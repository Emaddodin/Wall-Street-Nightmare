"""
cand/fb_rf_stages.py - stage-2 driver and finalist diagnostics for failed_breakout (H3) and range_fade (H10).

Stage 1 runs through the engine CLI (python3 lib/sweep.py <name> --jobs 2); its CSVs are copied to
cand/results/<name>_s1_{train,valid}.csv before stage 2 overwrites the default names.

    python3 cand/fb_rf_stages.py fb_stage2        # top 5 H3 stage-1 configs x sigma x TP x tmax (12 each)
    python3 cand/fb_rf_stages.py rf_stage2        # top 3 H10 stage-1 configs x sess x sigma x tmax (8 each)
    python3 cand/fb_rf_stages.py diag <name> '<json params>'   # finalist tables, nulls, monthly R (TRAIN+VALID)

Selection uses TRAIN only (sweep._job: orders at/after 2026-06-01 are dropped, stats on split 0). VALID is read
only for the finalist and the engine's top-15 validation. At most 2 worker processes.
"""
import itertools
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
import sweep  # noqa: E402
import nulltest  # noqa: E402
from sim import stats  # noqa: E402

RES = ROOT / "cand/results"
FB_S1 = ["w", "K", "N", "phi", "beta", "R", "mode"]
RF_S1 = ["w", "P", "wid", "K_edge", "tp", "trig"]


def _clean(v):
    return v.item() if hasattr(v, "item") else v


def _run(name, cfgs, out_csv, stage):
    t0 = time.time()
    rows = []
    with ProcessPoolExecutor(max_workers=2) as ex:
        for i, r in enumerate(ex.map(sweep._job, [(name, c, "base") for c in cfgs])):
            rows.append({**r, "stage": stage})
            if i % 10 == 0:
                print(f"[{stage}] {i + 1}/{len(cfgs)} {time.time() - t0:.0f}s", flush=True)
    df = pd.DataFrame(rows)
    df["rank_t"] = np.where(df.get("tr_n", 0) >= sweep.MIN_N_RANK, df["tr_t"], -np.inf)
    df = df.sort_values("rank_t", ascending=False)
    df.to_csv(out_csv, index=False)
    pd.set_option("display.width", 250)
    print(df.head(15).to_string(), flush=True)
    return df


def top_parents(csv, keys, n):
    df = pd.read_csv(csv)
    df = df[np.isfinite(df["rank_t"])].sort_values("rank_t", ascending=False)
    return [{k: _clean(r[k]) for k in keys} for _, r in df.head(n).iterrows()]


def fb_stage2():
    parents = top_parents(RES / "failed_breakout_s1_train.csv", FB_S1, 5)
    cfgs = []
    for p in parents:
        for sigma, (tpm, tpr), tmax in itertools.product([0.1, 0.3], [("near", 1.0), ("r", 1.5), ("r", 2.0)],
                                                         [20, 45]):
            cfgs.append({**p, "sigma": sigma, "tp_mode": tpm, "tp_r": tpr, "tmax": tmax})
    print(f"[fb_s2] {len(parents)} parents, {len(cfgs)} configs", flush=True)
    return _run("failed_breakout", cfgs, RES / "failed_breakout_s2_train.csv", "s2")


def rf_stage2():
    parents = top_parents(RES / "range_fade_s1_train.csv", RF_S1, 3)
    cfgs = []
    for p in parents:
        for sess, sigma, tmax in itertools.product(["lonny", "all"], [0.3, 0.6], [30, 60]):
            cfgs.append({**p, "sess": sess, "sigma": sigma, "tmax": tmax})
    print(f"[rf_s2] {len(parents)} parents, {len(cfgs)} configs", flush=True)
    return _run("range_fade", cfgs, RES / "range_fade_s2_train.csv", "s2")


# ------------------------------------------------------------------------------------------------ diagnostics
def _pf(x):
    gw, gl = x[x > 0].sum(), -x[x < 0].sum()
    return round(float(gw / gl), 3) if gl > 0 else float("inf")


def diag(name, params, seeds=50):
    """Finalist tables on TRAIN+VALID only. Prints JSON (small) - never writes trade lists."""
    import importlib
    from data import load_m1
    mod = importlib.import_module(name)
    m1 = load_m1()
    out = {"name": name, "params": params}
    trs = {}
    for cost in ("base", "harsh", "mid", "duka_raw"):
        ev, tr = sweep.evaluate(name, params, cost=cost, return_trades=True)
        trs[cost] = tr
        out[cost] = {k: v for k, v in ev.items() if k != "params"}
    tr = trs["base"]
    t0 = tr[tr.split == 0]
    h = len(t0) // 2
    out["train_halves_pf"] = [stats(t0.iloc[:h]).get("pf"), stats(t0.iloc[h:]).get("pf")]
    out["train_t"] = round(float(t0.R.mean() / t0.R.std(ddof=1) * np.sqrt(len(t0))), 2) if len(t0) > 1 else None
    v0 = tr[tr.split == 1]
    out["valid_t"] = round(float(v0.R.mean() / v0.R.std(ddof=1) * np.sqrt(len(v0))), 2) if len(v0) > 1 else None
    # stop-distance distribution and flip share
    for sp, spn in ((0, "train"), (1, "valid")):
        x = tr[tr.split == sp]
        if len(x):
            out[f"{spn}_risk_q"] = [round(float(q), 2) for q in np.quantile(x.risk, [0.1, 0.5, 0.9])]
            out[f"{spn}_flip_share"] = round(float(((x.risk >= 1.2) & (x.risk <= 4.0)).mean()), 3)
    # exit reasons
    out["reasons"] = tr.groupby(["split", "reason"]).size().unstack(fill_value=0).to_dict(orient="index")
    # monthly R and pts (base) for TRAIN+VALID
    tr = tr.copy()
    tr["month"] = pd.to_datetime(tr["t_in"], unit="ms", utc=True).dt.strftime("%Y-%m")
    mon = tr.groupby("month").agg(n=("R", "size"), sum_R=("R", "sum"), avg_pts=("pnl", "mean"),
                                  pf=("pnl", _pf), avg_risk=("risk", "mean"))
    out["monthly"] = mon.round(3).reset_index().to_dict(orient="records")
    # drop best month (VALID and TRAIN separately)
    for sp, spn in ((0, "train"), (1, "valid")):
        x = tr[tr.split == sp]
        if len(x):
            best = x.groupby("month")["pnl"].sum().idxmax()
            out[f"{spn}_pf_drop_best_month"] = _pf(x[x.month != best].pnl.values)
    # random-direction null on VALID and TRAIN (lf_base)
    orders = [o for o in mod.orders(m1, **params) if o["t"] < sweep.TEST_MS]
    # same draws as nulltest.random_direction (seed 1000+s), one simulation per seed shared by both splits
    rel = nulltest._to_relative(orders)
    rows = {0: [], 1: []}
    for s in range(seeds):
        dirs = np.random.default_rng(1000 + s).choice([-1, 1], size=len(orders))
        x = sweep.run_orders(nulltest._apply_dir(rel, dirs), "base")
        for sp in (0, 1):
            rows[sp].append(stats(x[x.split == sp]) if len(x) else {"n": 0})
    for sp, spn in ((1, "valid"), (0, "train")):
        nd = pd.DataFrame(rows[sp])
        real = out["base"][spn]
        out[f"null_{spn}"] = {
            "seeds": seeds, "real_avg_pts": real.get("avg_pts"), "real_pf": real.get("pf"),
            "null_avg_pts_mean": round(float(nd.avg_pts.mean()), 3),
            "null_avg_pts_sd": round(float(nd.avg_pts.std()), 3),
            "null_pf_median": round(float(nd.pf.median()), 3),
            "p_value": round(float((1 + (nd.avg_pts >= real.get("avg_pts", -9e9)).sum()) / (1 + len(nd))), 3),
        }
    mir = sweep.run_orders(nulltest.mirror(orders), "base")
    out["mirror_base"] = {"train": stats(mir[mir.split == 0]), "valid": stats(mir[mir.split == 1])}
    print(json.dumps(out, default=str))
    return out


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "fb_stage2":
        fb_stage2()
    elif cmd == "rf_stage2":
        rf_stage2()
    elif cmd == "diag":
        diag(sys.argv[2], json.loads(sys.argv[3]), int(sys.argv[4]) if len(sys.argv) > 4 else 50)
