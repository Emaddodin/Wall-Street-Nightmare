#!/usr/bin/env python3
"""
The edge sweep: what the recorded market actually pays, sliced every way
that could matter -- and what it would pay an orchestra.

The operator's thesis is that the market has an inefficiency on heavy,
high-ATR coins, where a coordinated system can take eight winning setups
a day. The recorded signals and their real post-paths can answer that
directly: the same 10,939 real prints, the same 5% target and 1.25% stop,
the same spanning-bar-is-a-stop convention, sliced by ATR, agents, tier,
side and the live gate chain -- plus the two questions an orchestra needs:

  1. does the top of each day (the best 8 by the book's own entry score)
     beat the population, and by how much?
  2. is there enough supply -- how many gate-passing candidates, and how
     many actual winners, does a real day offer?

Then the risk half: Kelly at the measured hit rates, and a seeded ruin
simulation for fixed per-trade risk, because a strategy is the edge times
the survival.

Nothing here changes the live setup. Read-only over recorded data.

    python3 dataset/sweep.py
"""
from __future__ import annotations

import json
import random
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import papertrade as P  # noqa: E402
import pace  # noqa: E402
from dataset import engine, sources  # noqa: E402
from dataset.make_dataset import measures_for  # noqa: E402

TP, SL = 5.0, 1.25
WIN_WALLET = 122.0      # +5% at 50x on half the wallet, minus fees
LOSE_WALLET = 34.25     # -1.25% at 50x on half the wallet, plus fees
B = WIN_WALLET / LOSE_WALLET     # the payoff odds, 3.56


def outcome(side: str, entry: float, bars) -> str:
    reason, _px, _n, _best = engine.walk_path(side, entry, bars, TP, SL)
    return reason


def kelly(p: float, b: float = B) -> float:
    """Fraction of the wallet to RISK per trade at hit rate p."""
    if p <= 1 / (1 + b):
        return 0.0
    return (b * p - (1 - p)) / b


def ruin_sim(p: float, risk: float, trades_per_day: int = 8,
             days: int = 30, trials: int = 4000, seed: int = 7):
    """Final equity multiples and ruin rate at fixed per-trade risk."""
    rng = random.Random(seed)
    finals = []
    ruined = 0
    for _ in range(trials):
        eq = 1.0
        for _ in range(trades_per_day * days):
            if eq <= 0.01:
                ruined += 1
                break
            if rng.random() < p:
                eq *= 1 + risk * B           # a win pays the measured odds
            else:
                eq *= 1 - risk               # a loss costs 1R
        finals.append(eq)
    finals.sort()
    return {
        "median": finals[len(finals) // 2],
        "p90": finals[int(len(finals) * 0.9)],
        "ruin_lt_1pct": ruined / trials,
    }


def main() -> int:
    rows = sources.joined()
    print(f"  {len(rows)} recorded signals with real paths, at "
          f"+{TP:g}% / -{SL:g}%\n")

    tmpf = Path(tempfile.mkdtemp(prefix="tbt-sweep-")) / "atr_measures.json"
    recs = []
    for sig in rows:
        sym, t, side = sig["sym"], int(sig["t"]), sig["side"]
        meas = measures_for(sym, t, sig)
        atr = meas.get("atr")
        trend = meas.get("trend")
        entry = float((sig.get("outcome") or {}).get("entry") or
                      sig.get("px") or 0)
        if not entry:
            continue
        bars = sources.path_bars(sig.get("outcome") or {}, side, entry)
        res = outcome(side, entry, bars)
        # The book's own entry score, over the same measures file pattern.
        tmpf.write_text(json.dumps({sym: meas}))
        P._ATR.update({"t": 0.0, "by": {}, "trend": {}, "shape": {},
                       "stamp": 0})
        with mock.patch.object(P, "ATR_M", tmpf):
            pts, _why = P.entry_score(sym, side,
                                      int(sig.get("agents") or 0))
        recs.append({"sym": sym, "t": t, "side": side, "atr": atr,
                     "trend": trend, "pts": pts,
                     "agents": sig.get("agents"),
                     "tier": sig.get("tier"), "counter": sig.get("counter"),
                     "res": res})
    import shutil
    shutil.rmtree(tmpf.parent, ignore_errors=True)

    def hit(sel):
        if not sel:
            return None
        w = sum(1 for r in sel if r["res"] == "target")
        return w, len(sel), 100.0 * w / len(sel)

    def line(label, sel):
        got = hit(sel)
        if got is None:
            print(f"    {label:<44} n=0")
            return
        w, n, pct = got
        exp = (pct / 100 * WIN_WALLET - (1 - pct / 100) * LOSE_WALLET)
        print(f"    {label:<44} n={n:<5} hit {pct:>5.1f}%   "
              f"expectancy {exp:>+7.2f}/trade")

    print("  1) the population, sliced (the thesis's first test)")
    for lo, hi in ((0, 1), (1, 2), (2, 2.5), (2.5, 4), (4, 5.5),
                   (5.5, 99)):
        sel = [r for r in recs if r["atr"] is not None
               and lo <= r["atr"] < hi]
        line(f"ATR {lo}-{hi}%", sel)
    print()
    for a in (3, 4, 5):
        line(f"agents {a}", [r for r in recs if r["agents"] == a])
    for tv in (1, 2, 3):
        line(f"tier {tv}", [r for r in recs if r["tier"] == tv])
    line("counter-trend prints", [r for r in recs if r["counter"]])
    line("with the 2h trend (measured)", [r for r in recs
                                          if r["trend"] is not None
                                          and r["trend"] * (1 if r["side"]
                                                            == "BUY" else -1)
                                          >= 0])
    print()

    # The live gate chain, approximated offline: the score floor, the ATR
    # floor and the agent floor. The pace bar varies by day and is applied
    # below, in the daily budget test.
    gated = [r for r in recs
             if r["agents"] >= 3 and r["pts"] >= 40
             and (r["atr"] is None or r["atr"] >= 2.5)]
    print(f"  2) the live gates select {len(gated)} of {len(recs)}")
    line("gated (agents>=3, score>=40, ATR>=2.5)", gated)
    line("gated AND ATR>=4", [r for r in gated
                              if r["atr"] is not None and r["atr"] >= 4])
    line("gated AND ATR>=5.5", [r for r in gated
                                if r["atr"] is not None and r["atr"] >= 5.5])
    line("gated AND agents>=4", [r for r in gated if r["agents"] >= 4])
    line("gated AND agents>=4 AND ATR>=4",
         [r for r in gated if r["agents"] >= 4
          and r["atr"] is not None and r["atr"] >= 4])
    print()

    # The daily budget's own question: does the best 8 of each day beat the
    # population? Group the gated candidates by trading day and take the
    # top 8 by the book's score.
    by_day = defaultdict(list)
    for r in gated:
        by_day[pace.day_start(r["t"], offset_h=17.5)].append(r)
    days = sorted(by_day)
    top8 = []
    day_winners = []
    for d in days:
        ranked = sorted(by_day[d], key=lambda r: -r["pts"])
        top8 += ranked[:8]
        day_winners.append(sum(1 for r in by_day[d] if r["res"] == "target"))
    print(f"  3) the budget: {len(days)} trading days of recorded data, "
          f"{len(gated)} candidates")
    line("the best 8 per day by score", top8)
    line("everything else the day offered",
         [r for r in gated if r not in top8])
    supply = Counter(len(by_day[d]) for d in days)
    print(f"      candidates per day: min {min(supply)} median "
          f"{sorted(len(by_day[d]) for d in days)[len(days)//2]} max "
          f"{max(supply)}")
    win_supply = Counter(w for w in day_winners)
    print(f"      winners per day among them: min {min(win_supply)} median "
          f"{sorted(day_winners)[len(days)//2]} max {max(win_supply)} -- "
          f"{sum(1 for w in day_winners if w >= 8)} day(s) held 8 winners")
    print()

    # The risk half: Kelly at the measured rates, then survival.
    print("  4) risk per trade: Kelly and survival (4R wins, 1R losses)")
    for p in (0.08, 0.15, 0.245, 0.30, 0.39):
        k = kelly(p)
        print(f"      hit {p*100:>4.0f}%   Kelly risk {k*100:>5.1f}% of the "
              f"wallet per trade")
    print()
    for p, label in ((0.08, "the full population"), (0.245, "the study"),
                     (0.39, "the ATR>5.5% slice")):
        for risk in (0.02, 0.05, 0.10, 0.25):
            got = ruin_sim(p, risk)
            print(f"      p={p*100:>4.0f}% risk {risk*100:>4.0f}%/trade: "
                  f"median x{got['median']:>8.2f}  p90 x{got['p90']:>8.2f}  "
                  f"ruin {got['ruin_lt_1pct']*100:>5.1f}%")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
