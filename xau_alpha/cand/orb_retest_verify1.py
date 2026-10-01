"""
cand/orb_retest_verify1.py - adversarial verifier #1 (statistical robustness lens) for the frozen H6 finalist.
Never TEST: every order list is cut at 2026-06-01 (asserted), final=True is never used.

    python3 cand/orb_retest_verify1.py [--rdv 1000] [--rdt 300]

Writes cand/results/orb_retest_verify1.json (small, no trade lists). One process.
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "cand"))

from data import _ms, load_m1  # noqa: E402
from nulltest import mirror, random_direction  # noqa: E402
from sim import COSTS, Cost, _H_BASE, simulate, stats  # noqa: E402

import orb_retest as M  # noqa: E402

TEST_MS = _ms("2026-06-01")
VALID_MS = _ms("2026-01-01")
P = {"window": "lon", "filt": 0, "kap": 0.0, "entry": "brk", "stop": "far", "pad": 0.25, "tp": 1.0, "flat_utc": 780,
     "be": 0.0, "tmax": 0, "brk_h": 120, "nb": 30, "nexit": -1}
OUT = ROOT / "cand/results/orb_retest_verify1.json"
T0 = time.time()
RES: dict = {"params": P}


def log(s):
    print(f"[{time.time() - T0:6.0f}s] {s}", flush=True)


def save():
    with open(OUT, "w") as f:
        json.dump(RES, f, indent=1, default=lambda x: x.item() if hasattr(x, "item") else str(x))


def tst(x):
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 2:
        return None
    return round(float(x.mean() / (x.std(ddof=1) + 1e-12) * math.sqrt(len(x))), 3)


def st(tr):
    if tr is None or len(tr) == 0:
        return {"n": 0}
    s = stats(tr)
    s["t_R"] = tst(tr["R"])
    s["t_pts"] = tst(tr["pnl"])
    return s


def cut(ords):
    ords = [o for o in ords if o["t"] < TEST_MS]
    assert all(o["t"] < TEST_MS for o in ords)
    return ords


def sim(ords, cost):
    ords = cut(ords)
    tr = simulate(ords, COSTS[cost] if isinstance(cost, str) else cost)
    if len(tr):
        assert tr["t_in"].max() < TEST_MS and tr["t_out"].max() <= TEST_MS
    return tr


def by_split(tr):
    out = {}
    for sp, nm in ((0, "train"), (1, "valid")):
        t = tr[tr.split == sp] if len(tr) else tr
        out[nm] = st(t)
        out[nm + "_long"] = st(t[t.d > 0]) if len(t) else {"n": 0}
        out[nm + "_short"] = st(t[t.d < 0]) if len(t) else {"n": 0}
    return out


def pv(null, actual):
    x = np.asarray(null, dtype=float)
    x = x[np.isfinite(x)]
    return round(float(((x >= actual).sum() + 1) / (len(x) + 1)), 4)


def main(rdv, rdt):
    m1 = load_m1()
    log(f"m1 loaded {len(m1)}")
    ords = cut(M.orders(m1, **P))
    so = {0: [o for o in ords if o["t"] < VALID_MS], 1: [o for o in ords if VALID_MS <= o["t"] < TEST_MS]}
    RES["n_orders"] = {"train": len(so[0]), "valid": len(so[1])}

    # ================= lf_base block (one broker-array build) =================
    base = sim(ords, "lf_base")
    RES["lf_base"] = by_split(base)
    log("lf_base " + json.dumps({k: RES["lf_base"][k] for k in ("train", "valid")}))

    # (4c) extra entry delay: shift every order t by +5 s (and +15 s, +30 s for context)
    RES["delay"] = {}
    for dms in (5_000, 15_000, 30_000):
        od = [{**o, "t": o["t"] + dms} for o in ords]
        RES["delay"][f"+{dms // 1000}s"] = by_split(sim(od, "lf_base"))
    log("delay " + json.dumps({k: {s: (v[s]["n"], v[s]["pf"], v[s]["avg_pts"], v[s]["sum_R"]) for s in ("train", "valid")}
                               for k, v in RES["delay"].items()}))
    save()

    # (1b) mirror
    RES["mirror"] = {}
    for sp, nm in ((0, "train"), (1, "valid")):
        RES["mirror"][nm] = st(simulate(mirror(so[sp]), COSTS["lf_base"]))
    log("mirror " + json.dumps(RES["mirror"]))

    # (1a) random direction, VALID (rdv seeds) and TRAIN (rdt seeds)
    for sp, nm, ns in ((1, "valid", rdv), (0, "train", rdt)):
        nd = random_direction(so[sp], seeds=range(ns), cost="lf_base")
        nd = nd[nd["n"] > 0]
        act = st(base[base.split == sp])
        RES[f"rd_{nm}"] = {
            "seeds": int(len(nd)), "actual": {k: act[k] for k in ("n", "pf", "avg_pts", "sum_R", "avg_R")},
            "p_avg_pts": pv(nd["avg_pts"], act["avg_pts"]), "p_pf": pv(nd["pf"], act["pf"]),
            "p_sum_R": pv(nd["sum_R"], act["sum_R"]),
            "pf_q50_90_95_99": [round(float(nd["pf"].quantile(q)), 3) for q in (0.5, 0.9, 0.95, 0.99)],
            "avg_q50_95": [round(float(nd["avg_pts"].quantile(q)), 3) for q in (0.5, 0.95)],
            "sumR_q50_95": [round(float(nd["sum_R"].quantile(q)), 2) for q in (0.5, 0.95)],
        }
        log(f"rd {nm} " + json.dumps(RES[f"rd_{nm}"]))
        save()

    # bootstrap of VALID trades (iid by trade; ~1 trade/day)
    v = base[base.split == 1]
    rng = np.random.default_rng(11)
    pn = v["pnl"].values
    Rv = v["R"].values
    pfs, avs, ars = [], [], []
    for _ in range(10_000):
        k = rng.integers(0, len(pn), len(pn))
        x = pn[k]
        gl = -x[x < 0].sum()
        pfs.append(x[x > 0].sum() / gl if gl > 0 else np.inf)
        avs.append(x.mean())
        ars.append(Rv[k].mean())
    pfs, avs, ars = map(np.asarray, (pfs, avs, ars))
    RES["boot_valid"] = {"pf_ci90": [round(float(np.quantile(pfs, q)), 3) for q in (0.05, 0.95)],
                         "p_pf_le_1": round(float((pfs <= 1).mean()), 4),
                         "p_pf_lt_1.15": round(float((pfs < 1.15).mean()), 4),
                         "avg_ci90": [round(float(np.quantile(avs, q)), 3) for q in (0.05, 0.95)],
                         "p_avg_le_0": round(float((avs <= 0).mean()), 4),
                         "avgR_ci90": [round(float(np.quantile(ars, q)), 3) for q in (0.05, 0.95)]}
    log("boot " + json.dumps(RES["boot_valid"]))

    # (3) monthly R table TRAIN+VALID (lf_base) and drop-best-month
    mon = pd.to_datetime(base["t_in"], unit="ms", utc=True).dt.strftime("%Y-%m")
    rows = []
    for mname, g in base.groupby(mon):
        rows.append({"month": mname, "split": "T" if g.split.iloc[0] == 0 else "V", "n": len(g),
                     "L": int((g.d > 0).sum()), "S": int((g.d < 0).sum()), "sum_R": round(float(g.R.sum()), 2),
                     "R_long": round(float(g.R[g.d > 0].sum()), 2), "R_short": round(float(g.R[g.d < 0].sum()), 2),
                     "sum_pts": round(float(g.pnl.sum()), 1), "pf": stats(g).get("pf"),
                     "avg_risk": round(float(g.risk.mean()), 1)})
    RES["monthly"] = rows
    dbm = {}
    for sp, nm in ((0, "train"), (1, "valid")):
        t = base[base.split == sp]
        mm = mon[base.split == sp]
        best_pts = t.groupby(mm)["pnl"].sum().idxmax()
        best_R = t.groupby(mm)["R"].sum().idxmax()
        dbm[nm] = {"best_month_pts": best_pts, "pf_without": stats(t[mm != best_pts]).get("pf"),
                   "avg_pts_without": stats(t[mm != best_pts]).get("avg_pts"),
                   "best_month_R": best_R, "sumR_without_bestR": round(float(t.R[mm != best_R].sum()), 2),
                   "months_pos_R": int((t.groupby(mm)["R"].sum() > 0).sum()), "months": int(mm.nunique())}
    RES["drop_best_month"] = dbm
    log("dbm " + json.dumps(dbm))
    # R by risk quartile, VALID
    q = np.quantile(v["risk"], [0.25, 0.5, 0.75])
    bins = np.digitize(v["risk"], q)
    RES["valid_by_risk_quartile"] = [{"q": int(b), "n": int((bins == b).sum()), "sum_R": round(float(v.R[bins == b].sum()), 2),
                                      "sum_pts": round(float(v.pnl[bins == b].sum()), 1)} for b in range(4)]
    save()

    # (5) long-beta benchmark: naive buy/sell at 08:30 London on the SAME signal days, exit 16:00 UTC (and 13:00)
    sig = M.signals(m1, **P)
    sig = sig[sig.t < TEST_MS]
    tr_days = base.merge(sig[["t", "day", "risk"]].rename(columns={"t": "t_sig", "day": "dcode", "risk": "risk_sig"}),
                         on="t_sig")
    assert len(tr_days) == len(base)
    b = M._base(m1)
    bench = {}
    for flat_utc in (960, 780):
        T = M._windows_table(b, "lon", 120, flat_utc=flat_utc).set_index("day")
        dd = tr_days["dcode"].values
        t_dec = (b["ts"][T.loc[dd, "e"].values] + 60_000).astype(np.int64)
        fl = T.loc[dd, "flat"].values.astype(np.int64)
        for dname, dv in (("buy", 1), ("sell", -1)):
            bo = [{"t": int(a), "d": dv, "kind": "mkt", "sl_dist": 1e4, "flat": int(f), "tag": "bench"}
                  for a, f in zip(t_dec, fl)]
            bt = sim(bo, "lf_base")
            bt = bt.merge(pd.DataFrame({"t_sig": t_dec, "brk_d": tr_days["d"].values,
                                        "risk_s": tr_days["risk"].values}), on="t_sig", how="left")
            bt["Rs"] = bt["pnl"] / bt["risk_s"]
            key = f"{dname}_0830L_flat{flat_utc // 60:02d}utc"
            bench[key] = {}
            for sp, nm in ((0, "train"), (1, "valid")):
                x = bt[bt.split == sp]
                bench[key][nm] = {**{k: v_ for k, v_ in stats(x).items() if k in ("n", "pf", "avg_pts")},
                                  "sum_R_stratrisk": round(float(x.Rs.sum()), 1),
                                  "on_longbreak_days": {**{k: v_ for k, v_ in stats(x[x.brk_d > 0]).items()
                                                           if k in ("n", "pf", "avg_pts")},
                                                        "sum_R_stratrisk": round(float(x.Rs[x.brk_d > 0].sum()), 1)},
                                  "on_shortbreak_days": {**{k: v_ for k, v_ in stats(x[x.brk_d < 0]).items()
                                                            if k in ("n", "pf", "avg_pts")},
                                                         "sum_R_stratrisk": round(float(x.Rs[x.brk_d < 0].sum()), 1)}}
    RES["beta_bench"] = bench
    log("bench " + json.dumps(bench))
    save()

    # (2) parameter neighbourhood: 12 nearest configs (single-parameter moves incl. OR-window length)
    M.WINDOWS["lon15"] = dict(clock="lon", start=480, end=495, flat=("utc", 960))
    M.WINDOWS["lon45"] = dict(clock="lon", start=480, end=525, flat=("utc", 960))
    moves = [{"pad": 0.1}, {"pad": 0.4}, {"kap": 0.05}, {"kap": 0.1}, {"tp": 0.75}, {"tp": 1.25},
             {"flat_utc": 720}, {"flat_utc": 840}, {"brk_h": 60}, {"brk_h": 180}, {"window": "lon15"},
             {"window": "lon45"}]
    nb = []
    for mv in moves:
        q_ = {**P, **mv}
        t = sim(M.orders(m1, **q_), "lf_base")
        s = by_split(t)
        nb.append({"move": mv, "tr_n": s["train"]["n"], "tr_pf": s["train"]["pf"], "tr_sum_R": s["train"]["sum_R"],
                   "va_n": s["valid"]["n"], "va_pf": s["valid"]["pf"], "va_avg": s["valid"]["avg_pts"],
                   "va_sum_R": s["valid"]["sum_R"], "va_long_R": s["valid_long"].get("sum_R"),
                   "va_short_R": s["valid_short"].get("sum_R")})
        log("nbr " + json.dumps(nb[-1]))
    RES["neighbours"] = nb
    RES["neighbours_valid_pf_median"] = float(np.median([x["va_pf"] for x in nb]))
    RES["neighbours_valid_sumR_median"] = float(np.median([x["va_sum_R"] for x in nb]))
    RES["neighbours_valid_pf_ge_1.15"] = int(sum(x["va_pf"] >= 1.15 for x in nb))
    save()

    # ================= other cost models =================
    extra = {
        "lf_harsh": "lf_harsh",
        "mid": "mid",
        "lf_base_slip+0.3": Cost(spread_mult=0.32, spread_floor=0.22, slip_entry=0.05 + 0.3, slip_exit=0.10 + 0.3,
                                 com_rt_lot=5.0, hour_add=_H_BASE),
        # +0.3 on EVERY fill incl. TP limits: spread +0.6 (floor raised by the same 0.6 so the add is exact)
        "lf_base_spread+0.6": Cost(spread_add=0.6, spread_mult=0.32, spread_floor=0.22 + 0.6, slip_entry=0.05,
                                   slip_exit=0.10, com_rt_lot=5.0, hour_add=_H_BASE),
    }
    RES["costs"] = {}
    for nm, c in extra.items():
        t = sim(ords, c)
        s = by_split(t)
        for sp in ("train", "valid"):
            h = len(t[t.split == (0 if sp == "train" else 1)]) // 2
            tt = t[t.split == (0 if sp == "train" else 1)]
            s[sp]["halves_pf"] = [stats(tt.iloc[:h]).get("pf"), stats(tt.iloc[h:]).get("pf")]
        RES["costs"][nm] = s
        log(f"cost {nm} " + json.dumps({k: s[k] for k in ("train", "valid")}))
        save()
    RES["elapsed_s"] = round(time.time() - T0)
    save()
    log("done")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rdv", type=int, default=1000)
    ap.add_argument("--rdt", type=int, default=300)
    a = ap.parse_args()
    main(a.rdv, a.rdt)
