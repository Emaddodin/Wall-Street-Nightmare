#!/usr/bin/env python3
"""Restart Chrome on a schedule, but only when it is safe to.

Chrome is the only thing on this box that grows -- about 80 MB an hour, and
after sixteen hours it was holding 2.45 GB of a 3.9 GB machine. The guard has
an emergency path for that (restart Chrome below 250 MB available) but it waits
for two consecutive bad readings, and memory kept recovering just above the
line between its rounds, so it never fired while Chrome kept growing.

This bounds the growth instead of reacting to it. Two conditions, both
required:

  * no position is open -- the book reads its prices from the exchange, not
    from the chart, so a restart cannot hurt an open trade, but there is no
    reason to take the risk while one is running;
  * Chrome has been up longer than MIN_UP_H, so this cannot thrash.

Prints one line saying what it did. Exit code is always 0: a skipped recycle
is a normal outcome, not a failure.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

BOT = str(Path(os.environ.get("TBT_BOT")
               or Path(__file__).resolve().parent.parent))
MIN_UP_H = 6.0


def sh(*args):
    return subprocess.run(args, capture_output=True, text=True).stdout.strip()


def open_positions() -> int:
    try:
        book = json.load(open(f"{BOT}/data/paper.json"))
    except Exception:
        # An unreadable book is not permission to restart anything.
        return 1
    return len([t for t in book.get("trades", []) if not t.get("closed")])


def chrome_up_hours() -> float:
    out = sh("systemctl", "show", "tbt-chrome", "-p",
             "ActiveEnterTimestampMonotonic")
    try:
        usec = int(out.split("=")[1])
    except Exception:
        return 0.0
    if usec <= 0:
        return 0.0
    with open("/proc/uptime") as fh:
        now_usec = float(fh.read().split()[0]) * 1e6
    return max(0.0, (now_usec - usec) / 1e6 / 3600.0)


def available_mb() -> int:
    for line in open("/proc/meminfo"):
        if line.startswith("MemAvailable"):
            return int(line.split()[1]) // 1024
    return 0


def main() -> int:
    up = chrome_up_hours()
    mem = available_mb()
    if up < MIN_UP_H:
        print(f"chrome up {up:.1f}h, under {MIN_UP_H:.0f}h -- leaving it alone")
        return 0
    held = open_positions()
    if held:
        print(f"{held} position(s) open -- not recycling chrome, "
              f"up {up:.1f}h, {mem} MB available")
        return 0
    print(f"recycling chrome after {up:.1f}h ({mem} MB available, nothing open)")
    subprocess.run(["systemctl", "restart", "tbt-chrome"])
    time.sleep(25)
    print(f"chrome restarted, {available_mb()} MB available")
    return 0


if __name__ == "__main__":
    sys.exit(main())
