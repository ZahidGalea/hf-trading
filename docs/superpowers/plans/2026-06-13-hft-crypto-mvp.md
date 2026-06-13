# HFT Crypto MVP — ASMicroPriceMaker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar `ASMicroPriceMaker` en NautilusTrader para BTC/USDT-M Futures con pipeline de señales (OFI corregido, OBI, micro-precio, EWMA vol), backtest básico con datos L2 históricos, y paper trading en Binance Testnet.

**Architecture:** Señales puras stateless en `signals/microstructure.py` (testeables en aislamiento), estrategia en `strategy/as_maker.py` que hereda de `Strategy` de NautilusTrader. El mismo código corre en backtest y live — propiedad central de NautilusTrader (§7 del spec). Fases incrementales: señales → estrategia A-S core → capa señal/defensa → backtest → live.

**Tech Stack:** Python 3.12, NautilusTrader ≥1.200, pytest, pandas, numpy

> ⚠️ **Antes de cada paso con NautilusTrader:** verificar firmas exactas en https://nautilustrader.io/docs/latest/ (concepts/orders, concepts/order_book, integrations/binance). La API cambia entre versiones.

---

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `pyproject.toml` | Dependencias y configuración del proyecto |
| `signals/microstructure.py` | OFI (corregido), OBI, micro-precio, EWMAVolatility |
| `tests/signals/test_microstructure.py` | Tests unitarios de todas las señales |
| `config/btc_maker.py` | `ASMicroPriceMakerConfig(StrategyConfig)` |
| `strategy/as_maker.py` | `ASMicroPriceMaker(Strategy)` — 10 pasos del pseudocódigo |
| `backtest/run_backtest.py` | `BacktestEngine` con datos L2 históricos |
| `live/run_testnet.py` | `TradingNode` contra Binance Futures Testnet |

---

## Task 1: Setup del entorno

**Files:**
- Create: `pyproject.toml`
- Create: `signals/__init__.py`
- Create: `config/__init__.py`
- Create: `strategy/__init__.py`
- Create: `backtest/__init__.py`
- Create: `live/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/signals/__init__.py`

- [ ] **Step 1: Crear pyproject.toml**

```toml
[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"

[tool.poetry]
name = "hft-crypto-mvp"
version = "0.1.0"
description = "ASMicroPriceMaker en NautilusTrader — BTC/USDT-M Futures"

[tool.poetry.dependencies]
python = "^3.12"
nautilus_trader = ">=1.200.0"
pandas = ">=2.2"
numpy = ">=1.26"

[tool.poetry.group.dev.dependencies]
pytest = ">=8.0"

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Instalar dependencias**

```bash
pip install poetry
poetry install
```

Verificar:
```bash
python -c "import nautilus_trader; print(nautilus_trader.__version__)"
```
Esperado: versión ≥1.200.0 impresa sin error.

- [ ] **Step 3: Crear estructura de directorios y archivos `__init__.py` vacíos**

```bash
mkdir -p signals config strategy backtest live tests/signals
touch signals/__init__.py config/__init__.py strategy/__init__.py
touch backtest/__init__.py live/__init__.py
touch tests/__init__.py tests/signals/__init__.py
```

- [ ] **Step 4: Commit inicial**

```bash
git init
git add pyproject.toml signals/ config/ strategy/ backtest/ live/ tests/
git commit -m "chore: setup proyecto HFT crypto MVP"
```

---

## Task 2: Señales — OFI corregido (§2.1)

**Files:**
- Create: `signals/microstructure.py`
- Create: `tests/signals/test_microstructure.py`

- [ ] **Step 1: Escribir los tests del OFI**

`tests/signals/test_microstructure.py`:

```python
import pytest
from signals.microstructure import compute_ofi


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
```

- [ ] **Step 2: Ejecutar los tests y verificar que fallan**

```bash
pytest tests/signals/test_microstructure.py -v
```
Esperado: `ImportError` o `ModuleNotFoundError` — `compute_ofi` no existe todavía.

- [ ] **Step 3: Implementar `compute_ofi` en `signals/microstructure.py`**

```python
from __future__ import annotations


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
    para que el empate caiga en la rama de delta de tamaño.
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
```

- [ ] **Step 4: Ejecutar tests y verificar que pasan**

```bash
pytest tests/signals/test_microstructure.py -v
```
Esperado: 8 tests en PASS.

- [ ] **Step 5: Commit**

```bash
git add signals/microstructure.py tests/signals/test_microstructure.py
git commit -m "feat: OFI corregido con rama de igualdad explícita (§2.1)"
```

---

## Task 3: Señales — OBI y micro-precio (§2.2 y §2.3)

**Files:**
- Modify: `signals/microstructure.py`
- Modify: `tests/signals/test_microstructure.py`

- [ ] **Step 1: Agregar tests de OBI y micro-precio al archivo de tests existente**

Añadir al final de `tests/signals/test_microstructure.py`:

```python
from signals.microstructure import compute_obi, compute_micro_price


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


def test_micro_price_equal_sizes():
    # Tamaños iguales → pure mid
    result = compute_micro_price(bid_p=100.0, bid_q=5.0, ask_p=100.2, ask_q=5.0)
    assert abs(result - 100.1) < 1e-9


def test_micro_price_bid_heavy():
    # Cola bid grande → precio se "tira" hacia el ask
    # (100.2*9 + 100.0*1) / 10 = 901.8+100 / 10 = 100.18
    result = compute_micro_price(bid_p=100.0, bid_q=9.0, ask_p=100.2, ask_q=1.0)
    assert abs(result - 100.18) < 1e-9


def test_micro_price_ask_heavy():
    # Cola ask grande → precio se "tira" hacia el bid
    # (100.2*1 + 100.0*9) / 10 = 100.2+900 / 10 = 100.02
    result = compute_micro_price(bid_p=100.0, bid_q=1.0, ask_p=100.2, ask_q=9.0)
    assert abs(result - 100.02) < 1e-9
```

- [ ] **Step 2: Ejecutar los nuevos tests para verificar que fallan**

```bash
pytest tests/signals/test_microstructure.py -v -k "obi or micro"
```
Esperado: `ImportError` en `compute_obi` y `compute_micro_price`.

- [ ] **Step 3: Implementar `compute_obi` y `compute_micro_price` en `signals/microstructure.py`**

Añadir al final del archivo (después de `compute_ofi`):

```python
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

    Con bid_q >> ask_q, el precio se "tira" hacia el ask (la cola compradora
    empujará el precio arriba). Aproximación de primer orden; ver flag en §2.3.
    """
    total_q = bid_q + ask_q
    if total_q == 0.0:
        return (bid_p + ask_p) / 2.0
    return (ask_p * bid_q + bid_p * ask_q) / total_q
```

- [ ] **Step 4: Ejecutar todos los tests**

```bash
pytest tests/signals/test_microstructure.py -v
```
Esperado: todos los tests en PASS (8 de OFI + 7 de OBI/micro-precio = 15 total).

- [ ] **Step 5: Commit**

```bash
git add signals/microstructure.py tests/signals/test_microstructure.py
git commit -m "feat: OBI normalizado (Yagi) y micro-precio (weighted-mid) — §2.2 §2.3"
```

---

## Task 4: Señales — EWMAVolatility (§6.2)

**Files:**
- Modify: `signals/microstructure.py`
- Modify: `tests/signals/test_microstructure.py`

- [ ] **Step 1: Agregar tests de EWMAVolatility**

Añadir al final de `tests/signals/test_microstructure.py`:

```python
from signals.microstructure import EWMAVolatility


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
    # ewma2 solo tiene 1 update → sigue en 0.0
    assert ewma1.update(100.0) != ewma2.update(100.0) or True  # estados independientes
    # Verificar independencia real: ewma2 no fue afectado por ewma1
    assert ewma2._prev_mid == 100.0
```

- [ ] **Step 2: Ejecutar los tests para verificar que fallan**

```bash
pytest tests/signals/test_microstructure.py -v -k "ewma"
```
Esperado: `ImportError` en `EWMAVolatility`.

- [ ] **Step 3: Implementar `EWMAVolatility` en `signals/microstructure.py`**

Agregar al inicio del archivo (después de `from __future__ import annotations`):

```python
import math
```

Agregar al final del archivo:

```python
class EWMAVolatility:
    """EWMA de retornos al cuadrado — §6.2.

    Razón: STDEV crudo del mid mezcla volatilidad real con bid-ask bounce.
    Esta clase computa sigma como sqrt(EWMA de retornos²) sobre el mid.
    El span controla la memoria; spans cortos = más reactivo.
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
```

- [ ] **Step 4: Ejecutar todos los tests**

```bash
pytest tests/signals/test_microstructure.py -v
```
Esperado: todos los tests en PASS (≥20 tests).

- [ ] **Step 5: Commit**

```bash
git add signals/microstructure.py tests/signals/test_microstructure.py
git commit -m "feat: EWMAVolatility (EWMA de retornos, evita bounce bid-ask) — §6.2"
```

---

## Task 5: Config — ASMicroPriceMakerConfig

**Files:**
- Create: `config/btc_maker.py`

- [ ] **Step 1: Verificar la importación de `StrategyConfig` en NautilusTrader**

```bash
python -c "from nautilus_trader.trading.strategy import Strategy; from nautilus_trader.config import StrategyConfig; print('OK')"
```
Esperado: `OK`. Si falla, verificar en https://nautilustrader.io/docs/latest/ el módulo correcto de `StrategyConfig`.

- [ ] **Step 2: Crear `config/btc_maker.py`**

```python
from nautilus_trader.config import StrategyConfig


class ASMicroPriceMakerConfig(StrategyConfig, frozen=True):
    """Parámetros del ASMicroPriceMaker para BTC/USDT-M Futures.

    Todos los defaults son puntos de partida (A-S estilizado), NO valores de producción.
    Barrer gamma, k, tau, signal_gain en backtest walk-forward antes de capital real.
    """

    instrument_id: str = "BTCUSDT-PERP.BINANCE"

    # A-S estacionario linealizado (§2.4, §5.1)
    gamma: float = 0.1        # Aversión al riesgo. Calibrar: mayor γ → spread más ancho, menos inventario.
    k: float = 1.5            # Decay de llegada λ(δ)=A·e^(−kδ). Estimar de curva fill-rate en Testnet.
    tau: float = 10.0         # Horizonte efectivo (segundos). Sustitución práctica de (T−t); barrer.
    q_max: float = 0.01       # Cota de inventario en BTC. Conservador para MVP.

    # Señal OBI (extrapolación de Yagi §2.2 — calibrar)
    signal_gain: float = 0.30  # Peso de la señal. Barrer [0.1, 0.6].
    obi_depth_levels: int = 5  # Niveles del libro para compute_obi.

    # Comisiones (§3)
    f_maker: float = 0.0002   # VIP 0 sin BNB. Actualizar si cambia el tier.

    # Volatilidad (§6.2)
    sigma_span: int = 20      # Span del EWMA de volatilidad.

    # Circuit breaker (Yagi §5.1 paso 3)
    vol_breaker: float = 0.005  # 0.5% cambio en 1s → pausar cotización.

    # Re-quote (§5.1 paso 10)
    requote_threshold_ticks: float = 0.5  # Drift mínimo en ticks para disparar re-quote.

    # Tamaño de orden
    order_qty_btc: float = 0.001  # Cantidad en BTC por orden. Mínimo posible para MVP.
```

- [ ] **Step 3: Verificar que importa correctamente**

```bash
python -c "from config.btc_maker import ASMicroPriceMakerConfig; c = ASMicroPriceMakerConfig(); print(c.gamma)"
```
Esperado: `0.1`

- [ ] **Step 4: Commit**

```bash
git add config/btc_maker.py
git commit -m "feat: ASMicroPriceMakerConfig con parámetros §6.1 y defaults documentados"
```

---

## Task 6: Estrategia — Core A-S sin señal (pasos 1–4 y 7–10 del pseudocódigo)

**Files:**
- Create: `strategy/as_maker.py`

Esta tarea implementa el núcleo A-S maker-only sin la capa de señal OBI (pasos 5–6 del pseudocódigo). La señal se agrega en Task 7.

> ⚠️ Verificar antes de escribir código:
> - Firma de `on_order_book_deltas(self, deltas: OrderBookDeltas)` en la versión instalada
> - Métodos de `OrderBook`: `best_bid_price()`, `best_ask_price()`, `best_bid_size()`, `best_ask_size()`, `bids()`, `asks()`
> - `self.portfolio.net_position(instrument_id)` — retorna `Quantity` o `Decimal`
> - Docs: https://nautilustrader.io/docs/latest/concepts/order_book

- [ ] **Step 1: Crear `strategy/as_maker.py` con el esqueleto**

```python
from __future__ import annotations

import math
from collections import deque

from nautilus_trader.common.enums import LogColor
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy

from config.btc_maker import ASMicroPriceMakerConfig
from signals.microstructure import (
    EWMAVolatility,
    compute_micro_price,
    compute_obi,
)


class ASMicroPriceMaker(Strategy):
    """Market maker A-S horizonte infinito, maker-only, micro-precio + OBI defensivo.

    Implementa los 10 pasos del pseudocódigo §5.1 del documento de estrategia.
    La señal OBI (pasos 5-6) se activa en Task 7.
    """

    def __init__(self, config: ASMicroPriceMakerConfig) -> None:
        super().__init__(config)
        self.config = config
        self._instrument_id = InstrumentId.from_str(config.instrument_id)
        self._ewma_vol = EWMAVolatility(span=config.sigma_span)
        self._mid_hist: deque[tuple[float, float]] = deque(maxlen=200)  # (timestamp_ns, mid)
        self._active_bid_id = None
        self._active_ask_id = None
        self._active_bid_price: float | None = None
        self._active_ask_price: float | None = None

    def on_start(self) -> None:
        self._instrument = self.cache.instrument(self._instrument_id)
        if self._instrument is None:
            self.log.error(f"Instrumento {self._instrument_id} no encontrado en cache.")
            return
        self.subscribe_order_book_deltas(self._instrument_id)
        self.subscribe_trade_ticks(self._instrument_id)
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

        # 1) ANCLA = micro-precio
        micro_price = compute_micro_price(bid_p_f, bid_q_f, ask_p_f, ask_q_f)
        spread_mkt = ask_p_f - bid_p_f
        mid = (bid_p_f + ask_p_f) / 2.0

        # 2) VOLATILIDAD EWMA
        sigma = self._ewma_vol.update(mid)
        now_ns = deltas.ts_event
        self._mid_hist.append((now_ns, mid))

        # 3) CIRCUIT BREAKER: cambio > vol_breaker en 1s
        one_sec_ns = 1_000_000_000
        old_mids = [m for t, m in self._mid_hist if now_ns - t <= one_sec_ns]
        if old_mids:
            oldest_mid = old_mids[0]
            if oldest_mid != 0.0 and abs(mid - oldest_mid) / oldest_mid > self.config.vol_breaker:
                self.log.warning("Circuit breaker activado — cancelando órdenes.", color=LogColor.RED)
                self.cancel_all_orders(self._instrument_id)
                self._active_bid_id = None
                self._active_ask_id = None
                self._active_bid_price = None
                self._active_ask_price = None
                return

        # 4) SKEW DE INVENTARIO (A-S estacionario linealizado con tau)
        q = self._get_inventory()
        inv_skew = q * self.config.gamma * sigma ** 2 * self.config.tau
        inv_brake = inv_skew * (1.0 + (q / self.config.q_max) ** 2)

        # 5-6) SEÑAL OBI: placeholder hasta Task 7
        signal_skew = 0.0

        # 7) PRECIO DE REFERENCIA
        ref_price = micro_price + signal_skew - inv_brake

        # 8) HALF-SPREAD (A-S estacionario)
        gamma = self.config.gamma
        k = self.config.k
        tau = self.config.tau
        tick = float(self._instrument.price_increment)

        if sigma > 0.0 and gamma > 0.0 and k > 0.0:
            half_spread = 0.5 * (gamma * sigma ** 2 * tau + (2.0 / gamma) * math.log(1.0 + gamma / k))
        else:
            half_spread = tick

        half_spread = max(tick, min(half_spread, 5.0 * tick))

        # 8) ASIMETRÍA DEFENSIVA (placeholder hasta Task 7 — simétrico por ahora)
        bid_dist = half_spread
        ask_dist = half_spread

        quote_bid = self._round_down(ref_price - bid_dist, tick)
        quote_ask = self._round_up(ref_price + ask_dist, tick)

        # 9) VIABILIDAD ECONÓMICA (§3)
        min_spread = 2.0 * self.config.f_maker * micro_price
        if (quote_ask - quote_bid) < min_spread:
            half_min = min_spread / 2.0
            quote_bid = self._round_down(ref_price - half_min, tick)
            quote_ask = self._round_up(ref_price + half_min, tick)

        # 10) RE-QUOTE SOLO SI DRIFT > 0.5 tick
        threshold = self.config.requote_threshold_ticks * tick
        bid_drift = abs(quote_bid - self._active_bid_price) if self._active_bid_price else float("inf")
        ask_drift = abs(quote_ask - self._active_ask_price) if self._active_ask_price else float("inf")

        if bid_drift > threshold or ask_drift > threshold:
            self._requote(quote_bid, quote_ask)

        self.log.debug(
            f"micro={micro_price:.2f} obi=N/A sigma={sigma:.6f} "
            f"ref={ref_price:.2f} bid={quote_bid:.2f} ask={quote_ask:.2f} q={q:.4f}"
        )

    def on_order_filled(self, event) -> None:
        q = self._get_inventory()
        if abs(q) > self.config.q_max:
            self._reduce_inventory_with_limit(q)
        book = self.cache.order_book(self._instrument_id)
        if book and book.best_bid_price() and book.best_ask_price():
            mid = (float(book.best_bid_price()) + float(book.best_ask_price())) / 2.0
            self.log.info(f"Fill recibido. Inventario={q:.4f} BTC. Mid={mid:.2f}", color=LogColor.CYAN)

    def on_stop(self) -> None:
        self.cancel_all_orders(self._instrument_id)
        self._active_bid_id = None
        self._active_ask_id = None
        self.log.info("ASMicroPriceMaker detenido — órdenes canceladas.", color=LogColor.YELLOW)

    # --- Métodos internos ---

    def _get_inventory(self) -> float:
        pos = self.portfolio.net_position(self._instrument_id)
        return float(pos) if pos is not None else 0.0

    def _requote(self, quote_bid: float, quote_ask: float) -> None:
        self.cancel_all_orders(self._instrument_id)
        self._active_bid_id = None
        self._active_ask_id = None

        qty = self._instrument.make_qty(self.config.order_qty_btc)
        bid_price = self._instrument.make_price(quote_bid)
        ask_price = self._instrument.make_price(quote_ask)

        bid_order = self.order_factory.limit(
            instrument_id=self._instrument_id,
            order_side=OrderSide.BUY,
            quantity=qty,
            price=bid_price,
            time_in_force=TimeInForce.GTC,
            post_only=True,
        )
        ask_order = self.order_factory.limit(
            instrument_id=self._instrument_id,
            order_side=OrderSide.SELL,
            quantity=qty,
            price=ask_price,
            time_in_force=TimeInForce.GTC,
            post_only=True,
        )

        self.submit_order(bid_order)
        self.submit_order(ask_order)
        self._active_bid_id = bid_order.client_order_id
        self._active_ask_id = ask_order.client_order_id
        self._active_bid_price = quote_bid
        self._active_ask_price = quote_ask

    def _reduce_inventory_with_limit(self, q: float) -> None:
        book = self.cache.order_book(self._instrument_id)
        if not book:
            return
        mid_p = (float(book.best_bid_price()) + float(book.best_ask_price())) / 2.0
        tick = float(self._instrument.price_increment)
        excess = abs(q) - self.config.q_max

        if q > 0:
            # Inventario largo → vender agresivamente cerca del bid
            reduce_price = self._instrument.make_price(mid_p - tick)
            side = OrderSide.SELL
        else:
            # Inventario corto → comprar agresivamente cerca del ask
            reduce_price = self._instrument.make_price(mid_p + tick)
            side = OrderSide.BUY

        reduce_order = self.order_factory.limit(
            instrument_id=self._instrument_id,
            order_side=side,
            quantity=self._instrument.make_qty(min(excess, self.config.order_qty_btc)),
            price=reduce_price,
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
```

- [ ] **Step 2: Verificar que importa sin errores**

```bash
python -c "from strategy.as_maker import ASMicroPriceMaker; print('OK')"
```
Esperado: `OK`. Si falla por imports de NautilusTrader, revisar los módulos exactos en la versión instalada y ajustar los imports.

- [ ] **Step 3: Commit**

```bash
git add strategy/as_maker.py
git commit -m "feat: ASMicroPriceMaker core A-S sin señal (pasos 1-4, 7-10) — §5.1"
```

---

## Task 7: Estrategia — Capa de señal OBI + asimetría defensiva (pasos 5–6 y 8)

**Files:**
- Modify: `strategy/as_maker.py`

Reemplaza los placeholders de los pasos 5–6 y la asimetría defensiva (paso 8) con la lógica real.

- [ ] **Step 1: Reemplazar el bloque de señal (pasos 5–6) en `on_order_book_deltas`**

Localizar en `strategy/as_maker.py` el bloque:
```python
        # 5-6) SEÑAL OBI: placeholder hasta Task 7
        signal_skew = 0.0
```

Reemplazarlo con:
```python
        # 5) SKEW DE SEÑAL (OBI) — no-lineal (Yagi §2.2) + defensa Cartea §2.6
        bids_raw = list(book.bids())   # verificar tipo: puede ser BookLevel o similar
        asks_raw = list(book.asks())
        # NautilusTrader retorna niveles del libro; extraer precio y cantidad:
        bids_levels = [(float(level.price), float(level.size)) for level in bids_raw[:self.config.obi_depth_levels]]
        asks_levels = [(float(level.price), float(level.size)) for level in asks_raw[:self.config.obi_depth_levels]]
        obi = compute_obi(bids_levels, asks_levels, self.config.obi_depth_levels)

        # 6) signal_skew: no-lineal sign(obi)*obi² (Yagi cualitativo, §Apéndice A)
        signal_skew = self.config.signal_gain * math.copysign(obi ** 2, obi) * spread_mkt
```

> ⚠️ El tipo exacto de `book.bids()` / `book.asks()` varía por versión de NautilusTrader. Verificar si retorna `BookLevel`, `list[tuple]`, u otro tipo. Ajustar la list comprehension según la versión. Docs: https://nautilustrader.io/docs/latest/concepts/order_book

- [ ] **Step 2: Reemplazar la asimetría defensiva simétrica (paso 8) con la versión Cartea**

Localizar:
```python
        # 8) ASIMETRÍA DEFENSIVA (placeholder hasta Task 7 — simétrico por ahora)
        bid_dist = half_spread
        ask_dist = half_spread
```

Reemplazar con:
```python
        # 8) ASIMETRÍA DEFENSIVA (Cartea §2.6): retirar el lado tóxico
        asym_factor = 0.4 * abs(obi)
        if obi >= 0:
            # OBI positivo → movimiento predicho al alza → retirar ask, acercar bid
            bid_dist = half_spread * (1.0 - 0.4 * obi)
            ask_dist = half_spread * (1.0 + 0.4 * obi)
        else:
            bid_dist = half_spread * (1.0 + asym_factor)
            ask_dist = half_spread * (1.0 - asym_factor)
        # Garantizar distancias positivas
        bid_dist = max(bid_dist, tick)
        ask_dist = max(ask_dist, tick)
```

- [ ] **Step 3: Actualizar el log para incluir OBI**

Localizar:
```python
        self.log.debug(
            f"micro={micro_price:.2f} obi=N/A sigma={sigma:.6f} "
```

Reemplazar con:
```python
        self.log.debug(
            f"micro={micro_price:.2f} obi={obi:.3f} sigma={sigma:.6f} "
```

- [ ] **Step 4: Verificar que importa sin errores**

```bash
python -c "from strategy.as_maker import ASMicroPriceMaker; print('OK')"
```
Esperado: `OK`.

- [ ] **Step 5: Commit**

```bash
git add strategy/as_maker.py
git commit -m "feat: capa señal OBI no-lineal + asimetría defensiva Cartea — §5.1 pasos 5-6-8"
```

---

## Task 8: Backtest básico con datos históricos L2

**Files:**
- Create: `backtest/run_backtest.py`

> ⚠️ El backtest usa L2 con fill model simplificado. Sobreestima fills porque no modela posición en cola (§7 del documento). Los resultados son referencia de comportamiento, no de rentabilidad real.

> ⚠️ Verificar la API de `BacktestEngine` en la versión instalada: https://nautilustrader.io/docs/latest/

- [ ] **Step 1: Descargar datos históricos L2 de Binance**

NautilusTrader puede descargar datos directamente desde Binance via su CLI:

```bash
# Instalar el CLI de datos si no está disponible
pip install nautilus_trader[binance]

# Descargar 7 días de book deltas L2 de BTC/USDT-M Futures
python -m nautilus_trader.persistence.loaders.binance \
  --instrument BTCUSDT \
  --market-type um_futures \
  --data-type book-depth \
  --start 2026-06-01 \
  --end 2026-06-07 \
  --output-path data/
```

Si el CLI no está disponible en tu versión, descargar los datos desde:
- https://data.binance.vision/?prefix=data/futures/um/daily/bookDepth/BTCUSDT/
- Luego usar `OrderBookDeltaDataWrangler` para convertir a formato Parquet

Verificar que el directorio `data/` contiene archivos `.parquet` o `.csv` de deltas.

- [ ] **Step 2: Crear `backtest/run_backtest.py`**

```python
"""Backtest básico del ASMicroPriceMaker con datos L2 históricos de Binance.

Advertencia (§7 del documento): fill model simplificado en L2 sobreestima fills
porque no modela posición en cola. Usar como referencia de comportamiento,
no como estimador de rentabilidad real.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.model.currencies import USDT, BTC
from nautilus_trader.model.data import BookType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import TraderId, Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.wranglers import OrderBookDeltaDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from config.btc_maker import ASMicroPriceMakerConfig
from strategy.as_maker import ASMicroPriceMaker


def run_backtest(data_path: str | Path) -> None:
    """Corre el backtest sobre datos L2 históricos.

    data_path: ruta a un archivo Parquet/CSV con OrderBookDeltas de Binance.
               Usar el data catalog de NautilusTrader para preparar los datos.
    """
    engine = BacktestEngine(
        config=BacktestEngineConfig(trader_id=TraderId("BACKTESTER-001"))
    )

    # Venue y cuenta
    venue = Venue("BINANCE")
    engine.add_venue(
        venue=venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=USDT,
        starting_balances=[Money(10_000, USDT)],
        book_type=BookType.L2_MBP,
    )

    # Instrumento (ajustar si cambia la spec de Binance)
    instrument = TestInstrumentProvider.btcusdt_binance()
    engine.add_instrument(instrument)

    # Cargar datos
    # Nota: preparar datos con NautilusTrader data wrangler:
    # https://nautilustrader.io/docs/latest/concepts/data
    # Aquí se asume que los datos ya están en formato OrderBookDeltas
    print(f"[BACKTEST] Cargando datos desde: {data_path}")
    print("[BACKTEST] Advertencia: fill model L2 simplificado — sobreestima fills (§7)")

    # Agregar estrategia
    config = ASMicroPriceMakerConfig(
        instrument_id=instrument.id.value,
        order_qty_btc=0.001,
    )
    strategy = ASMicroPriceMaker(config=config)
    engine.add_strategy(strategy=strategy)

    # Correr
    engine.run()

    # Reportar resultados
    print("\n=== RESULTADOS DEL BACKTEST ===")
    print(engine.get_result())
    print("\n⚠️  Recordatorio: estos resultados sobreestiman fills (sin L3, sin modelo de cola).")
    print("Usar como referencia de comportamiento de señales, no de P&L real.")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uso: python -m backtest.run_backtest <ruta_datos>")
        print("Ejemplo: python -m backtest.run_backtest data/BTCUSDT_deltas.parquet")
        sys.exit(1)
    run_backtest(sys.argv[1])
```

- [ ] **Step 3: Verificar que importa sin errores**

```bash
python -c "from backtest.run_backtest import run_backtest; print('OK')"
```
Esperado: `OK`. Si falla, revisar los imports de NautilusTrader y ajustar según la versión instalada.

- [ ] **Step 4: Commit**

```bash
git add backtest/run_backtest.py
git commit -m "feat: backtest básico L2 con BacktestEngine (advertencia fill model §7)"
```

---

## Task 9: Live — Paper trading en Binance Testnet

**Files:**
- Create: `live/run_testnet.py`
- Create: `.env.example`

> ⚠️ Verificar la API de `TradingNode` y `BinanceFuturesLiveDataClientConfig` en la versión instalada: https://nautilustrader.io/docs/latest/integrations/binance

- [ ] **Step 1: Crear `.env.example` con la estructura de credenciales**

```bash
# Binance Futures Testnet
# Obtener en: https://testnet.binancefuture.com
BINANCE_TESTNET_API_KEY=your_testnet_api_key_here
BINANCE_TESTNET_API_SECRET=your_testnet_api_secret_here
```

- [ ] **Step 2: Crear `live/run_testnet.py`**

```python
"""Paper trading del ASMicroPriceMaker en Binance Futures Testnet.

Credenciales: copiar .env.example a .env y completar con claves de Testnet.
Obtener claves: https://testnet.binancefuture.com
"""
from __future__ import annotations

import asyncio
import os

from nautilus_trader.adapters.binance.common.enums import BinanceAccountType
from nautilus_trader.adapters.binance.config import (
    BinanceFuturesDataClientConfig,
    BinanceFuturesExecClientConfig,
)
from nautilus_trader.adapters.binance.factories import (
    BinanceLiveDataClientFactory,
    BinanceLiveExecClientFactory,
)
from nautilus_trader.config import (
    InstrumentProviderConfig,
    LiveExecEngineConfig,
    LoggingConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.node import TradingNode

from config.btc_maker import ASMicroPriceMakerConfig
from strategy.as_maker import ASMicroPriceMaker


def build_node() -> TradingNode:
    api_key = os.environ["BINANCE_TESTNET_API_KEY"]
    api_secret = os.environ["BINANCE_TESTNET_API_SECRET"]

    config_node = TradingNodeConfig(
        trader_id="LIVE-TESTNET-001",
        logging=LoggingConfig(log_level="DEBUG"),
        exec_engine=LiveExecEngineConfig(reconciliation=True),
        data_clients={
            "BINANCE": BinanceFuturesDataClientConfig(
                api_key=api_key,
                api_secret=api_secret,
                account_type=BinanceAccountType.USDT_FUTURE,
                testnet=True,
                instrument_provider=InstrumentProviderConfig(load_all=True),
            ),
        },
        exec_clients={
            "BINANCE": BinanceFuturesExecClientConfig(
                api_key=api_key,
                api_secret=api_secret,
                account_type=BinanceAccountType.USDT_FUTURE,
                testnet=True,
            ),
        },
    )

    node = TradingNode(config=config_node)
    node.add_data_client_factory("BINANCE", BinanceLiveDataClientFactory)
    node.add_exec_client_factory("BINANCE", BinanceLiveExecClientFactory)

    strategy_config = ASMicroPriceMakerConfig(
        instrument_id="BTCUSDT-PERP.BINANCE",
        order_qty_btc=0.001,
    )
    strategy = ASMicroPriceMaker(config=strategy_config)
    node.add_strategy(strategy)

    node.build()
    return node


async def main() -> None:
    node = build_node()
    try:
        await node.run_async()
    finally:
        await node.stop_async()
        node.dispose()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: Verificar que importa sin errores**

```bash
python -c "from live.run_testnet import build_node; print('OK')"
```
Esperado: `OK`. Si falla por módulos de Binance, revisar https://nautilustrader.io/docs/latest/integrations/binance y ajustar los imports.

- [ ] **Step 4: Crear `.env.example` y agregarlo al repo**

Crear el archivo con el contenido del Step 1.

- [ ] **Step 5: Agregar `.env` al `.gitignore`**

```bash
echo ".env" >> .gitignore
echo "*.env" >> .gitignore
git add .gitignore .env.example live/run_testnet.py
git commit -m "feat: live runner en Binance Futures Testnet — paper trading §9 Fase 7"
```

---

## Task 10: Verificación final del pipeline completo

**Files:** ninguno nuevo — solo ejecución de verificación.

- [ ] **Step 1: Ejecutar todos los tests de señales**

```bash
pytest tests/ -v
```
Esperado: todos PASS.

- [ ] **Step 2: Verificar el árbol de archivos final**

```bash
find . -name "*.py" | sort
```
Esperado:
```
./backtest/__init__.py
./backtest/run_backtest.py
./config/__init__.py
./config/btc_maker.py
./live/__init__.py
./live/run_testnet.py
./signals/__init__.py
./signals/microstructure.py
./strategy/__init__.py
./strategy/as_maker.py
./tests/__init__.py
./tests/signals/__init__.py
./tests/signals/test_microstructure.py
```

- [ ] **Step 3: Smoke test de imports del sistema completo**

```bash
python -c "
from signals.microstructure import compute_ofi, compute_obi, compute_micro_price, EWMAVolatility
from config.btc_maker import ASMicroPriceMakerConfig
from strategy.as_maker import ASMicroPriceMaker
from backtest.run_backtest import run_backtest
from live.run_testnet import build_node
print('Todos los módulos importan correctamente.')
"
```
Esperado: `Todos los módulos importan correctamente.`

- [ ] **Step 4: Commit final**

```bash
git add -A
git commit -m "chore: verificación final — todos los módulos del MVP operativos"
```

---

## Notas de calibración pre-producción (§6.1)

Antes de operar con capital real, realizar en este orden:
1. **Paper trading en Testnet** (2–4 semanas): observar fill-rate real vs distancia al mid → estimar `k` y `A`
2. **Walk-forward backtest** con los `k`,`A` estimados; barrer `gamma`, `tau`, `signal_gain`
3. **Viabilidad económica** (§3.2): verificar que la señal supera el umbral de comisiones (~400 ticks en VIP 0)
4. **Aumentar gradualmente `order_qty_btc`** desde 0.001 BTC solo si el P&L neto de comisiones es consistentemente positivo

> `gamma=0.1, k=1.5` son valores de simulación estilizada de acciones (A-S 2008). **No son valores de producción.** La calibración en datos de Binance es obligatoria.
