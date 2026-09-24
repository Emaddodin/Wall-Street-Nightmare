"""
tjr/tjr_runner.py
=================
Master Execution Orchestrator for the TJR Trading Architecture.
- Executes massive multi-regime backtests across historical and simulated datasets
- Evaluates Raw TJR vs Laya-Gated TJR vs "To The Moon" Compounding ($59 Start)
- Exports streaming trade journals and trains the Laya AI decision model
- Generates institutional analytics report
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tjr.tjr_backtest_million import TJRMillionBacktester
from tjr.tjr_engine import TJREngineConfig
from tjr.tjr_to_the_moon_compounding import TJRToTheMoonCompounding


def run_pipeline(target_iterations: int = 1_000_000, starting_balance: float = 59.0) -> None:
    print("=" * 80)
    print("🌕 TJR TRADING ARCHITECTURE: MASSIVE MULTI-REGIME SIMULATION & LAYA AI ENGINE")
    print(f"   Target Setup Evaluations: {target_iterations:,}")
    print(f"   Starting Capital: ${starting_balance:.2f} USD ('To The Moon' Compounding Plan)")
    print(f"   Broker Friction: $0.35 Spread + $0.15 Slippage per oz on XAUUSD")
    print("=" * 80)

    backtester = TJRMillionBacktester()

    # 1. Run Baseline Raw TJR (Without Laya Gating)
    print("\n[PHASE 1] Running Raw TJR Baseline Evaluation (No AI Decision Gating)...")
    t0 = time.perf_counter()
    raw_outcome, _, _ = backtester.run_full_simulation(
        target_iterations=target_iterations,
        enable_laya_gating=False,
        simulate_to_the_moon_compounding=False,
        starting_balance=starting_balance,
    )
    t_raw = time.perf_counter() - t0
    print(f"   -> Found {raw_outcome.total_setups_found:,} setups | Executed: {raw_outcome.trades_executed:,}")
    print(f"   -> Raw Win Rate: {raw_outcome.win_rate_pct:.1f}% | Profit Factor: {raw_outcome.profit_factor:.2f}")

    # 2. Run Laya-Gated TJR Simulation with "To The Moon" Compounding from $59
    print(f"\n[PHASE 2] Running Laya AI-Gated TJR + 'To The Moon' Compounding (${starting_balance:.2f} Start)...")
    t0 = time.perf_counter()
    laya_outcome, journal, compounding = backtester.run_full_simulation(
        target_iterations=target_iterations,
        enable_laya_gating=True,
        simulate_to_the_moon_compounding=True,
        starting_balance=starting_balance,
    )
    t_laya = time.perf_counter() - t0

    # 3. Export Trade Journal & Model Weights
    csv_path = journal.save_csv("tjr_trade_journal.csv")
    journal_summary = journal.compute_summary_analytics()
    summary_path = journal.export_dir / "tjr_journal_summary.json"
    with open(summary_path, "w") as f:
        json.dump(journal_summary, f, indent=2)

    # 4. Print Executive Performance Comparison
    print("\n" + "=" * 80)
    print("📊 TJR TRADING SYSTEM: PERFORMANCE MATRIX & COMPILATION SUMMARY")
    print("=" * 80)
    print(f"{'Metric':<32} | {'Raw TJR (Baseline)':<22} | {'Laya-Gated (To The Moon)':<22}")
    print("-" * 80)
    print(f"{'Total Setups Evaluated':<32} | {raw_outcome.total_setups_found:<22,} | {laya_outcome.total_setups_found:<22,}")
    print(f"{'Executed Trades':<32} | {raw_outcome.trades_executed:<22,} | {laya_outcome.trades_executed:<22,}")
    print(f"{'Filtered Out (Low Edge Chop)':<32} | {'0 (0.0%)':<22} | {laya_outcome.total_setups_found - laya_outcome.trades_executed:<22,}")
    print(f"{'Empirical Win Rate':<32} | {f'{raw_outcome.win_rate_pct:.2f}%':<22} | {f'{laya_outcome.win_rate_pct:.2f}%':<22}")
    print(f"{'Profit Factor':<32} | {f'{raw_outcome.profit_factor:.2f}':<22} | {f'{laya_outcome.profit_factor:.2f}':<22}")
    print(f"{'Sharpe Ratio':<32} | {f'{raw_outcome.sharpe_ratio:.2f}':<22} | {f'{laya_outcome.sharpe_ratio:.2f}':<22}")
    print(f"{'Average Win ($)':<32} | {f'${raw_outcome.avg_win_usd:,.2f}':<22} | {f'${laya_outcome.avg_win_usd:,.2f}':<22}")
    print(f"{'Average Loss ($)':<32} | {f'${raw_outcome.avg_loss_usd:,.2f}':<22} | {f'${laya_outcome.avg_loss_usd:,.2f}':<22}")
    print(f"{'Max Drawdown ($)':<32} | {f'${raw_outcome.max_drawdown_usd:,.2f}':<22} | {f'${laya_outcome.max_drawdown_usd:,.2f}':<22}")
    print(f"{'Max Drawdown (%)':<32} | {f'{raw_outcome.max_drawdown_pct:.1f}%':<22} | {f'{laya_outcome.max_drawdown_pct:.1f}%':<22}")
    print("-" * 80)
    print(f"{'Starting Account Capital':<32} | {'$59.00 USD':<22} | {'$59.00 USD':<22}")
    print(f"{'Ending Compounded Balance':<32} | {f'${raw_outcome.final_equity:,.2f} USD':<22} | {f'${laya_outcome.final_equity:,.2f} USD':<22}")
    print(f"{'Sovereign Vault Bankroll':<32} | {'$0.00 USD':<22} | {f'${laya_outcome.vault_reserve:,.2f} USD':<22}")
    print(f"{'Peak Net Worth Achieved':<32} | {f'${raw_outcome.peak_equity:,.2f} USD':<22} | {f'${laya_outcome.peak_equity:,.2f} USD':<22}")
    print(f"{'Total Capital Growth':<32} | {f'{(raw_outcome.final_equity / 59.0):,.1f}x':<22} | {f'{( (laya_outcome.final_equity + laya_outcome.vault_reserve) / 59.0):,.1f}x':<22}")
    print("=" * 80)

    print("\n🎯 SESSION WIN RATE BREAKDOWN (Laya-Gated):")
    for s_name, s_data in journal_summary.get("session_breakdown", {}).items():
        print(f"   • {s_name:<20}: {s_data.get('count', 0):>6} trades | Win Rate: {s_data.get('win_rate_pct', 0.0):.1f}%")

    print("\n🎯 DISPLACEMENT RATIO EDGE (Body / ATR):")
    for d_bin, d_data in journal_summary.get("displacement_breakdown", {}).items():
        print(f"   • {d_bin:<20}: {d_data.get('count', 0):>6} trades | Win Rate: {d_data.get('win_rate_pct', 0.0):.1f}%")

    print(f"\n✅ Trade Journal saved to: {csv_path}")
    print(f"✅ Summary JSON saved to: {summary_path}")
    print(f"✅ Laya Model Weights saved to: {backtester.laya.weights_path}")
    print("=" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TJR Multi-Million Backtester & Laya Model Runner")
    parser.add_argument("--iterations", type=int, default=1_000_000, help="Target setup iterations")
    parser.add_argument("--start-balance", type=float, default=59.0, help="Starting account balance USD")
    args = parser.parse_args()

    run_pipeline(target_iterations=args.iterations, starting_balance=args.start_balance)
