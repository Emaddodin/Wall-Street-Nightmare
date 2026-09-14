#!/usr/bin/env python3
"""FROM-SCRATCH R&D -- baseline + strategy-family grid on the existing data.

Uses ONLY the data that already exists (venue store + internet store) and
the ORIGINAL strategy spec.  No last-night configs, no brain gate, no
evolved knobs.  Outputs a per-variant record table.
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
log = logging.getLogger("scalper.fresh")
logging.getLogger("scalper.paper").setLevel(logging.WARNING)

from config.loader import Config, load_config  # noqa: E402
from engine import BacktestEngine  # noqa: E402
from strategies.inventor import WARMUP_MS, stats_from_trades  # noqa: E402

frames: dict = {}
venue_syms = set()
for store_dir, is_venue in (("data/candles", True),
                            ("data/candles_binance", False)):
    for p in sorted(glob.glob(f"{store_dir}/*.parquet")):
        s = p.split("/")[-1].split("_")[0]
        df = pd.read_parquet(p)
        if len(df) >= 60 * 1440 and (s not in frames or is_venue):
            frames[s] = df
        if is_venue:
            venue_syms.add(s)
end_ms = min(int(frames[s]["open_time"].max())
             for s in frames if s in venue_syms) + 60_000
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 14
start = end_ms - DAYS * 86_400_000
split = start + int((end_ms - start) * 2 / 3)


def run_variant(label: str, over: dict) -> dict:
    raw = load_config(extra_file="config/original.yaml").raw()
    for path, val in over.items():
        d = raw
        parts = path.split(".")
        for k in parts[:-1]:
            d = d[k]
        d[parts[-1]] = val
    cfg = Config(raw)
    eng = BacktestEngine(cfg)
    sds = eng.prepare(frames, start - WARMUP_MS, end_ms)
    res = eng.run(sds, start - WARMUP_MS, end_ms)
    trades = [t for t in res.trades
              if not t.get("phantom") and (t.get("ts_ms") or 0) >= split]
    st = stats_from_trades(trades, res.equity_curve)
    wins = [t for t in trades if (t.get("pnl") or 0) > 0]
    losses = [t for t in trades if (t.get("pnl") or 0) <= 0]
    tp = sum(1 for t in trades if str(t.get("exit_reason", "")).startswith("TP"))
    sl = sum(1 for t in trades if t.get("exit_reason") == "STOP")
    dur = [((t.get("ts_ms") or 0) - (t.get("opened_ms") or 0)) // 60_000
           for t in trades]
    rec = {"variant": label, **st,
           "avg_win_r": round(sum(t["pnl_r"] or 0 for t in wins) / len(wins), 2)
           if wins else None,
           "avg_loss_r": round(sum(t["pnl_r"] or 0 for t in losses) / len(losses), 2)
           if losses else None,
           "tp_hits": tp, "sl_hits": sl,
           "avg_held_min": round(sum(dur) / len(dur), 1) if dur else None,
           "trades_per_day": round(len(trades) / (DAYS / 3), 2)}
    log.info("%s: n=%d wr=%.0f%% avgR %+.2f pf %.2f dd %.0f%%",
             label, st["n"], st["wr"] * 100, st["avg_r"], st["pf"],
             st["max_dd_pct"])
    return rec


if __name__ == "__main__":
    t0 = time.time()
    log.info("FRESH R&D: %d symbols, %dd window, pristine config",
             len(frames), DAYS)
    out = []
    out.append(run_variant("BASELINE D+bos", {}))
    out.append(run_variant("D+trail", {"tp.structure.runner_exit": "trail"}))
    out.append(run_variant("A r1.5", {"tp.model": "A", "tp.fixed_r": 1.5}))
    out.append(run_variant("A r2", {"tp.model": "A", "tp.fixed_r": 2.0}))
    out.append(run_variant("A r3", {"tp.model": "A", "tp.fixed_r": 3.0}))
    out.append(run_variant("B partial", {"tp.model": "B"}))
    out.append(run_variant("C struct", {"tp.model": "C"}))
    out.append(run_variant("D first75+trail",
                           {"tp.structure.runner_exit": "trail",
                            "tp.structure.first_frac": 0.75,
                            "tp.structure.runner_frac": 0.25}))
    Path("data/state/fresh_grid.json").write_text(json.dumps(
        {"as_of_ms": end_ms, "days": DAYS, "rows": out}, indent=1))
    log.info("grid done in %.0fs -> data/state/fresh_grid.json", time.time() - t0)
    for r in sorted(out, key=lambda r: -(r["avg_r"] or -99)):
        print(f"  {r['variant']:16s} n={r['n']:3d} wr={r['wr']:.0%} "
              f"avgR {r['avg_r']:+.2f} pf={r['pf']:.2f} "
              f"winR {r['avg_win_r']} lossR {r['avg_loss_r']} dd {r['max_dd_pct']:.0f}%")
