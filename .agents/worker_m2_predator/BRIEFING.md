# BRIEFING — 2026-09-19T09:15:00Z

## Mission
Build `hyper_predator_bot.py`: an ultra-aggressive, high-frequency M1 scalper for Hyperliquid DEX (GOLD only) implementing R1-R6 with dual-core decoupled architecture, order slicing, ruthless exits, order flow edge, and offline verification support.

## 🔒 My Identity
- Archetype: worker
- Roles: implementer, qa, specialist
- Working directory: /Users/mac/Desktop/TBT-Engine/.agents/worker_m2_predator
- Original parent: 39ebbf67-6c24-4133-888f-b0d9bed66dab
- Milestone: Milestone 2 (Hyper Predator Engine)

## 🔒 Key Constraints
- Exclusive write ownership of `hyper_predator_bot.py`.
- DO NOT modify `backtester.py`.
- Confine metadata to `/Users/mac/Desktop/TBT-Engine/.agents/worker_m2_predator`.
- Hardcode asset exclusively for `GOLD` on Hyperliquid (zero multi-ticker overhead).
- Sub-500ms timeout on macro edge with safe fallback / algorithmic hold.
- Extreme rejection wick math >= 65% of total high-low range closing in bias direction.
- Tick velocity edge >= 1.5x rolling baseline in final 5s of M1 candle.
- 5-slice micro-order spamming via asyncio.gather with 20ms jitter at 100x leverage.
- Detached Stop-Loss $1.00 beyond wick extreme.
- Opposing M5 S/R target exit, opposing 65% wick reversal exit, and -$10.00 hard equity shield exit.
- Microstructure L2 imbalance exit (> 3.0 * volatility_regime) & Volume Delta stall exit (> 80% opposing of last 20 trades when in profit).
- Dependency injection for simulated broker venue and offline deterministic verification.
- Genuine implementation with no mock/hardcoded cheats.

## Current Parent
- Conversation ID: 39ebbf67-6c24-4133-888f-b0d9bed66dab
- Updated: 2026-09-19T09:15:00Z

## Task Summary
- **What to build**: `hyper_predator_bot.py`
- **Success criteria**: Genuine complete implementation satisfying R1 to R6, clean modular architecture, full syntax & pytest passing.
- **Interface contracts**: /Users/mac/Desktop/TBT-Engine/.agents/orchestrator_3/PROJECT.md
- **Code layout**: Root repo (`hyper_predator_bot.py`)

## Key Decisions Made
- Built modular architecture with classes: `MacroState`, `MacroStateManager`, `TapeBookMemory`, `SniperEngine`, `ExecutionBridge`, `OrderflowMonitor`, `HyperPredatorBot`.
- Exported module-level functions `spam_orders`, `close_basket`, `orderflow_exit_monitor` matching interface contracts.
- Implemented sub-500ms timeout on Core 1 macro polling loop with fallback to safe hold (`permit_trade=False`).
- Implemented exact M1 rejection wick calculation: `(min(o, c) - lo) / (h - lo) >= 0.65` (bullish) and `(h - max(o, c)) / (h - lo) >= 0.65` (bearish).
- Implemented tick velocity surge: final 5s rate >= 1.5x baseline rate.
- Implemented 5-slice `spam_orders` with 20ms jitter stagger via `asyncio.gather` at 100x leverage on GOLD.
- Implemented detached stop-loss placed exactly $1.00 beyond invalidation wick extreme with `reduce_only=True`.
- Implemented 5 dynamic exit conditions: Hard Equity Shield (-$10.00 uPnL), Opposing M5 S/R target, Opposing 65% M1 reversal wick, L2 top-5 imbalance wall (> 3.0 * volatility_regime), and Volume Delta stall (> 80% opposing taker trades).
- All 57 unit tests in project pass in 3.28s. Flake8 linter clean. E2E lifecycle verified.

## Artifact Index
- `/Users/mac/Desktop/TBT-Engine/hyper_predator_bot.py` — Complete predator engine
- `/Users/mac/Desktop/TBT-Engine/.agents/worker_m2_predator/DISPATCH.md` — Assignment instructions
- `/Users/mac/Desktop/TBT-Engine/.agents/worker_m2_predator/BRIEFING.md` — Persistent memory
- `/Users/mac/Desktop/TBT-Engine/.agents/worker_m2_predator/progress.md` — Liveness & step progress
- `/Users/mac/Desktop/TBT-Engine/.agents/worker_m2_predator/report.md` — Detailed technical report
- `/Users/mac/Desktop/TBT-Engine/.agents/worker_m2_predator/handoff.md` — 5-component handoff report

## Change Tracker
- **Files modified**: `hyper_predator_bot.py` (created and verified)
- **Build status**: PASS (syntax, flake8, pytest 57/57 passed, E2E lifecycle passed)
- **Pending issues**: None

## Quality Status
- **Build/test result**: PASS (57/57 tests passed in 3.28s + comprehensive standalone lifecycle test)
- **Lint status**: 0 errors (flake8 --ignore=E501)
- **Tests added/modified**: Verified all R1-R6 behaviors deterministically

## Loaded Skills
- None
