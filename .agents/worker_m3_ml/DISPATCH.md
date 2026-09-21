## 2026-09-16T20:14:19Z
You are the ML and Memory Worker for Wall-Street-Nightmare Architecture B (Milestone 3: R3 & part of R5).
Working directory: /Users/mac/Desktop/TBT-Engine/.agents/worker_m3_ml
Authoritative User Request: Read /Users/mac/Desktop/TBT-Engine/ORIGINAL_REQUEST.md before starting work.
Project Plan: Read /Users/mac/Desktop/TBT-Engine/PROJECT.md and survey findings in /Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_2/survey_r3_r5.md.

DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Exclusive file ownership:
- filter_model.py
- quant/hft/memory/__init__.py
- quant/hft/memory/temporal_memory.py
- quant/hft/memory/mae_evaluator.py
- quant/hft/cpcv.py

Your mission:
Implement R3 (ML Filter Engine & 256-Dim RAG Temporal Memory) and R5 CPCV:
1. `filter_model.py`: Evolve `FilterModel` into a polymorphic engine:
   - Must preserve 100% backward compatibility for legacy `.npz` artifacts (w, b, mu, sd, med, threshold, trained_at, n) and pass all tests in `tests/test_filter.py` with zero regressions.
   - Support loading CatBoost models (`.cbm` via `catboost.CatBoostClassifier`) and LightGBM models (`.txt` / `.json` or pure-numpy fallback if lightgbm is absent).
   - Implement optimized vectorized scoring that runs in sub-10ms (actual expectation < 1ms).
2. `quant/hft/memory/temporal_memory.py`: Implement 256-dimensional vector embedding temporal memory system:
   - Provide `TemporalMemoryBackend` base interface.
   - Implement `InMemoryCosineMemory` (pure NumPy cosine similarity using normalized dot-product, storing vectors, metadata, MAE, outcome).
   - Implement `QdrantMemoryBackend` with automatic fallback to `InMemoryCosineMemory` if `qdrant_client` is not installed or daemon is unavailable.
   - Methods: `add_trade(embedding, trade_metadata)`, `query_similar(embedding, top_k=20)`.
3. `quant/hft/memory/mae_evaluator.py`: Implement `ExpectedMAEEvaluator`:
   - Queries temporal memory for top-K historical setups given a 256-dim embedding.
   - Computes expected MAE (mean / P90 adverse excursion) and historical win rate.
   - Implements hard veto gate: returns `is_vetoed=True` if `expected_mae >= proposed_hard_sl`.
4. `quant/hft/cpcv.py`: Implement Combinatorial Purged Cross-Validation (`CombinatorialPurgedKFold`):
   - Generates $\binom{N}{k}$ combinatorial train/test group splits.
   - Purges training observations whose event label spans overlap test group prediction spans.
   - Applies post-test embargoing buffer to prevent serial correlation leakage.
5. Verify your changes by running `pytest tests/test_filter.py` (all 6 legacy tests MUST pass) and testing your new modules.
6. Write a comprehensive handoff report to handoff.md in your working directory and send a completion message to the parent.
