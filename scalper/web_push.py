#!/usr/bin/env python3
"""
Stratton Oakmont Native Web Push Notification Engine
-----------------------------------------------------
Replaces 3rd-party Bark and ntfy services with self-hosted, end-to-end encrypted
W3C Web Push notifications directly through the browser and Progressive Web App (PWA).

Works natively on:
- iOS 16.4+ (Safari PWA on Home Screen)
- Android (Chrome / Edge / Firefox)
- Desktop macOS / Windows / Linux (Chrome / Safari / Edge)
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

logger = logging.getLogger("web_push")

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("SCALPER_DATA", "/root/ict_sniper/data" if Path("/root/ict_sniper").exists() else str(ROOT_DIR / "data")))
VAPID_FILE = DATA_DIR / "vapid.json"
SUBS_FILE = DATA_DIR / "push_subs.json"
VAPID_SUBJECT = "mailto:desk@stratton-oakmont.live"


def get_or_create_vapid() -> Dict[str, str]:
    """Retrieves or creates permanent VAPID keypair."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if VAPID_FILE.exists():
        try:
            with open(VAPID_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "public" in data and "private" in data:
                    return data
        except Exception as e:
            logger.warning("Failed to read vapid.json (%s), regenerating...", e)

    v = Vapid()
    v.generate_keys()
    pub_raw = v.public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    priv_raw = v.private_key.private_numbers().private_value.to_bytes(32, "big")

    b64 = lambda b: base64.urlsafe_b64encode(b).decode("utf-8").rstrip("=")
    key_dict = {
        "public": b64(pub_raw),
        "private": b64(priv_raw),
        "claims": {"sub": VAPID_SUBJECT},
    }

    tmp_file = VAPID_FILE.with_suffix(".tmp")
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(key_dict, f, indent=2)
    os.replace(tmp_file, VAPID_FILE)
    try:
        os.chmod(VAPID_FILE, 0o600)
    except Exception:
        pass
    logger.info("🔑 Generated new permanent VAPID keys for Web Push.")
    return key_dict


def get_vapid_public_key() -> str:
    """Returns the base64 URL-safe public key for client pushManager subscription."""
    keys = get_or_create_vapid()
    return keys["public"]


def load_subscriptions() -> List[Dict[str, Any]]:
    """Loads current subscriber list."""
    if not SUBS_FILE.exists():
        return []
    try:
        with open(SUBS_FILE, "r", encoding="utf-8") as f:
            subs = json.load(f)
            return subs if isinstance(subs, list) else []
    except Exception as e:
        logger.warning("Error reading push_subs.json: %s", e)
        return []


def save_subscriptions(subs: List[Dict[str, Any]]) -> None:
    """Persists subscriber list atomically."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp_file = SUBS_FILE.with_suffix(".tmp")
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(subs, f, indent=2)
    os.replace(tmp_file, SUBS_FILE)


def add_subscription(sub_data: Dict[str, Any]) -> bool:
    """Registers a browser push subscription endpoint."""
    endpoint = sub_data.get("endpoint")
    if not endpoint:
        return False

    subs = load_subscriptions()
    # Check if already present
    for s in subs:
        if s.get("endpoint") == endpoint:
            # Update keys if refreshed
            s["keys"] = sub_data.get("keys", s.get("keys"))
            save_subscriptions(subs)
            return True

    subs.append(sub_data)
    save_subscriptions(subs)
    logger.info("📱 New Web Push subscriber registered (%d active devices).", len(subs))
    return True


def remove_subscription(endpoint: str) -> None:
    """Removes an unsubscribed or expired device."""
    subs = load_subscriptions()
    filtered = [s for s in subs if s.get("endpoint") != endpoint]
    if len(filtered) != len(subs):
        save_subscriptions(filtered)


def send_web_push(
    title: str,
    message: str,
    tag: str = "stratton-trade",
    url: str = "/",
    icon: str = "/icon-180.png",
) -> int:
    """
    Broadcasts encrypted Web Push notification to all registered phones and browsers.
    Automatically purges expired/uninstalled subscriptions (404 / 410 status).
    Returns count of successfully delivered push packets.
    """
    subs = load_subscriptions()
    if not subs:
        return 0

    keys = get_or_create_vapid()
    priv_key = keys["private"]
    claims = dict(keys.get("claims", {"sub": VAPID_SUBJECT}))

    payload = json.dumps({
        "title": title,
        "body": message,
        "tag": tag,
        "url": url,
        "icon": icon,
    })

    sent_count = 0
    surviving_subs: List[Dict[str, Any]] = []

    for sub in subs:
        endpoint = sub.get("endpoint", "")
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=priv_key,
                vapid_claims=claims,
                timeout=8,
            )
            sent_count += 1
            surviving_subs.append(sub)
        except WebPushException as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", 0)
            if status_code in (404, 410):
                logger.info("Expired subscription purged (%s): %s", status_code, endpoint[:40])
            else:
                logger.warning("WebPush transmission warning (HTTP %s): %s", status_code, exc)
                surviving_subs.append(sub)
        except Exception as exc:
            logger.warning("WebPush general dispatch error: %s", exc)
            surviving_subs.append(sub)

    if len(surviving_subs) != len(subs):
        save_subscriptions(surviving_subs)

    logger.info("🔔 Web Push dispatched: '%s' -> %d/%d devices delivered.", title, sent_count, len(subs))
    return sent_count


# Quick helper shortcuts for trading signals
def push_trade_signal(title: str, message: str) -> int:
    return send_web_push(title=title, message=message, tag="trade-signal")


def push_doctor_alert(title: str, message: str) -> int:
    return send_web_push(title=title, message=message, tag="doctor-alert")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    pub = get_vapid_public_key()
    print(f"VAPID Public Key: {pub}")
    active = len(load_subscriptions())
    print(f"Active Subscriptions: {active}")
