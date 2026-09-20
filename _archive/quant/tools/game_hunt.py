"""Game hunter: which measurable conditions push the TP/SL first-hit game
win probability materially above base rates?

For each symbol, for each feature, bucketed P(win) of the game
g{tp}x{sl}_15, plus the threshold-conditional P(win) for monotone tails.
Reports, per feature, the best achievable win-rate bucket with its size
and consistency across symbols.

Also prints, per symbol, base win rates for each game so cost-adjusted
break-even can be compared directly (taker round trip ~10-12 bps).

Usage:
  python3 quant/tools/game_hunt.py --symbols ... --games 30x20,50x30,80x50 \
      --features r5,rvol_accel,hi_dist --min-bars 50000
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.lib import store, features, outcomes  # noqa: E402

log = logging.getLogger("quant.tools.game_hunt")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=None)
    ap.add_argument("--games", default="30x20,50x30,80x50,30x30")
    ap.add_argument("--features", default=None)
    ap.add_argument("--min-bars", type=int, default=50_000)
    ap.add_argument("--no-derivatives", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    root = store.data_root()
    import json
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        uni = json.loads(open(root / "universe.json").read())
        symbols = sorted(uni["symbols"])
    games = [g.strip() for g in args.games.split(",") if g.strip()]

    btc_feat = None
    try:
        btc_feat = features.compute_base(store.load_klines("BTCUSDT", root))
    except FileNotFoundError:
        pass

    base_rows = []
    tail_rows = []
    for sym in symbols:
        try:
            df = store.load_klines(sym, root)
        except FileNotFoundError:
            continue
        if len(df) < args.min_bars:
            continue
        metrics = funding = None
        if not args.no_derivatives:
            try:
                metrics = store.load_metrics(sym, root)
            except FileNotFoundError:
                pass
            try:
                funding = store.load_funding(sym, root)
            except FileNotFoundError:
                pass
        feat = features.compute_symbol(sym, df, metrics, funding, btc_feat)
        out = outcomes.compute_outcomes(df)
        for g in games:
            col = f"g{g}_15"
            if col not in out.columns:
                continue
            gv = out[col].to_numpy()
            # base win rate (non-overlapping stride 15)
            sub = np.zeros(len(gv), dtype=bool)
            sub[::15] = True
            ok = sub & np.isfinite(gv) & (gv != 0)
            base = (gv[ok] == 1).mean() if ok.sum() > 0 else np.nan
            base_rows.append({"symbol": sym, "game": g, "base_win": base,
                              "n": int(ok.sum())})
            # per-feature tail win rates: top and bottom 5% of feature
            cols = args.features.split(",") if args.features else \
                [c for c in feat.columns if c != "open_time"]
            for f in cols:
                if f not in feat.columns:
                    continue
                x = feat[f].to_numpy(dtype=float)
                m = ok & np.isfinite(x)
                if m.sum() < 2000:
                    continue
                for side_name, msk in (
                        ("lo5", m & (x <= np.nanquantile(x[m], 0.05))),
                        ("hi5", m & (x >= np.nanquantile(x[m], 0.95))),
                        ("lo10", m & (x <= np.nanquantile(x[m], 0.10))),
                        ("hi10", m & (x >= np.nanquantile(x[m], 0.90)))):
                    n = int(msk.sum())
                    if n < 200:
                        continue
                    w = float((gv[msk] == 1).mean())
                    tail_rows.append({"symbol": sym, "game": g,
                                      "feature": f, "tail": side_name,
                                      "win": w, "n": n})
        log.info("[%s] done", sym)

    base = pd.DataFrame(base_rows)
    print("=== BASE WIN RATES (per game, pooled) ===")
    if len(base):
        bpool = (base.groupby("game")
                 .apply(lambda g: pd.Series({
                     "base_win": (g["base_win"] * g["n"]).sum() / g["n"].sum(),
                     "n": g["n"].sum()}), include_groups=False))
        print(bpool.round(4))

    tails = pd.DataFrame(tail_rows)
    if args.out and len(tails):
        tails.to_csv(args.out, index=False)
        print(f"saved tails -> {args.out}")
    if len(tails) == 0:
        return 0

    # lift vs the symbol's own base rate
    mb = base.set_index(["symbol", "game"])["base_win"]
    tails["lift"] = tails["win"] - [mb.get((s, g), np.nan)
                                    for s, g in zip(tails["symbol"],
                                                    tails["game"])]
    # best tail per (feature, game) pooled across symbols
    print("\n=== BEST TAILS (top 25 by pooled lift) ===")
    rows = []
    for (f, g, tail), grp in tails.groupby(["feature", "game", "tail"]):
        w = grp["n"].to_numpy(dtype=float)
        lift = float((grp["lift"] * w).sum() / w.sum())
        win = float((grp["win"] * w).sum() / w.sum())
        consistency = float((np.sign(grp["lift"]) == np.sign(lift)).mean())
        rows.append({"feature": f, "game": g, "tail": tail, "win": win,
                     "lift": lift, "n": int(w.sum()),
                     "consistency": consistency, "n_sym": len(grp)})
    best = pd.DataFrame(rows).sort_values("lift", key=abs, ascending=False)
    with pd.option_context("display.max_rows", 60, "display.width", 200,
                           "display.float_format", "{:.3f}".format):
        print(best.head(25))
    return 0


if __name__ == "__main__":
    sys.exit(main())
