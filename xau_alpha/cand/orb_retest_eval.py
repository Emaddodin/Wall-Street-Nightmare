"""
cand/orb_retest_eval.py - evaluation of a frozen orb_retest config on TRAIN + VALID only (never TEST).

    python3 cand/orb_retest_eval.py '<params json>' [--seeds 50] [--rand 40] [--tag best]

Writes cand/results/orb_retest_<tag>_eval.json and orb_retest_<tag>_monthly.csv (small files; no trade lists).
Single process. Contents:
  * stats at lf_base / lf_harsh / mid / duka_raw, TRAIN and VALID, long/short, flip-eligible subset
  * TRAIN t-stat, TRAIN halves PF, PF with the best month dropped
  * nulltest.random_direction (seeds) on TRAIN and VALID at lf_base, plus mirror
  * random-window null: the same rule on random 30-minute windows (window='rand', matched to the real window's days)
  * entry-mode arms (brk / rt5 / rt30 / nt5 / nt30) at the same parameters, lf_base and mid
  * monthly R table at lf_base
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "cand"))

from data import load_m1  # noqa: E402
from nulltest import mirror, random_direction  # noqa: E402
from sim import stats  # noqa: E402
from sweep import FLIP_STOP, evaluate, run_orders  # noqa: E402

import orb_retest as M  # noqa: E402


def tstat(tr):
    if len(tr) < 2:
        return None
    return round(float(tr["R"].mean() / (tr["R"].std(ddof=1) + 1e-12) * np.sqrt(len(tr))), 2)


def drop_best_month_pf(tr):
    if len(tr) == 0:
        return None
    mon = pd.to_datetime(tr["t_in"], unit="ms", utc=True).dt.strftime("%Y-%m")
    best = tr.groupby(mon)["pnl"].sum().idxmax()
    return stats(tr[mon != best]).get("pf")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("params")
    ap.add_argument("--seeds", type=int, default=50)
    ap.add_argument("--rand", type=int, default=40)
    ap.add_argument("--tag", default="best")
    ap.add_argument("--arms", type=int, default=1)
    a = ap.parse_args()
    p = json.loads(a.params)
    m1 = load_m1()
    out = {"params": p}
    trades = {}
    for cost in ("lf_base", "lf_harsh", "mid", "duka_raw"):
        ev, tr = evaluate("orb_retest", p, cost=cost, return_trades=True)
        trades[cost] = tr
        row = {k: v for k, v in ev.items() if k != "params"}
        for sp, nm in ((0, "train"), (1, "valid")):
            t = tr[tr.split == sp] if len(tr) else tr
            row[f"{nm}_t"] = tstat(t) if len(t) else None
            row[f"{nm}_drop_best_month_pf"] = drop_best_month_pf(t) if len(t) else None
            if len(t):
                row[f"{nm}_exits"] = t["reason"].value_counts().to_dict()
                row[f"{nm}_risk_q"] = [round(float(x), 2) for x in np.percentile(t["risk"], [10, 50, 90])]
                fe = t[(t.risk >= FLIP_STOP[0]) & (t.risk <= FLIP_STOP[1])]
                row[f"{nm}_flip_share"] = round(len(fe) / len(t), 3)
        t0 = tr[tr.split == 0] if len(tr) else tr
        if len(t0) > 1:
            h = len(t0) // 2
            row["train_halves_pf"] = [stats(t0.iloc[:h]).get("pf"), stats(t0.iloc[h:]).get("pf")]
        out[cost] = row
        print(cost, "TRAIN", row.get("train"), "\n      VALID", row.get("valid"), flush=True)

    # monthly table at lf_base
    tr = trades["lf_base"]
    if len(tr):
        mon = pd.to_datetime(tr["t_in"], unit="ms", utc=True).dt.strftime("%Y-%m")
        g = tr.assign(mon=mon, L=(tr.d > 0).astype(int), S=(tr.d < 0).astype(int)).groupby("mon")
        mt = pd.DataFrame({"split": g["split"].first().map({0: "T", 1: "V"}), "n": g.size(), "L": g.L.sum(),
                           "S": g.S.sum(), "sum_R": g.R.sum().round(1), "avg_pts": g.pnl.mean().round(2),
                           "pf": g.apply(lambda x: stats(x).get("pf")), "avg_risk": g.risk.mean().round(2)})
        mt.to_csv(ROOT / f"cand/results/orb_retest_{a.tag}_monthly.csv")
        print(mt.to_string(), flush=True)

    # random-direction null and mirror (lf_base)
    ords = [o for o in M.orders(m1, **p) if o["t"] < M.TEST_MS]
    act = {0: stats(tr[tr.split == 0]), 1: stats(tr[tr.split == 1])}
    for sp, nm in ((0, "train"), (1, "valid")):
        nd = random_direction(ords, seeds=range(a.seeds), cost="lf_base", split=sp)
        nd = nd[nd["n"] > 0]
        out[f"null_rd_{nm}"] = {
            "seeds": int(len(nd)), "pf_med": round(float(nd["pf"].median()), 3),
            "pf_p95": round(float(nd["pf"].quantile(0.95)), 3),
            "avg_med": round(float(nd["avg_pts"].median()), 3),
            "p_avg": round(float(((nd["avg_pts"] >= act[sp].get("avg_pts", -9e9)).sum() + 1) / (len(nd) + 1)), 3),
            "p_pf": round(float(((nd["pf"] >= act[sp].get("pf", -9e9)).sum() + 1) / (len(nd) + 1)), 3)}
        print("null_rd", nm, out[f"null_rd_{nm}"], flush=True)
    mt_ = run_orders(mirror(ords), "lf_base")
    out["mirror"] = {"train": stats(mt_[mt_.split == 0]), "valid": stats(mt_[mt_.split == 1])}
    print("mirror", out["mirror"], flush=True)

    # random-window null: same rule on random 30-minute windows on the matched window's days
    wins = str(p["window"]).split("+")
    if a.rand > 0 and len(wins) == 1 and wins[0] in M.WINDOWS:
        hz = {"lon": 480, "comex": 205, "asia": 540}[wins[0]]
        rows = []
        for s in range(a.rand):
            q = {**p, "window": "rand", "rseed": s, "match": wins[0], "rand_hz": hz}
            for cost in ("lf_base", "mid"):
                t2 = run_orders(M.orders(m1, **q), cost)
                for sp in (0, 1):
                    st = stats(t2[t2.split == sp]) if len(t2) else {"n": 0}
                    rows.append({"seed": s, "cost": cost, "split": sp, **st})
        rw = pd.DataFrame(rows)
        res = {}
        for cost in ("lf_base", "mid"):
            for sp, nm in ((0, "train"), (1, "valid")):
                x = rw[(rw.cost == cost) & (rw.split == sp) & (rw.n > 0)]
                actual = stats(trades[cost][trades[cost].split == sp])
                res[f"{cost}_{nm}"] = {
                    "seeds": int(len(x)), "n_med": float(x["n"].median()),
                    "pf_med": round(float(x["pf"].median()), 3), "pf_p95": round(float(x["pf"].quantile(0.95)), 3),
                    "avg_med": round(float(x["avg_pts"].median()), 3),
                    "actual_pf": actual.get("pf"), "actual_avg": actual.get("avg_pts"),
                    "p_avg": round(float(((x["avg_pts"] >= actual.get("avg_pts", -9e9)).sum() + 1) / (len(x) + 1)), 3)}
        out["null_randwin"] = res
        print("null_randwin", json.dumps(res, indent=1), flush=True)

    # entry-mode arms at the same parameters
    if a.arms:
        arms = {}
        for e in ("brk", "rt5", "rt30", "nt5", "nt30"):
            q = {**p, "entry": e}
            for cost in ("lf_base", "mid"):
                ev = evaluate("orb_retest", q, cost=cost)
                arms[f"{e}_{cost}"] = {"train": ev.get("train"), "valid": ev.get("valid")}
        out["arms"] = arms
        for k, v in arms.items():
            print("arm", k, v["train"].get("n"), v["train"].get("pf"), v["train"].get("avg_pts"), "|",
                  v["valid"].get("n"), v["valid"].get("pf"), v["valid"].get("avg_pts"), flush=True)

    with open(ROOT / f"cand/results/orb_retest_{a.tag}_eval.json", "w") as f:
        json.dump(out, f, indent=1, default=str)


if __name__ == "__main__":
    main()
