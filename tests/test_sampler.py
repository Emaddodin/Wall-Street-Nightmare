"""
The million-sample generator is only honest if its vectorised labeler
agrees with the engine's own walk_path, case for case -- and if the same
seed reproduces the same samples.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dataset import engine, sampler as S  # noqa: E402


def test_vectorised_labeler_matches_the_engine_case_by_case():
    rng = np.random.default_rng(11)
    for _ in range(25):
        n = 40
        fav = rng.uniform(-2, 8, (n, 96)).astype(np.float32)
        adv = rng.uniform(0, 6, (n, 96)).astype(np.float32)
        flip = rng.random(n) < 0.5
        reason, bars = S.label_paths(fav, adv, flip)
        for k in range(n):
            if flip[k]:
                bars_k = [(i, 1 + adv[k, i] / 100, 1 - fav[k, i] / 100)
                          for i in range(96)]
            else:
                bars_k = [(i, 1 + fav[k, i] / 100, 1 - adv[k, i] / 100)
                          for i in range(96)]
            got, _px, nheld, _best = engine.walk_path("BUY", 1.0, bars_k)
            want = {"target": 1, "stop": 2, "open": 0}[got]
            assert reason[k] == want, (k, got, reason[k])
            assert bars[k] == nheld


def test_spanning_bar_is_a_stop_in_the_vectorised_labeler():
    fav = np.array([[6.0, 0.0]], dtype=np.float32)
    adv = np.array([[2.0, 0.0]], dtype=np.float32)
    reason, bars = S.label_paths(fav, adv, np.array([False]))
    assert reason[0] == 2 and bars[0] == 1


def test_sampler_draw_is_deterministic_and_in_range():
    rows = [
        {"atr": 2.5, "trend": 1.0, "vol20": 1.0, "mom6h": 2.0,
         "mom1h": 0.5, "volx": 1.0, "score": 50.0, "hour": 3.0,
         "agents": 4.0, "tier": 3.0, "who": 3.0, "counter": 0.0,
         "side": 1.0, "hit": 0.0, "pts": 60.0},
        {"atr": 5.5, "trend": -2.0, "vol20": 2.0, "mom6h": -1.0,
         "mom1h": 1.5, "volx": 2.0, "score": 70.0, "hour": 12.0,
         "agents": 5.0, "tier": 2.0, "who": 1.0, "counter": 1.0,
         "side": -1.0, "hit": 1.0, "pts": 80.0},
    ]
    a = S.FeatureSampler(rows, [0, 1], np.random.default_rng(1))
    b = S.FeatureSampler(rows, [0, 1], np.random.default_rng(1))
    Xa, pa, ba = a.draw(50)
    Xb, pb, bb = b.draw(50)
    assert Xa.shape == (50, len(S.FEATURES))
    assert np.array_equal(Xa, Xb, equal_nan=True)
    assert np.array_equal(pa, pb)
    assert np.array_equal(ba, bb)
    assert set(np.unique(pa)).issubset({0, 1})
    for f in S.CONT:
        col = Xa[:, S.FEATURES.index(f)][~np.isnan(
            Xa[:, S.FEATURES.index(f)])]
        assert col.min() >= a.lo[f] and col.max() <= a.hi[f]


def test_sampler_draw_marks_missingness():
    rows = [
        {"atr": None, "trend": 1.0, "vol20": 1.0, "mom6h": 2.0,
         "mom1h": 0.5, "volx": 1.0, "score": 50.0, "hour": 3.0,
         "agents": 4.0, "tier": 3.0, "who": 3.0, "counter": 0.0,
         "side": 1.0, "hit": 0.0, "pts": 60.0},
    ] * 10
    s = S.FeatureSampler(rows, [0] * 10, np.random.default_rng(2))
    X, pids, _boundary = s.draw(200)
    # atr is missing in every anchor row, so its rate is 1.0
    assert np.isnan(X[:, S.FEATURES.index("atr")]).all()
    assert set(np.unique(pids)) <= set(range(10))
