"""
The risk tool is arithmetic, and arithmetic must be checked.

The tool walks trades.jsonl like one compounding wallet. These tests feed
small hand-written records and assert the numbers by hand, so a change to
the tool cannot silently change what it reports.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from riskanalysis import equity_path, naive_path, max_drawdown, streaks  # noqa: E402


def trade(pnl: float, reason: str = "target", closed: float = 1.0) -> dict:
    return {"pnl": pnl, "reason": reason, "closed": closed,
            "path_best_pct": 0.0}


def test_compounding_path_scales_each_trade():
    # +50 on 100, then -34.25 on 150: the live book sizes half the
    # CURRENT equity, so the second trade's dollar PnL scales.
    path = equity_path([trade(50.0), trade(-34.25)])
    assert path == [100.0, 150.0, 150.0 * (1 - 0.3425)]


def test_naive_path_sums_flat():
    assert naive_path([trade(50.0), trade(-34.25)]) == [100.0, 150.0, 115.75]


def test_max_drawdown_finds_peak_and_trough():
    dd, peak_i, trough_i = max_drawdown([100, 120, 90, 60, 110])
    # peak at 120 (index 1), trough at 60 (index 3): 60/120 = 50%
    assert abs(dd - 0.5) < 1e-9
    assert (peak_i, trough_i) == (1, 3)


def test_streaks_count_stop_runs():
    s = streaks(["stop", "target", "stop", "stop", "stop", "target",
                 "stop", "stop"])
    assert s["longest"] == 3
    assert s["runs_of_2"] == 2   # the 3-run's tail and the final pair
    assert s["runs_of_3"] == 1
    assert s["runs_of_4"] == 0
