# E2E Test Infra: Hyper Predator Engine & Vectorized Backtester

## Test Philosophy
- Requirement-driven, opaque-box, and unit-level verification covering R1 to R8.
- Strictly adheres to the offline test isolation invariant (`tests/conftest.py` `_no_network`).
- All network interactions (Hyperliquid L1/L2 WebSockets, trades tape, order dispatch, and local LLM HTTP completion) are mocked deterministically.
- Methodology: Category-Partition + Boundary Value Analysis + Combinatorial + Real-World Workloads.

## Feature Inventory & Test Mapping
| # | Feature | Source | Tier 1 (Feature) | Tier 2 (Boundary) | Tier 3 (Interaction) | Tier 4 (Workload) |
|---|---------|--------|:----------------:|:-----------------:|:--------------------:|:-----------------:|
| 1 | R1 Macro Edge & State | R1 | 5 | 5 | ✓ | ✓ |
| 2 | R2 Sniper Math & Gates | R2 | 5 | 5 | ✓ | ✓ |
| 3 | R3 Order Slicing & Bridge | R3 | 5 | 5 | ✓ | ✓ |
| 4 | R4 Dynamic Exits & Shield | R4 | 5 | 5 | ✓ | ✓ |
| 5 | R5 Micro-Structure Exits | R5 | 5 | 5 | ✓ | ✓ |
| 6 | R6 Repo Purge & Asset Focus | R6 | 5 | 5 | ✓ | ✓ |
| 7 | R7 Vectorized Backtester | R7 | 5 | 5 | ✓ | ✓ |

## Coverage Thresholds
- Tier 1 (Feature Coverage): >= 5 test cases per feature area.
- Tier 2 (Boundary & Corner Cases): >= 5 test cases per feature area (rejection thresholds, exact $10 loss, exact 65% wick, 80% tape stall, etc.).
- Tier 3 (Cross-Feature Combinations): Pairwise interaction tests (e.g. macro regime scaling with L2 imbalance exit, rapid reversal wick while orderflow exit is active).
- Tier 4 (Real-World Application Scenarios): Full simulated trading day / session with synthetic WebSocket feed, multi-basket execution, and Monte Carlo stress tests.
- Total Target: >= 35 comprehensive automated tests in `tests/test_hyper_predator.py`.

## Test Runner
- Command: `pytest tests/test_hyper_predator.py -v`
- Pass criterion: 100% tests pass, zero unhandled exceptions, zero network calls.
