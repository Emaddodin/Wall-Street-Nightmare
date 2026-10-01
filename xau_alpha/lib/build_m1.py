"""
xau_alpha/lib/build_m1.py
Build bid/ask M1 bars for XAUUSD from Dukascopy hourly tick files (data/candles/duka_raw/YYYY-MM-DD_HH.bi5).

Output: xau_alpha/data/m1_ba.parquet, one row per minute that has at least one tick:
  ts (int64 ms UTC, minute open), bo bh bl bc (bid OHLC), ao ah al ac (ask OHLC),
  spr (mean ask-bid), spr_max, n (tick count), vol (sum ask+bid volume).
Tick record (big-endian 20 bytes): ms-in-hour u32, ask u32, bid u32, ask_vol f32, bid_vol f32; price = int / 1000.
Local file names use the real calendar date (the fetcher already handled Dukascopy's zero-based month).
"""
import glob
import lzma
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/candles/duka_raw"
OUT = ROOT / "xau_alpha/data/m1_ba.parquet"
REC = np.dtype([("ms", ">u4"), ("ask", ">u4"), ("bid", ">u4"), ("av", ">f4"), ("bv", ">f4")])


def hour_start_ms(path: str) -> int:
    stem = os.path.basename(path)[:-4]          # 2026-03-10_14
    d, h = stem.split("_")
    dt = datetime.strptime(d, "%Y-%m-%d").replace(hour=int(h), tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def decode(path: str):
    raw = open(path, "rb").read()
    if not raw:
        return None
    a = np.frombuffer(lzma.decompress(raw), dtype=REC)
    if len(a) == 0:
        return None
    return a


def bars_for_hour(path: str):
    a = decode(path)
    if a is None:
        return None
    t0 = hour_start_ms(path)
    ms = a["ms"].astype(np.int64)
    bid = a["bid"].astype(np.int64)
    ask = a["ask"].astype(np.int64)
    vol = (a["av"].astype(np.float64) + a["bv"].astype(np.float64))
    minute = ms // 60000
    df = pd.DataFrame({"m": minute, "bid": bid, "ask": ask, "spr": ask - bid, "vol": vol})
    g = df.groupby("m", sort=True)
    out = pd.DataFrame({
        "bo": g.bid.first(), "bh": g.bid.max(), "bl": g.bid.min(), "bc": g.bid.last(),
        "ao": g.ask.first(), "ah": g.ask.max(), "al": g.ask.min(), "ac": g.ask.last(),
        "spr": g.spr.mean(), "spr_max": g.spr.max(), "n": g.bid.size(), "vol": g.vol.sum(),
    })
    out.index = t0 + out.index.values.astype(np.int64) * 60000
    return out


def main():
    files = sorted(glob.glob(str(RAW / "*.bi5")))
    parts = []
    with ProcessPoolExecutor(max_workers=int(sys.argv[1]) if len(sys.argv) > 1 else 2) as ex:
        for i, r in enumerate(ex.map(bars_for_hour, files, chunksize=64)):
            if r is not None:
                parts.append(r)
            if i % 1000 == 0:
                print(i, len(files), flush=True)
    df = pd.concat(parts)
    df.index.name = "ts"
    df = df.reset_index()
    for c in ["bo", "bh", "bl", "bc", "ao", "ah", "al", "ac", "spr", "spr_max"]:
        df[c] = df[c] / 1000.0
    df = df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    print("rows", len(df), "->", OUT)


if __name__ == "__main__":
    main()
