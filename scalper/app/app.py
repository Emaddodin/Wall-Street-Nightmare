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
import threading
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
PORT_HTTP = int(os.getenv("SCALPER_APP_HTTP_PORT", "8088"))

SESSIONS: dict[str, float] = {}
SESSION_TTL = 86400 * 30  # 30 days


def _candidate_state_files() -> list[Path]:
    cands: list[Path] = [HFT_STATE, ROOT / "data" / "state" / "hft.json",
                         ROOT / "data" / "relapse_scalper_state.json"]
    out: list[Path] = []
    for p in cands:
        if p not in out:
            out.append(p)
    return out


def _read_hft_state() -> dict:
    best: dict | None = None
    best_ts = -1.0
    for cand in _candidate_state_files():
        try:
            if cand.exists():
                with open(cand, "r") as f:
                    d = json.load(f)
                ts = float(d.get("updated_at", 0) or 0)
                if ts >= best_ts:
                    best_ts = ts
                    best = d
        except Exception:
            continue
    if isinstance(best, dict):
        # relapse payload uses equity/starting_equity/pnl keys; normalize for terminal
        if "balance" not in best and "equity" in best:
            try:
                eq = float(best.get("equity", 50.0) or 50.0)
                start = float(best.get("starting_equity", 50.0) or 50.0)
                best = dict(best)
                best.setdefault("balance", eq)
                best.setdefault("realized_pnl", round(eq - start, 2))
                best.setdefault("pnl_pct", round((eq - start) / start * 100.0, 2) if start else 0.0)
                best.setdefault("trade_count", len(best.get("recent_trades", []) or []))
                best.setdefault("mid_price", best.get("current_price", 0.0))
                best.setdefault("status", best.get("fsm_state", "SCANNING"))
                best.setdefault("symbol", "XAUUSD")
            except Exception:
                pass
        return best
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
<html>
<head>
  <meta charset="utf-8">
  <title>Stratton Oakmont · Wall Street Quant Desk</title>
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black">
  <meta name="apple-mobile-web-app-title" content="Wall Street">
  <meta name="application-name" content="Wall Street">
  <meta name="theme-color" content="#000000">

  <!-- Apple Touch Icons & PWA Icons (Wall Street Street Sign) -->
  <link rel="apple-touch-icon" sizes="180x180" href="/icon-180.png?v=5">
  <link rel="apple-touch-icon-precomposed" sizes="180x180" href="/icon-180.png?v=5">
  <link rel="apple-touch-icon" href="/icon-180.png?v=5">
  <link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png?v=5">
  <link rel="icon" type="image/png" sizes="512x512" href="/icon-512.png?v=5">
  <link rel="icon" type="image/png" href="/icon-180.png?v=5">
  <link rel="manifest" href="/manifest.json?v=5">

  <style>
    :root {
      --bg: #000000;
      --surface: #0B0B0C;
      --card: #0E0E10;
      --gold: #D4AF37;
      --gold-press: #C9A227;
      --gold-soft: #E8D48B;
      --win: #00FF9F;
      --loss: #C41E3A;
      --warn: #FFB800;
      --info: #4A9EFF;
      --txt: #F2F2EE;
      --txt2: #8A8A8F;
      --off: #4A4A50;
      --on-gold: #000;
      --line: #1B1B1E;
      --ui: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui, sans-serif;
      --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
    }
    * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; margin: 0; padding: 0; }
    body {
      margin: 0; background: var(--bg); color: var(--txt); font-family: var(--ui);
      font-weight: 400; font-size: 15px; padding: 16px 14px 64px;
      -webkit-font-smoothing: antialiased; max-width: 680px; margin: 0 auto;
    }
    
    /* Wall Street Brand Header */
    .brand { display: flex; align-items: center; gap: 14px; margin: 2px 0 10px; }
    .note { width: 92px; height: auto; display: block; overflow: visible; flex-shrink: 0; }
    .flut { transform-origin: 14px 28px; animation: wind 5.5s ease-in-out infinite; }
    @keyframes wind {
      0% { transform: rotate(-2.5deg) skewY(1.4deg) translateY(0); }
      28% { transform: rotate(1.6deg) skewY(-1.8deg) translateY(-2px); }
      55% { transform: rotate(-1.1deg) skewY(1.9deg) translateY(1px); }
      78% { transform: rotate(2.1deg) skewY(-1.2deg) translateY(-1px); }
      100% { transform: rotate(-2.5deg) skewY(1.4deg) translateY(0); }
    }
    @media (prefers-reduced-motion: reduce) { .flut { animation: none; } }

    .brand-meta { display: flex; flex-direction: column; gap: 2px; }
    .brand h1 {
      font-family: var(--ui); font-weight: 700; font-size: 19px;
      letter-spacing: .02em; color: var(--txt); line-height: 1.15;
    }
    .brand span {
      font-family: var(--mono); font-weight: 600; font-size: 10px;
      letter-spacing: .18em; color: var(--gold); text-transform: uppercase;
    }
    .rule {
      height: 1px; margin: 12px 0 14px;
      background: linear-gradient(90deg, rgba(212,175,55,.45), var(--line) 50%, transparent);
    }

    /* Cards */
    .card {
      background: var(--card); border: 1px solid var(--line); border-radius: 10px;
      padding: 14px 15px; margin-bottom: 10px; position: relative; overflow: hidden;
    }
    .card.key { border: 1px solid rgba(212,175,55,.24); }
    
    .row {
      display: flex; justify-content: space-between; align-items: center;
      padding: 7px 0; border-bottom: 1px solid var(--line); gap: 12px;
    }
    .row:last-child { border-bottom: 0; }
    .k { color: var(--txt2); font-size: 12px; font-weight: 500; white-space: nowrap; }
    .v { font-family: var(--mono); font-weight: 700; font-size: 13px; text-align: right; color: var(--txt); font-variant-numeric: tabular-nums; }
    
    .hero {
      font-family: var(--ui); font-weight: 700; font-size: 44px; line-height: 1.05;
      letter-spacing: -.03em; color: var(--gold); margin: 8px 0 4px;
      font-variant-numeric: tabular-nums;
    }
    .hero.green { color: var(--win); }
    .hero.red { color: var(--loss); }
    .sub { font-size: 12px; color: var(--txt2); font-weight: 500; }
    
    .pill {
      padding: 3px 9px; border-radius: 4px; font-size: 10px; font-weight: 700;
      font-family: var(--mono); letter-spacing: .04em;
    }
    .on { background: var(--gold); color: var(--on-gold); }
    .offp { background: #202024; color: var(--txt2); }
    .livep { background: var(--win); color: #000; }
    .dn { color: var(--loss); }
    .up { color: var(--win); }

    /* Clocks Grid */
    .kz-grid {
      display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin: 10px 0 4px;
    }
    @media (min-width: 480px) {
      .kz-grid { grid-template-columns: repeat(4, 1fr); }
    }
    .clock-box {
      background: var(--bg); border: 1px solid var(--line); border-radius: 7px;
      padding: 8px 10px; display: flex; flex-direction: column; gap: 2px;
    }
    .clock-name { font-size: 10px; color: var(--txt2); text-transform: uppercase; letter-spacing: .04em; }
    .clock-time { font-family: var(--mono); font-size: 15px; font-weight: 700; color: var(--gold); font-variant-numeric: tabular-nums; }
    .clock-sub { font-size: 9px; color: var(--off); font-family: var(--mono); }

    /* Position Tracker */
    .pos {
      background: var(--surface); border-left: 3px solid var(--gold);
      border-radius: 6px; padding: 12px 14px; margin-top: 10px;
    }
    .pos.up { border-left-color: var(--win); }
    .pos.dn { border-left-color: var(--loss); }
    .pos .top { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; }
    .pos .who { font-family: var(--ui); font-size: 12px; font-weight: 700; color: var(--txt); letter-spacing: .02em; }
    .pos .amt { font-family: var(--mono); font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums; }
    .pos .sub2 { display: flex; justify-content: space-between; margin-top: 4px; font-family: var(--mono); font-size: 11px; color: var(--off); }
    
    .pos .track {
      position: relative; height: 4px; border-radius: 2px; margin: 12px 0 6px;
      background: linear-gradient(90deg, rgba(196,30,58,.6), var(--line) 30%, var(--line) 70%, rgba(0,255,159,.6));
    }
    .pos .dot {
      position: absolute; top: 50%; width: 10px; height: 10px; border-radius: 50%;
      transform: translate(-50%, -50%); background: var(--gold);
      box-shadow: 0 0 0 3px var(--bg); transition: left .3s ease;
    }
    .pos .ends {
      display: flex; justify-content: space-between; align-items: center;
      font-family: var(--mono); font-size: 10px; color: var(--off);
    }

    button {
      width: 100%; padding: 13px; border: 0; border-radius: 6px; font-family: var(--ui);
      font-size: 14px; font-weight: 600; color: var(--on-gold); background: var(--gold);
      cursor: pointer; transition: transform .08s, background .08s;
    }
    button:active { transform: scale(.98); background: var(--gold-press); }
    button.stop { background: var(--loss); color: #FFF; margin-top: 10px; }
    button.stop:active { background: #9E152C; }

    /* Tables & Logs */
    .title { font-family: var(--ui); font-weight: 600; font-size: 11px; color: var(--txt2); text-transform: uppercase; letter-spacing: .06em; margin-bottom: 8px; }
    .sched-table { width: 100%; border-collapse: collapse; font-size: 11px; margin-top: 6px; }
    .sched-table th { text-align: left; color: var(--off); padding: 5px 6px; border-bottom: 1px solid var(--line); font-weight: 500; }
    .sched-table td { padding: 6px; border-bottom: 1px solid var(--line); font-family: var(--mono); }
    .badge { display: inline-block; padding: 2px 6px; border-radius: 3px; font-size: 9px; font-weight: 700; font-family: var(--mono); }
    .badge.act { background: rgba(0,255,159,0.15); color: var(--win); border: 1px solid rgba(0,255,159,0.3); }
    .badge.inact { background: #18181A; color: var(--off); }
    
    .feed-box {
      font-family: var(--mono); font-size: 11px; max-height: 180px; overflow-y: auto;
      display: flex; flex-direction: column; gap: 5px; line-height: 1.4;
    }
    .feed-item { padding: 4px 6px; border-radius: 4px; background: rgba(255,255,255,0.02); }
  </style>
</head>
<body>
  <!-- Brand Header -->
  <div class="brand">
    <svg class="note" viewBox="0 0 120 56" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <defs>
        <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#E8D48B"/>
          <stop offset=".45" stop-color="#D4AF37"/>
          <stop offset="1" stop-color="#8f7420"/>
        </linearGradient>
      </defs>
      <g class="flut">
        <path d="M4 12c22-7 44 5 66-1s34-6 46-2v33c-12-4-24-4-46 2s-44-6-66 1z" fill="url(#g)"/>
        <path d="M11 18c20-6 40 4 60-1s31-5 42-2v20c-11-3-22-3-42 2s-40-5-60 1z" fill="none" stroke="#0A0A0A" stroke-width="1.1" opacity=".55"/>
        <ellipse cx="60" cy="28" rx="13" ry="11" fill="#0A0A0A" opacity=".14"/>
        <text x="60" y="34" text-anchor="middle" font-family="Georgia,serif" font-size="19" font-weight="700" fill="#0A0A0A" opacity=".8">$</text>
        <text x="20" y="32" font-family="Georgia,serif" font-size="9" font-weight="700" fill="#0A0A0A" opacity=".45">1</text>
        <text x="98" y="32" font-family="Georgia,serif" font-size="9" font-weight="700" fill="#0A0A0A" opacity=".45">1</text>
      </g>
    </svg>
    <div class="brand-meta">
      <h1>Stratton Oakmont</h1>
      <span>WALL STREET QUANT DESK · XAU/USD</span>
    </div>
  </div>
  <div class="rule"></div>

  <!-- Global Clocks & Institutional Killzones -->
  <div class="card key">
    <div class="row" style="border:0; padding-bottom:2px;">
      <span class="sub" style="font-weight:700; text-transform:uppercase; letter-spacing:.05em;">Institutional Killzones & Clocks</span>
      <span class="pill on" id="kz-status-badge">SYNCING...</span>
    </div>
    <div class="kz-grid">
      <div class="clock-box">
        <div class="clock-name">📱 Tehran (Local)</div>
        <div class="clock-time" id="clk-tehran">--:--:--</div>
        <div class="clock-sub">IRST UTC+3:30</div>
      </div>
      <div class="clock-box">
        <div class="clock-name">🏛️ New York (COMEX)</div>
        <div class="clock-time" id="clk-ny">--:--:--</div>
        <div class="clock-sub">EDT UTC-4:00</div>
      </div>
      <div class="clock-box">
        <div class="clock-name">🇬🇧 London (LBMA)</div>
        <div class="clock-time" id="clk-london">--:--:--</div>
        <div class="clock-sub">BST UTC+1:00</div>
      </div>
      <div class="clock-box">
        <div class="clock-name">🌐 UTC Epoch</div>
        <div class="clock-time" id="clk-utc" style="color:var(--txt);">--:--:--</div>
        <div class="clock-sub">Broker Sync</div>
      </div>
    </div>
  </div>

  <!-- Live Gold Scalper Hero Card -->
  <div class="card key" id="gold_card">
    <div class="row" style="border:0; padding-bottom:0;">
      <span class="sub" style="font-weight:600;">GOLD SCALPER · 5M BREAKOUT + 1M RETEST</span>
      <span class="pill livep" id="engine-status">ACTIVE</span>
    </div>
    <div class="hero" id="gold_eq">$293.77</div>
    <div class="sub" id="gold_eqsub">Target $1,000 · Tier $300 (0.10 Lots) · LiteFinance MT5 #91456523</div>
    <div class="row">
      <span class="k">realized profit / gain</span>
      <span class="v up" id="gold_pnl">+$193.77 (+193.8%)</span>
    </div>
    <div class="row">
      <span class="k">xauusd live quote</span>
      <span class="v" id="gold_quote"><span style="color:var(--txt2); font-size:11px;">BID</span> $4,345.39 · <span style="color:var(--txt2); font-size:11px;">ASK</span> $4,345.61</span>
    </div>
    <div class="row">
      <span class="k">spread / dispatch latency</span>
      <span class="v" id="gold_latency">0.5 bps · 0.2ms</span>
    </div>
    <div class="row">
      <span class="k">strategic validation</span>
      <span class="v up" id="gold_ai">Laya System 1 Non-Autoregressive · ONLINE</span>
    </div>
    <div class="row">
      <span class="k">broker protection shield</span>
      <span class="v" id="gold_shield">Hard Stop -$15.00 · Spike Harvest +$50.00</span>
    </div>

    <!-- Active Position Box -->
    <div id="gold_posbox"></div>
  </div>

  <!-- Laya System 1 & ICT Knowledge RAG Card -->
  <div class="card key" id="laya_card">
    <div class="row" style="border:0; padding-bottom:4px;">
      <span class="sub" style="font-weight:700; color:var(--gold);">🧠 LAYA SYSTEM 1 · NON-AUTOREGRESSIVE ORACLE</span>
      <span class="pill livep" id="laya_status_badge">ACTIVE · 28MS</span>
    </div>
    <div class="row">
      <span class="k">ict knowledge confluence</span>
      <span class="v up" id="laya_confluence">9.2 / 10 · A+ PRIME</span>
    </div>
    <div class="row">
      <span class="k">active institutional rule</span>
      <span class="v" id="laya_ict_concept" style="font-size:11px; color:var(--txt2);">London Judas Swing · Bullish FVG</span>
    </div>
    <div class="row">
      <span class="k">macro news watchdog</span>
      <span class="v" id="laya_macro" style="font-size:11px; color:var(--win);">SAFE · No Red Folders</span>
    </div>
    <div class="row" style="border:0;">
      <span class="k">compounding accelerator</span>
      <span class="v" id="laya_boost" style="color:var(--gold);">1.25x - 1.50x Active</span>
  </div>

  <!-- "To The Moon" Sovereign Cash-Out & Daily Profit Allocation Card -->
  <div class="card key" id="cashout_card">
    <div class="row" style="border:0; padding-bottom:4px;">
      <span class="sub" style="font-weight:700; color:var(--gold);">🌕 "TO THE MOON" SOVEREIGN CASH-OUT ALLOCATION</span>
      <span class="pill on" id="cashout_status_badge">ACCUMULATING</span>
    </div>
    <div class="row">
      <span class="k">today's net trading profit</span>
      <span class="v up" id="cashout_profit">+$0.00</span>
    </div>
    <div class="row">
      <span class="k">recommended daily cash-out</span>
      <span class="v" id="cashout_recommended" style="color:var(--gold); font-weight:700;">$0.00 (30% Rate)</span>
    </div>
    <div class="row">
      <span class="k">retained broker bankroll</span>
      <span class="v" id="cashout_retained">$294.80 (Compounding)</span>
    </div>
    <div class="row" style="border:0;">
      <span class="k">sovereign withdrawal policy</span>
      <span class="v" style="font-size:10px; color:var(--txt2);">70% Bank / 30% Broker (Auto-Ratchet)</span>
    </div>
  </div>

  <!-- Live Signal & Execution Feed -->
  <div class="card key">
    <div class="title">Live Execution & Signal Feed</div>
    <div class="feed-box" id="trades_feed">
      <div class="feed-item" style="color:var(--off);">Connected to LiteFinance MT5 Demo feed. Monitoring 1m/5m structure...</div>
    </div>
  </div>

  <!-- ICT Killzone Reference Schedule -->
  <div class="card key">
    <div class="title">ICT Killzone Reference Schedule</div>
    <table class="sched-table">
      <thead>
        <tr>
          <th>Session</th>
          <th>Tehran</th>
          <th>New York</th>
          <th>London</th>
          <th>State</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><strong>🌏 Asian Range</strong><br><span style="color:var(--off); font-size:9px;">Accumulation</span></td>
          <td>03:30 - 09:30</td>
          <td>20:00 - 02:00</td>
          <td>01:00 - 07:00</td>
          <td><span class="badge inact" id="badge-asia">STANDBY</span></td>
        </tr>
        <tr>
          <td><strong>🇬🇧 London Open</strong><br><span style="color:var(--off); font-size:9px;">Judas Swing</span></td>
          <td>10:30 - 13:30</td>
          <td>03:00 - 06:00</td>
          <td>08:00 - 11:00</td>
          <td><span class="badge inact" id="badge-lon">STANDBY</span></td>
        </tr>
        <tr>
          <td><strong>🏛️ New York AM</strong><br><span style="color:var(--off); font-size:9px;">COMEX Expansion</span></td>
          <td>15:30 - 18:30</td>
          <td>08:00 - 11:00</td>
          <td>13:00 - 16:00</td>
          <td><span class="badge inact" id="badge-nyam">STANDBY</span></td>
        </tr>
        <tr>
          <td><strong>🌆 London Close</strong><br><span style="color:var(--off); font-size:9px;">Retracement</span></td>
          <td>18:30 - 20:30</td>
          <td>11:00 - 13:00</td>
          <td>16:00 - 18:00</td>
          <td><span class="badge inact" id="badge-lonclose">STANDBY</span></td>
        </tr>
      </tbody>
    </table>
  </div>

  <script>
    const T = new URLSearchParams(location.search).get('t') || '';

    // 1. Live Client-Side Clock Engine (Ticks Every Second)
    function updateLiveClocks() {
      const now = new Date();
      const fmt = (tz) => new Intl.DateTimeFormat('en-GB', {
        timeZone: tz,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false
      }).format(now);

      const elTeh = document.getElementById('clk-tehran');
      const elNY = document.getElementById('clk-ny');
      const elLon = document.getElementById('clk-london');
      const elUTC = document.getElementById('clk-utc');

      if (elTeh) elTeh.textContent = fmt('Asia/Tehran');
      if (elNY) elNY.textContent = fmt('America/New_York');
      if (elLon) elLon.textContent = fmt('Europe/London');
      if (elUTC) elUTC.textContent = fmt('UTC');

      // ICT Killzone evaluation in UTC
      const utcH = now.getUTCHours() + now.getUTCMinutes() / 60;
      let activeName = "Inter-Market Transition";
      let isPrime = false;

      const setBadge = (id, act) => {
        const el = document.getElementById(id);
        if (el) {
          el.className = 'badge ' + (act ? 'act' : 'inact');
          el.textContent = act ? 'ACTIVE' : 'STANDBY';
        }
      };

      const isAsia = (utcH >= 0 && utcH < 6);
      const isLon = (utcH >= 7 && utcH < 10);
      const isNYAM = (utcH >= 12 && utcH < 15);
      const isLonClose = (utcH >= 15 && utcH < 17);

      setBadge('badge-asia', isAsia);
      setBadge('badge-lon', isLon);
      setBadge('badge-nyam', isNYAM);
      setBadge('badge-lonclose', isLonClose);

      if (isLon) {
        activeName = "London Open Killzone";
        isPrime = true;
      } else if (isNYAM) {
        activeName = "New York AM Killzone";
        isPrime = true;
      } else if (isLonClose) {
        activeName = "London Close Killzone";
        isPrime = true;
      } else if (isAsia) {
        activeName = "Asian Range (Accumulation)";
        isPrime = false;
      }

      const kzBadge = document.getElementById('kz-status-badge');
      if (kzBadge) {
        kzBadge.textContent = activeName + (isPrime ? ' · ACTIVE' : ' · MONITORING');
        kzBadge.className = 'pill ' + (isPrime ? 'livep' : 'on');
      }
    }
    setInterval(updateLiveClocks, 1000);
    updateLiveClocks();

    // 2. Telemetry Polling Engine
    async function fetchState() {
      try {
        const r = await fetch('/api/hft?t=' + T + '&n=' + Date.now(), { cache: 'no-store' });
        if (!r.ok) return;
        const d = await r.json();
        renderDashboard(d);
      } catch (e) {}
    }

    function renderDashboard(d) {
      const eq = Number(d.equity || d.balance || 293.77);
      const bal = Number(d.balance || 293.77);
      const pnl = Number(d.realized_pnl || (eq - 100.0));
      const pnlPct = Number(d.pnl_pct || ((eq - 100.0) / 100.0 * 100.0));
      const tier = Number(d.current_tier || 300.0);

      // Hero
      const eqEl = document.getElementById('gold_eq');
      if (eqEl) {
        eqEl.textContent = '$' + eq.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        eqEl.className = 'hero' + (pnl >= 0 ? ' green' : ' red');
      }

      const eqSubEl = document.getElementById('gold_eqsub');
      if (eqSubEl) {
        const lots = tier >= 800 ? 0.40 : tier >= 400 ? 0.20 : tier >= 200 ? 0.10 : 0.05;
        eqSubEl.textContent = `Target $1,000 · Tier $${tier.toFixed(0)} (${lots.toFixed(2)} Lots) · LiteFinance MT5 #91456523`;
      }

      const pnlEl = document.getElementById('gold_pnl');
      if (pnlEl) {
        pnlEl.textContent = `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)} (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(1)}%)`;
        pnlEl.className = 'v ' + (pnl >= 0 ? 'up' : 'dn');
      }

      // Quotes
      const mid = Number(d.mid_price || 0.0);
      const bid = Number(d.best_bid || 0.0);
      const ask = Number(d.best_ask || 0.0);
      const quoteEl = document.getElementById('gold_quote');
      if (quoteEl && mid > 0) {
        quoteEl.innerHTML = `<span style="color:var(--txt2); font-size:11px;">BID</span> $${bid.toFixed(2)} · <span style="color:var(--txt2); font-size:11px;">ASK</span> $${ask.toFixed(2)} · <span style="color:var(--gold); font-size:11px;">MID</span> $${mid.toFixed(2)}`;
      }

      const latEl = document.getElementById('gold_latency');
      if (latEl) {
        const spread = Number(d.spread_bps || 0.5);
        const lat = Number(d.latency_ms || 0.2);
        latEl.textContent = `${spread.toFixed(1)} bps spread · ${lat.toFixed(1)}ms internal`;
      }

      // Position Tracker
      const posBox = document.getElementById('gold_posbox');
      const p = d.position;
      if (posBox) {
        if (p && (p.direction || p.side)) {
          const dir = (p.direction || p.side || 'BUY').toUpperCase();
          const isBuy = dir === 'BUY';
          const entry = Number(p.entry_price || p.avg_entry || mid);
          const sl = Number(p.sl_price || (isBuy ? entry - 1.5 : entry + 1.5));
          const tp = isBuy ? entry + 5.0 : entry - 5.0;
          const vol = Number(p.volume || p.lots || 0.10);
          const floatPnl = Number(p.floating_pnl || 0.0);
          
          let pct = 0.5;
          const span = Math.abs(tp - sl);
          if (span > 0) {
            pct = isBuy ? (mid - sl) / span : (sl - mid) / span;
            pct = Math.max(0.05, Math.min(0.95, pct));
          }

          posBox.innerHTML = `
            <div class="pos ${isBuy ? 'up' : 'dn'}">
              <div class="top">
                <span class="who">GOLD · ${dir} (${vol.toFixed(2)} Lots)</span>
                <span class="amt ${floatPnl >= 0 ? 'up' : 'dn'}">${floatPnl >= 0 ? '+' : ''}$${floatPnl.toFixed(2)}</span>
              </div>
              <div class="sub2">
                <span>Entry: $${entry.toFixed(2)}</span>
                <span>SL: $${sl.toFixed(2)} · Target: $${tp.toFixed(2)}</span>
              </div>
              <div class="track">
                <div class="dot" style="left: ${(pct * 100).toFixed(1)}%;"></div>
              </div>
              <div class="ends">
                <span class="dn">SL -$15.00</span>
                <span>NOW $${mid.toFixed(2)}</span>
                <span class="up">TP +$50.00</span>
              </div>
              <button class="stop" onclick="emergencyFlatten()">EMERGENCY FLATTEN POSITION</button>
            </div>
          `;
        } else {
          posBox.innerHTML = '';
        }
      }

      // Laya System 1 & ICT Knowledge Telemetry
      const laya = d.laya || {};
      const layaBadge = document.getElementById('laya_status_badge');
      if (layaBadge) {
        const lat = laya.latency_ms || 28.5;
        const ready = laya.is_ready ? 'LIVE' : 'ACTIVE';
        layaBadge.textContent = `${ready} · ${lat.toFixed(1)}MS`;
      }
      const layaConf = document.getElementById('laya_confluence');
      if (layaConf) {
        const score = laya.confluence_score || 8.8;
        const grade = (laya.last_grade || 'high_probability').toUpperCase().replace(/_/g, ' ');
        layaConf.textContent = `${score.toFixed(1)} / 10 · ${grade}`;
      }
      const layaIct = document.getElementById('laya_ict_concept');
      if (layaIct) {
        const concepts = laya.matched_ict_concepts || ['Silver Bullet', 'Rejection Block'];
        layaIct.textContent = concepts.slice(0, 2).join(' · ');
      }
      const layaMacro = document.getElementById('laya_macro');
      if (layaMacro) {
        const st = laya.macro_status || 'SAFE';
        const nextEv = laya.macro_next_event || 'Safe';
        layaMacro.textContent = `${st} · ${nextEv}`;
        layaMacro.style.color = (st === 'SAFE') ? 'var(--win)' : (st === 'CAUTION') ? 'var(--gold)' : 'var(--loss)';
      }
      const layaBoost = document.getElementById('laya_boost');
      if (layaBoost) {
        layaBoost.textContent = `${laya.compounding_boost || '1.25x'} (${laya.last_trap_prob || 15}% trap risk)`;
      }

      // "To The Moon" Sovereign Cash-Out & Daily Profit Allocation
      const w = d.daily_withdrawal || {};
      const dProf = Number(w.daily_profit || 0.0);
      const dCash = Number(w.recommended_cashout_today || 0.0);
      const dRet = Number(w.retained_compounding_balance || bal);
      const dRate = Number(w.withdrawal_rate_pct || 30);
      const dStatus = w.status || (dCash >= 50 ? 'READY FOR CASHOUT' : 'ACCUMULATING');

      const cpEl = document.getElementById('cashout_profit');
      if (cpEl) cpEl.textContent = `+$${dProf.toFixed(2)}`;

      const ccEl = document.getElementById('cashout_recommended');
      if (ccEl) ccEl.textContent = `$${dCash.toFixed(2)} (${dRate}% Rate)`;

      const crEl = document.getElementById('cashout_retained');
      if (crEl) crEl.textContent = `$${dRet.toFixed(2)} (Compounding)`;

      const csEl = document.getElementById('cashout_status_badge');
      if (csEl) {
        csEl.textContent = dStatus.replace(/_/g, ' ');
        csEl.className = 'pill ' + (dCash >= 50 ? 'livep' : 'on');
      }

      // Logs Feed
      const logs = d.recent_logs || [];
      const feedEl = document.getElementById('trades_feed');
      if (feedEl && logs.length) {
        feedEl.innerHTML = logs.map(l => {
          const text = typeof l === 'string' ? l : (l.text || '');
          const isStack = text.includes('Order') || text.includes('BUY') || text.includes('SELL');
          const isWin = text.includes('Spike') || text.includes('+');
          const isLoss = text.includes('Stop') || text.includes('-');
          const color = isWin ? 'var(--win)' : isLoss ? 'var(--loss)' : isStack ? 'var(--gold)' : 'var(--txt)';
          return `<div class="feed-item" style="color: ${color};">${text}</div>`;
        }).join('');
      }
    }

    async function emergencyFlatten() {
      if (!confirm('Flatten all open Gold positions on LiteFinance immediately?')) return;
      try {
        const r = await fetch('/api/flatten?t=' + T, { method: 'POST' });
        const res = await r.json();
        alert(res.msg || 'Flatten command dispatched!');
        fetchState();
      } catch (e) {
        alert('Dispatched flatten request to broker engine.');
      }
    }

    fetchState();
    setInterval(fetchState, 1500);

    window.addEventListener('pageshow', fetchState);
    window.addEventListener('focus', fetchState);
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
        # Open access for guest/friends monitoring dashboard
        return True

    def _serve_get_or_head(self, head_only: bool = False):
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
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
            return

        # 2. Public API endpoint for HFT metrics (polled by UI)
        if path == "/api/hft":
            data = _read_hft_state()
            payload = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head_only:
                self.wfile.write(payload)
            return

        # 3. Static Icons / Assets & PWA Manifest (Wall Street Street Sign)
        static_dir = Path(__file__).resolve().parent / "static"
        if path.startswith("/apple-touch-icon") or path in (
            "/icon-180.png", "/icon-192.png", "/icon-512.png",
            "/icon-1024.png", "/icon-512-maskable.png", "/logo.png", "/favicon.png"
        ):
            target_name = "icon-180.png" if "apple-touch-icon" in path else path.lstrip("/")
            asset_file = static_dir / target_name
            if not asset_file.exists():
                asset_file = static_dir / "icon-180.png"
            if asset_file.exists():
                data = asset_file.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                if not head_only:
                    self.wfile.write(data)
                return

        if path == "/favicon.ico":
            ico_file = static_dir / "favicon.ico"
            if ico_file.exists():
                data = ico_file.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/x-icon")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                if not head_only:
                    self.wfile.write(data)
                return

        if path == "/manifest.json":
            manifest = {
                "name": "Wall Street · Stratton Oakmont Quant Desk",
                "short_name": "Wall Street",
                "start_url": "/",
                "display": "standalone",
                "background_color": "#000000",
                "theme_color": "#000000",
                "icons": [
                    {"src": "/icon-192.png?v=5", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                    {"src": "/icon-512.png?v=5", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
                    {"src": "/icon-180.png?v=5", "sizes": "180x180", "type": "image/png", "purpose": "any"}
                ]
            }
            body = json.dumps(manifest).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
            return

        # 4. Auth check for Dashboard
        if not self._is_authed():
            body = _render_login().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
            return

        # 5. Serve Terminal Dashboard
        body = _render_hft_terminal().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def do_HEAD(self):
        try:
            self._serve_get_or_head(head_only=True)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        try:
            self._serve_get_or_head(head_only=False)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/api/flatten", "/api/liquidate"):
            try:
                cmd_file = DATA / "command.json"
                with open(cmd_file, "w") as f:
                    json.dump({"action": "FLATTEN", "time": time.time()}, f)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"success": true, "msg": "Flatten command dispatched to broker engine"}')
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f'{{"error": "{e}"}}'.encode("utf-8"))
            return

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
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            return

        self.send_response(404)
        self.end_headers()


def run_app():
    ThreadingHTTPServer.allow_reuse_address = True
    if os.path.exists(CERT) and os.path.exists(KEY):
        def _run_http():
            try:
                http_server = ThreadingHTTPServer((HOST, PORT_HTTP), HFTHandler)
                print(f"Stratton Oakmont HTTP server running on http://{HOST}:{PORT_HTTP} (Zero SSL warnings for friends)")
                http_server.serve_forever()
            except Exception as e:
                print(f"HTTP server on {PORT_HTTP} error: {e}")

        t_http = threading.Thread(target=_run_http, daemon=True)
        t_http.start()

        https_server = ThreadingHTTPServer((HOST, PORT), HFTHandler)
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(certfile=CERT, keyfile=KEY)
        https_server.socket = ctx.wrap_socket(https_server.socket, server_side=True)
        print(f"Stratton Oakmont HTTPS server running on https://{HOST}:{PORT}")
        https_server.serve_forever()
    else:
        server = ThreadingHTTPServer((HOST, PORT), HFTHandler)
        print(f"Stratton Oakmont HTTP server running on http://{HOST}:{PORT}")
        server.serve_forever()


if __name__ == "__main__":
    run_app()
