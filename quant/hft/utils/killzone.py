"""
quant/hft/utils/killzone.py
===========================
ICT (Inner Circle Trader) Kill Zone time filter.

Kill zones are institutional high-probability trading windows where smart money
orders consistently cause significant price displacement. Outside of these
windows, the engine waits and preserves capital.

BTC/USDT-PERP Kill Zone Windows (UTC):
  ┌─────────────────────────────────────────────────────────────┐
  │  ZONE               UTC WINDOW      CHARACTERISTIC          │
  │  ─────────────────────────────────────────────────────────  │
  │  Asian Open KZ      00:00 – 02:00   Low vol, liquidity grab │
  │  London Open KZ     02:00 – 05:00   Highest momentum vol    │
  │  NY Open KZ         07:00 – 10:00   Highest volume + trend  │
  │  London Close KZ    11:00 – 13:00   NY/London overlap       │
  │  NY Afternoon KZ    14:00 – 16:00   NY afternoon sweep      │
  └─────────────────────────────────────────────────────────────┘

Outside these windows the engine logs and skips entries (positions already
open continue to be managed normally).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class KillZone:
    name: str
    start_hour: int   # inclusive, UTC
    start_min: int
    end_hour: int     # exclusive, UTC
    end_min: int
    emoji: str
    priority: int     # 1=highest, 5=lowest


# ICT Kill Zones for BTC-PERP on Hyperliquid
KILL_ZONES: tuple[KillZone, ...] = (
    KillZone("Asian Open",      0,  0,  2,  0, "🌏", 4),
    KillZone("London Open",     2,  0,  5,  0, "🇬🇧", 1),
    KillZone("NY Open",         7,  0, 10,  0, "🗽", 1),
    KillZone("London Close",   11,  0, 13,  0, "🔄", 2),
    KillZone("NY Afternoon",   14,  0, 16,  0, "📈", 3),
)


class KillZoneGuard:
    """
    Evaluates whether the current UTC time falls inside an ICT kill zone.

    Usage
    -----
    guard = KillZoneGuard()
    in_kz, kz_name = guard.check()
    if not in_kz:
        flow_auditor.record_outside_killzone()
        return
    """

    def __init__(self, zones: tuple[KillZone, ...] = KILL_ZONES) -> None:
        self._zones = zones
        self._current_zone: Optional[KillZone] = None
        self._zone_entry_ts: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, ts: Optional[float] = None) -> tuple[bool, str]:
        """
        Returns (in_kill_zone: bool, zone_name: str).
        zone_name is empty string when outside all zones.
        """
        utc_dt = datetime.fromtimestamp(ts or time.time(), tz=timezone.utc)
        hour = utc_dt.hour
        minute = utc_dt.minute

        for kz in self._zones:
            if self._in_zone(hour, minute, kz):
                if self._current_zone is None or self._current_zone.name != kz.name:
                    self._current_zone = kz
                    self._zone_entry_ts = time.time()
                return True, kz.name

        if self._current_zone is not None:
            self._current_zone = None
        return False, ""

    def current_zone(self) -> Optional[KillZone]:
        """Returns the currently active KillZone object, or None."""
        in_kz, _ = self.check()
        return self._current_zone if in_kz else None

    def minutes_until_next(self, ts: Optional[float] = None) -> tuple[int, str]:
        """
        Returns (minutes_until_next_killzone, next_zone_name).
        0 minutes means we are already inside one.
        """
        utc_dt = datetime.fromtimestamp(ts or time.time(), tz=timezone.utc)
        now_min = utc_dt.hour * 60 + utc_dt.minute

        in_kz, name = self.check(ts)
        if in_kz:
            return 0, name

        # Find nearest upcoming zone (within next 24h)
        best_wait = 24 * 60
        best_name = ""
        for kz in self._zones:
            zone_start_min = kz.start_hour * 60 + kz.start_min
            delta = zone_start_min - now_min
            if delta < 0:
                delta += 24 * 60  # next day
            if delta < best_wait:
                best_wait = delta
                best_name = kz.name
        return best_wait, best_name

    def zone_label(self) -> str:
        """Human-readable label for UI/Ntfy. Returns 'Outside Kill Zones' if none active."""
        kz = self.current_zone()
        if kz is None:
            mins, nxt = self.minutes_until_next()
            return f"⏳ Off-Window (next: {nxt} in {mins}m)"
        
        utc_dt = datetime.fromtimestamp(time.time(), tz=timezone.utc)
        now_min = utc_dt.hour * 60 + utc_dt.minute
        end_min = kz.end_hour * 60 + kz.end_min
        if end_min < now_min:
            end_min += 24 * 60
        mins_left = end_min - now_min
        end_str = f"{kz.end_hour:02d}:{kz.end_min:02d} UTC"
        return f"{kz.emoji} {kz.name} Kill Zone (Ends: {end_str} / {mins_left}m left)"

    def all_zones_summary(self) -> list[dict]:
        """Returns all zones as list of dicts for UI display."""
        utc_dt = datetime.fromtimestamp(time.time(), tz=timezone.utc)
        hour = utc_dt.hour
        minute = utc_dt.minute
        now_min = hour * 60 + minute
        
        result = []
        for kz in self._zones:
            active = self._in_zone(hour, minute, kz)
            countdown = ""
            if active:
                end_min = kz.end_hour * 60 + kz.end_min
                if end_min < now_min:
                    end_min += 24 * 60
                mins_left = end_min - now_min
                countdown = f"{mins_left}m left"
                
            result.append({
                "name": kz.name,
                "emoji": kz.emoji,
                "window": f"{kz.start_hour:02d}:{kz.start_min:02d} – {kz.end_hour:02d}:{kz.end_min:02d} UTC",
                "active": active,
                "countdown": countdown,
                "priority": kz.priority,
            })
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _in_zone(hour: int, minute: int, kz: KillZone) -> bool:
        now_min = hour * 60 + minute
        start_min = kz.start_hour * 60 + kz.start_min
        end_min = kz.end_hour * 60 + kz.end_min
        return start_min <= now_min < end_min
