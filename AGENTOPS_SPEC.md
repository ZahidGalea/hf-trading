# AgentOps: Sistema de Auto-Optimización del ASMicroPriceMaker

**Estado:** REQUERIMIENTO — pendiente de implementación  
**Contexto:** Este documento recoge todo lo aprendido en las sesiones de diagnóstico del bot para que un agente externo pueda continuar la tarea sin necesitar contexto adicional.

---

## 1. Qué hace el bot actualmente (contexto completo)

### 1.1 Arquitectura
- **Estrategia:** `ASMicroPriceMaker` — market maker A-S (Avellaneda-Stoikov) maker-only sobre `BTCUSDT-PERP.BINANCE` en Binance Futures Testnet
- **Framework:** [NautilusTrader](https://nautilustrader.io) — Python/Rust
- **Entrypoint:** `poetry run python -m live.run_testnet` (o `./run_bot.sh`)
- **Config:** `config/btc_maker.py` → `ASMicroPriceMakerConfig` (frozen dataclass)
- **Logs:** SQLite en `logs/trading_YYYYMMDD_HHMMSS.db` (auto-generado al arrancar)

### 1.2 Parámetros clave (todos en `config/btc_maker.py`)

| Parámetro | Default actual | Qué controla |
|---|---|---|
| `gamma` | 0.1 | Aversión al riesgo — mayor γ → spread más ancho, menos fills |
| `k` | 1.5 | Decay de llegada de órdenes — mayor k → spread más ancho |
| `tau` | 10.0 | Horizonte temporal (s) — mayor tau → spread más ancho |
| `q_max` | 0.01 BTC | Límite de inventario — cuando se supera, solo cotiza el lado reductor |
| `signal_gain` | 0.30 | Peso de la señal OBI — mayor → más asimetría bid/ask |
| `requote_threshold_ticks` | 2.0 | Drift mínimo en ticks para disparar re-quote |
| `requote_min_interval_ms` | 500 | Cooldown mínimo entre re-quotes (ms) |
| `f_maker` | 0.0002 | Fee maker (usado en viabilidad económica step 9) |
| `vol_breaker` | 0.005 | Circuit breaker — retira del mercado si mid sube/baja >0.5% en 1s |
| `order_qty_btc` | 0.001 BTC | Tamaño de cada orden |
| `log_db_path` | None (auto) | Ruta del SQLite de logging |

### 1.3 Estado del bot post-correcciones (sesión `trading_20260614_121628.db`)

**Lo que funciona bien:**
- Throttle de 500ms funciona → 1.45 submit/s (vs 164/s antes del fix)
- 0% cancel_rejected → reloj sincronizado
- Spread capturado positivo: VWAP_sell (64466) > VWAP_buy (64452) = **+$13.28/BTC**
- Inventario oscila correctamente dentro de ±q_max

**Problema principal identificado — P&L negativo:**
- P&L bruto: +$0.36 en 12 min
- Comisiones maker: −$0.70
- **P&L neto: −$0.34** → el spread capturado ($13.28/BTC) es menor que el costo de comisión round-trip ($25.78/BTC = 2 × 0.0002 × $64460)
- **Causa:** Los parámetros A-S (gamma/k/tau) generan un half-spread demasiado estrecho. La fórmula A-S con defaults produce ~$12-15/BTC de spread, pero el fee round-trip es ~$25.78/BTC

**Rechazos actuales (menores):**
- `-5022 Post Only`: 2 rechazos / 900 submits (0.2%) → normal, aceptable
- `-4164 Notional mínimo`: 4 rechazos → YA CORREGIDO en código (commit previo)

### 1.4 Script de análisis disponible
```bash
poetry run python -m live.analyze_trading                     # último DB
poetry run python -m live.analyze_trading logs/foo.db         # DB específico
poetry run python -m live.analyze_trading --watch             # live, refresca cada 5s
```

Secciones del reporte: Resumen, Diagnóstico automático, Órdenes/s, Intervalos re-quote, Rechazos, Fills/P&L, Inventario, Actividad reciente.

---

## 2. Qué se quiere construir: AgentOps Loop

### 2.1 Descripción de alto nivel

Un sistema de agente autónomo que:
1. **Lanza** el bot de trading en background
2. **Espera** N minutos (default 5)
3. **Analiza** la BD SQLite más reciente con el script de análisis
4. **Decide** si ajustar parámetros (basado en reglas cuantitativas)
5. **Aplica** el ajuste modificando `config/btc_maker.py` y reiniciando el bot
6. **Repite** el ciclo indefinidamente hasta que el usuario detenga

### 2.2 Arquitectura propuesta

```
agentops/
  runner.py          # Orquestador principal — loop de lanzar/analizar/ajustar
  analyzer.py        # Lee SQLite y devuelve métricas estructuradas (dict/dataclass)
  adjuster.py        # Reglas de ajuste de parámetros y límites de seguridad
  history.py         # Registra cada ciclo en un log de experimentos (JSON Lines)
  safety.py          # Guardas de emergencia — detiene el bot si algo va mal
```

### 2.3 Ciclo de operación detallado

```
START
  │
  ▼
[1] Lanzar bot en subprocess
    - comando: poetry run python -m live.run_testnet
    - capturar PID, esperar a que aparezca el primer log "ASMicroPriceMaker iniciado"
    - si no arranca en 30s → error + retry
    │
    ▼
[2] Esperar 5 minutos (configurable: --interval=300)
    │
    ▼
[3] Consultar SQLite del bot (sin detenerlo — WAL permite lectura concurrente)
    - encontrar el DB más reciente en logs/
    - ejecutar analyzer.py → retorna métricas
    │
    ▼
[4] Evaluar métricas con las reglas de ajuste (adjuster.py)
    - ¿Hay alguna condición de emergencia? → safety.py → detener si es necesario
    - Calcular el ajuste de parámetros recomendado
    │
    ▼
[5] ¿Hay ajuste que aplicar?
    ├─ NO → continuar al paso [2] sin reiniciar
    └─ SÍ → [6] Aplicar ajuste
              │
              ▼
           Detener bot (SIGTERM, esperar hasta 10s)
           Modificar config/btc_maker.py con nuevos valores
           Registrar experimento en history.py
           Reiniciar bot → volver al paso [2]
```

---

## 3. Reglas de ajuste de parámetros (`adjuster.py`)

### 3.1 Métrica principal: `net_pnl_per_hour`

```python
net_pnl_per_hour = net_pnl / session_duration_hours
```

Objetivo: **net_pnl_per_hour > 0**. Si es negativo, el spread capturado no cubre comisiones.

### 3.2 Métricas secundarias

```python
metrics = {
    "net_pnl":              float,   # P&L neto USD en la sesión
    "net_pnl_per_hour":     float,   # normalizado por tiempo
    "spread_captured_usd":  float,   # VWAP_sell - VWAP_buy ($/BTC)
    "fee_per_roundtrip":    float,   # 2 × f_maker × avg_price ($/BTC)
    "fill_rate_per_min":    float,   # fills / minuto
    "submit_rate_per_s":    float,   # submits / segundo
    "reject_pct":           float,   # % de submits rechazados
    "cancel_rejected":      int,     # número de cancel rechazados (-1021)
    "inventory_max_abs":    float,   # máxima exposición observada en BTC
    "requote_rate_per_s":   float,   # re-quotes por segundo
    "fast_requote_pct":     float,   # % de re-quotes < 490ms (throttle bypass)
}
```

### 3.3 Reglas de ajuste (ordenadas por prioridad)

#### EMERGENCIA — detener inmediatamente (safety.py)

| Condición | Acción |
|---|---|
| `cancel_rejected > 5` | SIGTERM + error "reloj desincronizado, sincronizar con sudo hwclock -s" |
| `submit_rate_per_s > 20` | SIGTERM + error "order flood detectado, revisar throttle" |
| `inventory_max_abs > q_max * 1.5` | SIGTERM + error "inventario excesivo, posible fill sin cancelación" |
| `reject_pct > 15%` | SIGTERM + error "tasa de rechazo crítica" |

#### AJUSTE DE SPREAD (prioridad alta)

El half-spread del modelo A-S es: `hs ≈ 0.5 × (γ × σ² × τ + (2/γ) × log(1 + γ/k))`

Para aumentar el spread (capturar más que las comisiones):

```python
# Si spread_captured < fee_per_roundtrip × 1.1  (spread insuficiente)
if spread_captured_usd < fee_per_roundtrip * 1.1:
    gamma  *= 1.2    # +20%  (max: 0.5)
    tau    *= 1.1    # +10%  (max: 30.0)
    # Nota: ajustar uno a la vez por ciclo para entender el impacto

# Si spread_captured > fee_per_roundtrip × 3.0  (spread excesivo, perdiendo fills)
if spread_captured_usd > fee_per_roundtrip * 3.0 and fill_rate_per_min < 0.5:
    gamma  *= 0.85   # −15%  (min: 0.02)
    tau    *= 0.9    # −10%  (min: 2.0)
```

#### AJUSTE DE FILL RATE (prioridad media)

```python
# Si muy pocos fills (spread demasiado ancho o threshold demasiado amplio)
if fill_rate_per_min < 0.2:
    requote_threshold_ticks = max(1.0, requote_threshold_ticks - 0.5)

# Si demasiados fills en un solo lado (inventario se acumula)
if inventory_max_abs > q_max * 0.8:
    signal_gain = min(0.6, signal_gain * 1.1)  # más sesgo OBI → defensivo
```

#### AJUSTE DE ESTABILIDAD (prioridad baja)

```python
# Si fast_requote_pct > 10% (circuit breaker disparándose mucho)
if fast_requote_pct > 10:
    vol_breaker = min(0.01, vol_breaker * 1.2)  # umbral más permisivo

# Si fill_rate muy alta (>5/min) con P&L negativo (fills adversos)
if fill_rate_per_min > 5 and net_pnl < 0:
    signal_gain = max(0.1, signal_gain * 0.9)  # reducir sesgo
```

### 3.4 Límites absolutos de seguridad de parámetros

```python
PARAM_BOUNDS = {
    "gamma":                   (0.02, 0.5),
    "k":                       (0.5,  5.0),
    "tau":                     (2.0,  30.0),
    "q_max":                   (0.005, 0.01),   # no tocar en auto-ajuste
    "signal_gain":             (0.1,  0.6),
    "requote_threshold_ticks": (1.0,  5.0),
    "requote_min_interval_ms": (200,  2000),     # no bajar de 200ms nunca
    "vol_breaker":             (0.003, 0.02),
}
```

---

## 4. Historial de experimentos (`history.py`)

Cada ciclo se registra en `logs/agentops_history.jsonl`:

```json
{
  "cycle": 3,
  "ts": "2026-06-14T15:30:00Z",
  "session_db": "logs/trading_20260614_153000.db",
  "duration_min": 5.0,
  "metrics": { "net_pnl": -0.12, "spread_captured_usd": 18.5, ... },
  "params_before": { "gamma": 0.1, "tau": 10.0, ... },
  "params_after":  { "gamma": 0.12, "tau": 11.0, ... },
  "adjustment_reason": "spread_captured (18.50) < fee_per_roundtrip × 1.1 (28.36)",
  "bot_restarted": true
}
```

---

## 5. Cómo modificar `config/btc_maker.py` programáticamente

La config es un frozen dataclass. El adjuster debe:
1. Leer el archivo actual con `ast.parse` o simplemente regex/string replace
2. Escribir los nuevos valores manteniendo el formato del archivo
3. Hacer `git commit` con el mensaje `agentops: cycle N — ajuste gamma 0.1→0.12`

Alternativa más robusta: crear un archivo `config/overrides.json` y modificar `ASMicroPriceMakerConfig` para leer overrides en runtime (no requiere reiniciar el proceso, puede hacer hot-reload via signal).

**Recomendación:** usar el approach de `overrides.json` + hot-reload para evitar reiniciar el bot en cada ajuste (menos downtime, más continuidad de datos).

---

## 6. CLI del AgentOps

```bash
# Arrancar el loop completo
poetry run python -m agentops.runner

# Opciones
poetry run python -m agentops.runner \
  --interval 300 \        # segundos entre análisis (default: 300 = 5min)
  --dry-run \             # analizar y sugerir ajustes pero NO aplicar
  --max-cycles 20 \       # detener después de N ciclos
  --no-restart            # analizar pero nunca reiniciar el bot

# Solo analizar la sesión más reciente (sin loop)
poetry run python -m agentops.runner --analyze-only

# Ver historial de experimentos
poetry run python -m agentops.runner --history
```

---

## 7. Implementación sugerida — orden de tareas

1. `agentops/analyzer.py` — leer SQLite y retornar dict de métricas (usar las queries de `live/analyze_trading.py` como referencia, las secciones ya están implementadas)
2. `agentops/safety.py` — condiciones de emergencia (SIGTERM si se disparan)
3. `agentops/adjuster.py` — reglas de ajuste + límites de parámetros
4. `agentops/history.py` — escritura de JSONL
5. `agentops/runner.py` — orquestador principal (subprocess + loop + señales)
6. Tests en `tests/agentops/` — mock del subprocess, mock del DB, verificar reglas

---

## 8. Restricciones de implementación

- **Maker-only siempre:** `post_only=True` en todas las órdenes — nunca tocar esto.
- **`requote_min_interval_ms` nunca < 200ms** — por debajo de eso hay riesgo de flood.
- **`q_max` no debe tocarse en auto-ajuste** — riesgo de exposición excesiva.
- **Máximo 1 parámetro ajustado por ciclo** — para poder atribuir causalidad.
- **Si el bot no arranca en 30s → abortar, no reintentar indefinidamente.**
- **Leer el DB con `PRAGMA wal_checkpoint(PASSIVE)` antes de analizar** — asegura que el WAL está flusheado.
- **No hacer `git push` automáticamente** — solo commit local.
- **Los tests existentes (34) deben seguir pasando** después de cada cambio de config.

---

## 9. Archivos relevantes del proyecto

```
config/btc_maker.py              # ASMicroPriceMakerConfig — parámetros a ajustar
strategy/as_maker.py             # ASMicroPriceMaker — lógica del bot (no tocar en AgentOps)
live/run_testnet.py              # Entrypoint del bot
live/order_logger.py             # Logger asíncrono SQLite (hilo de fondo)
live/analyze_trading.py          # Script de análisis — usar como referencia para analyzer.py
logs/trading_*.db                # DBs generados por cada sesión
logs/agentops_history.jsonl      # Historial de ciclos AgentOps (a crear)
CLAUDE.md                        # Contexto y comandos del proyecto
HFT_Crypto_Estrategia_Documento_Final.md  # Spec matemática del modelo A-S
```

---

## 10. Resultado esperado de una sesión de AgentOps sana

```
Cycle 1 (t=0min):   gamma=0.10 tau=10  → net_pnl=-0.34 spread=$13.28  → AJUSTE gamma→0.12
Cycle 2 (t=5min):   gamma=0.12 tau=10  → net_pnl=-0.18 spread=$16.50  → AJUSTE gamma→0.14
Cycle 3 (t=10min):  gamma=0.14 tau=10  → net_pnl=+0.05 spread=$20.10  → sin ajuste ✓
Cycle 4 (t=15min):  gamma=0.14 tau=10  → net_pnl=+0.08 spread=$21.30  → sin ajuste ✓
```

La convergencia típica hacia P&L positivo debería ocurrir en 3-6 ciclos si el modelo A-S es correcto para las condiciones de mercado del momento.
