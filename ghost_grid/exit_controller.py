import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

@dataclass
class GridExitConfig:
    tp1_pts: float = 2.80              # Allow expansion to develop (MR P FX captures 2.50 - 5.00 pts)
    tp1_scale_out_pct: float = 0.60
    trail_pct: float = 0.40
    watermark_pullback_pct: float = 0.45 # Allow 45% retracement so normal candle wicks don't cut winning runs
    watermark_min_peak: float = 3.50   # Peak profit in USD before watermark ratchet locks in
    time_decay_min_secs: float = 360.0 # 6 minutes minimum holding time before considering decay
    time_decay_min_pl: float = 0.75    # Minimum USD profit expected after 6 minutes
    hard_stop_loss: float = -3.50      # Micro-account hard stop ceiling ($3.50 max basket risk)
    spike_harvest_usd: float = 8.00    # Rapid macro spike harvest in USD ($8-24 gain)
    spike_harvest_secs: float = 180.0  # 3 minutes for rapid spike
    spike_harvest_pct: float = 0.80
    stall_range_pts: float = 0.35
    stall_secs: float = 180.0          # 3 minutes consolidation tolerance (no premature cuts during healthy pauses)

@dataclass
class GridExitDecision:
    action: str  # HOLD, SCALE_OUT_60, SCALE_OUT_80, CLOSE_ALL
    reason: str
    positions_to_close: List[str] = field(default_factory=list)

def get_ghost_grid_exit_config() -> GridExitConfig:
    return GridExitConfig()

class GridExitController:
    """
    Grid Exit Controller - adapts the production MicroExitController for grid batch management.
    Handles partial scale-outs, trailing, hard stops, and time decay on a batch of positions.
    """
    def __init__(self, config: Optional[GridExitConfig] = None):
        self.config = config or get_ghost_grid_exit_config()
        self.peak_pl = 0.0
        self.grid_start_time = 0.0
        self.stall_start_time = 0.0
        self.stall_high = 0.0
        self.stall_low = float('inf')
        
    def reset(self):
        """Resets the state of the controller for a new grid batch."""
        self.peak_pl = 0.0
        self.grid_start_time = 0.0
        self.stall_start_time = 0.0
        self.stall_high = 0.0
        self.stall_low = float('inf')
        
    def evaluate_grid_tick(self, current_price: float, grid_positions: List[dict], current_time: float) -> GridExitDecision:
        """
        Evaluates the current price against the active grid positions and returns an exit decision.
        """
        if not grid_positions:
            return GridExitDecision(action="HOLD", reason="No open positions")
            
        if self.grid_start_time == 0.0:
            self.grid_start_time = current_time
            
        total_pl = sum(p.get("unrealized_pl", 0.0) for p in grid_positions)
        elapsed_time = current_time - self.grid_start_time
        
        self.peak_pl = max(self.peak_pl, total_pl)
        
        # 6. Hard Stop
        if total_pl <= self.config.hard_stop_loss:
            return GridExitDecision(action="CLOSE_ALL", reason="Hard stop loss hit")
            
        # 7. Spike Harvest
        if total_pl >= self.config.spike_harvest_usd and elapsed_time <= self.config.spike_harvest_secs:
            return GridExitDecision(action="SCALE_OUT_80", reason="Spike harvest triggered")
            
        # 4. Grid Watermark
        if self.peak_pl > self.config.watermark_min_peak:
            pullback_threshold = self.peak_pl * (1.0 - self.config.watermark_pullback_pct)
            if total_pl < pullback_threshold:
                return GridExitDecision(action="CLOSE_ALL", reason="Watermark pullback triggered")
                
        # 5. Time Decay
        if elapsed_time >= self.config.time_decay_min_secs and total_pl < self.config.time_decay_min_pl:
            return GridExitDecision(action="CLOSE_ALL", reason="Time decay stagnation")
            
        # 8. Momentum Stall Detection
        if current_price > self.stall_high:
            self.stall_high = current_price
            self.stall_start_time = current_time
        if current_price < self.stall_low:
            self.stall_low = current_price
            self.stall_start_time = current_time
            
        stall_range = self.stall_high - self.stall_low
        stall_elapsed = current_time - self.stall_start_time
        
        if stall_range <= self.config.stall_range_pts and stall_elapsed >= self.config.stall_secs:
            return GridExitDecision(action="SCALE_OUT_60", reason="Momentum stall detected")
            
        # 3. Partial Scale-Out (TP1)
        total_lots = sum(p.get("lot_size", 0.0) for p in grid_positions)
        if total_lots > 0:
            avg_entry = sum(p.get("entry_price", 0.0) * p.get("lot_size", 0.0) for p in grid_positions) / total_lots
            is_long = grid_positions[0].get("direction", "long") == "long"
            pts_profit = (current_price - avg_entry) if is_long else (avg_entry - current_price)
            if pts_profit >= self.config.tp1_pts:
                return GridExitDecision(action="SCALE_OUT_60", reason="TP1 reached")
                
        return GridExitDecision(action="HOLD", reason="No exit criteria met")
