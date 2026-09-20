"""Spending a day's budget on the best of the day, not the first of the day.

A cap refuses the ninth signal whatever it is, and the ninth is as likely to
be the best as the first. A fixed floor leaves the budget unspent on a rich
day and spends it before noon on a poor one. These tests are about the bar
moving the way the day moves, and about the two ways this could quietly ruin
the book: taking something bad because the day was thin, and refusing
everything because it has no history yet.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pace as PA  # noqa: E402

DAY = 86400.0
NOON = PA.day_start(1788600000.0) + 12 * 3600


def scores(n, at_from, at_to, values):
    """`n` scores spread over a span, cycling through `values`."""
    step = (at_to - at_from) / max(1, n - 1) if n > 1 else 0
    return [{"at": at_from + i * step, "sym": "X",
             "score": values[i % len(values)]} for i in range(n)]


def test_the_budget_is_a_day_and_a_restart_does_not_refill_it():
    start = PA.day_start(NOON)
    trades = [{"opened": start + 60}, {"opened": start + 120},
              {"opened": start - 3600}]        # yesterday's
    assert PA.spent(trades, NOON) == 2


def test_the_budget_spent_stops_the_book_entirely():
    start = PA.day_start(NOON)
    trades = [{"opened": start + i} for i in range(8)]
    got, why = PA.bar([], trades, 8, 40.0, NOON)
    assert got is None, why
    assert "8 of 8" in why


def test_a_generous_day_raises_the_bar():
    """Eighty signals coming and eight slots left: only the top tenth."""
    hist = scores(80, NOON - 6 * 3600, NOON, list(range(20, 100)))
    got, why = PA.bar(hist, [], 8, 40.0, NOON)
    assert got > 80, why


def test_a_thin_day_lowers_it():
    """Ten signals in six hours and eight slots left: take almost anything."""
    hist = scores(10, NOON - 6 * 3600, NOON, list(range(20, 100)))
    got, _ = PA.bar(hist, [], 8, 40.0, NOON)
    rich, _ = PA.bar(scores(200, NOON - 6 * 3600, NOON, list(range(20, 100))),
                     [], 8, 40.0, NOON)
    assert got < rich


def test_the_floor_holds_however_thin_the_day():
    """"The best available" is not a reason to take something bad."""
    hist = scores(40, NOON - 6 * 3600, NOON, [10, 12, 14, 16])
    got, _ = PA.bar(hist, [], 8, 55.0, NOON)
    assert got == 55.0


def test_nothing_is_spent_before_the_day_has_been_looked_at():
    """The best eight of a day cannot be picked from the first signal.

    The first trade after a reset was taken on a score of exactly the floor
    with no history behind it, which is not choosing -- it is taking whatever
    arrived first, and it stopped out.
    """
    got, why = PA.bar([], [], 8, 40.0, NOON)
    assert got is None, why
    assert "too early in the day" in why


def test_late_in_the_day_it_stops_waiting_for_a_sample():
    """A budget that ends untouched is its own kind of failure."""
    late = PA.day_start(NOON) + 20 * 3600
    got, why = PA.bar([], [], 8, 40.0, late)
    assert got == 40.0, why
    assert "rather than ending the day unspent" in why


def test_the_bar_falls_as_the_day_runs_out():
    """Two slots left with an hour to go is not the time to be fussy."""
    hist = scores(80, NOON - 6 * 3600, NOON, list(range(20, 100)))
    start = PA.day_start(NOON)
    six_taken = [{"opened": start + i} for i in range(6)]
    midday, _ = PA.bar(hist, six_taken, 8, 40.0, NOON)
    late = PA.day_start(NOON) + 23 * 3600
    hist_late = scores(80, late - 6 * 3600, late, list(range(20, 100)))
    evening, _ = PA.bar(hist_late, six_taken, 8, 40.0, late)
    assert evening < midday


def test_a_score_from_last_week_is_not_part_of_this_day():
    old = [{"at": NOON - 3 * DAY, "sym": "X", "score": 99}]
    assert PA.seen(old, NOON) == []


def test_remembering_drops_what_aged_out():
    old = [{"at": NOON - 3 * DAY, "sym": "X", "score": 99}]
    got = PA.remember(old, "Y", 60.0, NOON)
    assert [s["sym"] for s in got] == ["Y"]


def test_the_quantile_is_the_value_it_claims():
    assert PA.quantile([10, 20, 30, 40, 50], 0.0) == 10
    assert PA.quantile([10, 20, 30, 40, 50], 1.0) == 50
    assert PA.quantile([10, 20, 30, 40, 50], 0.5) == 30
    assert PA.quantile([], 0.5) == 0.0
    assert PA.quantile([7], 0.9) == 7


def test_eight_slots_over_a_simulated_day_spend_close_to_eight():
    """The whole point, end to end.

    Signals arrive all day with random-ish scores; the bar moves; count what
    gets taken. It has to land on the budget rather than three or thirty.
    """
    import random
    rnd = random.Random(7)
    start = PA.day_start(NOON)
    hist, trades = [], []
    for i in range(240):                     # ten signals an hour, all day
        now = start + i * 360.0
        sc = rnd.uniform(10, 100)
        b, _ = PA.bar(hist, trades, 8, 40.0, now)
        if b is not None and sc >= b:
            trades.append({"opened": now})
        hist = PA.remember(hist, "X", sc, now)
    assert 6 <= len(trades) <= 10, (
        f"a day of signals spent {len(trades)} of a budget of 8")


def test_a_refused_score_still_counts_toward_seeing_the_day():
    """The bar is a percentile of what the day offered, not of what it took.

    The book recorded only accepted scores, so the sample began at the floor,
    every percentile of it sat too high, and the pacer grew fussier the worse
    the day got -- the exact opposite of its purpose. Worse, it never reached
    a sample at all: after three refusals it still reported one score seen and
    stayed on the floor forever.
    """
    src = (ROOT / "papertrade.py").read_text()
    i = src.index("Every score is remembered")
    body = src[i:i + 600]
    assert "if not poor:" not in body, (
        "refused scores are still being dropped from the sample")
    assert "state[\"scores\"] = pace.remember(" in body


def test_the_sample_is_what_makes_the_choice_possible():
    """Eight is not arbitrary: it is the budget, and a percentile of fewer
    points than slots cannot separate anything."""
    assert PA.MIN_SAMPLE >= 8
    assert 0 < PA.LATE_H < 12


# --------------------------------------------- whose day the budget belongs to
def test_the_day_can_begin_where_the_operator_says():
    """Nine in the evening in Tehran is 17:30 UTC.

    A boundary at midnight UTC falls in the middle of the session the operator
    is actually watching, and would hand the book a fresh eight halfway
    through their evening.
    """
    off = 17.5
    midnight = PA.day_start(NOON, 0.0)
    evening = midnight + 17.5 * 3600
    # just after the boundary, the day is the one that has just begun
    assert PA.day_start(evening + 60, off) == pytest.approx(evening)
    # just before it, the day is still yesterday's
    assert PA.day_start(evening - 60, off) == pytest.approx(evening - 86400)


def test_the_default_boundary_is_still_midnight():
    assert PA.day_start(NOON, 0.0) == pytest.approx(NOON - 12 * 3600)


def test_trades_are_counted_against_the_operators_day(monkeypatch):
    """A trade taken before the boundary belongs to the day that has ended."""
    monkeypatch.setattr(PA, "DAY_OFFSET_H", 17.5)
    start = PA.day_start(NOON)
    trades = [{"opened": start + 60}, {"opened": start - 60}]
    assert PA.spent(trades, NOON) == 1


def test_the_unit_and_the_pacer_agree_on_where_the_day_starts():
    import sys as _s
    _s.path.insert(0, str(ROOT / "tests"))
    from units import flag
    u = ROOT / "services" / "tbt-paper.service"
    off = flag(u, "--day-start")
    if off is None:
        return
    assert 0 <= float(off) < 24, f"--day-start {off} is not an hour of the day"
    src = (ROOT / "papertrade.py").read_text()
    assert "pace.DAY_OFFSET_H = a.day_start" in src, (
        "the unit names a day boundary the pacer never reads")
