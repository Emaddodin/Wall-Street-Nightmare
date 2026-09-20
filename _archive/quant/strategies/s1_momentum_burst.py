"""Strategy S1 -- 'momentum burst' smoke-test strategy.

Hypothesis (to be validated by discovery, NOT assumed): a sharp 5m move on
high relative volume continues over the next few minutes because fast
traders chase the move.  This is the placeholder used to validate the
simulator end-to-end; the real entries come from discovery.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant.strategies.base import Strategy


class MomentumBurst(Strategy):
    name = "s1_momentum_burst"

    def __init__(self, r5_thresh_bps: float = 60.0, rvol_min: float = 1.5,
                 tp_bps: float = 40.0, sl_bps: float = 40.0,
                 max_bars: int = 30, alloc: float = 1.0, lev: float = 20.0):
        super().__init__()
        self.p = dict(r5_thresh_bps=r5_thresh_bps, rvol_min=rvol_min,
                      tp_bps=tp_bps, sl_bps=sl_bps, max_bars=max_bars,
                      alloc=alloc, lev=lev)

    def signal(self, feat: pd.DataFrame) -> np.ndarray:
        r5 = feat["r5"].to_numpy()
        rvol = feat["rvol_s"].to_numpy()
        sig = np.zeros(len(feat))
        valid = np.isfinite(r5) & np.isfinite(rvol)
        m = valid & (rvol >= self.p["rvol_min"])
        sig[m & (r5 >= self.p["r5_thresh_bps"])] = r5[m & (r5 >= self.p["r5_thresh_bps"])]
        sig[m & (r5 <= -self.p["r5_thresh_bps"])] = -r5[m & (r5 <= -self.p["r5_thresh_bps"])]
        return sig

    def exit_model(self) -> dict:
        return {"tp_bps": self.p["tp_bps"], "sl_bps": self.p["sl_bps"],
                "trail_bps": None, "max_bars": self.p["max_bars"]}

    def meta(self) -> dict:
        return {"alloc": self.p["alloc"], "lev": self.p["lev"]}
