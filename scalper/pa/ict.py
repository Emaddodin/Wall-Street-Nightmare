"""ICT concepts, quantifiable and causal -- reimplemented from the studied
ict-knowledge-library (concepts/ folder, 2026-09-08).

Every detector is bar-by-bar deterministic on OHLCV; the value at bar i uses
ONLY bars <= i.  Killzone windows follow the library's PUBLIC set (the 2017
mentorship set is available via the zone table) expressed in FIXED UTC
(EDT alignment, no DST switching -- a documented simplification; crypto
markets never close so the exact minute of a session boundary matters less
than the consistency of the filter).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- killzones
# (name, start_hour_utc, end_hour_utc) -- public set, EDT = UTC-4
KILLZONES = {
    "asia": (0, 4),
    "london_open": (6, 9),
    "ny_am": (12, 15),
    "london_close": (14, 16),
    "ny_pm": (17, 20),
}
# 2017 mentorship alternate set
KILLZONES_2017 = {
    "london": (5, 9),
    "ny": (11, 14),
}


def killzone(t_ms: np.ndarray, zones: list[str],
             table: dict = KILLZONES) -> np.ndarray:
    """Boolean mask: bar OPEN time inside any of the named killzones
    (hour-of-day in UTC)."""
    hours = ((t_ms // 3_600_000) % 24).astype(int)
    mask = np.zeros(len(t_ms), dtype=bool)
    for z in zones:
        lo, hi = table[z]
        if hi >= lo:
            mask |= (hours >= lo) & (hours < hi)
        else:  # wraps midnight
            mask |= (hours >= lo) | (hours < hi)
    return mask


def _session_day(t_ms: np.ndarray, start_hour: int) -> np.ndarray:
    """Day key: a session starting at start_hour UTC belongs to the day of
    its start."""
    return (t_ms - start_hour * 3_600_000) // 86_400_000


def asian_range(df: pd.DataFrame, start_hour: int = 0,
                end_hour: int = 4) -> pd.DataFrame:
    """Per-day Asian-session range (high/low/eq) published on every bar of
    that session's day, computed from COMPLETED Asian bars only (causal).
    Uses the library's Asia killzone 00:00-04:00 UTC by default."""
    t = df["open_time"].to_numpy(dtype=np.int64)
    day = _session_day(t, start_hour)
    hours = ((t // 3_600_000) % 24).astype(int)
    in_asia = ((hours >= start_hour) & (hours < end_hour))
    out = pd.DataFrame(index=df.index)
    a_t = t[in_asia]
    if len(a_t) == 0:
        out["asia_high"] = np.nan
        out["asia_low"] = np.nan
        out["asia_eq"] = np.nan
        return out
    a_day = day[in_asia]
    grp = pd.Series(a_t).groupby(a_day)
    a_hi = df["high"].to_numpy()[in_asia]
    a_lo = df["low"].to_numpy()[in_asia]
    # running max/min of completed asian bars per day, via cummax within day
    cum_hi = pd.Series(a_hi).groupby(a_day).cummax().to_numpy()
    cum_lo = pd.Series(a_lo).groupby(a_day).cummin().to_numpy()
    pos = np.searchsorted(a_t, t, side="right") - 1
    valid = pos >= 0
    p = np.clip(pos, 0, len(a_t) - 1)
    same_day = valid & (a_day[p] == day)
    hi = np.full(len(df), np.nan)
    lo = np.full(len(df), np.nan)
    hi[same_day] = cum_hi[p[same_day]]
    lo[same_day] = cum_lo[p[same_day]]
    out["asia_high"] = hi
    out["asia_low"] = lo
    out["asia_eq"] = (hi + lo) / 2.0
    return out


# ---------------------------------------------------------------- FVG
def fvg_state(df: pd.DataFrame, max_age: int = 30) -> pd.DataFrame:
    """Rolling per-bar state of the freshest UNMITIGATED FVG in each
    direction (causal: a gap exists at bar i if bars i-2..i formed it and
    no later bar's wick has touched its CE -- mitigation = CE touch).

    Bullish: low[i] > high[i-2], zone [H[i-2], L[i]], CE = midpoint.
    Bearish: high[i] < low[i-2], zone [H[i], L[i-2]], CE = midpoint.

    Columns: bull_fvg (bool), bull_ce, bull_gap_hi, bull_gap_lo, bear_*.
    O(n) scan per symbol -- fine at 3m/15m sizes."""
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    n = len(df)
    out = pd.DataFrame(index=df.index, columns=[
        "bull_fvg", "bull_ce", "bull_gap_hi", "bull_gap_lo",
        "bear_fvg", "bear_ce", "bear_gap_hi", "bear_gap_lo"], dtype=float)
    out["bull_fvg"] = False
    out["bear_fvg"] = False
    cur_bull = None      # (created_at, gap_hi, gap_lo, ce)
    cur_bear = None
    for i in range(n):
        # an FVG completes AT bar i when bars i-2, i-1, i form the 3-candle
        # pattern -- known at bar i's close, used from bar i onward
        if i >= 2:
            if l[i] > h[i - 2]:
                gap_hi, gap_lo = h[i - 2], l[i]
                cur_bull = (i, gap_hi, gap_lo, (gap_hi + gap_lo) / 2.0)
            if h[i] < l[i - 2]:
                gap_hi, gap_lo = h[i], l[i - 2]
                cur_bear = (i, gap_hi, gap_lo, (gap_hi + gap_lo) / 2.0)
        # mitigation: any bar's wick reaching the CE kills the gap
        if cur_bull is not None:
            if l[i] <= cur_bull[3]:
                cur_bull = None
            elif i - cur_bull[0] > max_age:
                cur_bull = None
        if cur_bear is not None:
            if h[i] >= cur_bear[3]:
                cur_bear = None
            elif i - cur_bear[0] > max_age:
                cur_bear = None
        if cur_bull is not None:
            out.loc[df.index[i], ["bull_fvg", "bull_ce", "bull_gap_hi",
                                  "bull_gap_lo"]] = (True, *cur_bull[1:])
        if cur_bear is not None:
            out.loc[df.index[i], ["bear_fvg", "bear_ce", "bear_gap_hi",
                                  "bear_gap_lo"]] = (True, *cur_bear[1:])
    for col in ("bull_fvg", "bear_fvg"):
        out[col] = out[col].fillna(False).astype(bool)
    return out


# ---------------------------------------------------------------- displacement
def displacement(df: pd.DataFrame, body_mult: float = 1.5,
                 body_range: float = 0.70, max_opp_wick: float = 0.20,
                 avg_window: int = 20) -> pd.DataFrame:
    """ICT displacement: body >= body_mult x recent avg body AND
    body/range >= body_range AND opposing wick <= max_opp_wick x range.

    Returns bool columns disp_up / disp_dn."""
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    body = (c - o).abs()
    rng = (h - l).replace(0, np.nan)
    avg_body = body.shift(1).rolling(avg_window, min_periods=avg_window // 2).mean()
    up_wick = h - pd.concat([c, o], axis=1).max(axis=1)
    dn_wick = pd.concat([c, o], axis=1).min(axis=1) - l
    base = (body >= body_mult * avg_body) & (body / rng >= body_range)
    out = pd.DataFrame(index=df.index)
    out["disp_up"] = (base & (c > o) & (up_wick <= max_opp_wick * rng)).fillna(False)
    out["disp_dn"] = (base & (c < o) & (dn_wick <= max_opp_wick * rng)).fillna(False)
    return out


# ---------------------------------------------------------------- order block
def order_blocks(df: pd.DataFrame, swing_lo: pd.Series, swing_hi: pd.Series,
                 max_gap: int = 3, max_age: int = 60) -> pd.DataFrame:
    """Causal order blocks.

    A bullish OB forms at bar k when bar k is the LAST bearish candle before
    a bullish displacement candle (within max_gap bars) that closes beyond
    the most recent confirmed swing high.  MT = (O+C)/2, zone = [O, C]
    (body only).  The OB stays fresh for max_age bars or until its MT is
    traded through (mitigation).  O(n) scan."""
    o = df["open"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    n = len(df)
    sw_hi = swing_hi.to_numpy(dtype=float)
    sw_lo = swing_lo.to_numpy(dtype=float)
    disp = displacement(df)
    d_up = disp["disp_up"].to_numpy()
    d_dn = disp["disp_dn"].to_numpy()
    out = pd.DataFrame(index=df.index, columns=[
        "bull_ob", "bull_mt", "bull_lo", "bull_hi",
        "bear_ob", "bear_mt", "bear_lo", "bear_hi"], dtype=float)
    out["bull_ob"] = False
    out["bear_ob"] = False
    cur_bull = None      # (mt, lo, hi, created_at)
    cur_bear = None
    last_sh = np.nan
    last_sl = np.nan
    for i in range(n):
        if not np.isnan(sw_hi[i]):
            last_sh = sw_hi[i]
        if not np.isnan(sw_lo[i]):
            last_sl = sw_lo[i]
        if d_up[i] and not np.isnan(last_sh) and c[i] > last_sh:
            k = i - 1
            gap = 0
            while k >= 0 and gap <= max_gap and not (c[k] < o[k]):
                k -= 1
                gap += 1
            if k >= 0 and gap <= max_gap and (c[k] < o[k]):
                ob_lo, ob_hi = min(o[k], c[k]), max(o[k], c[k])
                cur_bull = ((ob_lo + ob_hi) / 2.0, ob_lo, ob_hi, i)
        if d_dn[i] and not np.isnan(last_sl) and c[i] < last_sl:
            k = i - 1
            gap = 0
            while k >= 0 and gap <= max_gap and not (c[k] > o[k]):
                k -= 1
                gap += 1
            if k >= 0 and gap <= max_gap and (c[k] > o[k]):
                ob_lo, ob_hi = min(o[k], c[k]), max(o[k], c[k])
                cur_bear = ((ob_lo + ob_hi) / 2.0, ob_lo, ob_hi, i)
        if cur_bull is not None:
            if l[i] <= cur_bull[0] or i - cur_bull[3] > max_age:
                cur_bull = None
        if cur_bear is not None:
            if h[i] >= cur_bear[0] or i - cur_bear[3] > max_age:
                cur_bear = None
        if cur_bull is not None:
            out.loc[df.index[i], ["bull_ob", "bull_mt", "bull_lo",
                                  "bull_hi"]] = (True, *cur_bull[:3])
        if cur_bear is not None:
            out.loc[df.index[i], ["bear_ob", "bear_mt", "bear_lo",
                                  "bear_hi"]] = (True, *cur_bear[:3])
    for col in ("bull_ob", "bear_ob"):
        out[col] = out[col].fillna(False).astype(bool)
    return out


# ---------------------------------------------------------------- CHoCH / MSS
def choch_state(df: pd.DataFrame, swing_lo: pd.Series,
                swing_hi: pd.Series) -> pd.Series:
    """+1 at a bar whose close breaks the most recent confirmed swing HIGH
    upward, -1 for a swing LOW broken downward, 0 otherwise.  Causal O(n)
    scan; returns an int8 state series."""
    c = df["close"].to_numpy(dtype=float)
    n = len(df)
    sw_hi = swing_hi.to_numpy(dtype=float)
    sw_lo = swing_lo.to_numpy(dtype=float)
    state = np.zeros(n, dtype=np.int8)
    last_sh = np.nan
    last_sl = np.nan
    for i in range(n):
        if not np.isnan(sw_hi[i]):
            last_sh = sw_hi[i]
        if not np.isnan(sw_lo[i]):
            last_sl = sw_lo[i]
        if not np.isnan(last_sh) and c[i] > last_sh:
            state[i] = 1
        elif not np.isnan(last_sl) and c[i] < last_sl:
            state[i] = -1
    return pd.Series(state, index=df.index).rename("choch")


# ---------------------------------------------------------------- equal highs/lows
def equal_highs_lows(swing_hi: pd.Series, swing_lo: pd.Series,
                     tol_pct: float = 0.0004) -> pd.DataFrame:
    """Liquidity pools: two confirmed swings at (nearly) the same price with
    an opposite swing between them.  tol_pct default 0.04% (the library's
    M15-H4 pip guidance in % terms).  Returns EQH/EQL levels with touch
    counts, ffill'd per bar.  O(n) scan."""
    n = len(swing_hi)
    sh = swing_hi.to_numpy(dtype=float)
    sl = swing_lo.to_numpy(dtype=float)
    out = pd.DataFrame(index=swing_hi.index, columns=[
        "eqh_level", "eqh_touches", "eql_level", "eql_touches"], dtype=float)
    cur_eqh = np.nan
    cur_eqh_n = 0
    cur_eql = np.nan
    cur_eql_n = 0
    last_sh_price = np.nan
    last_sl_price = np.nan
    low_between = False
    high_between = False
    for i in range(n):
        if not np.isnan(sh[i]):
            if (not np.isnan(last_sh_price)
                    and abs(sh[i] - last_sh_price) / last_sh_price <= tol_pct
                    and low_between):
                if not np.isnan(cur_eqh) and abs(sh[i] - cur_eqh) / cur_eqh <= tol_pct:
                    cur_eqh_n += 1
                else:
                    cur_eqh = sh[i]
                    cur_eqh_n = 2
            elif not np.isnan(cur_eqh) and abs(sh[i] - cur_eqh) / cur_eqh > tol_pct:
                cur_eqh = np.nan
                cur_eqh_n = 0
            last_sh_price = sh[i]
            low_between = False
        if not np.isnan(sl[i]):
            if (not np.isnan(last_sl_price)
                    and abs(sl[i] - last_sl_price) / last_sl_price <= tol_pct
                    and high_between):
                if not np.isnan(cur_eql) and abs(sl[i] - cur_eql) / cur_eql <= tol_pct:
                    cur_eql_n += 1
                else:
                    cur_eql = sl[i]
                    cur_eql_n = 2
            elif not np.isnan(cur_eql) and abs(sl[i] - cur_eql) / cur_eql > tol_pct:
                cur_eql = np.nan
                cur_eql_n = 0
            last_sl_price = sl[i]
            high_between = False
        if i > 0:
            if not np.isnan(sl[i - 1]):
                low_between = True
            if not np.isnan(sh[i - 1]):
                high_between = True
        if cur_eqh_n >= 2:
            out.loc[swing_hi.index[i], ["eqh_level", "eqh_touches"]] = (cur_eqh, cur_eqh_n)
        if cur_eql_n >= 2:
            out.loc[swing_hi.index[i], ["eql_level", "eql_touches"]] = (cur_eql, cur_eql_n)
    for col in ("eqh_level", "eql_level"):
        out[col] = out[col].ffill()
    return out


# ---------------------------------------------------------------- turtle soup
def turtle_soup(df: pd.DataFrame, levels: pd.Series, direction: str,
                wick_frac: float = 0.6) -> pd.Series:
    """Wick through a KNOWN liquidity level + close back inside, with the
    sweep wick >= wick_frac of the bar range (the library's sweep
    quantification).  direction: 'high' (sweep of highs -> short play) or
    'low' (sweep of lows -> long play)."""
    h, l, c = df["high"], df["low"], df["close"]
    rng = (h - l).replace(0, np.nan)
    lv = levels.reindex(df.index).ffill()
    if direction == "high":
        return ((h > lv) & (c < lv) & ((h - lv) >= wick_frac * rng)).rename(
            "turtle_soup_short")
    return ((l < lv) & (c > lv) & ((lv - l) >= wick_frac * rng)).rename(
        "turtle_soup_long")
