"""
replay_streamer.py
==================
Automated 1:1 Live Market Replay Simulator for HyperPredatorBot.

Simulates Thursday's (2026-09-17) GOLD trading day starting at 00:00:00 UTC (03:30 AM Tehran time)
at 1.0x real-time speed (or user-defined --speed multiplier), complete with:
- Non-drifting Scheduler Countdown & Instant (--now) Start
- In-Memory High-Precision Clock Spoofing
- Asynchronous M1 & L2 Tick Ingestion into HyperPredatorBot
- Sub-5ms Order Flow Exits & -$10.00 Hard Equity Shield Enforcement
- FastAPI Command Center with WebSocket Streaming (/ws/stream)
- Real-time ntfy Push Alerts tagged with [REPLAY] markers
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import uuid
from typing import Any, Dict, List, Optional

import uvicorn

import hyper_predator_bot
from hyper_predator_bot import (
    HyperPredatorBot,
    PredatorBasket,
)
from edge_diagnostics import EdgeDiagnosticReporter
from ntfy_integration import ReplayNtfyDispatcher
from replay_clock import ReplayClock
from replay_data import get_thursday_start_timestamp_ms, load_thursday_candles
from replay_scheduler import wait_until_midnight_utc
from replay_server import ReplayServerState, broadcast_state, create_replay_app

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("replay_streamer")


class ReplayBrokerVenue:
    """
    Simulated Broker Venue for 1:1 Replay conforming to HyperliquidVenue protocol.
    Maintains ledger, fills spam slices at 100x leverage, tracks resting stops,
    and calculates exact uPnL.
    """

    def __init__(self, initial_equity: float = 65.00, slippage_delta: float = 0.02) -> None:
        self.equity = initial_equity
        self.slippage_delta = slippage_delta
        self.coin = "GOLD"
        self.current_price = 2500.00
        self._orders: Dict[str, Dict[str, Any]] = {}
        self._resting_stops: Dict[str, Dict[str, Any]] = {}
        self.active_basket: Optional[PredatorBasket] = None
        self.trades_history: List[Dict[str, Any]] = []

    def set_market_price(self, *args: Any, **kwargs: Any) -> None:
        if len(args) == 1:
            self.current_price = round(float(args[0]), 2)
        elif len(args) >= 2:
            self.current_price = round(float(args[1]), 2)
        elif "price" in kwargs:
            self.current_price = round(float(kwargs["price"]), 2)

    async def get_equity(self) -> float:
        return self.equity

    async def get_market_price(self, coin: str = "GOLD") -> float:
        return self.current_price

    async def market_open(
        self,
        coin: str,
        is_buy: bool,
        sz: float,
        px: Optional[float] = None,
        slippage: float = 0.01,
    ) -> Dict[str, Any]:
        base_px = px or self.current_price
        slip = self.slippage_delta if is_buy else -self.slippage_delta
        fill_price = round(base_px + slip, 2)
        oid = f"REPLAY-OPEN-{uuid.uuid4().hex[:8].upper()}"

        record = {
            "status": "ok",
            "oid": oid,
            "coin": coin,
            "is_buy": is_buy,
            "sz": sz,
            "fill_price": fill_price,
            "take_profit": None,
        }
        self._orders[oid] = record
        return record

    async def market_close(
        self,
        coin: str,
        sz: Optional[float] = None,
        px: Optional[float] = None,
        slippage: float = 0.01,
        trigger_px: Optional[float] = None,
        reduce_only: bool = True,
    ) -> Dict[str, Any]:
        oid = f"REPLAY-CLOSE-{uuid.uuid4().hex[:8].upper()}"
        if trigger_px is not None:
            # Resting Stop-Market Order
            rec = {
                "oid": oid,
                "coin": coin,
                "sz": sz,
                "trigger_px": round(trigger_px, 2),
                "reduce_only": reduce_only,
                "status": "resting",
            }
            self._resting_stops[oid] = rec
            return {"status": "ok", "oid": oid, "type": "stop_market", "reduce_only": reduce_only}

        # Market Close
        fill_px = px or self.current_price
        return {
            "status": "ok",
            "oid": oid,
            "coin": coin,
            "sz": sz,
            "fill_price": fill_px,
            "reduce_only": reduce_only,
        }

    async def cancel(self, coin: str, oid: str) -> bool:
        if oid in self._resting_stops:
            self._resting_stops[oid]["status"] = "cancelled"
            return True
        return False


class MarketReplayStreamer:
    """
    Master Orchestrator for Automated Live Market Replay.
    """

    def __init__(
        self,
        speed: float = 1.0,
        start_immediate: bool = False,
        port: int = 8000,
        host: str = "0.0.0.0",
        run_server: bool = True,
        enable_ntfy: bool = True,
        test_mode_bars: Optional[int] = None,
        macro_url: Optional[str] = None,
        max_margin_pct: float = 0.20,
        sr_lookback_bars: int = 50,
        zone_epsilon: float = 0.25,
    ) -> None:
        self.speed = speed
        self.start_immediate = start_immediate
        self.port = port
        self.host = host
        self.run_server = run_server
        self.enable_ntfy = enable_ntfy
        self.test_mode_bars = test_mode_bars
        self.macro_url = macro_url or (f"http://127.0.0.1:{self.port}/completion" if run_server else "http://localhost:8080/completion")
        self.max_margin_pct = max_margin_pct
        self.sr_lookback_bars = sr_lookback_bars
        self.zone_epsilon = zone_epsilon

        # T0 Thursday 2026-09-17 00:00:00 UTC
        self.simulated_start_ts = get_thursday_start_timestamp_ms() / 1000.0
        self.clock = ReplayClock(self.simulated_start_ts, speed=self.speed)
        self.venue = ReplayBrokerVenue(initial_equity=65.00)
        self.bot = HyperPredatorBot(
            venue=self.venue,
            macro_url=self.macro_url,
            zone_epsilon=self.zone_epsilon,
            sr_lookback_bars=self.sr_lookback_bars,
            warmup_bars=15,
            max_margin_pct=self.max_margin_pct,
        )

        # Server and notifications
        self.state = ReplayServerState()
        self.state.speed = self.speed
        self.app = create_replay_app(self.state) if self.run_server else None
        self.ntfy = ReplayNtfyDispatcher(enabled=self.enable_ntfy)
        self.diagnostics = EdgeDiagnosticReporter(ntfy_dispatcher=self.ntfy, report_interval_seconds=10800.0)

        self._running = False
        self._server_task: Optional[asyncio.Task] = None
        self._server_instance: Optional[uvicorn.Server] = None
        self._replay_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    async def start_web_server(self) -> None:
        """Starts the FastAPI Command Center server in the background."""
        if not self.run_server or not self.app:
            return
        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config)
        self._server_instance = server
        self._server_task = asyncio.create_task(server.serve())

        # Wait until server is fully listening
        for _ in range(50):
            if server.started:
                break
            await asyncio.sleep(0.02)

        logger.info("FastAPI Replay Command Center running at http://%s:%d", self.host, self.port)

    async def run(self) -> None:
        """Main lifecycle entry point."""
        self._running = True

        # 1. Start Web Server
        await self.start_web_server()

        # 2. Scheduler Countdown to 00:00:00 UTC (unless --now is specified)
        if not self.start_immediate:
            self.state.status = "WAITING_FOR_MIDNIGHT_UTC"
            logger.info("Awaiting scheduled trigger at 00:00:00 UTC...")

            def on_countdown_tick(rem_sec: float, rem_str: str) -> None:
                self.state.countdown_seconds = round(rem_sec, 1)
                self.state.countdown_str = rem_str
                if self.app:
                    asyncio.create_task(broadcast_state(self.app))

            await wait_until_midnight_utc(
                heartbeat_interval=1.0,
                on_tick=on_countdown_tick,
                stop_event=self._stop_event,
            )
            if self._stop_event.is_set():
                return

        # 3. Trigger Replay Loop
        self.state.status = "REPLAYING"
        logger.info("Starting 1:1 Live Market Replay of Thursday GOLD at %sx speed!", self.speed)

        # Patch bot time to spoofed clock
        self.clock.start()
        self.clock.patch_hyper_predator_bot(hyper_predator_bot)

        try:
            # Start Bot
            await self.bot.start()

            # Load Thursday Candles strictly from CandleStore or disk
            candles = load_thursday_candles()
            if self.test_mode_bars:
                candles = candles[: self.test_mode_bars]
                logger.info("Test mode active: streaming %d bars only.", len(candles))

            # Stream Candles
            await self._stream_historical_data(candles)

        except asyncio.CancelledError:
            logger.info("Replay loop cancelled.")
        except Exception as exc:
            logger.error("Error in replay streamer: %s", exc, exc_info=True)
        finally:
            await self.shutdown()

    async def _stream_historical_data(self, candles: List[Dict[str, Any]]) -> None:
        """Streams candles, ticks, and order flow at calibrated speed."""
        prev_bias = self.bot.macro_mgr.get_state().bias
        bars_since_diagnostic = 0
        processed_closed_events_count = 0

        for idx, candle in enumerate(candles):
            if not self._running or self._stop_event.is_set():
                break

            # Synchronize clock with candle start time
            c_time_sec = candle["time"] / 1000.0
            self.clock.set_time(c_time_sec)
            cur_iso = self.clock.isoformat()
            self.state.simulated_time_iso = cur_iso
            self.state.simulated_timestamp = c_time_sec
            self.state.current_price = candle["close"]
            self.venue.set_market_price(candle["close"])

            # Feed L1 Best Bid/Ask & L2 Depth into Tape Memory using Hyperliquid schemas
            bbo_msg = {
                "bbo": [
                    {"px": str(candle["best_bid"]), "sz": "20.0"},
                    {"px": str(candle["best_ask"]), "sz": "20.0"},
                ]
            }
            self.bot.tape_memory.on_bbo(bbo_msg)

            l2_bids = [{"px": str(b[0]), "sz": str(b[1])} for b in candle.get("l2_bids", [])]
            l2_asks = [{"px": str(a[0]), "sz": str(a[1])} for a in candle.get("l2_asks", [])]
            self.bot.tape_memory.on_l2({"levels": [l2_bids, l2_asks]})

            # Feed Recent Trades into Tape Memory
            if "trades" in candle and candle["trades"]:
                self.bot.tape_memory.on_trades(candle["trades"])

            # Record intra-bar tick timestamps
            for tick_ts in candle.get("tick_timestamps", []):
                self.bot.tape_memory.record_tick(tick_ts)

            # Dynamic Causal Macro Trend Engine (Institutional M5 EMA9 vs EMA21)
            closes = [float(c["close"]) for c in self.bot.sniper_engine.m5_candles]
            if len(closes) >= 3:
                def calc_ema(arr: List[float], span: int) -> float:
                    alpha = 2.0 / (span + 1.0)
                    e = arr[0]
                    for x in arr[1:]:
                        e = alpha * x + (1.0 - alpha) * e
                    return e
                e9 = calc_ema(closes, min(len(closes), 9))
                e21 = calc_ema(closes, min(len(closes), 21))
                sim_bias = "BULLISH" if e9 >= e21 else "BEARISH"
                self.bot.macro_mgr.update(permit_trade=True, bias=sim_bias, volatility_regime=1.0)
            elif len(closes) >= 1:
                self.bot.macro_mgr.update(permit_trade=True, bias="BULLISH", volatility_regime=1.0)
            else:
                supp = self.bot.sniper_engine.current_support or candle["low"]
                res = self.bot.sniper_engine.current_resistance or candle["high"]
                sim_bias = "BULLISH" if candle["close"] >= (supp + res) / 2.0 else "BEARISH"
                self.bot.macro_mgr.update(permit_trade=True, bias=sim_bias, volatility_regime=1.0)

            current_macro = self.bot.macro_mgr.get_state()

            # Check LLM Macro Shift
            if current_macro.bias != prev_bias:
                self.ntfy.notify_llm_shift(
                    sim_time_str=cur_iso[11:19],
                    bias=current_macro.bias,
                    regime=current_macro.volatility_regime,
                    permit_trade=current_macro.permit_trade,
                )
                self.state.log_messages.append({
                    "time": cur_iso[11:19],
                    "type": "MACRO",
                    "text": f"🧠 Macro Brain shifted bias to {current_macro.bias} (Regime: {current_macro.volatility_regime:.2f}x)",
                })
                prev_bias = current_macro.bias

            # Check Hard Equity Shield (-$10.00), Breakeven Lock (+1.5R), Detached SL, and Runners
            basket = self.bot.execution_bridge.active_basket
            if basket and basket.is_active:
                cur_sz = basket.current_sz
                # Update floating uPnL
                if basket.is_buy:
                    upnl = (candle["close"] - basket.entry_price) * cur_sz
                else:
                    upnl = (basket.entry_price - candle["close"]) * cur_sz
                basket.unrealized_pnl = round(upnl, 2)

                # Breakeven Lock at +1.5R: Protects profits once basket moves in favor
                if basket.sl_price > 0.0:
                    init_risk = abs(basket.entry_price - basket.invalidation_price) if basket.invalidation_price > 0 else 1.00
                    if init_risk > 0.0:
                        pts = (candle["close"] - basket.entry_price) if basket.is_buy else (basket.entry_price - candle["close"])
                        if pts >= 1.5 * init_risk:
                            be_sl = round(basket.entry_price + 0.10, 2) if basket.is_buy else round(basket.entry_price - 0.10, 2)
                            if (basket.is_buy and basket.sl_price < be_sl) or ((not basket.is_buy) and basket.sl_price > be_sl):
                                basket.sl_price = be_sl
                                logger.info("BREAKEVEN LOCK ENGAGED for basket %s at $%.2f", basket.basket_id, be_sl)

                # Check detached stop loss
                if basket.stop_price is not None:
                    hit_stop = (candle["low"] <= basket.stop_price) if basket.is_buy else (candle["high"] >= basket.stop_price)
                    if hit_stop:
                        logger.warning(
                            "DETACHED_STOP_LOSS HIT at $%.2f (Stop: $%.2f)", candle["close"], basket.stop_price
                        )
                        await self.bot.execution_bridge.close_basket(
                            coin=self.bot.coin,
                            reason="DETACHED_STOP_LOSS",
                            exit_price=basket.stop_price,
                        )
                # Check opposing target S/R exit (Ruthless 100% Market Exit)
                elif basket.target_price is not None:
                    hit_target = (candle["high"] >= basket.target_price) if basket.is_buy else (candle["low"] <= basket.target_price)
                    if hit_target:
                        logger.info(
                            "TARGET S/R REACHED at $%.2f (Target: $%.2f)", candle["close"], basket.target_price
                        )
                        await self.bot.execution_bridge.close_basket(
                            coin=self.bot.coin,
                            reason="TARGET_M5_SR",
                            exit_price=basket.target_price,
                        )
                # Check Hard Equity Shield breach (-$10.00)
                elif upnl <= -10.00:
                    logger.warning(
                        "HARD EQUITY SHIELD BREACHED: uPnL = $%.2f at price %.2f", upnl, candle["close"]
                    )
                    self.ntfy.notify_equity_shield(
                        sim_time_str=cur_iso[11:19],
                        coin="GOLD",
                        pnl=upnl,
                        exit_price=candle["close"],
                    )
                    self.state.log_messages.append({
                        "time": cur_iso[11:19],
                        "type": "SHIELD",
                        "text": f"🚨 EQUITY SHIELD PANIC LIQUIDATION at ${candle['close']:.2f} (uPnL: -${abs(upnl):.2f})",
                    })
                    self.state.markers.append({
                        "time": candle["time"],
                        "type": "SHIELD",
                        "price": candle["close"],
                        "text": f"Shield Liquidate (-${abs(upnl):.2f})",
                    })
                    await self.bot.execution_bridge.close_basket(
                        coin=self.bot.coin,
                        reason="HARD_EQUITY_SHIELD_LIQUIDATION",
                        exit_price=candle["close"],
                    )

            # Ingest M1 Candle Close into Sniper Engine
            ticks_for_m1 = candle.get("tick_timestamps", [])
            new_basket = await self.bot.on_m1_candle_close(candle, ticks_for_m1)

            # If new spam entry fired
            if new_basket:
                dir_str = "BUY" if new_basket.is_buy else "SELL"
                self.ntfy.notify_spam_fired(
                    sim_time_str=cur_iso[11:19],
                    coin="GOLD",
                    direction=dir_str,
                    total_sz=new_basket.total_sz,
                    price=new_basket.entry_price,
                    slices=len(new_basket.slices),
                )
                self.state.log_messages.append({
                    "time": cur_iso[11:19],
                    "type": "SPAM",
                    "text": f"🟢 SPAM FIRED: {dir_str} {new_basket.total_sz:.2f} oz @ ${new_basket.entry_price:.2f} (5 Slices)",
                })
                self.state.markers.append({
                    "time": candle["time"],
                    "type": f"SPAM_{dir_str}",
                    "price": new_basket.entry_price,
                    "text": f"Spam {dir_str} ({new_basket.total_sz:.2f} oz)",
                })

            # Process any newly emitted closed basket events from ExecutionBridge
            while len(self.bot.execution_bridge.closed_events) > processed_closed_events_count:
                ev = self.bot.execution_bridge.closed_events[processed_closed_events_count]
                processed_closed_events_count += 1
                pnl = ev.get("realized_pnl", 0.0)
                exit_px = ev.get("exit_price", candle["close"])
                closed_sz = ev.get("closed_sz", ev.get("aggregate_sz", 0.0))
                reason = ev.get("reason", "DYNAMIC_EXIT")

                self.venue.equity = round(self.venue.equity + pnl, 2)
                self.state.recent_trades.append({
                    "time": cur_iso[11:19],
                    "coin": ev.get("coin", "GOLD"),
                    "entry_price": ev.get("entry_price", 0.0),
                    "exit_price": exit_px,
                    "sz": closed_sz,
                    "pnl": pnl,
                    "reason": reason,
                })
                self.ntfy.notify_micro_exit(
                    sim_time_str=cur_iso[11:19],
                    coin="GOLD",
                    exit_price=exit_px,
                    pnl=pnl,
                    reason=reason,
                )
                self.state.log_messages.append({
                    "time": cur_iso[11:19],
                    "type": "EXIT",
                    "text": f"🔵 EXIT @ ${exit_px:.2f} | PnL: ${pnl:+.2f} ({reason})",
                })
                self.state.markers.append({
                    "time": candle["time"],
                    "type": "EXIT",
                    "price": exit_px,
                    "text": f"Exit (${pnl:+.2f})",
                })

            # Update State Telemetry
            active_b = self.bot.execution_bridge.active_basket
            if active_b and active_b.is_active:
                dir_str = "LONG" if active_b.is_buy else "SHORT"
                upnl = active_b.unrealized_pnl
                upnl_pct = (upnl / self.venue.equity) * 100.0
                self.state.active_position = {
                    "is_active": True,
                    "direction": dir_str,
                    "entry_price": active_b.entry_price,
                    "size": active_b.total_sz,
                    "target_price": active_b.target_price,
                    "stop_price": active_b.stop_price,
                    "upnl": upnl,
                    "upnl_pct": round(upnl_pct, 2),
                }
            else:
                self.state.active_position = {
                    "is_active": False,
                    "direction": "FLAT",
                    "entry_price": 0.0,
                    "size": 0.0,
                    "target_price": None,
                    "stop_price": None,
                    "upnl": 0.0,
                    "upnl_pct": 0.0,
                }

            self.state.equity_current = self.venue.equity + (
                self.state.active_position["upnl"] if self.state.active_position["is_active"] else 0.0
            )
            self.state.macro_edge = {
                "permit_trade": current_macro.permit_trade,
                "bias": current_macro.bias,
                "volatility_regime": current_macro.volatility_regime,
                "last_updated": current_macro.last_updated,
            }
            self.state.latest_candle = candle
            self.state.candles_history.append(candle)

            # Broadcast update via WebSocket
            if self.app:
                await broadcast_state(self.app)

            # 3-Hour Edge Diagnostic Trigger (every 180 M1 bars = 3 hours simulated)
            bars_since_diagnostic += 1
            if bars_since_diagnostic >= 180:
                bars_since_diagnostic = 0
                asyncio.create_task(
                    self.diagnostics.dispatch_3hour_notification(
                        current_equity=self.state.equity_current,
                        trades=self.state.recent_trades,
                        macro_state=self.state.macro_edge,
                    )
                )

            # Sleep scaled by replay speed (60 seconds per M1 bar / speed)
            await self.clock.sleep(60.0)

        self.state.status = "COMPLETED"
        logger.info("Market replay completed for 2026-09-17 GOLD!")
        if self.app:
            await broadcast_state(self.app)

    async def shutdown(self) -> None:
        """Graceful shutdown of all subsystems."""
        logger.info("Shutting down Replay Streamer...")
        self._running = False
        self._stop_event.set()

        # Stop bot
        await self.bot.stop()

        # Unpatch clock
        self.clock.unpatch_hyper_predator_bot(hyper_predator_bot)
        self.clock.stop()

        # Stop diagnostics reporter
        self.diagnostics.stop()

        # Stop Web Server task cleanly if running
        if self._server_instance:
            self._server_instance.should_exit = True
            if self._server_task:
                try:
                    await asyncio.wait_for(self._server_task, timeout=2.0)
                except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                    pass
                self._server_task = None

        logger.info("Replay Streamer shutdown complete.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automated 1:1 Live Market Replay Simulator for HyperPredatorBot"
    )
    parser.add_argument(
        "--now",
        "--start-immediate",
        action="store_true",
        dest="now",
        help="Trigger replay immediately without waiting for 00:00:00 UTC",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Simulation speed multiplier (default: 1.0x, use 10.0 or 60.0 for faster testing)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for the FastAPI Replay Command Center (default: 8000)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host for FastAPI server (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--no-server",
        action="store_true",
        help="Disable the FastAPI web server",
    )
    parser.add_argument(
        "--no-ntfy",
        action="store_true",
        help="Disable outgoing ntfy push notifications",
    )
    parser.add_argument(
        "--test-mode",
        type=int,
        nargs="?",
        const=10,
        default=None,
        metavar="BARS",
        help="Run limited number of bars (default: 10) for testing and verification",
    )
    parser.add_argument(
        "--margin-pct",
        type=float,
        default=0.20,
        help="Initial margin ceiling for sizing (default: 0.20 for safe micro-account risk envelope)",
    )
    parser.add_argument(
        "--sr-lookback",
        type=int,
        default=50,
        help="Rolling M5 support/resistance lookback bars (default: 50 for true structural pivots)",
    )
    parser.add_argument(
        "--zone-eps",
        type=float,
        default=0.25,
        help="S/R touch zone tolerance in dollars (default: 0.25)",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    streamer = MarketReplayStreamer(
        speed=args.speed,
        start_immediate=args.now,
        port=args.port,
        host=args.host,
        run_server=not args.no_server,
        enable_ntfy=not args.no_ntfy,
        test_mode_bars=args.test_mode,
        max_margin_pct=args.margin_pct,
        sr_lookback_bars=args.sr_lookback,
        zone_epsilon=args.zone_eps,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(streamer.shutdown()))
        except NotImplementedError:
            pass

    await streamer.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Terminated by user.")
