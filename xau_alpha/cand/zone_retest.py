"""
cand/zone_retest.py - Hypothesis H2 (SYNTHESIS.md section 6): the owner's core setup A on M1.

    multi-touch S/R zone  ->  break  ->  retest (touch 1 or touch 2)  ->  engulfing / pin confirmation
    ->  market entry at the next bar  ->  stop beyond the retest swing  ->  target at the nearest swing level

Rules (long side; the short side is the exact mirror, implemented by running the same code on the negated
price series: o' = -o, h' = -l, l' = -h, c' = -c, so a bullish engulf/pin there is a bearish one here):

  ZONES     features.zone_timeline(h, l, A, L, k, w, K): confirmed k-fractal pivots (highs and lows) with pivot
            index in (i-L, i], greedy clusters of width <= w*A, >= K members, padded by 0.1*A.
            The zones used for a break at bar i are the ones known at the close of bar i-1.
  BREAK     at bar i0: c[i0] > z_hi + beta*A[i0]
            and the break leg c[i0] - min(l[i0-10..i0]) >= lam*A[i0]
            and the zone was not broken in the previous `fresh` bars (spec: 60):
                max(c[i0-fresh..i0-1]) <= z_hi + beta*A[i0].
            If several zones break on one bar, the highest one (the one just crossed) is used.
            The zone bounds are frozen at the break.
  RETEST    touch = a bar i in (i0, i0+R] with l[i] <= z_hi + tau*A[i]. Any M1 close < z_lo in between
            (touch bar and trigger bars included) invalidates the setup.
            touch=1: the first touch.  touch=2: a later touch after price has risen to >= z_hi + 0.5*A on a bar
            strictly after touch 1 (state machine of video_mindset.md section (b)).
  TRIGGER   trig=1: the first bar j in [touch, touch+2] with BULL_ENGULF(j) or BULL_PIN(j, z_hi)
                    (lower wick >= w_pin*range, l[j] <= z_hi, c[j] > z_hi). None within 2 bars: no trade.
            trig=0 (control arm): j = touch bar, entry at its close with no candle condition.
  ENTRY     market order at ts[j] + 60 s (sim fills at the next 10 s ask). Bar j must lie inside the London-local
            session and outside the HIGH-impact news blackout [T-news, T+news] minutes.
  STOP      min(l[touch..j]) - sigma*A[j]   (absolute price)
  TARGET    tp_mode='near': the nearest confirmed swing high (pivots of the last 240 bars) at least
            tp_r x stop distance above c[j]; if none lies within 4 x stop distance, 2R.
            tp_mode='r': c[j] + tp_r x stop distance.
  MANAGE    be=1: stop to entry + 0.45 once the trade is +1R (0.45 $/oz = cost buffer, a broker constraint).
            tmax minutes time stop.  One position at a time (sim).
  PLACEBO   placebo=s shifts every zone by s*w*A before the break test (null test for "zones mean something").

All thresholds are in ATR units (A = data.atr(m1), M1 Wilder ATR14). TEST bars (>= 2026-06-01) are cut before any
computation, so this module can never produce or look at a holdout signal.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from data import TEST, _ms, atr, news_block_mask  # noqa: E402
import features as F  # noqa: E402

TEST_MS = _ms(TEST[0])

# Stage 1 grid (SYNTHESIS H2): k=3, L=240, sigma=0.3, TP = nearest level (rho=1), tmax=30, video window.
GRID = {
    "w": [0.5, 1.0],
    "K": [3, 6],
    "beta": [0.1, 0.3],
    "lam": [1.5, 3.0],
    "R": [15, 45, 90],
    "touch": [1, 2],
    "trig": [1, 0],
}

DEFAULTS = dict(w=0.5, K=3, beta=0.1, lam=1.5, R=45, touch=1, trig=1, L=240, k=3, tau=0.1, w_pin=0.6,
                sigma=0.3, tp_mode="near", tp_r=1.0, tmax=30, be=0, sess="0612", news=30, placebo=0.0,
                fresh=60)

NEAR_WIN = 240          # NEAREST_LEVEL look-back (bars)
NEAR_MAX_R = 4.0        # beyond this many stop distances the nearest level is ignored and 2R is used
NEAR_FALLBACK_R = 2.0
BE_OFF = 0.45           # $/oz, break-even offset (covers the ~0.42 LiteFinance round trip)
LEG_BARS = 10           # break leg measured over l[i0-10..i0]

_C: dict = {}


# ------------------------------------------------------------------------------------------------ caches
def _base(m1):
    """Per-process cache of arrays cut at the TEST start, in long space for d=+1 and d=-1."""
    ts_all = m1["ts"].values
    key = (len(m1), int(ts_all[0]), int(ts_all[-1]))
    if _C.get("key") == key:
        return _C["base"]
    _C.clear()
    n = int(np.searchsorted(ts_all, TEST_MS, side="left"))
    df = m1.iloc[:n]
    o, h, l, c = (df[x].values.astype(np.float64) for x in "ohlc")
    A = atr(df)
    b = {"n": n, "ts": ts_all[:n].astype(np.int64), "lon_mod": df["lon_mod"].values.astype(np.int64),
         "A": A, "h": h, "l": l, "news": {}, "piv": {}, "sp": {}}
    for d in (1, -1):
        if d > 0:
            O, H, Lo, Cl = o, h, l, c
        else:
            O, H, Lo, Cl = -o, -l, -h, -c
        eng_bull, _ = F.engulf(O, Cl)
        b["sp"][d] = {
            "o": O, "h": H, "l": Lo, "c": Cl, "eng": np.nan_to_num(eng_bull).astype(bool),
            "rmaxc": {},                                                  # fresh window -> max close i-f..i-1
            "lmin": pd.Series(Lo).rolling(LEG_BARS + 1, min_periods=LEG_BARS + 1).min().values,  # l[i-10..i]
        }
    _C["key"] = key
    _C["base"] = b
    return b


def _news(b, win):
    if win not in b["news"]:
        b["news"][win] = news_block_mask(b["ts"], win, win) if win > 0 else np.zeros(b["n"], dtype=bool)
    return b["news"][win]


def _pivots(b, k):
    """All confirmed fractal pivots (same definition as features.pivots / zone_timeline), in long space."""
    if k not in b["piv"]:
        h, l = b["h"], b["l"]
        n = len(h)
        win = 2 * k + 1
        hmax = pd.Series(h).rolling(win, center=True).max().values
        lmin = pd.Series(l).rolling(win, center=True).min().values
        ok = np.zeros(n, dtype=bool)
        ok[k:n - k] = True
        sh = np.flatnonzero((h == hmax) & ok)
        sl = np.flatnonzero((l == lmin) & ok)
        b["piv"][k] = {1: (sh, h[sh]), -1: (sl, -l[sl])}     # long-space "highs" for each direction
    return b["piv"][k]


def _zones(b, L, k, w, K):
    """features.zone_timeline flattened to arrays: chg, off (len+1), Z (m,3) = lo, hi, touches."""
    key = ("z", L, k, w, K)
    if key not in _C:
        chg, zsets = F.zone_timeline(b["h"], b["l"], b["A"], L=L, k=k, w=w, K=K)
        cnt = np.array([len(z) for z in zsets], dtype=np.int64)
        off = np.r_[0, np.cumsum(cnt)].astype(np.int64)
        Z = np.vstack([z for z in zsets if len(z)]) if cnt.sum() else np.zeros((0, 3))
        # keep at most 4 zone timelines per process (memory)
        zkeys = [x for x in _C if isinstance(x, tuple) and x[0] == "z"]
        if len(zkeys) >= 4:
            _C.pop(zkeys[0])
        _C[key] = (chg, off, Z)
    return _C[key]


def _sess(sess):
    s = f"{int(sess):04d}"          # "0612" = 06:00-12:00 London local (also accepts 612 after a CSV round trip)
    return int(s[:2]) * 60, int(s[2:4]) * 60


# ------------------------------------------------------------------------------------------------ detector
def setups(m1, **params):
    """All entry signals as a DataFrame (one row per signal, before the one-position-at-a-time filter)."""
    p = {**DEFAULTS, **params}
    b = _base(m1)
    n, A, lon, ts = b["n"], b["A"], b["lon_mod"], b["ts"]
    s0, s1 = _sess(p["sess"])
    R = int(p["R"])
    fresh = int(p["fresh"])
    blocked = _news(b, int(p["news"]))
    chg, off, Z = _zones(b, int(p["L"]), int(p["k"]), float(p["w"]), int(p["K"]))
    piv = _pivots(b, int(p["k"]))

    # candidate break bars: bars from R minutes before the session start up to its end (London local)
    S = np.flatnonzero((lon >= s0 - R - 3) & (lon < s1))
    S = S[(S > max(fresh, LEG_BARS) + 1) & (S < n - 1)]
    jz = np.searchsorted(chg, S - 1, side="right") - 1        # zone set known at the close of bar i-1
    S, jz = S[jz >= 0], jz[jz >= 0]
    cnt = off[jz + 1] - off[jz]
    tot = int(cnt.sum())
    rows = []
    if tot == 0:
        return pd.DataFrame(rows)
    bar = np.repeat(S, cnt)
    first = np.repeat(off[jz], cnt)
    zi = first + (np.arange(tot) - np.repeat(np.cumsum(cnt) - cnt, cnt))
    zlo, zhi, ztc = Z[zi, 0].copy(), Z[zi, 1].copy(), Z[zi, 2]
    if p["placebo"]:
        sh = float(p["placebo"]) * float(p["w"]) * A[bar]
        zlo += sh
        zhi += sh

    beta, lam, tau, sigma = float(p["beta"]), float(p["lam"]), float(p["tau"]), float(p["sigma"])
    touch_req, trig = int(p["touch"]), int(p["trig"])
    w_pin = float(p["w_pin"])
    k = int(p["k"])
    for d in (1, -1):
        sp = b["sp"][d]
        o, h, l, c, eng = sp["o"], sp["h"], sp["l"], sp["c"], sp["eng"]
        lo_, hi_ = (zlo, zhi) if d > 0 else (-zhi, -zlo)
        Ab = A[bar]
        lvl = hi_ + beta * Ab
        if fresh not in sp["rmaxc"]:
            sp["rmaxc"][fresh] = F.rolling_max_prev(c, fresh)
        ok = (c[bar] > lvl) & (sp["rmaxc"][fresh][bar] <= lvl) & (c[bar] - sp["lmin"][bar] >= lam * Ab)
        if not ok.any():
            continue
        bi, blo, bhi, btc = bar[ok], lo_[ok], hi_[ok], ztc[ok]
        order = np.lexsort((-bhi, bi))                        # per bar, highest zone first
        bi, blo, bhi, btc = bi[order], blo[order], bhi[order], btc[order]
        keep = np.r_[True, bi[1:] != bi[:-1]]
        ph_idx, ph_val = piv[d]
        for i0, zl, zh, ztouch in zip(bi[keep], blo[keep], bhi[keep], btc[keep]):
            end = min(i0 + R, n - 1)
            touches, t_bar, away = 0, -1, False
            i = i0 + 1
            while i <= end:
                if c[i] < zl:                                  # close back through the far side: break failed
                    break
                if touches == 0 or away:
                    if l[i] <= zh + tau * A[i]:
                        touches += 1
                        if touches == touch_req:
                            t_bar = i
                            break
                        away = False
                elif h[i] >= zh + 0.5 * A[i]:
                    away = True
                i += 1
            if t_bar < 0:
                continue
            j = -1
            if trig:
                for jj in range(t_bar, min(t_bar + 3, n - 1)):
                    if c[jj] < zl:
                        break
                    rng = max(h[jj] - l[jj], 1e-9)
                    pin_ok = (min(o[jj], c[jj]) - l[jj] >= w_pin * rng) and l[jj] <= zh and c[jj] > zh
                    if eng[jj] or pin_ok:
                        j = jj
                        break
            else:
                j = t_bar
            if j < 0 or not (s0 <= lon[j] < s1) or blocked[j]:
                continue
            stop = l[t_bar:j + 1].min() - sigma * A[j]
            sd = c[j] - stop
            if not sd > 0:
                continue
            if p["tp_mode"] == "near":
                a0 = np.searchsorted(ph_idx, j - NEAR_WIN, side="right")
                a1 = np.searchsorted(ph_idx, j - k, side="right")          # pivot confirmed by j
                v = ph_val[a0:a1]
                v = v[v >= c[j] + float(p["tp_r"]) * sd]
                tpx = v.min() if len(v) and v.min() <= c[j] + NEAR_MAX_R * sd else c[j] + NEAR_FALLBACK_R * sd
                tp_src = "lvl" if len(v) and v.min() <= c[j] + NEAR_MAX_R * sd else "2R"
            else:
                tpx = c[j] + float(p["tp_r"]) * sd
                tp_src = "R"
            z_lo, z_hi = (zl, zh) if d > 0 else (-zh, -zl)          # back to price space
            rows.append((int(ts[j]) + 60_000, d, int(i0), int(t_bar), int(j), z_lo, z_hi, int(ztouch),
                         stop * d, tpx * d, sd, (tpx - c[j]) / sd, c[j] * d, tp_src))
    cols = ["t", "d", "i0", "i_touch", "j", "z_lo", "z_hi", "z_k", "sl", "tp", "sd", "tp_R", "c", "tp_src"]
    df = pd.DataFrame(rows, columns=cols)
    if len(df):
        df = df.sort_values(["t", "d"], kind="stable").reset_index(drop=True)
        # one decision per bar: drop bars where both directions fire (no directional tie-break), dedupe the rest
        both = df.groupby("t")["d"].transform("nunique") > 1
        df = df[~both].drop_duplicates("t", keep="first").reset_index(drop=True)
    return df


def orders(m1, **params):
    p = {**DEFAULTS, **params}
    df = setups(m1, **p)
    out = []
    for r in df.itertuples(index=False):
        o = {"t": int(r.t), "d": int(r.d), "kind": "mkt", "sl": float(r.sl), "tp": float(r.tp),
             "tmax": int(p["tmax"]) * 60_000, "tag": f"zr{'L' if r.d > 0 else 'S'}t{p['touch']}{r.tp_src}"}
        if int(p["be"]):
            o["be"] = float(r.sd)
            o["be_off"] = BE_OFF
        out.append(o)
    return out
