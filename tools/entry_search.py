#!/usr/bin/env python3
"""ENTRY SEARCH inside the leveraged-day-trade paradigm.

Goal, stated in the operator's own terms: find an entry whose WIN RATE beats
the breakeven for the payoff being traded, so that N wins/day can compose the
daily target.  (breakeven = 1/(1+R:R))

Every signal is evaluated with the SAME mechanical exit: entry at the signal
bar close, stop = k x ATR, target = R x stop, horizon H bars, stop wins
intrabar ties.  No look-ahead.

Signals tested
  breakout-N        close breaks the N-bar high/low
  momentum-K        K consecutive same-direction bars
  volspike          volume >= mult x its average, direction from the body
  rel-strength      only long the top third / short the bottom third by
                    past-48h return (the one effect that measured positive)
  sweep+fvg         today's live detector (the baseline to beat)

    python3 tools/entry_search.py [days] [n_coins]
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


def simulate(bars, i, side, stop_dist, rr, horizon):
    """Entry at close[i]; stop k x ATR away, target rr x that. -> R or None."""
    if stop_dist <= 0:
        return None
    e = float(bars[i]["c"])
    sl = e - side * stop_dist
    tp = e + side * rr * stop_dist
    for k in range(i + 1, min(len(bars), i + 1 + horizon)):
        h, lo = float(bars[k]["h"]), float(bars[k]["l"])
        if (side > 0 and lo <= sl) or (side < 0 and h >= sl):
            return -1.0
        if (side > 0 and h >= tp) or (side < 0 and lo <= tp):
            return rr
    return None                      # timeout: not counted


def load(days, want):
    out = []
    for c in COINS[:want]:
        f = CACHE / f"{c}_{days}d.json"
        if not f.exists():
            continue
        bars = json.loads(f.read_text())
        if len(bars) >= 500:
            out.append((c, bars))
    return out


def rel_rank(data, i, look=192):
    """Cross-sectional rank of each coin by its past-`look`-bar return."""
    rets = {}
    for c, bars in data:
        if i < look or i >= len(bars):
            continue
        p0, p1 = float(bars[i - look]["c"]), float(bars[i]["c"])
        if p0 > 0:
            rets[c] = p1 / p0 - 1.0
    if len(rets) < 6:
        return {}
    order = sorted(rets, key=lambda c: rets[c])
    k = max(1, len(order) // 3)
    rank = {}
    for idx, c in enumerate(order):
        if idx < k:
            rank[c] = -1             # weakest third
        elif idx >= len(order) - k:
            rank[c] = 1              # strongest third
        else:
            rank[c] = 0
    return rank


def test(name, data, sig_fn, rrs=(2.0, 3.0, 4.0), k_atr=1.0, horizon=32):
    res = {r: [] for r in rrs}
    for c, bars in data:
        for i in range(200, len(bars) - horizon - 1):
            s = sig_fn(c, bars, i)
            if not s:
                continue
            a = atr(bars, i)
            if a <= 0:
                continue
            for r in rrs:
                out = simulate(bars, i, s, k_atr * a, r, horizon)
                if out is not None:
                    res[r].append(out)
    print("-- %s" % name)
    for r in rrs:
        xs = res[r]
        if len(xs) < 30:
            print("   R:R %.1f  (n=%d too few)" % (r, len(xs)))
            continue
        wr = sum(1 for x in xs if x > 0) / len(xs) * 100
        exp = sum(xs) / len(xs)
        be = 1 / (1 + r) * 100
        verdict = "EDGE" if wr > be + 2 else ("~flat" if abs(wr - be) <= 2
                                              else "no")
        print("   R:R %.1f  n=%5d  win %4.1f%%  (breakeven %4.1f%%)  "
              "exp %+6.3fR   %s" % (r, len(xs), wr, be, exp, verdict))
    return res


def main(days: int, want: int) -> int:
    data = load(days, want)
    if not data:
        print("no cached data -- run edge_diagnosis.py first")
        return 1
    print("universe: %d coins x %d bars (%.0f days)\n"
          % (len(data), len(data[0][1]), len(data[0][1]) / 96))

    rank_cache: dict[int, dict] = {}

    def rk(i):
        if i not in rank_cache:
            rank_cache.clear()
            rank_cache[i] = rel_rank(data, i)
        return rank_cache[i]

    test("breakout-20", data,
         lambda c, b, i: (1 if float(b[i]["c"]) > max(float(x["h"]) for x in b[i - 20:i])
                          else (-1 if float(b[i]["c"]) < min(float(x["l"]) for x in b[i - 20:i]) else 0)))
    test("breakout-50", data,
         lambda c, b, i: (1 if float(b[i]["c"]) > max(float(x["h"]) for x in b[i - 50:i])
                          else (-1 if float(b[i]["c"]) < min(float(x["l"]) for x in b[i - 50:i]) else 0)))
    test("momentum-3 up/down", data,
         lambda c, b, i: (1 if all(float(b[j]["c"]) > float(b[j]["o"]) for j in range(i - 2, i + 1))
                          else (-1 if all(float(b[j]["c"]) < float(b[j]["o"]) for j in range(i - 2, i + 1)) else 0)))
    test("volspike 2x", data,
         lambda c, b, i: (0 if (sum(float(x.get("v") or 0) for x in b[i - 20:i]) / 20) <= 0
                          or float(b[i].get("v") or 0) < 2.0 * (sum(float(x.get("v") or 0) for x in b[i - 20:i]) / 20)
                          else (1 if float(b[i]["c"]) > float(b[i]["o"]) else -1)))
    test("breakout-20 + rel-strength", data,
         lambda c, b, i: (lambda s, r: s if (s and r.get(c) == s) else 0)(
             (1 if float(b[i]["c"]) > max(float(x["h"]) for x in b[i - 20:i]) else
              (-1 if float(b[i]["c"]) < min(float(x["l"]) for x in b[i - 20:i]) else 0)), rk(i)))
    test("rel-strength alone (48h)", data,
         lambda c, b, i: rk(i).get(c, 0))
    return 0


if __name__ == "__main__":
    d = int(sys.argv[1]) if len(sys.argv) > 1 else 180
    c = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    raise SystemExit(main(d, c))
