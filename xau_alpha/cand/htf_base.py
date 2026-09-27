"""
cand/htf_base.py - shared, per-process cached building blocks for the X2 trend families
(cand/htf_breakout.py and cand/trend_pullback.py).

Everything here is causal and CUT AT THE TEST START (2026-06-01): the M1 frame is truncated before any feature is
computed, so no holdout bar can influence a feature or emit an order. Order times are t = ts[i] + 60_000 for a
decision taken at the close of M1 bar i.

Provided (all arrays indexed by M1 bar, length n = number of pre-TEST bars):
  ts, o, h, l, c, mod (UTC min of day), ny, lon (DST-correct local min of day), dow
  A        Wilder ATR14 of M1 mid (data.atr)
  gate     entry allowed by the SYNTHESIS session gates: no entries 20:30-23:30 UTC, none after 19:00 UTC on
           Friday, none on Sunday (UTC) before 23:30 (the reopen hour), and none within [-5, +15] min of a
           HIGH calendar row (data.news_block_mask; the >= $21 equity window from SYNTHESIS N2, OPINION)
  eod_ms   16:45 ET of the bar's trading day (ms UTC): the intraday flat time
  htf(m)   dict for an HTF of m minutes: bars (o,h,l,c), atr14 on HTF bars, ki[k] = M1 index at whose close
           HTF bar k becomes known (complete), km[i] = last complete HTF bar at the close of M1 bar i (-1 if none)
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from data import TEST, _ms, atr, news_block_mask, resample_causal  # noqa: E402

TEST_MS = _ms(TEST[0])
LAST_FLAT_MS = TEST_MS - 60_000          # never hold a position into the holdout
SESSION_END_ET = 16 * 60 + 45            # flat by 16:45 ET (SYNTHESIS section 5 session gate)
DAY_MS = 86_400_000

_C: dict = {}


def base(m1: pd.DataFrame) -> dict:
    key = (len(m1), int(m1["ts"].values[0]), int(m1["ts"].values[-1]))
    if _C.get("key") == key:
        return _C["b"]
    _C.clear()
    n = int(np.searchsorted(m1["ts"].values, TEST_MS, side="left"))      # holdout cut
    m = m1.iloc[:n]
    ts = m["ts"].values.astype(np.int64)
    o, h, l, c = (m[k].values.astype(np.float64) for k in "ohlc")
    mod = m["mod"].values.astype(np.int64)
    ny = m["ny_mod"].values.astype(np.int64)
    lon = m["lon_mod"].values.astype(np.int64)
    dow = m["dow"].values.astype(np.int64)
    A = atr(m)
    gate = ~((mod >= 20 * 60 + 30) & (mod < 23 * 60 + 30))               # 20:30-23:30 UTC
    gate &= ~((dow == 4) & (mod >= 19 * 60))                              # Friday after 19:00 UTC
    gate &= ~((dow == 6) & (mod < 23 * 60 + 30))                          # Sunday reopen
    gate &= ~news_block_mask(ts, before_min=5, after_min=15)
    # 16:45 ET of the trading day (the day rolls at 17:00 ET): minutes until the next 16:45 ET
    mins_to = np.where(ny < SESSION_END_ET, SESSION_END_ET - ny, 1440 - ny + SESSION_END_ET)
    eod_ms = ts + mins_to * 60_000
    b = dict(n=n, m=m, ts=ts, o=o, h=h, l=l, c=c, mod=mod, ny=ny, lon=lon, dow=dow, A=A, gate=gate, eod_ms=eod_ms)
    _C["key"], _C["b"] = key, b
    return b


def htf(b: dict, minutes: int) -> dict:
    ck = ("htf", minutes)
    if ck in _C:
        return _C[ck]
    bars, known = resample_causal(b["m"], minutes)
    nk = len(bars)
    ki = np.searchsorted(known, np.arange(nk), side="left")               # M1 index where bar k is known
    out = dict(o=bars["o"].values, h=bars["h"].values, l=bars["l"].values, c=bars["c"].values,
               atr=atr(bars), ki=ki, km=known, nk=nk)
    _C[ck] = out
    return out


def cache(key, fn):
    """Generic per-process memo for derived arrays."""
    if key not in _C:
        _C[key] = fn()
    return _C[key]


def to_m1(km: np.ndarray, arr: np.ndarray) -> np.ndarray:
    """Map an HTF-bar array onto M1 bars through the causal `known` index (NaN before the first complete bar)."""
    out = arr[np.maximum(km, 0)].astype(np.float64)
    out[km < 0] = np.nan
    return out
