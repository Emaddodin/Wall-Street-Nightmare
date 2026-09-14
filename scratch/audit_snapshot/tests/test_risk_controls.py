"""
The controls that stop a bad day, and the readings the book is priced on.

These cover what the audit found missing rather than wrong: a circuit breaker
that was described in a config file nothing reads, a spread measured once and
believed for days, and a frozen feed the book skipped without ever raising its
voice.
"""
from __future__ import annotations

import pytest

import market as M
import papertrade as P
from fakes import FakeBitunix
from harness import add_break, quiet_window, run_book
from test_lifecycle import (BASE, LEVEL_UP, SYM, flags, measured,
                            scenario)


# ------------------------------------------------------ the circuit breaker
def _lose_then_try_again(tmp_path, argv, polls=26):
    """Open a trade, lose it, then offer another shape on a second coin."""
    a_win, last = quiet_window(sym="AAAUSDT", up=True)
    b_win, _ = quiet_window(sym="BBBUSDT", up=True)
    ex = FakeBitunix(prices={"AAAUSDT": 100.0, "BBBUSDT": 100.0}, equity=100.0)

    def on_poll(i, e):
        if i == 1:
            add_break(a_win, thrust=5.0, up=True)
            e.set_price("AAAUSDT", LEVEL_UP)
        elif i == 3:
            e.set_price("AAAUSDT", LEVEL_UP * 0.98)      # stopped out
        elif i == 5:
            add_break(b_win, thrust=5.0, up=True)
            e.set_price("BBBUSDT", LEVEL_UP)

    return run_book(tmp_path, argv, {0: a_win, 1: b_win}, ex, polls=polls,
                    on_poll=on_poll, clock_start=last + M.BAR,
                    boom_rows=measured("AAAUSDT", "BBBUSDT"))


def test_without_a_limit_the_book_keeps_trading_after_a_loss(tmp_path):
    """The state this engine has always been in: nothing counts the losses."""
    b, _ = _lose_then_try_again(tmp_path, flags())
    assert len(b.trades) == 2, [t["sym"] for t in b.trades]
    assert not b.said("HALT")


def test_a_daily_loss_limit_stops_the_next_position(tmp_path):
    """One stop at 50x on half the wallet is ~38% of the account.

    config.yaml has described a 20% daily loss limit since the beginning and
    nothing has ever read that file, so no circuit breaker has been in force
    at any point.
    """
    b, _ = _lose_then_try_again(tmp_path, flags(daily_loss_limit=20))
    assert len(b.trades) == 1, [t["sym"] for t in b.trades]
    assert b.trades[0]["sym"] == "AAAUSDT"
    assert b.said("HALT"), b.log


def test_the_limit_counts_realised_losses_only(tmp_path):
    """An open position's stop is already on the exchange; it is not a loss yet."""
    b, _ = scenario(tmp_path, [LEVEL_UP, LEVEL_UP * 0.995, LEVEL_UP * 0.995],
                    argv=flags(daily_loss_limit=1))
    assert len(b.open) == 1, "an open position tripped a realised-loss limit"
    assert not b.said("HALT")


def test_a_winning_day_never_trips_the_limit(tmp_path):
    b, _ = _lose_then_try_again(
        tmp_path, flags(daily_loss_limit=20, tp=0.1), polls=26)
    won = [t for t in b.trades if t.get("pnl", 0) > 0]
    if won:
        assert not b.said("HALT")


def test_the_limit_is_off_by_default():
    """Switching it on changes what the book does; that is the operator's call."""
    import sys
    from unittest import mock
    with mock.patch.object(sys, "argv", ["x"]):
        pass
    src = __import__("pathlib").Path(P.__file__).read_text()
    i = src.index('"--daily-loss-limit"')
    assert "default=0.0" in src[i:i + 200]


def test_live_says_so_when_no_limit_is_set(tmp_path):
    """Silence about a missing circuit breaker is how it stays missing."""
    from test_live import LIVE, combo_window
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    win, last, fired = combo_window()
    import contextlib
    import io
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        run_book(tmp_path, LIVE, {0: win}, ex, polls=3,
                 clock_start=last + M.BAR)
    assert "no daily loss limit" in out.getvalue(), out.getvalue()


# ------------------------------------------------------------- the spread
def test_the_spread_is_re_read_rather_than_believed_forever(tmp_path):
    """It was measured once per symbol and used for the life of the process.

    A session that runs for days priced every fill off a reading taken at
    start-up, through whatever the market did afterwards.
    """
    assert P.SPREAD_TTL_S > 0
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0, spread_bps=2.0)
    reads = {"n": 0}
    real = ex.spread_bps

    def counted(sym):
        reads["n"] += 1
        return real(sym)

    ex.spread_bps = counted

    def widen(i, e):
        if i == 4:
            e._spread = 60.0                # the book thins out

    b, _ = scenario(tmp_path, [LEVEL_UP] * 10, exchange=ex,
                    extra_on_poll=widen, argv=flags(entry="edge"), polls=14)
    assert reads["n"] >= 1


def test_a_bad_spread_reading_is_not_cached_as_though_it_were_good(tmp_path):
    """One unreadable book must not poison the symbol for the whole run."""
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    ex.depth = lambda sym, limit=5: {"bids": [], "asks": []}
    b, _ = scenario(tmp_path, [LEVEL_UP, LEVEL_UP * 1.11, LEVEL_UP * 1.11],
                    exchange=ex, argv=flags(entry="edge"))
    assert b.equity == b.equity
    for t in b.trades:
        assert t["entry"] == t["entry"] and abs(t["entry"]) != float("inf")


# -------------------------------------------------------- the frozen chart
def test_the_book_says_so_when_its_chart_stops(tmp_path):
    """Fourteen hours of a stopped feed left one log line per poll and no alarm."""
    win, last = quiet_window(sym=SYM)
    add_break(win, thrust=5.0, up=True)
    ex = FakeBitunix(prices={SYM: LEVEL_UP}, equity=100.0)
    b, _ = run_book(tmp_path, BASE, {0: win}, ex, polls=6,
                    clock_start=last + M.BAR * 40, boom_rows=measured(SYM))
    assert b.said("FROZEN"), b.log
    assert b.trades == []


def test_the_alarm_is_raised_once_not_every_poll(tmp_path):
    win, last = quiet_window(sym=SYM)
    ex = FakeBitunix(prices={SYM: LEVEL_UP}, equity=100.0)
    b, _ = run_book(tmp_path, BASE, {0: win}, ex, polls=10,
                    clock_start=last + M.BAR * 40, boom_rows=measured(SYM))
    assert len([x for x in b.log if "FROZEN" in x]) == 1


def test_a_live_chart_raises_no_alarm(tmp_path):
    b, _ = scenario(tmp_path, [LEVEL_UP] * 3)
    assert not b.said("FROZEN")


def test_the_book_notices_when_the_feed_comes_back(tmp_path):
    win, last = quiet_window(sym=SYM)
    ex = FakeBitunix(prices={SYM: LEVEL_UP}, equity=100.0)

    now = last + M.BAR * 40

    def thaw(i, e):
        if i == 3:
            # the feed reconnects and back-fills up to the present
            px = win["ohlc"][max(win["ohlc"])][3]
            for k in range(1, 42):
                t = last + k * M.BAR
                win["ohlc"][t] = (px, px * 1.0005, px * 0.9995, px)

    b, _ = run_book(tmp_path, BASE, {0: win}, ex, polls=8, on_poll=thaw,
                    clock_start=now, boom_rows=measured(SYM))
    assert b.said("FROZEN")
    assert b.said("live again"), b.log


# ------------------------------------------------- the fill, on the market path
def test_a_market_order_is_booked_at_the_price_the_exchange_gave(tmp_path):
    """`fill` is the ticker crossed by half the spread -- an estimate.

    Every later number was computed from the guess: the PnL, the exposure, and
    the margin the exchange is really holding.
    """
    from test_live import LIVE, combo_window
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    win, last, fired = combo_window()
    placed = {}
    real = ex.place_market_order

    def slipped(symbol, side, qty, **kw):
        r = real(symbol, side, qty, **kw)
        # the venue fills 80% of it, 0.3% worse than the quote
        placed["cid"] = kw.get("client_id")
        ex.resting[kw.get("client_id")] = {
            "orderId": r["orderId"], "clientId": kw.get("client_id"),
            "symbol": symbol, "side": side, "price": "100",
            "qty": qty, "status": "PART_FILLED",
            "tradeQty": str(float(qty) * 0.8), "avgPrice": "100.3",
            "tp": None, "sl": None}
        return r

    ex.place_market_order = slipped

    def on_poll(i, e):
        if i == 1:
            fired["on"] = True

    b, _ = run_book(tmp_path, LIVE, {0: win}, ex, polls=6, on_poll=on_poll,
                    clock_start=last + M.BAR)
    assert len(b.trades) == 1, b.log
    tr = b.trades[0]
    ordered = float(ex.orders[0]["qty"])
    assert tr["entry"] == pytest.approx(100.3), "the estimate was kept"
    assert tr["qty"] == pytest.approx(ordered * 0.8)
    assert tr["notional"] == pytest.approx(tr["qty"] * tr["entry"])
    assert b.said("PARTIAL")


def test_an_unreadable_fill_keeps_the_estimate_and_says_so(tmp_path):
    from test_live import LIVE, combo_window
    ex = FakeBitunix(prices={SYM: 100.0}, equity=100.0)
    win, last, fired = combo_window()
    ex.order_detail = lambda order_id=None, client_id=None: {}

    def on_poll(i, e):
        if i == 1:
            fired["on"] = True

    b, _ = run_book(tmp_path, LIVE, {0: win}, ex, polls=6, on_poll=on_poll,
                    clock_start=last + M.BAR)
    assert len(b.trades) == 1
    assert b.trades[0]["entry"] > 0
    assert b.said("could not be read")


# ------------------------------------------------------------- portability
def test_no_engine_module_carries_a_hard_coded_server_path():
    """A literal path is a module that can only run in one place, untested."""
    import pathlib
    root = pathlib.Path(P.__file__).resolve().parent
    offenders = []
    for f in list(root.glob("*.py")) + list((root / "tools").glob("*.py")):
        if f.parent.name in ("old", "scratch"):
            continue
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if "/home/tbt/bot" in line and not line.strip().startswith("#"):
                offenders.append(f"{f.name}:{i}")
    assert not offenders, offenders


# ------------------------------------------------------- every trade is said
def test_every_way_a_position_can_open_says_so(tmp_path):
    """Three of the six open sites were silent, including the live one.

    Closes were announced and opens were not, so the phone reported the end of
    trades whose beginning it had never mentioned.
    """
    src = __import__("pathlib").Path(P.__file__).read_text().split("\n")
    silent = []
    for i, line in enumerate(src):
        if line.strip() != "trades.append(tr)":
            continue
        after = "\n".join(src[i:i + 18])
        if "ping(" not in after and "announce(tr)" not in after:
            silent.append(i + 1)
    assert not silent, f"positions open silently at lines {silent}"


def test_every_way_a_position_can_close_says_so():
    import re
    src = __import__("pathlib").Path(P.__file__).read_text().split("\n")
    silent = []
    for i, line in enumerate(src):
        if not re.search(r'(tr|_t|v)\.reason = ', line):
            continue
        if "ping(" not in "\n".join(src[i:i + 26]):
            silent.append(i + 1)
    assert not silent, f"positions close silently at lines {silent}"


def test_the_notification_is_two_lines(tmp_path):
    """Result and balance. A notification you have to read is one you stop
    reading."""
    b, _ = scenario(tmp_path, [LEVEL_UP, LEVEL_UP * 1.11, LEVEL_UP * 1.11])
    seen = b.pings
    assert seen, "nothing was announced at all"
    for title, body in seen:
        assert len(body.split("\n")) <= 2, f"{title!r} sent {body!r}"
        assert "$" in body, f"{title!r} did not carry the balance"
    titles = [t for t, _ in seen]
    assert any("LONG" in t for t in titles), titles
    assert any("TARGET" in t for t in titles), titles


def test_every_module_defines_its_root_before_reaching_for_it():
    """A rewrite that moves a path into a variable has to put the variable first.

    Nineteen tools were left with `_BOT` defined below the line that used it,
    so each raised NameError on its third statement -- and none of them are
    imported by anything, so nothing noticed until one was run by hand.
    """
    import pathlib
    root = pathlib.Path(P.__file__).resolve().parent
    broken = []
    for f in list(root.glob("*.py")) + list((root / "tools").glob("*.py")):
        if f.parent.name in ("old", "scratch"):
            continue
        lines = f.read_text().split("\n")
        at = next((i for i, l in enumerate(lines)
                   if l.startswith("_BOT = ")), None)
        if at is None:
            continue
        used = next((i for i, l in enumerate(lines)
                     if "_BOT" in l and not l.startswith("_BOT = ")), None)
        if used is not None and used < at:
            broken.append(f"{f.name}:{used + 1} uses _BOT defined at {at + 1}")
    assert not broken, broken


def test_every_module_still_parses():
    """The cheapest possible guard, and it would have caught all nineteen."""
    import ast
    import pathlib
    root = pathlib.Path(P.__file__).resolve().parent
    broken = []
    for f in list(root.glob("*.py")) + list((root / "tools").glob("*.py")):
        if f.parent.name in ("old", "scratch"):
            continue
        try:
            ast.parse(f.read_text())
        except SyntaxError as e:
            broken.append(f"{f.name}:{e.lineno} {e.msg}")
    assert not broken, broken


# ------------------------------------- the panel restarting the book hourly
def test_autopilot_ignores_the_window_the_scout_owns():
    """The reason no resting order has ever survived to fill.

    Autopilot compared the live symbols of BOTH chart windows against the two
    it wanted. Window 1 belongs to the scout and shows whatever it is visiting
    that second, so the comparison failed every hour, every time -- and a
    switch restarts the book. With --fill-bars 8 on a 15m chart an order rests
    for two hours, so an hourly restart destroyed every pending order before
    its window could elapse. In the whole life of this book, not one filled.
    """
    import pathlib
    src = pathlib.Path(P.__file__).resolve().parent / "panel.py"
    body = src.read_text()
    assert "SCOUT_WINDOW" in body
    auto = body[body.index("def autopilot"):body.index("def autopilot") + 3000]
    assert "held_coins()" in auto, (
        "autopilot still compares against the scout's window")
    assert "cur = coins()" not in auto


def test_the_scout_window_is_the_same_number_everywhere():
    """Three files have to agree on which window the scout owns."""
    import pathlib
    import re
    root = pathlib.Path(P.__file__).resolve().parent
    scout_unit = (root / "services" / "tbt-scout.service").read_text()
    m = re.search(r"--window (\d+)", scout_unit)
    assert m, "the scout unit does not say which window it walks"
    n = int(m.group(1))

    guard_unit = (root / "services" / "tbt-guard.service").read_text()
    g = re.search(r"--scout-window (\d+)", guard_unit)
    assert g and int(g.group(1)) == n, (
        f"the guard thinks the scout is on window {g.group(1) if g else None}, "
        f"the scout says {n}")

    panel = (root / "panel.py").read_text()
    p_ = re.search(r"^SCOUT_WINDOW = (\d+)", panel, re.M)
    assert p_ and int(p_.group(1)) == n, (
        f"the panel thinks the scout is on window "
        f"{p_.group(1) if p_ else None}, the scout says {n}")

    paper = (root / "services" / "tbt-paper.service").read_text()
    s = re.search(r"--skip-window (\d+)", paper)
    assert s and int(s.group(1)) == n, (
        f"the book skips window {s.group(1) if s else None}, "
        f"the scout walks {n}")


def test_the_book_is_not_what_the_kernel_kills_first():
    """The browser grows without bound; the book must outlive it.

    tbt-chrome's tree is the largest thing on the box and grows every hour,
    so the box will run out of memory eventually. What the kernel picks then
    is not a detail: a killed renderer costs a chart the guard can reload, a
    killed book costs the management of an open position until systemd brings
    it back. The ordering has to be written down, not left to whichever
    process happens to be biggest at the time.
    """
    import pathlib
    import re
    root = pathlib.Path(P.__file__).resolve().parent
    unit = (root / "services" / "tbt-paper.service").read_text()
    m = re.search(r"^OOMScoreAdjust=(-?\d+)", unit, re.M)
    assert m, "the book does not tell the kernel to spare it"
    assert int(m.group(1)) < 0, (
        f"the book asks the kernel for oom_score_adj={m.group(1)}, which "
        f"makes it a MORE likely victim, not a less likely one")


def test_repairing_the_browser_does_not_restart_the_book():
    """The guard restarts chrome on purpose. It must not take the book with it.

    guard.repair_memory() says "restart it, not the book" -- and for as long
    as the unit said Requires=tbt-chrome.service, systemd restarted the book
    anyway, because Requires propagates a restart. On 2026-09-05 that fired
    at 06:16 with a position open: the book went down, came back, and the
    browser returned a window short.
    """
    import pathlib
    import re
    root = pathlib.Path(P.__file__).resolve().parent
    unit = (root / "services" / "tbt-paper.service").read_text()
    assert not re.search(r"^Requires=tbt-chrome", unit, re.M), (
        "Requires= propagates chrome's restarts to the book; use Wants= with "
        "After= so the ordering is kept and the propagation is not")
    assert re.search(r"^Wants=tbt-chrome", unit, re.M), (
        "the book no longer asks for chrome at all")
    assert re.search(r"^After=tbt-chrome", unit, re.M), (
        "the book must still start after chrome")

    guard = (root / "guard.py").read_text()
    assert "restart the book" not in guard.split("def repair_memory")[1][:400] \
        or "not the book" in guard.split("def repair_memory")[1][:400]
