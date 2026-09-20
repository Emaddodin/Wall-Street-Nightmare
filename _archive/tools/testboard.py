#!/usr/bin/env python3
"""
The 12-hour test, on one screen.

The operator's measurement: under the 50% rule (target pays half the
margin on every trade, stop = 1 x the coin's own ATR, no daily budget),
how many trades does the setup actually take, and what happens to each.
This reads the book's own ledger and journal and prints the running
tally -- the same facts the 04:30 report carries, available any time.

    python3 tools/testboard.py [--since "2026-09-07 09:17"]

Nothing here changes anything; it only counts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BOT = Path(__file__).resolve().parent.parent
BOOK = BOT / "data" / "paper.json"


def since_ts(since: str | None) -> float:
    if not since:
        return 0.0
    import time
    try:
        return time.mktime(time.strptime(since, "%Y-%m-%d %H:%M"))
    except ValueError:
        return 0.0


def board(book: Path = BOOK, since: float = 0.0) -> list[str]:
    try:
        b = json.loads(book.read_text())
    except Exception as e:
        return [f"the book will not parse: {str(e)[:60]}"]
    trades = [t for t in b.get("trades", [])
              if (t.get("opened") or 0) >= since]
    closed = [t for t in trades if t.get("closed")]
    open_now = [t for t in trades if not t.get("closed")]
    won = [t for t in closed if (t.get("pnl") or 0) > 0]
    out = [f"equity ${b.get('equity', 0):,.2f}   (started "
           f"${b.get('start', 0):,.2f})"]
    hours = 0.0
    if trades:
        span = (max(t.get("opened", 0) or t.get("closed", 0)
                    for t in trades)
                - min(t.get("opened", 0) for t in trades)) / 3600.0
        hours = max(span, 0.02)
        out.append(f"trades {len(trades)}   closed {len(closed)}   "
                   f"open {len(open_now)}   ~{len(trades)/hours:.1f} per hour")
    else:
        out.append("trades 0 -- no signal has passed the gates yet")
    if closed:
        rate = 100.0 * len(won) / len(closed)
        out.append(f"won {len(won)} / lost {len(closed)-len(won)}"
                   f"  =  {rate:.0f}%")
        out.append("")
        for t in closed:
            lev = t["notional"] / t["margin"] if t.get("margin") else 0
            sl_pct = 100 * abs(t["sl"] / t["entry"] - 1)
            tp_pct = 100 * abs(t["tp"] / t["entry"] - 1)
            pay = (t.get("pnl") or 0) / (t.get("margin") or 1) * 100
            out.append(f"  {t['sym']:<12} {t['side']:<4} {t['reason']:<8}"
                       f" {lev:>4.0f}x  stop {sl_pct:>4.2f}%  tp {tp_pct:>4.2f}%"
                       f"  margin ${t['margin']:>5.2f}  pnl ${t['pnl']:>+8.2f}"
                       f"  ({pay:+.0f}% of margin)")
    for t in open_now:
        lev = t["notional"] / t["margin"] if t.get("margin") else 0
        out.append(f"  OPEN {t['side']} {t['sym']} @ {t['entry']:.8g} "
                   f"{lev:.0f}x  margin ${t['margin']:.2f}")
    # What the journal saw and refused, so "quiet" and "fussy" are told
    # apart.
    path = book.with_suffix(".events.jsonl")
    try:
        kinds, skips = {}, {}
        for line in path.read_text().splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("ts", 0) < since:
                continue
            kinds[ev.get("kind", "?")] = kinds.get(ev.get("kind", "?"), 0) + 1
            if ev.get("decision") == "skip":
                br = ev.get("branch", "?")
                skips[br] = skips.get(br, 0) + 1
    except OSError:
        pass
    if kinds:
        out.append("")
        out.append("journal: " + ", ".join(f"{k} {v}" for k, v in
                                            sorted(kinds.items())))
    if skips:
        top = ", ".join(f"{k} {v}" for k, v in
                        sorted(skips.items(), key=lambda kv: -kv[1])[:6])
        out.append(f"  skipped: {top}")
    return out


def main() -> int:
    since = None
    if len(sys.argv) > 1 and sys.argv[1] != "--since":
        since = sys.argv[1]
    elif len(sys.argv) > 2 and sys.argv[1] == "--since":
        since = sys.argv[2]
    print("\n".join(board(BOOK, since_ts(since))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
