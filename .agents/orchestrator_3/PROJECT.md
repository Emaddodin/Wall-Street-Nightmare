# Project: Hyper Predator Bot & Decade Backtester

## Architecture
Decoupled dual-core asynchronous architecture:
- **Core 1 (Background Brain)**: Independent background loop `update_macro_edge()`, polling local `llama.cpp` every 5m via HTTP, strictly validated GBNF JSON output, updating thread-safe atomic `MACRO_STATE` (`permit_trade`, `bias`, `volatility_regime`, `last_updated`). Sub-500ms timeout with safe fallback.
- **Core 2 (High-Frequency Sniper Engine)**: Sub-50ms execution decision loop. Hyperliquid L1 orderbook WS + rolling M5 S/R pivot tracking. Evaluates M1 retest wick math (>= 65% rejection wick, body closes in direction of bias) and final 5-second tick velocity surge (>= 1.5x rolling baseline).
- **Execution Bridge (`spam_orders`)**: Concurrently dispatches 5 micro-slices with 20ms jitter stagger via `asyncio.gather` at 100x leverage on `GOLD` with open-ended entries. Detached stop-loss ($1.00 beyond invalidation wick extreme, `reduce_only=True`) placed immediately after entry fills.
- **Dynamic Ruthless Exits & Hard Equity Shield**:
  - Target Exit: Instant market close when live bid/ask touches opposing M5 S/R zone.
  - Reversal Exit: Instant market close on opposing >= 65% M1 rejection wick.
  - Hard Equity Shield: Instant parallel market close if basket uPnL reaches -$10.00.
- **Micro-Structure Order Flow Edge (`orderflow_exit_monitor`)**:
  - Sub-5ms in-memory calculation from live Hyperliquid WebSocket feeds.
  - Top-5 L2 book imbalance ratio > 3.0 * volatility_regime triggers instant basket liquidation.
  - Trade tape volume delta stall (> 80% opposing fills in last 20 ticks for profitable basket) triggers instant exit.
- **Decade-Deep Vectorized Backtester (`backtester.py`)**:
  - Streaming chunked processing (100k bars/chunk, 1k overlap halo buffer) + `np.memmap` vectorization operating under 200 MB RSS (strictly within 4GB RAM envelope).
  - Parameter sweep interface (wick %, M5 S/R lookback, L2 imbalance, tick velocity).
  - 500+ Monte Carlo runs with 5-slice jitter, slippage, fees, and 5% daily DD killswitch.
- **Automated Test Suite (`tests/test_hyper_predator.py`)**:
  - Full requirement-driven offline test suite covering R1 to R8 without external network access.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | R6 Repository Purge | Move legacy MT5, obsolete scanners, old bots, unused test files to `_archive/` | M1 | survey |
| 2 | R6 Strict Asset Focus | Hardcode execution and risk exclusively for GOLD on Hyperliquid with zero multi-ticker overhead | M1 | survey |
| 3 | R1 Core 1 Macro Loop | Background `update_macro_edge()` polling llama.cpp every 5m with sub-500ms timeout fallback | M2 | survey |
| 4 | R1 Thread-Safe MACRO_STATE | In-memory atomic dataclass (`permit_trade`, `bias`, `volatility_regime`, `last_updated`) | M2 | survey |
| 5 | R2 Core 2 Sniper Engine | L1 orderbook WS subscription and rolling M5 S/R pivot calculation | M2 | survey |
| 6 | R2 M1 Retest Wick Math | Invalidation anchor and >= 65% rejection wick calculation with body closing in bias direction | M2 | survey |
| 7 | R2 Tick Velocity Edge | Final 5s volume/tick rate surge >= 1.5x rolling baseline | M2 | survey |
| 8 | R3 Layered Order Slicing | `spam_orders` 5 micro-slices concurrently with 20ms jitter stagger via `asyncio.gather` at 100x leverage on GOLD | M2 | survey |
| 9 | R3 Detached Stop-Loss | Resting `exchange.market_close(reduce_only=True)` placed exactly $1.00 beyond invalidation wick | M2 | survey |
| 10 | R4 Opposing Target Exit | Instant market close at next immediate opposing M5 S/R zone | M2 | survey |
| 11 | R4 Reversal Wick Exit | Instant market close on opposing >= 65% M1 rejection wick | M2 | survey |
| 12 | R4 Hard Equity Shield | Instant basket close on floating uPnL <= -$10.00 | M2 | survey |
| 13 | R5 Top-5 L2 Imbalance Exit | Sub-5ms memory exit when top-5 L2 imbalance ratio > 3.0 * volatility_regime | M2 | survey |
| 14 | R5 Trade Tape Delta Stall | Sub-5ms exit when in profitable basket and > 80% of last 20 trade ticks are opposing fills | M2 | survey |
| 15 | R7 Vectorized Streaming Backtester | Chunked streaming (100k bars, 1k halo) + `np.memmap` vectorization under 200 MB RAM | M3 | survey |
| 16 | R7 Parameter Sweep Engine | Multi-parameter grid search (wick %, M5 S/R lookback, L2 imbalance, tick velocity) | M3 | survey |
| 17 | R7 Monte Carlo Engine | 500+ runs with 5-slice jitter, slippage, fees, 5% daily DD killswitch, and percentile metrics | M3 | survey |
| 18 | R8 Automated Unit & E2E Tests | 44 tests in `tests/test_hyper_predator.py` verifying all R1-R7 criteria offline | M4 | survey |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | M1: Repository Purge & Strict Asset Focus | Purge legacy MT5 and obsolete files into `_archive/`; clean project workspace | none | DONE |
| 2 | M2: Hyper Predator Bot Engine (`hyper_predator_bot.py`) | Implement R1-R5: Core 1 Macro loop, Core 2 Sniper, `spam_orders`, dynamic exits, L2/tape order flow | M1 | DONE |
| 3 | M3: Vectorized Backtester & Sweeper (`backtester.py`) | Implement R7: 4GB RAM streaming chunked vectorization, synthetic generator, sweep, Monte Carlo | M1 | DONE |
| 4 | M4: Automated Test Suite & Dual Track Verification | Implement R8 `tests/test_hyper_predator.py`, verify 100% tests pass, review, challenge, and audit | M2, M3 | DONE |

## Code Layout
- Active Source:
  - `hyper_predator_bot.py`: Complete decoupled dual-core scalper engine (1,426 lines).
  - `backtester.py`: Decade-deep streaming vectorized backtester and Monte Carlo simulator (1,623 lines).
  - `engine/execution_router.py`: Underlying execution router and mock venues.
  - `tests/test_hyper_predator.py`: Dedicated automated test suite (44 tests).
  - `tests/test_adversarial_predator_stress.py`: Adversarial stress harness (10 tests).
  - `tests/stress_backtester.py`: Backtester memory & Monte Carlo stress harness.
- Archive:
  - `_archive/`: Legacy MT5 files, multi-asset scanners, obsolete bots, and legacy tests (2,038 files).
