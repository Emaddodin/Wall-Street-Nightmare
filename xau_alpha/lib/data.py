"""
xau_alpha/lib/data.py
Loading and causal feature helpers for XAUUSD research on real Dukascopy data.

All timestamps are int64 ms UTC. An M1 bar with ts t covers [t, t+60s); its values are known at t+60s.
Signals decided on M1 bar i may only act at or after ts[i] + 60_000 (the simulator enforces this).
"""
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
M1_PATH = ROOT / "data/m1_ba.parquet"
S10_PATH = ROOT / "data/s10_ba.parquet"
CAL_PATH = ROOT / "data/econ_calendar.csv"

# Research splits. TEST is the untouched holdout: evaluate a frozen candidate there once.
TRAIN = ("2025-01-21", "2025-12-31")
VALID = ("2026-01-01", "2026-05-31")
TEST = ("2026-06-01", "2026-09-30")


def _ms(date_str):
    return int(pd.Timestamp(date_str, tz="UTC").value // 1_000_000)


def split_of(ts_ms):
    """0 train, 1 valid, 2 test for an array of ms timestamps."""
    ts_ms = np.asarray(ts_ms)
    out = np.full(len(ts_ms), 2, dtype=np.int8)
    out[ts_ms < _ms(TEST[0])] = 1
    out[ts_ms < _ms(VALID[0])] = 0
    return out


@lru_cache(maxsize=1)
def load_m1() -> pd.DataFrame:
    """Bid/ask M1 bars plus mid OHLC and time columns (UTC, New York, London)."""
    df = pd.read_parquet(M1_PATH)
    for c in "ohlc":
        df[c] = (df["b" + c] + df["a" + c]) / 2.0
    dt = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df["dt"] = dt
    ny = dt.dt.tz_convert("America/New_York")
    lon = dt.dt.tz_convert("Europe/London")
    df["hour"] = dt.dt.hour.astype(np.int16)
    df["mod"] = (dt.dt.hour * 60 + dt.dt.minute).astype(np.int16)          # minute of day UTC
    df["ny_mod"] = (ny.dt.hour * 60 + ny.dt.minute).astype(np.int16)       # minute of day New York
    df["lon_mod"] = (lon.dt.hour * 60 + lon.dt.minute).astype(np.int16)    # minute of day London
    df["dow"] = dt.dt.dayofweek.astype(np.int8)
    # Trading day = New York date, rolled at 17:00 NY (the metals daily break), the ICT/CME convention.
    df["tday"] = (ny + pd.Timedelta(hours=7)).dt.strftime("%Y-%m-%d")
    df["split"] = split_of(df["ts"].values)
    return df


@lru_cache(maxsize=1)
def load_s10() -> dict:
    """10-second bid/ask bars as float32 numpy arrays (prices; 0.0005 resolution at $5k) for the simulator."""
    df = pd.read_parquet(S10_PATH)
    out = {"ts": df["ts"].values.astype(np.int64)}
    for c in ["bo", "bh", "bl", "bc", "ao", "ah", "al", "ac"]:
        out[c] = (df[c].values / 1000.0).astype(np.float32)
    return out


def atr(df: pd.DataFrame, n: int = 14, h="h", l="l", c="c") -> np.ndarray:
    """Wilder ATR on mid prices; value at i uses bars <= i."""
    hi, lo, cl = df[h].values, df[l].values, df[c].values
    pc = np.r_[cl[0], cl[:-1]]
    tr = np.maximum(hi - lo, np.maximum(np.abs(hi - pc), np.abs(lo - pc)))
    return pd.Series(tr).ewm(alpha=1.0 / n, adjust=False).mean().values


def resample_causal(m1: pd.DataFrame, minutes: int) -> tuple[pd.DataFrame, np.ndarray]:
    """
    Resample mid M1 bars to `minutes` buckets aligned on UTC.
    Returns (htf, known) where htf has o/h/l/c/spr/vol/ts_open/last_i and `known[i]` is the index of the
    last HTF bar that is COMPLETE at the close of M1 bar i (-1 if none). A bucket is complete when its last
    minute has printed, or when a later bucket has started (data gap).
    """
    b = (m1["ts"].values // (minutes * 60_000)).astype(np.int64)
    g = pd.DataFrame({"b": b, "o": m1["o"].values, "h": m1["h"].values, "l": m1["l"].values,
                      "c": m1["c"].values, "spr": m1["spr"].values, "vol": m1["vol"].values,
                      "i": np.arange(len(m1))}).groupby("b", sort=True)
    htf = pd.DataFrame({"o": g.o.first(), "h": g.h.max(), "l": g.l.min(), "c": g.c.last(),
                        "spr": g.spr.mean(), "vol": g.vol.sum(), "first_i": g.i.first(), "last_i": g.i.last()})
    htf["ts_open"] = htf.index.values * minutes * 60_000
    htf = htf.reset_index(drop=True)
    last_i = htf["last_i"].values
    last_ts = m1["ts"].values[last_i]
    bucket_end_last_minute = htf["ts_open"].values + (minutes - 1) * 60_000
    # complete at its own last bar if that bar is the bucket's final minute, otherwise one bar later
    known_at = np.where(last_ts == bucket_end_last_minute, last_i, last_i + 1)
    known = np.full(len(m1), -1, dtype=np.int64)
    # for every M1 index, the largest k with known_at[k] <= i
    k_idx = np.searchsorted(known_at, np.arange(len(m1)), side="right") - 1
    known[:] = k_idx
    return htf, known


def load_calendar() -> pd.DataFrame:
    if not CAL_PATH.exists():
        return pd.DataFrame(columns=["ts", "event", "impact"])
    cal = pd.read_csv(CAL_PATH)
    cal["ts"] = pd.to_datetime(cal["ts_utc"], utc=True).astype("int64") // 1_000_000
    return cal


def news_block_mask(ts_ms: np.ndarray, before_min=10, after_min=10, impacts=("HIGH",)) -> np.ndarray:
    """True where an M1 bar's close falls within [event - before, event + after] of a listed event."""
    cal = load_calendar()
    if cal.empty:
        return np.zeros(len(ts_ms), dtype=bool)
    ev = np.sort(cal.loc[cal["impact"].isin(impacts), "ts"].values.astype(np.int64))
    t = np.asarray(ts_ms) + 60_000
    j = np.searchsorted(ev, t)
    nxt = np.where(j < len(ev), ev[np.minimum(j, len(ev) - 1)], np.iinfo(np.int64).max)
    prv = np.where(j > 0, ev[np.maximum(j - 1, 0)], np.iinfo(np.int64).min // 2)
    return ((nxt - t) <= before_min * 60_000) | ((t - prv) <= after_min * 60_000)
