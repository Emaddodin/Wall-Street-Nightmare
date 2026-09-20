"""Event studies: given a condition expression over features, compute the
mean forward path of 1m returns from event bars vs the unconditional mean,
pooled across symbols.  Also reports hit-probabilities of TP/SL levels.

Usage:
  python3 quant/tools/event_study.py --symbols BTCUSDT,SOLUSDT \
      --cond "rvol_accel > 3 and r5 > 50" --horizon 60
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.lib import store, features, outcomes  # noqa: E402
from quant.research.discover import event_study  # noqa: E402

log = logging.getLogger("quant.tools.event_study")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--cond", required=True,
                    help="e.g. 'rvol_accel > 3 and r5 > 50'")
    ap.add_argument("--horizon", type=int, default=60)
    ap.add_argument("--no-derivatives", action="store_true")
    ap.add_argument("--max-events", type=int, default=200_000)
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]
    root = store.data_root()
    all_rows = []
    n_ev = 0
    for sym in symbols:
        try:
            df = store.load_klines(sym, root)
        except FileNotFoundError:
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
        feat = features.compute_base(df)
        feat = features.compute_symbol(sym, df, metrics, funding)
        mask = feat.eval(args.cond).to_numpy(dtype=bool)
        n_ev += int(mask.sum())
        es = event_study(feat, mask, args.horizon)
        if len(es):
            es["symbol"] = sym
            all_rows.append(es)
        log.info("[%s] events=%d", sym, int(mask.sum()))
        if n_ev > args.max_events:
            log.info("event cap reached, stopping")
            break
    if not all_rows:
        print("no events matched")
        return 1
    out = pd.concat(all_rows, ignore_index=True)
    pooled = (out.groupby("k")
              .apply(lambda g: pd.Series({
                  "baseline": (g["baseline"] * g["n"]).sum() / g["n"].sum(),
                  "event": (g["event"] * g["n"]).sum() / g["n"].sum(),
                  "n": g["n"].sum()}), include_groups=False)
              .reset_index())
    pooled["edge"] = pooled["event"] - pooled["baseline"]
    pooled["cum_event"] = pooled["event"].cumsum()
    pooled["cum_base"] = pooled["baseline"].cumsum()
    print(f"total events: {n_ev}")
    print(pooled[["k", "cum_event", "cum_base", "edge", "n"]].round(2)
          .to_string(index=False, max_rows=70))
    return 0


if __name__ == "__main__":
    sys.exit(main())
