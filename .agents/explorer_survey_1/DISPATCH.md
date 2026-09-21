## 2026-09-16T19:56:27Z
You are Explorer 1 (Orderflow and Alpha Explorer) investigating the codebase for Architecture B Evolution.
Working directory: /Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_1
Authoritative User Request: Read /Users/mac/Desktop/TBT-Engine/ORIGINAL_REQUEST.md before starting work. Do NOT assume or guess requirements.

Your mission:
1. Thoroughly explore the existing codebase in /Users/mac/Desktop/TBT-Engine to map all data structures, market data feeds, bar aggregation, order flow analysis, and alpha setups.
2. Specifically investigate R1 (Continuous Timeframe-Agnostic Data Layer & Feature Engine):
   - Event-driven bar structures (Range Bars, Volume Bars, Tick Charts)
   - Multi-level Order Flow Imbalance (OFI) across 5 levels with rolling z-score normalization
   - Cumulative Volume Delta (CVD) tracking and divergence calculations
   - Open Interest (OI) contraction monitoring (>2.5% drop in <300 ticks)
   - Hawkes trade arrival intensity with exponential decay kernel and excitation ratio vs median
   - Mark vs Mid and Perp vs Spot basis spreads in basis points
   - Level 2 Book Depth Imbalances (top 1% bid/ask volume clusters)
3. Specifically investigate R2 (Deterministic Mechanical Alpha Setups):
   - 5 setups: OFI VWAP Reversion, Liquidation Cascade Absorption, Hawkes Volatility Breakout, CVD Divergence Sweep, L2 Depth Imbalance Scalp
   - Strict entry conditions, mathematical formulations, and hard invalidation rules
4. Document existing files, candidate file paths, classes, data contracts, and missing components.
5. Write your detailed analysis to /Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_1/survey_r1_r2.md and a self-contained handoff.md in your working directory. Update progress.md with your liveness heartbeat. When complete, send a message back with your findings.

## 2026-09-19T08:58:11Z
You are Explorer 1 (Hyperliquid SDK & WS Explorer).
Your working directory is: /Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_1

MANDATORY INPUT:
Read /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md (specifically the requirements under ## 2026-09-19T08:56:25Z: R1 to R8).

OBJECTIVE:
Investigate existing Hyperliquid integration, SDK usage, WebSocket feeds, and execution bridging in the repository:
1. Examine how `hyperliquid-python` SDK is installed and used in the codebase (e.g. `engine/execution_router.py`, `live_hyperliquid.py`, `quant/`, etc.).
2. Examine WebSocket connection patterns (L1 orderbook WS, `l2Book`, `trades` streams) and how callbacks / async queues work.
3. Investigate how `spam_orders` (5 micro-orders with 20ms jitter via `asyncio.gather` at 100x leverage on GOLD), detached stop-loss ($1.00 beyond invalidation wick extreme with `reduce_only=True`), and `close_basket()` can be cleanly implemented.
4. Investigate Core 1 background loop: `update_macro_edge()`, polling `http://localhost:8080/completion` with compressed market telemetry, thread-safe `MACRO_STATE` dataclass, sub-500ms timeout with fallback.
5. Identify any existing mock/testnet exchange abstractions or fixtures that can be reused or extended for offline testing.

SCOPE BOUNDARIES:
- Read-only exploration. DO NOT modify or create any source code files.
- Confine all your metadata and reports to your working directory: /Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_1.

OUTPUT REQUIREMENTS:
Write your comprehensive report to `/Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_1/survey_report.md` and write your handoff to `/Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_1/handoff.md`.
Then send a concise summary message back to orchestrator_3.

COMPLETION CRITERIA:
Clear mapping of SDK methods, WS subscription structures, macro edge polling implementation plan, and execution patterns needed for R1, R2, R3, R5.
