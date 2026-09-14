"""5m cross-sectional panel + market-breadth cache.

Loads every symbol's 1m klines, resamples to 5m, and computes per-timestamp
market aggregates (equal-weight mean return, fraction up, dispersion) that
are then joinable onto any symbol's bars.  Cached to data/research/.
"""
from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd

from quant.lib import store
from quant.lib.features import BPS

log = logging.getLogger("quant.research.panel")

STEP = 300_000  # 5m


def symbol_5m(symbol: str, root=None) -> pd.DataFrame:
    df = store.load_klines(symbol, root)
    t = df["open_time"].to_numpy()
    bucket = t // STEP * STEP
    g = df.groupby(bucket)
    agg = g.agg(open=("open", "first"), high=("high", "max"),
                low=("low", "min"), close=("close", "last"),
                volume=("volume", "sum"))
    agg = agg.reset_index().rename(columns={"index": "open_time"})
    agg["symbol"] = symbol
    return agg


def build_panel(symbols: list[str], root=None,
                min_rows: int = 0) -> pd.DataFrame:
    """Concatenated 5m panel with symbol column (sorted by time)."""
    frames = []
    for s in symbols:
        try:
            d = symbol_5m(s, root)
        except FileNotFoundError:
            continue
        if len(d) >= min_rows:
            frames.append(d)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True).sort_values(
        ["open_time", "symbol"]).reset_index(drop=True)
    return out


def build_breadth(symbols: list[str], root=None,
                  cache_path=None) -> pd.DataFrame:
    """Per-5m-timestamp: mkt_ret5 (equal-weight mean 5m return, bps),
    mkt_up (fraction positive), mkt_disp (std of returns).  Causal: return
    r5 at bucket T uses close of T vs close of T-1 (both closed at T)."""
    t0 = time.time()
    root = root or store.data_root()
    cache_path = cache_path or root / "research" / "breadth.parquet"
    if cache_path.exists():
        log.info("breadth cache hit: %s", cache_path)
        return pd.read_parquet(cache_path)
    panel = build_panel(symbols, root)
    if panel.empty:
        return pd.DataFrame()
    logc = np.log(panel["close"].to_numpy())
    ret = np.full(len(panel), np.nan)
    prev_close = panel.groupby("symbol")["close"].shift(1).to_numpy()
    ret = np.log(panel["close"].to_numpy() /
                 np.maximum(prev_close, 1e-12)) * BPS
    p = pd.DataFrame({"open_time": panel["open_time"].to_numpy(),
                      "ret5": ret})
    # only use buckets where the symbol had a previous bar at T-1 (no gaps):
    # shift within symbol guarantees this; NaNs excluded below
    g = p.groupby("open_time")["ret5"]
    breadth = pd.DataFrame({
        "mkt_ret5": g.mean(),
        "mkt_up": g.apply(lambda x: (x > 0).mean()),
        "mkt_disp": g.std(),
        "mkt_n": g.count(),
    }).reset_index()
    breadth = breadth.dropna(subset=["mkt_ret5"])
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    breadth.to_parquet(cache_path, index=False)
    log.info("breadth built: %d rows in %.1fs -> %s",
             len(breadth), time.time() - t0, cache_path)
    return breadth
