#!/usr/bin/env python3
"""All-time watchdog for the ICT Sniper (Hyperliquid paper system).

Same shape as the scalper watchdog that ran on the old server: it runs
every few minutes from systemd, checks the things that break without
looking like a crash, repairs what it can, and pushes an ntfy alarm for
what it cannot.  Alarms are deduplicated -- the same failing check
re-alarms every 15 minutes while the condition persists.

What it watches here:
  1. the two services (bot + phone app) are actually active
  2. the book state is readable and finite
  3. the PAPER LOCK is intact -- if it ever came off, that is the one
     alarm that matters most (real money risk)
  4. the bot is alive: paper.json keeps being republished (a hung asyncio
     loop is silent -- this turns it into an alarm + one restart)
  5. the journal has no error lines in the last 6 minutes
  6. the phone app answers on 8443
  7. disk / RAM headroom, bot RSS (leak detector)
  8. live price file fresh whenever a position is open
  9. the Hyperliquid API is reachable (the bot trades nothing without it)
 10. fail2ban is up (the box's own guard, same as the old server)

    hl_watchdog.py [--test]     # --test sends one test alarm and exits
"""
from __future__ import annotations

import json
import math
import os
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent          # lives in /root/ict_sniper
DATA = Path(os.getenv("SCALPER_DATA", str(ROOT / "data")))
STATE = DATA / "state" / "paper.json"
LIVE = DATA / "state" / "live.json"
WATCH = DATA / "state" / "hl_watchdog.json"

BOT_UNIT = "stratton-oakmont-hl-sniper"
APP_UNIT = "stratton-oakmont-hl-app"
SERVICES = [BOT_UNIT, APP_UNIT, "ssh", "fail2ban"]
APP_URL = "https://127.0.0.1:8443/"
HL_API = "https://api.hyperliquid.xyz/info"

REALARM_S = 15 * 60
STATE_MAX_AGE_S = 10 * 60        # the bot republishes every ~30s
LIVE_MAX_AGE_S = 5 * 60
RESTART_COOLDOWN_S = 3600        # at most one self-heal per hour
DISK_MAX_PCT = 90
RAM_MIN_MB = 200
RSS_MAX_MB = 800


def ntfy(msg: str, priority: int = 5) -> bool:
    topic = os.getenv("NTFY_TOPIC")
    if not topic:
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
            f"https://ntfy.sh/{topic}", data=msg.encode(),
            headers={"Title": "sniper-alarm", "Priority": str(priority),
                     "Tags": "warning,rotating_light"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:                      # noqa: BLE001
        print("ntfy push failed:", e)
        return False


def load_watch() -> dict:
    if WATCH.exists():
        try:
            return json.loads(WATCH.read_text())
        except Exception:                       # noqa: BLE001
            pass
    return {}


def save_watch(w: dict) -> None:
    WATCH.parent.mkdir(parents=True, exist_ok=True)
    tmp = WATCH.with_suffix(".tmp")
    tmp.write_text(json.dumps(w))
    tmp.replace(WATCH)


def alarm(key: str, msg: str, priority: int = 5) -> None:
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
    ntfy(f"{msg}\n(repeating #{count}; sniper watchdog)", priority)


def sysctl_active(unit: str) -> bool:
    try:
        r = subprocess.run(["systemctl", "is-active", unit],
                           capture_output=True, text=True, timeout=15)
        return r.stdout.strip() == "active"
    except Exception:                           # noqa: BLE001
        return False


def ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def bot_environ() -> str:
    try:
        pid = subprocess.run(
            ["systemctl", "show", "-p", "MainPID", "--value", BOT_UNIT],
            capture_output=True, text=True, timeout=15).stdout.strip()
        if not pid or pid == "0":
            return ""
        return Path(f"/proc/{pid}/environ").read_text(errors="ignore")
    except Exception:                           # noqa: BLE001
        return ""


def heal(unit: str, why: str, key: str) -> None:
    w = load_watch()
    last = float(w.get(f"heal:{key}", {}).get("last", 0))
    if time.time() - last < RESTART_COOLDOWN_S:
        return
    w[f"heal:{key}"] = {"last": time.time()}
    save_watch(w)
    subprocess.run(["systemctl", "restart", unit], capture_output=True,
                   timeout=60)
    print(f"SELF-HEAL restart {unit}: {why}")
    ntfy(f"{why} -- restarted {unit} (at most once an hour). "
         "It will not sleep.")


def checks() -> None:
    # 1 -- services
    for unit in SERVICES:
        if not sysctl_active(unit):
            alarm(f"svc:{unit}", f"{unit} is DOWN")

    # 2 -- book state finite & consistent
    try:
        st = json.loads(STATE.read_text())
        eq = st.get("equity")
        if eq is None or not math.isfinite(float(eq)):
            alarm("state:equity", f"equity is not finite: {eq!r}")
        for pos in st.get("positions", []):
            for lot in pos.get("lots", []):
                for f in ("entry", "sl", "qty"):
                    v = lot.get(f)
                    if v is not None and not math.isfinite(float(v)):
                        alarm("state:lots",
                              f"{pos.get('symbol')} lot {f} is not finite")
                        break
    except Exception as e:                      # noqa: BLE001
        alarm("state:read", f"cannot read paper state: {e}")

    # 3 -- PAPER LOCK intact (the alarm that protects real money)
    lock_file = (ROOT / "PAPER_ONLY").exists()
    env_txt = bot_environ()
    lock_env = "ICT_PAPER_ONLY=1" in env_txt
    if not lock_file or not lock_env:
        alarm("safety:paper_lock",
              "PAPER LOCK BROKEN: file=%s env=%s -- restoring the lock file"
              % (lock_file, lock_env), priority=5)
        if not lock_file:
            try:
                (ROOT / "PAPER_ONLY").write_text(
                    "paper lock restored by the watchdog\n")
            except OSError:
                pass
    for key in ("HL_SECRET", "HL_SECRET_KEY", "HL_PRIVATE_KEY"):
        if f"{key}=" in env_txt:
            alarm("safety:secret", f"{key} is present in the bot's "
                                   f"environment -- paper mode must hold no "
                                   f"signing key", priority=5)

    # 4 -- liveness: the bot keeps republishing the book
    try:
        age = time.time() - STATE.stat().st_mtime
        if age > STATE_MAX_AGE_S:
            alarm("bot:stuck", f"book state stale {int(age / 60)} min -- bot "
                               f"may be hung")
            heal(BOT_UNIT, f"the sniper went silent ({int(age / 60)} min)",
                 "bot")
    except Exception as e:                      # noqa: BLE001
        alarm("bot:state", f"no book state at all: {e}")

    # 5 -- journal errors
    r = subprocess.run(
        ["journalctl", "-u", BOT_UNIT, "--since", "-6 min", "--no-pager"],
        capture_output=True, text=True, timeout=20)
    errs = sum(1 for line in r.stdout.splitlines()
               if ("traceback" in line.lower() or " error " in line.lower()
                   or "exception" in line.lower()))
    if errs:
        alarm("log:errors", f"{errs} error line(s) in the bot journal")

    # 6 -- phone app reachable
    try:
        req = urllib.request.Request(APP_URL)
        with urllib.request.urlopen(req, timeout=10,
                                    context=ssl_ctx()) as resp:
            if resp.status != 200:
                alarm("app:http", f"app returned HTTP {resp.status}")
                heal(APP_UNIT, f"the app answered HTTP {resp.status}", "app")
    except Exception as e:                      # noqa: BLE001
        alarm("app:http", f"app unreachable: {e}")
        heal(APP_UNIT, "the phone app stopped answering", "app")

    # 7 -- disk / RAM / RSS
    try:
        r = subprocess.run(["df", "/"], capture_output=True, text=True,
                           timeout=10)
        pct = int(r.stdout.splitlines()[-1].split()[4].rstrip("%"))
        if pct >= DISK_MAX_PCT:
            alarm("disk", f"disk is {pct}% full")
    except Exception:                           # noqa: BLE001
        pass
    try:
        r = subprocess.run(["free", "-m"], capture_output=True, text=True,
                           timeout=10)
        avail = int(r.stdout.splitlines()[1].split()[6])
        if avail < RAM_MIN_MB:
            alarm("ram", f"only {avail} MB RAM available")
    except Exception:                           # noqa: BLE001
        pass
    try:
        pid = subprocess.run(
            ["systemctl", "show", "-p", "MainPID", "--value", BOT_UNIT],
            capture_output=True, text=True, timeout=15).stdout.strip()
        if pid and pid != "0":
            rss = int(Path(f"/proc/{pid}/status").read_text()
                      .split("VmRSS:")[1].split()[0]) / 1024.0
            if rss > RSS_MAX_MB:
                alarm("bot:rss", f"bot RSS {int(rss)} MB (leak?)")
    except Exception:                           # noqa: BLE001
        pass

    # 8 -- live prices fresh while a position is open
    try:
        st = json.loads(STATE.read_text())
        if st.get("positions") and LIVE.exists():
            age = time.time() - LIVE.stat().st_mtime
            if age > LIVE_MAX_AGE_S:
                alarm("live-prices",
                      f"live price file stale ({int(age / 60)} min) with an "
                      f"open position")
    except Exception:                           # noqa: BLE001
        pass

    # 9 -- venue reachable
    try:
        req = urllib.request.Request(
            HL_API, data=b'{"type":"allMids"}',
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status != 200:
                alarm("venue", f"Hyperliquid API HTTP {resp.status}")
    except Exception as e:                      # noqa: BLE001
        alarm("venue", f"Hyperliquid API unreachable: {e}")


def main() -> None:
    if "--test" in sys.argv:
        print("test alarm")
        ntfy("test alarm: the sniper watchdog is armed and working")
        return
    checks()


if __name__ == "__main__":
    main()
