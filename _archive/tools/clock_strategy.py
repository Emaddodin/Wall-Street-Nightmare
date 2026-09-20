#!/usr/bin/env python3
"""CLOCK STRATEGY -- trade the measured intraday drift, no per-trade stop.

The strongest effect found in 180 days of data:
   long  at 09:00-14:00 UTC, hold 12h  -> +14..+26 bps  (t = 4.4 .. 8.3)
   short at 21:00-22:00 UTC, hold 12h  -> -16..-17 bps  (t = -5.8 .. -6.2)

This is a SCHEDULED, market-wide bet, so risk is managed by sizing the whole
basket rather than by a stop that would clip the drift.  The backtest:

   - equal-weight basket of the universe, one ticket per window
   - taker fees on entry and exit
   - fixed leverage on equity, compounding daily
   - reports the honest daily % and the max drawdown

    python3 tools/clock_strategy.py [days] [n_coins]
"""
from __future__ import annotations

import json
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

# window: (entry UTC hour, hold hours, side)
WINDOWS = {
    "long 09:00 hold12": (9, 12, 1),
    "long 10:00 hold12": (10, 12, 1),
    "long 13:00 hold4": (13, 4, 1),
    "long 10:00 hold4": (10, 4, 1),
    "short 21:00 hold12": (21, 12, -1),
    "short 22:00 hold12": (22, 12, -1),
    "BOTH (10h long + 21h short)": None,
}


def load(days, want):
    out = {}
    for c in COINS[:want]:
        f = CACHE / f"{c}_{days}d.json"
        if f.exists():
            bars = json.loads(f.read_text())
            if len(bars) >= 500:
                out[c] = bars
    return out


def basket_return(data, t_ms, hold_bars):
    """Equal-weight return of the universe from t_ms to t_ms + hold."""
    step = 900_000
    rets = []
    for c, bars in data.items():
        idx = None
        for i, b in enumerate(bars):
            if int(b["t"]) >= t_ms:
                idx = i
                break
        if idx is None or idx + hold_bars >= len(bars):
            continue
        p0 = float(bars[idx]["c"])
        p1 = float(bars[idx + hold_bars]["c"])
        if p0 > 0:
            rets.append(p1 / p0 - 1.0)
    return sum(rets) / len(rets) if rets else None


def backtest(data, windows, lev=1.0, fee_bps=6.0, start=100.0):
    times = sorted({int(b["t"]) for bars in data.values() for b in bars})
    t0, t1 = times[0], times[-1]
    eq = peak = start
    max_dd = 0.0
    trades = wins = 0
    day = {}
    for t in times:
        hr = time.gmtime(t / 1000).tm_hour
        for name, spec in windows.items():
            if spec is None:
                continue
            h, hold, side = spec
            if hr != h:
                continue
            r = basket_return(data, t, int(hold * 4))
            if r is None:
                continue
            gross = r * side
            net = gross - fee_bps * 1e-4 * 2       # in and out
            pnl = eq * lev * net
            eq += pnl
            trades += 1
            if pnl > 0:
                wins += 1
            peak = max(peak, eq)
            max_dd = max(max_dd, (peak - eq) / peak * 100)
            d = int(t) // 86_400_000
            day[d] = day.get(d, 0.0) + pnl
    days = max(1, (t1 - t0) / 86_400_000)
    return {"n": trades, "win": wins / trades * 100 if trades else 0,
            "eq": eq, "per_day": ((eq / start) ** (1 / days) - 1) * 100,
            "max_dd": max_dd, "days": days}


def main(days: int, want: int) -> int:
    data = load(days, want)
    if not data:
        print("no cached data")
        return 1
    print("universe %d coins, %d days\n" % (len(data), days))
    print("%-30s %-8s %-8s %-9s %-10s %s"
          % ("window", "trades", "win%", "end eq", "%/day", "maxDD%"))
    print("-" * 80)
    for name, spec in WINDOWS.items():
        if spec is None:
            continue
        for lev in (1.0, 3.0, 5.0, 10.0):
            r = backtest(data, {name: spec}, lev=lev)
            print("%-30s %-8d %-8.1f %-9.2f %+10.3f %6.1f   (%.0fx)"
                  % (name, r["n"], r["win"], r["eq"], r["per_day"],
                     r["max_dd"], lev))
    print("\n-- combined book (long 10:00 + short 21:00) --")
    combo = {k: WINDOWS[k] for k in ("long 10:00 hold12", "short 21:00 hold12")}
    for lev in (1.0, 3.0, 5.0, 10.0):
        r = backtest(data, combo, lev=lev)
        print("%-30s %-8d %-8.1f %-9.2f %+10.3f %6.1f   (%.0fx)"
              % ("combo book", r["n"], r["win"], r["eq"], r["per_day"],
                 r["max_dd"], lev))
    return 0


if __name__ == "__main__":
    d = int(sys.argv[1]) if len(sys.argv) > 1 else 180
    c = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    raise SystemExit(main(d, c))
