"""
cand/x2_stages.py - the staged TRAIN grids for the X2 families, run through lib/sweep.py (2 workers).

    python3 cand/x2_stages.py <stage_key> [--top N]

Stage 1 of each family is the module GRID (run as `python3 lib/sweep.py <name> --jobs 2 --top 0`, output copied to
cand/results/<name>_s1_train.csv). Stage 2 grids are below; parents were chosen by TRAIN t-stat only (no VALID look).
Output: cand/results/<name>_<stage>_train.csv (+ _valid.csv only when --top > 0).
"""
import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "cand"))

import sweep  # noqa: E402

STAGES = {
    # htf_breakout stage 1 top-5 by TRAIN t were all H1 with N in {34, 55}, sl 1.5/3, exits ch2 / r2, both holds.
    # Stage 2 refines around them: N (3) x sl (3) x ex (4) x hold (2) x fresh (2) = 144.
    "hb_s2": ("htf_breakout", {
        "tf": [60], "N": [34, 45, 55], "sl": [1.0, 1.5, 3.0], "ex": ["ch1.5", "ch2", "ch2.5", "r2"],
        "hold": ["eod", "multi"], "fresh": [0, 1]}),
    # trend_pullback stage 1 top-5 by TRAIN t: 4 of 5 are zone=fvg, trig=eng, sess=day (W 10/30, ex tr/r3).
    # Stage 2: trend (2) x ex (4) x sigma (2) x smin (2) x gap (2) x W (2) = 128, zone/trig/sess fixed.
    "tp_s2": ("trend_pullback", {
        "trend": ["ema", "don4"], "zone": ["fvg"], "trig": ["eng"], "sess": ["day"],
        "ex": ["tr2", "tr4", "r3", "r5"], "sigma": [0.3, 1.0], "smin": [1.0, 2.0], "gap": [15, 60], "W": [10, 30]}),
}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage")
    ap.add_argument("--top", type=int, default=0)
    a = ap.parse_args()
    name, grid = STAGES[a.stage]
    sweep.sweep(name, grid, jobs=2, top=a.top, max_configs=400)
    st = a.stage.split("_")[1]
    res = ROOT / "cand/results"
    shutil.copy(res / f"{name}_train.csv", res / f"{name}_{st}_train.csv")
    if a.top:
        shutil.copy(res / f"{name}_valid.csv", res / f"{name}_{st}_valid.csv")
    print("stage done", a.stage, flush=True)
