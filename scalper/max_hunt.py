#!/usr/bin/env python3
"""MAX-CAPABILITY HUNT -- the whole database in one search.

Venue store (Bitunix, 130d) + internet store (Binance Vision, ~6mo) merged;
venue truth wins collisions.  Common calendar window so every symbol is
graded on the same history.  Biggest population the night allows.
"""
import glob
import json
import logging
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, ".")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("scalper.max")
logging.getLogger("scalper.paper").setLevel(logging.WARNING)

from strategies.inventor import invent  # noqa: E402

frames: dict = {}
for store_dir in ("data/candles", "data/candles_binance"):
    for p in sorted(glob.glob(f"{store_dir}/*.parquet")):
        s = p.split("/")[-1].split("_")[0]
        df = pd.read_parquet(p)
        if len(df) >= 60 * 1440:
            if s not in frames or store_dir == "data/candles":
                frames[s] = df

# one calendar window for every symbol: the venue store's newest bar
venue_syms = {x.split("/")[-1].split("_")[0]
              for x in glob.glob("data/candles/*.parquet")}
end_ms = min(int(frames[s]["open_time"].max())
             for s in frames if s in venue_syms) + 60_000
log.info("MAX HUNT: %d symbols, common window ending %d", len(frames), end_ms)

ic = {"population": 12, "generations": 2, "backtest_days": 60,
      "min_trades": 25}
t0 = time.time()
rep = invent(Path("data"), frames, inv_cfg=ic, end_ms=end_ms, log=log.info)


def clean(o):
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    import numpy as np
    if isinstance(o, (np.floating, np.integer)):
        return float(o)
    return o


rep = clean(rep)
Path("data/state/inventions.json").write_text(json.dumps(rep, indent=1))
champ = rep["champion"]
log.info("CHAMPION OOS: %s t wr %.0f%% pf %s avgR %s dd %s%%",
         champ.get("n"), (champ.get("wr") or 0) * 100, champ.get("pf"),
         champ.get("avg_r"), champ.get("max_dd_pct"))
for r in rep["inventions"]:
    log.info("IDEA %s: n %s wr %.0f%% pf %s avgR %s score %s | %s",
             r["name"], r.get("n"), (r.get("wr") or 0) * 100, r.get("pf"),
             r.get("avg_r"), r.get("score"), (r.get("changes") or [])[:3])
log.info("ADOPT: %s | trials %s best_z %s | sec %.0f",
         (rep.get("adopt") or {}).get("name"), rep.get("trials"),
         rep.get("best_z"), time.time() - t0)
pool = Path("data/state/lab_pool.jsonl")
if pool.exists():
    rows = [json.loads(l) for l in pool.read_text().splitlines() if l.strip()]
    log.info("POOL: %d trades", len(rows))
