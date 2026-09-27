"""
cand/sweep_reclaim.py - Hypothesis H1 (recon/SYNTHESIS.md section 6): sweep and reclaim of session extremes.

Idea: stops cluster just beyond obvious levels (Asia range, prior trading day, London range). When price runs
those stops and then closes back inside, fade the run toward the inside of the range.

Levels (all causal; level value known before the first bar that may sweep it):
  sess, LON window  : Asia high/low    = features.window_range(mod, tday, h, l, 0, 420)  (00:00-06:59 UTC), live from 07:00 UTC
  sess, NY window   : London high/low  = features.window_range(mod, tday, h, l, 420, 720) (07:00-11:59 UTC), live from 12:00 UTC
  pd, both windows  : prior trading-day high/low = features.prev_day_hl(tday, h, l), live from the start of the trading day
Windows (the SWEEP bar must be inside): LON = 08:00-11:30 London local (lon_mod), NY = 08:30-11:30 ET (ny_mod), or both.

Rule, short side (long side is the exact mirror image; identical parameters, no directional veto):
  1. Sweep   : the first bar i (inside the window) with h[i] >= L + delta*A5[i]. The level must not have been swept
               (h >= L + delta*A5) at any bar since it became live today; a sweep before the window kills it for the day.
  2. Reclaim : the first M1 close c[j] <= L - eps*A[j] with i <= j <= i + N (same trading day).
  3. Trigger : trig=0 none; trig=1 TRIG_BEAR(j, L) = bearish engulfing at j OR bearish pin (upper wick >= 0.5 range,
               h[j] >= L, c[j] < L). If the reclaim bar is not a trigger bar the level is dropped for the day.
  4. Entry   : market order at the close of j (t = ts[j] + 60 s; filled on the next 10-s bar at bid/ask + cost).
  5. Stop    : max(h[i..j]) + sigma*A[j] (absolute price).
  6. Target  : tp='mid' -> midpoint of the reference range (Asia / London / prior day); tp=1.5 or 2.5 -> k x R from c[j].
  7. Time    : tmax minutes; also flat at 16:45 ET (never binds with tmax <= 90 inside these windows).
  News       : no entry whose decision time falls in the HIGH-impact blackout `news` (default '30' = [T-30, T+30],
               the SYNTHESIS N2 rule for equity < $21). 'off' disables.
A = Wilder ATR14 of M1 mid (data.atr); A5 = Wilder ATR14 of causal M5 bars (data.resample_causal), value of the last
COMPLETE M5 bar at the close of the M1 bar.

Null-test switches (same code path):
  mode='cont'       continuation twin: at the first close beyond L + delta*A5 within N bars of the sweep, enter IN the
                    sweep direction; stop = the opposite extreme since the sweep -/+ sigma*A; target k x R, or for
                    tp='mid' the same distance as |L - mid| projected in the trade direction.
  placebo_seed=int  placebo levels: every level (and its range midpoint) of a trading day is shifted by
                    s * U(0.3, 0.7) * AsiaWidth(day), s = +-1, one draw per day.
  flip=True         keep only trades whose estimated stop distance |c[j] - stop| is within [1.2, 4.0] $/oz.

Stage 2 of the grid uses `s1` = index into STAGE1_TOP (the top-5 stage-1 configs by TRAIN t-stat) so that one
sweep.py call can cover five 24-point sub-grids.
"""
import numpy as np
import pandas as pd

from data import atr, news_block_mask, resample_causal
from features import engulf, prev_day_hl, window_range

# ----------------------------------------------------------------------------------------------------------------
# Stage 1 (SYNTHESIS H1): level set x delta x N x trigger x window = 3*3*3*2*3 = 162, sigma 0.3, TP 1.5R, tmax 60.
GRID = {
    "levels": ["sess", "pd", "both"],
    "delta": [0.25, 0.5, 1.0],
    "N": [3, 10, 20],
    "trig": [0, 1],
    "win": ["lon", "ny", "both"],
}

# Filled in after stage 1 (results/sweep_reclaim_s1_train.csv). Selection rule, fixed before stage 2 ran: no stage-1
# config had TRAIN n >= 150 with PF > 1, and the raw top-5 by t-stat had n = 2..36, so s1 = 0..4 are the top 5 by
# TRAIN t-stat among configs with TRAIN n >= 60 (the harness's own min_n); s1 = 5 is the only config with
# TRAIN n >= 150 (the pass-bar n), added so that at least one stage-2 family can meet the n requirement.
STAGE1_TOP = [
    {"levels": "sess", "delta": 0.5, "N": 10, "trig": 0, "win": "both"},   # tr n 76  PF 0.988 t +0.72
    {"levels": "both", "delta": 0.5, "N": 3, "trig": 0, "win": "both"},    # tr n 62  PF 0.867 t +0.50
    {"levels": "both", "delta": 0.5, "N": 20, "trig": 0, "win": "lon"},    # tr n 67  PF 0.886 t +0.37
    {"levels": "both", "delta": 0.5, "N": 10, "trig": 0, "win": "both"},   # tr n 98  PF 0.833 t +0.31
    {"levels": "sess", "delta": 0.5, "N": 20, "trig": 0, "win": "both"},   # tr n 93  PF 0.910 t +0.25
    {"levels": "both", "delta": 0.25, "N": 20, "trig": 0, "win": "both"},  # tr n 152 PF 0.685 t -1.78
]

# Stage 2 (SYNTHESIS H1): per stage-1 config, eps {0, 0.2} x sigma {0.2, 0.5} x TP {mid, 1.5R, 2.5R} x tmax {30, 90}
# = 24 each, 6 x 24 = 144.
GRID2 = {
    "s1": [0, 1, 2, 3, 4, 5],
    "eps": [0.0, 0.2],
    "sigma": [0.2, 0.5],
    "tp": ["mid", 1.5, 2.5],
    "tmax": [30, 90],
}

LON_WIN = (480, 690)     # 08:00-11:30 London local
NY_WIN = (510, 690)      # 08:30-11:30 New York
FLAT_NY = 16 * 60 + 45   # flat by 16:45 ET
NEWS = {"off": None, "30": (30, 30), "2_5": (2, 5), "5_15": (5, 15), "15_30": (15, 30)}
FLIP_STOP = (1.2, 4.0)

_CACHE: dict = {}


def _base(m1: pd.DataFrame) -> dict:
    """Parameter-free causal features, computed once per process."""
    key = (len(m1), int(m1["ts"].values[0]), int(m1["ts"].values[-1]))
    if key in _CACHE:
        return _CACHE[key]
    _CACHE.clear()
    o, h, l, c = (m1[k].values.astype(np.float64) for k in "ohlc")
    ts = m1["ts"].values.astype(np.int64)
    mod, lon_mod, ny_mod = m1["mod"].values, m1["lon_mod"].values, m1["ny_mod"].values
    tday = m1["tday"].values
    A = atr(m1)
    htf, known = resample_causal(m1, 5)
    a5 = atr(htf)
    A5 = np.where(known >= 0, a5[np.maximum(known, 0)], np.nan)
    asia_hi, asia_lo = window_range(mod, tday, h, l, 0, 420)
    lon_hi, lon_lo = window_range(mod, tday, h, l, 420, 720)
    pdh, pdl = prev_day_hl(tday, h, l)
    bull_eng, bear_eng = engulf(o, c)
    rng = np.maximum(h - l, 1e-9)
    # pin geometry (the level condition is applied per signal)
    up_wick = (h - np.maximum(o, c)) >= 0.5 * rng
    dn_wick = (np.minimum(o, c) - l) >= 0.5 * rng
    # trading-day segments (tday is monotonic in time)
    codes, uniq = pd.factorize(tday)
    starts = np.flatnonzero(np.r_[True, codes[1:] != codes[:-1]])
    ends = np.r_[starts[1:], len(tday)]
    days = []
    for s, e in zip(starts, ends):
        lm, nm, um = lon_mod[s:e], ny_mod[s:e], mod[s:e]
        lw = s + np.flatnonzero((lm >= LON_WIN[0]) & (lm < LON_WIN[1]))
        nw = s + np.flatnonzero((nm >= NY_WIN[0]) & (nm < NY_WIN[1]))
        a_live = s + np.flatnonzero(um >= 420)
        l_live = s + np.flatnonzero(um >= 720)
        days.append({
            "s": s, "e": e,
            "lon": (lw[0], lw[-1]) if len(lw) else None,
            "ny": (nw[0], nw[-1]) if len(nw) else None,
            "asia_live": a_live[0] if len(a_live) else None,
            "lon_live": l_live[0] if len(l_live) else None,
        })
    # flat time at 16:45 ET for each bar (ms), on the same NY clock day
    flat = ts + (FLAT_NY - ny_mod.astype(np.int64)) * 60_000
    out = dict(o=o, h=h, l=l, c=c, ts=ts, A=A, A5=A5, asia_hi=asia_hi, asia_lo=asia_lo, lon_hi=lon_hi,
               lon_lo=lon_lo, pdh=pdh, pdl=pdl, bull_eng=bull_eng, bear_eng=bear_eng, up_wick=up_wick,
               dn_wick=dn_wick, days=days, flat=flat, news={})
    _CACHE[key] = out
    return out


def _news_mask(b: dict, news: str):
    if NEWS.get(news) is None:
        return None
    if news not in b["news"]:
        bef, aft = NEWS[news]
        b["news"][news] = news_block_mask(b["ts"], bef, aft, impacts=("HIGH",))
    return b["news"][news]


def _levels_for_day(b: dict, dd: dict, win: str, levels: str):
    """List of (name, hi, lo, live_from, win_start, win_end) reference ranges for one day and one window."""
    w = dd[win]
    if w is None:
        return []
    ws, we = w
    out = []
    if levels in ("sess", "both"):
        if win == "lon":
            hi, lo, live = b["asia_hi"][ws], b["asia_lo"][ws], dd["asia_live"]
            nm = "asia"
        else:
            hi, lo, live = b["lon_hi"][ws], b["lon_lo"][ws], dd["lon_live"]
            nm = "london"
        if live is not None and np.isfinite(hi) and np.isfinite(lo) and live <= ws:
            out.append((nm, hi, lo, live, ws, we))
    if levels in ("pd", "both"):
        hi, lo = b["pdh"][dd["s"]], b["pdl"][dd["s"]]
        if np.isfinite(hi) and np.isfinite(lo):
            out.append(("pd", hi, lo, dd["s"], ws, we))
    return out


def orders(m1, levels="both", delta=0.5, N=10, trig=0, win="both", eps=0.0, sigma=0.3, tp=1.5, tmax=60,
           news="30", mode="fade", placebo_seed=None, flip=False, s1=None):
    if s1 is not None:                       # stage 2: take the stage-1 structural params from STAGE1_TOP
        base = dict(STAGE1_TOP[int(s1)])
        levels, delta, N, trig, win = base["levels"], base["delta"], base["N"], base["trig"], base["win"]
    b = _base(m1)
    h, l, c, ts, A, A5 = b["h"], b["l"], b["c"], b["ts"], b["A"], b["A5"]
    blocked = _news_mask(b, news)
    wins = ["lon", "ny"] if win == "both" else [win]
    rng = np.random.default_rng(10_000 + int(placebo_seed)) if placebo_seed is not None else None
    tp_k = None if tp == "mid" else float(tp)
    out = []
    for dd in b["days"]:
        off = 0.0
        if rng is not None:
            ws0 = dd["asia_live"]
            u, sgn = rng.uniform(0.3, 0.7), (1 if rng.random() < 0.5 else -1)   # always drawn: stable per day
            if ws0 is None or not np.isfinite(b["asia_hi"][ws0]):
                continue
            off = sgn * u * (b["asia_hi"][ws0] - b["asia_lo"][ws0])
        e = dd["e"]
        for wn in wins:
            for nm, rhi, rlo, live, ws, we in _levels_for_day(b, dd, wn, levels):
                rhi, rlo = rhi + off, rlo + off
                mid = 0.5 * (rhi + rlo)
                for u, L in ((1, rhi), (-1, rlo)):          # u=+1: level above (sweep up), u=-1: level below
                    ext = h if u > 0 else l
                    seg = slice(live, we + 1)
                    thr = L + u * delta * A5[seg]
                    hit = np.flatnonzero(u * (ext[seg] - thr) >= 0)
                    if not len(hit):
                        continue
                    i = live + hit[0]
                    if i < ws:                              # swept before the window: level used up today
                        continue
                    jmax = min(i + N, e - 1)
                    js = np.arange(i, jmax + 1)
                    if mode == "fade":
                        ok = u * (c[js] - (L - u * eps * A[js])) <= 0
                    else:                                   # continuation twin: close beyond L + delta*A5
                        ok = u * (c[js] - (L + u * delta * A5[js])) >= 0
                    k = np.flatnonzero(ok)
                    if not len(k):
                        continue
                    j = int(js[k[0]])
                    d = -u if mode == "fade" else u
                    if trig:
                        if mode == "fade":
                            if d < 0:
                                t_ok = b["bear_eng"][j] or (b["up_wick"][j] and h[j] >= L and c[j] < L)
                            else:
                                t_ok = b["bull_eng"][j] or (b["dn_wick"][j] and l[j] <= L and c[j] > L)
                        else:
                            t_ok = b["bull_eng"][j] if d > 0 else b["bear_eng"][j]
                        if not t_ok:
                            continue
                    if blocked is not None and blocked[j]:
                        continue
                    if not np.isfinite(A[j]):
                        continue
                    if d < 0:
                        sl = h[i:j + 1].max() + sigma * A[j]
                    else:
                        sl = l[i:j + 1].min() - sigma * A[j]
                    risk = (sl - c[j]) * (-d)
                    if not risk > 0:
                        continue
                    if flip and not (FLIP_STOP[0] <= risk <= FLIP_STOP[1]):
                        continue
                    if tp_k is None:
                        if mode == "fade":
                            tpx = mid
                            if (tpx - c[j]) * d <= 0:           # midpoint must lie on the profit side
                                continue
                        else:
                            tpx = c[j] + d * abs(L - mid)
                    else:
                        tpx = c[j] + d * tp_k * risk
                    out.append({"t": int(ts[j]) + 60_000, "d": int(d), "kind": "mkt", "sl": float(sl),
                                "tp": float(tpx), "tmax": int(tmax) * 60_000, "flat": int(b["flat"][j]),
                                "tag": f"{nm}{'H' if u > 0 else 'L'}-{wn}"})
    out.sort(key=lambda q: q["t"])
    return out
