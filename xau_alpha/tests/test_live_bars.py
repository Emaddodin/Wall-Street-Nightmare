"""live.bars.derive must reproduce data.load_m1 columns exactly (parity of strategy inputs)."""
import sys
from pathlib import Path

import numpy as np

X = Path(__file__).resolve().parents[1]
for p in (X / "lib", X / "live"):
    sys.path.insert(0, str(p))

from bars import COLS, LiveBars, derive  # noqa: E402
from data import load_m1  # noqa: E402


def test_derive_matches_load_m1():
    m = load_m1()
    for a, b in ((0, 3000), (250_000, 253_000), (len(m) - 3000, len(m))):
        sl = m.iloc[a:b]
        d = derive(sl[COLS])
        for c in ["o", "h", "l", "c", "hour", "mod", "ny_mod", "lon_mod", "dow", "tday"]:
            assert (d[c].values == sl[c].values).all(), c


def test_bar_aggregation():
    lb = LiveBars(None)
    t = 1_767_225_600.0          # a minute boundary
    assert lb.on_quote(100.0, 100.2, t + 1) is None
    assert lb.on_quote(99.5, 99.8, t + 20) is None
    assert lb.on_quote(101.0, 101.3, t + 50) is None
    closed = lb.on_quote(100.5, 100.7, t + 61)
    assert closed == int(t * 1000)
    b = lb.bars[-1]
    assert (b["bo"], b["bh"], b["bl"], b["bc"]) == (100.0, 101.0, 99.5, 101.0)
    assert (b["ao"], b["ah"], b["al"], b["ac"]) == (100.2, 101.3, 99.8, 101.3)
    assert np.isclose(b["spr"], (0.2 + 0.3 + 0.3) / 3)
    assert lb.close_stale(t + 125) == int(t * 1000) + 60_000
