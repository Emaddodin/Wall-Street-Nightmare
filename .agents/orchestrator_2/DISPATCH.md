# Dispatch Instructions

## 2026-09-17T19:09:40Z

You are the Project Orchestrator for the task defined in ORIGINAL_REQUEST.md.
Working directory: /Users/mac/Desktop/TBT-Engine/.agents/orchestrator_2
Project root: /Users/mac/Desktop/TBT-Engine
Authoritative specification: /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md

Mission:
Deploy, backtest, and harden the 5-Minute XAUUSD Relapse Scalper natively on Hyperliquid DEX (replacing MT5). The system must integrate a local llama.cpp GGUF server for sub-second dynamic intuition-based exits, asynchronous Order Slicing, and a rigid risk framework to scale a $65 micro-account to $10,000 on a 4GB Linux VPS using systemd services.

Requirements:
- R1. Hyperliquid DEX Asynchronous Execution Bridge (layered orders 3 tickets with 50ms jitter via asyncio.gather, 1:1000 leverage, <= 20% margin, SL 10-15 pips [$1.00-$1.50], breakeven lock at +1.5R, parallel market close).
- R2. Local llama.cpp Micro-LLM Intuition Exit Integration (Qwen2.5-Coder-1.5B-Instruct-GGUF on http://localhost:8080, compressed 1m candle telemetry, GBNF grammar / JSON schema {"decision": "HOLD"|"EXIT"}, < 300ms timeout with algorithmic fail-safe).
- R3. Macro Fundamental Calendar Blackout (economic calendar polling every 10m, halt new trades +/- 15m around High-Impact US news).
- R4. Walk-Forward Backtesting & Account Scaling Simulation ($65 to $10,000 Monte Carlo scaling harness with Hyperliquid fees, slippage, funding rates, 5% daily max drawdown killswitch).
- R5. Linux VPS Systemd Production Suite & Watchdog Hardening (production-ready systemd services, RAM <= 2.5 GB on 4GB host, structured Antigravity JSON telemetry).

Existing verification resources:
`tests/test_gold_relapse_scalper.py`, `engine/execution_router.py`, `engine/fsm.py`, `macro/slm_intuition.py`, `run_relapse_scalper.py`, etc.

## 2026-09-17T19:15:15Z

User architectural refinement received from Sentinel:
In engine/execution_router.py, the Hyperliquid CLOB architecture has been refined:
1. Leverage & Margin Ceiling: 100x leverage (Hyperliquid max for GOLD perpetuals), initial margin capped strictly <= 20% equity ($13 on $65).
2. SL Envelope (Absolute Delta): Strictly $1.00 to $1.50 from entry price. Trigger price placed $0.10 to $0.15 beyond invalidation wick. Delta > $1.50 systematically rejected.
3. No Static TP: Open-ended dispatch. No resting TP on CLOB.
4. Order Slicing: fire_layered_orders() concurrently dispatches micro-units (three slices) via exchange.market_open(coin="GOLD") with 50ms stagger jitter via asyncio.gather.
5. Detached Stop Mechanism: Unified Stop Market order via exchange.market_close() for aggregate size with reduce_only=True immediately following entry slices.
6. Breakeven Lock: At +1.5R floating profit, cancel old stop order and place new reduce_only=True stop at Entry +/- $0.10.
7. Dynamic Basket Close: Instant liquidation of aggregate position via exchange.market_close(sz=total_sz, reduce_only=True) on EXIT flag, cancelling resting detached stop.
Code and all 10 tests in tests/test_gold_relapse_scalper.py are verified and passing.

