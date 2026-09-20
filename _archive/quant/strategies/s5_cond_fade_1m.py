"""Strategy S5 -- 1m-frequency conditioned fade with maker limit entries.

Same overshoot-selection thesis as S3, but signals refresh every 1m bar
(15m lookback return, vol-normalized), so limits chase the extreme tick
instead of waiting for 5m closes.  Evaluated at 1m resolution by the
engine (fills and exits on 1m bars).

params: lookback (1m bars, default 15), top_k/bot_k, min_z, range_frac_min,
rvol_min, tp_bps/sl_bps/trail_bps/max_bars (1m bars), alloc/lev,
limit_bps, limit_wait (1m bars), vol_win (1m bars, default 1440=1d).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant.strategies.base import Strategy


class CondFade1m(Strategy):
    name = "s5_cond_fade_1m"

    def __init__(self, lookback: int = 15, top_k: int = 2, bot_k: int = 2,
                 min_z: float = 1.5, range_frac_min: float = 0.0,
                 rvol_min: float = 0.0, tp_bps: float | None = 80.0,
                 sl_bps: float | None = 60.0,
                 trail_bps: float | None = None, max_bars: int = 60,
                 alloc: float = 0.25, lev: float = 20.0,
                 vol_win: int = 1440, limit_bps: float = 0.0,
                 limit_wait: int = 30):
        super().__init__()
        self.p = dict(lookback=lookback, top_k=top_k, bot_k=bot_k,
                      min_z=min_z, range_frac_min=range_frac_min,
                      rvol_min=rvol_min, tp_bps=tp_bps, sl_bps=sl_bps,
                      trail_bps=trail_bps, max_bars=max_bars, alloc=alloc,
                      lev=lev, vol_win=vol_win, limit_bps=limit_bps,
                      limit_wait=limit_wait)

    def events(self, frames: dict[str, pd.DataFrame]) -> dict[int, list]:
        """Per-symbol vectorized: absolute threshold |z| >= min_z (no
        per-timestamp cross-sectional ranking -- the earlier research showed
        the absolute threshold is as effective and it keeps 1m event
        generation fast)."""
        p = self.p
        ex = self.exit_model()
        meta = self.meta()
        events: dict[int, list] = {}
        for sym, df in frames.items():
            c = df["close"].to_numpy(dtype=float)
            h = df["high"].to_numpy(dtype=float)
            l = df["low"].to_numpy(dtype=float)
            v = df["volume"].to_numpy(dtype=float)
            t = df["open_time"].to_numpy(dtype=np.int64)
            n = len(c)
            r1 = np.full(n, np.nan)
            r1[1:] = np.log(c[1:] / c[:-1])
            lb = p["lookback"]
            r3 = np.full(n, np.nan)
            r3[lb:] = np.log(c[lb:] / c[:-lb])
            sd = pd.Series(r1).rolling(p["vol_win"]).std().to_numpy()
            z = r3 / np.maximum(sd * np.sqrt(lb), 1e-9)
            rf = (h - l) / np.maximum(c, 1e-12)
            rv = v / np.maximum(
                pd.Series(v).rolling(p["vol_win"]).mean().to_numpy(), 1e-9)
            ok = np.isfinite(z) & np.isfinite(r3) & np.isfinite(rf) \
                & np.isfinite(rv)
            if p["range_frac_min"]:
                ok &= rf >= p["range_frac_min"]
            if p["rvol_min"]:
                ok &= rv >= p["rvol_min"]
            short_m = ok & (z >= p["min_z"]) & (r3 > 0)
            long_m = ok & (z <= -p["min_z"]) & (r3 < 0)
            for i in np.nonzero(short_m)[0]:
                events.setdefault(int(t[i]), []).append(
                    (sym, -1, float(z[i]), dict(ex), dict(meta)))
            for i in np.nonzero(long_m)[0]:
                events.setdefault(int(t[i]), []).append(
                    (sym, 1, float(-z[i]), dict(ex), dict(meta)))
        return events

    def exit_model(self) -> dict:
        return {"tp_bps": self.p["tp_bps"], "sl_bps": self.p["sl_bps"],
                "trail_bps": self.p["trail_bps"],
                "max_bars": self.p["max_bars"]}

    def meta(self) -> dict:
        m = {"alloc": self.p["alloc"], "lev": self.p["lev"]}
        if self.p["limit_bps"]:
            m["limit_bps"] = self.p["limit_bps"]
            m["limit_wait_bars"] = self.p["limit_wait"]
        return m
