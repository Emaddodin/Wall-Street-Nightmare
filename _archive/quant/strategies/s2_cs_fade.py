"""Strategy S2 -- Cross-Sectional Snap-Back Fade.

Hypothesis (from discovery): symbols with extreme 15m moves vs the rest of
the universe snap back over the next 15-60 minutes (measured edge
-2.5..-6 bps per rank snapshot, t~-8..-12, consistent across symbols and
horizons 15m..24h).  Short the top movers, long the bottom movers, sized
by vol-normalized extremity, with asymmetric TP/SL and a time stop.

Execution: signals at 5m bar close -> taker fill next 5m bar open.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant.strategies.base import Strategy


class CrossSectionalFade(Strategy):
    name = "s2_cs_fade"

    def __init__(self, lookback: int = 3, top_k: int = 2, bot_k: int = 2,
                 min_z: float = 1.0, tp_bps: float = 80.0,
                 sl_bps: float = 60.0, max_bars: int = 12, trail_bps=None,
                 alloc: float = 1.0, lev: float = 10.0,
                 vol_win: int = 288, limit_bps: float = 0.0):
        super().__init__()
        self.p = dict(lookback=lookback, top_k=top_k, bot_k=bot_k,
                      min_z=min_z, tp_bps=tp_bps, sl_bps=sl_bps,
                      max_bars=max_bars, trail_bps=trail_bps, alloc=alloc,
                      lev=lev, vol_win=vol_win, limit_bps=limit_bps)

    def events(self, frames: dict[str, pd.DataFrame]) -> dict[int, list]:
        """frames: 5m OHLCV per symbol.  Returns Sim events dict."""
        # per-symbol 5m returns and vol-normalized 15m z-score
        rets: dict[str, np.ndarray] = {}
        times: dict[str, np.ndarray] = {}
        zs: dict[str, np.ndarray] = {}
        for sym, df in frames.items():
            c = df["close"].to_numpy(dtype=float)
            r1 = np.full(len(c), np.nan)
            r1[1:] = np.log(c[1:] / c[:-1])
            r3 = np.full(len(c), np.nan)
            r3[self.p["lookback"]:] = np.log(c[self.p["lookback"]:] /
                                             c[:-self.p["lookback"]])
            sd = pd.Series(r1).rolling(self.p["vol_win"]).std().to_numpy()
            z = r3 / np.maximum(sd * np.sqrt(self.p["lookback"]), 1e-9)
            rets[sym] = r3
            zs[sym] = z
            times[sym] = df["open_time"].to_numpy(dtype=np.int64)
        # aligned timestamps
        all_t = np.unique(np.concatenate([t for t in times.values()]))
        tset = {s: set(t.tolist()) for s, t in times.items()}
        events: dict[int, list] = {}
        ex = self.exit_model()
        meta = self.meta()
        for t in all_t:
            rows = []
            for sym, tt in times.items():
                if t not in tset[sym]:
                    continue
                i = int(np.searchsorted(tt, t))
                z = zs[sym][i]
                r3 = rets[sym][i]
                if not np.isfinite(z) or not np.isfinite(r3):
                    continue
                rows.append((sym, z, r3))
            if not rows:
                continue
            rows.sort(key=lambda x: -x[1])
            n = len(rows)
            for k in range(min(self.p["top_k"], n)):
                sym, z, r3 = rows[k]
                if z >= self.p["min_z"] and r3 > 0:
                    events.setdefault(int(t), []).append(
                        (sym, -1, float(z), dict(ex), dict(meta)))
            for k in range(1, min(self.p["bot_k"], n) + 1):
                sym, z, r3 = rows[-k]
                if z <= -self.p["min_z"] and r3 < 0:
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
        return m
