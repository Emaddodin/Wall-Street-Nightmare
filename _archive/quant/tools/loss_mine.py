"""Loss mining: given a backtest's trades (with entry timestamps), join
pre-entry features and compare winning vs losing trades across features.

Answers: which measurable state BEFORE entry predicts failure?  Each
feature's top/bottom tercile win-rate gap identifies failure predictors.

Usage:
  python3 quant/tools/loss_mine.py --report <experiment_id> \
      --out data/research/lossmine.csv
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.lib import store, features  # noqa: E402
from quant.tools.discover5m import resample_5m, align5m  # noqa: E402

log = logging.getLogger("quant.tools.loss_mine")

FEATS = ["r3", "r15", "r60", "rvol", "rvol_accel", "hi_dist", "lo_dist",
         "hi_dist240", "lo_dist240", "wick_up", "wick_dn", "compress",
         "expand", "vol_ratio", "rv5", "rv60", "range_frac", "body_frac",
         "oi_chg1", "oi_z", "taker_imb_z", "liq_net", "funding_z",
         "slope60", "trend_dist", "accel5"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True, help="experiment id")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    root = store.data_root()
    rdir = root / "experiments" / "reports" / args.report
    rec = json.loads(open(rdir / "record.json").read())
    trades = pd.read_parquet(rdir / "trades.parquet")
    print(f"{args.report}: {len(trades)} trades, strategy "
          f"{rec['strategy']}")

    # find entry bar timestamps: entry_gi is a global bar index in the Sim's
    # merged timeline -- instead, reconstruct per-symbol entry times by
    # matching entry_price to bars near the entry (approximate): use the
    # per-symbol frame and locate the bar whose open is closest to
    # entry_price within the entry bar window.
    # Simpler robust approach: record the entry open_time in the trade
    # record.  (Patched backtester does this; if missing, skip.)
    if "entry_t" not in trades.columns:
        print("trades lack entry_t; re-run with patched backtester")
        return 1

    # feature cache per symbol (5m)
    btc5 = features.compute_base(resample_5m(
        store.load_klines("BTCUSDT", root)))
    rows = []
    for sym, g in trades.groupby("symbol"):
        try:
            df = store.load_klines(sym, root)
        except FileNotFoundError:
            continue
        d5 = resample_5m(df)
        metrics = funding = None
        try:
            metrics = store.load_metrics(sym, root)
        except FileNotFoundError:
            pass
        try:
            funding = store.load_funding(sym, root)
        except FileNotFoundError:
            pass
        feat = features.compute_base(d5)
        feat = align5m(feat, metrics, funding)
        tmap = {t: i for i, t in enumerate(d5["open_time"].to_numpy())}
        for _, tr in g.iterrows():
            i = tmap.get(int(tr["entry_t"]))
            if i is None:
                continue
            row = {"symbol": sym, "win": tr["pnl"] > 0,
                   "pnl": tr["pnl"]}
            for f in FEATS:
                if f in feat.columns:
                    row[f] = feat[f].iloc[i]
            rows.append(row)
    data = pd.DataFrame(rows)
    if args.out:
        data.to_csv(args.out, index=False)
    print(f"joined {len(data)} trades with features")
    wins = data[data.win]
    losses = data[~data.win]
    print(f"win rate {data.win.mean():.3f} (n={len(data)})")
    print("\nfeature | win mean | loss mean | gap")
    out = []
    for f in FEATS:
        if f not in data.columns:
            continue
        wm = wins[f].mean()
        lm = losses[f].mean()
        gap = wm - lm
        out.append((f, wm, lm, gap))
    out.sort(key=lambda x: -abs(x[3]))
    for f, wm, lm, gap in out[:25]:
        print(f"{f:14s} {wm:9.3f} {lm:9.3f} {gap:+9.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
