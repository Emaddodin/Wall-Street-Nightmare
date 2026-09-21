"""
backtest_tiered_progression.py
==============================
Tests the Progressive Capital Tier Progression Algorithm ("100$ -> 300$ -> 500$ -> 1000$ and ..."):
1. Dynamic Tier Progression Algo:
   - Starts Day 1 with only $100 real seed capital.
   - Banks all daily profits into the Stratton Vault.
   - Automatically promotes daily morning starting allocation as cumulative bankroll hits milestones:
     * Bankroll < $300       -> Daily Allocation: $100
     * Bankroll >= $300      -> Daily Allocation: $300
     * Bankroll >= $500      -> Daily Allocation: $500
     * Bankroll >= $1,000    -> Daily Allocation: $1,000
     * Bankroll >= $2,500    -> Daily Allocation: $2,000
     * Bankroll >= $5,000    -> Daily Allocation: $3,000
     * Bankroll >= $10,000   -> Daily Allocation: $5,000
     * Bankroll >= $25,000   -> Daily Allocation: $10,000
   - Protects initial capital: If a loss occurs, it steps back down safely.
   
2. Fixed Morning Starting Capital Comparison:
   - Fixed $100 / day
   - Fixed $300 / day
   - Fixed $500 / day
   - Fixed $1,000 / day
   - Fixed $2,500 / day
   - Fixed $5,000 / day
"""

import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT_DIR = Path("/Users/mac/Desktop/TBT-Engine")
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

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


class TieredWednesdayEngine:
    def __init__(self, starting_balance: float = 100.00, max_scale: float = 20.0):
        self.starting_balance: float = starting_balance
        self.balance: float = starting_balance
        self.equity: float = starting_balance
        self.max_scale: float = max_scale
        self.active_stack: List[StackedOrder] = []
        self.active_bias: Optional[str] = None
        self.entry_bar_idx: int = 0
        self.sl_price: float = 0.0
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

    def open_stack(self, direction: str, entry_price: float, sl_price: float, bar_idx: int,
                   timestamp_ms: int) -> float:
        self.active_stack.clear()
        rng = np.random.default_rng(int(timestamp_ms) % (2 ** 32))
        num_orders = int(rng.integers(5, 11))
        
        # Base lot scales proportionally to morning starting balance ($100 = 1.0x unit)
        base_unit = max(1.0, self.starting_balance / 100.0)
        
        # Intraday compounding: scales up as equity expands within the day
        intraday_scale = min(self.max_scale, max(1.0, self.balance / self.starting_balance))
        total_lots = 0.0

        for i in range(num_orders):
            base_lot = float(rng.uniform(0.1, 0.25)) * base_unit
            lot_sz = round(base_lot * intraday_scale, 2)
            # Ensure within standard broker limits (cap single order at 50 lots)
            lot_sz = min(50.0, max(0.01, lot_sz))
            
            fill_slip = 0.02 if direction == "BUY" else -0.02
            fill_px = round(entry_price + fill_slip, 2)
            ord_item = StackedOrder(
                order_id=f"STACK-{i+1}-{timestamp_ms}",
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
        return total_pnl


def simulate_day_fast_tiered(date_str: str, start_balance: float = 100.00) -> Dict[str, Any]:
    raw_m1 = load_candles_for_date(date_str)
    if not raw_m1:
        return {"date": date_str, "pnl": 0.0, "trades": 0, "wins": 0}

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

    engine = TieredWednesdayEngine(starting_balance=start_balance, max_scale=20.0)
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
                spike_target = max(50.0, engine.balance * 0.50)

                if profit_gain >= spike_target:
                    pnl = engine.flatten_stack(tick_px, f"Spike (+${profit_gain:.2f})")
                    trades_record.append({"pnl": pnl, "win": True})
                    consecutive_opposing_bars = 0
                    continue

                hit_sl = (
                    (tick_px <= engine.sl_price if engine.active_bias == "BUY" else tick_px >= engine.sl_price)
                    or (profit_gain <= -max_risk_loss)
                )
                if hit_sl:
                    capped_loss = -max_risk_loss if profit_gain <= -max_risk_loss else profit_gain
                    pnl = engine.flatten_stack(tick_px, "SL Triggered", forced_pnl=capped_loss)
                    trades_record.append({"pnl": pnl, "win": False})
                    consecutive_opposing_bars = 0
                    continue

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

        # Entry Check
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
                        consecutive_opposing_bars = 0

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
                        consecutive_opposing_bars = 0

    if engine.active_stack:
        pnl = engine.flatten_stack(float(m1_close[-1]), "Session End Flush")
        trades_record.append({"pnl": pnl, "win": pnl > 0})

    day_pnl = engine.balance - start_balance
    wins = [t for t in trades_record if t["win"]]
    losses = [t for t in trades_record if not t["win"]]
    win_rate = (len(wins) / len(trades_record) * 100.0) if trades_record else 0.0

    return {
        "date": date_str,
        "start": start_balance,
        "end": engine.balance,
        "pnl": day_pnl,
        "trades": len(trades_record),
        "wins": len(wins),
        "losses": len(losses),
        "wr": win_rate,
        "max_dd": engine.max_dd_dollar,
    }


def get_tier_allocation(bankroll: float) -> float:
    """Progressive tier allocation rule: 100$ -> 300$ -> 500$ -> 1000$ -> 2000$ -> 5000$ -> 10000$"""
    if bankroll >= 25000.0:
        return 10000.0
    elif bankroll >= 10000.0:
        return 5000.0
    elif bankroll >= 5000.0:
        return 3000.0
    elif bankroll >= 2500.0:
        return 2000.0
    elif bankroll >= 1000.0:
        return 1000.0
    elif bankroll >= 500.0:
        return 500.0
    elif bankroll >= 300.0:
        return 300.0
    else:
        return 100.0


def run_progressive_algo(trading_days: List[str]):
    print("\n" + "=" * 105)
    print("🚀 DYNAMIC TIER PROGRESSION ALGO: 100$ -> 300$ -> 500$ -> 1000$ -> ...")
    print("Strategy: Starts with $100 seed capital. Profits banked into Vault. Starting allocation escalates with bankroll.")
    print("=" * 105)

    bankroll = 100.0  # Seed capital
    initial_seed = 100.0
    daily_records = []
    tier_transitions = []
    current_tier = 100.0

    for i, day in enumerate(trading_days, 1):
        alloc = get_tier_allocation(bankroll)
        if alloc != current_tier:
            tier_transitions.append({
                "day": i,
                "date": day,
                "from": current_tier,
                "to": alloc,
                "bankroll": bankroll
            })
            current_tier = alloc

        res = simulate_day_fast_tiered(day, start_balance=alloc)
        pnl = res["pnl"]
        bankroll += pnl
        daily_records.append({
            "day_num": i,
            "date": day,
            "tier_alloc": alloc,
            "day_pnl": pnl,
            "bankroll": bankroll,
            "trades": res["trades"],
            "wins": res["wins"]
        })

    win_days = len([r for r in daily_records if r["day_pnl"] > 0])
    loss_days = len([r for r in daily_records if r["day_pnl"] < 0])
    total_harvest = bankroll - initial_seed
    best_d = max(daily_records, key=lambda x: x["day_pnl"])
    worst_d = min(daily_records, key=lambda x: x["day_pnl"])

    print(f"\n📊 PROGRESSIVE TIER EXECUTION RESULTS (130 TRADING DAYS):")
    print(f"  • Initial Real Seed Capital:      ${initial_seed:,.2f} USD")
    print(f"  • Final Total Bankroll in Vault:  ${bankroll:,.2f} USD")
    print(f"  • Total Net Profit Harvested:     ${total_harvest:+,.2f} USD")
    print(f"  • Overall Return on Seed Capital: +{(total_harvest / initial_seed) * 100:,.1f}%")
    print(f"  • Daily Win Rate:                 {win_days / len(daily_records) * 100:.1f}% ({win_days} Win Days / {loss_days} Loss Days)")
    print(f"  • Best Single Day Harvest:        {best_d['date']} (+${best_d['day_pnl']:,.2f} USD at ${best_d['tier_alloc']:,.0f} Tier)")
    print(f"  • Worst Single Day Loss:          {worst_d['date']} (${worst_d['day_pnl']:,.2f} USD)")

    print("\n📈 TIER ESCALATION MILESTONES:")
    print(f"{'Day #':<7} | {'Date':<12} | {'Old Tier':<12} | {'New Tier':<12} | {'Total Vault Bankroll':<22}")
    print("-" * 72)
    for trans in tier_transitions:
        print(f"Day {trans['day']:<3d} | {trans['date']:<12} | ${trans['from']:<10,.0f} | ${trans['to']:<10,.0f} | ${trans['bankroll']:>18,.2f} USD")

    return daily_records, bankroll


def run_fixed_tier_comparisons(trading_days: List[str]):
    print("\n" + "=" * 105)
    print("📊 FIXED MORNING CAPITAL COMPARISONS: 100$ vs 300$ vs 500$ vs 1000$ vs 2500$ vs 5000$")
    print("=" * 105)
    print(f"| {'Fixed Morning Tier':<20} | {'Total 6M Profit':<18} | {'Avg Daily Profit':<16} | {'Best Day':<14} | {'Worst Day':<12} | {'Win Rate':<8} |")
    print("-" * 105)

    tiers = [100.0, 300.0, 500.0, 1000.0, 2500.0, 5000.0]
    comparison_results = []

    for tier in tiers:
        pnls = []
        win_days = 0
        for day in trading_days:
            res = simulate_day_fast_tiered(day, start_balance=tier)
            pnls.append(res["pnl"])
            if res["pnl"] > 0:
                win_days += 1

        tot_p = sum(pnls)
        avg_d = tot_p / len(trading_days)
        best = max(pnls)
        worst = min(pnls)
        wr = (win_days / len(trading_days)) * 100.0
        comparison_results.append({
            "tier": tier,
            "tot_pnl": tot_p,
            "avg_daily": avg_d,
            "best": best,
            "worst": worst,
            "wr": wr
        })
        print(f"| ${tier:>6,.0f} Daily Reset    | ${tot_p:>15,.2f} | ${avg_d:>13,.2f}/d | ${best:>11,.2f} | ${worst:>9,.2f} | {wr:>6.1f}% |")

    print("=" * 105)
    return comparison_results


def main():
    end_date = datetime(2026, 9, 18)
    curr = end_date
    trading_days = []
    while len(trading_days) < 130:
        if curr.weekday() < 5:
            trading_days.append(curr.strftime("%Y-%m-%d"))
        curr -= timedelta(days=1)
    trading_days.reverse()

    print("=" * 105)
    print("🏛️ STRATTON OAKMONT QUANTITATIVE LAB: TIERED PROGRESSION ALGO BACKTEST")
    print(f"Period: {trading_days[0]} to {trading_days[-1]} ({len(trading_days)} Trading Days)")
    print("=" * 105)

    # 1. Progressive Compounding Tier Algorithm (100$ -> 300$ -> 500$ -> 1000$ ...)
    run_progressive_algo(trading_days)

    # 2. Fixed Tiers Comparison ($100, $300, $500, $1000, $2500, $5000)
    run_fixed_tier_comparisons(trading_days)


if __name__ == "__main__":
    main()
