"""
cand/exhaustion_fade_eval.py - finalist evaluation for cand/exhaustion_fade.py (TRAIN + VALID only, never TEST).

    python3 cand/exhaustion_fade_eval.py '<params json>' [--seeds 50] [--out exhaustion_fade_final]

For one frozen config it reports, at lf_base / lf_harsh / mid / duka_raw:
  train / valid stats, long / short, flip-eligible subset (stop in [1.2, 4.0] $/oz), TRAIN halves,
  the continuation twin (side='cont'), the HIGH-news-only variant (news='only'), VALID random-direction null
  (nulltest.random_direction on the VALID orders, `seeds` draws, lf_base), drop-best-month PF, a monthly R table,
  and one-step neighbours on the numeric axes (median VALID PF).
Writes cand/results/<out>.json and <out>_monthly.csv (small files; no per-trade dumps).
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

from data import VALID, _ms, load_m1  # noqa: E402
import nulltest  # noqa: E402
import sweep  # noqa: E402
from sim import stats  # noqa: E402
import exhaustion_fade as X  # noqa: E402

NAME = "exhaustion_fade"
VALID_MS = _ms(VALID[0])


def halves(tr0):
    h = len(tr0) // 2
    return stats(tr0.iloc[:h]).get("pf"), stats(tr0.iloc[h:]).get("pf")


def monthly(tr):
    if not len(tr):
        return pd.DataFrame()
    t = tr.copy()
    t["month"] = pd.to_datetime(t["t_in"], unit="ms", utc=True).dt.strftime("%Y-%m")
    rows = []
    for mth, g in t.groupby("month"):
        s = stats(g)
        rows.append({"month": mth, "split": int(g["split"].iloc[0]), "n": s["n"], "sum_R": s["sum_R"],
                     "avg_R": s["avg_R"], "avg_pts": s["avg_pts"], "pf": s["pf"], "wr": s["wr"]})
    return pd.DataFrame(rows)


def drop_best_month_pf(tr):
    m = monthly(tr)
    if len(m) < 2:
        return None
    best = m.sort_values("sum_R").iloc[-1]["month"]
    t = tr.copy()
    t["month"] = pd.to_datetime(t["t_in"], unit="ms", utc=True).dt.strftime("%Y-%m")
    return {"dropped": best, "pf": stats(t[t["month"] != best]).get("pf")}


def neighbours(p):
    steps = {"k": [2.0, 2.5, 3.0], "s": [0.15, 0.3, 0.6], "m": [3, 5, 10], "tmax": [30, 60, 120]}
    out = []
    for key, vals in steps.items():
        if key not in p or p[key] not in vals:
            continue
        i = vals.index(p[key])
        for j in (i - 1, i + 1):
            if 0 <= j < len(vals):
                q = {**p, key: vals[j]}
                ev = sweep.evaluate(NAME, q, cost="base")
                out.append({"change": f"{key}={vals[j]}", "train": ev["train"], "valid": ev["valid"]})
    pfs = [o["valid"].get("pf", 0) for o in out if o["valid"].get("n", 0)]
    return out, (float(np.median(pfs)) if pfs else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("params")
    ap.add_argument("--seeds", type=int, default=50)
    ap.add_argument("--out", default="exhaustion_fade_final")
    ap.add_argument("--no-neigh", action="store_true")
    a = ap.parse_args()
    p = {**X.DEFAULTS, **json.loads(a.params)}
    res = {"params": p}
    m1 = load_m1()

    for cost in ("base", "harsh", "mid", "duka_raw"):
        ev, tr = sweep.evaluate(NAME, p, cost=cost, return_trades=True)
        tr0 = tr[tr["split"] == 0] if len(tr) else tr
        ev["train_halves_pf"] = halves(tr0) if len(tr0) else None
        if len(tr0) > 1:
            ev["train_t"] = round(float(tr0["R"].mean() / tr0["R"].std(ddof=1) * np.sqrt(len(tr0))), 2)
        tr1 = tr[tr["split"] == 1] if len(tr) else tr
        if len(tr1) > 1:
            ev["valid_t"] = round(float(tr1["R"].mean() / tr1["R"].std(ddof=1) * np.sqrt(len(tr1))), 2)
        ev.pop("params", None)
        res[cost] = ev
        if cost == "base":
            mo = monthly(tr)
            mo.to_csv(ROOT / f"cand/results/{a.out}_monthly.csv", index=False)
            res["monthly"] = mo.to_dict("records")
            res["drop_best_month"] = {"train": drop_best_month_pf(tr0), "valid": drop_best_month_pf(tr1)}
            res["exit_reasons_valid"] = tr1["reason"].value_counts().to_dict() if len(tr1) else {}
            res["exit_reasons_train"] = tr0["reason"].value_counts().to_dict() if len(tr0) else {}
            res["risk_quantiles_train"] = tr0["risk"].quantile([0.1, 0.5, 0.9]).round(2).tolist() if len(tr0) else []
            res["risk_quantiles_valid"] = tr1["risk"].quantile([0.1, 0.5, 0.9]).round(2).tolist() if len(tr1) else []
            if len(tr):
                res["flip_share_valid"] = round(float(((tr1["risk"] >= 1.2) & (tr1["risk"] <= 4.0)).mean()), 3) \
                    if len(tr1) else None
                res["flip_share_train"] = round(float(((tr0["risk"] >= 1.2) & (tr0["risk"] <= 4.0)).mean()), 3)
            real_valid = stats(tr1)

    # continuation twin and the news-only variant (lf_base and mid)
    for tag, q in (("twin_cont", {**p, "side": "cont"}), ("news_only", {**p, "news": "only"}),
                   ("news_all", {**p, "news": "all"})):
        res[tag] = {}
        for cost in ("base", "mid"):
            ev = sweep.evaluate(NAME, q, cost=cost)
            res[tag][cost] = {k: ev.get(k) for k in ("train", "valid", "train_long", "train_short",
                                                      "valid_long", "valid_short")}

    # random-direction null on VALID orders (same times / stop / target distances, random side), lf_base
    ords = [o for o in X.orders(m1, **p) if VALID_MS <= o["t"] < sweep.TEST_MS]
    nd = nulltest.random_direction(ords, seeds=range(a.seeds), cost="base", split=1)
    null = {"seeds": a.seeds, "real_avg_pts": real_valid.get("avg_pts"), "real_pf": real_valid.get("pf"),
            "null_avg_pts_mean": round(float(nd["avg_pts"].mean()), 3),
            "null_avg_pts_p95": round(float(nd["avg_pts"].quantile(0.95)), 3),
            "null_pf_mean": round(float(nd["pf"].mean()), 3),
            "p_avg_pts": round(float((1 + (nd["avg_pts"] >= real_valid.get("avg_pts", 0)).sum()) / (1 + len(nd))), 3),
            "p_pf": round(float((1 + (nd["pf"] >= real_valid.get("pf", 0)).sum()) / (1 + len(nd))), 3)}
    mir = sweep.run_orders(nulltest.mirror(ords), "base")
    null["mirror_valid"] = stats(mir[mir["split"] == 1]) if len(mir) else {"n": 0}
    res["null_valid"] = null

    if not a.no_neigh:
        res["neighbours"], res["neigh_median_valid_pf"] = neighbours(p)

    (ROOT / f"cand/results/{a.out}.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: res[k] for k in res if k not in ("monthly", "neighbours")}, indent=1, default=str))
    if "neighbours" in res:
        for o in res["neighbours"]:
            print(o["change"], "train", o["train"].get("n"), o["train"].get("pf"), "valid", o["valid"].get("n"),
                  o["valid"].get("pf"))
        print("neigh median valid pf", res["neigh_median_valid_pf"])
    print(pd.DataFrame(res["monthly"]).to_string())


if __name__ == "__main__":
    main()
