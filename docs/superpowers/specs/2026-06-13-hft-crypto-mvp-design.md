# MVP HFT Crypto — ASMicroPriceMaker en NautilusTrader

**Fecha:** 2026-06-13
**Versión:** 1.0
**Basado en:** `HFT_Crypto_Estrategia_Documento_Final.md`

---

## Contexto y decisiones fijas

- **Par:** BTC/USDT-M Futures (Binance)
- **Comisión maker:** 0.0200% (VIP 0, sin BNB)
- **Entorno:** partir desde cero (Python + NautilusTrader + Testnet)
- **Alcance:** Estrategia 1 (`ASMicroPriceMaker`) corriendo en Testnet + backtest básico con datos L2 históricos

---

## Arquitectura de archivos

```
trading/
├── pyproject.toml
├── config/
│   └── btc_maker.py            # ASMicroPriceMakerConfig
├── signals/
│   └── microstructure.py       # OFI, OBI, micro-precio, EWMAVolatility
├── strategy/
│   └── as_maker.py             # ASMicroPriceMaker (Strategy NautilusTrader)
├── backtest/
│   └── run_backtest.py         # BacktestEngine con datos L2 históricos
└── live/
    └── run_testnet.py          # TradingNode contra Binance Testnet
```

---

## Componentes

### `signals/microstructure.py`

Funciones puras (sin estado), fáciles de testear en aislamiento:

- `compute_ofi(prev_bid_p, prev_bid_q, new_bid_p, new_bid_q, prev_ask_p, prev_ask_q, new_ask_p, new_ask_q) → float`
  - Implementa la fórmula **corregida** de §2.1 con las tres ramas por lado (precio sube / baja / igual) y rama de igualdad explícita para delta de tamaño.
  - Bug conocido a evitar: NO usar `>=` en la rama de subida (infla OFI al capturar el empate).

- `compute_obi(bids: list[tuple[float,float]], asks: list[tuple[float,float]], depth_levels: int) → float`
  - Normalización Yagi: `(buy_depth − sell_depth) / (buy_depth + sell_depth)` ∈ [−1, 1]
  - `buy_depth` = suma de volumen en los `depth_levels` mejores bids; idem asks.

- `compute_micro_price(bid_p, bid_q, ask_p, ask_q) → float`
  - Weighted-mid de §2.3: `(ask_p · bid_q + bid_p · ask_q) / (bid_q + ask_q)`
  - Nota: proxy de primer orden del micro-precio completo de Stoikov (2018); ver §2.3 del documento base.

- `class EWMAVolatility`
  - `.update(mid: float) → float` — retorna sigma (std de retornos, EWMA)
  - Span configurable; NO usar STDEV crudo sobre ventana fija (mezcla bounce bid-ask con vol real).

### `config/btc_maker.py`

```python
class ASMicroPriceMakerConfig(StrategyConfig):
    instrument_id: str = "BTCUSDT-PERP.BINANCE"
    gamma: float = 0.1        # aversión al riesgo (juguete A-S; calibrar)
    k: float = 1.5            # decay de llegada (juguete; estimar de fill-rate)
    tau: float = 10.0         # horizonte efectivo linealizado (segundos)
    q_max: float = 0.01       # cota de inventario en BTC (conservador MVP)
    signal_gain: float = 0.30 # peso de la señal OBI (extrapolación; barrer)
    f_maker: float = 0.0002   # comisión maker VIP 0
    sigma_span: int = 20      # span EWMA para volatilidad
    vol_breaker: float = 0.005 # circuit breaker: 0.5% cambio en 1s
    obi_depth_levels: int = 5  # niveles de libro para OBI
    requote_threshold_ticks: float = 0.5  # drift mínimo para re-quote
```

### `strategy/as_maker.py`

`class ASMicroPriceMaker(Strategy)` implementa los 10 pasos del pseudocódigo §5.1:

**`on_start`:**
- Suscribir `order_book_deltas` (L2 incremental)
- Suscribir `trade_ticks`
- Inicializar `EWMAVolatility`, buffers OFI/OBI

**`on_order_book_deltas(deltas)`:**
1. Leer top of book (`best_bid_price/size`, `best_ask_price/size`)
2. `micro_price ← compute_micro_price(...)`; `sigma ← ewma.update(mid)`
3. Circuit breaker: si `|Δmid_1s| / mid > vol_breaker` → `cancel_all_orders()` + limit en micro-precio + return
4. `inv_skew ← q · gamma · sigma² · tau` (q de `self.portfolio`)
5. `obi ← compute_obi(...)` ; `signal_skew ← signal_gain · sign(obi) · obi² · spread_mkt`
6. `inv_brake ← inv_skew · (1 + (q/q_max)²)` ; `ref_price ← micro_price + signal_skew − inv_brake`
7. `half_spread ← clamp(gamma·sigma²·tau + (2/gamma)·ln(1+gamma/k), tick, 5·tick)`
8. Asimetría defensiva Cartea: bid_dist/ask_dist ponderados por `|obi|`
9. Viabilidad: si `(quote_ask − quote_bid) < 2·f_maker·micro_price` → ensanchar simétricamente
10. Re-quote solo si drift > `requote_threshold_ticks` → `cancel` + `order_factory.limit(..., post_only=True)`

**`on_order_filled(event)`:**
- Leer inventario de `self.portfolio` (no shadow state manual)
- Si `|q| > q_max`: reducir exceso con limit agresivo (market solo último recurso)
- Requote inmediato

**`on_stop`:**
- `cancel_all_orders(instrument_id)`
- Aplanar inventario residual con limit en micro-precio

### `backtest/run_backtest.py`

- `BacktestEngine` de NautilusTrader con `BookType.L2_MBP`
- Datos: descargar con `BinanceDataClient` o CSV histórico de Binance (frecuencia: deltas, mínimo 1 semana de datos)
- Comisiones configuradas explícitamente en el simulador (VIP 0)
- Fill model: L2 simplificado — **advertencia explícita en log**: sobreestima fills sin modelar posición en cola (§7)
- Output: P&L neto de comisiones, inventario pico, número de re-quotes, señales OFI/OBI promedio

### `live/run_testnet.py`

- `TradingNode` con `BinanceFuturesLiveDataClientConfig` apuntando a Testnet
- API keys de `BINANCE_TESTNET_API_KEY` / `BINANCE_TESTNET_API_SECRET` (env vars)
- Logs estructurados con nivel DEBUG para señales (micro-precio, OBI, OFI, sigma) y órdenes

---

## Flujo de datos

```
Binance WS (L2 deltas)
    │
    ▼
on_order_book_deltas
    ├── compute_micro_price → ref_price anchor
    ├── EWMAVolatility.update → sigma
    ├── circuit breaker check
    ├── A-S linealizado → inv_skew, inv_brake
    ├── compute_obi → signal_skew
    ├── ref_price = micro_price + signal_skew − inv_brake
    ├── half_spread = A-S + EWMA vol
    ├── asimetría defensiva Cartea
    ├── viabilidad de comisiones
    └── re-quote si drift > 0.5 tick → order_factory.limit(post_only=True)
```

---

## Parámetros iniciales

| Parámetro | Default MVP | Cómo calibrar |
|---|---|---|
| `gamma` | 0.1 | Barrer [0.01, 0.5]; mayor γ → spread más ancho |
| `k` | 1.5 | Estimar de curva fill-rate vs distancia al mid en Testnet |
| `tau` | 10.0 s | Barrer; representa horizonte de riesgo del MM |
| `q_max` | 0.01 BTC | Límite de riesgo; ajustar según capital |
| `signal_gain` | 0.30 | Barrer [0.1, 0.6]; equilibra alfa vs ruido |
| `f_maker` | 0.0002 | VIP 0; actualizar si cambia tier |
| `sigma_span` | 20 | EWMA span para volatilidad |
| `vol_breaker` | 0.005 | 0.5% / 1s; ajustar al par |
| `obi_depth_levels` | 5 | Niveles de libro para OBI |

> Todos los defaults son puntos de partida, **no valores de producción**. `gamma`, `k`, `A` son de la simulación estilizada de A-S (acciones, no cripto).

---

## Manejo de errores y riesgos

| Escenario | Respuesta |
|---|---|
| Flash crash (`|Δmid| > vol_breaker`) | Cancelar todo + limit en micro-precio |
| `\|q\| > q_max` | Limit agresivo hacia el mid para reducir inventario |
| Spread cotizado < 2·f_maker·P | Ensanchar simétricamente antes de postear |
| `on_stop` | Siempre cancelar órdenes + aplanar residual |

---

## Fuera de alcance del MVP

- Régimen adaptativo (Estrategia 2 del §5.2)
- Funding-awareness (se loguea el funding rate pero sin lógica de aplanado)
- Fill model L3 / modelado de posición en cola
- Co-localización / Estrategia 3 taker
- VIP tiers o rebate maker

---

## Fases de implementación

1. Setup entorno (pyproject.toml, NautilusTrader, Testnet credentials)
2. `signals/microstructure.py` con tests unitarios de OFI (rama de igualdad), OBI, micro-precio, EWMA
3. `config/btc_maker.py`
4. `strategy/as_maker.py` (solo pasos 1–4 primero, sin señal)
5. Añadir capa de señal + defensa (pasos 5–9)
6. `backtest/run_backtest.py` con datos históricos
7. `live/run_testnet.py` conectado a Testnet

---

*Spec autocontenido. Toda la lógica de trading se deriva del documento base `HFT_Crypto_Estrategia_Documento_Final.md`.*
