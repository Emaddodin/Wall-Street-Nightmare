#!/usr/bin/env python3
"""
Watchdog. Keeps the stack alive and the box locked down, without you.

systemd already restarts a process that exits. It cannot see the failures that
matter here, because none of them look like a crash:

  * Chrome alive but its window unmapped -- the exact bug that cost an evening.
    CDP answers, the process is healthy, and nothing can be clicked or read.
  * A chart drifting off its symbol or timeframe after a reload.
  * The TradingView session expiring, so the study silently stops computing.
  * The paper book running but its last signal hours old while the charts move.
  * Chrome creeping up in memory until the OOM killer takes something else.

Each check has a specific repair, and a repair is only tried when its own
check fails -- restarting the world on every hiccup is how you turn a small
problem into a data-losing one. Anything it cannot fix, it reports once and
stops repeating.

    python guard.py --interval 120
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

log = logging.getLogger("guard")
STATE = ROOT / "data" / "guard.json"
# Trading is stopped on purpose -- the guard must not restart it.
SERVICES = ("tbt-xvfb", "tbt-wm", "tbt-chrome")


def sh(*cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception as e:
        return 1, "", str(e)[:120]


def ping(title, body, test=False):
    if test:
        title = "[TEST] " + title
    topics = []
    for k in ("NTFY_TOPIC", "NTFY_TOPIC_SHARED"):
        topics += [x.strip() for x in os.getenv(k, "").split(",") if x.strip()]
    for t in topics:
        try:
            req = urllib.request.Request(
                f"https://ntfy.sh/{urllib.parse.quote(t)}", data=body.encode(),
                headers={"Title": title.encode("utf-8").decode("latin-1", "replace"),
                         "Priority": "high"})
            urllib.request.urlopen(req, timeout=10).close()
        except Exception:
            pass


# ----------------------------------------------------------------- checks
def check_services():
    bad = [s for s in SERVICES
           if sh("systemctl", "is-active", s)[1] != "active"]
    return (not bad), f"down: {', '.join(bad)}" if bad else "all active"


def repair_services():
    for s in SERVICES:
        if sh("systemctl", "is-active", s)[1] != "active":
            sh("systemctl", "restart", s)
            time.sleep(6)


def check_window():
    """
    Chrome with no mapped window is invisible to VNC and to every click.

    Matching on the title was wrong -- the window is named "google-chrome",
    not "Google Chrome", and Chrome also keeps two 10x10 helper windows alive
    at all times. So the test is geometric: is there any window big enough to
    be a browser. That cannot be fooled by a title change or a helper.
    """
    rc, out, _ = sh("bash", "-lc",
                    "DISPLAY=:99 xwininfo -root -children 2>/dev/null | "
                    "grep -oE '[0-9]+x[0-9]+\\+' | tr -d '+'")
    big = 0
    for tok in (out or "").split():
        try:
            w, h = tok.split("x")
            if int(w) >= 400 and int(h) >= 300:
                big += 1
        except Exception:
            pass
    return big > 0, f"{big} real window(s)"


def repair_window():
    """
    Order matters. Chrome maps its window through the window manager, and a
    restart that races fluxbox comes up windowless -- which is the failure we
    are repairing, so doing it in the wrong order repairs nothing.
    """
    sh("systemctl", "restart", "tbt-wm")
    time.sleep(4)
    sh("systemctl", "restart", "tbt-chrome")
    time.sleep(30)


# TradingView, on the plan this account has, streams two charts and no more.
#
# A third window opens, loads, shows a price -- and then its feed simply
# stops. Nothing errors. The book goes on reading a chart whose newest candle
# is an hour old, which is exactly what "FROZEN USELESSUSDT" and "FROZEN
# NOMUSDT" were all morning: not a bug in the browser, a limit on the account.
# So two is the ceiling, and anything above it is not extra eyesight, it is
# one dead chart plus the two that work.
MAX_WINDOWS = 2

# Rounds of failed chart rebuilds before the guard tries again. At 120s a
# round this is about half an hour: three attempts, then quiet, then three
# more. See the comment at the give-up branch in the main loop.
CHART_RETRY_AFTER = 18

WANTED = ROOT / "data" / "chart_coins.json"


BEST = ROOT / "data" / "watchlist.json"


def best_coins(n=2):
    """The top of the ranked list, for the window the book watches directly.

    Only one window stands still -- the other is being walked by the scout --
    so the coin parked on it is the single coin the book sees continuously.
    Leaving it on whatever happened to be there last means that continuous
    attention is spent on an ordinary coin while the best in the list goes
    unwatched except in passing.
    """
    try:
        got = [str(x).upper() for x in json.loads(BEST.read_text())]
        return got[:n] if len(got) >= n else None
    except Exception:
        return None


def wanted(fallback):
    """The pair the charts should hold.

    The panel writes this whenever it switches coins, by hand or on its own.
    Reading it here is what stops the guard from dragging the windows back to
    whatever was on its command line at boot -- which it did, every two
    minutes, against every automatic switch.
    """
    try:
        got = json.loads(WANTED.read_text())
        got = [str(s).upper().replace(".P", "") for s in got if s]
        if len(got) >= 1:
            return got
    except Exception:
        pass
    top = best_coins()
    if top:
        return top
    return fallback


# How far behind the clock a chart's newest bar may be, in bars of its own
# timeframe. A healthy chart is always inside one -- the newest bar is the one
# forming right now. Three is generous enough that a slow reload or a quiet
# minute never cries wolf, and still catches a stopped feed within the hour.
STALE_BARS = 3


def newest_bar_age(rows, res) -> float | None:
    """Seconds since the newest bar on this chart began, or None.

    This is the check that was missing, and it is the one that mattered. A
    TradingView tab whose feed has stopped still answers every question put to
    it: the study is loaded, the symbol is right, the timeframe is right, the
    bar count is right. Every candle it hands back is simply frozen at the
    moment the feed died. The book read one of those for fourteen hours and
    the only trace was a `stale bar -- not traded` line per poll; nothing
    checked, nothing repaired, nothing said.
    """
    if not rows:
        return None
    try:
        newest = max(int(r[0]) for r in rows if r and r[0] is not None)
    except (TypeError, ValueError):
        return None
    # TradingView publishes bar times in seconds.
    return time.time() - newest


def check_charts(want, a_res="15", scout_window=-1):
    sys.path.insert(0, str(ROOT))
    from signals.tv_cdp import TradingViewCDP
    n = TradingViewCDP.chart_windows()
    if n < len(want):
        return False, f"only {n} chart window(s)", None
    # The scout's window is one nobody was counting.
    #
    # `want` is a wish: the coins the book would like to watch directly, and
    # the code below happily trims it to however many windows are free. The
    # scout's window is not a wish. It walks the whole ranked list -- almost
    # every candidate the book ever sees arrives through it -- and it appeared
    # in no list here; it simply happened to exist. On 2026-09-05 a browser
    # restart came back with one window, the wanted list held one coin,
    # `1 < 1` was false, and this check said all clear while the scout swept
    # zero coins for fifteen minutes. The engine was reading one coin instead
    # of three hundred and forty-eight and nothing anywhere disagreed.
    bad = check_candles(n)
    if bad:
        return False, (f"window(s) {', '.join(str(i) for i in bad)} are on "
                       f"Heikin Ashi -- every reading the indicator takes is "
                       f"from a price that was never traded"), None
    if n > MAX_WINDOWS:
        return False, (f"{n} chart windows open, and this account streams "
                       f"{MAX_WINDOWS} -- the extra one's feed stops and the "
                       f"book reads a chart that has quietly died"), None
    if scout_window >= 0 and n <= scout_window:
        return False, (f"only {n} chart window(s); the scout walks window "
                       f"{scout_window} and it does not exist"), None
    # Keyed by symbol, two windows showing the same coin land in one entry and
    # the window index is lost -- which is exactly what happened after a
    # reload: both tabs came back on the same coin, the book went on reading
    # two windows and seeing one coin's signals twice, and nothing anywhere
    # said so. Per-window is kept alongside so a duplicate is visible.
    seen, per_window, details, stale, walking = {}, {}, [], [], set()
    for i in range(n):
        try:
            c = TradingViewCDP(target_index=i)
            st = [s for s in c.studies() if "TBT" in s["name"]]
            if not st:
                c.close()
                details.append(f"window {i}: no TBT study")
                continue
            r = c.raw_series(st[0]["id"])
            at = {p: k + 1 for k, p in enumerate(r["plots"])}
            wi = at.get("TSL_WIRED")
            wired = any(row[wi] for row in r["rows"]
                        if wi is not None and wi < len(row) and row[wi])
            sym = str(r["symbol"]).split(":")[-1].replace(".P", "").upper()
            seen[sym] = (str(r["res"]), wired, len(r["rows"]))
            per_window[i] = sym
            if i == scout_window:
                # Alive and fresh matters here; which coin does not.
                walking.add(sym)
            age = newest_bar_age(r["rows"], r["res"])
            try:
                bar_s = int(str(r["res"])) * 60
            except (TypeError, ValueError):
                bar_s = 900
            if age is not None and age > STALE_BARS * bar_s:
                details.append(
                    f"window {i} ({sym}) is frozen: newest candle is "
                    f"{age / 60:.0f} minutes old on a {bar_s // 60}m chart")
                stale.append(i)
            c.close()
        except Exception as e:
            details.append(f"window {i}: {str(e)[:50]}")
    # Two windows on one coin halves the engine's coverage without producing a
    # single error anywhere, so it is named here rather than inferred later.
    vals = [sym for i, sym in per_window.items() if i != scout_window]
    if len(vals) > len(set(vals)):
        for c in {x for x in vals if vals.count(x) > 1}:
            which = " and ".join(str(i) for i, x in per_window.items() if x == c)
            details.append(f"windows {which} are both on {c}")
    # A wanted list that repeats a coin is satisfied by one window showing it
    # twice, so the duplicate has to be caught on this side as well.
    if len({w.upper() for w in want}) < len(want):
        details.append("the wanted coins are not distinct")
    held = len(per_window) - (1 if scout_window in per_window else 0)
    if held and len(want) > held:
        want = want[:held]
    for w in want:
        k = w.upper()
        if k in walking and k not in vals:
            continue          # the scout happens to be on it this second
        if k not in seen:
            details.append(f"{w} missing from the charts")
        else:
            res, wired, bars = seen[k]
            if res != a_res:
                details.append(f"{w} on {res}m not {a_res}m")
            if not wired:
                details.append(f"{w} tesla hook not wired")
            if bars < 50:
                details.append(f"{w} only {bars} bars")
    return (not details), ("; ".join(details) if details else
                           f"{len(seen)} charts healthy"), {"seen": seen,
                                                            "stale": stale}


CHART_URL = ROOT / "data" / "chart_url.txt"


def chart_url() -> str | None:
    """The saved chart layout to open.

    Learned from a live tab whenever one exists and written down, so that a
    Chrome that comes back with nothing at all can still be rebuilt.
    """
    try:
        raw = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=10)
        for t in json.load(raw):
            u = (t.get("url") or "").split("?")[0]
            if t.get("type") == "page" and "tradingview.com/chart" in u:
                try:
                    CHART_URL.write_text(u)
                except Exception:
                    pass
                return u
    except Exception:
        pass
    try:
        return CHART_URL.read_text().strip() or None
    except Exception:
        return None


def reload_stale(windows) -> None:
    """Reload a tab whose feed has stopped.

    A frozen tab cannot be repaired by setting its symbol -- it is already on
    the right one, which is exactly why nothing noticed. The page has to be
    reloaded so it opens a new feed. This is what somebody did by hand at
    04:00; there is no reason it should have needed a person.
    """
    if not windows:
        return
    sys.path.insert(0, str(ROOT))
    from signals.tv_cdp import TradingViewCDP
    for i in windows:
        try:
            c = TradingViewCDP(target_index=i)
            try:
                c.evaluate("location.reload()")
                log.warning("window %d had a stopped feed -- reloaded", i)
            finally:
                c.close()
        except Exception as e:
            log.error("could not reload window %d: %s", i, str(e)[:70])
    time.sleep(30)          # let the chart come back before anything reads it


# TradingView's own numbering: 1 is a real candle, 8 is Heikin Ashi.
REAL_CANDLES = 1

# How wide a candle is drawn. A chart left at TradingView's own spacing shows
# a hundred and fifty bars at once, which on a fifteen minute chart is thirty
# hours squeezed into a line -- unreadable to anyone looking at it, and the
# operator has to be able to look at it.
BAR_SPACING = 9

SET_SPACING_JS = """(function(){try{
  window.TradingViewApi.activeChart().setBarSpacing(%d); return "ok";
}catch(e){return String(e);}})()""" % BAR_SPACING

# The price axis must fit the candles and nothing else.
#
# The indicator publishes its readings as plots -- VOTES_PACKED and STATE are
# packed integers in the hundreds and thousands -- and with scaleSeriesOnly
# off the axis stretched to hold them. On a coin trading at 0.139 the scale
# ran to 1,345 and every candle collapsed into a flat line at the bottom. It
# was not a zoom problem and no amount of zooming would have fixed it.
SET_SERIES_SCALE_JS = """(function(){try{
  var w = window.TradingViewApi.activeChart()._chartWidget;
  w.properties().childs().scalesProperties.childs()
   .scaleSeriesOnly.setValue(true);
  return "ok";
}catch(e){return String(e);}})()"""


CHART_STYLE_JS = """(function(){try{
  var m = window.TradingViewApi.activeChart()._chartWidget.model();
  return String(m.mainSeries().properties().style.value());
}catch(e){return "?";}})()"""

SET_REAL_JS = """(function(){try{
  window.TradingViewApi.activeChart().setChartType(%d); return "ok";
}catch(e){return String(e);}})()""" % REAL_CANDLES


def check_candles(n: int) -> list:
    """Every window has to be showing real candles, not Heikin Ashi.

    A Heikin Ashi candle is an average of the one before it. It is a fine
    thing to look at and the wrong thing to compute on: the indicator's every
    reading -- the breaking candle, the body, the level -- is then taken from
    a price that was never traded. On 2026-09-05 both windows were on style 8
    and nobody had noticed, because nothing had ever asked.

    A saved layout restores its own style, so a reload puts it back. This is
    checked every round rather than set once.
    """
    sys.path.insert(0, str(ROOT))
    from signals.tv_cdp import TradingViewCDP
    wrong = []
    for i in range(n):
        c = None
        try:
            c = TradingViewCDP(target_index=i)
            got = str(c.evaluate(CHART_STYLE_JS)).strip()
            # Only a style we can actually read and that is not a real candle
            # is a fault. A window that cannot answer -- an old build, a page
            # still loading -- is not evidence of anything, and inventing a
            # fault from silence is how an alarm stops being believed.
            if got.isdigit() and int(got) != REAL_CANDLES:
                wrong.append(i)
        except Exception:
            pass
        finally:
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass
    return wrong


def fix_candles(which: list) -> None:
    sys.path.insert(0, str(ROOT))
    from signals.tv_cdp import TradingViewCDP
    for i in which:
        c = None
        try:
            c = TradingViewCDP(target_index=i)
            c.evaluate(SET_REAL_JS)
            c.evaluate(SET_SPACING_JS)
            c.evaluate(SET_SERIES_SCALE_JS)
            log.info("window %d put back on real candles", i)
        except Exception as e:
            log.error("could not set real candles on window %d: %s",
                      i, str(e)[:60])
        finally:
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass
    time.sleep(20)          # let the study recompute before anything reads it


def repair_charts(want, scout_window=-1) -> None:
    """Open the chart windows that are missing and put the wanted coins on them.

    Chrome restores the session it saved, and that session holds one window; a
    second opened later is not part of it. So every Chrome restart came back a
    window short and the old code could only report that, once every two
    minutes, until somebody noticed. This morning that ran for 53 minutes and
    the book watched one coin instead of two the whole time.

    Opening a tab and setting its symbol is exactly what a person would do, so
    the guard may as well do it.
    """
    sys.path.insert(0, str(ROOT))
    from signals.tv_cdp import TradingViewCDP

    url = chart_url()
    if not url:
        log.error("no chart URL known, cannot rebuild")
        return
    # Candles first: a window on Heikin Ashi is worse than a missing one,
    # because it answers every question with a price nobody paid.
    fix_candles(check_candles(TradingViewCDP.chart_windows()))
    have = TradingViewCDP.chart_windows()
    # Enough windows for the wanted coins, and enough for the scout to have
    # the one it walks -- the check above refuses either shortfall. Never more
    # than the account can actually stream.
    need = min(MAX_WINDOWS,
               max(len(want), scout_window + 1 if scout_window >= 0 else 0))
    for _ in range(max(0, need - have)):
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:9222/json/new?{url}", method="PUT")
            urllib.request.urlopen(req, timeout=25)
            log.info("opened a chart window")
        except Exception as e:
            log.error("could not open a chart window: %s", str(e)[:70])
            return
        time.sleep(25)

    # Which coin sits where, so only the windows that need moving are touched.
    n = TradingViewCDP.chart_windows()
    on = {}
    for i in range(n):
        try:
            c = TradingViewCDP(target_index=i)
            try:
                cur = str(c.evaluate(
                    "window.TradingViewApi.activeChart().symbol()"))
                on[i] = cur.split(":")[-1].replace(".P", "").upper()
            finally:
                c.close()
        except Exception:
            on[i] = ""
    missing = [w.upper() for w in want if w.upper() not in on.values()]
    # Never the scout's window. Setting a symbol there is undone within
    # seconds and the two services spend the night pulling the same chart in
    # opposite directions.
    spare = [i for i, sym in on.items()
             if i != scout_window
             and (sym not in [w.upper() for w in want]
                  or list(on.values()).count(sym) > 1)]
    if not spare and missing:
        log.info("nothing to move %s onto -- every standing window is already "
                 "on a wanted coin", ", ".join(missing))
    for sym in missing:
        if not spare:
            break
        i = spare.pop(0)
        try:
            c = TradingViewCDP(target_index=i)
            try:
                c.evaluate("window.TradingViewApi.activeChart()"
                           f'.setSymbol("BITUNIX:{sym}.P")')
                log.info("window %d -> %s", i, sym)
            finally:
                c.close()
        except Exception as e:
            log.error("could not set %s on window %d: %s", sym, i, str(e)[:60])
    time.sleep(30)      # let the study recompute before anything reads it


def check_memory():
    mem = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":")[0], line.split()[1]
        mem[k] = int(v) / 1024
    avail = mem.get("MemAvailable", 0)
    return avail > 250, f"{avail:.0f} MB available"


def repair_memory():
    """Chrome is the only thing here that grows. Restart it, not the book."""
    sh("systemctl", "restart", "tbt-chrome")
    time.sleep(25)


def check_security():
    notes = []
    rc, out, _ = sh("ufw", "status")
    if "Status: active" not in out:
        notes.append("firewall is off")
    for port, label in (("9222", "chrome debug"), ("5900", "vnc")):
        rc, o, _ = sh("bash", "-lc",
                      f"ss -tlnH 'sport = :{port}' 2>/dev/null | awk '{{print $4}}'")
        for addr in (o or "").split():
            if not (addr.startswith("127.") or addr.startswith("[::1]")):
                notes.append(f"{label} port {port} listening on {addr}")
    rc, o, _ = sh("bash", "-lc",
                  "grep -Ei '^ *PermitRootLogin' /etc/ssh/sshd_config "
                  "/etc/ssh/sshd_config.d/* 2>/dev/null | tail -1")
    if o and "yes" in o.lower():
        notes.append("ssh allows root password login")
    return (not notes), ("; ".join(notes) if notes else "locked down")


def check_disk():
    import shutil
    free = shutil.disk_usage("/").free / 1e9
    return free > 2.0, f"{free:.1f} GB free"


def repair_disk():
    sh("bash", "-lc", "journalctl --vacuum-size=200M >/dev/null 2>&1")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=120)
    ap.add_argument("--charts", nargs="+", default=["ZKCUSDT", "BTRUSDT"])
    # The window the scout walks. It changes coin every few seconds by
    # design, so requiring a fixed symbol on it is a fault that can never be
    # repaired -- and trying to repair it means two services setting the same
    # chart to different coins, seconds apart, forever. The guard reported
    # "TRIAUSDT missing from the charts" every two minutes for exactly this
    # reason, which is how a watchdog teaches you to ignore it.
    ap.add_argument("--scout-window", type=int, default=1, metavar="N",
                    help="the chart window the scout owns. It is checked for "
                         "life and freshness like any other, but never for "
                         "which coin it is on, and never repaired onto one. "
                         "-1 if no scout is running.")
    ap.add_argument("--resolution", default="15",
                    help="the timeframe the charts are supposed to be on. It "
                         "was hard-coded to 5, so moving to 15m had the guard "
                         "reporting a fault every two minutes for something "
                         "that was deliberate.")
    ap.add_argument("--quiet-repeats", type=int, default=6,
                    help="do not re-notify the same problem for this many rounds")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    told: dict[str, int] = {}
    strikes: dict[str, int] = {}
    round_no = 0
    log.info("guard up -- watching %s every %ds%s", ", ".join(a.charts),
             a.interval,
             f"; window {a.scout_window} is the scout's and is not held to a "
             f"coin" if a.scout_window >= 0 else "")

    while True:
        round_no += 1
        problems = []

        for name, chk, fix in (
            ("services", check_services, repair_services),
            ("chrome window", check_window, repair_window),
            ("memory", check_memory, repair_memory),
            ("disk", check_disk, repair_disk),
        ):
            try:
                ok, detail = chk()
            except Exception as e:
                ok, detail = False, f"check failed: {str(e)[:60]}"
            if ok:
                told.pop(name, None)
                strikes.pop(name, None)
                continue
            # One bad reading is usually a service still coming up. Repairs
            # here restart things, so they wait for a second opinion.
            strikes[name] = strikes.get(name, 0) + 1
            if strikes[name] < 2:
                log.info("%s: %s (first strike, waiting)", name, detail)
                continue
            log.warning("%s: %s -- repairing", name, detail)
            try:
                fix()
            except Exception as e:
                log.error("repair of %s failed: %s", name, str(e)[:80])
            try:
                ok2, detail2 = chk()
            except Exception:
                ok2, detail2 = False, "recheck failed"
            if ok2:
                log.info("%s: fixed (%s)", name, detail2)
                told.pop(name, None)
                strikes.pop(name, None)
            else:
                problems.append(f"{name}: {detail2}")

        try:
            ok, detail, info = check_charts(wanted(a.charts), a.resolution,
                                            a.scout_window)
        except Exception as e:
            ok, detail, info = False, f"chart check failed: {str(e)[:60]}", {}
        if ok:
            told.pop("charts", None)
            strikes.pop("charts", None)
            # Write the layout down while everything is healthy. Learning it
            # only at repair time is too late: the one case that needs it most
            # is a Chrome with no chart tab left to learn from.
            chart_url()
        else:
            # A missing window, or one parked on the wrong coin, is mechanical:
            # open it and set the symbol. An expired TradingView session still
            # needs a person, so if the rebuild does not take, say so and stop
            # trying rather than reopening tabs every two minutes.
            strikes["charts"] = strikes.get("charts", 0) + 1
            if strikes["charts"] == 1:
                log.info("charts: %s (first strike, waiting)", detail)
            elif strikes["charts"] <= 3:
                log.warning("charts: %s -- rebuilding", detail)
                try:
                    reload_stale((info or {}).get("stale") or [])
                    repair_charts(wanted(a.charts), a.scout_window)
                except Exception as e:
                    log.error("chart rebuild failed: %s", str(e)[:80])
                try:
                    ok2, detail2, _ = check_charts(wanted(a.charts),
                                                   a.resolution,
                                                   a.scout_window)
                except Exception:
                    ok2, detail2 = False, "recheck failed"
                if ok2:
                    log.info("charts: fixed (%s)", detail2)
                    told.pop("charts", None)
                    strikes.pop("charts", None)
                else:
                    problems.append(f"charts: {detail2}")
            else:
                problems.append(f"charts: {detail} (rebuild did not take)")
                # ...but never give up forever. This counter only ever grew,
                # so after three failed rebuilds the guard went permanently
                # passive: it reported the fault every two minutes and never
                # tried again, even once the cause was gone. On 2026-09-06
                # perch was asking for two coins when only one window can hold
                # one; fixing perch was not enough, because the guard had
                # already stopped trying and stayed stopped until it was
                # restarted by hand.
                #
                # Reset the count after a cooling-off period so the rebuild
                # path runs again. That keeps the original intent -- do not
                # reopen tabs every two minutes at a session that needs a
                # person -- while making the giving-up temporary.
                if strikes["charts"] >= CHART_RETRY_AFTER:
                    log.info("charts: cooling-off over, will try rebuilding "
                             "again on the next round")
                    strikes["charts"] = 1

        try:
            ok, detail = check_security()
        except Exception as e:
            ok, detail = False, str(e)[:60]
        if not ok:
            problems.append(f"security: {detail}")
        else:
            told.pop("security", None)

        for p in problems:
            key = p.split(":")[0]
            last = told.get(key, -999)
            if round_no - last >= a.quiet_repeats:
                told[key] = round_no
                ping("TBT guard", p)
            log.warning("unresolved -- %s", p)
        if not problems:
            log.info("all clear")
        STATE.write_text(json.dumps({
            "at": datetime.now(timezone.utc).isoformat(),
            "round": round_no, "problems": problems}))
        time.sleep(a.interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped")
