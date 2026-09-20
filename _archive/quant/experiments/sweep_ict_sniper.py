"""ICT Sniper sweep, phase 7 -- The Venue Cost & Volatility Final Play.

The risk side is closed by data (wide sweep stop + 0.75R BE + TP1
120/75% is the structural optimum).  Phase 7 changes the environment:

  * Universe RE-TIGHTENED to the phase-2 hyper-volatile set (top-20/day,
    ATR >= 1.0%) -- the 5m looser tail (top-30/0.8%) dragged expansion
    size; the venue play needs coins whose legs reach +400 bps.
  * Two cost tiers on the SAME config (tp1=120, frac 0.75, tp2=400,
    te80, cap300, 0.75R BE, no killzones, no trim, sweep SL):
      tight_base -- Bitunix retail: taker 6, maker 2, slip 1
      tight_vip  -- VIP simulation (~3 bps round-trip): maker ENTRY 0,
                    maker exit 1.5, taker 3.0, slip 1.5

Reports ENTRY-level economics (n_entries, entry_wr, entry_net_bps,
entry_gross_bps vs entry_cost_bps, avg_sl_bps, scale_rate).

  python3 quant/experiments/sweep_ict_sniper.py --shard 0 --of 1
  python3 quant/experiments/sweep_ict_sniper.py --report
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.engine.backtest import CostModel, stats  # noqa: E402
from quant.engine.guards import GuardedSim  # noqa: E402
from quant.lib import store  # noqa: E402
from quant.strategies.ict_sniper import build_events, resample_causal_1m  # noqa: E402
from quant.universe.coin_filter import scan_daily  # noqa: E402
from scalper.pa.sniper import (STRUCTURE_COLS, SniperParams,  # noqa: E402
                               detect_structure)
from quant.experiments.run_ict_sniper import (  # noqa: E402
    entry_breakdown, load_frames, parse_window, trade_breakdown)

log = logging.getLogger("quant.experiments.sweep_ict_sniper")

OUT_DIR = store.data_root() / "experiments" / "sweep_ict_sniper"
DAY_MS = 86_400_000

# keys whose change invalidates the cached structural scan
_STRUCTURAL_KEYS = {"arm", "swing_fresh_bars", "sweep_max_age", "mss_max_gap",
                    "wick_frac", "disp_body_mult", "disp_body_range",
                    "disp_max_opp_wick", "disp_avg_window", "fvg_max_age",
                    "fvg_after", "entry_at", "fvg_mitigation",
                    "sl_buffer_atr_mult", "atr_period"}


def grid() -> list[dict]:
    base = {"sl_mode": "sweep", "be_after_r": 0.75, "be_after_bps": None,
            "be_to_r": 0.0, "neg_bars": None, "trim": False, "trim_frac": 0.5,
            "tp_min_bps": 400.0, "tp_max_bps": 400.0, "tp_mode": "fixed",
            "time_exit_bars": 80, "killzones": None,
            "tp1_bps": 120.0, "tp1_frac": 0.75,
            "daily_profit_cap_bps": 300.0}
    return [
        # Bitunix retail costs
        {**base, "label": "tight_base",
         "taker_fee_bps": 6.0, "maker_fee_bps": 2.0,
         "maker_entry_fee_bps": None, "slippage_bps": 1.0},
        # VIP sim (~3 bps round-trip): maker 0 entry, maker 1.5 exits,
        # taker 3, slip 1.5
        {**base, "label": "tight_vip",
         "taker_fee_bps": 3.0, "maker_fee_bps": 1.5,
         "maker_entry_fee_bps": 0.0, "slippage_bps": 1.5},
    ]


def run_config(cfg, frames, tf_frames, eligible, funding, struct_cache,
               args) -> dict:
    t0 = time.time()
    known = SniperParams.__dataclass_fields__
    params = SniperParams(**{k: v for k, v in cfg.items()
                             if k in known and k != "label"})
    events = build_events(tf_frames, args.tf, params, alloc=args.alloc,
                          lev=args.lev, structure=struct_cache)
    if eligible is not None:
        keep: dict[int, list] = {}
        for key, evs in events.items():
            day = int(key // DAY_MS) * DAY_MS
            kept = [e for e in evs if eligible.get((e[0], day))]
            if kept:
                keep[key] = kept
        events = keep
    n_sig = sum(len(v) for v in events.values())
    sig_syms = {e[0] for evs in events.values() for e in evs}
    sim_frames = {s: df for s, df in frames.items() if s in sig_syms}

    cost = CostModel(taker_fee_bps=cfg.get("taker_fee_bps", args.taker_fee_bps),
                     maker_fee_bps=cfg.get("maker_fee_bps", args.maker_fee_bps),
                     maker_entry_fee_bps=cfg.get("maker_entry_fee_bps", None),
                     slippage_bps=cfg.get("slippage_bps", args.slippage_bps),
                     funding=not args.no_funding)
    sim = GuardedSim(sim_frames, cost, funding=funding,
                     start_equity=args.start_equity,
                     compound=not args.no_compound,
                     max_trades_per_day=None,      # PA dictates frequency
                     max_consecutive_losses=args.circuit_losses,
                     daily_profit_cap_bps=cfg["daily_profit_cap_bps"],
                     eligible=eligible)
    sim.events = events
    sim.run(top_k=args.top_k if args.top_k > 0 else None)
    st = stats(sim.trades, sim.equity_curve)
    bd = trade_breakdown(sim.trades)
    eb = entry_breakdown(sim.trades)
    row = {"label": cfg["label"]}
    for k in ("killzones", "time_exit_bars", "tp_mode", "tp_min_bps",
              "tp_max_bps", "tp1_bps", "tp1_frac", "be_after_r",
              "be_after_bps", "be_to_r", "neg_bars", "sl_mode",
              "trim", "trim_frac", "daily_profit_cap_bps",
              "taker_fee_bps", "maker_fee_bps", "maker_entry_fee_bps",
              "slippage_bps"):
        row[k] = cfg.get(k)
    row.update({"n_signals": n_sig, "n_symbols_with_sig": len(sig_syms),
                "runtime_s": round(time.time() - t0, 1),
                "breaker_trips": sim.n_blocks,
                "profit_hits": sim.n_profit_hits,
                "trades": st.get("trades", 0),
                "win_rate": round(st.get("win_rate", 0.0), 4),
                "profit_factor": round(st.get("profit_factor", 0.0), 4),
                "expectancy": round(st.get("expectancy", 0.0), 4),
                "total_pnl": round(st.get("total_pnl", 0.0), 2),
                "avg_bars": round(st.get("avg_bars", 0.0), 1),
                "trades_per_day": round(st.get("trades_per_day", 0.0), 3),
                **bd, **eb})
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--window", default="2025-01-01:2026-09-01")
    ap.add_argument("--symbols", default=None)
    ap.add_argument("--tf", type=int, default=5, choices=[5, 15])
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--of", type=int, default=4)
    ap.add_argument("--labels", default=None,
                    help="comma list: run only these grid labels")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--top-k", type=int, default=1)
    ap.add_argument("--alloc", type=float, default=0.5)
    ap.add_argument("--lev", type=float, default=20.0)
    ap.add_argument("--top-n", type=int, default=30)
    ap.add_argument("--min-atr-pct", type=float, default=0.8)
    ap.add_argument("--min-vol-usdt", type=float, default=1e6)
    ap.add_argument("--max-spread-bps", type=float, default=None)
    ap.add_argument("--circuit-losses", type=int, default=2)
    ap.add_argument("--taker-fee-bps", type=float, default=6.0)
    ap.add_argument("--maker-fee-bps", type=float, default=2.0)
    ap.add_argument("--slippage-bps", type=float, default=1.0)
    ap.add_argument("--no-funding", action="store_true")
    ap.add_argument("--start-equity", type=float, default=1000.0)
    ap.add_argument("--no-compound", action="store_true")
    ap.add_argument("--min-bars", type=int, default=20_000)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    if args.report:
        return report()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cfgs = grid()
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        uni = json.loads(open(store.data_root() / "universe.json").read())
        symbols = sorted(uni["symbols"])
    start_ms, end_ms = parse_window(args.window)

    frames, warm, funding = load_frames(symbols, start_ms, end_ms,
                                        args.min_bars, 2 * DAY_MS)
    log.info("loaded %d symbols", len(frames))
    eligible, _ = scan_daily(warm, start_ms, end_ms, top_n=args.top_n,
                             min_atr_pct=args.min_atr_pct,
                             min_vol_usdt=args.min_vol_usdt,
                             max_spread_bps=args.max_spread_bps)
    log.info("eligible symbol-days: %d", len(eligible))
    del warm
    tf_frames = {s: resample_causal_1m(df, args.tf * 60_000)
                 for s, df in frames.items()}

    # one structural scan per shard: every grid config shares the same
    # sweep/MSS/FVG parameters, only the TP gates vary
    base = SniperParams()
    for cfg in cfgs:
        for k in _STRUCTURAL_KEYS:
            if k in cfg and cfg[k] != getattr(base, k):
                raise RuntimeError(
                    f"config {cfg['label']} overrides structural key {k}; "
                    "the cache would be wrong")
    log.info("building structural cache for %d symbols ...", len(tf_frames))
    t0 = time.time()
    struct_cache = {
        s: detect_structure(df.reset_index(drop=True), base)[STRUCTURE_COLS]
        for s, df in tf_frames.items()}
    log.info("structure cached in %.0fs", time.time() - t0)

    mine = [c for i, c in enumerate(cfgs)
            if i % args.of == args.shard]
    if args.labels:
        want = set(args.labels.split(","))
        mine = [c for c in mine if c["label"] in want]
    out_path = OUT_DIR / f"rows_{args.shard}.jsonl"
    with open(out_path, "a") as f:
        for cfg in mine:
            log.info("[shard %d] %s", args.shard, cfg["label"])
            row = run_config(cfg, frames, tf_frames, eligible, funding,
                             struct_cache, args)
            f.write(json.dumps(row, default=float) + "\n")
            f.flush()
            log.info("  n=%s wr=%.3f pf=%.2f net=%.2f bps tp1=%.1f%% "
                     "tp2=%.1f%% sl=%.1f%% be=%.1f%% time=%.1f%%",
                     row["trades"], row["win_rate"], row["profit_factor"],
                     row["net_bps"], row["tp1_pct"], row["tp2_pct"],
                     row["sl_pct"], row["be_pct"], row["time_pct"])
    return 0


COLS = ["label", "taker_fee_bps", "maker_fee_bps", "maker_entry_fee_bps",
        "slippage_bps", "n_entries", "entry_wr", "entry_net_bps",
        "entry_gross_bps", "entry_cost_bps", "avg_sl_bps", "scale_rate",
        "trades", "win_rate", "profit_factor", "net_bps",
        "tp1_pct", "tp2_pct", "sl_pct", "be_pct", "time_pct",
        "tp2_per_tp1", "total_pnl", "n_signals", "profit_hits", "runtime_s"]


def report() -> int:
    rows = []
    for f in sorted(OUT_DIR.glob("rows_*.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line))
    if not rows:
        print("no sweep rows yet")
        return 0
    df = pd.DataFrame(rows)
    df = df.sort_values(["profit_factor", "net_bps"], ascending=False)
    df.to_csv(OUT_DIR / "sweep_summary.csv", index=False)
    df.to_parquet(OUT_DIR / "sweep_summary.parquet", index=False)
    print(df[COLS].to_string(index=False))
    lines = ["| " + " | ".join(COLS) + " |",
             "|" + "---|" * len(COLS)]
    for _, r in df[COLS].iterrows():
        cells = ["" if (v is None or (isinstance(v, float) and v != v))
                 else str(v) for v in r]
        lines.append("| " + " | ".join(cells) + " |")
    (OUT_DIR / "sweep_summary.md").write_text("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
