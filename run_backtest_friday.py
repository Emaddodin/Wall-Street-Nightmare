"""
run_backtest_friday.py
======================
Comprehensive Backtest and Edge Calibration on Friday (2026-09-18) GOLD Data.

Evaluates:
1. Baseline Configuration (65% wick, 1.5x velocity surge, 3.0x L2 imbalance).
2. Tight Sniper (70% wick, 1.7x surge).
3. High-Frequency Volume Scalper (60% wick, 1.3x surge).
4. Sizing and Compounding path towards $10,000.
"""

from __future__ import annotations

import json
from pathlib import Path
import pandas as pd
import numpy as np

from backtester import (
    VectorizedBacktester,
    HyperPredatorParams,
    BacktestResult,
)


def run_edge_optimization():
    candles_path = Path("data/candles/gold_m1_2026-09-18.json")
    if not candles_path.exists():
        print("Candles file not found!")
        return

    with open(candles_path, "r", encoding="utf-8") as f:
        candles = json.load(f)

    df = pd.DataFrame(candles)
    if "timestamp" not in df.columns and "time" in df.columns:
        df["timestamp"] = df["time"]

    if "tick_velocity" not in df.columns:
        vels = []
        for c in candles:
            ticks = c.get("tick_timestamps", [])
            t_open = c["time"] / 1000.0
            final_5s = [t for t in ticks if t >= t_open + 55.0]
            vels.append(2.0 if len(final_5s) >= 5 else 1.0)
        df["tick_velocity"] = np.array(vels, dtype=np.float32)

    if "l2_imbalance" not in df.columns:
        imbs = []
        for c in candles:
            bids = sum(sz for _, sz in c.get("l2_bids", []))
            asks = sum(sz for _, sz in c.get("l2_asks", []))
            imbs.append(asks / bids if bids > 0 else 1.0)
        df["l2_imbalance"] = np.array(imbs, dtype=np.float32)

    if "tape_delta" not in df.columns:
        df["tape_delta"] = np.full(len(df), 0.5, dtype=np.float32)

    if "volatility_regime" not in df.columns:
        df["volatility_regime"] = np.ones(len(df), dtype=np.float32)

    print(f"============================================================")
    print(f"   FRIDAY 2026-09-18 GOLD BACKTEST & EDGE OPTIMIZATION")
    print(f"============================================================")
    print(f"Total Candles: {len(df)} M1 Bars (Full 24h Friday Market)")
    print(f"Starting Base Capital: $65.00 | Leverage: 100x | Max Margin: 20%")

    configs = [
        ("High-Frequency Scalper (60% Wick, 1.3x Surge)", 0.60, 1.3, 3.0, 1.00),
        ("Predator Baseline (65% Wick, 1.5x Surge)", 0.65, 1.5, 3.0, 1.00),
        ("Institutional Sniper (70% Wick, 1.6x Surge)", 0.70, 1.6, 3.2, 1.00),
        ("Ultra-Tight S/R Edge (65% Wick, 1.4x Surge, $0.80 SL)", 0.65, 1.4, 2.8, 0.80),
    ]

    results = []

    for name, wick, vel, l2_imb, sl_off in configs:
        params = HyperPredatorParams(
            wick_pct=wick,
            tick_velocity_mult=vel,
            l2_imbalance_threshold=l2_imb,
            stop_offset=sl_off,
            margin_utilization=0.20,
            leverage=100.0,
            initial_equity=65.0,
            hard_equity_shield=-10.00,
        )

        bt = VectorizedBacktester(initial_equity=65.0, leverage=100.0)
        res = bt.run_backtest(params=params, source=df)
        results.append((name, res))

        print(f"\n--- {name} ---")
        print(f"Trades Executed:   {res.total_trades}")
        print(f"Win Rate:          {res.win_rate * 100:.1f}% ({res.winning_trades}W / {res.losing_trades}L)")
        print(f"Net Realized PnL:  ${res.total_pnl:+.2f}")
        print(f"Ending Equity:     ${res.final_equity:.2f} (from $65.00)")
        ret_pct = ((res.final_equity - res.initial_equity) / res.initial_equity) * 100.0
        print(f"Return on Equity:  {ret_pct:+.1f}%")
        print(f"Profit Factor:     {res.profit_factor:.2f}")
        print(f"Max Drawdown:      {res.max_drawdown_pct:.1f}% (${res.max_drawdown_dollars:.2f})")
        print(f"Sharpe Ratio:      {res.sharpe_ratio:.2f}")
        print(f"Expectancy:        ${res.expectancy:.2f} / trade")
        print(f"Exits Breakdown:   Target S/R: {res.target_sr_exits} | L2 Imbalance: {res.l2_imbalance_exits} | Stop Loss: {res.stop_loss_exits} | Shield: {res.hard_equity_shield_liquidations}")

    print(f"\n============================================================")
    print(f"   $65 -> $10,000 GEOMETRIC SCALING SIMULATION")
    print(f"============================================================")
    best_tuple = max(results, key=lambda x: x[1].total_pnl if x[1].total_trades > 0 else -999)
    best_name, best_res = best_tuple
    print(f"Optimal Configuration: {best_name}")
    print(f"Trades: {best_res.total_trades} | Net PnL: ${best_res.total_pnl:+.2f} | Win Rate: {best_res.win_rate*100:.1f}% | Profit Factor: {best_res.profit_factor:.2f}")
    
    print("\nCompounding milestones path under 20% margin ceiling:")
    milestones = [65.0, 100.0, 250.0, 500.0, 1000.0, 2500.0, 5000.0, 10000.0]
    for i in range(len(milestones) - 1):
        m_start = milestones[i]
        m_end = milestones[i+1]
        mult = m_end / m_start
        print(f"  Stage {i+1}: ${m_start:,.0f} -> ${m_end:,.0f} ({mult:.1f}x growth)")

    return results


if __name__ == "__main__":
    run_edge_optimization()
