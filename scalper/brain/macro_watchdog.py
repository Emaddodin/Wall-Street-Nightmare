"""
scalper/brain/macro_watchdog.py
================================
Real-Time Macro & News Watchdog for Gold (XAUUSD).
Surveils high-impact economic calendar events (CPI, NFP, FOMC, PPI, Jobless Claims)
and live breaking headlines, providing Laya-evaluated risk scores to protect
scalp equity from news-driven spread widening and whipsaws.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("macro_watchdog")


@dataclass
class MacroEvent:
    name: str
    impact: str  # "HIGH", "MEDIUM", "LOW"
    currency: str  # "USD"
    scheduled_epoch: float  # Unix timestamp
    forecast: Optional[str] = None
    previous: Optional[str] = None


class MacroWatchdog:
    """
    Monitors economic schedule and evaluates news headlines for XAUUSD impact.
    """

    def __init__(self):
        self._events: List[MacroEvent] = []
        self._last_news_assessment: Dict[str, Any] = {
            "status": "SAFE",
            "volatility_score": 1.5,
            "usd_bias": "neutral",
            "active_alert": "No high-impact news in window",
            "updated_at": time.time(),
        }

    def register_scheduled_event(
        self, name: str, scheduled_time: datetime, impact: str = "HIGH", currency: str = "USD"
    ) -> None:
        """Registers a known calendar event."""
        epoch = scheduled_time.replace(tzinfo=timezone.utc).timestamp()
        self._events.append(
            MacroEvent(name=name, impact=impact.upper(), currency=currency.upper(), scheduled_epoch=epoch)
        )
        self._events.sort(key=lambda x: x.scheduled_epoch)

    def get_upcoming_event(self, window_minutes: int = 30) -> Optional[Tuple[MacroEvent, float]]:
        """
        Returns (event, minutes_until) if an event falls within window_minutes.
        Can be negative if event occurred in last window_minutes.
        """
        now = time.time()
        for ev in self._events:
            diff_min = (ev.scheduled_epoch - now) / 60.0
            if -10.0 <= diff_min <= window_minutes:
                return (ev, diff_min)
        return None

    def is_entry_allowed(self) -> Tuple[bool, str]:
        """
        Returns False if we are within 5 minutes before or 5 minutes after a HIGH impact USD event.
        """
        upcoming = self.get_upcoming_event(window_minutes=5)
        if upcoming:
            ev, diff_min = upcoming
            if ev.impact == "HIGH":
                if diff_min > 0:
                    return False, f"FREEZE: {ev.name} in {diff_min:.1f}m (High Volatility Risk)"
                else:
                    return False, f"FREEZE: {ev.name} released {abs(diff_min):.1f}m ago (High Volatility Cooling)"

        # Check last news assessment
        if self._last_news_assessment.get("status") == "HALT_NEW_ENTRIES":
            return False, f"FREEZE: Breaking Headline Risk ({self._last_news_assessment.get('active_alert')})"

        return True, "SAFE"

    def assess_headline_heuristic(self, headline: str) -> Dict[str, Any]:
        """
        Fast heuristic evaluation of breaking market headline.
        """
        text = headline.lower()
        vol_score = 2.0
        usd_bias = "neutral"
        status = "SAFE"

        high_impact_keywords = ["cpi", "inflation", "fomc", "fed rate", "powell", "war", "missile", "nfp", "payroll"]
        bullish_usd_keywords = ["cpi hotter", "inflation rises", "hike rates", "strong jobs", "hawkish"]
        bearish_usd_keywords = ["cpi cools", "inflation falls", "cut rates", "weak jobs", "dovish", "geopolitical tension", "safe haven"]

        if any(k in text for k in high_impact_keywords):
            vol_score = 8.5
            status = "CAUTION"

        if any(k in text for k in bullish_usd_keywords):
            usd_bias = "bullish_usd_gold_bear"
            vol_score = 9.0
            status = "HALT_NEW_ENTRIES"
        elif any(k in text for k in bearish_usd_keywords):
            usd_bias = "bearish_usd_gold_bull"
            vol_score = 9.0
            status = "HALT_NEW_ENTRIES"

        result = {
            "headline": headline,
            "status": status,
            "volatility_score": vol_score,
            "usd_bias": usd_bias,
            "active_alert": headline if vol_score >= 7.0 else "Normal baseline",
            "updated_at": time.time(),
        }
        self._last_news_assessment = result
        return result

    def get_telemetry(self) -> Dict[str, Any]:
        """Telemetry dict for dashboard and logging."""
        allowed, reason = self.is_entry_allowed()
        upcoming = self.get_upcoming_event(window_minutes=180)

        next_event_str = "None in next 3 hours"
        if upcoming:
            ev, diff_min = upcoming
            next_event_str = f"{ev.name} ({ev.impact}) in {diff_min:.0f}m"

        return {
            "allowed": allowed,
            "reason": reason,
            "next_event": next_event_str,
            "status": self._last_news_assessment.get("status", "SAFE"),
            "volatility_score": self._last_news_assessment.get("volatility_score", 1.5),
            "usd_bias": self._last_news_assessment.get("usd_bias", "neutral"),
            "active_alert": self._last_news_assessment.get("active_alert", "Safe"),
        }


# Singleton
_watchdog_instance: Optional[MacroWatchdog] = None


def get_macro_watchdog() -> MacroWatchdog:
    global _watchdog_instance
    if _watchdog_instance is None:
        _watchdog_instance = MacroWatchdog()
    return _watchdog_instance
