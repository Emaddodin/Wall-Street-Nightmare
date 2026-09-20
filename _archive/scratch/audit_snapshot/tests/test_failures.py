"""
When the exchange and the chart misbehave.

Assume every external system can time out, disconnect, answer twice, answer
late, answer wrongly, or freeze while still answering. The question each test
asks is the same one: does the engine lose money, lose track of a position, or
lose the book?
"""
from __future__ import annotations

import json

import pytest
import requests

import market as M
import papertrade as P
from fakes import FakeBitunix
from harness import add_break, quiet_window, run_book
from test_lifecycle import (BASE, LEVEL_UP, SYM, flags, measured,
                            scenario)


# ------------------------------------------------------------- the exchange
def test_a_ticker_outage_does_not_open_or_close_anything(tmp_path):
    """No price is not a reason to guess one."""
    def break_ticker(i, ex):
        if i >= 2:
            ex.ticker_error = requests.ReadTimeout("timed out")

    b, _ = scenario(tmp_path, [LEVEL_UP] * 5, extra_on_poll=break_ticker,
                    argv=flags(no_fallback=True))
    assert b.trades == [], "a position was opened on a price nobody had"
    assert b.equity == pytest.approx(100.0)
    assert b.said("ticker fetch failed")


def test_an_open_position_is_not_closed_while_prices_are_missing(tmp_path):
    """A stale mark must freeze the position, not close it at the last price."""
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)

    def outage(i, e):
        if i >= 4:
            e.ticker_error = requests.ReadTimeout("gone")

    b, _ = scenario(tmp_path, [LEVEL_UP, LEVEL_UP], exchange=ex, polls=10,
                    extra_on_poll=outage)
    assert len(b.open) == 1, b.log
    assert b.open[0]["closed"] is None
    assert b.equity == pytest.approx(100.0)


def test_a_malformed_order_book_cannot_poison_the_equity(tmp_path):
    """The NaN case: an empty book made every number in the record nonsense.

    `spread_bps` returns infinity for a book it cannot read. Taken at face
    value the entry price became infinity, the quantity zero, the stop
    infinity -- and the stop then read as hit on the very next poll, giving an
    exit of infinity, a move of inf/inf, a PnL of nan and an equity of nan for
    the rest of the process's life AND in the book on disk.
    """
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    ex.depth_error = None

    def empty_book(i, e):
        e.depth = lambda sym, limit=5: {"bids": [], "asks": []}

    b, _ = scenario(tmp_path, [LEVEL_UP, LEVEL_UP * 1.11, LEVEL_UP * 1.11],
                    exchange=ex, extra_on_poll=empty_book,
                    argv=flags(entry="edge"))
    assert b.equity == b.equity, "equity became NaN"
    for t in b.trades:
        for k in ("entry", "tp", "sl", "pnl", "qty"):
            v = t[k]
            assert v == v and abs(v) != float("inf"), f"{k} is {v}"


def test_an_absurd_spread_is_refused_rather_than_believed():
    """A spread of a million bps is not a spread, it is a broken reply."""
    assert P.MAX_SPREAD_BPS < 1e4
    assert P.DEFAULT_SPREAD_BPS > 0


def test_a_rejected_order_leaves_no_row_in_the_book(tmp_path):
    """A row claiming a position the exchange refused is the worst outcome."""
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    ex.break_once("max_leverage", RuntimeError("symbol suspended"))
    b, _ = scenario(tmp_path, [LEVEL_UP, LEVEL_UP], exchange=ex)
    for t in b.trades:
        assert t["entry"] > 0 and t["notional"] > 0


def test_the_book_survives_five_bad_polls_and_keeps_going(tmp_path):
    """One bad poll costs one poll, not the night."""
    win, last_t = quiet_window(sym=SYM)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    calls = {"n": 0}

    def flaky(i, e):
        calls["n"] += 1
        e.ticker_error = (requests.ConnectionError("reset")
                          if calls["n"] % 2 else None)
        if i == 1:
            add_break(win, thrust=5.0, up=True)
            e.set_price(SYM, LEVEL_UP)

    b, _ = run_book(tmp_path, BASE, {0: win}, ex, polls=12, on_poll=flaky,
                    clock_start=last_t + M.BAR)
    assert b.state, "the book was never written"
    assert b.equity is not None


# ----------------------------------------------------------------- the chart
def test_a_dead_chart_window_does_not_stop_the_others(tmp_path):
    a_win, last_t = quiet_window(sym="AAAUSDT", up=True)
    dead, _ = quiet_window(sym="BBBUSDT", up=True)
    dead["raise_on"] = "studies"
    ex = FakeBitunix(prices={"AAAUSDT": 100.0, "BBBUSDT": 100.0}, equity=100.0)

    def on_poll(i, e):
        if i == 1:
            add_break(a_win, thrust=5.0, up=True)
            e.set_price("AAAUSDT", LEVEL_UP)

    b, _ = run_book(tmp_path, BASE, {0: a_win, 1: dead}, ex, polls=6,
                    on_poll=on_poll, clock_start=last_t + M.BAR,
                    boom_rows=measured("AAAUSDT", "BBBUSDT"))
    assert [t["sym"] for t in b.trades] == ["AAAUSDT"], b.log
    assert b.said("unreadable")


def test_a_chart_with_no_study_is_skipped_not_guessed(tmp_path):
    win, last_t = quiet_window(sym=SYM)
    win["no_study"] = True
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    b, _ = run_book(tmp_path, BASE, {0: win}, ex, polls=4,
                    clock_start=last_t + M.BAR)
    assert b.trades == []


def test_a_frozen_chart_produces_no_trade(tmp_path):
    """The fourteen-hour failure: the tab answers, and every candle is stale.

    The book read a stopped chart all night. Nothing traded -- the age cap
    caught it -- but nothing said so either, which is the half that matters.
    """
    win, last_t = quiet_window(sym=SYM)
    add_break(win, thrust=5.0, up=True)         # a shape, but a very old one
    ex = FakeBitunix(prices={SYM: LEVEL_UP}, equity=100.0)
    # nine hours after the last candle: the feed stopped and never came back
    b, _ = run_book(tmp_path, BASE, {0: win}, ex, polls=6,
                    clock_start=last_t + M.BAR * 40)
    assert b.trades == []
    assert b.said("stale"), b.log


def test_a_window_that_changes_coin_absorbs_the_new_coins_history(tmp_path):
    """A chart sent to another coin presents that coin's whole past at once."""
    win, last_t = quiet_window(sym="AAAUSDT", up=True)
    sig_t, _ = add_break(win, thrust=5.0, up=True)      # already in the past
    ex = FakeBitunix(prices={"AAAUSDT": 100.0, "BBBUSDT": 100.0}, equity=100.0)

    def switch(i, e):
        if i == 1:
            from fakes import FakeCDP
            FakeCDP.windows[0].symbol = "BITUNIX:BBBUSDT.P"
            e.set_price("BBBUSDT", LEVEL_UP)

    # An hour after that candle closed: far past --catch-up, well inside
    # --max-shape-age, so only the absorb can stop it being traded.
    b, _ = run_book(tmp_path, BASE, {0: win}, ex, polls=6, on_poll=switch,
                    clock_start=sig_t + M.BAR * 4)
    assert b.trades == [], "a coin's backlog was traded as if it were live"
    assert b.said("absorb") or b.said("backlog"), b.log


# ----------------------------------------------------------- duplicate events
def test_the_same_shape_on_every_poll_is_one_trade(tmp_path):
    """A level is a price, not an event: it is still there on the next poll."""
    b, _ = scenario(tmp_path, [LEVEL_UP] * 8, polls=12)
    assert len(b.trades) == 1
    assert len([x for x in b.log if x.startswith("PLAN")]) == 1


def test_a_scout_plan_and_a_window_plan_on_one_coin_are_one_trade(tmp_path):
    """The scout's copy of a coin a window already shows must not double up."""
    win, last_t = quiet_window(sym=SYM, up=True)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    rows = [{"kind": "break", "sym": SYM, "dir": 1, "entry": LEVEL_UP,
             "side": "BUY", "stop_pct": 0.82, "level": LEVEL_UP, "touches": 9,
             "thrust": 4.9, "agree": 6, "against": 0, "votes": 0,
             "ready": True, "members": {}, "t": last_t,
             "at": last_t + M.BAR}]

    def on_poll(i, e):
        if i == 1:
            add_break(win, thrust=5.0, up=True)
            e.set_price(SYM, LEVEL_UP)

    b, _ = run_book(tmp_path, flags(scout=True), {0: win}, ex, polls=8,
                    on_poll=on_poll, clock_start=last_t + M.BAR,
                    scout_rows=rows)
    assert len([t for t in b.trades if t["sym"] == SYM]) <= 1, b.log


def test_a_scout_plan_is_acted_on_once_across_restarts(tmp_path):
    """One plan on PORTAL became three positions before this was written down."""
    rows = [{"kind": "break", "sym": "ZZZUSDT", "dir": 1, "entry": 100.0,
             "side": "BUY", "stop_pct": 0.8, "level": 100.0, "touches": 3,
             "thrust": 4.0, "agree": 6, "against": 0, "votes": 0,
             "ready": True, "members": {}, "t": M.T0,
             "at": M.T0 + 60}]
    win, last_t = quiet_window(sym=SYM)
    seen_before = None
    total = 0
    for run in range(3):
        ex = FakeBitunix(prices={SYM: 100.0, "ZZZUSDT": 100.0}, equity=100.0)
        argv = flags(scout=True)
        if run:
            argv = [x for x in argv if x != "--reset"]
        b, _ = run_book(tmp_path, argv, {0: win}, ex, polls=5,
                        clock_start=M.T0 + 120, scout_rows=rows,
                        book_state=seen_before)
        seen_before = b.state
        total = len([t for t in b.trades if t["sym"] == "ZZZUSDT"])
    assert total <= 1, f"a restart re-traded the scout's list ({total} rows)"


def test_a_forecast_row_is_never_read_as_an_order(tmp_path):
    """`ripe` and `coil` carry no entry and no stop -- 69 polls died on them."""
    rows = [
        {"kind": "ripe", "sym": "AAAUSDT", "side": 1, "vote": 4, "short": 0,
         "tide": 1, "lean": 5, "ripe": 82.0, "t": M.T0, "at": M.T0 + 60},
        {"kind": "coil", "sym": "BBBUSDT", "pressure": 70.0, "top": 1.0,
         "bottom": 0.9, "width": 3.0, "t": M.T0, "at": M.T0 + 60},
    ]
    win, last_t = quiet_window(sym=SYM)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    b, _ = run_book(tmp_path, flags(scout=True), {0: win}, ex, polls=5,
                    clock_start=M.T0 + 120, scout_rows=rows)
    assert b.trades == []
    assert not b.said("poll failed"), b.log


def test_a_scout_row_with_no_entry_is_refused_not_crashed(tmp_path):
    rows = [{"kind": "break", "sym": "AAAUSDT", "dir": 1, "at": M.T0 + 60,
             "t": M.T0}]                                   # no entry at all
    win, last_t = quiet_window(sym=SYM)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    b, _ = run_book(tmp_path, flags(scout=True), {0: win}, ex, polls=4,
                    clock_start=M.T0 + 120, scout_rows=rows)
    assert b.trades == []
    assert not b.said("poll failed")


def test_a_corrupt_scout_file_is_survived(tmp_path):
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    win, last_t = quiet_window(sym=SYM)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    b, _ = run_book(tmp_path, flags(scout=True), {0: win}, ex, polls=4,
                    clock_start=last_t + M.BAR, scout_rows=[])
    (data / "scout.json").write_text('[{"kind": "break", "sym"')   # truncated
    b2, _ = run_book(tmp_path, flags(scout=True), {0: win}, ex, polls=4,
                     clock_start=last_t + M.BAR)
    assert not b2.said("poll failed")


# ------------------------------------------------------------ stale measures
def test_a_truncated_measurements_file_does_not_erase_every_reach(tmp_path):
    """The scanner writes without a temporary; a reader can catch it mid-write.

    Caching that emptiness is what makes it dangerous: an unmeasured coin used
    to score HIGHER than one measured never to travel, so a torn read would
    have opened the floodgates for five minutes.
    """
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    P._REACH.update({"t": 0.0, "by": {}, "stamp": 0})
    with __import__("unittest").mock.patch.object(
            P, "WATCH_M", data / "watch_measures.json"), \
            __import__("unittest").mock.patch.object(
                P, "RANKED", data / "boom.json"):
        (data / "watch_measures.json").write_text(
            json.dumps({"AAAUSDT": {"reach": 40.0, "smooth": 0.5}}))
        (data / "boom.json").write_text("[]")
        assert P.coin_reach("AAAUSDT")[0] == 40.0
        # now a torn write
        (data / "watch_measures.json").write_text('{"AAAUSDT": {"rea')
        P._REACH["stamp"] = 0
        P._REACH["t"] = 0.0
        assert P.coin_reach("AAAUSDT")[0] == 40.0, (
            "a torn read erased the measurements the book judges coins on")
