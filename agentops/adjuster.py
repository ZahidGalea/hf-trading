from __future__ import annotations


PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "gamma":                   (0.02,  0.5),
    "k":                       (0.5,   5.0),
    "tau":                     (2.0,  30.0),
    "q_max":                   (0.005, 0.01),
    "signal_gain":             (0.1,   0.6),
    "requote_threshold_ticks": (1.0,   5.0),
    "requote_min_interval_ms": (200,  2000),
    "vol_breaker":             (0.003, 0.02),
}


def apply_bounds(param: str, value: float) -> float:
    """Clamp value within bounds for the given parameter."""
    lo, hi = PARAM_BOUNDS[param]
    return max(lo, min(hi, value))


def compute_adjustment(metrics: dict, params: dict) -> dict | None:
    """Return a single parameter adjustment or None if no adjustment is needed.

    Priority: HIGH (spread) > MEDIUM (fill_rate, inventory) > LOW (stability).
    Only one parameter is adjusted per call (spec §8).
    q_max is never touched (spec §8).
    """
    fee_rt    = metrics["fee_per_roundtrip"]
    spread    = metrics["spread_captured_usd"]
    fill_rate = metrics["fill_rate_per_min"]
    inv_max   = metrics["inventory_max_abs"]
    fast_rq   = metrics["fast_requote_pct"]
    net_pnl   = metrics["net_pnl"]
    q_max     = metrics.get("q_max", params.get("q_max", 0.01))

    # ── HIGH: spread adjustment ───────────────────────────────────────────────
    if fee_rt > 0 and spread < fee_rt * 1.1:
        new_gamma = apply_bounds("gamma", params["gamma"] * 1.2)
        if new_gamma != params["gamma"]:
            return {
                "param": "gamma",
                "old_value": params["gamma"],
                "new_value": new_gamma,
                "reason": (
                    f"spread_captured ({spread:.2f}) < fee_per_roundtrip × 1.1 "
                    f"({fee_rt * 1.1:.2f})"
                ),
            }
        new_tau = apply_bounds("tau", params["tau"] * 1.1)
        if new_tau != params["tau"]:
            return {
                "param": "tau",
                "old_value": params["tau"],
                "new_value": new_tau,
                "reason": (
                    f"spread_captured ({spread:.2f}) < fee_per_roundtrip × 1.1 "
                    f"({fee_rt * 1.1:.2f}); gamma already at max"
                ),
            }

    if fee_rt > 0 and spread > fee_rt * 3.0 and fill_rate < 0.5:
        new_gamma = apply_bounds("gamma", params["gamma"] * 0.85)
        if new_gamma != params["gamma"]:
            return {
                "param": "gamma",
                "old_value": params["gamma"],
                "new_value": new_gamma,
                "reason": (
                    f"spread_captured ({spread:.2f}) > fee_per_roundtrip × 3.0 "
                    f"({fee_rt * 3.0:.2f}) and fill_rate ({fill_rate:.2f}/min) < 0.5"
                ),
            }

    # ── MEDIUM: fill rate / inventory ─────────────────────────────────────────
    if fill_rate < 0.2:
        new_thresh = apply_bounds(
            "requote_threshold_ticks", params["requote_threshold_ticks"] - 0.5
        )
        if new_thresh != params["requote_threshold_ticks"]:
            return {
                "param": "requote_threshold_ticks",
                "old_value": params["requote_threshold_ticks"],
                "new_value": new_thresh,
                "reason": f"fill_rate ({fill_rate:.2f}/min) < 0.2",
            }

    if inv_max > q_max * 0.8:
        new_sg = apply_bounds("signal_gain", params["signal_gain"] * 1.1)
        if new_sg != params["signal_gain"]:
            return {
                "param": "signal_gain",
                "old_value": params["signal_gain"],
                "new_value": new_sg,
                "reason": (
                    f"inventory_max_abs ({inv_max:.4f}) > q_max × 0.8 "
                    f"({q_max * 0.8:.4f})"
                ),
            }

    # ── LOW: stability ────────────────────────────────────────────────────────
    if fast_rq > 10:
        new_vb = apply_bounds("vol_breaker", params["vol_breaker"] * 1.2)
        if new_vb != params["vol_breaker"]:
            return {
                "param": "vol_breaker",
                "old_value": params["vol_breaker"],
                "new_value": new_vb,
                "reason": f"fast_requote_pct ({fast_rq:.1f}%) > 10%",
            }

    if fill_rate > 5 and net_pnl < 0:
        new_sg = apply_bounds("signal_gain", params["signal_gain"] * 0.9)
        if new_sg != params["signal_gain"]:
            return {
                "param": "signal_gain",
                "old_value": params["signal_gain"],
                "new_value": new_sg,
                "reason": (
                    f"fill_rate ({fill_rate:.2f}/min) > 5 with net_pnl={net_pnl:.4f} < 0 "
                    "(adverse fills)"
                ),
            }

    return None
