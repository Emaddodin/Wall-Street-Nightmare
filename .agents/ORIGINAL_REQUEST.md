# Original User Request

## 2026-09-16T19:51:52Z

Build, refactor, and evolve the "Wall-Street-Nightmare" autonomous cryptocurrency scalping engine to Architecture B (Volume Rules + LightGBM/CatBoost Execution Filter + Asynchronous Local LLM Critic), verify all components with rigorous unit tests, and deploy the entire engine and local llama.cpp LLM critic service onto the 2-core / 4GB RAM VPS at `82.115.21.155`.

Working directory: `/Users/mac/Desktop/TBT-Engine` (local repository) & `/root/ict_sniper` (VPS `82.115.21.155`)
Integrity mode: development

## Requirements

### R1. Continuous Timeframe-Agnostic Data Layer & Feature Engine
Construct event-driven bar structures (Range Bars, Volume Bars, Tick Charts) and real-time microstructural order-flow feature estimators:
- Multi-level Order Flow Imbalance (OFI) across 5 levels with rolling z-score normalization
- Cumulative Volume Delta (CVD) tracking and divergence calculations
- Open Interest (OI) contraction monitoring (>2.5% drop in <300 ticks)
- Hawkes trade arrival intensity with exponential decay kernel and excitation ratio vs median
- Mark vs Mid and Perp vs Spot basis spreads in basis points
- Level 2 Book Depth Imbalances (top 1% bid/ask volume clusters)

### R2. Deterministic Mechanical Alpha Setups
Implement 5 deterministic candidate alpha setups with strict mathematical entry conditions and hard invalidation rules:
- OFI VWAP Reversion (Entry at $\pm 2.5\sigma$ VWAP band + OFI sign reversal $|z_{OFI}| > 0.8\sigma$; Hard SL: 0.35%)
- Liquidation Cascade Absorption (OI drop > 2.5% in <300 ticks with Mark drop > 1.5% and taker sell > 85%; Invalidation SL at cascade extreme low)
- Hawkes Volatility Breakout (Local 100-bar high/low breach with trade arrival intensity > 3x median; Trailing SL ratcheted behind Hull Moving Average HMA(9))
- CVD Divergence Sweep (Price lower low vs CVD higher low over 10 bars with relative volume > 1.5x)
- L2 Depth Imbalance Scalp (Top 1% LOB bids > 5x asks within 0.1% of cluster; hard SL below cluster)

### R3. Sub-10ms ML Filter Engine & 256-Dim RAG Temporal Memory
Provide sub-10ms probability filtering supporting CatBoost and LightGBM models with 100% backward compatibility for legacy `.npz` artifacts. Implement a 256-dimensional vector embedding temporal memory system (Qdrant / in-memory cosine fallback) that queries historical setups to evaluate expected Maximum Adverse Excursion (MAE) and historical win rates, vetoing trades where expected MAE breaches the proposed hard SL.

### R4. Asynchronous Local LLM Strategic Critic
Build an isolated `llama.cpp` client and service running a sub-3B quantized model (Q4_K_M) on a constrained 2-core / 4GB RAM VPS with strict parameters (`-t 2`, `-ngl 0`, `--mlock`, `--ctx-size 2048`). Run asynchronously outside the critical sub-50ms execution path to critique regime shifts, evaluate rolling 20-trade JSON summaries, and dynamically scale risk multipliers.

### R5. Capital Allocation, Risk Engine & CDP Target Pinning
Strictly enforce a 10x–15x leverage ceiling (liquidation distance $\ge 9.5\%$), 25% margin sizing per trade (75% cash buffer), and deterministic hard stop-losses on every order. Enforce explicit tab target ID pinning in `signals/tv_cdp.py` to eliminate target swapping between chart windows. Implement Combinatorial Purged Cross-Validation (CPCV) with purging and embargoing.

### R6. Production VPS Deployment (`82.115.21.155`)
Deploy the complete codebase to `/root/ict_sniper` on the VPS via SSH, install `llama-server` and download a sub-3B Q4_K_M model, configure and start `stratton-llm-critic.service` under systemd, wire the HFT engine to the critic, and verify that all services run continuously with system RAM usage safely below 2.5 GB.

## Acceptance Criteria

### Unit Tests & Code Quality
- [ ] All new Architecture B modules pass unit tests under `tests/test_architecture_b.py`.
- [ ] Legacy filter model tests (`tests/test_filter.py`) pass with zero regressions.
- [ ] ML filter inference latency is objectively benchmarked at under 10ms.
- [ ] Target ID pinning in `signals/tv_cdp.py` preserves chart window attachment across tab reordering.
- [ ] CPCV module demonstrates proper purging of overlapping event labels and post-test embargoing.

### Production VPS Deployment
- [ ] `llama-server` runs as a systemd service on VPS (`82.115.21.155`) with `-t 2 -ngl 0 --mlock` on port 8080 and responds to `/health` and completion queries.
- [ ] VPS memory consumption with `llama-server` active does not exceed 2.5 GB resident memory.
- [ ] HFT execution engine runs on the VPS, connects to the Hyperliquid L2 WebSocket stream, and logs telemetry without crashes.

## 2026-09-17T19:08:47Z

Deploy, backtest, and harden the 5-Minute XAUUSD Relapse Scalper natively on Hyperliquid DEX (replacing MT5). The system must integrate a local llama.cpp GGUF server for sub-second dynamic intuition-based exits, asynchronous Order Slicing, and a rigid risk framework to scale a $65 micro-account to $10,000 on a 4GB Linux VPS using systemd services.

Working directory: /Users/mac/Desktop/TBT-Engine
Integrity mode: development

## Requirements

### R1. Hyperliquid DEX Asynchronous Execution Bridge
- Connect the Relapse Scalper execution router to Hyperliquid DEX (testnet/mock mode by default, supporting live key configuration).
- Implement asynchronous Order Slicing (`fire_layered_orders`) dispatching 3 sliced tickets simultaneously with 50ms jitter via `asyncio.gather`.
- Enforce core risk invariants: leverage 1:1000, max initial margin utilization <= 20% of account equity, stop-loss strictly between 10.0 and 15.0 pips ($1.00 - $1.50) placed 1.0-1.5 pips beyond invalidation wick, no static take-profit (`take_profit = None`), and breakeven lock at +1.5R.
- Support parallel market close across all active tickets upon receiving an EXIT signal.

### R2. Local llama.cpp Micro-LLM Intuition Exit Integration
- Run `Qwen2.5-Coder-1.5B-Instruct-GGUF` on a local `llama.cpp` server (`http://localhost:8080`).
- Feed compressed 1-minute candle telemetry (`unrealized_r`, `candle_wick_ratio`, `volume_stall`, `dxy_divergence`).
- Constrain inference via GBNF grammar or JSON schema strictly to `{"decision": "HOLD"}` or `{"decision": "EXIT"}`.
- Enforce strict < 300ms execution timeout with automatic algorithmic fail-safe fallback to prevent blocking the event loop.

### R3. Macro Fundamental Calendar Blackout
- Maintain a background economic calendar monitor polling every 10 minutes.
- Unconditionally halt new trades within 15 minutes before and after High-Impact US news releases (CPI, NFP, FOMC, PPI, Fed Rate Decisions).

### R4. Walk-Forward Backtesting & Account Scaling Simulation
- Implement a reproducible backtest and Monte Carlo scaling harness from $65 toward $10,000 accounting for Hyperliquid maker/taker fees, execution slippage, and funding rates.
- Enforce and verify the 5% max daily drawdown killswitch across historical market cycles.

### R5. Linux VPS Systemd Production Suite & Watchdog Hardening
- Provide production-ready systemd unit services for the scalper engine, the local llama-server, and health watchdogs (`quant/hft/guard.py`).
- Limit total resident RAM to under 2.5 GB to guarantee stable operation on a 4GB RAM / 2-vCPU host.
- Emit structured Antigravity JSON telemetry for real-time monitoring and alerting.

## Verification Resources
- Existing test suite: `tests/test_gold_relapse_scalper.py` (margin invariant, SL envelope, order slicing, breakeven lock, macro blackout, LLM intuition, FSM lifecycle, drawdown killswitch).
- Core modules: `engine/execution_router.py`, `engine/fsm.py`, `macro/slm_intuition.py`, `run_relapse_scalper.py`.
- Reused components: `live_hyperliquid.py`, `scalper/pa/candles.py`, `scalper/pa/ict.py`, `quant/hft/utils/killzone.py`, `quant/engine/guards.py`.

## Acceptance Criteria

### Execution & Slicing
- [ ] Layered orders dispatch 3 tickets with 50ms stagger jitter via `asyncio.gather` on Hyperliquid mock/testnet.
- [ ] Initial margin never exceeds 20% of account equity at 1:1000 leverage.
- [ ] Stop-Loss envelope strictly rejects any trade requiring > 15.0 pips SL ($1.50).
- [ ] Breakeven lock automatically moves SL to Entry + 1.0 pip at +1.5R unrealized profit.
- [ ] Market close liquidates all sliced tickets in parallel without orphan orders.

### Local LLM Intuition Exit
- [ ] LLM query executes under 300ms using local `llama.cpp` server.
- [ ] Output strictly adheres to `{"decision": "HOLD"}` or `{"decision": "EXIT"}`.
- [ ] Algorithmic fail-safe triggers if server times out or is unreachable.

### Risk & Macro Filter
- [ ] Trading is completely halted during the +/- 15-minute window surrounding High-Impact US news events.
- [ ] 5% max daily drawdown killswitch immediately stops trading and protects equity.

### Production & Resource Constraints
- [ ] System resident memory remains <= 2.5 GB on a 4GB RAM VPS.
- [ ] All automated unit and integration tests pass cleanly with `pytest`.
- [ ] Systemd service units and watchdog health checks run without failure.

## 2026-09-17T19:15:15Z

User architectural refinement received:
In engine/execution_router.py, the Hyperliquid CLOB architecture has been refined:
1. Leverage & Margin Ceiling: 100x leverage (Hyperliquid max for GOLD perpetuals), initial margin capped strictly <= 20% equity ($13 on $65).
2. SL Envelope (Absolute Delta): Strictly $1.00 to $1.50 from entry price. Trigger price placed $0.10 to $0.15 beyond invalidation wick. Delta > $1.50 systematically rejected.
3. No Static TP: Open-ended dispatch. No resting TP on CLOB.
4. Order Slicing: fire_layered_orders() concurrently dispatches micro-units (three slices) via exchange.market_open(coin="GOLD") with 50ms stagger jitter via asyncio.gather.
5. Detached Stop Mechanism: Unified Stop Market order via exchange.market_close() for aggregate size with reduce_only=True immediately following entry slices.
6. Breakeven Lock: At +1.5R floating profit, cancel old stop order and place new reduce_only=True stop at Entry +/- $0.10.
7. Dynamic Basket Close: Instant liquidation of aggregate position via exchange.market_close(sz=total_sz, reduce_only=True) on EXIT flag, cancelling resting detached stop.
Code and all 10 tests in tests/test_gold_relapse_scalper.py are verified and passing.


## 2026-09-17T19:14:58Z

User architectural refinement:
In engine/execution_router.py, the Hyperliquid CLOB architecture has been refined:
1. Leverage & Margin Ceiling: 100x leverage (Hyperliquid max for GOLD perpetuals), initial margin capped strictly <= 20% equity ($13 on $65).
2. SL Envelope (Absolute Delta): Strictly $1.00 to $1.50 from entry price. Trigger price placed $0.10 to $0.15 beyond invalidation wick. Delta > $1.50 systematically rejected.
3. No Static TP: Open-ended dispatch. No resting TP on CLOB.
4. Order Slicing: fire_layered_orders() concurrently dispatches micro-units (three slices) via exchange.market_open(coin="GOLD") with 50ms stagger jitter via asyncio.gather.
5. Detached Stop Mechanism: Unified Stop Market order via exchange.market_close() for aggregate size with reduce_only=True immediately following entry slices.
6. Breakeven Lock: At +1.5R floating profit, cancel old stop order and place new reduce_only=True stop at Entry +/- $0.10.
7. Dynamic Basket Close: Instant liquidation of aggregate position via exchange.market_close(sz=total_sz, reduce_only=True) on EXIT flag, cancelling resting detached stop.
Code and all 10 tests in tests/test_gold_relapse_scalper.py are verified and passing.

## 2026-09-19T08:56:25Z

Build `hyper_predator_bot.py`, an ultra-aggressive, high-frequency M1 scalper for Hyperliquid DEX (`hyperliquid-python`) using a decoupled dual-core asynchronous architecture (background LLM macro edge + sub-50ms orderbook sniper with 5-slice order spamming, ruthless dynamic exits, and sub-5ms L2 order flow tape reading), accompanied by a repository purge and a decade-deep vectorized backtester (`backtester.py`).

Working directory: /Users/mac/Desktop/TBT-Engine
Integrity mode: development

## Requirements

### R1. Decoupled Dual-Core Architecture (Background Brain & Macro State)
- Implement `Core 1`: An independent asynchronous background loop `async def update_macro_edge()`.
- Every 5 minutes, query the local `llama.cpp` server (`http://localhost:8080/completion`) with compressed market telemetry (M15 Trend direction, DXY divergence, Economic calendar blackout status, and M5 Support/Resistance proximity).
- Enforce strict JSON output from LLM:
  ```json
  {"permit_trade": bool, "bias": "BULLISH" | "BEARISH", "volatility_regime": float}
  ```
- Store results in an in-memory thread-safe `MACRO_STATE` dataclass (`permit_trade`, `bias`, `volatility_regime`, `last_updated`).
- Ensure Core 1 never blocks or delays the execution thread under any condition (sub-500ms timeout with automatic fallback to previous safe state or algorithmic hold).

### R2. High-Frequency Aggressive Sniper Engine (Core 2)
- Subscribe to Hyperliquid L1 Orderbook WebSockets for live sub-millisecond price and book updates.
- Track M5 Support and Resistance levels dynamically using rolling pivot highs/lows.
- Evaluate the M1 Retest Trigger at every candle close:
  1. Price touches or enters the active M5 S/R zone.
  2. M1 candle forms an extreme Rejection Wick: Wick length must be >= 65% of the total candle range ((High - Low)), and candle body closes in the direction of `MACRO_STATE.bias`.
  3. Tick Velocity Edge: The final 5 seconds of the M1 candle must exhibit a volume or tick count spike >= 1.5x the rolling average tick velocity.
- If `MACRO_STATE.permit_trade == True` and both Wick Math and Tick Velocity criteria are met, trigger execution immediately without delay.

### R3. Layered Order Slicing & Execution Bridge (`spam_orders`)
- Implement `async def spam_orders(coin="GOLD", is_buy=bool, total_sz=float, slices=5)`.
- Use `asyncio.gather` with a 20ms jitter stagger to dispatch 5 micro-orders (e.g., `sz = 1.0` each) directly onto Hyperliquid CLOB at 100x leverage.
- Open-Ended Entries: No static Take-Profit orders sent at entry.
- Detached Stop-Loss: Immediately following entry fill confirmation, dispatch a single resting `exchange.market_close(coin=coin, sz=aggregate_sz, reduce_only=True)` order placed exactly $1.00 absolute dollar beyond the invalidation wick extreme.

### R4. Dynamic Ruthless Exits & Hard Equity Shield
- Target Exit: Calculate the next immediate opposing M5 S/R zone. The exact millisecond live WebSocket bid/ask reaches this level, execute `async def close_basket()` to instantly market-close all open slices.
- Reversal Exit: If an opposing >= 65% M1 rejection wick prints while in a position, trigger immediate market exit.
- Hard Equity Shield: If the active basket's floating unrealized PnL drops to -$10.00 (protecting a $65.00 micro-account base), immediately execute market close on all open tickets with `reduce_only=True`.

### R5. Advanced Micro-Structure & Order Flow Edge (L2 Exit Engine)
- Implement `async def orderflow_exit_monitor()` running concurrently with active positions, executing in pure local Python memory in < 5ms:
  1. **L2 Imbalance Edge:** Continuously calculate the Bid/Ask imbalance at the top 5 levels of the Hyperliquid `l2Book` WebSocket stream. If the bot is Long and (Ask Volume / Bid Volume) > 3.0 * MACRO_STATE.volatility_regime (or for Short, (Bid Volume / Ask Volume) > 3.0 * MACRO_STATE.volatility_regime), fire `close_all_positions()` immediately before the opposing wall triggers slippage.
  2. **Volume Delta Edge:** Monitor the Hyperliquid `trades` WebSocket stream. If in a profitable basket and momentum stalls (defined as > 80% of the last 20 trade ticks being aggressive opposing market fills, e.g. market sells when long), close the basket immediately.
  3. **Adaptive Thresholds:** The background `volatility_regime` multiplier dynamically adjusts the imbalance ratio to hold longer during high-conviction trends and exit faster during choppy markets.

### R6. Repository Purge & Strict Asset Focus
- Codebase Cleanup: Aggressively move to an `_archive/` directory all legacy MT5 connectors, slow synchronous LLM trading loops, and multi-asset routing logic. Ensure the active workspace is minimalist and clean.
- Strict Asset Focus: Hardcode the entire execution and risk engine EXCLUSIVELY for `GOLD` on Hyperliquid. Strip out all multi-ticker scanning, symbol loops, and dictionary lookups.

### R7. Decade-Deep Vectorized Backtester & Monte Carlo Sweeper (`backtester.py`)
- Implement a high-performance event-driven and vectorized backtester in `backtester.py` using `pandas` and `numpy`.
- Capable of ingesting and streaming massive historical datasets (e.g., M1 OHLCV from 2010 to present) without memory leaks, operating strictly within a 4GB RAM environment (using chunked iterators or memory-mapped arrays).
- Parameter sweep interface optimizing:
  - Wick rejection percentage threshold (60% to 75%).
  - M5 Support/Resistance lookback window (20 to 100 bars).
  - L2 Imbalance threshold (2.0 to 5.0) and Tick Velocity multiplier (1.2x to 2.0x).
- Run Monte Carlo simulations (minimum 500 runs) incorporating execution jitter and slippage.
- Output Sharpe Ratio, Max Drawdown ($/%), Win Rate, Profit Factor, and Total Trades.

### R8. Automated Testing & Verification
- Dedicated test suite `tests/test_hyper_predator.py` covering:
  - Core 1 non-blocking macro polling and `volatility_regime` JSON parsing.
  - Core 2 M1 >= 65% wick calculation and final 5s velocity surge detection.
  - `spam_orders` 5-slice dispatch with 20ms jitter.
  - L2 top-5 book imbalance calculation and < 5ms exit trigger.
  - Trade tape volume delta stall detection (> 80% opposing ticks).
  - Hard Equity Shield liquidation at -$10.00.
  - Vectorized backtester execution and memory efficiency on M1 data.

## Acceptance Criteria

### Architecture & Non-Blocking Decoupling
- [ ] `update_macro_edge()` runs as an isolated async background loop polling every 5m without blocking sniper execution.
- [ ] `MACRO_STATE` holds strictly validated `permit_trade` (bool), `bias` ("BULLISH" | "BEARISH"), and `volatility_regime` (float).
- [ ] Sub-50ms execution decision latency from M1 signal trigger to initial order dispatch.

### Math Edge & Signal Generation
- [ ] Wick calculation strictly rejects candles with rejection wick < 65% of high-low range.
- [ ] Rejection wick body closes in the direction of the macro bias.
- [ ] Tick velocity calculation strictly gates entries unless the final 5s volume/tick rate is > 1.5x the rolling baseline.

### Execution & Slicing
- [ ] `spam_orders` fires 5 micro-slices concurrently via `asyncio.gather` with 20ms jitter stagger.
- [ ] Detached Stop order placed exactly $1.00 absolute dollar away from invalidation wick with `reduce_only=True`.
- [ ] Execution engine is hardcoded exclusively for `GOLD` with zero multi-ticker overhead.

### Sub-Millisecond Dynamic Exits & L2 Orderflow
- [ ] Top-5 L2 book imbalance triggers instant basket liquidation when ratio exceeds 3.0 * volatility_regime.
- [ ] Trade tape volume delta triggers basket exit when > 80% of last 20 ticks are opposing market fills.
- [ ] Order flow exit decision executes within < 5ms in pure local memory.
- [ ] Immediate market exit triggered on opposing >= 65% M1 rejection wick.
- [ ] Hard Equity Shield triggers instant basket liquidation the moment unrealized loss reaches -$10.00.

### Codebase Cleanliness & Deep Backtesting
- [ ] Legacy MT5 connectors, obsolete trading loops, and unused multi-asset routers archived in `_archive/`.
- [ ] `backtester.py` vectorization processes extensive historical M1 data within the 4GB RAM envelope without OOM.
- [ ] Parameter sweep and 500-run Monte Carlo simulation output Sharpe Ratio, Win Rate, and Max Drawdown.
- [ ] All automated tests in `tests/test_hyper_predator.py` pass cleanly with `pytest`.
