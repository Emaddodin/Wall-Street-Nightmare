/* Gold Desk: live chart, one-click BUY / SELL, close all. Prices stream in; orders go out on click. */
(() => {
  "use strict";
  const TOKEN = document.querySelector('meta[name="dash-token"]').content;
  const $ = (id) => document.getElementById(id);
  const TFSEC = { M1: 60, M5: 300, M15: 900, H1: 3600 };
  const store = {
    get(k, d) { try { const v = localStorage.getItem("gd2_" + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("gd2_" + k, JSON.stringify(v)); } catch { /* storage blocked */ } },
  };

  let S = null;            // latest /api/state
  let Q = null;            // latest streamed quote
  let tf = store.get("tf", "M1");
  let lastQuoteAt = 0;
  let shownNote = "";
  const fmt = (x) => (x == null || !Number.isFinite(+x)) ? "-" : (+x).toFixed(2);
  const money = (x) => x == null ? "-" : (x < 0 ? "-$" : "$") + Math.abs(x).toFixed(2);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  const get = (p) => fetch(p).then((r) => r.json());
  const post = (p, b) => fetch(p, { method: "POST", headers: { "Content-Type": "application/json", "X-Dash-Token": TOKEN }, body: JSON.stringify(b || {}) }).then((r) => r.json());

  // ---------------------------------------------------------------- chart
  const chart = LightweightCharts.createChart($("chart"), {
    autoSize: true,
    layout: { background: { type: "solid", color: "#0d1014" }, textColor: "#8792a1", fontSize: 12 },
    grid: { vertLines: { color: "#161c23" }, horzLines: { color: "#161c23" } },
    rightPriceScale: { borderColor: "#242c36" },
    timeScale: { borderColor: "#242c36", timeVisible: true, secondsVisible: false, rightOffset: 8 },
    crosshair: { mode: 0 },
  });
  const series = chart.addCandlestickSeries({
    upColor: "#34b37e", downColor: "#e5484d", borderVisible: false, wickUpColor: "#34b37e", wickDownColor: "#e5484d",
    priceFormat: { type: "price", precision: 2, minMove: 0.01 },
  });
  let last = null, loadedTf = null, first = 0;

  async function loadCandles() {
    const want = tf;
    const rows = await get(`/api/candles?tf=${want}&count=1200`).catch(() => null);
    if (!Array.isArray(rows) || want !== tf) return;
    series.setData(rows);
    loadedTf = want;
    first = rows.length ? rows[0].time : 0;
    last = rows.length ? { ...rows[rows.length - 1] } : null;
    markerKey = "";
    drawMarkers();
  }

  // live candle from the streamed bid, the same way the broker's chart builds it
  function liveCandle(bid) {
    if (!last || loadedTf !== tf || !S) return;
    const sec = TFSEC[tf];
    const now = Date.now() / 1000 + (S.clock.utc_offset_h || 0) * 3600;
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
      color: t.dir === 1 ? "#2f7df6" : "#e8553a", shape: t.dir === 1 ? "arrowUp" : "arrowDown", text: t.dir === 1 ? "BUY" : "SELL",
    })).filter((x) => x.time >= first).sort((a, b) => a.time - b.time);
    const key = tf + JSON.stringify(m.map((x) => [x.time, x.text]));
    if (key !== markerKey) { series.setMarkers(m); markerKey = key; }
  }

  let lines = [], linesKey = "";
  function drawLines() {
    const want = [];
    for (const p of S.positions || []) {
      want.push([p.open, p.side === "BUY" ? "#2f7df6" : "#e8553a", `${p.side} ${p.volume}`, 0]);
      if (p.sl) want.push([p.sl, "#e5484d", "SL", 2]);
      if (p.tp) want.push([p.tp, "#34b37e", "TP", 2]);
    }
    const a = S.active;
    if (a) {
      want.push([a.sl, "#e5484d", "signal SL", 1]);
      want.push([a.tp2, "#34b37e", "signal TP", 1]);
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
  function connectLive() {
    const es = new EventSource("/api/live");
    es.onmessage = (e) => {
      const q = JSON.parse(e.data);
      Q = q; lastQuoteAt = Date.now();
      $("bid").textContent = fmt(q.bid);
      $("ask").textContent = fmt(q.ask);
      $("spr").textContent = "spread " + fmt(q.ask - q.bid);
      liveCandle(q.bid);
    };
    es.onerror = () => { es.close(); setTimeout(connectLive, 1000); };
  }
  setInterval(() => {
    const on = Date.now() - lastQuoteAt < 15000;
    $("dot").classList.toggle("on", on);
    $("liveTxt").textContent = on ? "Live" : "No prices";
  }, 500);

  // ---------------------------------------------------------------- lots
  const step = () => (S && S.spec.lot_step) || 0.01;
  const roundLots = (v) => Math.max(0, Math.round(v / step()) * step());
  function setLots(v) {
    const x = roundLots(v);
    $("lots").value = x.toFixed(2);
    store.set("lots", x);
    document.querySelectorAll("#chips button").forEach((b) => b.classList.toggle("on", Math.abs(+b.textContent - x) < 1e-9));
  }
  $("lotDown").addEventListener("click", () => setLots(Math.max(step(), +$("lots").value - step())));
  $("lotUp").addEventListener("click", () => setLots(+$("lots").value + step()));
  $("lots").addEventListener("change", () => setLots(+$("lots").value || step()));
  document.querySelectorAll("#chips button").forEach((b) => b.addEventListener("click", () => setLots(+b.textContent)));
  setLots(store.get("lots", 0.01));

  // ---------------------------------------------------------------- orders: one click, no confirm
  function status(kind, what, sub) {
    const el = $("status");
    el.className = "status " + kind;
    el.innerHTML = `<div class="what">${esc(what)}</div><div class="sub">${esc(sub || "")}</div>`;
  }

  let busy = false;
  async function order(side) {
    if (busy) return;
    const lots = roundLots(+$("lots").value);
    const sl = parseFloat($("sl").value) || 0, tp = parseFloat($("tp").value) || 0;
    busy = true;
    const btn = $(side === "BUY" ? "buy" : "sell");
    btn.classList.add("flash");
    status("wait", `${side} ${lots.toFixed(2)} sending…`, "");
    const t0 = performance.now();
    let r;
    try { r = await post("/api/order", { side, lots, sl, tp }); } catch { r = { ok: false, message: "Gold Desk server is not running" }; }
    const rt = Math.round(performance.now() - t0);
    btn.classList.remove("flash");
    setTimeout(() => { busy = false; }, 150);     // stops an accidental double click, still allows fast repeat orders
    if (r.ok) status("ok", `${side} ${lots.toFixed(2)} sent${r.price ? " @ " + fmt(r.price) : ""}`, `${rt} ms from click to the broker's button${sl || tp ? ` · SL ${sl ? fmt(sl) : "none"} · TP ${tp ? fmt(tp) : "none"}` : ""}`);
    else status("bad", `${side} not sent`, r.message || "");
    refresh();
  }
  $("buy").addEventListener("click", () => order("BUY"));
  $("sell").addEventListener("click", () => order("SELL"));

  $("closeAll").addEventListener("click", async () => {
    status("wait", "Closing all…", "");
    const t0 = performance.now();
    let r;
    try { r = await post("/api/close_all", {}); } catch { r = { ok: false, message: "Gold Desk server is not running" }; }
    status(r.ok ? "ok" : "bad", r.ok ? "All closed" : "Close all failed", `${r.message || ""} · ${Math.round(performance.now() - t0)} ms`);
    refresh();
  });

  $("pos").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-t]");
    if (!b) return;
    status("wait", `Closing #${b.dataset.t}…`, "");
    let r;
    try { r = await post("/api/close", { ticket: +b.dataset.t }); } catch { r = { ok: false, message: "Gold Desk server is not running" }; }
    status(r.ok ? "ok" : "bad", r.ok ? `Closed #${b.dataset.t}` : `#${b.dataset.t} not closed`, r.message || "");
    refresh();
  });

  $("signal").addEventListener("click", (e) => {
    if (!e.target.closest(".use") || !S || !S.active) return;
    $("sl").value = fmt(S.active.sl); $("tp").value = fmt(S.active.tp2);
  });

  // ---------------------------------------------------------------- state (balance, positions, signal)
  function render() {
    const a = S.account;
    const mode = a.mode;
    const badge = $("badge");
    badge.className = "badge " + (mode === "demo" || mode === "contest" || S.source === "demo" ? "demo" : "real");
    badge.textContent = mode === "real" ? "REAL MONEY" : mode === "demo" ? "DEMO" : S.source === "demo" ? "PRACTICE (fake prices)" : "DEMO OR REAL?";
    $("bal").textContent = money(a.balance);
    $("eq").textContent = money(a.equity);
    if (!Q && S.tick) { $("bid").textContent = fmt(S.tick.bid); $("ask").textContent = fmt(S.tick.ask); }

    if (a.note && a.note !== shownNote) {          // what LiteFinance itself said after the last order
      shownNote = a.note;
      const sub = document.querySelector("#status .sub");
      if (sub) sub.textContent += (sub.textContent ? " · " : "") + "LiteFinance: " + a.note;
    }
    const issues = [];
    if (S.error) issues.push(S.error);
    if (S.source === "mt5" && !a.trade_allowed) issues.push("MT5 is blocking orders: turn on the Algo Trading button in MT5.");
    $("banner").hidden = !issues.length;
    $("banner").textContent = issues.join("  ");

    const canSee = !(S.caps && S.caps.positions === false);
    const ps = S.positions || [];
    $("closeAll").disabled = !canSee;
    $("closeAll").textContent = canSee ? (ps.length ? `CLOSE ALL (${ps.length})` : "CLOSE ALL") : "CLOSE ALL (use the LiteFinance window for now)";
    const rows = S.broker_rows || [];
    $("pos").innerHTML = !canSee
      ? (rows.length ? `<div class="label">Open trades in LiteFinance (${rows.length})</div>` + rows.map((t) => `<div class="prow raw">${esc(t)}</div>`).join("")
                     : `<div class="dim" style="font-size:13px">Open trades show in the LiteFinance window.</div>`)
      : ps.length ? ps.map((p) => `<div class="prow">
        <span class="tag ${p.side === "BUY" ? "b" : "s"}">${p.side} ${p.volume}</span>
        <span class="num dim">${fmt(p.open)}</span>
        <span class="num ${p.profit >= 0 ? "good" : "bad"}">${money(p.profit)}</span>
        <button class="x" data-t="${p.ticket}">Close</button></div>`).join("")
      : `<div class="dim" style="font-size:13px">No open trades.</div>`;

    const s = S.active;
    $("signal").innerHTML = s
      ? `<b class="${s.dir === 1 ? "good" : "bad"}">${s.side} signal</b> <span class="num">@ ${fmt(s.entry)} · SL ${fmt(s.sl)} · TP ${fmt(s.tp2)}</span><button class="use">Use levels</button>
         <div class="dim" style="font-size:12px;margin-top:4px">Signals lost money in the backtest. Trade them at your own judgement.</div>`
      : `<span class="dim">No signal right now.</span>`;

    drawMarkers();
    drawLines();
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
