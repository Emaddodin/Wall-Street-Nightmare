#!/usr/bin/env python3
"""One command that answers "is the ICT Sniper stack healthy right now".

Same contract as the health check that ran on the old server: every line
printed is either ok or FAULT (there is no middle), the exit code is the
answer (0 nothing wrong, 1 something is), and --notify pushes an ntfy
message only when the fault set changes, at most every 30 minutes while it
persists, plus one "recovered" message when it clears.

    hl_healthcheck.py            # human/console report, exit code
    hl_healthcheck.py --notify   # same, and shout to ntfy on change
"""
from __future__ import annotations

import json
import os
import shutil
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv("SCALPER_DATA", str(ROOT / "data")))
STATE = DATA / "state" / "paper.json"
LIVE = DATA / "state" / "live.json"
STAMP = DATA / "state" / "hl_health.json"

BOT_UNIT = "stratton-oakmont-hl-sniper"
APP_UNIT = "stratton-oakmont-hl-app"
APP_URL = "https://127.0.0.1:8443/"
HL_API = "https://api.hyperliquid.xyz/info"

faults: list[str] = []
notes: list[str] = []


def sh(*cmd, timeout: int = 60) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout)
        return (r.stdout or "").strip()
    except Exception as e:                      # noqa: BLE001
        return f"!{e}"


def fault(msg: str) -> None:
    faults.append(msg)
    print(f"  FAULT  {msg}")


def ok(msg: str) -> None:
    print(f"  ok     {msg}")


def ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def env_value(key: str) -> str:
    v = os.getenv(key)
    if v:
        return v
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return ""


def main() -> None:
    notify = "--notify" in sys.argv
    print(f"ICT Sniper health -- {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")

    # 1 -- the two services
    for unit in (BOT_UNIT, APP_UNIT):
        if sh("systemctl", "is-active", unit) == "active":
            ok(f"{unit} active")
        else:
            fault(f"{unit} not active")
    for unit in (BOT_UNIT, APP_UNIT):
        if sh("systemctl", "is-enabled", unit) == "enabled":
            ok(f"{unit} enabled at boot")
        else:
            fault(f"{unit} not enabled at boot")

    # 2 -- the paper lock (real-money safety)
    if (ROOT / "PAPER_ONLY").exists():
        ok("PAPER_ONLY file present")
    else:
        fault("PAPER_ONLY file missing -- paper lock degraded")
    pid = sh("systemctl", "show", "-p", "MainPID", "--value", BOT_UNIT)
    envtxt = ""
    if pid and pid != "0":
        try:
            envtxt = Path(f"/proc/{pid}/environ").read_text(errors="ignore")
        except Exception:                       # noqa: BLE001
            envtxt = ""
    if "ICT_PAPER_ONLY=1" in envtxt:
        ok("bot runs with ICT_PAPER_ONLY=1")
    else:
        fault("bot environment has no ICT_PAPER_ONLY=1")
    if any(k in envtxt for k in ("HL_SECRET=", "HL_SECRET_KEY=",
                                 "HL_PRIVATE_KEY=")):
        fault("a signing key is present in the bot environment")
    else:
        ok("no signing key in the bot environment")
    unit_txt = Path(f"/etc/systemd/system/{BOT_UNIT}.service").read_text() \
        if Path(f"/etc/systemd/system/{BOT_UNIT}.service").exists() else ""
    if "--live" in unit_txt or "--secret-key" in unit_txt:
        fault("the service unit passes live-trading flags")
    else:
        ok("service unit passes no live-trading flags")

    # 3 -- the book
    try:
        st = json.loads(STATE.read_text())
        age = time.time() - STATE.stat().st_mtime
        equity = float(st.get("equity"))
        npos = len(st.get("positions", []))
        if age > 600:
            fault(f"book state stale ({int(age / 60)} min)")
        else:
            ok(f"book state fresh ({int(age)}s) equity {equity:.2f} "
               f"positions {npos}")
        if st.get("halted"):
            notes.append(f"halted: {st.get('halt_reason')}")
    except Exception as e:                      # noqa: BLE001
        fault(f"book state unreadable: {e}")

    # 4 -- phone app
    try:
        with urllib.request.urlopen(urllib.request.Request(APP_URL),
                                    timeout=10, context=ssl_ctx()) as resp:
            if resp.status == 200:
                ok("phone app HTTP 200 on :8443")
            else:
                fault(f"phone app HTTP {resp.status}")
    except Exception as e:                      # noqa: BLE001
        fault(f"phone app unreachable: {e}")

    # 5 -- venue
    try:
        req = urllib.request.Request(HL_API, data=b'{"type":"allMids"}',
                                     headers={"Content-Type":
                                              "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            mids = json.loads(resp.read())
        ok(f"Hyperliquid API reachable ({len(mids)} mids)")
    except Exception as e:                      # noqa: BLE001
        fault(f"Hyperliquid API unreachable: {e}")

    # 6 -- box
    du = shutil.disk_usage("/")
    pct = du.used / du.total * 100
    if pct >= 90:
        fault(f"disk {pct:.0f}% full")
    else:
        ok(f"disk {pct:.0f}% used, {du.free / 1e9:.0f} GB free")
    try:
        mem = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            mem[k] = int(v.split()[0])          # kB
        avail_mb = mem["MemAvailable"] / 1024
        if avail_mb < 200:
            fault(f"only {avail_mb:.0f} MB RAM available")
        else:
            ok(f"RAM {avail_mb:.0f} MB available")
    except Exception as e:                      # noqa: BLE001
        fault(f"memory check failed: {e}")

    # 6b -- security guard
    if sh("systemctl", "is-active", "fail2ban") == "active":
        bans = sh("fail2ban-client", "status", "sshd")
        n = 0
        for line in bans.splitlines():
            if "Currently banned" in line:
                n = int(line.split(":")[-1].strip() or 0)
        ok(f"fail2ban active (sshd currently banned: {n})")
    else:
        fault("fail2ban not active")

    # 7 -- watchdog + health timers armed
    for t in ("stratton-oakmont-hl-watchdog.timer", "stratton-oakmont-hl-health.timer"):
        if sh("systemctl", "is-active", t) == "active":
            ok(f"{t} armed")
        else:
            fault(f"{t} not armed")

    # 8 -- ntfy topic configured (the alarm path itself)
    if env_value("NTFY_TOPIC"):
        ok("ntfy topic configured")
    else:
        fault("no NTFY_TOPIC -- alarms cannot be delivered")

    # 9 -- recent bot errors
    jr = sh("journalctl", "-u", BOT_UNIT, "--since", "-1 hour", "--no-pager")
    errs = sum(1 for line in jr.splitlines()
               if "traceback" in line.lower() or " error " in line.lower())
    if errs:
        fault(f"{errs} error line(s) in the bot journal (last hour)")
    else:
        ok("no error lines in the bot journal (last hour)")

    for n in notes:
        print(f"  note   {n}")
    print(f"-> {len(faults)} fault(s)")

    # ---- ntfy on change / every 30 min while it persists -----------------
    try:
        was = json.loads(STAMP.read_text())
    except Exception:                           # noqa: BLE001
        was = {"faults": [], "at": 0}
    now = sorted(faults)
    changed = now != sorted(was.get("faults") or [])
    stale = time.time() - float(was.get("at") or 0) > 1800
    title = body = None
    if faults and (changed or stale):
        title = f"SNIPER {len(faults)} FAULT" + ("S" if len(faults) > 1 else "")
        body = "\n".join(f"- {f}" for f in faults[:8])
    elif not faults and (was.get("faults") or []):
        title, body = "SNIPER recovered", "all clear"
    if title and notify:
        topics = [t.strip() for k in ("NTFY_TOPIC", "NTFY_TOPIC_SHARED")
                  for t in env_value(k).split(",") if t.strip()]
        for t in topics:
            try:
                req = urllib.request.Request(
                    "https://ntfy.sh/" + urllib.parse.quote(t),
                    data=(body or "").encode("utf-8"),
                    headers={"Title": title,
                             "Priority": "high" if faults else "default"})
                urllib.request.urlopen(req, timeout=15).close()
            except Exception:                   # noqa: BLE001
                pass
        print(f"-> notified: {title}")
    try:
        STAMP.parent.mkdir(parents=True, exist_ok=True)
        STAMP.write_text(json.dumps(
            {"faults": now,
             "at": time.time() if (faults and (changed or stale))
             else was.get("at", 0)}))
    except Exception:                           # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
    sys.exit(1 if faults else 0)
