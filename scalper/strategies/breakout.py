"""Consolidation breakout (stolgo pa.consolidation + breakout_up/down).

On the 15m: a prior `range_bars` window whose width (high-low over the
window / mean close) is <= range_pct, then a CLOSE beyond the prior range
high (long) or low (short).  The signal fires on the first 1m bar after
the breakout 15m bar closes; the stop sits under the prior range (longs)
-- the classic stolgo bracket, structure-anchored.

Per-symbol state prevents the same breakout firing on every 1m bar of the
same 15m bar.
"""
from __future__ import annotations

import numpy as np

from entry_engine import EntrySignal
from structure import LONG, SHORT

MIN_MS = 60_000


class BreakoutStrategy:
    name = "breakout"

    def __init__(self, cfg):
        self.cfg = cfg

    def on_bar(self, sd, i: int, tf15, j15: int, t_close: int, state: dict):
        cfg = self.cfg
        if j15 < 2:
            return None, []
        b = cfg.strategies["breakout"]
        bars = b["range_bars"]
        # prior window (excludes the current 15m bar)
        lo = j15 - bars
        if lo < 0:
            return None, []
        win_h = tf15.h[lo:j15]
        win_l = tf15.l[lo:j15]
        rng_hi = float(win_h.max())
        rng_lo = float(win_l.min())
        mid = float(tf15.c[lo:j15].mean())
        if mid <= 0:
            return None, []
        width = (rng_hi - rng_lo) / mid
        if width > b["range_pct"]:
            return None, []
        c = float(tf15.c[j15])
        # fire once per 15m bar
        key = "last_fired_j15"
        if c > rng_hi:
            if state.get(key) == j15:
                return None, []
            state[key] = j15
            sig = EntrySignal(
                symbol=sd.sym, direction=LONG, bar=i,
                at_ms=int(t_close), entry_price=float(c),
                swing_level=rng_lo,          # stop under the prior range
                entry_model="breakout", bias=1,
                vp_poc=float("nan"), atr1m=float(sd.tfs["1m"].atr[i]),
                tp_first=None)
            return sig, []
        if c < rng_lo:
            if state.get(key) == j15:
                return None, []
            state[key] = j15
            sig = EntrySignal(
                symbol=sd.sym, direction=SHORT, bar=i,
                at_ms=int(t_close), entry_price=float(c),
                swing_level=rng_hi,          # stop above the prior range
                entry_model="breakout", bias=-1,
                vp_poc=float("nan"), atr1m=float(sd.tfs["1m"].atr[i]),
                tp_first=None)
            return sig, []
        return None, []
