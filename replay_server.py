"""
replay_server.py
================
FastAPI Real-Time Command Center and WebSocket Server for Market Replay.

Features:
- WebSocket endpoint `/ws/stream` for live sub-second streaming.
- REST endpoints `/api/state`, `/api/health`, `/api/candles`.
- Embedded High-Performance HTML5 / Tailwind Command Center UI.
- Header Badge: Pulsing `🟡 REPLAY (1x Speed): [Simulated Timestamp]`.
- -$10.00 Hard Equity Shield visual progress bar.
- Interactive candlestick chart with spam execution (5 slices) and exit markers.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger("replay_server")


class ReplayServerState:
    """Thread-safe state manager for the Replay Command Center."""

    def __init__(self) -> None:
        self.mode: str = "REPLAY"
        self.status: str = "WAITING_FOR_MIDNIGHT_UTC"
        self.speed: float = 1.0
        self.simulated_time_iso: str = "2026-09-17T00:00:00Z"
        self.simulated_timestamp: float = 0.0
        self.countdown_seconds: float = 0.0
        self.countdown_str: str = "00h 00m 00s"
        self.current_price: float = 2500.00
        self.equity_initial: float = 65.00
        self.equity_current: float = 65.00
        self.shield_threshold_usd: float = -10.00
        self.active_position: Dict[str, Any] = {
            "is_active": False,
            "direction": "FLAT",
            "entry_price": 0.0,
            "size": 0.0,
            "target_price": None,
            "stop_price": None,
            "upnl": 0.0,
            "upnl_pct": 0.0,
        }
        self.macro_edge: Dict[str, Any] = {
            "permit_trade": True,
            "bias": "BULLISH",
            "volatility_regime": 1.0,
            "last_updated": 0.0,
        }
        self.latest_candle: Optional[Dict[str, Any]] = None
        self.candles_history: List[Dict[str, Any]] = []
        self.recent_trades: List[Dict[str, Any]] = []
        self.markers: List[Dict[str, Any]] = []
        self.log_messages: List[Dict[str, Any]] = []

    def get_shield_progress_pct(self) -> float:
        """
        Calculates progress towards the -$10.00 equity shield limit.
        0% when uPnL >= 0, 100% when uPnL <= -$10.00.
        """
        upnl = self.active_position.get("upnl", 0.0)
        if upnl >= 0.0:
            return 0.0
        loss = abs(upnl)
        threshold = abs(self.shield_threshold_usd)
        return min(100.0, round((loss / threshold) * 100.0, 1))

    def to_dict(self) -> Dict[str, Any]:
        """Serializes current state snapshot to dictionary."""
        return {
            "mode": self.mode,
            "status": self.status,
            "speed": self.speed,
            "simulated_time_iso": self.simulated_time_iso,
            "simulated_timestamp": self.simulated_timestamp,
            "countdown_seconds": self.countdown_seconds,
            "countdown_str": self.countdown_str,
            "current_price": self.current_price,
            "equity": {
                "initial": self.equity_initial,
                "current": self.equity_current,
                "shield_threshold": self.shield_threshold_usd,
                "shield_progress_pct": self.get_shield_progress_pct(),
            },
            "active_position": self.active_position,
            "macro_edge": self.macro_edge,
            "latest_candle": self.latest_candle,
            "recent_trades": self.recent_trades[-10:],
            "markers": self.markers[-30:],
            "log_messages": self.log_messages[-20:],
        }


def create_replay_app(state: Optional[ReplayServerState] = None) -> FastAPI:
    """Factory creating the FastAPI Command Center application."""
    app = FastAPI(title="HyperPredator Replay Command Center", version="2.0.0")
    server_state = state or ReplayServerState()
    active_connections: Set[WebSocket] = set()

    # Store state on app for access in handlers
    app.state.replay = server_state
    app.state.connections = active_connections

    @app.get("/api/health")
    async def get_health():
        return {"status": "ok", "mode": "replay", "speed": server_state.speed}

    @app.get("/api/state")
    async def get_state():
        return JSONResponse(content=server_state.to_dict())

    @app.get("/api/candles")
    async def get_candles():
        return JSONResponse(content=server_state.candles_history[-200:])

    @app.post("/completion")
    async def post_completion(body: Optional[Dict[str, Any]] = None):
        """
        High-speed LLM completion endpoint for Core 1 Macro Brain.
        Returns strict schema compliant with HyperPredator MacroState.
        """
        bias = server_state.macro_edge.get("bias", "BULLISH")
        regime = server_state.macro_edge.get("volatility_regime", 1.0)
        permit = server_state.macro_edge.get("permit_trade", True)
        result = {
            "permit_trade": permit,
            "bias": bias,
            "volatility_regime": regime,
        }
        return JSONResponse(content={"content": json.dumps(result)})

    @app.websocket("/ws/stream")
    async def websocket_stream(websocket: WebSocket):
        await websocket.accept()
        active_connections.add(websocket)
        try:
            # Send immediate initial snapshot
            await websocket.send_text(json.dumps(server_state.to_dict()))
            while True:
                # Keep socket alive and listen for client pings
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            active_connections.discard(websocket)

    @app.get("/manifest.json")
    async def get_manifest():
        return JSONResponse(content={
            "name": "HyperPredator DEX Scalper",
            "short_name": "Predator",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#0b0f17",
            "theme_color": "#0b0f17",
            "icons": [
                {
                    "src": "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🟡</text></svg>",
                    "sizes": "192x192 512x512",
                    "type": "image/svg+xml"
                }
            ]
        })

    @app.post("/api/liquidate")
    async def post_liquidate():
        """Emergency manual liquidation trigger callable from mobile."""
        server_state.active_position = {
            "is_active": False,
            "direction": "FLAT",
            "entry_price": 0.0,
            "size": 0.0,
            "target_price": None,
            "stop_price": None,
            "upnl": 0.0,
            "upnl_pct": 0.0,
        }
        server_state.log_messages.append({
            "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "type": "SHIELD",
            "text": "🚨 EMERGENCY MANUAL LIQUIDATE TRIGGERED FROM MOBILE",
        })
        await broadcast_state(app)
        return {"status": "ok", "action": "EMERGENCY_LIQUIDATE_EXECUTED"}

    @app.get("/", response_class=HTMLResponse)
    async def get_dashboard():
        return HTML_DASHBOARD_TEMPLATE

    return app


async def broadcast_state(app: FastAPI) -> None:
    """Broadcasts current state snapshot to all connected WebSocket clients."""
    connections: Set[WebSocket] = app.state.connections
    if not connections:
        return
    payload = json.dumps(app.state.replay.to_dict())
    dead_connections = set()
    for ws in list(connections):
        try:
            await ws.send_text(payload)
        except Exception:
            dead_connections.add(ws)
    for dead in dead_connections:
        connections.discard(dead)


HTML_DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
  <title>HyperPredator Replay Command Center</title>
  <link rel="manifest" href="/manifest.json">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="apple-mobile-web-app-title" content="Predator Scalper">
  <meta name="theme-color" content="#0b0f17">
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    body { font-family: 'Inter', sans-serif; background-color: #0b0f17; color: #e2e8f0; }
    .mono { font-family: 'JetBrains Mono', monospace; }
    @keyframes pulse-gold {
      0%, 100% { opacity: 1; transform: scale(1); box-shadow: 0 0 15px rgba(234, 179, 8, 0.4); }
      50% { opacity: 0.85; transform: scale(1.02); box-shadow: 0 0 25px rgba(234, 179, 8, 0.8); }
    }
    .badge-pulse { animation: pulse-gold 2s infinite ease-in-out; }
    .card-glass { background: rgba(18, 24, 38, 0.85); backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.08); }
  </style>
</head>
<body class="min-h-screen p-3 md:p-4 flex flex-col">
  <!-- Top Navigation & Replay Header -->
  <header class="flex flex-wrap items-center justify-between gap-3 mb-4 md:mb-6 pb-3 md:pb-4 border-b border-gray-800">
    <div class="flex items-center space-x-3">
      <div class="h-3 w-3 rounded-full bg-amber-400 animate-ping"></div>
      <h1 class="text-lg md:text-xl font-bold tracking-tight text-white flex items-center gap-2">
        <span class="text-amber-400">HYPER-PREDATOR</span>
        <span class="text-[10px] md:text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded font-mono">DEX M1 SCALPER</span>
      </h1>
    </div>

    <!-- Pulsing Replay Header Badge -->
    <div id="replay-badge" class="badge-pulse px-3 md:px-4 py-1.5 rounded-full bg-amber-950/70 border border-amber-500/60 text-amber-300 font-mono text-xs md:text-sm font-semibold flex items-center space-x-2">
      <span class="h-2.5 w-2.5 rounded-full bg-amber-400 animate-pulse"></span>
      <span id="badge-text">🟡 REPLAY (1x Speed): 2026-09-17 00:00:00 UTC</span>
    </div>

    <div class="flex items-center space-x-3 md:space-x-4">
      <span class="text-[11px] bg-indigo-950/70 border border-indigo-500/40 text-indigo-300 px-2.5 py-1 rounded-full font-mono flex items-center gap-1.5 shadow-sm">
        <span class="h-2 w-2 rounded-full bg-indigo-400 animate-pulse"></span>
        📱 172.20.10.4:8000
      </span>
      <div class="text-right">
        <div class="text-[10px] md:text-xs text-gray-500 font-mono">ASSET</div>
        <div class="text-xs md:text-sm font-bold text-amber-400">GOLD / USD (100x)</div>
      </div>
      <div class="text-right">
        <div class="text-[10px] md:text-xs text-gray-500 font-mono">STATUS</div>
        <div id="engine-status" class="text-xs md:text-sm font-bold text-emerald-400 font-mono">INITIALIZING</div>
      </div>
    </div>
  </header>

  <!-- Main Grid -->
  <main class="grid grid-cols-1 lg:grid-cols-4 gap-5 flex-grow">
    <!-- Left Column: Metrics & Position -->
    <div class="lg:col-span-1 space-y-5">
      <!-- Active Position Card -->
      <div class="card-glass rounded-xl p-5 shadow-lg">
        <div class="flex items-center justify-between mb-4">
          <span class="text-xs font-semibold uppercase tracking-wider text-gray-400">Active Position</span>
          <span id="pos-direction" class="px-2 py-0.5 text-xs font-bold rounded bg-gray-800 text-gray-400 font-mono">FLAT</span>
        </div>

        <div class="space-y-3">
          <div class="flex justify-between items-baseline">
            <span class="text-sm text-gray-400">Position Size:</span>
            <span id="pos-size" class="text-base font-bold font-mono text-white">0.00 oz</span>
          </div>
          <div class="flex justify-between items-baseline">
            <span class="text-sm text-gray-400">Entry Price:</span>
            <span id="pos-entry" class="text-base font-mono text-gray-300">$0.00</span>
          </div>
          <div class="flex justify-between items-baseline">
            <span class="text-sm text-gray-400">Current Market:</span>
            <span id="pos-market" class="text-lg font-bold font-mono text-amber-400">$2,500.00</span>
          </div>
          <div class="flex justify-between items-baseline pt-2 border-t border-gray-800">
            <span class="text-sm text-gray-400">Floating uPnL:</span>
            <span id="pos-upnl" class="text-xl font-bold font-mono text-gray-400">$0.00</span>
          </div>
        </div>

        <!-- Equity Shield Visual Bar -->
        <div class="mt-5 pt-4 border-t border-gray-800/80">
          <div class="flex justify-between items-center text-xs mb-1.5 font-mono">
            <span class="text-gray-400">Equity Shield Limit:</span>
            <span class="text-rose-400 font-bold">-$10.00 Stop</span>
          </div>
          <div class="w-full bg-gray-900 rounded-full h-3 p-0.5 border border-gray-800 relative overflow-hidden">
            <div id="shield-bar" class="h-full rounded-full transition-all duration-300 bg-emerald-500" style="width: 0%;"></div>
          </div>
          <div class="flex justify-between text-[11px] text-gray-500 mt-1 font-mono">
            <span>$0.00 Float</span>
            <span id="shield-percent">0.0% Burn</span>
            <span>-$10.00 Shield</span>
          </div>

          <!-- Mobile Emergency Liquidate Button -->
          <button onclick="triggerEmergencyLiquidate()" class="w-full mt-4 py-2.5 px-4 rounded-lg bg-rose-950/80 hover:bg-rose-900 active:scale-95 border border-rose-600/70 text-rose-300 font-mono text-xs font-bold transition flex items-center justify-center gap-2 shadow-lg cursor-pointer">
            <span>🚨</span> EMERGENCY MANUAL LIQUIDATE
          </button>
        </div>
      </div>

      <!-- Account Equity & Macro Edge -->
      <div class="card-glass rounded-xl p-5 shadow-lg space-y-4">
        <div class="flex justify-between items-center">
          <span class="text-xs font-semibold uppercase tracking-wider text-gray-400">Account Equity</span>
          <span id="account-equity" class="text-lg font-bold font-mono text-emerald-400">$65.00</span>
        </div>

        <div class="pt-3 border-t border-gray-800">
          <div class="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-3">Core 1: Macro Brain</div>
          <div class="space-y-2 text-sm font-mono">
            <div class="flex justify-between">
              <span class="text-gray-400">Trade Permit:</span>
              <span id="macro-permit" class="font-bold text-emerald-400">PERMITTED</span>
            </div>
            <div class="flex justify-between">
              <span class="text-gray-400">Macro Bias:</span>
              <span id="macro-bias" class="font-bold text-sky-400">BULLISH</span>
            </div>
            <div class="flex justify-between">
              <span class="text-gray-400">Vol Regime:</span>
              <span id="macro-regime" class="font-bold text-gray-200">1.00x</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Center/Right: Candlestick Chart & Terminal Journal -->
    <div class="lg:col-span-3 space-y-5 flex flex-col">
      <!-- Chart Canvas Container -->
      <div class="card-glass rounded-xl p-5 shadow-lg flex-grow flex flex-col min-h-[360px]">
        <div class="flex justify-between items-center mb-3">
          <div class="flex items-center space-x-2">
            <span class="text-sm font-semibold uppercase tracking-wider text-gray-300">Live M1 Chart & Execution Markers</span>
            <span class="text-xs bg-amber-500/20 text-amber-300 border border-amber-500/30 px-2 py-0.5 rounded font-mono">GOLD M1</span>
          </div>
          <div class="flex items-center space-x-4 text-xs font-mono">
            <div class="flex items-center space-x-1.5"><span class="h-2.5 w-2.5 rounded-full bg-emerald-400"></span><span class="text-gray-400">Spam Entry</span></div>
            <div class="flex items-center space-x-1.5"><span class="h-2.5 w-2.5 rounded-full bg-sky-400"></span><span class="text-gray-400">Micro-Exit</span></div>
            <div class="flex items-center space-x-1.5"><span class="h-2.5 w-2.5 rounded-full bg-rose-500"></span><span class="text-gray-400">Equity Shield</span></div>
          </div>
        </div>

        <div class="relative flex-grow w-full bg-black/40 rounded-lg border border-gray-800/80 overflow-hidden flex items-center justify-center">
          <canvas id="chart-canvas" class="w-full h-full block"></canvas>
          <div id="chart-watermark" class="absolute bottom-3 right-4 text-xs font-mono text-gray-600 pointer-events-none">1:1 REPLAY ENGINE</div>
        </div>
      </div>

      <!-- Journal & Activity Terminal -->
      <div class="card-glass rounded-xl p-4 shadow-lg h-56 flex flex-col">
        <div class="flex justify-between items-center mb-2 pb-1 border-b border-gray-800">
          <span class="text-xs font-semibold uppercase tracking-wider text-gray-400 font-mono">Execution Journal & Telemetry Stream</span>
          <span class="text-[11px] font-mono text-gray-500">Auto-scrolling</span>
        </div>
        <div id="log-terminal" class="flex-grow overflow-y-auto space-y-1 font-mono text-xs text-gray-300 pr-1">
          <div class="text-gray-500">[SYSTEM] Awaiting market replay ticks...</div>
        </div>
      </div>
    </div>
  </main>

  <script>
    // WebSocket Telemetry Connection
    let ws;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/stream`;

    function connectWs() {
      ws = new WebSocket(wsUrl);
      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          updateDashboard(data);
        } catch (e) {
          console.error("WS Parse error", e);
        }
      };
      ws.onclose = () => {
        setTimeout(connectWs, 2000);
      };
    }
    connectWs();

    // Chart Canvas Rendering
    const canvas = document.getElementById('chart-canvas');
    const ctx = canvas.getContext('2d');
    let candleData = [];
    let markerData = [];

    function resizeCanvas() {
      canvas.width = canvas.parentElement.clientWidth;
      canvas.height = canvas.parentElement.clientHeight;
      drawChart();
    }
    window.addEventListener('resize', resizeCanvas);
    setTimeout(resizeCanvas, 100);

    function updateDashboard(state) {
      // Header Badge
      const badgeText = document.getElementById('badge-text');
      const timeStr = state.simulated_time_iso || "00:00:00 UTC";
      const speedStr = `${state.speed}x Speed`;
      if (state.status === "WAITING_FOR_MIDNIGHT_UTC") {
        badgeText.textContent = `⏳ WAITING FOR 00:00:00 UTC (T-${state.countdown_str})`;
      } else {
        badgeText.textContent = `🟡 REPLAY (${speedStr}): ${timeStr.replace('T', ' ').replace('Z', ' UTC')}`;
      }

      // Status
      document.getElementById('engine-status').textContent = state.status;
      document.getElementById('account-equity').textContent = `$${state.equity.current.toFixed(2)}`;

      // Position
      const pos = state.active_position;
      const dirElem = document.getElementById('pos-direction');
      dirElem.textContent = pos.direction;
      if (pos.direction === "LONG") {
        dirElem.className = "px-2 py-0.5 text-xs font-bold rounded bg-emerald-950 text-emerald-400 border border-emerald-800 font-mono";
      } else if (pos.direction === "SHORT") {
        dirElem.className = "px-2 py-0.5 text-xs font-bold rounded bg-rose-950 text-rose-400 border border-rose-800 font-mono";
      } else {
        dirElem.className = "px-2 py-0.5 text-xs font-bold rounded bg-gray-800 text-gray-400 font-mono";
      }

      document.getElementById('pos-size').textContent = `${pos.size.toFixed(2)} oz`;
      document.getElementById('pos-entry').textContent = `$${pos.entry_price.toFixed(2)}`;
      document.getElementById('pos-market').textContent = `$${state.current_price.toFixed(2)}`;

      const upnlElem = document.getElementById('pos-upnl');
      const upnl = pos.upnl || 0.0;
      if (upnl > 0.01) {
        upnlElem.textContent = `+$${upnl.toFixed(2)} (${pos.upnl_pct.toFixed(2)}%)`;
        upnlElem.className = "text-xl font-bold font-mono text-emerald-400";
      } else if (upnl < -0.01) {
        upnlElem.textContent = `-$${Math.abs(upnl).toFixed(2)} (${pos.upnl_pct.toFixed(2)}%)`;
        upnlElem.className = "text-xl font-bold font-mono text-rose-400";
      } else {
        upnlElem.textContent = "$0.00";
        upnlElem.className = "text-xl font-bold font-mono text-gray-400";
      }

      // Hard Equity Shield Bar
      const shieldPct = state.equity.shield_progress_pct || 0;
      const shieldBar = document.getElementById('shield-bar');
      shieldBar.style.width = `${shieldPct}%`;
      document.getElementById('shield-percent').textContent = `${shieldPct.toFixed(1)}% Burn`;
      if (shieldPct > 70) {
        shieldBar.className = "h-full rounded-full transition-all duration-300 bg-rose-500";
      } else if (shieldPct > 40) {
        shieldBar.className = "h-full rounded-full transition-all duration-300 bg-amber-500";
      } else {
        shieldBar.className = "h-full rounded-full transition-all duration-300 bg-emerald-500";
      }

      // Macro Edge
      const macro = state.macro_edge;
      document.getElementById('macro-permit').textContent = macro.permit_trade ? "PERMITTED" : "HALTED";
      document.getElementById('macro-permit').className = macro.permit_trade ? "font-bold text-emerald-400" : "font-bold text-rose-400";
      document.getElementById('macro-bias').textContent = macro.bias;
      document.getElementById('macro-bias').className = macro.bias === "BULLISH" ? "font-bold text-sky-400" : "font-bold text-amber-400";
      document.getElementById('macro-regime').textContent = `${macro.volatility_regime.toFixed(2)}x`;

      // Update Chart Candle & Markers
      if (state.latest_candle) {
        if (!candleData.length || candleData[candleData.length - 1].time !== state.latest_candle.time) {
          candleData.push(state.latest_candle);
          if (candleData.length > 80) candleData.shift();
        } else {
          candleData[candleData.length - 1] = state.latest_candle;
        }
      }
      if (state.markers) {
        markerData = state.markers;
      }
      drawChart();

      // Log Messages
      if (state.log_messages && state.log_messages.length > 0) {
        const terminal = document.getElementById('log-terminal');
        terminal.innerHTML = state.log_messages.map(m => {
          let color = "text-gray-300";
          if (m.type === "SPAM") color = "text-emerald-400 font-bold";
          else if (m.type === "EXIT") color = "text-sky-400 font-bold";
          else if (m.type === "SHIELD") color = "text-rose-400 font-bold animate-pulse";
          else if (m.type === "MACRO") color = "text-amber-300";
          return `<div class="${color}">[${m.time}] ${m.text}</div>`;
        }).join('');
        terminal.scrollTop = terminal.scrollHeight;
      }
    }

    function drawChart() {
      if (!ctx || !candleData.length) return;
      const w = canvas.width;
      const h = canvas.height;
      ctx.clearRect(0, 0, w, h);

      // Price Bounds
      let minP = Infinity, maxP = -Infinity;
      for (const c of candleData) {
        if (c.low < minP) minP = c.low;
        if (c.high > maxP) maxP = c.high;
      }
      const pad = (maxP - minP) * 0.1 || 1.0;
      minP -= pad;
      maxP += pad;

      const pxToY = (p) => h - ((p - minP) / (maxP - minP)) * h;
      const barW = Math.max(3, (w - 60) / Math.max(candleData.length, 30));

      // Grid Lines
      ctx.strokeStyle = "rgba(255, 255, 255, 0.05)";
      ctx.lineWidth = 1;
      for (let i = 0; i < 5; i++) {
        const y = h * (i / 4);
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
        ctx.stroke();
      }

      // Draw Candles
      candleData.forEach((c, idx) => {
        const x = idx * barW + 10;
        const yOpen = pxToY(c.open);
        const yClose = pxToY(c.close);
        const yHigh = pxToY(c.high);
        const yLow = pxToY(c.low);
        const isUp = c.close >= c.open;

        ctx.strokeStyle = isUp ? "#10b981" : "#f43f5e";
        ctx.fillStyle = isUp ? "#10b981" : "#f43f5e";

        // Wick
        ctx.beginPath();
        ctx.moveTo(x + barW / 2, yHigh);
        ctx.lineTo(x + barW / 2, yLow);
        ctx.stroke();

        // Body
        const topY = Math.min(yOpen, yClose);
        const bodyH = Math.max(2, Math.abs(yClose - yOpen));
        ctx.fillRect(x + 1, topY, barW - 2, bodyH);
      });

      // Draw Execution Markers
      markerData.forEach(m => {
        const matchIdx = candleData.findIndex(c => Math.abs(c.time - m.time) < 120000);
        if (matchIdx >= 0) {
          const x = matchIdx * barW + 10 + barW / 2;
          const y = pxToY(m.price);

          ctx.beginPath();
          if (m.type.startsWith("SPAM")) {
            ctx.fillStyle = "#10b981";
            ctx.arc(x, y - 8, 5, 0, 2 * Math.PI);
            ctx.fill();
          } else if (m.type === "SHIELD") {
            ctx.fillStyle = "#f43f5e";
            ctx.arc(x, y + 8, 6, 0, 2 * Math.PI);
            ctx.fill();
          } else {
            ctx.fillStyle = "#38bdf8";
            ctx.arc(x, y, 4, 0, 2 * Math.PI);
            ctx.fill();
          }
        }
      });
    }

    async function triggerEmergencyLiquidate() {
      if (confirm("Confirm emergency panic liquidation of all active positions?")) {
        try {
          const res = await fetch('/api/liquidate', { method: 'POST' });
          const d = await res.json();
          alert("✓ Emergency liquidation executed: " + d.action);
        } catch (e) {
          alert("Error executing liquidation: " + e);
        }
      }
    }
  </script>
</body>
</html>
"""
