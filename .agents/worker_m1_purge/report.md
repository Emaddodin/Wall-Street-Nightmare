# Milestone 1 Implementation Report: Repository Purge & Strict Asset Focus (R6)

**Agent**: Worker 1 (`worker_m1_purge`)  
**Timestamp**: 2026-09-19T09:08:00Z  
**Directory**: `/Users/mac/Desktop/TBT-Engine`  
**Status**: COMPLETE & FULLY VERIFIED

---

## 1. Executive Summary

Milestone 1 execution has successfully quarantined **2,038 obsolete files** spanning TradingView Chrome DevTools Protocol scrapers, legacy Bitunix CEX connectors, multi-asset crypto scanner scripts, obsolete ML training logs and model files, PWA dashboard assets, scratch scripts, backup files, and 39 legacy unit test files into the `_archive/` directory.

The active repository root and core modules (`engine/`, `macro/`, `deploy/`, `data/`, `tests/`) are now fully purged of legacy clutter, decoupled from obsolete research dependencies, and strictly focused on single-asset execution (`GOLD` on Hyperliquid DEX).

All 57 active unit tests pass with 100% success rate in 2.87 seconds.

---

## 2. Pre-Purge Audit & Decoupling

### 2.1 Baseline Verification
Prior to any file moves, the active gold test suite was executed:
```bash
pytest tests/test_gold_relapse_scalper.py tests/test_scaling_simulation.py tests/test_gold_killzones_multitz.py tests/test_guard_watchdog.py tests/test_self_healing_and_ntfy.py
```
**Result**: 57 passed in 3.59s.

### 2.2 Dependency Decoupling (`engine/fsm.py`)
During codebase audit, an import of `from quant.engine.guards import DAY_MS` was discovered in `engine/fsm.py` (line 69). To eliminate dependency on the obsolete `quant/` module before relocation, `DAY_MS = 86_400_000` (the constant representing milliseconds in a standard UTC day) was defined directly within `engine/fsm.py`.

---

## 3. Quarantined Components (`_archive/` Relocation Map)

All legacy files were moved into `_archive/` preserving directory organization and git history:

### 3.1 Obsolete Directories Relocated
| Source Directory | Archive Destination | Description |
|---|---|---|
| `open-antigravity-patcher/` | `_archive/open-antigravity-patcher/` | External Electron/Python IDE patcher (1,492 files, ~20.5 MB) |
| `scratch/` | `_archive/scratch/` | Stale audit snapshot duplicates and scratch scripts (266 files) |
| `old/` | `_archive/old/` | Stale tarballs (`dead-code-*.tar.gz`, `removed-*.tar.gz`) |
| `services/` | `_archive/services/` | 34 legacy systemd services for Chrome, Xvfb, VNC, and old timers |
| `pine/` | `_archive/pine/` | `Stratton_Oakmont_Sniper.pine` TradingView indicator script |
| `tools/` | `_archive/tools/` | 58 legacy diagnostic and window-positioning scripts |
| `dataset/` | `_archive/dataset/` | Dataset creation and sampler scripts for obsolete altcoin ML |
| `quant/` | `_archive/quant/` | Multi-asset Bitunix research, legacy backtesters, and strategies |
| `catboost_info/` | `_archive/catboost_info/` | Stale CatBoost ML training checkpoints and TensorBoard events |

### 3.2 Obsolete Single Files Relocated
| File(s) | Archive Destination | Category |
|---|---|---|
| `signals/tv_cdp.py`, `signals/__init__.py` | `_archive/signals/` | Chrome DevTools Protocol TradingView scraper |
| `exchange/bitunix.py`, `exchange/__init__.py` | `_archive/exchange/` | Bitunix CEX exchange client |
| `not`, `would`, `bot_execution.log`, `*.bak*`, `.env.bak.*` | `_archive/scratch/` | Scratch files, stale logs, and editor backups |
| `icon-1024.png`, `icon-180.png`, `icon-192.png`, `icon-512-maskable.png`, `icon-512.png` | `_archive/pwa/` | PWA web panel icons |
| `atrscan.py`, `boom2.py`, `hunt.py`, `hunt_paper.py`, `pace.py`, `rank3.py`, `screen.py`, `signal_report.py` | `_archive/scanners/` | Multi-coin ranking and scanning scripts |
| `papertrade.py`, `scout.py`, `perch.py`, `recorder.py`, `panel.py`, `paper_venue.py`, `journal.py`, `live_hyperliquid.py` | `_archive/legacy_bots/` | Legacy paper-trading bots, TradingView scout routines, and panel |
| `filter_model.py`, `kronos_brain.py`, `KRONOS_BRAIN.md` | `_archive/ml/` | Obsolete 13-feature ML filter and Kronos foundation model hook |
| `guard.py` | `_archive/guard.py` | Obsolete Chrome window monitor watchdog |

### 3.3 Legacy Test Files Relocated (to `_archive/tests/`)
39 legacy test files were moved out of `tests/` to `_archive/tests/`:
1. `tests/test_atrscan.py`
2. `tests/test_audit_fixes.py`
3. `tests/test_backup.py`
4. `tests/test_bitunix.py`
5. `tests/test_casestudy.py`
6. `tests/test_confidence.py`
7. `tests/test_dataset.py`
8. `tests/test_edges.py`
9. `tests/test_entry_score.py`
10. `tests/test_failures.py`
11. `tests/test_filter.py`
12. `tests/test_funnel.py`
13. `tests/test_geometry.py`
14. `tests/test_guard.py`
15. `tests/test_journal.py`
16. `tests/test_lifecycle.py`
17. `tests/test_live.py`
18. `tests/test_pace.py`
19. `tests/test_panel_autopilot.py`
20. `tests/test_panel_mobile_app.py`
21. `tests/test_paper_venue.py`
22. `tests/test_patterns_entries.py`
23. `tests/test_perch.py`
24. `tests/test_perch_holdable.py`
25. `tests/test_persistence.py`
26. `tests/test_recycle.py`
27. `tests/test_replay.py`
28. `tests/test_risk_controls.py`
29. `tests/test_riskanalysis.py`
30. `tests/test_sampler.py`
31. `tests/test_scanner.py`
32. `tests/test_scout.py`
33. `tests/test_second_book.py`
34. `tests/test_selector.py`
35. `tests/test_semantics.py`
36. `tests/test_shapes.py`
37. `tests/test_soak.py`
38. `tests/test_sweep.py`
39. `tests/test_trophy_lock.py`

---

## 4. Test Infrastructure Optimization

Created `pytest.ini` at repository root:
```ini
[pytest]
testpaths = tests
norecursedirs = _archive .* build dist *.egg-info scalper
python_files = test_*.py
```
This guarantees that invoking `pytest` runs only active tests in `tests/` without attempting to discover archived or experimental legacy test subtrees.

---

## 5. Clean Active Repository Layout

```
TBT-Engine/
├── engine/
│   ├── __init__.py
│   ├── execution_router.py    # Hyperliquid CLOB order router, 5-slice spam, detached stop
│   ├── fsm.py                 # FSM lifecycle & daily drawdown killswitch (decoupled)
│   ├── guard.py               # Watchdog daemon for systemd & RAM ceiling (<= 2.5 GB)
│   ├── killzone.py            # Multitz institutional session calculations
│   └── monte_carlo_scaling.py # Statistical Monte Carlo risk modeling
├── macro/
│   ├── __init__.py
│   ├── slm_intuition.py       # Async llama.cpp GBNF client & economic calendar monitor
│   └── self_healing.py        # Systemd healing & ntfy.sh high-priority alert integration
├── deploy/
│   ├── install_llama.sh       # VPS llama.cpp setup script
│   ├── relapse-scalper.service
│   ├── stratton-llm-critic.service
│   ├── relapse-watchdog.service
│   └── relapse-watchdog.timer
├── tests/
│   ├── conftest.py
│   ├── fakes.py
│   ├── fixtures/
│   ├── harness.py
│   ├── market.py
│   ├── replay.py
│   ├── units.py
│   ├── test_gold_relapse_scalper.py
│   ├── test_gold_killzones_multitz.py
│   ├── test_scaling_simulation.py
│   ├── test_guard_watchdog.py
│   └── test_self_healing_and_ntfy.py
├── data/
│   └── state/
├── run_relapse_scalper.py
├── requirements.txt
├── pytest.ini
├── PROJECT.md
├── DEPLOY_SERVER.md
├── TEST_INFRA.md
├── README.md
└── _archive/                  # [R6] 2,038 Quarantined legacy files
```

---

## 6. Verification & Test Evidence

Command executed:
```bash
pytest tests/test_gold_relapse_scalper.py tests/test_scaling_simulation.py tests/test_gold_killzones_multitz.py tests/test_guard_watchdog.py tests/test_self_healing_and_ntfy.py
```

Output:
```
============================= test session starts ==============================
platform darwin -- Python 3.11.0, pytest-7.4.3, pluggy-1.6.0
rootdir: /Users/mac/Desktop/TBT-Engine
configfile: pytest.ini
plugins: cov-6.2.1, anyio-3.7.1, dash-2.14.2
collecting ... collecting 13 items                                                            collected 57 items                                                             

tests/test_gold_relapse_scalper.py .............                         [ 22%]
tests/test_scaling_simulation.py .................                       [ 52%]
tests/test_gold_killzones_multitz.py ......                              [ 63%]
tests/test_guard_watchdog.py ..............                              [ 87%]
tests/test_self_healing_and_ntfy.py .......                              [100%]

============================== 57 passed in 2.87s ==============================
```

Bare `pytest` execution also collected and passed 57/57 items in 3.23s.
Zero regressions. 100% clean workspace.
