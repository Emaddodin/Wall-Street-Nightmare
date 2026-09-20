"""
The one dial that decides.

`--min-confidence 70` is the only thing standing between a shape and half the
wallet at fifty times, so what this number does when an input is missing
matters as much as what it does when everything is present.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import papertrade as P
from papertrade import CONF_WEIGHTS, confidence

ROOT = Path(__file__).resolve().parent.parent


def test_a_perfect_setup_on_a_coin_that_travels_scores_full_marks():
    conf, _ = confidence(tall=1.8, agree=6, against=0, reach=50.0, smooth=0.5)
    assert conf == pytest.approx(100.0)


def test_a_coin_that_travels_scores_higher_than_one_that_does_not():
    """Reach is worth points again, by degrees rather than as a gate."""
    nowhere, _ = confidence(tall=1.8, agree=6, against=0, reach=0.0, smooth=0.0)
    anywhere, _ = confidence(tall=1.8, agree=6, against=0, reach=54.0, smooth=0.9)
    assert anywhere > nowhere
    assert P.CONF_WEIGHTS["reach"] == 35.0


def test_a_council_leaning_the_wrong_way_costs_more_than_it_gives():
    with_it, _ = confidence(tall=1.2, agree=4, against=0, reach=20.0)
    against_it, _ = confidence(tall=1.2, agree=4, against=3, reach=20.0)
    assert against_it < with_it


def test_a_measure_that_could_not_be_read_never_raises_the_score():
    """The regression that could have started the book trading on bad data.

    The old normalisation divided by the weight of whatever was PRESENT, so
    dropping a component scaled the rest up: a setup missing its heaviest
    input scored higher than one that had it and scored badly. The denominator
    is the whole weighted set now, so an unread measure earns nothing and
    still occupies its share.
    """
    measured_badly, _ = confidence(tall=1.8, agree=6, against=0, reach=0.0)
    unmeasured, _ = confidence(tall=1.8, agree=6, against=0, reach=None)
    assert unmeasured <= measured_badly


def test_dropping_a_setup_measure_still_renormalises():
    """Only the ground is protected -- an unread candle measure is 'not asked'.

    `body` and `wick` describe the candle in front of us and carry no weight
    in this configuration; their absence must not change the score.
    """
    a, _ = confidence(tall=1.4, agree=4, against=0, reach=20.0, body=0.5)
    b, _ = confidence(tall=1.4, agree=4, against=0, reach=20.0, body=None)
    assert a == pytest.approx(b)


def test_no_readable_measure_at_all_scores_nothing():
    assert confidence()[0] == 0.0


def test_the_weights_still_sum_to_a_hundred():
    """If someone re-weights this, the floor means something different."""
    assert sum(CONF_WEIGHTS.values()) == pytest.approx(100.0)


def test_the_breakdown_only_reports_measures_that_count():
    _, why = confidence(tall=1.4, agree=4, against=0, reach=20.0, body=0.5,
                        stairs=3)
    assert set(why) <= {k for k, w in CONF_WEIGHTS.items() if w > 0}
    assert "body" not in why and "stairs" not in why


# ------------------------------------------------ the floor, against reality
# A frozen copy, not the live files.
#
# These pin the scoring against real captured setups, which only works if the
# setups stay the same. `data/` is rewritten by the scanner every half hour,
# so the pin was really measuring whatever the market had done since -- it
# passed on a laptop with stale data and failed on the server with fresh data,
# which is the exact opposite of what a regression test is for.
FIXTURES = ROOT / "tests" / "fixtures"


def _live_break_rows():
    p = FIXTURES / "scout.json"
    if not p.exists():
        pytest.skip("no frozen scout output in this checkout")
    return [r for r in json.loads(p.read_text()) if r.get("kind") == "break"]


def test_the_recorded_shapes_score_where_the_record_says_they_did():
    """A guard on the scoring itself, pinned to real captured setups.

    These fourteen shapes are what the scout actually found. If a change to
    the shape functions, the ramps or the weights moves any of them across the
    70 floor, that is a change to what the book will trade and it should have
    to be noticed here first.
    """
    rows = _live_break_rows()
    wm = json.loads((FIXTURES / "watch_measures.json").read_text())
    boom = {r["sym"]: r for r in
            json.loads((FIXTURES / "boom.json").read_text())}
    room = P._stop_room()
    scored = {}
    for r in rows:
        m = boom.get(r["sym"]) or wm.get(r["sym"]) or {}
        conf, _ = confidence(
            stairs=(r.get("touches") or r.get("steps")),
            safety=(room / r["stop_pct"] if r["stop_pct"] else None),
            agree=r.get("agree"), against=r.get("against"),
            reach=m.get("reach"), smooth=m.get("smooth"), run=None,
            tall=(r.get("thrust") or 0) / 2.0, body=None, wick=None, votes=None)
        scored[r["sym"]] = conf
    assert scored, "no recorded shapes to score"
    # The weights are unchanged, so these are the scores they always were.
    # What changed is the floor: at 70 not one of them qualified, at 60 the
    # two strong-candle shapes do and the twelve weak ones still do not.
    assert scored["GWEIUSDT"] == pytest.approx(67.7, abs=0.1)
    assert scored["NOMUSDT"] == pytest.approx(65.0, abs=0.1)
    assert scored["HANAUSDT"] == pytest.approx(41.9, abs=0.1)
    assert max(scored.values()) < 70.0, "the old floor let nothing through"
    over = sorted(s for s, v in scored.items() if v >= 60.0)
    assert over == ["GWEIUSDT", "NOMUSDT"], over


def test_the_floor_of_sixty_is_reachable_on_every_coin():
    """Why the floor moved rather than the weights.

    A perfect candle (40) and a unanimous council (25) come to 65. Against 70
    that was unreachable, so every trade depended on how far the COIN travels
    and only 41 of 573 coins could ever qualify. Against 60 the two things a
    setup controls carry it on their own, on any coin.
    """
    assert P.QUALITY_ONLY_MAX == pytest.approx(65.0)
    assert confidence(tall=1.8, agree=6, against=0, reach=0.0)[0] >= 60.0
    assert confidence(tall=1.8, agree=6, against=0, reach=0.0)[0] < 70.0
    able, seen = P.floor_coverage(60.0)
    assert able == seen, f"only {able} of {seen} coins can clear 60"
    was_able, _ = P.floor_coverage(70.0)
    assert was_able / seen < 0.15, "70 was reachable after all"
