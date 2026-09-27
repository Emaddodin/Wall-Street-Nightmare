"""
cand/orb_retest_stage2.py - H6 stage 2 (TRAIN-only ranking) around the stage-1 leader
{window lon, filt 0, entry brk, stop far, kap 0/0.1}. Serial, one process (the machine is saturated; a 2-worker pool
would reload M1 in each worker). Ranking uses the engine's own sweep._job (TRAIN statistics only).

  Block A (72): kap {0, 0.1} x stop {far pad 0.1, far pad 0.25, mid} x tp {0, 1, 1.5, 2} x flat {13, 16, 19 UTC}
  Block B (33): top 3 of A x be {0, 0.5R, 1R} x tmax {0, 60, 120, 240 min}   (minus the parent itself)
  Block C (21): top 3 of A+B x {nb 0, nb 60, nexit 5, brk_h 60, brk_h 240, window lon+comex, filt 1}
Rank: TRAIN t-stat of R with n >= 60 (sweep.MIN_N_RANK). Finalist (pre-registered): the top TRAIN t with n >= 150
(the pass-bar minimum), chosen before any stage-2 VALID number is computed. VALID (lf_base + lf_harsh) is then
computed for the top 8 only, for the neighbour-robustness table.

    python3 cand/orb_retest_stage2.py
Writes results/orb_retest_s2_train.csv, results/orb_retest_s2_valid.csv, results/orb_retest_s2_finalist.json.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "cand"))

from sweep import MIN_N_RANK, _job, evaluate  # noqa: E402

RES = ROOT / "cand/results"
KEYS = ["window", "filt", "kap", "entry", "stop", "pad", "tp", "flat_utc", "be", "tmax", "brk_h", "nb", "nexit"]
BASE = dict(window="lon", filt=0, kap=0.0, entry="brk", stop="far", pad=0.1, tp=1.0, flat_utc=960, be=0.0, tmax=0,
            brk_h=120, nb=30, nexit=-1)
STOPS = [("far", 0.1), ("far", 0.25), ("mid", 0.1)]


def _key(c):
    return json.dumps({k: c[k] for k in KEYS}, sort_keys=True)


def _clean(v):
    return v.item() if hasattr(v, "item") else v


def run_block(cfgs, block, done, log=print):
    rows = []
    t0 = time.time()
    for i, c in enumerate(cfgs):
        k = _key(c)
        if k in done:
            continue
        r = _job(("orb_retest", c, "base"))
        r["block"] = block
        done[k] = r
        rows.append(r)
        if i % 10 == 0:
            log(f"[{block}] {i + 1}/{len(cfgs)} {time.time() - t0:.0f}s n={r.get('tr_n')} t={r.get('tr_t')}")
    return rows


def ranked(done):
    df = pd.DataFrame(list(done.values()))
    df["rank_t"] = np.where(df["tr_n"].fillna(0) >= MIN_N_RANK, df["tr_t"], -np.inf)
    return df.sort_values("rank_t", ascending=False).reset_index(drop=True)


def parents(df, top=3, blocks=None):
    d = df if blocks is None else df[df["block"].isin(blocks)]
    d = d[np.isfinite(d["rank_t"])].head(top)
    return [{k: _clean(r[k]) for k in KEYS} for _, r in d.iterrows()]


def main(log=print):
    done = {}
    A = [{**BASE, "kap": kap, "stop": s, "pad": pad, "tp": tp, "flat_utc": fu}
         for kap in (0.0, 0.1) for s, pad in STOPS for tp in (0.0, 1.0, 1.5, 2.0) for fu in (780, 960, 1140)]
    run_block(A, "A", done, log)
    df = ranked(done)
    df.to_csv(RES / "orb_retest_s2_train.csv", index=False)
    B = [{**p, "be": be, "tmax": tm} for p in parents(df, 3, ["A"]) for be in (0.0, 0.5, 1.0)
         for tm in (0, 60, 120, 240)]
    run_block(B, "B", done, log)
    df = ranked(done)
    df.to_csv(RES / "orb_retest_s2_train.csv", index=False)
    C = []
    for p in parents(df, 3):
        C += [{**p, "nb": 0}, {**p, "nb": 60}, {**p, "nexit": 5}, {**p, "brk_h": 60}, {**p, "brk_h": 240},
              {**p, "window": "lon+comex"}, {**p, "filt": 1}]
    run_block(C, "C", done, log)
    df = ranked(done)
    df.to_csv(RES / "orb_retest_s2_train.csv", index=False)
    log(f"stage 2 configs evaluated: {len(df)}")
    # pre-registered finalist: top TRAIN t with n >= 150 (chosen before any stage-2 VALID number exists)
    fin_row = df[(df["tr_n"] >= 150) & np.isfinite(df["rank_t"])].iloc[0]
    fin = {k: _clean(fin_row[k]) for k in KEYS}
    with open(RES / "orb_retest_s2_finalist.json", "w") as f:
        json.dump({"finalist": fin, "train_row": {k: _clean(v) for k, v in fin_row.items()}}, f, indent=1,
                  default=str)
    log(f"FINALIST {fin}")
    # VALID for the top 8 (information only; the finalist is already frozen)
    vrows = []
    for _, r in df[np.isfinite(df["rank_t"])].head(8).iterrows():
        p = {k: _clean(r[k]) for k in KEYS}
        for c in ("base", "harsh"):
            ev = evaluate("orb_retest", p, cost=c)
            vrows.append({**p, "cost": c, **{f"tr_{k}": v for k, v in ev["train"].items()},
                          **{f"va_{k}": v for k, v in ev["valid"].items()}})
    vdf = pd.DataFrame(vrows)
    vdf.to_csv(RES / "orb_retest_s2_valid.csv", index=False)
    return df, vdf, fin


if __name__ == "__main__":
    pd.set_option("display.width", 250)
    df, vdf, fin = main(lambda s: print(s, flush=True))
    print(df[KEYS + ["block", "tr_n", "tr_pf", "tr_avg_pts", "tr_t", "tr_pf_h1", "tr_pf_h2"]].head(25).to_string())
    print(vdf.to_string())
