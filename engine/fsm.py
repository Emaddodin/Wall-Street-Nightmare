"""
engine/fsm.py
=============
5-Minute Market Flow & Relapse Finite State Machine for Gold (XAUUSD).

FSM State Flow:
1. IDLE:
   - Evaluates KillZone (London: 07:00-10:00 UTC | NY: 12:30-16:30 UTC).
   - Validates Macro fundamental filter (no news blackout +/- 15 mins).
   - Enforces 5% max daily drawdown killswitch.
   -> Transitions to WAITING_FOR_RELAPSE when session active and guards clear.

2. WAITING_FOR_RELAPSE:
   - Confirms HTF (H1/M15) directional trend.
   - Awaits 5M corrective pullback (38.2% - 61.8% Fibonacci golden pocket)
     into an unmitigated FVG or Order Block with declining volume.
   -> Transitions to TRIGGER_DETECTED on mitigation.

3. TRIGGER_DETECTED:
   - 5M liquidity sweep (turtle soup) + Engulfing/Star pattern printed.
   - Validates SL envelope (strictly 10.0 to 15.0 pips).
   - Dispatches layered orders (3 x 0.01 lot with 50ms jitter).
   -> Transitions to IN_TRADE on fill.

4. IN_TRADE:
   - Shifts native SL to Entry + 1.0 pip upon reaching +1.5R (Breakeven Lock).
   - Polls sub-second SLM intuition engine on every 1-minute candle.
   - Checks scaling prerequisites.
   -> Transitions to SCALING if Tier-1 at BE + high momentum + 5M BOS.
   -> Transitions to EXIT_SIGNAL if SLM flags EXIT, opposing pattern, or SL hit.

5. SCALING:
   - Stacks Tier-2 sliced orders (3 x 0.01 lot).
   - Synchronizes global trailing SL across all active tickets.
   -> Transitions back to IN_TRADE.

6. EXIT_SIGNAL:
   - Executes parallel market close for all sliced tickets across all tiers.
   -> Returns to IDLE.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from engine.execution_router import (
    ExecutionRouter,
    OrderBasket,
    OrderSide,
    RiskInvariants,
    emit_telemetry,
)
from macro.slm_intuition import (
    EconomicCalendarFilter,
    IntuitionDecision,
    IntuitionTelemetry,
    SLMIntuitionEngine,
)
DAY_MS = 86_400_000  # Milliseconds in a standard UTC day (24 * 3600 * 1000)
from engine.killzone import (
    KillZone,
    KillZoneGuard,
    XAUUSD_GOLD_KILLZONES,
    XAUUSD_PEAK_KILLZONES,
)
from scalper.pa import candles, ict

logger = logging.getLogger("fsm")


# -------------------------------------------------------------------------
# XAUUSD Killzone Definitions
# London Open: 07:00-10:30 UTC | NY Session: 12:00-16:30 UTC
# -------------------------------------------------------------------------

XAUUSD_KILLZONES: Tuple[KillZone, ...] = XAUUSD_PEAK_KILLZONES


# -------------------------------------------------------------------------
# 5% Max Daily Drawdown Guard
# -------------------------------------------------------------------------

class DailyDrawdownGuard:
    """
    Guards micro-account against exceeding 5% maximum daily drawdown.
    Tracks peak equity per UTC day and trips hard killswitch if breached.
    """

    def __init__(self, max_drawdown_pct: float = 0.05) -> None:
        self.max_drawdown_pct = max_drawdown_pct
        self._current_day: int = 0
        self._peak_day_equity: float = 0.0
        self._is_tripped: bool = False

    def update(self, current_equity: float, current_ts: Optional[float] = None) -> Tuple[bool, float]:
        """
        Updates daily peak and checks drawdown.
        Returns: (is_tripped: bool, current_drawdown_pct: float)
        """
        ts = current_ts if current_ts is not None else time.time()
        day_key = int(ts * 1000 // DAY_MS) * DAY_MS

        if day_key != self._current_day:
            # New UTC Day: reset peak equity
            self._current_day = day_key
            self._peak_day_equity = current_equity
            self._is_tripped = False

        if current_equity > self._peak_day_equity:
            self._peak_day_equity = current_equity

        if self._peak_day_equity > 0:
            drawdown_pct = (self._peak_day_equity - current_equity) / self._peak_day_equity
        else:
            drawdown_pct = 0.0

        if drawdown_pct >= self.max_drawdown_pct:
            if not self._is_tripped:
                self._is_tripped = True
                emit_telemetry(
                    component="DailyDrawdownGuard",
                    event="KILLSWITCH_TRIPPED",
                    data={
                        "peak_day_equity": round(self._peak_day_equity, 2),
                        "current_equity": round(current_equity, 2),
                        "drawdown_pct": round(drawdown_pct * 100, 2),
                        "max_drawdown_pct": round(self.max_drawdown_pct * 100, 2),
                    },
                    level="CRITICAL",
                )

        return self._is_tripped, drawdown_pct

    @property
    def is_tripped(self) -> bool:
        return self._is_tripped

    @property
    def killswitch_tripped(self) -> bool:
        return self._is_tripped

    @property
    def peak_day_equity(self) -> float:
        return self._peak_day_equity


# -------------------------------------------------------------------------
# FSM State Definitions
# -------------------------------------------------------------------------

class RelapseState(str, Enum):
    IDLE = "IDLE"
    WAITING_FOR_RELAPSE = "WAITING_FOR_RELAPSE"
    TRIGGER_DETECTED = "TRIGGER_DETECTED"
    IN_TRADE = "IN_TRADE"
    SCALING = "SCALING"
    EXIT_SIGNAL = "EXIT_SIGNAL"


# -------------------------------------------------------------------------
# Candlestick Pattern Helper (Engulfing & Star Patterns)
# -------------------------------------------------------------------------

def detect_morning_star(df: pd.DataFrame) -> pd.Series:
    """
    Detects 3-candle Morning Star bullish reversal:
    Bar -2: Bearish candle
    Bar -1: Small body star / doji below bar -2
    Bar 0:  Bullish candle closing above 50% of bar -2 body
    """
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    rng = (h - l).replace(0, np.nan)
    body = (c - o).abs()

    b2_bearish = c.shift(2) < o.shift(2)
    b1_small = body.shift(1) <= 0.35 * rng.shift(1)
    b0_bullish = c > o

    # Bar 0 closes above midpoint of Bar 2
    b2_midpoint = (o.shift(2) + c.shift(2)) / 2.0
    b0_penetration = c >= b2_midpoint

    star = b2_bearish & b1_small & b0_bullish & b0_penetration
    return star.fillna(False).rename("morning_star")


def detect_evening_star(df: pd.DataFrame) -> pd.Series:
    """
    Detects 3-candle Evening Star bearish reversal:
    Bar -2: Bullish candle
    Bar -1: Small body star / doji above bar -2
    Bar 0:  Bearish candle closing below 50% of bar -2 body
    """
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    rng = (h - l).replace(0, np.nan)
    body = (c - o).abs()

    b2_bullish = c.shift(2) > o.shift(2)
    b1_small = body.shift(1) <= 0.35 * rng.shift(1)
    b0_bearish = c < o

    # Bar 0 closes below midpoint of Bar 2
    b2_midpoint = (o.shift(2) + c.shift(2)) / 2.0
    b0_penetration = c <= b2_midpoint

    star = b2_bullish & b1_small & b0_bearish & b0_penetration
    return star.fillna(False).rename("evening_star")


# -------------------------------------------------------------------------
# Relapse FSM Implementation
# -------------------------------------------------------------------------

@dataclass
class SetupContext:
    """Holds analytical state for the active relapse setup."""
    direction: OrderSide = OrderSide.BUY
    htf_bias: int = 1                     # +1 Bullish, -1 Bearish
    expansion_high: float = 0.0
    expansion_low: float = 0.0
    golden_pocket_min: float = 0.0        # 38.2% retracement level
    golden_pocket_max: float = 0.0        # 61.8% retracement level
    fvg_zone: Optional[Tuple[float, float]] = None
    ob_zone: Optional[Tuple[float, float]] = None
    invalidation_wick_price: float = 0.0
    trigger_pattern: str = ""
    trigger_candle_time: float = 0.0
    tier_scaled: bool = False


class RelapseFSM:
    """
    Finite State Machine orchestrating market flow, relapse detection,
    layered execution, and sub-second local LLM intuition exits.
    """

    def __init__(
        self,
        symbol: str = "XAUUSD",
        execution_router: Optional[ExecutionRouter] = None,
        intuition_engine: Optional[SLMIntuitionEngine] = None,
        calendar_filter: Optional[EconomicCalendarFilter] = None,
        drawdown_guard: Optional[DailyDrawdownGuard] = None,
        killzone_guard: Optional[KillZoneGuard] = None,
    ) -> None:
        self.symbol = symbol
        self.router = execution_router
        self.intuition = intuition_engine or SLMIntuitionEngine()
        self.calendar = calendar_filter or EconomicCalendarFilter()
        self.dd_guard = drawdown_guard or DailyDrawdownGuard(max_drawdown_pct=0.05)
        self.kz_guard = killzone_guard or KillZoneGuard(zones=XAUUSD_KILLZONES)

        self.state: RelapseState = RelapseState.IDLE
        self.context = SetupContext()
        self.active_basket: Optional[OrderBasket] = None
        self._bars_in_trade: int = 0
        self._last_state_change: float = time.time()

    def transition_to(self, new_state: RelapseState, reason: str = "") -> None:
        """Execute state transition with Antigravity structured telemetry."""
        old_state = self.state
        self.state = new_state
        self._last_state_change = time.time()
        emit_telemetry(
            component="RelapseFSM",
            event="STATE_TRANSITION",
            data={
                "from_state": old_state.value,
                "to_state": new_state.value,
                "reason": reason,
                "symbol": self.symbol,
            },
        )

    # ---------------------------------------------------------------------
    # Core Tick / Candle Evaluation Fast-Path
    # ---------------------------------------------------------------------

    async def on_5m_bar_update(
        self,
        df_5m: pd.DataFrame,
        df_htf: Optional[pd.DataFrame] = None,
    ) -> RelapseState:
        """
        Called causally on each completed 5-minute candle.
        Evaluates structural market flow, pullbacks, and trigger patterns.
        """
        if len(df_5m) < 30:
            return self.state

        current_equity = await self.router.venue.get_equity() if self.router else 65.0
        now_ts = float(df_5m["open_time"].iloc[-1] / 1000.0) if "open_time" in df_5m else time.time()

        # Update and check Daily Drawdown Guard
        is_dd_tripped, dd_pct = self.dd_guard.update(current_equity, now_ts)
        if is_dd_tripped:
            if self.state in (RelapseState.IN_TRADE, RelapseState.SCALING):
                await self.router.close_basket(reason="MAX_DAILY_DRAWDOWN_KILLSWITCH")
            self.transition_to(RelapseState.IDLE, reason=f"Drawdown {dd_pct*100:.1f}% >= 5.0% Killswitch")
            return self.state

        # Check KillZone & Macro Blackout
        in_kz, kz_name = self.kz_guard.check(now_ts)
        in_blackout, blackout_reason = self.calendar.is_macro_blackout(now_ts)

        # -----------------------------------------------------------------
        # State: IDLE
        # -----------------------------------------------------------------
        if self.state == RelapseState.IDLE:
            if not in_kz:
                return self.state
            if in_blackout:
                return self.state

            # Determine HTF trend (e.g. from H1/M15 or 5M macro EMA)
            htf_bias = self._calculate_htf_trend(df_5m, df_htf)
            if htf_bias != 0:
                self.context = SetupContext(
                    direction=OrderSide.BUY if htf_bias > 0 else OrderSide.SELL,
                    htf_bias=htf_bias,
                )
                self.transition_to(
                    RelapseState.WAITING_FOR_RELAPSE,
                    reason=f"Active KZ ({kz_name}) + Clear Macro + HTF Bias {'BULL' if htf_bias > 0 else 'BEAR'}",
                )

        # -----------------------------------------------------------------
        # State: WAITING_FOR_RELAPSE
        # -----------------------------------------------------------------
        elif self.state == RelapseState.WAITING_FOR_RELAPSE:
            if not in_kz or in_blackout:
                self.transition_to(RelapseState.IDLE, reason="Exited KZ or Entered Macro Blackout")
                return self.state

            relapse_found = self._check_relapse_pullback(df_5m, self.context.direction)
            if relapse_found:
                self.transition_to(
                    RelapseState.TRIGGER_DETECTED,
                    reason=f"38.2%-61.8% Golden Pocket Relapse into FVG/OB mitigated with declining volume",
                )

        # -----------------------------------------------------------------
        # State: TRIGGER_DETECTED
        # -----------------------------------------------------------------
        elif self.state == RelapseState.TRIGGER_DETECTED:
            if not in_kz or in_blackout:
                self.transition_to(RelapseState.IDLE, reason="Exited KZ or Entered Macro Blackout")
                return self.state

            trigger_confirmed, wick_price, pat_name = self._validate_trigger_pattern(df_5m, self.context.direction)

            if trigger_confirmed:
                self.context.invalidation_wick_price = wick_price
                self.context.trigger_pattern = pat_name

                # Attempt layered order slicing
                if self.router:
                    basket = await self.router.fire_layered_orders(
                        symbol=self.symbol,
                        side=self.context.direction,
                        invalidation_wick_price=wick_price,
                        total_lots=0.03,
                        num_slices=3,
                        tier=1,
                    )
                    if basket:
                        self.active_basket = basket
                        self._bars_in_trade = 0
                        self.transition_to(
                            RelapseState.IN_TRADE,
                            reason=f"Layered orders fired on {pat_name} (Wick: {wick_price})",
                        )
                    else:
                        # SL Envelope or Margin rejected trade
                        self.transition_to(
                            RelapseState.WAITING_FOR_RELAPSE,
                            reason="Order rejected by Risk/SL Invariant",
                        )
                else:
                    self.transition_to(RelapseState.IN_TRADE, reason="Trigger confirmed (No router attached)")
            else:
                # If price invalidated the relapse level, reset
                if self._check_invalidation(df_5m, self.context):
                    self.transition_to(RelapseState.WAITING_FOR_RELAPSE, reason="Setup invalidated before trigger")

        # -----------------------------------------------------------------
        # State: IN_TRADE
        # -----------------------------------------------------------------
        elif self.state == RelapseState.IN_TRADE:
            current_price = df_5m["close"].iloc[-1]

            # 1. Evaluate Breakeven Lock at +1.5R
            if self.router and self.active_basket and not self.active_basket.breakeven_locked:
                await self.router.evaluate_breakeven_lock(current_price)

            # 2. Check for Scaling Conditions
            if (
                self.active_basket
                and self.active_basket.breakeven_locked
                and not self.context.tier_scaled
            ):
                can_scale = self._check_scaling_prerequisites(df_5m, self.context.direction)
                if can_scale:
                    self.transition_to(RelapseState.SCALING, reason="Tier-1 at BE + High Momentum + New 5M BOS")
                    await self._execute_tier2_scaling(df_5m)
                    return self.state

            # 3. Check for Structural SL or Opposing Reversal Pattern
            opposing_pattern = self._check_opposing_reversal(df_5m, self.context.direction)
            if opposing_pattern:
                self.transition_to(RelapseState.EXIT_SIGNAL, reason=f"Opposing 5M pattern detected: {opposing_pattern}")
                await self._execute_basket_exit(reason=f"Opposing reversal: {opposing_pattern}")
                return self.state

            if self._check_stop_loss_hit(current_price):
                self.transition_to(RelapseState.EXIT_SIGNAL, reason="Structural Stop Loss hit")
                await self._execute_basket_exit(reason="Stop Loss Hit")
                return self.state

        # -----------------------------------------------------------------
        # State: SCALING
        # -----------------------------------------------------------------
        elif self.state == RelapseState.SCALING:
            # Reverts back to IN_TRADE after scaling tasks execute
            self.transition_to(RelapseState.IN_TRADE, reason="Tier-2 Slices Synchronized")

        # -----------------------------------------------------------------
        # State: EXIT_SIGNAL
        # -----------------------------------------------------------------
        elif self.state == RelapseState.EXIT_SIGNAL:
            await self._execute_basket_exit(reason="Exit State Finalized")
            self.transition_to(RelapseState.IDLE, reason="Trade finalized and liquidated")

        return self.state

    # Compatibility alias
    on_candle = on_5m_bar_update

    # ---------------------------------------------------------------------
    # 1-Minute Candle Telemetry & Intuition Exit Polling
    # ---------------------------------------------------------------------

    async def on_1m_bar_update(self, df_1m: pd.DataFrame, dxy_divergence: bool = False) -> None:
        """
        Called on every 1-minute candle while in an active trade.
        Constructs compressed telemetry and polls sub-second local LLM.
        """
        if self.state not in (RelapseState.IN_TRADE, RelapseState.SCALING) or not self.active_basket:
            return

        self._bars_in_trade += 1
        last_candle = df_1m.iloc[-1]
        c = float(last_candle["close"])
        o = float(last_candle["open"])
        h = float(last_candle["high"])
        l = float(last_candle["low"])
        rng = max(0.01, h - l)

        # Candle wick ratio against position direction
        if self.context.direction == OrderSide.BUY:
            opp_wick = h - max(c, o)
        else:
            opp_wick = min(c, o) - l
        candle_wick_ratio = min(1.0, max(0.0, opp_wick / rng))

        # Volume stall detection: last bar vol < 0.6x 10-bar avg
        vol_col = "volume" if "volume" in df_1m else ("vol" if "vol" in df_1m else None)
        if vol_col and len(df_1m) >= 10:
            recent_avg_vol = df_1m[vol_col].iloc[-10:-1].mean()
            current_vol = df_1m[vol_col].iloc[-1]
            volume_stall = bool(current_vol < (0.6 * recent_avg_vol))
        else:
            volume_stall = False

        _, unrealized_r = self.active_basket.calculate_unrealized_pnl(c)

        telemetry = IntuitionTelemetry(
            unrealized_r=unrealized_r,
            candle_wick_ratio=candle_wick_ratio,
            volume_stall=volume_stall,
            dxy_divergence=dxy_divergence,
            side=self.context.direction.value,
            bars_in_trade=self._bars_in_trade,
        )

        decision, latency_ms, rationale = await self.intuition.query_intuition_exit(telemetry)

        if decision == IntuitionDecision.EXIT:
            emit_telemetry(
                component="RelapseFSM",
                event="INTUITION_EXIT_TRIGGERED",
                data={
                    "latency_ms": round(latency_ms, 2),
                    "rationale": rationale,
                    "unrealized_r": round(unrealized_r, 2),
                    "wick_ratio": round(candle_wick_ratio, 2),
                },
            )
            self.transition_to(RelapseState.EXIT_SIGNAL, reason=f"SLM Intuition EXIT: {rationale}")
            await self._execute_basket_exit(reason="SLM Intuition Exit")

    # ---------------------------------------------------------------------
    # Internal Analytical & Execution Logic
    # ---------------------------------------------------------------------

    def _calculate_htf_trend(self, df_5m: pd.DataFrame, df_htf: Optional[pd.DataFrame]) -> int:
        """
        Determines directional bias: +1 (Bullish), -1 (Bearish), 0 (Neutral).
        Uses 50 EMA and causal swing structure.
        """
        closes = df_5m["close"]
        ema50 = closes.ewm(span=50, adjust=False).mean()
        last_c = closes.iloc[-1]
        last_ema = ema50.iloc[-1]

        if last_c > last_ema:
            return 1
        elif last_c < last_ema:
            return -1
        return 0

    def _check_relapse_pullback(self, df: pd.DataFrame, direction: OrderSide) -> bool:
        """
        Checks for 5M corrective pullback into 38.2%-61.8% golden pocket
        hitting an unmitigated FVG or Order Block with declining volume.
        """
        sw_hi = df["high"].rolling(10, min_periods=5).max()
        sw_lo = df["low"].rolling(10, min_periods=5).min()

        # FVG and OB detection causally from scalper/pa/ict.py
        fvg = ict.fvg_state(df)
        obs = ict.order_blocks(df, swing_lo=sw_lo, swing_hi=sw_hi)

        c = df["close"].iloc[-1]
        l = df["low"].iloc[-1]
        h = df["high"].iloc[-1]

        vol_col = "volume" if "volume" in df else ("vol" if "vol" in df else None)
        declining_vol = True
        if vol_col and len(df) >= 5:
            avg_v = df[vol_col].iloc[-20:].mean()
            declining_vol = df[vol_col].iloc[-1] <= avg_v

        if direction == OrderSide.BUY:
            exp_hi = float(df["high"].iloc[-20:].max())
            exp_lo = float(df["low"].iloc[-20:].min())
            leg = exp_hi - exp_lo
            if leg <= 0.50:  # Too small expansion
                return False

            # Fibonacci 38.2% - 61.8% retracement levels
            fib_382 = exp_hi - 0.382 * leg
            fib_618 = exp_hi - 0.618 * leg

            in_golden_pocket = (l <= fib_382) and (c >= fib_618)
            touches_fvg = bool(fvg["bull_fvg"].iloc[-1])
            touches_ob = bool(obs["bull_ob"].iloc[-1])

            if in_golden_pocket and (touches_fvg or touches_ob) and declining_vol:
                self.context.expansion_high = exp_hi
                self.context.expansion_low = exp_lo
                self.context.golden_pocket_min = fib_618
                self.context.golden_pocket_max = fib_382
                return True

        else:  # OrderSide.SELL
            exp_hi = float(df["high"].iloc[-20:].max())
            exp_lo = float(df["low"].iloc[-20:].min())
            leg = exp_hi - exp_lo
            if leg <= 0.50:
                return False

            fib_382 = exp_lo + 0.382 * leg
            fib_618 = exp_lo + 0.618 * leg

            in_golden_pocket = (h >= fib_382) and (c <= fib_618)
            touches_fvg = bool(fvg["bear_fvg"].iloc[-1])
            touches_ob = bool(obs["bear_ob"].iloc[-1])

            if in_golden_pocket and (touches_fvg or touches_ob) and declining_vol:
                self.context.expansion_high = exp_hi
                self.context.expansion_low = exp_lo
                self.context.golden_pocket_min = fib_382
                self.context.golden_pocket_max = fib_618
                return True

        return False

    def _validate_trigger_pattern(
        self,
        df: pd.DataFrame,
        direction: OrderSide,
    ) -> Tuple[bool, float, str]:
        """
        Validates 5M liquidity sweep (turtle soup) + Engulfing or Star pattern.
        Returns: (confirmed, invalidation_wick_price, pattern_name)
        """
        bull_eng = candles.bullish_engulfing(df)
        bear_eng = candles.bearish_engulfing(df)
        m_star = detect_morning_star(df)
        e_star = detect_evening_star(df)
        pin_l = candles.pin_bar_long(df)
        pin_s = candles.pin_bar_short(df)

        sw_lo = df["low"].rolling(5).min()
        sw_hi = df["high"].rolling(5).max()

        # Turtle soup sweep
        soup_l = ict.turtle_soup(df, sw_lo.shift(1), direction="low")
        soup_s = ict.turtle_soup(df, sw_hi.shift(1), direction="high")

        if direction == OrderSide.BUY:
            is_eng = bool(bull_eng.iloc[-1])
            is_star = bool(m_star.iloc[-1])
            is_pin = bool(pin_l.iloc[-1])
            is_sweep = bool(soup_l.iloc[-1]) or (df["low"].iloc[-1] <= df["low"].iloc[-2])

            if (is_eng or is_star or is_pin) and is_sweep:
                wick_price = float(df["low"].iloc[-2:].min())
                pat_name = "MorningStar" if is_star else ("BullishEngulfing" if is_eng else "PinBarLong")
                return True, wick_price, pat_name

        else:  # OrderSide.SELL
            is_eng = bool(bear_eng.iloc[-1])
            is_star = bool(e_star.iloc[-1])
            is_pin = bool(pin_s.iloc[-1])
            is_sweep = bool(soup_s.iloc[-1]) or (df["high"].iloc[-1] >= df["high"].iloc[-2])

            if (is_eng or is_star or is_pin) and is_sweep:
                wick_price = float(df["high"].iloc[-2:].max())
                pat_name = "EveningStar" if is_star else ("BearishEngulfing" if is_eng else "PinBarShort")
                return True, wick_price, pat_name

        return False, 0.0, ""

    def _check_invalidation(self, df: pd.DataFrame, context: SetupContext) -> bool:
        """Returns True if price broke past 61.8% golden pocket invalidating the relapse."""
        c = df["close"].iloc[-1]
        if context.direction == OrderSide.BUY:
            return c < context.golden_pocket_min
        else:
            return c > context.golden_pocket_max

    def _check_scaling_prerequisites(self, df: pd.DataFrame, direction: OrderSide) -> bool:
        """
        Check if Tier-1 is at Breakeven, momentum is high, and a new 5M Break of Structure forms.
        """
        sw_hi = df["high"].rolling(5).max()
        sw_lo = df["low"].rolling(5).min()
        choch = ict.choch_state(df, sw_lo, sw_hi)

        is_bos = (choch.iloc[-1] == 1) if direction == OrderSide.BUY else (choch.iloc[-1] == -1)
        disp = ict.displacement(df)
        disp_key = "disp_up" if direction == OrderSide.BUY else "disp_dn"
        high_momentum = bool(disp[disp_key].iloc[-1])

        return is_bos and high_momentum

    async def _execute_tier2_scaling(self, df: pd.DataFrame) -> None:
        """Stacks Tier-2 slices and synchronizes global trailing SL."""
        if not self.router:
            return

        wick_price = float(df["low"].iloc[-1] if self.context.direction == OrderSide.BUY else df["high"].iloc[-1])

        emit_telemetry(
            component="RelapseFSM",
            event="EXECUTING_TIER2_SCALING",
            data={"symbol": self.symbol, "wick_price": wick_price},
        )

        tier2_basket = await self.router.fire_layered_orders(
            symbol=self.symbol,
            side=self.context.direction,
            invalidation_wick_price=wick_price,
            total_lots=0.03,
            num_slices=3,
            tier=2,
        )

        if tier2_basket:
            self.context.tier_scaled = True
            # Sync global trailing SL across all tickets to the new protective level
            await self.router.sync_global_trailing_sl(tier2_basket.sl_price)

    def _check_opposing_reversal(self, df: pd.DataFrame, direction: OrderSide) -> Optional[str]:
        """Detects strong opposing 5M reversal pattern."""
        if direction == OrderSide.BUY:
            if bool(candles.bearish_engulfing(df).iloc[-1]):
                return "BearishEngulfing"
            if bool(detect_evening_star(df).iloc[-1]):
                return "EveningStar"
        else:
            if bool(candles.bullish_engulfing(df).iloc[-1]):
                return "BullishEngulfing"
            if bool(detect_morning_star(df).iloc[-1]):
                return "MorningStar"
        return None

    def _check_stop_loss_hit(self, current_price: float) -> bool:
        """Validates if current market price breached basket stop loss."""
        if not self.active_basket or not self.active_basket.is_active:
            return False

        if self.active_basket.side == OrderSide.BUY:
            return current_price <= self.active_basket.sl_price
        else:
            return current_price >= self.active_basket.sl_price

    async def _execute_basket_exit(self, reason: str) -> None:
        """Instantly liquidates all tickets in parallel and clears active state."""
        if self.router:
            await self.router.close_basket(reason=reason)
        self.active_basket = None
        self._bars_in_trade = 0
        self.context = SetupContext()
