## 2026-09-19T09:03:34Z
You are Worker 1 (Repository Purge & Asset Focus Worker).
Your working directory is: /Users/mac/Desktop/TBT-Engine/.agents/worker_m1_purge

MANDATORY INPUT:
Read /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md (specifically the requirements under ## 2026-09-19T08:56:25Z, in particular R6).
Read Explorer 2's survey report: /Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_2/survey_report.md (Section 2: Purge Inventory & Relocation Map).

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

OBJECTIVE:
Execute Milestone 1: Repository Purge & Strict Asset Focus (R6).
1. Create directory `_archive/` and subdirectories if needed (`_archive/scanners/`, `_archive/legacy_bots/`, `_archive/ml/`, `_archive/tests/`, `_archive/pwa/`, etc.).
2. Move obsolete legacy files and directories into `_archive/` as identified in Explorer 2's report:
   - Move directories:
     - `open-antigravity-patcher/` -> `_archive/open-antigravity-patcher/`
     - `scratch/` -> `_archive/scratch/`
     - `old/` -> `_archive/old/`
     - `services/` -> `_archive/services/`
     - `pine/` -> `_archive/pine/`
     - `tools/` -> `_archive/tools/`
     - `dataset/` -> `_archive/dataset/`
     - `quant/` -> `_archive/quant/`
     - `catboost_info/` -> `_archive/catboost_info/`
   - Move obsolete single files:
     - `signals/tv_cdp.py` -> `_archive/signals/tv_cdp.py`
     - `exchange/bitunix.py` -> `_archive/exchange/bitunix.py`
     - `not`, `would`, `bot_execution.log`, `*.bak*` -> `_archive/scratch/`
     - `icon-*.png` -> `_archive/pwa/`
     - `atrscan.py`, `boom2.py`, `hunt.py`, `hunt_paper.py`, `pace.py`, `rank3.py`, `screen.py`, `signal_report.py` -> `_archive/scanners/`
     - `papertrade.py`, `scout.py`, `perch.py`, `recorder.py`, `panel.py`, `paper_venue.py`, `journal.py`, `live_hyperliquid.py` -> `_archive/legacy_bots/`
     - `filter_model.py`, `kronos_brain.py` -> `_archive/ml/`
     - `guard.py` -> `_archive/guard.py`
   - Move legacy test files to `_archive/tests/`:
     - `tests/test_atrscan.py`, `tests/test_audit_fixes.py`, `tests/test_backup.py`, `tests/test_bitunix.py`, `tests/test_casestudy.py`, `tests/test_confidence.py`, `tests/test_dataset.py`, `tests/test_edges.py`, `tests/test_entry_score.py`, `tests/test_failures.py`, `tests/test_filter.py`, `tests/test_funnel.py`, `tests/test_geometry.py`, `tests/test_guard.py`, `tests/test_journal.py`, `tests/test_lifecycle.py`, `tests/test_live.py`, `tests/test_pace.py`, `tests/test_panel_autopilot.py`, `tests/test_panel_mobile_app.py`, `tests/test_paper_venue.py`, `tests/test_patterns_entries.py`, `tests/test_perch.py`, `tests/test_perch_holdable.py`, `tests/test_persistence.py`, `tests/test_recycle.py`, `tests/test_replay.py`, `tests/test_risk_controls.py`, `tests/test_riskanalysis.py`, `tests/test_sampler.py`, `tests/test_scanner.py`, `tests/test_scout.py`, `tests/test_second_book.py`, `tests/test_selector.py`, `tests/test_semantics.py`, `tests/test_shapes.py`, `tests/test_soak.py`, `tests/test_sweep.py`, `tests/test_trophy_lock.py`.
3. Verify that the active core files are preserved in the root:
   - `engine/`, `macro/`, `deploy/`, `data/`
   - Active gold tests: `tests/test_gold_relapse_scalper.py`, `tests/test_gold_killzones_multitz.py`, `tests/test_scaling_simulation.py`, `tests/test_guard_watchdog.py`, `tests/test_self_healing_and_ntfy.py`.
4. Run verification command:
   `pytest tests/test_gold_relapse_scalper.py tests/test_scaling_simulation.py tests/test_gold_killzones_multitz.py tests/test_guard_watchdog.py tests/test_self_healing_and_ntfy.py`
   Ensure all active tests pass cleanly without errors.
5. Check `git status` or file listings to ensure project root is now clean and organized.

SCOPE BOUNDARIES:
- DO NOT touch or delete active engine files (`engine/`, `macro/`, etc.).
- Move files to `_archive/` (preserving history), DO NOT rm -rf arbitrarily.
- Confine metadata and reports to `/Users/mac/Desktop/TBT-Engine/.agents/worker_m1_purge`.

OUTPUT REQUIREMENTS:
Write your implementation report to `/Users/mac/Desktop/TBT-Engine/.agents/worker_m1_purge/report.md` and handoff to `/Users/mac/Desktop/TBT-Engine/.agents/worker_m1_purge/handoff.md`. Include exact verification test results.
Then send a concise completion message back to orchestrator_3.
