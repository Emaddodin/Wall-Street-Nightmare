/* Gold Desk: an analysis desk on the MT5 chart. Every closed candle is read with every tool the desk has (the ICT
   playbook and its models, candlestick patterns, Kronos, the quant model, SMT, the news, the New York clock) and the
   page says what to do now. It never sends an order: execution is in MT5 on the phone. The only connection is the
   chart feed (candles and prices on every timeframe) and the server's read of it (/api/state). */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const TFSEC = { M1: 60, M5: 300, M15: 900, H1: 3600, H4: 14400, D1: 86400 };
  const LINE_TF = { M1: true, M5: true, M15: true };      // the one line is drawn on these; 1h shows Kronos 24 h
  const READ_TF = { M1: true, M5: true };                 // timeframes the brain reads candle by candle
  const TF6 = ["D1", "H4", "H1", "M15", "M5", "M1"];
  const C = { up: "#2fb67c", down: "#e5484d", gold: "#d6ad52", dim: "#8e8a80", line: "#262a2f", bg: "#0e0f11", warn: "#e8b23a", buy: "#2f7bf5", sell: "#e5533c" };
  const store = {
    get(k, d) { try { const v = localStorage.getItem("gd3_" + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("gd3_" + k, JSON.stringify(v)); } catch { /* storage blocked */ } },
  };

  let S = null;            // latest /api/state
  let Q = null;            // latest streamed quote
  let lastQuoteAt = 0;
  let tf = TFSEC[store.get("tf5", "M1")] ? store.get("tf5", "M1") : "M1";
  const ovl = Object.assign({ ict: true, smc: true, pos: true, smt: true }, store.get("ovl5", {}));
  const openK = {};        // folded parts the viewer opened or closed, kept across re-renders

  const fmt = (x) => (x == null || !Number.isFinite(+x)) ? "-" : (+x).toFixed(2);
  const money = (x) => (x == null || !Number.isFinite(+x)) ? "-" : (x > 0 ? "+$" : x < 0 ? "−$" : "$") + Math.abs(+x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const sgn = (x) => (x == null || !Number.isFinite(+x)) ? "-" : (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(+x).toFixed(2);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const tone = (x) => (x > 0 ? "up" : x < 0 ? "down" : "");
  const arr = (d) => (d > 0 ? "▲" : d < 0 ? "▼" : "•");
  const pct = (x) => (x == null || !Number.isFinite(+x) ? "-" : Math.round(x * 100) + "%");
  const pctUp = (u) => (u == null ? "" : +u >= 0.5 ? `up ${Math.round(u * 100)}%` : `down ${Math.round((1 - u) * 100)}%`);
  const clock = (sec) => new Date(sec * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  const candleClock = (sec) => new Date(sec * 1000).toISOString().slice(11, 16);       // the chart's own clock (MT5 server time)
  const tfName = (t) => String(t || "").replace(/^M(\d+)$/, "$1m").replace(/^H(\d+)$/, "$1h").replace(/^D1$/, "1D");
  const horizon = (m) => (!m ? "" : m >= 1440 && m % 1440 === 0 ? `${m / 1440 === 1 ? "24 h" : m / 1440 + " d"}` : m >= 120 ? `${Math.round(m / 60)} h` : `${m} min`);
  const gradeHtml = (g, off) => (g ? `<span class="grade ${g === "A+" ? "ap" : g === "A" ? "a" : ""}${off ? " off" : ""}">${esc(g)}</span>` : "");
  const setHtml = (el, html) => { if (el._h !== html) { el._h = html; el.innerHTML = html; } };
  const actCls = (a) => (/^DON'T/.test(a || "") ? "warnc" : /BUY/.test(a || "") ? "up" : /SELL/.test(a || "") ? "down" : "dim");
  const shortName = (n) => String(n || "").replace(/\s*\(.*\)\s*$/, "").trim();
  const chartNow = () => Date.now() / 1000 + ((S && S.clock && S.clock.utc_offset_h) || 0) * 3600;
  const get = (p) => fetch(p, { cache: "no-store" }).then((r) => r.json());

  // ---------------------------------------------------------------- chart
  const chart = LightweightCharts.createChart($("chart"), {
    autoSize: true,
    layout: { background: { type: "solid", color: C.bg }, textColor: C.dim, fontSize: 12, fontFamily: "JetBrains Mono, ui-monospace, Menlo, monospace" },
    grid: { vertLines: { color: "#16181b" }, horzLines: { color: "#16181b" } },
    rightPriceScale: { borderColor: C.line, scaleMargins: { top: 0.12, bottom: 0.08 } },
    timeScale: { borderColor: C.line, timeVisible: true, secondsVisible: false, rightOffset: 14 },
    crosshair: { mode: 0 },
  });
  const series = chart.addCandlestickSeries({
    upColor: C.up, downColor: C.down, borderVisible: false, wickUpColor: C.up, wickDownColor: C.down,
    priceFormat: { type: "price", precision: 2, minMove: 0.01 },
  });
  let bandRange = null;              // the line's band, lowest / highest, so the price scale keeps it in view
  const lineS = chart.addLineSeries({ color: C.gold, lineWidth: 2, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    autoscaleInfoProvider: (orig) => { const r = orig(); if (!bandRange) return r;
      const lo = Math.min(bandRange[0], r ? r.priceRange.minValue : Infinity), hi = Math.max(bandRange[1], r ? r.priceRange.maxValue : -Infinity);
      return { priceRange: { minValue: lo, maxValue: hi } }; } });
  const kdayS = chart.addLineSeries({ color: "rgba(214,173,82,.55)", lineWidth: 1, lineStyle: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  let last = null, loadedTf = null, first = 0, times = [], timeSet = new Set();

  async function loadCandles() {
    const want = tf;
    const rows = await get(`/api/candles?tf=${want}&count=1200`).catch(() => null);
    if (!Array.isArray(rows) || want !== tf) return;
    lineS.setData([]); kdayS.setData([]); bandRange = null; fcKey = "";
    series.setData(rows);
    const room = { M1: 36, M5: 12, M15: 8, H1: 28, H4: 8, D1: 4 }[want] || 14;   // empty bars on the right for the line
    chart.timeScale().applyOptions({ rightOffset: room });
    if (rows.length > 160) chart.timeScale().setVisibleLogicalRange({ from: rows.length - 150, to: rows.length + room });
    else chart.timeScale().fitContent();
    loadedTf = want;
    first = rows.length ? rows[0].time : 0;
    times = rows.map((r) => r.time);
    timeSet = new Set(times);
    last = rows.length ? { ...rows[rows.length - 1] } : null;
    markerKey = "";
    drawMarkers(); drawForecast(); requestAnimationFrame(drawZones);
  }

  // live candle from the streamed bid, the way MT5 builds it
  let zonesAt = 0;
  function liveCandle(bid) {
    if (!last || loadedTf !== tf || !S) return;
    const sec = TFSEC[tf], now = chartNow(), bucket = Math.floor(now - (now % sec));
    if (bucket > last.time && times[times.length - 1] < bucket) { times.push(bucket); timeSet.add(bucket); }
    if (bucket > last.time) last = { time: bucket, open: last.close, high: Math.max(last.close, bid), low: Math.min(last.close, bid), close: bid };
    else { last.high = Math.max(last.high, bid); last.low = Math.min(last.low, bid); last.close = bid; }
    series.update(last);
    if (LINE_TF[tf]) { drawForecast(); if (Date.now() - zonesAt > 1000) { zonesAt = Date.now(); requestAnimationFrame(drawZones); } }
  }

  // the server's copy of the last bars every few seconds (fixes any tick the stream missed)
  async function syncTail() {
    const want = tf;
    const rows = await get(`/api/candles?tf=${want}&count=3`).catch(() => null);
    if (!Array.isArray(rows) || want !== tf || loadedTf !== want) return;
    for (const r of rows) if (!last || r.time >= last.time) {
      series.update(r); last = { ...r };
      if (!times.length || times[times.length - 1] < r.time) { times.push(r.time); timeSet.add(r.time); }
    }
  }

  // ---------------------------------------------------------------- candle by candle: marks on the chart
  // A small letter on each candle whose close did something in ICT terms (state.ict.history[tf]: the brain's read
  // of every closed M1 / M5 candle), and a big ENTER arrow where an entry was called on that close (state.ict.entries).
  const GLYPH = [["sweep", "$"], ["cisd", "C"], ["structure", null], ["ifvg", "I"], ["breaker", "Bk"], ["fvg", "F"], ["key", "K"], ["pattern", "P"], ["disp", "D"]];
  function glyphs(rd) {
    const ev = (rd && rd.events || []).filter((e) => e.dir && e.kind !== "quiet"), out = [];
    for (const [k, g] of GLYPH) for (const e of ev) if (e.kind === k) {
      const x = g || (/^MSS/.test(e.text) ? "M" : /^CHoCH/i.test(e.text) ? "Ch" : "B");
      if (!out.includes(x)) out.push(x);
    }
    return out;
  }
  let markerKey = "", tipMap = new Map();
  function drawMarkers() {
    if (!S || loadedTf !== tf) return;
    const I = S.ict, m = [];
    tipMap = new Map();
    if (I && READ_TF[tf]) {
      const hist = (I.history && I.history[tf]) || [];
      const ents = (I.entries || []).filter((e) => e.tf === tf && e.action_dir && timeSet.has(e.time)).slice(-8);
      const entT = new Set(ents.map((e) => e.time));
      for (const r of hist) tipMap.set(r.time, r);
      {
        const marked = hist.filter((r) => timeSet.has(r.time) && !entT.has(r.time) && Math.abs(+r.lean || 0) >= 1 && glyphs(r).length).slice(-15);
        for (const r of marked) {
          const g = glyphs(r).slice(0, 2).join(""), d = r.lean >= 1 ? 1 : r.lean <= -1 ? -1 : 0;
          m.push({ time: r.time, position: d < 0 ? "aboveBar" : "belowBar", shape: "circle", size: 0.6,
            color: d > 0 ? "rgba(47,182,124,.9)" : d < 0 ? "rgba(229,72,77,.9)" : "rgba(185,167,122,.85)", text: g });
        }
        ents.forEach((e, i) => {                                   // words on the newest two only; hover any arrow for its read
          tipMap.set(e.time, { ...(tipMap.get(e.time) || {}), ...e });
          const up = e.action_dir > 0;
          m.push({ time: e.time, position: up ? "belowBar" : "aboveBar", shape: up ? "arrowUp" : "arrowDown", size: 2,
            color: up ? C.up : C.down, text: i >= ents.length - 2 ? `ENTER ${shortName(e.model) || (up ? "BUY" : "SELL")}` : "" });
        });
      }
    }
    if (ovl.pos) for (const p of posRows()) {                     // your MT5 positions: a square at the candle they opened in
      if (p.time == null) continue;
      const t = p.time - (p.time % TFSEC[tf]), b = p.side === "BUY";
      if (!timeSet.has(t)) continue;
      m.push({ time: t, position: b ? "belowBar" : "aboveBar", shape: "square", size: 0.8, color: b ? C.buy : C.sell, text: `${b ? "B" : "S"} ${(+p.volume).toFixed(2)}` });
    }
    m.sort((a, b) => a.time - b.time);
    const key = tf + JSON.stringify(m.map((x) => [x.time, x.text, x.color]));
    if (key !== markerKey) { series.setMarkers(m); markerKey = key; }
  }

  // ---------------------------------------------------------------- the one line (state.ict.line)
  // 30 minutes ahead from the live price: the ICT plan, Kronos' 30-minute path and the quant model's forecast,
  // blended by each one's conviction on the server (brain.desk_line). Moved onto the live price while a candle forms.
  const LS = {
    AGREE: { col: C.gold, rgb: "214,173,82", w: 3, style: 0, a1: .11, a2: .24, word: "ALL AGREE" },
    "ICT ONLY": { col: C.gold, rgb: "214,173,82", w: 2, style: 2, a1: .08, a2: .17, word: "ICT ONLY" },
    "MODELS ONLY": { col: C.gold, rgb: "214,173,82", w: 2, style: 2, a1: .08, a2: .17, word: "MODELS ONLY" },
    CONFLICT: { col: C.dim, rgb: "142,138,128", w: 2, style: 2, a1: .08, a2: .15, word: "THEY DISAGREE" },
    FLAT: { col: C.dim, rgb: "142,138,128", w: 1.5, style: 2, a1: .07, a2: .13, word: "FLAT" },
  };
  const lineStyleOf = (L) => LS[L && L.state] || LS["ICT ONLY"];
  let curLine = null;
  function oneLine() {
    const L = S && S.ict && S.ict.line;
    if (!L || !Array.isArray(L.path) || !L.path.length || !last || loadedTf !== tf || !LINE_TF[tf]) return null;
    if (S.market && S.market.open === false) return null;
    const sec = TFSEC[tf];
    if (last.time + sec - L.t > 900) return null;               // the read is more than 15 minutes behind the chart
    const px = last.time + sec > L.t ? last.close : +L.last, d = px - L.last;
    const path = L.path.map((p) => ({ time: p.time, value: p.value + d }));
    const band = (L.band || []).map((b) => ({ time: b.time, lo: b.lo + d, hi: b.hi + d, p25: b.p25 + d, p75: b.p75 + d }));
    return { ...L, path, band, last: px, target: path[path.length - 1].value };
  }
  function kronosDay() {
    const k = S && S.kronos && S.kronos.day;
    if (tf !== "H1" || !k || !Array.isArray(k.path) || !k.path.length || !last || loadedTf !== tf) return null;
    if (last.time - k.t > 2 * 86400) return null;
    return k;
  }
  const byBar = (t0, v0, pts, sec) => {
    const start = t0 - (t0 % sec), m = new Map([[start, v0]]);
    for (const p of pts) { const b = p.time - (p.time % sec); if (b >= start) m.set(b, p.value); }
    return [...m].sort((a, b) => a[0] - b[0]).map(([time, value]) => ({ time, value }));
  };
  let fcKey = "";
  function drawForecast() {
    const L = oneLine(), K = kronosDay();
    curLine = L;
    const key = `${tf}|${L ? `${L.t}${L.state}${L.target}${L.last}` : ""}|${K ? `${K.t}${K.target}` : ""}`;
    if (key === fcKey) return;
    fcKey = key;
    bandRange = L && L.band.length ? [Math.min(...L.band.map((b) => b.lo)), Math.max(...L.band.map((b) => b.hi))] : null;
    if (L) {
      const st = lineStyleOf(L);
      lineS.applyOptions({ color: st.col, lineStyle: st.style, lineWidth: st.w });
      lineS.setData(byBar(L.t, L.last, L.path, TFSEC[tf]));
    } else lineS.setData([]);
    kdayS.setData(K ? byBar(K.t, K.last, K.path, 3600) : []);
  }

  // ---------------------------------------------------------------- your MT5 positions (state.positions, read-only)
  // The trades on your account (opened on your phone): the open price, and your own stop and target when set.
  // Drawn only; nothing on this page can change or close them.
  const posRows = () => (S && S.positions && S.positions.ok !== false && Array.isArray(S.positions.rows) ? S.positions.rows : []);
  function posLevels() {
    if (!ovl.pos) return [];
    const out = [];
    for (const p of posRows()) {
      const b = p.side === "BUY", col = b ? C.buy : C.sell;
      if (p.open != null) out.push([+p.open, col, `${b ? "B" : "S"} ${(+p.volume).toFixed(2)} ${money(p.profit)}`, 0, 2, 100]);
      if (+p.sl) out.push([+p.sl, col, "SL", 2, 1, 95]);
      if (+p.tp) out.push([+p.tp, col, "TP", 2, 1, 95]);
    }
    return out;
  }
  let pLines = [], pKey = "";
  function drawPosLines() {
    const want = posLevels().map(([price, color, , style, w]) => [price, color, style, w]);
    const key = JSON.stringify(want);
    if (key === pKey) return;
    pKey = key;
    pLines.forEach((l) => series.removePriceLine(l));
    pLines = want.map(([price, color, lineStyle, lineWidth]) => series.createPriceLine({ price, color, title: "", lineStyle, lineWidth, axisLabelVisible: true }));
  }

  // ---------------------------------------------------------------- chart text
  // Every word on the chart goes through here: a short tag on a dark pill, 11px. Tags are queued while shapes are
  // drawn, then placed most important first; a tag that would cover another moves a little, or is left out.
  const FONT = "600 11px JetBrains Mono, ui-monospace, Menlo, monospace", TH = 16, PADX = 4;
  let tags = [];
  const FONTB = "700 11.5px JetBrains Mono, ui-monospace, Menlo, monospace";
  const tag = (txt, x, y, col, o) => tags.push({ txt, x, y, col, align: (o && o.align) || "left", pri: (o && o.pri) || 10,
    edge: !!(o && o.edge), must: !!(o && o.must), faint: !!(o && o.faint), bold: !!(o && o.bold) });
  function flushTags(right, H) {
    zx.font = FONT; zx.textBaseline = "middle";
    const placed = [];
    const hit = (a) => placed.some((b) => a.x0 < b.x1 + 3 && a.x1 + 3 > b.x0 && a.y0 < b.y1 + 1 && a.y1 + 1 > b.y0);
    for (const t of tags.sort((a, b) => b.pri - a.pri)) {
      zx.font = t.bold ? FONTB : FONT;
      const w = zx.measureText(t.txt).width + PADX * 2;
      let x0 = t.align === "right" ? t.x - w : t.align === "center" ? t.x - w / 2 : t.x;
      x0 = Math.max(2, Math.min(right - w - 2, x0));
      let box = null;
      const steps = t.must ? 8 : t.edge ? 3 : 1;
      for (let k = 0; k <= 2 * steps; k++) {
        const dy = (k % 2 ? -1 : 1) * Math.ceil(k / 2) * TH;
        const yc = t.y + dy, b = { x0, x1: x0 + w, y0: yc - TH / 2, y1: yc + TH / 2, yc };
        if (b.y0 < 2 || b.y1 > H - 2) continue;
        if (!hit(b)) { box = b; break; }
      }
      if (!box) continue;
      placed.push(box);
      if (t.edge && Math.abs(box.yc - t.y) > 1) {    // moved off its line: a thin leader back to the level
        zx.strokeStyle = t.col; zx.globalAlpha = .7; zx.lineWidth = 1;
        zx.beginPath(); zx.moveTo(box.x1, box.yc); zx.lineTo(right, t.y); zx.stroke(); zx.globalAlpha = 1;
      }
      zx.fillStyle = "rgba(14,15,17,.88)";
      zx.beginPath(); zx.roundRect ? zx.roundRect(box.x0, box.y0, w, TH, 3) : zx.rect(box.x0, box.y0, w, TH); zx.fill();
      zx.strokeStyle = t.col; zx.globalAlpha = t.faint ? .3 : t.bold ? .9 : .55; zx.lineWidth = t.bold ? 1.5 : 1; zx.stroke(); zx.globalAlpha = 1;
      zx.fillStyle = t.col; zx.globalAlpha = t.faint ? .6 : 1;
      zx.fillText(t.txt, box.x0 + PADX, box.yc + .5); zx.globalAlpha = 1;
    }
    zx.textBaseline = "alphabetic";
    tags = [];
  }
  function fitText(txt, maxW) {
    zx.font = FONT;
    if (zx.measureText(txt).width + PADX * 2 <= maxW) return txt;
    let s = txt;
    while (s.length > 4 && zx.measureText(s + "…").width + PADX * 2 > maxW) s = s.slice(0, -1);
    return s.trimEnd() + "…";
  }

  const zc = $("zones"), zx = zc.getContext("2d");
  function drawZones() {
    const r = zc.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    if (zc.width !== Math.round(r.width * dpr) || zc.height !== Math.round(r.height * dpr)) { zc.width = Math.round(r.width * dpr); zc.height = Math.round(r.height * dpr); }
    zx.setTransform(dpr, 0, 0, dpr, 0, 0);
    zx.clearRect(0, 0, r.width, r.height);
    tags = [];
    if (!S || loadedTf !== tf || !last) return;
    const right = r.width - chart.priceScale("right").width();
    const sec = TFSEC[tf], ts = chart.timeScale();
    if (ovl.smc) { drawSmc(right, sec, ts); drawIctLevels(right, sec, ts); }
    if (ovl.smt) drawSmt(right, sec, ts);
    if (ovl.ict) { drawIct(right, sec, ts); drawForming(right, sec, ts); }
    drawLineBand(right, sec, ts); drawKdayTag(right, sec, ts);
    for (const [price, col, name, , , pri] of posLevels()) {            // your positions' tags at the right edge
      const yy = series.priceToCoordinate(price);
      if (yy != null) tag(name, right - 4, yy, col, { align: "right", pri, edge: true, must: true });
    }
    flushTags(right, r.height);
  }
  // x for any time on the candle clock, also between bars, before the first one (clamped to 0) and in the future
  function xOf(t, ts, sec) {
    const n = times.length;
    if (!n) return null;
    const b = t - (t % sec);
    let i;
    if (b <= times[0]) i = b < times[0] ? -1 : 0;
    else if (b >= times[n - 1]) i = n - 1 + (b - times[n - 1]) / sec;
    else { let lo = 0, hi = n - 1; while (hi - lo > 1) { const m = (lo + hi) >> 1; if (times[m] <= b) lo = m; else hi = m; } i = lo + (b - times[lo]) / (times[hi] - times[lo]); }
    if (i < 0) return 0;
    const x = ts.logicalToCoordinate(i);
    return x == null ? null : x;
  }

  // the line's range: light = where 9 in 10 paths end, darker = the middle half; a dot at the live price and the
  // tip, and the line's label at the tip (the action, then the three voices)
  function drawLineBand(right, sec, ts) {
    const L = curLine;
    if (!L || !Array.isArray(L.band) || !L.band.length) return;
    const st = lineStyleOf(L), y = (v) => series.priceToCoordinate(v);
    const start = L.t - (L.t % sec), bb = new Map([[start, { lo: L.last, hi: L.last, p25: L.last, p75: L.last }]]);
    for (const b of L.band) { const k = b.time - (b.time % sec); if (k >= start) bb.set(k, b); }
    const pts = [...bb].sort((a, b) => a[0] - b[0]).map(([t, b]) => ({ x: ts.timeToCoordinate(t), b })).filter((p) => p.x != null && p.x <= right);
    if (pts.length < 2) return;
    const shade = (lo, hi, fill) => {
      zx.beginPath();
      pts.forEach((p, i) => (i ? zx.lineTo(p.x, y(p.b[hi])) : zx.moveTo(p.x, y(p.b[hi]))));
      for (let i = pts.length - 1; i >= 0; i--) zx.lineTo(pts[i].x, y(pts[i].b[lo]));
      zx.closePath(); zx.fillStyle = fill; zx.fill();
    };
    shade("lo", "hi", `rgba(${st.rgb},${st.a1})`);
    shade("p25", "p75", `rgba(${st.rgb},${st.a2})`);
    const end = pts[pts.length - 1], x0 = pts[0].x;
    zx.fillStyle = "rgba(236,232,223,.07)"; zx.fillRect(Math.round(x0), 0, 1, zc.getBoundingClientRect().height - ts.height());
    zx.fillStyle = `rgba(${st.rgb},.95)`;
    zx.beginPath(); zx.arc(x0, y(L.last), 3, 0, 7); zx.fill();
    const ye = y(L.target);
    if (ye != null) { zx.beginPath(); zx.arc(end.x, ye, 3.5, 0, 7); zx.fill(); }
    const parts = String(L.label || L.action || "").split(" · "), head = parts[0] || "", rest = parts.slice(1).join(" · ");
    const yTop = (y(end.b.hi) ?? ye ?? 20) - TH / 2 - 4, maxW = Math.max(120, Math.min(end.x - 6, 470));
    tag(fitText(`${arr(L.dir)} ${head} · ${st.word}`, maxW), end.x, yTop - TH - 1, st.col, { align: "right", pri: 92, must: true });
    if (rest) tag(fitText(rest, maxW), end.x, yTop, st.col, { align: "right", pri: 91, must: true, faint: L.state !== "AGREE" });
  }
  function drawKdayTag(right, sec, ts) {
    const K = kronosDay();
    if (!K) return;
    const t = K.path[K.path.length - 1].time, x = xOf(t, ts, sec), yy = series.priceToCoordinate(K.target ?? K.path[K.path.length - 1].value);
    if (x == null || yy == null) return;
    const mv = (K.target ?? K.path[K.path.length - 1].value) - K.last;
    tag(`Kronos 24h ${arr(K.dir ?? Math.sign(mv))} ${sgn(mv)}`, Math.min(x, right - 4), yy - TH / 2 - 3, "rgb(214,173,82)", { align: "right", pri: 80, faint: true });
  }

  // Smart Money / ICT layer, from state.smc (all times on the candle clock, all optional):
  //  killzones [{name, start, end}] · pd {high, low, eq, from_time} · ote / fvg / ob [{dir, top, bottom, from_time, to_time, kind}]
  //  liquidity [{price, kind, from_time, to_time, swept}] · structure [{kind, dir, price, from_time, time}] · swings · levels
  const KZ = { Asia: "120,110,230", London: "47,123,245", "NY AM": "214,173,82", "NY PM": "214,120,82", "NY Lunch": "142,138,128" };
  function trimSmc(m) {
    const px = last ? last.close : null;
    const recent = (a, n) => (a || []).slice(-n);
    const liq = (m.liquidity || []);
    const near = (side) => liq.filter((l) => !l.swept && px != null && (side > 0 ? l.price >= px : l.price < px))
      .sort((a, b) => Math.abs(a.price - px) - Math.abs(b.price - px)).slice(0, 2);
    const nearest = (a, n) => px == null ? recent(a, n) : (a || []).filter((z) => !z.to_time)
      .sort((p, q) => Math.abs((p.top + p.bottom) / 2 - px) - Math.abs((q.top + q.bottom) / 2 - px)).slice(0, n);
    return { ...m, killzones: recent(m.killzones, 3), fvg: nearest(m.fvg, 2), ob: nearest(m.ob, 1), structure: recent(m.structure, 1),
      swings: recent(m.swings, 3), ote: recent(m.ote, 1).filter((o) => px != null && px <= o.top + (o.top - o.bottom) && px >= o.bottom - (o.top - o.bottom)),
      liquidity: px == null ? recent(liq, 4) : [...near(1), ...near(-1), ...liq.filter((l) => l.swept).slice(-1)] };
  }
  function drawSmc(right, sec, ts) {
    const m = S && S.smc && trimSmc(S.smc);
    if (!m) return;
    const y = (v) => series.priceToCoordinate(v);
    const X = (t) => (t == null ? right : Math.min(right, xOf(t, ts, sec) ?? right));
    const H = zc.getBoundingClientRect().height;
    const lab = right - 4;
    const hline = (x1, x2, yy, col, dash) => {
      zx.beginPath(); zx.setLineDash(dash || []); zx.strokeStyle = col; zx.lineWidth = 1;
      zx.moveTo(x1, Math.round(yy) + .5); zx.lineTo(x2, Math.round(yy) + .5); zx.stroke(); zx.setLineDash([]);
    };
    const box = (b, rgb, a, txt) => {           // a zone is a soft beam: bright edge where it was born, fading toward price
      const x1 = X(b.from_time), x2 = X(b.to_time), y1 = y(b.top), y2 = y(b.bottom);
      if (x1 == null || y1 == null || y2 == null || x2 <= x1) return;
      const h = Math.max(2, y2 - y1), g = zx.createLinearGradient(x1, 0, x2, 0);
      g.addColorStop(0, `rgba(${rgb},${a * 2.2})`); g.addColorStop(.35, `rgba(${rgb},${a})`); g.addColorStop(1, `rgba(${rgb},0)`);
      zx.fillStyle = g; zx.fillRect(x1, y1, x2 - x1, h);
      zx.fillStyle = `rgba(${rgb},.85)`; zx.fillRect(x1, y1, 2, h);
      if (txt && h >= 6) tag(txt, x1 + 5, (y1 + y2) / 2, `rgb(${rgb})`, { pri: 40, faint: true });
    };
    if (sec <= 900) for (const k of m.killzones || []) {            // sessions only make sense on 1m-15m
      const x1 = X(k.start), x2 = X(k.end), rgb = KZ[k.name] || "142,138,128";
      if (x1 == null || x2 <= x1) continue;
      const hb = H - ts.height();
      zx.fillStyle = `rgba(${rgb},.55)`; zx.fillRect(x1, hb - 3, x2 - x1, 3);
      zx.fillStyle = `rgba(${rgb},.025)`; zx.fillRect(x1, 0, x2 - x1, hb - 3);
      tag(k.name, Math.max(x1 + 3, 64), hb - 14, `rgb(${rgb})`, { pri: 15, faint: true });
    }
    const pd = m.pd;
    if (pd && pd.high && pd.low) {
      const x1 = X(pd.from_time) ?? 0, eq = pd.eq ?? (pd.high + pd.low) / 2, yh = y(pd.high), ye = y(eq), yl = y(pd.low);
      if (yh != null && ye != null && yl != null) {
        const gx = right - 5;
        zx.fillStyle = "rgba(229,72,77,.55)"; zx.fillRect(gx, yh, 3, ye - yh);
        zx.fillStyle = "rgba(47,182,124,.55)"; zx.fillRect(gx, ye, 3, yl - ye);
        hline(Math.max(x1, right - 90), right, ye, "rgba(236,232,223,.3)", [3, 3]);
        tag("EQ", gx - 6, ye, "rgb(236,232,223)", { align: "right", pri: 26, faint: true });
      }
    }
    for (const o of m.ote || []) box(o, "214,173,82", .07, "OTE");
    for (const g of m.fvg || []) box(g, g.dir === 1 ? "47,182,124" : "229,72,77", .09, g.kind || "FVG");
    for (const o of m.ob || []) box(o, o.dir === 1 ? "47,123,245" : "229,83,60", .1, o.kind || "OB");
    for (const l of m.liquidity || []) {
      const x1 = X(l.from_time) ?? 0, x2 = X(l.to_time), yy = y(l.price);
      if (yy == null) continue;
      hline(x1, x2, yy, l.swept ? "rgba(142,138,128,.45)" : "rgba(232,178,58,.85)", [1, 3]);
      tag(`${l.kind || "LIQ"} ${l.swept ? "✕" : "$"}`, l.swept ? x2 - 4 : lab, yy, l.swept ? "rgb(142,138,128)" : "rgb(232,178,58)",
        { align: "right", pri: l.swept ? 22 : 50, edge: !l.swept, faint: !!l.swept });
    }
    for (const b of m.structure || []) {
      const x1 = X(b.from_time), x2 = X(b.time), yy = y(b.price);
      if (x1 == null || yy == null) continue;
      const col = b.kind === "CHoCH" ? "rgba(232,178,58,.95)" : b.dir === 1 ? "rgba(47,182,124,.9)" : "rgba(229,72,77,.9)";
      hline(x1, x2, yy, col, b.kind === "CHoCH" ? [4, 3] : []);
      tag(b.kind || "BOS", (x1 + x2) / 2, yy + (b.dir === 1 ? -TH / 2 - 1 : TH / 2 + 1), col, { align: "center", pri: 55 });
    }
    for (const w of m.swings || []) {
      const xx = X(w.time), yy = y(w.price);
      if (xx == null || yy == null || xx >= right) continue;
      tag(w.kind, xx, yy + (/H$/.test(w.kind) ? -TH / 2 - 3 : TH / 2 + 3), "rgb(190,186,176)", { align: "center", pri: 30, faint: true });
    }
    for (const l of m.levels || []) {
      const x1 = l.time ? X(l.time) : 0, yy = y(l.price);
      if (yy == null) continue;
      hline(x1, right, yy, "rgba(236,232,223,.4)", [8, 4]);
      tag(l.label, lab, yy, "rgb(236,232,223)", { align: "right", pri: 45, edge: true });
    }
  }

  // ---------------------------------------------------------------- ICT setups on the chart (state.ict, playbook.py)
  // The best model's entry zone, the sweep (a ring at the raid) and the shift (a short line ending on the break),
  // plus up to 3 other armed A / A+ zones on this timeframe. No stops or targets: the zone, the raid and the break.
  const SHORT = { mss_fvg: "MSS+FVG", unicorn: "UNICORN", breaker: "BREAKER", pulse: "PULSE", pulse_rev: "PULSE REV", bpr: "BPR",
    silver_bullet: "SB", ifvg: "IFVG", ote: "OTE", ob_mt: "OB MT", turtle_soup: "T.SOUP", hrlr: "HRLR", smt: "SMT", gap: "GAP",
    cisd_fvg: "CISD+FVG", judas: "JUDAS" };
  const shortModel = (s) => SHORT[s.model] || String(s.name || s.model || "ICT").toUpperCase().slice(0, 10);
  const poolShort = (k) => String(k || "liquidity").replace(/swing high/i, "high").replace(/swing low/i, "low");
  function ictDraw() {
    const I = S && S.ict;
    if (!I || !ovl.ict) return [];
    const live = (s) => s && (s.status === "armed" || s.status === "filled") && s.zone;
    const best = I.best && I.best.setup, out = [];
    if (live(best)) out.push({ s: best, best: true });
    const px = last ? last.close : I.price, mid = (s) => (s.zone.top + s.zone.bottom) / 2;
    const others = (I.setups || []).filter((s) => s.status === "armed" && (s.grade === "A+" || s.grade === "A") && s.tf === tf && !(best && s.id === best.id) && live(s))
      .sort((a, b) => Math.abs(mid(a) - px) - Math.abs(mid(b) - px));
    for (const s of others) {
      if (out.length >= 3) break;
      if (out.some((o) => o.s.dir === s.dir && Math.abs(mid(o.s) - mid(s)) < 0.05)) continue;
      out.push({ s });
    }
    return out;
  }
  const hseg = (x1, x2, yy, col, dash, w) => {
    if (yy == null || x2 <= x1) return;
    zx.beginPath(); zx.setLineDash(dash || []); zx.strokeStyle = col; zx.lineWidth = w || 1;
    zx.moveTo(x1, Math.round(yy) + .5); zx.lineTo(x2, Math.round(yy) + .5); zx.stroke(); zx.setLineDash([]);
  };
  function drawIct(right, sec, ts) {
    const list = ictDraw();
    if (!list.length) return;
    const y = (v) => (v == null ? null : series.priceToCoordinate(v));
    const X = (t) => { const x = xOf(t, ts, sec); return x == null ? null : Math.min(right, x); };
    for (const { s, best } of [...list].reverse()) {             // the best one last, on top
      const up = s.dir > 0, rgb = up ? "47,182,124" : "229,72,77", ar = up ? "▲" : "▼";
      const t0 = (s.shift && s.shift.time) || s.t, x1 = Math.max(0, X(t0) ?? 0);
      const z = s.zone;
      const y1 = y(Math.max(z.top, z.bottom)), y2 = y(Math.min(z.top, z.bottom));
      if (y1 != null && y2 != null && x1 < right) {
        const h = Math.max(3, y2 - y1);
        zx.fillStyle = `rgba(214,173,82,${best ? .16 : .06})`; zx.fillRect(x1, y1, right - x1, h);
        zx.fillStyle = `rgba(${rgb},.9)`; zx.fillRect(x1, y1, 2, h);
        zx.setLineDash(s.status === "armed" ? [4, 3] : []); zx.strokeStyle = `rgba(214,173,82,${best ? .7 : .4})`; zx.lineWidth = 1;
        zx.strokeRect(x1 + .5, y1 + .5, right - x1 - 1, h - 1); zx.setLineDash([]);
        hseg(x1, right, (y1 + y2) / 2, `rgba(214,173,82,${best ? .55 : .3})`, [2, 3]);       // the zone's middle: the close must clear it
        tag(`${shortModel(s)} ${ar} ${s.grade || ""}${s.tf !== tf ? " " + tfName(s.tf) : ""}`.trim(), right - 4, (y1 + y2) / 2, best ? C.gold : "rgb(214,173,82)",
          { align: "right", pri: best ? 70 : 52, edge: true, faint: !best });
      }
      if (sec > 900) continue;                                     // sweep and shift markers on 1m-15m only
      const sw = s.sweep;
      if (sw && sw.time != null) {
        const xs = X(sw.time), ys = y(sw.ext ?? sw.level);
        if (xs != null && xs > 0 && xs < right && ys != null) {
          zx.strokeStyle = `rgba(${rgb},.95)`; zx.lineWidth = 1.5; zx.beginPath(); zx.arc(xs, ys, 4.5, 0, 7); zx.stroke();
          const yl = y(sw.level);
          if (yl != null) hseg(xs - 18, xs + 8, yl, `rgba(${rgb},.6)`, [1, 2]);
          tag(`$ ${poolShort(sw.kind)}`, xs, ys + (up ? TH / 2 + 7 : -TH / 2 - 7), `rgb(${rgb})`, { align: "center", pri: best ? 64 : 47, faint: !best });
        }
      }
      const sh = s.shift;
      if (sh && sh.time != null && sh.level != null) {
        const xh = X(sh.time), yh = y(sh.level);
        if (xh != null && xh > 0 && xh < right && yh != null) {
          const x0 = Math.max(0, xh - 7 * ts.options().barSpacing);
          hseg(x0, xh, yh, `rgba(${rgb},.95)`, [], 1.5);
          tag(sh.kind || "MSS", x0, yh + (up ? -TH / 2 - 2 : TH / 2 + 2), `rgb(${rgb})`, { pri: best ? 63 : 46, faint: !best });
        }
      }
    }
  }
  // models forming on the live candles (state.ict.forming): the level they need as a dashed ray from where it began,
  // their zone, and a tag "JUDAS BUY · forming 3/5 · 72%". The chart's own timeframe, plus M5 items faintly on M1;
  // at most 4, the most reliable first. An item at "enter" is shown by the ENTER arrow instead.
  const stepsDone = (f) => { const s = f.steps || []; return [s.filter((x) => x.done === true).length, s.length]; };
  const formWord = (f) => { const [a, n] = stepsDone(f); return `${f.stage || "forming"}${n ? ` ${a}/${n}` : ""} · ${Math.round((+f.confidence || 0) * 100)}%`; };
  function formDraw() {
    const I = S && S.ict;
    if (!I || !ovl.ict || !Array.isArray(I.forming)) return [];
    const bk = I.best_forming && I.best_forming.key;
    const it = I.forming.filter((f) => f.stage !== "enter" && (f.level != null || f.zone) && (f.tf === tf || (tf === "M1" && f.tf === "M5")));
    it.sort((a, b) => (b.key === bk) - (a.key === bk));
    return it.slice(0, Math.max(1, 4 - ictDraw().length)).map((f) => ({ f, best: !!bk && f.key === bk, faint: f.tf !== tf || f.stage === "watch" }));
  }
  function drawForming(right, sec, ts) {
    const list = formDraw();
    if (!list.length) return;
    const y = (v) => (v == null ? null : series.priceToCoordinate(v));
    const zones = ictDraw().map((o) => o.s.zone);
    for (const { f, best, faint } of [...list].reverse()) {
      const rgb = f.dir > 0 ? "47,182,124" : "229,72,77";
      const xa = f.anchor_time != null ? xOf(f.anchor_time, ts, sec) : xOf(last.time - 20 * sec, ts, sec);
      const x0 = Math.max(0, Math.min(right - 30, xa ?? 0));
      const z = f.zone;
      if (z && z.top != null && z.bottom != null && !zones.some((q) => Math.abs(q.top - z.top) < 0.05 && Math.abs(q.bottom - z.bottom) < 0.05)) {
        const y1 = y(Math.max(z.top, z.bottom)), y2 = y(Math.min(z.top, z.bottom));
        if (y1 != null && y2 != null) {
          const h = Math.max(3, y2 - y1);
          zx.fillStyle = `rgba(${rgb},${best ? .1 : .045})`; zx.fillRect(x0, y1, right - x0, h);
          zx.setLineDash([3, 3]); zx.strokeStyle = `rgba(${rgb},${best ? .7 : .35})`; zx.lineWidth = 1;
          zx.strokeRect(x0 + .5, y1 + .5, right - x0 - 1, h - 1); zx.setLineDash([]);
        }
      }
      const lv = f.level != null ? f.level : z ? (z.top + z.bottom) / 2 : null, yy = y(lv);
      if (yy == null) continue;
      hseg(x0, right, yy, `rgba(${rgb},${best ? .95 : faint ? .4 : .7})`, [6, 4], best ? 1.6 : 1);
      zx.fillStyle = `rgba(${rgb},${best ? .95 : .6})`; zx.beginPath(); zx.arc(x0, yy, best ? 3 : 2.2, 0, 7); zx.fill();
      const name = SHORT[f.model] || shortName(f.name).toUpperCase();
      tag(fitText(`${name} ${f.side || (f.dir > 0 ? "BUY" : "SELL")}${f.tf !== tf ? " " + tfName(f.tf) : ""} · ${formWord(f)}`, 300), right - 4, yy, `rgb(${rgb})`,
        { align: "right", pri: best ? 89 : faint ? 41 : 58, edge: true, faint: faint && !best, bold: best, must: best });
    }
  }

  // state.ict.levels: Asian high / low, opening gaps, and any of PDH / PDL / PWH / PWL / NMO the SMC layer isn't drawing
  function drawIctLevels(right, sec, ts) {
    const L = S.ict && S.ict.levels;
    if (!L) return;
    const H = zc.getBoundingClientRect().height - ts.height();
    const sl = (S.smc && S.smc.levels) || [], have = new Set(sl.map((l) => l.label));
    const y = (v) => (v == null ? null : series.priceToCoordinate(v));
    const inView = (yy) => yy != null && yy > 0 && yy < H;
    for (const [k, lab, rgb] of [["pdh", "PDH"], ["pdl", "PDL"], ["pwh", "PWH"], ["pwl", "PWL"], ["nmo", "NMO"], ["asia_high", "ASIA H", "120,110,230"], ["asia_low", "ASIA L", "120,110,230"]]) {
      const p = L[k];
      if (p == null || have.has(lab) || sl.some((l) => Math.abs(l.price - p) < 0.02)) continue;
      const yy = y(p);
      if (!inView(yy)) continue;
      const c = rgb || "236,232,223";
      hseg(0, right, yy, `rgba(${c},.45)`, [8, 4]);
      tag(lab, right - 4, yy, `rgb(${c})`, { align: "right", pri: 44, edge: true });
    }
    for (const [arr2, name] of [[L.nwog, "NWOG"], [L.ndog, "NDOG"]]) for (const g of (arr2 || []).slice(0, 2)) {
      const y1 = y(Math.max(g.top, g.bottom)), y2 = y(Math.min(g.top, g.bottom));
      if (y1 == null || y2 == null || y2 < 0 || y1 > H) continue;
      const x0 = g.time != null ? Math.max(0, Math.min(right, xOf(g.time, ts, sec) ?? 0)) : 0;
      zx.fillStyle = "rgba(150,140,230,.07)"; zx.fillRect(x0, y1, right - x0, Math.max(2, y2 - y1));
      hseg(x0, right, y1, "rgba(150,140,230,.35)", [2, 4]); hseg(x0, right, y2, "rgba(150,140,230,.35)", [2, 4]);
      if (g.ce != null) hseg(x0, right, y(g.ce), "rgba(150,140,230,.6)", [5, 4]);
      tag(`${name}${g.ce != null ? " CE" : ""}`, right - 4, g.ce != null ? y(g.ce) : (y1 + y2) / 2, "rgb(170,160,240)", { align: "right", pri: 36, edge: true, faint: true });
    }
  }
  // state.smt[tf].events / forming: gold's two swing points joined (a_time -> b_time at gold's prices), coloured by side
  function drawSmt(right, sec, ts) {
    const r = S.smt && S.smt[tf];
    if (!r) return;
    const evs = [...(r.events || []).slice(-3)];
    if (r.forming) evs.push({ ...r.forming, forming: true });
    const y = (v) => series.priceToCoordinate(v);
    for (const e of evs) {
      if (!e.gold || e.gold.length < 2 || e.a_time == null || e.b_time == null || e.a_time < first) continue;
      const xa = xOf(e.a_time, ts, sec), xb = xOf(e.b_time, ts, sec), ya = y(e.gold[0]), yb = y(e.gold[1]);
      if (xa == null || xb == null || ya == null || yb == null || xa >= right) continue;
      const rgb = e.dir > 0 ? "47,182,124" : "229,72,77", a = e.forming ? .9 : e.intact === false ? .35 : .85;
      zx.beginPath(); zx.setLineDash(e.forming ? [4, 3] : []); zx.strokeStyle = `rgba(${rgb},${a})`; zx.lineWidth = 1.5;
      zx.moveTo(xa, ya); zx.lineTo(Math.min(xb, right), yb); zx.stroke(); zx.setLineDash([]);
      zx.fillStyle = `rgba(${rgb},${a})`;
      for (const [xx, yy] of [[xa, ya], [xb, yb]]) if (xx <= right) { zx.beginPath(); zx.arc(xx, yy, 2.5, 0, 7); zx.fill(); }
      const hi = e.side === "high";
      tag(e.forming ? "SMT?" : "SMT", (xa + Math.min(xb, right)) / 2, (ya + yb) / 2 + (hi ? -TH / 2 - 5 : TH / 2 + 5), `rgb(${rgb})`,
        { align: "center", pri: e.forming ? 52 : 49, faint: e.intact === false });
    }
  }
  chart.timeScale().subscribeVisibleLogicalRangeChange(() => requestAnimationFrame(drawZones));
  new ResizeObserver(() => requestAnimationFrame(drawZones)).observe(zc);
  const placeReads = () => { document.querySelector(".reads").style.top = (10 + document.querySelector(".chartbar").offsetHeight + 6) + "px"; };
  new ResizeObserver(placeReads).observe(document.querySelector(".chartbar"));

  // ---------------------------------------------------------------- hover / tap on the chart
  // A marked candle shows its read (events, pattern, the action at that close); the future part of the chart (the
  // line) shows how the line is made: the three voices and their weights.
  const wrap = $("chartwrap"), chartTip = $("chartTip"), lineTip = $("lineTip");
  function placeTip(el, x, y) {
    el.hidden = false;
    const W = wrap.clientWidth, H = wrap.clientHeight, w = el.offsetWidth, h = el.offsetHeight;
    let left = x + 14, top = y + 14;
    if (left + w > W - 8) left = x - w - 14;
    left = Math.max(8, Math.min(W - w - 8, left));
    if (top + h > H - 8) top = Math.max(8, y - h - 14);
    el.style.left = left + "px"; el.style.top = top + "px";
  }
  function readTipHtml(r) {
    const evs = (r.events || []).filter((e) => e.kind !== "quiet");
    const ent = r.action_dir ? `<b class="h ${r.action_dir > 0 ? "up" : "down"}">ENTER ${r.action_dir > 0 ? "BUY" : "SELL"} · ${esc(r.model || "")}</b>` : "";
    return `${ent}<b class="h">${esc(tfName(r.tf || tf))} ${candleClock(r.time)} · <span class="${r.tone === "bullish" ? "up" : r.tone === "bearish" ? "down" : "dim"}">${esc(String(r.tone || "neutral").toUpperCase())}</span>${r.c != null ? ` · close ${fmt(r.c)}` : ""}</b>
      ${evs.length ? `<ul>${evs.map((e) => `<li class="ev ${tone(e.dir)}">${esc(e.text)}</li>`).join("")}</ul>` : `<span class="dim">${esc(((r.events || [])[0] || {}).text || "nothing new")}</span>`}
      ${r.action ? `<div class="act">At this close: <span class="${actCls(r.action)}">${esc(r.action)}</span></div>` : ""}`;
  }
  function lineLegendHtml(L) {
    const w = L.weights || {}, p = L.parts || {}, st = lineStyleOf(L);
    const row = (n, wt, d, t) => `<div class="wrow"><span class="n">${n}</span><span class="w">${Math.round((wt || 0) * 100)}%</span><span class="d ${tone(d)}">${arr(d)}</span><span class="t">${t}</span></div>`;
    const ki = p.ict || {}, kk = p.kronos || {}, kq = p.quant || {};
    return `<b class="h">THE LINE · ${esc(st.word)} · ${sgn(L.move)} in ${L.minutes || 30} min</b>
      <div class="wbar"><i class="ict" style="width:${(w.ict || 0) * 100}%"></i><i class="kr" style="width:${(w.kronos || 0) * 100}%"></i><i class="qu" style="width:${(w.quant || 0) * 100}%"></i><i class="an" style="width:${(w.anchor || 0) * 100}%"></i></div>
      ${row("ICT", w.ict, ki.dir, esc(ki.name || "no setup") + (ki.conviction != null ? ` · conviction ${(+ki.conviction).toFixed(2)}` : ""))}
      ${row("Kronos", w.kronos, kk.dir, kk.up_prob != null ? `${pctUp(kk.up_prob)} · conviction ${(+kk.conviction || 0).toFixed(2)}` : "off")}
      ${row("Quant", w.quant, kq.dir, kq.up_prob != null ? `${pctUp(kq.up_prob)} · conviction ${(+kq.conviction || 0).toFixed(2)}` : "off")}
      ${row("Stay put", w.anchor, 0, "the anchor: no voice is sure")}
      <div class="act">${esc(L.note || "")}</div>`;
  }
  let lineTipPinned = false;
  function onChartPointer(p) {
    if (!p || !p.point || p.time == null) { chartTip.hidden = true; if (!lineTipPinned) lineTip.hidden = true; return; }
    const r = tipMap.get(p.time);
    if (r && (glyphs(r).length || r.action_dir)) { setHtml(chartTip, readTipHtml(r)); placeTip(chartTip, p.point.x, p.point.y); }
    else chartTip.hidden = true;
    if (curLine && last && p.time > last.time) { lineTipPinned = false; setHtml(lineTip, lineLegendHtml(curLine)); placeTip(lineTip, p.point.x, p.point.y); }
    else if (!lineTipPinned) lineTip.hidden = true;
  }
  chart.subscribeCrosshairMove(onChartPointer);
  chart.subscribeClick(onChartPointer);
  const lineChip = $("lineChip");
  const showChipTip = (pin) => {
    if (!curLine) return;
    lineTipPinned = pin;
    setHtml(lineTip, lineLegendHtml(curLine));
    const a = lineChip.getBoundingClientRect(), b = wrap.getBoundingClientRect();
    placeTip(lineTip, a.left - b.left - 14, a.bottom - b.top - 8);
    lineChip.setAttribute("aria-expanded", String(pin));
  };
  lineChip.addEventListener("mouseenter", () => { if (!lineTipPinned) showChipTip(false); });
  lineChip.addEventListener("mouseleave", () => { if (!lineTipPinned) lineTip.hidden = true; });
  lineChip.addEventListener("click", () => { if (lineTipPinned) { lineTipPinned = false; lineTip.hidden = true; lineChip.setAttribute("aria-expanded", "false"); } else showChipTip(true); });

  // ---------------------------------------------------------------- chart buttons
  document.querySelectorAll("#ovl button").forEach((b) => {
    b.classList.toggle("on", !!ovl[b.dataset.o]);
    b.addEventListener("click", () => {
      ovl[b.dataset.o] = !ovl[b.dataset.o]; store.set("ovl5", ovl);
      b.classList.toggle("on", ovl[b.dataset.o]);
      markerKey = ""; fcKey = "x";
      if (S) { drawMarkers(); drawForecast(); drawPosLines(); drawZones(); renderChartChips(); }
    });
  });
  function setTf(t) {
    if (!TFSEC[t]) return;
    tf = t; store.set("tf5", tf);
    document.querySelectorAll("#tfs button").forEach((x) => x.classList.toggle("on", x.dataset.tf === tf));
    chartTip.hidden = true; lineTip.hidden = true; lineTipPinned = false;
    loadCandles();
    if (S) { renderFeed(); renderTopDown(); renderChartChips(); }
  }
  document.querySelectorAll("#tfs button").forEach((b) => {
    b.classList.toggle("on", b.dataset.tf === tf);
    b.addEventListener("click", () => setTf(b.dataset.tf));
  });

  // ---------------------------------------------------------------- "?" cheat sheet
  let lang = store.get("lang", "fa");
  function buildLegend() {
    $("legendBody").innerHTML = window.glossaryHtml ? window.glossaryHtml(lang) : "";
    document.querySelectorAll("#langs button").forEach((x) => x.classList.toggle("on", x.dataset.l === lang));
    $("legendBody").scrollTop = 0;
  }
  $("langs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-l]");
    if (b) { lang = b.dataset.l; store.set("lang", lang); buildLegend(); }
  });
  const showLegend = (on) => { $("legend").hidden = !on; $("helpBtn").classList.toggle("on", on); if (on) buildLegend(); };
  $("helpBtn").addEventListener("click", () => showLegend($("legend").hidden));
  $("helpTop").addEventListener("click", () => { showLegend(true); if (window.innerWidth <= 900) wrap.scrollIntoView({ block: "start" }); });
  $("legendClose").addEventListener("click", () => showLegend(false));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { showLegend(false); lineTipPinned = false; lineTip.hidden = true; } });
  document.addEventListener("toggle", (e) => { const d = e.target; if (d && d.dataset && d.dataset.k) openK[d.dataset.k] = d.open; }, true);

  // ---------------------------------------------------------------- live prices (pushed by the server)
  function showQuote(bid, ask) {
    $("bid").textContent = fmt(bid);
    $("ask").textContent = fmt(ask);
    $("spr").textContent = bid != null && ask != null ? fmt(ask - bid) : "-";
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
  function paintConn() {
    const live = Date.now() - lastQuoteAt < 15000, mk = S && S.market, br = S && S.broker;
    let cls = "", txt, why = "";
    if (mk && mk.open === false && !live) { cls = "closed"; txt = "Market closed"; why = mk.note || ""; }
    else if (br && br.connected === false) { txt = "Feed offline"; why = br.message || "The chart feed is not connected."; }
    else if (!live) { txt = lastQuoteAt ? "Prices stopped" : "Connecting"; why = lastQuoteAt ? "No new prices for 15 seconds." : "Waiting for the first price."; }
    else { cls = "on"; txt = S && S.source === "mt5" ? "Live from MT5" : "Live"; }
    $("conn").className = "conn " + cls;
    $("connTxt").textContent = txt;
    $("conn").title = why;
    if (!live && last && !(S && S.tick)) { $("bid").textContent = fmt(last.close); $("ask").textContent = "-"; $("spr").textContent = "-"; }
  }

  // countdowns and ages that change every second, outside the re-rendered HTML
  const mmss = (s) => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`; };
  function tick() {
    paintConn();
    const now = Date.now() / 1000, cn = chartNow();
    document.querySelectorAll("[data-nc]").forEach((el) => {
      const sec = TFSEC[el.dataset.nc] || 60, nxt = Math.floor(cn / sec) * sec + sec;
      el.textContent = mmss(nxt - cn);
    });
    document.querySelectorAll("[data-cd]").forEach((el) => {
      const left = +el.dataset.cd - now;
      el.textContent = left > 0 ? `in ${mmss(left)}` : left > -1800 ? "just out" : "out";
    });
    document.querySelectorAll("[data-agec]").forEach((el) => {
      const m = Math.max(0, Math.round((cn - +el.dataset.agec) / 60));
      el.textContent = m < 60 ? `${m} min` : m < 1440 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${Math.floor(m / 1440)} d ${Math.floor((m % 1440) / 60)} h`;
    });
    document.querySelectorAll("[data-age]").forEach((el) => {
      const m = Math.max(0, Math.round((now - +el.dataset.age) / 60));
      el.textContent = m < 60 ? `${m} min ago` : m < 1440 ? `${Math.floor(m / 60)} h ago` : `${Math.floor(m / 1440)} d ago`;
    });
  }
  setInterval(tick, 1000);

  // ---------------------------------------------------------------- 1. what to do now (state.ict.decision: the one answer)
  // The page only shows what the server decided: the action, its sentence, its checklist, the most reliable forming
  // model (aligned with the decision on the server) and the server's sentence about your open position.
  const KIND = (a) => /NOW$/.test(a) ? "now" : /^DON'T/.test(a) ? "dont" : /^GET READY/.test(a) ? "ready" : /^(WAIT FOR|WATCH)/.test(a) ? "watch" : /CLOSED/.test(a) ? "closed" : "wait";
  const sideOf = (a) => (/BUY/.test(a || "") ? 1 : /SELL/.test(a || "") ? -1 : 0);
  const CKN = { bias: "Bias", key: "Level", pd: "P / D", time: "Time", sweep: "Sweep", shift: "Shift", smt: "SMT", kronos: "Kronos", rr: "Room",
    news: "News", "Candle-close confirmation": "Close", "Candlestick pattern on the entry candle": "Pattern" };
  const ckRow = (x) => `<span class="ck ${x.ok == null ? "na" : ""}"><i class="${x.ok === true ? "ok" : x.ok === false ? "no" : "na"}">${x.ok === true ? "✓" : x.ok === false ? "✗" : "–"}</i><em title="${esc(x.key || "")}">${esc(CKN[x.key] || x.key || "")}</em><span>${esc(x.label)}</span></span>`;
  function ckBlock(title, cks) {
    if (!Array.isArray(cks) || !cks.length) return "";
    const ok = cks.filter((c) => c.ok === true).length, no = cks.filter((c) => c.ok === false).length;
    return `<div class="cks"><b>${title} · ✓ ${ok} · ✗ ${no} · – ${cks.length - ok - no}</b>${cks.map(ckRow).join("")}</div>`;
  }
  const stepRow = (x) => `<span class="st2 ${x.done == null ? "todo" : ""}"><i class="${x.done === true ? "ok" : x.done === false ? "no" : "na"}">${x.done === true ? "✓" : x.done === false ? "✗" : "–"}</i><span>${esc(x.label)}</span></span>`;
  // the setup the decision names (by its id, else the one of that model and timeframe): for its trigger and pattern
  function setupOf(I, D) {
    const ss = I.setups || [];
    return (D.setup_id && ss.find((s) => s.id === D.setup_id))
      || (D.model && ss.find((s) => s.name === D.model && s.trigger && (!D.tf || s.trigger.tf === D.tf)))
      || (D.model && ss.find((s) => s.name === D.model && (!D.tf || s.tf === D.tf)))
      || null;
  }
  const partChip = (name, p) => (!p || p.up_prob == null ? `<span class="chip">${esc(name)} off</span>`
    : `<span class="chip ${tone(p.dir)}">${esc(name)} ${arr(p.dir)} ${pctUp(p.up_prob)}</span>`);
  // a forming item, as the server sent it: name, side, timeframe, stage, confidence, steps, next, confluence, conflict
  function formHtml(B, mini) {
    const [a, n] = stepsDone(B);
    const cf = B.conflict ? `<div class="twoway"><b>Two-way:</b> ${esc(B.conflict.name || "another model")} ${esc(B.conflict.side || "")}${B.conflict.confidence != null ? ` (${Math.round(B.conflict.confidence * 100)}%)` : ""} points the other way.</div>` : "";
    const cn = (B.confluence || []).length ? `<p class="meta">Confirmed by ${esc(B.confluence.join(", "))}</p>` : "";
    return `<div class="fbest ${tone(B.dir)}${mini ? " compact" : ""}"><div class="fh">Most reliable now</div>
      <div class="fn"><b class="nm">${esc(B.name)}</b><b class="${tone(B.dir)}">${esc(B.side || "")}</b><span>${esc(tfName(B.tf))}</span><span class="stg s-${esc(B.stage)}">${esc(B.stage)} ${a}/${n}</span>${B.trust != null ? `<span class="mb" title="Trust earned in the knowledge mesh">mesh ×${(+B.trust).toFixed(1)}</span>` : ""}<span class="cfb" title="Confidence: stage × fit for this market × measured record × higher-timeframe agreement × Kronos × grade × mesh trust">${Math.round((+B.confidence || 0) * 100)}%</span></div>
      <div class="pbar"><i style="width:${Math.round((+B.progress || 0) * 100)}%"></i></div>
      ${!mini && (B.steps || []).length ? `<div class="steps">${B.steps.map(stepRow).join("")}</div>` : ""}
      ${B.next ? `<div class="nextc"><b>Next</b>${esc(B.next)}</div>` : ""}
      ${!mini && (B.level != null || B.zone) ? `<p class="meta num">${B.zone ? `zone ${fmt(Math.min(B.zone.top, B.zone.bottom))}–${fmt(Math.max(B.zone.top, B.zone.bottom))}` : ""}${B.zone && B.level != null ? " · " : ""}${B.level != null ? `level ${fmt(B.level)}` : ""}</p>` : ""}
      ${cn}${cf}${!mini && (B.why || []).length ? `<p class="meta">${esc(B.why.join(" · "))}</p>` : ""}</div>`;
  }
  function renderTodo() {
    const el = $("todo"), I = S.ict, closed = !!(S.market && S.market.open === false);
    if (!I) {
      el.className = "box";
      setHtml(el, `<div class="bh"><span class="eyebrow">What to do now</span></div><div class="act dim">${closed ? "MARKET CLOSED" : "READING THE CHART…"}</div>
        <p class="say">${closed ? esc(S.market.note || "Gold is closed.") : "The playbook reads every timeframe once enough candles have closed. The answer shows here in a moment."}</p>`);
      return;
    }
    const tk = I.talk || {}, ta = tk.action || {};
    const D = I.decision || { action: tk.headline || ta.do || "WAIT", dir: ta.dir || 0, level: ta.level, model: ta.model, tf: ta.tf, grade: ta.grade, text: tk.summary || "", why: [], checks: [] };
    const act = D.action || "WAIT", k = KIND(act), sd = k === "closed" || k === "wait" ? 0 : (D.dir || sideOf(act));
    const sx = setupOf(I, D), tr = sx && sx.trigger, tfD = D.tf || (tr && tr.tf) || "M1";
    const rd = I.reads && I.reads[tfD], L = I.line, p = (L && L.parts) || {};
    const cls = k === "dont" ? "dont" : k === "wait" || k === "closed" ? "dim" : tone(sd) + (k === "watch" ? " soft" : "");
    const head = `${k === "dont" ? "✕ " : sd ? arr(sd) + " " : ""}${esc(act)}`;
    let lv = "", because = "";
    if (k === "now") {
      lv = `<div class="enter">Enter on this close <b class="${tone(sd)}">${esc(tfName(tfD))} ${fmt(D.level)}</b></div>`;
      because = [`<span class="chip on" title="${esc((sx && (sx.why || []).join(" · ")) || "")}">${esc(D.model || "ICT model")}${sx ? ` · ${esc(tfName(sx.tf))}` : ""} ${D.grade ? `<b>${esc(D.grade)}</b>` : ""}</span>`,
        tr && tr.pattern ? `<span class="chip ${tone(sd)}" title="${esc(tr.pattern_meaning || "")}">${esc(tr.pattern)}</span>` : "",
        partChip("Kronos", p.kronos), partChip("Quant", p.quant)].join("");
    } else if (k === "dont") {
      const miss = (sx && sx.entry_now && sx.entry_now.missing) || [];
      lv = `<div class="enter">${esc(D.model || "A model")} triggered on the ${esc(tfName(tfD))} close <b>${fmt(D.level)}</b> · skip it</div>`;
      because = miss.map((m) => `<span class="chip bad">✗ ${esc(m)}</span>`).join("");
    } else if (k === "ready" || k === "watch") {
      const z = sx && sx.zone;
      lv = /^WATCH/.test(act) ? `<div class="enter">Shift level <b>${fmt(D.level)}</b> on ${esc(tfName(tfD))}</div>`
        : z ? `<div class="enter">${k === "ready" ? "At the zone" : "Zone"} <b>${fmt(Math.min(z.top, z.bottom))}–${fmt(Math.max(z.top, z.bottom))}</b> ${esc(tfName(sx.tf))}${k === "ready" ? " · decide on the next close" : ""}</div>`
        : D.level != null ? `<div class="enter">Level <b>${fmt(D.level)}</b></div>` : "";
      because = [D.model ? `<span class="chip">${esc(D.model)}${D.grade ? ` <b>${esc(D.grade)}</b>` : ""}</span>` : "", partChip("Kronos", p.kronos), partChip("Quant", p.quant)].join("");
    } else if (k === "closed") {
      lv = S.market && S.market.note ? `<p class="meta">${esc(S.market.note)}</p>` : "";
    } else if (D.level != null) {
      lv = `<div class="enter">Level <b>${fmt(D.level)}</b></div>`;
    }
    const nw = S.news, news = ((nw && nw.wait) || I.news) && k !== "closed"
      ? `<div class="newswait"><b>News window:</b> ${esc((nw && nw.wait_text) || "high-impact USD news within 15 minutes: no entries until it's out.")}</div>` : "";
    const two = D.conflict ? `<div class="twoway"><b>Two-way market:</b> the models point both ways, so the answer is to wait.${I.best_forming && I.best_forming.conflict ? ` ${esc(I.best_forming.name)} ${esc(I.best_forming.side || "")} vs ${esc(I.best_forming.conflict.name || "")} ${esc(I.best_forming.conflict.side || "")}.` : ""}</div>` : "";
    const B = I.best_forming;
    const form = B && k !== "now" && k !== "closed" ? formHtml(B, true) : "";
    const checks = (D.checks || []).length ? ckBlock(k === "now" || k === "dont" ? "Entry checklist" : "Setup checklist", D.checks)
      : B && (B.steps || []).length && k !== "closed" ? `<div class="cks"><b>${esc(shortName(B.name))} · steps</b><div class="steps">${B.steps.map(stepRow).join("")}</div></div>` : "";
    const P = S.positions, mine = P && P.read ? `<div class="mine ${(P.net_lots || 0) < 0 ? "s" : ""}">${esc(P.read)}</div>` : "";
    const why = (k === "now" || k === "dont") && (D.why || []).length
      ? `<details class="fold" data-k="todo-why" ${openK["todo-why"] ? "open" : ""}><summary>Why, in full</summary><ul class="why2">${D.why.slice(0, 8).map((w) => `<li>${esc(w)}</li>`).join("")}</ul></details>` : "";
    const st = L ? lineStyleOf(L) : null;
    const foot = `<div class="foot">${L ? `<span title="${esc(L.label || "")}">Line: <b class="${L.state === "AGREE" ? "" : "dim"}" style="${L.state === "AGREE" ? "color:var(--gold)" : ""}">${arr(L.dir)} ${esc(st.word)}</b> ${sgn(L.move)} in 30 min</span>` : ""}<span>${I.proven ? "tested" : "not proven"} · analysis only, nothing is sent</span></div>`;
    el.className = `box k-${k} ${tone(sd)}`;
    setHtml(el, `<div class="bh"><span class="eyebrow">What to do now</span><span class="r">${rd ? `${esc(tfName(tfD))} ${candleClock(rd.time)}${k !== "closed" ? " · " : ""}` : ""}${k !== "closed" ? `next close <b data-nc="${esc(tfD)}">-</b>` : ""}</span></div>
      <div class="act ${cls}">${head} ${gradeHtml(k === "now" || k === "ready" ? D.grade : null)}</div>
      ${lv}${because ? `<div class="because">${because}</div>` : ""}
      <p class="say">${esc(D.text || "")}</p>
      ${two}${mine}${news}${form}
      ${checks}
      ${why}${foot}`);
  }

  // ---------------------------------------------------------------- my MT5 positions (state.positions, read-only)
  function renderPositions() {
    const el = $("posBox"), P = S.positions, rows = posRows();
    el.hidden = !P || (P.ok !== false && !rows.length && !P.count);
    if (el.hidden) return;
    if (P.ok === false || !rows.length) {
      el.className = "box empty";
      setHtml(el, `<div class="bh"><span class="eyebrow">My MT5 positions</span><span class="r">${esc(P.note || "unavailable")}</span></div>`);
      return;
    }
    el.className = "box";
    const body = rows.map((p) => {
      const b = p.side === "BUY";
      return `<div class="prow"><span class="l1"><span class="sd2 ${b ? "b" : "s"}">${b ? "BUY" : "SELL"}</span>${(+p.volume).toFixed(2)} @ ${fmt(p.open)} → ${fmt(p.price)}</span><span class="pl ${tone(p.profit)}">${money(p.profit)}</span>
        <span class="l2"><span>SL ${+p.sl ? fmt(p.sl) : "none"}</span><span>TP ${+p.tp ? fmt(p.tp) : "none"}</span>${p.time != null ? `<span>open <span data-agec="${p.time}"></span></span>` : ""}${p.ticket != null ? `<span>#${esc(p.ticket)}</span>` : ""}${p.comment ? `<span>${esc(p.comment)}</span>` : ""}</span></div>`;
    }).join("");
    const net = +P.net_lots || 0;
    setHtml(el, `<div class="bh"><span class="eyebrow">My MT5 positions · ${rows.length}</span><span class="r">read-only</span></div>${body}
      <div class="ptot"><span>Floating <b class="${tone(P.profit)}">${money(P.profit)}</b></span><span>Net <b class="${net > 0 ? "b" : net < 0 ? "s" : ""}">${net > 0 ? "long " : net < 0 ? "short " : ""}${Math.abs(net).toFixed(2)}</b> lots</span></div>
      ${P.note ? `<p class="meta">${esc(P.note)}</p>` : ""}`);
  }

  // ---------------------------------------------------------------- forming now (state.ict.forming / best_forming)
  const openForm = new Set();
  let formAll = false;
  function renderForming() {
    const el = $("formBox"), I = S.ict;
    el.hidden = !I || !Array.isArray(I.forming);
    if (el.hidden) return;
    const all = I.forming, B = I.best_forming, bk = B && B.key;
    const head = `<div class="bh"><span class="eyebrow">Forming now</span><span class="r">${all.length ? `${all.length} model${all.length === 1 ? "" : "s"} building · most reliable first` : "on the live candles"}</span></div>`;
    if (!all.length) {
      setHtml(el, `${head}<p class="say" style="font-size:13px">Nothing forming. Watching: ${esc((I.decision && I.decision.text) || "the next sweep of liquidity in a killzone")}</p>`);
      return;
    }
    const best = B ? formHtml(B, false) : `<p class="meta" style="margin:0 0 4px">Nothing past the watch stage yet: these are levels liquidity may be taken at.</p>`;
    const rest = all.filter((f) => f.key !== bk), shown = formAll ? rest : rest.slice(0, 6);
    const rows = shown.map((f) => {
      const [a, n] = stepsDone(f), open = openForm.has(f.key);
      return `<div class="fr${f.stage === "watch" ? " w" : ""}${open ? " open" : ""}" data-f="${esc(f.key)}" title="${esc(f.next ? "Next: " + f.next : f.text || "")}">
        <span class="stg s-${esc(f.stage)}">${esc(f.stage)} ${a}/${n}</span><span class="pb"><i style="width:${Math.round((+f.progress || 0) * 100)}%"></i></span><span class="cf">${Math.round((+f.confidence || 0) * 100)}%</span>
        <span class="nm">${esc(f.name)} <b class="${tone(f.dir)}">${esc(f.side || "")}</b> <span class="dim">${esc(tfName(f.tf))}</span>${f.trust != null ? `<span class="mb">mesh ×${(+f.trust).toFixed(1)}</span>` : ""}${f.conflict ? ` <span class="warnc" title="${esc(`${f.conflict.name || ""} ${f.conflict.side || ""}`)}">two-way</span>` : ""}</span>
        <span class="nx">${f.next ? `<b class="dim">Next:</b> ${esc(f.next)}` : ""}${(f.confluence || []).length ? `<br><span class="dim">Confirmed by ${esc(f.confluence.join(", "))}</span>` : ""}${f.conflict ? `<br><span class="warnc">Two-way: ${esc(f.conflict.name || "")} ${esc(f.conflict.side || "")}${f.conflict.confidence != null ? ` (${Math.round(f.conflict.confidence * 100)}%)` : ""}</span>` : ""}${(f.steps || []).length ? `<span class="steps" style="margin-top:3px">${f.steps.map(stepRow).join("")}</span>` : ""}</span></div>`;
    }).join("");
    setHtml(el, `${head}${best}${rows}${rest.length > 6 ? `<button class="rkall" data-fall="1">${formAll ? "Show fewer" : `All ${rest.length}`}</button>` : ""}`);
  }
  $("formBox").addEventListener("click", (e) => {
    if (e.target.closest("[data-fall]")) { formAll = !formAll; renderForming(); return; }
    const r = e.target.closest(".fr[data-f]");
    if (!r) return;
    const k = r.dataset.f;
    if (openForm.has(k)) openForm.delete(k); else openForm.add(k);
    r.classList.toggle("open");
    $("formBox")._h = null;
  });

  // ---------------------------------------------------------------- phone pushes (state.push): topic in the header, last ones in Details
  function renderPush() {
    const P = S.push, pill = $("pushPill"), el = $("pushList");
    const on = !!(P && P.topic), srv = P && P.server && !/^(https?:\/\/)?ntfy\.sh\/?$/i.test(String(P.server)) ? P.server : null;
    pill.textContent = on ? `PUSH · ${P.topic}` : "PUSH OFF";
    pill.className = "pill" + (on ? " ao" : "");
    pill.title = on ? `Phone pushes: subscribe to ${P.topic} in the ntfy app${srv ? ` (server ${srv})` : ""} · ${P.sent || 0} sent${P.error ? ` · last push failed: ${P.error}` : ""}`
      : "Phone pushes are off: no ntfy topic is set" + (S.source === "demo" ? " (the demo never pushes)" : "");
    if (!P) { setHtml(el, ""); return; }
    const rec = (P.recent || []).slice(-4).reverse();
    setHtml(el, `<div class="bh"><span class="eyebrow">Phone pushes</span><span class="r">${on ? `${P.sent || 0} sent` : "off"}</span></div>
      ${on ? `<p class="say" style="font-size:12.5px;margin:0">Subscribe to <b class="num" style="color:var(--gold)">${esc(P.topic)}</b> in the ntfy app${srv ? ` (server ${esc(srv)})` : ""}.</p>`
        : `<p class="meta" style="margin:0">Pushes off: no ntfy topic is set${S.source === "demo" ? " (the demo never pushes)" : ""}.</p>`}
      ${P.error ? `<p class="meta warnc">Last push failed: ${esc(P.error)}</p>` : ""}
      ${rec.length ? `<div class="pushl">${rec.map((r) => `<div><b>${esc(r.title || "")}</b>${r.time ? ` <span class="dim num">${clock(tsec(r.time))} · <span data-age="${tsec(r.time)}"></span></span>` : ""}<p>${esc(r.body || "")}</p></div>`).join("")}</div>` : ""}`);
  }

  // ---------------------------------------------------------------- 2. overall analysis (state.overall): every voice, expandable
  // Tap a voice for its details; Kronos and the quant model (with its three session models) open their own records here.
  const openVo = new Set();
  function kronosVoiceHtml() {
    const k = S.kronos, m = k && k.m30, kk = m && m.up_prob != null ? m : S.ict && S.ict.kronos30;
    let h;
    if (kk && kk.up_prob != null) {
      const cal = (m && m.calibration) || {}, c = cal.M30 || cal.M1x30;
      h = `<div>${arr(kk.dir)} ${esc(kk.call || "")} ${pctUp(kk.up_prob)} · ${sgn(kk.move)} in 30 min${kk.confidence != null ? ` · confidence ${(+kk.confidence).toFixed(2)}` : ""}${m && m.agree != null ? ` · M1 and M5 ${m.agree ? "agree" : "disagree"}` : ""}</div>
        <div class="dim">${c && (c.n || c.n_dir) ? `${c.n ?? c.n_dir} forecasts scored live · skill ${c.skill != null ? (+c.skill).toFixed(2) : "-"}${c.hit_rate != null ? ` · direction right ${Math.round(c.hit_rate * 100)}%` : ""} · beat a coin: <b class="${c.beats_coin ? "up" : "warnc"}">${c.beats_coin ? "yes" : "not yet"}</b>` : "Live record: nothing scored yet."}</div>`;
    } else h = `<div class="dim">${esc(k ? (k.m30_error || k.m30_status || "waiting for its first 30-minute forecast") : "off: start Gold Desk with --kronos")}</div>`;
    return h + (k ? `<div style="margin-top:6px">${kronosCardHtml()}</div>` : "");
  }
  function quantVoiceHtml() {
    const q = S.quant;
    let h;
    if (q && q.ok && q.up_prob != null) {
      const s = q.skill || {};
      const top = (q.top || []).slice(0, 3).map((f) => `${esc(f.feature)}${f.value != null ? ` ${typeof f.value === "number" ? (+f.value).toFixed(2) : esc(f.value)}` : ""} <b class="${tone(f.push)}">${arr(f.push)}</b>`).join(" · ");
      h = `<div>${arr(q.dir)} ${esc(q.call || "")} ${pctUp(q.up_prob)} · ${sgn(q.move)}${q.sd != null ? ` ±${fmt(q.sd)}` : ""} in ${q.horizon || 30} min${q.confidence != null ? ` · confidence ${(+q.confidence).toFixed(2)}` : ""}</div>
        <div class="dim">Beat a coin${s.test_period ? ` on ${esc(s.test_period)}` : " out of sample"}: <b class="${s.beats_coin ? "up" : "warnc"}">${s.beats_coin ? "yes" : "no"}</b>${s.brier_skill != null ? ` · Brier skill ${(+s.brier_skill).toFixed(3)}` : ""}${s.accuracy != null ? ` · right ${Math.round(s.accuracy * 100)}%${s.acc_low95 != null ? ` (95% low ${Math.round(s.acc_low95 * 100)}%)` : ""}` : ""}${s.n_test != null ? ` · n=${(+s.n_test).toLocaleString("en-US")}` : ""}${s.trained_period ? ` · trained on ${esc(s.trained_period)}` : ""}</div>
        ${top ? `<div class="dim">Pushing it: ${top}</div>` : ""}${q.model ? `<div class="dim">${esc(q.model)}${q.kronos_used ? " · uses Kronos" : ""}</div>` : ""}`;
    } else h = `<div class="dim">${esc((q && q.status) || "not running")}</div>`;
    const SESS = [["london", "London"], ["overlap", "LON/NY overlap"], ["ny", "New York"]];
    const sess = q && q.sessions ? SESS.filter(([key]) => q.sessions[key]).map(([key, nm]) => {
      const x = q.sessions[key], sk = x.skill || {}, up = x.up_prob, d = x.dir || 0;
      const off = up == null || /wait|not trained|untrained|no model|unavailable|missing/i.test(x.status || "");
      const call = up == null ? "–" : d > 0 ? `▲ UP ${Math.round(up * 100)}%` : d < 0 ? `▼ DOWN ${Math.round((1 - up) * 100)}%` : `• FLAT ${Math.round(up * 100)}% up`;
      const coin = sk.n_test != null || sk.accuracy != null
        ? `beat a coin: <b class="${sk.beats_coin ? "up" : "warnc"}">${sk.beats_coin ? "yes" : "no"}</b>${sk.accuracy != null ? ` (${Math.round(sk.accuracy * 100)}%${sk.n_test != null ? ` on ${(+sk.n_test).toLocaleString("en-US")} days` : ""})` : ""}` : "not tested";
      const tip = [x.hypothesis ? `hypothesis ${x.hypothesis}` : "", sk.test_period ? `tested ${sk.test_period}` : "", sk.brier_skill != null ? `Brier skill ${(+sk.brier_skill).toFixed(3)}` : "", x.status || ""].filter(Boolean).join(" · ");
      return `<div class="qs${off ? " off" : ""}" title="${esc(tip)}"><span>${esc(nm)}</span><span><span class="c ${tone(d)}">${call}</span>${x.move_pct != null ? `<span class="x">${x.move_pct > 0 ? "+" : ""}${(+x.move_pct).toFixed(2)}% · </span>` : ""}<span class="x">${coin}</span>${x.status ? `<span class="st">${esc(x.status)}</span>` : ""}</span></div>`;
    }).join("") : "";
    return h + (sess ? `<div style="margin-top:6px"><b class="dim" style="font:700 10.5px var(--label);letter-spacing:.1em">SESSION MODELS</b>${sess}</div>` : "");
  }
  function renderOverall() {
    const O = S.overall, el = $("overall");
    if (!O) {
      setHtml(el, `<div class="bh"><span class="eyebrow">Overall analysis</span></div><p class="meta">Appears once the ICT read is ready: news, ICT concepts, Kronos, the quant model, the killzones, SMT and the last candle in one verdict.</p>`);
      return;
    }
    const sc = Math.max(-1, Math.min(1, +O.score || 0)), cls = O.verdict === "BULLISH" ? "up" : O.verdict === "BEARISH" ? "down" : "dim";
    const ag = new Set(O.against || []);
    const voices = (O.voices || []).map((v) => {
      const off = v.value == null, x = off ? 0 : Math.max(-1, Math.min(1, +v.value)), open = openVo.has(v.name);
      const bar = off ? "" : `<i style="${x >= 0 ? `left:50%;width:${x * 50}%;background:var(--up)` : `right:50%;width:${-x * 50}%;background:var(--down)`}"></i>`;
      const vt = String(v.text || "").replace(/\s*\[mesh trust x[\d.]+\]\s*$/, "");
      const mb = v.trust != null ? `<span class="mb" title="Trust this voice earned in the knowledge mesh (0 to 2): its weight is multiplied by it">mesh ×${(+v.trust).toFixed(1)}</span>` : "";
      const more = !open ? "" : /^Kronos/.test(v.name) ? kronosVoiceHtml() : /^Quant/.test(v.name) ? quantVoiceHtml() : `<div>${esc(vt)}</div>${v.trust != null ? `<div class="dim">Weight ×${esc(v.weight)} after the knowledge-mesh trust ×${(+v.trust).toFixed(2)}.</div>` : ""}`;
      return `<div class="vo${off ? " off" : ""}${ag.has(v.name) ? " against" : ""}${open ? " open" : ""}" data-v="${esc(v.name)}" title="${esc(vt)}"><span class="vn">${esc(v.name)}<small>×${(+v.weight).toFixed(v.weight % 1 ? 1 : 0)}</small>${mb}</span><span class="vb">${bar}</span><span class="vv ${off ? "" : tone(x)}">${off ? "off" : (x > 0 ? "+" : "") + x.toFixed(2)}</span><span class="vt">${esc(vt)}</span>${open ? `<div class="vx">${more}</div>` : ""}</div>`;
    }).join("");
    setHtml(el, `<div class="bh"><span class="eyebrow">Overall analysis</span><span class="r">every voice · tap one for its details</span></div>
      <div class="verdict"><b class="${cls}">${esc(O.verdict || "MIXED")}</b><span class="sc ${tone(sc)}">${sc > 0 ? "+" : ""}${sc.toFixed(2)}</span><span class="cf">confidence ${pct(O.confidence)}${O.agree != null ? ` · agree ${pct(O.agree)}` : ""}</span></div>
      <div class="sbar" title="−1 every voice bearish · +1 every voice bullish"><i style="left:${(sc + 1) * 50}%"></i></div><div class="sax"><span>−1 bearish</span><span>0</span><span>+1 bullish</span></div>
      <div class="dobox">Do: <b class="${actCls(O.do)}">${esc(O.do || "WAIT")}</b>${O.line_state ? ` · line ${esc((LS[O.line_state] || {}).word || O.line_state)}` : ""}</div>
      ${(O.risks || []).length ? `<div class="chips">${O.risks.map((r) => `<span class="chip warn" title="${esc(r)}">⚠ ${esc(r)}</span>`).join("")}</div>` : ""}
      <div class="voices">${voices}</div>
      ${O.text ? `<details class="fold" data-k="ov-text" ${openK["ov-text"] ? "open" : ""}><summary>In words</summary><p class="otext">${esc(O.text)}</p></details>` : ""}
      ${O.note ? `<p class="meta">${esc(O.note)}</p>` : ""}`);
  }
  $("overall").addEventListener("click", (e) => {
    const kv = e.target.closest("button[data-kv]");
    if (kv) { kView = kv.dataset.kv; store.set("kview", kView); $("overall")._h = null; renderOverall(); return; }
    if (e.target.closest(".vx")) return;
    const v = e.target.closest(".vo[data-v]");
    if (!v) return;
    const n = v.dataset.v;
    if (openVo.has(n)) openVo.delete(n); else openVo.add(n);
    renderOverall();
  });

  // ---------------------------------------------------------------- news (state.news, news.py)
  const tsec = (v) => {
    if (v == null || v === "") return null;
    if (typeof v === "number") return v > 1e12 ? v / 1000 : v;
    if (Number.isFinite(+v)) return +v > 1e12 ? +v / 1000 : +v;
    const s = String(v), t = Date.parse(/[zZ]$|[+-]\d\d:?\d\d$/.test(s) ? s : s.replace(" ", "T") + "Z");
    return Number.isFinite(t) ? t / 1000 : null;
  };
  const impCls = (x) => { const s = String(x || "").toLowerCase(); return /high|3/.test(s) ? "high" : /med|2/.test(s) ? "medium" : "low"; };
  const openHl = new Set();
  function renderNews() {
    const el = $("newsBox"), N = S.news;
    if (!N) {
      setHtml(el, `<div class="bh"><span class="eyebrow">News</span><span class="r">off</span></div>
        <p class="meta">The news box is off: the server has no news feed running (news.py). The ICT read still steps aside around the big US releases it knows about${S.ict && S.ict.news ? ", and one is close now" : ""}.</p>`);
      return;
    }
    const st = String(N.status || ""), ok = st.startsWith("ok"), stale = st.startsWith("stale");
    const upd = tsec(N.updated);
    const statusTag = ok ? `<span class="tagp ok">LIVE</span>` : stale ? `<span class="tagp warn">STALE</span>` : `<span class="tagp">OFFLINE</span>`;
    const ne = N.next_event, net = ne ? tsec(ne.time_utc) : null;
    const nxt = ne ? `<div class="nx"><div class="t"><span class="imp ${impCls(ne.impact)}" title="${esc(ne.impact || "")} impact"></span><b>${esc(ne.title || "Event")}</b><span class="dim">${esc(ne.country || "")}${net ? ` · ${clock(net)} your time` : ""}</span>
        <span class="cd" ${net ? `data-cd="${net}"` : ""}>${!net && ne.in_min != null ? `in ${ne.in_min} min` : ""}</span></div>
        ${ne.gold_effect ? `<div class="ge">Gold: ${esc(ne.gold_effect)}</div>` : ""}
        ${ne.forecast != null || ne.previous != null || ne.actual != null ? `<div class="meta num">forecast ${esc(ne.forecast ?? "-")} · previous ${esc(ne.previous ?? "-")} · actual ${esc(ne.actual ?? "-")}</div>` : ""}</div>` : "";
    const b = N.bias != null ? Math.max(-1, Math.min(1, +N.bias)) : null;
    const bias = b != null || N.bias_text ? `<div class="nbias"><b class="${tone(b)}">${arr(b)} ${b != null ? (b > 0 ? "+" : "") + b.toFixed(2) : ""}</b><span>${esc(N.bias_text || "")}</span></div>` : "";
    const hls = (N.headlines || []).slice(0, 5).map((h, i) => {
      const t = tsec(h.time_utc), id = h.url || h.title || String(i), im = +h.impact || 0;
      return `<div class="hl${openHl.has(id) ? " open" : ""}" data-h="${esc(id)}" title="${esc(h.why || "")}"><span class="ar ${im > 0.15 ? "up" : im < -0.15 ? "down" : "dim"}">${im > 0.15 ? "▲" : im < -0.15 ? "▼" : "•"}</span>
        <span><span class="tt" style="${(h.importance ?? 1) < 0.3 ? "color:var(--dim)" : ""}">${esc(h.title || "")}</span>${h.url ? `<a href="${esc(h.url)}" target="_blank" rel="noopener noreferrer" title="Open the source">↗</a>` : ""}
        <span class="src">${esc(h.source || "")}${t ? ` · <span data-age="${t}"></span>` : h.age_min != null ? ` · ${h.age_min} min ago` : ""}${(h.tags || []).length ? ` · ${esc(h.tags.slice(0, 3).join(", "))}` : ""}</span>
        ${h.why ? `<span class="why">${esc(h.why)}</span>` : ""}</span></div>`;
    }).join("");
    const evs = (N.events || []).slice(0, 8).map((e) => { const t = tsec(e.time_utc);
      return `<div class="ev2"><time>${t ? clock(t) : "-"}</time><span class="imp ${impCls(e.impact)}"></span><span title="${esc(e.gold_effect || "")}">${esc(e.country || "")} ${esc(e.title || "")}</span><span class="num dim">${e.actual != null ? `act ${esc(e.actual)}` : e.forecast != null ? `f ${esc(e.forecast)}` : ""}</span></div>`; }).join("");
    setHtml(el, `<div class="bh"><span class="eyebrow">News</span>${statusTag}${N.llm ? `<span class="tagp gold" title="Summary written by a language model from the headlines">AI</span>` : ""}<span class="r">${upd ? `updated <span data-age="${upd}"></span>` : esc(ok || stale ? "" : st)}</span></div>
      ${N.wait ? `<div class="newswait"><b>Wait:</b> ${esc(N.wait_text || "inside a high-impact news window")}</div>` : ""}
      ${!ok && !stale ? `<p class="meta">${esc(st || "offline")}</p>` : ""}
      ${nxt}${bias}
      ${N.summary ? `<p class="say" style="font-size:12.5px;margin:4px 0">${esc(N.summary)}</p>` : ""}
      ${N.analysis ? `<details class="fold" data-k="news-an" ${openK["news-an"] ? "open" : ""}><summary>Analysis for gold</summary><p class="say" style="font-size:12.5px;margin:2px 0 4px">${esc(N.analysis)}</p></details>` : ""}
      ${hls ? `<div style="margin-top:4px">${hls}</div>` : `<p class="meta">No headlines yet.</p>`}
      ${evs ? `<details class="fold" data-k="news-cal" ${openK["news-cal"] ? "open" : ""}><summary>Calendar (${(N.events || []).length})</summary>${evs}</details>` : ""}`);
  }
  $("newsBox").addEventListener("click", (e) => {
    if (e.target.closest("a")) return;
    const h = e.target.closest(".hl[data-h]");
    if (!h) return;
    const id = h.dataset.h;
    if (openHl.has(id)) openHl.delete(id); else openHl.add(id);
    h.classList.toggle("open");
    $("newsBox")._h = null;
  });

  // ---------------------------------------------------------------- candle by candle (state.ict.reads / history)
  const feedTf = () => (tf === "M5" ? "M5" : "M1");
  const openRows = new Set();
  const evLine = (r) => {
    const ev = (r.events || []).filter((e) => e.kind !== "quiet");
    if (!ev.length) return `<span class="dim">${esc(((r.events || [])[0] || {}).text || "quiet")}</span>`;
    return ev.map((e) => (e.kind === "pattern" ? `<em>${esc(e.text)}</em>` : esc(e.text))).join(" · ");
  };
  function renderFeed() {
    const el = $("feed"), I = S.ict;
    if (!I || !I.reads) {
      setHtml(el, `<div class="bh"><span class="eyebrow">Candle by candle</span></div><p class="meta">Every closed 1m and 5m candle, read in ICT terms and as a candlestick, shows here as it closes.</p>`);
      return;
    }
    const newest = ["M1", "M5"].map((t) => I.reads[t]).filter(Boolean).map((r) => {
      const ev = (r.events || []).slice(0, 5);
      return `<div class="rd ${esc(r.tone)}"><div class="rh">${esc(tfName(r.tf))} ${candleClock(r.time)} · ${fmt(r.c)}<span class="tn ${r.tone === "bullish" ? "up" : r.tone === "bearish" ? "down" : "dim"}">${esc(r.tone || "")}</span>${r.pattern ? `<span class="pt">${esc(r.pattern)}</span>` : ""}</div>
        <ul>${ev.map((e) => `<li class="ev ${tone(e.dir)}">${esc(e.text)}</li>`).join("")}</ul></div>`;
    }).join("");
    const ft = feedTf(), hist = ((I.history && I.history[ft]) || []).slice(-15).reverse();
    const rows = hist.map((r) => {
      const id = `${ft}:${r.time}`, ent = !!r.action_dir;
      const ac = ent ? `<span class="ac ent ${r.action_dir > 0 ? "" : "down"}">ENTER ${r.action_dir > 0 ? "BUY" : "SELL"}</span>` : `<span class="ac ${actCls(r.action) === "dim" ? "" : actCls(r.action)}">${esc(r.action || "")}</span>`;
      return `<div class="hr${ent ? " ent" : ""}${openRows.has(id) ? " open" : ""}" data-r="${id}"><time>${candleClock(r.time)}</time><span class="dot ${esc(r.tone)}"></span>
        <span class="evs">${ent && r.model ? `<b>${esc(r.model)}</b> · ` : ""}${evLine(r)}</span>${ac}</div>`;
    }).join("");
    setHtml(el, `<div class="bh"><span class="eyebrow">Candle by candle</span><span class="r">each close, read · ${esc(tfName(ft))} history below</span></div>
      <div class="newest">${newest}</div>
      <div class="hist">${rows || `<p class="meta">No ${esc(tfName(ft))} candles read yet.</p>`}</div>`);
  }
  $("feed").addEventListener("click", (e) => {
    const r = e.target.closest(".hr[data-r]");
    if (!r) return;
    const id = r.dataset.r;
    if (openRows.has(id)) openRows.delete(id); else openRows.add(id);
    r.classList.toggle("open");
    $("feed")._h = null;
  });

  // ---------------------------------------------------------------- day map (state.ict.day_map: the New York day)
  const DMS = { "Asia": "Asia", "Asia late": "Asia", "London open": "LDN open", "London Silver Bullet": "LDN SB", "London expansion": "LDN exp",
    "London-NY transition": "LDN → NY", "NY open": "NY open", "NY AM Silver Bullet": "NY SB", "NY late morning": "NY late", "NY lunch": "Lunch",
    "NY PM open": "PM", "NY PM Silver Bullet": "PM SB", "NY close": "Close", "After hours": "AH", "Daily break": "Break", "Globex open": "Globex" };
  const dmPos = (m) => (((m - 1080) % 1440) + 1440) % 1440 / 1440 * 100;          // the trading day runs 18:00 to 18:00 New York
  let dmSel = null, dmHover = null, dmAll = false;
  function nyNowMin() {
    const c = S.ict && S.ict.clock;
    if (!c || c.minute == null) return null;
    return (c.minute + Math.max(0, Math.floor((chartNow() - S.ict.t) / 60))) % 1440;
  }
  function localOf(min, isNow) {
    const n = nyNowMin();
    if (n == null) return "";
    let d = (((min - n) % 1440) + 1440) % 1440;
    if (isNow) d -= 1440;
    const t = new Date(Date.now() + d * 60000);
    const local = t.getHours() * 60 + t.getMinutes();
    if (local === min % 1440) return "";            // the viewer is on New York time
    return t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  }
  const modelsHtml = (ms) => (ms || []).map((m) => `<span class="chip">${esc(shortName(m.name) || m.model)}</span>`).join("");
  const measHtml = (ms) => (ms || []).map((m) => `<span class="chip ${tone(m.mean_r)}" title="${m.n} setups · won ${m.win_pct != null ? Math.round(m.win_pct) + "%" : "-"} · edge ${m.edge}">${esc(shortName(m.name))} <b>${m.mean_r > 0 ? "+" : ""}${(+m.mean_r).toFixed(2)}R</b>/${m.n}</span>`).join("");
  function dmDetail(s) {
    if (!s) return "";
    const ls = localOf(s.start_min, s.now), le = localOf(s.end_min, s.now && s.end_min > s.start_min ? true : s.now);
    return `<div class="dmd"><div class="nm"><b>${esc(s.name)}</b><span class="num">${esc(s.start)}–${esc(s.end)} NY${ls ? ` · ${ls}–${le} your time` : ""}</span>${s.now ? `<span class="tagp gold">NOW</span>` : ""}${s.avoid ? `<span class="tagp warn">STAND ASIDE</span>` : ""}</div>
      <span class="note">${esc(s.note || "")}</span>
      ${(s.models || []).length ? `<span class="fits"><em>Fits</em>${modelsHtml(s.models)}</span>` : ""}
      <span class="fits"><em>Measured here</em>${(s.measured || []).length ? measHtml(s.measured) : `<span class="meta">no record in this window yet; run playbook_backtest.py</span>`}</span>
      ${(s.worst || []).length ? `<span class="fits"><em>Worst here</em>${measHtml(s.worst)}</span>` : ""}</div>`;
  }
  function renderDayMap() {
    const el = $("dayBox"), dm = S.ict && S.ict.day_map;
    if (!Array.isArray(dm) || !dm.length) {
      setHtml(el, `<div class="bh"><span class="eyebrow">Day map</span></div><p class="meta">Which ICT model fits which part of the New York day shows here once the playbook runs.</p>`);
      return;
    }
    const now = dm.find((s) => s.now), sel = dm.find((s) => s.name === (dmHover || dmSel)) || now || dm[0];
    const n = nyNowMin();
    const segs = dm.map((s, i) => {
      const left = dmPos(s.start_min), w = (s.end_min - s.start_min) / 1440 * 100;
      const kind = s.avoid ? "avoid" : /Silver Bullet/.test(s.name) ? "sb" : /^(London open|NY open|London expansion)$/.test(s.name) ? "kz" : "";
      return `<button class="seg ${kind}${s.now ? " now" : ""}${sel && s.name === sel.name ? " sel" : ""}${w < 6 ? " small" : ""}" data-i="${i}" style="left:${left}%;width:${w}%" title="${esc(`${s.name} ${s.start}-${s.end} NY${s.avoid ? " · stand aside" : ""}`)}"><span>${esc(DMS[s.name] || s.name)}</span></button>`;
    }).join("");
    const ticks = [1080, 1260, 0, 180, 360, 540, 720, 900].map((m) => `<span style="left:${dmPos(m)}%">${String(Math.floor(m / 60)).padStart(2, "0")}</span>`).join("");
    const all = dmAll ? `<div class="allw">${dm.map((s, i) => `<div class="aw${s.now ? " now" : ""}${s.avoid ? " avoid" : ""}" data-i="${i}"><span class="tm">${esc(s.start)}–${esc(s.end)} NY${localOf(s.start_min, s.now) ? `<small>${localOf(s.start_min, s.now)} yours</small>` : ""}</span>
        <span><b>${esc(s.name)}</b>${s.avoid ? ` <span class="tagp warn">STAND ASIDE</span>` : ""}<span class="chips">${modelsHtml(s.models)}${measHtml((s.measured || []).slice(0, 2))}</span></span></div>`).join("")}</div>` : "";
    setHtml(el, `<div class="bh"><span class="eyebrow">Day map · which model fits when</span><span class="r">New York time${n != null ? ` · now ${String(Math.floor(n / 60)).padStart(2, "0")}:${String(n % 60).padStart(2, "0")}` : ""}</span><button class="dmbtn${dmAll ? " on" : ""}" id="dmAll">${dmAll ? "Close list" : "All windows"}</button></div>
      <div class="strip">${segs}${n != null ? `<span class="nowmk" style="left:${dmPos(n)}%"></span>` : ""}</div><div class="ticks">${ticks}</div>
      ${dmDetail(sel)}${all}`);
  }
  $("dayBox").addEventListener("click", (e) => {
    const dm = S && S.ict && S.ict.day_map;
    if (!dm) return;
    if (e.target.closest("#dmAll")) { dmAll = !dmAll; renderDayMap(); return; }
    const s = e.target.closest("[data-i]");
    if (!s) return;
    const name = dm[+s.dataset.i] && dm[+s.dataset.i].name;
    dmSel = s.classList.contains("aw") ? name : dmSel === name ? null : name;
    if (s.classList.contains("aw")) dmAll = false;
    dmHover = null;
    renderDayMap();
  });
  $("dayBox").addEventListener("mouseover", (e) => {
    const s = e.target.closest(".seg[data-i]"), dm = S && S.ict && S.ict.day_map;
    if (!s || !dm || !window.matchMedia("(hover: hover)").matches) return;
    const name = dm[+s.dataset.i].name;
    if (dmHover !== name) { dmHover = name; renderDayMap(); }
  });
  $("dayBox").addEventListener("mouseleave", () => { if (dmHover) { dmHover = null; renderDayMap(); } });

  // ---------------------------------------------------------------- ICT read: the clock, the bias and the four phases
  function smtChip() {
    const sm = S.smt;
    if (!sm) return "";
    const su = sm.summary || {}, fd = sm.feed || {};
    const feed = !fd.source ? `silver feed ${esc(fd.status || "off")}` : `silver ${esc(fd.source)}${fd.delayed ? ` ${fd.delay_min != null ? fd.delay_min + "m " : ""}late` : ""}`;
    return `<span class="chip ${tone(su.state)}" title="${esc(su.note || "")}${fd.symbol ? ` · ${esc(fd.symbol)}` : ""}">SMT ${arr(su.state)} <b>${feed}</b></span>`;
  }
  function renderIctRead() {
    const I = S.ict, el = $("ictRead");
    if (!I) {
      setHtml(el, `<div class="bh"><span class="eyebrow">ICT read</span></div><p class="meta">Reading every timeframe…</p>${S.smt ? `<div class="chips">${smtChip()}</div>` : ""}`);
      return;
    }
    const c = I.clock || {}, closed = !!(S.market && S.market.open === false);
    const now = (S.clock && S.clock.server_time) || I.t, gone = Math.max(0, Math.floor((now - I.t) / 60));
    const nx = (c.next || [])[0], nxIn = nx ? Math.max(0, nx.start_t != null ? Math.ceil((nx.start_t - now) / 60) : nx.in_min - gone) : null;
    const chips = [
      `<span class="chip"><b>NY ${esc(c.ny || "-")}</b></span>`,
      c.killzone && !/lunch/i.test(c.killzone) ? `<span class="chip on">${esc(c.killzone)} killzone</span>` : c.killzone ? "" : `<span class="chip">No killzone</span>`,
      c.silver_bullet ? `<span class="chip on" title="Silver Bullet window">${esc(c.silver_bullet)}</span>` : "",
      c.macro ? `<span class="chip on" title="ICT macro window">${esc(c.macro)}</span>` : "",
      c.lunch ? `<span class="chip bad">NY lunch: stand aside</span>` : "",
      I.news ? `<span class="chip bad">USD news: wait</span>` : "",
      nx && !closed ? `<span class="chip" title="${esc(nx.kind)} ${esc(nx.start)}-${esc(nx.end)} NY">Next ${esc(nx.name)} <b>${nxIn ? `in ${nxIn >= 60 ? `${Math.floor(nxIn / 60)}h ${nxIn % 60}m` : nxIn + " min"}` : "now"}</b></span>` : "",
      c.amd ? `<span class="chip" title="Power of 3: accumulation, manipulation, distribution">Po3: ${esc(c.amd)}</span>` : "",
    ];
    const rg = I.regime || {}, b = Math.max(-1, Math.min(1, +I.bias || 0));
    const regime = `<span class="chip ${rg.kind === "trend" ? tone(rg.dir) : ""}" title="Efficiency ratio ${rg.er ?? "-"}: near 1 = trending, near 0 = ranging">${rg.kind === "trend" ? `Trend ${arr(rg.dir)}` : rg.kind === "range" ? "Range" : "Regime ?"} <b>ER ${rg.er != null ? (+rg.er).toFixed(2) : "-"}</b></span>`;
    const se = I.session;
    const dtext = (I.decision && I.decision.text) || "";
    const phases = ((I.talk || {}).lines || []).filter((l) => !(/^4/.test(l.phase || "") && l.text === dtext)).map((l) => {
      const m = /^(\d)\s+(.*)$/.exec(l.phase || ""), nn = m ? m[1] : "!", nm = m ? m[2] : l.phase;
      return `<span class="ph"><i class="${m ? "" : "x"}">${nn}</i><span><b>${esc(nm || "")}</b>${esc(l.text || "")}</span></span>`;
    }).join("");
    setHtml(el, `<div class="bh"><span class="eyebrow">ICT read${I.price != null ? ` · <span class="num">${fmt(I.price)}</span>` : ""}</span><span class="r">${se ? `${esc(se.name)}${se.avoid ? " · stand aside" : ""}` : ""}</span></div>
      <div class="chips">${chips.join("")}</div>
      <div class="chips">${regime}${smtChip()}</div>
      <div class="ibias"><span>HTF bias</span><span class="bar"><i style="${b >= 0 ? `left:50%;width:${b * 50}%;background:var(--up)` : `right:50%;width:${-b * 50}%;background:var(--down)`}"></i></span><b class="num ${tone(b)}">${b > 0 ? "+" : ""}${b.toFixed(2)}</b>
        ${I.bias_why ? `<span class="why">${esc(I.bias_why)}</span>` : ""}</div>
      ${phases ? `<div class="phases">${phases}</div>` : ""}
      ${S.smt && S.smt.summary && S.smt.summary.note ? `<p class="meta">${esc(S.smt.summary.note)}</p>` : ""}
      ${I.error ? `<p class="meta warnc">${esc(I.error)}</p>` : ""}
      <p class="meta num">Read at ${candleClock(I.t)} chart time${I.ms != null ? ` · ${I.ms} ms` : ""}</p>`);
  }

  // ---------------------------------------------------------------- best model now and every model ranked (no stops or targets)
  const STATUS = { armed: "ARMED", filled: "IN ZONE", target: "REACHED", stopped: "FAILED", invalid: "VOID", expired: "EXPIRED" };
  let rankOpen = null, rankAll = false;
  const rec = (st) => (st && st.n && st.mean_r != null ? `${st.mean_r > 0 ? "+" : ""}${(+st.mean_r).toFixed(2)}R/${st.n}` : "no record");
  function renderBest() {
    const I = S.ict, el = $("ictBest");
    const B = I && I.best, s = B && B.setup, rk = (I && I.ranking) || [];
    el.hidden = !B && !rk.length;
    if (el.hidden) return;
    let body = "";
    if (s) {
      const up = s.dir > 0, z = s.zone || {}, lo = Math.min(z.top, z.bottom), hi = Math.max(z.top, z.bottom), mid = (lo + hi) / 2;
      const tr = s.trigger, en = s.entry_now;
      body = `<div class="sd ${up ? "up" : "down"}">${arr(s.dir)} ${esc(s.side || (up ? "BUY" : "SELL"))} · ${esc(tfName(s.tf))} <span class="stat ${esc(s.status)}">${STATUS[s.status] || esc(String(s.status).toUpperCase())}${s.status === "armed" && s.fill_by ? ` until ${candleClock(s.fill_by)}` : ""}</span></div>
        ${z.top != null ? `<p class="meta">Entry zone <b class="num" style="color:var(--fg)">${fmt(lo)}–${fmt(hi)}</b> · middle ${fmt(mid)}</p>` : ""}
        ${tr ? `<div class="trig"><b class="${en && en.verdict === "ENTER" ? (up ? "up" : "down") : "warnc"}">${esc(en ? en.verdict : "TRIGGER")}</b> · ${esc(tr.text)}${tr.pattern ? ` <span class="dim">(${esc(tr.pattern_meaning || "")})</span>` : ""}${en && (en.missing || []).length ? `<br><span class="warnc">Missing: ${esc(en.missing.join("; "))}</span>` : ""}</div>`
          : `<div class="trig">Entry: a ${esc(tfName(s.tf))} candle that taps the zone and closes back ${up ? "up above" : "down below"} ${fmt(mid)}${s.tf === "M5" ? ", or an M1 CISD inside it" : ""}. Enter on that close, not on the touch.</div>`}
        ${(s.why || []).length ? `<ul class="why2">${s.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
        ${s.invalid_if ? `<p class="meta">Void if ${esc(s.invalid_if)}.</p>` : ""}
        ${ckBlock(`Checklist${s.score != null ? ` · score ${(+s.score).toFixed(2)}` : ""}`, (s.checks || []).map((c) => ({ label: c.label, ok: c.ok, key: c.key })))}`;
    } else if (B) {
      body = `<p class="say" style="font-size:13px">No setup from it yet.${B.waiting_for ? ` Waiting for ${esc(B.waiting_for)}.` : ""}</p>`;
    }
    const list = rankAll ? rk : rk.slice(0, 6);
    const rows = list.map((r, i) => {
      const st = r.stats || {}, ss = r.session_stats || {}, open = rankOpen === r.model, rs = r.setup;
      const g = rs ? gradeHtml(rs.grade, rs.status !== "armed" && rs.status !== "filled") : `<span title="waiting for ${esc(r.waiting_for || "its setup")}">…</span>`;
      const z = rs && rs.zone;
      const more = open ? `<span class="more">
          ${(r.why || []).length ? `<span>${esc(r.why.join(" · "))}</span>` : ""}
          ${rs ? `<span class="v num">${esc(rs.side)} ${esc(tfName(rs.tf))}${z ? ` · zone ${fmt(Math.min(z.top, z.bottom))}–${fmt(Math.max(z.top, z.bottom))}` : ""} · ${STATUS[rs.status] || esc(rs.status)}${rs.trigger ? ` · triggered (${esc((rs.entry_now || {}).verdict || "")})` : ""}</span>` : ""}
          ${r.waiting_for ? `<span>Waiting for ${esc(r.waiting_for)}</span>` : ""}
          <span class="num">Score ${(+r.score).toFixed(2)} · fit ${Math.round((+r.fit || 0) * 100)}% · edge ${(+r.edge || 0).toFixed(3)} · ${st.n_live || 0} live, ${st.n_backtest || 0} backtest${st.win_pct != null ? `, won ${Math.round(st.win_pct)}%` : ""}${st.pending ? `, ${st.pending} open` : ""}</span>
          ${ss.n ? `<span class="num">This window: ${rec(ss)}${ss.win_pct != null ? `, won ${Math.round(ss.win_pct)}%` : ""}</span>` : ""}</span>` : "";
      return `<span class="rk${i === 0 ? " top" : ""}" data-m="${esc(r.model)}" title="${esc(r.waiting_for ? "Waiting for " + r.waiting_for : (r.why || []).join(" · "))}">
        <span class="n">${rk.indexOf(r) + 1}</span><span class="nm"><span>${esc(r.name)}</span><span class="sb"><i style="width:${Math.round(Math.max(0, Math.min(1, +r.score || 0)) * 100)}%"></i></span></span>
        <span class="f">${Math.round((+r.fit || 0) * 100)}%</span><span class="rc ${st.n ? tone(st.mean_r) : ""}">${rec(st)}</span><span class="g">${g}</span>${more}</span>`;
    }).join("");
    setHtml(el, `<div class="bh"><span class="eyebrow">Best model now</span><span class="r">${s ? esc(tfName(s.tf)) + " · " : ""}not proven</span></div>
      ${B ? `<div class="mname">${esc(B.name)} ${s ? gradeHtml(s.grade) : ""}</div>${(B.why || []).length ? `<p class="meta">${esc(B.why.join(" · "))}</p>` : ""}` : ""}
      ${body}
      ${rk.length ? `<div class="rank"><b>Every model, best fit first</b><span class="rkhead"><span></span><span>Model · score</span><span>Fit</span><span>Record</span><span>Setup</span></span>${rows}
        ${rk.length > 6 ? `<button class="rkall">${rankAll ? "Show the top 6" : `All ${rk.length} models`}</button>` : ""}</div>` : ""}`);
  }

  // ---------------------------------------------------------------- timeframes, top-down
  function renderTopDown() {
    const I = S.ict, el = $("ictTf"), T = (I && I.timeframes) || {}, tfs = TF6.filter((t) => T[t]);
    el.hidden = !tfs.length;
    if (!tfs.length) return;
    setHtml(el, `<div class="bh"><span class="eyebrow">Timeframes top-down</span><span class="r">tap a row to open its chart</span></div>
      <div>${tfs.map((t) => {
        const x = T[t], lb = x.last_break, r = x.range, ph = x.phase, cls = x.trend > 0 ? "up" : x.trend < 0 ? "down" : "flat";
        const zone = r && r.zone ? `<span class="zp ${esc(r.zone)}">${esc(String(r.zone).toUpperCase())}${r.pos != null ? ` ${Math.round(r.pos * 100)}%` : ""}</span>` : "";
        const brk = lb ? `<span class="${lb.dir > 0 ? "up" : "down"}">${esc(lb.kind)} ${arr(lb.dir)} ${fmt(lb.level)}</span><span>${esc(lb.ago || "")}</span>` : "<span>no break yet</span>";
        const smt = x.smt && x.smt.state && x.smt.note ? `<span class="smt">${esc(x.smt.note)}</span>` : "";
        return `<span class="tr${t === tf ? " here" : ""}"${TFSEC[t] ? ` data-tf="${t}" title="Open the ${tfName(t)} chart"` : ""}>
          <span class="tf">${tfName(t)}</span><span class="role">${esc(x.role || "")}</span><span class="rd ${cls}">${arr(x.trend)} ${esc(String(x.label || "mixed").toUpperCase())}</span>
          <span class="ln">${brk}${zone}${ph && ph.from ? `<span>${esc(ph.from)}→${esc(ph.to)}</span>` : ""}</span>
          ${x.do ? `<span class="do">${esc(x.do)}</span>` : ""}
          ${ph && ph.text ? `<span class="ph2">${esc(ph.text)}</span>` : ""}${smt}</span>`;
      }).join("")}</div>
      <p class="meta">Read top-down: 1D and 4h give the bias, 1h the draw, 15m the setup, 5m and 1m the entry.</p>`);
  }
  document.querySelector("aside").addEventListener("click", (e) => {
    const t = e.target.closest(".tr[data-tf]");
    if (t) { setTf(t.dataset.tf); if (window.innerWidth <= 900) wrap.scrollIntoView({ behavior: "smooth", block: "start" }); return; }
    if (e.target.closest(".rkall")) { rankAll = !rankAll; renderBest(); return; }
    const r = e.target.closest(".rk[data-m]");
    if (r) { rankOpen = rankOpen === r.dataset.m ? null : r.dataset.m; renderBest(); return; }
  });

  // ---------------------------------------------------------------- the chips on the chart: timeframes, the line, SMC
  function renderChartChips() {
    const I = S.ict, T = (I && I.timeframes) || {}, pill = $("tfRead");
    pill.hidden = !I || !Object.keys(T).length;
    if (!pill.hidden) {
      const b = +I.bias || 0;
      setHtml(pill, TF6.filter((t) => T[t]).map((t) => `<span class="tfa">${tfName(t)}<b class="${tone(T[t].trend)}">${arr(T[t].trend)}</b></span>`).join("")
        + `<b class="${tone(b)}" title="${esc(I.bias_why || "")}">HTF ${b > 0 ? "+" : ""}${b.toFixed(2)}</b>`);
    }
    const L = curLine || (I && LINE_TF[tf] ? I.line : null);
    lineChip.hidden = !L || !LINE_TF[tf];
    if (!lineChip.hidden) {
      const st = lineStyleOf(L);
      lineChip.className = "chip0 linechip " + String(L.state || "").replace(/ /g, "_");
      setHtml(lineChip, `<i></i><b>LINE</b>${arr(L.dir)} ${esc(st.word)} · ${sgn(L.move)} in 30m <span class="dim">ⓘ</span>`);
      lineChip.title = "How the line is made: tap for the three voices and their weights";
    }
    const m = S.smc, sr = $("smcRead");
    sr.hidden = !ovl.smc || !m || !m.summary;
    if (!sr.hidden) setHtml(sr, `<b class="${tone(m.bias)}">${arr(m.bias)} SMC ${esc(tfName(m.tf || ""))}</b> ${esc(m.summary)}`);
  }

  // ---------------------------------------------------------------- more: the Kronos card and the live scoreboard
  function miniChart(f) {
    if (!f || !Array.isArray(f.path) || !f.path.length) return "";
    const W = 300, H = 92, P = 4;
    const ts = [f.t, ...f.path.map((p) => p.time)];
    const band = new Map((f.band || []).map((b) => [b.time, b]));
    const act = new Map((f.actual || []).map((a) => [a.time, a.value]));
    const vals = [f.last, ...f.path.map((p) => p.value), ...(f.actual || []).map((a) => a.value), ...(f.band || []).flatMap((b) => [b.lo, b.hi])].filter(Number.isFinite);
    const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
    const X = (i) => P + (i * (W - 2 * P)) / (ts.length - 1), Y = (v) => P + ((hi - v) * (H - 2 * P)) / span;
    const line = (pts) => pts.map(([i, v], n) => `${n ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join("");
    const area = (a, b) => {
      const top = ts.map((t, i) => [i, i ? (band.get(t) || {})[b] : f.last]).filter((p) => Number.isFinite(p[1]));
      const bot = ts.map((t, i) => [i, i ? (band.get(t) || {})[a] : f.last]).filter((p) => Number.isFinite(p[1])).reverse();
      return top.length > 1 ? `<path d="${line(top)}${line(bot).replace("M", "L")}Z" fill="${C.gold}" fill-opacity="${a === "lo" ? .12 : .22}"/>` : "";
    };
    const fpts = [[0, f.last], ...f.path.map((p, i) => [i + 1, p.value])];
    const apts = [[0, f.last], ...ts.slice(1).map((t, i) => [i + 1, act.get(t)]).filter((p) => Number.isFinite(p[1]))];
    return `<svg class="mini" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Last scored Kronos forecast against what price did">
      ${area("lo", "hi")}${area("p25", "p75")}
      <path d="${line(fpts)}" fill="none" stroke="${C.gold}" stroke-width="1.6" stroke-dasharray="4 3" vector-effect="non-scaling-stroke"/>
      ${apts.length > 1 ? `<path d="${line(apts)}" fill="none" stroke="#ece8df" stroke-width="1.6" vector-effect="non-scaling-stroke"/>` : ""}
    </svg>`;
  }
  function btHtml(bt) {
    if (!bt) return "";
    if (bt.status !== "done") return `<div class="bt"><b>Gold backtest · running</b><span class="num">${esc(bt.progress || "starting")}</span></div>`;
    return `<div class="bt"><b>Gold backtest</b>${(bt.lines || []).filter((l) => !/^Boom\/Crash/.test(l)).map((l) => /^Verdict/.test(l) ? `<span class="v">${esc(l)}</span>` : `<span>${esc(l)}</span>`).join("")}</div>`;
  }
  let kView = store.get("kview", "short");
  function kronosCardHtml() {
    const k = S.kronos;
    if (!k) {
      return `<div class="bh"><span class="eyebrow">Kronos forecast</span><span class="r">off</span></div>
        <p class="meta">Start Gold Desk with --kronos to add Kronos: its 30-minute forecast is one of the line's three voices; its 24 h forecast is the thin dotted line on the 1h chart.</p>`;
    }
    const tfS = k.tf || S.entry_tf || "M5";
    const f = kView === "day" ? k.day : (k.path ? k : null);
    const tr = (k.track || {})[kView === "day" ? "H1" : tfS];
    const tabs = `<span class="kv" role="tablist"><button data-kv="short" class="${kView !== "day" ? "on" : ""}">${k.path ? "Next " + horizon(k.minutes) : "Short"}</button><button data-kv="day" class="${kView === "day" ? "on" : ""}">Next 24 h</button></span>`;
    if (!f) {
      return `<div class="bh"><span class="eyebrow">Kronos forecast</span><span class="r">${tabs}</span></div><div class="kcall dim">${k.status === "off" ? "Off" : "Loading"}</div>
        <p class="meta">${esc(k.error || (k.status === "loading model" ? "Loading the model. The first run downloads it." :
          kView === "day" ? "The 24 h forecast runs once an hour. The first one appears after the next hour closes." : "Waiting for the next closed candle."))}</p>${btHtml(k.backtest)}`;
    }
    const d = f.dir, up = f.up_prob, rg = f.range;
    return `<div class="bh"><span class="eyebrow">Kronos forecast</span><span class="r">${tabs}</span></div>
      <div class="kcall ${tone(d)}">${d > 0 ? "▲ UP" : d < 0 ? "▼ DOWN" : "— FLAT"} <span class="num" style="font-size:14px">${fmt(f.target)} (${sgn(f.move)})</span></div>
      ${up != null ? `<div class="odds"><span>Up <b class="up">${pct(up)}</b></span><span>Down <b class="down">${pct(1 - up)}</b></span><span>Volatility jump <b>${pct(f.vol_amp_prob)}</b></span></div>
      <div class="split"><i style="width:${Math.round(up * 100)}%"></i></div>` : ""}
      ${rg ? `<p class="meta num">${f.samples ? `${f.samples} paths · ` : ""}Ends between ${fmt(rg.lo)} and ${fmt(rg.hi)} · middle half ${fmt(rg.p25)} to ${fmt(rg.p75)}</p>` : ""}
      ${tr ? `<div class="bt"><b>Forecast vs actual${tr.last ? ` · from ${clock(tr.last.t)}` : ""}</b>
        ${tr.last ? miniChart(tr.last) + `<span class="mlegend"><span><i class="f"></i>forecast</span><span><i></i>actual</span><span><i class="s"></i>path spread</span></span>` : `<span>No forecast has finished yet.</span>`}
        <span class="v">${tr.resolved ? `Direction right ${tr.direction_right} of ${tr.resolved} (${Math.round(tr.direction_pct)}%) · ended inside the range ${tr.inside_range} of ${tr.resolved}` : "Nothing scored yet"}${tr.waiting ? ` · ${tr.waiting} waiting` : ""}</span></div>` : ""}
      <p class="meta">${kView === "day" ? "The thin dotted line on the 1h chart." : "Its 30-minute forecast is a voice in the line on 1m-15m; it is not drawn on its own."}${k.backtest && k.backtest.status === "done" ? "" : " Not proven on gold yet."}${k.error ? " " + esc(k.error) : ""}</p>${btHtml(k.backtest)}`;
  }
  const MAIN = ["Desk line", "Trend line", "Higher timeframes", "Intraday structure", "ICT order flow", "Kronos", "Always up", "Last 30 min"];
  let meshOpen = false;
  const FEAT = { st: "Structure", reg: "EMA trend", turtle: "Turtle soup", cisd: "CISD", fvg: "FVG", ob: "Order block", pd: "Premium / discount",
    judas: "Judas swing", sweep: "Liquidity sweep", mss: "MSS", bos: "BOS", choch: "CHoCH", ote: "OTE", kz: "Killzone", asia: "Asian range" };
  const feat = (n) => { const m = /^([a-z]+)(?:_([A-Z]\d+))?$/.exec(n); return m && FEAT[m[1]] ? `${FEAT[m[1]]}${m[2] ? " " + tfName(m[2]) : ""}` : n; };
  function renderMesh() {
    const m = S.mesh, el = $("mesh");
    el.hidden = !m || !Array.isArray(m.sources);
    if (el.hidden) return;
    const hs = (m.horizons || [30, 60, 120]).map(String), tr = m.trust || {};
    const cell = (v) => {
      if (!v || !v.n) return `<td>-</td>`;
      const r = v.right, c = v.beats_coin ? (r > 0.5 ? "up" : "down") : "";
      return `<td class="${c}" title="${v.n} checks · a coin lands within ±${Math.round((v.coin_band || 0) * 100)}%">${Math.round(r * 100)}%<small>${v.n}</small></td>`;
    };
    const row = (s) => `<tr><td class="${/^(Always up|Last 30 min)$/.test(s.name) ? "base" : ""}" title="${esc(s.name)}">${esc(feat(s.name))}${tr[s.name] != null ? ` <small>×${tr[s.name]}</small>` : ""}</td>${hs.map((h) => cell((s.by_h || {})[h])).join("")}</tr>`;
    const head = `<tr><th>Right after</th>${hs.map((h) => `<th>${horizon(+h)}</th>`).join("")}</tr>`;
    const main = MAIN.map((n) => m.sources.find((s) => s.name === n)).filter(Boolean), rest = m.sources.filter((s) => !MAIN.includes(s.name));
    const earned = Object.keys(tr).length;
    setHtml(el, `<div class="bh"><span class="eyebrow">Live scoreboard</span><span class="r">${earned ? "a group earned weight" : "nothing proven yet"}</span></div>
      <table>${head}${main.map(row).join("")}</table>
      ${rest.length ? `<details class="fold" data-k="mesh-all" ${meshOpen ? "open" : ""}><summary>Every concept on its own (${rest.length})</summary><table>${head}${rest.map(row).join("")}</table></details>` : ""}
      <p class="meta">${m.since ? `Since ${new Date(m.since * 1000).toLocaleDateString([], { day: "numeric", month: "short" })} · ` : ""}${m.rows ? `${m.rows.toLocaleString("en-US")} candles · ` : ""}Green or red = beyond a coin flip so far; with this many rows a few colour by luck.${m.error ? " " + esc(m.error) : ""}</p>
      ${m.note ? `<p class="meta">${esc(m.note)}</p>` : ""}`);
    const d = el.querySelector("details");
    if (d) d.addEventListener("toggle", () => { meshOpen = d.open; });
  }

  // ---------------------------------------------------------------- header and the whole page
  function renderHeader() {
    const a = S.account || {}, src = S.source, pill = $("srcPill");
    pill.className = "pill src " + (src === "mt5" ? "mt5" : src === "demo" ? "demo" : "");
    pill.textContent = src === "mt5" ? "MT5 CHART" : src === "demo" ? "DEMO PRICES · NOT MT5" : `${String(src || "?").toUpperCase()} FEED`;
    pill.title = src === "demo" ? "Synthetic prices for trying the page; start Gold Desk with MT5 for your real chart."
      : [`Chart and prices from ${src === "mt5" ? "MT5" : src}`, a.server, a.company, a.mode && a.mode !== "unknown" ? `${a.mode} account` : "", a.login ? `#${a.login}` : ""].filter(Boolean).join(" · ");
    if (S.symbol) $("sym").textContent = S.symbol;
    if (!Q && S.tick) showQuote(S.tick.bid, S.tick.ask);
    const issues = [];
    if (S.error && !(S.market && S.error === S.market.note)) issues.push(S.error);
    $("banner").hidden = !issues.length;
    $("banner").textContent = issues.join("  ");
  }
  function render() {
    renderHeader();
    renderTodo();
    renderForming();
    renderPositions();
    renderOverall();
    renderNews();
    renderFeed();
    renderDayMap();
    renderIctRead();
    renderBest();
    renderTopDown();
    renderMesh();
    renderPush();
    drawMarkers();
    drawForecast();
    drawPosLines();
    renderChartChips();
    drawZones();
    tick();
  }

  let refreshing = false;
  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    try {
      S = await get("/api/state");
      render();
      if (loadedTf !== tf) loadCandles();
    } catch (e) {
      $("banner").hidden = false;
      $("banner").textContent = S ? "Gold Desk server is not answering. Start it again in Terminal." : "Gold Desk server is not running. Start it again in Terminal.";
      if (S) console.error(e);
    }
    refreshing = false;
  }

  refresh().then(loadCandles);
  connectLive();
  setInterval(refresh, 1000);
  setInterval(syncTail, 3000);
})();
