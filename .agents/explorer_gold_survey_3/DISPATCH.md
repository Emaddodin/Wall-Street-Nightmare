## 2026-09-17T19:10:58Z
You are explorer_gold_survey_3.
Your working directory is /Users/mac/Desktop/TBT-Engine/.agents/explorer_gold_survey_3.
Your parent is orchestrator_2 (convId: d8cde56b-142d-4ad0-b360-6f180e2c8eaa).

MANDATORY: Read the authoritative specification at /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md (specifically the section starting at ## 2026-09-17T19:08:47Z).

Your focus: R4. Walk-Forward Backtesting & Account Scaling Simulation ($65 to $10,000) & R5. Linux VPS Systemd Production Suite & Watchdog Hardening.
Investigate the codebase (e.g. tests/test_gold_relapse_scalper.py, run_relapse_scalper.py, quant/hft/guard.py, systemd unit definitions, etc.).
Determine:
1. Walk-Forward backtest and Monte Carlo scaling simulation harness from $65 to $10,000 with Hyperliquid fees, execution slippage, funding rates, and 5% max daily drawdown killswitch.
2. Production-ready systemd unit services for the scalper engine, local llama-server, and health watchdogs.
3. VPS resource constraints: ensuring RAM <= 2.5 GB on 4GB host.
4. Structured Antigravity JSON telemetry formatting and alerts.
5. Existing test suite structure in tests/test_gold_relapse_scalper.py and identify any gaps or implementation status.

Write your findings to /Users/mac/Desktop/TBT-Engine/.agents/explorer_gold_survey_3/survey_r4_r5.md and provide a structured handoff.md. Keep progress.md updated. When finished, send a completion message to parent.

## 2026-09-17T19:15:46Z
**Context**: User architectural refinement for Hyperliquid CLOB execution
**Content**: Sentinel provided architectural refinement in engine/execution_router.py:
1. Leverage & Margin Ceiling: 100x leverage (Hyperliquid max for GOLD perpetuals), initial margin capped strictly <= 20% equity ($13 on $65).
2. SL Envelope (Absolute Delta): Strictly $1.00 to $1.50 from entry price. Trigger price placed $0.10 to $0.15 beyond invalidation wick. Delta > $1.50 systematically rejected.
3. No Static TP: Open-ended dispatch. No resting TP on CLOB.
4. Order Slicing: fire_layered_orders() concurrently dispatches micro-units (three slices) via exchange.market_open(coin="GOLD") with 50ms stagger jitter via asyncio.gather.
5. Detached Stop Mechanism: Unified Stop Market order via exchange.market_close() for aggregate size with reduce_only=True immediately following entry slices.
6. Breakeven Lock: At +1.5R floating profit, cancel old stop order and place new reduce_only=True stop at Entry +/- $0.10.
7. Dynamic Basket Close: Instant liquidation of aggregate position via exchange.market_close(sz=total_sz, reduce_only=True) on EXIT flag, cancelling resting detached stop.
All 10 tests in tests/test_gold_relapse_scalper.py verified passing.
**Action**: Ensure your survey_r4_r5.md and Monte Carlo scaling/backtest design align with 100x leverage, $65 starting balance, and these execution mechanics.

