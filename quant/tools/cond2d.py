"""2D conditioning search for fade setups: which measurable state, at the
moment of an extreme 15m move, predicts the snap-back most reliably?

For each symbol (5m bars):
  base condition: |r3| >= min_z * vol (extreme 15m move)
  direction: fade up-moves (short) / fade down-moves (long)
  outcome: first-hit game g80x60 at 12 bars (1h), plus g50x40_3 (15m)
Report per conditioning feature (5 quantile bins) the fade win rate,
resolution rate, and lift vs the unconditional extreme-move fade.

Then, for the best single features, 2D grids (4x4) with pooled win rates.

Usage:
  python3 quant/tools/cond2d.py --symbols ... --out data/research/cond2d.csv
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
from quant.tools.discover5m import resample_5m, align5m, games5m  # noqa: E402

log = logging.getLogger("quant.tools.cond2d")

COND_FEATURES = ["hi_dist", "lo_dist", "hi_dist240", "lo_dist240",
                 "wick_up", "wick_dn", "rvol", "rvol_accel", "compress",
                 "expand", "vol_ratio", "oi_chg1", "oi_z", "taker_imb",
                 "taker_imb_z", "liq_net", "funding_z", "rs3", "r15",
                 "r60", "accel5", "slope60", "trend_dist", "range_frac",
                 "body_frac", "vpr"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-bars", type=int, default=30_000)
    ap.add_argument("--min-z", type=float, default=1.0)
    ap.add_argument("--game", default="g80x60_12")
    ap.add_argument("--min-cell", type=int, default=150)
    ap.add_argument("--pairs", default=None,
                    help="comma list of 'f1:f2' 2D cell pairs")
    args = ap.parse_args()

    root = store.data_root()
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        uni = json.loads(open(root / "universe.json").read())
        symbols = sorted(uni["symbols"])

    btc5 = None
    try:
        btc5 = features.compute_base(resample_5m(
            store.load_klines("BTCUSDT", root)))
    except FileNotFoundError:
        pass

    univ_rows = []
    rows = []
    pair_rows = []
    pairs = [(a.strip(), b.strip())
             for a, b in (x.split(":") for x in
                          (args.pairs or "").split(",") if x)]
    t0 = time.time()
    for si, sym in enumerate(symbols):
        t1 = time.time()
        try:
            df = store.load_klines(sym, root)
        except FileNotFoundError:
            continue
        if len(df) < args.min_bars * 5:
            continue
        d5 = resample_5m(df)
        if len(d5) < args.min_bars:
            continue
        metrics = funding = None
        try:
            metrics = store.load_metrics(sym, root)
        except FileNotFoundError:
            pass
        try:
            funding = store.load_funding(sym, root)
        except FileNotFoundError:
            pass
        feat = features.compute_base(d5)
        feat = align5m(feat, metrics, funding)
        if btc5 is not None and sym != "BTCUSDT":
            bb = btc5[["open_time", "r3", "r15"]]
            bb = bb.rename(columns={c: f"btc_{c}" for c in bb.columns
                                    if c != "open_time"})
            feat = feat.merge(bb, on="open_time", how="left")
            feat["rs3"] = feat["r3"] - feat["btc_r3"]
        out = games5m(d5, specs=[(50, 40, 3), (80, 60, 12), (120, 80, 12)])
        game = out[args.game].to_numpy()
        # vol-normalized 15m z
        r1 = np.full(len(d5), np.nan)
        c = d5["close"].to_numpy(dtype=float)
        r1[1:] = np.log(c[1:] / c[:-1])
        sd = pd.Series(r1).rolling(288).std().to_numpy()
        z3 = feat["r3"].to_numpy() / np.maximum(sd * np.sqrt(3), 1e-9)
        # base condition: extreme move; direction-specific fade win rates
        r3 = feat["r3"].to_numpy()
        up = (z3 >= args.min_z) & (r3 > 0)      # fade = short
        dn = (z3 <= -args.min_z) & (r3 < 0)     # fade = long
        sub = np.zeros(len(d5), dtype=bool)
        sub[::12] = True                        # non-overlap at 1h horizon
        base = (up | dn) & sub & np.isfinite(game) & (game != 0)
        n_base = int(base.sum())
        base_win = float((game[base] == 1).mean()) if n_base else np.nan
        univ_rows.append({"symbol": sym, "n_base": n_base,
                          "base_win": base_win})
        if n_base < args.min_cell:
            continue
        # 2D pair cells: both features in their top/bottom quintile
        for f1, f2 in pairs:
            if f1 not in feat.columns or f2 not in feat.columns:
                continue
            x1 = feat[f1].to_numpy(dtype=float)
            x2 = feat[f2].to_numpy(dtype=float)
            ok = base & np.isfinite(x1) & np.isfinite(x2)
            if ok.sum() < args.min_cell * 8:
                continue
            q1_hi = np.nanquantile(x1[ok], 0.8)
            q1_lo = np.nanquantile(x1[ok], 0.2)
            q2_hi = np.nanquantile(x2[ok], 0.8)
            q2_lo = np.nanquantile(x2[ok], 0.2)
            for tag, m in (("hihi", ok & (x1 >= q1_hi) & (x2 >= q2_hi)),
                           ("hilo", ok & (x1 >= q1_hi) & (x2 <= q2_lo)),
                           ("lohi", ok & (x1 <= q1_lo) & (x2 >= q2_hi)),
                           ("lolo", ok & (x1 <= q1_lo) & (x2 <= q2_lo))):
                gv = game[m]
                dec = gv[gv != 0]
                if len(dec) < args.min_cell:
                    continue
                pair_rows.append({"symbol": sym, "pair": f"{f1}:{f2}",
                                  "cell": tag,
                                  "win": float((dec == 1).mean()),
                                  "dec_rate": len(dec) / max(m.sum(), 1),
                                  "n": int(m.sum())})
        for f in COND_FEATURES:
            if f not in feat.columns:
                continue
            x = feat[f].to_numpy(dtype=float)
            ok = base & np.isfinite(x)
            if ok.sum() < args.min_cell * 3:
                continue
            qs = np.nanquantile(x[ok], [0.2, 0.4, 0.6, 0.8])
            for b in range(5):
                if b == 0:
                    m = ok & (x <= qs[0])
                elif b == 4:
                    m = ok & (x > qs[3])
                else:
                    m = ok & (x > qs[b - 1]) & (x <= qs[b])
                gv = game[m]
                dec = gv[gv != 0]
                if len(dec) < args.min_cell:
                    continue
                rows.append({"symbol": sym, "feature": f, "bin": b,
                             "win": float((dec == 1).mean()),
                             "dec_rate": len(dec) / max(m.sum(), 1),
                             "n": int(m.sum())})
        log.info("[%s] done in %.1fs (%d/%d)", sym, time.time() - t1,
                 si + 1, len(symbols))

    res = pd.DataFrame(rows)
    res.to_csv(args.out, index=False)
    log.info("saved %d rows -> %s (%.1f min)", len(res), args.out,
             (time.time() - t0) / 60)

    # pooled base rates
    univ = pd.DataFrame(univ_rows)
    print("=== base fade win rates (pooled over symbols) ===")
    if len(univ):
        w = univ["n_base"].to_numpy(dtype=float)
        base_all = float((univ['base_win'] * w).sum() / w.sum())
        print(f"  pooled base_win = {base_all:.3f} "
              f" n={int(w.sum())} across {len(univ)} symbols")

    if len(pair_rows):
        print("\n=== 2D pair cells (pooled) ===")
        pr = pd.DataFrame(pair_rows)
        pool = []
        for (pair, cell), g in pr.groupby(["pair", "cell"]):
            w = g["n"].to_numpy(dtype=float)
            pool.append({"pair": pair, "cell": cell,
                         "win": float((g["win"] * w).sum() / w.sum()),
                         "n": int(w.sum()), "n_sym": len(g)})
        pool = pd.DataFrame(pool)
        pool["lift"] = pool["win"] - base_all
        pool = pool.sort_values("win", ascending=False)
        with pd.option_context("display.max_rows", 60, "display.width",
                               160):
            print(pool.head(40).round(3).to_string(index=False))

    if len(res) == 0:
        return 0
    print("\n=== per-feature bin win rates (pooled, top bins) ===")
    pool = []
    for (f, b), g in res.groupby(["feature", "bin"]):
        w = g["n"].to_numpy(dtype=float)
        win = float((g["win"] * w).sum() / w.sum())
        pool.append({"feature": f, "bin": b, "win": win,
                     "n": int(w.sum()), "n_sym": len(g)})
    pool = pd.DataFrame(pool)
    base_all = float((univ["base_win"] * univ["n_base"]).sum() /
                     univ["n_base"].sum()) if len(univ) else np.nan
    pool["lift"] = pool["win"] - base_all
    pool = pool.sort_values("win", ascending=False)
    with pd.option_context("display.max_rows", 80, "display.width", 160):
        print(pool.head(40).round(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
