"""
The book on disk: restarts, crashes, locks, and what survives them.

A trading process is going to be restarted -- by systemd, by a deploy, by the
machine. Every one of those is a chance to forget a position, re-trade a
signal, or reset the equity, and each of those costs real money exactly once.
"""
from __future__ import annotations

import json
import os

import pytest

import market as M
from fakes import FakeBitunix
from harness import add_break, quiet_window, run_book
from test_lifecycle import BASE, LEVEL_UP, SYM, measured

NO_RESET = [x for x in BASE if x != "--reset"]


def _open_a_trade(tmp_path, prices=(LEVEL_UP,), polls=6, state=None):
    win, last_t = quiet_window(sym=SYM, up=True)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)

    def on_poll(i, e):
        if i == 1:
            add_break(win, thrust=5.0, up=True)
            e.set_price(SYM, LEVEL_UP)
        elif i - 2 < len(prices):
            e.set_price(SYM, prices[i - 2])

    return run_book(tmp_path, BASE if state is None else NO_RESET,
                    {0: win}, ex, polls=polls, on_poll=on_poll,
                    clock_start=last_t + M.BAR, book_state=state,
                     boom_rows=measured(SYM))


def test_an_open_position_survives_a_restart(tmp_path):
    """The whole point of the book: stopping the process is not an exit."""
    b, _ = _open_a_trade(tmp_path)
    assert len(b.open) == 1
    before = b.open[0]

    win, last_t = quiet_window(sym=SYM, up=True)
    ex = FakeBitunix(prices={SYM: LEVEL_UP}, equity=100.0)
    b2, _ = run_book(tmp_path, NO_RESET, {0: win}, ex, polls=4,
                     clock_start=last_t + M.BAR, book_state=b.state,
                     boom_rows=measured(SYM))
    assert len(b2.open) == 1
    after = b2.open[0]
    for k in ("sym", "side", "entry", "tp", "sl", "qty", "margin", "notional"):
        assert after[k] == before[k], k


def test_a_restarted_book_still_closes_the_position_it_inherited(tmp_path):
    b, _ = _open_a_trade(tmp_path)
    assert len(b.open) == 1

    win, last_t = quiet_window(sym=SYM, up=True)
    ex = FakeBitunix(prices={SYM: LEVEL_UP * 1.11}, equity=100.0)
    b2, _ = run_book(tmp_path, NO_RESET, {0: win}, ex, polls=4,
                     clock_start=last_t + M.BAR, book_state=b.state,
                     boom_rows=measured(SYM))
    assert len(b2.closed) == 1
    assert b2.closed[0]["reason"] == "target"
    assert b2.equity > 100.0


def test_the_equity_is_not_reset_by_a_restart(tmp_path):
    b, _ = _open_a_trade(tmp_path, prices=(LEVEL_UP, LEVEL_UP * 1.11,
                                           LEVEL_UP * 1.11), polls=8)
    assert b.equity > 100.0
    won = b.equity

    win, last_t = quiet_window(sym=SYM, up=True)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    b2, _ = run_book(tmp_path, NO_RESET, {0: win}, ex, polls=3,
                     clock_start=last_t + M.BAR, book_state=b.state,
                     boom_rows=measured(SYM))
    assert b2.equity == pytest.approx(won)
    assert b2.state["start"] == pytest.approx(100.0)


def test_a_signal_already_acted_on_is_not_traded_again_after_a_restart(tmp_path):
    """`seen` is on disk for exactly this reason."""
    b, _ = _open_a_trade(tmp_path)
    assert len(b.trades) == 1
    keys_before = len(b.state["seen"])
    assert keys_before > 0

    win, last_t = quiet_window(sym=SYM, up=True)
    add_break(win, thrust=5.0, up=True)          # the same shape, still there
    ex = FakeBitunix(prices={SYM: LEVEL_UP}, equity=100.0)
    b2, _ = run_book(tmp_path, NO_RESET, {0: win}, ex, polls=5,
                     clock_start=last_t + M.BAR, book_state=b.state,
                     boom_rows=measured(SYM))
    assert len(b2.trades) == 1, "the same shape was traded twice"


def test_a_book_that_will_not_parse_refuses_to_start(tmp_path):
    """Continuing would reset the equity and re-trade the chart's history."""
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "paper.json").write_text("{not json")
    win, last_t = quiet_window(sym=SYM)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    b, _ = run_book(tmp_path, NO_RESET, {0: win}, ex, polls=3,
                    clock_start=last_t + M.BAR,
                     boom_rows=measured(SYM))
    assert b.state == {} or b.state.get("trades") is None or not b.trades
    assert (data / "paper.json").read_text() == "{not json", (
        "a corrupt book was overwritten instead of preserved")


def test_the_book_is_written_atomically(tmp_path):
    """A truncated write is a lost account; the file is replaced, not edited."""
    b, _ = _open_a_trade(tmp_path)
    raw = (tmp_path / "data" / "paper.json").read_text()
    json.loads(raw)                       # parses in full
    assert not (tmp_path / "data" / "paper.tmp").exists()


def test_a_second_process_on_the_same_book_is_refused(tmp_path):
    """Two writers on one book is silent corruption, not a crash."""
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "paper.lock").write_text(str(os.getpid()))    # us: alive
    win, last_t = quiet_window(sym=SYM)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)

    def on_poll(i, e):
        if i == 1:
            add_break(win, thrust=5.0, up=True)
            e.set_price(SYM, LEVEL_UP)

    b, _ = run_book(tmp_path, NO_RESET, {0: win}, ex, polls=5, on_poll=on_poll,
                    clock_start=last_t + M.BAR,
                     boom_rows=measured(SYM))
    assert b.trades == [], "a second process traded on a locked book"


def test_a_stale_lock_from_a_dead_process_is_taken(tmp_path):
    """A crash must not leave the book unopenable."""
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "paper.lock").write_text("999999")           # no such pid
    b, _ = _open_a_trade(tmp_path)
    assert len(b.trades) == 1


def test_old_seen_keys_are_pruned_but_recent_ones_are_kept(tmp_path):
    """The file must not grow until a truncated write corrupts it."""
    ancient = ["BITUNIX:OLDUSDT.P", int(M.T0 - 30 * 86400), "BUY", False]
    recent = ["BITUNIX:NEWUSDT.P", int(M.T0), "BUY", False]
    state = {"equity": 100.0, "start": 100.0, "trades": [],
             "seen": [ancient, recent], "known_syms": []}
    b, _ = _open_a_trade(tmp_path, state=state)
    keys = {tuple(k) for k in b.state["seen"]}
    assert tuple(recent) in keys
    assert tuple(ancient) not in keys


def test_the_old_three_part_seen_key_still_blocks_both_streams(tmp_path):
    """A saved book from before `counter` was part of the key.

    Widening it matters: without it every signal on the chart reads as new and
    is traded at once, because a non-empty book means the absorb pass is off.
    """
    win, last_t = quiet_window(sym=SYM, up=True)
    sig_t, _ = add_break(win, thrust=5.0, up=True)
    state = {"equity": 100.0, "start": 100.0, "trades": [],
             "seen": [[f"BITUNIX:{SYM}.P", sig_t, "BUY"]],
             "known_syms": [f"BITUNIX:{SYM}.P"]}
    ex = FakeBitunix(prices={SYM: LEVEL_UP}, equity=100.0)
    b, _ = run_book(tmp_path, NO_RESET, {0: win}, ex, polls=5,
                    clock_start=sig_t + M.BAR, book_state=state,
                     boom_rows=measured(SYM))
    assert b.trades == [], "an old-format key failed to block the signal"


def test_a_reset_starts_a_genuinely_fresh_book(tmp_path):
    b, _ = _open_a_trade(tmp_path, prices=(LEVEL_UP, LEVEL_UP * 1.11,
                                           LEVEL_UP * 1.11), polls=8)
    assert b.equity > 100.0
    win, last_t = quiet_window(sym=SYM)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    b2, _ = run_book(tmp_path, BASE, {0: win}, ex, polls=3,
                     clock_start=last_t + M.BAR, book_state=b.state,
                     boom_rows=measured(SYM))
    assert b2.equity == pytest.approx(100.0)
    assert b2.trades == []


# ------------------------------------------------- orders waiting to fill
def test_a_resting_order_survives_a_restart(tmp_path):
    """A plan consumed and then discarded is a signal spent for nothing.

    `resting` lived only in memory, so every restart threw the pending orders
    away -- and the signal behind each one is already in `seen`, so it was
    never offered again. With Restart=always that happened on every crash and
    every deploy.
    """
    win, last_t = quiet_window(sym=SYM, up=True)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)

    def on_poll(i, e):
        if i == 1:
            add_break(win, thrust=5.0, up=True)
            e.set_price(SYM, LEVEL_UP * 1.05)      # never comes back yet

    b, _ = run_book(tmp_path, BASE, {0: win}, ex, polls=5, on_poll=on_poll,
                    clock_start=last_t + M.BAR, boom_rows=measured(SYM))
    assert b.said("PLAN"), b.log
    assert b.trades == []
    saved = b.state.get("resting")
    assert saved, "the pending order was not written to the book"
    assert saved[0]["sym"] == SYM
    assert saved[0]["want"] == pytest.approx(LEVEL_UP)

    # restart, and let price come back to the level
    win2, last2 = quiet_window(sym=SYM, up=True)
    ex2 = FakeBitunix(prices={SYM: LEVEL_UP}, equity=100.0)
    b2, _ = run_book(tmp_path, NO_RESET, {0: win2}, ex2, polls=5,
                     clock_start=last_t + M.BAR, book_state=b.state,
                     boom_rows=measured(SYM))
    assert b2.said("carried 1 resting order"), b2.log
    assert len(b2.trades) == 1, "the carried order never filled"
    assert b2.trades[0]["entry"] == pytest.approx(LEVEL_UP)


def test_an_order_whose_window_passed_is_not_carried(tmp_path):
    """Restoring a stale one would fill at a price the plan never saw."""
    state = {"equity": 100.0, "start": 100.0, "trades": [], "seen": [],
             "known_syms": [], "resting": [{
                 "sym": SYM, "side": "BUY", "want": LEVEL_UP, "give_up": 0.0,
                 "signal_px": LEVEL_UP, "placed": M.T0, "deadline": M.T0,
                 "expires": M.T0, "best_seen": LEVEL_UP, "sig": {},
                 "live": False, "order_id": "", "client_id": "", "filled": 0.0,
                 "qty": 0.0, "settled": False}]}
    win, last_t = quiet_window(sym=SYM, up=True)
    ex = FakeBitunix(prices={SYM: LEVEL_UP}, equity=100.0)
    b, _ = run_book(tmp_path, NO_RESET, {0: win}, ex, polls=4,
                    clock_start=M.T0 + 10 * M.BAR, book_state=state,
                    boom_rows=measured(SYM))
    assert b.trades == []
    assert b.said("its window had already passed"), b.log


def test_a_live_order_is_not_re_imagined_from_the_book(tmp_path):
    """Whatever is resting on the exchange is the exchange's answer, not ours."""
    state = {"equity": 100.0, "start": 100.0, "trades": [], "seen": [],
             "known_syms": [], "resting": [{
                 "sym": SYM, "side": "BUY", "want": LEVEL_UP, "give_up": 0.0,
                 "signal_px": LEVEL_UP, "placed": M.T0, "deadline": M.T0 + 1e6,
                 "expires": M.T0 + 1e6, "best_seen": LEVEL_UP, "sig": {},
                 "live": True, "order_id": "1", "client_id": "tbtx",
                 "filled": 0.0, "qty": 1.0, "settled": False}]}
    win, last_t = quiet_window(sym=SYM, up=True)
    ex = FakeBitunix(prices={SYM: LEVEL_UP}, equity=100.0)
    b, _ = run_book(tmp_path, NO_RESET, {0: win}, ex, polls=4,
                    clock_start=M.T0 + M.BAR, book_state=state,
                    boom_rows=measured(SYM))
    assert b.trades == [], "a live order was filled from the book alone"
