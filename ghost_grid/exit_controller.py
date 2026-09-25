"""
ghost_grid/exit_controller.py
=============================
Rapid Micro-Scalp Exit Controller for MR P FX Strategy.
Executes lightning fast profit captures and tight drawdown cuts:
1. Target Capture: Closes all immediately at +$0.35 to +$0.60 profit per lot ($1.50 - $4.00 net basket gain).
2. Watermark Scalp Trail: Peak profit ratchet locks in gains if momentum stalls.
3. Time Decay: Max hold time strictly 45 - 90 seconds (MR P FX never sits in chop).
4. Hard Floor: -$4.00 max risk per basket.
"""
import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

@dataclass
class GridExitConfig:
    # MR P FX Rapid Scalp Targets
    quick_tp_pts: float = 0.40          # 40 cents Gold move ($0.40 x volume x 100)
    quick_tp_usd: float = 1.20          # Immediate basket take-profit floor ($1.20 - $3.50)
    
    # Ratchet Trailing
    watermark_min_peak: float = 0.80    # Start protecting once basket is up $0.80
    watermark_pullback_pct: float = 0.25 # If drops 25% from peak profit, TAKE PROFIT IMMEDIATELY
    
    # Ultra-Strict Time Decay (Never hold > 90 seconds)
    max_hold_time_secs: float = 90.0
    stagnation_cut_secs: float = 45.0
    stagnation_min_pl: float = 0.20
    
    # Hard Risk Stop Loss Floor
    hard_stop_loss_usd: float = -4.00   # Max tolerable loss per basket attempt

@dataclass
class GridExitDecision:
    action: str  # "HOLD", "CLOSE_ALL"
    reason: str
    positions_to_close: List[str] = field(default_factory=list)

def get_ghost_grid_exit_config() -> GridExitConfig:
    return GridExitConfig()

class GridExitController:
    """
    Rapid Execution Exit Controller modeled on MR P FX manual scalp closing.
    """
    def __init__(self, config: Optional[GridExitConfig] = None):
        self.config = config or get_ghost_grid_exit_config()
        self.peak_pl = 0.0
        self.grid_start_time = 0.0
        
    def reset(self):
        self.peak_pl = 0.0
        self.grid_start_time = 0.0
        
    def evaluate_grid_tick(self, current_price: float, grid_positions: List[dict], current_time: float) -> GridExitDecision:
        if not grid_positions:
            return GridExitDecision(action="HOLD", reason="No open positions")
            
        if self.grid_start_time == 0.0:
            self.grid_start_time = current_time
            
        total_pl = sum(p.get("unrealized_pl", 0.0) for p in grid_positions)
        elapsed_time = current_time - self.grid_start_time
        
        self.peak_pl = max(self.peak_pl, total_pl)
        
        # 1. HARD RISK CEILING: Protect balance from any large pullback
        if total_pl <= self.config.hard_stop_loss_usd:
            return GridExitDecision(action="CLOSE_ALL", reason=f"Hard Stop Loss Floor Hit (${total_pl:.2f})")
            
        # 2. LIGHTNING PROFIT TARGET: MR P FX takes the money and runs!
        if total_pl >= self.config.quick_tp_usd:
            return GridExitDecision(action="CLOSE_ALL", reason=f"Rapid Scalp Target Hit (+${total_pl:.2f})")
            
        # 3. WATERMARK PROFIT LOCK: If was up >= $0.80 and starts dropping, CLOSE IN GREEN
        if self.peak_pl >= self.config.watermark_min_peak:
            pullback_threshold = self.peak_pl * (1.0 - self.config.watermark_pullback_pct)
            if total_pl < pullback_threshold and total_pl > 0.20:
                return GridExitDecision(action="CLOSE_ALL", reason=f"Watermark Profit Lock (+${total_pl:.2f} from peak +${self.peak_pl:.2f})")
                
        # 4. TIME DECAY CUT: If 45s passed and not gaining momentum, kill it
        if elapsed_time >= self.config.stagnation_cut_secs and total_pl < self.config.stagnation_min_pl:
            return GridExitDecision(action="CLOSE_ALL", reason=f"Stagnation Momentum Cut ({elapsed_time:.0f}s elapsed, PnL: ${total_pl:.2f})")
            
        # 5. HARD MAXIMUM HOLD TIME (90s): MR P FX never holds trades longer than 1-2 minutes
        if elapsed_time >= self.config.max_hold_time_secs:
            return GridExitDecision(action="CLOSE_ALL", reason=f"Max Scalp Hold Duration Exceeded ({elapsed_time:.0f}s)")
            
        return GridExitDecision(action="HOLD", reason="Holding position")
