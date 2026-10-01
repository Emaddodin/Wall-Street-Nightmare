"""
cand/ml_meta_eval.py - finalist evaluation for cand/ml_meta.py (TRAIN + VALID only, never TEST). Single process.

    ML_META_CACHE=<dir> python3 cand/ml_meta_eval.py '<params json>' [--seeds 200] [--out ml_meta_final] [--cv]

For one frozen config it reports:
  * model quality from the fitted model: AUC (stacked, long, short, direction, resolution), log-loss and Brier vs the
    base rate, calibration deciles and top-q win rates, on FIT (in-sample), Q4 2025 and VALID;
  * lf_base / lf_harsh / mid / duka_raw trade stats on the Q4-2025 slice (the sweep's TRAIN split with emit='oos';
    out-of-sample for the fit but used for early stopping, calibration and the threshold) and on VALID; long / short;
    flip-eligible subset (stop in [1.2, 4.0] $/oz); halves; t-stats;
  * the in-sample Jan-Sep 2025 trades (emit='all', tag 'insample'), marked, never judged;
  * random-direction null (nulltest.random_direction, `seeds` draws, lf_base and mid) and the mirror, VALID orders;
  * monthly R table (Q4 + VALID, lf_base) and drop-best-month PF;
  * one-step neighbours in q, T and a (median VALID PF, lf_base);
  * label-level check on VALID (mid bracket outcome of fired bars vs all eligible bars, no one-at-a-time);
  * --cv: a purged blocked 3-fold cross-fit over 2025 (Jan-Apr, May-Aug, Sep-Dec; 1-day purge each side; same
    iteration count as the main model; threshold = top-q quantile of each fold's OWN predictions, which uses no
    labels) giving out-of-sample TRAIN trades over the whole year: the honest TRAIN analog for the pass bar
    (folds 0-1 are predicted by models that saw LATER 2025 data: a diagnostic, not a tradeable history).
Writes cand/results/<out>.json and <out>_monthly.csv (small files; no per-trade dumps).
"""
import argparse
import json
import sys
import time
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
T0 = time.time()


def log(*a):
    print(f"[eval {time.time() - T0:5.0f}s]", *a, flush=True)


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
                     "avg_R": s["avg_R"], "avg_pts": s["avg_pts"], "pf": s["pf"], "wr": s["wr"],
                     "n_long": int((g.d == 1).sum()), "n_short": int((g.d == -1).sum())})
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
    steps = {"q": [0.01, 0.02, 0.03, 0.05, 0.07, 0.10, 0.20], "T": [30, 60, 120], "a": [0.75, 1.0, 1.5]}
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
                log("neighbour", key, vals[j], "q4", ev["train"].get("n"), ev["train"].get("pf"),
                    "valid", ev["valid"].get("n"), ev["valid"].get("pf"))
    pfs = [o["valid"].get("pf", 0) for o in out if o["valid"].get("n", 0)]
    return out, (float(np.median(pfs)) if pfs else None)


def label_check(m1, p):
    """Mean bracket outcome (mid, R units, label timeout = mark-to-market) on Q4 / VALID for fired vs all eligible
    bars (every fired bar counts, no one-at-a-time)."""
    b = M._base(m1)
    P = M.predictions(m1, p["a"], p["T"], p["wmode"], p["mode"])
    w = M.bracket(b["A5"], p["a"], p["wmode"])
    elig = M.eligible(b, w, p["wmode"], p["flip"], p["news"])
    thr = M.threshold(P, elig, b["td"], p["q"], p["score"])
    sc = M.select_score(P, p["score"])
    pmax = np.maximum(P["PL"], P["PS"])
    side = np.where(P["PL"] >= P["PS"], 1, -1)
    yL, yS, RL, RS = P["yL"], P["yS"], P["RL"], P["RS"]
    Rside = np.where(side == 1, RL, RS)
    Ropp = np.where(side == 1, RS, RL)
    ywin = np.where(side == 1, yL, yS)
    out = {"thr": round(thr, 4)}
    for nm, msk in (("q4", P["calmask"].astype(bool)), ("valid", P["valmask"].astype(bool))):
        m = elig & msk
        fire = m & (sc >= thr)
        out[nm] = {"elig": int(m.sum()), "fired": int(fire.sum()),
                   "all_long_R": round(float(RL[m].mean()), 4), "all_short_R": round(float(RS[m].mean()), 4),
                   "fired_R_model_side": round(float(Rside[fire].mean()), 4) if fire.any() else None,
                   "fired_R_opposite": round(float(Ropp[fire].mean()), 4) if fire.any() else None,
                   "fired_winrate": round(float(ywin[fire].mean()), 4) if fire.any() else None,
                   "fired_mean_p": round(float(pmax[fire].mean()), 4) if fire.any() else None,
                   "fired_long_share": round(float((side[fire] == 1).mean()), 3) if fire.any() else None,
                   "fired_t_R": round(float(Rside[fire].mean() / (Rside[fire].std(ddof=1) + 1e-12)
                                            * np.sqrt(fire.sum())), 2) if fire.sum() > 2 else None}
    return out


def crossfit(m1, p):
    """Purged blocked 3-fold cross-fit over 2025 -> out-of-sample TRAIN orders (diagnostic)."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score
    from threadpoolctl import threadpool_limits
    b = M._base(m1)
    P = M.predictions(m1, p["a"], p["T"], p["wmode"], p["mode"])
    it = P["diag"]["best_iter"]
    it = int(it if np.isscalar(it) else max(it))
    w = M.bracket(b["A5"], p["a"], p["wmode"])
    yL, yS, tend = P["yL"], P["yS"], P["tend"]
    X = M.model_matrix(b, w)
    Xm = M.mirror_X(X)
    td = b["td"]
    train_ok = b["gate"] & (b["near_ev_ms"] > 30 * 60_000)
    trade_ok = M.eligible(b, w, p["wmode"], p["flip"], p["news"])
    edges = [_ms("2025-01-01"), _ms("2025-05-01"), _ms("2025-09-01"), _ms("2026-01-01")]
    day = 86_400_000
    ords, aucs = [], []
    for f in range(3):
        lo, hi = edges[f], edges[f + 1]
        test = trade_ok & (td >= lo) & (td < hi)
        trn = train_ok & (tend <= M.CAL_END) & ~((tend > lo - day) & (td < hi + day))
        hp = {**M.HP, "max_iter": it}
        with threadpool_limits(limits=1):
            clf = HistGradientBoostingClassifier(**hp).fit(np.vstack([X[trn], Xm[trn]]), np.r_[yL[trn], yS[trn]])
            pl = clf.predict_proba(X[test])[:, 1]
            ps = clf.predict_proba(Xm[test])[:, 1]
        lab = test & (tend <= hi)
        sel = lab[test]
        yy = np.r_[yL[test][sel], yS[test][sel]]
        aucs.append({"fold": f, "n_train": int(trn.sum()), "n_test": int(test.sum()),
                     "auc_stacked": round(float(roc_auc_score(yy, np.r_[pl[sel], ps[sel]])), 4)})
        pm = np.maximum(pl, ps) if p["score"] == "p" else np.abs(pl - ps)
        thr = np.quantile(pm, 1 - p["q"])
        idx = np.flatnonzero(test)
        for r, a_, b_, s_ in zip(idx, pl, ps, pm):
            if s_ < thr:
                continue
            t = int(b["ts"][b["I"][r]]) + 60_000
            flat = int(td[r] + int(b["to_flat"][r]) * 60_000 - 60_000)
            ords.append({"t": t, "d": 1 if a_ >= b_ else -1, "kind": "mkt", "sl_dist": float(w[r]),
                         "tp_dist": float(w[r]), "tmax": int(p["T"]) * 60_000, "flat": flat, "tag": f"cv{f}"})
    res = {"iter": it, "fold_auc": aucs}
    for cost in ("base", "harsh", "mid"):
        tr = sweep.run_orders(ords, cost)
        tr0 = tr[tr["split"] == 0] if len(tr) else tr
        res[cost] = {"all2025": {**stats(tr0), "t": tstat(tr0), "halves_pf": halves(tr0) if len(tr0) else None},
                     "long": stats(tr0[tr0.d == 1]) if len(tr0) else {},
                     "short": stats(tr0[tr0.d == -1]) if len(tr0) else {},
                     "by_fold": {tg: stats(g) for tg, g in tr0.groupby("tag")} if len(tr0) else {}}
        if cost == "base":
            fe = tr0[(tr0["risk"] >= M.FLIP[0]) & (tr0["risk"] <= M.FLIP[1])] if len(tr0) else tr0
            res["flip_eligible_base"] = stats(fe)
            res["monthly_base"] = monthly(tr0).to_dict("records")
            res["null_base"] = null_p(ords, stats(tr0), 100, "base", 0)
        log("crossfit", cost, res[cost]["all2025"])
    return res


def null_p(ords, real, seeds, cost, split):
    nd = nulltest.random_direction(ords, seeds=range(seeds), cost=cost, split=split)
    ra, rp = real.get("avg_pts", 0), real.get("pf", 0)
    return {"seeds": seeds, "cost": cost, "n_orders": len(ords), "real_avg_pts": ra, "real_pf": rp,
            "null_avg_pts_mean": round(float(nd["avg_pts"].mean()), 3),
            "null_avg_pts_p95": round(float(nd["avg_pts"].quantile(0.95)), 3),
            "null_pf_mean": round(float(nd["pf"].mean()), 3),
            "null_pf_p95": round(float(nd["pf"].quantile(0.95)), 3),
            "p_avg_pts": round(float((1 + (nd["avg_pts"] >= ra).sum()) / (1 + len(nd))), 4),
            "p_pf": round(float((1 + (nd["pf"] >= rp).sum()) / (1 + len(nd))), 4)}


def verdict(train, valid, harsh_valid, p_null, halves_pf):
    ok = (train.get("n", 0) >= 150 and valid.get("n", 0) >= 60
          and train.get("pf", 0) >= 1.15 and train.get("avg_pts", -9) >= 0.15
          and valid.get("pf", 0) >= 1.15 and valid.get("avg_pts", -9) >= 0.15
          and harsh_valid.get("pf", 0) >= 1.0 and p_null <= 0.05
          and halves_pf is not None and all((h or 0) > 1 for h in halves_pf))
    if ok:
        return "pass"
    if valid.get("pf", 0) >= 1.05 and train.get("pf", 0) >= 1.10:
        return "near"
    return "fail"


def verdict_valid_only(valid, harsh_valid, p_null):
    """The brief for this ML family: TRAIN-period trades are in-sample, so judge ONLY on VALID.
    pass: VALID n >= 60, lf_base PF >= 1.15 and avg >= +0.15 $/oz, lf_harsh PF >= 1.0, random-direction p <= 0.05.
    near: VALID lf_base PF >= 1.05.  fail: otherwise."""
    ok = (valid.get("n", 0) >= 60 and valid.get("pf", 0) >= 1.15 and valid.get("avg_pts", -9) >= 0.15
          and harsh_valid.get("pf", 0) >= 1.0 and p_null <= 0.05)
    if ok:
        return "pass"
    if valid.get("n", 0) >= 60 and valid.get("pf", 0) >= 1.05:
        return "near"
    return "fail"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("params")
    ap.add_argument("--seeds", type=int, default=200)
    ap.add_argument("--out", default="ml_meta_final")
    ap.add_argument("--cv", action="store_true")
    ap.add_argument("--no-neigh", action="store_true")
    a = ap.parse_args()
    p = {**M.DEFAULTS, **json.loads(a.params)}
    p["emit"] = "oos"
    res = {"params": p}
    m1 = load_m1()
    res["model"] = M.predictions(m1, p["a"], p["T"], p["wmode"], p["mode"])["diag"]
    log("model", {k: res["model"][k] for k in ("best_iter", "n_fit_points", "n_q4_points", "n_valid_points")})

    trades = {}
    for cost in ("base", "harsh", "mid", "duka_raw"):
        ev, tr = sweep.evaluate(NAME, p, cost=cost, return_trades=True)
        tr0 = tr[tr["split"] == 0] if len(tr) else tr
        tr1 = tr[tr["split"] == 1] if len(tr) else tr
        ev["q4_halves_pf"] = halves(tr0) if len(tr0) else None
        ev["valid_halves_pf"] = halves(tr1) if len(tr1) else None
        ev["q4_t"] = tstat(tr0)
        ev["valid_t"] = tstat(tr1)
        ev.pop("params", None)
        res[cost] = ev
        trades[cost] = tr
        log(cost, "q4", ev["train"], "valid", ev["valid"])
        if cost == "base":
            mo = monthly(tr)
            mo.to_csv(ROOT / f"cand/results/{a.out}_monthly.csv", index=False)
            res["monthly"] = mo.to_dict("records")
            res["drop_best_month"] = {"q4": drop_best_month_pf(tr0), "valid": drop_best_month_pf(tr1)}
            res["exit_reasons"] = {"q4": tr0["reason"].value_counts().to_dict() if len(tr0) else {},
                                   "valid": tr1["reason"].value_counts().to_dict() if len(tr1) else {}}
            res["risk_quantiles_valid"] = tr1["risk"].quantile([0.1, 0.5, 0.9]).round(2).tolist() if len(tr1) else []
            res["flip_share_valid"] = round(float(((tr1["risk"] >= M.FLIP[0] - 1e-9)
                                                   & (tr1["risk"] <= M.FLIP[1] + 1e-9)).mean()), 3) if len(tr1) else None
            res["hour_valid"] = tr1.assign(hr=pd.to_datetime(tr1["t_in"], unit="ms", utc=True).dt.hour).groupby(
                "hr").agg(n=("pnl", "size"), avg_pts=("pnl", "mean")).round(3).reset_index().to_dict("records") \
                if len(tr1) else []

    # in-sample trades (the model was fit on them): reported, never judged
    ev_in, tr_in = sweep.evaluate(NAME, {**p, "emit": "all"}, cost="base", return_trades=True)
    if len(tr_in):
        ins = tr_in[tr_in["tag"] == "insample"]
        res["insample_base"] = {**stats(ins), "t": tstat(ins)}
        log("insample", res["insample_base"])

    # random-direction null and mirror on the VALID orders
    ords = [o for o in M.orders(m1, **p) if M.CAL_END <= o["t"] < sweep.TEST_MS]
    res["null_valid"] = null_p(ords, res["base"]["valid"], a.seeds, "base", 1)
    log("null base", res["null_valid"])
    res["null_valid_mid"] = null_p(ords, res["mid"]["valid"], min(a.seeds, 100), "mid", 1)
    log("null mid", res["null_valid_mid"])
    mir = sweep.run_orders(nulltest.mirror(ords), "base")
    res["mirror_valid_base"] = stats(mir[mir["split"] == 1]) if len(mir) else {"n": 0}
    q4o = [o for o in M.orders(m1, **p) if o["t"] < M.CAL_END]
    res["null_q4"] = null_p(q4o, res["base"]["train"], min(a.seeds, 100), "base", 0)
    log("null q4", res["null_q4"])

    res["label_check"] = label_check(m1, p)
    log("label_check", res["label_check"])
    if not a.no_neigh:
        res["neighbours"], res["neigh_median_valid_pf"] = neighbours(p)
    if a.cv:
        res["crossfit_2025"] = crossfit(m1, p)

    cf = res.get("crossfit_2025", {}).get("base", {}).get("all2025", {})
    res["verdict_train_crossfit"] = verdict(cf, res["base"]["valid"], res["harsh"]["valid"],
                                            res["null_valid"]["p_avg_pts"], cf.get("halves_pf")) if cf else None
    res["verdict_train_q4"] = verdict(res["base"]["train"], res["base"]["valid"], res["harsh"]["valid"],
                                      res["null_valid"]["p_avg_pts"], res["base"]["q4_halves_pf"])
    res["verdict"] = verdict_valid_only(res["base"]["valid"], res["harsh"]["valid"], res["null_valid"]["p_avg_pts"])
    (ROOT / f"cand/results/{a.out}.json").write_text(json.dumps(res, indent=1, default=str))
    log("VERDICT (VALID only, the brief)", res["verdict"], "| TRAIN = crossfit:", res["verdict_train_crossfit"],
        "| TRAIN = Q4:", res["verdict_train_q4"])
    print(pd.DataFrame(res["monthly"]).to_string())


if __name__ == "__main__":
    main()
