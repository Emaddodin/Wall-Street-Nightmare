"""Formal rolling walk-forward for the final candidate (fixed rule set,
no re-fitting -- the rule set is chosen once; every window is honest OOS).

Splits the data into sequential windows and reports each window's stats.

Usage:
  python3 quant/tools/roll_wf.py --strategy s5_cond_fade_1m \
      --params '{"min_z":1.5,...}' --window-months 3
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.engine.backtest import CostModel, Sim, stats  # noqa: E402
from quant.lib import store  # noqa: E402
from quant.experiments.runner import load_strategy  # noqa: E402

log = logging.getLogger("quant.tools.roll_wf")
DAY = 86_400_000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--params", required=True)
    ap.add_argument("--window-months", type=int, default=3)
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="2026-09-01")
    ap.add_argument("--resample", default=None, choices=[None, "5m"])
    ap.add_argument("--signal-resample", default=None, choices=[None, "5m"])
    ap.add_argument("--top-k", type=int, default=0)
    ap.add_argument("--min-bars", type=int, default=300_000)
    ap.add_argument("--taker-fee-bps", type=float, default=6.0)
    ap.add_argument("--slippage-bps", type=float, default=2.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    params = json.loads(args.params)
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    from quant.experiments.runner import resample_frames
    rows = []
    cur = start
    while cur < end:
        nxt = cur + pd.DateOffset(months=args.window_months)
        s_ms = int(cur.timestamp() * 1000)
        e_ms = int(nxt.timestamp() * 1000)
        cur = nxt
        strat = load_strategy(args.strategy, params)
        frames = {}
        funding = {}
        for s in sorted(json.loads(open(
                store.data_root() / "universe.json").read())["symbols"]):
            try:
                df = store.load_klines(s, start_ms=s_ms, end_ms=e_ms)
            except FileNotFoundError:
                continue
            if len(df) < args.min_bars * 0.5:
                continue
            frames[s] = df
            try:
                f = store.load_funding(s)
                f = f[(f["calc_time"] >= s_ms) & (f["calc_time"] < e_ms)]
                funding[s] = f
            except FileNotFoundError:
                pass
        if len(frames) < 2:
            log.info("window %s: not enough data", cur.date())
            continue
        sim_frames = frames
        if args.signal_resample:
            sig_frames = resample_frames(frames, args.signal_resample)
        elif args.resample:
            sig_frames = resample_frames(frames, args.resample)
            sim_frames = sig_frames
        else:
            sig_frames = frames
        events = strat.events(sig_frames)
        cost = CostModel(taker_fee_bps=args.taker_fee_bps,
                         slippage_bps=args.slippage_bps, funding=True)
        sim = Sim(sim_frames, cost, funding=funding, start_equity=100.0,
                  compound=False)
        sim.events = events
        sim.run(top_k=args.top_k if args.top_k > 0 else None)
        st = stats(sim.trades, sim.equity_curve)
        st["window_start"] = str(cur.date() - pd.DateOffset(
            months=args.window_months))
        st["window_end"] = str(cur.date())
        st["trades_list"] = None
        rows.append(st)
        log.info("[%s..%s] trades=%d PF=%.3f WR=%.3f pnl=%.1f",
                 st["window_start"], st["window_end"], st["trades"],
                 st["profit_factor"], st["win_rate"], st["total_pnl"])
    out = pd.DataFrame(rows)
    if args.out:
        out.to_csv(args.out, index=False)
    cols = ["window_start", "window_end", "trades", "win_rate",
            "profit_factor", "expectancy", "total_pnl", "max_dd",
            "trades_per_day"]
    print(out[cols].round(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
