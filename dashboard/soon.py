"""Heads-up: a setup is predicted to form soon, pushed to your phone with ntfy.

After every closed entry bar (M5) and its Kronos forecast, Gold Desk looks 30 minutes ahead, during London and
New York hours only, and sends one push when it expects one of these (pushed only inside your trading session,
coach.py; outside it they show on the page):

  zone   The higher timeframes allow the trade (H4 trend and H1 structure point that way, no shock against
         it), at least 6 of 10 Kronos paths end the forecast on the trade's side, and a quarter of the paths
         reach a fresh M15 demand zone (supply for a sell) within 30 minutes without running far through it.
         That is where the indicator takes its entries.
  sweep  Same filter, but the paths dip below the last 10 bars' low (above the high for a sell) and the
         average path is back above it 30 minutes out: the liquidity sweep the indicator trades.
(BOOM / CRASH calls are limit orders now, pushed when they are placed, so they need no heads-up of their own.)

Never twice for the same setup, at most one push per side per hour and one per half hour (entry setups and
BOOM / CRASH counted apart), 8 a day of which 5 BOOM / CRASH, none while a trade or call on that side is
running, none when both sides qualify at once. A heads-up to watch the chart, not a signal; nothing here
places an order. Pushes go to your own ntfy topic: --ntfy-topic or ~/.golddesk/ntfy_topic if set, else
NTFY_TOPIC in /root/ict_sniper/.env (the topic your trading bots use; never the shared ones).
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from engine import LADDER, session_ok

LOOK_MIN = 30          # how far ahead a setup counts as "soon"
COOLDOWN_MIN = 60      # per side, separately for entry setups (zone, sweep) and BOOM / CRASH (burst)
DAY_CAP = 8            # pushes a day in all ...
BURST_CAP = 5          # ... of which BOOM / CRASH heads-ups at most this many, so entry setups always get through
THROUGH_ATR = 1.0      # paths that run further than this through a zone or level break it rather than test it
MAJORITY = 0.6         # share of paths that must end on the trade's side (6 of 10)
GAP_MIN = 30           # between two heads-ups of the same group
SAME_MIN = 240         # the same setup (side, kind, price area) is announced once in this long
HOME = Path.home() / ".golddesk"
BOT_ENV = Path("/root/ict_sniper/.env")
SIDE = {1: "BUY", -1: "SELL"}
GROUP = {"zone": "entry", "sweep": "entry", "burst": "boom"}


def predict(eng, fc: dict, sec: int) -> list:
    """Setups Kronos and the indicator expect within LOOK_MIN minutes: at most one per side."""
    ctx, p = eng.ctx, eng.p
    path = [x["value"] for x in fc.get("path") or []]
    n = max(1, LOOK_MIN * 60 // sec)
    if ctx is None or len(path) < n or len(eng.m1) <= p.sweep_len or not fc.get("atr"):
        return []
    a, last, t = fc["atr"], fc["last"], fc["t"]
    band = fc.get("band") or []
    low = [b["p25"] for b in band[:n]] if len(band) >= n else path[:n]      # where a quarter of the paths dip to
    high = [b["p75"] for b in band[:n]] if len(band) >= n else path[:n]
    up = fc.get("up_prob")
    macro, struct, zone_tf = LADDER.get({60: "M1", 300: "M5"}.get(eng.sec, "M1"))
    m, i = eng.m1, len(eng.m1) - 1
    swing = {1: min(m.l[i - p.sweep_len + 1:i + 1]), -1: max(m.h[i - p.sweep_len + 1:i + 1])}
    out = []
    for d in (1, -1):
        reach = low if d == 1 else high              # how far the paths go against the trade first
        if up is not None:
            majority = (up if d == 1 else 1 - up) >= MAJORITY
        else:
            majority = (fc.get("move") or 0) * d > 0
        htf = ctx["regime"] == d and ctx["bias15"] == d and ctx["shock"] != -d
        why = [f"{macro} trend {'up' if d == 1 else 'down'}", f"{struct} structure {'bullish' if d == 1 else 'bearish'}"]
        in_session = [session_ok(t + (k + 1) * sec, p, eng.offset_fn) for k in range(n)]
        pred = None
        if htf and majority:
            for k in range(n):                       # zone: the first bar the paths reach a fresh zone of this side
                if not in_session[k]:
                    continue
                for z in eng.zones:
                    if z["dir"] != d:
                        continue
                    edge, far = (z["top"], z["bottom"]) if d == 1 else (z["bottom"], z["top"])
                    if (last - edge) * d > 0 and (reach[k] - edge) * d <= 0 and (far - reach[k]) * d < THROUGH_ATR * a:
                        pred = {"kind": "zone", "k": k + 1, "area": [z["bottom"], z["top"]],
                                "what": f"pull back into the {zone_tf} {'demand' if d == 1 else 'supply'} zone"}
                        break
                if pred:
                    break
            lvl = swing[d]                           # sweep: beyond the last bars' low / high, back by the window's end
            if pred is None and (last - lvl) * d > 0 and (path[n - 1] - lvl) * d > 0:
                for k in range(n):
                    if in_session[k] and 0 < (lvl - reach[k]) * d < THROUGH_ATR * a:
                        pred = {"kind": "sweep", "k": k + 1, "area": sorted([lvl, lvl - d * 0.3 * a]),
                                "what": f"sweep the recent {'low' if d == 1 else 'high'} ({lvl:.2f}) and turn back"}
                        break
        if pred:
            pred.update(dir=d, side=SIDE[d], t=t, minutes=pred["k"] * sec // 60, why=why, up_prob=up,
                        horizon_min=fc.get("minutes"), area=[round(x, 2) for x in sorted(pred["area"])])
            out.append(pred)
    return out


def message(pred: dict) -> tuple:
    d, (lo, hi) = pred["dir"], pred["area"]
    title = f"Gold {pred['side']} setup likely in ~{pred['minutes']} min"
    lines = [f"Kronos sees price {pred['what']} in about {pred['minutes']} min. Watch {lo:.2f}-{hi:.2f}."]
    if pred["why"]:
        lines.append(", ".join(pred["why"]) + ".")
    if pred.get("up_prob") is not None:
        share = pred["up_prob"] if d == 1 else 1 - pred["up_prob"]
        lines.append(f"{share:.0%} of Kronos paths end {'higher' if d == 1 else 'lower'} "
                     f"in {pred.get('horizon_min')} min.")
    lines.append("Heads-up only: wait for the signal on the chart. Untested.")
    return title, "\n".join(lines)


# ------------------------------------------------------------------ ntfy
def _env_value(path: Path, key: str) -> str:
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def find_topic(explicit: str | None = None) -> tuple:
    """(topic, where it came from). Only your own topic, never the bots' shared ones."""
    if explicit:
        return explicit, "--ntfy-topic"
    if os.environ.get("GOLDDESK_NTFY_TOPIC"):
        return os.environ["GOLDDESK_NTFY_TOPIC"], "GOLDDESK_NTFY_TOPIC"
    try:
        v = (HOME / "ntfy_topic").read_text().strip()
        if v:
            return v, "Gold Desk's own topic"
    except OSError:
        pass
    v = os.environ.get("NTFY_TOPIC") or _env_value(BOT_ENV, "NTFY_TOPIC")
    if v and v.split(",")[0].strip():
        return v.split(",")[0].strip(), "the topic your trading bots use"
    return "", ""


def ntfy_server() -> str:
    return (os.environ.get("NTFY_URL") or _env_value(BOT_ENV, "NTFY_URL") or "https://ntfy.sh").rstrip("/")


class Ntfy:
    """Pushes in the background, so a slow network never holds up the dashboard."""

    def __init__(self, topic: str, server: str):
        self.topic, self.server = topic, server
        self.q: queue.Queue = queue.Queue(maxsize=50)
        self.sent, self.error = 0, None
        threading.Thread(target=self._loop, daemon=True).start()

    def push(self, title: str, body: str, priority: str = "high", tags: str = "") -> None:
        try:
            self.q.put_nowait((title, body, priority, tags))
        except queue.Full:
            pass

    def _loop(self) -> None:
        url = f"{self.server}/{urllib.parse.quote(self.topic)}"
        while True:
            title, body, priority, tags = self.q.get()
            for attempt in range(3):
                try:
                    req = urllib.request.Request(url, data=body.encode("utf-8"), method="POST", headers={
                        "Title": title.encode("ascii", "replace").decode(), "Priority": priority, "Tags": tags})
                    urllib.request.urlopen(req, timeout=10).read()
                    self.sent, self.error = self.sent + 1, None
                    break
                except Exception as e:
                    self.error = f"ntfy push failed: {e}"
                    time.sleep(3 * (attempt + 1))


class SoonAlerts:
    """Decides which predictions become a push (cooldowns, daily cap) and remembers them across restarts."""

    def __init__(self, topic: str = "", source: str = "", server: str = "https://ntfy.sh", push: bool = True,
                 file: Path | None = HOME / "soon_alerts.json", ntfy: Ntfy | None = None, allow=None,
                 hello: bool = True):
        self.file, self.topic, self.source = file, topic, source
        self.ntfy = ntfy or (Ntfy(topic, server) if (topic and push) else None)
        self.allow = allow or (lambda t: True)      # e.g. only inside your trading session (coach.py)
        self.data = {"last": {}, "day": ["", 0, 0], "history": [], "hello": ""}
        try:
            self.data.update(json.loads(file.read_text()))
        except (OSError, ValueError, AttributeError):
            pass
        if len(self.data["day"]) != 3:
            self.data["day"] = ["", 0, 0]
        if self.ntfy and hello and self.data.get("hello") != topic:
            self.ntfy.push("Gold Desk alerts are on",
                           "You'll get a heads-up here when Kronos and the indicator expect a gold setup "
                           f"within about {LOOK_MIN} minutes: London and New York hours, at most {DAY_CAP} a day.",
                           "default", "bell")
            self.data["hello"] = topic
            self._save()

    def check(self, eng, fc: dict, sec: int, busy: set) -> list:
        """Called with each fresh forecast. Returns the heads-up sent now, if any."""
        preds = predict(eng, fc, sec)
        if len(preds) != 1:                          # nothing, or both sides at once: no clear heads-up
            return []
        pr = preds[0]
        d, t, g, last = pr["dir"], pr["t"], GROUP[pr["kind"]], self.data["last"]
        if d in busy or t - last.get(f"{g}:{d}", 0) < COOLDOWN_MIN * 60 \
                or t - max(last.get(f"{g}:1", 0), last.get(f"{g}:-1", 0)) < GAP_MIN * 60:
            return []
        lo, hi = pr["area"]
        if any(h["dir"] == d and h["kind"] == pr["kind"] and t - h["t"] < SAME_MIN * 60
               and max(lo, h["area"][0]) <= min(hi, h["area"][1]) for h in self.data["history"]):
            return []                                # already announced this one
        day = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")
        if self.data["day"][0] != day:
            self.data["day"] = [day, 0, 0]
        if self.data["day"][1] >= DAY_CAP or (g == "boom" and self.data["day"][2] >= BURST_CAP):
            return []
        last[f"{g}:{d}"] = t
        self.data["day"][1] += 1
        self.data["day"][2] += g == "boom"
        pr["title"], pr["text"] = message(pr)
        self.data["history"] = (self.data["history"] + [pr])[-20:]
        self._save()
        if self.ntfy and self.allow(time.time()):
            self.ntfy.push(pr["title"], pr["text"], "high",
                           "chart_with_upwards_trend" if d == 1 else "chart_with_downwards_trend")
        return [pr]

    def _save(self) -> None:
        if not self.file:
            return
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.file.write_text(json.dumps(self.data))
        except OSError:
            pass

    def state(self) -> dict:
        n, tp = self.ntfy, self.topic
        return {"push": bool(n), "topic": (tp[:4] + "..." + tp[-2:] if len(tp) > 8 else "...") if tp else None,
                "topic_source": self.source or None,                 # the topic stays on this machine, shortened
                "sent": n.sent if n else 0, "error": n.error if n else None,
                "recent": self.data["history"][-5:],
                "rules": {"look_min": LOOK_MIN, "cooldown_min": COOLDOWN_MIN, "gap_min": GAP_MIN,
                          "day_cap": DAY_CAP, "burst_cap": BURST_CAP, "majority": MAJORITY}}
