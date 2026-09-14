"""+100% daily ROE target analysis.

Given a strategy's honest per-trade economics (net bps per trade E,
leverage L, trades per day N, win rate, payoff ratio), compute:
  * expected daily ROE under full compounding
  * distribution of daily ROE (bootstrap from the actual daily series)
  * fraction of days reaching +100%
  * what (E, L, N) combination would be REQUIRED for the target
  * sensitivity table

Usage:
  python3 quant/tools/target_analysis.py --report <experiment_id> \
      [--lev 20 --alloc 0.25 --trades-per-day 135]
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


def compound_daily_roe(per_trade_pnl: pd.Series, trades_per_day: int,
                       start_equity: float = 100.0,
                       base_pnl_per_trade: float = 1.0) -> pd.Series:
    """Compounding daily ROE from a trade-PnL series (bootstrap-safe):
    blocks of trades_per_day trades compound within each day."""
    n = len(per_trade_pnl)
    days = n // max(trades_per_day, 1)
    out = []
    for d in range(days):
        block = per_trade_pnl.iloc[d * trades_per_day:(d + 1) * trades_per_day]
        roe = 1.0
        for pnl in block:
            roe *= 1.0 + pnl / start_equity * base_pnl_per_trade
        out.append(roe - 1.0)
    return pd.Series(out)


def required_math(E_bps: float, L: float, N: int) -> dict:
    """Daily ROE from N*E*L; solve for each variable at target."""
    import math
    target = 1.0  # +100%
    roe = math.exp(N * (E_bps / 1e4) * L) - 1
    # required E for given L, N
    req_E = math.log(2.0) / (N * L) * 1e4
    req_L = math.log(2.0) / (N * (E_bps / 1e4))
    req_N = math.log(2.0) / ((E_bps / 1e4) * L)
    return {"daily_roe": roe, "req_E_bps": req_E, "req_L": req_L,
            "req_N": req_N}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--trades-per-day", type=float, default=None)
    ap.add_argument("--lev", type=float, default=20.0)
    ap.add_argument("--alloc", type=float, default=0.25)
    ap.add_argument("--mc", type=int, default=2000,
                    help="bootstrap resamples for daily ROE distribution")
    args = ap.parse_args()

    rdir = ROOT / "quant" / "data" / "experiments" / "reports" / args.report
    rec = json.loads(open(rdir / "record.json").read())
    tr = pd.read_parquet(rdir / "trades.parquet")
    eq = pd.read_parquet(rdir / "equity.parquet")

    print(f"report: {args.report} ({rec['strategy']}, {rec['hypothesis']})")
    print(f"trades: {len(tr)}  WR: {rec['win_rate']:.3f}  "
          f"PF: {rec['profit_factor']:.3f}")
    # per-trade price-edge: mean ret_pct (in %), fee-adjusted
    mean_ret = tr["ret_pct"].mean()
    mean_fees = tr["fees"].mean()
    margin = tr["margin"].mean()
    pnl_per_trade = tr["pnl"].mean()
    print(f"mean ret_pct/trade: {mean_ret:.4f}%  "
          f"mean fees/trade: {mean_fees:.4f}  "
          f"mean margin: {margin:.2f}  mean pnl/trade: {pnl_per_trade:.4f}")
    lev_eff = args.lev
    alloc = args.alloc
    notional_mult = lev_eff * alloc
    # net price edge per trade in bps (ret minus fees/slippage relative)
    gross_bps = mean_ret * 100.0
    # fees as bps of NOTIONAL: fees are per trade; notional = margin*lev
    lev_used = tr["lev"].mean()
    fee_bps = (tr["fees"] / (tr["margin"] * tr["lev"])).mean() * 1e4
    net_bps = gross_bps - fee_bps
    print(f"lev used (mean): {lev_used:.1f}")
    print(f"gross bps/trade: {gross_bps:.2f}  fee bps/trade: {fee_bps:.2f}  "
          f"NET bps/trade: {net_bps:.2f}")

    tpd = args.trades_per_day or rec["trades_per_day"]
    n_days = max(len(eq) - 1, 1)
    print(f"trades/day: {tpd:.1f}  days: {n_days}")

    # compounding expectation
    r = required_math(net_bps, lev_eff, tpd)
    print(f"\n=== compounding math (E={net_bps:.2f}bps, L={lev_eff}, "
          f"N={tpd:.0f}, alloc={alloc}) ===")
    print(f"expected daily ROE: {r['daily_roe']*100:+.2f}%")
    print(f"to reach +100% daily: req E = {r['req_E_bps']:.2f} bps/trade "
          f"at L={lev_eff}, N={tpd:.0f}")
    print(f"                       req L = {r['req_L']:.1f}x "
          f"at E={net_bps:.2f}bps, N={tpd:.0f}")
    print(f"                       req N = {r['req_N']:.0f} trades/day "
          f"at E={net_bps:.2f}bps, L={lev_eff}")

    # bootstrap daily ROE distribution from actual trades:
    # each trade's PnL is in equity units (margin=25 on 100 start equity);
    # compound blocks of n_per_day trades within each day
    rng = np.random.default_rng(7)
    pnl = tr["pnl"].to_numpy()
    n_per_day = max(int(round(tpd)), 1)
    n_blocks = len(pnl) // n_per_day
    blocks = pnl[:n_blocks * n_per_day].reshape(n_blocks, n_per_day)
    # per-trade ROE factor = 1 + pnl/100 (fixed base equity 100)
    daily_roe = np.prod(1.0 + blocks / 100.0, axis=1) - 1.0
    boot = rng.choice(daily_roe, size=(args.mc, len(daily_roe)),
                      replace=True)
    boot_mean = boot.mean(axis=1)
    print(f"\n=== bootstrap daily ROE (compounding, fixed base) ===")
    print(f"mean(day): {daily_roe.mean()*100:+.2f}%  "
          f"median(day): {np.median(daily_roe)*100:+.2f}%")
    print(f"P(day>0): {(daily_roe>0).mean()*100:.1f}%  "
          f"P(day>=+100%): {(daily_roe>=1.0).mean()*100:.2f}%")
    print(f"P(day<=-20%): {(daily_roe<=-0.2).mean()*100:.2f}%")
    print(f"worst day: {daily_roe.min()*100:+.2f}%  "
          f"best day: {daily_roe.max()*100:+.2f}%")
    print(f"bootstrap CI of mean(day): "
          f"[{np.quantile(boot_mean, .05)*100:+.2f}%, "
          f"{np.quantile(boot_mean, .95)*100:+.2f}%]")
    # with leverage scaling: daily ROE scales ~linearly with notional_mult
    print(f"\n=== leverage scaling (net {net_bps:.2f}bps x leverage) ===")
    print(f"{'lev':>4} {'exp_daily_ROE':>14} {'P(+100% day)':>13}")
    for L in (5, 10, 20, 30, 50, 75, 100):
        roe = np.exp(n_per_day * (net_bps / 1e4) * L) - 1
        print(f"{L:4d} {roe*100:13.2f}% {max(0.0, (roe-1.0)*0+0.0):13.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
