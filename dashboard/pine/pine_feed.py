"""Serve the VPS Kronos forecasts as ready-to-paste lines for the TradingView indicator.

It does not run Kronos and does not touch the Kronos chart service: it polls that service's
latest forecast, turns each new one into a line

    KRONOS|<open time of the last closed bar, UTC s>|<its close>|<ATR14>|<model>|<p1,...,pN>|<bar s>

(bar s = 300 for M5 forecasts, 60 for M1) and serves the last --keep lines (newest last) as plain
text, so one copy-paste into the indicator's "Kronos forecasts" box brings the recent forecasts with it.

    python3 pine_feed.py                     # finds the forecast JSON of the chart service on :8790
    python3 pine_feed.py --source 'http://127.0.0.1:8790/<forecast json path>?k=TOKEN'

With no --source it reads the chart page on 127.0.0.1:8790, tries the JSON addresses the page uses
(and a few usual ones) and keeps the first that holds a forecast path; Gold Desk's own forecast on
127.0.0.1:8765 is the fallback.

    http://<vps>:8791/?k=TOKEN          all kept lines
    http://<vps>:8791/latest?k=TOKEN    the newest line only

The token is the chart service's own (/root/kronos/chart/token.txt) unless --token is given.
Standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import threading
import time
import traceback
import urllib.request
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

LF_URL = "https://my.litefinance.org/chart/get-history?symbol=XAUUSD&resolution={res}&from={a}&to={b}"
CHART = "http://127.0.0.1:8790"
GOLDDESK = "http://127.0.0.1:8765/api/state"
GUESSES = ["/api/state", "/api/forecast", "/api/kronos", "/api/latest", "/api/data", "/api/chart", "/state",
           "/forecast", "/kronos", "/latest", "/data", "/data.json", "/forecast.json", "/state.json"]
TF_SEC = {"M1": 60, "M5": 300, "M15": 900, "1": 60, "5": 300}


def get(url: str, token: str | None = None) -> bytes:
    headers = {"User-Agent": "Mozilla/5.0 GoldDesk-pine"}
    if token:
        headers["Cookie"] = f"k={token}"
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20).read()


def get_json(url: str, token: str | None = None):
    return json.loads(get(url, token))


def with_key(url: str, token: str) -> str:
    return url if "k=" in url else url + ("&" if "?" in url else "?") + "k=" + token


def discover(token: str) -> list:
    """JSON addresses the chart page uses, then the usual guesses."""
    found: list = []
    try:
        html = get(with_key(CHART + "/", token), token).decode("utf-8", "replace")
        texts = [html]
        for src in re.findall(r"""<script[^>]+src=["']([^"']+)["']""", html)[:5]:
            if src.startswith("/") and not src.startswith("//"):
                try:
                    texts.append(get(with_key(CHART + src, token), token).decode("utf-8", "replace"))
                except Exception:
                    pass
        for t in texts:
            for m in re.findall(r"""["'`](/[A-Za-z0-9_\-./]*)(?:\?[^"'`]*)?["'`]""", t):
                if m != "/" and not re.search(r"\.(js|css|png|ico|svg|woff2?|ttf|html?|map)$", m) and m not in found:
                    found.append(m)
    except Exception:
        pass
    return [CHART + u for u in found + [g for g in GUESSES if g not in found]]


def find(d, *names):
    """First value under any of `names`, searching nested dicts (e.g. {"kronos": {...}})."""
    if isinstance(d, dict):
        for n in names:
            if n in d and d[n] is not None:
                return d[n]
        for v in d.values():
            r = find(v, *names)
            if r is not None:
                return r
    return None


def owner(d, name):
    """The dict that holds `name`, searching nested dicts."""
    if isinstance(d, dict):
        if d.get(name) is not None:
            return d
        for v in d.values():
            r = owner(v, name)
            if r is not None:
                return r
    return None


def secs(x) -> int | None:
    """A time as UTC seconds: epoch seconds or milliseconds, or an ISO string."""
    if isinstance(x, (int, float)) or (isinstance(x, str) and re.fullmatch(r"\d+(\.\d+)?", x)):
        t = int(float(x))
        return t // 1000 if t > 10 ** 11 else t
    if isinstance(x, str):
        try:
            d = datetime.fromisoformat(x.replace("Z", "+00:00"))
            return int((d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp())
        except ValueError:
            return None
    return None


def path_closes(p) -> list:
    """Forecast closes from a list of numbers, {value|close: x} dicts, or [t, o, h, l, c] candles."""
    out = []
    for x in p or []:
        if isinstance(x, (int, float)):
            out.append(float(x))
        elif isinstance(x, dict):
            v = x.get("close", x.get("value", x.get("c")))
            if v is not None:
                out.append(float(v))
        elif isinstance(x, (list, tuple)) and len(x) >= 5:
            out.append(float(x[4]))
    return out


def extract(doc) -> tuple[int, list, int, str | None] | None:
    """(open time in UTC s of the last bar Kronos read, forecast closes, bar seconds, model) from the
    service's JSON."""
    for name in ("path", "forecast", "pred", "prediction", "kronos"):
        outer = home = owner(doc, name)
        if home is None:
            continue
        raw = home[name]
        if isinstance(raw, dict):
            home, raw = raw, find(raw, "path", "close", "closes", "candles", "values")
        closes = path_closes(raw)
        if len(closes) < 2:
            continue
        times = [secs(x.get("time", x.get("t"))) if isinstance(x, dict) else
                 (secs(x[0]) if isinstance(x, (list, tuple)) else None) for x in raw]
        step = None
        if len(times) >= 2 and times[0] and times[1]:
            step = times[1] - times[0]
        if step not in (60, 300):
            tf = find(home, "tf", "timeframe", "interval") or find(doc, "tf", "timeframe", "interval")
            step = TF_SEC.get(str(tf).upper().replace("MIN", ""), 300)
        t = times[0] - step if times and times[0] else None    # the path starts one bar after the last bar read
        for d in (home, outer, doc):
            for k in ("t", "base_time", "last_time", "t_last", "bar_time", "asof", "time"):
                if t:
                    break
                if k in d and not isinstance(d[k], (list, dict)):
                    t = secs(d[k])
        if not t:
            continue
        model = find(home, "model") or find(doc, "model")
        return t - t % step, closes, step, model if isinstance(model, str) else None
    return None


def bars_upto(t: int, step: int = 60) -> list:
    """Closed LiteFinance bars [t, o, h, l, c] of `step` seconds ending at bar t (the bar Kronos read last)."""
    d = get_json(LF_URL.format(res=step // 60, a=t - 40 * step, b=t + step))
    d = d.get("data", d)
    rows = [[int(x), d["o"][i], d["h"][i], d["l"][i], d["c"][i]] for i, x in enumerate(d.get("t") or [])]
    return [r for r in rows if r[0] <= t]


def atr14(rows: list) -> float:
    trs = [max(r[2], p[4]) - min(r[3], p[4]) for p, r in zip(rows[-15:-1], rows[-14:])]
    return sum(trs) / len(trs) if trs else 0.0


def make_line(t: int, closes: list, model: str, step: int = 60) -> str | None:
    rows = bars_upto(t, step)
    if len(rows) < 15 or rows[-1][0] != t:
        return None
    path = ",".join(f"{c:.2f}" for c in closes)
    return f"KRONOS|{t}|{rows[-1][4]:.2f}|{atr14(rows):.2f}|{model}|{path}|{step}"


class Feed:
    def __init__(self, a, token: str):
        self.a, self.token = a, token
        self.source = a.source if a.source != "auto" else None
        self.misses = 0
        self.lines: deque = deque(maxlen=a.keep)
        self.file = Path(a.out)
        if self.file.exists():
            self.lines.extend(ln for ln in self.file.read_text().splitlines() if ln.startswith("KRONOS|"))
        self.done_t = int(self.lines[-1].split("|")[1]) if self.lines else 0
        self.error = None

    def find_source(self) -> None:
        for url in discover(self.token) + [GOLDDESK]:
            try:
                if extract(get_json(with_key(url, self.token), self.token)):
                    self.source = with_key(url, self.token)
                    print(f"Reading forecasts from {url}", flush=True)
                    return
            except Exception:
                continue
        self.error = "no forecast JSON found on the Kronos chart (:8790) or Gold Desk (:8765) yet"

    def loop(self) -> None:
        while True:
            try:
                if not self.source:
                    self.find_source()
                got = extract(get_json(self.source, self.token)) if self.source else None
                if got and got[0] > self.done_t:
                    line = make_line(got[0], got[1], got[3] or self.a.model, got[2])
                    if line:
                        self.lines.append(line)
                        self.file.parent.mkdir(parents=True, exist_ok=True)
                        self.file.write_text("\n".join(self.lines) + "\n")
                        self.done_t = got[0]
                if self.source:
                    self.error = None if got else "no forecast found in the source JSON"
                self.misses = 0 if got else self.misses + 1
            except Exception as e:
                self.error = str(e)
                self.misses += 1
                traceback.print_exc()
            if self.misses >= 12 and self.a.source == "auto":
                self.source, self.misses = None, 0          # look again (the chart may have changed)
            time.sleep(self.a.every)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="auto", help="URL of the chart service's latest-forecast JSON (default: find it)")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--token", help="access key (default: /root/kronos/chart/token.txt)")
    ap.add_argument("--keep", type=int, default=12, help="how many recent forecasts to serve (keep the paste short)")
    ap.add_argument("--model", default="Kronos-base")
    ap.add_argument("--every", type=float, default=5, help="seconds between polls")
    ap.add_argument("--out", default="/root/kronos/pine/pine_lines.txt")
    a = ap.parse_args()
    token = a.token or Path("/root/kronos/chart/token.txt").read_text().strip()
    feed = Feed(a, token)
    threading.Thread(target=feed.loop, daemon=True).start()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlparse(self.path)
            if parse_qs(u.query).get("k", [""])[0] != token:
                self.send_error(403)
                return
            if u.path == "/latest":
                body = feed.lines[-1] if feed.lines else ""
            else:
                body = "\n".join(feed.lines)
            if not feed.lines:
                body = f"# no forecast yet{': ' + feed.error if feed.error else ''}"
            data = (body + "\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    print(f"Pine feed on :{a.port}, reading {a.source.split('?')[0]}", flush=True)  # "auto" = find it
    ThreadingHTTPServer((a.host, a.port), H).serve_forever()


if __name__ == "__main__":
    main()
