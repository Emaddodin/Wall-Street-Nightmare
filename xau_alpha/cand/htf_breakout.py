"""
cand/htf_breakout.py - Hypothesis X2(a): higher-timeframe Donchian close-breakout trend following, both directions.

Idea (OPINION / textbook trend following: Donchian "turtle" channel breakouts, chandelier exits): gold trends on
multi-hour horizons; a close beyond the N-bar channel of H1 or H4 bars starts a trend leg that a wide ATR-based
stop and a trailing exit can ride. This is a main-strategy candidate for equity >= $100 (stops are far wider than
the $4 flip cap; the flip-eligible subset is reported separately).

Rules (longs and shorts are exact mirrors, identical parameters, no directional veto):
  BARS      HTF = `tf`-minute mid bars (60 = H1, 240 = H4), UTC-aligned, from data.resample_causal. HTF bar k is
            known at the close of M1 bar ki[k] (its last minute); the decision time is t = ts[ki[k]] + 60 s.
  SIGNAL    long  if C[k] > max(H[k-N..k-1])   (close beyond the prior N-bar Donchian high)
            short if C[k] < min(L[k-N..k-1])
            fresh=1: only the first close beyond the channel (C[k-1] was inside the channel of bar k-1).
            fresh=0: every close beyond the channel is a signal; the simulator's one-position-at-a-time rule turns
            this into "re-enter on the next new N-bar extreme after an exit".
  GATES     htf_base.gate at the decision bar (no entries 20:30-23:30 UTC, Friday after 19:00 UTC, Sunday reopen,
            [-5, +15] min around HIGH calendar rows). hold='eod' also forbids entries from 15:00 ET to 17:00 ET.
  ENTRY     market order at t.
  STOP      initial stop sl x ATR14(HTF) at the signal bar (distance from the fill).
  EXIT      ex = 'chX'  : chandelier trail X x ATR14(HTF) behind the best exit-side price (fixed distance set at
                          entry, active from entry; the stop never loosens below the initial stop)
                 'don'  : turtle channel exit: flatten at the close of the first later HTF bar m whose close is
                          beyond the opposite channel of max(5, N//2) bars (C[m] < min(L[m-X..m-1]) for a long)
                 'rX'   : fixed target X x the initial stop distance
            hold = 'eod'   : also flat at 16:45 ET of the entry's trading day (intraday only, no swap)
                   'multi' : also flat after `maxd` calendar days (multi-day; swap is NOT modelled, see report)
            Every trade is also flattened at 2026-05-31 23:59 UTC so no position is simulated into the holdout.

Units: N in HTF bars, sl/X in ATR(HTF) multiples (scale-free); no dollar thresholds.
"""
import numpy as np

from features import rolling_max_prev, rolling_min_prev
from htf_base import DAY_MS, LAST_FLAT_MS, base, cache, htf

# Stage-1 grid: tf(2) x N(4) x sl(2) x ex(4) x hold(2) = 128 configs (fresh=0, maxd=10).
GRID = {
    "tf": [60, 240],
    "N": [10, 20, 34, 55],
    "sl": [1.5, 3.0],
    "ex": ["ch2", "ch3.5", "don", "r2"],
    "hold": ["eod", "multi"],
}


def _next_true_after(cond: np.ndarray) -> np.ndarray:
    """nxt[k] = smallest m > k with cond[m] True (-1 if none)."""
    idx = np.flatnonzero(cond)
    pos = np.searchsorted(idx, np.arange(len(cond)), side="right")
    return np.where(pos < len(idx), idx[np.minimum(pos, len(idx) - 1)], -1)


def _signals(b, tf, N, fresh):
    def mk():
        H = htf(b, tf)
        hh = rolling_max_prev(H["h"], N)
        ll = rolling_min_prev(H["l"], N)
        C = H["c"]
        up = C > hh
        dn = C < ll
        if fresh:
            up &= ~np.r_[False, up[:-1]]
            dn &= ~np.r_[False, dn[:-1]]
        X = max(5, N // 2)
        exl = rolling_min_prev(H["l"], X)
        exh = rolling_max_prev(H["h"], X)
        nx_long = _next_true_after(C < exl)       # turtle exit for longs
        nx_short = _next_true_after(C > exh)
        return up, dn, nx_long, nx_short
    return cache(("sig", tf, N, fresh), mk)


def orders(m1, tf=60, N=20, sl=3.0, ex="ch3.5", hold="eod", fresh=0, maxd=10, act=0.0, tag="htf_bo"):
    b = base(m1)
    H = htf(b, tf)
    up, dn, nx_long, nx_short = _signals(b, tf, int(N), int(fresh))
    ki, a_htf = H["ki"], H["atr"]
    ts, gate, ny, eod_ms = b["ts"], b["gate"], b["ny"], b["eod_ms"]
    n = b["n"]
    out = []
    for k in np.flatnonzero(up | dn):
        i = ki[k]
        if i >= n or k < N + 14 or not np.isfinite(a_htf[k]):
            continue
        if not gate[i]:
            continue
        if hold == "eod" and 15 * 60 <= ny[i] < 17 * 60:
            continue
        d = 1 if up[k] else -1
        t = int(ts[i]) + 60_000
        sd = sl * a_htf[k]
        o = {"t": t, "d": d, "kind": "mkt", "sl_dist": float(sd), "tag": f"{tag}_{'L' if d > 0 else 'S'}"}
        flat = LAST_FLAT_MS
        if hold == "eod":
            flat = min(flat, int(eod_ms[i]))
        else:
            flat = min(flat, t + int(maxd * DAY_MS))
        if ex.startswith("ch"):
            o["trail"] = float(float(ex[2:]) * a_htf[k])
            o["trail_act"] = float(act * sd)
        elif ex == "don":
            m = (nx_long if d > 0 else nx_short)[k]
            if m >= 0 and ki[m] < n:
                flat = min(flat, int(ts[ki[m]]) + 60_000)
        elif ex.startswith("r"):
            o["tp_dist"] = float(float(ex[1:]) * sd)
        o["flat"] = flat
        out.append(o)
    return out
