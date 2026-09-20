"""5m-resolution discovery pass over the full multi-year dataset.

Features are computed on 5m bars (bar-count horizons: r1=5m, r3=15m,
r6=30m, r12=1h, r48=4h).  Derivatives (5m metrics: OI, taker L/S ratio)
are aligned causally to the previous completed 5m bucket.  Outcomes at 5m
bars: fwd_1/3/6/12/48, TP/SL games at 15m/1h/4h horizons, MFE/MAE tails.

Usage:
  python3 quant/tools/discover5m.py --out data/research/disco5m.csv \
      --symbols ... --min-bars 30000
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
from quant.lib.outcomes import _window_maxmin  # noqa: E402
from quant.research.discover import bucket_table  # noqa: E402

log = logging.getLogger("quant.tools.discover5m")

STRIDE = {"fwd_1": 1, "fwd_3": 3, "fwd_6": 6, "fwd_12": 12, "fwd_48": 48,
          "mfe_12": 12, "mae_12": 12, "mfe_48": 48, "mae_48": 48,
          "g50x40_3": 3, "g80x60_3": 3, "g80x60_12": 12, "g120x80_12": 12,
          "g120x80_48": 48, "g200x120_48": 48, "orac_80x60_12": 12,
          "orac_120x80_12": 12, "orac_120x80_48": 48, "orac_200x120_48": 48}


def resample_5m(df: pd.DataFrame) -> pd.DataFrame:
    t = df["open_time"].to_numpy()
    bucket = t // 300_000 * 300_000
    g = df.groupby(bucket)
    out = g.agg(open=("open", "first"), high=("high", "max"),
                low=("low", "min"), close=("close", "last"),
                volume=("volume", "sum")).reset_index()
    return out.rename(columns={"index": "open_time"})


def align5m(feat: pd.DataFrame, metrics: pd.DataFrame | None,
            funding: pd.DataFrame | None) -> pd.DataFrame:
    out = feat.copy()
    bar_t = feat["open_time"].to_numpy()
    if metrics is not None and len(metrics):
        m = metrics[["create_time", "sum_open_interest",
                     "sum_taker_long_short_vol_ratio",
                     "count_long_short_ratio"]].copy()
        m["ct"] = m["create_time"].astype("int64") // 10**9 * 1000
        m = m.sort_values("ct").drop_duplicates("ct", keep="last")
        mt = m["ct"].to_numpy()
        idx = np.searchsorted(mt, bar_t - 300_000, side="right") - 1
        oi = np.full(len(feat), np.nan)
        ratio = np.full(len(feat), np.nan)
        lsr = np.full(len(feat), np.nan)
        v = idx >= 0
        oi[v] = m["sum_open_interest"].to_numpy()[idx[v]]
        ratio[v] = m["sum_taker_long_short_vol_ratio"].to_numpy()[idx[v]]
        lsr[v] = m["count_long_short_ratio"].to_numpy()[idx[v]]
        out["oi"] = oi
        out["oi_chg1"] = np.log(np.maximum(oi, 1e-12)) - np.log(
            np.maximum(pd.Series(oi).shift(1).to_numpy(), 1e-12))
        out["oi_chg6"] = np.log(np.maximum(oi, 1e-12)) - np.log(
            np.maximum(pd.Series(oi).shift(6).to_numpy(), 1e-12))
        out["oi_chg48"] = np.log(np.maximum(oi, 1e-12)) - np.log(
            np.maximum(pd.Series(oi).shift(48).to_numpy(), 1e-12))
        out["oi_chg288"] = np.log(np.maximum(oi, 1e-12)) - np.log(
            np.maximum(pd.Series(oi).shift(288).to_numpy(), 1e-12))
        out["oi_z"] = (oi - pd.Series(oi).rolling(288).mean().to_numpy()) / \
            np.maximum(pd.Series(oi).rolling(288).std().to_numpy(), 1e-9)
        out["taker_imb"] = (ratio - 1) / (ratio + 1)
        out["taker_imb_z"] = (out["taker_imb"] - pd.Series(
            out["taker_imb"]).rolling(288).mean().to_numpy()) / np.maximum(
                pd.Series(out["taker_imb"]).rolling(288).std().to_numpy(),
                1e-9)
        out["lsr"] = lsr
        r5 = np.nan_to_num(out["r1"].to_numpy())
        oi5 = np.nan_to_num(out["oi_chg1"].to_numpy())
        out["liq_long"] = np.maximum(0.0, -r5) * np.maximum(0.0, -oi5)
        out["liq_short"] = np.maximum(0.0, r5) * np.maximum(0.0, oi5)
        out["liq_net"] = out["liq_long"] - out["liq_short"]
        out["p_oi_div"] = r5 - 10.0 * oi5
    if funding is not None and len(funding):
        f = funding[["calc_time", "last_funding_rate"]].copy()
        f["ct"] = f["calc_time"].astype("int64")
        f = f.sort_values("ct").drop_duplicates("ct", keep="last")
        ft = f["ct"].to_numpy()
        idx = np.searchsorted(ft, bar_t, side="right") - 1
        fr = np.full(len(feat), np.nan)
        v = idx >= 0
        fr[v] = f["last_funding_rate"].to_numpy()[idx[v]]
        out["funding"] = fr
        out["funding_ann"] = fr * 3 * 365 * 100
        out["funding_chg"] = fr - pd.Series(fr).shift(1).to_numpy()
        out["funding_z"] = (fr - pd.Series(fr).rolling(288).mean().to_numpy()) \
            / np.maximum(pd.Series(fr).rolling(288).std().to_numpy(), 1e-9)
    return out


def games5m(df: pd.DataFrame, specs=((50, 40, 3), (80, 60, 3), (80, 60, 12),
                                     (120, 80, 12), (120, 80, 48),
                                     (200, 120, 48))) -> pd.DataFrame:
    """TP/SL first-hit games on 5m bars with exact ordering."""
    out = pd.DataFrame(index=df.index)
    out["open_time"] = df["open_time"].to_numpy()
    n = len(df)
    logc = np.log(df["close"].to_numpy())
    logh = np.log(df["high"].to_numpy())
    logl = np.log(df["low"].to_numpy())
    from numpy.lib.stride_tricks import sliding_window_view as swv
    for tp, sl, hh in specs:
        w = swv(logc, hh + 1)
        wf = w[:, 1:]
        wh = swv(logh, hh + 1)[:, 1:]
        wl = swv(logl, hh + 1)[:, 1:]
        t_tp = np.argmax(wh, axis=1)
        t_sl = np.argmin(wl, axis=1)
        tp_log, sl_log = np.log1p(tp / 1e4), np.log1p(sl / 1e4)
        hit_tp = (wh.max(axis=1) - w[:, 0]) >= tp_log
        hit_sl = (w[:, 0] - wl.min(axis=1)) >= sl_log
        n_ok = n - hh
        g = np.zeros(n, dtype=float)
        both = hit_tp & hit_sl
        g[:n_ok] = np.where(both, np.where(t_tp <= t_sl, 1.0, -1.0),
                            np.where(hit_tp, 1.0,
                                     np.where(hit_sl, -1.0, 0.0)))
        out[f"g{tp}x{sl}_{hh}"] = g
        # oracle exit value
        entry = w[:, 0]
        exit_log = np.empty(n_ok)
        exit_log[:] = logc[hh:hh + n_ok]
        tp_only = hit_tp & ~hit_sl
        sl_only = hit_sl & ~hit_tp
        exit_log[tp_only] = entry[tp_only] + tp_log
        exit_log[sl_only] = entry[sl_only] - sl_log
        exit_log[both] = np.where(t_tp <= t_sl, entry + tp_log,
                                  entry - sl_log)[both]
        col = np.full(n, np.nan)
        col[:n_ok] = (exit_log - entry) * 1e4
        out[f"orac_{tp}x{sl}_{hh}"] = col
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--symbols", default=None)
    ap.add_argument("--min-bars", type=int, default=30_000)
    ap.add_argument("--outcomes", default="fwd_3,fwd_12,"
                    "g80x60_3,g80x60_12,g120x80_12,mfe_12")
    ap.add_argument("--features", default=None)
    args = ap.parse_args()

    root = store.data_root()
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        uni = json.loads(open(root / "universe.json").read())
        symbols = sorted(uni["symbols"])
    out_cols = [x.strip() for x in args.outcomes.split(",") if x.strip()]

    btc5 = None
    try:
        btc5 = features.compute_base(resample_5m(
            store.load_klines("BTCUSDT", root)))
    except FileNotFoundError:
        pass

    rows = []
    t0 = time.time()
    for i, sym in enumerate(symbols):
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
            b = btc5[["open_time", "r1", "r3", "r5", "r15", "r60", "rvol"]]
            b = b.rename(columns={c: f"btc_{c}" for c in b.columns
                                  if c != "open_time"})
            feat = feat.merge(b, on="open_time", how="left")
            feat["rs3"] = feat["r3"] - feat["btc_r3"]
            feat["rs15"] = feat["r15"] - feat["btc_r15"]
        out = games5m(d5)
        # MFE/MAE at 12 and 48 bars
        logh = np.log(d5["high"].to_numpy())
        logl = np.log(d5["low"].to_numpy())
        logc = np.log(d5["close"].to_numpy())
        for wb in (12, 48):
            mfe, _ = _window_maxmin(logh, wb)
            _, mae = _window_maxmin(logl, wb)
            out[f"mfe_{wb}"] = (mfe - logc) * 1e4
            out[f"mae_{wb}"] = (mae - logc) * 1e4
        cols = (args.features.split(",") if args.features else
                [c for c in feat.columns if c != "open_time"])
        for f in cols:
            if f not in feat.columns:
                continue
            for o in out_cols:
                src = out if o in out.columns else feat
                if o not in src.columns:
                    continue
                stride = STRIDE.get(o, 3)
                bt = bucket_table(feat, f, src[o], stride, 10)
                if bt is None or "t" not in bt.attrs:
                    continue
                rows.append({
                    "symbol": sym, "feature": f, "outcome": o,
                    "edge": bt.attrs["edge"], "t": bt.attrs["t"],
                    "mono": bt.attrs["mono"], "n_eff": bt.attrs["n_eff"],
                    "bin0_mean": float(bt["mean"].iloc[0]),
                    "bin9_mean": float(bt["mean"].iloc[-1]),
                })
        log.info("[%s] %d rows in %.1fs (%d/%d)", sym,
                 sum(1 for r in rows if r["symbol"] == sym),
                 time.time() - t1, i + 1, len(symbols))
    pd.DataFrame(rows).to_csv(args.out, index=False)
    log.info("saved %d rows -> %s (%.1f min)", len(rows), args.out,
             (time.time() - t0) / 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
