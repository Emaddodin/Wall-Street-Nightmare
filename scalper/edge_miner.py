#!/usr/bin/env python3
"""EDGE MINER (Branch B) -- discover the edge BEFORE any strategy exists.

Scans the full candle stores and asks, for every bar and a battery of
observable conditions: what happens over the next 1/3/5 candles?

    labels: fwd return (next 1/3/5 closes), MAE/MFE over the next 3 bars,
            P(touch +0.5% before -0.5%), P(touch -0.5% first)
features (all computable from OHLCV alone, no old-architecture logic):
    vol_z       1m volume vs same-minute-of-day 20d median
    rvol5       5-bar volume vs 60-bar volume
    body_rng    |close-open| / (high-low)
    up_wick     upper wick / range      down_wick  lower wick / range
    atr_z       current range vs 60-bar mean range (compression/expansion)
    accel       |return(5)| - |return(20)|/5  (return acceleration)
    rng_exp     range(3) / range(20) ratio
    ret5        prior 5-bar return (momentum)
    rng_pos     position in the last 96-bar range

Output: data/state/edge_report.json -- per feature, bucketed P(next3>0),
P(next3>+0.5%), P(next3<-0.5%), avg fwd ret, avg MFE, avg MAE, n.
A real edge = a bucket whose probability differs from the base rate by a
wide margin with a large n.  Nothing else counts.
"""
import glob
import json
import math
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

MIN = 60_000


def labels(df: pd.DataFrame, n3: int = 3):
    c = df["close"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    n = len(df)
    fwd3 = np.full(n, np.nan)
    mfe = np.full(n, np.nan)
    mae = np.full(n, np.nan)
    up_first = np.full(n, np.nan)
    for i in range(n - n3):
        base = c[i]
        seg_h = h[i + 1:i + 1 + n3]
        seg_l = l[i + 1:i + 1 + n3]
        fwd3[i] = c[i + n3] / base - 1.0
        mfe[i] = (seg_h.max() - base) / base
        mae[i] = (seg_l.min() - base) / base
        up_first[i] = 1.0 if (seg_h.max() - base) >= (base - seg_l.min()) else 0.0
    return fwd3, mfe, mae, up_first


def features(df: pd.DataFrame):
    c = df["close"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    o = df["open"].to_numpy(float)
    v = df["volume"].to_numpy(float)
    t = df["open_time"].to_numpy()
    n = len(df)
    rng = h - l
    body = np.abs(c - o)
    f = {}
    f["body_rng"] = body / np.where(rng > 0, rng, np.nan)
    f["up_wick"] = (h - np.maximum(o, c)) / np.where(rng > 0, rng, np.nan)
    f["down_wick"] = (np.minimum(o, c) - l) / np.where(rng > 0, rng, np.nan)
    mean60 = pd.Series(rng).rolling(60, min_periods=20).mean().to_numpy()
    f["atr_z"] = rng / np.where(mean60 > 0, mean60, np.nan)
    r5 = pd.Series(c).pct_change(5).to_numpy()
    r20 = pd.Series(c).pct_change(20).to_numpy()
    f["ret5"] = r5
    f["accel"] = np.abs(r5) - np.abs(r20) / 4.0
    vol5 = pd.Series(v).rolling(5, min_periods=3).mean().to_numpy()
    vol60 = pd.Series(v).rolling(60, min_periods=20).mean().to_numpy()
    f["rvol5"] = vol5 / np.where(vol60 > 0, vol60, np.nan)
    rng3 = pd.Series(rng).rolling(3, min_periods=2).mean().to_numpy()
    rng20 = pd.Series(rng).rolling(20, min_periods=10).mean().to_numpy()
    f["rng_exp"] = rng3 / np.where(rng20 > 0, rng20, np.nan)
    hi96 = pd.Series(h).rolling(96, min_periods=48).max().to_numpy()
    lo96 = pd.Series(l).rolling(96, min_periods=48).min().to_numpy()
    f["rng_pos"] = (c - lo96) / np.where(hi96 - lo96 > 0, hi96 - lo96, np.nan)
    mod = (t // MIN) % 1440
    med = pd.Series(v).groupby(mod).transform("median").to_numpy()
    f["vol_z"] = (v - med) / np.where(med > 0, med, np.nan)
    return f


def bucket_report(feat: np.ndarray, fwd3, mfe, mae, up_first, n_buck=5):
    mask = np.isfinite(feat) & np.isfinite(fwd3)
    if mask.sum() < 500:
        return None
    qs = np.nanquantile(feat[mask], np.linspace(0, 1, n_buck + 1))
    out = []
    for i in range(n_buck):
        lo, hi = qs[i], qs[i + 1]
        m = mask & (feat >= lo) & (feat <= hi)
        if m.sum() < 100:
            continue
        r = fwd3[m]
        out.append({
            "lo": round(float(lo), 4), "hi": round(float(hi), 4),
            "n": int(m.sum()),
            "p_up": round(float((r > 0).mean()), 3),
            "p_big_up": round(float((r > 0.005).mean()), 3),
            "p_big_dn": round(float((r < -0.005).mean()), 3),
            "avg_ret": round(float(np.nanmean(r)), 5),
            "avg_mfe": round(float(np.nanmean(mfe[m])), 5),
            "avg_mae": round(float(np.nanmean(mae[m])), 5),
        })
    return out if len(out) >= 3 else None


def main() -> None:
    all_f = {}
    all_l = {}
    total_rows = 0
    for store in ("data/candles", "data/candles_binance"):
        for p in sorted(glob.glob(f"{store}/*.parquet")):
            df = pd.read_parquet(p)
            if len(df) < 5000:
                continue
            df = df[::2].reset_index(drop=True)   # sample every 2nd bar
            f = features(df)
            fwd3, mfe, mae, up = labels(df)
            for k, v in f.items():
                all_f.setdefault(k, []).append(v)
            all_l.setdefault("fwd3", []).append(fwd3)
            all_l.setdefault("mfe", []).append(mfe)
            all_l.setdefault("mae", []).append(mae)
            all_l.setdefault("up_first", []).append(up)
            total_rows += len(df)
    print(f"rows sampled: {total_rows}", flush=True)
    fwd3 = np.concatenate(all_l["fwd3"])
    mfe = np.concatenate(all_l["mfe"])
    mae = np.concatenate(all_l["mae"])
    up = np.concatenate(all_l["up_first"])
    base = np.isfinite(fwd3)
    print(f"base rates: P(next3>0)={np.nanmean(fwd3[base] > 0):.3f} "
          f"P(>+0.5%)={np.nanmean(fwd3[base] > 0.005):.3f} "
          f"P(<-0.5%)={np.nanmean(fwd3[base] < -0.005):.3f} "
          f"avgMFE={np.nanmean(mfe[base]):.4f} avgMAE={np.nanmean(mae[base]):.4f}",
          flush=True)
    report = {"base": {
        "p_up": float(np.nanmean(fwd3[base] > 0)),
        "p_big_up": float(np.nanmean(fwd3[base] > 0.005)),
        "p_big_dn": float(np.nanmean(fwd3[base] < -0.005)),
        "avg_mfe": float(np.nanmean(mfe[base])),
        "avg_mae": float(np.nanmean(mae[base])),
        "n": int(base.sum())}}
    for k in sorted(all_f):
        feat = np.concatenate(all_f[k])
        rep = bucket_report(feat, fwd3, mfe, mae, up)
        if rep:
            report[k] = rep
            best = max(rep, key=lambda r: r["p_big_up"] - r["p_big_dn"])
            print(f"{k:10s}: best bucket lo={best['lo']}..{best['hi']} "
                  f"n={best['n']} P(+0.5%)={best['p_big_up']} "
                  f"P(-0.5%)={best['p_big_dn']}", flush=True)
    with open("data/state/edge_report.json", "w") as fh:
        json.dump(report, fh, indent=1)
    print("saved data/state/edge_report.json")


if __name__ == "__main__":
    main()
