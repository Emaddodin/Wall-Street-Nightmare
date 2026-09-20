"""Discovery pass: per symbol, compute features + outcomes and run bucket
analysis for every feature x outcome pair.  Aggregates a CSV of edges with
per-symbol consistency.

Usage:
  python3 quant/tools/discover_run.py --symbols BTCUSDT,ETHUSDT,SOLUSDT \
      --features r5,rvol_accel,expand,hi_dist --outcomes fwd_5,g20x20_15 \
      --out data/research/disco1.csv
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.lib import store, features, outcomes  # noqa: E402
from quant.research.discover import bucket_table  # noqa: E402

log = logging.getLogger("quant.tools.discover")

# outcome name -> stride for non-overlapping sampling
STRIDE = {"fwd_1": 1, "fwd_3": 3, "fwd_5": 5, "fwd_10": 10, "fwd_15": 15,
          "fwd_30": 30, "fwd_60": 60, "fwd_abs_5": 5, "fwd_abs_15": 15,
          "mfe_15": 15, "mae_15": 15, "mfe_30": 30, "mae_30": 30,
          "orac_20x20": 15, "orac_30x30": 15, "orac_50x30": 15,
          "orac_80x50": 15, "g20x20_15": 15, "g30x20_15": 15,
          "g30x30_15": 15, "g50x30_15": 15, "g50x50_15": 15,
          "g80x50_15": 15}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=None, help="comma list")
    ap.add_argument("--features", default=None, help="comma list; "
                    "default = full battery")
    ap.add_argument("--outcomes", default=None,
                    help="comma list; default = fwd_5,fwd_15,g20x20_15,"
                         "g30x30_15,mfe_15")
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-bins", type=int, default=10)
    ap.add_argument("--min-bars", type=int, default=50_000)
    ap.add_argument("--no-derivatives", action="store_true",
                    help="skip metrics/funding joins (faster)")
    args = ap.parse_args()

    root = store.data_root()
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        uni = json.loads(open(root / "universe.json").read())
        symbols = sorted(uni["symbols"])

    if args.features:
        feat_cols = [x.strip() for x in args.features.split(",") if x.strip()]
    else:
        feat_cols = None

    if args.outcomes:
        out_cols = [x.strip() for x in args.outcomes.split(",") if x.strip()]
    else:
        out_cols = ["fwd_5", "fwd_15", "g20x20_15", "g30x30_15", "mfe_15"]

    # btc features for cross joins (cheap)
    btc_feat = None
    try:
        btc_feat = features.compute_base(store.load_klines("BTCUSDT", root))
    except FileNotFoundError:
        pass

    rows = []
    t0 = time.time()
    for i, sym in enumerate(symbols):
        t1 = time.time()
        try:
            df = store.load_klines(sym, root)
        except FileNotFoundError:
            log.info("skip %s (no data)", sym)
            continue
        if len(df) < args.min_bars:
            log.info("skip %s (%d bars)", sym, len(df))
            continue
        metrics = funding = None
        if not args.no_derivatives:
            try:
                metrics = store.load_metrics(sym, root)
            except FileNotFoundError:
                pass
            try:
                funding = store.load_funding(sym, root)
            except FileNotFoundError:
                pass
        feat = features.compute_symbol(sym, df, metrics, funding, btc_feat)
        out = outcomes.compute_outcomes(df)
        cols = feat_cols or [c for c in feat.columns if c != "open_time"]
        for f in cols:
            if f not in feat.columns:
                continue
            for o in out_cols:
                if o not in out.columns and o not in feat.columns:
                    continue
                src = out if o in out.columns else feat
                stride = STRIDE.get(o, 15)
                bt = bucket_table(feat, f, src[o], stride, args.n_bins)
                if bt is None or "t" not in bt.attrs:
                    continue
                rows.append({
                    "symbol": sym, "feature": f, "outcome": o,
                    "edge": bt.attrs["edge"], "t": bt.attrs["t"],
                    "mono": bt.attrs["mono"], "n_eff": bt.attrs["n_eff"],
                    "bin0_mean": float(bt["mean"].iloc[0]),
                    "bin9_mean": float(bt["mean"].iloc[-1]),
                })
        log.info("[%s] %d feature-outcome rows in %.1fs (%d/%d)", sym,
                 sum(1 for r in rows if r["symbol"] == sym),
                 time.time() - t1, i + 1, len(symbols))

    res = pd.DataFrame(rows)
    res.to_csv(args.out, index=False)
    log.info("saved %d rows -> %s (%.1f min)", len(res), args.out,
             (time.time() - t0) / 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
