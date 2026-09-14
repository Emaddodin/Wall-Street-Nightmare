#!/usr/bin/env python3
"""Spending eight trades a day on the best eight the day offers.

A cap refuses the ninth signal whatever it is, and the ninth is as likely to
be the best one as the first. A floor refuses everything under a fixed score,
which on a rich day leaves the budget unspent and on a poor day spends it all
before noon. Neither is what "take the best eight" means.

What that means is an old problem: choices arrive one at a time, you cannot
call one back, and you must commit a fixed number of them before the day ends.
The answer is not a number, it is a bar that moves.

Keep every score seen in the last day. From it, two things are known -- how
often signals arrive, and how good they usually are. Then:

    slots left      = the budget minus what today has already spent
    signals to come = the observed rate times the hours remaining
    affordable      = slots left / signals to come

If eight slots remain and eighty signals are coming, we can afford the top
tenth, so the bar is the ninetieth percentile of what we have seen. If two
slots remain with an hour left and three signals expected, we can afford two
of three and the bar drops to the thirty-third percentile. The bar rises when
the day is generous and falls when it is running out, and the budget is spent
on the best eight rather than the first eight or none at all.

A floor sits under all of it, because "the best available" is not a reason to
take something bad when the day has simply been poor.
"""
from __future__ import annotations

import time

# Kept for a day: the distribution has to describe the market we are in now,
# not last week's.
WINDOW_S = 24 * 3600

# How much of the day has to be seen before a slot is spent. Below this there
# is no distribution to be the best of, and taking the first arrival is not
# choosing.
MIN_SAMPLE = 8

# ...unless the day is nearly over. A budget that ends untouched because the
# book was still waiting for a sample is its own kind of failure.
LATE_H = 6.0


# Where the trading day begins, in hours past midnight UTC. The budget is a
# day's budget and the day is the operator's, not the calendar's: theirs runs
# from nine in the evening in Tehran, which is 17:30 UTC.
DAY_OFFSET_H = 0.0


def day_start(now: float | None = None, offset_h: float | None = None) -> float:
    """The start of the trading day containing `now`."""
    now = time.time() if now is None else now
    off = (DAY_OFFSET_H if offset_h is None else offset_h) * 3600.0
    start = now - ((now - off) % 86400)
    return start


def spent(trades, now: float | None = None) -> int:
    """Trades opened since midnight. Restarts must not refill the budget."""
    now = time.time() if now is None else now
    start = day_start(now)
    return sum(1 for t in trades
               if float((t.get("opened") if isinstance(t, dict)
                         else getattr(t, "opened", 0)) or 0) >= start)


def seen(scores, now: float | None = None) -> list:
    """Scores from the last day, oldest first."""
    now = time.time() if now is None else now
    out = [s for s in (scores or [])
           if now - float(s.get("at", 0)) <= WINDOW_S]
    out.sort(key=lambda s: s["at"])
    return out


def quantile(vals: list, q: float) -> float:
    """The value at `q` of a sorted sample, without numpy."""
    if not vals:
        return 0.0
    v = sorted(vals)
    if len(v) == 1:
        return v[0]
    i = q * (len(v) - 1)
    lo = int(i)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (i - lo)


def bar(scores, trades, budget: int, floor: float,
        now: float | None = None) -> tuple[float | None, str]:
    """The score a signal must clear right now, and why.

    None means the day's budget is spent and nothing should be taken at all.
    """
    now = time.time() if now is None else now
    used = spent(trades, now)
    left = budget - used
    if left <= 0:
        return None, f"{used} of {budget} taken today"

    hist = seen(scores, now)
    hours_left = max(0.25, (day_start(now) + 86400 - now) / 3600.0)

    # Nothing is spent before the day has been looked at.
    #
    # "The best eight of the day" is not something that can be said about the
    # first signal seen. The first trade after a reset was taken on a score of
    # exactly the floor with zero history behind it, which is not choosing --
    # it is taking whatever arrived first. So until there is a sample to judge
    # against, the book waits.
    #
    # It cannot wait forever, though: a quiet day would end with the budget
    # untouched, which is its own kind of failure. Once the day is nearly over
    # the requirement is dropped and the floor decides, because a floor is
    # still better than nothing at all.
    if len(hist) < MIN_SAMPLE:
        if hours_left > LATE_H:
            return None, (f"{len(hist)} of {MIN_SAMPLE} scores seen -- too "
                          f"early in the day to say which are the best "
                          f"{budget}, waiting")
        return floor, (f"{left} of {budget} left with {hours_left:.1f}h to "
                       f"go and only {len(hist)} scores seen -- taking the "
                       f"floor rather than ending the day unspent")
    span_h = max(0.5, (hist[-1]["at"] - hist[0]["at"]) / 3600.0)
    rate = len(hist) / span_h
    coming = max(float(left), rate * hours_left)
    affordable = min(1.0, left / coming)
    q = 1.0 - affordable
    want = quantile([s["score"] for s in hist], q)
    return max(floor, want), (
        f"{left} of {budget} left, {hours_left:.1f}h to go, "
        f"{rate:.1f} signals/h -- can afford the top {affordable * 100:.0f}%, "
        f"which is {want:.0f} of 100")


def remember(scores, sym: str, score: float, now: float | None = None) -> list:
    """Add a score and drop what has aged out."""
    now = time.time() if now is None else now
    out = list(scores or [])
    out.append({"at": now, "sym": sym, "score": float(score)})
    return [s for s in out if now - float(s.get("at", 0)) <= WINDOW_S]
