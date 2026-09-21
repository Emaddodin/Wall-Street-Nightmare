## 2026-09-19T09:08:45Z
You are Worker M2 (Hyper Predator Engine Worker).
Your working directory is: /Users/mac/Desktop/TBT-Engine/.agents/worker_m2_predator
Exclusive file ownership: /Users/mac/Desktop/TBT-Engine/hyper_predator_bot.py

MANDATORY INPUT:
Read /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md (specifically requirements R1, R2, R3, R4, R5, R6 under ## 2026-09-19T08:56:25Z).
Read /Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_1/survey_report.md (Hyperliquid SDK, WS feeds, order slicing, macro edge).
Read /Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_2/survey_report.md (S/R pivot math, wick math, tick velocity, dynamic exits, L2/tape order flow).
Read /Users/mac/Desktop/TBT-Engine/.agents/orchestrator_3/PROJECT.md (architecture and interface contracts).

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

OBJECTIVE:
Build `hyper_predator_bot.py`, an ultra-aggressive, high-frequency M1 scalper for Hyperliquid DEX (`hyperliquid-python`) implementing:
1. R1: Decoupled Dual-Core Architecture (Background Brain & Macro State):
   - `async def update_macro_edge()`: Independent async background loop polling every 5 minutes (or immediately on startup).
   - Queries local llama.cpp server (`http://localhost:8080/completion`) with compressed market telemetry (M15 Trend direction, DXY divergence, Economic calendar blackout status, M5 S/R proximity).
   - Enforce strict JSON output: `{"permit_trade": bool, "bias": "BULLISH" | "BEARISH", "volatility_regime": float}`.
   - Store in thread-safe in-memory `MACRO_STATE` dataclass (`permit_trade`, `bias`, `volatility_regime`, `last_updated`).
   - Sub-500ms timeout with automatic fallback to previous safe state or algorithmic hold (never blocks execution).
2. R2: High-Frequency Aggressive Sniper Engine (Core 2):
   - Subscribes to Hyperliquid L1 orderbook WS (`bbo` or L1 stream) for live sub-millisecond price and book updates.
   - Dynamic rolling M5 S/R pivots using rolling pivot highs/lows over lookback window (e.g. 50 bars).
   - M1 Retest Trigger evaluated at every candle close:
     1. Price touches or enters active M5 S/R zone.
     2. M1 candle forms extreme Rejection Wick: Wick length >= 65% of total high-low range (High - Low), and candle body closes in the direction of `MACRO_STATE.bias`.
     3. Tick Velocity Edge: Final 5 seconds of M1 candle must exhibit volume or tick count spike >= 1.5x rolling baseline tick velocity.
   - If `MACRO_STATE.permit_trade == True` and both Wick Math and Tick Velocity are met, trigger execution immediately (< 50ms decision latency).
3. R3: Layered Order Slicing & Execution Bridge (`spam_orders`):
   - `async def spam_orders(coin="GOLD", is_buy=bool, total_sz=float, slices=5)`:
   - Uses `asyncio.gather` with 20ms jitter stagger to dispatch 5 micro-orders directly onto Hyperliquid CLOB at 100x leverage on GOLD.
   - Open-Ended Entries: No static take-profit sent at entry.
   - Detached Stop-Loss: Immediately following entry fill confirmation, dispatch a single resting `exchange.market_close(coin=coin, sz=aggregate_sz, reduce_only=True)` order placed exactly $1.00 absolute dollar beyond the invalidation wick extreme ($wick\_low - 1.00$ for Long, $wick\_high + 1.00$ for Short).
4. R4: Dynamic Ruthless Exits & Hard Equity Shield:
   - Target Exit: Calculate next immediate opposing M5 S/R zone. The exact millisecond live WS bid/ask reaches this level, execute `async def close_basket()` to instantly market-close all open slices.
   - Reversal Exit: If an opposing >= 65% M1 rejection wick prints while in position, trigger immediate market exit.
   - Hard Equity Shield: If active basket floating uPnL drops to -$10.00, immediately execute market close on all open tickets with `reduce_only=True`.
5. R5: Advanced Micro-Structure & Order Flow Edge (`orderflow_exit_monitor`):
   - Runs concurrently with active positions, executing in pure local Python memory in < 5ms:
     1. L2 Imbalance Edge: Top-5 levels of Hyperliquid `l2Book`. If Long and (Ask Volume / Bid Volume) > 3.0 * MACRO_STATE.volatility_regime (or Short and (Bid Volume / Ask Volume) > 3.0 * MACRO_STATE.volatility_regime), fire `close_basket()` immediately before opposing wall triggers slippage.
     2. Volume Delta Edge: Monitor Hyperliquid `trades` stream. If in profitable basket and momentum stalls (> 80% of last 20 trade ticks are aggressive opposing market fills, e.g. market sells when long), close basket immediately.
     3. Adaptive thresholds scaled dynamically by `volatility_regime`.
6. R6 Strict Asset Focus: Hardcode execution and risk engine exclusively for `GOLD` on Hyperliquid. Zero multi-ticker overhead.
7. Modularity & Offline Verification:
   - Provide a clean class structure (`HyperPredatorBot`, `MacroState`, `SniperEngine`, `ExecutionBridge`, `OrderflowMonitor`, `TapeBookMemory`) with dependency injection for exchange venues (`SimulatedBrokerVenue` or live exchange) and async queues, allowing complete offline execution and deterministic unit testing in pytest.

SCOPE BOUNDARIES:
- Exclusive write ownership of `hyper_predator_bot.py`. DO NOT modify `backtester.py`.
- Confine metadata to `/Users/mac/Desktop/TBT-Engine/.agents/worker_m2_predator`.
