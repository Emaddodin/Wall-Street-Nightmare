"""
scripts/fetch_real_m1.py
========================
Pull REAL 1-minute gold-proxy candles (Binance PAXGUSDT) into data/candles/real/.

Why this exists
---------------
data/candles/gold_m1_*.csv is NOT market data.  scripts/generate_missing_candles_2025.py
calls replay_data.load_candles_for_date() which for every date except three falls through to

    backtester.generate_synthetic_gold_m1(n_bars=1440, start_price=2490.0, seed=<date>)

i.e. a deterministic random ramp that always starts at $2490.  Every backtest number ever
produced from that corpus is fiction.  This script restores a real price series.

Output schema matches the existing gold_m1 CSVs so loaders work unchanged:
    time,open_time,close_time,open,high,low,close,volume,mid_px

Files: data/candles/real/xau_m1_<YYYY-MM-DD>.csv   (UTC day, Monday-Friday only)
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KLINES = "https://data-api.binance.vision/api/v3/klines"
FIELDNAMES = [
    "time", "open_time", "close_time",
    "open", "high", "low", "close",
    "volume", "mid_px",
]


def _get(url: str, retries: int = 5):
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"klines fetch failed after {retries} attempts: {last}")


def fetch_range(start_ms: int, end_ms: int, symbol: str, interval: str = "1m"):
    """Yield raw klines from start_ms up to end_ms, paging 1000 bars at a time."""
    cur = start_ms
    while cur < end_ms:
        url = (
            f"{KLINES}?symbol={symbol}&interval={interval}"
            f"&startTime={cur}&endTime={end_ms}&limit=1000"
        )
        rows = _get(url)
        if not rows:
            return
        for row in rows:
            yield row
        last_open = int(rows[-1][0])
        nxt = last_open + 60_000
        if nxt <= cur:          # defensive: never loop on a non-advancing cursor
            return
        cur = nxt
        time.sleep(0.25)        # stay well inside Binance request-weight limits


def write_day(path: Path, rows: list) -> int:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    tmp.replace(path)
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch real 1m gold-proxy candles")
    ap.add_argument("--days", type=int, default=90, help="calendar days back from today (UTC)")
    ap.add_argument("--symbol", default="PAXGUSDT")
    ap.add_argument("--out", default="data/candles/real")
    ap.add_argument("--start-date", default=None, help="YYYY-MM-DD (UTC); overrides --days")
    ap.add_argument("--end-date", default=None, help="YYYY-MM-DD (UTC, exclusive); default now")
    args = ap.parse_args()

    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    # stop at the start of today; a partial day is written as-is for the final file
    start = (now - timedelta(days=args.days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    if args.start_date:
        start = datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start_ms = int(start.timestamp() * 1000)
    if args.end_date:
        now = datetime.strptime(args.end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_ms = int(now.timestamp() * 1000)

    print(f"Fetching {args.symbol} 1m from {start:%Y-%m-%d} to {now:%Y-%m-%d} ...")

    day_rows: dict[str, list] = {}
    total = 0
    t0 = time.time()
    for row in fetch_range(start_ms, end_ms, args.symbol):
        open_ms = int(row[0])
        d = datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc)
        if d.weekday() >= 5:                    # gold is closed Sat/Sun
            continue
        if d.weekday() == 4 and d.hour >= 21:   # Friday 21:00 UTC weekend close
            continue
        if d.weekday() == 6 and d.hour < 22:    # Sunday before 22:00 UTC
            continue
        close_ms = int(row[6])
        o, h, l, c = (float(row[1]), float(row[2]), float(row[3]), float(row[4]))
        vol = float(row[5])
        key = d.strftime("%Y-%m-%d")
        day_rows.setdefault(key, []).append({
            "time": open_ms,
            "open_time": open_ms,
            "close_time": close_ms,
            "open": f"{o:.5f}",
            "high": f"{h:.5f}",
            "low": f"{l:.5f}",
            "close": f"{c:.5f}",
            "volume": f"{vol:.8f}",
            "mid_px": f"{c:.5f}",
        })
        total += 1
        if total % 10000 == 0:
            print(f"  ...{total:>7} bars ({(open_ms - start_ms) / 86400000:.0f}d "
                  f"of {args.days}d, {time.time() - t0:.0f}s)", flush=True)
        # flush each UTC day as soon as it is complete so an interrupted run keeps its work
        if len(day_rows) >= 2:
            stale = [k for k in sorted(day_rows) if k != key]
            for k in stale:
                _flush(out_dir, k, day_rows.pop(k))

    days = 0
    for key in sorted(day_rows):
        rows = day_rows[key]
        rows.sort(key=lambda r: r["open_time"])
        write_day(out_dir / f"xau_m1_{key}.csv", rows)
        days += 1
        print(f"  wrote {key}: {len(rows)} bars")

    print(f"Done. {total} bars across {days} new trading days -> {out_dir}")
    if total == 0:
        print("No data fetched.", file=sys.stderr)
        return 1
    return 0


def _flush(out_dir: Path, key: str, rows: list) -> None:
    rows.sort(key=lambda r: r["open_time"])
    write_day(out_dir / f"xau_m1_{key}.csv", rows)
    print(f"  wrote {key}: {len(rows)} bars", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
