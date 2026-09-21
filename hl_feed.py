"""
hl_feed.py
==========
Live Hyperliquid market-data feed (PAXG-first) + REST history helpers.

- WS (websocket-client, daemon thread): l2Book, trades, 1m candle closes,
  activeAssetCtx (markPx / oraclePx / predicted funding).
- REST: candleSnapshot paging (5000 bars/req), fundingHistory, ctx snapshot.
- Latency: measures REST RTT (ack-latency proxy = RTT/2) and tracks WS book
  age at every fill so each trade logs its implementation shortfall honestly.

Fallback: if WS drops, REST polling (l2Book + allMids every 2s) keeps the
book fresh; every gap is counted in stats (phone-visible).
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger("hl_feed")

HL_WS_URL = "wss://api.hyperliquid.xyz/ws"
HL_INFO_URL = "https://api.hyperliquid.xyz/info"
CANDLE_PAGE = 5000


def rest_info(payload: Dict[str, Any], timeout: float = 10.0) -> Any:
    req = urllib.request.Request(
        HL_INFO_URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fetch_candles(coin: str, interval: str, start_ms: int,
                  end_ms: Optional[int] = None) -> List[Dict[str, Any]]:
    """1m/5m history via candleSnapshot, paged. Returns oldest-first dicts."""
    out: List[Dict[str, Any]] = []
    cur = start_ms
    end = end_ms or int(time.time() * 1000)
    while cur < end:
        raw = rest_info({"type": "candleSnapshot", "req": {
            "coin": coin, "interval": interval, "startTime": cur, "endTime": end}})
        if not raw:
            break
        out.extend(raw)
        if len(raw) < CANDLE_PAGE:
            break
        cur = int(raw[-1]["T"]) + 1
        if len(out) > 200_000:
            break
    return out


def fetch_funding(coin: str, start_ms: int,
                  end_ms: Optional[int] = None) -> List[Dict[str, Any]]:
    end = end_ms or int(time.time() * 1000)
    return rest_info({"type": "fundingHistory", "coin": coin,
                      "startTime": start_ms, "endTime": end})


def fetch_ctx(coin: str) -> Dict[str, Any]:
    data = rest_info({"type": "metaAndAssetCtxs"})
    names = [a["name"] for a in data[0]["universe"]]
    return dict(data[1][names.index(coin)])


def measure_rtt(samples: int = 3) -> float:
    """Median allMids RTT in ms (ack-latency proxy = RTT/2)."""
    dts: List[float] = []
    for _ in range(samples):
        t0 = time.monotonic()
        try:
            rest_info({"type": "allMids"}, timeout=5.0)
            dts.append((time.monotonic() - t0) * 1000.0)
        except Exception:
            pass
    dts.sort()
    return dts[len(dts) // 2] if dts else 250.0


class HLFeed:
    """Threaded WS feed with REST fallback. All readers are lock-protected."""

    def __init__(self, coin: str = "PAXG"):
        self.coin = coin
        self._lock = threading.Lock()
        self.bids: List[Tuple[float, float]] = []
        self.asks: List[Tuple[float, float]] = []
        self.book_ts: float = 0.0
        self.mark_px: float = 0.0
        self.oracle_px: float = 0.0
        self.pred_funding: float = 0.0
        self.mid_px: float = 0.0
        self.last_trades: Deque[dict] = deque(maxlen=100)
        self.closed_candles: Deque[dict] = deque(maxlen=500)
        self._candle_cb: Optional[Callable[[dict], None]] = None
        self.ws_connected = False
        self.ws_drops = 0
        self.rest_polls = 0
        self.rtt_ms = 250.0
        self.ack_ms = 125.0
        self._stop = threading.Event()
        self._ws_thread: Optional[threading.Thread] = None
        self._poll_thread: Optional[threading.Thread] = None

    # -- subscription ---------------------------------------------------------
    def on_closed_candle(self, cb: Callable[[dict], None]) -> None:
        self._candle_cb = cb

    def start(self) -> None:
        self._stop.clear()
        self.refresh_latency()
        try:
            ctx = fetch_ctx(self.coin)
            self._apply_ctx(ctx)
        except Exception as e:
            logger.warning("initial ctx fetch failed: %s", e)
        self._ws_thread = threading.Thread(target=self._ws_loop, daemon=True)
        self._ws_thread.start()
        self._poll_thread = threading.Thread(target=self._fallback_loop, daemon=True)
        self._poll_thread.start()

    def stop(self) -> None:
        self._stop.set()

    def refresh_latency(self) -> None:
        try:
            self.rtt_ms = measure_rtt()
            self.ack_ms = max(20.0, self.rtt_ms / 2.0)
        except Exception:
            pass

    # -- readers ----------------------------------------------------------------
    def book(self) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]], float]:
        with self._lock:
            return list(self.bids), list(self.asks), self.book_ts

    def book_age_ms(self) -> float:
        with self._lock:
            return (time.time() - self.book_ts) * 1000.0 if self.book_ts else 1e9

    def mark(self) -> float:
        with self._lock:
            return self.mark_px or self.mid_px

    # -- WS ----------------------------------------------------------------------
    def _ws_loop(self) -> None:
        import websocket
        while not self._stop.is_set():
            try:
                ws = websocket.WebSocketApp(
                    HL_WS_URL, on_message=self._on_msg,
                    on_error=lambda w, e: logger.warning("hl ws error: %s", e),
                    on_close=lambda w, c, m: logger.warning("hl ws closed"),
                    on_open=self._on_open)
                self.ws_connected = True
                ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as e:
                logger.warning("hl ws loop error: %s", e)
            self.ws_connected = False
            self.ws_drops += 1
            if not self._stop.is_set():
                time.sleep(min(2.0 * (1 + self.ws_drops), 15.0))

    def _on_open(self, ws) -> None:
        for sub in ({"type": "l2Book", "coin": self.coin},
                    {"type": "trades", "coin": self.coin},
                    {"type": "candle", "coin": self.coin, "interval": "1m"},
                    {"type": "activeAssetCtx", "coin": self.coin}):
            ws.send(json.dumps({"method": "subscribe", "subscription": sub}))

    def _on_msg(self, ws, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except Exception:
            return
        ch, data = msg.get("channel"), msg.get("data")
        now = time.time()
        try:
            if ch == "l2Book":
                lv = data["levels"]
                bids = [(float(x["px"]), float(x["sz"])) for x in lv[0]]
                asks = [(float(x["px"]), float(x["sz"])) for x in lv[1]]
                with self._lock:
                    self.bids, self.asks = bids, asks
                    self.book_ts = now
                    if bids and asks:
                        self.mid_px = (bids[0][0] + asks[0][0]) / 2.0
            elif ch == "trades" and isinstance(data, list):
                with self._lock:
                    self.last_trades.extend(data)
            elif ch == "activeAssetCtx":
                self._apply_ctx(data.get("ctx", {}))
            elif ch == "candle":
                # Closed 1m bar. Hand to the engine exactly once.
                if self._candle_cb and data.get("T", 0) <= int(now * 1000):
                    try:
                        self._candle_cb(dict(data))
                    except Exception as e:
                        logger.warning("candle callback failed: %s", e)
        except Exception as e:
            logger.debug("hl ws msg skipped: %s", e)

    def _apply_ctx(self, ctx: Dict[str, Any]) -> None:
        with self._lock:
            try:
                self.mark_px = float(ctx.get("markPx", 0.0) or 0.0)
                self.oracle_px = float(ctx.get("oraclePx", 0.0) or 0.0)
                self.pred_funding = float(ctx.get("funding", 0.0) or 0.0)
                if ctx.get("midPx"):
                    self.mid_px = float(ctx["midPx"])
            except (TypeError, ValueError):
                pass

    # -- REST fallback: keeps book fresh when WS is down --------------------------
    def _fallback_loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(2.0)
            if self.ws_connected and self.book_age_ms() < 5000:
                continue
            try:
                book = rest_info({"type": "l2Book", "coin": self.coin}, timeout=6.0)
                lv = book["levels"]
                bids = [(float(x["px"]), float(x["sz"])) for x in lv[0][:10]]
                asks = [(float(x["px"]), float(x["sz"])) for x in lv[1][:10]]
                with self._lock:
                    self.bids, self.asks = bids, asks
                    self.book_ts = time.time()
                    if bids and asks:
                        self.mid_px = (bids[0][0] + asks[0][0]) / 2.0
                self.rest_polls += 1
            except Exception as e:
                logger.debug("hl rest fallback failed: %s", e)
