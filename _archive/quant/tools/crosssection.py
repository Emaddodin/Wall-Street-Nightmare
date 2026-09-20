"""Cross-sectional selection research: at each 5m timestamp, rank symbols
by a feature (momentum, relative strength, volume surge, ...) and measure
forward outcomes of the top/bottom quintiles vs the middle.

This answers: does TRADING THE RANKED EXTREME (not an absolute threshold)
produce a better conditional distribution?

Usage:
  python3 quant/tools/crosssection.py --feature r15 \
      --outcome fwd_15 --top 5 --bottom 5
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

from quant.lib import store, features  # noqa: E402
from quant.research.panel import build_panel  # noqa: E402

log = logging.getLogger("quant.tools.crosssection")

STEP = 300_000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=None)
    ap.add_argument("--feature", default="r15",
                    help="cross-sectional ranking feature (computed on the "
                         "5m panel): r5, r15, r30, rvol5, ret_ratio ...")
    ap.add_argument("--outcome", default="fwd_15",
                    help="forward outcome: fwd_5, fwd_15, fwd_30")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--bottom", type=int, default=5)
    ap.add_argument("--min-symbols", type=int, default=12)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    root = store.data_root()
    import json
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        uni = json.loads(open(root / "universe.json").read())
        symbols = sorted(uni["symbols"])

    panel = build_panel(symbols, root)
    if panel.empty:
        print("empty panel")
        return 1
    log.info("panel rows: %d", len(panel))

    # per-symbol 5m returns and volumes
    p = panel.copy()
    p["logc"] = np.log(p["close"])
    p["ret5"] = (p.groupby("symbol")["logc"].diff() * 10_000.0)
    p["r15"] = (p.groupby("symbol")["logc"].diff(3) * 10_000.0)
    p["r30"] = (p.groupby("symbol")["logc"].diff(6) * 10_000.0)
    p["r60"] = (p.groupby("symbol")["logc"].diff(12) * 10_000.0)
    p["r240"] = (p.groupby("symbol")["logc"].diff(48) * 10_000.0)
    p["r720"] = (p.groupby("symbol")["logc"].diff(144) * 10_000.0)
    p["r1440"] = (p.groupby("symbol")["logc"].diff(288) * 10_000.0)
    p["r2880"] = (p.groupby("symbol")["logc"].diff(576) * 10_000.0)
    p["rvol5"] = p["volume"] / p.groupby("symbol")["volume"].transform(
        lambda s: s.rolling(24, min_periods=6).mean())
    p["range5"] = np.log(p["high"] / p["low"]) * 10_000.0
    p["body5"] = (p["close"] - p["open"]) / p["open"] * 10_000.0

    # forward outcomes at 5m resolution (stride h in 5m bars)
    fmap = {"fwd_5": 1, "fwd_15": 3, "fwd_30": 6, "fwd_60": 12,
            "fwd_240": 48, "fwd_720": 144, "fwd_1440": 288,
            "fwd_2880": 576}
    h5 = fmap[args.outcome]
    p["fwd"] = p.groupby("symbol")["logc"].shift(-h5) - p["logc"]
    p["fwd"] *= 10_000.0

    feat = args.feature
    p = p.dropna(subset=[feat, "fwd"])
    if len(p) == 0:
        print("no rows")
        return 1

    rows = []
    # rank per timestamp
    p["rank"] = p.groupby("open_time")[feat].rank(
        method="first", pct=True)
    p["n_sym"] = p.groupby("open_time")["symbol"].transform("count")
    p = p[p["n_sym"] >= args.min_symbols]

    # outcome horizon non-overlap: subsample every h5-th timestamp per sym
    # (timestamps are 5m apart; stride = h5)
    t_idx = {t: i for i, t in enumerate(sorted(p["open_time"].unique()))}
    p["t_i"] = p["open_time"].map(t_idx)
    p["use"] = (p["t_i"] % h5) == 0

    # 5m-bar TP/SL games for the buckets (h5 = horizon in 5m bars),
    # computed within each symbol's own time series
    games = [(50, 40), (80, 50), (80, 60), (100, 60), (120, 80), (150, 100)]
    do_games = h5 <= 12
    for tp, sl in games:
        p[f"g{tp}x{sl}"] = np.nan
    for sym, g in p.groupby("symbol", sort=False):
        if not do_games:
            continue
        idx = g.index.to_numpy()
        logh = np.log(p.loc[idx, "high"].to_numpy())
        logl = np.log(p.loc[idx, "low"].to_numpy())
        logc = np.log(p.loc[idx, "close"].to_numpy())
        n_g = len(idx)
        for tp, sl in games:
            tp_log, sl_log = np.log1p(tp / 1e4), np.log1p(sl / 1e4)
            t_hit_tp = np.full(n_g, np.nan)
            t_hit_sl = np.full(n_g, np.nan)
            hit_tp = np.zeros(n_g, dtype=bool)
            hit_sl = np.zeros(n_g, dtype=bool)
            for k in range(1, h5 + 1):
                hi_k = np.roll(logh, -k)
                lo_k = np.roll(logl, -k)
                new_tp = (~hit_tp) & (hi_k - logc >= tp_log)
                new_sl = (~hit_sl) & (logc - lo_k >= sl_log)
                t_hit_tp[new_tp] = k
                t_hit_sl[new_sl] = k
                hit_tp |= new_tp
                hit_sl |= new_sl
            # invalidate the last h5 rows (window runs past group end)
            hit_tp[n_g - h5:] = False
            hit_sl[n_g - h5:] = False
            game = np.where(hit_tp & hit_sl,
                            np.where(t_hit_tp <= t_hit_sl, 1.0, -1.0),
                            np.where(hit_tp, 1.0,
                                     np.where(hit_sl, -1.0, 0.0)))
            p.loc[idx, f"g{tp}x{sl}"] = game

    # per-symbol MFE/MAE at h5 and 12*h5 (5m bars), O(n) block method
    from quant.lib.outcomes import _window_maxmin
    for tag, wb in (("h", h5), ("h12", min(12 * h5, 288))):
        for sym, g in p.groupby("symbol", sort=False):
            idx = g.index.to_numpy()
            logh = np.log(g["high"].to_numpy())
            logl = np.log(g["low"].to_numpy())
            logc = np.log(g["close"].to_numpy())
            mfe, _ = _window_maxmin(logh, wb)
            _, mae = _window_maxmin(logl, wb)
            p.loc[idx, f"mfe_{tag}"] = (mfe - logc) * 10_000.0
            p.loc[idx, f"mae_{tag}"] = (mae - logc) * 10_000.0

    top = p[(p["use"]) & (p["rank"] >= 1 - args.top / p["n_sym"])]
    bot = p[(p["use"]) & (p["rank"] <= args.bottom / p["n_sym"])]
    mid = p[(p["use"]) & (p["rank"].between(0.4, 0.6))]

    def summ(name, g):
        n = len(g)
        if n == 0:
            return
        mean = g["fwd"].mean()
        rec = {"bucket": name, "n": n, "mean_fwd": mean,
               "median_fwd": g["fwd"].median(),
               "p_up": (g["fwd"] > 0).mean(),
               "std": g["fwd"].std(),
               "t": mean / g["fwd"].std() * np.sqrt(n) if g["fwd"].std() > 0
               else 0}
        for tp, sl in games:
            gv = g[f"g{tp}x{sl}"]
            dec = gv[gv != 0]
            rec[f"g{tp}x{sl}_win"] = (dec == 1).mean() if len(dec) else np.nan
            rec[f"g{tp}x{sl}_dec"] = len(dec)
        for tag in ("h", "h12"):
            rec[f"mfe_{tag}_mean"] = g[f"mfe_{tag}"].mean()
            rec[f"mfe_{tag}_p50"] = g[f"mfe_{tag}"].median()
            rec[f"mfe_{tag}_p75"] = g[f"mfe_{tag}"].quantile(0.75)
            rec[f"mfe_{tag}_p90"] = g[f"mfe_{tag}"].quantile(0.90)
            rec[f"mae_{tag}_mean"] = g[f"mae_{tag}"].mean()
        rows.append(rec)

    summ("top", top)
    summ("mid", mid)
    summ("bottom", bot)
    out = pd.DataFrame(rows)
    if len(out) >= 2:
        print(f"feature={feat} outcome={args.outcome} "
              f"topN={args.top} botN={args.bottom}")
        cols = ["bucket", "n", "mean_fwd", "p_up", "t"]
        cols += [c for c in out.columns if c.endswith("_win")]
        cols += [c for c in out.columns if "mfe_" in c or "mae_" in c]
        print(out[cols].round(3).to_string(index=False))
        if "top" in out["bucket"].values and "bottom" in out["bucket"].values:
            t = out[out.bucket == "top"].iloc[0]
            b = out[out.bucket == "bottom"].iloc[0]
            print(f"top-bottom edge: {t['mean_fwd'] - b['mean_fwd']:.2f} bps")
        if args.out:
            out.to_csv(args.out, index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
