"""
Runs the real engine.

`run_book()` calls `papertrade.main()` -- the actual 2000-line loop, with its
actual argument parsing, its actual gating, its actual sizing and its actual
exit handling. Only the two edges are replaced: the exchange and the chart.
Nothing in between is stubbed, so a scenario that opens a position has really
gone market data -> shape -> confidence -> risk checks -> order -> fill ->
management -> exit -> PnL -> book on disk.

The loop is `while True`. It is stopped the only way it can be stopped from
inside without being mistaken for a fault: `KeyboardInterrupt`, which the loop
re-raises rather than swallowing. A poll budget fires it, so every scenario
terminates deterministically.
"""
from __future__ import annotations

import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import papertrade as P  # noqa: E402
from fakes import FakeBitunix, FakeCDP, FakeClock  # noqa: E402


class Stop(KeyboardInterrupt):
    """The scenario is over. KeyboardInterrupt so the engine's own loop lets go."""


class Book:
    """What a scenario gets back: the state the engine actually persisted."""

    def __init__(self, path: Path, exchange: FakeBitunix, log: list[str]):
        self.path = path
        self.exchange = exchange
        self.log = log
        self.pings: list = []
        self.raw = path.read_text() if path.exists() else ""
        try:
            self.state = json.loads(self.raw) if self.raw else {}
        except ValueError:
            # A book the engine refused to start on is left exactly as it was;
            # the scenario still needs to look at what happened.
            self.state = {}

    @property
    def trades(self):
        return self.state.get("trades", [])

    @property
    def equity(self):
        return self.state.get("equity")

    @property
    def open(self):
        return [t for t in self.trades if not t.get("closed")]

    @property
    def closed(self):
        return [t for t in self.trades if t.get("closed")]

    def lines(self, needle):
        return [x for x in self.log if needle in x]

    def said(self, needle) -> bool:
        return any(needle in x for x in self.log)


@contextmanager
def _captured_log(records: list[str]):
    import logging

    class Sink(logging.Handler):
        def emit(self, rec):
            try:
                records.append(rec.getMessage() % () if not rec.args
                               else rec.getMessage())
            except Exception:
                records.append(str(rec.msg))

    h = Sink()
    root = logging.getLogger()
    was = root.level
    root.addHandler(h)
    root.setLevel(logging.INFO)
    try:
        yield
    finally:
        root.removeHandler(h)
        root.setLevel(was)


def run_book(tmp_path, argv, windows, exchange, polls=6, on_poll=None,
             clock_start=None, scout_rows=None, boom_rows=None,
             watch_measures=None, book_state=None):
    """Drive the engine for `polls` passes and hand back the book.

    `on_poll(i, exchange)` runs before each pass, which is where a scenario
    moves price, breaks the exchange, or injects a duplicate event.
    """
    data = Path(tmp_path) / "data"
    data.mkdir(parents=True, exist_ok=True)
    book = data / "paper.json"
    if book_state is not None:
        book.write_text(json.dumps(book_state))
    (data / "scout.json").write_text(json.dumps(scout_rows or []))
    (data / "boom.json").write_text(json.dumps(boom_rows or []))
    (data / "watch_measures.json").write_text(json.dumps(watch_measures or {}))

    FakeCDP.install(windows)
    clock = FakeClock(clock_start if clock_start is not None else time.time())
    records: list[str] = []
    counter = {"n": 0}
    # Kept rather than discarded: what reached the phone is part of what the
    # engine did, and a trade nobody was told about is a real defect.
    pings: list = []

    def guarded_sleep(secs):
        counter["n"] += 1
        if counter["n"] >= polls:
            raise Stop()
        clock.sleep(secs)
        if on_poll:
            on_poll(counter["n"], exchange)

    # Reset the module-level memory the engine keeps between polls, so one
    # scenario can never leak a window's history into the next.
    P._CONNS.clear()
    P._STUDY_ID.clear()
    P._STUDY_SWAPPED.clear()
    P._WINDOW_SYM.clear()
    P._PLAN_DIR.clear()
    P._REACH.update({"t": 0.0, "by": {}, "stamp": 0})
    P._RIPE.update({"t": 0.0, "by": {}})

    patches = [
        mock.patch.object(P, "BOOK", book),
        mock.patch.object(P, "SCOUT", data / "scout.json"),
        mock.patch.object(P, "RANKED", data / "boom.json"),
        mock.patch.object(P, "WATCH_M", data / "watch_measures.json"),
        mock.patch.object(P, "BitunixClient", lambda *a, **k: exchange),
        mock.patch.object(P, "TradingViewCDP", FakeCDP),
        mock.patch.object(P, "ping",
                          lambda t, b, test=False: pings.append((t, b))),
        mock.patch.object(time, "time", clock.time),
        mock.patch.object(time, "sleep", guarded_sleep),
        mock.patch.object(sys, "argv", ["papertrade.py", *argv]),
    ]
    for p in patches:
        p.start()
    try:
        with _captured_log(records):
            try:
                P.main()
            except (Stop, KeyboardInterrupt):
                pass
    finally:
        for p in reversed(patches):
            p.stop()
        lk = book.with_suffix(".lock")
        if lk.exists():
            lk.unlink()

    out = Book(book, exchange, records)
    out.pings = pings
    return out, clock


def unanimous(up=True) -> int:
    from fakes import pack_votes
    w = 1 if up else -1
    return pack_votes(Bank=w, Team45=w, Tesla=w, Sniper=w, HTF=w, MA=w)


def quiet_window(sym="TESTUSDT", res="15", n=45, base=100.0, t0=None, up=True):
    """A chart showing nothing yet -- a band that has not broken.

    Every scenario starts here. A shape already present at startup is
    deliberately absorbed as backlog by the engine and never traded, so the
    only honest way to test a trade is to let the shape appear while the book
    is already watching -- which is also the only way it happens in life.
    """
    import market as M
    t0 = M.T0 if t0 is None else t0
    bars = M.quiet_band(n=n, base=base, t0=t0)
    last = max(bars)
    return ({"symbol": f"BITUNIX:{sym}.P", "res": res, "ohlc": bars,
             "votes": unanimous(up)}, last)


def add_break(window, thrust=3.5, up=True, base=None):
    """Print the breaking candle onto a window that was quiet, and close it.

    Returns (signal_bar_time, signal_bar_close). The forming bar that follows
    is added too, because `read_window` reads the last CLOSED bar and would
    otherwise treat the break as still in progress.
    """
    import market as M
    from fakes import FakeCDP
    bars = window["ohlc"]
    ks = sorted(bars)
    typ = sum((bars[k][1] - bars[k][2]) / bars[k][3] for k in ks) / len(ks)
    top = max(bars[k][1] for k in ks)
    bot = min(bars[k][2] for k in ks)
    t = ks[-1] + M.BAR
    rng = typ * thrust * (top if up else bot)
    if up:
        o = top * 1.0005
        c = o + rng * 0.9
        h, l = c + rng * 0.05, o - rng * 0.05
    else:
        o = bot * 0.9995
        c = o - rng * 0.9
        h, l = o + rng * 0.05, c - rng * 0.05
    bars[t] = (o, h, l, c)
    bars[t + M.BAR] = (c, c * 1.0002, c * 0.9998, c)   # the bar now forming
    w = FakeCDP.windows.get(0)
    if w is not None and w.ohlc is bars:
        pass          # the window holds the same dict; nothing to copy
    return t, c
