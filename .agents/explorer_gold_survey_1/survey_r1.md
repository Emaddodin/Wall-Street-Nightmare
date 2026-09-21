# Survey R1: Hyperliquid DEX Asynchronous Execution Bridge & Order Slicing

**Author**: `explorer_gold_survey_1`  
**Date**: 2026-09-17T19:17:00Z  
**Target Specification**: `/Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md` (R1)  
**Parent Orchestrator**: `orchestrator_2` (convId: `d8cde56b-142d-4ad0-b360-6f180e2c8eaa`)  

---

## Executive Summary

This survey evaluates the implementation state of **Requirement R1 (Hyperliquid DEX Asynchronous Execution Bridge & Order Slicing)** across `engine/execution_router.py`, `live_hyperliquid.py`, `engine/fsm.py`, and the verification suite in `tests/test_gold_relapse_scalper.py`.

The recent architectural refinement in `engine/execution_router.py` establishes the core Hyperliquid Central Limit Order Book (CLOB) execution primitives:
1. **Leverage & Margin Ceiling**: 100x leverage on Hyperliquid GOLD perpetuals (`(sz * px) / leverage`), strictly enforcing initial margin $\le 20\%$ of account equity ($13.00 max on a $65.00 account).
2. **Stop-Loss Envelope (Absolute Delta)**: Strictly $1.00 to $1.50 ($10.0 to 15.0 pips) absolute delta from entry price, placed $0.10 to $0.15 (default $0.12) beyond the invalidation wick; trades requiring $> \$1.50$ SL are rejected.
3. **Open-Ended Slicing (No Static TP)**: Layered dispatch of 3 micro-unit slices via `asyncio.gather` with 50ms stagger jitter, with `take_profit = None` (no resting TP on CLOB).
4. **Detached Stop Mechanism**: Unified `market_close()` Stop Market order placed with `reduce_only=True` immediately following slice fills.
5. **Breakeven Lock**: At $+1.5R$ floating profit, cancels old stop order and places a new `reduce_only=True` stop at Entry $\pm \$0.10$.
6. **Dynamic Basket Close**: Dynamic liquidation via `exchange.market_close(sz=total_sz, reduce_only=True)` upon receiving an EXIT flag, cancelling resting stops without orphan orders.

All 10 tests in `tests/test_gold_relapse_scalper.py` are **currently passing cleanly** (`10 passed in 1.92s`).

However, our investigation identifies one major production implementation gap: **the lack of a concrete live/testnet `HyperliquidDEXVenue` adapter** wrapping the `hyperliquid-python-sdk` (`Exchange` and `Info`) to interface with `https://api.hyperliquid-testnet.xyz` and `https://api.hyperliquid.xyz`.

---

## 1. Hyperliquid DEX Connection State & Architecture

### 1.1 Existing Protocol Definition (`HyperliquidVenue`)
Located in `engine/execution_router.py:174-218`:
```python
class HyperliquidVenue(Protocol):
    async def get_equity(self) -> float: ...
    async def get_market_price(self, coin: str) -> float: ...
    async def market_open(self, coin: str, is_buy: bool, sz: float, px: Optional[float] = None, slippage: float = 0.01) -> Dict[str, Any]: ...
    async def market_close(self, coin: str, sz: Optional[float] = None, px: Optional[float] = None, slippage: float = 0.01, trigger_px: Optional[float] = None, reduce_only: bool = True) -> Dict[str, Any]: ...
    async def cancel(self, coin: str, oid: str) -> bool: ...

BrokerVenue = HyperliquidVenue  # Backward compatibility alias
```

### 1.2 Mock / Paper Mode (`SimulatedBrokerVenue`)
Located in `engine/execution_router.py:221-309`:
- Simulates execution with configurable `slippage_delta` (default `$0.02`).
- Maintains in-memory order tracking (`self._orders`) and resting trigger stops (`self._resting_stops`).
- Implements `market_close` dual behavior:
  - When `trigger_px` is provided: registers a resting stop-market order (`status="resting"`, `reduce_only=True`).
  - When `trigger_px is None`: executes immediate market liquidation at current market price.
- Supports `cancel(coin, oid)` to mark resting stops as `"cancelled"`.

### 1.3 Production Gap: Missing Concrete Live/Testnet Venue Adapter
`engine/execution_router.py` contains only `SimulatedBrokerVenue`. To connect to live Hyperliquid testnet or mainnet, a concrete class `HyperliquidDEXVenue` must be implemented.

#### SDK Availability & Inspection
The official SDK (`hyperliquid-python-sdk`, version 0.15+) and `eth-account` are installed in the Python environment:
- Endpoints:
  - Testnet: `hl_constants.TESTNET_API_URL` = `https://api.hyperliquid-testnet.xyz`
  - Mainnet: `hl_constants.MAINNET_API_URL` = `https://api.hyperliquid.xyz`
- SDK Core Classes:
  - `hyperliquid.info.Info(base_url, skip_ws=True)`
  - `hyperliquid.exchange.Exchange(wallet, base_url, account_address=address)`
  - `eth_account.Account.from_key(secret_key)`

#### API Contract Mapping for `HyperliquidDEXVenue`
| Protocol Method | Hyperliquid SDK Call | Details & Formatting |
|---|---|---|
| `get_equity()` | `await asyncio.to_thread(self.info.user_state, self.address)` | Extract `float(res["marginSummary"]["accountValue"])` |
| `get_market_price(coin)` | `await asyncio.to_thread(self.info.all_mids)` | Extract `float(mids.get(coin, 0.0))` or top of L2 book |
| `market_open(coin, is_buy, sz, slippage)` | `await asyncio.to_thread(self.exchange.market_open, coin, is_buy, sz, slippage=slippage)` | Aggressive IOC limit order on CLOB |
| `market_close(..., trigger_px=...)` | `await asyncio.to_thread(self.exchange.order, coin, is_buy, sz, trigger_px, {"trigger": {"triggerPx": trigger_px, "isMarket": True, "tpsl": "sl"}}, reduce_only=True)` | Detached Stop Market resting on CLOB |
| `market_close(..., trigger_px=None)` | `await asyncio.to_thread(self.exchange.market_close, coin, sz=sz, slippage=slippage)` | Immediate market liquidation with `reduce_only=True` |
| `cancel(coin, oid)` | `await asyncio.to_thread(self.exchange.cancel, coin, int(oid))` | Programmatic stop cancellation |

*Key Implementation Note*: The official Hyperliquid Python SDK methods are synchronous HTTP requests (`requests`). Therefore, inside the `async` methods of `HyperliquidDEXVenue`, SDK calls must be wrapped using `asyncio.to_thread()` or run on an executor to prevent blocking the async event loop!

---

## 2. Asynchronous Order Slicing (`fire_layered_orders`)

### 2.1 Implementation Analysis
Located in `engine/execution_router.py:432-588`:
```python
async def fire_layered_orders(
    self,
    symbol: Optional[str] = None,
    side: Optional[OrderSide] = None,
    invalidation_wick_price: float = 0.0,
    total_lots: Optional[float] = None,
    total_sz: float = 3.0,
    num_slices: int = 3,
    tier: int = 1,
    is_buy: Optional[bool] = None,
) -> Optional[OrderBasket]:
```

1. **Pre-flight Invariant Validation**:
   - Margin check: `validate_margin(effective_sz, current_price)` validates margin $\le 20\%$ equity.
   - SL envelope check: `calculate_and_validate_sl(is_buy, current_price, invalidation_wick_price)` ensures delta is within $\$1.00 - \$1.50$.
2. **Slice Partitioning**:
   - Computes `slice_sz = round(effective_sz / num_slices, 2)` (e.g., $0.45 / 3 = 0.15$).
3. **Concurrent Layering with 50ms Stagger Jitter**:
   ```python
   async def _dispatch_single_slice(idx: int) -> OrderSlice:
       if idx > 0:
           delay_s = (idx * self.risk.slice_jitter_ms) / 1000.0
           await asyncio.sleep(delay_s)
       res = await self.venue.market_open(coin=coin, is_buy=is_buy, sz=slice_sz)
       ...
   tasks = [_dispatch_single_slice(i) for i in range(num_slices)]
   slices = await asyncio.gather(*tasks)
   ```
4. **VWAP Entry Calculation**:
   - Computes weighted average entry price:
     $$\text{entry\_price} = \frac{\sum (s.\text{entry\_price} \times s.\text{sz})}{\sum s.\text{sz}}$$
5. **Detached Stop Placement**:
   - Immediately dispatches unified stop market order for aggregate size:
     `self.venue.market_close(coin=coin, sz=basket.total_sz, trigger_px=basket.sl_price, reduce_only=True)`
   - Binds `basket.stop_order_id`.

### 2.2 Slicing Evaluation & Edge Cases
- **Stagger Jitter**: Linearly staggered at $0\text{ms}, 50\text{ms}, 100\text{ms}$. Validated in `tests/test_gold_relapse_scalper.py::test_order_slicing_and_detached_stop`.
- **Fault-Tolerance Opportunity**: Currently, `asyncio.gather(*tasks)` does not pass `return_exceptions=True`. If slice 2 fails due to a temporary network timeout, an unhandled exception will escape and leave slice 0 and slice 1 unhedged without a detached stop.
  *Recommendation*: Wrap slice dispatch with `return_exceptions=True`, aggregate all successfully filled slices, and dispatch the detached stop order sized to the actual filled `aggregate_sz`.

---

## 3. Risk Invariants & Mathematical Enforcement

### 3.1 Leverage & Margin Invariant
- **Model**: `RiskInvariants(leverage=100.0, max_margin_pct=0.20, initial_account_equity=65.0)`
- **Mathematical Formula**:
  $$\text{Required Margin} = \frac{\text{sz} \times \text{Price}}{\text{Leverage}}$$
  $$\text{Max Allowed Margin} = \text{Equity} \times \text{max\_margin\_pct} = \$65.00 \times 0.20 = \$13.00$$
- **Enforcement Benchmark** (at Gold Price = $\$2,500.00$):
  - $\text{sz} = 0.50$: $\text{Margin} = \frac{0.50 \times 2500}{100} = \$12.50 \le \$13.00 \implies$ **VALID**
  - $\text{sz} = 1.00$: $\text{Margin} = \frac{1.00 \times 2500}{100} = \$25.00 > \$13.00 \implies$ **REJECTED**
- **Forex 1:1000 vs Hyperliquid 100x**:
  - In MT5 forex specifications, 1 lot = 100 oz. 0.03 lots = 3.0 oz. At 1:1000 leverage: $\frac{3.0 \times 2500}{1000} = \$7.50$.
  - On Hyperliquid DEX CLOB, GOLD perpetual contract max leverage is 100x. Sizing $\text{sz} = 0.45$ ($3 \times 0.15$ slices) at 100x leverage requires $\frac{0.45 \times 2500}{100} = \$11.25 \le \$13.00$.
  - The parameter `leverage` in `RiskInvariants` is fully configurable (supports both 100.0 and 1000.0).

### 3.2 Stop-Loss Envelope Invariant
Located in `engine/execution_router.py:374-427`:
- **Wick Buffer**: Default $\$0.12$ ($1.2$ pips) beyond the invalidation wick.
- **Rule Set**:
  - **Long Entry**: $\text{raw\_sl} = \text{wick\_price} - 0.12$, $\text{sl\_delta} = \text{entry\_price} - \text{raw\_sl}$.
  - **Short Entry**: $\text{raw\_sl} = \text{wick\_price} + 0.12$, $\text{sl\_delta} = \text{raw\_sl} - \text{entry\_price}$.
  - If $\text{sl\_delta} > \$1.50$ ($15.0$ pips): **REJECT** trade immediately.
  - If $\text{sl\_delta} < \$1.00$ ($10.0$ pips): **CLAMP** to minimum $\$1.00$ delta ($\text{sl\_price} = \text{entry} \pm \$1.00$).
  - If $\$1.00 \le \text{sl\_delta} \le \$1.50$: **ACCEPT** $\text{sl\_price} = \text{raw\_sl}$.

### 3.3 No Static Take-Profit Invariant (`take_profit = None`)
- On Hyperliquid CLOB, no resting limit take-profit orders are dispatched.
- Positions are held open-ended until closed dynamically by:
  1. Detached Stop Market execution on CLOB.
  2. Sub-second Local LLM Intuition exit signal.
  3. FSM Opposing Reversal pattern (Evening Star / Bearish Engulfing).
  4. Daily Drawdown Killswitch (5%).

### 3.4 Breakeven Lock Mechanism (+1.5R)
Located in `engine/execution_router.py:594-657`:
- Calculated via `calculate_unrealized_pnl(current_price)`:
  $$\text{unrealized\_r} = \frac{\text{price\_diff}}{\text{risk\_r\_dist}}$$
- Condition: $\text{unrealized\_r} \ge 1.5$ (where 1R is the SL delta between $\$1.00$ and $\$1.50$).
- Action Sequence:
  1. Programmatically cancel existing structural stop on CLOB: `await self.venue.cancel(basket.coin, basket.stop_order_id)`.
  2. Compute new protective price: $\text{Entry Price} \pm \$0.10$ ($+1.0$ pip in profit).
  3. Transmit new detached stop market order with `reduce_only=True`:
     `new_stop = await self.venue.market_close(coin=basket.coin, sz=basket.current_sz, trigger_px=new_sl, reduce_only=True)`.
  4. Set `basket.breakeven_locked = True`.

---

## 4. Parallel Market Close Across Active Tickets

### 4.1 Implementation in `close_basket`
Located in `engine/execution_router.py:697-774`:
- Triggered by `FSM` transition to `EXIT_SIGNAL` or direct invocation from the LLM intuition hook.
- Sequence:
  1. **Stop Cleanup**: Cancels resting detached stop market order via `self.venue.cancel(basket.coin, basket.stop_order_id)` to eliminate orphan stop orders.
  2. **Position Liquidation**: Dispatches `self.venue.market_close(coin=basket.coin, sz=aggregate_sz, reduce_only=True)`.
  3. **State Transition**: Sets all slice states to `is_active = False`, sets `basket.is_active = False`, resets `active_basket_id = None`.
  4. **Telemetry & Accounting**: Calculates realized PnL, emits `DYNAMIC_BASKET_CLOSED` structured JSON telemetry, and returns the liquidation summary.

### 4.2 CLOB vs Sliced Liquidation Considerations
- On Hyperliquid DEX, a user's balance is tracked as a single net position per coin. An aggregate `market_close(sz=aggregate_sz, reduce_only=True)` is the cleanest, lowest-latency, and most gas/fee-efficient method.
- `close_basket()` guarantees that no resting trigger order is left orphaned after the position is closed.

---

## 5. Test Coverage & Gap Analysis (`tests/test_gold_relapse_scalper.py`)

### 5.1 Current Test Execution Status
Command: `pytest tests/test_gold_relapse_scalper.py -v`
Result: **10 passed in 1.92s**

| Test Name | Validated Behavior | Status |
|---|---|---|
| `test_margin_invariant` | 100x leverage margin calculation, 20% equity ceiling ($13 on $65), rejection of sz=1.0 | **PASS** |
| `test_stop_loss_envelope_invariant` | > $1.50 rejection, $1.00-$1.50 acceptance, < $1.00 clamping to $1.00 | **PASS** |
| `test_order_slicing_and_detached_stop` | 3 slices with 50ms jitter via `asyncio.gather`, no static TP, detached stop with `reduce_only=True` | **PASS** |
| `test_breakeven_lock_at_1_5r` | No trigger at +0.76R, triggers at +1.89R, cancels old stop, places new stop at Entry+$0.10 | **PASS** |
| `test_dynamic_basket_close` | Liquidation of aggregate size, cancels resting stop, calculates PnL | **PASS** |
| `test_macro_calendar_blackout` | +/- 15 min blackout window around High-Impact news events | **PASS** |
| `test_slm_intuition_exit_mocked` | Grammar-constrained sub-300ms query & algorithmic fail-safe timeout fallback | **PASS** |
| `test_daily_drawdown_killswitch` | 5% equity drawdown trips killswitch ($65 -> $61.50) | **PASS** |
| `test_relapse_fsm_lifecycle` | IDLE -> WAITING -> TRIGGER -> IN_TRADE -> EXIT -> IDLE | **PASS** |
| `test_candlestick_and_ict_integration` | Morning Star, Evening Star, Engulfing, ICT FVG detection | **PASS** |

### 5.2 Identified Test & Implementation Gaps for R1

1. **Short / Sell Direction Invariants**:
   - `test_stop_loss_envelope_invariant` and `test_breakeven_lock_at_1_5r` only test `is_buy=True` (Long).
   - *Action*: Add unit tests for `is_buy=False` (Short) validating $\text{sl\_price} = \text{wick} + 0.12$, clamping, and breakeven stop at $\text{Entry} - \$0.10$.
2. **Concrete Live/Testnet Venue Adapter Unit Tests**:
   - Currently, tests only run against `SimulatedBrokerVenue`.
   - *Action*: Create mock-based unit tests for `HyperliquidDEXVenue` verifying that calls to `market_open`, `order`, and `cancel` properly format EIP-712 / Hyperliquid REST payloads and interact with `hyperliquid.exchange.Exchange`.
3. **Slice Failure & Partial Fill Hedging**:
   - If one of the 3 slices fails during `fire_layered_orders`, verify that the system either cancels remaining slices or sizes the detached stop to the exact filled amount.
4. **Idempotency on Multiple Close Invocations**:
   - Verify that calling `close_basket()` when no basket is active or twice in rapid succession does not raise an exception or attempt double liquidation.

---

## 6. Synthesis & Next Step Recommendations

| Priority | Component | Action Item | Target File |
|---|---|---|---|
| **High** | DEX Live Adapter | Implement `HyperliquidDEXVenue` conforming to `HyperliquidVenue` protocol, supporting testnet and mainnet configurations via `hyperliquid-python-sdk` | `engine/execution_router.py` |
| **High** | Test Suite Expansion | Add test cases for Short (SELL) SL envelope, Short breakeven lock, and live venue mock tests | `tests/test_gold_relapse_scalper.py` |
| **Medium** | Partial Fill Slicing | Wrap `_dispatch_single_slice` tasks with `return_exceptions=True` and adjust detached stop size to filled quantity | `engine/execution_router.py` |
| **Low** | Randomized Jitter | Allow configurable randomized jitter (e.g. $50\text{ms} \pm 10\text{ms}$) in addition to linear stagger | `engine/execution_router.py` |
