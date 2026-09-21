"""
tests/experiment_apex_v2.py
===========================
R&D Lab: Testing Advanced Alpha Levers for The Apex Engine.

Experiments:
1. Apex Trinity Baseline (60/40 Scale, 1-stage pyramid, 3.0 lot cap)
2. Hypothesis 1: Multi-Session Silver Bullet (London 07-08 + NY AM 14-15 + NY PM 18-19 UTC)
3. Hypothesis 2: Dual-Stage Risk-Free Stacking (Add 1 at +1.5 ATR, Add 2 at +3.0 ATR)
4. Hypothesis 3: Chandelier Moonbag Runner (Trailing 3-bar extremum - 1 ATR)
5. Hypothesis 4: The Apex TITAN Composite (All alpha levers combined + 5.0 lot cap)

Benchmarked on 60 trading days of historical XAUUSD M1 candles.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class TradeRecord:
    strategy: str
    direction: str
    entry_price: float
    exit_price: float
    volume: float
    pnl: float
    is_win: bool
    pyramid_levels: int = 0
    moonbag_points: float = 0.0


def compute_atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(c)
    tr = np.zeros(n, dtype=float)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    atr = pd.Series(tr).rolling(period, min_periods=3).mean().fillna(1.50).clip(0.80, 8.0).to_numpy()
    return atr


def precompute_day_data(f_path: Path) -> Optional[Dict[str, Any]]:
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
        df_5m["bo_up"] = (df_5m["res"] > 0) & (df_5m["close"] > df_5m["res"] * 1.0003)
        df_5m["bo_down"] = (df_5m["sup"] > 0) & (df_5m["close"] < df_5m["sup"] * 0.9997)

        df_merged = pd.merge_asof(
            df.sort_values("open_time"),
            df_5m[["open_time", "res", "sup", "bo_up", "bo_down"]].rename(columns={
                "open_time": "bar5m_time",
                "res": "bo_res",
                "sup": "bo_sup"
            }),
            left_on="open_time",
            right_on="bar5m_time",
            direction="backward"
        )
        bo_res = df_merged["bo_res"].fillna(0.0).to_numpy()
        bo_sup = df_merged["bo_sup"].fillna(0.0).to_numpy()
        bo_up = df_merged["bo_up"].fillna(False).to_numpy()
        bo_down = df_merged["bo_down"].fillna(False).to_numpy()
        bar5m_time = (df_merged["bar5m_time"].fillna(0) / 1000.0).to_numpy()
    else:
        n = len(df)
        bo_res = np.zeros(n)
        bo_sup = np.zeros(n)
        bo_up = np.zeros(n, dtype=bool)
        bo_down = np.zeros(n, dtype=bool)
        bar5m_time = np.zeros(n)

    asia = df[(df["hour"] >= 0) & (df["hour"] < 4)]
    asia_hi = float(asia["high"].max()) if len(asia) >= 30 else 0.0
    asia_lo = float(asia["low"].min()) if len(asia) >= 30 else 0.0

    return {
        "date": str(df["datetime"].iloc[0].date()),
        "n": len(df),
        "o": o, "h": h, "l": l, "c": c, "t": t, "hour": hour,
        "ema20": ema20, "ema50": ema50, "atr": atr,
        "bo_res": bo_res, "bo_sup": bo_sup, "bo_up": bo_up, "bo_down": bo_down,
        "bar5m_time": bar5m_time,
        "asia_hi": asia_hi, "asia_lo": asia_lo,
    }


def compute_lot_size(balance: float, max_lot_cap: float = 3.0) -> float:
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
        return min(max_lot_cap, round(balance / 2000.0, 2))


def simulate_model(
    days: List[Dict[str, Any]],
    multi_session_sb: bool = False,
    dual_stage_pyramid: bool = False,
    chandelier_moonbag: bool = False,
    max_lot_cap: float = 3.0,
) -> Dict[str, Any]:
    balance = 100.0
    peak_equity = balance
    max_dd = 0.0

    trades: List[TradeRecord] = []
    active_pos: Optional[Dict[str, Any]] = None
    last_sig_time = 0.0

    for d in days:
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

            # -------------------------------------------------------------
            # Active Position Management
            # -------------------------------------------------------------
            if active_pos:
                is_buy = active_pos["direction"] == "BUY"
                entry_px = active_pos["entry_price"]
                atr_entry = active_pos["atr_entry"]

                profit_pts = (curr_px - entry_px) if is_buy else (entry_px - curr_px)

                # STAGE 1 PYRAMID (+1.5 ATR)
                if active_pos["pyramid_count"] == 0 and profit_pts >= (1.5 * atr_entry):
                    add_vol = round(active_pos["initial_vol"] * 0.50, 2)
                    active_pos["volume"] += add_vol
                    active_pos["pyramid_count"] = 1
                    # Move SL to lock +0.5 ATR on position 1 -> guaranteed net break-even
                    active_pos["sl_price"] = round(entry_px + (0.5 * atr_entry), 2) if is_buy else round(entry_px - (0.5 * atr_entry), 2)

                # STAGE 2 DUAL PYRAMID (+3.0 ATR)
                elif dual_stage_pyramid and active_pos["pyramid_count"] == 1 and profit_pts >= (3.0 * atr_entry):
                    add_vol2 = round(active_pos["initial_vol"] * 0.25, 2)
                    active_pos["volume"] += add_vol2
                    active_pos["pyramid_count"] = 2
                    # Move SL to +2.0 ATR -> guaranteed massive net win
                    active_pos["sl_price"] = round(entry_px + (2.0 * atr_entry), 2) if is_buy else round(entry_px - (2.0 * atr_entry), 2)

                # 60/40 SCALE OUT
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
                        # Trail moonbag SL to BE + 0.10
                        active_pos["sl_price"] = round(entry_px + 0.10, 2) if is_buy else round(entry_px - 0.10, 2)

                # CHANDELIER TRAILING STOP FOR MOONBAG
                if chandelier_moonbag and active_pos["scaled_out_60"]:
                    # Trail lowest low of last 3 bars minus 1.0 ATR
                    if is_buy:
                        chandelier_sl = round(min(l[i - 1], l[i - 2], lo_px) - (1.0 * curr_atr), 2)
                        if chandelier_sl > active_pos["sl_price"]:
                            active_pos["sl_price"] = chandelier_sl
                    else:
                        chandelier_sl = round(max(h[i - 1], h[i - 2], hi_px) + (1.0 * curr_atr), 2)
                        if chandelier_sl < active_pos["sl_price"]:
                            active_pos["sl_price"] = chandelier_sl

                # CHECK STOP LOSS EXIT
                hit_sl = (lo_px <= active_pos["sl_price"]) if is_buy else (hi_px >= active_pos["sl_price"])
                if hit_sl:
                    exit_px = active_pos["sl_price"]
                    pts = (exit_px - entry_px) if is_buy else (entry_px - exit_px)
                    final_pnl = (pts * active_pos["volume"] * 100.0) + active_pos["realized_pnl"]
                    balance += (pts * active_pos["volume"] * 100.0)
                    trades.append(
                        TradeRecord(
                            strategy=active_pos["strategy"],
                            direction=active_pos["direction"],
                            entry_price=entry_px,
                            exit_price=exit_px,
                            volume=active_pos["volume"] + active_pos["scaled_out_vol"],
                            pnl=round(final_pnl, 2),
                            is_win=final_pnl > 0,
                            pyramid_levels=active_pos["pyramid_count"],
                            moonbag_points=round(pts, 2),
                        )
                    )
                    active_pos = None

                # CHECK SPIKE TARGET EXIT (Disabled or extended if Chandelier is active)
                elif not chandelier_moonbag:
                    hit_spike = (hi_px >= active_pos["spike_target"]) if is_buy else (lo_px <= active_pos["spike_target"])
                    if hit_spike:
                        exit_px = active_pos["spike_target"]
                        pts = (exit_px - entry_px) if is_buy else (entry_px - exit_px)
                        final_pnl = (pts * active_pos["volume"] * 100.0) + active_pos["realized_pnl"]
                        balance += (pts * active_pos["volume"] * 100.0)
                        trades.append(
                            TradeRecord(
                                strategy=active_pos["strategy"],
                                direction=active_pos["direction"],
                                entry_price=entry_px,
                                exit_price=exit_px,
                                volume=active_pos["volume"] + active_pos["scaled_out_vol"],
                                pnl=round(final_pnl, 2),
                                is_win=True,
                                pyramid_levels=active_pos["pyramid_count"],
                                moonbag_points=round(pts, 2),
                            )
                        )
                        active_pos = None

            # -------------------------------------------------------------
            # Entry Signal Evaluation (When Flat)
            # -------------------------------------------------------------
            elif (curr_t - last_sig_time) >= 180.0:
                sig_dir = None
                sig_strat = None

                # 1. SILVER BULLET SESSIONS
                # Standard: 14-15 UTC (NY AM)
                # Multi-Session: 07-08 UTC (London Open) + 14-15 UTC + 18-19 UTC (NY PM)
                is_sb_window = (14 <= hr < 15)
                if multi_session_sb:
                    is_sb_window = is_sb_window or (7 <= hr < 8) or (18 <= hr < 19)

                if is_sb_window:
                    # Bullish FVG
                    if lo_px > h[i - 2]:
                        gap = lo_px - h[i - 2]
                        if gap >= 0.70:
                            ce = (lo_px + h[i - 2]) / 2.0
                            if lo_px <= ce + (0.3 * curr_atr) and curr_px >= ce - 0.20:
                                sig_dir = "BUY"
                                sig_strat = "SILVER_BULLET"
                    # Bearish FVG
                    elif hi_px < l[i - 2]:
                        gap = l[i - 2] - hi_px
                        if gap >= 0.70:
                            ce = (hi_px + l[i - 2]) / 2.0
                            if hi_px >= ce - (0.3 * curr_atr) and curr_px <= ce + 0.20:
                                sig_dir = "SELL"
                                sig_strat = "SILVER_BULLET"

                # 2. TURTLE SOUP ASIAN SWEEP (06:00 - 09:00 UTC)
                if not sig_dir and (6 <= hr < 9) and asia_hi > 0 and asia_lo > 0:
                    rng = max(0.20, hi_px - lo_px)
                    if lo_px < asia_lo and curr_px > asia_lo:
                        wick = min(op_px, curr_px) - lo_px
                        if (wick / rng) >= 0.50:
                            sig_dir = "BUY"
                            sig_strat = "TURTLE_SOUP"
                    elif hi_px > asia_hi and curr_px < asia_hi:
                        wick = hi_px - max(op_px, curr_px)
                        if (wick / rng) >= 0.50:
                            sig_dir = "SELL"
                            sig_strat = "TURTLE_SOUP"

                # 3. 5M S&R BREAKOUT RETEST
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

            # Track peak equity & drawdown
            peak_equity = max(peak_equity, balance)
            dd = (peak_equity - balance) / peak_equity * 100.0 if peak_equity > 0 else 0.0
            max_dd = max(max_dd, dd)

    total_trades = len(trades)
    wins = [t for t in trades if t.is_win]
    losses = [t for t in trades if not t.is_win]
    wr = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else 99.9

    big_trades = [t for t in trades if t.pnl >= 500.0]
    four_fig = [t for t in trades if t.pnl >= 1000.0]
    five_fig = [t for t in trades if t.pnl >= 5000.0]

    return {
        "final_balance": round(balance, 2),
        "net_profit": round(balance - 100.0, 2),
        "return_pct": round((balance - 100.0) / 100.0 * 100.0, 1),
        "trades": total_trades,
        "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2),
        "max_drawdown": round(max_dd, 2),
        "trades_500_plus": len(big_trades),
        "trades_1000_plus": len(four_fig),
        "trades_5000_plus": len(five_fig),
    }


def run_all_experiments(num_days: int = 60):
    files = sorted(Path("data/candles").glob("gold_m1_*.csv"))[:num_days]
    print(f"Loading and precomputing {len(files)} historical days...")
    t0 = time.time()
    days_data = []
    for f in files:
        d = precompute_day_data(f)
        if d:
            days_data.append(d)
    print(f"Precomputed {len(days_data)} days in {time.time() - t0:.2f}s.\n")

    experiments = [
        ("Apex v1 Baseline (Current Production)", {
            "multi_session_sb": False, "dual_stage_pyramid": False, "chandelier_moonbag": False, "max_lot_cap": 3.0
        }),
        ("Exp 1: + Multi-Session Silver Bullet", {
            "multi_session_sb": True, "dual_stage_pyramid": False, "chandelier_moonbag": False, "max_lot_cap": 3.0
        }),
        ("Exp 2: + Dual-Stage Risk-Free Stacking", {
            "multi_session_sb": False, "dual_stage_pyramid": True, "chandelier_moonbag": False, "max_lot_cap": 3.0
        }),
        ("Exp 3: + Chandelier Moonbag Runner", {
            "multi_session_sb": False, "dual_stage_pyramid": False, "chandelier_moonbag": True, "max_lot_cap": 3.0
        }),
        ("Exp 4: The Apex TITAN (All Levers + 5.0 Cap)", {
            "multi_session_sb": True, "dual_stage_pyramid": True, "chandelier_moonbag": True, "max_lot_cap": 5.0
        }),
    ]

    print("=" * 95)
    print("                    THE APEX STRATEGY R&D BENCHMARK COMPARISON")
    print("=" * 95)
    print(f"{'Variant Name':<42} | {'Balance':<14} | {'Net Return':<12} | {'WR %':<6} | {'PF':<5} | {'MaxDD':<6} | {'$1k+ Tr'} | {'$5k+ Tr'}")
    print("-" * 95)

    for name, kwargs in experiments:
        t_start = time.time()
        res = simulate_model(days_data, **kwargs)
        elapsed = time.time() - t_start
        print(
            f"{name:<42} | ${res['final_balance']:<13,.2f} | +{res['return_pct']:<10.0f}% | {res['win_rate']:<5.1f}% | {res['profit_factor']:<4.2f} | {res['max_drawdown']:<5.1f}% | {res['trades_1000_plus']:<7} | {res['trades_5000_plus']:<7}"
        )
    print("=" * 95)


if __name__ == "__main__":
    run_all_experiments(num_days=60)
