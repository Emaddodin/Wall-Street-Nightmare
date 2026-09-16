"""
quant/hft/risk/day_planner.py
==============================
Institutional Day Planner — manages the trading day as a structured campaign.

The DayPlanner divides each UTC day (00:00–24:00) into kill-zone sessions and
continuously tracks progress toward the daily +100% compounding target.

Key responsibilities:
  1. Pace control   — spread risk across available kill zones
  2. Kelly throttle — scale Kelly fraction based on progress toward target
  3. Urgency mode   — increase risk if behind schedule late in the day
  4. Victory mode   — de-risk / halt when target is approached
  5. Loss limiter   — reduce size after consecutive losses
  6. Quota tracking — track remaining trades allowed in the session

Risk regimes:
  ┌────────────────────┬──────────────────────────────────────────────┐
  │  REGIME            │  KELLY MULTIPLIER   BEHAVIOR                │
  │  ──────────────    │  ────────────────   ────────────            │
  │  NORMAL            │  1.00×              Business as usual       │
  │  AHEAD             │  0.75×              Protect gains           │
  │  ALMOST_THERE      │  0.50×              One careful shot left   │
  │  TARGET_HIT        │  0.00×              No new trades           │
  │  BEHIND_EARLY      │  1.10×              Slight push             │
  │  BEHIND_LATE       │  1.25×              Aggressive push         │
  │  DRAWDOWN_WARNING  │  0.50×              Capital preservation    │
  │  DRAWDOWN_HALT     │  0.00×              Circuit breaker         │
  └────────────────────┴──────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Kill-zone time budget (how the day's 24h is divided for pacing)
# ---------------------------------------------------------------------------

KILL_ZONE_WINDOWS = [
    # (name,   start_hour, end_hour, weight)
    # weight = fraction of daily target this window should ideally capture
    ("Asian Open",    0,  2,  0.10),
    ("London Open",   2,  5,  0.25),
    ("NY Open",       7, 10,  0.30),
    ("London Close", 11, 13,  0.15),
    ("NY Afternoon", 14, 16,  0.20),
]


@dataclass
class DayPlan:
    """Snapshot of the day's trading plan at any point in time."""
    # Thresholds
    start_balance: float = 65.0
    target_balance: float = 130.0
    loss_floor: float = 32.5

    # Current state
    current_equity: float = 65.0
    day_pnl_usdt: float = 0.0
    progress_pct: float = 0.0    # 0-100, how far toward daily target
    time_elapsed_pct: float = 0.0  # 0-100, how much of the day is gone

    # Regime
    regime: str = "NORMAL"
    kelly_multiplier: float = 1.0
    confidence_floor: float = 0.60  # min CatBoost confidence to trade
    max_leverage_cap: int = 20

    # Session tracking
    current_session: str = ""
    session_quota_used: int = 0
    session_quota_max: int = 12   # max trades per session
    day_trades: int = 0
    day_max_trades: int = 40      # absolute daily max
    consecutive_losses: int = 0

    # Time
    hours_remaining: float = 24.0
    kz_hours_remaining: float = 0.0  # remaining hours of active kill zones


class DayPlanner:
    """
    Manages trading day pacing and risk regime for the +100% daily target.

    Usage
    -----
    planner = DayPlanner(start_balance=65.0, target_pct=1.0, loss_limit_pct=0.50)
    ...
    plan = planner.evaluate(current_equity=82.0, day_trades=5, consec_losses=0)
    kelly_adj = base_kelly * plan.kelly_multiplier
    if plan.regime == "TARGET_HIT" or plan.regime == "DRAWDOWN_HALT":
        skip_trade()
    """

    def __init__(
        self,
        start_balance: float = 65.0,
        target_pct: float = 1.0,
        loss_limit_pct: float = 0.50,
        max_trades_per_session: int = 12,
        max_trades_per_day: int = 40,
    ) -> None:
        self.start_balance = start_balance
        self.target_pct = target_pct
        self.loss_limit_pct = loss_limit_pct
        self.max_trades_per_session = max_trades_per_session
        self.max_trades_per_day = max_trades_per_day

        self.target_balance = start_balance * (1.0 + target_pct)
        self.loss_floor = start_balance * (1.0 - loss_limit_pct)

        # Track session quotas
        self._session_trades: dict[str, int] = {}
        self._consecutive_losses = 0
        self._day_start_epoch = self._utc_day_start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reset_day(self, new_start_balance: float) -> None:
        """Called at 00:00 UTC to reset for the new compounding day."""
        self.start_balance = new_start_balance
        self.target_balance = new_start_balance * (1.0 + self.target_pct)
        self.loss_floor = new_start_balance * (1.0 - self.loss_limit_pct)
        self._session_trades = {}
        self._consecutive_losses = 0
        self._day_start_epoch = self._utc_day_start()
        logger.info(
            "DayPlanner RESET: start=$%.2f target=$%.2f floor=$%.2f",
            self.start_balance, self.target_balance, self.loss_floor,
        )

    def record_trade_result(self, pnl_usdt: float) -> None:
        """Called after every trade close to track consecutive losses."""
        if pnl_usdt < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

    def evaluate(
        self,
        current_equity: float,
        day_trades: int = 0,
    ) -> DayPlan:
        """
        Evaluate the current regime, return a DayPlan with pacing directives.
        Called before every potential entry.
        """
        now = datetime.now(timezone.utc)
        utc_hour = now.hour
        utc_minute = now.minute
        now_ts = time.time()

        # --- Time math ---
        seconds_since_midnight = utc_hour * 3600 + utc_minute * 60 + now.second
        time_elapsed_pct = (seconds_since_midnight / 86400.0) * 100.0
        hours_remaining = (86400 - seconds_since_midnight) / 3600.0

        # Remaining kill-zone hours
        kz_hours_left = 0.0
        current_session = ""
        for name, start_h, end_h, _w in KILL_ZONE_WINDOWS:
            if start_h <= utc_hour < end_h:
                current_session = name
                kz_hours_left += (end_h - utc_hour) - (utc_minute / 60.0)
            elif utc_hour < start_h:
                kz_hours_left += (end_h - start_h)
        kz_hours_left = max(0.0, kz_hours_left)

        # --- Progress math ---
        day_pnl = current_equity - self.start_balance
        target_pnl = self.target_balance - self.start_balance
        progress_pct = (day_pnl / target_pnl * 100.0) if target_pnl > 0 else 0.0
        progress_pct = min(progress_pct, 100.0)

        # --- Session quota ---
        session_quota_used = self._session_trades.get(current_session, 0)
        session_quota_max = self.max_trades_per_session

        # --- Regime determination ---
        regime, kelly_mult, conf_floor, lev_cap = self._determine_regime(
            progress_pct=progress_pct,
            current_equity=current_equity,
            time_elapsed_pct=time_elapsed_pct,
            kz_hours_left=kz_hours_left,
            day_trades=day_trades,
            session_quota_used=session_quota_used,
        )

        return DayPlan(
            start_balance=self.start_balance,
            target_balance=self.target_balance,
            loss_floor=self.loss_floor,
            current_equity=current_equity,
            day_pnl_usdt=round(day_pnl, 2),
            progress_pct=round(progress_pct, 1),
            time_elapsed_pct=round(time_elapsed_pct, 1),
            regime=regime,
            kelly_multiplier=kelly_mult,
            confidence_floor=conf_floor,
            max_leverage_cap=lev_cap,
            current_session=current_session,
            session_quota_used=session_quota_used,
            session_quota_max=session_quota_max,
            day_trades=day_trades,
            day_max_trades=self.max_trades_per_day,
            consecutive_losses=self._consecutive_losses,
            hours_remaining=round(hours_remaining, 1),
            kz_hours_remaining=round(kz_hours_left, 1),
        )

    def should_allow_entry(self, plan: DayPlan) -> tuple[bool, str]:
        """
        Final gate: should the engine take this trade?
        Returns (allowed: bool, reason: str).
        """
        if plan.regime == "TARGET_HIT":
            return False, "DAILY_TARGET_REACHED"
        if plan.regime == "DRAWDOWN_HALT":
            return False, "DAILY_LOSS_LIMIT"
        if plan.day_trades >= plan.day_max_trades:
            return False, "DAY_TRADE_LIMIT"
        if plan.session_quota_used >= plan.session_quota_max:
            return False, "SESSION_QUOTA_EXHAUSTED"
        if plan.consecutive_losses >= 3 and plan.regime not in ("BEHIND_LATE",):
            return False, "CONSEC_LOSS_COOLDOWN"
        return True, "ALLOWED"

    def record_session_trade(self, session_name: str) -> None:
        """Increment trade count for the given session."""
        self._session_trades[session_name] = self._session_trades.get(session_name, 0) + 1

    # ------------------------------------------------------------------
    # Regime logic
    # ------------------------------------------------------------------

    def _determine_regime(
        self,
        progress_pct: float,
        current_equity: float,
        time_elapsed_pct: float,
        kz_hours_left: float,
        day_trades: int,
        session_quota_used: int,
    ) -> tuple[str, float, float, int]:
        """
        Returns (regime_name, kelly_multiplier, confidence_floor, max_leverage).
        """
        # Hard circuit breakers
        if current_equity >= self.target_balance:
            return "TARGET_HIT", 0.0, 1.0, 0
        if current_equity <= self.loss_floor:
            return "DRAWDOWN_HALT", 0.0, 1.0, 0

        # Consecutive loss cooldown
        if self._consecutive_losses >= 4:
            return "DRAWDOWN_WARNING", 0.40, 0.70, 10

        if self._consecutive_losses >= 3:
            return "DRAWDOWN_WARNING", 0.50, 0.65, 15

        # Near-target de-risking
        if progress_pct >= 90.0:
            return "ALMOST_THERE", 0.50, 0.65, 10

        if progress_pct >= 60.0:
            return "AHEAD", 0.75, 0.60, 15

        # Behind schedule — time pressure
        expected_progress = time_elapsed_pct  # 1:1 mapping (50% time = 50% progress expected)
        deficit = expected_progress - progress_pct

        if deficit > 30.0 and time_elapsed_pct > 70.0:
            # Very behind, late in the day
            return "BEHIND_LATE", 1.25, 0.55, 20

        if deficit > 20.0 and time_elapsed_pct > 40.0:
            return "BEHIND_EARLY", 1.10, 0.58, 20

        # Normal
        return "NORMAL", 1.0, 0.60, 20

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _utc_day_start() -> float:
        """Epoch timestamp of today's 00:00 UTC."""
        now = time.time()
        return (int(now) // 86400) * 86400

    def to_dict(self, plan: DayPlan) -> dict:
        """Serialize for hft.json telemetry."""
        return {
            "start_balance": round(plan.start_balance, 2),
            "target_balance": round(plan.target_balance, 2),
            "loss_floor": round(plan.loss_floor, 2),
            "current_equity": round(plan.current_equity, 2),
            "day_pnl_usdt": plan.day_pnl_usdt,
            "progress_pct": plan.progress_pct,
            "time_elapsed_pct": plan.time_elapsed_pct,
            "hours_remaining": plan.hours_remaining,
            "kz_hours_remaining": plan.kz_hours_remaining,
            "regime": plan.regime,
            "kelly_multiplier": plan.kelly_multiplier,
            "confidence_floor": plan.confidence_floor,
            "max_leverage_cap": plan.max_leverage_cap,
            "current_session": plan.current_session,
            "session_quota": f"{plan.session_quota_used}/{plan.session_quota_max}",
            "day_trades": f"{plan.day_trades}/{plan.day_max_trades}",
            "consecutive_losses": plan.consecutive_losses,
            "halted": plan.regime in ("TARGET_HIT", "DRAWDOWN_HALT"),
        }
