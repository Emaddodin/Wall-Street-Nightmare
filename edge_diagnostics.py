"""
edge_diagnostics.py
===================
Quantitative Edge Health Monitor & 3-Hour Diagnostic Notifier.

Tracks, audits, and notifies on all 11 core statistical and micro-structural edges
of the HyperPredator trading system, driving capital scaling from $65 to $10,000.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ntfy_integration import ReplayNtfyDispatcher

logger = logging.getLogger("edge_diagnostics")

# =========================================================================
# The 11 Core Trading Edges
# =========================================================================

EDGE_CATALOG: Dict[str, Dict[str, str]] = {
    "EDGE_1_MACRO_DIRECTIONAL": {
        "name": "Macro Directional Edge (Core 1)",
        "formula": "MACRO_STATE.bias == DIRECTION && permit_trade == True",
        "description": "Aligns execution exclusively with 15-minute macro trend and halts during US news blackout windows.",
        "fix_rule": "If counter-trend losses occur, enforce strict M15 EMA20 slope filter.",
    },
    "EDGE_2_DYNAMIC_SR_PIVOTS": {
        "name": "M5 Dynamic S/R Zone Edge",
        "formula": "low <= sr_low + sr_tolerance (Long) | high >= sr_high - sr_tolerance (Short)",
        "description": "Captures institutional liquidity pools from rolling 50-bar M5 pivot extremes.",
        "fix_rule": "If entry count is 0 in trending markets, increase sr_tolerance from $0.25 to $1.00-$1.50.",
    },
    "EDGE_3_REJECTION_WICK_MATH": {
        "name": "Asymmetrical Invalidation Wick Math",
        "formula": "wick_length / (high - low) >= 65% && candle_body closes in bias direction",
        "description": "Filters out churn bars and ensures protective stop is placed behind institutional order flow absorption.",
        "fix_rule": "If false wick breakouts stop out, raise wick threshold to 70%; if missing valid moves, relax to 60%.",
    },
    "EDGE_4_TICK_VELOCITY_SURGE": {
        "name": "Intra-Bar Final 5s Tick Velocity Surge",
        "formula": "final_5s_tick_density >= 1.5x rolling_tick_velocity",
        "description": "Confirms aggressive institutional market-order participation in the final 5 seconds before bar close.",
        "fix_rule": "Calibrate threshold between 1.3x and 1.8x based on London/NY killzone session volatility.",
    },
    "EDGE_5_MICRO_SPAM_LAYERING": {
        "name": "5-Slice Asynchronous Spam Layering",
        "formula": "asyncio.gather(*[slice_order(sz/5) for 5 slices]) with 20ms jitter",
        "description": "Minimizes CLOB book-sweeping slippage and prevents frontrunning by executing 5 micro-slices at 100x leverage.",
        "fix_rule": "Maintain 20ms-50ms jitter stagger to prevent exchange rate-limit throttling.",
    },
    "EDGE_6_DETACHED_STOP_LOSS": {
        "name": "Detached $1.00 Stop-Loss Envelope",
        "formula": "Stop Market at invalidation_wick_extreme +/- $1.00 with reduce_only=True",
        "description": "Resting stop order placed beyond liquidity pools, detached from entry tickets to prevent toxic fill trapping.",
        "fix_rule": "If micro-spikes touch stop before trend resumption, expand buffer to $1.25-$1.50.",
    },
    "EDGE_7_OPPOSING_SR_TARGET_EXIT": {
        "name": "Opposing M5 S/R Liquidity Target Exit",
        "formula": "Live Bid >= Resistance (Long) | Live Ask <= Support (Short)",
        "description": "Takes 100% profit dynamically the microsecond opposing institutional liquidity is reached.",
        "fix_rule": "Lock in +1.5R breakeven if price approaches within $0.50 of opposing target.",
    },
    "EDGE_8_L2_BOOK_IMBALANCE_EXIT": {
        "name": "Sub-5ms L2 Order Book Imbalance Exit",
        "formula": "(Ask_Vol / Bid_Vol) > 3.0 * volatility_regime (Long) in top 5 levels",
        "description": "Sub-millisecond tape exit before large opposing spoof/iceberg walls sweep the book against the basket.",
        "fix_rule": "Ensure volatility_regime dynamically scales threshold up to 4.0x during volatile CPI/FOMC releases.",
    },
    "EDGE_9_TAPE_DELTA_STALL_EXIT": {
        "name": "Trade Tape Volume Delta Stall Exit",
        "formula": "> 80% opposing aggressive fills in last 20 trade ticks while in profit",
        "description": "Instantly locks in floating profit upon order-flow exhaustion before reversal momentum initiates.",
        "fix_rule": "If premature exits occur during minor pullbacks, widen trade window from 20 to 30 ticks.",
    },
    "EDGE_10_HARD_EQUITY_SHIELD": {
        "name": "Hard -$10.00 Equity Shield Safeguard",
        "formula": "uPnL <= -$10.00 -> IMMEDIATE PANIC LIQUIDATION",
        "description": "Hard circuit breaker protecting the $65.00 micro-account capital base from liquidation at 100x leverage.",
        "fix_rule": "Triggering this shield enforces a mandatory 15-minute trading cooldown.",
    },
    "EDGE_11_GEOMETRIC_SCALING_COMPOUNDING": {
        "name": "$65 -> $10,000 Geometric Compounding Engine",
        "formula": "Initial Margin <= 20% of Current Equity; sz = (Equity * 0.20 * Leverage) / Price",
        "description": "Calculates dynamic sizing across 7 capital milestones ($65->$100->$250->$500->$1k->$2.5k->$5k->$10k).",
        "fix_rule": "Never exceed 20% margin ceiling regardless of conviction or win streak.",
    },
}


# =========================================================================
# Scaling Milestones Model
# =========================================================================

SCALING_MILESTONES = [
    {"stage": 1, "target": 100.0, "desc": "Proof of Edge ($65 -> $100)"},
    {"stage": 2, "target": 250.0, "desc": "Micro Cushion ($100 -> $250)"},
    {"stage": 3, "target": 500.0, "desc": "Capital Base ($250 -> $500)"},
    {"stage": 4, "target": 1000.0, "desc": "Four Figures ($500 -> $1,000)"},
    {"stage": 5, "target": 2500.0, "desc": "Accelerated Compounding ($1,000 -> $2,500)"},
    {"stage": 6, "target": 5000.0, "desc": "Halfway Milestone ($2,500 -> $5,000)"},
    {"stage": 7, "target": 10000.0, "desc": "Ultimate Target ($5,000 -> $10,000)"},
]


def get_current_scaling_milestone(current_equity: float) -> Dict[str, Any]:
    """Returns the active milestone and completion progress percentage."""
    for m in SCALING_MILESTONES:
        if current_equity < m["target"]:
            prev_target = 65.0 if m["stage"] == 1 else SCALING_MILESTONES[m["stage"] - 2]["target"]
            span = m["target"] - prev_target
            progress = max(0.0, min(100.0, ((current_equity - prev_target) / span) * 100.0))
            return {
                "stage": m["stage"],
                "target": m["target"],
                "description": m["desc"],
                "progress_pct": round(progress, 1),
                "remaining_usd": round(m["target"] - current_equity, 2),
            }
    return {
        "stage": 7,
        "target": 10000.0,
        "description": "Target Achieved! ($10,000)",
        "progress_pct": 100.0,
        "remaining_usd": 0.0,
    }


# =========================================================================
# Periodic Edge Diagnostic Reporter
# =========================================================================

class EdgeDiagnosticReporter:
    """
    Evaluates edge metrics every 3 hours (or user interval) and dispatches
    rich notifications to ntfy with actionable edge-tuning instructions.
    """

    def __init__(
        self,
        ntfy_dispatcher: Optional[ReplayNtfyDispatcher] = None,
        report_interval_seconds: float = 10800.0,  # 3 hours
    ) -> None:
        self.ntfy = ntfy_dispatcher or ReplayNtfyDispatcher()
        self.interval = report_interval_seconds
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.reports_generated: List[Dict[str, Any]] = []

    def format_diagnostic_report(
        self,
        current_equity: float,
        trades: List[Dict[str, Any]],
        macro_state: Dict[str, Any],
        shield_trips: int = 0,
    ) -> Dict[str, Any]:
        """Generates comprehensive edge diagnostic data and actionable recommendations."""
        total_trades = len(trades)
        wins = [t for t in trades if t.get("pnl", 0.0) > 0.0]
        losses = [t for t in trades if t.get("pnl", 0.0) <= 0.0]
        win_rate = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0
        total_pnl = sum(t.get("pnl", 0.0) for t in trades)

        # Milestone Progress
        milestone = get_current_scaling_milestone(current_equity)

        # Diagnose Edges & Identify Actionable Fixes
        fixes: List[str] = []
        if total_trades == 0:
            fixes.append("⚠️ [EDGE 2 & 4] Zero entries triggered: Consider widening sr_tolerance to $1.00 or lowering tick surge to 1.3x.")
        elif win_rate < 40.0:
            fixes.append("⚠️ [EDGE 3 & 1] Win rate < 40%: Filter counter-trend entries; ensure M15 EMA trend alignment before firing.")
        else:
            fixes.append("✅ [EDGE 1-5] Entry confluence nominal. Win rate: {:.1f}%.".format(win_rate))

        if shield_trips > 0:
            fixes.append(f"🚨 [EDGE 10] Hard Equity Shield triggered {shield_trips} times! Tighten entry wick rejection to >= 70%.")

        report = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "equity": round(current_equity, 2),
            "total_pnl": round(total_pnl, 2),
            "trades_count": total_trades,
            "win_rate_pct": round(win_rate, 1),
            "milestone": milestone,
            "macro_bias": macro_state.get("bias", "UNKNOWN"),
            "volatility_regime": macro_state.get("volatility_regime", 1.0),
            "shield_trips": shield_trips,
            "recommendations": fixes,
        }
        self.reports_generated.append(report)
        return report

    async def dispatch_3hour_notification(
        self,
        current_equity: float,
        trades: List[Dict[str, Any]],
        macro_state: Dict[str, Any],
        shield_trips: int = 0,
    ) -> bool:
        """Dispatches rich 3-Hour Edge Report to ntfy."""
        rep = self.format_diagnostic_report(current_equity, trades, macro_state, shield_trips)
        m = rep["milestone"]

        title = f"📊 [EDGE REPORT] Eq: ${rep['equity']:.2f} ({rep['win_rate_pct']:.1f}% WR)"
        message = (
            f"🕒 Period: 3-Hour Edge Diagnostic\n"
            f"💰 Capital: ${rep['equity']:.2f} / $10,000 (Stage {m['stage']}: {m['progress_pct']}% to ${m['target']:,.0f})\n"
            f"📈 Trades: {rep['trades_count']} | PnL: ${rep['total_pnl']:+.2f} | Win Rate: {rep['win_rate_pct']:.1f}%\n"
            f"🧠 Macro: {rep['macro_bias']} ({rep['volatility_regime']:.2f}x vol)\n"
            f"🛡️ Shield Trips: {rep['shield_trips']}\n\n"
            f"🛠️ EDGE RECOMMENDATIONS:\n" + "\n".join(rep["recommendations"])
        )

        return await self.ntfy._post_alert(
            title=title,
            message=message,
            priority="high",
            tags="bar_chart,hammer_and_wrench",
        )

    async def _loop(
        self,
        get_state_fn: Any,
    ) -> None:
        """Background loop executing diagnostic report every 3 hours."""
        logger.info("3-Hour Edge Diagnostic Reporter started (Interval: %ds)", self.interval)
        while self._running:
            await asyncio.sleep(self.interval)
            try:
                state = get_state_fn()
                equity = state.get("equity", {}).get("current", 65.0)
                trades = state.get("recent_trades", [])
                macro = state.get("macro_edge", {})
                await self.dispatch_3hour_notification(equity, trades, macro)
            except Exception as exc:
                logger.error("Error in 3-hour edge diagnostic reporter: %s", exc)

    def start(self, get_state_fn: Any) -> None:
        """Starts the background reporter task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(get_state_fn))

    def stop(self) -> None:
        """Stops the reporter."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
