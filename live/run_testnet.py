"""Paper trading del ASMicroPriceMaker en Binance Futures Testnet.

Credenciales: copiar .env.example a .env y completar con claves de Testnet.
Obtener claves en: https://testnet.binancefuture.com

Uso:
    export BINANCE_TESTNET_API_KEY=...
    export BINANCE_TESTNET_API_SECRET=...
    .venv/bin/python -m live.run_testnet
"""
from __future__ import annotations

import asyncio
import os

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
            "BINANCE": BinanceDataClientConfig(
                api_key=api_key,
                api_secret=api_secret,
                account_type=BinanceAccountType.USDT_FUTURE,
                environment=BinanceEnvironment.TESTNET,
                instrument_provider=InstrumentProviderConfig(load_all=True),
            ),
        },
        exec_clients={
            "BINANCE": BinanceExecClientConfig(
                api_key=api_key,
                api_secret=api_secret,
                account_type=BinanceAccountType.USDT_FUTURE,
                environment=BinanceEnvironment.TESTNET,
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
