from __future__ import annotations

import sqlite3


def analyze_db(db_path: str, f_maker: float = 0.0002) -> dict:
    """Read a trading session SQLite DB and return a metrics dict.

    Issues a WAL checkpoint so writes from a live bot are visible before reading.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    try:
        return _compute_metrics(conn, f_maker)
    finally:
        conn.close()


def _compute_metrics(conn: sqlite3.Connection, f_maker: float) -> dict:
    base = conn.execute("""
        SELECT
            SUM(event_type = 'submitted')       AS submitted,
            SUM(event_type = 'rejected')        AS rejected,
            SUM(event_type = 'cancel_rejected') AS cancel_rejected,
            (MAX(ts_ns) - MIN(ts_ns)) / 1e9     AS duration_s
        FROM order_events
    """).fetchone()

    submitted       = int(base["submitted"] or 0)
    rejected        = int(base["rejected"] or 0)
    cancel_rejected = int(base["cancel_rejected"] or 0)
    duration_s      = float(base["duration_s"] or 1.0)

    submit_rate_per_s = submitted / max(duration_s, 1.0)
    reject_pct        = 100.0 * rejected / max(submitted, 1)

    fills = conn.execute("""
        SELECT order_side, fill_qty, fill_price, inventory_qty
        FROM order_events
        WHERE event_type = 'filled' AND fill_qty IS NOT NULL AND fill_price IS NOT NULL
        ORDER BY ts_ns
    """).fetchall()

    buy_rows  = [r for r in fills if (r["order_side"] or "").upper() in ("BUY",  "1")]
    sell_rows = [r for r in fills if (r["order_side"] or "").upper() in ("SELL", "2")]

    total_buy_qty  = sum(r["fill_qty"] for r in buy_rows)
    total_sell_qty = sum(r["fill_qty"] for r in sell_rows)
    vwap_buy = (
        sum(r["fill_qty"] * r["fill_price"] for r in buy_rows) / max(total_buy_qty, 1e-12)
        if buy_rows else 0.0
    )
    vwap_sell = (
        sum(r["fill_qty"] * r["fill_price"] for r in sell_rows) / max(total_sell_qty, 1e-12)
        if sell_rows else 0.0
    )

    matched       = min(total_buy_qty, total_sell_qty)
    spread_usd    = (vwap_sell - vwap_buy) if (buy_rows and sell_rows) else 0.0
    gross_pnl     = matched * spread_usd
    avg_price     = (vwap_buy + vwap_sell) / 2 if (vwap_buy and vwap_sell) else max(vwap_buy, vwap_sell)
    fees          = matched * 2 * f_maker * avg_price
    net_pnl       = gross_pnl - fees
    fee_per_roundtrip = 2 * f_maker * avg_price

    fill_rate_per_min = len(fills) / max(duration_s / 60.0, 1e-9)

    inv_values    = [r["inventory_qty"] for r in fills if r["inventory_qty"] is not None]
    inventory_max_abs = max((abs(x) for x in inv_values), default=0.0)

    rq_rows = conn.execute("SELECT ts_ns FROM requotes ORDER BY ts_ns").fetchall()
    requote_rate_per_s = len(rq_rows) / max(duration_s, 1.0)

    if len(rq_rows) >= 2:
        intervals_ms = [
            (rq_rows[i]["ts_ns"] - rq_rows[i - 1]["ts_ns"]) / 1e6
            for i in range(1, len(rq_rows))
        ]
        fast = sum(1 for x in intervals_ms if x < 490)
        fast_requote_pct = 100.0 * fast / len(intervals_ms)
    else:
        fast_requote_pct = 0.0

    duration_hours   = duration_s / 3600.0
    net_pnl_per_hour = net_pnl / max(duration_hours, 1e-9)

    return {
        "net_pnl":             net_pnl,
        "net_pnl_per_hour":    net_pnl_per_hour,
        "spread_captured_usd": spread_usd,
        "fee_per_roundtrip":   fee_per_roundtrip,
        "fill_rate_per_min":   fill_rate_per_min,
        "submit_rate_per_s":   submit_rate_per_s,
        "reject_pct":          reject_pct,
        "cancel_rejected":     cancel_rejected,
        "inventory_max_abs":   inventory_max_abs,
        "requote_rate_per_s":  requote_rate_per_s,
        "fast_requote_pct":    fast_requote_pct,
        "session_duration_s":  duration_s,
        "avg_fill_price":      avg_price,
    }
