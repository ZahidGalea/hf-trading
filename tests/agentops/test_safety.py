import pytest
from agentops.safety import check_safety

BASE_METRICS = {
    "cancel_rejected": 0,
    "submit_rate_per_s": 1.5,
    "inventory_max_abs": 0.005,
    "reject_pct": 2.0,
}
Q_MAX = 0.01


def test_ok_metrics_no_emergency():
    ok, msg = check_safety(BASE_METRICS, q_max=Q_MAX)
    assert ok is False
    assert msg == ""


def test_cancel_rejected_triggers_emergency():
    m = {**BASE_METRICS, "cancel_rejected": 6}
    ok, msg = check_safety(m, q_max=Q_MAX)
    assert ok is True
    assert "reloj" in msg.lower()


def test_submit_flood_triggers_emergency():
    m = {**BASE_METRICS, "submit_rate_per_s": 25.0}
    ok, msg = check_safety(m, q_max=Q_MAX)
    assert ok is True
    assert "flood" in msg.lower()


def test_inventory_excess_triggers_emergency():
    # 0.016 > q_max * 1.5 = 0.015
    m = {**BASE_METRICS, "inventory_max_abs": 0.016}
    ok, msg = check_safety(m, q_max=Q_MAX)
    assert ok is True
    assert "inventario" in msg.lower()


def test_high_reject_pct_triggers_emergency():
    m = {**BASE_METRICS, "reject_pct": 20.0}
    ok, msg = check_safety(m, q_max=Q_MAX)
    assert ok is True
    assert "rechazo" in msg.lower()


def test_inventory_just_under_threshold_is_ok():
    # 0.014 < q_max * 1.5 = 0.015
    m = {**BASE_METRICS, "inventory_max_abs": 0.014}
    ok, msg = check_safety(m, q_max=Q_MAX)
    assert ok is False
    assert msg == ""
