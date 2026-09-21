"""
tests/test_replay_streamer.py
=============================
Automated Test Suite for Automated 1:1 Live Market Replay Simulator.

Requirements Covered:
1. Scheduler countdown & 00:00:00 UTC target calculation.
2. Clock spoofing engine (monotonic advancement, speed scaling, monkey-patching).
3. Friday historical Gold candle generation and disk cache.
4. FastAPI Command Center REST endpoints, HTML UI, and WebSocket broadcast.
5. -$10.00 Hard Equity Shield progress calculation.
6. ntfy push notification schema formatting and async HTTP POST.
7. End-to-end MarketReplayStreamer execution loop.
"""

from __future__ import annotations

import asyncio
import functools
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import hyper_predator_bot
from ntfy_integration import ReplayNtfyDispatcher
from replay_clock import ReplayClock
from replay_data import (
    generate_friday_gold_candles,
    generate_thursday_gold_candles,
    get_friday_start_timestamp_ms,
    get_thursday_start_timestamp_ms,
    load_friday_candles,
    load_thursday_candles,
    save_candles_to_disk,
)
from replay_scheduler import (
    get_seconds_until_midnight_utc,
    get_target_midnight_utc,
    wait_until_midnight_utc,
)
from replay_server import (
    ReplayServerState,
    broadcast_state,
    create_replay_app,
)
from replay_streamer import MarketReplayStreamer, ReplayBrokerVenue


def async_test(coro_func: Any) -> Any:
    """Decorator to run async coroutines synchronously using asyncio.run."""
    @functools.wraps(coro_func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(coro_func(*args, **kwargs))

    return wrapper


# =========================================================================
# 1. Scheduler Countdown Tests
# =========================================================================

class TestReplayScheduler:
    def test_target_midnight_utc_calculation(self):
        """Verifies calculation of next upcoming 00:00:00 UTC."""
        ref_time = datetime(2026, 9, 19, 21, 30, 0, tzinfo=timezone.utc)
        target = get_target_midnight_utc(ref_time)
        assert target == datetime(2026, 9, 20, 0, 0, 0, tzinfo=timezone.utc)

    def test_target_midnight_exact_midnight(self):
        """Verifies that if current time is exactly 00:00:00 UTC, target is current."""
        exact_midnight = datetime(2026, 9, 20, 0, 0, 0, 0, tzinfo=timezone.utc)
        target = get_target_midnight_utc(exact_midnight)
        assert target == exact_midnight

    def test_seconds_until_midnight_utc_positive(self):
        """Verifies remaining seconds is strictly positive and <= 86400."""
        ref_time = datetime(2026, 9, 19, 22, 0, 0, tzinfo=timezone.utc)
        rem = get_seconds_until_midnight_utc(ref_time)
        assert rem == 7200.0

    @async_test
    async def test_wait_until_midnight_stop_event(self):
        """Verifies scheduler loop aborts cleanly when stop_event is fired."""
        stop_event = asyncio.Event()
        stop_event.set()
        # Should return immediately without hanging
        await wait_until_midnight_utc(heartbeat_interval=0.01, stop_event=stop_event)


# =========================================================================
# 2. Clock Spoofing & Time Machine Tests
# =========================================================================

class TestReplayClock:
    def test_clock_initialization_and_monotonicity(self):
        """Verifies spoofed clock starts at start_ts and advances monotonically."""
        start_ts = 1789689600.0  # 2026-09-18 00:00:00 UTC
        clock = ReplayClock(simulated_start_ts=start_ts, speed=1.0)
        assert clock.time() == start_ts
        assert clock.now_utc().year == 2026
        assert clock.now_utc().month == 9
        assert clock.now_utc().day == 18

        clock.start()
        time.sleep(0.05)
        t1 = clock.time()
        assert t1 > start_ts
        clock.stop()

    def test_clock_speed_multiplier(self):
        """Verifies speed multiplier scales simulated elapsed time."""
        start_ts = 1789689600.0
        clock = ReplayClock(simulated_start_ts=start_ts, speed=100.0)
        clock.start()
        time.sleep(0.05)
        elapsed = clock.time() - start_ts
        # In 50ms wall clock at 100x speed, at least ~4-5 simulated seconds elapse
        assert elapsed >= 3.0
        clock.stop()

    @async_test
    async def test_clock_sleep_scaling(self):
        """Verifies clock.sleep scales wall clock delay inversely with speed."""
        clock = ReplayClock(1789689600.0, speed=100.0)
        wall_start = time.monotonic()
        await clock.sleep(10.0)  # 10 simulated seconds = 0.1 wall seconds
        wall_dur = time.monotonic() - wall_start
        assert 0.05 <= wall_dur <= 0.30

    def test_patch_and_unpatch_hyper_predator_bot(self):
        """Verifies monkey patching and restoring hyper_predator_bot.time.time."""
        start_ts = 1789689600.0
        clock = ReplayClock(simulated_start_ts=start_ts, speed=1.0)

        orig_time = hyper_predator_bot.time.time
        clock.patch_hyper_predator_bot(hyper_predator_bot)
        assert hyper_predator_bot.time.time() == start_ts

        clock.unpatch_hyper_predator_bot(hyper_predator_bot)
        # Should now be real wall clock epoch (> 1.7e9)
        assert hyper_predator_bot.time.time() != start_ts


# =========================================================================
# 3. Friday Historical Data Tests
# =========================================================================

class TestReplayData:
    def test_generate_friday_gold_candles(self):
        """Verifies generating full Friday candles (1440 bars) with correct fields."""
        candles = generate_friday_gold_candles(n_bars=10, seed=123)
        assert len(candles) == 10
        first = candles[0]
        assert "open" in first
        assert "high" in first
        assert "low" in first
        assert "close" in first
        assert "volume" in first
        assert "tick_timestamps" in first
        assert "l2_bids" in first
        assert "l2_asks" in first
        assert len(first["l2_bids"]) == 5
        assert len(first["l2_asks"]) == 5

    def test_load_and_save_friday_candles(self, tmp_path: Path):
        """Verifies saving and loading candle datasets to disk."""
        candles = generate_friday_gold_candles(n_bars=5, seed=99)
        json_p, csv_p = save_candles_to_disk(candles, candles_dir=tmp_path, filename_prefix="test_gold")
        assert json_p.exists()
        assert csv_p.exists()

        loaded = load_friday_candles(candles_dir=tmp_path, filename_prefix="test_gold")
        assert len(loaded) == 5
        assert loaded[0]["close"] == candles[0]["close"]

    def test_generate_thursday_gold_candles(self):
        """Verifies generating full Thursday candles (1440 bars) with correct fields."""
        candles = generate_thursday_gold_candles(n_bars=10, seed=123)
        assert len(candles) == 10
        first = candles[0]
        assert "open" in first
        assert "high" in first
        assert "low" in first
        assert "close" in first
        assert "volume" in first
        assert "tick_timestamps" in first
        assert "l2_bids" in first
        assert "l2_asks" in first
        assert len(first["l2_bids"]) == 5
        assert len(first["l2_asks"]) == 5

    def test_load_and_save_thursday_candles(self, tmp_path: Path):
        """Verifies saving and loading Thursday candle datasets to disk."""
        candles = generate_thursday_gold_candles(n_bars=5, seed=99)
        json_p, csv_p = save_candles_to_disk(candles, candles_dir=tmp_path, filename_prefix="test_thurs_gold")
        assert json_p.exists()
        assert csv_p.exists()

        loaded = load_thursday_candles(candles_dir=tmp_path, filename_prefix="test_thurs_gold")
        assert len(loaded) == 5
        assert loaded[0]["close"] == candles[0]["close"]


# =========================================================================
# 4. FastAPI Server & WebSocket Dashboard Tests
# =========================================================================

class TestReplayServerAndWebSockets:
    def test_rest_health_endpoint(self):
        """Verifies GET /api/health."""
        state = ReplayServerState()
        app = create_replay_app(state)
        client = TestClient(app)
        res = client.get("/api/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["mode"] == "replay"

    def test_rest_state_endpoint(self):
        """Verifies GET /api/state returns full structured state."""
        state = ReplayServerState()
        state.current_price = 2505.50
        state.active_position = {
            "is_active": True,
            "direction": "LONG",
            "entry_price": 2500.00,
            "size": 1.0,
            "target_price": 2515.00,
            "stop_price": 2495.00,
            "upnl": 5.50,
            "upnl_pct": 8.46,
        }
        app = create_replay_app(state)
        client = TestClient(app)
        res = client.get("/api/state")
        assert res.status_code == 200
        data = res.json()
        assert data["current_price"] == 2505.50
        assert data["active_position"]["direction"] == "LONG"
        assert data["active_position"]["upnl"] == 5.50

    def test_shield_progress_calculation(self):
        """Verifies -$10.00 Equity Shield progress percentage."""
        state = ReplayServerState()
        # In profit or breakeven: 0% burn
        state.active_position["upnl"] = 2.00
        assert state.get_shield_progress_pct() == 0.0

        # -$5.00 loss: 50% burn
        state.active_position["upnl"] = -5.00
        assert state.get_shield_progress_pct() == 50.0

        # -$10.00 loss: 100% burn
        state.active_position["upnl"] = -10.00
        assert state.get_shield_progress_pct() == 100.0

        # Beyond -$10.00 capped at 100%
        state.active_position["upnl"] = -12.50
        assert state.get_shield_progress_pct() == 100.0

    def test_dashboard_html_contains_replay_badge(self):
        """Verifies HTML dashboard contains the pulsing REPLAY badge and telemetry components."""
        state = ReplayServerState()
        app = create_replay_app(state)
        client = TestClient(app)
        res = client.get("/")
        assert res.status_code == 200
        assert "HYPER-PREDATOR" in res.text
        assert "REPLAY" in res.text
        assert "Equity Shield Limit" in res.text
        assert "Core 1: Macro Brain" in res.text

    def test_websocket_stream_connection(self):
        """Verifies WebSocket endpoint /ws/stream handshakes and streams state payload."""
        state = ReplayServerState()
        state.status = "REPLAYING"
        app = create_replay_app(state)
        client = TestClient(app)

        with client.websocket_connect("/ws/stream") as ws:
            msg = ws.receive_text()
            data = json.loads(msg)
            assert data["mode"] == "REPLAY"
            assert data["status"] == "REPLAYING"


# =========================================================================
# 5. ntfy Push Alert Tests
# =========================================================================

class TestReplayNtfyIntegration:
    @async_test
    async def test_ntfy_spam_fired_formatting(self):
        """Verifies formatting of 🟢 [REPLAY] SPAM FIRED alert."""
        dispatcher = ReplayNtfyDispatcher(topic="test-topic", enabled=False)
        task = dispatcher.notify_spam_fired(
            sim_time_str="14:30:00",
            coin="GOLD",
            direction="BUY",
            total_sz=1.25,
            price=2508.40,
            slices=5,
        )
        await task
        assert len(dispatcher._sent_messages) == 1
        msg = dispatcher._sent_messages[0]
        assert "🟢 [REPLAY] SPAM FIRED: BUY GOLD" in msg["title"]
        assert "14:30:00" in msg["message"]
        assert "1.25" in msg["message"]
        assert "$2508.40" in msg["message"]
        assert msg["priority"] == "high"

    @async_test
    async def test_ntfy_micro_exit_formatting(self):
        """Verifies formatting of 🔵 [REPLAY] MICRO-EXIT alert."""
        dispatcher = ReplayNtfyDispatcher(topic="test-topic", enabled=False)
        task = dispatcher.notify_micro_exit(
            sim_time_str="14:35:00",
            coin="GOLD",
            exit_price=2512.00,
            pnl=4.50,
            reason="M5_SR_TARGET",
        )
        await task
        assert len(dispatcher._sent_messages) == 1
        msg = dispatcher._sent_messages[0]
        assert "🔵 [REPLAY] MICRO-EXIT (+$$4.50)" in msg["title"] or "+$4.50" in msg["title"]
        assert "M5_SR_TARGET" in msg["message"]

    @async_test
    async def test_ntfy_equity_shield_formatting(self):
        """Verifies formatting of 🚨 [REPLAY] EQUITY SHIELD alert."""
        dispatcher = ReplayNtfyDispatcher(topic="test-topic", enabled=False)
        task = dispatcher.notify_equity_shield(
            sim_time_str="14:40:00",
            coin="GOLD",
            pnl=-10.05,
            exit_price=2497.95,
        )
        await task
        assert len(dispatcher._sent_messages) == 1
        msg = dispatcher._sent_messages[0]
        assert "🚨 [REPLAY] EQUITY SHIELD TRIGGERED" in msg["title"]
        assert "Breached -$10.00 ceiling" in msg["message"]
        assert msg["priority"] == "urgent"

    @async_test
    async def test_ntfy_llm_shift_formatting(self):
        """Verifies formatting of 🧠 [REPLAY] LLM SHIFT alert."""
        dispatcher = ReplayNtfyDispatcher(topic="test-topic", enabled=False)
        task = dispatcher.notify_llm_shift(
            sim_time_str="14:45:00",
            bias="BEARISH",
            regime=1.50,
            permit_trade=True,
        )
        await task
        assert len(dispatcher._sent_messages) == 1
        msg = dispatcher._sent_messages[0]
        assert "🧠 [REPLAY] LLM SHIFT: BEARISH" in msg["title"]
        assert "PERMITTED" in msg["message"]


# =========================================================================
# 6. Replay Venue & Full Streamer Integration Tests
# =========================================================================

class TestMarketReplayStreamerIntegration:
    @async_test
    async def test_replay_broker_venue_operations(self):
        """Verifies venue order execution, slippage calculation, stops, and cancels."""
        venue = ReplayBrokerVenue(initial_equity=65.00, slippage_delta=0.02)
        venue.set_market_price(2500.00)

        # Market open Long
        res = await venue.market_open("GOLD", is_buy=True, sz=1.0)
        assert res["status"] == "ok"
        assert res["fill_price"] == 2500.02

        # Resting Stop Market Order
        stop_res = await venue.market_close("GOLD", sz=1.0, trigger_px=2490.00)
        assert stop_res["status"] == "ok"
        assert stop_res["type"] == "stop_market"

        # Cancel Stop Order
        cancelled = await venue.cancel("GOLD", stop_res["oid"])
        assert cancelled is True

    @async_test
    async def test_streamer_smoke_test_run(self):
        """Verifies MarketReplayStreamer runs end-to-end for 3 bars with zero crashes."""
        streamer = MarketReplayStreamer(
            speed=500.0,
            start_immediate=True,
            run_server=False,
            enable_ntfy=False,
            test_mode_bars=3,
        )
        await streamer.run()
        assert streamer.state.status == "COMPLETED"
        assert len(streamer.state.candles_history) == 3


# =========================================================================
# 7. Edge Diagnostics & Mobile PWA Tests
# =========================================================================

class TestEdgeDiagnosticsAndMobilePWA:
    def test_edge_catalog_completeness(self):
        """Verifies all 11 quantitative edges are cataloged."""
        from edge_diagnostics import EDGE_CATALOG
        assert len(EDGE_CATALOG) == 11
        for k, v in EDGE_CATALOG.items():
            assert "name" in v
            assert "formula" in v
            assert "fix_rule" in v

    def test_scaling_milestones_math(self):
        """Verifies geometric compounding milestones from $65 to $10,000."""
        from edge_diagnostics import get_current_scaling_milestone
        m1 = get_current_scaling_milestone(65.0)
        assert m1["stage"] == 1
        assert m1["target"] == 100.0
        assert m1["progress_pct"] == 0.0

        m_mid = get_current_scaling_milestone(1000.0)
        assert m_mid["stage"] == 5
        assert m_mid["target"] == 2500.0

        m_end = get_current_scaling_milestone(10000.0)
        assert m_end["stage"] == 7
        assert m_end["progress_pct"] == 100.0

    @async_test
    async def test_3hour_diagnostic_notification_dispatch(self):
        """Verifies formatting and dispatch of 3-hour edge diagnostic alert."""
        from edge_diagnostics import EdgeDiagnosticReporter
        from ntfy_integration import ReplayNtfyDispatcher
        dispatcher = ReplayNtfyDispatcher(topic="test-diag", enabled=False)
        reporter = EdgeDiagnosticReporter(ntfy_dispatcher=dispatcher)

        trades = [
            {"pnl": 3.50, "reason": "M5_SR_TARGET"},
            {"pnl": -1.20, "reason": "REVERSAL_WICK"},
            {"pnl": 2.10, "reason": "M5_SR_TARGET"},
        ]
        macro = {"bias": "BULLISH", "volatility_regime": 1.15}
        success = await reporter.dispatch_3hour_notification(75.50, trades, macro)
        assert success is True
        assert len(dispatcher._sent_messages) == 1
        msg = dispatcher._sent_messages[0]
        assert "📊 [EDGE REPORT]" in msg["title"]
        assert "$75.50" in msg["title"]
        assert "66.7% WR" in msg["title"]
        assert "Stage 1" in msg["message"]
        assert "EDGE RECOMMENDATIONS" in msg["message"]

    def test_mobile_pwa_manifest_and_liquidate_endpoints(self):
        """Verifies GET /manifest.json and POST /api/liquidate."""
        state = ReplayServerState()
        app = create_replay_app(state)
        client = TestClient(app)

        res_man = client.get("/manifest.json")
        assert res_man.status_code == 200
        man = res_man.json()
        assert man["short_name"] == "Predator"
        assert man["display"] == "standalone"

        res_liq = client.post("/api/liquidate")
        assert res_liq.status_code == 200
        assert res_liq.json()["action"] == "EMERGENCY_LIQUIDATE_EXECUTED"
        assert state.active_position["direction"] == "FLAT"
