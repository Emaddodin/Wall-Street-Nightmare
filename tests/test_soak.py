"""
A soak: many randomised markets and a hostile exchange, run through the real
engine, checking the things that must be true after every single one.

The hand-written scenarios each test one idea. This tests the combinations
nobody thought of -- a sub-cent coin on a 75x account with a 120bps spread and
a ticker that times out one poll in eight, while three charts print shapes at
random. It does not assert what the engine should decide; it asserts that
whatever it decided, the numbers are still numbers and no loss exceeded the
margin that backed it.

Seeded, so a failure is reproducible: the seed is in the test id.
"""
from __future__ import annotations

import contextlib
import io
import math
import random

import pytest
import requests

import market as M
from fakes import FakeBitunix, FakeCDP
from harness import add_break, quiet_window, run_book
from test_lifecycle import flags

SYMS = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
PRICES = [0.00004321, 0.0731, 2.677, 100.0, 68_000.0]


def one_trial(tmp_path, rng):
    syms = SYMS[:rng.randint(1, 3)]
    wins, bases, last = {}, {}, None
    for i, s in enumerate(syms):
        bases[s] = rng.choice(PRICES)
        wins[i], last = quiet_window(sym=s, up=rng.choice([True, False]),
                                     base=bases[s])
    ex = FakeBitunix(
        prices=dict(bases), equity=rng.choice([1.0, 10.0, 100.0, 5000.0]),
        spread_bps=rng.uniform(0.1, 120.0),
        base_prec=rng.choice([0, 1, 2, 3, 6, 8]),
        quote_prec=rng.choice([1, 2, 4, 8, 10]),
        min_qty=rng.choice([0.0, 0.1, 1.0, 1e6]),
        max_lev=rng.choice([5, 20, 50, 75, 125]),
        position_mode=rng.choice(["ONE_WAY", "HEDGE"]))
    px = dict(bases)

    def on_poll(i, e):
        for w in wins.values():
            if i == 1 or rng.random() < 0.15:
                add_break(w, thrust=rng.uniform(0.3, 9.0),
                          up=rng.choice([True, False]))
        for s in px:
            shock = rng.choice([1.0, 1.0, 1.0, 1.0, rng.uniform(0.5, 0.99),
                                rng.uniform(1.01, 1.6)])
            px[s] = max(px[s] * (1 + rng.gauss(0, 0.04)) * shock,
                        bases[s] * 1e-9)
            e.set_price(s, px[s], mark=px[s] * (1 + rng.gauss(0, 0.002)))
        r = rng.random()
        e.ticker_error = requests.ReadTimeout("t") if r < 0.12 else None
        if r > 0.93:
            e.depth = lambda sym, limit=5: {"bids": [], "asks": []}
        elif r > 0.90:
            e.depth_error = requests.ConnectionError("x")
        else:
            e.depth_error = None
        if rng.random() < 0.08:
            e.break_once("history", requests.ReadTimeout("h"))
        if rng.random() < 0.06:
            e.break_once("max_leverage", RuntimeError("m"))
        for k in wins:
            FakeCDP.windows[k].raise_on = rng.choice(
                [None, None, None, None, "studies", "raw_series"])

    argv = flags(equity=ex._equity, min_confidence=rng.choice([0, 30, 70]),
                 interval=rng.choice([0.2, 2, 60, 300]),
                 sprint=rng.choice([0.2, 60]),
                 entry=rng.choice(["now", "edge", "smart", "back", "mid",
                                   "extreme"]),
                 max_hold_min=rng.choice([0, 3]),
                 stale_min=rng.choice([0, 3]),
                 max_exposure=rng.choice([0.5, 1.0, 2.0]),
                 max_per_day=rng.choice([0, 1, 3]),
                 lev=rng.choice([20, 50, 75]),
                 fill_bars=rng.choice([1, 8]))
    if rng.random() < 0.4:
        argv.append("--stack")

    with contextlib.redirect_stdout(io.StringIO()):
        book, _ = run_book(tmp_path, argv, wins, ex,
                           polls=rng.randint(4, 18), on_poll=on_poll,
                           clock_start=last + M.BAR)
    return book, argv


def check(book, argv):
    """What must hold no matter what the market or the exchange did."""
    why = []
    eq = book.equity
    if eq is None or eq != eq or math.isinf(eq):
        why.append(f"equity is {eq}")
    for t in book.trades:
        for k in ("entry", "tp", "sl", "qty", "notional", "margin", "pnl"):
            v = t[k]
            if v is None or v != v or math.isinf(v):
                why.append(f"{t['sym']} {k} is {v}")
        if t["entry"] <= 0 or t["qty"] <= 0:
            why.append(f"{t['sym']} opened at {t['entry']} for {t['qty']}")
        # Isolated margin: a loss cannot exceed what backed the position.
        if t["closed"] and -t["pnl"] > t["margin"] * 1.05 + 1:
            why.append(f"{t['sym']} lost {-t['pnl']:.2f} on {t['margin']:.2f} "
                       f"of margin via {t['reason']}")
        if t["side"] == "BUY" and not (t["sl"] < t["entry"] < t["tp"]):
            why.append(f"{t['sym']} long with tp/sl the wrong way round")
        if t["side"] == "SELL" and not (t["tp"] < t["entry"] < t["sl"]):
            why.append(f"{t['sym']} short with tp/sl the wrong way round")
    failed = [x for x in book.log if "poll failed" in x]
    if failed:
        why.append("an unhandled exception inside the loop: " + failed[0][:160])
    return why


@pytest.mark.parametrize("seed", [7, 11, 23, 99, 1234])
def test_the_engine_holds_together_under_random_abuse(tmp_path, seed):
    rng = random.Random(seed)
    problems = []
    for trial in range(12):
        book, argv = one_trial(tmp_path / f"t{trial}", rng)
        for why in check(book, argv):
            problems.append(f"trial {trial}: {why}\n  argv: {' '.join(argv)}")
    assert not problems, "\n".join(problems[:5])
