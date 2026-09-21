# Dispatch for worker_gold_hardening_2

Task: Adversarial Hardening & Defect Remediation (Iteration 2 Replacement)
Authoritative Specification: /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md
Project Specification: /Users/mac/Desktop/TBT-Engine/.agents/orchestrator_2/PROJECT.md
Challenger 1 Report: /Users/mac/Desktop/TBT-Engine/.agents/challenger_gold_1/handoff.md
Challenger 2 Report: /Users/mac/Desktop/TBT-Engine/.agents/challenger_gold_2/handoff.md
Working Directory: /Users/mac/Desktop/TBT-Engine/.agents/worker_gold_hardening_2
File Ownership (Exclusive): engine/monte_carlo_scaling.py, engine/execution_router.py, engine/fsm.py

Defects to Remediate:
1. `engine/monte_carlo_scaling.py:1044`:
   Handle `n_simulations=0` safely:
   `killswitch_trips_mean = round(total_killswitch_trips / n_simulations, 2) if n_simulations > 0 else 0.0`.
2. `engine/execution_router.py` in `fire_layered_orders`:
   In slice execution, catch exceptions in `asyncio.gather(*tasks, return_exceptions=True)`. If any slice raises an exception or fails to fill, immediately close any already filled slices with `venue.market_close(sz=filled_sz, reduce_only=True)` to prevent leaving open naked positions on the CLOB without stop loss.
3. `engine/execution_router.py` in `evaluate_breakeven_lock`:
   Acquire `async with self._lock:` and verify `if not basket.is_active: return False` after acquiring the lock to prevent concurrency race condition where breakeven stop order is dispatched after basket is liquidated.
4. `engine/fsm.py` in `RelapseFSM.on_5m_bar_update`:
   Add `is_macro_blackout` check in `RelapseState.TRIGGER_DETECTED` state (and any non-in-trade setup states). If `in_blackout` becomes True, transition back to `RelapseState.IDLE` to unconditionally prevent firing orders within the +/- 15m blackout window.

Verification:
- Run Challenger 1 harness: `python3 /Users/mac/Desktop/TBT-Engine/.agents/challenger_gold_1/stress_test_harness.py`
- Run Challenger 2 harness: `python3 /Users/mac/Desktop/TBT-Engine/.agents/challenger_gold_2/adversarial_stress_test.py`
- Run all unit test suites: `pytest tests/test_gold_relapse_scalper.py tests/test_scaling_simulation.py tests/test_hft_guard.py -v`
Ensure 100% of tests and stress scenarios pass.
Document all changes in `changes.md` and write a structured 5-component `handoff.md`.

## 2026-09-18T01:14:43Z
You are worker_gold_hardening_2.
Your working directory is /Users/mac/Desktop/TBT-Engine/.agents/worker_gold_hardening_2.
Your parent is orchestrator_2 (convId: d8cde56b-142d-4ad0-b360-6f180e2c8eaa).

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

MANDATORY: Read the authoritative specification at /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md.
Also read:
- /Users/mac/Desktop/TBT-Engine/.agents/orchestrator_2/PROJECT.md
- /Users/mac/Desktop/TBT-Engine/.agents/challenger_gold_1/handoff.md
- /Users/mac/Desktop/TBT-Engine/.agents/challenger_gold_2/handoff.md
- /Users/mac/Desktop/TBT-Engine/.agents/worker_gold_hardening_2/DISPATCH.md

Your exclusive write ownership:
- engine/monte_carlo_scaling.py
- engine/execution_router.py
- engine/fsm.py

Remediate the 4 adversarial edge cases identified by the Challengers:
1. `engine/monte_carlo_scaling.py:1044`:
   Handle `n_simulations=0` safely:
   `killswitch_trips_mean = round(total_killswitch_trips / n_simulations, 2) if n_simulations > 0 else 0.0`
2. `engine/execution_router.py` in `fire_layered_orders`:
   Catch slice exceptions via `asyncio.gather(*tasks, return_exceptions=True)`. If any slice fails or raises an exception, immediately execute an emergency market close for any filled slices (`venue.market_close(sz=filled_sz, reduce_only=True)`) to ensure no open positions are left unhedged on the CLOB without a stop loss.
3. `engine/execution_router.py` in `evaluate_breakeven_lock`:
   Acquire `async with self._lock:` and verify `if not basket.is_active: return False` after acquiring lock to eliminate race conditions between breakeven stop replacement and basket liquidation.
4. `engine/fsm.py` in `RelapseFSM.on_5m_bar_update`:
   Add `is_macro_blackout` check in `RelapseState.TRIGGER_DETECTED` (and non-in-trade setup states). If news blackout is detected, reset to `IDLE` to unconditionally prevent firing orders within the +/- 15m blackout window.

Run the verifications:
- `python3 /Users/mac/Desktop/TBT-Engine/.agents/challenger_gold_1/stress_test_harness.py`
- `python3 /Users/mac/Desktop/TBT-Engine/.agents/challenger_gold_2/adversarial_stress_test.py`
- `pytest tests/test_gold_relapse_scalper.py tests/test_scaling_simulation.py tests/test_hft_guard.py -v`

Ensure 100% of tests and stress scenarios pass. Write `changes.md` and a structured 5-component `handoff.md`.
When done, send completion message to parent.

## 2026-09-18T01:31:00Z
**Context**: Status check on Adversarial Defect Remediation (Iteration 2)
**Content**: Checking in on progress for the 4 fixes in engine/monte_carlo_scaling.py, engine/execution_router.py, and engine/fsm.py, and running the stress test harnesses.
**Action**: Please update progress.md and report current status.
