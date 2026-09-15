"""
quant/hft/execution/avellaneda_stoikov.py
==========================================
Avellaneda-Stoikov (2008) market-making model with GLFT extensions.

Reservation price (inventory-adjusted mid):
    r(s, q, t) = s - q * gamma * sigma^2 * (T - t)

Optimal bid/ask spread:
    delta* = gamma * sigma^2 * (T - t) + (2/gamma) * ln(1 + gamma/kappa)

Bid quote:  r - delta*/2
Ask quote:  r + delta*/2

where:
    s      = current mid-price
    q      = net inventory in base asset (+ = long, - = short)
    gamma  = risk-aversion coefficient
    sigma  = annualised volatility (per-second approximation used here)
    T - t  = time remaining in epoch (seconds)
    kappa  = order-book depth / fill-rate parameter
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

_EPS = 1e-12


@dataclass
class ReservationQuote:
    reservation_price: float  # r
    bid_price: float          # r - delta/2
    ask_price: float          # r + delta/2
    spread: float             # delta*
    inventory: float          # q
    mid_price: float          # s


class AvellanadaStoikov:
    """
    Real-time Avellaneda-Stoikov market maker.

    Usage
    -----
    as_model = AvellanadaStoikov(gamma=0.1, kappa=1.5, sigma=0.02, epoch_sec=300)
    as_model.update_inventory(+0.5)     # 0.5 BTC long
    quote = as_model.compute_quotes(mid_price=65000.0)
    """

    def __init__(
        self,
        gamma: float = 0.1,
        kappa: float = 1.5,
        sigma: float = 0.02,
        epoch_sec: float = 300.0,
        max_inventory: float = 5.0,
    ) -> None:
        """
        Parameters
        ----------
        gamma       : risk-aversion (higher = tighter skew, smaller spread)
        kappa       : fill-rate sensitivity to spread depth (market liquidity)
        sigma       : per-second price volatility (fraction of price)
        epoch_sec   : trading epoch length in seconds
        max_inventory: maximum absolute inventory (hard clamp)
        """
        self.gamma = gamma
        self.kappa = kappa
        self.sigma = sigma
        self.epoch_sec = epoch_sec
        self.max_inventory = max_inventory

        self._inventory: float = 0.0
        self._epoch_start: float = time.time()
        self._realized_pnl: float = 0.0
        self._last_mid: float = 0.0

    # ------------------------------------------------------------------
    # Inventory management
    # ------------------------------------------------------------------

    def update_inventory(self, delta_qty: float, price: float | None = None) -> None:
        """Adjust inventory by delta_qty. Positive = bought, negative = sold."""
        old = self._inventory
        self._inventory = np.clip(
            self._inventory + delta_qty,
            -self.max_inventory,
            self.max_inventory,
        )
        if price and price > 0:
            # Realised PnL when reducing inventory
            if old > 0 and delta_qty < 0:  # sold some of long
                self._realized_pnl += (-delta_qty) * (price - self._avg_entry)
            elif old < 0 and delta_qty > 0:  # covered some of short
                self._realized_pnl += delta_qty * (self._avg_entry - price)

    def reset_epoch(self) -> None:
        self._epoch_start = time.time()

    @property
    def inventory(self) -> float:
        return self._inventory

    # ------------------------------------------------------------------
    # Core computation
    # ------------------------------------------------------------------

    def time_remaining(self) -> float:
        elapsed = time.time() - self._epoch_start
        return max(self.epoch_sec - elapsed, 1.0)

    def reservation_price(self, mid: float) -> float:
        """r = s - q * gamma * sigma^2 * (T - t)"""
        T_minus_t = self.time_remaining()
        return mid - self._inventory * self.gamma * (self.sigma ** 2) * T_minus_t

    def optimal_spread(self) -> float:
        """
        delta* = gamma * sigma^2 * (T-t) + (2/gamma) * ln(1 + gamma/kappa)
        """
        T_minus_t = self.time_remaining()
        term1 = self.gamma * (self.sigma ** 2) * T_minus_t
        term2 = (2.0 / self.gamma) * np.log(1.0 + self.gamma / (self.kappa + _EPS))
        return term1 + term2

    def compute_quotes(self, mid: float) -> ReservationQuote:
        """
        Compute the full reservation quote from current state.
        Skews bid/ask symmetrically around reservation price.
        """
        self._last_mid = mid
        r = self.reservation_price(mid)
        delta = self.optimal_spread()
        half_spread = delta / 2.0

        return ReservationQuote(
            reservation_price=r,
            bid_price=r - half_spread,
            ask_price=r + half_spread,
            spread=delta,
            inventory=self._inventory,
            mid_price=mid,
        )

    # ------------------------------------------------------------------
    # Adaptive parameters
    # ------------------------------------------------------------------

    def update_sigma(self, new_sigma: float) -> None:
        """Hot-update volatility estimate (e.g. from GARCH or ATR)."""
        self.sigma = max(new_sigma, 1e-6)

    def update_kappa(self, new_kappa: float) -> None:
        """Hot-update fill-rate parameter from real-time book depth."""
        self.kappa = max(new_kappa, 0.01)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def _avg_entry(self) -> float:
        """Placeholder — full implementation needs cost-basis tracking."""
        return self._last_mid

    @property
    def realized_pnl(self) -> float:
        return self._realized_pnl

    def unrealized_pnl(self, current_mid: float) -> float:
        return self._inventory * (current_mid - self._last_mid)

    def __repr__(self) -> str:
        return (
            f"<AS gamma={self.gamma} kappa={self.kappa} "
            f"sigma={self.sigma:.4f} q={self._inventory:.4f}>"
        )
