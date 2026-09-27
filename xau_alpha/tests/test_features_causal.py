"""Truncation-invariance checks for causal feature helpers on real data."""
import sys
from pathlib import Path

import numpy as np

X = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(X / "lib"))

from data import load_m1  # noqa: E402
from features import prev_day_hl, running_day_extremes, window_range  # noqa: E402


def _same(a, b):
    return np.array_equal(np.nan_to_num(a, nan=-1.0), np.nan_to_num(b, nan=-1.0))


def test_window_range_truncation_invariant():
    m = load_m1().iloc[:60_000]
    full = window_range(m["mod"].values, m["tday"].values, m["h"].values, m["l"].values, 0, 420)
    for cut in (7_000, 23_456, 41_111, 59_000):
        s = m.iloc[:cut]
        part = window_range(s["mod"].values, s["tday"].values, s["h"].values, s["l"].values, 0, 420)
        assert _same(full[0][:cut], part[0]) and _same(full[1][:cut], part[1])


def test_window_range_not_exposed_before_window():
    m = load_m1().iloc[:60_000]
    hi, _ = window_range(m["mod"].values, m["tday"].values, m["h"].values, m["l"].values, 0, 420)
    # bars whose trading day (NY 17:00 roll) is already the NEXT calendar date: the day has started but its
    # 00:00-07:00 UTC window has not happened yet
    utc_date = m["dt"].dt.strftime("%Y-%m-%d").values
    early = m["tday"].values != utc_date
    assert early.sum() > 1000
    assert np.isnan(hi[early]).all()
    inwin = m["mod"].values < 420
    assert np.isnan(hi[inwin]).all()


def test_running_and_prev_day_are_causal():
    m = load_m1().iloc[:30_000]
    a = running_day_extremes(m["tday"].values, m["h"].values, m["l"].values)
    b = prev_day_hl(m["tday"].values, m["h"].values, m["l"].values)
    s = m.iloc[:17_000]
    a2 = running_day_extremes(s["tday"].values, s["h"].values, s["l"].values)
    b2 = prev_day_hl(s["tday"].values, s["h"].values, s["l"].values)
    assert _same(a[0][:17_000], a2[0]) and _same(b[0][:17_000], b2[0])
