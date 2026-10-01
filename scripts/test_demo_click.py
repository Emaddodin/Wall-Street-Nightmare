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
logger = logging.getLogger("test_demo_click")

async def test_order_flow():
    gw = LiteFinanceGateway(
        session_file="/root/lf_session.json",
        chrome_path="/usr/bin/google-chrome",
        proxy=None,
    )
    logger.info("Initializing gateway...")
    ok = await gw.initialize()
    if not ok:
        logger.error("Failed to initialize gateway")
        return

    acc_before = await gw.get_account_snapshot(force_fresh=True)
    logger.info("Account before: Balance=%.2f, AssetsUsed=%.2f", acc_before.balance, acc_before.assets_used)

    # Let's inspect the exact form and button state before clicking
    state1 = await gw._page.evaluate("""() => {
        const buyLabel = document.querySelector('label[for="trade_buy_1"]');
        const sellLabel = document.querySelector('label[for="trade_sell_1"]');
        const buyRadio = document.querySelector('#trade_buy_1');
        const sellRadio = document.querySelector('#trade_sell_1');
        const volInput = document.querySelector('#volume_value_1');
        const submitBtn = document.querySelector('button.js_trade_action_open, button[type="submit"].btn_large');
        
        return {
            buyChecked: buyRadio ? buyRadio.checked : null,
            sellChecked: sellRadio ? sellRadio.checked : null,
            volValue: volInput ? volInput.value : null,
            btnText: submitBtn ? submitBtn.innerText.trim() : null,
            btnClass: submitBtn ? submitBtn.className : null,
            btnDisabled: submitBtn ? submitBtn.disabled : null,
            btnVisible: submitBtn ? (submitBtn.getBoundingClientRect().width > 0 && submitBtn.getBoundingClientRect().height > 0) : false,
            btnRect: submitBtn ? {
                x: submitBtn.getBoundingClientRect().x,
                y: submitBtn.getBoundingClientRect().y,
                w: submitBtn.getBoundingClientRect().width,
                h: submitBtn.getBoundingClientRect().height,
            } : null
        };
    }""")
    logger.info("Initial DOM state: %s", json.dumps(state1, indent=2))

    # Test selecting BUY tab
    logger.info("Selecting BUY tab...")
    await gw._page.click('label[for="trade_buy_1"]', force=True)
    await asyncio.sleep(0.3)

    # Test setting volume
    logger.info("Setting volume to 0.01...")
    await gw._page.fill('#volume_value_1', '0.01')
    await asyncio.sleep(0.2)

    # Check state after BUY and volume selection
    state2 = await gw._page.evaluate("""() => {
        const buyRadio = document.querySelector('#trade_buy_1');
        const volInput = document.querySelector('#volume_value_1');
        const submitBtn = document.querySelector('button.js_trade_action_open, button[type="submit"].btn_large');
        return {
            buyChecked: buyRadio ? buyRadio.checked : null,
            volValue: volInput ? volInput.value : null,
            btnText: submitBtn ? submitBtn.innerText.trim() : null,
            btnClass: submitBtn ? submitBtn.className : null,
            btnDisabled: submitBtn ? submitBtn.disabled : null,
        };
    }""")
    logger.info("DOM state after BUY select: %s", json.dumps(state2, indent=2))

    # Now test clicking the submit button directly via JS
    logger.info("Inspecting and clicking submit button via JS...")
    res = await gw._page.evaluate("""() => {
        const btns = Array.from(document.querySelectorAll('button.js_trade_action_open, button[type="submit"].btn_large, button[class*="js_trade_action"]'));
        const info = btns.map((b, i) => ({
            i,
            text: b.innerText.trim(),
            cls: b.className,
            w: b.getBoundingClientRect().width,
            h: b.getBoundingClientRect().height,
            x: b.getBoundingClientRect().x,
            y: b.getBoundingClientRect().y,
        }));
        
        const target = btns.find(b => b.getBoundingClientRect().width > 0 && b.getBoundingClientRect().height > 0);
        if (target) {
            target.scrollIntoViewIfNeeded ? target.scrollIntoViewIfNeeded() : target.scrollIntoView();
            target.click();
            return { success: true, text: target.innerText.trim(), cls: target.className, all: info };
        }
        return { success: false, error: 'NO_VISIBLE_BUTTON', all: info };
    }""")
    logger.info("JS click result: %s", json.dumps(res, indent=2))

    logger.info("Clicked! Waiting 2s for broker response...")
    await asyncio.sleep(2.0)

    # Check for confirmation modals or popups
    popups = await gw._page.evaluate("""() => {
        const ps = Array.from(document.querySelectorAll('.popup, .modal, .toast, .notification, .alert, [class*="popup"], [class*="modal"]')).filter(p => p.getBoundingClientRect().width > 0);
        return ps.map(p => ({ tag: p.tagName, class: p.className, text: (p.innerText || '').slice(0, 200) }));
    }""")
    logger.info("Popups detected: %s", json.dumps(popups, indent=2))

    acc_after = await gw.get_account_snapshot(force_fresh=True)
    logger.info("Account after click: Balance=%.2f, AssetsUsed=%.2f", acc_after.balance, acc_after.assets_used)

    trades = await gw._page.evaluate("""() => {
        const rows = Array.from(document.querySelectorAll('.js_open_trades tr, [class*="open_trades"] tr, .portfolio_table tr'));
        return rows.map(r => (r.innerText || '').replace(/\\n+/g, ' | '));
    }""")
    logger.info("Open trades count: %d, rows: %s", len(trades), trades)

    # Clean up / flatten if opened
    if acc_after.assets_used > 0:
        logger.info("Order successfully opened! Flattening now...")
        flat = await gw.flatten_all_positions()
        logger.info("Flatten result: %s", flat)
        await asyncio.sleep(1.5)
        acc_final = await gw.get_account_snapshot(force_fresh=True)
        logger.info("Final account: AssetsUsed=%.2f", acc_final.assets_used)

    await gw.close()

if __name__ == "__main__":
    asyncio.run(test_order_flow())
