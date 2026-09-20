#!/usr/bin/env python3
"""
The pullback question, measured: the trade ran toward the target, the
market reversed, and the whole thing stopped out. Which protection rule
would have stood against that, at what cost to the winners?

Simulates the operator's 50% rule over every recorded signal path --
stop = 1 x the coin's own ATR, leverage = min(cap, 1/(stop+maint)),
target = 50/lev % of price -- and then applies each protection variant:

  baseline        target/stop only (what the book runs now)
  be1             stop moves to entry once +1.0%
  be_atr          stop moves to entry once +1 x ATR
  be_atr_trail    the above, then the stop trails 0.75 x ATR behind
                  the best price
  pb60            once past 60% of the target, a pullback of
                  0.75 x ATR from the best closes at market

The pain metric: among the baseline's stops, how many had been past 70%
of the target first -- and what each variant does to those trades.

Nothing here trades. Read-only over recorded data.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dataset import sources  # noqa: E402
from dataset.make_dataset import measures_for  # noqa: E402

MAINT = 0.5          # maintenance margin, as everywhere
CAP = 50.0           # the recorded base cap; the live cap varies per coin


def walk(fav, adv, tp_pct, sl_pct, be_pct=0.0, trail_pct=0.0,
         pb_frac=0.0, pb_pct=0.0):
    """One path through the protection rules.

    Returns (result, exit_pct) -- exit_pct is the price the trade left
    at: the stop LEVEL actually breached (which is entry once
    break-even has moved it), the target, the pullback exit, or the
    last price when it never closed.

    fav/adv are side-relative cumulative extremes per bar, in percent.
    Spanning-bar-is-a-stop, as everywhere: a bar touching both counts
    the stop. The protection levels update AFTER each bar closes, so a
    bar can only stop against the levels that existed when it opened.
    """
    tp = 1 + tp_pct / 100
    sl = 1 - sl_pct / 100
    best = 1.0
    last = 1.0
    for i in range(min(96, len(fav))):
        hi = 1 + fav[i] / 100
        lo = 1 - adv[i] / 100
        last = hi
        if lo <= sl:
            return "stop", sl, best
        if hi >= tp:
            return "target", tp, best
        best = max(best, hi)
        if be_pct and best >= 1 + be_pct / 100:
            sl = max(sl, 1.0)
        if trail_pct and best >= 1 + be_pct / 100:
            sl = max(sl, best * (1 - trail_pct / 100))
        if pb_frac and best >= 1 + pb_frac * (tp - 1):
            if lo <= best * (1 - pb_pct / 100):
                return "pullback", best * (1 - pb_pct / 100), best
    return "open", last, best


def pnl_frac(result, exit_px, lev):
    """The trade's PnL as a fraction of the margin (BUY-side, no fees)."""
    return lev * (exit_px - 1.0) * 100


def run() -> int:
    paths = []
    for sig in sources.joined():
        out = sig.get("outcome") or {}
        fav, adv = out.get("fav"), out.get("adv")
        if not fav or not adv or not out.get("entry"):
            continue
        meas = measures_for(sig["sym"], int(sig["t"]), sig)
        atr = meas.get("atr")
        if atr is None or atr <= 0:
            continue
        sl_pct = atr
        lev = min(CAP, 100.0 / (sl_pct + MAINT))
        tp_pct = 50.0 / lev
        paths.append((fav, adv, tp_pct, sl_pct, lev, sig["side"] == "SELL"))
    print(f"{len(paths)} recorded paths with a coin ATR\n")

    variants = {
        "baseline":     {},
        "be1":          {"be_pct": 1.0},
        "be_atr":       {"be_pct": "atr"},
        "be_atr_trail": {"be_pct": "atr", "trail_pct": 0.75, "trail_of": "atr"},
        "pb60":         {"pb_frac": 0.6, "pb_pct": 0.75, "pb_of": "atr"},
    }
    # baseline first: which paths stop, and how many had been close
    base = []
    for fav, adv, tp_pct, sl_pct, lev, sell in paths:
        f = adv if sell else fav
        a = fav if sell else adv
        got, ex, best = walk(f, a, tp_pct, sl_pct)
        base.append((got, ex, best))
    pain = sum(1 for got, ex, best, (_f, _a, tp_pct, _s, _l, _x) in
               zip([b[0] for b in base], [b[1] for b in base],
                   [b[2] for b in base], paths)
               if got == "stop" and (best - 1) * 100 >= 0.70 * tp_pct)
    results = {}
    for name, kw in variants.items():
        counts = {"target": 0, "stop": 0, "pullback": 0, "open": 0}
        total = 0.0
        saved = 0
        for (fav, adv, tp_pct, sl_pct, lev, sell), (bg, _bx, _bb) in zip(
                paths, base):
            f = adv if sell else fav
            a = fav if sell else adv
            be = kw.get("be_pct", 0.0)
            if be == "atr":
                be = sl_pct
            tr = kw.get("trail_pct", 0.0)
            if kw.get("trail_of") == "atr":
                tr = 0.75 * sl_pct
            pb = kw.get("pb_pct", 0.0)
            if kw.get("pb_of") == "atr":
                pb = 0.75 * sl_pct
            got, ex, _best = walk(f, a, tp_pct, sl_pct, be_pct=be,
                                   trail_pct=tr, pb_frac=kw.get("pb_frac", 0.0),
                                   pb_pct=pb)
            counts[got] += 1
            total += pnl_frac(got, ex, lev)
            if name != "baseline" and bg == "stop" and got != "stop":
                saved += 1
        n = sum(counts.values())
        results[name] = (counts, total / n, saved)
        print(f"{name:<13} n={n:6d}  target {counts['target']/n*100:5.1f}%  "
              f"stop {counts['stop']/n*100:5.1f}%  "
              f"prot {counts['pullback']/n*100:5.1f}%  "
              f"open {counts['open']/n*100:5.1f}%  "
              f"expectancy {total/n:+6.2f}% of margin")
    print(f"\nbaseline pain: {pain} stops ({pain/sum(results['baseline'][0].values())*100:.1f}% of all trades) "
          f"had been past 70% of the target first")
    for name in ("be1", "be_atr", "be_atr_trail", "pb60"):
        counts, exp, saved = results[name]
        print(f"{name:<13} turns {saved} of the baseline's stops into "
              f"non-stops; expectancy {exp:+6.2f}% of margin")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
