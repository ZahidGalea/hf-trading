from nautilus_trader.config import StrategyConfig


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
    requote_threshold_ticks: float = 0.5  # Drift mínimo en ticks para disparar re-quote.

    # Tamaño de orden
    order_qty_btc: float = 0.001  # BTC por orden. Mínimo posible para MVP.
