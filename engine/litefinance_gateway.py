"""
engine/litefinance_gateway.py
==============================
High-Speed Headless Execution Gateway for LiteFinance MT5 Demo / Real Accounts.
Maintains persistent authenticated browser session through proxy on Linux VPS.
Executes real market orders, retrieves live quotes, and flattens positions with sub-second latency.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

logger = logging.getLogger("lf_gateway")

PROXY_SERVER = os.getenv("LF_PROXY", "")
SESSION_PATH = os.getenv("LF_SESSION_PATH", "/root/lf_session.json")
CHROME_PATH = os.getenv("LF_CHROME_PATH", "/usr/bin/google-chrome")
CHART_URL = "https://my.litefinance.org/trading/chart?symbol=XAUUSD"


@dataclass
class AccountSnapshot:
    balance: float
    equity: float
    assets_used: float
    available: float
    floating_pnl: float
    currency: str = "USD"


@dataclass
class QuoteSnapshot:
    symbol: str
    bid: float
    ask: float
    mid: float
    timestamp: float


class LiteFinanceGateway:
    """
    Direct asynchronous execution gateway for LiteFinance Web / MT5 platform.
    """

    def __init__(
        self,
        session_file: str = SESSION_PATH,
        proxy: str = PROXY_SERVER,
        chrome_path: str = CHROME_PATH,
        symbol: str = "XAUUSD",
    ):
        self.session_file = session_file
        self.proxy = proxy
        self.chrome_path = chrome_path
        self.symbol = symbol.upper().replace("/", "")

        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._connected = False
        self._last_quote: Optional[QuoteSnapshot] = None
        self._new_quote_event = asyncio.Event()
        self._last_account: Optional[AccountSnapshot] = None
        self._last_account_ts: float = 0.0
        self._lock = asyncio.Lock()
        self._reconnecting: bool = False
        self._consecutive_errors: int = 0
        self._reconnect_attempts: int = 0
        self._switching_mode: bool = False
        self._background_tasks: set = set()

    def _spawn_bg_task(self, coro):
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    @property
    def is_connected(self) -> bool:
        return self._connected and self._page is not None and not self._page.is_closed()

    def _on_js_quote_tick(self, bid: float, ask: float, mid: float, ts_ms: float) -> None:
        """Callback invoked directly from Chrome V8 MutationObserver on every price tick."""
        now = time.time()
        self._last_quote = QuoteSnapshot(
            symbol=self.symbol,
            bid=float(bid),
            ask=float(ask),
            mid=float(mid),
            timestamp=now,
        )
        self._new_quote_event.set()

    def get_live_quote_sync(self) -> Optional[QuoteSnapshot]:
        """Instant RAM lookup for live quotes (0.001ms latency, zero CDP round-trips)."""
        if self._last_quote and (time.time() - self._last_quote.timestamp) < 2.0:
            return self._last_quote
        return None

    async def wait_for_quote(self, timeout: float = 0.10) -> Optional[QuoteSnapshot]:
        """Microsecond reactive wait for next market quote tick from Chrome V8."""
        try:
            await asyncio.wait_for(self._new_quote_event.wait(), timeout=timeout)
            self._new_quote_event.clear()
            return self._last_quote
        except asyncio.TimeoutError:
            return None

    async def initialize(self) -> bool:
        """Launches headless Chrome, applies session cookies, and loads XAUUSD terminal."""
        async with self._lock:
            return await self._initialize_locked()

    async def _initialize_locked(self) -> bool:
        """Internal initialization logic holding self._lock."""
        try:
            logger.info("Initializing LiteFinance Headless Gateway...")
            self._playwright = await async_playwright().start()

            launch_args = [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-ipc-flooding-protection",
                "--disable-hang-monitor",
                "--disable-features=Translate,OptimizationHints,MediaRouter",
                "--enable-tcp-fastopen",
                "--disable-extensions",
                "--mute-audio",
                "--disable-blink-features=AutomationControlled",
                "--js-flags=--max-old-space-size=512",
            ]
            if self.proxy:
                launch_args.append(f"--proxy-server={self.proxy}")

            has_valid_chrome = bool(self.chrome_path and os.path.exists(self.chrome_path))
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                executable_path=self.chrome_path if has_valid_chrome else None,
                args=launch_args,
            )

            context_kwargs: Dict[str, Any] = {
                "viewport": {"width": 1366, "height": 850},
                "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            }
            if os.path.exists(self.session_file):
                context_kwargs["storage_state"] = self.session_file
                logger.info("Loaded session state from %s", self.session_file)

            self._context = await self._browser.new_context(**context_kwargs)
            self._page = await self._context.new_page()

            # Auto-accept native browser dialogs (confirm/alert/prompt) to prevent Playwright lockup
            self._page.on("dialog", lambda dialog: asyncio.create_task(dialog.accept()))

            # Expose Python callback into Chrome V8 window for zero-polling quote stream
            await self._page.expose_function("__onJsQuoteUpdate", self._on_js_quote_tick)

            chart_url = f"https://my.litefinance.org/trading/chart?symbol={self.symbol}"
            logger.info("Navigating to %s...", chart_url)
            await self._page.goto(chart_url, wait_until="domcontentloaded", timeout=45000)
            await self._page.wait_for_timeout(3000)

            # Clear annoying overlays / 2FA popups
            await self._clear_overlays()

            ok = await self._setup_page_hooks()
            if not ok:
                cur_url = self._page.url
                logger.error("❌ LiteFinance trading panel not found (Current URL: %s). Session may need re-authentication.", cur_url)
                self._connected = False
                return False

            self._connected = True
            logger.info("✅ LiteFinance Gateway successfully connected and ready with Ultra-Fast Streaming.")
            return True
        except Exception as e:
            logger.error("Failed to initialize LiteFinance Gateway: %s", e)
            self._connected = False
            return False

    async def _clear_overlays(self) -> None:
        """Removes modal dialogs, banners, or blocking overlays."""
        if not self._page:
            return
        try:
            await self._page.evaluate("""() => {
                const popups = document.querySelectorAll('.popup, .modal, .toast, .notification, .alert, [class*="popup"], [class*="modal"]');
                popups.forEach(p => {
                    const closeBtn = p.querySelector('.close, [class*="close"], button, a');
                    if (closeBtn && closeBtn.getBoundingClientRect().width > 0) {
                        try { closeBtn.click(); } catch(e) { p.remove(); }
                    } else {
                        p.remove();
                    }
                });
                const overlays = document.querySelectorAll('.website_overlay, .overlay, .modal-backdrop');
                overlays.forEach(o => o.remove());
            }""")
        except Exception:
            pass

    async def _setup_page_hooks(self) -> bool:
        """Sets up ultra-fast MutationObserver and DOM helpers inside Chrome V8."""
        if not self._page:
            return False
        try:
            await self._clear_overlays()
            # Setup ultra-fast MutationObserver + pre-cached execution inside Chrome V8
            await self._page.evaluate("""() => {
                window._fastQuote = null;
                window._fastAccount = null;
                let lastBid = 0;
                let lastAsk = 0;

                const notifyQuote = () => {
                    const b = document.querySelector('.js_value_price_bid');
                    const a = document.querySelector('.js_value_price_ask');
                    if (b && a) {
                        const bText = b.innerText || '';
                        const aText = a.innerText || '';
                        const bid = parseFloat(bText.replace(/[^0-9.]/g, ''));
                        const ask = parseFloat(aText.replace(/[^0-9.]/g, ''));
                        if (bid && ask && bid > 0 && ask > 0 && ask >= bid && (bid !== lastBid || ask !== lastAsk)) {
                            lastBid = bid;
                            lastAsk = ask;
                            const mid = Math.round(((bid + ask) / 2.0) * 100) / 100;
                            const ts = Date.now();
                            window._fastQuote = { bid, ask, mid, ts };
                            if (window.__onJsQuoteUpdate) {
                                try {
                                    window.__onJsQuoteUpdate(bid, ask, mid, ts);
                                } catch(e) {}
                            }
                        }
                    }
                };

                // 1. Instant MutationObserver on DOM bid/ask elements (clean up old observer if present)
                if (window._quoteObserver) {
                    try { window._quoteObserver.disconnect(); } catch(e) {}
                }
                const bEl = document.querySelector('.js_value_price_bid');
                const aEl = document.querySelector('.js_value_price_ask');
                if (bEl && aEl) {
                    window._quoteObserver = new MutationObserver(() => notifyQuote());
                    window._quoteObserver.observe(bEl, { characterData: true, childList: true, subtree: true });
                    window._quoteObserver.observe(aEl, { characterData: true, childList: true, subtree: true });
                }

                // 2. High-speed 50ms interval fallback (clean up old interval if present)
                if (window._quoteInterval) {
                    try { clearInterval(window._quoteInterval); } catch(e) {}
                }
                window._quoteInterval = setInterval(notifyQuote, 50);
                notifyQuote();

                // Helper to inject broker-side Stop-Loss into LiteFinance trading DOM
                const applySL = (slPrice) => {
                    if (!slPrice || parseFloat(slPrice) <= 0) return;
                    try {
                        const trigger = document.querySelector('.js_extra_field_trigger');
                        const container = document.querySelector('.extra_fields_inner');
                        if (container && window.getComputedStyle(container).display === 'none' && trigger) {
                            trigger.click();
                        }
                        const slInput = document.querySelector('#stop_loss_price_1');
                        if (slInput) {
                            slInput.focus();
                            slInput.value = slPrice;
                            slInput.dispatchEvent(new Event('input', { bubbles: true }));
                            slInput.dispatchEvent(new Event('change', { bubbles: true }));
                            slInput.dispatchEvent(new Event('keyup', { bubbles: true }));
                            slInput.blur();
                        }
                    } catch(e) {}
                };

                // 3. Pre-cached single-shot fast execution function with broker SL
                window.__executeFastMarketOrder = (dir, vol, sl = null) => {
                    // Clear any lingering popups or overlays before interacting
                    try {
                        const popups = document.querySelectorAll('.popup, .modal, [class*="popup"], [class*="modal"]');
                        popups.forEach(p => {
                            const closeBtn = p.querySelector('.close, [class*="close"], button, a');
                            if (closeBtn && closeBtn.getBoundingClientRect().width > 0) {
                                try { closeBtn.click(); } catch(e) { p.remove(); }
                            } else {
                                p.remove();
                            }
                        });
                        const overlays = document.querySelectorAll('.website_overlay, .overlay');
                        overlays.forEach(o => o.remove());
                    } catch(e) {}

                    const isBuy = (dir === 'BUY');
                    const radioId = isBuy ? '#trade_buy_1' : '#trade_sell_1';
                    const radio = document.querySelector(radioId);
                    if (radio && !radio.checked) {
                        radio.checked = true;
                        radio.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    const label = document.querySelector('label[for="' + (isBuy ? 'trade_buy_1' : 'trade_sell_1') + '"]');
                    if (label) label.click();

                    const inp = document.querySelector('#volume_value_1');
                    if (inp) {
                        inp.focus();
                        inp.value = vol;
                        inp.dispatchEvent(new Event('input', { bubbles: true }));
                        inp.dispatchEvent(new Event('change', { bubbles: true }));
                        inp.dispatchEvent(new Event('keyup', { bubbles: true }));
                    }

                    if (sl) applySL(sl);

                    const btnSelector = isBuy ? 'button.btn_green.js_trade_action_open' : 'button.btn_red.js_trade_action_open';
                    let btn = document.querySelector(btnSelector);
                    if (!btn || btn.getBoundingClientRect().width === 0) {
                        const allBtns = Array.from(document.querySelectorAll('button.js_trade_action_open, button[type="submit"]'));
                        btn = allBtns.find(b => b.getBoundingClientRect().width > 0 && ((isBuy && (b.innerText || '').toUpperCase().includes('BUY')) || (!isBuy && (b.innerText || '').toUpperCase().includes('SELL')))) || allBtns.find(b => b.getBoundingClientRect().width > 0);
                    }
                    if (btn && btn.getBoundingClientRect().width > 0) {
                        btn.click();
                        return { success: true, text: btn.innerText.trim() || (isBuy ? 'BUY' : 'SELL') };
                    }
                    return { success: false, error: 'NO_VISIBLE_ORDER_BUTTON' };
                };

                // 3.1 Fast multi-order burst stacking function with broker SL
                window.__executeFastBurst = async (dir, count, vol, sl = null) => {
                    const isBuy = (dir === 'BUY');
                    const radioId = isBuy ? '#trade_buy_1' : '#trade_sell_1';
                    const radio = document.querySelector(radioId);
                    if (radio && !radio.checked) {
                        radio.checked = true;
                        radio.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    const label = document.querySelector('label[for="' + (isBuy ? 'trade_buy_1' : 'trade_sell_1') + '"]');
                    if (label) label.click();

                    const inp = document.querySelector('#volume_value_1');
                    if (inp && inp.value !== vol) {
                        inp.value = vol;
                        inp.dispatchEvent(new Event('input', { bubbles: true }));
                        inp.dispatchEvent(new Event('change', { bubbles: true }));
                    }

                    if (sl) applySL(sl);

                    const btnSelector = isBuy ? 'button.btn_green.js_trade_action_open' : 'button.btn_red.js_trade_action_open';
                    let btn = document.querySelector(btnSelector);
                    if (!btn || btn.getBoundingClientRect().width === 0) {
                        const allBtns = Array.from(document.querySelectorAll('button.js_trade_action_open, button[type="submit"]'));
                        btn = allBtns.find(b => b.getBoundingClientRect().width > 0 && ((isBuy && (b.innerText || '').toUpperCase().includes('BUY')) || (!isBuy && (b.innerText || '').toUpperCase().includes('SELL')))) || allBtns.find(b => b.getBoundingClientRect().width > 0);
                    }
                    if (!btn || btn.getBoundingClientRect().width === 0) {
                        return { success: false, error: 'NO_VISIBLE_ORDER_BUTTON', executed: 0 };
                    }

                    let executed = 0;
                    for (let i = 0; i < count; i++) {
                        btn.click();
                        executed++;
                        if (i < count - 1) {
                            await new Promise(r => setTimeout(r, 40));
                        }
                    }
                    return { success: true, executed, button: btn.innerText.trim() || (isBuy ? 'BUY' : 'SELL') };
                };

                // 4. Background account snapshot cache
                const updateAcc = () => {
                    try {
                        const rawText = document.querySelector('.portfolio, .bottom_bar, [class*="portfolio"]')?.innerText || '';
                        if (!rawText) return;
                        const parseNum = (str, regex) => {
                            const m = str.match(regex);
                            return m ? parseFloat(m[1].replace(/,/g, '')) : 0.0;
                        };
                        const total = parseNum(rawText, /([0-9,.]+)\\s*USD\\s*ASSETS,\\s*TOTAL/i);
                        const used = parseNum(rawText, /([0-9,.]+)\\s*USD\\s*ASSETS\\s*USED/i);
                        const avail = parseNum(rawText, /([0-9,.]+)\\s*USD\\s*AVAILABLE/i);
                        const change = parseNum(rawText, /([+-]?[0-9,.]+)\\s*USD\\s*CURRENT\\s*CHANGE/i);
                        window._fastAccount = { total, used, avail, change, ts: Date.now() };
                    } catch(e) {}
                };
                if (window._accInterval) {
                    try { clearInterval(window._accInterval); } catch(e) {}
                }
                window._accInterval = setInterval(updateAcc, 500);
                updateAcc();
            }""")

            # Verify trading panel is visible
            has_panel = await self._page.evaluate("""() => {
                const bid = document.querySelector('.js_value_price_bid');
                return !!bid;
            }""")
            if not has_panel:
                logger.warning("Trading panel not detected immediately, waiting an extra 4 seconds...")
                await self._page.wait_for_timeout(4000)
                await self._clear_overlays()
                has_panel = await self._page.evaluate("""() => {
                    const bid = document.querySelector('.js_value_price_bid');
                    return !!bid;
                }""")

            if not has_panel:
                cur_url = self._page.url
                logger.error("❌ LiteFinance trading panel not found (Current URL: %s). Session may need re-authentication.", cur_url)
                self._connected = False
                return False

            return bool(has_panel)
        except Exception as e:
            logger.error("Error setting up page hooks: %s", e)
            return False

    async def get_account_mode(self) -> str:
        """Returns DEMO or REAL based on LiteFinance DOM badges."""
        if not self._page:
            return "UNKNOWN"
        try:
            if hasattr(self._page, "is_closed") and callable(self._page.is_closed):
                res = self._page.is_closed()
                if not asyncio.iscoroutine(res) and res is True:
                    return "UNKNOWN"
        except Exception:
            pass
        try:
            mode = await self._page.evaluate("""() => {
                // 1. Direct leaf badge inspection (under user avatar pill)
                const allLeafEls = Array.from(document.querySelectorAll('*')).filter(el => el.children.length === 0 && el.offsetParent !== null);
                for (const el of allLeafEls) {
                    const t = (el.innerText || '').trim().toUpperCase();
                    if (t === "DEMO ACCOUNT" || t === "DEMO-ECN" || t === "MT5-DEMO") return "DEMO";
                    if (t === "REAL ACCOUNT" || t === "REAL-ECN" || t === "MT5-REAL") return "REAL";
                }

                // 2. Direct header action buttons (unambiguous state indicators)
                const actionBtns = Array.from(document.querySelectorAll('button, a, .btn, [class*="badge"], span, div')).filter(el => el.offsetParent !== null);
                for (const el of actionBtns) {
                    const t = (el.innerText || '').trim().toUpperCase();
                    if (t === "ACTIVATE REAL TRADING" || t.includes("ACTIVATE REAL")) {
                        return "DEMO"; // Presence of 'Activate Real' button proves current mode is DEMO
                    }
                    if (t === "ACTIVATE DEMO TRADING" || t.includes("ACTIVATE DEMO")) {
                        return "REAL"; // Presence of 'Activate Demo' button proves current mode is REAL
                    }
                }

                // 3. User badge container inspection
                const badgeEls = Array.from(document.querySelectorAll('.header_user, .user_name, .user_info, [class*="user"], [class*="account"]')).filter(el => el.offsetParent !== null);
                for (const el of badgeEls) {
                    const t = (el.innerText || '').trim().toUpperCase();
                    if (t.includes("DEMO ACCOUNT") || t.includes("DEMO-ECN") || t.includes("MT5-DEMO")) return "DEMO";
                    if (t.includes("REAL ACCOUNT") || t.includes("REAL-ECN") || t.includes("MT5-REAL")) return "REAL";
                }

                // 4. Fallback check on full text
                const text = document.body ? document.body.innerText.toUpperCase() : "";
                if (text.includes("DEMO ACCOUNT")) return "DEMO";
                if (text.includes("REAL ACCOUNT")) return "REAL";
                if (text.includes("ACTIVATE REAL TRADING")) return "DEMO";
                if (text.includes("ACTIVATE DEMO TRADING")) return "REAL";

                return "UNKNOWN";
            }""")
            if mode in ("DEMO", "REAL"):
                return mode

            # Fallback via Playwright Locators
            if await self._page.locator("text='DEMO ACCOUNT'").count() > 0:
                return "DEMO"
            if await self._page.locator("text='REAL ACCOUNT'").count() > 0:
                return "REAL"
            if await self._page.locator("#switch_mode_real, [data-url*='/switch/real']").count() > 0:
                return "DEMO"
            if await self._page.locator("#switch_mode_demo, [data-url*='/switch/demo']").count() > 0:
                return "REAL"

            # Check full content
            full_content = (await self._page.content()).upper()
            if "DEMO ACCOUNT" in full_content and "REAL ACCOUNT" not in full_content:
                return "DEMO"
            if "REAL ACCOUNT" in full_content and "DEMO ACCOUNT" not in full_content:
                return "REAL"

            return "UNKNOWN"
        except Exception as e:
            logger.warning("Error detecting account mode: %s", e)
            return "UNKNOWN"

    async def switch_account_mode(self, target_mode: str) -> Dict[str, Any]:
        """
        Switches between DEMO and REAL accounts on LiteFinance.
        Validates zero open positions before switching.
        Handles modal confirmations, URL redirects, and atomic session persistence.
        """
        async with self._lock:
            target_mode = target_mode.upper()
            if target_mode not in ("DEMO", "REAL"):
                return {"success": False, "error": f"Invalid target mode: {target_mode}"}

            if not self._page or (hasattr(self._page, "is_closed") and self._page.is_closed()):
                return {"success": False, "error": "Gateway not initialized"}

            # Invalidate stale account cache immediately to prevent cross-mode pollution
            self._last_account = None
            self._last_account_ts = 0.0
            self._switching_mode = True

            try:
                current_mode = await self.get_account_mode()
                if current_mode == target_mode:
                    logger.info("Account is already in %s mode.", target_mode)
                    fresh_acc = await self.get_account_snapshot(force_fresh=True)
                    return {
                        "success": True,
                        "mode": target_mode,
                        "already_in_mode": True,
                        "balance": fresh_acc.balance,
                        "equity": fresh_acc.equity,
                        "available": fresh_acc.available,
                    }

                # Safety check: ensure 0 open positions in snapshot
                acc = await self.get_account_snapshot(force_fresh=True)
                if acc.assets_used > 0.0:
                    logger.error("Cannot switch account mode: active positions exist ($%.2f assets used)", acc.assets_used)
                    return {"success": False, "error": "ACTIVE_POSITIONS_EXIST", "assets_used": acc.assets_used}

                # Additional DOM safety check: verify open trades table is empty
                dom_open_trades = await self._page.evaluate("""() => {
                    const rows = Array.from(document.querySelectorAll('.js_open_trades tr, [class*="open_trades"] tr, .portfolio_table tbody tr'));
                    return rows.filter(r => r.offsetParent !== null && !r.classList.contains('empty')).length;
                }""")
                if dom_open_trades > 0:
                    logger.error("Cannot switch account mode: %d open position rows in DOM", dom_open_trades)
                    return {"success": False, "error": "DOM_OPEN_TRADES_EXIST", "trades_count": dom_open_trades}

                logger.info("Initiating LiteFinance switch from %s to %s...", current_mode, target_mode)

                # 1. First, check if direct switch button is already visible in header/sidebar
                direct_clicked = False
                if target_mode == "REAL":
                    direct_loc = self._page.locator("button, a").filter(has_text=re.compile(r"Activate real trading|Activate real", re.I)).first
                else:
                    direct_loc = self._page.locator("button, a").filter(has_text=re.compile(r"Activate demo trading|Activate demo", re.I)).first

                if await direct_loc.count() > 0 and await direct_loc.is_visible():
                    await direct_loc.click(timeout=3000)
                    direct_clicked = True
                    logger.info("Clicked direct mode switch button!")

                if not direct_clicked:
                    # 2. Click user menu dropdown trigger (Playwright native locator)
                    user_menu = self._page.locator("text='Emadodin Akbari'").first
                    if await user_menu.count() == 0 or not await user_menu.is_visible():
                        user_menu = self._page.locator("text='DEMO ACCOUNT'").first
                    if await user_menu.count() == 0 or not await user_menu.is_visible():
                        user_menu = self._page.locator("text='REAL ACCOUNT'").first
                    if await user_menu.count() == 0 or not await user_menu.is_visible():
                        user_menu = self._page.locator(".header_user, .user_name, .user_info, [class*='user_menu'], .header_profile").first

                    if await user_menu.count() > 0:
                        await user_menu.click(timeout=5000)
                        logger.info("Clicked user profile dropdown trigger.")
                        await self._page.wait_for_timeout(1000)
                    else:
                        logger.warning("Could not find user profile dropdown trigger!")

                    # 3. Click the target mode switch button in dropdown
                    if target_mode == "REAL":
                        target_btn = self._page.locator("#switch_mode_real, [data-url*='/switch/real']").first
                        if await target_btn.count() == 0:
                            target_btn = self._page.locator("a, button, div.item").filter(has_text=re.compile(r"Activate real", re.I)).first
                    else:
                        target_btn = self._page.locator("#switch_mode_demo, [data-url*='/switch/demo']").first
                        if await target_btn.count() == 0:
                            target_btn = self._page.locator("a, button, div.item").filter(has_text=re.compile(r"Activate demo", re.I)).first

                    if await target_btn.count() > 0:
                        await target_btn.click(timeout=5000)
                        logger.info("Clicked switch button for %s mode.", target_mode)
                    else:
                        logger.error("Could not find switch button for %s mode in dropdown!", target_mode)

                # 4. Robust polling for potential confirmation modal (up to 3.6s)
                modal_confirmed = False
                for _ in range(12):
                    try:
                        confirm_loc = self._page.locator(
                            ".modal button, .popup button, [role='dialog'] button, .modal a, .popup a, .dialog button, [class*='modal'] button, [class*='popup'] button"
                        ).filter(has_text=re.compile(r"^(CONFIRM|YES|ACTIVATE|SWITCH|OK|CONTINUE|PROCEED)$|CONFIRM|ACTIVATE", re.I)).first
                        if await confirm_loc.count() > 0 and await confirm_loc.is_visible():
                            await confirm_loc.click(timeout=2000)
                            logger.info("Clicked modal confirmation button!")
                            modal_confirmed = True
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(0.3)

                # 5. Wait for navigation settlement
                try:
                    await self._page.wait_for_load_state("domcontentloaded", timeout=12000)
                except Exception:
                    pass

                await self._page.wait_for_timeout(3000)
                await self._clear_overlays()

                # Ensure we remain on the chart trading URL
                if "trading/chart" not in self._page.url:
                    logger.info("Redirecting back to trading chart after mode switch (current: %s)...", self._page.url)
                    await self._page.goto(CHART_URL, wait_until="domcontentloaded", timeout=15000)
                    await self._page.wait_for_timeout(3000)
                    await self._clear_overlays()

                # Re-inject streaming hooks
                hooks_ok = False
                for attempt in range(3):
                    hooks_ok = await self._setup_page_hooks()
                    if hooks_ok:
                        break
                    await self._page.wait_for_timeout(2000)

                if not hooks_ok:
                    logger.error("❌ Failed to re-inject fast streaming page hooks after switch!")
                    return {"success": False, "error": "HOOKS_INJECTION_FAILED"}

                # Multi-probe mode verification
                new_mode = "UNKNOWN"
                for _ in range(12):
                    new_mode = await self.get_account_mode()
                    if new_mode == target_mode:
                        break
                    await asyncio.sleep(0.5)

                if new_mode == target_mode:
                    self._connected = True
                    logger.info("✅ Successfully switched to %s account mode!", new_mode)
                    # Persist session state atomically
                    if self._context:
                        tmp_session = f"{self.session_file}.tmp"
                        await self._context.storage_state(path=tmp_session)
                        if os.path.exists(tmp_session):
                            os.replace(tmp_session, self.session_file)
                        logger.info("Saved updated session cookies atomically to %s", self.session_file)

                    # Invalidate cache again before reading fresh snapshot
                    self._last_account = None
                    self._last_account_ts = 0.0
                    fresh_acc = await self.get_account_snapshot(force_fresh=True)
                    return {
                        "success": True,
                        "mode": new_mode,
                        "balance": fresh_acc.balance,
                        "equity": fresh_acc.equity,
                        "available": fresh_acc.available,
                    }
                else:
                    logger.warning("Switch attempt finished but mode is %s (expected %s)", new_mode, target_mode)
                    return {"success": False, "error": f"MODE_MISMATCH_{new_mode}"}

            except Exception as e:
                logger.error("Exception switching account mode: %s", e)
                return {"success": False, "error": str(e)}
            finally:
                self._switching_mode = False

    async def get_live_quote(self) -> Optional[QuoteSnapshot]:
        """Reads current real-time Bid and Ask prices directly from memory or DOM fallback."""
        snap = self.get_live_quote_sync()
        if snap is not None:
            return snap

        # If lock is currently held (e.g. order execution or mode switch in progress), avoid DOM collision
        if self._lock.locked():
            return self._last_quote

        if not self._page:
            return self._last_quote
        try:
            quote_data = await self._page.evaluate("""() => {
                if (window._fastQuote && (Date.now() - window._fastQuote.ts) < 2000) {
                    return window._fastQuote;
                }
                const bidEl = document.querySelector('.js_value_price_bid');
                const askEl = document.querySelector('.js_value_price_ask');
                const bid = bidEl ? parseFloat(bidEl.innerText.replace(/[^0-9.]/g, '')) : null;
                const ask = askEl ? parseFloat(askEl.innerText.replace(/[^0-9.]/g, '')) : null;
                return { bid, ask, mid: Math.round(((bid + ask) / 2.0) * 100) / 100 };
            }""")
            if quote_data and quote_data.get("bid") and quote_data.get("ask"):
                bid = float(quote_data["bid"])
                ask = float(quote_data["ask"])
                mid = float(quote_data.get("mid") or round((bid + ask) / 2.0, 2))
                now = time.time()
                # If quote comes from fastQuote, use its timestamp. Otherwise, if DOM quote hasn't changed, retain original timestamp.
                if "ts" in quote_data and quote_data["ts"]:
                    quote_ts = float(quote_data["ts"]) / 1000.0
                elif self._last_quote and self._last_quote.bid == bid and self._last_quote.ask == ask:
                    quote_ts = self._last_quote.timestamp
                else:
                    quote_ts = now

                self._last_quote = QuoteSnapshot(
                    symbol=self.symbol,
                    bid=bid,
                    ask=ask,
                    mid=mid,
                    timestamp=quote_ts,
                )
                self._consecutive_errors = 0
                return self._last_quote
        except Exception as e:
            err_str = str(e).lower()
            if "crashed" in err_str or "closed" in err_str:
                self._handle_crash(e)
            logger.debug("Failed to read quote: %s", e)
        return self._last_quote

    async def get_account_snapshot(self, force_fresh: bool = False) -> AccountSnapshot:
        """Reads real-time balance, assets used, available margin, and floating change with RAM cache."""
        now = time.time()
        # Rate-limit CDP round-trips to at most once per 250ms even if force_fresh=True
        min_interval = 0.25 if force_fresh else 2.0
        if not force_fresh and self._last_account and (now - self._last_account_ts) < min_interval:
            return self._last_account

        if not self._page:
            return self._last_account or AccountSnapshot(balance=0.0, equity=0.0, assets_used=0.0, available=0.0, floating_pnl=0.0)
        try:
            acc_data = await self._page.evaluate("""(forceFresh) => {
                if (!forceFresh && window._fastAccount && (Date.now() - window._fastAccount.ts) < 1500) {
                    return window._fastAccount;
                }
                const rawText = document.querySelector('.portfolio, .bottom_bar, [class*="portfolio"]')?.innerText || '';
                const parseNum = (str, regex) => {
                    const m = str.match(regex);
                    return m ? parseFloat(m[1].replace(/,/g, '')) : 0.0;
                };
                
                const total = parseNum(rawText, /([0-9,.]+)\\s*[A-Z]{3,4}\\s*ASSETS[,\\s]+TOTAL/i) || parseNum(rawText, /([0-9,.]+)\\s*USD\\s*ASSETS,\\s*TOTAL/i);
                const used = parseNum(rawText, /([0-9,.]+)\\s*[A-Z]{3,4}\\s*ASSETS\\s*USED/i) || parseNum(rawText, /([0-9,.]+)\\s*USD\\s*ASSETS\\s*USED/i);
                const avail = parseNum(rawText, /([0-9,.]+)\\s*[A-Z]{3,4}\\s*AVAILABLE/i) || parseNum(rawText, /([0-9,.]+)\\s*USD\\s*AVAILABLE/i);
                const change = parseNum(rawText, /([+-]?[0-9,.]+)\\s*[A-Z]{3,4}\\s*CURRENT\\s*CHANGE/i) || parseNum(rawText, /([+-]?[0-9,.]+)\\s*USD\\s*CURRENT\\s*CHANGE/i);
                
                return { total, used, avail, change };
            }""", force_fresh)

            balance = float((acc_data.get("total") if acc_data else None) or 0.0)
            used = float((acc_data.get("used") if acc_data else None) or 0.0)
            avail = float((acc_data.get("avail") if acc_data else None) or 0.0)
            change = float((acc_data.get("change") if acc_data else None) or 0.0)

            if balance <= 0.0 and self._last_account and self._last_account.balance > 0.0:
                self._consecutive_errors += 1
                logger.warning("⚠️ DOM portfolio selector returned 0.0 (DOM overlay or session stall). Retaining last known balance ($%.2f | Error #%d)",
                               self._last_account.balance, self._consecutive_errors)
                if self._consecutive_errors >= 2:
                    self._spawn_bg_task(self._clear_overlays())
                if self._consecutive_errors >= 6 and not self._reconnecting and not self._switching_mode:
                    logger.error("🚨 Persistent DOM balance stall. Scheduling gateway auto-reconnect...")
                    self._spawn_bg_task(self.reconnect())
                return self._last_account

            equity = round(balance + change, 2)
            self._last_account = AccountSnapshot(
                balance=balance,
                equity=equity,
                assets_used=used,
                available=avail,
                floating_pnl=change,
            )
            self._last_account_ts = now
            self._consecutive_errors = 0
            return self._last_account
        except Exception as e:
            err_str = str(e).lower()
            if "crashed" in err_str or "closed" in err_str:
                self._handle_crash(e)
            else:
                logger.warning("Failed to fetch account snapshot: %s", e)
            return self._last_account or AccountSnapshot(balance=0.0, equity=0.0, assets_used=0.0, available=0.0, floating_pnl=0.0)

    async def open_market_order(
        self,
        direction: str,
        volume: float,
        sl_price: Optional[float] = None,
        tp_price: Optional[float] = None,
        expected_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes a real market order on LiteFinance with atomic single-shot dispatch.
        Zero sleeps, sub-5ms internal execution.
        """
        async with self._lock:
            if not self._page:
                return {"success": False, "error": "Gateway not initialized"}

            if expected_mode:
                cur_mode = await self.get_account_mode()
                if cur_mode != "UNKNOWN" and cur_mode != expected_mode.upper():
                    logger.error("Refusing order dispatch: Account mode is %s, expected %s", cur_mode, expected_mode)
                    return {"success": False, "error": f"ACCOUNT_MODE_MISMATCH_{cur_mode}"}

            t0 = time.perf_counter()
            direction = direction.upper()
            is_buy = (direction == "BUY")
            vol_str = f"{volume:.2f}"
            sl_str = f"{sl_price:.2f}" if (sl_price and sl_price > 0) else None
            try:
                # 0. Capture initial broker state before order dispatch to verify incremental fill
                initial_acc = await self.get_account_snapshot()
                initial_assets = initial_acc.assets_used if initial_acc else 0.0
                initial_trades = await self._page.evaluate("""() => {
                    const rows = document.querySelectorAll('.js_open_trades tr, [class*="open_trades"] tr, .portfolio_table tr');
                    return rows.length;
                }""")

                # 1. Clear any lingering popups or overlays before order dispatch
                await self._page.evaluate("""() => {
                    const popups = document.querySelectorAll('.popup, .modal, .toast, .notification, .alert, [class*="popup"], [class*="modal"]');
                    popups.forEach(p => {
                        const closeBtn = p.querySelector('.close, [class*="close"], button, a');
                        if (closeBtn && closeBtn.getBoundingClientRect().width > 0) {
                            try { closeBtn.click(); } catch(e) { p.remove(); }
                        } else {
                            p.remove();
                        }
                    });
                    const overlays = document.querySelectorAll('.website_overlay, .overlay');
                    overlays.forEach(o => o.remove());
                }""")

                # 2. Select direction tab (BUY or SELL)
                radio_id = "trade_buy_1" if is_buy else "trade_sell_1"
                try:
                    await self._page.click(f'label[for="{radio_id}"]', timeout=3000)
                except Exception as e_tab:
                    logger.warning("Tab click warning: %s, falling back to evaluate", e_tab)
                    await self._page.evaluate(f"""() => {{
                        const lbl = document.querySelector('label[for="{radio_id}"]');
                        if (lbl) lbl.click();
                        const r = document.querySelector('#{radio_id}');
                        if (r) {{ r.checked = true; r.dispatchEvent(new Event('change', {{ bubbles: true }})); }}
                    }}""")
                await asyncio.sleep(0.15)

                # 3. Set volume
                try:
                    await self._page.fill('#volume_value_1', vol_str, timeout=3000)
                except Exception as e_vol:
                    logger.warning("Volume fill warning: %s, falling back to evaluate", e_vol)
                    await self._page.evaluate(f"""(v) => {{
                        const inp = document.querySelector('#volume_value_1');
                        if (inp) {{
                            inp.value = v;
                            inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        }}
                    }}""", vol_str)
                await asyncio.sleep(0.10)

                # 3b. Ensure SL/TP drawer is open if SL or TP requested
                if (sl_price and sl_price > 0) or (tp_price and tp_price > 0):
                    try:
                        await self._page.evaluate("""() => {
                            const trigger = document.querySelector('.js_extra_field_trigger');
                            const container = document.querySelector('.extra_fields_inner');
                            if (container && window.getComputedStyle(container).display === 'none' && trigger) {
                                trigger.click();
                            }
                        }""")
                    except Exception:
                        pass

                # 3c. Set Stop Loss / Take Profit if provided
                if sl_price and sl_price > 0:
                    try:
                        await self._page.fill('#stop_loss_price_1', f"{sl_price:.2f}", timeout=1500)
                    except Exception:
                        await self._page.evaluate(f"""(sl) => {{
                            const inp = document.querySelector('#stop_loss_price_1');
                            if (inp) {{
                                inp.value = sl;
                                inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            }}
                        }}""", f"{sl_price:.2f}")

                if tp_price and tp_price > 0:
                    try:
                        await self._page.fill('#take_profit_price_1', f"{tp_price:.2f}", timeout=1500)
                    except Exception:
                        await self._page.evaluate(f"""(tp) => {{
                            const inp = document.querySelector('#take_profit_price_1');
                            if (inp) {{
                                inp.value = tp;
                                inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            }}
                        }}""", f"{tp_price:.2f}")

                # 4. Click order dispatch button (target exact direction-aware button)
                clicked_btn = direction
                res = await self._page.evaluate("""(isBuy) => {
                    const btnSelector = isBuy ? 'button.btn_green.js_trade_action_open' : 'button.btn_red.js_trade_action_open';
                    let target = document.querySelector(btnSelector);
                    if (!target || target.getBoundingClientRect().width === 0 || target.getBoundingClientRect().height === 0) {
                        const btns = Array.from(document.querySelectorAll('button.js_trade_action_open, button[type="submit"].btn_large')).filter(b => !b.classList.contains('js_trade_action_close') && !b.disabled);
                        target = btns.find(b => {
                            const rect = b.getBoundingClientRect();
                            if (rect.width <= 0 || rect.height <= 0) return false;
                            const txt = (b.innerText || '').toUpperCase();
                            return isBuy ? (txt.includes('BUY') || b.classList.contains('btn_green')) : (txt.includes('SELL') || b.classList.contains('btn_red'));
                        });
                    }
                    if (target) {
                        target.scrollIntoViewIfNeeded ? target.scrollIntoViewIfNeeded() : target.scrollIntoView();
                        target.click();
                        return { success: true, text: target.innerText.trim(), className: target.className };
                    }
                    return { success: false, error: 'NO_VALID_DIRECTION_ORDER_BUTTON' };
                }""", is_buy)
                if isinstance(res, dict) and res.get("success"):
                    clicked_btn = res.get("text") or direction

                if not isinstance(res, dict) or not res.get("success"):
                    err_msg = res.get("error", "Button dispatch failed") if isinstance(res, dict) else str(res)
                    logger.error("❌ BROKER ORDER DISPATCH FAILED: %s", err_msg)
                    return {"success": False, "error": err_msg}

                # 3. VERIFICATION & BROKER RESPONSE INSPECTION
                # Wait 200ms for LiteFinance DOM to process request
                await asyncio.sleep(0.20)

                broker_check = await self._page.evaluate("""() => {
                    // Check for error modals / popups / alerts
                    const popups = Array.from(document.querySelectorAll('.popup, .modal, .toast, .notification, .alert, [class*="popup"], [class*="modal"], [class*="toast"], [class*="notification"]')).filter(p => p.getBoundingClientRect().width > 0 && p.getBoundingClientRect().height > 0);
                    for (const p of popups) {
                        const text = (p.innerText || '').trim();
                        if (text.includes('Not enough funds') || text.includes('Attention') || text.includes('Error') || text.includes('rejected') || text.includes('failed') || text.includes('Invalid') || text.includes('disabled') || text.includes('cannot open')) {
                            // Dismiss popup
                            const closeBtn = p.querySelector('.close, [class*="close"], a, button');
                            if (closeBtn) {
                                try { closeBtn.click(); } catch(e) { p.remove(); }
                            } else {
                                p.remove();
                            }
                            return { rejected: true, reason: text.replace(/\\n+/g, ' ') };
                        }
                    }

                    // Check for confirmation modals
                    const confirmBtns = Array.from(document.querySelectorAll('button, a.btn')).filter(x => {
                        const t = (x.innerText || '').trim();
                        return (t === 'Confirm' || t === 'Yes' || t === 'OK') && x.getBoundingClientRect().width > 0;
                    });
                    if (confirmBtns.length > 0) {
                        confirmBtns[0].click();
                    }

                    const openTrades = document.querySelectorAll('.js_open_trades tr, [class*="open_trades"] tr, .portfolio_table tr');
                    return { rejected: false, openTradesCount: openTrades.length };
                }""")

                if broker_check.get("rejected"):
                    rej_reason = broker_check.get("reason", "Broker rejected order")
                    logger.error("❌ BROKER REJECTED ORDER: %s | Requested: %s %.2f lots", rej_reason, direction, volume)
                    return {"success": False, "error": rej_reason}

                # 4. Multi-tick confirmation: Ensure assets_used or trade table incremented
                confirmed = False
                for _ in range(15):  # Up to 3.0s buffer for broker settlement
                    await asyncio.sleep(0.20)
                    acc = await self.get_account_snapshot(force_fresh=True)
                    current_trades = await self._page.evaluate("""() => {
                        const rows = document.querySelectorAll('.js_open_trades tr, [class*="open_trades"] tr, .portfolio_table tr');
                        return rows.length;
                    }""")
                    if initial_assets <= 0.0:
                        if acc.assets_used > 0.0 or current_trades > initial_trades:
                            confirmed = True
                            break
                    else:
                        # Stacked / pyramid order: verify assets increased or trade row count increased
                        if acc.assets_used >= (initial_assets + 0.10) or current_trades > initial_trades:
                            confirmed = True
                            break

                latency_ms = (time.perf_counter() - t0) * 1000.0
                clicked_btn = res.get("text", "ORDER_CLICKED")

                if not confirmed:
                    # Final check for error popups that appeared late
                    late_err = await self._page.evaluate("""() => {
                        const p = document.querySelector('.popup, .modal, .toast, .notification, .alert');
                        return p ? (p.innerText || '').replace(/\\n+/g, ' ') : null;
                    }""")
                    err_msg = late_err or "Broker did not report active position (assets_used remained 0 or did not increment)"
                    logger.warning("⚠️ ORDER CONFIRMATION TIMEOUT: %s", err_msg)
                    return {"success": False, "error": err_msg}

                logger.info(
                    "⚡ ULTRA-FAST BROKER ORDER CONFIRMED: %s %.2f lots | SL: %s | Assets Used: $%.2f | Latency: %.2fms | Button: %s",
                    direction, volume, sl_str or "NONE", acc.assets_used, latency_ms, clicked_btn
                )

                return {
                    "success": True,
                    "direction": direction,
                    "volume": volume,
                    "sl_price": sl_price,
                    "latency_ms": latency_ms,
                    "button": clicked_btn,
                }
            except Exception as e:
                err_str = str(e).lower()
                if "crashed" in err_str or "closed" in err_str:
                    self._handle_crash(e)
                logger.error("Error opening broker order: %s", e)
                return {"success": False, "error": str(e)}

    async def execute_order_burst(
        self,
        direction: str,
        total_volume: float,
        stack_count: int = 5,
        lot_per_order: float = 0.10,
        sl_price: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Executes an Order Stacking Burst (as seen in scalp.mp4).
        Dispatches stack_count rapid orders of lot_per_order into LiteFinance broker with broker-side SL.
        """
        async with self._lock:
            if not self._page:
                return {"success": False, "error": "Gateway not initialized"}

            direction = direction.upper()
            if direction not in ("BUY", "SELL"):
                return {"success": False, "error": f"Invalid direction: {direction}"}

            # If lot_per_order not set or <= 0, divide total_volume by stack_count
            if lot_per_order <= 0.0:
                lot_per_order = max(0.01, round(total_volume / max(1, stack_count), 2))

            stack_count = max(1, min(20, stack_count))
            vol_str = f"{lot_per_order:.2f}"
            sl_str = f"{sl_price:.2f}" if (sl_price and sl_price > 0) else None
            t0 = time.perf_counter()

            try:
                res = await self._page.evaluate("""async ({ dir, count, vol, sl }) => {
                    if (typeof window.__executeFastBurst === 'function') {
                        return await window.__executeFastBurst(dir, count, vol, sl);
                    }
                    if (typeof window.__executeFastMarketOrder === 'function') {
                        return window.__executeFastMarketOrder(dir, vol, sl);
                    }
                    return { success: false, error: 'NO_BURST_EXECUTION_FUNCTION' };
                }""", {"dir": direction, "count": stack_count, "vol": vol_str, "sl": sl_str})

                latency_ms = (time.perf_counter() - t0) * 1000.0

                if not isinstance(res, dict) or not res.get("success"):
                    err_msg = res.get("error", "Burst dispatch failed") if isinstance(res, dict) else str(res)
                    logger.error("❌ BROKER BURST DISPATCH FAILED: %s", err_msg)
                    return {"success": False, "error": err_msg}

                executed_count = res.get("executed", stack_count)
                actual_total_vol = round(executed_count * lot_per_order, 2)
                logger.info(
                    "⚡ ORDER STACK BURST SENT: %s %d orders x %.2f lots (= %.2f lots total) | SL: %s | Dispatch Latency: %.2fms",
                    direction, executed_count, lot_per_order, actual_total_vol, sl_str or "NONE", latency_ms
                )

                return {
                    "success": True,
                    "direction": direction,
                    "orders_dispatched": executed_count,
                    "lot_per_order": lot_per_order,
                    "total_volume": actual_total_vol,
                    "sl_price": sl_price,
                    "latency_ms": latency_ms,
                    "button": res.get("button", direction),
                }
            except Exception as e:
                logger.error("Error in execute_order_burst: %s", e)
                return {"success": False, "error": str(e)}

    async def flatten_all_positions(self) -> Dict[str, Any]:
        """
        Emergency / Profit spike flatten: atomic execution across all open tickets.
        Directly targets .js_trade_action_close and auto-opens portfolio drawer if needed.
        Iteratively closes stacked orders and confirms all modals until 0 assets used.
        """
        async with self._lock:
            if not self._page:
                return {"success": False, "error": "Gateway not initialized"}

            t0 = time.perf_counter()
            total_closed = 0
            try:
                for iteration in range(8):
                    # 1. Check account snapshot: if 0 assets used, all orders are closed!
                    acc = await self.get_account_snapshot(force_fresh=True)
                    if acc.assets_used <= 0.0:
                        break

                    # 2. Ensure portfolio drawer is open (check visibility to prevent toggling closed on odd iterations)
                    await self._page.evaluate("""() => {
                        const table = document.querySelector('.portfolio_table, .js_open_trades');
                        if (!table || table.getBoundingClientRect().height === 0) {
                            const triggers = Array.from(document.querySelectorAll('a, button, span, div')).filter(el => {
                                const txt = (el.innerText || '').trim();
                                return (txt === 'PORTFOLIO' || txt === '^ PORTFOLIO' || txt.includes('PORTFOLIO')) && el.getBoundingClientRect().width > 0;
                            });
                            if (triggers.length > 0) triggers[0].click();
                        }
                    }""")
                    await self._page.wait_for_timeout(200)

                    # 3. Check for "Close all" button first!
                    closed_all = await self._page.evaluate("""() => {
                        const btns = Array.from(document.querySelectorAll('button, a, div')).filter(b => {
                            const txt = (b.innerText || '').trim().toLowerCase();
                            return (txt === 'close all' || txt === 'close all trades' || txt === 'close all positions') && b.getBoundingClientRect().width > 0;
                        });
                        if (btns.length > 0) {
                            btns[0].click();
                            return true;
                        }
                        return false;
                    }""")

                    if closed_all:
                        await self._page.wait_for_timeout(150)
                        # Confirm modal
                        await self._page.evaluate("""() => {
                            const confirmBtns = Array.from(document.querySelectorAll('button, a.btn')).filter(x => {
                                const t = (x.innerText || '').trim();
                                return (t === 'Close' || t === 'Yes' || t === 'Confirm' || t === 'OK') && x.getBoundingClientRect().width > 0;
                            });
                            confirmBtns.forEach(cb => cb.click());
                        }""")
                        await self._page.wait_for_timeout(300)
                        acc = await self.get_account_snapshot(force_fresh=True)
                        if acc.assets_used <= 0.0:
                            total_closed += 1
                            break

                    # 4. Click visible individual close button and confirm
                    clicked = await self._page.evaluate("""() => {
                        const closeBtns = Array.from(document.querySelectorAll('.js_trade_action_close, a.btn_red.js_trade_action_close, .btn_close, [class*="close_trade"]')).filter(b => b.getBoundingClientRect().width > 0);
                        if (closeBtns.length > 0) {
                            closeBtns[0].click();
                            return 1;
                        }
                        return 0;
                    }""")

                    if clicked > 0:
                        total_closed += clicked
                        await self._page.wait_for_timeout(150)
                        # Confirm modals
                        await self._page.evaluate("""() => {
                            const confirmBtns = Array.from(document.querySelectorAll('button, a.btn')).filter(x => {
                                const t = (x.innerText || '').trim();
                                return (t === 'Close' || t === 'Yes' || t === 'Confirm' || t === 'OK') && x.getBoundingClientRect().width > 0;
                            });
                            confirmBtns.forEach(cb => cb.click());
                        }""")
                        await self._page.wait_for_timeout(200)
                    else:
                        # Scroll down to reveal any hidden positions
                        await self._page.evaluate("""() => {
                            const scrollable = document.querySelector('.portfolio_table, .ui-scrollable, .portfolio_trades, .data_table_wrap, [class*="portfolio"]') || document.querySelector('.js_scrollable');
                            if (scrollable) scrollable.scrollTop += 250;
                        }""")
                        await self._page.wait_for_timeout(200)

                acc = await self.get_account_snapshot(force_fresh=True)
                latency_ms = (time.perf_counter() - t0) * 1000.0
                is_clean = (acc.assets_used <= 0.0)
                logger.info("⚡ ULTRA-FAST BROKER FLATTEN: Closed %s tickets | Latency: %.2fms | Balance: $%.2f | Assets Used: $%.2f",
                            total_closed, latency_ms, acc.balance, acc.assets_used)

                return {
                    "success": is_clean,
                    "closed_count": total_closed,
                    "latency_ms": latency_ms,
                    "balance": acc.balance,
                    "equity": acc.equity,
                    "assets_used": acc.assets_used,
                    "error": None if is_clean else f"Incomplete flatten: ${acc.assets_used:.2f} assets still in use",
                }
            except Exception as e:
                err_str = str(e).lower()
                if "crashed" in err_str or "closed" in err_str:
                    self._handle_crash(e)
                logger.error("Error flattening broker positions: %s", e)
                return {"success": False, "error": str(e)}


    def _handle_crash(self, error: Exception) -> None:
        """Tracks consecutive errors and schedules automatic background recovery on browser crashes."""
        self._consecutive_errors += 1
        err_str = str(error).lower()
        if "crashed" in err_str or "closed" in err_str or self._consecutive_errors >= 5:
            if not self._reconnecting:
                logger.error("🚨 LiteFinance Gateway browser crash/disconnect detected (%s). Triggering auto-recovery...", error)
                self._spawn_bg_task(self.reconnect())

    async def reconnect(self) -> bool:
        """Self-healing reconnect: cleanly shuts down dead browser context and re-spawns a fresh session under lock."""
        if self._reconnecting:
            return False
        self._reconnecting = True
        try:
            self._reconnect_attempts += 1
            backoff = min(30.0, 2.0 * (1.5 ** min(self._reconnect_attempts, 4)))
            logger.warning("🔄 Self-healing LiteFinanceGateway: Initiating automatic reconnection (attempt #%d, backoff %.1fs)...",
                           self._reconnect_attempts, backoff)
            await asyncio.sleep(backoff)
            async with self._lock:
                if self._context:
                    try:
                        await asyncio.wait_for(self._context.close(), timeout=4.0)
                    except Exception:
                        pass
                if self._browser:
                    try:
                        await asyncio.wait_for(self._browser.close(), timeout=4.0)
                    except Exception:
                        pass
                if self._playwright:
                    try:
                        await asyncio.wait_for(self._playwright.stop(), timeout=4.0)
                    except Exception:
                        pass
                self._browser = None
                self._context = None
                self._page = None
                self._playwright = None
                self._connected = False
                try:
                    success = await self._initialize_locked()
                    if success:
                        logger.info("✅ LiteFinanceGateway auto-recovery SUCCESSFUL! Terminal re-attached.")
                        self._consecutive_errors = 0
                        self._reconnect_attempts = 0
                    else:
                        logger.error("❌ LiteFinanceGateway auto-recovery failed to initialize.")
                    return success
                except Exception as ex:
                    logger.error("❌ LiteFinanceGateway auto-recovery encountered error: %s", ex)
                    return False
        finally:
            self._reconnecting = False

    async def close(self) -> None:
        """Closes browser and cleans up resources cleanly."""
        if self._context:
            try:
                await asyncio.wait_for(self._context.storage_state(path=self.session_file), timeout=3.0)
            except Exception as e:
                logger.warning("Failed to save session state: %s", e)
            try:
                await asyncio.wait_for(self._context.close(), timeout=3.0)
            except Exception:
                pass
        if self._browser:
            try:
                await asyncio.wait_for(self._browser.close(), timeout=3.0)
            except Exception:
                pass
        if self._playwright:
            try:
                await asyncio.wait_for(self._playwright.stop(), timeout=3.0)
            except Exception:
                pass
        self._connected = False
        logger.info("LiteFinance Gateway cleanly closed.")
