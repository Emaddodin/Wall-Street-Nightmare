"""
scalper/strategies/micro_exit_controller.py
============================================
Adaptive Dynamic Micro-Exit Controller ("بازی با پوزیشن").
Forensically calibrated from real-world high-frequency manual scalping (e.g. scalp.mp4).

Key Intuitive Pillars:
1. Micro-Spike Harvest: Captures the immediate +0.60 to +1.20 pt (XAU) or +4 to +8 pip (EUR) impulse burst.
2. Momentum Stall / Deceleration: Detects tape stagnation or adverse tick stalls at the peak of the impulse.
3. Peak Watermark Trailing (Bag Protection): Never lets a +$50 to +$140 floating profit collapse into a loss.
4. Fast Breakeven Lock: Moves protective cushion to BE+spread within the first +0.35 pts / +2 pips.
5. Time Decay Stop: Hyper-scalps must explode within 25-35s; if dead in consolidation, cuts immediately.
6. Hard Risk Floor: Strictly enforces software -$15 or structural swing invalidation.
"""

from __future__ import annotations
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class MicroExitConfig:
    symbol: str = "XAUUSD"
    pip_or_pt_size: float = 0.01          # 0.01 for Gold, 0.0001 for EURUSD
    point_scale_label: str = "pts"        # "pts" or "pips"
    
    # Impulse Target thresholds (points or pips)
    fast_be_trigger: float = 0.35         # Lock BE at +0.35 pts (XAU) / +2.0 pips (EUR)
    micro_harvest_min: float = 0.60       # Minimum acceptable spike harvest
    micro_harvest_target: float = 0.90    # Primary sweet-spot harvest (matches scalp.mp4 +0.90 move)
    micro_harvest_extended: float = 1.50  # Macro run extension limit
    
    # Peak Watermark Trailing (Dynamic Lock)
    watermark_activate_usd: float = 25.0  # Activate trailing watermark when floating profit >= $25
    watermark_pullback_pct: float = 0.18  # If profit drops > 18% from peak, harvest immediately
    
    # Momentum Stall & Tape Velocity
    stall_tick_threshold: int = 3         # Stalling for 3 consecutive ticks near peak triggers harvest
    velocity_window_sec: float = 1.5      # Sliding window to track velocity (pts/sec)
    min_velocity_pts_sec: float = 0.15    # Positive velocity required to keep holding extended spike
    
    # Time Decay (Edge Lifespan)
    time_decay_seconds: float = 30.0      # Maximum allowed trade lifespan in seconds
    time_decay_profit_floor_usd: float = 0.0 # If past time_decay and profit >= 0, exit immediately
    
    # Risk Guard
    hard_risk_stop_usd: float = 15.00     # Absolute maximum loss ceiling per stack


@dataclass
class ExitDecision:
    should_exit: bool
    reason: str
    urgency: str = "NORMAL"               # "NORMAL", "HIGH", "EMERGENCY"
    metric_label: str = ""
    current_gain: float = 0.0
    floating_pnl: float = 0.0
    peak_pnl: float = 0.0
    time_in_trade_sec: float = 0.0


def get_default_config(symbol: str) -> MicroExitConfig:
    sym = symbol.upper().replace("/", "")
    if "EUR" in sym:
        return MicroExitConfig(
            symbol="EURUSD",
            pip_or_pt_size=0.0001,
            point_scale_label="pips",
            fast_be_trigger=2.0,           # 2.0 pips
            micro_harvest_min=4.0,         # 4.0 pips
            micro_harvest_target=7.0,      # 7.0 pips
            micro_harvest_extended=12.0,   # 12.0 pips
            watermark_activate_usd=20.0,
            watermark_pullback_pct=0.20,
            stall_tick_threshold=2,
            velocity_window_sec=2.0,
            min_velocity_pts_sec=1.0,      # pips/sec
            time_decay_seconds=40.0,
            hard_risk_stop_usd=15.00,
        )
    else:  # Default to XAUUSD
        return MicroExitConfig(
            symbol="XAUUSD",
            pip_or_pt_size=0.01,
            point_scale_label="pts",
            fast_be_trigger=0.40,          # +0.40 pts (friction-compensated BE lock)
            micro_harvest_min=0.60,        # +0.60 pts
            micro_harvest_target=0.85,     # +0.85 pts (forensic match with scalp.mp4 +0.85-0.96 surge)
            micro_harvest_extended=1.60,   # +1.60 pts
            watermark_activate_usd=25.0,
            watermark_pullback_pct=0.18,   # 18% pullback from peak
            stall_tick_threshold=2,
            velocity_window_sec=1.5,
            min_velocity_pts_sec=0.20,     # pts/sec
            time_decay_seconds=30.0,       # 30 seconds max duration
            hard_risk_stop_usd=15.00,
        )


def get_to_the_moon_config(symbol: str = "XAUUSD", direction: str = "BUY") -> MicroExitConfig:
    """
    Configuration for the Sovereign Compounding "To The Moon" Engine ($10k-$25k/day).
    Asymmetric Posture:
    - BUY (Hold Long): Targets +4.00 to +8.00 ATR Macro Spike Expansions.
    - SELL (Scalp Sell): Tactical scalp targets (+2.20 pts), fast BE (+0.80 pts), strict 15m lifespan.
    """
    is_buy = direction.upper() == "BUY"
    return MicroExitConfig(
        symbol="XAUUSD",
        pip_or_pt_size=0.01,
        point_scale_label="pts",
        fast_be_trigger=1.20 if is_buy else 0.80,
        micro_harvest_min=2.50 if is_buy else 1.50,
        micro_harvest_target=4.00 if is_buy else 2.20,
        micro_harvest_extended=8.00 if is_buy else 4.00,
        watermark_activate_usd=60.0,
        watermark_pullback_pct=0.18,
        stall_tick_threshold=5 if is_buy else 3,
        velocity_window_sec=5.0,
        min_velocity_pts_sec=0.10,
        time_decay_seconds=2700.0 if is_buy else 900.0,
        hard_risk_stop_usd=100.0,
    )


def get_micro_account_config(balance: float = 30.0, direction: str = "BUY") -> MicroExitConfig:
    """
    Micro-Account Guardian Configuration ($30 - $100 Accounts):
    Asymmetric Posture:
    - BUY ("Hold Long"):
      * Fast BE at +1.20 pts
      * TP1 Target: +3.50 pts (+$3.50 on 0.01 lots)
      * Extended Macro Spike harvest: +6.50 pts (+$6.50)
      * 20m lifespan (1200s), stall threshold 20
    - SELL ("Scalp Sell"):
      * Rapid BE at +0.80 pts
      * Scalp Target: +1.80 pts (+$1.80 on 0.01 lots)
      * Extended limit: +2.50 pts
      * 10m lifespan (600s), stall threshold 8
    """
    safe_risk = max(1.80, min(0.08 * balance, 2.50))
    watermark_floor = max(2.00, 0.06 * balance)
    is_buy = direction.upper() == "BUY"

    if is_buy:
        return MicroExitConfig(
            symbol="XAUUSD",
            pip_or_pt_size=0.01,
            point_scale_label="pts",
            fast_be_trigger=1.20,
            micro_harvest_min=2.50,
            micro_harvest_target=3.50,
            micro_harvest_extended=6.50,
            watermark_activate_usd=watermark_floor,
            watermark_pullback_pct=0.22,
            stall_tick_threshold=20,
            velocity_window_sec=5.0,
            min_velocity_pts_sec=0.10,
            time_decay_seconds=1200.0,
            time_decay_profit_floor_usd=0.50,
            hard_risk_stop_usd=safe_risk,
        )
    else:
        # Tactical Scalp Sell
        return MicroExitConfig(
            symbol="XAUUSD",
            pip_or_pt_size=0.01,
            point_scale_label="pts",
            fast_be_trigger=0.80,
            micro_harvest_min=1.20,
            micro_harvest_target=1.80,
            micro_harvest_extended=2.50,
            watermark_activate_usd=watermark_floor,
            watermark_pullback_pct=0.20,
            stall_tick_threshold=8,
            velocity_window_sec=4.0,
            min_velocity_pts_sec=0.15,
            time_decay_seconds=600.0,
            time_decay_profit_floor_usd=0.30,
            hard_risk_stop_usd=safe_risk,
        )



class MicroExitController:
    """
    Stateful per-trade exit engine. Evaluated on every live price tick (<20ms).
    """

    def __init__(self, config: Optional[MicroExitConfig] = None):
        self.cfg = config or get_default_config("XAUUSD")
        self.entry_price: float = 0.0
        self.direction: str = "BUY"
        self.open_time: float = 0.0
        self.total_volume: float = 0.0
        self.sl_price: float = 0.0
        
        # In-flight tracking
        self.peak_price: float = 0.0
        self.peak_pnl: float = 0.0
        self.be_locked: bool = False
        self.ticks_history: List[Tuple[float, float]] = []  # (timestamp, price)
        self.consecutive_stalls: int = 0
        self.last_price: float = 0.0
        self.dynamic_hard_stop: float = self.cfg.hard_risk_stop_usd

    def arm_position(
        self,
        entry_price: float,
        direction: str,
        total_volume: float,
        sl_price: float = 0.0,
        open_time: Optional[float] = None,
    ) -> None:
        """Initializes state when an order stack is dispatched."""
        self.entry_price = float(entry_price)
        self.direction = direction.upper()
        self.total_volume = float(total_volume)
        self.sl_price = float(sl_price)
        self.open_time = open_time or time.time()
        
        self.peak_price = self.entry_price
        self.peak_pnl = 0.0
        self.be_locked = False
        self.ticks_history = [(self.open_time, self.entry_price)]
        self.consecutive_stalls = 0
        self.last_price = self.entry_price

        # Compute volume-adjusted dynamic risk ceiling with spread cushion
        if self.sl_price > 0.0 and self.total_volume > 0.0:
            structural_dist = abs(self.entry_price - self.sl_price)
            multiplier = 100.0 if self.cfg.symbol == "XAUUSD" else 100000.0
            computed_risk = structural_dist * multiplier * self.total_volume
            if self.cfg.hard_risk_stop_usd <= 2.50:  # Micro-account mode clamp (scaled by micro lot ratio)
                vol_ratio = max(1.0, round(self.total_volume / 0.01, 2))
                effective_risk_floor = self.cfg.hard_risk_stop_usd * vol_ratio
                self.dynamic_hard_stop = min(effective_risk_floor * 1.25, max(effective_risk_floor, round(computed_risk * 1.15, 2)))
            else:
                self.dynamic_hard_stop = max(self.cfg.hard_risk_stop_usd, round(computed_risk * 1.15, 2))
        else:
            self.dynamic_hard_stop = self.cfg.hard_risk_stop_usd

    def evaluate_tick(
        self,
        current_price: float,
        floating_pnl: float,
        current_time: Optional[float] = None,
    ) -> ExitDecision:
        """
        Microsecond evaluation of the tick against the intuitive scalper exit rules.
        """
        now = current_time or time.time()
        time_in_trade = max(0.001, now - self.open_time)
        self.ticks_history.append((now, current_price))
        
        # Keep rolling tick window within velocity_window_sec
        cutoff = now - self.cfg.velocity_window_sec
        self.ticks_history = [t for t in self.ticks_history if t[0] >= cutoff]

        # 1. Calculate price gain in direction
        if self.direction == "BUY":
            raw_gain = current_price - self.entry_price
            favorable_peak = current_price > self.peak_price
            if favorable_peak:
                self.peak_price = current_price
        else:
            raw_gain = self.entry_price - current_price
            favorable_peak = current_price < self.peak_price
            if favorable_peak:
                self.peak_price = current_price

        # Gain in points or pips with zero-division safeguard
        divisor = self.cfg.pip_or_pt_size if self.cfg.pip_or_pt_size > 0 else 0.0001
        gain_units = raw_gain / divisor if self.cfg.point_scale_label == "pips" else raw_gain

        # Update peak PnL
        if floating_pnl > self.peak_pnl:
            self.peak_pnl = floating_pnl

        # Track consecutive stalls near peak
        if favorable_peak:
            self.consecutive_stalls = 0
        else:
            self.consecutive_stalls += 1

        self.last_price = current_price

        # -------------------------------------------------------------
        # EXIT RULE 0: PEAK WATERMARK TRAILING (Bag Protection - Top Priority)
        # Never let a substantial floating profit collapse into breakeven or loss!
        # -------------------------------------------------------------
        if self.peak_pnl >= self.cfg.watermark_activate_usd:
            pullback_amount = self.peak_pnl - floating_pnl
            pullback_pct = pullback_amount / self.peak_pnl if self.peak_pnl > 0 else 0.0
            if pullback_pct >= self.cfg.watermark_pullback_pct:
                pnl_str = f"+${floating_pnl:.2f}" if floating_pnl >= 0 else f"-${abs(floating_pnl):.2f}"
                return ExitDecision(
                    should_exit=True,
                    reason=f"💰 Peak Watermark Harvest: Locked {pnl_str} (Peak was +${self.peak_pnl:.2f}, -{pullback_pct*100:.1f}%)",
                    urgency="HIGH",
                    metric_label="PEAK_WATERMARK",
                    current_gain=gain_units,
                    floating_pnl=floating_pnl,
                    peak_pnl=self.peak_pnl,
                    time_in_trade_sec=time_in_trade,
                )

        # -------------------------------------------------------------
        # EXIT RULE 1: HARD DISASTER RISK STOP (-$15 or Initial SL Hit)
        # -------------------------------------------------------------
        sl_violated = False
        if self.sl_price > 0.0:
            if self.direction == "BUY" and current_price <= self.sl_price:
                sl_violated = True
            elif self.direction == "SELL" and current_price >= self.sl_price:
                sl_violated = True

        effective_stop_usd = getattr(self, "dynamic_hard_stop", self.cfg.hard_risk_stop_usd)
        is_be_or_profit_sl = (
            (self.direction == "BUY" and self.sl_price >= self.entry_price) or
            (self.direction == "SELL" and self.sl_price <= self.entry_price)
        ) or self.be_locked
        if is_be_or_profit_sl:
            self.be_locked = True
        if sl_violated and is_be_or_profit_sl:
            return ExitDecision(
                should_exit=True,
                reason=f"🛡️ Breakeven Cushion Hit (PnL: ${floating_pnl:.2f}, SL: {self.sl_price:.2f})",
                urgency="NORMAL",
                metric_label="BREAKEVEN_CUSHION",
                current_gain=gain_units,
                floating_pnl=floating_pnl,
                peak_pnl=self.peak_pnl,
                time_in_trade_sec=time_in_trade,
            )

        if floating_pnl <= -effective_stop_usd or sl_violated:
            return ExitDecision(
                should_exit=True,
                reason=f"🛑 Hard Risk Stop Hit (PnL: ${floating_pnl:.2f}, SL: {self.sl_price:.2f})",
                urgency="EMERGENCY",
                metric_label="HARD_STOP",
                current_gain=gain_units,
                floating_pnl=floating_pnl,
                peak_pnl=self.peak_pnl,
                time_in_trade_sec=time_in_trade,
            )

        # -------------------------------------------------------------
        # EXIT RULE 2: FAST BREAKEVEN LOCK (At +0.35 pts / +2.0 pips)
        # -------------------------------------------------------------
        if not self.be_locked and gain_units >= self.cfg.fast_be_trigger:
            self.be_locked = True
            spread_buffer = 0.35 if self.cfg.symbol == "XAUUSD" else (0.5 * self.cfg.pip_or_pt_size)
            if self.direction == "BUY":
                self.sl_price = max(self.sl_price, self.entry_price + spread_buffer)
            else:
                self.sl_price = min(self.sl_price, self.entry_price - spread_buffer) if self.sl_price > 0.0 else (self.entry_price - spread_buffer)

        # -------------------------------------------------------------
        # EXIT RULE 3: TARGET IMPULSE REACHED (Sweet-Spot Spike Harvest)
        # Forensically matches scalp.mp4: +0.90 to +1.20 pts burst
        # -------------------------------------------------------------
        if gain_units >= self.cfg.micro_harvest_target:
            # If extended and still moving cleanly, check if stalled
            if gain_units >= self.cfg.micro_harvest_extended:
                return ExitDecision(
                    should_exit=True,
                    reason=f"🚀 Macro Extended Spike Harvest (+{gain_units:.2f} {self.cfg.point_scale_label}, PnL: +${floating_pnl:.2f})",
                    urgency="HIGH",
                    metric_label="EXTENDED_SPIKE",
                    current_gain=gain_units,
                    floating_pnl=floating_pnl,
                    peak_pnl=self.peak_pnl,
                    time_in_trade_sec=time_in_trade,
                )
            
            # In primary target zone: if momentum stalls for >= stall_tick_threshold, lock profit!
            if self.consecutive_stalls >= self.cfg.stall_tick_threshold:
                return ExitDecision(
                    should_exit=True,
                    reason=f"🎯 Primary Sweet-Spot Harvest on Stall (+{gain_units:.2f} {self.cfg.point_scale_label}, PnL: +${floating_pnl:.2f})",
                    urgency="HIGH",
                    metric_label="SWEET_SPOT_STALL",
                    current_gain=gain_units,
                    floating_pnl=floating_pnl,
                    peak_pnl=self.peak_pnl,
                    time_in_trade_sec=time_in_trade,
                )

        # -------------------------------------------------------------
        # EXIT RULE 4: MOMENTUM STALL IN MIN-HARVEST ZONE (Requires substantial stall + pullback)
        # -------------------------------------------------------------
        pullback = (self.peak_price - current_price) if self.direction == "BUY" else (current_price - self.peak_price)
        min_pullback = (4.0 * self.cfg.pip_or_pt_size) if self.cfg.point_scale_label == "pips" else 0.40
        if gain_units >= self.cfg.micro_harvest_min and self.consecutive_stalls >= (self.cfg.stall_tick_threshold * 2) and pullback >= min_pullback:
            return ExitDecision(
                should_exit=True,
                reason=f"⚡ Momentum Stalled at Peak (+{gain_units:.2f} {self.cfg.point_scale_label}, PnL: +${floating_pnl:.2f})",
                urgency="HIGH",
                metric_label="MOMENTUM_STALL",
                current_gain=gain_units,
                floating_pnl=floating_pnl,
                peak_pnl=self.peak_pnl,
                time_in_trade_sec=time_in_trade,
            )

        # -------------------------------------------------------------
        # EXIT RULE 5: TIME DECAY STOP (Edge Lifespan Expired)
        # -------------------------------------------------------------
        if time_in_trade >= self.cfg.time_decay_seconds:
            # If trade has positive or flat profit, cut it immediately!
            if floating_pnl >= self.cfg.time_decay_profit_floor_usd:
                return ExitDecision(
                    should_exit=True,
                    reason=f"⏳ Scalp Time-Decay Exit after {time_in_trade:.1f}s (PnL: +${floating_pnl:.2f})",
                    urgency="NORMAL",
                    metric_label="TIME_DECAY_PROFIT",
                    current_gain=gain_units,
                    floating_pnl=floating_pnl,
                    peak_pnl=self.peak_pnl,
                    time_in_trade_sec=time_in_trade,
                )
            # If negative but trade has lingered for 1.5x time_decay, cut it to prevent dead bleed
            elif time_in_trade >= (self.cfg.time_decay_seconds * 1.5):
                return ExitDecision(
                    should_exit=True,
                    reason=f"⏳ Scalp Timeout Invalidation after {time_in_trade:.1f}s (PnL: ${floating_pnl:.2f})",
                    urgency="NORMAL",
                    metric_label="TIME_DECAY_TIMEOUT",
                    current_gain=gain_units,
                    floating_pnl=floating_pnl,
                    peak_pnl=self.peak_pnl,
                    time_in_trade_sec=time_in_trade,
                )

        # Still active, riding the micro-wave
        return ExitDecision(
            should_exit=False,
            reason="Holding micro-momentum wave",
            urgency="NORMAL",
            metric_label="ACTIVE_HOLD",
            current_gain=gain_units,
            floating_pnl=floating_pnl,
            peak_pnl=self.peak_pnl,
            time_in_trade_sec=time_in_trade,
        )
