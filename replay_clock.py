"""
replay_clock.py
===============
High-Precision Clock Spoofing and Time Machine for Market Replay.

Allows HyperPredatorBot and simulated components to operate under the illusion
of running at an arbitrary historical point in time (e.g., Friday 2026-09-18 00:00:00 UTC),
advancing forward in lockstep with simulated market ticks at a configurable speed multiplier.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional


class ReplayClock:
    """
    Clock engine that translates real wall-clock elapsed time into simulated time
    scaled by a speed multiplier.
    """

    def __init__(
        self,
        simulated_start_ts: float,
        speed: float = 1.0,
    ) -> None:
        """
        :param simulated_start_ts: Unix epoch timestamp in seconds for simulated T0.
        :param speed: Replay multiplier (1.0 = 1x real-time, 10.0 = 10x faster, etc.).
        """
        self.simulated_start_ts = float(simulated_start_ts)
        self.speed = max(0.0001, float(speed))
        self._wall_start_mono: Optional[float] = None
        self._manual_override_ts: Optional[float] = None
        self._is_running = False
        self._orig_bot_time: Optional[Callable[[], float]] = None

    def start(self) -> None:
        """Start or resume monotonic time tracking."""
        self._wall_start_mono = time.monotonic()
        self._is_running = True

    def stop(self) -> None:
        """Freeze current simulated time."""
        if self._is_running and self._wall_start_mono is not None:
            self._manual_override_ts = self.time()
        self._is_running = False

    def set_time(self, ts: float) -> None:
        """Manually override the current simulated time."""
        self.simulated_start_ts = float(ts)
        self._wall_start_mono = time.monotonic()
        self._manual_override_ts = None

    def time(self) -> float:
        """Returns the current simulated Unix epoch timestamp in seconds."""
        if self._manual_override_ts is not None:
            return self._manual_override_ts
        if not self._is_running or self._wall_start_mono is None:
            return self.simulated_start_ts
        elapsed_wall = time.monotonic() - self._wall_start_mono
        return self.simulated_start_ts + (elapsed_wall * self.speed)

    def now_utc(self) -> datetime:
        """Returns current simulated datetime with UTC timezone."""
        return datetime.fromtimestamp(self.time(), tz=timezone.utc)

    def isoformat(self) -> str:
        """Returns ISO-8601 formatted simulated UTC time string."""
        return self.now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")

    async def sleep(self, simulated_seconds: float) -> None:
        """
        Asynchronously sleep for a duration in simulated time.
        Converts simulated seconds to actual wall-clock seconds based on speed.
        """
        if self.speed <= 0 or simulated_seconds <= 0:
            await asyncio.sleep(0)
            return
        wall_seconds = simulated_seconds / self.speed
        await asyncio.sleep(wall_seconds)

    def patch_hyper_predator_bot(self, bot_module: Any) -> None:
        """
        Patches time.time() within hyper_predator_bot to use this ReplayClock.
        """
        if hasattr(bot_module, "time"):
            self._orig_bot_time = bot_module.time.time
            bot_module.time.time = self.time

    def unpatch_hyper_predator_bot(self, bot_module: Any) -> None:
        """
        Restores original time.time in hyper_predator_bot.
        """
        if hasattr(bot_module, "time") and self._orig_bot_time is not None:
            bot_module.time.time = self._orig_bot_time
            self._orig_bot_time = None
