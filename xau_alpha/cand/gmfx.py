"""
cand/gmfx.py - "Gold Mastery Fx" break-and-retest (video 2P0aCxxFYCc, trades located on Dukascopy June 17-23 2025).

What the video shows (frames + audio):
  bias   : H4 + H1 trend ("gold is down-trending on H4 ... H1 bearish break and retest ... trend is your friend")
  level  : an M15 level that price BROKE in the trend direction (demand broken -> becomes supply, or resistance broken
           -> becomes support)
  entry  : price pulls back to RETEST the broken level; enter "just after seeing price start to go downward with these
           red candles" = first M5 candle that closes back in the trend direction from the retest
  stop   : 25-60 pips = $2.5-6 ; target: next zone, realised +$6..11/oz ; hold 30 min - 2 h ; London/NY hours

Rules (sell side; buys mirror on the negated series):
  trend  : H1 close < EMA(50) and EMA falling over 3 bars; if `h4` also H4 close < EMA(20) and falling
  break  : a confirmed M15 swing low (pivot k=3) is broken by an M15 close < level - 0.1*A15  (A15 = ATR14 on M15)
  retest : within `win` M15 bars after the break, an M5 high reaches level - tol*A15 (touch from below); a M15 close
           above level + 0.5*A15 cancels the setup
  trigger: first M5 candle after the touch with close < open and close < previous M5 low, close still within
           1.0*A15 below the level -> SELL at that M5 close
  exits  : stop `sl` $ above the entry, target `tp` $ below (fixed), time stop `tmax` min
  session: entries between `h0` and `h1` UTC
"""
import numpy as np
import pandas as pd

from data import atr, resample_causal
from features import ema

GRID = {}
WARMUP_BARS = 4000


def _pivot_lows(L, k=3):
    n = len(L)
    lmin = pd.Series(L).rolling(2 * k + 1, center=True).min().values
    return np.array([p for p in np.flatnonzero(L == lmin) if k <= p < n - k], dtype=np.int64)


def signals(m1, h4=True, tol=0.3, win=32, h0=2, h1=17, debug=False):
    out = []
    H1, k1 = resample_causal(m1, 60)
    H4, k4 = resample_causal(m1, 240)
    M15, k15 = resample_causal(m1, 15)
    M5, k5 = resample_causal(m1, 5)
    ki15 = np.searchsorted(k15, np.arange(len(M15)), side="left")       # M1 index where each M15 bar is known
    ki5 = np.searchsorted(k5, np.arange(len(M5)), side="left")
    A15 = atr(M15).astype(float)
    hour = m1["hour"].values
    for d in (-1, 1):                                                  # -1 sell, +1 buy (long space = -price)
        s = -1.0 if d < 0 else 1.0
        # in "sell space" we work on price for sells; for buys negate and swap high/low
        def side(df):
            if d < 0:
                return df["o"].values, df["h"].values, df["l"].values, df["c"].values
            return -df["o"].values, -df["l"].values, -df["h"].values, -df["c"].values
        o15, h15, l15, c15 = side(M15)
        o5, h5, l5, c5 = side(M5)
        # trend (sell space: price below a falling EMA)
        c1 = H1["c"].values * (1 if d < 0 else -1)
        e1 = ema(c1, 50)
        tr1 = (c1 < e1) & (e1 < np.r_[np.full(3, np.nan), e1[:-3]])
        c4 = H4["c"].values * (1 if d < 0 else -1)
        e4 = ema(c4, 20)
        tr4 = (c4 < e4) & (e4 < np.r_[np.full(3, np.nan), e4[:-3]])
        trend1 = np.where(k1 >= 0, tr1[np.maximum(k1, 0)], False)
        trend4 = np.where(k4 >= 0, tr4[np.maximum(k4, 0)], False)
        trend = trend1 & (trend4 if h4 else True)
        piv = _pivot_lows(l15, 3)
        conf_at = piv + 3                                              # pivot known at the close of M15 bar p+3
        used = set()
        j5 = 0
        for q in range(len(M15)):
            a = A15[q]
            if not np.isfinite(a) or a <= 0:
                continue
            # most recent confirmed swing low known by bar q-1 that this bar breaks
            cand = piv[(conf_at <= q - 1) & (piv >= q - 96)]
            if not len(cand):
                continue
            p = cand[-1]
            if p in used:
                continue
            lvl = l15[p]
            if not (c15[q] < lvl - 0.1 * a and c15[q - 1] >= lvl - 0.1 * a):
                continue
            used.add(p)
            i_known = ki15[q]
            if i_known >= len(m1) or not trend[i_known]:
                continue
            # retest on M5 within `win` M15 bars
            t_end = M15["ts_open"].values[min(q + win, len(M15) - 1)]
            k5a = int(np.searchsorted(M5["ts_open"].values, M15["ts_open"].values[q] + 15 * 60_000))
            touched = False
            for r in range(k5a, len(M5)):
                if M5["ts_open"].values[r] > t_end:
                    break
                # last COMPLETE M15 bar at the close of this M5 bar (no peeking at the forming M15 candle)
                q15 = int(k15[min(ki5[r], len(m1) - 1)])
                if q15 > q and c15[q15] > lvl + 0.5 * a:
                    break                                              # reclaimed: setup dead
                if not touched:
                    if h5[r] >= lvl - tol * a:
                        touched = True
                    continue
                if c5[r] < o5[r] and c5[r] < l5[r - 1] and c5[r] >= lvl - 1.0 * a:
                    i = ki5[r]
                    if i < len(m1) and h0 <= hour[i] < h1:
                        out.append({"i": int(i), "d": int(d), "level": float(lvl * (1 if d < 0 else -1))})
                    break
    return sorted(out, key=lambda x: x["i"])


def orders(m1, sl=4.0, tp=8.0, tmax=150, **kw):
    sig = signals(m1, **kw)
    ts = m1["ts"].values
    return [{"t": int(ts[s["i"]]) + 60_000, "d": s["d"], "kind": "mkt", "sl_dist": sl, "tp_dist": tp,
             "tmax": tmax * 60_000, "tag": "gmfx"} for s in sig]
