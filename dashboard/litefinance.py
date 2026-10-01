"""LiteFinance web terminal as a Gold Desk data source (Mac or Windows, no MT5 needed).

Drives https://my.litefinance.org/trading/chart?symbol=XAUUSD in a Chromium window with
your saved login (~/.golddesk/lf_session.json):

- candles come from the same history endpoint the LiteFinance chart uses, so bars match it;
- bid / ask are read live from the order ticket on that page;
- Buy / Sell fill the page's own ticket (direction, lots, stop loss, take profit) and press
  its button, and only when you click on the dashboard.

Playwright objects belong to the thread that created them, so one worker thread owns the
browser and every call is queued to it.
"""
from __future__ import annotations

import http.client
import json
import os
import queue
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

from engine import Bars, Spec, market_hours

HOME = Path.home() / ".golddesk"
SESSION = HOME / "lf_session.json"
BASE = "https://my.litefinance.org"
CHART = BASE + "/trading/chart?symbol=XAUUSD"
RES = {"M1": "1", "M5": "5", "M15": "15", "H1": "60", "H4": "240"}
SEC = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400}

# LiteFinance answers HTTP 429 and blocks the address for a while when candles are asked for too often (the
# VPS chart shares that address). The live bid keeps the last candle current, so history is re-read rarely:
REFRESH_LIVE = 60.0      # seconds between re-reads while the broker page streams prices ...
REFRESH_IDLE = 10.0      # ... and without live prices, when the candles are the only price there is
REFRESH_HTF = 120.0      # M15 / H1 / H4
SETTLE = 3.0             # after a bar closes, wait this long and read its final values once
SPACING = 1.5            # seconds between any two requests
BACKOFF = (60.0, 600.0)  # after a 429: wait 1 minute, doubling up to 10, before asking again
STALE = 120.0            # broker page prices frozen this long while gold trades: reload it, then restart it
DEAD = 15.0              # broker page unreadable this long: restart the browser


# Order ticket, mapped from the logged-in page on 2026-10-01.
SEL = {
    "bid": ".js_value_price_bid",
    "ask": ".js_value_price_ask",
    "market": "#trade_type_now_1",
    "buy": "#trade_buy_1",
    "sell": "#trade_sell_1",
    "volume": "#volume_value_1",
    "sl": "#stop_loss_price_1",
    "tp": "#take_profit_price_1",
    "submit": "button.js_trade_action_open, button.btn.btn_red.btn_large, button.btn.btn_large",
}

# One call: pick market + side, type lots / SL / TP, check what the ticket shows, press its button.
ORDER_JS = """async ({sel, side, lots, sl, tp, dry}) => {
  const t0 = performance.now();
  const q = (s) => document.querySelector(s);
  const visible = (el) => !!el && el.offsetParent !== null && getComputedStyle(el).visibility !== 'hidden';
  const pick = (id) => {
    const el = q(id); if (!el) return false;
    const lab = document.querySelector('label[for="' + id.slice(1) + '"]');
    if (!el.checked) (lab || el).click();
    if (!el.checked) { el.checked = true; el.dispatchEvent(new Event('change', {bubbles: true})); }
    return el.checked;
  };
  const setVal = (s, v) => {
    const el = q(s); if (!el) return 'missing';
    if (el.disabled || el.readOnly) return 'locked';
    const proto = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
    el.focus(); proto.set.call(el, v);
    for (const t of ['input', 'change', 'keyup', 'blur']) el.dispatchEvent(new Event(t, {bubbles: true}));
    return el.value;
  };
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  const findBtn = () => Array.from(document.querySelectorAll(sel.submit)).find(b => visible(b) && !b.disabled);
  if (q(sel.market)) pick(sel.market);
  if (!pick(side === 'BUY' ? sel.buy : sel.sell)) return {ok: false, message: 'Could not pick ' + side + ' on the LiteFinance ticket'};
  const vol = setVal(sel.volume, String(lots));
  if (vol === 'missing' || vol === 'locked') return {ok: false, message: 'LiteFinance lot box is ' + vol};
  if (Math.abs(parseFloat(String(vol).replace(',', '.')) - lots) > 1e-9) return {ok: false, message: 'LiteFinance lot box shows ' + vol + ', not ' + lots + '. Nothing sent.'};
  for (const [k, v] of [['sl', sl], ['tp', tp]]) {
    const got = setVal(sel[k], v ? String(v) : '');
    if (v && (got === 'missing' || got === 'locked')) return {ok: false, message: 'LiteFinance ' + (k === 'sl' ? 'stop loss' : 'take profit') + ' box is ' + got + '. Nothing sent.'};
  }
  let btn = findBtn(), label = btn ? (btn.innerText || '').toUpperCase() : '';
  for (let i = 0; i < 20 && !(btn && label.includes(side)); i++) {   // the button relabels a moment after the side switch
    await wait(5); btn = findBtn(); label = btn ? (btn.innerText || '').toUpperCase() : '';
  }
  if (!btn) return {ok: false, message: 'LiteFinance order button not found. Nothing sent.'};
  if (!label.includes(side)) return {ok: false, message: 'LiteFinance button says "' + label.trim() + '", expected ' + side + '. Nothing sent.'};
  if (dry) return {ok: true, message: 'dry run: ticket filled, button not pressed', button: label.trim()};
  const clickedAt = performance.now();
  btn.click();
  return {ok: true, message: 'Sent to LiteFinance', button: label.trim(), click_ms: clickedAt - t0};
}"""

NOTES_JS = """() => {
  const visible = (el) => !!el && el.offsetParent !== null;
  return Array.from(document.querySelectorAll('[class*="notif"], [class*="toast"], [class*="alert"], [class*="error"]'))
    .filter(visible).map(e => e.innerText.trim()).filter(Boolean).slice(-2).join(' | ');
}"""

# Open trades as LiteFinance prints them: the smallest rows that mention XAUUSD and Buy/Sell.
# Shown as plain text until the panel's columns are mapped; never clicked.
ROWS_JS = """() => {
  const isRow = (t) => /XAUUSD/i.test(t) && /\\b(buy|sell)\\b/i.test(t) && t.length < 400;
  const out = [], seen = new Set();
  for (const el of document.querySelectorAll('tr, li, [class*="row"], [class*="item"], [class*="trade"], [class*="position"]')) {
    if (el.offsetParent === null || el.closest('.js_trade_form, form')) continue;
    const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
    if (!isRow(t)) continue;
    if (Array.from(el.children).some(c => isRow((c.innerText || '').replace(/\\s+/g, ' ')))) continue;
    if (seen.has(t)) continue;
    seen.add(t); out.push({text: t, html: el.parentElement ? el.parentElement.outerHTML.slice(0, 20000) : ''});
    if (out.length >= 30) break;
  }
  return out;
}"""

OPEN_PORTFOLIO_JS = """() => {
  const el = Array.from(document.querySelectorAll('a, button, span, div'))
    .find(e => e.children.length === 0 && /^portfolio$/i.test((e.innerText || '').trim()));
  if (el) el.click();
  return !!el;
}"""

QUOTE_JS = """(sel) => {
  const n = (s) => { const e = document.querySelector(s); return e ? parseFloat(e.textContent.replace(/[^0-9.]/g, '')) : NaN; };
  return {bid: n(sel.bid), ask: n(sel.ask), url: location.href};
}"""

FETCH_JS = """async (u) => {
  const r = await fetch(u, {credentials: 'include', headers: {'X-Requested-With': 'XMLHttpRequest'}});
  return {status: r.status, text: await r.text()};
}"""

ACCOUNT_JS = """() => {
  const t = (document.querySelector('.portfolio, .bottom_bar, [class*="portfolio"], header') || document.body).innerText || '';
  const num = (re) => { const m = t.match(re); return m ? parseFloat(m[1].replace(/[\\s,]/g, '')) : null; };
  return {balance: num(/Balance[^0-9-]*(-?[0-9][0-9\\s,]*\\.?[0-9]*)/i),
          equity: num(/Equity[^0-9-]*(-?[0-9][0-9\\s,]*\\.?[0-9]*)/i)};
}"""


def parse_history(text: str) -> list:
    """LiteFinance history -> [[t, o, h, l, c, v], ...] in UTC seconds, oldest first."""
    try:
        d = json.loads(text)
    except ValueError:
        return []
    if isinstance(d, dict) and isinstance(d.get("data"), dict):       # LiteFinance: {"status":"success","data":{o,h,l,c,v,t}}
        d = d["data"]
    rows = []
    if isinstance(d, dict) and isinstance(d.get("t"), list):              # TradingView UDF
        n = len(d["t"])
        vs = d.get("v") or [0] * n
        rows = [[d["t"][i], d["o"][i], d["h"][i], d["l"][i], d["c"][i], vs[i] if i < len(vs) else 0] for i in range(n)]
    else:
        items = d
        if isinstance(d, dict):
            items = next((d[k] for k in ("data", "bars", "candles", "history", "items") if isinstance(d.get(k), list)), [])
        for x in items or []:
            if isinstance(x, dict):
                g = lambda *ks: next((x[k] for k in ks if k in x and x[k] is not None), None)
                r = [g("time", "t", "timestamp", "date"), g("open", "o"), g("high", "h"), g("low", "l"),
                     g("close", "c"), g("volume", "v", "tick_volume") or 0]
            elif isinstance(x, (list, tuple)) and len(x) >= 5:
                r = list(x[:6]) + [0] * (6 - len(x[:6]))
            else:
                continue
            if None not in r[:5]:
                rows.append(r)
    out = []
    for r in rows:
        try:
            t = float(r[0])
        except (TypeError, ValueError):
            continue
        if t > 1e11:
            t /= 1000.0
        out.append([int(t)] + [float(v) for v in r[1:6]])
    out.sort(key=lambda r: r[0])
    return out


def find_browsers() -> list:
    """Chrome / Chromium builds already on this machine (other Playwright, Puppeteer or Selenium downloads,
    system installs), for when Playwright's own download is missing. Newest first within each kind."""
    caches = [Path.home() / ".cache", Path("/root/.cache"), Path.home() / "Library" / "Caches"]
    pw_dirs = [c / "ms-playwright" for c in caches]
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        pw_dirs.insert(0, Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]))
    globs = [(d, pat) for d in pw_dirs for pat in (
        "chromium-*/chrome-linux*/chrome", "chromium-*/chrome-mac*/*.app/Contents/MacOS/*",
        "chromium_headless_shell-*/chrome-*/headless_shell", "chromium_headless_shell-*/chrome-*/chrome-headless-shell")]
    globs += [(c, pat) for c in caches for pat in (
        "puppeteer/chrome/*/chrome-linux64/chrome",
        "puppeteer/chrome-headless-shell/*/chrome-headless-shell-linux64/chrome-headless-shell",
        "selenium/chrome/*/*/chrome")]
    found: list = []
    for root, pat in globs:
        found += sorted((str(x) for x in root.glob(pat)), reverse=True)
    for name in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser"):
        if shutil.which(name):
            found.append(shutil.which(name))
    found += ["/opt/google/chrome/chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    out: list = []
    for x in found:
        if x not in out and os.path.isfile(x) and os.access(x, os.X_OK):
            out.append(x)
    return out


class LiteFinanceSource:
    """LiteFinance's web terminal in a Chromium page. Candles come straight from LiteFinance's public history,
    so the dashboard runs even while the broker page can't open (no browser, logged out); the page keeps
    retrying in the background and `broker` says why trading is off meanwhile."""
    kind = "litefinance"

    def __init__(self, headless: bool = False, account_type: str | None = None, url: str = CHART,
                 session: Path = SESSION, chrome: str | None = None, dry_run: bool = False):
        if not Path(session).exists():
            raise RuntimeError(f"No saved LiteFinance login at {session}. Run: python3 server.py --litefinance-login")
        self.symbol = "XAUUSD"
        self.url, self.session, self.headless, self.chrome = url, Path(session), headless, chrome
        self.base = url.split("/trading")[0] if "/trading" in url else BASE
        self.account_type = account_type
        self.dry_run = dry_run
        self.jobs: queue.Queue = queue.Queue()
        self._job_lock = threading.Lock()       # a job either runs on the page or is dropped, never both
        self._freezes = 0                       # page restarts in a row that didn't bring prices back
        self.cache: dict = {tf: [] for tf in RES}
        self.form: dict = {}
        self.last_quote = None
        self.spread = 0.22
        self._acct = {"at": 0.0}
        self._fresh_at: dict = {}
        self._quote_at = 0.0
        self._next_req = 0.0                    # earliest time for the next history request
        self._hold_until = 0.0                  # no history requests before this (after a 429 or an outage)
        self._backoff = BACKOFF[0]
        self.feed_note: str | None = None       # why the candles are not updating, shown on the page
        self.data_lock = threading.Lock()
        self.http_lock = threading.Lock()
        self._conn = None
        self.note = ""          # last message LiteFinance showed after an order
        self.rows: list = []    # open trades as LiteFinance prints them
        self._note_at = 0.0
        self.connected = False
        self.browser = None
        self._boot_err = "Opening the LiteFinance page..."
        ready = threading.Event()
        threading.Thread(target=self._worker, args=(ready,), daemon=True).start()
        ready.wait(25)                    # the dashboard starts after this either way; the page may connect later
        if not self.connected:
            print(f"Broker page not connected yet: {self._boot_err} (the dashboard runs; it keeps trying)", flush=True)

    @property
    def caps(self) -> dict:
        return {"positions": False, "trading": self.connected}

    def broker(self) -> dict:
        return {"connected": self.connected, "message": None if self.connected else self._boot_err}

    # ------------------------------------------------------------ browser thread
    def _launch(self, pw):
        kw = {"headless": self.headless, "args": ["--disable-background-timer-throttling",
                                                  "--disable-renderer-backgrounding",
                                                  "--disable-backgrounding-occluded-windows"]}
        exe = self.chrome or os.environ.get("GOLDDESK_CHROME")
        errors = []
        for cand in ([exe] if exe else []) + [None] + find_browsers():   # None = Playwright's own download
            try:
                browser = pw.chromium.launch(**kw, **({"executable_path": cand} if cand else {}))
                if cand:
                    print(f"Browser for LiteFinance: {cand}", flush=True)
                return browser
            except Exception as e:
                errors.append(f"{cand or 'Playwright chromium'}: {str(e).splitlines()[0]}")
        print("Browsers tried: " + " | ".join(errors), flush=True)
        raise RuntimeError("no Chrome / Chromium could start on this machine")

    def _open(self, pw) -> None:
        """Browser, saved login, chart page with live prices. Raises with a plain reason."""
        self.browser = self._launch(pw)
        self.ctx = self.browser.new_context(storage_state=str(self.session), viewport={"width": 1366, "height": 850})
        self.page = self.ctx.new_page()
        resp = self.page.goto(self.url, wait_until="domcontentloaded", timeout=45000)
        if resp is not None and resp.status == 429:
            raise RuntimeError("LiteFinance is refusing this server for now (too many requests, HTTP 429)")
        try:
            self.page.wait_for_selector(SEL["bid"], timeout=40000)
        except Exception:
            try:
                HOME.mkdir(parents=True, exist_ok=True)
                self.page.screenshot(path=str(HOME / "lf_boot.png"))     # what the page showed, for a look later
                login = "login" in self.page.url or self.page.query_selector("input[type=password]") is not None
            except Exception:
                login = False
            raise RuntimeError("LiteFinance asked to log in again: the saved login wasn't accepted here" if login
                               else f"the LiteFinance chart showed no prices ({self.page.url})")
        self._close_popups()

    def _worker(self, ready: threading.Event) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self._boot_err = "the playwright package is missing (python3 -m pip install playwright)"
            ready.set()
            return
        pw = None
        while True:                                # open the page, keep it fed, reopen it if it dies or freezes
            pw = self._connect(sync_playwright, pw, ready)
            try:
                why = self._run()
            except Exception as e:
                why = f"the page loop failed ({str(e).splitlines()[0][:80]})"
            self.connected, self._boot_err = False, f"Broker page: {why}; opening it again"
            print(self._boot_err, flush=True)
            self._drop_jobs("The LiteFinance page is reopening")
            self._close_browser()

    def _drop_jobs(self, why: str) -> None:
        """Answer every waiting job with an error: nothing queued now may reach the next page late."""
        while True:
            try:
                fn, box, done = self.jobs.get_nowait()
            except queue.Empty:
                return
            with self._job_lock:
                if box.get("dropped"):
                    continue
                box["started"], box["e"] = True, RuntimeError(f"{why}; nothing was sent")
            done.set()

    def _close_browser(self) -> None:
        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass
        self.browser = None

    def _connect(self, sync_playwright, pw, ready: threading.Event):
        wait = 60
        while True:                                # keep trying; the dashboard runs meanwhile
            if self._hold_until > time.time():         # LiteFinance is refusing this address: wait it out
                time.sleep(max(0.0, self._hold_until - time.time()))
            try:
                pw = pw or sync_playwright().start()
                self._open(pw)
                break
            except Exception as e:
                self._boot_err = f"Broker page not connected: {str(e).splitlines()[0]}"
                print(self._boot_err, flush=True)
                self._close_browser()
                ready.set()
                time.sleep(wait)
                wait = min(wait * 2, 600)
        try:
            self._read_quote()
            self._read_account()
            before = self.page.url
            self.page.evaluate(OPEN_PORTFOLIO_JS)      # show the open-trades panel (a tab, not a trade button)
            self.page.wait_for_timeout(300)
            if self.page.url != before:                # it was a link to another page: back to the chart
                self.page.goto(self.url, wait_until="domcontentloaded", timeout=45000)
                self.page.wait_for_selector(SEL["bid"], timeout=40000)
        except Exception:
            pass
        self.connected, self._boot_err = True, None
        print("Broker page connected.", flush=True)
        ready.set()
        return pw

    def _run(self) -> str:
        """Orders first; between them keep the quote fresh. Returns why the page has to be opened again."""
        acct_at = rows_at = reloaded_at = 0.0
        last, moved_at, failing = self._prices(), time.time(), None
        while True:
            try:
                fn, box, done = self.jobs.get(timeout=0.04)
            except queue.Empty:
                now = time.time()
                try:
                    self._read_quote()
                    failing = None
                    if self._note_at and now > self._note_at:
                        self._note_at = 0.0
                        self.note = self.page.evaluate(NOTES_JS) or ""
                    if now - acct_at > 3:
                        acct_at = now
                        self._read_account()
                    if now - rows_at > 1:
                        rows_at = now
                        self._read_rows()
                except Exception as e:
                    failing = failing or now
                    if now - failing > DEAD:
                        return f"the LiteFinance page stopped answering ({str(e).splitlines()[0][:80]})"
                q = self._prices()
                if q != last:                          # prices move: the page is fine
                    last, moved_at, reloaded_at, self._freezes = q, now, 0.0, 0
                elif not (market_hours(now)[0] and market_hours(now - 600)[0]):
                    moved_at = now                     # market shut, or open under 10 min: still prices are normal
                elif now - moved_at > STALE * 2 ** min(self._freezes, 4):   # gold trades but these prices don't
                    if reloaded_at:
                        self._freezes += 1             # next time wait longer (a holiday looks the same)
                        return "LiteFinance prices stayed frozen after a reload"
                    reloaded_at = moved_at = now
                    self.connected, self._boot_err = False, "Broker page prices froze; reloading it"
                    print(self._boot_err, flush=True)
                    try:
                        self.page.reload(wait_until="domcontentloaded", timeout=45000)
                        self.page.wait_for_selector(SEL["bid"], timeout=40000)
                        self._close_popups()
                    except Exception as e:
                        return f"the LiteFinance page froze and didn't reload ({str(e).splitlines()[0][:80]})"
                    self.connected, self._boot_err = True, None
                continue
            with self._job_lock:
                if box.get("dropped"):
                    continue                           # its caller gave up waiting: never run it late
                box["started"] = True
            try:
                box["v"] = fn(self.page)
            except Exception as e:
                box["e"] = e
            done.set()

    def _prices(self):
        q = self.last_quote
        return q and (q["bid"], q["ask"])

    def _read_quote(self) -> None:
        q = self.page.evaluate(QUOTE_JS, SEL)
        if q["bid"] > 0 and q["ask"] > 0:
            self.spread = round(q["ask"] - q["bid"], 2)
            self.last_quote = {"bid": q["bid"], "ask": q["ask"], "time": int(time.time())}
            self._quote_at = time.time()

    def _read_rows(self) -> None:
        rows = self.page.evaluate(ROWS_JS)
        self.rows = [r["text"] for r in rows]
        if rows and not getattr(self, "_dumped", False):       # one local copy, so the columns can be mapped
            self._dumped = True
            try:
                HOME.mkdir(parents=True, exist_ok=True)
                (HOME / "lf_positions_dump.html").write_text(rows[0]["html"])
            except OSError:
                pass

    def _read_account(self) -> None:
        r = self.page.evaluate(ACCOUNT_JS)
        a = self._acct
        a.update({"balance": r.get("balance") or a.get("balance") or 0.0,
                  "equity": r.get("equity") or r.get("balance") or a.get("equity") or 0.0, "at": time.time()})

    def _call(self, fn, timeout: float = 30.0):
        if not self.connected:
            raise RuntimeError(self._boot_err or "Broker page not connected")
        box, done = {}, threading.Event()
        self.jobs.put((fn, box, done))
        if not done.wait(timeout):
            with self._job_lock:
                box["dropped"] = "started" not in box
            if box["dropped"]:
                raise RuntimeError("LiteFinance page is not responding; nothing was sent")
            if not done.wait(30):                  # already running on the page: wait for its answer
                raise RuntimeError("LiteFinance page is not responding; check the LiteFinance window before trading again")
        if "e" in box:
            raise box["e"]
        return box["v"]

    def _close_popups(self) -> None:
        self.page.evaluate("""() => {
          const o = document.querySelector('.website_overlay'); if (o) o.style.display = 'none';
          document.querySelectorAll('.popup .close, .modal .close, [class*="popup"] [class*="close"]').forEach(b => {
            if (b.offsetParent !== null && !b.closest('.js_trade_form, form')) b.click(); });
        }""")

    # ------------------------------------------------------------ market data
    def _fetch(self, tf: str, t_from: int, t_to: int) -> list:
        path = "/chart/get-history?" + urlencode(
            {"symbol": self.symbol, "resolution": RES[tf], "from": int(t_from), "to": int(t_to)})
        status, text = self._get(path)
        if status is None and self.connected:        # Python couldn't reach it: try through the logged-in page
            r = self._call(lambda p: p.evaluate(FETCH_JS, self.base + path))
            status, text = r["status"], r["text"]
        if status == 200:
            self._backoff, self.feed_note = BACKOFF[0], None
            return parse_history(text)
        if status == 429:
            wait, self._backoff = self._backoff, min(self._backoff * 2, BACKOFF[1])
            self._hold_until = time.time() + wait
            self.feed_note = (f"LiteFinance is refusing this server for now (too many requests). Gold Desk waits "
                              f"{int(wait)} s before asking again; the chart catches up by itself.")
        else:
            self._hold_until = time.time() + 15
            self.feed_note = (f"LiteFinance candles: {text}" if status is None else
                              f"LiteFinance candles returned HTTP {status}") + "; trying again in 15 s."
        print(self.feed_note, flush=True)
        raise RuntimeError(self.feed_note)

    def _get(self, path: str) -> tuple:
        """Candle history is public, so fetch it straight from Python on a kept-alive connection
        instead of queueing behind orders in the browser. (status, body), or (None, reason) if unreachable."""
        if not self.base.startswith(("https://", "http://")):
            return None, "no web address"
        with self.http_lock:
            wait = self._next_req - time.time()
            if wait > 0:
                time.sleep(wait)                     # never two requests in a burst
            err = "no answer"
            for _ in range(2):
                self._next_req = time.time() + SPACING
                try:
                    if self._conn is None:
                        tls = self.base.startswith("https://")
                        host = self.base.split("://", 1)[1]
                        self._conn = (http.client.HTTPSConnection if tls else http.client.HTTPConnection)(host, timeout=8)
                    self._conn.request("GET", path, headers={"User-Agent": "Mozilla/5.0 GoldDesk",
                                                             "Accept": "application/json"})
                    r = self._conn.getresponse()
                    return r.status, r.read().decode("utf-8", "replace")
                except (OSError, http.client.HTTPException) as e:
                    self._conn, err = None, str(e) or type(e).__name__
            return None, err

    def _load(self, tf: str, count: int) -> list:
        with self.data_lock:
            return self._load_locked(tf, count)

    def _due(self, tf: str, rows: list) -> bool:
        """Re-read the tail now? Once each bar has closed, then every REFRESH_* seconds."""
        sec, now, last = SEC[tf], time.time(), self._fresh_at.get(tf, 0.0)
        end = rows[-1][0] + sec + SETTLE                    # the cached last bar is final from here
        if now >= end > last:
            return True
        gap = REFRESH_HTF if sec > 300 else REFRESH_LIVE if now - self._quote_at < 5 else REFRESH_IDLE
        if now - end > 600:                                 # market closed (weekend, holiday): nothing new
            gap = max(gap, 300.0)
        return now - last >= gap

    def _load_locked(self, tf: str, count: int) -> list:
        sec, now = SEC[tf], int(time.time())
        rows = self.cache[tf]
        held = time.time() < self._hold_until
        due = not rows or self._due(tf, rows)
        if rows and (held or (not due and len(rows) >= count)):
            return rows[-count:]                     # cached; while LiteFinance refuses, whatever we have
        if held:
            raise RuntimeError(self.feed_note or "LiteFinance candles are paused for a moment")
        if due:
            self._fresh_at[tf] = time.time()
        if rows and due:                           # refresh the tail (the last bar may still be forming)
            fresh = self._fetch(tf, rows[-1][0] - 2 * sec, now + sec)
            keep = [r for r in rows if r[0] < (fresh[0][0] if fresh else now + sec)]
            rows = keep + fresh
            self.cache[tf] = rows[-max(count, 6000):]
        chunk = 5000 * sec                          # LiteFinance answers up to about a week of M1 per call
        to = rows[0][0] if rows else now + sec
        empty = 0
        while len(rows) < count and empty < 5:     # walk back through weekends and holidays
            got = [r for r in self._fetch(tf, to - chunk, to - 1) if r[0] < to]
            if got:
                rows = got + rows
                empty = 0
            else:
                empty += 1
            to -= chunk
            self.cache[tf] = rows[-max(count, 6000):]
        self.cache[tf] = rows[-max(count, 6000):]
        return rows[-count:]

    def tick(self) -> dict | None:
        return self.last_quote             # refreshed by the browser thread every ~40 ms

    def rates(self, tf: str, count: int) -> Bars:
        rows = [list(r) for r in self._load(tf, int(count))]
        sec, now = SEC[tf], int(time.time())
        start = now - now % sec
        tk = self.last_quote
        if tk:                                     # keep the forming bar current with the live bid
            bid = tk["bid"]
            if rows and rows[-1][0] == start:
                r = rows[-1]
                r[2], r[3], r[4] = max(r[2], bid), min(r[3], bid), bid
            elif not rows or rows[-1][0] < start:
                f = self.form.get(tf)
                if not f or f[0] != start:
                    o = rows[-1][4] if rows else bid
                    f = [start, o, max(o, bid), min(o, bid), bid, 0.0]
                f[2], f[3], f[4] = max(f[2], bid), min(f[3], bid), bid
                self.form[tf] = f
                rows.append(list(f))
        b = Bars(sec)
        for r in rows[-int(count):]:
            b.append(r[0], r[1], r[2], r[3], r[4], r[5], self.spread)
        return b

    def spec(self) -> Spec:
        # LiteFinance XAUUSD: 100 oz per lot, 2 decimals, 0.01 lot steps
        return Spec(point=0.01, digits=2, vpu=100.0, min_lot=0.01, lot_step=0.01, max_lot=100.0)

    def account(self) -> dict:
        a = self._acct                     # refreshed by the browser thread every 3 s
        return {"balance": a.get("balance", 0.0), "equity": a.get("equity", 0.0), "currency": "USD",
                "server": "LiteFinance web", "company": "LiteFinance",
                "mode": self.account_type or "unknown",   # never guessed: a wrong "demo" badge is worse than none
                "trade_allowed": True, "ping_ms": None, "note": self.note}

    # ------------------------------------------------------------ manual trading (only on your clicks)
    def positions(self) -> list:
        return []    # positions panel not mapped yet; they show in the LiteFinance window

    def market(self, side: str, lots: float, sl: float, tp: float) -> dict:
        if not self.connected:
            return {"ok": False, "message": self._boot_err or "Broker page not connected", "ms": 0}
        t0 = time.perf_counter()
        args = {"sel": SEL, "side": side, "lots": float(lots), "sl": float(sl or 0), "tp": float(tp or 0),
                "dry": self.dry_run}
        res = self._call(lambda p: p.evaluate(ORDER_JS, args), timeout=15)
        total = (time.perf_counter() - t0) * 1000
        # time until LiteFinance's own button was pressed (the page then sends it to the broker)
        res["ms"] = round(res.pop("click_ms"), 1) if "click_ms" in res else round(total, 1)
        res["price"] = (self.last_quote or {}).get("ask" if side == "BUY" else "bid")
        if res.get("ok"):
            self.note, self._note_at = "", time.time() + 0.8   # read LiteFinance's reply a moment later
        return res

    def _not_yet(self, what: str) -> dict:
        return {"ok": False, "message": f"{what} from Gold Desk is not wired to LiteFinance yet. "
                                        "Use the LiteFinance window for now.", "ms": 0}

    def close(self, ticket: int, volume: float | None = None) -> dict:
        return self._not_yet("Closing")

    def modify(self, ticket: int, sl: float, tp: float) -> dict:
        return self._not_yet("Moving the stop")


def login(session: Path = SESSION, url: str = CHART) -> None:
    """Open a window, let you log in by hand, save the session when the chart's prices show."""
    from playwright.sync_api import sync_playwright
    session.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=False)
        ctx = b.new_context(storage_state=str(session)) if session.exists() else b.new_context()
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded")
        print("Log in to LiteFinance in the window that opened. It saves by itself once the XAUUSD chart shows.")
        deadline = time.time() + 900
        while time.time() < deadline:
            page.wait_for_timeout(2000)
            try:
                if page.query_selector(SEL["bid"]):
                    ctx.storage_state(path=str(session))
                    os.chmod(session, 0o600)
                    print(f"Saved your login to {session}. Start Gold Desk with: python3 server.py --litefinance")
                    b.close()
                    return
                u = page.url
                if "login" not in u and "openPopup" not in u and "/trading/" not in u and "my.litefinance" in u:
                    page.goto(url, wait_until="domcontentloaded")
            except Exception:
                pass
        b.close()
        raise SystemExit("Timed out waiting for the LiteFinance chart. Run it again when ready.")
