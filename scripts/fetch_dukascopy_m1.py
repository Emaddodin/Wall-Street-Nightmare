"""
scripts/fetch_dukascopy_m1.py
=============================
Build REAL XAUUSD 1-minute candles from Dukascopy hourly tick files (bi5).

Record layout (big-endian, 20 bytes): ms-in-hour u32, ask u32, bid u32, ask_vol f32, bid_vol f32;
XAUUSD prices are integers scaled by 1000. Candles are built from the mid price. Output schema matches
data/candles/real/xau_m1_<date>.csv, plus a `spread` column (mean ask-bid per minute).

    python3 scripts/fetch_dukascopy_m1.py --start 2025-01-21 --end 2025-06-01 --out data/candles/duka
"""
import argparse
import lzma
import struct
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

URL = "https://datafeed.dukascopy.com/datafeed/XAUUSD/{y}/{m:02d}/{d:02d}/{h:02d}h_ticks.bi5"
REC = struct.Struct(">IIIff")


def fetch_hour(day: datetime, hour: int, retries: int = 8):
    url = URL.format(y=day.year, m=day.month - 1, d=day.day, h=hour)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36", "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return b""
            time.sleep(2.0 * (attempt + 1) if e.code == 429 else 1.0)
        except Exception:
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"failed {url}")


def hour_to_minutes(day: datetime, hour: int, raw: bytes):
    if not raw:
        return {}
    data = lzma.decompress(raw)
    base = int(day.replace(hour=hour, minute=0, second=0, microsecond=0, tzinfo=timezone.utc).timestamp() * 1000)
    mins = {}
    for i in range(0, len(data) - REC.size + 1, REC.size):
        ms, ask, bid, av, bv = REC.unpack_from(data, i)
        mid = (ask + bid) / 2000.0
        sp = (ask - bid) / 1000.0
        m = (base + ms) // 60000 * 60000
        b = mins.get(m)
        if b is None:
            mins[m] = [mid, mid, mid, mid, av + bv, sp, 1]
        else:
            b[1] = max(b[1], mid); b[2] = min(b[2], mid); b[3] = mid
            b[4] += av + bv; b[5] += sp; b[6] += 1
    return mins


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True, help="exclusive")
    ap.add_argument("--out", default="data/candles/duka")
    ap.add_argument("--pause", type=float, default=0.6)
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    d = datetime.strptime(a.start, "%Y-%m-%d"); end = datetime.strptime(a.end, "%Y-%m-%d")
    while d < end:
        if d.weekday() != 5:                       # Saturday: market closed
            path = out / f"xau_m1_{d:%Y-%m-%d}.csv"
            if not path.exists():
                mins = {}
                hours = range(21, 24) if d.weekday() == 6 else range(24)   # Sunday: only the evening re-open
                for h in hours:
                    mins.update(hour_to_minutes(d, h, fetch_hour(d, h)))
                    time.sleep(a.pause)
                if mins:
                    tmp = path.with_suffix(".tmp")
                    with open(tmp, "w") as f:
                        f.write("time,open_time,close_time,open,high,low,close,volume,mid_px,spread\n")
                        for m in sorted(mins):
                            o, hi, lo, c, v, sp, n = mins[m]
                            f.write(f"{m},{m},{m+59999},{o:.3f},{hi:.3f},{lo:.3f},{c:.3f},{v:.3f},{c:.3f},{sp/n:.3f}\n")
                    tmp.replace(path)
                    print(f"{d:%Y-%m-%d}: {len(mins)} bars", flush=True)
        d += timedelta(days=1)


if __name__ == "__main__":
    sys.exit(main())
