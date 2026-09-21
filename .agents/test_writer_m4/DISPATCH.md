## 2026-09-19T09:18:00Z
You are Test Writer M4 (Dedicated Test Suite Writer).
Your working directory is: /Users/mac/Desktop/TBT-Engine/.agents/test_writer_m4
Exclusive file ownership: /Users/mac/Desktop/TBT-Engine/tests/test_hyper_predator.py

MANDATORY INPUT:
Read /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md (specifically requirement R8 and all acceptance criteria under ## 2026-09-19T08:56:25Z).
Read /Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_3/survey_report.md (detailed test blueprint and specifications in Section 7 & 8).
Read /Users/mac/Desktop/TBT-Engine/.agents/orchestrator_3/TEST_INFRA.md.
Read /Users/mac/Desktop/TBT-Engine/hyper_predator_bot.py.
Read /Users/mac/Desktop/TBT-Engine/backtester.py.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

CRITICAL NETWORK CONSTRAINT:
In tests/conftest.py, the _no_network fixture blocks all live network calls. Every network call (Hyperliquid WebSockets, HTTP requests to llama.cpp) MUST be mocked deterministically using unittest.mock (AsyncMock, patch, etc.).

OBJECTIVE:
Author the complete automated test suite `tests/test_hyper_predator.py` containing at least 32-37 comprehensive test cases organized into 8 distinct test classes covering R1 to R8:
1. `TestMacroBrainCore1`:
   - `test_macro_state_dataclass_defaults`
   - `test_llm_json_parsing_valid_bullish`
   - `test_llm_json_parsing_valid_bearish`
   - `test_llm_json_parsing_markdown_codeblock_fallback`
   - `test_llm_json_parsing_malformed_syntax_fallback`
   - `test_update_macro_edge_timeout_non_blocking` (assert sub-500ms timeout fallback)
   - `test_macro_state_thread_safety`
2. `TestSniperCore2M1Signal`:
   - `test_rejection_wick_calculation_exact_65pct_bullish`
   - `test_rejection_wick_calculation_sub_65pct_rejection`
   - `test_rejection_wick_bearish_alignment`
   - `test_rejection_wick_zero_range_guard`
   - `test_m5_support_resistance_rolling_pivot`
   - `test_tick_velocity_edge_threshold` (assert >= 1.5x baseline)
   - `test_signal_trigger_full_confluence`
3. `TestSpamOrdersLayeredExecution`:
   - `test_spam_orders_5_slices_dispatched`
   - `test_spam_orders_20ms_jitter_stagger`
   - `test_detached_stop_placement_distance` (assert exactly $1.00 beyond invalidation wick with reduce_only=True)
   - `test_leverage_100x_and_margin_ceiling_20pct`
   - `test_open_ended_entry_no_static_tp`
4. `TestL2OrderflowExitEngine`:
   - `test_l2_top5_imbalance_calculation`
   - `test_l2_imbalance_exit_long_wall` (ratio > 3.0 * volatility_regime triggers close)
   - `test_l2_imbalance_exit_short_wall`
   - `test_l2_adaptive_regime_threshold`
   - `test_l2_orderflow_exit_latency_sub_5ms` (benchmark latency < 5ms)
5. `TestTradeTapeVolumeDeltaStall`:
   - `test_volume_delta_stall_long_opposing_ticks` (> 80% opposing in profitable basket)
   - `test_volume_delta_stall_short_opposing_ticks`
   - `test_volume_delta_stall_normal_flow_holds`
6. `TestRuthlessExitsAndHardEquityShield`:
   - `test_hard_equity_shield_liquidation_at_minus_10` (unrealized PnL <= -$10.00 triggers market close)
   - `test_opposing_reversal_wick_exit` (opposing >= 65% wick triggers exit)
   - `test_opposing_m5_sr_target_exit` (opposing M5 S/R touch triggers exit)
7. `TestVectorizedBacktester`:
   - `test_backtester_data_ingestion_formats` (synthetic, pandas, chunked)
   - `test_backtester_chunked_streaming_continuity` (100k bars, 1k halo buffer, stateful basket persistence)
   - `test_backtester_memory_footprint_under_4gb` (assert RSS < 200MB on 100k bars)
   - `test_backtester_signal_generation_consistency` (assert all 6 exit mechanisms work)
8. `TestParameterSweepAndMonteCarlo`:
   - `test_parameter_sweep_interface` (sweeps wick %, M5 lookback, L2 imbalance, tick velocity)
   - `test_monte_carlo_500_runs_execution` (runs 500 Monte Carlo runs with jitter and slippage)
   - `test_metrics_calculation_accuracy` (Sharpe, Max DD, Win Rate, Profit Factor)

VERIFICATION REQUIREMENTS:
Execute:
`pytest tests/test_hyper_predator.py -v`
Ensure 100% of tests pass cleanly. Also verify full test suite `pytest tests/` passes without regressions.

OUTPUT REQUIREMENTS:
Write report to `/Users/mac/Desktop/TBT-Engine/.agents/test_writer_m4/report.md` and handoff to `/Users/mac/Desktop/TBT-Engine/.agents/test_writer_m4/handoff.md`.
Send completion message to orchestrator_3.
