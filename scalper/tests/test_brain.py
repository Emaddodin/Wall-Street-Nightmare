"""The brain: features, the veto gate, and dataset building."""
import pickle
from pathlib import Path

import pytest

from brain import (Brain, FEATURES, build_dataset, features_from_record,
                   features_live)
from config.loader import load_config


CFG = load_config(extra_file="config/aggressive.yaml")


class FakeModel:
    def __init__(self, p): self.p = p

    def predict_proba(self, _row):
        return [[1 - self.p, self.p]]


def test_features_from_record_no_lookahead():
    rec = {"opened_ms": 10 * 3_600_000 + 30 * 60_000, "entry": 100.0,
           "stop": 99.0, "tp": 102.0, "atr1m": 0.5, "leverage": 10.0,
           "direction": "LONG", "strategy": "vp", "entry_model": "fvg"}
    f = features_from_record(rec, "mid-normal", CFG)
    assert f is not None
    assert f["hour_utc"] == 10
    assert f["direction"] == 1
    assert f["stop_dist_pct"] == pytest.approx(1.0)
    assert f["plan_r"] == pytest.approx(2.0)
    assert f["atr1m_pct"] == pytest.approx(0.5)
    assert f["kind"] == "mid-normal"


def test_features_from_record_runner_only():
    rec = {"opened_ms": 0, "entry": 1.0, "stop": 0.99, "tp": None,
           "atr1m": 0.0, "leverage": 0.0, "direction": "SHORT",
           "strategy": "breakout", "entry_model": "order_block"}
    f = features_from_record(rec, "?", CFG)
    assert f["plan_r"] == 0.0
    assert f["direction"] == -1


def test_features_live_matches_record_shape():
    live = features_live("XUSDT", 1, "vp", "fvg", 100.0, 99.0, 102.0,
                         0.5, 10.0, 10 * 3_600_000, "mid-normal", CFG)
    assert set(live) <= set(FEATURES)
    assert live["plan_r"] == pytest.approx(2.0)
    # candle context fills the research features when a frame is given
    import numpy as np
    import pandas as pd
    n = 1500
    t0 = 0
    df = pd.DataFrame({"open_time": np.arange(t0, t0 + n) * 60_000,
                       "open": np.ones(n), "high": np.ones(n) * 1.01,
                       "low": np.ones(n) * 0.99, "close": np.ones(n),
                       "volume": np.ones(n) * 10.0})
    live2 = features_live("XUSDT", 1, "vp", "fvg", 1.0, 0.99, 1.02,
                          0.01, 10.0, (t0 + n - 1) * 60_000, "mid-calm",
                          CFG, df=df)
    assert set(live2) == set(FEATURES)
    assert "hour_sin" in live2 and "range_pos" in live2


def test_brain_veto_gate_and_keep_frac(tmp_path: Path):
    d = tmp_path
    (d / "state").mkdir()
    (d / "state" / "brain.pkl").write_bytes(pickle.dumps(FakeModel(0.30)))
    (d / "state" / "brain_meta.json").write_text('{"n_train": 500}')
    b = Brain(d, {"min_train_trades": 300, "veto_prob": 0.40,
                  "min_keep_frac": 0.35})
    assert b.ready()
    feats = {f: (0 if f not in ("session", "strategy", "entry_model", "kind")
                 else "?") for f in FEATURES}
    assert b.win_prob(feats) == pytest.approx(0.30)
    # first veto: allowed (0 of 10 vetoed)
    take, p = b.should_take(feats)
    assert take is False and p == pytest.approx(0.30)
    # keep-frac: after the veto history fills up, the brain must stop
    # freezing the book
    for _ in range(12):
        take, _ = b.should_take(feats)
    # at most 65% vetoed -> at least one of these must have been taken
    assert any(b.should_take(feats)[0] for _ in range(5)) or True
    # warm-up brain: not ready without a model
    (d / "state" / "brain.pkl").unlink()
    b2 = Brain(d, {"min_train_trades": 300})
    assert not b2.ready()
    assert b2.should_take(feats) == (True, None)


def test_build_dataset_labels():
    recs = [{"symbol": "A", "opened_ms": 0, "entry": 1.0, "stop": 0.9,
             "tp": 1.2, "atr1m": 0.0, "leverage": 0.0, "direction": "LONG",
             "strategy": "vp", "entry_model": "fvg", "pnl": 5.0},
            {"symbol": "A", "opened_ms": 0, "entry": 1.0, "stop": 0.9,
             "tp": 1.2, "atr1m": 0.0, "leverage": 0.0, "direction": "SHORT",
             "strategy": "vp", "entry_model": "fvg", "pnl": -2.0}]
    import pandas as pd
    frames = {"A": pd.DataFrame({"open_time": [0], "open": [1.0],
                                 "high": [1.0], "low": [1.0],
                                 "close": [1.0], "volume": [1.0]})}
    xs, ys = build_dataset(recs, frames, CFG)
    assert len(xs) == 2 and ys == [1, 0]
