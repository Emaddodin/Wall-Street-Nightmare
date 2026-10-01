"""
xau_alpha/lib/sweep.py
Shared evaluation / parameter-sweep harness for candidate strategies in xau_alpha/cand/<name>.py.

A candidate module exposes:
    GRID: dict[str, list]            parameter grid for the train search
    def orders(m1, **params) -> list[dict]   order dicts for sim.simulate (see sim.py docstring)

HOLDOUT DISCIPLINE: orders with t >= TEST start (2026-06-01) are dropped unless final=True. Search on TRAIN only,
pick finalists, check them on VALID, and leave TEST for the single frozen evaluation.

    python3 xau_alpha/lib/sweep.py <name> [--jobs 2] [--cost base] [--top 15]
writes xau_alpha/cand/results/<name>_train.csv and <name>_valid.csv
"""
import argparse
import importlib
import itertools
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

LIB = Path(__file__).resolve().parent
ROOT = LIB.parent
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(ROOT / "cand"))

from data import TEST, _ms, load_m1  # noqa: E402
from sim import COSTS, simulate, split_stats, stats  # noqa: E402

TEST_MS = _ms(TEST[0])
MIN_N_RANK = 60          # minimum TRAIN trades for a config to be ranked / validated
FLIP_STOP = (1.2, 4.0)   # $13 account at 1:500: stop must be within [1.2, 4.0] $/oz (SYNTHESIS section 5)


def run_orders(orders, cost="base", final=False):
    if not final:
        orders = [o for o in orders if o["t"] < TEST_MS]
    return simulate(orders, COSTS[cost] if isinstance(cost, str) else cost)


def evaluate(name: str, params: dict, cost="base", final=False, return_trades=False):
    mod = importlib.import_module(name)
    m1 = load_m1()
    tr = run_orders(mod.orders(m1, **params), cost, final)
    out = {"params": params, **{f"{k}": v for k, v in split_stats(tr, include_test=final).items()}}
    if len(tr):
        for side, nm in ((1, "long"), (-1, "short")):
            for sp, spn in ((0, "train"), (1, "valid")):
                out[f"{spn}_{nm}"] = stats(tr[(tr.d == side) & (tr.split == sp)])
        fe = tr[(tr["risk"] >= FLIP_STOP[0]) & (tr["risk"] <= FLIP_STOP[1])]
        for sp, spn in ((0, "train"), (1, "valid")):
            out[f"{spn}_flip_eligible"] = stats(fe[fe.split == sp])
    return (out, tr) if return_trades else out


def train_score(st: dict, min_n=60) -> float:
    """t-stat of R per trade on train, penalised for too few trades; 0 when PF <= 1."""
    if st.get("n", 0) < min_n or st.get("pf", 0) <= 1.0:
        return 0.0
    return st["avg_R"] * np.sqrt(st["n"]) / 1.0


def _job(args):
    name, params, cost = args
    try:
        mod = importlib.import_module(name)
        m1 = load_m1()
        tr = run_orders(mod.orders(m1, **params), cost)
        tr0 = tr[tr["split"] == 0] if len(tr) else tr
        st = stats(tr0)
        # R std for a proper t-stat
        if len(tr0) > 1:
            st["t"] = round(float(tr0["R"].mean() / (tr0["R"].std(ddof=1) + 1e-12) * np.sqrt(len(tr0))), 2)
            h = len(tr0) // 2
            st["pf_h1"] = stats(tr0.iloc[:h]).get("pf")
            st["pf_h2"] = stats(tr0.iloc[h:]).get("pf")
            fe = tr0[(tr0["risk"] >= FLIP_STOP[0]) & (tr0["risk"] <= FLIP_STOP[1])]
            st["fe_n"] = int(len(fe))
            st["fe_avgR"] = round(float(fe["R"].mean()), 3) if len(fe) else None
        return {**params, **{f"tr_{k}": v for k, v in st.items()}}
    except Exception as e:  # keep the sweep alive
        return {**params, "error": repr(e)[:200]}


def grid_iter(grid: dict):
    keys = list(grid)
    for vals in itertools.product(*[grid[k] for k in keys]):
        yield dict(zip(keys, vals))


def sweep(name: str, grid: dict | None = None, jobs=2, cost="base", top=15, max_configs=400):
    mod = importlib.import_module(name)
    grid = grid or mod.GRID
    cfgs = list(grid_iter(grid))
    if len(cfgs) > max_configs:
        rng = np.random.default_rng(0)
        cfgs = [cfgs[i] for i in sorted(rng.choice(len(cfgs), max_configs, replace=False))]
        print(f"[sweep] sampled {max_configs} of {len(list(grid_iter(grid)))} configs", flush=True)
    out_dir = ROOT / "cand/results"
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    rows = []
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        for i, r in enumerate(ex.map(_job, [(name, c, cost) for c in cfgs])):
            rows.append(r)
            if i % 20 == 0:
                print(f"[sweep] {i + 1}/{len(cfgs)} {time.time() - t0:.0f}s", flush=True)
    df = pd.DataFrame(rows)
    if "tr_t" in df:
        # rank only configs with enough trades: a 2-trade config can have an absurd t-stat
        df["rank_t"] = np.where(df.get("tr_n", 0) >= MIN_N_RANK, df["tr_t"], -np.inf)
        df = df.sort_values("rank_t", ascending=False)
    df.to_csv(out_dir / f"{name}_train.csv", index=False)
    # validate the top configs (by train t-stat) once, base and harsh
    keys = list(grid)
    best = df[np.isfinite(df["rank_t"])].head(top) if "rank_t" in df else df.head(0)
    vrows = []
    for _, r in best.iterrows():
        p = {k: (r[k].item() if hasattr(r[k], "item") else r[k]) for k in keys}
        for c in ("base", "harsh"):
            ev = evaluate(name, p, cost=c)
            vrows.append({**p, "cost": c, **{f"tr_{k}": v for k, v in ev["train"].items()},
                          **{f"va_{k}": v for k, v in ev["valid"].items()}})
    vdf = pd.DataFrame(vrows)
    vdf.to_csv(out_dir / f"{name}_valid.csv", index=False)
    return df, vdf


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--cost", default="base")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--max", type=int, default=400)
    ap.add_argument("--grid", default=None, help="JSON grid overriding the module GRID")
    a = ap.parse_args()
    g = json.loads(a.grid) if a.grid else None
    df, vdf = sweep(a.name, g, a.jobs, a.cost, a.top, a.max)
    pd.set_option("display.width", 250)
    print(df.head(20).to_string())
    print(vdf.to_string())
