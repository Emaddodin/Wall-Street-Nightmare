"""
The learned selector must never see its test day, and its arithmetic must
be checked -- the walk-forward split is the whole honesty of the claim.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dataset import selector as S  # noqa: E402


def _rows():
    return [
        {"t": 1788000000 + i * 86400, "hit": float(i % 2), "atr": 2.0,
         "trend": 1.0, "vol20": 1.0, "mom6h": 1.0, "mom1h": 1.0,
         "volx": 1.0, "agents": 4.0, "tier": 3.0, "score": 50.0,
         "who": 3.0, "counter": 0.0, "side": 1.0, "hour": 3.0,
         "pts": 60.0}
        for i in range(12)
    ]


def test_split_by_day_never_leaks_the_future():
    train, test = S.split_by_day(_rows(), test_days=1)
    assert train and test
    assert max(r["t"] for r in train) < min(r["t"] for r in test)


def test_matrix_carries_missingness_flags():
    rows = _rows()
    rows[0]["atr"] = None
    rows[1]["volx"] = None
    X, y = S.build_matrix(rows)
    assert X.shape == (len(rows), 2 * len(S.FEATURES))
    assert X[0, len(S.FEATURES)] == 1.0      # atr missing flag
    assert X[0, 0] == 0.0                    # and the value slot is empty
    assert X[1, len(S.FEATURES) + 5] == 1.0  # volx missing flag
    assert len(y) == len(rows)


def test_median_impute_leaves_no_gaps():
    rows = _rows()
    for r in rows[::3]:
        r["mom6h"] = None
    X, _ = S.build_matrix(rows)
    X, _ = S.median_impute(X, X)
    assert not np.isnan(X).any()


def test_logistic_recovers_a_separable_signal():
    rng = np.random.default_rng(3)
    X = rng.normal(size=(400, 4))
    y = (X[:, 0] > 0).astype(float)
    X, _, _mu, _sd = S.standardize(X, X)
    w, b = S.logistic(X, y, iters=400, lr=0.2, seed=1)
    p = S.predict_proba(X, w, b)
    assert S.auc(y, p) > 0.99


def test_auc_half_for_noise_one_for_perfect():
    rng = np.random.default_rng(5)
    y = rng.integers(0, 2, 500).astype(float)
    assert abs(S.auc(y, rng.random(500)) - 0.5) < 0.05
    assert S.auc(y, y) == 1.0


def test_top_slice_picks_the_highest_scored():
    y = np.array([1.0, 0.0, 1.0, 0.0, 1.0])
    score = np.array([0.9, 0.1, 0.8, 0.2, 0.7])
    hit, n = S.top_slice_hit(y, score, frac=0.4)
    assert n == 2 and hit == 1.0
