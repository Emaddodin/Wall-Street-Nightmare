"""
Bitunix USDT-M futures REST client.

Signature scheme (verified against openapidoc.bitunix.com/doc/common/sign.html):

    digest = SHA256(nonce + timestamp + api_key + queryParams + body)
    sign   = SHA256(digest + secret_key)

  * queryParams : params sorted by key ascending ASCII, concatenated as
                  key+value with no separators  ("id" -> 1, "uid" -> 200 => "id1uid200")
  * body       : the compact JSON string, byte-identical to what is sent
  * headers    : api-key, nonce, timestamp, sign, language, Content-Type

Two live-verified quirks are handled here, both of which silently corrupt
numbers if you trust the obvious reading:

  1. The volume field naming is INVERTED between endpoints. In /market/kline
     `baseVol` holds the USDT turnover and `quoteVol` holds the base-asset
     amount; in /market/tickers it is the other way round. We auto-detect per
     payload by checking which field reconciles with price rather than trusting
     either name.
  2. /market/kline returns bars NEWEST FIRST. Every indicator assumes oldest
     first, so we always sort ascending.

The API also 403s without a browser-ish User-Agent.
"""
from __future__ import annotations

import hashlib
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
import json
import logging
import os
import random
import string
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

# How stale the instrument list may be before a missing symbol is worth
# re-reading it for.
PAIRS_TTL = 600.0

REST_BASE = "https://fapi.bitunix.com"
WS_PUBLIC = "wss://fapi.bitunix.com/public/"
WS_PRIVATE = "wss://fapi.bitunix.com/private/"


class BitunixError(RuntimeError):
    """Non-zero `code` in a Bitunix response body."""

    def __init__(self, code: Any, msg: str, path: str = ""):
        self.code, self.msg, self.path = code, msg, path
        super().__init__(f"Bitunix error {code} on {path}: {msg}")


class BitunixUnknown(RuntimeError):
    """A write whose outcome nobody knows.

    The request was sent and no answer came back. That is NOT the same as a
    failure: the order may be resting on the exchange right now. It is raised
    instead of retrying, because retrying a place_order after a timeout is how
    one signal becomes two positions -- and the second one appears in the
    account, not in the book, so nothing here would ever notice it.

    A caller that gets this must go and look before deciding anything.
    """

    def __init__(self, path: str, client_id: str | None, cause: Exception):
        self.path, self.client_id, self.cause = path, client_id, cause
        super().__init__(
            f"{path} was sent and never answered ({cause}); the order may or "
            f"may not exist. Check clientId={client_id!r} before retrying.")


def _nonce(n: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choices(alphabet, k=n))


def _usdt_volume(row: dict, price: float) -> float:
    """
    Return the USDT turnover from a payload whose volume fields may be named
    either way round. Pick whichever assignment reconciles with price.
    """
    try:
        qv, bv = float(row.get("quoteVol", 0) or 0), float(row.get("baseVol", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    if price <= 0 or qv <= 0 or bv <= 0:
        return max(qv, bv)
    # If quoteVol is the base amount, quoteVol*price ~= baseVol, and vice versa.
    return bv if abs(qv * price - bv) < abs(bv * price - qv) else qv


class BitunixClient:
    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str = REST_BASE,
        timeout: int = 15,
    ):
        self.api_key = api_key or os.getenv("BITUNIX_API_KEY", "")
        self.api_secret = api_secret or os.getenv("BITUNIX_API_SECRET", "")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._s = requests.Session()
        self._s.headers.update({"User-Agent": "Mozilla/5.0", "language": "en-US"})
        self._pairs_cache: dict[str, dict] | None = None
        self._pairs_at: float = 0.0

    # ------------------------------------------------------------- signing
    def _sign(self, nonce: str, ts: str, query: str, body: str) -> str:
        digest = hashlib.sha256(
            (nonce + ts + self.api_key + query + body).encode()
        ).hexdigest()
        return hashlib.sha256((digest + self.api_secret).encode()).hexdigest()

    @staticmethod
    def _query_string(params: dict | None) -> str:
        """Sorted key+value concatenation, exactly as the signing spec requires."""
        if not params:
            return ""
        return "".join(f"{k}{params[k]}" for k in sorted(params))

    def _request(
        self, method: str, path: str, params: dict | None = None,
        body: dict | None = None, signed: bool = False,
        replayable: bool = True,
    ) -> Any:
        """`replayable` is False for anything that creates or destroys an
        order. Those are sent exactly once: a transport failure on them raises
        BitunixUnknown rather than being retried, because the request may
        already have been executed and a second copy is a second position."""
        url = f"{self.base_url}{path}"
        params = {k: v for k, v in (params or {}).items() if v is not None}
        # separators=(",",":") gives the space-free form the signature requires,
        # and we send this exact string so body and signature can never diverge.
        body_str = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = {"Content-Type": "application/json"}

        if signed:
            if not self.api_key or not self.api_secret:
                raise BitunixError("AUTH", "API key/secret not configured", path)
            nonce, ts = _nonce(), str(int(time.time() * 1000))
            headers.update({
                "api-key": self.api_key,
                "nonce": nonce,
                "timestamp": ts,
                "sign": self._sign(nonce, ts, self._query_string(params), body_str),
            })

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                r = self._s.request(
                    method, url, params=params or None,
                    data=body_str.encode() if body_str else None,
                    headers=headers, timeout=self.timeout,
                )
                if r.status_code in (429, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {r.status_code}")
                r.raise_for_status()
                payload = r.json()
                code = payload.get("code")
                if str(code) not in ("0", "00000"):
                    # A rejected order is a business outcome, not a transport
                    # failure -- surface it immediately instead of retrying.
                    raise BitunixError(code, payload.get("msg", ""), path)
                return payload.get("data")
            except BitunixError:
                raise
            except Exception as e:                       # transport-level only
                last_exc = e
                if not replayable:
                    raise BitunixUnknown(
                        path, (body or {}).get("clientId"), e) from e
                if attempt < 2:
                    time.sleep(0.4 * (2 ** attempt))
        raise RuntimeError(f"{method} {path} failed after 3 attempts: {last_exc}")

    # -------------------------------------------------------------- public
    def trading_pairs(self, refresh: bool = False) -> dict[str, dict]:
        if self._pairs_cache is None or refresh:
            data = self._request("GET", "/api/v1/futures/market/trading_pairs")
            self._pairs_cache = {d["symbol"]: d for d in data}
            self._pairs_at = time.time()
        return self._pairs_cache

    def tickers(self) -> list[dict]:
        rows = self._request("GET", "/api/v1/futures/market/tickers")
        for r in rows:
            price = float(r.get("lastPrice") or r.get("last") or 0)
            r["usdt_volume_24h"] = _usdt_volume(r, price)
            r["price"] = price
        return rows

    def klines(self, symbol: str, interval: str = "1m", limit: int = 200):
        """
        OHLCV as a UTC-indexed frame, oldest first.
        `volume` is USDT turnover (auto-detected, see module docstring).
        """
        import pandas as pd
        data = self._request(
            "GET", "/api/v1/futures/market/kline",
            params={"symbol": symbol, "interval": interval, "limit": limit},
        )
        if not data:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        rows = []
        for k in data:
            close = float(k["close"])
            rows.append({
                "time": pd.to_datetime(int(k["time"]), unit="ms", utc=True),
                "open": float(k["open"]), "high": float(k["high"]),
                "low": float(k["low"]), "close": close,
                "volume": _usdt_volume(k, close),
            })
        # Endpoint returns newest-first; everything downstream needs oldest-first.
        return pd.DataFrame(rows).set_index("time").sort_index()

    def history(self, symbol: str, interval: str = "1m", bars: int = 5000,
                end_ms: int | None = None, pause: float = 0.05):
        """
        Page backwards to assemble more than the 200-bar per-request cap.

        Every kline request is capped at 200 regardless of `limit`, so any
        meaningful backtest window has to be stitched together. We walk backwards
        with `endTime`, stopping when a page returns nothing new -- guarding
        against the endpoint returning the same page forever, which would
        otherwise spin.
        """
        import pandas as pd
        step_ms = {"1m": 60_000, "5m": 300_000, "15m": 900_000,
                   "1h": 3_600_000}.get(interval, 60_000)
        frames: list[pd.DataFrame] = []
        cursor = end_ms or int(time.time() * 1000)
        got = 0
        while got < bars:
            params = {"symbol": symbol, "interval": interval, "limit": 200,
                      "endTime": cursor}
            data = self._request("GET", "/api/v1/futures/market/kline", params=params)
            if not data:
                break
            rows = []
            for k in data:
                close = float(k["close"])
                rows.append({
                    "time": pd.to_datetime(int(k["time"]), unit="ms", utc=True),
                    "open": float(k["open"]), "high": float(k["high"]),
                    "low": float(k["low"]), "close": close,
                    "volume": _usdt_volume(k, close),
                })
            df = pd.DataFrame(rows).set_index("time").sort_index()
            frames.append(df)
            got += len(df)
            oldest = int(df.index[0].timestamp() * 1000)
            if oldest >= cursor:          # no progress -- stop rather than loop
                break
            cursor = oldest - step_ms
            if len(data) < 200:
                break
            time.sleep(pause)
        if not frames:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        out = pd.concat(frames).sort_index()
        out = out[~out.index.duplicated(keep="last")]
        return out.tail(bars)

    def depth(self, symbol: str, limit: int = 5) -> dict:
        return self._request(
            "GET", "/api/v1/futures/market/depth",
            params={"symbol": symbol, "limit": limit},
        )

    def funding_rate(self, symbol: str) -> float | None:
        """The venue's current funding rate, as a fraction per funding
        interval (positive = longs pay shorts). None when the venue cannot
        be read -- the paper book then accrues nothing and says so once,
        rather than guessing a cost."""
        try:
            data = self._request("GET", "/api/v1/futures/market/fundingRate",
                                 params={"symbol": symbol})
        except Exception:
            return None
        return _parse_funding(data)

    def spread_bps(self, symbol: str) -> float:
        """Top-of-book spread in basis points -- the honest cost of a market entry."""
        d = self.depth(symbol, 1)
        try:
            bid, ask = float(d["bids"][0][0]), float(d["asks"][0][0])
        except (KeyError, IndexError, ValueError):
            return float("inf")
        mid = (bid + ask) / 2
        return ((ask - bid) / mid) * 10_000 if mid > 0 else float("inf")

    # ------------------------------------------------------------- private
    def account(self, margin_coin: str = "USDT") -> dict:
        data = self._request(
            "GET", "/api/v1/futures/account",
            params={"marginCoin": margin_coin}, signed=True,
        )
        return (data[0] if isinstance(data, list) else data) or {}

    def equity(self, margin_coin: str = "USDT") -> float:
        a = self.account(margin_coin)
        return (
            float(a.get("available", 0) or 0)
            + float(a.get("margin", 0) or 0)
            + float(a.get("crossUnrealizedPNL", 0) or 0)
            + float(a.get("isolationUnrealizedPNL", 0) or 0)
        )

    def positions(self, symbol: str | None = None) -> list[dict]:
        data = self._request(
            "GET", "/api/v1/futures/position/get_pending_positions",
            params={"symbol": symbol} if symbol else None, signed=True,
        )
        return data or []

    def history_positions(self, symbol: str | None = None,
                         position_id: str | None = None) -> list[dict]:
        """Closed positions, with the exchange's own realised PnL and fees."""
        data = self._request(
            "GET", "/api/v1/futures/position/get_history_positions",
            params={"symbol": symbol, "positionId": position_id}, signed=True)
        if isinstance(data, dict):
            data = data.get("positionList") or data.get("list") or []
        return data or []

    def position_mode(self, margin_coin: str = "USDT") -> str:
        """ONE_WAY or HEDGE. Decides whether orders may carry `tradeSide`."""
        return (self.account(margin_coin).get("positionMode") or "ONE_WAY").upper()

    def set_leverage(self, symbol: str, leverage: int, margin_coin: str = "USDT"):
        return self._request(
            "POST", "/api/v1/futures/account/change_leverage",
            body={"symbol": symbol, "leverage": str(leverage), "marginCoin": margin_coin},
            signed=True,
        )

    def set_margin_mode(self, symbol: str, mode: str = "ISOLATION", margin_coin: str = "USDT"):
        return self._request(
            "POST", "/api/v1/futures/account/change_margin_mode",
            body={"symbol": symbol, "marginMode": mode, "marginCoin": margin_coin},
            signed=True,
        )

    def place_market_order(
        self, symbol: str, side: str, qty: str,
        tp_price: str | None = None, sl_price: str | None = None,
        client_id: str | None = None, reduce_only: bool = False,
        stop_type: str = "MARK_PRICE", position_mode: str | None = None,
    ) -> dict:
        """
        Market entry with TP and SL attached in the same call.

        Attaching them matters: if SL were a follow-up request it could fail or
        land late, leaving a 20x position naked on a 1m chart. One call means
        the protective orders exist the instant the position does.
        """
        body: dict[str, Any] = {
            "symbol": symbol, "qty": str(qty), "side": side.upper(),
            "orderType": "MARKET",
        }
        # `tradeSide` is documented as hedge-mode only. Sending it on a one-way
        # account is not merely redundant -- the field changes how `side` is
        # interpreted, so an unconditional "OPEN" risks the exchange reading the
        # order differently than intended. Query the mode rather than assume it.
        mode = position_mode
        if mode is None:
            try:
                mode = self.position_mode()
            except Exception as e:
                log.warning("Could not read position mode (%s); assuming ONE_WAY", e)
                mode = "ONE_WAY"
        if mode == "HEDGE" and not reduce_only:
            body["tradeSide"] = "OPEN"
        if reduce_only:
            body["reduceOnly"] = True
            body.pop("tradeSide", None)
        if client_id:
            body["clientId"] = client_id
        if tp_price:
            body.update({"tpPrice": str(tp_price), "tpStopType": stop_type,
                         "tpOrderType": "MARKET"})
        if sl_price:
            body.update({"slPrice": str(sl_price), "slStopType": stop_type,
                         "slOrderType": "MARKET"})
        return self._request(
            "POST", "/api/v1/futures/trade/place_order", body=body, signed=True,
            replayable=False,
        )

    def place_limit_order(
        self, symbol: str, side: str, qty: str, price: str,
        tp_price: str | None = None, sl_price: str | None = None,
        client_id: str | None = None, post_only: bool = True,
        stop_type: str = "MARK_PRICE", position_mode: str | None = None,
    ) -> dict:
        """
        Limit entry, POST_ONLY by default so the order can only ever be a MAKER.

        POST_ONLY matters more than it looks: without it a limit priced at or
        through the book fills as a taker at 0.06%, silently costing the 8bps
        this whole path exists to save. With it, such an order is rejected
        instead -- a rejection we can see and retry, rather than a fee we cannot.
        """
        body: dict[str, Any] = {
            "symbol": symbol, "qty": str(qty), "price": str(price),
            "side": side.upper(), "orderType": "LIMIT",
            "effect": "POST_ONLY" if post_only else "GTC",
        }
        mode = position_mode
        if mode is None:
            try:
                mode = self.position_mode()
            except Exception:
                mode = "ONE_WAY"
        if mode == "HEDGE":
            body["tradeSide"] = "OPEN"
        if client_id:
            body["clientId"] = client_id
        if tp_price:
            body.update({"tpPrice": str(tp_price), "tpStopType": stop_type,
                         "tpOrderType": "MARKET"})
        if sl_price:
            body.update({"slPrice": str(sl_price), "slStopType": stop_type,
                         "slOrderType": "MARKET"})
        return self._request("POST", "/api/v1/futures/trade/place_order",
                             body=body, signed=True, replayable=False)

    def pending_orders(self, symbol: str | None = None) -> list[dict]:
        data = self._request("GET", "/api/v1/futures/trade/get_pending_orders",
                             params={"symbol": symbol} if symbol else None,
                             signed=True)
        if isinstance(data, dict):
            data = data.get("orderList") or data.get("list") or []
        return data or []

    def cancel_orders(self, symbol: str, order_ids: list[str] | None = None,
                      client_ids: list[str] | None = None) -> dict:
        """Cancel by exchange id or clientId. One of the two must be given."""
        lst = []
        for oid in (order_ids or []):
            lst.append({"orderId": str(oid)})
        for cid in (client_ids or []):
            lst.append({"clientId": str(cid)})
        if not lst:
            raise BitunixError("ARGS", "no order ids supplied", "cancel_orders")
        return self._request("POST", "/api/v1/futures/trade/cancel_orders",
                             body={"symbol": symbol, "orderList": lst},
                             signed=True, replayable=False)

    def order_detail(self, order_id: str | None = None,
                     client_id: str | None = None) -> dict:
        return self._request("GET", "/api/v1/futures/trade/get_order_detail",
                             params={"orderId": order_id, "clientId": client_id},
                             signed=True) or {}

    # Field names the venue has used for the same three facts. The payload is
    # read through this map rather than by picking one spelling and hoping:
    # guessing wrong here does not raise, it silently reports a filled order as
    # unfilled -- which would leave a real position with nothing watching it,
    # and would place the next one on top.
    _FILLED_QTY_KEYS = ("tradeQty", "dealVolume", "filledQty", "executedQty",
                        "cumQty", "dealQty", "volume")
    _AVG_PRICE_KEYS = ("avgPrice", "dealAvgPrice", "averagePrice", "dealPrice",
                       "tradePrice", "priceAvg")
    _STATUS_KEYS = ("status", "state", "orderStatus")

    @classmethod
    def _pick(cls, row: dict, keys, cast):
        for k in keys:
            if k in row and row[k] not in (None, ""):
                try:
                    return cast(row[k])
                except (TypeError, ValueError):
                    continue
        return None

    @classmethod
    def read_order(cls, row: dict | None) -> dict:
        """One order, said the same way whatever the venue called the fields.

        Returns {status, filled, avg_price, order_id, client_id, raw}, where
        `status` is one of: new, part_filled, filled, cancelled, rejected,
        unknown.

        `unknown` is deliberate and load-bearing. An order whose state cannot
        be read is not an order that did nothing -- the caller must treat it as
        possibly live and go and look at the position, never as a free slot to
        place another.
        """
        row = row or {}
        raw = str(cls._pick(row, cls._STATUS_KEYS, str) or "").strip().upper()
        filled = cls._pick(row, cls._FILLED_QTY_KEYS, float)
        avg = cls._pick(row, cls._AVG_PRICE_KEYS, float)
        norm = {
            "NEW": "new", "INIT": "new", "OPEN": "new", "LIVE": "new",
            "PENDING": "new", "SUBMITTED": "new", "CREATED": "new",
            "PART_FILLED": "part_filled", "PARTIALLY_FILLED": "part_filled",
            "PARTIAL_FILLED": "part_filled", "PARTIALLY FILLED": "part_filled",
            "PART-FILLED": "part_filled", "PARTIAL": "part_filled",
            "FILLED": "filled", "FULL_FILLED": "filled", "CLOSED": "filled",
            "COMPLETED": "filled", "DONE": "filled",
            "CANCELED": "cancelled", "CANCELLED": "cancelled",
            "EXPIRED": "cancelled", "CANCEL": "cancelled",
            "REJECTED": "rejected", "FAILED": "rejected",
        }.get(raw)
        if norm is None:
            # No status we recognise. Infer only what the quantity can prove,
            # and otherwise say so rather than assuming the order is idle.
            norm = "part_filled" if filled else "unknown"
        return {"status": norm, "filled": filled or 0.0, "avg_price": avg,
                "order_id": str(row.get("orderId") or ""),
                "client_id": str(row.get("clientId") or ""),
                "raw_status": raw, "raw": row}

    def order_state(self, order_id: str | None = None,
                    client_id: str | None = None) -> dict:
        """`read_order` applied to a live lookup. Never raises for 'not found'.

        A cancelled or fully-filled order can disappear from the venue's view
        entirely; an empty reply is reported as `unknown` so the caller goes
        and checks the position rather than concluding nothing happened.
        """
        try:
            row = self.order_detail(order_id=order_id, client_id=client_id)
        except BitunixError as e:
            log.info("order %s: %s", order_id or client_id, str(e)[:120])
            return {"status": "unknown", "filled": 0.0, "avg_price": None,
                    "order_id": str(order_id or ""),
                    "client_id": str(client_id or ""), "raw_status": "",
                    "raw": {}}
        if isinstance(row, list):
            row = row[0] if row else {}
        return self.read_order(row)

    def flash_close(self, position_id: str) -> dict:
        return self._request(
            "POST", "/api/v1/futures/trade/flash_close_position",
            body={"positionId": str(position_id)}, signed=True,
            replayable=False,
        )

    # ------------------------------------------------------- instrument math
    def _pair(self, symbol: str) -> dict:
        """The instrument's own numbers, or a refusal.

        These used to fall back to basePrecision 4 / quotePrecision 2 for any
        symbol the cache had not seen. That is a guess wearing the clothes of
        a fact: a coin whose real base precision is 0 would be sent a quantity
        with four decimals and rejected, and one whose precision is larger
        would be sent a size nobody chose. A newly listed pair, or a cache
        fetched before the pair existed, is exactly when this happens -- and
        the scanner walks every pair on the venue. Refusing is recoverable;
        sizing an order on a guess is not.
        """
        got = self.trading_pairs().get(symbol)
        if got is None and time.time() - self._pairs_at > PAIRS_TTL:
            # A pair listed after this process started is the one honest
            # reason for a miss. Re-reading the list on EVERY miss instead
            # would send one request per unlisted symbol, and the scanner
            # walks every pair on the venue.
            got = self.trading_pairs(refresh=True).get(symbol)
        if got is None:
            raise BitunixError("SYMBOL", f"{symbol} is not a listed pair",
                               "trading_pairs")
        return got

    def round_qty(self, symbol: str, qty: float) -> float:
        """
        Floor quantity to the symbol's base precision.

        Floor, never round: rounding up can exceed the intended risk or the
        available margin and get the order rejected outright.

        Uses Decimal because binary floats cannot represent most decimal steps.
        The naive `int(qty/step)*step` yields values like 25.740000000000002 for
        a 2-decimal symbol, and `str()` of that goes into the request body --
        exceeding the declared precision and getting the order rejected. Since
        the body must be byte-identical to what was signed, there is no later
        chance to clean it up.
        """
        prec = int(self._pair(symbol)["basePrecision"])
        q = Decimal(str(qty)).quantize(Decimal(1).scaleb(-prec), rounding=ROUND_DOWN)
        return max(0.0, float(q))

    def fmt_qty(self, symbol: str, qty: float) -> str:
        """Quantity as the exact decimal string to put in the request body."""
        prec = int(self._pair(symbol)["basePrecision"])
        q = Decimal(str(qty)).quantize(Decimal(1).scaleb(-prec), rounding=ROUND_DOWN)
        return format(q.normalize() if q == q.to_integral() else q, "f")

    def round_price(self, symbol: str, price: float) -> float:
        prec = int(self._pair(symbol)["quotePrecision"])
        return float(Decimal(str(price)).quantize(
            Decimal(1).scaleb(-prec), rounding=ROUND_HALF_UP))

    def fmt_price(self, symbol: str, price: float) -> str:
        prec = int(self._pair(symbol)["quotePrecision"])
        p = Decimal(str(price)).quantize(Decimal(1).scaleb(-prec),
                                         rounding=ROUND_HALF_UP)
        return format(p, "f")

    def min_qty(self, symbol: str) -> float:
        return float(self._pair(symbol).get("minTradeVolume", 0) or 0)

    def max_leverage(self, symbol: str) -> int:
        return int(self._pair(symbol).get("maxLeverage", 20) or 20)


def _parse_funding(data) -> float | None:
    """Defensive parse of the venue's funding reply; None when unreadable."""
    if isinstance(data, dict):
        v = data.get("fundingRate")
    elif isinstance(data, list) and data:
        v = (data[0] or {}).get("fundingRate")
    else:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None
