import asyncio
import os
import sys
from pathlib import Path
from playwright.async_api import async_playwright

CHROME_PATH = "/usr/bin/google-chrome"
SESSION_PATH = "/root/lf_session.json"

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(
            headless=True,
            executable_path=CHROME_PATH if os.path.exists(CHROME_PATH) else None,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
        )
        ctx = await b.new_context(
            storage_state=SESSION_PATH if os.path.exists(SESSION_PATH) else None,
            viewport={"width": 1366, "height": 850}
        )
        page = await ctx.new_page()
        print("Navigating to trading chart...")
        await page.goto("https://my.litefinance.org/trading/chart?symbol=XAUUSD", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
        
        # Check if already demo
        content = await page.content()
        if "ACTIVATE REAL TRADING" in content.upper() or "DEMO ACCOUNT" in content.upper():
            print("Already in DEMO mode!")
            await ctx.storage_state(path=SESSION_PATH)
            await page.screenshot(path="/root/ict_sniper/data/demo_ready.png")
            await b.close()
            return True

        print("Currently in REAL mode. Opening user dropdown...")
        # Target user menu by text
        user_menu = page.locator("text='Emadodin Akbari'").first
        if await user_menu.count() == 0:
            user_menu = page.locator("text='REAL ACCOUNT'").first
        
        await user_menu.click(timeout=5000)
        print("Clicked user menu, waiting 1s...")
        await page.wait_for_timeout(1000)
        
        # Now click switch to demo button
        demo_btn = page.locator("#switch_mode_demo, [data-url='/switch/demo'], text='Activate demo trading'").first
        print(f"Found demo button: count={await demo_btn.count()}")
        await demo_btn.click(timeout=5000)
        print("Clicked Activate demo trading!")
        
        await page.wait_for_timeout(5000)
        await ctx.storage_state(path=SESSION_PATH)
        await page.screenshot(path="/root/ict_sniper/data/demo_activated.png")
        print("Demo switch complete, session saved!")
        
        # Verify
        new_content = await page.content()
        is_demo = "ACTIVATE REAL TRADING" in new_content.upper() or "DEMO ACCOUNT" in new_content.upper()
        print(f"Verification: is_demo = {is_demo}")
        await b.close()
        return is_demo

if __name__ == "__main__":
    asyncio.run(main())
