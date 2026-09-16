"""
quant/hft/data_feed/orderbook.py
=================================
Local limit-order-book (LOB) reconstruction from Hyperliquid L2 WebSocket feed.

Key formulas
------------
OFI at level k, window [T-h, T]:
    delta_B_k = sum of bid-qty changes at level k
    delta_A_k = sum of ask-qty changes at level k
    OFI_k(T,h) = (delta_B_k - delta_A_k) / (|delta_B_k| + |delta_A_k| + eps)

Micro-price:
    p_micro = bid_px * ask_qty/(bid_qty+ask_qty) + ask_px * bid_qty/(bid_qty+ask_qty)
"""

from __future__ import annotations

import asyncio
import time
from typing import NamedTuple

import numpy as np
from sortedcontainers import SortedDict

_EPS = 1e-12


class Level(NamedTuple):
    price: float
    qty: float


class OFISnapshot:
    """Immutable snapshot of order-book state used for OFI delta computation."""
    __slots__ = ("bids", "asks", "timestamp")

    def __init__(self, bids: dict[float, float], asks: dict[float, float], timestamp: float) -> None:
        self.bids = dict(bids)
        self.asks = dict(asks)
        self.timestamp = timestamp


class OrderBook:
    """
    Thread-safe asyncio-compatible Level-2 order book.
    Bids stored descending (best bid first).
    Asks stored ascending (best ask first).
    """

    def __init__(self, symbol: str, max_depth: int = 50) -> None:
        self.symbol = symbol
        self.max_depth = max_depth
        self._bids: SortedDict = SortedDict(lambda x: -x)
        self._asks: SortedDict = SortedDict()
        self._lock = asyncio.Lock()
        self._last_update_ts: float = 0.0
        self._seq: int = 0

    async def apply_snapshot(self, data: dict) -> None:
        async with self._lock:
            self._bids.clear()
            self._asks.clear()
            levels = data.get("levels", [[], []])
            # Hyperliquid WebSocket L2: levels[0] = BIDS (descending from best bid)
            #                            levels[1] = ASKS (ascending from best ask)
            for entry in levels[0]:
                px, sz = float(entry["px"]), float(entry["sz"])
                if sz > 0:
                    self._bids[px] = sz
            for entry in levels[1]:
                px, sz = float(entry["px"]), float(entry["sz"])
                if sz > 0:
                    self._asks[px] = sz
            self._last_update_ts = float(data.get("time", time.time() * 1000)) / 1000.0
            self._seq += 1

    async def apply_delta(self, data: dict) -> None:
        async with self._lock:
            levels = data.get("levels", [[], []])
            # Hyperliquid WebSocket L2: levels[0] = BIDS, levels[1] = ASKS
            for entry in levels[0]:
                px, sz = float(entry["px"]), float(entry["sz"])
                if sz == 0.0:
                    self._bids.pop(px, None)
                else:
                    self._bids[px] = sz
            for entry in levels[1]:
                px, sz = float(entry["px"]), float(entry["sz"])
                if sz == 0.0:
                    self._asks.pop(px, None)
                else:
                    self._asks[px] = sz
            self._last_update_ts = float(data.get("time", time.time() * 1000)) / 1000.0
            self._seq += 1

    @property
    def best_bid(self) -> float:
        if not self._bids:
            return float("nan")
        return self._bids.keys()[0]

    @property
    def best_ask(self) -> float:
        if not self._asks:
            return float("nan")
        return self._asks.keys()[0]

    @property
    def mid_price(self) -> float:
        bb, ba = self.best_bid, self.best_ask
        if np.isnan(bb) or np.isnan(ba):
            return float("nan")
        return (bb + ba) / 2.0

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid

    @property
    def spread_bps(self) -> float:
        mid = self.mid_price
        if mid == 0 or np.isnan(mid):
            return float("nan")
        return self.spread / mid * 10_000.0

    @property
    def last_update_ts(self) -> float:
        return self._last_update_ts

    @property
    def seq(self) -> int:
        return self._seq

    def get_levels(self, n: int = 5) -> tuple[list[Level], list[Level]]:
        bid_keys = self._bids.keys()[:n]
        ask_keys = self._asks.keys()[:n]
        bids = [Level(px, self._bids[px]) for px in bid_keys]
        asks = [Level(px, self._asks[px]) for px in ask_keys]
        return bids, asks

    def to_snapshot(self) -> OFISnapshot:
        return OFISnapshot(
            bids=dict(self._bids),
            asks=dict(self._asks),
            timestamp=self._last_update_ts,
        )

    @staticmethod
    def compute_ofi(prev: OFISnapshot, curr: OFISnapshot, levels: int = 5) -> np.ndarray:
        """
        Per-level OFI vector of shape (levels,).
        OFI_k = (dB_k - dA_k) / (|dB_k| + |dA_k| + eps)
        """
        prev_bids = sorted(prev.bids.items(), key=lambda x: -x[0])[:levels]
        prev_asks = sorted(prev.asks.items())[:levels]
        curr_bids = sorted(curr.bids.items(), key=lambda x: -x[0])[:levels]
        curr_asks = sorted(curr.asks.items())[:levels]

        def _qty(items, idx):
            return items[idx][1] if idx < len(items) else 0.0

        ofi = np.zeros(levels, dtype=np.float64)
        for k in range(levels):
            d_bid = _qty(curr_bids, k) - _qty(prev_bids, k)
            d_ask = _qty(curr_asks, k) - _qty(prev_asks, k)
            denom = abs(d_bid) + abs(d_ask) + _EPS
            ofi[k] = np.clip((d_bid - d_ask) / denom, -1.0, 1.0)
        return ofi

    @staticmethod
    def integrated_ofi(ofi_vec: np.ndarray) -> float:
        return float(np.mean(ofi_vec))

    def get_vwap(self, side: str, depth_usdt: float) -> float:
        book = self._bids if side == "bid" else self._asks
        remaining = depth_usdt
        total_qty = total_cost = 0.0
        for px in book.keys():
            qty = book[px]
            notional = px * qty
            if notional >= remaining:
                partial = remaining / px
                total_cost += remaining
                total_qty += partial
                break
            total_cost += notional
            total_qty += qty
            remaining -= notional
        if total_qty < _EPS:
            return self.best_bid if side == "bid" else self.best_ask
        return total_cost / total_qty

    def micro_price(self) -> float:
        bb, ba = self.best_bid, self.best_ask
        if np.isnan(bb) or np.isnan(ba):
            return float("nan")
        bq = self._bids.get(bb, 0.0)
        aq = self._asks.get(ba, 0.0)
        total = bq + aq + _EPS
        return bb * (aq / total) + ba * (bq / total)

    def __repr__(self) -> str:
        return (
            f"<OrderBook {self.symbol} "
            f"bid={self.best_bid:.4f} ask={self.best_ask:.4f} "
            f"spread_bps={self.spread_bps:.2f}>"
        )
