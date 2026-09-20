"""Final operating-point analysis for the honest Q-FADE baseline.

For the ALL-trades (or a chosen subset) of an S6 run, computes for the
full (lev x alloc x trades/day-cap) grid:
  median/mean daily ROE, P(+100%), P(<=-20%), P(<=-50%), P(ruin),
  expected geometric growth, max drawdown estimate,
plus a Monte Carlo (10k paths x 30 days) for the headline configs.

Also computes the three execution-cost tiers (optimistic/base/pessimistic)
per trade and the resulting net edges.

Usage:
  python3 quant/tools/op_point.py --report <run_id> --out <csv>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.tools.event_analysis import (add_net, daily_roe_series,  # noqa
                                        monte_carlo)

LEVS = (1, 2, 3, 5, 7.5, 10, 15, 20, 25, 30, 40, 50)
ALLOCS = (0.1, 0.25, 0.5, 1.0)
CAPS = (None, 25, 10, 5, 3, 1)


def cost_tiers(tr: pd.DataFrame) -> dict:
    """net bps per trade under three execution assumptions."""
    n = len(tr)
    p_tp = (tr["exit_reason"] == "tp").mean()
    p_sl = (tr["exit_reason"] != "tp").mean()
    gross = tr["gross_bps"].mean()
    # per-trade cost in bps of notional:
    # entry maker 2 / maker 1.5 / maker 2 ; exit: tp maker, else taker+slip
    opt = 1.5 + p_tp * 1.5 + p_sl * (1.5 + 1.0)       # rebates, tight spreads
    base = 2.0 + p_tp * 2.0 + p_sl * (6.0 + 2.0)      # Bitunix retail
    pess = 2.0 + p_tp * 2.0 + p_sl * (6.0 + 12.0)     # illiquid alts half-spread
    return {"gross_bps": gross, "opt_net": gross - opt,
            "base_net": gross - base, "pess_net": gross - pess,
            "opt_cost": opt, "base_cost": base, "pess_cost": pess,
            "p_tp": p_tp, "p_sl": p_sl}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--mc-paths", type=int, default=10_000)
    ap.add_argument("--path-len", type=int, default=30)
    args = ap.parse_args()

    rdir = ROOT / "quant" / "data" / "experiments" / "reports" / args.report
    rec = json.loads(open(rdir / "record.json").read())
    tr = add_net(pd.read_parquet(rdir / "trades.parquet"))
    print(f"run {args.report}: {rec['hypothesis']}  trades={len(tr)}")
    ct = cost_tiers(tr)
    print(f"cost tiers: gross {ct['gross_bps']:+.2f} | "
          f"opt {ct['opt_net']:+.2f} | base {ct['base_net']:+.2f} | "
          f"pess {ct['pess_net']:+.2f} bps/trade")
    print(f"  (costs: opt {ct['opt_cost']:.1f}, base {ct['base_cost']:.1f}, "
          f"pess {ct['pess_cost']:.1f} bps; p_tp={ct['p_tp']:.3f})")

    # adjust net_bps for the cost tiers -> tier-specific daily series
    tr_opt = tr.copy()
    tr_opt["net_bps"] = tr_opt["gross_bps"] - ct["opt_cost"]
    tr_pess = tr.copy()
    tr_pess["net_bps"] = tr_pess["gross_bps"] - ct["pess_cost"]

    tr_s = tr.sort_values(["signal_t", "entry_t"]).reset_index(drop=True)
    rows = []
    for lev in LEVS:
        for alloc in ALLOCS:
            for cap in CAPS:
                daily, used = daily_roe_series(tr_s, lev, alloc, cap,
                                               presorted=True)
                d = daily.to_numpy()
                geo = np.prod(1.0 + d) - 1.0
                rows.append({
                    "lev": lev, "alloc": alloc, "cap": str(cap),
                    "n_day": used / max(len(d), 1),
                    "med_roe": np.median(d) * 100,
                    "mean_roe": d.mean() * 100,
                    "p100": (d >= 1).mean() * 100,
                    "p_m20": (d <= -0.2).mean() * 100,
                    "p_m50": (d <= -0.5).mean() * 100,
                    "p_ruin": (d <= -1).mean() * 100,
                    "geo": geo * 100,
                })
    grid = pd.DataFrame(rows)
    if args.out:
        grid.to_csv(args.out, index=False)
    print(f"\n=== grid: top 25 operating points by median daily ROE ===")
    top = grid.nlargest(25, "med_roe")
    print(top[["lev", "alloc", "cap", "n_day", "med_roe", "mean_roe",
               "p100", "p_m20", "p_ruin", "geo"]].round(2)
          .to_string(index=False))

    # headline configs: MC for a few interesting points
    print(f"\n=== Monte Carlo ({args.mc_paths} paths x {args.path_len}d) "
          f"on headline configs (base costs) ===")
    for lev, alloc, cap, label in (
            (20, 0.25, None, "ALL 911/d L20 a.25"),
            (20, 0.25, 5, "top5/d L20 a.25"),
            (20, 0.25, 1, "top1/d L20 a.25"),
            (10, 0.25, 1, "top1/d L10 a.25"),
            (20, 0.10, 1, "top1/d L20 a.10"),
            (50, 0.10, 1, "top1/d L50 a.10")):
        daily, used = daily_roe_series(tr, lev, alloc, cap)
        d = daily.to_numpy()
        mc = monte_carlo(d, n_paths=args.mc_paths, path_len=args.path_len)
        print(f"[{label:18s}] n/day={used/max(len(d),1):6.1f} "
              f"med={np.median(d)*100:+7.2f}% mean={d.mean()*100:+7.2f}% "
              f"P100d={(d>=1).mean()*100:5.2f}% "
              f"MC_P100_30d={mc['P_hit_100_at_least_once']*100:5.1f}% "
              f"MC_Pruin={mc['P_ruin']*100:5.2f}% "
              f"MC_Pdd50={mc['P_dd_gt_50']*100:5.1f}% "
              f"MC_med_geo={mc['median_geo_growth']*100:+8.1f}%")

    # optimistic/pessimistic sensitivity on the best configs
    print("\n=== execution sensitivity (top1/d L20 a.25, cost tiers) ===")
    for name, trv in (("opt", tr_opt), ("base", tr), ("pess", tr_pess)):
        daily, used = daily_roe_series(trv, 20, 0.25, 1)
        d = daily.to_numpy()
        print(f"{name:5s} med={np.median(d)*100:+7.2f}% "
              f"mean={d.mean()*100:+7.2f}% P100={(d>=1).mean()*100:5.2f}% "
              f"Pruin={(d<=-1).mean()*100:5.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
