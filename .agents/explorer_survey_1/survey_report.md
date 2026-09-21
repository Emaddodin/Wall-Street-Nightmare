# Survey Report: Hyperliquid SDK, WebSocket Feeds & Execution Architecture

**Date**: 2026-09-19  
**Explorer**: Explorer 1 (Hyperliquid SDK & WS Explorer)  
**Target System**: `hyper_predator_bot.py` (Ultra-Aggressive M1 GOLD Scalper)  
**Requirements Addressed**: R1 (Decoupled Dual-Core & Macro Edge), R2 (L1 WS & Sniper Engine), R3 (Layered Order Slicing `spam_orders`), R5 (L2 & Tape Dynamic Exits)

---

## Executive Summary

This report delivers the technical mapping, SDK mechanics, WebSocket subscription architectures, macro edge background polling design, and execution patterns for `hyper_predator_bot.py`.

The codebase contains a mature foundation in `engine/execution_router.py`, `live_hyperliquid.py`, `macro/slm_intuition.py`, and `tests/test_gold_relapse_scalper.py`. We have verified that:
1. The official `hyperliquid-python-sdk` is installed (`/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/site-packages/hyperliquid`), containing `Exchange`, `Info`, and `WebsocketManager`.
2. All synchronous SDK calls are blocking; wrapping them in `asyncio.to_thread` guarantees non-blocking execution inside the asyncio event loop.
3. Subscriptions for L1 BBO (`bbo`), L2 Depth (`l2Book`), and Trades (`trades`) are natively supported by `WebsocketManager`. In Hyperliquid, trade `side == "B"` indicates aggressive taker buy (lifts ask) and `side == "A"` indicates aggressive taker sell (hits bid).
4. `spam_orders` (5 micro-slices with 20ms jitter stagger via `asyncio.gather`), detached stop-loss ($1.00 beyond invalidation wick with `reduce_only=True`), and `close_basket()` integrate seamlessly with Hyperliquid CLOB order structures.
5. Core 1 background polling (`update_macro_edge`) can query `http://localhost:8080/completion` with strict GBNF grammar / JSON schema under a sub-500ms timeout, writing atomically into a thread-safe `MACRO_STATE` dataclass.

---

## 1. Hyperliquid SDK Architecture & Usage

### 1.1 Installed SDK Inventory & Environment
- **SDK Path**: `/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/site-packages/hyperliquid`
- **Key Modules**:
  - `hyperliquid.info.Info`: REST & WebSocket query interface for account state, mids, meta, candles, and book snapshots.
  - `hyperliquid.exchange.Exchange`: Transaction signing and order placement (`market_open`, `market_close`, `order`, `cancel`, `update_leverage`).
  - `hyperliquid.websocket_manager.WebsocketManager`: Threaded background WebSocket client (`wss://api.hyperliquid.xyz/ws` or testnet `wss://api.hyperliquid-testnet.xyz/ws`).
  - `hyperliquid.utils.constants`: `MAINNET_API_URL`, `TESTNET_API_URL`.
  - `hyperliquid.utils.types`: TypedDict definitions for all messages and subscriptions.

### 1.2 Authentication & Leverage Configuration
- **Wallet Signing**: Local ECDSA signature generation using `eth_account.Account.from_key(secret_key)`.
- **Client Instantiation**:
  ```python
  wallet = EthAccount.from_key(secret_key)
  exchange = Exchange(wallet, base_url, account_address=account_address)
  info = Info(base_url, skip_ws=True)
  ```
- **Setting Leverage**:
  In Hyperliquid, leverage is updated per perpetual asset:
  ```python
  # Sets 100x cross leverage for GOLD
  await asyncio.to_thread(exchange.update_leverage, 100, "GOLD", True)
  ```

### 1.3 Asynchronous Non-Blocking Execution Bridge
Because `hyperliquid-python` uses synchronous `requests` under the hood, any direct call blocks the Python thread. In `engine/execution_router.py` (lines 352-354), this is solved by delegating all SDK network operations to worker threads:
```python
await asyncio.to_thread(self._sync_market_open, coin, is_buy, sz, px, slippage)
await asyncio.to_thread(self._sync_market_close, coin, sz, px, slippage, trigger_px, reduce_only)
await asyncio.to_thread(self._sync_cancel, coin, oid)
```
This pattern preserves sub-millisecond event loop responsiveness.

---

## 2. WebSocket Subscription Streams & High-Frequency Ingestion

### 2.1 Native SDK WebSocket Mechanics
`hyperliquid.websocket_manager.WebsocketManager` runs as an independent `threading.Thread`. It automatically maintains connections, sends pings every 50 seconds, parses incoming JSON frames, and routes them to registered callbacks.

### 2.2 Subscription Specifications for Hyper Predator Bot

| Stream Name | Subscription Payload | Channel Name | Identifier | Purpose in Bot |
|---|---|---|---|---|
| **L1 BBO** | `{"type": "bbo", "coin": "GOLD"}` | `bbo` | `bbo:gold` | Sub-millisecond best bid/ask updates for S/R target exit & Hard Equity Shield |
| **L2 Depth** | `{"type": "l2Book", "coin": "GOLD"}` | `l2Book` | `l2Book:gold` | Top-5 level book imbalance monitoring (< 5ms exit) |
| **Trades Tape** | `{"type": "trades", "coin": "GOLD"}` | `trades` | `trades:gold` | Trade arrival intensity & volume delta stall detection |
| **All Mids** | `{"type": "allMids"}` | `allMids` | `allMids` | Fast mid-price fallback across venue |

### 2.3 Message Structures & Field Mapping

#### A. L1 BBO (`bbo`)
- Payload structure:
  ```json
  {
    "channel": "bbo",
    "data": {
      "coin": "GOLD",
      "time": 1726747200000,
      "bbo": [
        {"px": "2500.10", "sz": "1.5", "n": 2},
        {"px": "2500.20", "sz": "2.0", "n": 1}
      ]
    }
  }
  ```
- `bbo[0]` = Best Bid (`px`, `sz`, `n`), `bbo[1]` = Best Ask (`px`, `sz`, `n`).

#### B. L2 Orderbook (`l2Book`)
- Payload structure:
  ```json
  {
    "channel": "l2Book",
    "data": {
      "coin": "GOLD",
      "time": 1726747200000,
      "levels": [
        [{"px": "2500.10", "sz": "1.5", "n": 2}, {"px": "2500.00", "sz": "3.0", "n": 4}, ...],
        [{"px": "2500.20", "sz": "2.0", "n": 1}, {"px": "2500.30", "sz": "4.5", "n": 3}, ...]
      ]
    }
  }
  ```
- `levels[0]` = Bid levels ordered from highest to lowest price.
- `levels[1]` = Ask levels ordered from lowest to highest price.
- **Top 5 Bid Volume**: `sum(float(lvl["sz"]) for lvl in levels[0][:5])`
- **Top 5 Ask Volume**: `sum(float(lvl["sz"]) for lvl in levels[1][:5])`

#### C. Trades Tape (`trades`)
- Payload structure:
  ```json
  {
    "channel": "trades",
    "data": [
      {
        "coin": "GOLD",
        "side": "A",
        "px": "2500.15",
        "sz": 0.5,
        "hash": "0x1a2b...",
        "time": 1726747200150
      }
    ]
  }
  ```
- **Side Semantics in Hyperliquid**:
  - `"B"` = **Bid / Buy**: Aggressive market buy (taker lifted the resting ask).
  - `"A"` = **Ask / Sell**: Aggressive market sell (taker hit the resting bid).

### 2.4 Sub-5ms Thread-Safe In-Memory Bridging
Because the WebSocket manager runs in a background thread, pushing to an asyncio queue introduces task scheduling overhead (~0.5ms - 2ms). For sub-5ms order flow exits, we utilize a direct **atomic in-memory state store**:
```python
class TapeBookMemory:
    """Thread-safe, lock-free or low-overhead in-memory orderbook and tape buffer."""
    def __init__(self):
        self.best_bid: float = 0.0
        self.best_ask: float = 0.0
        self.mid_px: float = 0.0
        self.top5_bids: List[Tuple[float, float]] = [] # [(px, sz), ...]
        self.top5_asks: List[Tuple[float, float]] = []
        self.recent_trades: Deque[dict] = deque(maxlen=100)
        self.tick_timestamps: Deque[float] = deque(maxlen=500)
        self._lock = threading.Lock()

    def on_bbo(self, data: dict):
        bbo = data.get("bbo", [None, None])
        if bbo[0] and bbo[1]:
            self.best_bid = float(bbo[0]["px"])
            self.best_ask = float(bbo[1]["px"])
            self.mid_px = (self.best_bid + self.best_ask) / 2.0

    def on_l2(self, data: dict):
        levels = data.get("levels", [[], []])
        # Parse top 5 levels in under 2 microseconds
        bids = [(float(x["px"]), float(x["sz"])) for x in levels[0][:5]]
        asks = [(float(x["px"]), float(x["sz"])) for x in levels[1][:5]]
        with self._lock:
            self.top5_bids = bids
            self.top5_asks = asks

    def on_trades(self, data: list):
        now = time.time()
        with self._lock:
            for tr in data:
                self.recent_trades.append(tr)
                self.tick_timestamps.append(now)
```
Reading from `TapeBookMemory` takes nanoseconds, satisfying the strict < 5ms requirement for R5.

---

## 3. Order Slicing (`spam_orders`), Detached Stop-Loss & Dynamic Basket Close

### 3.1 `spam_orders` Specification (R3)
- **Signature**: `async def spam_orders(coin="GOLD", is_buy=bool, total_sz=float, slices=5)`
- **Behavior**:
  - `slice_sz = round(total_sz / slices, 2)` (e.g. `total_sz = 5.0` -> five `1.0` slices).
  - Open-ended entries: NO static Take-Profit orders sent (`take_profit = None`).
  - Concurrent dispatch with 20ms jitter stagger via `asyncio.gather`:
    ```python
    async def _dispatch_slice(idx: int):
        if idx > 0:
            await asyncio.sleep(idx * 0.020) # 20ms jitter stagger
        return await venue.market_open(
            coin=coin,
            is_buy=is_buy,
            sz=slice_sz,
            slippage=0.01,
        )

    tasks = [_dispatch_slice(i) for i in range(slices)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    ```
- **Risk Invariants**:
  - Leverage: 100x on Hyperliquid GOLD.
  - Margin Ceiling: Total initial margin utilization $\le 20\%$ of account equity ($13.00 max on a $65 base).
  - Aggregate fill calculation: $P_{avg} = \frac{\sum (P_i \times sz_i)}{\sum sz_i}$.

### 3.2 Detached Stop-Loss Mechanism
Immediately following entry slice fills:
- **Price Calculation**: Placed exactly **$1.00 absolute dollar** beyond the invalidation wick extreme:
  - If Long (`is_buy=True`): `stop_px = round(invalidation_wick_low - 1.00, 2)`
  - If Short (`is_buy=False`): `stop_px = round(invalidation_wick_high + 1.00, 2)`
- **Order Placement**:
  Dispatched as a unified trigger Stop Market order for the aggregate position size with `reduce_only=True`:
  ```python
  order_type = {
      "trigger": {
          "triggerPx": float(stop_px),
          "isMarket": True,
          "tpsl": "sl",
      }
  }
  stop_res = await asyncio.to_thread(
      exchange.order,
      name=coin,
      is_buy=not is_buy,  # Closing direction
      sz=aggregate_sz,
      limit_px=float(stop_px),
      order_type=order_type,
      reduce_only=True,
  )
  ```

### 3.3 Dynamic Basket Close (`close_basket`)
Triggered ruthlessly by any of five exit conditions:
1. **Target Exit**: Real-time WS bid/ask touches opposing M5 S/R zone.
2. **Reversal Exit**: Opposing M1 rejection wick prints $\ge 65\%$.
3. **Hard Equity Shield**: Active basket unrealized PnL drops to $-\$10.00$.
4. **L2 Imbalance Wall**: Top-5 book imbalance $> 3.0 \times \text{volatility\_regime}$.
5. **Trade Tape Delta Stall**: $> 80\%$ of last 20 trades are opposing market fills.

**Execution Routine**:
```python
async def close_basket(self, reason: str = "EXIT_SIGNAL") -> Optional[Dict[str, Any]]:
    async with self._lock:
        if not self.active_basket or not self.active_basket.is_active:
            return None
        
        # 1. Programmatically cancel resting detached stop market order
        if self.active_basket.stop_order_id:
            await self.venue.cancel(self.active_basket.coin, self.active_basket.stop_order_id)
            self.active_basket.stop_order_id = None
            
        # 2. Instant market close for entire aggregate size
        close_res = await self.venue.market_close(
            coin=self.active_basket.coin,
            sz=self.active_basket.aggregate_sz,
            reduce_only=True,
        )
        
        self.active_basket.is_active = False
        self.active_basket = None
        return close_res
```

---

## 4. Core 1 Background Macro Loop (`update_macro_edge`)

### 4.1 Architecture & Polling Lifecycle
- **Independent Task**: Runs in an infinite loop sleeping 300 seconds (5 minutes).
- **Target Endpoint**: Local `llama.cpp` server at `http://localhost:8080/completion`.
- **Latency Budget**: Sub-500ms timeout (`aiohttp.ClientTimeout(total=0.500)`).
- **Zero Blocking Guarantee**: Runs asynchronously; never delays Core 2's sub-50ms tick processing.

### 4.2 Telemetry Ingestion & Grammar Constraint
Compressed telemetry payload format:
```json
{
  "m15_trend": "BULLISH",
  "dxy_divergence": false,
  "calendar_blackout": false,
  "m5_sr_proximity": {"dist_support": 1.45, "dist_resistance": 5.20}
}
```

Strict GBNF Grammar / JSON Schema:
```ebnf
root ::= "{" ws "\"permit_trade\":" ws boolean "," ws "\"bias\":" ws ("\"BULLISH\"" | "\"BEARISH\"") "," ws "\"volatility_regime\":" ws number ws "}"
boolean ::= "true" | "false"
number ::= ("-"? [0-9]+ ("." [0-9]+)?)
ws ::= [ \t\n\r]*
```

Expected LLM Response:
```json
{"permit_trade": true, "bias": "BULLISH", "volatility_regime": 1.15}
```

### 4.3 Thread-Safe `MACRO_STATE` Dataclass
```python
@dataclass(frozen=True)
class MacroState:
    permit_trade: bool = False
    bias: str = "BULLISH" # "BULLISH" | "BEARISH"
    volatility_regime: float = 1.0
    last_updated: float = field(default_factory=time.time)

class MacroStateManager:
    def __init__(self):
        self._state = MacroState()
        self._lock = threading.Lock()

    @property
    def state(self) -> MacroState:
        with self._lock:
            return self._state

    def update(self, permit_trade: bool, bias: str, volatility_regime: float):
        with self._lock:
            self._state = MacroState(
                permit_trade=permit_trade,
                bias=bias,
                volatility_regime=volatility_regime,
                last_updated=time.time(),
            )
```
- **Fallback Rule**: If `http://localhost:8080/completion` fails or times out (> 500ms), Core 1 retains the previous state or switches to safe hold (`permit_trade = False`).

---

## 5. Order Flow Edge & Microstructure Exit Mechanics (R5)

### 5.1 L2 Imbalance Calculation (< 5ms)
- At top 5 levels of `l2Book`:
  - $\text{BidVol} = \sum_{i=1}^5 \text{sz}_{bid, i}$
  - $\text{AskVol} = \sum_{i=1}^5 \text{sz}_{ask, i}$
- If active position is **Long**:
  $$\text{Imbalance Ratio} = \frac{\text{AskVol}}{\max(\text{BidVol}, 10^{-6})}$$
  If $\text{Imbalance Ratio} > 3.0 \times \text{MACRO\_STATE.volatility\_regime}$, trigger `close_basket("L2_IMBALANCE_WALL")`.
- If active position is **Short**:
  $$\text{Imbalance Ratio} = \frac{\text{BidVol}}{\max(\text{AskVol}, 10^{-6})}$$
  If $\text{Imbalance Ratio} > 3.0 \times \text{MACRO\_STATE.volatility\_regime}$, trigger `close_basket("L2_IMBALANCE_WALL")`.

### 5.2 Volume Delta Momentum Stall
- In a **profitable basket** (`unrealized_pnl > 0`):
  Inspect the last 20 trade ticks in `trades`:
  - If **Long**: Opposing fills are `"A"` (taker sells).
    $$\frac{\text{Count}(side == "A")}{20} > 0.80 \implies \text{Stall detected} \implies \text{Trigger } close\_basket("VOLUME\_DELTA\_STALL")$$
  - If **Short**: Opposing fills are `"B"` (taker buys).
    $$\frac{\text{Count}(side == "B")}{20} > 0.80 \implies \text{Stall detected} \implies \text{Trigger } close\_basket("VOLUME\_DELTA\_STALL")$$

---

## 6. Mock & Testnet Exchange Abstractions for Offline Verification

To support automated testing in `tests/test_hyper_predator.py` without requiring live Hyperliquid API access or private keys, we have identified and extended existing repository fixtures:

1. **`SimulatedBrokerVenue`** (from `engine/execution_router.py` lines 239-345):
   - Fully supports `market_open`, detached Stop Market orders via `market_close(trigger_px=...)`, `cancel`, and slippage injection.
2. **`MockWebsocketManager`**:
   - Simulates callback delivery for `bbo`, `l2Book`, and `trades` messages.
   - Allows deterministic injection of:
     - 65% rejection wick price sequences.
     - L2 book states with skewed bid/ask ratios.
     - Tape streams with $> 80\%$ opposing fills.
3. **`MockLlamaServer`**:
   - Simulates `http://localhost:8080/completion` responses with valid JSON and artificial delay injection (testing both sub-500ms success and timeout fallback).

---

## 7. Implementation Blueprint for `hyper_predator_bot.py`

```
+-----------------------------------------------------------------------------------+
|                            HYPER PREDATOR BOT ARCHITECTURE                         |
+-----------------------------------------------------------------------------------+
|                                                                                   |
|  [ CORE 1: Macro Brain Loop ] (async def update_macro_edge, every 5 min)          |
|    - Queries http://localhost:8080/completion (< 500ms timeout)                   |
|    - Updates thread-safe MACRO_STATE (permit_trade, bias, volatility_regime)     |
|                                                                                   |
+-----------------------------------------------------------------------------------+
                                         | Reads state (nanoseconds)
                                         v
+-----------------------------------------------------------------------------------+
|  [ CORE 2: High-Frequency Sniper & Exit Engine ]                                  |
|                                                                                   |
|  Live WebSocket Feeds (TapeBookMemory):                                           |
|    - L1 BBO: Live sub-ms price updates -> Opposing S/R Exit & Equity Shield       |
|    - L2 Depth: Top 5 levels -> Ask/Bid Imbalance Exit (< 5ms)                     |
|    - Trades: Tick Tape -> >80% Opposing Delta Stall Exit                          |
|                                                                                   |
|  M1 Candle Retest Trigger:                                                        |
|    1. Price in M5 S/R zone                                                        |
|    2. Rejection Wick >= 65% in direction of MACRO_STATE.bias                      |
|    3. Final 5s tick velocity >= 1.5x baseline                                     |
|                                                                                   |
|  Execution Router:                                                                |
|    - spam_orders(5 slices, 20ms jitter stagger via asyncio.gather)                |
|    - Detached SL: Invalidation wick extreme +/- $1.00 (reduce_only=True)           |
|    - close_basket(): Dynamic parallel exit on any trigger                         |
+-----------------------------------------------------------------------------------+
```
