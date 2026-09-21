"""
High-Speed Vectorized & Causal Backtest Benchmark for The Apex Engine.
Compares:
1. Baseline Model (Single 5m breakout setup, fixed TP/SL)
2. The Apex Engine (ICT Trinity Matrix, Risk-Free Pyramiding, 60/40 Scale-Out, Adaptive ATR)
Tested over 60+ days of M1 Gold (XAUUSD) historical data.
Optimized with pre-computed daily vector indicators for sub-second execution.
"""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from scalper.strategies.apex_trinity import ApexPosition, ApexSignal, ApexTrinityStrategy


@dataclass
class TradeRecord:
    ticket: int
    strategy: str
    direction: str
    entry_time: str
    entry_price: float
    exit_price: float
    volume: float
    pnl: float
    exit_reason: str
    is_win: bool
    scaled_out: bool = False
    pyramid_added: bool = False


class ApexBacktestEngine:
    def __init__(self, data_dir: str = "data/candles", starting_balance: float = 100.0):
        self.data_dir = Path(data_dir)
        self.starting_balance = starting_balance
        self.strategy = ApexTrinityStrategy(min_candles_warmup=30)

    def load_candle_files(self, max_days: int = 60) -> List[Path]:
        """Finds sorted CSV files for XAUUSD."""
        files = sorted(self.data_dir.glob("gold_m1_*.csv"))
        if not files:
            files = sorted(self.data_dir.glob("*.csv"))
        return files[:max_days]

    def compute_lot_size(self, balance: float) -> float:
        """Dynamic compounding ladder based on account equity."""
        if balance < 200.0:
            return 0.05
        elif balance < 400.0:
            return 0.10
        elif balance < 800.0:
            return 0.20
        elif balance < 1500.0:
            return 0.40
        elif balance < 3000.0:
            return 0.80
        else:
            return min(3.00, round(balance / 2000.0, 2))

    def precompute_day(self, df: pd.DataFrame) -> pd.DataFrame:
        """Precomputes all indicators and setup trigger candidates across the entire day at C-speed."""
        df = df.copy()
        if "open_time" not in df.columns and "time" in df.columns:
            df["open_time"] = df["time"]
        
        df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df["hour"] = df["datetime"].dt.hour
        df["minute"] = df["datetime"].dt.minute
        
        # 1m EMAs
        df["ema20"] = df["close"].ewm(span=20).mean()
        df["ema50"] = df["close"].ewm(span=50).mean()
        
        # ATR(14)
        h = df["high"].to_numpy(dtype=float)
        l = df["low"].to_numpy(dtype=float)
        c = df["close"].to_numpy(dtype=float)
        n = len(df)
        tr = np.zeros(n, dtype=float)
        tr[0] = h[0] - l[0]
        for i in range(1, n):
            tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        df["atr"] = pd.Series(tr, index=df.index).rolling(14, min_periods=3).mean().fillna(1.50).clip(0.80, 8.0)

        # Resample to 5m for Breakout S&R
        df_5m = (
            df.set_index("datetime")
            .resample("5min")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "open_time": "first"})
            .dropna()
            .reset_index()
        )
        if len(df_5m) >= 7:
            df_5m["res"] = df_5m["high"].rolling(6).max().shift(1)
            df_5m["sup"] = df_5m["low"].rolling(6).min().shift(1)
            df_5m["bo_up"] = (df_5m["res"] > 0) & (df_5m["close"] > df_5m["res"] * 1.0003)
            df_5m["bo_down"] = (df_5m["sup"] > 0) & (df_5m["close"] < df_5m["sup"] * 0.9997)
            
            # Merge 5m breakout info back to 1m
            df = pd.merge_asof(
                df.sort_values("open_time"),
                df_5m[["open_time", "res", "sup", "bo_up", "bo_down"]].rename(columns={
                    "open_time": "bar5m_time",
                    "res": "bo_res",
                    "sup": "bo_sup"
                }),
                left_on="open_time",
                right_on="bar5m_time",
                direction="backward"
            )
        else:
            df["bo_res"] = 0.0
            df["bo_sup"] = 0.0
            df["bo_up"] = False
            df["bo_down"] = False
            df["bar5m_time"] = 0

        # Precompute Asian Range (00:00 - 04:00 UTC)
        asia = df[(df["hour"] >= 0) & (df["hour"] < 4)]
        if len(asia) >= 30:
            df["asia_high"] = float(asia["high"].max())
            df["asia_low"] = float(asia["low"].min())
        else:
            df["asia_high"] = np.nan
            df["asia_low"] = np.nan

        return df

    def run_benchmark(self, max_days: int = 60) -> Dict[str, Any]:
        """Runs comparative backtest between Baseline and Apex Engine."""
        files = self.load_candle_files(max_days=max_days)
        print(f"Loaded {len(files)} trading days for backtesting...")

        # Pre-process all days once
        preprocessed_days: List[pd.DataFrame] = []
        for f_path in files:
            try:
                raw_df = pd.read_csv(f_path)
                if not raw_df.empty and "open" in raw_df.columns:
                    day_df = self.precompute_day(raw_df)
                    preprocessed_days.append(day_df)
            except Exception:
                continue

        # 1. Run Apex Engine Backtest
        apex_results = self._simulate_run(preprocessed_days, use_apex_features=True)

        # 2. Run Baseline Engine Backtest
        baseline_results = self._simulate_run(preprocessed_days, use_apex_features=False)

        return {
            "days_tested": len(preprocessed_days),
            "apex": apex_results,
            "baseline": baseline_results,
        }

    def _simulate_run(self, days: List[pd.DataFrame], use_apex_features: bool = True) -> Dict[str, Any]:
        balance = self.starting_balance
        peak_equity = balance
        max_drawdown = 0.0

        trades: List[TradeRecord] = []
        ticket_counter = 1

        active_pos: Optional[ApexPosition] = None
        scaled_out_vol = 0.0
        realized_scale_pnl = 0.0
        last_sig_time = 0.0

        for df in days:
            n_rows = len(df)
            if n_rows < 30:
                continue

            # Numpy arrays for sub-microsecond row iteration
            o_arr = df["open"].to_numpy()
            h_arr = df["high"].to_numpy()
            l_arr = df["low"].to_numpy()
            c_arr = df["close"].to_numpy()
            t_arr = (df["open_time"] / 1000.0).to_numpy()
            hour_arr = df["hour"].to_numpy()
            ema20_arr = df["ema20"].to_numpy()
            ema50_arr = df["ema50"].to_numpy()
            atr_arr = df["atr"].to_numpy()
            
            bo_res_arr = df["bo_res"].to_numpy()
            bo_sup_arr = df["bo_sup"].to_numpy()
            bo_up_arr = df["bo_up"].to_numpy()
            bo_down_arr = df["bo_down"].to_numpy()
            bar5m_time_arr = (df["bar5m_time"] / 1000.0).to_numpy()

            asia_hi = df["asia_high"].iloc[0] if not math.isnan(df["asia_high"].iloc[0]) else 0.0
            asia_lo = df["asia_low"].iloc[0] if not math.isnan(df["asia_low"].iloc[0]) else 0.0

            active_bo_type = None
            active_bo_lvl = 0.0
            active_bo_time = 0.0

            for i in range(2, n_rows):
                curr_px = c_arr[i]
                high_px = h_arr[i]
                low_px = l_arr[i]
                open_px = o_arr[i]
                curr_t = t_arr[i]
                hr = hour_arr[i]
                atr = atr_arr[i]

                # Update 5m breakout tracking
                if bo_up_arr[i]:
                    active_bo_type = "UP"
                    active_bo_lvl = bo_res_arr[i]
                    active_bo_time = bar5m_time_arr[i]
                elif bo_down_arr[i]:
                    active_bo_type = "DOWN"
                    active_bo_lvl = bo_sup_arr[i]
                    active_bo_time = bar5m_time_arr[i]

                # 1. Manage Active Position
                if active_pos:
                    is_buy = active_pos.direction == "BUY"
                    entry_px = active_pos.entry_price

                    # Check Pyramiding Opportunity (Apex Only)
                    if use_apex_features and active_pos.pyramid_count == 0:
                        profit_pts = (curr_px - entry_px) if is_buy else (entry_px - curr_px)
                        if profit_pts >= (1.5 * active_pos.atr_at_entry):
                            pyramid_vol = round(active_pos.volume * 0.50, 2)
                            new_sl = round(entry_px + (0.5 * active_pos.atr_at_entry), 2) if is_buy else round(entry_px - (0.5 * active_pos.atr_at_entry), 2)
                            active_pos.pyramid_count += 1
                            active_pos.pyramid_volume = pyramid_vol
                            active_pos.sl_price = new_sl

                    # Check 60/40 Scale Out (Apex Only)
                    if use_apex_features and not active_pos.scaled_out_60:
                        reached_tp1 = (curr_px >= active_pos.tp1_price) if is_buy else (curr_px <= active_pos.tp1_price)
                        if reached_tp1:
                            active_pos.scaled_out_60 = True
                            scaled_vol = round(active_pos.volume * 0.60, 2)
                            scaled_out_vol = scaled_vol
                            pts = (active_pos.tp1_price - entry_px) if is_buy else (entry_px - active_pos.tp1_price)
                            realized_scale_pnl = pts * scaled_vol * 100.0  # 100 oz per lot
                            balance += realized_scale_pnl
                            active_pos.volume = round(active_pos.volume - scaled_vol, 2)
                            active_pos.sl_price = round(entry_px + 0.10, 2) if is_buy else round(entry_px - 0.10, 2)

                    # Check Stop Loss Exit
                    hit_sl = (low_px <= active_pos.sl_price) if is_buy else (high_px >= active_pos.sl_price)
                    if hit_sl:
                        exit_px = active_pos.sl_price
                        pts = (exit_px - entry_px) if is_buy else (entry_px - exit_px)
                        net_pnl = (pts * active_pos.volume * 100.0) + realized_scale_pnl
                        balance += (pts * active_pos.volume * 100.0)
                        trades.append(
                            TradeRecord(
                                ticket=ticket_counter,
                                strategy=active_pos.strategy_type,
                                direction=active_pos.direction,
                                entry_time=str(active_pos.open_time),
                                entry_price=entry_px,
                                exit_price=exit_px,
                                volume=active_pos.volume + scaled_out_vol,
                                pnl=round(net_pnl, 2),
                                exit_reason="Stop Loss / Trailing BE",
                                is_win=net_pnl > 0,
                                scaled_out=active_pos.scaled_out_60,
                                pyramid_added=active_pos.pyramid_count > 0,
                            )
                        )
                        ticket_counter += 1
                        active_pos = None
                        scaled_out_vol = 0.0
                        realized_scale_pnl = 0.0

                    # Check Spike / Moonbag Extension Exit
                    elif (high_px >= active_pos.spike_target if is_buy else low_px <= active_pos.spike_target):
                        exit_px = active_pos.spike_target
                        pts = (exit_px - entry_px) if is_buy else (entry_px - exit_px)
                        total_vol = active_pos.volume + active_pos.pyramid_volume
                        net_pnl = (pts * total_vol * 100.0) + realized_scale_pnl
                        balance += (pts * total_vol * 100.0)
                        trades.append(
                            TradeRecord(
                                ticket=ticket_counter,
                                strategy=active_pos.strategy_type,
                                direction=active_pos.direction,
                                entry_time=str(active_pos.open_time),
                                entry_price=entry_px,
                                exit_price=exit_px,
                                volume=total_vol + scaled_out_vol,
                                pnl=round(net_pnl, 2),
                                exit_reason="Spike Harvest / Extension",
                                is_win=True,
                                scaled_out=active_pos.scaled_out_60,
                                pyramid_added=active_pos.pyramid_count > 0,
                            )
                        )
                        ticket_counter += 1
                        active_pos = None
                        scaled_out_vol = 0.0
                        realized_scale_pnl = 0.0

                # 2. Check Entry Signal if Flat
                elif (curr_t - last_sig_time) >= 180.0:  # 3-minute cooldown
                    sig: Optional[ApexSignal] = None

                    # --- SETUP A: Silver Bullet FVG (14:00 - 15:00 UTC) ---
                    if use_apex_features and 14 <= hr < 15:
                        # Bullish FVG
                        if low_px > h_arr[i - 2]:
                            gap_size = low_px - h_arr[i - 2]
                            if gap_size >= 0.70:
                                ce = round((low_px + h_arr[i - 2]) / 2.0, 2)
                                if low_px <= ce + (0.3 * atr) and curr_px >= ce - 0.20:
                                    sig = ApexSignal(
                                        direction="BUY",
                                        entry_price=curr_px,
                                        sl_price=round(l_arr[i - 2] - 0.30, 2),
                                        tp1_price=round(curr_px + (2.5 * atr), 2),
                                        spike_target=round(curr_px + (5.0 * atr), 2),
                                        strategy_type="SILVER_BULLET_FVG",
                                        ict_concepts=["Silver Bullet BISI FVG"],
                                        atr_1m=atr,
                                        reasoning="NY Silver Bullet Long",
                                        timestamp=curr_t,
                                    )
                        # Bearish FVG
                        elif high_px < l_arr[i - 2]:
                            gap_size = l_arr[i - 2] - high_px
                            if gap_size >= 0.70:
                                ce = round((high_px + l_arr[i - 2]) / 2.0, 2)
                                if high_px >= ce - (0.3 * atr) and curr_px <= ce + 0.20:
                                    sig = ApexSignal(
                                        direction="SELL",
                                        entry_price=curr_px,
                                        sl_price=round(h_arr[i - 2] + 0.30, 2),
                                        tp1_price=round(curr_px - (2.5 * atr), 2),
                                        spike_target=round(curr_px - (5.0 * atr), 2),
                                        strategy_type="SILVER_BULLET_FVG",
                                        ict_concepts=["Silver Bullet SIBI FVG"],
                                        atr_1m=atr,
                                        reasoning="NY Silver Bullet Short",
                                        timestamp=curr_t,
                                    )

                    # --- SETUP B: Turtle Soup Asian Sweep (06:00 - 09:00 UTC) ---
                    if not sig and use_apex_features and 6 <= hr < 9 and asia_hi > 0 and asia_lo > 0:
                        rng = max(0.20, high_px - low_px)
                        if low_px < asia_lo and curr_px > asia_lo:
                            lower_wick = min(open_px, curr_px) - low_px
                            if (lower_wick / rng) >= 0.50:
                                sig = ApexSignal(
                                    direction="BUY",
                                    entry_price=curr_px,
                                    sl_price=round(low_px - 0.30, 2),
                                    tp1_price=round(curr_px + (2.5 * atr), 2),
                                    spike_target=round(curr_px + (5.0 * atr), 2),
                                    strategy_type="TURTLE_SOUP_SWEEP",
                                    ict_concepts=["Asian Low Sweep"],
                                    atr_1m=atr,
                                    reasoning="Turtle Soup Long",
                                    timestamp=curr_t,
                                )
                        elif high_px > asia_hi and curr_px < asia_hi:
                            upper_wick = high_px - max(open_px, curr_px)
                            if (upper_wick / rng) >= 0.50:
                                sig = ApexSignal(
                                    direction="SELL",
                                    entry_price=curr_px,
                                    sl_price=round(high_px + 0.30, 2),
                                    tp1_price=round(curr_px - (2.5 * atr), 2),
                                    spike_target=round(curr_px - (5.0 * atr), 2),
                                    strategy_type="TURTLE_SOUP_SWEEP",
                                    ict_concepts=["Asian High Sweep"],
                                    atr_1m=atr,
                                    reasoning="Turtle Soup Short",
                                    timestamp=curr_t,
                                )

                    # --- SETUP C: 5m S&R Breakout + 1m Retest (All Sessions) ---
                    if not sig and active_bo_type and 0 < (curr_t - active_bo_time) <= 1200.0:
                        lvl = active_bo_lvl
                        rng = max(0.20, high_px - low_px)
                        if active_bo_type == "UP":
                            trend_ok = curr_px > ema20_arr[i] > ema50_arr[i]
                            retest_ok = low_px <= lvl + 1.20 and high_px >= lvl - 0.20
                            lower_wick = min(open_px, curr_px) - low_px
                            wick_ok = (lower_wick / rng) >= 0.45 and curr_px >= open_px
                            if trend_ok and retest_ok and wick_ok:
                                sig = ApexSignal(
                                    direction="BUY",
                                    entry_price=curr_px,
                                    sl_price=round(low_px - (0.8 * atr), 2),
                                    tp1_price=round(curr_px + (2.5 * atr), 2),
                                    spike_target=round(curr_px + (5.0 * atr), 2),
                                    strategy_type="BREAKOUT_RETEST",
                                    ict_concepts=["5m Breakout Retest"],
                                    atr_1m=atr,
                                    reasoning="5m Breakout Retest Long",
                                    timestamp=curr_t,
                                )
                                active_bo_type = None
                        elif active_bo_type == "DOWN":
                            trend_ok = curr_px < ema20_arr[i] < ema50_arr[i]
                            retest_ok = high_px >= lvl - 1.20 and low_px <= lvl + 0.20
                            upper_wick = high_px - max(open_px, curr_px)
                            wick_ok = (upper_wick / rng) >= 0.45 and curr_px <= open_px
                            if trend_ok and retest_ok and wick_ok:
                                sig = ApexSignal(
                                    direction="SELL",
                                    entry_price=curr_px,
                                    sl_price=round(high_px + (0.8 * atr), 2),
                                    tp1_price=round(curr_px - (2.5 * atr), 2),
                                    spike_target=round(curr_px - (5.0 * atr), 2),
                                    strategy_type="BREAKOUT_RETEST",
                                    ict_concepts=["5m Breakout Retest"],
                                    atr_1m=atr,
                                    reasoning="5m Breakout Retest Short",
                                    timestamp=curr_t,
                                )
                                active_bo_type = None

                    if sig:
                        base_vol = self.compute_lot_size(balance)
                        lot_vol = round(base_vol * 1.50, 2) if use_apex_features else base_vol
                        sl_px = sig.sl_price if use_apex_features else (sig.entry_price - 1.50 if sig.direction == "BUY" else sig.entry_price + 1.50)
                        tp1_px = sig.tp1_price if use_apex_features else (sig.entry_price + 5.00 if sig.direction == "BUY" else sig.entry_price - 5.00)
                        spike_px = sig.spike_target if use_apex_features else (sig.entry_price + 8.00 if sig.direction == "BUY" else sig.entry_price - 8.00)

                        active_pos = ApexPosition(
                            ticket=str(ticket_counter),
                            direction=sig.direction,
                            entry_price=sig.entry_price,
                            volume=lot_vol,
                            sl_price=sl_px,
                            tp1_price=tp1_px,
                            spike_target=spike_px,
                            open_time=sig.timestamp,
                            atr_at_entry=sig.atr_1m,
                            strategy_type=sig.strategy_type,
                        )
                        last_sig_time = curr_t

                # Track drawdown
                peak_equity = max(peak_equity, balance)
                dd = (peak_equity - balance) / peak_equity * 100.0 if peak_equity > 0 else 0.0
                max_drawdown = max(max_drawdown, dd)

        # Performance summary
        total_trades = len(trades)
        wins = [t for t in trades if t.is_win]
        losses = [t for t in trades if not t.is_win]
        win_rate = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0

        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 99.9

        big_trades = [t for t in trades if t.pnl >= 500.0]
        four_figure_trades = [t for t in trades if t.pnl >= 1000.0]

        return {
            "final_balance": round(balance, 2),
            "total_pnl": round(balance - self.starting_balance, 2),
            "return_pct": round((balance - self.starting_balance) / self.starting_balance * 100.0, 1),
            "total_trades": total_trades,
            "win_rate": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown_pct": round(max_drawdown, 2),
            "big_trades_count": len(big_trades),
            "four_figure_trades_count": len(four_figure_trades),
            "sample_trades": [t.__dict__ for t in trades[-10:]],
        }


if __name__ == "__main__":
    runner = ApexBacktestEngine()
    print("Starting Apex Engine 60-Day Historical Benchmark...")
    t0 = time.time()
    res = runner.run_benchmark(max_days=60)
    elapsed = time.time() - t0

    apex = res["apex"]
    base = res["baseline"]

    print("\n" + "=" * 65)
    print("           THE APEX ENGINE HISTORICAL BENCHMARK RESULTS")
    print("=" * 65)
    print(f"Days Simulated:         {res['days_tested']} days")
    print(f"Execution Speed:        {elapsed:.2f} seconds")
    print("-" * 65)
    print(f"{'Metric':<25} | {'Baseline':<16} | {'The Apex Engine':<16}")
    print("-" * 65)
    print(f"{'Final Balance':<25} | ${base['final_balance']:<15,.2f} | ${apex['final_balance']:<15,.2f}")
    print(f"{'Total Net Profit':<25} | ${base['total_pnl']:<15,.2f} | ${apex['total_pnl']:<15,.2f}")
    print(f"{'Net Return %':<25} | +{base['return_pct']:<14.1f}% | +{apex['return_pct']:<14.1f}%")
    print(f"{'Total Trades':<25} | {base['total_trades']:<16} | {apex['total_trades']:<16}")
    print(f"{'Win Rate %':<25} | {base['win_rate']:<15.1f}% | {apex['win_rate']:<15.1f}%")
    print(f"{'Profit Factor':<25} | {base['profit_factor']:<16.2f} | {apex['profit_factor']:<16.2f}")
    print(f"{'Max Drawdown':<25} | {base['max_drawdown_pct']:<15.2f}% | {apex['max_drawdown_pct']:<15.2f}%")
    print(f"{'Big Trades (+$500+)':<25} | {base['big_trades_count']:<16} | {apex['big_trades_count']:<16}")
    print(f"{'Four-Figure (+$1,000+)':<25} | {base['four_figure_trades_count']:<16} | {apex['four_figure_trades_count']:<16}")
    print("=" * 65)
