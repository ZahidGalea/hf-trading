import pytest
from signals.microstructure import (
    EWMAVolatility,
    compute_micro_price,
    compute_obi,
    compute_ofi,
)


def test_ofi_bid_price_rises():
    # Bid sube → contribución = +new_bid_q
    result = compute_ofi(
        prev_bid_p=100.0, prev_bid_q=5.0,
        new_bid_p=100.1,  new_bid_q=3.0,
        prev_ask_p=100.2, prev_ask_q=4.0,
        new_ask_p=100.2,  new_ask_q=4.0,
    )
    assert result == 3.0


def test_ofi_bid_price_falls():
    # Bid baja → contribución = -prev_bid_q
    result = compute_ofi(
        prev_bid_p=100.0, prev_bid_q=5.0,
        new_bid_p=99.9,   new_bid_q=3.0,
        prev_ask_p=100.2, prev_ask_q=4.0,
        new_ask_p=100.2,  new_ask_q=4.0,
    )
    assert result == -5.0


def test_ofi_bid_price_equal_size_grows():
    # Mismo precio, tamaño crece → +(new_q - prev_q)
    result = compute_ofi(
        prev_bid_p=100.0, prev_bid_q=5.0,
        new_bid_p=100.0,  new_bid_q=8.0,
        prev_ask_p=100.2, prev_ask_q=4.0,
        new_ask_p=100.2,  new_ask_q=4.0,
    )
    assert result == 3.0


def test_ofi_bid_price_equal_size_shrinks():
    # Mismo precio, tamaño encoge → negativo (bug clásico: >= capturaría esto mal)
    result = compute_ofi(
        prev_bid_p=100.0, prev_bid_q=8.0,
        new_bid_p=100.0,  new_bid_q=5.0,
        prev_ask_p=100.2, prev_ask_q=4.0,
        new_ask_p=100.2,  new_ask_q=4.0,
    )
    assert result == -3.0


def test_ofi_ask_price_falls_bearish():
    # Ask baja → oferta crece → bajista → -new_ask_q
    result = compute_ofi(
        prev_bid_p=100.0, prev_bid_q=5.0,
        new_bid_p=100.0,  new_bid_q=5.0,
        prev_ask_p=100.2, prev_ask_q=4.0,
        new_ask_p=100.1,  new_ask_q=6.0,
    )
    assert result == -6.0


def test_ofi_ask_price_rises_bullish():
    # Ask sube → oferta se retira → alcista → +prev_ask_q
    result = compute_ofi(
        prev_bid_p=100.0, prev_bid_q=5.0,
        new_bid_p=100.0,  new_bid_q=5.0,
        prev_ask_p=100.2, prev_ask_q=4.0,
        new_ask_p=100.3,  new_ask_q=6.0,
    )
    assert result == 4.0


def test_ofi_ask_price_equal_size_grows():
    # Mismo ask, tamaño crece → bajista → -(new_q - prev_q)
    result = compute_ofi(
        prev_bid_p=100.0, prev_bid_q=5.0,
        new_bid_p=100.0,  new_bid_q=5.0,
        prev_ask_p=100.2, prev_ask_q=4.0,
        new_ask_p=100.2,  new_ask_q=7.0,
    )
    assert result == -3.0


def test_ofi_combined_bullish():
    # Bid sube + ask sube → fuertemente alcista
    result = compute_ofi(
        prev_bid_p=100.0, prev_bid_q=5.0,
        new_bid_p=100.1,  new_bid_q=3.0,
        prev_ask_p=100.2, prev_ask_q=4.0,
        new_ask_p=100.3,  new_ask_q=6.0,
    )
    assert result == 7.0  # +3.0 (bid) + 4.0 (ask)


# --- OBI ---

def test_obi_balanced():
    bids = [(100.0, 5.0), (99.9, 3.0), (99.8, 2.0)]
    asks = [(100.1, 5.0), (100.2, 3.0), (100.3, 2.0)]
    result = compute_obi(bids, asks, depth_levels=3)
    assert result == 0.0


def test_obi_bid_heavy():
    bids = [(100.0, 10.0), (99.9, 5.0)]
    asks = [(100.1, 1.0), (100.2, 2.0)]
    result = compute_obi(bids, asks, depth_levels=2)
    expected = (15.0 - 3.0) / (15.0 + 3.0)
    assert abs(result - expected) < 1e-9


def test_obi_ask_heavy():
    bids = [(100.0, 1.0), (99.9, 2.0)]
    asks = [(100.1, 10.0), (100.2, 5.0)]
    result = compute_obi(bids, asks, depth_levels=2)
    expected = (3.0 - 15.0) / (3.0 + 15.0)
    assert abs(result - expected) < 1e-9


def test_obi_respects_depth_levels():
    # El 3er nivel tiene volumen masivo — no debe entrar si depth_levels=2
    bids = [(100.0, 10.0), (99.9, 5.0), (99.8, 1000.0)]
    asks = [(100.1, 1.0),  (100.2, 2.0), (100.3, 1000.0)]
    result = compute_obi(bids, asks, depth_levels=2)
    expected = (15.0 - 3.0) / (15.0 + 3.0)
    assert abs(result - expected) < 1e-9


def test_obi_range():
    bids = [(100.0, 1.0)]
    asks = [(100.1, 100.0)]
    result = compute_obi(bids, asks, depth_levels=1)
    assert -1.0 <= result <= 1.0


# --- Micro-precio ---

def test_micro_price_equal_sizes():
    result = compute_micro_price(bid_p=100.0, bid_q=5.0, ask_p=100.2, ask_q=5.0)
    assert abs(result - 100.1) < 1e-9


def test_micro_price_bid_heavy():
    # Cola bid grande → precio se "tira" hacia el ask
    # (100.2*9 + 100.0*1) / 10 = 100.18
    result = compute_micro_price(bid_p=100.0, bid_q=9.0, ask_p=100.2, ask_q=1.0)
    assert abs(result - 100.18) < 1e-9


def test_micro_price_ask_heavy():
    # Cola ask grande → precio se "tira" hacia el bid
    # (100.2*1 + 100.0*9) / 10 = 100.02
    result = compute_micro_price(bid_p=100.0, bid_q=1.0, ask_p=100.2, ask_q=9.0)
    assert abs(result - 100.02) < 1e-9


# --- EWMAVolatility ---

def test_ewma_vol_first_update_returns_zero():
    ewma = EWMAVolatility(span=10)
    result = ewma.update(100.0)
    assert result == 0.0


def test_ewma_vol_constant_price_is_zero():
    ewma = EWMAVolatility(span=10)
    for _ in range(20):
        result = ewma.update(100.0)
    assert result == 0.0


def test_ewma_vol_positive_after_moves():
    ewma = EWMAVolatility(span=10)
    prices = [100.0, 101.0, 99.0, 102.0, 98.0]
    results = [ewma.update(p) for p in prices]
    assert results[-1] > 0.0


def test_ewma_vol_different_spans_differ():
    ewma_fast = EWMAVolatility(span=5)
    ewma_slow = EWMAVolatility(span=50)
    prices = [100.0, 101.0, 99.5, 102.0, 98.0, 103.0]
    for p in prices:
        v_fast = ewma_fast.update(p)
        v_slow = ewma_slow.update(p)
    assert v_fast != v_slow


def test_ewma_vol_instances_independent():
    ewma1 = EWMAVolatility(span=10)
    ewma2 = EWMAVolatility(span=10)
    ewma1.update(100.0)
    ewma1.update(105.0)
    ewma2.update(100.0)
    # ewma2 solo tiene 1 update → prev_mid = 100.0, sin varianza aún
    assert ewma2._prev_mid == 100.0
    assert not ewma2._has_var
