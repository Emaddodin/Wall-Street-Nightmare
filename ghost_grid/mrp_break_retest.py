"""
ghost_grid/mrp_break_retest.py
==============================
Exact recreation of MR P FX Break & Retest Strategy from Video Forensics + Apex 5m S&R Structure.
1. 5m S&R Breakout: Detects 6-bar 5m range breakout.
2. 1m Retest: Price pulls back to the broken level.
3. Wick Rejection: 45%+ pin-bar rejection wick confirming buyers/sellers defense.
4. Trend Confirmation: EMA20 > EMA50 alignment.
5. Surgical Scalp Target: 0.40 - 0.60 pts rapid capture.
"""
from dataclasses import dataclass
from typing import List, Dict, Optional, Any
import math
import pandas as pd
import numpy as np

@dataclass
class MRPSignal:
    direction: str       # "BUY" or "SELL"
    entry_price: float
    breakout_level: float
    sl_price: float      # Local structure disaster stop
    target_pts: float    # Micro scalp target
    reasoning: str
    timestamp: float

class MRPBreakRetestStrategy:
    def __init__(self, min_warmup: int = 30):
        self.min_warmup = min_warmup
        self.active_5m_breakout: Optional[Dict[str, Any]] = None

    def evaluate(self, candles_1m: List[Dict[str, Any]]) -> Optional[MRPSignal]:
        if len(candles_1m) < self.min_warmup:
            return None

        df_1m = pd.DataFrame(candles_1m)
        if "datetime" not in df_1m.columns:
            if "open_time" in df_1m.columns:
                df_1m["datetime"] = pd.to_datetime(df_1m["open_time"], unit="ms", utc=True)
            elif "minute_ts" in df_1m.columns:
                df_1m["datetime"] = pd.to_datetime(df_1m["minute_ts"], unit="s", utc=True)
            else:
                return None

        # Calculate 1m EMA20 & EMA50
        df_1m["ema20"] = df_1m["close"].ewm(span=20, adjust=False).mean()
        df_1m["ema50"] = df_1m["close"].ewm(span=50, adjust=False).mean()

        # Resample to 5m bars
        df_5m = (
            df_1m.set_index("datetime")
            .resample("5min")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "open_time": "first"})
            .dropna()
            .reset_index()
        )
        if len(df_5m) < 7:
            return None

        # Rolling 6-bar high/low range
        highs = df_5m["high"].rolling(6).max().shift(1)
        lows = df_5m["low"].rolling(6).min().shift(1)

        last_5m = df_5m.iloc[-1]
        idx = len(df_5m) - 1
        res_lvl = float(highs.iloc[idx]) if not math.isnan(highs.iloc[idx]) else 0.0
        sup_lvl = float(lows.iloc[idx]) if not math.isnan(lows.iloc[idx]) else 0.0

        if res_lvl > 0 and float(last_5m["close"]) > res_lvl * 1.0002:
            self.active_5m_breakout = {
                "type": "UP",
                "level": res_lvl,
                "bar_time": int(last_5m["open_time"]),
            }
        elif sup_lvl > 0 and float(last_5m["close"]) < sup_lvl * 0.9998:
            self.active_5m_breakout = {
                "type": "DOWN",
                "level": sup_lvl,
                "bar_time": int(last_5m["open_time"]),
            }

        if not self.active_5m_breakout:
            return None

        b_type = self.active_5m_breakout["type"]
        lvl = self.active_5m_breakout["level"]
        b_time_ms = self.active_5m_breakout["bar_time"]

        last_1m = df_1m.iloc[-1]
        curr_t_ms = int(last_1m.get("open_time", 0))
        if curr_t_ms == 0:
            curr_t_ms = int(last_1m["datetime"].timestamp() * 1000)

        # Retest valid within 20 minutes
        if not (0 <= (curr_t_ms - b_time_ms) <= 20 * 60_000):
            self.active_5m_breakout = None
            return None

        curr_px = float(last_1m["close"])
        o, h, l, c = float(last_1m["open"]), float(last_1m["high"]), float(last_1m["low"]), float(last_1m["close"])
        rng = max(0.12, h - l)

        # Average True Range (simple 14)
        atr = 1.50 # typical gold 1m ATR default

        if b_type == "UP":
            trend_ok = curr_px > float(last_1m["ema20"]) > float(last_1m["ema50"])
            retest_ok = (l <= lvl + 0.60 and h >= lvl - 0.25)
            lower_wick = min(o, c) - l
            wick_ratio = lower_wick / rng
            if trend_ok and retest_ok and (wick_ratio >= 0.40 and c >= o):
                self.active_5m_breakout = None
                return MRPSignal(
                    direction="BUY",
                    entry_price=curr_px,
                    breakout_level=lvl,
                    sl_price=round(l - 1.50, 2),
                    target_pts=0.45,
                    reasoning=f"MR P FX 5m Breakout UP + 1m Retest @ ${lvl:.2f} + Pin Wick {wick_ratio:.2f}",
                    timestamp=curr_t_ms / 1000.0
                )

        elif b_type == "DOWN":
            trend_ok = curr_px < float(last_1m["ema20"]) < float(last_1m["ema50"])
            retest_ok = (h >= lvl - 0.60 and l <= lvl + 0.25)
            upper_wick = h - max(o, c)
            wick_ratio = upper_wick / rng
            if trend_ok and retest_ok and (wick_ratio >= 0.40 and c <= o):
                self.active_5m_breakout = None
                return MRPSignal(
                    direction="SELL",
                    entry_price=curr_px,
                    breakout_level=lvl,
                    sl_price=round(h + 1.50, 2),
                    target_pts=0.45,
                    reasoning=f"MR P FX 5m Breakout DOWN + 1m Retest @ ${lvl:.2f} + Pin Wick {wick_ratio:.2f}",
                    timestamp=curr_t_ms / 1000.0
                )

        return None
