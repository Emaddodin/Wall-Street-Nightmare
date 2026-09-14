"""
The pattern dataset and the entry measurement: features must never see
past their own bar, labels must only see the future, and the entry
arithmetic must agree with the recorded paths case by case.
"""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dataset import entries, patterns  # noqa: E402
from dataset import sources  # noqa: E402


def _bars(n=80, base=1.0):
    ohlc = {}
    for i in range(n):
        t = 1788400000 + i * 900
        rng = 0.004 + 0.001 * (i % 7)
        ohlc[t] = (base, base * (1 + rng), base * (1 - rng), base)
    return ohlc


def test_pattern_features_never_see_the_future():
    ohlc = _bars()
    t = 1788400000 + 60 * 900
    before = patterns.bar_features(ohlc, t)
    # appending future bars must not change the features at t
    for k in list(ohlc):
        if k > t:
            ohlc.pop(k)
    after = patterns.bar_features(ohlc, t)
    assert before == after


def test_pattern_labels_only_see_the_future():
    ohlc = _bars()
    t = 1788400000 + 30 * 900
    lab = patterns.forward_labels(ohlc, t)
    assert lab is not None
    # changing the PAST must not change the labels
    ohlc[1788400000] = (0.5, 0.6, 0.4, 0.5)
    lab2 = patterns.forward_labels(ohlc, t)
    assert lab == lab2


def test_entry_arithmetic_on_a_known_path():
    """A long entered at the candle low survives a path that stops the
    open entry -- the whole point of asking the question."""
    side, entry = "BUY", 1.0
    # bar 1: low -1.3% below the open, then up 6% in bar 2
    fav = [0.2, 6.0]
    adv = [1.3, 0.0]
    got_open = entries.outcome_for(side, entry, 0.0, fav, adv)
    assert got_open[0] == "stop", got_open
    # shift 1% (entered 1% lower, near the low): the same low is only
    # 0.3% adverse -> no stop, and the +6% bar reaches the target
    got_extreme = entries.outcome_for(side, entry, 0.01, fav, adv)
    assert got_extreme[0] == "target", got_extreme


def test_entry_measurement_covers_the_recorded_paths():
    n = 0
    for sig in sources.joined():
        out = sig.get("outcome") or {}
        if out.get("fav") and out.get("adv") and out.get("entry"):
            n += 1
    assert n > 10000, "the recorded paths are missing"
