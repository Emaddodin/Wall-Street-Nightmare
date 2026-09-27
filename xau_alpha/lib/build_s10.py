"""
xau_alpha/lib/build_s10.py
Build 10-second bid/ask bars for XAUUSD from Dukascopy hourly tick files (execution-resolution data for the simulator).

Output: xau_alpha/data/s10_ba.parquet with int32 prices (x1000):
  ts (int64 ms UTC, bucket open), bo bh bl bc, ao ah al ac, n
"""
import glob
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_m1 import RAW, decode, hour_start_ms  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "data/s10_ba.parquet"


def bars_for_hour(path: str):
    a = decode(path)
    if a is None:
        return None
    t0 = hour_start_ms(path)
    b10 = a["ms"].astype(np.int64) // 10000
    bid = a["bid"].astype(np.int32)
    ask = a["ask"].astype(np.int32)
    df = pd.DataFrame({"k": b10, "bid": bid, "ask": ask})
    g = df.groupby("k", sort=True)
    out = pd.DataFrame({
        "bo": g.bid.first(), "bh": g.bid.max(), "bl": g.bid.min(), "bc": g.bid.last(),
        "ao": g.ask.first(), "ah": g.ask.max(), "al": g.ask.min(), "ac": g.ask.last(),
        "n": g.bid.size().astype(np.int32),
    })
    out.index = t0 + out.index.values.astype(np.int64) * 10000
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
    df = df.reset_index().sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    df.to_parquet(OUT, index=False, compression="zstd")
    print("rows", len(df), "->", OUT)


if __name__ == "__main__":
    main()
