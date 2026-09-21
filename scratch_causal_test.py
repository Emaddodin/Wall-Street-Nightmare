import json
import logging
from pathlib import Path
import numpy as np
import pandas as pd

import scalper.pa.levels as pa_levels
import scalper.pa.candles as pa_candles
from replay_data import load_thursday_candles

raw_m1 = load_thursday_candles()
print(f"Loaded {len(raw_m1)} candles.")

history_1m = []
history_5m = []

balance = 50.0
trades = []
active_stack = None
active_5m_breakout = None

for idx, bar in enumerate(raw_m1):
    history_1m.append(bar)
    
    # Check if a 5m bar just closed
    # 5m bar closes when len(history_1m) is a multiple of 5
    if len(history_1m) % 5 == 0 and len(history_1m) >= 5:
        chunk = history_1m[-5:]
        bar_5m = {
            "open_time": chunk[0]["open_time"],
            "open": chunk[0]["open"],
            "high": max(c["high"] for c in chunk),
            "low": min(c["low"] for c in chunk),
            "close": chunk[-1]["close"],
            "volume": sum(c["volume"] for c in chunk),
        }
        history_5m.append(bar_5m)
        
        # Check breakout on closed 5m bars
        if len(history_5m) >= 13:
            df_5m = pd.DataFrame(history_5m)
            b_up = pa_levels.breakout_up(df_5m, 12, range_pct=0.05).iloc[-1]
            b_down = pa_levels.breakout_down(df_5m, 12, range_pct=0.05).iloc[-1]
            r_high = pa_levels.range_high(df_5m, 12).iloc[-1]
            r_low = pa_levels.range_low(df_5m, 12).iloc[-1]
            
            if b_up and not np.isnan(r_high):
                active_5m_breakout = {
                    "type": "UP",
                    "level": float(r_high),
                    "bar_time": int(bar_5m["open_time"]),
                }
            elif b_down and not np.isnan(r_low):
                active_5m_breakout = {
                    "type": "DOWN",
                    "level": float(r_low),
                    "bar_time": int(bar_5m["open_time"]),
                }

    if len(history_1m) < 50:
        continue

    curr_t_ms = int(bar["open_time"])
    o_px = float(bar["open"])
    h_px = float(bar["high"])
    l_px = float(bar["low"])
    c_px = float(bar["close"])

    # 1. Manage active position with intrabar sub-ticks
    if active_stack:
        trajectory = [o_px, l_px if o_px < c_px else h_px, h_px if o_px < c_px else l_px, c_px]
        for sub_i, tick_px in enumerate(trajectory):
            direction = active_stack["direction"]
            lots = active_stack["lots"]
            entry_px = active_stack["entry_price"]
            sl_px = active_stack["sl_price"]
            spike_target = max(50.0, balance * 0.35)
            max_risk_loss = max(15.0, balance * 0.15)
            
            if direction == "BUY":
                floating_pnl = (tick_px - entry_px) * 100.0 * lots
            else:
                floating_pnl = (entry_px - tick_px) * 100.0 * lots

            # Spike exit
            if floating_pnl >= spike_target:
                balance += floating_pnl
                trades.append({"type": direction, "pnl": floating_pnl, "balance": balance, "reason": "Spike Exit", "exit_px": tick_px})
                active_stack = None
                break

            # Stop loss
            hit_sl = (tick_px <= sl_px if direction == "BUY" else tick_px >= sl_px) or (floating_pnl <= -max_risk_loss)
            if hit_sl:
                actual_loss = max(floating_pnl, -max_risk_loss)
                balance += actual_loss
                trades.append({"type": direction, "pnl": actual_loss, "balance": balance, "reason": "SL Hit", "exit_px": tick_px})
                active_stack = None
                break

            # Momentum stall on bar close
            if sub_i == len(trajectory) - 1 and idx > active_stack["entry_idx"]:
                is_stall = (direction == "BUY" and c_px < o_px) or (direction == "SELL" and c_px > o_px)
                if is_stall:
                    actual_pnl = floating_pnl if floating_pnl > 0 else max(floating_pnl, -max_risk_loss)
                    balance += actual_pnl
                    trades.append({"type": direction, "pnl": actual_pnl, "balance": balance, "reason": "Momentum Stall", "exit_px": tick_px})
                    active_stack = None
                    break

    # 2. Check entry setup if no active position
    if not active_stack and active_5m_breakout is not None:
        lvl = active_5m_breakout["level"]
        b_type = active_5m_breakout["type"]
        b_time = active_5m_breakout["bar_time"]

        # 20 min window
        if 0 < (curr_t_ms - b_time) <= 20 * 60_000:
            # Calculate rolling EMA20 and EMA50 on closed 1m bars
            closes = pd.Series([c["close"] for c in history_1m])
            ema20 = closes.ewm(span=20).mean().iloc[-1]
            ema50 = closes.ewm(span=50).mean().iloc[-1]

            rng = max(0.01, h_px - l_px)
            if b_type == "UP":
                trend_ok = c_px > ema20 > ema50
                retest_ok = l_px <= lvl + 1.2 and h_px >= lvl - 0.2
                lower_wick = min(o_px, c_px) - l_px
                wick_ratio = lower_wick / rng
                rejection_ok = (wick_ratio >= 0.45 and c_px >= o_px) or (lower_wick / rng >= 0.5)

                if trend_ok and retest_ok and rejection_ok:
                    num_orders = np.random.randint(5, 11)
                    scale = max(1.0, balance / 50.0)
                    total_lots = round(float(np.random.uniform(0.1, 0.25)) * min(scale, 10.0) * num_orders, 2)
                    sl_px = round(l_px - 0.15, 2)
                    active_stack = {
                        "direction": "BUY",
                        "entry_price": c_px,
                        "sl_price": sl_px,
                        "lots": total_lots,
                        "entry_idx": idx,
                    }
                    active_5m_breakout = None

            elif b_type == "DOWN":
                trend_ok = c_px < ema20 < ema50
                retest_ok = h_px >= lvl - 1.2 and l_px <= lvl + 0.2
                upper_wick = h_px - max(o_px, c_px)
                wick_ratio = upper_wick / rng
                rejection_ok = (wick_ratio >= 0.45 and c_px <= o_px) or (upper_wick / rng >= 0.5)

                if trend_ok and retest_ok and rejection_ok:
                    num_orders = np.random.randint(5, 11)
                    scale = max(1.0, balance / 50.0)
                    total_lots = round(float(np.random.uniform(0.1, 0.25)) * min(scale, 10.0) * num_orders, 2)
                    sl_px = round(h_px + 0.15, 2)
                    active_stack = {
                        "direction": "SELL",
                        "entry_price": c_px,
                        "sl_price": sl_px,
                        "lots": total_lots,
                        "entry_idx": idx,
                    }
                    active_5m_breakout = None

print(f"Total trades taken: {len(trades)}")
for t in trades:
    print(f"Trade: {t['type']} | PnL: {t['pnl']:+.2f} | Balance: ${t['balance']:.2f} | Reason: {t['reason']}")
print(f"Final Balance: ${balance:.2f}")
