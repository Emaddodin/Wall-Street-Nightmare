import logging
from dataclasses import dataclass, field
import random
from typing import List

logger = logging.getLogger(__name__)

@dataclass
class ChameleonConfig:
    wins_before_loss_farm: int = 5
    target_win_rate_min: float = 0.70
    target_win_rate_max: float = 0.80
    optimal_leftover_balance: float = 50.0

@dataclass
class AccountProfile:
    wins: int = 0
    losses: int = 0
    total_trades: int = 0
    avg_hold_time_secs: float = 0.0
    avg_lot_size: float = 0.0
    detection_score: float = 0.0
    
    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.wins / self.total_trades

@dataclass
class FillQualityReport:
    avg_slippage_winners: float = 0.0
    avg_slippage_losers: float = 0.0
    asymmetry_score: float = 0.0

class BrokerChameleon:
    """
    Strategic layer that coordinates all noise and anti-detection behavioral patterns.
    Makes the bot appear human to broker risk systems.
    """
    def __init__(self, config: ChameleonConfig = None):
        self.config = config or ChameleonConfig()
        self.profile = AccountProfile()
        self.consecutive_wins = 0
        self.spread_history: List[float] = []
        
    def record_trade_result(self, is_win: bool, hold_time: float, lot_size: float, slippage: float):
        """Update account profile with a new trade result."""
        self.profile.total_trades += 1
        self.profile.avg_hold_time_secs = (self.profile.avg_hold_time_secs * (self.profile.total_trades - 1) + hold_time) / self.profile.total_trades
        self.profile.avg_lot_size = (self.profile.avg_lot_size * (self.profile.total_trades - 1) + lot_size) / self.profile.total_trades
        
        if is_win:
            self.profile.wins += 1
            self.consecutive_wins += 1
        else:
            self.profile.losses += 1
            self.consecutive_wins = 0
            
        self.update_detection_score()
        
    def check_loss_farming_schedule(self) -> bool:
        """Determines if a sacrificial trade should be scheduled to farm losses."""
        if self.consecutive_wins >= self.config.wins_before_loss_farm:
            return True
        if self.profile.win_rate > self.config.target_win_rate_max:
            return True
        return False
        
    def plan_withdrawal(self, current_balance: float) -> float:
        """Computes optimal withdrawal amount leaving exactly the threshold."""
        if current_balance <= self.config.optimal_leftover_balance:
            return 0.0
        return current_balance - self.config.optimal_leftover_balance
        
    def record_spread(self, spread: float):
        """Tracks historical spreads received to detect anomaly or targeting."""
        self.spread_history.append(spread)
        if len(self.spread_history) > 1000:
            self.spread_history.pop(0)
            
    def analyze_spreads(self) -> bool:
        """Detects if broker is widening spreads for this account specifically."""
        if not self.spread_history:
            return False
        avg_spread = sum(self.spread_history) / len(self.spread_history)
        # Assuming baseline exness pro is ~12.5 pts, anomaly threshold
        if avg_spread > 20.0:
            logger.warning(f"Spread anomaly detected! Avg spread is {avg_spread}")
            return True
        return False
        
    def update_detection_score(self):
        """Computes a 0-100 detection risk score based on patterns."""
        score = 0.0
        if self.profile.win_rate > 0.90:
            score += 40.0
        if self.profile.avg_hold_time_secs < 10.0:
            score += 20.0
        if self.analyze_spreads():
            score += 30.0
            
        self.profile.detection_score = min(100.0, score)
        
    def is_safe_to_trade(self) -> bool:
        """Determines if the account state and broker parameters are safe for grid deployment."""
        if self.profile.detection_score >= 80.0:
            return False
        return True
