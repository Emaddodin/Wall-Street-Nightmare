/* Gold Desk front end: polls the local server, draws the chart, sends orders only when you click. */
(() => {
  "use strict";
  const TOKEN = document.querySelector('meta[name="dash-token"]').content;
  const $ = (id) => document.getElementById(id);
  const store = {
    get(k, d) { try { const v = localStorage.getItem("gd_" + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("gd_" + k, JSON.stringify(v)); } catch { /* storage blocked */ } },
  };
  const css = getComputedStyle(document.documentElement);
  const C = (n) => css.getPropertyValue(n).trim();
  const TFSEC = { M1: 60, M5: 300, M15: 900, H1: 3600 };

  let S = null;          // latest /api/state
  let digits = 2;
  let tf = "M1";
  let lastEventId = null;
  let alertsOn = false;
  let audio = null;

  const num = (v) => { const x = parseFloat(v); return Number.isFinite(x) ? x : null; };
  const fmt = (x, d = digits) => (x == null || !Number.isFinite(+x)) ? "-" : (+x).toFixed(d);
  const money = (x) => x == null ? "-" : (x < 0 ? "-$" : "$") + Math.abs(x).toFixed(2);
  const hhmm = (t) => t ? new Date(t * 1000).toISOString().slice(11, 16) : "";   // broker server clock, like MT5
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  async function api(path, opts) {
    const r = await fetch(path, opts);
    return r.json();
  }
  async function post(path, body) {
    return api(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Dash-Token": TOKEN }, body: JSON.stringify(body || {}) });
  }

  // ------------------------------------------------------------------ chart
  const chart = LightweightCharts.createChart($("chart"), {
    autoSize: true,
    layout: { background: { type: "solid", color: C("--bg") }, textColor: C("--dim"), fontFamily: C("--mono"), fontSize: 11 },
    grid: { vertLines: { color: "#181e26" }, horzLines: { color: "#181e26" } },
    rightPriceScale: { borderColor: C("--line") },
    timeScale: { borderColor: C("--line"), timeVisible: true, secondsVisible: false, rightOffset: 10 },
    crosshair: { mode: 0 },
  });
  const candles = chart.addCandlestickSeries({
    upColor: C("--good"), downColor: C("--bad"), borderVisible: false, wickUpColor: C("--good"), wickDownColor: C("--bad"),
    priceFormat: { type: "price", precision: 2, minMove: 0.01 },
  });
  let lastBarTime = 0, firstBarTime = 0, loadedTf = null;

  const floorTf = (t) => t - (t % TFSEC[tf]);

  async function loadCandles(full) {
    const want = tf;
    const rows = await api(`/api/candles?tf=${want}&count=${full ? 1500 : 3}`).catch(() => null);
    if (!Array.isArray(rows) || want !== tf) return;
    if (full || loadedTf !== want) {
      candles.setData(rows);
      loadedTf = want;
      firstBarTime = rows.length ? rows[0].time : 0;
      lastBarTime = rows.length ? rows[rows.length - 1].time : 0;
      markerKey = "";
      drawMarkers();
    } else {
      for (const r of rows) if (r.time >= lastBarTime) { candles.update(r); lastBarTime = r.time; }
    }
    drawZones();
  }

  let markerKey = "";
  function drawMarkers() {
    if (!S || loadedTf !== tf) return;
    const list = S.trades.slice();
    if (S.active) list.push(S.active);
    const m = [];
    for (const t of list) {
      const d = t.dir;
      m.push({ time: floorTf(t.t_bar), position: d === 1 ? "belowBar" : "aboveBar", color: d === 1 ? C("--buy") : C("--sell"),
               shape: d === 1 ? "arrowUp" : "arrowDown", text: d === 1 ? "BUY" : "SELL" });
      if (!t.open && t.t_out) {
        const r = t.r || 0;
        m.push({ time: floorTf(t.t_out), position: d === 1 ? "aboveBar" : "belowBar", color: r > 0 ? C("--good") : C("--bad"),
                 shape: "circle", text: (r > 0 ? "+" : "") + r.toFixed(1) + "R" });
      }
    }
    const vis = m.filter((x) => x.time >= firstBarTime && x.time <= lastBarTime).sort((a, b) => a.time - b.time);
    const key = tf + JSON.stringify(vis.map((x) => [x.time, x.text]));
    if (key !== markerKey) { candles.setMarkers(vis); markerKey = key; }
  }

  let lines = [], linesKey = "";
  function drawLines() {
    const want = [];
    const a = S.active;
    if (a) {
      want.push([a.entry, a.dir === 1 ? C("--buy") : C("--sell"), "Signal " + a.side, 2, 0]);
      want.push([a.sl, C("--bad"), "Signal SL", 2, 0]);
      want.push([a.tp1, C("--good"), "TP1", 1, 2]);
      want.push([a.tp2, C("--good"), "TP2", 2, 0]);
    }
    for (const p of S.positions || []) {
      want.push([p.open, C("--brass"), `#${p.ticket} ${p.side} ${p.volume}`, 1, 1]);
      if (p.sl) want.push([p.sl, C("--bad"), `#${p.ticket} SL`, 1, 1]);
      if (p.tp) want.push([p.tp, C("--good"), `#${p.ticket} TP`, 1, 1]);
    }
    const key = JSON.stringify(want);
    if (key === linesKey) return;
    linesKey = key;
    lines.forEach((l) => candles.removePriceLine(l));
    lines = want.map(([price, color, title, lineWidth, lineStyle]) =>
      candles.createPriceLine({ price, color, title, lineWidth, lineStyle, axisLabelVisible: true }));
  }

  const zc = $("zones");
  function drawZones() {
    const ts = chart.timeScale();
    const w = ts.width(), h = Math.max(0, zc.parentElement.clientHeight - ts.height());
    const dpr = window.devicePixelRatio || 1;
    zc.width = Math.round(w * dpr); zc.height = Math.round(h * dpr);
    zc.style.width = w + "px"; zc.style.height = h + "px";
    const ctx = zc.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!S || !S.zones) return;
    const vr = ts.getVisibleRange();
    ctx.font = "10px " + C("--mono");
    for (const z of S.zones) {
      const y1 = candles.priceToCoordinate(z.top), y2 = candles.priceToCoordinate(z.bottom);
      if (y1 == null || y2 == null) continue;
      const bt = floorTf(z.born);
      let x1 = ts.timeToCoordinate(bt);
      if (x1 == null) x1 = (vr && bt < vr.from) ? 0 : null;
      if (x1 == null || x1 > w) continue;
      const rgb = z.dir === 1 ? "61,139,253" : "240,96,60";
      const top = Math.min(y1, y2), hh = Math.max(1, Math.abs(y2 - y1));
      ctx.fillStyle = `rgba(${rgb},0.10)`;
      ctx.fillRect(x1, top, w - x1, hh);
      ctx.strokeStyle = `rgba(${rgb},0.6)`;
      ctx.setLineDash(z.kind === "FVG" ? [3, 3] : [7, 3]);
      ctx.strokeRect(x1 + 0.5, top + 0.5, w - x1 - 1, hh - 1);
      ctx.fillStyle = `rgba(${rgb},0.95)`;
      ctx.fillText(`M5 ${z.kind}`, x1 + 4, top + 11);
    }
  }
  ts_sub();
  function ts_sub() {
    chart.timeScale().subscribeVisibleLogicalRangeChange(() => drawZones());
    new ResizeObserver(() => drawZones()).observe($("chart"));
  }
  chart.subscribeCrosshairMove((p) => {
    const d = p && p.seriesData ? p.seriesData.get(candles) : null;
    $("legend").textContent = d ? `${tf}  O ${fmt(d.open)}  H ${fmt(d.high)}  L ${fmt(d.low)}  C ${fmt(d.close)}` : "";
  });

  document.querySelectorAll("#tfTabs button").forEach((b) => b.addEventListener("click", () => {
    tf = b.dataset.tf;
    document.querySelectorAll("#tfTabs button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
    loadCandles(true);
  }));

  // ------------------------------------------------------------------ order ticket
  const ticketIds = ["riskPct", "lotMode", "fixedLots", "target", "slMode", "confirmToggle"];
  for (const id of ticketIds) {
    const el = $(id);
    const saved = store.get(id, null);
    if (saved !== null) { if (el.type === "checkbox") el.checked = saved; else el.value = saved; }
    el.addEventListener("change", () => { store.set(id, el.type === "checkbox" ? el.checked : el.value); renderTicket(); });
  }
  ["manualSl", "manualTp", "riskPct", "fixedLots"].forEach((id) => $(id).addEventListener("input", () => renderTicket()));
  function stepLots(dir) {
    const st = (S && S.spec.lot_step) || 0.01, mn = (S && S.spec.min_lot) || 0.01;
    const cur = num($("fixedLots").value) || mn;
    $("fixedLots").value = Math.max(mn, +(cur + dir * st).toFixed(2)).toFixed(2);
    $("lotMode").value = "fixed";
    store.set("lotMode", "fixed"); store.set("fixedLots", $("fixedLots").value);
    renderTicket();
  }
  $("volUp").addEventListener("click", () => stepLots(1));
  $("volDown").addEventListener("click", () => stepLots(-1));
  $("fixedLots").addEventListener("input", () => { if ($("lotMode").value !== "fixed") { $("lotMode").value = "fixed"; store.set("lotMode", "fixed"); } });

  function floorLots(x) {
    const st = S.spec.lot_step || 0.01;
    return Math.floor(x / st + 1e-9) * st;
  }

  function plan(side) {
    if (!S || !S.tick) return { ok: false, why: "No price yet" };
    const d = side === "BUY" ? 1 : -1, tk = S.tick, p = S.params;
    const price = d === 1 ? tk.ask : tk.bid;
    const sig = S.active && S.active.dir === d ? S.active : null;
    const slMode = $("slMode").value, target = $("target").value;
    let sl = null, tp = null, src;
    if (slMode === "manual") {
      sl = num($("manualSl").value); tp = num($("manualTp").value); src = "Manual levels";
    } else {
      if (slMode === "auto") {
        if (sig) { sl = sig.sl; src = "Signal levels"; }
        else if (S.atr_m1) { sl = price - d * p.atr_sl_mult * S.atr_m1; src = "No signal: 1.5 ATR stop"; }
      } else src = "No stop loss";
      const R = sl != null ? Math.abs(price - sl) : (S.atr_m1 ? p.atr_sl_mult * S.atr_m1 : null);
      if (target !== "none" && R) {
        if (sig) tp = target === "tp1" ? sig.tp1 : sig.tp2;
        else tp = price + d * (target === "tp1" ? p.tp1_r : p.tp2_r) * R;
      }
    }
    if (sl != null && (d === 1 ? sl >= tk.bid : sl <= tk.ask)) return { ok: false, why: "Stop is already beyond price" };
    if (tp != null && (d === 1 ? tp <= tk.ask : tp >= tk.bid)) return { ok: false, why: "Target is already reached" };
    let lots;
    const bal = S.account.balance || 0;
    if ($("lotMode").value === "fixed") lots = num($("fixedLots").value) || 0;
    else {
      if (sl == null) return { ok: false, why: "Risk sizing needs a stop: set one or use fixed lots" };
      const perLot = Math.abs(price - sl) * S.spec.vpu + S.params.commission;
      lots = floorLots((bal * (num($("riskPct").value) || 0) / 100) / perLot);
    }
    lots = +floorLots(lots).toFixed(2);
    let note = "";
    if (lots < S.spec.min_lot) { lots = S.spec.min_lot; note = "min lot is above your risk %"; }
    if (lots > S.max_lots) return { ok: false, why: `${lots} lots is above the ${S.max_lots} lot cap` };
    const risk = sl != null ? lots * (Math.abs(price - sl) * S.spec.vpu + S.params.commission) : null;
    return { ok: true, side, d, price, sl: sl != null ? +sl.toFixed(digits) : null, tp: tp != null ? +tp.toFixed(digits) : null, lots, risk, src, note };
  }

  function planHtml(pl) {
    if (!pl.ok) return `<span class="warn">${esc(pl.why)}</span>`;
    return `SL ${fmt(pl.sl)}<br>TP ${fmt(pl.tp)}<br>${pl.lots.toFixed(2)} lots · risk ${pl.risk != null ? money(pl.risk) : "open"}<br>` +
      `<span class="src">${esc(pl.src)}${pl.note ? " · " + esc(pl.note) : ""}</span>`;
  }

  function renderTicket() {
    if (!S) return;
    const tk = S.tick;
    $("sellPx").textContent = tk ? fmt(tk.bid) : "-";
    $("buyPx").textContent = tk ? fmt(tk.ask) : "-";
    const ps = plan("SELL"), pb = plan("BUY");
    $("planSell").innerHTML = planHtml(ps);
    $("planBuy").innerHTML = planHtml(pb);
    const canTrade = !!(S.account && S.account.trade_allowed);
    const shown = $("lotMode").value === "auto" ? (pb.ok ? pb : ps) : null;
    $("volNote").textContent = shown && shown.ok ? `auto: ${shown.lots.toFixed(2)}` : "lots";
    $("btnSell").disabled = !ps.ok || !canTrade;
    $("btnBuy").disabled = !pb.ok || !canTrade;
  }

  let pending = null;
  function confirmThen(title, html, fn) {
    if (!$("confirmToggle").checked) return fn();
    pending = fn;
    $("mTitle").textContent = title;
    $("mBody").innerHTML = html;
    $("modal").hidden = false;
    $("mOk").focus();
  }
  $("mCancel").addEventListener("click", () => { $("modal").hidden = true; pending = null; });
  $("mOk").addEventListener("click", () => { $("modal").hidden = true; const f = pending; pending = null; if (f) f(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("modal").hidden) $("mCancel").click(); });

  async function send(path, body, label) {
    const t0 = performance.now();
    let res;
    try { res = await post(path, body); } catch (e) { res = { ok: false, message: "Server not reachable" }; }
    const rt = Math.round(performance.now() - t0);
    const msg = `${label}: ${res.ok ? "done" : "refused"}${res.price ? " @ " + fmt(res.price) : ""}. ${res.message || ""}`;
    toast(msg, res.ok ? "order" : "reject");
    $("latency").textContent = res.ms != null
      ? (S.source === "litefinance" ? `Last request: LiteFinance's button pressed after ${res.ms} ms, ${rt} ms round trip from this page.` : `Last request: ${res.ms} ms for MT5 to answer, ${rt} ms round trip from this page.`)
      : `Last request: ${rt} ms round trip from this page.`;
    pollState(true);
  }

  function order(side) {
    const pl = plan(side);
    if (!pl.ok) return toast(pl.why, "reject");
    const acct = S.account.mode === "real" ? '<p class="bad"><b>REAL MONEY account</b></p>' : "";
    confirmThen(`${side} ${pl.lots.toFixed(2)} lots`, `${acct}<div class="lv num">
      <span>Price</span><span>${fmt(pl.price)}</span><span>market</span>
      <span>Stop</span><span>${fmt(pl.sl)}</span><span>${pl.sl != null ? fmt(Math.abs(pl.price - pl.sl)) : ""}</span>
      <span>Target</span><span>${fmt(pl.tp)}</span><span>${pl.tp != null ? fmt(Math.abs(pl.tp - pl.price)) : ""}</span>
      <span>Risk</span><span>${pl.risk != null ? money(pl.risk) : "no stop"}</span><span></span></div>
      <p class="dim">${esc(pl.src)}</p>`,
      () => send("/api/order", { side, lots: pl.lots, sl: pl.sl, tp: pl.tp }, `${side} ${pl.lots.toFixed(2)}`));
  }
  $("btnBuy").addEventListener("click", () => order("BUY"));
  $("btnSell").addEventListener("click", () => order("SELL"));
  $("closeAll").addEventListener("click", () => {
    if (S && S.caps && S.caps.positions === false) return toast("Close all from the LiteFinance window for now. Gold Desk can't see LiteFinance positions yet.", "reject");
    if (!S || !(S.positions || []).length) return toast("No open gold positions.", "reject");
    confirmThen("Close all gold positions", `<p>${S.positions.length} position(s) will be closed at market.</p>`,
      () => send("/api/close_all", {}, "Close all"));
  });

  $("posBody").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-act]");
    if (!b) return;
    const p = (S.positions || []).find((x) => String(x.ticket) === b.dataset.t);
    if (!p) return;
    if (b.dataset.act === "close")
      confirmThen(`Close #${p.ticket}`, `<p>${p.side} ${p.volume} lots, P/L ${money(p.profit)}</p>`, () => send("/api/close", { ticket: p.ticket }, `Close #${p.ticket}`));
    if (b.dataset.act === "half") {
      const v = +floorLots(p.volume / 2).toFixed(2);
      if (v < S.spec.min_lot || p.volume - v < S.spec.min_lot - 1e-9) return toast(`${p.volume} lots cannot be split at your broker's ${S.spec.min_lot} minimum.`, "reject");
      confirmThen(`Close half of #${p.ticket}`, `<p>Close ${v} of ${p.volume} lots.</p>`, () => send("/api/close", { ticket: p.ticket, volume: v }, `Close ${v} of #${p.ticket}`));
    }
    if (b.dataset.act === "be")
      confirmThen(`Stop to breakeven on #${p.ticket}`, `<p>Move the stop to ${fmt(p.open)}.</p>`, () => send("/api/modify", { ticket: p.ticket, sl: p.open, tp: p.tp }, `Breakeven #${p.ticket}`));
  });

  // ------------------------------------------------------------------ panels
  function renderHeader() {
    const a = S.account, tk = S.tick;
    $("sym").textContent = S.symbol;
    const badge = $("acctBadge");
    const mode = a.mode || "unknown";
    badge.className = "badge " + (mode === "real" ? "real" : mode === "demo" || mode === "contest" ? "demo" : "paper");
    badge.textContent = mode === "real" ? "Real money" : mode === "demo" ? "Demo account" : mode === "contest" ? "Contest account"
      : S.source === "demo" ? "Demo data, paper fills" : "Demo or real? Check";
    if (mode === "unknown" && S.source !== "demo") badge.className = "badge real";
    $("bid").textContent = tk ? fmt(tk.bid) : "-";
    $("ask").textContent = tk ? fmt(tk.ask) : "-";
    $("spr").textContent = tk ? fmt(tk.ask - tk.bid) : "-";
    $("bal").textContent = `${money(a.balance)} / ${money(a.equity)}`;
    $("srv").textContent = a.server + (a.ping_ms ? ` · ${a.ping_ms} ms` : "");
    $("utc").textContent = S.clock.utc;
    const sp = $("sess");
    sp.textContent = S.clock.session + (S.clock.session_ok ? "" : " · no signals");
    sp.className = "pill " + (S.clock.session_ok ? "on" : "off");
    const issues = [];
    if (S.error) issues.push((S.source === "litefinance" ? "LiteFinance: " : "MT5: ") + S.error);
    if (S.source === "mt5" && !a.trade_allowed) issues.push("Orders are blocked: turn on the Algo Trading button in MT5 (and allow algo trading in Tools > Options > Expert Advisors).");
    if (S.source === "litefinance" && $("latency").textContent.startsWith("Orders go to your MT5"))
      $("latency").textContent = "Orders fill the LiteFinance ticket in the window Gold Desk opened, and press its button.";
    if (S.source === "demo") issues.push("Demo data: prices are synthetic and orders are paper fills. Start without --demo to use your MT5 terminal.");
    $("banner").hidden = !issues.length;
    $("banner").textContent = issues.join("  ");
  }

  function renderSignal() {
    const a = S.active, el = $("signal");
    if (a) {
      const d = a.dir, cls = d === 1 ? "buy" : "sell";
      el.className = "signal " + (d === 1 ? "long" : "short");
      $("sigTime").textContent = "server " + hhmm(a.t_in);
      el.innerHTML = `<div class="head"><span class="${cls}">${a.side}</span><span class="num">@ ${fmt(a.entry)}</span><span class="dim" style="font-size:12px">${esc(a.why)}</span></div>
        <div class="lv num">
          <span>Stop</span><span class="bad">${fmt(a.sl)}</span><span>${fmt(Math.abs(a.entry - a.sl))}</span>
          <span>TP1 ${S.params.tp1_r}R</span><span class="good">${fmt(a.tp1)}</span><span>${a.partial ? "hit" : "close " + S.params.partial_pct + "%"}</span>
          <span>TP2 ${S.params.tp2_r}R</span><span class="good">${fmt(a.tp2)}</span><span>runner</span>
        </div>
        <div class="dim" style="margin-top:6px">${a.partial ? "TP1 reached: stop is at breakeven and trailing." : "Open. Levels match your broker's chart."}</div>`;
    } else {
      el.className = "signal";
      const l = S.last_trade;
      $("sigTime").textContent = "";
      el.innerHTML = `<div class="dim">No active signal.</div>` + (l && !l.open ?
        `<div style="margin-top:6px">Last: <span class="${l.dir === 1 ? "buy" : "sell"}">${l.side}</span> at <span class="num">${fmt(l.entry)}</span>, closed <span class="num ${l.r > 0 ? "good" : "bad"}">${(l.r > 0 ? "+" : "") + (l.r || 0).toFixed(1)}R</span> <span class="dim">server ${hhmm(l.t_out)}</span></div>` : "");
    }
  }

  function chip(v, txtL, txtS, txtN) {
    return v > 0 ? `<span class="chip long">${txtL}</span>` : v < 0 ? `<span class="chip short">${txtS}</span>` : `<span class="chip flat">${txtN}</span>`;
  }

  function renderConf() {
    const c = S.context;
    if (!c) { $("conf").innerHTML = `<span></span><span class="dim">Loading higher-timeframe history</span><span></span>`; return; }
    const ev = { 1: "BOS up", 2: "CHoCH up", "-1": "BOS down", "-2": "CHoCH down", 0: "no break yet" }[c.event];
    const nb = S.zones.filter((z) => z.dir === 1).length, ns = S.zones.length - nb;
    const mom = c.m5_long ? 1 : c.m5_short ? -1 : 0;
    const radar = S.radar ? `<span class="chip alert">RADAR ${S.radar}</span>` : `<span class="chip flat">waiting</span>`;
    $("conf").innerHTML = `
      <span class="tf">H1</span><span>EMA 50 ${c.ema_f > c.ema_s ? "above" : "below"} 200</span>${chip(c.regime, "BULL", "BEAR", "NEUTRAL")}
      <span class="tf">H1</span><span>Velocity shock</span>${c.shock ? `<span class="chip alert">${c.shock > 0 ? "UP: no shorts" : "DOWN: no longs"}</span>` : `<span class="chip flat">calm</span>`}
      <span class="tf">M15</span><span>Structure, last ${ev}</span>${chip(c.struct, "BULL", "BEAR", "NONE")}
      <span class="tf">M15</span><span>EMA 21 ${c.ema_up15 ? ">" : "<"} 55</span>${chip(c.ema_up15 ? 1 : -1, "UP", "DOWN", "")}
      <span class="tf">M5</span><span class="num">RSI ${c.rsi.toFixed(1)} <span class="dim">band ${c.rsi_lo.toFixed(0)}-${c.rsi_up.toFixed(0)}</span></span>${chip(mom, "RISING", "FALLING", "STRETCHED")}
      <span class="tf">M5</span><span>Zones</span><span class="chip flat">${nb} demand / ${ns} supply</span>
      <span class="tf">UTC</span><span>${esc(S.clock.session)}${S.asian.range ? ` <span class="dim">Asian range ${fmt(S.asian.range)}${S.asian.contracted ? ", tight" : ""}</span>` : ""}</span>${S.clock.session_ok ? `<span class="chip long">OPEN</span>` : `<span class="chip flat">CLOSED</span>`}
      <span class="tf">M1</span><span>Trigger</span>${radar}`;
    const okL = c.regime === 1 && c.shock !== -1 && c.bias15 === 1, okS = c.regime === -1 && c.shock !== 1 && c.bias15 === -1;
    $("verdict").innerHTML = `<span>Bias</span><span class="${okL ? "buy" : okS ? "sell" : "dim"}">${okL ? "LONGS ONLY" : okS ? "SHORTS ONLY" : "STAND ASIDE"}</span>`;
  }

  function renderPositions() {
    const ps = S.positions || [];
    $("posBody").innerHTML = ps.length ? ps.map((p) => `<tr>
      <td class="num">${p.ticket}</td><td class="${p.side === "BUY" ? "buy" : "sell"}">${p.side}</td><td class="num">${p.volume}</td>
      <td class="num">${fmt(p.open)}</td><td class="num">${p.sl ? fmt(p.sl) : "-"}</td><td class="num">${p.tp ? fmt(p.tp) : "-"}</td>
      <td class="num">${fmt(p.price)}</td><td class="num ${p.profit >= 0 ? "good" : "bad"}">${money(p.profit)}</td>
      <td class="acts"><button class="ghost" data-act="be" data-t="${p.ticket}" title="Move stop to entry">BE</button><button class="ghost" data-act="half" data-t="${p.ticket}" title="Close half">½</button><button class="ghost danger" data-act="close" data-t="${p.ticket}">Close</button></td></tr>`).join("")
      : `<tr><td colspan="9" class="empty">${S.caps && S.caps.positions === false
        ? "Your LiteFinance positions show in the LiteFinance window. Closing them from here comes next."
        : "No open gold positions."}</td></tr>`;
  }

  function renderFeed() {
    const evs = S.events || [];
    const maxId = evs.length ? evs[evs.length - 1].id : 0;
    if (lastEventId === null) lastEventId = maxId;          // don't replay old alerts on page load
    for (const e of evs) if (e.id > lastEventId && (e.kind === "radar" || e.kind === "execute")) notify(e);
    lastEventId = Math.max(lastEventId, maxId);
    $("feed").innerHTML = evs.length ? evs.slice().reverse().map((e) =>
      `<div class="ev ${e.kind}"><time>${new Date(e.at * 1000).toLocaleTimeString()}</time>${esc(e.text)}</div>`).join("")
      : `<div class="dim">Radar and execute alerts appear here.</div>`;
  }

  function renderMini() {
    const s = S.stats;
    $("miniStats").innerHTML = s.trades ? `
      <div class="row"><span>Trades / win rate</span><span class="num">${s.trades} / ${s.win_rate.toFixed(1)}%</span></div>
      <div class="row"><span>Profit factor</span><span class="num">${s.profit_factor == null ? "no losses" : s.profit_factor.toFixed(2)}</span></div>
      <div class="row"><span>Max drawdown</span><span class="num">${s.max_dd_pct.toFixed(2)}%</span></div>
      <div class="row"><span>Net</span><span class="num ${s.net >= 0 ? "good" : "bad"}">${money(s.net)} · ${(s.net_r >= 0 ? "+" : "") + s.net_r.toFixed(1)}R</span></div>`
      : `<div class="dim">No signals in the last ${s.bars} bars yet. The Backtest tab runs longer history.</div>`;
  }

  // ------------------------------------------------------------------ alerts
  function toast(text, kind) {
    const el = document.createElement("div");
    el.className = "toast";
    if (kind === "reject") el.style.borderLeftColor = C("--bad");
    if (kind === "order") el.style.borderLeftColor = C("--good");
    if (kind === "radar") el.style.borderLeftColor = C("--warn");
    el.textContent = text;
    $("toasts").appendChild(el);
    setTimeout(() => el.remove(), kind === "execute" || kind === "radar" ? 12000 : 6000);
  }
  function beep(freqs) {
    if (!audio) return;
    let t = audio.currentTime;
    for (const f of freqs) {
      const o = audio.createOscillator(), g = audio.createGain();
      o.frequency.value = f; o.connect(g); g.connect(audio.destination);
      g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.25, t + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.18);
      o.start(t); o.stop(t + 0.2); t += 0.22;
    }
  }
  function notify(e) {
    toast(e.text, e.kind);
    if (!alertsOn) return;
    beep(e.kind === "execute" ? [660, 990, 1320] : [880]);
    try { if (window.Notification && Notification.permission === "granted") new Notification(e.kind === "execute" ? "Gold: EXECUTE" : "Gold: RADAR", { body: e.text }); } catch { /* ignore */ }
  }
  $("alertsBtn").addEventListener("click", async () => {
    try { audio = audio || new (window.AudioContext || window.webkitAudioContext)(); await audio.resume(); } catch { audio = null; }
    try { if (window.Notification && Notification.permission === "default") await Notification.requestPermission(); } catch { /* ignore */ }
    alertsOn = true;
    $("alertsBtn").textContent = "Alerts on";
    beep([880]);
  });

  // ------------------------------------------------------------------ polling
  let pollTimer = null;
  async function pollState(once) {
    try {
      const s = await api("/api/state");
      if (!s.symbol) throw new Error(s.error || "no data");
      S = s;
      digits = s.spec.digits;
      renderHeader(); renderTicket(); renderSignal(); renderConf(); renderPositions(); renderFeed(); renderMini();
      drawLines(); drawMarkers(); drawZones();
    } catch (e) {
      $("banner").hidden = false;
      $("banner").textContent = "Lost contact with the dashboard server. Is its window still open?";
    }
    if (!once) { clearTimeout(pollTimer); pollTimer = setTimeout(() => pollState(false), 700); }
  }
  setInterval(() => { if (!$("live").hidden) loadCandles(false); }, 1000);

  // ------------------------------------------------------------------ backtest tab
  let eqChart = null, eqSeries = null;
  function showTab(live) {
    $("live").hidden = !live; $("bt").hidden = live;
    $("tabLive").setAttribute("aria-selected", String(live)); $("tabBt").setAttribute("aria-selected", String(!live));
    if (!live && !eqChart) {
      eqChart = LightweightCharts.createChart($("eqchart"), {
        autoSize: true, layout: { background: { type: "solid", color: C("--panel") }, textColor: C("--dim"), fontFamily: C("--mono"), fontSize: 11 },
        grid: { vertLines: { visible: false }, horzLines: { color: "#1f262f" } },
        rightPriceScale: { borderColor: C("--line") }, timeScale: { borderColor: C("--line"), timeVisible: true },
      });
      eqSeries = eqChart.addAreaSeries({ lineColor: C("--brass"), topColor: "rgba(200,162,74,0.25)", bottomColor: "rgba(200,162,74,0.02)", lineWidth: 2 });
    }
    if (!live && S && !$("btBal").value) $("btBal").placeholder = String(Math.round(S.account.balance || 1000));
  }
  $("tabLive").addEventListener("click", () => showTab(true));
  $("tabBt").addEventListener("click", () => showTab(false));

  $("btRun").addEventListener("click", async () => {
    const q = new URLSearchParams({ days: $("btDays").value, risk_pct: $("btRisk").value, mode: $("btMode").value, session_filter: $("btSess").value });
    if ($("btBal").value) q.set("balance", $("btBal").value);
    $("btRun").disabled = true;
    $("btStatus").textContent = S.source === "litefinance" ? "Replaying LiteFinance history..." : "Replaying your MT5 history...";
    try {
      const r = await api("/api/backtest?" + q);
      if (r.error) throw new Error(r.error);
      renderBacktest(r);
      $("btStatus").textContent = `${r.stats.bars.toLocaleString()} M1 bars from ${hhmm(r.stats.from) && new Date(r.stats.from * 1000).toISOString().slice(0, 16).replace("T", " ")} to ${new Date(r.stats.to * 1000).toISOString().slice(0, 16).replace("T", " ")} (server time) in ${r.seconds}s` +
        (r.source === "mt5" && r.bars_loaded < r.days_requested * 1440 * 0.6 ? ". Your terminal returned less history than asked: raise Max bars in chart in MT5 options." : "");
    } catch (e) {
      $("btStatus").textContent = "Backtest failed: " + e.message;
    }
    $("btRun").disabled = false;
  });

  function renderBacktest(r) {
    const s = r.stats;
    const tiles = [
      ["Trades", s.trades], ["Win rate", s.win_rate.toFixed(1) + "%"],
      ["Profit factor", s.profit_factor == null ? (s.trades ? "no losses" : "-") : s.profit_factor.toFixed(2)],
      ["Max drawdown", s.max_dd_pct.toFixed(2) + "%"],
      ["Avg win / loss", s.avg_loss ? (s.avg_win / s.avg_loss).toFixed(2) : "-"],
      ["Net P/L", money(s.net)], ["Net R", (s.net_r >= 0 ? "+" : "") + s.net_r.toFixed(1) + "R"],
    ];
    $("btTiles").innerHTML = tiles.map(([k, v]) => `<div class="tile"><small>${k}</small><div>${v}</div></div>`).join("");
    const pts = [];
    for (const [t, v] of r.equity) {
      if (pts.length && t <= pts[pts.length - 1].time) pts[pts.length - 1].value = v;
      else pts.push({ time: t, value: v });
    }
    eqSeries.setData(pts);
    eqChart.timeScale().fitContent();
    const dt = (t) => new Date(t * 1000).toISOString().slice(5, 16).replace("T", " ");
    $("btBody").innerHTML = r.trades.length ? r.trades.slice().reverse().map((t) => `<tr>
      <td class="num">${dt(t.t_in)}</td><td class="${t.dir === 1 ? "buy" : "sell"}">${t.side}</td><td>${esc(t.why)}</td>
      <td class="num">${fmt(t.entry)}</td><td class="num">${fmt(t.sl0)}</td><td class="num">${fmt(t.tp1)}</td><td class="num">${fmt(t.tp2)}</td>
      <td class="num">${fmt(t.exit)}</td><td class="num">${t.lots.toFixed(2)}</td>
      <td class="num ${t.r > 0 ? "good" : "bad"}">${(t.r > 0 ? "+" : "") + t.r.toFixed(2)}</td><td class="num ${t.pnl > 0 ? "good" : "bad"}">${money(t.pnl)}</td></tr>`).join("")
      : `<tr><td colspan="11" class="empty">No trades in this window. Try more days or Balanced mode.</td></tr>`;
  }

  // ------------------------------------------------------------------ start
  pollState(false).then(() => loadCandles(true));
})();
