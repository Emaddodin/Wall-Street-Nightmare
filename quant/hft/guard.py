"""
quant/hft/guard.py
==================
Autonomous Health & Watchdog Guard for the HFT Quant Ecosystem.
Monitors the execution engine, the terminal app, L2 book freshness, and
venue reachability. Self-heals services and sends alerts via ntfy.sh.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

ROOT = Path("/root/ict_sniper") if Path("/root/ict_sniper").exists() else Path(__file__).resolve().parents[2]
STATE_FILE = ROOT / "data" / "state" / "hft.json"
GUARD_STATE = ROOT / "data" / "state" / "guard_history.json"

HFT_UNIT = "tbt-hl-hft"
APP_UNIT = "tbt-hl-app"
MAX_STALE_SEC = 45.0
VENUE_URL = "https://api.hyperliquid.xyz/info"


def get_env_val(key: str, default: str = "") -> str:
    v = os.getenv(key)
    if v:
        return v
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return default


def push_ntfy(title: str, message: str, tags: str = "warning", priority: str = "high") -> None:
    topic = get_env_val("NTFY_TOPIC", "tbt-96c0dc08c297676b")
    url = f"https://ntfy.sh/{topic}"
    try:
        req = urllib.request.Request(
            url,
            data=message.encode("utf-8"),
            headers={"Title": title, "Tags": tags, "Priority": priority},
        )
        urllib.request.urlopen(req, timeout=10).close()
    except Exception as e:
        print(f"Ntfy alert failed: {e}", file=sys.stderr)


def is_service_active(unit: str) -> bool:
    try:
        res = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=10)
        return res.stdout.strip() == "active"
    except Exception:
        return False


def restart_service(unit: str) -> bool:
    try:
        subprocess.run(["systemctl", "restart", unit], timeout=15)
        return True
    except Exception as e:
        print(f"Failed restarting {unit}: {e}", file=sys.stderr)
        return False


def check_venue() -> bool:
    try:
        req = urllib.request.Request(VENUE_URL, data=b'{"type":"allMids"}', headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return len(data) > 0
    except Exception:
        return False


def run_guard(notify: bool = True) -> int:
    faults: list[str] = []
    healed: list[str] = []

    # 1. Engine Service Check
    if not is_service_active(HFT_UNIT):
        faults.append(f"Service {HFT_UNIT} is INACTIVE")
        if restart_service(HFT_UNIT):
            healed.append(f"Auto-Restarted {HFT_UNIT}")
    else:
        # Check feed freshness
        if STATE_FILE.exists():
            try:
                st = json.loads(STATE_FILE.read_text())
                age = time.time() - float(st.get("updated_at", 0))
                if age > MAX_STALE_SEC:
                    faults.append(f"L2 Telemetry Stale ({int(age)}s > {int(MAX_STALE_SEC)}s)")
                    if restart_service(HFT_UNIT):
                        healed.append(f"Self-Healed: Restarted {HFT_UNIT} due to stale L2 feed")
            except Exception as e:
                faults.append(f"Failed parsing hft.json: {e}")
        else:
            faults.append("hft.json state file missing")

    # 2. Terminal App Service Check
    if not is_service_active(APP_UNIT):
        faults.append(f"Service {APP_UNIT} is INACTIVE")
        if restart_service(APP_UNIT):
            healed.append(f"Auto-Restarted {APP_UNIT}")

    # 3. Venue Check
    if not check_venue():
        faults.append("Hyperliquid API unreachable from VPS")

    # 4. Storage & Memory Check
    du = shutil.disk_usage("/")
    if (du.used / du.total) > 0.92:
        faults.append(f"Disk storage critical: {du.used / du.total * 100:.1f}% used")

    # Historical state comparison for deduplicated notifications
    was_faults: list[str] = []
    if GUARD_STATE.exists():
        try:
            was_faults = json.loads(GUARD_STATE.read_text()).get("faults", [])
        except Exception:
            pass

    if faults and notify:
        title = f"HFT GUARD: {len(faults)} FAULT(S) DETECTED"
        msg = "🚨 [HFT AUTONOMOUS GUARD ALERT]\n\n" + "\n".join(f"• {f}" for f in faults)
        if healed:
            msg += "\n\n🛠️ [SELF-HEALING EXECUTED]:\n" + "\n".join(f"• {h}" for h in healed)
        push_ntfy(title, msg, tags="rotating_light,warning", priority="urgent")
    elif not faults and was_faults and notify:
        title = "HFT GUARD: ALL CLEAR (RECOVERED)"
        msg = "🟢 [HFT ECOSYSTEM HEALTHY]\n\nAll services, L2 feed updates, and venue feeds are fully nominal."
        push_ntfy(title, msg, tags="white_check_mark,shield", priority="default")

    GUARD_STATE.parent.mkdir(parents=True, exist_ok=True)
    GUARD_STATE.write_text(json.dumps({"faults": faults, "ts": time.time()}))

    if faults:
        print(f"GUARD: {len(faults)} fault(s) detected: {', '.join(faults)}")
        return 1
    else:
        print("GUARD: All systems NOMINAL (0 faults)")
        return 0


if __name__ == "__main__":
    notify_flag = "--no-notify" not in sys.argv
    sys.exit(run_guard(notify=notify_flag))
