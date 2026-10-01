"""
cand/zr.py - zone-rejection entries reverse-engineered from the "Gold Mastery Fx" video (2P0aCxxFYCc), whose 7 trades
were located on Dukascopy data (June 17-23 2025, UTC):
  T1 SELL 3386.04 06-17 ~08:30  T2 SELL 3396.6 06-18 13:30  T3 SELL 3374.0 06-19 ~04:26  T4 SELL 3354.5 06-20 09:53
  T5 BUY 3353.1 06-23 ~07:15     T6 BUY 3363.8 06-23 ~09:35  T7 BUY 3360.0 06-23 ~12:40
Sells were all with the H1+H4 downtrend; T5/T6 buys were counter-trend at a demand zone (double bottom), T7 with H1 up.

Rules:
  zones  : confirmed M15 swing highs AND lows (pivot k) from the last `look_h` hours, clustered when within w*A15 of each
           other; a zone = [min - pad*A15, max + pad*A15] with `touches` = cluster size. Broken support therefore also acts as
           resistance (break & retest) and vice versa.
  touch  : price comes INTO a zone: for a SELL, an M5 high reaches zone_lo while the M5 close `app` bars earlier was below
           zone_lo (approach from below); BUY mirrored.
  trigger: within `kt` M5 bars of the touch, an M5 candle closes in the reaction direction beyond the previous candle's
           extreme (SELL: close < open and close < previous low) and still within `near` x A15 of the zone.
  bias   : mode 'trend' -> only with the H1 trend (close vs EMA50 and its slope)
           mode 'trend_or_strong' -> also counter-trend when the zone has >= `strong` touches
           mode 'any' -> both directions at any zone
  session: entries between h0 and h1 UTC.
"""
import numpy as np
import pandas as pd

from data import atr, resample_causal
from features import ema

GRID = {}
WARMUP_BARS = 4000


def _zones(prices, w):
    v = np.sort(prices)
    out, s = [], 0
    for j in range(1, len(v) + 1):
        if j == len(v) or v[j] - v[s] > w:
            out.append((v[s], v[j - 1], j - s))
            s = j
    return out


def signals(m1, mode="trend_or_strong", k=2, look_h=48, w=0.6, pad=0.15, app=6, kt=6, near=1.0, strong=3,
            h0=2, h1=17, min_touch=2):
    H1, k1 = resample_causal(m1, 60)
    M15, k15 = resample_causal(m1, 15)
    M5, k5 = resample_causal(m1, 5)
    A15 = atr(M15).astype(float)
    e1 = ema(H1["c"].values, 50)
    c1 = H1["c"].values
    up1 = (c1 > e1) & (e1 > np.r_[np.full(3, np.nan), e1[:-3]])
    dn1 = (c1 < e1) & (e1 < np.r_[np.full(3, np.nan), e1[:-3]])
    hi15, lo15 = M15["h"].values, M15["l"].values
    n15 = len(M15)
    hmax = pd.Series(hi15).rolling(2 * k + 1, center=True).max().values
    lmin = pd.Series(lo15).rolling(2 * k + 1, center=True).min().values
    ph = np.array([p for p in np.flatnonzero(hi15 == hmax) if k <= p < n15 - k], dtype=np.int64)
    pl = np.array([p for p in np.flatnonzero(lo15 == lmin) if k <= p < n15 - k], dtype=np.int64)
    piv_idx = np.r_[ph, pl]
    piv_val = np.r_[hi15[ph], lo15[pl]]
    order = np.argsort(piv_idx)
    piv_idx, piv_val = piv_idx[order], piv_val[order]
    look = int(look_h * 4)
    o5, h5, l5, c5 = (M5[x].values for x in ("o", "h", "l", "c"))
    ki5 = np.searchsorted(k5, np.arange(len(M5)), side="left")         # M1 index where M5 bar r is known
    hour = m1["hour"].values
    out = []
    last_q, Z = -1, []
    cool_until = -1
    for r in range(app + 2, len(M5)):
        i = ki5[r]
        if i >= len(m1):
            break
        q = int(k15[i])                                                 # last complete M15 bar at this M5 close
        if q < 0 or not np.isfinite(A15[q]):
            continue
        a = A15[q]
        if q != last_q:
            last_q = q
            sel = (piv_idx + k <= q) & (piv_idx > q - look)
            Z = [z for z in _zones(piv_val[sel], w * a) if z[2] >= min_touch]
        if not Z or r <= cool_until or not (h0 <= hour[i] < h1):
            continue
        j1 = int(k1[i])
        trend = 1 if (j1 >= 3 and up1[j1]) else (-1 if (j1 >= 3 and dn1[j1]) else 0)
        for zlo, zhi, nt in Z:
            lo_z, hi_z = zlo - pad * a, zhi + pad * a
            for d in (-1, 1):
                if mode == "trend" and trend != d:
                    continue
                if mode == "trend_or_strong" and trend != d and nt < strong:
                    continue
                # touch within the last kt bars, approached from the correct side
                ok = False
                for t in range(max(app + 1, r - kt), r):
                    if d < 0 and h5[t] >= lo_z and c5[t - app] < lo_z:
                        ok = True
                        break
                    if d > 0 and l5[t] <= hi_z and c5[t - app] > hi_z:
                        ok = True
                        break
                if not ok:
                    continue
                if d < 0 and c5[r] < o5[r] and c5[r] < l5[r - 1] and c5[r] >= lo_z - near * a:
                    out.append({"i": int(i), "d": -1, "zlo": float(lo_z), "zhi": float(hi_z), "touches": int(nt),
                                "stop": float(max(h5[max(0, r - kt):r + 1].max(), hi_z) + 0.2 * a)})
                    cool_until = r + 3
                    break
                if d > 0 and c5[r] > o5[r] and c5[r] > h5[r - 1] and c5[r] <= hi_z + near * a:
                    out.append({"i": int(i), "d": 1, "zlo": float(lo_z), "zhi": float(hi_z), "touches": int(nt),
                                "stop": float(min(l5[max(0, r - kt):r + 1].min(), lo_z) - 0.2 * a)})
                    cool_until = r + 3
                    break
            if r <= cool_until:
                break
    return out


def orders(m1, sl=4.0, tp=8.0, tmax=150, stop_mode="fixed", **kw):
    sig = signals(m1, **kw)
    ts = m1["ts"].values
    c = m1["c"].values
    out = []
    for s in sig:
        o = {"t": int(ts[s["i"]]) + 60_000, "d": s["d"], "kind": "mkt", "tmax": tmax * 60_000, "tag": "zr"}
        if stop_mode == "struct":
            dist = min(max(abs(c[s["i"]] - s["stop"]), 1.0), sl)
            o.update(sl_dist=dist, tp_dist=dist * tp / sl)
        else:
            o.update(sl_dist=sl, tp_dist=tp)
        out.append(o)
    return out
