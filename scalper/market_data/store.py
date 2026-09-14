"""Local candle store: one parquet file per symbol, 1m base timeframe.

The 3m and 15m series are always RESAMPLED from the 1m base so every
timeframe shares one ground truth and one alignment.  A resampled bar only
exists once all of its 1m constituents exist -- a half-formed bar is dropped,
which is what keeps the engine lookahead-free by construction.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

COLS = ["open_time", "open", "high", "low", "close", "volume"]
TF_MINUTES = {"1m": 1, "3m": 3, "15m": 15}


class CandleStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- paths -----------------------------------------------------------
    def path(self, symbol: str, tf: str = "1m") -> Path:
        return self.root / f"{symbol}_{tf}.parquet"

    def has(self, symbol: str, tf: str = "1m") -> bool:
        return self.path(symbol, tf).exists()

    def symbols(self, tf: str = "1m") -> list[str]:
        out = []
        for p in sorted(self.root.glob(f"*_{tf}.parquet")):
            out.append(p.name[: -(len(tf) + len(".parquet") + 1)])
        return out

    # -- io ---------------------------------------------------------------
    def load(self, symbol: str, tf: str = "1m") -> pd.DataFrame:
        p = self.path(symbol, tf)
        if not p.exists():
            return pd.DataFrame(columns=COLS)
        return pd.read_parquet(p)

    def save(self, symbol: str, df: pd.DataFrame, tf: str = "1m") -> None:
        df = df[COLS].sort_values("open_time").drop_duplicates("open_time")
        df.to_parquet(self.path(symbol, tf), index=False)

    def merge(self, symbol: str, df: pd.DataFrame, tf: str = "1m") -> pd.DataFrame:
        """Add new bars (e.g. a fresh page) to the store; returns the merged
        frame.  Overlapping bars keep the newest value; gaps are left as gaps
        (never forward-filled -- a missing bar is missing)."""
        if df.empty:
            return self.load(symbol, tf)
        cur = self.load(symbol, tf)
        if cur.empty:
            self.save(symbol, df, tf)
            return df.copy()
        both = pd.concat([cur, df], ignore_index=True)
        both = both.drop_duplicates(subset="open_time", keep="last")
        both = both.sort_values("open_time").reset_index(drop=True)
        self.save(symbol, both, tf)
        return both


def resample_ohlcv(df1m: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregate 1m candles into N-minute candles, keeping ONLY complete bars.

    df1m must contain open_time (ms), open, high, low, close, volume, sorted
    ascending with no duplicates.  A bar whose every constituent minute is not
    present is dropped -- this is the alignment/no-lookahead guarantee."""
    if minutes == 1:
        return df1m.copy()
    if df1m.empty:
        return pd.DataFrame(columns=COLS)
    step = minutes * 60_000
    g = df1m.copy()
    g["bucket"] = (g["open_time"] // step) * step
    agg = g.groupby("bucket").agg(
        open_time=("open_time", "first"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        n=("open_time", "size"),
    )
    agg = agg[agg["n"] == minutes]          # complete bars only
    return agg[COLS].reset_index(drop=True)


def load_multi(store: CandleStore, symbol: str) -> dict[str, pd.DataFrame]:
    """Load 1m/3m/15m for one symbol with 3m/15m derived from the stored 1m."""
    one = store.load(symbol, "1m")
    return {
        "1m": one,
        "3m": resample_ohlcv(one, 3),
        "15m": resample_ohlcv(one, 15),
    }
