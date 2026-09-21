# Orchestration Plan — 5-Minute XAUUSD Relapse Scalper on Hyperliquid

## Objective
Deploy, backtest, and harden the 5-Minute XAUUSD Relapse Scalper natively on Hyperliquid DEX with local llama.cpp GGUF exit intuition, async order slicing, and rigid risk framework to scale a $65 micro-account to $10,000 on a 4GB Linux VPS.

## Step 0: Survey & Codebase Mapping (Parallel Explorers)
Dispatch 3 Explorers in parallel to inspect existing codebase, requirements, and test suites:
- **Explorer 1**: R1 Execution Bridge & Order Slicing (`engine/execution_router.py`, `live_hyperliquid.py`, order slicing with 50ms jitter, 1:1000 leverage, <=20% margin invariant, 10-15 pip SL envelope, breakeven at +1.5R, parallel market close).
- **Explorer 2**: R2 Local LLM Intuition & R3 Macro Fundamental Blackout (`macro/slm_intuition.py`, llama.cpp Qwen2.5-Coder-1.5B GGUF server, GBNF grammar, <300ms timeout with algorithmic fail-safe, 10-minute calendar polling, +/-15m blackout for High Impact US news).
- **Explorer 3**: R4 Walk-Forward Backtesting / $65->$10,000 Scaling Simulation & R5 VPS Systemd Suite / Watchdog (`tests/test_gold_relapse_scalper.py`, Monte Carlo simulation with Hyperliquid fees/slippage/funding, 5% daily drawdown killswitch, systemd unit files, watchdog memory constraints <=2.5GB RAM, Antigravity JSON telemetry).

## Step 1: Synthesis & PROJECT.md
Aggregate Explorer findings:
- Feature Inventory mapping R1 through R5.
- Milestone definitions and cross-module interface contracts.
- Code layout and test infrastructure specification.

## Step 2: Milestone Implementation & Verification (Dual Track)
- Milestone 1: Hyperliquid DEX Asynchronous Execution Bridge (R1)
- Milestone 2: Local llama.cpp Micro-LLM Intuition Exit & Macro Blackout (R2, R3)
- Milestone 3: Walk-Forward Backtest & Monte Carlo Scaling Harness (R4)
- Milestone 4: VPS Systemd Suite, Watchdog Hardening & Antigravity Telemetry (R5)
- E2E Testing Track & Coverage Verification (`tests/test_gold_relapse_scalper.py`, etc.)

## Step 3: Hardening, Gate Checks & Completion
- Verification via Reviewers, Challengers, and Forensic Integrity Auditor.
- Complete victory report delivered to Sentinel parent agent.
