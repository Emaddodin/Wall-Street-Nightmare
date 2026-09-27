"""
xau_alpha/recon/synthesis_checks.py
Spot checks behind recon/SYNTHESIS.md (single process, about 1-2 min, TRAIN+VALID only; TEST untouched).

  A. Runner (lib/ref_runner.py port, deployed config 48/3.5/4/2) on the 10-second bid/ask simulator under several cost
     models, to reconcile codebase_inventory.md (PF 1.07) with news_llm.md (PF 0.59/0.64) and HANDOFF.md.
  B. Cost-to-range ratio by UTC hour under the LiteFinance BASE cost model from broker_costs.md.
  C. Flip Monte Carlo from $13 with a 1-oz minimum lot AND the 1:500 margin barrier (a 0.01 lot cannot be opened
     once equity < ~$8.30 at $4,150 gold), which recon/microlot_ruin.py does not model.

Run: python3 xau_alpha/recon/synthesis_checks.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from data import load_m1  # noqa: E402
from ref_runner import runner_orders  # noqa: E402
from sim import COSTS, Cost, simulate, split_stats  # noqa: E402

H_BASE = (0.25,) + (0.08,) * 6 + (0.0,) * 15 + (0.25, 0.25)
H_HARSH = (0.20,) + (0.0,) * 21 + (0.20, 0.20)
COST_MODELS = {
    "mid (true zero: spread 0, slip 0)": Cost(spread_mult=0.0, spread_floor=0.0, slip_entry=0.0, slip_exit=0.0),
    "flat 0.22 spread, no slip": Cost(spread_mult=0.0, spread_floor=0.22, slip_entry=0.0, slip_exit=0.0),
    "LF_BASE (broker_costs.md)": Cost(spread_mult=0.32, spread_floor=0.22, slip_entry=0.05, slip_exit=0.10,
                                      com_rt_lot=0.0, hour_add=H_BASE),
    "LF_BASE + $5/lot commission": Cost(spread_mult=0.32, spread_floor=0.22, slip_entry=0.05, slip_exit=0.10,
                                        com_rt_lot=5.0, hour_add=H_BASE),
    "codebase 'mid' (0.18 + 0.15/0.25)": Cost(spread_mult=0.0, spread_floor=0.18, slip_entry=0.15, slip_exit=0.25),
    "sim COSTS['zero'] (= raw Dukascopy spread)": COSTS["zero"],
    "sim COSTS['base']": COSTS["base"],
    "LF_HARSH (broker_costs.md)": Cost(spread_mult=1.0, spread_floor=0.30, slip_entry=0.20, slip_exit=0.40,
                                       com_rt_lot=7.0, hour_add=H_HARSH),
}


def part_a():
    m1 = load_m1()
    test0 = int(pd.Timestamp("2026-06-01", tz="UTC").value // 1_000_000)
    orders = [o for o in runner_orders(48, 3.5, 4.0, 2.0, 0.3, 90, m1=m1) if o["t"] < test0]
    print(f"A. Runner orders (train+valid): {len(orders)}")
    for name, c in COST_MODELS.items():
        s = split_stats(simulate(orders, c))
        print(f"  {name:45s} " + "  ".join(
            f"{k}: n={v['n']} PF={v['pf']} avg={v['avg_pts']:+.2f}pt" for k, v in s.items()))


def part_b():
    m = load_m1()
    m = m[m["split"] < 2].copy()
    m["rng"] = m["h"] - m["l"]
    g = m.groupby("hour").agg(rng=("rng", "median"), spr=("spr", "median"))
    lf_spread = np.maximum(0.22, 0.32 * g["spr"].values) + np.asarray(H_BASE)[g.index.values]
    g["lf_rt"] = lf_spread + 0.05 + 0.10 + 0.05          # spread + slips + $5/lot commission per oz
    g["lf_rt/rng"] = g["lf_rt"] / g["rng"]
    g["duka_rt/rng"] = (g["spr"] + 0.13) / g["rng"]
    print("B. median M1 range, Dukascopy spread, LiteFinance round trip and cost/range by UTC hour (train+valid)")
    print(g.round(2).to_string())


def flip_mc(p, b, D, c, e0=13.0, target=100.0, margin_per_oz=8.30, risk_frac=0.10, paths=40000, max_n=5000, seed=11):
    """1-oz minimum; size up to floor(E*risk_frac/D) oz once affordable; a trade needs E >= oz*margin_per_oz."""
    rng = np.random.default_rng(seed)
    E = np.full(paths, e0)
    alive = np.ones(paths, bool)
    done = np.zeros(paths, bool)
    for _ in range(max_n):
        act = alive & ~done
        if not act.any():
            break
        oz = np.maximum(1.0, np.floor(E * risk_frac / D))
        oz = np.minimum(oz, np.floor(E / margin_per_oz))
        alive &= ~(act & (oz < 1))
        act = alive & ~done
        win = rng.random(paths) < p
        E = np.where(act, E + np.where(win, b * D - c, -D - c) * oz, E)
        done |= act & (E >= target)
    return float(done.mean()), float((~alive).mean())


def part_c():
    print("C. Flip MC: $13 -> $100, 1-oz min lot, margin $8.30/oz (1:500 at $4,150), cost $0.42/oz round trip")
    for D in (2.0, 3.0, 4.0):
        for ev in (0.0, 0.125, 0.25):
            b = 1.5
            p = (1 + ev) / (1 + b)
            hit, ruin = flip_mc(p, b, D, 0.42)
            print(f"  stop ${D:.0f}  b=1.5  gross EV {ev:+.3f}R: P(hit $100)={hit:.3f}  P(ruin)={ruin:.3f}")
    for b in (1.0, 2.0, 3.0, 5.0):
        for ev in (0.0, 0.125, 0.25):
            p = (1 + ev) / (1 + b)
            hit, ruin = flip_mc(p, b, 3.0, 0.42)
            print(f"  stop $3  b={b}  gross EV {ev:+.3f}R (p={p:.3f}): P(hit $100)={hit:.3f}  P(ruin)={ruin:.3f}")
    for e0 in (13, 20, 30, 50):
        hit, ruin = flip_mc(0.45, 1.5, 3.0, 0.42, e0=e0)
        print(f"  start ${e0}  stop $3  +0.125R: P(hit $100)={hit:.3f}  P(ruin)={ruin:.3f}")


if __name__ == "__main__":
    part_a()
    part_b()
    part_c()
