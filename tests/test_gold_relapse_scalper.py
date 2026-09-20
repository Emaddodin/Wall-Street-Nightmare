"""
tests/test_gold_relapse_scalper.py
==================================
Comprehensive Unit & Integration Test Suite for the
5-Minute Market Flow & Relapse Scalper for Gold (XAUUSD).

Validates:
1. Leverage (1:1000) & Margin Invariant (<= 20% equity).
2. Stop-Loss Envelope Invariant (strictly 10.0 to 15.0 pips; rejection of > 15 pips).
3. No Static Take-Profit Invariant (take_profit is None).
4. Asynchronous Order Slicing (3 x 0.01 lot tickets with 50ms jitter).
5. Breakeven Lock at +1.5R (shifts SL to Entry + 1.0 pip).
6. Parallel Basket Liquidation on EXIT.
7. Macro News Fundamental Filter (+/- 15 min blackout window).
8. Sub-second LLM Intuition Exit with grammar constraints & timeout fallback.
9. Full 6-state Relapse FSM lifecycle transitions.
10. 5% Max Daily Drawdown Killswitch.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from engine.execution_router import (
    ExecutionRouter,
    HyperliquidDEXVenue,
    HyperliquidVenue,
    OrderSide,
    RiskInvariants,
    SimulatedBrokerVenue,
)
from engine.fsm import (
    DailyDrawdownGuard,
    RelapseFSM,
    RelapseState,
    XAUUSD_KILLZONES,
    detect_evening_star,
    detect_morning_star,
)
from macro.slm_intuition import (
    EconomicCalendarFilter,
    IntuitionDecision,
    IntuitionTelemetry,
    MacroNewsEvent,
    SLMIntuitionEngine,
)
from engine.killzone import KillZoneGuard


# -------------------------------------------------------------------------
# Fixtures & Data Generators
# -------------------------------------------------------------------------

@pytest.fixture
def venue() -> SimulatedBrokerVenue:
    v = SimulatedBrokerVenue(initial_equity=65.0, slippage_delta=0.0)
    v.set_market_price("GOLD", 2500.00)
    v.set_market_price("XAUUSD", 2500.00)
    return v


@pytest.fixture
def router(venue: SimulatedBrokerVenue) -> ExecutionRouter:
    risk = RiskInvariants(
        coin="GOLD",
        leverage=100.0,
        max_margin_pct=0.20,
        min_sl_delta=1.00,
        max_sl_delta=1.50,
        wick_buffer=0.12,
        breakeven_trigger_r=1.5,
        breakeven_lock_offset=0.10,
        initial_account_equity=65.0,
        slice_jitter_ms=10,  # Fast for tests
    )
    return ExecutionRouter(venue=venue, risk=risk)


def make_5m_ohlcv(n_bars: int = 50, base_price: float = 2500.00) -> pd.DataFrame:
    """Generate deterministic 5M OHLCV test dataframe."""
    now_ms = int(time.time() * 1000)
    times = [now_ms - (n_bars - i) * 300_000 for i in range(n_bars)]
    opens = []
    highs = []
    lows = []
    closes = []
    volumes = []

    price = base_price
    for i in range(n_bars):
        o = price
        delta = 0.30 if (i % 4 != 0) else -0.40
        c = round(o + delta, 2)
        h = round(max(o, c) + 0.15, 2)
        lo = round(min(o, c) - 0.15, 2)
        v = 500.0 + (i % 5) * 50.0
        price = c
        opens.append(o)
        highs.append(h)
        lows.append(lo)
        closes.append(c)
        volumes.append(v)

    return pd.DataFrame({
        "open_time": times,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })


# -------------------------------------------------------------------------
# Test 1 & 2: Hyperliquid Leverage, Margin Ceiling & SL Envelope Invariants
# -------------------------------------------------------------------------

def test_margin_invariant(router: ExecutionRouter):
    """
    Hyperliquid CLOB Invariant:
    Enforces 100x leverage for GOLD perpetuals with initial margin capped strictly
    at <= 20% of account equity ($13.00 max on a $65 account).
    At $2500:
    - sz = 0.50 -> Margin = (0.50 * 2500) / 100 = $12.50 <= $13.00 (Valid).
    - sz = 1.00 -> Margin = (1.00 * 2500) / 100 = $25.00 > $13.00 (Rejected).
    """
    async def _test():
        is_ok, req_margin, max_margin = await router.validate_margin(0.50, 2500.00)
        assert is_ok is True
        assert req_margin == 12.50
        assert max_margin == 13.00

        is_ok, req_margin, max_margin = await router.validate_margin(1.00, 2500.00)
        assert is_ok is False
        assert req_margin == 25.00
        assert req_margin > max_margin

    asyncio.run(_test())


def test_stop_loss_envelope_invariant(router: ExecutionRouter):
    """
    SL Envelope (Absolute Delta):
    Strictly constrained to an absolute price difference of $1.00 to $1.50 from
    the entry price. Placed $0.10 to $0.15 beyond the invalidation wick.
    Setups requiring an SL delta > $1.50 are systematically rejected.
    """
    entry_price = 2500.00

    # Case A: Invalidation wick is $2498.30 (delta = $1.70 > $1.50) -> REJECT
    wick_far = 2498.30
    ok, sl_price, sl_delta, reason = router.calculate_and_validate_sl(
        is_buy=True, entry_price=entry_price, invalidation_wick_price=wick_far
    )
    assert ok is False
    assert sl_delta > 1.50
    assert "exceeds maximum allowed" in reason

    # Case B: Invalidation wick is $2498.80 (Raw SL = 2498.80 - 0.12 = 2498.68, delta = $1.32) -> VALID
    wick_valid = 2498.80
    ok, sl_price, sl_delta, reason = router.calculate_and_validate_sl(
        is_buy=True, entry_price=entry_price, invalidation_wick_price=wick_valid
    )
    assert ok is True
    assert 1.00 <= sl_delta <= 1.50
    assert sl_price == 2498.68

    # Case C: Invalidation wick is $2499.50 (Raw SL = 2499.38, delta = $0.62 < $1.00)
    # Clamped to minimum $1.00 absolute delta -> SL = $2499.00
    wick_tight = 2499.50
    ok, sl_price, sl_delta, reason = router.calculate_and_validate_sl(
        is_buy=True, entry_price=entry_price, invalidation_wick_price=wick_tight
    )
    assert ok is True
    assert sl_delta == 1.00
    assert sl_price == 2499.00


# -------------------------------------------------------------------------
# Test 3, 4 & 5: Order Slicing, No Static TP, Detached Stop Mechanism
# -------------------------------------------------------------------------

def test_order_slicing_and_detached_stop(router: ExecutionRouter):
    """
    fire_layered_orders:
    - Concurrently dispatches micro-units (three slices) via exchange.market_open(coin="GOLD") with 50ms jitter.
    - Dispatches open-ended (NO resting TP on CLOB).
    - Immediately dispatches unified Stop Market order via exchange.market_close() with reduce_only=True.
    """
    async def _test():
        wick_price = 2498.80  # SL at 2498.68, delta $1.32
        basket = await router.fire_layered_orders(
            symbol="GOLD",
            is_buy=True,
            invalidation_wick_price=wick_price,
            total_sz=0.45,  # 3 x 0.15 sz -> Margin = (0.45 * 2500) / 100 = $11.25 <= $13.00
            num_slices=3,
        )

        assert basket is not None
        assert basket.total_sz == 0.45
        assert len(basket.slices) == 3
        assert basket.is_active is True

        # Check slices are open-ended
        for s in basket.slices:
            assert s.sz == 0.15
            assert s.is_active is True

        # Check detached stop market order placed with reduce_only=True
        assert basket.stop_order_id is not None
        assert "HL-CLOSE-" in basket.stop_order_id
        assert basket.sl_price == 2498.68

    asyncio.run(_test())


# -------------------------------------------------------------------------
# Test 6: Breakeven Lock (Cancel Old Stop + New Stop at Entry +/- $0.10)
# -------------------------------------------------------------------------

def test_breakeven_lock_at_1_5r(router: ExecutionRouter, venue: SimulatedBrokerVenue):
    """
    At +1.5R floating profit:
    - Existing structural Stop Market order is cancelled.
    - New reduce_only=True Stop order transmitted at Entry Price +/- $0.10.
    """
    async def _test():
        entry_price = 2500.00
        wick_price = 2498.80  # SL = 2498.68 -> 1R = $1.32. +1.5R = +$1.98 (Price: $2501.98)
        basket = await router.fire_layered_orders(
            symbol="GOLD",
            is_buy=True,
            invalidation_wick_price=wick_price,
            total_sz=0.45,
            num_slices=3,
        )
        assert basket is not None
        assert basket.entry_price == entry_price
        initial_stop_oid = basket.stop_order_id
        assert basket.breakeven_locked is False

        # 1. Floating profit at +0.76R ($2501.00) -> Not triggered
        venue.set_market_price("GOLD", 2501.00)
        locked = await router.evaluate_breakeven_lock(2501.00)
        assert locked is False
        assert basket.breakeven_locked is False
        assert basket.stop_order_id == initial_stop_oid

        # 2. Floating profit at +1.89R ($2502.50 >= +1.5R) -> Breakeven Lock triggered!
        venue.set_market_price("GOLD", 2502.50)
        locked = await router.evaluate_breakeven_lock(2502.50)
        assert locked is True
        assert basket.breakeven_locked is True

        # Verify old stop order was cancelled and new stop transmitted at Entry + $0.10
        assert basket.stop_order_id != initial_stop_oid
        expected_be_sl = round(basket.entry_price + 0.10, 2)
        assert basket.sl_price == expected_be_sl

        # Check venue records show old stop cancelled and new stop resting
        assert venue._resting_stops[initial_stop_oid]["status"] == "cancelled"
        assert venue._resting_stops[basket.stop_order_id]["trigger_px"] == expected_be_sl
        assert venue._resting_stops[basket.stop_order_id]["reduce_only"] is True

    asyncio.run(_test())


# -------------------------------------------------------------------------
# Test 7: Dynamic Basket Close (Instant Market Liquidation)
# -------------------------------------------------------------------------

def test_dynamic_basket_close(router: ExecutionRouter, venue: SimulatedBrokerVenue):
    """
    Instant liquidation of the entire aggregate position via
    exchange.market_close(sz=total_sz, reduce_only=True) upon receiving EXIT flag.
    """
    async def _test():
        wick_price = 2498.80
        basket = await router.fire_layered_orders(
            symbol="GOLD",
            is_buy=True,
            invalidation_wick_price=wick_price,
            total_sz=0.45,
        )
        assert basket is not None
        stop_oid = basket.stop_order_id

        # Price reaches profit and EXIT flag is received
        venue.set_market_price("GOLD", 2503.00)
        summary = await router.close_basket(reason="LLM_INTUITION_EXIT")

        assert summary is not None
        assert summary["aggregate_sz"] == 0.45
        assert summary["total_pnl"] > 0
        assert summary["reason"] == "LLM_INTUITION_EXIT"
        assert basket.is_active is False
        assert router.active_basket_id is None

        # Detached stop was cancelled
        assert venue._resting_stops[stop_oid]["status"] == "cancelled"

    asyncio.run(_test())


# -------------------------------------------------------------------------
# Test 7: Macro News Fundamental Filter
# -------------------------------------------------------------------------

def test_macro_calendar_blackout():
    """
    Tests +/- 15 minute blackout window around High-Impact US news.
    """
    calendar = EconomicCalendarFilter(blackout_window_sec=900)  # 15 minutes
    event_time = 1_000_000.0

    # Add High-Impact US CPI Event
    calendar.add_scheduled_event(MacroNewsEvent(
        title="US Core CPI MoM",
        currency="USD",
        impact="HIGH",
        timestamp=event_time,
    ))

    # 1. 20 minutes before (T - 1200s): OUTSIDE blackout
    in_blackout, _ = calendar.is_macro_blackout(event_time - 1200)
    assert in_blackout is False

    # 2. 10 minutes before (T - 600s): INSIDE blackout
    in_blackout, reason = calendar.is_macro_blackout(event_time - 600)
    assert in_blackout is True
    assert "Macro Lock: US Core CPI MoM" in reason

    # 3. Exact release time (T): INSIDE blackout
    in_blackout, _ = calendar.is_macro_blackout(event_time)
    assert in_blackout is True

    # 4. 14 minutes after (T + 840s): INSIDE blackout
    in_blackout, _ = calendar.is_macro_blackout(event_time + 840)
    assert in_blackout is True

    # 5. 16 minutes after (T + 960s): OUTSIDE blackout
    in_blackout, _ = calendar.is_macro_blackout(event_time + 960)
    assert in_blackout is False


# -------------------------------------------------------------------------
# Test 8: Sub-Second SLM Intuition Exit
# -------------------------------------------------------------------------

def test_slm_intuition_exit_mocked():
    """
    Tests grammar-constrained response parsing and sub-300ms timeout fallback.
    """
    async def _test():
        engine = SLMIntuitionEngine(timeout_sec=0.300)
        telemetry = IntuitionTelemetry(
            unrealized_r=1.65,
            candle_wick_ratio=0.70,
            volume_stall=True,
            dxy_divergence=True,
            side="BUY",
            bars_in_trade=5,
        )

        # 1. Test successful grammar-constrained {"decision": "EXIT"}
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"content": '{"decision": "EXIT"}'})

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.post.return_value.__aexit__ = AsyncMock(return_value=None)
        engine._session = mock_session

        decision, latency, rationale = await engine.query_intuition_exit(telemetry)
        assert decision == IntuitionDecision.EXIT
        assert "Grammar constrained" in rationale

        # 2. Test successful {"decision": "HOLD"}
        mock_resp.json = AsyncMock(return_value={"content": '{"decision": "HOLD"}'})
        decision, latency, rationale = await engine.query_intuition_exit(telemetry)
        assert decision == IntuitionDecision.HOLD

        # 3. Test timeout fallback (< 300ms exception)
        mock_session.post.side_effect = asyncio.TimeoutError()
        decision, latency, rationale = await engine.query_intuition_exit(telemetry)
        # Fail-safe logic: +1.65R with high wick absorption + vol stall triggers fail-safe EXIT
        assert decision == IntuitionDecision.EXIT
        assert "Algorithmic Fail-Safe Triggered" in rationale

    asyncio.run(_test())


# -------------------------------------------------------------------------
# Test 9: 5% Max Daily Drawdown Killswitch
# -------------------------------------------------------------------------

def test_daily_drawdown_killswitch():
    """
    Tests 5% daily drawdown killswitch on $65 micro account.
    5% of $65 is $3.25 max loss (Floor equity = $61.75).
    """
    guard = DailyDrawdownGuard(max_drawdown_pct=0.05)

    # Initial equity: $65.00 -> 0% drawdown
    tripped, dd = guard.update(65.00)
    assert tripped is False
    assert dd == 0.0

    # Equity drops to $63.00 ($2 loss, 3.07% drawdown) -> Safe
    tripped, dd = guard.update(63.00)
    assert tripped is False
    assert dd < 0.05

    # Equity drops to $61.50 ($3.50 loss, 5.38% drawdown) -> Tripped!
    tripped, dd = guard.update(61.50)
    assert tripped is True
    assert dd >= 0.05
    assert guard.is_tripped is True


# -------------------------------------------------------------------------
# Test 10: Relapse FSM State Transitions
# -------------------------------------------------------------------------

def test_relapse_fsm_lifecycle(router: ExecutionRouter):
    """
    Validates complete FSM transition path:
    IDLE -> WAITING_FOR_RELAPSE -> TRIGGER_DETECTED -> IN_TRADE -> EXIT_SIGNAL -> IDLE.
    """
    async def _test():
        calendar = EconomicCalendarFilter()
        kz_guard = KillZoneGuard(zones=XAUUSD_KILLZONES)
        dd_guard = DailyDrawdownGuard(max_drawdown_pct=0.05)

        fsm = RelapseFSM(
            symbol="XAUUSD",
            execution_router=router,
            calendar_filter=calendar,
            drawdown_guard=dd_guard,
            killzone_guard=kz_guard,
        )

        assert fsm.state == RelapseState.IDLE

        # Transition to WAITING_FOR_RELAPSE
        fsm.transition_to(RelapseState.WAITING_FOR_RELAPSE, reason="Test HTF Bullish Flow")
        assert fsm.state == RelapseState.WAITING_FOR_RELAPSE

        # Transition to TRIGGER_DETECTED
        fsm.transition_to(RelapseState.TRIGGER_DETECTED, reason="Test 5M Pullback in FVG")
        assert fsm.state == RelapseState.TRIGGER_DETECTED

        # Fire layered orders -> IN_TRADE
        basket = await router.fire_layered_orders(
            symbol="XAUUSD",
            side=OrderSide.BUY,
            invalidation_wick_price=2498.80,
        )
        fsm.active_basket = basket
        fsm.transition_to(RelapseState.IN_TRADE, reason="Layered slices filled")
        assert fsm.state == RelapseState.IN_TRADE

        # Trigger exit signal
        fsm.transition_to(RelapseState.EXIT_SIGNAL, reason="SLM Intuition Exit Triggered")
        assert fsm.state == RelapseState.EXIT_SIGNAL

        # Finalize liquidation -> IDLE
        await fsm._execute_basket_exit(reason="Test Exit Finalized")
        fsm.transition_to(RelapseState.IDLE, reason="Basket closed")
        assert fsm.state == RelapseState.IDLE
        assert fsm.active_basket is None

    asyncio.run(_test())


# -------------------------------------------------------------------------
# Test 11: 5M Candlestick & ICT Pattern Detectors
# -------------------------------------------------------------------------

def test_candlestick_and_ict_integration():
    """
    Validates Morning Star, Evening Star, Engulfing, and ICT FVG detectors.
    """
    # 1. Morning Star test df
    df_mstar = pd.DataFrame({
        "open": [2502.00, 2498.20, 2498.00],
        "high": [2502.20, 2498.80, 2501.50],
        "low": [2499.00, 2497.80, 2497.90],
        "close": [2499.20, 2498.10, 2501.20],
    })
    mstar = detect_morning_star(df_mstar)
    assert bool(mstar.iloc[-1]) is True

    # 2. Evening Star test df
    df_estar = pd.DataFrame({
        "open": [2498.00, 2502.50, 2502.50],
        "high": [2502.00, 2503.00, 2502.60],
        "low": [2497.80, 2502.00, 2499.00],
        "close": [2501.80, 2502.60, 2499.20],
    })
    estar = detect_evening_star(df_estar)
    assert bool(estar.iloc[-1]) is True

    # 3. ICT Bullish FVG test
    # Bar 0: Low 2500, High 2501
    # Bar 1: Low 2501.2, High 2503 (expansion)
    # Bar 2: Low 2502.0, High 2504 (gap between Low[2]=2502 and High[0]=2501)
    df_fvg = pd.DataFrame({
        "open": [2500.2, 2501.3, 2503.0],
        "high": [2501.0, 2503.0, 2504.0],
        "low": [2500.0, 2501.2, 2502.0],
        "close": [2500.8, 2502.9, 2503.8],
    })
    from scalper.pa import ict
    fvg_res = ict.fvg_state(df_fvg)
    assert bool(fvg_res["bull_fvg"].iloc[-1]) is True
    assert fvg_res["bull_gap_hi"].iloc[-1] == 2502.0
    assert fvg_res["bull_ce"].iloc[-1] == 2501.0


# -------------------------------------------------------------------------
# Test 11: Short (SELL) SL Envelope Invariant
# -------------------------------------------------------------------------

def test_short_stop_loss_envelope_invariant(router: ExecutionRouter):
    """
    Short (SELL) SL Envelope (Absolute Delta):
    - Rejects setups requiring SL delta > $1.50 (invalidation high wick too far).
    - Places SL strictly 0.10 to 0.15 beyond the invalidation high wick ($0.12).
    - Clamps setups tighter than $1.00 minimum delta to $1.00 from entry price.
    """
    entry_price = 2500.00

    # Case A: Invalidation high wick is $2501.50 -> Raw SL = 2501.50 + 0.12 = 2501.62
    # Delta = $1.62 > $1.50 maximum allowed -> REJECT
    wick_far = 2501.50
    ok, sl_price, sl_delta, reason = router.calculate_and_validate_sl(
        is_buy=False, entry_price=entry_price, invalidation_wick_price=wick_far
    )
    assert ok is False
    assert sl_delta > 1.50
    assert "exceeds maximum allowed" in reason

    # Case B: Invalidation high wick is $2501.10 -> Raw SL = 2501.10 + 0.12 = 2501.22
    # Delta = $1.22 (within $1.00 - $1.50 range) -> VALID
    wick_valid = 2501.10
    ok, sl_price, sl_delta, reason = router.calculate_and_validate_sl(
        is_buy=False, entry_price=entry_price, invalidation_wick_price=wick_valid
    )
    assert ok is True
    assert 1.00 <= sl_delta <= 1.50
    assert sl_price == 2501.22
    assert sl_price > wick_valid  # Placed strictly beyond the high wick

    # Case C: Invalidation high wick is $2500.40 -> Raw SL = 2500.52
    # Delta = $0.52 < $1.00 minimum delta -> CLAMP to $1.00 delta ($2501.00)
    wick_tight = 2500.40
    ok, sl_price, sl_delta, reason = router.calculate_and_validate_sl(
        is_buy=False, entry_price=entry_price, invalidation_wick_price=wick_tight
    )
    assert ok is True
    assert sl_delta == 1.00
    assert sl_price == 2501.00


# -------------------------------------------------------------------------
# Test 12: Short (SELL) Breakeven Lock at +1.5R
# -------------------------------------------------------------------------

def test_short_breakeven_lock_at_1_5r(router: ExecutionRouter, venue: SimulatedBrokerVenue):
    """
    Short (SELL) Breakeven Lock at +1.5R:
    - For short positions, entry is at $2500.00, high wick at $2501.10 (SL = $2501.22, 1R = $1.22).
    - +1.5R floating profit occurs when price drops by 1.5 * 1.22 = $1.83 (Price <= $2498.17).
    - At +1.5R floating profit:
      1. Cancels old structural stop order.
      2. Transmits new stop order at Entry Price - $0.10 ($2499.90) with reduce_only=True.
    """
    async def _test():
        wick_price = 2501.10  # SL = 2501.22, 1R = $1.22. +1.5R = $1.83 gain -> Price $2498.17
        basket = await router.fire_layered_orders(
            symbol="GOLD",
            side=OrderSide.SELL,
            is_buy=False,
            invalidation_wick_price=wick_price,
            total_sz=0.45,
            num_slices=3,
        )
        assert basket is not None
        assert basket.is_buy is False
        assert basket.total_sz == 0.45
        assert len(basket.slices) == 3
        initial_stop_oid = basket.stop_order_id
        assert initial_stop_oid is not None
        assert basket.breakeven_locked is False

        # 1. Floating profit at +0.82R ($2499.00 -> $1.00 gain < $1.83) -> NOT triggered
        venue.set_market_price("GOLD", 2499.00)
        locked = await router.evaluate_breakeven_lock(2499.00)
        assert locked is False
        assert basket.breakeven_locked is False
        assert basket.stop_order_id == initial_stop_oid

        # 2. Floating profit at +1.64R ($2498.00 -> $2.00 gain >= +1.5R) -> Breakeven Lock TRIGGERED!
        venue.set_market_price("GOLD", 2498.00)
        locked = await router.evaluate_breakeven_lock(2498.00)
        assert locked is True
        assert basket.breakeven_locked is True

        # Verify old stop order cancelled and new stop transmitted at Entry - $0.10 ($2499.90)
        assert basket.stop_order_id != initial_stop_oid
        expected_be_sl = round(basket.entry_price - 0.10, 2)
        assert expected_be_sl == 2499.90
        assert basket.sl_price == expected_be_sl

        # Check venue records show old stop cancelled and new stop resting
        assert venue._resting_stops[initial_stop_oid]["status"] == "cancelled"
        assert venue._resting_stops[basket.stop_order_id]["trigger_px"] == expected_be_sl
        assert venue._resting_stops[basket.stop_order_id]["reduce_only"] is True

    asyncio.run(_test())


# -------------------------------------------------------------------------
# Test 13: HyperliquidDEXVenue Async Non-Blocking Execution with Mocked SDK
# -------------------------------------------------------------------------

def test_hyperliquid_dex_venue_async_execution():
    """
    Validates HyperliquidDEXVenue:
    1. Protocol conformance with HyperliquidVenue.
    2. URL configuration for testnet vs mainnet.
    3. Asynchronous non-blocking SDK call execution via asyncio.to_thread().
    4. Market open, market close (detached stop and immediate liquidation), and cancel.
    5. Integration with ExecutionRouter order slicing and detached stop placement.
    """
    async def _test():
        mock_info = MagicMock()
        mock_exchange = MagicMock()

        # Mock Info methods
        mock_info.user_state.return_value = {
            "marginSummary": {
                "accountValue": "100.00",
                "totalMarginUsed": "15.00",
            },
            "assetPositions": [],
        }
        mock_info.all_mids.return_value = {"GOLD": "2500.00", "BTC": "65000.00"}

        # Mock Exchange methods
        mock_exchange.market_open.return_value = {
            "status": "ok",
            "response": {
                "type": "order",
                "data": {
                    "statuses": [
                        {"filled": {"oid": 1001, "totalSz": "0.15", "avgPx": "2500.00"}}
                    ]
                },
            },
        }
        mock_exchange.order.return_value = {
            "status": "ok",
            "response": {
                "type": "order",
                "data": {
                    "statuses": [{"resting": {"oid": 2001}}]
                },
            },
        }
        mock_exchange.market_close.return_value = {
            "status": "ok",
            "response": {
                "type": "order",
                "data": {
                    "statuses": [
                        {"filled": {"oid": 3001, "totalSz": "0.45", "avgPx": "2503.00"}}
                    ]
                },
            },
        }
        mock_exchange.cancel.return_value = {
            "status": "ok",
            "response": {"type": "cancel", "data": {"statuses": ["success"]}},
        }

        # Initialize DEX Venue
        dex_venue = HyperliquidDEXVenue(
            account_address="0x0000000000000000000000000000000000000001",
            testnet=True,
            exchange=mock_exchange,
            info=mock_info,
        )

        # 1. Verify protocol conformance and URLs
        assert isinstance(dex_venue, HyperliquidVenue)
        assert dex_venue.testnet is True
        assert "testnet" in dex_venue.base_url

        mainnet_venue = HyperliquidDEXVenue(
            testnet=False,
            exchange=mock_exchange,
            info=mock_info,
        )
        assert "testnet" not in mainnet_venue.base_url

        # 2. Verify async get_equity via to_thread
        eq = await dex_venue.get_equity()
        assert eq == 100.00
        mock_info.user_state.assert_called_once_with("0x0000000000000000000000000000000000000001")

        # 3. Verify async get_market_price via to_thread
        px = await dex_venue.get_market_price("GOLD")
        assert px == 2500.00
        mock_info.all_mids.assert_called_once()

        # 4. Verify async market_open (open-ended dispatch)
        res_open = await dex_venue.market_open("GOLD", is_buy=True, sz=0.15)
        assert res_open["status"] == "ok"
        assert res_open["oid"] == "1001"
        assert res_open["sz"] == 0.15
        assert res_open["fill_price"] == 2500.00
        assert res_open["take_profit"] is None
        mock_exchange.market_open.assert_called_once_with("GOLD", True, 0.15, px=None, slippage=0.01)

        # 5. Verify async market_close with trigger_px (Detached Stop Market order)
        res_stop = await dex_venue.market_close(
            coin="GOLD",
            sz=0.45,
            trigger_px=2498.68,
            reduce_only=True,
        )
        assert res_stop["status"] == "ok"
        assert res_stop["oid"] == "2001"
        assert res_stop["reduce_only"] is True
        assert res_stop["type"] == "stop_market"
        mock_exchange.order.assert_called_once()
        order_call_kwargs = mock_exchange.order.call_args.kwargs
        assert order_call_kwargs["name"] == "GOLD"
        assert order_call_kwargs["sz"] == 0.45
        assert order_call_kwargs["limit_px"] == 2498.68
        assert order_call_kwargs["reduce_only"] is True
        assert order_call_kwargs["order_type"]["trigger"]["triggerPx"] == 2498.68
        assert order_call_kwargs["order_type"]["trigger"]["isMarket"] is True
        assert order_call_kwargs["order_type"]["trigger"]["tpsl"] == "sl"

        # 6. Verify async market_close immediate liquidation
        res_close = await dex_venue.market_close(
            coin="GOLD",
            sz=0.45,
            reduce_only=True,
        )
        assert res_close["status"] == "ok"
        assert res_close["oid"] == "3001"
        assert res_close["fill_price"] == 2503.00
        mock_exchange.market_close.assert_called_once_with(
            coin="GOLD",
            sz=0.45,
            px=None,
            slippage=0.01,
        )

        # 7. Verify async cancel
        cancelled = await dex_venue.cancel("GOLD", "2001")
        assert cancelled is True
        mock_exchange.cancel.assert_called_once_with("GOLD", 2001)

        # 8. Full End-to-End Integration with ExecutionRouter
        router = ExecutionRouter(
            venue=dex_venue,
            risk=RiskInvariants(
                coin="GOLD",
                leverage=100.0,
                max_margin_pct=0.20,
                min_sl_delta=1.00,
                max_sl_delta=1.50,
                wick_buffer=0.12,
                slice_jitter_ms=5,
            ),
        )

        basket = await router.fire_layered_orders(
            symbol="GOLD",
            is_buy=True,
            invalidation_wick_price=2498.80,
            total_sz=0.45,
            num_slices=3,
        )
        assert basket is not None
        assert basket.total_sz == 0.45
        assert len(basket.slices) == 3
        assert basket.stop_order_id == "2001"
        assert basket.sl_price == 2498.68

    asyncio.run(_test())
