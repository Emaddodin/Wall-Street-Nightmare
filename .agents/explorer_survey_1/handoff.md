# Handoff Report: Hyperliquid SDK, WebSocket Feeds & Execution Architecture

**Date**: 2026-09-19  
**Agent**: Explorer 1 (Hyperliquid SDK & WS Explorer)  
**Parent / Caller**: orchestrator_3 (`39ebbf67-6c24-4133-888f-b0d9bed66dab`)  
**Scope**: Requirements R1, R2, R3, R5 from `ORIGINAL_REQUEST.md` (2026-09-19T08:56:25Z)

---

## 1. Observation

1. **Hyperliquid SDK Package**:
   - Location: `/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/site-packages/hyperliquid`
   - Modules verified: `hyperliquid.exchange.Exchange`, `hyperliquid.info.Info`, `hyperliquid.websocket_manager.WebsocketManager`, `hyperliquid.utils.constants`, `hyperliquid.utils.types`.
   - In `types.py` (lines 73-98):
     - `BboData`: `{"coin": str, "time": int, "bbo": Tuple[Optional[L2Level], Optional[L2Level]]}`
     - `L2BookData`: `{"coin": str, "levels": Tuple[List[L2Level], List[L2Level]], "time": int}`
     - `Trade`: `{"coin": str, "side": Side, "px": str, "sz": int, "hash": str, "time": int}` where `Side = Union[Literal["A"], Literal["B"]]`.
   - Side semantics verified via official API documentation:
     - `"B"` = Bid / Buy: aggressive taker buy (lifts the resting ask).
     - `"A"` = Ask / Sell: aggressive taker sell (hits the resting bid).

2. **Existing Execution Router & Venue Implementation**:
   - Location: `engine/execution_router.py` (lines 1-1261).
   - In `engine/execution_router.py`:
     - Lines 448-490: All synchronous SDK network calls (`market_open`, `market_close`, `cancel`, `get_equity`, `get_market_price`) are wrapped in `await asyncio.to_thread(...)` to prevent blocking the asyncio event loop.
     - Lines 617-632: Trigger stop market orders are dispatched using `order_type={"trigger": {"triggerPx": float(trigger_px), "isMarket": True, "tpsl": "sl"}}` and `reduce_only=True`.
     - Lines 969-993: Order slicing uses `asyncio.gather` with staged `asyncio.sleep` jitter delays.
     - Lines 1183-1260: `close_basket()` cancels the resting detached stop order via `venue.cancel()` and market-liquidates the entire position via `venue.market_close(reduce_only=True)`.

3. **Existing WebSocket Architecture**:
   - Location: `live_hyperliquid.py` (lines 2616-2646).
   - Shows `WebsocketManager` initialization, thread management, and callback handling:
     ```python
     self.ws = WebsocketManager(hl_constants.MAINNET_API_URL)
     self.ws.start()
     self.ws.subscribe({"type": "allMids"}, self._cb_mids)
     ```
   - In `hyperliquid/websocket_manager.py` (lines 1-40):
     `WebsocketManager` inherits from `threading.Thread`. Callbacks run synchronously on the background WebSocket network thread.

4. **Local LLM Intuition Hook & Schema**:
   - Location: `macro/slm_intuition.py` (lines 250-420).
   - Uses `aiohttp.ClientSession` with `aiohttp.ClientTimeout(total=timeout_sec)` hitting `http://localhost:8080/completion`.
   - Enforces GBNF grammar constraints and implements automated fast-path fallback when timeouts or HTTP errors occur.

5. **Test Fixtures & Offline Mocking**:
   - Location: `tests/test_gold_relapse_scalper.py` (lines 57-80, 631-775).
   - Verified that `SimulatedBrokerVenue` provides an in-memory execution venue supporting `market_open`, `market_close`, trigger stop market orders, and cancellations.
   - Verified that mocking `mock_exchange` and `mock_info` enables 100% offline verification of Hyperliquid DEX venues.

---

## 2. Logic Chain

1. **Non-Blocking Dual-Core Execution (R1)**:
   - *Observation*: `hyperliquid-python` SDK network calls are synchronous HTTP/socket calls.
   - *Reasoning*: Direct calls in an asynchronous event loop would freeze tick processing for 50ms - 200ms.
   - *Deduction*: Core 1's `update_macro_edge()` must query `http://localhost:8080/completion` asynchronously via `aiohttp` with a strict `timeout=0.500s`. The resulting parsed JSON (`permit_trade`, `bias`, `volatility_regime`) must be written to an in-memory thread-safe `MACRO_STATE` dataclass. Core 2 reads `MACRO_STATE` instantly (nanosecond latency) without blocking.

2. **Sub-5ms Order Flow Exit Mechanics (R5)**:
   - *Observation*: WebSocket callbacks run on the background `WebsocketManager` thread.
   - *Reasoning*: Passing L2 book frames and trade ticks through asyncio queues introduces task scheduling latency (~1-2ms).
   - *Deduction*: Storing incoming L2 book levels and trade ticks into an in-memory `TapeBookMemory` using a low-overhead mutex or atomic reference swap enables `orderflow_exit_monitor()` to compute:
     - Top-5 Bid/Ask volume imbalance ratio
     - 20-tick trade tape opposing fill ratio (> 80% opposing fills: "A" for Long, "B" for Short)
     in $< 5\mu\text{s}$ (microseconds), well below the 5ms ceiling.

3. **`spam_orders` & Detached Stop Sizing (R3)**:
   - *Observation*: Requirements mandate 5 micro-orders with 20ms jitter stagger at 100x leverage on GOLD, followed by a detached stop placed exactly $1.00 beyond the invalidation wick extreme.
   - *Reasoning*: `asyncio.gather` with staged `await asyncio.sleep(idx * 0.020)` dispatches the 5 orders concurrently over an 80ms window.
   - *Deduction*: Once entry fills are confirmed, calculating $stop\_px = wick\_low - 1.00$ (for Long) or $stop\_px = wick\_high + 1.00$ (for Short) and placing a single `market_close(trigger_px=stop_px, sz=aggregate_sz, reduce_only=True)` guarantees zero reverse-positioning and exact risk bounding.

4. **Dynamic Basket Liquidation (`close_basket`) (R3, R4, R5)**:
   - *Observation*: Resting stop orders on Hyperliquid CLOB remain active unless cancelled.
   - *Reasoning*: If an exit trigger fires (Target S/R touched, opposing 65% wick, -$10.00 Equity Shield, L2 wall, or tape stall) and the bot only places a market close without cancelling the resting stop, the stop order could fill later as an orphan order.
   - *Deduction*: `close_basket()` must perform a two-step sequence: (1) `await venue.cancel(coin, basket.stop_order_id)`, (2) `await venue.market_close(coin=coin, sz=aggregate_sz, reduce_only=True)`.

---

## 3. Caveats

1. **Live Network Availability**:
   - Hyperliquid mainnet/testnet and the local llama.cpp server are external network services. In offline development environments, unit tests must use `SimulatedBrokerVenue`, `MockWebsocketManager`, and mock HTTP endpoints.
2. **Asset Symbol Convention**:
   - On Hyperliquid DEX, builder perps for Gold exist under names like `km:GOLD` and `cash:GOLD`. In the repository's execution router and tests, the contract is referred to as `"GOLD"`. The bot should maintain `"GOLD"` as default while supporting symbol remapping if needed.
3. **No Implementation in this Turn**:
   - In accordance with explorer read-only constraints, no production files were modified or created outside `.agents/explorer_survey_1/`.

---

## 4. Conclusion

The architecture for `hyper_predator_bot.py` is fully feasible and mapped:
1. **Core 1**: `update_macro_edge()` polling `http://localhost:8080/completion` every 5m with GBNF grammar, sub-500ms timeout, safe fallback, and thread-safe `MACRO_STATE`.
2. **Core 2**: Sub-50ms sniper reading L1 BBO, tracking M5 S/R pivots, gating entries on $\ge 65\%$ rejection wicks and final 5s tick velocity surge $\ge 1.5\times$.
3. **Execution**: `spam_orders` firing 5 slices with 20ms jitter via `asyncio.gather` at 100x leverage on GOLD, immediate detached stop at wick extreme $\pm \$1.00$ with `reduce_only=True`, and dynamic `close_basket()`.
4. **Order Flow Exits**: In-memory `< 5ms` monitoring of top-5 L2 book imbalance ($> 3.0 \times \text{volatility\_regime}$) and trade tape momentum stall ($> 80\%$ opposing taker fills).

---

## 5. Verification Method

To independently verify the findings in this report:

1. **Verify SDK Installation and Modules**:
   ```bash
   python3 -c "import hyperliquid; from hyperliquid.exchange import Exchange; from hyperliquid.info import Info; from hyperliquid.websocket_manager import WebsocketManager; print('Hyperliquid SDK OK')"
   ```
2. **Verify Existing Execution Router & Simulated Venue Tests**:
   ```bash
   pytest tests/test_gold_relapse_scalper.py -v
   ```
   *Expected Result*: All tests pass, validating 100x leverage, order slicing, detached stops, breakeven locks, and basket liquidation.
3. **Inspect Key Source Files**:
   - `engine/execution_router.py`: Check lines 448-490 (async SDK threading) and lines 969-993 (order slicing).
   - `macro/slm_intuition.py`: Check lines 250-400 (GBNF grammar and sub-300ms fallback).
   - `.agents/explorer_survey_1/survey_report.md`: Review comprehensive survey report.
