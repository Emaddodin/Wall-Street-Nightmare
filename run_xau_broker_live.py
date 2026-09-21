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
WEB_PORT = int(os.getenv("SCALPER_APP_PORT", "8443"))
LLAMA_COMPLETION_URL = os.getenv("LLAMA_SERVER_URL", "http://127.0.0.1:8080/completion")

MAX_RISK_STOP_USD = 15.00      # Hard -$15.00 loss cap
RAPID_SPIKE_TARGET_USD = 50.00 # Target profit spike harvest


def start_stratton_oakmont_app(port: int = WEB_PORT) -> None:
    try:
        import scalper.app.app as stratton_app
        stratton_app.PORT = port
        t = threading.Thread(target=stratton_app.run_app, daemon=True)
        t.start()
        token = getattr(stratton_app, "TOKEN", "7SQMRVRJ-VkD4lG3VXsb1Fc82oYUAP93")
        http_port = getattr(stratton_app, "PORT_HTTP", 8088)
        logger.info("🏛️ Stratton Oakmont HFT Terminal live at:")
        logger.info("   👉 Plain HTTP (Zero SSL warnings for friends): http://82.115.21.155:%d/", http_port)
        logger.info("   👉 Secure HTTPS: https://82.115.21.155:%d/", port)
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
) -> None:
    """Syncs live broker telemetry to HFT dashboard JSON file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "state").mkdir(parents=True, exist_ok=True)

    payload = {
        "engine": "XAUUSD Stratton Oakmont Broker LIVE Engine",
        "status": "ACTIVE",
        "fsm_state": "IN_POSITION" if active_pos else "SCANNING",
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
    Manages live candle generation, Wednesday pattern detection, and order dispatch via LiteFinanceGateway.
    """

    def __init__(self, gateway: LiteFinanceGateway):
        self.gw = gateway
        self.active_stack: Optional[Dict[str, Any]] = None
        self.active_5m_breakout: Optional[Dict[str, Any]] = None
        self.candles_1m: List[Dict[str, Any]] = []
        self.current_1m_bar: Optional[Dict[str, Any]] = None
        self.last_latency_ms: float = 0.0

    def compute_lot_size(self, balance: float) -> float:
        """
        Calculates safe lot volume based on account balance and 1:1000 leverage.
        $100 Tier -> 0.05 lots
        $300 Tier -> 0.10 lots
        $500 Tier -> 0.20 lots
        $1000 Tier -> 0.40 lots
        """
        if balance < 200.0:
            return 0.05
        elif balance < 400.0:
            return 0.10
        elif balance < 800.0:
            return 0.20
        else:
            return min(1.00, round(balance / 2000.0, 2))

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

    def evaluate_strategy(self) -> Optional[Tuple[str, float, float, str]]:
        """
        Evaluates 5m Breakout -> 1m Retest -> Rejection wick.
        Returns: (direction, entry_px, sl_px, reasoning) or None
        """
        if len(self.candles_1m) < 30:
            return None

        df_1m = pd.DataFrame(self.candles_1m)
        df_1m["datetime"] = pd.to_datetime(df_1m["open_time"], unit="ms", utc=True)
        df_1m["ema20"] = df_1m["close"].ewm(span=20).mean()
        df_1m["ema50"] = df_1m["close"].ewm(span=50).mean()

        # Resample to 5m
        df_5m = (
            df_1m.set_index("datetime")
            .resample("5min")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "open_time": "first"})
            .dropna()
            .reset_index()
        )
        if len(df_5m) < 6:
            return None

        df_5m["res"] = pa_levels.range_high(df_5m, 6)
        df_5m["sup"] = pa_levels.range_low(df_5m, 6)
        df_5m["break_up"] = pa_levels.breakout_up(df_5m, 6, range_pct=0.03)
        df_5m["break_down"] = pa_levels.breakout_down(df_5m, 6, range_pct=0.03)

        # Check last closed 5m candle for breakout
        last_5m = df_5m.iloc[-1]
        curr_t_ms = int(time.time() * 1000)

        if bool(last_5m.get("break_up", False)):
            self.active_5m_breakout = {
                "type": "UP",
                "level": float(last_5m["res"]),
                "bar_time": int(last_5m["open_time"]),
            }
        elif bool(last_5m.get("break_down", False)):
            self.active_5m_breakout = {
                "type": "DOWN",
                "level": float(last_5m["sup"]),
                "bar_time": int(last_5m["open_time"]),
            }

        # Check for 1m Retest + Rejection Setup
        last_1m = df_1m.iloc[-1]
        curr_px = float(last_1m["close"])

        if self.active_5m_breakout:
            b_type = self.active_5m_breakout["type"]
            lvl = self.active_5m_breakout["level"]
            b_time = self.active_5m_breakout["bar_time"]

            # Must be within 20 minutes of breakout
            if 0 < (curr_t_ms - b_time) <= 20 * 60_000:
                if b_type == "UP":
                    trend_ok = curr_px > last_1m["ema20"] > last_1m["ema50"]
                    retest_ok = float(last_1m["low"]) <= lvl + 1.2 and float(last_1m["high"]) >= lvl - 0.2
                    rng = max(0.01, float(last_1m["high"]) - float(last_1m["low"]))
                    lower_wick = min(float(last_1m["open"]), float(last_1m["close"])) - float(last_1m["low"])
                    wick_ratio = lower_wick / rng
                    rejection_ok = (wick_ratio >= 0.45 and float(last_1m["close"]) >= float(last_1m["open"]))

                    if trend_ok and retest_ok and rejection_ok:
                        sl_px = round(float(last_1m["low"]) - 0.20, 2)
                        reasoning = f"5m S&R Breakout UP + 1m Retest @ ${lvl:.2f} + Pin/Wick {wick_ratio:.2f}"
                        self.active_5m_breakout = None
                        return ("BUY", curr_px, sl_px, reasoning)

                elif b_type == "DOWN":
                    trend_ok = curr_px < last_1m["ema20"] < last_1m["ema50"]
                    retest_ok = float(last_1m["high"]) >= lvl - 1.2 and float(last_1m["low"]) <= lvl + 0.2
                    rng = max(0.01, float(last_1m["high"]) - float(last_1m["low"]))
                    upper_wick = float(last_1m["high"]) - max(float(last_1m["open"]), float(last_1m["close"]))
                    wick_ratio = upper_wick / rng
                    rejection_ok = (wick_ratio >= 0.45 and float(last_1m["close"]) <= float(last_1m["open"]))

                    if trend_ok and retest_ok and rejection_ok:
                        sl_px = round(float(last_1m["high"]) + 0.20, 2)
                        reasoning = f"5m S&R Breakout DOWN + 1m Retest @ ${lvl:.2f} + Pin/Wick {wick_ratio:.2f}"
                        self.active_5m_breakout = None
                        return ("SELL", curr_px, sl_px, reasoning)

        return None


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
    scalper = LiveBrokerScalper(gw)

    push_ntfy(
        title="🟢 Stratton Oakmont Broker LIVE Armed",
        message=f"Connected to LiteFinance MT5 Demo #91456523. Initial Balance: ${acc_snap.balance:.2f} (1:1000 Leverage).\nTerminal: http://82.115.21.155:8088/ (HTTPS: :8443)",
        tags="rocket,white_check_mark",
    )

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: stop_event.set())

    logger.info("Entering live trading loop...")
    tick_count = 0

    while not stop_event.is_set():
        try:
            # 0. Check external web command (e.g. Emergency Flatten button)
            cmd_file = DATA_DIR / "command.json"
            if cmd_file.exists():
                try:
                    with open(cmd_file, "r") as f:
                        cmd = json.load(f)
                    cmd_file.unlink(missing_ok=True)
                    if cmd.get("action") == "FLATTEN":
                        logger.warning("🚨 EMERGENCY FLATTEN SIGNAL RECEIVED FROM DASHBOARD!")
                        res = await gw.flatten_all_positions()
                        push_ntfy(
                            title="🛑 Manual Emergency Flatten",
                            message=f"Closed all positions via Dashboard button. Balance: ${res.get('balance', 0):.2f}",
                            tags="warning,hand",
                            priority="urgent",
                        )
                        scalper.active_stack = None
                except Exception as ce:
                    logger.warning("Error processing dashboard command: %s", ce)

            # 1. Fetch live quote
            quote = await gw.get_live_quote()
            if not quote:
                await asyncio.sleep(1.0)
                continue

            tick_count += 1
            scalper.update_tick(quote)

            # 2. Check position state if in trade
            if scalper.active_stack:
                acc = await gw.get_account_snapshot()
                floating_pnl = acc.floating_pnl
                scalper.active_stack["floating_pnl"] = floating_pnl
                scalper.active_stack["current_price"] = quote.mid

                # Check Exit Condition A: Hard Risk Stop (-$15.00)
                if floating_pnl <= -MAX_RISK_STOP_USD:
                    logger.warning("🚨 HARD RISK STOP TRIGGERED: Floating PnL: -$%.2f <= -$%.2f", abs(floating_pnl), MAX_RISK_STOP_USD)
                    res = await gw.flatten_all_positions()
                    push_ntfy(
                        title=f"🛑 Hard Risk Stop Hit (-${abs(floating_pnl):.2f})",
                        message=f"Closed position @ ${quote.mid:.2f}. New Balance: ${res.get('balance', acc.balance):.2f}",
                        tags="warning,octagonal_sign",
                        priority="urgent",
                    )
                    scalper.active_stack = None

                # Check Exit Condition B: Rapid Profit Spike (+$50.00)
                elif floating_pnl >= RAPID_SPIKE_TARGET_USD:
                    logger.info("🚀 RAPID PROFIT SPIKE REACHED: Floating PnL: +$%.2f >= +$%.2f", floating_pnl, RAPID_SPIKE_TARGET_USD)
                    res = await gw.flatten_all_positions()
                    push_ntfy(
                        title=f"🏁 Profit Spike Harvested (+${floating_pnl:.2f})",
                        message=f"Closed position @ ${quote.mid:.2f}. New Balance: ${res.get('balance', acc.balance):.2f}",
                        tags="tada,moneybag",
                        priority="high",
                    )
                    scalper.active_stack = None

            # 3. Check for Strategy Entry if Flat
            elif tick_count % 3 == 0:  # Check pattern every few ticks
                signal_res = scalper.evaluate_strategy()
                if signal_res:
                    direction, entry_px, sl_px, reasoning = signal_res
                    acc = await gw.get_account_snapshot()
                    lot_size = scalper.compute_lot_size(acc.balance)

                    logger.info("🎯 STRATEGY SIGNAL: %s @ $%.2f | SL: $%.2f | Lots: %.2f", direction, entry_px, sl_px, lot_size)

                    order_res = await gw.open_market_order(direction, lot_size, sl_price=sl_px)
                    if order_res.get("success"):
                        scalper.last_latency_ms = order_res.get("latency_ms", 0.0)
                        scalper.active_stack = {
                            "direction": direction,
                            "volume": lot_size,
                            "entry_price": entry_px,
                            "sl_price": sl_px,
                            "open_time": time.time(),
                            "floating_pnl": 0.0,
                        }
                        push_ntfy(
                            title=f"⚡ Broker Order Executed: {direction} {lot_size} Lots",
                            message=f"Entry: ${entry_px:.2f} | SL: ${sl_px:.2f} | Latency: {scalper.last_latency_ms:.1f}ms\nReason: {reasoning}",
                            tags="zap,dart",
                            priority="high",
                        )

            # 4. Sync telemetry to mobile dashboard
            if tick_count % 2 == 0:
                acc = await gw.get_account_snapshot()
                sync_dashboard_state(
                    balance=acc.balance,
                    equity=acc.equity,
                    active_pos=scalper.active_stack,
                    mid_px=quote.mid,
                    bid_px=quote.bid,
                    ask_px=quote.ask,
                    tier=vault.get("current_tier", 300.0),
                    last_latency_ms=scalper.last_latency_ms,
                    message="Trading on LiteFinance MT5 Demo" if not scalper.active_stack else f"In {scalper.active_stack['direction']} position ({scalper.active_stack['volume']} lots)",
                )

            await asyncio.sleep(1.0)
        except Exception as e:
            logger.error("Error in live trading cycle: %s", e)
            await asyncio.sleep(2.0)

    # Clean shutdown
    if scalper.active_stack:
        logger.info("Shutdown signal received: Flattening open broker positions...")
        await gw.flatten_all_positions()

    await gw.close()
    logger.info("Live Broker Scalper shutdown complete.")


if __name__ == "__main__":
    asyncio.run(run_live_scalper())
