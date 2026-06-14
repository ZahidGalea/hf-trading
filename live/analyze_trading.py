#!/usr/bin/env python
"""Análisis de sesión de trading desde la base de datos SQLite.

Uso:
    poetry run python -m live.analyze_trading                     # último DB en logs/
    poetry run python -m live.analyze_trading logs/trading_X.db  # DB específico
    poetry run python -m live.analyze_trading --watch             # refrescar cada 5s (live)

Responde preguntas como:
  - ¿Cuántas órdenes/segundo se están enviando?
  - ¿Cuántos rechazos hay y por qué?
  - ¿Con qué frecuencia se re-cotiza?
  - ¿Cuántos fills hay y cuál es el P&L estimado?
  - ¿Hay bursts sospechosos de actividad?
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path


# ── Utilidades ────────────────────────────────────────────────────────────────

def find_latest_db() -> Path:
    logs = sorted(Path("logs").glob("trading_*.db"), key=lambda p: p.stat().st_mtime)
    if not logs:
        print("No se encontró ningún archivo trading_*.db en logs/", file=sys.stderr)
        sys.exit(1)
    return logs[-1]


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def hr() -> None:
    print("─" * 70)


def section(title: str) -> None:
    hr()
    print(f"  {title}")
    hr()


# ── Queries de análisis ───────────────────────────────────────────────────────

def summary(conn: sqlite3.Connection) -> None:
    section("RESUMEN GENERAL")
    row = conn.execute("""
        SELECT
            COUNT(*)                                           AS total_events,
            MIN(ts_ns) / 1e9                                   AS start_epoch,
            MAX(ts_ns) / 1e9                                   AS end_epoch,
            (MAX(ts_ns) - MIN(ts_ns)) / 1e9                   AS duration_s,
            SUM(event_type = 'submitted')                      AS submitted,
            SUM(event_type = 'accepted')                       AS accepted,
            SUM(event_type = 'rejected')                       AS rejected,
            SUM(event_type = 'filled')                         AS filled,
            SUM(event_type = 'canceled')                       AS canceled,
            SUM(event_type = 'cancel_rejected')                AS cancel_rejected
        FROM order_events
    """).fetchone()

    dur = row["duration_s"] or 0.001
    print(f"  Duración sesión     : {dur:.1f}s")
    print(f"  Total eventos       : {row['total_events']}")
    print(f"  Submitted           : {row['submitted']}  ({row['submitted']/dur:.2f}/s)")
    print(f"  Accepted            : {row['accepted']}")
    print(f"  Rejected            : {row['rejected']}   ← rechazos por Binance")
    print(f"  Filled              : {row['filled']}")
    print(f"  Canceled            : {row['canceled']}")
    print(f"  Cancel Rejected     : {row['cancel_rejected']}  ← cancelaciones fallidas (-1021)")

    rq = conn.execute("SELECT COUNT(*), (MAX(ts_ns)-MIN(ts_ns))/1e9 AS dur FROM requotes").fetchone()
    rq_rate = rq[0] / max(rq[1] or 0.001, 0.001)
    print(f"  Re-quotes           : {rq[0]}  ({rq_rate:.2f}/s)")

    if row['submitted'] and row['submitted'] > 0:
        rej_pct = 100.0 * row['rejected'] / row['submitted']
        print(f"  Tasa de rechazo     : {rej_pct:.1f}%")


def order_rate_per_second(conn: sqlite3.Connection) -> None:
    """Ventana deslizante de 1s — detecta bursts."""
    section("ÓRDENES POR SEGUNDO (ventana 1s, top-10 picos)")
    rows = conn.execute("""
        SELECT
            CAST(ts_ns / 1e9 AS INTEGER) AS second_epoch,
            COUNT(*)                      AS orders
        FROM order_events
        WHERE event_type = 'submitted'
        GROUP BY second_epoch
        ORDER BY orders DESC
        LIMIT 10
    """).fetchall()

    if not rows:
        print("  Sin datos de submit.")
        return

    print(f"  {'Segundo (epoch)':<20} {'Órdenes/s':>12}")
    for r in rows:
        bar = "█" * min(r["orders"], 40)
        print(f"  {r['second_epoch']:<20} {r['orders']:>12}  {bar}")

    avg = conn.execute("""
        SELECT AVG(orders) FROM (
            SELECT CAST(ts_ns/1e9 AS INTEGER) AS s, COUNT(*) AS orders
            FROM order_events WHERE event_type='submitted' GROUP BY s
        )
    """).fetchone()[0] or 0
    print(f"\n  Promedio histórico  : {avg:.2f} órdenes/s")
    print("  ALERTA: >20 órdenes/s es señal de order-flood.")


def requote_intervals(conn: sqlite3.Connection) -> None:
    section("INTERVALOS ENTRE RE-QUOTES (distribución)")
    rows = conn.execute("""
        SELECT ts_ns FROM requotes ORDER BY ts_ns
    """).fetchall()

    if len(rows) < 2:
        print("  Muy pocos re-quotes para calcular distribución.")
        return

    intervals_ms = [
        (rows[i]["ts_ns"] - rows[i - 1]["ts_ns"]) / 1e6
        for i in range(1, len(rows))
    ]
    intervals_ms.sort()
    n = len(intervals_ms)
    p50 = intervals_ms[int(n * 0.50)]
    p90 = intervals_ms[int(n * 0.90)]
    p99 = intervals_ms[int(n * 0.99)]
    mn  = intervals_ms[0]
    mx  = intervals_ms[-1]
    avg = sum(intervals_ms) / n

    print(f"  N re-quotes  : {len(rows)}")
    print(f"  Mínimo       : {mn:.1f} ms  ← debe ser ≥ requote_min_interval_ms (500ms)")
    print(f"  Promedio     : {avg:.1f} ms")
    print(f"  p50          : {p50:.1f} ms")
    print(f"  p90          : {p90:.1f} ms")
    print(f"  p99          : {p99:.1f} ms")
    print(f"  Máximo       : {mx:.1f} ms")

    fast = sum(1 for x in intervals_ms if x < 490)
    if fast:
        print(f"\n  ⚠  {fast} intervalos < 490ms (throttle no estaba funcionando o CB activado)")


def rejection_breakdown(conn: sqlite3.Connection) -> None:
    section("RECHAZOS POR MOTIVO")
    rows = conn.execute("""
        SELECT
            event_type,
            COALESCE(reject_reason, 'n/a') AS reason,
            COUNT(*) AS cnt
        FROM order_events
        WHERE event_type IN ('rejected', 'cancel_rejected')
        GROUP BY event_type, reason
        ORDER BY cnt DESC
    """).fetchall()

    if not rows:
        print("  Sin rechazos registrados. ✓")
        return

    for r in rows:
        print(f"  [{r['event_type']:>16}]  {r['cnt']:>5}×  {r['reason']}")


def _is_buy(side: str | None) -> bool:
    """Acepta tanto 'BUY'/'SELL' como '1'/'2' (legacy enum int de Nautilus)."""
    s = (side or "").upper()
    return s in ("BUY", "1")


def fills_analysis(conn: sqlite3.Connection) -> None:
    section("FILLS — P&L ESTIMADO (aproximación maker)")
    rows = conn.execute("""
        SELECT order_side, fill_qty, fill_price, inventory_qty, ts_ns
        FROM order_events
        WHERE event_type = 'filled' AND fill_qty IS NOT NULL AND fill_price IS NOT NULL
        ORDER BY ts_ns
    """).fetchall()

    if not rows:
        print("  Sin fills en esta sesión.")
        return

    dur_s = conn.execute("SELECT (MAX(ts_ns)-MIN(ts_ns))/1e9 FROM order_events WHERE event_type='filled'").fetchone()[0] or 1

    buy_rows  = [r for r in rows if _is_buy(r["order_side"])]
    sell_rows = [r for r in rows if not _is_buy(r["order_side"])]

    total_buy_qty  = sum(r["fill_qty"] for r in buy_rows)
    total_sell_qty = sum(r["fill_qty"] for r in sell_rows)
    vwap_buy  = (sum(r["fill_qty"] * r["fill_price"] for r in buy_rows)  / max(total_buy_qty,  1e-12))
    vwap_sell = (sum(r["fill_qty"] * r["fill_price"] for r in sell_rows) / max(total_sell_qty, 1e-12))

    matched   = min(total_buy_qty, total_sell_qty)
    gross_pnl = matched * (vwap_sell - vwap_buy)
    mid_price = (vwap_buy + vwap_sell) / 2 if (vwap_buy and vwap_sell) else vwap_buy or vwap_sell
    fees      = matched * 2 * 0.0002 * mid_price  # 2 lados × f_maker × precio
    net_pnl   = gross_pnl - fees

    last_inv = rows[-1]["inventory_qty"] or 0
    open_pnl = last_inv * mid_price  # valoración mark-to-market de la posición residual

    print(f"  Total fills         : {len(rows)}  ({len(rows)/max(dur_s,1):.2f}/s)")
    print(f"  Compras (BUY)       : {total_buy_qty:.4f} BTC  VWAP {vwap_buy:.2f} USD  ({len(buy_rows)} fills)")
    print(f"  Ventas (SELL)       : {total_sell_qty:.4f} BTC  VWAP {vwap_sell:.2f} USD  ({len(sell_rows)} fills)")
    print(f"  Spread capturado    : {vwap_sell - vwap_buy:+.4f} USD/BTC  (positivo = bueno)")
    print(f"  Inventario final    : {last_inv:+.4f} BTC  (MtM ≈ {open_pnl:+.4f} USD a precio mid)")
    print(f"  P&L bruto           : {gross_pnl:+.6f} USD  ({matched:.4f} BTC emparejados)")
    print(f"  Comisiones (est.)   : {-fees:+.6f} USD")
    print(f"  P&L neto            : {net_pnl:+.6f} USD")
    if vwap_sell > vwap_buy:
        print(f"  ✓ Spread positivo — el maker captura el bid-ask spread correctamente.")
    else:
        print(f"  ⚠ Spread negativo — revisar señal OBI / skew de inventario.")


def inventory_drift(conn: sqlite3.Connection) -> None:
    section("DERIVA DE INVENTARIO (máx exposición observada)")
    rows = conn.execute("""
        SELECT inventory_qty FROM order_events
        WHERE event_type = 'filled' AND inventory_qty IS NOT NULL
        ORDER BY ts_ns
    """).fetchall()

    if not rows:
        print("  Sin datos de inventario.")
        return

    invs = [r["inventory_qty"] for r in rows]
    mx = max(abs(x) for x in invs)
    print(f"  Exposición máxima   : {mx:.4f} BTC (q_max default = 0.01 BTC)")
    if mx > 0.01:
        print(f"  ⚠  Superó q_max — el tope de posición debería haberlo limitado.")
    else:
        print(f"  ✓ Dentro de q_max.")


def diagnosis(conn: sqlite3.Connection) -> None:
    section("DIAGNÓSTICO AUTOMÁTICO")
    issues = []
    ok     = []

    # 1. Tasa de submit
    row = conn.execute("""
        SELECT COUNT(*) AS n, (MAX(ts_ns)-MIN(ts_ns))/1e9 AS dur
        FROM order_events WHERE event_type='submitted'
    """).fetchone()
    rate = row["n"] / max(row["dur"] or 1, 1)
    if rate > 20:
        issues.append(f"ORDER FLOOD: {rate:.1f} submit/s (umbral 20/s) — revisar throttle")
    else:
        ok.append(f"Tasa de submit OK: {rate:.2f}/s")

    # 2. Tasa de rechazo
    total_sub = conn.execute("SELECT COUNT(*) FROM order_events WHERE event_type='submitted'").fetchone()[0] or 1
    total_rej = conn.execute("SELECT COUNT(*) FROM order_events WHERE event_type='rejected'").fetchone()[0]
    rej_pct = 100.0 * total_rej / total_sub
    if rej_pct > 5:
        issues.append(f"ALTA TASA DE RECHAZO: {rej_pct:.1f}% ({total_rej} rechazos)")
    else:
        ok.append(f"Tasa de rechazo aceptable: {rej_pct:.1f}%")

    # 3. Cancel rejections (-1021 típico)
    can_rej = conn.execute("SELECT COUNT(*) FROM order_events WHERE event_type='cancel_rejected'").fetchone()[0]
    if can_rej > 0:
        issues.append(f"CANCEL RECHAZADOS: {can_rej} — posible -1021, sincronizar reloj (sudo hwclock -s)")
    else:
        ok.append("Sin cancel_rejected — reloj sincronizado OK")

    # 4. Throttle de re-quotes
    rq_rows = conn.execute("SELECT ts_ns FROM requotes ORDER BY ts_ns").fetchall()
    if len(rq_rows) >= 2:
        intervals = [(rq_rows[i]["ts_ns"] - rq_rows[i-1]["ts_ns"])/1e6 for i in range(1, len(rq_rows))]
        fast = sum(1 for x in intervals if x < 490)
        pct_fast = 100.0 * fast / len(intervals)
        if pct_fast > 10:
            issues.append(f"THROTTLE BYPASS: {pct_fast:.1f}% de re-quotes < 490ms ({fast} eventos)")
        else:
            ok.append(f"Throttle OK: solo {pct_fast:.1f}% de re-quotes < 490ms (circuit breaker)")

    # 5. Notional mínimo (-4164)
    notional_rej = conn.execute("""
        SELECT COUNT(*) FROM order_events
        WHERE event_type='rejected' AND reject_reason LIKE '%4164%'
    """).fetchone()[0]
    if notional_rej > 0:
        issues.append(f"NOTIONAL MÍNIMO ({notional_rej}×): órden < $50 rechazada — _reduce_inventory_with_limit envía qty muy pequeño")
    else:
        ok.append("Sin rechazos de notional mínimo")

    # 6. Inventario excedió q_max
    over_qmax = conn.execute("""
        SELECT COUNT(*) FROM order_events
        WHERE event_type='filled' AND ABS(inventory_qty) > 0.011
    """).fetchone()[0]
    if over_qmax > 0:
        issues.append(f"INVENTARIO > q_max+10%: {over_qmax} fills con exposición excesiva")
    else:
        ok.append("Inventario dentro de límites (q_max ± tolerancia)")

    # 7. P&L spread
    buy_rows  = conn.execute("SELECT fill_qty, fill_price FROM order_events WHERE event_type='filled' AND fill_qty>0 AND (order_side='BUY' OR order_side='1')").fetchall()
    sell_rows = conn.execute("SELECT fill_qty, fill_price FROM order_events WHERE event_type='filled' AND fill_qty>0 AND (order_side='SELL' OR order_side='2')").fetchall()
    if buy_rows and sell_rows:
        vwap_b = sum(r[0]*r[1] for r in buy_rows) / sum(r[0] for r in buy_rows)
        vwap_s = sum(r[0]*r[1] for r in sell_rows) / sum(r[0] for r in sell_rows)
        if vwap_s > vwap_b:
            ok.append(f"Spread capturado positivo: VWAP sell {vwap_s:.2f} > buy {vwap_b:.2f} (+{vwap_s-vwap_b:.4f} USD/BTC)")
        else:
            issues.append(f"Spread negativo: VWAP sell {vwap_s:.2f} < buy {vwap_b:.2f} ({vwap_s-vwap_b:.4f} USD/BTC)")

    print()
    for msg in ok:
        print(f"  ✓  {msg}")
    print()
    for msg in issues:
        print(f"  ✗  {msg}")
    if not issues:
        print("  Todo en orden. El bot se comporta correctamente.")
    print()


def recent_activity(conn: sqlite3.Connection, last_n_seconds: int = 60) -> None:
    section(f"ACTIVIDAD RECIENTE (últimos {last_n_seconds}s)")
    cutoff = conn.execute("SELECT MAX(ts_ns) FROM order_events").fetchone()[0]
    if not cutoff:
        print("  Sin datos.")
        return
    cutoff_low = cutoff - last_n_seconds * 1_000_000_000

    rows = conn.execute("""
        SELECT
            CAST((ts_ns - :lo) / 1e10 AS INTEGER) AS bucket_10s,
            event_type,
            COUNT(*) AS cnt
        FROM order_events
        WHERE ts_ns >= :lo
        GROUP BY bucket_10s, event_type
        ORDER BY bucket_10s, event_type
    """, {"lo": cutoff_low}).fetchall()

    if not rows:
        print("  Sin actividad reciente.")
        return

    print(f"  {'Ventana':>12}  {'Tipo':>16}  {'Cnt':>6}")
    for r in rows:
        window_label = f"+{r['bucket_10s']*10:>2}–{r['bucket_10s']*10+10}s"
        print(f"  {window_label:>12}  {r['event_type']:>16}  {r['cnt']:>6}")


# ── Main ──────────────────────────────────────────────────────────────────────

def run_analysis(db_path: str) -> None:
    print(f"\n{'═'*70}")
    print(f"  TRADING ANALYTICS — {db_path}")
    print(f"{'═'*70}")
    conn = connect(db_path)
    summary(conn)
    diagnosis(conn)
    order_rate_per_second(conn)
    requote_intervals(conn)
    rejection_breakdown(conn)
    fills_analysis(conn)
    inventory_drift(conn)
    recent_activity(conn)
    hr()
    conn.close()


def main() -> None:
    args = sys.argv[1:]
    watch = "--watch" in args
    args = [a for a in args if a != "--watch"]

    if args:
        db_path = args[0]
    else:
        db_path = str(find_latest_db())

    if watch:
        print(f"Modo watch — refrescando cada 5s. Ctrl+C para salir.")
        while True:
            try:
                run_analysis(db_path)
                time.sleep(5)
            except KeyboardInterrupt:
                break
    else:
        run_analysis(db_path)


if __name__ == "__main__":
    main()
