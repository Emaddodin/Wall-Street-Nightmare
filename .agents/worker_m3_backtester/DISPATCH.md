## 2026-09-19T09:08:45Z
You are Worker M3 (Decade Backtester Worker).
Your working directory is: /Users/mac/Desktop/TBT-Engine/.agents/worker_m3_backtester
Exclusive file ownership: /Users/mac/Desktop/TBT-Engine/backtester.py

MANDATORY INPUT:
Read /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md (specifically requirement R7 under ## 2026-09-19T08:56:25Z).
Read /Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_3/survey_report.md (decade-deep streaming chunking, memory envelope, parameter sweep, Monte Carlo).
Read /Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_2/survey_report.md (R2, R4, R5 math).
Read /Users/mac/Desktop/TBT-Engine/.agents/orchestrator_3/PROJECT.md (architecture and interface contracts).

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

OBJECTIVE:
Build `backtester.py`, a high-performance decade-deep vectorized and event-driven backtester in `pandas` and `numpy`:
1. R7 4GB RAM Streaming Architecture:
   - Capable of streaming and processing massive historical datasets (e.g., M1 OHLCV from 2010 to present, ~3.7M to 5.2M bars) strictly within 4GB RAM envelope.
   - Use chunked streaming iterators (100,000 bars per chunk) with a 1,000-bar overlap halo buffer (to calculate rolling M5 S/R pivots and indicators without boundary distortion) and stateful basket persistence across chunk boundaries.
   - Memory-mapped arrays (`np.memmap`) or compact float32/int64 structured numpy arrays keeping RSS under 200 MB.
   - Support loading historical data from Parquet, CSV, CSV.GZ, AND provide a built-in deterministic synthetic decade M1 generator (`generate_synthetic_gold_m1()`) with realistic price action, wick distributions, S/R pivots, tick velocity, and L2 imbalance for immediate offline testing and verification.
2. Signal & Execution Matching:
   - Implements identical M1 rejection wick math (>= 65% range, body in bias direction), rolling M5 S/R pivots, tick velocity surge (>= 1.5x baseline).
   - Invalidation anchor and detached stop-loss placed exactly $1.00 beyond invalidation wick extreme.
   - Target exit at opposing M5 S/R zone.
   - Reversal exit on opposing >= 65% M1 rejection wick.
   - Hard Equity Shield liquidation at -$10.00.
   - Top-5 L2 book imbalance exit (> 3.0 * volatility_regime) and trade tape delta stall exit (> 80% opposing ticks in last 20 trades for profitable basket).
3. Parameter Sweep Interface:
   - Optimize across:
     - Wick rejection percentage threshold (60% to 75%).
     - M5 Support/Resistance lookback window (20 to 100 bars).
     - L2 Imbalance threshold (2.0 to 5.0).
     - Tick Velocity multiplier (1.2x to 2.0x).
   - Pre-calculate base features to avoid redundant memory copies during sweep.
4. Monte Carlo Simulation Engine (500+ runs):
   - Incorporates execution jitter (5-slice 20ms stagger), adverse execution slippage (0.5 to 2.5 pips), Hyperliquid taker fees (3.5 bps), and 5% daily drawdown killswitch.
5. Metrics Output:
   - Output Sharpe Ratio, Max Drawdown ($ and %), Win Rate, Profit Factor, Total Trades, Expectancy, and Average Trade Duration.
   - Clean programmatic API (`VectorizedBacktester`, `BacktestResult`, `MonteCarloResult`) and CLI entrypoint.

SCOPE BOUNDARIES:
- Exclusive write ownership of `backtester.py`. DO NOT modify `hyper_predator_bot.py`.
- Confine metadata to `/Users/mac/Desktop/TBT-Engine/.agents/worker_m3_backtester`.

OUTPUT REQUIREMENTS:
Run syntax check and verification on `backtester.py`. Run a backtest run and Monte Carlo simulation with synthetic data to verify execution and memory efficiency.
Write detailed report to `/Users/mac/Desktop/TBT-Engine/.agents/worker_m3_backtester/report.md` and handoff to `/Users/mac/Desktop/TBT-Engine/.agents/worker_m3_backtester/handoff.md`.
Send completion message to orchestrator_3.
