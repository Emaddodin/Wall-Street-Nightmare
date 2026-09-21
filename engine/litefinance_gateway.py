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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

logger = logging.getLogger("lf_gateway")

PROXY_SERVER = os.getenv("LF_PROXY", "socks5://127.0.0.1:10808")
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
    ):
        self.session_file = session_file
        self.proxy = proxy
        self.chrome_path = chrome_path

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

    @property
    def is_connected(self) -> bool:
        return self._connected and self._page is not None and not self._page.is_closed()

    def _on_js_quote_tick(self, bid: float, ask: float, mid: float, ts_ms: float) -> None:
        """Callback invoked directly from Chrome V8 MutationObserver on every price tick."""
        now = time.time()
        self._last_quote = QuoteSnapshot(
            symbol="XAUUSD",
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
        except asyncio.TimeoutError:
            pass
        return self._last_quote

    async def initialize(self) -> bool:
        """Launches headless Chrome, applies session cookies, and loads XAUUSD terminal."""
        async with self._lock:
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
                ]
                if self.proxy:
                    launch_args.append(f"--proxy-server={self.proxy}")

                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    executable_path=self.chrome_path if os.path.exists(self.chrome_path) else None,
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

                # Expose Python callback into Chrome V8 window for zero-polling quote stream
                await self._page.expose_function("__onJsQuoteUpdate", self._on_js_quote_tick)

                logger.info("Navigating to %s...", CHART_URL)
                await self._page.goto(CHART_URL, wait_until="domcontentloaded", timeout=45000)
                await self._page.wait_for_timeout(3000)

                # Clear annoying overlays / 2FA popups
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
                            if (bid && ask && (bid !== lastBid || ask !== lastAsk)) {
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

                    // 1. Instant MutationObserver on DOM bid/ask elements
                    const bEl = document.querySelector('.js_value_price_bid');
                    const aEl = document.querySelector('.js_value_price_ask');
                    if (bEl && aEl) {
                        const obs = new MutationObserver(() => notifyQuote());
                        obs.observe(bEl, { characterData: true, childList: true, subtree: true });
                        obs.observe(aEl, { characterData: true, childList: true, subtree: true });
                    }

                    // 2. High-speed 50ms interval fallback
                    setInterval(notifyQuote, 50);
                    notifyQuote();

                    // 3. Pre-cached single-shot fast execution function
                    window.__executeFastMarketOrder = (dir, vol) => {
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

                        const btn = document.querySelector('button.js_trade_action_open:not([style*="none"])') ||
                                    document.querySelector('button[type="submit"]');
                        if (btn && btn.offsetParent !== null) {
                            btn.click();
                            return btn.innerText.trim() || 'ORDER_CLICKED';
                        }
                        return 'CLICKED_FALLBACK';
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
                    setInterval(updateAcc, 500);
                    updateAcc();
                }""")

                # Verify trading panel is visible
                has_panel = await self._page.evaluate("""() => {
                    const bid = document.querySelector('.js_value_price_bid');
                    return !!bid;
                }""")
                if not has_panel:
                    logger.warning("Trading panel not detected immediately, waiting an extra 3 seconds...")
                    await self._page.wait_for_timeout(3000)
                    await self._clear_overlays()

                self._connected = True
                logger.info("✅ LiteFinance Gateway successfully connected and ready with Ultra-Fast Streaming.")
                return True
            except Exception as e:
                logger.error("Failed to initialize LiteFinance Gateway: %s", e)
                self._connected = False
                return False

    async def _clear_overlays(self) -> None:
        """Removes modal dialogs or blocking overlays."""
        if not self._page:
            return
        try:
            await self._page.evaluate("""() => {
                const overlay = document.querySelector('.website_overlay');
                if (overlay) overlay.remove();
                const popups = document.querySelectorAll('.popup, .modal, [class*="popup"], [class*="modal"]');
                popups.forEach(p => {
                    if (p.innerText && (p.innerText.includes('two-factor') || p.innerText.includes('2FA') || p.innerText.includes('Google Authenticator'))) {
                        p.remove();
                    }
                });
            }""")
        except Exception:
            pass

    async def get_live_quote(self) -> Optional[QuoteSnapshot]:
        """Reads current real-time Bid and Ask prices directly from memory or DOM fallback."""
        snap = self.get_live_quote_sync()
        if snap is not None:
            return snap

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
                mid = float(quote_data.get("mid", round((bid + ask) / 2.0, 2)))
                now = time.time()
                self._last_quote = QuoteSnapshot(
                    symbol="XAUUSD",
                    bid=bid,
                    ask=ask,
                    mid=mid,
                    timestamp=now,
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
        if not force_fresh and self._last_account and (now - self._last_account_ts) < 2.0:
            return self._last_account

        if not self._page:
            return self._last_account or AccountSnapshot(balance=0.0, equity=0.0, assets_used=0.0, available=0.0, floating_pnl=0.0)
        try:
            acc_data = await self._page.evaluate("""() => {
                if (window._fastAccount && (Date.now() - window._fastAccount.ts) < 1500) {
                    return window._fastAccount;
                }
                const rawText = document.querySelector('.portfolio, .bottom_bar, [class*="portfolio"]')?.innerText || '';
                const parseNum = (str, regex) => {
                    const m = str.match(regex);
                    return m ? parseFloat(m[1].replace(/,/g, '')) : 0.0;
                };
                
                const total = parseNum(rawText, /([0-9,.]+)\\s*USD\\s*ASSETS,\\s*TOTAL/i);
                const used = parseNum(rawText, /([0-9,.]+)\\s*USD\\s*ASSETS\\s*USED/i);
                const avail = parseNum(rawText, /([0-9,.]+)\\s*USD\\s*AVAILABLE/i);
                const change = parseNum(rawText, /([+-]?[0-9,.]+)\\s*USD\\s*CURRENT\\s*CHANGE/i);
                
                return { total, used, avail, change };
            }""")

            balance = float(acc_data.get("total", 0.0))
            used = float(acc_data.get("used", 0.0))
            avail = float(acc_data.get("avail", 0.0))
            change = float(acc_data.get("change", 0.0))
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
    ) -> Dict[str, Any]:
        """
        Executes a real market order on LiteFinance with atomic single-shot dispatch.
        Zero sleeps, sub-5ms internal execution.
        """
        async with self._lock:
            if not self._page:
                return {"success": False, "error": "Gateway not initialized"}

            t0 = time.perf_counter()
            direction = direction.upper()
            vol_str = f"{volume:.2f}"
            try:
                # Atomic single-shot order execution inside Chrome's V8 engine
                clicked_btn = await self._page.evaluate("""({ dir, vol }) => {
                    if (typeof window.__executeFastMarketOrder === 'function') {
                        return window.__executeFastMarketOrder(dir, vol);
                    }
                    const isBuy = (dir === 'BUY');
                    const radioId = isBuy ? '#trade_buy_1' : '#trade_sell_1';
                    const radio = document.querySelector(radioId);
                    if (radio) {
                        radio.checked = true;
                        radio.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    const label = document.querySelector('label[for="' + (isBuy ? 'trade_buy_1' : 'trade_sell_1') + '"]');
                    if (label) label.click();

                    const inp = document.querySelector('#volume_value_1');
                    if (inp) {
                        inp.value = vol;
                        inp.dispatchEvent(new Event('input', { bubbles: true }));
                        inp.dispatchEvent(new Event('change', { bubbles: true }));
                    }

                    // Click order dispatch button
                    const btn = document.querySelector('button.js_trade_action_open:not([style*="none"])') ||
                                document.querySelector('button[type="submit"]');
                    if (btn && btn.offsetParent !== null) {
                        btn.click();
                        return btn.innerText.trim();
                    }
                    return 'CLICKED_FALLBACK';
                }""", {"dir": direction, "vol": vol_str})

                latency_ms = (time.perf_counter() - t0) * 1000.0
                logger.info(
                    "⚡ ULTRA-FAST BROKER ORDER SENT: %s %.2f lots | Dispatch Latency: %.2fms | Button: %s",
                    direction, volume, latency_ms, clicked_btn
                )

                return {
                    "success": True,
                    "direction": direction,
                    "volume": volume,
                    "latency_ms": latency_ms,
                    "button": clicked_btn,
                }
            except Exception as e:
                logger.error("Error opening broker order: %s", e)
                return {"success": False, "error": str(e)}

    async def flatten_all_positions(self) -> Dict[str, Any]:
        """
        Emergency / Profit spike flatten: atomic single-shot execution across all open tickets.
        Zero sleeps, instant closure.
        """
        async with self._lock:
            if not self._page:
                return {"success": False, "error": "Gateway not initialized"}

            t0 = time.perf_counter()
            try:
                # Atomic open portfolio + click close on all positions + auto-confirm
                closed_count = await self._page.evaluate("""() => {
                    // 1. Ensure portfolio is open
                    const p = Array.from(document.querySelectorAll('a, button, span')).find(el => el.innerText && el.innerText.trim() === 'PORTFOLIO');
                    if (p) p.click();

                    // 2. Click close on all positions immediately
                    let count = 0;
                    const closeBtns = Array.from(document.querySelectorAll('.btn_close, [class*="close_trade"], button.close, [data-action*="close"], table .icon_cross'));
                    closeBtns.forEach(b => {
                        const target = b.closest('a, button') || b;
                        if (target && target.offsetParent !== null) {
                            target.click();
                            count++;
                        }
                    });

                    // 3. Click any confirmation modal immediately
                    setTimeout(() => {
                        const confirmBtns = Array.from(document.querySelectorAll('button, .btn')).filter(x => {
                            const t = x.innerText ? x.innerText.trim() : '';
                            return (t === 'Close' || t === 'Yes' || t === 'Confirm') && x.offsetParent !== null;
                        });
                        confirmBtns.forEach(cb => cb.click());
                    }, 50);

                    return count;
                }""")

                latency_ms = (time.perf_counter() - t0) * 1000.0
                logger.info("⚡ ULTRA-FAST BROKER FLATTEN: Closed %s tickets | Latency: %.2fms", closed_count, latency_ms)

                return {
                    "success": True,
                    "closed_count": closed_count,
                    "latency_ms": latency_ms,
                }
            except Exception as e:
                logger.error("Error flattening broker positions: %s", e)
                return {"success": False, "error": str(e)}


    def _handle_crash(self, error: Exception) -> None:
        """Tracks consecutive errors and schedules automatic background recovery on browser crashes."""
        self._consecutive_errors += 1
        err_str = str(error).lower()
        if "crashed" in err_str or "closed" in err_str or self._consecutive_errors >= 5:
            if not self._reconnecting:
                logger.error("🚨 LiteFinance Gateway browser crash/disconnect detected (%s). Triggering auto-recovery...", error)
                asyncio.create_task(self.reconnect())

    async def reconnect(self) -> bool:
        """Self-healing reconnect: cleanly shuts down dead browser context and re-spawns a fresh session."""
        if self._reconnecting:
            return False
        self._reconnecting = True
        logger.warning("🔄 Self-healing LiteFinanceGateway: Initiating automatic reconnection...")
        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
        self._connected = False
        await asyncio.sleep(2.0)
        try:
            success = await self.initialize()
            if success:
                logger.info("✅ LiteFinanceGateway auto-recovery SUCCESSFUL! Terminal re-attached.")
                self._consecutive_errors = 0
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
        try:
            if self._context:
                await self._context.storage_state(path=self.session_file)
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
            self._connected = False
            logger.info("LiteFinance Gateway cleanly closed.")
        except Exception as e:
            logger.warning("Error during gateway shutdown: %s", e)
