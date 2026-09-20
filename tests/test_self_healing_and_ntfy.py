"""
tests/test_self_healing_and_ntfy.py
===================================
Comprehensive Unit & Integration Test Suite for:
1. Autonomous Local LLM Self-Healing Bug-Fixing Engine (macro/self_healing.py)
2. Real-Time ntfy.sh Mobile Alerting System (engine/guard.py & macro/self_healing.py)
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.execution_router import ExecutionRouter, OrderSide, RiskInvariants, SimulatedBrokerVenue
from engine.fsm import RelapseFSM, RelapseState
from macro.self_healing import (
    DEFAULT_NTFY_TOPIC,
    RecoveryAction,
    RecoveryResult,
    SelfHealingLLMGuard,
    get_ntfy_topic,
    push_ntfy_async,
    push_ntfy_sync,
)
from engine.guard import push_ntfy as guard_push_ntfy


# -------------------------------------------------------------------------
# Test 1: Diagnostic Prompt Formation
# -------------------------------------------------------------------------

def test_self_healing_prompt_formation():
    guard = SelfHealingLLMGuard()
    try:
        raise KeyError("tick_volume_missing")
    except KeyError as exc:
        tb_snippet = "line 42 in on_candle\nKeyError: tick_volume_missing"
        context = {"fsm_state": "WAITING_FOR_RELAPSE", "equity": 65.0, "symbol": "GOLD"}
        prompt = guard.build_prompt("RelapseFSM", exc, tb_snippet, context)

        assert "<|im_start|>system" in prompt
        assert "Autonomous Self-Healing Diagnostic Engine" in prompt
        assert "RETRY" in prompt
        assert "RESET_FSM_TO_IDLE" in prompt
        assert "EMERGENCY_BASKET_CLOSE" in prompt
        assert "<|im_start|>user" in prompt
        assert "tick_volume_missing" in prompt
        assert "RelapseFSM" in prompt
        assert "<|im_start|>assistant" in prompt


# -------------------------------------------------------------------------
# Test 2: Local LLM Mocked Diagnosis & JSON Extraction
# -------------------------------------------------------------------------

def test_self_healing_llm_mock_diagnosis():
    async def _test():
        guard = SelfHealingLLMGuard(timeout_sec=0.5)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={
                "content": json.dumps({
                    "action": "RESET_FSM_TO_IDLE",
                    "root_cause": "Corrupted 5M candle stream missing close price",
                    "fix_applied": "Reset FSM state to IDLE and cleared active setup context",
                })
            }
        )

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.post.return_value.__aexit__ = AsyncMock(return_value=None)
        guard._session = mock_session

        try:
            raise ValueError("Invalid candle schema")
        except ValueError as exc:
            action, root_cause, fix_applied, latency_ms = await guard.query_llm_diagnosis(
                component="RelapseFSM",
                exc=exc,
                tb_snippet="ValueError: Invalid candle schema",
                context={"fsm_state": "TRIGGER_DETECTED"},
            )

            assert action == RecoveryAction.RESET_FSM_TO_IDLE
            assert "Corrupted 5M candle stream" in root_cause
            assert "Reset FSM state" in fix_applied
            assert latency_ms >= 0

    asyncio.run(_test())


# -------------------------------------------------------------------------
# Test 3: Deterministic Fallback Heuristics When LLM Offline / Timed Out
# -------------------------------------------------------------------------

def test_self_healing_algorithmic_fallback_on_timeout():
    async def _test():
        guard = SelfHealingLLMGuard(timeout_sec=0.1)

        # Simulate connection timeout to llama-server
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post.side_effect = asyncio.TimeoutError()
        guard._session = mock_session

        # Case A: Network / Timeout Error -> Fallback to RETRY
        try:
            raise ConnectionResetError("DEX endpoint connection reset by peer")
        except ConnectionResetError as exc:
            action, cause, fix, lat = await guard.query_llm_diagnosis(
                component="ExecutionRouter",
                exc=exc,
                tb_snippet="ConnectionResetError",
                context={},
            )
            assert action == RecoveryAction.RETRY
            assert "Transient network or RPC" in cause

        # Case B: Critical Margin / Liquidation Violation -> Fallback to EMERGENCY_BASKET_CLOSE
        try:
            raise RuntimeError("Margin limit exceeded: lethal risk breach")
        except RuntimeError as exc:
            action, cause, fix, lat = await guard.query_llm_diagnosis(
                component="ExecutionRouter",
                exc=exc,
                tb_snippet="RuntimeError",
                context={},
            )
            assert action == RecoveryAction.EMERGENCY_BASKET_CLOSE
            assert "Critical risk constraint" in cause

        # Case C: FSM State or Key error -> Fallback to RESET_FSM_TO_IDLE
        try:
            raise IndexError("list index out of range in candle window")
        except IndexError as exc:
            action, cause, fix, lat = await guard.query_llm_diagnosis(
                component="RelapseFSM",
                exc=exc,
                tb_snippet="IndexError",
                context={},
            )
            assert action == RecoveryAction.RESET_FSM_TO_IDLE
            assert "State desynchronization" in cause

    asyncio.run(_test())


# -------------------------------------------------------------------------
# Test 4: Live Recovery Action Execution on FSM & Execution Router
# -------------------------------------------------------------------------

def test_recovery_action_execution():
    async def _test():
        guard = SelfHealingLLMGuard()
        venue = SimulatedBrokerVenue(initial_equity=65.0)
        risk = RiskInvariants(coin="GOLD", leverage=100.0, max_margin_pct=0.20)
        router = ExecutionRouter(venue=venue, risk=risk)
        fsm = RelapseFSM(symbol="XAUUSD", execution_router=router)

        # Set FSM to an arbitrary non-idle state
        fsm.state = RelapseState.TRIGGER_DETECTED

        # 1. Test RESET_FSM_TO_IDLE
        ok = await guard.execute_recovery_action(
            action=RecoveryAction.RESET_FSM_TO_IDLE,
            root_cause="Test bug recovery",
            fix_applied="Reset to IDLE",
            fsm=fsm,
            router=router,
            venue=venue,
        )
        assert ok is True
        assert fsm.state == RelapseState.IDLE

        # 2. Test EMERGENCY_BASKET_CLOSE
        # Create active basket
        basket = await router.fire_layered_orders(
            symbol="GOLD",
            side=OrderSide.BUY,
            invalidation_wick_price=2498.80,
            total_lots=0.03,
        )
        assert router.active_basket_id is not None

        ok_close = await guard.execute_recovery_action(
            action=RecoveryAction.EMERGENCY_BASKET_CLOSE,
            root_cause="Critical position mismatch",
            fix_applied="Closed basket",
            fsm=fsm,
            router=router,
            venue=venue,
        )
        assert ok_close is True
        assert router.active_basket_id is None
        assert basket.is_active is False

        # 3. Test ADJUST_BUFFER calibration within [$1.00, $1.50]
        router.risk.wick_buffer = 0.50  # Corrupted buffer
        ok_buf = await guard.execute_recovery_action(
            action=RecoveryAction.ADJUST_BUFFER,
            root_cause="Buffer drift",
            fix_applied="Calibrated buffer",
            fsm=fsm,
            router=router,
            venue=venue,
        )
        assert ok_buf is True
        assert router.risk.wick_buffer <= 0.15

    asyncio.run(_test())


# -------------------------------------------------------------------------
# Test 5: Full Exception Interceptor & ntfy Alerting Lifecycle
# -------------------------------------------------------------------------

def test_full_exception_interceptor_lifecycle():
    async def _test():
        guard = SelfHealingLLMGuard(timeout_sec=0.2)
        fsm = RelapseFSM(symbol="XAUUSD")
        fsm.state = RelapseState.WAITING_FOR_RELAPSE

        # Patch push_ntfy_async so it does not make real network calls during test
        with patch("macro.self_healing.push_ntfy_async", new_callable=AsyncMock) as mock_ntfy:
            mock_ntfy.return_value = True

            # Trigger simulated unhandled exception in RelapseFSM
            try:
                raise KeyError("missing_fvg_anchor_price")
            except Exception as exc:
                res = await guard.handle_exception(
                    exc=exc,
                    component="RelapseFSM",
                    context={"state": fsm.state.value},
                    fsm=fsm,
                )

                # Check that exception was diagnosed and resolved
                assert isinstance(res, RecoveryResult)
                assert res.action == RecoveryAction.RESET_FSM_TO_IDLE
                assert res.success is True
                assert fsm.state == RelapseState.IDLE
                assert len(guard.history) == 1
                assert guard.history[0].action == RecoveryAction.RESET_FSM_TO_IDLE

                # Check that ntfy notification was dispatched
                assert mock_ntfy.called
                call_kwargs = mock_ntfy.call_args.kwargs
                assert "Auto-Fix: [RESET_FSM_TO_IDLE]" in call_kwargs["title"]
                assert "RelapseFSM" in call_kwargs["title"]
                assert "KeyError" in call_kwargs["message"]
                assert call_kwargs["priority"] == "urgent"

    asyncio.run(_test())


# -------------------------------------------------------------------------
# Test 6: ntfy Push Notification Formatting & Topic Resolution
# -------------------------------------------------------------------------

def test_guard_push_ntfy_topic_and_dispatch():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        # Explicit topic test
        ok = guard_push_ntfy(
            title="🔔 Test Alert",
            message="Test push message body",
            tags="bell",
            priority="default",
            topic="tbt-test-topic",
        )
        assert ok is True
        assert mock_urlopen.called
        req = mock_urlopen.call_args[0][0]
        from email.header import decode_header
        title_hdr = req.get_header("Title")
        decoded_title = "".join(
            part.decode(enc or "utf-8") if isinstance(part, bytes) else part
            for part, enc in decode_header(title_hdr)
        )
        assert decoded_title == "🔔 Test Alert"
        assert req.get_header("Tags") == "bell"
        assert req.get_header("Priority") == "default"
        assert "tbt-test-topic" in req.full_url

        # Default topic test (resolves user's private topic or tbt-gold-scalper)
        ok_def = guard_push_ntfy(
            title="🔔 Default Alert",
            message="Body",
            tags="bell",
            priority="high",
        )
        assert ok_def is True
        req_def = mock_urlopen.call_args[0][0]
        assert "https://ntfy.sh/" in req_def.full_url


def test_async_push_ntfy_dispatch():
    async def _test():
        mock_resp = AsyncMock()
        mock_resp.status = 200

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.post.return_value.__aexit__ = AsyncMock(return_value=None)

        ok = await push_ntfy_async(
            title="🚀 Scalper Online",
            message="System nominal",
            tags="rocket",
            priority="high",
            topic="tbt-test-topic",
            session=mock_session,
        )
        assert ok is True
        assert mock_session.post.called
        url_called = mock_session.post.call_args[0][0]
        assert "tbt-test-topic" in url_called

    asyncio.run(_test())
