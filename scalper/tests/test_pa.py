"""The numpy fast paths must implement the SAME rules as the pandas
reference implementations (and be causal)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pa.fast import displacement_np, fvg_state_np, patterns_np
from pa.ict import displacement as disp_pd, fvg_state as fvg_pd
from pa.motive import BULLISH_PATTERNS, BEARISH_PATTERNS, NEUTRAL_PATTERNS
from tests.helpers import synth_with_swings

ALL_NAMES = list(NEUTRAL_PATTERNS) + list(BULLISH_PATTERNS) + list(BEARISH_PATTERNS)


def test_patterns_np_matches_pandas():
    df = synth_with_swings(2500, seed=9)
    tbl = patterns_np(df["open"].to_numpy(), df["high"].to_numpy(),
                      df["low"].to_numpy(), df["close"].to_numpy(), ALL_NAMES)
    from pa.motive import mw_patterns
    ref = mw_patterns(df)
    for name in ALL_NAMES:
        a = tbl[name][2:]
        b = ref[name].to_numpy(dtype=bool)[2:]
        assert (a == b).all(), f"{name} disagrees"
    # causality: appending bars never changes past values
    cut = synth_with_swings(2500, seed=9).iloc[:2000]
    tbl_cut = patterns_np(cut["open"].to_numpy(), cut["high"].to_numpy(),
                          cut["low"].to_numpy(), cut["close"].to_numpy(),
                          ALL_NAMES)
    for name in ALL_NAMES:
        assert (tbl[name][:2000] == tbl_cut[name]).all(), f"{name} not causal"


def test_fvg_np_matches_pandas_and_causal():
    df = synth_with_swings(4000, seed=5)
    a = fvg_state_np(df["high"].to_numpy(), df["low"].to_numpy(), 30)
    f = fvg_pd(df, 30)
    assert (a[0] == f["bull_fvg"].to_numpy(dtype=bool)).all()
    assert (a[4] == f["bear_fvg"].to_numpy(dtype=bool)).all()
    assert np.allclose(a[1], f["bull_ce"].to_numpy(), equal_nan=True)
    assert np.allclose(a[5], f["bear_ce"].to_numpy(), equal_nan=True)
    # causality
    cut = synth_with_swings(4000, seed=5).iloc[:3000]
    b = fvg_state_np(cut["high"].to_numpy(), cut["low"].to_numpy(), 30)
    for k in range(7):
        assert np.allclose(a[k][:3000], b[k], equal_nan=True)


def test_displacement_np_matches_pandas():
    df = synth_with_swings(3000, seed=3)
    u, d = displacement_np(df["open"].to_numpy(), df["high"].to_numpy(),
                           df["low"].to_numpy(), df["close"].to_numpy())
    p = disp_pd(df)
    assert (u == p["disp_up"].to_numpy(dtype=bool)).all()
    assert (d == p["disp_dn"].to_numpy(dtype=bool)).all()


def test_displacement_known_case():
    # 20 tiny-body bars then a 2x-body directional bar with no opposing wick
    n = 40
    o = np.full(n, 100.0)
    c = np.full(n, 100.1)
    h = np.maximum(o, c) + 0.05
    l = np.minimum(o, c) - 0.05
    c[-1] = 101.0          # body 1.0 >> avg 0.1, close > open
    h[-1] = 101.0          # no upper wick
    l[-1] = 100.0
    u, d = displacement_np(o, h, l, c)
    assert u[-1] and not d[-1]


def test_known_patterns():
    # bullish engulfing: bear bar (2) then bull bar (3) engulfing its body
    o = np.array([100.0, 101.0, 101.0, 99.5])
    c = np.array([101.0, 99.0, 100.0, 101.5])
    h = np.array([101.5, 101.5, 101.0, 102.0])
    l = np.array([99.5, 98.5, 98.0, 99.0])
    tbl = patterns_np(o, h, l, c, ["bullish_engulfing", "hammer"])
    # bar 2 bearish (c<o), bar 3 bullish, o[3]=99.5 <= c[2]=100, c[3]=101.5 >= o[2]=101
    assert tbl["bullish_engulfing"][3]
    assert not tbl["bullish_engulfing"][2]
