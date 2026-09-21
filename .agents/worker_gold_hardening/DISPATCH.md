# Dispatch for worker_gold_hardening

Task: Adversarial Hardening & Defect Remediation (Iteration 2)
Authoritative Specification: /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md
Project Specification: /Users/mac/Desktop/TBT-Engine/.agents/orchestrator_2/PROJECT.md
Challenger 1 Report: /Users/mac/Desktop/TBT-Engine/.agents/challenger_gold_1/handoff.md
Challenger 2 Report: /Users/mac/Desktop/TBT-Engine/.agents/challenger_gold_2/handoff.md
Working Directory: /Users/mac/Desktop/TBT-Engine/.agents/worker_gold_hardening
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
Ensure 100% of all tests and stress scenarios pass.
Document all changes in `changes.md` and write a structured 5-component `handoff.md`.
