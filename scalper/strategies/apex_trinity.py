"""
The "To The Moon" Strategy Engine (Apex Sovereign Matrix): Maximum Mathematical Alpha for XAUUSD.
Combines 3 uncorrelated institutional ICT playbooks:
1. 5m S&R Breakout + 1m Retest (Momentum Continuation)
2. Multi-Session Silver Bullet FVG 50% CE Tap (London 07:00-08:00 UTC & NY 14:00-15:00 UTC)
3. London Asian Turtle Soup Sweep (06:00-09:00 UTC Liquidity Pool Raid)

Features:
- Bar-by-bar causal deterministic computation
- Dynamic ATR(14) volatility normalized risk & reward
- Risk-free trailing pyramiding (cushion stacking)
- Institutional 60/40 partial scale-out & moonbag runner
- Sovereign compounding ladder up to 5.0 lots cap
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class ApexSignal:
    direction: str  # "BUY" or "SELL"
    entry_price: float
    sl_price: float
    tp1_price: float  # 60% scale out
    spike_target: float  # full momentum extension
    strategy_type: str  # "BREAKOUT_RETEST", "SILVER_BULLET_FVG", "TURTLE_SOUP_SWEEP"
    ict_concepts: List[str]
    atr_1m: float
    reasoning: str
    timestamp: float
    confidence_score: float = 8.5


@dataclass
class ApexPosition:
    ticket: str
    direction: str
    entry_price: float
    volume: float
    sl_price: float
    tp1_price: float
    spike_target: float
    open_time: float
    atr_at_entry: float
    strategy_type: str
    scaled_out_60: bool = False
    pyramid_count: int = 0
    pyramid_volume: float = 0.0
    floating_pnl: float = 0.0
    peak_pnl: float = 0.0


class ApexTrinityStrategy:
    """
    High-frequency quantitative strategy evaluator.
    Processes live 1-minute OHLCV candles with microsecond decision latency.
    """

    def __init__(self, min_candles_warmup: int = 30):
        self.min_warmup = min_candles_warmup
        self.active_5m_breakout: Optional[Dict[str, Any]] = None
        self.last_signal_time: float = 0.0
        self.min_signal_cooldown_sec: float = 180.0  # 3 minutes cooldown between entries

    @staticmethod
    def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculates Average True Range causal series."""
        h = df["high"].to_numpy(dtype=float)
        l = df["low"].to_numpy(dtype=float)
        c = df["close"].to_numpy(dtype=float)
        tr = np.zeros(len(df), dtype=float)
        tr[0] = h[0] - l[0]
        for i in range(1, len(df)):
            tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        return pd.Series(tr, index=df.index).rolling(period, min_periods=max(3, period // 3)).mean()

    def evaluate(self, candles_1m: List[Dict[str, Any]]) -> Optional[ApexSignal]:
        """
        Evaluates 1m candle history against the ICT Trinity Matrix.
        Returns ApexSignal if high-confluence entry is identified, else None.
        """
        if len(candles_1m) < self.min_warmup:
            return None

        df_1m = pd.DataFrame(candles_1m)
        df_1m["datetime"] = pd.to_datetime(df_1m["open_time"], unit="ms", utc=True)
        df_1m["ema20"] = df_1m["close"].ewm(span=20).mean()
        df_1m["ema50"] = df_1m["close"].ewm(span=50).mean()
        df_1m["atr"] = self.compute_atr(df_1m, period=14)

        last_bar = df_1m.iloc[-1]
        curr_px = float(last_bar["close"])
        curr_time = float(last_bar["open_time"]) / 1000.0
        curr_dt = last_bar["datetime"]
        utc_hour = curr_dt.hour
        utc_min = curr_dt.minute

        atr = float(last_bar["atr"]) if not math.isnan(last_bar["atr"]) else 1.50
        atr = max(0.80, min(8.0, atr))  # Sanity clamp for Gold

        # Enforce minimum cooldown between distinct signals
        if (curr_time - self.last_signal_time) < self.min_signal_cooldown_sec:
            return None

        # -------------------------------------------------------------
        # SETUP 2: ICT SILVER BULLET (London 07:00-08:00 UTC & NY 14:00-15:00 UTC)
        # -------------------------------------------------------------
        if (14 <= utc_hour < 15) or (7 <= utc_hour < 8):
            sb_signal = self._evaluate_silver_bullet(df_1m, curr_px, atr, curr_time)
            if sb_signal:
                self.last_signal_time = curr_time
                return sb_signal

        # -------------------------------------------------------------
        # SETUP 3: ICT LONDON ASIAN SWEEP / TURTLE SOUP (06:00 - 09:00 UTC)
        # -------------------------------------------------------------
        if 6 <= utc_hour < 9:
            ts_signal = self._evaluate_turtle_soup(df_1m, curr_px, atr, curr_time)
            if ts_signal:
                self.last_signal_time = curr_time
                return ts_signal

        # -------------------------------------------------------------
        # SETUP 1: 5m S&R BREAKOUT + 1m RETEST (ACTIVE ALL SESSIONS)
        # -------------------------------------------------------------
        bo_signal = self._evaluate_breakout_retest(df_1m, curr_px, atr, curr_time)
        if bo_signal:
            self.last_signal_time = curr_time
            return bo_signal

        return None

    def _evaluate_silver_bullet(
        self, df_1m: pd.DataFrame, curr_px: float, atr: float, curr_time: float
    ) -> Optional[ApexSignal]:
        """
        ICT Silver Bullet: 3-candle displacement creating an FVG during 14:00-15:00 UTC.
        Enters on tap of 50% Consequent Encroachment (CE).
        """
        if len(df_1m) < 5:
            return None

        bar0 = df_1m.iloc[-1]
        bar1 = df_1m.iloc[-2]
        bar2 = df_1m.iloc[-3]

        ema20 = float(df_1m["ema20"].iloc[-1])
        ema50 = float(df_1m["ema50"].iloc[-1])

        # Bullish FVG: Low of current bar > High of 2 bars ago
        if float(bar0["low"]) > float(bar2["high"]):
            # Strict Institutional Trend Alignment: Never buy against the 1m/5m trend!
            if curr_px < ema50 or ema20 < ema50:
                return None

            gap_lo = float(bar2["high"])
            gap_hi = float(bar0["low"])
            gap_size = gap_hi - gap_lo
            if gap_size >= 0.70:  # Minimum 70 cents gap on Gold for institutional displacement
                ce = round((gap_hi + gap_lo) / 2.0, 2)
                # Tap test: bar dipped into gap or is near CE
                if float(bar0["low"]) <= ce + (0.3 * atr) and curr_px >= ce - 0.20:
                    raw_sl = float(bar2["low"]) - 0.30
                    # Tightly clamp stop to at most 1.8 ATR to prevent oversize drawdowns
                    max_sl_dist = min(3.00, 1.8 * atr)
                    sl_px = round(max(raw_sl, curr_px - max_sl_dist), 2)
                    tp1_px = round(curr_px + (2.5 * atr), 2)
                    spike_px = round(curr_px + (5.0 * atr), 2)
                    return ApexSignal(
                        direction="BUY",
                        entry_price=curr_px,
                        sl_price=sl_px,
                        tp1_price=tp1_px,
                        spike_target=spike_px,
                        strategy_type="SILVER_BULLET_FVG",
                        ict_concepts=["Silver Bullet BISI FVG", "Consequent Encroachment Tap", "Trend Aligned EMA20/50"],
                        atr_1m=atr,
                        reasoning=f"NY Silver Bullet: Bullish FVG tap at CE ${ce:.2f} (Gap: ${gap_size:.2f} | Trend Aligned)",
                        timestamp=curr_time,
                        confidence_score=9.2,
                    )

        # Bearish FVG: High of current bar < Low of 2 bars ago
        elif float(bar0["high"]) < float(bar2["low"]):
            # Strict Institutional Trend Alignment: Never sell against the 1m/5m trend!
            if curr_px > ema50 or ema20 > ema50:
                return None

            gap_hi = float(bar2["low"])
            gap_lo = float(bar0["high"])
            gap_size = gap_hi - gap_lo
            if gap_size >= 0.70:
                ce = round((gap_hi + gap_lo) / 2.0, 2)
                if float(bar0["high"]) >= ce - (0.3 * atr) and curr_px <= ce + 0.20:
                    raw_sl = float(bar2["high"]) + 0.30
                    max_sl_dist = min(3.00, 1.8 * atr)
                    sl_px = round(min(raw_sl, curr_px + max_sl_dist), 2)
                    tp1_px = round(curr_px - (2.5 * atr), 2)
                    spike_px = round(curr_px - (5.0 * atr), 2)
                    return ApexSignal(
                        direction="SELL",
                        entry_price=curr_px,
                        sl_price=sl_px,
                        tp1_price=tp1_px,
                        spike_target=spike_px,
                        strategy_type="SILVER_BULLET_FVG",
                        ict_concepts=["Silver Bullet SIBI FVG", "Consequent Encroachment Tap", "Trend Aligned EMA20/50"],
                        atr_1m=atr,
                        reasoning=f"NY Silver Bullet: Bearish FVG tap at CE ${ce:.2f} (Gap: ${gap_size:.2f} | Trend Aligned)",
                        timestamp=curr_time,
                        confidence_score=9.2,
                    )
        return None

    def _evaluate_turtle_soup(
        self, df_1m: pd.DataFrame, curr_px: float, atr: float, curr_time: float
    ) -> Optional[ApexSignal]:
        """
        ICT Turtle Soup: Liquidity sweep of the Asian Session range (00:00-04:00 UTC)
        during London Open (06:00-09:00 UTC) with long rejection wick.
        """
        # Find Asian range bars for the current day
        today_date = df_1m.iloc[-1]["datetime"].date()
        asia_bars = df_1m[
            (df_1m["datetime"].dt.date == today_date)
            & (df_1m["datetime"].dt.hour >= 0)
            & (df_1m["datetime"].dt.hour < 4)
        ]
        if len(asia_bars) < 60:  # Need at least 1 hour of Asian bars
            return None

        asia_high = float(asia_bars["high"].max())
        asia_low = float(asia_bars["low"].min())

        last_bar = df_1m.iloc[-1]
        o, h, l, c = float(last_bar["open"]), float(last_bar["high"]), float(last_bar["low"]), float(last_bar["close"])
        rng = max(0.20, h - l)

        # Bullish Turtle Soup: Low sweeps below Asian Low, but closes back above with a hammer wick
        if l < asia_low and c > asia_low:
            sweep_wick = min(o, c) - l
            if (sweep_wick / rng) >= 0.50:  # At least 50% wick below Asian low
                sl_px = round(l - 0.30, 2)
                tp1_px = round(curr_px + (2.5 * atr), 2)
                spike_px = round(curr_px + (5.0 * atr), 2)
                return ApexSignal(
                    direction="BUY",
                    entry_price=curr_px,
                    sl_price=sl_px,
                    tp1_price=tp1_px,
                    spike_target=spike_px,
                    strategy_type="TURTLE_SOUP_SWEEP",
                    ict_concepts=["Asian Low Liquidity Raid", "Turtle Soup Reversal", "London Judas Swing"],
                    atr_1m=atr,
                    reasoning=f"Turtle Soup Long: Asian low ${asia_low:.2f} swept by ${(asia_low - l):.2f} with {(sweep_wick/rng)*100:.0f}% wick",
                    timestamp=curr_time,
                    confidence_score=9.4,
                )

        # Bearish Turtle Soup: High sweeps above Asian High, but closes back below with a shooting star wick
        elif h > asia_high and c < asia_high:
            sweep_wick = h - max(o, c)
            if (sweep_wick / rng) >= 0.50:  # At least 50% wick above Asian high
                sl_px = round(h + 0.30, 2)
                tp1_px = round(curr_px - (2.5 * atr), 2)
                spike_px = round(curr_px - (5.0 * atr), 2)
                return ApexSignal(
                    direction="SELL",
                    entry_price=curr_px,
                    sl_price=sl_px,
                    tp1_price=tp1_px,
                    spike_target=spike_px,
                    strategy_type="TURTLE_SOUP_SWEEP",
                    ict_concepts=["Asian High Liquidity Raid", "Turtle Soup Reversal", "London Judas Swing"],
                    atr_1m=atr,
                    reasoning=f"Turtle Soup Short: Asian high ${asia_high:.2f} swept by ${(h - asia_high):.2f} with {(sweep_wick/rng)*100:.0f}% wick",
                    timestamp=curr_time,
                    confidence_score=9.4,
                )
        return None

    def _evaluate_breakout_retest(
        self, df_1m: pd.DataFrame, curr_px: float, atr: float, curr_time: float
    ) -> Optional[ApexSignal]:
        """
        5m S&R Breakout + 1m Retest + Pin Bar (Momentum Continuation).
        """
        # Resample to 5m
        df_5m = (
            df_1m.set_index("datetime")
            .resample("5min")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "open_time": "first"})
            .dropna()
            .reset_index()
        )
        if len(df_5m) < 7:
            return None

        # Rolling 6-bar range
        highs = df_5m["high"].rolling(6).max().shift(1)
        lows = df_5m["low"].rolling(6).min().shift(1)

        last_5m = df_5m.iloc[-1]
        idx = len(df_5m) - 1
        res_lvl = float(highs.iloc[idx]) if not math.isnan(highs.iloc[idx]) else 0.0
        sup_lvl = float(lows.iloc[idx]) if not math.isnan(lows.iloc[idx]) else 0.0

        if res_lvl > 0 and float(last_5m["close"]) > res_lvl * 1.0003:
            self.active_5m_breakout = {
                "type": "UP",
                "level": res_lvl,
                "bar_time": int(last_5m["open_time"]),
            }
        elif sup_lvl > 0 and float(last_5m["close"]) < sup_lvl * 0.9997:
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
        curr_t_ms = int(curr_time * 1000)

        # Must be within 20 minutes of 5m breakout
        if not (0 < (curr_t_ms - b_time_ms) <= 20 * 60_000):
            return None

        last_1m = df_1m.iloc[-1]
        o, h, l, c = float(last_1m["open"]), float(last_1m["high"]), float(last_1m["low"]), float(last_1m["close"])
        rng = max(0.10, h - l)

        if b_type == "UP":
            trend_ok = curr_px > float(last_1m["ema20"]) > float(last_1m["ema50"])
            retest_ok = l <= lvl + (0.8 * atr) and h >= lvl - (0.3 * atr)
            lower_wick = min(o, c) - l
            wick_ratio = lower_wick / rng
            if trend_ok and retest_ok and (wick_ratio >= 0.45 and c >= o):
                sl_px = round(l - 0.20, 2)
                tp1_px = round(curr_px + (2.5 * atr), 2)
                spike_px = round(curr_px + (5.0 * atr), 2)
                self.active_5m_breakout = None
                return ApexSignal(
                    direction="BUY",
                    entry_price=curr_px,
                    sl_price=sl_px,
                    tp1_price=tp1_px,
                    spike_target=spike_px,
                    strategy_type="BREAKOUT_RETEST",
                    ict_concepts=["5m Range Breakout", "1m S&R Retest", "Bullish Rejection Wick"],
                    atr_1m=atr,
                    reasoning=f"5m S&R Breakout UP + 1m Retest @ ${lvl:.2f} + Pin Wick {wick_ratio:.2f}",
                    timestamp=curr_time,
                    confidence_score=9.0,
                )

        elif b_type == "DOWN":
            trend_ok = curr_px < float(last_1m["ema20"]) < float(last_1m["ema50"])
            retest_ok = h >= lvl - (0.8 * atr) and l <= lvl + (0.3 * atr)
            upper_wick = h - max(o, c)
            wick_ratio = upper_wick / rng
            if trend_ok and retest_ok and (wick_ratio >= 0.45 and c <= o):
                sl_px = round(h + 0.20, 2)
                tp1_px = round(curr_px - (2.5 * atr), 2)
                spike_px = round(curr_px - (5.0 * atr), 2)
                self.active_5m_breakout = None
                return ApexSignal(
                    direction="SELL",
                    entry_price=curr_px,
                    sl_price=sl_px,
                    tp1_price=tp1_px,
                    spike_target=spike_px,
                    strategy_type="BREAKOUT_RETEST",
                    ict_concepts=["5m Range Breakout", "1m S&R Retest", "Bearish Rejection Wick"],
                    atr_1m=atr,
                    reasoning=f"5m S&R Breakout DOWN + 1m Retest @ ${lvl:.2f} + Pin Wick {wick_ratio:.2f}",
                    timestamp=curr_time,
                    confidence_score=9.0,
                )
        return None

    # -------------------------------------------------------------------------
    # POSITION LIFECYCLE: RISK-FREE PYRAMID & 60/40 SCALE-OUT MANAGEMENT
    # -------------------------------------------------------------------------

    @staticmethod
    def check_pyramid_opportunity(
        pos: ApexPosition, curr_px: float, current_atr: float, max_pyramids: int = 1
    ) -> Optional[Tuple[float, float, str]]:
        """
        Risk-Free Pyramiding:
        When Order 1 is in profit by >= 1.5 * ATR, trail its SL to lock in >= 0.5 * ATR profit.
        Returns: (pyramid_volume, new_trailing_sl_for_order1, reasoning) or None
        """
        if pos.pyramid_count >= max_pyramids:
            return None

        is_buy = pos.direction == "BUY"
        pnl_points = (curr_px - pos.entry_price) if is_buy else (pos.entry_price - curr_px)

        # Order 1 must be up at least 1.5 ATR points
        if pnl_points >= 1.5 * current_atr:
            pyramid_vol = round(pos.volume * 0.75, 2)
            # Trail Order 1 SL to guarantee +0.5 ATR profit
            new_sl = round(pos.entry_price + (0.5 * current_atr) if is_buy else pos.entry_price - (0.5 * current_atr), 2)
            reason = f"Risk-Free Pyramid: Order 1 locked at +0.5 ATR (${new_sl:.2f}). Stacking {pyramid_vol} lots."
            return (pyramid_vol, new_sl, reason)
        return None

    @staticmethod
    def check_scale_out_60(pos: ApexPosition, curr_px: float) -> Optional[Tuple[float, float, str]]:
        """
        60/40 Scale-out:
        When price reaches TP1, bank 60% of total volume into cash,
        and trail SL on the remaining 40% moonbag to Break-Even + Spread ($0.30).
        Returns: (volume_to_close, new_be_sl, reasoning) or None
        """
        if pos.scaled_out_60:
            return None

        is_buy = pos.direction == "BUY"
        hit_tp1 = (curr_px >= pos.tp1_price) if is_buy else (curr_px <= pos.tp1_price)

        if hit_tp1:
            vol_to_close = round(pos.volume * 0.60, 2)
            # Trail remaining to BE + small buffer
            be_sl = round(pos.entry_price + 0.30 if is_buy else pos.entry_price - 0.30, 2)
            reason = f"TP1 Hit: Banked 60% ({vol_to_close} lots). Remaining 40% trailed to Break-Even (${be_sl:.2f})."
            return (vol_to_close, be_sl, reason)
        return None
