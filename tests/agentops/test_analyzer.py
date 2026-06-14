from __future__ import annotations
import sqlite3
import pytest
from agentops.analyzer import analyze_db


ONE_S = 1_000_000_000  # 1 second in nanoseconds


def _make_db(tmp_path, events=(), requotes=()):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE order_events (
            id INTEGER PRIMARY KEY,
            ts_ns INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            order_side TEXT,
            fill_qty REAL,
            fill_price REAL,
            inventory_qty REAL,
            reject_reason TEXT
        );
        CREATE TABLE requotes (
            id INTEGER PRIMARY KEY,
            ts_ns INTEGER NOT NULL
        );
    """)
    for ev in events:
        conn.execute(
            "INSERT INTO order_events "
            "(ts_ns, event_type, order_side, fill_qty, fill_price, inventory_qty) "
            "VALUES (?,?,?,?,?,?)",
            ev,
        )
    for ts in requotes:
        conn.execute("INSERT INTO requotes (ts_ns) VALUES (?)", (ts,))
    conn.commit()
    conn.close()
    return db_path


def test_empty_db_returns_zeros(tmp_path):
    db = _make_db(tmp_path)
    m = analyze_db(db)
    assert m["net_pnl"] == 0.0
    assert m["submit_rate_per_s"] == 0.0
    assert m["cancel_rejected"] == 0
    assert m["fill_rate_per_min"] == 0.0
    assert m["inventory_max_abs"] == 0.0


def test_submit_rate(tmp_path):
    t0 = 10 * ONE_S
    events = [(t0 + i * ONE_S, "submitted", None, None, None, None) for i in range(10)]
    db = _make_db(tmp_path, events=events)
    m = analyze_db(db)
    # 10 submits over 9s → ~1.11/s
    assert 1.0 <= m["submit_rate_per_s"] <= 1.5


def test_reject_pct(tmp_path):
    t0 = 10 * ONE_S
    events = (
        [(t0, "submitted", None, None, None, None)] * 10
        + [(t0 + ONE_S, "rejected", None, None, None, None)] * 2
    )
    db = _make_db(tmp_path, events=events)
    m = analyze_db(db)
    assert abs(m["reject_pct"] - 20.0) < 0.1


def test_cancel_rejected_count(tmp_path):
    t0 = 10 * ONE_S
    events = [
        (t0, "cancel_rejected", None, None, None, None),
        (t0 + ONE_S, "cancel_rejected", None, None, None, None),
    ]
    db = _make_db(tmp_path, events=events)
    m = analyze_db(db)
    assert m["cancel_rejected"] == 2


def test_pnl_with_balanced_fills(tmp_path):
    t0 = 10 * ONE_S
    events = [
        (t0, "submitted", None, None, None, None),
        (t0 + 10 * ONE_S, "submitted", None, None, None, None),
        (t0 + ONE_S, "filled", "BUY", 0.001, 60000.0, 0.001),
        (t0 + 11 * ONE_S, "filled", "SELL", 0.001, 60020.0, 0.0),
    ]
    db = _make_db(tmp_path, events=events)
    m = analyze_db(db, f_maker=0.0002)
    assert abs(m["spread_captured_usd"] - 20.0) < 0.01
    assert m["net_pnl"] < 0  # fees exceed gross with narrow spread


def test_inventory_max_abs(tmp_path):
    t0 = 10 * ONE_S
    events = [
        (t0, "filled", "BUY", 0.001, 60000.0, 0.001),
        (t0 + ONE_S, "filled", "BUY", 0.001, 60000.0, 0.002),
        (t0 + 2 * ONE_S, "filled", "SELL", 0.001, 60010.0, 0.001),
    ]
    db = _make_db(tmp_path, events=events)
    m = analyze_db(db)
    assert abs(m["inventory_max_abs"] - 0.002) < 1e-9


def test_fast_requote_pct(tmp_path):
    t0 = 10 * ONE_S
    # intervals: 400ms (fast), 400ms (fast), 600ms (slow) → 2/3 = 66.7%
    requotes = [t0, t0 + 400_000_000, t0 + 800_000_000, t0 + 1_400_000_000]
    db = _make_db(tmp_path, requotes=requotes)
    m = analyze_db(db)
    assert abs(m["fast_requote_pct"] - 66.7) < 1.0


def test_fill_rate_per_min(tmp_path):
    t0 = 10 * ONE_S
    events = [
        (t0, "filled", "BUY", 0.001, 60000.0, 0.001),
        (t0 + 30 * ONE_S, "filled", "SELL", 0.001, 60010.0, 0.0),
    ]
    db = _make_db(tmp_path, events=events)
    m = analyze_db(db)
    # 2 fills over 30s = 4/min
    assert abs(m["fill_rate_per_min"] - 4.0) < 0.5


def test_only_buy_fills_no_sell(tmp_path):
    t0 = 10 * ONE_S
    events = [
        (t0, "filled", "BUY", 0.001, 60000.0, 0.001),
    ]
    db = _make_db(tmp_path, events=events)
    m = analyze_db(db)
    # Only buys — spread is 0, no net PnL from roundtrip
    assert m["spread_captured_usd"] == 0.0
    assert m["net_pnl"] == 0.0
    assert m["inventory_max_abs"] == pytest.approx(0.001)
    # avg_fill_price should be non-zero (derived from vwap_buy)
    assert m["avg_fill_price"] == pytest.approx(60000.0)
