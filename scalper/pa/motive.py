"""MotiveWave candlestick pattern catalogue.

Exact rules reimplemented from the studied MotiveWave study
(src/CandlestickPatterns.java, v1.x, 33 patterns) -- bar-by-bar, same
hard-coded ratios, same priority order (triple > double > single).

Conventions (bar 0 = completion bar):
  isBullish = C > O ; isBearish = C < O
  body = |C-O| ; range = H-L ; upperShadow = H-max(O,C) ; lowerShadow = min(O,C)-L

Patterns detect on CLOSED bars only.  The study's optional 50/200-MA trend
filter is NOT reproduced here -- this engine supplies its own HTF regime
context (the study's filter reads SIDEWAYS on low TFs and disables itself
anyway).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DOJI = 0.1          # hard-coded in the study


def _fields(df: pd.DataFrame):
    o, h, l, c = (df["open"], df["high"], df["low"], df["close"])
    body = (c - o).abs()
    rng = (h - l).replace(0, np.nan)
    up = h - pd.concat([c, o], axis=1).max(axis=1)
    lo = pd.concat([c, o], axis=1).min(axis=1) - l
    return o, h, l, c, body, rng, up, lo


def _S(cond, name):
    return cond.fillna(False).astype(bool).rename(name)


# ------------------------------------------------------------------ single
def doji(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S(body / rng < DOJI, "doji")


def long_legged_doji(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((body / rng < DOJI) & (up > 2 * body) & (lo > 2 * body),
              "long_legged_doji")


def dragonfly_doji(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((body / rng < DOJI) & (lo > 0.6 * rng) & (up < 0.1 * rng),
              "dragonfly_doji")


def gravestone_doji(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((body / rng < DOJI) & (up > 0.6 * rng) & (lo < 0.1 * rng),
              "gravestone_doji")


def spinning_top(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    ratio = body / rng
    return _S((ratio > 0.1) & (ratio < 0.3) & (up > body) & (lo > body),
              "spinning_top")


def hammer(df):
    """Study variant: BULLISH body required."""
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((c > o) & (lo > 2 * body) & (up < 0.5 * body), "hammer")


def inverted_hammer(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((c > o) & (up > 2 * body) & (lo < 0.5 * body), "inverted_hammer")


def shooting_star(df):
    """Study variant: BEARISH body required."""
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((c < o) & (up > 2 * body) & (lo < 0.5 * body), "shooting_star")


def hanging_man(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((c < o) & (lo > 2 * body) & (up < 0.5 * body), "hanging_man")


def bullish_marubozu(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((c > o) & (body / rng > 0.95), "bullish_marubozu")


def bearish_marubozu(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((c < o) & (body / rng > 0.95), "bearish_marubozu")


# ------------------------------------------------------------------ double
def bullish_engulfing(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((c.shift(1) < o.shift(1)) & (c > o)
              & (o <= c.shift(1)) & (c >= o.shift(1)), "bullish_engulfing")


def bearish_engulfing(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((c.shift(1) > o.shift(1)) & (c < o)
              & (o >= c.shift(1)) & (c <= o.shift(1)), "bearish_engulfing")


def bullish_harami(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((c.shift(1) < o.shift(1)) & (c > o)
              & (body < 0.5 * body.shift(1))
              & (o > c.shift(1)) & (c < o.shift(1)), "bullish_harami")


def bearish_harami(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((c.shift(1) > o.shift(1)) & (c < o)
              & (body < 0.5 * body.shift(1))
              & (o < c.shift(1)) & (c > o.shift(1)), "bearish_harami")


def piercing_line(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    mid = (o.shift(1) + c.shift(1)) / 2
    return _S((c.shift(1) < o.shift(1)) & (c > o)
              & (o < c.shift(1)) & (c > mid) & (c < o.shift(1)),
              "piercing_line")


def dark_cloud_cover(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    mid = (o.shift(1) + c.shift(1)) / 2
    return _S((c.shift(1) > o.shift(1)) & (c < o)
              & (o > c.shift(1)) & (c < mid) & (c > o.shift(1)),
              "dark_cloud_cover")


def tweezer_bottom(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    avg = rng.shift(1).rolling(14, min_periods=1).mean()
    return _S(((l - l.shift(1)).abs() / avg < 0.05)
              & ((c > o) != (c.shift(1) > o.shift(1))), "tweezer_bottom")


def tweezer_top(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    avg = rng.shift(1).rolling(14, min_periods=1).mean()
    return _S(((h - h.shift(1)).abs() / avg < 0.05)
              & ((c > o) != (c.shift(1) > o.shift(1))), "tweezer_top")


def bullish_kicker(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((c.shift(1) < o.shift(1)) & (c > o) & (o > o.shift(1)),
              "bullish_kicker")


def bearish_kicker(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((c.shift(1) > o.shift(1)) & (c < o) & (o < o.shift(1)),
              "bearish_kicker")


# ------------------------------------------------------------------ triple
def morning_star(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    mid1 = pd.concat([o.shift(1), c.shift(1)], axis=1).max(axis=1)
    return _S((c.shift(2) < o.shift(2)) & (c > o)
              & (body.shift(1) < 0.3 * body.shift(2))
              & (mid1 < c.shift(2))
              & (body > 0.5 * body.shift(2)), "morning_star")


def evening_star(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    min1 = pd.concat([o.shift(1), c.shift(1)], axis=1).min(axis=1)
    return _S((c.shift(2) > o.shift(2)) & (c < o)
              & (body.shift(1) < 0.3 * body.shift(2))
              & (min1 > c.shift(2))
              & (body > 0.5 * body.shift(2)), "evening_star")


def morning_doji_star(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((c.shift(2) < o.shift(2)) & (c > o)
              & (body.shift(1) / rng.shift(1) < DOJI)
              & (h.shift(1) < c.shift(2)), "morning_doji_star")


def evening_doji_star(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((c.shift(2) > o.shift(2)) & (c < o)
              & (body.shift(1) / rng.shift(1) < DOJI)
              & (l.shift(1) > c.shift(2)), "evening_doji_star")


def bullish_abandoned_baby(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((c.shift(2) < o.shift(2)) & (c > o)
              & (body.shift(1) / rng.shift(1) < DOJI)
              & (h.shift(1) < l.shift(2)) & (h.shift(1) < l),
              "bullish_abandoned_baby")


def bearish_abandoned_baby(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((c.shift(2) > o.shift(2)) & (c < o)
              & (body.shift(1) / rng.shift(1) < DOJI)
              & (l.shift(1) > h.shift(2)) & (l.shift(1) > h),
              "bearish_abandoned_baby")


def three_white_soldiers(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((c.shift(2) > o.shift(2)) & (c.shift(1) > o.shift(1)) & (c > o)
              & (c.shift(1) > c.shift(2)) & (c > c.shift(1)),
              "three_white_soldiers")


def three_black_crows(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((c.shift(2) < o.shift(2)) & (c.shift(1) < o.shift(1)) & (c < o)
              & (c.shift(1) < c.shift(2)) & (c < c.shift(1)),
              "three_black_crows")


def three_inside_up(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((c.shift(2) < o.shift(2)) & (c.shift(1) > o.shift(1)) & (c > o)
              & (o.shift(1) > c.shift(2)) & (c.shift(1) < o.shift(2))
              & (c > o.shift(2)), "three_inside_up")


def three_inside_down(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((c.shift(2) > o.shift(2)) & (c.shift(1) < o.shift(1)) & (c < o)
              & (o.shift(1) < c.shift(2)) & (c.shift(1) > o.shift(2))
              & (c < o.shift(2)), "three_inside_down")


def three_outside_up(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((c.shift(2) < o.shift(2)) & (c.shift(1) > o.shift(1)) & (c > o)
              & (o.shift(1) <= c.shift(2)) & (c.shift(1) >= o.shift(2))
              & (c > c.shift(1)), "three_outside_up")


def three_outside_down(df):
    o, h, l, c, body, rng, up, lo = _fields(df)
    return _S((c.shift(2) > o.shift(2)) & (c.shift(1) < o.shift(1)) & (c < o)
              & (o.shift(1) >= c.shift(2)) & (c.shift(1) <= o.shift(2))
              & (c < c.shift(1)), "three_outside_down")


# ------------------------------------------------------------------ table
BULLISH_PATTERNS = {
    "hammer": hammer, "inverted_hammer": inverted_hammer,
    "dragonfly_doji": dragonfly_doji, "bullish_marubozu": bullish_marubozu,
    "bullish_engulfing": bullish_engulfing, "bullish_harami": bullish_harami,
    "piercing_line": piercing_line, "tweezer_bottom": tweezer_bottom,
    "bullish_kicker": bullish_kicker, "morning_star": morning_star,
    "morning_doji_star": morning_doji_star,
    "bullish_abandoned_baby": bullish_abandoned_baby,
    "three_white_soldiers": three_white_soldiers,
    "three_inside_up": three_inside_up, "three_outside_up": three_outside_up,
}
BEARISH_PATTERNS = {
    "shooting_star": shooting_star, "hanging_man": hanging_man,
    "gravestone_doji": gravestone_doji, "bearish_marubozu": bearish_marubozu,
    "bearish_engulfing": bearish_engulfing, "bearish_harami": bearish_harami,
    "dark_cloud_cover": dark_cloud_cover, "tweezer_top": tweezer_top,
    "bearish_kicker": bearish_kicker, "evening_star": evening_star,
    "evening_doji_star": evening_doji_star,
    "bearish_abandoned_baby": bearish_abandoned_baby,
    "three_black_crows": three_black_crows,
    "three_inside_down": three_inside_down,
    "three_outside_down": three_outside_down,
}
NEUTRAL_PATTERNS = {
    "doji": doji, "long_legged_doji": long_legged_doji,
    "spinning_top": spinning_top,
}


def mw_patterns(df: pd.DataFrame) -> pd.DataFrame:
    """One boolean column per pattern, indexed like df."""
    cols = {}
    for name, fn in {**NEUTRAL_PATTERNS, **BULLISH_PATTERNS,
                     **BEARISH_PATTERNS}.items():
        cols[name] = fn(df)
    return pd.DataFrame(cols, index=df.index)


def mw_signal(df: pd.DataFrame, names: list[str],
              direction: str = "long") -> pd.Series:
    """True where ANY of the named patterns fired in the given direction."""
    if direction == "long":
        names = [n for n in names if n in BULLISH_PATTERNS]
    elif direction == "short":
        names = [n for n in names if n in BEARISH_PATTERNS]
    if not names:
        return pd.Series(False, index=df.index)
    tbl = mw_patterns(df)
    return tbl[names].any(axis=1).fillna(False)
