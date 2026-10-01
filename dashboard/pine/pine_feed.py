"""Serve the VPS Kronos forecasts as ready-to-paste lines for the TradingView indicator.

It does not run Kronos and does not touch the Kronos chart service: it polls that service's
latest forecast, turns each new one into a line

    KRONOS|<open time of the last closed M1 bar, UTC s>|<its close>|<ATR14>|<model>|<p1,...,pN>

and serves the last --keep lines (newest last) as plain text, so one copy-paste into the
indicator's "Kronos forecasts" box brings the recent forecasts with it.

    python3 pine_feed.py --source 'http://127.0.0.1:8790/<forecast json path>?k=TOKEN'

    http://<vps>:8791/?k=TOKEN          all kept lines
    http://<vps>:8791/latest?k=TOKEN    the newest line only

The token is the chart service's own (/root/kronos/chart/token.txt) unless --token is given.
Standard library only.
"""
from __future__ import annotations

import argparse
import json
import threading
import time
import traceback
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

LF_URL = "https://my.litefinance.org/chart/get-history?symbol=XAUUSD&resolution=1&from={a}&to={b}"


def get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 GoldDesk-pine"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


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


def extract(doc) -> tuple[int, list] | None:
    """(open time in UTC s of the last bar Kronos read, forecast closes) from the service's JSON."""
    closes = path_closes(find(doc, "path", "forecast", "candles", "pred", "prediction"))
    t = find(doc, "t", "base_time", "last_time", "t_last", "time")
    if not closes or t is None:
        return None
    t = int(float(t))
    t = t // 1000 if t > 10 ** 11 else t
    return t - t % 60, closes


def bars_upto(t: int) -> list:
    """Closed LiteFinance M1 bars [t, o, h, l, c] ending at bar t (the bar Kronos read last)."""
    d = get_json(LF_URL.format(a=t - 3 * 3600, b=t + 60))
    d = d.get("data", d)
    rows = [[int(x), d["o"][i], d["h"][i], d["l"][i], d["c"][i]] for i, x in enumerate(d.get("t") or [])]
    return [r for r in rows if r[0] <= t]


def atr14(rows: list) -> float:
    trs = [max(r[2], p[4]) - min(r[3], p[4]) for p, r in zip(rows[-15:-1], rows[-14:])]
    return sum(trs) / len(trs) if trs else 0.0


def make_line(t: int, closes: list, model: str) -> str | None:
    rows = bars_upto(t)
    if len(rows) < 15 or rows[-1][0] != t:
        return None
    path = ",".join(f"{c:.2f}" for c in closes)
    return f"KRONOS|{t}|{rows[-1][4]:.2f}|{atr14(rows):.2f}|{model}|{path}"


class Feed:
    def __init__(self, a):
        self.a = a
        self.lines: deque = deque(maxlen=a.keep)
        self.file = Path(a.out)
        if self.file.exists():
            self.lines.extend(ln for ln in self.file.read_text().splitlines() if ln.startswith("KRONOS|"))
        self.done_t = int(self.lines[-1].split("|")[1]) if self.lines else 0
        self.error = None

    def loop(self) -> None:
        while True:
            try:
                got = extract(get_json(self.a.source))
                if got and got[0] > self.done_t:
                    line = make_line(got[0], got[1], self.a.model)
                    if line:
                        self.lines.append(line)
                        self.file.parent.mkdir(parents=True, exist_ok=True)
                        self.file.write_text("\n".join(self.lines) + "\n")
                        self.done_t = got[0]
                self.error = None if got else "no forecast found in the source JSON"
            except Exception as e:
                self.error = str(e)
                traceback.print_exc()
            time.sleep(self.a.every)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="URL of the chart service's latest-forecast JSON")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--token", help="access key (default: /root/kronos/chart/token.txt)")
    ap.add_argument("--keep", type=int, default=12, help="how many recent forecasts to serve (keep the paste short)")
    ap.add_argument("--model", default="Kronos-base")
    ap.add_argument("--every", type=float, default=5, help="seconds between polls")
    ap.add_argument("--out", default="/root/kronos/pine/pine_lines.txt")
    a = ap.parse_args()
    token = a.token or Path("/root/kronos/chart/token.txt").read_text().strip()
    feed = Feed(a)
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

    print(f"Pine feed on :{a.port}, reading {a.source.split('?')[0]}", flush=True)
    ThreadingHTTPServer((a.host, a.port), H).serve_forever()


if __name__ == "__main__":
    main()
