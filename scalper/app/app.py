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

LETSENCRYPT_CERT = Path("/etc/letsencrypt/live/82-115-21-155.sslip.io/fullchain.pem")
LETSENCRYPT_KEY = Path("/etc/letsencrypt/live/82-115-21-155.sslip.io/privkey.pem")

if LETSENCRYPT_CERT.exists() and LETSENCRYPT_KEY.exists():
    DEFAULT_CERT = str(LETSENCRYPT_CERT)
    DEFAULT_KEY = str(LETSENCRYPT_KEY)
else:
    DEFAULT_CERT = "/root/ict_sniper/tls/fullchain.pem"
    DEFAULT_KEY = "/root/ict_sniper/tls/privkey.pem"

TOKEN = os.getenv("SCALPER_APP_TOKEN", "7SQMRVRJ-VkD4lG3VXsb1Fc82oYUAP93")
CERT = os.getenv("SCALPER_APP_CERT", DEFAULT_CERT)
KEY = os.getenv("SCALPER_APP_KEY", DEFAULT_KEY)
HOST = os.getenv("SCALPER_APP_HOST", "0.0.0.0")
PORT = int(os.getenv("SCALPER_APP_PORT", "443"))
PORT_HTTP = int(os.getenv("SCALPER_APP_HTTP_PORT", "80"))

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
        # Load Autonomous LLM Doctor Telemetry if available
        try:
            doc_file = DATA / "state" / "doctor_telemetry.json"
            if doc_file.exists():
                with open(doc_file, "r") as df:
                    best["doctor"] = json.load(df)
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


TEMPLATE_FILE = Path(__file__).resolve().parent / "templates" / "terminal.html"

def _render_hft_terminal() -> str:
    if TEMPLATE_FILE.exists():
        try:
            return TEMPLATE_FILE.read_text(encoding="utf-8")
        except Exception:
            pass
    return "<html><body><h1>Stratton Oakmont</h1><p>Loading UI template...</p></body></html>"


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

        # 1. Native Web Push Service Worker
        if path == "/sw.js":
            body = (
                b"self.addEventListener('install', e => self.skipWaiting());\n"
                b"self.addEventListener('activate', e => clients.claim());\n"
                b"self.addEventListener('push', e => {\n"
                b"  let data = {};\n"
                b"  if (e.data) {\n"
                b"    try { data = e.data.json(); } catch(err) { data = { body: e.data.text() }; }\n"
                b"  }\n"
                b"  const title = data.title || 'Stratton Oakmont Desk';\n"
                b"  const options = {\n"
                b"    body: data.body || 'Live Market Update',\n"
                b"    icon: data.icon || '/icon-180.png',\n"
                b"    badge: '/icon-180.png',\n"
                b"    tag: data.tag || 'stratton-trade',\n"
                b"    renotify: true,\n"
                b"    data: { url: data.url || '/' },\n"
                b"    vibrate: [200, 100, 200]\n"
                b"  };\n"
                b"  e.waitUntil(self.registration.showNotification(title, options));\n"
                b"});\n"
                b"self.addEventListener('notificationclick', e => {\n"
                b"  e.notification.close();\n"
                b"  const targetUrl = (e.notification.data && e.notification.data.url) ? e.notification.data.url : '/';\n"
                b"  e.waitUntil(\n"
                b"    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clientList => {\n"
                b"      for (let client of clientList) {\n"
                b"        if (client.url && 'focus' in client) return client.focus();\n"
                b"      }\n"
                b"      if (clients.openWindow) return clients.openWindow(targetUrl);\n"
                b"    })\n"
                b"  );\n"
                b"});\n"
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache, must-revalidate")
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
            return

        # 1b. VAPID Public Key for client push subscription
        if path == "/api/push/key":
            try:
                from scalper.web_push import get_vapid_public_key
                key = get_vapid_public_key()
                payload = json.dumps({"publicKey": key}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                if not head_only:
                    self.wfile.write(payload)
                return
            except Exception as e:
                self.send_response(500)
                self.end_headers()
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

        if path == "/api/push/subscribe":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length).decode("utf-8")
                sub = json.loads(raw)
                from scalper.web_push import add_subscription
                ok = add_subscription(sub)
                res = json.dumps({"ok": ok, "msg": "Push subscription activated"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(res)
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(f'{{"error": "{e}"}}'.encode("utf-8"))
            return

        if path == "/api/push/test":
            try:
                from scalper.web_push import send_web_push
                sent = send_web_push(
                    title="🟢 Stratton Oakmont Test Push",
                    message="Native Web Push notification connected successfully to your device!",
                    tag="test-push",
                )
                res = json.dumps({"ok": True, "sent": sent, "msg": f"Dispatched to {sent} active device(s)"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(res)
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
    http_ports = list(dict.fromkeys([PORT_HTTP, 80, 8088]))
    for p in http_ports:
        def _make_http_server(port_num):
            def _serve():
                try:
                    s = ThreadingHTTPServer((HOST, port_num), HFTHandler)
                    print(f"Stratton Oakmont HTTP server running on http://{HOST}:{port_num} (Zero SSL warnings for friends)")
                    s.serve_forever()
                except Exception as e:
                    print(f"HTTP server on port {port_num} notice: {e}")
            threading.Thread(target=_serve, daemon=True).start()
        _make_http_server(p)

    has_ssl = os.path.exists(CERT) and os.path.exists(KEY)
    if has_ssl:
        https_ports = list(dict.fromkeys([PORT, 443, 8443]))
        for p in https_ports[:-1]:
            def _make_https_server(port_num):
                def _serve():
                    try:
                        s = ThreadingHTTPServer((HOST, port_num), HFTHandler)
                        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
                        ctx.load_cert_chain(certfile=CERT, keyfile=KEY)
                        s.socket = ctx.wrap_socket(s.socket, server_side=True)
                        print(f"Stratton Oakmont HTTPS server running on https://{HOST}:{port_num}")
                        s.serve_forever()
                    except Exception as e:
                        print(f"HTTPS server on port {port_num} notice: {e}")
                threading.Thread(target=_serve, daemon=True).start()
            _make_https_server(p)

        last_port = https_ports[-1]
        try:
            s = ThreadingHTTPServer((HOST, last_port), HFTHandler)
            ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ctx.load_cert_chain(certfile=CERT, keyfile=KEY)
            s.socket = ctx.wrap_socket(s.socket, server_side=True)
            print(f"Stratton Oakmont HTTPS server running on https://{HOST}:{last_port}")
            s.serve_forever()
        except Exception as e:
            print(f"HTTPS main server on {last_port} error: {e}")
            while True:
                time.sleep(3600)
    else:
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    run_app()
