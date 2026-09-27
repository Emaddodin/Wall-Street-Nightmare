"""Unit tests for the execution simulator on hand-built 10s bars."""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from sim import Cost, simulate  # noqa: E402

Z = Cost(0, 1, 0, 0, 0, 0)


def bars(mids, spread=0.2, hl=None):
    """Build bid/ask 10s arrays from a list of (o,h,l,c) mids."""
    n = len(mids)
    ts = np.arange(n, dtype=np.int64) * 10_000 + 1_000_000_000_000
    m = np.array(mids, dtype=float)
    a = {"ts": ts}
    for j, c in enumerate("ohlc"):
        a["b" + c] = m[:, j] - spread / 2
        a["a" + c] = m[:, j] + spread / 2
    return a


def test_long_tp_and_costs():
    a = bars([(100, 100, 100, 100), (100, 101, 99.9, 101), (101, 103, 100.5, 102.5)])
    o = {"t": a["ts"][0], "d": 1, "kind": "mkt", "sl_dist": 1.0, "tp_dist": 2.0}
    tr = simulate([o], Z, arr=a)
    r = tr.iloc[0]
    assert math.isclose(r.entry, 100.1)          # filled at ask open
    assert r.reason == "tp" and math.isclose(r.exit, 102.1)
    assert math.isclose(r.pnl, 2.0)


def test_sl_priority_same_bar():
    a = bars([(100, 100, 100, 100), (100, 100, 100, 100), (100, 103, 98, 100)])
    o = {"t": a["ts"][0], "d": 1, "kind": "mkt", "sl_dist": 1.0, "tp_dist": 1.0}
    r = simulate([o], Z, arr=a).iloc[0]
    assert r.reason == "sl" and math.isclose(r.pnl, -1.0)


def test_short_mirror_and_gap():
    a = bars([(100, 100, 100, 100), (100, 100, 100, 100), (103, 104, 102, 103)])
    o = {"t": a["ts"][0], "d": -1, "kind": "mkt", "sl_dist": 1.0}
    r = simulate([o], Z, arr=a).iloc[0]
    assert math.isclose(r.entry, 99.9)           # short filled at bid
    assert r.reason == "sl" and math.isclose(r.exit, 103.1)   # gapped through: filled at ask open
    assert math.isclose(r.pnl, 99.9 - 103.1)


def test_trailing_uses_previous_bars_only():
    mids = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 105, 100, 105), (105, 105, 103.5, 104)]
    a = bars(mids, spread=0.0)
    o = {"t": a["ts"][0], "d": 1, "kind": "mkt", "sl_dist": 2.0, "trail": 1.0, "trail_act": 1.0}
    r = simulate([o], Z, arr=a).iloc[0]
    assert r.reason == "trail" and math.isclose(r.exit, 104.0)   # peak 105 on bar 2, stop 104 active on bar 3


def test_stop_entry_and_expiry():
    a = bars([(100, 100, 100, 100), (100, 100.5, 99.5, 100), (100, 102, 100, 101.8), (101.8, 104, 101.5, 104)], spread=0.0)
    o = {"t": a["ts"][0], "d": 1, "kind": "stop", "px": 101.0, "exp": a["ts"][3], "sl_dist": 1.5, "tp_dist": 2.0}
    r = simulate([o], Z, arr=a).iloc[0]
    assert math.isclose(r.entry, 101.0) and r.reason == "tp" and math.isclose(r.exit, 103.0)
    o2 = dict(o, px=110.0)
    assert len(simulate([o2], Z, arr=a)) == 0


def test_no_fill_before_decision_time():
    a = bars([(100, 100, 100, 100), (100, 100, 100, 100), (200, 200, 200, 200)], spread=0.0)
    o = {"t": a["ts"][1] + 1, "d": 1, "kind": "mkt", "sl_dist": 1.0}
    r = simulate([o], Z, arr=a).iloc[0]
    assert math.isclose(r.entry, 200.0)


def test_one_at_a_time_and_commission():
    a = bars([(100, 100, 100, 100)] * 3 + [(100, 101, 100, 101)] * 3, spread=0.0)
    c = Cost(0, 1, 0, 0, 0, com_rt_lot=7.0)
    o1 = {"t": a["ts"][0], "d": 1, "kind": "mkt", "sl_dist": 5.0, "tp_dist": 1.0}
    o2 = {"t": a["ts"][1], "d": 1, "kind": "mkt", "sl_dist": 5.0, "tp_dist": 1.0}
    tr = simulate([o1, o2], c, arr=a)
    assert len(tr) == 1 and math.isclose(tr.iloc[0].pnl, 1.0 - 0.07)
