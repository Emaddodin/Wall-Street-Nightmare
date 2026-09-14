"""
Stand-ins for the two things the engine cannot run without: the exchange and
the chart.

Both are deliberately *hostile by default* -- every one of them can be told to
time out, reject, duplicate, delay or lie, because that is what the real ones
do and the whole point of these tests is to find out what the engine does when
they misbehave.

Nothing here reaches the network. `FakeBitunix` records every order it is
asked to place, so a test can assert on what would have been sent without
anything being sent.
"""
from __future__ import annotations

import itertools
import json
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from exchange.bitunix import BitunixError, BitunixUnknown


class Timeout(Exception):
    """A transport failure -- the request may or may not have been executed."""


class Rejected(Exception):
    """The exchange understood the order and refused it."""


class FakeClock:
    """A wall clock the test owns.

    Patched over `time.time` and `time.sleep`; sleeping advances it, so a
    scenario covering half an hour of market runs in milliseconds and every
    age, deadline and cooldown in the engine is exercised for real.
    """

    def __init__(self, start: float):
        self.now = float(start)
        self.slept = 0.0

    def time(self) -> float:
        return self.now

    def sleep(self, secs: float) -> None:
        self.slept += secs
        self.now += secs

    def advance(self, secs: float) -> None:
        self.now += secs


class FakeBitunix:
    """A Bitunix that behaves, until a test tells it not to.

    Prices are driven by the test rather than by a market: `set_price` moves a
    symbol and everything downstream -- marks, fills, exit checks -- follows.
    """

    def __init__(self, prices=None, equity=100.0, spread_bps=4.0,
                 base_prec=3, quote_prec=4, min_qty=0.1, max_lev=50,
                 position_mode="ONE_WAY"):
        self.prices = dict(prices or {})
        self.marks = dict(self.prices)
        self._equity = equity
        self._spread = spread_bps
        self._funding = 0.0
        self.base_prec, self.quote_prec = base_prec, quote_prec
        self._min_qty, self._max_lev = min_qty, max_lev
        self._mode = position_mode

        # What a test inspects afterwards.
        self.orders: list[dict] = []
        self.calls: list[str] = []
        self.leverage_set: dict[str, int] = {}
        self.margin_mode_set: dict[str, str] = {}

        # What a test can break.
        self.fail_next: dict[str, list[Exception]] = {}
        self.ticker_error: Exception | None = None
        self.depth_error: Exception | None = None
        self.open_positions: list[dict] = []
        self.closed_positions: list[dict] = []
        self.order_seq = itertools.count(1000)
        # When set, place_market_order raises AFTER recording the order --
        # exactly the shape of a request that reached the exchange and whose
        # reply was lost.
        self.silent_success = False
        # The resting order book.
        self.resting: dict[str, dict] = {}
        self.cancelled: list = []
        # None = fill in full when reached; 0.4 = fill 40% and stop there,
        # which is what a thin book does to a size it cannot absorb.
        self.partial_fill_frac = None
        self.reject_post_only = True

    # ---------------------------------------------------------- test control
    def set_price(self, sym: str, px: float, mark: float | None = None):
        self.prices[sym] = px
        self.marks[sym] = px if mark is None else mark
        # A venue matches resting orders the moment price reaches them; a fake
        # that only moved the quote would let a test "prove" a fill that the
        # book had no reason to produce.
        self.step_book()

    def break_once(self, method: str, exc: Exception):
        self.fail_next.setdefault(method, []).append(exc)

    def _maybe_fail(self, method: str):
        self.calls.append(method)
        q = self.fail_next.get(method)
        if q:
            raise q.pop(0)

    # ---------------------------------------------------------------- public
    def tickers(self):
        self._maybe_fail("tickers")
        if self.ticker_error:
            raise self.ticker_error
        return [{"symbol": s, "lastPrice": str(p),
                 "markPrice": str(self.marks.get(s, p))}
                for s, p in self.prices.items()]

    def depth(self, symbol, limit=5):
        self._maybe_fail("depth")
        if self.depth_error:
            raise self.depth_error
        px = self.prices.get(symbol)
        if px is None:
            return {"bids": [], "asks": []}
        half = px * self._spread / 2 / 1e4
        # A deep ladder: any size the engine can send fills at the top
        # price, so the paper venue's book-walk reproduces the old
        # half-spread fill. Tests that want a THIN book override depth.
        rows = [[str(round(px + half, 10)), "1000000000"] for _ in range(20)]
        return {"bids": [[str(round(px - half, 10)), "1000000000"]] * 20,
                "asks": rows}

    def funding_rate(self, symbol):
        self._maybe_fail("funding_rate")
        return self._funding

    def spread_bps(self, symbol):
        self._maybe_fail("spread_bps")
        d = self.depth(symbol, 1)
        try:
            bid, ask = float(d["bids"][0][0]), float(d["asks"][0][0])
        except (KeyError, IndexError, ValueError):
            return float("inf")
        mid = (bid + ask) / 2
        return ((ask - bid) / mid) * 10_000 if mid > 0 else float("inf")

    def history(self, symbol, interval="15m", bars=200, **kw):
        """A flat, well-behaved series -- enough for ATR to be computable."""
        self._maybe_fail("history")
        import pandas as pd
        px = self.prices.get(symbol, 100.0)
        n = min(bars, 200)
        idx = pd.date_range("2026-09-01", periods=n, freq="15min", tz="UTC")
        return pd.DataFrame({
            "open": [px] * n, "high": [px * 1.002] * n,
            "low": [px * 0.998] * n, "close": [px] * n,
            "volume": [1e6] * n}, index=idx)

    def trading_pairs(self, refresh=False):
        self._maybe_fail("trading_pairs")
        return {s: {"basePrecision": self.base_prec,
                    "quotePrecision": self.quote_prec,
                    "minTradeVolume": self._min_qty,
                    "maxLeverage": self._max_lev} for s in self.prices}

    def max_leverage(self, symbol):
        self._maybe_fail("max_leverage")
        return self._max_lev

    def min_qty(self, symbol):
        self._maybe_fail("min_qty")
        return self._min_qty

    def round_qty(self, symbol, qty):
        q = Decimal(str(qty)).quantize(Decimal(1).scaleb(-self.base_prec),
                                       rounding=ROUND_DOWN)
        return max(0.0, float(q))

    def fmt_qty(self, symbol, qty):
        q = Decimal(str(qty)).quantize(Decimal(1).scaleb(-self.base_prec),
                                       rounding=ROUND_DOWN)
        return format(q.normalize() if q == q.to_integral() else q, "f")

    def fmt_price(self, symbol, price):
        p = Decimal(str(price)).quantize(Decimal(1).scaleb(-self.quote_prec),
                                         rounding=ROUND_HALF_UP)
        return format(p, "f")

    # --------------------------------------------------------------- private
    def equity(self, margin_coin="USDT"):
        self._maybe_fail("equity")
        return self._equity

    def position_mode(self, margin_coin="USDT"):
        self._maybe_fail("position_mode")
        return self._mode

    def positions(self, symbol=None):
        self._maybe_fail("positions")
        return [p for p in self.open_positions
                if symbol is None or p.get("symbol") == symbol]

    def history_positions(self, symbol=None, position_id=None):
        self._maybe_fail("history_positions")
        return [p for p in self.closed_positions
                if symbol is None or p.get("symbol") == symbol]

    def set_leverage(self, symbol, leverage, margin_coin="USDT"):
        self._maybe_fail("set_leverage")
        self.leverage_set[symbol] = int(leverage)
        return {}

    def set_margin_mode(self, symbol, mode="ISOLATION", margin_coin="USDT"):
        self._maybe_fail("set_margin_mode")
        self.margin_mode_set[symbol] = mode
        return {}

    def place_market_order(self, symbol, side, qty, tp_price=None,
                           sl_price=None, client_id=None, **kw):
        rec = {"symbol": symbol, "side": side, "qty": qty,
               "tp": tp_price, "sl": sl_price, "clientId": client_id}
        # Record BEFORE the failure: a lost reply still leaves a real order.
        # The real client turns a transport failure on this endpoint into
        # BitunixUnknown rather than retrying, so the fake must too -- a fake
        # that raises something else would let the engine take a branch the
        # exchange can never actually put it on.
        if self.silent_success:
            self.orders.append(rec)
            raise BitunixUnknown("/api/v1/futures/trade/place_order",
                                 client_id, Timeout("read timed out"))
        self._maybe_fail("place_market_order")
        self.orders.append(rec)
        oid = str(next(self.order_seq))
        rec["orderId"] = oid
        return {"orderId": oid}

    def flash_close(self, position_id):
        self._maybe_fail("flash_close")
        return {}

    # ------------------------------------------------------- resting orders
    # A real limit order book, small but honest: orders rest until price
    # reaches them, they fill at their own price or better, they can fill in
    # parts, and a post-only order that would cross is refused. Without this
    # the live entry path could only be tested against a stub that always
    # said yes, which tests nothing.
    def place_limit_order(self, symbol, side, qty, price, tp_price=None,
                          sl_price=None, client_id=None, post_only=True, **kw):
        px = float(price)
        mkt = self.prices.get(symbol)
        rec = {"symbol": symbol, "side": side, "qty": qty, "price": price,
               "tp": tp_price, "sl": sl_price, "clientId": client_id,
               "postOnly": post_only, "type": "LIMIT"}
        if self.silent_success:
            self.orders.append(rec)
            self._rest(rec)
            raise BitunixUnknown("/api/v1/futures/trade/place_order",
                                 client_id, Timeout("read timed out"))
        self._maybe_fail("place_limit_order")
        if post_only and mkt is not None and self.reject_post_only:
            crosses = (px >= mkt) if side.upper() == "BUY" else (px <= mkt)
            if crosses:
                raise BitunixError("10005",
                                   "order would immediately match: post only",
                                   "/api/v1/futures/trade/place_order")
        self.orders.append(rec)
        oid = str(next(self.order_seq))
        rec["orderId"] = oid
        self._rest(rec)
        return {"orderId": oid}

    def _rest(self, rec):
        oid = rec.get("orderId") or str(next(self.order_seq))
        rec["orderId"] = oid
        self.resting[rec["clientId"] or oid] = {
            "orderId": oid, "clientId": rec["clientId"], "symbol": rec["symbol"],
            "side": rec["side"], "price": rec["price"], "qty": rec["qty"],
            "status": "NEW", "tradeQty": "0", "avgPrice": None,
            "tp": rec["tp"], "sl": rec["sl"]}

    def step_book(self):
        """Fill whatever the current price reaches. Call after set_price."""
        for o in self.resting.values():
            if o["status"] in ("FILLED", "CANCELED", "REJECTED"):
                continue
            mkt = self.prices.get(o["symbol"])
            if mkt is None:
                continue
            px, want = float(o["price"]), float(o["qty"])
            reached = (mkt <= px) if o["side"].upper() == "BUY" else (mkt >= px)
            if not reached:
                continue
            done = float(o["tradeQty"] or 0)
            take = want - done
            if self.partial_fill_frac is not None and done == 0:
                take = want * self.partial_fill_frac
            done = min(want, done + take)
            o["tradeQty"] = str(done)
            # A limit fills at its own price or better, never worse.
            o["avgPrice"] = str(px)
            o["status"] = "FILLED" if done >= want - 1e-12 else "PART_FILLED"
            if o["status"] == "FILLED" or done > 0:
                self.open_positions = [
                    p for p in self.open_positions
                    if (p["symbol"], p["side"]) != (o["symbol"], o["side"].upper())]
                self.open_positions.append({
                    "symbol": o["symbol"], "side": o["side"].upper(),
                    "qty": str(done), "positionId": f"p{o['orderId']}"})

    def pending_orders(self, symbol=None):
        self._maybe_fail("pending_orders")
        return [dict(o) for o in self.resting.values()
                if o["status"] in ("NEW", "PART_FILLED")
                and (symbol is None or o["symbol"] == symbol)]

    def order_detail(self, order_id=None, client_id=None):
        self._maybe_fail("order_detail")
        for k, o in self.resting.items():
            if (client_id and o["clientId"] == client_id) or \
                    (order_id and o["orderId"] == str(order_id)):
                return dict(o)
        return {}

    def order_state(self, order_id=None, client_id=None):
        from exchange.bitunix import BitunixClient
        try:
            row = self.order_detail(order_id=order_id, client_id=client_id)
        except Exception:
            row = {}
        return BitunixClient.read_order(row)

    def read_order(self, row):
        from exchange.bitunix import BitunixClient
        return BitunixClient.read_order(row)

    def cancel_orders(self, symbol, order_ids=None, client_ids=None):
        self._maybe_fail("cancel_orders")
        self.cancelled.append((symbol, tuple(order_ids or ()),
                               tuple(client_ids or ())))
        for cid in (client_ids or []):
            o = self.resting.get(cid)
            if o and o["status"] in ("NEW", "PART_FILLED"):
                o["status"] = "CANCELED"
        for oid in (order_ids or []):
            for o in self.resting.values():
                if o["orderId"] == str(oid) and o["status"] in ("NEW",
                                                                "PART_FILLED"):
                    o["status"] = "CANCELED"
        return {}


class FakeCDP:
    """One TradingView chart window, backed by a candle dict the test owns.

    Serves exactly what `papertrade.read_window` reads: the study list, the
    study's published series rows, and the candles behind `BARS_JS`. Study
    plots default to the council's packed vote only, so the shape path is
    exercised without the combo path firing on its own.
    """

    # Filled in by whichever test installed the class.
    windows: dict[int, "FakeCDP"] = {}
    n_windows = 1

    def __init__(self, host=None, port=None, timeout=30, target_index=None):
        self.idx = 0 if target_index is None else target_index
        me = FakeCDP.windows.get(self.idx)
        if me is None:
            raise RuntimeError(f"no fake chart window {self.idx}")
        self.__dict__.update({k: v for k, v in me.__dict__.items()
                              if k != "idx"})
        self._src = me
        self.closed = False

    # -- what a test sets up -------------------------------------------------
    @classmethod
    def install(cls, windows: dict[int, dict]):
        """windows: {index: {"symbol":..., "res":..., "ohlc":..., "votes":...}}"""
        cls.windows = {}
        for i, spec in windows.items():
            w = cls.__new__(cls)
            w.idx = i
            w.symbol = spec.get("symbol", "BITUNIX:TESTUSDT.P")
            w.res = str(spec.get("res", "15"))
            # Held by reference on purpose: a scenario prints a new candle by
            # mutating the dict it passed in, exactly as the chart gains one.
            w.ohlc = spec.setdefault("ohlc", {})
            w.votes = spec.get("votes", 0)
            # Extra published series, as {plot_name: value} or
            # {plot_name: callable(bar_time)} -- STATE and PLAN_ENTRY for the
            # council, TBT_*_SPAN for the old convergence stream.
            w.plots = dict(spec.get("plots") or {})
            w.study_name = spec.get("study_name", "TBT Sniper")
            w.study_id = spec.get("study_id", f"st{i}")
            w.raise_on = spec.get("raise_on")
            w.no_study = spec.get("no_study", False)
            w.evaluate_error = spec.get("evaluate_error")
            cls.windows[i] = w
        cls.n_windows = len(cls.windows)

    @classmethod
    def chart_windows(cls, host=None, port=None):
        return cls.n_windows

    # -- what the engine calls ----------------------------------------------
    def studies(self):
        if self._src.raise_on == "studies":
            raise RuntimeError("CDP dropped")
        if self._src.no_study:
            return []
        return [{"id": self._src.study_id, "name": self._src.study_name}]

    def raw_series(self, study_id, limit=None):
        if self._src.raise_on == "raw_series":
            raise RuntimeError("CDP dropped")
        names = ["VOTES_PACKED", *self._src.plots]
        ks = sorted(self._src.ohlc)
        rows = []
        for t in ks:
            row = [t, self._src.votes]
            for nm in self._src.plots:
                v = self._src.plots[nm]
                row.append(v(t) if callable(v) else v)
            rows.append(row)
        if limit:
            rows = rows[-int(limit):]
        return {"symbol": self._src.symbol, "res": self._src.res,
                "plots": names, "rows": rows}

    def evaluate(self, expression, retry=True):
        if self._src.evaluate_error:
            raise self._src.evaluate_error
        ks = sorted(self._src.ohlc)
        return json.dumps([[t, *self._src.ohlc[t]] for t in ks])

    def close(self):
        self.closed = True


def pack_votes(**members) -> int:
    """The inverse of `papertrade.unpack_votes` -- build a packed council vote.

    Kept here rather than imported so a change to the packing on either side
    shows up as a failing test instead of two functions agreeing on the wrong
    thing.
    """
    order = ("Bank", "Team45", "Tesla", "Sniper", "HTF", "MA")
    n = 0
    for i, nm in enumerate(order):
        n += (members.get(nm, 0) + 1) * (3 ** i)
    return n
