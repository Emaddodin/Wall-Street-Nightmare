"""
replay_scheduler.py
===================
Scheduling engine to wake up and start replay execution at exactly 00:00:00 UTC.

Tehran Time Alignment:
UTC 00:00:00 corresponds to 03:30:00 AM Tehran Time (UTC+03:30).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

logger = logging.getLogger("replay_scheduler")


def get_target_midnight_utc(now: Optional[datetime] = None) -> datetime:
    """
    Computes the upcoming 00:00:00 UTC datetime.
    If 'now' is already exactly midnight UTC, returns 'now'.
    Otherwise, returns the midnight of the next UTC calendar day.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    # If it is exactly midnight 00:00:00.000000, we are at the target
    if current.hour == 0 and current.minute == 0 and current.second == 0 and current.microsecond == 0:
        return current

    # Midnight of next day
    tomorrow = current.date() + timedelta(days=1)
    target = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 0, 0, tzinfo=timezone.utc)
    return target


def get_seconds_until_midnight_utc(now: Optional[datetime] = None) -> float:
    """
    Returns the exact number of seconds remaining until the upcoming 00:00:00 UTC.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    target = get_target_midnight_utc(current)
    remaining = (target - current).total_seconds()
    return max(0.0, remaining)


async def wait_until_midnight_utc(
    heartbeat_interval: float = 60.0,
    on_tick: Optional[Callable[[float, str], None]] = None,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """
    Asynchronously sleeps until 00:00:00 UTC.
    Uses precise non-drifting delta calculations and emits countdown progress.
    """
    target = get_target_midnight_utc()
    tehran_offset = timedelta(hours=3, minutes=30)
    tehran_target = target + tehran_offset

    logger.info(
        "Scheduler armed. Waiting until 00:00:00 UTC (%s Tehran time)...",
        tehran_target.strftime("%Y-%m-%d %H:%M:%S"),
    )

    while True:
        if stop_event is not None and stop_event.is_set():
            logger.info("Scheduler wait cancelled by stop event.")
            return

        remaining = get_seconds_until_midnight_utc()
        if remaining <= 0.05:
            logger.info("00:00:00 UTC REACHED! Replay trigger firing now!")
            break

        hours = int(remaining // 3600)
        minutes = int((remaining % 3600) // 60)
        seconds = int(remaining % 60)
        time_str = f"{hours:02d}h {minutes:02d}m {seconds:02d}s"

        if on_tick is not None:
            try:
                on_tick(remaining, time_str)
            except Exception as exc:
                logger.error("Error in scheduler on_tick callback: %s", exc)

        if int(remaining) % 60 == 0 or remaining <= 10.0:
            logger.info("Replay countdown: %s remaining until 00:00:00 UTC", time_str)

        # Sleep up to heartbeat_interval, or remaining if smaller
        sleep_dur = min(heartbeat_interval, max(0.01, remaining - 0.05))
        await asyncio.sleep(sleep_dur)
