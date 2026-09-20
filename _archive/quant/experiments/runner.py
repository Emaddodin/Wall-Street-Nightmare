"""Experiment runner + leaderboard.

Every experiment records: id, strategy family, hypothesis, params, dataset
window, symbols, costs, and the full metric set.  Results append to
quant/data/experiments/leaderboard.jsonl; a markdown leaderboard is
regenerated from it.

Usage:
  python3 quant/experiments/runner.py --strategy s1_momentum_burst \
      --params '{"r5_thresh_bps":60}' --window 2025-01-01:2026-09-01 \
      --symbols BTCUSDT,ETHUSDT,SOLUSDT --top-k 1
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.engine.backtest import CostModel, Sim, stats  # noqa: E402
from quant.lib import store  # noqa: E402
from quant.strategies.base import events_from_signals  # noqa: E402

log = logging.getLogger("quant.experiments.runner")

LEADERBOARD = store.data_root() / "experiments" / "leaderboard.jsonl"
REPORTS = store.data_root() / "experiments" / "reports"

STRATEGIES = {
    "s1_momentum_burst": "quant.strategies.s1_momentum_burst.MomentumBurst",
    "s2_cs_fade": "quant.strategies.s2_cs_fade.CrossSectionalFade",
    "s3_cond_fade": "quant.strategies.s3_cond_fade.CondFade",
    "s5_cond_fade_1m": "quant.strategies.s5_cond_fade_1m.CondFade1m",
    "s6_selective_fade": "quant.strategies.s6_selective_fade.SelectiveFade",
}


def resample_frames(frames: dict[str, pd.DataFrame], interval: str) -> dict:
    if interval != "5m":
        return frames
    out = {}
    for s, df in frames.items():
        t = df["open_time"].to_numpy()
        bucket = t // 300_000 * 300_000
        g = df.groupby(bucket)
        out[s] = g.agg(open=("open", "first"), high=("high", "max"),
                       low=("low", "min"), close=("close", "last"),
                       volume=("volume", "sum")).reset_index().rename(
                           columns={"index": "open_time"})
    return out


def load_strategy(name: str, params: dict):
    import importlib
    mod_name, cls_name = STRATEGIES[name].rsplit(".", 1)
    mod = importlib.import_module(mod_name)
    cls = getattr(mod, cls_name)
    return cls(**params)


def parse_window(w: str | None) -> tuple[int | None, int | None]:
    if not w:
        return None, None
    a, _, b = w.partition(":")
    def conv(x):
        if not x:
            return None
        return int(datetime.fromisoformat(x).replace(
            tzinfo=timezone.utc).timestamp() * 1000)
    return conv(a), conv(b)


def run_experiment(strategy_name: str, params: dict, symbols: list[str],
                   start_ms: int | None, end_ms: int | None,
                   top_k: int = 1, min_score: float | None = None,
                   taker_fee_bps: float = 5.0, slippage_bps: float = 1.0,
                   funding_on: bool = True, start_equity: float = 100.0,
                   hypothesis: str = "", allow_pyramiding: bool = False,
                   min_bars: int = 20_000, resample: str | None = None,
                   max_positions: int = 0, compound: bool = True,
                   signal_resample: str | None = None) -> dict:
    t0 = time.time()
    strat = load_strategy(strategy_name, params)
    frames: dict[str, pd.DataFrame] = {}
    funding: dict[str, pd.DataFrame] = {}
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
            if start_ms:
                f = f[f["calc_time"] >= start_ms]
            if end_ms:
                f = f[f["calc_time"] < end_ms]
            funding[s] = f
        except FileNotFoundError:
            pass
    if len(frames) < 2:
        raise RuntimeError("not enough symbols with data in window")
    sim_frames = frames
    if signal_resample:
        sig_frames = resample_frames(frames, signal_resample)
    elif resample:
        sig_frames = resample_frames(frames, resample)
        sim_frames = sig_frames
    else:
        sig_frames = frames
    log.info("loaded %d symbols, %d bars total",
             len(sim_frames), sum(len(d) for d in sim_frames.values()))

    if hasattr(strat, "events"):
        # cross-sectional / panel strategies take the raw frames
        events = strat.events(sig_frames)
    else:
        # per-symbol feature strategies
        from quant.lib import features
        feat_frames: dict[str, pd.DataFrame] = {}
        for s, df in frames.items():
            metrics = funding_f = None
            try:
                metrics = store.load_metrics(s)
            except FileNotFoundError:
                pass
            try:
                funding_f = store.load_funding(s)
            except FileNotFoundError:
                pass
            f = features.compute_symbol(s, df, metrics, funding_f)
            if len(f):
                feat_frames[s] = f
        if not feat_frames:
            raise RuntimeError("no features computed")
        events = events_from_signals(feat_frames, strat)
    n_sig = sum(len(v) for v in events.values())
    log.info("signals: %d", n_sig)
    events_out = getattr(strat, "events_out", None)

    cost = CostModel(taker_fee_bps=taker_fee_bps, slippage_bps=slippage_bps,
                     funding=funding_on)
    sim = Sim(sim_frames, cost, funding=funding, start_equity=start_equity,
              compound=compound)
    sim.events = events
    sim.run(top_k=top_k if top_k > 0 else None,
            min_score=min_score, allow_pyramiding=allow_pyramiding)

    st = stats(sim.trades, sim.equity_curve)
    rec = {
        "id": uuid.uuid4().hex[:12],
        "ts": time.time(),
        "strategy": strategy_name,
        "hypothesis": hypothesis,
        "params": params,
        "symbols": sorted(frames),
        "n_symbols": len(frames),
        "start_ms": start_ms, "end_ms": end_ms,
        "top_k": top_k, "min_score": min_score,
        "taker_fee_bps": taker_fee_bps, "slippage_bps": slippage_bps,
        "funding_on": funding_on,
        "n_signals": n_sig,
        "runtime_s": round(time.time() - t0, 1),
        **st,
    }
    # persist
    LEADERBOARD.parent.mkdir(parents=True, exist_ok=True)
    REPORT_DIR = REPORTS / rec["id"]
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    if events_out:
        import shutil
        from pathlib import Path as _P
        rec["events_out"] = str(_P(events_out).name)
        shutil.copy(events_out, REPORT_DIR / _P(events_out).name)
    with open(LEADERBOARD, "a") as f:
        f.write(json.dumps(rec, default=float) + "\n")
    # trades detail
    pd.DataFrame(sim.trades).to_parquet(REPORT_DIR / "trades.parquet")
    eq = pd.DataFrame(sim.equity_curve, columns=["t", "equity"])
    eq.to_parquet(REPORT_DIR / "equity.parquet")
    with open(REPORT_DIR / "record.json", "w") as f:
        json.dump(rec, f, indent=1, default=float)
    log.info("[%s] trades=%d wr=%.3f pf=%.2f avg_daily_roe=%.2f%% "
             "days_100pct=%.1f%%", rec["id"], rec["trades"],
             rec.get("win_rate", 0), rec.get("profit_factor", 0),
             rec.get("avg_daily_roe", 0), rec.get("pct_days_100pct", 0))
    return rec


def leaderboard_md(n: int = 40) -> str:
    if not LEADERBOARD.exists():
        return "no experiments yet"
    rows = []
    with open(LEADERBOARD) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda r: -r.get("total_pnl", -1e18))
    cols = ["id", "strategy", "trades", "win_rate", "profit_factor",
            "avg_daily_roe", "median_daily_roe", "max_dd", "pct_days_100pct",
            "sharpe", "final_equity", "n_symbols"]
    out = ["| " + " | ".join(cols) + " |",
           "|" + "---|" * len(cols)]
    for r in rows[:n]:
        vals = []
        for c in cols:
            v = r.get(c, "")
            if isinstance(v, float):
                v = f"{v:.3f}" if c in ("win_rate", "sharpe") else f"{v:.1f}"
            vals.append(str(v))
        out.append("| " + " | ".join(vals) + " |")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--params", default="{}")
    ap.add_argument("--window", default=None,
                    help="YYYY-MM-DD:YYYY-MM-DD")
    ap.add_argument("--symbols", default=None,
                    help="comma list; default = universe.json tiers")
    ap.add_argument("--top-k", type=int, default=1)
    ap.add_argument("--min-score", type=float, default=None)
    ap.add_argument("--taker-fee-bps", type=float, default=5.0)
    ap.add_argument("--slippage-bps", type=float, default=1.0)
    ap.add_argument("--no-funding", action="store_true")
    ap.add_argument("--hypothesis", default="")
    ap.add_argument("--pyramid", action="store_true")
    ap.add_argument("--leaderboard", action="store_true")
    ap.add_argument("--min-bars", type=int, default=20_000)
    ap.add_argument("--resample", default=None, choices=[None, "5m"])
    ap.add_argument("--signal-resample", default=None,
                    choices=[None, "5m"],
                    help="signals on 5m closes, fills/exits on 1m bars")
    ap.add_argument("--no-compound", action="store_true",
                    help="fixed-base sizing (additive PnL, clean stats)")
    args = ap.parse_args()

    if args.leaderboard:
        print(leaderboard_md())
        return 0

    params = json.loads(args.params)
    start_ms, end_ms = parse_window(args.window)
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        uni = json.loads(open(store.data_root() / "universe.json").read())
        symbols = sorted(uni["symbols"])
    rec = run_experiment(
        args.strategy, params, symbols, start_ms, end_ms,
        top_k=args.top_k, min_score=args.min_score,
        taker_fee_bps=args.taker_fee_bps,
        slippage_bps=args.slippage_bps,
        funding_on=not args.no_funding,
        hypothesis=args.hypothesis,
        allow_pyramiding=args.pyramid,
        min_bars=args.min_bars,
        resample=args.resample,
        compound=not args.no_compound,
        signal_resample=args.signal_resample)
    print(json.dumps({k: v for k, v in rec.items()
                      if k not in ("params",)}, indent=1, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
