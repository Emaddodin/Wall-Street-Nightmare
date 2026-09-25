"""
ghost_grid/compounding_ladder.py
================================
Exact compounding escalation ladder for MR P FX small account growth.
Balances:
- Sub-$30: Micro Survival (3 orders x 0.01 = 0.03 total lots)
- $30-$50: Breakout Velocity (3 orders x 0.02 = 0.06 total lots)
- $50-$100: Careful Expansion (4 orders x 0.03 = 0.12 total lots)
- $100-$250: Aggressive Escalation (5 orders x 0.05 = 0.25 total lots)
- $250-$500: Standard Heavy (6 orders x 0.08 = 0.48 total lots)
- $500+: Full Deployment (8-10 orders x 0.10+ lots)
"""
import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

@dataclass
class CompoundingTier:
    threshold: float
    lot_size: float
    grid_count: int
    risk_grade: str

class CompoundingLadder:
    def __init__(self, withdrawal_threshold: float = 2500.0, max_loss_floor: float = 4.0):
        self.withdrawal_threshold = withdrawal_threshold
        self.max_loss_floor = max_loss_floor
        
        self.tiers = [
            CompoundingTier(threshold=0.0, lot_size=0.01, grid_count=3, risk_grade="micro_survival"),
            CompoundingTier(threshold=30.0, lot_size=0.02, grid_count=3, risk_grade="breakout_velocity"),
            CompoundingTier(threshold=50.0, lot_size=0.03, grid_count=4, risk_grade="careful_expansion"),
            CompoundingTier(threshold=100.0, lot_size=0.05, grid_count=5, risk_grade="aggressive_escalation"),
            CompoundingTier(threshold=250.0, lot_size=0.08, grid_count=6, risk_grade="standard_heavy"),
            CompoundingTier(threshold=500.0, lot_size=0.10, grid_count=8, risk_grade="full_deployment"),
            CompoundingTier(threshold=1000.0, lot_size=0.20, grid_count=10, risk_grade="institutional_extraction"),
        ]
        
    def resolve_tier(self, balance: float) -> CompoundingTier:
        selected_tier = self.tiers[0]
        for tier in self.tiers:
            if balance >= tier.threshold:
                selected_tier = tier
            else:
                break
        return selected_tier
        
    def check_withdrawal(self, current_balance: float) -> bool:
        return current_balance >= self.withdrawal_threshold
        
    def get_risk_budget(self) -> float:
        return self.max_loss_floor
