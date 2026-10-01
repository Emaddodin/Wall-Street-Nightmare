"""
cand/x2_eval.py - evaluation of a frozen X2 config (htf_breakout / trend_pullback) on TRAIN + VALID only.

    python3 cand/x2_eval.py <module> '<params json>' [--seeds 50] [--tag best] [--nulls 1] [--neighbors '<json list>']

Never touches TEST: every order is generated from TEST-cut arrays (htf_base) and sweep.run_orders drops t >= TEST.
Single process. Writes cand/results/<module>_<tag>_eval.json and <module>_<tag>_monthly.csv (no trade lists).
Contents:
  * TRAIN / VALID stats at lf_base, lf_harsh, mid, duka_raw; long / short; flip-eligible subset (risk 1.2-4.0 $/oz)
  * TRAIN t-stat, TRAIN halves PF, PF with the best month dropped, exit reasons, risk quantiles, hold times
  * long-beta diagnostic (mid): each trade's P&L minus d x (split-average drift per minute) x holding minutes
  * nulls at lf_base on each split: nulltest.random_direction (p = 0.5 per trade) and a direction PERMUTATION null
    (keeps the long/short mix, so it controls for long-beta in a trending sample); p = P(null avg_pts >= real)
  * mirror (all directions reversed)
  * monthly R table at lf_base
  * optional neighbours: the same config with single-parameter changes (VALID PF at lf_base)
"""
import argparse
import importlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "cand"))

from data import load_m1  # noqa: E402
from nulltest import _apply_dir, _to_relative, mirror  # noqa: E402
from sim import COSTS, simulate, stats  # noqa: E402
from sweep import FLIP_STOP, TEST_MS, evaluate, run_orders  # noqa: E402
from data import _ms, VALID  # noqa: E402

VALID_MS = _ms(VALID[0])


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


def drift_per_min(m1):
    out = {}
    for sp in (0, 1):
        s = m1[m1["split"] == sp]
        out[sp] = float((s["c"].values[-1] - s["c"].values[0]) / ((s["ts"].values[-1] - s["ts"].values[0]) / 60_000))
    return out


def null_p(orders, real_avg, seeds, split, perm):
    rel = _to_relative(orders)
    d0 = np.array([o["d"] for o in orders])
    vals = []
    for s in range(seeds):
        rng = np.random.default_rng(5000 + s)
        dirs = rng.permutation(d0) if perm else rng.choice([-1, 1], size=len(orders))
        tr = simulate(_apply_dir(rel, dirs), COSTS["lf_base"])
        tr = tr[tr["split"] == split] if len(tr) else tr
        vals.append(stats(tr).get("avg_pts", 0.0) if len(tr) else 0.0)
    vals = np.array(vals)
    return {"p": round(float((np.sum(vals >= real_avg) + 1) / (len(vals) + 1)), 4),
            "null_mean": round(float(vals.mean()), 3), "null_sd": round(float(vals.std()), 3),
            "null_q95": round(float(np.quantile(vals, 0.95)), 3), "seeds": seeds}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("module")
    ap.add_argument("params")
    ap.add_argument("--seeds", type=int, default=50)
    ap.add_argument("--tag", default="best")
    ap.add_argument("--nulls", type=int, default=1)
    ap.add_argument("--neighbors", default=None)
    a = ap.parse_args()
    p = json.loads(a.params)
    M = importlib.import_module(a.module)
    m1 = load_m1()
    mu = drift_per_min(m1)
    out = {"module": a.module, "params": p, "drift_per_min": mu}
    trades = {}
    for cost in ("lf_base", "lf_harsh", "mid", "duka_raw"):
        ev, tr = evaluate(a.module, p, cost=cost, return_trades=True)
        trades[cost] = tr
        row = {k: v for k, v in ev.items() if k != "params"}
        for sp, nm in ((0, "train"), (1, "valid")):
            t = tr[tr.split == sp] if len(tr) else tr
            if not len(t):
                continue
            row[f"{nm}_t"] = tstat(t)
            row[f"{nm}_drop_best_month_pf"] = drop_best_month_pf(t)
            row[f"{nm}_exits"] = t["reason"].value_counts().to_dict()
            row[f"{nm}_risk_q10_50_90"] = [round(float(x), 2) for x in np.percentile(t["risk"], [10, 50, 90])]
            fe = t[(t.risk >= FLIP_STOP[0]) & (t.risk <= FLIP_STOP[1])]
            row[f"{nm}_flip_share"] = round(len(fe) / len(t), 3)
            hold = (t["t_out"] - t["t_in"]) / 60_000
            row[f"{nm}_hold_min_q10_50_90"] = [round(float(x), 1) for x in np.percentile(hold, [10, 50, 90])]
            row[f"{nm}_long_share"] = round(float((t.d > 0).mean()), 3)
            # nights held: count of 17:00 ET daily breaks crossed
            d_in = pd.to_datetime(t["t_in"], unit="ms", utc=True).dt.tz_convert("America/New_York") + pd.Timedelta(hours=7)
            d_out = pd.to_datetime(t["t_out"], unit="ms", utc=True).dt.tz_convert("America/New_York") + pd.Timedelta(hours=7)
            row[f"{nm}_avg_nights"] = round(float((d_out.dt.normalize() - d_in.dt.normalize()).dt.days.mean()), 2)
            if cost == "mid":
                beta = t["d"].values * mu[sp] * hold.values
                adj = t["pnl"].values - beta
                row[f"{nm}_beta"] = {
                    side: {"n": int(msk.sum()), "avg_pts": round(float(t["pnl"].values[msk].mean()), 3),
                           "avg_beta_pts": round(float(beta[msk].mean()), 3),
                           "avg_beta_adj_pts": round(float(adj[msk].mean()), 3)}
                    for side, msk in (("all", np.ones(len(t), bool)), ("long", t["d"].values > 0),
                                      ("short", t["d"].values < 0)) if msk.sum()}
        t0 = tr[tr.split == 0] if len(tr) else tr
        if len(t0) > 1:
            h = len(t0) // 2
            row["train_halves_pf"] = [stats(t0.iloc[:h]).get("pf"), stats(t0.iloc[h:]).get("pf")]
        out[cost] = row
        print(cost, "TRAIN", row.get("train"), "\n      VALID", row.get("valid"), flush=True)
    base_tr = trades["lf_base"]
    # monthly table (lf_base)
    mon = pd.to_datetime(base_tr["t_in"], unit="ms", utc=True).dt.strftime("%Y-%m")
    mt = base_tr.groupby(mon).apply(lambda g: pd.Series({
        "n": len(g), "long": int((g.d > 0).sum()), "short": int((g.d < 0).sum()),
        "sum_R": round(float(g["R"].sum()), 2), "avg_pts": round(float(g["pnl"].mean()), 3),
        "pf": round(float(g.pnl[g.pnl > 0].sum() / max(-g.pnl[g.pnl < 0].sum(), 1e-9)), 2),
        "sum_R_long": round(float(g["R"][g.d > 0].sum()), 2), "sum_R_short": round(float(g["R"][g.d < 0].sum()), 2)}))
    mt.to_csv(ROOT / f"cand/results/{a.module}_{a.tag}_monthly.csv")
    print(mt.to_string(), flush=True)
    # mirror
    od_all = [o for o in M.orders(m1, **p) if o["t"] < TEST_MS]
    mtr = run_orders(mirror(od_all), "lf_base")
    out["mirror_lf_base"] = {nm: stats(mtr[mtr.split == sp]) for sp, nm in ((0, "train"), (1, "valid"))}
    print("mirror", out["mirror_lf_base"], flush=True)
    # nulls per split
    if a.nulls:
        for sp, nm, lo, hi in ((1, "valid", VALID_MS, TEST_MS), (0, "train", 0, VALID_MS)):
            od = [o for o in od_all if lo <= o["t"] < hi]
            real = stats(base_tr[base_tr.split == sp]).get("avg_pts")
            out[f"null_random_{nm}"] = null_p(od, real, a.seeds, sp, perm=False)
            out[f"null_perm_{nm}"] = null_p(od, real, a.seeds, sp, perm=True)
            print(nm, "real", real, "rand", out[f"null_random_{nm}"], "perm", out[f"null_perm_{nm}"], flush=True)
    # neighbours
    if a.neighbors:
        nb = []
        for ch in json.loads(a.neighbors):
            q = {**p, **ch}
            ev = evaluate(a.module, q, cost="lf_base")
            nb.append({"change": ch, "train": ev["train"], "valid": ev["valid"]})
            print("nb", ch, ev["train"].get("pf"), ev["valid"].get("pf"), flush=True)
        out["neighbors_lf_base"] = nb
        vpf = [x["valid"].get("pf", 0) for x in nb if x["valid"].get("n", 0)]
        out["neighbors_valid_pf_median"] = float(np.median(vpf)) if vpf else None
    with open(ROOT / f"cand/results/{a.module}_{a.tag}_eval.json", "w") as f:
        json.dump(out, f, indent=1, default=str)


if __name__ == "__main__":
    main()
