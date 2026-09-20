"""
engine/killzone.py
===========================
ICT (Inner Circle Trader) Institutional Kill Zone Engine & Multi-Timezone Scheduler.

Kill zones are institutional high-probability trading windows where smart money
and central bank bullion fixing orders consistently trigger significant price displacement.
Outside of these windows, the engine stands down and preserves capital.

Empirical Gold (XAUUSD / Hyperliquid GOLD) Kill Zone Windows (UTC):
  ┌──────────────────────────────────────────────────────────────────────────────────┐
  │  ZONE                    UTC WINDOW      CENTERS & CHARACTERISTICS               │
  │  ─────────────────────────────────────────────────────────────────────────────── │
  │  London Open (LBMA AM)   07:00 – 10:30   London LBMA Fix (10:30), Peak Momentum  │
  │  New York AM / COMEX     12:00 – 16:30   COMEX Pit (08:20 NY), US Macro (08:30), │
  │                                          LBMA PM Fix (15:00 Lon), Highest Volume │
  │  London Close            15:00 – 17:00   London Bank Settlement, Overlap Sweeps  │
  │  Asian Open (SGE/TOCOM)  00:00 – 03:30   Shanghai/Tokyo Open, Range Accumulation │
  └──────────────────────────────────────────────────────────────────────────────────┘

Multi-Timezone Architecture:
  All state evaluations are strictly normalized to UTC epoch timestamps, eliminating
  any dependency on the host operating system's local clock or timezone settings.
  Comprehensive multi-timezone schedule converters map all institutional windows to
  UTC, US/Eastern (New York), Europe/London (London), Asia/Tokyo, Asia/Shanghai,
  Asia/Tehran (UTC+3:30), and Asia/Dubai with automatic Daylight Saving Time (DST)
  awareness via Python's zoneinfo.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

logger = logging.getLogger("killzone")


@dataclass(frozen=True)
class KillZone:
    name: str
    start_hour: int   # inclusive, UTC (0-23)
    start_min: int    # inclusive, UTC (0-59)
    end_hour: int     # exclusive, UTC (0-23)
    end_min: int      # exclusive, UTC (0-59)
    emoji: str
    priority: int     # 1=highest priority, 4=lowest
    description: str = ""


# -------------------------------------------------------------------------
# Institutional Gold (XAUUSD) Kill Zones
# -------------------------------------------------------------------------

XAUUSD_GOLD_KILLZONES: Tuple[KillZone, ...] = (
    KillZone(
        name="London Open (LBMA AM)",
        start_hour=7,
        start_min=0,
        end_hour=10,
        end_min=30,
        emoji="🇬🇧",
        priority=1,
        description="LBMA physical bullion market open & AM Gold Fix (10:30 London). Highest trend momentum.",
    ),
    KillZone(
        name="New York AM / COMEX",
        start_hour=12,
        start_min=0,
        end_hour=16,
        end_min=30,
        emoji="🗽",
        priority=1,
        description="COMEX futures open, US macro data releases (08:30 NY), LBMA PM Fix. Peak global volume.",
    ),
    KillZone(
        name="London Close",
        start_hour=15,
        start_min=0,
        end_hour=17,
        end_min=0,
        emoji="🔄",
        priority=2,
        description="European bank book squaring and London market close. Session trend extensions or reversals.",
    ),
    KillZone(
        name="Asian Open (SGE / TOCOM)",
        start_hour=0,
        start_min=0,
        end_hour=3,
        end_min=30,
        emoji="🌏",
        priority=3,
        description="Shanghai Gold Exchange (01:00 UTC) & Tokyo open. Asian range accumulation & sweep.",
    ),
)

# Peak Momentum Gold Kill Zones (Default for 5M Scalper)
XAUUSD_PEAK_KILLZONES: Tuple[KillZone, ...] = (
    XAUUSD_GOLD_KILLZONES[0],  # London Open
    XAUUSD_GOLD_KILLZONES[1],  # New York AM / COMEX
)

# Backwards-compatible alias for existing modules
XAUUSD_KILLZONES = XAUUSD_PEAK_KILLZONES

# Legacy BTC Kill Zones for backwards compatibility
KILL_ZONES: Tuple[KillZone, ...] = (
    KillZone("Asian Open",      0,  0,  2,  0, "🌏", 4, "Asian session liquidity grab"),
    KillZone("London Open",     2,  0,  5,  0, "🇬🇧", 1, "London momentum volume"),
    KillZone("NY Open",         7,  0, 10,  0, "🗽", 1, "New York opening trend"),
    KillZone("London Close",   11,  0, 13,  0, "🔄", 2, "NY/London overlap close"),
    KillZone("NY Afternoon",   14,  0, 16,  0, "📈", 3, "New York afternoon sweep"),
)


# -------------------------------------------------------------------------
# Multi-Timezone Constants & Registry
# -------------------------------------------------------------------------

FINANCIAL_TIMEZONES: Dict[str, str] = {
    "UTC": "UTC",
    "New York (COMEX/ICT)": "America/New_York",
    "London (LBMA)": "Europe/London",
    "Tokyo (TOCOM)": "Asia/Tokyo",
    "Shanghai (SGE)": "Asia/Shanghai",
    "Tehran (Local)": "Asia/Tehran",
    "Dubai (Gold Souk)": "Asia/Dubai",
}


def convert_utc_to_tz(
    utc_hour: int,
    utc_min: int,
    target_tz_name: str,
    ref_ts: Optional[float] = None,
) -> Tuple[int, int, str]:
    """
    Converts a UTC time (hour, minute) to target timezone with DST awareness.
    Returns: (target_hour, target_min, tz_abbrev).
    """
    now_ts = ref_ts if ref_ts is not None else time.time()
    ref_date = datetime.fromtimestamp(now_ts, tz=timezone.utc)
    utc_dt = datetime(
        year=ref_date.year,
        month=ref_date.month,
        day=ref_date.day,
        hour=utc_hour,
        minute=utc_min,
        tzinfo=timezone.utc,
    )
    try:
        tz = ZoneInfo(target_tz_name)
        target_dt = utc_dt.astimezone(tz)
        abbrev = target_dt.strftime("%Z")
        return target_dt.hour, target_dt.minute, abbrev
    except Exception:
        # Fallback to UTC if timezone lookup fails
        return utc_hour, utc_min, "UTC"


def get_current_financial_clocks(ts: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
    """
    Returns the live formatted time, timezone abbreviation, and UTC offset
    for all key institutional financial trading hubs.
    """
    now_ts = ts if ts is not None else time.time()
    utc_now = datetime.fromtimestamp(now_ts, tz=timezone.utc)

    clocks: Dict[str, Dict[str, Any]] = {}
    for label, tz_identifier in FINANCIAL_TIMEZONES.items():
        try:
            tz = ZoneInfo(tz_identifier)
            local_dt = utc_now.astimezone(tz)
            clocks[label] = {
                "tz": tz_identifier,
                "time_str": local_dt.strftime("%H:%M:%S"),
                "date_str": local_dt.strftime("%Y-%m-%d"),
                "hour": local_dt.hour,
                "minute": local_dt.minute,
                "abbrev": local_dt.strftime("%Z"),
                "utc_offset": local_dt.strftime("%z"),
            }
        except Exception:
            clocks[label] = {
                "tz": tz_identifier,
                "time_str": utc_now.strftime("%H:%M:%S"),
                "date_str": utc_now.strftime("%Y-%m-%d"),
                "hour": utc_now.hour,
                "minute": utc_now.minute,
                "abbrev": "UTC",
                "utc_offset": "+0000",
            }
    return clocks


# -------------------------------------------------------------------------
# KillZoneGuard Implementation
# -------------------------------------------------------------------------

class KillZoneGuard:
    """
    Evaluates whether the current UTC time falls inside an active institutional kill zone.
    Guarantees strict UTC normalization, cross-midnight support, and provides
    multi-timezone reporting for mobile and monitoring dashboards.
    """

    def __init__(self, zones: Tuple[KillZone, ...] = XAUUSD_PEAK_KILLZONES) -> None:
        self._zones = zones
        self._current_zone: Optional[KillZone] = None
        self._zone_entry_ts: float = 0.0

    @property
    def zones(self) -> Tuple[KillZone, ...]:
        return self._zones

    def check(self, ts: Optional[float] = None) -> Tuple[bool, str]:
        """
        Evaluates whether the given timestamp falls within an active kill zone.
        Strictly normalizes ts to UTC epoch.
        Returns: (in_kill_zone: bool, zone_name: str).
        """
        now_ts = ts if ts is not None else time.time()
        utc_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc)
        hour = utc_dt.hour
        minute = utc_dt.minute

        for kz in self._zones:
            if self._in_zone(hour, minute, kz):
                if self._current_zone is None or self._current_zone.name != kz.name:
                    self._current_zone = kz
                    self._zone_entry_ts = now_ts
                return True, kz.name

        if self._current_zone is not None:
            self._current_zone = None
        return False, ""

    def current_zone(self, ts: Optional[float] = None) -> Optional[KillZone]:
        """Returns the currently active KillZone object, or None if outside all windows."""
        in_kz, _ = self.check(ts)
        return self._current_zone if in_kz else None

    def minutes_until_next(self, ts: Optional[float] = None) -> Tuple[int, str]:
        """
        Returns (minutes_until_next_killzone, next_zone_name).
        Returns (0, active_zone_name) if currently inside an active zone.
        """
        now_ts = ts if ts is not None else time.time()
        utc_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc)
        now_min = utc_dt.hour * 60 + utc_dt.minute

        in_kz, name = self.check(ts=now_ts)
        if in_kz:
            return 0, name

        best_wait = 24 * 60
        best_name = ""
        for kz in self._zones:
            zone_start_min = kz.start_hour * 60 + kz.start_min
            delta = zone_start_min - now_min
            if delta < 0:
                delta += 24 * 60  # Next calendar day
            if delta < best_wait:
                best_wait = delta
                best_name = kz.name

        return best_wait, best_name

    def minutes_remaining_in_current(self, ts: Optional[float] = None) -> int:
        """Returns minutes remaining in currently active kill zone, or 0 if inactive."""
        now_ts = ts if ts is not None else time.time()
        kz = self.current_zone(ts=now_ts)
        if kz is None:
            return 0

        utc_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc)
        now_min = utc_dt.hour * 60 + utc_dt.minute
        end_min = kz.end_hour * 60 + kz.end_min

        if end_min <= kz.start_hour * 60 + kz.start_min:
            # Cross-midnight window
            if now_min >= kz.start_hour * 60 + kz.start_min:
                end_min += 24 * 60
        elif end_min < now_min:
            end_min += 24 * 60

        return max(0, end_min - now_min)

    def zone_label(self, ts: Optional[float] = None) -> str:
        """Human-readable status label for UI and telemetry."""
        now_ts = ts if ts is not None else time.time()
        kz = self.current_zone(ts=now_ts)
        if kz is None:
            mins, nxt = self.minutes_until_next(ts=now_ts)
            return f"⏳ Off-Window (next: {nxt} in {mins}m)"

        mins_left = self.minutes_remaining_in_current(ts=now_ts)
        end_str = f"{kz.end_hour:02d}:{kz.end_min:02d} UTC"
        return f"{kz.emoji} {kz.name} Active ({mins_left}m left / Ends {end_str})"

    def all_zones_summary(
        self,
        ts: Optional[float] = None,
        target_tz: str = "UTC",
    ) -> List[Dict[str, Any]]:
        """
        Returns all configured kill zones with their active status, time remaining,
        and localized start/end windows for a target timezone.
        """
        now_ts = ts if ts is not None else time.time()
        utc_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc)
        hour = utc_dt.hour
        minute = utc_dt.minute
        now_min = hour * 60 + minute

        result = []
        for kz in self._zones:
            active = self._in_zone(hour, minute, kz)
            countdown = ""
            if active:
                mins_left = self.minutes_remaining_in_current(ts=now_ts)
                countdown = f"{mins_left}m left"
            else:
                zone_start_min = kz.start_hour * 60 + kz.start_min
                delta = zone_start_min - now_min
                if delta < 0:
                    delta += 24 * 60
                countdown = f"in {delta}m"

            # Format window in target timezone
            st_h, st_m, abbrev = convert_utc_to_tz(kz.start_hour, kz.start_min, target_tz, now_ts)
            en_h, en_m, _ = convert_utc_to_tz(kz.end_hour, kz.end_min, target_tz, now_ts)
            local_window = f"{st_h:02d}:{st_m:02d} – {en_h:02d}:{en_m:02d} {abbrev}"
            utc_window = f"{kz.start_hour:02d}:{kz.start_min:02d} – {kz.end_hour:02d}:{kz.end_min:02d} UTC"

            result.append({
                "name": kz.name,
                "emoji": kz.emoji,
                "utc_window": utc_window,
                "local_window": local_window,
                "target_tz": target_tz,
                "active": active,
                "countdown": countdown,
                "priority": kz.priority,
                "description": kz.description,
            })
        return result

    def multitz_dashboard(self, ts: Optional[float] = None) -> Dict[str, Any]:
        """
        Comprehensive multi-timezone diagnostic payload for mobile dashboard and API.
        Includes live institutional clocks, active zone status, and localized zone tables.
        """
        now_ts = ts if ts is not None else time.time()
        clocks = get_current_financial_clocks(now_ts)
        in_kz, active_name = self.check(now_ts)
        mins_until, next_name = self.minutes_until_next(now_ts)
        mins_remaining = self.minutes_remaining_in_current(now_ts)

        zone_rows = []
        for kz in self._zones:
            active = self._in_zone(
                datetime.fromtimestamp(now_ts, tz=timezone.utc).hour,
                datetime.fromtimestamp(now_ts, tz=timezone.utc).minute,
                kz,
            )

            # Localized times for major hubs
            ny_s_h, ny_s_m, _ = convert_utc_to_tz(kz.start_hour, kz.start_min, "America/New_York", now_ts)
            ny_e_h, ny_e_m, _ = convert_utc_to_tz(kz.end_hour, kz.end_min, "America/New_York", now_ts)

            lon_s_h, lon_s_m, _ = convert_utc_to_tz(kz.start_hour, kz.start_min, "Europe/London", now_ts)
            lon_e_h, lon_e_m, _ = convert_utc_to_tz(kz.end_hour, kz.end_min, "Europe/London", now_ts)

            teh_s_h, teh_s_m, _ = convert_utc_to_tz(kz.start_hour, kz.start_min, "Asia/Tehran", now_ts)
            teh_e_h, teh_e_m, _ = convert_utc_to_tz(kz.end_hour, kz.end_min, "Asia/Tehran", now_ts)

            zone_rows.append({
                "name": kz.name,
                "emoji": kz.emoji,
                "priority": kz.priority,
                "active": active,
                "utc": f"{kz.start_hour:02d}:{kz.start_min:02d}–{kz.end_hour:02d}:{kz.end_min:02d}",
                "new_york": f"{ny_s_h:02d}:{ny_s_m:02d}–{ny_e_h:02d}:{ny_e_m:02d}",
                "london": f"{lon_s_h:02d}:{lon_s_m:02d}–{lon_e_h:02d}:{lon_e_m:02d}",
                "tehran": f"{teh_s_h:02d}:{teh_s_m:02d}–{teh_e_h:02d}:{teh_e_m:02d}",
                "description": kz.description,
            })

        return {
            "in_killzone": in_kz,
            "active_zone": active_name,
            "minutes_remaining": mins_remaining,
            "next_zone": next_name,
            "minutes_until_next": mins_until,
            "label": self.zone_label(now_ts),
            "clocks": clocks,
            "schedule": zone_rows,
        }

    # ------------------------------------------------------------------
    # Private Helper
    # ------------------------------------------------------------------

    @staticmethod
    def _in_zone(hour: int, minute: int, kz: KillZone) -> bool:
        now_min = hour * 60 + minute
        start_min = kz.start_hour * 60 + kz.start_min
        end_min = kz.end_hour * 60 + kz.end_min

        if start_min <= end_min:
            # Standard single-day window (e.g., 07:00 to 10:30)
            return start_min <= now_min < end_min
        else:
            # Cross-midnight window (e.g., 22:00 to 02:00)
            return now_min >= start_min or now_min < end_min
