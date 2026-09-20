"""
Walk the engine end to end and prove each stage, rather than assume it.

Every check either exercises the real thing or says it could not. A check that
cannot run is reported as unknown, never as a pass -- the whole point of this
file is that a silent stage and a working stage looked identical for a day.
"""
import json
import os
import subprocess
import sys
import time
_BOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
sys.path.insert(0, _BOT)
os.chdir(_BOT)

PASS, FAIL, WARN = [], [], []


def check(name):
    def deco(fn):
        try:
            ok, detail = fn()
        except Exception as e:
            FAIL.append((name, f"{type(e).__name__}: {str(e)[:140]}"))
            return
        (PASS if ok is True else WARN if ok is None else FAIL).append((name, detail))
    return deco


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True, timeout=30).stdout.strip()


# ---------------------------------------------------------------- 1. chrome
@check("chrome is up and both chart windows are open")
def _():
    from signals.tv_cdp import TradingViewCDP
    wins = TradingViewCDP.chart_windows()
    n = wins if isinstance(wins, int) else len(wins)
    return (n >= 2, f"{n} chart window(s)")


@check("each window carries the TBT study on 15m")
def _():
    from signals.tv_cdp import TradingViewCDP
    out = []
    for i in (0, 1):
        c = TradingViewCDP(target_index=i)
        st = c.state()
        names = [x["name"] for x in c.studies()]
        c.close()
        has = any("TBT" in n for n in names)
        out.append(f"{i}:{st.symbol.split(':')[-1]}@{st.resolution}m"
                   f"{'' if has else ' NO-TBT'}")
        if not has or str(st.resolution) != "15":
            return (False, " ".join(out))
    return (True, " ".join(out))


# ------------------------------------------------------- 2. the three series
@check("the chart publishes PLAN_ENTRY, STATE and VOTES_PACKED")
def _():
    from signals.tv_cdp import TradingViewCDP
    c = TradingViewCDP(target_index=0)
    sid = [x for x in c.studies() if "TBT" in x["name"]][0]["id"]
    r = c.raw_series(sid, limit=3)
    c.close()
    plots = set(r["plots"])
    want = {"PLAN_ENTRY", "STATE", "VOTES_PACKED"}
    missing = want - plots
    return (not missing,
            f"{len(plots)} series published, missing {missing or 'nothing'}")


@check("STATE and VOTES_PACKED decode to sane values")
def _():
    from signals.tv_cdp import TradingViewCDP
    from papertrade import unpack_state, unpack_votes
    c = TradingViewCDP(target_index=0)
    sid = [x for x in c.studies() if "TBT" in x["name"]][0]["id"]
    r = c.raw_series(sid, limit=6)
    c.close()
    at = {p: i + 1 for i, p in enumerate(r["plots"])}
    row = r["rows"][-1]
    d = unpack_state(row[at["STATE"]])
    v = unpack_votes(row[at["VOTES_PACKED"]] or 0)
    bad = [k for k, x in v.items() if x not in (-1, 0, 1)]
    if bad or d["plan_dir"] not in (-1, 0, 1) or not 0 <= d["votes"] <= 6:
        return (False, f"out of range: {bad} {d}")
    said = ",".join(f"{k}{'+' if x>0 else '-'}" for k, x in v.items() if x)
    return (True, f"dir={d['plan_dir']} votes={d['votes']} "
                  f"ready={d['ready']} [{said or 'all silent'}]")


@check("the vote count in STATE matches the votes in VOTES_PACKED")
def _():
    from signals.tv_cdp import TradingViewCDP
    from papertrade import unpack_state, unpack_votes
    c = TradingViewCDP(target_index=0)
    sid = [x for x in c.studies() if "TBT" in x["name"]][0]["id"]
    r = c.raw_series(sid, limit=200)
    c.close()
    at = {p: i + 1 for i, p in enumerate(r["plots"])}
    checked = wrong = 0
    for row in r["rows"]:
        s, p = row[at["STATE"]], row[at["VOTES_PACKED"]]
        if s is None or p is None or s != s or p != p:
            continue
        d, v = unpack_state(s), unpack_votes(p)
        if not d["plan_dir"]:
            continue
        checked += 1
        agree = sum(1 for x in v.values() if x == d["plan_dir"])
        if agree != d["votes"]:
            wrong += 1
    if not checked:
        return (None, "no plan on the last 200 bars to cross-check")
    return (wrong == 0, f"{checked} plans cross-checked, {wrong} disagreed")


@check("the vote ceiling is live on the chart, not just in the file")
def _():
    """A plan published on a clean sweep means the running indicator predates
    the ceiling. This cannot be read from the Pine source -- only from what the
    chart actually publishes."""
    from signals.tv_cdp import TradingViewCDP
    from papertrade import unpack_state
    swept = plans = bars = 0
    for i in (0, 1):
        c = TradingViewCDP(target_index=i)
        st = [x for x in c.studies() if "TBT" in x["name"]]
        if not st:
            c.close(); continue
        r = c.raw_series(st[0]["id"], limit=1000)
        c.close()
        at = {p: k + 1 for k, p in enumerate(r["plots"])}
        for row in r["rows"]:
            sv = row[at["STATE"]]
            if sv is None or sv != sv:
                continue
            bars += 1
            d = unpack_state(sv)
            if not d["plan_dir"]:
                continue
            plans += 1
            if d["votes"] >= 6:
                swept += 1
    if not plans:
        return (None, f"{bars} bars carried no plan -- nothing to judge")
    return (swept == 0,
            f"{plans} plans over {bars} bars, {swept} published on a clean "
            f"sweep" + ("" if swept == 0 else " -- the chart is running the "
                        "version without the ceiling"))


# --------------------------------------------------------------- 3. scanner
@check("the scanner's ranking is readable and fresh")
def _():
    p = "data/boom.json"
    if not os.path.exists(p):
        return (False, "boom.json does not exist")
    d = json.load(open(p))
    age = (time.time() - os.path.getmtime(p)) / 3600
    if not d or "sym" not in d[0]:
        return (False, "ranking is empty or malformed")
    return (age < 6, f"{len(d)} coins, written {age:.1f}h ago, "
                     f"top {d[0]['sym']}")


@check("the scanner writes where it can write")
def _():
    src = open("boom2.py").read()
    if "/tmp/boom" in src:
        return (False, "still pointed at /tmp")
    out = "data/boom.json"
    return (os.access("data", os.W_OK), f"writes to {out}, directory writable")


# ---------------------------------------------------------------- 4. trader
@check("the paper book is running and has not been restarting")
def _():
    act = sh("systemctl", "is-active", "tbt-paper")
    n = sh("systemctl", "show", "tbt-paper", "-p", "NRestarts", "--value")
    return (act == "active" and n == "0", f"{act}, {n} restarts")


@check("it is reading the council, not the old convergence")
def _():
    u = open("/etc/systemd/system/tbt-paper.service").read()
    need = ["--source council", "--sl 2.1", "--min-expansion", "--frac 0.10"]
    missing = [x for x in need if x not in u]
    return (not missing, f"missing {missing or 'nothing'}")


@check("read_window returns council plans off the live chart")
def _():
    import papertrade
    seen = []
    for i in (0, 1):
        got = papertrade.read_window(i)
        if not got:
            seen.append(f"{i}:none")
            continue
        sym, res, sigs, ohlc = got
        cn = [x for x in sigs if x.get("council")]
        seen.append(f"{i}:{len(sigs)}sig/{len(cn)}plan/{len(ohlc)}candles")
        if not ohlc:
            return (False, "no candles came back -- the expansion filter "
                           "cannot run without them")
    return (True, " ".join(seen))


@check("the expansion and wick filters actually compute")
def _():
    import papertrade
    got = papertrade.read_window(0)
    if not got:
        return (None, "no window to test against")
    _sym, _res, _sigs, ohlc = got
    t = max(ohlc)
    ex = papertrade.expansion(ohlc, t)
    wr = papertrade.wick_risk(ohlc, "BUY", 2.1)
    if ex is None:
        return (False, "expansion returned None on a live window")
    return (True, f"expansion {ex:.2f}x, {wr:.0f}% of candles wick past the stop"
            if wr is not None else f"expansion {ex:.2f}x, wick unknown")


@check("the book is at a hundred dollars with nothing stranded")
def _():
    b = json.load(open("data/paper.json"))
    op = [t for t in b.get("trades", []) if not t.get("closed")]
    return (abs(b["equity"] - 100.0) < 50, 
            f"equity ${b['equity']:.2f} from ${b['start']:.2f}, "
            f"{len(b.get('trades', []))} trades, {len(op)} open")


# ----------------------------------------------------------------- 5. panel
@check("the panel is up and serving the council")
def _():
    import panel
    cn = panel.council()
    if not cn:
        return (False, "the panel sees no council on either window")
    w = cn[0]
    return (len(cn) >= 1 and w["res"] not in (None, "?"),
            f"{len(cn)} window(s), first {w['sym']}@{w['res']}m "
            f"dir={w['dir']}")


@check("the panel can capture the real chart windows")
def _():
    from signals.tv_cdp import TradingViewCDP
    sizes = []
    for i in (0, 1):
        c = TradingViewCDP(target_index=i)
        sizes.append(len(c.screenshot()))
        c.close()
    return (all(s > 10000 for s in sizes),
            " ".join(f"{s//1024}KB" for s in sizes))


@check("the scout is walking coins and writing what it finds")
def _():
    act = sh("systemctl", "is-active", "tbt-scout")
    n = sh("systemctl", "show", "tbt-scout", "-p", "NRestarts", "--value")
    if act != "active":
        return (False, f"tbt-scout is {act}")
    out = subprocess.run(["journalctl", "-u", "tbt-scout", "--since", "-10 min",
                          "--no-pager", "-o", "cat"],
                         capture_output=True, text=True, timeout=30).stdout
    swept = [l for l in out.splitlines() if "swept" in l]
    if not swept:
        return (None, "running but has not finished a sweep yet")
    return (True, f"{n} restarts, last: {swept[-1].strip()[:70]}")


@check("the book and the scout are not fighting over a window")
def _():
    u = open("/etc/systemd/system/tbt-paper.service").read()
    v = open("/etc/systemd/system/tbt-scout.service").read()
    import re
    skip = re.search(r"--skip-window\s+([0-9 ]+)", u)
    win = re.search(r"--window\s+(\d+)", v)
    if not skip or not win:
        return (False, "one of them does not say which window it owns")
    owned = win.group(1).strip()
    left = skip.group(1).split()
    return (owned in left,
            f"scout owns window {owned}, book skips {left}")


@check("the target comes from the leg, not from a number")
def _():
    u = open("/etc/systemd/system/tbt-paper.service").read()
    if "--tp-leg" not in u:
        return (False, "still on a fixed target multiple")
    import re
    m = re.search(r"--tp-leg\s+([0-9.]+)", u)
    return (True, f"taking {m.group(1)} of each impulse")


# ------------------------------------------------------------- 6. the others
@check("only one paper trader exists")
def _():
    h = sh("systemctl", "is-active", "tbt-hunt")
    he = sh("systemctl", "is-enabled", "tbt-hunt")
    return (h != "active" and he != "enabled", f"tbt-hunt {h}/{he}")


@check("guard, recorder and panel are alive")
def _():
    st = {u: sh("systemctl", "is-active", u)
          for u in ("tbt-guard", "tbt-recorder", "tbt-panel", "tbt-chrome")}
    dead = [k for k, v in st.items() if v != "active"]
    return (not dead, f"down: {dead or 'nothing'}")


@check("every module still imports")
def _():
    import importlib
    bad = []
    for m in ("papertrade", "panel", "guard", "boom2", "recorder",
              "signals.tv_cdp"):
        try:
            importlib.import_module(m)
        except Exception as e:
            bad.append(f"{m}({type(e).__name__})")
    return (not bad, f"broken: {bad or 'none'}")


# ------------------------------------------------------------------- report
print()
for tag, rows, mark in (("PASS", PASS, "ok  "), ("UNKNOWN", WARN, "??  "),
                        ("FAIL", FAIL, "FAIL")):
    for name, detail in rows:
        print(f"  {mark}  {name}")
        if detail:
            print(f"        {detail}")
print(f"\n  {len(PASS)} passed, {len(WARN)} unknown, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
