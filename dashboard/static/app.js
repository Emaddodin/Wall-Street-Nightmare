/* Gold Desk: live chart, one-click BUY / SELL, open trades, close all. Prices stream in; orders go out on click. */
(() => {
  "use strict";
  const TOKEN = document.querySelector('meta[name="dash-token"]').content;
  const $ = (id) => document.getElementById(id);
  const TFSEC = { M1: 60, M5: 300, M15: 900, H1: 3600 };
  const C = { buy: "#2f7bf5", sell: "#e5533c", up: "#2fb67c", down: "#e5484d", gold: "#d6ad52", dim: "#8e8a80", line: "#262a2f", bg: "#0e0f11" };
  const store = {
    get(k, d) { try { const v = localStorage.getItem("gd3_" + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("gd3_" + k, JSON.stringify(v)); } catch { /* storage blocked */ } },
  };

  let S = null;            // latest /api/state
  let Q = null;            // latest streamed quote
  let tf = store.get("tf", "M1");
  let lastQuoteAt = 0;
  let shownNote = "";
  const fmt = (x) => (x == null || !Number.isFinite(+x)) ? "-" : (+x).toFixed(2);
  const money = (x) => (x == null || !Number.isFinite(+x)) ? "-" : (x < 0 ? "−$" : "$") + Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const signed = (x) => (x == null || !Number.isFinite(+x)) ? "-" : (x > 0 ? "+" : "") + money(x);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const bigPx = (x) => { const s = fmt(x); return s === "-" ? s : `${s.slice(0, -2)}<em>${s.slice(-2)}</em>`; };
  const tone = (x) => (x > 0 ? "up" : x < 0 ? "down" : "");
  const clock = (sec) => new Date(sec * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });

  const get = (p) => fetch(p).then((r) => r.json());
  const post = (p, b) => fetch(p, { method: "POST", headers: { "Content-Type": "application/json", "X-Dash-Token": TOKEN }, body: JSON.stringify(b || {}) }).then((r) => r.json());

  // ---------------------------------------------------------------- chart
  const chart = LightweightCharts.createChart($("chart"), {
    autoSize: true,
    layout: { background: { type: "solid", color: C.bg }, textColor: C.dim, fontSize: 12, fontFamily: "JetBrains Mono, ui-monospace, Menlo, monospace" },
    grid: { vertLines: { color: "#16181b" }, horzLines: { color: "#16181b" } },
    rightPriceScale: { borderColor: C.line, scaleMargins: { top: 0.12, bottom: 0.08 } },
    timeScale: { borderColor: C.line, timeVisible: true, secondsVisible: false, rightOffset: 10 },
    crosshair: { mode: 0 },
  });
  const series = chart.addCandlestickSeries({
    upColor: C.up, downColor: C.down, borderVisible: false, wickUpColor: C.up, wickDownColor: C.down,
    priceFormat: { type: "price", precision: 2, minMove: 0.01 },
  });
  const forecast = chart.addLineSeries({ color: C.gold, lineWidth: 2, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  let last = null, loadedTf = null, first = 0;

  async function loadCandles() {
    const want = tf;
    const rows = await get(`/api/candles?tf=${want}&count=1200`).catch(() => null);
    if (!Array.isArray(rows) || want !== tf) return;
    series.setData(rows);
    loadedTf = want;
    first = rows.length ? rows[0].time : 0;
    last = rows.length ? { ...rows[rows.length - 1] } : null;
    markerKey = ""; fcKey = "";
    drawMarkers(); drawForecast();
  }

  // live candle from the streamed bid, the same way the broker's chart builds it
  function liveCandle(bid) {
    if (!last || loadedTf !== tf || !S) return;
    const sec = TFSEC[tf];
    const now = Date.now() / 1000 + ((S.clock && S.clock.utc_offset_h) || 0) * 3600;
    const bucket = now - (now % sec);
    if (bucket > last.time) last = { time: Math.floor(bucket), open: last.close, high: Math.max(last.close, bid), low: Math.min(last.close, bid), close: bid };
    else { last.high = Math.max(last.high, bid); last.low = Math.min(last.low, bid); last.close = bid; }
    series.update(last);
  }

  // server copy of the bar every few seconds (fixes any tick the stream missed)
  async function syncTail() {
    const want = tf;
    const rows = await get(`/api/candles?tf=${want}&count=3`).catch(() => null);
    if (!Array.isArray(rows) || want !== tf || loadedTf !== want) return;
    for (const r of rows) if (!last || r.time >= last.time) { series.update(r); last = { ...r }; }
  }

  let markerKey = "";
  function drawMarkers() {
    if (!S || loadedTf !== tf) return;
    const sec = TFSEC[tf];
    const list = (S.trades || []).slice(-40);
    if (S.active) list.push(S.active);
    const m = list.map((t) => ({
      time: t.t_bar - (t.t_bar % sec), position: t.dir === 1 ? "belowBar" : "aboveBar",
      color: t.dir === 1 ? C.buy : C.sell, shape: t.dir === 1 ? "arrowUp" : "arrowDown", text: t.dir === 1 ? "BUY" : "SELL",
    })).filter((x) => x.time >= first).sort((a, b) => a.time - b.time);
    const key = tf + JSON.stringify(m.map((x) => [x.time, x.text]));
    if (key !== markerKey) { series.setMarkers(m); markerKey = key; }
  }

  // Kronos forecast path (state.kronos.path: M1 bars on the broker clock), drawn on the 1m chart only
  let fcKey = "";
  function drawForecast() {
    const k = S && S.kronos;
    const ok = !!(tf === "M1" && loadedTf === tf && k && Array.isArray(k.path) && k.path.length && last);
    $("legend").hidden = !ok;
    const key = ok ? tf + k.t : "";
    if (key === fcKey) return;
    fcKey = key;
    forecast.setData(ok ? [{ time: k.t, value: k.last }, ...k.path.filter((p) => p.time > k.t)] : []);
  }

  let lines = [], linesKey = "";
  function drawLines() {
    const want = [];
    for (const p of S.positions || []) {
      want.push([p.open, p.side === "BUY" ? C.buy : C.sell, `${p.side} ${p.volume}`, 0]);
      if (p.sl) want.push([p.sl, C.down, "SL", 2]);
      if (p.tp) want.push([p.tp, C.up, "TP", 2]);
    }
    const key = JSON.stringify(want);
    if (key === linesKey) return;
    linesKey = key;
    lines.forEach((l) => series.removePriceLine(l));
    lines = want.map(([price, color, title, lineStyle]) => series.createPriceLine({ price, color, title, lineStyle, lineWidth: 1, axisLabelVisible: true }));
  }

  document.querySelectorAll("#tfs button").forEach((b) => {
    b.classList.toggle("on", b.dataset.tf === tf);
    b.addEventListener("click", () => {
      tf = b.dataset.tf; store.set("tf", tf);
      document.querySelectorAll("#tfs button").forEach((x) => x.classList.toggle("on", x === b));
      loadCandles();
    });
  });

  // ---------------------------------------------------------------- live prices (pushed by the server)
  function showQuote(bid, ask) {
    $("bid").innerHTML = bigPx(bid);
    $("ask").innerHTML = bigPx(ask);
    $("spr").textContent = fmt(ask - bid);
  }
  function connectLive() {
    const es = new EventSource("/api/live");
    es.onmessage = (e) => {
      const q = JSON.parse(e.data);
      Q = q; lastQuoteAt = Date.now();
      showQuote(q.bid, q.ask);
      liveCandle(q.bid);
    };
    es.onerror = () => { es.close(); setTimeout(connectLive, 1000); };
  }
  setInterval(() => {
    const age = Date.now() - lastQuoteAt;
    const on = age < 15000;
    $("conn").classList.toggle("on", on);
    $("connTxt").textContent = !lastQuoteAt ? "Connecting" : on ? "Live prices" : "Prices stopped";
    $("buy").disabled = $("sell").disabled = !on;
  }, 500);

  // ---------------------------------------------------------------- lots
  const step = () => (S && S.spec && S.spec.lot_step) || 0.01;
  const roundLots = (v) => Math.max(0, Math.round(v / step()) * step());
  function setLots(v) {
    const x = roundLots(v);
    $("lots").value = x.toFixed(2);
    $("lotsTxt").textContent = x.toFixed(2);
    store.set("lots", x);
    document.querySelectorAll("#chips button").forEach((b) => b.classList.toggle("on", Math.abs(+b.textContent - x) < 1e-9));
  }
  $("lotDown").addEventListener("click", () => setLots(Math.max(step(), +$("lots").value - step())));
  $("lotUp").addEventListener("click", () => setLots(+$("lots").value + step()));
  $("lots").addEventListener("change", () => setLots(+$("lots").value || step()));
  $("lots").addEventListener("keydown", (e) => {
    if (e.key === "ArrowUp") { e.preventDefault(); setLots(+$("lots").value + step()); }
    if (e.key === "ArrowDown") { e.preventDefault(); setLots(Math.max(step(), +$("lots").value - step())); }
  });
  document.querySelectorAll("#chips button").forEach((b) => b.addEventListener("click", () => setLots(+b.textContent)));
  setLots(store.get("lots", 0.01));

  // stop loss / take profit summary on the collapsed row
  function sltpSummary() {
    const sl = parseFloat($("sl").value) || 0, tp = parseFloat($("tp").value) || 0;
    $("sltpSet").textContent = sl || tp ? `SL ${sl ? fmt(sl) : "none"} · TP ${tp ? fmt(tp) : "none"}` : "none";
  }
  $("sl").addEventListener("input", sltpSummary);
  $("tp").addEventListener("input", sltpSummary);

  // ---------------------------------------------------------------- what just happened
  const ICON = { ok: "✓", bad: "!", wait: "…", idle: "✓" };
  function result(kind, what, sub) {
    const el = $("result");
    el.className = "result " + kind;
    el.innerHTML = `<span class="ico">${ICON[kind] || "✓"}</span><div class="what">${esc(what)}</div><div class="sub">${esc(sub || "")}</div>`;
    void el.offsetWidth; el.classList.add("pop");
  }

  // ---------------------------------------------------------------- orders: one click, no confirm
  let busy = false;
  async function order(side) {
    if (busy) return;
    const lots = roundLots(+$("lots").value);
    const sl = parseFloat($("sl").value) || 0, tp = parseFloat($("tp").value) || 0;
    busy = true;
    const btn = $(side === "BUY" ? "buy" : "sell");
    btn.classList.add("sending");
    result("wait", `Sending ${side} ${lots.toFixed(2)}`, "");
    const t0 = performance.now();
    let r;
    try { r = await post("/api/order", { side, lots, sl, tp }); } catch { r = { ok: false, message: "Gold Desk server is not running" }; }
    const rt = Math.round(performance.now() - t0);
    btn.classList.remove("sending");
    setTimeout(() => { busy = false; }, 150);     // stops an accidental double click, still allows fast repeat orders
    if (r.ok) result("ok", `${side} ${lots.toFixed(2)} sent${r.price ? " at " + fmt(r.price) : ""}`,
      `${rt} ms${sl || tp ? ` · SL ${sl ? fmt(sl) : "none"} · TP ${tp ? fmt(tp) : "none"}` : ""}`);
    else result("bad", `${side} not sent`, r.message || "");
    refresh();
  }
  $("buy").addEventListener("click", () => order("BUY"));
  $("sell").addEventListener("click", () => order("SELL"));

  $("closeAll").addEventListener("click", async () => {
    result("wait", "Closing all trades", "");
    const t0 = performance.now();
    let r;
    try { r = await post("/api/close_all", {}); } catch { r = { ok: false, message: "Gold Desk server is not running" }; }
    result(r.ok ? "ok" : "bad", r.ok ? "All trades closed" : "Close all failed", `${r.message || ""} · ${Math.round(performance.now() - t0)} ms`);
    refresh();
  });

  $("pos").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-t]");
    if (!b) return;
    b.disabled = true;
    result("wait", `Closing ${b.dataset.label}`, "");
    let r;
    try { r = await post("/api/close", { ticket: +b.dataset.t }); } catch { r = { ok: false, message: "Gold Desk server is not running" }; }
    result(r.ok ? "ok" : "bad", r.ok ? `Closed ${b.dataset.label}` : `${b.dataset.label} not closed`, r.message || "");
    refresh();
  });

  document.querySelector(".sig").addEventListener("click", (e) => {
    const u = e.target.closest(".use");
    if (!u || !S) return;
    const lv = S.active && { sl: S.active.sl, tp: S.active.tp2 };
    if (!lv) return;
    $("sl").value = lv.sl ? fmt(lv.sl) : ""; $("tp").value = lv.tp ? fmt(lv.tp) : "";
    $("protect").open = true;
    sltpSummary();
  });

  // ---------------------------------------------------------------- signals
  // Kronos arrives as state.kronos: {status, error, and once it has forecast: t, last, target, move, atr, dir, call, minutes, model, path}
  function renderKronos() {
    const k = S.kronos, el = $("kronos");
    if (!k) {
      el.innerHTML = `<span class="name">Kronos forecast</span><span></span><span class="call flat">Off</span><span></span>
        <span class="meta">Start Gold Desk with --kronos to see its up or down call here.</span>`;
      return;
    }
    if (!k.path) {
      el.innerHTML = `<span class="name">Kronos forecast</span><span></span><span class="call flat">${k.status === "off" ? "Off" : "Loading"}</span><span></span>
        <span class="meta">${esc(k.error || (k.status === "loading model" ? "Loading the model. The first run downloads it." : "Waiting for the next closed 1m candle."))}</span>`;
      return;
    }
    const d = k.dir;
    const atr = +k.atr;
    const strength = atr > 0 ? Math.min(100, Math.round(Math.abs(k.move) / atr * 50)) : null;   // a 2 ATR move fills the bar
    el.innerHTML = `<span class="name">Kronos · next ${esc(k.minutes)} min</span><span></span>
      <span class="call ${d > 0 ? "up" : d < 0 ? "down" : "flat"}">${d > 0 ? "▲ UP" : d < 0 ? "▼ DOWN" : "— FLAT"} <span class="num" style="font-size:14px">${fmt(k.target)} (${k.move >= 0 ? "+" : ""}${fmt(k.move)})</span></span><span></span>
      ${strength != null ? `<span class="meter"><i style="width:${strength}%;background:${d > 0 ? C.up : d < 0 ? C.down : C.dim}"></i></span>` : ""}
      <span class="meta">${esc(k.model || "Kronos")} forecast, gold dashed line on the 1m chart. Not proven on gold yet.${k.error ? " " + esc(k.error) : ""}</span>`;
  }
  function renderScalper() {
    const s = S.active;
    $("scalper").innerHTML = s
      ? `<span class="name">M1 scalper</span><button class="use" data-src="scalper">Use SL/TP</button>
         <span class="call ${s.dir === 1 ? "up" : "down"}">${s.dir === 1 ? "▲ BUY" : "▼ SELL"} <span class="num" style="font-size:14px">${fmt(s.entry)}</span></span>
         <span class="meta">SL ${fmt(s.sl)} · TP ${fmt(s.tp2)} · lost money in the backtest, use your own judgement</span>`
      : `<span class="name">M1 scalper</span><span></span><span class="call flat">No signal</span><span></span>
         <span class="meta">Lost money in the backtest. Shown for reference only.</span>`;
  }

  // ---------------------------------------------------------------- state (balance, trades, signals)
  function render() {
    const a = S.account || {};
    const mode = S.source === "demo" ? "paper" : a.mode;
    const badge = $("badge");
    badge.className = "pill " + (mode === "real" ? "real" : mode === "demo" || mode === "contest" || mode === "paper" ? "demo" : "unknown");
    badge.textContent = mode === "real" ? "REAL MONEY" : mode === "demo" ? "DEMO ACCOUNT" : mode === "paper" ? "PRACTICE · FAKE PRICES" : "DEMO OR REAL? CHECK";
    if (S.symbol) $("sym").textContent = S.symbol;
    $("bal").textContent = money(a.balance);
    $("eq").textContent = money(a.equity);
    if (!Q && S.tick) showQuote(S.tick.bid, S.tick.ask);

    const canSee = !(S.caps && S.caps.positions === false);
    const ps = S.positions || [];
    const total = ps.reduce((s, p) => s + (+p.profit || 0), 0);
    const opl = canSee ? total : (a.equity != null && a.balance != null && a.equity !== 0 ? a.equity - a.balance : null);
    $("opl").textContent = opl == null ? "-" : signed(opl);
    $("opl").className = tone(opl);

    if (a.note && a.note !== shownNote) {          // what LiteFinance itself said after the last order
      shownNote = a.note;
      const sub = document.querySelector("#result .sub");
      if (sub) sub.textContent += (sub.textContent ? " · " : "") + "LiteFinance: " + a.note;
    }
    const issues = [];
    if (S.error) issues.push(S.error);
    if (S.source === "mt5" && !a.trade_allowed) issues.push("MT5 is blocking orders: turn on the Algo Trading button in MT5.");
    $("banner").hidden = !issues.length;
    $("banner").textContent = issues.join("  ");

    // open trades
    const rows = S.broker_rows || [];
    $("npos").textContent = canSee ? ps.length : rows.length;
    $("tpl").innerHTML = canSee && ps.length ? `<span class="${tone(total)}">${signed(total)}</span>` : "";
    $("closeAll").disabled = !canSee || !ps.length;
    $("closeAll").textContent = !canSee ? "CLOSE ALL · IN LITEFINANCE FOR NOW" : ps.length > 1 ? `CLOSE ALL ${ps.length}` : "CLOSE ALL";
    const px = (p) => p.price ?? (Q ? (p.side === "BUY" ? Q.bid : Q.ask) : null);
    $("pos").innerHTML = !canSee
      ? (rows.length ? rows.map((t) => `<div class="raw">${esc(t)}</div>`).join("")
                     : `<div class="empty">No open trades. Trades you open show here as LiteFinance lists them.</div>`)
      : ps.length ? `<table><thead><tr><th>Side</th><th>Lots</th><th>Open</th><th>Now</th><th>SL</th><th>TP</th><th>P/L</th><th></th></tr></thead><tbody>${
          ps.map((p) => `<tr><td class="side ${p.side === "BUY" ? "b" : "s"}">${p.side}</td><td>${(+p.volume).toFixed(2)}</td>
            <td>${fmt(p.open)}</td><td>${fmt(px(p))}</td><td class="dim">${p.sl ? fmt(p.sl) : "-"}</td><td class="dim">${p.tp ? fmt(p.tp) : "-"}</td>
            <td class="pl ${tone(p.profit)}">${signed(p.profit)}</td>
            <td><button class="x" data-t="${p.ticket}" data-label="${p.side} ${(+p.volume).toFixed(2)}">CLOSE</button></td></tr>`).join("")}</tbody></table>`
      : `<div class="empty">No open trades.</div>`;

    // my recent orders, newest first
    const mine = (S.events || []).filter((e) => e.kind === "order" || e.kind === "reject").slice(-4).reverse();
    $("log").innerHTML = mine.length
      ? mine.map((e) => { const i = e.text.indexOf(": "); const head = i > 0 ? e.text.slice(0, i) : e.text, tail = i > 0 ? e.text.slice(i + 2) : "";
          return `<div><time>${clock(e.at)}</time><span class="${e.kind === "reject" ? "rej" : ""}">${esc(head.replace(" lots", ""))}<small>${esc(tail)}</small></span></div>`; }).join("")
      : `<div><time></time><span class="dim">Nothing sent yet.</span></div>`;

    renderKronos();
    renderScalper();
    drawMarkers();
    drawLines();
    drawForecast();
  }

  let refreshing = false;
  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    try {
      S = await get("/api/state");
      render();
      if (loadedTf !== tf) loadCandles();
    } catch { $("banner").hidden = false; $("banner").textContent = "Gold Desk server is not running. Start it again in Terminal."; }
    refreshing = false;
  }

  refresh().then(loadCandles);
  connectLive();
  setInterval(refresh, 1000);
  setInterval(syncTail, 3000);
})();
