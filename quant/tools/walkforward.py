"""Walk-forward evaluation: split the data into train / validation / test
windows rolled forward.  For each roll: fit (if the strategy supports
fitting) on train, pick the best params on validation, evaluate on the
unseen test window; the test windows concatenate into the OOS equity path.

For non-parametric strategies (threshold rules), 'fit' = grid search over
the given param grid on the train window, selection on validation.

Usage:
  python3 quant/tools/walkforward.py --strategy s1_momentum_burst \
      --grid '{"r5_thresh_bps":[40,60,80,100]}' \
      --train-days 60 --val-days 20 --test-days 20 \
      --symbols BTCUSDT,ETHUSDT,SOLUSDT
"""
from __future__ import annotations

import argparse
import itertools
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.engine.backtest import CostModel, Sim, stats  # noqa: E402
from quant.lib import store  # noqa: E402
from quant.experiments.runner import load_strategy  # noqa: E402
from quant.strategies.base import events_from_signals  # noqa: E402
from quant.lib import features as featmod  # noqa: E402

log = logging.getLogger("quant.tools.walkforward")
DAY = 86_400_000


def grid_params(grid: dict) -> list[dict]:
    keys = list(grid)
    for combo in itertools.product(*[grid[k] for k in keys]):
        yield dict(zip(keys, combo))


def run_one(strategy_name: str, params: dict, frames: dict[str, pd.DataFrame],
            funding: dict, top_k: int, cost: CostModel,
            min_score: float | None) -> dict:
    feat_frames = {}
    for s, df in frames.items():
        f = featmod.compute_base(df)
        if len(f):
            feat_frames[s] = f
    events = events_from_signals(feat_frames, load_strategy(strategy_name,
                                                            params))
    sim = Sim(frames, cost, funding=funding, start_equity=100.0)
    sim.events = events
    sim.run(top_k=top_k, min_score=min_score)
    st = stats(sim.trades, sim.equity_curve)
    st["n_signals"] = sum(len(v) for v in events.values())
    st["trades_list"] = sim.trades
    return st


def window_frames(symbols: list[str], start_ms: int, end_ms: int,
                  min_bars: int) -> tuple[dict, dict]:
    frames, funding = {}, {}
    for s in symbols:
        try:
            df = store.load_klines(s, start_ms=start_ms, end_ms=end_ms)
        except FileNotFoundError:
            continue
        if len(df) < min_bars:
            continue
        frames[s] = df
        try:
            f = store.load_funding(s)
            f = f[(f["calc_time"] >= start_ms) & (f["calc_time"] < end_ms)]
            funding[s] = f
        except FileNotFoundError:
            pass
    return frames, funding


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--grid", required=True, help="JSON param grid")
    ap.add_argument("--train-days", type=int, default=60)
    ap.add_argument("--val-days", type=int, default=20)
    ap.add_argument("--test-days", type=int, default=20)
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--top-k", type=int, default=1)
    ap.add_argument("--min-score", type=float, default=None)
    ap.add_argument("--taker-fee-bps", type=float, default=5.0)
    ap.add_argument("--slippage-bps", type=float, default=1.0)
    ap.add_argument("--no-funding", action="store_true")
    ap.add_argument("--start", default=None, help="YYYY-MM-DD")
    ap.add_argument("--min-bars", type=int, default=20_000)
    ap.add_argument("--metric", default="total_pnl",
                    help="selection metric on validation")
    args = ap.parse_args()

    grid = json.loads(args.grid)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    cost = CostModel(taker_fee_bps=args.taker_fee_bps,
                     slippage_bps=args.slippage_bps,
                     funding=not args.no_funding)

    # find data range
    lo = hi = None
    for s in symbols:
        try:
            df = store.load_klines(s)
        except FileNotFoundError:
            continue
        lo = df["open_time"].min() if lo is None else min(lo, df["open_time"].min())
        hi = df["open_time"].max() if hi is None else max(hi, df["open_time"].max())
    if args.start:
        lo = int(datetime.fromisoformat(args.start).replace(
            tzinfo=timezone.utc).timestamp() * 1000)

    train, val, test = (args.train_days * DAY, args.val_days * DAY,
                        args.test_days * DAY)
    pos = lo
    rolls = []
    t0 = time.time()
    while pos + train + val + test <= hi:
        t_s, v_s, v_e, t_e = (pos, pos + train, pos + train + val,
                              pos + train + val + test)
        log.info("roll %s .. %s", datetime.fromtimestamp(t_s / 1000, tz=timezone.utc),
                 datetime.fromtimestamp(t_e / 1000, tz=timezone.utc))
        tr_f, tr_fund = window_frames(symbols, t_s, v_s, args.min_bars)
        va_f, va_fund = window_frames(symbols, v_s, v_e, args.min_bars)
        if len(tr_f) < 2 or len(va_f) < 2:
            pos += test
            continue
        best = None
        for params in grid_params(grid):
            tr_res = run_one(args.strategy, params, tr_f, tr_fund,
                             args.top_k, cost, args.min_score)
            va_res = run_one(args.strategy, params, va_f, va_fund,
                             args.top_k, cost, args.min_score)
            score = va_res.get(args.metric, -1e18)
            if best is None or score > best[0]:
                best = (score, params, tr_res, va_res)
        if best is None:
            pos += test
            continue
        _, best_params, tr_res, va_res = best
        te_f, te_fund = window_frames(symbols, v_e, t_e, args.min_bars)
        te_res = run_one(args.strategy, best_params, te_f, te_fund,
                         args.top_k, cost, args.min_score)
        te_res["trades_list"] = None
        rolls.append({
            "train_start": t_s, "val_start": v_s, "test_start": v_e,
            "test_end": t_e, "best_params": best_params,
            "train": {k: v for k, v in tr_res.items() if k != "trades_list"},
            "val": {k: v for k, v in va_res.items() if k != "trades_list"},
            "test": {k: v for k, v in te_res.items() if k != "trades_list"},
        })
        log.info("best %s -> test trades=%d pnl=%.2f", best_params,
                 te_res["trades"], te_res.get("total_pnl", 0))
        pos += test

    print(json.dumps(rolls, indent=1, default=float))
    if rolls:
        test_pnl = [r["test"].get("total_pnl", 0) for r in rolls]
        test_trades = sum(r["test"].get("trades", 0) for r in rolls)
        wins = [r["test"].get("win_rate", 0) for r in rolls]
        print(f"=== {len(rolls)} rolls; OOS trades={test_trades} "
              f"pnl={sum(test_pnl):.2f} "
              f"wr={np.mean(wins):.3f} ({(time.time()-t0)/60:.1f} min)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
