"""
The entry decision: a floor a setup can actually reach, and a candle floor
nothing can score its way past.

The weights are unchanged -- 40 candle, 35 reach, 25 council. What changed is
the floor. At 70 a perfect candle and a unanimous council came to 65 and could
not clear it, so every trade depended on how far the COIN travels and only 41
of 573 scanned coins could ever qualify. At 60 the two measures a setup
controls carry it on their own.

That leaves one hole, and `--min-thrust` closes it: the score adds up, so on a
coin that travels far enough a unanimous council reaches 60 by itself and any
shape would pass whatever its candle looked like.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import market as M
import papertrade as P
from fakes import FakeBitunix
from harness import quiet_window, run_book
from test_lifecycle import LEVEL_UP, SYM, flags, scenario

ROOT = Path(__file__).resolve().parent.parent

# thrust >= 3.2 gives full candle marks; `tall` is thrust/2 on a 0.7->1.6 ramp
PERFECT_CANDLE = 1.8
WEAK_CANDLE = 0.5
FLOOR = 60.0


# ------------------------------------------------------------ the invariant
def test_the_floor_is_reachable_by_a_setup_on_its_own():
    """The guard. Without it, every part works and the book never trades.

    This is not a hypothetical: the engine ran for weeks in exactly that
    state. Every component was correct, every test of every component passed,
    and the sum of the two a setup controls was 65 against a floor of 70.
    """
    assert P.QUALITY_ONLY_MAX >= FLOOR, (
        f"a perfect setup reaches {P.QUALITY_ONLY_MAX} against a floor of "
        f"{FLOOR}: the floor depends on the coin, not the setup")
    assert P.MAX_ACHIEVABLE_SCORE >= FLOOR
    assert P.check_score_reachable(FLOOR) is None


def test_an_unreachable_floor_is_refused_out_loud():
    why = P.check_score_reachable(P.MAX_ACHIEVABLE_SCORE + 0.5)
    assert why and "can never be reached" in why
    assert P.check_score_reachable(0) is None


def test_the_maximum_is_derived_from_the_weights_not_declared():
    """Change a weight and the maximum follows, or the guard is decoration."""
    assert P.MAX_ACHIEVABLE_SCORE == pytest.approx(
        sum(w for w in P.CONF_WEIGHTS.values() if w > 0))
    assert P.MAX_ACHIEVABLE_SCORE == pytest.approx(P.SCORE_MAX)
    assert P.QUALITY_ONLY_MAX == pytest.approx(
        P.CONF_WEIGHTS["tall"] + P.CONF_WEIGHTS["agree"])


def test_the_book_refuses_to_start_on_an_unreachable_floor(tmp_path):
    win, last = quiet_window(sym=SYM)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    b, _ = run_book(tmp_path, flags(min_confidence=140), {0: win}, ex,
                    polls=4, clock_start=last + M.BAR,
                    boom_rows=[{"sym": SYM, "reach": 40.0, "smooth": 0.4}])
    assert b.trades == []
    assert b.state == {} or not b.state.get("trades")


# --------------------------------------------------------------- the scale
def test_a_perfect_setup_clears_the_floor_on_any_coin():
    """65 against a floor of 60 -- which is the whole point of the change."""
    conf, _ = P.confidence(tall=PERFECT_CANDLE, agree=6, against=0, reach=0.0)
    assert conf == pytest.approx(65.0)
    assert conf >= FLOOR


def test_a_perfect_setup_clears_it_on_a_realistic_council():
    """Four of six leaning with none against is full council marks."""
    conf, _ = P.confidence(tall=PERFECT_CANDLE, agree=4, against=0, reach=0.0)
    assert conf >= FLOOR


def test_a_coin_that_travels_lifts_the_score_it_does_not_decide_it():
    far, _ = P.confidence(tall=PERFECT_CANDLE, agree=6, against=0, reach=45.0)
    near, _ = P.confidence(tall=PERFECT_CANDLE, agree=6, against=0, reach=0.0)
    assert far > near
    assert near >= FLOOR, "the coin must not be able to veto a perfect setup"


def test_a_weak_setup_still_fails():
    conf, _ = P.confidence(tall=WEAK_CANDLE, agree=2, against=1, reach=10.0)
    assert conf < FLOOR


def test_a_council_leaning_against_still_costs_more_than_it_gives():
    with_it, _ = P.confidence(tall=1.2, agree=4, against=0, reach=10.0)
    against_it, _ = P.confidence(tall=1.2, agree=4, against=3, reach=10.0)
    assert against_it < with_it


def test_the_weights_are_the_ones_the_record_supports():
    """The floor moved; the weights did not."""
    assert P.CONF_WEIGHTS["tall"] == 40.0
    assert P.CONF_WEIGHTS["reach"] == 35.0
    assert P.CONF_WEIGHTS["agree"] == 25.0


def test_an_unmeasured_coin_never_scores_above_a_measured_bad_one():
    """An absent measure must not scale the rest up."""
    unknown, _ = P.confidence(tall=PERFECT_CANDLE, agree=6, against=0)
    bad, _ = P.confidence(tall=PERFECT_CANDLE, agree=6, against=0, reach=0.0)
    assert unknown <= bad


def test_nothing_readable_scores_nothing():
    assert P.confidence()[0] == 0.0


# ------------------------------------------- the floor on the candle itself
def test_the_dials_are_named_constants_not_numbers_inside_a_branch():
    assert P.SCORE_MAX == 100.0
    assert P.MAX_ACHIEVABLE_SCORE == 100.0
    assert P.QUALITY_ONLY_MAX == 65.0
    assert P.COIN_MEASURES == ("reach", "smooth")
    assert set(P.GROUND) == {"tall", "reach", "agree"}, (
        "every weighted measure must keep its share when unread")


def test_a_travelling_coin_cannot_carry_a_candle_that_did_nothing(tmp_path):
    """The hole a lower floor opens, and the reason --min-thrust exists.

    The score adds up. On a coin covering the target 45% of the time, a 6-0
    council reaches 60 by itself -- so without this floor ANY shape passes,
    whatever its candle looks like. That is precisely the trade this strategy
    is not for.
    """
    weak, _ = P.confidence(tall=0.0, agree=6, against=0, reach=45.0)
    assert weak >= FLOOR, "the hole this guards is supposed to exist"

    b, _ = scenario(tmp_path, [LEVEL_UP] * 3, thrust=1.2,
                    argv=flags(min_confidence=60, min_thrust=2.0),
                    boom=[{"sym": SYM, "reach": 50.0, "smooth": 0.6}])
    assert b.trades == [], "a characterless candle was traded"
    assert b.said("weak_candle"), b.log


def test_the_candle_floor_is_checked_before_the_score(tmp_path):
    """A floor nothing can score its way past."""
    b, _ = scenario(tmp_path, [LEVEL_UP] * 3, thrust=1.2,
                    argv=flags(min_confidence=60, min_thrust=2.0),
                    boom=[{"sym": SYM, "reach": 50.0, "smooth": 0.6}])
    assert b.said("weak_candle")
    assert not b.said("below_threshold"), "it was scored despite a weak candle"


def test_a_strong_candle_passes_the_floor(tmp_path):
    b, _ = scenario(tmp_path, [LEVEL_UP, LEVEL_UP * 1.11, LEVEL_UP * 1.11],
                    thrust=5.0, argv=flags(min_confidence=60, min_thrust=2.0),
                    boom=[{"sym": SYM, "reach": 20.0, "smooth": 0.3}])
    assert not b.said("weak_candle")
    assert len(b.trades) == 1, b.log
    assert b.trades[0]["reason"] == "target"


def test_the_candle_floor_is_off_unless_it_is_asked_for(tmp_path):
    """It changes what the book takes, so it is the operator's dial.

    The floor belongs to the shape source. It measures the breaking candle
    against the coin's own ordinary one, and there is no breaking candle in a
    signal the indicator published -- so this is asserted against the unit
    only when the unit is actually running that source.
    """
    src = __import__("pathlib").Path(P.__file__).read_text()
    i = src.index('"--min-thrust"')
    assert "default=0.0" in src[i:i + 200]
    from units import exec_start, flag
    u = ROOT / "services" / "tbt-paper.service"
    if "--source break" in exec_start(u):
        m = flag(u, "--min-thrust")
        assert m, "the unit trades shapes and sets no candle floor at all"
        assert float(m) > 0


def test_a_weak_shape_on_an_ordinary_coin_is_rejected_on_the_score(tmp_path):
    """Without the candle floor the score still refuses it."""
    b, _ = scenario(tmp_path, [LEVEL_UP] * 3, thrust=0.8,
                    argv=flags(min_confidence=60),
                    boom=[{"sym": SYM, "reach": 15.0, "smooth": 0.3}])
    assert b.trades == []
    assert b.said("below_threshold"), b.log


# ------------------------------------------------------------ observability
def test_every_rejection_says_which_gate_and_what_it_scored(tmp_path):
    b, _ = scenario(tmp_path, [LEVEL_UP] * 3, thrust=0.8,
                    argv=flags(min_confidence=60),
                    boom=[{"sym": SYM, "reach": 15.0, "smooth": 0.3}])
    line = next(x for x in b.log if "below_threshold" in x)
    assert "scored" in line and "needs" in line


def test_startup_says_what_the_floor_costs(tmp_path):
    """A floor nobody can reach looks exactly like a quiet market."""
    b, _ = scenario(tmp_path, [LEVEL_UP] * 2,
                    argv=flags(min_confidence=60, min_thrust=2.0),
                    boom=[{"sym": SYM, "reach": 40.0, "smooth": 0.5}])
    line = next((x for x in b.log if "the floor is 60" in x), None)
    assert line, b.log
    assert "a setup reaches 65 on its own" in line
    assert any("2.00x" in x for x in b.log), "the candle floor was not announced"


def test_startup_warns_when_nothing_guards_the_candle(tmp_path):
    b, _ = scenario(tmp_path, [LEVEL_UP] * 2, argv=flags(min_confidence=60),
                    boom=[{"sym": SYM, "reach": 40.0, "smooth": 0.5}])
    assert b.said("no floor on the candle"), b.log


def test_a_periodic_summary_is_emitted(tmp_path):
    b, _ = scenario(tmp_path, [LEVEL_UP] * 6, thrust=0.8,
                    argv=flags(min_confidence=60, interval=600),
                    boom=[{"sym": SYM, "reach": 15.0, "smooth": 0.3}],
                    polls=12)
    assert b.said("candidates:"), b.log


def test_the_summary_counts_add_up(tmp_path):
    b, _ = scenario(tmp_path, [LEVEL_UP] * 6, thrust=0.8,
                    argv=flags(min_confidence=60, interval=600),
                    boom=[{"sym": SYM, "reach": 15.0, "smooth": 0.3}],
                    polls=12)
    import re
    line = next(x for x in b.log if x.startswith("candidates:"))
    scanned = int(re.search(r"(\d+) scanned", line).group(1))
    weak = int(re.search(r"(\d+) weak candles", line).group(1))
    below = int(re.search(r"(\d+) below", line).group(1))
    passed = int(re.search(r"(\d+) passed", line).group(1))
    assert scanned == weak + below + passed, line


# ------------------------------------------------------- against real data
def test_the_floor_on_the_recorded_universe():
    """What the change actually costs, measured rather than assumed."""
    able_60, seen = P.floor_coverage(60.0)
    able_70, _ = P.floor_coverage(70.0)
    assert able_60 == seen, f"only {able_60} of {seen} coins can clear 60"
    assert able_70 / seen < 0.15, f"{able_70} of {seen} could clear 70"
    assert P.confidence(tall=PERFECT_CANDLE, agree=4, against=0,
                        reach=0.0)[0] >= 60.0


def test_an_unread_council_can_only_make_a_setup_harder(tmp_path):
    """The second time the same mistake bit.

    The score was divided by the weight of whatever could be READ, so a shape
    whose council reading failed -- which happens when the scout's study read
    does not come back -- was divided by 75 instead of 100. A perfect candle
    on a coin covering the target 20% of the time went from 51 to 68, straight
    through a floor of 60. An unread measure must cost, never pay.
    """
    for reach in (0.0, 10.0, 20.0, 30.0, 45.0):
        unread, _ = P.confidence(tall=PERFECT_CANDLE, reach=reach)
        zero, _ = P.confidence(tall=PERFECT_CANDLE, agree=0, against=0,
                               reach=reach)
        assert unread == pytest.approx(zero), (
            f"at reach {reach} an unread council scored {unread} against "
            f"{zero} for a council that said nothing")


def test_an_unread_reach_can_only_make_a_setup_harder():
    for agree in (0, 3, 6):
        unread, _ = P.confidence(tall=PERFECT_CANDLE, agree=agree, against=0)
        zero, _ = P.confidence(tall=PERFECT_CANDLE, agree=agree, against=0,
                               reach=0.0)
        assert unread == pytest.approx(zero)


def test_no_weighted_measure_can_be_dropped_from_the_denominator():
    """The rule, stated once, so a new weight cannot forget it."""
    weighted = {k for k, w in P.CONF_WEIGHTS.items() if w > 0}
    assert set(P.GROUND) == weighted


# ------------------------------------------------------------- the leverage
def test_a_coin_the_exchange_caps_low_is_refused(tmp_path):
    """Ten percent of price is the payout only at the leverage it assumes.

    At 50x it is 500% of the margin; at 10x it is 100%. Same stop, same risk,
    a fifth of the reward -- and better than half the coins the scout walks
    are capped below fifty.
    """
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0, max_lev=10)
    b, _ = scenario(tmp_path, [LEVEL_UP] * 3, thrust=5.0, exchange=ex,
                    argv=flags(min_confidence=60, min_thrust=2.0, min_lev=50),
                    boom=[{"sym": SYM, "reach": 40.0, "smooth": 0.5}])
    assert b.trades == []
    assert b.said("low_leverage"), b.log


@pytest.mark.parametrize("cap", [50, 75])
def test_a_coin_at_fifty_or_above_is_kept(tmp_path, cap):
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0, max_lev=cap)
    b, _ = scenario(tmp_path, [LEVEL_UP, LEVEL_UP * 1.11, LEVEL_UP * 1.11],
                    thrust=5.0, exchange=ex,
                    argv=flags(min_confidence=60, min_thrust=2.0, min_lev=50),
                    boom=[{"sym": SYM, "reach": 40.0, "smooth": 0.5}])
    assert not b.said("low_leverage")
    assert len(b.trades) == 1, b.log
    # never more than asked for, whatever the exchange allows
    assert b.trades[0]["notional"] / b.trades[0]["margin"] == pytest.approx(50.0)


def test_an_unreadable_leverage_cap_is_refused_not_assumed(tmp_path):
    """"Unknown" is not "fifty".

    Guessing upward here puts a trade on exactly the coin the filter exists to
    exclude, which is how every other unknown in this engine used to behave.
    """
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    ex.break_once("max_leverage", RuntimeError("symbol suspended"))
    b, _ = scenario(tmp_path, [LEVEL_UP] * 3, thrust=5.0, exchange=ex,
                    argv=flags(min_confidence=60, min_thrust=2.0, min_lev=50),
                    boom=[{"sym": SYM, "reach": 40.0, "smooth": 0.5}])
    assert b.trades == []
    assert b.said("unknown"), b.log


def test_the_leverage_floor_is_checked_before_anything_is_scored(tmp_path):
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0, max_lev=20)
    b, _ = scenario(tmp_path, [LEVEL_UP] * 3, thrust=5.0, exchange=ex,
                    argv=flags(min_confidence=60, min_thrust=2.0, min_lev=50),
                    boom=[{"sym": SYM, "reach": 40.0, "smooth": 0.5}])
    assert b.said("low_leverage")
    assert not b.said("below_threshold") and not b.said("weak_candle")


def test_the_book_never_asks_for_leverage_a_coin_cannot_give():
    """--min-lev has to be at least --lev, whatever those numbers are.

    This used to assert the pair of fifties the book ran at. The numbers are
    the operator's and they have moved once already; what can never move is
    the relationship. Asking for forty times on a coin the exchange caps at
    twenty is an order the exchange refuses, and in paper it is worse than a
    refusal -- the book records a trade at a leverage that was never available
    and reports a return nobody could have had.
    """
    from units import flag, has_flag
    u = ROOT / "services" / "tbt-paper.service"
    # With --per-coin-lev the book takes each coin's own ceiling, so there is
    # no leverage it can ask for and not get, and a floor would only throw
    # coins away. The invariant applies to the fixed-leverage configuration.
    if has_flag(u, "--per-coin-lev"):
        return
    lev, floor = flag(u, "--lev"), flag(u, "--min-lev")
    assert lev, "the unit does not say what leverage it trades at"
    assert floor, "the unit does not refuse coins that cannot carry it"
    assert float(floor) >= float(lev), (
        f"the book trades at {lev}x but will accept coins capped at {floor}x")


def test_the_stop_fits_inside_the_leverage_the_unit_asks_for():
    """A stop past the liquidation line is decoration.

    At --lev L the exchange closes the position at about (1/L - maintenance)
    of adverse movement. A stop beyond that never fires: the loss is the whole
    margin at a worse price, and the exit is not ours.
    """
    from units import flag
    u = ROOT / "services" / "tbt-paper.service"
    lev = float(flag(u, "--lev"))
    sl = flag(u, "--sl")
    if not sl:
        return
    assert float(sl) <= P._stop_room(lev=lev) + 1e-9, (
        f"--sl {sl}% is outside the {P._stop_room(lev=lev):.2f}% the "
        f"exchange leaves at {lev:.0f}x -- liquidation comes first")


def test_the_floor_is_off_unless_asked_for():
    src = __import__("pathlib").Path(P.__file__).read_text()
    i = src.index('"--min-lev"')
    assert "default=0.0" in src[i:i + 200]


# ------------------------------------------------------ price that ran away
def test_a_level_price_has_already_left_is_refused(tmp_path):
    """`--max-entry-r` existed and was only ever checked on the source this
    book does not trade.

    Over 125 recorded shapes, the thirteen found more than two stops from
    their level never filled -- not one. The order rests for eight bars at a
    price that is not coming back, while the signal behind it is already spent
    and can never be offered again.
    """
    # the shape's stop is ~0.8%, so 5% away is roughly six stops
    b, _ = scenario(tmp_path, [LEVEL_UP * 1.05] * 4, thrust=5.0,
                    argv=flags(min_confidence=60, min_thrust=1.5,
                               max_entry_r=2.0),
                    boom=[{"sym": SYM, "reach": 40.0, "smooth": 0.5}])
    assert b.trades == []
    assert b.said("ran_away"), b.log
    assert not b.said("PLAN"), "an order was rested at a level price had left"


def test_a_level_price_is_still_near_is_taken(tmp_path):
    """The generator's breaking candle closes further from its level than a
    real one does, so the price is put where the record says it sits.

    Measured over 125 recorded shapes, thrust and distance-from-level are
    uncorrelated (-0.14) and the median shape sits under a fifth of a stop
    from its level -- the two filters do not fight each other. Leaving the
    synthetic geometry in place would have made this test claim they do.
    """
    def back_to_the_level(i, e):
        if i == 1:
            e.set_price(SYM, LEVEL_UP * 1.002)

    b, _ = scenario(tmp_path, [LEVEL_UP, LEVEL_UP * 1.11, LEVEL_UP * 1.11],
                    thrust=5.0, extra_on_poll=back_to_the_level,
                    argv=flags(min_confidence=60, min_thrust=1.5,
                               max_entry_r=2.0),
                    boom=[{"sym": SYM, "reach": 40.0, "smooth": 0.5}])
    assert not b.said("ran_away"), b.log
    assert len(b.trades) == 1, b.log


def test_the_two_floors_do_not_fight_each_other():
    """A strong candle is not systematically a distant one.

    If they conflicted, turning both on would leave nothing -- so the
    relationship is pinned here rather than assumed.
    """
    import replay
    ROOM = P._stop_room()
    rows = []
    for sym, ohlc, meta in replay.coins():
        for t, g in replay.walk(
                ohlc, lambda p, t: (P.breakout(p, t, max_stop=ROOM)
                                    or P.trend_ride(p, t, max_stop=ROOM))):
            close = ohlc[t][3]
            rows.append((g.get("thrust") or 0.0,
                         abs(close - g["entry"]) / g["entry"] * 100
                         / g["stop_pct"]))
    if not rows:
        pytest.skip("no recorded history")
    only_thrust = [1 for th, r in rows if th >= 1.5]
    both = [1 for th, r in rows if th >= 1.5 and r <= 2.0]
    assert len(both) >= 0.9 * len(only_thrust), (
        f"the distance floor removes {len(only_thrust) - len(both)} of "
        f"{len(only_thrust)} shapes the candle floor kept -- they conflict")


def test_the_distance_is_measured_in_stops_not_percent(tmp_path):
    """A tight coin and a wide one have to be judged the same way."""
    src = __import__("pathlib").Path(P.__file__).read_text()
    i = src.index("_away_r = ")
    assert "_csl" in src[i:i + 120], "the distance is not scaled by the stop"


def test_the_guard_is_off_unless_asked_for(tmp_path):
    b, _ = scenario(tmp_path, [LEVEL_UP * 1.05] * 4, thrust=5.0,
                    argv=flags(min_confidence=60, min_thrust=1.5),
                    boom=[{"sym": SYM, "reach": 40.0, "smooth": 0.5}])
    assert not b.said("ran_away")
    assert b.said("PLAN"), b.log


def test_the_leverage_floor_is_checked_on_the_source_that_trades():
    """--min-lev was dead on the traded path, exactly as --max-entry-r was.

    It existed, it was documented, it was set to 40, and it was only ever
    checked on the shape source. On --source combo there was no floor at all,
    so the book opened USELESSUSDT at 25x under a --min-lev of 40 and nothing
    said a word. The size was honest -- it used the coin's real cap -- but a
    5% target pays 125% of the margin at 25x rather than the 200% the whole
    arithmetic assumes.
    """
    src = __import__("pathlib").Path(P.__file__).read_text()
    i = src.index("wrong_way = (a.with_trend")
    body = src[i:i + 1400]
    assert "sym_cap(sym)" in body, (
        "the combo path never asks the exchange what it caps this coin at")
    assert "low_lev" in body
    j = src.index("or wrong_way\n                          or low_lev")
    assert j > 0, "the leverage floor is computed but never gates anything"
    assert "low_leverage: the exchange caps this" in src, (
        "the refusal does not name the cap it refused on")


def test_each_coin_is_traded_at_its_own_ceiling(monkeypatch):
    """A cap of 25 means 25, and a cap of 75 means as much as the stop allows."""
    import inspect
    src = inspect.getsource(P.main)
    i = src.index("def sym_lev(")
    body = src[i:i + 1400]
    assert "a.per_coin_lev" in body, (
        "sym_lev never asks whether per-coin leverage was requested")
    assert "cap" in body


def test_the_stop_is_always_inside_the_liquidation_line():
    """A stop the exchange reaches first is not a stop.

    At 75x liquidation sits about 0.83% away and a 1.25% stop would never be
    touched -- the loss would be the whole margin at a worse price and the
    exit would be the exchange's, not ours.
    """
    import inspect
    src = inspect.getsource(P.main)
    i = src.index("def sym_lev(")
    body = src[i:i + 2200]
    assert "a.sl / 100.0 + a.maint_margin" in body, (
        "leverage is not held to what the stop can survive")
    assert "1.0 / room" in body
    assert "if not a.per_coin_lev:" in body, (
        "the ceiling is applied even when the operator named the leverage "
        "themselves, which silently resizes every trade in a configuration "
        "nobody asked to change")


def test_the_unit_asks_for_per_coin_leverage_without_a_floor():
    from units import flag, has_flag
    u = ROOT / "services" / "tbt-paper.service"
    if has_flag(u, "--per-coin-lev"):
        assert float(flag(u, "--min-lev") or 0) == 0, (
            "the book takes each coin's own ceiling and still refuses coins "
            "for having a low one")
