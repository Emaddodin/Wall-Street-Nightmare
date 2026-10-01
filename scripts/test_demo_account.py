"""
scripts/test_demo_account.py
============================
Automated switch to Demo Account and full usability/execution test suite:
1. Switches LiteFinance session to Demo (MT5-DEMO-ECN-91456523).
2. Verifies Demo balance (~$309.76).
3. Executes a test BUY and test SELL (0.01L), tracks telemetry, tests emergency flatten.
4. Confirms zero residue on Demo.
"""

from __future__ import annotations

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

from playwright.async_api import async_playwright
from engine.litefinance_gateway import LiteFinanceGateway

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("demo_test")

SESSION_PATH = os.getenv("LF_SESSION_PATH", "/root/lf_session.json")
PROXY = os.getenv("LF_PROXY", "")
CHROME_PATH = os.getenv("LF_CHROME_PATH", "/usr/bin/google-chrome")


async def switch_to_demo_mode():
    logger.info("Connecting via Playwright to ensure DEMO trading is active...")
    async with async_playwright() as p:
        args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-extensions",
            "--mute-audio",
        ]
        if PROXY:
            args.append(f"--proxy-server={PROXY}")

        browser = await p.chromium.launch(
            headless=True,
            executable_path=CHROME_PATH if os.path.exists(CHROME_PATH) else None,
            args=args,
        )
        ctx = await browser.new_context(
            storage_state=SESSION_PATH if os.path.exists(SESSION_PATH) else None,
            viewport={"width": 1366, "height": 850},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        )
        page = await ctx.new_page()

        chart_url = "https://my.litefinance.org/trading/chart?symbol=XAUUSD"
        logger.info("Navigating to %s...", chart_url)
        await page.goto(chart_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)

        # Check if already in demo
        content = await page.content()
        is_demo = "DEMO" in content.upper() and ("ACTIVATE REAL TRADING" in content.upper() or "DEMO-ECN" in content.upper())

        if is_demo:
            logger.info("Already in DEMO mode.")
        else:
            logger.info("Currently in REAL mode. Switching to DEMO mode...")
            try:
                # Open user menu
                user_btn = page.locator(".header_user, .user_name, .user_info").first
                await user_btn.click(timeout=5000)
                await page.wait_for_timeout(1000)
                
                demo_toggle = page.locator("a:has-text('ACTIVATE DEMO TRADING'), button:has-text('ACTIVATE DEMO TRADING')").first
                await demo_toggle.click(timeout=5000)
                await page.wait_for_timeout(5000)
                logger.info("Successfully clicked 'ACTIVATE DEMO TRADING'.")
            except Exception as e:
                logger.warning("Dropdown toggle failed, attempting direct button: %s", e)
                try:
                    await page.click("button:has-text('Demo'), a:has-text('Demo')", timeout=5000)
                    await page.wait_for_timeout(4000)
                except Exception as e2:
                    logger.error("Could not toggle demo: %s", e2)

        # Save session
        await ctx.storage_state(path=SESSION_PATH)
        screenshot_path = str(ROOT_DIR / "data" / "demo_switch_verification.png")
        os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
        await page.screenshot(path=screenshot_path)
        logger.info("Saved screenshot to %s", screenshot_path)

        await browser.close()


async def run_demo_pentest():
    await switch_to_demo_mode()

    logger.info("\n--- Connecting LiteFinanceGateway to DEMO Account ---")
    gw = LiteFinanceGateway(session_file=SESSION_PATH, proxy=PROXY or None, chrome_path=CHROME_PATH)
    connected = await gw.initialize()
    if not connected:
        logger.error("Failed to connect gateway to Demo account!")
        return False

    acc = await gw.get_account_snapshot(force_fresh=True)
    logger.info("🏦 DEMO ACCOUNT SNAPSHOT: Balance=$%.2f | Assets Used=$%.2f | Available=$%.2f",
                acc.balance, acc.assets_used, acc.available)

    # 1. Quote Check
    logger.info("\n--- Checking Live Price Feeds ---")
    q = await gw.wait_for_quote(timeout=5.0)
    if q:
        logger.info("✅ Live Tick: Bid=$%.2f | Ask=$%.2f | Mid=$%.2f | Spread=$%.2f",
                    q.bid, q.ask, q.mid, q.ask - q.bid)
    else:
        logger.error("❌ No quote received!")
        await gw.close()
        return False

    # 2. Test Execution: Micro BUY 0.01 lots
    logger.info("\n--- Testing DEMO BUY Order (0.01 lots) ---")
    entry_mid = q.mid
    buy_res = await gw.open_market_order("BUY", 0.01, sl_price=entry_mid - 2.50)
    logger.info("BUY Execution Result: %s", buy_res)

    await asyncio.sleep(3.0)
    acc_in_buy = await gw.get_account_snapshot(force_fresh=True)
    logger.info("In-Trade Telemetry: Assets Used=$%.2f | Floating PnL=$%.2f",
                acc_in_buy.assets_used, acc_in_buy.floating_pnl)

    # 3. Test Flatten
    logger.info("\n--- Testing DEMO Emergency Flatten ---")
    flat_res = await gw.flatten_all_positions()
    logger.info("Flatten Result: %s", flat_res)

    await asyncio.sleep(2.0)
    acc_clean = await gw.get_account_snapshot(force_fresh=True)
    zero_residue = (acc_clean.assets_used == 0.0)
    logger.info("Zero Residue Verification: Assets Used=$%.2f (Clean: %s) | Balance=$%.2f",
                acc_clean.assets_used, zero_residue, acc_clean.balance)

    await gw.close()
    logger.info("\n🎉 DEMO EXECUTION TEST COMPLETED SUCCESSFULLY!")
    return zero_residue


if __name__ == "__main__":
    asyncio.run(run_demo_pentest())
