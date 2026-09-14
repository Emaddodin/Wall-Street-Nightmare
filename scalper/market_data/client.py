"""Bitunix USDT-M futures public REST client -- self-contained.

Written for this project from the public API; shares no code with the
production engine.  Only public endpoints are used (no keys): the same data a
backtest, a paper trader, or a research collector needs.

Known venue quirks handled here (measured, not copied):
  * /market/kline returns bars NEWEST FIRST; everything here returns
    oldest-first DataFrames.
  * volume field naming is inverted between endpoints -- auto-detected by
    reconciling with price rather than trusting the field name.
  * requests without a browser-ish User-Agent are 403'd.
  * every kline request is capped at 200 bars regardless of `limit`.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import pandas as pd
import requests

REST_BASE = "https://fapi.bitunix.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
KLINE_PAGE_CAP = 200

log = logging.getLogger("scalper.market_data")


class MarketDataError(RuntimeError):
    pass


def _usdt_volume(row: dict, price: float) -> float:
    """Which field holds USDT turnover?  Reconcile with price instead of
    trusting names: the field that equals the other one * price (within
    rounding) is the base-asset amount; the other is USDT."""
    bv = float(row.get("baseVol") or 0.0)
    qv = float(row.get("quoteVol") or 0.0)
    if price <= 0:
        return max(bv, qv)
    # USDT turnover = base * price.  The larger of {bv, qv, bv*price, qv*price}
    # is normally the USDT figure; pick via reconciliation.
    if abs(qv - bv * price) / max(qv, 1e-9) < 0.05 and qv > 0:
        return qv
    if abs(bv - qv * price) / max(bv, 1e-9) < 0.05 and bv > 0:
        return bv
    return max(bv, qv, bv * price, qv * price)


class BitunixPublic:
    def __init__(self, base: str = REST_BASE, pause: float = 0.05,
                 timeout: float = 15.0, session: requests.Session | None = None):
        self.base = base
        self.pause = pause
        self.timeout = timeout
        self.s = session or requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept": "application/json"})

    # ------------------------------------------------------------------
    def _request(self, method: str, path: str, params: dict | None = None) -> Any:
        url = f"{self.base}{path}"
        last = None
        for attempt in range(5):
            try:
                r = self.s.request(method, url, params=params, timeout=self.timeout)
                if r.status_code == 429:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                body = r.json()
                code = body.get("code")
                msg = body.get("msg", "")
                if code not in (0, None, "0"):
                    # "System error" is the venue's overload signal -- retry it
                    # with backoff like any transient failure; anything else is
                    # a real error and is reported as-is.
                    if "system error" in str(msg).lower() or r.status_code >= 500:
                        time.sleep(1.0 * (2 ** attempt))
                        last = MarketDataError(f"{path}: {msg[:200]}")
                        continue
                    raise MarketDataError(f"{path}: {msg[:200]}")
                return body.get("data")
            except requests.RequestException as e:
                last = e
                time.sleep(1.0 * (2 ** attempt))
        raise MarketDataError(f"{method} {path} failed: {last}")

    # ------------------------------------------------------------------
    def trading_pairs(self) -> dict[str, dict]:
        rows = self._request("GET", "/api/v1/futures/market/trading_pairs")
        return {d["symbol"]: d for d in rows}

    def tickers(self) -> list[dict]:
        rows = self._request("GET", "/api/v1/futures/market/tickers")
        out = []
        for r in rows:
            price = float(r.get("lastPrice") or r.get("last") or 0.0)
            out.append({
                "symbol": r["symbol"],
                "price": price,
                "mark": float(r.get("markPrice") or 0.0),
                "usdt_volume_24h": _usdt_volume(r, price),
            })
        return out

    def last_price(self, symbol: str) -> float | None:
        """The live last-trade price for one symbol (fast ticker path).
        The endpoint may ignore the symbol filter and return the whole
        market, so ALWAYS match on the symbol field."""
        try:
            rows = self._request("GET", "/api/v1/futures/market/tickers",
                                 {"symbol": symbol})
            if rows:
                rows = rows if isinstance(rows, list) else [rows]
                for r in rows:
                    if r.get("symbol") != symbol:
                        continue
                    px = float(r.get("lastPrice") or r.get("last") or 0.0)
                    if px > 0:
                        return px
        except Exception:
            pass
        # fallback: scan the full ticker list
        try:
            for t in self.tickers():
                if t["symbol"] == symbol and t["price"] > 0:
                    return t["price"]
        except Exception:
            return None
        return None

    def depth(self, symbol: str, limit: int = 5) -> dict:
        return self._request("GET", "/api/v1/futures/market/depth",
                             params={"symbol": symbol, "limit": limit})

    def spread_bps(self, symbol: str) -> float:
        """Top-of-book spread in basis points; inf when unreadable."""
        try:
            d = self.depth(symbol, 1)
            bid, ask = float(d["bids"][0][0]), float(d["asks"][0][0])
        except (KeyError, IndexError, TypeError, ValueError):
            return float("inf")
        mid = (bid + ask) / 2
        if mid <= 0:
            return float("inf")
        return (ask - bid) / mid * 10_000.0

    def book_liquidity_usdt(self, symbol: str, levels: int = 5) -> float:
        """USDT sitting in the top-N levels of both sides -- the honest
        'sufficient order-book liquidity' measure."""
        try:
            d = self.depth(symbol, levels)
            total = 0.0
            for side in ("bids", "asks"):
                for row in d.get(side, []):
                    total += float(row[0]) * float(row[1])
            return total
        except (KeyError, IndexError, TypeError, ValueError):
            return 0.0

    # ------------------------------------------------------------------
    def klines_page(self, symbol: str, interval: str, limit: int = 200,
                    end_ms: int | None = None) -> pd.DataFrame:
        """One page of candles, oldest first (venue returns newest first)."""
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        if end_ms is not None:
            params["endTime"] = end_ms
        rows = self._request("GET", "/api/v1/futures/market/kline", params=params) or []
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        recs = []
        for k in rows:
            close = float(k["close"])
            recs.append({
                "open_time": int(k["time"]),
                "open": float(k["open"]),
                "high": float(k["high"]),
                "low": float(k["low"]),
                "close": close,
                "volume": _usdt_volume(k, close),
            })
        df = pd.DataFrame(recs)
        df = df.drop_duplicates(subset="open_time", keep="last")
        return df.sort_values("open_time").reset_index(drop=True)

    def klines(self, symbol: str, interval: str, bars: int,
               end_ms: int | None = None) -> pd.DataFrame:
        """Page backwards to assemble up to `bars` candles, oldest first.

        Stops when a page returns nothing new (guards against the endpoint
        echoing the same page forever)."""
        step_ms = {"1m": 60_000, "3m": 180_000, "5m": 300_000,
                   "15m": 900_000, "1h": 3_600_000}.get(interval, 60_000)
        frames: list[pd.DataFrame] = []
        cursor = end_ms or int(time.time() * 1000)
        got = 0
        while got < bars:
            page = self.klines_page(symbol, interval, KLINE_PAGE_CAP, cursor)
            if page.empty:
                break
            frames.append(page)
            got += len(page)
            oldest = int(page["open_time"].iloc[0])
            if oldest >= cursor:              # no progress -> stop, not loop
                break
            cursor = oldest - step_ms
            if len(page) < KLINE_PAGE_CAP:
                break
            time.sleep(self.pause)
        if not frames:
            return pd.DataFrame(columns=["open_time", "open", "high", "low",
                                         "close", "volume"])
        out = pd.concat(frames, ignore_index=True)
        out = out.drop_duplicates(subset="open_time", keep="last")
        return out.sort_values("open_time").reset_index(drop=True).tail(bars)
