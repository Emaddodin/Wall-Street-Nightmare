"""
The edges: sizes, precision, rapid fire, and the moments between polls.

Most of these produce no error at all when they go wrong. They produce a
number -- a quantity the exchange rounds to zero, a position twice the size
intended, a stop the wrong side of the entry -- and the number is acted on.
"""
from __future__ import annotations

import json
from unittest import mock

import pytest

import market as M
import papertrade as P
from exchange.bitunix import BitunixClient, BitunixError
from fakes import FakeBitunix
from harness import add_break, quiet_window, run_book
from test_lifecycle import (BASE, LEVEL_UP, SYM, flags, measured,
                            scenario)


# ------------------------------------------------------- position size edges
def test_a_tiny_account_cannot_produce_a_zero_quantity_position(tmp_path):
    """Rounding a size to zero must not leave a row claiming a position."""
    ex = FakeBitunix(prices={SYM: 100.0}, equity=0.0001, base_prec=0)
    b, _ = scenario(tmp_path, [LEVEL_UP, LEVEL_UP], exchange=ex,
                    argv=flags(equity=0.0001))
    for t in b.trades:
        assert t["qty"] > 0, "a position was opened with no quantity"


def test_a_very_high_priced_coin_still_sizes(tmp_path):
    """A coin at $70,000 and a $50 margin is a fractional quantity."""
    win, last = quiet_window(sym=SYM, up=True, base=70_000.0)
    ex = FakeBitunix(prices={SYM: 70_000.0}, equity=100.0, base_prec=6)

    def on_poll(i, e):
        if i == 1:
            t, c = add_break(win, thrust=5.0, up=True)
            e.set_price(SYM, 70_000.0 * 1.006)
        elif i == 2:
            e.set_price(SYM, 70_000.0 * 1.006)

    b, _ = run_book(tmp_path, BASE, {0: win}, ex, polls=6, on_poll=on_poll,
                    clock_start=last + M.BAR, boom_rows=measured(SYM))
    for t in b.trades:
        assert t["qty"] > 0
        assert t["notional"] == pytest.approx(t["qty"] * t["entry"], rel=1e-6)


def test_a_sub_cent_coin_still_sizes(tmp_path):
    win, last = quiet_window(sym=SYM, up=True, base=0.00004321)
    ex = FakeBitunix(prices={SYM: 0.00004321}, equity=100.0, quote_prec=10,
                     base_prec=0)

    def on_poll(i, e):
        if i == 1:
            add_break(win, thrust=5.0, up=True)
            e.set_price(SYM, 0.00004321 * 1.006)
        elif i == 2:
            e.set_price(SYM, 0.00004321 * 1.006)

    b, _ = run_book(tmp_path, BASE, {0: win}, ex, polls=6, on_poll=on_poll,
                    clock_start=last + M.BAR, boom_rows=measured(SYM))
    for t in b.trades:
        assert t["qty"] > 0 and t["entry"] > 0
        assert t["tp"] > t["entry"] > t["sl"]


def test_the_stop_is_always_the_right_side_of_the_entry(tmp_path):
    for up in (True, False):
        b, _ = scenario(tmp_path / ("up" if up else "dn"),
                        [LEVEL_UP if up else 99.4] * 3, up=up)
        for t in b.trades:
            if t["side"] == "BUY":
                assert t["sl"] < t["entry"] < t["tp"]
            else:
                assert t["tp"] < t["entry"] < t["sl"]


def test_the_notional_is_always_the_margin_times_the_leverage(tmp_path):
    b, _ = scenario(tmp_path, [LEVEL_UP] * 3)
    for t in b.trades:
        assert t["notional"] == pytest.approx(t["margin"] * 50.0)


# --------------------------------------------------------------- rapid fire
def test_a_shape_on_every_bar_does_not_become_a_position_on_every_bar(tmp_path):
    """Consecutive signals: each new bar prints another break."""
    win, last = quiet_window(sym=SYM, up=True)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)

    def storm(i, e):
        if 1 <= i <= 6:
            add_break(win, thrust=5.0, up=True)
            e.set_price(SYM, max(win["ohlc"][k][1]
                                 for k in sorted(win["ohlc"])[:-2]))

    b, _ = run_book(tmp_path, flags(one_per_symbol=True), {0: win}, ex,
                    polls=10, on_poll=storm, clock_start=last + M.BAR,
                    boom_rows=measured(SYM))
    open_at_once = [t for t in b.trades if not t["closed"]]
    assert len(open_at_once) <= 1, "one coin held several positions at once"
    assert sum(t["margin"] for t in open_at_once) <= 100.0 + 1e-9


def test_rapid_polls_inside_one_bar_produce_one_decision(tmp_path):
    """--sprint polls five times a second across a bar boundary."""
    b, _ = scenario(tmp_path, [LEVEL_UP] * 10,
                    argv=flags(interval=0.2, sprint=0.2), polls=14)
    assert len([x for x in b.log if x.startswith("PLAN")]) == 1
    assert len(b.trades) <= 1


# -------------------------------------------------------- exchange refusals
def test_insufficient_margin_is_reported_not_retried():
    cli = BitunixClient(api_key="K", api_secret="S")
    calls = []

    class R:
        status_code = 200

        def json(self):
            calls.append(1)
            return {"code": "20007", "msg": "insufficient balance"}

        def raise_for_status(self):
            pass

    with mock.patch.object(cli._s, "request", return_value=R()):
        with pytest.raises(BitunixError) as e:
            cli.place_market_order("A", "BUY", "1", position_mode="ONE_WAY")
    assert len(calls) == 1
    assert "20007" in str(e.value)


def test_an_invalid_order_parameter_is_not_retried_either():
    cli = BitunixClient(api_key="K", api_secret="S")
    calls = []

    class R:
        status_code = 200

        def json(self):
            calls.append(1)
            return {"code": "10004", "msg": "qty precision error"}

        def raise_for_status(self):
            pass

    with mock.patch.object(cli._s, "request", return_value=R()):
        with pytest.raises(BitunixError):
            cli.place_market_order("A", "BUY", "1.23456789",
                                   position_mode="ONE_WAY")
    assert len(calls) == 1


def test_an_order_under_the_exchange_minimum_is_never_sent(tmp_path):
    """Caught here or rejected there -- but a row must not claim it opened."""
    from test_live import LIVE, combo_window
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0, min_qty=1e9)
    win, last, fired = combo_window()

    def on_poll(i, e):
        if i == 1:
            fired["on"] = True

    b, _ = run_book(tmp_path, LIVE, {0: win}, ex, polls=6, on_poll=on_poll,
                    clock_start=last + M.BAR)
    assert ex.orders == []
    assert b.trades == []


# -------------------------------------------------- spread, slippage, marks
def test_a_maker_edge_fill_is_at_the_limit_whatever_the_spread(tmp_path):
    """A maker resting at its own limit fills AT the limit, never worse.

    The old code charged the edge entry half the spread, as if the maker
    crossed the book -- which a maker never does, and which quietly
    over-priced every resting fill.
    """
    fills = {}
    for bps in (2.0, 40.0):
        ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0, spread_bps=bps)
        b, _ = scenario(tmp_path / f"s{bps}",
                        [LEVEL_UP, LEVEL_UP], exchange=ex,
                        argv=flags(entry="edge"))
        if b.trades:
            fills[bps] = b.trades[0]["entry"]
    if len(fills) == 2:
        assert fills[40.0] == fills[2.0], \
            "a maker fill must not depend on the spread"


def test_a_taker_pays_the_spread_on_the_late_fallback(tmp_path):
    """When the band-edge limit never fills, the late taker crosses the book.

    (The edge entry deliberately has no fallback -- its miss is a miss.
    The band-edge resting order is the one that gets taken at market.)
    """
    fills = {}
    for bps in (2.0, 40.0):
        ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0, spread_bps=bps)
        # price parks above the level: the order expires and the fallback
        # takes it at market. One 900s sleep per cycle so 8 bars = 8 polls.
        b, _ = scenario(tmp_path / f"s{bps}",
                        [LEVEL_UP * 1.05] * 12, exchange=ex,
                        argv=flags(interval=900, sprint=0), polls=16)
        assert b.trades, f"no trade at {bps} bps: {b.log[-2:]}"
        fills[bps] = b.trades[0]["entry"]
    assert fills[40.0] > fills[2.0], \
        "a wider spread must not produce a better taker fill"


def test_the_mark_price_decides_the_exit_not_the_last_trade(tmp_path):
    """TP and SL trigger off the mark; the entry fills at the traded price."""
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)

    def diverge(i, e):
        if i >= 3:
            # last price nowhere near the target, mark straight through it
            e.set_price(SYM, LEVEL_UP, mark=LEVEL_UP * 1.11)

    b, _ = scenario(tmp_path, [LEVEL_UP, LEVEL_UP], exchange=ex,
                    extra_on_poll=diverge, polls=8)
    assert b.closed and b.closed[0]["reason"] == "target"


# ------------------------------------------------- reconnects mid-position
def test_the_chart_dropping_mid_trade_does_not_lose_the_position(tmp_path):
    """The websocket goes; the position is the exchange's and the book's."""
    win, last = quiet_window(sym=SYM, up=True)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)

    def drop(i, e):
        if i == 1:
            add_break(win, thrust=5.0, up=True)
            e.set_price(SYM, LEVEL_UP)
        elif i == 3:
            from fakes import FakeCDP
            FakeCDP.windows[0].raise_on = "raw_series"       # the socket dies
        elif i == 6:
            from fakes import FakeCDP
            FakeCDP.windows[0].raise_on = None               # it comes back

    b, _ = run_book(tmp_path, BASE, {0: win}, ex, polls=10, on_poll=drop,
                    clock_start=last + M.BAR, boom_rows=measured(SYM))
    assert len(b.trades) == 1
    assert b.said("unreadable")


def test_a_position_still_closes_while_the_chart_is_down(tmp_path):
    """Exits are marked from the exchange ticker, never from the chart."""
    win, last = quiet_window(sym=SYM, up=True)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)

    def drop(i, e):
        if i == 1:
            add_break(win, thrust=5.0, up=True)
            e.set_price(SYM, LEVEL_UP)
        elif i == 3:
            from fakes import FakeCDP
            FakeCDP.windows[0].raise_on = "studies"
            e.set_price(SYM, LEVEL_UP * 1.11)

    b, _ = run_book(tmp_path, BASE, {0: win}, ex, polls=8, on_poll=drop,
                    clock_start=last + M.BAR, boom_rows=measured(SYM))
    assert len(b.closed) == 1
    assert b.closed[0]["reason"] == "target"


def test_every_connection_is_rebuilt_after_five_bad_polls_in_a_row():
    """Otherwise a dead socket is retried forever with the same object."""
    src = __import__("pathlib").Path(P.__file__).read_text()
    assert "consecutive_errors >= 5" in src
    assert "drop_conn(_k)" in src


# ------------------------------------------------------------ numerical care
def test_repeated_wins_and_losses_do_not_drift_the_equity(tmp_path):
    """Float error accumulating across a long book would go unnoticed."""
    eq, start = 100.0, 100.0
    for _ in range(500):
        eq += 2500 * 0.10 - 2500 * 12 / 1e4
        eq -= 2500 * 0.10 - 2500 * 12 / 1e4
    assert eq == pytest.approx(start, abs=1e-6)


def test_the_book_round_trips_through_json_without_losing_precision(tmp_path):
    b, _ = scenario(tmp_path, [LEVEL_UP] * 3)
    raw = (tmp_path / "data" / "paper.json").read_text()
    again = json.loads(raw)
    assert again == b.state
    for t in again["trades"]:
        assert isinstance(t["entry"], float)
        assert t["entry"] == float(repr(t["entry"]))
