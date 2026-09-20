"""ICT Sniper backtest runner (canonical path).

Signals on closed 5m/15m bars (causal resample), fills and exits on 1m
bars -- the intrabar-honest resolution that caught the old same-bar
artifact.  Executes the spec's rule set C via GuardedSim:

  * passive maker limit at the FVG CE, fill only on trade-through,
  * TP fixed (120-200 bps default range) or ATR-scaled, SL beyond the
    sweep wick (taker+slip), optional breakeven/lock gate (be_after_r),
  * time exit after `time_exit_bars` TF bars,
  * max N fills per symbol per day (default 3),
  * circuit breaker: 2 consecutive losses halt the symbol for the day,
  * dynamic coin-finder pre-filter (quant.universe.coin_filter -- the
    historical port of atrscan.py): per UTC day, only the top-N symbols
    by ATR% (>= min_atr_pct, 24h volume >= min_vol_usdt) trade.

Example:
  python3 quant/experiments/run_ict_sniper.py \\
      --window 2025-06-01:2026-09-01 --tf 15 \\
      --top-n 20 --min-atr-pct 2.5 --min-vol-usdt 1000000 \\
      --max-trades-per-day 3 --circuit-losses 2 \\
      --params '{"tp_mode":"atr","tp_atr_mult":2.0,"be_after_r":1.0,"be_to_r":0.0}'

Results: stdout JSON summary + quant/data/experiments/reports/<id>/
(trades.parquet, equity.parquet, record.json) + leaderboard.jsonl entry.
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

from quant.engine.backtest import CostModel, stats  # noqa: E402
from quant.engine.guards import GuardedSim  # noqa: E402
from quant.lib import store  # noqa: E402
from quant.strategies.ict_sniper import build_events, resample_causal_1m  # noqa: E402
from quant.universe.coin_filter import scan_daily  # noqa: E402
from scalper.pa.sniper import SniperParams  # noqa: E402

log = logging.getLogger("quant.experiments.run_ict_sniper")

LEADERBOARD = store.data_root() / "experiments" / "leaderboard.jsonl"
REPORTS = store.data_root() / "experiments" / "reports"
DAY_MS = 86_400_000


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


def load_frames(symbols, start_ms, end_ms, min_bars, warmup_ms,
                downcast32: bool = True
                ) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    """frames = window slice for the Sim; warm = window + warmup for the
    coin filter.  float32 downcast halves memory (the Sim casts anyway)."""
    frames: dict[str, pd.DataFrame] = {}
    warm: dict[str, pd.DataFrame] = {}
    funding: dict[str, pd.DataFrame] = {}
    for s in symbols:
        try:
            df = store.load_klines(s, start_ms=start_ms - warmup_ms,
                                   end_ms=end_ms)
        except FileNotFoundError:
            continue
        if len(df) < min_bars:
            continue
        if downcast32:
            for col in ("open", "high", "low", "close", "volume"):
                df[col] = df[col].astype(np.float32)
        warm[s] = df
        frames[s] = df[df["open_time"] >= start_ms].reset_index(drop=True)
        try:
            f = store.load_funding(s)
            if start_ms:
                f = f[f["calc_time"] >= start_ms]
            if end_ms:
                f = f[f["calc_time"] < end_ms]
            funding[s] = f
        except FileNotFoundError:
            pass
    return frames, warm, funding


def entry_breakdown(trades: list[dict]) -> dict:
    """Per-ENTRY economics: an entry (one fill) may close as one full-size
    leg or as TP1 + a runner leg.  Notional-weighted, so full-size stops
    weigh twice as much as half-size TP1 legs -- the honest unit."""
    z = {"n_entries": 0, "entry_wr": 0.0, "entry_net_bps": 0.0,
         "entry_gross_bps": 0.0, "entry_cost_bps": 0.0, "avg_sl_bps": 0.0,
         "scale_rate": 0.0, "avg_be_bps": 0.0, "avg_negkill_bps": 0.0,
         "avg_trim_bps": 0.0, "trim_rate": 0.0}
    if not trades:
        return z
    tr = pd.DataFrame(trades)
    tr["notional"] = tr["margin"] * tr["lev"]
    tr["ret_bps"] = tr["ret_pct"] * 100.0
    tr["cost_bps"] = (tr["fees"] + tr["funding"]) / tr["notional"] * 1e4
    tr["wret"] = tr["ret_bps"] * tr["notional"]
    tr["wcost"] = tr["cost_bps"] * tr["notional"]
    tr["key"] = list(zip(tr["symbol"], tr["entry_t"], tr["side"]))
    g = tr.groupby("key")
    ent = pd.DataFrame({"pnl": g["pnl"].sum(), "notional": g["notional"].sum(),
                        "wret": g["wret"].sum(), "wcost": g["wcost"].sum(),
                        "n_legs": g["pnl"].size(),
                        "first_ret": g["ret_bps"].first()})
    n = len(ent)
    sl = tr[tr["exit_reason"] == "sl"]
    be = tr[tr["exit_reason"] == "be"]
    nk = tr[tr["exit_reason"] == "negkill"]
    tm = tr[tr["exit_reason"] == "trim"]
    pres = tr.groupby("key")["exit_reason"].apply(set)
    scale_rate = float(pres.apply(lambda s: "tp1" in s).mean()) if n else 0.0
    trim_rate = float(pres.apply(lambda s: "trim" in s).mean()) if n else 0.0
    return {
        "n_entries": n,
        "entry_wr": round(float((ent["pnl"] > 0).mean()), 4),
        "entry_net_bps": round(
            float((ent["wret"].sum() - ent["wcost"].sum())
                  / ent["notional"].sum()), 2),
        "entry_gross_bps": round(
            float(ent["wret"].sum() / ent["notional"].sum()), 2),
        "entry_cost_bps": round(
            float(ent["wcost"].sum() / ent["notional"].sum()), 2),
        "avg_sl_bps": round(float(sl["ret_bps"].mean()), 2) if len(sl) else 0.0,
        "avg_be_bps": round(float(be["ret_bps"].mean()), 2) if len(be) else 0.0,
        "avg_negkill_bps": round(float(nk["ret_bps"].mean()), 2)
        if len(nk) else 0.0,
        "avg_trim_bps": round(float(tm["ret_bps"].mean()), 2)
        if len(tm) else 0.0,
        "scale_rate": round(scale_rate, 4),
        "trim_rate": round(trim_rate, 4),
    }


def trade_breakdown(trades: list[dict]) -> dict:
    if not trades:
        return {"tp1_pct": 0.0, "tp2_pct": 0.0, "tp_pct": 0.0,
                "sl_pct": 0.0, "be_pct": 0.0, "time_pct": 0.0,
                "negkill_pct": 0.0,
                "gross_bps": 0.0, "cost_bps": 0.0, "net_bps": 0.0,
                "w_gross_bps": 0.0, "w_cost_bps": 0.0, "w_net_bps": 0.0,
                "tp2_per_tp1": 0.0, "n": 0}
    tr = pd.DataFrame(trades)
    tr["ret_bps"] = tr["ret_pct"] * 100.0
    tr["notional"] = tr["margin"] * tr["lev"]
    tr["cost_bps"] = (tr["fees"] + tr["funding"]) / tr["notional"] * 1e4
    n = len(tr)
    dist = tr["exit_reason"].value_counts(normalize=True)
    n_tp1 = int((tr["exit_reason"] == "tp1").sum())
    n_tp2 = int((tr["exit_reason"] == "tp2").sum())
    w = tr["notional"]
    return {
        "tp1_pct": round(float(dist.get("tp1", 0.0)) * 100, 1),
        "tp2_pct": round(float(dist.get("tp2", 0.0)) * 100, 1),
        "tp_pct": round(float(dist.get("tp", 0.0)) * 100, 1),
        "sl_pct": round(float(dist.get("sl", 0.0)) * 100, 1),
        "be_pct": round(float(dist.get("be", 0.0)) * 100, 1),
        "time_pct": round(float(dist.get("time", 0.0)) * 100, 1),
        "negkill_pct": round(float(dist.get("negkill", 0.0)) * 100, 1),
        "trim_pct": round(float(dist.get("trim", 0.0)) * 100, 1),
        "gross_bps": round(float(tr["ret_bps"].mean()), 2),
        "cost_bps": round(float(tr["cost_bps"].mean()), 2),
        "net_bps": round(float((tr["ret_bps"] - tr["cost_bps"]).mean()), 2),
        # notional-weighted: the equity truth (full-size SL legs weigh 2x)
        "w_gross_bps": round(float((tr["ret_bps"] * w).sum() / w.sum()), 2),
        "w_cost_bps": round(float((tr["cost_bps"] * w).sum() / w.sum()), 2),
        "w_net_bps": round(
            float(((tr["ret_bps"] - tr["cost_bps"]) * w).sum() / w.sum()), 2),
        "tp2_per_tp1": round(n_tp2 / n_tp1, 3) if n_tp1 else 0.0,
        "n": n,
    }


def run(symbols, start_ms, end_ms, tf_minutes, params, top_k, alloc, lev,
        taker_fee_bps, maker_fee_bps, maker_entry_fee_bps, slippage_bps,
        funding_on,
        max_trades_per_day, circuit_losses, max_trades_global,
        daily_profit_cap_bps,
        start_equity, compound, min_bars,
        coin_top_n, min_atr_pct, min_vol_usdt, max_spread_bps) -> dict:
    t0 = time.time()
    frames, warm, funding = load_frames(symbols, start_ms, end_ms, min_bars,
                                        2 * DAY_MS)
    if not frames:
        raise RuntimeError("no symbols with data in window")

    # ---- dynamic coin-finder pre-filter (atrscan.py doctrine, causal) ----
    eligible: dict[tuple[str, int], bool] | None = None
    filt_summary = None
    if coin_top_n and coin_top_n > 0:
        eligible, filt_summary = scan_daily(
            warm, start_ms, end_ms, top_n=coin_top_n,
            min_atr_pct=min_atr_pct, min_vol_usdt=min_vol_usdt,
            max_spread_bps=max_spread_bps)
        log.info("coin filter: %d eligible symbol-days; median %s/day",
                 len(eligible),
                 filt_summary["n_eligible"].median() if len(filt_summary)
                 else 0)
    del warm   # keep only the window slice in memory

    # ---- detection + events ---------------------------------------------
    tf_ms = tf_minutes * 60_000
    tf_frames = {s: resample_causal_1m(df, tf_ms) for s, df in frames.items()}
    events = build_events(tf_frames, tf_minutes, params, alloc=alloc, lev=lev)
    if eligible is not None:
        keep: dict[int, list] = {}
        for key, evs in events.items():
            day = int(key // DAY_MS) * DAY_MS
            kept = [e for e in evs if eligible.get((e[0], day))]
            if kept:
                keep[key] = kept
        events = keep
    n_sig = sum(len(v) for v in events.values())
    # keep only symbols that still have signals (shrinks the Sim loop)
    sig_syms = {e[0] for evs in events.values() for e in evs}
    sim_frames = {s: df for s, df in frames.items() if s in sig_syms}
    log.info("signals: %d over %d symbols", n_sig, len(sim_frames))

    cost = CostModel(taker_fee_bps=taker_fee_bps, maker_fee_bps=maker_fee_bps,
                     maker_entry_fee_bps=maker_entry_fee_bps,
                     slippage_bps=slippage_bps, funding=funding_on)
    sim = GuardedSim(sim_frames, cost, funding=funding, start_equity=start_equity,
                     compound=compound,
                     max_trades_per_day=max_trades_per_day or None,
                     max_consecutive_losses=circuit_losses,
                     max_trades_global_per_day=max_trades_global,
                     daily_profit_cap_bps=daily_profit_cap_bps or None,
                     eligible=eligible)
    sim.events = events
    sim.run(top_k=top_k if top_k > 0 else None)
    st = stats(sim.trades, sim.equity_curve)
    bd = trade_breakdown(sim.trades)
    eb = entry_breakdown(sim.trades)

    rec = {
        "id": uuid.uuid4().hex[:12],
        "ts": time.time(),
        "strategy": "ict_sniper",
        "hypothesis": "sweep->MSS->FVG limit retest (coin-filtered universe)",
        "params": {k: v for k, v in params.__dict__.items()
                   if not callable(v)},
        "tf_minutes": tf_minutes,
        "symbols": sorted(sig_syms),
        "n_symbols": len(sig_syms),
        "n_loaded": len(frames),
        "start_ms": start_ms, "end_ms": end_ms,
        "top_k": top_k, "alloc": alloc, "lev": lev,
        "taker_fee_bps": taker_fee_bps, "maker_fee_bps": maker_fee_bps,
        "maker_entry_fee_bps": maker_entry_fee_bps,
        "slippage_bps": slippage_bps, "funding_on": funding_on,
        "max_trades_per_day": max_trades_per_day,
        "circuit_losses": circuit_losses,
        "max_trades_global_per_day": max_trades_global,
        "daily_profit_cap_bps": daily_profit_cap_bps,
        "coin_top_n": coin_top_n, "min_atr_pct": min_atr_pct,
        "min_vol_usdt": min_vol_usdt, "max_spread_bps": max_spread_bps,
        "n_signals": n_sig,
        "breaker_trips": sim.n_blocks,
        "profit_hits": sim.n_profit_hits,
        "runtime_s": round(time.time() - t0, 1),
        **st, **bd, **eb,
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    out_dir = REPORTS / rec["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(sim.trades).to_parquet(out_dir / "trades.parquet")
    pd.DataFrame(sim.equity_curve, columns=["t", "equity"]).to_parquet(
        out_dir / "equity.parquet")
    if filt_summary is not None:
        filt_summary.to_parquet(out_dir / "coin_filter.parquet")
    with open(out_dir / "record.json", "w") as f:
        json.dump(rec, f, indent=1, default=float)
    with open(LEADERBOARD, "a") as f:
        f.write(json.dumps(rec, default=float) + "\n")
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--window", default="2025-01-01:2026-09-01",
                    help="YYYY-MM-DD:YYYY-MM-DD")
    ap.add_argument("--symbols", default=None,
                    help="comma list; default = universe.json symbols")
    ap.add_argument("--tf", type=int, default=5, choices=[5, 15],
                    help="detection timeframe in minutes (signals)")
    ap.add_argument("--top-k", type=int, default=1)
    ap.add_argument("--alloc", type=float, default=0.5)
    ap.add_argument("--lev", type=float, default=20.0)
    ap.add_argument("--params", default="{}",
                    help="JSON overrides of SniperParams (e.g. "
                         "'{\"tp1_bps\":120,\"tp_max_bps\":500}')")
    # coin finder (atrscan.py doctrine; phase-7 data: loose set wins)
    ap.add_argument("--top-n", type=int, default=30,
                    help="top-N symbols by ATR% per day; 0 = filter off")
    ap.add_argument("--min-atr-pct", type=float, default=0.8)
    ap.add_argument("--min-vol-usdt", type=float, default=1e6)
    ap.add_argument("--max-spread-bps", type=float, default=None,
                    help="optional 1m-range spread proxy ceiling (off)")
    # guards (Hit & Run phase: no hard trade cap, daily profit cap on)
    ap.add_argument("--max-trades-per-day", type=int, default=0,
                    help="optional fill cap per symbol-day; 0 = PA dictates")
    ap.add_argument("--circuit-losses", type=int, default=2)
    ap.add_argument("--max-trades-global", type=int, default=0)
    ap.add_argument("--daily-profit-cap-bps", type=float, default=300.0,
                    help="halt a symbol for the day once its realized "
                         "un-leveraged move reaches this (0 = off)")
    # costs
    ap.add_argument("--taker-fee-bps", type=float, default=6.0,
                    help="Bitunix retail taker")
    ap.add_argument("--maker-fee-bps", type=float, default=2.0,
                    help="Bitunix retail maker (exits)")
    ap.add_argument("--maker-entry-fee-bps", type=float, default=None,
                    help="maker ENTRY fee override (VIP sim)")
    ap.add_argument("--slippage-bps", type=float, default=1.0)
    ap.add_argument("--no-funding", action="store_true")
    ap.add_argument("--start-equity", type=float, default=1000.0)
    ap.add_argument("--no-compound", action="store_true")
    ap.add_argument("--min-bars", type=int, default=20_000)
    args = ap.parse_args()

    start_ms, end_ms = parse_window(args.window)
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        uni = json.loads(open(store.data_root() / "universe.json").read())
        symbols = sorted(uni["symbols"])

    params = SniperParams(**json.loads(args.params))
    rec = run(symbols, start_ms, end_ms, args.tf, params, args.top_k,
              args.alloc, args.lev, args.taker_fee_bps, args.maker_fee_bps,
              args.maker_entry_fee_bps, args.slippage_bps,
              not args.no_funding,
              args.max_trades_per_day, args.circuit_losses,
              args.max_trades_global or None,
              args.daily_profit_cap_bps,
              args.start_equity, not args.no_compound, args.min_bars,
              args.top_n, args.min_atr_pct, args.min_vol_usdt,
              args.max_spread_bps)
    print(json.dumps(rec, indent=1, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
