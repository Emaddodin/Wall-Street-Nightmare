"""
hyper_predator_bot.py
=====================
Ultra-Aggressive High-Frequency M1 Scalper for Hyperliquid DEX (GOLD Perpetual).
Decoupled Dual-Core Asynchronous Architecture (Architecture Hyper-Predator).

Key Architectural Invariants:
1. R1: Decoupled Dual-Core Architecture (Background Brain & Macro State)
   - Core 1: Independent async background loop `update_macro_edge()` polling local
     llama.cpp server (http://localhost:8080/completion) every 5 minutes.
   - Enforces strict JSON output:
     {"permit_trade": bool, "bias": "BULLISH" | "BEARISH", "volatility_regime": float}
   - Atomically updates thread-safe in-memory `MACRO_STATE` dataclass.
   - Sub-500ms timeout with automatic fail-safe fallback to previous state or algorithmic hold.
     Guarantees Core 1 NEVER blocks or delays the execution path under any condition.
2. R2: High-Frequency Aggressive Sniper Engine (Core 2)
   - Subscribes to Hyperliquid L1 orderbook WS (bbo / allMids) for sub-millisecond price updates.
   - Dynamic rolling M5 Support & Resistance pivots using rolling pivot highs/lows over lookback window.
   - M1 Retest Trigger evaluated at every candle close:
     a. Price touches or enters active M5 S/R zone.
     b. M1 candle forms extreme Rejection Wick (>= 65% of High - Low range), and body closes
        in the direction of MACRO_STATE.bias.
     c. Tick Velocity Edge: Final 5 seconds of M1 candle exhibits volume/tick surge >= 1.5x rolling baseline.
   - Instant trigger (< 50ms decision latency) when MACRO_STATE.permit_trade is True.
3. R3: Layered Order Slicing & Execution Bridge (`spam_orders`)
   - `spam_orders(coin="GOLD", is_buy=bool, total_sz=float, slices=5)`:
     Dispatches 5 micro-slices concurrently via `asyncio.gather` with 20ms jitter stagger at 100x leverage.
   - Open-Ended Entries: No static Take-Profit orders placed at entry.
   - Detached Stop-Loss: Single resting Stop Market order placed exactly $1.00 absolute dollar
     beyond the invalidation wick extreme (wick_low - 1.00 for Long, wick_high + 1.00 for Short)
     with `reduce_only=True`.
4. R4: Dynamic Ruthless Exits & Hard Equity Shield
   - Target Exit: Instant market-close the exact millisecond live WS bid/ask touches opposing M5 S/R zone.
   - Reversal Exit: Instant market-close on opposing >= 65% M1 rejection wick.
   - Hard Equity Shield: Instant liquidation with reduce_only=True if floating uPnL reaches -$10.00
     (protecting a $65.00 micro-account).
5. R5: Advanced Micro-Structure & Order Flow Edge (`orderflow_exit_monitor`)
   - Evaluates in pure local Python memory in < 5ms:
     a. L2 Imbalance Edge: Top-5 levels of Hyperliquid `l2Book`. If Long and (Ask Vol / Bid Vol) > 3.0 * volatility_regime
        (or Short and (Bid Vol / Ask Vol) > 3.0 * volatility_regime), fires `close_basket()` immediately.
     b. Volume Delta Edge: Hyperliquid `trades` stream. If in profitable basket and momentum stalls
        (> 80% of last 20 trade ticks are aggressive opposing market fills), fires `close_basket()` immediately.
     c. Adaptive thresholds scaled dynamically by `volatility_regime`.
6. R6: Strict Asset Focus
   - Hardcoded execution and risk engine exclusively for `GOLD` on Hyperliquid. Zero multi-ticker overhead.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

try:
    import aiohttp
    _AIOHTTP_AVAILABLE = True
except ImportError:
    aiohttp = None
    _AIOHTTP_AVAILABLE = False

try:
    from engine.execution_router import (
        HyperliquidVenue,
        SimulatedBrokerVenue,
    )
except ImportError:
    # Standalone fallback protocol and simulated venue if imported outside engine/
    from typing import Protocol, runtime_checkable

    @runtime_checkable
    class HyperliquidVenue(Protocol):
        async def get_equity(self) -> float:
            ...

        async def get_market_price(self, coin: str) -> float:
            ...

        async def market_open(
            self, coin: str, is_buy: bool, sz: float, px: Optional[float] = None, slippage: float = 0.01
        ) -> Dict[str, Any]:
            ...

        async def market_close(
            self, coin: str, sz: Optional[float] = None, px: Optional[float] = None, slippage: float = 0.01, trigger_px: Optional[float] = None, reduce_only: bool = True
        ) -> Dict[str, Any]:
            ...

        async def cancel(self, coin: str, oid: str) -> bool:
            ...

    class SimulatedBrokerVenue:
        def __init__(self, initial_equity: float = 65.0, slippage_delta: float = 0.02):
            self.equity = initial_equity
            self.slippage_delta = slippage_delta
            self._prices = {"GOLD": 2500.00}
            self._orders = {}
            self._resting_stops = {}

        def set_market_price(self, coin: str, price: float) -> None:
            self._prices[coin] = round(price, 2)

        async def get_equity(self) -> float:
            return self.equity

        async def get_market_price(self, coin: str) -> float:
            return self._prices.get(coin, 2500.00)

        async def market_open(self, coin: str, is_buy: bool, sz: float, px: Optional[float] = None, slippage: float = 0.01) -> Dict[str, Any]:
            base_px = px or await self.get_market_price(coin)
            slip = self.slippage_delta if is_buy else -self.slippage_delta
            fill_price = round(base_px + slip, 2)
            oid = f"SIM-OPEN-{uuid.uuid4().hex[:8].upper()}"
            res = {"status": "ok", "oid": oid, "coin": coin, "is_buy": is_buy, "sz": sz, "fill_price": fill_price, "take_profit": None}
            self._orders[oid] = res
            return res

        async def market_close(self, coin: str, sz: Optional[float] = None, px: Optional[float] = None, slippage: float = 0.01, trigger_px: Optional[float] = None, reduce_only: bool = True) -> Dict[str, Any]:
            oid = f"SIM-CLOSE-{uuid.uuid4().hex[:8].upper()}"
            if trigger_px is not None:
                rec = {"oid": oid, "coin": coin, "sz": sz, "trigger_px": round(trigger_px, 2), "reduce_only": reduce_only, "status": "resting"}
                self._resting_stops[oid] = rec
                return {"status": "ok", "oid": oid, "type": "stop_market", "reduce_only": reduce_only}
            fill_px = px or await self.get_market_price(coin)
            return {"status": "ok", "oid": oid, "coin": coin, "sz": sz, "fill_price": fill_px, "reduce_only": reduce_only}

        async def cancel(self, coin: str, oid: str) -> bool:
            if oid in self._resting_stops:
                self._resting_stops[oid]["status"] = "cancelled"
                return True
            return False

logger = logging.getLogger("hyper_predator_bot")


# =========================================================================
# Telemetry Formatting
# =========================================================================

def emit_predator_telemetry(
    component: str,
    event: str,
    data: Dict[str, Any],
    level: str = "INFO",
) -> None:
    """Emit Antigravity structured JSON telemetry for Hyper Predator Bot."""
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "bot": "HyperPredator-GOLD",
        "component": component,
        "event": event,
        "level": level,
        "data": data,
    }
    line = json.dumps(payload, separators=(",", ":"))
    if level in ("ERROR", "CRITICAL"):
        logger.error(line)
    elif level == "WARNING":
        logger.warning(line)
    else:
        logger.info(line)


# =========================================================================
# R1: Decoupled Dual-Core Architecture (Background Brain & Macro State)
# =========================================================================

@dataclass
class MacroState:
    """
    In-memory state container populated asynchronously by Core 1.
    Strict JSON Schema:
    {"permit_trade": bool, "bias": "BULLISH" | "BEARISH", "volatility_regime": float}
    """
    permit_trade: bool = False
    bias: str = "BULLISH"              # "BULLISH" | "BEARISH" | "NEUTRAL"
    volatility_regime: float = 1.0     # Multiplier for L2 imbalance exit threshold
    last_updated: float = 0.0          # Epoch timestamp

    def is_fresh(self, max_age_seconds: float = 600.0) -> bool:
        """Checks if the macro state is within freshness SLA (default 10 mins)."""
        if self.last_updated <= 0.0:
            return False
        return (time.time() - self.last_updated) <= max_age_seconds


class MacroStateManager:
    """
    Thread-safe atomic store for MacroState.
    Allows lock-free or lightweight atomic reads by Core 2 in nanoseconds.
    """
    def __init__(self, initial_state: Optional[MacroState] = None):
        self._lock = threading.Lock()
        self._state = initial_state or MacroState(
            permit_trade=False,
            bias="BULLISH",
            volatility_regime=1.0,
            last_updated=0.0,
        )

    def get_state(self) -> MacroState:
        with self._lock:
            # Return a shallow copy snapshot
            return MacroState(
                permit_trade=self._state.permit_trade,
                bias=self._state.bias,
                volatility_regime=self._state.volatility_regime,
                last_updated=self._state.last_updated,
            )

    @property
    def state(self) -> MacroState:
        return self.get_state()

    def update(self, permit_trade: bool, bias: str, volatility_regime: float) -> MacroState:
        normalized_bias = bias.upper().strip()
        if normalized_bias not in ("BULLISH", "BEARISH", "NEUTRAL"):
            normalized_bias = "NEUTRAL"

        vol_clamped = max(0.1, min(float(volatility_regime), 10.0))
        now = time.time()
        with self._lock:
            self._state = MacroState(
                permit_trade=bool(permit_trade),
                bias=normalized_bias,
                volatility_regime=vol_clamped,
                last_updated=now,
            )
            return self._state

    def hold_safe_state(self) -> MacroState:
        """Sets permit_trade to False while preserving bias & regime."""
        with self._lock:
            self._state = MacroState(
                permit_trade=False,
                bias=self._state.bias,
                volatility_regime=self._state.volatility_regime,
                last_updated=time.time(),
            )
            return self._state


# Global singleton instance for easy import and access
MACRO_STATE = MacroStateManager()


# =========================================================================
# Domain Models: Slices, Baskets, Order Invariants
# =========================================================================

HARD_EQUITY_SHIELD_USD = -10.00       # Hard equity shield threshold in floating USD uPnL
DETACHED_SL_OFFSET_USD = 1.00         # Detached SL placed exactly $1.00 beyond wick extreme
LEVERAGE_GOLD = 100.0                 # 100x leverage on Hyperliquid GOLD
MAX_INITIAL_MARGIN_PCT = 0.20         # 20% max initial margin utilization ($13 on $65)
WICK_REJECTION_THRESHOLD = 0.65       # Minimum 65% rejection wick (R2 contract specification)
TICK_VELOCITY_SURGE_RATIO = 1.50      # Minimum 1.5x tick velocity surge in final 5s


@dataclass
class PredatorOrderSlice:
    """Individual micro-unit slice dispatched via spam_orders."""
    ticket_id: str
    basket_id: str
    coin: str = "GOLD"
    is_buy: bool = True
    sz: float = 1.0
    entry_price: float = 0.0
    created_at: float = field(default_factory=time.time)
    is_active: bool = True
    close_price: Optional[float] = None
    realized_pnl: float = 0.0


@dataclass
class PredatorBasket:
    """
    Active position basket containing 5 micro-slices, detached resting stop order,
    and dynamic exit tracking.
    """
    basket_id: str
    coin: str = "GOLD"
    is_buy: bool = True
    total_sz: float = 5.0
    entry_price: float = 0.0
    invalidation_price: float = 0.0
    sl_price: float = 0.0
    slices: List[PredatorOrderSlice] = field(default_factory=list)
    stop_order_id: Optional[str] = None
    target_price: Optional[float] = None
    is_active: bool = True
    created_at: float = field(default_factory=time.time)
    exit_reason: Optional[str] = None
    close_price: Optional[float] = None
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0

    @property
    def current_sz(self) -> float:
        return sum(s.sz for s in self.slices if s.is_active)

    @property
    def stop_price(self) -> float:
        return self.sl_price

    @property
    def exit_price(self) -> Optional[float]:
        return self.close_price

    def calculate_unrealized_pnl(self, current_price: float) -> float:
        """
        Calculates floating dollar unrealized PnL:
        Long: (current_price - avg_entry) * active_sz
        Short: (avg_entry - current_price) * active_sz
        """
        sz = self.current_sz
        if sz <= 0.0 or self.entry_price <= 0.0:
            return 0.0
        if self.is_buy:
            return (current_price - self.entry_price) * sz
        else:
            return (self.entry_price - current_price) * sz


# =========================================================================
# TapeBookMemory: Sub-5ms Orderbook and Tape Buffer
# =========================================================================

class TapeBookMemory:
    """
    High-performance thread-safe in-memory cache of:
    - Live L1 best bid, best ask, mid price
    - Top 5 levels of L2 orderbook
    - Rolling window of completed trade ticks (trades channel)
    - Tick timestamps for sub-millisecond velocity calculation

    Reads execute in pure local Python memory in microseconds (< 5ms).
    """
    def __init__(self, trade_history_len: int = 100, tick_history_len: int = 1000):
        self._lock = threading.Lock()
        self.best_bid: float = 0.0
        self.best_ask: float = 0.0
        self.mid_px: float = 0.0
        self.top5_bids: List[Tuple[float, float]] = []  # [(px, sz), ...]
        self.top5_asks: List[Tuple[float, float]] = []  # [(px, sz), ...]
        self.recent_trades: Deque[dict] = collections.deque(maxlen=trade_history_len)
        self.tick_timestamps: Deque[float] = collections.deque(maxlen=tick_history_len)

    def on_bbo(self, data: dict) -> None:
        """Handles Hyperliquid 'bbo' message."""
        # Format: {"bbo": [{"px": "...", "sz": "...", "n": ...}, {"px": "...", ...}]}
        bbo = data.get("bbo")
        if not bbo or len(bbo) < 2:
            return
        bid_item = bbo[0]
        ask_item = bbo[1]
        if bid_item and ask_item:
            try:
                b_px = float(bid_item["px"])
                a_px = float(ask_item["px"])
                with self._lock:
                    self.best_bid = b_px
                    self.best_ask = a_px
                    self.mid_px = round((b_px + a_px) / 2.0, 2)
            except (KeyError, ValueError, TypeError):
                pass

    def on_l2(self, data: dict) -> None:
        """Handles Hyperliquid 'l2Book' message."""
        levels = data.get("levels")
        if not levels or len(levels) < 2:
            return
        # levels[0] = bids, levels[1] = asks
        raw_bids = levels[0][:5]
        raw_asks = levels[1][:5]
        bids: List[Tuple[float, float]] = []
        asks: List[Tuple[float, float]] = []
        for b in raw_bids:
            try:
                bids.append((float(b["px"]), float(b["sz"])))
            except (KeyError, ValueError, TypeError):
                continue
        for a in raw_asks:
            try:
                asks.append((float(a["px"]), float(a["sz"])))
            except (KeyError, ValueError, TypeError):
                continue

        with self._lock:
            self.top5_bids = bids
            self.top5_asks = asks
            if bids and asks:
                self.best_bid = bids[0][0]
                self.best_ask = asks[0][0]
                self.mid_px = round((self.best_bid + self.best_ask) / 2.0, 2)

    def on_trades(self, trades: list) -> None:
        """Handles Hyperliquid 'trades' list."""
        now = time.time()
        with self._lock:
            for tr in trades:
                if isinstance(tr, dict):
                    self.recent_trades.append(tr)
                    self.tick_timestamps.append(now)

    def record_tick(self, timestamp: Optional[float] = None) -> None:
        """Record a single tick event timestamp for velocity tracking."""
        ts = timestamp if timestamp is not None else time.time()
        with self._lock:
            self.tick_timestamps.append(ts)

    def get_top5_volumes(self) -> Tuple[float, float]:
        """Returns (bid_volume, ask_volume) aggregated across top 5 book levels."""
        with self._lock:
            bid_vol = sum(sz for _, sz in self.top5_bids)
            ask_vol = sum(sz for _, sz in self.top5_asks)
            return bid_vol, ask_vol

    def get_opposing_trade_ratio(self, is_buy: bool, window: int = 20) -> Tuple[float, int]:
        """
        Calculates the ratio of aggressive opposing fills in the last `window` trades.
        In Hyperliquid:
          - side == "B": Aggressive buyer (taker lifts ask) -> aligns with Long, opposes Short
          - side == "A": Aggressive seller (taker hits bid) -> aligns with Short, opposes Long
        Returns: (opposing_ratio, total_considered)
        """
        with self._lock:
            if not self.recent_trades:
                return 0.0, 0
            trades_slice = list(self.recent_trades)[-window:]

        if not trades_slice:
            return 0.0, 0

        opposing_side = "A" if is_buy else "B"
        opposing_count = sum(1 for t in trades_slice if t.get("side") == opposing_side)
        ratio = opposing_count / len(trades_slice)
        return ratio, len(trades_slice)

    def get_recent_ticks_in_window(self, start_time: float, end_time: float) -> int:
        """Count ticks occurring within [start_time, end_time]."""
        with self._lock:
            return sum(1 for t in self.tick_timestamps if start_time <= t <= end_time)


# =========================================================================
# R2: High-Frequency Aggressive Sniper Engine (Core 2 Math & Trigger)
# =========================================================================

class SniperEngine:
    """
    Core 2 Mathematical Signal Generator:
    1. Dynamic rolling M5 Support/Resistance pivots (lookback K bars, e.g. 50 bars).
    2. M1 extreme Rejection Wick math (>= 65% of total high-low span) closing in bias direction.
    3. Final 5s tick velocity surge calculation (>= 1.5x rolling baseline).
    4. Evaluates setup at every M1 candle close with < 50ms decision latency.
    """
    def __init__(
        self,
        sr_lookback_bars: int = 50,
        zone_epsilon: float = 0.25,
        wick_rejection_pct: float = WICK_REJECTION_THRESHOLD,
        velocity_multiplier_threshold: float = TICK_VELOCITY_SURGE_RATIO,
    ):
        self.sr_lookback_bars = sr_lookback_bars
        self.zone_epsilon = zone_epsilon
        self.wick_rejection_pct = wick_rejection_pct
        self.velocity_multiplier_threshold = velocity_multiplier_threshold

        # Completed M5 candles: list of dicts with open, high, low, close, volume, timestamp
        self.m5_candles: List[dict] = []
        self._sr_support: Optional[float] = None
        self._sr_resistance: Optional[float] = None

        # Rolling tick velocity baseline (ticks/second)
        self._rolling_baseline_velocity: float = 1.0

    def add_m5_candle(self, candle: dict) -> None:
        """Add a completed M5 candle and re-evaluate rolling pivots."""
        self.m5_candles.append(candle)
        if len(self.m5_candles) > self.sr_lookback_bars * 2:
            self.m5_candles = self.m5_candles[-self.sr_lookback_bars * 2:]
        self._compute_sr_pivots()

    def set_m5_candles(self, candles: List[dict]) -> None:
        """Bulk load historical completed M5 candles."""
        self.m5_candles = list(candles)
        self._compute_sr_pivots()

    def _compute_sr_pivots(self) -> Tuple[Optional[float], Optional[float]]:
        """
        Rolling Support & Resistance Pivots over lookback window K.
        Zero Lookahead Bias: Uses completed historical candles up to t-1.
        Support: min(L_{t-K} .. L_{t-1})
        Resistance: max(H_{t-K} .. H_{t-1})
        """
        if len(self.m5_candles) < 2:
            return None, None

        window = self.m5_candles[-self.sr_lookback_bars:]
        lows = [float(c["low"]) for c in window]
        highs = [float(c["high"]) for c in window]
        self._sr_support = round(min(lows), 2)
        self._sr_resistance = round(max(highs), 2)
        return self._sr_support, self._sr_resistance

    @property
    def current_support(self) -> Optional[float]:
        return self._sr_support

    @property
    def current_resistance(self) -> Optional[float]:
        return self._sr_resistance

    def is_in_sr_zone(self, price: float, is_buy: bool) -> bool:
        """
        Check if price enters the active M5 S/R zone:
        - Bullish: Price touches or enters Support zone [Support - eps, Support + eps].
        - Bearish: Price touches or enters Resistance zone [Resistance - eps, Resistance + eps].
        """
        eps = self.zone_epsilon
        if is_buy:
            if self._sr_support is None:
                return False
            return (price <= self._sr_support + eps) and (price >= self._sr_support - eps)
        else:
            if self._sr_resistance is None:
                return False
            return (price >= self._sr_resistance - eps) and (price <= self._sr_resistance + eps)

    def check_candle_touch_sr_zone(self, candle: dict, is_buy: bool) -> bool:
        """
        Evaluates whether the candle traversed into the active S/R zone.
        Bullish: Low <= Support + eps and Close >= Support - eps.
        Bearish: High >= Resistance - eps and Close <= Resistance + eps.
        """
        eps = self.zone_epsilon
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])

        if is_buy:
            if self._sr_support is None:
                return False
            return (low <= self._sr_support + eps) and (close >= self._sr_support - eps)
        else:
            if self._sr_resistance is None:
                return False
            return (high >= self._sr_resistance - eps) and (close <= self._sr_resistance + eps)

    def compute_rejection_wick(self, candle: dict, bias: str) -> Tuple[bool, float, float]:
        """
        Mathematical definition of M1 extreme Rejection Wick:
        hl_range = High - Low
        If hl_range <= 0: return False, 0.0, 0.0

        Bullish bounce (Support):
          Lower Wick: W_lower = min(Open, Close) - Low
          Ratio: rho = W_lower / hl_range
          Condition: rho >= 0.65 AND Close > Open (body closes green/bullish).
          Invalidation price: Low

        Bearish rejection (Resistance):
          Upper Wick: W_upper = High - max(Open, Close)
          Ratio: rho = W_upper / hl_range
          Condition: rho >= 0.65 AND Close < Open (body closes red/bearish).
          Invalidation price: High

        Returns: (is_valid, wick_ratio, invalidation_price)
        """
        o = float(candle["open"])
        h = float(candle["high"])
        lo = float(candle["low"])
        c = float(candle["close"])

        hl_range = h - lo
        if hl_range <= 1e-6:
            return False, 0.0, lo

        if bias == "BULLISH":
            lower_wick = min(o, c) - lo
            rho = lower_wick / hl_range
            is_bullish_close = (c > o)
            is_valid = (rho >= self.wick_rejection_pct) and is_bullish_close
            invalidation_price = lo
            return is_valid, round(rho, 4), round(invalidation_price, 2)

        elif bias == "BEARISH":
            upper_wick = h - max(o, c)
            rho = upper_wick / hl_range
            is_bearish_close = (c < o)
            is_valid = (rho >= self.wick_rejection_pct) and is_bearish_close
            invalidation_price = h
            return is_valid, round(rho, 4), round(invalidation_price, 2)

        return False, 0.0, lo

    def evaluate_tick_velocity(
        self,
        m1_open_time: float,
        m1_close_time: float,
        tick_timestamps: List[float],
    ) -> Tuple[bool, float, float, float]:
        """
        Evaluates Final 5-second Tick Velocity Surge:
        - Baseline window: [m1_open_time, m1_close_time - 5s] (55 seconds duration).
        - Surge window: [m1_close_time - 5s, m1_close_time] (5 seconds duration).
        - v_base = N_base / 55.0 (or rolling baseline if N_base == 0).
        - v_surge = N_surge / 5.0.
        - Ratio: Lambda = v_surge / max(v_base, 0.1).
        - Condition: Lambda >= 1.50.

        Returns: (is_valid, surge_ratio, v_surge, v_base)
        """
        surge_cutoff = m1_close_time - 5.0
        n_base = sum(1 for t in tick_timestamps if m1_open_time <= t < surge_cutoff)
        n_surge = sum(1 for t in tick_timestamps if surge_cutoff <= t <= m1_close_time)

        v_base = (n_base / 55.0) if n_base > 0 else self._rolling_baseline_velocity
        v_surge = n_surge / 5.0

        surge_ratio = v_surge / max(v_base, 0.1)
        is_valid = (surge_ratio >= self.velocity_multiplier_threshold) and (n_surge >= 2)

        # Update rolling baseline smoothly
        if v_base > 0:
            self._rolling_baseline_velocity = round(0.9 * self._rolling_baseline_velocity + 0.1 * v_base, 3)

        return is_valid, round(surge_ratio, 2), round(v_surge, 2), round(v_base, 2)

    def evaluate_m1_trigger(
        self,
        candle: dict,
        macro_state: MacroState,
        tick_timestamps: Optional[List[float]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Full M1 Retest Trigger Evaluation:
        1. macro_state.permit_trade == True and bias in ('BULLISH', 'BEARISH').
        2. Price enters/touches active M5 S/R zone.
        3. M1 candle forms extreme Rejection Wick (>= 65%) with directional body close.
        4. Final 5s tick velocity surge >= 1.5x rolling baseline.

        Returns Signal dict if all criteria met, else None.
        """
        # 1. Macro Gate
        if not macro_state.permit_trade or macro_state.bias not in ("BULLISH", "BEARISH"):
            return None

        is_buy = (macro_state.bias == "BULLISH")

        # 2. Touch active M5 S/R zone
        zone_touched = self.check_candle_touch_sr_zone(candle, is_buy)
        if not zone_touched:
            return None

        # 3. M1 Extreme Rejection Wick
        wick_ok, wick_ratio, inval_px = self.compute_rejection_wick(candle, macro_state.bias)
        if not wick_ok:
            return None

        # 4. Final 5s Tick Velocity Surge
        if tick_timestamps is not None and len(tick_timestamps) > 0:
            raw_ts = float(candle.get("close_time", candle.get("time", time.time())))
            if raw_ts > 1e11:
                raw_ts /= 1000.0
            if "close_time" in candle:
                c_close = raw_ts
                c_open = float(candle.get("open_time", c_close - 60.0))
                if c_open > 1e11:
                    c_open /= 1000.0
            else:
                c_open = raw_ts
                c_close = c_open + 60.0
            vel_ok, vel_ratio, v_surge, v_base = self.evaluate_tick_velocity(c_open, c_close, tick_timestamps)
            if not vel_ok:
                return None
        else:
            # Gate entries strictly unless final 5s surge is demonstrated >= 1.5x baseline
            vel_ok = candle.get("velocity_surge_valid", False)
            vel_ratio = candle.get("velocity_surge_ratio", 0.0)
            if not vel_ok or vel_ratio < 1.5:
                return None

        # Calculate detached SL price ($1.00 beyond invalidation extreme)
        if is_buy:
            sl_price = round(inval_px - DETACHED_SL_OFFSET_USD, 2)
        else:
            sl_price = round(inval_px + DETACHED_SL_OFFSET_USD, 2)

        return {
            "signal": "ENTRY",
            "is_buy": is_buy,
            "coin": "GOLD",
            "bias": macro_state.bias,
            "invalidation_price": inval_px,
            "sl_price": sl_price,
            "wick_ratio": wick_ratio,
            "velocity_ratio": vel_ratio,
            "entry_reference_price": float(candle["close"]),
        }


# =========================================================================
# R3: Layered Order Slicing & Execution Bridge (`spam_orders`)
# =========================================================================

class ExecutionBridge:
    """
    Coordinates CLOB execution for GOLD:
    - Margin validation at 100x leverage (<= 20% equity utilization).
    - `spam_orders`: Concurrently dispatches 5 micro-slices via asyncio.gather
      with 20ms jitter stagger.
    - Open-Ended entries (no static TP).
    - Detached Stop-Loss: Placed exactly $1.00 absolute dollar beyond invalidation
      wick extreme with reduce_only=True.
    - `close_basket`: Cancels resting detached stop and market closes entire basket.
    """
    def __init__(self, venue: HyperliquidVenue, max_margin_pct: float = MAX_INITIAL_MARGIN_PCT):
        self.venue = venue
        self.max_margin_pct = max_margin_pct
        self._lock = asyncio.Lock()
        self.active_basket: Optional[PredatorBasket] = None
        self.closed_events: List[Dict[str, Any]] = []

    async def validate_margin(self, total_sz: float, price: float) -> Tuple[bool, float, float]:
        """
        Validates initial margin ceiling at 100x leverage.
        Margin = (sz * price) / 100.0
        """
        equity = await self.venue.get_equity()
        required_margin = (total_sz * price) / LEVERAGE_GOLD
        max_margin = equity * self.max_margin_pct
        return (required_margin <= max_margin), round(required_margin, 4), round(max_margin, 4)

    async def spam_orders(
        self,
        coin: str = "GOLD",
        is_buy: bool = True,
        total_sz: float = 5.0,
        slices: int = 5,
        jitter_ms: int = 20,
        invalidation_wick_price: float = 0.0,
    ) -> Optional[PredatorBasket]:
        """
        Layered micro-order spamming:
        1. Margin check at 100x leverage.
        2. Asyncio.gather 5 slices with 20ms jitter stagger.
        3. Detached Stop Market order placed exactly $1.00 beyond invalidation wick.
        """
        async with self._lock:
            # If an active basket is already open, prevent overlapping entry
            if self.active_basket and self.active_basket.is_active:
                emit_predator_telemetry(
                    component="ExecutionBridge",
                    event="ORDER_REJECTED_ACTIVE_BASKET_EXISTS",
                    data={"active_basket_id": self.active_basket.basket_id},
                    level="WARNING",
                )
                return None

            current_price = await self.venue.get_market_price(coin)

            # 1. Margin validation
            margin_ok, req_m, max_m = await self.validate_margin(total_sz, current_price)
            if not margin_ok:
                emit_predator_telemetry(
                    component="ExecutionBridge",
                    event="ORDER_REJECTED_MARGIN_EXCEEDED",
                    data={
                        "coin": coin,
                        "total_sz": total_sz,
                        "required_margin": req_m,
                        "max_allowed_margin": max_m,
                    },
                    level="WARNING",
                )
                return None

            # Calculate detached stop-loss price ($1.00 absolute delta beyond wick)
            if is_buy:
                inval = invalidation_wick_price if invalidation_wick_price > 0 else (current_price - 0.50)
                sl_price = round(inval - DETACHED_SL_OFFSET_USD, 2)
            else:
                inval = invalidation_wick_price if invalidation_wick_price > 0 else (current_price + 0.50)
                sl_price = round(inval + DETACHED_SL_OFFSET_USD, 2)

            basket_id = f"HL-PRED-{uuid.uuid4().hex[:8].upper()}"
            slice_sz = round(total_sz / slices, 4)

            emit_predator_telemetry(
                component="ExecutionBridge",
                event="SPAMMING_5_SLICES_INITIATED",
                data={
                    "basket_id": basket_id,
                    "coin": coin,
                    "is_buy": is_buy,
                    "total_sz": total_sz,
                    "slices": slices,
                    "slice_sz": slice_sz,
                    "jitter_ms": jitter_ms,
                    "sl_price": sl_price,
                    "inval_wick": inval,
                },
            )

            # 2. Concurrently dispatch micro-slices with jitter
            async def _dispatch_slice(idx: int) -> PredatorOrderSlice:
                if idx > 0 and jitter_ms > 0:
                    await asyncio.sleep((idx * jitter_ms) / 1000.0)
                # Open-ended market open
                res = await self.venue.market_open(
                    coin=coin,
                    is_buy=is_buy,
                    sz=slice_sz,
                )
                fill_px = res.get("fill_price", current_price)
                oid = res.get("oid", f"TICK-{uuid.uuid4().hex[:6]}")
                return PredatorOrderSlice(
                    ticket_id=oid,
                    basket_id=basket_id,
                    coin=coin,
                    is_buy=is_buy,
                    sz=slice_sz,
                    entry_price=fill_px,
                )

            tasks = [_dispatch_slice(i) for i in range(slices)]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            successful_slices: List[PredatorOrderSlice] = []
            for r in results:
                if isinstance(r, PredatorOrderSlice):
                    successful_slices.append(r)
                else:
                    logger.error("Slice dispatch failure: %s", r)

            if not successful_slices:
                emit_predator_telemetry(
                    component="ExecutionBridge",
                    event="ALL_SLICES_FAILED",
                    data={"basket_id": basket_id},
                    level="ERROR",
                )
                return None

            filled_sz = round(sum(s.sz for s in successful_slices), 4)
            avg_entry = round(sum(s.entry_price * s.sz for s in successful_slices) / filled_sz, 2)

            basket = PredatorBasket(
                basket_id=basket_id,
                coin=coin,
                is_buy=is_buy,
                total_sz=filled_sz,
                entry_price=avg_entry,
                invalidation_price=inval,
                sl_price=sl_price,
                slices=successful_slices,
            )

            # 3. Dispatch detached resting stop order
            stop_res = await self.venue.market_close(
                coin=coin,
                sz=filled_sz,
                trigger_px=sl_price,
                reduce_only=True,
            )
            basket.stop_order_id = stop_res.get("oid")

            self.active_basket = basket

            emit_predator_telemetry(
                component="ExecutionBridge",
                event="BASKET_OPENED_AND_DETACHED_STOP_RESTING",
                data={
                    "basket_id": basket_id,
                    "avg_entry": avg_entry,
                    "filled_sz": filled_sz,
                    "stop_order_id": basket.stop_order_id,
                    "sl_price": sl_price,
                    "reduce_only": True,
                },
            )
            return basket

    async def close_basket(
        self,
        coin: str = "GOLD",
        reason: str = "TARGET",
        exit_price: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Ruthless 100% Binary Market Liquidation:
        1. Updates venue market price if exit_price provided and supported.
        2. Programmatically cancels resting detached stop market order.
        3. Liquidates 100% of open basket size with reduce_only=True.
        4. Clears active_basket state immediately.
        """
        async with self._lock:
            if not self.active_basket or not self.active_basket.is_active:
                return None

            basket = self.active_basket
            active_slices = [s for s in basket.slices if s.is_active]
            if not active_slices:
                basket.is_active = False
                self.active_basket = None
                return None

            close_sz = round(sum(s.sz for s in active_slices), 4)
            if close_sz <= 0:
                return None

            emit_predator_telemetry(
                component="ExecutionBridge",
                event="CLOSING_BASKET_INITIATED",
                data={
                    "basket_id": basket.basket_id,
                    "sz": close_sz,
                    "reason": reason,
                    "exit_price": exit_price,
                },
            )

            # Update venue market price if exit_price provided (for simulated venues / mock bridges)
            if exit_price is not None and hasattr(self.venue, "set_market_price"):
                self.venue.set_market_price(coin, exit_price)

            # 1. Cancel resting detached stop order
            if basket.stop_order_id:
                try:
                    await self.venue.cancel(coin, basket.stop_order_id)
                except Exception as exc:
                    logger.warning("Error cancelling stop order %s: %s", basket.stop_order_id, exc)
                basket.stop_order_id = None

            # 2. Market close entire basket (100% OUT)
            close_res = await self.venue.market_close(
                coin=coin,
                sz=close_sz,
                px=exit_price,
                reduce_only=True,
            )
            exit_px = exit_price if exit_price is not None else close_res.get("fill_price", await self.venue.get_market_price(coin))

            # 3. Calculate realized PnL
            if basket.is_buy:
                pnl = (exit_px - basket.entry_price) * close_sz
            else:
                pnl = (basket.entry_price - exit_px) * close_sz
            pnl = round(pnl, 2)

            for s in active_slices:
                s.is_active = False
                s.close_price = exit_px

            basket.realized_pnl = pnl
            basket.is_active = False
            basket.exit_reason = reason
            basket.close_price = exit_px
            self.active_basket = None

            summary = {
                "basket_id": basket.basket_id,
                "coin": coin,
                "aggregate_sz": close_sz,
                "entry_price": basket.entry_price,
                "exit_price": exit_px,
                "realized_pnl": pnl,
                "total_realized_pnl": pnl,
                "reason": reason,
            }

            emit_predator_telemetry(
                component="ExecutionBridge",
                event="BASKET_CLOSED",
                data=summary,
            )
            self.closed_events.append(summary)
            return summary


# =========================================================================
# Module-Level Execution Wrappers (Interface Contract Compliance)
# =========================================================================

# Shared bridge instance for module-level functions
_GLOBAL_BRIDGE: Optional[ExecutionBridge] = None


def get_default_execution_bridge(venue: Optional[HyperliquidVenue] = None) -> ExecutionBridge:
    global _GLOBAL_BRIDGE
    if _GLOBAL_BRIDGE is None:
        _GLOBAL_BRIDGE = ExecutionBridge(venue or SimulatedBrokerVenue())
    elif venue is not None and _GLOBAL_BRIDGE.venue is not venue:
        _GLOBAL_BRIDGE = ExecutionBridge(venue)
    return _GLOBAL_BRIDGE


async def spam_orders(
    coin: str = "GOLD",
    is_buy: bool = True,
    total_sz: float = 5.0,
    slices: int = 5,
    jitter_ms: int = 20,
    venue: Optional[HyperliquidVenue] = None,
    invalidation_wick_price: float = 0.0,
) -> Optional[PredatorBasket]:
    """
    Interface Contract:
    Concurrently dispatches 5 micro-orders with 20ms jitter stagger via asyncio.gather.
    """
    bridge = get_default_execution_bridge(venue)
    return await bridge.spam_orders(
        coin=coin,
        is_buy=is_buy,
        total_sz=total_sz,
        slices=slices,
        jitter_ms=jitter_ms,
        invalidation_wick_price=invalidation_wick_price,
    )


async def close_basket(
    coin: str = "GOLD",
    venue: Optional[HyperliquidVenue] = None,
    reason: str = "TARGET",
    exit_price: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Interface Contract:
    Cancels resting detached stop-loss and executes market close with reduce_only=True.
    """
    bridge = get_default_execution_bridge(venue)
    return await bridge.close_basket(coin=coin, reason=reason, exit_price=exit_price)


# =========================================================================
# R4 & R5: Dynamic Ruthless Exits & Order Flow Edge
# =========================================================================

async def orderflow_exit_monitor(
    position_state: Optional[PredatorBasket],
    book_tape_memory: TapeBookMemory,
    macro_state: MacroState,
    close_callback: Callable[[str], Any],
    opposing_m5_sr_target: Optional[float] = None,
) -> Optional[str]:
    """
    Advanced Microstructure Order Flow Edge Evaluator (< 5ms pure Python memory).

    Evaluates:
    1. Hard Equity Shield: Basket floating uPnL drops to -$10.00 -> instant liquidation.
    2. Target Exit: Live WS bid/ask touches next immediate opposing M5 S/R zone.
    3. L2 Imbalance Wall: Top-5 levels book imbalance > 3.0 * volatility_regime.
    4. Volume Delta Edge: When in profitable basket, > 80% of last 20 trades are
       opposing market fills -> momentum stall exit.

    Returns the exit trigger reason if fired, else None.
    """
    if not position_state or not position_state.is_active:
        return None

    is_buy = position_state.is_buy
    current_mid = book_tape_memory.mid_px
    best_bid = book_tape_memory.best_bid
    best_ask = book_tape_memory.best_ask

    # Fallback to mid if bid/ask not yet populated
    if best_bid <= 0.0:
        best_bid = current_mid
    if best_ask <= 0.0:
        best_ask = current_mid

    eval_price = best_bid if is_buy else best_ask
    if eval_price <= 0.0:
        eval_price = current_mid

    # 1. Hard Equity Shield: Floating uPnL <= -$10.00
    if eval_price > 0.0:
        floating_pnl = position_state.calculate_unrealized_pnl(eval_price)
        if floating_pnl <= HARD_EQUITY_SHIELD_USD:
            reason = "HARD_EQUITY_SHIELD"
            emit_predator_telemetry(
                component="OrderflowExitMonitor",
                event="HARD_EQUITY_SHIELD_TRIGGERED",
                data={"floating_pnl": floating_pnl, "threshold": HARD_EQUITY_SHIELD_USD},
                level="CRITICAL",
            )
            await close_callback(reason)
            return reason

    # 1.5. Breakeven Lock at +1.5R: Locks in profit once trade advances
    if eval_price > 0.0 and position_state.sl_price > 0.0:
        initial_risk = abs(position_state.entry_price - position_state.sl_price)
        if initial_risk > 0.0:
            unrealized_pts = (eval_price - position_state.entry_price) if is_buy else (position_state.entry_price - eval_price)
            if unrealized_pts >= 1.5 * initial_risk:
                be_sl = round(position_state.entry_price + 0.10, 2) if is_buy else round(position_state.entry_price - 0.10, 2)
                should_update = (is_buy and position_state.sl_price < be_sl) or ((not is_buy) and position_state.sl_price > be_sl)
                if should_update:
                    position_state.sl_price = be_sl
                    emit_predator_telemetry(
                        component="OrderflowExitMonitor",
                        event="BREAKEVEN_LOCK_ENGAGED",
                        data={"basket_id": position_state.basket_id, "new_sl": be_sl, "unrealized_pts": round(unrealized_pts, 2)},
                        level="INFO",
                    )

    # 2. Target Exit: Live WS reaches opposing M5 S/R zone (Ruthless 100% Market Exit)
    target_price = opposing_m5_sr_target or position_state.target_price
    if target_price is not None and target_price > 0.0:
        if is_buy and best_bid >= target_price:
            reason = "OPPOSING_M5_SR_TARGET"
            emit_predator_telemetry(
                component="OrderflowExitMonitor",
                event="TARGET_SR_REACHED",
                data={"best_bid": best_bid, "target": target_price},
            )
            await close_callback(reason)
            return reason
        elif (not is_buy) and best_ask <= target_price:
            reason = "OPPOSING_M5_SR_TARGET"
            emit_predator_telemetry(
                component="OrderflowExitMonitor",
                event="TARGET_SR_REACHED",
                data={"best_ask": best_ask, "target": target_price},
            )
            await close_callback(reason)
            return reason

    # 3. L2 Imbalance Wall: Top 5 levels ratio > 3.0 * volatility_regime
    bid_vol, ask_vol = book_tape_memory.get_top5_volumes()
    if bid_vol > 0.0 or ask_vol > 0.0:
        threshold = 3.0 * macro_state.volatility_regime
        if is_buy:
            # Long guard: Ask wall overpowering bids
            imbalance = ask_vol / max(bid_vol, 1e-6)
            if imbalance > threshold:
                reason = "L2_IMBALANCE_WALL"
                emit_predator_telemetry(
                    component="OrderflowExitMonitor",
                    event="L2_IMBALANCE_WALL_TRIGGERED",
                    data={"ask_vol": ask_vol, "bid_vol": bid_vol, "ratio": round(imbalance, 2), "threshold": round(threshold, 2)},
                )
                await close_callback(reason)
                return reason
        else:
            # Short guard: Bid wall overpowering asks
            imbalance = bid_vol / max(ask_vol, 1e-6)
            if imbalance > threshold:
                reason = "L2_IMBALANCE_WALL"
                emit_predator_telemetry(
                    component="OrderflowExitMonitor",
                    event="L2_IMBALANCE_WALL_TRIGGERED",
                    data={"bid_vol": bid_vol, "ask_vol": ask_vol, "ratio": round(imbalance, 2), "threshold": round(threshold, 2)},
                )
                await close_callback(reason)
                return reason

    # 4. Volume Delta Edge: > 80% opposing of last 20 trades when in profit
    if eval_price > 0.0:
        floating_pnl = position_state.calculate_unrealized_pnl(eval_price)
        if floating_pnl > 0.0:
            opp_ratio, count = book_tape_memory.get_opposing_trade_ratio(is_buy, window=20)
            if count >= 10 and opp_ratio > 0.80:
                reason = "VOLUME_DELTA_STALL"
                emit_predator_telemetry(
                    component="OrderflowExitMonitor",
                    event="VOLUME_DELTA_STALL_TRIGGERED",
                    data={"opposing_ratio": round(opp_ratio, 2), "sample_count": count, "floating_pnl": round(floating_pnl, 2)},
                )
                await close_callback(reason)
                return reason

    return None


class OrderflowMonitor:
    """Wrapper class providing object-oriented access to orderflow_exit_monitor."""
    def __init__(self, book_tape_memory: TapeBookMemory, macro_state_mgr: MacroStateManager):
        self.memory = book_tape_memory
        self.macro_mgr = macro_state_mgr

    async def evaluate(
        self,
        basket: Optional[PredatorBasket],
        close_callback: Callable[..., Any],
        opposing_target: Optional[float] = None,
    ) -> Optional[str]:
        current_exit_px = None
        if basket is not None:
            current_exit_px = self.memory.best_bid if basket.is_buy else self.memory.best_ask
            if current_exit_px <= 0.0:
                current_exit_px = self.memory.mid_px
            if current_exit_px <= 0.0:
                current_exit_px = None

        async def _wrapped_close(reason: str) -> Any:
            try:
                import inspect
                sig = inspect.signature(close_callback)
                params = sig.parameters
                if "exit_price" in params:
                    res = close_callback(reason, exit_price=current_exit_px)
                elif "px" in params:
                    res = close_callback(reason, px=current_exit_px)
                elif len(params) >= 2:
                    res = close_callback(reason, current_exit_px)
                else:
                    res = close_callback(reason)
            except Exception:
                res = close_callback(reason)
            if asyncio.iscoroutine(res):
                return await res
            return res

        return await orderflow_exit_monitor(
            position_state=basket,
            book_tape_memory=self.memory,
            macro_state=self.macro_mgr.get_state(),
            close_callback=_wrapped_close,
            opposing_m5_sr_target=opposing_target,
        )


# =========================================================================
# HyperPredatorBot Orchestrator
# =========================================================================

class HyperPredatorBot:
    """
    High-Frequency Decoupled Dual-Core Predator Bot for Hyperliquid GOLD:
    - Core 1: Background Macro Polling loop (`update_macro_edge`).
    - Core 2: Sub-50ms Sniper Engine with rolling M5 S/R pivots, M1 wick math,
              and final 5s tick velocity surge.
    - Execution Bridge: `spam_orders` 5 micro-slices with 20ms jitter stagger.
    - Dynamic Exits: Opposing S/R zone target, reversal wick exit, hard -$10 equity shield,
      and sub-5ms L2 orderflow tape exit monitor.
    """
    def __init__(
        self,
        venue: Optional[HyperliquidVenue] = None,
        macro_url: str = "http://localhost:8080/completion",
        sr_lookback_bars: int = 50,
        macro_poll_interval_sec: float = 300.0,
        default_total_sz: Optional[float] = None,
        zone_epsilon: float = 0.25,
        warmup_bars: int = 15,
        max_margin_pct: float = 0.20,
    ):
        self.coin = "GOLD"
        self.venue = venue or SimulatedBrokerVenue()
        self.macro_url = macro_url
        self.macro_poll_interval_sec = macro_poll_interval_sec
        self.default_total_sz = default_total_sz
        self.zone_epsilon = zone_epsilon
        self.warmup_bars = warmup_bars
        self.max_margin_pct = max_margin_pct
        self.m1_bars_received: int = 0
        self._m1_buffer: List[dict] = []

        self.macro_mgr = MacroStateManager()
        self.tape_memory = TapeBookMemory()
        self.sniper_engine = SniperEngine(sr_lookback_bars=sr_lookback_bars, zone_epsilon=zone_epsilon)
        self.execution_bridge = ExecutionBridge(self.venue, max_margin_pct=self.max_margin_pct)
        self.orderflow_monitor = OrderflowMonitor(self.tape_memory, self.macro_mgr)

        self._running = False
        self._macro_task: Optional[asyncio.Task] = None
        self._monitor_task: Optional[asyncio.Task] = None

    # ---------------------------------------------------------------------
    # Core 1: Non-Blocking Background Macro Polling Loop
    # ---------------------------------------------------------------------

    async def update_macro_edge(self, poll_once: bool = False) -> MacroState:
        """
        Independent async loop / poll function.
        Queries local llama.cpp server with compressed market telemetry.
        Sub-500ms timeout with automatic fail-safe fallback to previous safe state
        or algorithmic hold (permit_trade=False).
        """
        timeout_sec = 0.500

        # Construct compressed market telemetry payload
        supp = self.sniper_engine.current_support or 2500.00
        res = self.sniper_engine.current_resistance or 2510.00
        mid = self.tape_memory.mid_px or 2505.00

        dist_supp = round(mid - supp, 2)
        dist_res = round(res - mid, 2)

        # Compute true 15-minute directional trend via M5 rolling EMA slope
        if len(self.sniper_engine.m5_candles) >= 5:
            closes = [float(c["close"]) for c in self.sniper_engine.m5_candles]
            span = min(len(closes), 20)
            alpha = 2.0 / (span + 1.0)
            ema_prev = closes[0]
            ema_curr = closes[0]
            for px in closes[1:]:
                ema_prev = ema_curr
                ema_curr = alpha * px + (1.0 - alpha) * ema_curr
            m15_trend = "BULLISH" if (ema_curr - ema_prev) >= 0 else "BEARISH"
        elif len(self.sniper_engine.m5_candles) >= 2:
            m15_trend = "BULLISH" if float(self.sniper_engine.m5_candles[-1]["close"]) >= float(self.sniper_engine.m5_candles[0]["close"]) else "BEARISH"
        else:
            m15_trend = "BULLISH" if mid >= (supp + res) / 2.0 else "BEARISH"

        telemetry = {
            "m15_trend": m15_trend,
            "dxy_divergence": False,
            "calendar_blackout": False,
            "m5_sr_proximity": {"dist_support": dist_supp, "dist_resistance": dist_res},
        }

        prompt = (
            "You are an institutional macro execution filter for GOLD. "
            f"Telemetry: {json.dumps(telemetry)}. "
            "Output strictly valid JSON with keys permit_trade (bool), bias (BULLISH or BEARISH), "
            "and volatility_regime (float between 0.5 and 3.0)."
        )

        gbnf_grammar = (
            'root ::= "{" ws "\\"permit_trade\\":" ws boolean "," ws "\\"bias\\":" ws ("\\"BULLISH\\"" | "\\"BEARISH\\"") "," ws "\\"volatility_regime\\":" ws number ws "}"\n'
            'boolean ::= "true" | "false"\n'
            'number ::= ("-"? [0-9]+ ("." [0-9]+)?)\n'
            'ws ::= [ \\t\\n\\r]*'
        )

        body = {
            "prompt": prompt,
            "temperature": 0.1,
            "n_predict": 128,
            "grammar": gbnf_grammar,
        }

        try:
            if not _AIOHTTP_AVAILABLE:
                raise RuntimeError("aiohttp not installed; falling back to safe hold")

            client_timeout = aiohttp.ClientTimeout(total=timeout_sec)
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.post(self.macro_url, json=body) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"HTTP error {resp.status} from llama-server")
                    data = await resp.json()
                    content = data.get("content", data.get("response", ""))
                    if isinstance(content, str):
                        parsed = json.loads(content)
                    elif isinstance(content, dict):
                        parsed = content
                    else:
                        raise ValueError(f"Unparseable LLM output: {content}")

                    permit = bool(parsed.get("permit_trade", False))
                    bias = str(parsed.get("bias", "BULLISH"))
                    vol_regime = float(parsed.get("volatility_regime", 1.0))

                    new_state = self.macro_mgr.update(permit, bias, vol_regime)
                    emit_predator_telemetry(
                        component="Core1-MacroEdge",
                        event="MACRO_STATE_UPDATED",
                        data={"permit_trade": permit, "bias": bias, "volatility_regime": vol_regime},
                    )
                    return new_state

        except Exception as exc:
            # Sub-500ms timeout or connection failure: automatic safe hold
            emit_predator_telemetry(
                component="Core1-MacroEdge",
                event="MACRO_POLL_TIMEOUT_OR_ERROR",
                data={"error": str(exc), "action": "HOLD_SAFE_STATE"},
                level="WARNING",
            )
            return self.macro_mgr.hold_safe_state()

    async def _macro_loop(self) -> None:
        """Core 1 background polling loop running every 5 minutes."""
        while self._running:
            try:
                await self.update_macro_edge(poll_once=True)
            except Exception as exc:
                logger.error("Unexpected error in macro loop: %s", exc)
            await asyncio.sleep(self.macro_poll_interval_sec)

    # ---------------------------------------------------------------------
    # Core 2: High-Frequency Sniper Execution Loop & Handlers
    # ---------------------------------------------------------------------

    async def on_m1_candle_close(self, candle: dict, tick_timestamps: Optional[List[float]] = None) -> Optional[PredatorBasket]:
        """
        Invoked immediately upon every M1 candle close:
        1. Ingests candle into rolling M1->M5 aggregator (zero prior knowledge).
        2. Evaluates Reversal Exit if in active position.
        3. If IDLE and past warmup, evaluates M1 Retest Trigger.
        4. If triggered, dispatches spam_orders (< 50ms latency).
        """
        # Ingest M1 candle into rolling M5 aggregator
        self._m1_buffer.append(candle)
        if len(self._m1_buffer) >= 5:
            m5_candle = {
                "open": float(self._m1_buffer[0]["open"]),
                "high": max(float(c["high"]) for c in self._m1_buffer),
                "low": min(float(c["low"]) for c in self._m1_buffer),
                "close": float(self._m1_buffer[-1]["close"]),
                "volume": sum(float(c.get("volume", 0.0)) for c in self._m1_buffer),
                "time": self._m1_buffer[0].get("time", 0),
            }
            self.sniper_engine.add_m5_candle(m5_candle)
            self._m1_buffer.clear()

        self.m1_bars_received += 1

        # Reversal Exit Check: If in position and opposing >= 70% wick prints
        if self.execution_bridge.active_basket and self.execution_bridge.active_basket.is_active:
            basket = self.execution_bridge.active_basket
            opposing_bias = "BEARISH" if basket.is_buy else "BULLISH"
            wick_ok, rho, _ = self.sniper_engine.compute_rejection_wick(candle, opposing_bias)
            if wick_ok:
                emit_predator_telemetry(
                    component="Core2-Sniper",
                    event="OPPOSING_REVERSAL_WICK_DETECTED",
                    data={"basket_id": basket.basket_id, "opposing_wick_ratio": rho},
                    level="WARNING",
                )
                exit_px = float(candle.get("close", 0.0))
                if exit_px <= 0.0:
                    exit_px = self.tape_memory.best_bid if basket.is_buy else self.tape_memory.best_ask
                if exit_px <= 0.0:
                    exit_px = self.tape_memory.mid_px
                await self.execution_bridge.close_basket(
                    coin=self.coin,
                    reason="OPPOSING_M1_REJECTION_WICK",
                    exit_price=exit_px if exit_px > 0.0 else None,
                )
                return None

        # Zero-lookahead Causal Warmup Gate (requires 15 M1 bars / 3 M5 candles)
        if self.m1_bars_received < self.warmup_bars:
            return None

        # Evaluates Retest Trigger if not in trade
        state = self.macro_mgr.get_state()
        ticks = tick_timestamps if tick_timestamps is not None else list(self.tape_memory.tick_timestamps)
        trigger = self.sniper_engine.evaluate_m1_trigger(candle, state, ticks)

        if trigger is not None:
            emit_predator_telemetry(
                component="Core2-Sniper",
                event="SNIPER_TRIGGER_FIRE",
                data=trigger,
            )
            # Calculate sizing compliant with margin ceiling
            # and risk ceiling (potential stop-loss loss <= $8.50 to strictly protect -$10 Hard Equity Shield)
            equity = await self.venue.get_equity()
            cur_px = trigger["entry_reference_price"]
            sl_px = trigger["sl_price"]
            sl_dist = abs(cur_px - sl_px)
            import math
            effective_margin = getattr(self.execution_bridge, "max_margin_pct", self.max_margin_pct)
            max_allowed_sz = math.floor(((equity * effective_margin * LEVERAGE_GOLD) / cur_px) * 100) / 100.0
            max_sz_risk = math.floor((8.50 / max(sl_dist, 0.50)) * 100) / 100.0
            bounded_sz = min(max_allowed_sz, max_sz_risk)
            if bounded_sz < 0.05:
                return None
            if self.default_total_sz is not None and self.default_total_sz > 0:
                effective_sz = min(self.default_total_sz, bounded_sz)
            else:
                effective_sz = bounded_sz
            effective_sz = max(0.05, round(effective_sz, 2))

            # Dispatch 5 micro-slices via ExecutionBridge
            basket = await self.execution_bridge.spam_orders(
                coin=self.coin,
                is_buy=trigger["is_buy"],
                total_sz=effective_sz,
                slices=5,
                jitter_ms=20,
                invalidation_wick_price=trigger["invalidation_price"],
            )
            if basket:
                # Calculate next immediate opposing M5 S/R target
                opp_target = self.get_opposing_sr_target(basket.is_buy, basket.entry_price)
                basket.target_price = opp_target
            return basket

        return None

    def get_opposing_sr_target(self, is_buy: bool, entry_price: float) -> Optional[float]:
        """
        Target Exit:
        - Long: next immediate resistance > entry_price.
        - Short: next immediate support < entry_price.
        """
        if is_buy:
            res = self.sniper_engine.current_resistance
            return res if (res and res > entry_price) else None
        else:
            supp = self.sniper_engine.current_support
            return supp if (supp and supp < entry_price) else None

    # ---------------------------------------------------------------------
    # Continuous Real-Time Order Flow Monitor Loop
    # ---------------------------------------------------------------------

    async def _orderflow_loop(self) -> None:
        """
        Continuous sub-5ms order flow evaluation loop for active baskets.
        Runs tightly while bot is active.
        """
        while self._running:
            try:
                basket = self.execution_bridge.active_basket
                if basket and basket.is_active:
                    opp_target = basket.target_price or self.get_opposing_sr_target(basket.is_buy, basket.entry_price)
                    exit_px = self.tape_memory.best_bid if basket.is_buy else self.tape_memory.best_ask
                    if exit_px <= 0.0:
                        exit_px = self.tape_memory.mid_px
                    async def _orderflow_close(r: str, px: Optional[float] = None) -> Optional[Dict[str, Any]]:
                        return await self.execution_bridge.close_basket(
                            self.coin,
                            reason=r,
                            exit_price=px or (exit_px if exit_px > 0.0 else None),
                        )

                    await self.orderflow_monitor.evaluate(
                        basket=basket,
                        close_callback=_orderflow_close,
                        opposing_target=opp_target,
                    )
            except Exception as exc:
                logger.error("Error in orderflow loop: %s", exc)
            await asyncio.sleep(0.005)  # 5ms check cycle

    # ---------------------------------------------------------------------
    # Lifecycle Controls
    # ---------------------------------------------------------------------

    async def start(self) -> None:
        """Starts Core 1 macro polling and real-time order flow monitor."""
        if self._running:
            return
        self._running = True

        # Initial macro edge poll
        await self.update_macro_edge(poll_once=True)

        self._macro_task = asyncio.create_task(self._macro_loop())
        self._monitor_task = asyncio.create_task(self._orderflow_loop())

        emit_predator_telemetry(
            component="HyperPredatorBot",
            event="BOT_STARTED",
            data={"coin": self.coin, "macro_poll_interval": self.macro_poll_interval_sec},
        )

    async def stop(self) -> None:
        """Stops all tasks and closes active positions cleanly."""
        self._running = False
        if self._macro_task:
            self._macro_task.cancel()
            self._macro_task = None
        if self._monitor_task:
            self._monitor_task.cancel()
            self._monitor_task = None

        if self.execution_bridge.active_basket and self.execution_bridge.active_basket.is_active:
            basket = self.execution_bridge.active_basket
            exit_px = self.tape_memory.best_bid if basket.is_buy else self.tape_memory.best_ask
            if exit_px <= 0.0:
                exit_px = self.tape_memory.mid_px
            await self.execution_bridge.close_basket(
                self.coin,
                reason="BOT_SHUTDOWN",
                exit_price=exit_px if exit_px > 0.0 else None,
            )

        emit_predator_telemetry(
            component="HyperPredatorBot",
            event="BOT_STOPPED",
            data={"coin": self.coin},
        )
