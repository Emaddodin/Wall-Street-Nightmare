"""Historical port of the production coin finder (atrscan.py).

atrscan.py -- the operator's measured scanner (Bitunix live) -- ranks by
ATR%: the mean TRUE range over `atr_bars` x 15m candles as a percentage of
the last price.  Measured over 163 real signals:

    ATR < 2.5%      ->  -54.6% per trade
    ATR 2.5-4.0%    ->  +57.6%
    ATR 4.0-5.5%    ->  +44.0%
    ATR > 5.5%      -> +128.8%

24h volume is a FLOOR (default $1M), never a ranking term -- ranking by
volume hands the list back to BTC/ETH, which do not move enough to reach
the target at all.  Flat (low-ATR) and illiquid (thin volume, wide spread
proxy) symbols never trade.

Universe note: atrscan's 2.5% ATR floor was measured on Bitunix's new
listings (NIULAIUSDT etc.).  On the Binance USDT-perp universe the 15m
ATR% distribution sits at ~0.6% median / ~1.0% p90 / ~1.5% p99.  Phase 7
tested BOTH sets on the 5m system and the entry-level audit rejected the
tight set: top-20/ATR>=1.0% cut gross from +5.05 to +1.75 bps/entry (the
excluded 0.8-1.0% band carried ~+8 bps/entry).  The PRODUCTION universe
is the phase-3 loose set: top-30 / ATR >= 0.8%.

The book-spread filter from the live scanner is OFF by default: the honest
candle proxy (median 1m high-low range) runs 13-20+ bps on exactly the
high-ATR movers this finder selects, while the live 8 bps ceiling refers
to the real top-of-book spread (1-4 bps) that candles cannot reconstruct.
Volume floor + ATR floor + ATR rank remain the illiquidity guards.

scan_daily() reproduces those numbers from historical candles, strictly
causally: a symbol is eligible on UTC day D only if, using candles closed
<= D's 00:00 boundary, it passes the floors and ranks in the top-N by ATR%.
Returns {(symbol, day_ms) -> True} plus a per-day summary frame for audit.

(The live pick files data/picked.json, data/eligible.json are venue
snapshots and cannot be replayed historically -- the LOGIC is what gets
wired into the backtest, exactly as atrscan computes it.)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant.strategies.ict_sniper import resample_causal_1m

DAY_MS = 86_400_000
MIN_MS = 60_000


def _tr_pct(df15: pd.DataFrame) -> np.ndarray:
    """Mean true range over `atr_bars` 15m bars, % of last close
    (atrscan.atr_pct semantics: simple average, not Wilder)."""
    hi = df15["high"].to_numpy(dtype=np.float32)
    lo = df15["low"].to_numpy(dtype=np.float32)
    c = df15["close"].to_numpy(dtype=np.float32)
    pc = np.empty_like(c)
    pc[0] = c[0]
    pc[1:] = c[:-1]
    tr = np.maximum(hi - lo, np.maximum(np.abs(hi - pc), np.abs(lo - pc)))
    return tr


def scan_daily(frames: dict[str, pd.DataFrame], start_ms: int, end_ms: int,
               top_n: int = 30, min_atr_pct: float = 0.8,
               min_vol_usdt: float = 1e6, max_spread_bps: float | None = None,
               atr_bars: int = 14, tf_ms: int = 900_000
               ) -> tuple[dict[tuple[str, int], bool], pd.DataFrame]:
    """Dynamic per-day eligibility from candles only (no lookahead).

    frames: {symbol: 1m DataFrame with open_time >= start_ms - warmup}.
    Callers must load ~48h of warmup BEFORE start_ms so day-0 eligibility
    has a full trailing 24h and a populated ATR window.
    """
    days = np.arange(start_ms // DAY_MS * DAY_MS, end_ms, DAY_MS)
    eligible: dict[tuple[str, int], bool] = {}
    rows: list[dict] = []
    for sym, df in frames.items():
        t = df["open_time"].to_numpy(dtype=np.int64)
        if len(t) < 1440:
            continue
        v = df["volume"].to_numpy(dtype=np.float64)
        hi = df["high"].to_numpy(dtype=np.float32)
        lo = df["low"].to_numpy(dtype=np.float32)
        c = df["close"].to_numpy(dtype=np.float32)
        vol24 = pd.Series(v).rolling(1440, min_periods=1440).sum().to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            spread1m = (hi - lo) / c * 1e4
        spread24 = (pd.Series(spread1m)
                    .rolling(1440, min_periods=1440).median().to_numpy())
        df15 = resample_causal_1m(df, tf_ms)
        t15_close = df15["open_time"].to_numpy(np.int64) + tf_ms
        tr = _tr_pct(df15)
        tr_mean = (pd.Series(tr).rolling(atr_bars, min_periods=atr_bars)
                   .mean().to_numpy())
        c15 = df15["close"].to_numpy(dtype=np.float32)
        with np.errstate(divide="ignore", invalid="ignore"):
            atr_pct = tr_mean / c15 * 100.0
        for D in days:
            j = int(np.searchsorted(t, D, side="left")) - 1   # last bar < D
            k = int(np.searchsorted(t15_close, D, side="left")) - 1
            vol = vol24[j] if 0 <= j < len(vol24) else np.nan
            atp = atr_pct[k] if 0 <= k < len(atr_pct) else np.nan
            if not np.isfinite(vol) or not np.isfinite(atp):
                continue
            if vol < min_vol_usdt or atp < min_atr_pct:
                continue
            if max_spread_bps is not None:
                spd = spread24[j] if 0 <= j < len(spread24) else np.nan
                if not np.isfinite(spd) or spd > max_spread_bps:
                    continue
            rows.append({"symbol": sym, "day": int(D), "atr_pct": float(atp),
                         "vol24_usdt": float(vol),
                         "spread_bps": float(spread24[j])
                         if 0 <= j < len(spread24) else np.nan})
    summary = pd.DataFrame(rows)
    if not len(summary):
        return eligible, pd.DataFrame(columns=["day", "n_eligible", "n_pool",
                                               "top_atr_pct"])
    # per day: rank by ATR% (the measured ranking term), keep top_n
    per_day = []
    for D in days:
        g = summary[summary["day"] == D]
        keep = g.nlargest(top_n, "atr_pct")
        for s in keep["symbol"]:
            eligible[(s, int(D))] = True
        per_day.append({"day": int(D), "n_eligible": len(keep),
                        "n_pool": len(g),
                        "top_atr_pct": float(keep["atr_pct"].max())
                        if len(keep) else np.nan})
    return eligible, pd.DataFrame(per_day)
