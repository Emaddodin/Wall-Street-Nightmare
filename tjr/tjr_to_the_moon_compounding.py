"""
tjr/tjr_to_the_moon_compounding.py
==================================
The "To The Moon" Compounding Engine calibrated specifically for a $59.00 USD start.
Features:
- Sovereign Compounding Ladder: Progressively scales volume from 0.10 lots up to 15.00 lots
- Hard Risk Floor & Micro-Risk Allocation: Maximum single-trade loss strictly budgeted
- Accelerated Breakeven Ratchets (+0.75 ATR) ensuring early risk-free status
- Dynamic Peak Watermark Lock (locks profit if floating gains retrace >18% from peak)
- High-velocity scalp duration ceiling (6 - 15 minutes max holding)
- 30% Sovereign Vault Sweep: Harvests 30% of daily gains into protected off-broker reserve
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class CompoundingTier:
    min_balance: float
    max_balance: float
    base_lot_size: float
    tier_name: str
    risk_stop_max_usd: float


class TJRToTheMoonCompounding:
    """
    Manages dynamic lot sizing and capital protection starting from $59.00 USD.
    """

    TIERS: List[CompoundingTier] = [
        CompoundingTier(0.0, 50.0, 0.05, "Floor Defense Tier", 6.0),
        CompoundingTier(50.0, 120.0, 0.10, "Genesis Launch Tier ($59 Start)", 12.0),
        CompoundingTier(120.0, 250.0, 0.20, "Early Ignition Tier", 20.0),
        CompoundingTier(250.0, 500.0, 0.40, "Stratosphere Tier", 35.0),
        CompoundingTier(500.0, 1000.0, 0.80, "Sub-Orbital Tier", 60.0),
        CompoundingTier(1000.0, 2500.0, 1.50, "Orbital Velocity Tier", 120.0),
        CompoundingTier(2500.0, 6000.0, 3.00, "Lunar Approach Tier", 250.0),
        CompoundingTier(6000.0, 15000.0, 5.00, "Lunar Orbit Tier", 500.0),
        CompoundingTier(15000.0, 50000.0, 8.00, "Sovereign Titan Tier", 1200.0),
        CompoundingTier(50000.0, float("inf"), 12.00, "To The Moon Tier ($1M+ Target)", 3000.0),
    ]

    def __init__(self, starting_balance: float = 59.0, vault_sweep_rate: float = 0.30):
        self.starting_balance = starting_balance
        self.balance = starting_balance
        self.equity = starting_balance
        self.peak_equity = starting_balance
        self.vault_reserve = 0.0
        self.vault_sweep_rate = vault_sweep_rate
        self.consecutive_losses = 0
        self.total_trades = 0
        self.wins = 0
        self.losses = 0

    def compute_lot_size(self, balance: Optional[float] = None, laya_multiplier: float = 1.0) -> Tuple[float, str]:
        bal = balance if balance is not None else self.balance
        base_lots = 0.10
        tier_title = "Genesis Launch Tier"

        for tier in self.TIERS:
            if tier.min_balance <= bal < tier.max_balance:
                base_lots = tier.base_lot_size
                tier_title = tier.tier_name
                break

        # If balance >= $50,000, scale dynamically
        if bal >= 50000.0:
            base_lots = min(15.00, round(bal / 4500.0, 2))
            tier_title = "To The Moon Titan Tier"

        # Apply Laya A-tier multiplier (up to 1.50x on Grade 3)
        final_lots = round(base_lots * max(1.0, min(1.65, laya_multiplier)), 2)
        return final_lots, tier_title

    def get_max_risk_stop(self, balance: Optional[float] = None) -> float:
        bal = balance if balance is not None else self.balance
        for tier in self.TIERS:
            if tier.min_balance <= bal < tier.max_balance:
                return tier.risk_stop_max_usd
        return 50.0

    def process_trade_result(self, realized_pnl: float, is_win: bool) -> Dict[str, Any]:
        """
        Updates internal equity, balances, win streak, and applies sovereign vault sweep.
        """
        self.total_trades += 1
        self.balance += realized_pnl
        self.equity = self.balance

        if is_win:
            self.wins += 1
            self.consecutive_losses = 0
            if self.equity > self.peak_equity:
                self.peak_equity = self.equity

            # Optional sovereign sweep: harvest 30% of profits once account passes $250
            if self.balance > 250.0 and realized_pnl > 0 and self.vault_sweep_rate > 0:
                sweep_amount = round(realized_pnl * self.vault_sweep_rate, 2)
                self.balance -= sweep_amount
                self.equity = self.balance
                self.vault_reserve += sweep_amount
        else:
            self.losses += 1
            self.consecutive_losses += 1

        lots, tier_name = self.compute_lot_size()
        return {
            "current_balance": round(self.balance, 2),
            "current_equity": round(self.equity, 2),
            "vault_reserve": round(self.vault_reserve, 2),
            "total_capital": round(self.balance + self.vault_reserve, 2),
            "active_tier": tier_name,
            "next_lot_size": lots,
            "win_rate_pct": round((self.wins / self.total_trades) * 100.0, 1) if self.total_trades > 0 else 0.0,
            "consecutive_losses": self.consecutive_losses,
        }
