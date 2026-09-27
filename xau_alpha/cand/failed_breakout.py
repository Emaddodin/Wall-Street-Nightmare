"""
cand/failed_breakout.py - Hypothesis H3 (SYNTHESIS.md section 6): failed breakout -> breakdown -> retest from the
other side. The owner's setup C / trade T7 ("fake out").

Rules, written for the SHORT side as in SYNTHESIS (the long side is the exact mirror, run on the negated series by
zonekit; identical parameters, no directional veto):

  ZONES     features.zone_timeline(h, l, A, L, k, w, K) (via zonekit.zones), frozen at the close of bar
            s = i0 - N - 1, i.e. BEFORE the fake-out window, so the fake-out's own pivots cannot move the zone.
  START     c[s] <= z_hi: price was inside or below the zone before the window (a breakout, not a breakdown of a
            support that price had been sitting above).
  FAKE-OUT  window W = bars i0-N .. i0-1. f = argmax h[W] (first occurrence).
            fake=+1 (H3):   h[f] > z_hi + phi*A[i0]
            fake=-1 (twin): h[f] <= z_hi + phi*A[i0]        <- the "no-fake-out twin" null (plain breakdown)
            fake= 0:        no condition (union of both)
  BREAKDOWN c[i0] < z_lo - beta*A[i0], and every close c[f..i0-1] >= z_lo - beta*A[i0] (first close below since f).
            If several zones qualify on one bar, the lowest zone (the one just crossed) is used.
  RETEST    the first bar i1 in (i0, i0+R] with h[i1] >= z_lo - tau*A[i1]  (tau = 0.1).
  ENTRY     mode 'a': market at the close of i1 if c[i1] < z_lo (T7).
            mode 'b': the first j in [i1, i1+2] with TRIG_BEAR(j, z_lo) (bear engulf, or upper wick >= w_pin*range
                      with h[j] >= z_lo > c[j]); a close above z_hi first aborts.
            mode 'c': sell limit at z_lo - 0.1*A[i0] placed at the close of i0, expiring after R bars.
  STOP      max(h[f], z_hi, h[i1..j]) + sigma*A   (absolute price; for mode c: max(h[f], z_hi) + sigma*A[i0])
  TARGET    tp_mode 'near': NEAREST_LEVEL (nearest confirmed swing low of the last 240 bars) at least tp_r x stop
            distance away, else 2R if none within 4R; tp_mode 'r': tp_r x stop distance.
  TIME      tmax minutes.  One position at a time (sim).
  SESSION   decision bar (j, or i0 for mode c) in 06:00-12:00 London local OR 08:30-11:30 ET (sess='lonny');
            HIGH-impact calendar blackout +-news minutes (default 30 = the <$21-equity rule, SYNTHESIS N2).

Units: A = data.atr(m1) (M1 Wilder ATR14). No dollar thresholds; the flip stop band [1.2, 4.0] $/oz is applied
only in reporting (sweep.evaluate flip_eligible) so the wide-stop result stays visible for the >= $100 account.
"""
import numpy as np
import pandas as pd

import zonekit as ZK

# Stage 1 (SYNTHESIS H3, w fixed at 1.0 from H2's stage 1: its top-ranked configs all use w=1.0):
# K(2) x N(3) x phi(3) x beta(2) x R(2) x mode(3) = 216, with sigma=0.3, TP = nearest level (rho=1), tmax=45.
GRID = {
    "w": [1.0],
    "K": [3, 6],
    "N": [10, 20, 30],
    "phi": [0.3, 0.6, 1.0],
    "beta": [0.0, 0.2],
    "R": [5, 15],
    "mode": ["a", "b", "c"],
}

DEFAULTS = dict(w=1.0, K=3, N=20, phi=0.6, beta=0.0, R=15, mode="a", fake=1, L=240, k=3, tau=0.1, w_pin=0.6,
                sigma=0.3, tp_mode="near", tp_r=1.0, tmax=45, sess="lonny", news=30, lim_off=0.1)


def setups(m1, **params):
    """All entry signals (one row per signal, before the one-position-at-a-time filter)."""
    p = {**DEFAULTS, **params}
    b = ZK.base(m1)
    n, A, ts = b["n"], b["A"], b["ts"]
    N, R, k = int(p["N"]), int(p["R"]), int(p["k"])
    phi, beta, tau, sigma = float(p["phi"]), float(p["beta"]), float(p["tau"]), float(p["sigma"])
    fake, mode, w_pin = int(p["fake"]), str(p["mode"]), float(p["w_pin"])
    sess = ZK.session(b, p["sess"])
    blocked = ZK.news(b, int(p["news"]))
    zt = ZK.zones(b, int(p["L"]), k, float(p["w"]), int(p["K"]))
    piv = ZK.pivots(b, k)

    # breakdown bars that can still lead to an in-session decision within R+2 bars
    ahead = pd.Series(sess[::-1].astype(np.int8)).rolling(R + 3, min_periods=1).max().values[::-1] > 0
    S = np.flatnonzero(ahead)
    S = S[(S > N + 2) & (S < n - R - 4)]
    bar, zlo_p, zhi_p, _ = ZK.zone_pairs(b, zt, S - N - 1)       # zones known at s = i0 - N - 1
    i0s = bar + N + 1
    rows = []
    for d in (1, -1):
        sp = b["sp"][d]
        o, h, l, c, eng = sp["o"], sp["h"], sp["l"], sp["c"], sp["eng"]
        # long space: the "short" rule above becomes: fake-out BELOW z_lo, breakout ABOVE z_hi, retest from above
        lo_, hi_ = (zlo_p, zhi_p) if d > 0 else (-zhi_p, -zlo_p)
        Ab = A[i0s]
        lvl = hi_ + beta * Ab
        ok = (c[i0s] > lvl) & (c[i0s - 1] <= lvl) & (c[i0s - N - 1] >= lo_)
        if not ok.any():
            continue
        cand = pd.DataFrame({"i0": i0s[ok], "lo": lo_[ok], "hi": hi_[ok], "lvl": lvl[ok]})
        # per bar, the highest (long space) zone = the one just crossed
        cand = cand.sort_values(["i0", "hi"], ascending=[True, False]).drop_duplicates("i0")
        for i0, zl, zh, lv in cand.itertuples(index=False):
            w0 = i0 - N
            f = w0 + int(np.argmin(l[w0:i0]))
            is_fake = l[f] < zl - phi * A[i0]
            if (fake == 1 and not is_fake) or (fake == -1 and is_fake):
                continue
            if c[f:i0].max() > lv:                       # not the first close beyond since the extreme
                continue
            ext = min(l[f], zl)
            if mode == "c":
                if not sess[i0] or blocked[i0:i0 + R + 1].any():
                    continue
                px = zh + float(p["lim_off"]) * A[i0]
                stop = ext - sigma * A[i0]
                sd = px - stop
                if not sd > 0:
                    continue
                tpx, src = ZK.target(piv[d], i0, k, px, sd, p["tp_mode"], float(p["tp_r"]))
                t = int(ts[i0]) + 60_000
                rows.append((t, d, "limit", px * d, t + R * 60_000, int(i0), int(i0), stop * d, tpx * d, sd,
                             zl, zh, src))
                continue
            i1 = -1
            for i in range(i0 + 1, min(i0 + R, n - 1) + 1):
                if l[i] <= zh + tau * A[i]:
                    i1 = i
                    break
            if i1 < 0:
                continue
            j = -1
            if mode == "a":
                if c[i1] > zh:
                    j = i1
            else:
                for jj in range(i1, min(i1 + 3, n - 1)):
                    if c[jj] < zl:
                        break
                    if eng[jj] or ZK.pin_bull(o, h, l, c, jj, zh, w_pin):
                        j = jj
                        break
            if j < 0 or not sess[j] or blocked[j]:
                continue
            stop = min(ext, l[i1:j + 1].min()) - sigma * A[j]
            sd = c[j] - stop
            if not sd > 0:
                continue
            tpx, src = ZK.target(piv[d], j, k, c[j], sd, p["tp_mode"], float(p["tp_r"]))
            rows.append((int(ts[j]) + 60_000, d, "mkt", np.nan, 0, int(i0), int(j), stop * d, tpx * d, sd,
                         zl, zh, src))
    cols = ["t", "d", "kind", "px", "exp", "i0", "j", "sl", "tp", "sd", "z_lo_ls", "z_hi_ls", "tp_src"]
    return ZK.dedupe(pd.DataFrame(rows, columns=cols))


def orders(m1, **params):
    p = {**DEFAULTS, **params}
    df = setups(m1, **p)
    out = []
    tag0 = {1: "fb", 0: "fbAll", -1: "fbTwin"}[int(p["fake"])]
    for r in df.itertuples(index=False):
        o = {"t": int(r.t), "d": int(r.d), "kind": r.kind, "sl": float(r.sl), "tp": float(r.tp),
             "tmax": int(p["tmax"]) * 60_000, "tag": f"{tag0}{'L' if r.d > 0 else 'S'}{p['mode']}{r.tp_src}"}
        if r.kind == "limit":
            o["px"] = float(r.px)
            o["exp"] = int(r.exp)
        out.append(o)
    return out
