# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`hft-crypto-mvp` — ASMicroPriceMaker: a maker-only market-making strategy for BTC/USDT-M Futures on Binance, built on [NautilusTrader](https://nautilustrader.io). Strategy logic is in Python; NautilusTrader's engine core is in Rust.

**Dependency on strategy document:** `HFT_Crypto_Estrategia_Documento_Final.md` is the canonical spec. Section references (e.g. §2.1, §5.1) in code comments and docstrings point to this document. Read it before modifying signal or quoting logic.

## Commands

```bash
# Install dependencies
poetry install

# Run all tests
poetry run pytest

# Run a specific test module
poetry run pytest tests/signals/
poetry run pytest tests/integration/

# Run a single test
poetry run pytest tests/integration/test_backtest_run.py::test_backtest_completes_without_error -v

# Backtest with historical L2 data (Parquet)
.venv/bin/python -m backtest.run_backtest <ruta_datos.parquet>

# Paper trading on Binance Futures Testnet
export BINANCE_TESTNET_API_KEY=...
export BINANCE_TESTNET_API_SECRET=...
.venv/bin/python -m live.run_testnet
```

Testnet credentials go in `.env` (copy from `.env.example`). Get keys at https://testnet.binancefuture.com.

## Architecture

```
config/btc_maker.py         ASMicroPriceMakerConfig — all strategy parameters with defaults
signals/microstructure.py   Pure-Python signal primitives (no NautilusTrader imports)
strategy/as_maker.py        ASMicroPriceMaker(Strategy) — 10-step quoting algorithm
backtest/run_backtest.py    BacktestEngine runner for L2 historical Parquet data
live/run_testnet.py         TradingNode runner for Binance Futures Testnet
tests/fixtures/book_data.py Synthetic L2 OrderBookDeltas generator for tests
tests/signals/              Unit tests for signal primitives
tests/integration/          End-to-end BacktestEngine tests using synthetic data
```

### Signal layer (`signals/microstructure.py`)

Three stateless functions plus one stateful class:

- `compute_micro_price(bid_p, bid_q, ask_p, ask_q)` — weighted-mid anchoring quotes (§2.3)
- `compute_obi(bids, asks, depth_levels)` — Order Book Imbalance normalized to [−1, 1] (§2.2)
- `compute_ofi(...)` — Order Flow Imbalance per-event contribution (§2.1, corrected strict inequalities)
- `EWMAVolatility(span)` — EWMA of squared returns; avoids bid-ask bounce contaminating σ (§6.2)

### Strategy (`strategy/as_maker.py`)

`ASMicroPriceMaker` implements `on_order_book_deltas` in 10 steps (matching §5.1 pseudocode):

1. Micro-price anchor
2. EWMA volatility update
3. Circuit breaker (cancel all orders if mid moves >0.5% in 1s)
4. A-S stationary inventory skew (`q·γ·σ²·τ`)
5. Non-linear OBI signal (`sign(obi)·obi²`)
6. Reference price (`micro_price + signal_skew - inv_brake`)
7. A-S half-spread formula with tick clamp `[1 tick, 5 ticks]`
8. Cartea asymmetric defense (widen toxic side by `0.4·|obi|`)
9. Fee viability floor (`spread ≥ 2·f_maker·price`)
10. Re-quote only if drift > 0.5 tick (minimize message rate)

All orders are `post_only=True`. Inventory reduction uses limit orders (not market) to avoid taker fees.

### Config (`config/btc_maker.py`)

`ASMicroPriceMakerConfig` is a frozen NautilusTrader `StrategyConfig`. All defaults are starting points for calibration — not production values. Parameters to sweep in walk-forward backtest: `gamma`, `k`, `tau`, `signal_gain`.

### Backtest limitations

The L2 backtest fill model overestimates fills because it doesn't model queue position. Use it for signal-behavior validation, not PnL estimation (§7 of the strategy doc).

## Key invariants

- Strategy is **maker-only**: never submit a non-`post_only` order.
- OFI inequalities must be **strict** (`>`, `<`), not `>=`/`<=` — the equality branch handles the tie case (the most frequent event). Regression risk on any OFI formula edit.
- `f_maker` in config must match the actual Binance tier in use — the fee viability check in step 9 depends on it being correct.
