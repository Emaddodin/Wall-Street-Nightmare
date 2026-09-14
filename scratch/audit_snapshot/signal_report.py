#!/usr/bin/env python3
"""
What the recorded signals say so far.

Reads data/signals.jsonl and data/outcomes.jsonl and answers the only question
that matters before real money: does this signal know anything the market does
not, and are we sure enough to bet on it.

The test is a symmetric barrier. Put the target and the stop the same distance
from the entry, scaled to the coin's own ATR so every coin is asked the same
question, and see which one price reaches first. An entry that carries no
information lands on 50%. Everything above that is edge; the confidence band
says whether we are allowed to believe it yet.

  tbt-signals              everything on file
  tbt-signals --min 40     only subsets with at least this many resolved
"""
from __future__ import annotations

import argparse
import json
import math
import os

BOT = os.path.dirname(os.path.abspath(__file__))


def load() -> list[dict]:
    # Tesla was captured from the chart after the first signals were already on
    # file, so the older ones carry it in a companion keyed by the same id.
    tesla = {}
    p = f"{BOT}/data/tesla.jsonl"
    if os.path.exists(p):
        for line in open(p):
            try:
                d = json.loads(line)
            except Exception:
                continue
            tesla[d["id"]] = d.get("tesla")
    out = {}
    p = f"{BOT}/data/outcomes.jsonl"
    if os.path.exists(p):
        for line in open(p):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if not d.get("skipped"):
                out[d["id"]] = d
    rows = []
    p = f"{BOT}/data/signals.jsonl"
    if os.path.exists(p):
        for line in open(p):
            try:
                s = json.loads(line)
            except Exception:
                continue
            o = out.get(s.get("id"))
            if o and o.get("atr"):
                r = {**s, **o}
                if r.get("tesla") is None:
                    r["tesla"] = tesla.get(s.get("id"))
                rows.append(r)
    return rows


def race(rows, k, mins=60):
    """How often our side reaches +k x ATR before -k x ATR."""
    w = l = 0
    for d in rows:
        a = d["atr"]
        fav, adv = d["fav"], d["adv"]
        for i in range(min(mins, len(fav))):
            if adv[i] >= k * a:
                l += 1
                break
            if fav[i] >= k * a:
                w += 1
                break
    return w, l


def signal_rate() -> float:
    """Signals per day actually being recorded, scored or not."""
    ts = []
    p = f"{BOT}/data/signals.jsonl"
    if os.path.exists(p):
        for line in open(p):
            try:
                ts.append(json.loads(line)["t"])
            except Exception:
                continue
    if len(ts) < 2:
        return 0.0
    days = (max(ts) - min(ts)) / 86400
    return len(ts) / days if days > 0.2 else 0.0


def band(w, n):
    if not n:
        return 0.0, 0.0, 0.0
    p = w / n
    se = math.sqrt(p * (1 - p) / n)
    return 100 * p, 100 * (p - 1.96 * se), 100 * (p + 1.96 * se)


def need_for(edge_pts: float) -> int:
    """Roughly how many resolved trades to tell 50+edge from 50."""
    return int(math.ceil(2 * (1.96 ** 2) * 0.25 / ((edge_pts / 100) ** 2)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=30,
                    help="hide subsets with fewer resolved than this")
    ap.add_argument("-k", type=float, default=1.0, help="barrier, in ATR")
    a = ap.parse_args()

    rows = load()
    if not rows:
        print("\n  Nothing scored yet. The recorder needs a couple of hours "
              "after a signal\n  prints before it can say what happened.\n")
        return 0

    span = (max(d["t"] for d in rows) - min(d["t"] for d in rows)) / 86400
    print(f"\n  {len(rows)} signals with a known outcome, over {span:.1f} days.")
    print(f"  Barrier {a.k} x ATR each way, one hour to resolve.\n")

    subs = [
        ("everything", lambda d: True),
        ("combo (not counter-trend)", lambda d: not d["counter"]),
        ("counter-trend", lambda d: d["counter"]),
        ("4 agents", lambda d: d["agents"] >= 4),
        ("3 agents", lambda d: d["agents"] == 3),
        ("tier 3", lambda d: d["tier"] == 3),
        ("tier 1-2", lambda d: d["tier"] in (1, 2)),
        ("tesla wired", lambda d: d["wired"]),
        ("long", lambda d: d["side"] == "BUY"),
        ("short", lambda d: d["side"] == "SELL"),
        ("atr >= 1%", lambda d: d["atr"] >= 1.0),
        ("atr < 1%", lambda d: d["atr"] < 1.0),
        # Tesla is the leg that cannot be recomputed anywhere, so what it was
        # doing when the label printed is worth asking about directly.
        ("tesla fired our way",
         lambda d: bool((d.get("tesla") or {}).get(
             "long" if d["side"] == "BUY" else "short"))),
        ("tesla quiet",
         lambda d: d.get("tesla") is not None and not (d["tesla"] or {}).get(
             "long" if d["side"] == "BUY" else "short")),
        ("tesla trend agrees",
         lambda d: (d.get("tesla") or {}).get("trend") is not None
         and ((d["tesla"]["trend"] > 0) == (d["side"] == "BUY"))),
    ]
    print(f"  {'subset':<28}{'resolved':>10}{'win rate':>11}{'95% band':>18}{'':>4}")
    print("  " + "-" * 72)
    shown = flagged = 0
    for name, sel in subs:
        g = [d for d in rows if sel(d)]
        w, l = race(g, a.k)
        n = w + l
        if n < a.min:
            continue
        shown += 1
        p, lo, hi = band(w, n)
        mark = "  <-- beats 50" if lo > 50 else ("  <-- loses" if hi < 50 else "")
        if mark:
            flagged += 1
        print(f"  {name:<28}{n:>10}{p:>10.1f}%"
              f"{f'{lo:.0f}% - {hi:.0f}%':>18}{mark}")

    # A 95% band is wrong one time in twenty by construction. Reading twelve of
    # them and believing the one that clears is how a losing strategy gets
    # confirmed. Say so here, where the flag is, not in a footnote.
    if flagged:
        expect = shown * 0.05
        print(f"\n  {flagged} subset(s) cleared 50%, out of {shown} tested. "
              f"Testing {shown}\n  bands at 95% throws up about {expect:.1f} by "
              f"chance alone, so a single\n  flag here is noise, not a finding. "
              f"A subset earns belief by clearing\n  again on signals recorded "
              f"AFTER you first noticed it.")

    w, l = race(rows, a.k)
    n = w + l
    p, lo, hi = band(w, n)
    print(f"\n  Where the evidence stands: {p:.1f}% on {n} resolved signals.")
    if lo > 50:
        print("  The band clears 50%. That is a real edge on this sample.")
    elif hi < 50:
        print("  The band sits under 50%. This is losing, not merely unproven.")
    else:
        print("  The band straddles 50%, so nothing is proven either way yet.")
        # Rate from every signal on file, not only the scored ones: the
        # backlog is still draining and using it here would flatter the wait.
        rate = signal_rate() or (len(rows) / max(span, 0.1))
        for e in (5, 3):
            k = need_for(e)
            if k > n:
                days = (k - n) / max(rate, 0.1)
                print(f"    to resolve a {e}-point edge: {k} signals "
                      f"-- about {days:.0f} more days at the current rate")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
