"""
quant/hft/exits/chandelier.py
===============================
Dynamic Chandelier Exit with ATR Ratchet mechanism.

Chandelier Exit (Long):
    CE_long  = HighestHigh(n) - ATR(n) * multiplier

Chandelier Exit (Short):
    CE_short = LowestLow(n) + ATR(n) * multiplier

ATR Ratchet:
    As unrealised profit grows, the multiplier is stepped DOWN to lock in gains:
        profit >= 2 * ATR → multiplier = max(multiplier - 0.5, min_mult)
        profit >= 4 * ATR → multiplier = max(multiplier - 1.0, min_mult)
        profit >= 6 * ATR → multiplier = 1.0

True Range:
    TR = max(high - low, |high - prev_close|, |low - prev_close|)
ATR(n) = EMA(TR, n)  (Wilder smoothing: alpha = 1/n)
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Deque

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ExitState:
    stop_price: float
    multiplier: float
    atr: float
    highest_high: float
    lowest_low: float
    triggered: bool = False
    trigger_reason: str = ""


class ChandelierExit:
    """
    Trailing Chandelier Exit with dynamic ATR Ratchet.

    Parameters
    ----------
    atr_period      : look-back period for ATR (default 22)
    initial_mult    : starting multiplier (default 3.0)
    min_mult        : minimum multiplier floor (default 1.0)
    ratchet_steps   : list of (profit_atr_multiple, new_multiplier) step-downs
    """

    DEFAULT_RATCHET = [
        (4.0, 2.5),   # at 4x ATR profit → tighten to 2.5
        (6.0, 2.0),   # at 6x ATR profit → tighten to 2.0
        (8.0, 1.5),   # at 8x ATR profit → tighten to 1.5
        (10.0, 1.0),  # at 10x ATR profit → lock to 1.0
    ]

    def __init__(
        self,
        atr_period: int = 22,
        initial_mult: float = 3.0,
        min_mult: float = 1.0,
        ratchet_steps: list[tuple[float, float]] | None = None,
    ) -> None:
        self.atr_period = atr_period
        self.initial_mult = initial_mult
        self.min_mult = min_mult
        self.ratchet_steps = sorted(
            ratchet_steps or self.DEFAULT_RATCHET, key=lambda x: x[0]
        )

        # Rolling price buffer
        self._highs: Deque[float] = deque(maxlen=atr_period)
        self._lows: Deque[float] = deque(maxlen=atr_period)
        self._closes: Deque[float] = deque(maxlen=atr_period)

        # ATR state (Wilder EMA)
        self._atr: float = 0.0
        self._wilder_alpha: float = 1.0 / atr_period

        # Per-position state
        self._current_mult: float = initial_mult
        self._highest_high: float = 0.0
        self._lowest_low: float = float("inf")
        self._stop: float = 0.0
        self._entry_price: float = 0.0
        self._side: str = "long"

    # ------------------------------------------------------------------
    # Price feed
    # ------------------------------------------------------------------

    def update_bar(self, high: float, low: float, close: float) -> None:
        """
        Feed a new price bar (OHLC tick or aggregated micro-bar).
        Updates ATR via Wilder EMA and rolling High/Low buffers.
        """
        prev_close = self._closes[-1] if self._closes else close
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )

        if self._atr == 0.0:
            self._atr = tr
        else:
            self._atr = self._wilder_alpha * tr + (1 - self._wilder_alpha) * self._atr

        self._highs.append(high)
        self._lows.append(low)
        self._closes.append(close)

    # ------------------------------------------------------------------
    # Position initialisation
    # ------------------------------------------------------------------

    def init_position(self, side: str, entry_price: float) -> None:
        """Call once when a new position is opened."""
        self._side = side.lower()
        self._entry_price = entry_price
        self._current_mult = self.initial_mult
        if self._side == "long":
            self._highest_high = entry_price
            self._stop = entry_price - self._atr * self._current_mult
        else:
            self._lowest_low = entry_price
            self._stop = entry_price + self._atr * self._current_mult

    # ------------------------------------------------------------------
    # Per-tick evaluation
    # ------------------------------------------------------------------

    def evaluate(self, current_price: float) -> ExitState:
        """
        Evaluate stop on each new tick / micro-bar close.
        Applies ATR Ratchet and updates trailing stop.
        Returns ExitState with triggered=True if stop hit.
        """
        atr = max(self._atr, 1e-9)

        # --- ATR Ratchet ---
        unrealized_pnl = (
            current_price - self._entry_price
            if self._side == "long"
            else self._entry_price - current_price
        )
        profit_in_atrs = unrealized_pnl / atr

        # Walk down ratchet steps (apply highest applicable tightening)
        target_mult = self.initial_mult
        for threshold, new_mult in self.ratchet_steps:
            if profit_in_atrs >= threshold:
                target_mult = new_mult
        # Ratchet only tightens, never loosens
        self._current_mult = min(self._current_mult, max(target_mult, self.min_mult))

        # --- Trailing stop update ---
        if self._side == "long":
            self._highest_high = max(self._highest_high, current_price)
            new_stop = self._highest_high - atr * self._current_mult
            # Stop only moves up
            self._stop = max(self._stop, new_stop)
            triggered = current_price <= self._stop

        else:  # short
            self._lowest_low = min(self._lowest_low, current_price)
            new_stop = self._lowest_low + atr * self._current_mult
            # Stop only moves down
            self._stop = min(self._stop, new_stop)
            triggered = current_price >= self._stop

        return ExitState(
            stop_price=self._stop,
            multiplier=self._current_mult,
            atr=atr,
            highest_high=self._highest_high if self._side == "long" else current_price,
            lowest_low=self._lowest_low if self._side == "short" else current_price,
            triggered=triggered,
            trigger_reason="chandelier_stop" if triggered else "",
        )

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    @property
    def atr(self) -> float:
        return self._atr

    @property
    def current_stop(self) -> float:
        return self._stop

    @property
    def current_multiplier(self) -> float:
        return self._current_mult

    def reset(self) -> None:
        """Reset position tracking (keep ATR history)."""
        self._current_mult = self.initial_mult
        self._highest_high = 0.0
        self._lowest_low = float("inf")
        self._stop = 0.0
        self._entry_price = 0.0
