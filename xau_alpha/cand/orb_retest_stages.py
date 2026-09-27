"""
SUPERSEDED (2026-09-30) by cand/orb_retest_stage2.py, which ran the actual stage 2 (results/orb_retest_s2_*).
This file is the killed agent's unrun draft. Its stage2 read results/orb_retest_s1_train.csv, which never existed
(FileNotFoundError); it now falls back to results/orb_retest_train.csv and writes orb_retest_s2legacy_*.

cand/orb_retest_stages.py - staged search driver for orb_retest (H6). TRAIN-only ranking via sweep._job
(the engine's own per-config TRAIN statistics), 2 worker processes, then VALID for the top configs only.

    python3 cand/orb_retest_stages.py stage2 [--top 5]
    python3 cand/orb_retest_stages.py custom '<json list of configs>' --name <label>

Stage 1 is the plain engine sweep (python3 lib/sweep.py orb_retest --jobs 2), whose CSVs are renamed to
results/orb_retest_s1_{train,valid}.csv.
Stage 2 (top 5 stage-1 parents by TRAIN t with n >= 60): brk_h {60, 120} x be {0, 1R} x tau {0.1, 0.3} (tau only for
retest arms) + the parent on the window unions 'lon+comex' and 'lon+comex+asia'.
"""
import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "cand"))

from sweep import MIN_N_RANK, _job, evaluate  # noqa: E402

RES = ROOT / "cand/results"
KEYS = ["window", "filt", "kap", "entry", "stop", "tp", "brk_h", "tau", "be"]


def _clean(v):
    return v.item() if hasattr(v, "item") else v


def run_configs(cfgs, name, top=8, jobs=2):
    t0 = time.time()
    rows = []
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        for i, r in enumerate(ex.map(_job, [("orb_retest", c, "base") for c in cfgs])):
            rows.append(r)
            if i % 20 == 0:
                print(f"[{name}] {i + 1}/{len(cfgs)} {time.time() - t0:.0f}s", flush=True)
    df = pd.DataFrame(rows)
    df["rank_t"] = np.where(df.get("tr_n", 0) >= MIN_N_RANK, df["tr_t"], -np.inf)
    df = df.sort_values("rank_t", ascending=False)
    df.to_csv(RES / f"orb_retest_{name}_train.csv", index=False)
    keys = [k for k in cfgs[0].keys()]
    vrows = []
    for _, r in df[np.isfinite(df["rank_t"])].head(top).iterrows():
        p = {k: _clean(r[k]) for k in keys}
        for c in ("base", "harsh"):
            ev = evaluate("orb_retest", p, cost=c)
            vrows.append({**p, "cost": c, **{f"tr_{k}": v for k, v in ev["train"].items()},
                          **{f"va_{k}": v for k, v in ev["valid"].items()},
                          **{f"vafe_{k}": v for k, v in ev.get("valid_flip_eligible", {}).items()}})
    vdf = pd.DataFrame(vrows)
    vdf.to_csv(RES / f"orb_retest_{name}_valid.csv", index=False)
    pd.set_option("display.width", 250)
    cols = [c for c in keys + ["tr_n", "tr_pf", "tr_avg_pts", "tr_t", "tr_pf_h1", "tr_pf_h2", "tr_fe_n"] if c in df]
    print(df[cols].head(15).to_string(), flush=True)
    if len(vdf):
        print(vdf[keys + ["cost", "tr_n", "tr_pf", "tr_avg_pts", "va_n", "va_pf", "va_avg_pts"]].to_string(), flush=True)
    return df, vdf


def stage2(top=5):
    # the stage-1 CSVs were never renamed to *_s1_* (the agent was killed first): fall back to the sweep's own name
    f1 = RES / "orb_retest_s1_train.csv"
    s1 = pd.read_csv(f1 if f1.exists() else RES / "orb_retest_train.csv")
    s1 = s1[np.isfinite(s1["rank_t"])].head(top)
    cfgs = []
    for _, r in s1.iterrows():
        par = {k: _clean(r[k]) for k in ["window", "filt", "kap", "entry", "stop", "tp"]}
        taus = [0.1, 0.3] if str(par["entry"]).startswith("rt") else [0.1]
        for brk_h in (60, 120):
            for be in (0.0, 1.0):
                for tau in taus:
                    cfgs.append({**par, "brk_h": brk_h, "tau": tau, "be": be})
        for wu in ("lon+comex", "lon+comex+asia"):
            if wu != par["window"]:
                cfgs.append({**par, "window": wu, "brk_h": 120, "tau": 0.1, "be": 0.0})
    # de-duplicate
    seen, uniq = set(), []
    for c in cfgs:
        k = json.dumps(c, sort_keys=True)
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    print(f"stage2: {len(uniq)} configs from {len(s1)} parents", flush=True)
    return run_configs(uniq, "s2legacy")      # do not overwrite orb_retest_s2_* from orb_retest_stage2.py


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what")
    ap.add_argument("cfgs", nargs="?")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--name", default="custom")
    a = ap.parse_args()
    if a.what == "stage2":
        stage2(a.top)
    elif a.what == "custom":
        run_configs(json.loads(a.cfgs), a.name)
