# Code Changes — Milestone M1 (worker_gold_m1)

## Overview
Implemented `HyperliquidDEXVenue` conforming to the `HyperliquidVenue` protocol in `engine/execution_router.py` using `asyncio.to_thread()` to execute all synchronous network SDK methods without blocking the event loop. Expanded `tests/test_gold_relapse_scalper.py` with 3 comprehensive new tests covering Short SL envelope invariant, Short breakeven lock at +1.5R, and `HyperliquidDEXVenue` asynchronous execution with mocked SDK. All 13 tests pass with 100% pass rate and 0 lint violations.

---

## 1. `engine/execution_router.py`

### Changes Made:
1. **SDK Imports & Fallbacks**:
   - Imported `os` and `runtime_checkable`.
   - Imported `Exchange`, `Info`, and `hl_constants` from `hyperliquid-python-sdk` with graceful fallback if unavailable.
   - Imported `Account as EthAccount` from `eth_account`.
   - Removed unused `asdict` import.
2. **Protocol Definition**:
   - Added `@runtime_checkable` decorator to `class HyperliquidVenue(Protocol):` to allow runtime `isinstance(obj, HyperliquidVenue)` checks.
3. **`HyperliquidDEXVenue` Implementation**:
   - Implemented `HyperliquidDEXVenue` conforming to `HyperliquidVenue`:
     - `__init__`: Accepts credentials (`secret_key`, `account_address`), `testnet` flag (defaults to True), `base_url`, `default_equity`, and dependency injection parameters (`exchange`, `info`). Automatically resolves `hl_constants.TESTNET_API_URL` vs `hl_constants.MAINNET_API_URL`.
     - `async get_equity()`: Dispatches to `_sync_get_equity` via `asyncio.to_thread()`. Parses `user_state(account_address)` marginSummary/crossMarginSummary `accountValue` or `withdrawable`, falling back to `default_equity`.
     - `async get_market_price(coin)`: Dispatches to `_sync_get_market_price` via `asyncio.to_thread()`. Fetches mid price from `info.all_mids()`.
     - `async market_open(...)`: Dispatches to `_sync_market_open` via `asyncio.to_thread()`. Calls `exchange.market_open(...)` and extracts fill price, order ID, and ensures `take_profit=None`.
     - `async market_close(...)`: Dispatches to `_sync_market_close` via `asyncio.to_thread()`.
       - If `trigger_px` is provided: builds trigger stop order with `triggerPx`, `isMarket=True`, `tpsl="sl"`, and dispatches via `exchange.order(..., reduce_only=reduce_only)`. Correctly determines buy/sell direction based on position or price.
       - If `trigger_px` is None: executes immediate market liquidation via `exchange.market_close(...)` with `reduce_only=reduce_only`.
     - `async cancel(coin, oid)`: Dispatches to `_sync_cancel` via `asyncio.to_thread()`. Calls `exchange.cancel(...)` with integer order ID conversion or fallback.

### Rationale:
- `hyperliquid-python-sdk` performs blocking HTTP requests using `requests.Session`. Executing these inside an async event loop would degrade execution latency and stall the high-frequency tape feedback and LLM intuition exit monitoring. Wrapping every synchronous network call in `asyncio.to_thread()` offloads I/O to a background thread pool without blocking the main event loop.
- Providing direct injection for `exchange` and `info` allows deterministic, hermetic unit and integration testing without requiring live network calls.

---

## 2. `tests/test_gold_relapse_scalper.py`

### Changes Made:
1. **Imports**:
   - Imported `HyperliquidDEXVenue` and `HyperliquidVenue`.
   - Cleaned up unused imports (`Any`, `Dict`, `patch`, `np`, `OrderBasket`, `OrderSlice`) and resolved all flake8 warnings.
2. **New Test Case: `test_short_stop_loss_envelope_invariant`**:
   - Verifies Short (`is_buy=False`) SL envelope calculations:
     - Case A: Rejection when setup requires delta > $1.50 (invalidation high wick too far).
     - Case B: Acceptance when delta is between $1.00 and $1.50, placing stop $0.12 beyond the high wick.
     - Case C: Clamping to minimum $1.00 delta when wick is tighter than $1.00.
3. **New Test Case: `test_short_breakeven_lock_at_1_5r`**:
   - Dispatches a Short order basket (`is_buy=False`, side=OrderSide.SELL) on `SimulatedBrokerVenue`.
   - Verifies that floating profit < +1.5R does not trigger breakeven lock.
   - Verifies that when price drops past +1.5R floating profit:
     - The old structural stop order is cancelled.
     - A new detached stop order is transmitted at `Entry Price - $0.10` ($2499.90 on $2500.00 entry).
     - The new stop order has `reduce_only=True`.
4. **New Test Case: `test_hyperliquid_dex_venue_async_execution`**:
   - Instantiates `HyperliquidDEXVenue` with mocked `Exchange` and `Info` instances.
   - Verifies runtime protocol conformance (`isinstance(dex_venue, HyperliquidVenue)`).
   - Verifies testnet (`testnet.xyz`) vs mainnet URL configuration.
   - Tests async non-blocking execution of `get_equity()`, `get_market_price()`, `market_open()`, detached stop `market_close()`, immediate liquidation `market_close()`, and `cancel()`.
   - Tests full integration with `ExecutionRouter.fire_layered_orders()`, confirming 3-slice order dispatch with 50ms stagger jitter and detached reduce_only stop market order placement through the DEX venue.

---

## 3. Verification Commands and Results

```bash
# 1. Run unit and integration tests for Gold Relapse Scalper
pytest tests/test_gold_relapse_scalper.py -v
# Result: 13 passed in 2.53s (100% pass rate)

# 2. Run lint check
python3 -m flake8 engine/execution_router.py tests/test_gold_relapse_scalper.py --max-line-length=120 --ignore=E203,W503
# Result: 0 errors, 0 warnings

# 3. Check regression on neighboring test suites
pytest tests/test_hft_guard.py tests/test_alpha_setups.py -v
# Result: 39 passed in 2.18s
```
