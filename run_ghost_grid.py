"""
Ghost Grid Runner Script
Connects to LiteFinance Demo and runs the GhostEngine.
"""

import asyncio
import os
import argparse
import logging
import signal
import sys
from pathlib import Path
import urllib.request

from engine.litefinance_gateway import LiteFinanceGateway
from ghost_grid.ghost_engine import GhostEngine

BANNER = r"""
   ____ _               _     ____      _     _ 
  / ___| |__   ___  ___| |_  / ___|_ __(_)___| |
 | |  _| '_ \ / _ \/ __| __| | |  _| '__| / _` |
 | |_| | | | | (_) \__ \ |_  | |_| | |  | \__, |
  \____|_| |_|\___/|___/\__|  \____|_|  |_|___/ 
                                                
    [ LITEFINANCE MR P FX SCALPER  (DEMO default, REAL needs --allow-real) ]
"""

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)

# Add file handler dynamically once logs dir is ready
Path("logs").mkdir(exist_ok=True)
file_handler = logging.FileHandler(Path("logs/ghost_grid.log"))
file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
logging.getLogger().addHandler(file_handler)

logger = logging.getLogger("GhostRunner")

# Global engine ref for graceful shutdown
engine_ref = None
stop_event = asyncio.Event()

try:
    from bark_integration import send_alert
except ImportError:
    send_alert = None

def notify(title: str, message: str, priority: str = "high"):
    try:
        clean_msg = " · ".join([line.strip() for line in message.strip().splitlines() if line.strip()])
        clean_title = title.strip()
        if send_alert:
            send_alert(title=clean_title, message=clean_msg, priority=priority)
    except Exception as e:
        logger.error(f"Failed to send ntfy notification: {e}")

async def shutdown():
    logger.info("Shutdown signal received! Flattening positions and exiting...")
    if engine_ref and engine_ref.active_positions:
        try:
            logger.info("Attempting emergency flatten...")
            await engine_ref.gateway.flatten_all_positions()
        except Exception as e:
            logger.error(f"Emergency flatten failed: {e}")
            
    if engine_ref:
        engine_ref.save_state()
        
    logger.info("Shutdown complete.")
    stop_event.set()

async def close_and_exit(gateway):
    """Close the browser and hard-exit: a lingering Chromium subprocess used to hang shutdown until SIGKILL."""
    logger.info("Main loop stopped; closing gateway...")
    try:
        await asyncio.wait_for(gateway.close(), timeout=10)
    except Exception as e:
        logger.warning("Gateway close: %s", e)
    logging.shutdown()
    os._exit(0)


async def main():
    global engine_ref
    
    print(BANNER)
    
    parser = argparse.ArgumentParser(description="Ghost Grid Runner")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    parser.add_argument("--allow-real", action="store_true",
                        help="Explicitly authorize trading on the REAL account (refuses to start on REAL without it)")
    args = parser.parse_args()
    
    logging.getLogger().setLevel(args.log_level.upper())
    
    logger.info("Initializing LiteFinance Gateway...")
    gateway = LiteFinanceGateway()
    success = await gateway.initialize()
    
    if not success:
        logger.error("Failed to initialize gateway.")
        return
        
    cur_mode = await gateway.get_account_mode()
    logger.info(f"Gateway initialized. Current account mode: {cur_mode}")
    
    account = await gateway.get_account_snapshot()
    logger.info(f"Initial Account Snapshot: Mode={cur_mode}, Balance={account.balance}, Equity={account.equity}")
    
    if cur_mode == "REAL" and not args.allow_real:
        logger.error("Session is on the REAL account but --allow-real was not given. Refusing to trade.")
        await gateway.close()
        return
    if cur_mode not in ("DEMO", "REAL"):
        logger.error("Account mode could not be verified (%s). Refusing to trade.", cur_mode)
        await gateway.close()
        return

    engine = GhostEngine(gateway)
    engine_ref = engine
    engine.target_mode = cur_mode
    if cur_mode == "DEMO":
        # Demo trades its own broker balance. GHOST_MIRROR_BALANCE=<usd> makes it size as if the demo held
        # that amount (e.g. the real account balance) so the engines can be rehearsed at the real size.
        mirror = os.getenv("GHOST_MIRROR_BALANCE")
        engine.broker_baseline = account.balance
        engine.cycle_start_balance = float(mirror) if mirror else account.balance
        engine.live_real_balance = engine.cycle_start_balance
        engine.save_state()
    if cur_mode == "REAL":
        # Real balance is always the broker's; reset any stale/simulated mirror state.
        engine.live_real_balance = account.balance
        engine.cycle_start_balance = account.balance
        engine.save_state()
    
    notify(
        title=f"👻 Ghost Grid Armed ({engine.target_mode})",
        message=(f"LiteFinance {engine.target_mode} · broker balance ${account.balance:.2f} · tracked ${engine.live_real_balance:.2f} · "
                 f"strategy {os.getenv('GHOST_STRATEGY', 'mrp')} · risk/basket {float(os.getenv('GHOST_RISK_PCT', '0.15'))*100:.0f}% · handoff to Apex at ${float(os.getenv('GHOST_HANDOFF_BALANCE', '100')):.0f} · "
                 f"warming up {engine.min_candles} candles ({len(engine.candles_1m)} restored)"),
        priority="high"
    )
    
    # Graceful shutdown handler
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, lambda: asyncio.create_task(shutdown()))
    loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(shutdown()))
    
    logger.info("Starting main event loop...")
    handoff_at = float(os.getenv("GHOST_HANDOFF_BALANCE", "100"))
    while not stop_event.is_set():
        try:
            quote = await gateway.wait_for_quote(timeout=5.0)
            if quote:
                await engine.tick(quote)
            # Phase 2: once flat and equity has crossed the handoff balance, stop so Apex Trinity takes the account.
            if not engine.active_positions and not engine.halted and engine.live_real_balance >= handoff_at:
                logger.info("🎯 Handoff balance $%.2f reached (equity $%.2f). Stopping Ghost Grid for Apex Trinity.", handoff_at, engine.live_real_balance)
                notify(title="🎯 Ghost Grid → Apex Trinity", message=f"Balance ${engine.live_real_balance:.2f} ≥ ${handoff_at:.2f}. Handing the account to Apex.", priority="high")
                Path("data/state").mkdir(parents=True, exist_ok=True)
                Path("data/state/handoff_to_apex").write_text(str(engine.live_real_balance))
                try:
                    import json as _json
                    mf = Path("data/account_mode.json")
                    cur = _json.loads(mf.read_text()) if mf.exists() else {}
                    cur["real_balance"] = round(engine.live_real_balance, 2)
                    mf.write_text(_json.dumps(cur, indent=2))
                except Exception as e:
                    logger.warning("Could not update account_mode.json: %s", e)
                engine.save_state()
                stop_event.set()
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)
            await asyncio.sleep(1)
    await close_and_exit(gateway)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
