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
    """Dispatches push notification asynchronously without blocking the event loop."""
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, send_alert, title, message, priority)
    except RuntimeError:
        try:
            send_alert(title=title, message=message, priority=priority)
        except Exception:
            pass
    except Exception as e:
        logger.debug("push_ntfy dispatch failed: %s", e)


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
) -> None:
    """Syncs live broker telemetry to HFT dashboard JSON file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "state").mkdir(parents=True, exist_ok=True)

    payload = {
        "engine": "XAUUSD Stratton Oakmont Broker LIVE Engine",
        "status": "ACTIVE" if bot_running else "PAUSED",
        "bot_running": bot_running,
        "fsm_state": "IN_POSITION" if active_pos else ("PAUSED" if not bot_running else "SCANNING"),
        "mode": "BROKER LIVE (LiteFinance Real Account)",
        "symbol": "XAUUSD",
        "balance": round(balance, 2),
        "equity": round(equity, 2),
        "realized_pnl": round(balance - 59.87, 2),
        "pnl_pct": round((equity - 59.87) / 59.87 * 100.0, 2) if 59.87 > 0 else 0.0,
        "current_tier": tier,
        "current_price": mid_px,
        "mid_price": mid_px,
        "best_bid": bid_px,
        "best_ask": ask_px,
        "spread_bps": round(((ask_px - bid_px) / mid_px * 10000.0), 2) if mid_px > 0 else 0.0,
        "latency_ms": round(last_latency_ms, 2),
        "position": active_pos,
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
    """

    def __init__(self, gateway: LiteFinanceGateway, symbol: str = "XAUUSD"):
        self.gw = gateway
        self.symbol = symbol
        self.apex = ApexTrinityStrategy(min_candles_warmup=30)
        self.active_stack: Optional[Dict[str, Any]] = None
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

    def get_current_session_label(self) -> str:
        """Returns ICT session killzone based on current UTC hour."""
        now = datetime.now(timezone.utc)
        if now.weekday() == 4 and now.hour >= 18:
            return "Friday Evening Pre-Weekend Curfew (Trading Blocked)"
        if now.weekday() == 5 or (now.weekday() == 6 and now.hour < 22):
            return "Weekend Market Closed (Trading Blocked)"

        hr = now.hour
        if 0 <= hr < 6:
            return "Asian Range Accumulation"
        elif 6 <= hr < 11:
            return "London Open Judas / Silver Bullet"
        elif 11 <= hr < 17:
            return "New York AM Silver Bullet Expansion"
        else:
            return "London Close / Asian Pre-Market"

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
        if balance < 35.0:
            lots = 0.01
        elif balance < 75.0:
            lots = 0.03  # Active Tier for $59.87 ($25.62 margin)
        elif balance < 150.0:
            lots = 0.05
        elif balance < 300.0:
            lots = 0.10
        elif balance < 600.0:
            lots = 0.20
        elif balance < 1200.0:
            lots = 0.40
        elif balance < 2500.0:
            lots = 0.80
        elif balance < 5000.0:
            lots = 1.60
        else:
            lots = min(10.00, round(balance / 3000.0, 2))

        # Absolute Margin Safety Guard (1:500 leverage):
        # Never allow base lot size to exceed 55% of total balance in required margin!
        margin_per_001 = max(8.0, (current_price * 100.0 * 0.01) / 500.0)
        if balance < (margin_per_001 * 1.10):
            return 0.0  # Balance insufficient to safely open even 0.01 lots
        max_safe_lots = max(0.01, math.floor((balance * 0.55) / margin_per_001) * 0.01)
        return max(0.01, min(lots, round(max_safe_lots, 2)))

    def is_in_killzone(self, hour_utc: Optional[int] = None) -> bool:
        hr = hour_utc if hour_utc is not None else datetime.now(timezone.utc).hour
        now = datetime.now(timezone.utc)
        if now.weekday() == 4 and hr >= 18:
            return False
        if now.weekday() == 5 or (now.weekday() == 6 and hr < 22):
            return False
        return (6 <= hr < 11) or (11 <= hr < 17)

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
                if len(self.candles_1m) > 200:
                    self.candles_1m = self.candles_1m[-200:]
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
        Evaluates "To The Moon" (Apex Sovereign Trinity Matrix):
        1. 5m S&R Breakout + 1m Retest + Pin Wick
        2. Multi-Session Silver Bullet FVG CE Tap (London 07-08 UTC & NY 14-15 UTC)
        3. London Turtle Soup Asian Liquidity Sweep (06-09 UTC)
        """
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

    push_ntfy(
        title="🟢 Stratton Oakmont Broker LIVE Armed (REAL ACCOUNT)",
        message=f"Connected to LiteFinance Real Account. Real Balance: ${acc_snap.balance:.2f} (1:1000 Leverage).\nLaya System 1 & Politician Brain Active.\nSovereign Compounding Ladder Armed (0.05 Lots Baseline).\nDynamic Peak Bag Protection Armed.\nTerminal: https://82-115-21-155.sslip.io/",
        tags="rocket,white_check_mark",
    )

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: stop_event.set())

    logger.info("Entering live trading loop with Laya System 1 Surveillance...")
    tick_count = 0
    last_quote_time = time.time()

    while not stop_event.is_set():
        try:
            # 0. Check external web command (e.g. Emergency Flatten button)
            cmd_file = DATA_DIR / "command.json"
            if cmd_file.exists():
                try:
                    with open(cmd_file, "r") as f:
                        cmd = json.load(f)
                    cmd_file.unlink(missing_ok=True)
                    action = cmd.get("action")
                    if action == "FLATTEN":
                        logger.warning("🚨 EMERGENCY FLATTEN SIGNAL RECEIVED FROM DASHBOARD!")
                        res = await gw.flatten_all_positions()
                        push_ntfy(
                            title="🛑 Manual Emergency Flatten",
                            message=f"Closed all positions via Dashboard button. Balance: ${res.get('balance', 0):.2f}",
                            tags="warning,hand",
                            priority="urgent",
                        )
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
                except Exception as ce:
                    logger.warning("Error processing dashboard command: %s", ce)

            # 1. Fetch live quote via reactive event stream or RAM lookup
            quote = await gw.wait_for_quote(timeout=0.10)
            if not quote:
                quote = await gw.get_live_quote()
            if not quote:
                if (time.time() - last_quote_time) > 20.0 and not getattr(gw, "_reconnecting", False):
                    logger.warning("⚠️ Market quote stream stalled (>20s). Triggering self-healing gateway reconnect...")
                    asyncio.create_task(gw.reconnect())
                    last_quote_time = time.time()
                await asyncio.sleep(0.05)
                continue

            last_quote_time = time.time()
            tick_count += 1
            scalper.update_tick(quote)

            # 2. Check position state if in trade
            if scalper.active_stack:
                acc = await gw.get_account_snapshot(force_fresh=True)
                
                # 2.1 Ghost Position Watchdog (if broker closed order or hit SL externally)
                if acc.assets_used <= 0.0:
                    scalper.ghost_position_ticks += 1
                    if scalper.ghost_position_ticks >= 8:
                        logger.warning("👻 GHOST POSITION DETECTED: Broker reports 0 assets used for 8 ticks. Clearing active stack.")
                        scalper.active_stack = None
                        scalper.ghost_position_ticks = 0
                        continue
                else:
                    scalper.ghost_position_ticks = 0

                floating_pnl = acc.floating_pnl
                current_mid = quote.mid
                scalper.active_stack["floating_pnl"] = floating_pnl
                scalper.active_stack["current_price"] = current_mid
                if floating_pnl > scalper.active_stack.get("peak_pnl", 0.0):
                    scalper.active_stack["peak_pnl"] = floating_pnl

                entry_px = scalper.active_stack["entry_price"]
                direction = scalper.active_stack["direction"]
                atr = scalper.active_stack.get("atr_1m", 1.50)

                # Distance moved in favorable direction using physical executable prices (Bid for Buy close, Ask for Sell close)
                exec_px = quote.bid if direction == "BUY" else quote.ask
                gain_pts = (exec_px - entry_px) if direction == "BUY" else (entry_px - exec_px)

                # 2.2 High-Velocity Scalping Stagnation & Holding Duration
                time_held_sec = time.time() - scalper.active_stack["open_time"]
                max_duration_sec = scalper.active_stack.get("max_safe_duration_sec", 600.0) # ~10 mins scalp default
                stagnation_exit = False
                stagnation_reason = ""
                if time_held_sec >= max_duration_sec:
                    if floating_pnl >= 2.0:
                        stagnation_exit = True
                        stagnation_reason = f"Scalp Duration Harvest: Locked +${floating_pnl:.2f} after {int(time_held_sec//60)}m (High-Velocity Edge Captured)"
                    elif time_held_sec >= (max_duration_sec * 1.25):
                        stagnation_exit = True
                        stagnation_reason = f"Scalp Stagnation Scratch: Exited at ${floating_pnl:.2f} after {int(time_held_sec//60)}m (Protect Capital from Reversal)"

                # --- "To The Moon" Sovereign Trailing Ratchets & Bag Protection ---

                # EDGE FIX 1: Dynamic Peak Watermark Bag Protection
                # If floating profit reached >= $25 (or >= 5% of balance) and drops by >= 18% of peak -> LOCK CASH & EXIT!
                peak_pnl = scalper.active_stack.get("peak_pnl", 0.0)
                min_peak_threshold = max(25.0, 0.05 * acc.balance)
                watermark_exit = False
                if peak_pnl >= min_peak_threshold:
                    pullback_usd = peak_pnl - floating_pnl
                    pullback_pct = pullback_usd / peak_pnl
                    if pullback_pct >= 0.18:
                        watermark_exit = True

                # Ratchet 1: Accelerated Breakeven Lock at +0.75 ATR (Fast Risk-Free Cushion)
                if not scalper.active_stack.get("be_ratchet_hit", False) and gain_pts >= 0.75 * atr:
                    scalper.active_stack["be_ratchet_hit"] = True
                    new_sl = entry_px + 0.20 if direction == "BUY" else entry_px - 0.20
                    scalper.active_stack["sl_price"] = new_sl
                    logger.info("🛡️ 'TO THE MOON' BE RATCHET LOCKED: SL moved to BE+0.20 ($%.2f) at +%.2f pts", new_sl, gain_pts)

                    # Multi-Order Momentum Pyramiding: Stack an additional runner when position is risk-free
                    if not scalper.active_stack.get("pyramided", False) and floating_pnl >= 3.0:
                        pyr_lot = 0.02
                        margin_per_001 = max(8.0, (current_mid * 100.0 * 0.01) / 500.0)
                        if (acc.available - (pyr_lot / 0.01 * margin_per_001)) >= 10.0:
                            logger.info("🚀 MOMENTUM PYRAMID TRIGGER: Stacking +%.2f lots on risk-free position (Floating: +$%.2f)", pyr_lot, floating_pnl)
                            pyr_res = await gw.open_market_order(direction, pyr_lot, sl_price=new_sl)
                            scalper.active_stack["pyramided"] = True  # Flag pyramid attempted regardless to prevent spam
                            if pyr_res.get("success"):
                                scalper.active_stack["volume"] = round(scalper.active_stack["volume"] + pyr_lot, 2)
                                push_ntfy(
                                    title=f"🚀 Multi-Order Pyramid Stacked: +{pyr_lot} Lots {direction}",
                                    message=f"Total Stack: {scalper.active_stack['volume']} Lots | Locked BE SL: ${new_sl:.2f}\nFloating PnL: +${floating_pnl:.2f} (Filling the Gap on Runner Expansion)",
                                    tags="rocket,fire",
                                    priority="high",
                                Shakespeare="default")

                # Ratchet 2: Fast Scalp Profit Lock at +1.8 ATR (TP1 Zone) -> Ratchet SL to +1.0 ATR
                if not scalper.active_stack.get("tp1_ratchet_hit", False) and gain_pts >= 1.8 * atr:
                    scalper.active_stack["tp1_ratchet_hit"] = True
                    locked_sl = entry_px + (1.0 * atr) if direction == "BUY" else entry_px - (1.0 * atr)
                    scalper.active_stack["sl_price"] = locked_sl
                    logger.info("💰 'TO THE MOON' SCALP PROFIT LOCK: SL ratcheted to +1.0 ATR ($%.2f) at +%.2f pts", locked_sl, gain_pts)

                # Check if executable price hit current active software Stop Loss
                sl_hit = (direction == "BUY" and quote.bid <= scalper.active_stack["sl_price"]) or \
                         (direction == "SELL" and quote.ask >= scalper.active_stack["sl_price"])

                # Hard single-trade loss ceiling (Strict 5% equity floor, maximum $35 on accounts under $1,000)
                tier_mult = max(1.0, acc.balance / 100.0)
                dynamic_risk_stop = max(MAX_RISK_STOP_USD, min(0.05 * acc.balance, 35.0 if acc.balance < 1000.0 else tier_mult * 25.0))
                dynamic_spike_target = max(RAPID_SPIKE_TARGET_USD, tier_mult * 50.0)

                # Check Friday 20:30 UTC force-flatten
                now_utc = datetime.now(timezone.utc)
                friday_force_flatten = (now_utc.weekday() == 4 and (now_utc.hour > 20 or (now_utc.hour == 20 and now_utc.minute >= 30)))

                # Check Macro Spike Harvest (+5.0 ATR or dynamic spike target)
                hit_macro_spike = gain_pts >= 5.0 * atr or floating_pnl >= dynamic_spike_target

                # Exit Handling
                if watermark_exit:
                    logger.info("💰 PEAK WATERMARK BAG PROTECTION: Locked +$%.2f (Peak was +$%.2f, -18%% pullback)", floating_pnl, peak_pnl)
                    saved_stack = dict(scalper.active_stack)
                    try:
                        res = await gw.flatten_all_positions()
                    finally:
                        scalper.active_stack = None

                    scalper.consecutive_losses = 0
                    log_live_trade({
                        "timestamp": time.time(),
                        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                        "time_utc": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                        "symbol": "XAUUSD",
                        "direction": direction,
                        "strategy": saved_stack.get("strategy_type", "TO_THE_MOON"),
                        "entry_price": entry_px,
                        "exit_price": current_mid,
                        "volume": saved_stack["volume"],
                        "peak_floating_pnl": peak_pnl,
                        "realized_pnl": floating_pnl,
                        "exit_reason": "BAG_PROTECTION_WATERMARK_LOCK",
                        "duration_min": max(1, int((time.time() - saved_stack["open_time"]) // 60)),
                        "balance_after": res.get("balance", acc.balance),
                        "equity_after": res.get("balance", acc.balance),
                    })
                    push_ntfy(
                        title=f"💰 Bag Protection Harvest (+${floating_pnl:.2f})",
                        message=f"Locked +${floating_pnl:.2f} (Peak was +${peak_pnl:.2f}). Never let a winner become a loss!\nNew Balance: ${res.get('balance', acc.balance):.2f}",
                        tags="moneybag,shield",
                        priority="high",
                    )

                elif stagnation_exit:
                    logger.info("⏳ %s: Mid: $%.2f | Floating PnL: $%.2f", stagnation_reason, current_mid, floating_pnl)
                    saved_stack = dict(scalper.active_stack)
                    try:
                        res = await gw.flatten_all_positions()
                    finally:
                        scalper.active_stack = None

                    if floating_pnl < 0:
                        scalper.consecutive_losses += 1
                        scalper.daily_realized_loss += abs(floating_pnl)
                        scalper.cooldown_until = time.time() + 180.0
                    else:
                        scalper.consecutive_losses = 0

                    log_live_trade({
                        "timestamp": time.time(),
                        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                        "time_utc": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                        "symbol": "XAUUSD",
                        "direction": direction,
                        "strategy": saved_stack.get("strategy_type", "TO_THE_MOON"),
                        "entry_price": entry_px,
                        "exit_price": current_mid,
                        "volume": saved_stack["volume"],
                        "peak_floating_pnl": peak_pnl,
                        "realized_pnl": floating_pnl,
                        "exit_reason": "STAGNATION_TIME_DECAY_EXIT",
                        "duration_min": max(1, int((time.time() - saved_stack["open_time"]) // 60)),
                        "balance_after": res.get("balance", acc.balance),
                        "equity_after": res.get("balance", acc.balance),
                    })
                    push_ntfy(
                        title=f"⏳ Stagnation Exit (${floating_pnl:+.2f})",
                        message=f"{stagnation_reason}\nClosed @ ${current_mid:.2f}. Balance: ${res.get('balance', acc.balance):.2f}",
                        tags="hourglass,shield" if floating_pnl >= 0 else "hourglass,warning",
                        priority="high" if floating_pnl >= 0 else "default",
                    )

                elif sl_hit or floating_pnl <= -dynamic_risk_stop or friday_force_flatten:
                    is_trailing = scalper.active_stack.get("be_ratchet_hit", False)
                    reason_label = "Friday Weekend Force-Flatten" if friday_force_flatten else ("Trailing Profit Lock" if (is_trailing and floating_pnl > 0) else ("Trailing BE Hit" if is_trailing else "Risk Stop Hit"))
                    logger.warning("🛑 %s: Mid: $%.2f, SL: $%.2f, Floating PnL: $%.2f", reason_label, current_mid, scalper.active_stack["sl_price"], floating_pnl)
                    saved_stack = dict(scalper.active_stack)
                    try:
                        res = await gw.flatten_all_positions()
                    finally:
                        scalper.active_stack = None

                    if floating_pnl < 0:
                        scalper.consecutive_losses += 1
                        scalper.daily_realized_loss += abs(floating_pnl)
                        scalper.cooldown_until = time.time() + 300.0  # 5 min cooldown
                        if scalper.consecutive_losses >= 2:
                            scalper.lockout_until = time.time() + 3600.0  # 60 min lockout

                        max_day_loss = min(50.0, max(20.0, scalper.daily_start_balance * 0.08))
                        if scalper.daily_realized_loss >= max_day_loss:
                            scalper.circuit_breaker_active = True
                            scalper.trading_paused = True
                    else:
                        scalper.consecutive_losses = 0

                    log_live_trade({
                        "timestamp": time.time(),
                        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                        "time_utc": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                        "symbol": "XAUUSD",
                        "direction": direction,
                        "strategy": saved_stack.get("strategy_type", "TO_THE_MOON"),
                        "entry_price": entry_px,
                        "exit_price": current_mid,
                        "volume": saved_stack["volume"],
                        "peak_floating_pnl": peak_pnl,
                        "realized_pnl": floating_pnl,
                        "exit_reason": reason_label,
                        "duration_min": max(1, int((time.time() - saved_stack["open_time"]) // 60)),
                        "balance_after": res.get("balance", acc.balance),
                        "equity_after": res.get("balance", acc.balance),
                    })

                    push_ntfy(
                        title=f"🛑 {reason_label} (${floating_pnl:+.2f})",
                        message=f"Strategy: {saved_stack.get('strategy_type')}\nClosed @ ${current_mid:.2f}. New Balance: ${res.get('balance', acc.balance):.2f}",
                        tags="warning,octagonal_sign" if floating_pnl < 0 else "moneybag,shield",
                        priority="urgent" if floating_pnl < 0 else "default",
                    )

                elif hit_macro_spike:
                    logger.info("🌕 'TO THE MOON' MACRO SPIKE HARVEST: Mid: $%.2f | Gain: +%.2f pts | Floating PnL: +$%.2f", current_mid, gain_pts, floating_pnl)
                    saved_stack = dict(scalper.active_stack)
                    try:
                        res = await gw.flatten_all_positions()
                    finally:
                        scalper.active_stack = None

                    scalper.consecutive_losses = 0
                    log_live_trade({
                        "timestamp": time.time(),
                        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                        "time_utc": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                        "symbol": "XAUUSD",
                        "direction": direction,
                        "strategy": saved_stack.get("strategy_type", "TO_THE_MOON"),
                        "entry_price": entry_px,
                        "exit_price": current_mid,
                        "volume": saved_stack["volume"],
                        "peak_floating_pnl": peak_pnl,
                        "realized_pnl": floating_pnl,
                        "exit_reason": "MACRO_SPIKE_HARVEST",
                        "duration_min": max(1, int((time.time() - saved_stack["open_time"]) // 60)),
                        "balance_after": res.get("balance", acc.balance),
                        "equity_after": res.get("balance", acc.balance),
                    })

                    push_ntfy(
                        title=f"🌕 Macro Spike Harvest (+${floating_pnl:+.2f})",
                        message=f"Strategy: {saved_stack.get('strategy_type')}\nGain: +{gain_pts:.2f} pts (${floating_pnl:+.2f})\nNew Balance: ${res.get('balance', acc.balance):.2f}",
                        tags="rocket,moneybag,trophy",
                        priority="high",
                    )

            # 3. Check for Strategy Entry if Flat (evaluated every ~250ms)
            elif (
                tick_count % 5 == 0
                and not getattr(scalper, "trading_paused", False)
                and time.time() >= scalper.cooldown_until
                and time.time() >= scalper.lockout_until
                and not scalper.circuit_breaker_active
            ):
                now_utc = datetime.now(timezone.utc)
                # Friday Curfew (No new trades after Friday 18:00 UTC)
                if now_utc.weekday() == 4 and now_utc.hour >= 18:
                    pass
                # Live Spread Filter (Skip if spread > $0.45/oz)
                elif (quote.ask - quote.bid) > 0.45:
                    pass
                else:
                    sig: Optional[ApexSignal] = scalper.evaluate_strategy()
                    if sig:
                        acc = await gw.get_account_snapshot(force_fresh=True)
                        base_lot_size = scalper.compute_lot_size(acc.balance, current_price=quote.mid)

                        # --- LAYA SYSTEM 1 DECISION & ICT RAG VALIDATION ---
                        recent_high = max(c["high"] for c in scalper.candles_1m[-30:]) if len(scalper.candles_1m) >= 5 else sig.entry_price + (sig.atr_1m * 3.0)
                        recent_low = min(c["low"] for c in scalper.candles_1m[-30:]) if len(scalper.candles_1m) >= 5 else sig.entry_price - (sig.atr_1m * 3.0)
                        market_state = {
                            "direction": sig.direction,
                            "entry_price": sig.entry_price,
                            "sl_price": sig.sl_price,
                            "wick_ratio": getattr(scalper, "last_wick_ratio", 0.65),
                            "session": sig.strategy_type,
                            "setup_type": sig.strategy_type,
                            "hour_utc": datetime.now(timezone.utc).hour,
                            "trend_aligned": True,
                            "atr_1m": sig.atr_1m,
                            "recent_high": recent_high,
                            "recent_low": recent_low,
                        }
                        laya_decision = laya_oracle.evaluate_setup_sync(market_state)

                        # Smart & Bold Directive:
                        # VETO only on BLATANT, catastrophic toxic traps (Trap Prob >= 80% or Fatal Politician Red Line)
                        # All other moderate warnings (< 80% Trap Prob) are overridden to execute boldly on base lot size!
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
                                    title=f"🛡️ Blatant Trap Veto: {sig.direction} {sig.strategy_type}",
                                    message=f"Suppressed toxic trap @ ${sig.entry_price:.2f} | Trap Risk: {laya_decision.trap_probability*100:.1f}%\nReason: {laya_decision.reasoning}",
                                    tags="shield,no_entry_sign",
                                    priority="default",
                                )
                                continue

                            logger.info("⚡ SMART & BOLD OVERRIDE: %s (Trap Prob: %.1f%%) -> Executing trade on base lot size (%.2f lots)", 
                                        laya_decision.reasoning, laya_decision.trap_probability * 100, base_lot_size)
                            lot_size = base_lot_size
                            boost_tag = " (Smart & Bold Override - Base Sizing)"
                            effective_spike_target = sig.spike_target
                        else:
                            # Apply dynamic compounding multiplier (up to 1.50x on Macro Sovereign Titan)
                            boosted_lots = round(base_lot_size * max(1.0, laya_decision.compounding_multiplier), 2)
                            
                            # Hard margin cap: Boosted trade must never exceed 75% of available margin at 1:500 leverage!
                            margin_per_001 = max(8.0, (sig.entry_price * 100.0 * 0.01) / 500.0)
                            max_allowed_lots = math.floor((acc.available * 0.75) / margin_per_001) * 0.01
                            lot_size = max(0.01, min(boosted_lots, round(max_allowed_lots, 2)))
                            
                            boost_tag = f" (Laya {laya_decision.setup_grade} {laya_decision.compounding_multiplier:.2f}x Boost | TP {laya_decision.tp_expansion_multiplier:.2f}x)" if laya_decision.compounding_multiplier > 1.0 else ""
                            
                            # Apply Macro Target Expansion (The Sword)
                            effective_spike_target = sig.spike_target
                            if laya_decision.tp_expansion_multiplier > 1.0 and sig.atr_1m > 0:
                                expansion_dist = sig.atr_1m * (laya_decision.tp_expansion_multiplier - 1.0) * 2.5
                                effective_spike_target = (sig.spike_target + expansion_dist) if sig.direction == "BUY" else (sig.spike_target - expansion_dist)

                        # Margin Pre-Check: Never attempt order if available funds cannot cover 1.15x margin
                        margin_per_001 = max(8.0, (sig.entry_price * 100.0 * 0.01) / 500.0)
                        if acc.available < (margin_per_001 * 1.15):
                            logger.warning("⚠️ Insufficient available margin ($%.2f vs required $%.2f). Skipping trade.", acc.available, margin_per_001 * 1.15)
                            continue

                        # Safe broker-side disaster stop (at least $1.50 away to avoid broker DOM minimum stop-level rejects)
                        min_sl_dist = max(1.50, sig.atr_1m * 1.0)
                        if sig.direction == "BUY":
                            broker_sl = round(min(sig.sl_price, sig.entry_price - min_sl_dist), 2)
                        else:
                            broker_sl = round(max(sig.sl_price, sig.entry_price + min_sl_dist), 2)

                        logger.info("🎯 'TO THE MOON' SIGNAL [%s]: %s @ $%.2f | SL: $%.2f (Broker SL: $%.2f) | TP1: $%.2f | Spike: $%.2f | Lots: %.2f%s | Confluence: %.1f/10",
                                    sig.strategy_type, sig.direction, sig.entry_price, sig.sl_price, broker_sl, sig.tp1_price, effective_spike_target, lot_size, boost_tag, laya_decision.confluence_score)

                        # Multi-Order Stacking: If lot_size >= 0.04, split into 2 rapid tickets to fill the volume gap
                        if lot_size >= 0.04:
                            tranche1 = round(lot_size * 0.60, 2)
                            tranche2 = round(lot_size - tranche1, 2)
                            logger.info("⚡ MULTI-ORDER DISPATCH: Order 1 = %.2f lots | Order 2 = %.2f lots (Target: %.2f lots)", tranche1, tranche2, lot_size)
                            order_res1 = await gw.open_market_order(sig.direction, tranche1, sl_price=broker_sl)
                            if order_res1.get("success"):
                                await asyncio.sleep(0.15)
                                # Check available margin before dispatching tranche 2
                                acc_post1 = await gw.get_account_snapshot(force_fresh=True)
                                margin_t2 = (tranche2 / 0.01) * margin_per_001
                                if acc_post1.available >= (margin_t2 * 1.10):
                                    order_res2 = await gw.open_market_order(sig.direction, tranche2, sl_price=broker_sl)
                                    total_vol = round(tranche1 + (tranche2 if order_res2.get("success") else 0.0), 2)
                                    order_res = order_res1
                                    order_res["volume"] = total_vol
                                    order_res["stack_count"] = 2 if order_res2.get("success") else 1
                                else:
                                    logger.info("Tranche 1 filled (%.2f lots). Tranche 2 skipped to preserve margin ($%.2f available).", tranche1, acc_post1.available)
                                    order_res = order_res1
                                    order_res["volume"] = tranche1
                                    order_res["stack_count"] = 1
                            else:
                                order_res = order_res1
                        else:
                            order_res = await gw.open_market_order(sig.direction, lot_size, sl_price=broker_sl)

                        if order_res.get("success"):
                            actual_volume = order_res.get("volume", lot_size)
                            stack_count = order_res.get("stack_count", 1)
                            scalper.last_latency_ms = order_res.get("latency_ms", 0.0)
                            scalper.active_stack = {
                                "direction": sig.direction,
                                "volume": actual_volume,
                                "stack_count": stack_count,
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
                                "pyramided": False,
                                "laya_grade": laya_decision.setup_grade,
                                "ict_concepts": sig.ict_concepts,
                                "political_regime": laya_decision.political_regime,
                                "macro_bias": laya_decision.macro_bias,
                            }
                            stack_tag = f" ({stack_count} Stacked Tickets)" if stack_count > 1 else ""
                            push_ntfy(
                                title=f"🌕 {laya_decision.setup_grade.upper()}: {sig.direction} {actual_volume} Lots [{sig.strategy_type}]{stack_tag}",
                                message=f"✅ REAL BROKER FILLED @ ${sig.entry_price:.2f} | SL: ${sig.sl_price:.2f} | Spike: ${effective_spike_target:.2f}\nSizing: {laya_decision.compounding_multiplier:.2f}x | TP Exp: {laya_decision.tp_expansion_multiplier:.2f}x\nPolitician: {laya_decision.political_regime} ({laya_decision.macro_bias})\nBroker Latency: {scalper.last_latency_ms:.1f}ms",
                                tags="zap,rocket,shield",
                                priority="high",
                            )
                        else:
                            err_msg = order_res.get("error", "Unknown error")
                            logger.error("❌ BROKER ORDER FAILED / REJECTED: %s | Requested: %s %.2f lots", err_msg, sig.direction, lot_size)
                            push_ntfy(
                                title=f"⚠️ Broker Order Rejected: {sig.direction} {lot_size} Lots",
                                message=f"Signal: {sig.strategy_type} @ ${sig.entry_price:.2f}\nBroker Message: {err_msg}\nBalance: ${acc.balance:.2f} (Margin Protection)",
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
                            title=f"🏦 Daily Cash-Out Milestone: ${milestone_bracket}",
                            message=f"Today's Profit: +${withdrawal_info['daily_profit']:.2f}\n"
                                    f"Available Cash-Out: ${withdrawal_info['recommended_cashout_today']:.2f} ({withdrawal_info['withdrawal_rate_pct']}%)\n"
                                    f"Retained for Compounding: ${withdrawal_info['retained_compounding_balance']:.2f}",
                            tags="moneybag,gem",
                            priority="high",
                        )

                status_msg = "Trading on LiteFinance Real Account"
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
                    message=status_msg,
                )

            # Micro-yield (20ms) to keep CPU cool while maintaining sub-millisecond reactivity
            await asyncio.sleep(0.02)
        except Exception as e:
            logger.error("Error in live trading cycle: %s", e)
            await asyncio.sleep(0.5)

    # Clean shutdown
    if scalper.active_stack:
        logger.info("Shutdown signal received: Flattening open broker positions...")
        await gw.flatten_all_positions()

    await gw.close()
    logger.info("Live Broker Scalper shutdown complete.")


if __name__ == "__main__":
    asyncio.run(run_live_scalper())
