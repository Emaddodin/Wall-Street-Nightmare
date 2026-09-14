"""Levels + breakout rules -- exact semantics from stolgo pa/levels and
pa/patterns/structure.py.

Key discipline carried over from the library: every rolling level uses
PRIOR bars only (shift(1) before rolling), so a bar never helps define the
level it is tested against.
"""
from __future__ import annotations

import pandas as pd


def _prior_max(s: pd.Series, window: int) -> pd.Series:
    return s.shift(1).rolling(window, min_periods=window).max()


def _prior_min(s: pd.Series, window: int) -> pd.Series:
    return s.shift(1).rolling(window, min_periods=window).min()


def prior_resistance(df: pd.DataFrame, lookback: int) -> pd.Series:
    """Highest high of the PREVIOUS `lookback` bars."""
    return _prior_max(df["high"], lookback).rename(f"resistance({lookback})")


def prior_support(df: pd.DataFrame, lookback: int) -> pd.Series:
    """Lowest low of the PREVIOUS `lookback` bars."""
    return _prior_min(df["low"], lookback).rename(f"support({lookback})")


def range_high(df: pd.DataFrame, window: int) -> pd.Series:
    return _prior_max(df["high"], window).rename(f"range_high({window})")


def range_low(df: pd.DataFrame, window: int) -> pd.Series:
    return _prior_min(df["low"], window).rename(f"range_low({window})")


def consolidation(df: pd.DataFrame, periods: int,
                  range_pct: float = 0.12) -> pd.Series:
    """Prior `periods` bars trade inside a range whose width (high-low over
    the window, as a fraction of the window's mean close) is <= range_pct."""
    h = _prior_max(df["high"], periods)
    l = _prior_min(df["low"], periods)
    mid = df["close"].shift(1).rolling(periods, min_periods=periods).mean()
    width = (h - l) / mid.replace(0, float("nan"))
    return (width <= range_pct).fillna(False).rename("consolidation")


def breakout_up(df: pd.DataFrame, periods: int,
                range_pct: float = 0.12) -> pd.Series:
    """Close above the prior range high, out of a consolidation."""
    cons = consolidation(df, periods, range_pct)
    level = range_high(df, periods)
    return (cons & (df["close"] > level)).rename("breakout_up")


def breakout_down(df: pd.DataFrame, periods: int,
                  range_pct: float = 0.12) -> pd.Series:
    cons = consolidation(df, periods, range_pct)
    level = range_low(df, periods)
    return (cons & (df["close"] < level)).rename("breakout_down")
