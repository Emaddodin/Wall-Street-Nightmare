"""Momentum continuation on explosive alt moves (1m resolution).

Hypothesis to test: on high-volatility alts (NOT the top majors), a sharp
1-5m move on an extreme volume shock CONTINUES over the next few minutes
(FOMO/chasing), unlike the mean-reverting majors.  If true, this is the
high-ROE engine: 30-80bp continuation at 20-50x leverage.

Measures, for symbols with rv60 above a threshold:
  events = rvol_accel >= k AND |r5| >= m * rv5
  outcomes = fwd_1/3/5/10/15 in the MOVE direction (signed by event dir),
             plus first-hit game in the move direction (g30x20_15 etc.)
Pooled over symbols and months; also split by symbol vol tercile.

Usage:
  python3 quant/tools/momo_alts.py --out data/research/momo_alts.csv
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

from quant.lib import store, features, outcomes  # noqa: E402

log = logging.getLogger("quant.tools.momo_alts")

# exclude the mega-caps (mean-reverting core) from the "alts" universe
MAJOR_EXCLUDE = {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--symbols", default=None)
    ap.add_argument("--min-bars", type=int, default=100_000)
    ap.add_argument("--rvol-min", type=float, default=2.5)
    ap.add_argument("--move-min", type=float, default=1.2,
                    help="min |r5| in units of rv5")
    ap.add_argument("--vol-tercile", type=int, default=0,
                    help="0=all, 1=low-vol third only, 2=mid, 3=high")
    args = ap.parse_args()

    root = store.data_root()
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        uni = json.loads(open(root / "universe.json").read())
        symbols = sorted(s for s in uni["symbols"]
                         if s not in MAJOR_EXCLUDE)

    # first pass: per-symbol rv60 to compute vol terciles
    vol_rows = []
    for sym in symbols:
        try:
            df = store.load_klines(sym, root)
        except FileNotFoundError:
            continue
        if len(df) < args.min_bars:
            continue
        f = features.compute_base(df.iloc[::5])  # 5m-sampled
        rv = np.nanmedian(f["rv60"].to_numpy())
        vol_rows.append((sym, rv))
    if len(vol_rows) < 6:
        print("not enough symbols")
        return 1
    vol = pd.DataFrame(vol_rows, columns=["symbol", "rv"])
    vol["tercile"] = pd.qcut(vol["rv"], 3, labels=[1, 2, 3])
    tercile_of = dict(zip(vol["symbol"], vol["tercile"]))

    rows = []
    t0 = time.time()
    for sym, rv in vol_rows:
        tc = int(tercile_of[sym])
        if args.vol_tercile and tc != args.vol_tercile:
            continue
        try:
            df = store.load_klines(sym, root)
        except FileNotFoundError:
            continue
        feat = features.compute_base(df)
        out = outcomes.compute_outcomes(df)
        r5 = feat["r5"].to_numpy()
        rv5 = feat["rv5"].to_numpy()
        rvol = feat["rvol_accel"].to_numpy()
        mask = (np.isfinite(r5) & np.isfinite(rv5) & np.isfinite(rvol) &
                (rvol >= args.rvol_min) &
                (np.abs(r5) >= args.move_min * rv5))
        # direction-signed forward returns
        sgn = np.sign(r5)
        for h in (1, 3, 5, 10, 15):
            fwd = out[f"fwd_{h}"].to_numpy() * sgn
            m = mask & np.isfinite(fwd)
            if m.sum() < 200:
                continue
            rows.append({
                "symbol": sym, "tercile": tc, "horizon": h,
                "n": int(m.sum()),
                "mean_dir_ret": float(fwd[m].mean()),
                "p_dir_up": float((fwd[m] > 0).mean()),
                "t": float(fwd[m].mean() /
                           fwd[m].std() * np.sqrt(m.sum() /
                                                  max(h, 1))),
            })
        # game in move direction
        for g in ("g30x20_15", "g50x30_15", "g80x60_60", "g120x80_60"):
            gv = out[g].to_numpy()
            if g.endswith("_15"):
                m = mask & np.isfinite(gv) & (gv != 0)
            else:
                m = mask & np.isfinite(gv) & (gv != 0)
            if m.sum() < 200:
                continue
            # mirror the game: g is computed from bar t forward in the
            # +/- direction; in move direction win = (gv==1)
            rows.append({
                "symbol": sym, "tercile": tc, "horizon": -1,
                "n": int(m.sum()),
                "mean_dir_ret": float((gv[m] == 1).mean()),
                "p_dir_up": float((gv[m] != 0).mean()),
                "t": float(np.nan),
            })
            rows[-1]["game"] = g
        log.info("[%s] tercile %d done", sym, tc)
    res = pd.DataFrame(rows)
    if args.out:
        res.to_csv(args.out, index=False)
    print(f"saved {len(res)} rows ({time.time()-t0:.0f}s)")
    # pool by horizon and tercile
    print("\n=== pooled direction-signed forward returns (bps) ===")
    for tc in (0, 1, 2, 3):
        sub = res[(res["horizon"] > 0)]
        if tc:
            sub = sub[sub["tercile"] == tc]
        if not len(sub):
            continue
        print(f"--- vol tercile {tc or 'ALL'} ---")
        for h in (1, 3, 5, 10, 15):
            g = sub[sub["horizon"] == h]
            if not len(g):
                continue
            w = g["n"].to_numpy(dtype=float)
            mean = float((g["mean_dir_ret"] * w).sum() / w.sum())
            print(f"  h={h:2d}  n={int(w.sum()):9d}  mean={mean:7.2f} bps"
                  f"  p_up={float((g['p_dir_up']*w).sum()/w.sum()):.3f}")
        print()
    print("=== games in move direction ===")
    for tc in (0, 1, 2, 3):
        sub = res[(res["horizon"] == -1)]
        if tc:
            sub = sub[sub["tercile"] == tc]
        if not len(sub):
            continue
        print(f"--- vol tercile {tc or 'ALL'} ---")
        for gname, g in sub.groupby("game"):
            w = g["n"].to_numpy(dtype=float)
            print(f"  {gname:12s} n={int(w.sum()):9d} "
                  f"win={float((g['mean_dir_ret']*w).sum()/w.sum()):.3f} "
                  f"dec={float((g['p_dir_up']*w).sum()/w.sum()):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
