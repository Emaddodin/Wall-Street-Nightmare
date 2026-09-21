"""
optimize_6months.py
Quantitative optimization study comparing:
1. Baseline (Current Wednesday Strategy): Scale cap 10x, +35% TP, 1-bar stall exit, 24h
2. Variation A: London + NY Active Hours Only (06:00 to 18:00 UTC)
3. Variation B: 2-Bar Opposing Confirmation (Avoid cutting trades on single 1m wiggles)
4. Variation C: Increased Scale Cap to 15x / 20x (Unleashing larger compounding when deep in profit)
5. Variation D: Dynamic Trailing Exit (Locking Breakeven at +20%, letting runners stretch to +50%+)
6. Variation E: Daily Loss Circuit Breaker (Halt session if down -$35 to preserve capital for next day)
7. Variation F: COMBINED OPTIMAL ENGINE
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


class ConfigurableWednesdayEngine:
    def __init__(self, starting_balance: float = 100.00, max_scale: float = 10.0):
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
        scale = max(1.0, self.balance / 50.0)
        total_lots = 0.0

        for i in range(num_orders):
            lot_sz = round(float(rng.uniform(0.1, 0.25)) * min(scale, self.max_scale), 2)
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


def simulate_day_variant(
    date_str: str,
    start_balance: float = 100.00,
    max_scale: float = 10.0,
    spike_target_pct: float = 0.35,
    stall_bars_needed: int = 1,
    active_hours_only: bool = False,
    daily_circuit_breaker: Optional[float] = None,
    trail_runner: bool = False,
) -> Dict[str, Any]:
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

    m5_times = df_5m["open_time"].values
    m5_break_up = df_5m["break_up"].values
    m5_break_down = df_5m["break_down"].values
    m5_res = df_5m["res"].values
    m5_sup = df_5m["sup"].values

    m1_open = df_1m["open"].values
    m1_high = df_1m["high"].values
    m1_low = df_1m["low"].values
    m1_close = df_1m["close"].values
    m1_times = df_1m["open_time"].values
    m1_ema20 = df_1m["ema20"].values
    m1_ema50 = df_1m["ema50"].values
    m1_pin_long = df_1m["pin_long"].values
    m1_pin_short = df_1m["pin_short"].values
    m1_hammer = df_1m["hammer"].values
    m1_inv_hammer = df_1m["inv_hammer"].values

    engine = ConfigurableWednesdayEngine(starting_balance=start_balance, max_scale=max_scale)
    active_5m_breakout: Optional[Dict[str, Any]] = None
    trades_record: List[Dict[str, Any]] = []
    consecutive_opposing_bars = 0
    high_water_mark_profit = 0.0

    for idx in range(len(df_1m)):
        curr_t_ms = int(m1_times[idx])
        o_px = float(m1_open[idx])
        h_px = float(m1_high[idx])
        l_px = float(m1_low[idx])
        c_px = float(m1_close[idx])

        # Circuit breaker: check if day loss exceeds threshold
        if daily_circuit_breaker is not None:
            if (engine.balance - start_balance) <= -daily_circuit_breaker and not engine.active_stack:
                break

        # Session filter: Active hours 06:00 to 19:00 UTC (London + NY)
        hour_utc = (curr_t_ms // 3600000) % 24
        if active_hours_only and not (6 <= hour_utc < 19):
            if not engine.active_stack:
                continue

        m5_idx = np.searchsorted(m5_times, curr_t_ms, side="right") - 1
        if m5_idx >= 0:
            if bool(m5_break_up[m5_idx]) and not np.isnan(m5_res[m5_idx]):
                active_5m_breakout = {"type": "UP", "level": float(m5_res[m5_idx]), "bar_time": int(m5_times[m5_idx])}
            elif bool(m5_break_down[m5_idx]) and not np.isnan(m5_sup[m5_idx]):
                active_5m_breakout = {"type": "DOWN", "level": float(m5_sup[m5_idx]), "bar_time": int(m5_times[m5_idx])}

        trajectory = [o_px, l_px if o_px < c_px else h_px, h_px if o_px < c_px else l_px, c_px]

        for sub_i, tick_px in enumerate(trajectory):
            tick_px = round(float(tick_px), 2)
            if engine.active_stack:
                flt_eq = engine.calculate_equity(tick_px)
                profit_gain = flt_eq - engine.balance
                max_risk_loss = max(9.0, engine.balance * 0.15)
                spike_target = max(20.0, engine.balance * spike_target_pct)

                if profit_gain > high_water_mark_profit:
                    high_water_mark_profit = profit_gain

                # Trailing runner logic: if reached spike target, lock in at least 70% of peak or exit if pulling back
                if trail_runner and high_water_mark_profit >= spike_target:
                    trail_exit_level = high_water_mark_profit * 0.75
                    if profit_gain <= trail_exit_level:
                        pnl = engine.flatten_stack(tick_px, "Trailing Profit Lock")
                        trades_record.append({"pnl": pnl, "win": True})
                        consecutive_opposing_bars = 0
                        high_water_mark_profit = 0.0
                        continue
                elif not trail_runner and profit_gain >= spike_target:
                    pnl = engine.flatten_stack(tick_px, f"Spike (+${profit_gain:.2f})")
                    trades_record.append({"pnl": pnl, "win": True})
                    consecutive_opposing_bars = 0
                    high_water_mark_profit = 0.0
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
                    high_water_mark_profit = 0.0
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

                    if consecutive_opposing_bars >= stall_bars_needed:
                        if profit_gain > 0:
                            pnl = engine.flatten_stack(tick_px, "Momentum Stall Win")
                            trades_record.append({"pnl": pnl, "win": True})
                        else:
                            capped_loss = max(profit_gain, -max_risk_loss)
                            pnl = engine.flatten_stack(tick_px, "Momentum Stall Loss", forced_pnl=capped_loss)
                            trades_record.append({"pnl": pnl, "win": False})
                        consecutive_opposing_bars = 0
                        high_water_mark_profit = 0.0
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
                        high_water_mark_profit = 0.0

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
                        high_water_mark_profit = 0.0

    if engine.active_stack:
        pnl = engine.flatten_stack(float(m1_close[-1]), "Session End Flush")
        trades_record.append({"pnl": pnl, "win": pnl > 0})

    day_pnl = engine.balance - start_balance
    wins = [t for t in trades_record if t["win"]]
    return {
        "date": date_str,
        "pnl": day_pnl,
        "trades": len(trades_record),
        "wins": len(wins),
        "wr": (len(wins) / len(trades_record) * 100.0) if trades_record else 0.0,
    }


def evaluate_setup(name: str, days: List[str], **kwargs) -> Dict[str, Any]:
    daily_pnls = []
    win_days = 0
    total_trades = 0
    total_wins = 0

    for i, d in enumerate(days, 1):
        s_bal = 59.00 if i == 1 else 100.00
        res = simulate_day_variant(d, start_balance=s_bal, **kwargs)
        daily_pnls.append(res["pnl"])
        if res["pnl"] > 0:
            win_days += 1
        total_trades += res["trades"]
        total_wins += res["wins"]

    tot_pnl = sum(daily_pnls)
    avg_d = tot_pnl / len(days)
    win_d_pct = (win_days / len(days)) * 100.0
    trade_wr = (total_wins / total_trades * 100.0) if total_trades > 0 else 0.0
    best = max(daily_pnls)
    worst = min(daily_pnls)

    print(f"| {name:<35} | ${tot_pnl:>14,.2f} | ${avg_d:>10,.2f}/d | {win_d_pct:>5.1f}% | {trade_wr:>5.1f}% | ${best:>11,.2f} | ${worst:>9,.2f} |")
    return {
        "name": name,
        "tot_pnl": tot_pnl,
        "avg_daily": avg_d,
        "win_days_pct": win_d_pct,
        "trade_wr": trade_wr,
        "best": best,
        "worst": worst,
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

    print("=" * 115)
    print("🔬 COMPREHENSIVE 6-MONTH STRATEGY OPTIMIZATION MATRIX (130 TRADING DAYS)")
    print("=" * 115)
    print(f"| {'Configuration':<35} | {'Total 6M PnL':<14} | {'Avg Daily':<12} | {'WinDays':<7} | {'Trd WR':<6} | {'Best Day':<13} | {'Worst Day':<11} |")
    print("-" * 115)

    # 1. Baseline
    evaluate_setup("1. Baseline (Current Wednesday)", trading_days,
                   max_scale=10.0, spike_target_pct=0.35, stall_bars_needed=1, active_hours_only=False)

    # 2. Scale Cap Expansion: 15x
    evaluate_setup("2. Scale Cap 15x (compounds more)", trading_days,
                   max_scale=15.0, spike_target_pct=0.35, stall_bars_needed=1, active_hours_only=False)

    # 3. Scale Cap Expansion: 20x
    evaluate_setup("3. Scale Cap 20x (aggressive)", trading_days,
                   max_scale=20.0, spike_target_pct=0.35, stall_bars_needed=1, active_hours_only=False)

    # 4. Filter: London + NY Hours (06:00-19:00 UTC)
    evaluate_setup("4. Active Hours Only (06-19 UTC)", trading_days,
                   max_scale=10.0, spike_target_pct=0.35, stall_bars_needed=1, active_hours_only=True)

    # 5. Stall confirmation: 2 bars (filter 1m noise)
    evaluate_setup("5. Stall Exit: 2 Opposing Bars", trading_days,
                   max_scale=10.0, spike_target_pct=0.35, stall_bars_needed=2, active_hours_only=False)

    # 6. Higher Target: +50% Spike TP
    evaluate_setup("6. Target +50% Spike TP", trading_days,
                   max_scale=10.0, spike_target_pct=0.50, stall_bars_needed=1, active_hours_only=False)

    # 7. Trailing Runner (Trail 75% peak after +35%)
    evaluate_setup("7. Trailing Runner (+35% -> Trail)", trading_days,
                   max_scale=10.0, spike_target_pct=0.35, stall_bars_needed=1, trail_runner=True)

    # 8. Daily Circuit Breaker: Halt at -$40 daily loss
    evaluate_setup("8. Daily Circuit Breaker (-$40)", trading_days,
                   max_scale=10.0, spike_target_pct=0.35, stall_bars_needed=1, daily_circuit_breaker=40.0)

    # 9. Synergistic Combination Alpha (Scale 15x + 2-Bar Stall + Trailing Runner)
    evaluate_setup("9. Combo Alpha: 15x + 2-Bar + Trail", trading_days,
                   max_scale=15.0, spike_target_pct=0.35, stall_bars_needed=2, trail_runner=True)

    # 10. Synergistic Combination Elite (Scale 20x + 2-Bar + Active Hours)
    evaluate_setup("10. Combo Elite: 20x + 2-Bar + ActHrs", trading_days,
                   max_scale=20.0, spike_target_pct=0.35, stall_bars_needed=2, active_hours_only=True)

    # 11. Supreme Moon Engine (Scale 20x + 2-Bar + Trail + Circuit Breaker)
    evaluate_setup("11. Supreme Moon: 20x+2Bar+Trail+CB", trading_days,
                   max_scale=20.0, spike_target_pct=0.40, stall_bars_needed=2, trail_runner=True, daily_circuit_breaker=45.0)

    print("=" * 115)


if __name__ == "__main__":
    main()
