"""Feature computation for the new quant research project.

Every feature is causal: computed at bar t using only data from bars <= t.
Metrics (5m OI / taker ratios) are mapped from the previous COMPLETED 5m
bucket -- never the bucket still forming at t.  Funding uses the most recent
8h mark <= t.

All returns are log returns in basis points unless noted.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BPS = 10_000.0

# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

def _roll_mean(x: np.ndarray, w: int) -> np.ndarray:
    s = pd.Series(x).rolling(w).mean()
    return s.to_numpy()


def _roll_std(x: np.ndarray, w: int) -> np.ndarray:
    s = pd.Series(x).rolling(w).std()
    return s.to_numpy()


def _roll_max(x: np.ndarray, w: int) -> np.ndarray:
    s = pd.Series(x).rolling(w).max()
    return s.to_numpy()


def _roll_min(x: np.ndarray, w: int) -> np.ndarray:
    s = pd.Series(x).rolling(w).min()
    return s.to_numpy()


def _logret(x: np.ndarray, h: int) -> np.ndarray:
    """log(x_t / x_{t-h}) in bps, NaN for first h bars."""
    out = np.full(len(x), np.nan)
    out[h:] = np.log(x[h:] / x[:-h]) * BPS
    return out


def _shift(x: np.ndarray, k: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if k >= 0:
        out[k:] = x[:len(x) - k]
    else:
        out[:len(x) + k] = x[-k:]
    return out


# ----------------------------------------------------------------------
# base 1m OHLCV features
# ----------------------------------------------------------------------

def compute_base(df: pd.DataFrame) -> pd.DataFrame:
    """OHLCV-only features on 1m bars.  df: open_time, open, high, low,
    close, volume (USDT)."""
    n = len(df)
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    v = df["volume"].to_numpy(dtype=float)
    logc = np.log(np.maximum(c, 1e-12))

    out = pd.DataFrame(index=df.index)
    out["open_time"] = df["open_time"].to_numpy()

    # --- returns (bps)
    for hh in (1, 3, 5, 10, 15, 30, 60, 120, 240):
        out[f"r{hh}"] = _logret(c, hh)

    # --- acceleration: change of 5m return over 5m
    out["accel5"] = out["r5"] - _shift(out["r5"].to_numpy(), 5)

    # --- volume features
    vma240 = _roll_mean(v, 240)
    out["rvol"] = v / np.maximum(vma240, 1e-9)          # relative volume
    out["rvol_s"] = _roll_mean(v, 5) / np.maximum(vma240, 1e-9)   # smoothed
    out["rvol_accel"] = out["rvol_s"] / np.maximum(
        _shift(out["rvol_s"].to_numpy(), 5), 1e-9)      # volume acceleration
    out["vol_shock"] = np.maximum(
        v / np.maximum(_shift(v, 1), 1e-9),
        _shift(v, 1) / np.maximum(v, 1e-9))             # symmetric shock

    # --- candle anatomy
    rng = np.maximum(h - l, 1e-12)
    out["range_frac"] = rng / np.maximum(c, 1e-12)
    out["body_frac"] = np.abs(c - o) / rng
    out["wick_up"] = (h - np.maximum(c, o)) / rng
    out["wick_dn"] = (np.minimum(c, o) - l) / rng
    out["vpr"] = v / np.maximum(rng, 1e-12)             # volume per range

    # --- position within recent extremes (breakout proximity)
    out["hi_dist"] = np.log(np.maximum(c, 1e-12)) - np.log(
        np.maximum(_roll_max(h, 60), 1e-12))            # <=0; 0 = at 60-bar high
    out["lo_dist"] = np.log(np.maximum(c, 1e-12)) - np.log(
        np.maximum(_roll_min(l, 60), 1e-12))            # >=0; 0 = at 60-bar low
    out["hi_dist240"] = np.log(np.maximum(c, 1e-12)) - np.log(
        np.maximum(_roll_max(h, 240), 1e-12))
    out["lo_dist240"] = np.log(np.maximum(c, 1e-12)) - np.log(
        np.maximum(_roll_min(l, 240), 1e-12))

    # --- compression / expansion
    rng20 = _roll_max(h, 20) - _roll_min(l, 20)
    rng120 = _roll_max(h, 120) - _roll_min(l, 120)
    out["compress"] = rng20 / np.maximum(rng120, 1e-12)  # low = squeezed
    rng5 = _roll_max(h, 5) - _roll_min(l, 5)
    out["expand"] = rng5 / np.maximum(rng20, 1e-12)      # recent burst

    # --- volatility state
    rv5 = _roll_std(out["r1"].to_numpy(), 20)
    rv60 = _roll_std(out["r1"].to_numpy(), 240)
    out["rv5"] = rv5
    out["rv60"] = rv60
    out["vol_ratio"] = rv5 / np.maximum(rv60, 1e-9)     # >1 = heating up
    r1 = out["r1"].to_numpy()
    out["max_abs_r60"] = _roll_max(np.abs(np.nan_to_num(r1)), 60)
    out["jump"] = out["max_abs_r60"] / np.maximum(rv60, 1e-9)

    # --- trend proxy: split mean slope
    f = pd.Series(c)
    short_ma = f.rolling(10).mean().to_numpy()
    long_ma = f.rolling(30).mean().shift(20).to_numpy()
    out["slope60"] = (short_ma - long_ma) / np.maximum(c, 1e-12)
    out["trend_dist"] = c / np.maximum(f.rolling(60).mean().to_numpy(), 1e-9) - 1.0

    # --- asymmetric volatility (up vs down semivariance)
    up = np.where(r1 > 0, r1, 0.0)
    dn = np.where(r1 < 0, r1, 0.0)
    up_sd = _roll_std(up, 60)
    dn_sd = _roll_std(dn, 60)
    out["vol_asym"] = (up_sd - dn_sd) / np.maximum(up_sd + dn_sd, 1e-9)

    # --- time features
    t = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    hour = t.dt.hour + t.dt.minute / 60.0
    dow = t.dt.dayofweek
    out["sin_hour"] = np.sin(2 * np.pi * hour / 24)
    out["cos_hour"] = np.cos(2 * np.pi * hour / 24)
    out["sin_dow"] = np.sin(2 * np.pi * dow / 7)
    out["cos_dow"] = np.cos(2 * np.pi * dow / 7)

    return out


# ----------------------------------------------------------------------
# derivatives features (5m metrics + funding)
# ----------------------------------------------------------------------

def align_metrics(feat: pd.DataFrame, metrics: pd.DataFrame) -> pd.DataFrame:
    """Join 5m metrics onto 1m bars using the previous COMPLETED bucket.

    Bar open_time t uses the metrics row whose create_time is the last 5m
    boundary strictly before t (bucket [T, T+5m) is known at T+5m)."""
    if metrics is None or len(metrics) == 0:
        return feat
    m = metrics[["create_time", "sum_open_interest",
                 "sum_taker_long_short_vol_ratio",
                 "count_long_short_ratio"]].copy()
    m["create_time"] = m["create_time"].astype("int64") // 10**9 * 1000
    m = m.sort_values("create_time").drop_duplicates("create_time",
                                                     keep="last")
    bar_t = feat["open_time"].to_numpy()
    # previous completed 5m bucket boundary: floor((t-1)/5m)*5m
    bucket = ((bar_t - 1) // 300_000) * 300_000
    idx = np.searchsorted(m["create_time"].to_numpy(), bucket,
                          side="right") - 1
    oi = np.full(len(feat), np.nan)
    ratio = np.full(len(feat), np.nan)
    lsr = np.full(len(feat), np.nan)
    valid = idx >= 0
    mt = m["create_time"].to_numpy()
    oi[valid] = m["sum_open_interest"].to_numpy()[idx[valid]]
    ratio[valid] = m["sum_taker_long_short_vol_ratio"].to_numpy()[idx[valid]]
    lsr[valid] = m["count_long_short_ratio"].to_numpy()[idx[valid]]

    out = feat.copy()
    out["oi"] = oi
    out["oi_chg5"] = _logret(oi, 1) * 1.0                # 5m pct change (frac)
    out["oi_chg30"] = _logret(oi, 6)                    # 30m change
    out["oi_chg120"] = _logret(oi, 24)                  # 2h change
    out["oi_chg480"] = _logret(oi, 96)                  # 8h change
    out["oi_z"] = (oi - _roll_mean(oi, 288)) / np.maximum(
        _roll_std(oi, 288), 1e-9)                       # vs 24h
    out["taker_imb"] = (ratio - 1.0) / (ratio + 1.0)    # [-1, 1]
    out["taker_imb_z"] = (out["taker_imb"] - _roll_mean(
        out["taker_imb"].to_numpy(), 288)) / np.maximum(
            _roll_std(out["taker_imb"].to_numpy(), 288), 1e-9)
    out["lsr"] = lsr

    # --- liquidation-surge proxies (price move x OI flush in same direction)
    r5 = np.nan_to_num(out["r5"].to_numpy())
    oi5 = np.nan_to_num(out["oi_chg5"].to_numpy())
    out["liq_long"] = np.maximum(0.0, -r5) * np.maximum(0.0, -oi5)
    out["liq_short"] = np.maximum(0.0, r5) * np.maximum(0.0, oi5)
    out["liq_net"] = out["liq_long"] - out["liq_short"]

    # --- price/OI divergence (continuous)
    out["p_oi_div"] = r5 - 10.0 * oi5

    return out


def align_funding(feat: pd.DataFrame, funding: pd.DataFrame) -> pd.DataFrame:
    """Attach the most recent funding mark <= t (8h marks)."""
    if funding is None or len(funding) == 0:
        return feat
    f = funding[["calc_time", "last_funding_rate"]].copy()
    f["calc_time"] = f["calc_time"].astype("int64")
    f = f.sort_values("calc_time").drop_duplicates("calc_time", keep="last")
    bar_t = feat["open_time"].to_numpy()
    idx = np.searchsorted(f["calc_time"].to_numpy(), bar_t, side="right") - 1
    fr = np.full(len(feat), np.nan)
    valid = idx >= 0
    fr[valid] = f["last_funding_rate"].to_numpy()[idx[valid]]

    out = feat.copy()
    out["funding"] = fr                                     # per 8h, signed
    out["funding_ann"] = fr * 3 * 365 * 100                 # annualized %
    # funding momentum: current vs mean of previous 8 marks
    prev = _shift(fr, 1)
    out["funding_chg"] = fr - prev
    out["funding_z"] = (fr - _roll_mean(fr, 288)) / np.maximum(
        _roll_std(fr, 288), 1e-9)
    return out


# ----------------------------------------------------------------------
# cross-asset joins
# ----------------------------------------------------------------------

def join_btc(feat: pd.DataFrame, btc_feat: pd.DataFrame) -> pd.DataFrame:
    """Attach BTC same-minute returns and relative strength."""
    cols = ["open_time", "r1", "r5", "r15", "r30", "r60", "rvol", "rv5",
            "rv60", "vol_ratio"]
    b = btc_feat[cols].rename(columns={x: f"btc_{x}" for x in cols
                                       if x != "open_time"})
    out = feat.merge(b, on="open_time", how="left")
    out["rs15"] = out["r15"] - out["btc_r15"]          # relative strength 15m
    out["rs60"] = out["r60"] - out["btc_r60"]
    out["beta_proxy"] = out["rv5"] / np.maximum(out["btc_rv5"], 1e-9)
    return out


def join_breadth(feat: pd.DataFrame, breadth: pd.DataFrame) -> pd.DataFrame:
    """Attach market-wide breadth at 5m (forward-filled to 1m)."""
    if breadth is None or len(breadth) == 0:
        return feat
    b = breadth.sort_values("open_time")
    times = b["open_time"].to_numpy()
    idx = np.searchsorted(times, feat["open_time"].to_numpy(),
                          side="right") - 1
    valid = idx >= 0
    out = feat.copy()
    for col in b.columns:
        if col == "open_time":
            continue
        vals = np.full(len(feat), np.nan)
        vals[valid] = b[col].to_numpy()[idx[valid]]
        out[f"mkt_{col}"] = vals
    return out


# ----------------------------------------------------------------------
# chunked driver
# ----------------------------------------------------------------------

def compute_symbol(symbol: str, df: pd.DataFrame, metrics: pd.DataFrame | None,
                   funding: pd.DataFrame | None,
                   btc_feat: pd.DataFrame | None = None,
                   breadth: pd.DataFrame | None = None) -> pd.DataFrame:
    feat = compute_base(df)
    if metrics is not None:
        feat = align_metrics(feat, metrics)
    if funding is not None:
        feat = align_funding(feat, funding)
    if btc_feat is not None and symbol != "BTCUSDT":
        feat = join_btc(feat, btc_feat)
    if breadth is not None:
        feat = join_breadth(feat, breadth)
    return feat
