"""
ghost_grid/runner_strategy.py
=============================
Volatility-Breakout RUNNER: the flip engine for micro accounts (convex payoff: small fixed loss, uncapped winner).

Signal: a COMPLETED 5m bar closes beyond the prior `lookback` 5m bars' high/low while the 1m ATR(14) (mean bar
range) is at least `atr_min` dollars, so the move is large relative to the ~$0.18 spread. Evaluated once per 5m bar.
Exit management lives in RunnerExitController (initial stop, breakeven arm, trailing stop, time stop).
Validated on real 1m gold data with walk-forward halves and slippage stress (see scripts/runner_flip.py).
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class RunnerSignal:
    direction: str
    entry_price: float
    breakout_level: float
    sl_price: float
    target_pts: float
    reasoning: str
    timestamp: float
    wick_ratio: float = 0.6


class RunnerStrategy:
    def __init__(self, lookback: int = 24, atr_min: float = 2.5, stop_pts: float = 2.5,
                 hours: Optional[tuple] = None, **_ignored):
        self.lookback = lookback
        self.atr_min = atr_min
        self.stop_pts = stop_pts
        self.hours = hours
        self.min_warmup = 5 * (lookback + 2) + 14

    def evaluate(self, candles_1m: List[Dict[str, Any]]) -> Optional[RunnerSignal]:
        if len(candles_1m) < self.min_warmup:
            return None
        last = candles_1m[-1]
        minute_ts = int(last.get("minute_ts", int(last["open_time"] // 1000)))
        if (minute_ts // 60) % 5 != 4:          # only when the 5m bar has just completed
            return None
        if self.hours:
            hr = (minute_ts % 86400) // 3600
            if not (self.hours[0] <= hr < self.hours[1]):
                return None

        recent = candles_1m[-14:]
        atr = sum(c["high"] - c["low"] for c in recent) / len(recent)
        if atr < self.atr_min:
            return None

        # Group the tail into completed 5m bars
        bars, cur_bucket, cur = [], None, None
        for c in candles_1m[-(5 * (self.lookback + 2)):]:
            ts = int(c.get("minute_ts", int(c["open_time"] // 1000)))
            b = ts // 300
            if b != cur_bucket:
                if cur is not None:
                    bars.append(cur)
                cur_bucket, cur = b, {"h": c["high"], "l": c["low"], "c": c["close"], "n": 1, "b": b}
            else:
                cur["h"] = max(cur["h"], c["high"]); cur["l"] = min(cur["l"], c["low"]); cur["c"] = c["close"]; cur["n"] += 1
        if cur is not None:
            bars.append(cur)
        bars = [b for b in bars if b["n"] >= 4]
        if len(bars) < self.lookback + 1 or bars[-1]["b"] != minute_ts // 300:
            return None
        prior = bars[-(self.lookback + 1):-1]
        hi = max(b["h"] for b in prior)
        lo = min(b["l"] for b in prior)
        px = bars[-1]["c"]
        if px > hi:
            return RunnerSignal("BUY", px, hi, round(px - self.stop_pts, 2), 0.0,
                                f"Runner: 5m close {px:.2f} > {self.lookback}-bar high {hi:.2f}, ATR {atr:.2f}", float(minute_ts))
        if px < lo:
            return RunnerSignal("SELL", px, lo, round(px + self.stop_pts, 2), 0.0,
                                f"Runner: 5m close {px:.2f} < {self.lookback}-bar low {lo:.2f}, ATR {atr:.2f}", float(minute_ts))
        return None
