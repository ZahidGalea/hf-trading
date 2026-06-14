from __future__ import annotations
import json
from pathlib import Path

from nautilus_trader.config import StrategyConfig

OVERRIDES_PATH = str(Path(__file__).parent / "overrides.json")


class ASMicroPriceMakerConfig(StrategyConfig, frozen=True):
    """Parámetros del ASMicroPriceMaker para BTC/USDT-M Futures.

    Todos los defaults son puntos de partida (A-S estilizado), NO valores de producción.
    Barrer gamma, k, tau, signal_gain en backtest walk-forward antes de capital real.
    """

    instrument_id: str = "BTCUSDT-PERP.BINANCE"

    # A-S estacionario linealizado (§2.4, §5.1)
    gamma: float = 0.1        # Aversión al riesgo; mayor γ → spread más ancho, menos inventario.
    k: float = 1.5            # Decay de llegada λ(δ)=A·e^(−kδ). Estimar de fill-rate en Testnet.
    tau: float = 10.0         # Horizonte efectivo (segundos). Sustitución de (T−t); barrer.
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
    requote_threshold_ticks: float = 2.0  # Drift mínimo en ticks para disparar re-quote.
    requote_min_interval_ms: int = 500    # Cooldown mínimo entre re-quotes (ms). Evita order-flood.

    # Tamaño de orden
    order_qty_btc: float = 0.001  # BTC por orden. Mínimo posible para MVP.

    # Logging a SQLite (None = auto-generar logs/trading_YYYYMMDD_HHMMSS.db)
    log_db_path: str | None = None


def get_default_params() -> dict:
    """Return the adjustable A-S parameters with their class defaults."""
    cfg = ASMicroPriceMakerConfig()
    return {
        "gamma":                   cfg.gamma,
        "k":                       cfg.k,
        "tau":                     cfg.tau,
        "q_max":                   cfg.q_max,
        "signal_gain":             cfg.signal_gain,
        "requote_threshold_ticks": cfg.requote_threshold_ticks,
        "requote_min_interval_ms": cfg.requote_min_interval_ms,
        "vol_breaker":             cfg.vol_breaker,
        "f_maker":                 cfg.f_maker,
    }


def read_overrides() -> dict:
    """Read active parameter overrides from OVERRIDES_PATH. Returns {} if missing."""
    p = Path(OVERRIDES_PATH)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def write_overrides(params: dict) -> None:
    """Persist parameter overrides to OVERRIDES_PATH."""
    Path(OVERRIDES_PATH).write_text(
        json.dumps(params, indent=2, ensure_ascii=False), encoding="utf-8"
    )
