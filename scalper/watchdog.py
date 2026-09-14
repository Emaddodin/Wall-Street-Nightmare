#!/usr/bin/env python3
"""All-time watchdog for the scalper paper system (isolated project).

Runs every few minutes via systemd.  Checks the services, the paper state,
the engine log, the app, the disk, memory and candle freshness, and pushes
an ntfy alarm when anything breaks.  Alarms are deduplicated: the same
failing check re-alarms every 15 minutes while the condition persists.

    watchdog.py [--test]     # --test sends one test alarm and exits
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent          # watchdog.py lives at the project root
DATA = Path(os.getenv("SCALPER_DATA", str(ROOT / "data")))
STATE = DATA / "state" / "paper.json"
LIVE = DATA / "state" / "live.json"
CANDLES = DATA / "candles"
WATCH = DATA / "state" / "watchdog.json"

SERVICES = ["stratton-oakmont-paper", "stratton-oakmont-app", "ssh", "fail2ban",
            "stratton-oakmont-backup.timer"]
REALARM_S = 15 * 60
CANDLE_MAX_AGE_S = 10 * 60
DISK_MAX_PCT = 90
RAM_MIN_MB = 200


def ntfy(msg: str, priority: int = 5) -> bool:
    topic = os.getenv("NTFY_TOPIC")
    if not topic:
        # read the project .env when the unit does not export it
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("NTFY_TOPIC="):
                    topic = line.split("=", 1)[1].strip()
    if not topic:
        print("no NTFY_TOPIC configured; cannot alarm")
        return False
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}",
            data=msg.encode(),
            headers={"Title": "scalper-alarm", "Priority": str(priority),
                     "Tags": "warning,rotating_light"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        print("ntfy push failed:", e)
        return False


def load_watch() -> dict:
    if WATCH.exists():
        try:
            return json.loads(WATCH.read_text())
        except Exception:
            pass
    return {}


def save_watch(w: dict) -> None:
    WATCH.parent.mkdir(parents=True, exist_ok=True)
    tmp = WATCH.with_suffix(".tmp")
    tmp.write_text(json.dumps(w))
    tmp.replace(WATCH)


def alarm(key: str, msg: str) -> None:
    """Push the alarm, but only once every REALARM_S per key."""
    w = load_watch()
    now = time.time()
    prev = w.get(key, {})
    last = float(prev.get("last", 0))
    count = int(prev.get("count", 0)) + 1
    if now - last < REALARM_S:
        w[key] = {"last": last, "count": count}
        save_watch(w)
        return
    w[key] = {"last": now, "count": count}
    save_watch(w)
    print(f"ALARM {key}: {msg}")
    ntfy(f"{msg}\n(repeating #{count}; scalper watchdog)")


def sysctl_active(unit: str) -> bool:
    r = subprocess.run(["systemctl", "is-active", unit],
                       capture_output=True, text=True, timeout=15)
    return r.stdout.strip() == "active"


def checks() -> None:
    # 1 -- services
    for unit in SERVICES:
        if not sysctl_active(unit):
            alarm(f"svc:{unit}", f"{unit} is DOWN")

    # 2 -- paper state finite & consistent
    try:
        st = json.loads(STATE.read_text())
        eq = st.get("equity")
        if eq is None or not math.isfinite(float(eq)):
            alarm("state:equity", f"equity is not finite: {eq!r}")
        for pos in st.get("positions", []):
            for lot in pos.get("lots", []):
                for f in ("entry", "sl"):
                    v = lot.get(f)
                    if v is not None and not math.isfinite(float(v)):
                        alarm("state:lots", f"{pos.get('symbol')} lot {f} is not finite")
                        break
    except Exception as e:
        alarm("state:read", f"cannot read paper state: {e}")

    # 3 -- engine errors in the journal
    r = subprocess.run(
        ["journalctl", "-u", "stratton-oakmont-paper", "--since", "-6 min",
         "--no-pager"], capture_output=True, text=True, timeout=20)
    errs = sum(1 for line in r.stdout.splitlines()
               if ("traceback" in line.lower() or "step failed" in line.lower()
                   or "error" in line.lower()))
    if errs:
        alarm("log:errors", f"{errs} error line(s) in the paper journal")

    # 4 -- app reachable
    try:
        req = urllib.request.Request("https://127.0.0.1/")
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            if resp.status != 200:
                alarm("app:http", f"app returned HTTP {resp.status}")
    except Exception as e:
        alarm("app:http", f"app unreachable: {e}")

    # 5 -- disk
    r = subprocess.run(["df", "/"], capture_output=True, text=True, timeout=10)
    try:
        pct = int(r.stdout.splitlines()[-1].split()[4].rstrip("%"))
        if pct >= DISK_MAX_PCT:
            alarm("disk", f"disk is {pct}% full")
    except Exception:
        pass

    # 6 -- memory
    r = subprocess.run(["free", "-m"], capture_output=True, text=True, timeout=10)
    try:
        avail = int(r.stdout.splitlines()[1].split()[6])
        if avail < RAM_MIN_MB:
            alarm("ram", f"only {avail} MB RAM available")
    except Exception:
        pass

    # 7 -- candle freshness
    try:
        newest = max(CANDLES.glob("*.parquet"),
                     key=lambda p: p.stat().st_mtime, default=None)
        if newest is not None and time.time() - newest.stat().st_mtime > CANDLE_MAX_AGE_S:
            alarm("candles", "no fresh candles: data feed looks stalled")
    except Exception as e:
        alarm("candles", f"candle check failed: {e}")

    # 8 -- evaluation liveness: the paper trader must keep advancing
    # (rejections or trades written within the last 15 min).  A stuck
    # incremental loop is silent -- this turns it into an alarm.
    try:
        ev_mt = max(
            (ROOT / "data" / "logs" / "rejections.jsonl").stat().st_mtime,
            (ROOT / "data" / "logs" / "trades.jsonl").stat().st_mtime)
        if time.time() - ev_mt > 15 * 60:
            alarm("engine:stuck",
                  "paper evaluator silent >15 min (rejections+trades stale)")
            # SELF-HEAL: a restart re-arms the evaluator from scratch.
            # At most once per hour so a deeper fault cannot cause a
            # restart loop.
            w = load_watch()
            last = float(w.get("heal:restart", {}).get("last", 0))
            if time.time() - last > 3600:
                w["heal:restart"] = {"last": time.time()}
                save_watch(w)
                subprocess.run(["systemctl", "restart", "stratton-oakmont-paper"],
                               capture_output=True, timeout=60)
                ntfy("the bot went silent and restarted itself "
                     "(stratton-oakmont-paper). It will not sleep.")
    except Exception:
        pass

    # 9 -- live price feed for open positions
    if LIVE.exists():
        try:
            age = time.time() - LIVE.stat().st_mtime
            if age > 5 * 60:
                alarm("live-prices", f"live price file stale ({int(age/60)} min)")
        except Exception:
            pass


def main() -> None:
    if "--test" in sys.argv:
        print("test alarm")
        ntfy("test alarm: the scalper watchdog is armed and working")
        return
    checks()


if __name__ == "__main__":
    main()
