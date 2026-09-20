#!/usr/bin/env python3
"""
Risk arithmetic over the recorded trades -- read-only.

`data/dataset/trades.jsonl` holds every trade the REAL book opened during
the dataset replay (123 of them replayed from the recorded signals, plus
the branch scenarios). This tool walks that record like an account would:

  - the equity path if every trade had run on one $100 wallet in order
  - the deepest drawdown, and where it was
  - how the stops cluster: the longest losing streak, and how often 2, 3
    and 4 stops land in a row
  - the PnL distribution: expectancy per trade, average win, average loss
  - the same numbers split by direction, agents and the indicator's tier

Nothing here is a forecast. It is the arithmetic of what already happened,
which is the only honest basis for judging the 8-a-day budget.

    python3 tools/riskanalysis.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

BOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT))

TRADES = BOT / "data" / "dataset" / "trades.jsonl"


def load(path: Path | None = None) -> list[dict]:
    try:
        return [json.loads(l) for l in
                (path or TRADES).read_text().splitlines() if l.strip()]
    except FileNotFoundError:
        print(f"no trades at {path or TRADES} -- run "
              f"dataset/make_dataset.py first")
        raise SystemExit(1)


def equity_path(trades: list[dict], start: float = 100.0) -> list[float]:
    """One wallet walking the trades in order.

    Every trade in the record was sized at 50% of a $100 wallet, so its
    dollar PnL is also its percentage return on that wallet. The live book
    sizes half the CURRENT equity, so the honest compounding path scales
    each trade with the equity standing when it opened.
    """
    out = [start]
    for t in trades:
        ret = float(t.get("pnl") or 0) / 100.0
        out.append(out[-1] * (1 + ret))
    return out


def naive_path(trades: list[dict], start: float = 100.0) -> list[float]:
    """The same trades summed flat, as if the wallet were topped back up
    to $100 between every trade -- for comparison only."""
    out = [start]
    for t in trades:
        out.append(out[-1] + float(t.get("pnl") or 0))
    return out


def max_drawdown(path: list[float]) -> tuple[float, int, int]:
    """(deepest drawdown as a fraction, its peak index, its trough index)."""
    peak = path[0]
    peak_i = worst = 0
    worst_i = trough_i = 0
    for i, v in enumerate(path):
        if v > peak:
            peak, peak_i = v, i
        dd = (peak - v) / peak if peak else 0.0
        if dd > worst:
            worst, worst_i, trough_i = dd, peak_i, i
    return worst, worst_i, trough_i


def streaks(reasons: list[str]) -> dict:
    """How stops cluster: longest losing run and the 2/3/4-run counts."""
    losses = [1 if r == "stop" else 0 for r in reasons]
    longest = cur = 0
    runs = Counter()
    for x in losses:
        cur = cur + 1 if x else 0
        longest = max(longest, cur)
        if x and cur >= 2:
            runs[cur] += 1
    out = {"longest": longest, "of": len(losses)}
    for n in (2, 3, 4):
        out[f"runs_of_{n}"] = runs[n]
    return out


def splits(trades: list[dict]) -> dict:
    out = {}
    for name, key in (("side", "side"), ("agents", "agents"),
                      ("tier", "tier"), ("direction_taken",
                                         "counter")):
        got = {}
        for k, rows in group_by(trades, key).items():
            pnls = [float(t.get("pnl") or 0) for t in rows]
            got[str(k)] = {
                "n": len(rows),
                "wins": sum(1 for p in pnls if p > 0),
                "sum_pnl": round(sum(pnls), 2),
                "per_trade": round(sum(pnls) / len(pnls), 2) if pnls else 0,
            }
        out[name] = got
    return out


def group_by(rows: list[dict], key: str) -> dict:
    out: dict = {}
    for r in rows:
        out.setdefault(r.get(key), []).append(r)
    return out


def report(trades: list[dict]) -> str:
    closed = [t for t in trades if t.get("closed")]
    pnls = [float(t.get("pnl") or 0) for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    path = equity_path(closed)
    flat = naive_path(closed)
    dd, peak_i, trough_i = max_drawdown(path)
    st = streaks([t.get("reason") or "" for t in closed])
    # How many of the stops had been far enough in front that the live
    # book's --ct-exit 3.5 (a counter print at +3.5%) could have saved them
    # -- the replay had no counter prints to act on, and must say so.
    stopped = [t for t in closed if t.get("reason") == "stop"]
    saved_candidates = sum(
        1 for t in stopped if float(t.get("path_best_pct") or 0) >= 3.5)
    lines = [
        f"{len(trades)} trades in the record, {len(closed)} closed",
        f"compounding equity on one wallet: ${path[-1]:.2f} from $100.00 "
        f"(flat sum: ${flat[-1]:+.2f})",
        f"deepest drawdown: {dd*100:.1f}% "
        f"(from trade {peak_i} to trade {trough_i})",
        f"expectancy per closed trade: "
        f"{sum(pnls)/max(len(pnls),1):+.2f}",
        f"wins {len(wins)} / losses {len(losses)} "
        f"({100*len(wins)/max(len(pnls),1):.0f}% hit rate)",
        f"average win {sum(wins)/max(len(wins),1):+.2f}, "
        f"average loss {sum(losses)/max(len(losses),1):+.2f}",
        f"longest losing streak: {st['longest']} stops in a row",
        f"2/3/4-stop runs observed: {st['runs_of_2']} / "
        f"{st['runs_of_3']} / {st['runs_of_4']}",
        f"of {len(stopped)} stops, {saved_candidates} had been +3.5% or "
        f"better -- ct-exit candidates the replay could not act on",
    ]
    return "\n".join(lines)


def main() -> int:
    trades = load()
    real = [t for t in trades if t.get("provenance") in
            ("replay", "synthetic-prefix")]
    print("\n  the whole record (replay + branch scenarios)\n")
    print("\n".join("  " + l for l in report(trades).splitlines()))
    print("\n  the real replayed trades only\n")
    print("\n".join("  " + l for l in report(real).splitlines()))
    print("\n  by side / agents / tier / counter (real replayed)\n")
    for name, rows in splits(real).items():
        print(f"  {name}")
        for k, v in rows.items():
            print(f"    {k:<8} n={v['n']:<4} wins={v['wins']:<3} "
                  f"sum {v['sum_pnl']:>+9.2f}  per trade "
                  f"{v['per_trade']:>+7.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
