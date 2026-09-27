"""
cand/x2_trainsplit.py - TRAIN-ONLY long/short and beta diagnostic for X2 configs (no VALID numbers are computed).

    python3 cand/x2_trainsplit.py <module> '<json list of param dicts>' [--cost lf_base]

For each config: TRAIN stats for all / long / short at `cost` and at mid, plus the average "beta" P&L a trade of the
same direction and holding time would earn from the TRAIN-average drift, and the beta-adjusted average at mid.
Used to decide whether a TRAIN edge is just long exposure in the 2025 bull market before any VALID look.
"""
import argparse
import importlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "cand"))

from data import VALID, _ms, load_m1  # noqa: E402
from sim import stats  # noqa: E402
from sweep import run_orders  # noqa: E402

VALID_MS = _ms(VALID[0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("module")
    ap.add_argument("plist")
    ap.add_argument("--cost", default="lf_base")
    a = ap.parse_args()
    M = importlib.import_module(a.module)
    m1 = load_m1()
    s = m1[m1["split"] == 0]
    mu = float((s["c"].values[-1] - s["c"].values[0]) / ((s["ts"].values[-1] - s["ts"].values[0]) / 60_000))
    print(f"TRAIN drift {mu:.5f} $/min", flush=True)
    for p in json.loads(a.plist):
        od = [o for o in M.orders(m1, **p) if o["t"] < VALID_MS]          # TRAIN orders only
        line = {"p": p}
        for cost in (a.cost, "mid"):
            tr = run_orders(od, cost)
            tr = tr[tr.split == 0]
            for nm, msk in (("all", np.ones(len(tr), bool)), ("L", tr.d.values > 0), ("S", tr.d.values < 0)):
                st = stats(tr[msk])
                line[f"{cost}_{nm}"] = (st.get("n"), st.get("pf"), st.get("avg_pts"))
            if cost == "mid":
                hold = (tr["t_out"] - tr["t_in"]).values / 60_000
                beta = tr["d"].values * mu * hold
                for nm, msk in (("L", tr.d.values > 0), ("S", tr.d.values < 0)):
                    line[f"beta_{nm}"] = (round(float(beta[msk].mean()), 2),
                                          round(float((tr["pnl"].values - beta)[msk].mean()), 2))
        print(json.dumps(line), flush=True)


if __name__ == "__main__":
    main()
