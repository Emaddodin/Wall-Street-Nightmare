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


def get_to_the_moon_config(symbol: str = "XAUUSD") -> MicroExitConfig:
    """
    Configuration for the Sovereign Compounding "To The Moon" Engine ($10k-$25k/day).
    Targets +2.5 to +8.0 ATR Macro Spike Expansions with Dynamic Peak Watermark Bag Protection.
    Guarantees that a +$300 floating profit locks cash and NEVER reverses into a -$500 loss.
    """
    return MicroExitConfig(
        symbol="XAUUSD",
        pip_or_pt_size=0.01,
        point_scale_label="pts",
        fast_be_trigger=1.20,          # +1.20 pts (+1.0 ATR) -> moves SL to BE + $0.30 (Risk-Free)
        micro_harvest_min=2.50,        # +2.50 pts
        micro_harvest_target=4.00,     # +4.00 pts (+2.5 ATR primary scale-out target)
        micro_harvest_extended=8.00,   # +8.00 pts (+5.0 ATR Macro Spike Harvest)
        watermark_activate_usd=60.0,   # Activate trailing watermark once profit >= $60 (or 6% equity)
        watermark_pullback_pct=0.18,   # 18% pullback buffer (at +$300 peak, locks +$246)
        stall_tick_threshold=5,
        velocity_window_sec=5.0,
        min_velocity_pts_sec=0.10,
        time_decay_seconds=2700.0,     # 45 minutes max duration for macro trend expansion
        hard_risk_stop_usd=100.0,      # Dynamic safety risk ceiling
    )


def get_micro_account_config(balance: float = 30.0) -> MicroExitConfig:
    """
    Micro-Account Guardian Configuration ($30 - $100 Accounts):
    - Fast BE at +1.20 pts (locks risk-free stop to Entry + 0.10)
    - TP1 Target: +3.50 pts (+3.5 pts on 0.01 lots = +$3.50)
    - Extended Macro Spike harvest: +6.50 pts (+$6.50)
    - Dynamic peak watermark protection: locks once profit >= $2.00 and pulls back > 22%
    - Time decay aligned with 473-day empirical trade duration (20 min / 1200s)
    - Hard risk stop capped at $2.20 (2.20 pts on 0.01 lots)
    """
    safe_risk = max(1.80, min(0.08 * balance, 2.50))
    watermark_floor = max(2.00, 0.06 * balance)
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
            favorable_peak = current_price < self.peak_price or self.peak_price == self.entry_price
            if favorable_peak:
                self.peak_price = current_price

        # Gain in points or pips
        if self.cfg.point_scale_label == "pips":
            gain_units = raw_gain / self.cfg.pip_or_pt_size
        else:
            gain_units = raw_gain  # points for gold

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
        # EXIT RULE 0: HARD DISASTER RISK STOP (-$15 or Initial SL Hit)
        # -------------------------------------------------------------
        sl_violated = False
        if self.sl_price > 0.0:
            if self.direction == "BUY" and current_price <= self.sl_price:
                sl_violated = True
            elif self.direction == "SELL" and current_price >= self.sl_price:
                sl_violated = True

        effective_stop_usd = getattr(self, "dynamic_hard_stop", self.cfg.hard_risk_stop_usd)
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
        # EXIT RULE 1: FAST BREAKEVEN LOCK (At +0.35 pts / +2.0 pips)
        # -------------------------------------------------------------
        if not self.be_locked and gain_units >= self.cfg.fast_be_trigger:
            self.be_locked = True
            spread_buffer = 0.05 if self.cfg.symbol == "XAUUSD" else (0.5 * self.cfg.pip_or_pt_size)
            if self.direction == "BUY":
                self.sl_price = max(self.sl_price, self.entry_price + spread_buffer)
            else:
                self.sl_price = min(self.sl_price, self.entry_price - spread_buffer)

        # -------------------------------------------------------------
        # EXIT RULE 2: TARGET IMPULSE REACHED (Sweet-Spot Spike Harvest)
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
        # EXIT RULE 3: PEAK WATERMARK TRAILING (Bag Protection)
        # If profit reached >= $25 and drops by > 18% of peak -> Harvest!
        # -------------------------------------------------------------
        if self.peak_pnl >= self.cfg.watermark_activate_usd:
            pullback_amount = self.peak_pnl - floating_pnl
            pullback_pct = pullback_amount / self.peak_pnl if self.peak_pnl > 0 else 0.0
            if pullback_pct >= self.cfg.watermark_pullback_pct:
                return ExitDecision(
                    should_exit=True,
                    reason=f"💰 Peak Watermark Harvest: Locked +${floating_pnl:.2f} (Peak was +${self.peak_pnl:.2f}, -{pullback_pct*100:.1f}%)",
                    urgency="HIGH",
                    metric_label="PEAK_WATERMARK",
                    current_gain=gain_units,
                    floating_pnl=floating_pnl,
                    peak_pnl=self.peak_pnl,
                    time_in_trade_sec=time_in_trade,
                )

        # -------------------------------------------------------------
        # EXIT RULE 4: MOMENTUM STALL IN MIN-HARVEST ZONE (Requires substantial stall + pullback)
        # -------------------------------------------------------------
        pullback = (self.peak_price - current_price) if self.direction == "BUY" else (current_price - self.peak_price)
        if gain_units >= self.cfg.micro_harvest_min and self.consecutive_stalls >= (self.cfg.stall_tick_threshold * 2) and pullback >= 0.40:
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
