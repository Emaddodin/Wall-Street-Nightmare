"""
The fav/adv convention, pinned down by the recorder's own construction.

outcomes.jsonl's fav/adv are RELATIVE to the recorded signal's side: for
a SELL, fav is the DOWN move and adv the UP move (recorder.py computes
exactly that). Any code that builds bars must orient by side, or the
SELL labels invert -- which is what happened until 2026-09-06, when
sources.path_bars ignored the side. These tests reproduce the inversion
so it cannot come back.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dataset import engine, sources  # noqa: E402


def _sell_outcome():
    """A synthetic SELL recording, built the way recorder.py builds them:
    fav = (entry - low)/entry, adv = (high - entry)/entry."""
    entry = 1.0
    fav, adv, cls = [], [], []
    for i in range(96):
        up = 0.5 + 0.2 * (i % 5)          # the real high, percent
        dn = 0.3 + 0.1 * (i % 7)          # the real low, percent
        fav.append(round(dn, 3))
        adv.append(round(up, 3))
        cls.append(round(-dn + 0.5 * up, 3))   # a close somewhere between
    return {"fav": fav, "adv": adv, "cls": cls, "entry": entry}


def test_recorded_fav_adv_are_side_relative():
    """The recorder's own formula: for a SELL, fav is the down move."""
    import recorder
    src = Path(recorder.__file__).read_text()
    assert "((w.high - e) / e * 100) if long else ((e - w.low) / e * 100)" \
        in src, "recorder fav semantics changed -- check every consumer"


def test_path_bars_orients_bars_by_side():
    out = _sell_outcome()
    entry = out["entry"]
    bars = sources.path_bars(out, "SELL", entry)
    for i, hi, lo in bars:
        assert abs(hi - entry * (1 + out["adv"][i] / 100)) < 1e-9, \
            "the SELL high must come from adv (the up move)"
        assert abs(lo - entry * (1 - out["fav"][i] / 100)) < 1e-9, \
            "the SELL low must come from fav (the down move)"
    bars_b = sources.path_bars(out, "BUY", entry)
    for i, hi, lo in bars_b:
        assert abs(hi - entry * (1 + out["fav"][i] / 100)) < 1e-9
        assert abs(lo - entry * (1 - out["adv"][i] / 100)) < 1e-9


def test_sell_labels_are_not_inverted():
    """A SELL whose path first goes DOWN 5% then UP 1.5% must be a TARGET
    (down hits +5% first), never a stop."""
    out = {"fav": [5.0, 0.2], "adv": [0.5, 1.5], "cls": [-2.0, 0.5],
           "entry": 1.0}
    bars = sources.path_bars(out, "SELL", 1.0)
    reason, _px, nheld, _best = engine.walk_path("SELL", 1.0, bars)
    assert reason == "target" and nheld == 1
    # and the mirrored case: up 1.5% first -> stop
    out2 = {"fav": [0.2, 5.0], "adv": [1.5, 0.5], "cls": [0.5, -2.0],
            "entry": 1.0}
    bars2 = sources.path_bars(out2, "SELL", 1.0)
    reason2, _px2, nheld2, _b2 = engine.walk_path("SELL", 1.0, bars2)
    assert reason2 == "stop" and nheld2 == 1


def test_sampler_flip_rule_agrees_with_side_relative_semantics():
    """label_paths + the sampler's XOR flip reproduces the true bars for
    all four anchor/sample pairings."""
    from dataset import sampler as S
    anchor_buy = (np.array([[2.0, 0.0]], dtype=np.float32),   # fav: up 2%
                  np.array([[1.0, 0.0]], dtype=np.float32))   # adv: down 1%
    anchor_sell = (np.array([[1.0, 0.0]], dtype=np.float32),  # fav: down 1%
                   np.array([[2.0, 0.0]], dtype=np.float32))  # adv: up 2%
    for (fav, adv), pside in ((anchor_buy, False), (anchor_sell, True)):
        for sample_sell in (False, True):
            flip = np.array([pside != sample_sell])
            reason, bars = S.label_paths(fav, adv, flip)
            # the true absolute range
            if pside:
                hi_true, lo_true = 1 + adv[0, 0] / 100, 1 - fav[0, 0] / 100
            else:
                hi_true, lo_true = 1 + fav[0, 0] / 100, 1 - adv[0, 0] / 100
            # for a BUY sample: stop=lo<=0.9875, target=hi>=1.05
            # for a SELL sample: stop=hi>=1.0125, target=lo<=0.95
            if sample_sell:
                want = 1 if lo_true <= 0.95 else 2 if hi_true >= 1.0125 else 0
            else:
                want = 1 if hi_true >= 1.05 else 2 if lo_true <= 0.9875 else 0
            assert reason[0] == want, (pside, sample_sell, reason[0], want)
