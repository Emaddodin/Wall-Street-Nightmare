"""
cand/ml_meta_stages.py - driver for cand/ml_meta.py (TRAIN + VALID only; TEST is never computed).

    ML_META_CACHE=<dir> python3 cand/ml_meta_stages.py prefit [--pairs '[[1.0,60],...]'] [--mode mirror]
        builds the causal feature matrix once and fits one model per (a, T) bracket (sequential, single process),
        caching predictions under $ML_META_CACHE; writes cand/results/ml_meta_models.json (AUC, calibration,
        best iteration, base rates on FIT / Q4 / VALID for every model).
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "cand"))

from data import load_m1  # noqa: E402
import ml_meta as M  # noqa: E402


def prefit(pairs, mode):
    m1 = load_m1()
    t0 = time.time()
    b = M._base(m1)
    print(f"[prefit] features {b['X'].shape} in {time.time() - t0:.0f}s", flush=True)
    out_p = ROOT / "cand/results/ml_meta_models.json"
    allres = json.loads(out_p.read_text()) if out_p.exists() else {}
    for a, T in pairs:
        t1 = time.time()
        P = M.predictions(m1, float(a), int(T), mode)
        d = P["diag"]
        allres[f"{a}_{T}_{mode}"] = d
        print(f"[prefit] a={a} T={T} {mode} it={d.get('best_iter')} "
              f"q4 auc L/S/stk {d['q4']['auc_long']}/{d['q4']['auc_short']}/{d['q4']['auc_stacked']} "
              f"valid auc {d['valid']['auc_long']}/{d['valid']['auc_short']}/{d['valid']['auc_stacked']} "
              f"base q4 {d['q4']['base_rate_long']}/{d['q4']['base_rate_short']} "
              f"({time.time() - t1:.0f}s)", flush=True)
        out_p.write_text(json.dumps(allres, indent=1))


def diag(pairs, mode):
    """Split each model's ranking skill into DIRECTION (among bars where exactly one side's bracket won, does
    P_long - P_short pick it?) and RESOLUTION (does P_long + P_short predict that some bracket resolves, i.e.
    volatility?). Also the mid-price label R of the model's side vs the opposite side in the top decile."""
    import numpy as np
    from sklearn.metrics import roc_auc_score
    m1 = load_m1()
    b = M._base(m1)
    out = {}
    for a, T in pairs:
        P = M.predictions(m1, float(a), int(T), mode)
        yL, yS, tend, RL, RS, complete = M.labels(b, float(a), int(T))
        PL, PS = P["PL"].astype(float), P["PS"].astype(float)
        res = {}
        for nm, msk in (("q4", P["calmask"].astype(bool)), ("valid", P["valmask"].astype(bool))):
            one = msk & ((yL == 1) ^ (yS == 1))
            anyr = msk
            r = {"n": int(msk.sum()), "n_one_side": int(one.sum()),
                 "auc_direction": round(float(roc_auc_score(yL[one], (PL - PS)[one])), 4),
                 "auc_resolution": round(float(roc_auc_score(((yL == 1) | (yS == 1))[anyr], (PL + PS)[anyr])), 4)}
            pm = np.maximum(PL, PS)
            thr = np.quantile(pm[msk], 0.9)
            top = msk & (pm >= thr)
            side = PL >= PS
            r["top10_R_model_side"] = round(float(np.where(side, RL, RS)[top].mean()), 4)
            r["top10_R_opposite"] = round(float(np.where(side, RS, RL)[top].mean()), 4)
            r["all_R_long"] = round(float(RL[msk].mean()), 4)
            r["all_R_short"] = round(float(RS[msk].mean()), 4)
            r["top10_long_share"] = round(float(side[top].mean()), 3)
            res[nm] = r
        out[f"{a}_{T}_{mode}"] = res
        print(a, T, json.dumps(res), flush=True)
    (ROOT / "cand/results/ml_meta_diag.json").write_text(json.dumps(out, indent=1))


def stage(cfgs, out, jobs=2, top=5):
    """Run an explicit config list through sweep._job (same TRAIN(=Q4) metrics as sweep.py), rank by Q4 t-stat
    (n >= 60), then validate the top configs once at lf_base and lf_harsh (same columns as sweep.py)."""
    from concurrent.futures import ProcessPoolExecutor
    import numpy as np
    import pandas as pd
    import sweep
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        rows = list(ex.map(sweep._job, [("ml_meta", c, "base") for c in cfgs]))
    df = pd.DataFrame(rows)
    df["rank_t"] = np.where(df["tr_n"] >= sweep.MIN_N_RANK, df["tr_t"], -np.inf)
    df = df.sort_values("rank_t", ascending=False)
    df.to_csv(ROOT / f"cand/results/{out}_train.csv", index=False)
    print(f"[stage] {len(cfgs)} configs in {time.time() - t0:.0f}s", flush=True)
    keys = sorted({k for c in cfgs for k in c})
    vrows = []
    for _, r in df[np.isfinite(df["rank_t"])].head(top).iterrows():
        p = {k: (r[k].item() if hasattr(r[k], "item") else r[k]) for k in keys if k in r and pd.notna(r[k])}
        for c in ("base", "harsh"):
            ev = sweep.evaluate("ml_meta", p, cost=c)
            vrows.append({**p, "cost": c, **{f"tr_{k}": v for k, v in ev["train"].items()},
                          **{f"va_{k}": v for k, v in ev["valid"].items()}})
    vdf = pd.DataFrame(vrows)
    vdf.to_csv(ROOT / f"cand/results/{out}_valid.csv", index=False)
    pd.set_option("display.width", 250)
    print(df.drop(columns=[c for c in df.columns if c in ("tr_wr", "tr_maxdd_R")]).to_string())
    print(vdf.to_string())


S2 = (
    # (A) finer q around the stage-1 top 5 (ranked by Q4 t, n >= 60)
    [{"a": 1.0, "T": 120, "q": q, "flip": 1} for q in (0.01, 0.03)]
    + [{"a": 1.0, "T": 30, "q": q, "flip": 0} for q in (0.01, 0.03)]
    + [{"a": 1.5, "T": 60, "q": q, "flip": 0} for q in (0.01, 0.03)]
    + [{"a": 1.0, "T": 30, "q": q, "flip": 1} for q in (0.01, 0.03)]
    + [{"a": 1.0, "T": 60, "q": q, "flip": 1} for q in (0.03, 0.07)]
    # (B) separate long / short models (directional comparison arm) at the stage-1 top 5
    + [{"a": 1.0, "T": 120, "q": 0.02, "flip": 1, "mode": "sep"}, {"a": 1.0, "T": 30, "q": 0.02, "flip": 0, "mode": "sep"},
       {"a": 1.5, "T": 60, "q": 0.02, "flip": 0, "mode": "sep"}, {"a": 1.0, "T": 30, "q": 0.02, "flip": 1, "mode": "sep"},
       {"a": 1.0, "T": 60, "q": 0.05, "flip": 1, "mode": "sep"}]
    # (C) bracket-width neighbours a +- 0.25
    + [{"a": a, "T": 120, "q": 0.02, "flip": 1} for a in (0.75, 1.25)]
    + [{"a": a, "T": 30, "q": 0.02, "flip": 0} for a in (0.75, 1.25)]
    + [{"a": 1.25, "T": 60, "q": 0.02, "flip": 0}]
    + [{"a": a, "T": 30, "q": 0.02, "flip": 1} for a in (0.75, 1.25)]
    + [{"a": a, "T": 60, "q": 0.05, "flip": 1} for a in (0.75, 1.25)]
)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("--pairs", default=json.dumps([[a, T] for a in (0.5, 1.0, 1.5) for T in (30, 60, 120)]))
    ap.add_argument("--mode", default="mirror")
    a = ap.parse_args()
    if a.cmd == "prefit":
        prefit(json.loads(a.pairs), a.mode)
    elif a.cmd == "diag":
        diag(json.loads(a.pairs), a.mode)
    elif a.cmd == "s2":
        stage(S2, "ml_meta_s2")
