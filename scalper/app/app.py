"""
scalper/app/app.py
==================
Institutional HFT Quant Terminal.
Dedicated web application for the 5-Pillar High-Frequency Quant Execution Engine.
Serves real-time Avellaneda-Stoikov dynamics, Hawkes clustering, OFI depth,
CatBoost direction probabilities, dynamic Kelly leverage, and ATR Chandelier ratchets.
"""

from __future__ import annotations

import json
import os
import secrets
import ssl
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.getenv("SCALPER_DATA", "/root/ict_sniper/data" if Path("/root/ict_sniper").exists() else str(ROOT / "data")))
HFT_STATE = DATA / "state" / "hft.json"

TOKEN = os.getenv("SCALPER_APP_TOKEN", "7SQMRVRJ-VkD4lG3VXsb1Fc82oYUAP93")
CERT = os.getenv("SCALPER_APP_CERT", "/root/ict_sniper/tls/fullchain.pem")
KEY = os.getenv("SCALPER_APP_KEY", "/root/ict_sniper/tls/privkey.pem")
HOST = os.getenv("SCALPER_APP_HOST", "0.0.0.0")
PORT = int(os.getenv("SCALPER_APP_PORT", "8443"))

SESSIONS: dict[str, float] = {}
SESSION_TTL = 86400 * 30  # 30 days


def _read_hft_state() -> dict:
    if HFT_STATE.exists():
        try:
            with open(HFT_STATE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "engine": "5-Pillar High-Frequency Quant Execution Engine",
        "status": "INITIALIZING",
        "mode": "PAPER TRADING ($65 Start)",
        "symbol": "BTC",
        "balance": 65.00,
        "equity": 65.00,
        "realized_pnl": 0.0,
        "pnl_pct": 0.0,
        "trade_count": 0,
        "mid_price": 0.0,
        "best_bid": 0.0,
        "best_ask": 0.0,
        "spread_bps": 0.0,
        "as_maker_spread_bps": 0.0,
        "as_reservation_price": 0.0,
        "as_inventory_skew": 0.0,
        "hawkes_buy": 0.0,
        "hawkes_sell": 0.0,
        "hawkes_ratio": 0.5,
        "ofi_mean": 0.0,
        "ofi_levels": [0.0, 0.0, 0.0, 0.0, 0.0],
        "garch_sigma": 0.0,
        "dynamic_leverage": 1,
        "kelly_fraction": 0.0,
        "catboost_confidence": 0.0,
        "catboost_direction": "NEUTRAL",
        "atr_ratchet_mult": 3.0,
        "position": None,
        "recent_logs": [],
        "updated_at": time.time(),
    }


def _render_hft_terminal() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>HFT Quant Desk · 5-Pillar Engine</title>
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="theme-color" content="#08090C">
  <style>
    :root {
      --bg: #08090C;
      --card-bg: #0E1117;
      --card-border: #1B2234;
      --accent-cyan: #00F0FF;
      --accent-gold: #D4AF37;
      --accent-green: #00FF88;
      --accent-red: #FF2E54;
      --accent-purple: #A259FF;
      --text-main: #F0F3F8;
      --text-muted: #7E8B9F;
      --mono: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;
      --sans: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui, sans-serif;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
    body {
      background: var(--bg);
      color: var(--text-main);
      font-family: var(--sans);
      min-height: 100vh;
      padding: 16px 14px 48px;
      -webkit-font-smoothing: antialiased;
    }
    .container { max-width: 1200px; margin: 0 auto; display: flex; flex-direction: column; gap: 14px; }
    
    /* Top Header */
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 12px 16px;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
    }
    .brand-box { display: flex; align-items: center; gap: 12px; }
    .brand-logo {
      width: 36px; height: 36px; border-radius: 8px;
      background: linear-gradient(135deg, #1B2234, #D4AF37);
      display: flex; align-items: center; justify-content: center;
      font-weight: 800; font-size: 16px; color: #000;
    }
    .brand-title h1 { font-size: 15px; font-weight: 700; letter-spacing: 0.02em; }
    .brand-title span { font-size: 11px; color: var(--accent-gold); font-family: var(--mono); text-transform: uppercase; }
    
    .status-pill {
      display: flex; align-items: center; gap: 6px;
      padding: 5px 12px; border-radius: 20px;
      background: rgba(0, 255, 136, 0.08); border: 1px solid rgba(0, 255, 136, 0.3);
      font-size: 11px; font-family: var(--mono); color: var(--accent-green);
    }
    .pulse-dot {
      width: 7px; height: 7px; border-radius: 50%;
      background: var(--accent-green); box-shadow: 0 0 8px var(--accent-green);
      animation: pulse 1.8s infinite;
    }
    @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }

    /* Grids */
    .hero-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 12px;
    }
    .pillar-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
      gap: 12px;
    }

    /* Cards */
    .card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 16px;
      position: relative;
      overflow: hidden;
    }
    .card-header {
      display: flex; justify-content: space-between; align-items: center;
      margin-bottom: 12px;
    }
    .card-title {
      font-size: 11px; font-weight: 600; text-transform: uppercase;
      letter-spacing: 0.06em; color: var(--text-muted);
    }
    .card-badge {
      font-size: 10px; font-family: var(--mono);
      padding: 2px 7px; border-radius: 4px;
      background: rgba(212, 175, 55, 0.12); color: var(--accent-gold);
    }
    
    .val-hero { font-size: 26px; font-weight: 700; font-family: var(--mono); }
    .val-sub { font-size: 12px; color: var(--text-muted); margin-top: 4px; font-family: var(--mono); }
    
    /* Rows */
    .metric-row {
      display: flex; justify-content: space-between; align-items: center;
      padding: 8px 0; border-bottom: 1px solid rgba(255, 255, 255, 0.04);
      font-size: 13px;
    }
    .metric-row:last-child { border-bottom: none; }
    .k { color: var(--text-muted); }
    .v { font-family: var(--mono); font-weight: 600; }
    
    /* Visual Bars */
    .bar-container {
      width: 100%; height: 6px; background: #181D29;
      border-radius: 3px; overflow: hidden; margin-top: 6px; display: flex;
    }
    .bar-fill { height: 100%; transition: width 0.3s ease; }

    /* OFI 5-Levels Depth */
    .ofi-stack { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
    .ofi-level-row { display: flex; align-items: center; gap: 8px; font-size: 11px; font-family: var(--mono); }
    .ofi-level-lbl { width: 24px; color: var(--text-muted); }
    .ofi-bar-wrap { flex: 1; height: 10px; background: #141824; border-radius: 3px; display: flex; align-items: center; position: relative; }
    .ofi-mid-line { position: absolute; left: 50%; top: 0; bottom: 0; width: 1px; background: #333C52; }
    .ofi-bar-fill { height: 100%; position: absolute; }
    .ofi-level-val { width: 44px; text-align: right; }

    /* Position Box */
    .pos-box {
      border: 1px solid rgba(0, 240, 255, 0.25);
      background: rgba(0, 240, 255, 0.03);
      border-radius: 8px; padding: 12px; margin-top: 6px;
    }
    .pos-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
    .tag-buy { background: rgba(0, 255, 136, 0.15); color: var(--accent-green); padding: 3px 8px; border-radius: 4px; font-weight: 700; font-size: 12px; }
    .tag-sell { background: rgba(255, 46, 84, 0.15); color: var(--accent-red); padding: 3px 8px; border-radius: 4px; font-weight: 700; font-size: 12px; }

    /* Terminal Log */
    .log-terminal {
      background: #060709;
      border: 1px solid var(--card-border);
      border-radius: 8px; padding: 10px 12px;
      font-family: var(--mono); font-size: 11px;
      height: 180px; overflow-y: auto;
      display: flex; flex-direction: column; gap: 4px;
    }
    .log-line { display: flex; gap: 8px; line-height: 1.4; }
    .log-time { color: var(--text-muted); }
    .log-badge { padding: 0 4px; border-radius: 2px; font-size: 9px; font-weight: 700; }
    .badge-ALPHA { background: var(--accent-cyan); color: #000; }
    .badge-RATCHET { background: var(--accent-gold); color: #000; }
    .badge-EXIT { background: var(--accent-green); color: #000; }
    .badge-SYSTEM { background: #333C52; color: #FFF; }

    /* DayPlanner Progress */
    .day-progress-wrap {
      width: 100%; height: 20px; background: #141824;
      border-radius: 6px; overflow: hidden; position: relative;
      margin: 10px 0;
    }
    .day-progress-fill {
      height: 100%; border-radius: 6px;
      background: linear-gradient(90deg, #D4AF37, #00FF88);
      transition: width 0.5s ease; position: relative;
    }
    .day-progress-label {
      position: absolute; right: 8px; top: 50%;
      transform: translateY(-50%);
      font-size: 10px; font-family: var(--mono); font-weight: 700;
      color: #000; text-shadow: 0 0 3px rgba(0,0,0,0.5);
    }
    .regime-badge {
      display: inline-block; padding: 3px 10px; border-radius: 4px;
      font-size: 11px; font-family: var(--mono); font-weight: 700;
      letter-spacing: 0.05em;
    }
    .regime-NORMAL { background: rgba(0,240,255,0.12); color: var(--accent-cyan); }
    .regime-AHEAD { background: rgba(0,255,136,0.12); color: var(--accent-green); }
    .regime-ALMOST_THERE { background: rgba(0,255,136,0.25); color: #00FF88; }
    .regime-TARGET_HIT { background: rgba(212,175,55,0.25); color: var(--accent-gold); }
    .regime-BEHIND_EARLY { background: rgba(255,170,50,0.15); color: #FFAA32; }
    .regime-BEHIND_LATE { background: rgba(255,46,84,0.15); color: var(--accent-red); }
    .regime-DRAWDOWN_WARNING { background: rgba(255,46,84,0.20); color: var(--accent-red); }
    .regime-DRAWDOWN_HALT { background: rgba(255,46,84,0.30); color: #FF1744; }
    .day-stats-grid {
      display: grid; grid-template-columns: 1fr 1fr; gap: 6px 16px;
    }
  </style>
</head>
<body>
  <div class="container">
    <!-- Header -->
    <header>
      <div class="brand-box">
        <div class="brand-logo">Q</div>
        <div class="brand-title">
          <h1>HFT QUANT TERMINAL</h1>
          <span>5-Pillar Algorithmic Execution Engine</span>
        </div>
      </div>
      <div class="status-pill">
        <div class="pulse-dot"></div>
        <span id="conn-status">HYPERLIQUID L2 LIVE</span>
      </div>
    </header>

    <!-- Key Performance Stats -->
    <div class="hero-grid">
      <div class="card">
        <div class="card-header">
          <span class="card-title">Account Equity</span>
          <span class="card-badge">Base $65.00</span>
        </div>
        <div class="val-hero" id="hero-equity">$65.00</div>
        <div class="val-sub" id="hero-pnl">+0.00 USDT (+0.00%)</div>
      </div>

      <div class="card">
        <div class="card-header">
          <span class="card-title">BTC Mid Price</span>
          <span class="card-badge" id="badge-spread">-- bps</span>
        </div>
        <div class="val-hero" id="hero-mid">$--</div>
        <div class="val-sub" id="hero-bidask">Bid: -- | Ask: --</div>
      </div>

      <div class="card">
        <div class="card-header">
          <span class="card-title">Kelly Leverage</span>
          <span class="card-badge">Dynamic Guard</span>
        </div>
        <div class="val-hero" id="hero-lev">1x</div>
        <div class="val-sub" id="hero-sigma">GARCH σ: 0.0000 · f*: 0.00</div>
      </div>

      <div class="card">
        <div class="card-header">
          <span class="card-title">Execution & Kill Zone</span>
          <span class="card-badge">Paper Account</span>
        </div>
        <div class="val-hero" style="font-size: 14px; color: var(--accent-gold);" id="hero-kz">--</div>
        <div class="val-sub" id="hero-trades">Trades: 0 · Halt: False</div>
      </div>
    </div>

    <!-- Day Planner / Daily Campaign -->
    <div class="card" style="border-color: rgba(212,175,55,0.3);">
      <div class="card-header">
        <span class="card-title">📊 Daily Campaign Planner</span>
        <span class="regime-badge regime-NORMAL" id="dp-regime">NORMAL</span>
      </div>
      <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 4px;">
        <span style="font-family: var(--mono); font-size: 13px; color: var(--text-muted);">
          $<span id="dp-start">65.00</span> → $<span id="dp-target">130.00</span>
        </span>
        <span style="font-family: var(--mono); font-size: 11px; color: var(--accent-gold);">
          Floor: $<span id="dp-floor">32.50</span>
        </span>
      </div>
      <div class="day-progress-wrap">
        <div class="day-progress-fill" id="dp-bar" style="width: 0%;">
          <span class="day-progress-label" id="dp-bar-label">0%</span>
        </div>
      </div>
      <div class="day-stats-grid" style="font-size: 12px; font-family: var(--mono);">
        <div class="metric-row">
          <span class="k">Day PnL</span>
          <span class="v" id="dp-pnl">$0.00</span>
        </div>
        <div class="metric-row">
          <span class="k">Kelly Mult</span>
          <span class="v" id="dp-kelly">1.00×</span>
        </div>
        <div class="metric-row">
          <span class="k">Session Quota</span>
          <span class="v" id="dp-quota">0/12</span>
        </div>
        <div class="metric-row">
          <span class="k">Day Trades</span>
          <span class="v" id="dp-day-trades">0/40</span>
        </div>
        <div class="metric-row">
          <span class="k">KZ Hours Left</span>
          <span class="v" id="dp-kz-hours">--</span>
        </div>
        <div class="metric-row">
          <span class="k">Consec Losses</span>
          <span class="v" id="dp-consec">0</span>
        </div>
        <div class="metric-row">
          <span class="k">Conf Floor</span>
          <span class="v" id="dp-conf-floor">60%</span>
        </div>
        <div class="metric-row">
          <span class="k">Lev Cap</span>
          <span class="v" id="dp-lev-cap">20x</span>
        </div>
      </div>
    </div>
    
    <!-- ICT Kill Zones Schedule & Live Countdown -->
    <div class="card" id="kz-summary-card" style="border-color: rgba(0, 240, 255, 0.3);">
      <div class="card-header">
        <span class="card-title">🕒 ICT Kill Zones & Countdown</span>
        <span class="card-badge" id="kz-hero-badge" style="background: rgba(0,255,136,0.2); color: var(--accent-green); font-weight: 700;">LIVE</span>
      </div>
      <div id="kz-list" style="display: flex; flex-direction: column; gap: 8px; font-family: var(--mono); font-size: 13px;">
        <!-- Populated by JS -->
      </div>
    </div>

    <!-- The 5 Pillars of HFT -->
    <div class="pillar-grid">
      <!-- Pillar 1: Avellaneda-Stoikov -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">Pillar 1: Avellaneda-Stoikov Dynamics</span>
          <span class="card-badge">Market Making</span>
        </div>
        <div class="metric-row">
          <span class="k">Optimal Maker Spread (δ)</span>
          <span class="v" id="as-spread">-- bps</span>
        </div>
        <div class="metric-row">
          <span class="k">Reservation Price (r)</span>
          <span class="v" id="as-reservation">$--</span>
        </div>
        <div class="metric-row">
          <span class="k">Inventory Skew (q)</span>
          <span class="v" id="as-skew">0.00</span>
        </div>
        <div class="metric-row">
          <span class="k">Mid Price Deviation</span>
          <span class="v" id="as-dev">0.00%</span>
        </div>
      </div>

      <!-- Pillar 2: CatBoost Direction -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">Pillar 2: CatBoost ML Predictor</span>
          <span class="card-badge">Threshold > 0.60</span>
        </div>
        <div class="metric-row">
          <span class="k">Confidence Prob</span>
          <span class="v" id="ml-conf" style="color: var(--accent-gold);">0.0%</span>
        </div>
        <div class="bar-container">
          <div class="bar-fill" id="ml-bar" style="width: 50%; background: var(--accent-gold);"></div>
        </div>
        <div class="metric-row" style="margin-top: 8px;">
          <span class="k">Predicted Direction</span>
          <span class="v" id="ml-dir">NEUTRAL</span>
        </div>
        <div class="metric-row">
          <span class="k">Taker Alpha Trigger</span>
          <span class="v" id="ml-trigger">WAITING FOR SETUP</span>
        </div>
      </div>

      <!-- Pillar 3: Hawkes Clustering -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">Pillar 3: Hawkes Mutual Excitation</span>
          <span class="card-badge">Trade Clustering</span>
        </div>
        <div class="metric-row">
          <span class="k">Buy Intensity (λ_b)</span>
          <span class="v" id="hk-buy" style="color: var(--accent-green);">0.00</span>
        </div>
        <div class="metric-row">
          <span class="k">Sell Intensity (λ_s)</span>
          <span class="v" id="hk-sell" style="color: var(--accent-red);">0.00</span>
        </div>
        <div class="metric-row">
          <span class="k">Liquidity Excitement Ratio</span>
          <span class="v" id="hk-ratio">0.50</span>
        </div>
        <div class="bar-container">
          <div class="bar-fill" id="hk-bar" style="width: 50%; background: var(--accent-purple);"></div>
        </div>
      </div>

      <!-- Pillar 4: Order Flow Imbalance (OFI) -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">Pillar 4: Order Flow Imbalance (L1-L5)</span>
          <span class="card-badge">Depth Microstructure</span>
        </div>
        <div class="metric-row">
          <span class="k">Mean OFI Score</span>
          <span class="v" id="ofi-mean">0.00</span>
        </div>
        <div class="ofi-stack" id="ofi-stack">
          <!-- Populated by JS -->
        </div>
      </div>
    </div>

    <!-- Trade Flow Diagnostic & Execution Funnel -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">Trade Flow & Pipeline Diagnostic (A-to-Z Auditor)</span>
        <span class="card-badge" id="flow-status-badge">FUNNEL NOMINAL</span>
      </div>
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; margin-bottom: 8px;">
        <div class="metric-row"><span class="k">Idle Duration</span><span class="v" id="flow-idle">0m</span></div>
        <div class="metric-row"><span class="k">E2E Pipeline Self-Test</span><span class="v" id="flow-selftest" style="color: var(--accent-green);">PASSED (0 Bugs)</span></div>
        <div class="metric-row"><span class="k">Peak Confidence Seen</span><span class="v" id="flow-maxconf">0.0%</span></div>
        <div class="metric-row"><span class="k">Hawkes Quiet Ratio</span><span class="v" id="flow-quiet">0.0%</span></div>
      </div>
      <div style="background: rgba(255,255,255,0.03); border-radius: 6px; padding: 8px 12px; font-size: 12px; font-family: var(--mono); color: var(--text-muted);">
        <span style="color: var(--accent-cyan); font-weight: 700;">DIAGNOSIS:</span> <span id="flow-diagnosis">Evaluating execution pipeline...</span>
      </div>
    </div>

    <!-- Active Position & ATR Chandelier Ratchet -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">Pillar 5: Active Position & Chandelier Ratchet</span>
        <span class="card-badge">Trailing Exit Guard</span>
      </div>
      <div id="position-container">
        <div style="text-align: center; color: var(--text-muted); font-size: 13px; padding: 16px;">
          Scanning L2 Orderbook for Aggressive Alpha Triggers (No Active Position)
        </div>
      </div>
    </div>

    <!-- Live Execution & Telemetry Log -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">Live Execution & Telemetry Stream</span>
        <span class="card-badge">Real-Time</span>
      </div>
      <div class="log-terminal" id="log-terminal">
        <!-- Injected by JS -->
      </div>
    </div>
  </div>

  <script>
    const T = new URLSearchParams(location.search).get('t') || '';
    
    function fmtMoney(n) {
      const v = Number(n) || 0;
      return (v >= 0 ? '+' : '') + v.toFixed(2);
    }

    async function fetchHftState() {
      try {
        const res = await fetch('/api/hft?t=' + T + '&n=' + Date.now());
        if (res.status === 401 || res.status === 403) {
          location.href = '/login';
          return;
        }
        const data = await res.json();
        renderDashboard(data);
      } catch (err) {
        document.getElementById('conn-status').textContent = 'RECONNECTING...';
      }
    }

    function renderDashboard(d) {
      document.getElementById('conn-status').textContent = 'HYPERLIQUID L2 LIVE';
      
      // Hero stats
      const eq = d.equity || d.balance || 65.0;
      const pnl = (d.realized_pnl || 0);
      const pnlPct = d.pnl_pct || 0;
      document.getElementById('hero-equity').textContent = '$' + eq.toFixed(2);
      
      const pnlEl = document.getElementById('hero-pnl');
      pnlEl.textContent = fmtMoney(pnl) + ' USDT (' + fmtMoney(pnlPct) + '%)';
      pnlEl.style.color = pnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';

      const mid = d.mid_price || 0;
      document.getElementById('hero-mid').textContent = mid > 0 ? '$' + mid.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1}) : '$--';
      document.getElementById('hero-bidask').textContent = 'Bid: ' + (d.best_bid||0).toFixed(1) + ' | Ask: ' + (d.best_ask||0).toFixed(1);
      document.getElementById('badge-spread').textContent = (d.spread_bps||0).toFixed(1) + ' bps';

      document.getElementById('hero-lev').textContent = (d.dynamic_leverage||1) + 'x';
      document.getElementById('hero-sigma').textContent = 'GARCH σ: ' + (d.garch_sigma||0).toFixed(5) + ' · f*: ' + (d.kelly_fraction||0).toFixed(3);
      document.getElementById('hero-trades').textContent = 'Trades: ' + (d.trade_count||0) + ' · Cap: $65.00';
      document.getElementById('hero-kz').textContent = d.killzone || '--';

      // DayPlanner Campaign
      const dp = d.day || {};
      const dpProg = Math.max(0, Math.min(100, dp.progress_pct || 0));
      document.getElementById('dp-bar').style.width = dpProg + '%';
      document.getElementById('dp-bar-label').textContent = dpProg.toFixed(0) + '%';
      document.getElementById('dp-start').textContent = (dp.start_balance || 65).toFixed(2);
      document.getElementById('dp-target').textContent = (dp.target_balance || 130).toFixed(2);
      document.getElementById('dp-floor').textContent = (dp.loss_floor || 32.5).toFixed(2);
      const dpPnlEl = document.getElementById('dp-pnl');
      const dpPnl = dp.day_pnl_usdt || 0;
      dpPnlEl.textContent = (dpPnl >= 0 ? '+' : '') + dpPnl.toFixed(2);
      dpPnlEl.style.color = dpPnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
      document.getElementById('dp-kelly').textContent = (dp.kelly_multiplier || 1).toFixed(2) + '×';
      document.getElementById('dp-quota').textContent = dp.session_quota || '0/12';
      document.getElementById('dp-day-trades').textContent = dp.day_trades || '0/40';
      document.getElementById('dp-kz-hours').textContent = (dp.kz_hours_remaining !== undefined ? dp.kz_hours_remaining.toFixed(1) + 'h' : '--');
      const consecEl = document.getElementById('dp-consec');
      consecEl.textContent = dp.consecutive_losses || 0;
      consecEl.style.color = (dp.consecutive_losses || 0) >= 3 ? 'var(--accent-red)' : 'var(--text-main)';
      document.getElementById('dp-conf-floor').textContent = Math.round((dp.confidence_floor || 0.6) * 100) + '%';
      document.getElementById('dp-lev-cap').textContent = (dp.max_leverage_cap || 20) + 'x';
      const regimeEl = document.getElementById('dp-regime');
      const regime = dp.regime || 'NORMAL';
      regimeEl.textContent = regime.replace(/_/g, ' ');
      regimeEl.className = 'regime-badge regime-' + regime;

      // Render ICT Kill Zones Schedule with live countdown
      const defaultZones = [
        { name: "Asian Open", startH: 0, startM: 0, endH: 2, endM: 0, emoji: "🌏", window: "00:00 – 02:00 UTC" },
        { name: "London Open", startH: 2, startM: 0, endH: 5, endM: 0, emoji: "🇬🇧", window: "02:00 – 05:00 UTC" },
        { name: "NY Open", startH: 7, startM: 0, endH: 10, endM: 0, emoji: "🗽", window: "07:00 – 10:00 UTC" },
        { name: "London Close", startH: 11, startM: 0, endH: 13, endM: 0, emoji: "🔄", window: "11:00 – 13:00 UTC" },
        { name: "NY Afternoon", startH: 14, startM: 0, endH: 16, endM: 0, emoji: "📈", window: "14:00 – 16:00 UTC" }
      ];

      const now = new Date();
      const nowMin = now.getUTCHours() * 60 + now.getUTCMinutes();
      let activeFound = false;

      const kzListHtml = defaultZones.map(z => {
        const startMin = z.startH * 60 + z.startM;
        const endMin = z.endH * 60 + z.endM;
        const isActive = nowMin >= startMin && nowMin < endMin;
        
        let statusBadge = "";
        let rowWeight = "400";
        let bgStyle = "background: rgba(255,255,255,0.02);";

        if (isActive) {
          activeFound = true;
          const minsLeft = endMin - nowMin;
          const endStr = String(z.endH).padStart(2, '0') + ":" + String(z.endM).padStart(2, '0') + " UTC";
          rowWeight = "700";
          bgStyle = "background: rgba(0,255,136,0.08); border-left: 3px solid var(--accent-green);";
          statusBadge = `<span style="background: rgba(0,255,136,0.2); color: var(--accent-green); padding: 3px 8px; border-radius: 4px; font-weight: 700; font-size: 11px;">ACTIVE · Ends ${endStr} (${minsLeft}m left)</span>`;
        } else {
          let minsUntil = startMin - nowMin;
          if (minsUntil < 0) minsUntil += 24 * 60;
          const hrs = Math.floor(minsUntil / 60);
          const remMins = minsUntil % 60;
          const timeStr = hrs > 0 ? `${hrs}h ${remMins}m` : `${remMins}m`;
          statusBadge = `<span style="color: var(--text-muted); font-size: 11px;">Starts in ${timeStr}</span>`;
        }

        return `
          <div style="display: flex; justify-content: space-between; align-items: center; padding: 8px 10px; border-radius: 6px; ${bgStyle} font-weight: ${rowWeight};">
            <div style="display: flex; align-items: center; gap: 8px;">
              <span style="font-size: 16px;">${z.emoji}</span>
              <span style="color: ${isActive ? 'var(--accent-green)' : 'var(--text-main)'};">${z.name}</span>
              <span style="font-size: 11px; color: var(--text-muted); margin-left: 4px;">${z.window}</span>
            </div>
            <div>${statusBadge}</div>
          </div>
        `;
      }).join('');

      document.getElementById('kz-list').innerHTML = kzListHtml;
      const heroBadge = document.getElementById('kz-hero-badge');
      if (heroBadge) {
        if (activeFound) {
          heroBadge.textContent = "IN KILL ZONE";
          heroBadge.style.background = "rgba(0,255,136,0.2)";
          heroBadge.style.color = "var(--accent-green)";
        } else {
          heroBadge.textContent = "OFF-WINDOW";
          heroBadge.style.background = "rgba(255,255,255,0.08)";
          heroBadge.style.color = "var(--text-muted)";
        }
      }

      // Pillar 1: AS
      document.getElementById('as-spread').textContent = (d.as_maker_spread_bps||0).toFixed(1) + ' bps';
      document.getElementById('as-reservation').textContent = (d.as_reservation_price||0) > 0 ? '$' + (d.as_reservation_price).toFixed(1) : '$--';
      document.getElementById('as-skew').textContent = (d.as_inventory_skew||0).toFixed(3);
      const dev = mid > 0 && d.as_reservation_price ? ((d.as_reservation_price - mid) / mid * 100).toFixed(2) : '0.00';
      document.getElementById('as-dev').textContent = (dev > 0 ? '+' : '') + dev + '%';

      // Pillar 2: ML
      const conf = (d.catboost_confidence || 0);
      const confPct = Math.round(conf * 100);
      document.getElementById('ml-conf').textContent = confPct + '%';
      document.getElementById('ml-bar').style.width = Math.min(100, Math.max(0, confPct)) + '%';
      document.getElementById('ml-dir').textContent = d.catboost_direction || 'NEUTRAL';
      
      const triggerEl = document.getElementById('ml-trigger');
      if (conf >= 0.60) {
        triggerEl.textContent = 'ALPHA TRIGGER ACTIVE (>0.60)';
        triggerEl.style.color = 'var(--accent-green)';
      } else {
        triggerEl.textContent = 'WAITING FOR CONF > 60%';
        triggerEl.style.color = 'var(--text-muted)';
      }

      // Pillar 3: Hawkes
      document.getElementById('hk-buy').textContent = (d.hawkes_buy||0).toFixed(2);
      document.getElementById('hk-sell').textContent = (d.hawkes_sell||0).toFixed(2);
      const ratio = d.hawkes_ratio !== undefined ? d.hawkes_ratio : 0.5;
      document.getElementById('hk-ratio').textContent = (ratio * 100).toFixed(0) + '% Buy';
      document.getElementById('hk-bar').style.width = Math.round(ratio * 100) + '%';

      // Pillar 4: OFI Levels
      document.getElementById('ofi-mean').textContent = (d.ofi_mean||0).toFixed(3);
      const ofiStack = document.getElementById('ofi-stack');
      const levels = d.ofi_levels && d.ofi_levels.length === 5 ? d.ofi_levels : [0,0,0,0,0];
      ofiStack.innerHTML = levels.map((lvl, idx) => {
        const val = Number(lvl) || 0;
        const isPos = val >= 0;
        const width = Math.min(50, Math.abs(val) * 50);
        const left = isPos ? '50%' : (50 - width) + '%';
        const color = isPos ? 'var(--accent-cyan)' : 'var(--accent-red)';
        return `
          <div class="ofi-level-row">
            <span class="ofi-level-lbl">L${idx+1}</span>
            <div class="ofi-bar-wrap">
              <div class="ofi-mid-line"></div>
              <div class="ofi-bar-fill" style="left: ${left}; width: ${width}%; background: ${color};"></div>
            </div>
            <span class="ofi-level-val" style="color: ${color};">${(val > 0 ? '+' : '') + val.toFixed(2)}</span>
          </div>
        `;
      }).join('');

      // Pillar 5: Active Position
      const posContainer = document.getElementById('position-container');
      if (d.position) {
        const p = d.position;
        const isLong = p.side === 'LONG' || p.side === 'long';
        posContainer.innerHTML = `
          <div class="pos-box">
            <div class="pos-header">
              <div style="display: flex; align-items: center; gap: 8px;">
                <span class="${isLong ? 'tag-buy' : 'tag-sell'}">${p.side.toUpperCase()}</span>
                <span style="font-family: var(--mono); font-weight: 700;">${p.qty} BTC (${p.leverage||1}x)</span>
              </div>
              <span style="font-family: var(--mono); font-weight: 700; color: ${p.unrealized_pnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)'}">
                ${fmtMoney(p.unrealized_pnl)} USDT (${fmtMoney(p.unrealized_pnl_pct)}%)
              </span>
            </div>
            <div class="metric-row">
              <span class="k">Entry Price</span>
              <span class="v">$${(p.entry_price||0).toFixed(1)}</span>
            </div>
            <div class="metric-row">
              <span class="k">Chandelier Ratchet Stop</span>
              <span class="v" style="color: var(--accent-gold);">$${(p.stop_price||0).toFixed(1)} (${p.ratchet_mult||3.0}x ATR)</span>
            </div>
          </div>
        `;
      } else {
        posContainer.innerHTML = `
          <div style="text-align: center; color: var(--text-muted); font-size: 13px; padding: 16px;">
            Scanning L2 Orderbook for Aggressive Alpha Triggers (No Active Position)
          </div>
        `;
      }

      // Trade Flow Diagnostics
      if (d.trade_flow) {
        const tf = d.trade_flow;
        const idleEl = document.getElementById('flow-idle');
        if (idleEl) idleEl.textContent = (tf.idle_minutes || 0) + 'm';
        const stEl = document.getElementById('flow-selftest');
        if (stEl) {
          stEl.textContent = tf.self_test_passed ? 'PASSED (0 Bugs)' : 'FAILED';
          stEl.style.color = tf.self_test_passed ? 'var(--accent-green)' : 'var(--accent-red)';
        }
        const mcEl = document.getElementById('flow-maxconf');
        if (mcEl) mcEl.textContent = ((tf.max_confidence_seen || 0) * 100).toFixed(1) + '% (Req: >60%)';
        const qEl = document.getElementById('flow-quiet');
        if (qEl) qEl.textContent = (tf.hawkes_quiet_pct || 0) + '% of ticks';
        const diagEl = document.getElementById('flow-diagnosis');
        if (diagEl) diagEl.textContent = tf.diagnosis || 'Scanning market';
        const badge = document.getElementById('flow-status-badge');
        if (badge) {
          badge.textContent = tf.status || 'NOMINAL';
          badge.style.color = tf.status && tf.status.includes('FAULT') ? 'var(--accent-red)' : 'var(--accent-green)';
        }
      }

      // Logs Terminal
      const terminal = document.getElementById('log-terminal');
      if (d.recent_logs && d.recent_logs.length) {
        terminal.innerHTML = d.recent_logs.map(log => `
          <div class="log-line">
            <span class="log-time">[${log.time || '--:--:--'}]</span>
            <span class="log-badge badge-${log.type || 'INFO'}">${log.type || 'INFO'}</span>
            <span>${log.text}</span>
          </div>
        `).join('');
      } else {
        terminal.innerHTML = `<div class="log-line" style="color: var(--text-muted)">[System Active] Waiting for HFT events...</div>`;
      }
    }

    // Auto-refresh every 1000ms
    fetchHftState();
    setInterval(fetchHftState, 1000);
  </script>
</body>
</html>
"""


def _render_login(err: str = "") -> str:
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>HFT Quant Desk · Login</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: #08090C; color: #F0F3F8;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      display: flex; align-items: center; justify-content: center;
      min-height: 100vh; padding: 20px;
    }}
    .box {{
      width: 100%; max-width: 320px;
      background: #0E1117; border: 1px solid #1B2234;
      border-radius: 12px; padding: 24px; text-align: center;
    }}
    .logo {{
      width: 48px; height: 48px; border-radius: 10px;
      background: #D4AF37; color: #000; font-weight: 800;
      font-size: 20px; display: flex; align-items: center; justify-content: center;
      margin: 0 auto 12px;
    }}
    h1 {{ font-size: 16px; margin-bottom: 4px; }}
    p {{ font-size: 12px; color: #7E8B9F; margin-bottom: 20px; }}
    input {{
      width: 100%; padding: 12px; border-radius: 8px;
      border: 1px solid #1B2234; background: #060709;
      color: #FFF; font-size: 14px; text-align: center;
      outline: none; margin-bottom: 12px;
    }}
    button {{
      width: 100%; padding: 12px; border-radius: 8px;
      border: none; background: #D4AF37; color: #000;
      font-weight: 600; font-size: 14px; cursor: pointer;
    }}
    .err {{ color: #FF2E54; font-size: 12px; margin-top: 12px; min-height: 16px; }}
  </style>
</head>
<body>
  <div class="box">
    <div class="logo">Q</div>
    <h1>HFT Quant Terminal</h1>
    <p>5-Pillar Engine · Secure Access</p>
    <form method="POST" action="/login">
      <input type="password" name="token" placeholder="Access Token" autofocus required>
      <button type="submit">Enter Terminal</button>
    </form>
    <div class="err">{err}</div>
  </div>
</body>
</html>
"""


class HFTHandler(BaseHTTPRequestHandler):
    def _is_authed(self) -> bool:
        q = parse_qs(urlparse(self.path).query)
        token_param = (q.get("t") or [""])[0]
        if token_param and token_param == TOKEN:
            return True
        if token_param and SESSIONS.get(token_param, 0) > time.time():
            return True

        cookie_hdr = self.headers.get("Cookie", "")
        for part in cookie_hdr.split(";"):
            part = part.strip()
            if part.startswith("hft_s="):
                s_val = part.split("=", 1)[1]
                if SESSIONS.get(s_val, 0) > time.time():
                    return True
        return False

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # 1. Kill old service worker from previous apps immediately
        if path == "/sw.js":
            body = (
                b"self.addEventListener('install', e => self.skipWaiting());\n"
                b"self.addEventListener('activate', e => {\n"
                b"  e.waitUntil(caches.keys().then(keys => Promise.all(keys.map(k => caches.delete(k)))));\n"
                b"  self.registration.unregister();\n"
                b"});\n"
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(body)
            return

        # 2. Public API endpoint for HFT metrics (polled by UI)
        if path == "/api/hft":
            data = _read_hft_state()
            payload = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
            return

        # 3. Static Icons / Assets
        if path in ("/icon-192.png", "/icon-180.png", "/logo.png", "/favicon.ico"):
            self.send_response(204)
            self.end_headers()
            return

        # 4. Auth check for Dashboard
        if not self._is_authed():
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_render_login().encode("utf-8"))
            return

        # 5. Serve Terminal Dashboard
        body = _render_hft_terminal().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/login":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode("utf-8")
            form = parse_qs(raw)
            token = (form.get("token") or [""])[0].strip()

            if token == TOKEN:
                sid = secrets.token_urlsafe(24)
                SESSIONS[sid] = time.time() + SESSION_TTL
                self.send_response(302)
                self.send_header("Location", f"/?t={sid}")
                self.send_header("Set-Cookie", f"hft_s={sid}; Path=/; Max-Age={SESSION_TTL}; SameSite=Lax; Secure")
                self.end_headers()
            else:
                body = _render_login(err="Invalid Token").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body)
            return

        self.send_response(404)
        self.end_headers()


def run_app():
    server = ThreadingHTTPServer((HOST, PORT), HFTHandler)
    if os.path.exists(CERT) and os.path.exists(KEY):
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(certfile=CERT, keyfile=KEY)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        proto = "https"
    else:
        proto = "http"

    print(f"HFT Terminal running on {proto}://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    run_app()
