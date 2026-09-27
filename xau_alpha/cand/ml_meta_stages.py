"""
cand/ml_meta_stages.py - driver for cand/ml_meta.py (TRAIN + VALID only; TEST is never computed). Single process.

    ML_META_CACHE=<dir> python3 cand/ml_meta_stages.py prefit
        builds the causal feature matrix once and fits one model per (a, T) bracket of ml_meta.GRID (wmode 'cap',
        mode 'mirror'), caching predictions; writes cand/results/ml_meta_models.json (AUC incl. direction /
        resolution split, calibration, best iteration, Q4 log-loss curve, base rates on FIT / Q4 / VALID).
    ML_META_CACHE=<dir> python3 cand/ml_meta_stages.py stage1
        the pre-declared 27-config grid (a x T x q), each simulated at lf_base; ranked by the t-stat of R on the
        sweep's TRAIN split, which is the Q4-2025 slice with emit='oos' (n >= 60); the top 5 are then checked once
        on VALID at lf_base and lf_harsh. Writes cand/results/ml_meta_s1_{train,valid}.csv.
    ML_META_CACHE=<dir> python3 cand/ml_meta_stages.py stage2 --base '<json of the stage-1 winner>'
        comparison arms around the stage-1 winner (uncapped 'atr' stop, 'atr' + flip filter, separate side models,
        q neighbours), same ranking / validation. Writes cand/results/ml_meta_s2_{train,valid}.csv.
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

from data import load_m1  # noqa: E402
import ml_meta as M  # noqa: E402
import sweep  # noqa: E402

RES = ROOT / "cand/results"


def prefit(pairs, wmode="cap", mode="mirror"):
    m1 = load_m1()
    t0 = time.time()
    b = M._base(m1)
    print(f"[prefit] features {b['X'].shape} in {time.time() - t0:.0f}s", flush=True)
    out_p = RES / "ml_meta_models.json"
    allres = json.loads(out_p.read_text()) if out_p.exists() else {}
    for a, T in pairs:
        t1 = time.time()
        P = M.predictions(m1, float(a), int(T), wmode, mode)
        d = P["diag"]
        allres[f"{a}_{T}_{wmode}_{mode}"] = d
        q4, va = d["q4"], d["valid"]
        print(f"[prefit] a={a} T={T} {wmode}/{mode} it={d.get('best_iter')} "
              f"q4 auc stk/dir/res {q4['auc_stacked']}/{q4['auc_direction']}/{q4['auc_resolution']} "
              f"valid auc stk/dir/res {va['auc_stacked']}/{va['auc_direction']}/{va['auc_resolution']} "
              f"q4 ll {d.get('q4_logloss_best')} vs base {d.get('q4_logloss_base')} "
              f"w med fit/q4/val {d['w_median']} ({time.time() - t1:.0f}s)", flush=True)
        out_p.write_text(json.dumps(allres, indent=1))


def stage(cfgs, out, top=5):
    """sweep._job on each config (TRAIN = Q4 slice with emit='oos'), rank by TRAIN t (n >= 60), then validate the
    top configs once at lf_base and lf_harsh (same columns as sweep.py)."""
    t0 = time.time()
    rows = []
    for i, c in enumerate(cfgs):
        p = {**{k: v for k, v in M.DEFAULTS.items() if k not in ("emit",)}, **c}
        r = sweep._job(("ml_meta", p, "base"))
        rows.append(r)
        print(f"[stage] {i + 1}/{len(cfgs)} {json.dumps(c)} n={r.get('tr_n')} pf={r.get('tr_pf')} "
              f"t={r.get('tr_t')} err={r.get('error', '')} ({time.time() - t0:.0f}s)", flush=True)
    df = pd.DataFrame(rows)
    df["rank_t"] = np.where(df["tr_n"] >= sweep.MIN_N_RANK, df["tr_t"], -np.inf)
    df = df.sort_values("rank_t", ascending=False)
    df.to_csv(RES / f"{out}_train.csv", index=False)
    keys = [k for k in M.DEFAULTS if k != "emit"]
    vrows = []
    for _, r in df[np.isfinite(df["rank_t"])].head(top).iterrows():
        p = {k: (r[k].item() if hasattr(r[k], "item") else r[k]) for k in keys if k in r and pd.notna(r[k])}
        for c in ("base", "harsh"):
            ev = sweep.evaluate("ml_meta", p, cost=c)
            vrows.append({**p, "cost": c, **{f"tr_{k}": v for k, v in ev["train"].items()},
                          **{f"va_{k}": v for k, v in ev["valid"].items()},
                          "va_long_pf": ev.get("valid_long", {}).get("pf"),
                          "va_short_pf": ev.get("valid_short", {}).get("pf")})
    vdf = pd.DataFrame(vrows)
    vdf.to_csv(RES / f"{out}_valid.csv", index=False)
    pd.set_option("display.width", 250)
    show = [c for c in df.columns if c not in ("tr_wr", "tr_maxdd_R", "news", "emit")]
    print(df[show].to_string())
    print(vdf.to_string())


def stage1_cfgs():
    return [{"a": a, "T": T, "q": q} for a in M.GRID["a"] for T in M.GRID["T"] for q in M.GRID["q"]]


def stage2_cfgs(base):
    a, T, q = base["a"], base["T"], base["q"]
    qs = [0.01, 0.02, 0.03, 0.05, 0.07, 0.10]
    i = qs.index(q) if q in qs else None
    out = [{"a": a, "T": T, "q": q, "wmode": "atr"},              # uncapped stop (not flip-eligible in 2026)
           {"a": a, "T": T, "q": q, "wmode": "atr", "flip": 1},   # uncapped model, trade only when w in [1.2, 4]
           {"a": a, "T": T, "q": q, "mode": "sep"},               # separate long / short models
           {"a": a, "T": T, "q": q, "score": "edge"},             # select on |P_long - P_short| (direction only)
           {"a": a, "T": T, "q": 0.05, "score": "edge"}]
    if i is not None:
        out += [{"a": a, "T": T, "q": qs[j]} for j in (i - 1, i + 1) if 0 <= j < len(qs) and qs[j] not in M.GRID["q"]]
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("--base", default=None)
    a = ap.parse_args()
    if a.cmd == "prefit":
        prefit([(x, T) for x in M.GRID["a"] for T in M.GRID["T"]])
    elif a.cmd == "prefit_arms":
        b = json.loads(a.base)
        prefit([(b["a"], b["T"])], wmode="atr")
        prefit([(b["a"], b["T"])], mode="sep")
    elif a.cmd == "stage1":
        stage(stage1_cfgs(), "ml_meta_s1")
    elif a.cmd == "stage2":
        stage(stage2_cfgs(json.loads(a.base)), "ml_meta_s2", top=10)
