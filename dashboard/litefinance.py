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

import json
import os
import queue
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

from engine import Bars, Spec

HOME = Path.home() / ".golddesk"
SESSION = HOME / "lf_session.json"
BASE = "https://my.litefinance.org"
CHART = BASE + "/trading/chart?symbol=XAUUSD"
RES = {"M1": "1", "M5": "5", "M15": "15", "H1": "60"}
SEC = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600}

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
  if (q(sel.market)) pick(sel.market);
  if (!pick(side === 'BUY' ? sel.buy : sel.sell)) return {ok: false, message: 'Could not pick ' + side + ' on the LiteFinance ticket'};
  await wait(60);
  const vol = setVal(sel.volume, String(lots));
  if (vol === 'missing' || vol === 'locked') return {ok: false, message: 'LiteFinance lot box is ' + vol};
  if (Math.abs(parseFloat(String(vol).replace(',', '.')) - lots) > 1e-9) return {ok: false, message: 'LiteFinance lot box shows ' + vol + ', not ' + lots + '. Nothing sent.'};
  for (const [k, v] of [['sl', sl], ['tp', tp]]) {
    const got = setVal(sel[k], v ? String(v) : '');
    if (v && (got === 'missing' || got === 'locked')) return {ok: false, message: 'LiteFinance ' + (k === 'sl' ? 'stop loss' : 'take profit') + ' box is ' + got + '. Nothing sent.'};
  }
  await wait(60);
  const btn = Array.from(document.querySelectorAll(sel.submit)).find(b => visible(b) && !b.disabled);
  if (!btn) return {ok: false, message: 'LiteFinance order button not found. Nothing sent.'};
  const label = (btn.innerText || '').toUpperCase();
  if (!label.includes(side)) return {ok: false, message: 'LiteFinance button says "' + label.trim() + '", expected ' + side + '. Nothing sent.'};
  if (dry) return {ok: true, message: 'dry run: ticket filled, button not pressed', button: label.trim()};
  const clickedAt = performance.now();
  btn.click();
  await wait(400);
  const notes = Array.from(document.querySelectorAll('[class*="notif"], [class*="toast"], [class*="alert"], [class*="error"], .popup'))
    .filter(visible).map(e => e.innerText.trim()).filter(Boolean).slice(-3);
  return {ok: true, message: notes.join(' | ') || 'Sent to LiteFinance', button: label.trim(), click_ms: clickedAt - t0};
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


class LiteFinanceSource:
    kind = "litefinance"
    caps = {"positions": False}

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
        self.cache: dict = {tf: [] for tf in RES}
        self.form: dict = {}
        self.last_quote = None
        self.spread = 0.22
        self._acct = {"at": 0.0}
        self._fresh_at: dict = {}
        ready = threading.Event()
        self._boot_err = None
        threading.Thread(target=self._worker, args=(ready,), daemon=True).start()
        ready.wait(90)
        if self._boot_err:
            raise RuntimeError(self._boot_err)
        if not ready.is_set():
            raise RuntimeError("LiteFinance page did not load within 90 seconds.")

    # ------------------------------------------------------------ browser thread
    def _worker(self, ready: threading.Event) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self._boot_err = "The playwright package is missing. Run: python3 -m pip install playwright"
            ready.set()
            return
        try:
            pw = sync_playwright().start()
            kw = {"headless": self.headless, "args": ["--disable-background-timer-throttling",
                                                      "--disable-renderer-backgrounding",
                                                      "--disable-backgrounding-occluded-windows"]}
            exe = self.chrome or os.environ.get("GOLDDESK_CHROME")
            if exe:
                kw["executable_path"] = exe
            self.browser = pw.chromium.launch(**kw)
            self.ctx = self.browser.new_context(storage_state=str(self.session), viewport={"width": 1366, "height": 850})
            self.page = self.ctx.new_page()
            self.page.goto(self.url, wait_until="domcontentloaded", timeout=45000)
            self.page.wait_for_selector(SEL["bid"], timeout=40000)
            self._close_popups()
        except Exception as e:
            msg = str(e).splitlines()[0]
            self._boot_err = (f"LiteFinance chart did not open ({msg}). If you were logged out, run: "
                              "python3 server.py --litefinance-login")
            ready.set()
            return
        ready.set()
        while True:
            fn, box, done = self.jobs.get()
            try:
                box["v"] = fn(self.page)
            except Exception as e:
                box["e"] = e
            done.set()

    def _call(self, fn, timeout: float = 30.0):
        box, done = {}, threading.Event()
        self.jobs.put((fn, box, done))
        if not done.wait(timeout):
            raise RuntimeError("LiteFinance page is not responding")
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
        u = f"{self.base}/chart/get-history?" + urlencode(
            {"symbol": self.symbol, "resolution": RES[tf], "from": int(t_from), "to": int(t_to)})
        r = self._call(lambda p: p.evaluate(FETCH_JS, u))
        if r["status"] != 200:
            raise RuntimeError(f"LiteFinance history returned HTTP {r['status']}")
        return parse_history(r["text"])

    def _load(self, tf: str, count: int) -> list:
        sec, now = SEC[tf], int(time.time())
        rows = self.cache[tf]
        if rows and time.time() - self._fresh_at.get(tf, 0) < 1.0 and len(rows) >= count:
            return rows[-count:]
        self._fresh_at[tf] = time.time()
        if rows:                                   # refresh the tail (the last bar may still be forming)
            fresh = self._fetch(tf, rows[-1][0] - 2 * sec, now + sec)
            keep = [r for r in rows if r[0] < (fresh[0][0] if fresh else now + sec)]
            rows = keep + fresh
        chunk = 1500 * sec
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
        return rows[-count:]

    def tick(self) -> dict | None:
        q = self._call(lambda p: p.evaluate(QUOTE_JS, SEL))
        if not (q["bid"] > 0 and q["ask"] > 0):
            return self.last_quote
        self.spread = round(q["ask"] - q["bid"], 2)
        self.last_quote = {"bid": q["bid"], "ask": q["ask"], "time": int(time.time())}
        return self.last_quote

    def rates(self, tf: str, count: int) -> Bars:
        rows = [list(r) for r in self._load(tf, int(count))]
        sec, now = SEC[tf], int(time.time())
        start = now - now % sec
        tk = self.last_quote or self.tick()
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
        a = self._acct
        if time.time() - a["at"] > 3:
            try:
                r = self._call(lambda p: p.evaluate(ACCOUNT_JS))
                mode = self.account_type or "unknown"   # never guessed: a wrong "demo" badge is worse than none
                a.update({"balance": r.get("balance") or a.get("balance") or 0.0,
                          "equity": r.get("equity") or r.get("balance") or a.get("equity") or 0.0,
                          "mode": mode, "at": time.time()})
            except Exception:
                a["at"] = time.time()
        return {"balance": a.get("balance", 0.0), "equity": a.get("equity", 0.0), "currency": "USD",
                "server": "LiteFinance web", "company": "LiteFinance", "mode": a.get("mode", "unknown"),
                "trade_allowed": True, "ping_ms": None}

    # ------------------------------------------------------------ manual trading (only on your clicks)
    def positions(self) -> list:
        return []    # positions panel not mapped yet; they show in the LiteFinance window

    def market(self, side: str, lots: float, sl: float, tp: float) -> dict:
        t0 = time.perf_counter()
        args = {"sel": SEL, "side": side, "lots": float(lots), "sl": float(sl or 0), "tp": float(tp or 0),
                "dry": self.dry_run}
        res = self._call(lambda p: p.evaluate(ORDER_JS, args), timeout=15)
        total = (time.perf_counter() - t0) * 1000
        # time until LiteFinance's own button was pressed (the page then sends it to the broker)
        res["ms"] = round(res.pop("click_ms"), 1) if "click_ms" in res else round(total, 1)
        res["price"] = (self.last_quote or {}).get("ask" if side == "BUY" else "bid")
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
