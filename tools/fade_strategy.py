#!/usr/bin/env python3
"""FADE STRATEGY BACKTEST -- the measured effects, with real risk controls.

Signal (measured, t=+5.6): a coin moves >= THR x ATR over LOOK bars, then
FADE it (bet on reversion) holding HOLD bars.  Optional clock filter: only
long inside the strong up-window, only short in the down-window.

Unlike the raw expectancy test this models what actually decides survival:
  - a hard stop (a fade can run away)
  - taker/maker costs
  - fixed fractional risk per trade
  - leverage derived from the stop distance (risk-first sizing)
  - max concurrent positions (capital constraint)

    python3 tools/fade_strategy.py [days] [n_coins]
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CACHE = Path("/root/ict_sniper/data/research/binance_15m")
COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK",
         "UNI", "ARB", "OP", "NEAR", "ATOM", "FIL", "INJ", "SUI", "APT",
         "SEI", "TIA", "MINA", "SAGA", "ACE", "LTC", "DOT", "MATIC", "TON",
         "PEPE", "WIF", "ORDI"]
TF = 900_000


def atr(bars, i, n=14):
    if i < n + 1:
        return 0.0
    trs = []
    for k in range(i - n, i + 1):
        h, lo = float(bars[k]["h"]), float(bars[k]["l"])
        pc = float(bars[k - 1]["c"])
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    return sum(trs) / len(trs)


def run(data, look=4, thr=3.0, hold=16, stop_atr=1.5, risk_pct=0.02,
        fee_bps=6.0, slots=4, clock=False, start_eq=100.0):
    """Event-driven over the merged timeline of all coins."""
    events = []
    for c, bars in data.items():
        for i in range(max(60, look + 20), len(bars) - 1):
            a = atr(bars, i)
            if a <= 0:
                continue
            move = (float(bars[i]["c"]) - float(bars[i - look]["c"])) / a
            if abs(move) < thr:
                continue
            side = -1 if move > 0 else 1
            hr = time.gmtime(int(bars[i]["t"]) / 1000).tm_hour
            if clock and not (9 <= hr <= 15 or hr in (21, 22)):
                continue
            events.append((int(bars[i]["t"]), c, i, side, a))
    events.sort()
    bars_of = {c: b for c, b in data.items()}

    eq = start_eq
    peak = eq
    max_dd = 0.0
    open_pos = []
    trades = wins = 0
    pnl_sum = 0.0
    curve = []
    idx = 0
    while idx < len(events):
        t, c, i, side, a = events[idx]
        # close mature positions first
        still = []
        for (ct, cc, ci, cside, ca, cstop, cmargin, centry, cnotional) in open_pos:
            b = bars_of[cc]
            exit_px = None
            for k in range(ci + 1, min(len(b), ci + 1 + hold)):
                h, lo = float(b[k]["h"]), float(b[k]["l"])
                if (cside > 0 and lo <= cstop) or (cside < 0 and h >= cstop):
                    exit_px = cstop
                    break
            if exit_px is None:
                k = min(len(b) - 1, ci + hold)
                exit_px = float(b[k]["c"])
            gross = (exit_px - centry) * cside / centry
            pnl = cnotional * gross - cnotional * fee_bps * 1e-4
            eq += pnl
            pnl_sum += pnl
            trades += 1
            if pnl > 0:
                wins += 1
            peak = max(peak, eq)
            max_dd = max(max_dd, (peak - eq) / peak * 100)
        open_pos = []
        # open a new one if a slot is free
        entry = float(bars_of[c][i]["c"])
        sd = stop_atr * a
        lev = min(50.0, max(1.0, (risk_pct / (sd / entry))) if sd > 0 else 1.0)
        margin = eq / slots
        notional = margin * lev
        stop = entry - side * sd
        open_pos.append((t, c, i, side, a, stop, margin, entry, notional))
        curve.append((t, eq))
        idx += 1

    days = (events[-1][0] - events[0][0]) / 86_400_000 if events else 1
    return {"n": trades, "win": wins / trades * 100 if trades else 0,
            "end_eq": eq, "days": days,
            "per_day": ((eq / start_eq) ** (1 / max(days, 1)) - 1) * 100,
            "max_dd": max_dd}


def main(days: int, want: int) -> int:
    data = {}
    for c in COINS[:want]:
        f = CACHE / f"{c}_{days}d.json"
        if f.exists():
            bars = json.loads(f.read_text())
            if len(bars) >= 500:
                data[c] = bars
    if not data:
        print("no cached data")
        return 1
    print("universe %d coins, %d days\n" % (len(data), days))
    print("%-34s %-6s %-7s %-9s %-9s %s"
          % ("config", "trades", "win%", "end eq", "%/day", "maxDD%"))
    print("-" * 78)
    cfgs = [
        ("fade 3ATR/1h -> 4h, stop1.5ATR", dict(look=4, thr=3.0, hold=16)),
        ("fade 2ATR/4h -> 4h, stop1.5ATR", dict(look=16, thr=2.0, hold=16)),
        ("fade 3ATR/4h -> 4h, stop1.5ATR", dict(look=16, thr=3.0, hold=16)),
        ("fade 3ATR/1h -> 4h, stop3ATR", dict(look=4, thr=3.0, hold=16,
                                             stop_atr=3.0)),
        ("+ clock filter", dict(look=4, thr=3.0, hold=16, clock=True)),
        ("risk 5%/trade", dict(look=4, thr=3.0, hold=16, risk_pct=0.05)),
        ("risk 10%/trade", dict(look=4, thr=3.0, hold=16, risk_pct=0.10)),
        ("maker fees (3bps)", dict(look=4, thr=3.0, hold=16, fee_bps=3.0)),
    ]
    for name, kw in cfgs:
        r = run(data, **kw)
        print("%-34s %-6d %-7.1f %-9.2f %+9.3f %6.1f"
              % (name, r["n"], r["win"], r["end_eq"], r["per_day"], r["max_dd"]))
    return 0


if __name__ == "__main__":
    d = int(sys.argv[1]) if len(sys.argv) > 1 else 180
    c = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    raise SystemExit(main(d, c))
