# Implementation Plan - Worker M3 (Decade Backtester)

## Goal
Implement `backtester.py` providing a decade-deep vectorized and event-driven backtester in pandas and numpy adhering to 4GB RAM envelope (<200MB RSS), chunked streaming (100k + 1k halo), synthetic decade M1 gold generator, parameter sweep with feature caching, and 500+ run Monte Carlo engine.

## Step 1: Core Data Models & Parameters
- `HyperPredatorParams`: Dataclass holding strategy parameters (wick_pct, m5_lookback, l2_imbalance, tick_velocity_mult, etc.).
- `ActiveBasket`: Dataclass holding in-flight trade state across chunk boundaries.
- `BacktestResult`: Dataclass holding trades, metrics, equity curve, summary, and serialization.
- `MonteCarloResult`: Dataclass holding 500+ simulation percentiles, ruin prob, killswitch trips.

## Step 2: Realistic Synthetic Decade M1 Generator (`generate_synthetic_gold_m1`)
- Fast, deterministic NumPy-based generation.
- Generates Open, High, Low, Close, Volume, Tick Velocity, L2 Imbalance, Tape Delta, Macro Bias, Volatility Regime.
- Realistic wick distribution with rejection wicks >= 65% near S/R pivots.
- Realistic tick velocity spikes (>= 1.5x) and L2 imbalances (> 3.0).
- Support exporting to DataFrame, NumPy array, CSV, or Parquet.

## Step 3: Chunked Streaming Iterator (`M1ChunkIterator`)
- Supports Parquet, CSV, CSV.GZ, memory-mapped NumPy arrays, and in-memory DataFrames.
- Chunk size: 100,000 bars, Halo buffer: 1,000 bars.
- Yields `(chunk_df_or_array, halo_offset, is_last)`.
- Guarantees RSS stays under 200 MB.

## Step 4: Vectorized Signal & Execution Engine (`VectorizedSignalEngine`, `VectorizedBacktester`)
- Vectorized rolling M5 S/R pivots with zero lookahead (`.shift(1).rolling()`).
- Vectorized M1 rejection wick ratio and body direction.
- Vectorized tick velocity surge detection.
- Fast event-driven execution pass over valid bar range $[halo\_offset : ]$.
- Detached Stop-Loss ($1.00 beyond invalidation wick).
- Target Exit at immediate opposing M5 S/R.
- Reversal Exit on opposing >= 65% M1 rejection wick.
- Hard Equity Shield liquidation at -$10.00 floating PnL.
- Top-5 L2 book imbalance exit (> 3.0 * volatility_regime).
- Trade tape delta stall exit (> 80% opposing ticks in last 20 trades for profitable basket).
- ActiveBasket carried across chunk boundaries.

## Step 5: Parameter Sweep Engine
- Optimize across:
  - `wick_pct`: 0.60 to 0.75
  - `m5_lookback`: 20 to 100
  - `l2_imbalance`: 2.0 to 5.0
  - `tick_velocity`: 1.2x to 2.0x
- Pre-calculates base features (M5 pivots for distinct lookbacks, wick ratios, velocities) to eliminate redundant memory allocations during sweep.
- Returns ranked summary DataFrame.

## Step 6: Monte Carlo Simulation Engine (500+ Runs)
- 5-slice execution jitter with 20ms stagger.
- Adverse execution slippage (0.5 to 2.5 pips).
- Hyperliquid taker fees (3.5 bps).
- 5% daily drawdown killswitch.
- Computes 5th, 25th, 50th, 75th, 95th percentiles of Sharpe, MaxDD, Win Rate, Profit Factor, Equity.

## Step 7: Verification & Testing
- Unit & integration test script to verify:
  1. Data ingestion (CSV, Parquet, memmap, synthetic).
  2. Chunk streaming continuity with halo buffer.
  3. Memory footprint under 200 MB RSS for 150k+ bars.
  4. Signal math (exact 65% rejection, body direction, S/R pivots, velocity surge).
  5. Exit mechanics (Stop, Target, Reversal Wick, Equity Shield -$10, L2 exit, tape delta exit).
  6. Parameter sweep interface.
  7. 500-run Monte Carlo execution and percentile outputs.
  8. Metrics calculation accuracy.
- CLI smoke test.
