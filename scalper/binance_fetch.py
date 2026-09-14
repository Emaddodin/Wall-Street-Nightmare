"""INTERNET DATABASE -- Binance Vision bulk downloader (research data).

Free monthly 1m-kline zips for USDT-M perpetuals, 2019..now.  Binance is
NOT our venue (Bitunix is), so this store lives separately as
data/candles_binance and is used for training/hunts only -- venue-labeled,
never mixed into the live book.

Resumable: months already stored are skipped.
"""
from __future__ import annotations

import io
import logging
import os
import sys
import time
import zipfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

log = logging.getLogger("scalper.binance")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

DATA = Path(os.getenv("SCALPER_DATA", str(ROOT / "data")))
OUT = DATA / "candles_binance"
TOP_N = int(os.environ.get("SCALPER_BINANCE_TOP", "50"))
MONTHS = int(os.environ.get("SCALPER_BINANCE_MONTHS", "6"))
BASE = "https://data.binance.vision/data/futures/um/monthly/klines"


def month_list(n: int) -> list[str]:
    out = []
    now = datetime.now(timezone.utc)
    y, m = now.year, now.month
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y}-{m:02d}")
    return out


def fetch_zip(url: str, tries: int = 3) -> bytes | None:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:
            if i == tries - 1:
                log.warning("GET %s failed: %s", url, e)
                return None
            time.sleep(2)


def main() -> int:
    from market_data.client import BitunixPublic
    import pandas as pd

    OUT.mkdir(parents=True, exist_ok=True)
    months = month_list(MONTHS)
    log.info("binance vision: top %d symbols x %d months (%s..%s)",
             TOP_N, MONTHS, months[-1], months[0])
    client = BitunixPublic(pause=0.1)
    ticks = client.tickers()
    rows = [t for t in ticks if t["symbol"].endswith("USDT")
            and t["usdt_volume_24h"] > 0]
    rows.sort(key=lambda t: -t["usdt_volume_24h"])
    symbols = [t["symbol"] for t in rows[:TOP_N]]
    log.info("symbols: %s", ",".join(symbols[:10]) + " ...")

    done = skipped = failed = 0
    t0 = time.time()
    for sym in symbols:
        per = OUT / f"{sym}_1m.parquet"
        frames = []
        for ym in months:
            if per.exists():
                try:
                    have = pd.read_parquet(per)
                    if ((have["open_time"] // 2_628_000_000)
                            == ym).any():          # month already inside
                        continue
                except Exception:
                    pass
            url = f"{BASE}/{sym}/1m/{sym}-1m-{ym}.zip"
            raw = fetch_zip(url)
            if raw is None:
                failed += 1
                continue
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    csv = z.read(z.namelist()[0]).decode()
                df = pd.read_csv(io.StringIO(csv), header=None,
                                 skiprows=1,       # the venue CSV has a header
                                 usecols=[0, 1, 2, 3, 4, 7],
                                 names=["open_time", "open", "high", "low",
                                        "close", "volume"])
                frames.append(df)
                log.info("[%s] %s: %d bars", sym, ym, len(df))
            except Exception as e:
                failed += 1
                log.warning("[%s] %s parse failed: %s", sym, ym, e)
        if not frames:
            continue
        df = pd.concat(frames).drop_duplicates("open_time").sort_values("open_time")
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = df[col].astype(float)
        if per.exists():
            try:
                old = pd.read_parquet(per)
                df = pd.concat([old, df]).drop_duplicates(
                    "open_time", keep="last").sort_values("open_time")
            except Exception:
                pass
        df.reset_index(drop=True).to_parquet(per, index=False)
        done += 1
        log.info("[%s] stored %d bars total", sym, len(df))
        if done and done % 10 == 0:
            el = (time.time() - t0) / 60.0
            log.info("progress: %d done, %d failed, %.0f min", done, failed, el)
    log.info("binance done: %d symbols, %d failed, %.0f min",
             done, failed, (time.time() - t0) / 60.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
