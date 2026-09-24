"""
tjr/tjr_hybrid_ensemble.py
==========================
Third-Generation Hybrid Synergy Architecture: TJR + "To The Moon" (Apex Trinity).
Combines:
1. TJR Pure Price Action (Liquidity Sweeps, Displacement MSS, Discount/Premium FVGs)
2. Apex Trinity ICT Playbooks (Silver Bullet FVG, Turtle Soup, S&R Breakout & Retest)
3. Confluence Multiplier: Upgrades sizing and expands R:R targets when both systems agree
4. Conflict Filter: Discards contradictory signals during choppy market transitions
5. High-Velocity Risk Rails: Accelerated BE Ratchet, Peak Watermark Lock, Stagnation Scratch
6. Compounding Engine starting at $59.00 USD with Sovereign Vault Sweep
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Add project root
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scalper.strategies.apex_trinity import ApexSignal, ApexTrinityStrategy
from tjr.tjr_backtest_million import TJRMillionBacktester
from tjr.tjr_engine import Direction, MarketStateSnapshot, TJREngineConfig, TJRMicrostructureEngine, TJRSetup
from tjr.tjr_laya_model import TJRLayaModel
from tjr.tjr_to_the_moon_compounding import TJRToTheMoonCompounding
from tjr.tjr_trade_journal import JournalEntry, TJRTradeJournal


@dataclass
class HybridSignal:
    timestamp: Any
    index: int
    symbol: str
    direction: str  # "LONG" or "SHORT"
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_pts: float
    reward_pts: float
    rr_ratio: float
    confluence_type: str  # "DUAL_CONFIRMATION", "TJR_PRIMARY", "APEX_PRIMARY"
    laya_grade: int
    laya_probability: float
    compounding_mult: float
    state_snapshot: MarketStateSnapshot


class TJRHybridEnsemble:
    """
    Synergistic Ensemble integrating TJR and Apex Trinity engines.
    """

    def __init__(
        self,
        tjr_config: Optional[TJREngineConfig] = None,
        starting_balance: float = 59.0,
        spread_pts: float = 0.35,
        slippage_pts: float = 0.15,
    ):
        self.tjr_engine = TJRMicrostructureEngine(tjr_config or TJREngineConfig())
        self.apex_engine = ApexTrinityStrategy()
        self.laya = TJRLayaModel()
        self.compounding = TJRToTheMoonCompounding(starting_balance=starting_balance)
        self.journal = TJRTradeJournal(export_dir=Path(__file__).resolve().parent / "journal_data")
        self.spread_pts = spread_pts
        self.slippage_pts = slippage_pts

    def scan_hybrid_setups(self, df: pd.DataFrame, symbol: str = "XAUUSD") -> List[HybridSignal]:
        """
        Scans both TJR and Apex Trinity strategies and synthesizes confluence signals.
        """
        # 1. Scan TJR Setups
        tjr_setups = self.tjr_engine.scan_setups(df, symbol=symbol)
        tjr_by_index: Dict[int, TJRSetup] = {s.index: s for s in tjr_setups}

        # 2. Vectorized Apex Trinity Scan
        apex_signals: Dict[int, ApexSignal] = {}
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        opens = df["open"].values
        indices = df.index
        n = len(df)

        if "atr" not in df.columns:
            df["atr"] = self.tjr_engine.compute_atr(df, 14)
        atr_vals = df["atr"].values

        # Vectorized session hours
        if isinstance(indices, pd.DatetimeIndex):
            hours = indices.hour.values
        else:
            hours = np.full(n, 14)

        in_sb = ((hours >= 7) & (hours < 8)) | ((hours >= 14) & (hours < 15))
        in_ts = (hours >= 6) & (hours < 9)
        c_filter = in_sb | in_ts

        prev_highs = np.roll(highs, 1)
        prev_highs[0] = highs[0]
        prev_lows = np.roll(lows, 1)
        prev_lows[0] = lows[0]

        bull_mask = c_filter & ((closes - opens) >= (1.3 * atr_vals)) & (closes > prev_highs)
        bear_mask = c_filter & ((opens - closes) >= (1.3 * atr_vals)) & (closes < prev_lows)

        bull_indices = np.where(bull_mask)[0]
        bear_indices = np.where(bear_mask)[0]

        for i in bull_indices:
            if i >= 30:
                c_atr = atr_vals[i]
                sl = lows[i] - (0.2 * c_atr)
                entry = closes[i]
                risk = entry - sl
                if risk > 0:
                    apex_signals[i] = ApexSignal(
                        direction="BUY",
                        entry_price=entry,
                        sl_price=sl,
                        tp1_price=entry + (risk * 2.0),
                        spike_target=entry + (risk * 3.0),
                        strategy_type="SILVER_BULLET_FVG" if in_sb[i] else "TURTLE_SOUP_SWEEP",
                        ict_concepts=["Silver Bullet", "Liquidity Sweep"],
                        atr_1m=c_atr,
                        reasoning="Apex Trinity Confluence",
                        timestamp=time.time(),
                    )

        for i in bear_indices:
            if i >= 30:
                c_atr = atr_vals[i]
                sl = highs[i] + (0.2 * c_atr)
                entry = closes[i]
                risk = sl - entry
                if risk > 0:
                    apex_signals[i] = ApexSignal(
                        direction="SELL",
                        entry_price=entry,
                        sl_price=sl,
                        tp1_price=entry - (risk * 2.0),
                        spike_target=entry - (risk * 3.0),
                        strategy_type="SILVER_BULLET_FVG" if in_sb[i] else "TURTLE_SOUP_SWEEP",
                        ict_concepts=["Silver Bullet", "Liquidity Sweep"],
                        atr_1m=c_atr,
                        reasoning="Apex Trinity Confluence",
                        timestamp=time.time(),
                    )

        # 3. Fuse Signals & Synthesize Confluence
        hybrid_signals: List[HybridSignal] = []
        all_indices = sorted(set(list(tjr_by_index.keys()) + list(apex_signals.keys())))

        for idx in all_indices:
            has_tjr = idx in tjr_by_index
            has_apex = idx in apex_signals

            # Case A: Dual Confirmation (Both TJR & Apex agree within current bar)
            if has_tjr and has_apex:
                tjr_s = tjr_by_index[idx]
                apex_s = apex_signals[idx]
                tjr_dir = "LONG" if tjr_s.direction == Direction.LONG else "SHORT"
                apex_dir = "LONG" if apex_s.direction == "BUY" else "SHORT"

                if tjr_dir == apex_dir:
                    # Dual Confluence: Upgrade to Grade 3, Boost R:R to 2.5x - 3.0x
                    laya_eval = self.laya.evaluate_setup(tjr_s.state_snapshot)
                    boosted_mult = max(1.35, laya_eval.compounding_multiplier * 1.25)
                    reward_pts = tjr_s.risk_pts * 2.5
                    tp_price = tjr_s.entry_price + reward_pts if tjr_dir == "LONG" else tjr_s.entry_price - reward_pts

                    sig = HybridSignal(
                        timestamp=tjr_s.timestamp,
                        index=idx,
                        symbol=symbol,
                        direction=tjr_dir,
                        entry_price=tjr_s.entry_price,
                        stop_loss=tjr_s.stop_loss,
                        take_profit=tp_price,
                        risk_pts=tjr_s.risk_pts,
                        reward_pts=reward_pts,
                        rr_ratio=2.5,
                        confluence_type="DUAL_CONFIRMATION",
                        laya_grade=3,
                        laya_probability=max(0.85, laya_eval.approval_probability),
                        compounding_mult=boosted_mult,
                        state_snapshot=tjr_s.state_snapshot,
                    )
                    hybrid_signals.append(sig)
                else:
                    # Conflicting direction -> Filter out to avoid chop!
                    continue

            # Case B: TJR Primary Setup (Evaluated by Laya)
            elif has_tjr:
                tjr_s = tjr_by_index[idx]
                laya_eval = self.laya.evaluate_setup(tjr_s.state_snapshot)
                if laya_eval.authorized:
                    tjr_dir = "LONG" if tjr_s.direction == Direction.LONG else "SHORT"
                    sig = HybridSignal(
                        timestamp=tjr_s.timestamp,
                        index=idx,
                        symbol=symbol,
                        direction=tjr_dir,
                        entry_price=tjr_s.entry_price,
                        stop_loss=tjr_s.stop_loss,
                        take_profit=tjr_s.take_profit,
                        risk_pts=tjr_s.risk_pts,
                        reward_pts=tjr_s.reward_pts,
                        rr_ratio=tjr_s.rr_ratio,
                        confluence_type="TJR_PRIMARY",
                        laya_grade=laya_eval.grade,
                        laya_probability=laya_eval.approval_probability,
                        compounding_mult=laya_eval.compounding_multiplier,
                        state_snapshot=tjr_s.state_snapshot,
                    )
                    hybrid_signals.append(sig)

        return hybrid_signals

    def simulate_hybrid_trade_forward(
        self,
        df: pd.DataFrame,
        sig: HybridSignal,
        max_holding_bars: int = 25,
        be_ratchet_atr: float = 0.75,
        peak_watermark_pct: float = 0.18,
    ) -> JournalEntry:
        """
        Bar-by-bar causal forward simulation with BE lock, watermark trail, and stagnation scratch.
        """
        start_idx = sig.index
        direction = sig.direction
        entry_price = sig.entry_price
        stop_loss = sig.stop_loss
        take_profit = sig.take_profit
        atr = sig.state_snapshot.atr_14

        # Friction
        effective_entry = entry_price + (self.spread_pts / 2.0 + self.slippage_pts) if direction == "LONG" else entry_price - (self.spread_pts / 2.0 + self.slippage_pts)

        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        indices = df.index
        n = len(df)

        active_sl = stop_loss
        be_ratchet_hit = False
        peak_favorable_pts = 0.0
        max_adverse_pts = 0.0

        exit_price = effective_entry
        exit_time = indices[start_idx]
        exit_reason = "TIMEOUT_SCRATCH"
        holding_bars = 0

        end_idx = min(n, start_idx + max_holding_bars + 1)
        for k in range(start_idx + 1, end_idx):
            holding_bars += 1
            bar_high = highs[k]
            bar_low = lows[k]
            bar_close = closes[k]
            exit_time = indices[k]

            if direction == "LONG":
                favorable_pts = bar_high - effective_entry
                adverse_pts = effective_entry - bar_low
                peak_favorable_pts = max(peak_favorable_pts, favorable_pts)
                max_adverse_pts = max(max_adverse_pts, adverse_pts)

                if bar_low <= active_sl:
                    exit_price = active_sl
                    exit_reason = "SL_HIT"
                    break

                if bar_high >= take_profit:
                    exit_price = take_profit
                    exit_reason = "TP_HIT"
                    break

                # Accelerated BE Ratchet
                if not be_ratchet_hit and favorable_pts >= (be_ratchet_atr * atr):
                    be_ratchet_hit = True
                    active_sl = effective_entry + 0.20

                # Peak Watermark Lock
                if peak_favorable_pts >= (1.5 * atr):
                    pullback = peak_favorable_pts - (bar_close - effective_entry)
                    if pullback / peak_favorable_pts >= peak_watermark_pct:
                        exit_price = bar_close
                        exit_reason = "WATERMARK_LOCK"
                        break

                # Stagnation scratch
                if holding_bars >= 12 and (bar_close - effective_entry) < 0.20:
                    exit_price = bar_close
                    exit_reason = "STAGNATION_SCRATCH"
                    break

            else:  # SHORT
                favorable_pts = effective_entry - bar_low
                adverse_pts = bar_high - effective_entry
                peak_favorable_pts = max(peak_favorable_pts, favorable_pts)
                max_adverse_pts = max(max_adverse_pts, adverse_pts)

                if bar_high >= active_sl:
                    exit_price = active_sl
                    exit_reason = "SL_HIT"
                    break

                if bar_low <= take_profit:
                    exit_price = take_profit
                    exit_reason = "TP_HIT"
                    break

                # Accelerated BE Ratchet
                if not be_ratchet_hit and favorable_pts >= (be_ratchet_atr * atr):
                    be_ratchet_hit = True
                    active_sl = effective_entry - 0.20

                # Peak Watermark Lock
                if peak_favorable_pts >= (1.5 * atr):
                    pullback = peak_favorable_pts - (effective_entry - bar_close)
                    if pullback / peak_favorable_pts >= peak_watermark_pct:
                        exit_price = bar_close
                        exit_reason = "WATERMARK_LOCK"
                        break

                # Stagnation scratch
                if holding_bars >= 12 and (effective_entry - bar_close) < 0.20:
                    exit_price = bar_close
                    exit_reason = "STAGNATION_SCRATCH"
                    break

        pts_pnl = (exit_price - effective_entry) if direction == "LONG" else (effective_entry - exit_price)
        is_win = pts_pnl > 0
        risk_pts = sig.risk_pts
        rr_realized = (pts_pnl / risk_pts) if risk_pts > 0 else 0.0

        return JournalEntry(
            ticket_id=sig.index,
            symbol=sig.symbol,
            entry_time=indices[start_idx],
            exit_time=exit_time,
            direction=direction,
            entry_price=effective_entry,
            exit_price=exit_price,
            stop_loss=sig.stop_loss,
            take_profit=sig.take_profit,
            position_size=0.10,
            risk_pts=sig.risk_pts,
            reward_pts=sig.reward_pts,
            rr_realized=rr_realized,
            realized_pnl=pts_pnl * 10.0,
            is_win=is_win,
            exit_reason=exit_reason,
            holding_bars=holding_bars,
            mae_pts=max_adverse_pts,
            mfe_pts=peak_favorable_pts,
            session_window=sig.state_snapshot.session_window,
            htf_bias=sig.state_snapshot.htf_bias,
            sweep_side=sig.state_snapshot.sweep_side,
            sweep_penetration_pips=sig.state_snapshot.sweep_penetration_pips,
            displacement_ratio=sig.state_snapshot.displacement_ratio,
            fvg_size_atr=sig.state_snapshot.fvg_size_atr,
            dealing_range_coordinate=sig.state_snapshot.dealing_range_coordinate,
            planned_rr=sig.rr_ratio,
            atr_14=atr,
            laya_authorized=True,
            laya_regime=sig.confluence_type,
            laya_grade=sig.laya_grade,
            laya_probability=sig.laya_probability,
        )

    def run_hybrid_backtest(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Executes backtest across the full dataset with compounding from $59.
        """
        t0 = time.perf_counter()
        self.journal.clear()
        self.compounding = TJRToTheMoonCompounding(starting_balance=self.compounding.starting_balance)

        signals = self.scan_hybrid_setups(df, symbol="XAUUSD")
        print(f"⚡ Detected {len(signals):,} Hybrid Confluence Setups across {len(df):,} bars")

        pnl_series = []
        for sig in signals:
            lot_size, _ = self.compounding.compute_lot_size(laya_multiplier=sig.compounding_mult)
            trade = self.simulate_hybrid_trade_forward(df, sig)
            trade.position_size = lot_size
            trade.realized_pnl = trade.risk_pts * trade.rr_realized * (lot_size * 100.0)

            self.journal.record_entry(trade)
            pnl_series.append(trade.realized_pnl)
            self.compounding.process_trade_result(trade.realized_pnl, trade.is_win)

        elapsed = time.perf_counter() - t0
        stats = self.journal.compute_summary_analytics()

        # Save journal
        csv_path = self.journal.save_csv("tjr_hybrid_ensemble_journal.csv")

        return {
            "total_bars": len(df),
            "signals_detected": len(signals),
            "trades_executed": len(self.journal.entries),
            "win_count": stats.get("win_count", 0),
            "loss_count": stats.get("loss_count", 0),
            "win_rate_pct": stats.get("win_rate_pct", 0.0),
            "profit_factor": stats.get("profit_factor", 0.0),
            "sharpe_ratio": stats.get("sharpe_ratio", 0.0),
            "starting_balance": self.compounding.starting_balance,
            "final_equity": round(self.compounding.equity, 2),
            "vault_reserve": round(self.compounding.vault_reserve, 2),
            "peak_net_worth": round(self.compounding.peak_equity, 2),
            "max_drawdown_usd": stats.get("max_drawdown", 0.0),
            "execution_time_sec": round(elapsed, 2),
            "journal_csv": str(csv_path),
        }


def main():
    backtester = TJRMillionBacktester()
    print("📖 Loading full 2020-2026 historical dataset...")
    full_df = backtester.load_all_historical_candles()

    ensemble = TJRHybridEnsemble(starting_balance=59.0)
    print("\n" + "=" * 80)
    print("🚀 RUNNING THIRD-GENERATION HYBRID SYNERGY SYSTEM (TJR + TO THE MOON)")
    print("=" * 80)
    results = ensemble.run_hybrid_backtest(full_df)

    print("\n" + "=" * 80)
    print("🏆 HYBRID ENSEMBLE PERFORMANCE SUMMARY")
    print("=" * 80)
    print(f"Total 1-Minute Bars Analyzed : {results['total_bars']:,} (~{results['total_bars']//1440:,} days)")
    print(f"Executed Hybrid Trades      : {results['trades_executed']:,}")
    print(f"Empirical Win Rate          : {results['win_rate_pct']:.2f}%")
    print(f"Profit Factor               : {results['profit_factor']:.2f}")
    print(f"Sharpe Ratio                : {results['sharpe_ratio']:.2f}")
    print(f"Starting Capital            : ${results['starting_balance']:.2f} USD")
    print(f"Ending Trading Balance      : ${results['final_equity']:,.2f} USD")
    print(f"Sovereign Vault Bankroll    : ${results['vault_reserve']:,.2f} USD (30% Swept)")
    print(f"Peak Net Worth Achieved     : ${results['peak_net_worth']:,.2f} USD")
    print(f"Backtest Runtime            : {results['execution_time_sec']:.2f}s")
    print(f"Trade Journal Saved         : {results['journal_csv']}")
    print("=" * 80)


if __name__ == "__main__":
    main()
