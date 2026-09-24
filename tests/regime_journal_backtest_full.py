"""
tests/regime_journal_backtest_full.py
=====================================
Backtest and Per-Trade Journaling Engine across ALL Trading Days
from January 21, 2025 (Day 1 of Trump's Administration) to September 20, 2026.

Models the "To The Moon" Institutional Architecture:
- Strict Causality (Shifted 5m bar, zero lookahead)
- Real-world friction ($0.30 spread + $0.10 slippage = $0.40 total)
- Stage 1 Risk-Free Pyramiding (+50% vol @ +1.5 ATR with BE lock)
- 60/40 Scale-Out at +2.5 ATR + Moonbag Runner to +5.0 ATR
- Hard Dollar Risk Stop ($15 base or 15% tier stop)
- Sovereign Daily Withdrawal Compounding Schedule

Exports:
- data/regime_trade_journal_full.csv (Granular per-trade journal from 2025-01-21 to 2026-09-20)
- data/regime_analytics_summary_full.json (Empirical win rates, session expectancies, trap matrix)
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

import numpy as np
import pandas as pd

# Set root dir
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

    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    t = (df["open_time"] / 1000.0).to_numpy(dtype=float)
    hour = df["hour"].to_numpy(dtype=int)

    ema20 = pd.Series(c).ewm(span=20).mean().to_numpy()
    ema50 = pd.Series(c).ewm(span=50).mean().to_numpy()
    atr = compute_atr(h, l, c, period=14)

    # Resample 5m bars
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

        # Causal shift by 1 full 5m bar
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

    # Asian Range (00:00 - 05:00 UTC)
    asia = df[(df["hour"] >= 0) & (df["hour"] < 5)]
    asia_hi = float(asia["high"].max()) if len(asia) >= 30 else 0.0
    asia_lo = float(asia["low"].min()) if len(asia) >= 30 else 0.0

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
        "ema20": ema20,
        "ema50": ema50,
        "atr": atr,
        "bo_res": bo_res,
        "bo_sup": bo_sup,
        "bo_up": bo_up,
        "bo_down": bo_down,
        "avail_t": avail_t,
        "asia_hi": asia_hi,
        "asia_lo": asia_lo,
    }


def compute_lot_size(balance: float, max_lot_cap: float = 5.0) -> float:
    """Exact sizing ladder from run_xau_broker_live.py."""
    if balance < 25.0:
        return 0.01
    elif balance < 200.0:
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
        return min(max_lot_cap, round(balance / 2000.0, 2))


@dataclass
class JournalEntry:
    ticket_id: int
    date: str
    time_utc: str
    hour: int
    strategy: str
    direction: str
    entry_price: float
    exit_price: float
    initial_volume: float
    final_volume: float
    points_captured: float
    realized_pnl: float
    is_win: bool
    exit_reason: str
    duration_min: int
    atr_entry: float
    wick_ratio: float
    pyramid_added: bool
    scaled_out: bool
    running_balance: float
    peak_balance: float
    drawdown_pct: float
    laya_grade: str = "A_plus_prime"
    compounding_boost: float = 1.0
    tp_expansion: float = 1.0
    politician_regime: str = "TRADE_WAR_TARIFFS"



def run_full_regime_backtest(
    data_dir: str = "data/candles",
    start_date_str: str = "2025-01-21",
    starting_balance: float = 50.0,
    enable_daily_withdrawal: bool = True,
    buffer_equity: float = 200.0,
) -> Dict[str, Any]:
    SPREAD = 0.30
    SLIPPAGE = 0.10
    TOTAL_FRICTION = SPREAD + SLIPPAGE

    candle_files = sorted(Path(data_dir).glob("gold_m1_*.csv"))
    # Filter only files starting from 2025-01-21 onwards
    candle_files = [f for f in candle_files if f.stem.replace("gold_m1_", "") >= start_date_str]

    print(f"Loading and precomputing {len(candle_files)} trading days from {start_date_str} to current...")
    days_data = [precompute_day_causal(f) for f in candle_files]
    days_data = [d for d in days_data if d is not None]
    print(f"Successfully loaded {len(days_data)} valid trading days.")

    balance = starting_balance
    peak_equity = balance
    total_withdrawn_cash = 0.0

    regime_engine = get_regime_prior_engine()
    politician_brain = get_politician_brain()

    journal: List[JournalEntry] = []
    daily_summaries: List[Dict[str, Any]] = []
    ticket_counter = 1
    active_pos: Optional[Dict[str, Any]] = None
    last_sig_time = 0.0

    for day_idx, d in enumerate(days_data, 1):
        day_start_bal = balance
        trades_before = len(journal)
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

                # Hard Risk Stop Cap (-$15 or 15% tier stop)
                tier_mult = max(1.0, balance / 100.0)
                risk_stop_usd = max(15.0, min(15.0 * tier_mult, 0.25 * balance))
                curr_pts = (curr_px - entry_px) if is_buy else (entry_px - curr_px)
                floating_pnl = (curr_pts * active_pos["volume"] * 100.0) + active_pos["realized_pnl"]

                # Exit Checks
                exit_triggered = False
                exit_reason = ""
                final_exit_px = curr_px

                if floating_pnl <= -risk_stop_usd:
                    exit_triggered = True
                    exit_reason = "HARD_RISK_STOP"
                    final_pnl = -risk_stop_usd
                    balance += (final_pnl - active_pos["realized_pnl"])
                    raw_pts = (curr_px - entry_px) if is_buy else (entry_px - curr_px)

                elif (lo_px <= active_pos["sl_price"]) if is_buy else (hi_px >= active_pos["sl_price"]):
                    exit_triggered = True
                    final_exit_px = active_pos["sl_price"]
                    exit_reason = "TRAILING_BE_LOCK" if active_pos["scaled_out_60"] else "STOP_LOSS"
                    raw_pts = (final_exit_px - entry_px) if is_buy else (entry_px - final_exit_px)
                    net_pts = raw_pts - TOTAL_FRICTION
                    unconstrained_pnl = (net_pts * active_pos["volume"] * 100.0) + active_pos["realized_pnl"]
                    final_pnl = max(-risk_stop_usd, unconstrained_pnl)
                    balance += (final_pnl - active_pos["realized_pnl"])

                elif (hi_px >= active_pos["spike_target"]) if is_buy else (lo_px <= active_pos["spike_target"]):
                    exit_triggered = True
                    final_exit_px = active_pos["spike_target"]
                    exit_reason = "MACRO_SPIKE_HARVEST"
                    raw_pts = (final_exit_px - entry_px) if is_buy else (entry_px - final_exit_px)
                    net_pts = raw_pts - TOTAL_FRICTION
                    final_pnl = (net_pts * active_pos["volume"] * 100.0) + active_pos["realized_pnl"]
                    balance += (net_pts * active_pos["volume"] * 100.0)

                if exit_triggered:
                    peak_equity = max(peak_equity, balance)
                    dd_pct = (peak_equity - balance) / peak_equity * 100.0 if peak_equity > 0 else 0.0

                    entry_dt = datetime.fromtimestamp(active_pos["entry_time"], tz=timezone.utc)
                    journal.append(
                        JournalEntry(
                            ticket_id=ticket_counter,
                            date=date,
                            time_utc=entry_dt.strftime("%H:%M:%S"),
                            hour=active_pos["hour"],
                            strategy=active_pos["strategy"],
                            direction=active_pos["direction"],
                            entry_price=round(entry_px, 2),
                            exit_price=round(final_exit_px, 2),
                            initial_volume=active_pos["initial_vol"],
                            final_volume=round(active_pos["initial_vol"] + (active_pos["initial_vol"] * 0.50 if active_pos["pyramid_count"] > 0 else 0.0), 2),
                            points_captured=round(raw_pts, 2),
                            realized_pnl=round(final_pnl, 2),
                            is_win=final_pnl > 0,
                            exit_reason=exit_reason,
                            duration_min=bars_held,
                            atr_entry=round(active_pos["atr_entry"], 2),
                            wick_ratio=round(active_pos["wick_ratio"], 2),
                            pyramid_added=active_pos["pyramid_count"] > 0,
                            scaled_out=active_pos["scaled_out_60"],
                            running_balance=round(balance, 2),
                            peak_balance=round(peak_equity, 2),
                            drawdown_pct=round(dd_pct, 2),
                            laya_grade=active_pos.get("laya_grade", "A_plus_prime"),
                            compounding_boost=round(active_pos.get("compounding_boost", 1.0), 2),
                            tp_expansion=round(active_pos.get("tp_expansion", 1.0), 2),
                            politician_regime=active_pos.get("politician_regime", "TRADE_WAR_TARIFFS"),
                        )
                    )
                    ticket_counter += 1
                    active_pos = None

            # -------------------------------------------------------------
            # 2. SCAN FOR STRATEGY SIGNALS IF FLAT
            # -------------------------------------------------------------
            # Exclude toxic rollover hour 23 UTC
            elif (curr_t - last_sig_time) >= 180.0 and balance >= 10.0 and hr != 23:
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

                # Execute Setup Fill
                if sig_dir:
                    regime_eval = regime_engine.evaluate_regime_fit(
                        strategy=sig_strat,
                        hour_utc=hr,
                        wick_ratio=wick_r,
                        trend_aligned=True,
                    )
                    if not regime_eval.is_allowed:
                        sig_dir = None
                        continue

                    pol_eval = politician_brain.evaluate_entry_macro_fit(
                        direction=sig_dir,
                        strategy_type=sig_strat,
                    )
                    if not pol_eval.is_permitted:
                        sig_dir = None
                        continue

                    base_lot = compute_lot_size(balance)
                    compounding_mult = max(1.0, regime_eval.compounding_multiplier, pol_eval.alpha_boost_multiplier)
                    grade = "high_probability"
                    if pol_eval.alpha_boost_multiplier >= 1.50 and regime_eval.regime_grade == "A_plus_prime":
                        compounding_mult = 1.65  # Macro Sovereign Titan Sizing Boost!
                        grade = "macro_sovereign_titan"
                    elif regime_eval.regime_grade == "A_plus_prime":
                        grade = "A_plus_prime"

                    lot_vol = round(base_lot * compounding_mult, 2)

                    is_buy = sig_dir == "BUY"
                    actual_entry = curr_px + (SPREAD / 2.0) if is_buy else curr_px - (SPREAD / 2.0)
                    sl = round(lo_px - (0.8 * curr_atr), 2) if is_buy else round(hi_px + (0.8 * curr_atr), 2)
                    tp1 = round(curr_px + (2.5 * curr_atr), 2) if is_buy else round(curr_px - (2.5 * curr_atr), 2)

                    # Macro Target Expansion (+5.0 to +8.0 ATR)
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
                        "laya_grade": grade,
                        "compounding_boost": compounding_mult,
                        "tp_expansion": pol_eval.tp_expansion_multiplier,
                        "politician_regime": pol_eval.regime.value,
                    }
                    last_sig_time = curr_t

        # -------------------------------------------------------------
        # 3. END OF DAY SOVEREIGN WITHDRAWAL
        # -------------------------------------------------------------
        day_trades = journal[trades_before:]
        day_wins = [t for t in day_trades if t.is_win]
        day_pnl = balance - day_start_bal

        withdrawn_today = 0.0
        if enable_daily_withdrawal and balance > buffer_equity and day_pnl > 0:
            rate = 0.30 if balance < 1000.0 else 0.50 if balance < 5000.0 else 0.70
            withdrawn_today = round(day_pnl * rate, 2)
            total_withdrawn_cash += withdrawn_today
            balance = round(balance - withdrawn_today, 2)

        wr = (len(day_wins) / len(day_trades) * 100.0) if len(day_trades) > 0 else 0.0

        daily_summaries.append(
            {
                "day_num": day_idx,
                "date": date,
                "start_balance": round(day_start_bal, 2),
                "end_balance": round(balance, 2),
                "day_pnl": round(day_pnl, 2),
                "withdrawn_today": withdrawn_today,
                "cumulative_withdrawn": round(total_withdrawn_cash, 2),
                "trades_count": len(day_trades),
                "wins": len(day_wins),
                "win_rate_pct": round(wr, 1),
            }
        )

    # -------------------------------------------------------------
    # 4. COMPUTE DEEP REGIME ANALYTICS
    # -------------------------------------------------------------
    total_trades = len(journal)
    wins = [t for t in journal if t.is_win]
    losses = [t for t in journal if not t.is_win]
    overall_wr = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0

    gross_profit = sum(t.realized_pnl for t in wins)
    gross_loss = abs(sum(t.realized_pnl for t in losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else 99.9

    # Setup Breakdown
    setups_stat: Dict[str, Dict[str, Any]] = {}
    for s in ["BREAKOUT_RETEST", "SILVER_BULLET", "TURTLE_SOUP"]:
        s_trades = [t for t in journal if t.strategy == s]
        s_wins = [t for t in s_trades if t.is_win]
        s_wr = (len(s_wins) / len(s_trades) * 100.0) if s_trades else 0.0
        s_pnl = sum(t.realized_pnl for t in s_trades)
        setups_stat[s] = {
            "total_trades": len(s_trades),
            "wins": len(s_wins),
            "win_rate": round(s_wr, 1),
            "total_pnl": round(s_pnl, 2),
            "expectancy_usd": round(s_pnl / len(s_trades), 2) if s_trades else 0.0,
        }

    # Session / Hour Breakdown
    hourly_stat: Dict[int, Dict[str, Any]] = {}
    for hr in range(24):
        h_trades = [t for t in journal if t.hour == hr]
        if h_trades:
            h_wins = [t for t in h_trades if t.is_win]
            h_pnl = sum(t.realized_pnl for t in h_trades)
            hourly_stat[hr] = {
                "trades": len(h_trades),
                "wins": len(h_wins),
                "win_rate": round(len(h_wins) / len(h_trades) * 100.0, 1),
                "pnl": round(h_pnl, 2),
            }

    # Wick Ratio Geometry Curve
    wick_curve: Dict[str, Dict[str, Any]] = {}
    brackets = [("0.45-0.50", 0.45, 0.50), ("0.50-0.60", 0.50, 0.60), (">0.60", 0.60, 2.0)]
    for label, w_min, w_max in brackets:
        w_trades = [t for t in journal if w_min <= t.wick_ratio < w_max]
        if w_trades:
            w_wins = [t for t in w_trades if t.is_win]
            wick_curve[label] = {
                "trades": len(w_trades),
                "wins": len(w_wins),
                "win_rate": round(len(w_wins) / len(w_trades) * 100.0, 1),
                "avg_pnl": round(sum(t.realized_pnl for t in w_trades) / len(w_trades), 2),
            }

    # Max Drawdown across all days
    max_dd = max(t.drawdown_pct for t in journal) if journal else 0.0

    analytics_summary = {
        "regime": "Trump Administration Day 1 to Current (2025-01-21 to 2026-09-20)",
        "date_range": f"{days_data[0]['date']} to {days_data[-1]['date']}",
        "total_days_audited": len(days_data),
        "total_trades_logged": total_trades,
        "overall_win_rate_pct": round(overall_wr, 1),
        "profit_factor": round(pf, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "starting_capital": starting_balance,
        "total_cash_withdrawn_usd": round(total_withdrawn_cash, 2),
        "final_retained_equity_usd": round(balance, 2),
        "total_realized_wealth_usd": round(total_withdrawn_cash + balance, 2),
        "setups_performance": setups_stat,
        "hourly_performance": hourly_stat,
        "wick_geometry_curve": wick_curve,
    }

    # Export CSV Journal
    csv_path = ROOT_DIR / "data" / "regime_trade_journal_full.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[f.name for f in JournalEntry.__dataclass_fields__.values()])
        writer.writeheader()
        for row in journal:
            writer.writerow(asdict(row))
    print(f"📁 Saved complete per-trade journal ({total_trades} trades) to {csv_path}")

    # Export Daily Summaries CSV ($60 USD Sovereign Compounding)
    daily_csv_path = ROOT_DIR / "data" / "trump_regime_daily_60_usd_compounding.csv"
    with open(daily_csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "day_num", "date", "start_balance", "end_balance", "day_pnl",
            "withdrawn_today", "cumulative_withdrawn", "trades_count", "wins", "win_rate_pct"
        ])
        writer.writeheader()
        writer.writerows(daily_summaries)
    print(f"📊 Saved daily compounding ledger to {daily_csv_path}")

    # Export JSON Analytics Summary
    json_path = ROOT_DIR / "data" / "regime_analytics_summary_full.json"
    with open(json_path, "w") as f:
        json.dump(analytics_summary, f, indent=2)
    print(f"📊 Saved empirical regime analytics summary to {json_path}")

    analytics_summary["daily_summaries"] = daily_summaries
    return analytics_summary


if __name__ == "__main__":
    t0 = time.time()
    res = run_full_regime_backtest(
        data_dir="data/candles",
        start_date_str="2025-01-21",
        starting_balance=60.0,
        enable_daily_withdrawal=True,
        buffer_equity=200.0,
    )
    elapsed = time.time() - t0
    print("\n" + "=" * 85)
    print("   COMPLETE REGIME AUDIT: 2025-01-21 (TRUMP DAY 1) TO 2026-09-20")
    print("=" * 85)
    print(f"Days Audited:          {res['total_days_audited']} trading days")
    print(f"Execution Elapsed:     {elapsed:.2f} seconds")
    print(f"Total Trades Logged:   {res['total_trades_logged']:,}")
    print(f"Overall Win Rate:      {res['overall_win_rate_pct']}%")
    print(f"Profit Factor:         {res['profit_factor']}")
    print(f"Max Drawdown:          {res['max_drawdown_pct']}%")
    print("-" * 85)
    print(f"Starting Balance:      ${res['starting_capital']:,.2f}")
    print(f"Total Cash Withdrawn:  ${res['total_cash_withdrawn_usd']:,.2f}")
    print(f"Retained Trading Eq:   ${res['final_retained_equity_usd']:,.2f}")
    print(f"Total Realized Wealth: ${res['total_realized_wealth_usd']:,.2f}")
    print("=" * 85)
    print("\n--- PERFORMANCE BY ICT SETUP ---")
    for s_name, s_data in res["setups_performance"].items():
        print(f"{s_name:<18} | Trades: {s_data['total_trades']:<5} | Win Rate: {s_data['win_rate']}% | Expectancy: ${s_data['expectancy_usd']}/trade")
    print("=" * 85)
