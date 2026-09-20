"""Vol-scaled fade games: TP/SL proportional to each symbol's current
volatility instead of fixed bps.

Setup: |z15| >= min_z (vol-normalized extreme 15m move at 5m bars).
Exit: fade-direction target = k_tp * sigma_12, stop = k_sl * sigma_12,
where sigma_12 = 5m-bar std * sqrt(12) (1h vol).  Per-bar dynamic
thresholds; exact first-hit ordering; horizon 12 bars (1h).

Also computes the maker/taker break-even win rate per (k_tp, k_sl) so the
gap between observed and required is visible directly.

Usage:
  python3 quant/tools/volscaled.py --out data/research/volscaled.csv
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.lib import store, features  # noqa: E402
from quant.tools.discover5m import resample_5m  # noqa: E402

log = logging.getLogger("quant.tools.volscaled")

GRIDS = [(1.5, 1.0), (2.0, 1.0), (2.0, 1.25), (2.5, 1.25), (3.0, 1.5),
         (2.0, 1.5), (3.0, 2.0)]
HH = 12  # 1h horizon in 5m bars


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--symbols", default=None)
    ap.add_argument("--min-bars", type=int, default=30_000)
    ap.add_argument("--min-z", type=float, default=1.5)
    args = ap.parse_args()

    root = store.data_root()
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        uni = json.loads(open(root / "universe.json").read())
        symbols = sorted(uni["symbols"])

    rows = []
    t0 = time.time()
    for si, sym in enumerate(symbols):
        try:
            df = store.load_klines(sym, root)
        except FileNotFoundError:
            continue
        if len(df) < args.min_bars * 5:
            continue
        d5 = resample_5m(df)
        if len(d5) < args.min_bars:
            continue
        c = d5["close"].to_numpy(dtype=float)
        h = d5["high"].to_numpy(dtype=float)
        l = d5["low"].to_numpy(dtype=float)
        n = len(c)
        r1 = np.full(n, np.nan)
        r1[1:] = np.log(c[1:] / c[:-1])
        r3 = np.full(n, np.nan)
        r3[3:] = np.log(c[3:] / c[:-3])
        sd = pd.Series(r1).rolling(288).std().to_numpy()
        z = r3 / np.maximum(sd * np.sqrt(3), 1e-9)
        logc = np.log(c)
        logh = np.log(h)
        logl = np.log(l)
        mask = (np.abs(z) >= args.min_z) & np.isfinite(z)
        mask[:n - HH] &= True
        mask[n - HH:] = False
        sgn = np.sign(r3)
        # per-bar thresholds in log space, fade direction = -sgn
        for k_tp, k_sl in GRIDS:
            sig12 = sd * np.sqrt(HH)           # log-space 1h vol
            tp_log = k_tp * sig12
            sl_log = k_sl * sig12
            hit_tp = np.zeros(n, dtype=bool)
            hit_sl = np.zeros(n, dtype=bool)
            t_tp = np.full(n, np.inf)
            t_sl = np.full(n, np.inf)
            for k in range(1, HH + 1):
                hi_k = np.roll(logh, -k)
                lo_k = np.roll(logl, -k)
                up_hit = hi_k - logc >= tp_log        # +side game
                dn_hit = logc - lo_k >= tp_log        # -side game
                up_sl = logc - lo_k >= sl_log
                dn_sl = hi_k - logc >= sl_log
                tp_hit = np.where(sgn < 0, up_hit, dn_hit)
                sl_hit = np.where(sgn < 0, up_sl, dn_sl)
                new_tp = (~hit_tp) & tp_hit
                new_sl = (~hit_sl) & sl_hit
                t_tp[new_tp] = k
                t_sl[new_sl] = k
                hit_tp |= new_tp
                hit_sl |= new_sl
            hit_tp[n - HH:] = False
            hit_sl[n - HH:] = False
            both = hit_tp & hit_sl
            win = hit_tp & (~hit_sl | (t_tp <= t_sl))
            m = mask & (hit_tp | hit_sl)
            # win rate among decided, non-overlapping subsample
            sub = np.zeros(n, dtype=bool)
            sub[::HH] = True
            dec = m & sub
            wins = win & dec
            if dec.sum() < 100:
                continue
            rows.append({
                "symbol": sym, "k_tp": k_tp, "k_sl": k_sl,
                "win": float(wins.sum() / dec.sum()),
                "dec_rate": float(dec.sum() / max(mask[sub].sum(), 1)),
                "n": int(dec.sum()),
            })
        log.info("[%s] done (%d/%d)", sym, si + 1, len(symbols))
    res = pd.DataFrame(rows)
    if args.out:
        res.to_csv(args.out, index=False)
    print(f"saved {len(res)} rows ({time.time()-t0:.0f}s)")
    print("\n=== pooled win rates by (k_tp, k_sl) ===")
    print(f"{'k_tp':>5} {'k_sl':>5} {'win':>7} {'dec':>7} {'n':>9}  "
          f"{'BE_t12@60':>10} {'BE_m4@60':>10} {'BE_t12@100':>11} "
          f"{'BE_m4@100':>11}")
    for (kt, ks), g in res.groupby(["k_tp", "k_sl"]):
        w = g["n"].to_numpy(dtype=float)
        win = float((g["win"] * w).sum() / w.sum())
        dec = float((g["dec_rate"] * w).sum() / w.sum())
        def be(sig_bps, cost_bps):
            c = cost_bps / sig_bps
            return (ks + c) / (kt + ks)
        print(f"{kt:5.1f} {ks:5.1f} {win:7.3f} {dec:7.3f} {int(w.sum()):9d}"
              f" {be(60, 12):10.3f} {be(60, 4):10.3f} {be(100, 12):11.3f}"
              f" {be(100, 4):11.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
