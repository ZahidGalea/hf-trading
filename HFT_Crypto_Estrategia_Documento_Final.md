# Estrategia HFT de Baja Latencia para Cripto (Binance) — Documento Final de Implementación

**Versión:** 1.0 (consolidada y corregida)
**Propósito:** Base única y autocontenida para implementar la estrategia fuera de Claude.
**Stack objetivo:** NautilusTrader (core en Rust, capa de estrategia en Python), Binance USDT-M Futures.
**Alcance:** Market making maker-only de baja latencia basado en microestructura, con variantes adaptativa y taker condicional.

> Este documento integra el análisis de cinco papers académicos, un análisis económico de viabilidad, tres estrategias con pseudocódigo, la arquitectura de implementación en NautilusTrader, guía de calibración, limitaciones de backtesting y un apéndice que separa lo respaldado por los papers de las extrapolaciones propias. Está pensado para que un desarrollador (o un agente de IA) pueda implementar sin contexto adicional.

---

## 0. Cómo usar este documento

- **§1–2** son el *qué* y el *por qué*: leer primero para entender el fundamento.
- **§3** es el filtro económico que condiciona todo el diseño: **no saltárselo**.
- **§4** es la arquitectura concreta en NautilusTrader (mapeo a handlers y órdenes reales).
- **§5** son las estrategias con pseudocódigo de la lógica de trading.
- **§6–9** son calibración, backtesting, riesgos y plan de fases.
- **§10** lista las decisiones que el implementador debe tomar antes de codificar.
- **Apéndice A** es crítico para la integridad intelectual: qué viene de los papers y qué es extrapolación.

**Advertencia transversal:** las comisiones, specs de contrato y la API de NautilusTrader cambian. Verificar antes de operar dinero real. Las cifras de este documento se verificaron en junio de 2026.

---

## 1. Resumen ejecutivo

Se construye un **market maker maker-only** sobre Binance USDT-M Futures que:

1. Ancla sus cotizaciones en el **micro-precio** (mid ponderado por imbalance), mejor predictor de corto plazo que el mid.
2. Controla el inventario con el **precio de reserva estacionario de Avellaneda-Stoikov** (horizonte infinito, apropiado para mercado 24/7).
3. Usa **OBI/OFI** simultáneamente como (a) alfa direccional que captura el movimiento de corto plazo y (b) protección contra **selección adversa**.
4. Se retira del mercado ante **flash crashes** (circuit breaker).
5. Opera exclusivamente con órdenes **post-only** porque las comisiones hacen inviable el scalping taker de pocos ticks.

El edge **no** sale de capturar el spread (que no cubre comisiones en BTC/USDT a tier base), sino de la **predicción de microestructura**, que debe superar el umbral de comisiones. Por eso la elección de tier de comisiones (BNB/VIP/rebate) y de par de trading son decisiones de primer orden.

La razón de fondo para preferir maker-only sobre scalping taker es doble: **economía de comisiones** (§3) y la dinámica **winner-take-all de latencia** de Byrd et al. (§2.5) — si no se gana la carrera de latencia, la postura pasiva es estructuralmente menos frágil.

---

## 2. Fundamentos teóricos (los cinco papers)

### 2.1 OFI — Order Flow Imbalance (Cont, Kukanov & Stoikov, 2010)

El OFI resume en un escalar el efecto neto de órdenes límite, de mercado y cancelaciones sobre las colas del mejor bid/ask. Contribución del evento *n* (desigualdades **estrictas** + rama de igualdad explícita):

```
Lado BID:
    P_bid_n > P_bid_{n-1}   →   +Q_bid_n                 (precio sube: tamaño nuevo)
    P_bid_n < P_bid_{n-1}   →   −Q_bid_{n-1}             (precio baja: tamaño retirado)
    P_bid_n = P_bid_{n-1}   →   +(Q_bid_n − Q_bid_{n-1}) (mismo precio: delta de tamaño)

Lado ASK (signos invertidos):
    P_ask_n < P_ask_{n-1}   →   −Q_ask_n                 (oferta crece → bajista)
    P_ask_n > P_ask_{n-1}   →   +Q_ask_{n-1}             (oferta se retira → alcista)
    P_ask_n = P_ask_{n-1}   →   −(Q_ask_n − Q_ask_{n-1}) (mismo precio: delta de tamaño)

e_n   = contribución_bid + contribución_ask
OFI_k = Σ e_n   sobre los eventos del intervalo [t_{k-1}, t_k]
```

Relación precio-OFI empírica (lineal, R²≈65% en Cont et al.): `ΔP_k = β·OFI_k + ε_k`, con `β ≈ c/(profundidad)^λ` (inversa a la profundidad del libro).

> **Bug corregido:** la versión inicial usaba `P_bid_n >= P_bid_{n-1}`, que capturaba el empate y dejaba muerta la rama de delta de tamaño — el evento **más frecuente** del libro. Resultado: OFI inflado e inservible.
> **Aviso de escala:** Cont et al. validan β a **10 segundos**. Aplicarlo sub-segundo es extrapolación; recalibrar y validar.

### 2.2 OBI — Order Book Imbalance (Byrd et al., 2020; Yagi et al., 2023)

```
Byrd (ratio ∈ [0,1]):   OBI = ΣVol_bid(L) / ΣVol_total(L)        (0.5 = equilibrio)
Yagi (norm. ∈ [−1,1]):  OBI = (buy_depth − sell_depth)/(buy_depth + sell_depth)
                        donde buy/sell_depth = volumen en Dp ticks desde el best bid/ask
```

Yagi: el MM que incorpora OBI (POMM) es **alto-riesgo/alto-retorno**; la respuesta al OBI debe ser **no-lineal** (corrección pequeña con señal débil, grande con señal fuerte).

### 2.3 Micro-precio / weighted-mid (Stoikov, citado en Yagi et al.)

Yagi cita que el **micro-precio** de Stoikov —ajuste del mid que incorpora imbalance y spread— predice mejor los movimientos de corto plazo del mid que el mid o el mid ponderado por volumen. Aproximación de primer orden:

```
micro_price = (P_ask · Q_bid + P_bid · Q_ask) / (Q_bid + Q_ask)
```

Con `Q_bid ≫ Q_ask`, el precio se "tira" hacia el ask (la cola compradora empujará el precio arriba).

> **Flag:** esto es la aproximación weighted-mid, **no** el micro-precio completo de Stoikov (2018), que usa un ajuste tipo cadena de Markov. Stoikov (2018) no está en la lista de lectura; se usa como proxy de primer orden de un concepto sí citado en Yagi.

### 2.4 Avellaneda-Stoikov (2008): market making con control de inventario

**Horizonte finito** (resultado clásico):
```
Precio de reserva:  r(s,q,t) = s − q·γ·σ²·(T−t)
Spread óptimo:       δ_a + δ_b = γ·σ²·(T−t) + (2/γ)·ln(1 + γ/k)
Tasa de llegada:     λ(δ) = A · e^(−k·δ)
```
Variables: `s` mid, `q` inventario, `γ` aversión al riesgo, `σ²` varianza, `(T−t)` tiempo a cierre, `k` decay de llegada de market orders, `A` frecuencia base.

**Problema en cripto:** mercado 24/7, no hay `T`. Cuando `(T−t)→0` el skew de inventario desaparece — lo contrario de lo deseable.

**Horizonte infinito** (versión estacionaria, §2.3 del paper) — la correcta para 24/7:
```
r̄_a(s,q) = s + (1/γ)·ln(1 + ((1−2q)·γ²σ²)/(2ω − γ²q²σ²))
r̄_b(s,q) = s + (1/γ)·ln(1 + ((−1−2q)·γ²σ²)/(2ω − γ²q²σ²))
con  ω > ½·γ²σ²q²  ;  elección natural  ω = ½·γ²σ²·(q_max+1)²
```
`ω` actúa como **cota superior de inventario** y no depende de un tiempo terminal, por lo que el skew persiste de forma estacionaria. Para inventario pequeño se linealiza a `r ≈ s − q·γσ²·τ` con horizonte efectivo constante `τ`.

> **Flag:** en el pseudocódigo se usa la **linealización con `τ` constante** (parámetro de tuning) por implementabilidad. La forma estacionaria de arriba sí es de A-S; la sustitución por `τ` constante es práctica habitual en bots A-S de cripto, no del paper.

### 2.5 Latencia de rango (Byrd et al., 2020)

No es la latencia absoluta sino el **rango ordinal** entre quienes usan la misma señal lo que determina el beneficio. Es **winner-take-all**: el agente más cercano casi nunca pierde; el segundo casi nunca gana; el décimo pierde mucho.

**Implicación de diseño:** si no se garantiza rango-1 (caso de casi cualquiera no co-locado frente a HFT institucional), una postura *señal-taking* taker es la más expuesta a llegar tarde sobre señal ya arbitrada. La postura *maker pasiva* es **menos dependiente del rango**: no se corre a capturar la señal, se provee.

### 2.6 Selección adversa (Cartea, Donnelly & Jaimungal, 2018)

El imbalance del libro **predice el signo de la próxima orden de mercado**. Consecuencia para un MM: con OBI muy sesgado, la orden pasiva en el lado débil es ejecutada por flujo informado justo antes de que el precio se mueva en contra (**selección adversa**). Respuesta: además de hacer skew del precio de reserva, **ensanchar o retirar la cotización del lado tóxico**. En este diseño, ese skew defensivo coincide en dirección con el skew que captura el movimiento predicho, reforzándose.

---

## 3. Análisis económico: el filtro que decide todo

### 3.1 Comisiones reales de Binance (verificadas, jun-2026)

| Mercado | Maker (VIP 0) | Taker (VIP 0) | Notas |
|---|---|---|---|
| USDT-M Futures | 0.0200 % | 0.0500 % | −10 % extra pagando con BNB |
| Spot | 0.1000 % | 0.1000 % | peor para HFT |
| Futures VIP altos | → 0 % | → ~0.017 % | requieren volúmenes muy altos |

### 3.2 Break-even (BTC ≈ $100.000, tick ≈ $0.10, posición 1 BTC — verificar specs)

**Taker (lo que invalidaba el scalping de pocos ticks):**
```
Round-trip taker = 0.05% × 100.000 × 2 = $100 = 1.000 ticks
Para ser rentable, el precio debe moverse ≥ ~1.000 ticks (≈0.1%).
Scalpear 1–3 ticks con market orders ⇒ pérdida sistemática.
```

**Maker (la base correcta, pero exigente):**
```
Round-trip maker = 0.02% × 100.000 × 2 = $40 = 400 ticks
Captura de spread sola (S ≈ 1–2 ticks ≈ $0.10–0.20) ⇒ neto ≈ −$39.85.
```

**Conclusión:** la captura de spread por sí sola **no** cubre comisiones en un par de spread estrecho a tier base. El edge debe venir de la **predicción**:
```
Condición de viabilidad por round-trip maker:
    S + Δ_predicho  >  2 · f_maker · P
    ⇒  Δ_predicho  >  ~400 ticks-equivalente (a VIP 0)
```

### 3.3 Consecuencias de diseño

1. **Maker-only** (`post_only`) es necesario.
2. **El edge sale de la señal**, no del spread → el ancla de precio es el micro-precio (predictivo), no el mid.
3. **Reducir comisiones es parte de la estrategia:** BNB, escalar VIP, o venues/pares con **rebate** maker (que invierten la economía: spread + rebate pasan a ser ingreso, y el juego se vuelve de volumen).
4. **El instrumento importa:** pares de spread más ancho relativo a la comisión bajan el umbral. BTC/USDT a VIP 0 es de los más difíciles.

---

## 4. Arquitectura del sistema (NautilusTrader)

NautilusTrader es event-driven con core en Rust y estrategias en Python que reaccionan a eventos del *message bus*. Lo que en §5 llamo `on_book_update` se mapea a los handlers reales de Nautilus.

### 4.1 Componentes y flujo

```
Binance WebSocket (L2/L3 deltas, trades, mark price/funding)
        │
        ▼
DataEngine  ──►  OrderBook (mantenido por instrumento)  ──►  handlers de la Strategy
        │                                                         │
        ▼                                                         ▼
   MessageBus  ◄───────────────  ExecutionEngine  ◄──────  submit/modify/cancel_order
        │
        ▼
RiskEngine ──► Binance ExecutionClient ──► Binance (órdenes post-only)
```

### 4.2 Datos: suscripciones y book type

Tipos de libro en Nautilus: **L3_MBO** (market-by-order, cada orden por ID — necesario para modelar posición en cola), **L2_MBP** (market-by-price, agregado por nivel), **L1_MBP** (top-of-book).

```python
# En on_start de la Strategy:
self.subscribe_order_book_deltas(self.instrument_id)        # L2/L3 incremental (<100 ms)
# o, si solo se necesitan 10 niveles agregados:
self.subscribe_order_book_depth(self.instrument_id)          # OrderBookDepth10
# trades para OFI/flujo y mark price+funding para perpetuos:
self.subscribe_trade_ticks(self.instrument_id)
# Funding (Binance Futures): BinanceFuturesMarkPriceUpdate incluye funding rate
```

- Para intervalos < 100 ms, suscribirse a **deltas**, no a snapshots por intervalo.
- Para modelar **posición en cola** (clave en backtest realista, §7) se requiere **L3_MBO**; con L2 no es posible.
- El handler entrega `OrderBookDeltas` / `OrderBookDepth10`; el `OrderBook` se consulta con `best_bid_price()`, `best_ask_price()`, `best_bid_size()`, `best_ask_size()`, `bids()`, `asks()`.
- **Funding de perpetuos:** Binance paga funding (típicamente cada 8 h). El adapter de Binance Futures expone `BinanceFuturesMarkPriceUpdate` con la tasa de funding. Un MM que mantiene inventario a través del funding tiene un coste/ingreso que **debe** entrar en el P&L (ningún paper lo cubre).

### 4.3 Gestión de órdenes maker-only

Una orden marcada `post_only` **solo** provee liquidez y nunca cruza el spread (si fuese a ejecutarse como taker, se rechaza/cancela). Solo las órdenes **límite** soportan `post_only`. Esto implementa el requisito maker-only de §3.

```python
order = self.order_factory.limit(
    instrument_id=self.instrument_id,
    order_side=OrderSide.BUY,                 # o SELL
    quantity=self.instrument.make_qty(qty),
    price=self.instrument.make_price(quote_bid),
    time_in_force=TimeInForce.GTC,            # por defecto GTC
    post_only=True,                           # ← MAKER-ONLY garantizado
)
self.submit_order(order)
# Reposicionar sin cancelar+crear cuando el venue lo permita:
self.modify_order(order, price=self.instrument.make_price(nuevo_precio))
# Retirada total (incluye órdenes inflight en Binance):
self.cancel_all_orders(self.instrument_id)
```

> Binance Futures permite además *BBO price matching* vía el parámetro `price_match` (la orden se une al libro a un precio óptimo determinado por el exchange). Es una alternativa a fijar el precio exacto; evaluar si conviene para el re-quote.

### 4.4 Estado: inventario y posición

Inventario y P&L se consultan vía `self.portfolio` y `self.cache` (posición neta, no-realizado, etc.) en lugar de llevar contadores manuales propios (que pueden desincronizarse con los fills reales). Mantener no obstante un *shadow state* del OFI acumulado y de los buffers de señal en la estrategia.

### 4.5 Mapeo pseudocódigo → handlers Nautilus

| Pseudocódigo (§5) | Handler/método NautilusTrader |
|---|---|
| `on_book_update(event)` | `on_order_book_deltas(deltas)` o `on_order_book_depth(depth)` |
| lectura de best bid/ask y tamaños | `book.best_bid_price()` / `best_ask_price()` / `best_bid_size()` / `best_ask_size()` |
| `on_fill(fill)` | `on_order_filled(event)` / `on_event(event)` |
| señal de funding | suscripción a `BinanceFuturesMarkPriceUpdate` |
| `ENVIAR limit_..._POSTONLY` | `order_factory.limit(..., post_only=True)` + `submit_order` |
| `cancelar_todo()` | `cancel_all_orders(instrument_id)` |
| `liquidar/reducir con market` (último recurso) | `order_factory.market(...)` / `close_position(...)` con `reduce_only=True` |
| ciclo de vida | `on_start` (suscripciones), `on_stop` (cancelar todo, aplanar) |

**Esqueleto de clase (rellenar con la lógica de §5):**
```python
class ASMicroPriceMaker(Strategy):
    def __init__(self, config: ASMicroPriceMakerConfig):
        super().__init__(config)
        # parámetros desde config (§6); buffers de señal; instrument_id

    def on_start(self):
        self.instrument = self.cache.instrument(self.instrument_id)
        self.subscribe_order_book_deltas(self.instrument_id)
        self.subscribe_trade_ticks(self.instrument_id)
        # suscripción a mark price/funding

    def on_order_book_deltas(self, deltas):
        book = self.cache.order_book(self.instrument_id)
        # 1) micro-precio, 2) σ (EWMA), 3) circuit breaker,
        # 4) skew inventario (A-S estacionario), 5) skew señal (OBI),
        # 6) ref_price, 7) half-spread, 8) asimetría defensiva,
        # 9) chequeo de viabilidad de comisiones, 10) re-quote si drift
        ...

    def on_trade_tick(self, tick):
        # actualizar OFI/flujo si se usa como confirmador/régimen
        ...

    def on_order_filled(self, event):
        # leer inventario de self.portfolio; reducir exceso con limit agresivo;
        # re-quote inmediato
        ...

    def on_stop(self):
        self.cancel_all_orders(self.instrument_id)
        # aplanar inventario residual de forma controlada
```

> Verificar nombres exactos de config y firmas en la doc vigente: https://nautilustrader.io/docs/latest/ (concepts/order_book, concepts/orders, integrations/binance).

---

## 5. Estrategias

### 5.1 Estrategia 1 (NÚCLEO): `ASMicroPriceMaker` — A-S horizonte infinito, maker-only, micro-precio + OBI defensivo

**Objetivo:** market making pasivo que captura el movimiento de corto plazo predicho por el micro-precio, con control de inventario A-S estacionario y protección contra selección adversa. Posición persistente; re-quote 10–500 ms.

```
PARÁMETROS { gamma, k, tau, q_max, signal_gain, f_maker, sigma_win, vol_breaker }   # ver §6
ESTADO     { mid_hist, sigma, (inventario vía portfolio), active_bid, active_ask }

FUNCIÓN on_book_update(event):

  # 1) ANCLA = micro-precio (no mid)
  Qb,Qa,Pb,Pa ← top of book
  micro_price ← (Pa·Qb + Pb·Qa)/(Qb+Qa)
  spread_mkt  ← Pa − Pb

  # 2) VOLATILIDAD: EWMA filtrando bid-ask bounce (NO STDEV crudo del mid)
  mid_hist.push((Pa+Pb)/2);  sigma ← EWMA_vol(mid_hist)

  # 3) CIRCUIT BREAKER (Yagi): no operar en flash crash
  SI cambio_pct(mid_hist, 1s) > vol_breaker:
      cancelar_todo(); liquidar_con_limit(micro_price); RETORNAR

  # 4) SKEW DE INVENTARIO (A-S estacionario, linealizado con tau)
  q ← inventario_actual()                      # de self.portfolio
  inv_skew ← q · gamma · sigma² · tau

  # 5) SKEW DE SEÑAL (OBI), no-lineal (Yagi) — captura mov. predicho + defensa (Cartea)
  obi ← (buy_depth − sell_depth)/(buy_depth + sell_depth)
  signal_skew ← signal_gain · sign(obi) · obi² · spread_mkt          # [EXTRAPOLACIÓN]

  # 6) PRECIO DE REFERENCIA = ancla + señal − freno de inventario
  inv_brake ← inv_skew · (1 + (q/q_max)²)      # endurece cerca del límite (Yagi)
  ref_price ← micro_price + signal_skew − inv_brake

  # 7) HALF-SPREAD (componente de mercado A-S + componente de volatilidad)
  half_spread ← 0.5·( gamma·sigma²·tau + (2/gamma)·ln(1 + gamma/k) )
  half_spread ← CLAMP(half_spread, tick, 5·tick)

  # 8) ASIMETRÍA DEFENSIVA (Cartea): retirar el lado tóxico
  SI obi ≥ 0:  bid_dist ← half_spread·(1 − 0.4·obi);   ask_dist ← half_spread·(1 + 0.4·obi)
  SINO:        bid_dist ← half_spread·(1 + 0.4·|obi|); ask_dist ← half_spread·(1 − 0.4·|obi|)

  quote_bid ← ROUND_DOWN(ref_price − bid_dist, tick)
  quote_ask ← ROUND_UP  (ref_price + ask_dist, tick)

  # 9) VIABILIDAD ECONÓMICA (§3): no postear spreads que no cubran el round-trip
  SI (quote_ask − quote_bid) < 2·f_maker·micro_price:
      ensanchar_simétricamente_hasta(2·f_maker·micro_price)

  # 10) RE-QUOTE SOLO SI DRIFT > 0.5·tick (minimizar mensajes → latencia, Byrd)
  SI drift(quote_bid,active_bid)>0.5·tick O drift(quote_ask,active_ask)>0.5·tick:
      cancelar(active_bid, active_ask)
      ENVIAR limit_buy_POSTONLY(qty_bid, quote_bid)
      ENVIAR limit_sell_POSTONLY(qty_ask, quote_ask)
      actualizar(active_bid, active_ask)

FUNCIÓN on_fill(fill):
  # inventario de self.portfolio
  SI |q| > q_max:
      reducir_exceso_con_limit_agresivo()      # market solo último recurso (coste taker)
  requote_inmediato()
```

**Ventajas (respaldadas):** inventario A-S estacionario apto 24/7; ancla en micro-precio (mejor predictor corto plazo, Yagi/Stoikov); defensa contra flujo informado (Cartea); circuit breaker (Yagi); maker-only viable (§3); menos dependiente del rango de latencia (Byrd).
**Riesgos (respaldados):** A-S asume random walk sin drift y Poisson simétrica — irreal en tendencias/volumen clustered; la señal puede no superar el umbral de comisiones a VIP 0 (§3.2); posición en cola no modelada (§7); `σ,k,A` requieren calibración en Binance.

### 5.2 Estrategia 2: `RegimeAdaptiveMaker` — maker adaptativo por régimen

**Objetivo:** misma base maker-only, modulando la agresividad del skew según el régimen detectado con OFI/OBI. **Nunca pasa a taker.**

```
ENUM Regime { RANGING, TRENDING_UP, TRENDING_DOWN, VOLATILE }

FUNCIÓN on_book_update(event):
  micro_price, spread_mkt, sigma ← señales_base(event)
  ofi ← OFI_incremental(event)        # fórmula CORREGIDA §2.1
  obi ← OBI_normalizado(event)
  ofi_hist.push(ofi); obi_hist.push(obi)
  regime ← detectar_regimen(ofi_hist, obi_hist, sigma)

  SELECCIONAR regime:
    VOLATILE:        cancelar_todo(); liquidar_con_limit(micro_price); RETORNAR
    RANGING:         signal_gain ← 0.15;  asimetria ← 0.2     # capturar spread, skew suave
    TRENDING_UP/DOWN:signal_gain ← 0.50;  asimetria ← 0.6     # cabalgar mov., retirar lado contrario

  quote_bid,quote_ask ← cotizar_maker(micro_price, sigma, obi, inventario, signal_gain, asimetria)
  chequear_viabilidad_fees(quote_bid, quote_ask)
  requote_si_drift(quote_bid, quote_ask)

FUNCIÓN detectar_regimen(ofi_hist, obi_hist, sigma):
  SI sigma > vol_breaker: RETORNAR VOLATILE
  ofi_z   ← media(ofi_hist[-10:]) / (std(ofi_hist[-20:]) + ε)
  obi_avg ← media(obi_hist[-10:])
  SI ofi_z >  1.5 Y obi_avg >  0.15: RETORNAR TRENDING_UP      # umbrales ad hoc — CALIBRAR
  SI ofi_z < -1.5 Y obi_avg < -0.15: RETORNAR TRENDING_DOWN
  RETORNAR RANGING
```

> **Flag:** la conmutación de régimen introduce riesgo de *whipsaw* y de latencia (al detectar la tendencia, parte del movimiento puede haber pasado — §2.5). Umbrales `1.5`/`0.15` y pesos son extrapolación; calibrar y validar fuera de muestra.

**Ventajas:** doma la alta varianza del POMM de Yagi sin abandonar maker-only.
**Riesgos:** detección lenta/ruidosa; estado en transiciones (órdenes huérfanas, inventario atrapado).

### 5.3 Estrategia 3 (CONDICIONAL): `OFIMomentumTaker` — solo con rango-1 de latencia

**Objetivo:** scalping direccional taker. **Solo viable si se cumplen simultáneamente** dos condiciones; para la mayoría no se cumplen:

```
CONDICIÓN A (Byrd): ganar la carrera de latencia (co-locación real, core Rust/C++,
                    WebSocket L2, región AWS de Binance).
CONDICIÓN B (§3.2): la señal predice un movimiento > ~1.000 ticks (≈0.1%) para cubrir
                    el round-trip taker ⇒ SOLO entradas en señales EXTREMAS.
```

Si A y B no se cumplen, pierde de forma sistemática (error de la versión original). Se incluye por completitud / caso co-locado.

```
FUNCIÓN on_book_update(event):
  ofi ← OFI_incremental(event)               # fórmula CORREGIDA §2.1
  obi ← OBI_normalizado(event)
  beta ← c/(avg_depth^lambda)                # CALIBRADO en cripto (no β de 10s directo)
  predicted_dp ← beta · ofi_acumulado_ventana
  fee_hurdle ← 2·f_taker·mid + colchón       # umbral = comisiones taker, no arbitrario
  señal ← sign(ofi)·|ofi|·(1+|obi|)          # OBI confirma (Cartea)

  SI inventario == 0:
    SI predicted_dp >  +fee_hurdle Y obi >  0.5 Y señal >  umbral_extremo:
        ENVIAR market_buy(qty); abrir_long(best_ask)
    SINO SI predicted_dp < −fee_hurdle Y obi < −0.5 Y señal < −umbral_extremo:
        ENVIAR market_sell(qty); abrir_short(best_bid)
  SINO:
    gestionar_salida(target = entry ± 1.5·fee_hurdle,
                     stop   = entry ∓ 0.8·fee_hurdle,
                     reversal_si = OBI_cruza_cero)        # trailing en señal (Byrd)
    salida_por_timeout(max_hold = 2s)
```

**Ventajas (si A y B):** captura señal perecedera con R²≈65% del OFI.
**Riesgos:** rango-2 ya pierde (Byrd); umbral de comisiones taker brutal; β extrapolado a sub-segundo. Para no co-locados, **usar la Estrategia 1**.

---

## 6. Parámetros y calibración

### 6.1 Tabla de parámetros

| Parámetro | Símbolo | Default inicial | Fuente | Cómo calibrar |
|---|---|---|---|---|
| Aversión al riesgo | `gamma` (γ) | 0.1 | A-S (juguete) | Barrer en backtest; mayor γ → spread más ancho, menos inventario |
| Decay de llegada | `k` | 1.5 | A-S (juguete) | Estimar de la curva fill-rate vs distancia al mid en datos Binance |
| Frecuencia base | `A` | 140 | A-S (juguete) | Estimar de la intensidad de fills observada |
| Horizonte efectivo | `tau` (τ) | constante a tunear | ADAPTACIÓN | Representa el horizonte de riesgo del MM; barrer |
| Cota de inventario | `q_max` | 0.05 BTC | diseño | Límite de riesgo; define `ω` en A-S estacionario |
| Ganancia de señal | `signal_gain` | 0.30 (núcleo) | EXTRAPOLACIÓN | Barrer; equilibra alfa vs ruido |
| Ventana de vol | `sigma_win` | EWMA, no 200 fijo | adaptación | Usar EWMA; validar contra realized vol |
| Circuit breaker | `vol_breaker` | 0.5 % / 1 s | Yagi (cualit.) | Ajustar al perfil de volatilidad del par |
| Comisión maker | `f_maker` | 0.0002 (VIP0) | Binance | Usar el tier real (BNB/VIP/rebate) |
| Umbral régimen | z, OBI | 1.5 / 0.15 | EXTRAPOLACIÓN | Aprender/validar fuera de muestra |

> **Importante:** `γ=0.1, k=1.5, A=140` provienen de la **simulación estilizada de acciones** de A-S, **no** de cripto. Son puntos de partida, no valores de producción. `k` y `A` en particular deben estimarse de la curva empírica fill-rate-vs-distancia en Binance (medible en paper trading).

### 6.2 Estimador de volatilidad

No usar STDEV crudo del mid sobre una ventana fija (mezcla volatilidad real con bid-ask bounce). Usar **EWMA de retornos** o **volatilidad realizada** filtrando el bounce. `σ` entra tanto en el precio de reserva como en el spread, así que su calidad afecta todo.

---

## 7. Backtesting y validación

El backtest ingenuo **sobreestima** el rendimiento de forma dramática. Antes de creer cualquier P&L:

1. **Simulación de fills (lo más difícil).** No asumir que una orden maker se llena porque el precio "tocó" su nivel: depende de la **posición en cola**. No asumir que una market order se llena al precio mostrado (slippage + latencia). Requiere datos **L3_MBO** para modelar la cola; con L2 no es posible un fill model fiel.
2. **Comisiones dentro del simulador** (§3), con el tier real asumido.
3. **Funding de perpetuos** (cada ~8 h) en el P&L (vía `BinanceFuturesMarkPriceUpdate`).
4. **Latencia señal→llegada** modelada (Byrd): el libro cuando la orden llega ≠ el libro que disparó la señal.
5. **Impacto de mercado** de las órdenes propias.
6. **Calibración walk-forward** de `γ,k,A,β,τ` y umbrales de régimen; nunca calibrar y testear en la misma muestra.
7. **Paper trading en Testnet** para medir latencia y fill-rate reales y así estimar `k,A` antes de capital real.

NautilusTrader usa el **mismo código** de estrategia en backtest y en vivo, lo que reduce el riesgo de discrepancia backtest/producción — pero el realismo del backtest sigue dependiendo del fill model y de los datos (L3 vs L2).

---

## 8. Riesgos y gestión

- **Selección adversa:** mitigada con el skew defensivo (§2.6, §5.1 paso 8), pero nunca eliminada.
- **Inventario en tendencias:** A-S asume sin drift; en una tendencia fuerte el inventario se acumula en contra. El freno no-lineal cerca de `q_max` (paso 6) y el `q_max` duro lo acotan; considerar stop de inventario y límite de pérdida diaria.
- **Flash crash:** circuit breaker (Yagi) retira del mercado; liquidar residual con limit, no market.
- **Funding adverso:** mantener inventario a través del funding puede costar; considerar aplanar antes de la hora de funding si la tasa es desfavorable.
- **Riesgo operacional/latencia:** rango-2 pierde en estrategias taker (Byrd); por eso el núcleo es maker. Minimizar mensajes (re-quote solo si drift) reduce la latencia efectiva y el riesgo de rate-limit.
- **Límites de riesgo globales:** definir pérdida máxima diaria, drawdown máximo y kill-switch que cancele todo y aplane.

---

## 9. Plan de implementación por fases

1. **Pipeline de datos + señales.** Suscripción L2/L3 en Nautilus; OFI **corregido** (§2.1); micro-precio (§2.3); EWMA de volatilidad.
2. **MM A-S estacionario maker-only.** Precio de reserva (linealizado con `τ`), half-spread, `post_only`, re-quote por drift. Sin señal aún.
3. **Capa de señal + defensa.** Skew de OBI (alfa) + asimetría defensiva (Cartea) + chequeo de viabilidad de comisiones.
4. **Robustez.** Circuit breaker (Yagi) + funding-awareness (mark price) + límites de riesgo/kill-switch.
5. **Régimen (opcional).** Estrategia 2 sobre el mismo motor.
6. **Backtesting realista.** Fill model con L3, comisiones, funding, latencia; calibración walk-forward (§7).
7. **Paper trading en Testnet.** Medir latencia y fill-rate; estimar `k,A`; validar economía con el tier real.
8. **Producción gradual.** Tamaños pequeños; monitorizar selección adversa, inventario y P&L neto de comisiones+funding.

---

## 10. Decisiones abiertas (tomar antes de codificar)

1. **Tier de comisiones.** ¿VIP 0 + BNB? ¿Se puede alcanzar un tier con **rebate** maker? Esto determina si el edge requerido (§3.2) es alcanzable y puede invertir la economía.
2. **Par de trading.** BTC/USDT es el más líquido pero de spread estrecho (umbral de comisiones alto relativo al spread). Un par de spread más ancho puede ser más viable; evaluar liquidez vs umbral.
3. **Spot vs Futures.** Futures tiene comisiones más bajas y funding; Spot 0.1% (peor). El documento asume USDT-M Futures.
4. **Co-locación.** ¿Se puede garantizar rango-1 de latencia (servidores en la región de Binance)? Si no, descartar la Estrategia 3 y quedarse en maker.
5. **Profundidad de datos.** ¿Se dispone de **L3_MBO** para un fill model realista? Sin L3, el backtest de un MM es poco fiable.
6. **Horizonte `τ` y `q_max`.** Definir el horizonte de riesgo efectivo y la cota de inventario según tolerancia al riesgo y capital.
7. **Niveles `L`/`Dp` para OBI.** Cuántos niveles/ticks entran en el cálculo del imbalance (afecta sensibilidad de la señal).

---

## Apéndice A — Respaldo en papers vs extrapolaciones

**Respaldado directamente por los papers:**
- OFI y relación lineal ΔP = β·OFI, R²≈65% — Cont, Kukanov & Stoikov (2010).
- Precio de reserva y spread, horizonte finito **e infinito (estacionario)** — Avellaneda & Stoikov (2008), §3 y §2.3.
- Tasa de llegada λ(δ)=A·e^(−kδ) — A-S (2008).
- Definiciones de OBI (ratio y normalizada) — Byrd et al. (2020); Yagi et al. (2023).
- Micro-precio como mejor predictor de corto plazo que mid/mid-ponderado-por-volumen — Stoikov, **citado en** Yagi et al. (2023).
- Latencia de rango ordinal dominante (winner-take-all) — Byrd et al. (2020).
- El imbalance predice el signo de la próxima orden de mercado — Cartea, Donnelly & Jaimungal (2018).
- MM con OBI alto-riesgo/alto-retorno; el MM se retira en flash crashes — Yagi et al. (2023).

**Extrapolaciones / parametrizaciones propias (marcadas en el texto):**
- `signal_skew = gain·sign(obi)·obi²·spread`: interpretación cuantitativa de la no-linealidad **cualitativa** de Yagi (que usa exponente impar para la posición). No es la fórmula exacta del paper.
- **weighted-mid** como proxy del micro-precio completo de Stoikov (2018), que no está en la lista de lectura.
- Sustitución de `(T−t)` por **horizonte constante `τ`** en la linealización A-S (la forma estacionaria sí es del paper; el atajo con `τ` es práctica habitual, no de A-S).
- Umbrales de régimen (`z>1.5`, `OBI>0.15`) y pesos `signal_gain`/`asimetria`: ad hoc; requieren calibración.
- Lógica de **retirada defensiva** del lado tóxico: inspirada en la implicación de Cartea sobre selección adversa; el mecanismo de pull-quote concreto es construcción propia.
- Uso de **funding de perpetuos** y **comisiones reales de Binance**: conocimiento práctico de mercado, no de los papers.
- Aplicación de β a escala **sub-segundo**: fuera del rango de 10 s validado por Cont et al.

---

## Apéndice B — Referencias

1. Byrd, Palaparthi, Hybinette, Balch (2020). *The Importance of Low Latency to Order Book Imbalance Trading Strategies.* arXiv:2006.08682
2. Cont, Kukanov, Stoikov (2010). *The Price Impact of Order Book Events.* arXiv:1011.6402
3. Avellaneda, Stoikov (2008). *High-frequency trading in a limit order book.* (Cornell)
4. Cartea, Donnelly, Jaimungal (2018). *Enhancing Trading Strategies with Order Book Signals.* SSRN 2668277
5. Yagi et al. (2023). *Impact of High-Frequency Trading with an Order Book Imbalance Strategy on Agent-Based Stock Markets.* Complexity 2023:3996948
6. Stoikov (2018). *The micro-price* (citado en Yagi; no en la lista de lectura) — proxy weighted-mid usado aquí.
7. NautilusTrader docs: https://nautilustrader.io/docs/latest/ (concepts/order_book, concepts/orders, integrations/binance)
8. Binance fee schedule (verificar antes de operar).

*Fin del documento.*
