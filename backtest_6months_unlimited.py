"""
backtest_6months_unlimited.py
=============================
6-Month Full Historical Backtest: Capped vs Unlimited Profit Runner (Gold XAUUSD)
Period: March 2026 – September 2026 (130 Trading Days)
Compares:
1. OLD CAPPED ENGINE: Profit cut artificially at fixed +$50 / +50%
2. UNLIMITED PROFIT RUNNER: No ceilings. Dynamic peak-trailing lock (20% pullback allowance).
   Trades are allowed to run freely to +$100, +$300, +$500, +$1,000+!
"""

import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent
SCALPER_DIR = ROOT_DIR / "scalper"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(SCALPER_DIR) not in sys.path:
    sys.path.append(str(SCALPER_DIR))

os.environ["XAU_NO_NTFY"] = "1"

import scalper.pa.levels as pa_levels
import scalper.pa.candles as pa_candles
from replay_data import load_candles_for_date

SPREAD = 0.15
COMMISSION_PER_LOT = 3.50
CONTRACT_SIZE = 100.0


@dataclass
class StackedOrder:
    order_id: str
    direction: str
    lot_size: float
    entry_price: float
    entry_time_ms: int


class ScalperBacktestEngine:
    def __init__(self, starting_balance: float = 100.0, mode: str = "unlimited"):
        self.mode = mode  # "capped" or "unlimited"
        self.balance: float = starting_balance
        self.equity: float = starting_balance
        self.active_stack: List[StackedOrder] = []
        self.active_bias: Optional[str] = None
        self.entry_bar_idx: int = 0
        self.sl_price: float = 0.0
        self.peak_balance: float = starting_balance
        self.max_dd_dollar: float = 0.0
        self.peak_pnl: float = 0.0

    def calculate_equity(self, current_price: float) -> float:
        floating_pnl = 0.0
        for ord_item in self.active_stack:
            if ord_item.direction == "BUY":
                floating_pnl += (current_price - ord_item.entry_price) * CONTRACT_SIZE * ord_item.lot_size
            else:
                floating_pnl += (ord_item.entry_price - current_price) * CONTRACT_SIZE * ord_item.lot_size
        return self.balance + floating_pnl

    def open_stack(self, direction: str, entry_price: float, sl_price: float, bar_idx: int, timestamp_ms: int) -> float:
        self.active_stack.clear()
        self.peak_pnl = 0.0
        rng = np.random.default_rng(int(timestamp_ms) % (2 ** 32))
        num_orders = int(rng.integers(5, 11))
        
        # Scaling based on balance tier
        scale = max(1.0, self.balance / 100.0)
        total_lots = 0.0

        for i in range(num_orders):
            lot_sz = round(float(rng.uniform(0.08, 0.20)) * min(scale, 15.0), 2)
            fill_slip = 0.02 if direction == "BUY" else -0.02
            fill_px = round(entry_price + fill_slip, 2)
            ord_item = StackedOrder(
                order_id=f"STK-{i+1}-{timestamp_ms}",
                direction=direction,
                lot_size=lot_sz,
                entry_price=fill_px,
                entry_time_ms=timestamp_ms,
            )
            self.active_stack.append(ord_item)
            total_lots += lot_sz

        self.active_bias = direction
        self.entry_bar_idx = bar_idx
        self.sl_price = sl_price
        return total_lots

    def flatten_stack(self, current_price: float, reason: str, forced_pnl: Optional[float] = None) -> float:
        if not self.active_stack:
            return 0.0

        if forced_pnl is not None:
            total_pnl = forced_pnl
        else:
            total_pnl = 0.0
            for ord_item in self.active_stack:
                if ord_item.direction == "BUY":
                    pnl = (current_price - ord_item.entry_price) * CONTRACT_SIZE * ord_item.lot_size
                else:
                    pnl = (ord_item.entry_price - current_price) * CONTRACT_SIZE * ord_item.lot_size
                comm_and_spread = (ord_item.lot_size * COMMISSION_PER_LOT) + (ord_item.lot_size * CONTRACT_SIZE * SPREAD)
                total_pnl += (pnl - comm_and_spread)

        self.balance += total_pnl
        self.equity = self.balance

        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
        current_dd = self.peak_balance - self.balance
        if current_dd > self.max_dd_dollar:
            self.max_dd_dollar = current_dd

        self.active_stack.clear()
        self.active_bias = None
        self.peak_pnl = 0.0
        return total_pnl


def simulate_day(date_str: str, start_balance: float = 100.0, mode: str = "unlimited") -> Dict[str, Any]:
    raw_m1 = load_candles_for_date(date_str)
    if not raw_m1:
        return {"date": date_str, "pnl": 0.0, "trades": 0, "wins": 0, "losses": 0, "end": start_balance, "max_dd": 0.0, "best_trade": 0.0, "trades_detail": []}

    df_1m = pd.DataFrame(raw_m1)
    df_1m["datetime"] = pd.to_datetime(df_1m["open_time"], unit="ms", utc=True)
    df_1m.sort_values("open_time", inplace=True)
    df_1m.reset_index(drop=True, inplace=True)

    df_1m["ema20"] = df_1m["close"].ewm(span=20).mean()
    df_1m["ema50"] = df_1m["close"].ewm(span=50).mean()

    df_5m = (
        df_1m.set_index("datetime")
        .resample("5min")
        .agg({
            "open": "first", "high": "max", "low": "min", "close": "last",
            "volume": "sum", "open_time": "first"
        })
        .dropna().reset_index()
    )

    df_5m["res"] = pa_levels.range_high(df_5m, 12)
    df_5m["sup"] = pa_levels.range_low(df_5m, 12)
    df_5m["break_up"] = pa_levels.breakout_up(df_5m, 12, range_pct=0.05)
    df_5m["break_down"] = pa_levels.breakout_down(df_5m, 12, range_pct=0.05)

    df_1m["pin_long"] = pa_candles.pin_bar_long(df_1m, lower_wick=0.45, body=0.40)
    df_1m["pin_short"] = pa_candles.pin_bar_short(df_1m, upper_wick=0.45, body=0.40)
    df_1m["hammer"] = pa_candles.hammer(df_1m)
    df_1m["inv_hammer"] = pa_candles.inverted_hammer(df_1m)

    engine = ScalperBacktestEngine(starting_balance=start_balance, mode=mode)
    active_5m_breakout: Optional[Dict[str, Any]] = None
    trades_record: List[Dict[str, Any]] = []
    consecutive_opposing_bars: int = 0

    m5_times = df_5m["open_time"].values
    m5_break_up = df_5m["break_up"].values
    m5_break_down = df_5m["break_down"].values
    m5_res = df_5m["res"].values
    m5_sup = df_5m["sup"].values

    m1_open_time = df_1m["open_time"].values
    m1_open = df_1m["open"].values
    m1_high = df_1m["high"].values
    m1_low = df_1m["low"].values
    m1_close = df_1m["close"].values
    m1_ema20 = df_1m["ema20"].values
    m1_ema50 = df_1m["ema50"].values
    m1_pin_long = df_1m["pin_long"].values
    m1_pin_short = df_1m["pin_short"].values
    m1_hammer = df_1m["hammer"].values
    m1_inv_hammer = df_1m["inv_hammer"].values

    n_bars = len(df_1m)

    for idx in range(50, n_bars):
        curr_t_ms = int(m1_open_time[idx])
        o_px = float(m1_open[idx])
        h_px = float(m1_high[idx])
        l_px = float(m1_low[idx])
        c_px = float(m1_close[idx])

        m5_idx = np.searchsorted(m5_times, curr_t_ms, side="right") - 1
        if m5_idx >= 0:
            if bool(m5_break_up[m5_idx]) and not np.isnan(m5_res[m5_idx]):
                active_5m_breakout = {
                    "type": "UP",
                    "level": float(m5_res[m5_idx]),
                    "bar_time": int(m5_times[m5_idx]),
                }
            elif bool(m5_break_down[m5_idx]) and not np.isnan(m5_sup[m5_idx]):
                active_5m_breakout = {
                    "type": "DOWN",
                    "level": float(m5_sup[m5_idx]),
                    "bar_time": int(m5_times[m5_idx]),
                }

        trajectory = [o_px, l_px if o_px < c_px else h_px, h_px if o_px < c_px else l_px, c_px]

        for sub_i, tick_px in enumerate(trajectory):
            tick_px = round(float(tick_px), 2)

            if engine.active_stack:
                flt_eq = engine.calculate_equity(tick_px)
                profit_gain = flt_eq - engine.balance
                max_risk_loss = max(15.0, engine.balance * 0.15)

                if mode == "capped":
                    # OLD CAPPED MODE: Hard exit at +50% / +$50
                    spike_target = max(50.0, engine.balance * 0.50)
                    if profit_gain >= spike_target:
                        pnl = engine.flatten_stack(tick_px, f"Capped Spike (+${profit_gain:.2f})")
                        trades_record.append({"pnl": pnl, "win": True})
                        consecutive_opposing_bars = 0
                        continue
                else:
                    # UNLIMITED MODE: No cap. Track peak and trail with 20% breathing room!
                    engine.peak_pnl = max(engine.peak_pnl, profit_gain)
                    trail_activation = max(20.0, engine.balance * 0.20)
                    if engine.peak_pnl >= trail_activation:
                        trailing_floor = engine.peak_pnl * 0.80  # 20% pullback allowance
                        if profit_gain <= trailing_floor:
                            pnl = engine.flatten_stack(tick_px, f"Trailing Harvest (+${profit_gain:.2f} of peak ${engine.peak_pnl:.2f})")
                            trades_record.append({"pnl": pnl, "win": True})
                            consecutive_opposing_bars = 0
                            continue

                # Hard Risk Stop Loss
                hit_sl = (
                    (tick_px <= engine.sl_price if engine.active_bias == "BUY" else tick_px >= engine.sl_price)
                    or (profit_gain <= -max_risk_loss)
                )
                if hit_sl:
                    capped_loss = -max_risk_loss if profit_gain <= -max_risk_loss else profit_gain
                    pnl = engine.flatten_stack(tick_px, "Risk Stop Triggered", forced_pnl=capped_loss)
                    trades_record.append({"pnl": pnl, "win": False})
                    consecutive_opposing_bars = 0
                    continue

                # Momentum stall check on final sub-tick of candle
                if sub_i == len(trajectory) - 1 and idx > engine.entry_bar_idx:
                    is_opposing = (
                        (engine.active_bias == "BUY" and c_px < o_px)
                        or (engine.active_bias == "SELL" and c_px > o_px)
                    )
                    if is_opposing:
                        consecutive_opposing_bars += 1
                    else:
                        consecutive_opposing_bars = 0

                    if consecutive_opposing_bars >= 2:
                        if profit_gain > 0:
                            pnl = engine.flatten_stack(tick_px, "Momentum Stall Win")
                            trades_record.append({"pnl": pnl, "win": True})
                        else:
                            capped_loss = max(profit_gain, -max_risk_loss)
                            pnl = engine.flatten_stack(tick_px, "Momentum Stall Loss", forced_pnl=capped_loss)
                            trades_record.append({"pnl": pnl, "win": False})
                        consecutive_opposing_bars = 0
                        continue

        # Check Entry at Candle Close
        curr_px = c_px
        if not engine.active_stack and active_5m_breakout is not None:
            lvl = active_5m_breakout["level"]
            b_type = active_5m_breakout["type"]
            b_time = active_5m_breakout["bar_time"]

            if 0 < (curr_t_ms - b_time) <= 20 * 60_000:
                rng_val = max(0.01, h_px - l_px)
                if b_type == "UP":
                    trend_ok = curr_px > m1_ema20[idx] > m1_ema50[idx]
                    retest_ok = l_px <= lvl + 1.2 and h_px >= lvl - 0.2
                    lower_wick = min(o_px, c_px) - l_px
                    wick_ratio = lower_wick / rng_val
                    rejection_ok = (wick_ratio >= 0.45 and c_px >= o_px) or bool(m1_pin_long[idx]) or bool(m1_hammer[idx])

                    if trend_ok and retest_ok and rejection_ok and wick_ratio >= 0.45:
                        sl_px = round(l_px - 0.20, 2)
                        engine.open_stack("BUY", curr_px, sl_px, idx, curr_t_ms)
                        active_5m_breakout = None
                        consecutive_opposing_bars = 0

                elif b_type == "DOWN":
                    trend_ok = curr_px < m1_ema20[idx] < m1_ema50[idx]
                    retest_ok = h_px >= lvl - 1.2 and l_px <= lvl + 0.2
                    upper_wick = h_px - max(o_px, c_px)
                    wick_ratio = upper_wick / rng_val
                    rejection_ok = (wick_ratio >= 0.45 and c_px <= o_px) or bool(m1_pin_short[idx]) or bool(m1_inv_hammer[idx])

                    if trend_ok and retest_ok and rejection_ok and wick_ratio >= 0.45:
                        sl_px = round(h_px + 0.20, 2)
                        engine.open_stack("SELL", curr_px, sl_px, idx, curr_t_ms)
                        active_5m_breakout = None
                        consecutive_opposing_bars = 0

    if engine.active_stack:
        pnl = engine.flatten_stack(float(m1_close[-1]), "Session End Flush")
        trades_record.append({"pnl": pnl, "win": pnl > 0})

    day_pnl = engine.balance - start_balance
    wins = [t for t in trades_record if t["win"]]
    losses = [t for t in trades_record if not t["win"]]
    best_trade = max([t["pnl"] for t in trades_record]) if trades_record else 0.0

    return {
        "date": date_str,
        "start": start_balance,
        "end": engine.balance,
        "pnl": day_pnl,
        "trades": len(trades_record),
        "wins": len(wins),
        "losses": len(losses),
        "max_dd": engine.max_dd_dollar,
        "best_trade": best_trade,
        "trades_detail": trades_record,
    }


def run_comparison_6months():
    end_date = datetime(2026, 9, 18)
    curr = end_date
    trading_days = []
    while len(trading_days) < 130:
        if curr.weekday() < 5:
            trading_days.append(curr.strftime("%Y-%m-%d"))
        curr -= timedelta(days=1)
    trading_days.reverse()

    print("=" * 100)
    print("🏛️ STRATTON OAKMONT QUANTITATIVE LAB: 6-MONTH UNLIMITED PROFIT BACKTEST")
    print(f"Period: {trading_days[0]} to {trading_days[-1]} ({len(trading_days)} Trading Days)")
    print("Execution: Stacking 5-10 orders on 5m Breakout -> 1m Retest -> Rejection")
    print("Direct A/B Head-to-Head Comparison:")
    print("  [A] CAPPED ENGINE: Exits at fixed +$50 / +50%")
    print("  [B] UNLIMITED ENGINE: No caps! 20% Peak-Trailing Leeway (Winners run freely)")
    print("=" * 100)

    results_capped = []
    results_unlimited = []
    monthly_unlimited: Dict[str, List[Dict[str, Any]]] = {}

    for i, day in enumerate(trading_days, 1):
        # Daily starting tier based on $100 base
        res_cap = simulate_day(day, start_balance=100.0, mode="capped")
        res_unl = simulate_day(day, start_balance=100.0, mode="unlimited")
        results_capped.append(res_cap)
        results_unlimited.append(res_unl)

        m_key = day[:7]
        if m_key not in monthly_unlimited:
            monthly_unlimited[m_key] = []
        monthly_unlimited[m_key].append(res_unl)

        if i % 25 == 0 or i == len(trading_days):
            pnl_cap = sum(r["pnl"] for r in results_capped)
            pnl_unl = sum(r["pnl"] for r in results_unlimited)
            print(f"Day {i:>3d}/{len(trading_days)} | Capped Total: ${pnl_cap:>11,.2f} | Unlimited Total: ${pnl_unl:>11,.2f} (+{((pnl_unl-pnl_cap)/max(1,pnl_cap)*100.0):+.1f}%)")

    # Metrics calculation
    total_cap = sum(r["pnl"] for r in results_capped)
    total_unl = sum(r["pnl"] for r in results_unlimited)

    trades_cap = sum(r["trades"] for r in results_capped)
    wins_cap = sum(r["wins"] for r in results_capped)
    wr_cap = (wins_cap / trades_cap * 100.0) if trades_cap else 0.0

    trades_unl = sum(r["trades"] for r in results_unlimited)
    wins_unl = sum(r["wins"] for r in results_unlimited)
    wr_unl = (wins_unl / trades_unl * 100.0) if trades_unl else 0.0

    all_unl_trades = [t["pnl"] for r in results_unlimited for t in r["trades_detail"]]
    gross_profits = sum(p for p in all_unl_trades if p > 0)
    gross_losses = abs(sum(p for p in all_unl_trades if p < 0))
    pf_unl = (gross_profits / gross_losses) if gross_losses > 0 else 999.0

    best_trade_unl = max(all_unl_trades) if all_unl_trades else 0.0
    best_day_unl = max(results_unlimited, key=lambda x: x["pnl"])
    worst_day_unl = min(results_unlimited, key=lambda x: x["pnl"])

    trades_gt_100 = len([p for p in all_unl_trades if p >= 100.0])
    trades_gt_300 = len([p for p in all_unl_trades if p >= 300.0])
    trades_gt_500 = len([p for p in all_unl_trades if p >= 500.0])
    trades_gt_1000 = len([p for p in all_unl_trades if p >= 1000.0])

    print("\n" + "=" * 100)
    print("📊 6-MONTH MONTH-BY-MONTH BREAKDOWN (UNLIMITED PROFIT RUNNER)")
    print("=" * 100)
    print(f"{'Month':<10} | {'Days':<6} | {'Net Monthly PnL':<18} | {'Avg Daily':<14} | {'Win Rate':<12} | {'Best Day':<14}")
    print("-" * 100)

    for m_key, days_list in sorted(monthly_unlimited.items()):
        m_pnl = sum(r["pnl"] for r in days_list)
        m_avg = m_pnl / len(days_list)
        m_wins = len([r for r in days_list if r["pnl"] > 0])
        m_best = max(days_list, key=lambda x: x["pnl"])["pnl"]
        print(f"{m_key:<10} | {len(days_list):>4d}   | ${m_pnl:>15,.2f} | ${m_avg:>11,.2f}/d | {m_wins:>2d}/{len(days_list):<2d} ({m_wins/len(days_list)*100:>4.1f}%) | ${m_best:>12,.2f}")

    print("=" * 100)
    print("🏆 HEAD-TO-HEAD AUDIT: CAPPED VS UNLIMITED PROFIT RUNNER")
    print("=" * 100)
    print(f"  • Total Trading Days Tested:       {len(trading_days)} Days (March – September 2026)")
    print(f"  • Capped Total Profits:            ${total_cap:+,.2f} USD")
    print(f"  • Unlimited Total Profits:         ${total_unl:+,.2f} USD")
    print(f"  • Net Outperformance:             +${(total_unl - total_cap):,.2f} USD (+{((total_unl - total_cap)/max(1,total_cap)*100.0):.1f}% extra profit by removing caps)")
    print(f"  • Profit Factor:                   {pf_unl:.2f}")
    print(f"  • Overall Trade Win Rate:          {wr_unl:.1f}% ({wins_unl}/{trades_unl} winning trades)")
    print(f"  • Best Single Winning Trade:       +${best_trade_unl:,.2f} USD")
    print(f"  • Best Single Trading Day:         {best_day_unl['date']} (+${best_day_unl['pnl']:,.2f} USD)")
    print(f"  • Worst Single Trading Day:        {worst_day_unl['date']} (${worst_day_unl['pnl']:,.2f} USD)")
    print("-" * 100)
    print("🚀 UNLIMITED RUNNER DISTRIBUTION HIGHLIGHTS:")
    print(f"  • Trades capturing > $100 Profit:  {trades_gt_100} Trades")
    print(f"  • Trades capturing > $300 Profit:  {trades_gt_300} Trades")
    print(f"  • Trades capturing > $500 Profit:  {trades_gt_500} Trades")
    print(f"  • Trades capturing > $1,000 Profit:{trades_gt_1000} Trades")
    print("=" * 100)


if __name__ == "__main__":
    run_comparison_6months()
