# Handoff Report: Empirical Adversarial Challenge 2 (Async Jitter, Stop Cancellation & Fail-Safe Stress Verifier)

**Author**: challenger_gold_2  
**Role**: Empirical Adversarial Challenger (critic, specialist)  
**Date**: 2026-09-17T19:50:00Z  
**Verdict**: **REJECT**

---

## 1. Observation

Direct empirical observations, verbatim log messages, file locations, line numbers, and tool outputs:

### 1.1 Test Execution Output Summary
- Test script: `/Users/mac/Desktop/TBT-Engine/.agents/challenger_gold_2/adversarial_stress_test.py`
- Command executed: `python3 .agents/challenger_gold_2/adversarial_stress_test.py`
- Structured results: `/Users/mac/Desktop/TBT-Engine/.agents/challenger_gold_2/test_results.json`
- Full execution log: `/Users/mac/Desktop/TBT-Engine/.agents/challenger_gold_2/test_results.txt`
- Result summary: **13 executed, 10 PASSED, 3 FAILED (Confirmed Vulnerabilities)**

```
2026-09-17 23:18:53,939 [INFO]   [PASS] Pillar 1.1: Slicing Jitter & Timing (50ms) (101.3ms): Jitter timing verified (50ms increments via asyncio.gather)
2026-09-17 23:18:53,939 [INFO]   [PASS] Pillar 1.2: Concurrent Slicing Load (10x calls) (229.1ms): Concurrent load handled cleanly (10 concurrent calls = 30 slices)
2026-09-17 23:18:53,939 [ERROR]  [FAIL] Pillar 1.3: Partial Slice Failure Unhedged Risk (11.0ms): VULNERABILITY DETECTED: Partial slice failure left 1 slice(s) open without placing a detached Stop Market order on Hyperliquid CLOB!
2026-09-17 23:18:53,939 [INFO]   [PASS] Pillar 2.1: Detached Stop Lifecycle & Basket Close (12.5ms): Detached stop placed and cancelled with zero orphan orders
2026-09-17 23:18:53,939 [ERROR]  [FAIL] Pillar 2.2: Race Condition: BE Lock vs Basket Close (139.1ms): RACE CONDITION VULNERABILITY CONFIRMED: evaluate_breakeven_lock() created 1 orphan resting stop order(s) on the CLOB after basket was closed!
2026-09-17 23:18:53,939 [INFO]   [PASS] Pillar 2.3: Race Condition: Stop Filled on Venue (25.8ms): Handled stop already filled on exchange cleanly
2026-09-17 23:18:53,939 [INFO]   [PASS] Pillar 3.1: LLM Latency Enforcement (<300ms Hang) (5011.2ms): Timeout strictly enforced (301.3ms < 350ms) and fail-safe triggered
2026-09-17 23:18:53,939 [INFO]   [PASS] Pillar 3.2: LLM Fail-Safe Matrix Verification (7.1ms): All 6 fail-safe heuristic matrix test cases passed deterministically
2026-09-17 23:18:53,939 [INFO]   [PASS] Pillar 3.3: LLM HTTP Error & Malformed Resilience (153.6ms): Resilience to HTTP 500, 502, truncated JSON, and HTML verified
2026-09-17 23:18:53,939 [INFO]   [PASS] Pillar 4.1: Macro Calendar Subsecond Boundary Precision (0.3ms): Sub-second boundary precision (+/- 900.000s) verified
2026-09-17 23:18:53,939 [INFO]   [PASS] Pillar 4.2: Macro Calendar Overlapping Windows (0.2ms): Overlapping events form seamless continuous blackout window
2026-09-17 23:18:53,939 [INFO]   [PASS] Pillar 4.3: Macro Calendar Currency/Impact Matrix (0.1ms): Currency and keyword filter matrix strictly enforced
2026-09-17 23:18:53,939 [ERROR]  [FAIL] Pillar 4.4: FSM TRIGGER_DETECTED Blackout Bypass (14.9ms): VULNERABILITY CONFIRMED: FSM in TRIGGER_DETECTED state dispatched 3 orders inside the +/- 15m Macro Blackout window without checking calendar filter!
```

---

### 1.2 Verbatim Observations for Vulnerabilities Found

#### Observation A: Unhandled Partial Slice Failure Leaves Naked Leveraged Positions on CLOB Without Stop-Loss
- **Target File**: `/Users/mac/Desktop/TBT-Engine/engine/execution_router.py`, lines 953–993:
```python
953:             async def _dispatch_single_slice(idx: int) -> OrderSlice:
954:                 if idx > 0:
955:                     delay_s = (idx * self.risk.slice_jitter_ms) / 1000.0
956:                     await asyncio.sleep(delay_s)
957:                 # Open-ended market order on Hyperliquid CLOB
958:                 res = await self.venue.market_open(
959:                     coin=coin,
960:                     is_buy=is_buy,
961:                     sz=slice_sz,
962:                 )
...
974:             tasks = [_dispatch_single_slice(i) for i in range(num_slices)]
975:             slices = await asyncio.gather(*tasks)
976:             basket.slices = list(slices)
...
986:             stop_res = await self.venue.market_close(
987:                 coin=coin,
988:                 sz=basket.total_sz,
989:                 trigger_px=basket.sl_price,
990:                 reduce_only=True,
991:             )
992:             basket.stop_order_id = stop_res.get("oid")
```
- **Observed Behavior**:
  When a single slice coroutine raises an exception (e.g. `RuntimeError("Simulated Hyperliquid CLOB Disconnection")` or network timeout on slice 2), `asyncio.gather(*tasks)` immediately raises the exception out of `fire_layered_orders`.
  - Slice 0 was already transmitted and filled on the venue (`orders_opened = 1`).
  - Lines 986–993 were never reached.
  - `stops_placed = 0`.
  - `self.active_basket_id` remains `None`.
  - **Verbatim Result**: `VULNERABILITY DETECTED: Partial slice failure left 1 slice(s) open without placing a detached Stop Market order on Hyperliquid CLOB!`

#### Observation B: Concurrency Race Between Breakeven Lock and Dynamic Basket Close Generates Orphan Stop Orders
- **Target File**: `/Users/mac/Desktop/TBT-Engine/engine/execution_router.py`, lines 1015–1078 vs lines 1126–1195:
```python
1015:     async def evaluate_breakeven_lock(self, current_price: float) -> bool:
1016:         if not self.active_basket_id:
1017:             return False
1018: 
1019:         basket = self.baskets.get(self.active_basket_id)
1020:         if not basket or not basket.is_active or basket.breakeven_locked:
1021:             return False
...
1051:             if basket.stop_order_id:
1052:                 await self.venue.cancel(basket.coin, basket.stop_order_id)
1053: 
1054:             # Transmit new reduce_only=True stop order at Breakeven
1055:             new_stop_res = await self.venue.market_close(
1056:                 coin=basket.coin,
1057:                 sz=basket.current_sz,
1058:                 trigger_px=new_sl,
1059:                 reduce_only=True,
1060:             )
```
  Notice: `evaluate_breakeven_lock` **DOES NOT acquire `self._lock`**.
  In contrast, `close_basket` has:
```python
1126:         async with self._lock:
1127:             if not self.active_basket_id:
1128:                 return None
```
- **Observed Behavior**:
  When `evaluate_breakeven_lock` and `close_basket` run concurrently:
  1. `evaluate_breakeven_lock` passes `if not basket.is_active` check and awaits `venue.cancel(...)`.
  2. Concurrently, `close_basket` acquires `self._lock`, liquidates the position, cancels resting stops, sets `basket.is_active = False`, and clears `self.active_basket_id = None`.
  3. `evaluate_breakeven_lock` resumes and executes lines 1055–1060, transmitting a NEW Stop Market order `HL-CLOSE-4DBC3E13` (`trigger_px = 2500.10`) on the exchange.
  4. **Verbatim Result**: `venue.get_orphan_resting_stops()` contains `{'oid': 'HL-CLOSE-4DBC3E13', 'trigger_px': 2500.1, 'reduce_only': True, 'status': 'resting'}`. An orphan stop order remains permanently active on the exchange CLOB after the position is closed.

#### Observation C: RelapseFSM Bypasses Macro Calendar Blackout in `TRIGGER_DETECTED` State
- **Target File**: `/Users/mac/Desktop/TBT-Engine/engine/fsm.py`, lines 304–377:
```python
304:         in_blackout, blackout_reason = self.calendar.is_macro_blackout(now_ts)
...
309:         if self.state == RelapseState.IDLE:
310:             if not in_kz:
311:                 return self.state
312:             if in_blackout:
313:                 return self.state
...
328:         elif self.state == RelapseState.WAITING_FOR_RELAPSE:
329:             if not in_kz or in_blackout:
330:                 self.transition_to(RelapseState.IDLE, reason="Exited KZ or Entered Macro Blackout")
331:                 return self.state
...
345:         elif self.state == RelapseState.TRIGGER_DETECTED:
346:             trigger_confirmed, wick_price, pat_name = self._validate_trigger_pattern(df_5m, self.context.direction)
347: 
348:             if trigger_confirmed:
349:                 self.context.invalidation_wick_price = wick_price
350:                 self.context.trigger_pattern = pat_name
351: 
352:                 # Attempt layered order slicing
353:                 if self.router:
354:                     basket = await self.router.fire_layered_orders(...)
```
- **Observed Behavior**:
  While `IDLE` (line 312) and `WAITING_FOR_RELAPSE` (line 329) guard against `in_blackout`, `TRIGGER_DETECTED` (lines 345–377) contains **no check for `in_blackout`**.
  When the FSM transitions to `TRIGGER_DETECTED` prior to news, and the trigger candle closes 5 minutes before High-Impact US CPI (inside the +/- 15-minute blackout window where `is_macro_blackout == True`), `on_5m_bar_update` executes `fire_layered_orders` and transitions to `IN_TRADE`.
  - **Verbatim Result**: `VULNERABILITY CONFIRMED: FSM in TRIGGER_DETECTED state dispatched 3 orders inside the +/- 15m Macro Blackout window without checking calendar filter!`

---

## 2. Logic Chain

1. **Safety Contract for Leveraged Scalping**:
   The engine operates with 100x leverage on GOLD perpetuals (`ORIGINAL_REQUEST.md` R1, R5). Under 100x leverage, a $1.00 move is ~4% of account equity, and a $2.50 move causes total account liquidation. Therefore, two non-negotiable invariants are:
   - *Zero naked positions*: Every open position must have a detached Stop Market order on the CLOB at all times.
   - *Zero orphan orders*: When a basket is closed, no detached stop orders may remain resting on the exchange.
   - *Unconditional macro blackout*: Absolutely zero orders may be placed during the +/- 15m window surrounding High-Impact news releases.

2. **From Observation A to Logic Conclusion**:
   In `ExecutionRouter.fire_layered_orders`, `asyncio.gather(*tasks)` dispatches slices concurrently without wrapping each slice task in exception handling or rollback logic. If slice 0 succeeds and slice 1 throws an exception, `gather` raises immediately. Slice 0 is filled and live on Hyperliquid CLOB, but line 986 (`market_close` with `trigger_px`) is never executed. Consequently, slice 0 remains completely unhedged on the exchange at 100x leverage without a stop-loss order. Furthermore, since `active_basket_id` is never recorded, neither the FSM nor the runtime can manage or liquidate this live position.

3. **From Observation B to Logic Conclusion**:
   In `ExecutionRouter`, `close_basket()` uses `async with self._lock:`, but `evaluate_breakeven_lock()` does not acquire `self._lock`. When high volatility triggers a dynamic basket close at the same moment an unrealized R threshold (+1.5R) is evaluated, both coroutines interleave across `await self.venue.cancel()` network I/O boundaries. `close_basket` closes the position and cancels the old stop. Subsequently, `evaluate_breakeven_lock` transmits a new detached stop order (`market_close(trigger_px=...)`). Because the position was already liquidated, this new stop order rests on the CLOB as an orphan order. If market price later reaches the trigger price, this resting order could trigger an unintended position flip or fail with reduce-only rejection. This violates Acceptance Criterion R1: *"Market close liquidates all sliced tickets in parallel without orphan orders"*.

4. **From Observation C to Logic Conclusion**:
   `RelapseFSM.on_5m_bar_update` correctly checks `in_blackout` in `IDLE` and `WAITING_FOR_RELAPSE`, but completely omits the check in `TRIGGER_DETECTED`. If the market satisfies the 5M pullback condition at T-16m (outside blackout), transitions to `TRIGGER_DETECTED`, and then prints an engulfing trigger candle at T-10m or T-5m (inside blackout), the FSM fires 3 market orders. This directly violates Acceptance Criterion R3: *"Trading is completely halted during the +/- 15-minute window surrounding High-Impact US news events"*.

5. **Robust Components Verified (Positive Logic)**:
   - Pillar 1.1: 50ms stagger jitter was verified: $dt_{0 \to 1} = 52.2\text{ms}$, $dt_{1 \to 2} = 48.1\text{ms}$, cumulative $dt_{0 \to 2} = 100.3\text{ms}$.
   - Pillar 1.2: Concurrent load serialization via `self._lock` successfully processed 10 simultaneous multi-slice requests (30 slices) without coroutine deadlocks or state corruption.
   - Pillar 3.1 & 3.2 & 3.3: Local LLM latency enforcement (< 300ms timeout) was validated under severe server hang (5.0s delay). The client aborted at exactly 301.3ms and triggered the deterministic algorithmic fail-safe EXIT. HTTP 500, 502, malformed JSON, and HTML error pages were all handled without crashing.
   - Pillar 4.1 & 4.2 & 4.3: Economic calendar filter verified sub-second precision to 1ms ($T - 900.000\text{s}$ is True, $T - 900.001\text{s}$ is False), merged overlapping event windows without gaps, and filtered non-USD / low-impact events.

---

## 3. Caveats

- Tests were run against `InstrumentedSimulatedVenue` mimicking Hyperliquid DEX REST/WebSocket mechanics with injected network delays (0–50ms) and mock failures. Live mainnet WebSocket network jitter under VPS CPU saturation was not directly tested, but mock socket hangs and 5-second HTTP hangs verified timeout safety.
- The 100x leverage margin ceiling formula ($sz \times px / 100 \le 0.20 \times \text{equity}$) passed all margin checks under standard and concurrent loads; no margin overflow was observed.

---

## 4. Conclusion & Actionable Mitigations

### Binary Verdict: **REJECT**

The current implementation violates three core safety and execution invariants required by `ORIGINAL_REQUEST.md` and `PROJECT.md`:
1. **Unhedged Slices on Exception**: `fire_layered_orders` leaves opened slices naked on CLOB without stop orders if any later slice fails.
2. **Orphan Orders on Concurrency**: `evaluate_breakeven_lock` lacks lock synchronization with `close_basket`, generating orphan resting stop orders under simulated race conditions.
3. **Macro Blackout Breach**: `RelapseFSM` fails to enforce macro calendar blackout when in `TRIGGER_DETECTED` state.

### Actionable Mitigations for Worker:
1. **Fix `fire_layered_orders` in `engine/execution_router.py`**:
   Wrap slice gathering with exception handling and a rollback/cleanup mechanism. If any slice fails or throws:
   - For all slices that were already filled, immediately place an emergency detached stop order for the filled aggregate size OR immediately market-close the filled slices.
   - Do not leave open slices unhedged and unmonitored.
2. **Fix `evaluate_breakeven_lock` and `sync_global_trailing_sl` in `engine/execution_router.py`**:
   Add `async with self._lock:` inside `evaluate_breakeven_lock` and `sync_global_trailing_sl`.
   Before placing the new stop order at line 1055, verify that `basket.is_active` is still `True` and `self.active_basket_id == basket.basket_id`. If the basket was closed while awaiting order cancellation, abort immediately without placing a new stop order.
3. **Fix `on_5m_bar_update` in `engine/fsm.py`**:
   In state `TRIGGER_DETECTED` (line 345), add an unconditional guard:
   ```python
   elif self.state == RelapseState.TRIGGER_DETECTED:
       if not in_kz or in_blackout:
           self.transition_to(RelapseState.IDLE, reason="Exited KZ or Entered Macro Blackout before trigger execution")
           return self.state
   ```

---

## 5. Verification Method

To independently verify the observations and reproduce all test results:

1. **Execute Empirical Adversarial Test Suite**:
   ```bash
   cd /Users/mac/Desktop/TBT-Engine
   python3 .agents/challenger_gold_2/adversarial_stress_test.py
   ```
2. **Inspect Structured Output**:
   ```bash
   cat .agents/challenger_gold_2/test_results.json
   ```
3. **Inspect Failure Details**:
   - In `test_results.json`, examine:
     - `Pillar 1.3: Partial Slice Failure Unhedged Risk` -> `passed: false`
     - `Pillar 2.2: Race Condition: BE Lock vs Basket Close` -> `passed: false`, `orphan_count: 1`
     - `Pillar 4.4: FSM TRIGGER_DETECTED Blackout Bypass` -> `passed: false`, `orders_dispatched: 3`

4. **Invalidation Condition**:
   This REJECT verdict will be invalidated only when the worker applies the mitigations above, and re-running `python3 .agents/challenger_gold_2/adversarial_stress_test.py` yields **13/13 PASSED** with 0 orphan orders, 0 unhedged slices, and 0 orders dispatched inside macro news blackouts.
