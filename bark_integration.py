"""
bark_integration.py
===================
Real-time iOS push notification dispatcher via Bark (https://github.com/Finb/Bark).
Replaces ntfy for iOS devices with rich push alerts, custom icons,
direct deep-links to the Stratton Oakmont HFT web terminal, and priority sounds.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.parse
import urllib.request
from email.header import Header
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("bark_integration")

DEFAULT_BARK_SERVER = "https://api.day.app"
DEFAULT_NTFY_URL = "https://ntfy.sh"
DASHBOARD_URL = os.getenv("SCALPER_DASHBOARD_URL", "https://82-115-21-155.sslip.io/")
ICON_URL = os.getenv("SCALPER_ICON_URL", "https://82-115-21-155.sslip.io/icon-180.png")


def _read_env_value(key: str) -> str:
    for env_path in (
        Path(".env"),
        Path(__file__).resolve().parent / ".env",
        Path("/root/ict_sniper/.env"),
    ):
        if env_path.exists():
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith(f"{key}="):
                            val = line.split("=", 1)[1].strip().strip('"').strip("'")
                            if val:
                                return val
            except Exception as exc:
                logger.debug("Could not read %s for %s: %s", env_path, key, exc)
    return ""


def get_bark_server() -> str:
    server = os.getenv("BARK_SERVER") or _read_env_value("BARK_SERVER") or DEFAULT_BARK_SERVER
    return server.rstrip("/")


_RESOLVED_KEYS_CACHE: Dict[str, str] = {}


def resolve_bark_key(key_or_token: str, server: Optional[str] = None) -> str:
    """
    If the provided string is a 64-character APNs device token, resolves it via
    /register?devicetoken=... to get the active Bark device key.
    """
    cleaned = key_or_token.strip()
    if not cleaned:
        return ""
    if len(cleaned) == 64 and all(c in "0123456789abcdefABCDEF" for c in cleaned):
        if cleaned in _RESOLVED_KEYS_CACHE:
            return _RESOLVED_KEYS_CACHE[cleaned]
        srv = server or get_bark_server()
        try:
            reg_url = f"{srv}/register?devicetoken={cleaned}"
            req = urllib.request.Request(reg_url, headers={"User-Agent": "StrattonOakmont-Quant/1.0"})
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                resolved = data.get("data", {}).get("key") or data.get("data", {}).get("device_key")
                if resolved:
                    _RESOLVED_KEYS_CACHE[cleaned] = resolved
                    return resolved
        except Exception as exc:
            logger.warning("Could not resolve 64-char device token via %s: %s", srv, exc)
    return cleaned


def get_bark_keys() -> List[str]:
    raw = (
        os.getenv("BARK_KEY")
        or os.getenv("BARK_DEVICE_KEY")
        or _read_env_value("BARK_KEY")
        or _read_env_value("BARK_DEVICE_KEY")
        or _read_env_value("BARK_DEVICE_TOKEN")
    )
    raw_keys = [k.strip() for k in str(raw or "").split(",") if k.strip()]
    resolved_keys: List[str] = []
    srv = get_bark_server()
    for k in raw_keys:
        res = resolve_bark_key(k, server=srv)
        if res and res not in resolved_keys:
            resolved_keys.append(res)
    return resolved_keys


def _push_ntfy_fallback(title: str, message: str, priority: str = "high") -> bool:
    """Always-connected multi-topic dispatcher for ntfy alerts."""
    raw_topics = (
        os.getenv("NTFY_TOPIC")
        or _read_env_value("NTFY_TOPIC")
        or "tbt-96c0dc08c297676b"
    )
    shared = os.getenv("NTFY_TOPIC_SHARED") or _read_env_value("NTFY_TOPIC_SHARED") or ""
    all_topics = list(dict.fromkeys([t.strip() for t in (raw_topics + "," + shared).split(",") if t.strip()]))
    if not all_topics:
        all_topics = ["tbt-96c0dc08c297676b"]

    success = False
    for topic in all_topics:
        url = f"{DEFAULT_NTFY_URL}/{topic}"
        try:
            encoded_title = Header(title, "utf-8", maxlinelen=1000).encode().replace("\r", "").replace("\n", "")
            req = urllib.request.Request(
                url,
                data=message.encode("utf-8"),
                headers={"Title": encoded_title, "Priority": priority},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                if resp.status in (200, 201):
                    success = True
        except Exception as e:
            logger.debug("ntfy fallback to %s failed: %s", topic, e)
    return success


def push_bark(
    title: str,
    message: str,
    group: str = "Stratton Oakmont",
    sound: Optional[str] = None,
    level: str = "timeSensitive",
    url: str = DASHBOARD_URL,
    icon: str = ICON_URL,
    badge: Optional[int] = 1,
    priority: Optional[str] = None,
    **kwargs: Any,
) -> bool:
    """
    Dispatches a push notification via Bark to all configured device keys.
    """
    if priority and level == "timeSensitive":
        if priority in ("urgent", "max", "emergency"):
            level = "timeSensitive"
            sound = sound or "alarm"
        elif priority in ("low", "min"):
            level = "passive"
            sound = sound or "glass"

    keys = get_bark_keys()
    if not keys:
        logger.warning("No BARK_KEY found in environment or .env file.")
        return False

    server = get_bark_server()
    success = True

    # Standard Bark sounds: minuet, chime, alarm, bell, electronic, glass, etc.
    chosen_sound = sound or "minuet"
    if not chosen_sound.endswith(".caf"):
        chosen_sound += ".caf"

    for key in keys:
        payload = {
            "body": message,
            "title": title,
            "device_key": key,
            "group": group,
            "icon": icon,
            "url": url,
            "sound": chosen_sound,
            "level": level,  # 'active', 'timeSensitive', 'passive'
        }
        if badge is not None:
            payload["badge"] = badge

        endpoint = f"{server}/push"
        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                endpoint,
                data=req_data,
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "User-Agent": "StrattonOakmont-Quant/1.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                res_body = resp.read().decode("utf-8")
                res_json = json.loads(res_body)
                if res_json.get("code") != 200:
                    logger.warning("Bark push returned non-200 (%s): %s", key[:6] + "...", res_body)
                    success = False
                else:
                    logger.info("✅ Bark push delivered successfully to device %s...", key[:6])
        except Exception as exc:
            logger.warning("Failed to dispatch Bark push to %s...: %s", key[:6], exc)
            success = False

    return success


async def push_bark_async(
    title: str,
    message: str,
    group: str = "Stratton Oakmont",
    sound: Optional[str] = None,
    level: str = "timeSensitive",
    url: str = DASHBOARD_URL,
    icon: str = ICON_URL,
    badge: Optional[int] = 1,
) -> bool:
    """Asynchronously pushes via Bark using an executor thread so event loop never blocks."""
    return await asyncio.to_thread(
        push_bark,
        title=title,
        message=message,
        group=group,
        sound=sound,
        level=level,
        url=url,
        icon=icon,
        badge=badge,
    )


DISCONNECT_BARK_NTFY = False


def send_alert(
    title: str,
    message: str,
    priority: str = "high",
    sound: Optional[str] = None,
    group: str = "Stratton Oakmont",
    url: str = DASHBOARD_URL,
    icon: str = ICON_URL,
) -> bool:
    """
    Primary notification gateway.
    Bark is 100% disconnected per user directive.
    NTFY is the sole active push notification provider.
    """
    ntfy_ok = _push_ntfy_fallback(title=title, message=message, priority=priority)
    logger.info("Dispatched alert '%s' -> Ntfy: %s (Bark disconnected)", title, ntfy_ok)
    return ntfy_ok


async def send_alert_async(
    title: str,
    message: str,
    priority: str = "high",
    sound: Optional[str] = None,
    group: str = "Stratton Oakmont",
    url: str = DASHBOARD_URL,
    icon: str = ICON_URL,
) -> bool:
    """Async wrapper for send_alert."""
    return await asyncio.to_thread(
        send_alert,
        title=title,
        message=message,
        priority=priority,
        sound=sound,
        group=group,
        url=url,
        icon=icon,
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Bark Notification Dispatcher & Tester")
    parser.add_argument("--key", default="", help="Bark device key (overrides .env)")
    parser.add_argument("--title", default="🏛️ Stratton Oakmont Test", help="Notification title")
    parser.add_argument("--message", default="Bark push integration verified successfully.", help="Notification body")
    parser.add_argument("--sound", default="minuet", help="Sound (minuet, alarm, chime, etc.)")
    args = parser.parse_args()

    if args.key:
        os.environ["BARK_KEY"] = args.key

    keys = get_bark_keys()
    print(f"Configured Bark keys: {len(keys)}")
    if not keys:
        print("❌ No BARK_KEY found in environment or .env file.")
        print("Usage: python bark_integration.py --key <YOUR_BARK_KEY>")
        sys.exit(1)

    ok = push_bark(title=args.title, message=args.message, sound=args.sound)
    if ok:
        print("✅ Bark notification dispatched successfully!")
    else:
        print("❌ Failed to dispatch Bark notification.")

