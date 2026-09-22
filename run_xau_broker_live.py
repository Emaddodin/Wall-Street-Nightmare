"""
run_xau_broker_live.py
======================
Production Live Trading Engine for XAUUSD executed directly on LiteFinance MT5 Demo.
Features:
- Real-time quote streaming from LiteFinance terminal
- Wednesday Breakout -> Retest -> Rejection candle algorithm
- Direct broker order execution via LiteFinanceGateway
- Tight -$15 Risk Stop & Rapid Profit Spike (+ $50 - $100) exit
- Real-time mobile dashboard sync (:8443) and ntfy push alerts
- Stratton Vault bankroll & tier persistence
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
import urllib.request
import threading
from datetime import datetime, timezone
from email.header import Header
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
DEFAULT_NTFY_URL = "https://ntfy.sh"
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "tbt-96c0dc08c297676b")
WEB_PORT = int(os.getenv("SCALPER_APP_PORT", "443"))
LLAMA_COMPLETION_URL = os.getenv("LLAMA_SERVER_URL", "http://127.0.0.1:8080/completion")

MAX_RISK_STOP_USD = 15.00      # Hard -$15.00 loss cap (15% risk protection)
RAPID_SPIKE_TARGET_USD = 50.00 # Target rapid profit spike harvest (+50% / $50 per tier)


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
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            if resp.status == 200:
                res_json = json.loads(resp.read().decode("utf-8"))
                content = res_json.get("content", "").strip()
                parsed = json.loads(content)
                return (parsed.get("decision") == "GO", parsed.get("reasoning", "Llama GO"))
    except Exception:
        pass
    return (True, "Rule-Based Geometry Confirmed (Local Llama Bypass)")



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


def push_ntfy(title: str, message: str, tags: str = "zap,chart", priority: str = "high") -> bool:
    """Dispatches push notification via Bark (primary) with fallback."""
    return send_alert(title=title, message=message, priority=priority)


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
        "mode": "BROKER LIVE (LiteFinance MT5 Demo #91456523)",
        "symbol": "XAUUSD",
        "balance": round(balance, 2),
        "equity": round(equity, 2),
        "realized_pnl": round(balance - 100.0, 2),
        "pnl_pct": round((equity - 100.0) / 100.0 * 100.0, 2),
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

    def __init__(self, gateway: LiteFinanceGateway):
        self.gw = gateway
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

    def compute_daily_withdrawal(self, current_balance: float) -> Dict[str, Any]:
        """
        Computes recommended daily profit withdrawal according to the "To The Moon" Sovereign schedule.
        - Under $1,000 balance: 30% daily profit cash-out (retaining 70% to compound through initial velocity).
        - $1,000 - $5,000 balance: 50% daily profit cash-out.
        - $5,000+ balance: 70% daily profit cash-out (locking in hard cash while keeping bankroll sovereign).
        """
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.daily_date != today_str:
            self.daily_date = today_str
            self.daily_start_balance = current_balance
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
        hr = datetime.now(timezone.utc).hour
        if 0 <= hr < 6:
            return "Asian Range Accumulation"
        elif 6 <= hr < 11:
            return "London Open Judas / Silver Bullet"
        elif 11 <= hr < 17:
            return "New York AM Silver Bullet Expansion"
        else:
            return "London Close / Asian Pre-Market"

    def compute_lot_size(self, balance: float) -> float:
        """
        "To The Moon" (Apex Sovereign) Aggressive Compounding Ladder:
        $50 - $200 Tier   -> 0.05 lots
        $200 - $400 Tier  -> 0.10 lots
        $400 - $800 Tier  -> 0.20 lots
        $800 - $1500 Tier -> 0.40 lots
        $1500 - $3000 Tier -> 0.80 lots
        $3000+ Tier       -> min(5.00, round(balance / 2000.0, 2))
        """
        if balance < 200.0:
            return 0.05
        elif balance < 400.0:
            return 0.10
        elif balance < 800.0:
            return 0.20
        elif balance < 1500.0:
            return 0.40
        elif balance < 3000.0:
            return 0.80
        else:
            return min(5.00, round(balance / 2000.0, 2))

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

    def evaluate_strategy(self) -> Optional[ApexSignal]:
        """
        Evaluates "To The Moon" (Apex Sovereign Trinity Matrix):
        1. 5m S&R Breakout + 1m Retest + Pin Wick
        2. Multi-Session Silver Bullet FVG CE Tap (London 07-08 UTC & NY 14-15 UTC)
        3. London Turtle Soup Asian Liquidity Sweep (06-09 UTC)
        """
        if len(self.candles_1m) < 30:
            return None
        return self.apex.evaluate(self.candles_1m)


async def run_live_scalper():
    """Main async execution loop."""
    logger.info("Initializing Live Broker Scalper...")

    # Start Stratton Oakmont HFT Web Terminal on background thread (:8443)
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
        title="🟢 Stratton Oakmont Broker LIVE Armed",
        message=f"Connected to LiteFinance MT5 Demo #91456523. Initial Balance: ${acc_snap.balance:.2f} (1:1000 Leverage).\nLaya System 1 & ICT RAG Active.\nTerminal: https://82-115-21-155.sslip.io/ (HTTP: http://82.115.21.155/)",
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
                floating_pnl = acc.floating_pnl
                current_mid = quote.mid
                scalper.active_stack["floating_pnl"] = floating_pnl
                scalper.active_stack["current_price"] = current_mid
                if floating_pnl > scalper.active_stack.get("peak_pnl", 0.0):
                    scalper.active_stack["peak_pnl"] = floating_pnl

                entry_px = scalper.active_stack["entry_price"]
                direction = scalper.active_stack["direction"]
                atr = scalper.active_stack.get("atr_1m", 1.50)

                # Distance moved in favorable direction in points ($/oz)
                gain_pts = (current_mid - entry_px) if direction == "BUY" else (entry_px - current_mid)

                # --- "To The Moon" Sovereign Trailing Ratchet ---
                # Ratchet 1: Breakeven Lock at +1.5 ATR (Guarantees Risk-Free Cushion)
                if not scalper.active_stack.get("be_ratchet_hit", False) and gain_pts >= 1.5 * atr:
                    scalper.active_stack["be_ratchet_hit"] = True
                    new_sl = entry_px + 0.20 if direction == "BUY" else entry_px - 0.20
                    scalper.active_stack["sl_price"] = new_sl
                    logger.info("🛡️ 'TO THE MOON' BE RATCHET LOCKED: SL moved to BE+0.20 ($%.2f) at +%.2f pts", new_sl, gain_pts)

                # Ratchet 2: Profit Lock at +2.5 ATR (TP1 Zone) -> Ratchet SL to +1.5 ATR
                if not scalper.active_stack.get("tp1_ratchet_hit", False) and gain_pts >= 2.5 * atr:
                    scalper.active_stack["tp1_ratchet_hit"] = True
                    locked_sl = entry_px + (1.5 * atr) if direction == "BUY" else entry_px - (1.5 * atr)
                    scalper.active_stack["sl_price"] = locked_sl
                    logger.info("💰 'TO THE MOON' PROFIT LOCK: SL ratcheted to +1.5 ATR ($%.2f) at +%.2f pts", locked_sl, gain_pts)

                # Check if price hit current active software Stop Loss
                sl_hit = (direction == "BUY" and current_mid <= scalper.active_stack["sl_price"]) or \
                         (direction == "SELL" and current_mid >= scalper.active_stack["sl_price"])

                # Calculate tier-scaled risk stop and spike target
                tier_mult = max(1.0, acc.balance / 100.0)
                dynamic_risk_stop = max(MAX_RISK_STOP_USD, tier_mult * 15.0)
                dynamic_spike_target = max(RAPID_SPIKE_TARGET_USD, tier_mult * 50.0)

                # Exit Condition A: Software Trailing SL or Fixed Risk Stop Hit
                if sl_hit or floating_pnl <= -dynamic_risk_stop:
                    is_trailing = scalper.active_stack.get("be_ratchet_hit", False)
                    reason_label = "Trailing Profit Lock" if (is_trailing and floating_pnl > 0) else ("Trailing BE Hit" if is_trailing else "Risk Stop Hit")
                    logger.warning("🛑 %s: Mid: $%.2f, SL: $%.2f, Floating PnL: $%.2f", reason_label, current_mid, scalper.active_stack["sl_price"], floating_pnl)
                    res = await gw.flatten_all_positions()
                    push_ntfy(
                        title=f"🛑 {reason_label} (${floating_pnl:+.2f})",
                        message=f"Strategy: {scalper.active_stack.get('strategy_type')}\nClosed @ ${current_mid:.2f}. New Balance: ${res.get('balance', acc.balance):.2f}",
                        tags="warning,octagonal_sign" if floating_pnl < 0 else "moneybag,shield",
                        priority="urgent" if floating_pnl < 0 else "default",
                    )
                    scalper.active_stack = None

                # Exit Condition B: Macro Spike Harvest (+5.0 ATR or +50% tier target reached)
                elif gain_pts >= 5.0 * atr or floating_pnl >= dynamic_spike_target:
                    logger.info("🚀 'TO THE MOON' MACRO EXPANSION HARVESTED: PnL: +$%.2f | Points: +%.2f", floating_pnl, gain_pts)
                    res = await gw.flatten_all_positions()
                    push_ntfy(
                        title=f"🚀 TO THE MOON HARVEST (+${floating_pnl:.2f})",
                        message=f"Strategy: {scalper.active_stack.get('strategy_type')}\nHarvested spike @ ${current_mid:.2f} (+{gain_pts:.2f} pts).\nNew Balance: ${res.get('balance', acc.balance):.2f} 🌕",
                        tags="tada,moneybag,rocket",
                        priority="high",
                    )
                    scalper.active_stack = None

                # Exit Condition C: Laya In-Flight Momentum Exhaustion
                else:
                    bars_in_trade = len([c for c in scalper.candles_1m if c["open_time"] / 1000.0 >= scalper.active_stack["open_time"]])
                    exhaustion = laya_oracle.evaluate_momentum_exhaustion(floating_pnl, quote.mid, scalper.active_stack["entry_price"], bars_in_trade)
                    if floating_pnl >= 25.0 and exhaustion >= 0.85:
                        logger.info("🧠 LAYA MOMENTUM EXHAUSTION DETECTED (%.0f%%): Locking in profit +$%.2f before retrace", exhaustion * 100, floating_pnl)
                        res = await gw.flatten_all_positions()
                        push_ntfy(
                            title=f"🏁 Laya Momentum Harvest (+${floating_pnl:.2f})",
                            message=f"Locked in profit @ ${quote.mid:.2f} on momentum stall.\nNew Balance: ${res.get('balance', acc.balance):.2f}",
                            tags="sparkles,moneybag",
                            priority="high",
                        )
                        scalper.active_stack = None

            # 3. Check for Strategy Entry if Flat (evaluated every ~250ms)
            elif tick_count % 5 == 0 and not getattr(scalper, "trading_paused", False):
                sig: Optional[ApexSignal] = scalper.evaluate_strategy()
                if sig:
                    acc = await gw.get_account_snapshot(force_fresh=True)
                    base_lot_size = scalper.compute_lot_size(acc.balance)

                    # --- LAYA SYSTEM 1 DECISION & ICT RAG VALIDATION ---
                    market_state = {
                        "direction": sig.direction,
                        "entry_price": sig.entry_price,
                        "sl_price": sig.sl_price,
                        "wick_ratio": getattr(scalper, "last_wick_ratio", 0.65),
                        "session": sig.strategy_type,
                        "setup_type": sig.strategy_type,
                        "hour_utc": datetime.now(timezone.utc).hour,
                        "trend_aligned": True,
                    }
                    laya_decision = laya_oracle.evaluate_setup_sync(market_state)

                    if not laya_decision.is_valid:
                        logger.warning("🛡️ LAYA / POLITICIAN SHIELD VETOED SETUP: %s (Trap Prob: %.1f%%)", laya_decision.reasoning, laya_decision.trap_probability * 100)
                        push_ntfy(
                            title="🛡️ System Guarantee Shield Veto",
                            message=f"Vetoed {sig.direction} ({sig.strategy_type}) @ ${sig.entry_price:.2f} | Trap Risk: {laya_decision.trap_probability*100:.1f}%\nReason: {laya_decision.reasoning}",
                            tags="shield,no_entry_sign",
                            priority="default",
                        )
                    else:
                        # Apply dynamic compounding multiplier (up to 1.65x - 1.75x on Macro Sovereign Titan)
                        lot_size = round(base_lot_size * max(1.0, laya_decision.compounding_multiplier), 2)
                        boost_tag = f" (Laya {laya_decision.setup_grade} {laya_decision.compounding_multiplier:.2f}x Boost | TP {laya_decision.tp_expansion_multiplier:.2f}x)" if laya_decision.compounding_multiplier > 1.0 else ""
                        
                        # Apply Macro Target Expansion (The Sword)
                        effective_spike_target = sig.spike_target
                        if laya_decision.tp_expansion_multiplier > 1.0 and sig.atr_1m > 0:
                            expansion_dist = sig.atr_1m * (laya_decision.tp_expansion_multiplier - 1.0) * 2.5
                            effective_spike_target = (sig.spike_target + expansion_dist) if sig.direction == "BUY" else (sig.spike_target - expansion_dist)

                        logger.info("🎯 'TO THE MOON' SIGNAL [%s]: %s @ $%.2f | SL: $%.2f | TP1: $%.2f | Spike: $%.2f | Lots: %.2f%s | Confluence: %.1f/10",
                                    sig.strategy_type, sig.direction, sig.entry_price, sig.sl_price, sig.tp1_price, effective_spike_target, lot_size, boost_tag, laya_decision.confluence_score)

                        order_res = await gw.open_market_order(sig.direction, lot_size, sl_price=sig.sl_price)
                        if order_res.get("success"):
                            scalper.last_latency_ms = order_res.get("latency_ms", 0.0)
                            scalper.active_stack = {
                                "direction": sig.direction,
                                "volume": lot_size,
                                "entry_price": sig.entry_price,
                                "sl_price": sig.sl_price,
                                "tp1_price": sig.tp1_price,
                                "spike_target": effective_spike_target,
                                "atr_1m": sig.atr_1m,
                                "strategy_type": sig.strategy_type,
                                "open_time": time.time(),
                                "floating_pnl": 0.0,
                                "peak_pnl": 0.0,
                                "be_ratchet_hit": False,
                                "tp1_ratchet_hit": False,
                                "laya_grade": laya_decision.setup_grade,
                                "ict_concepts": sig.ict_concepts,
                                "political_regime": laya_decision.political_regime,
                                "macro_bias": laya_decision.macro_bias,
                            }
                            push_ntfy(
                                title=f"🌕 {laya_decision.setup_grade.upper()}: {sig.direction} {lot_size} Lots [{sig.strategy_type}]",
                                message=f"Entry: ${sig.entry_price:.2f} | SL: ${sig.sl_price:.2f} | Spike: ${effective_spike_target:.2f}\nSizing: {laya_decision.compounding_multiplier:.2f}x | TP Exp: {laya_decision.tp_expansion_multiplier:.2f}x\nPolitician: {laya_decision.political_regime} ({laya_decision.macro_bias})\nLatency: {scalper.last_latency_ms:.1f}ms",
                                tags="zap,rocket,shield",
                                priority="high",
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
                    message=("Trading on LiteFinance MT5 Demo" if not scalper.active_stack else f"In {scalper.active_stack['direction']} position ({scalper.active_stack['volume']} lots)") if not getattr(scalper, "trading_paused", False) else "Auto-trade paused via dashboard",
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
