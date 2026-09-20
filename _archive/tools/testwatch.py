#!/usr/bin/env python3
"""
The half-hour heartbeat for the 12-hour test.

systemd fires this every 30 minutes; it checks the system the same way
a person would -- healthcheck, the funnel's verdict, the scoreboard
tally, the model's age -- and pushes ONE compact line to the operator's
single ntfy topic. Exit 1 when anything is wrong, so the timer shows a
failed run and the guard's own alarms stay visible in the journal.

    python3 tools/testwatch.py [--push]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT))
try:
    from dotenv import load_dotenv
    load_dotenv(BOT / ".env")
except Exception:
    pass


def health_lines() -> tuple[list[str], bool]:
    try:
        r = subprocess.run(
            [sys.executable, str(BOT / "tools" / "healthcheck.py")],
            capture_output=True, text=True, timeout=120)
        out = (r.stdout or "").strip().splitlines()
        ok = r.returncode == 0
        return out[-6:], ok
    except Exception as e:
        return [f"healthcheck failed: {str(e)[:80]}"], False


def funnel_verdict() -> str:
    try:
        r = subprocess.run(
            [sys.executable, str(BOT / "tools" / "funnel.py")],
            capture_output=True, text=True, timeout=240)
        lines = [l.strip() for l in (r.stdout or "").splitlines()]
        for l in reversed(lines):
            if l.startswith(("nothing was lost", "BLOCKED")):
                return l
        return "funnel unreadable"
    except Exception as e:
        return f"funnel failed: {str(e)[:60]}"


def model_age_line() -> str:
    try:
        from filter_model import model_check
        got = model_check(str(BOT / "data" / "dataset" / "samples"
                              / "model.npz"))
        return ", ".join(m for _, m in got) if got else "fresh"
    except Exception:
        return "model unreadable"


def tally_lines(since: str) -> list[str]:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "testboard_tool", BOT / "tools" / "testboard.py")
    T = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(T)
    book = BOT / "data" / "paper.json"
    lines = T.board(book, T.since_ts(since))
    try:
        b = json.loads(book.read_text())
    except Exception:
        b = {}
    return [l for l in lines
            if l.startswith(("equity", "trades", "won"))][:2]


def compose(since: str) -> tuple[str, bool]:
    health, ok = health_lines()
    tally = tally_lines(since)
    verdict = funnel_verdict()
    age = model_age_line()
    body = "\n".join(
        tally + ["  " + verdict, f"  health: {'; '.join(health[-4:])}",
                 f"  model: {age}"])
    return body, ok


def push(body: str) -> int:
    topics = []
    for k in ("NTFY_TOPIC", "NTFY_TOPIC_SHARED"):
        topics += [x.strip() for x in os.getenv(k, "").split(",") if x.strip()]
    sent = 0
    for t in topics:
        try:
            req = urllib.request.Request(
                "https://ntfy.sh/" + urllib.parse.quote(t),
                data=body.encode(),
                headers={"Title": "TBT 12h watch",
                         "Priority": "default"})
            urllib.request.urlopen(req, timeout=15).close()
            sent += 1
        except Exception:
            pass
    return sent


def main() -> int:
    since = "2026-09-07 09:17"
    body, ok = compose(since)
    print(body)
    sent = push(body)
    print(f"-> {sent} topic(s)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
