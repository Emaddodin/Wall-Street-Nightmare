# Handoff Report: R1 Hyperliquid DEX Asynchronous Execution Bridge & Order Slicing

**Agent**: `explorer_gold_survey_1`  
**Date**: 2026-09-17T19:18:00Z  
**Recipient**: `orchestrator_2` (convId: `d8cde56b-142d-4ad0-b360-6f180e2c8eaa`)  
**Type**: Hard Handoff (Task Complete)  

---

## 1. Observation

1. **Protocol & Invariants in `engine/execution_router.py`**:
   - Lines 86-100: `RiskInvariants` defines `coin="GOLD"`, `leverage=100.0`, `max_margin_pct=0.20`, `min_sl_delta=1.00`, `max_sl_delta=1.50`, `wick_buffer=0.12`, `breakeven_trigger_r=1.5`, `breakeven_lock_offset=0.10`, `initial_account_equity=65.0`, `slice_count=3`, `slice_jitter_ms=50`.
   - Lines 174-218: `HyperliquidVenue` protocol specifies `get_equity()`, `get_market_price()`, `market_open()`, `market_close()`, and `cancel()`. `BrokerVenue = HyperliquidVenue` provides backward compatibility.
   - Lines 221-309: `SimulatedBrokerVenue` is implemented with order tracking, resting trigger stops, and slippage simulation.
   - Lines 341-372: `calculate_margin_required(sz, price)` computes `(sz * price) / leverage`. `validate_margin` asserts `required_margin <= equity * max_margin_pct` ($13.00 max on $65.00 account).
   - Lines 374-427: `calculate_and_validate_sl` rejects setups requiring $> \$1.50$ delta, clamps setups $< \$1.00$ to $\$1.00$ delta, and places SL $\$0.12$ beyond the invalidation wick.
   - Lines 432-588: `fire_layered_orders` concurrently dispatches 3 slices via `asyncio.gather` with `(idx * slice_jitter_ms) / 1000.0` delay and open-ended execution (`take_profit = None`), followed immediately by a detached unified stop market order with `reduce_only=True`.
   - Lines 594-657: `evaluate_breakeven_lock` triggers at `unrealized_r >= 1.5`, cancels the old stop order, and transmits a new stop order at Entry Price $\pm \$0.10$ with `reduce_only=True`.
   - Lines 697-774: `close_basket` cancels resting stops, liquidates aggregate size via `venue.market_close(sz=aggregate_sz, reduce_only=True)`, marks all slices inactive, and emits structured telemetry.

2. **Absence of Concrete Live/Testnet DEX Venue**:
   - Grep search for `TESTNET_API_URL` and `HyperliquidDEXVenue` across `engine/` returned 0 results.
   - In `live_hyperliquid.py:3091-3105`, live mode is initialized using `eth_account.Account.from_key(secret_key)` and `hyperliquid.exchange.Exchange(wallet, hl_constants.MAINNET_API_URL, account_address=address)`.
   - SDK methods in `hyperliquid.exchange.Exchange` (`market_open`, `market_close`, `order`, `cancel`) and `hyperliquid.info.Info` (`user_state`, `all_mids`) are synchronous HTTP calls.

3. **Current Test Suite Results**:
   - Running `pytest tests/test_gold_relapse_scalper.py -v`:
     ```
     tests/test_gold_relapse_scalper.py::test_margin_invariant PASSED         [ 10%]
     tests/test_gold_relapse_scalper.py::test_stop_loss_envelope_invariant PASSED [ 20%]
     tests/test_gold_relapse_scalper.py::test_order_slicing_and_detached_stop PASSED [ 30%]
     tests/test_gold_relapse_scalper.py::test_breakeven_lock_at_1_5r PASSED   [ 40%]
     tests/test_gold_relapse_scalper.py::test_dynamic_basket_close PASSED     [ 50%]
     tests/test_gold_relapse_scalper.py::test_macro_calendar_blackout PASSED  [ 60%]
     tests/test_gold_relapse_scalper.py::test_slm_intuition_exit_mocked PASSED [ 70%]
     tests/test_gold_relapse_scalper.py::test_daily_drawdown_killswitch PASSED [ 80%]
     tests/test_gold_relapse_scalper.py::test_relapse_fsm_lifecycle PASSED    [ 90%]
     tests/test_gold_relapse_scalper.py::test_candlestick_and_ict_integration PASSED [100%]
     ============================== 10 passed in 1.92s ==============================
     ```

---

## 2. Logic Chain

1. **Premise**: Requirement R1 dictates connecting the Relapse Scalper execution router to Hyperliquid DEX (testnet/mock mode by default, supporting live keys), implementing 3-ticket order slicing with 50ms jitter, enforcing 100x/1000x leverage and $\le 20\%$ initial margin, $\$1.00-\$1.50$ SL envelope, breakeven lock at $+1.5R$, open-ended dispatch (`take_profit = None`), and parallel market close on EXIT.
2. **Analysis of Existing Execution Engine**:
   - `engine/execution_router.py` accurately implements the algorithmic rules:
     - Slicing: `asyncio.gather(*tasks)` with 50ms stagger jitter.
     - Detached stop: immediate `market_close(trigger_px=sl_price, reduce_only=True)`.
     - Invariants: margin ceiling ($\le 20\%$ equity), SL envelope ($\$1.00-\$1.50$), breakeven lock ($+1.5R \implies \text{Entry} \pm \$0.10$), and basket close.
3. **Analysis of Live DEX Bridge**:
   - Observation 2 demonstrates that only `SimulatedBrokerVenue` is implemented in `engine/execution_router.py`. No concrete class exists to communicate over HTTP/WebSocket with `https://api.hyperliquid-testnet.xyz` or `https://api.hyperliquid.xyz`.
   - Therefore, while paper/simulated execution is 100% complete and passing tests, Milestone 1 must introduce `HyperliquidDEXVenue` wrapping `hyperliquid-python-sdk` to fulfill the live/testnet requirement.
4. **Analysis of Test Coverage**:
   - Observation 3 confirms all 10 current tests pass.
   - However, existing tests currently cover only the Long (`is_buy=True`) path for SL envelope and breakeven lock, and run exclusively against `SimulatedBrokerVenue`.
   - Therefore, additional test cases for Short positions and mocked live venue adapters are needed to prevent regressions when deploying live.

---

## 3. Caveats

1. **Leverage Scaling on Hyperliquid**:
   - While traditional forex platforms offer 1:1000 leverage for XAUUSD, Hyperliquid perpetual contracts cap maximum leverage for GOLD at 100x (or 50x depending on tier). `RiskInvariants` supports both via the `leverage` parameter; tests currently validate 100x leverage on $0.50 vs 1.00 sz ($12.50 vs $25.00 margin on $65 equity).
2. **Synchronous SDK Blocking**:
   - `hyperliquid-python-sdk` calls are synchronous. When implementing `HyperliquidDEXVenue`, executing calls without `asyncio.to_thread()` will block the event loop, causing latency spikes in high-frequency monitoring and LLM intuition exit evaluation.
3. **No Live Key Testing in Local Workspace**:
   - No active live testnet API keys were tested against live network endpoints during this read-only survey.

---

## 4. Conclusion

- **R1 State**: Algorithmic order slicing, risk invariants, breakeven lock, detached stop placement, and dynamic market close are **architecturally complete, refined, and validated by 10 passing unit tests**.
- **Key Implementation Work Remaining**:
  1. Implement `HyperliquidDEXVenue` conforming to `HyperliquidVenue` protocol in `engine/execution_router.py` using `asyncio.to_thread()` around `hyperliquid.exchange.Exchange` and `hyperliquid.info.Info`.
  2. Support seamless switching between mock mode (`SimulatedBrokerVenue`), testnet mode (`hl_constants.TESTNET_API_URL`), and live mode (`hl_constants.MAINNET_API_URL` with `secret_key`).
  3. Expand `tests/test_gold_relapse_scalper.py` to cover Short (`SELL`) SL calculations, Short breakeven locks, and mocked SDK adapter interactions.

---

## 5. Verification Method

To independently verify all findings and test execution:

```bash
cd /Users/mac/Desktop/TBT-Engine

# 1. Run the comprehensive Gold Relapse Scalper test suite
pytest tests/test_gold_relapse_scalper.py -v

# 2. Inspect execution router risk invariants and order slicing
grep -n "class RiskInvariants" engine/execution_router.py
grep -n "async def fire_layered_orders" engine/execution_router.py
grep -n "async def evaluate_breakeven_lock" engine/execution_router.py
grep -n "async def close_basket" engine/execution_router.py

# 3. Verify absence of concrete live DEX venue class
grep -n "class Hyperliquid" engine/execution_router.py

# 4. Verify Hyperliquid SDK availability in environment
python3 -c "import hyperliquid; from hyperliquid.utils import constants; print('SDK OK:', constants.TESTNET_API_URL)"
```

**Invalidation Conditions**:
- Any failure in `pytest tests/test_gold_relapse_scalper.py`.
- If `engine/execution_router.py` already contains a fully implemented live `HyperliquidDEXVenue` connecting to testnet.
