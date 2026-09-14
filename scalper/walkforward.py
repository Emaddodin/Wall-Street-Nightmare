#!/usr/bin/env python3
"""WALK-FORWARD -- Step 14: does a candidate keep working window after
window, or did it only win one slice?

Usage: python3 walkforward.py '<overrides-json>' [windows] [window_days]
Each window is split IS(2/3)/OOS(1/3); OOS stats per window + aggregate.
"""
import glob
import json
import logging
import sys

import pandas as pd

sys.path.insert(0, ".")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("scalper.wf")
logging.getLogger("scalper.paper").setLevel(logging.WARNING)

from config.loader import Config, load_config  # noqa: E402
from engine import BacktestEngine  # noqa: E402
from strategies.inventor import WARMUP_MS, stats_from_trades  # noqa: E402

frames: dict = {}
venue = set()
for store_dir, is_v in (("data/candles", True),
                        ("data/candles_binance", False)):
    for p in sorted(glob.glob(f"{store_dir}/*.parquet")):
        s = p.split("/")[-1].split("_")[0]
        df = pd.read_parquet(p)
        if len(df) >= 60 * 1440 and (s not in frames or is_v):
            frames[s] = df
        if is_v:
            venue.add(s)
end_ms = min(int(frames[s]["open_time"].max())
             for s in frames if s in venue) + 60_000

overrides = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
n_win = int(sys.argv[2]) if len(sys.argv) > 2 else 4
win_days = int(sys.argv[3]) if len(sys.argv) > 3 else 14

rows = []
for w in range(n_win):
    w_end = end_ms - w * win_days * 86_400_000
    w_start = w_end - win_days * 86_400_000
    split = w_start + int((w_end - w_start) * 2 / 3)
    raw = load_config(extra_file="config/original.yaml").raw()
    for path, val in overrides.items():
        d = raw
        for k in path.split(".")[:-1]:
            d = d[k]
        d[path.split(".")[-1]] = val
    cfg = Config(raw)
    eng = BacktestEngine(cfg)
    sds = eng.prepare(frames, w_start - WARMUP_MS, w_end)
    res = eng.run(sds, w_start - WARMUP_MS, w_end)
    trades = [t for t in res.trades
              if not t.get("phantom") and (t.get("ts_ms") or 0) >= split]
    st = stats_from_trades(trades, res.equity_curve)
    rows.append(st)
    log.info("window %d (end %d): n=%d wr=%.0f%% avgR %+.2f pf %.2f dd %.0f%%",
             w, w_end, st["n"], st["wr"] * 100, st["avg_r"], st["pf"],
             st["max_dd_pct"])

agg_n = sum(r["n"] for r in rows)
if agg_n:
    agg_wr = sum(r["n"] * r["wr"] for r in rows) / agg_n
    agg_r = sum(r["n"] * r["avg_r"] for r in rows) / agg_n
    pos = sum(1 for r in rows if r["avg_r"] > 0)
    print(f"\nAGGREGATE over {n_win} windows: n={agg_n} wr={agg_wr:.0%} "
          f"avgR {agg_r:+.2f} | windows with avgR>0: {pos}/{n_win}")
    print("per-window avgR:", [round(r["avg_r"], 2) for r in rows])
    print("per-window wr:  ", [round(r["wr"], 2) for r in rows])
else:
    print("no trades in any window")
