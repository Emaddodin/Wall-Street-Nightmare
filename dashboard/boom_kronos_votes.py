"""Kronos up-probability at the candles where the smart money setup layer completed, for boom_backtest.py --kronos.

    python3 boom_backtest.py data/dukascopy_xauusd_m1.csv.gz --dump setups.txt
    python3 boom_kronos_votes.py data/dukascopy_xauusd_m1.csv.gz setups.txt votes.csv --repo /root/kronos/Kronos --size base

Each forecast reads the 400 M5 candles up to and including the setup candle (nothing after it) and draws
--paths sample paths 24 candles (2 hours) ahead, as the live page does. Resumes: times already in the output
file are skipped, so it can be stopped and started again.
"""
from __future__ import annotations

import argparse
import csv
import time
from bisect import bisect_right
from pathlib import Path

from boom_backtest import aggregate, load
from kronos_signal import DEFAULT_REPO, HORIZON, Kronos


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("setups")
    ap.add_argument("out")
    ap.add_argument("--repo", default=str(DEFAULT_REPO))
    ap.add_argument("--size", default="small", choices=["mini", "small", "base"])
    ap.add_argument("--paths", type=int, default=10)
    ap.add_argument("--lookback", type=int, default=400)
    a = ap.parse_args()

    m5 = aggregate(load(a.csv), 300)
    rows = [[m5.t[i], m5.o[i], m5.h[i], m5.l[i], m5.c[i], m5.v[i]] for i in range(len(m5))]
    want = [int(x) for x in Path(a.setups).read_text().split()[1:]]
    done = set()
    out = Path(a.out)
    if out.exists():
        with open(out, newline="") as f:
            done = {int(r["time"]) for r in csv.DictReader(f)}
    todo = [t for t in want if t not in done]
    print(f"{len(want)} setup candles, {len(todo)} to forecast with Kronos-{a.size}, {a.paths} paths each")
    k = Kronos(a.repo, a.size, a.lookback, HORIZON["M5"], 1, 0.5)
    new = not out.exists()
    with open(out, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "up_prob", "move", "atr"])
        t0 = time.time()
        for n, t in enumerate(todo, 1):
            i = bisect_right(m5.t, t) - 1
            if i < a.lookback or m5.t[i] != t:
                continue
            fc = k.forecast(rows[i - a.lookback + 1:i + 1], step=300, paths=a.paths)
            w.writerow([t, fc["up_prob"], fc["move"], fc["atr"]])
            f.flush()
            print(f"  {n}/{len(todo)}  ({(time.time() - t0) / n:.1f} s each)", end="\r", flush=True)
    print(f"\nDone: {out.resolve()}")


if __name__ == "__main__":
    main()
