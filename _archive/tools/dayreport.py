#!/usr/bin/env python3
"""
What the book did, said in one notification.

Runs on the server, on a timer, and pushes to the same ntfy topics the book
itself uses. It reads the book on disk and the journal, and it reports the
things that actually decide whether this is working:

  * what the account did
  * how many setups were taken, and how they ended
  * for everything NOT taken, which gate stopped it

That last part is the one that has been missing. A book that takes nothing
looks exactly like a book that is being careful, and for weeks there was no
way to tell the two apart without reading a day of log by hand.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
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

BOOK = BOT / "data" / "paper.json"
UNIT = "tbt-paper"
TAG = ""


def journal(since: str = "24 hours ago") -> str:
    try:
        r = subprocess.run(
            ["journalctl", "-u", UNIT, "--since", since,
             "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=120)
        return r.stdout or ""
    except Exception:
        return ""


def since_restart() -> str | None:
    """When the book last started, as journalctl wants it.

    Health is measured from here, not across the whole window. The first run
    of this report shouted about 379 failed polls that had all happened on a
    version replaced hours earlier -- an alarm for a fault that no longer
    existed, which is the fastest way to teach someone to ignore alarms.
    """
    try:
        r = subprocess.run(
            ["systemctl", "show", UNIT, "-p", "ActiveEnterTimestamp",
             "--value"], capture_output=True, text=True, timeout=30)
        v = (r.stdout or "").strip()
        return v or None
    except Exception:
        return None


def book_summary(hours: float = 24.0, book: Path | None = None) -> list[str]:
    try:
        b = json.loads((book or BOOK).read_text())
    except Exception as e:
        return [f"the book will not parse: {str(e)[:60]}"]
    since = time.time() - hours * 3600
    trades = b.get("trades", [])
    closed = [t for t in trades if t.get("closed") and t["closed"] >= since]
    opened = [t for t in trades if (t.get("opened") or 0) >= since]
    still = [t for t in trades if not t.get("closed")]
    won = [t for t in closed if (t.get("pnl") or 0) > 0]
    lost = [t for t in closed if (t.get("pnl") or 0) <= 0]
    eq, start = b.get("equity", 0.0), b.get("start", 0.0) or 1.0

    out = [f"equity ${eq:,.2f}  ({(eq / start - 1) * 100:+.1f}% from "
           f"${start:,.2f})"]
    out.append(f"opened {len(opened)}   closed {len(closed)}   "
               f"open now {len(still)}")
    if closed:
        rate = 100.0 * len(won) / len(closed)
        out.append(f"won {len(won)} / lost {len(lost)}  =  {rate:.0f}%")
        by = {}
        for t in closed:
            by[t.get("reason", "?")] = by.get(t.get("reason", "?"), 0) + 1
        out.append("  " + ", ".join(f"{k} {v}" for k, v in sorted(by.items())))
        for t in closed[-6:]:
            out.append(f"  {t['side']} {t['sym']} {t.get('reason', '?')} "
                       f"${t.get('pnl', 0):+.2f}")
    else:
        out.append("nothing closed")
    for t in still:
        lev = (t["notional"] / t["margin"]) if t.get("margin") else 0
        out.append(f"  OPEN {t['side']} {t['sym']} @ {t['entry']:.8g} "
                   f"{lev:.0f}x")
    return out


def gate_summary(log: str, health: str | None = None) -> list[str]:
    n = {
        "low leverage": log.count("low_leverage"),
        "weak candle": log.count("weak_candle"),
        "below the floor": log.count("below_threshold"),
    }
    plans = log.count("PLAN  ")
    fills = log.count("FILL  ") + log.count("LIVE OPEN")
    drops = log.count("DROP  ")
    out = [f"plans {plans}   filled {fills}   unfilled {drops}"]
    if any(n.values()):
        out.append("refused: " + ", ".join(f"{k} {v}" for k, v in n.items()
                                           if v))
    # Health is only ever about the version running now.
    hl = health if health is not None else log
    frozen = hl.count("FROZEN")
    failed = hl.count("poll failed")
    if frozen or failed:
        bits = []
        if frozen:
            bits.append(f"chart froze {frozen}x")
        if failed:
            bits.append(f"{failed} polls failed")
        out.append("!! " + ", ".join(bits) + " (since the last restart)")
    else:
        out.append("healthy since the last restart")
    return out


def journal_summary(hours: float = 24.0, book: Path | None = None) -> list[str]:
    """What the book's own journal says about the window.

    Every decision the book makes lands in a JSONL file beside its state
    file (see journal.py). The report reads that file directly instead of
    grepping a day of logs: the model's pass/refuse line is the one number
    that says whether the learned filter is doing anything at all, and the
    skip-branch histogram says which gate is eating the rest.
    """
    path = (book or BOOK).with_suffix(".events.jsonl")
    since = time.time() - hours * 3600
    rows = []
    for p in (path, Path(str(path) + ".1")):
        try:
            for ln in p.read_text().splitlines():
                try:
                    ev = json.loads(ln)
                except ValueError:
                    continue
                if ev.get("ts", 0) >= since:
                    rows.append(ev)
        except OSError:
            pass
    if not rows:
        return ["journal: nothing recorded in this window"]
    by_kind = {}
    for ev in rows:
        by_kind[ev.get("kind", "?")] = by_kind.get(ev.get("kind", "?"), 0) + 1
    out = ["journal: " + ", ".join(f"{k} {v}" for k, v in
                                    sorted(by_kind.items()))]
    sigs = [ev for ev in rows if ev.get("kind") == "signal"]
    taken = [ev for ev in sigs if ev.get("decision") == "trade"]
    passed = [ev for ev in sigs if ev.get("branch") == "filter_pass"]
    refused = [ev for ev in sigs if ev.get("branch") == "filter_model"]
    if sigs:
        out.append(f"  signals {len(sigs)}  model passed {len(passed)}  "
                   f"model refused {len(refused)}  taken {len(taken)}")
    if refused:
        probs = sorted(ev["prob"] for ev in refused if "prob" in ev)
        if probs:
            out.append(f"  refused by the model at "
                       f"{probs[0]}%..{probs[-1]}% model score")
    skips = {}
    for ev in sigs:
        if ev.get("decision") == "skip":
            skips[ev.get("branch", "?")] = skips.get(ev.get("branch", "?"), 0) + 1
    if skips:
        top = ", ".join(f"{k} {v}" for k, v in
                        sorted(skips.items(), key=lambda kv: -kv[1])[:5])
        out.append(f"  skipped: {top}")
    exits = [ev for ev in rows if ev.get("kind") == "exit"
             and ev.get("decision") == "close"]
    won = [ev for ev in exits if (ev.get("pnl") or 0) > 0]
    if exits:
        out.append(f"  exits {len(exits)} (journaled won {len(won)})")
    reloads = sum(1 for ev in rows if ev.get("branch") == "filter_reloaded")
    if reloads:
        out.append(f"  model hot-reloaded {reloads}x")
    return out


def send(title: str, body: str) -> int:
    topics = []
    for k in ("NTFY_TOPIC", "NTFY_TOPIC_SHARED"):
        topics += [x.strip() for x in os.getenv(k, "").split(",") if x.strip()]
    sent = 0
    for t in topics:
        try:
            req = urllib.request.Request(
                "https://ntfy.sh/" + urllib.parse.quote(t),
                data=body.encode("utf-8"),
                headers={"Title": title.encode("utf-8").decode("latin-1",
                                                               "replace"),
                         "Priority": "default"})
            urllib.request.urlopen(req, timeout=15).close()
            sent += 1
        except Exception as e:
            print(f"  ntfy {t[:14]}: {str(e)[:70]}", file=sys.stderr)
    return sent


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("hours", nargs="?", type=float, default=24.0)
    ap.add_argument("--unit", default="tbt-paper",
                    help="systemd unit whose journal the health lines come "
                         "from (default tbt-paper)")
    ap.add_argument("--book", default="", metavar="PATH",
                    help="state file to summarise (default data/paper.json)")
    ap.add_argument("--tag", default="",
                    help="name in the ntfy title when reporting a second "
                         "book")
    a = ap.parse_args()
    global BOOK, UNIT, TAG
    UNIT = a.unit
    TAG = a.tag
    if a.book:
        BOOK = Path(a.book).expanduser().resolve()
    hours = a.hours
    log = journal(f"{hours:g} hours ago")
    started = since_restart()
    health = journal(started) if started else None
    # The funnel matters as much as the result: three or four certain trades
    # a day is the design, so "nothing was lost on the way" is the number to
    # wake up to, not "how many".
    funnel = []
    try:
        r = subprocess.run(
            [sys.executable, str(BOT / "tools" / "funnel.py"), f"{hours:g}",
             "--unit", UNIT, "--book", str(BOOK)],
            capture_output=True, text=True, timeout=240)
        for ln in (r.stdout or "").splitlines():
            t = ln.strip()
            if t.startswith(("offered to the book", "judged", "plans rested",
                             "filled", "still waiting", "dropped unfilled",
                             "nothing was lost", "BLOCKED")):
                funnel.append(t)
    except Exception:
        pass
    lines = book_summary(hours) + [""] + journal_summary(hours) \
        + [""] + gate_summary(log, health)
    if funnel:
        lines += [""] + funnel
    body = "\n".join(lines)
    print(body)
    # Quiet is a result too, and a report that only arrives on a busy day is
    # the one that goes unnoticed on the day it mattered.
    title = f"TBT {TAG + ' ' if TAG else ''}{hours:g}h"
    sent = send(title, body)
    print(f"\n-> {sent} topic(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
