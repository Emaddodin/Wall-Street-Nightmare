"""
ntfy_integration.py
===================
Real-time asynchronous push notification dispatcher for HyperPredator Market Replay.

Dispatches structured HTTP POST alerts to ntfy.sh tagged with [REPLAY] markers:
- 🟢 [REPLAY] SPAM FIRED
- 🔵 [REPLAY] MICRO-EXIT
- 🚨 [REPLAY] EQUITY SHIELD
- 🧠 [REPLAY] LLM SHIFT
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ntfy_integration")

DEFAULT_NTFY_TOPIC = "tbt-96c0dc08c297676b"


def _read_env_value(key: str) -> str:
    for env_path in (Path(".env"), Path(__file__).resolve().parent / ".env"):
        if env_path.exists():
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if key == "NTFY_TOPIC" and line.startswith("NTFY_TOPIC_SHARED="):
                            continue
                        if line.startswith(f"{key}="):
                            val = line.split("=", 1)[1].strip()
                            if val:
                                return val
            except Exception as exc:
                logger.warning("Could not read %s for %s: %s", env_path, key, exc)
    return ""


def load_all_ntfy_topics() -> List[str]:
    """Every topic that must receive alerts: NTFY_TOPIC + NTFY_TOPIC_SHARED comma list."""
    seen: List[str] = []
    for key in ("NTFY_TOPIC", "NTFY_TOPIC_SHARED"):
        raw = os.getenv(key) or _read_env_value(key)
        for part in str(raw or "").split(","):
            t = part.strip()
            if t and t not in seen:
                seen.append(t)
    return seen or [DEFAULT_NTFY_TOPIC]


def load_ntfy_topic_from_env() -> str:
    """Reads NTFY_TOPIC from environment or .env file (first topic only)."""
    topic = os.getenv("NTFY_TOPIC")
    if topic:
        return topic.split(",")[0].strip()

    val = _read_env_value("NTFY_TOPIC")
    if val:
        return val.split(",")[0].strip()

    return DEFAULT_NTFY_TOPIC


class ReplayNtfyDispatcher:
    """
    Asynchronous notification dispatcher for market replay events.
    Never blocks the execution loop.
    """

    def __init__(
        self,
        topic: Optional[str] = None,
        base_url: str = "https://ntfy.sh",
        enabled: bool = True,
    ) -> None:
        if topic:
            self.topics: List[str] = [t.strip() for t in str(topic).split(",") if t.strip()]
        else:
            self.topics = load_all_ntfy_topics()
        self.topic = self.topics[0]
        self.base_url = base_url.rstrip("/")
        self.urls = [f"{self.base_url}/{t}" for t in self.topics]
        self.url = self.urls[0]
        self.enabled = enabled
        self._sent_messages: list[Dict[str, Any]] = []

    async def _post_alert(
        self,
        title: str,
        message: str,
        priority: str = "default",
        tags: Optional[str] = None,
    ) -> bool:
        """Dispatches an HTTP POST request to ALL ntfy topics (phone + shared)."""
        record = {
            "title": title,
            "message": message,
            "priority": priority,
            "tags": tags,
            "url": self.url,
            "urls": list(self.urls),
            "topics": list(self.topics),
        }
        self._sent_messages.append(record)

        if not self.enabled:
            logger.debug("[NTFY-MOCK] %s: %s", title, message)
            return True

        headers = {
            "Title": title,
            "Priority": priority,
        }
        if tags:
            headers["Tags"] = tags

        ok_any = False
        try:
            import aiohttp
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3.0)) as session:
                for url, tp in zip(self.urls, self.topics):
                    try:
                        async with session.post(url, data=message.encode("utf-8"), headers=headers) as resp:
                            if resp.status in (200, 201):
                                logger.info("ntfy alert dispatched to %s: %s", tp, title)
                                ok_any = True
                            else:
                                logger.warning("ntfy %s returned HTTP %s for %s", tp, resp.status, title)
                    except Exception as exc:
                        logger.warning("Failed to dispatch ntfy alert '%s' to %s: %s", title, tp, exc)
            return ok_any
        except Exception as exc:
            logger.warning("Failed to dispatch ntfy alert '%s': %s", title, exc)
            return False

    def notify_spam_fired(
        self,
        sim_time_str: str,
        coin: str,
        direction: str,
        total_sz: float,
        price: float,
        slices: int = 5,
    ) -> asyncio.Task:
        """
        🟢 [REPLAY] SPAM FIRED: At simulated time [HH:MM:SS], injected [total_sz] orders across 5 slices at [Price].
        """
        title = f"🟢 [REPLAY] SPAM FIRED: {direction} {coin}"
        message = (
            f"Simulated Time: {sim_time_str}\n"
            f"Direction: {direction}\n"
            f"Total Size: {total_sz:.2f} ({slices} slices)\n"
            f"Execution Price: ${price:.2f}\n"
            f"Leverage: 100x"
        )
        return asyncio.create_task(
            self._post_alert(title, message, priority="high", tags="green_circle,zap")
        )

    def notify_micro_exit(
        self,
        sim_time_str: str,
        coin: str,
        exit_price: float,
        pnl: float,
        reason: str,
    ) -> asyncio.Task:
        """
        🔵 [REPLAY] MICRO-EXIT: Closed basket at [Price]. PnL: [+$X.XX] / [-$X.XX] via [REASON].
        """
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        title = f"🔵 [REPLAY] MICRO-EXIT ({pnl_str})"
        message = (
            f"Simulated Time: {sim_time_str}\n"
            f"Asset: {coin}\n"
            f"Exit Price: ${exit_price:.2f}\n"
            f"Basket PnL: {pnl_str}\n"
            f"Reason: {reason}"
        )
        return asyncio.create_task(
            self._post_alert(title, message, priority="default", tags="blue_circle,check")
        )

    def notify_equity_shield(
        self,
        sim_time_str: str,
        coin: str,
        pnl: float,
        exit_price: float,
    ) -> asyncio.Task:
        """
        🚨 [REPLAY] EQUITY SHIELD: Hard stop breached at [HH:MM:SS]. Panic liquidation executed.
        """
        title = "🚨 [REPLAY] EQUITY SHIELD TRIGGERED"
        message = (
            f"Simulated Time: {sim_time_str}\n"
            f"Asset: {coin}\n"
            f"Floating Loss: -${abs(pnl):.2f} (Breached -$10.00 ceiling)\n"
            f"Exit Price: ${exit_price:.2f}\n"
            f"Action: IMMEDIATE PANIC LIQUIDATION"
        )
        return asyncio.create_task(
            self._post_alert(title, message, priority="urgent", tags="rotating_light,skull")
        )

    def notify_llm_shift(
        self,
        sim_time_str: str,
        bias: str,
        regime: float,
        permit_trade: bool,
    ) -> asyncio.Task:
        """
        🧠 [REPLAY] LLM SHIFT: Macro brain shifted bias to [BULLISH/BEARISH] (Regime: [X.XX]).
        """
        status = "PERMITTED" if permit_trade else "HALTED"
        title = f"🧠 [REPLAY] LLM SHIFT: {bias} ({regime:.2f}x)"
        message = (
            f"Simulated Time: {sim_time_str}\n"
            f"Macro Bias: {bias}\n"
            f"Volatility Regime: {regime:.2f}\n"
            f"Trade Status: {status}"
        )
        return asyncio.create_task(
            self._post_alert(title, message, priority="default", tags="brain,chart_with_upwards_trend")
        )
