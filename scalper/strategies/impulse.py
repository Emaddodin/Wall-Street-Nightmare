"""IMPULSE -- from-scratch momentum family (R&D 2026-09-11).

No volume profile, no ICT levels.  Pure expansion mechanics:

    15m: the bar just closed must be an EXPANSION bar -- range >=
         expand_mult x the trailing ATR15 -- and its volume >= vol_mult x
         the trailing median volume.  Direction = a close beyond the
         prior `lookback`-bar 15m swing high (LONG) / low (SHORT).
    1m:  first bar whose body (displacement) >= body_mult x ATR1m and
         whose close is beyond the expansion bar's extreme.
    SL:  under the expansion bar's low (longs) minus an ATR buffer.
    TP:  a fixed first-R target (first_r), the rest trails (engine D).

This is a brand-new family: it can be enabled on its own
(strategies.enabled: [impulse]) or raced against the others.
"""
from __future__ import annotations

import numpy as np

from entry_engine import EntrySignal
from structure import LONG, SHORT

MIN_MS = 60_000


class ImpulseStrategy:
    name = "impulse"

    def __init__(self, cfg):
        self.cfg = cfg

    def on_bar(self, sd, i: int, tf15, j15: int, t_close: int, state: dict):
        cfg = self.cfg
        b = cfg.strategies.get("impulse") or {}
        if j15 < 30:
            return None, []
        # 15m expansion + volume surge on the just-closed bar
        rng = float(tf15.h[j15] - tf15.l[j15])
        atr_ref = float(np.nanmean(tf15.h[j15 - 20:j15] - tf15.l[j15 - 20:j15]))
        if atr_ref <= 0:
            return None, []
        if rng < float(b.get("expand_mult", 1.5)) * atr_ref:
            return None, []
        vol_now = float(tf15.v[j15])
        vol_ref = float(np.nanmedian(tf15.v[j15 - 20:j15]))
        if vol_now < float(b.get("vol_mult", 1.5)) * (vol_ref or 1.0):
            return None, []
        c = float(tf15.c[j15])
        lb = int(b.get("lookback", 30))
        lo = max(0, j15 - lb)
        swing_hi = float(np.nanmax(tf15.h[lo:j15]))
        swing_lo = float(np.nanmin(tf15.l[lo:j15]))
        if c > swing_hi:
            direction, level, anchor = LONG, swing_hi, swing_lo
        elif c < swing_lo:
            direction, level, anchor = SHORT, swing_lo, swing_hi
        else:
            return None, []
        # 1m trigger: displacement beyond the breakout level, once per 15m
        t1 = sd.tfs["1m"]
        if i >= len(t1.c):
            return None, []
        px = float(t1.c[i])
        if (direction == LONG and px <= level) or \
                (direction == SHORT and px >= level):
            return None, []
        body = abs(float(t1.c[i]) - float(t1.o[i]))
        atr1m = float(t1.atr[i]) if t1.atr[i] and t1.atr[i] > 0 else atr_ref / 15
        if body < float(b.get("body_mult", 1.2)) * atr1m:
            return None, []
        key = "last_fired_j15"
        if state.get(key) == j15:
            return None, []
        state[key] = j15
        buf = cfg.stop["atr_buffer_mult"] * atr1m
        sl = (float(tf15.l[j15]) - buf if direction == LONG
              else float(tf15.h[j15]) + buf)
        dist = abs(px - sl)
        tp_first = px + direction * float(b.get("first_r", 1.5)) * dist \
            if dist > 0 else None
        sig = EntrySignal(
            symbol=sd.sym, direction=direction, bar=i,
            at_ms=int(t_close), entry_price=px,
            swing_level=sl, entry_model="impulse", bias=int(direction),
            vp_poc=float("nan"), atr1m=atr1m,
            tp_first=tp_first)
        return sig, []
