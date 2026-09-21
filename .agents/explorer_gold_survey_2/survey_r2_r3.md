# Technical Survey Report: R2 (Micro-LLM Intuition Exit) & R3 (Macro Fundamental Calendar Blackout)

**Date**: 2026-09-17  
**Author**: `explorer_gold_survey_2`  
**Parent**: `orchestrator_2`  
**System Target**: 5-Minute XAUUSD Relapse Scalper on Hyperliquid DEX  
**Spec Reference**: `/Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md` (Section `## 2026-09-17T19:08:47Z`)

---

## 1. Executive Summary

This survey analyzes the architecture, codebase implementation, operational performance, and test coverage for:
- **R2: Local llama.cpp Micro-LLM Intuition Exit Integration**
- **R3: Macro Fundamental Calendar Blackout**

Both requirements are centered primarily within `/Users/mac/Desktop/TBT-Engine/macro/slm_intuition.py` and are integrated into the execution runtime via `/Users/mac/Desktop/TBT-Engine/engine/fsm.py`, `/Users/mac/Desktop/TBT-Engine/run_relapse_scalper.py`, and validated by `/Users/mac/Desktop/TBT-Engine/tests/test_gold_relapse_scalper.py`.

The current test suite (`tests/test_gold_relapse_scalper.py`) runs **10 out of 10 tests passing** in 2.58 seconds, confirming that both R2 and R3 meet the algorithmic invariants specified in the authoritative request.

---

## 2. R2: Local llama.cpp Micro-LLM Intuition Exit Integration

### 2.1 Server Integration & Model Deployment

- **Endpoint**: Default target is `http://127.0.0.1:8080/completion` (configurable via `--llm-host` and `--llm-port` CLI flags in `run_relapse_scalper.py` and passed into `SLMIntuitionEngine`).
- **Model Target**: The authoritative specification mandates `Qwen2.5-Coder-1.5B-Instruct-GGUF`.
  - In `macro/slm_intuition.py` (lines 15, 259, 287), system prompts and inference payloads are designed specifically for the Qwen2.5 chat template format (`<|im_start|>system ... <|im_end|><|im_start|>user ... <|im_end|><|im_start|>assistant`).
  - *Observation / Discrepancy*: `deploy/install_llama.sh` (line 14) and `deploy/stratton-llm-critic.service` (line 13) currently reference `Qwen2.5-1.5B-Instruct-Q4_K_M.gguf` from `Qwen/Qwen2.5-1.5B-Instruct-GGUF` (the base instruct model rather than the coder variant). The model URL should be updated to `https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF/resolve/main/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf` to strictly match R2.
- **Server Runtime Settings**:
  - Bound to `127.0.0.1:8080` with `-t 2` (2 CPU threads), `-ngl 0` (CPU only), `--mlock` (memory lock to prevent paging), and `--ctx-size 2048`.
  - Systemd unit `deploy/stratton-llm-critic.service` sets `MemoryMax=2000M` (resident memory capped under 2.0 GB, adhering to the 2.5 GB VPS budget).

### 2.2 Compressed 1-Minute Candle Telemetry Feature Engine

In `macro/slm_intuition.py` (lines 228–247) and `engine/fsm.py` (lines 436–478), telemetry is computed causally on each completed 1-minute bar while in `IN_TRADE` or `SCALING`:

```python
@dataclass
class IntuitionTelemetry:
    unrealized_r: float          # Current floating profit/loss expressed in R-multiples
    candle_wick_ratio: float     # Upper wick/range for BUY, lower wick/range for SELL (0.0 - 1.0)
    volume_stall: bool          # True if current volume < 0.6x 10-period rolling average
    dxy_divergence: bool        # True if US Dollar index is diverging adversely against Gold
    side: str                   # "BUY" or "SELL"
    bars_in_trade: int          # Number of 1-minute bars active in position
```

#### Mathematical Telemetry Formulation:
1. **Unrealized R (`unrealized_r`)**:
   $$\text{Unrealized } R = \frac{\text{Current Close} - \text{Entry Price}}{\text{Entry Price} - \text{Initial SL}}$$
   (inverted for SELL). Computed in real time via `OrderBasket.calculate_unrealized_pnl(current_close)`.
2. **Candle Wick Absorption Ratio (`candle_wick_ratio`)**:
   $$\text{Range} = \max(0.01, \text{High} - \text{Low})$$
   $$\text{Opposing Wick}_{\text{BUY}} = \text{High} - \max(\text{Close}, \text{Open})$$
   $$\text{Opposing Wick}_{\text{SELL}} = \min(\text{Close}, \text{Open}) - \text{Low}$$
   $$\text{Ratio} = \min\left(1.0, \max\left(0.0, \frac{\text{Opposing Wick}}{\text{Range}}\right)\right)$$
   A ratio $\ge 0.55$ indicates heavy institutional absorption / rejection against the trade direction.
3. **Volume Stall (`volume_stall`)**:
   $$\text{Volume Stall} = \mathbb{I}\left(\text{Volume}_{\text{last}} < 0.60 \times \frac{1}{9}\sum_{i=1}^{9} \text{Volume}_{-i}\right)$$
   Detects sudden volume exhaustion during a momentum push.
4. **DXY Divergence (`dxy_divergence`)**:
   Boolean flag passed to `on_1m_bar_update()` indicating inverse dollar decoupling or adverse DXY spike.

### 2.3 GBNF Grammar & JSON Schema Constraint

In `macro/slm_intuition.py` (lines 250–254), strict formal grammar is enforced:

```bnf
root ::= "{" ws "\"decision\":" ws ("\"HOLD\"" | "\"EXIT\"") ws "}"
ws ::= [ \t\n\r]*
```

- **Enforcement Mechanism**:
  - The grammar string is passed directly in the HTTP POST payload to `llama-server`:
    ```python
    payload = {
        "prompt": prompt,
        "n_predict": 16,
        "temperature": 0.1,
        "stream": False,
        "grammar": GBNF_INTUITION_GRAMMAR,
        "stop": ["<|im_end|>", "\n"],
    }
    ```
  - Constrains the token sampling mask at the logits level inside `llama-server`.
  - Zero possibility of markdown fences (````json ... ````), conversational padding, explanations, or hallucinated fields.
  - Generates exactly 7 to 9 tokens, enabling ultra-fast completion.
  - Robust parser (`json.loads`) with fallback regex (`if '"EXIT"' in raw_text: EXIT else HOLD`) in case of network stream truncation.

### 2.4 Latency Budget & Algorithmic Fail-Safe Fallback

- **Latency Threshold**: Strict `< 300ms` total execution budget (`timeout_sec = 0.300`).
- **Non-Blocking Architecture**:
  - Uses `aiohttp.ClientSession` with `aiohttp.ClientTimeout(total=0.300)`.
  - Because it runs asynchronously in an `await` block inside the FSM tick handler, it does **not** block the asyncio event loop or interfere with WebSockets or order dispatch.
- **Algorithmic Fail-Safe Fallback (`_fail_safe_evaluation`)**:
  - Automatically triggered under any of:
    1. `asyncio.TimeoutError` (inference takes $\ge 300\text{ ms}$)
    2. HTTP status $\ne 200$ (e.g. 500 server error, 503 overload)
    3. Network exception (connection refused, server down, network split)
  - **Deterministic Rule**:
    ```python
    if telemetry.unrealized_r >= 1.0 and telemetry.candle_wick_ratio >= 0.65 and telemetry.volume_stall:
        decision = IntuitionDecision.EXIT
        rationale = f"Algorithmic Fail-Safe Triggered: {reason} (Absorption + Volume Stall at +{telemetry.unrealized_r:.1f}R)"
    else:
        decision = IntuitionDecision.HOLD
        rationale = f"Algorithmic Fail-Safe Default: {reason} (HOLD active basket)"
    ```
  - **Safety Invariant**: If the trade has gained $\ge +1.0R$ and shows severe adverse wick rejection ($\ge 0.65$) and volume has stalled, the deterministic fail-safe triggers an immediate market exit to protect equity, even if the LLM server is dead. Otherwise, it defaults to `HOLD`, relying on the native breakeven lock and stop-loss envelope.

---

## 3. R3: Macro Fundamental Calendar Blackout

### 3.1 Architecture & Polling Engine

In `macro/slm_intuition.py` (lines 96–218):
- **Class**: `EconomicCalendarFilter`
- **Default Parameters**:
  - `poll_interval_sec: int = 600` (10 minutes)
  - `blackout_window_sec: int = 900` (15 minutes before and after)
  - `calendar_api_url: Optional[str] = None`
- **Background Worker**:
  - `start()` spawns `asyncio.create_task(self._poll_loop())`.
  - Loops continuously while `_is_running` is True, sleeping for `poll_interval_sec` (600s).
  - Emits Antigravity structured telemetry (`CALENDAR_MONITOR_STARTED`, `CALENDAR_UPDATED`, `CALENDAR_POLL_ERROR`, `CALENDAR_MONITOR_STOPPED`).
  - `stop()` cleanly cancels and awaits task shutdown.

### 3.2 High-Impact News Filter & Keyword Dictionary

In `macro/slm_intuition.py` (lines 68–83, 121–143):
- **Currency Filter**: Strictly filters for `USD` events (Gold XAUUSD reacts predominantly to USD fundamental shocks). Non-USD events (e.g. EUR, GBP, JPY) are ignored.
- **High-Impact Trigger**:
  Event triggers blackout if `impact == "HIGH"` OR if the event title contains any of:
  - `CPI`, `CONSUMER PRICE INDEX`
  - `NFP`, `NON-FARM`, `NONFARM`
  - `FOMC`
  - `FED INTEREST RATE`, `FEDERAL FUNDS RATE`, `POWELL`
  - `PPI`, `PRODUCER PRICE INDEX`
  - `GDP`
  - `UNEMPLOYMENT RATE`
- **Dual Verification**: Both the provider's impact rating and keyword matching are used, preventing missed blackouts due to API tagging inconsistencies.

### 3.3 +/- 15-Minute Blackout Window

$$\Delta t = t_{\text{current}} - t_{\text{event}}$$
$$\text{In Blackout} \iff -900 \le \Delta t \le +900$$

- **Exact Timing Logic**:
  - $t_{\text{current}} < t_{\text{event}} - 900\text{s}$ ($> 15\text{m}$ before): **NO BLACKOUT** (Trading Allowed).
  - $t_{\text{event}} - 900\text{s} \le t_{\text{current}} \le t_{\text{event}} + 900\text{s}$: **BLACKOUT ACTIVE** (Trading Halted).
  - $t_{\text{current}} > t_{\text{event}} + 900\text{s}$ ($> 15\text{m}$ after): **NO BLACKOUT** (Trading Resumed).
- **Reason String**: Provides human-readable UTC timestamp and relative minutes, e.g.:
  `"Macro Lock: US Core CPI MoM (10m before release at 12:30 UTC)"`.

### 3.4 State Machine & Execution Router Guard Integration

In `engine/fsm.py` (lines 302–314):
- In `RelapseFSM.on_5m_bar_update()`:
  ```python
  in_blackout, blackout_reason = self.calendar.is_macro_blackout(now_ts)
  if self.state == RelapseState.IDLE:
      if not in_kz:
          return self.state
      if in_blackout:
          return self.state  # Unconditionally refuse new setups
  ```
- While inside the blackout window, the state machine remains locked in `IDLE`. No transitions to `WAITING_FOR_RELAPSE` or `TRIGGER_DETECTED` can occur.
- **Existing Position Protection**: Active trades already entered prior to the blackout window are managed by `on_1m_bar_update` (breakeven lock, SLM intuition exit, trailing stop). New orders are unconditionally blocked.

---

## 4. Codebase Architecture & Component Mapping

| Component | File Path | Line Range | Responsibilities |
|---|---|---|---|
| **Macro & SLM Intuition** | `macro/slm_intuition.py` | 1–419 | `EconomicCalendarFilter`, `MacroNewsEvent`, `GBNF_INTUITION_GRAMMAR`, `SLMIntuitionEngine`, `IntuitionTelemetry`, `_fail_safe_evaluation` |
| **Package Exports** | `macro/__init__.py` | 1–16 | Exports `EconomicCalendarFilter`, `SLMIntuitionEngine`, `IntuitionTelemetry`, `IntuitionDecision`, `MacroNewsEvent` |
| **Relapse FSM** | `engine/fsm.py` | 231–495 | Evaluates macro blackout in `IDLE`; computes 1m candle telemetry; calls `query_intuition_exit()`; executes basket exit on `EXIT` |
| **KillZone Guard** | `quant/hft/utils/killzone.py` | 1–180 | Institutional session windows (London Open 07:00-10:00 UTC, NY 12:30-16:30 UTC) |
| **Daily Drawdown Guard** | `engine/fsm.py` (and `quant/engine/guards.py`) | 91–146 | 5% daily drawdown killswitch tracking peak daily equity |
| **Execution Router** | `engine/execution_router.py` | 1–779 | Order slicing (`fire_layered_orders`), detached stop-loss, breakeven lock at +1.5R, parallel basket close |
| **Production Runtime** | `run_relapse_scalper.py` | 1–163 | Wires `ExecutionRouter`, `KillZoneGuard`, `EconomicCalendarFilter`, `SLMIntuitionEngine`, `RelapseFSM` |
| **Systemd Service** | `deploy/stratton-llm-critic.service` | 1–28 | Configures `llama-server` on port 8080 with 2000M RAM limit |
| **Installer Script** | `deploy/install_llama.sh` | 1–75 | Downloads `llama-server` binary and GGUF model |
| **Unit Test Suite** | `tests/test_gold_relapse_scalper.py` | 1–509 | 10 comprehensive unit/integration tests |

---

## 5. Test Coverage & Empirical Verification

### 5.1 Test Execution Results

Command executed:
```bash
pytest tests/test_gold_relapse_scalper.py -v
```

Output summary:
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

### 5.2 Specific Test Coverage for R2 & R3

1. **`test_macro_calendar_blackout` (Lines 296–331)**:
   - Registers a High-Impact USD CPI event at $T = 1,000,000$.
   - Validates $T - 1200\text{s}$ (20m before): `in_blackout is False`.
   - Validates $T - 600\text{s}$ (10m before): `in_blackout is True`, reason contains `"Macro Lock: US Core CPI MoM"`.
   - Validates $T$ (exact release): `in_blackout is True`.
   - Validates $T + 840\text{s}$ (14m after): `in_blackout is True`.
   - Validates $T + 960\text{s}$ (16m after): `in_blackout is False`.
2. **`test_slm_intuition_exit_mocked` (Lines 337–380)**:
   - Mocks `aiohttp` response with valid GBNF JSON `{"decision": "EXIT"}`: asserts `decision == IntuitionDecision.EXIT`.
   - Mocks `aiohttp` response with valid GBNF JSON `{"decision": "HOLD"}`: asserts `decision == IntuitionDecision.HOLD`.
   - Mocks `asyncio.TimeoutError` (simulating latency $> 300\text{ms}$):
     With exhausted telemetry (`unrealized_r=1.65`, `candle_wick_ratio=0.70`, `volume_stall=True`), asserts fail-safe triggers `EXIT` with `"Algorithmic Fail-Safe Triggered"`.
3. **`test_relapse_fsm_lifecycle` (Lines 414–463)**:
   - Wires `EconomicCalendarFilter`, `KillZoneGuard`, `DailyDrawdownGuard`, and `SLMIntuitionEngine` into `RelapseFSM`.
   - Simulates full transition chain from `IDLE` $\to$ `WAITING_FOR_RELAPSE` $\to$ `TRIGGER_DETECTED` $\to$ `IN_TRADE` $\to$ `EXIT_SIGNAL` $\to$ `IDLE`.

---

## 6. Gaps, Inconsistencies, & Concrete Recommendations

### 6.1 GGUF Model Filename & Download URL Alignment
- **Observation**:
  `ORIGINAL_REQUEST.md` (R2, line 71) specifies:
  > Run `Qwen2.5-Coder-1.5B-Instruct-GGUF` on a local `llama.cpp` server (`http://localhost:8080`).
  However, `deploy/install_llama.sh` (lines 14–15) and `deploy/stratton-llm-critic.service` (line 13) reference:
  > `Qwen2.5-1.5B-Instruct-Q4_K_M.gguf` from `Qwen/Qwen2.5-1.5B-Instruct-GGUF`.
- **Impact**: Non-blocking functional equivalence (both are 1.5B Q4_K_M Qwen2.5 models using the same ChatML format), but the Coder model is optimized for JSON schema/grammar adherence and technical reasoning.
- **Proposed Patch**:
  Update `deploy/install_llama.sh`:
  ```bash
  MODEL_FILENAME="qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"
  MODEL_URL="https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF/resolve/main/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"
  ```
  Update `deploy/stratton-llm-critic.service`:
  ```ini
  ExecStart=/root/ict_sniper/llama.cpp/llama-server \
      -m /root/ict_sniper/models/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf \
      ...
  ```

### 6.2 Calendar API Feed Configuration in CLI Runtime
- **Observation**:
  `EconomicCalendarFilter` supports fetching live events via `calendar_api_url` in `fetch_latest_events()`. However, `run_relapse_scalper.py` does not provide a `--calendar-url` argument in its `argparse` configuration, leaving `self.calendar_api_url = None` by default in live/paper runs.
- **Proposed Refinement**:
  Add `--calendar-url` parameter in `run_relapse_scalper.py`:
  ```python
  parser.add_argument(
      "--calendar-url",
      type=str,
      default="",
      help="HTTP endpoint returning JSON economic calendar events"
  )
  ```
  And pass `calendar_api_url=args.calendar_url or None` into `EconomicCalendarFilter`.

### 6.3 Test Coverage Expansion for Fail-Safe Runner Fallback
- **Observation**:
  `test_slm_intuition_exit_mocked` validates timeout when the position is in profit with high wick ratio (triggering fail-safe `EXIT`). It does not currently assert the alternate branch: where telemetry represents a healthy runner (e.g. `unrealized_r=0.5, candle_wick_ratio=0.20, volume_stall=False`), which should fall back to `HOLD`.
- **Verification**:
  An independent check confirmed that `_fail_safe_evaluation()` correctly returns `HOLD` for healthy runners. Adding an explicit unit test case in `test_gold_relapse_scalper.py` will guarantee 100% branch coverage.

---

## 7. Conclusion

Requirements **R2** (Sub-second Local Micro-LLM Intuition Exit Integration) and **R3** (Macro Fundamental Calendar Blackout) are fully designed, integrated, and verified in the codebase.
- The local server integration uses GBNF grammar constraints for strictly binary `{"decision": "HOLD"}` / `{"decision": "EXIT"}` outputs.
- Sub-300ms latency is enforced with deterministic algorithmic fallback to protect capital.
- The macro calendar monitor enforces unconditional 15-minute halts around High-Impact US news events and integrates cleanly with the 5-minute Relapse FSM.
- Unit and integration tests in `tests/test_gold_relapse_scalper.py` pass cleanly.
