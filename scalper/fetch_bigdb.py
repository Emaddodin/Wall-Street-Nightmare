"""BIG-DATABASE builder -- fill the candle store with deep 1m history for
the top volume coins so the lab and the brain train on months, not days.

Resumable: symbols that already have enough bars are skipped, and pages are
saved as they arrive, so a restart continues where it left off.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

log = logging.getLogger("scalper.bigdb")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

DATA = Path(os.getenv("SCALPER_DATA", str(ROOT / "data")))
STORE = DATA / "candles"
DAYS_DEEP = int(os.environ.get("SCALPER_BIGDB_DAYS", "90"))      # top tier
DAYS_MID = int(os.environ.get("SCALPER_BIGDB_DAYS_MID", "60"))   # second tier
TOP_N = int(os.environ.get("SCALPER_BIGDB_TOP", "40"))
MID_N = int(os.environ.get("SCALPER_BIGDB_MID", "30"))


def main() -> int:
    from market_data.client import BitunixPublic
    from market_data.store import CandleStore
    import pandas as pd

    client = BitunixPublic(pause=0.15)
    store = CandleStore(STORE)
    ticks = client.tickers()
    rows = [t for t in ticks if t["symbol"].endswith("USDT")
            and t["usdt_volume_24h"] > 0]
    rows.sort(key=lambda t: -t["usdt_volume_24h"])
    plan = [(t["symbol"], DAYS_DEEP) for t in rows[:TOP_N]]
    plan += [(t["symbol"], DAYS_MID) for t in rows[TOP_N:TOP_N + MID_N]]
    log.info("plan: %d symbols (top %d x %dd, next %d x %dd)",
             len(plan), TOP_N, DAYS_DEEP, MID_N, DAYS_MID)
    end_ms = int(time.time() * 1000) // 60_000 * 60_000
    done = skipped = failed = 0
    t0 = time.time()
    for sym, days in plan:
        try:
            have = store.load(sym, "1m")
            if len(have) >= days * 1440:
                skipped += 1
                continue
            log.info("[%s] need %dd, have %d bars", sym, days, len(have))
            df = client.klines(sym, "1m", days * 1440, end_ms=end_ms)
            if len(df) < 1000:
                failed += 1
                log.warning("[%s] venue returned only %d bars", sym, len(df))
                continue
            if len(have):
                df = pd.concat([have, df]).drop_duplicates(
                    subset="open_time", keep="last").sort_values("open_time")
            df = df[df["open_time"] >= end_ms - (days + 2) * 86_400_000]
            store.save(sym, "1m", df.reset_index(drop=True))
            done += 1
            log.info("[%s] stored %d bars", sym, len(df))
        except Exception as e:
            failed += 1
            log.warning("[%s] failed: %s", sym, e)
        if done and done % 10 == 0:
            el = (time.time() - t0) / 60.0
            log.info("progress: %d done, %d skipped, %d failed, %.0f min",
                     done, skipped, failed, el)
    log.info("BIGDB done: %d fetched, %d skipped, %d failed in %.0f min",
             done, skipped, failed, (time.time() - t0) / 60.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
