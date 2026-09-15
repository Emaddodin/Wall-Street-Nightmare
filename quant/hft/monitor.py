"""
quant/hft/monitor.py
====================
Observability & Telemetry sidecar for the 5-pillar HFT Engine.
Publishes live metrics to JSON state for the HFT dashboard and sends
real-time push notifications via ntfy.sh.
"""

from __future__ import annotations

import asyncio
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

        # State path aligns with VPS environment
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
                {"time": time.strftime("%H:%M:%S", time.gmtime()), "text": "HFT Engine Initialized with $65.00 Paper Allocation", "type": "SYSTEM", "ts": time.time()}
            ],
            "updated_at": time.time()
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
            "ts": time.time()
        }
        self.metrics["recent_logs"] = [entry] + self.metrics["recent_logs"][:25]
        self._flush_state()

    def push_ntfy(self, title: str, message: str, tags: str = "chart_with_upwards_trend", priority: str = "default"):
        try:
            requests.post(
                self.ntfy_url,
                data=message.encode("utf-8"),
                headers={"Title": title, "Tags": tags, "Priority": priority},
                timeout=5
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
        kelly_f: float = 0.0
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
            "ofi_levels": [round(float(x), 2) for x in ofi_levels[:5]] if ofi_levels else [0.0]*5,
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
        })
        self._flush_state()

    async def log_as_dynamics(self, spread, inv_skew):
        self.metrics["as_maker_spread_bps"] = round(spread, 2)
        self.metrics["as_inventory_skew"] = round(inv_skew, 3)
        self._flush_state()

    async def log_alpha_trigger(self, conf, h_buy, h_sell, ofi, lev, price, direction="BUY"):
        self.metrics["catboost_confidence"] = round(conf, 3)
        self.metrics["catboost_direction"] = direction
        self.metrics["hawkes_buy"] = round(h_buy, 2)
        self.metrics["hawkes_sell"] = round(h_sell, 2)
        self.metrics["ofi_mean"] = round(ofi, 3)
        self.metrics["dynamic_leverage"] = lev

        self.add_log(f"Alpha Trigger ({direction}) | Conf: {conf*100:.1f}% | Lev: {lev}x @ ${price:.1f}", "ALPHA")
        self.push_ntfy(
            f"Alpha Trigger: {direction} @ ${price:.1f}",
            f"Confidence: {conf*100:.1f}%\nHawkes B/S: {h_buy:.1f}/{h_sell:.1f}\nOFI: {ofi:+.2f} | Lev: {lev}x",
            "rocket",
            "high"
        )

    async def log_ratchet_shift(self, old_mult, new_mult, pnl_pct):
        self.metrics["atr_ratchet_mult"] = round(new_mult, 1)
        self.add_log(f"Chandelier Ratchet Tightened: {old_mult:.1f}x -> {new_mult:.1f}x (PnL: {pnl_pct*100:+.1f}%)", "RATCHET")
        self.push_ntfy(
            "Chandelier Ratchet Tightened",
            f"Multiplier shifted: {old_mult:.1f}x -> {new_mult:.1f}x\nUnrealized Gain: {pnl_pct*100:+.2f}%",
            "lock"
        )

    async def log_exit(self, side, price, pnl_usdt, pnl_pct, reason):
        self.add_log(f"Exit {side.upper()} @ ${price:.1f} | PnL: {pnl_usdt:+.2f} USDT ({pnl_pct*100:+.2f}%) [{reason}]", "EXIT")
        tags = "moneybag" if pnl_usdt > 0 else "rotating_light"
        self.push_ntfy(
            f"Position Closed ({side.upper()}): {pnl_usdt:+.2f} USDT",
            f"Exit: ${price:.1f} | ROI: {pnl_pct*100:+.2f}%\nReason: {reason}",
            tags,
            "high" if abs(pnl_usdt) > 1.0 else "default"
        )
