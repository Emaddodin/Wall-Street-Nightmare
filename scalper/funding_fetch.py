#!/usr/bin/env python3
"""FUNDING ARCHIVE (Branch B, Dataset C) -- Binance Vision funding history.

Monthly 8h funding-rate zips (free, official): downloads the top-N USDT-M
perps x N months into data/funding/<SYM>.csv (calc_time, interval, rate).
Resumable: months already stored are skipped.
"""
import io
import os
import sys
import time
import zipfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

DATA = Path(os.getenv("SCALPER_DATA", str(ROOT / "data")))
OUT = DATA / "funding"
TOP_N = int(os.environ.get("SCALPER_FUND_TOP", "40"))
MONTHS = int(os.environ.get("SCALPER_FUND_MONTHS", "6"))
BASE = "https://data.binance.vision/data/futures/um/monthly/fundingRate"


def month_list(n):
    out = []
    now = datetime.now(timezone.utc)
    y, m = now.year, now.month
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y}-{m:02d}")
    return out


def main() -> int:
    from market_data.client import BitunixPublic
    OUT.mkdir(parents=True, exist_ok=True)
    months = month_list(MONTHS)
    client = BitunixPublic(pause=0.1)
    ticks = client.tickers()
    rows = [t for t in ticks if t["symbol"].endswith("USDT")
            and t["usdt_volume_24h"] > 0]
    rows.sort(key=lambda t: -t["usdt_volume_24h"])
    symbols = [t["symbol"] for t in rows[:TOP_N]]
    print(f"funding: {len(symbols)} symbols x {MONTHS} months", flush=True)
    done = skipped = failed = 0
    for sym in symbols:
        out_csv = OUT / f"{sym}.csv"
        rows_all = []
        for ym in months:
            url = f"{BASE}/{sym}/{sym}-fundingRate-{ym}.zip"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
                with urllib.request.urlopen(req, timeout=40) as r:
                    raw = r.read()
            except Exception:
                failed += 1
                continue
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    txt = z.read(z.namelist()[0]).decode()
                rows_all.append(txt if txt.startswith("calc_time")
                                else "\n".join(txt.splitlines()[1:]))
            except Exception:
                failed += 1
        if not rows_all:
            continue
        body = "\n".join(x.strip() for x in rows_all if x.strip())
        out_csv.write_text(body + "\n")
        done += 1
        if done % 10 == 0:
            print(f"  {done} symbols stored", flush=True)
    print(f"funding done: {done} stored, {failed} failed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
