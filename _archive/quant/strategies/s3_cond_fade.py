"""Strategy S3 -- Conditioned Cross-Sectional Fade, maker entries,
structured exits.

Entry (5m close): universe rank by vol-normalized 15m return z; fade the
extremes (short top-K, long bottom-K) when:
  * |z| >= min_z
  * range_frac >= range_frac_min   (big recent bars -- vol expansion)
  * rvol >= rvol_min                (volume behind the move)
Optional maker entry: limit at close offset limit_bps beyond the market
(fade side); fills only if price trades through; maker fee.

Exits: initial stop sl_bps; optional fixed target tp_bps; optional
chandelier trail trail_bps; time stop max_bars (5m bars).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant.strategies.base import Strategy


class CondFade(Strategy):
    name = "s3_cond_fade"

    def __init__(self, lookback: int = 3, top_k: int = 2, bot_k: int = 2,
                 min_z: float = 1.5, range_frac_min: float = 0.0,
                 rvol_min: float = 0.0, tp_bps: float | None = None,
                 sl_bps: float = 100.0, trail_bps: float | None = 120.0,
                 max_bars: int = 48, alloc: float = 0.25,
                 lev: float = 20.0, vol_win: int = 288,
                 limit_bps: float = 0.0, limit_wait: int = 6):
        super().__init__()
        self.p = dict(lookback=lookback, top_k=top_k, bot_k=bot_k,
                      min_z=min_z, range_frac_min=range_frac_min,
                      rvol_min=rvol_min, tp_bps=tp_bps, sl_bps=sl_bps,
                      trail_bps=trail_bps, max_bars=max_bars, alloc=alloc,
                      lev=lev, vol_win=vol_win, limit_bps=limit_bps,
                      limit_wait=limit_wait)

    def events(self, frames: dict[str, pd.DataFrame]) -> dict[int, list]:
        rets: dict[str, np.ndarray] = {}
        times: dict[str, np.ndarray] = {}
        zs: dict[str, np.ndarray] = {}
        rfs: dict[str, np.ndarray] = {}
        rvs: dict[str, np.ndarray] = {}
        for sym, df in frames.items():
            c = df["close"].to_numpy(dtype=float)
            h = df["high"].to_numpy(dtype=float)
            l = df["low"].to_numpy(dtype=float)
            v = df["volume"].to_numpy(dtype=float)
            n = len(c)
            r1 = np.full(n, np.nan)
            r1[1:] = np.log(c[1:] / c[:-1])
            lb = self.p["lookback"]
            r3 = np.full(n, np.nan)
            r3[lb:] = np.log(c[lb:] / c[:-lb])
            sd = pd.Series(r1).rolling(self.p["vol_win"]).std().to_numpy()
            z = r3 / np.maximum(sd * np.sqrt(lb), 1e-9)
            rf = (h - l) / np.maximum(c, 1e-12)
            rv = v / np.maximum(
                pd.Series(v).rolling(self.p["vol_win"]).mean().to_numpy(),
                1e-9)
            rets[sym] = r3
            zs[sym] = z
            rfs[sym] = rf
            rvs[sym] = rv
            times[sym] = df["open_time"].to_numpy(dtype=np.int64)
        all_t = np.unique(np.concatenate([t for t in times.values()]))
        tset = {s: set(t.tolist()) for s, t in times.items()}
        events: dict[int, list] = {}
        ex = self.exit_model()
        meta = self.meta()
        p = self.p
        for t in all_t:
            rows = []
            for sym, tt in times.items():
                if t not in tset[sym]:
                    continue
                i = int(np.searchsorted(tt, t))
                z = zs[sym][i]
                r3 = rets[sym][i]
                rf = rfs[sym][i]
                rv = rvs[sym][i]
                if not (np.isfinite(z) and np.isfinite(r3)
                        and np.isfinite(rf) and np.isfinite(rv)):
                    continue
                if rf < p["range_frac_min"] or rv < p["rvol_min"]:
                    continue
                rows.append((sym, z, r3))
            if not rows:
                continue
            rows.sort(key=lambda x: -x[1])
            n = len(rows)
            for k in range(min(p["top_k"], n)):
                sym, z, r3 = rows[k]
                if z >= p["min_z"] and r3 > 0:
                    events.setdefault(int(t), []).append(
                        (sym, -1, float(z), dict(ex), dict(meta)))
            for k in range(1, min(p["bot_k"], n) + 1):
                sym, z, r3 = rows[-k]
                if z <= -p["min_z"] and r3 < 0:
                    events.setdefault(int(t), []).append(
                        (sym, 1, float(-z), dict(ex), dict(meta)))
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
