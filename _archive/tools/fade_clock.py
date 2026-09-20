#!/usr/bin/env python3
"""FADE + CLOCK -- nail down the two effects that measured real.

Finding A  mean reversion after a spike: FADE a >=2 ATR move  -> t up to +7.8
Finding B  intraday seasonality: 10:00-14:00 UTC drifts UP hard (t=+8.3),
           21:00-22:00 UTC drifts DOWN (t=-5.8)

This tool tunes them and, crucially, prices them against real costs:
  taker round trip ~6 bps | maker round trip ~3 bps

Then it converts the best setup into what the account actually earns at a
given leverage and margin fraction, so the daily number is honest.

    python3 tools/fade_clock.py [days] [n_coins]
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


def stats(xs):
    n = len(xs)
    if n < 10:
        return 0.0, 0.0, n
    mu = sum(xs) / n
    var = sum((x - mu) ** 2 for x in xs) / (n - 1)
    sd = math.sqrt(var) if var > 0 else 0.0
    return mu, (mu / (sd / math.sqrt(n)) if sd > 0 else 0.0), n


def atr(bars, i, n=14):
    if i < n + 1:
        return 0.0
    trs = []
    for k in range(i - n, i + 1):
        h, lo = float(bars[k]["h"]), float(bars[k]["l"])
        pc = float(bars[k - 1]["c"])
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    return sum(trs) / len(trs)


def main(days: int, want: int) -> int:
    data = {}
    for c in COINS[:want]:
        f = CACHE / f"{c}_{days}d.json"
        if f.exists():
            bars = json.loads(f.read_text())
            if len(bars) >= 500:
                data[c] = bars
    alts = [c for c in data if c not in ("BTC", "ETH")]
    print("universe %d coins\n" % len(data))

    # ---- A. fade threshold sensitivity --------------------------------
    print("=" * 72)
    print("A. FADE a k-bar move of >= T x ATR, hold H bars (all coins)")
    print("=" * 72)
    print("%-8s %-6s %-8s %-10s %-10s %s"
          % ("lookback", "thresh", "hold", "gross bps", "net@3bps", "t-stat"))
    best = []
    for kb, k in (("1h", 4), ("4h", 16)):
        for thr in (1.5, 2.0, 3.0, 4.0):
            for hn, h in (("4h", 16), ("12h", 48)):
                xs = []
                for c in alts:
                    b = data[c]
                    for i in range(k + 40, len(b) - h - 1):
                        a = atr(b, i)
                        if a <= 0:
                            continue
                        move = (float(b[i]["c"]) - float(b[i - k]["c"])) / a
                        if abs(move) < thr:
                            continue
                        side = -1 if move > 0 else 1
                        p0, p1 = float(b[i]["c"]), float(b[i + h]["c"])
                        if p0 > 0:
                            xs.append((p1 / p0 - 1.0) * 1e4 * side)
                m, t, n = stats(xs)
                if n < 200:
                    continue
                print("%-8s %-6.1f %-8s %+9.1f %+9.1f  (t=%+5.2f n=%6d)"
                      % (kb, thr, hn, m, m - 3.0, t, n))
                best.append((m - 3.0, "fade %s>=%.1fATR hold %s" % (kb, thr, hn), t, n))

    # ---- B. the clock, and what it pays -------------------------------
    print("\n" + "=" * 72)
    print("B. CLOCK: forward H-bar return by UTC hour (long side), best hours")
    print("=" * 72)
    for hn, h in (("4h", 16), ("12h", 48)):
        rows = []
        for hr in range(24):
            xs = []
            for c in alts:
                b = data[c]
                for i in range(60, len(b) - h - 1):
                    if time.gmtime(int(b[i]["t"]) / 1000).tm_hour != hr:
                        continue
                    p0, p1 = float(b[i]["c"]), float(b[i + h]["c"])
                    if p0 > 0:
                        xs.append((p1 / p0 - 1.0) * 1e4)
            m, t, n = stats(xs)
            if n:
                rows.append((m, hr, t, n))
        rows.sort(key=lambda x: -x[0])
        print("  hold %s -- top 4 LONG hours:" % hn)
        for m, hr, t, n in rows[:4]:
            print("     %02d:00 UTC  %+7.1f bps (t=%+5.2f n=%6d)  net@3bps %+6.1f"
                  % (hr, m, t, n, m - 3.0))
        print("  hold %s -- worst 3 (SHORT candidates):" % hn)
        for m, hr, t, n in rows[-3:]:
            print("     %02d:00 UTC  %+7.1f bps (t=%+5.2f n=%6d)  net@3bps %+6.1f"
                  % (hr, m, t, n, -m - 3.0))

    # ---- C. combine: clock window AND fade -----------------------------
    print("\n" + "=" * 72)
    print("C. COMBO: enter only inside the strong clock window AND on a fade")
    print("=" * 72)
    for kb, k, thr in (("4h", 16, 2.0), ("1h", 4, 2.0)):
        for hn, h in (("4h", 16), ("12h", 48)):
            xs = []
            for c in alts:
                b = data[c]
                for i in range(k + 60, len(b) - h - 1):
                    hr = time.gmtime(int(b[i]["t"]) / 1000).tm_hour
                    # long only in the strong up-window, short in the down one
                    if not (9 <= hr <= 15 or hr in (21, 22)):
                        continue
                    a = atr(b, i)
                    if a <= 0:
                        continue
                    move = (float(b[i]["c"]) - float(b[i - k]["c"])) / a
                    if abs(move) < thr:
                        continue
                    side = -1 if move > 0 else 1
                    p0, p1 = float(b[i]["c"]), float(b[i + h]["c"])
                    if p0 > 0:
                        xs.append((p1 / p0 - 1.0) * 1e4 * side)
            m, t, n = stats(xs)
            if n >= 100:
                print("  fade %s>=%.1fATR in window, hold %-4s %+7.1f bps "
                      "(t=%+5.2f n=%5d) net@3bps %+6.1f"
                      % (kb, thr, hn, m, t, n, m - 3.0))

    # ---- D. what it pays the account ----------------------------------
    print("\n" + "=" * 72)
    print("D. ACCOUNT MATH: daily %% at leverage L, margin = 50%% of equity")
    print("=" * 72)
    print("  assume 2 signal-trades/day at the best combo's net bps")
    if best:
        best.sort(key=lambda x: -x[0])
        net, label, t, n = best[0]
        print("  best combo: %s -> %+.1f bps net/trade (t=%+.2f, n=%d)"
              % (label, net, t, n))
        for lev in (5, 10, 20, 50):
            daily = net * 1e-4 * lev * 0.5 * 2 * 100
            print("     %2dx leverage -> %+6.2f%% per day" % (lev, daily))
    return 0


if __name__ == "__main__":
    d = int(sys.argv[1]) if len(sys.argv) > 1 else 180
    c = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    raise SystemExit(main(d, c))
