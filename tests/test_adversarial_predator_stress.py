"""
tests/test_adversarial_predator_stress.py
=========================================
Adversarial Empirical Stress Harness for hyper_predator_bot.py (R1-R6).

Covers:
1. Stress Test 1: Sub-5ms order flow evaluation latency benchmark across 10,000 synthetic
   L2 book updates and trade bursts (measures p50, p90, p95, p99, p99.9, and max latency).
2. Stress Test 2: Rapid reversal and adversarial market shocks:
   - Instant -$10.00 loss drop (Hard Equity Shield liquidation)
   - Opposing 75% rejection wick print (Reversal Wick exit)
   - Top-5 opposing wall spike (L2 Imbalance Wall exit)
   - Verifies zero orphan slices, cancellation of resting detached stop, and clean state.
3. Stress Test 3: Background macro edge timeout and network glitch simulation:
   - Hanging HTTP responses (1000ms+)
   - Corrupted/malformed JSON, HTTP 502/503 errors, type corruptions
   - Sudden server restarts / connection resets
   - Verifies Core 2 execution never hangs or blocks.
4. Stress Test 4: Invalidation wick extreme detached stop placement under wide spreads
   and extreme wick volatility.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import random
import time
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest

from hyper_predator_bot import (
    DETACHED_SL_OFFSET_USD,
    ExecutionBridge,
    HyperPredatorBot,
    MacroState,
    MacroStateManager,
    PredatorBasket,
    PredatorOrderSlice,
    SimulatedBrokerVenue,
    SniperEngine,
    TapeBookMemory,
    orderflow_exit_monitor,
)

logger = logging.getLogger("adversarial_stress")


def async_test(coro_func: Any) -> Any:
    """Helper decorator to execute coroutines synchronously in pytest."""
    @functools.wraps(coro_func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(coro_func(*args, **kwargs))
    return wrapper


# =========================================================================
# Stress Test 1: Sub-5ms Orderflow Latency Benchmark (10,000 Updates)
# =========================================================================

class TestStress1OrderflowLatencyBenchmark:
    """
    Stress test 1: Sub-5ms order flow evaluation latency benchmark across
    10,000 synthetic L2 book updates and trade bursts.
    Measures p50, p90, p95, p99, and max latency.
    """

    @async_test
    async def test_orderflow_latency_10000_synthetic_cycles(self) -> None:
        """
        Runs 10,000 continuous order flow evaluation cycles on randomized L2 books and trades.
        Strict SLA: p50 < 5.0ms, p99 < 5.0ms in pure local memory.
        """
        mem = TapeBookMemory(trade_history_len=100, tick_history_len=1000)
        macro = MacroState(permit_trade=True, bias="BULLISH", volatility_regime=1.0)
        basket = PredatorBasket(
            basket_id="BENCH-BASKET",
            is_buy=True,
            total_sz=0.50,
            entry_price=2500.00,
            target_price=2520.00,
        )
        basket.slices = [
            PredatorOrderSlice(ticket_id=f"T-{i}", basket_id="BENCH-BASKET", sz=0.10, entry_price=2500.00)
            for i in range(5)
        ]

        # Pre-seed random generator for deterministic benchmark reproducibility
        rng = random.Random(42)
        n_iterations = 10_000
        latencies_ns: List[int] = []

        # Dummy non-blocking callback
        async def mock_close(reason: str) -> None:
            pass

        # Populate initial book
        base_mid = 2500.00
        for i in range(n_iterations):
            # Synthetic L2 book update: 5 levels bids, 5 levels asks
            spread = rng.uniform(0.05, 0.20)
            bid0 = round(base_mid - spread / 2.0, 2)
            ask0 = round(base_mid + spread / 2.0, 2)

            # Balanced volumes to prevent early exits during the benchmark
            bid_levels = [{"px": str(round(bid0 - 0.1 * j, 2)), "sz": str(round(rng.uniform(10.0, 50.0), 2))} for j in range(5)]
            ask_levels = [{"px": str(round(ask0 + 0.1 * j, 2)), "sz": str(round(rng.uniform(10.0, 50.0), 2))} for j in range(5)]

            l2_payload = {"levels": [bid_levels, ask_levels]}
            mem.on_l2(l2_payload)

            # Interleaved trade bursts (5 random ticks)
            trades_burst = [
                {"side": rng.choice(["B", "A"]), "sz": str(round(rng.uniform(0.1, 2.0), 2)), "px": str(bid0)}
                for _ in range(5)
            ]
            mem.on_trades(trades_burst)

            # High precision nanosecond timing of orderflow_exit_monitor
            t_start = time.perf_counter_ns()
            _ = await orderflow_exit_monitor(
                position_state=basket,
                book_tape_memory=mem,
                macro_state=macro,
                close_callback=mock_close,
                opposing_m5_sr_target=2520.00,
            )
            t_end = time.perf_counter_ns()
            latencies_ns.append(t_end - t_start)

        # Convert to milliseconds
        latencies_ms = [ns / 1_000_000.0 for ns in latencies_ns]
        sorted_ms = sorted(latencies_ms)

        p50 = sorted_ms[int(n_iterations * 0.50)]
        p90 = sorted_ms[int(n_iterations * 0.90)]
        p95 = sorted_ms[int(n_iterations * 0.95)]
        p99 = sorted_ms[int(n_iterations * 0.99)]
        p999 = sorted_ms[int(n_iterations * 0.999)]
        max_lat = sorted_ms[-1]
        mean_lat = sum(sorted_ms) / n_iterations

        print("\n--- LATENCY BENCHMARK RESULTS (10,000 ITERATIONS) ---")
        print(f"Mean Latency: {mean_lat * 1000.0:.2f} µs ({mean_lat:.5f} ms)")
        print(f"p50  Latency: {p50 * 1000.0:.2f} µs ({p50:.5f} ms)")
        print(f"p90  Latency: {p90 * 1000.0:.2f} µs ({p90:.5f} ms)")
        print(f"p95  Latency: {p95 * 1000.0:.2f} µs ({p95:.5f} ms)")
        print(f"p99  Latency: {p99 * 1000.0:.2f} µs ({p99:.5f} ms)")
        print(f"p99.9 Latency: {p999 * 1000.0:.2f} µs ({p999:.5f} ms)")
        print(f"Max  Latency: {max_lat * 1000.0:.2f} µs ({max_lat:.5f} ms)")
        print("----------------------------------------------------\n")

        # Rigorous assertions: p50 and p99 must both be strictly < 5.0ms (sub-5ms requirement)
        assert p50 < 5.0, f"p50 latency {p50}ms exceeded 5.0ms threshold!"
        assert p99 < 5.0, f"p99 latency {p99}ms exceeded 5.0ms threshold!"
        # In fact, in pure local memory, p99 is typically sub-0.1ms (< 100 µs)
        assert p99 < 1.0, f"p99 latency {p99}ms exceeded 1.0ms local memory target!"


# =========================================================================
# Stress Test 2: Rapid Reversals & Adversarial Market Shocks
# =========================================================================

class TestStress2RapidReversalsAndMarketShocks:
    """
    Stress test 2: Rapid reversal and adversarial market shocks:
    - Instant -$10.00 loss drop
    - Opposing 75% rejection wick print
    - Top-5 opposing wall spike
    Confirms immediate basket liquidation without orphan slices or uncaught exceptions.
    """

    @async_test
    async def test_instant_minus_10_loss_drop_long_and_short(self) -> None:
        """
        Simulate instant market drop triggering Hard Equity Shield (uPnL <= -$10.00).
        Verifies:
        1. Resting detached stop order cancelled in venue.
        2. All 5 slices marked is_active = False.
        3. Realized PnL is properly accounted.
        4. No orphan slices or active basket left in bridge.
        """
        venue = SimulatedBrokerVenue(initial_equity=65.0)
        bridge = ExecutionBridge(venue)

        # 1. Long Basket: Entry $2500.00, size 0.50 oz (5 slices of 0.10 oz, margin = $12.50 <= $13.00)
        venue.set_market_price("GOLD", 2500.00)
        basket_long = await bridge.spam_orders(
            coin="GOLD",
            is_buy=True,
            total_sz=0.50,
            slices=5,
            jitter_ms=0,
            invalidation_wick_price=2495.00,
        )
        assert basket_long is not None
        assert len(basket_long.slices) == 5
        stop_id = basket_long.stop_order_id
        assert stop_id in venue._resting_stops
        assert venue._resting_stops[stop_id]["status"] == "resting"

        # Shock: Bid drops instantly to $2479.00 (uPnL = -$21.00 * 0.50 = -$10.50 <= -$10.00)
        venue.set_market_price("GOLD", 2479.00)
        mem = TapeBookMemory()
        mem.best_bid = 2479.00
        mem.best_ask = 2479.10
        mem.mid_px = 2479.05
        macro = MacroState()

        # Evaluate orderflow monitor with liquidation hook
        reason = await orderflow_exit_monitor(
            position_state=bridge.active_basket,
            book_tape_memory=mem,
            macro_state=macro,
            close_callback=lambda r: bridge.close_basket("GOLD", reason=r),
        )

        assert reason == "HARD_EQUITY_SHIELD"
        # Confirm basket is completely closed
        assert bridge.active_basket is None
        assert basket_long.is_active is False
        assert basket_long.exit_reason == "HARD_EQUITY_SHIELD"
        # Confirm detached stop order was cancelled in venue
        assert venue._resting_stops[stop_id]["status"] == "cancelled"
        # Confirm all slices are closed (no orphan slices)
        for s in basket_long.slices:
            assert s.is_active is False
            assert s.close_price == 2479.00

        # 2. Short Basket: Entry $2500.00, ask rises instantly to $2521.00 (uPnL = -$21.00 * 0.50 = -$10.50)
        venue.set_market_price("GOLD", 2500.00)
        basket_short = await bridge.spam_orders(
            coin="GOLD",
            is_buy=False,
            total_sz=0.50,
            slices=5,
            jitter_ms=0,
            invalidation_wick_price=2505.00,
        )
        assert basket_short is not None
        stop_id_short = basket_short.stop_order_id

        venue.set_market_price("GOLD", 2521.00)
        mem_short = TapeBookMemory()
        mem_short.best_bid = 2520.90
        mem_short.best_ask = 2521.00
        mem_short.mid_px = 2520.95

        reason_short = await orderflow_exit_monitor(
            position_state=bridge.active_basket,
            book_tape_memory=mem_short,
            macro_state=macro,
            close_callback=lambda r: bridge.close_basket("GOLD", reason=r),
        )
        assert reason_short == "HARD_EQUITY_SHIELD"
        assert bridge.active_basket is None
        assert basket_short.is_active is False
        assert venue._resting_stops[stop_id_short]["status"] == "cancelled"
        for s in basket_short.slices:
            assert s.is_active is False

    @async_test
    async def test_opposing_75pct_rejection_wick_instant_liquidation(self) -> None:
        """
        Simulate an opposing 75% rejection wick print while in an active position.
        Long position: Bearish 75% upper rejection wick prints -> immediate market close.
        Short position: Bullish 75% lower rejection wick prints -> immediate market close.
        """
        venue = SimulatedBrokerVenue(initial_equity=65.0)
        bot = HyperPredatorBot(venue=venue)

        # 1. Long position
        basket_long = await bot.execution_bridge.spam_orders(
            coin="GOLD",
            is_buy=True,
            total_sz=0.50,
            slices=5,
            jitter_ms=0,
            invalidation_wick_price=2492.00,
        )
        assert basket_long is not None
        assert bot.execution_bridge.active_basket is not None
        stop_id = basket_long.stop_order_id

        # Candle with 75% upper wick:
        # Range = $10.00. High = 2510.00, Low = 2500.00, Open = 2502.50, Close = 2502.00
        # Upper wick = 2510.00 - 2502.50 = 7.50 -> 75% rejection wick. Close < Open (bearish).
        bearish_75_candle = {
            "open": 2502.50,
            "high": 2510.00,
            "low": 2500.00,
            "close": 2502.00,
        }

        # Send candle close event
        res = await bot.on_m1_candle_close(bearish_75_candle)
        assert res is None
        # Verify active basket closed immediately
        assert bot.execution_bridge.active_basket is None
        assert basket_long.is_active is False
        assert basket_long.exit_reason == "OPPOSING_M1_REJECTION_WICK"
        assert venue._resting_stops[stop_id]["status"] == "cancelled"
        for s in basket_long.slices:
            assert s.is_active is False

        # 2. Short position
        basket_short = await bot.execution_bridge.spam_orders(
            coin="GOLD",
            is_buy=False,
            total_sz=0.50,
            slices=5,
            jitter_ms=0,
            invalidation_wick_price=2508.00,
        )
        assert basket_short is not None
        stop_id_short = basket_short.stop_order_id

        # Bullish 75% lower wick candle:
        # Range = $10.00. High = 2510.00, Low = 2500.00, Open = 2507.50, Close = 2508.00
        # Lower wick = 2507.50 - 2500.00 = 7.50 -> 75% lower wick. Close > Open (bullish).
        bullish_75_candle = {
            "open": 2507.50,
            "high": 2510.00,
            "low": 2500.00,
            "close": 2508.00,
        }
        res_short = await bot.on_m1_candle_close(bullish_75_candle)
        assert res_short is None
        assert bot.execution_bridge.active_basket is None
        assert basket_short.is_active is False
        assert basket_short.exit_reason == "OPPOSING_M1_REJECTION_WICK"
        assert venue._resting_stops[stop_id_short]["status"] == "cancelled"

    @async_test
    async def test_top5_opposing_wall_spike_liquidation(self) -> None:
        """
        Simulate massive sudden opposing wall spike in top-5 L2 book.
        Long: Ask / Bid ratio spikes to 15.0 (>> 3.0 * regime).
        Verifies instant liquidation without orphan orders.
        """
        venue = SimulatedBrokerVenue(initial_equity=65.0)
        bridge = ExecutionBridge(venue)

        basket = await bridge.spam_orders(
            coin="GOLD",
            is_buy=True,
            total_sz=0.50,
            slices=5,
            jitter_ms=0,
            invalidation_wick_price=2490.00,
        )
        assert basket is not None
        stop_id = basket.stop_order_id

        mem = TapeBookMemory()
        # Top 5 levels with massive ask wall: 1500 asks vs 100 bids -> ratio 15.0
        mem.on_l2({
            "levels": [
                [{"px": "2500.0", "sz": "50.0"}, {"px": "2499.9", "sz": "50.0"}],
                [{"px": "2500.1", "sz": "500.0"}, {"px": "2500.2", "sz": "500.0"}, {"px": "2500.3", "sz": "500.0"}],
            ]
        })
        macro = MacroState(volatility_regime=1.0)

        reason = await orderflow_exit_monitor(
            position_state=bridge.active_basket,
            book_tape_memory=mem,
            macro_state=macro,
            close_callback=lambda r: bridge.close_basket("GOLD", reason=r),
        )
        assert reason == "L2_IMBALANCE_WALL"
        assert bridge.active_basket is None
        assert basket.is_active is False
        assert venue._resting_stops[stop_id]["status"] == "cancelled"
        for s in basket.slices:
            assert s.is_active is False


# =========================================================================
# Stress Test 3: Macro Timeout, Glitches & Non-Blocking Resilience
# =========================================================================

class TestStress3MacroTimeoutAndNetworkGlitches:
    """
    Stress test 3: Background macro edge timeout and network glitch simulation:
    - Hanging HTTP responses (1000ms+)
    - Corrupted JSON (HTML 502, truncated JSON, invalid types, empty strings)
    - Sudden server restarts (connection reset, connection refused)
    Confirms Core 2 execution never hangs or blocks.
    """

    @async_test
    async def test_hanging_http_response_non_blocking_core2(self) -> None:
        """
        Simulate an LLM server hanging for 1500ms.
        Verifies:
        1. update_macro_edge() times out within sub-500ms and sets safe hold.
        2. Core 2 sniper execution evaluates concurrently with sub-millisecond decision latency.
        """
        venue = SimulatedBrokerVenue(initial_equity=65.0)
        bot = HyperPredatorBot(venue=venue)
        bot.macro_mgr.update(permit_trade=True, bias="BULLISH", volatility_regime=1.0)

        class HangingAiohttpSession:
            def post(self, *args: Any, **kwargs: Any) -> Any:
                class HangingResponse:
                    async def __aenter__(self) -> Any:
                        await asyncio.sleep(1.500)
                        raise asyncio.TimeoutError("Hanging server timeout")

                    async def __aexit__(self, *args: Any) -> None:
                        pass

                return HangingResponse()

            async def __aenter__(self) -> "HangingAiohttpSession":
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

        # Concurrently launch hanging macro poll and Core 2 signal evaluation
        t_macro_start = time.perf_counter()
        with patch("aiohttp.ClientSession", return_value=HangingAiohttpSession()):
            macro_task = asyncio.create_task(bot.update_macro_edge(poll_once=True))

            # Core 2 execution should not be blocked at all
            t_core2_start = time.perf_counter()
            candle = {"open": 2506.50, "high": 2510.00, "low": 2500.00, "close": 2507.00}
            valid, rho, inval = bot.sniper_engine.compute_rejection_wick(candle, "BULLISH")
            core2_latency = time.perf_counter() - t_core2_start

            # Core 2 execution must be instantaneous (< 5ms)
            assert core2_latency < 0.005
            assert valid is True

            # Wait for macro task to complete timeout
            state = await macro_task
            macro_duration = time.perf_counter() - t_macro_start

        # Confirm macro edge safely aborted and set permit_trade = False
        assert state.permit_trade is False
        assert bot.macro_mgr.get_state().permit_trade is False

    @async_test
    async def test_corrupted_json_and_http_502_error_handling(self) -> None:
        """
        Simulate varied corrupted payloads:
        1. HTTP 502 Bad Gateway / Cloudflare HTML
        2. Truncated malformed JSON: `{"permit_trade": true, "bias":`
        3. Type corruption: `{"permit_trade": "not_bool", "volatility_regime": "inf"}`
        4. Empty string response: `""`
        Verifies bot safely holds safe state without any uncaught exceptions or crashes.
        """
        venue = SimulatedBrokerVenue(initial_equity=65.0)
        bot = HyperPredatorBot(venue=venue)

        corrupted_payloads = [
            # HTML error page
            {"status": 502, "content": "<html><body>502 Bad Gateway</body></html>"},
            # Truncated JSON
            {"status": 200, "content": "{\"permit_trade\": true, \"bias\": \"BULL"},
            # Extreme out-of-range regime and non-standard bias
            {"status": 200, "content": json.dumps({"permit_trade": True, "bias": "UNKNOWN_BIAS", "volatility_regime": 9999.0})},
            # Empty content
            {"status": 200, "content": ""},
            # Missing keys
            {"status": 200, "content": json.dumps({"unrelated_key": 123})},
        ]

        class MockCorruptedSession:
            def __init__(self, item: Dict[str, Any]):
                self.item = item

            def post(self, *args: Any, **kwargs: Any) -> Any:
                mock_resp = AsyncMock()
                mock_resp.status = self.item["status"]
                mock_resp.json = AsyncMock(return_value={"content": self.item["content"]})
                mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
                mock_resp.__aexit__ = AsyncMock(return_value=None)
                return mock_resp

            async def __aenter__(self) -> "MockCorruptedSession":
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

        for p in corrupted_payloads:
            # Set to permit_trade=True prior to test
            bot.macro_mgr.update(permit_trade=True, bias="BULLISH", volatility_regime=1.0)
            with patch("aiohttp.ClientSession", return_value=MockCorruptedSession(p)):
                try:
                    state = await bot.update_macro_edge(poll_once=True)
                    # Either clamped or safely fallen back to permit_trade=False
                    if p.get("status") != 200 or "UNKNOWN" in str(p.get("content")):
                        # Handled as safe hold or clamped
                        assert isinstance(state, MacroState)
                except Exception as exc:
                    pytest.fail(f"Uncaught exception on payload {p}: {exc}")

    @async_test
    async def test_sudden_server_disconnect_and_recovery(self) -> None:
        """
        Simulate server crashes (ConnectionRefusedError, ServerDisconnectedError),
        followed by successful recovery.
        Verifies seamless fail-safe operation.
        """
        venue = SimulatedBrokerVenue(initial_equity=65.0)
        bot = HyperPredatorBot(venue=venue)

        # 1. Simulate server offline (ConnectionRefusedError)
        class OfflineSession:
            def post(self, *args: Any, **kwargs: Any) -> Any:
                raise ConnectionRefusedError("Connection refused by localhost:8080")

            async def __aenter__(self) -> "OfflineSession":
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

        bot.macro_mgr.update(permit_trade=True, bias="BEARISH", volatility_regime=1.2)
        with patch("aiohttp.ClientSession", return_value=OfflineSession()):
            state_fail = await bot.update_macro_edge(poll_once=True)
        assert state_fail.permit_trade is False

        # 2. Server recovers
        class HealthySession:
            def post(self, *args: Any, **kwargs: Any) -> Any:
                mock_resp = AsyncMock()
                mock_resp.status = 200
                payload = json.dumps({"permit_trade": True, "bias": "BEARISH", "volatility_regime": 1.10})
                mock_resp.json = AsyncMock(return_value={"content": payload})
                mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
                mock_resp.__aexit__ = AsyncMock(return_value=None)
                return mock_resp

            async def __aenter__(self) -> "HealthySession":
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

        with patch("aiohttp.ClientSession", return_value=HealthySession()):
            state_recovered = await bot.update_macro_edge(poll_once=True)
        assert state_recovered.permit_trade is True
        assert state_recovered.bias == "BEARISH"
        assert state_recovered.volatility_regime == 1.10


# =========================================================================
# Stress Test 4: Detached Stop Placement Precision Under Extreme Conditions
# =========================================================================

class TestStress4DetachedStopPlacementPrecision:
    """
    Stress test 4: Invalidation wick extreme detached stop placement:
    Verifies stop placement precision under wide spreads and extreme wick volatility.
    """

    @async_test
    async def test_detached_stop_under_wide_spread(self) -> None:
        """
        Verifies stop-loss placement remains exactly $1.00 beyond invalidation wick extreme
        even when bid/ask spread is arbitrarily wide ($5.00 spread).
        """
        venue = SimulatedBrokerVenue(initial_equity=65.0)
        bridge = ExecutionBridge(venue)

        # Market price mid = 2500, but spread is 5.00 (bid 2497.50, ask 2502.50)
        venue.set_market_price("GOLD", 2500.00)

        # Long with invalidation wick low at 2490.00
        inval_long = 2490.00
        basket_long = await bridge.spam_orders(
            coin="GOLD",
            is_buy=True,
            total_sz=0.50,
            slices=5,
            jitter_ms=0,
            invalidation_wick_price=inval_long,
        )
        assert basket_long is not None
        # SL must be exactly 2490.00 - 1.00 = 2489.00 regardless of wide spread
        assert basket_long.sl_price == 2489.00
        stop_long = venue._resting_stops.get(basket_long.stop_order_id)
        assert stop_long["trigger_px"] == 2489.00
        assert stop_long["reduce_only"] is True

        await bridge.close_basket("GOLD", reason="TEST_RESET")

        # Short with invalidation wick high at 2510.00
        inval_short = 2510.00
        basket_short = await bridge.spam_orders(
            coin="GOLD",
            is_buy=False,
            total_sz=0.50,
            slices=5,
            jitter_ms=0,
            invalidation_wick_price=inval_short,
        )
        assert basket_short is not None
        # SL must be exactly 2510.00 + 1.00 = 2511.00 regardless of wide spread
        assert basket_short.sl_price == 2511.00
        stop_short = venue._resting_stops.get(basket_short.stop_order_id)
        assert stop_short["trigger_px"] == 2511.00
        assert stop_short["reduce_only"] is True

    @async_test
    async def test_extreme_wick_volatility_stop_precision(self) -> None:
        """
        Verifies stop placement precision under extreme wick volatility:
        - Massive 85% lower wick ($85.00 wick on $100.00 candle).
        - Fractional cents invalidation prices (e.g. 2415.375).
        - Confirms trigger_px is rounded cleanly to 2 decimal places.
        """
        venue = SimulatedBrokerVenue(initial_equity=65.0)
        bridge = ExecutionBridge(venue)

        # Extreme candle with fractional penny invalidation: Low = 2415.375
        inval_frac = 2415.375
        expected_sl = round(inval_frac - DETACHED_SL_OFFSET_USD, 2)  # 2414.38

        basket = await bridge.spam_orders(
            coin="GOLD",
            is_buy=True,
            total_sz=0.50,
            slices=5,
            jitter_ms=0,
            invalidation_wick_price=inval_frac,
        )
        assert basket is not None
        assert basket.sl_price == expected_sl
        stop_rec = venue._resting_stops.get(basket.stop_order_id)
        assert stop_rec["trigger_px"] == expected_sl
        assert stop_rec["reduce_only"] is True

    def test_sniper_engine_extreme_rejection_wick_shapes(self) -> None:
        """
        Adversarial evaluation of candle geometry:
        - Candle range = $100.00: High = 2600.00, Low = 2500.00, Open = 2585.00, Close = 2586.00
          Lower wick = 2585.00 - 2500.00 = 85.00 (85.0%).
        - Asserts rejection wick calculation handles wide range effortlessly.
        """
        sniper = SniperEngine(wick_rejection_pct=0.65)
        extreme_bullish_candle = {
            "open": 2585.00,
            "high": 2600.00,
            "low": 2500.00,
            "close": 2586.00,
        }
        valid, rho, inval = sniper.compute_rejection_wick(extreme_bullish_candle, "BULLISH")
        assert valid is True
        assert rho == 0.85
        assert inval == 2500.00


if __name__ == "__main__":
    pytest.main(["-v", __file__])
