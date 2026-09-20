"""
The sweep is arithmetic over recorded data; its helpers must be checked.

Kelly and the ruin simulation decide what the sweep advises, so a change
to either must fail a test rather than silently move the advice.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dataset.sweep import kelly, ruin_sim  # noqa: E402


def test_kelly_is_zero_below_break_even():
    # break-even at 4R is 1/(1+4) = 20%; below it there is no bet.
    assert kelly(0.08) == 0.0
    assert kelly(0.15) == 0.0
    assert kelly(0.1999) == 0.0


def test_kelly_fraction_at_known_rates():
    # p=0.5, odds b=122/34.25: f* = (b*p - q)/b
    b = 122 / 34.25
    got = kelly(0.5)
    assert abs(got - (b * 0.5 - 0.5) / b) < 1e-12
    assert 0.0 < kelly(0.245) < 0.05      # the study's rate: a small bet
    assert abs(kelly(0.39) - 0.219) < 0.01


def test_ruin_sim_is_deterministic_and_bounded():
    a = ruin_sim(0.24, 0.05, seed=7)
    b = ruin_sim(0.24, 0.05, seed=7)
    assert a == b
    assert 0.0 <= a["ruin_lt_1pct"] <= 1.0
    assert a["median"] > 0


def test_a_losing_game_ruins_at_any_real_risk():
    for risk in (0.05, 0.10, 0.25):
        got = ruin_sim(0.08, risk, seed=7)
        assert got["median"] < 0.05, risk


def test_a_strong_edge_compounds_with_small_risk():
    got = ruin_sim(0.39, 0.05, seed=7)
    assert got["median"] > 100, "a 39% hit rate at 4R should compound hard"
    assert got["ruin_lt_1pct"] == 0.0
