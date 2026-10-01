import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from engine.litefinance_gateway import LiteFinanceGateway

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("inspect_portfolio")

async def inspect():
    gw = LiteFinanceGateway(
        session_file="/root/lf_session.json",
        chrome_path="/usr/bin/google-chrome",
        proxy=None,
    )
    ok = await gw.initialize()
    if not ok:
        logger.error("Failed to connect")
        return

    acc = await gw.get_account_snapshot(force_fresh=True)
    logger.info("Account: Balance=%.2f, AssetsUsed=%.2f, PnL=%.2f", acc.balance, acc.assets_used, acc.floating_pnl)

    # Inspect portfolio DOM elements
    info = await gw._page.evaluate("""() => {
        // Try opening portfolio if there's a portfolio tab/button
        const portBtns = Array.from(document.querySelectorAll('a, button, div, span')).filter(el => {
            const txt = (el.innerText || '').trim().toUpperCase();
            return (txt === 'PORTFOLIO' || txt.includes('PORTFOLIO')) && el.getBoundingClientRect().width > 0;
        });
        if (portBtns.length > 0) {
            portBtns[0].click();
        }

        const buttons = Array.from(document.querySelectorAll('button, a')).map(b => ({
            tag: b.tagName,
            cls: b.className,
            text: (b.innerText || '').trim().slice(0, 40),
            w: Math.round(b.getBoundingClientRect().width),
            h: Math.round(b.getBoundingClientRect().height),
            x: Math.round(b.getBoundingClientRect().x),
            y: Math.round(b.getBoundingClientRect().y),
        })).filter(b => b.w > 0 && (
            b.cls.includes('close') || 
            b.text.toLowerCase().includes('close') || 
            b.cls.includes('trade_action') ||
            b.cls.includes('portfolio')
        ));

        const openTradeElements = Array.from(document.querySelectorAll('.js_open_trades tr, [class*="open_trades"] tr, .portfolio_table tr, .trade_item, [class*="portfolio_row"]')).map(el => ({
            cls: el.className,
            text: (el.innerText || '').replace(/\\s+/g, ' ').slice(0, 100)
        }));

        return { buttons, openTradeElements, portButtonsCount: portBtns.length };
    }""")

    logger.info("Portfolio inspect: %s", json.dumps(info, indent=2))

    # Also capture screenshot
    screenshot_path = str(ROOT_DIR / "data" / "portfolio_debug.png")
    os.makedirs(ROOT_DIR / "data", exist_ok=True)
    await gw._page.screenshot(path=screenshot_path)
    logger.info("Screenshot saved to %s", screenshot_path)

    await gw.close()

if __name__ == "__main__":
    asyncio.run(inspect())
