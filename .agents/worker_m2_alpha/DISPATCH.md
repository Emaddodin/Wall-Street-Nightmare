## 2026-09-16T20:34:00Z
Assignment: Alpha Setups Worker for Wall-Street-Nightmare Architecture B (Milestone 2: R2).
Working directory: /Users/mac/Desktop/TBT-Engine/.agents/worker_m2_alpha
Authoritative User Request: Read /Users/mac/Desktop/TBT-Engine/ORIGINAL_REQUEST.md
Project Plan: Read /Users/mac/Desktop/TBT-Engine/PROJECT.md and survey findings in /Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_1/survey_r1_r2.md.
Exclusive file ownership:
- quant/hft/alpha/__init__.py
- quant/hft/alpha/setups.py
- quant/hft/alpha/alpha_engine.py

Mission:
Implement the 5 deterministic candidate alpha setups and the central AlphaEngine in quant/hft/alpha/:
1. quant/hft/alpha/setups.py:
   - Setup 1: OFI_VWAP_Reversion:
     * Entry: Price at ±2.5σ VWAP band AND OFI sign reversal |z_OFI| > 0.8σ (Short if price >= +2.5σ and z_OFI < -0.8; Long if price <= -2.5σ and z_OFI > +0.8).
     * Hard SL: exactly 0.35% from entry price (entry * (1 - 0.0035) for long, entry * (1 + 0.0035) for short).
   - Setup 2: Liquidation_Cascade_Absorption:
     * Entry: Detected via OIMonitor (OI drop > 2.5% in < 300 ticks, Mark drop > 1.5%, taker sell > 85%). Absorption confirmed when taker sell drops or bid OFI emerges.
     * Invalidation SL: strictly at the cascade extreme low (cascade_low - tick_size).
   - Setup 3: Hawkes_Volatility_Breakout:
     * Entry: Price breaches local 100-bar high (long) or low (short) with Hawkes trade arrival intensity > 3x rolling median (ER > 3.0).
     * Trailing SL: Dynamic ratchet behind Hull Moving Average HMA(9).
   - Setup 4: CVD_Divergence_Sweep:
     * Entry: 10-bar price lower-low vs CVD higher-low with relative volume > 1.5x (bullish sweep -> Long), or price higher-high vs CVD lower-high (bearish sweep -> Short).
     * Invalidation SL: strictly below the sweep wick extreme.
   - Setup 5: L2_Depth_Imbalance_Scalp:
     * Entry: Top 1% LOB bids > 5x asks within 0.1% of cluster price -> Long (or asks > 5x bids -> Short).
     * Invalidation Hard SL: strictly placed just below/above the depth cluster.
2. quant/hft/alpha/alpha_engine.py:
   - Coordinates the 5 setups on incoming market updates/bars/ticks.
   - Generates standardized CandidateSignal objects:
     setup_name: str, symbol: str, direction: str, entry_price: float, hard_sl: float, size_multiplier: float, feature_vector: np.ndarray (256-dim), timestamp: float.
3. quant/hft/alpha/__init__.py: Clean exports of all setups, CandidateSignal, and AlphaEngine.
4. Implement a comprehensive unit test suite in tests/test_alpha_setups.py verifying mathematical entries, stop losses, invalidations, and signal generation for all 5 setups.
5. Run pytest and verify 100% passing tests with zero regressions.
6. Write a comprehensive handoff report to handoff.md in your working directory and send a completion message to the parent.
