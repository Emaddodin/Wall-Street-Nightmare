"""
The engine's own logic, over the market it actually saw.

13,500 recorded 15-minute candles on 45 coins, taken off the live chart. The
shape functions are run one bar at a time and are never handed a bar that had
not closed, so this is a replay and not a fit.

These tests are not a claim that the strategy makes money. They pin what it
DOES, so that a change to the shapes, the ramps or the weights cannot quietly
alter it, and so the sample size behind any conclusion is visible.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

import papertrade as P
import replay

ROOT = Path(__file__).resolve().parent.parent
ROOM = P._stop_room()
TARGET = 10.0
FEE_PCT = 0.12          # 12 bps round trip, as a percent of price


@pytest.fixture(scope="module")
def recorded():
    got = replay.coins()
    if not got:
        pytest.skip("no recorded chart history in this checkout")
    return got


@pytest.fixture(scope="module")
def measures():
    wm = json.loads((ROOT / "data" / "watch_measures.json").read_text())
    boom = {r["sym"]: r for r in
            json.loads((ROOT / "data" / "boom.json").read_text())}
    return wm, boom


def find_shapes(ohlc, meta):
    """Every shape the engine would have seen, with the council of that bar."""
    got = replay.walk(
        ohlc, lambda pre, t: (P.breakout(pre, t, max_stop=ROOM)
                              or P.trend_ride(pre, t, max_stop=ROOM)))
    out = []
    for t, g in got:
        mem = (meta.get(t) or {}).get("members") or {}
        want = 1 if g["side"] == "BUY" else -1
        g["agree"] = sum(1 for v in mem.values() if v == want)
        g["against"] = sum(1 for v in mem.values() if v == -want)
        out.append((t, g))
    return out


def score(sym, g, measures):
    wm, boom = measures
    m = boom.get(sym) or wm.get(sym) or {}
    return P.confidence(
        stairs=(g.get("touches") or g.get("steps")),
        safety=(ROOM / g["stop_pct"] if g["stop_pct"] else None),
        agree=g.get("agree"), against=g.get("against"),
        reach=m.get("reach"), smooth=m.get("smooth"), run=None,
        tall=(g.get("thrust") or 0) / 2.0, body=None, wick=None, votes=None)[0]


def run_floor(recorded, measures, floor):
    res, taken = Counter(), 0
    for sym, ohlc, meta in recorded:
        for t, g in find_shapes(ohlc, meta):
            if score(sym, g, measures) < floor:
                continue
            taken += 1
            f = replay.filled(ohlc, t, g["side"], g["entry"], bars=8)
            if f is None:
                res["unfilled"] += 1
                continue
            out, _ = replay.forward(ohlc, f, g["side"], g["entry"],
                                    TARGET, g["stop_pct"], bars=48)
            res[out] += 1
    return taken, res


# ------------------------------------------------------- what the data holds
def test_the_recording_is_big_enough_to_mean_anything(recorded):
    bars = sum(len(o) for _s, o, _m in recorded)
    assert len(recorded) >= 40
    assert bars >= 12_000


def test_the_shapes_are_found_on_real_candles(recorded):
    """If a change stops the engine seeing setups in real data, say so here."""
    total = sum(len(find_shapes(o, m)) for _s, o, m in recorded)
    assert total > 50, f"only {total} shapes over 13,500 real bars"
    # trend_ride() joins a run at its NEWEST level (the docstring's rule,
    # fixed 2026-09-06 -- it used to take the far extreme of the whole leg,
    # which is where the leg STARTED). With the level where the docstring
    # puts it, more runs are genuinely joinable: 929 over 13,500 bars is
    # one per 14.5 bars across 45 violent coins. The cap only exists to
    # catch a regression that turns every bar into a shape.
    assert total < 1400, f"{total} shapes is one every 9 bars -- too loose"


def test_every_shape_the_engine_takes_fits_inside_the_leverage(recorded):
    """The invariant the whole design rests on, checked against real data."""
    for _sym, ohlc, meta in recorded:
        for _t, g in find_shapes(ohlc, meta):
            assert 0 < g["stop_pct"] <= ROOM


def test_shapes_appear_on_both_sides_of_the_market(recorded):
    sides = Counter()
    for _sym, ohlc, meta in recorded:
        for _t, g in find_shapes(ohlc, meta):
            sides[g["side"]] += 1
    assert sides["BUY"] > 5 and sides["SELL"] > 5, sides


# --------------------------------------------------------------- the filter
def test_the_confidence_floor_improves_the_hit_rate_on_real_data(
        recorded, measures):
    """The floor earns its place -- on a sample far too small to trust.

    Raising it from nothing to seventy takes the hit rate from about one in
    twelve to about one in five. It also takes the number of decided trades
    from 74 to 5. Five is not a result; it is an anecdote with error bars
    wider than the effect. The direction is right and the sample is not
    evidence, and both halves of that belong in the record.
    """
    hits = {}
    for floor in (0, 70):
        taken, res = run_floor(recorded, measures, floor)
        decided = res["target"] + res["stop"]
        hits[floor] = (taken, decided, res["target"] / max(decided, 1))
    assert hits[0][1] > 40, "the unfiltered sample should be substantial"
    assert hits[70][1] < 15, "the filtered sample is tiny -- do not trust it"
    assert hits[70][2] >= hits[0][2], (
        f"the floor made the hit rate worse: {hits}")


def test_the_floor_lets_through_roughly_one_setup_a_week(recorded, measures):
    """What 70 costs in opportunity, measured rather than felt.

    Seventy-four days of 15m candles on forty-five coins. If this number moves
    a long way in either direction, the book's whole cadence has changed.
    """
    taken, _res = run_floor(recorded, measures, 70)
    assert 1 <= taken <= 40, f"{taken} shapes cleared the floor in 74 days"


def test_a_stop_and_a_target_in_one_candle_resolve_against_us(recorded):
    """The ambiguity a backtest must never resolve in its own favour."""
    ohlc = {i: (100, 120, 80, 100) for i in range(10)}     # every bar spans both
    out, _ = replay.forward(ohlc, 0, "BUY", 100.0, 10.0, 1.0, bars=5)
    assert out == "stop"


def test_the_replay_never_shows_a_function_an_unclosed_bar(recorded):
    """The walk rebuilds the prefix per bar; there is nothing ahead to peek at."""
    sym, ohlc, _meta = recorded[0]
    seen_max = []
    replay.walk(ohlc, lambda pre, t: seen_max.append(max(pre) <= t))
    assert seen_max and all(seen_max)
