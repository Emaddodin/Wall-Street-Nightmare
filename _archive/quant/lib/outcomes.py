"""Forward outcome computation: for every bar t, what happens in the next
1..60 minutes, causally (only bars > t are used).

Outcomes (all vs the close of bar t):
  fwd_{h}          log return over next h bars, bps
  fwd_abs_{h}      |return|
  tpXslY_h         Bernoulli: hits +X bps before -Y bps within h bars
  tpX_h            Bernoulli: hits +X bps within h bars (regardless of -Y)
  slX_h            Bernoulli: hits -X bps within h bars
  mfe_{h}          max favorable excursion, bps (using highs)
  mae_{h}          max adverse excursion, bps (using lows, signed negative)
  ttpXslY_h        bars until first of {+X/-Y} hit (NaN if neither)
  fwd_ret_tpXslY   realized return if exited at first hit of {+X/-Y}, else
                   return at horizon (bps) -- the "oracle exit" benchmark

First-hit ordering is exact: argmax/argmin give the FIRST bar at which the
running extreme is reached, so "TP before SL" is determined by comparing the
two first-hit bar indices.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant.lib.features import BPS


def _windows(arr: np.ndarray, h: int) -> np.ndarray:
    """Strided view of shape (n-h, h+1): row t = arr[t .. t+h]."""
    from numpy.lib.stride_tricks import sliding_window_view
    return sliding_window_view(arr, h + 1)


def _window_maxmin(a: np.ndarray, h: int) -> tuple[np.ndarray, np.ndarray]:
    """For each t: max(a[t+1..t+h]) and min(a[t+1..t+h]), O(n) vectorized
    via block decomposition.  NaN for the last h positions."""
    n = len(a)
    mx = np.full(n, np.nan)
    mn = np.full(n, np.nan)
    if n <= h:
        return mx, mn
    nb = (n - 1) // h + 1
    pad_n = nb * h - n

    def _side(arr: np.ndarray, fill: float) -> np.ndarray:
        padded = np.concatenate([arr, np.full(pad_n, fill)])
        blocks = padded.reshape(nb, h)
        suf = np.maximum.accumulate(blocks[:, ::-1], axis=1)[:, ::-1] \
            if fill == -np.inf else \
            np.minimum.accumulate(blocks[:, ::-1], axis=1)[:, ::-1]
        pre = np.maximum.accumulate(blocks, axis=1) if fill == -np.inf else \
            np.minimum.accumulate(blocks, axis=1)
        j = np.arange(1, n - h + 1)
        bj = j // h
        r = j % h
        m1 = suf[bj, r]
        cond = r > 0
        b2 = np.minimum(bj + 1, nb - 1)
        m2 = np.where(cond, pre[b2, r - 1], fill)
        out = np.full(n, np.nan)
        out[j - 1] = np.maximum(m1, m2) if fill == -np.inf else \
            np.minimum(m1, m2)
        return out

    mx = _side(a, -np.inf)
    mn = _side(a, np.inf)
    return mx, mn


def compute_mfe_mae(df: pd.DataFrame, horizons=(15, 30, 60, 240, 720)
                    ) -> pd.DataFrame:
    """MFE/MAE for long horizons using O(n) block maxima."""
    c = df["close"].to_numpy(dtype=float)
    h = np.log(np.maximum(df["high"].to_numpy(dtype=float), 1e-12))
    l = np.log(np.maximum(df["low"].to_numpy(dtype=float), 1e-12))
    logc = np.log(np.maximum(c, 1e-12))
    out = pd.DataFrame(index=df.index)
    out["open_time"] = df["open_time"].to_numpy()
    for hh in horizons:
        mx, _ = _window_maxmin(h, hh)
        _, mn = _window_maxmin(l, hh)
        out[f"mfe_{hh}"] = (mx - logc) * BPS
        out[f"mae_{hh}"] = (mn - logc) * BPS
    return out


def compute_outcomes(df: pd.DataFrame,
                     horizons=(1, 3, 5, 10, 15, 30, 60),
                     tp_levels=(20, 30, 50, 80),
                     sl_levels=(20, 30, 50)) -> pd.DataFrame:
    """df needs open_time, high, low, close.  Returns one row per bar
    (last `max(horizons)` bars get NaN outcomes)."""
    n = len(df)
    c = df["close"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    out = pd.DataFrame(index=df.index)
    out["open_time"] = df["open_time"].to_numpy()
    maxh = max(horizons)

    # --- forward returns (log, bps) via direct shifts
    for hh in horizons:
        fwd = np.full(n, np.nan)
        fwd[:n - hh] = np.log(c[hh:] / c[:n - hh]) * BPS
        out[f"fwd_{hh}"] = fwd
        out[f"fwd_abs_{hh}"] = np.abs(fwd)

    # --- extremes via strided windows on log prices
    logc = np.log(np.maximum(c, 1e-12))
    for hh in horizons:
        w = _windows(logc, hh)          # (n-hh, hh+1), incl bar t
        w_hi = _windows(np.log(np.maximum(h, 1e-12)), hh)[:, 1:]
        w_lo = _windows(np.log(np.maximum(l, 1e-12)), hh)[:, 1:]
        mfe = (w_hi.max(axis=1) - w[:, 0]) * BPS
        mae = (w_lo.min(axis=1) - w[:, 0]) * BPS
        mfe_a = np.full(n, np.nan)
        mae_a = np.full(n, np.nan)
        mfe_a[:n - hh] = mfe
        mae_a[:n - hh] = mae
        out[f"mfe_{hh}"] = mfe_a
        out[f"mae_{hh}"] = mae_a

    # --- TP/SL first-hit games (exact ordering via argmax/argmin)
    game_specs = [(tp, sl, 15) for tp in tp_levels for sl in sl_levels]
    game_specs += [(80, 60, 60), (120, 80, 60), (150, 100, 60),
                   (200, 120, 60), (120, 80, 240), (200, 120, 240)]
    for tp, sl, hh in game_specs:
        # thresholds in log space
        tp_log = np.log1p(tp / BPS)
        sl_log = np.log1p(sl / BPS)
        w = _windows(logc, hh)                       # (n-hh, hh+1)
        wf = w[:, 1:]                                # future only
        # first bar reaching the window max/min
        t_tp = np.argmax(wf, axis=1)                 # first argmax
        t_sl = np.argmin(wf, axis=1)
        hit_tp = (wf.max(axis=1) - w[:, 0]) >= tp_log
        hit_sl = (w[:, 0] - wf.min(axis=1)) >= sl_log
        n_ok = n - hh
        col_tp = np.zeros(n, dtype=float)
        col_sl = np.zeros(n, dtype=float)
        col_g = np.zeros(n, dtype=float)             # game: +1 tp first,
        # -1 sl first, 0 neither
        col_tp[:n_ok] = hit_tp
        col_sl[:n_ok] = hit_sl
        both = hit_tp & hit_sl
        col_g[:n_ok] = np.where(both,
                                np.where(t_tp <= t_sl, 1.0, -1.0),
                                np.where(hit_tp, 1.0,
                                         np.where(hit_sl, -1.0, 0.0)))
        out[f"tp{tp}_{hh}"] = col_tp
        out[f"sl{sl}_{hh}"] = col_sl
        out[f"g{tp}x{sl}_{hh}"] = col_g

        # realized return under oracle exit at first hit, else at horizon
        hit_any = hit_tp | hit_sl
        entry = w[:, 0]
        exit_log2 = np.empty(n_ok)
        exit_log2[:] = logc[hh:hh + n_ok]            # horizon close
        tp_only = hit_tp & ~hit_sl
        sl_only = hit_sl & ~hit_tp
        exit_log2[tp_only] = entry[tp_only] + tp_log
        exit_log2[sl_only] = entry[sl_only] - sl_log
        exit_log2[both] = np.where(t_tp <= t_sl,
                                   entry + tp_log, entry - sl_log)[both]
        col_r = np.full(n, np.nan)
        col_r[:n_ok] = (exit_log2 - entry) * BPS
        out[f"orac_{tp}x{sl}_{hh}"] = col_r

        # bars until first hit (NaN = none within horizon)
        t_exit = np.where(both,
                          np.where(t_tp <= t_sl, t_tp, t_sl),
                          np.where(hit_tp, t_tp,
                                   np.where(hit_sl, t_sl, hh - 1)))
        col_t = np.full(n, np.nan)
        col_t[:n_ok] = np.where(hit_any, t_exit + 1, np.nan)
        out[f"tt_{tp}x{sl}_{hh}"] = col_t

    return out
