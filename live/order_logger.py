"""Logger asíncrono de órdenes/fills/requotes a SQLite.

Usa un hilo de fondo + queue.Queue para que el loop de la estrategia nunca
espere un write a disco. La estrategia llama log_event() de forma no-bloqueante
(put_nowait); el hilo escritor drena la cola y hace batch-inserts con WAL.

Uso típico desde la estrategia:
    self._logger = OrderLogger()          # __init__
    self._logger.start()                  # on_start
    self._logger.log_event("submitted", client_order_id="...", ...)
    self._logger.stop()                   # on_stop
"""
from __future__ import annotations

import queue
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


_SENTINEL = None  # señal de fin al hilo escritor


class OrderLogger:
    """Logger no-bloqueante: la estrategia pone eventos en una queue; un hilo
    de fondo los escribe en SQLite en batches (≤50 eventos o ≤500ms)."""

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            Path("logs").mkdir(exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            db_path = f"logs/trading_{ts}.db"
        self._db_path = db_path
        self._q: queue.Queue[dict | None] = queue.Queue(maxsize=10_000)
        self._thread: threading.Thread | None = None

    @property
    def db_path(self) -> str:
        return self._db_path

    # ── Ciclo de vida ──────────────────────────────────────────────────────

    def start(self) -> None:
        """Abre la base de datos e inicia el hilo escritor."""
        conn = sqlite3.connect(self._db_path, check_same_thread=True)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        self._create_tables(conn)
        conn.close()

        self._thread = threading.Thread(
            target=self._writer_loop,
            name="order-logger",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        """Espera a que la cola se vacíe y cierra el hilo (hasta timeout_s)."""
        try:
            self._q.put(_SENTINEL, timeout=timeout_s)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=timeout_s)

    # ── API pública (no bloqueante) ────────────────────────────────────────

    def log_event(self, event_type: str, **fields) -> None:
        """Encola un evento. Fire-and-forget: nunca bloquea la estrategia."""
        try:
            self._q.put_nowait({
                "ts_ns": time.time_ns(),
                "event_type": event_type,
                **fields,
            })
        except queue.Full:
            pass  # si la cola está llena, descartamos (evitar bloquear el bot)

    # ── Hilo escritor ──────────────────────────────────────────────────────

    def _writer_loop(self) -> None:
        conn = sqlite3.connect(self._db_path, check_same_thread=True)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")

        batch: list[dict] = []
        while True:
            try:
                item = self._q.get(timeout=0.5)
                if item is _SENTINEL:
                    if batch:
                        self._flush(conn, batch)
                    conn.commit()
                    conn.close()
                    return
                batch.append(item)
                if len(batch) >= 50:
                    self._flush(conn, batch)
                    batch = []
            except queue.Empty:
                if batch:
                    self._flush(conn, batch)
                    batch = []

    @staticmethod
    def _flush(conn: sqlite3.Connection, batch: list[dict]) -> None:
        order_events = [e for e in batch if e["event_type"] != "requote"]
        requotes = [e for e in batch if e["event_type"] == "requote"]

        _ORDER_DEFAULTS: dict = {
            "client_order_id": None, "venue_order_id": None,
            "instrument_id": None, "order_side": None,
            "quantity": None, "price": None,
            "fill_qty": None, "fill_price": None,
            "cumulative_qty": None, "reject_reason": None,
            "inventory_qty": None,
        }
        _REQUOTE_DEFAULTS: dict = {
            "quote_bid": None, "quote_ask": None,
            "inventory_qty": None, "submit_bid": None, "submit_ask": None,
        }

        if order_events:
            rows = [{**_ORDER_DEFAULTS, **e} for e in order_events]
            conn.executemany(
                "INSERT INTO order_events "
                "(ts_ns, event_type, client_order_id, venue_order_id, instrument_id, "
                " order_side, quantity, price, fill_qty, fill_price, "
                " cumulative_qty, reject_reason, inventory_qty) VALUES "
                "(:ts_ns, :event_type, :client_order_id, :venue_order_id, :instrument_id, "
                " :order_side, :quantity, :price, :fill_qty, :fill_price, "
                " :cumulative_qty, :reject_reason, :inventory_qty)",
                rows,
            )
        if requotes:
            rows = [{**_REQUOTE_DEFAULTS, **r} for r in requotes]
            conn.executemany(
                "INSERT INTO requotes "
                "(ts_ns, quote_bid, quote_ask, inventory_qty, submit_bid, submit_ask) VALUES "
                "(:ts_ns, :quote_bid, :quote_ask, :inventory_qty, :submit_bid, :submit_ask)",
                rows,
            )
        conn.commit()

    @staticmethod
    def _create_tables(conn: sqlite3.Connection) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS order_events (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_ns            INTEGER NOT NULL,
                event_type       TEXT NOT NULL,
                client_order_id  TEXT,
                venue_order_id   TEXT,
                instrument_id    TEXT,
                order_side       TEXT,
                quantity         REAL,
                price            REAL,
                fill_qty         REAL,
                fill_price       REAL,
                cumulative_qty   REAL,
                reject_reason    TEXT,
                inventory_qty    REAL
            );
            CREATE TABLE IF NOT EXISTS requotes (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_ns         INTEGER NOT NULL,
                quote_bid     REAL,
                quote_ask     REAL,
                inventory_qty REAL,
                submit_bid    INTEGER,
                submit_ask    INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_events_ts   ON order_events(ts_ns);
            CREATE INDEX IF NOT EXISTS idx_events_type ON order_events(event_type);
            CREATE INDEX IF NOT EXISTS idx_requotes_ts ON requotes(ts_ns);
        """)
        conn.commit()
