"""
The Human Noise Engine for Ghost Grid.
Generates stochastic variations to mimic human manual trading behavior.
"""
import random
import time
import math
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

@dataclass
class NoiseConfig:
    """Configures the behavior of the Noise Engine."""
    # Temporal noise
    delay_mu_ms: float = 1200.0
    delay_sigma_ms: float = 500.0
    delay_min_ms: float = 400.0
    delay_max_ms: float = 2800.0
    hesitation_prob: float = 0.15
    hesitation_min_s: float = 3.0
    hesitation_max_s: float = 8.0
    
    # Price noise
    price_spread_min: float = 0.10
    price_spread_max: float = 0.30
    limit_order_prob: float = 0.075
    
    # Lot size noise
    lot_variance_pct: float = 0.10
    lot_increment: float = 0.01
    test_order_prob: float = 0.10
    exposure_variance_pct: float = 0.15
    
    # Behavioral noise
    chart_switch_prob: float = 0.05
    skip_marginal_setup_prob: float = 0.175
    daily_trade_min: int = 4
    daily_trade_max: int = 12
    sacrifice_prob: float = 0.2  # ~1 bad per 5 winners
    
    # Random seed (None for system random)
    seed: Optional[int] = None

@dataclass
class NoiseDecision:
    """Represents a specific set of noise parameters for a grid deployment."""
    delays_ms: List[float]
    price_offsets: List[float]
    lot_adjustments: List[float]
    test_order_first: bool = False
    is_sacrifice: bool = False
    skip_setup: bool = False

class NoiseEngine:
    """Main class for generating human-like noise for trades."""
    
    def __init__(self, config: Optional[NoiseConfig] = None):
        self.config = config or NoiseConfig()
        self.rng = random.Random(self.config.seed)
        if self.config.seed is None:
            self.rng.seed(time.time())
            
    def compute_entry_delays(self, num_orders: int) -> List[float]:
        """Generate random delays (in ms) between consecutive orders."""
        delays = []
        for _ in range(num_orders):
            # Base gaussian delay
            delay = self.rng.gauss(self.config.delay_mu_ms, self.config.delay_sigma_ms)
            delay = max(self.config.delay_min_ms, min(delay, self.config.delay_max_ms))
            
            # Check for hesitation
            if self.rng.random() < self.config.hesitation_prob:
                hesitation = self.rng.uniform(self.config.hesitation_min_s, self.config.hesitation_max_s) * 1000
                delay += hesitation
                
            # Chart switch simulation
            if self.rng.random() < self.config.chart_switch_prob:
                delay += self.rng.uniform(2.0, 5.0) * 1000
                
            # Avoid exact clock second boundaries (modulo 1000)
            if delay % 1000 < 50:
                delay += self.rng.uniform(50, 150)
            elif delay % 1000 > 950:
                delay -= self.rng.uniform(50, 150)
                
            delays.append(delay)
        return delays
        
    def compute_price_offsets(self, num_orders: int) -> List[float]:
        """Compute price offsets around a target price for a non-uniform grid."""
        offsets = []
        for _ in range(num_orders):
            spread = self.rng.uniform(self.config.price_spread_min, self.config.price_spread_max)
            # Weighted random: favor slightly worse or closer prices non-uniformly
            # using a beta distribution centered around 0 but skewed
            beta = self.rng.betavariate(2, 2) * 2 - 1  # [-1, 1]
            offset = beta * spread
            offsets.append(offset)
            
        # Ensure it's not evenly spaced by sorting and slightly shuffling
        offsets.sort()
        if num_orders > 2:
            idx = self.rng.randint(1, num_orders - 2)
            offsets[idx], offsets[idx-1] = offsets[idx-1], offsets[idx]
            
        return offsets
        
    def compute_lot_adjustments(self, base_lot: float, num_orders: int) -> List[float]:
        """Compute individual lot sizes given a base lot size."""
        lots = []
        # Total target exposure with variance
        variance = 1.0 + self.rng.uniform(-self.config.exposure_variance_pct, self.config.exposure_variance_pct)
        target_total = (base_lot * num_orders) * variance
        
        remaining_total = target_total
        for i in range(num_orders - 1):
            # Target per order
            target_per_order = remaining_total / (num_orders - i)
            # Add variance
            var_pct = self.rng.uniform(-self.config.lot_variance_pct, self.config.lot_variance_pct)
            order_lot = target_per_order * (1 + var_pct)
            
            # Round to increment
            order_lot = max(self.config.lot_increment, round(order_lot / self.config.lot_increment) * self.config.lot_increment)
            lots.append(order_lot)
            remaining_total -= order_lot
            
        # Last order takes the rest
        last_lot = max(self.config.lot_increment, round(remaining_total / self.config.lot_increment) * self.config.lot_increment)
        lots.append(last_lot)
        return lots
        
    def should_skip_setup(self, is_marginal: bool = False) -> bool:
        """Determine if a setup should be skipped to appear less mechanical."""
        if is_marginal and self.rng.random() < self.config.skip_marginal_setup_prob:
            return True
        return False
        
    def should_inject_sacrifice(self) -> bool:
        """Determine if a sacrificial loss should be injected."""
        return self.rng.random() < self.config.sacrifice_prob
        
    def get_session_preference(self) -> str:
        """Rotate session preference for non-uniform trading times."""
        sessions = ['London', 'NewYork_AM', 'NewYork_PM', 'Tokyo']
        return self.rng.choice(sessions)
        
    def generate_grid_noise_plan(self, base_lot: float, num_orders: int, is_marginal: bool = False) -> NoiseDecision:
        """Generate a complete noise plan for a grid deployment."""
        skip = self.should_skip_setup(is_marginal)
        if skip:
            return NoiseDecision([], [], [], skip_setup=True)
            
        delays = self.compute_entry_delays(num_orders)
        offsets = self.compute_price_offsets(num_orders)
        lots = self.compute_lot_adjustments(base_lot, num_orders)
        test_first = self.rng.random() < self.config.test_order_prob
        sacrifice = self.should_inject_sacrifice()
        
        return NoiseDecision(
            delays_ms=delays,
            price_offsets=offsets,
            lot_adjustments=lots,
            test_order_first=test_first,
            is_sacrifice=sacrifice
        )
