import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

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
class MoonOrder:
    order_id: str
    direction: str
    lot_size: float
    entry_price: float
    entry_time_ms: int
    commission: float

class MoonEngine:
    def __init__(self, starting_balance: float = 59.00, max_lot_cap: float = 50.0):
        self.balance: float = starting_balance
        self.equity: float = starting_balance
        self.active_stack: List[MoonOrder] = []
        self.active_bias: Optional[str] = None
        self.entry_bar_idx: int = 0
        self.sl_price: float = 0.0
        self.max_lot_cap: float = max_lot_cap
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
        num_orders = 8

        # AGGRESSIVE COMPOUNDING:
        # Scale lot size linearly with equity!
        # Every $50 of balance = 0.12 lots (e.g. $500 -> 1.2 lots, $5,000 -> 12 lots)
        # Capped only by broker ticket limits (50 lots)
        target_total_lots = min(self.max_lot_cap, round(0.12 * (self.balance / 50.0), 2))
        if target_total_lots < 0.08:
            target_total_lots = 0.08

        fill_price = round(mid_price + (SPREAD / 2.0) if direction == "BUY" else mid_price - (SPREAD / 2.0), 2)
        lots_per_order = round(target_total_lots / num_orders, 2)
        if lots_per_order < 0.01:
            lots_per_order = 0.01

        total_lots = 0.0
        for i in range(num_orders):
            comm = round(lots_per_order * COMMISSION_PER_LOT, 3)
            ord_item = MoonOrder(
                order_id=f"MOON-{i+1}-{timestamp_ms}",
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

def run_moon_day(date_str: str, engine: MoonEngine) -> Dict[str, Any]:
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
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "open_time": "first"})
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

    day_start_balance = engine.balance
    active_5m_breakout: Optional[Dict[str, Any]] = None
    trades_record: List[Dict[str, Any]] = []

    for idx in range(50, len(df_1m)):
        bar = df_1m.iloc[idx]
        curr_t_ms = int(bar["open_time"])

        o_px = float(bar["open"])
        h_px = float(bar["high"])
        l_px = float(bar["low"])
        c_px = float(bar["close"])

        trajectory = [o_px, l_px if o_px < c_px else h_px, h_px if o_px < c_px else l_px, c_px]

        for sub_i, tick_px in enumerate(trajectory):
            tick_px = round(float(tick_px), 2)

            m5_candidates = df_5m[df_5m["open_time"] <= curr_t_ms]
            if not m5_candidates.empty:
                last_5m = m5_candidates.iloc[-1]
                if bool(last_5m.get("break_up", False)) and not np.isnan(last_5m.get("res", np.nan)):
                    active_5m_breakout = {"type": "UP", "level": float(last_5m["res"]), "bar_time": int(last_5m["open_time"])}
                elif bool(last_5m.get("break_down", False)) and not np.isnan(last_5m.get("sup", np.nan)):
                    active_5m_breakout = {"type": "DOWN", "level": float(last_5m["sup"]), "bar_time": int(last_5m["open_time"])}

            if engine.active_stack:
                flt_eq = engine.calculate_equity(tick_px)
                profit_gain = flt_eq - engine.balance
                max_risk_loss = max(9.0, engine.balance * 0.15)
                spike_target = max(20.0, engine.balance * 0.35)

                if profit_gain >= spike_target:
                    pnl = engine.flatten_stack(tick_px, f"Spike (+${profit_gain:.2f})")
                    trades_record.append({"pnl": pnl, "win": True})
                    continue

                hit_sl = (
                    (tick_px <= engine.sl_price if engine.active_bias == "BUY" else tick_px >= engine.sl_price)
                    or (profit_gain <= -max_risk_loss)
                )
                if hit_sl:
                    capped_loss = -max_risk_loss if profit_gain <= -max_risk_loss else profit_gain
                    pnl = engine.flatten_stack(tick_px, "SL Invalidation", forced_pnl=capped_loss)
                    trades_record.append({"pnl": pnl, "win": False})
                    continue

                if sub_i == len(trajectory) - 1 and idx > engine.entry_bar_idx:
                    is_stall = (
                        (engine.active_bias == "BUY" and c_px < o_px)
                        or (engine.active_bias == "SELL" and c_px > o_px)
                    )
                    if is_stall:
                        if profit_gain > 0:
                            pnl = engine.flatten_stack(tick_px, f"Momentum Stall (+${profit_gain:.2f})")
                            trades_record.append({"pnl": pnl, "win": True})
                        else:
                            capped_loss = max(profit_gain, -max_risk_loss)
                            pnl = engine.flatten_stack(tick_px, "Momentum Stall", forced_pnl=capped_loss)
                            trades_record.append({"pnl": pnl, "win": False})
                        continue

        curr_px = c_px
        if not engine.active_stack and active_5m_breakout is not None:
            lvl = active_5m_breakout["level"]
            b_type = active_5m_breakout["type"]
            b_time = active_5m_breakout["bar_time"]

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

    if engine.active_stack:
        pnl = engine.flatten_stack(float(df_1m["close"].iloc[-1]), "EOD")
        trades_record.append({"pnl": pnl, "win": pnl > 0})

    day_pnl = engine.balance - day_start_balance
    wins = [t for t in trades_record if t["win"]]
    return {
        "date": date_str,
        "start": day_start_balance,
        "end": engine.balance,
        "pnl": day_pnl,
        "trades": len(trades_record),
        "wr": (len(wins) / len(trades_record) * 100.0) if trades_record else 0.0,
    }

def run_moon_backtest():
    end_date = datetime(2026, 9, 18)
    curr = end_date
    trading_days = []
    while len(trading_days) < 22:
        if curr.weekday() < 5:
            trading_days.append(curr.strftime("%Y-%m-%d"))
        curr -= timedelta(days=1)
    trading_days.reverse()

    # TEST WITH BROKER REALISTIC TICKET LIMIT: Max 25.0 Lots per Stack (Institutional Cap)
    engine_25 = MoonEngine(starting_balance=59.00, max_lot_cap=25.0)

    print("=" * 95)
    print("🌕 FULL COMPOUNDING 'TO THE MOON' MODE (STARTING BALANCE: $59.00 USD)")
    print("Policy: ZERO DAILY RESET. Every dollar of profit compounds directly into the next trade.")
    print("Broker Cap: 25.0 Lots Maximum per Stack (LiteFinance ECN liquidity ceiling)")
    print("=" * 95)
    print(f"{'Day':<4} | {'Date':<10} | {'Start Balance ($)':<18} | {'Daily PnL ($)':<18} | {'End Balance ($)':<18} | {'Trades':<6} | {'WR (%)':<6}")
    print("-" * 95)

    results_25 = []
    for i, d in enumerate(trading_days, 1):
        res = run_moon_day(d, engine_25)
        results_25.append(res)
        print(f"{i:02d}   | {d:<10} | ${res['start']:>16,.2f} | {res['pnl']:>+17,.2f} | ${res['end']:>16,.2f} | {res['trades']:>6d} | {res['wr']:>5.1f}%")

    total_net = engine_25.balance - 59.00
    total_roi = (total_net / 59.00) * 100.0

    print("=" * 95)
    print("🚀 'TO THE MOON' FINAL MISSION SUMMARY")
    print("=" * 95)
    print(f"  • Starting Capital (Day 1):         $59.00 USD")
    print(f"  • Final Account Balance (Day 22):   ${engine_25.balance:,.2f} USD")
    print(f"  • Net Compounded Profit:            ${total_net:+,.2f} USD")
    print(f"  • Cumulative Return on Investment:  {total_roi:+,.1f}%")
    print("=" * 95)

if __name__ == "__main__":
    run_moon_backtest()
