"""
cand/zone_retest_stages.py - stage-2 / stage-3 driver for cand/zone_retest.py (H2).

Stage 1 is run with the engine CLI:   python3 lib/sweep.py zone_retest --jobs 2
and its CSVs are copied to cand/results/zone_retest_stage1_{train,valid}.csv.

    python3 cand/zone_retest_stages.py stage2   # top 3 stage-1 configs (by TRAIN t, n >= 60) x 36 exit/stop variants
    python3 cand/zone_retest_stages.py stage3   # top 2 stage-2 configs x session x (k, L) x fresh = 12 each

Selection uses TRAIN only (sweep._job: orders at/after 2026-06-01 are dropped, stats on split 0) and the engine's
ranking rule: TRAIN t-stat among configs with >= sweep.MIN_N_RANK (60) TRAIN trades. Every evaluated config is
written to cand/results/zone_retest_stage{2,3}_train.csv. Uses at most 2 worker processes.

History: a first stage-2 run ranked parents by raw t without the n >= 60 rule and picked 5 low-n parents
(n = 19-39); its 180 configs are kept in cand/results/zone_retest_stage2a_lowN_train.csv and counted in the
config total. Parent counts were cut from 5/3 to 3/2 to stay near the ~500-config budget.
"""
import itertools
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "cand"))
import sweep  # noqa: E402

NAME = "zone_retest"
RES = ROOT / "cand/results"
S1_KEYS = ["w", "K", "beta", "lam", "R", "touch", "trig"]
S2_KEYS = S1_KEYS + ["sigma", "tp_mode", "tp_r", "tmax", "be"]
S3_KEYS = S2_KEYS + ["sess", "k", "L", "fresh"]


def _clean(v):
    return v.item() if hasattr(v, "item") else v


def _run(cfgs, out_csv, stage):
    t0 = time.time()
    rows = []
    with ProcessPoolExecutor(max_workers=2) as ex:
        for i, r in enumerate(ex.map(sweep._job, [(NAME, c, "base") for c in cfgs])):
            rows.append({**r, "stage": stage})
            if i % 20 == 0:
                print(f"[{stage}] {i + 1}/{len(cfgs)} {time.time() - t0:.0f}s", flush=True)
    df = pd.DataFrame(rows)
    df["rank_t"] = df["tr_t"].where(df["tr_n"] >= sweep.MIN_N_RANK, -float("inf"))
    df = df.sort_values("rank_t", ascending=False)
    df.to_csv(out_csv, index=False)
    print(df.head(15).to_string(), flush=True)
    return df


def top_parents(csv, keys, n):
    df = pd.read_csv(csv).dropna(subset=["tr_t"])
    df = df[df["tr_n"] >= sweep.MIN_N_RANK].sort_values("tr_t", ascending=False)
    return [{k: _clean(r[k]) for k in keys} for _, r in df.head(n).iterrows()]


def stage2():
    parents = top_parents(RES / f"{NAME}_stage1_train.csv", S1_KEYS, 3)
    cfgs = []
    for p in parents:
        for sigma, (tpm, tpr), tmax, be in itertools.product([0.2, 0.5], [("near", 1.0), ("near", 1.5), ("r", 1.5)],
                                                              [15, 30, 60], [0, 1]):
            cfgs.append({**p, "sigma": sigma, "tp_mode": tpm, "tp_r": tpr, "tmax": tmax, "be": be})
    print(f"[stage2] {len(parents)} parents, {len(cfgs)} configs", flush=True)
    return _run(cfgs, RES / f"{NAME}_stage2_train.csv", "s2")


def stage3():
    parents = top_parents(RES / f"{NAME}_stage2_train.csv", S2_KEYS, 2)
    cfgs = []
    for p in parents:
        for (k, L), sess, fresh in itertools.product([(2, 120), (3, 240), (2, 240)], ["0612", "0617"], [60, 15]):
            cfgs.append({**p, "sess": sess, "k": k, "L": L, "fresh": fresh})
    print(f"[stage3] {len(parents)} parents, {len(cfgs)} configs", flush=True)
    return _run(cfgs, RES / f"{NAME}_stage3_train.csv", "s3")


if __name__ == "__main__":
    {"stage2": stage2, "stage3": stage3}[sys.argv[1]]()
