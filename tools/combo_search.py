#!/usr/bin/env python3
"""COMBO SEARCH -- do stacked signals + filters beat the breakeven line?

Single entries measured ~exactly at the random-walk breakeven (1/(1+R:R)),
i.e. no information.  This tests whether COMBINATIONS do better: a base
signal plus any subset of independent filters, all of which must agree.

Per (coin, bar) we precompute the forward outcome for BOTH directions and
each R:R, so a combination is just a filtered lookup -- fast enough to sweep
every subset.

filters
  rel    trade only with the 48h cross-sectional rank (strong->long)
  sess   only the NY killzone (12:00-20:00 UTC)
  trend  only with the 4h trend (EMA20 slope)
  volhi  only when ATR% is above the coin's own median

    python3 tools/combo_search.py [days] [n_coins]
"""
from __future__ import annotations

import itertools
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
RRS = (2.0, 3.0)
HORIZON = 32
LOOK = 192          # 48h in 15m bars


def atr(bars, i, n=14):
    if i < n + 1:
        return 0.0
    trs = []
    for k in range(i - n, i + 1):
        h, lo = float(bars[k]["h"]), float(bars[k]["l"])
        pc = float(bars[k - 1]["c"])
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    return sum(trs) / len(trs)


def outcome(bars, i, side, sd, rr):
    e = float(bars[i]["c"])
    sl, tp = e - side * sd, e + side * rr * sd
    for k in range(i + 1, min(len(bars), i + 1 + HORIZON)):
        h, lo = float(bars[k]["h"]), float(bars[k]["l"])
        if (side > 0 and lo <= sl) or (side < 0 and h >= sl):
            return -1.0
        if (side > 0 and h >= tp) or (side < 0 and lo <= tp):
            return rr
    return None


def build(data):
    """rows: (coin_idx, i, signals, filters, outcomes[rr][side])"""
    rows = []
    for ci, (coin, bars) in enumerate(data):
        n = len(bars)
        atrs = [atr(bars, i) for i in range(n)]
        pcts = [a / float(bars[i]["c"]) for i, a in enumerate(atrs)
                if a > 0 and float(bars[i]["c"]) > 0]
        med = sorted(pcts)[len(pcts) // 2] if pcts else 0.0
        closes = [float(b["c"]) for b in bars]
        for i in range(max(200, LOOK), n - HORIZON - 1):
            a = atrs[i]
            if a <= 0:
                continue
            sigs = {}
            hi20 = max(float(b["h"]) for b in bars[i - 20:i])
            lo20 = min(float(b["l"]) for b in bars[i - 20:i])
            hi50 = max(float(b["h"]) for b in bars[i - 50:i])
            lo50 = min(float(b["l"]) for b in bars[i - 50:i])
            c = closes[i]
            sigs["bo20"] = 1 if c > hi20 else (-1 if c < lo20 else 0)
            sigs["bo50"] = 1 if c > hi50 else (-1 if c < lo50 else 0)
            sigs["mom3"] = 1 if all(float(bars[j]["c"]) > float(bars[j]["o"])
                                    for j in range(i - 2, i + 1)) else (
                -1 if all(float(bars[j]["c"]) < float(bars[j]["o"])
                          for j in range(i - 2, i + 1)) else 0)
            v = float(bars[i].get("v") or 0)
            vavg = sum(float(x.get("v") or 0) for x in bars[i - 20:i]) / 20
            sigs["vol"] = (1 if float(bars[i]["c"]) > float(bars[i]["o"])
                           else -1) if (vavg > 0 and v >= 2 * vavg) else 0
            # filters
            p0 = closes[i - LOOK]
            past = c / p0 - 1.0 if p0 > 0 else 0.0
            ema_f = sum(closes[i - 4:i + 1]) / 5
            ema_s = sum(closes[i - 20:i + 1]) / 21
            hours = time.gmtime(int(bars[i]["t"]) / 1000).tm_hour
            rows.append((ci, i, sigs,
                         {"ret": past, "trend": 1 if ema_f > ema_s else -1,
                          "sess": 1 if 12 <= hours < 20 else 0,
                          "volhi": 1 if (a / c) > med else 0},
                         {rr: {1: outcome(bars, i, 1, a, rr),
                               -1: outcome(bars, i, -1, a, rr)}
                          for rr in RRS}))
    return rows


def main(days: int, want: int) -> int:
    data = []
    for c in COINS[:want]:
        f = CACHE / f"{c}_{days}d.json"
        if f.exists():
            bars = json.loads(f.read_text())
            if len(bars) >= 500:
                data.append((c, bars))
    if not data:
        print("no cached data")
        return 1
    print("universe %d coins; building features ..." % len(data), flush=True)
    rows = build(data)
    print("rows: %d\n" % len(rows), flush=True)

    # cross-sectional rank per timestamp (strong third = +1, weak = -1)
    by_t: dict[int, list] = {}
    for idx, (ci, i, sigs, filt, outs) in enumerate(rows):
        by_t.setdefault(data[ci][1][i]["t"], []).append(idx)
    rank = {}
    for t, idxs in by_t.items():
        if len(idxs) < 6:
            continue
        order = sorted(idxs, key=lambda x: rows[x][3]["ret"])
        k = max(1, len(order) // 3)
        for pos, x in enumerate(order):
            rank[x] = -1 if pos < k else (1 if pos >= len(order) - k else 0)

    base_sigs = ["bo20", "bo50", "mom3", "vol"]
    filters = ["rel", "sess", "trend", "volhi"]
    results = []
    for sig in base_sigs:
        for bits in itertools.product([0, 1], repeat=len(filters)):
            on = [f for f, b in zip(filters, bits) if b]
            for rr in RRS:
                xs = []
                for idx, (ci, i, sigs, filt, outs) in enumerate(rows):
                    side = sigs[sig]
                    if not side:
                        continue
                    ok = True
                    for f in on:
                        if f == "rel":
                            if rank.get(idx, 0) != side:
                                ok = False
                                break
                        elif f == "sess":
                            if not filt["sess"]:
                                ok = False
                                break
                        elif f == "trend":
                            if filt["trend"] != side:
                                ok = False
                                break
                        elif f == "volhi":
                            if not filt["volhi"]:
                                ok = False
                                break
                    if not ok:
                        continue
                    o = outs[rr][side]
                    if o is not None:
                        xs.append(o)
                if len(xs) < 100:
                    continue
                wr = sum(1 for x in xs if x > 0) / len(xs) * 100
                exp = sum(xs) / len(xs)
                be = 1 / (1 + rr) * 100
                results.append((wr - be, sig, on, rr, len(xs), wr, exp, be))

    results.sort(key=lambda x: -x[0])
    print("%-6s %-26s %-5s %-7s %-7s %-7s %s"
          % ("sig", "filters", "R:R", "n", "win%", "be%", "exp R"))
    print("-" * 78)
    for d, sig, on, rr, n, wr, exp, be in results[:18]:
        print("%-6s %-26s %-5.1f %-7d %-7.1f %-7.1f %+7.3f  %s"
              % (sig, ",".join(on) or "(none)", rr, n, wr, be, exp,
                 "EDGE" if d > 2 else ""))
    print("\nworst 5:")
    for d, sig, on, rr, n, wr, exp, be in results[-5:]:
        print("%-6s %-26s %-5.1f %-7d %-7.1f %-7.1f %+7.3f"
              % (sig, ",".join(on) or "(none)", rr, n, wr, be, exp))
    pos = [r for r in results if r[0] > 2]
    print("\ncombos with win%% > breakeven+2: %d of %d tested"
          % (len(pos), len(results)))
    return 0


if __name__ == "__main__":
    d = int(sys.argv[1]) if len(sys.argv) > 1 else 180
    c = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    raise SystemExit(main(d, c))
