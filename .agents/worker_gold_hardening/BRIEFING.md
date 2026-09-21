# BRIEFING — 2026-09-17T19:52:00Z

## Mission
Harden 5-Minute XAUUSD Relapse Scalper execution router, FSM, and Monte Carlo scaling against 4 adversarial edge cases identified by Challengers 1 & 2.

## 🔒 My Identity
- Archetype: worker
- Roles: implementer, qa, specialist
- Working directory: /Users/mac/Desktop/TBT-Engine/.agents/worker_gold_hardening
- Original parent: d8cde56b-142d-4ad0-b360-6f180e2c8eaa
- Milestone: M5 Full E2E Test Suite Pass & Adversarial Hardening

## 🔒 Key Constraints
- Exclusive write ownership: engine/monte_carlo_scaling.py, engine/execution_router.py, engine/fsm.py
- Write agent metadata only in /Users/mac/Desktop/TBT-Engine/.agents/worker_gold_hardening/
- Remediate 4 identified adversarial defects:
  1. engine/monte_carlo_scaling.py:1044 ZeroDivisionError on n_simulations=0
  2. engine/execution_router.py fire_layered_orders unhedged slices on partial failure
  3. engine/execution_router.py evaluate_breakeven_lock race condition leading to orphan resting stop
  4. engine/fsm.py on_5m_bar_update macro blackout check in TRIGGER_DETECTED state
- Must pass Challenger 1 harness, Challenger 2 harness, and pytest suites at 100%

## Current Parent
- Conversation ID: d8cde56b-142d-4ad0-b360-6f180e2c8eaa
- Updated: not yet

## Task Summary
- **What to build**: Adversarial hardening patches in engine/monte_carlo_scaling.py, engine/execution_router.py, and engine/fsm.py
- **Success criteria**: 100% pass on challenger_gold_1/stress_test_harness.py, challenger_gold_2/adversarial_stress_test.py, and pytest test suites
- **Interface contracts**: /Users/mac/Desktop/TBT-Engine/.agents/orchestrator_2/PROJECT.md § Interface Contracts
- **Code layout**: /Users/mac/Desktop/TBT-Engine/.agents/orchestrator_2/PROJECT.md § Code Layout

## Key Decisions Made
- Use return_exceptions=True in asyncio.gather for slice dispatching; if any slice fails or raises, immediately market-close any filled slices to prevent naked unhedged positions
- Synchronize evaluate_breakeven_lock using self._lock and verify basket.is_active and active_basket_id to prevent orphan stop placement post-close
- Guard TRIGGER_DETECTED state against in_blackout and !in_kz, transitioning back to IDLE
- Guard n_simulations=0 in MonteCarloScalingSimulator.run_simulation at line 1044

## Artifact Index
- .agents/worker_gold_hardening/BRIEFING.md — Situational awareness working memory
- .agents/worker_gold_hardening/progress.md — Liveness heartbeat & task progress
- .agents/worker_gold_hardening/changes.md — Detailed code changes log
- .agents/worker_gold_hardening/handoff.md — 5-component handoff report

## Change Tracker
- **Files modified**: None yet
- **Build status**: Pending
- **Pending issues**: None

## Quality Status
- **Build/test result**: Not yet executed
- **Lint status**: Pending
- **Tests added/modified**: Pending

## Loaded Skills
None
