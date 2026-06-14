"""Paper trading del ASMicroPriceMaker en Binance Futures Testnet.

Credenciales: copiar .env.example a .env y completar con claves de Testnet.
Obtener claves en: https://testnet.binancefuture.com

Uso:
    export BINANCE_TESTNET_API_KEY=...
    export BINANCE_TESTNET_API_SECRET=...
    .venv/bin/python -m live.run_testnet

Nota WSL2: si aparecen errores -1021 ("Timestamp ahead of server time"), sincronizar el
reloj antes de arrancar:  sudo hwclock -s
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.request

from dotenv import load_dotenv

load_dotenv()

from nautilus_trader.adapters.binance.config import (
    BinanceAccountType,
    BinanceDataClientConfig,
    BinanceEnvironment,
    BinanceExecClientConfig,
)
from nautilus_trader.adapters.binance.factories import (
    BinanceLiveDataClientFactory,
    BinanceLiveExecClientFactory,
)
from nautilus_trader.config import (
    ImportableStrategyConfig,
    InstrumentProviderConfig,
    LiveExecEngineConfig,
    LoggingConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.config import LiveRiskEngineConfig
from nautilus_trader.live.node import TradingNode

_CLOCK_MAX_OFFSET_MS = 500  # abortar si el reloj local difiere más de ±500ms vs testnet
_TESTNET_TIME_URL = "https://testnet.binancefuture.com/fapi/v1/time"


def _check_clock_sync() -> None:
    """Comprueba el desfase de reloj frente a Binance Testnet.

    Aborta con mensaje claro si el desfase supera ±500ms.
    Los errores -1021 de Binance ocurren cuando el cliente va >1s adelantado;
    con WSL2, el reloj puede desviarse varios segundos tras suspensión del sistema.

    Para sincronizar en WSL2:  sudo hwclock -s
    """
    try:
        with urllib.request.urlopen(_TESTNET_TIME_URL, timeout=5) as r:
            server_time_ms = json.loads(r.read())["serverTime"]
        offset_ms = int(time.time() * 1000) - server_time_ms
        if abs(offset_ms) > _CLOCK_MAX_OFFSET_MS:
            print(
                f"\n[ERROR] Reloj local {offset_ms:+d}ms respecto a Binance Testnet "
                f"(límite ±{_CLOCK_MAX_OFFSET_MS}ms).\n"
                f"  Esto produce errores -1021 que impiden cancelar órdenes y provoca\n"
                f"  acumulación de posiciones. Sincronizar el reloj y reiniciar:\n\n"
                f"      sudo hwclock -s\n",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"[clock] OK — desfase vs testnet: {offset_ms:+d}ms")
    except SystemExit:
        raise
    except Exception as exc:
        # No abortar si la comprobación falla (red no disponible, etc.)
        print(f"[clock] advertencia — no se pudo verificar reloj: {exc}", file=sys.stderr)


def _load_strategy_config() -> dict:
    """Base strategy config merged with any active overrides from config/overrides.json."""
    from config.btc_maker import read_overrides
    overrides = read_overrides()
    base = {
        "instrument_id": "BTCUSDT-PERP.BINANCE",
        "order_qty_btc": 0.001,
    }
    # base keys always win — overrides may set gamma, tau, etc. but not instrument_id
    return {**overrides, **base}


def build_node() -> TradingNode:
    api_key = os.environ["BINANCE_TESTNET_API_KEY"]
    api_secret = os.environ["BINANCE_TESTNET_API_SECRET"]

    config_node = TradingNodeConfig(
        trader_id="LIVE-TESTNET-001",
        logging=LoggingConfig(log_level="INFO"),
        risk_engine=LiveRiskEngineConfig(
            # Tope duro de motor independiente de la lógica de la estrategia.
            # La estrategia ya aplica un cooldown de 500ms (~4 órdenes/s en condiciones normales);
            # este backstop captura cualquier burst inesperado.
            max_order_submit_rate="20/00:00:01",
        ),
        exec_engine=LiveExecEngineConfig(reconciliation=True),
        strategies=[
            ImportableStrategyConfig(
                strategy_path="strategy.as_maker:ASMicroPriceMaker",
                config_path="config.btc_maker:ASMicroPriceMakerConfig",
                config=_load_strategy_config(),
            )
        ],
        data_clients={
            "BINANCE": BinanceDataClientConfig(
                api_key=api_key,
                api_secret=api_secret,
                account_type=BinanceAccountType.USDT_FUTURES,
                environment=BinanceEnvironment.TESTNET,
                instrument_provider=InstrumentProviderConfig(load_all=True),
            ),
        },
        exec_clients={
            "BINANCE": BinanceExecClientConfig(
                api_key=api_key,
                api_secret=api_secret,
                account_type=BinanceAccountType.USDT_FUTURES,
                environment=BinanceEnvironment.TESTNET,
                # Instrument provider en exec client para que la reconciliación encuentre
                # BTCUSDT-PERP.BINANCE en cache (evita "Instrument not in cache" al arrancar).
                instrument_provider=InstrumentProviderConfig(load_all=True),
                # recv_window amplia para tolerar desfases de reloj residuales.
                # No sustituye la sincronización del reloj: usar 'sudo hwclock -s' si hay -1021.
                recv_window_ms=10_000,
            ),
        },
    )

    node = TradingNode(config=config_node)
    node.add_data_client_factory("BINANCE", BinanceLiveDataClientFactory)
    node.add_exec_client_factory("BINANCE", BinanceLiveExecClientFactory)

    node.build()
    return node


async def main() -> None:
    _check_clock_sync()
    node = build_node()
    try:
        await node.run_async()
    finally:
        await node.stop_async()
        node.dispose()


if __name__ == "__main__":
    asyncio.run(main())
