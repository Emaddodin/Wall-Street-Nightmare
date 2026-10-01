/**
 * Stratton Oakmont — Apple Pro Minimal Controller
 * Grounded in Emil Kowalski's Apple Design Framework (WWDC Fluid Interfaces)
 * Monochromatic, high-frequency, responsive execution controller.
 */

// Global State
let currentTab = 'tab-desk';
let autoTradeArmed = true;
let chartTicks = [];
let maxChartTicks = 60;
let lastMidPrice = 0;
let pollTimer = null;
let regimeDaysCache = [];
let regimeTradesCache = [];
let chartDrawPending = false;

// DOM Initialization
document.addEventListener('DOMContentLoaded', () => {
  initChart();
  initAutoTradeToggle();
  initModals();
  initOperatorAuth();
  updateIctKillzoneTicker();
  setInterval(updateIctKillzoneTicker, 30000);
  startTelemetryPolling();
});

// View State & Daily History State
let currentView = 'desk';
let currentCard3Tab = 'chart';
let currentHistoryPage = 1;
let totalHistoryPages = 1;
let historyFilter = 'all';
let historySearchQuery = '';
let historyLoaded = false;
let lastHadPosition = false;

// History Drawer Modal Controller (Bottom Sheet)
function openHistoryDrawer() {
  const drawer = document.getElementById('history-drawer-overlay');
  if (drawer) {
    drawer.style.display = 'flex';
    document.body.style.overflow = 'hidden';
    if (!historyLoaded) {
      loadDailyHistory(1);
    }
  }
}

function closeHistoryDrawer() {
  const drawer = document.getElementById('history-drawer-overlay');
  if (drawer) {
    drawer.style.display = 'none';
    document.body.style.overflow = '';
  }
}

function handleDrawerOverlayClick(e) {
  if (e.target && e.target.id === 'history-drawer-overlay') {
    closeHistoryDrawer();
  }
}

// View Switching: Desk <-> Daily History via Segmented Buttons or Drawer
function switchView(viewName) {
  if (viewName === 'history') {
    openHistoryDrawer();
  } else {
    closeHistoryDrawer();
    requestChartDraw();
  }
}

// Sub-Tab Switcher for Card 3 (Chart / Live Position / Radar)
function switchCard3View(tab) {
  currentCard3Tab = tab;
  const btnChart = document.getElementById('btn-card3-chart');
  const btnPos = document.getElementById('btn-card3-pos');
  const btnRadar = document.getElementById('btn-card3-radar');

  const pnlChart = document.getElementById('panel-card3-chart');
  const pnlPos = document.getElementById('panel-card3-pos');
  const pnlRadar = document.getElementById('panel-card3-radar');

  if (btnChart) btnChart.classList.toggle('active', tab === 'chart');
  if (btnPos) btnPos.classList.toggle('active', tab === 'position');
  if (btnRadar) btnRadar.classList.toggle('active', tab === 'radar');

  if (pnlChart) pnlChart.style.display = (tab === 'chart') ? 'block' : 'none';
  if (pnlPos) pnlPos.style.display = (tab === 'position') ? 'flex' : 'none';
  if (pnlRadar) pnlRadar.style.display = (tab === 'radar') ? 'grid' : 'none';

  if (tab === 'chart') {
    requestChartDraw();
  }
}

// Minimal Dynamic ICT Killzone Detector
function updateIctKillzoneTicker() {
  const now = new Date();
  const utcHours = now.getUTCHours();
  const utcMinutes = now.getUTCMinutes();
  const timeVal = utcHours + utcMinutes / 60;

  let kzName = "";
  let kzHours = "";
  let isActive = false;

  if (timeVal >= 7 && timeVal < 10) {
    kzName = "London Open Killzone";
    kzHours = "07:00 – 10:00 UTC";
    isActive = true;
  } else if (timeVal >= 12 && timeVal < 15) {
    kzName = "New York Open Killzone";
    kzHours = "12:00 – 15:00 UTC";
    isActive = true;
  } else if (timeVal >= 15 && timeVal < 17) {
    kzName = "London Close Killzone";
    kzHours = "15:00 – 17:00 UTC";
    isActive = true;
  } else if (timeVal >= 0 && timeVal < 5) {
    kzName = "Asian Session Killzone";
    kzHours = "00:00 – 05:00 UTC";
    isActive = true;
  } else {
    const nextKz = timeVal < 7 ? "London Open (07:00 UTC)" : (timeVal < 12 ? "NY Open (12:00 UTC)" : "Asian (00:00 UTC)");
    kzName = "Interbank Drift";
    kzHours = `Next: ${nextKz}`;
    isActive = false;
  }

  const nameEl = document.getElementById('killzone-name');
  const timeEl = document.getElementById('killzone-time');
  const dotEl = document.getElementById('killzone-dot');
  const chartKzEl = document.getElementById('chart-kz-label');

  if (nameEl) nameEl.innerText = kzName;
  if (timeEl) timeEl.innerText = kzHours;
  if (dotEl) {
    dotEl.className = isActive ? 'killzone-dot' : 'killzone-dot offpeak';
  }
  if (chartKzEl) {
    chartKzEl.innerText = `ICT Killzone: ${kzName} (${kzHours}) ${isActive ? '· ACTIVE' : ''}`;
  }
}

// Daily Compounding History Fetcher
async function loadDailyHistory(page = 1) {
  currentHistoryPage = page;
  const tbody = document.getElementById('history-table-body');
  if (tbody) {
    tbody.innerHTML = `
      <tr>
        <td colspan="10" style="text-align: center; color: var(--text-tertiary); padding: 25px;">
          Fetching daily compounding records from sovereign ledger...
        </td>
      </tr>
    `;
  }

  try {
    const q = encodeURIComponent(historySearchQuery);
    const res = await fetch(`/api/daily-history?page=${page}&limit=25&filter=${historyFilter}&q=${q}&t=${Date.now()}`);
    if (!res.ok) throw new Error('Network response not ok');
    const data = await res.json();
    historyLoaded = true;
    totalHistoryPages = data.pages || 1;
    renderDailyHistoryTable(data.days || []);
    updateDailyPagination(data.total || 0, data.page || 1, data.pages || 1, data.limit || 25);
  } catch (err) {
    if (tbody) {
      tbody.innerHTML = `
        <tr>
          <td colspan="10" style="text-align: center; color: var(--signal-alert); padding: 25px;">
            Error loading daily history: ${err.message}
          </td>
        </tr>
      `;
    }
  }
}

// Render Daily History Rows
function renderDailyHistoryTable(days) {
  const tbody = document.getElementById('history-table-body');
  if (!tbody) return;

  if (!days || days.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="10" style="text-align: center; color: var(--text-tertiary); padding: 25px;">
          No daily records match current filters.
        </td>
      </tr>
    `;
    return;
  }

  let html = '';
  days.forEach(d => {
    const pnl = parseFloat(d.day_pnl || 0);
    const isLive = String(d.day_num || '').includes('Live');
    const pnlSign = pnl >= 0 ? '+' : '';
    const pnlColor = pnl > 0 ? 'var(--signal-active)' : (pnl < 0 ? 'var(--signal-alert)' : 'var(--text-secondary)');
    const statusClass = isLive ? 'color: var(--signal-active); font-weight: 700;' : (pnl >= 0 ? 'color: var(--signal-active);' : 'color: var(--text-tertiary);');

    html += `
      <tr style="${isLive ? 'background: rgba(220, 70, 95, 0.12);' : ''}">
        <td class="tabular" style="font-weight: 700; color: ${isLive ? 'var(--signal-active)' : 'var(--text-primary)'};">
          ${isLive ? '<span class="live-pulse-dot" style="display:inline-block; vertical-align:middle; margin-right:4px;"></span>[LIVE] ' : ''}Day ${d.day_num || '—'}
        </td>
        <td class="tabular" style="font-size: 11.5px; color: var(--text-secondary);">${d.date || ''}</td>
        <td class="tabular">$${parseFloat(d.start_balance || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
        <td class="tabular" style="font-weight: 700; color: ${pnlColor};">
          ${pnlSign}$${Math.abs(pnl).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </td>
        <td class="tabular hide-mobile">$${parseFloat(d.withdrawn_today || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
        <td class="tabular hide-mobile" style="font-weight: 600; color: var(--gold-accent);">$${parseFloat(d.cumulative_withdrawn || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
        <td class="tabular" style="font-weight: 700; color: var(--text-primary);">$${parseFloat(d.end_balance || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
        <td class="tabular hide-mobile">${d.trades_count || 0} (${d.wins || 0})</td>
        <td class="tabular" style="font-weight: 600;">${parseFloat(d.win_rate_pct || 0).toFixed(1)}%</td>
        <td class="hide-mobile"><span style="font-size: 10px; font-family: var(--font-mono); ${statusClass}">${d.status || 'COMPOUNDED'}</span></td>
      </tr>
    `;
  });

  tbody.innerHTML = html;
}

// Update Daily Pagination Bar
function updateDailyPagination(total, page, pages, limit) {
  const infoEl = document.getElementById('history-page-info');
  const counterEl = document.getElementById('history-page-counter');
  const btnFirst = document.getElementById('btn-page-first');
  const btnPrev = document.getElementById('btn-page-prev');
  const btnNext = document.getElementById('btn-page-next');
  const btnLast = document.getElementById('btn-page-last');

  const start = total === 0 ? 0 : (page - 1) * limit + 1;
  const end = Math.min(page * limit, total);

  if (infoEl) infoEl.innerText = `Showing ${start} - ${end} of ${total.toLocaleString()} days`;
  if (counterEl) counterEl.innerText = `Page ${page} of ${pages}`;

  if (btnFirst) btnFirst.disabled = (page <= 1);
  if (btnPrev) btnPrev.disabled = (page <= 1);
  if (btnNext) btnNext.disabled = (page >= pages);
  if (btnLast) btnLast.disabled = (page >= pages);
}

function changeDailyPage(newPage) {
  if (newPage < 1 || newPage > totalHistoryPages) return;
  loadDailyHistory(newPage);
}

function setDailyFilter(flt) {
  historyFilter = flt;
  document.querySelectorAll('.filter-pill').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-filter') === flt);
  });
  loadDailyHistory(1);
}

const handleDailySearch = debounce((val) => {
  historySearchQuery = val.trim();
  loadDailyHistory(1);
}, 250);

// Telemetry Polling with Visibility & Battery Optimization
let isPageVisible = !document.hidden;

function startTelemetryPolling() {
  document.addEventListener('visibilitychange', () => {
    isPageVisible = !document.hidden;
    if (isPageVisible) {
      if (pollTimer) clearTimeout(pollTimer);
      fetchTelemetry();
    }
  });

  const fetchTelemetry = async () => {
    try {
      const res = await fetch('/api/hft?t=' + Date.now(), { cache: 'no-store' });
      if (res.ok) {
        const data = await res.json();
        window.requestAnimationFrame(() => updateDashboard(data));
      }
    } catch (err) {
      console.warn('Telemetry poll error:', err);
    } finally {
      // Throttle when phone is locked or tab is hidden
      const delay = isPageVisible ? 1000 : 10000;
      pollTimer = setTimeout(fetchTelemetry, delay);
    }
  };
  fetchTelemetry();
}

// Update DOM with live HFT data
function updateDashboard(d) {
  if (!d) return;

  // 1. Header Quote
  const quotePriceEl = document.getElementById('nav-quote-price');
  const quoteSpreadEl = document.getElementById('nav-quote-spread');
  const midPrice = d.current_price || d.mid_price || 0;
  
  if (quotePriceEl && midPrice > 0) {
    quotePriceEl.innerText = '$' + midPrice.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  if (quoteSpreadEl && d.spread_bps !== undefined) {
    quoteSpreadEl.innerText = (d.spread_bps * 10).toFixed(1) + ' pts';
  }

  // 2. Net Equity Display
  const bal = d.balance || d.equity || 59.87;
  const intPart = Math.floor(bal);
  const decPart = (bal - intPart).toFixed(2).slice(1);
  
  const heroIntEl = document.getElementById('hero-int');
  const heroDecEl = document.getElementById('hero-dec');
  if (heroIntEl) heroIntEl.innerText = intPart.toLocaleString('en-US');
  if (heroDecEl) heroDecEl.innerText = decPart;

  // Broker Account Badge & Leverage
  const brokerBadgeEl = document.getElementById('broker-account-badge');
  if (brokerBadgeEl) {
    const isReal = (d.account_mode && d.account_mode.toUpperCase() === "REAL") ||
                   (d.mode && d.mode.toUpperCase().includes("REAL"));
    if (isReal) {
      brokerBadgeEl.innerText = 'LiteFinance MT5 Real Account';
      brokerBadgeEl.style.borderColor = 'rgba(48, 209, 88, 0.45)';
      brokerBadgeEl.style.color = 'rgba(48, 209, 88, 0.95)';
    } else {
      brokerBadgeEl.innerText = d.mode || 'LiteFinance MT5 Demo Account';
      brokerBadgeEl.style.borderColor = 'rgba(255, 159, 10, 0.45)';
      brokerBadgeEl.style.color = 'rgba(255, 159, 10, 0.95)';
    }
  }

  const leverageBadgeEl = document.getElementById('broker-leverage-badge');
  if (leverageBadgeEl) {
    const lev = d.current_tier ? '1:1000 LEVERAGE' : '1:1000 LEVERAGE';
    leverageBadgeEl.innerText = lev;
  }

  // PnL Ribbon
  const pnlUsd = d.realized_pnl || 0;
  const pnlPct = d.pnl_pct || 0;
  const heroPnlEl = document.getElementById('hero-pnl-chip');
  if (heroPnlEl) {
    const sign = pnlUsd >= 0 ? '+' : '';
    heroPnlEl.innerText = `${sign}$${pnlUsd.toFixed(2)} (${sign}${pnlPct.toFixed(1)}%)`;
  }

  // Vault Reserves
  const vaultVal = (d.vault && d.vault.total_harvested) ? d.vault.total_harvested : 1.57;
  const cardVaultEl = document.getElementById('card-vault-val');
  if (cardVaultEl) cardVaultEl.innerText = '$' + vaultVal.toFixed(2) + ' USD';

  // Dynamic Dollar Risk Floor
  const riskFloorEl = document.getElementById('card-risk-floor');
  const forensicCapEl = document.getElementById('forensic-dollar-cap');
  const dynamicRisk = Math.min(50.0, Math.max(20.0, bal * 0.08));
  if (riskFloorEl) riskFloorEl.innerText = `≤ $${dynamicRisk.toFixed(2)} Strict`;
  if (forensicCapEl) forensicCapEl.innerText = `min($${dynamicRisk.toFixed(0)}, 8% Eq)`;

  // Auto-Trade State
  const botRunning = d.bot_running !== undefined ? d.bot_running : true;
  updateSwitchState(botRunning);

  // 3. Update Chart Ticks
  if (midPrice > 0 && midPrice !== lastMidPrice) {
    pushChartTick(midPrice);
    lastMidPrice = midPrice;
  }

  // 4. Laya System 1 & RAG Memory HUD
  const laya = d.laya || {};
  const rag = laya.rag_twins || {};
  
  const layaConfEl = document.getElementById('laya-conf-val');
  if (layaConfEl) layaConfEl.innerText = (laya.last_confidence || 91.5).toFixed(1) + '%';
  
  const layaGradeEl = document.getElementById('laya-grade-badge');
  if (layaGradeEl) layaGradeEl.innerText = (laya.last_grade || 'READY') + ' · 6,613 TRADES';

  const ragWrEl = document.getElementById('rag-twin-wr');
  if (ragWrEl) ragWrEl.innerText = rag.twin_win_rate || '80.0%';

  const ragTrapEl = document.getElementById('rag-trap-risk');
  if (ragTrapEl) ragTrapEl.innerText = rag.trap_risk || '15.0%';

  const ragSafeMinEl = document.getElementById('rag-safe-min');
  if (ragSafeMinEl) ragSafeMinEl.innerText = (rag.max_safe_holding_min || 25) + ' min';

  // Macro Politician Heat
  const politician = laya.politician || {};
  const heatIdx = politician.heat_index !== undefined ? politician.heat_index : 40.0;
  const heatEl = document.getElementById('macro-heat-val');
  if (heatEl) heatEl.innerText = heatIdx.toFixed(0) + '/100 (' + (politician.heat_grade || 'MODERATE') + ')';

  // 5. Broker Radar & Margin Telemetry
  const freeMarginEl = document.getElementById('margin-free-val');
  if (freeMarginEl) {
    freeMarginEl.innerText = `$${(d.equity || bal).toFixed(2)}`;
  }
  const radarSpreadEl = document.getElementById('radar-spread');
  if (radarSpreadEl && d.spread_bps !== undefined) {
    radarSpreadEl.innerText = `${(d.spread_bps * 10).toFixed(1)} pts (Cutoff 4.5)`;
  }
  const radarFsmEl = document.getElementById('radar-fsm');
  if (radarFsmEl) {
    radarFsmEl.innerText = `${d.fsm_state || 'SCANNING'} · Breakout + Retest`;
  }
  const radarLevelsEl = document.getElementById('radar-levels');
  if (radarLevelsEl && midPrice > 0) {
    radarLevelsEl.innerText = `Asia $${(midPrice - 18.5).toFixed(1)} – $${(midPrice + 16.2).toFixed(1)}`;
  }

  // 6. Live Position HUD (Auto-switch on trade)
  renderLivePositionHUD(d.position, midPrice);

  // 7. Active Position & Orders Table
  renderActiveOrders(d.position, d.recent_logs);
}

// Render Live Position Floating / Switching HUD
function renderLivePositionHUD(pos, midPrice) {
  const tabCountEl = document.getElementById('tab-pos-count');
  const hud = document.getElementById('panel-card3-pos');
  const idle = document.getElementById('panel-pos-idle');
  const cardTitle = document.getElementById('card3-title');

  if (!pos || !pos.is_active) {
    if (tabCountEl) tabCountEl.innerText = '0';
    if (hud) hud.style.display = 'none';
    if (idle) idle.style.display = 'block';
    lastHadPosition = false;
    if (cardTitle) cardTitle.innerText = 'Market Order Flow · 1m Micro Ticks';
    return;
  }

  // Active Position Detected!
  if (tabCountEl) tabCountEl.innerText = '1';
  if (hud) hud.style.display = 'flex';
  if (idle) idle.style.display = 'none';
  lastHadPosition = true;
  if (cardTitle) cardTitle.innerText = `Active Market Position · ${pos.direction} Ticket #${pos.ticket_id || '91456523-1'}`;

  const isBuy = (pos.direction || 'BUY').toUpperCase() === 'BUY';
  const pnl = pos.floating_pnl || 0;
  const pnlSign = pnl >= 0 ? '+' : '';

  if (hud) {
    hud.className = isBuy ? 'live-position-hud' : 'live-position-hud short';
  }

  const dirBadge = document.getElementById('hud-pos-dir');
  if (dirBadge) {
    dirBadge.className = isBuy ? 'dir-badge buy' : 'dir-badge sell';
    dirBadge.innerText = isBuy ? 'BUY' : 'SELL';
  }

  const ticketEl = document.getElementById('hud-pos-ticket');
  if (ticketEl) ticketEl.innerText = '#' + (pos.ticket_id || '91456523-1');

  const volEl = document.getElementById('hud-pos-vol');
  if (volEl) volEl.innerText = (pos.volume || 0.01).toFixed(2) + ' Lots';

  const stagEl = document.getElementById('hud-pos-stagnation');
  if (stagEl) stagEl.innerText = `Holding: ${pos.duration_min || 1}m / 25m`;

  const pnlEl = document.getElementById('hud-pos-pnl');
  if (pnlEl) {
    pnlEl.innerText = `${pnlSign}$${Math.abs(pnl).toFixed(2)}`;
    pnlEl.style.color = pnl >= 0 ? 'var(--signal-active)' : 'var(--signal-alert)';
  }

  const entryEl = document.getElementById('hud-pos-entry');
  if (entryEl) entryEl.innerText = '$' + (pos.entry_price || midPrice || 0).toFixed(2);

  const currEl = document.getElementById('hud-pos-current');
  if (currEl) currEl.innerText = '$' + (pos.current_price || midPrice || 0).toFixed(2);

  const slEl = document.getElementById('hud-pos-sl');
  if (slEl) {
    const slVal = pos.stop_loss || (isBuy ? (pos.entry_price - 1.5) : (pos.entry_price + 1.5));
    slEl.innerText = `$${slVal.toFixed(2)}`;
  }

  const wmEl = document.getElementById('hud-pos-wm');
  if (wmEl) wmEl.innerText = (pos.peak_floating_pnl ? `Peak +$${pos.peak_floating_pnl.toFixed(2)}` : '-18% Pullback Active');
}

// Render Active Orders Table
function renderActiveOrders(pos, logs) {
  const tbody = document.getElementById('active-orders-body');
  const countEl = document.getElementById('open-tickets-count');
  if (!tbody) return;

  if (!pos || !pos.is_active) {
    if (countEl) countEl.innerText = '0 OPEN POSITIONS';
    tbody.innerHTML = `
      <tr>
        <td colspan="7" style="text-align: center; color: var(--text-tertiary); padding: 18px;">
          No open market tickets · FSM State: SCANNING for high-confluence ICT Breakout + Retest
        </td>
      </tr>
    `;
    return;
  }

  if (countEl) countEl.innerText = '1 ACTIVE POSITION';
  const pnlSign = pos.floating_pnl >= 0 ? '+' : '';
  tbody.innerHTML = `
    <tr>
      <td class="tabular" style="font-weight: 600; color: var(--text-primary);">${pos.ticket_id || '91456523-1'}</td>
      <td><span style="font-weight: 700; color: ${pos.direction === 'BUY' ? 'var(--signal-active)' : 'var(--signal-alert)'};">${pos.direction}</span></td>
      <td class="tabular">${pos.volume ? pos.volume.toFixed(2) : '0.02'}</td>
      <td class="tabular">$${pos.entry_price ? pos.entry_price.toFixed(2) : '4358.00'}</td>
      <td class="tabular">$${pos.current_price ? pos.current_price.toFixed(2) : '4358.15'}</td>
      <td class="tabular" style="font-weight: 700; color: ${pos.floating_pnl >= 0 ? 'var(--signal-active)' : 'var(--signal-alert)'};">
        ${pnlSign}$${pos.floating_pnl ? pos.floating_pnl.toFixed(2) : '0.00'}
      </td>
      <td>
        <button class="btn-minimal destructive" style="height: 24px; padding: 0 8px; font-size: 11px;" onclick="triggerEmergencyFlatten()">
          Close
        </button>
      </td>
    </tr>
  `;
}

// Chart Engine (Monochromatic, High-Precision, Hardware-Accelerated)
function initChart() {
  const canvas = document.getElementById('tickChart');
  if (!canvas) return;

  const resize = () => {
    const rect = canvas.parentElement.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    requestChartDraw();
  };

  window.addEventListener('resize', debounce(resize, 100));
  resize();
}

function pushChartTick(price) {
  chartTicks.push(price);
  if (chartTicks.length > maxChartTicks) {
    chartTicks.shift();
  }
  requestChartDraw();
}

function requestChartDraw() {
  if (chartDrawPending || !isPageVisible) return;
  chartDrawPending = true;
  window.requestAnimationFrame(() => {
    drawChart();
    chartDrawPending = false;
  });
}

function drawChart() {
  const canvas = document.getElementById('tickChart');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.width;
  const h = canvas.height;

  ctx.clearRect(0, 0, w, h);

  if (chartTicks.length < 2) return;

  const min = Math.min(...chartTicks) - 0.5;
  const max = Math.max(...chartTicks) + 0.5;
  const range = max - min || 1;

  const points = chartTicks.map((val, idx) => {
    const x = (idx / (chartTicks.length - 1)) * (w - 40 * dpr) + 20 * dpr;
    const y = h - ((val - min) / range) * (h - 40 * dpr) - 20 * dpr;
    return { x, y };
  });

  // 1. Draw Area Gradient (Burgundy Wine Refraction)
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, 'rgba(220, 70, 95, 0.22)');
  grad.addColorStop(0.5, 'rgba(122, 20, 40, 0.08)');
  grad.addColorStop(1, 'rgba(74, 8, 21, 0.00)');

  ctx.beginPath();
  ctx.moveTo(points[0].x, h);
  ctx.lineTo(points[0].x, points[0].y);
  for (let i = 1; i < points.length; i++) {
    const xc = (points[i - 1].x + points[i].x) / 2;
    const yc = (points[i - 1].y + points[i].y) / 2;
    ctx.quadraticCurveTo(points[i - 1].x, points[i - 1].y, xc, yc);
  }
  ctx.lineTo(points[points.length - 1].x, points[points.length - 1].y);
  ctx.lineTo(points[points.length - 1].x, h);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // 2. Draw Stroke Line (Razor-Sharp Pure White)
  ctx.beginPath();
  ctx.moveTo(points[0].x, points[0].y);
  for (let i = 1; i < points.length; i++) {
    const xc = (points[i - 1].x + points[i].x) / 2;
    const yc = (points[i - 1].y + points[i].y) / 2;
    ctx.quadraticCurveTo(points[i - 1].x, points[i - 1].y, xc, yc);
  }
  ctx.lineTo(points[points.length - 1].x, points[points.length - 1].y);
  ctx.strokeStyle = '#FFFFFF';
  ctx.lineWidth = 2 * dpr;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.stroke();

  // 3. Draw Last Price Dot
  const last = points[points.length - 1];
  ctx.beginPath();
  ctx.arc(last.x, last.y, 4 * dpr, 0, Math.PI * 2);
  ctx.fillStyle = '#FFFFFF';
  ctx.fill();
  ctx.beginPath();
  ctx.arc(last.x, last.y, 8 * dpr, 0, Math.PI * 2);
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.3)';
  ctx.lineWidth = 2 * dpr;
  ctx.stroke();
}

// Operator Security & Execution Authorization
function getOperatorKey() {
  return localStorage.getItem('stratton_operator_key') || sessionStorage.getItem('stratton_operator_key') || '';
}

function setOperatorKey(key) {
  if (key) {
    localStorage.setItem('stratton_operator_key', key);
    sessionStorage.setItem('stratton_operator_key', key);
    updateAuthBadge(true);
  }
}

function clearOperatorKey() {
  localStorage.removeItem('stratton_operator_key');
  sessionStorage.removeItem('stratton_operator_key');
  updateAuthBadge(false);
}

function updateAuthBadge(isAuthed) {
  const lbl = document.getElementById('operator-lock-label');
  const btn = document.getElementById('operator-auth-btn');
  const icon = document.getElementById('lock-icon-svg');
  if (lbl) {
    lbl.innerText = isAuthed ? 'Armed' : 'Locked';
  }
  if (btn) {
    if (isAuthed) {
      btn.classList.add('primary');
      btn.title = 'Operator Authorized · Tap to Lock';
    } else {
      btn.classList.remove('primary');
      btn.title = 'Operator Locked · Tap to Authorize';
    }
  }
  if (icon) {
    if (isAuthed) {
      icon.innerHTML = '<rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0 1 9.9-1"></path>';
      icon.style.color = 'var(--signal-active)';
    } else {
      icon.innerHTML = '<rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path>';
      icon.style.color = 'var(--text-secondary)';
    }
  }
}

async function initOperatorAuth() {
  const key = getOperatorKey();
  if (key) {
    try {
      const res = await fetch('/api/auth/status', {
        headers: { 'X-Stratton-Auth': 'Bearer ' + key }
      });
      if (res.ok) {
        const d = await res.json();
        updateAuthBadge(Boolean(d.authenticated));
      } else {
        clearOperatorKey();
      }
    } catch {
      updateAuthBadge(Boolean(key));
    }
  } else {
    updateAuthBadge(false);
  }

  const pinInput = document.getElementById('operator-pin-input');
  if (pinInput) {
    pinInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') submitOperatorAuth();
    });
  }
}

function openAuthModal() {
  const key = getOperatorKey();
  if (key) {
    if (confirm('Operator controls are currently authorized. Lock terminal to Read-Only mode?')) {
      clearOperatorKey();
      return;
    }
  }
  const errEl = document.getElementById('auth-modal-error');
  if (errEl) errEl.style.display = 'none';
  const inp = document.getElementById('operator-pin-input');
  if (inp) {
    inp.value = '';
    setTimeout(() => inp.focus(), 100);
  }
  openModal('auth-modal');
}

async function submitOperatorAuth() {
  const inp = document.getElementById('operator-pin-input');
  const errEl = document.getElementById('auth-modal-error');
  const btn = document.getElementById('btn-submit-auth');
  const val = inp ? inp.value.trim() : '';

  if (!val) {
    if (errEl) {
      errEl.innerText = 'Please enter a PIN or Operator Key';
      errEl.style.display = 'block';
    }
    return;
  }

  if (btn) btn.disabled = true;
  if (errEl) errEl.style.display = 'none';

  try {
    const res = await fetch('/api/auth/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin: val, token: val })
    });
    const d = await res.json();
    if (res.ok && d.authenticated) {
      setOperatorKey(d.token || val);
      closeModal('auth-modal');
    } else {
      if (errEl) {
        errEl.innerText = d.error || 'Invalid Operator Key or PIN';
        errEl.style.display = 'block';
      }
    }
  } catch (err) {
    if (errEl) {
      errEl.innerText = 'Verification failed: ' + err.message;
      errEl.style.display = 'block';
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Auto-Trade Switch Handlers with Operator Auth & Mutex
let botToggleInFlight = false;
async function toggleAutoTrade() {
  const key = getOperatorKey();
  if (!key) {
    openAuthModal();
    return;
  }
  if (botToggleInFlight) return;
  botToggleInFlight = true;

  const btn = document.getElementById('bot-toggle-btn');
  if (btn) btn.style.opacity = '0.5';

  try {
    const res = await fetch('/api/bot/toggle?t=' + Date.now(), {
      method: 'POST',
      headers: {
        'X-Stratton-Auth': 'Bearer ' + key,
        'Content-Type': 'application/json'
      }
    });
    if (res.status === 401) {
      clearOperatorKey();
      openAuthModal();
      return;
    }
    if (res.ok) {
      const d = await res.json();
      updateSwitchState(d.bot_running);
    }
  } catch (err) {
    console.warn('Bot toggle error:', err);
  } finally {
    botToggleInFlight = false;
    if (btn) btn.style.opacity = '1';
  }
}

function initAutoTradeToggle() {
  const btn = document.getElementById('bot-toggle-btn');
  if (btn) btn.addEventListener('click', toggleAutoTrade);
}

function updateSwitchState(active) {
  autoTradeArmed = active;
  const dot = document.getElementById('bot-toggle-dot');
  const label = document.getElementById('bot-toggle-label');
  if (dot) {
    dot.style.background = active ? 'var(--signal-active)' : 'var(--text-tertiary)';
  }
  if (label) {
    label.innerText = active ? 'Armed' : 'Paused';
  }
}

// Modals Handling
function initModals() {
  document.querySelectorAll('.apple-modal-overlay, .glass-modal-overlay').forEach(overlay => {
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) {
        closeModal(overlay.id);
      }
    });
  });
}

function openModal(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = 'flex';
}

function closeModal(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = 'none';
}

// Emergency Flatten with Confirmation & Operator Auth
function triggerEmergencyFlatten() {
  const key = getOperatorKey();
  if (!key) {
    openAuthModal();
    return;
  }
  openModal('confirm-flatten-modal');
}

let flattenInFlight = false;
async function executeFlattenNow() {
  const key = getOperatorKey();
  if (!key) {
    closeModal('confirm-flatten-modal');
    openAuthModal();
    return;
  }
  if (flattenInFlight) return;
  flattenInFlight = true;

  const btn = document.getElementById('btn-confirm-flatten');
  if (btn) {
    btn.disabled = true;
    btn.innerText = 'Liquidating...';
  }

  try {
    const res = await fetch('/api/flatten', {
      method: 'POST',
      headers: {
        'X-Stratton-Auth': 'Bearer ' + key,
        'Content-Type': 'application/json'
      }
    });
    if (res.status === 401) {
      closeModal('confirm-flatten-modal');
      clearOperatorKey();
      openAuthModal();
      return;
    }
    if (res.ok) {
      closeModal('confirm-flatten-modal');
      alert('Flatten command executed. Broker order cancel & market liquidation active.');
      pollTelemetry();
    } else {
      const d = await res.json();
      alert('Flatten error: ' + (d.error || 'Server rejected command'));
    }
  } catch (err) {
    alert('Flatten network error: ' + err.message);
  } finally {
    flattenInFlight = false;
    if (btn) {
      btn.disabled = false;
      btn.innerText = 'Confirm & Liquidate';
    }
  }
}

// Vault Harvest
async function triggerVaultHarvest() {
  const key = getOperatorKey();
  if (!key) {
    openAuthModal();
    return;
  }
  try {
    const res = await fetch('/api/vault/harvest', {
      method: 'POST',
      headers: {
        'X-Stratton-Auth': 'Bearer ' + key,
        'Content-Type': 'application/json'
      }
    });
    if (res.status === 401) {
      closeModal('vault-modal');
      clearOperatorKey();
      openAuthModal();
      return;
    }
    if (res.ok) {
      const d = await res.json();
      alert(`Harvested $${(d.amount || 0).toFixed(2)} to Stratton Vault!`);
      closeModal('vault-modal');
    }
  } catch (err) {
    alert('Vault error: ' + err.message);
  }
}

// Utility: Debounce
function debounce(func, wait) {
  let timeout;
  return function executedFunction(...args) {
    const later = () => {
      clearTimeout(timeout);
      func(...args);
    };
    clearTimeout(timeout);
    timeout = setTimeout(later, wait);
  };
}
