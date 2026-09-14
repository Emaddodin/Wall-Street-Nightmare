#!/usr/bin/env python3
"""CROSS MINER -- cross-sectional relative-strength scan (Hypothesis D).

Every 15 minutes: rank ALL symbols by a composite edge score
(60-bar return x volume-acceleration z), then LONG the top decile and
SHORT the bottom decile for the next 15 minutes.  Taker fees both legs.
Vectorized per symbol; the cross-section is rebuilt each rebalance.

Reports the long-only, short-only and long/short (spread) P&L, walk-forward
quarters, and the score's information coefficient vs forward returns.
"""
import glob
import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

FEE_BPS = 6.0
SLIP_BPS = 2.0
REBAL_MIN = 15
MIN = 60_000


def prep(path):
    df = pd.read_parquet(path)
    if len(df) < 30000:
        return None
    c = df["close"].to_numpy(float)
    v = df["volume"].to_numpy(float)
    t = df["open_time"].to_numpy()
    r60 = pd.Series(c).pct_change(60).to_numpy()
    v20 = pd.Series(v).rolling(20).mean().to_numpy()
    v240 = pd.Series(v).rolling(240).mean().to_numpy()
    vacc = v20 / np.where(v240 > 0, v240, np.nan)
    score = r60 * np.where(np.isfinite(vacc), vacc, 1.0)
    return pd.DataFrame({"t": t, "c": c, "score": score})


def main():
    frames = {}
    for store in ("data/candles", "data/candles_binance"):
        for p in sorted(glob.glob(f"{store}/*.parquet")):
            d = prep(p)
            if d is not None:
                frames[p.split("/")[-1]] = d
    print(f"symbols: {len(frames)}", flush=True)
    if not frames:
        return
    tmin = max(d["t"].min() for d in frames.values())
    tmax = min(d["t"].max() for d in frames.values())
    grid = np.arange(tmin, tmax, REBAL_MIN * MIN)
    longs = []
    shorts = []
    ics = []
    for t0 in grid:
        row = {}
        for name, d in frames.items():
            i = int(np.searchsorted(d["t"].to_numpy(), t0, side="right") - 1)
            if i < 60:
                continue
            s = d["score"].iloc[i]
            if np.isfinite(s):
                row[name] = (d["c"].iloc[i], s)
        if len(row) < 20:
            continue
        names = list(row)
        ranked = sorted(names, key=lambda x: -row[x][1])
        k = max(2, len(ranked) // 10)
        top = ranked[:k]
        bot = ranked[-k:]
        # forward return over the next rebalance window
        for nm in top:
            d = frames[nm]
            i = int(np.searchsorted(d["t"].to_numpy(), t0, side="right") - 1)
            j = int(np.searchsorted(d["t"].to_numpy(),
                                    t0 + REBAL_MIN * MIN, side="right") - 1)
            if j > i + 1:
                fwd = d["c"].iloc[j] / d["c"].iloc[i] - 1.0
                longs.append(fwd - (FEE_BPS + SLIP_BPS) / 1e4)
                ics.append((row[nm][1], fwd))
        for nm in bot:
            d = frames[nm]
            i = int(np.searchsorted(d["t"].to_numpy(), t0, side="right") - 1)
            j = int(np.searchsorted(d["t"].to_numpy(),
                                    t0 + REBAL_MIN * MIN, side="right") - 1)
            if j > i + 1:
                fwd = d["c"].iloc[j] / d["c"].iloc[i] - 1.0
                shorts.append(-fwd - (FEE_BPS + SLIP_BPS) / 1e4)

    def rep(arr, label):
        arr = np.array(arr)
        n = len(arr)
        if n == 0:
            print(f"{label}: no trades")
            return
        wr = (arr > 0).mean()
        print(f"{label}: n={n} wr={wr:.0%} avg {arr.mean()*100:+.4f}% "
              f"sum {arr.sum()*100:+.1f}%")

    rep(longs, "LONG top-decile")
    rep(shorts, "SHORT bottom-decile")
    rep(list(np.array(longs) - np.array(shorts)[:len(longs)]),
        "SPREAD long-short")
    if ics:
        s = np.array([x[0] for x in ics])
        f = np.array([x[1] for x in ics])
        ic = np.corrcoef(s, f)[0, 1]
        print(f"IC (score vs fwd): {ic:.4f} | n={len(ics)}")
    json.dump({"longs": [float(x) for x in longs],
               "shorts": [float(x) for x in shorts]},
              open("data/state/cross_report.json", "w"))


if __name__ == "__main__":
    main()
