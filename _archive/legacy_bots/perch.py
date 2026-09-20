#!/usr/bin/env python3
"""Park the book's own window where the print is about to happen.

The scout walks the whole watchlist and circles a coin that is close to
printing. The book's window did not follow it -- it sat on whatever the panel
had chosen, so the one chart being watched continuously was almost never the
one about to fire.

This closes that gap. It reads the ripeness the scout already writes down and
puts the ripest coin in front of the book, so the eagle is sitting over the
coin rather than passing over it.

    python3 perch.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

BOT = Path(os.environ.get("TBT_BOT") or Path(__file__).resolve().parent)
sys.path.insert(0, str(BOT))

import scout  # noqa: E402   readiness_of: one formula, both birds

SCOUT = BOT / "data" / "scout.json"
WATCH = BOT / "data" / "watchlist.json"
ATR_M = BOT / "data" / "atr_measures.json"
WANTED = BOT / "data" / "chart_coins.json"
BOOK = BOT / "data" / "paper.json"

# A ripeness reading older than this is about a bar that has already closed
# and a count that has already moved on.
FRESH_MIN = 6.0

# How many coins may be asked for at once.
#
# TradingView streams only TWO charts on this account, and the scout owns one
# of them -- it changes symbol every few seconds by design and can never be
# held to a coin. That leaves exactly ONE window that can hold anything, so
# asking for two coins is asking for a window that does not exist.
#
# On 2026-09-06 that is exactly what happened: with SELL CASHCATUSDT open,
# perch asked for the held coin PLUS one ripe one. The guard could place only
# the one, found the other missing, and reported "CASHCATUSDT missing from the
# charts (rebuild did not take)" every two minutes with no way to ever resolve
# it. A fault that cannot be repaired is worse than no fault at all: it trains
# the operator to ignore the alarm.
HOLDABLE = 1


def write_atomic(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, path)


def held() -> set:
    """Coins the book has a position on. Those windows are never moved."""
    try:
        b = json.loads(BOOK.read_text())
    except Exception:
        return set()
    return {str(t.get("sym")).upper() for t in b.get("trades", [])
            if not t.get("closed")}


def ripest() -> list:
    """The coins closest to a print, nearest first.

    The chain, in order: the finder keeps the biggest candles in the market,
    the scout walks only those, and this picks the one among them closest to
    firing. So the coin the book sits over is a high-ATR coin that is one
    module from a print -- not merely one or the other.

    Ordered by how few modules are left before the indicator fires, then by
    the ripeness count, then by how much of the council is already leaning,
    and finally by the coin's own ATR so that between two equally ripe coins
    the one that can actually reach the target wins. All of it comes off
    readings something else already wrote down.
    """
    try:
        allowed = {str(x).upper()
                   for x in json.loads(WATCH.read_text())}
    except Exception:
        allowed = set()
    try:
        atr = {str(k).upper(): float((v or {}).get("atr") or 0)
               for k, v in json.loads(ATR_M.read_text()).items()}
    except Exception:
        atr = {}
    try:
        rows = json.loads(SCOUT.read_text())
    except Exception:
        return []
    now, out = time.time(), []
    states = {}
    for r in rows:
        if now - float(r.get("at", 0)) > FRESH_MIN * 60:
            continue
        sym = str(r.get("sym") or "").upper()
        # Only coins the finder kept. A ripe coin whose candles are too small
        # to reach the target is a print we cannot trade.
        if not sym or (allowed and sym not in allowed):
            continue
        # Remember every coin's freshest expansion: the fallback needs it
        # when nothing is ripe at all.
        exp = r.get("exp")
        if exp is not None:
            if sym not in states or r.get("at", 0) > states[sym][0]:
                states[sym] = (r.get("at", 0), float(exp))
        if r.get("kind") != "ripe" and "ripe" not in str(r.get("kind", "")):
            if r.get("ripe") is None or r.get("short") is None:
                continue
        # The readiness: closeness to the print, the tide, and the shape --
        # each weight measured, not felt. One formula, shared with the
        # scout's own walk order (scout.readiness_of), so the eyesight and
        # the perch never disagree about which coin matters most.
        readiness = scout.readiness_of(r)
        short = int(r.get("short", 9))
        out.append((-readiness, short, -float(r.get("ripe", 0)),
                    -int(r.get("lean", 0)), -atr.get(sym, 0.0), sym))
    out.sort()
    seen, order = set(), []
    for *_, sym in out:
        if sym not in seen:
            seen.add(sym)
            order.append(sym)
    if order:
        return order
    # Nothing ripe: still sit somewhere useful -- the most-expanding coin
    # on the list, then the biggest candles. A quiet day is when the shape
    # eye matters most.
    fallback = sorted(states.items(),
                      key=lambda kv: (-kv[1][1], -atr.get(kv[0], 0.0)))
    return [sym for sym, _ in fallback]


def main() -> int:
    on = held()
    # A position keeps its chart, always -- and it takes the window ahead of
    # anything merely ripe. Never ask for more than HOLDABLE: see above.
    want = list(on)[:HOLDABLE]
    for sym in ripest():
        if len(want) >= HOLDABLE:
            break
        if sym not in want:
            want.append(sym)
    if not want:
        print("nothing ripe and nothing held -- leaving the chart alone")
        return 0
    try:
        now = json.loads(WANTED.read_text())
    except Exception:
        now = []
    if list(now) == want:
        print(f"already on {', '.join(want)}")
        return 0
    write_atomic(WANTED, want)
    print(f"perched on {', '.join(want)}  (was {', '.join(now) or 'nothing'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
