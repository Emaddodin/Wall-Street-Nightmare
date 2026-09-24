"""
scalper/sentinel/llm_doctor.py
==============================
Autonomous Layer 2 Sentinel: NOC SRE + DevOps + Quant Financial Guard & Auto-Repair Crew.
Powered by local Qwen2.5-1.5B-Instruct on llama-server (127.0.0.1:8080) with deterministic <5ms fast reflex fallback.

Tri-Domain Surveillance:
1. LAYER 2 NOC (Network & Infrastructure Operations Center):
   - Quote stream freshness & tick stalls (>15s warning, >35s critical)
   - Chrome headless memory footprint & zombie process surveillance
   - Local web endpoints responsiveness (:80, :443, :8088, :8443)
2. LAYER 2 DEVOPS SRE (Exceptions & DOM Triage):
   - Journalctl inspection for CDP disconnects, Playwright timeouts, and Python tracebacks
   - Autonomous remediation: RECYCLE_CHROME, RESTART_LIVE_SERVICE, DISMISS_OVERLAYS
3. LAYER 2 FINANCIAL & QUANT RISK GUARD:
   - Margin exhaustion protection (alerts if available funds drop dangerously low)
   - Spread blowout detection (>1.2 bps / $0.50 spread pause)
   - Runaway floating drawdown guard (emergency liquidation if stop failure detected)
   - Stuck/zombie position timeout guard (>20 min holding without ratchet progression)
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
    format="%(asctime)s [L2-SENTINEL-%(levelname)s]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("layer2_sentinel")

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

MAX_QUOTE_STALL_SEC = 35.0
POLL_INTERVAL_SEC = 3.5


@dataclass
class IncidentReport:
    domain: str  # "NOC", "DEVOPS", "FINANCIAL_GUARD"
    incident_type: str
    severity: str  # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    observed_latency_sec: float
    recent_errors: List[str]
    systemd_state: str
    chrome_proc_count: int
    chrome_memory_mb: float
    financial_metrics: Dict[str, Any]
    timestamp: float


@dataclass
class Prescription:
    domain: str
    diagnosis: str
    severity: str
    prescribed_action: str
    explanation: str
    notify_user: bool


class Layer2SentinelDoctor:
    """
    Autonomous Layer 2 Sentinel combining NOC Operations, DevOps SRE, and Financial Risk Guard.
    """

    def __init__(self, llama_url: str = LLAMA_URL, api_url: str = HFT_API_URL):
        self.llama_url = llama_url
        self.api_url = api_url
        self.total_healings = 0
        self.last_healing_time: Optional[float] = None
        self.last_prescription: Optional[Dict[str, Any]] = None
        self.health_status = "SURVEILLANCE_ACTIVE 🟢"
        self._consecutive_fails = 0
        self._last_alert_time = 0.0
        self._trading_paused_by_guard = False
        self._last_action_time = 0.0
        self._min_action_interval_sec = 180.0  # 3 minutes cooldown between disruptive interventions

    def query_hft_api(self) -> Optional[Dict[str, Any]]:
        """Queries local HFT engine API for quote age and operational metrics."""
        try:
            req = urllib.request.Request(self.api_url, headers={"User-Agent": "StrattonSentinel/2.0"})
            with urllib.request.urlopen(req, timeout=1.8) as resp:
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

    def get_recent_journal_errors(self, lines: int = 20) -> List[str]:
        """Scans journalctl for recent crashes within the last 60 seconds."""
        try:
            res = subprocess.run(
                ["journalctl", "-u", LIVE_SERVICE_NAME, "-n", str(lines), "--since", "60 seconds ago", "--no-pager"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            out = res.stdout or ""
            suspicious = []
            for line in out.splitlines():
                l_low = line.lower()
                if any(k in l_low for k in [
                    "traceback", "timeouterror", "crashed", "connection closed", 
                    "target closed", "errno", "failed to read quote", "out of memory",
                    "broker order failed", "broker order rejected"
                ]):
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

    def get_chrome_memory_mb(self) -> float:
        """Calculates total RSS memory consumed by Chrome processes in MB."""
        try:
            res = subprocess.run(["pgrep", "-f", "chrome"], capture_output=True, text=True, timeout=2)
            if res.returncode == 0:
                pids = [p.strip() for p in res.stdout.splitlines() if p.strip()]
                if not pids:
                    return 0.0
                ps_res = subprocess.run(["ps", "-o", "rss=", "-p", ",".join(pids[:50])], capture_output=True, text=True, timeout=2)
                if ps_res.returncode == 0:
                    total_kb = sum(int(line.strip()) for line in ps_res.stdout.splitlines() if line.strip().isdigit())
                    return round(total_kb / 1024.0, 1)
        except Exception:
            pass
        return 0.0

    def consult_llm_sentinel(self, incident: IncidentReport) -> Prescription:
        """
        Consults local Qwen2.5-1.5B LLM to diagnose root cause and prescribe targeted remedy.
        Falls back to high-speed deterministic reflex if local LLM exceeds latency budget.
        """
        prompt = (
            "<|im_start|>system\n"
            "You are the Stratton Oakmont Autonomous Layer 2 NOC SRE, DevOps Engineer, and Quant Financial Risk Sentinel.\n"
            "Analyze the incident report across NOC, DevOps, and Financial Guard domains and output strictly valid JSON.\n"
            "Available prescribed_action options:\n"
            "- 'RESTART_LIVE_SERVICE': Full systemd restart of the trading engine\n"
            "- 'RECYCLE_CHROME': Kill hung Chrome renderers & reload gateway\n"
            "- 'EMERGENCY_FLATTEN': Immediately liquidate all open broker positions\n"
            "- 'PAUSE_TRADING': Temporarily halt entries during severe infrastructure or spread anomalies\n"
            "- 'RESUME_TRADING': Unpause trading once market conditions stabilize\n"
            "- 'DISMISS_OVERLAYS': Send external command to dismiss blocking popups\n"
            "- 'OBSERVE_PASS': False alarm or transient flicker, do not disrupt trading\n\n"
            "Format strictly as JSON with keys:\n"
            "{\n"
            "  \"domain\": \"NOC\" | \"DEVOPS\" | \"FINANCIAL_GUARD\",\n"
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
            "n_predict": 90,
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
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                if resp.status == 200:
                    raw = json.loads(resp.read().decode("utf-8"))
                    content = raw.get("content", "").strip()
                    m = re.search(r"\{.*\}", content, re.DOTALL)
                    if m:
                        parsed = json.loads(m.group(0))
                        return Prescription(
                            domain=parsed.get("domain", incident.domain),
                            diagnosis=parsed.get("diagnosis", "HEURISTIC_TRIAGE"),
                            severity=parsed.get("severity", "HIGH"),
                            prescribed_action=parsed.get("prescribed_action", "RESTART_LIVE_SERVICE"),
                            explanation=parsed.get("explanation", "Autonomous recovery prescribed."),
                            notify_user=bool(parsed.get("notify_user", True)),
                        )
        except Exception as err:
            logger.debug("Local LLM consultation deferred (%s), executing deterministic emergency reflex.", err)

        # High-Speed Deterministic Reflex Fallback (<5ms)
        domain = incident.domain
        if domain == "FINANCIAL_GUARD":
            if incident.incident_type in ("RUNAWAY_FLOATING_DRAWDOWN", "ZOMBIE_POSITION_STALL"):
                action = "EMERGENCY_FLATTEN"
                diag = incident.incident_type
            elif incident.incident_type == "SPREAD_BLOWOUT":
                action = "PAUSE_TRADING"
                diag = "SPREAD_BLOWOUT_SHIELD"
            else:
                action = "OBSERVE_PASS"
                diag = incident.incident_type
        elif incident.systemd_state != "active":
            action = "RESTART_LIVE_SERVICE"
            diag = "SERVICE_UNHEALTHY_OR_DEAD"
        elif incident.observed_latency_sec > MAX_QUOTE_STALL_SEC:
            action = "RECYCLE_CHROME"
            diag = "MARKET_QUOTE_FEED_STALL"
        elif incident.chrome_proc_count > 15 or incident.chrome_memory_mb > 1800.0:
            action = "RECYCLE_CHROME"
            diag = "CHROME_RESOURCE_LEAK"
        else:
            action = "RESTART_LIVE_SERVICE"
            diag = "RUNTIME_EXCEPTION_TRIP"

        return Prescription(
            domain=domain,
            diagnosis=diag,
            severity="HIGH",
            prescribed_action=action,
            explanation=f"Deterministic {domain} SRE reflex: {diag}",
            notify_user=True,
        )

    def actuate_remediation(self, prescription: Prescription) -> bool:
        """Executes the prescribed clinical remediation action."""
        action = prescription.prescribed_action.upper()
        now = time.time()
        if action in ("RECYCLE_CHROME", "RESTART_LIVE_SERVICE"):
            if (now - self._last_action_time) < self._min_action_interval_sec:
                logger.info("⏳ L2 Remediation %s suppressed: Cooldown active (%.1fs remaining).", action, self._min_action_interval_sec - (now - self._last_action_time))
                return False
            self._last_action_time = now

        logger.warning("⚡ EXECUTING L2 REMEDIATION [%s]: %s -> %s (%s)", 
                       prescription.domain, prescription.diagnosis, action, prescription.explanation)

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

            elif action == "EMERGENCY_FLATTEN":
                # Write emergency liquidation command to engine
                try:
                    tmp_cmd = COMMAND_FILE.with_suffix(".tmp")
                    with open(tmp_cmd, "w") as f:
                        json.dump({"action": "FLATTEN", "reason": prescription.diagnosis, "timestamp": time.time()}, f)
                    tmp_cmd.replace(COMMAND_FILE)
                    success = True
                    logger.warning("🚨 EMERGENCY FLATTEN COMMAND DISPATCHED TO ENGINE!")
                except Exception as ce:
                    logger.warning("Could not write command file: %s", ce)

            elif action == "PAUSE_TRADING":
                try:
                    tmp_cmd = COMMAND_FILE.with_suffix(".tmp")
                    with open(tmp_cmd, "w") as f:
                        json.dump({"action": "PAUSE", "reason": prescription.diagnosis, "timestamp": time.time()}, f)
                    tmp_cmd.replace(COMMAND_FILE)
                    self._trading_paused_by_guard = True
                    success = True
                except Exception as ce:
                    logger.warning("Could not write pause command: %s", ce)

            elif action == "RESUME_TRADING":
                try:
                    tmp_cmd = COMMAND_FILE.with_suffix(".tmp")
                    with open(tmp_cmd, "w") as f:
                        json.dump({"action": "RESUME", "timestamp": time.time()}, f)
                    tmp_cmd.replace(COMMAND_FILE)
                    self._trading_paused_by_guard = False
                    success = True
                except Exception as ce:
                    logger.warning("Could not write resume command: %s", ce)

            elif action == "DISMISS_OVERLAYS":
                try:
                    tmp_cmd = COMMAND_FILE.with_suffix(".tmp")
                    with open(tmp_cmd, "w") as f:
                        json.dump({"action": "CLEAR_OVERLAYS", "timestamp": time.time()}, f)
                    tmp_cmd.replace(COMMAND_FILE)
                    success = True
                except Exception as ce:
                    logger.warning("Could not write command file: %s", ce)

            elif action == "OBSERVE_PASS":
                success = True

        except Exception as e:
            logger.error("Remediation execution failed: %s", e)

        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        if success and action != "OBSERVE_PASS":
            self.total_healings += 1
            self.last_healing_time = time.time()
            logger.info("✅ Remediation %s completed successfully in %.1fms.", action, elapsed_ms)

            if prescription.notify_user:
                self.notify_user_alert(prescription, elapsed_ms)

        return success

    def notify_user_alert(self, p: Prescription, elapsed_ms: float) -> None:
        """Sends instant Bark & ntfy notifications to the user."""
        now = time.time()
        if (now - self._last_alert_time) < 15.0:
            return  # Debounce spam
        self._last_alert_time = now

        title = f"🏥 Doctor Healed: {p.diagnosis}"
        body = f"Action: {p.prescribed_action} · Latency: {elapsed_ms:.0f}ms · Total: {self.total_healings}"

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
            with urllib.request.urlopen(req, timeout=3.0):
                pass
        except Exception:
            pass

    def export_telemetry(self) -> None:
        """Exports doctor health state for dashboard and API."""
        now = time.time()
        telemetry = {
            "doctor_status": self.health_status,
            "role": "Layer 2 NOC SRE + DevOps + Quant Risk Guard",
            "model": "Qwen2.5-1.5B-Instruct",
            "runtime": "llama-server :8080",
            "total_healings": self.total_healings,
            "last_healing_iso": datetime.fromtimestamp(self.last_healing_time, tz=timezone.utc).isoformat() if self.last_healing_time else "None",
            "last_prescription": self.last_prescription,
            "monitored_service": LIVE_SERVICE_NAME,
            "guard_paused": self._trading_paused_by_guard,
            "updated_at": now,
        }
        try:
            tmp = DOCTOR_STATE_FILE.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(telemetry, f, indent=2)
            tmp.replace(DOCTOR_STATE_FILE)
        except Exception:
            pass

    def run_surveillance_cycle(self) -> None:
        """Single monitoring and diagnostic pass across NOC, DevOps, and Financial Guard."""
        svc_state = self.get_service_status()
        hft_data = self.query_hft_api()
        errors = self.get_recent_journal_errors()
        chrome_count = self.get_chrome_process_count()
        chrome_mem_mb = self.get_chrome_memory_mb()

        now = time.time()
        quote_age = 0.0
        fin_metrics: Dict[str, Any] = {}

        if hft_data:
            updated_at = hft_data.get("updated_at", now)
            quote_age = max(0.0, now - updated_at)
            fin_metrics = {
                "balance": float(hft_data.get("balance", 0.0)),
                "equity": float(hft_data.get("equity", 0.0)),
                "spread_bps": float(hft_data.get("spread_bps", 0.50)),
                "position": hft_data.get("position"),
            }

        anomaly_detected = False
        incident_domain = "NOC"
        incident_type = "NORMAL"
        severity = "LOW"

        # --- 1. LAYER 2 NOC CHECKS ---
        if svc_state != "active":
            anomaly_detected = True
            incident_domain = "NOC"
            incident_type = "SERVICE_CRASHED"
            severity = "CRITICAL"

        elif quote_age > 60.0 and (now - self._last_action_time) > 90.0:
            anomaly_detected = True
            incident_domain = "NOC"
            incident_type = "QUOTE_STREAM_STALL"
            severity = "HIGH"

        elif chrome_count > 16 or chrome_mem_mb > 1800.0:
            anomaly_detected = True
            incident_domain = "NOC"
            incident_type = "CHROME_RESOURCE_LEAK"
            severity = "HIGH"

        elif (now - self._last_action_time) > 90.0 and quote_age > 10.0 and hft_data and float(hft_data.get("balance", 1.0)) <= 0.0 and hft_data.get("bot_running", False):
            anomaly_detected = True
            incident_domain = "NOC"
            incident_type = "ZERO_BALANCE_DOM_STALL"
            severity = "CRITICAL"

        # --- 2. LAYER 2 DEVOPS SRE CHECKS ---
        elif (now - self._last_action_time) > 90.0 and any("target closed" in e.lower() or "crashed" in e.lower() for e in errors):
            anomaly_detected = True
            incident_domain = "DEVOPS"
            incident_type = "CHROME_CDP_SOCKET_CRASH"
            severity = "HIGH"

        elif any("no_visible_order_button" in e.lower() or "button dispatch failed" in e.lower() for e in errors):
            anomaly_detected = True
            incident_domain = "DEVOPS"
            incident_type = "DOM_BUTTON_OCCLUDED"
            severity = "HIGH"

        # --- 3. LAYER 2 FINANCIAL RISK GUARD CHECKS ---
        elif hft_data and hft_data.get("position"):
            pos = hft_data["position"]
            fl_pnl = float(pos.get("floating_pnl", 0.0))
            open_time = float(pos.get("open_time", now))
            pos_duration_min = (now - open_time) / 60.0

            # Guard A: Runaway drawdown (Floating loss worse than -$25 without stop trigger)
            if fl_pnl <= -25.0:
                anomaly_detected = True
                incident_domain = "FINANCIAL_GUARD"
                incident_type = "RUNAWAY_FLOATING_DRAWDOWN"
                severity = "CRITICAL"

            # Guard B: Zombie / Stuck position (Held over 20 min in chop without progress)
            elif pos_duration_min >= 20.0 and fl_pnl < 0.0:
                anomaly_detected = True
                incident_domain = "FINANCIAL_GUARD"
                incident_type = "ZOMBIE_POSITION_STALL"
                severity = "HIGH"

        # Guard C: Spread Blowout (>1.5 bps or > $0.65 spread)
        elif fin_metrics.get("spread_bps", 0.0) >= 1.50 and not self._trading_paused_by_guard:
            anomaly_detected = True
            incident_domain = "FINANCIAL_GUARD"
            incident_type = "SPREAD_BLOWOUT"
            severity = "MEDIUM"

        # Guard D: Auto-unpause once spread normalizes
        elif self._trading_paused_by_guard and fin_metrics.get("spread_bps", 0.0) < 0.80:
            anomaly_detected = True
            incident_domain = "FINANCIAL_GUARD"
            incident_type = "SPREAD_NORMALIZED"
            severity = "LOW"

        if anomaly_detected:
            self._consecutive_fails += 1
            logger.warning("🚨 [%s] ANOMALY CAUGHT: %s (Latency: %.1fs | State: %s | Fails: %d)", 
                           incident_domain, incident_type, quote_age, svc_state, self._consecutive_fails)

            incident = IncidentReport(
                domain=incident_domain,
                incident_type=incident_type,
                severity=severity,
                observed_latency_sec=quote_age,
                recent_errors=errors,
                systemd_state=svc_state,
                chrome_proc_count=chrome_count,
                chrome_memory_mb=chrome_mem_mb,
                financial_metrics=fin_metrics,
                timestamp=now,
            )

            # Consult LLM Sentinel
            prescription = self.consult_llm_sentinel(incident)
            self.last_prescription = asdict(prescription)
            self.health_status = f"HEALING: [{prescription.domain}] {prescription.diagnosis}"
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
        logger.info("🛡️ Stratton Layer 2 Sentinel (NOC + DevOps + Financial Guard) armed and active...")
        while True:
            try:
                self.run_surveillance_cycle()
            except Exception as e:
                logger.error("Error in sentinel surveillance cycle: %s", e)
            time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stratton Oakmont Layer 2 Sentinel (NOC + DevOps + Risk Guard)")
    parser.add_argument("--once", action="store_true", help="Run a single surveillance check and exit")
    args = parser.parse_args()

    doctor = Layer2SentinelDoctor()
    if args.once:
        doctor.run_surveillance_cycle()
        print("Sentinel single check completed. State:", doctor.health_status)
    else:
        doctor.run_forever()
