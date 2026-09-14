"""Universe selection.

Two modes share one ranking function:

  * live     -- current tickers + order-book depth from the exchange;
  * historical -- everything derived from a symbol's own candles up to a
                  rebalance timestamp (what the backtester uses; no forward
                  information).

Filters: 24h volume floor, spread ceiling, book liquidity floor, ATR floor,
and a minimum history length.  Survivors are ranked by a weighted blend of
liquidity, volume, volatility, trend strength and spread quality; only the
top N trade.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from config.loader import Config
from indicators import atr, ema
from market_data.store import resample_ohlcv


def _rank_rows(rows: pd.DataFrame, weights: dict, vol_cap: float) -> pd.DataFrame:
    """Percentile-rank each metric, blend with weights, highest first."""
    out = rows.copy()
    out["vola"] = out["vola"].clip(upper=vol_cap)
    for col in ("liquidity", "volume", "vola", "trend"):
        out[f"{col}_pct"] = out[col].rank(pct=True)
    out["spread_pct"] = (1.0 - out["spread"].rank(pct=True))
    out["rank"] = (
        weights["liquidity"] * out["liquidity_pct"]
        + weights["volume"] * out["volume_pct"]
        + weights["volatility"] * out["vola_pct"]
        + weights["trend"] * out["trend_pct"]
        + weights["spread"] * out["spread_pct"]
    )
    return out.sort_values("rank", ascending=False)


def select_live(client, cfg: Config) -> pd.DataFrame:
    """Current universe: tickers + pairs + depth.  Columns:
    symbol, price, volume, spread, liquidity, vola, trend, atr_pct, rank."""
    pairs = client.trading_pairs()
    ticks = {t["symbol"]: t for t in client.tickers()}
    rows = []
    for sym, t in ticks.items():
        meta = pairs.get(sym) or {}
        if meta.get("symbolStatus") != "OPEN":
            continue
        if meta.get("isApiSupported") is False:
            continue
        vol = t["usdt_volume_24h"]
        if vol < cfg.universe["min_volume_usdt_24h"]:
            continue
        spread = client.spread_bps(sym)
        liq = client.book_liquidity_usdt(sym)
        try:
            k = client.klines(sym, "15m", 300)
        except Exception:
            k = pd.DataFrame()
        if len(k) < 50:
            continue
        atr_s = atr(k, cfg.stop["atr_period"])
        atr_pct = float(atr_s.iloc[-1] / k["close"].iloc[-1] * 100.0) if len(atr_s.dropna()) else 0.0
        if atr_pct < cfg.universe["min_atr_pct"]:
            continue
        e50 = ema(k["close"], 50)
        e200 = ema(k["close"], 200)
        trend = abs(float(e50.iloc[-1] / e200.iloc[-1] - 1.0)) if not math.isnan(e200.iloc[-1]) and e200.iloc[-1] else 0.0
        rows.append({
            "symbol": sym, "price": t["price"], "volume": vol,
            "spread": spread if math.isfinite(spread) else 1e9,
            "liquidity": liq, "vola": atr_pct, "trend": trend,
            "atr_pct": atr_pct,
        })
    if not rows:
        return pd.DataFrame(columns=["symbol", "price", "volume", "spread",
                                     "liquidity", "vola", "trend", "atr_pct", "rank"])
    rows = pd.DataFrame(rows)
    rows = rows[rows["spread"] <= cfg.universe["max_spread_bps"]]
    rows = rows[rows["liquidity"] >= cfg.universe["min_book_usdt"]]
    out = _rank_rows(rows, cfg.universe["rank_weights"],
                     cfg.universe["volatility_cap_atr_pct"])
    return out.head(cfg.universe["top_n"]).reset_index(drop=True)


def select_historical(df1m: pd.DataFrame, up_to_ms: int, cfg: Config,
                      min_history_days: float | None = None) -> dict | None:
    """Rank ONE symbol using only candles with open_time <= up_to_ms.

    Returns a metrics dict, or None when the symbol fails a filter.  The
    backtester calls this for every candidate at every daily rebalance.
    All measures are causal: volume is the trailing 24h, spread is the
    trailing median of the 1m (high-low)/close proxy (honest: no historical
    order book exists), ATR/trend come from the 15m resample.
    """
    hist = df1m[df1m["open_time"] <= up_to_ms]
    need = int((min_history_days if min_history_days is not None
                else cfg.universe["min_history_days"]) * 1440)
    if len(hist) < need:
        return None
    day = up_to_ms - 86_400_000
    last24 = hist[hist["open_time"] > day]
    if len(last24) < 300:
        return None
    volume = float(last24["volume"].sum())
    if volume < cfg.universe["min_volume_usdt_24h"]:
        return None
    spread = float(((last24["high"] - last24["low"]) / last24["close"]
                    .replace(0.0, np.nan) * 10_000).median())
    if not math.isfinite(spread) or spread > cfg.universe["max_spread_bps"]:
        return None
    k15 = resample_ohlcv(hist, 15)
    if len(k15) < 205:
        return None
    atr_s = atr(k15, cfg.stop["atr_period"])
    atr_pct = float(atr_s.iloc[-1] / k15["close"].iloc[-1] * 100.0)
    if not math.isfinite(atr_pct) or atr_pct < cfg.universe["min_atr_pct"]:
        return None
    e50 = ema(k15["close"], 50)
    e200 = ema(k15["close"], 200)
    trend = 0.0
    if e200.iloc[-1] and not math.isnan(e200.iloc[-1]):
        trend = abs(float(e50.iloc[-1] / e200.iloc[-1] - 1.0))
    last_close = float(hist["close"].iloc[-1])
    return {
        "symbol": None,  # filled by caller
        "volume": volume,
        "spread": spread,
        "liquidity": volume,          # honest proxy: turnover stands in for depth
        "vola": atr_pct,
        "trend": float(trend),
        "atr_pct": atr_pct,
        "last_close": last_close,
    }


def rank_historical(candidates: dict[str, pd.DataFrame], up_to_ms: int,
                    cfg: Config) -> list[str]:
    """Rank every candidate symbol as of `up_to_ms`; returns the ordered
    symbol list (best first), truncated to universe.top_n."""
    rows = []
    for sym, df1m in candidates.items():
        m = select_historical(df1m, up_to_ms, cfg)
        if m is None:
            continue
        m["symbol"] = sym
        rows.append(m)
    if not rows:
        return []
    out = _rank_rows(pd.DataFrame(rows), cfg.universe["rank_weights"],
                     cfg.universe["volatility_cap_atr_pct"])
    return out["symbol"].head(cfg.universe["top_n"]).tolist()
