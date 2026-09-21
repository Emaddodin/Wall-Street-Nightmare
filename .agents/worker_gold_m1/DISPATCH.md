# Dispatch for worker_gold_m1

Milestone: M1 Hyperliquid DEX Adapter & Execution Hardening
Authoritative Specification: /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md
Project Specification: /Users/mac/Desktop/TBT-Engine/.agents/orchestrator_2/PROJECT.md
Explorer Survey: /Users/mac/Desktop/TBT-Engine/.agents/explorer_gold_survey_1/handoff.md
Working Directory: /Users/mac/Desktop/TBT-Engine/.agents/worker_gold_m1
File Ownership (Exclusive): engine/execution_router.py, tests/test_gold_relapse_scalper.py

Task:
1. Implement `HyperliquidDEXVenue` conforming to `HyperliquidVenue` protocol in `engine/execution_router.py`:
   - Wrap `hyperliquid-python-sdk` (`hyperliquid.exchange.Exchange` and `hyperliquid.info.Info`).
   - Execute synchronous SDK methods in worker threads via `asyncio.to_thread()` to prevent blocking the async event loop.
   - Support testnet (`hl_constants.TESTNET_API_URL`) and live mode, configurable via environment/arguments.
   - Maintain all refined CLOB invariants: 100x leverage on GOLD, initial margin <= 20% equity, $1.00-$1.50 SL delta, 3-slice order dispatch with 50ms jitter via `asyncio.gather`, detached reduce_only stop market order, breakeven lock at +1.5R, and dynamic basket close.
2. Expand `tests/test_gold_relapse_scalper.py`:
   - Add test case verifying Short (SELL) SL envelope invariant.
   - Add test case verifying Short breakeven lock at +1.5R.
   - Add test case verifying `HyperliquidDEXVenue` async execution with mocked SDK.
3. Run `pytest tests/test_gold_relapse_scalper.py -v` and verify all tests pass.
4. Document changes in `changes.md` and write a structured 5-component `handoff.md`.

## 2026-09-17T19:18:27Z
You are worker_gold_m1.
Your working directory is /Users/mac/Desktop/TBT-Engine/.agents/worker_gold_m1.
Your parent is orchestrator_2 (convId: d8cde56b-142d-4ad0-b360-6f180e2c8eaa).

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

MANDATORY: Read the authoritative specification at /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md.
Also read:
- /Users/mac/Desktop/TBT-Engine/.agents/orchestrator_2/PROJECT.md
- /Users/mac/Desktop/TBT-Engine/.agents/explorer_gold_survey_1/handoff.md
- /Users/mac/Desktop/TBT-Engine/.agents/worker_gold_m1/DISPATCH.md

Your exclusive write ownership:
- engine/execution_router.py
- tests/test_gold_relapse_scalper.py

Task:
1. Implement `HyperliquidDEXVenue` conforming to `HyperliquidVenue` protocol in `engine/execution_router.py`:
   - Wrap `hyperliquid-python-sdk` (`hyperliquid.exchange.Exchange` and `hyperliquid.info.Info`).
   - Execute all synchronous SDK network calls via `asyncio.to_thread()` to prevent blocking the async event loop.
   - Support testnet (`hl_constants.TESTNET_API_URL`) and mainnet modes, taking credentials or falling back cleanly.
   - Maintain all refined CLOB invariants: 100x leverage on GOLD, initial margin <= 20% equity, $1.00-$1.50 SL delta, 3-slice order dispatch with 50ms jitter via `asyncio.gather`, detached reduce_only stop market order, breakeven lock at +1.5R, dynamic basket close.
2. Expand `tests/test_gold_relapse_scalper.py`:
   - Add test case verifying Short (SELL) SL envelope invariant (> $1.50 rejected, placed beyond high wick).
   - Add test case verifying Short breakeven lock at +1.5R (moves stop to Entry - $0.10 with reduce_only=True).
   - Add test case verifying `HyperliquidDEXVenue` async execution with mocked SDK.
3. Run the test suite: `pytest tests/test_gold_relapse_scalper.py -v`. Ensure 100% of tests pass.
4. Write `changes.md` and a structured 5-component `handoff.md` in /Users/mac/Desktop/TBT-Engine/.agents/worker_gold_m1/.
When done, send a completion message to parent.

## 2026-09-17T19:27:40Z
**Context**: Status check on Milestone M1 DEX Adapter & Tests
**Content**: Checking in on progress for HyperliquidDEXVenue implementation in engine/execution_router.py and tests in tests/test_gold_relapse_scalper.py.
**Action**: Please update progress.md and report current status.
