# Master Plan - orchestrator_3

## Objective
Deliver `hyper_predator_bot.py`, `backtester.py`, `tests/test_hyper_predator.py`, and repo purge according to R1-R8.

## Strategy: Project Pattern
1. **Survey (Step 0)**:
   - Dispatch 3 Explorers (including spec mining/repo structure analysis) to analyze:
     - Explorer 1: Hyperliquid Python SDK usage, existing execution routers (`engine/execution_router.py`, `live_hyperliquid.py`), WebSocket connections (L1 orderbook, L2 book, trades streams).
     - Explorer 2: Existing PA/ICT math, candle structures, S/R pivots, and legacy codebase structure to identify files for R6 purge into `_archive/`.
     - Explorer 3: Existing backtester code or datasets, pandas/numpy vectorization constraints, and existing test patterns.
2. **Decomposition & Architecture**:
   - Create `PROJECT.md` with Feature Inventory, Milestones, and Interface Contracts.
   - Create `TEST_INFRA.md` for test coverage strategy.
3. **Execution Tracks**:
   - Purge legacy MT5 and obsolete components into `_archive/` (R6).
   - Implement `hyper_predator_bot.py` (R1-R5).
   - Implement `backtester.py` (R7).
   - Implement `tests/test_hyper_predator.py` (R8).
4. **Verification & Audit**:
   - Dispatch Reviewers, Challengers, and Forensic Integrity Auditor.
   - Strictly verify all acceptance criteria and pass gate.
