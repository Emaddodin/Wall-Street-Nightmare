"""Indicator correctness against hand-computed / reference values."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config.loader import load_config
from indicators import adx, atr, ema, swing_points

cfg = load_config()


def test_ema_known():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    out = ema(s, 3)
    # ewm span=3, min_periods=3: first value at index 2
    assert np.isnan(out.iloc[0]) and np.isnan(out.iloc[1])
    assert out.iloc[2] == pytest.approx(2.25)
    assert out.iloc[3] == pytest.approx(3.125)
    assert out.iloc[4] == pytest.approx(4.0625)


def test_atr_wilder_seed_and_recursion():
    df = pd.DataFrame({
        "high": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0],
        "low": [9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0],
        "close": [9.5, 10.5, 11.5, 12.5, 13.5, 14.5, 15.5],
    })
    a = atr(df, 3)
    # TR: bar0 = 1; bars 1+ = max(1, 1.5, 0.5) = 1.5
    assert a.iloc[2] == pytest.approx((1.0 + 1.5 + 1.5) / 3)   # SMA seed
    # Wilder recursion after the seed
    assert a.iloc[3] == pytest.approx((a.iloc[2] * 2 + 1.5) / 3)
    assert a.iloc[4] == pytest.approx((a.iloc[3] * 2 + 1.5) / 3)


def test_atr_matches_reference_wilder():
    rng = np.random.default_rng(5)
    n = 200
    h = 100 + np.cumsum(rng.normal(0, 1, n)) + np.abs(rng.normal(0, 0.5, n))
    l = 100 + np.cumsum(rng.normal(0, 1, n)) - np.abs(rng.normal(0, 0.5, n))
    c = 100 + np.cumsum(rng.normal(0, 1, n))
    df = pd.DataFrame({"high": h, "low": l, "close": c})
    got = atr(df, 14).to_numpy()
    # independent reference implementation
    tr = np.empty(n)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    ref = np.full(n, np.nan)
    prev = tr[:14].mean()
    ref[13] = prev
    for i in range(14, n):
        prev = (prev * 13 + tr[i]) / 14
        ref[i] = prev
    assert np.allclose(got[13:], ref[13:], equal_nan=True)


def test_swing_confirmation_lag():
    # valley at bar 10, flanked by higher lows
    n = 30
    lows = np.full(n, 10.0)
    lows[10] = 5.0
    highs = np.full(n, 15.0)
    df = pd.DataFrame({"high": highs, "low": lows, "close": (highs + lows) / 2,
                       "open": 12.0})
    sh, sl = swing_points(df, arm=2)
    # swing low confirmed at bar 10+2
    assert sl.iloc[12] == pytest.approx(5.0)
    # nothing known before the confirmation bar
    assert np.isnan(sl.iloc[11])


def test_swing_high_confirmation_lag():
    n = 30
    highs = np.full(n, 10.0)
    highs[10] = 20.0
    lows = np.full(n, 5.0)
    df = pd.DataFrame({"high": highs, "low": lows, "close": 8.0, "open": 7.0})
    sh, sl = swing_points(df, arm=2)
    assert sh.iloc[12] == pytest.approx(20.0)
    assert np.isnan(sh.iloc[11])


def test_adx_finite_and_bounded():
    from tests.helpers import synth_1m
    df = synth_1m(500)
    a = adx(df, 14)
    assert a.notna().sum() > 100
    assert (a.dropna() >= 0).all()
    assert (a.dropna() <= 100).all()
