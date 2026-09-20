"""Bitunix execution-environment snapshot: live spreads, top-of-book
depth, and fee structure for the tradable universe.  This is a LIVE
snapshot (Bitunix has no public historical book archive), used to set the
honest execution assumptions, clearly separated from historical
validation in the final report.

Usage:
  python3 quant/tools/bitunix_snapshot.py --top 15 \
      --out data/research/bitunix_snapshot.csv
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scalper"))

from market_data.client import BitunixPublic  # noqa: E402

# venue fee schedule (from exchange/bitunix.py comments + docs):
# maker 0.02%, taker 0.06%, both sides, of notional.
FEES = {"maker_bps": 2.0, "taker_bps": 6.0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    c = BitunixPublic(pause=0.05)
    ticks = c.tickers()
    rows = [t for t in ticks if t["symbol"].endswith("USDT")
            and t["usdt_volume_24h"] > 5e6]
    rows.sort(key=lambda t: -t["usdt_volume_24h"])
    out_rows = []
    for t in rows[:args.top]:
        sym = t["symbol"]
        try:
            spread = c.spread_bps(sym)
        except Exception:
            print(f"{sym:14s} depth unavailable (not tradeable?) -- skip")
            continue
        liq5 = c.book_liquidity_usdt(sym, 5)
        out_rows.append({
            "symbol": sym,
            "price": t["price"],
            "vol24h_usdt": t["usdt_volume_24h"],
            "spread_bps": round(spread, 3) if spread != float("inf")
            else None,
            "book5_usdt": round(liq5, 0),
            "maker_fee_bps": FEES["maker_bps"],
            "taker_fee_bps": FEES["taker_bps"],
        })
        print(f"{sym:14s} px={t['price']:>12.5f} spread={spread:>8.2f}bps "
              f"book5=${liq5:>12,.0f}")
        time.sleep(0.1)
    df = pd.DataFrame(out_rows)
    if args.out:
        df.to_csv(args.out, index=False)
        print(f"saved -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
