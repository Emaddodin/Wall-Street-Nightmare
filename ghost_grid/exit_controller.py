"""
ghost_grid/exit_controller.py
=============================
Rapid micro-scalp exit controller for the MR P FX break & retest strategy.

All thresholds are in PRICE POINTS of net basket move (after spread, measured bid/ask), converted to
dollars with the live total lot size, so the same rules behave identically at 0.03 lots and at 0.30 lots.
(The old dollar thresholds - +$1.20 target, $0.80 watermark - collapsed to a fraction of the spread as
lots grew, so bigger tiers exited on noise.)

  1. Hard stop        : net move <= -hard_stop_pts (the engine sets this per basket)
  2. Quick target     : net move >= quick_tp_pts
  3. Watermark lock   : once peak >= watermark_min_peak_pts, close green on a 30% giveback
  4. Stagnation cut   : after stagnation_cut_secs with < stagnation_min_pts, cut it
  5. Max hold         : hard exit at max_hold_time_secs
The clock starts at the first fill (earliest entry_time), not at the first evaluated tick.
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class GridExitConfig:
    # Original MR P FX values (docs: +35-50 cent target, 25% watermark pullback, 45s stagnation, 90s max hold,
    # ~-$4 basket stop on 0.03 lots = 1.3pt), converted to points so they are identical at every lot size.
    quick_tp_pts: float = 0.45
    watermark_min_peak_pts: float = 0.30
    watermark_pullback_pct: float = 0.25
    watermark_floor_pts: float = 0.07
    stagnation_cut_secs: float = 45.0
    stagnation_min_pts: float = 0.07
    max_hold_time_secs: float = 90.0
    hard_stop_pts: float = 1.30


@dataclass
class GridExitDecision:
    action: str  # "HOLD", "CLOSE_ALL"
    reason: str
    positions_to_close: List[str] = field(default_factory=list)


def get_ghost_grid_exit_config() -> GridExitConfig:
    return GridExitConfig()


class GridExitController:
    def __init__(self, config: Optional[GridExitConfig] = None):
        self.config = config or get_ghost_grid_exit_config()
        self.peak_pts = 0.0
        self.grid_start_time = 0.0

    def reset(self):
        self.peak_pts = 0.0
        self.grid_start_time = 0.0

    def evaluate_grid_tick(self, current_price: float, grid_positions: List[dict], current_time: float) -> GridExitDecision:
        if not grid_positions:
            return GridExitDecision(action="HOLD", reason="No open positions")

        total_lots = sum(p.get("volume", 0.0) for p in grid_positions)
        if total_lots <= 0:
            return GridExitDecision(action="HOLD", reason="Zero volume")

        entry_times = [p["entry_time"] for p in grid_positions if p.get("entry_time")]
        start = min(entry_times) if entry_times else (self.grid_start_time or current_time)
        if self.grid_start_time == 0.0:
            self.grid_start_time = start
        elapsed = current_time - start

        total_pl = sum(p.get("unrealized_pl", 0.0) for p in grid_positions)
        pts = total_pl / (total_lots * 100.0)
        self.peak_pts = max(self.peak_pts, pts)
        c = self.config

        if pts <= -c.hard_stop_pts:
            return GridExitDecision(action="CLOSE_ALL", reason=f"Hard Stop ({pts:+.2f}pt / ${total_pl:.2f})")
        if pts >= c.quick_tp_pts:
            return GridExitDecision(action="CLOSE_ALL", reason=f"Scalp Target Hit ({pts:+.2f}pt / +${total_pl:.2f})")
        if self.peak_pts >= c.watermark_min_peak_pts:
            floor = self.peak_pts * (1.0 - c.watermark_pullback_pct)
            if pts < floor and pts > c.watermark_floor_pts:
                return GridExitDecision(action="CLOSE_ALL", reason=f"Watermark Lock ({pts:+.2f}pt from peak {self.peak_pts:+.2f}pt)")
        if elapsed >= c.stagnation_cut_secs and pts < c.stagnation_min_pts:
            return GridExitDecision(action="CLOSE_ALL", reason=f"Stagnation Cut ({elapsed:.0f}s, {pts:+.2f}pt)")
        if elapsed >= c.max_hold_time_secs:
            return GridExitDecision(action="CLOSE_ALL", reason=f"Max Hold Exceeded ({elapsed:.0f}s, {pts:+.2f}pt)")
        return GridExitDecision(action="HOLD", reason="Holding position")
