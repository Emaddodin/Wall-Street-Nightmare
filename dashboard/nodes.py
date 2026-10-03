"""More knowledge for the mesh: the clock, the economic calendar and cross-markets. Free sources, no keys.

  Clock        how much gold usually moves in this New York hour (from a year of Dukascopy gold, clock_stats.json).
               Checked: the hour ranking of the size of moves held out of sample (Jun-Sep vs before: correlation
               0.79). Which way gold goes by hour did not (0.17), so the clock gives size, not direction.
  Calendar     the week's economic events from the free Forex Factory feed (nfs.faireconomy.media), fetched
               every hour. A high-impact USD event from 15 minutes before to 10 minutes after says WAIT. The
               mesh records how far gold moved around each event, so the effect is measured on live prices.
  Cross        silver, the dollar index, the US 10-year yield and S&P 500 futures from Yahoo's free chart feed
               (1-minute bars, polled each minute; often 10+ minutes delayed, shown as such). Votes: silver
               SMT (gold makes a new 15-minute high or low that silver doesn't confirm), dollar and yield
               (gold usually moves against them) and S&P (recorded as is; the mesh finds its sign).
               These votes go into the mesh like every other source and are scored out of sample on live
               prices. `python3 nodes.py cross GOLD.csv.gz SILVER.csv.gz ...` checks them on Dukascopy history.

Every node reports its own status (ok / delayed / unreachable), so a source the VPS can't reach shows as such.

    python3 nodes.py clock data/dukascopy_xauusd_m1.csv.gz     # rebuild clock_stats.json
"""
from __future__ import annotations

import json
import math
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLOCK_FILE = HERE / "clock_stats.json"
CACHE = Path.home() / ".golddesk"
UA = {"User-Agent": "Mozilla/5.0 GoldDesk"}
CAL_URLS = ("https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            "https://nfs.faireconomy.media/ff_calendar_nextweek.json")
CAL_EVERY = 3600
WAIT_BEFORE, WAIT_AFTER = 15, 10           # minutes around a high-impact USD event
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{}?interval=1m&range=1d"
CROSS = (   # name in the mesh, Yahoo symbol, Dukascopy name for the history check, sign for gold
    ("Silver SMT", "SI=F", "XAGUSD", 0),
    ("Dollar (DXY)", "DX-Y.NYB", "DOLLARIDXUSD", -1),
    ("US 10y yield", "%5ETNX", "USTBONDTRUSD", -1),    # the Dukascopy bond moves opposite to its yield
    ("S&P futures", "ES=F", "USA500IDXUSD", 1),
)
WINDOW, RECENT = 60, 15


def _get_json(url: str):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read())


def _ny_hour(utc: int) -> tuple:
    from smc import _ny
    ny = _ny(utc)
    return ny % 86400 // 3600, (ny // 86400 + 3) % 7


# ------------------------------------------------------------------ clock
def clock_build(path: str) -> dict:
    from boom_backtest import load
    m1 = load(path)
    c = {r[0]: r for r in m1}
    acc: dict = {}
    for r in m1:
        t = r[0]
        if t % 600:
            continue
        h, wd = _ny_hour(t)
        a = acc.setdefault(h, {"n10": 0, "m10": 0.0, "n60": 0, "m60": 0.0})
        e10 = c.get(t + 540)
        if e10:
            a["n10"] += 1
            a["m10"] += abs(e10[4] - r[1])
        if t % 3600 == 0:
            e60 = c.get(t + 3540)
            if e60:
                a["n60"] += 1
                a["m60"] += abs(e60[4] - r[1])
    hours = {str(h): {"move_10": round(a["m10"] / a["n10"], 2), "move_60": round(a["m60"] / max(a["n60"], 1), 2)}
             for h, a in sorted(acc.items()) if a["n10"]}
    from nowcast import hour_sd30
    for h, v in hour_sd30(m1).items():                 # the 30-minute line's hour term (nowcast.sigma_end)
        if str(h) in hours:
            hours[str(h)]["sd30_log"] = round(v, 6)
    avg = sum(v["move_60"] for v in hours.values()) / len(hours)
    out = {"source": Path(path).name, "built": time.strftime("%Y-%m-%d"), "avg_move_60": round(avg, 2),
           "hours_ny": hours, "checked": "hour ranking of move size, Jun-Sep vs Oct-May: correlation 0.79; "
                                        "direction by hour: 0.17 (no edge)"}
    CLOCK_FILE.write_text(json.dumps(out, indent=1))
    return out


class Clock:
    def __init__(self):
        try:
            self.d = json.loads(CLOCK_FILE.read_text())
        except (OSError, ValueError):
            self.d = None

    def sd30(self, utc: int) -> float | None:
        v = (self.d or {}).get("hours_ny", {}).get(str(_ny_hour(utc)[0])) or {}
        return v.get("sd30_log")

    def node(self, utc: int) -> dict:
        if not self.d:
            return {"id": "clock", "name": "Clock", "status": "missing", "value": None, "text": "no clock stats"}
        h, wd = _ny_hour(utc)
        v = self.d["hours_ny"].get(str(h))
        if not v:
            return {"id": "clock", "name": "Clock", "status": "ok", "value": None, "text": "market closed hour"}
        ratio = v["move_60"] / self.d["avg_move_60"]
        word = "busy" if ratio >= 1.25 else ("quiet" if ratio <= 0.8 else "normal")
        return {"id": "clock", "name": "Clock", "kind": "size", "status": "ok", "value": None,
                "text": f"NY {h:02d}:00 is a {word} hour: gold usually moves {v['move_60']:.1f} in an hour "
                        f"({ratio:.1f}x average), {v['move_10']:.1f} in 10 minutes",
                "detail": {"hour_ny": h, "weekday": wd, "move_10": v["move_10"], "move_60": v["move_60"],
                           "ratio": round(ratio, 2), "word": word}, "checked": self.d.get("checked"),
                "proven": "size only"}


# ------------------------------------------------------------------ calendar
class Calendar:
    def __init__(self):
        self.events: list = []
        self.status, self.error, self.fetched = "starting", None, 0
        self._load_cache()

    def _load_cache(self) -> None:
        try:
            d = json.loads((CACHE / "calendar.json").read_text())
            self.events, self.fetched = d["events"], d["fetched"]
            self.status = "ok"
        except (OSError, ValueError, KeyError):
            pass

    def refresh(self) -> None:
        if time.time() - self.fetched < CAL_EVERY:
            return
        evs, errs = [], []
        for u in CAL_URLS:
            try:
                for e in _get_json(u):
                    try:
                        t = int(datetime.fromisoformat(e["date"]).timestamp())
                    except (KeyError, ValueError):
                        continue
                    evs.append({"t": t, "title": e.get("title"), "country": e.get("country"),
                                "impact": e.get("impact"), "forecast": e.get("forecast"),
                                "previous": e.get("previous")})
            except Exception as ex:                       # next week's file is often missing early in the week
                errs.append(f"{u.rsplit('/', 1)[-1]}: {ex}")
        if evs:
            self.events = sorted({(e["t"], e["title"], e["country"]): e for e in evs}.values(), key=lambda e: e["t"])
            self.fetched, self.status, self.error = time.time(), "ok", None
            try:
                CACHE.mkdir(parents=True, exist_ok=True)
                (CACHE / "calendar.json").write_text(json.dumps({"fetched": self.fetched, "events": self.events}))
            except OSError:
                pass
        else:
            self.fetched = time.time() - CAL_EVERY + 600      # retry in 10 minutes
            self.status, self.error = ("unreachable" if not self.events else "stale"), "; ".join(errs)

    def near(self, utc: int) -> dict | None:
        """The high-impact USD event inside the wait window, else None."""
        for e in self.events:
            if e["country"] == "USD" and e["impact"] == "High" and \
                    -WAIT_BEFORE * 60 <= utc - e["t"] <= WAIT_AFTER * 60:
                return e
        return None

    def node(self, utc: int) -> dict:
        nxt = [e for e in self.events if e["t"] >= utc - WAIT_AFTER * 60 and e["country"] == "USD"
               and e["impact"] in ("High", "Medium")][:5]
        e = self.near(utc)
        if e:
            mins = round((e["t"] - utc) / 60)
            text = f"WAIT · {e['title']} {'in ' + str(mins) + ' min' if mins > 0 else str(-mins) + ' min ago'}"
        elif nxt:
            n = nxt[0]
            text = f"Next USD {n['impact'].lower()}: {n['title']} in {_dur(n['t'] - utc)}"
        else:
            text = "No USD high or medium events ahead this week" if self.events else "Calendar not loaded"
        return {"id": "calendar", "name": "Calendar", "kind": "event", "status": self.status, "error": self.error,
                "value": None, "wait": bool(e), "text": text, "event": e, "upcoming": nxt,
                "fetched": int(self.fetched) or None}


def _dur(s: int) -> str:
    m = max(0, s) // 60
    return f"{m} min" if m < 90 else (f"{m // 60} h {m % 60} min" if m < 1440 else f"{m // 1440} d {m % 1440 // 60} h")


# ------------------------------------------------------------------ cross-markets
def cross_votes(gold: dict, others: dict, t: int) -> dict:
    """Votes at minute t (UTC open time of the last closed candle). gold / others[name]: {t: (h, l, c)}."""
    out = {}
    for name, _, _, sign in CROSS:
        o = others.get(name)
        if not o:
            continue
        ts = [x for x in range(t - (WINDOW - 1) * 60, t + 60, 60) if x in o and x in gold]
        if len(ts) < WINDOW * 0.7 or ts[-1] < t - 120:
            continue
        if sign == 0:                                    # silver SMT
            cut = t - (RECENT - 1) * 60
            old, new = [x for x in ts if x < cut], [x for x in ts if x >= cut]
            if len(old) < 20 or len(new) < 8:
                continue
            gh, gl = max(gold[x][0] for x in new) > max(gold[x][0] for x in old), \
                min(gold[x][1] for x in new) < min(gold[x][1] for x in old)
            sh, sl = max(o[x][0] for x in new) > max(o[x][0] for x in old), \
                min(o[x][1] for x in new) < min(o[x][1] for x in old)
            out[name] = (-1.0 if gh and not sh else 0.0) + (1.0 if gl and not sl else 0.0)
        else:
            cs = [o[x][2] for x in ts]
            r = [math.log(b / a) for a, b in zip(cs, cs[1:]) if a > 0 and b > 0]
            back = [x for x in ts if x <= t - RECENT * 60]
            if len(r) < 20 or not back:
                continue
            sd = math.sqrt(sum(x * x for x in r) / len(r)) or 1e-9
            z = math.log(o[t][2] / o[back[-1]][2]) / (sd * math.sqrt(RECENT)) if t in o else 0.0
            out[name] = round(max(-1.0, min(1.0, sign * z / 2)), 3)
    return out


class Cross:
    def __init__(self):
        self.bars: dict = {}
        self.status: dict = {name: "starting" for name, *_ in CROSS}
        self.age: dict = {}
        self.fetched = 0
        self.votes: dict = {}

    def refresh(self) -> None:
        if time.time() - self.fetched < 60:
            return
        self.fetched = time.time()
        for name, sym, _, _ in CROSS:
            try:
                r = _get_json(YAHOO.format(sym))["chart"]["result"][0]
                q = r["indicators"]["quote"][0]
                bars = {t: (h, l, c) for t, h, l, c in zip(r["timestamp"], q["high"], q["low"], q["close"])
                        if None not in (h, l, c)}
                if not bars:
                    raise ValueError("no bars")
                self.bars[name] = bars
                self.age[name] = int(time.time() - max(bars) - 60)
                self.status[name] = "ok" if self.age[name] < 300 else "delayed"
            except Exception as e:
                self.status[name] = f"unreachable: {str(e)[:80]}"

    def update(self, gold: dict, t: int) -> dict:
        self.votes = cross_votes(gold, self.bars, t)
        return self.votes

    def nodes(self) -> list:
        out = []
        for name, *_ in CROSS:
            v = self.votes.get(name)
            st = self.status.get(name, "starting")
            age = self.age.get(name)
            if v is None:
                text = f"{name}: {st}" + (f", last bar {age // 60} min old" if age is not None else "")
            elif name == "Silver SMT":
                text = {1.0: "Silver SMT: gold swept a low silver held (bullish)",
                        -1.0: "Silver SMT: gold made a high silver didn't (bearish)"}.get(v, "Silver confirms gold")
            else:
                text = f"{name} {'favours' if v > 0 else 'weighs on'} gold ({v:+.2f})" if abs(v) >= 0.25 \
                    else f"{name}: flat"
            out.append({"id": "cross:" + name, "name": name, "kind": "vote", "status": st, "value": v,
                        "delay_min": age // 60 if age is not None else None, "text": text})
        return out


class Nodes:
    """Clock + calendar + cross-markets; fetching runs in its own thread so the dashboard never waits."""

    def __init__(self, fetch: bool = True):
        self.clock, self.calendar, self.cross = Clock(), Calendar(), Cross()
        self.fetch = fetch
        if fetch:
            threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while True:
            for f in (self.calendar.refresh, self.cross.refresh):
                try:
                    f()
                except Exception:
                    pass
            time.sleep(20)

    def votes(self, gold: dict, t: int) -> dict:
        return self.cross.update(gold, t) if self.fetch else {}

    def state(self, utc: int) -> list:
        return [self.clock.node(utc), self.calendar.node(utc)] + (self.cross.nodes() if self.fetch else [])


# ------------------------------------------------------------------ history check
def cross_check(gold_path: str, others: list, split: str) -> None:
    """Score the cross-market votes on Dukascopy history with the mesh's scoreboard, after `split`."""
    from boom_backtest import load
    from mesh import Board
    cut = int(datetime.fromisoformat(split).replace(tzinfo=timezone.utc).timestamp())
    g = {r[0]: (r[2], r[3], r[4]) for r in load(gold_path)}
    names = {d: n for n, _, d, _ in CROSS}
    oth = {}
    for p in others:
        key = Path(p).name.split("_")[1].upper()
        if key in names:
            oth[names[key]] = {r[0]: (r[2], r[3], r[4]) for r in load(p)}
    b = Board()
    for t in sorted(g):
        if t < cut:
            continue
        row = {"t": t + 60, "o": 0, "h": g[t][0], "l": g[t][1], "c": g[t][2]}
        if (t + 60) % 300 == 0:
            row.update(x={}, g=cross_votes(g, oth, t))
        b.feed(row)
    print(f"Cross-market votes on {gold_path} after {split} (non-overlapping samples):")
    for h in sorted(b.score):
        for n, s in sorted(b.score[h].items()):
            if n in oth or n in ("Always up", "Last 30 min"):
                v = s.view()
                print(f"  {h:>3} min  {n:<14} n {v['n']:>5}  right {v['right']:.1%}  (coin +/-{v['coin_band']:.1%})"
                      + ("  <- beyond a coin" if v["beats_coin"] else ""))


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "clock":
        print(json.dumps(clock_build(sys.argv[2]), indent=1))
    elif len(sys.argv) >= 4 and sys.argv[1] == "cross":
        cross_check(sys.argv[2], sys.argv[3:], "2026-06-01")
    else:
        raise SystemExit(__doc__)
