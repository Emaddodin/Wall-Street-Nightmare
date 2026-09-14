"""Turtle-soup fade (ICT stop-run).

On the 15m: a bar wicks through an equal-highs/lows liquidity pool with
wick >= wick_frac of its range and CLOSES back inside -- the failed break
-- then price is faded the other way.  Long fade after an EQL sweep-down,
short fade after an EQH sweep-up.  Stop beyond the swept extreme.

The sweep detection and EQH/EQL levels are precomputed on the 15m in the
engine (pa.fast); this strategy fires once per fresh sweep bar.
"""
from __future__ import annotations

import numpy as np

from entry_engine import EntrySignal
from structure import LONG, SHORT

MIN_MS = 60_000


class TurtleStrategy:
    name = "turtle"

    def __init__(self, cfg):
        self.cfg = cfg

    def on_bar(self, sd, i: int, tf15, j15: int, t_close: int, state: dict):
        if j15 < 0:
            return None, []
        fresh = self.cfg.strategies["turtle"]["fresh_bars"]
        # a sweep must have JUST completed on this 15m bar (or the previous
        # few) -- fire once per sweep
        key = "last_fired_sweep_bar"
        ts_long = getattr(tf15, "ts_long", None)
        ts_short = getattr(tf15, "ts_short", None)
        if ts_long is None:
            return None, []
        sweep_bar = -1
        for k in range(j15, max(-1, j15 - fresh), -1):
            if ts_long[k]:
                sweep_bar = k
                break
        if sweep_bar >= 0 and state.get(key) != sweep_bar \
                and not np.isnan(tf15.last_sl[j15]):
            state[key] = sweep_bar
            # fade long: stop below the swept swing low extreme
            sig = EntrySignal(
                symbol=sd.sym, direction=LONG, bar=i,
                at_ms=int(t_close), entry_price=float(tf15.c[j15]),
                swing_level=float(tf15.last_sl[j15]),
                entry_model="turtle_soup", bias=1,
                vp_poc=float("nan"), atr1m=float(sd.tfs["1m"].atr[i]),
                tp_first=(float(tf15.last_sh[j15])
                          if not np.isnan(tf15.last_sh[j15])
                          and tf15.last_sh[j15] > tf15.c[j15] else None))
            return sig, []
        sweep_bar = -1
        for k in range(j15, max(-1, j15 - fresh), -1):
            if ts_short[k]:
                sweep_bar = k
                break
        if sweep_bar >= 0 and state.get(key) != sweep_bar \
                and not np.isnan(tf15.last_sh[j15]):
            state[key] = sweep_bar
            sig = EntrySignal(
                symbol=sd.sym, direction=SHORT, bar=i,
                at_ms=int(t_close), entry_price=float(tf15.c[j15]),
                swing_level=float(tf15.last_sh[j15]),
                entry_model="turtle_soup", bias=-1,
                vp_poc=float("nan"), atr1m=float(sd.tfs["1m"].atr[i]),
                tp_first=(float(tf15.last_sl[j15])
                          if not np.isnan(tf15.last_sl[j15])
                          and tf15.last_sl[j15] < tf15.c[j15] else None))
            return sig, []
        return None, []
