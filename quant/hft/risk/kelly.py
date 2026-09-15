"""
quant/hft/risk/kelly.py
========================
Fractional Kelly position sizing with Isolated Margin liquidation math.

Full Kelly fraction:
    f* = (b*p - q) / b

where:
    p = win probability
    q = 1 - p (loss probability)
    b = Win/Loss ratio (avg_win / avg_loss)

Fractional Kelly:
    f_frac = fraction * f*         (e.g. fraction=0.25 for Quarter-Kelly)

Isolated Margin Liquidation Prices:
    Long:  P_liq = P_entry * (1 - 1/leverage + MMR)
    Short: P_liq = P_entry * (1 + 1/leverage - MMR)

Constraint: |P_entry - P_liq| must exceed predicted_volatility * safety_factor
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

_EPS = 1e-12


@dataclass
class PositionSpec:
    """Fully resolved position specification."""
    symbol: str
    side: str               # 'long' | 'short'
    leverage: int
    margin_usdt: float      # USDT collateral to commit (isolated)
    notional_usdt: float    # leverage * margin_usdt
    qty_base: float         # notional / entry_price
    entry_price: float
    liq_price: float
    stop_price: float       # Chandelier Exit starting stop
    kelly_fraction: float
    kelly_f_star: float


class FractionalKelly:
    """
    Computes Fractional Kelly position sizes for isolated-margin perpetual futures.

    Parameters
    ----------
    fraction        : Kelly divisor (0.25 = Quarter-Kelly recommended)
    max_leverage    : maximum allowed leverage
    mmr             : Maintenance Margin Rate (0.005 = 0.5% for BTC)
    safety_factor   : liquidation distance must be >= safety_factor * predicted_volatility
    max_risk_pct    : hard cap on % of account balance per trade
    """

    def __init__(
        self,
        fraction: float = 0.25,
        max_leverage: int = 20,
        mmr: float = 0.005,
        safety_factor: float = 2.0,
        max_risk_pct: float = 0.10,
    ) -> None:
        self.fraction = fraction
        self.max_leverage = max_leverage
        self.mmr = mmr
        self.safety_factor = safety_factor
        self.max_risk_pct = max_risk_pct

    # ------------------------------------------------------------------
    # Kelly fraction
    # ------------------------------------------------------------------

    def kelly_f_star(
        self,
        win_prob: float,
        win_loss_ratio: float,
    ) -> float:
        """
        f* = (b*p - q) / b
        Clamped to [0, 1] (no shorting Kelly, no over-betting).
        """
        p = np.clip(win_prob, _EPS, 1.0 - _EPS)
        q = 1.0 - p
        b = max(win_loss_ratio, _EPS)
        f_star = (b * p - q) / b
        return float(np.clip(f_star, 0.0, 1.0))

    def fractional_f(self, win_prob: float, win_loss_ratio: float) -> float:
        """fraction * f*"""
        return self.fraction * self.kelly_f_star(win_prob, win_loss_ratio)

    # ------------------------------------------------------------------
    # Liquidation price
    # ------------------------------------------------------------------

    def liquidation_price(
        self,
        entry_price: float,
        leverage: int,
        side: str,
    ) -> float:
        """
        Isolated margin liquidation price.
        Long:  P_liq = P_entry * (1 - 1/leverage + MMR)
        Short: P_liq = P_entry * (1 + 1/leverage - MMR)
        """
        lev = max(leverage, 1)
        if side == "long":
            return entry_price * (1.0 - 1.0 / lev + self.mmr)
        else:
            return entry_price * (1.0 + 1.0 / lev - self.mmr)

    def liq_distance_pct(self, entry: float, liq: float) -> float:
        return abs(entry - liq) / (entry + _EPS)

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def compute_position(
        self,
        symbol: str,
        side: str,
        balance_usdt: float,
        entry_price: float,
        win_prob: float,
        win_loss_ratio: float,
        predicted_vol_pct: float,   # e.g. 0.005 = 0.5%
        leverage: int | None = None,
        stop_atr_mult: float = 3.0,
        atr_price: float | None = None,
    ) -> PositionSpec | None:
        """
        Full position specification with Kelly sizing and liquidation check.

        Returns None if trade is unsafe (liq too close, Kelly says flat).
        """
        f = self.fractional_f(win_prob, win_loss_ratio)
        f_star = self.kelly_f_star(win_prob, win_loss_ratio)

        if f <= 0.005:
            logger.debug("Kelly edge too thin (f=%.4f) — skip.", f)
            return None

        # Risk budget
        risk_pct = min(f, self.max_risk_pct)
        margin_usdt = balance_usdt * risk_pct

        # Determine leverage
        if leverage is None:
            # Choose leverage so that liquidation distance >= safety_factor * vol
            # liq_dist ≈ 1/leverage - MMR
            # 1/leverage >= safety_factor * vol + MMR
            # leverage <= 1 / (safety_factor * vol + MMR)
            max_safe_lev = int(1.0 / max(self.safety_factor * predicted_vol_pct + self.mmr, 0.01))
            leverage = int(np.clip(max_safe_lev, 1, self.max_leverage))

        liq_price = self.liquidation_price(entry_price, leverage, side)
        liq_dist_pct = self.liq_distance_pct(entry_price, liq_price)

        # Safety check: liquidation must be further than safety_factor * predicted_vol
        min_safe_dist = self.safety_factor * predicted_vol_pct
        if liq_dist_pct < min_safe_dist:
            logger.warning(
                "Liq too close: dist=%.4f%% < min=%.4f%% @ lev=%d. Reducing leverage.",
                liq_dist_pct * 100, min_safe_dist * 100, leverage,
            )
            # Try to halve leverage
            leverage = max(1, leverage // 2)
            liq_price = self.liquidation_price(entry_price, leverage, side)
            liq_dist_pct = self.liq_distance_pct(entry_price, liq_price)
            if liq_dist_pct < min_safe_dist:
                logger.warning("Still unsafe after leverage reduction — skip.")
                return None

        notional = margin_usdt * leverage
        qty = notional / (entry_price + _EPS)

        # Initial stop price (Chandelier uses ATR; fallback: liq_dist * 0.7)
        if atr_price is not None:
            if side == "long":
                stop_price = entry_price - stop_atr_mult * atr_price
            else:
                stop_price = entry_price + stop_atr_mult * atr_price
        else:
            dist = entry_price * liq_dist_pct * 0.7
            stop_price = entry_price - dist if side == "long" else entry_price + dist

        return PositionSpec(
            symbol=symbol,
            side=side,
            leverage=leverage,
            margin_usdt=margin_usdt,
            notional_usdt=notional,
            qty_base=qty,
            entry_price=entry_price,
            liq_price=liq_price,
            stop_price=stop_price,
            kelly_fraction=risk_pct,
            kelly_f_star=f_star,
        )
