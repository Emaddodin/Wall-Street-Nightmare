# Handoff Report — Reviewer 2 (Orderflow Math & Risk Reviewer)

**Verdict: REQUEST_CHANGES**

---

## 1. Observation

1. **Test Execution Results**:
   - Executed `pytest tests/test_hyper_predator.py -v`:
     Command exited with code 0.
     Result: `============================== 40 passed in 8.94s ==============================`
   - Executed `pytest tests/`:
     Command exited with code 1.
     Result: `================== 1 failed, 106 passed, 1 warning in 13.88s ===================`
     Verbatim failure:
     ```text
     FAILED tests/test_adversarial_predator_stress.py::TestStress2RapidReversalsAndMarketShocks::test_instant_minus_10_loss_drop_long_and_short - AssertionError: assert 2500.0 == 2479.0
     where 2500.0 = PredatorOrderSlice(..., close_price=2500.0, ...).close_price
     ```
2. **Execution Bridge Price Tracking Defect**:
   In `hyper_predator_bot.py:908-925`:
   ```python
   close_res = await self.venue.market_close(
       coin=coin,
       sz=aggregate_sz,
       reduce_only=True,
   )
   exit_px = close_res.get("fill_price", await self.venue.get_market_price(coin))
   ```
   `ExecutionBridge.close_basket` has signature `async def close_basket(self, coin: str = "GOLD", reason: str = "TARGET")` and does not take or forward an `exit_price` argument to `market_close`. In simulated venues where the external market price is not separately updated prior to calling `close_basket`, `market_close` defaults to the stale opening price ($2500.00), leaving slice `close_price` recorded at $2500.00 instead of the actual liquidation drop price ($2479.00), skewing realized PnL accounting.
3. **Tick Velocity Gate Fallback Loophole**:
   In `hyper_predator_bot.py:667-670`:
   ```python
   else:
       vel_ok = candle.get("velocity_surge_valid", True)
       vel_ratio = candle.get("velocity_surge_ratio", 1.6)
       if not vel_ok:
           return None
   ```
   If `tick_timestamps` is empty (e.g. at cold startup or during WebSocket tick stalls), `candle.get("velocity_surge_valid", True)` defaults to `True`, allowing trades to trigger without tick velocity verification.
4. **Doji Candle Inequality Inconsistency**:
   In `backtester.py:675-676`:
   ```python
   is_bullish_candle = closes >= opens
   is_bearish_candle = closes <= opens
   ```
   vs `hyper_predator_bot.py:579, 587`:
   ```python
   is_bullish_close = (c > o)
   is_bearish_close = (c < o)
   ```
   In the backtester, neutral doji candles ($C = O$) are treated as directional, conflicting with the bot's strict body close requirement.
5. **Mathematical Verification of Core Invariants**:
   - Lower rejection wick formula for Long: $\frac{\min(O, C) - L}{H - L} \ge 0.65, C > O$ (`hyper_predator_bot.py:577-582`) verified.
   - Upper rejection wick formula for Short: $\frac{H - \max(O, C)}{H - L} \ge 0.65, C < O$ (`hyper_predator_bot.py:584-590`) verified.
   - Detached stop-loss placement: placed at exactly $L - \$1.00$ for Long and $H + \$1.00$ for Short with `reduce_only=True` (`hyper_predator_bot.py:766-772`).
   - Final 5s tick velocity surge: $\frac{v_{\text{surge}}}{\max(v_{\text{base}}, 0.1)} \ge 1.50$ (`hyper_predator_bot.py:611-620`) verified.
   - Opposing M5 S/R touch target exit (`hyper_predator_bot.py:1060-1081`) verified.
   - Opposing $\ge 65\%$ reversal wick exit (`hyper_predator_bot.py:1298-1310`) verified.
   - Hard Equity Shield liquidation at $-\$10.00$ floating PnL (`hyper_predator_bot.py:1046-1059`) verified.
   - Top-5 L2 book imbalance ratio ($> 3.0 \cdot \text{volatility\_regime}$) and trade tape delta stall ($> 80\%$ opposing fills) verified.
   - Orderflow memory latency benchmark verified at $< 0.02\text{ms}$ (strictly $< 5\text{ms}$).
6. **Repository Purge Verification**:
   - Active root contains only active components (`hyper_predator_bot.py`, `backtester.py`, `engine/`, `macro/`, `deploy/`, `tests/`).
   - Legacy MT5 connectors, obsolete scrapers, and deprecated trading loops are quarantined in `_archive/`.

---

## 2. Logic Chain

1. **From Observation 1**: `pytest tests/` failed with exit code 1 due to `test_instant_minus_10_loss_drop_long_and_short` failing with `AssertionError: assert 2500.0 == 2479.0`.
2. **From Observation 2**: The failure occurs because `close_basket` has no mechanism to accept or propagate the market price at liquidation. In `test_adversarial_predator_stress.py:206-233`, the test simulated a sudden drop in `TapeBookMemory.best_bid = 2479.00`, but because `close_basket` called `venue.market_close(coin=coin, sz=aggregate_sz, reduce_only=True)` without `px`, `SimulatedBrokerVenue` filled the order at `self._prices["GOLD"]` which remained at $2500.00. Slices were marked closed at $2500.00, resulting in the assertion failure and incorrect PnL calculation.
3. **From Observation 3**: Under live execution conditions, if tick data is missing or cold, defaulting `velocity_surge_valid` to `True` bypasses the tick velocity filter mandated by Requirement R2.
4. **From Observations 1, 2, and 3**: A complete acceptance standard requires `pytest tests/` to pass cleanly with exit code 0 and zero regressions, and requires strict gating of tick velocity. Because an automated test fails and an execution price tracking defect exists, changes must be requested.

---

## 3. Caveats

- Live WebSocket connection to Hyperliquid testnet/mainnet was not tested in this offline environment; all tests ran using mock and simulated venues (`SimulatedBrokerVenue` and `TapeBookMemory`).
- Network latency over external internet was not measured; only local Python memory execution latency was benchmarked ($< 0.02\text{ms}$).

---

## 4. Conclusion

The core order flow math, rejection wick formulas, rolling M5 S/R pivots, top-5 L2 book imbalance calculations, and vectorized streaming backtester are mathematically sound, highly performant, and correctly aligned with the specification.
However, because:
1. `pytest tests/` fails on `tests/test_adversarial_predator_stress.py` with exit code 1;
2. `ExecutionBridge.close_basket` fails to propagate execution fill prices during liquidations;
3. `SniperEngine.evaluate_m1_trigger` permits a bypass when tick buffers are empty;

The final verdict is **REQUEST_CHANGES**.

### Required Changes:
1. In `tests/test_adversarial_predator_stress.py:206`, set `venue.set_market_price("GOLD", 2479.00)` to ensure venue state reflects the market shock (consistent with line 247).
2. In `hyper_predator_bot.py:871`, update `close_basket` to optionally accept `exit_price: Optional[float] = None` and pass `px=exit_price` to `self.venue.market_close` to guarantee accurate fill price tracking on slices and realized PnL.
3. In `hyper_predator_bot.py:667`, change `candle.get("velocity_surge_valid", True)` to `candle.get("velocity_surge_valid", False)` to prevent entry when tick data is missing.
4. In `backtester.py:675-676`, change `>=` and `<=` to strict `>` and `<` to prevent dojis from triggering directional entries.

---

## 5. Verification Method

To independently verify after changes are made:
1. Run `pytest tests/test_hyper_predator.py -v` — must pass 40/40 tests.
2. Run `pytest tests/` — must pass all 107 tests with exit code 0.
3. Inspect `hyper_predator_bot.py` lines 667, 871, and 908-925 to verify safe defaults and fill price propagation.
4. Inspect `backtester.py` lines 675-676 to confirm doji strict inequality.
