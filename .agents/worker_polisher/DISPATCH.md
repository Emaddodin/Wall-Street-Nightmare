## 2026-09-19T09:33:23Z

<USER_REQUEST>
You are Worker Polisher (Code Polish & Review Remediation Worker).
Your working directory is: /Users/mac/Desktop/TBT-Engine/.agents/worker_polisher

MANDATORY INPUT:
Read /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md.
Read /Users/mac/Desktop/TBT-Engine/.agents/reviewer_2/report.md (specifically Section 4: Key Findings & Actionable Recommendations).
Read /Users/mac/Desktop/TBT-Engine/hyper_predator_bot.py.
Read /Users/mac/Desktop/TBT-Engine/backtester.py.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

OBJECTIVE:
Address the 3 actionable findings identified by Reviewer 2:
1. In `hyper_predator_bot.py`:
   - In `ExecutionBridge.close_basket(coin="GOLD", reason="TARGET", exit_price: Optional[float] = None)`: If `exit_price` is provided and `hasattr(self.venue, "set_market_price")`, update `self.venue.set_market_price(coin, exit_price)` before calling `market_close`. In `OrderflowMonitor.evaluate()` and exit checks, pass current market/bid/ask price to `close_basket` so venue PnL reflection is always accurate.
   - In `SniperEngine.evaluate_m1_trigger`: When `tick_timestamps` is empty or None, ensure `velocity_surge_valid` defaults to `False` (gate entries strictly unless final 5s surge is demonstrated >= 1.5x baseline). If any existing tests omitted tick timestamps, ensure those test cases provide representative timestamps or verify that tests continue to pass 100%.
2. In `backtester.py`:
   - In `VectorizedSignalEngine.compute_signals`: Ensure `bull_body = close_arr > open_arr` and `bear_body = close_arr < open_arr` (strict inequalities, excluding neutral dojis where Close == Open).
3. Verification:
   Run:
   `pytest tests/test_hyper_predator.py -v`
   `pytest tests/`
   Confirm 100% pass rate across all test files (107/107 tests).

OUTPUT REQUIREMENTS:
Write report to `/Users/mac/Desktop/TBT-Engine/.agents/worker_polisher/report.md` and handoff to `/Users/mac/Desktop/TBT-Engine/.agents/worker_polisher/handoff.md`.
Send concise completion message to orchestrator_3.
</USER_REQUEST>
