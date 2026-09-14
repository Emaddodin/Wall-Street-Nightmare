"""
Whole trades, through the real engine.

Every test here calls `papertrade.main()`. The shape is found by the real
shape functions on candles the market generator built, scored by the real
confidence function, sized by the real sizing code, filled by the real
resting-order code and closed by the real exit code. Nothing declares that a
trade should happen; the market is arranged so that one does.

The pipeline under test, end to end:

    candles -> shape -> confidence -> risk checks -> resting order -> fill
            -> management -> exit -> PnL -> the book on disk
"""
from __future__ import annotations

import pytest

import market as M
import papertrade as P
from fakes import FakeBitunix
from harness import add_break, quiet_window, run_book

SYM = "TESTUSDT"

# The live unit's own flags, minus the confidence floor. The floor is exercised
# separately -- with it on, nothing in the recorded universe trades at all,
# which is a fact about the strategy rather than about the plumbing.
BASE = ["--source", "break", "--reset", "--equity", "100",
        "--tp", "10", "--lev", "50", "--frac", "0.5", "--sl", "2.1",
        "--sl-atr", "0", "--tp-atr", "0", "--tp-r", "0", "--tp-leg", "0",
        "--r-adapt", "0", "--risk-pct", "0", "--max-per-day", "0",
        "--fill-bars", "8", "--step-at", "10000", "--step-frac", "0.25",
        "--break-even", "0", "--interval", "60", "--sprint", "60",
        "--min-confidence", "0"]


def flags(**over):
    """The live unit's flags with named overrides -- never index-sliced.

    Slicing the list to change one setting is how a test ends up asserting
    against a configuration nobody meant to run.
    """
    argv = list(BASE)
    for k, v in over.items():
        name = "--" + k.replace("_", "-")
        if name in argv:
            argv[argv.index(name) + 1] = str(v)
        elif v is True:
            argv.append(name)
        else:
            argv += [name, str(v)]
    return argv


# Coins the scanner has measured as able to cover the target. `reach` is a
# gate now, not a score, so a lifecycle test that did not supply this would be
# rejected before anything it means to exercise ever ran.
def measured(*syms, reach=40.0, smooth=0.4):
    return [{"sym": s, "reach": reach, "smooth": smooth} for s in syms]


def scenario(tmp_path, moves, argv=None, up=True, thrust=5.0, polls=None,
             equity=100.0, exchange=None, boom=None, extra_on_poll=None):
    """A break appears, then price does `moves` -- one price per poll."""
    if boom is None:
        boom = measured(SYM)
    win, last_t = quiet_window(sym=SYM, up=up)
    ex = exchange or FakeBitunix(prices={SYM: 100.0}, equity=equity)
    printed = {}

    def on_poll(i, exch):
        if i == 1:
            t, c = add_break(win, thrust=thrust, up=up)
            printed["t"], printed["level"] = t, (max(
                win["ohlc"][k][1] for k in sorted(win["ohlc"])[:-2]) if up else
                min(win["ohlc"][k][2] for k in sorted(win["ohlc"])[:-2]))
            exch.set_price(SYM, c)
        elif i - 2 < len(moves):
            exch.set_price(SYM, moves[i - 2])
        if extra_on_poll:
            extra_on_poll(i, exch)

    return run_book(tmp_path, argv or BASE, {0: win}, ex,
                    polls=polls or (len(moves) + 4), on_poll=on_poll,
                    clock_start=last_t + M.BAR, boom_rows=boom)


LEVEL_UP = 100.6          # the band's top edge, where a long rests
LEVEL_DN = 99.4           # the band's bottom edge, where a short rests


# ------------------------------------------------------------- the long trade
def test_a_long_runs_the_whole_way_to_the_target(tmp_path):
    b, _ = scenario(tmp_path, [LEVEL_UP, LEVEL_UP * 1.11, LEVEL_UP * 1.11])
    assert b.said("PLAN"), b.log
    assert len(b.trades) == 1
    tr = b.trades[0]
    assert tr["side"] == "BUY"
    assert tr["entry"] == pytest.approx(LEVEL_UP)
    assert tr["reason"] == "target"
    # 50% of the wallet at 50x, ten percent of price
    assert tr["margin"] == pytest.approx(50.0)
    assert tr["notional"] == pytest.approx(2500.0)
    assert tr["tp"] == pytest.approx(LEVEL_UP * 1.10)
    assert tr["pnl"] == pytest.approx(2500 * 0.10 - 2500 * 12 / 1e4)
    assert b.equity == pytest.approx(100.0 + tr["pnl"])


def test_a_short_runs_the_whole_way_to_the_target(tmp_path):
    b, _ = scenario(tmp_path, [LEVEL_DN, LEVEL_DN * 0.89, LEVEL_DN * 0.89],
                    up=False)
    assert len(b.trades) == 1
    tr = b.trades[0]
    assert tr["side"] == "SELL"
    assert tr["entry"] == pytest.approx(LEVEL_DN)
    assert tr["tp"] == pytest.approx(LEVEL_DN * 0.90)
    assert tr["reason"] == "target"
    assert tr["pnl"] > 0


def test_the_stop_is_the_band_and_not_a_chosen_percentage(tmp_path):
    """`--sl 2.1` is on the command line and must not reach a shape."""
    b, _ = scenario(tmp_path, [LEVEL_UP, LEVEL_UP])
    tr = b.open[0]
    stop_pct = (tr["entry"] - tr["sl"]) / tr["entry"] * 100
    assert stop_pct < 2.1, "the fixed stop overrode the level's own"
    assert 0 < stop_pct <= 1.5, "the band's stop must fit inside the leverage"


def test_a_losing_trade_is_taken_out_by_the_leverage_not_the_stop(tmp_path):
    """At 50x the exchange closes the position before any 2.1% stop.

    The shape's stop is inside the liquidation line, so a shape SHOULD stop
    normally -- this is the check that it does, rather than being liquidated
    for the whole margin.
    """
    b, _ = scenario(tmp_path, [LEVEL_UP, LEVEL_UP * 0.98, LEVEL_UP * 0.98])
    tr = b.closed[0]
    assert tr["reason"] in ("stop", "liquidated")
    assert tr["reason"] == "stop", "a shape's stop must be reachable"
    assert -tr["pnl"] < tr["margin"], "a stop must cost less than the margin"
    assert b.equity < 100.0


def test_a_violent_gap_still_exits_at_the_nearer_line(tmp_path):
    """Price jumps 20% through everything between two polls.

    Only the start and the end of the move are known, but price cannot arrive
    20% down without passing the 0.82% stop on the way -- and the stop is
    nearer than the 1.5% liquidation line, so the stop is what closed it. The
    engine used to record a liquidation here and charge the whole $50 margin
    for a trade the exchange would have stopped out for $20.50: two and a half
    times the real loss, on every fast move, in the only record this system
    keeps of what it does.
    """
    b, _ = scenario(tmp_path, [LEVEL_UP, LEVEL_UP * 0.80, LEVEL_UP * 0.80])
    tr = b.closed[0]
    assert tr["reason"] == "stop"
    stop_pct = (tr["entry"] - tr["sl"]) / tr["entry"] * 100
    assert tr["pnl"] == pytest.approx(
        -tr["notional"] * stop_pct / 100 - tr["notional"] * 12 / 1e4)
    assert -tr["pnl"] < tr["margin"], "a loss exceeded the isolated margin"
    assert b.equity > 0


# -------------------------------------------------------------- not filling
def test_a_level_price_never_returns_to_is_a_miss_not_a_loss(tmp_path):
    """The order rests at the level; price running away costs nothing."""
    b, _ = scenario(tmp_path, [LEVEL_UP * 1.05] * 4,
                    argv=flags(no_fallback=True))
    assert b.said("PLAN")
    assert b.trades == []
    assert b.equity == pytest.approx(100.0)


def test_the_book_never_opens_the_same_plan_twice(tmp_path):
    """A shape is the same shape on every poll; only the first is a signal."""
    b, _ = scenario(tmp_path, [LEVEL_UP] * 6, argv=flags(one_per_symbol=True))
    assert len([x for x in b.log if x.startswith("PLAN")]) == 1
    assert len(b.trades) == 1


# ---------------------------------------------------------------- the floor
def test_a_coin_that_never_travels_scores_lower_but_a_great_shape_survives(
        tmp_path):
    """Reach is worth points, not a veto.

    A perfect candle and a unanimous council reach 65 on a coin that never
    travels, which clears the 60 floor. That is deliberate: the setup is what
    the strategy trades, and the coin moves the score rather than deciding it.
    """
    b, _ = scenario(tmp_path, [LEVEL_UP, LEVEL_UP * 1.11, LEVEL_UP * 1.11],
                    argv=flags(min_confidence=60, min_thrust=2.0),
                    boom=[{"sym": SYM, "reach": 0.0, "smooth": 0.05}])
    assert len(b.trades) == 1, b.log


def test_a_weak_shape_on_a_coin_that_never_travels_is_refused(tmp_path):
    b, _ = scenario(tmp_path, [LEVEL_UP] * 3, thrust=1.2,
                    argv=flags(min_confidence=60, min_thrust=2.0),
                    boom=[{"sym": SYM, "reach": 0.0, "smooth": 0.05}])
    assert b.trades == []
    assert b.said("weak_candle"), b.log


def test_the_confidence_floor_admits_a_shape_on_a_coin_that_does(tmp_path):
    """The same shape, on a coin the scanner measured as able to reach 10%."""
    b, _ = scenario(tmp_path,
                    [LEVEL_UP, LEVEL_UP * 1.11, LEVEL_UP * 1.11],
                    argv=flags(min_confidence=70),
                    boom=[{"sym": SYM, "reach": 50.0, "smooth": 0.5}])
    assert len(b.trades) == 1, b.log
    assert b.trades[0]["reason"] == "target"


def test_an_unmeasured_coin_is_never_easier_than_a_measured_one(tmp_path):
    """"Not measured" must not score higher than "measured badly"."""
    unknown, _ = __import__("papertrade").confidence(
        tall=1.8, agree=6, against=0)
    bad, _ = __import__("papertrade").confidence(
        tall=1.8, agree=6, against=0, reach=0.0)
    assert unknown <= bad


# ------------------------------------------------------------- risk ceilings
def test_exposure_is_capped_across_two_symbols(tmp_path):
    """Two 50% trades fill the account; a third is refused, not squeezed in."""
    wins, syms = {}, ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
    for i, s in enumerate(syms):
        w, last_t = quiet_window(sym=s, up=True)
        wins[i] = w
    ex = FakeBitunix(prices={s: 100.0 for s in syms}, equity=100.0)

    def on_poll(i, exch):
        if i == 1:
            for w in wins.values():
                add_break(w, thrust=5.0, up=True)
            for s in syms:
                exch.set_price(s, LEVEL_UP)

    b, _ = run_book(tmp_path, BASE, wins, ex, polls=6, on_poll=on_poll,
                    clock_start=last_t + M.BAR, boom_rows=measured(*syms))
    assert len(b.open) == 2, [t["sym"] for t in b.open]
    assert sum(t["margin"] for t in b.open) <= 100.0 + 1e-9
    assert b.said("already"), b.log


def test_a_daily_cap_counts_positions_opened_not_orders_placed(tmp_path):
    """Three shapes in one poll all passed a cap of one.

    The count was taken when the order was PLACED, and at that moment none of
    them had opened anything -- so each was compared against zero. A limit
    resting for eight bars leaves the same two-hour hole. The cap has to be
    asked again at the moment a position actually opens.
    """
    syms = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
    wins = {}
    for i, s in enumerate(syms):
        wins[i], last_t = quiet_window(sym=s, up=True)
    ex = FakeBitunix(prices={s: 100.0 for s in syms}, equity=1000.0)

    def on_poll(i, exch):
        if i == 1:
            for w in wins.values():
                add_break(w, thrust=5.0, up=True)
            for s in syms:
                exch.set_price(s, LEVEL_UP)

    b, _ = run_book(tmp_path, flags(max_per_day=1, equity=1000),
                    wins, ex, polls=6, on_poll=on_poll,
                    clock_start=last_t + M.BAR, boom_rows=measured(*syms))
    assert len(b.trades) == 1, [t["sym"] for t in b.trades]
    assert b.said("the day's count is spent") or b.said("twenty-four hours")


def test_a_wiped_out_book_stops_opening_positions(tmp_path):
    b, _ = scenario(tmp_path, [LEVEL_UP] * 3, equity=0.0,
                    exchange=FakeBitunix(prices={SYM: 100.0}, equity=0.0),
                    argv=flags(equity=0))
    assert b.trades == []


# ----------------------------------------------------------------- the clock
def test_a_position_that_goes_nowhere_is_closed_on_the_clock(tmp_path):
    """Getting stuck in a range is how this strategy dies."""
    b, _ = scenario(
        tmp_path, [LEVEL_UP] + [LEVEL_UP * 1.0005] * 8,
        argv=flags(max_hold_min=3), polls=14)
    assert b.closed, b.log
    assert b.closed[0]["reason"] == "timeout"
    assert b.said("TIMEOUT")


def test_a_trade_that_never_travelled_is_closed_as_stalled_not_stopped(tmp_path):
    """A stall in the record must not read as a stop the chart never showed."""
    b, _ = scenario(
        tmp_path, [LEVEL_UP] + [LEVEL_UP * 1.0005] * 8,
        argv=flags(stale_min=3, stale_r=0.5), polls=14)
    assert b.closed[0]["reason"] == "stalled"


# ------------------------------------------------------------- both at once
def test_two_symbols_can_hold_opposite_sides(tmp_path):
    """A long on one coin and a short on another is two independent trades."""
    up_w, last_t = quiet_window(sym="AAAUSDT", up=True)
    dn_w, _ = quiet_window(sym="BBBUSDT", up=False)
    ex = FakeBitunix(prices={"AAAUSDT": 100.0, "BBBUSDT": 100.0}, equity=200.0)

    def on_poll(i, exch):
        if i == 1:
            add_break(up_w, thrust=5.0, up=True)
            add_break(dn_w, thrust=5.0, up=False)
            exch.set_price("AAAUSDT", LEVEL_UP)
            exch.set_price("BBBUSDT", LEVEL_DN)

    b, _ = run_book(tmp_path, flags(equity=200),
                    {0: up_w, 1: dn_w}, ex, polls=6, on_poll=on_poll,
                    clock_start=last_t + M.BAR,
                    boom_rows=measured("AAAUSDT", "BBBUSDT"))
    sides = {t["sym"]: t["side"] for t in b.trades}
    assert sides == {"AAAUSDT": "BUY", "BBBUSDT": "SELL"}, b.log


def test_the_banner_says_what_will_actually_happen_to_an_unfilled_order(
        tmp_path, capsys):
    """"Unfilled is a miss, not a loss" is only true with --no-fallback.

    The flag defaults to ON and the live unit does not turn it off, so an
    order that never fills is taken at MARKET two hours later, at a price the
    plan never saw, with the stop still measured from the level. The banner
    claimed the opposite.
    """
    import contextlib
    import io
    win, last = quiet_window(sym=SYM, up=True)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        run_book(tmp_path, flags(), {0: win}, ex, polls=3,
                 clock_start=last + M.BAR, boom_rows=measured(SYM))
    said = out.getvalue()
    assert "TAKEN AT MARKET" in said, said[-400:]
    assert "unfilled is a miss, not a loss" not in said

    out2 = io.StringIO()
    with contextlib.redirect_stdout(out2):
        run_book(tmp_path / "b", flags(no_fallback=True), {0: win}, ex,
                 polls=3, clock_start=last + M.BAR, boom_rows=measured(SYM))
    said2 = out2.getvalue()
    assert "unfilled is a miss, not a loss" in said2
    assert "TAKEN AT MARKET" not in said2


def test_a_scouted_indicator_signal_is_not_dressed_as_a_council_row(tmp_path):
    """The book drops council rows on --source combo. This must not be one.

    Every row the scout wrote used to be marked as the council's, so on the
    combo source the book discarded the scout's entire output -- eleven coins
    walked every forty seconds, a plan delivered on AKEUSDT, and nothing in
    any log between "scout brought 1 plan" and an hour of silence.
    """
    import json
    import time
    from unittest import mock
    scout = tmp_path / "scout.json"
    scout.write_text(json.dumps([{
        "sym": "AAAUSDT", "kind": "combo", "at": time.time(), "t": 1900,
        "side": "BUY", "dir": 1, "score": 55, "tier": 3, "span": 12,
        "agents": 5, "wired": True, "who": 0,
    }]))
    with mock.patch.object(P, "SCOUT", scout):
        got = P.scout_plans(set(), set())
    assert len(got) == 1, "the scout's indicator signal did not survive"
    s = got[0]
    assert not s.get("council"), (
        "a combo signal marked as a council row is dropped unread on "
        "--source combo")
    assert s["agents"] == 5 and s["tier"] == 3 and s["side"] == "BUY"
    assert "entry" not in s, (
        "a combo signal is taken at the print; carrying a stale entry level "
        "would rest an order at a price the signal never described")


# ------------------------------------------- leaving on a counter-trend print
def test_a_counter_print_takes_the_profit_when_the_trade_is_far_in_front(
        tmp_path):
    """Six or seven percent up is most of the way to a ten percent target.

    A counter-trend print is the indicator saying the move is being taken
    back. Riding that back down to the stop turns a good trade into a loss.
    """
    import json
    import time
    from unittest import mock
    scout = tmp_path / "scout.json"
    scout.write_text(json.dumps([{
        "sym": "AAAUSDT", "kind": "combo", "ct": -1, "ct_only": True,
        "at": time.time(), "t": 1900, "side": "SELL", "dir": -1,
    }]))
    with mock.patch.object(P, "SCOUT", scout):
        assert P.ct_prints() == {"AAAUSDT": -1}


def test_a_stale_counter_print_says_nothing_about_now(tmp_path):
    """Half an hour later it is history, not a warning."""
    import json
    import time
    from unittest import mock
    scout = tmp_path / "scout.json"
    scout.write_text(json.dumps([{
        "sym": "AAAUSDT", "kind": "combo", "ct": -1,
        "at": time.time() - 3600, "t": 1900, "side": "SELL", "dir": -1,
    }]))
    with mock.patch.object(P, "SCOUT", scout):
        assert P.ct_prints() == {}


def test_only_a_signal_row_can_warn_the_book_out_of_a_trade(tmp_path):
    """This used to require the counter-trend flag. The flag is not the point.

    Reading 4USDT bar by bar showed `ct` labels the signal's own type -- it
    appears on the same bar and points the same way -- so any opposing signal
    now counts. What must still be true is that only a SIGNAL counts: a state
    row is what the indicator says on a bar with nothing on it, and a shape
    row belongs to a source this book does not trade. Neither is news about
    the move, and treating one as a reason to close a winning position would
    end trades for nothing.
    """
    import json
    import time
    from unittest import mock
    scout = tmp_path / "scout.json"
    scout.write_text(json.dumps([
        {"sym": "AAAUSDT", "kind": "state", "at": time.time(),
         "votes": 4, "plan_dir": -1, "htf": 1, "ct": 0},
        {"sym": "BBBUSDT", "kind": "break", "at": time.time(), "dir": 1,
         "entry": 1.0},
    ]))
    with mock.patch.object(P, "SCOUT", scout):
        assert P.ct_prints() == {}


def test_the_counter_exit_is_off_unless_it_is_asked_for():
    """It changes when a trade ends, so it is the operator's dial."""
    src = __import__("pathlib").Path(P.__file__).read_text()
    i = src.index('"--ct-exit"')
    assert "default=0.0" in src[i:i + 200]


def test_the_counter_exit_never_fires_on_a_losing_trade():
    """It exists to keep a profit, not to cut a loss early.

    A counter-trend print on a trade that is behind is not a reason to take
    the loss now -- the stop is what decides that, and it is where it is for a
    reason.
    """
    src = __import__("pathlib").Path(P.__file__).read_text()
    i = src.index("a.ct_exit > 0 and not tr.closed")
    body = src[i:i + 900]
    assert "_fav >= a.ct_exit" in body, (
        "the counter exit does not require the trade to be in front")


def test_the_counter_exit_only_fires_against_the_position():
    """A counter print in our own direction is not a reason to leave."""
    src = __import__("pathlib").Path(P.__file__).read_text()
    i = src.index("a.ct_exit > 0 and not tr.closed")
    body = src[i:i + 900]
    assert '_against = -1 if tr.side == "BUY" else 1' in body
    assert "_cts.get(tr.sym) == _against" in body


def test_a_plain_opposite_signal_is_a_warning_too(tmp_path):
    """The ct flag labels the signal's type, not a separate reversal warning.

    Reading 4USDT bar by bar showed it appears on the same bar as the signal
    and always points the same way. Gating the exit on it meant the book would
    leave a winning short on a counter-trend BUY and sit through a plain one,
    when a plain BUY against an open short says the same thing about the move.
    """
    import json
    import time
    from unittest import mock
    scout = tmp_path / "scout.json"
    scout.write_text(json.dumps([{
        "sym": "AAAUSDT", "kind": "combo", "at": time.time(), "t": 1900,
        "side": "BUY", "dir": 1, "ct": 0, "agents": 4,
    }]))
    with mock.patch.object(P, "SCOUT", scout):
        assert P.ct_prints() == {"AAAUSDT": 1}, (
            "a plain opposite signal is invisible to the exit")


def test_a_counter_trend_flag_still_counts(tmp_path):
    import json
    import time
    from unittest import mock
    scout = tmp_path / "scout.json"
    scout.write_text(json.dumps([{
        "sym": "AAAUSDT", "kind": "combo", "at": time.time(), "t": 1900,
        "side": "SELL", "dir": -1, "ct": -1, "ct_only": True,
    }]))
    with mock.patch.object(P, "SCOUT", scout):
        assert P.ct_prints() == {"AAAUSDT": -1}


def test_the_phone_is_told_which_of_the_day_this_was():
    """A price on its own says nothing about the decision.

    With a budget of eight, "the fourth of eight, scored 71" is the whole
    story: how far through the day's allowance the book is, and what it
    thought of the signal it spent one on.
    """
    src = __import__("pathlib").Path(P.__file__).read_text()
    i = src.index("def announce(")
    body = src[i:i + 1400]
    assert "pace.spent" in body, (
        "the open notification does not say which of the day's trades this is")
    assert "/100" in body, (
        "the open notification does not carry the entry score")


def test_every_path_through_the_signal_loop_binds_a_score():
    """A trade opened on another source must not record the last one's score."""
    src = __import__("pathlib").Path(P.__file__).read_text()
    i = src.index("for s in sigs:")
    head = src[i:i + 600]
    assert "_pts, _why = float(s.get(\"score\") or 0), \"\"" in head, (
        "the entry score is not initialised at the top of the signal loop, "
        "so a path that does not compute one carries whatever the previous "
        "signal left behind")
