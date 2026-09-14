"""
A second book: the same engine, a second ledger.

papertrade.py running with --book is the whole difference between two
instances -- each has its own state file, its own lock, its own journal.
These tests pin that a second instance never writes the first one's
files, that the tools that count it (funnel, dayreport) can be pointed
at its ledger, and the engine behaviours that path exercises (the
--colour gate, two concurrent positions, and the scout's signal-bar
candle that makes edge entries fillable).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import papertrade as P  # noqa: E402
from test_lifecycle import LEVEL_UP, scenario  # noqa: E402
from harness import run_book  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "funnel_tool", ROOT / "tools" / "funnel.py")
F = importlib.util.module_from_spec(_spec)
sys.modules["funnel_tool"] = F
_spec.loader.exec_module(F)

_spec2 = importlib.util.spec_from_file_location(
    "dayreport_tool", ROOT / "tools" / "dayreport.py")
D = importlib.util.module_from_spec(_spec2)
sys.modules["dayreport_tool"] = D
_spec2.loader.exec_module(D)


def test_a_second_book_keeps_its_own_ledger_and_journal(tmp_path):
    """--book owns state, lock and journal; the book's files stay clean."""
    beast = tmp_path / "beast.json"
    moves = [LEVEL_UP, LEVEL_UP * 1.11, LEVEL_UP * 1.11]
    b, _ = scenario(tmp_path, moves, argv=scenario_args(["--book",
                                                         str(beast)]))
    # The trade itself ran exactly as the book would run it -- but it is
    # recorded in the beast's ledger, which is the only place to look.
    assert beast.exists(), "the beast did not get its own state file"
    st = json.loads(beast.read_text())
    assert len(st["trades"]) == 1, b.log
    assert st["trades"][0]["reason"] == "target", b.log
    # The journal sits beside the beast, derived from its own book path.
    assert beast.with_suffix(".events.jsonl").exists(), \
        "the beast's journal was not written beside it"
    # The paper book's own files were never touched by the second instance.
    assert not (tmp_path / "data" / "paper.json").exists()


def scenario_args(extra):
    from test_lifecycle import BASE
    return BASE + extra


def test_funnel_reads_a_named_book(tmp_path):
    b = tmp_path / "beast.json"
    b.write_text(json.dumps({"equity": 42.0, "trades": []}))
    assert F.read_book(b)["equity"] == 42.0


def test_dayreport_journal_summary_reads_a_named_book(tmp_path):
    beast = tmp_path / "beast.json"
    rows = [
        {"v": 1, "ts": int(__import__("time").time()) - 100,
         "kind": "signal", "decision": "trade", "branch": "trade_now",
         "sym": "BEASTUSDT"},
    ]
    with open(beast.with_suffix(".events.jsonl"), "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    text = "\n".join(D.journal_summary(24.0, book=beast))
    assert "signals 1" in text and "taken 1" in text


def test_two_books_can_run_side_by_side(tmp_path):
    """Book and beast both trade the same signal; ledgers stay separate."""
    book_path = tmp_path / "data" / "paper.json"
    beast_path = tmp_path / "data" / "beast.json"
    moves = [LEVEL_UP, LEVEL_UP * 1.11, LEVEL_UP * 1.11]
    b1, _ = scenario(tmp_path, moves, argv=scenario_args([]))
    b2, _ = scenario(tmp_path, moves,
                     argv=scenario_args(["--book", str(beast_path)]))
    assert book_path.exists() and beast_path.exists()
    # Same market, same entries, two separate ledgers -- each holds its own
    # copy of the trade and neither holds the other's.
    bt = json.loads(book_path.read_text())["trades"]
    wt = json.loads(beast_path.read_text())["trades"]
    assert len(bt) == 1, b1.log
    assert len(wt) == 1, b2.log
    assert bt[0]["entry"] == wt[0]["entry"]
    assert bt[0].get("sym") == wt[0].get("sym")


SECOND_ARGV = ["--source", "break", "--reset", "--equity", "100",
              "--book", "__BEAST__",
              "--entry", "edge",
              "--tp", "8.8", "--lev", "75", "--frac", "1.0", "--sl", "1.25",
              "--max-exposure", "2.0", "--counter-frac", "1.0",
              "--colour", "purple", "blue", "orange",
              "--min-confidence", "0", "--min-agents", "0", "--min-atr", "0",
              "--min-entry", "0",
              "--per-day", "0", "--day-start", "17.5", "--max-per-day", "0",
              "--fill-bars", "8", "--step-at", "0", "--step-frac", "1.0",
              "--break-even", "0", "--ct-exit", "6.0",
              "--interval", "60", "--sprint", "60"]


def test_zero_gates_flags_still_trade(tmp_path):
    """The no-limits argv: every gate off, the whole wallet, the 8.8%
    target that is 500% of margin at the stop ceiling."""
    import pytest
    beast = tmp_path / "beast.json"
    argv = [a.replace("__BEAST__", str(beast)) for a in SECOND_ARGV]
    moves = [LEVEL_UP, LEVEL_UP * 1.09, LEVEL_UP * 1.09]
    b, _ = scenario(tmp_path, moves, argv=argv)
    assert beast.exists(), b.log
    st = json.loads(beast.read_text())
    assert len(st["trades"]) == 1, b.log
    tr = st["trades"][0]
    assert tr["reason"] == "target"
    # the whole wallet on the one trade...
    assert tr["margin"] == pytest.approx(100.0)
    # ...at the most the 1.25% stop allows inside the liquidation line
    # (the test exchange caps the symbol at 50; the ~57 ceiling itself is
    # pinned in test_risk_controls -- here the point is the cap is the
    # cap, whatever it is)
    lev = tr["notional"] / tr["margin"]
    assert 45.0 <= lev <= 58.0, lev
    # ...and the target is the operator's 500%-of-margin number
    assert tr["tp"] == pytest.approx(tr["entry"] * 1.088)


def test_orange_passes_when_the_flags_ask_for_it(tmp_path):
    """The colour gate is the --colour flag, not a hardcoded "never orange".

    The beast asks for all three colours; this pins that an orange signal
    is no longer refused on colour when the flags say orange is in.
    """
    from dataset import engine, scenarios
    from dataset.make_dataset import build_scenario_run
    sc = [s for s in scenarios.SCENARIOS
          if s["name"] == "orange_colour"][0]
    kw, sig, coin, clock, st = build_scenario_run(sc)
    kw["args"] = kw["args"] + ["--colour", "purple", "blue", "orange"]
    kw["tmpdir"] = str(tmp_path)
    book, _c, _m = engine.run(**kw)
    assert not any("colour" in ln for ln in book.log), book.log


def test_orange_is_still_refused_by_the_default_flags(tmp_path):
    """...and the book's own colours still refuse it, unchanged."""
    from dataset import engine, scenarios
    from dataset.make_dataset import build_scenario_run
    sc = [s for s in scenarios.SCENARIOS
          if s["name"] == "orange_colour"][0]
    kw, sig, coin, clock, st = build_scenario_run(sc)
    kw["tmpdir"] = str(tmp_path)
    book, _c, _m = engine.run(**kw)
    assert any("colour" in ln for ln in book.log), book.log


def test_two_concurrent_full_wallet_positions(tmp_path):
    """--max-exposure 2.0 with --frac 1.0: two positions, each the whole
    wallet, 200% committed -- the beast's appetite axis, pinned for real."""
    import market as M
    from fakes import FakeBitunix
    from harness import add_break, quiet_window, run_book
    syms = ["AAAUSDT", "BBBUSDT"]
    wins, last = {}, None
    for i, s in enumerate(syms):
        wins[i], last = quiet_window(sym=s, up=True, base=100.0)
    ex = FakeBitunix(prices={s: 100.0 for s in syms}, equity=100.0)
    boom = [{"sym": s, "reach": 40.0, "smooth": 0.4} for s in syms]

    def on_poll(i, exch):
        if i == 1:
            for j, s in enumerate(syms):
                _t, c = add_break(wins[j], thrust=5.0, up=True)
                exch.set_price(s, c)
        elif i == 2:
            for s in syms:
                exch.set_price(s, 100.6)      # the band top: both fill
        else:
            for s in syms:
                exch.set_price(s, 111.66)     # 11% up: 8.8% target hit

    beast = tmp_path / "beast.json"
    argv = [a.replace("__BEAST__", str(beast)) for a in SECOND_ARGV]
    argv += ["--stack"]
    b, _ = run_book(tmp_path, argv, wins, ex, polls=8, on_poll=on_poll,
                    boom_rows=boom, clock_start=last + M.BAR)
    st = json.loads(beast.read_text())
    trades = st["trades"]
    assert len(trades) == 2, b.log
    for t in trades:
        assert t["reason"] == "target", b.log
        assert abs(t["margin"] - 100.0) < 1e-6, t["margin"]
    # both were open at once -- the second never saw a full book
    assert any("exposure full" in ln or "MISSED" in ln for ln in b.log) is False
    # 200% of the wallet worked at the same time
    assert abs(trades[0]["opened"] - trades[1]["opened"]) < 900.0


def test_a_scout_signal_fills_an_edge_entry_from_its_own_candle(tmp_path):
    """The scout row now carries the signal bar's candle; the edge entry
    rests at its extreme on a coin NO window is watching, and fills.

    Before this, every scout combo plan died at "no candle for the signal
    bar" under --entry edge -- the beast's appetite existed on paper only.
    """
    import market as M
    from fakes import FakeBitunix
    from harness import quiet_window, run_book
    win, last = quiet_window(sym="TESTUSDT", up=True, base=100.0)
    ex = FakeBitunix(prices={"TESTUSDT": 100.0, "SCOUTEDUSDT": 1.0},
                     equity=100.0)
    boom = [{"sym": "TESTUSDT", "reach": 40.0, "smooth": 0.4},
            {"sym": "SCOUTEDUSDT", "reach": 40.0, "smooth": 0.4}]
    scout_rows = [{"kind": "combo", "at": last, "t": last, "side": "BUY",
                   "span": 5, "tier": 3, "who": 3, "score": 70, "agents": 4,
                   "wired": True, "sym": "SCOUTEDUSDT",
                   "bar": {"o": 1.0, "h": 1.01, "l": 0.99, "c": 1.0}}]

    def on_poll(i, exch):
        if i == 2:
            exch.set_price("SCOUTEDUSDT", 0.99)   # the candle's low: fill
        elif i >= 3:
            exch.set_price("SCOUTEDUSDT", 1.08)   # 8.8% from the edge

    beast = tmp_path / "beast.json"
    argv = [a.replace("__BEAST__", str(beast)) for a in SECOND_ARGV]
    argv[argv.index("--source") + 1] = "combo"
    argv += ["--scout"]
    b, _ = run_book(tmp_path, argv, {0: win}, ex, polls=7,
                    on_poll=on_poll, clock_start=last + M.BAR,
                    boom_rows=boom, scout_rows=scout_rows)
    assert not any("no candle" in ln for ln in b.log), b.log
    st = json.loads(beast.read_text())
    trades = st["trades"]
    assert len(trades) == 1, b.log
    tr = trades[0]
    assert tr["sym"] == "SCOUTEDUSDT"
    assert tr["entry"] == 0.99, tr["entry"]
    assert tr["reason"] == "target", b.log
    assert abs(tr["margin"] - 100.0) < 1e-6


def test_tp_margin_pays_half_the_margin_with_adaptive_stop(tmp_path):
    """The operator's rule, pinned: stop = the coin's own ATR, leverage
    held inside the liquidation line for THAT stop, and the target pays
    50% of the margin, fixed, whatever the coin and the leverage do."""
    import pytest
    from dataset import engine, scenarios
    from dataset.make_dataset import build_scenario_run
    sc = [s for s in scenarios.SCENARIOS if s["name"] == "exit_target"][0]
    kw, sig, coin, clock, st = build_scenario_run(sc)
    kw["args"] = kw["args"] + ["--entry", "now", "--tp-margin", "0.5",
                               "--sl-atr", "1.0"]
    kw["tmpdir"] = str(tmp_path)
    book, _c, _m = engine.run(**kw)
    state = json.loads((tmp_path / "data" / "paper.json").read_text())
    assert state["trades"], book.log
    tr = state["trades"][0]
    assert tr["reason"] == "target", book.log
    lev = tr["notional"] / tr["margin"]
    sl_pct = 100 * (1 - tr["sl"] / tr["entry"])
    tp_pct = 100 * (tr["tp"] / tr["entry"] - 1)
    # the core rule: the target is 50/lev percent of PRICE -- so the
    # payment is half the margin whatever the coin and its leverage are
    assert tp_pct == pytest.approx(50.0 / lev)
    # the stop is the coin's own ATR (positive, from the live history),
    # and the leverage stays inside the liquidation line for THAT stop
    assert sl_pct > 0.05
    assert lev <= 1.0 / (sl_pct / 100.0 + 0.005) + 1e-9
    # the payment itself: half the margin, less the round-trip fee
    assert tr["pnl"] == pytest.approx(tr["margin"] * 0.5
                                      - tr["notional"] * 12 / 1e4)
    # and the trade rode half the wallet, as asked
    assert tr["margin"] == pytest.approx(50.0)


def test_a_cleared_print_never_fills_the_resting_order(tmp_path):
    """The reunion: an edge order rests while the print still stands, and
    is dropped the moment the scout's next sweep no longer carries it --
    the book must not enter a trade the agents have left."""
    import market as M
    from fakes import FakeBitunix
    from harness import quiet_window, run_book
    win, last = quiet_window(sym="TESTUSDT", up=True, base=100.0)
    ex = FakeBitunix(prices={"TESTUSDT": 100.0, "SCOUTEDUSDT": 1.0},
                     equity=100.0)
    boom = [{"sym": "TESTUSDT", "reach": 40.0, "smooth": 0.4},
            {"sym": "SCOUTEDUSDT", "reach": 40.0, "smooth": 0.4}]
    scout_rows = [{"kind": "combo", "at": last, "t": last, "side": "BUY",
                   "span": 5, "tier": 3, "who": 3, "score": 70, "agents": 4,
                   "wired": True, "sym": "SCOUTEDUSDT",
                   "bar": {"o": 1.0, "h": 1.01, "l": 0.99, "c": 1.0}}]
    scout_file = tmp_path / "data" / "scout.json"

    def on_poll(i, exch):
        if i == 2:
            # the scout's next sweep came back without the print
            scout_file.write_text(json.dumps([]))
            exch.set_price("SCOUTEDUSDT", 0.99)   # the level is reached...
        elif i >= 3:
            exch.set_price("SCOUTEDUSDT", 1.08)

    beast = tmp_path / "beast.json"
    argv = [a.replace("__BEAST__", str(beast)) for a in SECOND_ARGV]
    argv[argv.index("--source") + 1] = "combo"
    argv += ["--scout"]
    b, _ = run_book(tmp_path, argv, {0: win}, ex, polls=6,
                    on_poll=on_poll, clock_start=last + M.BAR,
                    boom_rows=boom, scout_rows=scout_rows)
    assert any("the reunion is over" in ln for ln in b.log), b.log
    state = json.loads((beast).read_text())
    assert state["trades"] == [], b.log


def test_the_trail_saves_the_almost_there_trade(tmp_path):
    """The pullback protection, pinned: the trade runs +1.2% (past one
    ATR), the trail locks most of it in, and the reversal closes it
    ABOVE entry instead of at the full stop."""
    import pytest
    from dataset import engine, scenarios
    from dataset.make_dataset import build_scenario_run
    from dataset.scenarios import base_coin, base_sig
    sc = {
        "name": "trail_test",
        "stage": "exit", "expect": "trade",
        "args": {"--interval": "3.0", "--entry": "now",
                 "--tp-margin": "0.5", "--sl-atr": "1.0",
                 "--trail-atr": "0.75", "--min-atr": "0",
                 "--min-entry": "0"},
        "sig": base_sig(), "coin": base_coin(atr=1.0, lev_cap=20),
        "prices_path": [("up", 1.2), ("down", 3.0)],
        "reason": "stop",
        "exchange": {"max_lev": 20},
        "polls": 5,
    }
    kw, sig, coin, clock, st = build_scenario_run(sc)
    kw["tmpdir"] = str(tmp_path)
    book, _c, _m = engine.run(**kw)
    assert any("TRAIL" in ln for ln in book.log), book.log
    state = json.loads((tmp_path / "data" / "paper.json").read_text())
    tr = state["trades"][0]
    assert tr["reason"] == "stop", book.log
    # entry +1.2% best, trailed 0.75 x 1% ATR behind -> ~ +0.44%
    assert tr["exit"] == pytest.approx(tr["entry"] * 1.012 * 0.9925)
    assert tr["pnl"] > 0, book.log


def test_the_same_path_without_the_trail_stops_at_the_full_stop(tmp_path):
    """Control: no --trail-atr, the same reversal is a full stop below
    entry -- which is the pain the trail exists for."""
    import pytest
    from dataset import engine
    from dataset.make_dataset import build_scenario_run
    from dataset.scenarios import base_coin, base_sig
    sc = {
        "name": "trail_control",
        "stage": "exit", "expect": "trade",
        "args": {"--interval": "3.0", "--entry": "now",
                 "--tp-margin": "0.5", "--sl-atr": "1.0",
                 "--min-atr": "0", "--min-entry": "0"},
        "sig": base_sig(), "coin": base_coin(atr=1.0, lev_cap=20),
        "prices_path": [("up", 1.2), ("down", 3.0)],
        "reason": "stop",
        "exchange": {"max_lev": 20},
        "polls": 5,
    }
    kw, sig, coin, clock, st = build_scenario_run(sc)
    kw["tmpdir"] = str(tmp_path)
    book, _c, _m = engine.run(**kw)
    state = json.loads((tmp_path / "data" / "paper.json").read_text())
    tr = state["trades"][0]
    assert tr["reason"] == "stop", book.log
    # the full stop, wherever the coin's own ATR put it -- below entry
    assert tr["exit"] == pytest.approx(tr["sl"])
    assert tr["exit"] < tr["entry"]
    assert tr["pnl"] < 0, book.log
