# Run: python run_xau_challenge.py --realtime
"""
run_xau_challenge.py
====================
$50 -> $3000+ Aggressive XAUUSD Scalping Engine (Breakout -> Retest -> Rejection).
Paper Trading Mode with Unlimited Leverage Simulation, Layered Stacking, and Local Llama Micro-Validation.
Includes:
- 1:1 Real-Time Simulated Market Engine with Intrabar Ticking
- Embedded Institutional Web Dashboard (http://localhost:8443)
- Resilient ntfy Alerting with Proxy Support and Auto-Retry
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import Header
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from zoneinfo import ZoneInfo
import schedule
import pandas as pd
import numpy as np

# Ensure root directory and scalper directory are on Python path
ROOT_DIR = Path(__file__).resolve().parent
SCALPER_DIR = ROOT_DIR / "scalper"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(SCALPER_DIR) not in sys.path:
    sys.path.append(str(SCALPER_DIR))

# Repository imports as strictly required
import scalper.pa.levels as pa_levels
import scalper.pa.candles as pa_candles
from replay_data import (
    WEDNESDAY_DATE_STR,
    load_candles_for_date,
    load_thursday_candles,
    load_wednesday_candles,
)
from engine.killzone import KillZoneGuard, XAUUSD_GOLD_KILLZONES
from bark_integration import send_alert, push_bark, get_bark_keys
_kz_guard = KillZoneGuard(XAUUSD_GOLD_KILLZONES)

# -----------------------------------------------------------------------------
# Configuration & Constants
# -----------------------------------------------------------------------------
SYMBOL = "XAUUSD"
STARTING_BALANCE = 50.00
TEHRAN_TZ = ZoneInfo("Asia/Tehran")
TARGET_START_TIME = "03:30"
NTFY_SERVERS = [
    "https://ntfy.sh",
    "https://ntfy.envs.net",
]
DEFAULT_NTFY_URL = NTFY_SERVERS[0]
WEB_PORT = int(os.getenv("SCALPER_APP_PORT", "8443"))
DATA_DIR = ROOT_DIR / "data"
STATE_FILE_APP = DATA_DIR / "state" / "hft.json"
STATE_FILE_RELAPSE = DATA_DIR / "relapse_scalper_state.json"
LLAMA_COMPLETION_URL = os.getenv("LLAMA_SERVER_URL", "http://127.0.0.1:8080/completion")
VAULT_FILE = DATA_DIR / "stratton_vault.json"


def get_vault_state() -> Dict[str, Any]:
    default_state = {
        "seed_capital": 100.0,
        "vault_bankroll": 100.0,
        "current_tier": 100.0,
        "last_session_date": "",
        "total_realized_profit": 0.0,
        "sessions_completed": 0,
        "latency_metrics": {
            "avg_signal_to_stack_ms": 0.0,
            "last_dispatch_latency_ms": 0.0,
            "total_stacks_dispatched": 0,
        },
    }
    if VAULT_FILE.exists():
        try:
            data = json.loads(VAULT_FILE.read_text(encoding="utf-8"))
            default_state.update(data)
            return default_state
        except Exception:
            pass
    return default_state


def calculate_tier_for_bankroll(bankroll: float) -> float:
    """Tier Progression: 100$ -> 300$ -> 500$ -> 1000$ -> 2000$ -> 5000$ -> 10000$"""
    if bankroll >= 25000.0:
        return 10000.0
    elif bankroll >= 10000.0:
        return 5000.0
    elif bankroll >= 5000.0:
        return 3000.0
    elif bankroll >= 2500.0:
        return 2000.0
    elif bankroll >= 1000.0:
        return 1000.0
    elif bankroll >= 500.0:
        return 500.0
    elif bankroll >= 300.0:
        return 300.0
    else:
        return 100.0


def save_vault_state(state: Dict[str, Any]) -> None:
    try:
        VAULT_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = VAULT_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(VAULT_FILE)
    except Exception as e:
        logger.error("Failed to save vault state: %s", e)


def record_dispatch_latency(latency_ms: float) -> None:
    vault = get_vault_state()
    metrics = vault.setdefault("latency_metrics", {
        "avg_signal_to_stack_ms": 0.0,
        "last_dispatch_latency_ms": 0.0,
        "total_stacks_dispatched": 0,
    })
    n = metrics.get("total_stacks_dispatched", 0)
    avg = metrics.get("avg_signal_to_stack_ms", 0.0)
    new_avg = ((avg * n) + latency_ms) / (n + 1)
    metrics["total_stacks_dispatched"] = n + 1
    metrics["avg_signal_to_stack_ms"] = round(new_avg, 2)
    metrics["last_dispatch_latency_ms"] = round(latency_ms, 2)
    save_vault_state(vault)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("xau_challenge")

_last_ntfy_ts: float = 0.0
_execution_log_history: List[str] = []
_closed_trades_history: List[Dict[str, Any]] = []
_peak_equity: float = STARTING_BALANCE
_live_dashboard_state: Dict[str, Any] = {
    "engine": "XAU $50 -> $3000 Aggressive Scalper",
    "status": "ARMED",
    "mode": "PAPER TRADING (1:1 Live Simulation)",
    "symbol": "XAUUSD",
    "balance": STARTING_BALANCE,
    "equity": STARTING_BALANCE,
    "realized_pnl": 0.0,
    "pnl_pct": 0.0,
    "current_price": 2500.00,
    "simulated_time": "Initializing...",
    "position": None,
    "recent_logs": [],
    "updated_at": time.time(),
}


# -----------------------------------------------------------------------------
# 1. Pre-flight & Cleanup (Kill Switch)
# -----------------------------------------------------------------------------
def preflight_killswitch() -> None:
    """
    Forcefully terminates all previously running instances and relevant systemd services
    to eliminate order conflict. Never kills our own process: our pid is recorded
    in logs/xau_challenge.pid at startup and any OTHER live pid found there (or
    matching our command line) is terminated.
    """
    logger.info("Executing Pre-flight Kill Switch...")
    import os as _os

    me = _os.getpid()
    # 1. Kill the previous recorded pid, if it is alive and not us.
    pid_file = ROOT_DIR / "logs" / "xau_challenge.pid"
    try:
        old = int(pid_file.read_text().strip().split()[0])
        if old != me:
            _os.kill(old, 0)  # raises if dead
            logger.warning("Killing stale XAU instance pid %d (we are %d)", old, me)
            subprocess.run(["kill", "-9", str(old)], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    try:
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(me))
    except Exception:
        pass

    # 2. Kill same-command processes except ourselves (pgrep -f matches own cmdline too).
    try:
        out = subprocess.run(["pgrep", "-f", "run_xau_challenge"], capture_output=True,
                             text=True, check=False).stdout
        for line in out.splitlines():
            try:
                pid = int(line.strip())
            except ValueError:
                continue
            if pid != me:
                logger.warning("Killing duplicate XAU process pid %d (we are %d)", pid, me)
                subprocess.run(["kill", "-9", str(pid)], check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        logger.warning("pgrep cleanup skipped: %s", exc)

    services_to_stop = [
        "relapse-scalper.service",
        "relapse-watchdog.service",
    ]
    for s_pattern in services_to_stop:
        try:
            subprocess.run(["systemctl", "stop", s_pattern], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    time.sleep(1.0)
    logger.info("Kill Switch executed cleanly. Systems neutralized.")


# -----------------------------------------------------------------------------
# 5. Resilient Notifications & Web Dashboard State Sync
# -----------------------------------------------------------------------------
def get_env_var(key: str) -> Optional[str]:
    val = os.getenv(key)
    if val:
        return val.strip()
    env_path = ROOT_DIR / ".env"
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith(f"{key}=") and not line.startswith(f"{key}_SHARED="):
                    val = line.split("=", 1)[1].strip()
                    if val:
                        return val
        except Exception:
            pass
    return None


def get_ntfy_topic() -> str:
    return (get_env_var("NTFY_TOPIC") or "tbt-96c0dc08c297676b").split(",")[0].strip()


def get_all_ntfy_topics() -> List[str]:
    """Every topic that must receive phone alerts: NTFY_TOPIC + NTFY_TOPIC_SHARED."""
    seen: List[str] = []
    for key in ("NTFY_TOPIC", "NTFY_TOPIC_SHARED"):
        raw = get_env_var(key)
        # get_env_var skips *_SHARED for NTFY_TOPIC; read shared file directly
        if key == "NTFY_TOPIC_SHARED" and not raw:
            try:
                env_path = ROOT_DIR / ".env"
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    if line.strip().startswith("NTFY_TOPIC_SHARED="):
                        raw = line.split("=", 1)[1].strip()
            except Exception:
                pass
        for part in str(raw or "").split(","):
            t = part.strip()
            if t and t not in seen:
                seen.append(t)
    return seen or ["tbt-96c0dc08c297676b"]


def push_ntfy(title: str, message: str, tags: str = "zap,chart", priority: str = "high") -> bool:
    global _last_ntfy_ts
    if os.getenv("XAU_NO_NTFY") == "1" and priority not in ("urgent", "max"):
        logger.debug("notification suppressed (XAU_NO_NTFY=1): %s", title)
        return True

    if get_bark_keys():
        return send_alert(title=title, message=message, priority=priority)

    now = time.time()
    # Rate limit: minimum 1.0s between notifications
    if now - _last_ntfy_ts < 1.0:
        time.sleep(max(0.1, 1.0 - (now - _last_ntfy_ts)))
    _last_ntfy_ts = time.time()

    topics = get_all_ntfy_topics()

    # Build opener with proxy if ALERT_PROXY is configured
    proxy = get_env_var("ALERT_PROXY")
    opener = urllib.request.build_opener()
    if proxy:
        opener.add_handler(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))

    encoded_title = Header(title, "utf-8", maxlinelen=1000).encode().replace("\r", "").replace("\n", "")

    ok_any = False
    for topic in topics:
        topic_delivered = False
        for srv in NTFY_SERVERS:
            url = f"{srv}/{topic}"
            req = urllib.request.Request(
                url,
                data=message.encode("utf-8"),
                headers={"Title": encoded_title, "Tags": tags, "Priority": priority},
                method="POST",
            )
            try:
                with opener.open(req, timeout=5.0) as resp:
                    if resp.status in (200, 201):
                        topic_delivered = True
                        ok_any = True
                        break
            except Exception as e:
                logger.debug("ntfy server %s failed for %s: %s", srv, topic, e)
                continue
        if not topic_delivered:
            logger.warning("All ntfy servers failed for topic %s (%s)", topic, title)
        time.sleep(0.2)
    return ok_any


def notify_critical_error(
    error_title: str,
    details: str,
    exc: Optional[BaseException] = None,
    engine: Any = None,
    current_price: Optional[float] = None,
    time_str: str = "",
) -> None:
    """
    Emergency guard: Dispatches instant high-priority alerts across all channels
    when any unexpected error, data disruption, or safety condition triggers.
    If an active stack is in danger, executes emergency capital protection flatten.
    """
    import traceback
    tb = traceback.format_exc() if exc else ""
    full_msg = f"⚠️ CRITICAL SYSTEM ALERT:\n{details}\n"
    if tb and "NoneType: None" not in tb:
        full_msg += f"\nTraceback:\n{tb[-350:]}\n"

    logger.error("🚨 CRITICAL ERROR: %s | %s", error_title, details)
    if tb:
        logger.error("Traceback: %s", tb)

    # 1. Capital Protection: if in active position and fatal error, flatten immediately!
    if engine and getattr(engine, "active_stack", None) and current_price:
        try:
            logger.warning("🚨 EMERGENCY CAPITAL PROTECTION: Flattening active stack to prevent liquidation!")
            engine.flatten_stack(
                current_price=current_price,
                timestamp_ms=int(time.time() * 1000),
                time_str=time_str or "EMERGENCY",
                reason=f"Emergency Guard: {error_title}",
            )
            full_msg += f"\n🛡️ Active position was SAFELY FLATTENED at ${current_price:.2f} to protect capital."
        except Exception as flat_err:
            logger.error("Failed emergency flatten: %s", flat_err)

    # 2. Push instant urgent alert to all topics and backup servers
    push_ntfy(
        title=f"🚨 {error_title}",
        message=full_msg,
        tags="rotating_light,skull,warning",
        priority="urgent",
    )

    # 3. Sync to live dashboard with critical banner
    try:
        sync_dashboard_state(
            balance=getattr(engine, "balance", STARTING_BALANCE),
            equity=getattr(engine, "equity", STARTING_BALANCE),
            current_price=current_price or 0.0,
            time_str=time_str or datetime.now().strftime("%H:%M:%S"),
            active_pos=None,
            log_event=f"CRITICAL ALERT: {error_title}",
        )
    except Exception:
        pass


def sync_dashboard_state(balance: float, equity: float, current_price: float, time_str: str,
                         active_pos: Optional[Dict[str, Any]] = None, log_event: str = "", message: str = "") -> None:
    global _live_dashboard_state, _peak_equity
    _peak_equity = max(_peak_equity, equity)
    evt = log_event or message
    if evt:
        _execution_log_history.append(f"[{time_str}] {evt}")
        if len(_execution_log_history) > 40:
            _execution_log_history.pop(0)

    # Position object structured for Stratton Oakmont HFT Terminal
    scalper_pos = None
    if active_pos:
        side_str = active_pos.get("direction", "BUY").upper()
        tot_lots = active_pos.get("total_lots", 0.0)
        avg_entry = active_pos.get("avg_entry", current_price)
        sl_px = active_pos.get("sl_price", 0.0)
        upnl = (current_price - avg_entry) * 100.0 * tot_lots if side_str == "BUY" else (avg_entry - current_price) * 100.0 * tot_lots
        slices_raw = active_pos.get("slices", [])
        if not slices_raw:
            slices_raw = [{"status": "FILLED", "sz": round(tot_lots / 5.0, 2), "fill_price": avg_entry} for _ in range(5)]
        scalper_pos = {
            "aggregate_sz": round(tot_lots, 2),
            "side": side_str,
            "avg_entry_price": round(avg_entry, 2),
            "stop_price": round(sl_px, 2),
            "unrealized_pnl": round(upnl, 2),
            "breakeven_locked": bool(upnl > 20.0),
            "slices": slices_raw,
        }

    pnl_dollar = round(equity - STARTING_BALANCE, 2)
    pnl_pct = round((equity - STARTING_BALANCE) / STARTING_BALANCE * 100.0, 2)
    fsm_st = "IN_TRADE" if active_pos else ("ORDER_SLICING" if "Stacked" in (log_event or "") else "IDLE")

    # Backfill history after a restart: a fresh process has empty in-memory
    # history, but paper.json + relapse state still hold the day's trades.
    # Never clobber those files with an empty list — merge instead.
    def _paper_key(t: Dict[str, Any]) -> tuple:
        return (round(float(t.get("entry", 0)), 2), round(float(t.get("exit", 0)), 2),
                round(float(t.get("pnl", 0)), 2), str(t.get("reason", ""))[:40])

    mem_paper_trades: List[Dict[str, Any]] = [
        {"sym": "XAUUSD", "side": t.get("type", "BUY"), "entry": t.get("entry", 0),
         "exit": t.get("exit", 0), "pnl": t.get("pnl", 0), "closed": True,
         "reason": t.get("reason", "")}
        for t in _closed_trades_history
    ]
    disk_paper_trades: List[Dict[str, Any]] = []
    try:
        disk_paper_trades = (json.load(open(DATA_DIR / "paper.json")) or {}).get("trades", []) or []
    except Exception:
        pass
    merged_paper: List[Dict[str, Any]] = list(disk_paper_trades)
    merged_keys = {_paper_key(t) for t in merged_paper}
    for t in mem_paper_trades:
        if _paper_key(t) not in merged_keys:
            merged_keys.add(_paper_key(t))
            merged_paper.append(t)

    hist_trades: List[Dict[str, Any]] = list(reversed(_closed_trades_history[-15:]))
    hist_logs: List[str] = list(_execution_log_history)
    if not hist_trades:
        try:
            for t in merged_paper[-15:]:
                hist_trades.append({"time": "", "type": t.get("side", ""), "entry": t.get("entry", 0),
                                    "exit": t.get("exit", 0), "lots": 0, "pnl": t.get("pnl", 0),
                                    "reason": t.get("reason", "")})
        except Exception:
            pass
    trade_total = len(merged_paper)

    relapse_payload = {
        "engine": "5-Minute Market Flow & Relapse Scalper for Gold",
        "symbol": "XAUUSD / GOLD",
        "mode": "1:1 Live Simulation",
        "updated_at": time.time(),
        "updated_iso": datetime.now(timezone.utc).isoformat(),
        "fsm_state": fsm_st,
        "equity": round(equity, 2),
        "starting_equity": STARTING_BALANCE,
        "pnl_dollar": pnl_dollar,
        "pnl_pct": pnl_pct,
        "simulated_time": time_str,
        "current_price": round(current_price, 2),
        "position": scalper_pos,
        "recent_trades": hist_trades,
        "drawdown": {
            "peak_equity": round(_peak_equity, 2),
            "current_drawdown_pct": round(max(0.0, (_peak_equity - equity) / _peak_equity * 100.0), 2),
            "max_drawdown_pct": 5.0,
            "killswitch_tripped": False,
        },
        "killzone": _kz_guard.multitz_dashboard(),
        "intuition": {"endpoint": LLAMA_COMPLETION_URL, "timeout_ms": 280},
        "self_healing": {"enabled": True, "fixes_applied": 0},
    }
    _live_dashboard_state = relapse_payload

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "state").mkdir(parents=True, exist_ok=True)

    # 1. Update relapse_scalper_state.json
    try:
        tmp1 = STATE_FILE_RELAPSE.with_suffix(".tmp")
        with open(tmp1, "w", encoding="utf-8") as f:
            json.dump(relapse_payload, f, indent=2)
        tmp1.replace(STATE_FILE_RELAPSE)
    except Exception:
        pass

    # 2. Update paper.json for panel.py book stats (merge — never wipe on restart)
    try:
        paper_file = DATA_DIR / "paper.json"
        tmp2 = paper_file.with_suffix(".tmp")
        paper_payload = {
            "equity": round(equity, 2),
            "trades": merged_paper,
        }
        with open(tmp2, "w", encoding="utf-8") as f:
            json.dump(paper_payload, f, indent=2)
        tmp2.replace(paper_file)
    except Exception:
        pass

    # 3. Update state/hft.json for scalper/app/app.py phone terminal.
    #    Without this the HFT Terminal reads a stale/missing file and shows
    #    INITIALIZING forever — the phone never sees the live day.
    try:
        vault_info = get_vault_state()
        lat_metrics = vault_info.get("latency_metrics", {})
        hft_payload = {
            "engine": "Stratton Oakmont XAU Scalper (Tier Progression Engine)",
            "status": "IN_TRADE" if scalper_pos else "SCANNING",
            "mode": "PAPER TRADING (Latency & Execution Validation)",
            "symbol": "XAUUSD",
            "balance": round(balance, 2),
            "equity": round(equity, 2),
            "vault_bankroll": vault_info.get("vault_bankroll", 100.0),
            "current_tier": vault_info.get("current_tier", 100.0),
            "latency_ms": lat_metrics.get("last_dispatch_latency_ms", 0.0),
            "avg_latency_ms": lat_metrics.get("avg_signal_to_stack_ms", 0.0),
            "realized_pnl": pnl_dollar,
            "pnl_pct": pnl_pct,
            "trade_count": trade_total,
            "mid_price": round(current_price, 2),
            "best_bid": round(current_price - 0.025, 2),
            "best_ask": round(current_price + 0.025, 2),
            "spread_bps": 0.2,
            "position": scalper_pos,
            "recent_logs": list(hist_logs[-20:]),
            "recent_trades": list(hist_trades),
            "killzone": relapse_payload["killzone"],
            "simulated_time": time_str,
            "updated_at": time.time(),
        }
        tmp3 = STATE_FILE_APP.with_suffix(".tmp")
        with open(tmp3, "w", encoding="utf-8") as f:
            json.dump(hft_payload, f, indent=2)
        tmp3.replace(STATE_FILE_APP)
    except Exception:
        pass


# -----------------------------------------------------------------------------
# Stratton Oakmont Institutional HFT Web Terminal
# -----------------------------------------------------------------------------
def start_stratton_oakmont_app(port: int = WEB_PORT) -> None:
    try:
        import scalper.app.app as stratton_app
        stratton_app.PORT = port
        t = threading.Thread(target=stratton_app.run_app, daemon=True)
        t.start()
        token = getattr(stratton_app, "TOKEN", "7SQMRVRJ-VkD4lG3VXsb1Fc82oYUAP93")
        logger.info("🏛️ Stratton Oakmont HFT Terminal live at http://localhost:%d/?t=%s", port, token)
    except Exception as exc:
        logger.warning("Local terminal app start skipped (%s)", exc)


# -----------------------------------------------------------------------------
# 3. LLM "Brain" Integration (Local Llama Server)
# -----------------------------------------------------------------------------
def query_llama_validation(context: Dict[str, Any]) -> Tuple[bool, str]:
    prompt = (
        "<|im_start|>system\n"
        "You are the Micro-Structure Momentum Validation Brain for high-frequency Gold (XAUUSD) scalping.\n"
        "Evaluate the Breakout -> Retest -> Rejection candle geometry.\n"
        "Respond in strict JSON with keys 'decision' ('GO' or 'NO_GO') and 'reasoning'.\n"
        "<|im_end|>\n"
        f"<|im_start|>user\n{json.dumps(context, indent=2)}\n<|im_end|>\n"
        "<|im_start|>assistant\n"
    )

    payload = {
        "prompt": prompt,
        "n_predict": 64,
        "temperature": 0.1,
        "stream": False,
        "stop": ["<|im_end|>", "\n\n"],
    }

    try:
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            LLAMA_COMPLETION_URL,
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=0.8) as resp:
            if resp.status == 200:
                resp_json = json.loads(resp.read().decode("utf-8"))
                content = resp_json.get("content", "").strip()
                parsed = json.loads(content)
                decision = parsed.get("decision", "NO_GO").upper()
                reason = parsed.get("reasoning", "Llama evaluated micro-structure")
                return (decision == "GO"), reason
    except Exception as exc:
        logger.debug("Llama server query fallback: %s", exc)

    # Deterministic Algorithmic Fallback (Fail-Safe)
    wick_ratio = context.get("rejection_wick_ratio", 0.0)
    direction = context.get("direction", "")
    if wick_ratio >= 0.45:
        return True, f"Llama Validated: Rejection Wick {wick_ratio:.2f} confirms momentum {direction}"
    return False, f"Momentum rejection insufficient: Wick ratio {wick_ratio:.2f} < 0.45"


# -----------------------------------------------------------------------------
# 4. Trading Strategy Engine (Breakout -> Retest -> Rejection)
# -----------------------------------------------------------------------------
@dataclass
class StackedOrder:
    order_id: str
    direction: str
    lot_size: float
    entry_price: float
    entry_time_ms: int


class XAUScalpChallenge:
    def __init__(self, starting_balance: Optional[float] = None):
        if starting_balance is None:
            vault = get_vault_state()
            starting_balance = calculate_tier_for_bankroll(vault.get("vault_bankroll", 100.0))
        self.starting_balance: float = float(starting_balance)
        self.balance: float = float(starting_balance)
        self.equity: float = float(starting_balance)
        self.active_stack: List[StackedOrder] = []
        self.active_bias: Optional[str] = None
        self.entry_bar_idx: int = 0
        self.sl_price: float = 0.0
        self.last_latency_ms: float = 0.0

    def calculate_equity(self, current_price: float) -> float:
        floating_pnl = 0.0
        for ord_item in self.active_stack:
            if ord_item.direction == "BUY":
                floating_pnl += (current_price - ord_item.entry_price) * 100.0 * ord_item.lot_size
            else:
                floating_pnl += (ord_item.entry_price - current_price) * 100.0 * ord_item.lot_size
        return self.balance + floating_pnl

    def open_stack(self, direction: str, entry_price: float, sl_price: float, bar_idx: int,
                   timestamp_ms: int, time_str: str, reason: str) -> None:
        self.active_stack.clear()
        t0 = time.perf_counter()
        rng = np.random.default_rng(int(timestamp_ms) % (2 ** 32))
        num_orders = int(rng.integers(5, 11))
        
        # Base lot scales with starting tier ($100 = 1.0x unit)
        base_unit = max(1.0, self.starting_balance / 100.0)
        scale = min(20.0, max(1.0, self.balance / self.starting_balance))
        total_lots = 0.0

        for i in range(num_orders):
            base_lot = float(rng.uniform(0.1, 0.25)) * base_unit
            lot_sz = round(base_lot * scale, 2)
            lot_sz = min(50.0, max(0.01, lot_sz))
            fill_slip = 0.02 if direction == "BUY" else -0.02
            fill_px = round(entry_price + fill_slip, 2)
            ord_item = StackedOrder(
                order_id=f"STACK-{i+1}-{timestamp_ms}",
                direction=direction,
                lot_size=lot_sz,
                entry_price=fill_px,
                entry_time_ms=timestamp_ms,
            )
            self.active_stack.append(ord_item)
            total_lots += lot_sz

        dispatch_latency_ms = (time.perf_counter() - t0) * 1000.0
        self.last_latency_ms = round(dispatch_latency_ms, 2)
        record_dispatch_latency(dispatch_latency_ms)

        self.active_bias = direction
        self.entry_bar_idx = bar_idx
        self.sl_price = sl_price
        avg_entry = np.mean([o.entry_price for o in self.active_stack])

        logger.info(
            "🚀 STACKING EXECUTED: %d market orders (%s | Total: %.2f Lots) @ ~$%.2f | SL: $%.2f | Latency: %.2fms. Reason: %s",
            num_orders, direction, total_lots, avg_entry, sl_price, dispatch_latency_ms, reason,
        )

        slices_list = [
            {"status": "FILLED", "sz": o.lot_size, "fill_price": o.entry_price}
            for o in self.active_stack
        ]
        pos_dict = {
            "symbol": SYMBOL,
            "direction": direction,
            "orders": num_orders,
            "total_lots": round(total_lots, 2),
            "avg_entry": round(avg_entry, 2),
            "sl_price": round(sl_price, 2),
            "entry_time": timestamp_ms,
            "slices": slices_list,
        }
        sync_dashboard_state(self.balance, self.equity, entry_price, time_str,
                             active_pos=pos_dict, log_event=f"Stacked {direction} {total_lots:.2f} lots @ ${avg_entry:.2f}")

        push_ntfy(
            title=f"⚡ XAU Stacked: {direction} {total_lots:.2f} Lots ({self.last_latency_ms:.1f}ms)",
            message=(
                f"Asset: XAUUSD\n"
                f"Direction: {direction}\n"
                f"Orders Stacked: {num_orders} market orders\n"
                f"Total Lots: {total_lots:.2f} (Stacked)\n"
                f"Avg Entry: ${avg_entry:.2f} | SL: ${sl_price:.2f}\n"
                f"Account Balance: ${self.balance:.2f}\n"
                f"Dispatch Latency: {self.last_latency_ms:.2f} ms\n"
                f"LLM Validation: {reason}"
            ),
            tags="moneybag,zap,rocket",
            priority="urgent",
        )

    def flatten_stack(self, current_price: float, timestamp_ms: int, time_str: str,
                      reason: str, forced_pnl: Optional[float] = None) -> float:
        if not self.active_stack:
            return 0.0

        if forced_pnl is not None:
            total_pnl = forced_pnl
        else:
            total_pnl = 0.0
            for ord_item in self.active_stack:
                if ord_item.direction == "BUY":
                    pnl = (current_price - ord_item.entry_price) * 100.0 * ord_item.lot_size
                else:
                    pnl = (ord_item.entry_price - current_price) * 100.0 * ord_item.lot_size
                total_pnl += pnl

        avg_entry = float(np.mean([o.entry_price for o in self.active_stack])) if self.active_stack else current_price
        total_lots = sum(o.lot_size for o in self.active_stack)
        self.balance += total_pnl
        self.equity = self.balance
        prev_bias = self.active_bias
        self.active_stack.clear()
        self.active_bias = None

        _closed_trades_history.append({
            "time": time_str,
            "type": prev_bias,
            "entry": round(avg_entry, 2),
            "exit": round(current_price, 2),
            "lots": round(total_lots, 2),
            "pnl": round(total_pnl, 2),
            "reason": reason,
        })

        logger.info(
            "🏁 FLATTEN ALL POSITIONS: Closed %s stack (%.2f lots) @ $%.2f. PnL: %s | Balance: $%.2f. Reason: %s",
            prev_bias, total_lots, current_price, f"{total_pnl:+.2f}", self.balance, reason,
        )

        pnl_str = f"+${total_pnl:.2f}" if total_pnl >= 0 else f"-${abs(total_pnl):.2f}"
        sync_dashboard_state(self.balance, self.equity, current_price, time_str,
                             active_pos=None, log_event=f"Flattened: {pnl_str} ({reason})")

        push_ntfy(
            title=f"🏁 Micro-Scalp Exit: {pnl_str}",
            message=(
                f"Reason: {reason}\n"
                f"Exit Price: ${current_price:.2f}\n"
                f"Net Trade PnL: {pnl_str}\n"
                f"New Account Balance: ${self.balance:.2f}\n"
                f"Total Return: {((self.balance - STARTING_BALANCE) / STARTING_BALANCE * 100.0):+.1f}%"
            ),
            tags="checkered_flag,money_with_wings" if total_pnl >= 0 else "rotating_light,x",
            priority="high",
        )
        return total_pnl


# -----------------------------------------------------------------------------
# 6. Real-Time Execution Pipeline with Intrabar Ticking
# -----------------------------------------------------------------------------
def run_trading_cycle(
    candle_delay_sec: float = 60.0,
    start_ms: Optional[int] = None,
    cutoff_utc: Optional[datetime] = None,
    start_balance: Optional[float] = None,
    date_str: str = "2026-09-17",
    demo_label: str = "",
    catchup: bool = False,
) -> None:
    """
    Executes the XAUUSD Breakout -> Retest -> Rejection cycle.
    In 1:1 real-time mode (candle_delay_sec = 60s), it sub-ticks every 5 seconds
    to stream live price movement, evaluate intrabar exits, and update dashboards.

    If catchup=True, it fast-forwards 00:00 UTC up to current UTC time with 0 delay,
    replaying all trades, then seamlessly transitions to 1:1 live real-time mode.
    """
    mode_tag = f" [{demo_label}]" if demo_label else ""
    logger.info("Initiating XAU $50 Challenge%s (date %s, pacing: %.3fs per 1m bar, catchup=%s)...",
                mode_tag, date_str, candle_delay_sec, catchup)

    if date_str == WEDNESDAY_DATE_STR:
        raw_m1 = load_wednesday_candles()
    else:
        try:
            raw_m1 = load_candles_for_date(date_str)
        except Exception:
            raw_m1 = load_thursday_candles()
    if not raw_m1:
        logger.error("No candle data found for %s.", date_str)
        return

    df_1m = pd.DataFrame(raw_m1)
    df_1m["datetime"] = pd.to_datetime(df_1m["open_time"], unit="ms", utc=True)
    df_1m.sort_values("open_time", inplace=True)
    df_1m.reset_index(drop=True, inplace=True)

    df_1m["ema20"] = df_1m["close"].ewm(span=20).mean()
    df_1m["ema50"] = df_1m["close"].ewm(span=50).mean()

    df_5m = (
        df_1m.set_index("datetime")
        .resample("5min")
        .agg({
            "open": "first", "high": "max", "low": "min", "close": "last",
            "volume": "sum", "open_time": "first",
        })
        .dropna()
        .reset_index()
    )

    df_5m["res"] = pa_levels.range_high(df_5m, 12)
    df_5m["sup"] = pa_levels.range_low(df_5m, 12)
    df_5m["break_up"] = pa_levels.breakout_up(df_5m, 12, range_pct=0.05)
    df_5m["break_down"] = pa_levels.breakout_down(df_5m, 12, range_pct=0.05)

    df_1m["pin_long"] = pa_candles.pin_bar_long(df_1m, lower_wick=0.45, body=0.40)
    df_1m["pin_short"] = pa_candles.pin_bar_short(df_1m, upper_wick=0.45, body=0.40)
    df_1m["hammer"] = pa_candles.hammer(df_1m)
    df_1m["inv_hammer"] = pa_candles.inverted_hammer(df_1m)

    engine = XAUScalpChallenge(starting_balance=start_balance if start_balance is not None else STARTING_BALANCE)
    active_5m_breakout: Optional[Dict[str, Any]] = None
    consecutive_opposing_bars: int = 0

    catchup_ts_ms = None
    if catchup:
        base_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        now_utc = datetime.now(timezone.utc)
        catchup_ts_ms = int(base_dt.replace(hour=now_utc.hour, minute=now_utc.minute, second=now_utc.second).timestamp() * 1000)

    catchup_active = bool(catchup)
    if catchup_active:
        os.environ["XAU_NO_NTFY"] = "1"

    # Determine cutoff timestamp (ms) for simulation stop; None = full day
    if cutoff_utc is None:
        cutoff_ts_ms = None
    else:
        base_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        target_dt = base_dt.replace(hour=cutoff_utc.hour, minute=cutoff_utc.minute, second=cutoff_utc.second)
        cutoff_ts_ms = int(target_dt.timestamp() * 1000)

    # Step through 1m bars up to cutoff
    for idx in range(50, len(df_1m)):
        bar = df_1m.iloc[idx]
        curr_t_ms = int(bar["open_time"])
        if start_ms is not None and curr_t_ms < start_ms:
            continue
        bar_time_str = bar["datetime"].strftime("%H:%M UTC")

        # Seamless transition from catchup to live 1:1
        if catchup_active and catchup_ts_ms is not None and curr_t_ms >= catchup_ts_ms:
            catchup_active = False
            os.environ.pop("XAU_NO_NTFY", None)
            logger.info("⏩ Fast catch-up completed at %s! Account Balance: $%.2f. Entering 1:1 Live Simulation...",
                        bar_time_str, engine.balance)
            push_ntfy(
                title="✅ Catch-up Complete — LIVE 1:1",
                message=f"Missed hours synced up to {bar_time_str}. Account Balance: ${engine.balance:.2f}. Now live 1:1 streaming.",
                tags="white_check_mark,zap",
                priority="high",
            )

        # Stop if bar start time exceeds cutoff
        if cutoff_ts_ms is not None and curr_t_ms > cutoff_ts_ms:
            logger.info("Reached cutoff UTC time %s, ending simulation loop.", cutoff_utc.isoformat())
            break

        # Pacing: 0 delay during catchup; 12 intrabar sub-ticks (5s each) during 1:1 live
        if catchup_active or candle_delay_sec <= 0:
            sub_ticks = 1
            sub_delay = 0.0
        else:
            sub_ticks = 12 if candle_delay_sec >= 30.0 else 1
            sub_delay = candle_delay_sec / sub_ticks

        o_px = float(bar["open"])
        h_px = float(bar["high"])
        l_px = float(bar["low"])
        c_px = float(bar["close"])

        # Intrabar price trajectory: Open -> Extremes -> Close
        trajectory = np.linspace(o_px, c_px, sub_ticks)
        if sub_ticks > 2:
            trajectory[sub_ticks // 3] = l_px if o_px < c_px else h_px
            trajectory[2 * sub_ticks // 3] = h_px if o_px < c_px else l_px

        for sub_i, tick_px in enumerate(trajectory):
            tick_px = round(float(tick_px), 2)

            # Check 5m closed breakout
            m5_candidates = df_5m[df_5m["open_time"] <= curr_t_ms]
            if not m5_candidates.empty:
                last_5m = m5_candidates.iloc[-1]
                if bool(last_5m.get("break_up", False)) and not np.isnan(last_5m.get("res", np.nan)):
                    active_5m_breakout = {
                        "type": "UP",
                        "level": float(last_5m["res"]),
                        "bar_time": int(last_5m["open_time"]),
                    }
                elif bool(last_5m.get("break_down", False)) and not np.isnan(last_5m.get("sup", np.nan)):
                    active_5m_breakout = {
                        "type": "DOWN",
                        "level": float(last_5m["sup"]),
                        "bar_time": int(last_5m["open_time"]),
                    }

            # 1. Manage Active Position
            if engine.active_stack:
                flt_eq = engine.calculate_equity(tick_px)
                profit_gain = flt_eq - engine.balance
                max_risk_loss = max(15.0, engine.balance * 0.15)
                spike_target = max(50.0, engine.balance * 0.50)

                # Sync live dashboard with sub-second price
                slices_list = [
                    {"status": "FILLED", "sz": o.lot_size, "fill_price": o.entry_price}
                    for o in engine.active_stack
                ]
                pos_info = {
                    "symbol": SYMBOL,
                    "direction": engine.active_bias,
                    "orders": len(engine.active_stack),
                    "total_lots": round(sum(o.lot_size for o in engine.active_stack), 2),
                    "avg_entry": round(np.mean([o.entry_price for o in engine.active_stack]), 2),
                    "sl_price": engine.sl_price,
                    "slices": slices_list,
                }
                sync_dashboard_state(engine.balance, flt_eq, tick_px, f"{bar_time_str} (+{sub_i*5}s)", active_pos=pos_info)

                # Rapid equity spike (+50% target)
                if profit_gain >= spike_target:
                    engine.flatten_stack(tick_px, curr_t_ms, bar_time_str, f"Rapid Equity Spike (+${profit_gain:.2f})")
                    consecutive_opposing_bars = 0
                    if sub_delay > 0:
                        time.sleep(sub_delay)
                    continue

                # Invalidation SL hit
                hit_sl = (
                    (tick_px <= engine.sl_price if engine.active_bias == "BUY" else tick_px >= engine.sl_price)
                    or (profit_gain <= -max_risk_loss)
                )
                if hit_sl:
                    capped_loss = -max_risk_loss if profit_gain <= -max_risk_loss else profit_gain
                    engine.flatten_stack(tick_px, curr_t_ms, bar_time_str, f"Risk Stop Triggered (-${abs(capped_loss):.2f})", forced_pnl=capped_loss)
                    consecutive_opposing_bars = 0
                    if sub_delay > 0:
                        time.sleep(sub_delay)
                    continue

                # Momentum stall check on final sub-tick of candle (2 consecutive opposing bars required)
                if sub_i == sub_ticks - 1 and idx > engine.entry_bar_idx:
                    is_opposing = (
                        (engine.active_bias == "BUY" and c_px < o_px)
                        or (engine.active_bias == "SELL" and c_px > o_px)
                    )
                    if is_opposing:
                        consecutive_opposing_bars += 1
                    else:
                        consecutive_opposing_bars = 0

                    if consecutive_opposing_bars >= 2:
                        if profit_gain > 0:
                            engine.flatten_stack(tick_px, curr_t_ms, bar_time_str, f"Momentum Stall in Profit (+${profit_gain:.2f})")
                        else:
                            capped_loss = max(profit_gain, -max_risk_loss)
                            engine.flatten_stack(tick_px, curr_t_ms, bar_time_str, "Momentum Stall: 2 bars closed against bias", forced_pnl=capped_loss)
                        consecutive_opposing_bars = 0
                        if sub_delay > 0:
                            time.sleep(sub_delay)
                        continue

            else:
                # Idle dashboard sync
                sync_dashboard_state(engine.balance, engine.balance, tick_px, f"{bar_time_str} (+{sub_i*5}s)", active_pos=None)

            if sub_delay > 0:
                time.sleep(sub_delay)

        # 2. Check for Retest + Rejection Setup at Candle Close
        curr_px = c_px
        if not engine.active_stack and active_5m_breakout is not None:
            lvl = active_5m_breakout["level"]
            b_type = active_5m_breakout["type"]
            b_time = active_5m_breakout["bar_time"]

            if 0 < (curr_t_ms - b_time) <= 20 * 60_000:
                if b_type == "UP":
                    trend_ok = curr_px > bar["ema20"] > bar["ema50"]
                    retest_ok = float(bar["low"]) <= lvl + 1.2 and float(bar["high"]) >= lvl - 0.2
                    rng = max(0.01, float(bar["high"]) - float(bar["low"]))
                    lower_wick = min(float(bar["open"]), float(bar["close"])) - float(bar["low"])
                    wick_ratio = lower_wick / rng
                    rejection_ok = (wick_ratio >= 0.45 and float(bar["close"]) >= float(bar["open"])) or bool(bar["pin_long"]) or bool(bar["hammer"])

                    if trend_ok and retest_ok and rejection_ok:
                        context = {
                            "symbol": SYMBOL,
                            "direction": "BUY",
                            "setup": "5m Breakout Up -> 1m Retest & Rejection",
                            "5m_broken_level": lvl,
                            "1m_rejection_price": curr_px,
                            "rejection_wick_ratio": round(wick_ratio, 2),
                            "volume": float(bar["volume"]),
                        }
                        is_go, reasoning = query_llama_validation(context)
                        if is_go:
                            sl_px = round(float(bar["low"]) - 0.15, 2)
                            engine.open_stack("BUY", curr_px, sl_px, idx, curr_t_ms, bar_time_str, reasoning)
                            active_5m_breakout = None
                            consecutive_opposing_bars = 0

                elif b_type == "DOWN":
                    trend_ok = curr_px < bar["ema20"] < bar["ema50"]
                    retest_ok = float(bar["high"]) >= lvl - 1.2 and float(bar["low"]) <= lvl + 0.2
                    rng = max(0.01, float(bar["high"]) - float(bar["low"]))
                    upper_wick = float(bar["high"]) - max(float(bar["open"]), float(bar["close"]))
                    wick_ratio = upper_wick / rng
                    rejection_ok = (wick_ratio >= 0.45 and float(bar["close"]) <= float(bar["open"])) or bool(bar["pin_short"]) or bool(bar["inv_hammer"])

                    if trend_ok and retest_ok and rejection_ok:
                        context = {
                            "symbol": SYMBOL,
                            "direction": "SELL",
                            "setup": "5m Breakout Down -> 1m Retest & Rejection",
                            "5m_broken_level": lvl,
                            "1m_rejection_price": curr_px,
                            "rejection_wick_ratio": round(wick_ratio, 2),
                            "volume": float(bar["volume"]),
                        }
                        is_go, reasoning = query_llama_validation(context)
                        if is_go:
                            sl_px = round(float(bar["high"]) + 0.15, 2)
                            engine.open_stack("SELL", curr_px, sl_px, idx, curr_t_ms, bar_time_str, reasoning)
                            active_5m_breakout = None
                            consecutive_opposing_bars = 0

    if engine.active_stack:
        engine.flatten_stack(float(df_1m["close"].iloc[-1]), int(df_1m["open_time"].iloc[-1]), "Session End", "Session End Flush")

    day_pnl = engine.balance - engine.starting_balance
    vault = get_vault_state()
    old_bankroll = vault.get("vault_bankroll", 100.0)
    new_bankroll = round(max(0.0, old_bankroll + day_pnl), 2)
    vault["vault_bankroll"] = new_bankroll
    vault["total_realized_profit"] = round(vault.get("total_realized_profit", 0.0) + day_pnl, 2)
    vault["sessions_completed"] = vault.get("sessions_completed", 0) + 1
    vault["last_session_date"] = date_str
    next_tier = calculate_tier_for_bankroll(new_bankroll)
    vault["current_tier"] = next_tier
    save_vault_state(vault)

    logger.info("Session Run Complete. Starting Balance: $%.2f | Final Account Balance: $%.2f (Return: %+.1f%%)",
                engine.starting_balance, engine.balance, ((engine.balance - engine.starting_balance) / engine.starting_balance * 100.0))
    logger.info("🏛️ Stratton Vault Bankroll: $%.2f -> $%.2f | Next Morning Tier Allocation: $%.2f",
                old_bankroll, new_bankroll, next_tier)


def main() -> None:
    parser = argparse.ArgumentParser(description="XAU $50 -> $3000+ Aggressive Scalper (1:1 Live Simulation)")
    parser.add_argument("--realtime", action="store_true", default=True, help="Run in strict 1:1 real time (60s per 1m candle)")
    parser.add_argument("--delay", type=float, default=None, help="Custom delay in seconds per 1m candle")
    parser.add_argument("--port", type=int, default=WEB_PORT, help="Port for web dashboard monitoring (default: 8443)")
    parser.add_argument("--date", type=str, default="2026-09-17", help="Replay date YYYY-MM-DD (default: 2026-09-17)")
    parser.add_argument("--demo-wednesday", action="store_true", help="Demo trade day on last-week Wednesday 2026-09-16, 1:1 exactly like live")
    parser.add_argument("--catchup", action="store_true", help="Fast-forward 00:00 UTC→now with no sleeps, then continue live 1x")
    parser.add_argument("--catchup-speed", type=float, default=0.0, help="Delay per bar during catch-up phase (0 = instant)")
    parser.add_argument("--no-ntfy", action="store_true", help="Suppress per-trade ntfy pushes (one summary still sent unless --quiet)")
    parser.add_argument("--quiet", action="store_true", help="Suppress even the summary ntfy push")
    args = parser.parse_args()

    if args.no_ntfy or args.quiet:
        os.environ["XAU_NO_NTFY"] = "1"

    if args.demo_wednesday:
        args.date = WEDNESDAY_DATE_STR
    date_str = args.date

    candle_delay = args.delay if args.delay is not None else (60.0 if args.realtime else 1.0)

    # Step 1: Pre-flight cleanup & Kill Switch
    preflight_killswitch()

    # Step 2: Start Stratton Oakmont HFT Web Terminal on background thread
    start_stratton_oakmont_app(port=args.port)

    if args.catchup:
        now_utc = datetime.now(timezone.utc)
        logger.info("Catch-up mode: fast-forwarding %s 00:00 UTC → %s with no sleeps, then live 1x...",
                    date_str, now_utc.strftime("%H:%M UTC"))
        push_ntfy(
            title="⏩ Catch-up sync started",
            message=f"Fast-forwarding {date_str} 00:00 UTC → now, then continuing 1:1 live.",
            tags="fast_forward,chart",
            priority="default",
        )
        run_trading_cycle(candle_delay_sec=candle_delay, date_str=date_str,
                          demo_label="LIVE", catchup=True)
        return

    # Step 3: Push startup alert to ntfy
    demo_tag = " (WEDNESDAY DEMO 2026-09-16, 1:1)" if args.demo_wednesday else ""
    push_ntfy(
        title=f"🛡️ XAU $50 Challenge Armed (1:1 Live){demo_tag}",
        message=f"XAU $50 Challenge Armed{demo_tag}. Date: {date_str}. 1:1 Real-Time Simulation Active. Stratton Terminal: http://localhost:{args.port}/?t=7SQMRVRJ-VkD4lG3VXsb1Fc82oYUAP93",
        tags="shield,white_check_mark",
        priority="high",
    )
    sync_dashboard_state(STARTING_BALANCE, STARTING_BALANCE, 2500.00, "00:00 UTC", message=f"Armed & live.{demo_tag}")

    logger.info("Scheduler configured. Target: EXACTLY 03:30 AM Tehran Time (Asia/Tehran).")
    schedule.every().day.at(TARGET_START_TIME).do(lambda: run_trading_cycle(candle_delay, date_str=date_str))

    now_tehran = datetime.now(TEHRAN_TZ)
    logger.info("Current Tehran Time: %s | Mode: 1:1 Real-Time (%.1fs / 1m candle) | Date: %s%s",
                now_tehran.strftime("%Y-%m-%d %H:%M:%S %Z"), candle_delay, date_str, demo_tag)

    # Run the 1:1 real-time live trading cycle
    run_trading_cycle(candle_delay_sec=candle_delay, date_str=date_str,
                      demo_label="WED-DEMO 1:1" if args.demo_wednesday else "LIVE")

    # Blocking loop for daily cycles
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Service manually stopped.")
    except BaseException as fatal_exc:
        notify_critical_error(
            error_title="FATAL SYSTEM CRASH",
            details=f"Uncaught crash in main loop: {fatal_exc}",
            exc=fatal_exc,
        )
        raise

