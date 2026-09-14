"""Causal indicator computations.

Every function here is strictly causal: the value at bar i depends only on
bars <= i.  The backtester's no-lookahead discipline is built on these plus
the confirmed-swing helpers at the bottom of the file.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ----------------------------------------------------------------------
def ema(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(span=period, adjust=False, min_periods=period).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder-smoothed ATR."""
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return _wilder(tr, period)


def _wilder(s: pd.Series, period: int) -> pd.Series:
    """Wilder smoothing: SMA seed of first `period` values, then the exact
    recursive Wilder identity atr_t = (atr_{t-1}*(p-1) + x_t) / p.

    Computed in raw numpy (the .iloc[i]= per-bar variant cost ~35s on a
    190k-bar series; this one ~0.1s)."""
    n = len(s)
    res = np.full(n, np.nan)
    if n < period:
        return pd.Series(res, index=s.index)
    vals = s.to_numpy(dtype=float)
    prev = float(np.nanmean(vals[:period]))
    res[period - 1] = prev
    for i in range(period, n):
        prev = (prev * (period - 1) + vals[i]) / period
        res[i] = prev
    return pd.Series(res, index=s.index)


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder ADX.  Returns NaN until enough bars exist."""
    h, l = df["high"], df["low"]
    up = h.diff()
    down = -l.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    atr_s = atr(df, period)
    atr_safe = atr_s.replace(0.0, np.nan)
    plus_di = 100.0 * _wilder(plus_dm, period) / atr_safe
    minus_di = 100.0 * _wilder(minus_dm, period) / atr_safe
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return _wilder(dx, period)


def vwap_session(df: pd.DataFrame) -> pd.Series:
    """Session VWAP, reset at 00:00 UTC each day (futures convention)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = tp * df["volume"]
    day = df["open_time"] // 86_400_000
    cum_pv = pv.groupby(day).cumsum()
    cum_v = df["volume"].groupby(day).cumsum()
    return (cum_pv / cum_v.replace(0.0, np.nan)).rename("vwap")


def rolling_median_body(df: pd.DataFrame, window: int) -> pd.Series:
    body = (df["close"] - df["open"]).abs()
    return body.rolling(window, min_periods=window // 2).median()


def rolling_volume_avg(df: pd.DataFrame, window: int) -> pd.Series:
    return df["volume"].rolling(window, min_periods=window // 2).mean()


def ema_slope_pct(ema_s: pd.Series, bars: int) -> pd.Series:
    """Relative slope over `bars`: (ema_t - ema_{t-bars}) / ema_{t-bars}."""
    prev = ema_s.shift(bars)
    return (ema_s - prev) / prev


# ----------------------------------------------------------------------
# Swing points (fractal highs/lows) with explicit confirmation lag.
# ----------------------------------------------------------------------
def swing_points(df: pd.DataFrame, arm: int) -> tuple[pd.Series, pd.Series]:
    """Confirmed swing highs and lows, VECTORIZED.

    A swing high at bar i is confirmed only at bar i+arm (needs `arm` bars on
    each side).  The returned Series is indexed by the CONFIRMATION bar: the
    value is the swing price.  Indexing by confirmation bar makes "what do I
    know at bar t" a plain positional slice: everything up to t is known.
    """
    h = pd.Series(df["high"].to_numpy(dtype=float))
    l = pd.Series(df["low"].to_numpy(dtype=float))
    if arm <= 0:
        return pd.Series(np.nan, index=h.index), pd.Series(np.nan, index=l.index)
    left_hi = h.rolling(arm, min_periods=arm).max().shift(1)
    right_hi = h[::-1].rolling(arm, min_periods=arm).max().shift(1)[::-1]
    is_sh = (h > left_hi) & (h > right_hi)
    left_lo = l.rolling(arm, min_periods=arm).min().shift(1)
    right_lo = l[::-1].rolling(arm, min_periods=arm).min().shift(1)[::-1]
    is_sl = (l < left_lo) & (l < right_lo)
    sh = pd.Series(np.where(is_sh.to_numpy(), h.to_numpy(), np.nan),
                   index=h.index).shift(arm)
    sl = pd.Series(np.where(is_sl.to_numpy(), l.to_numpy(), np.nan),
                   index=l.index).shift(arm)
    return sh, sl


def confirmed_swings(sw: pd.Series, up_to: int) -> pd.Series:
    """The swing series restricted to confirmations known at bar `up_to`
    (inclusive).  Because the series is indexed by confirmation bar, this is
    a positional slice."""
    return sw.iloc[: up_to + 1].dropna()


def last_swing(sw: pd.Series, up_to: int) -> tuple[int, float] | None:
    """(confirm_bar, price) of the most recent confirmed swing known at
    bar `up_to`, or None."""
    known = sw.iloc[: up_to + 1].dropna()
    if known.empty:
        return None
    return int(known.index[-1]), float(known.iloc[-1])


def market_structure(df: pd.DataFrame, arm: int, lookback: int, up_to: int,
                     direction: str) -> bool:
    """HH/HL (direction='up') or LH/LL (direction='down') over the last
    `lookback` bars, evaluated with knowledge through bar `up_to`.

    'up': last two confirmed swing highs are rising AND last two confirmed
    swing lows are rising.  'down' mirrors it."""
    sh, sl = swing_points(df, arm)
    hs = sh.iloc[max(0, up_to - lookback + 1): up_to + 1].dropna()
    ls = sl.iloc[max(0, up_to - lookback + 1): up_to + 1].dropna()
    if len(hs) < 2 or len(ls) < 2:
        return False
    if direction == "up":
        return bool(hs.iloc[-1] > hs.iloc[-2] and ls.iloc[-1] > ls.iloc[-2])
    return bool(hs.iloc[-1] < hs.iloc[-2] and ls.iloc[-1] < ls.iloc[-2])
