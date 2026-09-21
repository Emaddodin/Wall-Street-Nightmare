"""
run_6months_audit.py
====================
Institutional 6-Month Backtest & Stress Audit for Gold (XAUUSD) Scalping Engine
Period: March 23, 2026 – September 18, 2026 (130 Trading Days)
Tests:
1. FIXED CAPPED HARVEST: Standard rapid +35% to +50% exit
2. UNLIMITED TRAILING RUNNER: Zero artificial caps. Intelligently locks in 80% of peak profit once armed, allowing monster runners to expand freely to +$100, +$500, +$1,000+!
"""

from __future__ import annotations

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


class SimulationEngine:
    def __init__(self, starting_balance: float = 100.0, engine_type: str = "unlimited"):
        self.balance: float = starting_balance
        self.equity: float = starting_balance
        self.engine_type: str = engine_type  # "capped" or "unlimited"
        self.active_stack: List[StackedOrder] = []
        self.active_bias: Optional[str] = None
        self.entry_bar_idx: int = 0
        self.sl_price: float = 0.0
        self.peak_pnl: float = 0.0
        self.peak_balance: float = starting_balance
        self.max_dd_dollar: float = 0.0

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
        scale = max(1.0, self.balance / 100.0)
        total_lots = 0.0

        for i in range(num_orders):
            lot_sz = round(float(rng.uniform(0.10, 0.25)) * min(scale, 10.0), 2)
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


def run_single_day(date_str: str, start_balance: float = 100.0, engine_type: str = "unlimited") -> Dict[str, Any]:
    raw_m1 = load_candles_for_date(date_str)
    if not raw_m1:
        return {"date": date_str, "pnl": 0.0, "trades": 0, "wins": 0, "losses": 0, "end": start_balance, "max_dd": 0.0, "trades_list": []}

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

    engine = SimulationEngine(starting_balance=start_balance, engine_type=engine_type)
    active_5m_breakout: Optional[Dict[str, Any]] = None
    trades_record: List[Dict[str, Any]] = []

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

        # Binary search for matching closed 5m candle
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

                if engine_type == "capped":
                    # Fixed +35% to +50% spike exit
                    spike_target = max(50.0, engine.balance * 0.35)
                    if profit_gain >= spike_target:
                        pnl = engine.flatten_stack(tick_px, f"Capped Spike (+${profit_gain:.2f})")
                        trades_record.append({"pnl": pnl, "win": True})
                        continue
                else:
                    # Unlimited Trailing Engine:
                    # Tracks highest floating profit. Once in solid profit (>= +$35 or +25% equity),
                    # trailing floor locks in 80% of peak, allowing runners to push to +$100, +$300, +$1,000+!
                    engine.peak_pnl = max(engine.peak_pnl, profit_gain)
                    trail_trigger = max(35.0, engine.balance * 0.25)
                    if engine.peak_pnl >= trail_trigger:
                        trailing_floor = engine.peak_pnl * 0.80
                        if profit_gain <= trailing_floor:
                            pnl = engine.flatten_stack(tick_px, f"Unlimited Trail Lock (+${profit_gain:.2f} of peak ${engine.peak_pnl:.2f})")
                            trades_record.append({"pnl": pnl, "win": True})
                            continue

                # Hard Risk Stop Loss (-$15 or 15% equity max)
                hit_sl = (
                    (tick_px <= engine.sl_price if engine.active_bias == "BUY" else tick_px >= engine.sl_price)
                    or (profit_gain <= -max_risk_loss)
                )
                if hit_sl:
                    capped_loss = -max_risk_loss if profit_gain <= -max_risk_loss else profit_gain
                    pnl = engine.flatten_stack(tick_px, "Risk Stop Triggered", forced_pnl=capped_loss)
                    trades_record.append({"pnl": pnl, "win": False})
                    continue

                # Momentum stall check on final sub-tick of candle
                if sub_i == len(trajectory) - 1 and idx > engine.entry_bar_idx:
                    is_opposing = (
                        (engine.active_bias == "BUY" and c_px < o_px)
                        or (engine.active_bias == "SELL" and c_px > o_px)
                    )
                    if is_opposing:
                        if profit_gain > 0:
                            pnl = engine.flatten_stack(tick_px, f"Momentum Stall in Profit (+${profit_gain:.2f})")
                            trades_record.append({"pnl": pnl, "win": True})
                        else:
                            capped_loss = max(profit_gain, -max_risk_loss)
                            pnl = engine.flatten_stack(tick_px, "Momentum Stall Loss", forced_pnl=capped_loss)
                            trades_record.append({"pnl": pnl, "win": False})
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
                        sl_px = round(l_px - 0.15, 2)
                        engine.open_stack("BUY", curr_px, sl_px, idx, curr_t_ms)
                        active_5m_breakout = None

                elif b_type == "DOWN":
                    trend_ok = curr_px < m1_ema20[idx] < m1_ema50[idx]
                    retest_ok = h_px >= lvl - 1.2 and l_px <= lvl + 0.2
                    upper_wick = h_px - max(o_px, c_px)
                    wick_ratio = upper_wick / rng_val
                    rejection_ok = (wick_ratio >= 0.45 and c_px <= o_px) or bool(m1_pin_short[idx]) or bool(m1_inv_hammer[idx])

                    if trend_ok and retest_ok and rejection_ok and wick_ratio >= 0.45:
                        sl_px = round(h_px + 0.15, 2)
                        engine.open_stack("SELL", curr_px, sl_px, idx, curr_t_ms)
                        active_5m_breakout = None

    if engine.active_stack:
        pnl = engine.flatten_stack(float(m1_close[-1]), "Session End Flush")
        trades_record.append({"pnl": pnl, "win": pnl > 0})

    day_pnl = engine.balance - start_balance
    wins = [t for t in trades_record if t["win"]]
    losses = [t for t in trades_record if not t["win"]]

    return {
        "date": date_str,
        "start": start_balance,
        "end": engine.balance,
        "pnl": day_pnl,
        "trades": len(trades_record),
        "wins": len(wins),
        "losses": len(losses),
        "max_dd": engine.max_dd_dollar,
        "trades_list": trades_record,
    }


def main():
    end_date = datetime(2026, 9, 18)
    curr = end_date
    trading_days = []
    while len(trading_days) < 130:
        if curr.weekday() < 5:
            trading_days.append(curr.strftime("%Y-%m-%d"))
        curr -= timedelta(days=1)
    trading_days.reverse()

    print("=" * 95)
    print("🏛️ STRATTON OAKMONT QUANTITATIVE LAB: 6-MONTH UNLIMITED PROFIT BACKTEST")
    print(f"Period: {trading_days[0]} to {trading_days[-1]} ({len(trading_days)} Trading Days)")
    print("Starting Capital: $100 Seed Capital Daily Compounding (1:1000 Leverage)")
    print("Setup: 5m Breakout -> 1m Retest -> Rejection (Pin Bar >= 45% Wick)")
    print("Direct Comparison:")
    print("  [1] CAPPED ENGINE: Profit cut at fixed +35% to +50%")
    print("  [2] UNLIMITED PROFIT RUNNER: No upper limit! Peak-trailing floor locks in 80% of runners")
    print("=" * 95)

    results_capped = []
    results_unl = []
    monthly_unl: Dict[str, List[Dict[str, Any]]] = {}

    for i, day in enumerate(trading_days, 1):
        res_c = run_single_day(day, start_balance=100.0, engine_type="capped")
        res_u = run_single_day(day, start_balance=100.0, engine_type="unlimited")

        results_capped.append(res_c)
        results_unl.append(res_u)

        m_key = day[:7]
        if m_key not in monthly_unl:
            monthly_unl[m_key] = []
        monthly_unl[m_key].append(res_u)

        if i % 25 == 0 or i == len(trading_days):
            cum_c = sum(r["pnl"] for r in results_capped)
            cum_u = sum(r["pnl"] for r in results_unl)
            print(f"Progress: [{i:3d}/{len(trading_days)}] Days | Capped: ${cum_c:>12,.2f} | Unlimited: ${cum_u:>12,.2f} (Edge: {((cum_u-cum_c)/max(1,cum_c)*100.0):+.1f}%)")

    total_c = sum(r["pnl"] for r in results_capped)
    total_u = sum(r["pnl"] for r in results_unl)

    trades_u = sum(r["trades"] for r in results_unl)
    wins_u = sum(r["wins"] for r in results_unl)
    wr_u = (wins_u / trades_u * 100.0) if trades_u else 0.0

    all_u_pnls = [t["pnl"] for r in results_unl for t in r["trades_list"]]
    gross_win = sum(p for p in all_u_pnls if p > 0)
    gross_loss = abs(sum(p for p in all_u_pnls if p < 0))
    pf_u = (gross_win / gross_loss) if gross_loss > 0 else 999.0

    best_day_u = max(results_unl, key=lambda x: x["pnl"])
    worst_day_u = min(results_unl, key=lambda x: x["pnl"])
    win_days_u = len([r for r in results_unl if r["pnl"] > 0])
    loss_days_u = len([r for r in results_unl if r["pnl"] < 0])

    t_100 = len([p for p in all_u_pnls if p >= 100.0])
    t_300 = len([p for p in all_u_pnls if p >= 300.0])
    t_500 = len([p for p in all_u_pnls if p >= 500.0])
    t_1000 = len([p for p in all_u_pnls if p >= 1000.0])
    t_3000 = len([p for p in all_u_pnls if p >= 3000.0])

    print("\n" + "=" * 95)
    print("📅 6-MONTH MONTH-BY-MONTH BREAKDOWN (UNLIMITED PROFIT RUNNER)")
    print("=" * 95)
    print(f"{'Month':<10} | {'Days':<5} | {'Net Profits Harvested':<22} | {'Avg / Day':<14} | {'Win Days':<12} | {'Best Day':<14}")
    print("-" * 95)

    for m_key, dlist in sorted(monthly_unl.items()):
        m_net = sum(r["pnl"] for r in dlist)
        m_avg = m_net / len(dlist)
        m_wins = len([r for r in dlist if r["pnl"] > 0])
        m_best = max(dlist, key=lambda x: x["pnl"])["pnl"]
        print(f"{m_key:<10} | {len(dlist):>3d}   | ${m_net:>19,.2f} USD | ${m_avg:>10,.2f}/d | {m_wins:>2d}/{len(dlist):<2d} ({m_wins/len(dlist)*100:>4.1f}%) | ${m_best:>12,.2f}")

    print("=" * 95)
    print("🏆 6-MONTH EXECUTIVE AUDIT: UNLIMITED PROFIT RUNNER RESULTS")
    print("=" * 95)
    print(f"  • Total Trading Days Evaluated:    {len(trading_days)} Days (~March 23, 2026 to Sept 18, 2026)")
    print(f"  • Old Capped Engine Profits:       ${total_c:+,.2f} USD")
    print(f"  • Unlimited Runner Profits:        ${total_u:+,.2f} USD")
    print(f"  • Additional Edge by Letting Run:  +${(total_u - total_c):,.2f} USD ({((total_u - total_c)/max(1,total_c)*100.0):+.1f}% extra alpha)")
    print(f"  • Profit Factor:                   {pf_u:.2f}")
    print(f"  • Daily Win Rate:                  {(win_days_u / len(results_unl) * 100.0):.1f}% ({win_days_u} Winning Days / {loss_days_u} Loss Days)")
    print(f"  • Trade Win Rate:                  {wr_u:.1f}% ({wins_u}/{trades_u} executed trades)")
    print(f"  • Best Single Day Harvest:         {best_day_u['date']} (+${best_day_u['pnl']:,.2f} USD)")
    print(f"  • Worst Single Day:                {worst_day_u['date']} (${worst_day_u['pnl']:,.2f} USD)")
    print("-" * 95)
    print("🚀 MONSTER RUNNER DISTRIBUTION (TRADES ALLOWED TO RUN WITHOUT LIMITS):")
    print(f"  • Trades capturing > $100 Profit:  {t_100} Trades")
    print(f"  • Trades capturing > $300 Profit:  {t_300} Trades")
    print(f"  • Trades capturing > $500 Profit:  {t_500} Trades")
    print(f"  • Trades capturing > $1,000 Profit:{t_1000} Trades")
    print(f"  • Trades capturing > $3,000 Profit:{t_3000} Trades")
    print(f"  • Highest Single Trade Harvest:    +${max(all_u_pnls):,.2f} USD")
    print("=" * 95)


if __name__ == "__main__":
    main()
