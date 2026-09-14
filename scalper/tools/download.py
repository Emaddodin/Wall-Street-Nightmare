"""Download 1m candles for the top-N liquid Bitunix USDT perpetuals.

Usage:
    python tools/download.py --days 130 --top 30 --workers 8

Pages backwards through the 200-bar-per-request cap, saves one parquet per
symbol under data/candles/, and skips symbols whose store is already fresh
(resume-friendly).  USDT-quoted, OPEN, API-supported pairs only.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scalper.market_data.client import BitunixPublic  # noqa: E402
from scalper.market_data.store import CandleStore  # noqa: E402

log = logging.getLogger("download")


def pick_symbols(client: BitunixPublic, top: int) -> list[str]:
    pairs = client.trading_pairs()
    ticks = client.tickers()
    rows = []
    for t in ticks:
        sym = t["symbol"]
        if not sym.endswith("USDT"):
            continue
        meta = pairs.get(sym) or {}
        if meta.get("symbolStatus") != "OPEN":
            continue
        if meta.get("isApiSupported") is False:
            continue
        rows.append((sym, t["usdt_volume_24h"]))
    rows.sort(key=lambda r: -r[1])
    return [r[0] for r in rows[:top]]


def download_one(client: BitunixPublic, store: CandleStore, symbol: str,
                 days: int) -> dict:
    # per-worker pause grows the retry budget: page requests with a calmer
    # cadence keep the venue from answering "System error"
    client.pause = 0.12
    bars = days * 1440
    df = client.klines(symbol, "1m", bars)
    store.save(symbol, df, "1m")
    return {"symbol": symbol, "bars": len(df),
            "first_ms": int(df["open_time"].iloc[0]) if len(df) else None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=130)
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default="data/candles")
    ap.add_argument("--symbols", nargs="*", default=None,
                    help="explicit symbol list; overrides --top")
    ap.add_argument("--fresh-days", type=float, default=0.9,
                    help="skip a symbol whose newest bar is newer than this fraction of --days")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    store = CandleStore(a.out)
    client = BitunixPublic()

    if a.symbols:
        symbols = a.symbols
    else:
        log.info("picking top %d USDT perps by 24h volume...", a.top)
        symbols = pick_symbols(client, a.top)
    log.info("universe (%d): %s", len(symbols), ", ".join(symbols))

    todo = []
    for sym in symbols:
        cur = store.load(sym, "1m")
        if len(cur):
            newest = int(cur["open_time"].iloc[-1])
            age_days = (time.time() * 1000 - newest) / 86_400_000
            if age_days < a.days * (1 - a.fresh_days) + a.fresh_days * 0:
                # fresh enough: newest bar is within the trailing tail of the
                # window (no gap larger than fresh-days behind now)
                if age_days < a.fresh_days:
                    log.info("%s fresh (newest %.2fd ago) -- skip", sym, age_days)
                    continue
        todo.append(sym)
    log.info("to download: %d symbols, %d days each", len(todo), a.days)

    ok = fail = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(download_one, client, store, s, a.days): s for s in todo}
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                r = fut.result()
                log.info("%-14s done: %6d bars  first=%s", r["symbol"], r["bars"],
                         time.strftime("%Y-%m-%d", time.gmtime(r["first_ms"] / 1000))
                         if r["first_ms"] else "-")
                ok += 1
            except Exception as e:
                log.error("%s FAILED: %s", sym, e)
                fail += 1
    log.info("finished: %d ok, %d failed", ok, fail)
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
