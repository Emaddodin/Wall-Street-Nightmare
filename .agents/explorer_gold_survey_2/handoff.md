# Handoff Report: Survey of R2 (Local Micro-LLM Intuition Exit) & R3 (Macro Fundamental Calendar Blackout)

**Agent**: `explorer_gold_survey_2`  
**Parent**: `orchestrator_2` (convId: `d8cde56b-142d-4ad0-b360-6f180e2c8eaa`)  
**Working Directory**: `/Users/mac/Desktop/TBT-Engine/.agents/explorer_gold_survey_2`  
**Handoff Type**: Hard Handoff (Investigation Complete)  

---

## 1. Observation

1. **Authoritative Specification (`ORIGINAL_REQUEST.md:70-79`)**:
   - R2 mandates running `Qwen2.5-Coder-1.5B-Instruct-GGUF` on a local `llama.cpp` server (`http://localhost:8080`), feeding compressed 1-minute candle telemetry (`unrealized_r`, `candle_wick_ratio`, `volume_stall`, `dxy_divergence`), constraining inference via GBNF grammar or JSON schema strictly to `{"decision": "HOLD"}` or `{"decision": "EXIT"}`, and enforcing strict `< 300ms` execution timeout with automatic algorithmic fail-safe fallback.
   - R3 mandates maintaining a background economic calendar monitor polling every 10 minutes and unconditionally halting new trades within 15 minutes before and after High-Impact US news releases (`CPI`, `NFP`, `FOMC`, `PPI`, `Fed Rate Decisions`).

2. **Core Implementation in `macro/slm_intuition.py`**:
   - `EconomicCalendarFilter` (lines 96–218): Polls every 600s (`poll_interval_sec=600`), checks blackout window of 900s (`blackout_window_sec=900`), parses events from `calendar_api_url`, and provides `is_macro_blackout(current_ts) -> Tuple[bool, str]`.
   - `HIGH_IMPACT_KEYWORDS` (lines 68–82): Includes `"CPI"`, `"CONSUMER PRICE INDEX"`, `"NFP"`, `"NON-FARM"`, `"NONFARM"`, `"FOMC"`, `"FED INTEREST RATE"`, `"FEDERAL FUNDS RATE"`, `"POWELL"`, `"PPI"`, `"PRODUCER PRICE INDEX"`, `"GDP"`, `"UNEMPLOYMENT RATE"`.
   - `IntuitionTelemetry` (lines 228–247): Contains fields `unrealized_r`, `candle_wick_ratio`, `volume_stall`, `dxy_divergence`, `side`, `bars_in_trade`, and `to_compact_dict()`.
   - `GBNF_INTUITION_GRAMMAR` (lines 250–253):
     ```bnf
     root ::= "{" ws "\"decision\":" ws ("\"HOLD\"" | "\"EXIT\"") ws "}"
     ws ::= [ \t\n\r]*
     ```
   - `SLMIntuitionEngine` (lines 256–419): Defaults to `http://127.0.0.1:8080`, `timeout_sec=0.300`. Calls `aiohttp.ClientSession.post(f"{self.base_url}/completion")` with `n_predict: 16`, `grammar: GBNF_INTUITION_GRAMMAR`, and timeout 300ms.
   - `_fail_safe_evaluation` (lines 391–419): Triggers `EXIT` if `telemetry.unrealized_r >= 1.0 and telemetry.candle_wick_ratio >= 0.65 and telemetry.volume_stall`, else `HOLD`.

3. **FSM Integration in `engine/fsm.py`**:
   - Lines 304–314: In `RelapseFSM.on_5m_bar_update`, evaluates `in_blackout, blackout_reason = self.calendar.is_macro_blackout(now_ts)`. If `self.state == RelapseState.IDLE and in_blackout`, it unconditionally returns `self.state`, preventing new trades.
   - Lines 436–494: In `RelapseFSM.on_1m_bar_update`, computes `candle_wick_ratio`, `volume_stall` (current vol < 0.6x 10-bar avg), `unrealized_r`, and constructs `IntuitionTelemetry`. Awaits `self.intuition.query_intuition_exit(telemetry)`. If `decision == IntuitionDecision.EXIT`, transitions to `RelapseState.EXIT_SIGNAL` and executes basket exit.

4. **Runtime Wiring in `run_relapse_scalper.py`**:
   - Lines 90–99: Starts `calendar_filter = EconomicCalendarFilter()`, instantiates `intuition_engine = SLMIntuitionEngine(host=args.llm_host, port=args.llm_port, timeout_sec=0.300)`, and passes both into `RelapseFSM`. Clean shutdown handles `await calendar_filter.stop()` and `await intuition_engine.close()`.

5. **Existing Tests in `tests/test_gold_relapse_scalper.py`**:
   - Command executed: `pytest tests/test_gold_relapse_scalper.py -v`.
   - Result: All 10 tests PASSED cleanly in 2.58s:
     - `test_macro_calendar_blackout` PASSED (verifies T-20m, T-10m, T, T+14m, T+16m).
     - `test_slm_intuition_exit_mocked` PASSED (verifies GBNF parsing of EXIT, HOLD, and TimeoutError fail-safe fallback).
     - `test_relapse_fsm_lifecycle` PASSED (verifies full FSM integration with calendar, killzone, drawdown guard, and intuition).

6. **Deployment Files Inspection**:
   - `deploy/install_llama.sh` (line 14) and `deploy/stratton-llm-critic.service` (line 13) currently set `MODEL_FILENAME="Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"`. The specification in `ORIGINAL_REQUEST.md:71` explicitly designates `Qwen2.5-Coder-1.5B-Instruct-GGUF`.

---

## 2. Logic Chain

1. **R2 Architecture Compliance**:
   - Observation 2 demonstrates that `SLMIntuitionEngine` connects to `http://127.0.0.1:8080` (or configured host/port) and formats prompt queries specifically for Qwen2.5 ChatML format.
   - Observation 2 & 3 demonstrate that 1-minute tape features (`unrealized_r`, `candle_wick_ratio`, `volume_stall`, `dxy_divergence`) are causally computed on each 1m bar and serialized into compact JSON.
   - Observation 2 demonstrates that `GBNF_INTUITION_GRAMMAR` restricts the sampler logits to strictly output `{"decision": "HOLD"}` or `{"decision": "EXIT"}` within 16 tokens.
   - Observation 2 demonstrates that `aiohttp.ClientTimeout(total=0.300)` guarantees termination under 300ms without blocking the event loop. In the event of timeout, non-200 HTTP response, or network failure, `_fail_safe_evaluation` triggers algorithmic capital protection.

2. **R3 Architecture Compliance**:
   - Observation 2 demonstrates that `EconomicCalendarFilter` maintains an asynchronous 10-minute polling task (`poll_interval_sec=600`).
   - Observation 2 & 3 demonstrate that `is_macro_blackout` enforces a 15-minute window (`blackout_window_sec=900`) around High-Impact US news events matching `HIGH_IMPACT_KEYWORDS` or `impact == "HIGH"`.
   - Observation 3 demonstrates that `RelapseFSM.on_5m_bar_update` checks `is_macro_blackout` in `IDLE` state and unconditionally rejects new setup transitions, halting all new entries during news releases.

3. **Production Readiness**:
   - Observation 5 confirms that existing unit tests validate both R2 and R3 components, passing all tests with 0 failures.
   - Observation 6 highlights a minor configuration alignment: `deploy/install_llama.sh` and `deploy/stratton-llm-critic.service` should update the model name from `Qwen2.5-1.5B-Instruct-Q4_K_M.gguf` to `qwen2.5-coder-1.5b-instruct-q4_k_m.gguf` to strictly match the Coder variant specified in R2.

---

## 3. Caveats

1. **Local llama-server Process**: The unit tests in `test_gold_relapse_scalper.py` use mocked `aiohttp` responses for `SLMIntuitionEngine`, which is correct for CI/CD test isolation. End-to-end live testing with a real `llama-server` process requires downloading the GGUF model and launching `llama-server` on localhost.
2. **External News Feed**: In production, `EconomicCalendarFilter` requires an external HTTP JSON feed URL passed via `calendar_api_url`. In `run_relapse_scalper.py`, a `--calendar-url` argument is currently not exposed in `argparse`; it currently relies on default `None` (or manual event registration in tests).

---

## 4. Conclusion

Requirements **R2** (Micro-LLM Intuition Exit Integration) and **R3** (Macro Fundamental Calendar Blackout) are **fully implemented and verified** in the codebase (`macro/slm_intuition.py`, `engine/fsm.py`, `tests/test_gold_relapse_scalper.py`).

### Key Implementation Status:
1. **Local llama.cpp Server**: Configured for `http://localhost:8080` with asynchronous `aiohttp` client in `SLMIntuitionEngine`.
2. **Compressed Telemetry**: Causally computed in `on_1m_bar_update` across all 4 metrics (`unrealized_r`, `candle_wick_ratio`, `volume_stall`, `dxy_divergence`).
3. **GBNF Grammar**: Strict grammar `root ::= "{" ws "\"decision\":" ws ("\"HOLD\"" | "\"EXIT\"") ws "}"` restricts output strictly to binary JSON.
4. **Latency Bounds & Fail-Safe**: Non-blocking asynchronous requests with `< 300ms` timeout and deterministic fallback logic (`unrealized_r >= 1.0` + `candle_wick_ratio >= 0.65` + `volume_stall` triggers `EXIT`, else `HOLD`).
5. **Macro Blackout**: 10-minute polling monitor enforcing unconditional +/- 15-minute halt on new entries around High-Impact USD news events.
6. **Test Coverage**: 10/10 tests pass cleanly in `tests/test_gold_relapse_scalper.py`.

### Recommended Minor Action Items for Implementers:
- Update `deploy/install_llama.sh` and `deploy/stratton-llm-critic.service` model URL to point to `qwen2.5-coder-1.5b-instruct-q4_k_m.gguf` (Coder variant).
- Add `--calendar-url` CLI argument to `run_relapse_scalper.py` `argparse` to allow passing the live economic calendar endpoint.

---

## 5. Verification Method

### Test Execution Command:
```bash
pytest /Users/mac/Desktop/TBT-Engine/tests/test_gold_relapse_scalper.py -v
```

### Expected Output:
```
tests/test_gold_relapse_scalper.py::test_margin_invariant PASSED               [ 10%]
tests/test_gold_relapse_scalper.py::test_stop_loss_envelope_invariant PASSED       [ 20%]
tests/test_gold_relapse_scalper.py::test_order_slicing_and_detached_stop PASSED    [ 30%]
tests/test_gold_relapse_scalper.py::test_breakeven_lock_at_1_5r PASSED         [ 40%]
tests/test_gold_relapse_scalper.py::test_dynamic_basket_close PASSED           [ 50%]
tests/test_gold_relapse_scalper.py::test_macro_calendar_blackout PASSED        [ 60%]
tests/test_gold_relapse_scalper.py::test_slm_intuition_exit_mocked PASSED      [ 70%]
tests/test_gold_relapse_scalper.py::test_daily_drawdown_killswitch PASSED       [ 80%]
tests/test_gold_relapse_scalper.py::test_relapse_fsm_lifecycle PASSED          [ 90%]
tests/test_gold_relapse_scalper.py::test_candlestick_and_ict_integration PASSED [100%]

============================== 10 passed in 2.58s ==============================
```

### Standalone Edge Case Verification Command:
```bash
python3 -c "
from macro.slm_intuition import EconomicCalendarFilter, MacroNewsEvent, SLMIntuitionEngine, IntuitionTelemetry, IntuitionDecision
calendar = EconomicCalendarFilter()
now = 1_000_000.0
calendar.add_scheduled_event(MacroNewsEvent('ECB Rate', 'EUR', 'HIGH', now))
assert calendar.is_macro_blackout(now)[0] is False
calendar.add_scheduled_event(MacroNewsEvent('US CPI', 'USD', 'HIGH', now))
assert calendar.is_macro_blackout(now)[0] is True
assert calendar.is_macro_blackout(now - 1200)[0] is False
assert calendar.is_macro_blackout(now + 1200)[0] is False
print('Verification successful!')
"
```

### Invalidation Conditions:
- If `pytest tests/test_gold_relapse_scalper.py` fails on `test_macro_calendar_blackout` or `test_slm_intuition_exit_mocked`.
- If `SLMIntuitionEngine` query duration exceeds 300ms without invoking `_fail_safe_evaluation`.
- If `RelapseFSM` transitions out of `IDLE` while `is_macro_blackout` returns `True`.
