"""
quant/hft/execution/order_splitter.py
=======================================
Square-Root Law order splitting for Taker market orders.

Square-Root Law (market impact):
    I(Q) ≈ Y * sigma * sqrt(Q / V)

where:
    Q     = order size (base units)
    V     = daily volume in base units
    sigma = daily price volatility (fraction)
    Y     = empirical constant (≈ 0.5 for crypto, Almgren et al.)

Splitting strategy:
    If I(Q) > profit_threshold_pct → split into N TWAP/VWAP chunks
    N = ceil(Q / max_child_size)
    Minimum child size enforced to avoid excessive API calls.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterator


_Y_CONST = 0.5   # empirical impact constant for crypto


@dataclass
class OrderChunk:
    size: float          # base-asset quantity for this chunk
    chunk_index: int
    total_chunks: int
    delay_sec: float     # seconds to wait before sending (TWAP spacing)


class SquareRootSplitter:
    """
    Splits a parent Taker order into child chunks when estimated
    market impact exceeds a profitability threshold.

    Parameters
    ----------
    daily_volume   : 24h traded volume in base asset (e.g. BTC)
    sigma_daily    : daily volatility (fraction, e.g. 0.03 = 3%)
    profit_threshold_pct : maximum acceptable impact as % of price
    min_child_size : minimum chunk size (base asset)
    max_child_size : maximum chunk size before splitting is triggered
    twap_window_sec: total time window over which to distribute children
    Y              : Square-Root Law constant (default 0.5)
    """

    def __init__(
        self,
        daily_volume: float = 1_000.0,
        sigma_daily: float = 0.03,
        profit_threshold_pct: float = 0.002,   # 0.2% max impact
        min_child_size: float = 0.001,
        max_child_size: float = 0.1,
        twap_window_sec: float = 30.0,
        Y: float = _Y_CONST,
    ) -> None:
        self.daily_volume = daily_volume
        self.sigma_daily = sigma_daily
        self.profit_threshold_pct = profit_threshold_pct
        self.min_child_size = min_child_size
        self.max_child_size = max_child_size
        self.twap_window_sec = twap_window_sec
        self.Y = Y

    def estimate_impact(self, qty: float) -> float:
        """
        I(Q) = Y * sigma * sqrt(Q / V)
        Returns impact as a fraction of price.
        """
        if self.daily_volume < 1e-9:
            return float("inf")
        return self.Y * self.sigma_daily * math.sqrt(qty / self.daily_volume)

    def should_split(self, qty: float) -> bool:
        """True if impact exceeds profitability threshold."""
        return self.estimate_impact(qty) > self.profit_threshold_pct

    def split(self, qty: float) -> list[OrderChunk]:
        """
        Split qty into TWAP chunks.

        If impact is acceptable, returns a single chunk.
        Otherwise, splits into N equal chunks spaced evenly over twap_window_sec.
        """
        if not self.should_split(qty):
            return [OrderChunk(size=qty, chunk_index=0, total_chunks=1, delay_sec=0.0)]

        # Determine number of chunks needed so each child has acceptable impact
        n = math.ceil(qty / self.max_child_size)
        n = max(n, 2)  # at least 2 if splitting at all

        child_size = qty / n
        if child_size < self.min_child_size:
            # Can't split further — just send as-is and accept impact
            return [OrderChunk(size=qty, chunk_index=0, total_chunks=1, delay_sec=0.0)]

        spacing = self.twap_window_sec / n
        chunks = [
            OrderChunk(
                size=child_size,
                chunk_index=i,
                total_chunks=n,
                delay_sec=i * spacing,
            )
            for i in range(n)
        ]
        return chunks

    def iter_chunks(self, qty: float) -> Iterator[OrderChunk]:
        yield from self.split(qty)

    def update_volume(self, new_daily_volume: float) -> None:
        self.daily_volume = max(new_daily_volume, 1e-9)

    def update_sigma(self, new_sigma: float) -> None:
        self.sigma_daily = max(new_sigma, 1e-6)
