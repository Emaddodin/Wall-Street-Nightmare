"""
scripts/fetch_duka_raw.py
=========================
Resumable, parallel download of Dukascopy XAUUSD hourly tick files (bi5) into data/candles/duka_raw/, then build
per-day 1-minute CSVs (mid-price OHLC + mean spread) into data/candles/duka/.

    python3 scripts/fetch_duka_raw.py fetch --start 2025-01-21 --end 2025-06-01 --threads 24
    python3 scripts/fetch_duka_raw.py build --start 2025-01-21 --end 2025-06-01
"""
import argparse
import concurrent.futures as cf
import lzma
import struct
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/candles/duka_raw"
OUT = ROOT / "data/candles/duka"
URL = "https://datafeed.dukascopy.com/datafeed/XAUUSD/{y}/{m:02d}/{d:02d}/{h:02d}h_ticks.bi5"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
REC = struct.Struct(">IIIff")


def tasks(start, end):
    d = start
    while d < end:
        if d.weekday() != 5:
            for h in (range(21, 24) if d.weekday() == 6 else range(24)):
                yield d, h
        d += timedelta(days=1)


def path_for(d, h):
    return RAW / f"{d:%Y-%m-%d}_{h:02d}.bi5"


def fetch_one(args):
    d, h = args
    p = path_for(d, h)
    if p.exists():
        return "cached"
    url = URL.format(y=d.year, m=d.month - 1, d=d.day, h=h)
    for attempt in range(12):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=45) as r:
                data = r.read()
            p.write_bytes(data)
            return "ok"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                p.write_bytes(b"")
                return "empty"
            time.sleep(min(30, 2 * (attempt + 1)))
        except Exception:
            time.sleep(min(30, 2 * (attempt + 1)))
    return "fail"


def cmd_fetch(a):
    RAW.mkdir(parents=True, exist_ok=True)
    start = datetime.strptime(a.start, "%Y-%m-%d"); end = datetime.strptime(a.end, "%Y-%m-%d")
    todo = list(tasks(start, end))
    print(f"{len(todo)} hourly files", flush=True)
    t0, n, stats = time.time(), 0, {}
    with cf.ThreadPoolExecutor(a.threads) as ex:
        for res in ex.map(fetch_one, todo):
            n += 1; stats[res] = stats.get(res, 0) + 1
            if n % 100 == 0:
                print(f"{n}/{len(todo)} {stats} {time.time() - t0:.0f}s", flush=True)
    print("done", stats, flush=True)


def cmd_build(a):
    OUT.mkdir(parents=True, exist_ok=True)
    start = datetime.strptime(a.start, "%Y-%m-%d"); end = datetime.strptime(a.end, "%Y-%m-%d")
    d = start
    while d < end:
        mins, missing = {}, 0
        for h in (range(21, 24) if d.weekday() == 6 else range(24)):
            if d.weekday() == 5:
                break
            p = path_for(d, h)
            if not p.exists():
                missing += 1
                continue
            raw = p.read_bytes()
            if not raw:
                continue
            data = lzma.decompress(raw)
            base = int(d.replace(hour=h, tzinfo=timezone.utc).timestamp() * 1000)
            for i in range(0, len(data) - REC.size + 1, REC.size):
                ms, ask, bid, av, bv = REC.unpack_from(data, i)
                mid = (ask + bid) / 2000.0
                m = (base + ms) // 60000 * 60000
                b = mins.get(m)
                if b is None:
                    mins[m] = [mid, mid, mid, mid, av + bv, (ask - bid) / 1000.0, 1]
                else:
                    b[1] = max(b[1], mid); b[2] = min(b[2], mid); b[3] = mid; b[4] += av + bv; b[5] += (ask - bid) / 1000.0; b[6] += 1
        if mins and not missing:
            with open(OUT / f"xau_m1_{d:%Y-%m-%d}.csv", "w") as f:
                f.write("time,open_time,close_time,open,high,low,close,volume,mid_px,spread\n")
                for m in sorted(mins):
                    o, hi, lo, c, v, sp, n = mins[m]
                    f.write(f"{m},{m},{m + 59999},{o:.3f},{hi:.3f},{lo:.3f},{c:.3f},{v:.3f},{c:.3f},{sp / n:.3f}\n")
        elif missing:
            print(f"{d:%Y-%m-%d}: {missing} hourly files missing - day skipped")
        d += timedelta(days=1)
    print("built", len(list(OUT.glob('xau_m1_*.csv'))), "days")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fetch", "build"])
    ap.add_argument("--start", required=True); ap.add_argument("--end", required=True)
    ap.add_argument("--threads", type=int, default=24)
    a = ap.parse_args()
    sys.exit(cmd_fetch(a) if a.cmd == "fetch" else cmd_build(a))
