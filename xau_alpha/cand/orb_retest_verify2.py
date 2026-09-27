"""
cand/orb_retest_verify2.py - adversarial verifier #2 for the frozen H6 finalist (cand/orb_retest.py).

Never TEST: orb_retest._base cuts at 2026-06-01, every order list is re-cut and asserted here, final=True is never used.
One process. Writes cand/results/orb_retest_verify2.json (small; no trade lists) after every part.

    python3 cand/orb_retest_verify2.py [--parts caus,base,null,stress,nbr,mt] [--rdv 1000]

Parts
  caus   independent re-implementation of the finalist from raw M1 + truncation invariance at 6 cut times
  base   sweep.evaluate under lf_base / lf_harsh / mid; R metrics, long/short, monthly, drop-best-month, bootstrap
  null   random_direction (VALID >= 1000 seeds, TRAIN 300), direction permutation (global and within month),
         mirror, always-long / always-short at the same timestamps
  stress lf_base + 0.3 $/oz slippage per side; lf_base + 0.3 $/oz per side at every fill (spread_add 0.6);
         entry delay +5 s / +10 s / +30 s / +60 s; slip+0.3 with +5 s
  nbr    12 nearest parameter neighbours (VALID PF and R)
  mt     multiple-testing haircut from the logged stage-1/2 TRAIN sweeps (CSV only)
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "cand"))

from sweep import evaluate  # noqa: E402  (puts lib and cand on sys.path first)
from data import _ms, load_calendar, load_m1  # noqa: E402
from nulltest import _apply_dir, _to_relative, mirror, random_direction  # noqa: E402
from sim import COSTS, Cost, _H_BASE, simulate, stats  # noqa: E402

import orb_retest as M  # noqa: E402  (same module object sweep.evaluate imports)

TEST_MS = _ms("2026-06-01")
VALID_MS = _ms("2026-01-01")
P = {"window": "lon", "filt": 0, "kap": 0.0, "entry": "brk", "stop": "far", "pad": 0.25, "tp": 1.0, "flat_utc": 780,
     "be": 0.0, "tmax": 0, "brk_h": 120, "nb": 30, "nexit": -1}
NAME = "orb_retest"
OUT = ROOT / "cand/results/orb_retest_verify2.json"
T0 = time.time()
ND = NormalDist()
RES: dict = {}
if OUT.exists():
    RES = json.load(open(OUT))
RES["params"] = P


def log(s):
    print(f"[{time.time() - T0:6.0f}s] {s}", flush=True)


def save():
    with open(OUT, "w") as f:
        json.dump(RES, f, indent=1, default=lambda x: x.item() if hasattr(x, "item") else str(x))


def tstat(x):
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 2:
        return None
    return round(float(x.mean() / (x.std(ddof=1) + 1e-12) * math.sqrt(len(x))), 3)


def rs(tr):
    """Point and R metrics. pf_R = sum of winning R / sum of losing R (what a fixed-risk trader experiences)."""
    if tr is None or len(tr) == 0:
        return {"n": 0}
    s = stats(tr)
    R = tr["R"].values
    gw, gl = R[R > 0].sum(), -R[R < 0].sum()
    s.update({"sum_pts": round(float(tr["pnl"].sum()), 1), "t_pts": tstat(tr["pnl"]), "t_R": tstat(R),
              "pf_R": round(float(gw / gl), 3) if gl > 0 else None,
              "med_risk": round(float(tr["risk"].median()), 2)})
    return s


def cut(ords):
    ords = [o for o in ords if o["t"] < TEST_MS]
    assert all(o["t"] < TEST_MS for o in ords)
    return ords


def sim(ords, cost):
    tr = simulate(cut(ords), COSTS[cost] if isinstance(cost, str) else cost)
    if len(tr):
        assert tr["t_in"].max() < TEST_MS
    return tr


def by_split(tr, sides=True):
    out = {}
    for sp, nm in ((0, "train"), (1, "valid")):
        t = tr[tr.split == sp] if len(tr) else tr
        out[nm] = rs(t)
        if sides:
            out[nm + "_long"] = rs(t[t.d > 0]) if len(t) else {"n": 0}
            out[nm + "_short"] = rs(t[t.d < 0]) if len(t) else {"n": 0}
    return out


def pv(null, actual):
    """One-sided p: share of null draws >= actual (with +1 smoothing)."""
    x = np.asarray(null, dtype=float)
    x = x[np.isfinite(x)]
    return round(float(((x >= actual).sum() + 1) / (len(x) + 1)), 4)


def short(d, keys=("n", "pf", "avg_pts", "sum_R", "avg_R", "t_R", "pf_R")):
    return {k: d.get(k) for k in keys}


# ---------------------------------------------------------------------------------------------------------------
# A. causality

def indep_orders(m1, pad=0.25, tpm=1.0, flat_utc=780, brk_h=120, nb_min=30):
    """Independent re-implementation of the finalist (kap 0, at-break, far stop, 1R, flat 13:00 UTC) from raw M1.
    Uses only: lon_mod / mod / tday columns, mid OHLC, and a Wilder ATR computed here with pandas directly."""
    n = int(np.searchsorted(m1["ts"].values, TEST_MS, side="left"))
    ts = m1["ts"].values[:n].astype(np.int64)
    h, l, c = (m1[k].values[:n].astype(np.float64) for k in "hlc")
    lon = m1["lon_mod"].values[:n].astype(np.int64)
    mod = m1["mod"].values[:n].astype(np.int64)
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    A = pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().values
    dcode, _ = pd.factorize(m1["tday"].values[:n], sort=True)
    assert (np.diff(dcode) >= 0).all(), "tday not contiguous"
    starts = np.r_[0, np.flatnonzero(np.diff(dcode)) + 1]
    ends = np.r_[starts[1:], n]
    cal = load_calendar()
    ev = np.sort(cal.loc[cal["impact"] == "HIGH", "ts"].values.astype(np.int64))
    out = []
    for a, b in zip(starts, ends):
        idx = np.arange(a, b)
        w = idx[(lon[a:b] >= 480) & (lon[a:b] < 510)]
        if len(w) < 24:
            continue
        hi, lo, e = h[w].max(), l[w].min(), w[-1]
        if hi - lo <= 0:
            continue
        br = idx[(lon[a:b] >= 510) & (lon[a:b] < 510 + brk_h) & (idx > e)]
        if len(br) == 0:
            continue
        i0, d = None, 0
        for i in br:                                  # first close outside the range, either side
            if c[i] > hi:
                i0, d = i, 1
                break
            if c[i] < lo:
                i0, d = i, -1
                break
        if i0 is None:
            continue
        t = int(ts[i0]) + 60_000
        fl_c = idx[(mod[a:b] >= flat_utc) & (idx > e)]
        flat = int(ts[fl_c[0]]) if len(fl_c) else int(ts[b - 1]) + 60_000
        if t >= flat:
            continue
        k = np.searchsorted(ev, t - nb_min * 60_000, side="left")
        if k < len(ev) and ev[k] <= t + nb_min * 60_000:
            continue
        sl = lo - pad * A[i0] if d > 0 else hi + pad * A[i0]
        risk = (c[i0] - sl) * d
        if risk <= 0:
            continue
        out.append({"t": t, "d": d, "sl": float(sl), "tp": float(c[i0] + d * tpm * risk), "flat": flat,
                    "or_end_ts": int(ts[e]), "dec_lon_mod": int(lon[i0])})
    return out


def cmp_orders(A_, B_, tol=1e-6):
    """Match by t; count missing/extra and field mismatches (d, sl, tp, flat)."""
    a = {o["t"]: o for o in A_}
    b = {o["t"]: o for o in B_}
    common = sorted(set(a) & set(b))
    mm = {"d": 0, "sl": 0, "tp": 0, "flat": 0}
    ex = []
    for t in common:
        for f in mm:
            x, y = a[t].get(f), b[t].get(f)
            if f == "d":
                bad = int(x) != int(y)
            else:
                bad = not (x is not None and y is not None and abs(float(x) - float(y)) <= tol)
            if bad:
                mm[f] += 1
                if len(ex) < 5:
                    ex.append({"t": t, "field": f, "a": x, "b": y})
    return {"n_a": len(a), "n_b": len(b), "common": len(common), "only_a": len(set(a) - set(b)),
            "only_b": len(set(b) - set(a)), "mismatch": mm, "examples": ex}


def part_caus(m1):
    full = cut(M.orders(m1, **P))
    RES.setdefault("caus", {})
    # 1) independent re-implementation
    ind = indep_orders(m1)
    RES["caus"]["indep_vs_module"] = cmp_orders(full, ind)
    RES["caus"]["decision_lon_mod_range"] = [min(o["dec_lon_mod"] for o in ind), max(o["dec_lon_mod"] for o in ind)]
    RES["caus"]["min_gap_decision_minus_or_end_s"] = min((o["t"] - 60_000 - o["or_end_ts"]) / 1000 for o in ind)
    log("indep " + json.dumps(RES["caus"]["indep_vs_module"]))
    save()
    # 2) truncation invariance at 6 cut times (London local), 3 TRAIN / 3 VALID, several inside 08:00-13:00 London
    tl = pd.to_datetime([o["t"] for o in full], unit="ms", utc=True).tz_convert("Europe/London")
    first_sig = {}
    for o, dt in zip(full, tl):
        first_sig.setdefault(dt.strftime("%Y-%m"), (o, dt))

    def lon_ms(dt, hhmm):
        hh, mn = hhmm
        x = pd.Timestamp(dt.strftime("%Y-%m-%d") + f" {hh:02d}:{mn:02d}", tz="Europe/London")
        return int(x.tz_convert("UTC").value // 1_000_000)

    cuts = []
    o, dt = first_sig["2025-04"]; cuts.append(("2025-04 08:15 London (inside OR window)", lon_ms(dt, (8, 15))))
    o, dt = first_sig["2025-06"]; cuts.append(("2025-06 08:31 London (1 bar into break search)", lon_ms(dt, (8, 31))))
    o, dt = first_sig["2025-09"]; cuts.append(("2025-09 signal t + 6 min", o["t"] + 360_000))
    o, dt = first_sig["2026-02"]; cuts.append(("2026-02 11:30 London (during hold)", lon_ms(dt, (11, 30))))
    o, dt = first_sig["2026-04"]; cuts.append(("2026-04 12:45 London (before flat)", lon_ms(dt, (12, 45))))
    o, dt = first_sig["2026-05"]; cuts.append(("2026-05 signal t + 5 min (boundary)", o["t"] + 300_000))
    tr_rows = []
    for lab, T in cuts:
        assert T < TEST_MS
        mt = m1[m1["ts"] < T].reset_index(drop=True)
        qt = M.orders(mt, **P)
        del mt
        F = [x for x in full if x["t"] <= T - 300_000]
        Q = [x for x in qt if x["t"] <= T - 300_000]
        r = cmp_orders(F, Q)
        # stronger: every truncated order (t <= T) must equal the full-data order at the same t on d/sl/tp
        Fall = [x for x in full if x["t"] <= T]
        r_all = cmp_orders(Fall, qt)
        # flat mismatches are expected only when the full-data flat lies beyond the cut (the truncated day ends at T)
        fl_bad_informative = sum(1 for x in Q for y in F if y["t"] == x["t"] and y["flat"] <= T - 60_000
                                 and y["flat"] != x["flat"])
        tr_rows.append({"cut": lab, "T_utc": str(pd.Timestamp(T, unit="ms", tz="UTC")), "cmp_t_le_T-5min": r,
                        "cmp_all_trunc_orders": r_all, "flat_mismatch_with_flat_before_cut": fl_bad_informative,
                        "last_trunc_order_utc": str(pd.Timestamp(max(x["t"] for x in qt), unit="ms", tz="UTC"))})
        log(f"trunc {lab}: " + json.dumps({"n": r["common"], "only_full": r["only_a"], "only_trunc": r["only_b"],
                                             "mm": r["mismatch"], "all_mm": r_all["mismatch"],
                                             "all_only_trunc": r_all["only_b"], "all_only_full": r_all["only_a"]}))
    RES["caus"]["truncation"] = tr_rows
    M._C.clear()
    save()


# ---------------------------------------------------------------------------------------------------------------
# B. base evaluation + R metrics

def monthly(tr):
    mon = pd.to_datetime(tr["t_in"], unit="ms", utc=True).dt.strftime("%Y-%m")
    rows = []
    for mname, g in tr.groupby(mon):
        rows.append({"month": mname, "sp": "T" if g.split.iloc[0] == 0 else "V", "n": len(g),
                     "L": int((g.d > 0).sum()), "S": int((g.d < 0).sum()), "sum_R": round(float(g.R.sum()), 2),
                     "R_L": round(float(g.R[g.d > 0].sum()), 2), "R_S": round(float(g.R[g.d < 0].sum()), 2),
                     "pts": round(float(g.pnl.sum()), 1), "pf": stats(g).get("pf"),
                     "med_risk": round(float(g.risk.median()), 1)})
    return rows, mon


def part_base(m1):
    RES.setdefault("base", {})
    for cost in ("lf_base", "lf_harsh", "mid"):
        ev, tr = evaluate(NAME, P, cost=cost, return_trades=True)
        assert len(tr) == 0 or tr["t_in"].max() < TEST_MS
        RES["base"][cost] = by_split(tr)
        RES["base"][cost]["engine_valid_pf"] = ev["valid"].get("pf")
        log(f"{cost}: TRAIN {short(RES['base'][cost]['train'])} | VALID {short(RES['base'][cost]['valid'])}")
        if cost == "lf_base":
            base = tr
    save()
    # monthly R, drop-best-month (by pts and by R), risk quartiles, bootstrap
    rows, mon = monthly(base)
    RES["monthly_lf_base"] = rows
    dbm = {}
    for sp, nm in ((0, "train"), (1, "valid")):
        t = base[base.split == sp]
        mm = mon[base.split == sp]
        bp = t.groupby(mm)["pnl"].sum().idxmax()
        bR = t.groupby(mm)["R"].sum().idxmax()
        dbm[nm] = {"best_month_pts": bp, "pf_without_best_pts": stats(t[mm != bp]).get("pf"),
                   "sumR_without_best_pts": round(float(t.R[mm != bp].sum()), 2),
                   "best_month_R": bR, "pf_without_best_R": stats(t[mm != bR]).get("pf"),
                   "sumR_without_best_R": round(float(t.R[mm != bR].sum()), 2),
                   "months_pos_R": int((t.groupby(mm)["R"].sum() > 0).sum()),
                   "months_pos_pts": int((t.groupby(mm)["pnl"].sum() > 0).sum()), "months": int(mm.nunique())}
    RES["drop_best_month"] = dbm
    v = base[base.split == 1]
    q = pd.qcut(v["risk"], 4, labels=False)
    RES["valid_by_risk_quartile"] = [{"q": int(k), "risk_range": [round(float(g.risk.min()), 1),
                                                                   round(float(g.risk.max()), 1)],
                                      "n": len(g), "sum_R": round(float(g.R.sum()), 2),
                                      "pts": round(float(g.pnl.sum()), 1)} for k, g in v.groupby(q)]
    tq = base[base.split == 0]
    q0 = pd.qcut(tq["risk"], 4, labels=False)
    RES["train_by_risk_quartile"] = [{"q": int(k), "risk_range": [round(float(g.risk.min()), 1),
                                                                   round(float(g.risk.max()), 1)],
                                      "n": len(g), "sum_R": round(float(g.R.sum()), 2),
                                      "pts": round(float(g.pnl.sum()), 1)} for k, g in tq.groupby(q0)]
    rng = np.random.default_rng(12345)
    boot = {}
    for sp, nm in ((0, "train"), (1, "valid")):
        t = base[base.split == sp]
        pn, Rv = t["pnl"].values, t["R"].values
        pfs, avs, ars = [], [], []
        for _ in range(5000):
            k = rng.integers(0, len(pn), len(pn))
            x = pn[k]
            gl = -x[x < 0].sum()
            pfs.append(x[x > 0].sum() / gl if gl > 0 else np.inf)
            avs.append(x.mean())
            ars.append(Rv[k].mean())
        pfs, avs, ars = map(np.asarray, (pfs, avs, ars))
        boot[nm] = {"pf_ci90": [round(float(np.quantile(pfs, a)), 3) for a in (0.05, 0.95)],
                    "P_pf_le_1": round(float((pfs <= 1).mean()), 4),
                    "avg_pts_ci90": [round(float(np.quantile(avs, a)), 3) for a in (0.05, 0.95)],
                    "P_avg_pts_le_0": round(float((avs <= 0).mean()), 4),
                    "avg_R_ci90": [round(float(np.quantile(ars, a)), 3) for a in (0.05, 0.95)],
                    "P_avg_R_le_0": round(float((ars <= 0).mean()), 4)}
    RES["bootstrap"] = boot
    log("dbm " + json.dumps(dbm))
    log("boot " + json.dumps(boot))
    save()
    return base


# ---------------------------------------------------------------------------------------------------------------
# C. nulls

def part_null(m1, base, rdv, rdt):
    ords = cut(M.orders(m1, **P))
    so = {0: [o for o in ords if o["t"] < VALID_MS], 1: [o for o in ords if VALID_MS <= o["t"] < TEST_MS]}
    RES["null"] = {"n_orders": {"train": len(so[0]), "valid": len(so[1])}}
    for sp, nm, ns in ((1, "valid", rdv), (0, "train", rdt)):
        act = rs(base[base.split == sp])
        rel = _to_relative(so[sp])
        dirs = np.array([o["d"] for o in so[sp]])
        mon = pd.to_datetime([o["t"] for o in so[sp]], unit="ms", utc=True).strftime("%Y-%m").values
        res = {"actual": short(act)}
        # (a) engine random_direction
        nd = random_direction(so[sp], seeds=range(ns), cost="lf_base", split=sp)
        nd = nd[nd["n"] > 0]
        res["random_direction"] = {"draws": int(len(nd)), "p_avg_pts": pv(nd["avg_pts"], act["avg_pts"]),
                                   "p_pf": pv(nd["pf"], act["pf"]), "p_sum_R": pv(nd["sum_R"], act["sum_R"]),
                                   "p_avg_R": pv(nd["avg_R"], act["avg_R"]),
                                   "pf_q50_95": [round(float(nd["pf"].quantile(a)), 3) for a in (0.5, 0.95)],
                                   "avg_pts_q50_95": [round(float(nd["avg_pts"].quantile(a)), 3) for a in (0.5, 0.95)],
                                   "sum_R_q50_95": [round(float(nd["sum_R"].quantile(a)), 2) for a in (0.5, 0.95)]}
        log(f"rd {nm}: " + json.dumps(res["random_direction"]))
        save()
        # (b) permutation keeping the long/short mix; (c) permutation within calendar month (keeps monthly mix)
        for lab, within_month in (("perm_global", False), ("perm_within_month", True)):
            rng = np.random.default_rng(777 + sp + (100 if within_month else 0))
            rows = []
            for s in range(max(200, ns // 2 if sp == 1 else ns)):
                if within_month:
                    dd = dirs.copy()
                    for mname in np.unique(mon):
                        ix = np.flatnonzero(mon == mname)
                        dd[ix] = rng.permutation(dirs[ix])
                else:
                    dd = rng.permutation(dirs)
                tr = simulate(_apply_dir(rel, dd), COSTS["lf_base"])
                tr = tr[tr.split == sp]
                rows.append(stats(tr))
            nd2 = pd.DataFrame(rows)
            res[lab] = {"draws": int(len(nd2)), "p_avg_pts": pv(nd2["avg_pts"], act["avg_pts"]),
                        "p_pf": pv(nd2["pf"], act["pf"]), "p_sum_R": pv(nd2["sum_R"], act["sum_R"]),
                        "pf_q50_95": [round(float(nd2["pf"].quantile(a)), 3) for a in (0.5, 0.95)],
                        "sum_R_q50_95": [round(float(nd2["sum_R"].quantile(a)), 2) for a in (0.5, 0.95)]}
            log(f"{lab} {nm}: " + json.dumps(res[lab]))
            save()
        # (d) mirror, always-long, always-short at the same timestamps and geometry
        res["mirror"] = short(rs(simulate(mirror(so[sp]), COSTS["lf_base"])))
        for lab, dv in (("always_long", 1), ("always_short", -1)):
            t_ = simulate(_apply_dir(rel, np.full(len(rel), dv)), COSTS["lf_base"])
            res[lab] = short(rs(t_))
            # the same-day 2x2: forced side on the days the rule chose long / short
            tin = set(o["t"] for o, d in zip(so[sp], dirs) if d > 0)
            res[lab + "_on_long_break_days"] = short(rs(t_[t_.t_sig.isin(tin)]))
            res[lab + "_on_short_break_days"] = short(rs(t_[~t_.t_sig.isin(tin)]))
        res["actual_long"] = short(rs(base[(base.split == sp) & (base.d > 0)]))
        res["actual_short"] = short(rs(base[(base.split == sp) & (base.d < 0)]))
        RES["null"][nm] = res
        log(f"mirror/naive {nm}: " + json.dumps({k: res[k] for k in ("mirror", "always_long", "always_short")}))
        save()


# ---------------------------------------------------------------------------------------------------------------
# stress

def part_stress(m1):
    ords = cut(M.orders(m1, **P))
    RES["stress"] = {}
    for lab, dms in (("delay+5s", 5_000), ("delay+10s", 10_000), ("delay+30s", 30_000), ("delay+60s", 60_000)):
        od = [{**o, "t": o["t"] + dms} for o in ords]
        RES["stress"][lab] = by_split(sim(od, "lf_base"), sides=False)
        log(f"{lab}: " + json.dumps({k: short(v) for k, v in RES["stress"][lab].items()}))
    save()
    lb = COSTS["lf_base"]
    slip = Cost(spread_mult=lb.spread_mult, spread_floor=lb.spread_floor, slip_entry=lb.slip_entry + 0.3,
                slip_exit=lb.slip_exit + 0.3, com_rt_lot=lb.com_rt_lot, hour_add=_H_BASE)
    spr = Cost(spread_add=0.6, spread_mult=lb.spread_mult, spread_floor=lb.spread_floor, slip_entry=lb.slip_entry,
               slip_exit=lb.slip_exit, com_rt_lot=lb.com_rt_lot, hour_add=_H_BASE)
    RES["stress"]["slip+0.3/side"] = by_split(sim(ords, slip), sides=False)
    od5 = [{**o, "t": o["t"] + 5_000} for o in ords]
    RES["stress"]["slip+0.3/side & delay+5s"] = by_split(sim(od5, slip), sides=False)
    RES["stress"]["spread+0.6 (0.3/side every fill)"] = by_split(sim(ords, spr), sides=False)
    for k in ("slip+0.3/side", "slip+0.3/side & delay+5s", "spread+0.6 (0.3/side every fill)"):
        log(f"{k}: " + json.dumps({s: short(v) for s, v in RES["stress"][k].items()}))
    save()


# ---------------------------------------------------------------------------------------------------------------
# neighbours

def part_nbr():
    M.WINDOWS["lon15"] = dict(clock="lon", start=480, end=495, flat=("utc", 960))     # 08:00-08:14 OR
    M.WINDOWS["lon45"] = dict(clock="lon", start=480, end=525, flat=("utc", 960))     # 08:00-08:44 OR
    moves = [("kap", 0.05), ("kap", 0.1), ("pad", 0.1), ("pad", 0.4), ("tp", 0.75), ("tp", 1.25),
             ("flat_utc", 720), ("flat_utc", 840), ("brk_h", 90), ("brk_h", 150), ("window", "lon15"),
             ("window", "lon45")]
    rows = []
    for k, v in moves:
        p = {**P, k: v}
        ev, tr = evaluate(NAME, p, cost="lf_base", return_trades=True)
        assert len(tr) == 0 or tr["t_in"].max() < TEST_MS
        b = by_split(tr)
        rows.append({"move": f"{k}={v}", "train": short(b["train"]), "valid": short(b["valid"]),
                     "valid_long_sumR": b["valid_long"].get("sum_R"), "valid_short_sumR": b["valid_short"].get("sum_R"),
                     "train_long_sumR": b["train_long"].get("sum_R"), "train_short_sumR": b["train_short"].get("sum_R")})
        log(f"nbr {k}={v}: TRAIN {short(b['train'])} VALID {short(b['valid'])}")
    vp = [r["valid"]["pf"] for r in rows]
    vr = [r["valid"]["sum_R"] for r in rows]
    RES["neighbours"] = {"rows": rows, "median_valid_pf": float(np.median(vp)),
                         "median_valid_sum_R": float(np.median(vr)),
                         "n_valid_sumR_pos": int(sum(1 for x in vr if x > 0)), "n": len(rows)}
    log("nbr median VALID PF %.3f, median VALID sum R %.2f" % (np.median(vp), np.median(vr)))
    save()


# ---------------------------------------------------------------------------------------------------------------
# multiple testing

def part_mt():
    R_ = ROOT / "cand/results"
    s1 = pd.read_csv(R_ / "orb_retest_train.csv")
    s2 = pd.read_csv(R_ / "orb_retest_s2_train.csv")
    key = ["tr_n", "tr_pf", "tr_avg_pts", "tr_sum_R"]
    allr = pd.concat([s1[key + ["tr_t"]], s2[key + ["tr_t"]]])
    rank = allr[allr.tr_n >= 60]
    dist = rank.drop_duplicates(key)
    t_fin = 1.83
    p1 = 1 - ND.cdf(t_fin)

    def emax(N):
        g = 0.5772156649
        return (1 - g) * ND.inv_cdf(1 - 1.0 / N) + g * ND.inv_cdf(1 - 1.0 / (N * math.e))

    out = {"N_tried": int(len(allr)), "N_rankable": int(len(rank)), "N_distinct_rankable": int(len(dist)),
           "train_t_finalist": t_fin, "p_one_sided_raw": round(p1, 4),
           "cross_config_t": {"median": round(float(dist.tr_t.median()), 2), "sd": round(float(dist.tr_t.std()), 2),
                              "max": round(float(dist.tr_t.max()), 2)}}
    rows = {}
    for N in (5, 10, 30, len(dist), len(allr)):
        ps = 1 - (1 - p1) ** N
        rows[f"N={N}"] = {"E_max_t_iid_null": round(emax(N), 2), "p_sidak": round(ps, 4),
                          "haircut_t": round(ND.inv_cdf(1 - min(ps, 0.9999)), 2) if ps < 1 else None}
    out["sidak"] = rows
    RES["multiple_testing"] = out
    log("mt " + json.dumps(out))
    save()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", default="caus,base,null,stress,nbr,mt")
    ap.add_argument("--rdv", type=int, default=1000)
    ap.add_argument("--rdt", type=int, default=300)
    a = ap.parse_args()
    parts = a.parts.split(",")
    if "mt" in parts:
        part_mt()
    if set(parts) - {"mt"}:
        m1 = load_m1()
        log(f"m1 loaded {len(m1)}")
        base = None
        if "caus" in parts:
            part_caus(m1)
        if "base" in parts or "null" in parts:
            base = part_base(m1)
        if "null" in parts:
            part_null(m1, base, a.rdv, a.rdt)
        if "nbr" in parts:
            part_nbr()
        if "stress" in parts:
            part_stress(m1)
    log("done")


if __name__ == "__main__":
    main()
