"""PULLBACK -- from-scratch trend-continuation family (R&D 2026-09-11).

No volume profile, no bias arrays.  Pure trend mechanics:

    15m: EMA(20) slope over `slope_bars` decides the trend (up/down).
    1m:  a pullback into the trend (retrace >= retrace_min x the prior
         swing leg, measured from the EMA) must be REJECTED by a wick
         candle -- lower wick >= wick_mult x body for longs, mirror for
         shorts -- then the close beyond the rejection bar's extreme.
    SL:  under the rejection bar's low (longs) minus an ATR buffer.
    TP:  fixed first-R target (first_r), the rest trails (engine D).

Brand-new family: enable on its own (strategies.enabled: [pullback]) or
race it against the others.
"""
from __future__ import annotations

import numpy as np

from entry_engine import EntrySignal
from structure import LONG, SHORT

MIN_MS = 60_000


class PullbackStrategy:
    name = "pullback"

    def __init__(self, cfg):
        self.cfg = cfg

    def on_bar(self, sd, i: int, tf15, j15: int, t_close: int, state: dict):
        cfg = self.cfg
        b = cfg.strategies.get("pullback") or {}
        if j15 < 30 or i < 5:
            return None, []
        # 15m trend: EMA slope over the last slope_bars closes
        ema_len = int(b.get("ema_len", 20))
        c15 = tf15.c[max(0, j15 - 60):j15 + 1]
        if len(c15) < ema_len + 2:
            return None, []
        k = 2.0 / (ema_len + 1.0)
        ema = np.zeros(len(c15))
        ema[0] = c15[0]
        for m in range(1, len(c15)):
            ema[m] = c15[m] * k + ema[m - 1] * (1 - k)
        sb = int(b.get("slope_bars", 8))
        if len(ema) < sb + 1:
            return None, []
        slope = float(ema[-1] - ema[-1 - sb])
        if abs(slope) < 1e-12:
            return None, []
        direction = LONG if slope > 0 else SHORT
        # 1m pullback rejection: a wick candle against the trend
        t1 = sd.tfs["1m"]
        o_, h_, l_, c_ = (float(t1.o[i]), float(t1.h[i]), float(t1.l[i]),
                          float(t1.c[i]))
        rng = h_ - l_
        body = abs(c_ - o_)
        wick_mult = float(b.get("wick_mult", 2.0))
        if direction == LONG:
            lower_wick = min(o_, c_) - l_
            rejected = lower_wick >= wick_mult * body and c_ > o_
            trigger = c_ > h_ - 0.2 * rng   # close in the top of the bar
        else:
            upper_wick = h_ - max(o_, c_)
            rejected = upper_wick >= wick_mult * body and c_ < o_
            trigger = c_ < l_ + 0.2 * rng
        if not rejected or not trigger or rng <= 0:
            return None, []
        key = "last_fired_i"
        if state.get(key) == i:
            return None, []
        state[key] = i
        atr1m = float(t1.atr[i]) if t1.atr[i] and t1.atr[i] > 0 else rng
        buf = cfg.stop["atr_buffer_mult"] * atr1m
        sl = l_ - buf if direction == LONG else h_ + buf
        dist = abs(c_ - sl)
        tp_first = c_ + direction * float(b.get("first_r", 1.5)) * dist \
            if dist > 0 else None
        sig = EntrySignal(
            symbol=sd.sym, direction=direction, bar=i,
            at_ms=int(t_close), entry_price=float(c_),
            swing_level=sl, entry_model="pullback", bias=int(direction),
            vp_poc=float("nan"), atr1m=atr1m,
            tp_first=tp_first)
        return sig, []
