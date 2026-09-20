"""
--live: the path where mistakes cost real money.

Two things must hold before anything else. A row in the book must mean a
position on the exchange, and a position on the exchange must mean a row in
the book. Every test here is one way that correspondence can break.
"""
from __future__ import annotations


import pytest
import requests

import market as M
from fakes import FakeBitunix
from harness import add_break, quiet_window, run_book
from test_lifecycle import LEVEL_UP, SYM, measured


def combo_window(sym=SYM, up=True, res="15"):
    """A chart publishing the old convergence stream -- the only live path."""
    from harness import unanimous
    bars = M.quiet_band(n=45)
    last = max(bars)
    side = "BUY" if up else "SELL"
    fired = {"on": False}
    plots = {
        f"TBT_{side}_SPAN": (lambda t: 1 if (fired["on"] and t == last) else None),
        f"TBT_{side}_TIER": 2,
        f"TBT_{side}_LAST": 1,          # blue/Bank -- not orange
        f"TBT_{side}_SCORE": 3,
        f"SNIP_{side}_VOTE": 4,
        "TSL_WIRED": 1,
    }
    win = {"symbol": f"BITUNIX:{sym}.P", "res": res, "ohlc": bars,
           "votes": unanimous(up), "plots": plots}
    return win, last, fired


BREAK_LIVE = ["--source", "break", "--reset", "--live", "--equity", "100",
              "--tp", "10", "--lev", "50", "--frac", "0.5", "--sl", "2.1",
              "--min-confidence", "0", "--fill-bars", "8",
              "--interval", "60", "--sprint", "60"]

LIVE = ["--source", "combo", "--reset", "--live", "--equity", "100",
        "--tp", "10", "--lev", "50", "--frac", "0.5", "--sl", "2.1",
        "--entry", "now", "--interval", "60", "--sprint", "60",
        "--colour", "blue", "purple", "--min-agents", "3"]


def run_live(tmp_path, exchange, argv=None, polls=6, on_poll=None, win=None,
             last=None):
    if win is None:
        win, last, fired = combo_window()

        def default(i, e):
            if i == 1:
                fired["on"] = True
        on_poll = on_poll or default
    return run_book(tmp_path, argv or LIVE, {0: win}, exchange, polls=polls,
                    on_poll=on_poll, clock_start=last + M.BAR)


# ------------------------------------------------------- what --live refuses
def test_a_shape_rests_a_real_limit_order_at_its_own_level(tmp_path):
    """The live entry for the strategy this system actually trades.

    The shape's whole premise is that the entry IS the level: the stop can be
    tight because the band is narrow and a real break does not come back
    through it. So the order is a limit AT the level -- which can never fill
    worse than it -- and not a market order fired when the ticker touches,
    which would give away exactly the difference the shape exists to capture.

    Before this existed, `--live --source break` printed LIVE BOOK, read the
    real account equity, sent [REAL] notifications, and opened positions that
    lived only in the local JSON file.
    """
    win, last = quiet_window(sym=SYM, up=True)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)

    def on_poll(i, e):
        if i == 1:
            add_break(win, thrust=5.0, up=True)
            e.set_price(SYM, LEVEL_UP * 1.03)      # broke up, above the level

    b, _ = run_book(tmp_path, BREAK_LIVE, {0: win}, ex, polls=6,
                    on_poll=on_poll, clock_start=last + M.BAR,
                    boom_rows=measured(SYM))
    assert len(ex.orders) == 1, ex.orders
    o = ex.orders[0]
    assert o["type"] == "LIMIT"
    assert o["side"] == "BUY"
    assert float(o["price"]) == pytest.approx(LEVEL_UP)
    assert o["postOnly"] is True, "a long resting below the market is a maker"
    assert o["tp"] and o["sl"], "a 50x order must never rest without its stop"
    assert float(o["sl"]) < float(o["price"]) < float(o["tp"])
    assert o["clientId"]


def test_the_shape_position_is_booked_from_the_exchange_fill(tmp_path):
    """Not the price we asked for -- the price the venue reports."""
    win, last = quiet_window(sym=SYM, up=True)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)

    def on_poll(i, e):
        if i == 1:
            add_break(win, thrust=5.0, up=True)
            e.set_price(SYM, LEVEL_UP * 1.03)
        elif i == 2:
            e.set_price(SYM, LEVEL_UP * 0.999)     # trades back through it
    b, _ = run_book(tmp_path, BREAK_LIVE, {0: win}, ex, polls=8,
                    on_poll=on_poll, clock_start=last + M.BAR,
                    boom_rows=measured(SYM))
    assert len(b.trades) == 1, b.log
    tr = b.trades[0]
    assert tr["live"] is True and tr["order_id"]
    assert tr["entry"] == pytest.approx(LEVEL_UP), "filled worse than the level"
    assert tr["entry_kind"] == "limit"
    assert tr["notional"] == pytest.approx(tr["qty"] * tr["entry"])


def test_a_partial_fill_is_booked_at_the_size_that_exists(tmp_path):
    """A thin book gives 40% of the order. That is the position we have.

    Recording the intended size would put the margin, the stop distance and
    every later PnL on a quantity that does not exist.
    """
    win, last = quiet_window(sym=SYM, up=True)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    ex.partial_fill_frac = 0.4

    def on_poll(i, e):
        if i == 1:
            add_break(win, thrust=5.0, up=True)
            e.set_price(SYM, LEVEL_UP * 1.03)
        elif i == 2:
            e.set_price(SYM, LEVEL_UP * 0.999)

    b, _ = run_book(tmp_path, BREAK_LIVE, {0: win}, ex, polls=14,
                    on_poll=on_poll, clock_start=last + M.BAR,
                    boom_rows=measured(SYM))
    assert len(b.trades) == 1, b.log
    tr = b.trades[0]
    ordered = float(ex.orders[0]["qty"])
    assert tr["qty"] == pytest.approx(ordered * 0.4, rel=0.02)
    assert tr["notional"] == pytest.approx(tr["qty"] * tr["entry"])
    assert tr["margin"] == pytest.approx(tr["notional"] / 50.0)
    assert b.said("PARTIAL")


def test_an_unfilled_order_is_cancelled_when_its_window_passes(tmp_path):
    """Unfilled is a miss, not a loss -- but it must not be left resting."""
    win, last = quiet_window(sym=SYM, up=True)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)

    def on_poll(i, e):
        if i == 1:
            add_break(win, thrust=5.0, up=True)
        e.set_price(SYM, LEVEL_UP * 1.05)          # never comes back

    b, _ = run_book(tmp_path, BREAK_LIVE + ["--fill-bars", "1"], {0: win}, ex,
                    polls=22, on_poll=on_poll, clock_start=last + M.BAR,
                    boom_rows=measured(SYM))
    assert len(ex.orders) == 1
    assert ex.cancelled, "the order was left resting on the exchange"
    assert b.trades == []


def test_a_fill_that_lands_during_the_cancel_is_still_booked(tmp_path):
    """Cancelling races the fill, and the fill is a real position."""
    win, last = quiet_window(sym=SYM, up=True)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    real_cancel = ex.cancel_orders

    def racing_cancel(symbol, order_ids=None, client_ids=None):
        # price touches the level in the instant between decide and cancel
        ex.set_price(SYM, LEVEL_UP * 0.999)
        return real_cancel(symbol, order_ids, client_ids)

    def on_poll(i, e):
        if i == 1:
            add_break(win, thrust=5.0, up=True)
            e.cancel_orders = racing_cancel
        e.prices[SYM] = LEVEL_UP * 1.05            # no book walk

    b, _ = run_book(tmp_path, BREAK_LIVE + ["--fill-bars", "1"], {0: win}, ex,
                    polls=22, on_poll=on_poll, clock_start=last + M.BAR,
                    boom_rows=measured(SYM))
    assert len(b.trades) == 1, "a fill that raced the cancel was dropped"
    assert b.trades[0]["live"] is True


def test_price_already_at_the_level_still_rests_a_limit(tmp_path):
    """Post-only is refused when it would cross; a plain limit still holds."""
    win, last = quiet_window(sym=SYM, up=True)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)

    def on_poll(i, e):
        if i == 1:
            add_break(win, thrust=5.0, up=True)
            e.prices[SYM] = LEVEL_UP * 0.99        # already through the level

    b, _ = run_book(tmp_path, BREAK_LIVE, {0: win}, ex, polls=8,
                    on_poll=on_poll, clock_start=last + M.BAR,
                    boom_rows=measured(SYM))
    # The refused maker order is not an order; only the plain limit is.
    assert len(ex.orders) == 1, ex.orders
    assert ex.orders[0]["postOnly"] is False
    assert float(ex.orders[0]["price"]) == pytest.approx(LEVEL_UP), (
        "the retry must rest at the level, not at the market")
    assert b.said("already at the level")


def test_a_market_order_is_never_used_for_a_shape(tmp_path):
    """Crossing the spread at the level is a different, worse strategy."""
    win, last = quiet_window(sym=SYM, up=True)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    sent = []
    ex.place_market_order = lambda *a, **k: sent.append(1) or {"orderId": "x"}

    def on_poll(i, e):
        if i == 1:
            add_break(win, thrust=5.0, up=True)
            e.set_price(SYM, LEVEL_UP * 1.03)
        elif i == 2:
            e.set_price(SYM, LEVEL_UP * 0.999)

    run_book(tmp_path, BREAK_LIVE, {0: win}, ex, polls=8, on_poll=on_poll,
             clock_start=last + M.BAR,
                    boom_rows=measured(SYM))
    assert sent == [], "a shape was entered with a market order"


def test_the_book_never_invents_a_fill_the_exchange_did_not_give(tmp_path):
    """The local price-touch logic must not run alongside a live order."""
    win, last = quiet_window(sym=SYM, up=True)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)

    def on_poll(i, e):
        if i == 1:
            add_break(win, thrust=5.0, up=True)
            e.set_price(SYM, LEVEL_UP * 1.03)
        elif i >= 2:
            # price is at the level, but the venue never fills the order
            e.prices[SYM] = LEVEL_UP * 0.99
            e.marks[SYM] = LEVEL_UP * 0.99

    b, _ = run_book(tmp_path, BREAK_LIVE, {0: win}, ex, polls=8,
                    on_poll=on_poll, clock_start=last + M.BAR,
                    boom_rows=measured(SYM))
    assert b.trades == [], "the book opened a position the exchange never filled"


def test_an_order_left_resting_by_an_earlier_run_is_cancelled(tmp_path):
    """A restart used to forget the order while the exchange kept it."""
    win, last = quiet_window(sym=SYM, up=True)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    ex.resting["tbt999BLTESTUSDT"] = {
        "orderId": "777", "clientId": "tbt999BLTESTUSDT", "symbol": SYM,
        "side": "BUY", "price": str(LEVEL_UP), "qty": "10", "status": "NEW",
        "tradeQty": "0", "avgPrice": None, "tp": None, "sl": None}
    b, _ = run_book(tmp_path, BREAK_LIVE, {0: win}, ex, polls=4,
                    clock_start=last + M.BAR,
                    boom_rows=measured(SYM))
    assert any("tbt999BLTESTUSDT" in c[2] for c in ex.cancelled), ex.cancelled


def test_a_lost_reply_on_a_resting_order_is_checked_not_guessed(tmp_path):
    """The order may be on the book right now; assuming either way is wrong."""
    win, last = quiet_window(sym=SYM, up=True)
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    ex.silent_success = True

    def on_poll(i, e):
        if i == 1:
            add_break(win, thrust=5.0, up=True)
            e.set_price(SYM, LEVEL_UP * 1.03)
        elif i == 2:
            e.silent_success = False
            e.set_price(SYM, LEVEL_UP * 0.999)

    b, _ = run_book(tmp_path, BREAK_LIVE, {0: win}, ex, polls=8,
                    on_poll=on_poll, clock_start=last + M.BAR,
                    boom_rows=measured(SYM))
    assert len(ex.orders) == 1, "the order was sent twice"
    assert len(b.trades) == 1, "an order that really existed was abandoned"


@pytest.mark.parametrize("bad", [
    ["--entry", "edge"], ["--entry", "smart"], ["--break-even", "1.0"],
])
def test_live_refuses_every_mode_that_records_before_it_sends(tmp_path, bad):
    win, last, _ = combo_window()
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    b, _ = run_book(tmp_path, LIVE + bad, {0: win}, ex, polls=4,
                    clock_start=last + M.BAR)
    assert ex.orders == []
    assert b.trades == []


def test_live_refuses_to_start_on_an_empty_account(tmp_path):
    win, last, _ = combo_window()
    ex = FakeBitunix(prices={SYM: 100.0}, equity=0.0)
    b, _ = run_book(tmp_path, LIVE, {0: win}, ex, polls=4,
                    clock_start=last + M.BAR)
    assert ex.orders == []


def test_live_refuses_to_start_when_the_account_cannot_be_read(tmp_path):
    win, last, _ = combo_window()
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    ex.break_once("equity", requests.ConnectionError("no route to host"))
    b, _ = run_book(tmp_path, LIVE, {0: win}, ex, polls=4,
                    clock_start=last + M.BAR)
    assert ex.orders == []


# --------------------------------------------------------------- pre-flight
def test_preflight_sets_isolated_margin_and_leverage_before_any_signal(tmp_path):
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    run_live(tmp_path, ex, polls=3)
    assert ex.margin_mode_set.get(SYM) == "ISOLATION"
    assert ex.leverage_set.get(SYM) == 50


def test_preflight_respects_a_leverage_the_exchange_caps(tmp_path):
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0, max_lev=20)
    run_live(tmp_path, ex, polls=3)
    assert ex.leverage_set.get(SYM) == 20


def test_preflight_refuses_when_the_smallest_order_is_under_the_minimum(tmp_path):
    """An account too small to place the order this config makes."""
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0, min_qty=10_000.0)
    b, _ = run_live(tmp_path, ex, polls=4)
    assert ex.orders == []


# ------------------------------------------------------------- placing an order
def test_a_live_order_is_sent_with_its_stop_and_target_attached(tmp_path):
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    b, _ = run_live(tmp_path, ex, polls=6)
    assert len(ex.orders) == 1, ex.orders
    o = ex.orders[0]
    assert o["side"] == "BUY"
    assert o["tp"] and o["sl"], "a 50x position must never be sent naked"
    assert float(o["tp"]) > float(o["sl"])
    assert o["clientId"]
    assert len(b.trades) == 1 and b.trades[0]["live"] is True
    assert b.trades[0]["order_id"] == o["orderId"]


def test_a_rejected_order_records_nothing(tmp_path):
    """A row claiming a position the exchange refused is the worst of both."""
    from fakes import Rejected
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    ex.break_once("place_market_order", Rejected("insufficient margin"))
    b, _ = run_live(tmp_path, ex, polls=6)
    assert b.trades == [], "the book claims a position that was refused"


def test_a_lost_reply_is_checked_rather_than_guessed(tmp_path):
    """The order landed; the answer did not.

    Assuming failure abandons a real 50x position that nothing will manage.
    Assuming success invents one. The engine has to go and look, and the
    client must not have quietly sent the order three times while trying.
    """
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    ex.silent_success = True              # records the order, then times out
    ex.order_detail = lambda order_id=None, client_id=None: {
        "orderId": "9001", "clientId": client_id}
    b, _ = run_live(tmp_path, ex, polls=6)
    assert len(ex.orders) == 1, "a lost reply resent the order"
    assert len(b.trades) == 1
    assert b.trades[0]["live"] is True
    assert b.trades[0]["order_id"] == "9001"


def test_a_lost_reply_with_no_order_behind_it_records_nothing(tmp_path):
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    ex.silent_success = True
    ex.order_detail = lambda order_id=None, client_id=None: {}
    b, _ = run_live(tmp_path, ex, polls=6)
    assert len(ex.orders) == 1
    assert b.trades == []
    assert b.said("ORDER UNKNOWN")


def test_the_client_id_is_unique_per_stream_and_symbol():
    """Two orders the exchange reads as one is a cancel hitting the wrong trade."""
    ids = set()
    for sym in ("ARBUSDT", "ARBUSDC"):
        for counter in (False, True):
            for side in ("BUY", "SELL"):
                ids.add((f"tbt{1788440500}{side[0]}"
                         f"{'c' if counter else 'n'}{sym}")[:32])
    assert len(ids) == 8


def test_the_live_position_cap_is_honoured(tmp_path):
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    b, _ = run_live(tmp_path, ex, argv=LIVE + ["--max-live", "0"], polls=6)
    assert len(ex.orders) == 1


# ------------------------------------------------------------ reconciliation
def test_a_position_the_exchange_no_longer_holds_is_closed_from_its_history(
        tmp_path):
    """The exchange's own realised PnL, fees and all -- not a guess from price."""
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)

    def script(i, e):
        if i == 2:
            # the exchange now reports it -- this is where the position id
            # is learned, and it is the only field the closed-position
            # history shares with anything the book holds
            e.open_positions = [{"symbol": SYM, "side": "BUY", "qty": "25",
                                 "positionId": "p1"}]
        if i == 4:
            e.open_positions = []
            e.closed_positions = [{"symbol": SYM, "positionId": "p1",
                                   "realizedPNL": "37.50",
                                   "closePrice": "110.0",
                                   "ctime": str(int(M.T0 * 1000))}]
            e._equity = 137.50

    win, last, fired = combo_window()

    def on_poll(i, e):
        if i == 1:
            fired["on"] = True
        script(i, e)

    b, _ = run_book(tmp_path, LIVE + ["--recon-secs", "0"], {0: win}, ex,
                    polls=8, on_poll=on_poll, clock_start=last + M.BAR)
    closed = b.closed
    assert closed, b.log
    assert closed[0]["pnl"] == pytest.approx(37.50)
    assert closed[0]["exit"] == pytest.approx(110.0)
    assert closed[0]["reason"] == "target"


def test_a_reconciliation_failure_does_not_close_a_live_position(tmp_path):
    """Not being able to ask is not the same as being told it is gone."""
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)

    def script(i, e):
        if i >= 2:
            e.fail_next.setdefault("positions", []).append(
                requests.ReadTimeout("timed out"))

    win, last, fired = combo_window()

    def on_poll(i, e):
        if i == 1:
            fired["on"] = True
        script(i, e)

    b, _ = run_book(tmp_path, LIVE + ["--recon-secs", "0"], {0: win}, ex,
                    polls=8, on_poll=on_poll, clock_start=last + M.BAR)
    assert len(b.open) == 1, "a live position was closed because a call failed"


def test_a_live_row_is_never_marked_to_market_by_the_book(tmp_path):
    """A live position is the exchange's; the book must not invent an exit."""
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    win, last, fired = combo_window()

    def on_poll(i, e):
        if i == 1:
            fired["on"] = True
        elif i >= 2:
            e.set_price(SYM, 40.0)         # far past any stop
            e.open_positions = [{"symbol": SYM, "side": "BUY", "qty": "25",
                                 "positionId": "p1"}]

    b, _ = run_book(tmp_path, LIVE + ["--recon-secs", "0"], {0: win}, ex,
                    polls=8, on_poll=on_poll, clock_start=last + M.BAR)
    assert len(b.open) == 1
    assert b.open[0]["pnl"] == 0.0, "the book priced a position it does not own"


def test_a_position_the_exchange_has_not_listed_yet_is_not_settled(tmp_path):
    """The race that closed a live trade the instant it opened.

    The reconciler runs every three seconds. An order placed a moment ago is
    not always in get_pending_positions on the very next pass -- and a row the
    exchange has not listed was read as a row the exchange had closed. The
    book settled it at zero and stopped watching, while the account went on
    holding a real 50x position with an attached stop that nothing in this
    process was tracking any more.
    """
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    win, last, fired = combo_window()

    def on_poll(i, e):
        if i == 1:
            fired["on"] = True
        e.open_positions = []          # the exchange never lists it in time

    b, _ = run_book(tmp_path,
                    [x if x != "60" else "2" for x in LIVE] +
                    ["--recon-secs", "0"],
                    {0: win}, ex, polls=6, on_poll=on_poll,
                    clock_start=last + M.BAR)
    assert len(ex.orders) == 1
    assert len(b.open) == 1, "a live position was settled before it was listed"
    assert b.open[0]["pnl"] == 0.0
