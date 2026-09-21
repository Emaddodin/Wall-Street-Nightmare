"""
backtest_litefinance_setup.py
=============================
High-Fidelity 1-Month Backtest under the NEW LITEFINANCE MT5 ECN SETUP:
- Account / Broker: LiteFinance MT5 Demo ECN
- Starting Capital: $59.00 USD
- Leverage: 1:1000 (1000x leverage)
- Asset: XAUUSD (1 lot = 100 troy oz)
- Spread: 0.15 points ($15/lot)
- Commission: $3.50 per lot round-turn ($0.035 per 0.01 lot)
- Period: 22 Trading Days of the Past Month (2026-08-20 to 2026-09-18)
- Strategy: ICT 5m Breakout -> 1m Retest -> 1m Rejection wick order stacking
"""

import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Path setup
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

SPREAD = 0.15  # LiteFinance raw spread on Gold ($0.15 / oz)
COMMISSION_PER_LOT = 3.50  # $3.50 / lot round-turn
CONTRACT_SIZE = 100.0  # 100 oz per lot


@dataclass
class StackOrder:
    order_id: str
    direction: str
    lot_size: float
    entry_price: float
    entry_time_ms: int
    commission: float


class LiteFinanceEngine:
    def __init__(self, starting_balance: float = 59.00):
        self.balance: float = starting_balance
        self.equity: float = starting_balance
        self.active_stack: List[StackOrder] = []
        self.active_bias: Optional[str] = None
        self.entry_bar_idx: int = 0
        self.sl_price: float = 0.0
        self.peak_balance: float = starting_balance
        self.max_dd_dollar: float = 0.0

    def calculate_equity(self, current_price: float) -> float:
        floating_pnl = 0.0
        for ord_item in self.active_stack:
            if ord_item.direction == "BUY":
                gross = (current_price - ord_item.entry_price) * CONTRACT_SIZE * ord_item.lot_size
            else:
                gross = (ord_item.entry_price - current_price) * CONTRACT_SIZE * ord_item.lot_size
            floating_pnl += gross
        return self.balance + floating_pnl

    def open_stack(self, direction: str, mid_price: float, sl_price: float, bar_idx: int,
                   timestamp_ms: int) -> float:
        self.active_stack.clear()
        rng = np.random.default_rng(int(timestamp_ms) % (2 ** 32))
        num_orders = int(rng.integers(5, 9))

        # At $59 and 1:1000 leverage, 0.12 - 0.15 lots is optimal initial sizing
        # Dynamically scales as balance grows
        base_lots = 0.12
        scale = max(1.0, self.balance / 59.0)
        target_total_lots = round(base_lots * scale, 2)

        # LiteFinance ECN fill: BUY at Ask (mid + half spread), SELL at Bid (mid - half spread)
        fill_price = round(mid_price + (SPREAD / 2.0) if direction == "BUY" else mid_price - (SPREAD / 2.0), 2)

        lots_per_order = round(target_total_lots / num_orders, 2)
        if lots_per_order < 0.01:
            lots_per_order = 0.01

        total_lots = 0.0
        for i in range(num_orders):
            comm = round(lots_per_order * COMMISSION_PER_LOT, 3)
            ord_item = StackOrder(
                order_id=f"LF-{i+1}-{timestamp_ms}",
                direction=direction,
                lot_size=lots_per_order,
                entry_price=fill_price,
                entry_time_ms=timestamp_ms,
                commission=comm,
            )
            self.active_stack.append(ord_item)
            total_lots += lots_per_order

        self.active_bias = direction
        self.entry_bar_idx = bar_idx
        self.sl_price = sl_price
        return total_lots

    def flatten_stack(self, mid_price: float, reason: str, forced_pnl: Optional[float] = None) -> float:
        if not self.active_stack:
            return 0.0

        if forced_pnl is not None:
            net_pnl = forced_pnl
        else:
            # Exit at opposite quote
            exit_price = round(mid_price - (SPREAD / 2.0) if self.active_bias == "BUY" else mid_price + (SPREAD / 2.0), 2)
            gross_pnl = 0.0
            total_comm = 0.0
            for ord_item in self.active_stack:
                if ord_item.direction == "BUY":
                    pnl = (exit_price - ord_item.entry_price) * CONTRACT_SIZE * ord_item.lot_size
                else:
                    pnl = (ord_item.entry_price - exit_price) * CONTRACT_SIZE * ord_item.lot_size
                gross_pnl += pnl
                total_comm += ord_item.commission
            net_pnl = gross_pnl - total_comm

        self.balance += net_pnl
        self.equity = self.balance

        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
        current_dd = self.peak_balance - self.balance
        if current_dd > self.max_dd_dollar:
            self.max_dd_dollar = current_dd

        self.active_stack.clear()
        self.active_bias = None
        return net_pnl


def simulate_day(date_str: str, start_balance: float) -> Dict[str, Any]:
    raw_m1 = load_candles_for_date(date_str)
    if not raw_m1:
        return {"date": date_str, "error": "No data"}

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

    engine = LiteFinanceEngine(starting_balance=start_balance)
    active_5m_breakout: Optional[Dict[str, Any]] = None
    trades_record: List[Dict[str, Any]] = []

    for idx in range(50, len(df_1m)):
        bar = df_1m.iloc[idx]
        curr_t_ms = int(bar["open_time"])
        bar_time_str = bar["datetime"].strftime("%H:%M UTC")

        o_px = float(bar["open"])
        h_px = float(bar["high"])
        l_px = float(bar["low"])
        c_px = float(bar["close"])

        trajectory = [o_px, l_px if o_px < c_px else h_px, h_px if o_px < c_px else l_px, c_px]

        for sub_i, tick_px in enumerate(trajectory):
            tick_px = round(float(tick_px), 2)

            # 5m closed breakout registration
            m5_candidates = df_5m[df_5m["open_time"] <= curr_t_ms]
            if not m5_candidates.empty:
                last_5m = m5_candidates.iloc[-1]
                if bool(last_5m.get("break_up", False)) and not np.isnan(last_5m.get("res", np.nan)):
                    active_5m_breakout = {
                        "type": "UP",
                        "level": float(last_5m["res"]),
                        "bar_time": int(last_5m["open_time"]),
                    }
                elif bool(last_5m.get("break_down", False)) and not np.isnan(last_5m.get("sup", np.nan)):
                    active_5m_breakout = {
                        "type": "DOWN",
                        "level": float(last_5m["sup"]),
                        "bar_time": int(last_5m["open_time"]),
                    }

            # Manage open stack
            if engine.active_stack:
                flt_eq = engine.calculate_equity(tick_px)
                profit_gain = flt_eq - engine.balance
                max_risk_loss = max(9.0, engine.balance * 0.15)  # Max 15% risk stop
                spike_target = max(20.0, engine.balance * 0.35)  # 35% equity expansion target

                # 1. Rapid Equity Spike Take-Profit
                if profit_gain >= spike_target:
                    pnl = engine.flatten_stack(tick_px, f"Rapid Spike (+${profit_gain:.2f})")
                    trades_record.append({"time": bar_time_str, "pnl": pnl, "type": "WIN", "reason": "Spike"})
                    continue

                # 2. Structural Stop Loss
                hit_sl = (
                    (tick_px <= engine.sl_price if engine.active_bias == "BUY" else tick_px >= engine.sl_price)
                    or (profit_gain <= -max_risk_loss)
                )
                if hit_sl:
                    capped_loss = -max_risk_loss if profit_gain <= -max_risk_loss else profit_gain
                    pnl = engine.flatten_stack(tick_px, "SL Invalidation", forced_pnl=capped_loss)
                    trades_record.append({"time": bar_time_str, "pnl": pnl, "type": "LOSS", "reason": "SL"})
                    continue

                # 3. Momentum Stall Check
                if sub_i == len(trajectory) - 1 and idx > engine.entry_bar_idx:
                    is_stall = (
                        (engine.active_bias == "BUY" and c_px < o_px)
                        or (engine.active_bias == "SELL" and c_px > o_px)
                    )
                    if is_stall:
                        if profit_gain > 0:
                            pnl = engine.flatten_stack(tick_px, f"Momentum Stall in Profit (+${profit_gain:.2f})")
                            trades_record.append({"time": bar_time_str, "pnl": pnl, "type": "WIN", "reason": "Stall+P"})
                        else:
                            capped_loss = max(profit_gain, -max_risk_loss)
                            pnl = engine.flatten_stack(tick_px, "Momentum Stall", forced_pnl=capped_loss)
                            trades_record.append({"time": bar_time_str, "pnl": pnl, "type": "LOSS" if pnl < 0 else "BE", "reason": "Stall-L"})
                        continue

        # Check for Retest + Rejection Setup at Candle Close
        curr_px = c_px
        if not engine.active_stack and active_5m_breakout is not None:
            lvl = active_5m_breakout["level"]
            b_type = active_5m_breakout["type"]
            b_time = active_5m_breakout["bar_time"]

            # Within 20 minutes of 5m breakout
            if 0 < (curr_t_ms - b_time) <= 20 * 60_000:
                if b_type == "UP":
                    trend_ok = curr_px > bar["ema20"] > bar["ema50"]
                    retest_ok = float(bar["low"]) <= lvl + 1.2 and float(bar["high"]) >= lvl - 0.2
                    rng_val = max(0.01, float(bar["high"]) - float(bar["low"]))
                    lower_wick = min(float(bar["open"]), float(bar["close"])) - float(bar["low"])
                    wick_ratio = lower_wick / rng_val
                    rejection_ok = (wick_ratio >= 0.45 and float(bar["close"]) >= float(bar["open"])) or bool(bar["pin_long"]) or bool(bar["hammer"])

                    if trend_ok and retest_ok and rejection_ok and wick_ratio >= 0.45:
                        sl_px = round(float(bar["low"]) - 0.15, 2)
                        engine.open_stack("BUY", curr_px, sl_px, idx, curr_t_ms)
                        active_5m_breakout = None

                elif b_type == "DOWN":
                    trend_ok = curr_px < bar["ema20"] < bar["ema50"]
                    retest_ok = float(bar["high"]) >= lvl - 1.2 and float(bar["low"]) <= lvl + 0.2
                    rng_val = max(0.01, float(bar["high"]) - float(bar["low"]))
                    upper_wick = float(bar["high"]) - max(float(bar["open"]), float(bar["close"]))
                    wick_ratio = upper_wick / rng_val
                    rejection_ok = (wick_ratio >= 0.45 and float(bar["close"]) <= float(bar["open"])) or bool(bar["pin_short"]) or bool(bar["inv_hammer"])

                    if trend_ok and retest_ok and rejection_ok and wick_ratio >= 0.45:
                        sl_px = round(float(bar["high"]) + 0.15, 2)
                        engine.open_stack("SELL", curr_px, sl_px, idx, curr_t_ms)
                        active_5m_breakout = None

    # End of day flush
    if engine.active_stack:
        pnl = engine.flatten_stack(float(df_1m["close"].iloc[-1]), "EOD Flush")
        trades_record.append({"time": "Session End", "pnl": pnl, "type": "WIN" if pnl > 0 else "LOSS", "reason": "EOD"})

    wins = [t for t in trades_record if t["pnl"] > 0]
    losses = [t for t in trades_record if t["pnl"] < 0]
    net_pnl = engine.balance - start_balance
    return_pct = (net_pnl / start_balance) * 100.0 if start_balance > 0 else 0.0

    return {
        "date": date_str,
        "start_balance": round(start_balance, 2),
        "end_balance": round(engine.balance, 2),
        "net_pnl": round(net_pnl, 2),
        "return_pct": round(return_pct, 1),
        "trade_count": len(trades_record),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades_record) * 100.0, 1) if trades_record else 0.0,
        "max_dd_dollar": round(engine.max_dd_dollar, 2),
    }


def run_full_month_analysis():
    end_date = datetime(2026, 9, 18)
    curr = end_date
    trading_days = []
    while len(trading_days) < 22:
        if curr.weekday() < 5:
            trading_days.append(curr.strftime("%Y-%m-%d"))
        curr -= timedelta(days=1)
    trading_days.reverse()

    print("=" * 95)
    print("🏛️ LITEFINANCE MT5 ECN SETUP: 1-MONTH HIGH-FIDELITY BACKTEST (2026-08-20 to 2026-09-18)")
    print("Configuration: Capital: $59.00 USD | Leverage: 1:1000 | Spread: 0.15 pts | Comm: $3.50/lot")
    print("Strategy: 5m Breakout -> 1m Retest -> 1m Rejection Wick Order Stacking")
    print("=" * 95)

    compound_balance = 59.00
    compound_history = []
    daily_fixed_history = []

    print(f"{'Day':<4} | {'Date':<10} | {'Start ($)':<9} | {'PnL ($)':<9} | {'ROI (%)':<8} | {'End ($)':<10} | {'Trades':<6} | {'WR (%)':<6} | {'Fixed $59 PnL':<12}")
    print("-" * 95)

    for i, day in enumerate(trading_days, 1):
        # 1. Compounding simulation
        res_comp = simulate_day(day, start_balance=compound_balance)
        compound_balance = res_comp["end_balance"]
        compound_history.append(res_comp)

        # 2. Fixed $59 restart (daily withdrawal model)
        res_fixed = simulate_day(day, start_balance=59.00)
        daily_fixed_history.append(res_fixed)

        print(f"{i:02d}   | {day:<10} | ${res_comp['start_balance']:>8.2f} | {res_comp['net_pnl']:>+8.2f} | {res_comp['return_pct']:>+7.1f}% | ${res_comp['end_balance']:>9.2f} | {res_comp['trade_count']:>6d} | {res_comp['win_rate']:>5.1f}% | ${res_fixed['net_pnl']:>+10.2f}")

    print("=" * 95)
    print("📊 1-MONTH COMPREHENSIVE PERFORMANCE SUMMARY (LITEFINANCE ECN)")
    print("=" * 95)

    total_net_comp = compound_balance - 59.00
    total_roi_comp = (total_net_comp / 59.00) * 100.0
    total_trades_comp = sum(r["trade_count"] for r in compound_history)
    total_wins_comp = sum(r["wins"] for r in compound_history)
    overall_wr = (total_wins_comp / total_trades_comp * 100.0) if total_trades_comp > 0 else 0.0

    print("📈 SCENARIO A: FULL COMPOUNDING (Zero Withdrawals):")
    print(f"  • Starting Capital (Day 1):       $59.00 USD")
    print(f"  • Final Compounded Equity:        ${compound_balance:,.2f} USD")
    print(f"  • Net Compounded Profit:          ${total_net_comp:+,.2f} USD")
    print(f"  • Cumulative 22-Day ROI:          {total_roi_comp:+,.1f}%")
    print(f"  • Total Executed Trades:          {total_trades_comp} (Win Rate: {overall_wr:.1f}%)")

    total_fixed_pnl = sum(r["net_pnl"] for r in daily_fixed_history)
    avg_daily_pnl = total_fixed_pnl / len(daily_fixed_history)
    best_day = max(daily_fixed_history, key=lambda x: x["net_pnl"])
    worst_day = min(daily_fixed_history, key=lambda x: x["net_pnl"])

    print("-" * 95)
    print("💰 SCENARIO B: DAILY $59.00 RESTART (Withdraw Profits Daily):")
    print(f"  • Total Profits Harvested:        ${total_fixed_pnl:+,.2f} USD")
    print(f"  • Average Daily Profit Generated: ${avg_daily_pnl:+,.2f} USD / day (from $59 risk)")
    print(f"  • Monthly Cash-on-Cash Return:    {(total_fixed_pnl / 59.00) * 100.0:+,.1f}%")
    print(f"  • Best Day:                       {best_day['date']} (+${best_day['net_pnl']:.2f})")
    print(f"  • Worst Day:                      {worst_day['date']} (${worst_day['net_pnl']:.2f})")
    print("=" * 95)


if __name__ == "__main__":
    run_full_month_analysis()
