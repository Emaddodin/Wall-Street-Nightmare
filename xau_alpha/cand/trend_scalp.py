"""
cand/trend_scalp.py - "Gold Mastery Fx" style micro-scalp + Mr P Fx confirmation, back-to-back.
  bias   : HTF trend = sign of (HTF close - EMA(ema)) AND EMA slope over the last 3 HTF bars agree (htf = 60 or 240 min)
  setup  : M1 pullback against the bias: price touched/closed beyond EMA(pb_ema) of M1 within the last `pb_win` bars
  trigger: first M1 candle closing back in the bias direction with body >= `body` x ATR(M1) and beyond the prior bar's
           extreme (for sells: close < previous low)
  exits  : fixed stop `sl` $ and fixed take-profit `tp` $ (video: "exit once I see profits", SL 25-30 pips = $2.5-3),
           time stop `tmax` minutes. Back-to-back: a new signal is allowed as soon as the previous trade is closed.
"""
import numpy as np
import pandas as pd
from data import atr, resample_causal
from features import ema

GRID = {}
WARMUP_BARS = 4000


def signals(m1, htf=60, ema_n=50, pb_ema=20, pb_win=10, body=0.3, hours=(0, 24)):
    H, known = resample_causal(m1, htf)
    he = ema(H["c"].values, ema_n)
    up = (H["c"].values > he) & (he > np.r_[np.nan, np.nan, np.nan, he[:-3]])
    dn = (H["c"].values < he) & (he < np.r_[np.nan, np.nan, np.nan, he[:-3]])
    bias = np.where(known >= 0, np.where(up[np.maximum(known, 0)], 1, np.where(dn[np.maximum(known, 0)], -1, 0)), 0)
    o, h, l, c = (m1[x].values for x in "ohlc")
    e = ema(c, pb_ema)
    A = atr(m1)
    hr = m1["hour"].values
    # pullback seen in the last pb_win bars: for longs a low at/below EMA, for shorts a high at/above EMA
    touch_dn = pd.Series(l <= e).rolling(pb_win).max().values > 0
    touch_up = pd.Series(h >= e).rolling(pb_win).max().values > 0
    pl, ph = np.r_[np.nan, l[:-1]], np.r_[np.nan, h[:-1]]
    body_ok = np.abs(c - o) >= body * A
    long_sig = (bias == 1) & touch_dn & (c > o) & (c > ph) & body_ok
    short_sig = (bias == -1) & touch_up & (c < o) & (c < pl) & body_ok
    ok_h = (hr >= hours[0]) & (hr < hours[1])
    idx = np.flatnonzero((long_sig | short_sig) & ok_h)
    return idx, np.where(long_sig[idx], 1, -1)


def orders(m1, sl=3.0, tp=2.0, tmax=60, **kw):
    idx, d = signals(m1, **kw)
    ts = m1["ts"].values
    return [{"t": int(ts[i]) + 60_000, "d": int(dd), "kind": "mkt", "sl_dist": sl, "tp_dist": tp,
             "tmax": tmax * 60_000, "tag": "tscalp"} for i, dd in zip(idx, d)]
