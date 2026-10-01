"""Causality checks for cand/ml_meta.py (X3, machine-learned entry filter).

1. Feature truncation invariance: every feature / gate at a decision point is identical whether or not later M1 bars
   exist (so no feature looks ahead). Runs on the first ~5 months of 2025 with three cut points.
2. (slow, RUN_SLOW=1) The fitted model ignores everything from 2026-01-01 on: predictions for every decision point
   before 2026-01-01 are bit-identical when the M1 data is truncated at 2026-01-01.
"""
import os
import sys
from pathlib import Path

import numpy as np
import pytest

X = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(X / "lib"))
sys.path.insert(0, str(X / "cand"))

from data import _ms, load_m1  # noqa: E402
import ml_meta as M  # noqa: E402

KEYS = ("X", "A5", "gate", "near_ev_ms", "to_flat", "td", "K")


def _eq(a, b):
    return np.array_equal(np.nan_to_num(a, nan=-999.0), np.nan_to_num(b, nan=-999.0))


def test_features_truncation_invariant():
    m = load_m1().iloc[:140_000].reset_index(drop=True)
    full = M.build_features(m)
    for cut in (61_237, 97_531, 131_001):
        part = M.build_features(m.iloc[:cut].reset_index(drop=True))
        # the decision points before the cut are exactly the same set
        assert np.array_equal(full["I"][full["I"] < cut], part["I"])
        jf = np.searchsorted(full["I"], part["I"])
        for k in KEYS:
            assert _eq(full[k][jf], part[k]), (cut, k)


def test_features_truncation_targeted_times():
    """Cuts placed where a look-ahead would show: inside the Asia window (03:00 UTC), just after it (07:02), in the
    21:00-23:59 UTC stretch where the old window_range leaked the upcoming Asia range (22:31), just after the NY
    17:00 roll, and in the middle of an M5 bucket (mod % 5 == 2). Uses 2025-03..2025-05 so the 20-day medians and
    zones are warm."""
    m = load_m1()
    m = m[(m["ts"].values >= _ms("2025-03-03")) & (m["ts"].values < _ms("2025-05-10"))].reset_index(drop=True)
    full = M.build_features(m)
    mod, dow = m["mod"].values, m["dow"].values
    cuts = []
    for target in (180, 422, 1351, 1262, 602):         # 03:00, 07:02, 22:31, 21:02, 10:02 UTC
        idx = np.flatnonzero((mod == target) & (dow < 4))
        cuts += [int(idx[len(idx) // 3]) + 1, int(idx[2 * len(idx) // 3]) + 1]
    assert len(cuts) == 10
    for cut in cuts:
        part = M.build_features(m.iloc[:cut].reset_index(drop=True))
        assert np.array_equal(full["I"][full["I"] < cut], part["I"]), cut
        jf = np.searchsorted(full["I"], part["I"])
        for k in KEYS:
            assert _eq(full[k][jf], part[k]), (cut, k)


def test_asia_features_hidden_before_0700_utc():
    """pos_asia / asia_rng must be NaN at every decision point before 07:00 UTC (the window is not over)."""
    m = load_m1()
    m = m[(m["ts"].values >= _ms("2025-03-03")) & (m["ts"].values < _ms("2025-04-05"))].reset_index(drop=True)
    b = M.build_features(m)
    modI = m["mod"].values[b["I"]]
    j = M.FNAMES.index("asia_rng")
    early = modI < 420
    assert early.sum() > 1000
    assert np.isnan(b["X"][early, j]).all()
    assert np.isfinite(b["X"][~early, j]).mean() > 0.9


def test_mirror_is_an_involution():
    rng = np.random.default_rng(0)
    Xr = rng.normal(size=(200, len(M.ALL_NAMES))).astype(np.float32)
    assert np.allclose(M.mirror_X(M.mirror_X(Xr)), Xr)


@pytest.mark.skipif(not os.environ.get("RUN_SLOW"), reason="slow: two model fits")
def test_model_ignores_data_from_2026():
    os.environ.pop("ML_META_CACHE", None)
    m = load_m1()
    P1 = M.predictions(m, 1.0, 60, "cap", "mirror")
    b1 = M._base(m)
    I1, PL1, PS1, td1 = b1["I"].copy(), P1["PL"].copy(), P1["PS"].copy(), b1["td"].copy()
    it1 = P1["diag"]["best_iter"]
    mc = m[m["ts"].values < _ms("2026-01-01")].reset_index(drop=True)
    P2 = M.predictions(mc, 1.0, 60, "cap", "mirror")
    b2 = M._base(mc)
    assert P2["diag"]["best_iter"] == it1
    keep = td1 < M.CAL_END
    assert np.array_equal(I1[keep], b2["I"][b2["td"] < M.CAL_END])
    j = np.searchsorted(b2["I"], I1[keep])
    assert np.array_equal(PL1[keep], P2["PL"][j]) and np.array_equal(PS1[keep], P2["PS"][j])
