#!/usr/bin/env python3
"""NEXT HYPOTHESES -- effects the OHLCV-pattern search could not see.

Each test measures the FORWARD return (bps) of a directional bet, with a
t-stat.  |t| >= 2 means the effect is probably real.  Costs ~6 bps round trip.

  1. BTC LEAD-LAG      BTC moves first; do alts follow?  (beta-adjusted)
  2. FADE THE SPIKE    after an extreme k-bar move, does it revert?
  3. FOLLOW THE SPIKE  the sign-flip of (2)
  4. SQUEEZE BREAKOUT  low-volatility coil, then trade the break
  5. SESSION CLOCK     forward return by UTC hour (where is the money?)
  6. FUNDING PROXY     perp premium / OI change -> next move

    python3 tools/next_hypotheses.py [days] [n_coins]
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
HORIZONS = {"1h": 4, "4h": 16, "12h": 48, "24h": 96}


def stats(xs):
    n = len(xs)
    if n < 10:
        return 0.0, 0.0, n
    mu = sum(xs) / n
    var = sum((x - mu) ** 2 for x in xs) / (n - 1)
    sd = math.sqrt(var) if var > 0 else 0.0
    return mu, (mu / (sd / math.sqrt(n)) if sd > 0 else 0.0), n


def load(days, want):
    out = {}
    for c in COINS[:want]:
        f = CACHE / f"{c}_{days}d.json"
        if f.exists():
            bars = json.loads(f.read_text())
            if len(bars) >= 500:
                out[c] = bars
    return out


def atr(bars, i, n=14):
    if i < n + 1:
        return 0.0
    trs = []
    for k in range(i - n, i + 1):
        h, lo = float(bars[k]["h"]), float(bars[k]["l"])
        pc = float(bars[k - 1]["c"])
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    return sum(trs) / len(trs)


def fwd(bars, i, h):
    if i + h >= len(bars):
        return None
    p0, p1 = float(bars[i]["c"]), float(bars[i + h]["c"])
    return (p1 / p0 - 1.0) * 1e4 if p0 > 0 else None


def main(days: int, want: int) -> int:
    data = load(days, want)
    if not data:
        print("no cached data")
        return 1
    alts = [c for c in data if c not in ("BTC", "ETH")]
    print("universe: %d coins (%d alts)\n" % (len(data), len(alts)))

    # ---- 1. BTC lead-lag ------------------------------------------------
    print("=" * 70)
    print("1. BTC LEAD-LAG: when BTC moves, do alts follow (beta-adjusted)?")
    print("=" * 70)
    btc = data.get("BTC")
    if btc:
        for kb, k in (("1h", 4), ("4h", 16), ("12h", 48)):
            for hn, h in HORIZONS.items():
                xs = []
                for c in alts[:10]:
                    b = data[c]
                    for i in range(k + 20, min(len(b), len(btc)) - h - 1):
                        bt = (float(btc[i]["c"]) / float(btc[i - k]["c"]) - 1.0)
                        if abs(bt) < 0.002:      # only real BTC moves
                            continue
                        side = 1 if bt > 0 else -1
                        f = fwd(b, i, h)
                        if f is not None:
                            xs.append(f * side)
                m, t, n = stats(xs)
                if n:
                    print("   BTC %-4s -> alt %-4s  %+7.1f bps (t=%+5.2f, n=%5d)"
                          % (kb, hn, m, t, n))

    # ---- 2/3. spike fade vs follow --------------------------------------
    print("\n" + "=" * 70)
    print("2/3. SPIKE: after an extreme k-bar move, FADE or FOLLOW?")
    print("=" * 70)
    for kb, k in (("1h", 4), ("4h", 16)):
        for hn, h in HORIZONS.items():
            xs = []
            for c in alts:
                b = data[c]
                for i in range(k + 40, len(b) - h - 1):
                    a = atr(b, i)
                    if a <= 0:
                        continue
                    move = (float(b[i]["c"]) - float(b[i - k]["c"])) / a
                    if abs(move) < 2.0:          # >= 2 ATR move
                        continue
                    side = -1 if move > 0 else 1   # FADE the spike
                    f = fwd(b, i, h)
                    if f is not None:
                        xs.append(f * side)
            m, t, n = stats(xs)
            if n:
                print("   spike %-4s -> %-4s  FADE %+7.1f bps (t=%+5.2f n=%5d)"
                      "   FOLLOW %+7.1f bps" % (kb, hn, m, t, n, -m))

    # ---- 4. squeeze breakout --------------------------------------------
    print("\n" + "=" * 70)
    print("4. SQUEEZE: low-vol coil then break -> continuation?")
    print("=" * 70)
    for hn, h in HORIZONS.items():
        xs, n_sig = [], 0
        for c in alts:
            b = data[c]
            for i in range(120, len(b) - h - 1):
                a = atr(b, i)
                if a <= 0:
                    continue
                recent = [abs(float(b[j]["c"]) - float(b[j - 1]["c"]))
                          for j in range(i - 24, i)]
                if not recent:
                    continue
                coil = sum(recent) / len(recent)
                if coil > 0.7 * a:           # tight range vs its own ATR
                    continue
                brk = float(b[i]["c"]) - float(b[i - 1]["c"])
                if abs(brk) < 0.3 * a:
                    continue
                n_sig += 1
                side = 1 if brk > 0 else -1
                f = fwd(b, i, h)
                if f is not None:
                    xs.append(f * side)
        m, t, n = stats(xs)
        if n:
            print("   coil->break -> %-4s  %+7.1f bps (t=%+5.2f n=%5d)"
                  % (hn, m, t, n))

    # ---- 5. session clock ------------------------------------------------
    print("\n" + "=" * 70)
    print("5. CLOCK: mean forward 4h return by UTC hour (all coins, long side)")
    print("=" * 70)
    for h in range(24):
        xs = []
        for c in alts[:8]:
            b = data[c]
            for i in range(60, len(b) - 16 - 1):
                if time.gmtime(int(b[i]["t"]) / 1000).tm_hour != h:
                    continue
                f = fwd(b, i, 16)
                if f is not None:
                    xs.append(f)
        m, t, n = stats(xs)
        if n:
            bar = "#" * max(0, int(m / 5)) if m > 0 else "-" * max(0, int(-m / 5))
            print("   %02d:00 UTC  %+7.1f bps (t=%+5.2f)  %s" % (h, m, t, bar))
    return 0


if __name__ == "__main__":
    d = int(sys.argv[1]) if len(sys.argv) > 1 else 180
    c = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    raise SystemExit(main(d, c))
