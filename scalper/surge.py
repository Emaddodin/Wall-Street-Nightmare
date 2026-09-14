#!/usr/bin/env python3
"""SURGE -- Branch-B candidate, built ONLY on the edge-miner's findings.

Architecture is independent of the old engine: a lightweight vectorized
scalper over the raw stores.

The measured edge (7.7M bars): volume surge (vol_z >= surge_mult) makes
P(|move| >= 0.5% within 3 candles) ~2.4x the base rate, and 5-bar momentum
+ acceleration tilt the direction.  Surge trades exactly that:

    ENTER  next bar open after: vol_z >= surge_mult AND |ret5| >= mom_min
           direction = sign(ret5) (momentum tilt; optional accel confirm)
    EXIT   first of: TP +tp_pct / SL -sl_pct / 3-candle time stop
           taker fees + adverse slippage on both legs
    SIZE   1x notional per signal (leverage is an operator dial)

Reports: n, wr, avg win %, avg loss %, expectancy %, pf, per-symbol
robustness, and the walk-forward window breakdown.
"""
import glob
import json
import math
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

FEE_BPS = 6.0
SLIP_BPS = 2.0
MIN = 60_000


def run_symbol(df: pd.DataFrame, surge_mult=2.0, mom_min=0.001,
               tp_pct=0.005, sl_pct=0.003, max_bars=3, accel_confirm=False):
    c = df["close"].to_numpy(float)
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    v = df["volume"].to_numpy(float)
    t = df["open_time"].to_numpy()
    n = len(df)
    mod = (t // MIN) % 1440
    med = pd.Series(v).groupby(mod).transform("median").to_numpy()
    vol_z = (v - med) / np.where(med > 0, med, np.nan)
    r5 = pd.Series(c).pct_change(5).to_numpy()
    r20 = pd.Series(c).pct_change(20).to_numpy()
    accel = np.abs(r5) - np.abs(r20) / 4.0
    trades = []
    i = 60
    while i < n - 1 - max_bars:
        vz = vol_z[i]
        if not np.isfinite(vz) or vz < surge_mult:
            i += 1
            continue
        m = r5[i]
        if not np.isfinite(m) or abs(m) < mom_min:
            i += 1
            continue
        if accel_confirm and (not np.isfinite(accel[i]) or accel[i] <= 0):
            i += 1
            continue
        direction = 1 if m > 0 else -1
        entry = c[i + 1]          # next bar open
        cost = entry * (FEE_BPS + SLIP_BPS) / 1e4
        exit_px = entry
        reason = "TIME"
        for k in range(1, max_bars + 1):
            j = i + 1 + k
            if direction == 1:
                if h[j] >= entry * (1 + tp_pct):
                    exit_px = entry * (1 + tp_pct)
                    reason = "TP"
                    break
                if l[j] <= entry * (1 - sl_pct):
                    exit_px = entry * (1 - sl_pct)
                    reason = "SL"
                    break
            else:
                if l[j] <= entry * (1 - tp_pct):
                    exit_px = entry * (1 - tp_pct)
                    reason = "TP"
                    break
                if h[j] >= entry * (1 + sl_pct):
                    exit_px = entry * (1 + sl_pct)
                    reason = "SL"
                    break
            exit_px = c[j]
        gross = direction * (exit_px / entry - 1.0)
        net = gross - cost / entry
        trades.append({"ts": int(t[i]), "dir": direction, "gross": gross,
                       "net": net, "reason": reason})
        i = j + 1
    return trades


def stats(trs):
    n = len(trs)
    if n == 0:
        return None
    wins = [t for t in trs if t["net"] > 0]
    wr = len(wins) / n
    avg_w = sum(t["net"] for t in wins) / len(wins) if wins else 0
    losses = [t for t in trs if t["net"] <= 0]
    avg_l = sum(t["net"] for t in losses) / len(losses) if losses else 0
    gw = sum(t["net"] for t in wins)
    gl = -sum(t["net"] for t in losses)
    return {"n": n, "wr": round(wr, 3),
            "avg_win": round(avg_w * 100, 3), "avg_loss": round(avg_l * 100, 3),
            "expectancy_pct": round(sum(t["net"] for t in trs) / n * 100, 4),
            "pf": round(gw / gl, 2) if gl > 0 else 99.0}


def run_symbol_ignition(df, surge_mult=2.0, mom_min=0.001,
                         tp_pct=0.005, max_bars=3):
    c = df["close"].to_numpy(float)
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    v = df["volume"].to_numpy(float)
    t = df["open_time"].to_numpy()
    n = len(df)
    mod = (t // MIN) % 1440
    med = pd.Series(v).groupby(mod).transform("median").to_numpy()
    vol_z = (v - med) / np.where(med > 0, med, np.nan)
    r5 = pd.Series(c).pct_change(5).to_numpy()
    trades = []
    i = 60
    while i < n - 2 - max_bars:
        vz = vol_z[i]
        if not np.isfinite(vz) or vz < surge_mult:
            i += 1
            continue
        m = r5[i]
        if not np.isfinite(m) or abs(m) < mom_min:
            i += 1
            continue
        direction = 1 if m > 0 else -1
        level = h[i] if direction == 1 else l[i]
        sl = l[i] if direction == 1 else h[i]
        filled = False
        for k in range(1, max_bars + 1):
            j = i + k
            if direction == 1 and h[j] >= level:
                entry, filled = level, True
                break
            if direction == -1 and l[j] <= level:
                entry, filled = level, True
                break
            if direction == 1 and l[j] <= sl or direction == -1 and h[j] >= sl:
                break
        if not filled:
            i += 1
            continue
        # maker entry: 2bps + no slip; exit taker
        cost_in = entry * 2.0 / 1e4
        exit_px = entry
        reason = "TIME"
        for k in range(1, max_bars + 1):
            j2 = i + 1 + k
            if direction == 1:
                if l[j2] <= sl:
                    exit_px, reason = sl, "SL"
                    break
                if h[j2] >= entry * (1 + tp_pct):
                    exit_px, reason = entry * (1 + tp_pct), "TP"
                    break
            else:
                if h[j2] >= sl:
                    exit_px, reason = sl, "SL"
                    break
                if l[j2] <= entry * (1 - tp_pct):
                    exit_px, reason = entry * (1 - tp_pct), "TP"
                    break
            exit_px = c[j2]
        cost_out = exit_px * (FEE_BPS + SLIP_BPS) / 1e4
        net = direction * (exit_px / entry - 1.0) \
            - (cost_in + cost_out) / entry
        trades.append({"ts": int(t[i]), "dir": direction,
                       "net": net, "reason": reason})
        i = i + 2
    return trades


def main():
    all_trs = []
    per = {}
    for store in ("data/candles", "data/candles_binance"):
        for p in sorted(glob.glob(f"{store}/*.parquet")):
            df = pd.read_parquet(p)
            if len(df) < 20000:
                continue
            trs = run_symbol(df)
            if trs:
                per[p] = stats(trs)
                all_trs.extend(trs)
    st = stats(all_trs)
    print(f"SURGE: {st['n']} trades | wr {st['wr']:.0%} | "
          f"avg win {st['avg_win']}% | avg loss {st['avg_loss']}% | "
          f"expectancy {st['expectancy_pct']}%/trade | pf {st['pf']}")
    # walk-forward style: split by trade time
    if all_trs:
        ts = sorted(t["ts"] for t in all_trs)
        qs = np.quantile(ts, [0, 0.25, 0.5, 0.75, 1.0])
        for k in range(4):
            seg = [t for t in all_trs if qs[k] <= t["ts"] < qs[k + 1]]
            s2 = stats(seg)
            if s2:
                print(f"  quarter {k+1}: n={s2['n']} wr={s2['wr']:.0%} "
                      f"exp {s2['expectancy_pct']}% pf {s2['pf']}")
    pos = sum(1 for s in per.values() if s and s["expectancy_pct"] > 0)
    print(f"symbols: {len(per)} | positive-expectancy: {pos}")
    all_ig = []
    per_ig = {}
    for store in ("data/candles", "data/candles_binance"):
        for p in sorted(glob.glob(f"{store}/*.parquet")):
            df = pd.read_parquet(p)
            if len(df) < 20000:
                continue
            trs = run_symbol_ignition(df)
            if trs:
                per_ig[p] = stats(trs)
                all_ig.extend(trs)
    st2 = stats(all_ig)
    if st2:
        print(f"SURGE-IGNITION: {st2['n']} trades | wr {st2['wr']:.0%} | "
              f"avg win {st2['avg_win']}% | avg loss {st2['avg_loss']}% | "
              f"expectancy {st2['expectancy_pct']}%/trade | pf {st2['pf']}")
        if all_ig:
            ts2 = sorted(t["ts"] for t in all_ig)
            qs2 = np.quantile(ts2, [0, 0.25, 0.5, 0.75, 1.0])
            for k in range(4):
                seg = [t for t in all_ig if qs2[k] <= t["ts"] < qs2[k + 1]]
                s3 = stats(seg)
                if s3:
                    print(f"  quarter {k+1}: n={s3['n']} wr={s3['wr']:.0%} "
                          f"exp {s3['expectancy_pct']}% pf {s3['pf']}")
        print(f"symbols: {len(per_ig)} | positive: "
              f"{sum(1 for s2v in per_ig.values() if s2v and s2v['expectancy_pct'] > 0)}")
    with open("data/state/surge_report.json", "w") as fh:
        json.dump({"stats": st, "per_symbol": {k.split('/')[-1]: v
                                               for k, v in per.items()}},
                  fh, indent=1)


if __name__ == "__main__":
    main()
