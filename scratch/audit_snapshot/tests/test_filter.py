"""
The learned filter: a candidate below the model's floor is refused with
its own reason, a candidate above it logs its probability, and a model
that will not load is a refusal to start -- never a silent no-op.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import filter_model as FM  # noqa: E402
from dataset import scenarios  # noqa: E402
from dataset.make_dataset import run_scenario  # noqa: E402


def artifact(tmp_path, w=None, b=0.0):
    n = len(FM.FEATURES)
    path = tmp_path / "model.npz"
    np.savez_compressed(
        path,
        w=np.zeros(2 * n, dtype=np.float32) if w is None else w,
        b=float(b),
        mu=np.zeros(2 * n, dtype=np.float32),
        sd=np.ones(2 * n, dtype=np.float32),
        med=np.zeros(n, dtype=np.float32),
        features=np.array(FM.FEATURES),
        threshold=0.5, trained_at=0, n=1)
    return path


def feats(**kw):
    out = {f: (1.0 if f not in ("side",) else 1.0) for f in FM.FEATURES}
    out.update(kw)
    return out


def test_model_loads_and_scores_deterministically(tmp_path):
    m = FM.FilterModel(str(artifact(tmp_path)))
    p1 = m.score(feats())
    p2 = m.score(feats())
    assert p1 == p2 == 0.5          # zero weights: sigmoid(0)


def test_missing_features_are_imputed_not_invented(tmp_path):
    m = FM.FilterModel(str(artifact(tmp_path)))
    p = m.score(feats(atr=None, volx=None))
    assert p == 0.5


def test_feature_mismatch_refuses_to_load(tmp_path):
    path = tmp_path / "bad.npz"
    np.savez_compressed(path, w=np.zeros(26, dtype=np.float32), b=0.0,
                        mu=np.zeros(26, dtype=np.float32),
                        sd=np.ones(26, dtype=np.float32),
                        med=np.zeros(13, dtype=np.float32),
                        features=np.array(["other"] * 13),
                        threshold=0.5, trained_at=0, n=1)
    with pytest.raises(ValueError):
        FM.FilterModel(str(path))


def test_the_book_refuses_a_candidate_the_model_rejects(tmp_path):
    sc = [s for s in scenarios.SCENARIOS
          if s["name"] == "filter_refused"][0]
    book, kw, sig, clock, st = run_scenario(sc, str(tmp_path))
    assert any("filter: the model gives it" in ln for ln in book.log), \
        "the refusal must carry the model's own reason"
    assert not any(t.get("closed") is None and t.get("sym") == sig["sym"]
                   and t.get("opened") for t in
                   book.state.get("trades", [])), "nothing was opened"


def test_the_book_refuses_to_start_on_a_missing_model():
    import papertrade as P
    argv = sys.argv
    try:
        with mock.patch.object(sys, "argv", [
                "papertrade.py", "--filter-model",
                "/nonexistent/model.npz", "--filter-min", "0.5"]):
            rc = P.main()
    finally:
        sys.argv = argv
    assert rc == 1


def test_off_by_default_takes_nothing_from_anyone():
    """--filter-min 0 loads the model and refuses nothing (a passing
    candidate still logs its probability)."""
    sc = [s for s in scenarios.SCENARIOS
          if s["name"] == "baseline_trade"][0]
    book, kw, sig, clock, st = run_scenario(sc)
    assert any("OPEN" in ln for ln in book.log)
