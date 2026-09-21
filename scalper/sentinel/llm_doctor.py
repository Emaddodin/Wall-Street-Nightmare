"""
scalper/sentinel/llm_doctor.py
==============================
Autonomous LLM Self-Healing Doctor ("Aescalapius Sentinel")
Directly interfaces with local Qwen2.5-1.5B LLM (llama-server on 127.0.0.1:8080).
Continuously surveils:
- Live service process liveness & state transitions
- Market quote freshness & tick stream stalls (>15s)
- Unhandled Python exceptions / Playwright CDP socket drops in journalctl
- Chrome zombie processes & memory leaks
- Real-time diagnostic triage and autonomous remediation execution
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure root directory is in python path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from bark_integration import send_alert, push_bark
except ImportError:
    send_alert = None
    push_bark = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [DOCTOR-%(levelname)s]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("llm_doctor")

# Configuration Defaults
DATA_DIR = ROOT_DIR / "data"
STATE_DIR = DATA_DIR / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
DOCTOR_STATE_FILE = STATE_DIR / "doctor_telemetry.json"
COMMAND_FILE = DATA_DIR / "command.json"

LLAMA_URL = os.getenv("LLAMA_SERVER_URL", "http://127.0.0.1:8080/completion")
HFT_API_URL = os.getenv("HFT_API_URL", "http://127.0.0.1:8088/api/hft")
LIVE_SERVICE_NAME = os.getenv("LIVE_SERVICE_NAME", "stratton-xau-live.service")
NTFY_URL = os.getenv("NTFY_URL", "https://ntfy.sh/tbt-96c0dc08c297676b")

MAX_QUOTE_STALL_SEC = 20.0
POLL_INTERVAL_SEC = 4.0


@dataclass
class IncidentReport:
    incident_type: str
    severity: str
    observed_latency_sec: float
    recent_errors: List[str]
    systemd_state: str
    chrome_proc_count: int
    timestamp: float


@dataclass
class Prescription:
    diagnosis: str
    severity: str
    prescribed_action: str
    explanation: str
    notify_user: bool


class LLMDoctor:
    """
    Autonomous Medical Doctor & Recovery Sentinel for the HFT trading bot.
    """

    def __init__(self, llama_url: str = LLAMA_URL, api_url: str = HFT_API_URL):
        self.llama_url = llama_url
        self.api_url = api_url
        self.total_healings = 0
        self.last_healing_time: Optional[float] = None
        self.last_prescription: Optional[Dict[str, Any]] = None
        self.health_status = "SURVEILLANCE_ACTIVE"
        self._consecutive_fails = 0
        self._last_alert_time = 0.0

    def query_hft_api(self) -> Optional[Dict[str, Any]]:
        """Queries local HFT engine API for quote age and operational metrics."""
        try:
            req = urllib.request.Request(self.api_url, headers={"User-Agent": "StrattonDoctor/2.0"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception:
            pass
        return None

    def get_service_status(self) -> str:
        """Checks if the systemd live service is running."""
        try:
            res = subprocess.run(
                ["systemctl", "is-active", LIVE_SERVICE_NAME],
                capture_output=True,
                text=True,
                timeout=3,
            )
            return res.stdout.strip()
        except Exception:
            return "unknown"

    def get_recent_journal_errors(self, lines: int = 30) -> List[str]:
        """Scans journalctl for recent crashes, timeouts, and python tracebacks."""
        try:
            res = subprocess.run(
                ["journalctl", "-u", LIVE_SERVICE_NAME, "-n", str(lines), "--no-pager"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            out = res.stdout or ""
            suspicious = []
            for line in out.splitlines():
                l_low = line.lower()
                if any(k in l_low for k in ["traceback", "timeouterror", "crashed", "connection closed", "target closed", "errno", "failed to read quote", "out of memory"]):
                    suspicious.append(line.strip())
            return suspicious[-5:]
        except Exception:
            return []

    def get_chrome_process_count(self) -> int:
        """Counts active Chrome instances."""
        try:
            res = subprocess.run(["pgrep", "-c", "-f", "chrome"], capture_output=True, text=True, timeout=2)
            if res.returncode == 0:
                return int(res.stdout.strip())
        except Exception:
            pass
        return 0

    def consult_llm_doctor(self, incident: IncidentReport) -> Prescription:
        """
        Consults local Qwen2.5-1.5B LLM to diagnose root cause and prescribe targeted remedy.
        """
        prompt = (
            "<|im_start|>system\n"
            "You are the Stratton Oakmont Autonomous Chief Medical Officer & Systems Doctor for high-frequency trading.\n"
            "Analyze the telemetry incident report and output a strictly valid JSON diagnosis.\n"
            "Available prescribed_action options:\n"
            "- 'RESTART_LIVE_SERVICE': Full systemd restart of the trading engine\n"
            "- 'RECYCLE_CHROME': Kill hung Chrome renderers & reload gateway\n"
            "- 'DISMISS_OVERLAYS': Send external command to dismiss blocking popups\n"
            "- 'OBSERVE_PASS': False alarm or transient flicker, do not disrupt trading\n\n"
            "Format strictly as JSON with keys:\n"
            "{\n"
            "  \"diagnosis\": \"SHORT_NAME\",\n"
            "  \"severity\": \"LOW\" | \"MEDIUM\" | \"HIGH\" | \"CRITICAL\",\n"
            "  \"prescribed_action\": \"ACTION_NAME\",\n"
            "  \"explanation\": \"Brief clinical summary in 1 sentence\",\n"
            "  \"notify_user\": true | false\n"
            "}\n"
            "<|im_end|>\n"
            f"<|im_start|>user\n{json.dumps(asdict(incident), indent=2)}\n<|im_end|>\n"
            "<|im_start|>assistant\n"
        )

        payload = {
            "prompt": prompt,
            "n_predict": 80,
            "temperature": 0.1,
            "stream": False,
            "stop": ["<|im_end|>", "\n\n\n"],
        }

        try:
            req = urllib.request.Request(
                self.llama_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                if resp.status == 200:
                    raw = json.loads(resp.read().decode("utf-8"))
                    content = raw.get("content", "").strip()
                    m = re.search(r"\{.*\}", content, re.DOTALL)
                    if m:
                        parsed = json.loads(m.group(0))
                        return Prescription(
                            diagnosis=parsed.get("diagnosis", "HEURISTIC_TRIAGE"),
                            severity=parsed.get("severity", "HIGH"),
                            prescribed_action=parsed.get("prescribed_action", "RESTART_LIVE_SERVICE"),
                            explanation=parsed.get("explanation", "Autonomous recovery prescribed."),
                            notify_user=bool(parsed.get("notify_user", True)),
                        )
        except Exception as err:
            logger.warning("Local LLM consultation failed (%s), falling back to deterministic emergency reflex.", err)

        # High-Speed Deterministic Reflex Fallback if LLM is cold
        if incident.systemd_state != "active":
            action = "RESTART_LIVE_SERVICE"
            diag = "SERVICE_UNHEALTHY_OR_DEAD"
        elif incident.observed_latency_sec > MAX_QUOTE_STALL_SEC:
            action = "RECYCLE_CHROME"
            diag = "MARKET_QUOTE_FEED_STALL"
        else:
            action = "RESTART_LIVE_SERVICE"
            diag = "RUNTIME_EXCEPTION_TRIP"

        return Prescription(
            diagnosis=diag,
            severity="HIGH",
            prescribed_action=action,
            explanation=f"Deterministic heuristic recovery: {diag}",
            notify_user=True,
        )

    def actuate_remediation(self, prescription: Prescription) -> bool:
        """Executes the prescribed clinical remediation action."""
        action = prescription.prescribed_action.upper()
        logger.warning("⚡ EXECUTING PRESCRIPTION: %s -> %s (%s)", prescription.diagnosis, action, prescription.explanation)

        success = False
        t0 = time.perf_counter()

        try:
            if action == "RECYCLE_CHROME":
                # Kill stale Chrome instances and let Playwright self-heal or restart service
                subprocess.run(["pkill", "-9", "-f", "chrome"], capture_output=True, timeout=3)
                subprocess.run(["systemctl", "restart", LIVE_SERVICE_NAME], capture_output=True, timeout=5)
                success = True

            elif action == "RESTART_LIVE_SERVICE":
                subprocess.run(["systemctl", "restart", LIVE_SERVICE_NAME], capture_output=True, timeout=5)
                success = True

            elif action == "DISMISS_OVERLAYS":
                try:
                    with open(COMMAND_FILE, "w") as f:
                        json.dump({"action": "CLEAR_OVERLAYS", "timestamp": time.time()}, f)
                    success = True
                except Exception as ce:
                    logger.warning("Could not write command file: %s", ce)

            elif action == "OBSERVE_PASS":
                logger.info("Doctor opted to observe without intrusive action.")
                return True

            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            if success:
                self.total_healings += 1
                self.last_healing_time = time.time()
                logger.info("✅ Remediation %s completed successfully in %.1fms.", action, elapsed_ms)

                if prescription.notify_user:
                    self._dispatch_push_alert(prescription, elapsed_ms)

            return success
        except Exception as e:
            logger.error("Remediation execution failed: %s", e)
            return False

    def _dispatch_push_alert(self, p: Prescription, elapsed_ms: float) -> None:
        """Sends instant Bark & ntfy notifications to the user."""
        now = time.time()
        if (now - self._last_alert_time) < 15.0:
            return  # Debounce spam
        self._last_alert_time = now

        title = f"🏥 Stratton LLM Doctor Healed Engine ({p.diagnosis})"
        body = (
            f"Prescription: {p.prescribed_action}\n"
            f"Clinical Summary: {p.explanation}\n"
            f"Recovery Latency: {elapsed_ms:.0f}ms\n"
            f"Total Heals: {self.total_healings}"
        )

        # Bark Push
        if send_alert:
            try:
                send_alert(title, body, group="stratton-doctor")
            except Exception:
                pass

        # ntfy Push
        try:
            req = urllib.request.Request(
                NTFY_URL,
                data=body.encode("utf-8"),
                headers={
                    "Title": title,
                    "Priority": "high" if p.severity in ("HIGH", "CRITICAL") else "default",
                    "Tags": "hospital,shield,zap",
                },
                method="POST",
            )
            urllib.request.urlopen(req, timeout=2.0)
        except Exception:
            pass

    def export_telemetry(self) -> None:
        """Exports doctor health state for dashboard and API."""
        now = time.time()
        telemetry = {
            "doctor_status": self.health_status,
            "model": "Qwen2.5-1.5B-Instruct",
            "runtime": "llama-server :8080",
            "total_healings": self.total_healings,
            "last_healing_iso": datetime.fromtimestamp(self.last_healing_time, tz=timezone.utc).isoformat() if self.last_healing_time else "None",
            "last_prescription": self.last_prescription,
            "monitored_service": LIVE_SERVICE_NAME,
            "updated_at": now,
        }
        try:
            with open(DOCTOR_STATE_FILE, "w") as f:
                json.dump(telemetry, f, indent=2)
        except Exception:
            pass

    def run_surveillance_cycle(self) -> None:
        """Single monitoring and diagnostic pass."""
        svc_state = self.get_service_status()
        hft_data = self.query_hft_api()
        errors = self.get_recent_journal_errors()
        chrome_count = self.get_chrome_process_count()

        now = time.time()
        quote_age = 0.0
        if hft_data:
            updated_at = hft_data.get("updated_at", now)
            quote_age = max(0.0, now - updated_at)

        anomaly_detected = False
        incident_type = "NORMAL"

        # Check 1: Is service completely dead?
        if svc_state != "active":
            anomaly_detected = True
            incident_type = "SERVICE_CRASHED"

        # Check 2: Is quote stream frozen while market is supposed to tick?
        elif quote_age > MAX_QUOTE_STALL_SEC:
            anomaly_detected = True
            incident_type = "QUOTE_STREAM_STALL"

        # Check 3: Are there critical CDP socket/Playwright crash logs?
        elif any("target closed" in e.lower() or "crashed" in e.lower() for e in errors):
            anomaly_detected = True
            incident_type = "CHROME_CDP_SOCKET_CRASH"

        if anomaly_detected:
            self._consecutive_fails += 1
            logger.warning("🚨 ANOMALY CAUGHT: %s (Quote Age: %.1fs | Service: %s | Fails: %d)", incident_type, quote_age, svc_state, self._consecutive_fails)

            incident = IncidentReport(
                incident_type=incident_type,
                severity="HIGH" if self._consecutive_fails >= 2 else "MEDIUM",
                observed_latency_sec=quote_age,
                recent_errors=errors,
                systemd_state=svc_state,
                chrome_proc_count=chrome_count,
                timestamp=now,
            )

            # Consult LLM
            prescription = self.consult_llm_doctor(incident)
            self.last_prescription = asdict(prescription)
            self.health_status = f"HEALING: {prescription.diagnosis}"
            self.export_telemetry()

            # Execute Remediation
            self.actuate_remediation(prescription)

            # Reset fail counter after action
            self._consecutive_fails = 0
            time.sleep(3.0)  # Grace period after restart
        else:
            self._consecutive_fails = 0
            self.health_status = "SURVEILLANCE_ACTIVE 🟢"

        self.export_telemetry()

    def run_forever(self) -> None:
        """Blocking daemon loop."""
        logger.info("🏥 Stratton Autonomous LLM Doctor armed and in active surveillance mode...")
        while True:
            try:
                self.run_surveillance_cycle()
            except Exception as e:
                logger.error("Error in surveillance cycle: %s", e)
            time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stratton Oakmont LLM Auto-Repair Doctor")
    parser.add_argument("--once", action="store_true", help="Run a single surveillance check and exit")
    args = parser.parse_args()

    doctor = LLMDoctor()
    if args.once:
        doctor.run_surveillance_cycle()
        print("Doctor single check completed. State:", doctor.health_status)
    else:
        doctor.run_forever()
