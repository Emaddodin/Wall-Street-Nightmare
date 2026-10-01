"""
scripts/runner_flip.py
======================
Invented flip strategy: VOLATILITY-BREAKOUT RUNNER (convex payoff: small fixed loss, uncapped trailing winner).

  Signal : a completed 5m bar closes beyond the prior N-bar (5m) high/low, and 1m ATR(14) >= atr_min (the move is
           big relative to the $0.18 spread), optional session filter.
  Entry  : next 1m open, market, paying half the spread.
  Exit   : initial stop `stop` pts; once price runs `stop` pts in favour the stop moves to breakeven+0.3; after that
           it trails `trail` pts behind the best price. No profit cap. Time stop `max_min` minutes.
  Fills  : conservative intrabar order - the stop is checked before the bar's favourable extreme is credited.

Grid search on real 1m gold candles, split in two halves for walk-forward consistency, then flip odds ($12.47 -> $100).
"""
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SPREAD = 0.18
SLIP = 0.05
ENTRY_SLIP = 0.0
DIR = sys.argv[1] if len(sys.argv) > 1 else "data/candles/real_bt2"


def load():
    O, H, L, C, T, D = [], [], [], [], [], []
    for di, f in enumerate(sorted(Path(ROOT / DIR).glob("gold_m1_*.csv"))):
        d = pd.read_csv(f, usecols=["open_time", "open", "high", "low", "close"])
        O += d.open.tolist(); H += d.high.tolist(); L += d.low.tolist(); C += d.close.tolist()
        T += (d.open_time / 1000).tolist(); D += [di] * len(d)
    return [np.array(x) for x in (O, H, L, C, T, D)]


def five_min(O, H, L, C, T):
    b = (T // 300).astype(np.int64)
    idx = np.flatnonzero(np.r_[True, b[1:] != b[:-1]])
    ends = np.r_[idx[1:], len(T)]
    return idx, ends  # bar k covers 1m indices idx[k]..ends[k]-1


def signals(H, L, C, T, idx, ends, n, atr_min, atr14, hours):
    sig = []
    h5 = np.array([H[s:e].max() for s, e in zip(idx, ends)])
    l5 = np.array([L[s:e].min() for s, e in zip(idx, ends)])
    c5 = np.array([C[e - 1] for e in ends])
    for k in range(n, len(idx) - 1):
        e = ends[k]
        if e >= len(C) - 2 or (ends[k] - idx[k]) < 4:
            continue
        # skip bars that span a data gap (weekend)
        if T[e - 1] - T[idx[k]] > 330:
            continue
        hr = int((T[e - 1] % 86400) // 3600)
        if hours and not (hours[0] <= hr < hours[1]):
            continue
        if atr14[e - 1] < atr_min:
            continue
        if c5[k] > h5[k - n:k].max():
            sig.append((e, 1))
        elif c5[k] < l5[k - n:k].min():
            sig.append((e, -1))
    return sig


def run(O, H, L, C, T, sig, stop, trail, max_min):
    trades, busy = [], -1
    N = len(C)
    for e, d in sig:
        if e <= busy:
            continue
        entry = O[e] + d * (SPREAD / 2 + ENTRY_SLIP)
        sl = entry - d * stop
        best = entry
        armed = False
        exit_px, end = None, None
        for j in range(e, min(N, e + max_min)):
            if T[j] - T[e] > max_min * 60 + 60:   # data gap
                exit_px, end = C[j - 1] - d * SPREAD / 2, j - 1
                break
            lo, hi = (L[j], H[j]) if d == 1 else (H[j], L[j])   # adverse, favourable extremes
            adv = lo - d * SPREAD / 2 if d == 1 else lo + SPREAD / 2 * 1
            # long exits at bid (px - s/2); short exits at ask (px + s/2)
            adv_px = lo - SPREAD / 2 if d == 1 else lo + SPREAD / 2
            if (d == 1 and adv_px <= sl) or (d == -1 and adv_px >= sl):
                exit_px, end = sl - d * SLIP, j
                break
            fav = hi
            best = max(best, fav) if d == 1 else min(best, fav)
            if not armed and d * (best - entry) >= stop:
                armed = True
                sl = entry + d * 0.3
            if armed:
                nsl = best - d * trail
                sl = max(sl, nsl) if d == 1 else min(sl, nsl)
        if exit_px is None:
            j = min(N - 1, e + max_min - 1)
            exit_px, end = C[j] - d * SPREAD / 2, j
        trades.append((e, d * (exit_px - entry), end))
        busy = end
    return trades


def stats(tr, T, split):
    p = np.array([x[1] for x in tr])
    if len(p) < 30:
        return None
    def pf(a):
        return a[a > 0].sum() / -a[a < 0].sum() if (a < 0).any() and (a > 0).any() else 0
    idx = np.array([x[0] for x in tr])
    h1, h2 = p[idx < split], p[idx >= split]
    return dict(n=len(p), wr=round((p > 0).mean() * 100, 1), avg=round(p.mean(), 3), pf=round(pf(p), 2),
                pf1=round(pf(h1), 2), pf2=round(pf(h2), 2), avg_win=round(p[p > 0].mean(), 2), avg_loss=round(p[p < 0].mean(), 2))


def main():
    O, H, L, C, T, D = load()
    print("bars", len(C), "days", D.max() + 1, flush=True)
    idx, ends = five_min(O, H, L, C, T)
    rng = H - L
    atr14 = pd.Series(rng).rolling(14, min_periods=1).mean().to_numpy()
    split = len(C) // 2
    rows = []
    for n, atr_min, hours in itertools.product((6, 12, 24), (1.0, 1.5, 2.0), (None, (7, 17))):
        sig = signals(H, L, C, T, idx, ends, n, atr_min, atr14, hours)
        for stop, trail, mm in itertools.product((1.0, 1.5, 2.5), (1.0, 2.0, 3.5), (30, 90)):
            tr = run(O, H, L, C, T, sig, stop, trail, mm)
            s = stats(tr, T, split)
            if s:
                rows.append(dict(lookback=n, atr_min=atr_min, hours=str(hours), stop=stop, trail=trail, max_min=mm, **s))
    df = pd.DataFrame(rows)
    df["score"] = np.minimum(df.pf1, df.pf2)
    df = df.sort_values("score", ascending=False)
    pd.set_option("display.width", 250)
    print(df.head(15).to_string(index=False))
    df.to_csv(ROOT / "data/runner_grid.csv", index=False)


if __name__ == "__main__":
    main()
