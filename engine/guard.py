"""
engine/guard.py
===============
Autonomous Health & Watchdog Guard for the Relapse Scalper & LLM Critic Ecosystem.
Monitors the execution engine (relapse-scalper.service), the local LLM critic
(stratton-llm-critic.service), resident memory ceiling (<= 2560 MB / 2.5 GB),
and venue connectivity. Self-heals services, emits Antigravity JSON structured
telemetry, and escalates critical alerts via ntfy.sh.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path("/root/ict_sniper") if Path("/root/ict_sniper").exists() else Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / "data" / "state" / "scalper_telemetry.json"
GUARD_STATE = ROOT / "data" / "state" / "guard_history.json"

SCALPER_UNIT = "relapse-scalper.service"
CRITIC_UNIT = "stratton-llm-critic.service"
HFT_UNIT = SCALPER_UNIT  # Backward compatibility alias
CRITIC_HEALTH_URL = "http://127.0.0.1:8080/health"
MAX_SYSTEM_RAM_MB = 2560.0  # 2.5 GB resident memory ceiling
MAX_STALE_SEC = 45.0
VENUE_URL = "https://api.hyperliquid.xyz/info"


def emit_telemetry(
    component: str,
    event: str,
    data: Dict[str, Any],
    level: str = "INFO",
) -> Dict[str, Any]:
    """
    Emit Antigravity structured JSON telemetry line to stdout/stderr.
    Schema: {"timestamp": ..., "agent": "watchdog_guard",
             "component": ..., "event": ..., "level": ..., "data": ...}
    """
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": "watchdog_guard",
        "component": component,
        "event": event,
        "level": level,
        "data": data,
    }
    log_line = json.dumps(payload, separators=(",", ":"))
    if level in ("CRITICAL", "ERROR"):
        print(log_line, file=sys.stderr, flush=True)
    else:
        print(log_line, file=sys.stdout, flush=True)
    return payload


def check_critic_health(url: str = CRITIC_HEALTH_URL, timeout: float = 5.0) -> bool:
    """Check if local llama-server critic health endpoint responds with HTTP 200."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            return code == 200
    except Exception:
        return False


def get_system_ram_used_mb() -> float:
    """Returns total system resident memory used in MB (MemTotal - MemAvailable) from /proc/meminfo."""
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        try:
            total = 0.0
            avail = 0.0
            for line in meminfo.read_text().splitlines():
                if line.startswith("MemTotal:"):
                    total = float(line.split()[1]) / 1024.0
                elif line.startswith("MemAvailable:"):
                    avail = float(line.split()[1]) / 1024.0
            if total > 0 and avail > 0:
                return total - avail
        except Exception:
            pass
    return 0.0


def get_env_val(key: str, default: str = "") -> str:
    """Retrieve environment variable value from os.environ or .env file."""
    v = os.getenv(key)
    if v:
        return v
    env_file = ROOT / ".env"
    if env_file.exists():
        try:
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip()
        except Exception:
            pass
    return default


def push_ntfy(
    title: str,
    message: str,
    tags: str = "warning",
    priority: str = "high",
    topic: str = "",
) -> bool:
    """
    Send push notification alert via ntfy.sh.
    Supports urgent priority escalation for critical faults.
    """
    topic = topic or get_env_val("NTFY_TOPIC", "tbt-gold-scalper")
    url = f"https://ntfy.sh/{topic}"
    try:
        from email.header import Header
        encoded_title = Header(title, "utf-8").encode()
        req = urllib.request.Request(
            url,
            data=message.encode("utf-8"),
            headers={"Title": encoded_title, "Tags": tags, "Priority": priority},
        )
        urllib.request.urlopen(req, timeout=10).close()
        emit_telemetry(
            component="AlertEscalation",
            event="NTFY_ALERT_DISPATCHED",
            data={"title": title, "topic": topic, "priority": priority, "tags": tags},
            level="INFO",
        )
        return True
    except Exception as e:
        emit_telemetry(
            component="AlertEscalation",
            event="NTFY_ALERT_FAILED",
            data={"error": str(e), "topic": topic, "priority": priority},
            level="ERROR",
        )
        print(f"Ntfy alert failed: {e}", file=sys.stderr, flush=True)
        return False


def is_service_active(unit: str) -> bool:
    """Check systemd unit active status via systemctl is-active."""
    try:
        res = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=10)
        return res.stdout.strip() == "active"
    except Exception:
        return False


def restart_service(unit: str) -> bool:
    """Restart systemd unit via systemctl restart."""
    try:
        subprocess.run(["systemctl", "restart", unit], timeout=15)
        emit_telemetry(
            component="ServiceMonitor",
            event="SERVICE_RESTARTED",
            data={"unit": unit},
            level="WARNING",
        )
        return True
    except Exception as e:
        emit_telemetry(
            component="ServiceMonitor",
            event="SERVICE_RESTART_FAILED",
            data={"unit": unit, "error": str(e)},
            level="ERROR",
        )
        print(f"Failed restarting {unit}: {e}", file=sys.stderr, flush=True)
        return False


def check_venue() -> bool:
    """Verify Hyperliquid venue connectivity."""
    try:
        req = urllib.request.Request(
            VENUE_URL,
            data=b'{"type":"allMids"}',
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return len(data) > 0
    except Exception:
        return False


def run_guard(notify: bool = True) -> int:
    """
    Execute a full watchdog health audit cycle across services, memory, and venue.
    Emits Antigravity structured JSON telemetry and triggers ntfy.sh escalation.
    Returns 0 if all nominal, 1 if any faults were detected.
    """
    faults: List[str] = []
    healed: List[str] = []
    is_critical_escalation: bool = False

    emit_telemetry(
        component="Watchdog",
        event="AUDIT_CYCLE_STARTED",
        data={"scalper_unit": SCALPER_UNIT, "critic_unit": CRITIC_UNIT, "ram_ceiling_mb": MAX_SYSTEM_RAM_MB},
        level="INFO",
    )

    # 1. Scalper Engine Service Check (relapse-scalper.service)
    if not is_service_active(SCALPER_UNIT):
        faults.append(f"Service {SCALPER_UNIT} is INACTIVE")
        is_critical_escalation = True
        if restart_service(SCALPER_UNIT):
            healed.append(f"Auto-Restarted {SCALPER_UNIT}")
    else:
        # Check telemetry freshness if state file exists
        if STATE_FILE.exists():
            try:
                st = json.loads(STATE_FILE.read_text())
                updated_at = float(st.get("updated_at", st.get("timestamp", 0)))
                if updated_at > 0:
                    age = time.time() - updated_at
                    if age > MAX_STALE_SEC:
                        faults.append(f"Telemetry Stale ({int(age)}s > {int(MAX_STALE_SEC)}s)")
                        if restart_service(SCALPER_UNIT):
                            healed.append(f"Self-Healed: Restarted {SCALPER_UNIT} due to stale telemetry")
            except Exception as e:
                faults.append(f"Failed parsing state file {STATE_FILE.name}: {e}")

    # 2. Local LLM Critic Service Check (stratton-llm-critic.service / llama-server)
    if not is_service_active(CRITIC_UNIT):
        faults.append(f"Service {CRITIC_UNIT} is INACTIVE")
        is_critical_escalation = True
        if restart_service(CRITIC_UNIT):
            healed.append(f"Auto-Restarted {CRITIC_UNIT}")
    else:
        if not check_critic_health():
            faults.append(f"Service {CRITIC_UNIT} is active but health check failed ({CRITIC_HEALTH_URL})")
            is_critical_escalation = True
            if restart_service(CRITIC_UNIT):
                healed.append(f"Auto-Restarted {CRITIC_UNIT} (unresponsive health)")

    # 3. Hyperliquid Venue Connectivity Check
    if not check_venue():
        faults.append("Hyperliquid API unreachable from VPS")

    # 4. Storage & System Resident RAM Audit
    du = shutil.disk_usage("/")
    if (du.used / du.total) > 0.92:
        faults.append(f"Disk storage critical: {du.used / du.total * 100:.1f}% used")

    used_ram_mb = get_system_ram_used_mb()
    if used_ram_mb > MAX_SYSTEM_RAM_MB:
        is_critical_escalation = True
        faults.append(
            f"System RAM ceiling breached: {used_ram_mb:.1f} MB > {MAX_SYSTEM_RAM_MB:.1f} MB (2.5 GB limit)"
        )
        emit_telemetry(
            component="MemoryGuard",
            event="RAM_CEILING_BREACH",
            data={"used_ram_mb": used_ram_mb, "max_system_ram_mb": MAX_SYSTEM_RAM_MB},
            level="CRITICAL",
        )

    # 5. State Persistence & Alert Escalation
    was_faults: List[str] = []
    if GUARD_STATE.exists():
        try:
            was_faults = json.loads(GUARD_STATE.read_text()).get("faults", [])
        except Exception:
            pass

    if faults:
        priority = "urgent" if is_critical_escalation else "high"
        tags = "rotating_light,fire,warning" if is_critical_escalation else "warning"
        level = "CRITICAL" if is_critical_escalation else "WARNING"

        emit_telemetry(
            component="Watchdog",
            event="FAULTS_DETECTED",
            data={
                "fault_count": len(faults),
                "faults": faults,
                "healed": healed,
                "ram_used_mb": used_ram_mb,
                "critical_escalation": is_critical_escalation,
            },
            level=level,
        )

        if notify:
            title = f"HFT GUARD: {len(faults)} FAULT(S) DETECTED"
            msg = "🚨 [RELAPSE WATCHDOG GUARD ALERT]\n\n" + "\n".join(f"• {f}" for f in faults)
            if healed:
                msg += "\n\n🛠️ [SELF-HEALING EXECUTED]:\n" + "\n".join(f"• {h}" for h in healed)
            push_ntfy(title, msg, tags=tags, priority=priority)
    else:
        emit_telemetry(
            component="Watchdog",
            event="SYSTEM_NOMINAL",
            data={"status": "NOMINAL", "ram_used_mb": used_ram_mb, "scalper": SCALPER_UNIT, "critic": CRITIC_UNIT},
            level="INFO",
        )
        if was_faults and notify:
            title = "HFT GUARD: ALL CLEAR (RECOVERED)"
            msg = (
                "🟢 [RELAPSE ECOSYSTEM HEALTHY]\n\n"
                "All services, LLM critic endpoints, and venue feeds are fully nominal."
            )
            push_ntfy(title, msg, tags="white_check_mark,shield", priority="default")

    try:
        GUARD_STATE.parent.mkdir(parents=True, exist_ok=True)
        GUARD_STATE.write_text(json.dumps({"faults": faults, "ts": time.time()}))
    except Exception:
        pass

    if faults:
        print(f"GUARD: {len(faults)} fault(s) detected: {', '.join(faults)}")
        return 1
    else:
        print("GUARD: All systems NOMINAL (0 faults)")
        return 0


def main() -> int:
    """CLI entrypoint supporting --oneshot, --no-notify, and --loop."""
    parser = argparse.ArgumentParser(description="Relapse Scalper & VPS System Watchdog Guard")
    parser.add_argument(
        "--oneshot",
        action="store_true",
        default=True,
        help="Execute single watchdog check cycle and exit",
    )
    parser.add_argument("--no-notify", action="store_true", help="Suppress ntfy.sh push notifications")
    parser.add_argument("--loop", action="store_true", help="Run continuously in a periodic loop")
    parser.add_argument("--interval", type=int, default=30, help="Cadence in seconds for loop mode")
    args = parser.parse_args()

    notify_flag = not args.no_notify

    if args.loop:
        emit_telemetry(
            component="Watchdog",
            event="WATCHDOG_DAEMON_START",
            data={"interval": args.interval, "notify": notify_flag},
            level="INFO",
        )
        while True:
            run_guard(notify=notify_flag)
            time.sleep(args.interval)

    return run_guard(notify=notify_flag)


if __name__ == "__main__":
    sys.exit(main())
