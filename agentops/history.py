from __future__ import annotations

import json
from pathlib import Path


def record_cycle(
    *,
    cycle: int,
    ts_iso: str,
    session_db: str,
    duration_min: float,
    metrics: dict,
    params_before: dict,
    params_after: dict,
    adjustment_reason: str | None,
    bot_restarted: bool,
    path: str,
) -> None:
    """Append one cycle record to the JSONL history file."""
    entry = {
        "cycle": cycle,
        "ts": ts_iso,
        "session_db": session_db,
        "duration_min": duration_min,
        "metrics": metrics,
        "params_before": params_before,
        "params_after": params_after,
        "adjustment_reason": adjustment_reason,
        "bot_restarted": bot_restarted,
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_history(path: str) -> list[dict]:
    """Read all cycle records from a JSONL history file. Returns [] if missing."""
    p = Path(path)
    if not p.exists():
        return []
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records
