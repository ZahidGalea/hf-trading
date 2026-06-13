from __future__ import annotations

import math


def compute_ofi(
    prev_bid_p: float,
    prev_bid_q: float,
    new_bid_p: float,
    new_bid_q: float,
    prev_ask_p: float,
    prev_ask_q: float,
    new_ask_p: float,
    new_ask_q: float,
) -> float:
    """OFI de Cont, Kukanov & Stoikov (2010) — fórmula corregida §2.1.

    Bug corregido: la rama de subida usa > estricto (no >=),
    para que el empate caiga en la rama de delta de tamaño (evento más frecuente).
    """
    if new_bid_p > prev_bid_p:
        bid_contrib = new_bid_q
    elif new_bid_p < prev_bid_p:
        bid_contrib = -prev_bid_q
    else:
        bid_contrib = new_bid_q - prev_bid_q

    if new_ask_p < prev_ask_p:
        ask_contrib = -new_ask_q
    elif new_ask_p > prev_ask_p:
        ask_contrib = prev_ask_q
    else:
        ask_contrib = -(new_ask_q - prev_ask_q)

    return bid_contrib + ask_contrib


def compute_obi(
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
    depth_levels: int,
) -> float:
    """OBI normalizado de Yagi et al. (2023) — §2.2.

    bids: lista de (precio, cantidad) ordenada de mejor a peor (mayor a menor precio).
    asks: lista de (precio, cantidad) ordenada de mejor a peor (menor a mayor precio).
    Retorna valor en [−1, 1]; 0 = equilibrio, >0 = buy-side pesado.
    """
    buy_depth = sum(q for _, q in bids[:depth_levels])
    sell_depth = sum(q for _, q in asks[:depth_levels])
    total = buy_depth + sell_depth
    if total == 0.0:
        return 0.0
    return (buy_depth - sell_depth) / total


def compute_micro_price(
    bid_p: float,
    bid_q: float,
    ask_p: float,
    ask_q: float,
) -> float:
    """Weighted-mid (proxy del micro-precio de Stoikov, citado en Yagi §2.3).

    Con bid_q >> ask_q el precio se "tira" hacia el ask (la cola compradora
    empujará el precio arriba). Aproximación de primer orden; ver flag §2.3.
    """
    total_q = bid_q + ask_q
    if total_q == 0.0:
        return (bid_p + ask_p) / 2.0
    return (ask_p * bid_q + bid_p * ask_q) / total_q


class EWMAVolatility:
    """EWMA de retornos al cuadrado — §6.2.

    Razón: STDEV crudo del mid mezcla volatilidad real con bid-ask bounce.
    Computa sigma como sqrt(EWMA de retornos²) sobre el mid.
    """

    def __init__(self, span: int) -> None:
        self._alpha: float = 2.0 / (span + 1)
        self._prev_mid: float | None = None
        self._ewma_var: float = 0.0
        self._has_var: bool = False

    def update(self, mid: float) -> float:
        """Actualiza con nuevo mid y retorna sigma estimada. Retorna 0.0 en el primer update."""
        if self._prev_mid is None:
            self._prev_mid = mid
            return 0.0
        ret = (mid - self._prev_mid) / self._prev_mid
        self._prev_mid = mid
        ret_sq = ret * ret
        if not self._has_var:
            self._ewma_var = ret_sq
            self._has_var = True
        else:
            self._ewma_var = self._alpha * ret_sq + (1.0 - self._alpha) * self._ewma_var
        return math.sqrt(self._ewma_var)
