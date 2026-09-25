import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class CompoundingTier:
    """Represents a configuration tier for the grid based on account balance."""
    threshold: float
    lot_size: float
    grid_count: int
    max_drawdown_pts: float
    risk_grade: str

@dataclass
class CompoundingState:
    """Tracks the current compounding cycle state."""
    current_tier: CompoundingTier
    cycle_start_balance: float
    cycle_peak: float
    should_withdraw: bool
    
class CompoundingLadder:
    """
    Manages tier-based lot sizing based on account equity, modeling the 
    Ghost Grid / MR P FX escalation pattern.
    """
    def __init__(self, withdrawal_threshold: float = 2500.0, max_loss_floor: float = 15.0):
        self.withdrawal_threshold = withdrawal_threshold
        self.max_loss_floor = max_loss_floor
        
        self.tiers = [
            CompoundingTier(threshold=50.0, lot_size=0.10, grid_count=8, max_drawdown_pts=15.0, risk_grade="careful"),
            CompoundingTier(threshold=100.0, lot_size=0.50, grid_count=10, max_drawdown_pts=10.0, risk_grade="aggressive"),
            CompoundingTier(threshold=250.0, lot_size=0.50, grid_count=12, max_drawdown_pts=20.0, risk_grade="standard"),
            CompoundingTier(threshold=500.0, lot_size=0.50, grid_count=12, max_drawdown_pts=45.0, risk_grade="buffered"),
            CompoundingTier(threshold=1000.0, lot_size=0.50, grid_count=15, max_drawdown_pts=70.0, risk_grade="maximum"),
            CompoundingTier(threshold=2000.0, lot_size=0.50, grid_count=15, max_drawdown_pts=140.0, risk_grade="overdue")
        ]
        
    def resolve_tier(self, balance: float) -> CompoundingTier:
        """Determines the active grid parameters based on current balance/equity."""
        selected_tier = self.tiers[0]
        for tier in self.tiers:
            if balance >= tier.threshold:
                selected_tier = tier
            else:
                break
        return selected_tier
        
    def compute_margin(self, lot_size: float) -> float:
        """
        Calculates required margin per lot for Exness 1:Unlimited on XAUUSD.
        Unlimited leverage means margin ≈ $0.50-2.00 per lot on sub-$1K accounts.
        We estimate ~$1.50 per lot for conservatism.
        """
        return lot_size * 1.50
        
    def check_withdrawal(self, current_balance: float) -> bool:
        """Signals when balance exceeds withdrawal threshold."""
        return current_balance >= self.withdrawal_threshold
        
    def get_risk_budget(self) -> float:
        """Returns the maximum total grid P/L loss floor."""
        return self.max_loss_floor
