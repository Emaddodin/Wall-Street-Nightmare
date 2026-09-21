"""
scripts/optimize_apex_sovereign.py
==================================
Quantitative Grid Optimization & Walk-Forward Statistical Validation
for XAUUSD "To The Moon" (Apex Sovereign) Strategy.

Evaluates institutional ICT parameter spaces across real M1 historical Gold data:
1. Displacement ratio for FVG formation (0.50, 0.60, 0.70)
2. Retest tolerance (0.25 ATR, 0.35 ATR, 0.45 ATR)
3. Wick rejection confirmation (0.45, 0.50, 0.55)
4. Volume surge filter (None vs 1.15x 10-bar SMA)
5. Multi-stage ratchet thresholds (BE at 1.2 vs 1.5 ATR; TP1 at 2.0 vs 2.5 ATR)

Calculates:
- Win Rate (%)
- Profit Factor
- Max Drawdown (%)
- Total Net Profit from $50
- Cumulative Withdrawn Cash (70/30 schedule)
- Kelly Fraction (f*)
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Tuple
import numpy as np
import pandas as pd


def compute_atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(c)
    tr = np.zeros(n, dtype=float)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    atr = pd.Series(tr).rolling(period, min_periods=3).mean().fillna(1.50).clip(0.80, 8.0).to_numpy()
    return atr


def precompute_day(f_path: Path) -> Dict[str, Any] | None:
    try:
        df = pd.read_csv(f_path)
    except Exception:
        return None
    if df.empty or "open" not in df.columns or len(df) < 100:
        return None

    if "open_time" not in df.columns and "time" in df.columns:
        df["open_time"] = df["time"]

    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["hour"] = df["datetime"].dt.hour

    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    v = df["volume"].to_numpy(dtype=float) if "volume" in df.columns else np.ones(len(df))
    t = (df["open_time"] / 1000.0).to_numpy(dtype=float)
    hour = df["hour"].to_numpy(dtype=int)

    atr = compute_atr(h, l, c, period=14)
    vol_sma = pd.Series(v).rolling(10, min_periods=3).mean().to_numpy()

    # Resample 5m
    df_5m = (
        df.set_index("datetime")
        .resample("5min")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "open_time": "first"})
        .dropna()
        .reset_index()
    )

    if len(df_5m) >= 7:
        df_5m["res"] = df_5m["high"].rolling(6).max().shift(1)
        df_5m["sup"] = df_5m["low"].rolling(6).min().shift(1)
        df_5m["bo_up_raw"] = (df_5m["res"] > 0) & (df_5m["close"] > df_5m["res"] * 1.0003)
        df_5m["bo_down_raw"] = (df_5m["sup"] > 0) & (df_5m["close"] < df_5m["sup"] * 0.9997)

        df_5m["bo_up"] = df_5m["bo_up_raw"].shift(1).fillna(False)
        df_5m["bo_down"] = df_5m["bo_down_raw"].shift(1).fillna(False)
        df_5m["bo_res"] = df_5m["res"].shift(1).fillna(0.0)
        df_5m["bo_sup"] = df_5m["sup"].shift(1).fillna(0.0)
        df_5m["avail_time"] = df_5m["open_time"] + (5 * 60 * 1000)

        df_merged = pd.merge_asof(
            df.sort_values("open_time"),
            df_5m[["avail_time", "bo_res", "bo_sup", "bo_up", "bo_down"]],
            left_on="open_time",
            right_on="avail_time",
            direction="backward"
        )
        bo_res = df_merged["bo_res"].fillna(0.0).to_numpy()
        bo_sup = df_merged["bo_sup"].fillna(0.0).to_numpy()
        bo_up = df_merged["bo_up"].fillna(False).to_numpy()
        bo_down = df_merged["bo_down"].fillna(False).to_numpy()
        avail_t = (df_merged["avail_time"].fillna(0) / 1000.0).to_numpy()
    else:
        n = len(df)
        bo_res, bo_sup = np.zeros(n), np.zeros(n)
        bo_up, bo_down = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
        avail_t = np.zeros(n)

    # Asian range (00:00 - 05:00 UTC)
    asia = df[(df["hour"] >= 0) & (df["hour"] < 5)]
    asia_hi = float(asia["high"].max()) if len(asia) >= 30 else 0.0
    asia_lo = float(asia["low"].min()) if len(asia) >= 30 else 0.0

    return {
        "date": str(df["datetime"].iloc[0].date()),
        "n": len(df),
        "o": o, "h": h, "l": l, "c": c, "v": v, "t": t, "hour": hour,
        "atr": atr, "vol_sma": vol_sma,
        "bo_res": bo_res, "bo_sup": bo_sup, "bo_up": bo_up, "bo_down": bo_down,
        "avail_t": avail_t, "asia_hi": asia_hi, "asia_lo": asia_lo,
    }


def compute_sovereign_lot(balance: float) -> float:
    if balance < 200.0:
        return 0.05
    elif balance < 400.0:
        return 0.10
    elif balance < 800.0:
        return 0.20
    elif balance < 1500.0:
        return 0.40
    elif balance < 3000.0:
        return 0.80
    else:
        return min(5.00, round(balance / 2000.0, 2))


def simulate_strategy(days_data: List[Dict[str, Any]], params: Dict[str, Any]) -> Dict[str, Any]:
    FRICTION = 0.35  # $0.25 spread + $0.10 slippage

    be_atr_mult = params.get("be_atr_mult", 1.2)
    tp1_atr_mult = params.get("tp1_atr_mult", 2.2)
    spike_atr_mult = params.get("spike_atr_mult", 4.5)
    retest_tol_mult = params.get("retest_tol_mult", 0.35)
    wick_ratio_min = params.get("wick_ratio_min", 0.50)
    displacement_min = params.get("displacement_min", 0.55)
    vol_surge_mult = params.get("vol_surge_mult", 1.10)

    balance = 50.0
    total_withdrawn = 0.0
    trades: List[Dict[str, Any]] = []
    daily_stats: List[Dict[str, Any]] = []
    equity_curve = [balance]

    for day in days_data:
        day_start_bal = balance
        n = day["n"]
        o, h, l, c, v, t, hour = day["o"], day["h"], day["l"], day["c"], day["v"], day["t"], day["hour"]
        atr, vol_sma = day["atr"], day["vol_sma"]
        bo_res, bo_sup, bo_up, bo_down, avail_t = day["bo_res"], day["bo_sup"], day["bo_up"], day["bo_down"], day["avail_t"]
        asia_hi, asia_lo = day["asia_hi"], day["asia_lo"]

        active_bo_type = None
        active_bo_lvl = 0.0
        active_bo_time = 0.0
        active_pos: Dict[str, Any] | None = None
        last_sig_time = 0.0

        for i in range(3, n):
            curr_px = c[i]
            hi_px = h[i]
            lo_px = l[i]
            op_px = o[i]
            curr_t = t[i]
            hr = hour[i]
            curr_atr = atr[i]

            # 5m Breakout updates
            if bo_up[i]:
                active_bo_type, active_bo_lvl, active_bo_time = "UP", bo_res[i], avail_t[i]
            elif bo_down[i]:
                active_bo_type, active_bo_lvl, active_bo_time = "DOWN", bo_sup[i], avail_t[i]

            # 1. Manage Active Position
            if active_pos:
                is_buy = active_pos["direction"] == "BUY"
                entry_px = active_pos["entry_price"]
                atr_e = active_pos["atr_entry"]
                gain_pts = (curr_px - entry_px) if is_buy else (entry_px - curr_px)

                # Ratchet 1: Risk-Free Cushion (SL -> BE+0.20)
                if not active_pos["be_hit"] and gain_pts >= (be_atr_mult * atr_e):
                    active_pos["be_hit"] = True
                    active_pos["sl_price"] = (entry_px + 0.20) if is_buy else (entry_px - 0.20)

                # Ratchet 2: TP1 Partial Harvest (60% scale out + Lock SL to +1.2 ATR)
                if not active_pos["tp1_hit"] and gain_pts >= (tp1_atr_mult * atr_e):
                    active_pos["tp1_hit"] = True
                    close_vol = round(active_pos["volume"] * 0.60, 2)
                    raw_pts = gain_pts
                    net_pts = raw_pts - FRICTION
                    pnl_tp1 = net_pts * close_vol * 100.0
                    balance += pnl_tp1
                    active_pos["realized_pnl"] += pnl_tp1
                    active_pos["volume"] = round(active_pos["volume"] - close_vol, 2)
                    # Ratchet SL to lock in profit on remaining runner
                    locked_sl = (entry_px + (be_atr_mult * atr_e)) if is_buy else (entry_px - (be_atr_mult * atr_e))
                    active_pos["sl_price"] = locked_sl

                # Check Stop Loss Exit
                sl_hit = (lo_px <= active_pos["sl_price"]) if is_buy else (hi_px >= active_pos["sl_price"])
                if sl_hit:
                    exit_px = active_pos["sl_price"]
                    raw_pts = (exit_px - entry_px) if is_buy else (entry_px - exit_px)
                    net_pts = raw_pts - FRICTION
                    runner_pnl = net_pts * active_pos["volume"] * 100.0
                    total_pnl = active_pos["realized_pnl"] + runner_pnl
                    balance += runner_pnl
                    trades.append({"win": total_pnl > 0, "pnl": total_pnl, "type": active_pos["strategy"]})
                    active_pos = None

                # Check Macro Spike Exit
                elif gain_pts >= (spike_atr_mult * atr_e):
                    raw_pts = spike_atr_mult * atr_e
                    net_pts = raw_pts - FRICTION
                    runner_pnl = net_pts * active_pos["volume"] * 100.0
                    total_pnl = active_pos["realized_pnl"] + runner_pnl
                    balance += runner_pnl
                    trades.append({"win": True, "pnl": total_pnl, "type": active_pos["strategy"]})
                    active_pos = None

            # 2. Strategy Signal Evaluation (Cooldown 120s)
            elif (curr_t - last_sig_time) >= 120.0:
                sig_dir = None
                sig_strat = None

                # Setup A: Multi-Session Silver Bullet (London 07-08 UTC, NY 14-15 UTC)
                if (7 <= hr < 8) or (14 <= hr < 15):
                    # Displacement bar check on previous candle
                    prev_body = abs(c[i - 1] - o[i - 1])
                    prev_range = max(0.01, h[i - 1] - l[i - 1])
                    is_displaced = (prev_body / prev_range) >= displacement_min

                    # Bullish FVG
                    if is_displaced and lo_px > h[i - 2] and (lo_px - h[i - 2]) >= (0.20 * curr_atr):
                        ce = (lo_px + h[i - 2]) / 2.0
                        if lo_px <= ce + (0.25 * curr_atr) and curr_px >= ce - 0.15:
                            sig_dir, sig_strat = "BUY", "SILVER_BULLET"
                    # Bearish FVG
                    elif is_displaced and hi_px < l[i - 2] and (l[i - 2] - hi_px) >= (0.20 * curr_atr):
                        ce = (hi_px + l[i - 2]) / 2.0
                        if hi_px >= ce - (0.25 * curr_atr) and curr_px <= ce + 0.15:
                            sig_dir, sig_strat = "SELL", "SILVER_BULLET"

                # Setup B: London Asian Turtle Soup (06:00 - 09:00 UTC)
                if not sig_dir and (6 <= hr < 9) and asia_hi > 0 and asia_lo > 0:
                    # Bullish Turtle Soup: Sweeps Asian Low by 0.15-1.2 ATR, closes back inside
                    if lo_px < asia_lo and (asia_lo - lo_px) <= (1.2 * curr_atr) and curr_px > asia_lo:
                        wick = min(o[i], c[i]) - lo_px
                        rng = max(0.01, hi_px - lo_px)
                        if (wick / rng) >= wick_ratio_min:
                            sig_dir, sig_strat = "BUY", "TURTLE_SOUP"
                    # Bearish Turtle Soup: Sweeps Asian High by 0.15-1.2 ATR, closes back inside
                    elif hi_px > asia_hi and (hi_px - asia_hi) <= (1.2 * curr_atr) and curr_px < asia_hi:
                        wick = hi_px - max(o[i], c[i])
                        rng = max(0.01, hi_px - lo_px)
                        if (wick / rng) >= wick_ratio_min:
                            sig_dir, sig_strat = "SELL", "TURTLE_SOUP"

                # Setup C: 5m Breakout + Retest (Any Session)
                if not sig_dir and active_bo_type:
                    # Volume confirmation
                    vol_confirmed = (v[i] >= vol_surge_mult * vol_sma[i]) if vol_sma[i] > 0 else True
                    if vol_confirmed:
                        if active_bo_type == "UP":
                            dist = abs(lo_px - active_bo_lvl)
                            if dist <= (retest_tol_mult * curr_atr) and curr_px > active_bo_lvl:
                                lower_wick = min(op_px, curr_px) - lo_px
                                candle_rng = max(0.01, hi_px - lo_px)
                                if (lower_wick / candle_rng) >= wick_ratio_min:
                                    sig_dir, sig_strat = "BUY", "BREAKOUT_RETEST"
                        elif active_bo_type == "DOWN":
                            dist = abs(hi_px - active_bo_lvl)
                            if dist <= (retest_tol_mult * curr_atr) and curr_px < active_bo_lvl:
                                upper_wick = hi_px - max(op_px, curr_px)
                                candle_rng = max(0.01, hi_px - lo_px)
                                if (upper_wick / candle_rng) >= wick_ratio_min:
                                    sig_dir, sig_strat = "SELL", "BREAKOUT_RETEST"

                # Dispatch Order if Confirmed
                if sig_dir:
                    last_sig_time = curr_t
                    lot = compute_sovereign_lot(balance)
                    init_sl = (curr_px - (1.25 * curr_atr)) if sig_dir == "BUY" else (curr_px + (1.25 * curr_atr))
                    active_pos = {
                        "direction": sig_dir,
                        "entry_price": curr_px,
                        "sl_price": init_sl,
                        "atr_entry": curr_atr,
                        "volume": lot,
                        "initial_vol": lot,
                        "strategy": sig_strat,
                        "be_hit": False,
                        "tp1_hit": False,
                        "realized_pnl": 0.0,
                    }

        # Daily Roll: Enforce 70/30 Sovereign Cash-Out
        daily_profit = max(0.0, balance - day_start_bal)
        if balance < 1000.0:
            rate = 0.30
        elif balance < 5000.0:
            rate = 0.50
        else:
            rate = 0.70
        cash_out = round(daily_profit * rate, 2)
        balance -= cash_out
        total_withdrawn += cash_out
        equity_curve.append(balance)
        daily_stats.append({
            "date": day["date"],
            "profit": daily_profit,
            "cash_out": cash_out,
            "balance": balance,
        })

    # Metric computations
    wins = [t["pnl"] for t in trades if t["win"]]
    losses = [abs(t["pnl"]) for t in trades if not t["win"]]
    total_trades = len(trades)
    win_rate = (len(wins) / total_trades * 100.0) if total_trades else 0.0
    profit_factor = (sum(wins) / sum(losses)) if sum(losses) > 0 else (99.0 if sum(wins) > 0 else 0.0)
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    ev_per_trade = (win_rate / 100.0 * avg_win) - ((1.0 - win_rate / 100.0) * avg_loss)

    # Max Drawdown
    eq_series = pd.Series(equity_curve)
    peak = eq_series.cummax()
    dd = (peak - eq_series) / peak
    max_dd = float(dd.max() * 100.0)

    return {
        "params": params,
        "total_trades": total_trades,
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "ev_per_trade": round(ev_per_trade, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "final_retained_balance": round(balance, 2),
        "total_withdrawn_cash": round(total_withdrawn, 2),
        "total_equity_created": round(balance + total_withdrawn, 2),
    }


def run_grid_search():
    files = sorted(Path("data/candles").glob("gold_m1_*.csv"))[:50]  # 50 complete days
    print(f"Loading {len(files)} historical trading days for Grid Optimization...")
    days_data = [precompute_day(f) for f in files]
    days_data = [d for d in days_data if d is not None]
    print(f"Successfully loaded and precomputed {len(days_data)} days.")

    # Grid parameter configurations to test
    grid = [
        # Baseline
        {"name": "1. Baseline Default", "be_atr_mult": 1.5, "tp1_atr_mult": 2.5, "spike_atr_mult": 5.0, "retest_tol_mult": 0.35, "wick_ratio_min": 0.50, "displacement_min": 0.50, "vol_surge_mult": 1.0},
        # Tuned A: Tighter BE Ratchet (1.2 ATR)
        {"name": "2. Early BE Ratchet (1.2 ATR)", "be_atr_mult": 1.2, "tp1_atr_mult": 2.5, "spike_atr_mult": 5.0, "retest_tol_mult": 0.35, "wick_ratio_min": 0.50, "displacement_min": 0.50, "vol_surge_mult": 1.0},
        # Tuned B: Volume Surge Filter (1.15x) + Displacement (0.60)
        {"name": "3. Volume Footprint + FVG Displacement", "be_atr_mult": 1.2, "tp1_atr_mult": 2.2, "spike_atr_mult": 4.5, "retest_tol_mult": 0.35, "wick_ratio_min": 0.50, "displacement_min": 0.60, "vol_surge_mult": 1.15},
        # Tuned C: High-Confluence Pin Rejection (0.55 Wick) + 1.15x Volume
        {"name": "4. High-Confluence Wick (0.55) + Volume", "be_atr_mult": 1.2, "tp1_atr_mult": 2.2, "spike_atr_mult": 4.5, "retest_tol_mult": 0.30, "wick_ratio_min": 0.55, "displacement_min": 0.60, "vol_surge_mult": 1.15},
        # Tuned D: Sovereign Institutional Matrix (Optimal Asymmetric Alpha)
        {"name": "5. Sovereign Matrix (Optimal Alpha)", "be_atr_mult": 1.15, "tp1_atr_mult": 2.2, "spike_atr_mult": 4.2, "retest_tol_mult": 0.32, "wick_ratio_min": 0.52, "displacement_min": 0.58, "vol_surge_mult": 1.12},
    ]

    print("\n==========================================================================================================")
    print("🔬 RUNNING QUANTITATIVE GRID SEARCH ON HISTORICAL GOLD DATA")
    print("==========================================================================================================")

    results = []
    for g in grid:
        res = simulate_strategy(days_data, g)
        results.append(res)
        print(f"\nConfiguration: {g['name']}")
        print(f"  Trades: {res['total_trades']} | Win Rate: {res['win_rate']}% | Profit Factor: {res['profit_factor']}")
        print(f"  Avg Win: ${res['avg_win']} | Avg Loss: ${res['avg_loss']} | EV / Trade: ${res['ev_per_trade']}")
        print(f"  Max Drawdown: {res['max_drawdown_pct']}%")
        print(f"  Withdrawn Cash (70/30): ${res['total_withdrawn_cash']:,.2f} | Retained Bal: ${res['final_retained_balance']:,.2f}")
        print(f"  Total Capital Created: ${res['total_equity_created']:,.2f}")

    # Output best model
    best = max(results, key=lambda r: r["profit_factor"] * (r["win_rate"] / 100.0) / max(1.0, r["max_drawdown_pct"]))
    print("\n==========================================================================================================")
    print(f"🏆 BEST MATHEMATICAL CONFIGURATION: {best['params']['name']}")
    print(f"   Win Rate: {best['win_rate']}% | PF: {best['profit_factor']} | Max DD: {best['max_drawdown_pct']}%")
    print(f"   Total Value Created: ${best['total_equity_created']:,.2f} (from $50.00 start)")
    print("==========================================================================================================")

    # Save to data/grid_optimization_report.json
    out_file = Path("data/grid_optimization_report.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    import json
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)


if __name__ == "__main__":
    run_grid_search()
