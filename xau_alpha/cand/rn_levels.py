"""
cand/rn_levels.py - H9 level-effect test: real round numbers vs placebo levels across a whole grid block.

For step in {10, 25} with K_app = 10 (the only blocks with n >= 60 on TRAIN), every (M, mu, sigma, tp, tmax) cell of
the stage-1 grid is run at place = 0 (real $10/$25 levels), 1 (L + 0.37 step) and 2 (L + 0.63 step), under mid
(zero cost: the gross barrier effect) and lf_base. No selection is done here: the output is the per-cell difference
real - placebo, pooled over cells. TEST (t >= 2026-06-01) is never simulated.

    python3 cand/rn_levels.py     -> cand/results/round_reject_levels.csv
"""
import itertools
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "cand"))

from data import TEST, _ms, load_m1  # noqa: E402
from sim import COSTS, simulate, stats  # noqa: E402
import round_reject as rr  # noqa: E402

TEST_MS = _ms(TEST[0])


def main():
    m1 = load_m1()
    rows = []
    cells = list(itertools.product([10, 25], [60, 240], [1.0, 2.0], [0.3, 0.6], [1.0, 1.5], [10, 20]))
    for step, M, mu, sigma, tp, tmax in cells:
        for place in (0, 1, 2):
            p = dict(step=step, M=M, mu=mu, sigma=sigma, tp=tp, tmax=tmax, K_app=10, place=place)
            o = [q for q in rr.orders(m1, **p) if q["t"] < TEST_MS]
            for c in ("mid", "base"):
                tr = simulate(o, COSTS[c])
                for sp, nm in ((0, "train"), (1, "valid")):
                    s = stats(tr[tr.split == sp]) if len(tr) else {"n": 0}
                    rows.append({**p, "cost": c, "split": nm, **{k: s.get(k) for k in
                                                                 ("n", "pf", "avg_pts", "avg_R", "wr")}})
        print(step, M, mu, sigma, tp, tmax, flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(ROOT / "cand/results/round_reject_levels.csv", index=False)
    piv = df.pivot_table(index=["step", "cost", "split"], columns="place", values=["avg_R", "pf", "n"],
                         aggfunc="median")
    pd.set_option("display.width", 250)
    print(piv.round(3).to_string())


if __name__ == "__main__":
    main()
