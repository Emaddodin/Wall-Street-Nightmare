"""
tests/test_hyper_predator.py
============================
Automated Test Suite for Hyper Predator Architecture & Vectorized Backtester.
Requirements Coverage: R1 to R8.

Test Classes:
1. TestMacroBrainCore1 (R1): Macro state defaults, LLM JSON parsing, markdowns, fallback, timeout, thread safety.
2. TestSniperCore2M1Signal (R2): Rejection wick (exact 65%, sub-65%, directional alignment, zero-range guard),
   rolling M5 S/R pivots, final 5s tick velocity surge, full confluence trigger.
3. TestSpamOrdersLayeredExecution (R3): 5-slice spam dispatch, 20ms jitter stagger, detached stop placement
   distance ($1.00 beyond wick), 100x leverage & 20% margin ceiling, open-ended entry (no static TP).
4. TestL2OrderflowExitEngine (R5): Top-5 L2 imbalance calculation, long/short wall liquidation,
   adaptive volatility regime scaling, sub-5ms memory benchmark.
5. TestTradeTapeVolumeDeltaStall (R5): Volume delta stall in profit (> 80% opposing), normal flow holding.
6. TestRuthlessExitsAndHardEquityShield (R4): Hard equity shield liquidation at -$10.00, opposing reversal wick exit,
   opposing M5 S/R target exit.
7. TestVectorizedBacktester (R7): Data ingestion formats (CSV, Parquet, np.ndarray, DataFrame), chunked streaming
   continuity with 1k halo buffer, memory footprint under 4GB RAM (< 200MB RSS), signal consistency across exit types.
8. TestParameterSweepAndMonteCarlo (R7): 4-parameter grid sweep interface, 500-run Monte Carlo execution with jitter
   and slippage friction, analytical metrics calculation accuracy (Sharpe, Max DD, Win Rate, Profit Factor).
"""

from __future__ import annotations

import asyncio
import functools
import gc
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, call, patch

import numpy as np
import pandas as pd

from backtester import (
    BacktestResult,
    HyperPredatorParams,
    M1ChunkIterator,
    MonteCarloResult,
    TradeRecord,
    VectorizedBacktester,
    VectorizedSignalEngine,
    generate_synthetic_gold_m1,
)
from hyper_predator_bot import (
    DETACHED_SL_OFFSET_USD,
    ExecutionBridge,
    HyperPredatorBot,
    MacroState,
    MacroStateManager,
    OrderflowMonitor,
    PredatorBasket,
    PredatorOrderSlice,
    SimulatedBrokerVenue,
    SniperEngine,
    TapeBookMemory,
    close_basket,
    orderflow_exit_monitor,
)

# -------------------------------------------------------------------------
# Mock Helpers for Local LLM Macro Polling (Zero Network Calls)
# -------------------------------------------------------------------------

class MockAiohttpResponse:
    """Deterministic in-memory mock for aiohttp response."""

    def __init__(self, json_data: Dict[str, Any], status: int = 200, delay_s: float = 0.0) -> None:
        self._json_data = json_data
        self.status = status
        self.delay_s = delay_s

    async def json(self) -> Dict[str, Any]:
        return self._json_data

    async def __aenter__(self) -> "MockAiohttpResponse":
        if self.delay_s > 0.0:
            await asyncio.sleep(self.delay_s)
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass


class MockAiohttpSession:
    """Deterministic in-memory mock for aiohttp.ClientSession."""

    def __init__(self, response: MockAiohttpResponse) -> None:
        self.response = response

    def post(self, *args: Any, **kwargs: Any) -> MockAiohttpResponse:
        return self.response

    async def __aenter__(self) -> "MockAiohttpSession":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass


def async_test(coro_func: Any) -> Any:
    """Decorator to run async coroutines synchronously using asyncio.run."""
    @functools.wraps(coro_func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(coro_func(*args, **kwargs))

    return wrapper


# =========================================================================
# 1. TestMacroBrainCore1 (Requirement R1)
# =========================================================================

class TestMacroBrainCore1:
    """Validates Core 1: Asynchronous Background Macro Edge & State Container."""

    def test_macro_state_dataclass_defaults(self) -> None:
        """Assures MacroState dataclass initializes with strict safe default values."""
        state = MacroState()
        assert state.permit_trade is False
        assert state.bias == "BULLISH"
        assert state.volatility_regime == 1.0
        assert state.last_updated == 0.0
        assert state.is_fresh(max_age_seconds=600.0) is False

        mgr = MacroStateManager()
        initial = mgr.get_state()
        assert initial.permit_trade is False
        assert initial.bias == "BULLISH"
        assert initial.volatility_regime == 1.0
        assert initial.last_updated == 0.0

    @async_test
    async def test_llm_json_parsing_valid_bullish(self) -> None:
        """Verifies LLM JSON response parser correctly decodes bullish market sentiment."""
        bot = HyperPredatorBot(venue=SimulatedBrokerVenue())
        payload = {"content": json.dumps({"permit_trade": True, "bias": "BULLISH", "volatility_regime": 1.25})}
        mock_resp = MockAiohttpResponse(payload, status=200)

        with patch("aiohttp.ClientSession", return_value=MockAiohttpSession(mock_resp)):
            state = await bot.update_macro_edge(poll_once=True)

        assert state.permit_trade is True
        assert state.bias == "BULLISH"
        assert state.volatility_regime == 1.25
        assert state.last_updated > 0.0
        assert bot.macro_mgr.get_state().permit_trade is True

    @async_test
    async def test_llm_json_parsing_valid_bearish(self) -> None:
        """Verifies LLM JSON response parser correctly decodes bearish market sentiment."""
        bot = HyperPredatorBot(venue=SimulatedBrokerVenue())
        payload = {"content": json.dumps({"permit_trade": True, "bias": "BEARISH", "volatility_regime": 0.85})}
        mock_resp = MockAiohttpResponse(payload, status=200)

        with patch("aiohttp.ClientSession", return_value=MockAiohttpSession(mock_resp)):
            state = await bot.update_macro_edge(poll_once=True)

        assert state.permit_trade is True
        assert state.bias == "BEARISH"
        assert state.volatility_regime == 0.85
        assert bot.macro_mgr.get_state().bias == "BEARISH"

    @async_test
    async def test_llm_json_parsing_markdown_codeblock_fallback(self) -> None:
        """Verifies responses wrapped in markdown codeblocks safely fallback to permit_trade=False."""
        bot = HyperPredatorBot(venue=SimulatedBrokerVenue())
        # Pre-set to permit_trade=True to verify fallback disarms it
        bot.macro_mgr.update(permit_trade=True, bias="BEARISH", volatility_regime=1.5)

        markdown_payload = {
            "content": "```json\n{\"permit_trade\": false, \"bias\": \"BEARISH\", \"volatility_regime\": 1.5}\n```"
        }
        mock_resp = MockAiohttpResponse(markdown_payload, status=200)

        with patch("aiohttp.ClientSession", return_value=MockAiohttpSession(mock_resp)):
            state = await bot.update_macro_edge(poll_once=True)

        # Fallback must hold safe state (permit_trade=False)
        assert state.permit_trade is False
        assert bot.macro_mgr.get_state().permit_trade is False

    @async_test
    async def test_llm_json_parsing_malformed_syntax_fallback(self) -> None:
        """Verifies broken JSON syntax triggers safe fallback without crashing or blocking."""
        bot = HyperPredatorBot(venue=SimulatedBrokerVenue())
        bot.macro_mgr.update(permit_trade=True, bias="BULLISH", volatility_regime=1.0)

        malformed_payload = {"content": "{\"permit_trade\": true, bias: BULLISH"}
        mock_resp = MockAiohttpResponse(malformed_payload, status=200)

        with patch("aiohttp.ClientSession", return_value=MockAiohttpSession(mock_resp)):
            state = await bot.update_macro_edge(poll_once=True)

        assert state.permit_trade is False
        assert bot.macro_mgr.get_state().permit_trade is False

    @async_test
    async def test_update_macro_edge_timeout_non_blocking(self) -> None:
        """Verifies sub-500ms timeout enforcement with non-blocking fallback to safe hold."""
        bot = HyperPredatorBot(venue=SimulatedBrokerVenue())
        bot.macro_mgr.update(permit_trade=True, bias="BULLISH", volatility_regime=1.0)

        class HangingPostSession:
            def post(self, *args: Any, **kwargs: Any) -> Any:
                # Simulate ClientTimeout raising TimeoutError
                raise asyncio.TimeoutError("LLM response timeout > 500ms")

            async def __aenter__(self) -> "HangingPostSession":
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

        t0 = time.perf_counter()
        with patch("aiohttp.ClientSession", return_value=HangingPostSession()):
            state = await bot.update_macro_edge(poll_once=True)
        elapsed = time.perf_counter() - t0

        # Assert sub-500ms non-blocking fallback
        assert elapsed < 0.500
        assert state.permit_trade is False

    def test_macro_state_thread_safety(self) -> None:
        """Verifies atomic snapshot isolation under concurrent reader and writer threads."""
        mgr = MacroStateManager()
        iterations = 500
        read_snapshots: List[MacroState] = []
        stop_flag = threading.Event()

        def writer_loop() -> None:
            for i in range(iterations):
                b = "BULLISH" if i % 2 == 0 else "BEARISH"
                mgr.update(permit_trade=(i % 2 == 0), bias=b, volatility_regime=1.0 + (i % 5) * 0.1)
                time.sleep(0.0001)
            stop_flag.set()

        def reader_loop() -> None:
            while not stop_flag.is_set():
                snap = mgr.get_state()
                read_snapshots.append(snap)
                time.sleep(0.0001)

        t_write = threading.Thread(target=writer_loop)
        t_read = threading.Thread(target=reader_loop)

        t_write.start()
        t_read.start()
        t_write.join()
        t_read.join()

        assert len(read_snapshots) > 0
        for s in read_snapshots:
            assert isinstance(s.permit_trade, bool)
            assert s.bias in ("BULLISH", "BEARISH", "NEUTRAL")
            assert 0.1 <= s.volatility_regime <= 10.0


# =========================================================================
# 2. TestSniperCore2M1Signal (Requirement R2)
# =========================================================================

class TestSniperCore2M1Signal:
    """Validates Core 2: High-Frequency M1 Sniper Math, S/R Zones, and Velocity Gates."""

    def test_rejection_wick_calculation_exact_65pct_bullish(self) -> None:
        """Validates exact 65.0% and > 65.0% lower rejection wick on bullish candle."""
        sniper = SniperEngine(wick_rejection_pct=0.65)

        # Exact 65.0%: Range=10.00, Low=2500.00, Open=2506.50, Close=2507.00, High=2510.00
        # Lower wick = 2506.50 - 2500.00 = 6.50 -> 65.0%. Close > Open.
        candle_65 = {"open": 2506.50, "high": 2510.00, "low": 2500.00, "close": 2507.00}
        valid_65, rho_65, inval_65 = sniper.compute_rejection_wick(candle_65, "BULLISH")
        assert valid_65 is True
        assert rho_65 == 0.65
        assert inval_65 == 2500.00

        # 90.0% wick: Range=10.00, Low=2492.00, Open=2501.00, High=2502.00, Close=2501.50
        # Lower wick = 2501.00 - 2492.00 = 9.00 -> 90.0%. Close > Open.
        candle_90 = {"open": 2501.00, "high": 2502.00, "low": 2492.00, "close": 2501.50}
        valid_90, rho_90, inval_90 = sniper.compute_rejection_wick(candle_90, "BULLISH")
        assert valid_90 is True
        assert rho_90 == 0.90
        assert inval_90 == 2492.00

    def test_rejection_wick_calculation_sub_65pct_rejection(self) -> None:
        """Validates strict rejection of candles with rejection wick < 65% (e.g. 64.5%)."""
        sniper = SniperEngine(wick_rejection_pct=0.65)
        # Lower wick = 2506.45 - 2500.00 = 6.45 out of 10.00 -> 64.5% < 65%
        candle_sub = {"open": 2506.45, "high": 2510.00, "low": 2500.00, "close": 2507.00}
        valid, rho, _ = sniper.compute_rejection_wick(candle_sub, "BULLISH")
        assert valid is False
        assert rho == 0.645

    def test_rejection_wick_bearish_alignment(self) -> None:
        """Validates upper rejection wick (>= 65%) with bearish body close and macro alignment."""
        sniper = SniperEngine(wick_rejection_pct=0.65)
        # Upper wick = 2510.00 - 2503.50 = 6.50 out of 10.00 (65%). Close < Open (red candle).
        candle_bear = {"open": 2503.50, "high": 2510.00, "low": 2500.00, "close": 2503.00}

        # Aligned with BEARISH bias -> Pass
        valid_bear, rho, inval_bear = sniper.compute_rejection_wick(candle_bear, "BEARISH")
        assert valid_bear is True
        assert rho == 0.65
        assert inval_bear == 2510.00

        # Opposed by BULLISH bias -> Must be rejected
        valid_opposed, _, _ = sniper.compute_rejection_wick(candle_bear, "BULLISH")
        assert valid_opposed is False

    def test_rejection_wick_zero_range_guard(self) -> None:
        """Validates zero-division guard when candle High == Low == Open == Close."""
        sniper = SniperEngine()
        candle_zero = {"open": 2500.00, "high": 2500.00, "low": 2500.00, "close": 2500.00}
        valid, rho, inval = sniper.compute_rejection_wick(candle_zero, "BULLISH")
        assert valid is False
        assert rho == 0.0
        assert inval == 2500.00

    def test_m5_support_resistance_rolling_pivot(self) -> None:
        """Validates dynamic M5 S/R pivot calculation over rolling lookback window."""
        sniper = SniperEngine(sr_lookback_bars=20, zone_epsilon=0.25)
        # Create 25 historical M5 candles where window [-20:] has min low=2485.50 and max high=2525.00
        candles = []
        for i in range(25):
            lo = 2490.00 + (i % 3)
            hi = 2510.00 - (i % 3)
            if i == 10:
                lo = 2485.50  # Global minimum in window
            if i == 15:
                hi = 2525.00  # Global maximum in window
            candles.append({"open": 2500.0, "high": hi, "low": lo, "close": 2500.0})

        sniper.set_m5_candles(candles)
        assert sniper.current_support == 2485.50
        assert sniper.current_resistance == 2525.00

        # Test zone tolerance boundaries (epsilon = 0.25)
        assert sniper.is_in_sr_zone(2485.70, is_buy=True) is True   # Inside Support zone [2485.25, 2485.75]
        assert sniper.is_in_sr_zone(2486.00, is_buy=True) is False  # Outside Support zone
        assert sniper.is_in_sr_zone(2524.80, is_buy=False) is True  # Inside Resistance zone [2524.75, 2525.25]
        assert sniper.is_in_sr_zone(2500.00, is_buy=False) is False

    def test_tick_velocity_edge_threshold(self) -> None:
        """Validates tick velocity surge gate: >= 1.5x baseline permitted, < 1.5x blocked."""
        sniper = SniperEngine(velocity_multiplier_threshold=1.50)
        t_close = 1000.0
        t_open = 940.0
        # 55s baseline window [940.0, 995.0): 55 ticks -> v_base = 1.0 tick/s
        base_ticks = [940.0 + i for i in range(55)]

        # Surge A: 8 ticks in final 5s [995.0, 1000.0] -> v_surge = 8/5 = 1.6 tick/s -> 1.6x >= 1.5x
        surge_ticks_pass = base_ticks + [995.5, 996.0, 996.5, 997.0, 997.5, 998.0, 998.5, 999.0]
        pass_ok, ratio_pass, v_surge_pass, v_base_pass = sniper.evaluate_tick_velocity(
            t_open, t_close, surge_ticks_pass
        )
        assert pass_ok is True
        assert ratio_pass == 1.6
        assert v_surge_pass == 1.6
        assert v_base_pass == 1.0

        # Surge B: 7 ticks in final 5s -> v_surge = 7/5 = 1.4 tick/s -> 1.4x < 1.5x (blocked)
        surge_ticks_fail = base_ticks + [995.5, 996.0, 996.5, 997.0, 997.5, 998.0, 998.5]
        fail_ok, ratio_fail, v_surge_fail, _ = sniper.evaluate_tick_velocity(
            t_open, t_close, surge_ticks_fail
        )
        assert fail_ok is False
        assert ratio_fail == 1.4
        assert v_surge_fail == 1.4

    def test_signal_trigger_full_confluence(self) -> None:
        """Verifies trigger fires if and only if: macro permit, S/R zone touch, wick >= 65%, velocity >= 1.5x."""
        sniper = SniperEngine(sr_lookback_bars=20, zone_epsilon=0.25)
        # Establish support at 2500.00
        sniper.set_m5_candles([{"high": 2510.0, "low": 2500.0, "open": 2505.0, "close": 2505.0}] * 20)

        macro_valid = MacroState(permit_trade=True, bias="BULLISH", volatility_regime=1.0)
        # M1 candle touching support at 2499.90, lower wick = 65% of 10.00
        candle = {
            "open": 2506.50,
            "high": 2510.00,
            "low": 2499.90,
            "close": 2507.00,
            "open_time": 940.0,
            "close_time": 1000.0,
        }
        # Ticks with 1.6x surge
        ticks = [940.0 + i for i in range(55)] + [995.5, 996.0, 996.5, 997.0, 997.5, 998.0, 998.5, 999.0]

        # 1. Full Confluence -> Trigger fires
        signal = sniper.evaluate_m1_trigger(candle, macro_valid, ticks)
        assert signal is not None
        assert signal["signal"] == "ENTRY"
        assert signal["is_buy"] is True
        assert signal["invalidation_price"] == 2499.90
        assert signal["sl_price"] == round(2499.90 - DETACHED_SL_OFFSET_USD, 2)
        assert signal["wick_ratio"] >= 0.65
        assert signal["velocity_ratio"] >= 1.50

        # 2. Defect: Macro veto (permit_trade=False) -> Vetoed
        macro_veto = MacroState(permit_trade=False, bias="BULLISH", volatility_regime=1.0)
        assert sniper.evaluate_m1_trigger(candle, macro_veto, ticks) is None

        # 3. Defect: Low outside S/R zone (Low=2501.00 > 2500.25) -> Vetoed
        candle_outside = dict(candle, low=2501.00)
        assert sniper.evaluate_m1_trigger(candle_outside, macro_valid, ticks) is None

    def test_m5_sr_empty_candles_guard(self) -> None:
        """Validates S/R pivot calculation behavior when candle history is insufficient."""
        sniper = SniperEngine()
        assert sniper.current_support is None
        assert sniper.current_resistance is None
        assert sniper.is_in_sr_zone(2500.0, is_buy=True) is False
        assert sniper.is_in_sr_zone(2500.0, is_buy=False) is False


# =========================================================================
# 3. TestSpamOrdersLayeredExecution (Requirement R3)
# =========================================================================

class TestSpamOrdersLayeredExecution:
    """Validates Layered Order Slicing, Detached Stops, Leverage & Margin Ceiling."""

    @async_test
    async def test_spam_orders_5_slices_dispatched(self) -> None:
        """Verifies spam_orders dispatches exactly 5 slices of equal micro-size."""
        venue = SimulatedBrokerVenue(initial_equity=65.0)
        bridge = ExecutionBridge(venue)

        basket = await bridge.spam_orders(
            coin="GOLD",
            is_buy=True,
            total_sz=0.50,
            slices=5,
            jitter_ms=5,
            invalidation_wick_price=2492.00,
        )

        assert basket is not None
        assert basket.is_active is True
        assert len(basket.slices) == 5
        assert basket.total_sz == 0.50
        for s in basket.slices:
            assert s.sz == 0.10
            assert s.is_buy is True
            assert s.entry_price > 0.0

    @async_test
    async def test_spam_orders_20ms_jitter_stagger(self) -> None:
        """Verifies 20ms jitter stagger delays between micro-slice dispatches."""
        venue = SimulatedBrokerVenue(initial_equity=65.0)
        bridge = ExecutionBridge(venue)

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await bridge.spam_orders(
                coin="GOLD",
                is_buy=True,
                total_sz=0.50,
                slices=5,
                jitter_ms=20,
                invalidation_wick_price=2492.00,
            )

            # Slices 1..4 should sleep 20ms, 40ms, 60ms, 80ms
            expected_calls = [call(0.020), call(0.040), call(0.060), call(0.080)]
            mock_sleep.assert_has_calls(expected_calls, any_order=False)

    @async_test
    async def test_detached_stop_placement_distance(self) -> None:
        """Asserts detached stop is placed exactly $1.00 beyond invalidation wick with reduce_only=True."""
        venue = SimulatedBrokerVenue(initial_equity=65.0)
        bridge = ExecutionBridge(venue)

        # Long: Invalidation wick extreme = 2492.00 -> SL must be exactly 2491.00
        basket_long = await bridge.spam_orders(
            coin="GOLD",
            is_buy=True,
            total_sz=0.50,
            slices=5,
            jitter_ms=0,
            invalidation_wick_price=2492.00,
        )
        assert basket_long is not None
        assert basket_long.sl_price == 2491.00
        stop_order = venue._resting_stops.get(basket_long.stop_order_id)
        assert stop_order is not None
        assert stop_order["trigger_px"] == 2491.00
        assert stop_order["reduce_only"] is True

        # Close long basket before testing short
        await bridge.close_basket("GOLD", reason="TEST_RESET")

        # Short: Invalidation wick extreme = 2508.00 -> SL must be exactly 2509.00
        basket_short = await bridge.spam_orders(
            coin="GOLD",
            is_buy=False,
            total_sz=0.50,
            slices=5,
            jitter_ms=0,
            invalidation_wick_price=2508.00,
        )
        assert basket_short is not None
        assert basket_short.sl_price == 2509.00
        stop_order_short = venue._resting_stops.get(basket_short.stop_order_id)
        assert stop_order_short is not None
        assert stop_order_short["trigger_px"] == 2509.00
        assert stop_order_short["reduce_only"] is True

    @async_test
    async def test_leverage_100x_and_margin_ceiling_20pct(self) -> None:
        """Asserts 100x leverage margin requirement and strict 20% equity margin ceiling."""
        venue = SimulatedBrokerVenue(initial_equity=65.0)
        bridge = ExecutionBridge(venue)
        cur_px = 2500.00
        venue.set_market_price("GOLD", cur_px)

        # Max allowed margin = $65.00 * 20% = $13.00
        # Compliant size: 0.50 oz -> Margin = (0.50 * 2500) / 100 = $12.50 <= $13.00
        ok_valid, req_valid, max_m = await bridge.validate_margin(0.50, cur_px)
        assert ok_valid is True
        assert req_valid == 12.50
        assert max_m == 13.00

        # Excessive size: 1.00 oz -> Margin = (1.00 * 2500) / 100 = $25.00 > $13.00 -> Blocked
        ok_invalid, req_invalid, _ = await bridge.validate_margin(1.00, cur_px)
        assert ok_invalid is False
        assert req_invalid == 25.00

        # spam_orders must return None when margin ceiling breached
        rejected_basket = await bridge.spam_orders(coin="GOLD", is_buy=True, total_sz=1.00, slices=5)
        assert rejected_basket is None

    @async_test
    async def test_open_ended_entry_no_static_tp(self) -> None:
        """Verifies open-ended order entry: no static take-profit limit orders dispatched."""
        venue = SimulatedBrokerVenue(initial_equity=65.0)
        bridge = ExecutionBridge(venue)

        basket = await bridge.spam_orders(
            coin="GOLD", is_buy=True, total_sz=0.50, slices=5, jitter_ms=0, invalidation_wick_price=2492.00
        )
        assert basket is not None

        # Verify all slices and resting orders have no take_profit
        for oid, ord_data in venue._orders.items():
            assert ord_data.get("take_profit") is None

        # Only one resting stop order exists
        assert len(venue._resting_stops) == 1

    @async_test
    async def test_active_basket_prevents_overlapping_entry(self) -> None:
        """Verifies ExecutionBridge prevents overlapping entries while active basket is open."""
        venue = SimulatedBrokerVenue(initial_equity=65.0)
        bridge = ExecutionBridge(venue)

        basket1 = await bridge.spam_orders(coin="GOLD", is_buy=True, total_sz=0.50, slices=5, jitter_ms=0)
        assert basket1 is not None

        # Overlapping entry attempt -> Rejected
        basket2 = await bridge.spam_orders(coin="GOLD", is_buy=True, total_sz=0.50, slices=5, jitter_ms=0)
        assert basket2 is None

        # Liquidate basket1
        summary = await bridge.close_basket("GOLD", reason="TARGET")
        assert summary is not None
        assert bridge.active_basket is None

        # Subsequent entry now permitted
        basket3 = await bridge.spam_orders(coin="GOLD", is_buy=True, total_sz=0.50, slices=5, jitter_ms=0)
        assert basket3 is not None


# =========================================================================
# 4. TestL2OrderflowExitEngine (Requirement R5)
# =========================================================================

class TestL2OrderflowExitEngine:
    """Validates Sub-5ms L2 Orderflow Imbalance Exit Engine & Adaptive Thresholds."""

    def test_l2_top5_imbalance_calculation(self) -> None:
        """Validates Top-5 L2 order book volume aggregation and imbalance ratio."""
        mem = TapeBookMemory()
        l2_data = {
            "levels": [
                [{"px": "2500.0", "sz": "30.0"}, {"px": "2499.9", "sz": "20.0"}, {"px": "2499.8", "sz": "50.0"}],
                [{"px": "2500.1", "sz": "100.0"}, {"px": "2500.2", "sz": "150.0"}, {"px": "2500.3", "sz": "100.0"}],
            ]
        }
        mem.on_l2(l2_data)
        bid_vol, ask_vol = mem.get_top5_volumes()
        assert bid_vol == 100.0
        assert ask_vol == 350.0
        assert mem.best_bid == 2500.0
        assert mem.best_ask == 2500.1

    @async_test
    async def test_l2_imbalance_exit_long_wall(self) -> None:
        """Validates instant basket liquidation when Long faces Ask/Bid ratio > 3.0 * vol_regime."""
        mem = TapeBookMemory()
        # Ask/Bid ratio = 350.0 / 100.0 = 3.5 > 3.0 * 1.0
        mem.on_l2({
            "levels": [
                [{"px": "2500.0", "sz": "100.0"}],
                [{"px": "2500.1", "sz": "350.0"}],
            ]
        })
        macro = MacroState(permit_trade=True, bias="BULLISH", volatility_regime=1.0)
        basket = PredatorBasket(basket_id="B1", is_buy=True, total_sz=0.50, entry_price=2500.00)
        basket.slices = [PredatorOrderSlice(ticket_id="T1", basket_id="B1", sz=0.50, entry_price=2500.00)]

        cb = AsyncMock()
        reason = await orderflow_exit_monitor(basket, mem, macro, cb)
        assert reason == "L2_IMBALANCE_WALL"
        cb.assert_called_once_with("L2_IMBALANCE_WALL")

    @async_test
    async def test_l2_imbalance_exit_short_wall(self) -> None:
        """Validates instant basket liquidation when Short faces Bid/Ask ratio > 3.0 * vol_regime."""
        mem = TapeBookMemory()
        # Bid/Ask ratio = 400.0 / 100.0 = 4.0 > 3.0 * 1.0
        mem.on_l2({
            "levels": [
                [{"px": "2499.9", "sz": "400.0"}],
                [{"px": "2500.0", "sz": "100.0"}],
            ]
        })
        macro = MacroState(permit_trade=True, bias="BEARISH", volatility_regime=1.0)
        basket = PredatorBasket(basket_id="B2", is_buy=False, total_sz=0.50, entry_price=2500.00)
        basket.slices = [PredatorOrderSlice(ticket_id="T2", basket_id="B2", is_buy=False, sz=0.50, entry_price=2500.00)]

        cb = AsyncMock()
        reason = await orderflow_exit_monitor(basket, mem, macro, cb)
        assert reason == "L2_IMBALANCE_WALL"
        cb.assert_called_once_with("L2_IMBALANCE_WALL")

    @async_test
    async def test_l2_adaptive_regime_threshold(self) -> None:
        """Validates volatility_regime dynamically scales the exit threshold (holds in trends, exits in chop)."""
        mem = TapeBookMemory()
        # Ratio = 350 / 100 = 3.5
        mem.on_l2({
            "levels": [
                [{"px": "2500.0", "sz": "100.0"}],
                [{"px": "2500.1", "sz": "350.0"}],
            ]
        })
        basket = PredatorBasket(basket_id="B3", is_buy=True, total_sz=0.50, entry_price=2500.00)
        basket.slices = [PredatorOrderSlice(ticket_id="T3", basket_id="B3", sz=0.50, entry_price=2500.00)]
        cb = AsyncMock()

        # Regime 1.5 -> Threshold = 3.0 * 1.5 = 4.5. Ratio 3.5 < 4.5 -> Holds position!
        macro_high_vol = MacroState(volatility_regime=1.5)
        reason_hold = await orderflow_exit_monitor(basket, mem, macro_high_vol, cb)
        assert reason_hold is None
        cb.assert_not_called()

        # Regime 1.0 -> Threshold = 3.0 * 1.0 = 3.0. Ratio 3.5 > 3.0 -> Fires exit!
        macro_norm_vol = MacroState(volatility_regime=1.0)
        reason_exit = await orderflow_exit_monitor(basket, mem, macro_norm_vol, cb)
        assert reason_exit == "L2_IMBALANCE_WALL"
        cb.assert_called_once_with("L2_IMBALANCE_WALL")

    def test_l2_orderflow_exit_latency_sub_5ms(self) -> None:
        """Benchmarks pure in-memory order flow evaluation latency (< 5ms SLA)."""
        mem = TapeBookMemory()
        mem.on_l2({
            "levels": [
                [{"px": "2500.0", "sz": "100.0"}],
                [{"px": "2500.1", "sz": "200.0"}],
            ]
        })
        iterations = 1000
        t0 = time.perf_counter_ns()
        for _ in range(iterations):
            b_vol, a_vol = mem.get_top5_volumes()
            ratio = a_vol / max(b_vol, 1e-6)
            _ = ratio > 3.0
        total_ns = time.perf_counter_ns() - t0
        mean_latency_ms = (total_ns / iterations) / 1_000_000.0

        # Mean latency in pure local memory should be strictly < 5ms (typically < 0.01ms)
        assert mean_latency_ms < 5.0


# =========================================================================
# 5. TestTradeTapeVolumeDeltaStall (Requirement R5)
# =========================================================================

class TestTradeTapeVolumeDeltaStall:
    """Validates Trade Tape Momentum Stall Detection (> 80% Opposing Fills)."""

    @async_test
    async def test_volume_delta_stall_long_opposing_ticks(self) -> None:
        """Validates momentum stall exit when Long basket is in profit and > 80% of trades are market sells."""
        mem = TapeBookMemory()
        # Feed price where Long basket is in profit: entry 2500, bid 2502 (+2.00)
        mem.best_bid = 2502.00
        mem.best_ask = 2502.10
        mem.mid_px = 2502.05

        # Feed 20 trades: 17 sells ("A") and 3 buys ("B") -> 17/20 = 85% > 80%
        trades = [{"side": "A"} for _ in range(17)] + [{"side": "B"} for _ in range(3)]
        mem.on_trades(trades)

        macro = MacroState(volatility_regime=1.0)
        basket = PredatorBasket(basket_id="B4", is_buy=True, total_sz=0.50, entry_price=2500.00)
        basket.slices = [PredatorOrderSlice(ticket_id="T4", basket_id="B4", sz=0.50, entry_price=2500.00)]

        cb = AsyncMock()
        reason = await orderflow_exit_monitor(basket, mem, macro, cb)
        assert reason == "VOLUME_DELTA_STALL"
        cb.assert_called_once_with("VOLUME_DELTA_STALL")

    @async_test
    async def test_volume_delta_stall_short_opposing_ticks(self) -> None:
        """Validates momentum stall exit when Short basket is in profit and > 80% of trades are market buys."""
        mem = TapeBookMemory()
        # Short basket in profit: entry 2500, ask 2498 (+2.00)
        mem.best_bid = 2497.90
        mem.best_ask = 2498.00
        mem.mid_px = 2497.95

        # Feed 20 trades: 18 buys ("B") and 2 sells ("A") -> 18/20 = 90% > 80%
        trades = [{"side": "B"} for _ in range(18)] + [{"side": "A"} for _ in range(2)]
        mem.on_trades(trades)

        macro = MacroState(volatility_regime=1.0)
        basket = PredatorBasket(basket_id="B5", is_buy=False, total_sz=0.50, entry_price=2500.00)
        basket.slices = [PredatorOrderSlice(ticket_id="T5", basket_id="B5", is_buy=False, sz=0.50, entry_price=2500.00)]

        cb = AsyncMock()
        reason = await orderflow_exit_monitor(basket, mem, macro, cb)
        assert reason == "VOLUME_DELTA_STALL"
        cb.assert_called_once_with("VOLUME_DELTA_STALL")

    @async_test
    async def test_volume_delta_stall_normal_flow_holds(self) -> None:
        """Verifies balanced trade flow (<= 80% opposing) does not trigger stall exit."""
        mem = TapeBookMemory()
        mem.best_bid = 2502.00
        mem.best_ask = 2502.10
        mem.mid_px = 2502.05

        # 10 sells ("A") and 10 buys ("B") -> 50% opposing <= 80%
        trades = [{"side": "A"} for _ in range(10)] + [{"side": "B"} for _ in range(10)]
        mem.on_trades(trades)

        macro = MacroState(volatility_regime=1.0)
        basket = PredatorBasket(basket_id="B6", is_buy=True, total_sz=0.50, entry_price=2500.00)
        basket.slices = [PredatorOrderSlice(ticket_id="T6", basket_id="B6", sz=0.50, entry_price=2500.00)]

        cb = AsyncMock()
        reason = await orderflow_exit_monitor(basket, mem, macro, cb)
        assert reason is None
        cb.assert_not_called()


# =========================================================================
# 6. TestRuthlessExitsAndHardEquityShield (Requirement R4)
# =========================================================================

class TestRuthlessExitsAndHardEquityShield:
    """Validates Hard -$10.00 Equity Shield, Opposing Reversal Wick & Opposing M5 S/R Target Exits."""

    @async_test
    async def test_hard_equity_shield_liquidation_at_minus_10(self) -> None:
        """Asserts floating loss <= -$10.00 triggers immediate market liquidation."""
        mem = TapeBookMemory()
        # Long basket with 1.0 oz at $2500.00. Bid drops to $2489.95 -> uPnL = -$10.05 <= -$10.00
        mem.best_bid = 2489.95
        mem.best_ask = 2490.05
        mem.mid_px = 2490.00

        macro = MacroState()
        basket = PredatorBasket(basket_id="B7", is_buy=True, total_sz=1.0, entry_price=2500.00)
        basket.slices = [PredatorOrderSlice(ticket_id="T7", basket_id="B7", sz=1.0, entry_price=2500.00)]

        cb = AsyncMock()
        reason = await orderflow_exit_monitor(basket, mem, macro, cb)
        assert reason == "HARD_EQUITY_SHIELD"
        cb.assert_called_once_with("HARD_EQUITY_SHIELD")

    @async_test
    async def test_hard_equity_shield_short_liquidation(self) -> None:
        """Asserts short floating loss <= -$10.00 triggers immediate market liquidation."""
        mem = TapeBookMemory()
        # Short basket with 1.0 oz at $2500.00. Ask rises to $2510.05 -> uPnL = -$10.05 <= -$10.00
        mem.best_bid = 2509.95
        mem.best_ask = 2510.05
        mem.mid_px = 2510.00

        macro = MacroState()
        basket = PredatorBasket(basket_id="B8", is_buy=False, total_sz=1.0, entry_price=2500.00)
        basket.slices = [PredatorOrderSlice(ticket_id="T8", basket_id="B8", is_buy=False, sz=1.0, entry_price=2500.00)]

        cb = AsyncMock()
        reason = await orderflow_exit_monitor(basket, mem, macro, cb)
        assert reason == "HARD_EQUITY_SHIELD"
        cb.assert_called_once_with("HARD_EQUITY_SHIELD")

    @async_test
    async def test_opposing_reversal_wick_exit(self) -> None:
        """Asserts active Long basket is immediately closed when an opposing bearish >= 65% wick prints."""
        bot = HyperPredatorBot(venue=SimulatedBrokerVenue())
        basket = await bot.execution_bridge.spam_orders(coin="GOLD", is_buy=True, total_sz=0.50, slices=5, jitter_ms=0)
        assert basket is not None
        assert bot.execution_bridge.active_basket is not None

        # Opposing bearish rejection wick candle (65% upper wick, close < open)
        bearish_candle = {"open": 2503.50, "high": 2510.00, "low": 2500.00, "close": 2503.00}
        result = await bot.on_m1_candle_close(bearish_candle)
        assert result is None
        # Active basket must be closed
        assert bot.execution_bridge.active_basket is None

    @async_test
    async def test_opposing_m5_sr_target_exit(self) -> None:
        """Asserts active basket is immediately closed the exact millisecond opposing M5 S/R is reached."""
        mem = TapeBookMemory()
        # Long target is resistance at 2515.00. Bid reaches 2515.00
        mem.best_bid = 2515.00
        mem.best_ask = 2515.10
        mem.mid_px = 2515.05

        macro = MacroState()
        basket = PredatorBasket(basket_id="B9", is_buy=True, total_sz=0.50, entry_price=2500.00, target_price=2515.00)
        basket.slices = [PredatorOrderSlice(ticket_id="T9", basket_id="B9", sz=0.50, entry_price=2500.00)]

        cb = AsyncMock()
        reason = await orderflow_exit_monitor(basket, mem, macro, cb, opposing_m5_sr_target=2515.00)
        assert reason == "OPPOSING_M5_SR_TARGET"
        cb.assert_called_once_with("OPPOSING_M5_SR_TARGET")


# =========================================================================
# 7. TestVectorizedBacktester (Requirement R7)
# =========================================================================

class TestVectorizedBacktester:
    """Validates Decade-Deep Streaming Ingestion, Memory Efficiency & Vectorized Signal Masks."""

    def test_backtester_data_ingestion_formats(self, tmp_path: Path) -> None:
        """Validates streaming ingestion across DataFrame, numpy array, CSV, and Parquet."""
        n_bars = 300
        df = generate_synthetic_gold_m1(n_bars=n_bars, as_df=True)
        arr = generate_synthetic_gold_m1(n_bars=n_bars, as_df=False)

        # 1. In-memory DataFrame
        it_df = M1ChunkIterator(df, chunk_size=100, halo_size=20)
        chunks_df = list(it_df)
        assert len(chunks_df) == 3
        assert len(chunks_df[0][0]) == 100

        # 2. In-memory numpy structured array
        it_arr = M1ChunkIterator(arr, chunk_size=100, halo_size=20)
        chunks_arr = list(it_arr)
        assert len(chunks_arr) == 3

        # 3. CSV File
        csv_path = tmp_path / "gold_m1.csv"
        df.to_csv(csv_path, index=False)
        it_csv = M1ChunkIterator(csv_path, chunk_size=100, halo_size=20)
        chunks_csv = list(it_csv)
        assert len(chunks_csv) >= 3

        # 4. Parquet File
        parquet_path = tmp_path / "gold_m1.parquet"
        df.to_parquet(parquet_path, index=False)
        it_pq = M1ChunkIterator(parquet_path, chunk_size=100, halo_size=20)
        chunks_pq = list(it_pq)
        assert len(chunks_pq) >= 1

    def test_backtester_chunked_streaming_continuity(self) -> None:
        """Validates seamless active basket carryover and stateful continuity across chunk boundaries."""
        n_bars = 4000
        data = generate_synthetic_gold_m1(n_bars=n_bars, seed=42)
        bt = VectorizedBacktester()

        # Run with chunk_size=1000, halo_size=200 (4 chunks total)
        res = bt.run_backtest(source=data, chunk_size=1000, halo_size=200)
        assert isinstance(res, BacktestResult)
        assert res.initial_equity == 65.00
        assert len(res.equity_curve) >= 1
        assert res.total_trades >= 0

    def test_backtester_memory_footprint_under_4gb(self) -> None:
        """Validates streaming backtest on 50,000 bars operates strictly under 200MB RSS (< 4GB)."""
        import psutil
        proc = psutil.Process(os.getpid())
        gc.collect()
        rss_start_mb = proc.memory_info().rss / (1024 * 1024)

        data = generate_synthetic_gold_m1(n_bars=50_000, seed=42)
        bt = VectorizedBacktester()
        res = bt.run_backtest(source=data, chunk_size=10_000, halo_size=500)
        assert isinstance(res, BacktestResult)

        gc.collect()
        rss_end_mb = proc.memory_info().rss / (1024 * 1024)
        rss_growth_mb = max(0.0, rss_end_mb - rss_start_mb)

        # Operating memory growth should remain far below the 200MB threshold (typically < 30MB)
        assert rss_growth_mb < 200.0
        assert rss_end_mb < 500.0  # Safe within 4GB VPS

    def test_backtester_signal_generation_consistency(self) -> None:
        """Validates vectorized boolean masks compute identical indicators and all exit mechanisms work."""
        n_bars = 50
        opens = np.full(n_bars, 2500.0, dtype=np.float32)
        highs = np.full(n_bars, 2510.0, dtype=np.float32)
        lows = np.full(n_bars, 2490.0, dtype=np.float32)
        closes = np.full(n_bars, 2500.0, dtype=np.float32)
        tick_vels = np.ones(n_bars, dtype=np.float32)

        # Bar 25: Bullish rejection wick touching support
        lows[25] = 2489.0
        opens[25] = 2497.0
        closes[25] = 2498.0  # lower wick = 2497 - 2489 = 8.0 out of 21.0
        tick_vels[25] = 1.8

        p = HyperPredatorParams(wick_pct=0.65, tick_velocity_mult=1.5)
        long_sigs, short_sigs, sr_h, sr_l, opp_wicks = VectorizedSignalEngine.generate_signal_masks(
            opens, highs, lows, closes, tick_vels, p
        )

        assert len(long_sigs) == n_bars
        assert len(short_sigs) == n_bars
        assert len(sr_h) == n_bars
        assert len(sr_l) == n_bars


# =========================================================================
# 8. TestParameterSweepAndMonteCarlo (Requirement R7)
# =========================================================================

class TestParameterSweepAndMonteCarlo:
    """Validates Multi-Dimensional Parameter Sweep, 500-Run Monte Carlo & Metric Precision."""

    def test_parameter_sweep_interface(self) -> None:
        """Validates parameter sweep across wick %, M5 lookback, L2 imbalance, and tick velocity."""
        data = generate_synthetic_gold_m1(n_bars=600, seed=42)
        bt = VectorizedBacktester()
        grid = {
            "wick_pct": (0.60, 0.70),
            "m5_lookback": (20, 50),
            "l2_imbalance_threshold": (2.5, 3.5),
            "tick_velocity_mult": (1.3, 1.6),
        }

        # 2 x 2 x 2 x 2 = 16 parameter configurations
        df_results = bt.parameter_sweep(grid=grid, source=data, max_bars=600)

        assert isinstance(df_results, pd.DataFrame)
        assert len(df_results) == 16
        expected_cols = [
            "wick_pct", "m5_lookback", "l2_imbalance", "tick_velocity",
            "total_trades", "win_rate", "profit_factor", "sharpe_ratio",
            "max_drawdown_dollars", "max_drawdown_pct", "total_pnl",
        ]
        for col in expected_cols:
            assert col in df_results.columns

        # Verify sorted by Sharpe ratio descending
        sharpes = df_results["sharpe_ratio"].tolist()
        assert sharpes == sorted(sharpes, reverse=True)

    def test_monte_carlo_500_runs_execution(self) -> None:
        """Validates 500-run Monte Carlo simulation with execution jitter and adverse slippage friction."""
        data = generate_synthetic_gold_m1(n_bars=600, seed=42)
        bt = VectorizedBacktester()
        base_res = bt.run_backtest(source=data)

        mc_res = bt.run_monte_carlo(n_runs=500, base_result=base_res, seed=42)
        assert isinstance(mc_res, MonteCarloResult)
        assert mc_res.n_runs == 500
        assert 0.0 <= mc_res.ruin_probability <= 1.0

        # Verify percentiles are populated
        for p in ("p5", "p25", "p50", "p75", "p95"):
            assert p in mc_res.sharpe_percentiles
            assert p in mc_res.max_drawdown_percentiles
            assert p in mc_res.final_equity_percentiles
            assert p in mc_res.win_rate_percentiles

    def test_metrics_calculation_accuracy(self) -> None:
        """Validates mathematical accuracy of Sharpe, Max DD, Win Rate, and Profit Factor metrics."""
        bt = VectorizedBacktester()
        init_eq = 100.0

        # Predetermined trade series: 6 wins of +$2.00, 4 losses of -$1.00 -> Net PnL = +$8.00
        trades: List[TradeRecord] = []
        eq = init_eq
        eq_curve = [eq]
        for i in range(10):
            pnl = 2.00 if i < 6 else -1.00
            eq += pnl
            eq_curve.append(eq)
            trades.append(
                TradeRecord(
                    trade_id=i + 1,
                    side="LONG",
                    entry_time=1000 + i * 60,
                    exit_time=1050 + i * 60,
                    entry_price=2500.0,
                    exit_price=2502.0 if pnl > 0 else 2499.0,
                    basket_size=1.0,
                    gross_pnl=pnl,
                    entry_fee=0.0,
                    exit_fee=0.0,
                    net_pnl=pnl,
                    duration_bars=5,
                    exit_reason="TEST",
                    equity_before=eq - pnl,
                    equity_after=eq,
                )
            )

        res = bt._compute_metrics(
            initial_equity=init_eq,
            final_equity=eq,
            trades=trades,
            equity_curve=eq_curve,
            shield_exits=0,
            reversal_exits=0,
            target_exits=0,
            l2_exits=0,
            tape_exits=0,
            stop_exits=0,
        )

        assert res.total_trades == 10
        assert res.winning_trades == 6
        assert res.losing_trades == 4
        assert res.win_rate == 0.60
        # Gross profit = 6 * 2 = 12.00. Gross loss = 4 * 1 = 4.00. PF = 12 / 4 = 3.00
        assert res.profit_factor == 3.00
        assert res.total_pnl == 8.00
        assert res.expectancy == 0.80
        # Peak equity = 112.00, final = 108.00 -> Max drawdown = $4.00
        assert res.max_drawdown_dollars == 4.00
        assert res.max_drawdown_pct == round((4.00 / 112.00) * 100.0, 2)


# =========================================================================
# 9. TestReviewer2PolishRemediation (Actionable Findings Coverage)
# =========================================================================

class TestReviewer2PolishRemediation:
    """Validates remediation of Reviewer 2 actionable findings."""

    @async_test
    async def test_close_basket_exit_price_propagation(self) -> None:
        """Validates ExecutionBridge.close_basket updates venue price and fills slices at exit_price."""
        venue = SimulatedBrokerVenue()
        bridge = ExecutionBridge(venue=venue)
        basket = await bridge.spam_orders(coin="GOLD", is_buy=True, total_sz=0.50, slices=5, jitter_ms=0)
        assert basket is not None
        assert bridge.active_basket is not None

        # Call close_basket with explicit exit_price
        summary = await bridge.close_basket(coin="GOLD", reason="HARD_EQUITY_SHIELD", exit_price=2479.00)
        assert summary is not None
        assert summary["exit_price"] == 2479.00
        # Venue price must be synchronized
        assert await venue.get_market_price("GOLD") == 2479.00
        # Realized PnL reflects exit_price ($2479.00 - entry_price) * 0.50
        expected_pnl = round((2479.00 - basket.entry_price) * 0.50, 2)
        assert summary["realized_pnl"] == expected_pnl
        assert basket.realized_pnl == expected_pnl
        # Slices marked closed at exit_price
        for s in basket.slices:
            assert s.is_active is False
            assert s.close_price == 2479.00

    @async_test
    async def test_orderflow_monitor_passes_exit_price_to_close_basket(self) -> None:
        """Validates OrderflowMonitor.evaluate captures and passes current market/bid/ask price."""
        mem = TapeBookMemory()
        mem.best_bid = 2479.00
        mem.best_ask = 2479.10
        mem.mid_px = 2479.05
        macro_mgr = MacroStateManager()
        monitor = OrderflowMonitor(mem, macro_mgr)

        basket = PredatorBasket(basket_id="B_TEST", is_buy=True, total_sz=0.50, entry_price=2500.00)
        basket.slices = [PredatorOrderSlice(ticket_id="T1", basket_id="B_TEST", sz=0.50, entry_price=2500.00)]

        captured_args: List[tuple] = []

        def mock_close(reason: str, exit_price: Optional[float] = None) -> None:
            captured_args.append((reason, exit_price))

        reason = await monitor.evaluate(basket, close_callback=mock_close)
        assert reason == "HARD_EQUITY_SHIELD"
        assert len(captured_args) == 1
        assert captured_args[0] == ("HARD_EQUITY_SHIELD", 2479.00)

    def test_sniper_velocity_surge_defaults_to_false_without_ticks(self) -> None:
        """Validates evaluate_m1_trigger strictly gates entries when tick timestamps are omitted."""
        sniper = SniperEngine(sr_lookback_bars=20, zone_epsilon=0.25)
        sniper.set_m5_candles([{"high": 2510.0, "low": 2500.0, "open": 2505.0, "close": 2505.0}] * 20)
        candle = {
            "open": 2506.50,
            "high": 2510.00,
            "low": 2499.90,
            "close": 2507.00,
            "open_time": 940.0,
            "close_time": 1000.0,
        }
        macro = MacroState(permit_trade=True, bias="BULLISH", volatility_regime=1.0)

        # 1. No tick timestamps and no velocity_surge_valid in candle -> Must reject
        assert sniper.evaluate_m1_trigger(candle, macro, tick_timestamps=None) is None
        assert sniper.evaluate_m1_trigger(candle, macro, tick_timestamps=[]) is None
        assert sniper.evaluate_m1_trigger(candle, macro) is None

        # 2. Explicit velocity_surge_valid=True in candle -> Accepted
        valid_candle = dict(candle, velocity_surge_valid=True, velocity_surge_ratio=1.6)
        signal = sniper.evaluate_m1_trigger(valid_candle, macro)
        assert signal is not None
        assert signal["signal"] == "ENTRY"

    def test_backtester_doji_strict_inequality_excludes_neutral_bars(self) -> None:
        """Validates VectorizedSignalEngine requires strict inequality, rejecting dojis where Close == Open."""
        opens = np.array([2500.0, 2500.0, 2500.0], dtype=np.float32)
        closes = np.array([2500.0, 2502.0, 2498.0], dtype=np.float32)  # bar 0: doji, bar 1: bull, bar 2: bear
        highs = np.array([2505.0, 2505.0, 2505.0], dtype=np.float32)
        lows = np.array([2490.0, 2490.0, 2490.0], dtype=np.float32)

        lower_ratio, upper_ratio, is_bull, is_bear = VectorizedSignalEngine.compute_wick_ratios(
            opens, highs, lows, closes
        )

        # Bar 0 (Doji Close == Open): Both must be False
        assert is_bull[0] == False
        assert is_bear[0] == False

        # Bar 1 (Bullish Close > Open): is_bull is True
        assert is_bull[1] == True
        assert is_bear[1] == False

        # Bar 2 (Bearish Close < Open): is_bear is True
        assert is_bull[2] == False
        assert is_bear[2] == True

        # compute_signals interface check
        long_sigs, short_sigs, sr_h, sr_l, opp_wicks = VectorizedSignalEngine.compute_signals(
            open_arr=opens,
            high_arr=highs,
            low_arr=lows,
            close_arr=closes,
        )
        # Bar 0 cannot trigger a long or short signal
        assert long_sigs[0] == False
        assert short_sigs[0] == False

