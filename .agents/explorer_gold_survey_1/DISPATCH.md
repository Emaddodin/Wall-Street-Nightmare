# Dispatch for explorer_gold_survey_1

## 2026-09-17T19:10:58Z

Target: R1 Hyperliquid DEX Asynchronous Execution Bridge & Order Slicing
Authoritative Specification: /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md
Working Directory: /Users/mac/Desktop/TBT-Engine/.agents/explorer_gold_survey_1

Task:
Read /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md.
Investigate existing execution modules (e.g., engine/execution_router.py, live_hyperliquid.py, engine/fsm.py, etc.) and tests (tests/test_gold_relapse_scalper.py).
Survey:
1. Architecture of Hyperliquid DEX bridge (mock/testnet mode, live keys, API contracts).
2. Order Slicing implementation (3 tickets with 50ms jitter via asyncio.gather).
3. Risk invariants: 1:1000 leverage, <= 20% max initial margin, 10.0-15.0 pip ($1.00-$1.50) SL envelope, breakeven lock at +1.5R, take_profit = None.
4. Parallel market close across all active tickets.
5. Current test coverage in tests/test_gold_relapse_scalper.py and identify any gaps or implementation status.

Write your findings to survey_r1.md and provide a structured handoff.md.

## 2026-09-17T19:15:42Z
From: orchestrator_2 (d8cde56b-142d-4ad0-b360-6f180e2c8eaa)
Context: User architectural refinement for Hyperliquid CLOB execution
Content: Sentinel provided architectural refinement in engine/execution_router.py:
1. Leverage & Margin Ceiling: 100x leverage (Hyperliquid max for GOLD perpetuals), initial margin capped strictly <= 20% equity ($13 on $65).
2. SL Envelope (Absolute Delta): Strictly $1.00 to $1.50 from entry price. Trigger price placed $0.10 to $0.15 beyond invalidation wick. Delta > $1.50 systematically rejected.
3. No Static TP: Open-ended dispatch. No resting TP on CLOB.
4. Order Slicing: fire_layered_orders() concurrently dispatches micro-units (three slices) via exchange.market_open(coin="GOLD") with 50ms stagger jitter via asyncio.gather.
5. Detached Stop Mechanism: Unified Stop Market order via exchange.market_close() for aggregate size with reduce_only=True immediately following entry slices.
6. Breakeven Lock: At +1.5R floating profit, cancel old stop order and place new reduce_only=True stop at Entry +/- $0.10.
7. Dynamic Basket Close: Instant liquidation of aggregate position via exchange.market_close(sz=total_sz, reduce_only=True) on EXIT flag, cancelling resting detached stop.
All 10 tests in tests/test_gold_relapse_scalper.py verified passing.
Action: Incorporate this refinement into your survey_r1.md and handoff.md.
