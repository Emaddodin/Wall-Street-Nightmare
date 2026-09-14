"""SWEEP -- the London-open liquidity sweep (operator's Tehran session map).

Built from scratch on the operator's own session windows (UTC):

    Asia range   = high/low of 23.5..5.5 UTC  (config: asia_hours_utc)
    London open  = 6.5..9.5 UTC                (config: london_hours_utc)

When London opens, the classic play: price SWEEPS the Asia extreme
(high for shorts, low for longs) and is then REJECTED back inside --
retail stops get run, the reversal follows.  The trade fades the sweep.

    1m trigger: the first 1m close back inside the Asia range after a
                sweep, with a displacement body.
    SL: beyond the sweep extreme + ATR buffer.
    TP: fixed first-R target, the rest trails (engine D).
"""
from __future__ import annotations

import numpy as np

from entry_engine import EntrySignal
from structure import LONG, SHORT

MIN_MS = 60_000
HOUR_MS = 3_600_000


def _in_window(hour: float, lo: float, hi: float) -> bool:
    if hi >= lo:
        return lo <= hour < hi
    return hour >= lo or hour < hi   # wraps midnight


class SweepStrategy:
    name = "sweep"

    def __init__(self, cfg):
        self.cfg = cfg

    def on_bar(self, sd, i: int, tf15, j15: int, t_close: int, state: dict):
        cfg = self.cfg
        s = cfg.strategy["session"]
        ah_lo, ah_hi = s["asia_hours_utc"]
        lh_lo, lh_hi = s["london_hours_utc"]
        hour = (t_close // HOUR_MS) % 24 + ((t_close // MIN_MS) % 60) / 60.0
        if not _in_window(hour, lh_lo, lh_hi):
            return None, []
        t1 = sd.tfs["1m"]
        if i < 60:
            return None, []
        # Asia range: the 15m bars whose close-time hour is inside Asia
        # (this session's own extremes)
        t15 = tf15.t
        hour15 = (t15 // HOUR_MS) % 24 + ((t15 // MIN_MS) % 60) / 60.0
        asia = _in_window(hour15, ah_lo, ah_hi)
        idx = np.flatnonzero(asia)
        if len(idx) < 4:
            return None, []
        last_asia = idx[idx <= j15]
        if len(last_asia) == 0:
            return None, []
        a_hi = float(np.nanmax(tf15.h[last_asia]))
        a_lo = float(np.nanmin(tf15.l[last_asia]))
        # sweep then reject: the current 1m bar is back INSIDE the range
        px = float(t1.c[i])
        rng = a_hi - a_lo
        if rng <= 0:
            return None, []
        # need evidence of the sweep in the last few 1m bars
        look = t1.h[max(0, i - 12):i + 1]
        look_l = t1.l[max(0, i - 12):i + 1]
        swept_hi = float(np.max(look)) > a_hi
        swept_lo = float(np.min(look_l)) < a_lo
        if swept_hi and px < a_hi:
            direction, level = SHORT, a_hi
        elif swept_lo and px > a_lo:
            direction, level = LONG, a_lo
        else:
            return None, []
        # displacement: the rejection bar must actually move
        atr1m = float(t1.atr[i]) if t1.atr[i] and t1.atr[i] > 0 else rng / 20
        body = abs(float(t1.c[i]) - float(t1.o[i]))
        if body < 0.8 * atr1m:
            return None, []
        key = "last_fired_i"
        if state.get(key) == i:
            return None, []
        state[key] = i
        buf = cfg.stop["atr_buffer_mult"] * atr1m
        sl = a_hi + buf if direction == SHORT else a_lo - buf
        dist = abs(px - sl)
        tp_first = px + direction * 1.5 * dist if dist > 0 else None
        sig = EntrySignal(
            symbol=sd.sym, direction=direction, bar=i,
            at_ms=int(t_close), entry_price=px,
            swing_level=sl, entry_model="sweep", bias=int(direction),
            vp_poc=float("nan"), atr1m=atr1m,
            tp_first=tp_first)
        return sig, []
