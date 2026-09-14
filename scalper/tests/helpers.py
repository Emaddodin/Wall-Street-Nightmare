"""Shared synthetic-data builders for the test suite."""
from __future__ import annotations

import numpy as np
import pandas as pd

# epoch-aligned to the 15m grid so 1m/3m/15m resampling buckets line up
ALIGNED = 1_700_000_000_000 // 900_000 * 900_000


def synth_1m(n: int = 4000, start_ms: int = ALIGNED,
             seed: int = 3, drift: float = 0.0001,
             vol: float = 0.001) -> pd.DataFrame:
    """A random-walk 1m series with a mild trend and volume noise."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    close = 100.0 * np.exp(np.cumsum(rets))
    spread = np.abs(rng.normal(0, vol * 0.6, n))
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    volu = rng.uniform(50_000, 150_000, n)
    return pd.DataFrame({
        "open_time": np.arange(n, dtype=np.int64) * 60_000 + start_ms,
        "open": open_, "high": high, "low": low, "close": close,
        "volume": volu,
    })


def synth_with_swings(n: int = 3000, seed: int = 11) -> pd.DataFrame:
    """A trend with clean swings: alternating impulse legs + pullbacks so
    swing highs/lows and sweeps actually form."""
    rng = np.random.default_rng(seed)
    close = np.zeros(n)
    high = np.zeros(n)
    low = np.zeros(n)
    open_ = np.zeros(n)
    price = 100.0
    for i in range(n):
        phase = (i // 60) % 4          # 1h legs: up, pullback, up, pullback
        if phase == 0:
            step = 0.0006
        elif phase == 1:
            step = -0.0003
        elif phase == 2:
            step = 0.0006
        else:
            step = -0.0003
        step += rng.normal(0, 0.0004)
        open_[i] = price
        price += step
        close[i] = price
        high[i] = max(open_[i], close[i]) + abs(rng.normal(0, 0.00015))
        low[i] = min(open_[i], close[i]) - abs(rng.normal(0, 0.00015))
    return pd.DataFrame({
        "open_time": np.arange(n, dtype=np.int64) * 60_000 + ALIGNED,
        "open": open_, "high": high, "low": low, "close": close,
        "volume": rng.uniform(50_000, 150_000, n),
    })
