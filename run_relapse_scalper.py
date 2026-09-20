#!/usr/bin/env python3
"""
run_relapse_scalper.py
======================
Production & Paper Execution Runtime for the
5-Minute Market Flow & Relapse Scalper for Gold (XAUUSD).

Usage:
  python run_relapse_scalper.py --paper --dry-run
  python run_relapse_scalper.py --paper --initial-equity 65.0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from engine.execution_router import (
    ExecutionRouter,
    OrderSide,
    RiskInvariants,
    SimulatedBrokerVenue,
    emit_telemetry,
)
from engine.fsm import (
    DailyDrawdownGuard,
    RelapseFSM,
    RelapseState,
    XAUUSD_KILLZONES,
)
from macro.self_healing import (
    DEFAULT_NTFY_TOPIC,
    RecoveryAction,
    SelfHealingLLMGuard,
    push_ntfy_async,
)
from macro.slm_intuition import (
    EconomicCalendarFilter,
    SLMIntuitionEngine,
)
from engine.killzone import KillZoneGuard, XAUUSD_GOLD_KILLZONES

logger = logging.getLogger("runtime")
STATE_FILE = Path(__file__).resolve().parent / "data" / "relapse_scalper_state.json"
EMERGENCY_FLAG_FILE = Path(__file__).resolve().parent / "data" / "emergency_liquidate.flag"


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


async def fetch_hl_candles(coin: str = "PAXG", interval: str = "5m", n_bars: int = 60) -> Optional[pd.DataFrame]:
    """Fetches real-time candles snapshot directly from Hyperliquid venue info API."""
    try:
        now_ms = int(time.time() * 1000)
        bar_ms = 300_000 if interval == "5m" else 60_000
        start_ms = now_ms - (n_bars * bar_ms)
        url = "https://api.hyperliquid.xyz/info"
        payload = json.dumps({
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": interval, "startTime": start_ms},
        }).encode()

        def _sync_req() -> Any:
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                return json.loads(resp.read().decode())

        raw = await asyncio.to_thread(_sync_req)
        if isinstance(raw, list) and len(raw) >= 10:
            df = pd.DataFrame([{
                "open_time": int(c["t"]),
                "open": float(c["o"]),
                "high": float(c["h"]),
                "low": float(c["l"]),
                "close": float(c["c"]),
                "volume": float(c["v"]),
            } for c in raw])
            return df
    except Exception as exc:
        logger.debug("Hyperliquid candle fetch skipped: %s", exc)
    return None


async def dump_scalper_state(
    fsm: RelapseFSM,
    router: ExecutionRouter,
    venue: SimulatedBrokerVenue,
    kz_guard: KillZoneGuard,
    calendar: EconomicCalendarFilter,
    dd_guard: DailyDrawdownGuard,
    intuition: SLMIntuitionEngine,
    self_healing: Optional[SelfHealingLLMGuard] = None,
    mode: str = "PAPER",
) -> None:
    """Exports instantaneous engine state to JSON for panel.py mobile app and monitoring."""
    try:
        equity = await venue.get_equity()
        basket = router.active_basket
        in_blackout, blackout_reason = calendar.is_macro_blackout()
        kz_dash = kz_guard.multitz_dashboard()

        pos_info = None
        if basket and basket.is_active:
            pos_info = {
                "basket_id": basket.basket_id,
                "symbol": "GOLD",
                "side": basket.side.name,
                "aggregate_sz": round(basket.aggregate_sz, 4),
                "avg_entry_price": round(basket.avg_entry_price, 2),
                "stop_price": round(basket.current_stop_price, 2) if basket.current_stop_price else None,
                "breakeven_locked": basket.breakeven_locked,
                "unrealized_pnl": 0.0,
                "slices": [
                    {
                        "order_id": s.order_id,
                        "sz": s.sz,
                        "fill_price": s.fill_price,
                        "status": s.status.name,
                    }
                    for s in basket.slices
                ],
            }

        payload = {
            "engine": "5-Minute Market Flow & Relapse Scalper for Gold",
            "symbol": "XAUUSD / GOLD",
            "mode": mode,
            "updated_at": time.time(),
            "updated_iso": datetime.now(timezone.utc).isoformat(),
            "fsm_state": fsm.state.value,
            "equity": round(equity, 2),
            "starting_equity": router.risk.initial_account_equity,
            "pnl_dollar": round(equity - router.risk.initial_account_equity, 2),
            "pnl_pct": round(
                (equity - router.risk.initial_account_equity) / router.risk.initial_account_equity * 100,
                2,
            ),
            "drawdown": {
                "peak_equity": round(dd_guard.peak_day_equity, 2),
                "current_drawdown_pct": round(
                    (dd_guard.peak_day_equity - equity) / dd_guard.peak_day_equity * 100
                    if dd_guard.peak_day_equity > 0
                    else 0.0,
                    2,
                ),
                "max_drawdown_pct": 5.0,
                "killswitch_tripped": dd_guard.killswitch_tripped,
            },
            "killzone": kz_dash,
            "macro": {
                "in_blackout": in_blackout,
                "reason": blackout_reason,
                "window_minutes": 15,
            },
            "intuition": {
                "endpoint": f"http://{intuition.host}:{intuition.port}",
                "timeout_ms": 300,
                "grammar": '{"decision": "HOLD"|"EXIT"}',
            },
            "risk": {
                "leverage": router.risk.leverage,
                "margin_ceiling_pct": router.risk.max_margin_pct * 100,
                "max_initial_margin": round(equity * router.risk.max_margin_pct, 2),
                "min_sl_delta": router.risk.min_sl_delta,
                "max_sl_delta": router.risk.max_sl_delta,
                "breakeven_trigger_r": router.risk.breakeven_trigger_r,
            },
            "self_healing": {
                "enabled": True if self_healing else False,
                "endpoint": f"http://{self_healing.llm_host}:{self_healing.llm_port}" if self_healing else "",
                "timeout_ms": int(self_healing.timeout_sec * 1000) if self_healing else 500,
                "fixes_applied": len(self_healing.history) if self_healing else 0,
                "recent_fixes": [r.to_dict() for r in self_healing.history[-5:]] if self_healing else [],
            },
            "position": pos_info,
        }

        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = STATE_FILE.with_suffix(".tmp")
        with open(tmp_file, "w") as f:
            json.dump(payload, f, indent=2)
        tmp_file.replace(STATE_FILE)
    except Exception as e:
        logger.warning("Failed to dump scalper state: %s", e)


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="5-Minute Market Flow & Relapse Scalper for Gold (XAUUSD)"
    )
    parser.add_argument("--paper", action="store_true", default=True, help="Run in paper trading mode")
    parser.add_argument("--dry-run", action="store_true", help="Run self-diagnostic loop and exit")
    parser.add_argument("--initial-equity", type=float, default=65.0, help="Initial micro account equity ($65)")
    parser.add_argument("--llm-host", type=str, default="127.0.0.1", help="Local llama.cpp host")
    parser.add_argument("--llm-port", type=int, default=8080, help="Local llama.cpp port")
    parser.add_argument("--calendar-url", type=str, default=None, help="Live economic calendar API endpoint URL")
    parser.add_argument("--ntfy-topic", type=str, default=DEFAULT_NTFY_TOPIC, help="ntfy.sh topic for push notifications")
    parser.add_argument("--all-killzones", action="store_true", default=True, help="Trade all 4 institutional Gold killzones including Asian Open (00:00-03:30 UTC / 03:30-07:00 Tehran)")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose debug logging")
    args = parser.parse_args()

    setup_logging(args.verbose)
    mode_str = "PAPER" if args.paper else "LIVE"

    emit_telemetry(
        component="Runtime",
        event="SYSTEM_STARTING",
        data={
            "paper": args.paper,
            "dry_run": args.dry_run,
            "initial_equity": args.initial_equity,
            "llm_endpoint": f"http://{args.llm_host}:{args.llm_port}",
            "calendar_url": args.calendar_url,
            "ntfy_topic": args.ntfy_topic,
        },
    )

    # 1. Initialize Self-Healing LLM Guard
    self_healing = SelfHealingLLMGuard(
        llm_host=args.llm_host,
        llm_port=args.llm_port,
        ntfy_topic=args.ntfy_topic,
    )

    # 2. Initialize Broker Venue & Execution Router
    venue = SimulatedBrokerVenue(initial_equity=args.initial_equity)
    risk = RiskInvariants(
        coin="GOLD",
        leverage=100.0,
        max_margin_pct=0.20,
        min_sl_delta=1.00,
        max_sl_delta=1.50,
        wick_buffer=0.12,
        breakeven_trigger_r=1.5,
        breakeven_lock_offset=0.10,
        initial_account_equity=args.initial_equity,
    )
    router = ExecutionRouter(venue=venue, risk=risk)

    # 3. Initialize Guards & Fundamental Filters
    active_zones = XAUUSD_GOLD_KILLZONES if args.all_killzones else XAUUSD_KILLZONES
    kz_guard = KillZoneGuard(zones=active_zones)
    calendar_filter = EconomicCalendarFilter(calendar_api_url=args.calendar_url)
    await calendar_filter.start()
    dd_guard = DailyDrawdownGuard(max_drawdown_pct=0.05)

    # 4. Initialize Local SLM Intuition Engine
    intuition_engine = SLMIntuitionEngine(
        host=args.llm_host,
        port=args.llm_port,
        timeout_sec=0.300,
    )

    # 5. Initialize Core Relapse FSM
    fsm = RelapseFSM(
        symbol="XAUUSD",
        execution_router=router,
        intuition_engine=intuition_engine,
        calendar_filter=calendar_filter,
        drawdown_guard=dd_guard,
        killzone_guard=kz_guard,
    )

    # Export state for mobile dashboard immediately
    await dump_scalper_state(
        fsm=fsm,
        router=router,
        venue=venue,
        kz_guard=kz_guard,
        calendar=calendar_filter,
        dd_guard=dd_guard,
        intuition=intuition_engine,
        self_healing=self_healing,
        mode=mode_str,
    )

    emit_telemetry(
        component="Runtime",
        event="ALL_COMPONENTS_INITIALIZED",
        data={
            "fsm_state": fsm.state.value,
            "equity": await venue.get_equity(),
            "killzone_status": kz_guard.zone_label(),
        },
    )

    # If dry run, verify self-diagnostics and exit cleanly
    if args.dry_run:
        emit_telemetry(
            component="Runtime",
            event="DRY_RUN_DIAGNOSTIC_PASSED",
            data={"status": "NOMINAL", "components": ["Router", "FSM", "Calendar", "Intuition", "SelfHealing", "Guard"]},
        )
        await calendar_filter.stop()
        await intuition_engine.close()
        await self_healing.close()
        return 0

    # Dispatch startup push alert to ntfy
    await push_ntfy_async(
        title="🚀 5M Gold Scalper Online",
        message=(
            f"Mode: {mode_str} | Equity: ${args.initial_equity:.2f}\n"
            f"Target: Scale $65 -> $10,000 (100x Hyperliquid CLOB)\n"
            f"Active Window: {kz_guard.zone_label()}\n"
            f"Self-Healing LLM: Online (http://{args.llm_host}:{args.llm_port})"
        ),
        tags="rocket,gold,shield",
        priority="high",
        topic=args.ntfy_topic,
    )

    # Handle graceful termination
    stop_event = asyncio.Event()

    def _sig_handler(*_) -> None:
        emit_telemetry(component="Runtime", event="SHUTDOWN_SIGNAL_RECEIVED", data={})
        stop_event.set()

    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, _sig_handler)
        except NotImplementedError:
            pass

    # Lifecycle state tracking for event-driven ntfy pushes
    prev_kz_active, _ = kz_guard.check()
    prev_blackout, _ = calendar_filter.is_macro_blackout()
    prev_basket_id: Optional[str] = None
    prev_be_locked: bool = False
    last_dump = time.time()
    last_candle_poll = 0.0

    try:
        while not stop_event.is_set():
            try:
                # 1. Check for emergency liquidation request from panel or CLI
                if EMERGENCY_FLAG_FILE.exists():
                    try:
                        EMERGENCY_FLAG_FILE.unlink()
                    except Exception:
                        pass
                    if router.active_basket_id:
                        emit_telemetry(
                            component="Runtime",
                            event="EMERGENCY_LIQUIDATION_TRIGGERED",
                            data={"source": "panel_flag"},
                        )
                        await router.close_basket(reason="EMERGENCY_PANEL_LIQUIDATE")
                        await push_ntfy_async(
                            title="🚨 Emergency Basket Liquidation",
                            message="Panel emergency liquidation triggered. All sliced tickets closed immediately.",
                            tags="rotating_light,skull",
                            priority="urgent",
                            topic=args.ntfy_topic,
                        )
                        await dump_scalper_state(
                            fsm=fsm,
                            router=router,
                            venue=venue,
                            kz_guard=kz_guard,
                            calendar=calendar_filter,
                            dd_guard=dd_guard,
                            intuition=intuition_engine,
                            self_healing=self_healing,
                            mode=mode_str,
                        )

                # 2. Check and notify Killzone transitions
                now_ts = time.time()
                curr_kz_active, curr_kz_name = kz_guard.check(now_ts)
                if curr_kz_active != prev_kz_active:
                    prev_kz_active = curr_kz_active
                    kz_title = f"🏛️ Institutional Killzone {'ACTIVE' if curr_kz_active else 'CLOSED'}"
                    kz_msg = f"Session: {curr_kz_name}\nUTC: {datetime.now(timezone.utc).strftime('%H:%M:%S')}\nTehran: {kz_guard.multitz_dashboard()['clocks']['Tehran (Local)']['time_str']}"
                    await push_ntfy_async(
                        title=kz_title,
                        message=kz_msg,
                        tags="classical_building,clock8" if curr_kz_active else "clock1",
                        priority="default",
                        topic=args.ntfy_topic,
                    )

                # 3. Check and notify Macro Blackout transitions
                curr_blackout, curr_reason = calendar_filter.is_macro_blackout(now_ts)
                if curr_blackout != prev_blackout:
                    prev_blackout = curr_blackout
                    await push_ntfy_async(
                        title=f"⚠️ Macro Lock {'ENGAGED' if curr_blackout else 'RELEASED'}",
                        message=f"{curr_reason}\nTrading halted around high-impact release.",
                        tags="warning,calendar" if curr_blackout else "white_check_mark",
                        priority="urgent" if curr_blackout else "default",
                        topic=args.ntfy_topic,
                    )

                # 4. Ingest live market candles & advance Relapse FSM
                if now_ts - last_candle_poll >= 10.0:
                    last_candle_poll = now_ts
                    df_5m = await fetch_hl_candles(coin="PAXG", interval="5m", n_bars=60)
                    if df_5m is not None and not df_5m.empty:
                        curr_px = float(df_5m["close"].iloc[-1])
                        venue.set_market_price("GOLD", curr_px)
                        await fsm.on_candle(df_5m)

                        # If in active trade, poll 1M microstructure for LLM intuition exit
                        if fsm.state == RelapseState.IN_TRADE:
                            df_1m = await fetch_hl_candles(coin="PAXG", interval="1m", n_bars=30)
                            if df_1m is not None and not df_1m.empty:
                                await fsm.on_1m_bar_update(df_1m)

                # 5. Check and notify Basket lifecycle events (Entry, BE Lock, Exit)
                active_basket = router.active_basket
                if active_basket and active_basket.is_active:
                    if active_basket.basket_id != prev_basket_id:
                        prev_basket_id = active_basket.basket_id
                        prev_be_locked = active_basket.breakeven_locked
                        await push_ntfy_async(
                            title=f"⚡ GOLD Order Dispatched: 3 Slices {active_basket.side.name}",
                            message=(
                                f"Symbol: GOLD / XAUUSD\n"
                                f"Total Size: {active_basket.aggregate_sz:.2f} oz\n"
                                f"Avg Entry: ${active_basket.avg_entry_price:.2f}\n"
                                f"Detached SL: ${active_basket.current_stop_price:.2f} (Reduce-Only)"
                            ),
                            tags="zap,moneybag",
                            priority="high",
                            topic=args.ntfy_topic,
                        )
                    elif active_basket.breakeven_locked and not prev_be_locked:
                        prev_be_locked = True
                        await push_ntfy_async(
                            title="🎯 Breakeven Locked (+1.5R Achieved)",
                            message=(
                                f"Position secured at +1.5R floating profit.\n"
                                f"Stop moved to Entry + $0.10 (${active_basket.current_stop_price:.2f}).\n"
                                f"Trade is now strictly risk-free!"
                            ),
                            tags="dart,lock",
                            priority="high",
                            topic=args.ntfy_topic,
                        )
                else:
                    if prev_basket_id is not None:
                        # Basket was closed
                        prev_basket_id = None
                        prev_be_locked = False
                        eq = await venue.get_equity()
                        await push_ntfy_async(
                            title="🏁 Position Closed",
                            message=f"Current Account Equity: ${eq:.2f}",
                            tags="checkered_flag,money_with_wings",
                            priority="default",
                            topic=args.ntfy_topic,
                        )

                # 5. Periodic state export for mobile dashboard
                now = time.time()
                if now - last_dump >= 2.0:
                    await dump_scalper_state(
                        fsm=fsm,
                        router=router,
                        venue=venue,
                        kz_guard=kz_guard,
                        calendar=calendar_filter,
                        dd_guard=dd_guard,
                        intuition=intuition_engine,
                        self_healing=self_healing,
                        mode=mode_str,
                    )
                    last_dump = now

            except Exception as loop_exc:
                # Intercept exception with Autonomous Self-Healing Guard
                await self_healing.handle_exception(
                    exc=loop_exc,
                    component="RelapseRuntimeLoop",
                    context={
                        "fsm_state": fsm.state.value,
                        "basket_id": router.active_basket_id,
                        "equity": await venue.get_equity(),
                    },
                    fsm=fsm,
                    router=router,
                    venue=venue,
                )

            await asyncio.sleep(0.5)

    finally:
        emit_telemetry(component="Runtime", event="CLEANING_UP", data={})
        if router.active_basket_id:
            await router.close_basket(reason="SHUTDOWN")
        await dump_scalper_state(
            fsm=fsm,
            router=router,
            venue=venue,
            kz_guard=kz_guard,
            calendar=calendar_filter,
            dd_guard=dd_guard,
            intuition=intuition_engine,
            self_healing=self_healing,
            mode="STOPPED",
        )
        await calendar_filter.stop()
        await intuition_engine.close()
        await self_healing.close()
        emit_telemetry(component="Runtime", event="SYSTEM_STOPPED", data={})

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
