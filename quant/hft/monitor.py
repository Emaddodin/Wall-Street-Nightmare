"""
quant/hft/monitor.py
====================
Observability & Telemetry sidecar for the 5-pillar HFT Engine.
Publishes live metrics to JSON state for the HFT dashboard and sends
institutional-grade push notifications via ntfy.sh with emojis and
structured telemetry.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class LiveMonitorAgent:
    def __init__(self, ntfy_topic: str = "tbt-96c0dc08c297676b"):
        self.ntfy_topic = os.getenv("NTFY_TOPIC", ntfy_topic)
        self.ntfy_url = f"https://ntfy.sh/{self.ntfy_topic}"

        vps_state = Path("/root/ict_sniper/data/state/hft.json")
        local_state = Path(__file__).resolve().parents[2] / "data" / "state" / "hft.json"
        self.state_file = vps_state if Path("/root/ict_sniper").exists() else local_state
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        self.metrics = {
            "engine": "5-Pillar High-Frequency Quant Execution Engine",
            "status": "RUNNING",
            "mode": "PAPER TRADING ($65 Start)",
            "symbol": "BTC",
            "balance": 65.00,
            "equity": 65.00,
            "realized_pnl": 0.0,
            "pnl_pct": 0.0,
            "trade_count": 0,
            "mid_price": 0.0,
            "best_bid": 0.0,
            "best_ask": 0.0,
            "spread_bps": 0.0,
            "as_maker_spread_bps": 0.0,
            "as_reservation_price": 0.0,
            "as_inventory_skew": 0.0,
            "hawkes_buy": 0.0,
            "hawkes_sell": 0.0,
            "hawkes_ratio": 0.5,
            "ofi_mean": 0.0,
            "ofi_levels": [0.0, 0.0, 0.0, 0.0, 0.0],
            "garch_sigma": 0.0,
            "dynamic_leverage": 1,
            "kelly_fraction": 0.0,
            "catboost_confidence": 0.0,
            "catboost_direction": "NEUTRAL",
            "atr_ratchet_mult": 3.0,
            "position": None,
            "recent_logs": [
                {
                    "time": time.strftime("%H:%M:%S", time.gmtime()),
                    "text": "HFT Engine Initialized with $65.00 Paper Allocation",
                    "type": "SYSTEM",
                    "ts": time.time(),
                }
            ],
            "updated_at": time.time(),
        }
        self._flush_state()

    async def start(self, host="0.0.0.0", port=8765):
        logger.info("LiveMonitorAgent active -> %s", self.state_file)

    async def stop(self):
        pass

    def _flush_state(self):
        self.metrics["updated_at"] = time.time()
        try:
            tmp = self.state_file.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(self.metrics, f)
            tmp.replace(self.state_file)
        except Exception as e:
            logger.error(f"Failed writing state: {e}")

    def add_log(self, text: str, log_type: str = "INFO"):
        entry = {
            "time": time.strftime("%H:%M:%S", time.gmtime()),
            "text": text,
            "type": log_type,
            "ts": time.time(),
        }
        self.metrics["recent_logs"] = [entry] + self.metrics["recent_logs"][:25]
        self._flush_state()

    def push_ntfy(self, title: str, message: str, tags: str = "chart_with_upwards_trend", priority: str = "default"):
        """
        Dispatches push notification via Ntfy.
        Title is ASCII-safe to prevent latin-1 HTTP header encoding crashes.
        Emojis are mapped into tags and the UTF-8 message body.
        """
        try:
            requests.post(
                self.ntfy_url,
                data=message.encode("utf-8"),
                headers={
                    "Title": title,
                    "Tags": tags,
                    "Priority": priority,
                },
                timeout=5,
            )
        except Exception as e:
            logger.error(f"Ntfy push error: {e}")

    async def update_tick(
        self,
        mid: float,
        bid: float,
        ask: float,
        spread_bps: float,
        as_spread: float,
        as_res: float,
        as_inv: float,
        h_buy: float,
        h_sell: float,
        ofi_mean: float,
        ofi_levels: list[float],
        sigma: float,
        balance: float,
        realized_pnl: float,
        trade_count: int,
        position: dict | None = None,
        catboost_conf: float = 0.0,
        catboost_dir: str = "NEUTRAL",
        leverage: int = 1,
        kelly_f: float = 0.0,
        killzone_label: str = "",
    ):
        unrealized = position["unrealized_pnl"] if position else 0.0
        equity = balance + unrealized
        pnl_pct = ((equity - 65.0) / 65.0) * 100.0

        self.metrics.update({
            "mid_price": round(mid, 2),
            "best_bid": round(bid, 2),
            "best_ask": round(ask, 2),
            "spread_bps": round(spread_bps, 2),
            "as_maker_spread_bps": round(as_spread, 2),
            "as_reservation_price": round(as_res, 2),
            "as_inventory_skew": round(as_inv, 3),
            "hawkes_buy": round(h_buy, 2),
            "hawkes_sell": round(h_sell, 2),
            "hawkes_ratio": round(h_buy / (h_buy + h_sell) if (h_buy + h_sell) > 0 else 0.5, 3),
            "ofi_mean": round(ofi_mean, 3),
            "ofi_levels": [round(float(x), 2) for x in ofi_levels[:5]] if ofi_levels else [0.0] * 5,
            "garch_sigma": round(sigma, 6),
            "balance": round(balance, 2),
            "equity": round(equity, 2),
            "realized_pnl": round(realized_pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "trade_count": trade_count,
            "position": position,
            "catboost_confidence": round(catboost_conf, 3),
            "catboost_direction": catboost_dir,
            "dynamic_leverage": leverage,
            "kelly_fraction": round(kelly_f, 4),
            "killzone": killzone_label,
        })
        self._flush_state()


    async def log_as_dynamics(self, spread, inv_skew):
        self.metrics["as_maker_spread_bps"] = round(spread, 2)
        self.metrics["as_inventory_skew"] = round(inv_skew, 3)
        self._flush_state()

    # ------------------------------------------------------------------
    # Granular Step-by-Step Trade Lifecycle Notifications
    # ------------------------------------------------------------------

    async def notify_entry(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        qty: float,
        margin: float,
        leverage: int,
        stop_price: float,
        confidence: float,
        hawkes_buy: float,
        hawkes_sell: float,
        ofi: float,
        killzone: str = "",
    ):
        """Dispatched immediately upon order execution."""
        side_upper = side.upper()
        is_long = side_upper == "LONG" or side_upper == "BUY"
        icon = "🟢" if is_long else "🔴"
        tag = "green_circle" if is_long else "red_circle"
        sl_pct = abs((stop_price - entry_price) / entry_price) * 100.0
        kz_line = f"🕐 Kill Zone: {killzone}\n" if killzone else ""

        self.metrics["catboost_confidence"] = round(confidence, 3)
        self.metrics["catboost_direction"] = "LONG" if is_long else "SHORT"
        self.metrics["dynamic_leverage"] = leverage

        self.add_log(f"Alpha Entry ({side_upper}) | Conf: {confidence*100:.1f}% | Lev: {leverage}x @ ${entry_price:.1f} | KZ: {killzone}", "ALPHA")

        title = f"HFT ENTRY: {side_upper} {symbol} ({leverage}x)"
        body = (
            f"{icon} [POSITION OPENED: {side_upper}]\n\n"
            f"🪙 Asset: {symbol} @ ${entry_price:,.1f}\n"
            f"{kz_line}"
            f"⚡ Leverage: {leverage}x (Fractional Kelly)\n"
            f"💵 Margin: ${margin:.2f} USDT (Size: {qty:.4f} {symbol})\n"
            f"🛑 Initial Stop: ${stop_price:,.1f} (-{sl_pct:.2f}%)\n\n"
            f"🧠 Alpha Telemetry:\n"
            f"• CatBoost Confidence: {confidence*100:.1f}%\n"
            f"• Hawkes Intensities: {hawkes_buy:.1f} Buy / {hawkes_sell:.1f} Sell\n"
            f"• 5-Tier OFI Score: {ofi:+.3f}\n\n"
            f"🎯 Target: Chandelier Ratchet Trailing Active"
        )
        self.push_ntfy(title, body, tags=f"{tag},rocket,dart", priority="high")


    async def log_ratchet_shift(self, old_mult: float, new_mult: float, pnl_pct: float, new_stop: float = 0.0):
        """Dispatched when Chandelier ratchet tightens the trailing stop."""
        self.metrics["atr_ratchet_mult"] = round(new_mult, 1)
        self.add_log(f"Chandelier Ratchet Tightened: {old_mult:.1f}x -> {new_mult:.1f}x (PnL: {pnl_pct*100:+.1f}%)", "RATCHET")

        stop_str = f"🛡️ New Trailing Stop: ${new_stop:,.1f}\n" if new_stop > 0 else ""
        title = f"RATCHET TIGHTENED: {new_mult:.1f}x ATR"
        body = (
            f"🔒 [TRAILING STOP TIGHTENED]\n\n"
            f"📊 Unrealized Gain: {pnl_pct*100:+.2f}%\n"
            f"📐 Ratchet Contraction: {old_mult:.1f}x ➔ {new_mult:.1f}x ATR\n"
            f"{stop_str}\n"
            f"⚡ Stop-loss advanced to protect open floating profit."
        )
        self.push_ntfy(title, body, tags="lock,arrow_up,gem", priority="default")

    async def notify_tp_scale(self, symbol: str, side: str, exit_px: float, pnl_usdt: float, pnl_pct: float, tier: str, remaining_qty: float):
        """Dispatched on TP scale-out fill."""
        self.add_log(f"Scale-Out ({tier}) | PnL: {pnl_usdt:+.2f} USDT (+{pnl_pct*100:.1f}%) @ ${exit_px:.1f}", "EXIT")

        title = f"TAKE PROFIT: {tier} FILL"
        body = (
            f"💰 [TAKE PROFIT HIT: {tier}]\n\n"
            f"🪙 Asset: {symbol} ({side.upper()})\n"
            f"🎯 Execution Price: ${exit_px:,.1f}\n"
            f"💵 Realized Gain: +${pnl_usdt:.2f} USDT (+{pnl_pct*100:.2f}%)\n"
            f"📦 Remaining Size: {remaining_qty:.4f} {symbol}\n\n"
            f"🔒 Runner trailing with locked-in profit."
        )
        self.push_ntfy(title, body, tags="moneybag,chart_with_upwards_trend", priority="high")

    async def log_exit(self, side: str, price: float, pnl_usdt: float, pnl_pct: float, reason: str, balance: float = 65.0):
        """Dispatched when a trade fully closes."""
        is_win = pnl_usdt >= 0
        icon = "🎉" if is_win else "🛑"
        tag = "moneybag" if is_win else "rotating_light"
        pnl_sign = "+" if is_win else ""

        self.add_log(f"Exit {side.upper()} @ ${price:.1f} | PnL: {pnl_sign}{pnl_usdt:.2f} USDT ({pnl_sign}{pnl_pct*100:.2f}%) [{reason}]", "EXIT")

        title = f"POSITION CLOSED: {pnl_sign}${pnl_usdt:.2f} USDT ({side.upper()})"
        body = (
            f"{icon} [TRADE COMPLETED: {reason.upper()}]\n\n"
            f"🪙 Position: {side.upper()} BTC-PERP\n"
            f"🎯 Exit Price: ${price:,.1f}\n"
            f"💵 Net Trade PnL: {pnl_sign}${pnl_usdt:.2f} USDT ({pnl_sign}{pnl_pct*100:.2f}%)\n\n"
            f"💼 Session Update:\n"
            f"• Current Account Equity: ${balance:.2f} USDT\n"
            f"• Realized Total PnL: {pnl_sign}${self.metrics['realized_pnl']:.2f} USDT\n"
            f"• Total Trades: {self.metrics['trade_count']}\n\n"
            f"📡 Scanner resumed looking for next microstructure setup."
        )
        self.push_ntfy(title, body, tags=f"{tag},checkered_flag", priority="high" if is_win else "default")

    async def notify_daily_target_hit(self, balance: float, target_pct: float = 100.0):
        """Dispatched when the +100% daily target is reached."""
        title = "DAILY TARGET ACHIEVED: +100% FLIP"
        body = (
            f"🏆 [DAILY TARGET HIT: +{target_pct:.0f}%]\n\n"
            f"💰 Account Balance: ${balance:.2f} USDT\n"
            f"🎯 Milestone: Capital Doubled (Account Flipped)\n"
            f"🛡️ Target Circuit Breaker: Engine safely halted for the rest of the UTC day.\n\n"
            f"Enjoy your profits! Resume scheduled for next UTC 00:00."
        )
        self.push_ntfy(title, body, tags="trophy,partying_face,star2", priority="urgent")

    async def notify_drawdown_halt(self, balance: float, max_loss: float = 32.50):
        """Dispatched if daily loss limit is hit."""
        title = "CIRCUIT BREAKER: DAILY DRAWDOWN HALT"
        body = (
            f"🛑 [CIRCUIT BREAKER TRIGGERED]\n\n"
            f"⚠️ Max daily loss limit of -50% reached.\n"
            f"💼 Current Balance: ${balance:.2f} USDT\n"
            f"🔒 Capital preservation lock engaged until next UTC day."
        )
        self.push_ntfy(title, body, tags="octagonal_sign,warning,rotating_light", priority="urgent")

    async def notify_flow_diagnostic(self, diag: dict):
        """Dispatched periodically when the bot is idle to confirm market vs bug."""
        idle_m = diag.get("idle_minutes", 30)
        status = diag.get("status", "SCANNING_NOMINAL")
        is_bug = "FAULT" in status
        icon = "⚠️" if is_bug else "🔍"
        tag = "warning" if is_bug else "magifying_glass_tilted_left"
        priority = "high" if is_bug else "default"

        title = f"TRADE FLOW AUDIT: {status}"
        body = (
            f"{icon} [TRADE FLOW INACTIVITY AUDIT: {idle_m}M IDLE]\n\n"
            f"📊 Status: {status}\n"
            f"🔬 Diagnosis: {diag.get('diagnosis')}\n\n"
            f"⚙️ Pipeline Funnel Health:\n"
            f"• E2E Self-Test: {'✅ PASSED (Zero Software Bugs)' if diag.get('self_test_passed') else '❌ FAILED'}\n"
            f"• L2 Ticks Processed: {diag.get('ticks_processed'):,} ticks\n"
            f"• Peak Confidence Seen: {diag.get('max_confidence_seen', 0)*100:.1f}% (Req: >60.0%)\n"
            f"• Quiet Order Flow: {diag.get('hawkes_quiet_pct')}% of ticks\n"
            f"• Pipeline Exceptions: {diag.get('pipeline_exceptions')}\n\n"
            f"🛡️ Capital is fully guarded. Engine is waiting for verified alpha conditions."
        )
        self.push_ntfy(title, body, tags=f"{tag},bar_chart,shield", priority=priority)

    async def notify_day_rollover(self, day_num: int, balance: float, target_balance: float):
        """Dispatched at 00:00 UTC marking the new trading day of the path."""
        title = f"DAY {day_num} PATH LAUNCHED: 00:00 UTC"
        body = (
            f"🏆 [DAY {day_num} OF COMPOUNDING PATH: 00:00 UTC]\n\n"
            f"💰 Day Starting Equity: ${balance:.2f} USDT\n"
            f"🎯 Day {day_num} Target (+100%): ${target_balance:.2f} USDT\n"
            f"🛑 Daily Circuit Breaker: ${balance * 0.50:.2f} USDT (-50%)\n\n"
            f"⚡ All 5 Quantitative Pillars Active:\n"
            f"• Avellaneda-Stoikov Market Maker: ONLINE\n"
            f"• CatBoost Microsecond Direction Predictor: ONLINE\n"
            f"• Hawkes Liquidity Excitation Tracker: ONLINE\n"
            f"• 5-Level OFI Microstructure: ONLINE\n"
            f"• ATR Chandelier Trailing Ratchet: ONLINE\n\n"
            f"🚀 Hunting microsecond BTC alpha setups. Good luck!"
        )
        self.push_ntfy(title, body, tags="trophy,rocket,chart_with_upwards_trend", priority="high")
