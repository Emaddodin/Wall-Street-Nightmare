"""
The nightly backup and the journal section of the daily report.

The backup exists because signals/outcomes/paper.json/journal are the
experiment -- no code can regenerate them, so one tar a day must exist and
old ones must actually be pruned. The report's journal section exists so a
day of refusals by the learned filter is visible in the notification
instead of only in a log nobody reads.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tarfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

_spec = importlib.util.spec_from_file_location(
    "backup_tool", ROOT / "tools" / "backup.py")
backup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backup)

_spec2 = importlib.util.spec_from_file_location(
    "dayreport_tool", ROOT / "tools" / "dayreport.py")
dayreport = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(dayreport)


def _seed(data_dir: Path, *names: str) -> None:
    for name in names:
        (data_dir / name).write_text(f"{{\"seed\": \"{name}\"}}\n")


def test_backup_tars_only_what_exists_and_prunes(tmp_path):
    _seed(tmp_path, "signals.jsonl", "outcomes.jsonl", "paper.json")
    _seed(tmp_path, "paper-archive-20260906-2019.json",
          "paper-events-archive-20260906-2019.jsonl",
          "casestudy-20260906-2019.jsonl")
    out = backup.make_backup(tmp_path, keep=7)
    assert out.exists() and out.suffix == ".gz"
    with tarfile.open(out, "r:gz") as tar:
        names = tar.getnames()
    assert set(names) == {"signals.jsonl", "outcomes.jsonl", "paper.json",
                          "paper-archive-20260906-2019.json",
                          "paper-events-archive-20260906-2019.jsonl",
                          "casestudy-20260906-2019.jsonl"}
    assert not Path(str(out) + ".tmp").exists()
    assert not list(tmp_path.glob("backup-*.tmp"))


def test_backup_prunes_old_ones_down_to_keep(tmp_path):
    # Stamps are second-resolution, so hand out distinct names instead of
    # racing the clock.
    for i in range(1, 11):
        (tmp_path / f"backup-2025010{i:02d}-0000{i:02d}.tar.gz").write_bytes(
            b"x")
    backup.prune(tmp_path, keep=3)
    left = sorted(tmp_path.glob("backup-*.tar.gz"))
    assert [p.name for p in left] == [
        "backup-202501008-000008.tar.gz",
        "backup-202501009-000009.tar.gz",
        "backup-202501010-000010.tar.gz"]


def test_make_backup_returns_file_even_when_nothing_exists(tmp_path):
    out = backup.make_backup(tmp_path, keep=7)
    with tarfile.open(out, "r:gz") as tar:
        assert tar.getnames() == []


def _events(path: Path, rows: list[dict]) -> None:
    now = int(time.time())
    with open(path, "w") as f:
        for i, r in enumerate(rows):
            f.write(json.dumps({"v": 1, "ts": now - 3600 + i, **r}) + "\n")


def test_dayreport_journal_summary_reports_model_and_skips(tmp_path,
                                                          monkeypatch):
    monkeypatch.setattr(dayreport, "BOOK", tmp_path / "paper.json")
    _events(tmp_path / "paper.events.jsonl", [
        {"kind": "signal", "decision": "trade", "branch": "trade_now",
         "sym": "AUSDT"},
        {"kind": "signal", "decision": "pass", "branch": "filter_pass",
         "sym": "BUSDT", "prob": 23},
        {"kind": "signal", "decision": "skip", "branch": "filter_model",
         "sym": "CUSDT", "prob": 5},
        {"kind": "signal", "decision": "skip", "branch": "filter_model",
         "sym": "DUSDT", "prob": 9},
        {"kind": "signal", "decision": "skip", "branch": "sig_few_agents",
         "sym": "EUSDT"},
        {"kind": "exit", "decision": "close", "branch": "exit_target",
         "sym": "AUSDT", "pnl": 1.0},
    ])
    lines = dayreport.journal_summary(24.0)
    text = "\n".join(lines)
    assert "signals 5" in text
    assert "model passed 1" in text and "model refused 2" in text
    assert "taken 1" in text
    assert "5%..9%" in text, "the refused probabilities must be visible"
    assert "sig_few_agents 1" in text
    assert "exits 1" in text


def test_dayreport_journal_summary_counts_rotation_too(tmp_path, monkeypatch):
    monkeypatch.setattr(dayreport, "BOOK", tmp_path / "paper.json")
    _events(tmp_path / "paper.events.jsonl",
            [{"kind": "signal", "decision": "trade", "branch": "trade_now"}])
    _events(tmp_path / "paper.events.jsonl.1",
            [{"kind": "exit", "decision": "close", "branch": "exit_target",
              "pnl": 1.0}])
    text = "\n".join(dayreport.journal_summary(24.0))
    assert "signals 1" in text and "exits 1" in text


def test_dayreport_journal_summary_empty_window_is_honest(tmp_path,
                                                          monkeypatch):
    monkeypatch.setattr(dayreport, "BOOK", tmp_path / "paper.json")
    lines = dayreport.journal_summary(24.0)
    assert lines == ["journal: nothing recorded in this window"]


def test_testboard_reports_the_running_tally(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "testboard_tool", ROOT / "tools" / "testboard.py")
    T = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(T)
    book = tmp_path / "paper.json"
    now = int(__import__("time").time())
    book.write_text(json.dumps({
        "equity": 119.0, "start": 100.0,
        "trades": [{
            "sym": "AAAUSDT", "side": "BUY", "reason": "target",
            "entry": 1.0, "tp": 1.01, "sl": 0.99,
            "margin": 50.0, "notional": 2500.0,
            "opened": now - 3600, "closed": now - 1800, "pnl": 25.0,
        }, {
            "sym": "BBBUSDT", "side": "SELL", "reason": "stop",
            "entry": 2.0, "tp": 1.96, "sl": 2.02,
            "margin": 50.0, "notional": 2000.0,
            "opened": now - 3600, "closed": now - 1200, "pnl": -20.0,
        }],
    }))
    lines = T.board(book, since=now - 7200)
    text = "\n".join(lines)
    assert "equity $119.00" in text
    assert "trades 2" in text and "won 1 / lost 1" in text
    assert "50x" in text and "40x" in text
    assert "tp 1.00%" in text and "stop 1.00%" in text


def test_testboard_says_so_when_nothing_traded(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "testboard_tool", ROOT / "tools" / "testboard.py")
    T = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(T)
    book = tmp_path / "paper.json"
    book.write_text(json.dumps({"equity": 100.0, "start": 100.0,
                                "trades": []}))
    text = "\n".join(T.board(book))
    assert "trades 0" in text
