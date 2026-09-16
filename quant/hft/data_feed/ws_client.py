"""
quant/hft/data_feed/ws_client.py
==================================
Async WebSocket client for Hyperliquid L2 & trade streams.

Reconnects with exponential back-off (cap 60 s).
Maintains per-symbol OrderBook instances and dispatches callbacks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable, Awaitable

import websockets
from websockets.exceptions import ConnectionClosed

from .orderbook import OrderBook

logger = logging.getLogger(__name__)

WS_URL = "wss://api.hyperliquid.xyz/ws"
_MAX_BACKOFF = 60.0
_PING_INTERVAL = 20.0
_PING_TIMEOUT = 10.0


class HyperliquidFeed:
    """
    Maintains live L2 order book and trade stream for a list of symbols.

    Callbacks
    ---------
    on_book_update(symbol: str, book: OrderBook) -> Awaitable[None]
    on_trade(symbol: str, trade: dict) -> Awaitable[None]
    """

    def __init__(
        self,
        symbols: list[str],
        on_book_update: Callable[[str, OrderBook], Awaitable[None]] | None = None,
        on_trade: Callable[[str, dict], Awaitable[None]] | None = None,
    ) -> None:
        self.symbols = [s.upper() for s in symbols]
        self._on_book_update = on_book_update
        self._on_trade = on_trade
        self.books: dict[str, OrderBook] = {s: OrderBook(s) for s in self.symbols}
        self._running = False
        self._latency_ms: float = 0.0
        self._reconnect_delay: float = 1.0

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._running = True
        while self._running:
            try:
                await self._connect_and_run()
                self._reconnect_delay = 1.0  # reset on clean exit
            except asyncio.CancelledError:
                logger.info("Feed cancelled — shutting down.")
                break
            except Exception as exc:
                logger.warning(
                    "WebSocket error: %s — reconnecting in %.1f s", exc, self._reconnect_delay
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, _MAX_BACKOFF)

    async def stop(self) -> None:
        self._running = False

    @property
    def latency_ms(self) -> float:
        return self._latency_ms

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _connect_and_run(self) -> None:
        logger.info("Connecting to %s ...", WS_URL)
        async with websockets.connect(
            WS_URL,
            ping_interval=_PING_INTERVAL,
            ping_timeout=_PING_TIMEOUT,
            max_size=2**23,  # 8 MB
        ) as ws:
            logger.info("Connected. Subscribing to %d symbols.", len(self.symbols))
            await self._subscribe(ws)
            async for raw in ws:
                if not self._running:
                    break
                await self._handle_message(raw)

    async def _subscribe(self, ws) -> None:
        for symbol in self.symbols:
            # L2 order book
            await ws.send(
                json.dumps({"method": "subscribe", "subscription": {"type": "l2Book", "coin": symbol}})
            )
            # Trade stream
            await ws.send(
                json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": symbol}})
            )

    async def _handle_message(self, raw: str) -> None:
        try:
            msg: dict = json.loads(raw)
        except json.JSONDecodeError:
            return

        channel = msg.get("channel", "")
        data = msg.get("data", {})

        if channel == "l2Book":
            await self._handle_book(data)
        elif channel == "trades":
            await self._handle_trades(data)
        elif channel == "pong":
            # Latency probe
            sent_ts = msg.get("data", {}).get("ts")
            if sent_ts:
                self._latency_ms = (time.time() * 1000 - float(sent_ts))

    async def _handle_book(self, data: dict) -> None:
        coin = data.get("coin", "").upper()
        if coin not in self.books:
            return
        book = self.books[coin]

        # Hyperliquid WebSocket ALWAYS sends full L2 snapshots — not incremental deltas.
        # apply_snapshot clears the book and repopulates from scratch on every message.
        await book.apply_snapshot(data)

        if self._on_book_update:
            await self._on_book_update(coin, book)

    async def _handle_trades(self, data) -> None:
        # data is a list of trade dicts
        if not isinstance(data, list):
            data = [data]
        for trade in data:
            coin = trade.get("coin", "").upper()
            if coin not in self.books:
                continue
            if self._on_trade:
                await self._on_trade(coin, trade)
