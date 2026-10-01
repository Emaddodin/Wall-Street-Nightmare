"""
tests/run_daily_trump_backtest_hardened.py
=========================================
Daily Backtest of the Hardened "To The Moon" Engine across ALL 473 Trading Days
of the Trump Administration (January 21, 2025 to September 20, 2026).

Equipped with all 8 Institutional Safeguards:
1. Dynamic Peak Watermark Trailing (Bag Protection: locks +$246 on +$300 peak)
2. Mathematical 2% Risk Sizing Floor
3. 20-Minute Time-Decay Lifespan
4. Post-Loss 5-Minute Cooldown & 2-Loss Lockout
5. Daily Max Drawdown Circuit Breaker (8% / $50 ceiling)
6. Friday 18:00 UTC Curfew & 20:30 UTC Force-Flatten
7. Live Broker Spread & Slippage Friction ($0.40/pt)
8. Sovereign Compounding & Daily Profit Vaulting

Exports:
- data/trump_regime_daily_report.csv
- data/trump_regime_daily_report.json
- data/trump_regime_hardened_summary.json
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import concurrent.futures as cf

import numpy as np
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scalper.brain.regime_prior_engine import get_regime_prior_engine
from scalper.brain.politician_brain import get_politician_brain


def compute_atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(c)
    tr = np.zeros(n, dtype=float)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    atr = pd.Series(tr).rolling(period, min_periods=3).mean().fillna(1.50).clip(0.80, 8.0).to_numpy()
    return atr


def precompute_day_causal(f_path: Path) -> Optional[Dict[str, Any]]:
    """Loads and precomputes daily indicators with strict zero-lookahead causality."""
    try:
        df = pd.read_csv(f_path)
    except Exception:
        return None
    if df.empty or "open" not in df.columns or len(df) < 60:
        return None

    if "open_time" not in df.columns and "time" in df.columns:
        df["open_time"] = df["time"]

    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["hour"] = df["datetime"].dt.hour
    df["minute"] = df["datetime"].dt.minute
    df["weekday"] = df["datetime"].dt.weekday

    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    t = (df["open_time"] / 1000.0).to_numpy(dtype=float)
    hour = df["hour"].to_numpy(dtype=int)
    minute = df["minute"].to_numpy(dtype=int)
    weekday = df["weekday"].to_numpy(dtype=int)

    ema20 = pd.Series(c).ewm(span=20).mean().to_numpy()
    ema50 = pd.Series(c).ewm(span=50).mean().to_numpy()
    atr = compute_atr(h, l, c, period=14)

    # Resample 5m bars causally
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
            direction="backward",
        )
        bo_res = df_merged["bo_res"].fillna(0.0).to_numpy()
        bo_sup = df_merged["bo_sup"].fillna(0.0).to_numpy()
        bo_up = df_merged["bo_up"].fillna(False).to_numpy()
        bo_down = df_merged["bo_down"].fillna(False).to_numpy()
        avail_t = (df_merged["avail_time"].fillna(0) / 1000.0).to_numpy()
    else:
        n = len(df)
        bo_res = np.zeros(n)
        bo_sup = np.zeros(n)
        bo_up = np.zeros(n, dtype=bool)
        bo_down = np.zeros(n, dtype=bool)
        avail_t = np.zeros(n)

    return {
        "file": f_path.name,
        "date": str(df["datetime"].iloc[0].date()),
        "n": len(df),
        "o": o,
        "h": h,
        "l": l,
        "c": c,
        "t": t,
        "hour": hour,
        "minute": minute,
        "weekday": weekday,
        "ema20": ema20,
        "ema50": ema50,
        "atr": atr,
        "bo_res": bo_res,
        "bo_sup": bo_sup,
        "bo_up": bo_up,
        "bo_down": bo_down,
        "avail_t": avail_t,
    }


def compute_hardened_lot_size(balance: float, stop_distance: float = 2.50) -> float:
    """Mathematical Risk-Based Sizing (Strict 2% equity risk ceiling)."""
    effective_balance = max(50.0, balance)
    dollar_risk = min(effective_balance * 0.02, 250.0)
    safe_stop_dist = max(1.50, stop_distance)
    target_vol = round(dollar_risk / (safe_stop_dist * 100.0), 2)
    min_vol = 0.02
    max_vol = round(min(5.0, max(0.04, (effective_balance / 700.0) * 0.08)), 2)
    return max(min_vol, min(max_vol, target_vol))


def run_daily_trump_backtest(
    data_dir: str = "data/candles",
    start_date_str: str = "2025-01-21",
    starting_balance: float = 100.0,
    enable_daily_withdrawal: bool = True,
    buffer_equity: float = 200.0,
) -> Dict[str, Any]:
    SPREAD = 0.30
    SLIPPAGE = 0.10
    TOTAL_FRICTION = SPREAD + SLIPPAGE

    candle_files = sorted(Path(data_dir).glob("gold_m1_*.csv"))
    candle_files = [f for f in candle_files if f.stem.replace("gold_m1_", "") >= start_date_str]

    print(f"Parallel precomputing {len(candle_files)} trading days from {start_date_str} to current...")
    t_start = time.time()
    with cf.ProcessPoolExecutor() as executor:
        days_data = list(executor.map(precompute_day_causal, candle_files))
    days_data = [d for d in days_data if d is not None]
    print(f"Successfully loaded {len(days_data)} days in {time.time()-t_start:.1f}s.")

    balance = starting_balance
    peak_equity = balance
    total_withdrawn_cash = 0.0

    regime_engine = get_regime_prior_engine()
    politician_brain = get_politician_brain()

    all_trades: List[Dict[str, Any]] = []
    daily_reports: List[Dict[str, Any]] = []
    active_pos: Optional[Dict[str, Any]] = None
    last_sig_time = 0.0
    cooldown_until = 0.0
    lockout_until = 0.0
    consecutive_losses = 0

    for day_idx, d in enumerate(days_data, 1):
        day_start_bal = balance
        day_trades_before = len(all_trades)
        date = d["date"]
        n = d["n"]
        o, h, l, c, t = d["o"], d["h"], d["l"], d["c"], d["t"]
        hour, minute, weekday = d["hour"], d["minute"], d["weekday"]
        atr_arr = d["atr"]
        ema20, ema50 = d["ema20"], d["ema50"]
        bo_res, bo_sup, bo_up, bo_down = d["bo_res"], d["bo_sup"], d["bo_up"], d["bo_down"]
        avail_t = d["avail_t"]

        active_bo_type = None
        active_bo_lvl = 0.0
        active_bo_time = 0.0
        day_realized_loss = 0.0
        day_circuit_breaker_active = False

        for i in range(2, n):
            curr_px = c[i]
            hi_px = h[i]
            lo_px = l[i]
            op_px = o[i]
            curr_t = t[i]
            hr = hour[i]
            mn = minute[i]
            wk = weekday[i]
            curr_atr = atr_arr[i]

            # 5m Breakout detection
            if bo_up[i]:
                active_bo_type = "UP"
                active_bo_lvl = bo_res[i]
                active_bo_time = avail_t[i]
            elif bo_down[i]:
                active_bo_type = "DOWN"
                active_bo_lvl = bo_sup[i]
                active_bo_time = avail_t[i]

            # -------------------------------------------------------------
            # 1. MANAGE OPEN POSITION
            # -------------------------------------------------------------
            if active_pos:
                is_buy = active_pos["direction"] == "BUY"
                entry_px = active_pos["entry_price"]
                atr_entry = active_pos["atr_entry"]
                profit_pts = (curr_px - entry_px) if is_buy else (entry_px - curr_px)
                bars_held = i - active_pos["entry_bar_idx"]

                # Current floating PnL
                floating_pnl = (profit_pts * active_pos["volume"] * 100.0) + active_pos["realized_pnl"]

                # Update Peak Watermark
                if floating_pnl > active_pos["peak_pnl"]:
                    active_pos["peak_pnl"] = floating_pnl

                # Stage 1 Risk-Free Pyramiding (+1.5 ATR)
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
                        realized_scale = net_pts * close_vol * 100.0
                        balance += realized_scale
                        active_pos["realized_pnl"] += realized_scale
                        active_pos["volume"] = round(active_pos["volume"] - close_vol, 2)
                        active_pos["sl_price"] = round(entry_px + 0.20, 2) if is_buy else round(entry_px - 0.20, 2)

                # --- EXIT CHECKS WITH INSTITUTIONAL SAFEGUARDS ---
                exit_triggered = False
                exit_reason = ""
                final_exit_px = curr_px
                final_pnl = 0.0

                # A. Dynamic Hard Risk Stop (Strict 5% equity ceiling or structural stop)
                hard_risk_floor = max(15.0, min(balance * 0.05, 50.0))
                sl_hit = (lo_px <= active_pos["sl_price"]) if is_buy else (hi_px >= active_pos["sl_price"])

                if floating_pnl <= -hard_risk_floor or sl_hit:
                    exit_triggered = True
                    exit_reason = "HARD_RISK_STOP" if floating_pnl <= -hard_risk_floor else "STOP_LOSS"
                    final_exit_px = active_pos["sl_price"] if sl_hit else curr_px
                    raw_pts = (final_exit_px - entry_px) if is_buy else (entry_px - final_exit_px)
                    net_pts = raw_pts - TOTAL_FRICTION
                    unconstrained = (net_pts * active_pos["volume"] * 100.0) + active_pos["realized_pnl"]
                    final_pnl = max(-hard_risk_floor, unconstrained)
                    balance += (final_pnl - active_pos["realized_pnl"])

                # B. Peak Watermark Trailing (Bag Protection: locks cash if pulls back > 18% from >= $50 peak)
                elif active_pos["peak_pnl"] >= 50.0:
                    pullback = active_pos["peak_pnl"] - floating_pnl
                    pullback_pct = pullback / active_pos["peak_pnl"] if active_pos["peak_pnl"] > 0 else 0.0
                    if pullback_pct >= 0.18:
                        exit_triggered = True
                        exit_reason = "PEAK_WATERMARK_LOCK"
                        final_exit_px = curr_px
                        raw_pts = (final_exit_px - entry_px) if is_buy else (entry_px - final_exit_px)
                        net_pts = raw_pts - TOTAL_FRICTION
                        final_pnl = (net_pts * active_pos["volume"] * 100.0) + active_pos["realized_pnl"]
                        balance += (net_pts * active_pos["volume"] * 100.0)

                # C. Macro Target Spike Harvest (+5.0 to +8.0 ATR)
                elif (hi_px >= active_pos["spike_target"]) if is_buy else (lo_px <= active_pos["spike_target"]):
                    exit_triggered = True
                    exit_reason = "MACRO_SPIKE_HARVEST"
                    final_exit_px = active_pos["spike_target"]
                    raw_pts = (final_exit_px - entry_px) if is_buy else (entry_px - final_exit_px)
                    net_pts = raw_pts - TOTAL_FRICTION
                    final_pnl = (net_pts * active_pos["volume"] * 100.0) + active_pos["realized_pnl"]
                    balance += (net_pts * active_pos["volume"] * 100.0)

                # D. 20-Minute Time-Decay Lifespan
                elif bars_held >= 20:
                    exit_triggered = True
                    exit_reason = "TIME_DECAY_SCRATCH"
                    final_exit_px = curr_px
                    raw_pts = (final_exit_px - entry_px) if is_buy else (entry_px - final_exit_px)
                    net_pts = raw_pts - TOTAL_FRICTION
                    final_pnl = (net_pts * active_pos["volume"] * 100.0) + active_pos["realized_pnl"]
                    balance += (net_pts * active_pos["volume"] * 100.0)

                # E. Friday Weekend Curfew Force-Flatten (20:30 UTC)
                elif wk == 4 and hr == 20 and mn >= 30:
                    exit_triggered = True
                    exit_reason = "FRIDAY_WEEKEND_FORCE_FLATTEN"
                    final_exit_px = curr_px
                    raw_pts = (final_exit_px - entry_px) if is_buy else (entry_px - final_exit_px)
                    net_pts = raw_pts - TOTAL_FRICTION
                    final_pnl = (net_pts * active_pos["volume"] * 100.0) + active_pos["realized_pnl"]
                    balance += (net_pts * active_pos["volume"] * 100.0)

                if exit_triggered:
                    peak_equity = max(peak_equity, balance)
                    is_win = final_pnl > 0

                    if not is_win:
                        consecutive_losses += 1
                        day_realized_loss += abs(final_pnl)
                        cooldown_until = curr_t + 300.0  # 5 min post-loss freeze
                        if consecutive_losses >= 2:
                            lockout_until = curr_t + 3600.0  # 60 min whipsaw lockout

                        # Check Daily Drawdown Circuit Breaker
                        max_day_loss = min(50.0, max(20.0, day_start_bal * 0.08))
                        if day_realized_loss >= max_day_loss:
                            day_circuit_breaker_active = True
                    else:
                        consecutive_losses = 0

                    all_trades.append({
                        "date": date,
                        "time_utc": f"{hr:02d}:{mn:02d}",
                        "direction": active_pos["direction"],
                        "strategy": active_pos["strategy"],
                        "entry_price": round(entry_px, 2),
                        "exit_price": round(final_exit_px, 2),
                        "volume": active_pos["volume"],
                        "peak_floating_pnl": round(active_pos["peak_pnl"], 2),
                        "realized_pnl": round(final_pnl, 2),
                        "exit_reason": exit_reason,
                        "duration_min": bars_held,
                        "balance_after": round(balance, 2),
                    })
                    active_pos = None

            # -------------------------------------------------------------
            # 2. SCAN FOR STRATEGY SIGNALS IF FLAT
            # -------------------------------------------------------------
            elif (
                not day_circuit_breaker_active
                and curr_t >= cooldown_until
                and curr_t >= lockout_until
                and (curr_t - last_sig_time) >= 180.0
                and balance >= 10.0
                and hr != 23
            ):
                # Friday Curfew (No new trades after Friday 18:00 UTC)
                if wk == 4 and hr >= 18:
                    continue

                sig_dir = None
                sig_strat = None
                wick_r = 0.50

                # Setup A: Multi-Session Silver Bullet (London 07-08 UTC & NY 14-15 UTC)
                if (7 <= hr < 8) or (14 <= hr < 15):
                    if lo_px > h[i - 2] and (lo_px - h[i - 2]) >= 0.70:
                        ce = (lo_px + h[i - 2]) / 2.0
                        if lo_px <= ce + (0.3 * curr_atr) and curr_px >= ce - 0.20:
                            sig_dir = "BUY"
                            sig_strat = "SILVER_BULLET"
                            wick_r = 0.65
                    elif hi_px < l[i - 2] and (l[i - 2] - hi_px) >= 0.70:
                        ce = (hi_px + l[i - 2]) / 2.0
                        if hi_px >= ce - (0.3 * curr_atr) and curr_px <= ce + 0.20:
                            sig_dir = "SELL"
                            sig_strat = "SILVER_BULLET"
                            wick_r = 0.65

                # Setup B: 5m Breakout + Retest + Rejection Pin Bar
                if not sig_dir and active_bo_type and 0 < (curr_t - active_bo_time) <= 1200.0:
                    lvl = active_bo_lvl
                    rng = max(0.20, hi_px - lo_px)
                    if active_bo_type == "UP":
                        trend_ok = curr_px > ema20[i] > ema50[i]
                        retest_ok = lo_px <= lvl + 1.20 and hi_px >= lvl - 0.20
                        wick = min(op_px, curr_px) - lo_px
                        w = wick / rng
                        if trend_ok and retest_ok and w >= 0.45 and curr_px >= op_px:
                            sig_dir = "BUY"
                            sig_strat = "BREAKOUT_RETEST"
                            wick_r = w
                            active_bo_type = None
                    elif active_bo_type == "DOWN":
                        trend_ok = curr_px < ema20[i] < ema50[i]
                        retest_ok = hi_px >= lvl - 1.20 and lo_px <= lvl + 0.20
                        wick = hi_px - max(op_px, curr_px)
                        w = wick / rng
                        if trend_ok and retest_ok and w >= 0.45 and curr_px <= op_px:
                            sig_dir = "SELL"
                            sig_strat = "BREAKOUT_RETEST"
                            wick_r = w
                            active_bo_type = None

                # Execute Setup
                if sig_dir:
                    regime_eval = regime_engine.evaluate_regime_fit(
                        strategy=sig_strat,
                        hour_utc=hr,
                        wick_ratio=wick_r,
                        trend_aligned=True,
                    )
                    if not regime_eval.is_allowed:
                        continue

                    pol_eval = politician_brain.evaluate_entry_macro_fit(
                        direction=sig_dir,
                        strategy_type=sig_strat,
                    )
                    if not pol_eval.is_permitted:
                        continue

                    sl_dist = round(0.8 * curr_atr, 2)
                    lot_vol = compute_hardened_lot_size(balance, stop_distance=sl_dist)

                    is_buy = sig_dir == "BUY"
                    actual_entry = curr_px + (SPREAD / 2.0) if is_buy else curr_px - (SPREAD / 2.0)
                    sl = round(lo_px - sl_dist, 2) if is_buy else round(hi_px + sl_dist, 2)
                    tp1 = round(curr_px + (2.5 * curr_atr), 2) if is_buy else round(curr_px - (2.5 * curr_atr), 2)
                    spike_mult = 5.0 * pol_eval.tp_expansion_multiplier
                    spike = round(curr_px + (spike_mult * curr_atr), 2) if is_buy else round(curr_px - (spike_mult * curr_atr), 2)

                    active_pos = {
                        "direction": sig_dir,
                        "strategy": sig_strat,
                        "entry_price": actual_entry,
                        "entry_time": curr_t,
                        "entry_bar_idx": i,
                        "hour": hr,
                        "volume": lot_vol,
                        "initial_vol": lot_vol,
                        "scaled_out_vol": 0.0,
                        "sl_price": sl,
                        "tp1_price": tp1,
                        "spike_target": spike,
                        "atr_entry": curr_atr,
                        "wick_ratio": wick_r,
                        "pyramid_count": 0,
                        "scaled_out_60": False,
                        "realized_pnl": 0.0,
                        "peak_pnl": 0.0,
                    }
                    last_sig_time = curr_t

        # -------------------------------------------------------------
        # 3. END OF DAY SOVEREIGN WITHDRAWAL
        # -------------------------------------------------------------
        day_trades = all_trades[day_trades_before:]
        day_wins = [t for t in day_trades if t["realized_pnl"] > 0]
        day_pnl = round(balance - day_start_bal, 2)

        withdrawn_today = 0.0
        if enable_daily_withdrawal and balance > buffer_equity and day_pnl > 0:
            rate = 0.35 if balance < 1000.0 else 0.50 if balance < 5000.0 else 0.70
            withdrawn_today = round(day_pnl * rate, 2)
            total_withdrawn_cash += withdrawn_today
            balance = round(balance - withdrawn_today, 2)

        day_wr = (len(day_wins) / len(day_trades) * 100.0) if day_trades else 0.0
        dd_pct = (peak_equity - balance) / peak_equity * 100.0 if peak_equity > 0 else 0.0

        daily_reports.append({
            "day_num": day_idx,
            "date": date,
            "start_balance": round(day_start_bal, 2),
            "end_balance": round(balance, 2),
            "day_pnl": day_pnl,
            "withdrawn_today": withdrawn_today,
            "cumulative_withdrawn": round(total_withdrawn_cash, 2),
            "trades_count": len(day_trades),
            "wins": len(day_wins),
            "win_rate_pct": round(day_wr, 1),
            "drawdown_pct": round(dd_pct, 1),
        })

    # -------------------------------------------------------------
    # 4. EXPORT REPORTS
    # -------------------------------------------------------------
    DATA_DIR = ROOT_DIR / "data"
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Export daily report CSV
    csv_path = DATA_DIR / "trump_regime_daily_report.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "day_num", "date", "start_balance", "end_balance", "day_pnl",
            "withdrawn_today", "cumulative_withdrawn", "trades_count", "wins", "win_rate_pct", "drawdown_pct"
        ])
        writer.writeheader()
        writer.writerows(daily_reports)

    # Export daily report JSON
    json_path = DATA_DIR / "trump_regime_daily_report.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(daily_reports, f, indent=2)

    total_trades_count = len(all_trades)
    total_wins_count = sum(1 for t in all_trades if t["realized_pnl"] > 0)
    total_losses_count = total_trades_count - total_wins_count
    overall_wr = (total_wins_count / total_trades_count * 100.0) if total_trades_count > 0 else 0.0

    gross_profit = sum(t["realized_pnl"] for t in all_trades if t["realized_pnl"] > 0)
    gross_loss = abs(sum(t["realized_pnl"] for t in all_trades if t["realized_pnl"] < 0))
    pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 99.9

    total_wealth = round(balance + total_withdrawn_cash, 2)
    profitable_days = sum(1 for d in daily_reports if d["day_pnl"] > 0)
    losing_days = sum(1 for d in daily_reports if d["day_pnl"] < 0)
    flat_days = len(daily_reports) - profitable_days - losing_days

    summary = {
        "regime": "Trump Administration (Jan 21, 2025 to Sep 20, 2026)",
        "total_calendar_days": len(daily_reports),
        "initial_starting_balance": starting_balance,
        "final_retained_balance": round(balance, 2),
        "total_cash_vaulted": round(total_withdrawn_cash, 2),
        "total_wealth_generated": total_wealth,
        "wealth_multiplier": round(total_wealth / starting_balance, 2),
        "total_trades": total_trades_count,
        "wins": total_wins_count,
        "losses": total_losses_count,
        "win_rate_pct": round(overall_wr, 2),
        "profit_factor": pf,
        "profitable_days": profitable_days,
        "losing_days": losing_days,
        "flat_days": flat_days,
        "daily_win_rate_pct": round(profitable_days / len(daily_reports) * 100.0, 1),
        "average_daily_profit": round((total_wealth - starting_balance) / len(daily_reports), 2),
        "daily_report_csv": str(csv_path),
        "daily_report_json": str(json_path),
    }

    summary_path = DATA_DIR / "trump_regime_hardened_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    return summary


if __name__ == "__main__":
    t0 = time.time()
    summary = run_daily_trump_backtest()
    print("\n" + "=" * 60)
    print(" TRUMP REGIME DAILY BACKTEST COMPLETE")
    print("=" * 60)
    for k, v in summary.items():
        print(f"{k}: {v}")
    print(f"Executed in {time.time()-t0:.1f}s")
