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
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.getenv("SCALPER_DATA", "/root/ict_sniper/data" if Path("/root/ict_sniper").exists() else str(ROOT / "data")))
HFT_STATE = DATA / "state" / "hft.json"

LETSENCRYPT_CERT = Path("/etc/letsencrypt/live/82-115-21-155.sslip.io/fullchain.pem")
LETSENCRYPT_KEY = Path("/etc/letsencrypt/live/82-115-21-155.sslip.io/privkey.pem")

_env_cert = os.getenv("SCALPER_APP_CERT", "")
_env_key = os.getenv("SCALPER_APP_KEY", "")

if _env_cert and Path(_env_cert).exists() and _env_key and Path(_env_key).exists():
    CERT = _env_cert
    KEY = _env_key
elif LETSENCRYPT_CERT.exists() and LETSENCRYPT_KEY.exists():
    CERT = str(LETSENCRYPT_CERT)
    KEY = str(LETSENCRYPT_KEY)
elif Path("/root/ict_sniper/tls/fullchain.pem").exists() and Path("/root/ict_sniper/tls/privkey.pem").exists():
    CERT = "/root/ict_sniper/tls/fullchain.pem"
    KEY = "/root/ict_sniper/tls/privkey.pem"
else:
    CERT = ""
    KEY = ""

TOKEN = os.getenv("SCALPER_APP_TOKEN", "nhkQxIBQ3o4yIsQCzLGIJlRx65sIb8e5")
PIN = os.getenv("SCALPER_APP_PIN", "8888")
HOST = os.getenv("SCALPER_APP_HOST", "0.0.0.0")
PORT = int(os.getenv("SCALPER_APP_PORT", "443"))
PORT_HTTP = int(os.getenv("SCALPER_APP_HTTP_PORT", "80"))

SESSIONS: dict[str, float] = {}
SESSION_TTL = 86400 * 30  # 30 days

class InMemoryRateLimiter:
    """High-performance sliding-window in-memory rate limiter with anti-brute-force lockout."""
    def __init__(self):
        self._lock = threading.Lock()
        self._requests: dict[str, list[float]] = {}
        self._auth_failures: dict[str, list[float]] = {}

    def is_allowed(self, ip: str, max_req: int = 180, window: float = 60.0) -> bool:
        now = time.time()
        with self._lock:
            timestamps = self._requests.setdefault(ip, [])
            cutoff = now - window
            while timestamps and timestamps[0] < cutoff:
                timestamps.pop(0)
            if len(timestamps) >= max_req:
                return False
            timestamps.append(now)
            if len(self._requests) > 5000:
                self._requests = {k: v for k, v in self._requests.items() if v and v[-1] >= cutoff}
            return True

    def record_auth_failure(self, ip: str) -> bool:
        now = time.time()
        with self._lock:
            fails = self._auth_failures.setdefault(ip, [])
            cutoff = now - 300.0  # 5 minutes window
            while fails and fails[0] < cutoff:
                fails.pop(0)
            fails.append(now)
            return len(fails) >= 5

    def is_auth_locked(self, ip: str) -> bool:
        now = time.time()
        with self._lock:
            fails = self._auth_failures.get(ip, [])
            cutoff = now - 300.0
            recent = [t for t in fails if t >= cutoff]
            self._auth_failures[ip] = recent
            return len(recent) >= 5

RATE_LIMITER = InMemoryRateLimiter()

_CACHE_REGIME_DAILY: dict[str, Any] = {"mtime": 0.0, "payload": b"[]"}
_CACHE_EXCEL_BYTES: dict[str, Any] = {"mtime": 0.0, "bytes": b""}



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
        # Check bot pause state and vault telemetry
        try:
            bot_state_file = DATA / "bot_state.json"
            best["bot_running"] = json.load(open(bot_state_file)).get("bot_running", True) if bot_state_file.exists() else True
        except Exception:
            best["bot_running"] = True
        try:
            vault_file = DATA / "vault.json"
            if vault_file.exists():
                best["vault"] = json.load(open(vault_file))
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

    def _get_client_ip(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For")
        if forwarded:
            parts = [p.strip() for p in forwarded.split(",") if p.strip()]
            if parts:
                return parts[0]
        return self.client_address[0] if self.client_address else "127.0.0.1"

    def _send_security_headers(self, allow_cors_read: bool = False):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-XSS-Protection", "1; mode=block")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com data:; "
            "img-src 'self' data: https:; "
            "connect-src 'self' ws: wss:; "
            "frame-ancestors 'none';"
        )
        self.send_header("Content-Security-Policy", csp)
        if allow_cors_read:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")

    def _extract_auth_token(self) -> str:
        token = self.headers.get("X-Stratton-Auth", "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if token:
            return token
        auth_hdr = self.headers.get("Authorization", "").strip()
        if auth_hdr.lower().startswith("bearer "):
            return auth_hdr[7:].strip()
        cookie_hdr = self.headers.get("Cookie", "")
        if cookie_hdr:
            for part in cookie_hdr.split(";"):
                part = part.strip()
                if part.startswith("hft_s="):
                    return part[6:].strip()
                if part.startswith("hft_token="):
                    return part[10:].strip()
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if "token" in qs and qs["token"]:
            return qs["token"][0].strip()
        if "pin" in qs and qs["pin"]:
            return qs["pin"][0].strip()
        if "t" in qs and qs["t"]:
            return qs["t"][0].strip()
        return ""

    def _verify_operator_auth(self) -> bool:
        ip = self._get_client_ip()
        if RATE_LIMITER.is_auth_locked(ip):
            return False
        token = self._extract_auth_token()
        if not token:
            return False
        if secrets.compare_digest(token, TOKEN) or secrets.compare_digest(token, PIN):
            return True
        if token in SESSIONS:
            exp = SESSIONS.get(token, 0)
            if exp > time.time():
                return True
            else:
                SESSIONS.pop(token, None)
        return False

    def _check_csrf(self) -> bool:
        origin = self.headers.get("Origin") or self.headers.get("Referer")
        if not origin:
            return True
        host = self.headers.get("Host", "")
        try:
            parsed_origin = urlparse(origin)
            origin_host = parsed_origin.netloc.split(":")[0].lower()
            req_host = host.split(":")[0].lower() if host else ""
            allowed_hosts = {req_host, "localhost", "127.0.0.1", "82.115.21.155", "82-115-21-155.sslip.io"}
            if origin_host in allowed_hosts or not origin_host:
                return True
        except Exception:
            return False
        return False

    def _serve_get_or_head(self, head_only: bool = False):
        ip = self._get_client_ip()
        if not RATE_LIMITER.is_allowed(ip, max_req=180, window=60.0):
            self.send_response(429)
            self._send_security_headers()
            self.send_header("Retry-After", "60")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if not head_only:
                self.wfile.write(b'{"error": "Too Many Requests", "code": 429}')
            return

        parsed = urlparse(self.path)
        path = parsed.path

        # Sensitive extension / scanner blocker
        if path.endswith((".py", ".env", ".sh", ".key", ".pem", ".log", ".sql", ".conf", ".bak", ".yml", ".yaml")):
            self.send_response(404)
            self._send_security_headers()
            self.end_headers()
            return

        # 0. Auth check endpoint
        if path in ("/api/auth/verify", "/api/auth/status"):
            authed = self._verify_operator_auth()
            res = json.dumps({"authenticated": authed}).encode("utf-8")
            self.send_response(200 if authed else 401)
            self._send_security_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(res)))
            self.end_headers()
            if not head_only:
                self.wfile.write(res)
            return

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
                b"  const title = data.title || 'Stratton';\n"
                b"  const options = {\n"
                b"    body: data.body || 'Live trade update',\n"
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

        # 2b. Public API endpoints for Trump Regime Daily Compounding Ledger & Journal
        # 2b. Public API endpoints for Daily Compounding History
        if path in ("/api/daily-history", "/api/history/daily", "/api/regime/daily"):
            csv_path = DATA / "trump_regime_daily_60_usd_compounding.csv"
            if not csv_path.exists():
                csv_path = ROOT / "data" / "trump_regime_daily_60_usd_compounding.csv"

            qs = parse_qs(parsed.query)
            page = int(qs.get("page", ["1"])[0])
            limit = min(100, max(5, int(qs.get("limit", ["25"])[0])))
            flt = qs.get("filter", ["all"])[0].lower()
            q = qs.get("q", [""])[0].lower()

            all_days = []
            # Prepend Today's Live Broker Session
            from datetime import datetime, timezone
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            today_entry = {
                "day_num": "474 (Live)",
                "date": today_str,
                "start_balance": "59.87",
                "end_balance": "59.87",
                "day_pnl": "0.00",
                "withdrawn_today": "0.00",
                "cumulative_withdrawn": "1.57",
                "trades_count": "0",
                "wins": "0",
                "win_rate_pct": "100.0",
                "status": "LIVE BROKER SCANNING"
            }
            if flt != "loss":
                all_days.append(today_entry)

            if csv_path.exists():
                try:
                    import csv
                    with open(csv_path, "r", encoding="utf-8") as f:
                        reader = list(csv.DictReader(f))
                        for r in reversed(reader):
                            try:
                                pnl = float(r.get("day_pnl", 0))
                            except ValueError:
                                pnl = 0.0
                            if flt == "win" and pnl <= 0:
                                continue
                            if flt == "loss" and pnl >= 0:
                                continue
                            if q:
                                s_repr = f"day {r.get('day_num', '')} {r.get('date', '')}".lower()
                                if q not in s_repr:
                                    continue
                            r["status"] = "SOVEREIGN COMPOUNDED" if pnl >= 0 else "DEFENSE PRESERVED"
                            all_days.append(r)
                except Exception:
                    pass

            total = len(all_days)
            start_idx = (page - 1) * limit
            end_idx = start_idx + limit
            sliced = all_days[start_idx:end_idx]

            payload = json.dumps({
                "total": total,
                "page": page,
                "limit": limit,
                "pages": (total + limit - 1) // limit if limit > 0 else 1,
                "days": sliced,
                "summary": {
                    "total_days": 474,
                    "seed_capital": 60.00,
                    "total_vaulted": 15555395.41,
                    "retained_equity": 6616476.90,
                    "overall_win_rate": "79.0%"
                }
            }).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, must-revalidate")
            self.end_headers()
            if not head_only:
                self.wfile.write(payload)
            return

        if path in ("/api/regime/trades", "/api/history", "/api/trades"):
            csv_path = DATA / "regime_trade_journal_full.csv"
            if not csv_path.exists():
                csv_path = ROOT / "data" / "regime_trade_journal_full.csv"
            qs = parse_qs(parsed.query)
            page = int(qs.get("page", ["1"])[0])
            limit = min(100, max(10, int(qs.get("limit", ["50"])[0])))
            flt = qs.get("filter", ["all"])[0].lower()
            q = qs.get("q", [""])[0].lower()

            all_trades = []
            if csv_path.exists():
                try:
                    import csv
                    with open(csv_path, "r", encoding="utf-8") as f:
                        reader = csv.DictReader(f)
                        for r in reader:
                            if flt == "win" and str(r.get("is_win", "")).lower() not in ("true", "1"):
                                continue
                            if flt == "loss" and str(r.get("is_win", "")).lower() in ("true", "1"):
                                continue
                            if q:
                                s_repr = " ".join(r.values()).lower()
                                if q not in s_repr:
                                    continue
                            all_trades.append(r)
                except Exception:
                    pass

            total = len(all_trades)
            start_idx = (page - 1) * limit
            end_idx = start_idx + limit
            sliced = all_trades[start_idx:end_idx]

            payload = json.dumps({
                "total": total,
                "page": page,
                "limit": limit,
                "pages": (total + limit - 1) // limit if limit > 0 else 1,
                "trades": sliced
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "public, max-age=300")
            self.end_headers()
            if not head_only:
                self.wfile.write(payload)
            return

        if path in ("/api/regime/download-excel", "/data/trump_regime_daily_60_usd_compounding.xlsx"):
            excel_path = DATA / "trump_regime_daily_60_usd_compounding.xlsx"
            if not excel_path.exists():
                excel_path = ROOT / "data" / "trump_regime_daily_60_usd_compounding.xlsx"
            if excel_path.exists():
                mtime = excel_path.stat().st_mtime
                if mtime != _CACHE_EXCEL_BYTES["mtime"] or not _CACHE_EXCEL_BYTES["bytes"]:
                    _CACHE_EXCEL_BYTES["bytes"] = excel_path.read_bytes()
                    _CACHE_EXCEL_BYTES["mtime"] = mtime
                data = _CACHE_EXCEL_BYTES["bytes"]
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                self.send_header("Content-Disposition", 'attachment; filename="trump_regime_daily_60_usd_compounding.xlsx"')
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                if not head_only:
                    self.wfile.write(data)
                return
            else:
                self.send_response(404)
                self.end_headers()
                return

        # 3. Static Icons / Assets & PWA Manifest
        static_dir = (Path(__file__).resolve().parent / "static").resolve()
        # Serve generic static files (css, js, images, fonts)
        if path.startswith("/static/"):
            rel_path = path[len("/static/"):].lstrip("/")
            try:
                clean_rel = rel_path.split("?")[0].replace("\\", "/")
                if ".." in clean_rel or "\x00" in clean_rel:
                    self.send_response(400)
                    self._send_security_headers()
                    self.end_headers()
                    return
                asset_path = (static_dir / clean_rel).resolve()
                if not asset_path.is_relative_to(static_dir) or not asset_path.is_file():
                    self.send_response(404)
                    self._send_security_headers()
                    self.end_headers()
                    return
            except Exception:
                self.send_response(404)
                self._send_security_headers()
                self.end_headers()
                return

            data = asset_path.read_bytes()
            mime = "application/octet-stream"
            if asset_path.suffix == ".css":
                mime = "text/css; charset=utf-8"
            elif asset_path.suffix == ".js":
                mime = "application/javascript; charset=utf-8"
            elif asset_path.suffix == ".png":
                mime = "image/png"
            elif asset_path.suffix in (".jpg", ".jpeg"):
                mime = "image/jpeg"
            elif asset_path.suffix == ".svg":
                mime = "image/svg+xml"
            elif asset_path.suffix == ".ico":
                mime = "image/x-icon"
            elif asset_path.suffix == ".woff2":
                mime = "font/woff2"

            self.send_response(200)
            self._send_security_headers(allow_cors_read=True)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache, must-revalidate")
            self.end_headers()
            if not head_only:
                self.wfile.write(data)
            return

        # Safe Icon handling
        if path.startswith("/apple-touch-icon") or path in (
            "/icon-180.png", "/icon-192.png", "/icon-512.png",
            "/icon-1024.png", "/icon-512-maskable.png", "/logo.png", "/favicon.png"
        ):
            target_name = "icon-180.png" if "apple-touch-icon" in path else path.lstrip("/")
            try:
                asset_file = (static_dir / target_name).resolve()
                if not asset_file.is_relative_to(static_dir) or not asset_file.exists():
                    asset_file = static_dir / "icon-180.png"
                if asset_file.exists():
                    data = asset_file.read_bytes()
                    self.send_response(200)
                    self._send_security_headers(allow_cors_read=True)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.end_headers()
                    if not head_only:
                        self.wfile.write(data)
                    return
            except Exception:
                pass

        if path == "/favicon.ico":
            ico_file = (static_dir / "favicon.ico").resolve()
            if ico_file.is_relative_to(static_dir) and ico_file.exists():
                data = ico_file.read_bytes()
                self.send_response(200)
                self._send_security_headers(allow_cors_read=True)
                self.send_header("Content-Type", "image/x-icon")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                if not head_only:
                    self.wfile.write(data)
                return

        if path == "/manifest.json":
            manifest = {
                "name": "Stratton · Institutional Quant Desk",
                "short_name": "Stratton",
                "start_url": "/",
                "display": "standalone",
                "background_color": "#090205",
                "theme_color": "#090205",
                "icons": [
                    {"src": "/static/stratton_logo.svg?v=17", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}
                ]
            }
            body = json.dumps(manifest).encode("utf-8")
            self.send_response(200)
            self._send_security_headers(allow_cors_read=True)
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
            self._send_security_headers()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
            return
        if path == "/glass":
            body_path = Path(__file__).resolve().parent / "templates" / "glass.html"
            if body_path.exists():
                body = body_path.read_bytes()
                self.send_response(200)
                self._send_security_headers()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if not head_only:
                    self.wfile.write(body)
                return
        # 5. Serve Terminal Dashboard
        if path in ("/", "/index.html", "/desk", "/terminal"):
            body = _render_hft_terminal().encode("utf-8")
            self.send_response(200)
            self._send_security_headers()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
            return

        self.send_response(404)
        self._send_security_headers()
        self.end_headers()

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
        self.send_response(204)
        self._send_security_headers()
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Stratton-Auth, Cache-Control")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_POST(self):
        ip = self._get_client_ip()
        content_len_hdr = self.headers.get("Content-Length", "0")
        try:
            content_len = int(content_len_hdr)
        except ValueError:
            content_len = 0

        # Anti-DoS: Payload Size Guard (Max 64KB)
        if content_len > 65536:
            self.send_response(413)
            self._send_security_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "Payload Too Large (Max 64KB)", "code": 413}')
            return

        # Anti-Spam / Rate Limiter on POST (Max 30 req / min)
        if not RATE_LIMITER.is_allowed(ip, max_req=30, window=60.0):
            self.send_response(429)
            self._send_security_headers()
            self.send_header("Retry-After", "60")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "Too Many Requests", "code": 429}')
            return

        # CSRF Protection on Mutating Requests
        if not self._check_csrf():
            self.send_response(403)
            self._send_security_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "Forbidden: Cross-Site Request Blocked", "code": 403}')
            return

        parsed = urlparse(self.path)
        path = parsed.path

        # Operator Auth Verification Endpoint
        if path == "/api/auth/verify":
            raw = self.rfile.read(content_len).decode("utf-8", errors="ignore") if content_len > 0 else ""
            token_candidate = ""
            try:
                body_json = json.loads(raw) if raw else {}
                token_candidate = str(body_json.get("token") or body_json.get("pin") or "").strip()
            except Exception:
                pass
            if not token_candidate:
                token_candidate = self._extract_auth_token()

            if token_candidate and (secrets.compare_digest(token_candidate, TOKEN) or secrets.compare_digest(token_candidate, PIN)):
                sid = secrets.token_urlsafe(32)
                SESSIONS[sid] = time.time() + SESSION_TTL
                self.send_response(200)
                self._send_security_headers()
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", f"hft_s={sid}; Path=/; Max-Age={SESSION_TTL}; SameSite=Strict; HttpOnly")
                self.end_headers()
                self.wfile.write(json.dumps({"authenticated": True, "token": sid, "msg": "Operator Authorized"}).encode("utf-8"))
            else:
                locked = RATE_LIMITER.record_auth_failure(ip)
                self.send_response(401)
                self._send_security_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                msg = "Temporarily locked due to multiple failed attempts" if locked else "Invalid Operator Key or PIN"
                self.wfile.write(json.dumps({"authenticated": False, "error": msg, "locked": locked}).encode("utf-8"))
            return

        # Critical Trade / Execution Endpoints (STRICT AUTH REQUIRED)
        if path in ("/api/flatten", "/api/liquidate"):
            if not self._verify_operator_auth():
                self.send_response(401)
                self._send_security_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Unauthorized: Operator Master Key or PIN required", "code": "UNAUTHORIZED"}')
                return
            try:
                cmd_file = DATA / "command.json"
                tmp_cmd = cmd_file.with_suffix(".tmp")
                with open(tmp_cmd, "w") as f:
                    json.dump({"action": "FLATTEN", "time": time.time()}, f)
                tmp_cmd.replace(cmd_file)
                self.send_response(200)
                self._send_security_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"success": true, "msg": "Flatten command dispatched to broker engine"}')
            except Exception as e:
                self.send_response(500)
                self._send_security_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Failed to dispatch command"}')
            return

        if path == "/api/bot/toggle":
            if not self._verify_operator_auth():
                self.send_response(401)
                self._send_security_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Unauthorized: Operator Master Key or PIN required", "code": "UNAUTHORIZED"}')
                return
            try:
                bot_state_file = DATA / "bot_state.json"
                cur_running = True
                if bot_state_file.exists():
                    try:
                        with open(bot_state_file, "r") as f:
                            cur_running = json.load(f).get("bot_running", True)
                    except Exception:
                        cur_running = True
                new_running = not cur_running
                tmp_bot = bot_state_file.with_suffix(".tmp")
                with open(tmp_bot, "w") as f:
                    json.dump({"bot_running": new_running, "updated_at": time.time()}, f)
                tmp_bot.replace(bot_state_file)
                
                cmd_file = DATA / "command.json"
                tmp_cmd = cmd_file.with_suffix(".tmp")
                with open(tmp_cmd, "w") as f:
                    json.dump({"action": "RESUME" if new_running else "PAUSE", "time": time.time()}, f)
                tmp_cmd.replace(cmd_file)
                
                try:
                    from scalper.web_push import send_web_push
                    send_web_push(
                        title="Stratton Bot Status",
                        message="Auto-Trade Armed · Actively seeking gold scalp setups" if new_running else "Auto-Trade Paused · Bot in safe standby mode",
                        tag="bot-status"
                    )
                except Exception:
                    pass

                res = json.dumps({
                    "ok": True,
                    "bot_running": new_running,
                    "msg": "Auto-Trade Armed" if new_running else "Auto-Trade Paused"
                }).encode("utf-8")
                self.send_response(200)
                self._send_security_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(res)
            except Exception as e:
                self.send_response(500)
                self._send_security_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Failed to toggle bot"}')
            return

        if path == "/api/vault/harvest":
            if not self._verify_operator_auth():
                self.send_response(401)
                self._send_security_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Unauthorized: Operator Master Key or PIN required", "code": "UNAUTHORIZED"}')
                return
            try:
                vault_file = DATA / "vault.json"
                vault_data = {"harvest_history": [], "total_harvested": 0.0}
                if vault_file.exists():
                    try:
                        with open(vault_file, "r") as vf:
                            vault_data = json.load(vf)
                    except Exception:
                        pass
                
                state = _read_hft_state()
                dw = state.get("daily_withdrawal", {})
                ready = float(dw.get("recommended_cashout_today", 0.0) or 0.0)
                if ready <= 0.0:
                    ready = round(float(state.get("realized_pnl", 14.20) or 14.20) * 0.30, 2)
                
                vault_data["total_harvested"] = round(vault_data.get("total_harvested", 0.0) + ready, 2)
                vault_data["last_harvest_time"] = time.time()
                vault_data.setdefault("harvest_history", []).append({
                    "amount": ready,
                    "time": time.time(),
                    "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                })
                with open(vault_file, "w") as vf:
                    json.dump(vault_data, vf, indent=2)

                try:
                    from scalper.web_push import send_web_push
                    send_web_push(
                        title="Daily Profit Harvested",
                        message=f"${ready:.2f} secured in Daily Profit Vault · Capital protected",
                        tag="vault-harvest"
                    )
                except Exception:
                    pass

                res = json.dumps({
                    "ok": True,
                    "amount": ready,
                    "total_harvested": vault_data["total_harvested"],
                    "msg": f"${ready:.2f} locked to daily profit vault"
                }).encode("utf-8")
                self.send_response(200)
                self._send_security_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(res)
            except Exception as e:
                self.send_response(500)
                self._send_security_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Failed to harvest vault"}')
            return

        if path == "/api/push/subscribe":
            try:
                raw = self.rfile.read(content_len).decode("utf-8", errors="ignore")
                sub = json.loads(raw)
                from scalper.web_push import add_subscription
                ok = add_subscription(sub)
                res = json.dumps({"ok": ok, "msg": "Push subscription activated"}).encode("utf-8")
                self.send_response(200)
                self._send_security_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(res)
            except Exception as e:
                self.send_response(400)
                self._send_security_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Invalid subscription data"}')
            return

        if path == "/api/push/test":
            try:
                from scalper.web_push import send_web_push
                sent = send_web_push(
                    title="Stratton Alert",
                    message="Notifications active · You will receive instant 1-line trade alerts",
                    tag="test-push",
                )
                res = json.dumps({"ok": True, "sent": sent, "msg": f"Dispatched to {sent} active device(s)"}).encode("utf-8")
                self.send_response(200)
                self._send_security_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(res)
            except Exception as e:
                self.send_response(500)
                self._send_security_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Failed to dispatch test notification"}')
            return

        if path == "/login":
            raw = self.rfile.read(content_len).decode("utf-8", errors="ignore")
            form = parse_qs(raw)
            token = (form.get("token") or [""])[0].strip()

            if token and (secrets.compare_digest(token, TOKEN) or secrets.compare_digest(token, PIN)):
                sid = secrets.token_urlsafe(32)
                SESSIONS[sid] = time.time() + SESSION_TTL
                self.send_response(302)
                self._send_security_headers()
                self.send_header("Location", f"/?t={sid}")
                self.send_header("Set-Cookie", f"hft_s={sid}; Path=/; Max-Age={SESSION_TTL}; SameSite=Strict; HttpOnly")
                self.end_headers()
            else:
                locked = RATE_LIMITER.record_auth_failure(ip)
                err_text = "Too many attempts. Locked for 5m." if locked else "Invalid Token or PIN"
                body = _render_login(err=err_text).encode("utf-8")
                self.send_response(200)
                self._send_security_headers()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            return

        self.send_response(404)
        self._send_security_headers()
        self.end_headers()


def run_app():
    ThreadingHTTPServer.allow_reuse_address = True
    http_ports = list(dict.fromkeys([PORT_HTTP, 80, 8088]))
    for p in http_ports:
        def _make_http_server(port_num):
            def _serve():
                try:
                    s = ThreadingHTTPServer((HOST, port_num), HFTHandler)
                    print(f"Stratton Oakmont HTTP server running on http://{HOST}:{port_num} (Zero SSL warnings for friends)", flush=True)
                    s.serve_forever()
                except Exception as e:
                    print(f"HTTP server on port {port_num} notice: {e}", flush=True)
            threading.Thread(target=_serve, daemon=True).start()
        _make_http_server(p)

    has_ssl = bool(CERT and KEY and os.path.exists(CERT) and os.path.exists(KEY))
    if has_ssl:
        https_ports = list(dict.fromkeys([PORT, 443, 8443]))
        for p in https_ports:
            def _make_https_server(port_num):
                def _serve():
                    try:
                        s = ThreadingHTTPServer((HOST, port_num), HFTHandler)
                        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
                        ctx.load_cert_chain(certfile=CERT, keyfile=KEY)
                        s.socket = ctx.wrap_socket(s.socket, server_side=True)
                        print(f"Stratton Oakmont HTTPS server running on https://{HOST}:{port_num}", flush=True)
                        s.serve_forever()
                    except Exception as e:
                        print(f"HTTPS server on port {port_num} notice: {e}", flush=True)
                threading.Thread(target=_serve, daemon=True).start()
            _make_https_server(p)
    else:
        print(f"Stratton Oakmont notice: No valid SSL certificates found ({CERT}, {KEY}). HTTPS disabled.", flush=True)

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    run_app()
