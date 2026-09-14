#!/usr/bin/env python3
"""FUNDING FADE -- Branch-B candidate on derivatives data (Dataset C).

Documented edge (2024-2026 literature, Gate/voiceofchain research in the
knowledge store): extreme perp funding marks crowded positioning; fading it
(short high-positive funding, long high-negative) earns the reversion.
Horizon: hours-to-days -- a different product than the scalper.

Test: hourly bars (resampled from the stores) x 8h funding prints.
    ENTER  at the hour close after a funding print with |rate| >= thresh,
           direction AGAINST the crowd.
    EXIT   when |rate| reverts below exit_thresh, or after max_hours,
           or at a stop-loss, whichever first.  Taker fees both legs.
"""
import glob
import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

FEE_BPS = 6.0
SLIP_BPS = 2.0


def load_hourly(path):
    df = pd.read_parquet(path)
    if len(df) < 30 * 24:
        return None
    df = df.set_index(pd.to_datetime(df["open_time"], unit="ms"))
    h = df.resample("1h").agg({"open": "first", "high": "max", "low": "min",
                               "close": "last", "volume": "sum"}).dropna()
    return h


def load_funding(sym):
    p = f"data/funding/{sym}.csv"
    try:
        f = pd.read_csv(p)
    except Exception:
        return None
    f = f.rename(columns={f.columns[0]: "calc_time"})
    f = f[pd.to_numeric(f["calc_time"], errors="coerce").notna()]
    f["calc_time"] = pd.to_datetime(f["calc_time"].astype(float), unit="ms")
    f["rate"] = f[f.columns[-1]].astype(float)
    return f.set_index("calc_time")["rate"]


def run(sym, hourly, funding, thresh=0.001, exit_thresh=0.0001,
        max_hours=24, sl_pct=0.03):
    trades = []
    fx = funding.sort_index()
    for ts, rate in fx.items():
        if abs(rate) < thresh:
            continue
        direction = -1 if rate > 0 else 1      # fade the crowd
        entry_slice = hourly[hourly.index >= ts + pd.Timedelta(hours=1)]
        if len(entry_slice) == 0:
            continue
        entry_row = entry_slice.iloc[0]
        entry = float(entry_row["close"])
        cost = entry * (FEE_BPS + SLIP_BPS) / 1e4
        exit_px, reason = entry, "TIME"
        for j in range(1, max_hours + 1):
            row = entry_slice.iloc[j] if j < len(entry_slice) else None
            if row is None:
                break
            if direction == 1:
                if float(row["low"]) <= entry * (1 - sl_pct):
                    exit_px, reason = entry * (1 - sl_pct), "SL"
                    break
            else:
                if float(row["high"]) >= entry * (1 + sl_pct):
                    exit_px, reason = entry * (1 + sl_pct), "SL"
                    break
            exit_px = float(row["close"])
            # reversion exit: funding crossed back
            later = fx[fx.index > row.name]
            if len(later) and abs(float(later.iloc[0])) < exit_thresh:
                reason = "REVERT"
                break
        net = direction * (exit_px / entry - 1.0) - cost / entry
        trades.append({"ts": int(ts.timestamp()), "dir": direction,
                       "net": net, "reason": reason})
    return trades


def main():
    all_trs = []
    per = {}
    for p in sorted(glob.glob("data/candles/*.parquet")) + \
            sorted(glob.glob("data/candles_binance/*.parquet")):
        sym = p.split("/")[-1].split("_")[0]
        hourly = load_hourly(p)
        funding = load_funding(sym)
        if hourly is None or funding is None or len(funding) < 20:
            continue
        trs = run(sym, hourly, funding)
        if trs:
            all_trs.extend(trs)
            per[sym] = len(trs)
    n = len(all_trs)
    if n == 0:
        print("no funding-fade trades")
        return
    wins = [t for t in all_trs if t["net"] > 0]
    losses = [t for t in all_trs if t["net"] <= 0]
    wr = len(wins) / n
    avg_w = sum(t["net"] for t in wins) / len(wins) * 100 if wins else 0
    avg_l = sum(t["net"] for t in losses) / len(losses) * 100 if losses else 0
    exp = sum(t["net"] for t in all_trs) / n * 100
    gw = sum(t["net"] for t in wins)
    gl = -sum(t["net"] for t in losses)
    print(f"FUNDING-FADE: n={n} | wr {wr:.0%} | avg win {avg_w:.3f}% | "
          f"avg loss {avg_l:.3f}% | expectancy {exp:.4f}%/trade | "
          f"pf {gw/gl if gl else 99:.2f}")
    ts = sorted(t["ts"] for t in all_trs)
    qs = np.quantile(ts, [0, 0.25, 0.5, 0.75, 1.0])
    for k in range(4):
        seg = [t for t in all_trs if qs[k] <= t["ts"] < qs[k + 1]]
        if seg:
            w = [t for t in seg if t["net"] > 0]
            e = sum(t["net"] for t in seg) / len(seg) * 100
            print(f"  quarter {k+1}: n={len(seg)} wr={len(w)/len(seg):.0%} "
                  f"exp {e:.4f}%")
    print("symbols with trades:", len(per))
    json.dump({"n": n, "wr": wr, "avg_win": avg_w, "avg_loss": avg_l,
               "expectancy_pct": exp}, open("data/state/funding_fade.json", "w"),
              indent=1)


if __name__ == "__main__":
    main()
