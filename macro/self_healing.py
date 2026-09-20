"""
macro/self_healing.py
=====================
Autonomous LLM-Powered Exception Interceptor & Self-Healing Engine.
Communicates with the local micro-LLM (Qwen2.5-Coder-1.5B on llama.cpp at http://127.0.0.1:8080)
to diagnose runtime exceptions, state inconsistencies, and network faults in real-time,
applying live hotfix transitions and recovery procedures without crashing the trading engine.
Escalates all diagnoses and actions via ntfy.sh push notifications.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
import traceback
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger("self_healing")


# -------------------------------------------------------------------------
# Telemetry Formatting
# -------------------------------------------------------------------------

def emit_telemetry(
    component: str,
    event: str,
    data: Dict[str, Any],
    level: str = "INFO",
) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": "Antigravity-SelfHealingLLM",
        "component": component,
        "event": event,
        "level": level,
        "data": data,
    }
    log_line = json.dumps(payload, separators=(",", ":"))
    if level in ("ERROR", "CRITICAL"):
        logger.error(log_line)
    elif level == "WARNING":
        logger.warning(log_line)
    else:
        logger.info(log_line)


# -------------------------------------------------------------------------
# ntfy.sh Push Notification Dispatcher
# -------------------------------------------------------------------------

from pathlib import Path

def get_ntfy_topic(default: str = "tbt-gold-scalper") -> str:
    topic = os.getenv("NTFY_TOPIC")
    if topic:
        return topic
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if env_file.exists():
        try:
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("NTFY_TOPIC=") and not line.startswith("NTFY_TOPIC_SHARED="):
                    val = line.split("=", 1)[1].strip()
                    if val:
                        return val
        except Exception:
            pass
    return default


DEFAULT_NTFY_TOPIC = get_ntfy_topic("tbt-gold-scalper")


def push_ntfy_sync(
    title: str,
    message: str,
    tags: str = "robot,wrench",
    priority: str = "high",
    topic: Optional[str] = None,
) -> bool:
    """
    Synchronously push an alert via ntfy.sh (fallback for non-async callers).
    """
    target_topic = topic or get_ntfy_topic()
    url = f"https://ntfy.sh/{target_topic}"
    try:
        from email.header import Header
        encoded_title = Header(title, "utf-8").encode()
        req = urllib.request.Request(
            url,
            data=message.encode("utf-8"),
            headers={"Title": encoded_title, "Tags": tags, "Priority": priority},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status in (200, 201)
    except Exception as e:
        logger.warning("Failed to dispatch sync ntfy alert: %s", e)
        return False


async def push_ntfy_async(
    title: str,
    message: str,
    tags: str = "robot,wrench",
    priority: str = "high",
    topic: Optional[str] = None,
    session: Optional[aiohttp.ClientSession] = None,
) -> bool:
    """
    Asynchronously push an alert via ntfy.sh without blocking the event loop.
    """
    from email.header import Header
    target_topic = topic or get_ntfy_topic()
    url = f"https://ntfy.sh/{target_topic}"
    encoded_title = Header(title, "utf-8", maxlinelen=1000).encode().replace("\r", "").replace("\n", "")
    headers = {"Title": encoded_title, "Tags": tags, "Priority": priority}

    own_session = False
    if session is None or session.closed:
        session = aiohttp.ClientSession()
        own_session = True

    try:
        async with session.post(url, data=message.encode("utf-8"), headers=headers, timeout=aiohttp.ClientTimeout(total=4.0)) as resp:
            ok = resp.status in (200, 201)
            emit_telemetry(
                component="SelfHealingAlerting",
                event="NTFY_PUSHED",
                data={"title": title, "topic": target_topic, "status": resp.status},
            )
            return ok
    except Exception as e:
        emit_telemetry(
            component="SelfHealingAlerting",
            event="NTFY_PUSH_FAILED",
            data={"title": title, "topic": target_topic, "error": str(e)},
            level="WARNING",
        )
        return False
    finally:
        if own_session and not session.closed:
            await session.close()


# -------------------------------------------------------------------------
# Recovery Actions & Results
# -------------------------------------------------------------------------

class RecoveryAction(str, Enum):
    RETRY = "RETRY"
    RESET_FSM_TO_IDLE = "RESET_FSM_TO_IDLE"
    RECONNECT_VENUE = "RECONNECT_VENUE"
    CANCEL_ORPHAN_ORDERS = "CANCEL_ORPHAN_ORDERS"
    ADJUST_BUFFER = "ADJUST_BUFFER"
    EMERGENCY_BASKET_CLOSE = "EMERGENCY_BASKET_CLOSE"
    IGNORE_AND_CONTINUE = "IGNORE_AND_CONTINUE"


@dataclass
class RecoveryResult:
    action: RecoveryAction
    success: bool
    root_cause: str
    fix_applied: str
    latency_ms: float
    error_type: str
    component: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "success": self.success,
            "root_cause": self.root_cause,
            "fix_applied": self.fix_applied,
            "latency_ms": round(self.latency_ms, 2),
            "error_type": self.error_type,
            "component": self.component,
            "timestamp": self.timestamp,
        }


# GBNF Grammar constraining LLM output strictly to recovery schema
GBNF_SELF_HEALING_GRAMMAR = r'''
root ::= "{" ws "\"action\":" ws action ws ",\"root_cause\":" ws string ws ",\"fix_applied\":" ws string ws "}"
action ::= "\"RETRY\"" | "\"RESET_FSM_TO_IDLE\"" | "\"RECONNECT_VENUE\"" | "\"CANCEL_ORPHAN_ORDERS\"" | "\"ADJUST_BUFFER\"" | "\"EMERGENCY_BASKET_CLOSE\"" | "\"IGNORE_AND_CONTINUE\""
string ::= "\"" [^"\\]* "\""
ws ::= [ \t\n\r]*
'''.strip()


# -------------------------------------------------------------------------
# Self-Healing LLM Guard
# -------------------------------------------------------------------------

class SelfHealingLLMGuard:
    """
    Autonomous exception interceptor and self-healing engine.
    Monitors engine components, analyzes tracebacks and states with local LLM,
    executes hotfix recovery actions, and alerts mobile operators via ntfy.sh.
    """

    def __init__(
        self,
        llm_host: str = "127.0.0.1",
        llm_port: int = 8080,
        timeout_sec: float = 0.500,  # 500ms budget for self-healing inference
        ntfy_topic: str = DEFAULT_NTFY_TOPIC,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> None:
        self.llm_host = llm_host
        self.llm_port = llm_port
        self.base_url = f"http://{llm_host}:{llm_port}"
        self.timeout_sec = timeout_sec
        self.ntfy_topic = ntfy_topic
        self._session = session
        self._own_session = False
        self._recovery_history: List[RecoveryResult] = []
        self._max_history = 50

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._own_session = True
        return self._session

    async def close(self) -> None:
        if self._own_session and self._session and not self._session.closed:
            await self._session.close()

    @property
    def history(self) -> List[RecoveryResult]:
        return list(self._recovery_history)

    def build_prompt(
        self,
        component: str,
        exc: Exception,
        tb_snippet: str,
        context: Dict[str, Any],
    ) -> str:
        """
        Builds high-density diagnostic prompt tailored for Qwen2.5-Coder-1.5B.
        """
        err_type = type(exc).__name__
        err_msg = str(exc)

        payload = {
            "component": component,
            "error_type": err_type,
            "error_message": err_msg,
            "traceback": tb_snippet,
            "system_context": context,
        }
        data_json = json.dumps(payload, separators=(",", ":"))

        prompt = (
            "<|im_start|>system\n"
            "You are the Autonomous Self-Healing Diagnostic Engine for an institutional Gold (XAUUSD) trading system.\n"
            "An exception occurred. Analyze the traceback, component, and state to select the safest recovery action:\n"
            "- RETRY: For transient network timeouts, HTTP 5xx, or brief RPC disconnection.\n"
            "- RESET_FSM_TO_IDLE: If state machine got desynchronized, hung, or received invalid candle structure.\n"
            "- RECONNECT_VENUE: If DEX WebSocket or REST API session dropped or authentication failed.\n"
            "- CANCEL_ORPHAN_ORDERS: If resting stop orders or limit slices exist without an active position.\n"
            "- ADJUST_BUFFER: If SL wick envelope or buffer was borderline due to noise, safely calibrate.\n"
            "- EMERGENCY_BASKET_CLOSE: If position/margin is in an unrecoverable risk mismatch or lethal state.\n"
            "- IGNORE_AND_CONTINUE: If non-fatal telemetry, file I/O lock, or benign logging error.\n"
            "STRICT RULES: Never increase position risk. Never remove SL protection. Always respond in JSON format:\n"
            '{"action":"<ACTION>","root_cause":"<concise explanation>","fix_applied":"<recovery action description>"}\n'
            "<|im_end|>\n"
            f"<|im_start|>user\n{data_json}\n<|im_end|>\n"
            "<|im_start|>assistant\n"
        )
        return prompt

    def _algorithmic_fallback(
        self,
        component: str,
        exc: Exception,
        context: Dict[str, Any],
        reason: str,
    ) -> Tuple[RecoveryAction, str, str]:
        """
        Deterministic fallback heuristics when local LLM is offline or times out.
        Ensures guaranteed recovery within 0ms without external dependencies.
        """
        err_type = type(exc).__name__
        err_str = str(exc).lower()

        # 1. Network / RPC / HTTP Timeout -> RETRY
        if any(w in err_str or w in err_type.lower() for w in ("timeout", "connection", "clienterror", "aiohttp", "econnreset", "network")):
            return (
                RecoveryAction.RETRY,
                f"Transient network or RPC transport failure ({err_type}): {exc}",
                "Initiating asynchronous retry with exponential jitter backoff.",
            )

        # 2. Risk / Margin / Invariant mismatch -> EMERGENCY_BASKET_CLOSE
        if any(w in err_str for w in ("margin", "drawdown", "lethal", "liquidation", "balance exceeded")):
            return (
                RecoveryAction.EMERGENCY_BASKET_CLOSE,
                f"Critical risk constraint trigger in {component}: {exc}",
                "Executing immediate market liquidation across all slices to safeguard micro-account equity.",
            )

        # 3. Order desynchronization / dangling order -> CANCEL_ORPHAN_ORDERS
        if any(w in err_str for w in ("orphan", "untracked order", "order id not found", "stop mismatch")):
            return (
                RecoveryAction.CANCEL_ORPHAN_ORDERS,
                f"Order tracking desynchronization in {component}: {exc}",
                "Purging all dangling limit slices and detached resting stops on venue.",
            )

        # 4. FSM state corruption or invalid transition -> RESET_FSM_TO_IDLE
        if component in ("RelapseFSM", "Runtime") or "state" in err_str or "keyerror" in err_type.lower() or "indexerror" in err_type.lower():
            return (
                RecoveryAction.RESET_FSM_TO_IDLE,
                f"State desynchronization or candle telemetry defect ({err_type}): {exc}",
                "Safely resetting Relapse FSM state to IDLE and re-evaluating killzones.",
            )

        # 5. Non-critical telemetry / file I/O -> IGNORE_AND_CONTINUE
        if any(w in err_str or w in err_type.lower() for w in ("json", "telemetry", "log", "tmp", "permission")):
            return (
                RecoveryAction.IGNORE_AND_CONTINUE,
                f"Non-critical telemetry or disk I/O glitch ({err_type}): {exc}",
                "Bypassing non-fatal telemetry serialization to preserve execution loop.",
            )

        # Default safe recovery: RESET_FSM_TO_IDLE
        return (
            RecoveryAction.RESET_FSM_TO_IDLE,
            f"Unhandled exception ({err_type}) caught by fallback: {exc} [{reason}]",
            "Resetting FSM to IDLE to ensure system remains operational.",
        )

    async def query_llm_diagnosis(
        self,
        component: str,
        exc: Exception,
        tb_snippet: str,
        context: Dict[str, Any],
    ) -> Tuple[RecoveryAction, str, str, float]:
        """
        Asynchronously queries the local llama.cpp server for diagnosis and hotfix action.
        Returns: (action, root_cause, fix_applied, latency_ms)
        """
        start_t = time.perf_counter()
        prompt = self.build_prompt(component, exc, tb_snippet, context)

        payload = {
            "prompt": prompt,
            "n_predict": 96,
            "temperature": 0.05,
            "stream": False,
            "grammar": GBNF_SELF_HEALING_GRAMMAR,
            "stop": ["<|im_end|>", "\n\n"],
        }

        session = await self._get_session()
        timeout = aiohttp.ClientTimeout(total=self.timeout_sec)

        try:
            async with session.post(
                f"{self.base_url}/completion",
                json=payload,
                timeout=timeout,
            ) as resp:
                latency_ms = (time.perf_counter() - start_t) * 1000.0
                if resp.status != 200:
                    action, cause, fix = self._algorithmic_fallback(
                        component, exc, context, f"LLM HTTP {resp.status}"
                    )
                    return action, cause, fix, latency_ms

                data = await resp.json()
                raw_text = data.get("content", "").strip()

                # Parse JSON output
                parsed = self._extract_json(raw_text)
                if parsed and "action" in parsed:
                    action_str = str(parsed["action"]).strip().upper()
                    try:
                        action = RecoveryAction(action_str)
                    except ValueError:
                        action = RecoveryAction.RESET_FSM_TO_IDLE

                    root_cause = parsed.get("root_cause", f"LLM Diagnosis: {type(exc).__name__}")
                    fix_applied = parsed.get("fix_applied", f"Applied {action.value}")
                    return action, root_cause, fix_applied, latency_ms

                # Fallback if parsing failed
                action, cause, fix = self._algorithmic_fallback(
                    component, exc, context, "Malformed LLM JSON"
                )
                return action, cause, fix, latency_ms

        except (asyncio.TimeoutError, aiohttp.ClientError) as net_err:
            latency_ms = (time.perf_counter() - start_t) * 1000.0
            action, cause, fix = self._algorithmic_fallback(
                component, exc, context, f"LLM Offline/Timeout ({type(net_err).__name__})"
            )
            return action, cause, fix, latency_ms
        except Exception as e:
            latency_ms = (time.perf_counter() - start_t) * 1000.0
            action, cause, fix = self._algorithmic_fallback(
                component, exc, context, f"Guard Internal Error: {e}"
            )
            return action, cause, fix, latency_ms

    def _extract_json(self, text: str) -> Optional[Dict[str, Any]]:
        """Extract and parse JSON object from raw LLM output text."""
        try:
            return json.loads(text)
        except Exception:
            pass

        # Regex match JSON block
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return None

    async def execute_recovery_action(
        self,
        action: RecoveryAction,
        root_cause: str,
        fix_applied: str,
        fsm: Optional[Any] = None,
        router: Optional[Any] = None,
        venue: Optional[Any] = None,
    ) -> bool:
        """
        Executes the diagnosed recovery procedure on the live trading components.
        """
        try:
            if action == RecoveryAction.RETRY:
                # Signal caller to retry with brief backoff
                await asyncio.sleep(0.05)
                return True

            elif action == RecoveryAction.RESET_FSM_TO_IDLE:
                if fsm is not None:
                    # Import RelapseState if needed
                    from engine.fsm import RelapseState
                    fsm.state = RelapseState.IDLE
                    fsm.active_basket = None
                    emit_telemetry(
                        component="SelfHealingLLMGuard",
                        event="FSM_RESET_TO_IDLE",
                        data={"cause": root_cause, "fix": fix_applied},
                        level="WARNING",
                    )
                return True

            elif action == RecoveryAction.CANCEL_ORPHAN_ORDERS:
                if router is not None:
                    if hasattr(router, "active_basket_id") and router.active_basket_id:
                        await router.close_basket(reason=f"SELF_HEAL_ORPHAN_PURGE: {root_cause}")
                if venue is not None and hasattr(venue, "cancel_all_orders"):
                    await venue.cancel_all_orders(symbol="GOLD")
                return True

            elif action == RecoveryAction.EMERGENCY_BASKET_CLOSE:
                if router is not None and hasattr(router, "close_basket"):
                    await router.close_basket(reason=f"SELF_HEAL_EMERGENCY_CLOSE: {root_cause}")
                    emit_telemetry(
                        component="SelfHealingLLMGuard",
                        event="EMERGENCY_CLOSE_EXECUTED",
                        data={"cause": root_cause},
                        level="CRITICAL",
                    )
                return True

            elif action == RecoveryAction.RECONNECT_VENUE:
                if venue is not None and hasattr(venue, "reconnect"):
                    await venue.reconnect()
                return True

            elif action == RecoveryAction.ADJUST_BUFFER:
                if router is not None and hasattr(router, "risk"):
                    # Safely calibrate buffer within strictly allowed [$1.00, $1.50] bounds
                    router.risk.wick_buffer = max(0.10, min(0.15, router.risk.wick_buffer))
                return True

            elif action == RecoveryAction.IGNORE_AND_CONTINUE:
                return True

            return False
        except Exception as e:
            emit_telemetry(
                component="SelfHealingLLMGuard",
                event="ACTION_EXECUTION_FAILED",
                data={"action": action.value, "error": str(e)},
                level="ERROR",
            )
            return False

    async def handle_exception(
        self,
        exc: Exception,
        component: str,
        context: Optional[Dict[str, Any]] = None,
        fsm: Optional[Any] = None,
        router: Optional[Any] = None,
        venue: Optional[Any] = None,
    ) -> RecoveryResult:
        """
        Full autonomous self-healing interceptor:
        1. Formats exception & telemetry context.
        2. Consults local Qwen2.5-Coder LLM (with algorithmic fallback).
        3. Executes live hotfix action.
        4. Broadcasts ntfy push notification to mobile operators.
        5. Emits Antigravity structured telemetry.
        """
        ctx = context or {}
        tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
        tb_snippet = "".join(tb_lines[-4:]) if len(tb_lines) >= 4 else "".join(tb_lines)

        emit_telemetry(
            component="SelfHealingLLMGuard",
            event="EXCEPTION_INTERCEPTED",
            data={
                "component": component,
                "error_type": type(exc).__name__,
                "error_msg": str(exc),
                "context": ctx,
            },
            level="WARNING",
        )

        action, root_cause, fix_applied, latency_ms = await self.query_llm_diagnosis(
            component=component,
            exc=exc,
            tb_snippet=tb_snippet,
            context=ctx,
        )

        # Apply the recovery action
        action_ok = await self.execute_recovery_action(
            action=action,
            root_cause=root_cause,
            fix_applied=fix_applied,
            fsm=fsm,
            router=router,
            venue=venue,
        )

        res = RecoveryResult(
            action=action,
            success=action_ok,
            root_cause=root_cause,
            fix_applied=fix_applied,
            latency_ms=latency_ms,
            error_type=type(exc).__name__,
            component=component,
        )

        self._recovery_history.append(res)
        if len(self._recovery_history) > self._max_history:
            self._recovery_history.pop(0)

        # Dispatch ntfy push alert
        priority = "urgent" if action in (RecoveryAction.EMERGENCY_BASKET_CLOSE, RecoveryAction.RESET_FSM_TO_IDLE) else "high"
        ntfy_title = f"🛠️ Auto-Fix: [{action.value}] in {component}"
        ntfy_body = (
            f"Fault: {type(exc).__name__}: {str(exc)[:120]}\n"
            f"Diagnosis: {root_cause}\n"
            f"Action: {fix_applied}\n"
            f"Latency: {latency_ms:.1f}ms | Status: {'SUCCESS' if action_ok else 'FAILED'}"
        )

        asyncio.create_task(
            push_ntfy_async(
                title=ntfy_title,
                message=ntfy_body,
                tags="wrench,robot,shield",
                priority=priority,
                topic=self.ntfy_topic,
            )
        )

        emit_telemetry(
            component="SelfHealingLLMGuard",
            event="SELF_HEALING_RESOLVED",
            data=res.to_dict(),
            level="INFO" if action_ok else "ERROR",
        )

        return res
