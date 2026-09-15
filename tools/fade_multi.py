#!/usr/bin/env python3
"""FADE, MANY POSITIONS -- the operator's "split it into many trades" model.

The measured fade edge is ~+11 bps NET per signal and the signal fires ~28
times/day across the universe.  The earlier backtest failed because it used a
tight stop that clipped the reversion and ran ONE position at a time.

This runs the honest version of the operator's idea:
  - every signal opens a ticket (no stop; a time exit at HOLD bars)
  - capital is SPLIT across concurrent tickets (margin = equity / slots)
  - leverage applied per ticket
  - compounding equity, real costs on entry+exit

    python3 tools/fade_multi.py [days] [n_coins]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CACHE = Path("/root/ict_sniper/data/research/binance_15m")
COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK",
         "UNI", "ARB", "OP", "NEAR", "ATOM", "FIL", "INJ", "SUI", "APT",
         "SEI", "TIA", "MINA", "SAGA", "ACE", "LTC", "DOT", "MATIC", "TON",
         "PEPE", "WIF", "ORDI"]


def atr(bars, i, n=14):
    if i < n + 1:
        return 0.0
    trs = []
    for k in range(i - n, i + 1):
        h, lo = float(bars[k]["h"]), float(bars[k]["l"])
        pc = float(bars[k - 1]["c"])
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    return sum(trs) / len(trs)


def load(days, want):
    out = {}
    for c in COINS[:want]:
        f = CACHE / f"{c}_{days}d.json"
        if f.exists():
            bars = json.loads(f.read_text())
            if len(bars) >= 500:
                out[c] = bars
    return out


def backtest(data, look=4, thr=3.0, hold=16, slots=10, lev=5.0,
             fee_bps=6.0, start=100.0, stop_atr=0.0):
    """Event-driven; up to `slots` tickets open at once."""
    events = []
    for c, bars in data.items():
        for i in range(max(60, look + 20), len(bars) - hold - 1):
            a = atr(bars, i)
            if a <= 0:
                continue
            move = (float(bars[i]["c"]) - float(bars[i - look]["c"])) / a
            if abs(move) < thr:
                continue
            events.append((int(bars[i]["t"]), c, i, -1 if move > 0 else 1, a))
    events.sort()
    times = sorted({t for t, *_ in events})
    if not times:
        return None
    by_t = {}
    for e in events:
        by_t.setdefault(e[0], []).append(e)

    eq = start
    peak = start
    max_dd = 0.0
    open_t: list = []
    trades = wins = 0
    t0, t1 = times[0], times[-1]
    for t in times:
        # settle anything whose hold expired at or before t
        keep = []
        for (ot, oc, oi, oside, oa, oentry, omargin, olev, ostop) in open_t:
            b = data[oc]
            end = oi + hold
            if end > len(b) - 1:
                continue
            exited = False
            if ostop > 0:
                for k in range(oi + 1, min(end, len(b))):
                    h, lo = float(b[k]["h"]), float(b[k]["l"])
                    if (oside > 0 and lo <= ostop) or (oside < 0 and h >= ostop):
                        px = ostop
                        exited = True
                        break
            if not exited:
                px = float(b[end]["c"])
            ret = (px - oentry) / oentry * oside
            pnl = omargin * olev * ret - omargin * olev * fee_bps * 1e-4 * 2
            eq += pnl
            trades += 1
            if pnl > 0:
                wins += 1
            peak = max(peak, eq)
            max_dd = max(max_dd, (peak - eq) / peak * 100)
        # anything still running stays
        open_t = [x for x in open_t if x[0] + hold * 900_000 > t]
        # open new tickets into free slots
        for (et, c, i, side, a) in by_t.get(t, []):
            if len(open_t) >= slots:
                break
            entry = float(data[c][i]["c"])
            margin = eq / slots
            stop = entry - side * stop_atr * a if stop_atr > 0 else 0.0
            open_t.append((et, c, i, side, a, entry, margin, lev, stop))
    days = max(1, (t1 - t0) / 86_400_000)
    return {"n": trades, "win": wins / trades * 100 if trades else 0,
            "eq": eq, "per_day": ((eq / start) ** (1 / days) - 1) * 100,
            "days": days, "max_dd": max_dd,
            "sig_per_day": len(events) / days}


def main(days: int, want: int) -> int:
    data = load(days, want)
    if not data:
        print("no cached data")
        return 1
    print("universe %d coins, %d days\n" % (len(data), days))
    print("%-40s %-8s %-7s %-9s %-10s %s"
          % ("config", "trades", "win%", "end eq", "%/day", "maxDD%"))
    print("-" * 85)
    cfgs = [
        ("3ATR/1h fade, 4h, 10 slots, 5x", dict(thr=3.0, slots=10, lev=5.0)),
        ("3ATR/1h fade, 4h, 10 slots, 10x", dict(thr=3.0, slots=10, lev=10.0)),
        ("3ATR/1h fade, 4h, 20 slots, 10x", dict(thr=3.0, slots=20, lev=10.0)),
        ("2ATR/1h fade, 4h, 20 slots, 10x", dict(thr=2.0, slots=20, lev=10.0)),
        ("2ATR/4h fade, 12h, 20 slots, 10x", dict(look=16, thr=2.0, hold=48,
                                                  slots=20, lev=10.0)),
        ("+ stop 3ATR", dict(thr=3.0, slots=10, lev=5.0, stop_atr=3.0)),
        ("+ stop 5ATR", dict(thr=3.0, slots=10, lev=5.0, stop_atr=5.0)),
    ]
    for name, kw in cfgs:
        r = backtest(data, **kw)
        if not r:
            continue
        print("%-40s %-8d %-7.1f %-9.2f %+10.3f %6.1f"
              % (name, r["n"], r["win"], r["eq"], r["per_day"], r["max_dd"]))
    r = backtest(data, thr=3.0, slots=10, lev=5.0)
    if r:
        print("\nsignals/day across the universe: %.1f" % r["sig_per_day"])
    return 0


if __name__ == "__main__":
    d = int(sys.argv[1]) if len(sys.argv) > 1 else 180
    c = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    raise SystemExit(main(d, c))
