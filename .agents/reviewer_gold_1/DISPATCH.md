# Dispatch for reviewer_gold_1

Role: High-Reliability Reviewer 1
Authoritative Specification: /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md
Project Specification: /Users/mac/Desktop/TBT-Engine/.agents/orchestrator_2/PROJECT.md
Working Directory: /Users/mac/Desktop/TBT-Engine/.agents/reviewer_gold_1

Task:
1. Review correctness, completeness, and interface conformance of:
   - `engine/execution_router.py`: Hyperliquid DEX bridge, `HyperliquidDEXVenue`, 100x leverage, <= 20% margin ceiling, $1.00-$1.50 SL delta, 3-slice order dispatch with 50ms jitter via `asyncio.gather`, detached reduce_only stop market order, breakeven lock at +1.5R, dynamic basket close.
   - `engine/monte_carlo_scaling.py`: $65->$10k dynamic compounding model, fees, slippage, funding, 5% daily drawdown killswitch, Monte Carlo simulation.
2. Execute verification:
   - `pytest tests/test_gold_relapse_scalper.py -v`
   - `pytest tests/test_scaling_simulation.py -v`
3. Deliver verdict: APPROVE or REQUEST_CHANGES in handoff.md.

## 2026-09-17T19:43:16Z
You are reviewer_gold_1.
Your working directory is /Users/mac/Desktop/TBT-Engine/.agents/reviewer_gold_1.
Your parent is orchestrator_2 (convId: d8cde56b-142d-4ad0-b360-6f180e2c8eaa).

MANDATORY: Read the authoritative specification at /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md.
Also read:
- /Users/mac/Desktop/TBT-Engine/.agents/orchestrator_2/PROJECT.md
- /Users/mac/Desktop/TBT-Engine/.agents/reviewer_gold_1/DISPATCH.md

Review the execution and simulation modules:
- engine/execution_router.py (HyperliquidDEXVenue, 100x leverage, <=20% margin, $1.00-$1.50 SL delta, 3-slice order dispatch with 50ms jitter via asyncio.gather, detached reduce_only stop market order, breakeven lock at +1.5R, dynamic basket close).
- engine/monte_carlo_scaling.py ($65->$10k dynamic compounding, fees, slippage, funding, 5% DD killswitch).
- tests/test_gold_relapse_scalper.py and tests/test_scaling_simulation.py.

Run the test suites:
- pytest tests/test_gold_relapse_scalper.py -v
- pytest tests/test_scaling_simulation.py -v

Deliver a structured review with your verdict (APPROVE or REQUEST_CHANGES) in handoff.md and send completion message to parent.
