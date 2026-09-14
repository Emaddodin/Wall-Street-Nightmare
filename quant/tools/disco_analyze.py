"""Aggregate discovery results across symbols: rank features by pooled
edge, consistency (fraction of symbols agreeing on sign), and significance.

Usage:
  python3 quant/tools/disco_analyze.py data/research/disco_quick.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def pool(df: pd.DataFrame, min_n: int = 100) -> pd.DataFrame:
    """Pool per-symbol bucket edges per (feature, outcome)."""
    rows = []
    for (f, o), g in df.groupby(["feature", "outcome"]):
        g = g[g["n_eff"] >= min_n]
        if len(g) < 3:
            continue
        w = g["n_eff"].to_numpy(dtype=float)
        wsum = w.sum()
        if wsum <= 0:
            continue
        edge = float((g["edge"] * w).sum() / wsum)
        # pooled t: inverse-variance style on per-symbol edges
        consistency = float((np.sign(g["edge"]) == np.sign(edge)).mean())
        n_sym = len(g)
        mono = float(g["mono"].mean())
        rows.append({
            "feature": f, "outcome": o, "edge": edge,
            "consistency": consistency, "n_sym": n_sym,
            "mean_t": float(g["t"].mean()),
            "max_abs_t": float(g["t"].abs().max()),
            "mono": mono,
            "n_eff": int(wsum),
            "bin0": float((g["bin0_mean"] * w).sum() / wsum),
            "bin9": float((g["bin9_mean"] * w).sum() / wsum),
        })
    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values("edge", key=abs, ascending=False)
    return out.reset_index(drop=True)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: disco_analyze.py <disco.csv> [--outcome fwd_5]")
        return 1
    df = pd.read_csv(sys.argv[1])
    outcomes = df["outcome"].unique()
    print(f"rows={len(df)} symbols={df['symbol'].nunique()} "
          f"features={df['feature'].nunique()} outcomes={list(outcomes)}")
    for o in outcomes:
        sub = df[df["outcome"] == o]
        pooled = pool(sub)
        if len(pooled) == 0:
            continue
        print(f"\n===== outcome {o} =====")
        with pd.option_context("display.max_rows", 60, "display.width", 200,
                               "display.float_format", "{:.3f}".format):
            print(pooled[["feature", "edge", "consistency", "n_sym",
                          "mean_t", "max_abs_t", "mono", "n_eff",
                          "bin0", "bin9"]].head(40))
    return 0


if __name__ == "__main__":
    sys.exit(main())
