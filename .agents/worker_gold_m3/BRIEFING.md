# BRIEFING — 2026-09-17T19:42:00Z

## Mission
Implement Monte Carlo scaling engine ($65 -> $10,000) and verification test suite for 5M XAUUSD Relapse Scalper on Hyperliquid DEX.

## 🔒 My Identity
- Archetype: worker
- Roles: implementer, qa, specialist
- Working directory: /Users/mac/Desktop/TBT-Engine/.agents/worker_gold_m3
- Original parent: d8cde56b-142d-4ad0-b360-6f180e2c8eaa
- Milestone: M3 Walk-Forward Backtesting & Monte Carlo Scaling Harness ($65->$10k)

## 🔒 Key Constraints
- Starting equity $65.00, target $10,000.
- Leverage: 100x (Hyperliquid GOLD perpetual maximum).
- Margin ceiling: initial margin strictly <= 20% equity ($13.00 max on $65).
- Dynamic compounding formula: basket size S(E) = max(0.45, round_down(0.18 * E * 100 / P, 2)), 3 slices with 50ms jitter delay model, risk strictly < 0.86% equity.
- Friction modeling: Hyperliquid fees (3.5 bps taker, -0.2 bps maker), 0.5-1.5 pip slippage, and 1h funding rates.
- 5% daily max drawdown killswitch: track UTC daily peak equity, halt trading for remainder of day if daily drawdown >= 5%.
- Monte Carlo simulation: N permutations (default 1,000) over historical/synthetic trade series, evaluating probability of reaching $10,000, median time, max drawdown distribution, and Sharpe ratio.
- CLI execution support: `python3 engine/monte_carlo_scaling.py --simulations 1000 --trades 500`.
- Exclusive write ownership: engine/monte_carlo_scaling.py, tests/test_scaling_simulation.py.
- DO NOT CHEAT: Genuine implementation, no hardcoded test results, maintain real state.

## Current Parent
- Conversation ID: d8cde56b-142d-4ad0-b360-6f180e2c8eaa
- Updated: 2026-09-17T19:42:00Z

## Task Summary
- **What to build**: `engine/monte_carlo_scaling.py` and `tests/test_scaling_simulation.py`
- **Success criteria**: Full test pass for `tests/test_scaling_simulation.py` and `tests/test_gold_relapse_scalper.py`, CLI simulation runs cleanly, 5% DD killswitch and margin invariants verified.
- **Interface contracts**: `/Users/mac/Desktop/TBT-Engine/.agents/orchestrator_2/PROJECT.md`
- **Code layout**: `/Users/mac/Desktop/TBT-Engine/.agents/orchestrator_2/PROJECT.md § Code Layout`

## Key Decisions Made
- Implemented dynamic compounding formula S(E) = max(0.45, round_down(0.18 * E * 100 / P, 2)) with strict margin and risk ceiling clamps.
- Implemented 3-slice partitioning with 50ms stagger jitter delays (0ms, 50ms, 100ms) and micro-drift modeling.
- Modeled Hyperliquid CLOB fees (3.5 bps taker, -0.2 bps maker rebate), 0.5-1.5 pip slippage, and 1h funding payments.
- Created `SimulationDailyDrawdownGuard` to enforce the 5% daily max drawdown killswitch and reset on UTC midnight.
- Implemented Monte Carlo simulation supporting both synthetic ICT trade generation and custom/historical trade input.
- Added full CLI support with summary table and `--json` serialization.

## Change Tracker
- **Files modified**:
  - `engine/monte_carlo_scaling.py`: Full Monte Carlo scaling engine and CLI harness
  - `tests/test_scaling_simulation.py`: Comprehensive 17-test unit and integration test suite
- **Build status**: All tests passing (17/17 in test_scaling_simulation.py, 13/13 in test_gold_relapse_scalper.py)
- **Pending issues**: None

## Quality Status
- **Build/test result**: PASS (30/30 total tests passing across scaling and scalper suites)
- **Lint status**: 0 violations (ruff check clean)
- **Tests added/modified**: 17 new tests covering dynamic compounding, margin invariants, risk bounds, friction, slicing, killswitch, Monte Carlo engine, and CLI.

## Loaded Skills
- None specified in prompt.

## Artifact Index
- `.agents/worker_gold_m3/BRIEFING.md` — Agent state and working memory
- `.agents/worker_gold_m3/progress.md` — Liveness heartbeat and step tracking
- `.agents/worker_gold_m3/changes.md` — Documented changes
- `.agents/worker_gold_m3/handoff.md` — 5-component handoff report
- `engine/monte_carlo_scaling.py` — Monte Carlo scaling engine
- `tests/test_scaling_simulation.py` — Test suite for scaling and Monte Carlo simulation
