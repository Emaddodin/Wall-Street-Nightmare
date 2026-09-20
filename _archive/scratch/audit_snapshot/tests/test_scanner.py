"""
The scanner: one at a time, and its output usable while it runs.

`boom2` rewrites the three files the book and the scout read continuously --
the watchlist, the measurements, and the ranked list. Every one of those reads
happens from another process, on a timer, while this one is running.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import boom2  # noqa: E402


def test_the_scanner_writes_atomically():
    """A reader must never see half a file.

    The book reads these on a timer from another process; a truncated
    watchlist is a book that thinks every coin is unmeasured, and an
    unmeasured coin used to be the easiest kind to trade.
    """
    import re
    src = Path(boom2.__file__).read_text()
    assert "os.replace(tmp, path)" in src, "write_atomic does not replace"
    # Every place a data file is opened for writing must go through it.
    raw = re.findall(r'open\(([^)]*?),\s*["\']w["\']\)', src)
    raw = [r for r in raw if "tmp" not in r]
    assert not raw, f"these are written without write_atomic: {raw}"
    for name in ("watch_measures.json", "watchlist.json", "boom.json"):
        calls = re.findall(r'write_atomic\([^)]*' + re.escape(name), src)
        assert calls, f"{name} is not written through write_atomic"


def test_a_second_scanner_refuses_to_run(tmp_path, monkeypatch):
    """systemd blocks a second copy of the unit; nothing blocked a hand-run one.

    Both rewrite the same three files. Each file is replaced atomically, so
    none can be seen half-written -- but the SET of three is not atomic, and
    two overlapping runs leave a watchlist from one and measurements from the
    other. That happened twice in one evening.
    """
    monkeypatch.setattr(boom2, "BOT", tmp_path)
    lock = tmp_path / "data" / "boom.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(os.getpid()))          # us: alive

    ran = []
    monkeypatch.setattr(boom2, "_scan", lambda a: ran.append(1) or 0)
    monkeypatch.setattr(sys, "argv", ["boom2.py"])
    rc = boom2.main()
    assert rc == 1
    assert ran == [], "a second scan ran alongside the first"


def test_a_stale_lock_from_a_dead_scan_is_taken(tmp_path, monkeypatch):
    """A crashed scan must not stop every later one."""
    monkeypatch.setattr(boom2, "BOT", tmp_path)
    lock = tmp_path / "data" / "boom.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("999999")                  # no such pid

    ran = []
    monkeypatch.setattr(boom2, "_scan", lambda a: ran.append(1) or 0)
    monkeypatch.setattr(sys, "argv", ["boom2.py"])
    assert boom2.main() == 0
    assert ran == [1]
    assert not lock.exists(), "the lock was not released"


def test_the_lock_is_released_even_when_the_scan_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(boom2, "BOT", tmp_path)

    def boom(a):
        raise RuntimeError("the venue went away")

    monkeypatch.setattr(boom2, "_scan", boom)
    monkeypatch.setattr(sys, "argv", ["boom2.py"])
    with pytest.raises(RuntimeError):
        boom2.main()
    assert not (tmp_path / "data" / "boom.lock").exists()


def test_the_scanner_takes_its_root_from_its_own_location():
    assert boom2.BOT == ROOT
