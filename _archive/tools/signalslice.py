#!/usr/bin/env python3
"""Slice the saved signals every way that could change the funnel.

signalcheck.py walks the charts, which takes ten minutes and needs the guard
out of the way. This reads what it saved, so every question after the first
one is free.

    python3 tools/signalslice.py [data/signals_study.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

FEE = 12 / 1e4
TIER = {3: "PEERLESS", 2: "EXCELLENT", 1: "GOOD"}


def outcome(bars, t0, side, target_pct, stop_pct):
    """Entry at the next bar's open. A bar spanning both counts as a stop."""
    keys = [k for k in sorted(bars) if k > t0]
    if not keys:
        return None, None
    entry = bars[keys[0]][0]
    tp = entry * (1 + target_pct / 100) if side == "BUY" \
        else entry * (1 - target_pct / 100)
    sl = entry * (1 - stop_pct / 100) if side == "BUY" \
        else entry * (1 + stop_pct / 100)
    for n, k in enumerate(keys):
        o, h, l, c = bars[k]
        if (l <= sl) if side == "BUY" else (h >= sl):
            return "stop", n
        if (h >= tp) if side == "BUY" else (l <= tp):
            return "target", n
    return "open", len(keys)


def run(sigs, target, stop, lev=50):
    w = l = o = 0
    ret = 0.0
    bars_to = []
    for s in sigs:
        r, n = outcome(s["bars"], s["t"], s["side"], target, stop)
        if r == "target":
            w += 1
            ret += (target / 100 - FEE) * lev
            bars_to.append(n)
        elif r == "stop":
            l += 1
            ret -= (stop / 100 + FEE) * lev
        elif r == "open":
            o += 1
    n_ = w + l
    return dict(n=len(sigs), w=w, l=l, o=o,
                exp=(ret / n_ * 100 if n_ else 0.0),
                med=(sorted(bars_to)[len(bars_to) // 2] if bars_to else None),
                fast=sum(1 for b in bars_to if b <= 3))


def line(label, r, width=30):
    if r["n"] < 6:
        print(f"    {label:<{width}} n={r['n']} (too few)")
        return
    med = f"{r['med']}" if r["med"] is not None else "-"
    print(f"    {label:<{width}} n={r['n']:<4} target {r['w']:>3} "
          f"({100*r['w']/r['n']:>4.1f}%)   exp {r['exp']:>+7.1f}%   "
          f"median {med} bars   {r['fast']} within 3 bars")


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1
                else "data/signals_study.json")
    raw = json.loads(path.read_text())
    sigs = [{**s, "bars": {int(k): tuple(v) for k, v in s["bars"].items()}}
            for s in raw]
    print(f"\n  {len(sigs)} signals, "
          f"{len({s['sym'] for s in sigs})} coins\n")

    print("  1) target and stop together, at 50x")
    print(f"    {'':30}{'':4}")
    for target in (3, 5, 7, 10):
        for stop in (1.0, 1.5, 2.0, 3.0):
            line(f"target {target}%  stop {stop}%", run(sigs, target, stop))
        print()

    best_t, best_s = 10, 2.0
    print(f"  2) the indicator's own tier  (target {best_t}% stop {best_s}%)")
    for tv in (3, 2, 1):
        line(TIER[tv], run([s for s in sigs if s["tier"] == tv],
                           best_t, best_s))

    print(f"\n  3) the coin's ATR  (target {best_t}% stop {best_s}%)")
    for lo, hi in ((0, 2.5), (2.5, 4.0), (4.0, 5.5), (5.5, 99)):
        line(f"ATR {lo}-{hi}%",
             run([s for s in sigs if lo <= (s.get("atr") or 0) < hi],
                 best_t, best_s))

    print(f"\n  4) the indicator's confluence score  "
          f"(target {best_t}% stop {best_s}%)")
    for lo, hi in ((0, 40), (40, 55), (55, 70), (70, 101)):
        line(f"score {lo}-{hi}",
             run([s for s in sigs if lo <= (s.get("score") or 0) < hi],
                 best_t, best_s))

    print(f"\n  5) the sniper's own agents  (target {best_t}% stop {best_s}%)")
    for lo, hi in ((0, 2), (2, 3), (3, 4), (4, 99)):
        sel = [s for s in sigs
               if lo <= ((s.get("buy_vote") if s["side"] == "BUY"
                          else s.get("sell_vote")) or 0) < hi]
        line(f"agents {lo}-{hi}", run(sel, best_t, best_s))

    print(f"\n  6) side  (target {best_t}% stop {best_s}%)")
    for sd in ("BUY", "SELL"):
        line(sd, run([s for s in sigs if s["side"] == sd], best_t, best_s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
