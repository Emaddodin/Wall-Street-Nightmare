#!/usr/bin/env python3
"""PATTERN MINER -- systematic edge discovery over candle n-grams.

Encodes every 1m bar into a small discrete state:
    dir      : body sign (up/down)
    body     : |body|/range tercile (0..2)
    vol      : volume vs same-minute median tercile (0..2)
    wick     : dominant-wick side vs direction (with/against)

Then counts, for every 3-bar state SEQUENCE, the forward statistics:
    P(next3 > 0), P(next3 >= +0.5%), P(next3 <= -0.5%), avg MFE/MAE, n.

Patterns with n >= 500 and a wide P(+0.5%) - P(-0.5%) gap are the raw
material for the new strategy's triggers.  This is discovery, not tuning.
"""
import glob
import json
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

MIN = 60_000


def encode(df: pd.DataFrame):
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    v = df["volume"].to_numpy(float)
    t = df["open_time"].to_numpy()
    rng = h - l
    body = c - o
    dirs = (body > 0).astype(np.int8)
    body_r = np.abs(body) / np.where(rng > 0, rng, np.nan)
    # terciles of body/range and volume per symbol
    body_t = pd.qcut(pd.Series(body_r[body_r.notna()]), 3, labels=False,
                     duplicates="drop").reindex(pd.Series(body_r).index)
    # fallback for symbols where qcut fails
    if body_t.isna().all():
        body_t = pd.cut(pd.Series(body_r), 3, labels=False)
    body_t = body_t.fillna(1).astype(np.int8).to_numpy()
    mod = (t // MIN) % 1440
    med = pd.Series(v).groupby(mod).transform("median").to_numpy()
    vz = (v - med) / np.where(med > 0, med, np.nan)
    vol_t = pd.qcut(pd.Series(vz[vz.notna()]), 3, labels=False,
                    duplicates="drop").reindex(pd.Series(vz).index)
    if vol_t.isna().all():
        vol_t = pd.cut(pd.Series(vz), 3, labels=False)
    vol_t = vol_t.fillna(1).astype(np.int8).to_numpy()
    up_w = (h - np.maximum(o, c)) / np.where(rng > 0, rng, np.nan)
    dn_w = (np.minimum(o, c) - l) / np.where(rng > 0, rng, np.nan)
    wick = np.where(up_w > dn_w, 1, np.where(dn_w > up_w, 0, 2)).astype(np.int8)
    state = dirs * 18 + body_t * 6 + vol_t * 2 + wick   # 0..71
    return state


def main():
    counts = defaultdict(int)
    wins_up = defaultdict(int)
    big_up = defaultdict(int)
    big_dn = defaultdict(int)
    mfe = defaultdict(float)
    mae = defaultdict(float)
    n_bars = 0
    for store in ("data/candles", "data/candles_binance"):
        for p in sorted(glob.glob(f"{store}/*.parquet")):
            df = pd.read_parquet(p)
            if len(df) < 20000:
                continue
            st = encode(df)
            c = df["close"].to_numpy(float)
            h = df["high"].to_numpy(float)
            l = df["low"].to_numpy(float)
            n_bars += len(df)
            n = len(st)
            for i in range(60, n - 4):
                key = (int(st[i - 2]), int(st[i - 1]), int(st[i]))
                base = c[i]
                fwd = c[i + 3] / base - 1.0
                seg_h = h[i + 1:i + 4]
                seg_l = l[i + 1:i + 4]
                counts[key] += 1
                if fwd > 0:
                    wins_up[key] += 1
                if fwd >= 0.005:
                    big_up[key] += 1
                if fwd <= -0.005:
                    big_dn[key] += 1
                mfe[key] += (seg_h.max() - base) / base
                mae[key] += (seg_l.min() - base) / base
    print(f"bars: {n_bars} | distinct patterns: {len(counts)}", flush=True)
    rows = []
    for key, n in counts.items():
        if n < 500:
            continue
        pu = big_up[key] / n
        pd_ = big_dn[key] / n
        rows.append({
            "pattern": list(key), "n": n,
            "p_up": round(wins_up[key] / n, 3),
            "p_big_up": round(pu, 3), "p_big_dn": round(pd_, 3),
            "gap": round(pu - pd_, 3),
            "avg_mfe": round(mfe[key] / n, 5),
            "avg_mae": round(mae[key] / n, 5),
        })
    rows.sort(key=lambda r: -r["gap"])
    print("top patterns by P(+0.5%)-P(-0.5%) gap:")
    for r in rows[:10]:
        print(f"  {r['pattern']} n={r['n']:6d} up={r['p_up']} "
              f"bigup={r['p_big_up']} bigdn={r['p_big_dn']} "
              f"mfe={r['avg_mfe']} mae={r['avg_mae']}")
    with open("data/state/patterns.json", "w") as fh:
        json.dump({"n_bars": n_bars, "patterns": rows[:500]}, fh, indent=1)
    print(f"saved {len(rows[:500])} patterns -> data/state/patterns.json")


if __name__ == "__main__":
    main()
