"""Download gold M1 history for the backtests (Dukascopy and LiteFinance) into gzipped CSVs.

    python3 fetch_history.py dukascopy 2025-10-01 2026-09-30     # -> data/dukascopy_xauusd_m1.csv.gz
    python3 fetch_history.py dukascopy 2025-10-01 2026-09-30 XAGUSD   # another instrument (silver, USA500IDXUSD, ...)
    python3 fetch_history.py litefinance 90                       # last 90 days -> data/litefinance_xauusd_m1.csv.gz

Columns: time (UTC epoch seconds), open, high, low, close, volume. Bid prices.
Dukascopy publishes one LZMA file of minute candles per day (month is 0-based in its URL).
"""
from __future__ import annotations

import csv
import gzip
import json
import lzma
import struct
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent / "data"
UA = {"User-Agent": "Mozilla/5.0 GoldDesk"}


def _get(url: str, tries: int = 4) -> bytes:
    for k in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return b""
            time.sleep(2 ** k)
        except OSError:
            time.sleep(2 ** k)
    return b""


SCALE = {"EURUSD": 100000, "GBPUSD": 100000, "USDCHF": 100000, "AUDUSD": 100000}   # everything else: 1000


def dukascopy_day(d: date, sym: str = "XAUUSD") -> list:
    div = SCALE.get(sym, 1000)
    url = f"https://datafeed.dukascopy.com/datafeed/{sym}/{d.year}/{d.month - 1:02d}/{d.day:02d}/BID_candles_min_1.bi5"
    raw = _get(url)
    if not raw:
        return []
    try:
        buf = lzma.decompress(raw)
    except lzma.LZMAError:
        return []
    t0 = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())
    rows = []
    for k in range(0, len(buf) - 23, 24):
        s, o, c, l, h, v = struct.unpack(">5if", buf[k:k + 24])
        if v <= 0 and h == l:
            continue                                  # padding for closed minutes
        rows.append([t0 + s, o / div, h / div, l / div, c / div, round(v, 4)])
    return rows


def dukascopy(start: str, end: str, sym: str = "XAUUSD") -> Path:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    days = [a + timedelta(n) for n in range((b - a).days + 1)]
    days = [d for d in days if d.weekday() != 5]      # no gold on Saturdays
    rows = []
    with ThreadPoolExecutor(8) as ex:
        for n, part in enumerate(ex.map(lambda d: dukascopy_day(d, sym), days)):
            rows += part
            print(f"  {n + 1}/{len(days)} days, {len(rows)} bars", end="\r", flush=True)
    print()
    return _write(f"dukascopy_{sym.lower()}_m1.csv.gz", rows)


def litefinance(days: int) -> Path:
    now, chunk, out, to = int(time.time()), 4000 * 60, {}, int(time.time())
    while to > now - days * 86400:
        d = json.loads(_get(f"https://my.litefinance.org/chart/get-history?symbol=XAUUSD&resolution=1"
                            f"&from={to - chunk}&to={to}") or b"{}")
        d = d.get("data", d)
        for i, t in enumerate(d.get("t") or []):
            out[int(t)] = [int(t), d["o"][i], d["h"][i], d["l"][i], d["c"][i], (d.get("v") or [0] * len(d["t"]))[i]]
        to -= chunk
        print(f"  back to {datetime.fromtimestamp(to, timezone.utc):%Y-%m-%d}, {len(out)} bars", end="\r", flush=True)
    print()
    rows = sorted(r for r in out.values() if now - days * 86400 <= r[0] <= now - 60)
    return _write("litefinance_xauusd_m1.csv.gz", rows)


def _write(name: str, rows: list) -> Path:
    OUT.mkdir(exist_ok=True)
    rows.sort(key=lambda r: r[0])
    p = OUT / name
    with gzip.open(p, "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close", "volume"])
        w.writerows(rows)
    if rows:
        span = " to ".join(f"{datetime.fromtimestamp(r[0], timezone.utc):%Y-%m-%d}" for r in (rows[0], rows[-1]))
        print(f"{p}: {len(rows)} M1 bars, {span}")
    else:
        print(f"{p}: no data")
    return p


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "dukascopy":
        dukascopy(sys.argv[2], sys.argv[3], *(sys.argv[4:5]))
    elif len(sys.argv) >= 3 and sys.argv[1] == "litefinance":
        litefinance(int(sys.argv[2]))
    else:
        raise SystemExit(__doc__)
