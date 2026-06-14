from __future__ import annotations

import math
from collections import deque

from nautilus_trader.common.enums import LogColor
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

from config.btc_maker import ASMicroPriceMakerConfig
from live.order_logger import OrderLogger
from signals.microstructure import EWMAVolatility, compute_micro_price, compute_obi


class ASMicroPriceMaker(Strategy):
    """Market maker A-S horizonte infinito, maker-only, micro-precio + OBI defensivo.

    Implementa los 10 pasos del pseudocódigo §5.1:
    1. Micro-precio (ancla)
    2. EWMA volatilidad
    3. Circuit breaker
    4. Skew inventario A-S estacionario linealizado
    5. OBI (señal no-lineal)
    6. Precio de referencia
    7. Half-spread A-S
    8. Asimetría defensiva Cartea
    9. Viabilidad económica (comisiones)
    10. Re-quote solo si drift > 0.5 tick
    """

    def __init__(self, config: ASMicroPriceMakerConfig) -> None:
        super().__init__(config)
        # self.config es provisto por la clase base de NautilusTrader
        self._instrument_id = InstrumentId.from_str(config.instrument_id)
        self._ewma_vol = EWMAVolatility(span=config.sigma_span)
        # (timestamp_ns, mid) para el circuit breaker de 1s
        self._mid_hist: deque[tuple[int, float]] = deque(maxlen=500)
        self._active_bid_price: float | None = None
        self._active_ask_price: float | None = None
        self._last_requote_ns: int = 0  # throttle: timestamp del último re-quote
        self._logger: OrderLogger | None = None

    def on_start(self) -> None:
        self._instrument = self.cache.instrument(self._instrument_id)
        if self._instrument is None:
            self.log.error(f"Instrumento {self._instrument_id} no encontrado en cache.")
            return
        self.subscribe_order_book_deltas(self._instrument_id)
        self.subscribe_trade_ticks(self._instrument_id)

        self._logger = OrderLogger(db_path=self.config.log_db_path)
        self._logger.start()
        self.log.info(
            f"Order logger iniciado → {self._logger.db_path}", color=LogColor.GREEN
        )
        self.log.info("ASMicroPriceMaker iniciado.", color=LogColor.GREEN)

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        book = self.cache.order_book(self._instrument_id)
        if book is None:
            return

        bid_p = book.best_bid_price()
        ask_p = book.best_ask_price()
        bid_q = book.best_bid_size()
        ask_q = book.best_ask_size()

        if bid_p is None or ask_p is None or bid_q is None or ask_q is None:
            return

        bid_p_f = float(bid_p)
        ask_p_f = float(ask_p)
        bid_q_f = float(bid_q)
        ask_q_f = float(ask_q)
        tick = float(self._instrument.price_increment)

        # 1) ANCLA = micro-precio (mejor predictor corto plazo que mid)
        micro_price = compute_micro_price(bid_p_f, bid_q_f, ask_p_f, ask_q_f)
        spread_mkt = ask_p_f - bid_p_f
        mid = (bid_p_f + ask_p_f) / 2.0

        # 2) VOLATILIDAD EWMA (filtra bid-ask bounce — §6.2)
        sigma = self._ewma_vol.update(mid)
        now_ns = deltas.ts_event
        self._mid_hist.append((now_ns, mid))

        # 3) CIRCUIT BREAKER (Yagi §5.1 paso 3): pausar en flash crash
        one_sec_ns = 1_000_000_000
        mids_in_1s = [m for t, m in self._mid_hist if now_ns - t <= one_sec_ns]
        if len(mids_in_1s) >= 2:
            oldest = mids_in_1s[0]
            if oldest != 0.0 and abs(mid - oldest) / oldest > self.config.vol_breaker:
                self.log.warning("Circuit breaker activado — retirando del mercado.", color=LogColor.RED)
                self.cancel_all_orders(self._instrument_id)
                self._active_bid_price = None
                self._active_ask_price = None
                return

        # 4) SKEW DE INVENTARIO (A-S estacionario linealizado con tau)
        q = self._get_inventory()
        q_max = self.config.q_max
        gamma = self.config.gamma
        tau = self.config.tau
        k = self.config.k

        if sigma > 0.0:
            inv_skew = q * gamma * sigma ** 2 * tau
            inv_brake = inv_skew * (1.0 + (q / q_max) ** 2 if q_max != 0.0 else 0.0)
        else:
            inv_skew = 0.0
            inv_brake = 0.0

        # 5) SEÑAL OBI (no-lineal, Yagi §2.2 + defensa Cartea §2.6)
        bids_raw = list(book.bids())
        asks_raw = list(book.asks())
        # BookLevel tiene .price y .size
        bids_levels = [
            (float(level.price), level.size())
            for level in bids_raw[: self.config.obi_depth_levels]
        ]
        asks_levels = [
            (float(level.price), level.size())
            for level in asks_raw[: self.config.obi_depth_levels]
        ]
        obi = compute_obi(bids_levels, asks_levels, self.config.obi_depth_levels)

        # signal_skew: sign(obi)*obi² — no-lineal (pequeña corrección con señal débil, §Apéndice A)
        signal_skew = self.config.signal_gain * math.copysign(obi ** 2, obi) * spread_mkt

        # 6) PRECIO DE REFERENCIA
        ref_price = micro_price + signal_skew - inv_brake

        # 7) HALF-SPREAD (A-S estacionario)
        if sigma > 0.0 and gamma > 0.0 and k > 0.0:
            half_spread = 0.5 * (
                gamma * sigma ** 2 * tau + (2.0 / gamma) * math.log(1.0 + gamma / k)
            )
        else:
            half_spread = tick
        half_spread = max(tick, min(half_spread, 5.0 * tick))

        # 8) ASIMETRÍA DEFENSIVA CARTEA §2.6: retirar lado tóxico
        asym = 0.4 * abs(obi)
        if obi >= 0:
            bid_dist = half_spread * (1.0 - 0.4 * obi)
            ask_dist = half_spread * (1.0 + 0.4 * obi)
        else:
            bid_dist = half_spread * (1.0 + asym)
            ask_dist = half_spread * (1.0 - asym)
        bid_dist = max(bid_dist, tick)
        ask_dist = max(ask_dist, tick)

        quote_bid = self._round_down(ref_price - bid_dist, tick)
        quote_ask = self._round_up(ref_price + ask_dist, tick)

        # 9) VIABILIDAD ECONÓMICA §3: spread cotizado ≥ 2·f_maker·micro_price
        # Se agrega medio tick de margen extra para absorber errores de punto flotante en floor/ceil.
        min_spread = 2.0 * self.config.f_maker * micro_price
        if (quote_ask - quote_bid) < min_spread:
            half_min = min_spread / 2.0 + tick * 0.5
            quote_bid = self._round_down(ref_price - half_min, tick)
            quote_ask = self._round_up(ref_price + half_min, tick)

        # 10) RE-QUOTE SOLO SI DRIFT > threshold Y cooldown mínimo cumplido (Byrd §2.5)
        threshold = self.config.requote_threshold_ticks * tick
        bid_drift = abs(quote_bid - self._active_bid_price) if self._active_bid_price is not None else float("inf")
        ask_drift = abs(quote_ask - self._active_ask_price) if self._active_ask_price is not None else float("inf")

        min_interval_ns = self.config.requote_min_interval_ms * 1_000_000
        time_ok = (now_ns - self._last_requote_ns) >= min_interval_ns

        if (bid_drift > threshold or ask_drift > threshold) and time_ok:
            self._requote(quote_bid, quote_ask, q, now_ns)

        self.log.debug(
            f"micro={micro_price:.2f} obi={obi:.3f} sigma={sigma:.6f} q={q:.4f} "
            f"ref={ref_price:.2f} bid={quote_bid:.2f} ask={quote_ask:.2f}"
        )

    # ── Callbacks de estado de órdenes ────────────────────────────────────

    def on_order_submitted(self, event) -> None:
        if self._logger:
            self._logger.log_event(
                "submitted",
                client_order_id=str(event.client_order_id),
                instrument_id=str(self._instrument_id),
            )

    def on_order_accepted(self, event) -> None:
        if self._logger:
            self._logger.log_event(
                "accepted",
                client_order_id=str(event.client_order_id),
                venue_order_id=str(getattr(event, "venue_order_id", None) or ""),
                instrument_id=str(self._instrument_id),
            )

    def on_order_rejected(self, event) -> None:
        reason = str(getattr(event, "reason", "unknown"))
        self.log.warning(f"Orden rechazada: {event.client_order_id} — {reason}", color=LogColor.RED)
        if self._logger:
            self._logger.log_event(
                "rejected",
                client_order_id=str(event.client_order_id),
                instrument_id=str(self._instrument_id),
                reject_reason=reason,
            )

    def on_order_canceled(self, event) -> None:
        if self._logger:
            self._logger.log_event(
                "canceled",
                client_order_id=str(event.client_order_id),
                venue_order_id=str(getattr(event, "venue_order_id", None) or ""),
                instrument_id=str(self._instrument_id),
            )

    def on_order_cancel_rejected(self, event) -> None:
        reason = str(getattr(event, "reason", "unknown"))
        self.log.warning(
            f"Cancel rechazado: {event.client_order_id} — {reason}", color=LogColor.RED
        )
        if self._logger:
            self._logger.log_event(
                "cancel_rejected",
                client_order_id=str(event.client_order_id),
                instrument_id=str(self._instrument_id),
                reject_reason=reason,
            )

    def on_order_filled(self, event) -> None:
        q = self._get_inventory()
        fill_qty = float(event.last_qty)
        fill_price = float(event.last_px)
        side = "BUY" if event.is_buy else "SELL"
        self.log.info(
            f"Fill recibido — {side} {fill_qty:.4f} @ {fill_price:.2f} | inventario: {q:.4f} BTC",
            color=LogColor.CYAN,
        )
        if self._logger:
            self._logger.log_event(
                "filled",
                client_order_id=str(event.client_order_id),
                venue_order_id=str(getattr(event, "venue_order_id", None) or ""),
                instrument_id=str(self._instrument_id),
                order_side=side,
                fill_qty=fill_qty,
                fill_price=fill_price,
                inventory_qty=q,
            )
        if abs(q) > self.config.q_max:
            self._reduce_inventory_with_limit(q)

    def on_stop(self) -> None:
        self.cancel_all_orders(self._instrument_id)
        self._active_bid_price = None
        self._active_ask_price = None
        if self._logger:
            self._logger.stop()
        self.log.info("ASMicroPriceMaker detenido — órdenes canceladas.", color=LogColor.YELLOW)

    # --- Métodos internos ---

    def _get_inventory(self) -> float:
        pos = self.portfolio.net_position(self._instrument_id)
        return float(pos) if pos is not None else 0.0

    def _requote(self, quote_bid: float, quote_ask: float, q: float, now_ns: int) -> None:
        # Guard: no apilamos nuevas órdenes si aún hay envíos sin confirmar por el exchange
        if self.cache.orders_inflight(instrument_id=self._instrument_id):
            return

        self.cancel_all_orders(self._instrument_id)

        qty = self._instrument.make_qty(self.config.order_qty_btc)
        q_max = self.config.q_max

        # Tope de posición en la ruta de envío (§5 — Cartea cap):
        # si abs(q) >= q_max, cotizar solo el lado que reduce inventario.
        # Previene acumulación runaway aunque fallen las cancelaciones.
        submit_bid = q < q_max    # no agregar más long si ya en el límite
        submit_ask = q > -q_max   # no agregar más short si ya en el límite

        if submit_bid:
            bid_order = self.order_factory.limit(
                instrument_id=self._instrument_id,
                order_side=OrderSide.BUY,
                quantity=qty,
                price=self._instrument.make_price(quote_bid),
                time_in_force=TimeInForce.GTC,
                post_only=True,
            )
            self.submit_order(bid_order)
            self._active_bid_price = quote_bid

        if submit_ask:
            ask_order = self.order_factory.limit(
                instrument_id=self._instrument_id,
                order_side=OrderSide.SELL,
                quantity=qty,
                price=self._instrument.make_price(quote_ask),
                time_in_force=TimeInForce.GTC,
                post_only=True,
            )
            self.submit_order(ask_order)
            self._active_ask_price = quote_ask

        self._last_requote_ns = now_ns

        sides = ("BID " if submit_bid else "") + ("ASK" if submit_ask else "")
        self.log.info(
            f"Re-quote [{sides.strip()}] bid={quote_bid:.2f} ask={quote_ask:.2f} q={q:.4f}",
            color=LogColor.CYAN,
        )
        if self._logger:
            self._logger.log_event(
                "requote",
                quote_bid=quote_bid,
                quote_ask=quote_ask,
                inventory_qty=q,
                submit_bid=int(submit_bid),
                submit_ask=int(submit_ask),
            )

    def _reduce_inventory_with_limit(self, q: float) -> None:
        """Reduce inventario excedente con limit agresivo (no market, evita fee taker).

        Siempre envía order_qty_btc completo (no solo el exceso), porque el mínimo
        nocional de Binance (~50 USD) rechaza órdenes de exceso fraccional pequeño.
        """
        book = self.cache.order_book(self._instrument_id)
        if book is None:
            return
        mid_p = (float(book.best_bid_price()) + float(book.best_ask_price())) / 2.0
        tick = float(self._instrument.price_increment)

        if q > 0:
            price = self._instrument.make_price(mid_p - tick)
            side = OrderSide.SELL
        else:
            price = self._instrument.make_price(mid_p + tick)
            side = OrderSide.BUY

        reduce_order = self.order_factory.limit(
            instrument_id=self._instrument_id,
            order_side=side,
            quantity=self._instrument.make_qty(self.config.order_qty_btc),
            price=price,
            time_in_force=TimeInForce.GTC,
            post_only=True,
        )
        self.submit_order(reduce_order)

    @staticmethod
    def _round_down(price: float, tick: float) -> float:
        return math.floor(price / tick) * tick

    @staticmethod
    def _round_up(price: float, tick: float) -> float:
        return math.ceil(price / tick) * tick
