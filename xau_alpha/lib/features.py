"""
xau_alpha/lib/features.py
Causal price-action / ICT feature helpers on numpy arrays. Every output at index i uses only bars <= i
(a value "known at i" is usable for a decision at the close of bar i).
"""
import numpy as np
import pandas as pd


def ema(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).ewm(span=n, adjust=False).mean().values


def rolling_max_prev(x: np.ndarray, n: int) -> np.ndarray:
    """Max of the n bars BEFORE i (excludes bar i)."""
    return pd.Series(x).shift(1).rolling(n, min_periods=n).max().values


def rolling_min_prev(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).shift(1).rolling(n, min_periods=n).min().values


def pivots(h: np.ndarray, l: np.ndarray, k: int):
    """
    Fractal swing points with k bars each side. A pivot at bar p is only CONFIRMED at bar p+k.
    Returns (last_sh, last_sh_idx, last_sl, last_sl_idx) giving, at each bar i, the most recent swing high/low
    that is confirmed by bar i (NaN / -1 if none).
    """
    n = len(h)
    win = 2 * k + 1
    hmax = pd.Series(h).rolling(win, center=True).max().values
    lmin = pd.Series(l).rolling(win, center=True).min().values
    is_sh = (h == hmax)
    is_sl = (l == lmin)
    is_sh[:k] = False
    is_sh[n - k:] = False
    is_sl[:k] = False
    is_sl[n - k:] = False
    out = []
    for is_p, v in ((is_sh, h), (is_sl, l)):
        conf = np.full(n, -1, dtype=np.int64)
        p_idx = np.flatnonzero(is_p)
        conf_at = p_idx + k
        last = np.full(n, -1, dtype=np.int64)
        last[conf_at[conf_at < n]] = p_idx[conf_at < n]
        last = pd.Series(np.where(last >= 0, last, np.nan)).ffill().fillna(-1).values.astype(np.int64)
        price = np.where(last >= 0, v[np.maximum(last, 0)], np.nan)
        out += [price, last]
    return out[0], out[1], out[2], out[3]


def fvg(h: np.ndarray, l: np.ndarray):
    """
    Three-bar fair value gaps, known at the close of bar i:
      bullish: l[i] > h[i-2]  -> zone (h[i-2], l[i])
      bearish: h[i] < l[i-2]  -> zone (h[i], l[i-2])
    Returns bull (bool), bull_lo, bull_hi, bear (bool), bear_lo, bear_hi.
    """
    h2 = np.r_[np.nan, np.nan, h[:-2]]
    l2 = np.r_[np.nan, np.nan, l[:-2]]
    bull = l > h2
    bear = h < l2
    return bull, h2, l, bear, h, l2


def window_range(mod: np.ndarray, day_key: np.ndarray, h: np.ndarray, l: np.ndarray, start: int, end: int):
    """
    High/low of bars whose minute-of-day is in [start, end) within each day key, exposed only at bars that come
    AFTER an in-window bar of the same day key and are themselves outside the window (i.e. the window has
    occurred and ended in this trading day). Values are running max/min of the in-window bars seen so far, so the
    result is causal by construction.
    FIX 2026-09-30: the old version exposed the whole-day aggregate at every bar with mod >= end, so with the
    NY-17:00 trading-day key the bars at 21:00-23:59 UTC (start of the trading day) saw the upcoming Asia/London
    range (look-ahead, found by the silver_bullet agent).
    Returns (rng_hi, rng_lo) arrays (NaN where unknown).
    """
    if start < end:
        inwin = (mod >= start) & (mod < end)
    else:
        inwin = (mod >= start) | (mod < end)
    df = pd.DataFrame({"k": day_key, "h": np.where(inwin, h, -np.inf), "l": np.where(inwin, l, np.inf),
                       "w": inwin.astype(np.int8)})
    g = df.groupby("k", sort=False)
    hi_run = g["h"].cummax().values
    lo_run = g["l"].cummin().values
    seen = g["w"].cummax().values.astype(bool)
    after = seen & ~inwin
    hi = np.where(after & np.isfinite(hi_run), hi_run, np.nan)
    lo = np.where(after & np.isfinite(lo_run), lo_run, np.nan)
    return hi, lo


def day_open_price(day_key: np.ndarray, o: np.ndarray) -> np.ndarray:
    return pd.Series(o).groupby(day_key).transform("first").values


def running_day_extremes(day_key: np.ndarray, h: np.ndarray, l: np.ndarray):
    """High/low of the trading day so far, including bar i."""
    s = pd.DataFrame({"k": day_key, "h": h, "l": l})
    return s.groupby("k")["h"].cummax().values, s.groupby("k")["l"].cummin().values


def prev_day_hl(day_key: np.ndarray, h: np.ndarray, l: np.ndarray):
    """Previous trading day's high/low for each bar."""
    s = pd.DataFrame({"k": day_key, "h": h, "l": l})
    agg = s.groupby("k", sort=False).agg(h=("h", "max"), l=("l", "min"))
    prev = agg.shift(1)
    return prev.loc[day_key, "h"].values, prev.loc[day_key, "l"].values


# ---------------------------------------------------------------------------------------------------------------
# Owner-style building blocks (SYNTHESIS.md section 6 "shared definitions")

def engulf(o: np.ndarray, c: np.ndarray):
    """(bull, bear) engulfing at bar i vs bar i-1, bodies compared; known at the close of i."""
    po, pc = np.r_[np.nan, o[:-1]], np.r_[np.nan, c[:-1]]
    body, pbody = np.abs(c - o), np.abs(pc - po)
    bull = (c > o) & (pc < po) & (c >= po) & (o <= pc) & (body > pbody)
    bear = (c < o) & (pc > po) & (c <= po) & (o >= pc) & (body > pbody)
    return bull, bear


def pin(o, h, l, c, w_pin=0.5):
    """(bull, bear) pin bars: lower (upper) wick >= w_pin x range."""
    rng = np.maximum(h - l, 1e-9)
    bull = (np.minimum(o, c) - l) >= w_pin * rng
    bear = (h - np.maximum(o, c)) >= w_pin * rng
    return bull, bear


def zone_timeline(h, l, A, L=240, k=3, w=0.5, K=3, pad=0.1):
    """
    Causal multi-touch S/R zones from confirmed pivots (highs AND lows together).
    A pivot at bar p is confirmed at p+k and stays in the pool while p > i-L. At every bar where the pool changes,
    pivots are sorted and greedily clustered (a cluster is a maximal run with max-min <= w*A[i]); clusters with
    >= K members become zones [min - pad*A, max + pad*A].
    Returns (chg, zsets): chg is the sorted array of bar indices at whose close the zone set changes, and
    zsets[j] is an (n, 3) array of (lo, hi, touches) valid from chg[j] until chg[j+1]-1.
    Use zones_at(chg, zsets, i) to get the zones known at the close of bar i.
    """
    n = len(h)
    win = 2 * k + 1
    hmax = pd.Series(h).rolling(win, center=True).max().values
    lmin = pd.Series(l).rolling(win, center=True).min().values
    piv = []
    for p in np.flatnonzero(h == hmax):
        if k <= p < n - k:
            piv.append((p, h[p]))
    for p in np.flatnonzero(l == lmin):
        if k <= p < n - k:
            piv.append((p, l[p]))
    piv.sort()
    pidx = np.array([p for p, _ in piv], dtype=np.int64)
    pval = np.array([v for _, v in piv], dtype=np.float64)
    add_at = pidx + k                     # enters the pool at the close of this bar
    drop_at = pidx + L                    # leaves when p <= i - L, i.e. at bar p + L
    events = np.unique(np.r_[add_at[add_at < n], drop_at[drop_at < n]])
    chg, zsets = [], []
    empty = np.zeros((0, 3))
    for i in events:
        lo_p = np.searchsorted(pidx, i - L, side="right")        # p > i - L
        hi_p = np.searchsorted(add_at, i, side="right")           # confirmed by i
        vals = np.sort(pval[lo_p:hi_p])
        if len(vals) < K or not np.isfinite(A[i]):
            chg.append(i)
            zsets.append(empty)
            continue
        tol = w * A[i]
        zs = []
        start = 0
        for j in range(1, len(vals) + 1):
            if j == len(vals) or vals[j] - vals[start] > tol:
                cnt = j - start
                if cnt >= K:
                    zs.append((vals[start] - pad * A[i], vals[j - 1] + pad * A[i], cnt))
                start = j
        chg.append(i)
        zsets.append(np.array(zs) if zs else empty)
    return np.array(chg, dtype=np.int64), zsets


def zones_at(chg, zsets, i):
    j = np.searchsorted(chg, i, side="right") - 1
    return zsets[j] if j >= 0 else np.zeros((0, 3))
