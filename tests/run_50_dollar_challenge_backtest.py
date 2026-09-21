"""
tests/run_50_dollar_challenge_backtest.py
=========================================
Runs the $50 Starting Capital Challenge across 60 days of historical M1 Gold data.
Outputs exact day-by-day account balances, daily net profits, win rates, and compounding trajectory.
"""
from __future__ import annotations

import json
from pathlib import Path
from tests.experiment_apex_v2 import precompute_day_data, compute_lot_size


def run_challenge_50(max_days: int = 60, max_lot_cap: float = 5.0):
    files = sorted(Path("data/candles").glob("gold_m1_*.csv"))[:max_days]
    print(f"Loading and precomputing {len(files)} trading days for the $50 Challenge...")
    days_data = [precompute_day_data(f) for f in files if precompute_day_data(f)]

    balance = 50.0  # Starting strictly at $50.00
    peak_equity = balance
    max_dd = 0.0
    daily_records = []
    trades = []
    active_pos = None
    last_sig_time = 0.0

    for day_idx, d in enumerate(days_data, 1):
        day_start_bal = balance
        trades_before = len(trades)
        date = d["date"]
        n = d["n"]
        o, h, l, c, t = d["o"], d["h"], d["l"], d["c"], d["t"]
        hour, atr_arr = d["hour"], d["atr"]
        ema20, ema50 = d["ema20"], d["ema50"]
        bo_res, bo_sup, bo_up, bo_down = d["bo_res"], d["bo_sup"], d["bo_up"], d["bo_down"]
        bar5m_t = d["bar5m_time"]
        asia_hi, asia_lo = d["asia_hi"], d["asia_lo"]

        active_bo_type = None
        active_bo_lvl = 0.0
        active_bo_time = 0.0

        for i in range(2, n):
            curr_px = c[i]
            hi_px = h[i]
            lo_px = l[i]
            op_px = o[i]
            curr_t = t[i]
            hr = hour[i]
            curr_atr = atr_arr[i]

            if bo_up[i]:
                active_bo_type = "UP"
                active_bo_lvl = bo_res[i]
                active_bo_time = bar5m_t[i]
            elif bo_down[i]:
                active_bo_type = "DOWN"
                active_bo_lvl = bo_sup[i]
                active_bo_time = bar5m_t[i]

            if active_pos:
                is_buy = active_pos["direction"] == "BUY"
                entry_px = active_pos["entry_price"]
                atr_entry = active_pos["atr_entry"]
                profit_pts = (curr_px - entry_px) if is_buy else (entry_px - curr_px)

                # Stage 1 Risk-Free Pyramid (+1.5 ATR)
                if active_pos["pyramid_count"] == 0 and profit_pts >= (1.5 * atr_entry):
                    add_vol = round(active_pos["initial_vol"] * 0.50, 2)
                    active_pos["volume"] += add_vol
                    active_pos["pyramid_count"] = 1
                    active_pos["sl_price"] = round(entry_px + (0.5 * atr_entry), 2) if is_buy else round(entry_px - (0.5 * atr_entry), 2)

                # 60/40 Scale Out (+2.5 ATR)
                if not active_pos["scaled_out_60"]:
                    hit_tp1 = (curr_px >= active_pos["tp1_price"]) if is_buy else (curr_px <= active_pos["tp1_price"])
                    if hit_tp1:
                        active_pos["scaled_out_60"] = True
                        close_vol = round(active_pos["volume"] * 0.60, 2)
                        active_pos["scaled_out_vol"] = close_vol
                        pts = (active_pos["tp1_price"] - entry_px) if is_buy else (entry_px - active_pos["tp1_price"])
                        realized_pnl = pts * close_vol * 100.0
                        balance += realized_pnl
                        active_pos["realized_pnl"] += realized_pnl
                        active_pos["volume"] = round(active_pos["volume"] - close_vol, 2)
                        active_pos["sl_price"] = round(entry_px + 0.10, 2) if is_buy else round(entry_px - 0.10, 2)

                # Stop Loss Exit
                hit_sl = (lo_px <= active_pos["sl_price"]) if is_buy else (hi_px >= active_pos["sl_price"])
                if hit_sl:
                    exit_px = active_pos["sl_price"]
                    pts = (exit_px - entry_px) if is_buy else (entry_px - exit_px)
                    final_pnl = (pts * active_pos["volume"] * 100.0) + active_pos["realized_pnl"]
                    balance += (pts * active_pos["volume"] * 100.0)
                    trades.append({"win": final_pnl > 0, "pnl": final_pnl})
                    active_pos = None

                # Macro Spike Exit (+5.0 ATR)
                elif (hi_px >= active_pos["spike_target"]) if is_buy else (lo_px <= active_pos["spike_target"]):
                    exit_px = active_pos["spike_target"]
                    pts = (exit_px - entry_px) if is_buy else (entry_px - exit_px)
                    final_pnl = (pts * active_pos["volume"] * 100.0) + active_pos["realized_pnl"]
                    balance += (pts * active_pos["volume"] * 100.0)
                    trades.append({"win": True, "pnl": final_pnl})
                    active_pos = None

            elif (curr_t - last_sig_time) >= 180.0:
                sig_dir = None
                sig_strat = None

                # 1. NY AM Silver Bullet (14:00 - 15:00 UTC)
                if 14 <= hr < 15:
                    if lo_px > h[i - 2] and (lo_px - h[i - 2]) >= 0.70:
                        ce = (lo_px + h[i - 2]) / 2.0
                        if lo_px <= ce + (0.3 * curr_atr) and curr_px >= ce - 0.20:
                            sig_dir = "BUY"
                            sig_strat = "SILVER_BULLET"
                    elif hi_px < l[i - 2] and (l[i - 2] - hi_px) >= 0.70:
                        ce = (hi_px + l[i - 2]) / 2.0
                        if hi_px >= ce - (0.3 * curr_atr) and curr_px <= ce + 0.20:
                            sig_dir = "SELL"
                            sig_strat = "SILVER_BULLET"

                # 2. Turtle Soup (06:00 - 09:00 UTC)
                if not sig_dir and (6 <= hr < 9) and asia_hi > 0 and asia_lo > 0:
                    rng = max(0.20, hi_px - lo_px)
                    if lo_px < asia_lo and curr_px > asia_lo:
                        if ((min(op_px, curr_px) - lo_px) / rng) >= 0.50:
                            sig_dir = "BUY"
                            sig_strat = "TURTLE_SOUP"
                    elif hi_px > asia_hi and curr_px < asia_hi:
                        if ((hi_px - max(op_px, curr_px)) / rng) >= 0.50:
                            sig_dir = "SELL"
                            sig_strat = "TURTLE_SOUP"

                # 3. 5m Breakout Retest
                if not sig_dir and active_bo_type and 0 < (curr_t - active_bo_time) <= 1200.0:
                    lvl = active_bo_lvl
                    rng = max(0.20, hi_px - lo_px)
                    if active_bo_type == "UP":
                        trend_ok = curr_px > ema20[i] > ema50[i]
                        retest_ok = lo_px <= lvl + 1.20 and hi_px >= lvl - 0.20
                        wick = min(op_px, curr_px) - lo_px
                        if trend_ok and retest_ok and ((wick / rng) >= 0.45) and curr_px >= op_px:
                            sig_dir = "BUY"
                            sig_strat = "BREAKOUT_RETEST"
                            active_bo_type = None
                    elif active_bo_type == "DOWN":
                        trend_ok = curr_px < ema20[i] < ema50[i]
                        retest_ok = hi_px >= lvl - 1.20 and lo_px <= lvl + 0.20
                        wick = hi_px - max(op_px, curr_px)
                        if trend_ok and retest_ok and ((wick / rng) >= 0.45) and curr_px <= op_px:
                            sig_dir = "SELL"
                            sig_strat = "BREAKOUT_RETEST"
                            active_bo_type = None

                if sig_dir:
                    base_vol = compute_lot_size(balance, max_lot_cap=max_lot_cap)
                    lot_vol = round(base_vol * 1.50, 2)
                    is_buy = sig_dir == "BUY"
                    sl = round(lo_px - (0.8 * curr_atr), 2) if is_buy else round(hi_px + (0.8 * curr_atr), 2)
                    tp1 = round(curr_px + (2.5 * curr_atr), 2) if is_buy else round(curr_px - (2.5 * curr_atr), 2)
                    spike = round(curr_px + (5.0 * curr_atr), 2) if is_buy else round(curr_px - (5.0 * curr_atr), 2)

                    active_pos = {
                        "direction": sig_dir,
                        "strategy": sig_strat,
                        "entry_price": curr_px,
                        "volume": lot_vol,
                        "initial_vol": lot_vol,
                        "scaled_out_vol": 0.0,
                        "sl_price": sl,
                        "tp1_price": tp1,
                        "spike_target": spike,
                        "atr_entry": curr_atr,
                        "pyramid_count": 0,
                        "scaled_out_60": False,
                        "realized_pnl": 0.0,
                    }
                    last_sig_time = curr_t

            peak_equity = max(peak_equity, balance)
            dd = (peak_equity - balance) / peak_equity * 100.0 if peak_equity > 0 else 0.0
            max_dd = max(max_dd, dd)

        day_trades = trades[trades_before:]
        day_wins = [t for t in day_trades if t["win"]]
        day_pnl = balance - day_start_bal
        ret_pct = (day_pnl / day_start_bal * 100.0) if day_start_bal > 0 else 0.0
        wr = (len(day_wins) / len(day_trades) * 100.0) if len(day_trades) > 0 else 0.0

        daily_records.append({
            "day": day_idx,
            "date": date,
            "start": round(day_start_bal, 2),
            "end": round(balance, 2),
            "pnl": round(day_pnl, 2),
            "ret_pct": round(ret_pct, 1),
            "trades": len(day_trades),
            "wr": round(wr, 1),
        })

    # Output Day-by-Day Table
    print("\n" + "=" * 95)
    print("                 THE $50.00 STARTING CAPITAL CHALLENGE: FULL 60-DAY BREAKDOWN")
    print("=" * 95)
    print(f"{'Day':<4} | {'Date':<10} | {'Start Bal':<12} | {'End Bal':<14} | {'Daily PnL':<13} | {'Return %':<10} | {'Trades':<6} | {'WR %':<6}")
    print("-" * 95)
    for r in daily_records:
        pnl_s = f"+${r['pnl']:,.2f}" if r["pnl"] >= 0 else f"-${abs(r['pnl']):,.2f}"
        ret_s = f"+{r['ret_pct']:.1f}%" if r["ret_pct"] >= 0 else f"{r['ret_pct']:.1f}%"
        print(f"{r['day']:<4} | {r['date']:<10} | ${r['start']:<11,.2f} | ${r['end']:<13,.2f} | {pnl_s:<13} | {ret_s:<10} | {r['trades']:<6} | {r['wr']:<5.1f}%")
    print("=" * 95)
    print(f"Final Balance from $50.00: ${balance:,.2f} | Total Trades: {len(trades)} | Max Drawdown: {max_dd:.2f}%")


if __name__ == "__main__":
    run_challenge_50(max_days=60, max_lot_cap=5.0)
