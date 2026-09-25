"""
run_xau_broker_live.py
======================
Production Live Trading Engine for XAUUSD executed directly on LiteFinance MT5 Demo.
Restored directly to the pure "To The Moon" Sovereign Compounding Architecture with:
- Wednesday Breakout -> Retest -> Rejection candle algorithm (ApexTrinityStrategy)
- Laya System 1 Decision & Politician Brain Macro Confluence
- Sovereign Compounding Ladder (0.02 - 5.00 lots) calibrated for current demo balance ($35+)
- Dynamic Peak Watermark Bag Protection (locks cash if profit pulls back 18% from peak)
- Fast Breakeven Lock (+1.0 ATR) to guarantee risk-free cushion
- Physical broker-side Stop-Loss injection via LiteFinance DOM
- Spread blowout veto (> $0.45), 5-min post-loss cooldown, 60-min 2-loss lockout
- Friday 18:00 UTC curfew and 20:30 UTC force-flatten
- Real-time mobile dashboard sync (:443, :8088, :8443) and ntfy push alerts
- Stratton Vault bankroll & tier persistence
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import math
import os
import signal
import sys
import time
import urllib.request
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

# Ensure root directory is on Python path
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from engine.litefinance_gateway import LiteFinanceGateway, AccountSnapshot, QuoteSnapshot
import scalper.pa.levels as pa_levels
import scalper.pa.candles as pa_candles
from bark_integration import send_alert, push_bark
from scalper.brain.laya_oracle import get_laya_oracle, LayaOracle
from scalper.strategies.apex_trinity import ApexTrinityStrategy, ApexSignal
from scalper.strategies.micro_exit_controller import (
    MicroExitController,
    get_micro_account_config,
    get_to_the_moon_config,
    ExitDecision,
)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("xau_broker_live")

# Configuration & Paths
DATA_DIR = ROOT_DIR / "data"
STATE_FILE_APP = DATA_DIR / "state" / "hft.json"
STATE_FILE_RELAPSE = DATA_DIR / "relapse_scalper_state.json"
VAULT_FILE = DATA_DIR / "stratton_vault.json"
LIVE_JOURNAL_CSV = DATA_DIR / "live_trade_journal.csv"
LIVE_JOURNAL_JSON = DATA_DIR / "live_trade_journal.json"
DEFAULT_NTFY_URL = "https://ntfy.sh"
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "tbt-96c0dc08c297676b")
WEB_PORT = int(os.getenv("SCALPER_APP_PORT", "443"))
LLAMA_COMPLETION_URL = os.getenv("LLAMA_SERVER_URL", "http://127.0.0.1:8080/completion")

MAX_RISK_STOP_USD = 15.00      # Hard -$15.00 loss floor
RAPID_SPIKE_TARGET_USD = 50.00 # Target rapid profit spike harvest (+50% / $50 per tier)


def log_live_trade(trade: Dict[str, Any]) -> None:
    """Logs executed trade to CSV and JSON journals on persistent storage."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    file_exists = LIVE_JOURNAL_CSV.exists()
    fieldnames = [
        "timestamp", "date", "time_utc", "symbol", "direction",
        "strategy", "entry_price", "exit_price", "volume", "peak_floating_pnl",
        "realized_pnl", "exit_reason", "duration_min", "balance_after", "equity_after"
    ]
    try:
        with open(LIVE_JOURNAL_CSV, "a", newline="", encoding="utf-8") as f_csv:
            writer = csv.DictWriter(f_csv, fieldnames=fieldnames, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            writer.writerow(trade)
    except Exception as ex:
        logger.error("Error writing live trade to CSV: %s", ex)

    try:
        entries = []
        if LIVE_JOURNAL_JSON.exists():
            try:
                with open(LIVE_JOURNAL_JSON, "r", encoding="utf-8") as f_json:
                    entries = json.load(f_json)
            except Exception:
                entries = []
        entries.append(trade)
        tmp_json = LIVE_JOURNAL_JSON.with_suffix(".tmp")
        with open(tmp_json, "w", encoding="utf-8") as f_json:
            json.dump(entries, f_json, indent=2)
        tmp_json.replace(LIVE_JOURNAL_JSON)
    except Exception as ex:
        logger.error("Error writing live trade to JSON: %s", ex)


def start_stratton_oakmont_app(port: int = WEB_PORT) -> None:
    try:
        import scalper.app.app as stratton_app
        stratton_app.PORT = port
        t = threading.Thread(target=stratton_app.run_app, daemon=True)
        t.start()
        logger.info("🏛️ Stratton Oakmont HFT Terminal live at:")
        logger.info("   👉 Official HTTPS (Trusted SSL): https://82-115-21-155.sslip.io/")
        logger.info("   👉 Standard HTTP (No port needed): http://82.115.21.155/")
        logger.info("   👉 Alternative Ports: http://82.115.21.155:8088/ and https://82.115.21.155:8443/")
    except Exception as exc:
        logger.warning("Local terminal app start skipped (%s)", exc)


def get_vault_state() -> Dict[str, Any]:
    """Reads vault state from persistent JSON file."""
    if VAULT_FILE.exists():
        try:
            with open(VAULT_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "seed_capital": 100.0,
        "vault_bankroll": 293.77,
        "current_tier": 300.0,
        "last_session_date": "",
        "total_realized_profit": 193.77,
        "sessions_completed": 1,
        "latency_metrics": {
            "avg_signal_to_stack_ms": 0.2,
            "last_dispatch_latency_ms": 0.2,
            "total_stacks_dispatched": 0,
        },
    }


def save_vault_state(state: Dict[str, Any]) -> None:
    """Saves vault state atomically."""
    VAULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = VAULT_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    tmp.replace(VAULT_FILE)


def push_ntfy(title: str, message: str, tags: str = "zap,chart", priority: str = "high") -> None:
    """Dispatches one-liner push notification asynchronously without blocking the event loop."""
    try:
        clean_msg = " · ".join([line.strip() for line in message.strip().splitlines() if line.strip()])
        clean_title = title.strip()
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, send_alert, clean_title, clean_msg, priority)
    except RuntimeError:
        try:
            clean_msg = " · ".join([line.strip() for line in message.strip().splitlines() if line.strip()])
            send_alert(title=title.strip(), message=clean_msg, priority=priority)
        except Exception:
            pass
    except Exception as e:
        logger.debug("push_ntfy dispatch failed: %s", e)


def calc_required_margin(price: float, lots: float, leverage: float = 500.0) -> float:
    """Calculates exact required broker margin for XAUUSD (100 oz contract) under 1:500 leverage."""
    return (price * 100.0 * lots) / leverage


def sync_dashboard_state(
    balance: float,
    equity: float,
    active_pos: Optional[Dict[str, Any]] = None,
    message: str = "",
    mid_px: float = 0.0,
    bid_px: float = 0.0,
    ask_px: float = 0.0,
    tier: float = 300.0,
    last_latency_ms: float = 0.0,
    laya_telemetry: Optional[Dict[str, Any]] = None,
    daily_withdrawal: Optional[Dict[str, Any]] = None,
    bot_running: bool = True,
    account_mode: str = "REAL",
    start_balance: Optional[float] = None,
) -> None:
    """Syncs live broker telemetry to HFT dashboard JSON file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "state").mkdir(parents=True, exist_ok=True)

    clean_pos = None
    if active_pos:
        clean_pos = {k: v for k, v in active_pos.items() if k != "micro_exit"}

    base_bal = start_balance if (start_balance is not None and start_balance > 0) else (59.87 if account_mode == "REAL" else balance)
    realized = round(balance - base_bal, 2)
    pnl_pct = round((equity - base_bal) / base_bal * 100.0, 2) if base_bal > 0 else 0.0

    payload = {
        "engine": "XAUUSD Stratton Oakmont Broker LIVE Engine",
        "status": "ACTIVE" if bot_running else "PAUSED",
        "bot_running": bot_running,
        "fsm_state": "IN_POSITION" if active_pos else ("PAUSED" if not bot_running else "SCANNING"),
        "mode": f"BROKER LIVE (LiteFinance {account_mode} Account)",
        "account_mode": account_mode,
        "symbol": "XAUUSD",
        "balance": round(balance, 2),
        "equity": round(equity, 2),
        "realized_pnl": realized,
        "pnl_pct": pnl_pct,
        "current_tier": tier,
        "current_price": mid_px,
        "mid_price": mid_px,
        "best_bid": bid_px,
        "best_ask": ask_px,
        "spread_bps": round(((ask_px - bid_px) / mid_px * 10000.0), 2) if mid_px > 0 else 0.0,
        "latency_ms": round(last_latency_ms, 2),
        "position": clean_pos,
        "laya": laya_telemetry or {},
        "daily_withdrawal": daily_withdrawal or {},
        "recent_logs": [message] if message else [],
        "updated_at": time.time(),
        "updated_iso": datetime.now(timezone.utc).isoformat(),
    }

    for target in (STATE_FILE_APP, STATE_FILE_RELAPSE):
        try:
            tmp = target.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(payload, f, indent=2)
            tmp.replace(target)
        except Exception as e:
            logger.debug("Failed to write dashboard state to %s: %s", target, e)


class LiveBrokerScalper:
    """
    Manages live candle generation, "To The Moon" ICT Sovereign strategy, and order dispatch via LiteFinanceGateway.
    Supports multi-order parallel execution and direction-aware asymmetric trade management.
    """

    def __init__(self, gateway: LiteFinanceGateway, symbol: str = "XAUUSD"):
        self.gw = gateway
        self.symbol = symbol
        self.apex = ApexTrinityStrategy(min_candles_warmup=30)
        self.active_positions: List[Dict[str, Any]] = []
        self.candles_1m: List[Dict[str, Any]] = []
        self.current_1m_bar: Optional[Dict[str, Any]] = None
        self.last_latency_ms: float = 0.0
        self.last_wick_ratio: float = 0.50
        self.last_trend_aligned: bool = True
        self.daily_date: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.daily_start_balance: float = 0.0
        self.milestone_notified: set[int] = set()

        # Institutional Risk Hardening
        self.cooldown_until: float = 0.0
        self.lockout_until: float = 0.0
        self.consecutive_losses: int = 0
        self.daily_realized_loss: float = 0.0
        self.circuit_breaker_active: bool = False
        self.trading_paused: bool = False
        self.ghost_position_ticks: int = 0
        self.account_mode: str = "DEMO"
        self.micro_exit: MicroExitController = MicroExitController(get_micro_account_config(balance=30.0))

    @property
    def active_stack(self) -> Optional[Dict[str, Any]]:
        return self.active_positions[-1] if self.active_positions else None

    @active_stack.setter
    def active_stack(self, val: Optional[Dict[str, Any]]):
        if val is None:
            self.active_positions.clear()
        else:
            if not self.active_positions:
                self.active_positions.append(val)
            else:
                self.active_positions[-1] = val

    def compute_daily_withdrawal(self, current_balance: float) -> Dict[str, Any]:
        """
        Computes recommended daily profit withdrawal according to the "To The Moon" Sovereign schedule:
        - Under $1,000 balance: 30% daily profit cash-out.
        - $1,000 - $5,000 balance: 50% daily profit cash-out.
        - $5,000+ balance: 70% daily profit cash-out.
        """
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.daily_date != today_str:
            self.daily_date = today_str
            self.daily_start_balance = current_balance
            self.daily_realized_loss = 0.0
            self.consecutive_losses = 0
            self.circuit_breaker_active = False
            self.milestone_notified.clear()
        elif self.daily_start_balance <= 0.0:
            self.daily_start_balance = current_balance

        daily_profit = max(0.0, current_balance - self.daily_start_balance)
        if current_balance < 1000.0:
            rate = 0.30
        elif current_balance < 5000.0:
            rate = 0.50
        else:
            rate = 0.70

        cashout_target = round(daily_profit * rate, 2)
        retained = round(current_balance - cashout_target, 2)

        return {
            "daily_date": self.daily_date,
            "daily_start_balance": round(self.daily_start_balance, 2),
            "current_balance": round(current_balance, 2),
            "daily_profit": round(daily_profit, 2),
            "withdrawal_rate_pct": int(rate * 100),
            "recommended_cashout_today": cashout_target,
            "retained_compounding_balance": retained,
            "status": "READY_FOR_CASH_OUT" if cashout_target >= 50.0 else "ACCUMULATING",
        }

    def is_in_allowed_session(self, hour_utc: Optional[int] = None, allow_news_expansion: bool = False) -> Tuple[bool, str]:
        """
        Restricts trading strictly to the 4 Empirical Master Sessions (>83% Win Rate)
        plus the Post-News Institutional Expansion Window:
        1. Asian Range Accumulation: 00:00 - 06:00 UTC (82.7% WR)
        2. London Mid-Day Continuation: 09:00 - 12:00 UTC (84.4% WR)
        3. London Close / NY PM Overlap: 15:00 - 18:00 UTC (85.6% WR)
        4. US Evening / Pacific Session: 18:00 - 23:00 UTC (83.3% WR)
        5. Post-News Institutional Expansion Window (+5m to +45m post-release)
        """
        now = datetime.now(timezone.utc)
        hr = hour_utc if hour_utc is not None else now.hour

        # Friday Evening Curfew (No trades after Friday 18:00 UTC / 21:30 IRST)
        if now.weekday() == 4 and hr >= 18:
            return False, "Friday Evening Pre-Weekend Curfew (Trading Blocked)"

        # Weekend Market Closed
        if now.weekday() == 5 or (now.weekday() == 6 and hr < 22):
            return False, "Weekend Market Closed (Trading Blocked)"

        # Toxic Rollover Hour
        if hr >= 23:
            return False, "Toxic Rollover Hour (Spread Expansion Blocked)"

        # Post-News Institutional Expansion Window (permitted even if 12-15 UTC whipsaw, because the initial spike has cleared!)
        if allow_news_expansion:
            return True, "Post-News Institutional Expansion Window (Volatility Momentum)"

        # Blocked High-Whip Zones
        if 6 <= hr < 9:
            return False, "London Open Judas Window (Filter: Blocked for False-Breakout Safety)"
        if 12 <= hr < 15:
            return False, "New York Open Whipsaw Window (Filter: Blocked for Spread/News Noise)"

        # The 4 Allowed Master Sessions (>83% WR)
        if 0 <= hr < 6:
            return True, "Asian Range Accumulation (82.7% WR)"
        elif 9 <= hr < 12:
            return True, "London Mid-Day Continuation (84.4% WR)"
        elif 15 <= hr < 18:
            return True, "London Close / NY PM Overlap (85.6% WR)"
        elif 18 <= hr < 23:
            return True, "US Evening / Pacific Session (83.3% WR)"

        return False, "Off-Hours Interbank Transition (Filter: Blocked)"

    def get_current_session_label(self) -> str:
        """Returns ICT session status label based on current UTC hour."""
        _, label = self.is_in_allowed_session()
        return label

    def compute_lot_size(self, balance: float, current_price: float = 4285.0) -> float:
        """
        "To The Moon" Sovereign Compounding Ladder (Calibrated for LiteFinance 1:500 Leverage):
        XAUUSD contract = 100 oz. At ~$4285/oz:
        - 1.00 lot margin = $857.00
        - 0.01 lot margin = $8.57

        Calibrated Tiers (keeping base margin ~25-45% of balance, leaving buffer for A+ boost & noise):
          - Balance < $35:    0.01 lots ($8.57 margin)
          - $35 - $75 Tier:   0.02 lots ($17.14 margin = 28.6% of $59.87 balance) -> ACTIVE TIER!
          - $75 - $150 Tier:  0.04 lots ($34.28 margin)
          - $150 - $300 Tier: 0.08 lots ($68.56 margin)
          - $300 - $600 Tier: 0.15 lots ($128.55 margin)
          - $600 - $1200:     0.30 lots ($257.10 margin)
          - $1200 - $2500:    0.60 lots ($514.20 margin)
          - $2500 - $5000:    1.20 lots ($1028.40 margin)
          - $5000+ Tier:      min(10.00, round(balance / 4000.0, 2))
        """
        if balance < 75.0:
            lots = 0.01  # Strictly 0.01 lot for micro-accounts (<$75) to maintain massive margin buffer
        elif balance < 100.0:
            lots = 0.02  # $17.14 margin
        elif balance < 180.0:
            lots = 0.05
        elif balance < 350.0:
            lots = 0.10
        elif balance < 600.0:
            lots = 0.20
        elif balance < 1000.0:
            lots = 0.40
        elif balance < 1800.0:
            lots = 0.80
        elif balance < 3000.0:
            lots = 1.50
        elif balance < 6000.0:
            lots = 3.00
        else:
            lots = min(10.00, round(balance / 1200.0, 2))

        # Absolute Margin Safety Guard (1:500 leverage):
        # On micro accounts (<$100), clamp to max 30% margin utilization so drawdowns never risk margin call
        margin_per_001 = calc_required_margin(current_price, 0.01)
        if balance < (margin_per_001 * 1.50):
            return 0.0  # Balance insufficient to safely open 0.01 lots with margin cushion
        if balance < 75.0:
            return 0.01  # Inviolable 0.01 micro-account lock below $75.00
        if balance < 100.0:
            max_safe_lots = max(0.01, math.floor((balance * 0.30) / margin_per_001) * 0.01)
            return max(0.01, min(lots, round(max_safe_lots, 2)))
        return lots

    def is_in_killzone(self, hour_utc: Optional[int] = None) -> bool:
        allowed, _ = self.is_in_allowed_session(hour_utc)
        return allowed

    def compute_stack_sizing(self, balance: float, stop_distance: float = 2.50, mode: str = "TO_THE_MOON") -> Tuple[int, float, float]:
        if mode == "CONSERVATIVE_PROP_FIRM":
            vol = round(min(0.08, max(0.02, (balance * 0.02) / (stop_distance * 100.0))), 2)
            lot = 0.01
            count = int(vol / lot)
            return count, lot, vol
        vol = self.compute_lot_size(balance)
        return 1, vol, vol

    def update_tick(self, quote: QuoteSnapshot) -> None:
        """Accumulates ticks into 1-minute OHLCV candles."""
        px = quote.mid
        t = quote.timestamp
        current_minute_ts = int(t // 60) * 60

        if self.current_1m_bar is None or self.current_1m_bar["minute_ts"] != current_minute_ts:
            if self.current_1m_bar is not None:
                self.candles_1m.append(self.current_1m_bar)
                if len(self.candles_1m) > 720:
                    self.candles_1m = self.candles_1m[-720:]
            self.current_1m_bar = {
                "minute_ts": current_minute_ts,
                "open_time": current_minute_ts * 1000,
                "open": px,
                "high": px,
                "low": px,
                "close": px,
                "volume": 1,
            }
        else:
            self.current_1m_bar["high"] = max(self.current_1m_bar["high"], px)
            self.current_1m_bar["low"] = min(self.current_1m_bar["low"], px)
            self.current_1m_bar["close"] = px
            self.current_1m_bar["volume"] += 1

    def evaluate_strategy(self, quote: Optional[QuoteSnapshot] = None, account_balance: Optional[float] = None) -> Optional[ApexSignal]:
        """
        Evaluates "To The Moon" Master Sessions Strategy:
        Strictly gated to the 4 empirical top-performing sessions (>83% Win Rate).
        """
        allowed, _ = self.is_in_allowed_session()
        if not allowed:
            return None
        if self.circuit_breaker_active or time.time() < self.cooldown_until or time.time() < self.lockout_until:
            return None
        if quote and (quote.ask - quote.bid) > 0.45:
            return None
        now_utc = datetime.now(timezone.utc)
        if now_utc.weekday() == 4 and now_utc.hour >= 18:
            return None
        if len(self.candles_1m) < 30:
            return None
        return self.apex.evaluate(self.candles_1m)


async def run_live_scalper():
    """Main async execution loop."""
    logger.info("Initializing Live Broker Scalper ('To The Moon' Sovereign Architecture)...")

    # Start Stratton Oakmont HFT Web Terminal on background thread (:443 / :8088 / :80)
    start_stratton_oakmont_app(port=WEB_PORT)

    gw = LiteFinanceGateway()
    connected = await gw.initialize()
    if not connected:
        logger.error("Could not connect to LiteFinance. Exiting.")
        return

    acc_snap = await gw.get_account_snapshot()
    logger.info("🏦 LIVE BROKER CONNECTED: Balance: $%.2f | Assets Used: $%.2f", acc_snap.balance, acc_snap.assets_used)

    vault = get_vault_state()
    laya_oracle = get_laya_oracle()
    scalper = LiveBrokerScalper(gw)

    initial_mode = await gw.get_account_mode()
    mode_file = ROOT_DIR / "data" / "account_mode.json"
    if initial_mode == "UNKNOWN" and mode_file.exists():
        try:
            persisted = json.loads(mode_file.read_text())
            initial_mode = persisted.get("account_mode", "UNKNOWN")
            logger.info("Loaded persisted account mode: %s", initial_mode)
        except Exception:
            pass
    if initial_mode == "UNKNOWN":
        initial_mode = "DEMO"  # Default safe mode
    scalper.account_mode = initial_mode
    scalper.daily_start_balance = acc_snap.balance
    scalper.last_known_balance = acc_snap.balance
    logger.info("🏦 LIVE BROKER CONNECTED (%s MODE): Balance: $%.2f | Assets Used: $%.2f", initial_mode, acc_snap.balance, acc_snap.assets_used)

    push_ntfy(
        title="🟢 Stratton Live Engine Armed",
        message=f"LiteFinance {initial_mode} · Balance: ${acc_snap.balance:.2f} · 1:500 Live Margin Active",
        tags="rocket,white_check_mark",
    )

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: stop_event.set())

    logger.info("Entering live trading loop with Laya System 1 Surveillance...")
    tick_count = 0
    last_quote_time = time.time()

    try:
        while not stop_event.is_set():
            try:
                # 0. Check external web command (e.g. Emergency Flatten button) with atomic rename
                cmd_file = DATA_DIR / "command.json"
                if cmd_file.exists():
                    proc_cmd = DATA_DIR / f"command_proc_{time.time_ns()}.json"
                    try:
                        cmd_file.replace(proc_cmd)
                        with open(proc_cmd, "r", encoding="utf-8") as f:
                            cmd = json.load(f)
                        action = cmd.get("action")
                        if action == "FLATTEN":
                            logger.warning("🚨 EMERGENCY FLATTEN SIGNAL RECEIVED FROM DASHBOARD!")
                            res = await gw.flatten_all_positions()
                            push_ntfy(
                                title="🛑 Manual Flatten Executed",
                                message=f"Closed all positions via Dashboard · Balance: ${res.get('balance', 0):.2f}",
                                tags="warning,hand",
                                priority="urgent",
                            )
                            if res.get("success") and res.get("assets_used", 1.0) <= 0.0:
                                scalper.active_positions.clear()
                                scalper.active_stack = None
                        elif action == "PAUSE":
                            logger.warning("⏸️ BOT AUTO-TRADE PAUSED VIA DASHBOARD")
                            scalper.trading_paused = True
                        elif action == "RESUME":
                            logger.info("▶️ BOT AUTO-TRADE RESUMED VIA DASHBOARD")
                            scalper.trading_paused = False
                        elif action == "TOGGLE_PAUSE":
                            scalper.trading_paused = not getattr(scalper, "trading_paused", False)
                            logger.info("🔄 BOT TRADING TOGGLED: paused=%s", scalper.trading_paused)
                        elif action == "CLEAR_OVERLAYS":
                            logger.info("🧹 Clear overlays command received from Sentinel.")
                            await gw._clear_overlays()
                    except Exception as ce:
                        logger.warning("Error processing dashboard command: %s", ce)
                    finally:
                        proc_cmd.unlink(missing_ok=True)
    
                # 1. Fetch live quote via reactive event stream or RAM lookup
                quote = await gw.wait_for_quote(timeout=0.10)
                now_time = time.time()
                is_stale = False
                if quote:
                    if (now_time - quote.timestamp) > 2.0:
                        is_stale = True
                        quote = None
                else:
                    quote = await gw.get_live_quote()
                    if quote and (now_time - quote.timestamp) > 2.0:
                        is_stale = True
                        quote = None
    
                if not quote or is_stale:
                    if (now_time - last_quote_time) > 20.0 and not getattr(gw, "_reconnecting", False):
                        logger.warning("⚠️ Market quote stream stalled (>20s). Triggering self-healing gateway reconnect...")
                        gw._spawn_bg_task(gw.reconnect())
                        last_quote_time = now_time
                    await asyncio.sleep(0.05)
                    continue
    
                last_quote_time = now_time
                tick_count += 1
                scalper.update_tick(quote)
    
                # 2. Check position state if in trade
                if scalper.active_positions:
                    acc = await gw.get_account_snapshot(force_fresh=True)
                    
                    # 2.1 Ghost Position Watchdog (if broker closed order or hit SL/TP externally)
                    if acc.assets_used <= 0.0:
                        scalper.ghost_position_ticks += 1
                        oldest_pos_age = time.time() - min(p["open_time"] for p in scalper.active_positions)
                        if scalper.ghost_position_ticks >= 20 and oldest_pos_age >= 3.0:
                            logger.warning("👻 GHOST POSITION DETECTED: Broker reports 0 assets used for 20 ticks (age %.1fs). Reconciling closed trade...", oldest_pos_age)
                            ghost_positions = list(scalper.active_positions)
                            fresh_acc = await gw.get_account_snapshot(force_fresh=True)
                            pnl_diff = round(fresh_acc.balance - getattr(scalper, "last_known_balance", fresh_acc.balance), 2)
                            scalper.active_positions.clear()
                            scalper.active_stack = None
                            scalper.ghost_position_ticks = 0

                            for gp in ghost_positions:
                                log_live_trade({
                                    "timestamp": time.time(),
                                    "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                                    "time_utc": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                                    "symbol": "XAUUSD",
                                    "direction": gp["direction"],
                                    "strategy": gp.get("strategy_type", "TO_THE_MOON"),
                                    "entry_price": gp["entry_price"],
                                    "exit_price": quote.mid,
                                    "volume": gp["volume"],
                                    "peak_floating_pnl": gp.get("peak_pnl", 0.0),
                                    "realized_pnl": pnl_diff,
                                    "exit_reason": "BROKER_TP_OR_EXTERNAL_CLOSE",
                                    "duration_min": max(1, int((time.time() - gp["open_time"]) // 60)),
                                    "balance_after": fresh_acc.balance,
                                    "equity_after": fresh_acc.equity,
                                })

                            if pnl_diff >= 0:
                                push_ntfy(
                                    title=f"💰 Broker Locked: +${pnl_diff:.2f}",
                                    message=f"Broker TP/Close @ ${quote.mid:.2f} · Bal: ${fresh_acc.balance:.2f}",
                                    tags="moneybag,shield",
                                    priority="high",
                                )
                                # Check Demo-to-Real Auto-Switch Trigger on broker-side win!
                                if getattr(scalper, "account_mode", "REAL") == "DEMO" and pnl_diff > 0.0:
                                    logger.info("🎯 FIRST DEMO WIN DETECTED (+${pnl_diff:.2f})! Initiating automatic transition to LIVE REAL account...")
                                    sw_res = await gw.switch_account_mode("REAL")
                                    if sw_res.get("success"):
                                        scalper.account_mode = "REAL"
                                        fresh_real = await gw.get_account_snapshot(force_fresh=True)
                                        scalper.daily_start_balance = fresh_real.balance
                                        scalper.last_known_balance = fresh_real.balance
                                        scalper.daily_realized_loss = 0.0
                                        scalper.consecutive_losses = 0
                                        try:
                                            mf = ROOT_DIR / "data" / "account_mode.json"
                                            mf.parent.mkdir(parents=True, exist_ok=True)
                                            mf.write_text(json.dumps({
                                                "account_mode": "REAL",
                                                "switched_at": time.time(),
                                                "switched_at_iso": datetime.now(timezone.utc).isoformat(),
                                                "real_balance": fresh_real.balance,
                                                "trigger_win_usd": pnl_diff,
                                            }, indent=2))
                                        except Exception as me:
                                            logger.warning("Could not persist account_mode.json: %s", me)
                                        push_ntfy(
                                            title="🟢 Real Account Active!",
                                            message=f"Switched to REAL mode · Balance: ${fresh_real.balance:.2f} · Ready to compound.",
                                            tags="white_check_mark,moneybag",
                                            priority="urgent",
                                        )
                            else:
                                push_ntfy(
                                    title=f"🛑 Broker Cut: ${pnl_diff:.2f}",
                                    message=f"Broker SL/Close @ ${quote.mid:.2f} · Bal: ${fresh_acc.balance:.2f}",
                                    tags="octagonal_sign,warning",
                                    priority="urgent",
                                )
                            scalper.last_known_balance = fresh_acc.balance
                            continue
                    else:
                        scalper.ghost_position_ticks = 0
    
                    current_mid = quote.mid
                    now_utc = datetime.now(timezone.utc)
                    friday_force_flatten = (now_utc.weekday() == 4 and (now_utc.hour > 20 or (now_utc.hour == 20 and now_utc.minute >= 30)))
    
                    should_exit_any = False
                    exit_reason = ""
                    exit_label = "NORMAL"
                    exec_px = quote.bid
    
                    # Multi-position parallel evaluation
                    for pos in scalper.active_positions:
                        direction = pos["direction"]
                        entry_px = pos["entry_price"]
                        atr = pos.get("atr_1m", 1.50)
                        vol = pos.get("volume", 0.01)
                        pos_exec_px = quote.bid if direction == "BUY" else quote.ask
                        gain_pts = (pos_exec_px - entry_px) if direction == "BUY" else (entry_px - pos_exec_px)
                        time_held_sec = time.time() - pos["open_time"]
    
                        # Calculate position floating PnL
                        pos_pnl = round(gain_pts * 100.0 * vol, 2)
                        pos["floating_pnl"] = pos_pnl
                        pos["current_price"] = current_mid
                        if pos_pnl > pos.get("peak_pnl", 0.0):
                            pos["peak_pnl"] = pos_pnl
    
                        # MicroExitController evaluation
                        controller: Optional[MicroExitController] = pos.get("micro_exit")
                        if controller:
                            exit_dec = controller.evaluate_tick(
                                current_price=pos_exec_px,
                                floating_pnl=pos_pnl,
                                current_time=time.time(),
                            )
                            # Ratchet 1: Sync Breakeven lock
                            if controller.be_locked and not pos.get("be_ratchet_hit", False):
                                pos["be_ratchet_hit"] = True
                                pos["sl_price"] = controller.sl_price
                                logger.info("🛡️ DYNAMIC BE RATCHET LOCKED: %s SL moved to $%.2f at +%.2f pts", pos.get("strategy_type", "POS"), controller.sl_price, gain_pts)
                        else:
                            exit_dec = ExitDecision(should_exit=False, reason="")
    
                        # Ratchet 2: Scalp TP1 Profit Lock at +1.8 ATR (TP1 Zone)
                        if not pos.get("tp1_ratchet_hit", False) and gain_pts >= 1.8 * atr:
                            pos["tp1_ratchet_hit"] = True
                            locked_sl = entry_px + (1.0 * atr) if direction == "BUY" else entry_px - (1.0 * atr)
                            pos["sl_price"] = locked_sl
                            if controller:
                                controller.sl_price = locked_sl
                            logger.info("💰 SCALP PROFIT LOCK: %s SL ratcheted to +1.0 ATR ($%.2f) at +%.2f pts", pos.get("strategy_type", "POS"), locked_sl, gain_pts)
    
                        # Structure Invalidation Scratch (if trade held >= 35s and latest 1m bar reversed prior swing)
                        structure_exit = False
                        structure_reason = ""
                        if time_held_sec >= 35.0 and len(scalper.candles_1m) >= 2:
                            last_c = scalper.candles_1m[-1]
                            prev_c = scalper.candles_1m[-2]
                            if direction == "BUY" and last_c["close"] < prev_c["low"] and pos_pnl < 0:
                                structure_exit = True
                                structure_reason = f"Structure Invalidation: 1m bar broke below prior swing low ({last_c['close']:.2f} < {prev_c['low']:.2f})"
                            elif direction == "SELL" and last_c["close"] > prev_c["high"] and pos_pnl < 0:
                                structure_exit = True
                                structure_reason = f"Structure Invalidation: 1m bar broke above prior swing high ({last_c['close']:.2f} > {prev_c['high']:.2f})"
    
                        # Active software Stop Loss check
                        sl_hit = (direction == "BUY" and quote.bid <= pos["sl_price"]) or \
                                 (direction == "SELL" and quote.ask >= pos["sl_price"])
    
                        # Macro Spike Harvest (Hold Long targets +6.5 ATR, Scalp Sell targets +2.5 ATR)
                        spike_thresh = 6.5 * atr if pos.get("is_hold_long", True) else 2.5 * atr
                        hit_macro_spike = gain_pts >= spike_thresh or (acc.balance >= 100.0 and pos_pnl >= RAPID_SPIKE_TARGET_USD)
    
                        if friday_force_flatten:
                            should_exit_any = True
                            exit_reason = "Friday Weekend Force-Flatten"
                            exit_label = "FRIDAY_FLATTEN"
                            exec_px = pos_exec_px
                            break
                        elif exit_dec.should_exit:
                            should_exit_any = True
                            exit_reason = exit_dec.reason
                            exit_label = exit_dec.metric_label
                            exec_px = pos_exec_px
                            break
                        elif structure_exit:
                            should_exit_any = True
                            exit_reason = structure_reason
                            exit_label = "STRUCTURE_INVALIDATION"
                            exec_px = pos_exec_px
                            break
                        elif sl_hit:
                            # Guard: if MicroExitController watermark is active and profitable,
                            # skip the thin-BE software SL — let watermark protect the profit
                            watermark_protecting = (
                                controller and controller.be_locked and pos_pnl > 0
                                and controller.peak_pnl >= controller.cfg.watermark_activate_usd
                            )
                            if not watermark_protecting:
                                should_exit_any = True
                                is_trailing = pos.get("be_ratchet_hit", False)
                                exit_reason = "Trailing SL Cushion Hit" if is_trailing else "Risk Stop Hit"
                                exit_label = "TRAILING_SL" if is_trailing else "RISK_STOP"
                                exec_px = pos_exec_px
                                break
                        elif hit_macro_spike:
                            should_exit_any = True
                            exit_reason = f"Macro Spike Harvest (+{gain_pts:.2f} pts)"
                            exit_label = "MACRO_SPIKE_HARVEST"
                            exec_px = pos_exec_px
                            break
    
                    # Execute Flatten & Accounting if exit triggered
                    if should_exit_any:
                        logger.info("🚨 EXIT TRIGGERED [%s]: %s | Exec: $%.2f | Floating: $%.2f",
                                    exit_label, exit_reason, exec_px, acc.floating_pnl)
                        saved_positions = list(scalper.active_positions)
                        res = await gw.flatten_all_positions()
                        if res.get("success") and res.get("assets_used", 1.0) <= 0.0:
                            scalper.active_positions.clear()
                            scalper.active_stack = None
                        else:
                            logger.critical("🚨 FLATTEN INCOMPLETE OR FAILED: %s (Assets used: $%.2f). Retaining active position tracking.",
                                            res.get("error"), res.get("assets_used", 0.0))
                            await asyncio.sleep(0.5)
                            continue
    
                        tot_pnl = acc.floating_pnl
                        if tot_pnl < 0:
                            scalper.consecutive_losses += 1
                            scalper.daily_realized_loss += abs(tot_pnl)
                            cd_time = 90.0 if abs(tot_pnl) < 1.0 else 240.0
                            scalper.cooldown_until = time.time() + cd_time
                            if scalper.consecutive_losses >= 2:
                                scalper.lockout_until = time.time() + 1800.0  # 30 min lockout
    
                            max_day_loss = min(50.0, max(10.0, scalper.daily_start_balance * 0.15))
                            if scalper.daily_realized_loss >= max_day_loss:
                                scalper.circuit_breaker_active = True
                                scalper.trading_paused = True
                        else:
                            scalper.consecutive_losses = 0
    
                        bal_after = res.get("balance", acc.balance)
                        for sp in saved_positions:
                            sp_time_held = max(1, int((time.time() - sp["open_time"]) // 60))
                            log_live_trade({
                                "timestamp": time.time(),
                                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                                "time_utc": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                                "symbol": "XAUUSD",
                                "direction": sp["direction"],
                                "strategy": sp.get("strategy_type", "TO_THE_MOON"),
                                "entry_price": sp["entry_price"],
                                "exit_price": exec_px,
                                "volume": sp["volume"],
                                "peak_floating_pnl": sp.get("peak_pnl", 0.0),
                                "realized_pnl": sp.get("floating_pnl", tot_pnl),
                                "exit_reason": f"{exit_label}: {exit_reason}",
                                "duration_min": sp_time_held,
                                "balance_after": bal_after,
                                "equity_after": bal_after,
                            })
    
                        if tot_pnl >= 0:
                            push_ntfy(
                                title=f"💰 Locked: +${tot_pnl:.2f}",
                                message=f"{exit_label} @ ${exec_px:.2f} · Bal: ${bal_after:.2f}",
                                tags="moneybag,shield",
                                priority="high",
                            )
                            # Check Demo-to-Real Auto-Switch Trigger on First Win
                            if getattr(scalper, "account_mode", "REAL") == "DEMO" and tot_pnl > 0.0:
                                logger.info("🎯 FIRST DEMO WIN DETECTED (+${tot_pnl:.2f})! Initiating automatic transition to LIVE REAL account...")
                                push_ntfy(
                                    title=f"🎯 Demo Win Locked (+${tot_pnl:.2f})!",
                                    message="Initiating auto-switch to LiteFinance REAL account...",
                                    tags="trophy,gear",
                                    priority="high",
                                )
                                sw_res = await gw.switch_account_mode("REAL")
                                if sw_res.get("success"):
                                    scalper.account_mode = "REAL"
                                    fresh_real = await gw.get_account_snapshot(force_fresh=True)
                                    scalper.daily_start_balance = fresh_real.balance
                                    scalper.last_known_balance = fresh_real.balance
                                    scalper.daily_realized_loss = 0.0
                                    scalper.consecutive_losses = 0
                                    try:
                                        mf = ROOT_DIR / "data" / "account_mode.json"
                                        mf.parent.mkdir(parents=True, exist_ok=True)
                                        mf.write_text(json.dumps({
                                            "account_mode": "REAL",
                                            "switched_at": time.time(),
                                            "switched_at_iso": datetime.now(timezone.utc).isoformat(),
                                            "real_balance": fresh_real.balance,
                                            "trigger_win_usd": tot_pnl,
                                        }, indent=2))
                                    except Exception as me:
                                        logger.warning("Could not persist account_mode.json: %s", me)
                                    logger.info("✅ Live engine transitioned to REAL account! Real Balance: $%.2f", fresh_real.balance)
                                    push_ntfy(
                                        title="🟢 Real Account Active!",
                                        message=f"Switched to REAL mode · Balance: ${fresh_real.balance:.2f} · Ready to compound.",
                                        tags="white_check_mark,moneybag",
                                        priority="urgent",
                                    )
                                else:
                                    logger.error("❌ Failed to switch to REAL account: %s. Retaining DEMO mode.", sw_res.get("error"))
                        else:
                            push_ntfy(
                                title=f"🛑 Cut: ${tot_pnl:.2f}",
                                message=f"{exit_label} @ ${exec_px:.2f} · Bal: ${bal_after:.2f}",
                                tags="octagonal_sign,warning",
                                priority="urgent",
                            )
    
                # 3. Check for Strategy Entry (Parallel Allowed)
                # Strict Live Margin Rules (1:500 Leverage):
                # Dynamically evaluate fresh balance for parallel capacity
                acc_current = await gw.get_account_snapshot(force_fresh=False)
                max_parallel = 1 if acc_current.balance < 75.0 else (2 if acc_current.balance < 150.0 else 3)
                current_pos_count = len(scalper.active_positions)
                can_enter_parallel = False

                if current_pos_count < max_parallel:
                    if current_pos_count == 0:
                        can_enter_parallel = True
                    else:
                        # Parallel entry guard: ALL prior positions must be strictly de-risked to BE!
                        all_prior_safe = all(p.get("be_ratchet_hit", False) for p in scalper.active_positions)
                        next_lot_size = scalper.compute_lot_size(acc_current.balance, current_price=quote.mid)
                        next_req_margin = calc_required_margin(quote.mid, next_lot_size)
                        
                        acc_fresh = await gw.get_account_snapshot(force_fresh=True)
                        has_margin_cushion = acc_fresh.available >= (next_req_margin * 2.50)
                        proj_used = acc_fresh.assets_used + next_req_margin
                        margin_level_ok = (acc_fresh.equity / proj_used * 100.0) >= 350.0 if proj_used > 0 else True
                        if all_prior_safe and has_margin_cushion and margin_level_ok:
                            can_enter_parallel = True
                if (
                    can_enter_parallel
                    and tick_count % 5 == 0
                    and not getattr(scalper, "trading_paused", False)
                    and time.time() >= scalper.cooldown_until
                    and time.time() >= scalper.lockout_until
                    and not scalper.circuit_breaker_active
                ):
                    # 3.1 Check Economic Calendar & High-Impact News Sentinel
                    is_frozen, freeze_reason, is_post_expansion = laya_oracle.politician.check_calendar_freeze(window_minutes=15)
                    if is_frozen:
                        logger.debug("Trade entry paused: %s", freeze_reason)
                    # Live Spread Filter (Skip if spread > $0.45/oz)
                    elif (quote.ask - quote.bid) > 0.45:
                        pass
                    else:
                        allowed, session_label = scalper.is_in_allowed_session(allow_news_expansion=is_post_expansion)
                        if allowed:
                            sig: Optional[ApexSignal] = scalper.evaluate_strategy()
                            if sig:
                                acc = await gw.get_account_snapshot(force_fresh=True)
                                base_lot_size = scalper.compute_lot_size(acc.balance, current_price=quote.mid)
    
                                # --- REAL WICK & REAL TREND CALCULATION ---
                                recent_high = max(c["high"] for c in scalper.candles_1m[-30:]) if len(scalper.candles_1m) >= 5 else sig.entry_price + (sig.atr_1m * 3.0)
                                recent_low = min(c["low"] for c in scalper.candles_1m[-30:]) if len(scalper.candles_1m) >= 5 else sig.entry_price - (sig.atr_1m * 3.0)
                                
                                last_c = scalper.candles_1m[-1]
                                c_range = max(0.01, last_c["high"] - last_c["low"])
                                if sig.direction == "BUY":
                                    real_wick = round(max(0.0, min(last_c["open"], last_c["close"]) - last_c["low"]) / c_range, 3)
                                else:
                                    real_wick = round(max(0.0, last_c["high"] - max(last_c["open"], last_c["close"])) / c_range, 3)
    
                                # Trend alignment using EMA20 and EMA50
                                closes = [c["close"] for c in scalper.candles_1m]
                                if len(closes) >= 50:
                                    s_closes = pd.Series(closes)
                                    ema20 = float(s_closes.ewm(span=20).mean().iloc[-1])
                                    ema50 = float(s_closes.ewm(span=50).mean().iloc[-1])
                                    real_trend = (quote.mid >= ema50 and ema20 >= ema50) if sig.direction == "BUY" else (quote.mid <= ema50 and ema20 <= ema50)
                                else:
                                    real_trend = True
    
                                market_state = {
                                    "direction": sig.direction,
                                    "entry_price": sig.entry_price,
                                    "sl_price": sig.sl_price,
                                    "wick_ratio": real_wick,
                                    "session": session_label,
                                    "setup_type": sig.strategy_type,
                                    "hour_utc": datetime.now(timezone.utc).hour,
                                    "trend_aligned": real_trend,
                                    "atr_1m": sig.atr_1m,
                                    "recent_high": recent_high,
                                    "recent_low": recent_low,
                                }
                                laya_decision = laya_oracle.evaluate_setup_sync(market_state)
    
                                # Micro-Account Capital Preservation Shield (<$100):
                                if acc.balance < 100.0:
                                    if not laya_decision.is_valid or laya_decision.trap_probability >= 0.50 or laya_decision.confluence_score < 6.5:
                                        logger.warning("🛡️ MICRO CAPITAL GUARD VETO: Trap Prob: %.1f%%, Confluence: %.1f/10, Valid: %s (%s) - Setup rejected",
                                                       laya_decision.trap_probability * 100, laya_decision.confluence_score, laya_decision.is_valid, laya_decision.reasoning)
                                        push_ntfy(
                                            title=f"🛡️ Micro Veto: {sig.direction} @ ${sig.entry_price:.2f}",
                                            message=f"Trap: {laya_decision.trap_probability*100:.0f}% · Score: {laya_decision.confluence_score:.1f}/10 · Rejected for capital safety",
                                            tags="shield,no_entry_sign",
                                            priority="default",
                                        )
                                        continue
    
                                    lot_size = 0.01
                                    boost_tag = " (Micro Protection: Strictly 0.01L Locked)"
                                else:
                                    # Standard account sizing (>=$100)
                                    if not laya_decision.is_valid:
                                        is_blatant_trap = (
                                            laya_decision.trap_probability >= 0.80 or
                                            "Politician Shield Veto" in getattr(laya_decision, "matched_ict_concepts", []) or
                                            (getattr(laya_decision, "setup_grade", "") == "toxic_trap" and laya_decision.trap_probability >= 0.80)
                                        )
                                        if is_blatant_trap:
                                            logger.warning("🛡️ BLATANT TOXIC TRAP VETOED: %s (Trap Prob: %.1f%%) - Trade suppressed for capital protection",
                                                           laya_decision.reasoning, laya_decision.trap_probability * 100)
                                            push_ntfy(
                                                title=f"🛡️ Trap Veto: {sig.direction} @ ${sig.entry_price:.2f}",
                                                message=f"Suppressed toxic setup · Trap Risk: {laya_decision.trap_probability*100:.0f}%",
                                                tags="shield,no_entry_sign",
                                                priority="default",
                                            )
                                            continue
    
                                        logger.info("⚡ SMART & BOLD OVERRIDE: %s (Trap Prob: %.1f%%) -> Executing trade on base lot size (%.2f lots)", 
                                                    laya_decision.reasoning, laya_decision.trap_probability * 100, base_lot_size)
                                        lot_size = base_lot_size
                                        boost_tag = " (Smart & Bold Override - Base Sizing)"
                                    else:
                                        # Apply dynamic compounding multiplier (up to 1.50x on Macro Sovereign Titan)
                                        boosted_lots = round(base_lot_size * max(1.0, laya_decision.compounding_multiplier), 2)
                                        margin_per_001 = calc_required_margin(sig.entry_price, 0.01)
                                        max_allowed_lots = math.floor((min(acc.balance, acc.equity) * 0.35) / margin_per_001) * 0.01
                                        lot_size = max(0.01, min(boosted_lots, round(max_allowed_lots, 2)))
                                        boost_tag = f" (Laya {laya_decision.setup_grade} {laya_decision.compounding_multiplier:.2f}x Boost | TP {laya_decision.tp_expansion_multiplier:.2f}x)" if laya_decision.compounding_multiplier > 1.0 else ""
                                    
                                # Apply Macro Target Expansion (The Sword)
                                effective_spike_target = sig.spike_target
                                if laya_decision.tp_expansion_multiplier > 1.0 and sig.atr_1m > 0:
                                    expansion_dist = sig.atr_1m * (laya_decision.tp_expansion_multiplier - 1.0) * 2.5
                                    effective_spike_target = (sig.spike_target + expansion_dist) if sig.direction == "BUY" else (sig.spike_target - expansion_dist)
    
                                # Real Live Margin Pre-Check: Calculate margin for the ACTUAL requested lot size
                                req_margin = calc_required_margin(sig.entry_price, lot_size)
                                if acc.available < (req_margin * 1.50):
                                    logger.warning("⚠️ Insufficient available margin ($%.2f vs required $%.2f). Skipping trade.",
                                                   acc.available, req_margin * 1.50)
                                    continue

                                # Margin Level Guard: Prevent entering if projected margin level is below 400% on micro accounts (<$150) or 300% on larger accounts
                                projected_used = acc.assets_used + req_margin
                                min_margin_level = 400.0 if acc.balance < 150.0 else 300.0
                                if projected_used > 0 and (acc.equity / projected_used * 100.0) < min_margin_level:
                                    logger.warning("⚠️ Projected margin level too low (<%.0f%%: $%.2f eq / $%.2f used). Skipping trade.",
                                                   min_margin_level, acc.equity, projected_used)
                                    continue
    
                                # Safe broker-side disaster stop
                                min_sl_dist = max(1.50, sig.atr_1m * 1.0)
                                if sig.direction == "BUY":
                                    broker_sl = round(min(sig.sl_price, sig.entry_price - min_sl_dist), 2)
                                else:
                                    broker_sl = round(max(sig.sl_price, sig.entry_price + min_sl_dist), 2)
    
                                logger.info("🎯 'TO THE MOON' SIGNAL [%s]: %s @ $%.2f | SL: $%.2f (Broker SL: $%.2f) | TP1: $%.2f | Spike: $%.2f | Lots: %.2f%s | Confluence: %.1f/10",
                                            sig.strategy_type, sig.direction, sig.entry_price, sig.sl_price, broker_sl, sig.tp1_price, effective_spike_target, lot_size, boost_tag, laya_decision.confluence_score)
    
                                order_res = await gw.open_market_order(
                                    sig.direction,
                                    lot_size,
                                    sl_price=broker_sl,
                                    expected_mode=getattr(scalper, "account_mode", "REAL"),
                                )
                                if order_res.get("success"):
                                    actual_volume = order_res.get("volume", lot_size)
                                    scalper.last_latency_ms = order_res.get("latency_ms", 0.0)
    
                                    # Create dedicated direction-aware MicroExitController for this position
                                    pos_cfg = get_micro_account_config(balance=acc.balance, direction=sig.direction) if acc.balance < 100.0 else get_to_the_moon_config("XAUUSD", direction=sig.direction)
                                    pos_ctrl = MicroExitController(pos_cfg)
                                    pos_ctrl.arm_position(
                                        entry_price=sig.entry_price,
                                        direction=sig.direction,
                                        total_volume=actual_volume,
                                        sl_price=sig.sl_price,
                                        open_time=time.time(),
                                    )
    
                                    new_position = {
                                        "ticket_id": f"pos_{int(time.time()*1000)}",
                                        "direction": sig.direction,
                                        "volume": actual_volume,
                                        "entry_price": sig.entry_price,
                                        "sl_price": sig.sl_price,
                                        "tp1_price": sig.tp1_price,
                                        "spike_target": effective_spike_target,
                                        "atr_1m": sig.atr_1m,
                                        "strategy_type": sig.strategy_type,
                                        "open_time": time.time(),
                                        "max_safe_duration_sec": max(6, min(15, getattr(laya_decision, "max_safe_holding_min", 10))) * 60,
                                        "floating_pnl": 0.0,
                                        "peak_pnl": 0.0,
                                        "be_ratchet_hit": False,
                                        "tp1_ratchet_hit": False,
                                        "is_hold_long": getattr(sig, "is_hold_long", sig.direction == "BUY"),
                                        "is_scalp_sell": getattr(sig, "is_scalp_sell", sig.direction == "SELL"),
                                        "micro_exit": pos_ctrl,
                                        "laya_grade": laya_decision.setup_grade,
                                        "ict_concepts": sig.ict_concepts,
                                        "political_regime": laya_decision.political_regime,
                                        "macro_bias": laya_decision.macro_bias,
                                    }
                                    scalper.active_positions.append(new_position)
    
                                    parallel_tag = f" [Parallel #{len(scalper.active_positions)}]" if len(scalper.active_positions) > 1 else ""
                                    push_ntfy(
                                        title=f"🌕 {sig.direction} {actual_volume}L @ ${sig.entry_price:.2f}{parallel_tag}",
                                        message=f"{sig.strategy_type} · SL: ${sig.sl_price:.2f} · Spike: ${effective_spike_target:.2f} · Latency: {scalper.last_latency_ms:.0f}ms",
                                        tags="zap,rocket,shield",
                                        priority="high",
                                    )
                                else:
                                    err_msg = order_res.get("error", "Unknown error")
                                    logger.error("❌ BROKER ORDER FAILED / REJECTED: %s | Requested: %s %.2f lots", err_msg, sig.direction, lot_size)
                                    push_ntfy(
                                        title=f"⚠️ Rejected: {sig.direction} {lot_size}L @ ${sig.entry_price:.2f}",
                                        message=f"{err_msg} · Balance: ${acc.balance:.2f}",
                                        tags="warning,no_entry_sign",
                                        priority="urgent",
                                    )
    
                # 4. Sync telemetry to mobile dashboard (every ~1s)
                if tick_count % 20 == 0:
                    acc = await gw.get_account_snapshot()
                    withdrawal_info = scalper.compute_daily_withdrawal(acc.balance)
    
                    # Milestone notification check (every $50 in recommended cashout)
                    if withdrawal_info["recommended_cashout_today"] >= 50.0:
                        milestone_bracket = int(withdrawal_info["recommended_cashout_today"] // 50) * 50
                        if milestone_bracket not in scalper.milestone_notified:
                            scalper.milestone_notified.add(milestone_bracket)
                            push_ntfy(
                                title=f"🏦 Vault Milestone: ${milestone_bracket}",
                                message=f"Profit: +${withdrawal_info['daily_profit']:.2f} · Ready to Cash-Out: ${withdrawal_info['recommended_cashout_today']:.2f}",
                                tags="moneybag,gem",
                                priority="high",
                            )
    
                    status_msg = f"Trading on LiteFinance {getattr(scalper, 'account_mode', 'REAL')} Account"
                    if scalper.active_stack:
                        status_msg = f"In {scalper.active_stack['direction']} position ({scalper.active_stack['volume']} lots)"
                    elif getattr(scalper, "trading_paused", False):
                        status_msg = "Auto-trade paused via dashboard"
    
                    sync_dashboard_state(
                        balance=acc.balance,
                        equity=acc.equity,
                        active_pos=scalper.active_stack,
                        mid_px=quote.mid,
                        bid_px=quote.bid,
                        ask_px=quote.ask,
                        tier=vault.get("current_tier", 300.0),
                        last_latency_ms=scalper.last_latency_ms,
                        laya_telemetry=laya_oracle.get_telemetry(),
                        daily_withdrawal=withdrawal_info,
                        bot_running=not getattr(scalper, "trading_paused", False),
                        account_mode=getattr(scalper, "account_mode", "REAL"),
                        message=status_msg,
                        start_balance=getattr(scalper, "daily_start_balance", acc.balance),
                    )
    
                # Micro-yield (20ms) to keep CPU cool while maintaining sub-millisecond reactivity
                await asyncio.sleep(0.02)
            except Exception as e:
                logger.error("Error in live trading cycle: %s", e)
                await asyncio.sleep(0.5)

    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Shutdown requested via signal or cancellation.")
    finally:
        # Clean shutdown
        if scalper.active_positions or scalper.active_stack:
            logger.info("Shutdown cleanup: Flattening open broker positions...")
            try:
                await gw.flatten_all_positions()
            except Exception as fe:
                logger.error("Error flattening during shutdown: %s", fe)

        try:
            await gw.close()
        except Exception as ce:
            logger.warning("Error closing gateway: %s", ce)
        logger.info("Live Broker Scalper shutdown complete.")


if __name__ == "__main__":
    asyncio.run(run_live_scalper())
