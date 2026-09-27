"""
cand/rnsb_eval.py - finalist evaluation for H9 (cand/round_reject.py) and H11 (cand/silver_bullet.py).

    python3 cand/rnsb_eval.py <module> '<params json>' [--seeds 50] [--placebo '<json list of overrides>']
                              [--tag name]

HOLDOUT: every order with t >= 2026-06-01 is removed BEFORE simulation; nothing from TEST is simulated.
Reports, for one frozen config:
  * TRAIN / VALID stats under lf_base, lf_harsh, mid (+ t-stat, TRAIN halves, long / short, flip-eligible subset,
    drop-best-month PF);
  * nulltest.random_direction on VALID orders only (lf_base), p = (#seeds with avg_pts >= actual + 1) / (seeds + 1),
    and on TRAIN (20 seeds, information only);
  * nulltest.mirror (all directions reversed);
  * placebo overrides (e.g. placebo levels / hours / entry depths) at lf_base and mid;
  * monthly R table on TRAIN+VALID (lf_base).
Writes cand/results/<tag>_final.json and <tag>_monthly.csv (small files; no trade lists).
"""
import argparse
import importlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "cand"))

from data import TEST, VALID, _ms, load_m1  # noqa: E402
from nulltest import mirror, random_direction  # noqa: E402
from sim import COSTS, simulate, stats  # noqa: E402

TEST_MS, VALID_MS = _ms(TEST[0]), _ms(VALID[0])
FLIP = (1.2, 4.0)


def tstat(tr):
    if len(tr) < 2:
        return None
    r = tr["R"].values
    return round(float(r.mean() / (r.std(ddof=1) + 1e-12) * np.sqrt(len(r))), 2)


def block(tr):
    """stats + t for one subset."""
    st = stats(tr)
    if st.get("n", 0):
        st["t"] = tstat(tr)
    return st


def full_stats(tr):
    out = {}
    for sp, nm in ((0, "train"), (1, "valid")):
        s = tr[tr.split == sp] if len(tr) else tr
        out[nm] = block(s)
        if not len(s):
            continue
        h = len(s) // 2
        out[nm]["pf_h1"] = stats(s.iloc[:h]).get("pf")
        out[nm]["pf_h2"] = stats(s.iloc[h:]).get("pf")
        out[f"{nm}_long"] = block(s[s.d == 1])
        out[f"{nm}_short"] = block(s[s.d == -1])
        fe = s[(s.risk >= FLIP[0]) & (s.risk <= FLIP[1])]
        out[f"{nm}_flip_eligible"] = block(fe)
        out[f"{nm}_flip_share"] = round(len(fe) / len(s), 3)
        mon = pd.to_datetime(s["t_in"], unit="ms", utc=True).dt.strftime("%Y-%m")
        best = s.groupby(mon)["pnl"].sum().idxmax()
        out[f"{nm}_drop_best_month"] = {"month": best, **stats(s[mon != best])}
    return out


def monthly(tr):
    mon = pd.to_datetime(tr["t_in"], unit="ms", utc=True).dt.strftime("%Y-%m")
    g = tr.groupby(mon)
    df = pd.DataFrame({"n": g.size(), "sum_R": g["R"].sum().round(2), "avg_R": g["R"].mean().round(3),
                       "sum_pts": g["pnl"].sum().round(2),
                       "pf": g["pnl"].apply(lambda p: round(p[p > 0].sum() / -p[p < 0].sum(), 2)
                                            if (p < 0).any() else float("inf")),
                       "avg_risk": g["risk"].mean().round(2)})
    df.index.name = "month"
    return df


def compact(st):
    keys = ("n", "pf", "avg_pts", "avg_R", "wr", "t", "avg_risk")
    return {k: st.get(k) for k in keys if k in st}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("module")
    ap.add_argument("params")
    ap.add_argument("--seeds", type=int, default=50)
    ap.add_argument("--placebo", default="[]")
    ap.add_argument("--tag", default=None)
    a = ap.parse_args()
    t0 = time.time()
    mod = importlib.import_module(a.module)
    params = json.loads(a.params)
    tag = a.tag or a.module
    m1 = load_m1()
    orders = [o for o in mod.orders(m1, **params) if o["t"] < TEST_MS]
    res = {"module": a.module, "params": params, "n_orders": len(orders)}
    trades = {}
    for c in ("base", "harsh", "mid"):
        tr = simulate(orders, COSTS[c])
        trades[c] = tr
        res[c] = full_stats(tr)
        print(f"[{c}] train {compact(res[c]['train'])}\n        valid {compact(res[c]['valid'])}", flush=True)
    mdf = monthly(trades["base"])
    out_dir = ROOT / "cand/results"
    mdf.to_csv(out_dir / f"{tag}_monthly.csv")
    res["monthly_base"] = mdf.reset_index().to_dict(orient="records")
    # reason mix (base)
    tb = trades["base"]
    res["exit_reasons_base"] = {nm: tb[tb.split == sp]["reason"].value_counts().to_dict() for sp, nm in
                                ((0, "train"), (1, "valid"))}
    # random-direction null on VALID orders only, and TRAIN (information)
    va_orders = [o for o in orders if VALID_MS <= o["t"] < TEST_MS]
    tr_orders = [o for o in orders if o["t"] < VALID_MS]
    act_va = res["base"]["valid"]
    if a.seeds and va_orders and act_va.get("n", 0):
        nd = random_direction(va_orders, range(a.seeds), "base", split=1)
        p = (int((nd["avg_pts"] >= act_va["avg_pts"]).sum()) + 1) / (len(nd) + 1)
        res["null_valid"] = {"seeds": a.seeds, "p_avg_pts": round(p, 4),
                             "null_avg_pts_mean": round(float(nd["avg_pts"].mean()), 3),
                             "null_avg_pts_p95": round(float(nd["avg_pts"].quantile(0.95)), 3),
                             "null_pf_median": round(float(nd["pf"].median()), 3),
                             "actual_avg_pts": act_va["avg_pts"], "actual_pf": act_va["pf"]}
        print("[null valid]", res["null_valid"], flush=True)
    act_tr = res["base"]["train"]
    if a.seeds and tr_orders and act_tr.get("n", 0):
        nd = random_direction(tr_orders, range(20), "base", split=0)
        p = (int((nd["avg_pts"] >= act_tr["avg_pts"]).sum()) + 1) / (len(nd) + 1)
        res["null_train"] = {"seeds": 20, "p_avg_pts": round(p, 4),
                             "null_avg_pts_mean": round(float(nd["avg_pts"].mean()), 3),
                             "null_avg_pts_p95": round(float(nd["avg_pts"].quantile(0.95)), 3)}
        print("[null train]", res["null_train"], flush=True)
    mtr = simulate(mirror(orders), COSTS["base"])
    res["mirror_base"] = {nm: compact(block(mtr[mtr.split == sp])) for sp, nm in ((0, "train"), (1, "valid"))}
    print("[mirror]", res["mirror_base"], flush=True)
    # placebo / variant overrides
    res["placebo"] = []
    for ov in json.loads(a.placebo):
        p2 = {**params, **ov}
        o2 = [o for o in mod.orders(m1, **p2) if o["t"] < TEST_MS]
        row = {"override": ov}
        for c in ("base", "mid"):
            tr = simulate(o2, COSTS[c])
            for sp, nm in ((0, "train"), (1, "valid")):
                row[f"{c}_{nm}"] = compact(block(tr[tr.split == sp] if len(tr) else tr))
        res["placebo"].append(row)
        print("[placebo]", json.dumps(row), flush=True)
    res["seconds"] = round(time.time() - t0, 1)
    with open(out_dir / f"{tag}_final.json", "w") as f:
        json.dump(res, f, indent=1, default=str)
    print(f"wrote {tag}_final.json in {res['seconds']}s")


if __name__ == "__main__":
    main()
