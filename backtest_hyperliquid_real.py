"""
backtest_hyperliquid_real.py
============================
Backtesting the exact ICT Breakout -> Retest -> Rejection scalper under
REAL-WORLD HYPERLIQUID PROTOCOL SPECS:
- Asset: PAXG-PERP (Paxos Gold, 1 PAXG = 1 troy oz)
- Maximum Allowed Leverage: 10x
- Fee Structure: Taker fee 0.045% (VIP0 base), Maker fee 0.015%
- Minimum Order Value: $10.00 notional
- Maintenance Margin: 5.0% (Tier 1 < $3M notional)
- Starting Capital: $50.00 USDC
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

ROOT_DIR = Path(__file__).resolve().parent
SCALPER_DIR = ROOT_DIR / "scalper"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(SCALPER_DIR) not in sys.path:
    sys.path.append(str(SCALPER_DIR))

import scalper.pa.levels as pa_levels
import scalper.pa.candles as pa_candles
from replay_data import load_candles_for_date

def run_hl_backtest():
    end_date = datetime(2026, 9, 18)
    curr = end_date
    trading_days = []
    while len(trading_days) < 22:
        if curr.weekday() < 5:
            trading_days.append(curr.strftime("%Y-%m-%d"))
        curr -= timedelta(days=1)
    trading_days.reverse()

    print("=" * 80)
    print("⚡ HYPERLIQUID REAL-WORLD PROTOCOL SPEC BACKTEST (PAXG-PERP)")
    print("Specs: Max Leverage: 10x | Taker Fee: 0.045% | Maintenance Margin: 5%")
    print("Initial Capital: $50.00 USDC | Period: 22 Trading Days (2026-08-20 to 2026-09-18)")
    print("=" * 80)

    balance = 50.0
    fee_rate = 0.00045 # 0.045%
    daily_summary = []

    for day in trading_days:
        raw_m1 = load_candles_for_date(day)
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

        day_start_bal = balance
        active_breakout = None
        pos = None
        trades = 0
        day_fees = 0.0

        for idx in range(50, len(df_1m)):
            bar = df_1m.iloc[idx]
            curr_t_ms = int(bar["open_time"])
            o_px, h_px, l_px, c_px = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])

            # 5m breakout check
            m5_cands = df_5m[df_5m["open_time"] <= curr_t_ms]
            if not m5_cands.empty:
                l5 = m5_cands.iloc[-1]
                if bool(l5.get("break_up", False)) and not np.isnan(l5.get("res", np.nan)):
                    active_breakout = {"type": "UP", "level": float(l5["res"]), "time": int(l5["open_time"])}
                elif bool(l5.get("break_down", False)) and not np.isnan(l5.get("sup", np.nan)):
                    active_breakout = {"type": "DOWN", "level": float(l5["sup"]), "time": int(l5["open_time"])}

            # Active position management
            if pos:
                traj = [o_px, l_px if o_px < c_px else h_px, h_px if o_px < c_px else l_px, c_px]
                for sub_i, px in enumerate(traj):
                    if not pos:
                        break
                    pnl = (px - pos["entry"]) * pos["sz_paxg"] if pos["dir"] == "BUY" else (pos["entry"] - px) * pos["sz_paxg"]
                    spike_target = max(3.0, balance * 0.15)
                    max_stop = max(2.0, balance * 0.08)

                    exit_reason = None
                    if pnl >= spike_target:
                        exit_reason = "Spike"
                    elif pnl <= -max_stop or (px <= pos["sl"] if pos["dir"] == "BUY" else px >= pos["sl"]):
                        exit_reason = "SL"
                    elif sub_i == len(traj) - 1 and idx > pos["entry_bar"]:
                        is_stall = (pos["dir"] == "BUY" and c_px < o_px) or (pos["dir"] == "SELL" and c_px > o_px)
                        if is_stall:
                            exit_reason = "Stall"

                    if exit_reason:
                        exit_fee = (px * pos["sz_paxg"]) * fee_rate
                        day_fees += exit_fee
                        balance += (pnl - exit_fee)
                        trades += 1
                        pos = None
                        break

            # Entry condition
            if not pos and active_breakout:
                lvl, b_type, b_time = active_breakout["level"], active_breakout["type"], active_breakout["time"]
                if 0 < (curr_t_ms - b_time) <= 20 * 60_000:
                    if b_type == "UP":
                        trend_ok = c_px > bar["ema20"] > bar["ema50"]
                        retest_ok = l_px <= lvl + 1.2 and h_px >= lvl - 0.2
                        rng_val = max(0.01, h_px - l_px)
                        wick_ratio = (min(o_px, c_px) - l_px) / rng_val
                        rejection_ok = (wick_ratio >= 0.45 and c_px >= o_px) or bool(bar["pin_long"]) or bool(bar["hammer"])
                        if trend_ok and retest_ok and rejection_ok and wick_ratio >= 0.45:
                            # 10x max leverage
                            max_notional = balance * 9.5 # 9.5x leverage safe margin
                            sz_paxg = round(max_notional / c_px, 3)
                            if sz_paxg * c_px >= 10.0:
                                entry_fee = (c_px * sz_paxg) * fee_rate
                                balance -= entry_fee
                                day_fees += entry_fee
                                pos = {"dir": "BUY", "entry": c_px, "sz_paxg": sz_paxg, "sl": l_px - 0.15, "entry_bar": idx}
                                active_breakout = None

                    elif b_type == "DOWN":
                        trend_ok = c_px < bar["ema20"] < bar["ema50"]
                        retest_ok = h_px >= lvl - 1.2 and l_px <= lvl + 0.2
                        rng_val = max(0.01, h_px - l_px)
                        wick_ratio = (h_px - max(o_px, c_px)) / rng_val
                        rejection_ok = (wick_ratio >= 0.45 and c_px <= o_px) or bool(bar["pin_short"]) or bool(bar["inv_hammer"])
                        if trend_ok and retest_ok and rejection_ok and wick_ratio >= 0.45:
                            max_notional = balance * 9.5
                            sz_paxg = round(max_notional / c_px, 3)
                            if sz_paxg * c_px >= 10.0:
                                entry_fee = (c_px * sz_paxg) * fee_rate
                                balance -= entry_fee
                                day_fees += entry_fee
                                pos = {"dir": "SELL", "entry": c_px, "sz_paxg": sz_paxg, "sl": h_px + 0.15, "entry_bar": idx}
                                active_breakout = None

        net_day = balance - day_start_bal
        ret_pct = (net_day / day_start_bal) * 100.0 if day_start_bal > 0 else 0.0
        daily_summary.append({
            "day": day, "start": day_start_bal, "end": balance, "pnl": net_day,
            "ret": ret_pct, "trades": trades, "fees": day_fees
        })

    print(f"{'Date':<12} | {'Start ($)':<10} | {'Net PnL ($)':<12} | {'Return (%)':<10} | {'End ($)':<10} | {'Trades':<6} | {'Fees ($)':<8}")
    print("-" * 84)
    for d in daily_summary:
        print(f"{d['day']:<12} | {d['start']:>10.2f} | {d['pnl']:>+12.2f} | {d['ret']:>+9.1f}% | {d['end']:>10.2f} | {d['trades']:>6d} | {d['fees']:>8.2f}")

    print("=" * 84)
    print(f"Hyperliquid 1-Month Compounded: $50.00 -> ${balance:,.2f} (Total Return: {((balance - 50.0) / 50.0 * 100.0):+,.1f}%)")
    print(f"Total Taker Fees Paid: ${sum(d['fees'] for d in daily_summary):,.2f}")

if __name__ == "__main__":
    run_hl_backtest()
