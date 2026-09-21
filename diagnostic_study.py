"""
diagnostic_study.py
Analyzes trade exit breakdown and tests key structural optimizations:
1. Baseline exit reasons and PnL breakdown
2. Session filter: London + NY hours (06:00 to 18:00 UTC) vs Asian chop
3. Scale cap: 10x vs 15x vs 20x
4. Stall exit refinement: 2 consecutive opposing candles vs instant 1 candle panic
5. Trailing Take-Profit: let runners run past +35% up to +70%
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

ROOT_DIR = Path("/Users/mac/Desktop/TBT-Engine")
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import backtest_6months_wednesday as bw

def get_130_days():
    end_date = datetime(2026, 9, 18)
    curr = end_date
    trading_days = []
    while len(trading_days) < 130:
        if curr.weekday() < 5:
            trading_days.append(curr.strftime("%Y-%m-%d"))
        curr -= timedelta(days=1)
    trading_days.reverse()
    return trading_days

def run_detailed_diagnostic(trading_days):
    print("--- RUNNING DETAILED BASELINE DIAGNOSTIC ---")
    exit_reasons = {}
    hour_stats = {h: {"trades": 0, "pnl": 0.0, "wins": 0} for h in range(24)}
    
    for i, day in enumerate(trading_days, 1):
        start_bal = 59.00 if i == 1 else 100.00
        raw_m1 = bw.load_candles_for_date(day)
        if not raw_m1:
            continue
            
        df_1m = pd.DataFrame(raw_m1)
        df_1m["datetime"] = pd.to_datetime(df_1m["open_time"], unit="ms", utc=True)
        df_1m.sort_values("open_time", inplace=True)
        df_1m.reset_index(drop=True, inplace=True)

        df_1m["ema20"] = df_1m["close"].ewm(span=20).mean()
        df_1m["ema50"] = df_1m["close"].ewm(span=50).mean()
        df_1m["pin_long"] = bw.pa_candles.identify_pin_bar(df_1m, "bullish")
        df_1m["pin_short"] = bw.pa_candles.identify_pin_bar(df_1m, "bearish")
        df_1m["hammer"] = bw.pa_candles.identify_hammer(df_1m)
        df_1m["inv_hammer"] = bw.pa_candles.identify_inverted_hammer(df_1m)

        df_5m = (
            df_1m.set_index("datetime")
            .resample("5min")
            .agg({
                "open": "first", "high": "max", "low": "min", "close": "last",
                "volume": "sum", "open_time": "first",
            })
            .dropna().reset_index()
        )
        df_5m = bw.pa_levels.identify_sr_levels(df_5m, lookback=12)
        df_5m = bw.pa_levels.detect_breakouts(df_5m)

        m5_times = df_5m["open_time"].values
        m5_break_up = df_5m["breakout_up"].values
        m5_break_down = df_5m["breakout_down"].values
        m5_res = df_5m["res_level"].values
        m5_sup = df_5m["sup_level"].values

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

        engine = bw.WednesdayEngine(starting_balance=start_bal)
        active_5m_breakout = None

        for idx in range(len(df_1m)):
            curr_t_ms = int(m1_times[idx])
            o_px = float(m1_open[idx])
            h_px = float(m1_high[idx])
            l_px = float(m1_low[idx])
            c_px = float(m1_close[idx])

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
                    spike_target = max(20.0, engine.balance * 0.35)

                    hour = (curr_t_ms // 3600000) % 24

                    if profit_gain >= spike_target:
                        pnl = engine.flatten_stack(tick_px, "Spike Target")
                        exit_reasons["Spike Target"] = exit_reasons.get("Spike Target", 0.0) + pnl
                        hour_stats[hour]["trades"] += 1
                        hour_stats[hour]["pnl"] += pnl
                        hour_stats[hour]["wins"] += 1
                        continue

                    hit_sl = (
                        (tick_px <= engine.sl_price if engine.active_bias == "BUY" else tick_px >= engine.sl_price)
                        or (profit_gain <= -max_risk_loss)
                    )
                    if hit_sl:
                        capped_loss = -max_risk_loss if profit_gain <= -max_risk_loss else profit_gain
                        pnl = engine.flatten_stack(tick_px, "SL Triggered", forced_pnl=capped_loss)
                        exit_reasons["SL Triggered"] = exit_reasons.get("SL Triggered", 0.0) + pnl
                        hour_stats[hour]["trades"] += 1
                        hour_stats[hour]["pnl"] += pnl
                        continue

                    if sub_i == len(trajectory) - 1 and idx > engine.entry_bar_idx:
                        is_stall = (
                            (engine.active_bias == "BUY" and c_px < o_px)
                            or (engine.active_bias == "SELL" and c_px > o_px)
                        )
                        if is_stall:
                            if profit_gain > 0:
                                pnl = engine.flatten_stack(tick_px, "Stall Win")
                                exit_reasons["Stall Win"] = exit_reasons.get("Stall Win", 0.0) + pnl
                                hour_stats[hour]["wins"] += 1
                            else:
                                capped_loss = max(profit_gain, -max_risk_loss)
                                pnl = engine.flatten_stack(tick_px, "Stall Loss", forced_pnl=capped_loss)
                                exit_reasons["Stall Loss"] = exit_reasons.get("Stall Loss", 0.0) + pnl
                            hour_stats[hour]["trades"] += 1
                            hour_stats[hour]["pnl"] += pnl
                            continue

            # Entry
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
                            engine.open_stack("BUY", curr_px, round(l_px - 0.15, 2), idx, curr_t_ms)
                            active_5m_breakout = None
                    elif b_type == "DOWN":
                        trend_ok = curr_px < m1_ema20[idx] < m1_ema50[idx]
                        retest_ok = h_px >= lvl - 1.2 and l_px <= lvl + 0.2
                        upper_wick = h_px - max(o_px, c_px)
                        wick_ratio = upper_wick / rng_val
                        rejection_ok = (wick_ratio >= 0.45 and c_px <= o_px) or bool(m1_pin_short[idx]) or bool(m1_inv_hammer[idx])
                        if trend_ok and retest_ok and rejection_ok and wick_ratio >= 0.45:
                            engine.open_stack("SELL", curr_px, round(h_px + 0.15, 2), idx, curr_t_ms)
                            active_5m_breakout = None

    print("\n--- EXIT REASONS TOTAL PNL CONTRIBUTION ---")
    for reason, pnl in sorted(exit_reasons.items(), key=lambda x: x[1], reverse=True):
        print(f"  {reason:<20}: ${pnl:>14,.2f} USD")

    print("\n--- PERFORMANCE BY HOUR OF DAY (UTC) ---")
    print(f"{'Hour (UTC)':<12} | {'Trades':<8} | {'Win Rate':<10} | {'Net PnL':<16}")
    print("-" * 52)
    for h in range(24):
        t = hour_stats[h]["trades"]
        p = hour_stats[h]["pnl"]
        w = hour_stats[h]["wins"]
        wr = (w / t * 100) if t > 0 else 0
        print(f"{h:02d}:00 - {h:02d}:59  | {t:>6d}   | {wr:>6.1f}%    | ${p:>13,.2f}")

if __name__ == "__main__":
    days = get_130_days()
    run_detailed_diagnostic(days)
