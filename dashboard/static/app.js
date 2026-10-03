/* Gold Desk: live chart, one-click BUY / SELL, open trades, close all. Prices stream in; orders go out on click. */
(() => {
  "use strict";
  const TOKEN = document.querySelector('meta[name="dash-token"]').content;
  const $ = (id) => document.getElementById(id);
  const TFSEC = { M1: 60, M5: 300, M15: 900, H1: 3600, H4: 14400 };
  const C = { buy: "#2f7bf5", sell: "#e5533c", up: "#2fb67c", down: "#e5484d", gold: "#d6ad52", dim: "#8e8a80", line: "#262a2f", bg: "#0e0f11" };
  const store = {
    get(k, d) { try { const v = localStorage.getItem("gd3_" + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("gd3_" + k, JSON.stringify(v)); } catch { /* storage blocked */ } },
  };

  let S = null;            // latest /api/state
  let Q = null;            // latest streamed quote
  let tf = store.get("tf5", "M5");          // M5 is the main entry timeframe
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
  let bandRange = null;              // the shading's lowest / highest price, so the price scale keeps the whole band in view
  const forecast = chart.addLineSeries({ color: C.gold, lineWidth: 2, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    autoscaleInfoProvider: (orig) => { const r = orig(); if (!bandRange) return r;
      const lo = Math.min(bandRange[0], r ? r.priceRange.minValue : Infinity), hi = Math.max(bandRange[1], r ? r.priceRange.maxValue : -Infinity);
      return { priceRange: { minValue: lo, maxValue: hi } }; } });
  let last = null, loadedTf = null, first = 0, times = [];
  const ovl = Object.assign({ kronos: true, scalper: false, boom: false, smc: true, flow: true }, store.get("ovl3", {}));   // signals off by default: the measured line comes first   // SMC/ICT covers the scalper's zones

  async function loadCandles() {
    const want = tf;
    const rows = await get(`/api/candles?tf=${want}&count=1200`).catch(() => null);
    if (!Array.isArray(rows) || want !== tf) return;
    series.setData(rows);
    if (rows.length > 160) chart.timeScale().setVisibleLogicalRange({ from: rows.length - 150, to: rows.length + 28 });   // recent bars, room for the forecast
    loadedTf = want;
    first = rows.length ? rows[0].time : 0;
    times = rows.map((r) => r.time);
    last = rows.length ? { ...rows[rows.length - 1] } : null;
    markerKey = ""; fcKey = "";
    drawMarkers(); drawForecast(); requestAnimationFrame(drawZones);
  }

  // live candle from the streamed bid, the same way the broker's chart builds it
  function liveCandle(bid) {
    if (!last || loadedTf !== tf || !S) return;
    const sec = TFSEC[tf];
    const now = Date.now() / 1000 + ((S.clock && S.clock.utc_offset_h) || 0) * 3600;
    const bucket = now - (now % sec);
    if (bucket > last.time && times[times.length - 1] < Math.floor(bucket)) times.push(Math.floor(bucket));
    if (bucket > last.time) last = { time: Math.floor(bucket), open: last.close, high: Math.max(last.close, bid), low: Math.min(last.close, bid), close: bid };
    else { last.high = Math.max(last.high, bid); last.low = Math.min(last.low, bid); last.close = bid; }
    series.update(last);
    if (tf === "M1") { drawForecast(); if (Date.now() - zonesAt > 1000) { zonesAt = Date.now(); requestAnimationFrame(drawZones); } }
  }
  let zonesAt = 0;

  // server copy of the bar every few seconds (fixes any tick the stream missed)
  async function syncTail() {
    const want = tf;
    const rows = await get(`/api/candles?tf=${want}&count=3`).catch(() => null);
    if (!Array.isArray(rows) || want !== tf || loadedTf !== want) return;
    for (const r of rows) if (!last || r.time >= last.time) {
      series.update(r); last = { ...r };
      if (!times.length || times[times.length - 1] < r.time) times.push(r.time);
    }
  }

  let markerKey = "";
  function drawMarkers() {
    if (!S || loadedTf !== tf) return;
    const sec = TFSEC[tf];
    const list = ovl.scalper ? (S.trades || []).slice(-40) : [];
    if (ovl.scalper && S.active) list.push(S.active);
    const m = list.map((t) => ({
      time: t.t_bar - (t.t_bar % sec), position: t.dir === 1 ? "belowBar" : "aboveBar",
      color: t.dir === 1 ? C.buy : C.sell, shape: t.dir === 1 ? "arrowUp" : "arrowDown", text: "",
    }));
    // Boom/Crash calls: entered on the bar after the forecast bar (t); finished ones show their result in R
    const bm = S.boom, esec = TFSEC[(bm && bm.tf) || S.entry_tf] || 300;
    if (ovl.boom && bm) {
      for (const b of (bm.history || []).slice(-12).concat(bm.active ? [bm.active] : [])) {
        const at = b.t + esec, up = b.dir === 1 || b.side === "BUY";
        m.push({ time: Math.min(at - (at % sec), last ? last.time : at), position: up ? "belowBar" : "aboveBar",
          color: up ? C.up : C.down, shape: up ? "arrowUp" : "arrowDown",
          text: b.r != null && b.exit != null ? `${b.r > 0 ? "+" : ""}${(+b.r).toFixed(1)}R` : "" });   // the arrow is the call, the text its result
      }
    }
    const merged = new Map();                      // same bar and side: one arrow, results joined
    for (const x of m.filter((x) => x.time >= first)) {
      const k = x.time + x.position, had = merged.get(k);
      if (!had) merged.set(k, x);
      else if (x.text) had.text = had.text ? `${had.text} ${x.text}` : x.text;
    }
    m.splice(0, m.length, ...[...merged.values()].sort((a, b) => a.time - b.time));
    const key = tf + JSON.stringify(m.map((x) => [x.time, x.text]));
    if (key !== markerKey) { series.setMarkers(m); markerKey = key; }
  }

  // Kronos forecasts: state.kronos is the entry-timeframe (M5) one, state.kronos.day the 24 h H1 one. The 1m and 5m charts
  // show the M5 forecast, the 1h chart the 24 h one: average path as a dashed line, spread of the sample paths as shading.
  // The gold line: on 1m-15m the trend reading (state.consensus: timeframes + ICT order flow + Kronos, 2 h), with
  // Kronos's own spread as the shading when it has one; on 1h the Kronos 24 h forecast.
  function chartForecast() {
    const k = S && S.kronos, c = S && S.consensus;
    const ok = (f) => f && Array.isArray(f.path) && f.path.length ? f : null;
    if (tf === "H1") return ok(k && k.day);
    if (tf !== "M1" && tf !== "M5" && tf !== "M15") return null;
    if (ok(c)) { const f = tf === "M1" ? liveLine(c) : c; return { ...f, band: Array.isArray(f.band) && f.band.length ? f.band : lineBand(f, k), mix: true }; }
    return tf === "M15" ? null : ok(k);
  }
  // On 1m the gold line looks 10 minutes ahead, one point per candle. The reading is redone at every candle close;
  // while a candle is forming the line starts from the live price, so it stays attached to the chart.
  const LIVE_MIN = 10;
  function liveLine(c) {
    const lv = c.live, now0 = last && loadedTf === "M1" ? last : null;
    if (lv && Array.isArray(lv.path) && lv.path.length)       // the backend's own 10-minute forecast, when it sends one
      return { ...c, ...lv, target: lv.path[lv.path.length - 1].value, last: now0 && now0.time >= lv.t ? now0.close : lv.last, live: true };
    const pts = [[c.t, +c.last], ...c.path.map((p) => [p.time, p.value])].sort((a, b) => a[0] - b[0]);
    const at = (x) => {
      for (let i = 1; i < pts.length; i++) if (pts[i][0] >= x) {
        const [t0, v0] = pts[i - 1], [t1, v1] = pts[i];
        return t1 === t0 ? v1 : v0 + (v1 - v0) * (x - t0) / (t1 - t0);
      }
      return pts[pts.length - 1][1];
    };
    const path = [];
    for (let m = 1; m <= LIVE_MIN; m++) path.push({ time: c.t + m * 60, value: +at(c.t + m * 60).toFixed(2) });
    const now = last && loadedTf === "M1" && last.time >= c.t ? last.close : null;
    return { ...c, path, minutes: LIVE_MIN, target: path[path.length - 1].value, last: now ?? c.last, live: true };
  }
  const candleClock = (sec) => new Date(sec * 1000).toISOString().slice(11, 16);
  // Shading for the trend line, the same two tones as Kronos's: around the line, as wide as Kronos's own sample
  // spread when a fresh Kronos run has one, else as wide as gold's usual M1 swing for that many minutes ahead.
  function lineBand(c, k) {
    const kb = k && Array.isArray(k.band) && k.path && Math.abs((k.t || 0) - c.t) < 1800 ? new Map(k.band.map((b) => [b.time - (b.time % 300), b])) : null;
    const kp = kb ? new Map(k.path.map((p) => [p.time - (p.time % 300), p.value])) : null;
    const atr = +c.atr || 0;
    return c.path.map((p) => {
      const b = kb && kb.get(p.time - (p.time % 300)), mid = kp && kp.get(p.time - (p.time % 300));
      const w = b && mid != null ? { lo: mid - b.lo, hi: b.hi - mid, p25: mid - b.p25, p75: b.p75 - mid } : null;
      const sd = atr * 0.8 * Math.sqrt(Math.max(1, (p.time - c.t) / 60 + 1));     // random-walk spread in M1 ATRs
      const d = w || { lo: 1.64 * sd, hi: 1.64 * sd, p25: 0.67 * sd, p75: 0.67 * sd };
      return { time: p.time, lo: p.value - d.lo, hi: p.value + d.hi, p25: p.value - d.p25, p75: p.value + d.p75 };
    });
  }
  let fcKey = "";
  function drawForecast() {
    const k = chartForecast();
    const ok = !!(ovl.kronos && loadedTf === tf && k && last);
    const key = ok ? `${tf}${k.t}${k.mix ? "c" : "k"}${k.target}${k.live ? k.last : ""}` : "";
    if (key === fcKey) return;
    fcKey = key;
    bandRange = ok && Array.isArray(k.band) && k.band.length ? [Math.min(...k.band.map((b) => b.lo)), Math.max(...k.band.map((b) => b.hi))] : null;
    if (!ok) { forecast.setData([]); return; }
    const sec = TFSEC[tf], start = k.t - (k.t % sec), byBar = new Map([[start, k.last]]);
    for (const p of k.path) { const b = p.time - (p.time % sec); if (b >= start) byBar.set(b, p.value); }
    forecast.setData([...byBar].sort((a, b) => a[0] - b[0]).map(([time, value]) => ({ time, value })));
  }

  // horizontal levels: [price, color, short name, line style, label priority]. The chart draws the line and the price on
  // the axis; the short name is a tag at the right edge, placed by the label engine so tags never sit on top of each other.
  function levelList() {
    const want = [];
    for (const p of S.positions || []) {
      want.push([p.open, p.side === "BUY" ? C.buy : C.sell, `${p.side === "BUY" ? "B" : "S"} ${(+p.volume).toFixed(2)}`, 0, 100]);
      if (p.sl) want.push([p.sl, C.down, "SL", 2, 95]);
      if (p.tp) want.push([p.tp, C.up, "TP", 2, 95]);
    }
    const kf = chartForecast();
    if (ovl.kronos && kf && Number.isFinite(+kf.target))
      want.push([+kf.target, C.gold, `${kf.mix ? "TREND" : "K"} ${kf.dir > 0 ? "▲" : kf.dir < 0 ? "▼" : "•"} ${horizon(kf.minutes).replace(" ", "")}`, 2, 70]);
    const ba = S.boom && S.boom.active;
    if (ovl.boom && ba) {
      const n = (ba.dir === 1 || ba.side === "BUY") ? "BOOM" : "CRASH";
      want.push([ba.entry, C.gold, `${n} in`, 0, 85]);
      want.push([ba.sl, C.down, `${n} SL`, 2, 85]);
      want.push([ba.tp, C.up, `${n} TP`, 2, 85]);
    }
    if (ovl.scalper) {
      const a = S.active;
      if (a) {
        want.push([a.entry, C.dim, `SIG ${a.side === "BUY" ? "▲" : "▼"}`, 1, 60]);
        if (a.sl) want.push([a.sl, C.down, "SIG SL", 1, 60]);
        if (a.tp1) want.push([a.tp1, C.up, "TP1", 1, 60]);
        if (a.tp2) want.push([a.tp2, C.up, "TP2", 1, 60]);
      }
      const c = S.context;
      if (c && c.sw_h) want.push([c.sw_h, "#6f6a60", "M15 HI", 3, 20]);
      if (c && c.sw_l) want.push([c.sw_l, "#6f6a60", "M15 LO", 3, 20]);
    }
    return want;
  }
  let lines = [], linesKey = "";
  function drawLines() {
    const want = levelList().map(([price, color, , lineStyle]) => [price, color, lineStyle]);
    const key = JSON.stringify(want);
    if (key === linesKey) return;
    linesKey = key;
    lines.forEach((l) => series.removePriceLine(l));
    lines = want.map(([price, color, lineStyle]) => series.createPriceLine({ price, color, title: "", lineStyle, lineWidth: 1, axisLabelVisible: true }));
  }

  // ---------------------------------------------------------------- chart text
  // Every word on the chart goes through here: a short tag on a dark pill, 11px, one size and weight everywhere.
  // Tags are queued while shapes are drawn, then placed most important first. A tag that would cover another one
  // moves up or down a little (your trades go first and may move furthest); if there is no room it is left out.
  const FONT = "600 11px JetBrains Mono, ui-monospace, Menlo, monospace", TH = 16, PADX = 4;
  let tags = [];
  const tag = (txt, x, y, col, o) => tags.push({ txt, x, y, col, align: (o && o.align) || "left", pri: (o && o.pri) || 10,
    edge: !!(o && o.edge), must: !!(o && o.must), faint: !!(o && o.faint) });
  function flushTags(right, H) {
    zx.font = FONT; zx.textBaseline = "middle";
    const placed = [];
    const hit = (a) => placed.some((b) => a.x0 < b.x1 + 3 && a.x1 + 3 > b.x0 && a.y0 < b.y1 + 1 && a.y1 + 1 > b.y0);
    for (const t of tags.sort((a, b) => b.pri - a.pri)) {
      const w = zx.measureText(t.txt).width + PADX * 2;
      let x0 = t.align === "right" ? t.x - w : t.align === "center" ? t.x - w / 2 : t.x;
      x0 = Math.max(2, Math.min(right - w - 2, x0));
      let box = null;
      const steps = t.must ? 8 : t.edge ? 3 : 1;     // your trades may move further to find a free spot
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
      zx.strokeStyle = t.col; zx.globalAlpha = t.faint ? .3 : .55; zx.lineWidth = 1; zx.stroke(); zx.globalAlpha = 1;
      zx.fillStyle = t.col; zx.globalAlpha = t.faint ? .6 : 1;
      zx.fillText(t.txt, box.x0 + PADX, box.yc + .5); zx.globalAlpha = 1;
    }
    zx.textBaseline = "alphabetic";
    tags = [];
  }

  // scalper FVG / order-block zones: boxes from the bar they formed to the right edge, drawn on a canvas over the chart
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
    if (ovl.smc) drawSmc(right, sec, ts);
    if (ovl.flow) drawFlow(right, sec, ts);
    if (ovl.boom) { drawWatch(right, sec, ts); drawHeadsUp(right, sec, ts); }
    drawBand(right, sec, ts);
    if (ovl.scalper) drawScalperZones(right, sec, ts);
    for (const [price, col, name, , pri] of levelList()) {        // names of the horizontal levels, at the right edge
      const yy = series.priceToCoordinate(price);
      if (yy != null) tag(name, right - 4, yy, col, { align: "right", pri, edge: true, must: pri >= 95 });
    }
    flushTags(right, r.height);
  }
  function drawScalperZones(right, sec, ts) {
    for (const z of S.zones || []) {
      const b = z.born - (z.born % sec);
      if (b > last.time) continue;
      let x = b < first ? 0 : ts.timeToCoordinate(b);
      if (x == null) continue;
      x = Math.max(0, x);
      const y1 = series.priceToCoordinate(z.top), y2 = series.priceToCoordinate(z.bottom);
      if (y1 == null || y2 == null || x >= right) continue;
      const col = z.dir === 1 ? "47,123,245" : "229,83,60";
      zx.fillStyle = `rgba(${col},.13)`;
      zx.fillRect(x, y1, right - x, Math.max(1, y2 - y1));
      zx.strokeStyle = `rgba(${col},.45)`;
      zx.lineWidth = 1;
      zx.strokeRect(x + .5, y1 + .5, right - x - 1, Math.max(1, y2 - y1) - 1);
      tag(z.kind, x + 4, y1 + TH / 2 + 1, `rgb(${col})`, { pri: 35 });
    }
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

  // Smart Money / ICT layer, from state.smc (all times on the candle clock, all optional):
  //  killzones [{name, start, end, high, low}]          session windows, shaded columns with their range
  //  pd {high, low, eq, from_time}                        dealing range: premium (top half) / discount (bottom half)
  //  ote [{dir, top, bottom, from_time}]                  optimal trade entry box (62-79% retracement)
  //  fvg [{dir, top, bottom, from_time, to_time, kind}]   fair value gaps (kind FVG / IFVG / BPR); to_time = filled
  //  ob  [{dir, top, bottom, from_time, to_time, kind}]   order blocks (kind OB / BB / MB)
  //  liquidity [{price, kind, from_time, to_time, swept}] EQH / EQL / BSL / SSL pools; swept ones fade
  //  structure [{kind, dir, price, from_time, time}]      BOS / CHoCH: the broken swing level, from swing to break
  //  swings [{time, price, kind}]                         HH / HL / LH / LL labels
  //  levels [{price, label, time}]                        PDH / PDL / PWH / PWL / midnight open ...
  const KZ = { Asia: "120,110,230", London: "47,123,245", "NY AM": "214,173,82", "NY PM": "214,120,82", "NY Lunch": "142,138,128" };
  // keep the chart readable: the newest few of each kind, open ones before used ones, nearest liquidity to price
  function trimSmc(m) {
    const px = last ? last.close : null;
    const recent = (a, n) => (a || []).slice(-n);
    const openFirst = (a, nOpen, nUsed) => [...(a || []).filter((z) => z.to_time).slice(-nUsed), ...(a || []).filter((z) => !z.to_time).slice(-nOpen)];
    const liq = (m.liquidity || []);
    const near = (side) => liq.filter((l) => !l.swept && px != null && (side > 0 ? l.price >= px : l.price < px))
      .sort((a, b) => Math.abs(a.price - px) - Math.abs(b.price - px)).slice(0, 2);
    return { ...m, killzones: recent(m.killzones, 4), fvg: openFirst(m.fvg, 4, 1), ob: openFirst(m.ob, 3, 1), structure: recent(m.structure, 2),
      swings: recent(m.swings, 4), ote: recent(m.ote, 1), liquidity: px == null ? recent(liq, 4) : [...near(1), ...near(-1), ...liq.filter((l) => l.swept).slice(-1)] };
  }
  function drawSmc(right, sec, ts) {
    const m = S && S.smc && trimSmc(S.smc);
    if (!m) return;
    const y = (v) => series.priceToCoordinate(v);
    const X = (t) => (t == null ? right : Math.min(right, xOf(t, ts, sec) ?? right));
    const H = zc.getBoundingClientRect().height;
    const lab = right - 4;                         // level names stack at the right edge with the trade tags
    const hline = (x1, x2, yy, col, dash) => {
      zx.beginPath(); zx.setLineDash(dash || []); zx.strokeStyle = col; zx.lineWidth = 1;
      zx.moveTo(x1, Math.round(yy) + .5); zx.lineTo(x2, Math.round(yy) + .5); zx.stroke(); zx.setLineDash([]);
    };
    const box = (b, rgb, a, txt) => {
      const x1 = X(b.from_time), x2 = X(b.to_time), y1 = y(b.top), y2 = y(b.bottom);
      if (x1 == null || y1 == null || y2 == null || x2 <= x1) return;
      zx.fillStyle = `rgba(${rgb},${a})`; zx.fillRect(x1, y1, x2 - x1, Math.max(1, y2 - y1));
      zx.strokeStyle = `rgba(${rgb},${a * 3})`; zx.strokeRect(x1 + .5, y1 + .5, x2 - x1 - 1, Math.max(1, y2 - y1) - 1);
      if (txt) tag(txt, x1 + 3, y1 + TH / 2 + 1, `rgb(${rgb})`, { pri: b.to_time ? 32 : 40, faint: !!b.to_time });
    };
    if (sec <= 900) for (const k of m.killzones || []) {            // sessions only make sense on 1m-15m
      const x1 = X(k.start), x2 = X(k.end), rgb = KZ[k.name] || "142,138,128";
      if (x1 == null || x2 <= x1) continue;
      zx.fillStyle = `rgba(${rgb},.06)`; zx.fillRect(x1, 0, x2 - x1, H);
      tag(k.name, Math.max(x1 + 3, 64), H - 40, `rgb(${rgb})`, { pri: 15 });
      if (k.high && k.low) { hline(x1, x2, y(k.high), `rgba(${rgb},.6)`, [2, 2]); hline(x1, x2, y(k.low), `rgba(${rgb},.6)`, [2, 2]); }
    }
    const pd = m.pd;
    if (pd && pd.high && pd.low) {
      const x1 = X(pd.from_time) ?? 0, eq = pd.eq ?? (pd.high + pd.low) / 2, yh = y(pd.high), ye = y(eq), yl = y(pd.low);
      if (yh != null && ye != null && yl != null) {
        zx.fillStyle = "rgba(229,72,77,.05)"; zx.fillRect(x1, yh, right - x1, ye - yh);
        zx.fillStyle = "rgba(47,182,124,.05)"; zx.fillRect(x1, ye, right - x1, yl - ye);
        hline(x1, right, ye, "rgba(236,232,223,.35)", [6, 4]);
        tag("PREM", lab, yh + TH / 2 + 1, "rgb(229,72,77)", { align: "right", pri: 25, edge: true, faint: true });
        tag("EQ", lab, ye, "rgb(236,232,223)", { align: "right", pri: 26, edge: true, faint: true });
        tag("DISC", lab, yl - TH / 2 - 1, "rgb(47,182,124)", { align: "right", pri: 25, edge: true, faint: true });
      }
    }
    for (const o of m.ote || []) box(o, "214,173,82", .08, "OTE");
    for (const g of m.fvg || []) box(g, g.dir === 1 ? "47,182,124" : "229,72,77", g.to_time ? .04 : .09, g.kind || "FVG");
    for (const o of m.ob || []) box(o, o.dir === 1 ? "47,123,245" : "229,83,60", o.to_time ? .05 : .12, o.kind || "OB");
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
      tag(w.kind, xx, yy + (/H$/.test(w.kind) ? -TH / 2 - 3 : TH / 2 + 3), "rgb(190,186,176)", { align: "center", pri: 30 });
    }
    for (const l of m.levels || []) {
      const x1 = l.time ? X(l.time) : 0, yy = y(l.price);
      if (yy == null) continue;
      hline(x1, right, yy, "rgba(236,232,223,.4)", [8, 4]);
      tag(l.label, lab, yy, "rgb(236,232,223)", { align: "right", pri: 45, edge: true });
    }
  }

  // the latest "setup likely soon" heads-up while it is live: its watch area as a dashed box up to when it's due
  function liveHeadsUp() {
    const al = S && S.alerts, h = al && al.recent && al.recent[al.recent.length - 1];
    if (!h || !h.area) return null;
    const now = (S.clock && S.clock.server_time) || Date.now() / 1000;
    const due = h.t + (h.minutes || 15) * 60;
    return now <= due + 15 * 60 ? { ...h, due, now } : null;
  }
  function drawHeadsUp(right, sec, ts) {
    const h = liveHeadsUp();
    if (!h) return;
    const x1 = Math.min(right, xOf(h.t, ts, sec) ?? right), x2 = Math.min(right, xOf(h.due, ts, sec) ?? right);
    const y1 = series.priceToCoordinate(h.area[1]), y2 = series.priceToCoordinate(h.area[0]);
    if (y1 == null || y2 == null) return;
    const rgb = h.dir === 1 || h.side === "BUY" ? "47,182,124" : "229,72,77";
    const w = Math.max(6, x2 - x1), hh = Math.max(3, y2 - y1);
    zx.fillStyle = `rgba(${rgb},.14)`; zx.fillRect(x1, y1, w, hh);
    zx.setLineDash([5, 3]); zx.strokeStyle = `rgba(${rgb},.9)`; zx.lineWidth = 1.5; zx.strokeRect(x1 + .5, y1 + .5, w - 1, hh - 1); zx.setLineDash([]);
    tag(`WATCH ${h.side === "BUY" || h.dir === 1 ? "▲" : "▼"}`, x1 + 2, y1 - TH / 2 - 2, `rgb(${rgb})`, { pri: 75 });
  }

  // Boom / Crash setups past their CHoCH, waiting for price to come back into the zone (state.boom.watch)
  function drawWatch(right, sec, ts) {
    for (const w of (S.boom && S.boom.watch) || []) {
      if (!w.poi || w.poi.length < 2) continue;
      const x1 = Math.min(right, xOf(w.since, ts, sec) ?? right), x2 = Math.min(right, xOf(w.until, ts, sec) ?? right);
      const y1 = series.priceToCoordinate(Math.max(...w.poi)), y2 = series.priceToCoordinate(Math.min(...w.poi));
      if (y1 == null || y2 == null || x2 - x1 < 2) continue;
      const rgb = w.dir === 1 ? "47,182,124" : "229,72,77";
      zx.fillStyle = `rgba(${rgb},.07)`; zx.fillRect(x1, y1, x2 - x1, Math.max(2, y2 - y1));
      zx.setLineDash([2, 3]); zx.strokeStyle = `rgba(${rgb},.55)`; zx.lineWidth = 1;
      zx.strokeRect(x1 + .5, y1 + .5, x2 - x1 - 1, Math.max(2, y2 - y1) - 1); zx.setLineDash([]);
      if (w.entry != null) {                            // a limit order waiting in the gap: dash at its entry
        const ye = series.priceToCoordinate(w.entry);
        if (ye != null) { zx.strokeStyle = `rgba(${rgb},.95)`; zx.lineWidth = 1.5; zx.beginPath(); zx.moveTo(x1, Math.round(ye) + .5); zx.lineTo(x2, Math.round(ye) + .5); zx.stroke(); }
      }
      tag(w.entry != null ? `${w.dir === 1 ? "BUY" : "SELL"} LIMIT` : `SETUP ${w.dir === 1 ? "▲" : "▼"}`, x1 + 3, y2 + TH / 2 + 2, `rgb(${rgb})`, { pri: 65, faint: w.entry == null });
    }
  }

  // M1 order flow (state.flow): liquidity raids, CISD levels and the limit-order gaps they led to. Shown on 1m-5m.
  function drawFlow(right, sec, ts) {
    const f = S.flow;
    if (!f || sec > 300) return;
    const a = S.boom && S.boom.active, y = (v) => series.priceToCoordinate(v);
    const X = (t) => { const x = xOf(t, ts, sec); return x == null ? null : Math.min(right, x); };
    for (const r of (f.raids || []).slice(-6)) {
      const x = X(r.time), yy = y(r.ext), yl = y(r.price);
      if (x == null || yy == null || x >= right) continue;
      const col = r.dir === 1 ? "47,182,124" : "229,72,77";
      if (yl != null) { zx.setLineDash([1, 2]); zx.strokeStyle = `rgba(${col},.6)`; zx.beginPath(); zx.moveTo(x - 14, Math.round(yl) + .5); zx.lineTo(x + 6, Math.round(yl) + .5); zx.stroke(); zx.setLineDash([]); }
      zx.fillStyle = `rgba(${col},.95)`; zx.beginPath(); zx.arc(x, yy, 2.5, 0, 7); zx.fill();
      tag(`$ ${r.name || "raid"}`, x, yy + (r.dir === 1 ? TH / 2 + 4 : -TH / 2 - 4), `rgb(${col})`, { align: "center", pri: 48 });
    }
    for (const c of (f.cisd || []).slice(-6)) {
      const x = X(c.time), yy = y(c.price);
      if (x == null || yy == null || x >= right) continue;
      const x0 = Math.max(0, x - 8 * ts.options().barSpacing);
      zx.setLineDash([3, 2]); zx.strokeStyle = "rgba(232,178,58,.85)"; zx.lineWidth = 1;
      zx.beginPath(); zx.moveTo(x0, Math.round(yy) + .5); zx.lineTo(x, Math.round(yy) + .5); zx.stroke(); zx.setLineDash([]);
      tag("CISD", x0, yy + (c.dir === 1 ? -TH / 2 - 1 : TH / 2 + 1), "rgb(232,178,58)", { pri: 46 });
    }
    for (const o of (f.orders || []).slice(-6)) {
      if (a && a.status === "waiting" && a.dir === o.dir && Math.abs(a.entry - o.entry) < 0.01) continue;   // drawn as the live order
      const x1 = X(o.time), x2 = X(o.time + 30 * 60), y1 = y(Math.max(...o.gap)), y2 = y(Math.min(...o.gap)), ye = y(o.entry);
      if (x1 == null || x2 == null || y1 == null || y2 == null || x2 - x1 < 2) continue;
      const col = o.dir === 1 ? "47,182,124" : "229,72,77";
      zx.strokeStyle = `rgba(${col},.5)`; zx.lineWidth = 1; zx.strokeRect(x1 + .5, y1 + .5, x2 - x1 - 1, Math.max(2, y2 - y1) - 1);
      if (ye != null) { zx.strokeStyle = `rgba(${col},.9)`; zx.beginPath(); zx.moveTo(x1, Math.round(ye) + .5); zx.lineTo(x2, Math.round(ye) + .5); zx.stroke(); }
      tag(`CE ${o.dir === 1 ? "▲" : "▼"}`, x2 + 2, ye ?? y1, `rgb(${col})`, { pri: 42, faint: true });
    }
  }

  // the Kronos sample-path spread: outer shade = lowest to highest path, inner shade = middle half (p25 to p75)
  function drawBand(right, sec, ts) {
    const k = chartForecast();
    if (!ovl.kronos || !k || !Array.isArray(k.band) || !k.band.length) return;
    const start = k.t - (k.t % sec), byBar = new Map([[start, { lo: k.last, hi: k.last, p25: k.last, p75: k.last }]]);
    for (const b of k.band) byBar.set(b.time - (b.time % sec), b);
    const pts = [...byBar].sort((a, b) => a[0] - b[0]).map(([t, b]) => ({ x: ts.timeToCoordinate(t), b })).filter((p) => p.x != null && p.x <= right);
    if (pts.length < 2) return;
    const y = (v) => series.priceToCoordinate(v);
    const shade = (lo, hi, fill) => {
      zx.beginPath();
      pts.forEach((p, i) => (i ? zx.lineTo(p.x, y(p.b[hi])) : zx.moveTo(p.x, y(p.b[hi]))));
      for (let i = pts.length - 1; i >= 0; i--) zx.lineTo(pts[i].x, y(pts[i].b[lo]));
      zx.closePath(); zx.fillStyle = fill; zx.fill();
    };
    shade("lo", "hi", k.live ? "rgba(214,173,82,.13)" : "rgba(214,173,82,.10)");
    shade("p25", "p75", k.live ? "rgba(214,173,82,.26)" : "rgba(214,173,82,.20)");
    if (k.live) {                      // the measured range: dotted edges and where they end
      zx.save(); zx.setLineDash([2, 3]); zx.strokeStyle = "rgba(214,173,82,.75)"; zx.lineWidth = 1;
      for (const e of ["lo", "hi"]) { zx.beginPath(); pts.forEach((p, i) => (i ? zx.lineTo(p.x, y(p.b[e])) : zx.moveTo(p.x, y(p.b[e])))); zx.stroke(); }
      zx.restore();
      const end = pts[pts.length - 1];
      tag(`HI ${fmt(end.b.hi)}`, end.x + 4, y(end.b.hi), C.gold, { pri: 66, faint: true });
      tag(`LO ${fmt(end.b.lo)}`, end.x + 4, y(end.b.lo), C.gold, { pri: 66, faint: true });
    }
  }
  chart.timeScale().subscribeVisibleLogicalRangeChange(() => requestAnimationFrame(drawZones));
  new ResizeObserver(() => requestAnimationFrame(drawZones)).observe(zc);
  const placeReads = () => { document.querySelector(".reads").style.top = (12 + document.querySelector(".chartbar").offsetHeight + 8) + "px"; };
  new ResizeObserver(placeReads).observe(document.querySelector(".chartbar"));

  document.querySelectorAll("#ovl button").forEach((b) => {
    b.classList.toggle("on", !!ovl[b.dataset.o]);
    b.addEventListener("click", () => {
      ovl[b.dataset.o] = !ovl[b.dataset.o]; store.set("ovl3", ovl);
      b.classList.toggle("on", ovl[b.dataset.o]);
      markerKey = ""; fcKey = "x";
      if (S) { drawMarkers(); drawLines(); drawForecast(); drawZones(); renderSmcRead(); renderTrend(); renderDesks(); renderBoom(); renderScalper(); }
    });
  });

  document.querySelectorAll("#tfs button").forEach((b) => {
    b.classList.toggle("on", b.dataset.tf === tf);
    b.addEventListener("click", () => {
      tf = b.dataset.tf; store.set("tf5", tf);
      document.querySelectorAll("#tfs button").forEach((x) => x.classList.toggle("on", x === b));
      loadCandles();
    });
  });

  // ---------------------------------------------------------------- "?" cheat sheet: what every word on the chart means
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
  $("helpTop").addEventListener("click", () => showLegend(true));
  $("legendClose").addEventListener("click", () => showLegend(false));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") showLegend(false); });

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
  // Buy / Sell work only with fresh prices and a connected broker page (state.caps.trading, state.broker)
  function tradeState() {
    const live = Date.now() - lastQuoteAt < 15000;
    const br = (S && S.broker) || { connected: true, message: null };
    const capOk = !(S && S.caps && S.caps.trading === false);
    const mk = S && S.market;
    if (mk && mk.open === false && !live) {         // gold's daily break or the weekend: closed, not broken
      const at = mk.reopens ? new Date(mk.reopens * 1000) : null;
      const when = at ? (mk.why === "weekend" ? at.toLocaleDateString([], { weekday: "short" }) + " " : "") + clock(mk.reopens) : null;
      return { ok: false, live, head: "Market closed", closed: true,
        why: `${mk.why === "weekend" ? "Weekend" : "Gold's daily break"}${when ? ` · opens ${when}` : ""}. Prices start again by themselves.` };
    }
    if (S && (!br.connected || !capOk)) return { ok: false, live, head: "Broker offline", why: br.message || "The broker page is not connected." };
    if (!live) return { ok: false, live, head: lastQuoteAt ? "Prices stopped" : "Connecting", why: lastQuoteAt ? "No new prices for 15 seconds." : "Waiting for the first price." };
    return { ok: true, live, head: "Live prices", why: "" };
  }
  function paintTrade() {
    const t = tradeState();
    $("conn").classList.toggle("on", t.ok);
    $("connTxt").textContent = t.head;
    $("conn").title = t.why;
    $("buy").disabled = $("sell").disabled = !t.ok;
    $("ticketWrap").classList.toggle("off", !t.ok);
    const off = $("tradeOff");
    off.hidden = t.ok || (!S && !lastQuoteAt);
    off.classList.toggle("closed", !!t.closed);
    if (!off.hidden) off.innerHTML = t.closed ? `<b>Market closed.</b> ${esc(t.why)}` : `<b>Buy and Sell are off.</b> ${esc(t.why)}${last ? " The chart still updates." : ""}`;
    const want = t.ok ? "ready" : t.closed ? "closed" : "off";
    if (resultIdle && idleShown !== want && (S || lastQuoteAt)) {
      idleShown = want;
      if (t.ok) result("idle", "Ready to trade", "Click SELL or BUY. The order goes out at once, with no confirm box.", true);
      else if (t.closed) result("wait", "Market closed", "Buy and Sell come back when prices return.", true);
      else result("wait", "Waiting for the broker", "Buy and Sell come back by themselves once prices flow again.", true);
    }
    if (!t.live && last && !(S && S.tick)) {          // no live quote: show the last candle price, greyed out
      $("bid").innerHTML = bigPx(last.close); $("ask").innerHTML = "-"; $("spr").textContent = "-";
    }
  }
  setInterval(paintTrade, 500);

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
  const ICON = { ok: "✓", bad: "!", wait: "…", idle: "✓", warn: "?" };
  let resultIdle = true, idleShown = "";
  function result(kind, what, sub, idle) {
    resultIdle = !!idle;
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
    // pressed but no trade seen yet: it may still have opened, so never invite a second click
    if (r.status === "unconfirmed") result("warn", `${side} ${lots.toFixed(2)} not confirmed`,
      `Check Open trades below before clicking again. It may have opened.${r.message ? " · " + r.message : ""}`);
    else if (r.ok) result("ok", `${side} ${lots.toFixed(2)} sent${r.price ? " at " + fmt(r.price) : ""}`,
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
    const ba = S.boom && S.boom.active;
    const lv = u.dataset.src === "boom" ? ba && { sl: ba.sl, tp: ba.tp } : S.active && { sl: S.active.sl, tp: S.active.tp2 };
    if (!lv) return;
    $("sl").value = lv.sl ? fmt(lv.sl) : ""; $("tp").value = lv.tp ? fmt(lv.tp) : "";
    $("protect").open = true;
    sltpSummary();
  });

  // ---------------------------------------------------------------- signals
  // Kronos arrives as state.kronos: {status, error, and once it has forecast: t, last, target, move, atr, dir, call, minutes, model, path}
  // state.kronos.backtest: {status: "running"|"done", lines: ["Direction right: ...", "Trades ...", "Verdict: ..."], progress}
  function btHtml(bt) {
    if (!bt) return "";
    if (bt.status !== "done") return `<div class="bt"><b>Gold backtest · running</b><span class="num">${esc(bt.progress || "starting")}</span></div>`;
    return `<div class="bt"><b>Gold backtest</b>${(bt.lines || []).map((l) => {
      return /^Verdict/.test(l) ? `<span class="v">${esc(l)}</span>` : `<span>${esc(l)}</span>`;
    }).join("")}</div>`;
  }
  const horizon = (m) => (!m ? "" : m >= 1440 && m % 1440 === 0 ? `${m / 1440 === 1 ? "24 h" : m / 1440 + " d"}` : m >= 120 ? `${Math.round(m / 60)} h` : `${m} min`);
  const pct = (x) => (x == null || !Number.isFinite(+x) ? "-" : Math.round(x * 100) + "%");
  let kView = store.get("kview", "short");
  $("kronos").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-kv]");
    if (!b) return;
    kView = b.dataset.kv; store.set("kview", kView); renderKronos();
  });

  // forecast vs what happened, for the last forecast that has been scored: shaded spread, dashed forecast, solid actual
  function miniChart(f) {
    if (!f || !Array.isArray(f.path) || !f.path.length) return "";
    const W = 300, H = 92, P = 4;
    const ts = [f.t, ...f.path.map((p) => p.time)];
    const band = new Map((f.band || []).map((b) => [b.time, b]));
    const act = new Map((f.actual || []).map((a) => [a.time, a.value]));
    const vals = [f.last, ...f.path.map((p) => p.value), ...(f.actual || []).map((a) => a.value),
      ...(f.band || []).flatMap((b) => [b.lo, b.hi])].filter(Number.isFinite);
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

  // state.kronos: M5 forecast {t, last, target, move, dir, call, minutes, samples, up_prob, vol_amp_prob, band, range, path},
  // plus day (the same for the next 24 h on H1), track {M5|H1: {resolved, direction_right, inside_range, waiting, last}}, backtest
  function renderKronos() {
    const k = S.kronos, el = $("kronos");
    if (!k) {
      el.innerHTML = `<span class="name">Kronos forecast</span><span></span><span class="call flat">Off</span><span></span>
        <span class="meta">Start Gold Desk with --kronos to see its up or down call here.</span>`;
      return;
    }
    const tfS = k.tf || S.entry_tf || "M5";
    const f = kView === "day" ? k.day : (k.path ? k : null);
    const tr = (k.track || {})[kView === "day" ? "H1" : tfS];
    const tabs = `<span class="kv" role="tablist"><button data-kv="short" class="${kView !== "day" ? "on" : ""}">${k.path ? "Next " + horizon(k.minutes) : "Short"}</button><button data-kv="day" class="${kView === "day" ? "on" : ""}">Next 24 h</button></span>`;
    if (!f) {
      el.innerHTML = `<span class="name">Kronos forecast</span>${tabs}<span class="call flat">${k.status === "off" ? "Off" : "Loading"}</span><span></span>
        <span class="meta">${esc(k.error || (k.status === "loading model" ? "Loading the model. The first run downloads it." :
          kView === "day" ? "The 24 h forecast runs once an hour. The first one appears after the next hour closes." : "Waiting for the next closed candle."))}</span>${btHtml(k.backtest)}`;
      return;
    }
    const d = f.dir, up = f.up_prob;
    const rg = f.range;
    el.innerHTML = `<span class="name">Kronos</span>${tabs}
      <span class="call ${d > 0 ? "up" : d < 0 ? "down" : "flat"}">${d > 0 ? "▲ UP" : d < 0 ? "▼ DOWN" : "— FLAT"} <span class="num" style="font-size:14px">${fmt(f.target)} (${f.move >= 0 ? "+" : ""}${fmt(f.move)})</span></span><span></span>
      ${up != null ? `<span class="odds"><span>Up <b class="up">${pct(up)}</b></span><span>Down <b class="down">${pct(1 - up)}</b></span><span>Volatility jump <b>${pct(f.vol_amp_prob)}</b></span></span>
      <span class="split"><i style="width:${Math.round(up * 100)}%"></i></span>` : ""}
      ${rg ? `<span class="meta num">${f.samples ? `${f.samples} paths · ` : ""}Ends between ${fmt(rg.lo)} and ${fmt(rg.hi)} · middle half ${fmt(rg.p25)} to ${fmt(rg.p75)}</span>` : ""}
      ${tr ? `<div class="bt"><b>Forecast vs actual${tr.last ? ` · from ${clock(tr.last.t)}` : ""}</b>
        ${tr.last ? miniChart(tr.last) + `<span class="mlegend"><span><i class="f"></i>forecast</span><span><i class="a"></i>actual</span><span><i class="s"></i>path spread</span></span>` : `<span>No forecast has finished yet.</span>`}
        <span class="v">${tr.resolved ? `Direction right ${tr.direction_right} of ${tr.resolved} (${Math.round(tr.direction_pct)}%) · ended inside the range ${tr.inside_range} of ${tr.resolved}` : "Nothing scored yet"}${tr.waiting ? ` · ${tr.waiting} waiting` : ""}</span></div>` : ""}
      <span class="meta">Gold dashed line and shading on the ${kView === "day" ? "1h" : "1m and 5m"} chart.${k.backtest && k.backtest.status === "done" ? "" : " Not proven on gold yet."}${k.error ? " " + esc(k.error) : ""}</span>${btHtml(k.backtest)}`;
  }
  // state.boom: {active: {kind, side, dir, entry, sl, tp, move, move_atr, minutes, expires, strong, why[]} | null,
  //              history: [...with exit, how, r, usd_001], stats: {calls, wins, losses, net_r, net_usd_001}, proven}
  function renderBoom() {
    const bm = S.boom, el = $("boom");
    el.hidden = !bm || !ovl.boom;
    if (!bm) return;
    const st = bm.stats || {}, a = bm.active;
    const tally = st.calls
      ? `${st.calls} call${st.calls === 1 ? "" : "s"} · ${st.wins}W ${st.losses}L · <span class="${tone(st.net_r)}">${st.net_r > 0 ? "+" : ""}${(+st.net_r).toFixed(1)}R</span> · <span class="${tone(st.net_usd_001)}">${signed(st.net_usd_001)}</span> per 0.01 lot`
      : "No calls yet";
    const tag = `<span class="untested">${bm.proven ? "TESTED" : "UNTESTED"}</span>`;
    el.className = "card boom" + (a ? (a.dir === 1 || a.side === "BUY" ? " live up" : " live down") : "");
    if (!a) {
      const h = (bm.history || [])[bm.history.length - 1];
      const w = (bm.watch || [])[0];
      el.innerHTML = `<span class="name">Boom / Crash</span>${tag}<span class="call flat">${w ? `Watching ${w.dir === 1 ? "▲ BUY" : "▼ SELL"}` : "Waiting"}</span><span></span>
        ${w ? `<span class="meta">${w.dir === 1 ? "Swept a low, CHoCH up" : "Swept a high, CHoCH down"} through ${fmt(w.choch)}. Waiting for price back into <b class="num">${fmt(w.poi[0])}-${fmt(w.poi[1])}</b>${w.until ? ` until ${clock(w.until)}` : ""}.</span>` : ""}
        <span class="meta">${h ? `Last: ${esc(h.kind)} ${esc(h.side)} ${h.how === "target" ? "hit target" : h.how === "stop" ? "hit stop" : "timed out"}, ${h.r > 0 ? "+" : ""}${(+h.r).toFixed(2)}R · ` : ""}${tally}</span>`;
      return;
    }
    const up = a.dir === 1 || a.side === "BUY";
    const now = (S.clock && S.clock.server_time) || Date.now() / 1000;
    const waiting = a.status === "waiting";
    const left = Math.max(0, Math.ceil(((waiting && a.fill_by ? a.fill_by : a.expires) - now) / 60));
    const mins = left >= 60 ? `${Math.floor(left / 60)} h ${left % 60}` : left;
    el.innerHTML = `<span class="name">${up ? "BOOM" : "CRASH"}${bm.tf ? " · " + esc(bm.tf.replace(/^M(\d+)$/, "$1m")) : ""}${a.strong ? " · strong" : ""}</span>${tag}
      <span class="call ${up ? "up" : "down"}">${up ? "▲ BUY" : "▼ SELL"}${waiting ? " LIMIT" : a.order === "limit" ? " · filled" : ""} <span class="num" style="font-size:14px">@ ${fmt(a.entry)}</span></span>
      <button class="use" data-src="boom">Use SL/TP</button>
      <span class="lvls num"><span>SL <b class="down">${fmt(a.sl)}</b></span><span>TP <b class="up">${fmt(a.tp)}</b></span><span>${waiting ? `valid <b>${mins}</b> more min` : `<b>${mins}</b> min left`}</span></span>
      ${waiting ? `<span class="meta">A limit order idea: place it yourself at ${fmt(a.entry)} if you agree. It is void if price doesn't come back in time.</span>` : ""}
      ${a.why && a.why.length ? `<span class="meta">${a.why.map(esc).join(" · ")}</span>` : ""}
      ${a.move ? `<span class="meta">Kronos ${a.move > 0 ? "+" : ""}${fmt(a.move)}${a.up_prob != null ? ` · up ${pct(a.up_prob)}` : ""}</span>` : ""}
      <span class="meta">${tally}</span>`;
  }
  // state.alerts: {push, topic, sent, error, recent: [{kind, side, dir, t, minutes, area, what, why, up_prob, title, text}]}
  function renderHeadsUp() {
    const al = S.alerts, el = $("heads");
    el.hidden = !al;
    if (!al) return;
    const h = liveHeadsUp(), last = (al.recent || [])[al.recent.length - 1];
    const push = al.error ? `<span class="down">Phone push failed: ${esc(al.error)}</span>`
      : al.push ? `Phone push on${al.sent ? ` · ${al.sent} sent` : ""}` : "Phone push off";
    if (!h) {
      el.className = "heads";
      el.innerHTML = `<span class="hd">HEADS-UP</span><span class="ht dim">${last ? `Last: ${esc(last.side)} near ${fmt(last.area[0])}-${fmt(last.area[1])} at ${clock(last.t)}` : "Nothing expected right now."}</span><span class="hp">${push}</span>`;
      return;
    }
    const up = h.dir === 1 || h.side === "BUY", left = Math.max(0, Math.round((h.due - h.now) / 60));
    el.className = "heads live " + (up ? "up" : "down");
    el.innerHTML = `<span class="hd">${up ? "▲" : "▼"} ${esc(h.side)} SETUP ${left ? `IN ~${left} MIN` : "DUE NOW"}</span>
      <span class="ht">Watch <b class="num">${fmt(h.area[0])}-${fmt(h.area[1])}</b>${h.what ? ` · ${esc(h.what)}` : ""}${h.up_prob != null ? ` · Kronos up ${Math.round(h.up_prob * 100)}%` : ""}</span>
      ${h.why && h.why.length ? `<span class="ht dim">${esc(h.why.join(", "))}</span>` : ""}
      <span class="hp">${push} · a heads-up, not a trade signal</span>`;
  }
  // state.consensus: {score -1..1, bias, label, tf {M1..D1: +1|0|-1}, parts [{name, score, weight}], note, kronos, proven}
  const TF6 = ["D1", "H4", "H1", "M15", "M5", "M1"];
  const tfName = (t) => t.replace(/^M(\d+)$/, "$1m").replace(/^H(\d+)$/, "$1h").replace(/^D1$/, "1D");
  const arrow = (v) => v > 0 ? `<b class="up">▲</b>` : v < 0 ? `<b class="down">▼</b>` : `<b>•</b>`;
  // state.timeframes: each timeframe's own desk (D1 bias down to M1 trigger), read top-down like ICT does
  const deskDir = (t) => { const d = S.timeframes && S.timeframes[t]; return d ? d.bias : ((S.consensus && S.consensus.tf) || {})[t]; };
  function renderDesks() {
    const ds = S.timeframes || {}, el = $("desks"), rows = TF6.filter((t) => ds[t]);
    el.hidden = !rows.length;
    if (el.hidden) return;
    el.innerHTML = `<span class="name">Top-down</span><span class="untested">NOT PROVEN</span>
      <span class="rows">${rows.map((t) => { const d = ds[t], w = (d.why || [])[0], cls = d.bias > 0 ? "up" : d.bias < 0 ? "down" : "";
        const sc = `${d.score > 0 ? "+" : ""}${(+d.score).toFixed(2)}`;
        return `<span class="row${t === tf ? " here" : ""}"><span class="tf">${tfName(t)}</span><span class="role">${esc(d.role || "")}</span>
          <span class="rd ${cls}">${d.bias > 0 ? "▲" : d.bias < 0 ? "▼" : "•"} ${esc((d.label || "mixed").toUpperCase())} ${sc}</span>
          <span class="why">${w ? esc(w.text) : "Nothing strong right now"}${d.with_above === false ? ` · <em>against ${tfName(d.above)}</em>` : ""}</span></span>`; }).join("")}</span>
      <span class="meta">Each timeframe reads its own ICT concepts. ICT takes a lower timeframe's signal only when it sides with the one above it. The weights are the textbook, not fitted.</span>`;
  }
  function renderTrend() {
    const c0 = S.consensus, el = $("trend"), pill = $("tfRead");
    el.hidden = !c0; pill.hidden = !c0 || !ovl.kronos;
    if (!c0) return;
    const c = tf === "M1" && Array.isArray(c0.path) && c0.path.length ? liveLine(c0) : c0;
    const sc = `${c.score > 0 ? "+" : ""}${(+c.score).toFixed(2)}`, cls = c.bias > 0 ? "up" : c.bias < 0 ? "down" : "flat";
    const word = (c.label || "mixed").toUpperCase();
    pill.innerHTML = TF6.map((t) => `<span class="tfa">${tfName(t)}${arrow(deskDir(t))}</span>`).join("") +
      `<b class="${cls === "flat" ? "" : cls}">${c.bias > 0 ? "▲" : c.bias < 0 ? "▼" : "•"} ${word} ${sc}</b>`;
    const lb = Array.isArray(c.band) && c.band.length ? c.band : lineBand(c, S.kronos), le = lb[lb.length - 1];
    if (c.live && c.up_prob != null && le) {
      const up = Math.round(+c.up_prob * 100);
      el.innerHTML = `<span class="name">Next 10 minutes</span><span class="untested">RANGE CHECKED</span>
        <span class="call" style="grid-column:1 / -1;color:var(--fg)"><span class="num" style="white-space:nowrap">${fmt(le.lo)} – ${fmt(le.hi)}</span></span>
        <span class="meta num">Middle half ${fmt(le.p25)} – ${fmt(le.p75)} · line ends ${fmt(c.target)} (${c.target - c.last >= 0 ? "+" : ""}${fmt(c.target - c.last)})</span>
        <span class="odds"><span>Up <b class="up">${up}%</b></span><span>Down <b class="down">${100 - up}%</b></span></span>
        <span class="split"><i style="width:${up}%"></i></span>
        <span class="tfs6">${TF6.map((t) => `<span>${tfName(t)}${arrow(deskDir(t))}</span>`).join("")}</span>
        <span class="parts">${(c0.parts || []).map((p) => { const v = Math.max(-1, Math.min(1, +p.score || 0));
          return `<span>${esc(p.name)}</span><span class="bar"><i style="${v >= 0 ? `left:50%;width:${v * 50}%;background:var(--up)` : `right:50%;width:${-v * 50}%;background:var(--down)`}"></i></span><span class="num ${tone(v)}">${v > 0 ? "+" : ""}${v.toFixed(2)}</span>`; }).join("")}</span>
        <span class="meta">Gold line and shading on the 1m chart, redone with every price. Checked on past gold: about 9 in 10 ten-minute moves ended inside the light shading and half inside the dark one, so the range is honest. Which way it goes was a coin flip, so read the up/down odds as a lean, not a call.${c.kronos ? " Kronos is in it." : ""}</span>`;
      return;
    }
    el.innerHTML = `<span class="name">Trend reading</span><span class="untested">${c.proven ? "TESTED" : "NOT A FORECAST"}</span>
      <span class="call ${cls}">${c.bias > 0 ? "▲ UP" : c.bias < 0 ? "▼ DOWN" : "— FLAT"} ${c.target != null ? `<span class="num" style="font-size:14px">${fmt(c.target)} (${c.target - c.last >= 0 ? "+" : ""}${fmt(c.target - c.last)})</span>` : ""}</span><span></span>
      <span class="odds"><span>Reading <b class="${cls === "flat" ? "" : cls}">${word} ${sc}</b></span><span>in ${horizon(c.minutes || 120)}</span></span>
      <span class="split"><i style="width:${Math.round((1 + Math.max(-1, Math.min(1, +c.score || 0))) * 50)}%"></i></span>
      ${le ? `<span class="meta num">Likely ends between ${fmt(le.lo)} and ${fmt(le.hi)} · middle half ${fmt(le.p25)} to ${fmt(le.p75)}</span>` : ""}
      <span class="tfs6">${TF6.map((t) => `<span>${tfName(t)}${arrow(deskDir(t))}</span>`).join("")}</span>
      <span class="parts">${(c.parts || []).map((p) => { const v = Math.max(-1, Math.min(1, +p.score || 0));
        return `<span>${esc(p.name)}</span><span class="bar"><i style="${v >= 0 ? `left:50%;width:${v * 50}%;background:var(--up)` : `right:50%;width:${-v * 50}%;background:var(--down)`}"></i></span><span class="num ${tone(v)}">${v > 0 ? "+" : ""}${v.toFixed(2)}</span>`; }).join("")}</span>
      <span class="meta">${c.live ? `The gold line looks ${LIVE_MIN} minutes ahead and is redone at every 1m candle close · from the ${candleClock(c.t - 60)} candle` : `The gold line on the 1m to 15m chart leans this way for the next ${horizon(c.minutes || 120)}`}${c.kronos ? ", with Kronos mixed in" : " (Kronos not in it right now)"}.</span>
      <span class="meta">${esc(c.note || "Describes the chart, not a forecast.")}</span>`;
  }
  // state.mesh: live scoreboard. sources [{name, by_h {"30"|"60"|"120": {n, right, coin_band, beats_coin} | null}}],
  // horizons, rows, since, trust {group: x}, min_n, note
  const MAIN = ["Trend line", "Higher timeframes", "Intraday structure", "ICT order flow", "Kronos", "Always up", "Last 30 min"];
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
    const earned = Object.keys(tr).length;       // trust needs 3 sigma on 200+ checks, so luck rarely gets there
    el.innerHTML = `<span class="name">Live scoreboard</span><span class="untested">${earned ? "A GROUP EARNED WEIGHT" : "NOTHING PROVEN YET"}</span>
      <table>${head}${main.map(row).join("")}</table>
      ${rest.length ? `<details ${meshOpen ? "open" : ""}><summary>Every concept on its own (${rest.length})</summary><table>${head}${rest.map(row).join("")}</table></details>` : ""}
      <span class="meta">${m.since ? `Since ${new Date(m.since * 1000).toLocaleDateString([], { day: "numeric", month: "short" })} · ` : ""}${m.rows ? `${m.rows.toLocaleString("en-US")} candles · ` : ""}Green or red = beyond a coin flip so far; with this many rows a few colour by luck. A group earns more weight in the trend line after ${m.min_n || 200} checks clearly beyond a coin.${m.error ? " " + esc(m.error) : ""}</span>
      <span class="meta">${esc(m.note || "")}</span>`;
    const d = el.querySelector("details");
    if (d) d.addEventListener("toggle", () => { meshOpen = d.open; });
  }
  function renderSmcRead() {
    const m = S.smc, el = $("smcRead");
    el.hidden = !ovl.smc || !m || !m.summary;
    if (el.hidden) return;
    const b = m.bias;
    el.innerHTML = `<b class="${b > 0 ? "up" : b < 0 ? "down" : ""}">${b > 0 ? "▲" : b < 0 ? "▼" : "•"} SMC ${esc((m.tf || "").replace(/^M(\d+)$/, "$1m").replace(/^H(\d+)$/, "$1h"))}</b> ${esc(m.summary)}`;
  }
  function renderScalper() {
    const s = S.active;
    $("scalper").hidden = !ovl.scalper;
    $("scalper").innerHTML = s
      ? `<span class="name">Scalper</span><button class="use" data-src="scalper">Use SL/TP</button>
         <span class="call ${s.dir === 1 ? "up" : "down"}">${s.dir === 1 ? "▲ BUY" : "▼ SELL"} <span class="num" style="font-size:14px">${fmt(s.entry)}</span></span>
         <span class="meta">SL ${fmt(s.sl)} · TP ${fmt(s.tp2)} · lost money in the backtest, use your own judgement</span>`
      : `<span class="name">Scalper</span><span></span><span class="call flat">No signal</span><span></span>
         <span class="meta">Lost money in the backtest. Shown for reference only.</span>`;
  }

  // ---------------------------------------------------------------- state (balance, trades, signals)
  function render() {
    const a = S.account || {};
    const mode = S.source === "demo" ? "paper" : a.mode;
    const badge = $("badge");
    badge.className = "pill " + (mode === "real" ? "real" : mode === "demo" || mode === "contest" || mode === "paper" ? "demo" : "unknown");
    badge.textContent = mode === "real" ? "REAL MONEY" : mode === "demo" ? "DEMO ACCOUNT" : mode === "paper" ? "PRACTICE · FAKE PRICES" : "DEMO OR REAL? CHECK";
    if (a.login && mode !== "paper") badge.textContent += ` · #${a.login}`;
    if (S.symbol) $("sym").textContent = S.symbol;
    $("bal").textContent = money(a.balance);
    $("eq").textContent = money(a.equity);
    $("mrgK").hidden = a.margin == null; $("mrg").textContent = money(a.margin);           // the MT5 bridge sends these
    $("freeK").hidden = a.free_margin == null; $("free").textContent = money(a.free_margin);
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
    if (S.error && !(S.market && S.error === S.market.note)) issues.push(S.error);   // market closed is said by the ticket
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
    renderBoom();
    renderHeadsUp();
    renderTrend();
    renderDesks();
    renderMesh();
    renderSmcRead();
    drawMarkers();
    drawLines();
    drawForecast();
    drawZones();
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
