"""The chrome recycle must never restart Chrome while a trade is open.

Chrome grows ~80 MB an hour and the guard's emergency path (restart below
250 MB available) waits for two consecutive bad readings, which never arrived
because memory kept recovering just over the line. This bounds the growth
instead -- but only when nothing is at risk.
"""
import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "recycle_chrome", ROOT / "tools" / "recycle_chrome.py")
rc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rc)


@pytest.fixture(autouse=True)
def no_proc(monkeypatch):
    """/proc does not exist on every machine the suite runs on."""
    monkeypatch.setattr(rc, "available_mb", lambda: 300)


def book(tmp_path, trades):
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    (d / "paper.json").write_text(json.dumps({"trades": trades}))
    return str(tmp_path)


def test_an_open_trade_blocks_the_recycle(tmp_path, monkeypatch, capsys):
    rc.BOT = book(tmp_path, [{"sym": "X", "closed": None}])
    monkeypatch.setattr(rc, "chrome_up_hours", lambda: 99.0)
    called = []
    monkeypatch.setattr(rc.subprocess, "run", lambda *a, **k: called.append(a))
    assert rc.main() == 0
    assert called == [], "chrome must not be restarted with a position open"
    assert "not recycling" in capsys.readouterr().out


def test_a_closed_trade_does_not_block(tmp_path, monkeypatch):
    rc.BOT = book(tmp_path, [{"sym": "X", "closed": 123.0}])
    monkeypatch.setattr(rc, "chrome_up_hours", lambda: 99.0)
    called = []
    monkeypatch.setattr(rc.subprocess, "run", lambda *a, **k: called.append(a))
    monkeypatch.setattr(rc.time, "sleep", lambda s: None)
    assert rc.main() == 0
    assert called and "restart" in called[0][0]


def test_an_unreadable_book_blocks_the_recycle(tmp_path, monkeypatch):
    """Not knowing is not permission."""
    rc.BOT = str(tmp_path / "nowhere")
    monkeypatch.setattr(rc, "chrome_up_hours", lambda: 99.0)
    called = []
    monkeypatch.setattr(rc.subprocess, "run", lambda *a, **k: called.append(a))
    assert rc.main() == 0
    assert called == []


def test_a_young_chrome_is_left_alone(tmp_path, monkeypatch):
    rc.BOT = book(tmp_path, [])
    monkeypatch.setattr(rc, "chrome_up_hours", lambda: 1.0)
    called = []
    monkeypatch.setattr(rc.subprocess, "run", lambda *a, **k: called.append(a))
    assert rc.main() == 0
    assert called == []


def test_healthcheck_does_not_alarm_at_the_guards_own_threshold():
    """The guard owns 250 MB and waits for a second strike; alarming there
    buzzed the phone for something the guard had chosen not to act on."""
    src = (ROOT / "tools" / "healthcheck.py").read_text()
    assert "MEM_GUARD = 250" in src
    assert "MEM_DANGER = 150" in src
    assert "mb < MEM_DANGER" in src, "the alarm must key on the danger line"
