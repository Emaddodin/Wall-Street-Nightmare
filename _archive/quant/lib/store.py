"""Shared storage helpers for the quant project."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]


def data_root() -> Path:
    import os
    return Path(os.getenv("QUANT_DATA", str(REPO / "quant" / "data")))


def ensure_dirs(root: Path):
    for name in ("klines", "metrics", "funding", "research", "experiments",
                 "reports"):
        (root / name).mkdir(parents=True, exist_ok=True)


def load_klines(symbol: str, root: Path | None = None,
                start_ms: int | None = None, end_ms: int | None = None
                ) -> pd.DataFrame:
    root = root or data_root()
    per = root / "klines" / f"{symbol}_1m.parquet"
    if not per.exists():
        raise FileNotFoundError(per)
    df = pd.read_parquet(per)
    if start_ms is not None:
        df = df[df["open_time"] >= start_ms]
    if end_ms is not None:
        df = df[df["open_time"] < end_ms]
    return df.reset_index(drop=True)


def load_metrics(symbol: str, root: Path | None = None) -> pd.DataFrame:
    root = root or data_root()
    per = root / "metrics" / f"{symbol}_metrics.parquet"
    if not per.exists():
        raise FileNotFoundError(per)
    return pd.read_parquet(per)


def load_funding(symbol: str, root: Path | None = None) -> pd.DataFrame:
    root = root or data_root()
    per = root / "funding" / f"{symbol}.parquet"
    if not per.exists():
        raise FileNotFoundError(per)
    return pd.read_parquet(per)


def klines_summary(root: Path | None = None) -> pd.DataFrame:
    """One row per symbol: bars, first/last open_time."""
    root = root or data_root()
    rows = []
    for per in sorted((root / "klines").glob("*_1m.parquet")):
        sym = per.name.split("_")[0]
        df = pd.read_parquet(per, columns=["open_time"])
        rows.append({"symbol": sym, "bars": len(df),
                     "first": df["open_time"].min(),
                     "last": df["open_time"].max()})
    return pd.DataFrame(rows)
