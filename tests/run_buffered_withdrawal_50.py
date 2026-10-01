"""
tests/run_buffered_withdrawal_50.py
===================================
Real-World Laboratory Backtest:
- $50 Starting Capital
- Full Real-World Friction ($0.30 Gold spread + $0.10 slippage)
- Strict Causality (Shifted 5m breakout, zero lookahead)
- $200 Safety Buffer + 90% Daily Profit Withdrawal

Outputs the exact day-by-day cash extracted to your bank account vs left equity.
"""
from __future__ import annotations

from pathlib import Path
from tests.real_lab_backtest_50 import precompute_causal_day, compute_real_lot_size


def run_buffered_test(max_days: int = 60, buffer_equity: float = 200.0):
    SPREAD = 0.30
    SLIPPAGE = 0.10
    TOTAL_FRICTION = SPREAD + SLIPPAGE

    files = sorted(Path("data/candles").glob("gold_m1_*.csv"))[:max_days]
    print(f"Loading {len(files)} trading days for Buffered Withdrawal Backtest...")
    days_data = [precompute_causal_day(f) for f in files if precompute_causal_day(f)]

    balance = 50.0
    total_withdrawn_cash = 0.0
    trades = []
    active_pos = None
    last_sig_time = 0.0
    daily_log = []

    for day_idx, d in enumerate(days_data, 1):
        day_start_bal = balance
        trades_before = len(trades)
        date = d["date"]
        n = d["n"]
        o, h, l, c, t = d["o"], d["h"], d["l"], d["c"], d["t"]
        hour, atr_arr = d["hour"], d["atr"]
        ema20, ema50 = d["ema20"], d["ema50"]
        bo_res, bo_sup, bo_up, bo_down = d["bo_res"], d["bo_sup"], d["bo_up"], d["bo_down"]
        avail_t = d["avail_t"]
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
                active_bo_time = avail_t[i]
            elif bo_down[i]:
                active_bo_type = "DOWN"
                active_bo_lvl = bo_sup[i]
                active_bo_time = avail_t[i]

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
                        raw_pts = (active_pos["tp1_price"] - entry_px) if is_buy else (entry_px - active_pos["tp1_price"])
                        net_pts = raw_pts - TOTAL_FRICTION
                        realized_pnl = net_pts * close_vol * 100.0
                        balance += realized_pnl
                        active_pos["realized_pnl"] += realized_pnl
                        active_pos["volume"] = round(active_pos["volume"] - close_vol, 2)
                        active_pos["sl_price"] = round(entry_px + 0.10, 2) if is_buy else round(entry_px - 0.10, 2)

                # Hard Dollar Risk Stop Cap (matches live production: -$15 or 15% tier stop)
                tier_mult = max(1.0, balance / 100.0)
                risk_stop_usd = max(15.0, min(15.0 * tier_mult, 0.20 * balance))
                curr_pts = (curr_px - entry_px) if is_buy else (entry_px - curr_px)
                floating_pnl = (curr_pts * active_pos["volume"] * 100.0) + active_pos["realized_pnl"]

                if floating_pnl <= -risk_stop_usd:
                    final_pnl = -risk_stop_usd
                    balance += (final_pnl - active_pos["realized_pnl"])
                    trades.append({"win": False, "pnl": final_pnl})
                    active_pos = None

                # Normal Stop Loss Exit
                elif (lo_px <= active_pos["sl_price"]) if is_buy else (hi_px >= active_pos["sl_price"]):
                    exit_px = active_pos["sl_price"]
                    raw_pts = (exit_px - entry_px) if is_buy else (entry_px - exit_px)
                    net_pts = raw_pts - TOTAL_FRICTION
                    final_pnl = (net_pts * active_pos["volume"] * 100.0) + active_pos["realized_pnl"]
                    balance += (net_pts * active_pos["volume"] * 100.0)
                    trades.append({"win": final_pnl > 0, "pnl": final_pnl})
                    active_pos = None

                # Macro Spike Exit (+5.0 ATR)
                elif (hi_px >= active_pos["spike_target"]) if is_buy else (lo_px <= active_pos["spike_target"]):
                    exit_px = active_pos["spike_target"]
                    raw_pts = (exit_px - entry_px) if is_buy else (entry_px - exit_px)
                    net_pts = raw_pts - TOTAL_FRICTION
                    final_pnl = (net_pts * active_pos["volume"] * 100.0) + active_pos["realized_pnl"]
                    balance += (net_pts * active_pos["volume"] * 100.0)
                    trades.append({"win": True, "pnl": final_pnl})
                    active_pos = None

            elif (curr_t - last_sig_time) >= 180.0:
                sig_dir = None
                sig_strat = None

                # NY AM Silver Bullet
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

                # Turtle Soup
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

                # 5m Breakout Retest
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

                if sig_dir and balance >= 15.0:
                    lot_vol = compute_real_lot_size(balance)
                    is_buy = sig_dir == "BUY"
                    actual_entry = curr_px + (SPREAD / 2.0) if is_buy else curr_px - (SPREAD / 2.0)
                    sl = round(lo_px - (0.8 * curr_atr), 2) if is_buy else round(hi_px + (0.8 * curr_atr), 2)
                    tp1 = round(curr_px + (2.5 * curr_atr), 2) if is_buy else round(curr_px - (2.5 * curr_atr), 2)
                    spike = round(curr_px + (5.0 * curr_atr), 2) if is_buy else round(curr_px - (5.0 * curr_atr), 2)

                    active_pos = {
                        "direction": sig_dir,
                        "strategy": sig_strat,
                        "entry_price": actual_entry,
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

        day_trades = trades[trades_before:]
        day_wins = [t for t in day_trades if t["win"]]
        day_pnl = balance - day_start_bal

        # BUFFERED WITHDRAWAL LOGIC:
        # Keep a $200 drawdown cushion in the broker so red days never liquidate the account.
        # Once balance > buffer_equity, withdraw 90% of profit above the buffer into cold storage!
        withdrawn_today = 0.0
        if balance > buffer_equity and day_pnl > 0:
            excess = balance - buffer_equity
            withdrawn_today = round(min(day_pnl * 0.90, excess), 2)
            total_withdrawn_cash += withdrawn_today
            balance = round(balance - withdrawn_today, 2)

        wr = (len(day_wins) / len(day_trades) * 100.0) if len(day_trades) > 0 else 0.0

        daily_log.append({
            "day": day_idx,
            "date": date,
            "start": round(day_start_bal, 2),
            "pnl": round(day_pnl, 2),
            "withdrawn": withdrawn_today,
            "cum_withdrawn": round(total_withdrawn_cash, 2),
            "left_equity": round(balance, 2),
            "trades": len(day_trades),
            "wr": round(wr, 1),
        })

    print("\n" + "=" * 105)
    print("      REAL LAB: $50 START | $0.40 SPREAD/SLIPPAGE | BUFFERED 90% DAILY CASH WITHDRAWAL")
    print("=" * 105)
    print(f"{'Day':<4} | {'Date':<10} | {'Start Bal':<10} | {'Day PnL':<11} | {'90% Cash Out':<14} | {'Total Banked':<14} | {'Left Equity':<12} | {'Tr':<3} | {'WR %':<5}")
    print("-" * 105)
    for r in daily_log:
        pnl_val = r["pnl"]
        pnl_s = f"+${pnl_val:,.2f}" if pnl_val >= 0 else f"-${abs(pnl_val):,.2f}"
        w_s = f"${r['withdrawn']:,.2f}"
        cum_s = f"${r['cum_withdrawn']:,.2f}"
        print(f"{r['day']:<4} | {r['date']:<10} | ${r['start']:<9,.2f} | {pnl_s:<11} | {w_s:<14} | {cum_s:<14} | ${r['left_equity']:<11,.2f} | {r['trades']:<3} | {r['wr']:<4.1f}%")
    print("=" * 105)
    print(f"Total Cold-Storage Cash Withdrawn: ${total_withdrawn_cash:,.2f}")
    print(f"Remaining Broker Trading Balance:   ${balance:,.2f}")
    print(f"Net Realized Wealth:               ${total_withdrawn_cash + balance:,.2f}")


if __name__ == "__main__":
    run_buffered_test(max_days=60, buffer_equity=200.0)
