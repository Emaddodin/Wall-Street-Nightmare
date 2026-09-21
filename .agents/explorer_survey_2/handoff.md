# Handoff Report: Architecture & Purge Exploration
**Agent**: Explorer 2 (Architecture & Purge Explorer)  
**Recipient**: Orchestrator (`orchestrator_3` / `39ebbf67-6c24-4133-888f-b0d9bed66dab`)  
**Type**: Hard Handoff  
**Working Directory**: `/Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_2`  
**Reference Document**: `/Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_2/survey_report.md`  

---

## 1. Observation
1. **Repository Inventory**:
   - Total files surveyed: 2,100+ files across 19 top-level subdirectories.
   - Files identified for R6 purge into `_archive/`: **1,994 files totaling 27.95 MB**.
   - Specific purge directories:
     - `open-antigravity-patcher/`: 1,492 files, 20.48 MB (Desktop IDE utility).
     - `scratch/`: 266 files, 4.74 MB (Duplicate snapshot copy of repo in `scratch/audit_snapshot/`).
     - `tools/`: 73 files, 0.52 MB (Obsolete Chrome recycling and TradingView scrapers).
     - `quant/`: 56 files, 0.35 MB (Multi-alt Bitunix research).
     - `services/`: 34 files, 0.03 MB (Legacy Chrome/VNC systemd units).
     - `dataset/`: 22 files, 0.37 MB (Multi-asset ML dataset generators).
     - `old/`: 11 files, 0.42 MB (Tarballs: `dead-code-*.tar.gz`, `removed-*.tar.gz`).
     - `catboost_info/`: 6 files, 0.02 MB (Obsolete ML training logs).
     - `pine/`: 1 file, 0.05 MB (`Stratton_Oakmont_Sniper.pine`).
   - Root files identified for purge:
     - Scanners: `atrscan.py` (10.9 KB), `boom2.py` (23.5 KB), `hunt.py` (8.7 KB), `hunt_paper.py` (15.1 KB), `pace.py` (6.0 KB), `rank3.py` (4.2 KB), `screen.py` (2.7 KB), `signal_report.py` (7.4 KB).
     - Obsolete bots: `papertrade.py` (270.5 KB), `scout.py` (32.5 KB), `perch.py` (6.2 KB), `recorder.py` (13.1 KB), `panel.py` (84.3 KB), `paper_venue.py` (4.6 KB), `journal.py` (9.3 KB), `live_hyperliquid.py` (149.1 KB).
     - Connectors & models: `exchange/bitunix.py` (28.1 KB), `signals/tv_cdp.py` (21.8 KB), `filter_model.py` (15.2 KB), `kronos_brain.py` (2.2 KB), `guard.py` (30.7 KB).
     - Root clutter: `not` (0 bytes), `would` (329 bytes), `bot_execution.log` (16.4 KB), `*.bak` (3 files, 235 KB), `guard.py.bak-res` (15.4 KB), `icon-*.png` (5 files, 14.3 KB).

2. **Existing Price Action Math**:
   - `scalper/pa/candles.py` (lines 16–22): Implements `_wick_parts()` calculating `rng = (h - l)`, `body = (c - o).abs()`, `upper = h - max(c, o)`, `lower = min(c, o) - l`.
   - `scalper/pa/levels.py` (lines 13–28): Implements lookahead-free rolling levels via `.shift(1).rolling(window).max()` / `.min()`.
   - `scalper/surge.py` (lines 45–56): Demonstrates volume surge z-score gating.
   - `engine/execution_router.py` (lines 870–1049): Implements `fire_layered_orders()` with `asyncio.gather` and 50ms stagger jitter, open-ended TP, and detached `market_close` stop order with `reduce_only=True`.
   - `engine/execution_router.py` (lines 1183–1250): Implements `close_basket()` with `market_close(reduce_only=True)` and cancellation of resting detached stop.

3. **Current Test Suite Health**:
   - Executed: `pytest tests/test_gold_relapse_scalper.py tests/test_gold_killzones_multitz.py tests/test_scaling_simulation.py tests/test_guard_watchdog.py tests/test_self_healing_and_ntfy.py`
   - Result: **57 passed in 3.23s** with zero failures.

4. **Dependency Coupling in Active Code**:
   - `engine/fsm.py` line 69 imports `DAY_MS` from `quant.engine.guards`.
   - `engine/fsm.py` line 76 imports `candles, ict` from `scalper.pa`.

---

## 2. Logic Chain
1. *From Observation 1*: The repository contains 1,994 files that belong to obsolete trading architectures (Bitunix, TradingView Chrome scraping, multi-coin scanning). Retaining them in the active tree adds clutter, slows searches, and creates confusion. Therefore, quarantining them into `_archive/` satisfies R6 without risking permanent data loss.
2. *From Observation 2*: The core math for wicks and rolling levels exists in `scalper/pa/candles.py` and `scalper/pa/levels.py`. The formula required for R2 is:
   - Wick ratio $\rho = \frac{\min(O, C) - L}{H - L} \ge 0.65$ with $C > O$ for Long; $\rho = \frac{H - \max(O, C)}{H - L} \ge 0.65$ with $C < O$ for Short.
   - Final 5s tick surge $\Lambda_{tick} = \frac{N_{final5} / 5.0}{\max(N_{base} / 55.0, 0.1)} \ge 1.5$.
3. *From Observation 2 & Requirement R4*: `engine/execution_router.py` already possesses verified dynamic liquidation mechanisms (`close_basket`) with `reduce_only=True`. Extending this to check opposing M5 S/R, opposing $\ge 65\%$ M1 rejection wick, and floating PnL $\le -\$10.00$ directly leverages the proven execution path.
4. *From Observation 2 & Requirement R5*: Hyperliquid publishes `l2Book` and `trades` via WebSocket. Evaluating the top 5 levels ($V_{ask}^{(5)} / V_{bid}^{(5)} > 3.0 \cdot \text{volatility\_regime}$) and the last 20 trade ticks ($> 80\%$ opposing fills) against in-memory arrays requires $< 5 \ \mu\text{s}$, well within the sub-5ms requirement.
5. *From Observation 4*: Before purging `quant/` and `scalper/`, `engine/fsm.py` must have `DAY_MS = 86_400_000` defined locally, and `scalper/pa/` must either be preserved or copied into `engine/pa/` to prevent breaking the 57 passing tests.

---

## 3. Caveats
- **Live WebSocket Data**: The Hyperliquid L2 book stream and trades stream were verified via the SDK's types and `WebsocketManager`, but live latency during extreme market volatility depends on VPS network proximity to Hyperliquid validators.
- **Transitional Preservation**: While R6 mandates purging unused code, `scalper/pa/` contains pure math referenced by `engine/fsm.py`. It is recommended to either retain `scalper/pa/` or copy `candles.py` and `levels.py` to `engine/pa/` prior to archiving `scalper/`.
- **Read-Only Scope**: In accordance with explorer constraints, no files were moved, modified, or deleted during this investigation.

---

## 4. Conclusion
1. **Purge Readiness**: An exact catalog of 1,994 files (27.95 MB) across 9 directories and 32 root/script files is prepared for moving to `_archive/`.
2. **Mathematical Edge**: Full deterministic mathematical models for R2 (M5 S/R, M1 65% wick, 5s tick surge), R4 (target exit, reversal exit, -$10 shield), and R5 (top-5 L2 imbalance, tape delta stall) are formulated and documented in `survey_report.md`.
3. **Architecture Blueprint**: A decoupled dual-core design (Core 1: async 5m LLM macro brain with GBNF grammar; Core 2: sub-50ms sniper with 5-slice spam and sub-5ms order flow exit monitor) is fully specified for `hyper_predator_bot.py`.
4. **Baseline Invariance**: The 57 unit tests in the active gold test suite remain 100% green and serve as the foundation for testing `hyper_predator_bot.py`.

---

## 5. Verification Method
To independently verify the observations and conclusions in this report:

1. **Verify Baseline Test Suite**:
   ```bash
   pytest tests/test_gold_relapse_scalper.py \
          tests/test_gold_killzones_multitz.py \
          tests/test_scaling_simulation.py \
          tests/test_guard_watchdog.py \
          tests/test_self_healing_and_ntfy.py
   ```
   *Expected Output*: `57 passed in ~3.23s`.

2. **Verify Purge Statistics & Paths**:
   Run the survey script documented in `survey_report.md` §2.2:
   ```bash
   python3 -c "
   import os
   purge_dirs = ['catboost_info', 'open-antigravity-patcher', 'scratch', 'old', 'services', 'pine', 'tools', 'dataset', 'quant']
   print('Total purge directories exist:', all(os.path.exists(d) for d in purge_dirs))
   "
   ```
   *Expected Output*: `Total purge directories exist: True`.

3. **Inspect Specification Artifacts**:
   - Detailed survey and mathematical formulas: `cat .agents/explorer_survey_2/survey_report.md`
   - Progress heartbeat: `cat .agents/explorer_survey_2/progress.md`
