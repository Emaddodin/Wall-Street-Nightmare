"""
The beast: the same engine, a second ledger.

The beast is papertrade.py running with --book, which is the whole
difference between it and the book -- its own state file, its own lock,
its own journal. These tests pin that a second instance never writes the
first one's files, and that the tools that count it (funnel, dayreport)
can be pointed at its ledger.
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


def test_the_beast_keeps_its_own_ledger_and_journal(tmp_path):
    """--book owns state, lock and journal; the book's files stay clean."""
    beast = tmp_path / "beast.json"
    moves = [LEVEL_UP * 1.11] * 3
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
    moves = [LEVEL_UP * 1.11] * 3
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
