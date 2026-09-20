#!/usr/bin/env python3
"""STRUCTURAL EDGE SEARCH -- does any classic crypto effect exist here?

The pattern-based entry measured -0.20R gross over 180 days and every
target/stop geometry stayed negative, so the question becomes: is there ANY
tradeable effect on this universe, or is the whole approach dead?

Tests (all on the same aligned Binance 15m panel, no look-ahead):

  1. X-SECTIONAL MOMENTUM  long the top-k past winners / short the bottom-k
  2. X-SECTIONAL REVERSAL  the same, sign-flipped
  3. TIME-SERIES MOMENTUM  long every coin whose past L-bar return > 0
  4. TIME-SERIES REVERSAL  the sign-flip of (3)

Reports per-rebalance expectancy in bps with a t-stat: |t| >= 2 means the
effect is probably real rather than luck.

    python3 tools/edge_hypotheses.py [days] [n_coins]
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CACHE = Path("/root/ict_sniper/data/research/binance_15m")
COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK",
         "UNI", "ARB", "OP", "NEAR", "ATOM", "FIL", "INJ", "SUI", "APT",
         "SEI", "TIA", "MINA", "SAGA", "ACE", "LTC", "DOT", "MATIC", "TON",
         "PEPE", "WIF", "ORDI"]


def load(days: int, want: int):
    """Aligned close-price panel: {t: {coin: close}} plus the sorted times."""
    panel: dict[int, dict[str, float]] = {}
    used = []
    for c in COINS[:want]:
        f = CACHE / f"{c}_{days}d.json"
        if not f.exists():
            continue
        bars = json.loads(f.read_text())
        if len(bars) < 500:
            continue
        used.append(c)
        for b in bars:
            panel.setdefault(int(b["t"]), {})[c] = float(b["c"])
    ts = sorted(panel)
    # keep only timestamps where every used coin printed
    ts = [t for t in ts if len(panel[t]) == len(used)]
    return ts, panel, used


def stats(xs):
    n = len(xs)
    if n < 3:
        return 0.0, 0.0, 0.0, n
    mu = sum(xs) / n
    var = sum((x - mu) ** 2 for x in xs) / (n - 1)
    sd = math.sqrt(var) if var > 0 else 0.0
    t = mu / (sd / math.sqrt(n)) if sd > 0 else 0.0
    win = sum(1 for x in xs if x > 0) / n * 100
    return mu, t, win, n


def main(days: int, want: int) -> int:
    ts, panel, used = load(days, want)
    if len(ts) < 500:
        print("not enough aligned data (run edge_diagnosis first to cache)")
        return 1
    print("panel: %d coins x %d bars (%d days of 15m)\n"
          % (len(used), len(ts), len(ts) // 96))

    BARS = {  # label -> 15m bars
        "4h": 16, "12h": 48, "24h": 96, "48h": 192, "72h": 288,
    }
    HOLD = {"12h": 48, "24h": 96, "48h": 192}

    print("=" * 74)
    print("1/2. CROSS-SECTIONAL (long top-k past winners, short bottom-k)")
    print("=" * 74)
    print("%-10s %-8s | %-24s | %-24s" % ("lookback", "hold",
                                          "MOMENTUM exp bps (t)",
                                          "REVERSAL exp bps (t)"))
    for lb_name, lb in BARS.items():
        for hd_name, hd in HOLD.items():
            mom, rev = [], []
            for i in range(lb, len(ts) - hd, hd):
                t0, t1 = ts[i], ts[i + hd]
                past = {}
                for c in used:
                    p0, p1 = panel[ts[i - lb]].get(c), panel[t0].get(c)
                    if p0 and p1:
                        past[c] = p1 / p0 - 1.0
                if len(past) < 6:
                    continue
                order = sorted(past, key=lambda c: past[c])
                k = max(2, len(order) // 3)
                longs, shorts = order[-k:], order[:k]
                lr = [panel[t1][c] / panel[t0][c] - 1.0 for c in longs
                      if panel[t1].get(c) and panel[t0].get(c)]
                sr = [panel[t1][c] / panel[t0][c] - 1.0 for c in shorts
                      if panel[t1].get(c) and panel[t0].get(c)]
                if not lr or not sr:
                    continue
                m = (sum(lr) / len(lr) - sum(sr) / len(sr)) * 1e4
                mom.append(m)
                rev.append(-m)
            if not mom:
                continue
            mm, mt, mw, mn = stats(mom)
            rm, rt, rw, rn = stats(rev)
            print("%-10s %-8s | %+8.1f bps (t=%+5.2f, n=%3d) | "
                  "%+8.1f bps (t=%+5.2f, n=%3d)"
                  % (lb_name, hd_name, mm, mt, mn, rm, rt, rn))

    print("\n" + "=" * 74)
    print("3/4. TIME-SERIES (per coin, equal weight)")
    print("=" * 74)
    print("%-10s %-8s | %-24s | %-24s" % ("lookback", "hold",
                                          "MOMENTUM exp bps (t)",
                                          "REVERSAL exp bps (t)"))
    for lb_name, lb in BARS.items():
        for hd_name, hd in HOLD.items():
            mom, rev = [], []
            for i in range(lb, len(ts) - hd, hd):
                t0, t1 = ts[i], ts[i + hd]
                rs = []
                for c in used:
                    p0, p1, p2 = (panel[ts[i - lb]].get(c), panel[t0].get(c),
                                  panel[t1].get(c))
                    if not (p0 and p1 and p2):
                        continue
                    past = p1 / p0 - 1.0
                    fwd = p2 / p1 - 1.0
                    rs.append(fwd if past > 0 else -fwd)
                if rs:
                    m = sum(rs) / len(rs) * 1e4
                    mom.append(m)
                    rev.append(-m)
            if not mom:
                continue
            mm, mt, mw, mn = stats(mom)
            rm, rt, rw, rn = stats(rev)
            print("%-10s %-8s | %+8.1f bps (t=%+5.2f, n=%3d) | "
                  "%+8.1f bps (t=%+5.2f, n=%3d)"
                  % (lb_name, hd_name, mm, mt, mn, rm, rt, rn))
    # ---- out-of-sample split on the surviving cross-sectional configs ----
    print("\n" + "=" * 74)
    print("OUT-OF-SAMPLE SPLIT (cross-sectional momentum, half 1 vs half 2)")
    print("=" * 74)
    half = len(ts) // 2
    for lb_name, lb in BARS.items():
        if lb_name == "4h":
            continue
        for hd_name, hd in HOLD.items():
            out = []
            for seg, (lo_i, hi_i) in (("1st", (0, half)), ("2nd", (half, len(ts)))):
                xs = []
                for i in range(lo_i + lb, hi_i - hd, hd):
                    t0, t1 = ts[i], ts[i + hd]
                    past = {}
                    for c in used:
                        p0, p1 = panel[ts[i - lb]].get(c), panel[t0].get(c)
                        if p0 and p1:
                            past[c] = p1 / p0 - 1.0
                    if len(past) < 6:
                        continue
                    order = sorted(past, key=lambda c: past[c])
                    k = max(2, len(order) // 3)
                    lr = [panel[t1][c] / panel[t0][c] - 1.0 for c in order[-k:]
                          if panel[t1].get(c) and panel[t0].get(c)]
                    sr = [panel[t1][c] / panel[t0][c] - 1.0 for c in order[:k]
                          if panel[t1].get(c) and panel[t0].get(c)]
                    if lr and sr:
                        xs.append((sum(lr) / len(lr) - sum(sr) / len(sr)) * 1e4)
                out.append(stats(xs))
            (m1, t1s, _, n1), (m2, t2s, _, n2) = out
            flag = "OK" if (m1 > 0 and m2 > 0) else "--"
            print("%-6s/%-6s 1st %+7.1f bps (t=%+5.2f n=%3d) | "
                  "2nd %+7.1f bps (t=%+5.2f n=%3d)  %s"
                  % (lb_name, hd_name, m1, t1s, n1, m2, t2s, n2, flag))
    print("\nnote: |t| >= 2 suggests a real effect; costs ~3 bps round trip")
    return 0


if __name__ == "__main__":
    d = int(sys.argv[1]) if len(sys.argv) > 1 else 180
    c = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    raise SystemExit(main(d, c))
