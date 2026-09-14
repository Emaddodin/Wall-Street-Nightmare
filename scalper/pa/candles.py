"""Candlestick detectors -- exact rules from stolgo pa/patterns/candles.py
and pa/patterns/streaks.py (MIT, studied 2026-09-08).

Threshold defaults are the library's own defaults; the engine's config may
override them.
"""
from __future__ import annotations

import pandas as pd


def _ohlc(df: pd.DataFrame):
    return df["open"], df["high"], df["low"], df["close"]


def _wick_parts(df: pd.DataFrame):
    o, h, l, c = _ohlc(df)
    rng = (h - l).replace(0, float("nan"))
    body = (c - o).abs()
    upper = h - pd.concat([c, o], axis=1).max(axis=1)
    lower = pd.concat([c, o], axis=1).min(axis=1) - l
    return rng, body, upper, lower


def bullish(df: pd.DataFrame) -> pd.Series:
    return (df["close"] > df["open"]).rename("bullish")


def bearish(df: pd.DataFrame) -> pd.Series:
    return (df["close"] < df["open"]).rename("bearish")


def doji(df: pd.DataFrame, body_pct: float = 0.1) -> pd.Series:
    o, h, l, c = _ohlc(df)
    rng = (h - l).replace(0, float("nan"))
    body = (c - o).abs()
    return (body <= body_pct * rng).rename("doji")


def hammer(df: pd.DataFrame, lower_wick: float = 0.6, body: float = 0.2,
           upper_wick: float = 0.2) -> pd.Series:
    rng, body_sz, up, low = _wick_parts(df)
    return ((body_sz <= body * rng) & (up <= upper_wick * rng)
            & (low >= lower_wick * rng)).rename("hammer")


def inverted_hammer(df: pd.DataFrame, lower_wick: float = 0.2,
                    body: float = 0.2, upper_wick: float = 0.6) -> pd.Series:
    rng, body_sz, up, low = _wick_parts(df)
    return ((body_sz <= body * rng) & (low <= lower_wick * rng)
            & (up >= upper_wick * rng)).rename("inverted_hammer")


def bullish_engulfing(df: pd.DataFrame) -> pd.Series:
    o, _, _, c = _ohlc(df)
    prev_o, prev_c = o.shift(1), c.shift(1)
    return ((prev_c < prev_o) & (c > o) & (c > prev_o)
            & (o < prev_c)).rename("bullish_engulfing")


def bearish_engulfing(df: pd.DataFrame) -> pd.Series:
    o, _, _, c = _ohlc(df)
    prev_o, prev_c = o.shift(1), c.shift(1)
    return ((prev_c > prev_o) & (c < o) & (c < prev_o)
            & (o > prev_c)).rename("bearish_engulfing")


def higher_close_streak(df: pd.DataFrame, periods: int = 3) -> pd.Series:
    """`periods` consecutive higher closes (strictly), evaluated causally."""
    c = df["close"]
    out = (c > c.shift(1)).rolling(periods, min_periods=periods).agg("min")
    return out.fillna(0.0).astype(bool).rename("higher_close_streak")


def lower_close_streak(df: pd.DataFrame, periods: int = 3) -> pd.Series:
    c = df["close"]
    out = (c < c.shift(1)).rolling(periods, min_periods=periods).agg("min")
    return out.fillna(0.0).astype(bool).rename("lower_close_streak")


def pin_bar_long(df: pd.DataFrame, lower_wick: float = 0.5,
                 body: float = 0.35) -> pd.Series:
    """A hammer with a strictly positive close (the 'rejection' at a level)."""
    rng, body_sz, up, low = _wick_parts(df)
    return ((body_sz <= body * rng) & (low >= lower_wick * rng)
            & (df["close"] > df["open"])).rename("pin_bar_long")


def pin_bar_short(df: pd.DataFrame, upper_wick: float = 0.5,
                  body: float = 0.35) -> pd.Series:
    rng, body_sz, up, low = _wick_parts(df)
    return ((body_sz <= body * rng) & (up >= upper_wick * rng)
            & (df["close"] < df["open"])).rename("pin_bar_short")
