"""Edge discovery: quantile bucket analysis, 2D conditionals, event studies.

All statistics use non-overlapping samples: for an outcome with horizon h
bars, analysis subsamples stride h, so overlapping forward windows never
inflate significance.
"""
from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd

log = logging.getLogger("quant.research.discover")


# ----------------------------------------------------------------------
# per-symbol feature materialization (uncached, chunked by month for RAM)
# ----------------------------------------------------------------------

def load_features(symbol: str, root=None, btc_feat=None,
                  breadth=None) -> pd.DataFrame:
    """Full-resolution features for one symbol (all months available)."""
    from quant.lib import store, features
    df = store.load_klines(symbol, root)
    if len(df) < 5000:
        return pd.DataFrame()
    metrics = funding = None
    try:
        metrics = store.load_metrics(symbol, root)
    except FileNotFoundError:
        pass
    try:
        funding = store.load_funding(symbol, root)
    except FileNotFoundError:
        pass
    # warmup: keep everything; rolling windows handle the head via NaN
    return features.compute_symbol(symbol, df, metrics, funding, btc_feat,
                                   breadth)


def load_btc_features(root=None) -> pd.DataFrame:
    from quant.lib import store, features
    df = store.load_klines("BTCUSDT", root)
    return features.compute_base(df)


# ----------------------------------------------------------------------
# bucket analysis
# ----------------------------------------------------------------------

def _bins(x: np.ndarray, n_bins: int, mask: np.ndarray) -> np.ndarray:
    """Quantile bin ids (0..n_bins-1) over masked values; NaN -> -1."""
    xm = x[mask]
    if len(xm) < n_bins * 10 or np.all(~np.isfinite(xm)):
        return None
    qs = np.nanquantile(xm[np.isfinite(xm)], np.linspace(0, 1, n_bins + 1))
    qs[0] = -np.inf
    qs[-1] = np.inf
    ids = np.searchsorted(qs[1:-1], x)      # 0..n_bins-1
    ids[~np.isfinite(x)] = -1
    return ids


def bucket_table(feat: pd.DataFrame, feat_col: str, out_col,
                 horizon: int, n_bins: int = 10, min_n: int = 30,
                 weights=None) -> pd.DataFrame | None:
    """Mean of out_col across quantile bins of feat_col, non-overlapping
    samples (stride=horizon).  out_col: column name in feat or a Series.
    weights: optional per-row weights."""
    if isinstance(out_col, str):
        y = feat[out_col].to_numpy(dtype=float)
    else:
        y = np.asarray(out_col, dtype=float)
    x = feat[feat_col].to_numpy(dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    # non-overlapping subsample: every horizon-th bar
    sub = np.zeros(len(feat), dtype=bool)
    sub[::horizon] = True
    mask &= sub
    if mask.sum() < min_n * n_bins:
        return None
    ids = _bins(x, n_bins, mask)
    if ids is None:
        return None
    w = np.ones(len(feat)) if weights is None else weights
    rows = []
    for b in range(n_bins):
        m = (ids == b)
        if m.sum() < min_n:
            continue
        ym = y[m]
        rows.append({
            "bin": b,
            "n": int(m.sum()),
            "mean": float(np.average(ym, weights=w[m])),
            "median": float(np.median(ym)),
            "p_up": float((ym > 0).mean()),
            "std": float(np.std(ym)),
            "x_lo": float(np.nanquantile(x[m], 0.05)),
            "x_hi": float(np.nanquantile(x[m], 0.95)),
        })
    if len(rows) < 3:
        return None
    out = pd.DataFrame(rows)
    # edge: top bin vs bottom bin, t-stat on pooled subsample
    top = ids == n_bins - 1
    bot = ids == 0
    if top.sum() >= min_n and bot.sum() >= min_n:
        yt = y[top]
        yb = y[bot]
        diff = yt.mean() - yb.mean()
        se = np.sqrt(np.var(yt) / len(yt) + np.var(yb) / len(yb))
        out.attrs["edge"] = float(diff)
        out.attrs["t"] = float(diff / se) if se > 0 else 0.0
        out.attrs["n_eff"] = int(top.sum() + bot.sum())
        out.attrs["mono"] = float(np.corrcoef(
            np.arange(len(out)), out["mean"])[0, 1]) if len(out) >= 3 else 0.0
    return out


def rank_features(feat: pd.DataFrame, out_col: str, horizon: int,
                  feature_cols: list[str], n_bins: int = 10,
                  weights=None) -> pd.DataFrame:
    """Rank every feature by |top-bottom edge| and t-stat."""
    rows = []
    for f in feature_cols:
        if f not in feat.columns:
            continue
        try:
            bt = bucket_table(feat, f, out_col, horizon, n_bins,
                              weights=weights)
        except Exception:
            continue
        if bt is None or "t" not in bt.attrs:
            continue
        rows.append({"feature": f, "edge": bt.attrs["edge"],
                     "t": bt.attrs["t"], "mono": bt.attrs["mono"],
                     "n_eff": bt.attrs["n_eff"]})
    out = pd.DataFrame(rows).sort_values("t", key=abs, ascending=False)
    return out.reset_index(drop=True)


def two_way(feat: pd.DataFrame, f1: str, f2: str, out_col: str,
            horizon: int, q: int = 4, min_n: int = 40,
            weights=None) -> pd.DataFrame:
    """2D conditional mean of out_col on quantiles of (f1, f2)."""
    y = feat[out_col].to_numpy(dtype=float)
    x1 = feat[f1].to_numpy(dtype=float)
    x2 = feat[f2].to_numpy(dtype=float)
    mask = np.isfinite(x1) & np.isfinite(x2) & np.isfinite(y)
    sub = np.zeros(len(feat), dtype=bool)
    sub[::horizon] = True
    mask &= sub
    if mask.sum() < min_n * q * q:
        return pd.DataFrame()
    i1 = _bins(x1, q, mask)
    i2 = _bins(x2, q, mask)
    if i1 is None or i2 is None:
        return pd.DataFrame()
    rows = []
    for a in range(q):
        for b in range(q):
            m = (i1 == a) & (i2 == b)
            if m.sum() < min_n:
                rows.append({"f1_q": a, "f2_q": b, "n": int(m.sum()),
                             "mean": np.nan})
                continue
            rows.append({"f1_q": a, "f2_q": b, "n": int(m.sum()),
                         "mean": float(y[m].mean())})
    return pd.DataFrame(rows)


def event_study(feat: pd.DataFrame, mask: np.ndarray, horizon: int = 60,
                ret_col: str = "r1", n_bins: int = 10) -> pd.DataFrame:
    """Mean cumulative forward path from event bars (mask) vs random bars.

    Uses cumulative sum of 1m returns from t+1..t+horizon (excluding bar t
    itself).  Baseline = mean over all bars."""
    r1 = feat[ret_col].to_numpy(dtype=float)
    n = len(feat)
    # cumulative forward sum for every bar: F[t][k] = sum r1[t+1..t+k]
    # computed via matrix ops per horizon is heavy; use loop over k with
    # shifted arrays (fast enough for k<=60)
    ev = mask & np.isfinite(r1)
    if ev.sum() < 200:
        return pd.DataFrame()
    rows = []
    baseline = np.full(horizon + 1, np.nan)
    ev_path = np.full(horizon + 1, np.nan)
    n_ev = int(ev.sum())
    for k in range(horizon + 1):
        fwd = np.full(n, np.nan)
        fwd[:n - k] = r1[k:]
        base_ok = np.isfinite(fwd)
        baseline[k] = np.nanmean(fwd)
        ev_path[k] = np.nanmean(fwd[ev & base_ok])
        rows.append({"k": k, "baseline": baseline[k], "event": ev_path[k],
                     "n": int((ev & base_ok).sum())})
    out = pd.DataFrame(rows)
    out["edge"] = out["event"] - out["baseline"]
    out.attrs["n_events"] = n_ev
    return out
