"""
cand/x2_valid_top.py - the sweep.py VALID step for the staged X2 searches: take the top-K configs by TRAIN t-stat
(TRAIN n >= min_n) from all stage CSVs of a family, de-duplicate, and evaluate each once on TRAIN+VALID at lf_base and
lf_harsh. Writes cand/results/<name>_valid.csv (and <name>_train.csv = all stages concatenated, de-duplicated).
This is a READ-ONLY confirmation step: the finalist was frozen from TRAIN before this ran.

    python3 cand/x2_valid_top.py <name> <grid keys comma-separated> [--top 10] [--min_n 150] [--defaults '<json>']
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "cand"))

from sweep import evaluate  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("keys")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--min_n", type=int, default=150)
    ap.add_argument("--defaults", default="{}", help="JSON: values for keys missing from early-stage CSVs")
    a = ap.parse_args()
    defaults = json.loads(a.defaults)
    res = ROOT / "cand/results"
    parts = []
    for f in sorted(res.glob(f"{a.name}_s*_train.csv")):
        d = pd.read_csv(f)
        d["stage"] = f.stem.split("_")[-2]
        parts.append(d)
    df = pd.concat(parts, ignore_index=True)
    keys = a.keys.split(",")
    for k in keys:
        if k not in df:
            df[k] = np.nan
        if k in defaults:
            df[k] = df[k].fillna(defaults[k])
    if a.name == "trend_pullback" and "ex" in df:
        df["ex"] = df["ex"].replace({"tr": "tr2"})          # 'tr' uses the default trm = 2: same config
    for k in ("N", "tf", "fresh", "W", "gap"):
        if k in keys:
            df[k] = df[k].astype(int)
    for k in ("sl", "sigma", "smin"):
        if k in keys:
            df[k] = df[k].astype(float)
    df["_key"] = df[keys].astype(str).agg("|".join, axis=1)
    df = df.drop_duplicates("_key").sort_values("tr_t", ascending=False)
    df.drop(columns="_key").to_csv(res / f"{a.name}_train.csv", index=False)
    best = df[df["tr_n"] >= a.min_n].head(a.top)
    rows = []
    for _, r in best.iterrows():
        p = {k: (r[k].item() if hasattr(r[k], "item") else r[k]) for k in keys if pd.notna(r[k])}
        for k in ("N", "tf", "fresh", "W", "gap"):
            if k in p:
                p[k] = int(p[k])
        for c in ("lf_base", "lf_harsh"):
            ev = evaluate(a.name, p, cost=c)
            rows.append({**p, "cost": c, **{f"tr_{k}": v for k, v in ev["train"].items()},
                         **{f"va_{k}": v for k, v in ev["valid"].items()},
                         "va_long_pf": ev.get("valid_long", {}).get("pf"), "va_long_n": ev.get("valid_long", {}).get("n"),
                         "va_short_pf": ev.get("valid_short", {}).get("pf"),
                         "va_short_n": ev.get("valid_short", {}).get("n")})
            print(p, c, ev["train"].get("pf"), ev["valid"], flush=True)
    pd.DataFrame(rows).to_csv(res / f"{a.name}_valid.csv", index=False)
    print("unique configs", len(df), flush=True)


if __name__ == "__main__":
    main()
