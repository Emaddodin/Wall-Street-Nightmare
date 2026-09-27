"""
cand/trend_pullback.py - Hypothesis X2(b): higher-timeframe trend state + M5 pullback + M1 trigger, both directions.

Idea (OPINION; classic "trade the pullback in the direction of the higher-timeframe trend"): enter with the HTF
trend after a counter-trend pullback to the M5 EMA20 or into an M5 fair value gap, on an M1 reversal trigger, with
the stop behind the pullback swing. The stop is set by the local pullback, so it is much smaller than an HTF-ATR
stop (this is what could make the idea flip-compatible). Main-strategy candidate for equity >= $100; the
flip-eligible subset (stop in [1.2, 4.0] $/oz) is reported separately.

Rules (longs described; shorts are the exact mirror with identical parameters, no directional veto):
  TREND     trend='ema'  : last complete H1 bar has C > EMA50 > EMA200 (EMA on H1 closes)          -> +1
            trend='don4' : H4 Donchian state: +1 after the last H4 close above the prior `don_n`-bar high,
                           -1 after the last H4 close below the prior `don_n`-bar low (persists until flipped)
  ZONE      zone='ema'   : pullback level = EMA20 of the last complete M5 bar; touch if M1 low <= EMA20 + tol*A5
            zone='fvg'   : the most recent bullish M5 FVG (features.fvg definition on M5 bars: L[j] > H[j-2],
                           gap >= 0.1*A5), alive for `fvg_age` M5 bars and killed by an M5 close below its bottom;
                           touch if M1 low <= gap top
            zone='any'   : either
  ARM       first touch at M1 bar i while TREND = +1, with no touch in the previous `gap` M1 bars (a fresh
            pullback after price was away from the level)
  TRIGGER   within M1 bars j in [i, i + W], the first bar where TREND is still +1, the entry gates pass, and
            trig='eng'   : bullish engulfing (features.engulf)
            trig='swing' : M1 close above the highest high of the previous 3 M1 bars (break of the minor swing)
  ENTRY     market order at the close of j (t = ts[j] + 60 s)
  STOP      absolute: min(L[i-3 .. j]) - sigma*A[j] (behind the pullback swing); widened to C[j] - smin*A[j] if
            closer than that; skipped if the distance exceeds smax*A5 (smax=0: no cap)
  EXIT      ex='rX'  : target at C[j] + X * (C[j] - stop)
            ex='tr'  : no target; trail trm*A5 behind the best price once the trade is +1R ('trX': trm = X)
            and always: time stop `tmax` minutes, flat at 16:45 ET of the trading day, flat before the holdout.
  SESSION   sess='day' : entries only 07:00-16:29 London local time
            sess='all' : any time the gates allow, but no entries 15:30-17:00 ET
  GATES     htf_base.gate (20:30-23:30 UTC, Friday after 19:00 UTC, Sunday reopen, [-5, +15] min HIGH news)

A = Wilder ATR14 of M1 mid, A5 = Wilder ATR14 of causal M5 bars (last complete bar). All thresholds scale-free.
"""
import numpy as np

from features import ema, engulf, rolling_max_prev, rolling_min_prev
from htf_base import LAST_FLAT_MS, base, cache, htf, to_m1

# Stage-1 grid: trend(2) x zone(2) x trig(2) x W(2) x ex(3) x sess(2) = 96 configs.
GRID = {
    "trend": ["ema", "don4"],
    "zone": ["ema", "fvg"],
    "trig": ["eng", "swing"],
    "W": [10, 30],
    "ex": ["r2", "r3", "tr"],
    "sess": ["day", "all"],
}


def _trend(b, trend, don_n):
    def mk():
        if trend == "ema":
            H = htf(b, 60)
            e50, e200 = ema(H["c"], 50), ema(H["c"], 200)
            st = np.where((H["c"] > e50) & (e50 > e200), 1, np.where((H["c"] < e50) & (e50 < e200), -1, 0))
            st[:200] = 0
            return to_m1(H["km"], st.astype(np.float64))
        H = htf(b, 240)
        hh = rolling_max_prev(H["h"], don_n)
        ll = rolling_min_prev(H["l"], don_n)
        st = np.zeros(H["nk"])
        s = 0
        for k in range(H["nk"]):
            if H["c"][k] > hh[k]:
                s = 1
            elif H["c"][k] < ll[k]:
                s = -1
            st[k] = s
        return to_m1(H["km"], st)
    return cache(("trend", trend, don_n), mk)


def _m5(b, fvg_age):
    def mk():
        H = htf(b, 5)
        e20 = to_m1(H["km"], ema(H["c"], 20))
        a5 = to_m1(H["km"], H["atr"])
        h5, l5, c5, A5 = H["h"], H["l"], H["c"], H["atr"]
        nk = H["nk"]
        bu_lo, bu_hi = np.full(nk, np.nan), np.full(nk, np.nan)
        be_lo, be_hi = np.full(nk, np.nan), np.full(nk, np.nan)
        ub = db = -10 ** 9
        ulo = uhi = dlo = dhi = np.nan
        for j in range(2, nk):
            if l5[j] > h5[j - 2] and l5[j] - h5[j - 2] >= 0.1 * A5[j]:       # new bullish gap
                ulo, uhi, ub = h5[j - 2], l5[j], j
            if h5[j] < l5[j - 2] and l5[j - 2] - h5[j] >= 0.1 * A5[j]:       # new bearish gap
                dlo, dhi, db = h5[j], l5[j - 2], j
            if ub > -1 and (c5[j] < ulo or j - ub > fvg_age):
                ub = -10 ** 9
            if db > -1 and (c5[j] > dhi or j - db > fvg_age):
                db = -10 ** 9
            if ub > -1:
                bu_lo[j], bu_hi[j] = ulo, uhi
            if db > -1:
                be_lo[j], be_hi[j] = dlo, dhi
        return dict(e20=e20, a5=a5, bu_hi=to_m1(H["km"], bu_hi), be_lo=to_m1(H["km"], be_lo))
    return cache(("m5", fvg_age), mk)


def _m1feat(b):
    def mk():
        bull, bear = engulf(b["o"], b["c"])
        return dict(eng_b=bull, eng_s=bear, hi3=rolling_max_prev(b["h"], 3), lo3=rolling_min_prev(b["l"], 3))
    return cache(("m1feat",), mk)


def _fresh(touch: np.ndarray, gap: int) -> np.ndarray:
    prev = np.convolve(touch.astype(np.int64), np.ones(gap, dtype=np.int64), mode="full")[:len(touch)]
    prev = np.r_[0, prev[:-1]]              # touches in (i-gap, i-1]
    return touch & (prev == 0)


def orders(m1, trend="ema", zone="ema", trig="eng", W=10, ex="r2", sess="day", sigma=0.3, smin=1.0, smax=0.0,
           tol=0.1, gap=15, lb=3, trm=2.0, tmax=240, don_n=20, fvg_age=24, tag="tpb"):
    b = base(m1)
    n = b["n"]
    ts, o, h, l, c, A = b["ts"], b["o"], b["h"], b["l"], b["c"], b["A"]
    st = _trend(b, trend, int(don_n))
    f5 = _m5(b, int(fvg_age))
    f1 = _m1feat(b)
    e20, a5 = f5["e20"], f5["a5"]
    ok = b["gate"].copy()
    if sess == "day":
        ok &= (b["lon"] >= 7 * 60) & (b["lon"] < 16 * 60 + 30)
    else:
        ok &= ~((b["ny"] >= 15 * 60 + 30) & (b["ny"] < 17 * 60))
    with np.errstate(invalid="ignore"):
        t_ema_b = l <= e20 + tol * a5
        t_ema_s = h >= e20 - tol * a5
        t_fvg_b = l <= f5["bu_hi"]
        t_fvg_s = h >= f5["be_lo"]
    if zone == "ema":
        tb, tsd = t_ema_b, t_ema_s
    elif zone == "fvg":
        tb, tsd = t_fvg_b, t_fvg_s
    else:
        tb, tsd = t_ema_b | t_fvg_b, t_ema_s | t_fvg_s
    out = []
    for d, touch, trg in ((1, tb, f1["eng_b"] if trig == "eng" else (c > f1["hi3"])),
                          (-1, tsd, f1["eng_s"] if trig == "eng" else (c < f1["lo3"]))):
        arm = _fresh(touch & (st == d), gap)
        good = trg & (st == d) & ok & np.isfinite(A) & np.isfinite(a5)
        for i in np.flatnonzero(arm):
            if i < lb:
                continue
            w = good[i:i + W + 1]
            hit = np.flatnonzero(w)
            if not len(hit):
                continue
            j = i + hit[0]
            if j >= n:
                continue
            if d > 0:
                stop = l[i - lb:j + 1].min() - sigma * A[j]
                stop = min(stop, c[j] - smin * A[j])
            else:
                stop = h[i - lb:j + 1].max() + sigma * A[j]
                stop = max(stop, c[j] + smin * A[j])
            risk = abs(c[j] - stop)
            if smax > 0 and risk > smax * a5[j]:
                continue
            t = int(ts[j]) + 60_000
            od = {"t": t, "d": d, "kind": "mkt", "sl": float(stop), "tmax": int(tmax) * 60_000,
                  "flat": int(min(b["eod_ms"][j], LAST_FLAT_MS)), "tag": f"{tag}_{'L' if d > 0 else 'S'}"}
            if ex.startswith("r"):
                od["tp"] = float(c[j] + d * float(ex[1:]) * risk)
            else:                                   # 'tr' uses trm; 'trX' sets the trail multiple to X
                od["trail"] = float((float(ex[2:]) if len(ex) > 2 else trm) * a5[j])
                od["trail_act"] = float(risk)
            out.append(od)
    out.sort(key=lambda x: x["t"])
    return out
