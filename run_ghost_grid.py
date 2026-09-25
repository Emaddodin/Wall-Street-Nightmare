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

def notify(message):
    try:
        url = "https://ntfy.sh/tbt_ghost_grid"
        req = urllib.request.Request(url, data=message.encode('utf-8'), method='POST')
        urllib.request.urlopen(req, timeout=5)
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
        
    mode = await gateway.get_account_mode()
    if mode != "DEMO":
        logger.warning(f"Account is in {mode} mode. Requesting switch to DEMO mode...")
        sw_res = await gateway.switch_account_mode("DEMO")
        if not sw_res.get("success"):
            logger.critical(f"FATAL: Failed to switch to DEMO mode: {sw_res.get('error')}. Aborting for safety.")
            return
        mode = await gateway.get_account_mode()
        if mode != "DEMO":
            logger.critical(f"FATAL: Account is still in {mode} mode after switch attempt! Aborting.")
            return
        
    logger.info("Gateway verified strictly in DEMO mode.")
    
    account = await gateway.get_account_snapshot()
    logger.info(f"Initial Account Snapshot: Balance={account.balance}, Equity={account.equity}")
    
    engine = GhostEngine(gateway)
    engine_ref = engine
    
    notify(f"👻 Ghost Grid started. Balance: ${account.balance}")
    
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
