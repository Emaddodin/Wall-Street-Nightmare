#!/usr/bin/env python3
"""COMBO BOOK -- stack the two effects that actually measured.

   effect 1  cross-sectional momentum: rank by the past 48h return, long the
             strongest third / short the weakest  (+27 bps per 12h, t=4.2)
   effect 2  the intraday clock: 09:00-14:00 UTC drifts up, 21:00-22:00 down
             (+26 bps per 12h, t=8.3)

Stacking them means: inside the strong up-window, buy only the coins that
are ALSO the strongest on 48h; inside the down-window, short only the
weakest.  If the two effects are independent this should beat either alone.

Risk is handled by sizing, not stops.  Costs are charged on entry+exit.

    python3 tools/combo_book.py [days] [n_coins]
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
LOOK = 192          # 48h


def load(days, want):
    out = {}
    for c in COINS[:want]:
        f = CACHE / f"{c}_{days}d.json"
        if f.exists():
            bars = json.loads(f.read_text())
            if len(bars) >= 500:
                out[c] = bars
    return out


def idx_at(bars, t_ms):
    for i, b in enumerate(bars):
        if int(b["t"]) >= t_ms:
            return i
    return None


def main(days: int, want: int) -> int:
    data = load(days, want)
    if not data:
        print("no cached data")
        return 1
    print("universe %d coins, %d days\n" % (len(data), days))
    times = sorted({int(b["t"]) for bars in data.values() for b in bars})

    def book(windows, levs, select, fee_bps=6.0, start=100.0):
        """windows: list of (entry_hour, hold_hours, side)."""
        eq = peak = start
        max_dd = 0.0
        trades = wins = 0
        t0, t1 = times[0], times[-1]
        for t in times:
            hr = time.gmtime(t / 1000).tm_hour
            for (h, hold, side) in windows:
                if hr != h:
                    continue
                picks = []
                for c, bars in data.items():
                    i = idx_at(bars, t)
                    if i is None or i + hold * 4 >= len(bars) or i < LOOK:
                        continue
                    p0, p1 = float(bars[i]["c"]), float(bars[i + hold * 4]["c"])
                    if p0 <= 0:
                        continue
                    ret = p1 / p0 - 1.0
                    past = float(bars[i]["c"]) / float(bars[i - LOOK]["c"]) - 1.0
                    picks.append((c, ret, past))
                if len(picks) < 6:
                    continue
                if select == "rank":
                    picks.sort(key=lambda x: x[2])
                    k = max(2, len(picks) // 3)
                    picks = picks[-k:] if side > 0 else picks[:k]
                r = sum(x[1] for x in picks) / len(picks) * side
                net = r - fee_bps * 1e-4 * 2
                pnl = eq * levs * net
                eq += pnl
                trades += 1
                if pnl > 0:
                    wins += 1
                peak = max(peak, eq)
                max_dd = max(max_dd, (peak - eq) / peak * 100)
        dd = max(1, (t1 - t0) / 86_400_000)
        return {"n": trades, "win": wins / trades * 100 if trades else 0,
                "eq": eq, "per_day": ((eq / start) ** (1 / dd) - 1) * 100,
                "max_dd": max_dd}

    tests = [
        ("clock only (10h L / 21h S)", [(10, 12, 1), (21, 12, -1)], "all"),
        ("clock + 48h rank select", [(10, 12, 1), (21, 12, -1)], "rank"),
        ("rank only (12h rebalance)", [(0, 12, 1)], "rank"),
        ("clock + rank, 13h window", [(13, 4, 1)], "rank"),
    ]
    print("%-32s %-8s %-8s %-9s %-10s %s"
          % ("book", "trades", "win%", "end eq", "%/day", "maxDD%"))
    print("-" * 80)
    for name, wins, sel in tests:
        for lev in (1.0, 2.0, 3.0):
            r = book(wins, lev, sel)
            print("%-32s %-8d %-8.1f %-9.2f %+10.3f %6.1f   (%.0fx)"
                  % (name, r["n"], r["win"], r["eq"], r["per_day"],
                     r["max_dd"], lev))

    # ---- out-of-sample: does it hold in BOTH halves? --------------------
    print("\n" + "=" * 80)
    print("OUT-OF-SAMPLE: clock + rank book, 1st half vs 2nd half")
    print("=" * 80)
    cut = times[len(times) // 2]
    global _CUT
    _CUT = cut

    def book_range(windows, select, lo, hi, fee_bps=6.0, start=100.0):
        eq = peak = start
        max_dd = 0.0
        trades = wins = 0
        for t in times:
            if not (lo <= t < hi):
                continue
            hr = time.gmtime(t / 1000).tm_hour
            for (h, hold, side) in windows:
                if hr != h:
                    continue
                picks = []
                for c, bars in data.items():
                    i = idx_at(bars, t)
                    if i is None or i + hold * 4 >= len(bars) or i < LOOK:
                        continue
                    p0, p1 = float(bars[i]["c"]), float(bars[i + hold * 4]["c"])
                    if p0 <= 0:
                        continue
                    picks.append((c, p1 / p0 - 1.0,
                                  float(bars[i]["c"]) / float(bars[i - LOOK]["c"]) - 1.0))
                if len(picks) < 6:
                    continue
                if select == "rank":
                    picks.sort(key=lambda x: x[2])
                    k = max(2, len(picks) // 3)
                    picks = picks[-k:] if side > 0 else picks[:k]
                net = sum(x[1] for x in picks) / len(picks) * side \
                    - fee_bps * 1e-4 * 2
                pnl = eq * net
                eq += pnl
                trades += 1
                if pnl > 0:
                    wins += 1
                peak = max(peak, eq)
                max_dd = max(max_dd, (peak - eq) / peak * 100)
        dd = max(1, (hi - lo) / 86_400_000)
        return {"n": trades, "win": wins / trades * 100 if trades else 0,
                "per_day": ((eq / start) ** (1 / dd) - 1) * 100,
                "max_dd": max_dd}

    cw = [(10, 12, 1), (21, 12, -1)]
    for label, lo, hi in (("1st half", times[0], cut),
                          ("2nd half", cut, times[-1])):
        r = book_range(cw, "rank", lo, hi)
        print("  %-10s trades %-5d win %-5.1f %%/day %+7.3f  maxDD %5.1f"
              % (label, r["n"], r["win"], r["per_day"], r["max_dd"]))
    return 0


if __name__ == "__main__":
    d = int(sys.argv[1]) if len(sys.argv) > 1 else 180
    c = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    raise SystemExit(main(d, c))
