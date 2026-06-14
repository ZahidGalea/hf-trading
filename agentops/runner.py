from __future__ import annotations

"""AgentOps orchestration loop.

Launches the bot as a subprocess, waits for the configured interval,
analyzes the trading SQLite DB, applies parameter adjustments if needed,
and repeats indefinitely until stopped.
"""

import argparse
import datetime
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional

from agentops.analyzer import analyze_db
from agentops.adjuster import compute_adjustment
from agentops.history import record_cycle, read_history
from agentops.safety import check_safety
from config.btc_maker import get_default_params, read_overrides, write_overrides

_HISTORY_PATH = "logs/agentops_history.jsonl"
_BOT_CMD = ["poetry", "run", "python", "-m", "live.run_testnet"]
_STARTUP_TIMEOUT_S = 30


@dataclass
class RunnerConfig:
    interval_s: int = 300
    dry_run: bool = False
    max_cycles: Optional[int] = None
    no_restart: bool = False
    logs_dir: str = "logs"
    history_path: str = _HISTORY_PATH


def find_latest_db(logs_dir: str = "logs") -> str:
    """Return path to the most recently modified trading_*.db in logs_dir."""
    from pathlib import Path
    candidates = sorted(
        Path(logs_dir).glob("trading_*.db"),
        key=lambda p: p.stat().st_mtime,
    )
    if not candidates:
        raise FileNotFoundError(f"No trading_*.db files found in {logs_dir}")
    return str(candidates[-1])


def get_effective_params() -> dict:
    """Return default A-S params merged with any active overrides from overrides.json."""
    defaults = get_default_params()
    overrides = read_overrides()
    return {**defaults, **overrides}


def _launch_bot() -> subprocess.Popen:
    """Launch the bot subprocess and poll 5s to detect immediate startup failures."""
    proc = subprocess.Popen(
        _BOT_CMD,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ},
    )
    check_until = time.monotonic() + 5
    while time.monotonic() < check_until:
        rc = proc.poll()
        if rc is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(
                f"Bot exited (code {rc}) within {_STARTUP_TIMEOUT_S}s.\nOutput:\n{out}"
            )
        time.sleep(0.5)
    print(f"[agentops] Bot launched — PID {proc.pid}")
    return proc


def _stop_bot(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    print(f"[agentops] Stopping bot (PID {proc.pid}) …")
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        print(f"[agentops] Graceful stop timed out — sending SIGKILL")
        proc.kill()
        proc.wait()
    print(f"[agentops] Bot stopped (exit {proc.returncode})")


def _ts_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def run_loop(cfg: RunnerConfig) -> None:
    proc: subprocess.Popen | None = None
    cycle = 0

    def _shutdown(signum, frame):
        print(f"\n[agentops] Signal {signum} — stopping.")
        if proc:
            _stop_bot(proc)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        proc = _launch_bot()
    except RuntimeError as exc:
        print(f"[agentops] STARTUP FAILURE: {exc}", file=sys.stderr)
        sys.exit(1)

    while True:
        cycle += 1
        if cfg.max_cycles and cycle > cfg.max_cycles:
            print(f"[agentops] Reached max_cycles={cfg.max_cycles}. Stopping.")
            break

        print(f"\n[agentops] Cycle {cycle} — waiting {cfg.interval_s}s …")
        time.sleep(cfg.interval_s)

        if proc.poll() is not None:
            print(
                f"[agentops] Bot died unexpectedly (exit {proc.returncode}). Aborting.",
                file=sys.stderr,
            )
            sys.exit(1)

        try:
            db_path = find_latest_db(cfg.logs_dir)
        except FileNotFoundError as exc:
            print(f"[agentops] {exc} — skipping cycle {cycle}")
            continue

        params = get_effective_params()
        metrics = analyze_db(db_path, f_maker=params.get("f_maker", 0.0002))
        duration_min = metrics["session_duration_s"] / 60.0

        print(
            f"[agentops] Cycle {cycle}: net_pnl={metrics['net_pnl']:.4f} USD  "
            f"spread={metrics['spread_captured_usd']:.2f} USD/BTC  "
            f"fill_rate={metrics['fill_rate_per_min']:.2f}/min"
        )

        is_emergency, emergency_msg = check_safety(metrics, q_max=params["q_max"])
        if is_emergency:
            print(f"[agentops] {emergency_msg}", file=sys.stderr)
            _stop_bot(proc)
            sys.exit(2)

        metrics_for_adj = {**metrics, "q_max": params["q_max"]}
        adjustment = compute_adjustment(metrics_for_adj, params)

        params_before = dict(params)
        params_after = dict(params)
        bot_restarted = False
        reason: str | None = None

        if adjustment and not cfg.no_restart:
            reason = adjustment["reason"]
            params_after[adjustment["param"]] = adjustment["new_value"]
            print(
                f"[agentops] Adjusting {adjustment['param']}: "
                f"{adjustment['old_value']} → {adjustment['new_value']}\n"
                f"  Reason: {reason}"
            )
            if not cfg.dry_run:
                _adjustable_keys = {
                    "gamma", "k", "tau", "signal_gain",
                    "requote_threshold_ticks", "requote_min_interval_ms", "vol_breaker",
                }
                write_overrides({k: v for k, v in params_after.items() if k in _adjustable_keys})
                _stop_bot(proc)
                proc = _launch_bot()
                bot_restarted = True
            else:
                print(f"[agentops] DRY RUN — changes not applied")
        elif adjustment and cfg.no_restart:
            reason = adjustment["reason"] + " (--no-restart: not applied)"
            print(
                f"[agentops] Suggested: {adjustment['param']} → {adjustment['new_value']} "
                f"(not applied — --no-restart)"
            )
        else:
            print(f"[agentops] No adjustment needed.")

        record_cycle(
            cycle=cycle,
            ts_iso=_ts_iso(),
            session_db=db_path,
            duration_min=round(duration_min, 2),
            metrics={
                k: round(v, 6) if isinstance(v, float) else v
                for k, v in metrics.items()
            },
            params_before=params_before,
            params_after=params_after,
            adjustment_reason=reason,
            bot_restarted=bot_restarted,
            path=cfg.history_path,
        )

    if proc:
        _stop_bot(proc)


def _print_history(cfg: RunnerConfig) -> None:
    records = read_history(cfg.history_path)
    if not records:
        print("No history yet.")
        return
    for r in records:
        adj = r.get("adjustment_reason") or "—"
        print(
            f"  Cycle {r['cycle']:3d}  [{r['ts']}]  "
            f"net_pnl={r['metrics'].get('net_pnl', 0):+.4f}  "
            f"spread={r['metrics'].get('spread_captured_usd', 0):.2f}  "
            f"adj={adj[:60]}"
        )


def _analyze_only(cfg: RunnerConfig) -> None:
    db_path = find_latest_db(cfg.logs_dir)
    params = get_effective_params()
    metrics = analyze_db(db_path, f_maker=params.get("f_maker", 0.0002))
    print(f"DB: {db_path}")
    for k, v in metrics.items():
        print(f"  {k:<28} {v}")
    adjustment = compute_adjustment({**metrics, "q_max": params["q_max"]}, params)
    if adjustment:
        print(f"\nSuggested: {adjustment['param']} → {adjustment['new_value']}")
        print(f"  Reason: {adjustment['reason']}")
    else:
        print("\nNo adjustment needed.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AgentOps — autonomous parameter optimization loop"
    )
    parser.add_argument("--interval", type=int, default=300, metavar="S",
                        help="Seconds between analysis cycles (default: 300)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Analyze and suggest adjustments but do not apply them")
    parser.add_argument("--max-cycles", type=int, default=None, metavar="N",
                        help="Stop after N cycles")
    parser.add_argument("--no-restart", action="store_true",
                        help="Analyze but never restart the bot")
    parser.add_argument("--analyze-only", action="store_true",
                        help="Analyze the most recent DB and exit (no loop)")
    parser.add_argument("--history", action="store_true",
                        help="Print experiment history and exit")
    args = parser.parse_args()

    cfg = RunnerConfig(
        interval_s=args.interval,
        dry_run=args.dry_run,
        max_cycles=args.max_cycles,
        no_restart=args.no_restart,
    )

    if args.history:
        _print_history(cfg)
        return
    if args.analyze_only:
        _analyze_only(cfg)
        return

    run_loop(cfg)


if __name__ == "__main__":
    main()
