"""
cand/range_fade.py - Hypothesis H10 (SYNTHESIS.md section 6): fade the edge of a range at a strong zone, with a real
stop, target the range midpoint. The owner's setup B (trade T2) with the stop it lacked.

Rules, written for the SHORT side as in SYNTHESIS (the long side is the exact mirror, run on the negated series by
zonekit; identical parameters, no directional veto):

  ZONES     features.zone_timeline(h, l, A, L=240, k=3, w, K=3) known at the close of bar t-1 (zonekit.zones);
            the zone's pivot count is its touch count.
  RANGE     over bars t-P .. t-1: RH = max h, RL = min l, mid = (RH + RL) / 2, and RH - RL <= wid * A[t-1].
  EDGE      the faded (upper) zone: touches >= K_edge, z_lo > mid, and RH inside [z_lo, z_hi + 0.5*A]
            (the range top is this zone). If several qualify, the lowest one (nearest to price) is used.
  OPPOSITE  some other zone with touches >= 3, z_hi < mid, and RL inside [z_lo - 0.5*A, z_hi] (a real range floor).
  TOUCH     bar t: h[t] >= z_lo of the edge zone and c[t-1] < z_lo (price arrives from inside the range).
  TRIGGER   trig=1: the first j in [t, t+2] with TRIG_BEAR(j, z_lo): bear engulf, or upper wick >= w_pin*range with
            h[j] >= z_lo > c[j]. trig=0 (control arm): j = t if c[t] <= z_hi.
            A close above z_hi, or a high at/above the stop, on bars t..j aborts the setup.
  ENTRY     market at the close of j (order t = ts[j] + 60 s).
  STOP      z_hi + sigma*A[j]   (sigma = 0.3)
  TARGET    tp='mid': the range midpoint, only if it is at least min_rr x stop distance below the entry;
            tp='1R': 1 x stop distance.
  TIME      tmax = 30 minutes. One position at a time (sim).
  SESSION   decision bar in 06:00-12:00 London local OR 08:30-11:30 ET (sess='lonny'); HIGH-impact calendar
            blackout +-news minutes (default 30, the <$21-equity rule).

Units: A = data.atr(m1). The flip stop band [1.2, 4.0] $/oz is applied only in reporting (sweep.evaluate).
"""
import numpy as np
import pandas as pd

import features as F
import zonekit as ZK

# SYNTHESIS H10 grid P(2) x wid(2) x K_edge(2) x TP(2) = 16, extended with zone width w(2) and the trigger vs
# no-trigger control arm(2) = 64.
GRID = {
    "w": [0.5, 1.0],
    "P": [60, 180],
    "wid": [8, 12],
    "K_edge": [6, 8],
    "tp": ["mid", "1R"],
    "trig": [1, 0],
}

DEFAULTS = dict(w=1.0, P=60, wid=12, K_edge=6, K_opp=3, tp="mid", trig=1, L=240, k=3, sigma=0.3, w_pin=0.6,
                min_rr=0.5, tmax=30, sess="lonny", news=30, edge_tol=0.5)


def setups(m1, **params):
    p = {**DEFAULTS, **params}
    b = ZK.base(m1)
    n, A, ts = b["n"], b["A"], b["ts"]
    P, k = int(p["P"]), int(p["k"])
    wid, sigma, w_pin, tol = float(p["wid"]), float(p["sigma"]), float(p["w_pin"]), float(p["edge_tol"])
    K_edge, K_opp, trig = int(p["K_edge"]), int(p["K_opp"]), int(p["trig"])
    sess = ZK.session(b, p["sess"])
    blocked = ZK.news(b, int(p["news"]))
    zt = ZK.zones(b, int(p["L"]), k, float(p["w"]), 3)

    ahead = pd.Series(sess[::-1].astype(np.int8)).rolling(3, min_periods=1).max().values[::-1] > 0
    T = np.flatnonzero(ahead)
    T = T[(T > P + 2) & (T < n - 4)]
    bar, zlo_p, zhi_p, ztc = ZK.zone_pairs(b, zt, T - 1)            # zones known at t-1
    tb = bar + 1
    rows = []
    for d in (1, -1):
        sp = b["sp"][d]
        o, h, l, c, eng = sp["o"], sp["h"], sp["l"], sp["c"], sp["eng"]
        # long space: fade the LOWER edge (support) of the range, buy
        RH = F.rolling_max_prev(h, P)        # bars t-P..t-1
        RL = F.rolling_min_prev(l, P)
        lo_, hi_ = (zlo_p, zhi_p) if d > 0 else (-zhi_p, -zlo_p)
        rh, rl, Am = RH[tb], RL[tb], A[tb - 1]
        mid = 0.5 * (rh + rl)
        rng_ok = (rh - rl) <= wid * Am
        edge = rng_ok & (ztc >= K_edge) & (hi_ < mid) & (rl >= lo_ - tol * Am) & (rl <= hi_) \
            & (l[tb] <= hi_) & (c[tb - 1] > hi_)
        if not edge.any():
            continue
        opp = rng_ok & (ztc >= K_opp) & (lo_ > mid) & (rh <= hi_ + tol * Am) & (rh >= lo_)
        opp_bars = np.unique(tb[opp])
        edge &= np.isin(tb, opp_bars)
        if not edge.any():
            continue
        cand = pd.DataFrame({"t": tb[edge], "lo": lo_[edge], "hi": hi_[edge], "tc": ztc[edge], "mid": mid[edge]})
        cand = cand.sort_values(["t", "hi"], ascending=[True, False]).drop_duplicates("t")
        for t, zl, zh, tc, md in cand.itertuples(index=False):
            j = -1
            for jj in range(t, min(t + 3, n - 1)):
                stop_j = zl - sigma * A[jj]
                if c[jj] < zl or l[t:jj + 1].min() <= stop_j:
                    break
                if not trig:
                    if c[jj] >= zl:
                        j = jj
                    break
                if eng[jj] or ZK.pin_bull(o, h, l, c, jj, zh, w_pin):
                    j = jj
                    break
            if j < 0 or not sess[j] or blocked[j]:
                continue
            stop = zl - sigma * A[j]
            sd = c[j] - stop
            if not sd > 0:
                continue
            if p["tp"] == "mid":
                if md - c[j] < float(p["min_rr"]) * sd:
                    continue
                tpx = md
            else:
                tpx = c[j] + sd
            rows.append((int(ts[j]) + 60_000, d, int(t), int(j), stop * d, tpx * d, sd, (tpx - c[j]) / sd, int(tc),
                         zl, zh))
    cols = ["t", "d", "i_touch", "j", "sl", "tp", "sd", "tp_R", "z_k", "z_lo_ls", "z_hi_ls"]
    return ZK.dedupe(pd.DataFrame(rows, columns=cols))


def orders(m1, **params):
    p = {**DEFAULTS, **params}
    df = setups(m1, **p)
    return [{"t": int(r.t), "d": int(r.d), "kind": "mkt", "sl": float(r.sl), "tp": float(r.tp),
             "tmax": int(p["tmax"]) * 60_000, "tag": f"rf{'L' if r.d > 0 else 'S'}{p['tp']}"}
            for r in df.itertuples(index=False)]
