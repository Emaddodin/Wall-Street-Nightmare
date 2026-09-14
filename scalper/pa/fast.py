"""Numpy-first fast paths for the PA detectors -- used by the engine's
per-symbol preparation at real-data scale (190k 1m bars x 30 symbols).

Same rules as the pandas API in candles.py / motive.py / ict.py; the tests
pin the two implementations to agree on random data.
"""
from __future__ import annotations

import numpy as np

np.seterr(divide="ignore", invalid="ignore")


def _wick_parts(o, h, l, c):
    rng = h - l
    body = np.abs(c - o)
    up = h - np.maximum(c, o)
    lo = np.minimum(c, o) - l
    return rng, body, up, lo


def patterns_np(o, h, l, c, names: list[str]) -> dict[str, np.ndarray]:
    """Boolean array per requested pattern name (motivewave exact rules).
    names must be keys of motive.BULLISH/BEARISH/NEUTRAL_PATTERNS."""
    n = len(o)
    rng, body, up, lo = _wick_parts(o, h, l, c)
    out: dict[str, np.ndarray] = {}

    def S(cond):
        return np.where(np.isnan(cond), False, cond).astype(bool)

    def shift(a, k):
        if k <= 0:
            return a
        r = np.empty_like(a)
        r[:k] = np.nan
        r[k:] = a[:-k]
        return r

    oc = c - o
    bull = oc > 0
    bear = oc < 0
    prev_bull, prev_bear = shift(bull, 1), shift(bear, 1)
    prev_o, prev_c = shift(o, 1), shift(c, 1)
    prev_h, prev_l = shift(h, 1), shift(l, 1)
    prev_body = shift(body, 1)
    prev_rng = shift(rng, 1)

    for name in names:
        cond = np.zeros(n, dtype=bool)
        if name == "doji":
            cond = S(body / rng < 0.1)
        elif name == "long_legged_doji":
            cond = S((body / rng < 0.1) & (up > 2 * body) & (lo > 2 * body))
        elif name == "dragonfly_doji":
            cond = S((body / rng < 0.1) & (lo > 0.6 * rng) & (up < 0.1 * rng))
        elif name == "gravestone_doji":
            cond = S((body / rng < 0.1) & (up > 0.6 * rng) & (lo < 0.1 * rng))
        elif name == "spinning_top":
            r = body / rng
            cond = S((r > 0.1) & (r < 0.3) & (up > body) & (lo > body))
        elif name == "hammer":
            cond = S(bull & (lo > 2 * body) & (up < 0.5 * body))
        elif name == "inverted_hammer":
            cond = S(bull & (up > 2 * body) & (lo < 0.5 * body))
        elif name == "shooting_star":
            cond = S(bear & (up > 2 * body) & (lo < 0.5 * body))
        elif name == "hanging_man":
            cond = S(bear & (lo > 2 * body) & (up < 0.5 * body))
        elif name == "bullish_marubozu":
            cond = S(bull & (body / rng > 0.95))
        elif name == "bearish_marubozu":
            cond = S(bear & (body / rng > 0.95))
        elif name == "bullish_engulfing":
            cond = S(prev_bear & bull & (o <= prev_c) & (c >= prev_o))
        elif name == "bearish_engulfing":
            cond = S(prev_bull & bear & (o >= prev_c) & (c <= prev_o))
        elif name == "bullish_harami":
            cond = S(prev_bear & bull & (body < 0.5 * prev_body)
                     & (o > prev_c) & (c < prev_o))
        elif name == "bearish_harami":
            cond = S(prev_bull & bear & (body < 0.5 * prev_body)
                     & (o < prev_c) & (c > prev_o))
        elif name == "piercing_line":
            mid = (prev_o + prev_c) / 2
            cond = S(prev_bear & bull & (o < prev_c) & (c > mid) & (c < prev_o))
        elif name == "dark_cloud_cover":
            mid = (prev_o + prev_c) / 2
            cond = S(prev_bull & bear & (o > prev_c) & (c < mid) & (c > prev_o))
        elif name == "tweezer_bottom":
            avg = _roll_mean(rng, 14, mp=1, shifted=True)
            cond = S((np.abs(l - prev_l) / avg < 0.05) & (bull != prev_bull))
        elif name == "tweezer_top":
            avg = _roll_mean(rng, 14, mp=1, shifted=True)
            cond = S((np.abs(h - prev_h) / avg < 0.05) & (bull != prev_bull))
        elif name == "bullish_kicker":
            cond = S(prev_bear & bull & (o > prev_o))
        elif name == "bearish_kicker":
            cond = S(prev_bull & bear & (o < prev_o))
        elif name in ("morning_star", "evening_star",
                      "morning_doji_star", "evening_doji_star",
                      "bullish_abandoned_baby", "bearish_abandoned_baby",
                      "three_white_soldiers", "three_black_crows",
                      "three_inside_up", "three_inside_down",
                      "three_outside_up", "three_outside_down"):
            o2, c2 = shift(o, 2), shift(c, 2)
            bull2, bear2 = shift(bull, 2), shift(bear, 2)
            body2 = shift(body, 2)
            h2, l2 = shift(h, 2), shift(l, 2)
            o1, c1 = prev_o, prev_c
            if name == "morning_star":
                mid1 = np.maximum(o1, c1)
                cond = S(bear2 & bull & (prev_body < 0.3 * body2)
                         & (mid1 < c2) & (body > 0.5 * body2))
            elif name == "evening_star":
                min1 = np.minimum(o1, c1)
                cond = S(bull2 & bear & (prev_body < 0.3 * body2)
                         & (min1 > c2) & (body > 0.5 * body2))
            elif name == "morning_doji_star":
                cond = S(bear2 & bull & (prev_body / prev_rng < 0.1)
                         & (prev_h < c2))
            elif name == "evening_doji_star":
                cond = S(bull2 & bear & (prev_body / prev_rng < 0.1)
                         & (prev_l > c2))
            elif name == "bullish_abandoned_baby":
                cond = S(bear2 & bull & (prev_body / prev_rng < 0.1)
                         & (prev_h < l2) & (prev_h < l))
            elif name == "bearish_abandoned_baby":
                cond = S(bull2 & bear & (prev_body / prev_rng < 0.1)
                         & (prev_l > h2) & (prev_l > h))
            elif name == "three_white_soldiers":
                cond = S(bull2 & prev_bull & bull & (c1 > c2) & (c > c1))
            elif name == "three_black_crows":
                cond = S(bear2 & prev_bear & bear & (c1 < c2) & (c < c1))
            elif name == "three_inside_up":
                cond = S(bear2 & prev_bull & bull & (o1 > c2) & (c1 < o2) & (c > o2))
            elif name == "three_inside_down":
                cond = S(bull2 & prev_bear & bear & (o1 < c2) & (c1 > o2) & (c < o2))
            elif name == "three_outside_up":
                cond = S(bear2 & prev_bull & bull & (o1 <= c2) & (c1 >= o2)
                         & (c > c1))
            elif name == "three_outside_down":
                cond = S(bull2 & prev_bear & bear & (o1 >= c2) & (c1 <= o2)
                         & (c < c1))
        out[name] = cond
    return out


def _roll_mean(a: np.ndarray, w: int, mp: int = 1,
               shifted: bool = False) -> np.ndarray:
    """Rolling mean over window w with min_periods mp; shifted=True excludes
    the current element (pandas .shift(1).rolling semantics)."""
    n = len(a)
    a = np.where(np.isnan(a), 0.0, a)
    valid_a = ~np.isnan(np.where(np.isnan(a), np.nan, 0.0)) | (np.abs(a) >= 0)
    cs = np.cumsum(a)
    cnt = np.cumsum(valid_a).astype(float)
    out = np.full(n, np.nan)
    j = np.arange(n) - (1 if shifted else 0)
    ok = j >= 0
    if not ok.any():
        return out
    jj = np.clip(j, 0, n - 1)
    i0 = np.maximum(0, j - w + 1)
    s = cs[jj] - np.where(i0 > 0, cs[np.clip(i0 - 1, 0, n - 1)], 0.0)
    k = cnt[jj] - np.where(i0 > 0, cnt[np.clip(i0 - 1, 0, n - 1)], 0.0)
    good = ok & (k >= mp)
    out[good] = s[good] / k[good]
    return out


def fvg_state_np(h: np.ndarray, l: np.ndarray, max_age: int = 30):
    """(bull_fvg, bull_ce, bull_hi, bull_lo, bear_fvg, bear_ce, bear_hi,
    bear_lo) per bar -- numpy O(n), identical rules to ict.fvg_state."""
    n = len(h)
    bull_fvg = np.zeros(n, dtype=bool)
    bear_fvg = np.zeros(n, dtype=bool)
    bull_ce = np.full(n, np.nan)
    bull_hi = np.full(n, np.nan)
    bull_lo = np.full(n, np.nan)
    bear_ce = np.full(n, np.nan)
    bear_hi = np.full(n, np.nan)
    bear_lo = np.full(n, np.nan)
    cb = None   # (created, hi, lo, ce)
    cs = None
    for i in range(n):
        if i >= 2:
            if l[i] > h[i - 2]:
                gap_hi, gap_lo = h[i - 2], l[i]
                cb = (i, gap_hi, gap_lo, (gap_hi + gap_lo) / 2.0)
            if h[i] < l[i - 2]:
                gap_hi, gap_lo = h[i], l[i - 2]
                cs = (i, gap_hi, gap_lo, (gap_hi + gap_lo) / 2.0)
        if cb is not None:
            if l[i] <= cb[3] or i - cb[0] > max_age:
                cb = None
        if cs is not None:
            if h[i] >= cs[3] or i - cs[0] > max_age:
                cs = None
        if cb is not None:
            bull_fvg[i] = True
            bull_hi[i], bull_lo[i], bull_ce[i] = cb[1], cb[2], cb[3]
        if cs is not None:
            bear_fvg[i] = True
            bear_hi[i], bear_lo[i], bear_ce[i] = cs[1], cs[2], cs[3]
    return bull_fvg, bull_ce, bull_hi, bull_lo, bear_fvg, bear_ce, bear_hi, bear_lo


def displacement_np(o, h, l, c, body_mult=1.5, body_range=0.70,
                    max_opp_wick=0.20, avg_window=20):
    """(disp_up, disp_dn) -- ICT displacement, numpy."""
    body = np.abs(c - o)
    rng = h - l
    up_w = h - np.maximum(c, o)
    dn_w = np.minimum(c, o) - l
    avg_body = _roll_mean(body, avg_window, mp=avg_window // 2, shifted=True)
    base = (body >= body_mult * avg_body) & (body / rng >= body_range)
    disp_up = base & (c > o) & (up_w <= max_opp_wick * rng)
    disp_dn = base & (c < o) & (dn_w <= max_opp_wick * rng)
    return disp_up, disp_dn


def eq_levels_np(sw_hi: np.ndarray, sw_lo: np.ndarray,
                 tol_pct: float = 0.0004):
    """(eqh, eqh_touches, eql, eql_touches) ffill'd levels -- numpy O(n),
    identical rules to ict.equal_highs_lows."""
    n = len(sw_hi)
    eqh = np.full(n, np.nan)
    eql = np.full(n, np.nan)
    eqh_n = np.zeros(n)
    eql_n = np.zeros(n)
    cur_h = cur_l = np.nan
    cur_hn = cur_ln = 0
    last_h = last_l = np.nan
    low_between = high_between = False
    for i in range(n):
        sh, sl = sw_hi[i], sw_lo[i]
        if not np.isnan(sh):
            if (not np.isnan(last_h) and abs(sh - last_h) / last_h <= tol_pct
                    and low_between):
                if not np.isnan(cur_h) and abs(sh - cur_h) / cur_h <= tol_pct:
                    cur_hn += 1
                else:
                    cur_h, cur_hn = sh, 2
            elif not np.isnan(cur_h) and abs(sh - cur_h) / cur_h > tol_pct:
                cur_h, cur_hn = np.nan, 0
            last_h = sh
            low_between = False
        if not np.isnan(sl):
            if (not np.isnan(last_l) and abs(sl - last_l) / last_l <= tol_pct
                    and high_between):
                if not np.isnan(cur_l) and abs(sl - cur_l) / cur_l <= tol_pct:
                    cur_ln += 1
                else:
                    cur_l, cur_ln = sl, 2
            elif not np.isnan(cur_l) and abs(sl - cur_l) / cur_l > tol_pct:
                cur_l, cur_ln = np.nan, 0
            last_l = sl
            high_between = False
        if i > 0:
            if not np.isnan(sw_lo[i - 1]):
                low_between = True
            if not np.isnan(sw_hi[i - 1]):
                high_between = True
        if cur_hn >= 2:
            eqh[i], eqh_n[i] = cur_h, cur_hn
        if cur_ln >= 2:
            eql[i], eql_n[i] = cur_l, cur_ln
    # ffill
    for arr in (eqh, eql):
        last = np.nan
        for i in range(n):
            if not np.isnan(arr[i]):
                last = arr[i]
            elif not np.isnan(last):
                arr[i] = last
    return eqh, eqh_n, eql, eql_n


def turtle_soup_np(h, l, c, level: np.ndarray, direction: str,
                   wick_frac: float = 0.6) -> np.ndarray:
    """Wick through a known level + close back inside, wick >= frac of range."""
    rng = h - l
    if direction == "high":
        return (h > level) & (c < level) & ((h - level) >= wick_frac * rng)
    return (l < level) & (c > level) & ((level - l) >= wick_frac * rng)


def order_blocks_np(o, h, l, c, swing_hi, swing_lo, max_gap=3, max_age=60,
                    body_mult=1.5, body_range=0.70, max_opp_wick=0.20,
                    avg_window=20):
    """(bull_ob, bull_mt, bull_lo, bull_hi, bear_ob, bear_mt, bear_lo,
    bear_hi) per bar -- numpy O(n).  Same rules as ict.order_blocks: the
    last opposite-color candle before a displacement candle that breaks the
    recent swing; zone = the block candle's body; MT = (O+C)/2; mitigated
    when traded through MT, stale after max_age bars."""
    n = len(o)
    disp_up, disp_dn = displacement_np(o, h, l, c, body_mult, body_range,
                                       max_opp_wick, avg_window)
    bull_ob = np.zeros(n, dtype=bool)
    bull_mt = np.full(n, np.nan)
    bull_lo = np.full(n, np.nan)
    bull_hi = np.full(n, np.nan)
    bear_ob = np.zeros(n, dtype=bool)
    bear_mt = np.full(n, np.nan)
    bear_lo = np.full(n, np.nan)
    bear_hi = np.full(n, np.nan)
    cur_bull = None      # (mt, lo, hi, created_at)
    cur_bear = None
    last_sh = np.nan
    last_sl = np.nan
    for i in range(n):
        if not np.isnan(swing_hi[i]):
            last_sh = swing_hi[i]
        if not np.isnan(swing_lo[i]):
            last_sl = swing_lo[i]
        if disp_up[i] and not np.isnan(last_sh) and c[i] > last_sh:
            k = i - 1
            gap = 0
            while k >= 0 and gap <= max_gap and not (c[k] < o[k]):
                k -= 1
                gap += 1
            if k >= 0 and gap <= max_gap and c[k] < o[k]:
                ob_lo, ob_hi = min(o[k], c[k]), max(o[k], c[k])
                cur_bull = ((ob_lo + ob_hi) / 2.0, ob_lo, ob_hi, i)
        if disp_dn[i] and not np.isnan(last_sl) and c[i] < last_sl:
            k = i - 1
            gap = 0
            while k >= 0 and gap <= max_gap and not (c[k] > o[k]):
                k -= 1
                gap += 1
            if k >= 0 and gap <= max_gap and c[k] > o[k]:
                ob_lo, ob_hi = min(o[k], c[k]), max(o[k], c[k])
                cur_bear = ((ob_lo + ob_hi) / 2.0, ob_lo, ob_hi, i)
        if cur_bull is not None:
            if l[i] <= cur_bull[0] or i - cur_bull[3] > max_age:
                cur_bull = None
        if cur_bear is not None:
            if h[i] >= cur_bear[0] or i - cur_bear[3] > max_age:
                cur_bear = None
        if cur_bull is not None:
            bull_ob[i] = True
            bull_mt[i], bull_lo[i], bull_hi[i] = cur_bull[0], cur_bull[1], cur_bull[2]
        if cur_bear is not None:
            bear_ob[i] = True
            bear_mt[i], bear_lo[i], bear_hi[i] = cur_bear[0], cur_bear[1], cur_bear[2]
    return (bull_ob, bull_mt, bull_lo, bull_hi,
            bear_ob, bear_mt, bear_lo, bear_hi)
