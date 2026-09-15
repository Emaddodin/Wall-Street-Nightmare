import asyncio
import json
import logging
import os
import requests
import websockets
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

class LiveMonitorAgent:
    def __init__(self, ntfy_topic="hft_alerts"):
        self.ntfy_topic = os.getenv("NTFY_TOPIC", ntfy_topic)
        self.ntfy_url = f"https://ntfy.sh/{self.ntfy_topic}"
        self.clients = set()
        self.ws_server = None
        self.metrics = {
            "ofi_mean": 0.0,
            "hawkes_buy": 0.0,
            "hawkes_sell": 0.0,
            "maker_spread_bps": 0.0,
            "inventory_skew": 0.0,
            "dynamic_leverage": 0,
            "atr_ratchet_mult": 0.0,
        }
        
    async def start(self, host="0.0.0.0", port=8765):
        self.ws_server = await websockets.serve(self._ws_handler, host, port)
        logger.info(f"LiveMonitorAgent WS server started on ws://{host}:{port}")
        
    async def stop(self):
        if self.ws_server:
            self.ws_server.close()
            await self.ws_server.wait_closed()
            
    async def _ws_handler(self, websocket):
        self.clients.add(websocket)
        try:
            # Send initial state
            await websocket.send(json.dumps({"type": "INIT", "data": self.metrics}))
            async for message in websocket:
                pass
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.remove(websocket)
            
    async def _broadcast(self, msg_type, data):
        if not self.clients:
            return
        payload = json.dumps({"type": msg_type, "data": data})
        coros = [client.send(payload) for client in self.clients]
        await asyncio.gather(*coros, return_exceptions=True)
        
    def push_ntfy(self, title, message, tags="chart_with_upwards_trend"):
        try:
            requests.post(
                self.ntfy_url,
                data=message.encode(encoding='utf-8'),
                headers={"Title": title, "Tags": tags}
            )
        except Exception as e:
            logger.error(f"Ntfy push failed: {e}")

    async def log_as_dynamics(self, spread, inv_skew):
        self.metrics["maker_spread_bps"] = spread
        self.metrics["inventory_skew"] = inv_skew
        await self._broadcast("AS_DYNAMICS", {"spread": spread, "skew": inv_skew})
        
    async def log_alpha_trigger(self, conf, h_buy, h_sell, ofi, lev, price):
        self.metrics["hawkes_buy"] = h_buy
        self.metrics["hawkes_sell"] = h_sell
        self.metrics["ofi_mean"] = ofi
        self.metrics["dynamic_leverage"] = lev
        await self._broadcast("ALPHA_TRIGGER", self.metrics)
        self.push_ntfy(
            "Alpha Trigger: Taker Aggression", 
            f"Conf: {conf:.2f} | H-Buy: {h_buy:.2f} | H-Sell: {h_sell:.2f}\nOFI: {ofi:.3f} | Lev: {lev}x | Px: {price:.2f}",
            "rocket"
        )
        
    async def log_ratchet_shift(self, old_mult, new_mult, pnl_pct):
        self.metrics["atr_ratchet_mult"] = new_mult
        await self._broadcast("RATCHET_SHIFT", {"mult": new_mult})
        self.push_ntfy(
            "ATR Ratchet Shift",
            f"Multiplier tightened: {old_mult:.1f} -> {new_mult:.1f}\nUnrealized PnL: {pnl_pct*100:.2f}%",
            "lock"
        )

    async def log_exit(self, side, price, pnl_usdt, pnl_pct, reason):
        await self._broadcast("EXIT", {"side": side, "pnl_usdt": pnl_usdt, "reason": reason})
        tags = "moneybag" if pnl_usdt > 0 else "rotating_light"
        self.push_ntfy(
            f"Scale-Out / Exit ({side.upper()})",
            f"PnL: {pnl_usdt:+.2f} USDT ({pnl_pct*100:+.2f}%)\nPx: {price:.2f} | Reason: {reason}",
            tags
        )
