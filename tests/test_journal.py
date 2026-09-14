"""
The book journals its own decisions, append-only, beside its state file.

The journal is a log handler: it must classify the book's decision lines
into the same branches the funnel and the dataset use, it must never raise,
and a run of the real book must leave events on disk -- including the
refusals that were silent until the log lines were added.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import journal  # noqa: E402
from dataset import engine, scenarios  # noqa: E402


CASES = [
    ("skip  BUY FLOCKUSDT 15m purple/Tesla PEERLESS agents 4 score 52 -- "
     "thin_coin: ATR 1.20% of price, the floor is 2.50%",
     "signal", "skip", "sig_thin_coin", "FLOCKUSDT"),
    ("wait  BUY MAGMAUSDT 15m ? ? agents 3 score 30 -- scored 25, the bar "
     "is 60 right now", "signal", "wait", "sig_pace_wait", "MAGMAUSDT"),
    ("stale SELL USELESSUSDT bar 2000s old (2.2 bars) -- not traded",
     "signal", "skip", "sig_stale_age", "USELESSUSDT"),
    ("drop  BUY XUSDT -- absorbed as backlog on a fresh chart",
     "signal", "skip", "sig_backlog_absorbed", "XUSDT"),
    ("OPEN  SELL NIULAIUSDT 15m purple/Tesla PEERLESS agents 4 score 62 | "
     "2500 @ 1 tp 0.95 sl 1.0125 margin $50.00  7.0s after the signal",
     "signal", "trade", "trade_now", "NIULAIUSDT"),
    ("WAIT  BUY SCEN1USDT 15m purple/Tesla PEERLESS agents 4 score 62 | "
     "want 0.999, price now 1, 1 bars to fill or it is dropped",
     "rest", "rest", "rest_placed", "SCEN1USDT"),
    ("FILL  BUY SCEN1USDT at the candle edge 0.999 (signal was 1)",
     "rest", "open", "rest_filled_at_level", "SCEN1USDT"),
    ("DROP  BUY SCEN1USDT -- never returned to 0.999 (0 filled / 1 dropped)",
     "rest", "skip", "rest_expired_dropped", "SCEN1USDT"),
    ("LATE  BUY SCEN1USDT at market 1.004 -- limit 0.999 never hit",
     "rest", "open", "rest_late_market_fallback", "SCEN1USDT"),
    ("fill skipped BUY SCEN1USDT -- already in SCEN1USDT",
     "rest", "skip", "rest_fill_skipped_one_per_symbol", "SCEN1USDT"),
    ("skip  BUY SCEN1USDT 15m ? ? agents 4 score 62 -- equity is gone, "
     "nothing more will be taken",
     "signal", "skip", "sig_equity_zero", "SCEN1USDT"),
    ("TARGET SELL NIULAIUSDT  +5.00%  pnl $+68.11  equity $120.97",
     "exit", "close", "exit_target", "NIULAIUSDT"),
    ("STOP  BUY WOOUSDT  -1.25%  pnl $-34.25  equity $65.75",
     "exit", "close", "exit_stop", "WOOUSDT"),
    ("LIQ   BUY XUSDT at 0.99 -- 1.00% against us reached the liquidation "
     "line before the 1.25% stop.  lost the whole $50.00 margin",
     "exit", "close", "exit_liquidated", "XUSDT"),
    ("HALT  BUY XUSDT -- $10.00 lost today against a 5% limit on $100.00. "
     "No new positions.", "state", "skip", "sig_loss_limit", "XUSDT"),
    ("model  BUY XUSDT 23%", "signal", "pass", "filter_pass", "XUSDT"),
    ("skip  BUY XUSDT -- filter: the model gives it 5%, the floor is 12%",
     "signal", "skip", "filter_model", "XUSDT"),
    ("filter: model reloaded (12421 training rows, threshold 0.123)",
     "state", "pass", "filter_reloaded", None),
]


@pytest.mark.parametrize("line,kind,decision,branch,sym", CASES)
def test_classify_line(line, kind, decision, branch, sym):
    ev = journal.classify_line(line)
    assert ev is not None, line
    assert ev["kind"] == kind
    assert ev["decision"] == decision
    assert ev["branch"] == branch
    if sym is not None:
        assert ev.get("sym") == sym


def test_numbers_are_parsed():
    ev = journal.classify_line(
        "OPEN  SELL NIULAIUSDT 15m purple/Tesla PEERLESS agents 4 "
        "score 62 | 2500 @ 1 tp 0.95 sl 1.0125")
    assert ev["agents"] == 4 and ev["score"] == 62


def test_model_probabilities_are_parsed():
    # The prob field on the model's own lines is the whole point of the
    # journal: a pass and a refusal both must carry the number, even though
    # neither ends in a %%.
    passed = journal.classify_line("model  BUY XUSDT 23%")
    assert passed["branch"] == "filter_pass" and passed["prob"] == 23
    refused = journal.classify_line(
        "skip  BUY XUSDT -- filter: the model gives it 5%, the floor is 12%")
    assert refused["branch"] == "filter_model" and refused["prob"] == 5


def test_junk_never_raises():
    assert journal.events_for("completely unrelated noise") == []
    assert journal.events_for("") == []


def test_handler_writes_and_rotates(tmp_path):
    log = logging.getLogger("paper-journal-test")
    log.setLevel(logging.INFO)
    h = journal.attach(log, tmp_path / "book.events.jsonl")
    log.info("skip  BUY TESTUSDT 15m ? ? agents 2 score 10 -- agents")
    log.info("TARGET SELL TESTUSDT  +5.00%  pnl $+1.00  equity $101.00")
    log.info("noise")
    log.removeHandler(h)
    rows = [json.loads(l) for l in
            (tmp_path / "book.events.jsonl").read_text().splitlines()]
    assert [r["kind"] for r in rows] == ["signal", "exit"]
    assert all(r["v"] == 1 and r["ts"] for r in rows)


def test_real_run_leaves_events(tmp_path):
    """A real book run writes its decisions beside its own state file."""
    from dataset.make_dataset import build_scenario_run
    sc = [s for s in scenarios.SCENARIOS
          if s["name"] == "exit_target"][0]
    kw, sig, coin, clock, st = build_scenario_run(sc)
    kw["tmpdir"] = str(tmp_path)
    book, _c, meas_file = engine.run(**kw)
    events = tmp_path / "data" / "paper.events.jsonl"
    assert events.exists(), "the journal file was not written"
    kinds = [json.loads(l)["kind"] for l in events.read_text().splitlines()]
    assert "signal" in kinds and "exit" in kinds
    closed = [t for t in book.state.get("trades") or [] if t.get("closed")]
    assert closed and closed[0]["reason"] == "target"


def test_equity_zero_is_no_longer_silent(tmp_path):
    from dataset.make_dataset import build_scenario_run
    sc = [s for s in scenarios.SCENARIOS
          if s["name"] == "equity_zero"][0]
    kw, sig, coin, clock, st = build_scenario_run(sc)
    kw["tmpdir"] = str(tmp_path)
    book, _c, _m = engine.run(**kw)
    assert any("equity is gone" in ln for ln in book.log), \
        "the wiped-account refusal must say so out loud"


def test_fill_site_one_per_symbol_is_no_longer_silent(tmp_path):
    from dataset.make_dataset import run_rest_fill
    book, sigA, meas, clock, st, meas_file = run_rest_fill(
        "one_per_symbol", str(tmp_path))
    assert any("fill skipped" in ln and "already in" in ln
               for ln in book.log), \
        "the fill refused for a held coin must say so out loud"
