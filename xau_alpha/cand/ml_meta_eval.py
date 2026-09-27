"""
cand/ml_meta_eval.py - finalist evaluation for cand/ml_meta.py (TRAIN + VALID only, never TEST).

    ML_META_CACHE=<dir> python3 cand/ml_meta_eval.py '<params json>' [--seeds 50] [--out ml_meta_final] [--cv]

For one frozen config it reports:
  * lf_base / lf_harsh / mid / duka_raw stats on the Q4-2025 slice (sweep "train" split with emit='oos'; out-of-sample
    for the fit but used for early stopping, calibration and the threshold) and on VALID; long / short;
    flip-eligible subset (stop in [1.2, 4.0] $/oz); Q4 halves; t-stats
  * the in-sample Jan-Sep 2025 trades (emit='all', tag 'insample'), marked, never judged
  * random-direction null (nulltest.random_direction, `seeds` draws, lf_base) and the mirror on the VALID orders
  * a monthly R table (Q4 + VALID, lf_base), drop-best-month PF
  * one-step neighbours in q and T (median VALID PF, lf_base)
  * a label-level check on VALID (mid, no one-at-a-time): mean bracket outcome of fired bars vs all eligible bars
  * --cv: a purged 3-fold blocked cross-fit over 2025 (fold = 4 months, 1-day purge each side; same iteration count
    as the main model; threshold = top-q quantile of each fold's OWN predictions, which uses no labels) to get
    out-of-sample TRAIN trades over the whole year. Diagnostic only.
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

from data import _ms, load_m1  # noqa: E402
import nulltest  # noqa: E402
import sweep  # noqa: E402
from sim import stats  # noqa: E402
import ml_meta as M  # noqa: E402

NAME = "ml_meta"


def tstat(tr):
    if len(tr) < 2:
        return None
    return round(float(tr["R"].mean() / (tr["R"].std(ddof=1) + 1e-12) * np.sqrt(len(tr))), 2)


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
    steps = {"q": [0.02, 0.05, 0.10, 0.20], "T": [30, 60, 120]}
    out = []
    for key, vals in steps.items():
        if p.get(key) not in vals:
            continue
        i = vals.index(p[key])
        for j in (i - 1, i + 1):
            if 0 <= j < len(vals):
                q = {**p, key: vals[j]}
                ev = sweep.evaluate(NAME, q, cost="base")
                out.append({"change": f"{key}={vals[j]}", "train": ev["train"], "valid": ev["valid"]})
    pfs = [o["valid"].get("pf", 0) for o in out if o["valid"].get("n", 0)]
    return out, (float(np.median(pfs)) if pfs else None)


def label_check(m1, p):
    """Mean bracket outcome (mid, R units, label timeout = mark-to-market) on VALID for fired vs all eligible bars."""
    b = M._base(m1)
    P = M.predictions(m1, p["a"], p["T"], p.get("mode", "mirror"))
    w = p["a"] * b["A5"]
    elig = b["gate"] & (b["near_ev_ms"] > p.get("news", 30) * 60_000)
    if p.get("flip"):
        elig &= (w >= M.FLIP[0]) & (w <= M.FLIP[1])
    pmax = np.maximum(P["PL"], P["PS"])
    q4 = elig & (b["td"] >= M.FIT_END) & (b["td"] < M.CAL_END)
    thr = float(np.quantile(pmax[q4], 1 - p["q"]))
    val = elig & P["valmask"].astype(bool)
    yL, yS, _, RL, RS, _ = M.labels(b, p["a"], p["T"])
    side = np.where(P["PL"] >= P["PS"], 1, -1)
    Rside = np.where(side == 1, RL, RS)
    fire = val & (pmax >= thr)
    out = {"thr": round(thr, 4), "valid_elig": int(val.sum()), "valid_fired": int(fire.sum()),
           "all_long_R": round(float(RL[val].mean()), 4), "all_short_R": round(float(RS[val].mean()), 4),
           "fired_R": round(float(Rside[fire].mean()), 4) if fire.any() else None,
           "fired_opposite_R": round(float(np.where(side == 1, RS, RL)[fire].mean()), 4) if fire.any() else None,
           "fired_winrate": round(float(np.where(side == 1, yL, yS)[fire].mean()), 4) if fire.any() else None,
           "fired_mean_p": round(float(pmax[fire].mean()), 4) if fire.any() else None,
           "fired_long_share": round(float((side[fire] == 1).mean()), 3) if fire.any() else None}
    q4f = q4 & (pmax >= thr) & P["calmask"].astype(bool)
    out["q4_fired_winrate"] = round(float(np.where(side == 1, yL, yS)[q4f].mean()), 4) if q4f.any() else None
    out["q4_fired_mean_p"] = round(float(pmax[q4f].mean()), 4) if q4f.any() else None
    return out


def crossfit(m1, p):
    """Purged blocked 3-fold cross-fit over 2025 -> out-of-sample TRAIN orders (diagnostic)."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    from threadpoolctl import threadpool_limits
    b = M._base(m1)
    P = M.predictions(m1, p["a"], p["T"], p.get("mode", "mirror"))
    it = P["diag"]["best_iter"]
    yL, yS, tend, RL, RS, complete = M.labels(b, p["a"], p["T"])
    X, td = b["X"], b["td"]
    Xm = M.mirror_X(X)
    w = p["a"] * b["A5"]
    elig = b["gate"] & (b["near_ev_ms"] > p.get("news", 30) * 60_000)
    trade_ok = elig & ((w >= M.FLIP[0]) & (w <= M.FLIP[1]) if p.get("flip") else True)
    edges = [_ms("2025-01-01"), _ms("2025-05-01"), _ms("2025-09-01"), _ms("2026-01-01")]
    day = 86_400_000
    ords = []
    for f in range(3):
        lo, hi = edges[f], edges[f + 1]
        test = trade_ok & (td >= lo) & (td < hi)
        trn = elig & (tend <= M.CAL_END) & ~((tend > lo - day) & (td < hi + day))
        hp = {**M.HP, "max_iter": int(it)}
        with threadpool_limits(limits=1):
            clf = HistGradientBoostingClassifier(**hp).fit(np.vstack([X[trn], Xm[trn]]), np.r_[yL[trn], yS[trn]])
            pl = clf.predict_proba(X[test])[:, 1]
            ps = clf.predict_proba(Xm[test])[:, 1]
        pm = np.maximum(pl, ps)
        thr = np.quantile(pm, 1 - p["q"])
        idx = np.flatnonzero(test)
        for r, a_, b_ in zip(idx, pl, ps):
            if max(a_, b_) < thr:
                continue
            t = int(b["ts"][b["I"][r]]) + 60_000
            flat = int(td[r] + int(b["to_flat"][r]) * 60_000 - 60_000)
            ords.append({"t": t, "d": 1 if a_ >= b_ else -1, "kind": "mkt", "sl_dist": float(w[r]),
                         "tp_dist": float(w[r]), "tmax": int(p["T"]) * 60_000, "flat": flat, "tag": f"cv{f}"})
    res = {}
    for cost in ("base", "harsh", "mid"):
        tr = sweep.run_orders(ords, cost)
        tr0 = tr[tr["split"] == 0] if len(tr) else tr
        res[cost] = {"all2025": {**stats(tr0), "t": tstat(tr0), "halves_pf": halves(tr0) if len(tr0) else None},
                     "long": stats(tr0[tr0.d == 1]) if len(tr0) else {}, "short": stats(tr0[tr0.d == -1]) if len(tr0) else {},
                     "by_fold": {tg: stats(g) for tg, g in tr0.groupby("tag")} if len(tr0) else {}}
        if cost == "base":
            fe = tr0[(tr0["risk"] >= 1.2) & (tr0["risk"] <= 4.0)] if len(tr0) else tr0
            res["flip_eligible_base"] = stats(fe)
            res["monthly_base"] = monthly(tr0).to_dict("records")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("params")
    ap.add_argument("--seeds", type=int, default=50)
    ap.add_argument("--out", default="ml_meta_final")
    ap.add_argument("--cv", action="store_true")
    ap.add_argument("--no-neigh", action="store_true")
    a = ap.parse_args()
    p = {**M.DEFAULTS, **json.loads(a.params)}
    p["emit"] = "oos"
    res = {"params": p}
    m1 = load_m1()
    res["model"] = M.predictions(m1, p["a"], p["T"], p["mode"])["diag"]

    for cost in ("base", "harsh", "mid", "duka_raw"):
        ev, tr = sweep.evaluate(NAME, p, cost=cost, return_trades=True)
        tr0 = tr[tr["split"] == 0] if len(tr) else tr
        tr1 = tr[tr["split"] == 1] if len(tr) else tr
        ev["q4_halves_pf"] = halves(tr0) if len(tr0) else None
        ev["q4_t"] = tstat(tr0)
        ev["valid_t"] = tstat(tr1)
        ev.pop("params", None)
        res[cost] = ev
        if cost == "base":
            mo = monthly(tr)
            mo.to_csv(ROOT / f"cand/results/{a.out}_monthly.csv", index=False)
            res["monthly"] = mo.to_dict("records")
            res["drop_best_month"] = {"q4": drop_best_month_pf(tr0), "valid": drop_best_month_pf(tr1)}
            res["exit_reasons_valid"] = tr1["reason"].value_counts().to_dict() if len(tr1) else {}
            res["risk_quantiles_valid"] = tr1["risk"].quantile([0.1, 0.5, 0.9]).round(2).tolist() if len(tr1) else []
            res["flip_share_valid"] = round(float(((tr1["risk"] >= 1.2) & (tr1["risk"] <= 4.0)).mean()), 3) \
                if len(tr1) else None
            real_valid = stats(tr1)

    # in-sample trades (model was fit on them): reported, never judged
    ev_in, tr_in = sweep.evaluate(NAME, {**p, "emit": "all"}, cost="base", return_trades=True)
    if len(tr_in):
        ins = tr_in[tr_in["tag"] == "insample"]
        res["insample_base"] = {**stats(ins), "t": tstat(ins)}

    # random-direction null and mirror on the VALID orders, lf_base
    ords = [o for o in M.orders(m1, **p) if M.CAL_END <= o["t"] < sweep.TEST_MS]
    nd = nulltest.random_direction(ords, seeds=range(a.seeds), cost="base", split=1)
    ra, rp = real_valid.get("avg_pts", 0), real_valid.get("pf", 0)
    res["null_valid"] = {"seeds": a.seeds, "n_orders": len(ords), "real_avg_pts": ra, "real_pf": rp,
                         "null_avg_pts_mean": round(float(nd["avg_pts"].mean()), 3),
                         "null_avg_pts_p95": round(float(nd["avg_pts"].quantile(0.95)), 3),
                         "null_pf_mean": round(float(nd["pf"].mean()), 3),
                         "p_avg_pts": round(float((1 + (nd["avg_pts"] >= ra).sum()) / (1 + len(nd))), 3),
                         "p_pf": round(float((1 + (nd["pf"] >= rp).sum()) / (1 + len(nd))), 3)}
    mir = sweep.run_orders(nulltest.mirror(ords), "base")
    res["null_valid"]["mirror_valid"] = stats(mir[mir["split"] == 1]) if len(mir) else {"n": 0}

    res["label_check"] = label_check(m1, p)
    if not a.no_neigh:
        res["neighbours"], res["neigh_median_valid_pf"] = neighbours(p)
    if a.cv:
        res["crossfit_2025"] = crossfit(m1, p)

    (ROOT / f"cand/results/{a.out}.json").write_text(json.dumps(res, indent=1, default=str))
    show = {k: res[k] for k in res if k not in ("monthly", "neighbours", "model")}
    print(json.dumps(show, indent=1, default=str))
    if "neighbours" in res:
        for o in res["neighbours"]:
            print(o["change"], "q4", o["train"].get("n"), o["train"].get("pf"), "valid", o["valid"].get("n"),
                  o["valid"].get("pf"))
        print("neigh median valid pf", res["neigh_median_valid_pf"])
    print(pd.DataFrame(res["monthly"]).to_string())


if __name__ == "__main__":
    main()
