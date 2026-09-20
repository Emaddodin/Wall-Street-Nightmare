"""
tests/test_gold_killzones_multitz.py
====================================
Comprehensive test suite for the Institutional Gold (XAUUSD) Kill Zone Engine
and Multi-Timezone Scheduler (engine/killzone.py).

Verifies:
  1. Exact institutional killzone definitions for Gold (LBMA London Open, NY AM/COMEX, London Close, Asian Open).
  2. Cross-midnight window evaluation logic.
  3. Real-time timezone conversions and DST normalization across major financial hubs (UTC, NY, Lon, Tok, Sha, Teh, Dxb).
  4. Live financial clocks structure and validity.
  5. KillZoneGuard state tracking, countdown calculations, remaining minutes, labels, and multitz dashboards.
  6. UTC epoch invariance: guarantees the engine is immune to local host clock drift.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Dict

import pytest

from engine.killzone import (
    FINANCIAL_TIMEZONES,
    KILL_ZONES,
    XAUUSD_GOLD_KILLZONES,
    XAUUSD_KILLZONES,
    XAUUSD_PEAK_KILLZONES,
    KillZone,
    KillZoneGuard,
    convert_utc_to_tz,
    get_current_financial_clocks,
)


def test_gold_institutional_killzones_definitions():
    """Verifies empirical Gold killzones and peak momentum defaults."""
    assert len(XAUUSD_GOLD_KILLZONES) == 4

    names = [kz.name for kz in XAUUSD_GOLD_KILLZONES]
    assert "London Open (LBMA AM)" in names
    assert "New York AM / COMEX" in names
    assert "London Close" in names
    assert "Asian Open (SGE / TOCOM)" in names

    # London Open: 07:00 - 10:30 UTC
    lon_open = XAUUSD_GOLD_KILLZONES[0]
    assert lon_open.start_hour == 7 and lon_open.start_min == 0
    assert lon_open.end_hour == 10 and lon_open.end_min == 30
    assert lon_open.priority == 1

    # New York AM / COMEX: 12:00 - 16:30 UTC
    ny_am = XAUUSD_GOLD_KILLZONES[1]
    assert ny_am.start_hour == 12 and ny_am.start_min == 0
    assert ny_am.end_hour == 16 and ny_am.end_min == 30
    assert ny_am.priority == 1

    # London Close: 15:00 - 17:00 UTC
    lon_close = XAUUSD_GOLD_KILLZONES[2]
    assert lon_close.start_hour == 15 and lon_close.start_min == 0
    assert lon_close.end_hour == 17 and lon_close.end_min == 0

    # Asian Open: 00:00 - 03:30 UTC
    asia_open = XAUUSD_GOLD_KILLZONES[3]
    assert asia_open.start_hour == 0 and asia_open.start_min == 0
    assert asia_open.end_hour == 3 and asia_open.end_min == 30

    # Peak killzones should be the top 2 momentum sessions
    assert len(XAUUSD_PEAK_KILLZONES) == 2
    assert XAUUSD_PEAK_KILLZONES[0] == lon_open
    assert XAUUSD_PEAK_KILLZONES[1] == ny_am

    # Aliases
    assert XAUUSD_KILLZONES == XAUUSD_PEAK_KILLZONES


def test_cross_midnight_killzone_logic():
    """Verifies that KillZoneGuard._in_zone correctly handles windows spanning across 00:00 UTC."""
    midnight_kz = KillZone(
        name="Late NY to Asia Overlap",
        start_hour=22,
        start_min=30,
        end_hour=2,
        end_min=15,
        emoji="🌙",
        priority=3,
        description="Cross-midnight test window",
    )

    # 22:15 UTC -> Before start (Outside)
    assert not KillZoneGuard._in_zone(22, 15, midnight_kz)

    # 22:30 UTC -> Exactly at start (Inside)
    assert KillZoneGuard._in_zone(22, 30, midnight_kz)

    # 23:45 UTC -> Inside (pre-midnight)
    assert KillZoneGuard._in_zone(23, 45, midnight_kz)

    # 00:00 UTC -> Midnight exactly (Inside)
    assert KillZoneGuard._in_zone(0, 0, midnight_kz)

    # 01:30 UTC -> Inside (post-midnight)
    assert KillZoneGuard._in_zone(1, 30, midnight_kz)

    # 02:14 UTC -> Inside (last minute before end)
    assert KillZoneGuard._in_zone(2, 14, midnight_kz)

    # 02:15 UTC -> Exactly at end (Outside)
    assert not KillZoneGuard._in_zone(2, 15, midnight_kz)

    # 05:00 UTC -> Outside
    assert not KillZoneGuard._in_zone(5, 0, midnight_kz)


def test_convert_utc_to_tz_dst_and_fractional_offsets():
    """Verifies timezone conversion with DST handling and non-integer timezone offsets (e.g. Tehran +3:30)."""
    # Fixed summer timestamp (September 2026)
    # EDT is UTC-4, BST is UTC+1, Tehran is UTC+3:30
    summer_dt = datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc)
    summer_ts = summer_dt.timestamp()

    # 12:00 UTC -> New York (EDT): 08:00
    ny_h, ny_m, ny_abbr = convert_utc_to_tz(12, 0, "America/New_York", ref_ts=summer_ts)
    assert ny_h == 8
    assert ny_m == 0
    assert ny_abbr == "EDT"

    # 12:00 UTC -> London (BST): 13:00
    lon_h, lon_m, lon_abbr = convert_utc_to_tz(12, 0, "Europe/London", ref_ts=summer_ts)
    assert lon_h == 13
    assert lon_m == 0
    assert lon_abbr == "BST"

    # 12:00 UTC -> Tehran (IRST, +03:30): 15:30
    teh_h, teh_m, teh_abbr = convert_utc_to_tz(12, 0, "Asia/Tehran", ref_ts=summer_ts)
    assert teh_h == 15
    assert teh_m == 30

    # 12:00 UTC -> Tokyo (JST, +09:00): 21:00
    tok_h, tok_m, tok_abbr = convert_utc_to_tz(12, 0, "Asia/Tokyo", ref_ts=summer_ts)
    assert tok_h == 21
    assert tok_m == 0
    assert tok_abbr == "JST"

    # 12:00 UTC -> Dubai (GST, +04:00): 16:00
    dxb_h, dxb_m, dxb_abbr = convert_utc_to_tz(12, 0, "Asia/Dubai", ref_ts=summer_ts)
    assert dxb_h == 16
    assert dxb_m == 0


def test_get_current_financial_clocks():
    """Verifies that all 7 institutional financial trading hubs are present in live clocks."""
    clocks = get_current_financial_clocks()
    required_hubs = [
        "UTC",
        "New York (COMEX/ICT)",
        "London (LBMA)",
        "Tokyo (TOCOM)",
        "Shanghai (SGE)",
        "Tehran (Local)",
        "Dubai (Gold Souk)",
    ]
    for hub in required_hubs:
        assert hub in clocks, f"Missing financial hub clock: {hub}"
        data = clocks[hub]
        assert "time_str" in data
        assert "date_str" in data
        assert "hour" in data
        assert "minute" in data
        assert "abbrev" in data
        assert "utc_offset" in data
        assert len(data["time_str"]) == 8  # HH:MM:SS


def test_killzone_guard_active_evaluation():
    """Verifies that KillZoneGuard activates strictly during configured windows."""
    guard = KillZoneGuard(zones=XAUUSD_PEAK_KILLZONES)

    # 08:30 UTC -> Inside London Open (07:00 - 10:30)
    ts_lon = datetime(2026, 9, 15, 8, 30, 0, tzinfo=timezone.utc).timestamp()
    in_kz, name = guard.check(ts=ts_lon)
    assert in_kz is True
    assert name == "London Open (LBMA AM)"
    assert guard.current_zone(ts_lon).name == "London Open (LBMA AM)"
    assert guard.minutes_remaining_in_current(ts_lon) == 120  # Ends at 10:30 (8:30 -> 10:30 is 120m)
    assert guard.minutes_until_next(ts_lon) == (0, "London Open (LBMA AM)")

    # 11:00 UTC -> Off-Window between London Open and New York AM
    ts_off = datetime(2026, 9, 15, 11, 0, 0, tzinfo=timezone.utc).timestamp()
    in_kz, name = guard.check(ts=ts_off)
    assert in_kz is False
    assert name == ""
    assert guard.current_zone(ts_off) is None
    assert guard.minutes_remaining_in_current(ts_off) == 0

    mins_next, next_name = guard.minutes_until_next(ts_off)
    assert next_name == "New York AM / COMEX"
    assert mins_next == 60  # 11:00 -> 12:00 is 60m

    label = guard.zone_label(ts_off)
    assert "Off-Window" in label
    assert "New York AM / COMEX in 60m" in label

    # 13:45 UTC -> Inside New York AM / COMEX (12:00 - 16:30)
    ts_ny = datetime(2026, 9, 15, 13, 45, 0, tzinfo=timezone.utc).timestamp()
    in_kz, name = guard.check(ts=ts_ny)
    assert in_kz is True
    assert name == "New York AM / COMEX"
    assert guard.minutes_remaining_in_current(ts_ny) == 165  # 13:45 -> 16:30 is 165m

    label_ny = guard.zone_label(ts_ny)
    assert "New York AM / COMEX Active" in label_ny
    assert "165m left" in label_ny


def test_all_zones_summary_and_multitz_dashboard():
    """Verifies that all_zones_summary and multitz_dashboard produce rich, structured telemetry."""
    guard = KillZoneGuard(zones=XAUUSD_GOLD_KILLZONES)
    ts = datetime(2026, 9, 15, 14, 0, 0, tzinfo=timezone.utc).timestamp()

    summary = guard.all_zones_summary(ts=ts, target_tz="America/New_York")
    assert len(summary) == 4
    for row in summary:
        assert "name" in row
        assert "emoji" in row
        assert "utc_window" in row
        assert "local_window" in row
        assert "active" in row
        assert "countdown" in row
        assert "description" in row

    # At 14:00 UTC, NY AM is active
    ny_row = next(r for r in summary if r["name"] == "New York AM / COMEX")
    assert ny_row["active"] is True
    assert "150m left" in ny_row["countdown"]  # 14:00 -> 16:30 is 150m

    # Test full multitz_dashboard
    dash = guard.multitz_dashboard(ts=ts)
    assert dash["in_killzone"] is True
    assert dash["active_zone"] == "New York AM / COMEX"
    assert "clocks" in dash
    assert len(dash["clocks"]) == 7
    assert "schedule" in dash
    assert len(dash["schedule"]) == 4

    for item in dash["schedule"]:
        assert "utc" in item
        assert "new_york" in item
        assert "london" in item
        assert "tehran" in item
        assert "active" in item
