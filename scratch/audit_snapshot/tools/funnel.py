#!/usr/bin/env python3
"""
Where candidates go, and where they stop.

This engine is not supposed to be fast. Three or four trades a day, each one
worth the whole size, is the design. So the question that matters is never
"why so few" -- it is "did anything get lost on the way that should not have".

Every stage below either passes a candidate on or refuses it for a reason it
can name. A candidate that disappears without a reason is the fault this looks
for: an opportunity burned, which is the one cost this strategy cannot afford
and the one that leaves no trace in the book.

    python3 tools/funnel.py [hours]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

BOT = Path(__file__).resolve().parent.parent
DATA = BOT / "data"

# Which book this run describes. The funnel is written for the paper book;
# the beast is the same engine with its own unit and its own ledger, so
# both are asked the same question with different names.
UNIT = "tbt-paper"
BOOK = DATA / "paper.json"


def journal(hours: float) -> str:
    try:
        r = subprocess.run(
            ["journalctl", "-u", UNIT, "--since", f"{hours:g} hours ago",
             "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=180)
        return r.stdout or ""
    except Exception:
        return ""


def journal_since(when: str) -> str:
    try:
        r = subprocess.run(
            ["journalctl", "-u", UNIT, "--since", when,
             "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=180)
        return r.stdout or ""
    except Exception:
        return ""


def read_book(path: Path) -> dict:
    """The book's own ledger, read for the summary lines."""
    return json.loads(path.read_text())


def scout_log(hours: float) -> str:
    try:
        r = subprocess.run(
            ["journalctl", "-u", "tbt-scout", "--since", f"{hours:g} hours ago",
             "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=180)
        return r.stdout or ""
    except Exception:
        return ""


def bar(label: str, n: int, of: int, note: str = "") -> None:
    pct = (100.0 * n / of) if of else 0.0
    width = int(pct / 4)
    print(f"  {label:<26}{n:>6}  {'█' * width}{'·' * (25 - width)} "
          f"{pct:>5.1f}%  {note}")


def since_restart() -> str:
    try:
        return subprocess.run(
            ["systemctl", "show", "tbt-paper", "-p", "ActiveEnterTimestamp",
             "--value"], capture_output=True, text=True,
            timeout=30).stdout.strip()
    except Exception:
        return ""


def count(log: str) -> dict:
    """Classify a book's log into the stages of the funnel.

    Split out of main() so it can be run against a log written by hand. This
    counter has now been wrong five times -- mixed denominators reporting
    584%, a line matching two gates at once, post-score refusals counted as
    passes, an expiry that showed as "still waiting" forever, and the two
    fixed here. Every one of them either invented a fault or hid one, and not
    one was visible without a log to check the arithmetic against.
    """
    offered = (len(re.findall(r"scout brought", log))
               + len(re.findall(r"      \w+USD[TC]: level ", log)))

    # Counted per line, and each line classified once.
    #
    # `reject ... -- below_threshold: scored 47.9 of 100` contains both words,
    # so counting substrings scored it twice and left the totals one short --
    # which this tool then reported as a candidate that had "vanished". A
    # counter that invents a fault is worse than no counter.
    #
    # A refusal is not always spelled "reject". A candidate that scores well
    # and then finds the book full is logged as `skip ... already committed`,
    # so measuring the gates against reject lines alone left this tool saying
    # "63 rejections logged, 67 attributed to a named gate" every time the
    # book was full -- a permanent four-off in a check whose whole purpose is
    # to notice a gate that forgot to log. A warning that is always on is a
    # warning nobody reads.
    rejected = passed = skipped = 0
    for ln in log.splitlines():
        if " reject " in ln or ln.lstrip().startswith("reject "):
            rejected += 1
        elif re.search(r"\s\w+USD[TC]: scored [0-9.]+ of ", ln):
            passed += 1
        elif re.search(r"^OPEN\s", ln):
            passed += 1                    # the combo path's own pass line
        elif ("already committed" in ln or "twenty-four hours" in ln
              or "already in " in ln or "HALT" in ln
              or "equity is gone" in ln
              or "filter: the model" in ln
              or re.search(r"^skip\s", ln)):
            skipped += 1                   # every combo refusal, whatever gate

    gates = [
        ("low leverage", log.count("low_leverage"),
         "the exchange caps it under 50x"),
        ("weak candle", log.count("weak_candle"),
         "the break had no force behind it"),
        ("price ran away", log.count("ran_away"),
         "the level was already out of reach"),
        ("below the floor", log.count("below_threshold"),
         "judged and found wanting"),
        # Only the pre-plan refusal belongs here. `exposure full` is written
        # on the fill path, about an order that already existed -- counting it
        # as a candidate refusal added it a second time, on top of the plan
        # section where it already appears, and subtracted it from the check
        # below, which is how a real vanished candidate could be hidden by an
        # unrelated expiry.
        ("exposure full", log.count("already committed"),
         "no room left in the book"),
        ("day's count spent", log.count("twenty-four hours"), "the daily cap"),
        ("halted", log.count("HALT"), "the daily loss limit"),
        ("model filter", log.count("filter: the model"),
         "the learned selector said no"),
        ("combo floor", len(re.findall(
            r"skip\s+\S+\s+\S+USD.*-- scored [0-9.]+ of 100", log)),
         "scored under --min-entry on the combo path"),
    ]

    plans = len(re.findall(r"PLAN  ", log))
    fills = len(re.findall(r"FILL  |LIVE OPEN", log))
    # LATE-SKIP is an expiry too -- the window closed, the market fallback
    # was tried, and something refused it. Counting only DROP/EXPIRED left
    # those orders showing as "still waiting" forever.
    drops = len(re.findall(r"DROP  |EXPIRED |LATE-SKIP ", log))
    # And so is a fill the book had no room to take: the price came, the order
    # was there, and the book was full. Left out, it was another order that
    # waited forever.
    missed = len(re.findall(r"MISSED |fill missed ", log))
    # A fill refused because the coin is already held was silent until
    # 2026-09-06; it is a real decision and is counted like the rest.
    fill_skipped = len(re.findall(r"fill skipped \w+ \w+ -- already in ",
                                  log))
    late = log.count("LATE  ")

    # A candidate that scores well and is then refused for having nowhere to
    # put it is both a pass and a refusal. The gates that come AFTER the score
    # have to be subtracted or every one of them looks like a candidate that
    # vanished -- which is what this tool reported three times in one night.
    unexplained = passed - plans - skipped

    return {
        "offered": offered,
        "stale": len(re.findall(r"stale \w+ \w+ bar", log)),
        "absorbed": log.count("absorbed as backlog"),
        "dup": log.count("already acted on"),
        "rejected": rejected, "passed": passed, "skipped": skipped,
        "judged": rejected + passed,
        "gates": gates, "refused": sum(x for _, x, _ in gates),
        "plans": plans, "fills": fills, "drops": drops, "missed": missed,
        "fill_skipped": fill_skipped,
        "late": late, "unexplained": unexplained,
    }


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("hours", nargs="?", type=float, default=24.0)
    ap.add_argument("--unit", default="tbt-paper",
                    help="systemd unit whose journal this funnel counts "
                         "(tbt-beast for the beast)")
    ap.add_argument("--book", default="", metavar="PATH",
                    help="state file for the stage-5 summary "
                         "(default data/paper.json)")
    a = ap.parse_args()
    global UNIT, BOOK
    UNIT = a.unit
    if a.book:
        BOOK = Path(a.book).expanduser().resolve()
    hours = a.hours
    # The funnel is counted from the last restart, not across the window.
    #
    # Every deploy changes what the log says: a gate that did not exist logs
    # nothing, and a line that was reworded stops matching. Counting across a
    # code change subtracts one version's rejections from another version's
    # candidates, and the first run of this tool reported two candidates
    # "vanished" that had simply been logged by yesterday's wording.
    started = since_restart()
    log = journal_since(started) if started else journal(hours)
    slog = scout_log(hours)

    when = ""
    if started:
        parts = started.split()
        # systemd writes "Fri 2026-09-04 20:39:55 UTC"
        when = " ".join(parts[1:3]) if len(parts) >= 3 else started
    print("\nthe funnel, since the book last started"
          + (f" ({when})" if when else f", last {hours:g}h") + "\n")

    # ---- stage 1: what the scanner made eligible -------------------------
    try:
        watch = json.loads((DATA / "watchlist.json").read_text())
    except Exception:
        watch = []
    swept = [int(m) for m in re.findall(r"swept (\d+) coins", slog)]
    carrying = [int(m) for m in re.findall(r"(\d+) carrying a plan", slog)]
    print(f"  universe                  {len(watch):>6}  coins the scanner "
          f"passed as tradeable")
    if swept:
        print(f"  sweeps                    {len(swept):>6}  "
              f"{sum(swept)} coin-visits, {sum(carrying)} carried something")

    # ---- the stages, in the order a candidate meets them -----------------
    #
    # Each stage's denominator is what actually reached it, not the total.
    # Mixing those was the first version of this tool reporting that 584% of
    # candidates were refused for being stale.
    n = count(log)

    print()
    print(f"  offered to the book       {n['offered']:>6}  shapes the scout "
          f"and the windows produced")
    for name, n_, why in (
            ("stale bar", n["stale"], "older than its window"),
            ("absorbed as backlog", n["absorbed"], "a fresh chart's history"),
            ("offered twice", n["dup"], "already acted on")):
        if n_:
            bar(name, n_, max(n["offered"], 1), why)

    print()
    print(f"  judged                    {n['judged']:>6}  candidates that "
          f"reached the gates")
    # The gates must account for every refusal, or one of them is unlogged.
    accounted = n["rejected"] + n["skipped"]
    if n["refused"] != accounted:
        print(f"  (note: {accounted} refusals logged, {n['refused']} "
              f"attributed to a named gate)")
    for name, n_, why in n["gates"]:
        if n_:
            bar(name, n_, max(n["judged"], 1), why)
    if not n["refused"]:
        print("  (nothing was refused at the gates)")

    # ---- stage 4: plans, fills, trades ------------------------------------
    print()
    print(f"  plans rested              {n['plans']:>6}")
    print(f"    filled                  {n['fills']:>6}")
    print(f"    still waiting           "
          f"{max(0, n['plans'] - n['fills'] - n['drops'] - n['missed'] - n['late'] - n['fill_skipped']):>6}")
    print(f"    dropped unfilled        {n['drops']:>6}  "
          f"price never came back -- a miss, not a loss")
    if n["fill_skipped"]:
        print(f"    already in the coin     {n['fill_skipped']:>6}  the price "
              f"came and the coin was held -- silent until 2026-09-06")
    if n["missed"]:
        print(f"    filled with no room     {n['missed']:>6}  the price came "
              f"and the book was full")
    if n["late"]:
        print(f"    taken late at market    {n['late']:>6}")

    # ---- stage 5: what the book holds -------------------------------------
    try:
        b = read_book(BOOK)
    except Exception:
        b = {}
    trades = b.get("trades", [])
    closed = [t for t in trades if t.get("closed")]
    won = [t for t in closed if (t.get("pnl") or 0) > 0]
    print()
    print(f"  positions opened          {len(trades):>6}")
    print(f"    closed                  {len(closed):>6}"
          + (f"   won {len(won)} ({100*len(won)/len(closed):.0f}%)"
             if closed else ""))
    print(f"    open now                "
          f"{len([t for t in trades if not t.get('closed')]):>6}")
    print(f"  equity                    "
          f"${b.get('equity', 0):>9,.2f}")

    # ---- the only thing that is actually a fault --------------------------
    print()
    faults = []
    if n["unexplained"] > 0:
        faults.append(f"{n['unexplained']} candidate(s) passed every gate and "
                      f"still did not become a plan -- they vanished between "
                      f"the score and the order")
    # Health is about the version running now, not about a fault that was
    # fixed hours ago. Counting the whole window made this shout about 379
    # failures that had all happened on code already replaced.
    now_log = log
    if now_log.count("poll failed"):
        faults.append(f"{now_log.count('poll failed')} polls have failed since "
                      f"the last restart -- each one skipped every candidate "
                      f"in it")
    if now_log.count("FROZEN"):
        faults.append("a chart is frozen -- everything on it is invisible")
    # A plan that expired while price sat AT the level is a fill that was
    # missed, not a level that was never reached.
    if faults:
        print("  BLOCKED:")
        for f in faults:
            print(f"    - {f}")
        return 1
    print("  nothing was lost: every candidate either became a plan or was "
          "refused for a reason it can name")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
