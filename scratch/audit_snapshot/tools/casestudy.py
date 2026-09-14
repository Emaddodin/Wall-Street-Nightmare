#!/usr/bin/env python3
"""The whole path of a trade, not just where it ended.

A closed trade leaves one number. That is enough to keep score and useless for
learning anything: it cannot say whether the trade was ever in front, how far
it ran before it turned, how long it sat still, or what the indicator was
saying while it happened. Two trades that both stop out for the same amount
can be completely different mistakes.

So this walks alongside every open position and writes a line a minute:

  * where price is, as a percent of the entry, and how far that is from the
    target and the stop
  * the best and worst it has been so far
  * whatever the scout last read off the indicator for that coin -- agents,
    score, tier, and any counter-trend print

Recording only. It places nothing, closes nothing, and touches no service.

    python3 tools/casestudy.py record          one sample of every open trade
    python3 tools/casestudy.py report [SYM]    the path of a trade, closed or not
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

BOT = Path(os.environ.get("TBT_BOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(BOT))

BOOK = BOT / "data" / "paper.json"
SCOUT = BOT / "data" / "scout.json"
TRACK = BOT / "data" / "casestudy.jsonl"

# A scout reading older than this says nothing about the position right now.
FRESH_MIN = 20.0


def scout_by_coin() -> dict:
    """What the scout last read off the indicator, per coin."""
    try:
        rows = json.loads(SCOUT.read_text())
    except Exception:
        return {}
    out, now = {}, time.time()
    for r in rows:
        # A "state" row is what the indicator is saying with no signal on it,
        # which is most minutes and exactly the ones worth studying. A "combo"
        # row is a live signal and wins where both exist.
        if r.get("kind") not in ("combo", "state"):
            continue
        if now - float(r.get("at", 0)) > FRESH_MIN * 60:
            continue
        sym = str(r.get("sym") or "").upper()
        if not sym:
            continue
        if sym in out and out[sym].get("kind") == "combo":
            continue
        out[sym] = r
    return out


def record() -> int:
    from exchange.bitunix import BitunixClient
    try:
        b = json.loads(BOOK.read_text())
    except Exception as e:
        print(f"the book will not parse: {str(e)[:60]}")
        return 1
    live = [t for t in b.get("trades", []) if not t.get("closed")]
    if not live:
        return 0
    cli = BitunixClient()
    marks = {}
    for x in cli.tickers():
        try:
            marks[x["symbol"]] = float(x.get("markPrice") or x["lastPrice"])
        except (KeyError, TypeError, ValueError):
            pass
    seen = scout_by_coin()
    now = time.time()
    lines = []
    for t in live:
        px = marks.get(t["sym"])
        if not px:
            continue
        sign = 1 if t["side"] == "BUY" else -1
        fav = (px / t["entry"] - 1) * 100 * sign
        s = seen.get(str(t["sym"]).upper()) or {}
        lines.append({
            "at": now, "sym": t["sym"], "side": t["side"],
            "opened": t["opened"], "age_min": (now - t["opened"]) / 60,
            "entry": t["entry"], "mark": px,
            "pct": fav,
            "to_tp": abs(t["tp"] / px - 1) * 100,
            "to_sl": abs(t["sl"] / px - 1) * 100,
            "pnl": t["notional"] * (px / t["entry"] - 1) * sign
                   - t["notional"] * 12 / 1e4,
            "best": t.get("best_pct", 0.0),
            # What the indicator was saying at this moment, if the scout had
            # walked past recently enough for the answer to mean anything.
            "ind_side": s.get("side"), "ind_agents": s.get("agents"),
            "ind_score": s.get("score"), "ind_tier": s.get("tier"),
            "ind_ct": s.get("ct"),
            "ind_kind": s.get("kind"),
            "ind_votes": s.get("votes"), "ind_plan": s.get("plan_dir"),
            "ind_htf": s.get("htf"),
        })
    if not lines:
        return 0
    with TRACK.open("a") as f:
        for ln in lines:
            f.write(json.dumps(ln) + "\n")
    return 0


def report(sym: str | None = None) -> int:
    try:
        rows = [json.loads(x) for x in TRACK.read_text().splitlines() if x]
    except Exception:
        print("nothing recorded yet")
        return 0
    try:
        b = json.loads(BOOK.read_text())
    except Exception:
        b = {}
    trades = {(t["sym"], t["opened"]): t for t in b.get("trades", [])}

    keys = sorted({(r["sym"], r["opened"]) for r in rows},
                  key=lambda k: k[1])
    if sym:
        keys = [k for k in keys if k[0].upper() == sym.upper()]
    for k in keys:
        path = [r for r in rows if (r["sym"], r["opened"]) == k]
        path.sort(key=lambda r: r["at"])
        t = trades.get(k, {})
        head = f"{path[0]['side']} {k[0]}"
        state = (f"CLOSED {t.get('reason')} ${t.get('pnl', 0):+.2f}"
                 if t.get("closed") else "OPEN")
        print(f"\n  {head}  entry {path[0]['entry']:.8g}   {state}")
        print(f"  {'min':>5} {'price':>12} {'pct':>8} {'pnl':>9} "
              f"{'to tp':>7} {'to sl':>7}   what the indicator was saying")
        for r in path:
            bits = []
            if r.get("ind_agents") is not None:
                bits.append(f"{r.get('ind_side')} {r.get('ind_agents')}ag "
                            f"score {r.get('ind_score')}")
            if r.get("ind_votes") is not None:
                bits.append(f"council {r['ind_votes']}/6")
            if r.get("ind_plan"):
                bits.append("plan " + ("long" if r["ind_plan"] == 1
                                       else "short"))
            if r.get("ind_htf") is not None and r["ind_htf"]:
                bits.append("HTF " + ("long" if r["ind_htf"] == 1
                                      else "short"))
            if r.get("ind_ct"):
                bits.append("CT " + ("BUY" if r["ind_ct"] == 1 else "SELL"))
            ind = "  ".join(bits)
            print(f"  {r['age_min']:>5.0f} {r['mark']:>12.8g} "
                  f"{r['pct']:>+7.2f}% {r['pnl']:>+8.2f} "
                  f"{r['to_tp']:>6.2f}% {r['to_sl']:>6.2f}%   {ind}")
        best = max(r["pct"] for r in path)
        worst = min(r["pct"] for r in path)
        print(f"  ran as far as {best:+.2f}%, as far back as {worst:+.2f}%, "
              f"over {path[-1]['age_min']:.0f} minutes and "
              f"{len(path)} samples")
    return 0


def main() -> int:
    what = sys.argv[1] if len(sys.argv) > 1 else "record"
    if what == "record":
        return record()
    if what == "report":
        return report(sys.argv[2] if len(sys.argv) > 2 else None)
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
