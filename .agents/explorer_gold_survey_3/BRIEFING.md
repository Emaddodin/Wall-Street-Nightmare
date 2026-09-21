# BRIEFING — 2026-09-17T19:11:30Z

## Mission
Investigate R4 (Walk-Forward Backtesting & Account Scaling Simulation $65 to $10,000) and R5 (Linux VPS Systemd Production Suite & Watchdog Hardening).

## 🔒 My Identity
- Archetype: explorer
- Roles: read-only investigation, synthesis, gap analysis
- Working directory: /Users/mac/Desktop/TBT-Engine/.agents/explorer_gold_survey_3
- Original parent: orchestrator_2 (convId: d8cde56b-142d-4ad0-b360-6f180e2c8eaa)
- Milestone: Gold Survey R4 & R5

## 🔒 Key Constraints
- Read-only investigation — do NOT implement
- Produce survey_r4_r5.md and handoff.md
- Inform orchestrator_2 via send_message

## Current Parent
- Conversation ID: d8cde56b-142d-4ad0-b360-6f180e2c8eaa
- Updated: 2026-09-17T19:17:00Z

## Investigation State
- **Explored paths**: `ORIGINAL_REQUEST.md`, `tests/test_gold_relapse_scalper.py`, `tests/test_hft_guard.py`, `run_relapse_scalper.py`, `quant/hft/guard.py`, `engine/execution_router.py`, `engine/fsm.py`, `quant/tools/walkforward.py`, `scalper/robustness/__init__.py`, `deploy/stratton-llm-critic.service`, `deploy/install_llama.sh`.
- **Key findings**:
  1. All 10 tests in `test_gold_relapse_scalper.py` and 5 tests in `test_hft_guard.py` pass cleanly.
  2. 100x leverage on Hyperliquid GOLD perps at 20% margin ceiling allows 0.45 oz size on $65 equity ($11.25 margin, 17.3%), risking only $0.54 per trade (0.83% equity).
  3. Formulated dynamic compounding model scaling to 72.0 oz at $10,000 equity.
  4. 5% daily drawdown killswitch halts trading upon $\ge 5\%$ drop from daily peak equity.
  5. Total VPS resident RAM budgeted at ~2,020 MB <= 2,560 MB (2.5 GB ceiling) on 4GB host, enforced by systemd `MemoryMax` cgroups.
  6. Defined systemd production suite for scalper, llama-server, and watchdog timer.
  7. Formatted Antigravity JSON telemetry schema and event catalog.
- **Unexplored areas**: None for R4/R5 survey scope; ready for milestone implementation.

## Key Decisions Made
- Confirmed alignment with Hyperliquid CLOB invariants (100x leverage, detached stops, breakeven lock at +1.5R, dynamic close).
- Documented full systemd units and Monte Carlo architecture in `survey_r4_r5.md`.
- Completed hard handoff in `handoff.md`.

## Artifact Index
- /Users/mac/Desktop/TBT-Engine/.agents/explorer_gold_survey_3/DISPATCH.md — Initial prompt & updates
- /Users/mac/Desktop/TBT-Engine/.agents/explorer_gold_survey_3/BRIEFING.md — Working memory
- /Users/mac/Desktop/TBT-Engine/.agents/explorer_gold_survey_3/progress.md — Liveness & progress tracking
- /Users/mac/Desktop/TBT-Engine/.agents/explorer_gold_survey_3/survey_r4_r5.md — Detailed survey report
- /Users/mac/Desktop/TBT-Engine/.agents/explorer_gold_survey_3/handoff.md — 5-component handoff report

