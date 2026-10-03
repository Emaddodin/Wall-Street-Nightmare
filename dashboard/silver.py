"""Where Gold Desk's silver (XAGUSD) and dollar index candles come from, for the SMT reading (smt.py).

In order:
  1. the broker source, when it has `rates_of(symbol, tf, count) -> Bars` (MT5, the MT5 bridge, LiteFinance):
     same server clock as gold's candles, and live. The symbol is found once by trying the usual names (the
     gold symbol's own suffix first: XAUUSDm -> XAGUSDm) and remembered;
  2. else Yahoo's free 1-minute chart (SI=F silver futures, DX-Y.NYB dollar index), the same feed nodes.py
     reads, moved to the gold candle clock with the caller's function and built up into M5 / M15 / H1. It
     is often 10+ minutes late, and says so.

`rates(tf, count)` never raises: Bars or None, and `status` says what is used or why nothing is.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request

from engine import Bars

TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400}
UA = {"User-Agent": "Mozilla/5.0 GoldDesk"}
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{}?interval=1m&range={}"
SILVER_SYMBOLS = ("XAGUSD", "XAGUSDm", "SILVER", "XAGUSD.", "XAGUSD.a", "XAGUSD.r", "XAGUSD_i", "XAGUSD.pro",
                  "XAGUSD+", "XAGUSD#")
DOLLAR_SYMBOLS = ("DXY", "USDX", "DX", "USDIDX", "DXY.cash", "DOLLARIDX", "DXYm", "USDINDEX")
CACHE_SEC = 5.0          # don't ask the source again for the same timeframe within this
DELAYED = 300            # Yahoo's newest 1-minute bar older than this (seconds): "delayed"
RETRY_GONE = 900.0       # broker has none of the symbols / can't serve them: look again after this
RETRY_SOON = 30.0        # broker failed for a maybe-passing reason (history loading, busy): 30 s, doubling
YAHOO_SHORT = 20.0       # re-read today's 1-minute bars at most this often
YAHOO_LONG = 300.0       # re-read the last 7 days at most this often
YAHOO_RETRY = 60.0       # after a failed Yahoo read
KEEP_DAYS = 8


def _get_json(url: str):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=12).read())


def _short(e: BaseException) -> str:
    return (str(e) or type(e).__name__).strip().splitlines()[0][:120]


class Yahoo:
    """One Yahoo chart symbol's 1-minute bars (UTC open times), merged across reads. Reads run in a thread of
    their own unless block=True, so a slow Yahoo never holds up the dashboard's poll."""

    def __init__(self, symbol: str, block: bool = False):
        self.symbol, self.block = symbol, block
        self.bars: dict = {}                  # utc open time -> [o, h, l, c, v]
        self.short_at = self.long_at = self.retry_at = 0.0
        self.error: str | None = None
        self._lock = threading.Lock()
        self._busy = False

    def refresh(self, need_sec: int) -> None:
        now = time.time()
        if self._busy or now < self.retry_at:
            return
        with self._lock:
            oldest = min(self.bars) if self.bars else None
        long = not self.long_at or (now - self.long_at >= YAHOO_LONG and
                                    (oldest is None or oldest > now - need_sec or now - self.long_at >= 3600))
        if not long and now - self.short_at < YAHOO_SHORT:
            return
        self._busy = True
        if self.block:
            self._read(long)
        else:
            threading.Thread(target=self._read, args=(long,), daemon=True).start()

    def _read(self, long: bool) -> None:
        now = time.time()
        try:
            r = _get_json(YAHOO.format(self.symbol, "7d" if long else "1d"))["chart"]["result"][0]
            q = r["indicators"]["quote"][0]
            cols = [q.get(k) or [] for k in ("open", "high", "low", "close", "volume")]
            new = {}
            for i, t in enumerate(r.get("timestamp") or []):
                row = [col[i] if i < len(col) else None for col in cols]
                if None in row[:4]:
                    continue
                new[int(t)] = [float(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4] or 0)]
            if not new:
                raise ValueError("no bars in Yahoo's answer")
            cut = now - KEEP_DAYS * 86400
            with self._lock:
                self.bars.update(new)
                for t in [t for t in self.bars if t < cut]:
                    del self.bars[t]
            self.short_at = now
            if long:
                self.long_at = now
            self.error = None
        except Exception as e:                  # unreachable, blocked, changed format: say so, try later
            self.error = _short(e)
            self.retry_at = now + YAHOO_RETRY
            if long:
                self.long_at = now - YAHOO_LONG + YAHOO_RETRY   # don't ask for 7 days again at once
        finally:
            self._busy = False

    def age(self) -> int | None:
        """Seconds since the newest bar closed."""
        with self._lock:
            newest = max(self.bars) if self.bars else None
        return None if newest is None else int(time.time() - newest - 60)

    def rates(self, tf: str, count: int, clock) -> Bars | None:
        sec = TF_SECONDS[tf]
        rows: dict = {}
        with self._lock:
            items = sorted(self.bars.items())
        for t, (o, h, l, c, v) in items:
            tc = int(clock(t))
            k = tc - tc % sec
            r = rows.get(k)
            if r is None:
                rows[k] = [o, h, l, c, v]
            else:
                r[1], r[2], r[3], r[4] = max(r[1], h), min(r[2], l), c, r[4] + v
        if not rows:
            return None
        b = Bars(sec)
        for k in sorted(rows)[-int(count):]:
            r = rows[k]
            b.append(k, r[0], r[1], r[2], r[3], r[4], 0.0)
        return b


class CrossFeed:
    """Candles of one other market on the gold candle clock: the broker's own symbol when it has one, else
    Yahoo's delayed 1-minute feed. rates(tf, count) -> Bars | None, never raises; `status` says which."""

    label = "market"
    hint = ""                                   # source attribute naming its own symbols for this market

    def __init__(self, src, to_candle_clock=None, symbols: tuple = (), yahoo: str | None = None,
                 derive: tuple | None = None, cache_sec: float = CACHE_SEC, block: bool = False):
        self.src = src
        self.clock = to_candle_clock or (lambda t: t)
        own = getattr(src, self.hint, None) if self.hint else None
        self.cands = list(own) if isinstance(own, (tuple, list)) else self._candidates(src, tuple(symbols), derive)
        self.yahoo = Yahoo(yahoo, block) if yahoo else None
        self.cache_sec = cache_sec
        self.symbol: str | None = None          # the broker symbol that works, once found
        self.source: str | None = None          # "broker" | "yahoo" | None (last answer)
        self.status = "starting"
        self.broker_error: str | None = None
        self._broker_retry = 0.0
        self._misses = 0                        # symbol searches in a row that found nothing
        self._cache: dict = {}                  # tf -> (when, count, Bars | None, source)
        self._lock = threading.Lock()

    @staticmethod
    def _candidates(src, symbols: tuple, derive: tuple | None) -> list:
        out = []
        gold = str(getattr(src, "symbol", "") or "")
        if derive and gold:                     # the broker's naming: XAUUSDm -> XAGUSDm, GOLD.x -> SILVER.x
            for a, b in derive:
                if gold.upper().startswith(a) and b + gold[len(a):] not in out:
                    out.append(b + gold[len(a):])
        for s in symbols:
            if s not in out:
                out.append(s)
        return out

    # ------------------------------------------------------------ broker
    def _broker(self, tf: str, count: int) -> Bars | None:
        fn = getattr(self.src, "rates_of", None)
        if not callable(fn) or not self.cands:
            self.broker_error = "the broker source has no other symbols" if not callable(fn) else \
                f"the broker has no {self.label} candles"
            return None
        if time.time() < self._broker_retry:
            return None
        if self.symbol:
            try:
                b = fn(self.symbol, tf, count)
                if b is not None and len(b):
                    self.broker_error = None
                    return b
                self.broker_error = f"{self.symbol}: no {tf} candles"
            except NotImplementedError as e:    # e.g. an older MT5 bridge EA: no other symbols at all
                self.broker_error, self._broker_retry = _short(e), time.time() + RETRY_GONE
            except LookupError as e:            # the symbol went away: look for it again
                self.broker_error, self.symbol = _short(e), None
            except Exception as e:
                self.broker_error, self._broker_retry = f"{self.symbol}: {_short(e)}", time.time() + 5.0
            return None
        passing, tried = False, []
        for s in self.cands:                    # find the symbol once
            try:
                b = fn(s, tf, count)
            except NotImplementedError as e:
                self.broker_error, self._broker_retry = _short(e), time.time() + RETRY_GONE
                return None
            except LookupError:
                tried.append(s)
                continue
            except Exception as e:              # exists maybe, but not now (history loading, refused, busy)
                passing = True
                tried.append(f"{s} ({_short(e)})")
                continue
            if b is not None and len(b):
                self.symbol, self.broker_error, self._misses = s, None, 0
                return b
            tried.append(s)
        self.broker_error = "broker has none of " + ", ".join(tried) if tried else "no symbols to try"
        # a source that says "missing" with LookupError is believed at once; any other failure may be a
        # history still loading, so look again soon, then less and less often
        wait = min(RETRY_GONE, RETRY_SOON * 2 ** self._misses) if passing else RETRY_GONE
        self._misses += 1
        self._broker_retry = time.time() + wait
        return None

    # ------------------------------------------------------------ public
    def rates(self, tf: str, count: int) -> Bars | None:
        try:
            count = int(count)
            with self._lock:
                hit = self._cache.get(tf)
                now = time.time()
                if hit and now - hit[0] < self.cache_sec and (hit[2] is None or hit[1] >= count):
                    b = hit[2]
                    return b if b is None or len(b) <= count else b.slice_from(len(b) - count)
                b, src = self._broker(tf, count), "broker"
                if b is None:
                    b, src = self._from_yahoo(tf, count), "yahoo"
                self.source = src if b is not None else None
                self._status(src if b is not None else None)
                self._cache[tf] = (now, count, b, self.source)
                return b
        except Exception as e:                  # never let the reading break the page
            self.status = f"unavailable: {_short(e)}"
            return None

    def _from_yahoo(self, tf: str, count: int) -> Bars | None:
        if not self.yahoo:
            return None
        self.yahoo.refresh(TF_SECONDS[tf] * count)
        return self.yahoo.rates(tf, count, self.clock)

    def _status(self, src: str | None) -> None:
        if src == "broker":
            self.status = f"broker {self.symbol}"
            return
        if src == "yahoo":
            age = self.yahoo.age()
            late = age is not None and age > DELAYED
            self.status = f"Yahoo {self.yahoo.symbol}" + (f" (delayed {age // 60} min)" if late else "") + \
                (f"; broker: {self.broker_error}" if self.broker_error and late else "")
            return
        why = [w for w in (self.broker_error and f"broker: {self.broker_error}",
                           self.yahoo and f"Yahoo {self.yahoo.symbol}: " + (self.yahoo.error or (
                               "reading..." if self.yahoo._busy else "no bars"))) if w]
        self.status = "unavailable: " + ("; ".join(why) or "no source")

    @property
    def delayed(self) -> bool:
        if self.source != "yahoo" or not self.yahoo:
            return False
        age = self.yahoo.age()
        return age is not None and age > DELAYED

    def info(self) -> dict:
        """For the page: {"label", "source": "broker"|"yahoo"|None, "symbol", "delayed", "delay_min", "status"}."""
        age = self.yahoo.age() if self.yahoo and self.source == "yahoo" else None
        return {"label": self.label, "source": self.source,
                "symbol": self.symbol if self.source == "broker" else (self.yahoo.symbol if self.source else None),
                "delayed": self.delayed, "delay_min": age // 60 if age is not None else None, "status": self.status}


class SilverFeed(CrossFeed):
    """XAGUSD on the gold candle clock: broker symbol first, Yahoo SI=F (silver futures) as the fallback."""
    label = "silver"
    hint = "silver_symbols"

    def __init__(self, src, to_candle_clock=None, symbols: tuple = SILVER_SYMBOLS, yahoo: str | None = "SI=F",
                 cache_sec: float = CACHE_SEC, block: bool = False):
        super().__init__(src, to_candle_clock, symbols, yahoo,
                         derive=(("XAUUSD", "XAGUSD"), ("XAU", "XAG"), ("GOLD", "SILVER")), cache_sec=cache_sec,
                         block=block)


class DollarFeed(CrossFeed):
    """The dollar index on the gold candle clock (for the inverse SMT): a broker DXY symbol if there is one,
    else Yahoo DX-Y.NYB."""
    label = "dxy"
    hint = "dollar_symbols"

    def __init__(self, src, to_candle_clock=None, symbols: tuple = DOLLAR_SYMBOLS, yahoo: str | None = "DX-Y.NYB",
                 cache_sec: float = CACHE_SEC, block: bool = False):
        super().__init__(src, to_candle_clock, symbols, yahoo, derive=None, cache_sec=cache_sec, block=block)
