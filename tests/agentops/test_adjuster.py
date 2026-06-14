import pytest
from agentops.adjuster import compute_adjustment, apply_bounds, PARAM_BOUNDS

BASE_PARAMS = {
    "gamma": 0.1,
    "k": 1.5,
    "tau": 10.0,
    "q_max": 0.01,
    "signal_gain": 0.30,
    "requote_threshold_ticks": 2.0,
    "requote_min_interval_ms": 500,
    "vol_breaker": 0.005,
}

GOOD_METRICS = {
    "spread_captured_usd": 30.0,
    "fee_per_roundtrip": 25.0,   # spread 30 > 25*1.1=27.5 — sufficient
    "fill_rate_per_min": 2.0,
    "inventory_max_abs": 0.003,  # < q_max*0.8=0.008
    "fast_requote_pct": 2.0,
    "net_pnl": 0.5,
    "q_max": 0.01,
}


def test_no_adjustment_when_profitable():
    result = compute_adjustment(GOOD_METRICS, BASE_PARAMS)
    assert result is None


def test_spread_too_narrow_increases_gamma():
    metrics = {**GOOD_METRICS, "spread_captured_usd": 20.0}  # 20 < 25*1.1=27.5
    result = compute_adjustment(metrics, BASE_PARAMS)
    assert result is not None
    assert result["param"] == "gamma"
    assert result["new_value"] > BASE_PARAMS["gamma"]
    assert "spread" in result["reason"].lower()


def test_spread_too_narrow_falls_back_to_tau_when_gamma_maxed():
    params = {**BASE_PARAMS, "gamma": 0.5}   # already at PARAM_BOUNDS max
    metrics = {**GOOD_METRICS, "spread_captured_usd": 10.0}
    result = compute_adjustment(metrics, params)
    assert result is not None
    assert result["param"] == "tau"
    assert result["new_value"] > params["tau"]


def test_spread_too_wide_decreases_gamma():
    metrics = {
        **GOOD_METRICS,
        "spread_captured_usd": 80.0,  # 80 > 25*3.0=75
        "fill_rate_per_min": 0.2,     # < 0.5
    }
    result = compute_adjustment(metrics, BASE_PARAMS)
    assert result is not None
    assert result["param"] == "gamma"
    assert result["new_value"] < BASE_PARAMS["gamma"]


def test_low_fill_rate_reduces_requote_threshold():
    metrics = {
        **GOOD_METRICS,
        "fill_rate_per_min": 0.1,       # < 0.2
        "spread_captured_usd": 28.0,    # OK — 28 > 25*1.1=27.5
    }
    result = compute_adjustment(metrics, BASE_PARAMS)
    assert result is not None
    assert result["param"] == "requote_threshold_ticks"
    assert result["new_value"] < BASE_PARAMS["requote_threshold_ticks"]


def test_high_inventory_increases_signal_gain():
    metrics = {
        **GOOD_METRICS,
        "inventory_max_abs": 0.009,   # > q_max*0.8=0.008
        "spread_captured_usd": 28.0,
        "fill_rate_per_min": 2.0,
    }
    result = compute_adjustment(metrics, BASE_PARAMS)
    assert result is not None
    assert result["param"] == "signal_gain"
    assert result["new_value"] > BASE_PARAMS["signal_gain"]


def test_only_one_param_adjusted_per_cycle():
    # Both spread (HIGH) and fill_rate (MEDIUM) triggered — only HIGH wins
    metrics = {
        **GOOD_METRICS,
        "spread_captured_usd": 20.0,
        "fill_rate_per_min": 0.1,
        "inventory_max_abs": 0.009,
    }
    result = compute_adjustment(metrics, BASE_PARAMS)
    assert result is not None
    assert result["param"] == "gamma"  # HIGH priority wins


def test_apply_bounds_clamps_to_max():
    assert apply_bounds("gamma", 0.9) == PARAM_BOUNDS["gamma"][1]   # 0.5


def test_apply_bounds_clamps_to_min():
    assert apply_bounds("gamma", 0.001) == PARAM_BOUNDS["gamma"][0]  # 0.02


def test_apply_bounds_requote_min_interval_floor():
    assert apply_bounds("requote_min_interval_ms", 100) == 200


def test_fast_requotes_increases_vol_breaker():
    metrics = {
        **GOOD_METRICS,
        "fast_requote_pct": 15.0,   # > 10 — triggers LOW stability
        "spread_captured_usd": 28.0,  # OK spread
        "fill_rate_per_min": 2.0,     # OK fill rate
        "inventory_max_abs": 0.003,   # OK inventory
    }
    result = compute_adjustment(metrics, BASE_PARAMS)
    assert result is not None
    assert result["param"] == "vol_breaker"
    assert result["new_value"] > BASE_PARAMS["vol_breaker"]


def test_adverse_fills_decreases_signal_gain():
    metrics = {
        **GOOD_METRICS,
        "fill_rate_per_min": 6.0,    # > 5
        "net_pnl": -0.5,             # < 0 (adverse fills)
        "spread_captured_usd": 28.0, # OK spread
        "inventory_max_abs": 0.003,  # OK inventory
        "fast_requote_pct": 2.0,     # OK stability
    }
    result = compute_adjustment(metrics, BASE_PARAMS)
    assert result is not None
    assert result["param"] == "signal_gain"
    assert result["new_value"] < BASE_PARAMS["signal_gain"]
