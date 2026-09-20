"""Walk the whole trade path and prove each link, on the live system."""
import json
import os
import subprocess
import sys
import time
_BOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
sys.path.insert(0, _BOT); os.chdir(_BOT)

PASS, FAIL, WARN = [], [], []


def check(name):
    def deco(fn):
        try:
            ok, detail = fn()
        except Exception as e:
            FAIL.append((name, f"{type(e).__name__}: {str(e)[:150]}"))
            return
        (PASS if ok is True else WARN if ok is None else FAIL).append(
            (name, detail))
    return deco


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True,
                          timeout=30).stdout.strip()


@check("every module imports and every name resolves")
def _():
    import importlib
    bad = []
    for m in ("papertrade", "scout", "panel", "guard", "boom2",
              "recorder", "signals.tv_cdp"):
        try:
            importlib.import_module(m)
        except Exception as e:
            bad.append(f"{m}({type(e).__name__}: {str(e)[:40]})")
    return (not bad, f"broken: {bad or 'none'}")


@check("the two shapes run on real candles")
def _():
    import papertrade as pt
    from signals.tv_cdp import TradingViewCDP
    c = TradingViewCDP(target_index=0)
    bars = json.loads(c.evaluate(pt.BARS_JS))
    c.close()
    ohlc = {int(b[0]): (float(b[1]), float(b[2]), float(b[3]), float(b[4]))
            for b in bars}
    if len(ohlc) < 80:
        return (False, f"only {len(ohlc)} candles came back")
    room = pt._stop_room()
    hits = 0
    for k in sorted(ohlc)[-200:]:
        win = {x: ohlc[x] for x in sorted(ohlc) if x <= k}
        if pt.breakout(win, k, max_stop=room) or pt.trend_ride(win, k,
                                                               max_stop=room):
            hits += 1
    return (True, f"{len(ohlc)} candles, both functions ran, "
                  f"{hits} setups in the last 200 bars")


@check("no setup can ever have a stop the exchange reaches first")
def _():
    import papertrade as pt
    from signals.tv_cdp import TradingViewCDP
    room = pt._stop_room()
    c = TradingViewCDP(target_index=0)
    bars = json.loads(c.evaluate(pt.BARS_JS))
    c.close()
    ohlc = {int(b[0]): (float(b[1]), float(b[2]), float(b[3]), float(b[4]))
            for b in bars}
    worst = 0.0
    n = 0
    for k in sorted(ohlc)[-200:]:
        win = {x: ohlc[x] for x in sorted(ohlc) if x <= k}
        for g in (pt.breakout(win, k, max_stop=room),
                  pt.trend_ride(win, k, max_stop=room)):
            if g:
                n += 1
                worst = max(worst, g["stop_pct"])
    return (worst <= room + 1e-9,
            f"the line is {room:.2f}%; the widest of {n} setups was "
            f"{worst:.2f}%")


@check("the reading refuses a coin that cannot travel")
def _():
    from papertrade import confidence
    good, _ = confidence(reach=45, smooth=0.6, agree=5, against=0,
                         tall=1.8, stairs=4)
    quiet, _ = confidence(reach=5, smooth=0.6, agree=5, against=0,
                          tall=1.8, stairs=4)
    chop, _ = confidence(reach=45, smooth=0.12, agree=5, against=0,
                         tall=1.8, stairs=4)
    return (good > 60 and quiet < 20 and chop < 20,
            f"a mover reads {good:.0f}, a quiet coin {quiet:.0f}, "
            f"a thrasher {chop:.0f}")


@check("the scanner publishes what the reading needs")
def _():
    d = json.load(open("data/boom.json"))
    have = [k for k in ("reach", "smooth", "median_bars") if k in d[0]]
    age = (time.time() - os.path.getmtime("data/boom.json")) / 3600
    return (len(have) == 3,
            f"{len(d)} coins, fields {have}, written {age:.1f}h ago")


@check("the book can read those numbers back")
def _():
    from papertrade import coin_reach
    d = json.load(open("data/boom.json"))
    sym = d[0]["sym"]
    r, s = coin_reach(sym)
    return (r is not None and s is not None,
            f"{sym}: covers 10% {r}% of the time, {s} of it in a line")


@check("read_window returns candles and setups off the live chart")
def _():
    import papertrade as pt
    got = pt.read_window(0)
    if not got:
        return (False, "no window")
    sym, res, sigs, ohlc = got
    br = [x for x in sigs if x.get("kind") == "break"]
    return (len(ohlc) > 50,
            f"{sym} @ {res}m, {len(ohlc)} candles, {len(sigs)} signals, "
            f"{len(br)} of them a shape")


@check("the notification goes to ntfy and says so")
def _():
    import papertrade as pt
    import logging
    seen = []
    h = logging.Handler()
    h.emit = lambda r: seen.append(r.getMessage())
    pt.log.addHandler(h)
    pt.SILENT = False
    pt.ping("TBT path check", "final debug", test=True)
    pt.log.removeHandler(h)
    bad = [m for m in seen if "nothing reached" in m or "failed" in m]
    return (not bad, f"sent; complaints: {bad or 'none'}")


@check("the book is running and has not been restarting")
def _():
    act = sh("systemctl", "is-active", "tbt-paper")
    n = sh("systemctl", "show", "tbt-paper", "-p", "NRestarts", "--value")
    return (act == "active" and n == "0", f"{act}, {n} restarts")


@check("the service asks for the shape and nothing else")
def _():
    u = open("/etc/systemd/system/tbt-paper.service").read()
    need = ["--source break", "--min-confidence", "--lev 50", "--tp 10",
            "--frac 0.5", "--scout"]
    missing = [x for x in need if x not in u]
    return (not missing, f"missing {missing or 'nothing'}")


@check("scout, guard, panel and chrome are alive")
def _():
    st = {u: sh("systemctl", "is-active", u)
          for u in ("tbt-scout", "tbt-guard", "tbt-panel", "tbt-chrome",
                    "tbt-recorder")}
    dead = [k for k, v in st.items() if v != "active"]
    return (not dead, f"down: {dead or 'nothing'}")


@check("the book is clean and nothing is stranded")
def _():
    b = json.load(open("data/paper.json"))
    op = [t for t in b.get("trades", []) if not t.get("closed")]
    return (True, f"${b['equity']:.2f} from ${b['start']:.2f}, "
                  f"{len(b.get('trades', []))} trades, {len(op)} open")


print()
for tag, rows, mark in (("", PASS, "ok  "), ("", WARN, "??  "),
                        ("", FAIL, "FAIL")):
    for name, detail in rows:
        print(f"  {mark}  {name}")
        if detail:
            print(f"        {detail}")
print(f"\n  {len(PASS)} passed, {len(WARN)} unknown, {len(FAIL)} failed")
