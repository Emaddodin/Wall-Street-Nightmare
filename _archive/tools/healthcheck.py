#!/usr/bin/env python3
"""
One command that answers "is anything wrong right now".

Written as a script rather than a handful of remembered shell one-liners so
every check runs the same way every time, and so a new failure mode gets added
here once instead of being noticed by whoever happens to be looking.

Exit code is the answer: 0 nothing wrong, 1 something is. Every line it prints
is either OK or a fault -- there is no middle, because a status page nobody
can read at a glance is a status page nobody reads.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

BOT = Path(__file__).resolve().parent.parent
DATA = BOT / "data"

SERVICES = ("tbt-paper", "tbt-scout", "tbt-guard", "tbt-chrome", "tbt-panel")
# tbt-boom is retired, not broken: its ranking asked what a coin had done over
# weeks and put the market's biggest movers outside the list. tbt-atr replaced
# it. Naming the live one here matters -- a health check that shouts about a
# timer nobody runs any more is a health check people learn to ignore.
TIMERS = ("tbt-atr.timer", "tbt-report.timer")

faults: list[str] = []
notes: list[str] = []


def sh(*cmd, timeout=60) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "").strip()
    except Exception as e:
        return f"!{e}"


def fault(msg):
    faults.append(msg)
    print(f"  FAULT  {msg}")


def ok(msg):
    print(f"  ok     {msg}")


def age_min(p: Path) -> float:
    try:
        return (time.time() - p.stat().st_mtime) / 60
    except Exception:
        return 1e9


# --------------------------------------------------------------- services
print("services")
for s in SERVICES + TIMERS:
    st = sh("systemctl", "is-active", s)
    # A service caught mid-restart is not a service that is down.
    #
    # systemd reports "deactivating" and "activating" for the second or two a
    # restart takes, and this check happened to land inside one -- so an alarm
    # went to the operator's phone about a unit that was healthy before they
    # could read it. Anything genuinely stopped is still stopped a few seconds
    # later; anything in transition has finished by then.
    if st in ("deactivating", "activating", "reloading"):
        time.sleep(6)
        st = sh("systemctl", "is-active", s)
    (ok if st == "active" else fault)(f"{s} {st}")

# how often has systemd had to pick them back up
for s in ("tbt-paper", "tbt-scout"):
    n = sh("systemctl", "show", s, "-p", "NRestarts", "--value")
    try:
        if int(n or 0) > 3:
            fault(f"{s} has restarted {n} times")
    except ValueError:
        pass

# ----------------------------------------------------------------- health
print("\nthe book, since it last started")
started = sh("systemctl", "show", "tbt-paper", "-p", "ActiveEnterTimestamp",
             "--value")
log = sh("journalctl", "-u", "tbt-paper", "--since", started or "1 hour ago",
         "--no-pager", "-o", "cat", timeout=120) if started else ""
up_min = 0.0
if started:
    try:
        up_min = (time.time() - time.mktime(time.strptime(
            " ".join(started.split()[1:3]), "%Y-%m-%d %H:%M:%S"))) / 60
    except Exception:
        pass

# Recent, not historical.
#
# These were counted from the book's last restart, so one chart that froze at
# 09:30 and was reloaded by the guard at 09:34 went on being reported as a
# fault for as long as the process lived -- and it reached the operator's
# phone as an alarm about something already repaired. The guard fixes freezes;
# a freeze it has fixed is history, not a fault. Anything still going wrong is
# still going wrong in the last few minutes.
RECENT_MIN = 20
recent = sh("journalctl", "-u", "tbt-paper", "--since",
            f"{RECENT_MIN} min ago", "--no-pager", "-o", "cat", timeout=120)

for name, needle in (("polls failed", "poll failed"),
                     ("orders rejected", "REJECTED"),
                     ("order state unknown", "ORDER UNKNOWN"),
                     ("book unreadable", "will not parse")):
    now_n, ever = recent.count(needle), log.count(needle)
    if now_n:
        fault(f"{name}: {now_n} in the last {RECENT_MIN} min "
              f"({ever} since the restart)")
    elif ever:
        ok(f"{name}: 0 now ({ever} earlier, repaired)")
    else:
        ok(f"{name}: 0")

# Asked of the browser, not of a line the book printed once.
#
# The book prints "following N chart window(s)" exactly once, at startup. A
# book that came up while chrome was still starting printed 0 and attached to
# a window seconds later -- and this check went on calling that a fault for
# the whole life of the process. On 2026-09-05 it was still reporting an
# outage twenty minutes after the guard had repaired it, which is how a status
# page teaches people to ignore it.
try:
    sys.path.insert(0, str(BOT))
    from signals.tv_cdp import TradingViewCDP
    n_win = TradingViewCDP.chart_windows()
except Exception as e:
    n_win, why = None, str(e)[:60]

# A freeze is asked of the charts, not counted in the log.
#
# The guard repairs a stopped feed within about three minutes; the log line
# saying it happened stayed inside the health check's twenty-minute window for
# far longer, so every freeze sent an alarm to the operator's phone about
# something already fixed before they could read it. USELESSUSDT did this
# twice in one morning. What matters is whether a chart is frozen NOW.
STALE_BARS = 3
frozen_now = []
if n_win:
    for i in range(n_win):
        c = None
        try:
            c = TradingViewCDP(target_index=i)
            st = [x for x in c.studies() if "TBT" in x["name"]]
            if not st:
                continue
            r = c.raw_series(st[0]["id"], limit=3)
            rows = (r or {}).get("rows") or []
            if not rows:
                continue
            newest = max(int(x[0]) for x in rows)
            if newest > 1e12:
                newest //= 1000
            try:
                bar_s = int(str(r.get("res") or 15)) * 60
            except (TypeError, ValueError):
                bar_s = 900
            age = time.time() - newest
            if age > STALE_BARS * bar_s:
                sym = str(r.get("symbol", "?")).split(":")[-1]
                frozen_now.append(f"{sym} ({age / 60:.0f} min behind)")
        except Exception:
            pass
        finally:
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass
_ever_froze = log.count("FROZEN")
if frozen_now:
    fault("chart frozen right now: " + ", ".join(frozen_now))
elif _ever_froze:
    ok(f"chart froze: 0 now ({_ever_froze} earlier, the guard repaired it)")
else:
    ok("chart froze: 0")
# Two is the ceiling this account can stream, not a preference.
MAX_WINDOWS = 2
if n_win is None:
    fault(f"cannot ask the browser how many charts it has: {why}")
elif n_win == 0:
    fault("the book can see no chart windows")
elif n_win > MAX_WINDOWS:
    fault(f"{n_win} chart windows open -- this account streams "
          f"{MAX_WINDOWS}, so one of them is already dead or about to be")
else:
    ok(f"{n_win} chart window(s)")

# A book that is running but never evaluating anything is a book that has
# quietly stopped -- the scout may have died, or every window may be stuck.
# Every word the book uses to say it looked at something.
#
# This counted "reject" and "scored", which are what the shape source writes.
# On --source combo the book writes skip, drop and OPEN instead, so it
# reported "nothing evaluated in 46 minutes" while the log in front of it
# showed a position opened and a candidate refused by name. A check that
# knows only one source's vocabulary goes blind the moment the source changes.
looked = sum(log.count(w) for w in
             ("reject", "scored ", "skip  ", "drop  ", "OPEN  ", "LIVE OPEN"))
if up_min > 45 and looked == 0:
    fault(f"nothing evaluated in {up_min:.0f} minutes of uptime")
else:
    ok(f"{looked} candidates evaluated in {up_min:.0f} min")

# ------------------------------------------------------------------- guard
print("\nguard")
try:
    g = json.loads((DATA / "guard.json").read_text())
    p = g.get("problems") or []
    if age_min(DATA / "guard.json") > 10:
        fault(f"guard state is {age_min(DATA / 'guard.json'):.0f} min stale")
    elif p:
        for x in p:
            fault(f"guard: {x}")
    else:
        ok("all clear")
except Exception as e:
    fault(f"guard state unreadable: {str(e)[:60]}")

# -------------------------------------------------------------------- data
print("\ndata")
# atr_measures, not boom. boom.json was written by tbt-boom, which was
# retired on purpose -- its ranking asked what a coin had done over weeks and
# put the market's biggest movers outside the list. Left in this table it went
# stale by definition and alarmed every ten minutes about a file nothing was
# supposed to be writing.
for f, limit in ((DATA / "scout.json", 25),
                 (DATA / "watchlist.json", 30),
                 (DATA / "atr_measures.json", 30),
                 (DATA / "paper.json", 5)):
    a = age_min(f)
    (fault if a > limit else ok)(f"{f.name} {a:.0f} min old (limit {limit})")

# The learned filter: a silently aging model is a silent no-op -- the class
# of fault this engine cannot afford. The book refuses to start without the
# artifact, so its absence reads as a crash-looping book, and its age says
# whether the nightly retrain is still alive.
try:
    import filter_model
    for state, msg in filter_model.model_check(
            DATA / "dataset" / "samples" / "model.npz"):
        (fault if state == "fault" else ok)(msg)
except Exception as e:
    fault(f"model check failed: {str(e)[:60]}")

# ------------------------------------------------------------------ leaks
print("\nresources")
for s in ("tbt-paper", "tbt-scout", "tbt-guard", "tbt-chrome"):
    pid = sh("systemctl", "show", s, "-p", "MainPID", "--value")
    if not pid or pid == "0":
        continue
    try:
        fds = len(os.listdir(f"/proc/{pid}/fd"))
        rss = 0
        for line in open(f"/proc/{pid}/status"):
            if line.startswith("VmRSS"):
                rss = int(line.split()[1]) // 1024
        bad = fds > 400 or (rss > 2000 and s != "tbt-chrome")
        (fault if bad else ok)(f"{s}: {fds} fds, {rss} MB")
    except Exception:
        pass

# The guard owns memory at 250 MB, and it deliberately waits for a SECOND
# consecutive bad reading before restarting Chrome -- one dip is usually
# Chrome allocating and giving it straight back. Alarming here at the guard's
# own watch line meant the phone buzzed for a condition the guard had decided
# not to act on yet, and on 2026-09-06 it did exactly that twice while
# availability recovered on its own both times.
#
# So this alarms only where the guard's policy has actually failed: below the
# point where the OOM killer becomes a real risk. Between the two numbers it
# reports what it sees and says who owns it.
MEM_GUARD = 250     # the guard's threshold; it repairs, we do not alarm
MEM_DANGER = 150    # below this the guard has not saved it and someone must know

free = sh("bash", "-lc", "free -m | awk '/Mem:/{print $7}'")
try:
    mb = int(free)
    if mb < MEM_DANGER:
        fault(f"only {mb} MB of memory left")
    elif mb < MEM_GUARD:
        ok(f"{mb} MB memory free -- under {MEM_GUARD}, the guard is watching it")
    else:
        ok(f"{mb} MB memory free")
except ValueError:
    pass

disk = sh("bash", "-lc", "df --output=avail -BG / | tail -1 | tr -dc '0-9'")
try:
    if int(disk) < 3:
        fault(f"only {disk} GB of disk left")
    else:
        ok(f"{disk} GB disk free")
except ValueError:
    pass

# ------------------------------------------------------------------- book
print("\nthe account")
try:
    b = json.loads((DATA / "paper.json").read_text())
    eq = b.get("equity")
    if eq is None or eq != eq:
        fault(f"equity is {eq}")
    else:
        ok(f"equity ${eq:,.2f}")
    for t in b.get("trades", []):
        for k in ("entry", "tp", "sl", "qty", "notional", "margin", "pnl"):
            v = t.get(k)
            if v is None or v != v or abs(v) == float("inf"):
                fault(f"{t.get('sym')} has {k}={v}")
        if t.get("closed") and -(t.get("pnl") or 0) > (t.get("margin") or 0) * 1.05 + 1:
            fault(f"{t.get('sym')} lost more than its margin")
    resting = b.get("resting", [])
    stale = [x for x in resting if time.time() > x.get("expires", 0)]
    if stale:
        fault(f"{len(stale)} resting order(s) past their window and still held")
    else:
        ok(f"{len(resting)} resting, {len([x for x in b.get('trades', []) if not x.get('closed')])} open")
    # --live must never appear, whatever anyone edited
    unit = Path("/etc/systemd/system/tbt-paper.service")
    if unit.exists() and "--live" in unit.read_text():
        fault("THE UNIT HAS --live IN IT")
    else:
        ok("paper only, no --live")
except Exception as e:
    fault(f"book unreadable: {str(e)[:70]}")

print()
if faults:
    print(f"{len(faults)} FAULT(S)")
    for f in faults:
        print(f"  - {f}")
else:
    print("all clear")

# --notify: say it on the phone, and say it once.
#
# A check that only speaks when somebody runs it is not monitoring. This runs
# on a timer here, so it works whether or not a laptop is open or a session is
# alive -- which is the only kind of watching worth calling that.
if "--notify" in sys.argv:
    STAMP = DATA / "health_last.json"
    try:
        was = json.loads(STAMP.read_text())
    except Exception:
        was = {"faults": [], "at": 0}
    now = sorted(faults)
    changed = now != sorted(was.get("faults") or [])
    # Repeat an unfixed fault every half hour rather than every ten minutes,
    # and say so once when it clears.
    stale = time.time() - float(was.get("at") or 0) > 1800
    if faults and (changed or stale):
        title = f"TBT {len(faults)} FAULT" + ("S" if len(faults) > 1 else "")
        body = "\n".join(f"- {f}" for f in faults[:8])
    elif not faults and (was.get("faults") or []):
        title, body = "TBT recovered", "all clear"
    else:
        title = body = None
    if title:
        topics = []
        try:
            from dotenv import load_dotenv
            load_dotenv(BOT / ".env")
        except Exception:
            pass
        for k in ("NTFY_TOPIC", "NTFY_TOPIC_SHARED"):
            topics += [x.strip() for x in os.getenv(k, "").split(",")
                       if x.strip()]
        import urllib.parse
        import urllib.request
        for t in topics:
            try:
                req = urllib.request.Request(
                    "https://ntfy.sh/" + urllib.parse.quote(t),
                    data=body.encode("utf-8"),
                    headers={"Title": title, "Priority":
                             "high" if faults else "default"})
                urllib.request.urlopen(req, timeout=15).close()
            except Exception:
                pass
        print(f"-> notified: {title}")
    try:
        STAMP.write_text(json.dumps(
            {"faults": now, "at": time.time() if (faults and (changed or stale))
             else was.get("at", 0)}))
    except Exception:
        pass

sys.exit(1 if faults else 0)
