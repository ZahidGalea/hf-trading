"""Generador de datos sintéticos de order book para BTC/USDT-M Futures.

Produce una secuencia realista de OrderBookDeltas que simula:
- Random walk del precio alrededor de $100,000
- Spread de ~1-2 ticks ($0.10 por tick)
- OBI imbalances que activan la señal de la estrategia
- Volatilidad variable para testear el circuit breaker
"""
from __future__ import annotations

import random
from typing import Generator

import nautilus_trader.model as nt_model
from nautilus_trader.model.data import OrderBookDelta, OrderBookDeltas
from nautilus_trader.model.enums import BookAction, OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity

BookOrder = nt_model.BookOrder

TICK = 0.01          # BTC/USDT test instrument tick size (price_increment=0.01)
PRICE_PRECISION = 2  # decimales en el precio (coin BTCUSDT.BINANCE)
QTY_PRECISION = 6    # decimales en la cantidad (size_precision=6)


def _price(p: float) -> Price:
    return Price(round(p, PRICE_PRECISION), precision=PRICE_PRECISION)


def _qty(q: float) -> Quantity:
    return Quantity(round(max(q, 0.000001), QTY_PRECISION), precision=QTY_PRECISION)


def _book_order(side: OrderSide, price: float, qty: float, order_id: int) -> BookOrder:
    return BookOrder(side=side, price=_price(price), size=_qty(qty), order_id=order_id)


def _delta(
    instrument_id: InstrumentId,
    action: BookAction,
    order: BookOrder | None,
    seq: int,
    ts_ns: int,
) -> OrderBookDelta:
    return OrderBookDelta(
        instrument_id=instrument_id,
        action=action,
        order=order,
        flags=0,
        sequence=seq,
        ts_event=ts_ns,
        ts_init=ts_ns,
    )


def generate_book_snapshot(
    instrument_id: InstrumentId,
    mid: float,
    spread_ticks: int,
    levels: int,
    seq_start: int,
    ts_ns: int,
) -> list[OrderBookDelta]:
    """Genera un snapshot inicial del libro: CLEAR + ADD para bid y ask."""
    deltas: list[OrderBookDelta] = []
    seq = seq_start

    # Limpiar el libro
    deltas.append(_delta(instrument_id, BookAction.CLEAR, None, seq, ts_ns))
    seq += 1

    half_spread = spread_ticks * TICK / 2.0
    best_bid = round(mid - half_spread, PRICE_PRECISION)
    best_ask = round(mid + half_spread, PRICE_PRECISION)

    # Agregar niveles bid (decrecientes)
    for i in range(levels):
        price = round(best_bid - i * TICK, PRICE_PRECISION)
        qty = round(random.uniform(0.500000, 5.000000), QTY_PRECISION)
        order = _book_order(OrderSide.BUY, price, qty, 1000 + i)
        deltas.append(_delta(instrument_id, BookAction.ADD, order, seq, ts_ns))
        seq += 1

    # Agregar niveles ask (crecientes)
    for i in range(levels):
        price = round(best_ask + i * TICK, PRICE_PRECISION)
        qty = round(random.uniform(0.500000, 5.000000), QTY_PRECISION)
        order = _book_order(OrderSide.SELL, price, qty, 2000 + i)
        deltas.append(_delta(instrument_id, BookAction.ADD, order, seq, ts_ns))
        seq += 1

    return deltas


def generate_book_update(
    instrument_id: InstrumentId,
    best_bid: float,
    best_ask: float,
    obi_target: float,
    seq: int,
    ts_ns: int,
) -> list[OrderBookDelta]:
    """Genera actualizaciones del top-of-book con OBI simulado.

    obi_target: valor deseado de OBI en [-1, 1]; controla el balance bid/ask.
    """
    deltas: list[OrderBookDelta] = []

    # Bid side: tamaño mayor cuando OBI positivo
    bid_base = 2.0
    ask_base = 2.0
    if obi_target > 0:
        bid_qty = bid_base * (1 + 3 * obi_target)
        ask_qty = ask_base * (1 - 0.5 * obi_target)
    else:
        bid_qty = bid_base * (1 + 0.5 * obi_target)
        ask_qty = ask_base * (1 - 3 * obi_target)

    bid_qty = max(bid_qty, 0.01)
    ask_qty = max(ask_qty, 0.01)

    # UPDATE del best bid
    order_bid = _book_order(OrderSide.BUY, best_bid, bid_qty, 1000)
    deltas.append(_delta(instrument_id, BookAction.UPDATE, order_bid, seq, ts_ns))

    # UPDATE del best ask
    order_ask = _book_order(OrderSide.SELL, best_ask, ask_qty, 2000)
    deltas.append(_delta(instrument_id, BookAction.UPDATE, order_ask, seq + 1, ts_ns))

    return deltas


def generate_synthetic_feed(
    instrument_id: InstrumentId,
    start_price: float = 100_000.0,
    n_events: int = 500,
    event_interval_ms: int = 100,
    seed: int = 42,
    include_volatile_period: bool = False,
) -> list[OrderBookDelta]:
    """Genera una secuencia completa de deltas sintéticos.

    Args:
        instrument_id: ID del instrumento.
        start_price: Precio inicial del mid (USD).
        n_events: Número de eventos de actualización.
        event_interval_ms: Intervalo entre eventos en ms.
        seed: Semilla para reproducibilidad.
        include_volatile_period: Si True, incluye un período de alta volatilidad
                                  que debe activar el circuit breaker.
    """
    random.seed(seed)
    all_deltas: list[OrderBookDelta] = []
    seq = 0
    ts_start_ns = 1_700_000_000_000_000_000  # 2023-11-14 en ns
    interval_ns = event_interval_ms * 1_000_000

    mid = start_price

    # Snapshot inicial
    snapshot = generate_book_snapshot(
        instrument_id=instrument_id,
        mid=mid,
        spread_ticks=1,
        levels=5,
        seq_start=seq,
        ts_ns=ts_start_ns,
    )
    all_deltas.extend(snapshot)
    seq += len(snapshot)

    # Simulación de eventos
    for i in range(n_events):
        ts_ns = ts_start_ns + (i + 1) * interval_ns

        # Random walk del mid con reversión a la media
        drift = random.gauss(0, 0.5) * TICK
        mid = max(mid + drift, start_price * 0.99)  # precio mínimo = 99% del inicial
        mid = min(mid, start_price * 1.01)           # precio máximo = 101% del inicial

        # Período volátil (si se solicita): eventos 200-210 tienen saltos grandes
        if include_volatile_period and 200 <= i < 210:
            mid += random.choice([-1, 1]) * start_price * 0.003  # salto de ~0.3%

        best_bid = round(mid - TICK / 2, PRICE_PRECISION)
        best_ask = round(mid + TICK / 2, PRICE_PRECISION)

        # OBI oscilante con tendencia leve
        t = i / n_events
        obi_target = 0.4 * (2 * t - 1) + 0.3 * random.gauss(0, 1)
        obi_target = max(-0.9, min(0.9, obi_target))

        updates = generate_book_update(
            instrument_id=instrument_id,
            best_bid=best_bid,
            best_ask=best_ask,
            obi_target=obi_target,
            seq=seq,
            ts_ns=ts_ns,
        )
        all_deltas.extend(updates)
        seq += len(updates)

    return all_deltas
