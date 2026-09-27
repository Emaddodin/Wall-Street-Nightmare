"""
cand/silver_bullet.py - Hypothesis H11 (recon/SYNTHESIS.md section 6): ICT Silver Bullet, a fair value gap after a
liquidity sweep, traded with a limit order at the gap.

Idea (OPINION, ICT lore; no systematic test found, SOURCED web_research section 2.12): in fixed New York hours, price
runs resting stops beyond an obvious high/low (the sweep), then reverses with a displacement candle that leaves a
3-bar fair value gap (FVG); the retrace into the gap is bought/sold with the stop beyond the sweep extreme.

Rule, long side (after a SELL-side sweep; the short side is the exact mirror with identical parameters):
  Liquidity levels (all causal; a level is "live" from the bar after it is known and is swept at most once):
    sess : prior trading-day low (features.prev_day_hl, live from the start of the trading day), Asia low
           (00:00-06:59 UTC, live 07:00-20:00 UTC), London low (07:00-11:59 UTC, live 12:00-20:00 UTC).
    eq   : equal lows = two confirmed M1 pivot lows (fractal k=3, confirmed 3 bars later) more than k and at most
           Lb=240 bars apart, within tol_eq=0.1 * A5 of each other, with no low between them below the lower one;
           level = the lower one, live from the confirmation of the second pivot for up to Lb bars.
    liq  : 'sess', 'eq' or 'both'.
  1. Sweep: the first bar s after the level is live with l[s] < level - delta*A5[s].
  2. Displacement + FVG at bar i (known at the close of i): features.fvg bull (l[i] > h[i-2]); the middle bar i-1 is
     bullish with body >= disp * mean(body of the 20 bars before it) and body/range >= 0.7; gap = l[i] - h[i-2] >= g*A[i].
     The most recent sell-side sweep s must satisfy i-60 <= s <= i-1, and c[i] must be back above the swept level
     (reclaim). The FVG bar i must lie in the window (New York local hour `win`) and pass the gates below.
     Only the FIRST qualifying setup of each window-day is taken (either direction).
  3. Entry: buy LIMIT at px = gap_hi - e*(gap_hi - gap_lo) (e=0.5: the midpoint; the placebo depths are 0.25 / 0.75),
     placed at ts[i] + 60 s, cancelled after 25 min if not filled.
  4. Stop: min(l[s..i]) - 0.2*A[i] (the sweep extreme; `sb_stop` in A units).
  5. Target: tp='2R' -> px + 2*(px - stop); tp='opp' -> the running trading-day high at bar i (the opposite session
     extreme), skipped if that is less than 1R away.
  6. Time stop tmax minutes after the fill; flat by 16:45 ET.
Windows (`win`, New York local): '3' = 03:00-04:00, '10' = 10:00-11:00 (primary), '14' = 14:00-15:00, 'sb' = all
three; any other hour string (e.g. '12') is a PLACEBO window.
Gates: no entries 20:30-23:30 UTC, Friday after 19:00 UTC, Sunday (UTC) bars or 16:25-18:00 ET; HIGH-impact calendar
blackout `news` (default '30' = [T-30, T+30], the SYNTHESIS N2 rule for equity < $21) at the decision bar.
A = Wilder ATR14 of M1 mid (data.atr); A5 = Wilder ATR14 on causal M5 bars (data.resample_causal).

ENGINE NOTE: features.window_range(mod, tday, ...) with the NY-17:00 trading-day key exposes a day's Asia/London
range at bars with mod >= end, which includes 22:00-23:59 UTC bars that BELONG to that trading day but precede the
window (look-ahead). This module masks those values to 07:00/12:00-20:00 UTC before use.

Switches: flip=True keeps only setups with (px - stop) in [1.2, 4.0] $/oz; s1=int takes the stage-1 params from
STAGE1_TOP[s1] (stage-2 helper).

History: the first sweep of this module (2026-09-30 ~04:00) had a cache-key collision in _sweeps() (the raw 'eq'
(ssl, bsl) pair and the liq='eq' 4-tuple shared one key), which crashed one config and made results depend on
config order; fixed, and equal pivots must now be distinct swings. That sweep is superseded.
"""
import numpy as np
import pandas as pd

from data import atr, news_block_mask, resample_causal
from features import fvg, prev_day_hl, running_day_extremes, window_range

# Stage 1: SYNTHESIS grid window (3, + '10,14' NY AM+PM and 'sb' = all three) x g {0.3, 0.5} x target {2R, opp},
# crossed with the liquidity definition {sess, eq, both} and sweep depth delta {0, 0.25} = 5 x 2 x 2 x 3 x 2 = 120.
GRID = {
    "win": ["3", "10", "14", "10,14", "sb"],
    "g": [0.3, 0.5],
    "tp": ["2R", "opp"],
    "liq": ["sess", "eq", "both"],
    "delta": [0.0, 0.25],
}

# Stage-2 bases (results/silver_bullet_s1_train.csv): the top 5 by TRAIN t (n >= 60; all win '10', n 68-100 < 150)
# plus the best config with n >= 150 (s1=5, win '10,14').
STAGE1_TOP: list = [
    dict(win="10", g=0.5, tp="opp", liq="both", delta=0.0),
    dict(win="10", g=0.5, tp="opp", liq="eq", delta=0.25),
    dict(win="10", g=0.5, tp="opp", liq="eq", delta=0.0),
    dict(win="10", g=0.5, tp="opp", liq="both", delta=0.25),
    dict(win="10", g=0.5, tp="2R", liq="both", delta=0.0),
    dict(win="10,14", g=0.5, tp="opp", liq="eq", delta=0.25),
]
# Stage 2: s1 (6) x stop pad sb_stop {0.2, 0.5} x tmax {30, 60, 120} x sweep look-back {30, 60} x news blackout
# {'30' = [T-30, T+30], '5_15' = [T-5, T+15] (equity >= $21 only)} = 144.
GRID_S2 = {"s1": list(range(6)), "sb_stop": [0.2, 0.5], "tmax": [30, 60, 120], "look": [30, 60],
           "news": ["30", "5_15"]}

FLIP_STOP = (1.2, 4.0)
NEWS = {"off": None, "30": (30, 30), "2_5": (2, 5), "5_15": (5, 15), "15_30": (15, 30)}
FLAT_NY = 16 * 60 + 45
SB_HOURS = (3, 10, 14)

_CACHE: dict = {}


def _base(m1: pd.DataFrame) -> dict:
    key = (len(m1), int(m1["ts"].values[0]), int(m1["ts"].values[-1]))
    if key in _CACHE:
        return _CACHE[key]
    _CACHE.clear()
    o, h, l, c = (m1[k].values.astype(np.float64) for k in "ohlc")
    ts = m1["ts"].values.astype(np.int64)
    mod = m1["mod"].values.astype(np.int64)
    ny_mod = m1["ny_mod"].values.astype(np.int64)
    dow = m1["dow"].values
    tday = m1["tday"].values
    A = atr(m1)
    htf, known = resample_causal(m1, 5)
    a5 = atr(htf)
    A5 = np.where(known >= 0, a5[np.maximum(known, 0)], np.nan)
    body = np.abs(c - o)
    rng = np.maximum(h - l, 1e-9)
    avgb = pd.Series(body).shift(1).rolling(20, min_periods=20).mean().values
    fb, fb_lo, fb_hi, fs, fs_lo, fs_hi = fvg(h, l)
    # session levels, masked to same-calendar-day bars after the window (see ENGINE NOTE)
    a_hi, a_lo = window_range(mod, tday, h, l, 0, 420)
    l_hi, l_lo = window_range(mod, tday, h, l, 420, 720)
    a_ok = (mod >= 420) & (mod < 1200)
    l_ok = (mod >= 720) & (mod < 1200)
    a_hi, a_lo = np.where(a_ok, a_hi, np.nan), np.where(a_ok, a_lo, np.nan)
    l_hi, l_lo = np.where(l_ok, l_hi, np.nan), np.where(l_ok, l_lo, np.nan)
    pdh, pdl = prev_day_hl(tday, h, l)
    dh, dl = running_day_extremes(tday, h, l)
    gate = ~(((mod >= 20 * 60 + 30) & (mod < 23 * 60 + 30))
             | ((dow == 4) & (mod >= 19 * 60))
             | (dow >= 5)
             | ((ny_mod >= 16 * 60 + 25) & (ny_mod < 18 * 60)))
    flat = ts + ((FLAT_NY - ny_mod) % (24 * 60)) * 60_000
    codes, _ = pd.factorize(tday)
    starts = np.flatnonzero(np.r_[True, codes[1:] != codes[:-1]])
    ends = np.r_[starts[1:], len(tday)]
    out = dict(o=o, h=h, l=l, c=c, ts=ts, A=A, A5=A5, body=body, rng=rng, avgb=avgb, fb=fb, fb_lo=fb_lo,
               fb_hi=fb_hi, fs=fs, fs_lo=fs_lo, fs_hi=fs_hi, a_hi=a_hi, a_lo=a_lo, l_hi=l_hi, l_lo=l_lo, pdh=pdh,
               pdl=pdl, dh=dh, dl=dl, gate=gate, flat=flat, ny_mod=ny_mod, starts=starts, ends=ends, news={},
               sweeps={})
    _CACHE[key] = out
    return out


def _news_mask(b: dict, news: str):
    if NEWS.get(news) is None:
        return None
    if news not in b["news"]:
        bef, aft = NEWS[news]
        b["news"][news] = news_block_mask(b["ts"], bef, aft, impacts=("HIGH",))
    return b["news"][news]


def _first_cross(x, lvl, A5, delta, a, e, below):
    """First index s in [a, e) with x[s] < lvl - delta*A5[s] (below) or x[s] > lvl + delta*A5[s]; -1 if none."""
    if a >= e:
        return -1
    seg = x[a:e] < lvl - delta * A5[a:e] if below else x[a:e] > lvl + delta * A5[a:e]
    k = np.flatnonzero(seg)
    return a + int(k[0]) if len(k) else -1


def _sess_sweeps(b, delta):
    """Per-bar swept level arrays (NaN = no sweep) for session levels: ssl (lows taken), bsl (highs taken)."""
    h, l, A5 = b["h"], b["l"], b["A5"]
    n = len(h)
    ssl = np.full(n, np.nan)
    bsl = np.full(n, np.nan)
    for s0, e in zip(b["starts"], b["ends"]):
        lev = [("pd", s0, b["pdh"][s0], b["pdl"][s0])]
        for nm, hi_a, lo_a in (("asia", b["a_hi"], b["a_lo"]), ("lon", b["l_hi"], b["l_lo"])):
            ok = np.flatnonzero(np.isfinite(hi_a[s0:e]))
            if len(ok):
                live = s0 + int(ok[0])
                lev.append((nm, live, hi_a[live], lo_a[live]))
        for nm, live, hi, lo in lev:
            if not (np.isfinite(hi) and np.isfinite(lo)):
                continue
            # a session level's validity ends when its value stops being exposed (masked range) or at the day end
            end = e
            if nm in ("asia", "lon"):
                arr = b["a_hi"] if nm == "asia" else b["l_hi"]
                ok = np.flatnonzero(np.isfinite(arr[live:e]))
                end = live + int(ok[-1]) + 1
            s = _first_cross(l, lo, A5, delta, live, end, True)
            if s >= 0:
                ssl[s] = np.nanmin([ssl[s], lo])
            s = _first_cross(h, hi, A5, delta, live, end, False)
            if s >= 0:
                bsl[s] = np.nanmax([bsl[s], hi])
    return ssl, bsl


def _pivot_idx(x, k, is_high):
    win = 2 * k + 1
    ref = pd.Series(x).rolling(win, center=True)
    ext = ref.max().values if is_high else ref.min().values
    p = np.flatnonzero(x == ext)
    return p[(p >= k) & (p < len(x) - k)]


def _eq_sweeps(b, delta, k=3, Lb=240, tol_eq=0.1):
    """Per-bar swept level arrays for equal highs / equal lows (see module docstring)."""
    h, l, A5 = b["h"], b["l"], b["A5"]
    n = len(h)
    ssl = np.full(n, np.nan)
    bsl = np.full(n, np.nan)
    for is_high, x, out in ((False, l, ssl), (True, h, bsl)):
        P = _pivot_idx(x, k, is_high)
        pv = x[P]
        j0 = 0
        for jq in range(len(P)):
            q = P[jq]
            while P[j0] <= q - Lb:
                j0 += 1
            if j0 >= jq:
                continue
            conf = q + k
            if conf >= n or not np.isfinite(A5[conf]):
                continue
            tol = tol_eq * A5[conf]
            # two DISTINCT swings: the earlier pivot must be more than k bars before q (no flat-bottom ties)
            cand = np.flatnonzero((np.abs(pv[j0:jq] - pv[jq]) <= tol) & (P[j0:jq] < q - k))
            if not len(cand):
                continue
            p = P[j0 + cand[-1]]               # the most recent matching pivot
            if is_high:
                lvl = max(x[p], x[q])
                if p + 1 < q and h[p + 1:q].max() > lvl:
                    continue
                s = _first_cross(h, lvl, A5, delta, conf + 1, min(conf + 1 + Lb, n), False)
                if s >= 0:
                    out[s] = np.nanmax([out[s], lvl])
            else:
                lvl = min(x[p], x[q])
                if p + 1 < q and l[p + 1:q].min() < lvl:
                    continue
                s = _first_cross(l, lvl, A5, delta, conf + 1, min(conf + 1 + Lb, n), True)
                if s >= 0:
                    out[s] = np.nanmin([out[s], lvl])
    return ssl, bsl


def _sweeps(b, liq, delta):
    key = ("res", liq, float(delta))
    if key in b["sweeps"]:
        return b["sweeps"][key]
    parts = []
    for src, fn in (("sess", _sess_sweeps), ("eq", _eq_sweeps)):
        if liq in (src, "both"):
            k2 = ("raw", src, float(delta))          # raw (ssl, bsl) pair; distinct key from the 4-tuple result
            if k2 not in b["sweeps"]:
                b["sweeps"][k2] = fn(b, delta)
            parts.append(b["sweeps"][k2])
    ssl = np.fmin.reduce([p[0] for p in parts]) if len(parts) > 1 else parts[0][0]
    bsl = np.fmax.reduce([p[1] for p in parts]) if len(parts) > 1 else parts[0][1]
    n = len(ssl)
    idx = np.arange(n)
    last_ssl = np.maximum.accumulate(np.where(np.isfinite(ssl), idx, -1))
    last_bsl = np.maximum.accumulate(np.where(np.isfinite(bsl), idx, -1))
    res = (ssl, bsl, last_ssl, last_bsl)
    b["sweeps"][key] = res
    return res


def orders(m1, win="10", g=0.3, tp="2R", liq="both", delta=0.0, e=0.5, disp=1.5, br=0.7, sb_stop=0.2, look=60,
           exp_min=25, tmax=60, reclaim=1, news="30", flip=False, s1=None):
    if s1 is not None:
        p = STAGE1_TOP[int(s1)]
        win, g, tp, liq, delta = p["win"], p["g"], p["tp"], p["liq"], p["delta"]
    b = _base(m1)
    o, h, l, c, ts, A = b["o"], b["h"], b["l"], b["c"], b["ts"], b["A"]
    n = len(c)
    ssl, bsl, last_ssl, last_bsl = _sweeps(b, liq, float(delta))
    hours = SB_HOURS if str(win) == "sb" else tuple(int(x) for x in str(win).split(","))
    hr = b["ny_mod"] // 60
    inwin = np.isin(hr, hours)
    ok = inwin & b["gate"]
    blk = _news_mask(b, news)
    if blk is not None:
        ok &= ~blk
    body, rng, avgb = b["body"], b["rng"], b["avgb"]
    with np.errstate(invalid="ignore"):
        big = (body >= disp * avgb) & (body / rng >= br)
        disp_up = np.r_[False, (big & (c > o))[:-1]]     # middle bar i-1 of the FVG at i
        disp_dn = np.r_[False, (big & (c < o))[:-1]]
        gap_b = b["fb_hi"] - b["fb_lo"]
        gap_s = b["fs_hi"] - b["fs_lo"]
        cb = b["fb"] & disp_up & (gap_b >= g * A)
        cs = b["fs"] & disp_dn & (gap_s >= g * A)
    prev = np.r_[-1, np.arange(n - 1)]
    out = []
    taken = set()
    for i in np.flatnonzero((cb | cs) & ok):
        wkey = (int(b["flat"][i]), int(hr[i]))          # (NY date via its 16:45 flat time, hour): one setup per window-day
        if wkey in taken:
            continue
        for d in ((1,) if cb[i] else ()) + ((-1,) if cs[i] else ()):
            s = (last_ssl if d > 0 else last_bsl)[prev[i]]
            if s < 0 or i - s > look:
                continue
            lvl = (ssl if d > 0 else bsl)[s]
            if reclaim and (c[i] - lvl) * d <= 0:
                continue
            if d > 0:
                glo, ghi = b["fb_lo"][i], b["fb_hi"][i]
                px = ghi - e * (ghi - glo)
                sl = l[s:i + 1].min() - sb_stop * A[i]
                opp = b["dh"][i]
            else:
                glo, ghi = b["fs_lo"][i], b["fs_hi"][i]
                px = glo + e * (ghi - glo)
                sl = h[s:i + 1].max() + sb_stop * A[i]
                opp = b["dl"][i]
            risk = (px - sl) * d
            if not risk > 0:
                continue
            if tp == "2R":
                tpx = px + d * 2.0 * risk
            else:
                if (opp - px) * d < risk:
                    continue
                tpx = opp
            taken.add(wkey)
            if flip and not (FLIP_STOP[0] <= risk <= FLIP_STOP[1]):
                break
            t = int(ts[i]) + 60_000
            out.append({"t": t, "d": int(d), "kind": "limit", "px": float(px), "exp": t + int(exp_min) * 60_000,
                        "sl": float(sl), "tp": float(tpx), "tmax": int(tmax) * 60_000, "flat": int(b["flat"][i]),
                        "tag": f"sb{hr[i]}{'L' if d > 0 else 'S'}"})
            break
    out.sort(key=lambda q: q["t"])
    return out
