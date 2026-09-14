#!/usr/bin/env python3
"""
Put the two chart tabs into two side-by-side windows on the VNC screen.

Both chart tabs normally live in ONE Chrome window, so the desktop shows
only whichever tab is active -- the other chart exists but is invisible.
Splitting them into two windows, each half the 1280x800 screen, makes the
VNC desktop (and the app's "open the desktop" page) show both charts live
at once.

The split is stable for the services by construction: window 0 is always
the chart tab with the smallest CDP target id, window 1 the next (see
signals/tv_cdp.py), and this tool only ever moves the SECOND tab into a
new window. The book's tab is never touched, never navigated, never
closed.

Idempotent: run it any time, it re-checks and does nothing when the
layout is already split. Run it again after any Chrome restart.

    python3 tools/twocharts.py [--port 9222] [--width 640] [--height 780]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

try:
    import websocket
except ImportError:
    websocket = None

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from signals.tv_cdp import TradingViewCDP  # noqa: E402

CHART_URL_FILE = ROOT / "data" / "chart_url.txt"
WANTED = ROOT / "data" / "chart_coins.json"


def get(h, p, what="json"):
    return requests.get(f"http://{h}:{p}/{what}", timeout=10).json()


def chart_pages(host, port):
    ts = get(host, port)
    pages = [t for t in ts if t.get("type") == "page"
             and "tradingview.com/chart" in (t.get("url") or "")]
    pages.sort(key=lambda t: t.get("id") or "")
    return pages


def browser_ws(host, port):
    if websocket is None:
        print("no websocket library on this interpreter", file=sys.stderr)
        raise SystemExit(2)
    v = get(host, port, "json/version")
    return websocket.create_connection(v["webSocketDebuggerUrl"],
                                       timeout=30, suppress_origin=True)


def call(ws, mid, method, params=None):
    ws.send(json.dumps({"id": mid, "method": method,
                        "params": params or {}}))
    while True:
        got = json.loads(ws.recv())
        if got.get("id") == mid:
            if "error" in got:
                raise RuntimeError(f"{method}: {got['error']}")
            return got.get("result", {})


def window_of(ws, target_id):
    try:
        return call(ws, 1, "Browser.getWindowForTarget",
                    {"targetId": target_id}).get("windowId")
    except RuntimeError:
        return None


def set_bounds(ws, window_id, left, top, width, height):
    call(ws, 2, "Browser.setWindowBounds",
         {"windowId": window_id,
          "bounds": {"left": left, "top": top, "width": width,
                     "height": height, "windowState": "normal"}})
    time.sleep(1.5)


def perched_coin():
    try:
        got = json.loads(WANTED.read_text())
        if got:
            return str(got[0]).upper().replace(".P", "")
    except Exception:
        pass
    return None


def tab_symbol(index, host, port):
    try:
        c = TradingViewCDP(host=host, port=port, target_index=index)
        try:
            st = c.state()
            return str(st.symbol).split(":")[-1].replace(".P", "").upper()
        finally:
            c.close()
    except Exception as e:
        return f"<unreadable: {str(e)[:40]}>"


def wait_for_two_charts(host, port, seconds=100):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if len(chart_pages(host, port)) >= 2:
            return True
        time.sleep(3)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=780)
    a = ap.parse_args()

    if not wait_for_two_charts(a.host, a.port):
        print("only one chart tab -- is Chrome still restoring its session?")
        return 1

    pages = chart_pages(a.host, a.port)
    ws = browser_ws(a.host, a.port)
    w0 = window_of(ws, pages[0]["id"])
    w1 = window_of(ws, pages[1]["id"])

    if w0 is None or w1 is None:
        print("could not read the browser windows over CDP")
        return 1

    # Identity check before anything moves. Window 0 must be the perched
    # coin -- the book reads window 0 and the eagle parks it. If that does
    # not hold, the tab order is ambiguous and moving anything is the one
    # mistake this tool exists to avoid.
    want = perched_coin()
    got0 = tab_symbol(0, a.host, a.port)
    if want and got0 != want:
        print(f"refusing to move anything: window 0 is on {got0} but the "
              f"eagle says it should be perched on {want}")
        return 2
    print(f"window 0 = {got0} (the book's, untouched)")

    if w0 != w1:
        print("already split -- just making sure the geometry is right")
        set_bounds(ws, w0, 0, 0, a.width, a.height)
        set_bounds(ws, w1, a.width, 0, a.width, a.height)
        print("done: two windows side by side")
        return 0

    # The split itself: close the scout's tab and reopen it in its own
    # window, from the saved layout URL, so the TBT study comes with it.
    url = ""
    try:
        url = CHART_URL_FILE.read_text().strip()
    except OSError:
        pass
    if not url:
        url = pages[1].get("url") or ""
    if "tradingview.com/chart" not in url:
        print(f"no saved chart layout URL ({url!r}) -- nothing to rebuild "
              "the scout window from")
        return 3

    print("closing the scout's tab and reopening it in its own window...")
    call(ws, 3, "Target.closeTarget", {"targetId": pages[1]["id"]})
    time.sleep(2)
    new = call(ws, 4, "Target.createTarget",
               {"url": url, "newWindow": True})
    new_id = new.get("targetId")

    # Wait for the new tab to become a live chart with the study computed.
    deadline = time.time() + 150
    ok = False
    while time.time() < deadline:
        pages = chart_pages(a.host, a.port)
        if len(pages) >= 2 and any(t.get("id") == new_id for t in pages):
            try:
                c = TradingViewCDP(host=a.host, port=a.port, target_index=1)
                try:
                    if c.studies():
                        ok = True
                        break
                finally:
                    c.close()
            except Exception:
                pass
        time.sleep(4)
    if not ok:
        print("the new scout tab did not come up with the TBT study in "
              "time -- the guard will report it; re-run this tool")
        return 4

    # Now put each window on its own half of the screen.
    pages = chart_pages(a.host, a.port)
    w0 = window_of(ws, pages[0]["id"])
    w1 = window_of(ws, pages[1]["id"])
    if w0 is None or w1 is None or w0 == w1:
        print("windows did not split as expected")
        return 5
    set_bounds(ws, w0, 0, 0, a.width, a.height)
    set_bounds(ws, w1, a.width, 0, a.width, a.height)
    s0 = tab_symbol(0, a.host, a.port)
    s1 = tab_symbol(1, a.host, a.port)
    print(f"done: window 0 ({s0}) left, window 1 ({s1}) right -- "
          f"{a.width}x{a.height} each")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
