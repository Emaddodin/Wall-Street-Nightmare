# Orderflow Math & Risk Review Report — Hyper Predator Bot & Decade Backtester

**Reviewer**: Reviewer 2 (Orderflow Math & Risk Reviewer)  
**Date**: 2026-09-19  
**Review Target**:
- `hyper_predator_bot.py`
- `backtester.py`
- `tests/test_hyper_predator.py`
- `tests/test_adversarial_predator_stress.py`
- Repository Structure & `_archive/` Quarantine

---

## 1. Review Summary

**Verdict: REQUEST_CHANGES**

While the core mathematical formulas for M1 rejection wicks, rolling M5 S/R pivots, tick velocity surges, and top-5 L2 orderbook imbalances are rigorously formulated, a full repository test execution (`pytest tests/`) reveals a test failure in `tests/test_adversarial_predator_stress.py`. Furthermore, an adversarial audit revealed a critical order-tracking limitation in `close_basket` and a bypass loophole in tick velocity gating when tick buffers are uninitialized.

### Test Execution Summary
- `pytest tests/test_hyper_predator.py -v`: **40 PASSED in 8.94s** (100% Pass Rate).
- `pytest tests/`: **106 PASSED, 1 FAILED in 13.88s** (Exit Code 1).
  - Failed test: `tests/test_adversarial_predator_stress.py::TestStress2RapidReversalsAndMarketShocks::test_instant_minus_10_loss_drop_long_and_short`.

---

## 2. Findings

### [Critical] Finding 1: Repository Test Failure & Execution Price Tracking under Hard Equity Shield Liquidation
- **Where**:
  - `hyper_predator_bot.py:908-925` (`ExecutionBridge.close_basket`)
  - `tests/test_adversarial_predator_stress.py:206-233`
- **What**:
  Running `pytest tests/` fails with:
  ```text
  FAILED tests/test_adversarial_predator_stress.py::TestStress2RapidReversalsAndMarketShocks::test_instant_minus_10_loss_drop_long_and_short
  AssertionError: assert 2500.0 == 2479.0
  where 2500.0 = PredatorOrderSlice(..., close_price=2500.0, ...).close_price
  ```
- **Why**:
  1. In `hyper_predator_bot.py`, `ExecutionBridge.close_basket` is defined as:
     ```python
     async def close_basket(self, coin: str = "GOLD", reason: str = "TARGET") -> Optional[Dict[str, Any]]:
     ```
     When liquidating an active basket, `close_basket` does not accept an execution price argument (`exit_price: Optional[float] = None`), nor does it query `TapeBookMemory` for the prevailing best bid/ask. It dispatches:
     ```python
     close_res = await self.venue.market_close(coin=coin, sz=aggregate_sz, reduce_only=True)
     exit_px = close_res.get("fill_price", await self.venue.get_market_price(coin))
     ```
     Because `market_close` is called with `px=None`, `SimulatedBrokerVenue` falls back to `self._prices.get(coin)`, which remained at the initial entry price ($2500.00). Consequently, `s.close_price` was marked as $2500.00 instead of the actual liquidation price ($2479.00), and the realized PnL was incorrectly recorded near $0.00 instead of -$10.50.
  2. Simultaneously, in `test_adversarial_predator_stress.py`, lines 206-220, the test updated `TapeBookMemory.best_bid = 2479.00` but omitted calling `venue.set_market_price("GOLD", 2479.00)` (which was correctly called in the short test at line 247).
- **Suggestion**:
  - Update `close_basket` to optionally accept `exit_price: Optional[float] = None` (or allow passing `px` through to `self.venue.market_close(coin=coin, sz=aggregate_sz, px=exit_price, reduce_only=True)`).
  - In `orderflow_exit_monitor`, pass the triggering evaluation price (`eval_price`) to `close_callback(reason, eval_price)`.
  - In `tests/test_adversarial_predator_stress.py:206`, ensure `venue.set_market_price("GOLD", 2479.00)` is updated or `close_basket` correctly receives the simulated fill price.

---

### [Major] Finding 2: Tick Velocity Edge Fallback Default Bypass on Missing Ticks
- **Where**:
  - `hyper_predator_bot.py:659-671` (`SniperEngine.evaluate_m1_trigger`)
- **What**:
  When `tick_timestamps` is empty or None, `evaluate_m1_trigger` falls back to:
  ```python
  vel_ok = candle.get("velocity_surge_valid", True)
  vel_ratio = candle.get("velocity_surge_ratio", 1.6)
  if not vel_ok:
      return None
  ```
- **Why**:
  If the bot has just started or WebSocket tick feeds experience a transient blackout, `tick_timestamps` is empty. Because `candle.get("velocity_surge_valid", True)` defaults to `True`, the sniper engine will trigger a trade entry without verifying the 1.5x tick velocity surge.
  This violates Requirement R2:
  *"Tick velocity calculation strictly gates entries unless the final 5s volume/tick rate is > 1.5x the rolling baseline."*
- **Suggestion**:
  In `hyper_predator_bot.py:667`, change the default to `False`:
  ```python
  vel_ok = candle.get("velocity_surge_valid", False)
  vel_ratio = candle.get("velocity_surge_ratio", 0.0)
  ```
  This ensures that live entries are strictly gated unless tick velocity is explicitly proven and measured.

---

### [Minor] Finding 3: Doji Candle Body Inconsistency between Backtester and Bot
- **Where**:
  - `backtester.py:675-676` (`VectorizedSignalEngine.compute_wick_ratios`)
  - `hyper_predator_bot.py:579, 587` (`SniperEngine.compute_rejection_wick`)
- **What**:
  In `backtester.py`, candle direction is defined as:
  ```python
  is_bullish_candle = closes >= opens
  is_bearish_candle = closes <= opens
  ```
  In `hyper_predator_bot.py`, candle direction is strictly:
  ```python
  is_bullish_close = (c > o)
  is_bearish_close = (c < o)
  ```
- **Why**:
  For a doji candle where `Close == Open`, `backtester.py` evaluates both `is_bullish_candle` and `is_bearish_candle` as `True`. If a doji prints at support with a large lower wick, the backtester considers it a valid bullish signal, whereas `hyper_predator_bot.py` rejects it because the body did not close strictly in the direction of the bias ($C > O$).
- **Suggestion**:
  Align `backtester.py:675-676` with `hyper_predator_bot.py` by requiring strict inequality:
  ```python
  is_bullish_candle = closes > opens
  is_bearish_candle = closes < opens
  ```

---

## 3. Verified Mathematical & Microstructural Invariants

| Invariant | Specification | Code Implementation | Verification Status | Evidence / Notes |
|---|---|---|:---:|---|
| **M1 Lower Rejection Wick (Long)** | $\frac{\min(O, C) - L}{H - L} \ge 0.65, C > O$ | `hyper_predator_bot.py:577-582` | **VERIFIED** | Lower wick = $O - L$ (since $C > O$). Invalidation price = $L$. Exact 65.0% and 90.0% verified; 64.5% rejected. |
| **M1 Upper Rejection Wick (Short)** | $\frac{H - \max(O, C)}{H - L} \ge 0.65, C < O$ | `hyper_predator_bot.py:584-590` | **VERIFIED** | Upper wick = $H - O$ (since $C < O$). Invalidation price = $H$. Rejection wicks opposing macro bias are strictly rejected. |
| **Zero-Range Candle Guard** | Guard $H - L \le 0$ against zero division | `hyper_predator_bot.py:573` | **VERIFIED** | Returns `False, 0.0, lo` if $H - L \le 10^{-6}$. |
| **Final 5s Tick Velocity Surge** | $\frac{v_{\text{surge}}}{\max(v_{\text{base}}, 0.1)} \ge 1.50$ | `hyper_predator_bot.py:611-620` | **VERIFIED** | $v_{\text{base}} = \frac{n_{\text{base}}}{55.0}$, $v_{\text{surge}} = \frac{n_{\text{surge}}}{5.0}$. Smooth rolling baseline update $\lambda_{\text{EMA}} = 0.9 v_{\text{old}} + 0.1 v_{\text{base}}$. |
| **Detached Stop Placement** | Exactly $\$1.00$ beyond invalidation wick | `hyper_predator_bot.py:766-772` | **VERIFIED** | Long: $L - 1.00$. Short: $H + 1.00$. Placed via `market_close(trigger_px=sl_price, reduce_only=True)`. |
| **100x Leverage & Margin Ceiling** | Initial margin $\le 20\%$ equity ($13 on $65) | `hyper_predator_bot.py:711-720` | **VERIFIED** | Margin = $\frac{\text{size} \cdot \text{px}}{100.0}$. Rejects orders exceeding $13.00. Automatic size capping in `on_m1_candle_close`. |
| **5-Slice Jitter Spreading** | 5 micro-slices with 20ms stagger jitter | `hyper_predator_bot.py:793-815` | **VERIFIED** | Dispatched via `asyncio.gather` with delays `[0ms, 20ms, 40ms, 60ms, 80ms]`. |
| **Opposing M5 S/R Target Exit** | Close basket when bid/ask reaches target | `hyper_predator_bot.py:1060-1081` | **VERIFIED** | Long: `best_bid >= target_price`. Short: `best_ask <= target_price`. |
| **Opposing Reversal Wick Exit** | Close basket on opposing $\ge 65\%$ M1 wick | `hyper_predator_bot.py:1298-1310` | **VERIFIED** | Immediate market exit on candle close when opposing rejection wick prints. |
| **Hard Equity Shield Liquidation** | Floating uPnL $\le -\$10.00$ | `hyper_predator_bot.py:1046-1059` | **VERIFIED** | Evaluated continuously in memory; triggers `HARD_EQUITY_SHIELD` liquidation. |
| **Top-5 L2 Book Imbalance** | Ratio $> 3.0 \cdot \text{volatility\_regime}$ | `hyper_predator_bot.py:1083-1110` | **VERIFIED** | Long: $\frac{\text{Ask Vol}}{\text{Bid Vol}} > 3.0 \cdot \text{vol\_regime}$. Short: $\frac{\text{Bid Vol}}{\text{Ask Vol}} > 3.0 \cdot \text{vol\_regime}$. |
| **Trade Tape Delta Stall** | $> 80\%$ opposing fills in last 20 ticks | `hyper_predator_bot.py:1112-1125` | **VERIFIED** | Evaluated only when basket is in profit (`floating_pnl > 0`). Requires $\ge 10$ ticks. |
| **Microstructure Memory Latency** | Pure in-memory evaluation $< 5\text{ms}$ | `test_hyper_predator.py:667-687` | **VERIFIED** | Measured mean latency $< 0.02\text{ms}$ across 1,000 iterations. |

---

## 4. Repository Purge Verification

- Verified active root directory:
  - Clean active root contains only active components: `hyper_predator_bot.py`, `backtester.py`, `engine/`, `macro/`, `deploy/`, `tests/`.
  - Obsolete legacy MT5 connectors, obsolete scrapers, and deprecated trading loops are quarantined in `_archive/` (`_archive/legacy_bots`, `_archive/quant`, `_archive/scanners`, `_archive/signals`, `_archive/tools`, `_archive/tests`).
- Codebase is strictly dedicated to `GOLD` execution on Hyperliquid CLOB with zero multi-symbol routing overhead.

---

## 5. Adversarial Stress & Attack Surface Analysis

1. **Adversarial Market Shocks (Extreme Gaps & Slippage)**:
   - In the event of a sudden gap past the $-\$10.00$ Hard Equity Shield or the detached stop-loss price, `SimulatedBrokerVenue` fills at the best available bid/ask. In `backtester.py`, worst-case intra-bar low/high execution is modeled with `min(px_open, shield_px)` to account for gap openings.
2. **LLM Downtime and Corrupted Telemetry**:
   - `update_macro_edge` enforces a strict 500ms timeout with `ClientTimeout(total=0.500)`. If the local `llama.cpp` service fails, times out, or returns invalid markdown or non-JSON payloads, `MacroStateManager.hold_safe_state()` is invoked, atomically setting `permit_trade = False` while preserving the previous regime. Core 2 execution is never blocked.
3. **Overlapping Basket Prevention**:
   - `ExecutionBridge.spam_orders` acquires `async with self._lock:` and checks `if self.active_basket and self.active_basket.is_active: return None`. Overlapping entries are prevented.

---

## 6. Actionable Fix Recommendations

1. **Fix Failed Test in `tests/test_adversarial_predator_stress.py`**:
   In `tests/test_adversarial_predator_stress.py:206`, set the market price on the simulated venue to match the shock price:
   ```python
   venue.set_market_price("GOLD", 2479.00)
   ```
2. **Propagate Execution Fill Price in `ExecutionBridge.close_basket`**:
   Allow `close_basket` to accept an optional `exit_price: Optional[float] = None` and pass it to `venue.market_close(coin=coin, sz=aggregate_sz, px=exit_price, reduce_only=True)` so that liquidation fill prices are accurately recorded on slices and realized PnL.
3. **Tighten Tick Velocity Default Gate**:
   In `hyper_predator_bot.py:667`, change `candle.get("velocity_surge_valid", True)` to `False` to prevent entering trades when tick velocity cannot be determined.
4. **Align Doji Body Direction in `backtester.py`**:
   In `backtester.py:675-676`, change `>=` / `<=` to `>` / `<`.
