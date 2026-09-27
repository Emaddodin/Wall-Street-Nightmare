"""
ghost_grid/runner_exit.py
=========================
Trailing exit for the Runner flip strategy, in net price points (marked at bid/ask, so spread is already paid).

  initial stop  : -stop_pts
  breakeven arm : once net gain >= stop_pts, the stop moves to +be_lock_pts
  trail         : after arming, the stop follows the best net gain minus trail_pts (never moves down)
  time stop     : max_hold_secs
No profit cap: winners run until the trail is hit.
"""
from dataclasses import dataclass
from typing import List, Optional

from ghost_grid.exit_controller import GridExitDecision


@dataclass
class RunnerExitConfig:
    stop_pts: float = 2.5
    trail_pts: float = 3.5
    be_lock_pts: float = 0.3
    max_hold_secs: float = 5400.0


class RunnerExitController:
    def __init__(self, config: Optional[RunnerExitConfig] = None):
        self.config = config or RunnerExitConfig()
        self.reset()

    def reset(self):
        self.peak_pts = 0.0
        self.stop_level = -self.config.stop_pts
        self.armed = False
        self.grid_start_time = 0.0

    def evaluate_grid_tick(self, current_price: float, grid_positions: List[dict], current_time: float) -> GridExitDecision:
        if not grid_positions:
            return GridExitDecision(action="HOLD", reason="No open positions")
        lots = sum(p.get("volume", 0.0) for p in grid_positions)
        if lots <= 0:
            return GridExitDecision(action="HOLD", reason="Zero volume")
        c = self.config
        pts = sum(p.get("unrealized_pl", 0.0) for p in grid_positions) / (lots * 100.0)
        start = min((p["entry_time"] for p in grid_positions if p.get("entry_time")), default=current_time)
        self.peak_pts = max(self.peak_pts, pts)
        if not self.armed and self.peak_pts >= c.stop_pts:
            self.armed = True
            self.stop_level = c.be_lock_pts
        if self.armed:
            self.stop_level = max(self.stop_level, self.peak_pts - c.trail_pts)
        if pts <= self.stop_level:
            kind = "Trail Stop" if self.armed else "Initial Stop"
            return GridExitDecision(action="CLOSE_ALL", reason=f"{kind} ({pts:+.2f}pt, peak {self.peak_pts:+.2f}pt)")
        if current_time - start >= c.max_hold_secs:
            return GridExitDecision(action="CLOSE_ALL", reason=f"Time Stop ({pts:+.2f}pt)")
        return GridExitDecision(action="HOLD", reason="Running")
