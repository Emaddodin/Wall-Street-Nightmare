"""Strategy interface for the quant project.

A strategy produces an events dict consumed by quant.engine.backtest.Sim:

    events: {close_timestamp_ms: [(symbol, side, score, exit_model, meta)]}

  side        +1 long / -1 short
  score       any float; the simulator picks top_k by score per bar
  exit_model  {'tp_bps', 'sl_bps', 'trail_bps', 'max_bars'}
  meta        {'alloc': equity fraction, 'lev': leverage}

Strategies compute per-symbol signal arrays vectorized and convert to
events here (single place, tested once).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Strategy:
    name: str = "base"
    params: dict = field(default_factory=dict)

    def signal(self, feat: pd.DataFrame) -> np.ndarray:
        """Return per-bar values: 0 = flat, +side*score otherwise."""
        raise NotImplementedError

    def exit_model(self) -> dict:
        return {"tp_bps": 30, "sl_bps": 30, "trail_bps": None,
                "max_bars": None}

    def meta(self) -> dict:
        return {"alloc": 1.0, "lev": 20.0}


def events_from_signals(frames: dict[str, pd.DataFrame],
                        strategy: Strategy,
                        min_abs_score: float = 0.0) -> dict[int, list]:
    """frames: {symbol: DataFrame(open_time, ...)} -> events dict."""
    events: dict[int, list] = {}
    for symbol, df in frames.items():
        feat = df
        sig = strategy.signal(feat)
        if sig is None:
            continue
        t = df["open_time"].to_numpy(dtype=np.int64)
        nz = np.nonzero(sig)[0]
        ex = strategy.exit_model()
        meta = strategy.meta()
        for i in nz:
            if min_abs_score > 0 and abs(sig[i]) < min_abs_score:
                continue
            side = 1 if sig[i] > 0 else -1
            events.setdefault(int(t[i]), []).append(
                (symbol, side, float(abs(sig[i])), dict(ex), dict(meta)))
    return events
