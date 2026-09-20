"""
macro/slm_intuition.py
======================
Macro Economic Fundamental Calendar Filter & Grammar-Constrained
Sub-Second Local LLM Intuition Exit Engine.

Architecture:
1. Macro Fundamental Filter:
   - Polls economic calendar every 10 minutes.
   - Enforces +/- 15-minute trading blackout around High-Impact US news
     (CPI, NFP, FOMC, PPI, Fed Interest Rate Decisions).
2. Sub-Second Intuition Exit:
   - Evaluates 1-minute candle telemetry: unrealized_r, candle_wick_ratio,
     volume_stall, dxy_divergence.
   - Queries local llama.cpp server (Qwen2.5-Coder-1.5B) under 300ms timeout.
   - Employs GBNF grammar / JSON schema to guarantee output format:
     {"decision": "HOLD"} or {"decision": "EXIT"}.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger("slm_intuition")


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
        "agent": "Antigravity-RelapseScalper",
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
# Macro Fundamental Filter (Economic Calendar)
# -------------------------------------------------------------------------

HIGH_IMPACT_KEYWORDS = (
    "CPI",
    "CONSUMER PRICE INDEX",
    "NFP",
    "NON-FARM",
    "NONFARM",
    "FOMC",
    "FED INTEREST RATE",
    "FEDERAL FUNDS RATE",
    "POWELL",
    "PPI",
    "PRODUCER PRICE INDEX",
    "GDP",
    "UNEMPLOYMENT RATE",
)


@dataclass
class MacroNewsEvent:
    """Scheduled macro news event from economic calendar."""
    title: str
    currency: str
    impact: str                  # "HIGH", "MEDIUM", "LOW"
    timestamp: float             # UTC epoch seconds
    forecast: str = ""
    previous: str = ""


class EconomicCalendarFilter:
    """
    Asynchronous fundamental macro filter.
    Polls calendar every 10 minutes and enforces 15-minute pre/post news blackout.
    """

    def __init__(
        self,
        poll_interval_sec: int = 600,
        blackout_window_sec: int = 900,  # 15 minutes before/after
        calendar_api_url: Optional[str] = None,
    ) -> None:
        self.poll_interval_sec = poll_interval_sec
        self.blackout_window_sec = blackout_window_sec
        self.calendar_api_url = calendar_api_url
        self._events: List[MacroNewsEvent] = []
        self._poll_task: Optional[asyncio.Task] = None
        self._is_running = False

    def add_scheduled_event(self, event: MacroNewsEvent) -> None:
        """Manually register or mock high-impact events for testing."""
        self._events.append(event)
        # Keep events sorted by timestamp
        self._events.sort(key=lambda e: e.timestamp)

    def is_macro_blackout(self, current_ts: Optional[float] = None) -> Tuple[bool, str]:
        """
        Check if current timestamp falls within the 15-minute pre/post blackout
        window of any High-Impact US economic release.

        Returns: (in_blackout: bool, reason: str)
        """
        now = current_ts if current_ts is not None else time.time()
        for ev in self._events:
            if ev.currency.upper() != "USD":
                continue
            if ev.impact.upper() != "HIGH" and not any(k in ev.title.upper() for k in HIGH_IMPACT_KEYWORDS):
                continue

            diff = now - ev.timestamp
            # In blackout if within [-blackout_window_sec, +blackout_window_sec]
            if -self.blackout_window_sec <= diff <= self.blackout_window_sec:
                mins_rel = int(abs(diff) / 60)
                timing = f"{mins_rel}m before" if diff < 0 else f"{mins_rel}m after"
                reason = f"Macro Lock: {ev.title} ({timing} release at {datetime.fromtimestamp(ev.timestamp, tz=timezone.utc).strftime('%H:%M')} UTC)"
                return True, reason

        return False, ""

    async def start(self) -> None:
        """Start background polling loop."""
        if self._is_running:
            return
        self._is_running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        emit_telemetry(
            component="EconomicCalendarFilter",
            event="CALENDAR_MONITOR_STARTED",
            data={"poll_interval_sec": self.poll_interval_sec, "blackout_window_sec": self.blackout_window_sec},
        )

    async def stop(self) -> None:
        """Stop background polling loop."""
        self._is_running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        emit_telemetry(
            component="EconomicCalendarFilter",
            event="CALENDAR_MONITOR_STOPPED",
            data={},
        )

    async def _poll_loop(self) -> None:
        while self._is_running:
            try:
                await self.fetch_latest_events()
            except Exception as e:
                emit_telemetry(
                    component="EconomicCalendarFilter",
                    event="CALENDAR_POLL_ERROR",
                    data={"error": str(e)},
                    level="WARNING",
                )
            await asyncio.sleep(self.poll_interval_sec)

    async def fetch_latest_events(self) -> None:
        """Fetch economic calendar events from remote API if configured."""
        if not self.calendar_api_url:
            return

        async with aiohttp.ClientSession() as session:
            async with session.get(self.calendar_api_url, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    now = time.time()
                    new_events: List[MacroNewsEvent] = []
                    for item in data:
                        title = item.get("title", "")
                        curr = item.get("currency", item.get("country", ""))
                        impact = item.get("impact", "LOW")
                        ts = float(item.get("timestamp", 0))
                        # Only keep events within next 24 hours or past 1 hour
                        if (now - 3600) <= ts <= (now + 86400):
                            new_events.append(MacroNewsEvent(
                                title=title,
                                currency=curr,
                                impact=impact,
                                timestamp=ts,
                                forecast=str(item.get("forecast", "")),
                                previous=str(item.get("previous", "")),
                            ))
                    self._events = sorted(new_events, key=lambda e: e.timestamp)
                    emit_telemetry(
                        component="EconomicCalendarFilter",
                        event="CALENDAR_UPDATED",
                        data={"active_events": len(self._events)},
                    )


# -------------------------------------------------------------------------
# Sub-Second SLM Intuition Exit Engine
# -------------------------------------------------------------------------

class IntuitionDecision(str, Enum):
    HOLD = "HOLD"
    EXIT = "EXIT"


@dataclass
class IntuitionTelemetry:
    """Compressed 1-minute telemetry payload fed into local micro-LLM."""
    unrealized_r: float
    candle_wick_ratio: float      # Upper wick/range for BUY, lower wick/range for SELL
    volume_stall: bool           # True if volume dropped sharply during push
    dxy_divergence: bool         # True if US Dollar index is diverging adversely
    side: str                    # "BUY" or "SELL"
    bars_in_trade: int           # Number of 1-minute bars active

    def to_compact_dict(self) -> Dict[str, Any]:
        return {
            "unrealized_r": round(self.unrealized_r, 2),
            "candle_wick_ratio": round(self.candle_wick_ratio, 2),
            "volume_stall": self.volume_stall,
            "dxy_divergence": self.dxy_divergence,
            "side": self.side,
            "bars_in_trade": self.bars_in_trade,
        }


# GBNF Grammar for llama.cpp enforcing strictly {"decision": "HOLD"} or {"decision": "EXIT"}
GBNF_INTUITION_GRAMMAR = r'''
root ::= "{" ws "\"decision\":" ws ("\"HOLD\"" | "\"EXIT\"") ws "}"
ws ::= [ \t\n\r]*
'''.strip()


class SLMIntuitionEngine:
    """
    Sub-second Local LLM Intuition Exit Engine.
    Communicates asynchronously with llama.cpp running Qwen2.5-Coder-1.5B
    under strict grammar constraints and < 300ms timeout budget.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        timeout_sec: float = 0.300,  # 300ms latency ceiling
        session: Optional[aiohttp.ClientSession] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.timeout_sec = timeout_sec
        self._session = session
        self._own_session = False

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._own_session = True
        return self._session

    async def close(self) -> None:
        if self._own_session and self._session and not self._session.closed:
            await self._session.close()

    def build_prompt(self, telemetry: IntuitionTelemetry) -> str:
        """
        Builds high-density system prompt for Qwen2.5-Coder-1.5B.
        """
        data_json = json.dumps(telemetry.to_compact_dict(), separators=(",", ":"))
        prompt = (
            "<|im_start|>system\n"
            "You are the sub-second Microstructure Intuition Exit engine for Gold (XAUUSD) scalping.\n"
            "Analyze the 1-minute tape telemetry:\n"
            "- If candle_wick_ratio >= 0.55 (severe wick absorption against position),\n"
            "  or volume_stall is true with stalled momentum, or dxy_divergence is true,\n"
            "  output {\"decision\": \"EXIT\"} to protect capital immediately.\n"
            "- If momentum is healthy, candles expand cleanly, and runners can sprint, output {\"decision\": \"HOLD\"}.\n"
            "Respond ONLY with valid JSON conforming to the grammar.\n"
            "<|im_end|>\n"
            f"<|im_start|>user\n{data_json}\n<|im_end|>\n"
            "<|im_start|>assistant\n"
        )
        return prompt

    async def query_intuition_exit(
        self,
        telemetry: IntuitionTelemetry,
    ) -> Tuple[IntuitionDecision, float, str]:
        """
        Asynchronously queries the local llama.cpp server with GBNF grammar constraints.

        Returns: (decision, latency_ms, rationale)
        Guaranteed to execute within timeout_sec (300ms) or fallback to fail-safe HOLD.
        """
        start_t = time.perf_counter()
        prompt = self.build_prompt(telemetry)

        payload = {
            "prompt": prompt,
            "n_predict": 16,
            "temperature": 0.1,
            "stream": False,
            "grammar": GBNF_INTUITION_GRAMMAR,
            "stop": ["<|im_end|>", "\n"],
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
                    emit_telemetry(
                        component="SLMIntuitionEngine",
                        event="LLM_SERVER_NON_200",
                        data={"status": resp.status, "latency_ms": round(latency_ms, 2)},
                        level="WARNING",
                    )
                    return self._fail_safe_evaluation(telemetry, latency_ms, f"HTTP {resp.status}")

                data = await resp.json()
                raw_text = data.get("content", "").strip()

                # Parse JSON constrained by grammar
                try:
                    parsed = json.loads(raw_text)
                    decision_str = parsed.get("decision", "HOLD").upper()
                    decision = IntuitionDecision.EXIT if decision_str == "EXIT" else IntuitionDecision.HOLD
                    emit_telemetry(
                        component="SLMIntuitionEngine",
                        event="LLM_DECISION_RECEIVED",
                        data={
                            "decision": decision.value,
                            "latency_ms": round(latency_ms, 2),
                            "raw": raw_text,
                        },
                    )
                    return decision, latency_ms, "Grammar constrained inference"
                except json.JSONDecodeError:
                    # Fallback pattern match if JSON was slightly truncated
                    if '"EXIT"' in raw_text:
                        return IntuitionDecision.EXIT, latency_ms, "Pattern match fallback (EXIT)"
                    return IntuitionDecision.HOLD, latency_ms, "Pattern match fallback (HOLD)"

        except asyncio.TimeoutError:
            latency_ms = (time.perf_counter() - start_t) * 1000.0
            emit_telemetry(
                component="SLMIntuitionEngine",
                event="LLM_INFERENCE_TIMEOUT",
                data={"timeout_sec": self.timeout_sec, "latency_ms": round(latency_ms, 2)},
                level="WARNING",
            )
            return self._fail_safe_evaluation(telemetry, latency_ms, "Timeout > 300ms")

        except Exception as e:
            latency_ms = (time.perf_counter() - start_t) * 1000.0
            emit_telemetry(
                component="SLMIntuitionEngine",
                event="LLM_INFERENCE_EXCEPTION",
                data={"error": str(e), "latency_ms": round(latency_ms, 2)},
                level="WARNING",
            )
            return self._fail_safe_evaluation(telemetry, latency_ms, f"Error: {e}")

    def _fail_safe_evaluation(
        self,
        telemetry: IntuitionTelemetry,
        latency_ms: float,
        reason: str,
    ) -> Tuple[IntuitionDecision, float, str]:
        """
        Fast-path algorithmic fallback if local LLM service is offline or times out.
        Ensures execution is never blinded by server hiccups.
        """
        # If severe absorption and momentum stalled while in profit, trigger EXIT
        if telemetry.unrealized_r >= 1.0 and telemetry.candle_wick_ratio >= 0.65 and telemetry.volume_stall:
            decision = IntuitionDecision.EXIT
            desc = f"Algorithmic Fail-Safe Triggered: {reason} (Absorption + Volume Stall at +{telemetry.unrealized_r:.1f}R)"
        else:
            decision = IntuitionDecision.HOLD
            desc = f"Algorithmic Fail-Safe Default: {reason} (HOLD active basket)"

        emit_telemetry(
            component="SLMIntuitionEngine",
            event="FAIL_SAFE_EXIT_EVALUATED",
            data={
                "decision": decision.value,
                "latency_ms": round(latency_ms, 2),
                "reason": desc,
            },
        )
        return decision, latency_ms, desc
