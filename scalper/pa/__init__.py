"""Price-action detectors -- flat, vectorized, strictly causal.

Reimplemented from the studied libraries:

  * stolgo (lib/stolgo/pa/patterns + pa/levels): doji, hammer,
    inverted hammer, engulfing, streaks, consolidation/breakout,
    prior rolling support/resistance, swing levels.
  * motivewave candlestick study: the full pattern catalogue (motive.py).
  * ICT knowledge library: FVG, order blocks, MSS/CHoCH, liquidity
    levels, killzones, asian range, turtle soup (ict.py).

Every function takes a DataFrame with open/high/low/close[/volume] columns
sorted oldest-first and returns a boolean Series whose value at bar i uses
ONLY bars <= i.  The engine consumes the numpy fast paths in fast.py.
"""
from __future__ import annotations

from .candles import (bearish, bearish_engulfing, bullish, bullish_engulfing,
                      doji, hammer, higher_close_streak, inverted_hammer,
                      lower_close_streak)
from .levels import (breakout_down, breakout_up, consolidation,
                     prior_resistance, prior_support, range_high, range_low)

__all__ = [
    "bullish", "bearish", "doji", "hammer", "inverted_hammer",
    "bullish_engulfing", "bearish_engulfing",
    "higher_close_streak", "lower_close_streak",
    "prior_resistance", "prior_support", "range_high", "range_low",
    "consolidation", "breakout_up", "breakout_down",
]
