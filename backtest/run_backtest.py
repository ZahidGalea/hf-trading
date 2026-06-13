"""Backtest básico del ASMicroPriceMaker con datos L2 históricos de Binance.

Advertencia (§7 del documento): fill model simplificado en L2 sobreestima fills
porque no modela posición en cola. Usar como referencia de comportamiento de señales,
no como estimador de rentabilidad real.

Uso:
    .venv/bin/python -m backtest.run_backtest <ruta_datos.parquet>

Preparar datos históricos:
    Descargar desde https://data.binance.vision/?prefix=data/futures/um/daily/bookDepth/BTCUSDT/
    y convertir a formato NautilusTrader con OrderBookDeltaDataWrangler.
    Documentación: https://nautilustrader.io/docs/latest/concepts/data
"""
from __future__ import annotations

import sys
from pathlib import Path

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.enums import AccountType, BookType, OmsType
from nautilus_trader.model.identifiers import TraderId, Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from config.btc_maker import ASMicroPriceMakerConfig
from strategy.as_maker import ASMicroPriceMaker


def run_backtest(data_path: str | Path | None = None) -> BacktestEngine:
    """Corre el backtest sobre datos L2 históricos.

    Si data_path es None, corre sin datos (útil como smoke test de configuración).
    """
    engine = BacktestEngine(
        config=BacktestEngineConfig(trader_id=TraderId("BACKTESTER-001"))
    )

    venue = Venue("BINANCE")
    engine.add_venue(
        venue=venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=USDT,
        starting_balances=[Money(10_000, USDT)],
        book_type=BookType.L2_MBP,
    )

    instrument = TestInstrumentProvider.btcusdt_binance()
    engine.add_instrument(instrument)

    strategy_config = ASMicroPriceMakerConfig(
        instrument_id=instrument.id.value,
        order_qty_btc=0.001,
    )
    strategy = ASMicroPriceMaker(config=strategy_config)
    engine.add_strategy(strategy=strategy)

    if data_path is not None:
        data_path = Path(data_path)
        if not data_path.exists():
            print(f"[BACKTEST] ERROR: archivo no encontrado: {data_path}", file=sys.stderr)
            sys.exit(1)
        print(f"[BACKTEST] Cargando datos desde: {data_path}")
        print("[BACKTEST] ⚠️  Fill model L2 simplificado — sobreestima fills (§7)")
        # Cargar datos según el formato disponible:
        # Para datos Parquet de NautilusTrader:
        #   from nautilus_trader.persistence.catalog import ParquetDataCatalog
        #   catalog = ParquetDataCatalog(str(data_path.parent))
        #   deltas = catalog.order_book_deltas(instrument_ids=[instrument.id.value])
        #   engine.add_data(deltas)
        print("[BACKTEST] Implementar carga de datos según formato: ver comentario en código.")
    else:
        print("[BACKTEST] Corriendo sin datos (smoke test de configuración).")

    engine.run()

    print("\n=== RESULTADOS DEL BACKTEST ===")
    try:
        result = engine.get_result()
        print(result)
    except Exception:
        print("[BACKTEST] Sin resultados — no hubo datos o fills.")

    print("\n⚠️  Recordatorio: resultados sobreestiman fills sin L3 ni modelo de cola.")
    return engine


if __name__ == "__main__":
    data_path = sys.argv[1] if len(sys.argv) > 1 else None
    if data_path is None:
        print("Uso: .venv/bin/python -m backtest.run_backtest <ruta_datos.parquet>")
        print("Corriendo smoke test (sin datos)...")
    run_backtest(data_path)
