"""
Ghost Grid Runner Script
Connects to LiteFinance Demo and runs the GhostEngine.
"""

import asyncio
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
                                                
    [ LITEFINANCE DEMO ACCUMULATION SCALPER ]
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
    sys.exit(0)

async def main():
    global engine_ref
    
    print(BANNER)
    
    parser = argparse.ArgumentParser(description="Ghost Grid Runner")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
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
    
    engine = GhostEngine(gateway)
    engine_ref = engine
    if cur_mode in ("DEMO", "REAL"):
        engine.target_mode = cur_mode
    
    notify(
        title=f"👻 Ghost Grid Armed ({engine.target_mode})",
        message=f"LiteFinance {engine.target_mode} · Balance: ${account.balance:.2f} · Grid Scalper Active",
        priority="high"
    )
    
    # Graceful shutdown handler
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, lambda: asyncio.create_task(shutdown()))
    loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(shutdown()))
    
    logger.info("Starting main event loop...")
    while True:
        try:
            quote = await gateway.wait_for_quote(timeout=5.0)
            if quote:
                await engine.tick(quote)
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)
            await asyncio.sleep(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
