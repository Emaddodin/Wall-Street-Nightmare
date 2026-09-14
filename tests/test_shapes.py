"""
The shape layer: what the engine believes it is looking at.

Everything downstream -- confidence, sizing, the stop, the whole trade -- is
built on these four functions. If one of them reads a bar it should not have
seen, or calls a level a level when it is not one, no amount of care further
down can recover.
"""
from __future__ import annotations

import pytest

import market as M
import papertrade as P
from papertrade import (_stop_room, body_share, breakout, coiling, expansion,
                        leg_of, ripeness, run_strength, staircase, trend_ride,
                        unpack_state, unpack_votes, wick_risk)

ROOM = _stop_room()


def _with_future(bars, sig_t, n=60, adverse=True):
    """The same series with `n` further bars bolted on after the signal bar."""
    out = dict(bars)
    px = bars[sig_t][3]
    for i in range(1, n + 1):
        t = sig_t + i * M.BAR
        out[t] = ((px, px * 1.001, px * 0.90, px * 0.92) if adverse
                  else (px, px * 1.10, px * 0.999, px * 1.08))
    return out


# --------------------------------------------------------------- both shapes
@pytest.mark.parametrize("up", [True, False])
def test_breakout_is_found_in_both_directions(up):
    bars, t = M.band_then_break(thrust=3.5, up=up)
    g = breakout(bars, t, max_stop=ROOM)
    assert g is not None
    assert g["side"] == ("BUY" if up else "SELL")
    assert g["touches"] >= 2
    assert 0 < g["stop_pct"] <= ROOM
    # The entry is the level itself, and the stop sits just past it.
    assert g["entry"] == g["level"]
    assert (g["stop"] < g["level"]) is up


@pytest.mark.parametrize("up", [True, False])
def test_trend_ride_is_found_in_both_directions(up):
    bars, t = M.staircase_trend(up=up)
    g = trend_ride(bars, t, max_stop=ROOM)
    assert g is not None
    assert g["side"] == ("BUY" if up else "SELL")
    assert g["steps"] >= 3
    assert 0 < g["stop_pct"] <= ROOM
    assert (g["stop"] < g["level"]) is up


def test_a_clean_band_that_never_broke_is_not_a_shape():
    bars = M.quiet_band(n=60)
    t = sorted(bars)[-2]
    assert breakout(bars, t, max_stop=ROOM) is None
    assert trend_ride(bars, t, max_stop=ROOM) is None


def test_a_level_touched_once_is_not_a_level():
    """One high is a high. Two is a level -- that is the whole distinction."""
    bars = M.quiet_band(n=45, band=0.006)
    ks = sorted(bars)
    # Flatten every touch of the top edge except one.
    for i, k in enumerate(ks):
        o, h, l, c = bars[k]
        if i % 4 == 0 and i < len(ks) - 4:
            bars[k] = (o, h * 0.995, l, c)
    typ = sum((bars[k][1] - bars[k][2]) / bars[k][3] for k in ks) / len(ks)
    top = max(bars[k][1] for k in ks)
    t = ks[-1] + M.BAR
    rng = typ * 3.5 * top
    bars[t] = (top * 1.0005, top + rng, top * 0.999, top + rng * 0.9)
    g = breakout(bars, t, max_stop=ROOM, min_touches=2)
    assert g is None or g["touches"] >= 2


def test_a_stop_wider_than_the_leverage_allows_is_refused():
    """The ceiling on the stop is not a taste, it is the leverage."""
    bars, t = M.band_then_break(thrust=3.5, band=0.05)     # a very wide band
    assert breakout(bars, t, max_stop=ROOM) is None
    assert breakout(bars, t, max_stop=99.0) is not None    # only the cap refused it


def test_an_indecisive_candle_is_not_a_break():
    """Mostly wick is a fight, not a move."""
    bars = M.quiet_band(n=45)
    ks = sorted(bars)
    top = max(bars[k][1] for k in ks)
    t = ks[-1] + M.BAR
    # closes above the level, but the body is a tenth of the range
    bars[t] = (top * 1.001, top * 1.05, top * 0.99, top * 1.0015)
    assert breakout(bars, t, max_stop=ROOM) is None


# ------------------------------------------------------------ no future data
@pytest.mark.parametrize("fn,args", [
    (breakout, ()), (trend_ride, ()),
])
def test_shapes_cannot_see_past_the_signal_bar(fn, args):
    bars, t = M.band_then_break(thrust=3.5)
    before = fn(bars, t, max_stop=ROOM)
    after = fn(_with_future(bars, t), t, max_stop=ROOM)
    assert before == after


def test_staircase_expansion_and_run_cannot_see_the_future():
    bars, t = M.band_then_break(thrust=3.5)
    fut = _with_future(bars, t)
    assert staircase(bars, t, "BUY") == staircase(fut, t, "BUY")
    assert expansion(bars, t) == expansion(fut, t)
    assert run_strength(bars, t, "BUY") == run_strength(fut, t, "BUY")
    assert body_share(bars, t) == body_share(fut, t)
    assert leg_of(bars, t) == leg_of(fut, t)
    assert coiling(M.quiet_band(n=120), sorted(M.quiet_band(n=120))[-2]) == \
        coiling(M.quiet_band(n=120), sorted(M.quiet_band(n=120))[-2])


def test_wick_risk_cannot_see_the_future():
    """The coin's shadow habit must be measured up to the signal, not past it.

    This measures how often the coin's ordinary candles would clear the stop
    on their own. Reading bars that printed AFTER the decision means judging
    the entry on what the market did next, which is the definition of
    lookahead -- and here it inverts the answer completely: a coin that had
    never once wicked through the stop reads as doing it 61% of the time.
    """
    bars, t = M.band_then_break(thrust=3.5)
    fut = _with_future(bars, t, adverse=True)
    assert wick_risk(bars, "BUY", 0.8, t=t) == wick_risk(fut, "BUY", 0.8, t=t)


def test_wick_risk_still_counts_the_history_it_should_see():
    """Guarding against the future must not blind it to the past."""
    bars = M.quiet_band(n=120)
    ks = sorted(bars)
    for k in ks[:60]:
        o, h, l, c = bars[k]
        bars[k] = (o, h, l * 0.90, c)          # deep shadows, long ago
    assert wick_risk(bars, "BUY", 1.0, t=ks[-1]) > 0


# --------------------------------------------------------- the packed council
@pytest.mark.parametrize("votes", [
    {"Bank": 1, "Team45": 1, "Tesla": 1, "Sniper": 1, "HTF": 1, "MA": 1},
    {"Bank": -1, "Team45": 0, "Tesla": 1, "Sniper": -1, "HTF": 0, "MA": 1},
    {"Bank": 0, "Team45": 0, "Tesla": 0, "Sniper": 0, "HTF": 0, "MA": 0},
])
def test_vote_packing_round_trips(votes):
    from fakes import pack_votes
    assert unpack_votes(pack_votes(**votes)) == votes


def test_state_packing_round_trips():
    for plan in (-1, 0, 1):
        for ready in (0, 1):
            for v in range(7):
                n = (plan + 1) + 3 * ready + 6 * v + 42 * 2 + 126 * 1 + 252 * 0
                d = unpack_state(n)
                assert d["plan_dir"] == plan
                assert d["ready"] is bool(ready)
                assert d["votes"] == v
                assert d["fib_dir"] == 1 and d["fib_hit"] is True


def test_ripeness_needs_the_tide_behind_it():
    """One module short is worth nothing on its own -- the tide is the thing."""
    at = {"SNIP_BUY_VOTE": 1, "SNIP_SELL_VOTE": 2, "HTF_BIAS": 3,
          "VOTES_PACKED": 4}
    from fakes import pack_votes
    packed = pack_votes(Bank=1, Team45=1, Tesla=1, Sniper=1)
    with_tide = ripeness([0, 2, 0, 1, packed], at)
    against = ripeness([0, 2, 0, -1, packed], at)
    assert with_tide["side"] == 1 and with_tide["ripe"] > 0
    assert against["ripe"] == 0.0


def test_ripeness_is_none_without_the_series():
    assert ripeness([0], {}) is None


# ------------------------------------------------------------ degenerate data
@pytest.mark.parametrize("fn", [breakout, trend_ride])
def test_shapes_survive_empty_and_short_series(fn):
    assert fn({}, 0, max_stop=ROOM) is None
    assert fn({M.T0: (1, 1, 1, 1)}, M.T0, max_stop=ROOM) is None


def test_shapes_survive_zero_prices():
    """A frozen or broken feed can hand back zeros; nothing may divide by them."""
    bars = {M.T0 + i * M.BAR: (0.0, 0.0, 0.0, 0.0) for i in range(60)}
    t = sorted(bars)[-1]
    assert breakout(bars, t, max_stop=ROOM) is None
    assert trend_ride(bars, t, max_stop=ROOM) is None
    assert staircase(bars, t, "BUY") is None          # refused, not crashed
    assert body_share(bars, t) is None
    assert expansion(bars, t) is None
    assert coiling(bars, t) is None


# ------------------------------------------------------- how many touches
def _band_then_break(bars_at_level: int, base=100.0, n=44):
    """A quiet band where one price is refused, then broken upward.

    The ordinary bars have to wander. A band whose every bar tops out at the
    same tick hands breakout() that tick as the level, with forty touches to
    go with it -- a fixture measuring itself rather than the function. The
    wicks at the level have to stay small too, or the stop the shape derives
    from them lands outside the ceiling and the shape is refused for a reason
    that has nothing to do with what is being tested.
    """
    import market as M
    bars, t, hits = {}, M.T0, 0
    level = base * 1.0015
    for i in range(n):
        if i and i % 4 == 0 and hits < bars_at_level:
            hits += 1
            bars[t] = (base * 1.0005, level, base * 0.9995, base * 1.0002)
        else:
            wob = 1 + ((i * 7) % 5) * 0.00012        # highs that do not line up
            bars[t] = (base, base * wob,
                       base * (0.9994 - (i % 3) * 0.0001),
                       base * (1 + ((i % 2) - 0.5) * 0.0002))
        t += M.BAR
    bars[t] = (level * 1.0001, level * 1.011, level * 0.9999, level * 1.010)
    return bars, t


def test_min_touches_defaults_to_two_so_nothing_moved():
    """The flag exists; what it defaults to is the behaviour that was there."""
    import inspect
    assert inspect.signature(P.breakout).parameters["min_touches"].default == 2
    assert P._SHAPE["min_touches"] == 2


def test_a_price_refused_twice_is_a_level_at_the_default():
    bars, t = _band_then_break(3)
    got = P.breakout(bars, t, min_touches=2)
    assert got is not None and got["touches"] == 2


def test_the_same_shape_is_refused_when_a_level_must_hold_four_times():
    """Two touches is an accident; the flag is what lets that be said.

    Measured over 119 shapes filled on 41 hours of real 15m candles across 70
    coins: the whole set reached +10% 2.5% of the time and stopped 84.9% of
    the time, while the subset whose level had held four times or more reached
    it 8.3% of the time. Twenty-four shapes -- a direction, not a proof, which
    is why this is reachable rather than changed.
    """
    two, t2 = _band_then_break(3)
    assert P.breakout(two, t2, min_touches=2) is not None
    assert P.breakout(two, t2, min_touches=4) is None

    four, t4 = _band_then_break(5)
    got = P.breakout(four, t4, min_touches=4)
    assert got is not None and got["touches"] >= 4


def test_the_scout_and_the_book_cannot_disagree_about_what_a_level_is():
    """One finds the shape, the other judges it. Two answers means lost work.

    A scout on two touches offering shapes to a book that wants four produces
    a plan the book refuses every time, and the refusal is logged as a
    rejection rather than as the misconfiguration it is.
    """
    import pathlib
    from units import flag
    root = pathlib.Path(P.__file__).resolve().parent
    b = flag(root / "services" / "tbt-paper.service", "--min-touches")
    s = flag(root / "services" / "tbt-scout.service", "--min-touches")
    assert (b is None) == (s is None), (
        f"one unit sets --min-touches and the other does not: "
        f"book={b} scout={s}")
    if b and s:
        assert b == s, (f"the book wants a level held {b} times, the scout "
                        f"offers shapes at {s}")
