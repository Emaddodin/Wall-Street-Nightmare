"""
cand/round_reject.py - Hypothesis H9 (recon/SYNTHESIS.md section 6): rejection at the FIRST touch of a round number.

Idea (SOURCED web_research section 2.4): gold shows barrier effects at round numbers (Aggarwal-Lucey 2007) and take-profit
orders cluster there (Osler 2003). HANDOFF l.36: crossing a $10 level showed nothing, so only the rejection side is
tested here: price arrives at a round level it has not been near for a while, prints a rejection wick, and we fade.

Levels: L = k * step + off, step in {10, 25, 50} $/oz. off = 0 for the real levels; the PLACEBO arms use
off = 0.37 * step or 0.63 * step (for step 10 these are SYNTHESIS's L + 3.7 and L + 6.3).

Rule, short side (touching from below; the long side is the exact mirror with identical parameters):
  1. First touch at bar i: L = the lowest level >= max(h[i-M..i-1]) + mu*A[i]  (so for the prior M bars price stayed
     at least mu*A below L), and h[i] >= L - tol*A[i].  This is K_app=0, the literal SYNTHESIS rule; it forces the
     touch bar itself to cover the last mu*A (a fast approach).
     K_app>0 (variant): the mu*A clearance applies to bars [i-M, i-K_app-1] only; the last K_app bars may approach the
     level but must not touch it (max(h[i-K_app..i-1]) < L - tol*A). This is "first touch in M bars" with a normal
     approach.
  2. Trigger on the first bar j in [i, i+W]: upper wick >= wk * range[j] and c[j] <= L - dep*A[j].
     trig=0 is the CONTROL arm: the same bar-j condition without the wick requirement (c[j] <= L - dep*A[j] only).
     The setup is cancelled if any bar in [i, j) closes above L (price accepted above the level: not a rejection).
  3. Entry: market order at the close of j (t = ts[j] + 60 s; the sim fills on the next 10-s bar, bid for shorts).
  4. Stop: max(h[i..j]) + sigma*A[j] (absolute price).
  5. Target: c[j] - tp * (stop - c[j])  (tp in R).
  6. Time stop: tmax minutes. Flat by 16:45 ET.
Gates (SYNTHESIS section 5, OPINION there): no entries 20:30-23:30 UTC, after 19:00 UTC on Friday, on Sunday (UTC)
bars (the Sunday reopen) or 16:25-18:00 ET (flat by 16:45 ET); no entry whose decision bar is inside the HIGH-impact
calendar blackout `news` (default '30' = [T-30, T+30], the N2 rule for equity < $21).
sess='active' additionally restricts entries to 07:00 London local .. 16:25 ET.

All thresholds are in units of A = Wilder ATR14 of M1 mid (data.atr), except the level grid itself (dollar round numbers
by construction) and the broker stop window used by `flip`.

Switches (same code path):
  place in {0, 1, 2}: 0 real levels, 1 placebo off = 0.37*step, 2 placebo off = 0.63*step.
  flip=True: keep only trades whose estimated stop distance (stop - c[j]) is within [1.2, 4.0] $/oz.
  s1=int: stage-2 helper, takes (step, M, mu, sigma, tp, tmax) from STAGE1_TOP[s1].
"""
import numpy as np
import pandas as pd

from data import atr, news_block_mask
from features import rolling_max_prev, rolling_min_prev

# Stage 1 (SYNTHESIS H9): L (3) x M {60, 240} x mu {1, 2} x stop {0.3, 0.6} x TP {1, 1.5} x tmax {10, 20} = 96,
# crossed with K_app {0 (literal), 10} = 192. Fixed: wk 0.6, dep 0.2, W 2, tol 0.05, trig 1, sess 'all', news '30'.
GRID = {
    "step": [10, 25, 50],
    "M": [60, 240],
    "mu": [1.0, 2.0],
    "K_app": [0, 10],
    "sigma": [0.3, 0.6],
    "tp": [1.0, 1.5],
    "tmax": [10, 20],
}

# Stage 1 as run: GRID x K_app {0, 10} = 192 configs (results/round_reject_s1_train.csv). Every config had TRAIN
# PF < 1.0 at lf_base except six with n <= 33. Stage-2 bases = the top 5 by TRAIN t (n >= 60), all step 25 with
# n 69-82 (< 150), plus the best config with n >= 150 (s1=5, step 10), so the refinement can reach the n bar.
STAGE1_TOP: list = [
    dict(step=25, M=60, mu=2.0, sigma=0.3, tp=1.5, tmax=20),
    dict(step=25, M=240, mu=1.0, sigma=0.3, tp=1.5, tmax=20),
    dict(step=25, M=60, mu=2.0, sigma=0.3, tp=1.0, tmax=20),
    dict(step=25, M=60, mu=2.0, sigma=0.3, tp=1.5, tmax=10),
    dict(step=25, M=60, mu=2.0, sigma=0.6, tp=1.5, tmax=20),
    dict(step=10, M=60, mu=2.0, sigma=0.3, tp=1.5, tmax=20),
]
# Stage 2: s1 (6) x wick wk {0.5, 0.6} x close depth dep {0.1, 0.2} x session {all, active} x K_app {10, 30} = 96.
GRID_S2 = {"s1": list(range(6)), "wk": [0.5, 0.6], "dep": [0.1, 0.2], "sess": ["all", "active"], "K_app": [10, 30]}

FLIP_STOP = (1.2, 4.0)
NEWS = {"off": None, "30": (30, 30), "2_5": (2, 5), "5_15": (5, 15), "15_30": (15, 30)}
FLAT_NY = 16 * 60 + 45
PLACEBO_FRAC = (0.0, 0.37, 0.63)

_CACHE: dict = {}


def _base(m1: pd.DataFrame) -> dict:
    """Parameter-free causal features, computed once per process."""
    key = (len(m1), int(m1["ts"].values[0]), int(m1["ts"].values[-1]))
    if key in _CACHE:
        return _CACHE[key]
    _CACHE.clear()
    o, h, l, c = (m1[k].values.astype(np.float64) for k in "ohlc")
    ts = m1["ts"].values.astype(np.int64)
    mod = m1["mod"].values.astype(np.int64)
    ny_mod = m1["ny_mod"].values.astype(np.int64)
    lon_mod = m1["lon_mod"].values.astype(np.int64)
    dow = m1["dow"].values
    A = atr(m1)
    rng = np.maximum(h - l, 1e-9)
    up_w = (h - np.maximum(o, c)) / rng
    dn_w = (np.minimum(o, c) - l) / rng
    gate = ~(((mod >= 20 * 60 + 30) & (mod < 23 * 60 + 30))
             | ((dow == 4) & (mod >= 19 * 60))
             | (dow >= 5)
             | ((ny_mod >= 16 * 60 + 25) & (ny_mod < 18 * 60)))
    active = (lon_mod >= 7 * 60) & (ny_mod < 16 * 60 + 25)
    # next 16:45 ET at or after the bar (entries 16:25-18:00 ET are gated, so an Asia entry flattens next afternoon)
    add = (FLAT_NY - ny_mod) % (24 * 60)
    flat = ts + add * 60_000
    out = dict(o=o, h=h, l=l, c=c, ts=ts, A=A, up_w=up_w, dn_w=dn_w, gate=gate, active=active, flat=flat,
               news={}, mx={}, mn={})
    _CACHE[key] = out
    return out


def _roll(b, M, K=0):
    """Max high / min low over bars [i-M, i-K-1] (the 'far' part of the look-back), cached."""
    key = (M, K)
    if key not in b["mx"]:
        mx = rolling_max_prev(b["h"], M - K)
        mn = rolling_min_prev(b["l"], M - K)
        if K:
            mx = np.r_[np.full(K, np.nan), mx[:-K]]
            mn = np.r_[np.full(K, np.nan), mn[:-K]]
        b["mx"][key], b["mn"][key] = mx, mn
    return b["mx"][key], b["mn"][key]


def _news_mask(b: dict, news: str):
    if NEWS.get(news) is None:
        return None
    if news not in b["news"]:
        bef, aft = NEWS[news]
        b["news"][news] = news_block_mask(b["ts"], bef, aft, impacts=("HIGH",))
    return b["news"][news]


def orders(m1, step=10, M=60, mu=1.0, sigma=0.3, tp=1.0, tmax=10, wk=0.6, dep=0.2, W=2, tol=0.05, trig=1,
           K_app=0, sess="all", news="30", place=0, flip=False, s1=None):
    if s1 is not None:
        p = STAGE1_TOP[int(s1)]
        step, M, mu, sigma, tp, tmax = p["step"], p["M"], p["mu"], p["sigma"], p["tp"], p["tmax"]
    b = _base(m1)
    h, l, c, ts, A = b["h"], b["l"], b["c"], b["ts"], b["A"]
    n = len(c)
    mx, mn = _roll(b, int(M), int(K_app))
    if K_app:                                   # the last K_app bars may approach but must not touch the level
        nx, nn = _roll(b, int(K_app))
    off = PLACEBO_FRAC[int(place)] * step
    ok_entry = b["gate"] & (b["active"] if sess == "active" else True)
    blk = _news_mask(b, news)
    if blk is not None:
        ok_entry = ok_entry & ~blk
    with np.errstate(invalid="ignore"):
        up_req, dn_req = mx + mu * A, mn - mu * A
        if K_app:
            up_req = np.maximum(up_req, nx + (tol + 1e-3) * A)
            dn_req = np.minimum(dn_req, nn - (tol + 1e-3) * A)
        L_up = np.ceil((up_req - off) / step) * step + off      # lowest level >= mu*A clear of the look-back highs
        L_dn = np.floor((dn_req - off) / step) * step + off
        t_up = np.flatnonzero(h >= L_up - tol * A)                    # first touch from below -> short setup
        t_dn = np.flatnonzero(l <= L_dn + tol * A)                    # first touch from above -> long setup
    cand = [(i, -1, L_up[i]) for i in t_up] + [(i, 1, L_dn[i]) for i in t_dn]
    cand.sort()
    out = []
    for i, d, L in cand:
        if not np.isfinite(L) or not np.isfinite(A[i]):
            continue
        j_found = -1
        for j in range(i, min(i + int(W), n - 1) + 1):
            if d < 0:
                cond = c[j] <= L - dep * A[j] and (not trig or b["up_w"][j] >= wk)
            else:
                cond = c[j] >= L + dep * A[j] and (not trig or b["dn_w"][j] >= wk)
            if cond:
                j_found = j
                break
            if (c[j] - L) * d < 0:          # closed beyond the level (above for a short): not a rejection
                break
        if j_found < 0:
            continue
        j = j_found
        if not ok_entry[j]:
            continue
        if d < 0:
            sl = h[i:j + 1].max() + sigma * A[j]
        else:
            sl = l[i:j + 1].min() - sigma * A[j]
        risk = (c[j] - sl) * d
        if not risk > 0:
            continue
        if flip and not (FLIP_STOP[0] <= risk <= FLIP_STOP[1]):
            continue
        out.append({"t": int(ts[j]) + 60_000, "d": int(d), "kind": "mkt", "sl": float(sl),
                    "tp": float(c[j] + d * tp * risk), "tmax": int(tmax) * 60_000, "flat": int(b["flat"][j]),
                    "tag": f"rn{step}{'S' if d < 0 else 'L'}"})
    out.sort(key=lambda q: q["t"])
    return out
