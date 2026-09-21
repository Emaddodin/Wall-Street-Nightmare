# BRIEFING — 2026-09-16T20:47:00Z

## Mission
Implement the 5 deterministic candidate alpha setups and central AlphaEngine in quant/hft/alpha/ with high-integrity mathematical models, stop-loss invalidation logic, and comprehensive unit tests.

## 🔒 My Identity
- Archetype: worker_m2_alpha
- Roles: implementer, qa, specialist
- Working directory: /Users/mac/Desktop/TBT-Engine/.agents/worker_m2_alpha
- Original parent: 68106074-9b56-4a6f-a92f-8aef4206f858
- Milestone: Milestone 2: R2 (Alpha Setups & AlphaEngine)

## 🔒 Key Constraints
- DO NOT CHEAT: No hardcoded test results, facade implementations, or circumventing genuine logic. Real state and real behavior.
- Exclusive file ownership: quant/hft/alpha/__init__.py, quant/hft/alpha/setups.py, quant/hft/alpha/alpha_engine.py, and tests/test_alpha_setups.py.
- Setup 1 (OFI_VWAP_Reversion): ±2.5σ VWAP band AND |z_OFI| > 0.8σ (Short if >= +2.5σ and z_OFI < -0.8; Long if <= -2.5σ and z_OFI > +0.8). SL: exactly 0.35% from entry.
- Setup 2 (Liquidation_Cascade_Absorption): Detected via OIMonitor (OI drop > 2.5% in < 300 ticks, Mark drop > 1.5%, taker sell > 85%). Absorption confirmed when taker sell drops or bid OFI emerges. Invalidation SL: strictly at cascade_low - tick_size.
- Setup 3 (Hawkes_Volatility_Breakout): Breaches local 100-bar high (long) or low (short) with Hawkes trade arrival intensity > 3x rolling median (ER > 3.0). Trailing SL: Dynamic ratchet behind Hull Moving Average HMA(9).
- Setup 4 (CVD_Divergence_Sweep): 10-bar price lower-low vs CVD higher-low with relative volume > 1.5x (bullish sweep -> Long), or price higher-high vs CVD lower-high (bearish sweep -> Short). Invalidation SL: strictly below/above sweep wick extreme.
- Setup 5 (L2_Depth_Imbalance_Scalp): Top 1% LOB bids > 5x asks within 0.1% of cluster price -> Long (or asks > 5x bids -> Short). Invalidation Hard SL: strictly placed just below/above depth cluster.
- CandidateSignal must have: setup_name: str, symbol: str, direction: str, entry_price: float, hard_sl: float, size_multiplier: float, feature_vector: np.ndarray (256-dim), timestamp: float.
- Clean exports in quant/hft/alpha/__init__.py.
- 100% passing tests with zero regressions.

## Current Parent
- Conversation ID: 68106074-9b56-4a6f-a92f-8aef4206f858
- Updated: 2026-09-16T20:47:00Z

## Task Summary
- **What to build**: 5 candidate alpha setups and AlphaEngine in `quant/hft/alpha/` plus tests in `tests/test_alpha_setups.py`.
- **Success criteria**: Full mathematical rigor, proper integration with `quant/hft/data_layer/`, complete tests, 0 regressions.
- **Interface contracts**: PROJECT.md, ORIGINAL_REQUEST.md, survey_r1_r2.md.
- **Code layout**: quant/hft/alpha/{__init__.py, setups.py, alpha_engine.py}

## Key Decisions Made
- Implemented `OFI_VWAP_Reversion` with rolling VWAP + variance calculation, sign reversal confirmation, and exact 0.35% hard stop.
- Implemented `Liquidation_Cascade_Absorption` integrating `OIMonitor`, detecting forced unwinding followed by absorption confirmation (taker sell drop, bid OFI surge, or price stabilization), placing hard stop strictly at `cascade_low - tick_size`.
- Implemented `Hawkes_Volatility_Breakout` integrating `HawkesProcess`, local 100-bar extreme breaches, and monotonically ratcheting trailing SL behind HMA(9).
- Implemented `CVD_Divergence_Sweep` integrating `CVDDivergenceDetector`, 10-bar price/CVD sweeps, and hard stop strictly at `sweep_wick +/- tick_size`.
- Implemented `L2_Depth_Imbalance_Scalp` integrating `L2DepthImbalanceEstimator`, 99th percentile depth walls within 0.1% of mid, and hard stop strictly behind cluster.
- Implemented `build_256d_feature_vector` in `alpha_engine.py` producing guaranteed finite 256-dim embedding with microstructural features, multi-horizon momentum, bar series, LOB depth levels, and Fourier/spectral density statistics.
- Built comprehensive 25-test unit suite in `tests/test_alpha_setups.py` with 100% pass rate.

## Artifact Index
- /Users/mac/Desktop/TBT-Engine/.agents/worker_m2_alpha/DISPATCH.md — Assignment instructions
- /Users/mac/Desktop/TBT-Engine/.agents/worker_m2_alpha/BRIEFING.md — Persistent situational awareness
- /Users/mac/Desktop/TBT-Engine/.agents/worker_m2_alpha/progress.md — Execution heartbeat
- /Users/mac/Desktop/TBT-Engine/.agents/worker_m2_alpha/handoff.md — Final self-contained handoff report

## Change Tracker
- **Files modified**:
  - `quant/hft/alpha/setups.py`: All 5 deterministic setups + math helpers (WMA, HMA9, VWAP bands)
  - `quant/hft/alpha/alpha_engine.py`: CandidateSignal, MarketState, build_256d_feature_vector, AlphaEngine coordinator
  - `quant/hft/alpha/__init__.py`: Clean exports of all setups, signals, and engine
  - `tests/test_alpha_setups.py`: 25 comprehensive unit tests
- **Build status**: All tests pass (25/25 in test_alpha_setups.py, 56/56 combined)
- **Pending issues**: None

## Quality Status
- **Build/test result**: PASS (56 passed in 2.34s)
- **Coverage**: quant/hft/alpha/__init__.py (100%), setups.py (92%), alpha_engine.py (83%)
- **Lint status**: Clean (ruff check: All checks passed!)
- **Tests added/modified**: 25 new unit tests in tests/test_alpha_setups.py

## Loaded Skills
- None specified in dispatch prompt.
