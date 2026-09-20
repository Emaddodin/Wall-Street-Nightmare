"""The recorder that turns a trade into something we can learn from.

A closed trade leaves one number, which is enough to keep score and useless
for learning: it cannot say whether the trade was ever in front, how far it
ran before it turned, or what the indicator was saying while it happened. Two
trades that stop out for the same amount can be entirely different mistakes.

These tests are about the recorder never lying and never getting in the way --
it must not touch the book, and a reading too old to mean anything must not be
attached to a moment it does not describe.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "casestudy_tool", ROOT / "tools" / "casestudy.py")
C = importlib.util.module_from_spec(_spec)
sys.modules["casestudy_tool"] = C
_spec.loader.exec_module(C)


def test_a_fresh_scout_reading_is_attached_to_the_coin(tmp_path, monkeypatch):
    f = tmp_path / "scout.json"
    f.write_text(json.dumps([
        {"sym": "AAAUSDT", "kind": "combo", "at": time.time(),
         "side": "BUY", "agents": 4, "score": 53, "tier": 3, "ct": 0},
    ]))
    monkeypatch.setattr(C, "SCOUT", f)
    got = C.scout_by_coin()
    assert got["AAAUSDT"]["agents"] == 4


def test_a_stale_reading_is_not_attached_to_a_moment_it_never_saw(
        tmp_path, monkeypatch):
    """An indicator reading from half an hour ago describes half an hour ago.

    Recording it against this minute's price would make a case study that
    reads as though the indicator said something it did not.
    """
    f = tmp_path / "scout.json"
    f.write_text(json.dumps([
        {"sym": "AAAUSDT", "kind": "combo", "at": time.time() - 3600,
         "side": "BUY", "agents": 4},
    ]))
    monkeypatch.setattr(C, "SCOUT", f)
    assert C.scout_by_coin() == {}


def test_a_shape_row_is_not_an_indicator_reading(tmp_path, monkeypatch):
    f = tmp_path / "scout.json"
    f.write_text(json.dumps([
        {"sym": "AAAUSDT", "kind": "break", "at": time.time(), "level": 1.0},
    ]))
    monkeypatch.setattr(C, "SCOUT", f)
    assert C.scout_by_coin() == {}


def test_an_unreadable_scout_file_records_nothing_rather_than_failing(
        tmp_path, monkeypatch):
    """The recorder must never be the reason a pass dies."""
    f = tmp_path / "scout.json"
    f.write_text("{not json")
    monkeypatch.setattr(C, "SCOUT", f)
    assert C.scout_by_coin() == {}


def test_the_recorder_never_writes_to_the_book():
    """It is a witness, not a participant."""
    src = (ROOT / "tools" / "casestudy.py").read_text()
    body = src[src.index("def record("):src.index("def report(")]
    assert "BOOK.write" not in body and "write_text" not in body, (
        "the case study recorder writes to the book")
    for danger in ("place_order", "cancel", "flash_close", "--live"):
        assert danger not in src, f"the recorder can {danger}"


def test_the_book_is_only_ever_read(tmp_path, monkeypatch):
    """A missing or broken book is reported, not repaired or overwritten."""
    b = tmp_path / "paper.json"
    b.write_text("{not json")
    monkeypatch.setattr(C, "BOOK", b)
    assert C.record() == 1
    assert b.read_text() == "{not json", "the recorder rewrote the book"


def test_a_state_row_fills_the_minutes_a_signal_never_covers(tmp_path,
                                                             monkeypatch):
    """Most minutes have no signal, and those are the ones worth studying.

    read_combo() answers only while a tradeable signal is printing. The
    interesting minute in a trade is usually the one where the indicator
    quietly changed its mind, and there is no signal on that bar to carry the
    news -- so the case study would record a blank exactly where the answer
    is.
    """
    f = tmp_path / "scout.json"
    f.write_text(json.dumps([
        {"sym": "AAAUSDT", "kind": "state", "at": time.time(),
         "votes": 4, "plan_dir": -1, "htf": 1, "ct": -1},
    ]))
    monkeypatch.setattr(C, "SCOUT", f)
    got = C.scout_by_coin()
    assert got["AAAUSDT"]["votes"] == 4
    assert got["AAAUSDT"]["ct"] == -1


def test_a_live_signal_beats_a_state_row_for_the_same_coin(tmp_path,
                                                           monkeypatch):
    """Both describe the same minute; the signal is the fuller answer."""
    f = tmp_path / "scout.json"
    f.write_text(json.dumps([
        {"sym": "AAAUSDT", "kind": "state", "at": time.time(), "votes": 4},
        {"sym": "AAAUSDT", "kind": "combo", "at": time.time(),
         "side": "BUY", "agents": 4, "score": 53},
    ]))
    monkeypatch.setattr(C, "SCOUT", f)
    assert C.scout_by_coin()["AAAUSDT"]["kind"] == "combo"

    # and in the other order, so it is the kind that decides and not the
    # position in the file
    f.write_text(json.dumps([
        {"sym": "AAAUSDT", "kind": "combo", "at": time.time(),
         "side": "BUY", "agents": 4, "score": 53},
        {"sym": "AAAUSDT", "kind": "state", "at": time.time(), "votes": 4},
    ]))
    assert C.scout_by_coin()["AAAUSDT"]["kind"] == "combo"
