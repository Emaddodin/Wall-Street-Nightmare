"""ICT time and the day's reference levels for gold, in New York time (ICT's clock, daylight saving included).

Windows from the gold notes (ICT Concepts for Gold Trading):
  Asian range        20:00-02:00   accumulation: the range London manipulates (Judas swing)
  London killzone    02:00-05:00   manipulation, the Judas swing in the first 30-90 minutes
  NY AM killzone     08:30-11:00   continuation, the 10:00 Silver Bullet
  NY lunch           12:00-13:00   avoid
  NY PM killzone     13:30-16:00
  Silver Bullet      03:00-04:00, 10:00-11:00 (the one gold traders prefer), 14:00-15:00
  Macros (20 min)    02:33-03:00, 04:03-04:30, 08:50-09:10, 09:50-10:10 (highest-volume gold macro),
                     10:50-11:10, 11:50-12:10, 13:10-13:40
  NDOG               the 17:00 close to the 18:00 open each day; NWOG Friday 17:00 close to Sunday 18:00 open.
                     Their edges and 50 % act as support / resistance and pull price back.
  PDH / PDL          previous New York trading day (17:00 to 17:00); PWH / PWL the previous week.
"""
from __future__ import annotations

from engine import Bars

DAY = 86400
KILLZONES = (("Asia", 1200, 1440), ("London", 120, 300), ("NY AM", 510, 660), ("NY Lunch", 720, 780),
             ("NY PM", 810, 960))
SILVER_BULLET = (("London SB", 180, 240), ("NY AM SB", 600, 660), ("NY PM SB", 840, 900))
MACROS = (("London Morning Macro", 153, 180), ("London Expansion Macro", 243, 270), ("NY Pre-Open Macro", 530, 550),
          ("NY AM Primary Macro", 590, 610), ("NY Midday Macro", 650, 670), ("NY Rebalance Macro", 710, 730),
          ("NY Afternoon Macro", 790, 820))
AMD = (("Accumulation (Asia)", 1200, 1560), ("Manipulation (London)", 120, 330), ("Distribution (London-NY)", 330, 960))


# The New York day for gold, segment by segment: what the notes say each part of the day is for, and which
# playbook models fit it. "avoid" segments are thin or erratic (lunch, the last hour, the daily break).
DAY_MAP = (
    ("Asia", 1200, 1440, False, "Accumulation: Asia builds the range London will raid. Mark its high and low; trade small.",
     ("turtle_soup", "bpr", "pulse_rev")),
    ("Asia late", 0, 120, False, "The Asian range finishes. Reactions at the new day opening gap.",
     ("gap", "turtle_soup", "bpr")),
    ("London open", 120, 180, False, "Manipulation: the Judas swing runs the Asian range against the daily bias "
     "(London morning macro 02:33-03:00).", ("judas", "turtle_soup", "smt", "cisd_fvg", "hrlr")),
    ("London Silver Bullet", 180, 240, False, "The 03:00-04:00 Silver Bullet: sweep, MSS toward the draw, enter the FVG.",
     ("silver_bullet", "mss_fvg", "unicorn", "ifvg", "judas")),
    ("London expansion", 240, 300, False, "Distribution after the London shift (macro 04:03-04:30): continuation entries.",
     ("mss_fvg", "unicorn", "ote", "breaker", "pulse")),
    ("London-NY transition", 300, 510, False, "Quieter: London's move retraces. Continuation from internal liquidity only.",
     ("ote", "pulse", "ob_mt", "bpr")),
    ("NY open", 510, 600, False, "08:30 data and the open: the NY Judas runs London's range (macros 08:50 and 09:50).",
     ("judas", "turtle_soup", "smt", "cisd_fvg", "hrlr", "mss_fvg")),
    ("NY AM Silver Bullet", 600, 660, False, "10:00-11:00, gold's best window: sweep, MSS, FVG entry toward the draw.",
     ("silver_bullet", "mss_fvg", "unicorn", "ifvg", "breaker", "cisd_fvg")),
    ("NY late morning", 660, 720, False, "Continuation and the last morning distribution (macros 10:50 and 11:50).",
     ("ote", "pulse", "ob_mt", "bpr", "breaker")),
    ("NY lunch", 720, 780, True, "Lunch: thin and choppy. Setups fail more; stand aside.", ()),
    ("NY PM open", 780, 840, False, "The afternoon resumes the trend or retraces the morning (macro 13:10-13:40).",
     ("pulse_rev", "turtle_soup", "ote", "pulse")),
    ("NY PM Silver Bullet", 840, 900, False, "14:00-15:00 Silver Bullet.", ("silver_bullet", "mss_fvg", "unicorn", "ifvg")),
    ("NY close", 900, 960, False, "The last hour: rebalancing into internal liquidity.", ("pulse_rev", "ote", "bpr")),
    ("After hours", 960, 1020, True, "Thin until the daily break: no new trades.", ()),
    ("Daily break", 1020, 1080, True, "17:00-18:00: gold is closed. The new day opening gap forms.", ()),
    ("Globex open", 1080, 1200, False, "The reopen: price reacts to the NDOG / NWOG; slow until Asia.",
     ("gap", "pulse", "bpr")),
)


def session(utc: int) -> dict:
    """The DAY_MAP segment at this moment."""
    m = ny_minute(utc)
    for name, a, z, avoid, note, models in DAY_MAP:
        if a <= m < z:
            return {"name": name, "start": a, "end": z, "avoid": avoid, "note": note, "models": models}
    return {"name": "Asia", "start": 1200, "end": 1440, "avoid": False, "note": "", "models": ()}


def ny(utc: int) -> int:
    from smc import _ny
    return _ny(int(utc))


def ny_minute(utc: int) -> int:
    return ny(utc) % DAY // 60


def weekday(utc: int) -> int:
    """Monday = 0 (New York date)."""
    return (ny(utc) // DAY + 3) % 7


def _inside(m: int, a: int, z: int) -> bool:
    return a <= m < z if z <= 1440 else (m >= a or m < z - 1440)


def _until(m: int, a: int) -> int:
    return (a - m) % 1440


def clock(utc: int) -> dict:
    """What time it is in ICT terms: killzone, macro, Silver Bullet, AMD phase and what comes next."""
    m = ny_minute(utc)
    kz = next((n for n, a, z in KILLZONES if _inside(m, a, z)), None)
    sb = next((n for n, a, z in SILVER_BULLET if _inside(m, a, z)), None)
    mac = next((n for n, a, z in MACROS if _inside(m, a, z)), None)
    amd = next((n for n, a, z in AMD if _inside(m, a, z)), None)
    nxt = []
    for kind, rows in (("killzone", KILLZONES), ("silver bullet", SILVER_BULLET), ("macro", MACROS)):
        for n, a, z in rows:
            if not _inside(m, a, z) and n != "NY Lunch":
                nxt.append({"kind": kind, "name": n, "in_min": _until(m, a), "start": f"{a // 60 % 24:02d}:{a % 60:02d}",
                            "end": f"{z // 60 % 24:02d}:{z % 60:02d}"})
    nxt.sort(key=lambda x: x["in_min"])
    wd = weekday(utc)
    return {"ny": f"{m // 60:02d}:{m % 60:02d}", "minute": m, "weekday": wd, "killzone": kz, "silver_bullet": sb,
            "macro": mac, "amd": amd, "lunch": kz == "NY Lunch", "active": bool(kz and kz != "NY Lunch") or bool(sb or mac),
            "next": nxt[:4]}


def in_window(utc: int, windows) -> bool:
    m = ny_minute(utc)
    return any(_inside(m, a, z) for _, a, z in windows)


def levels(h1: Bars, m5: Bars | None, utc) -> dict:
    """Reference levels known at the last closed candle: PDH / PDL, PWH / PWL, Asian range, NDOGs, NWOGs.
    Each level carries `from`, the candle time it became known (so sweeps of it are only counted after)."""
    out = {"pdh": None, "pdl": None, "pwh": None, "pwl": None, "asia": None, "ndog": [], "nwog": [], "nmo": None}
    if h1 is None or len(h1) < 30:
        return out
    tday = lambda t: (ny(utc(t)) - 17 * 3600) // DAY
    week = lambda d: (d + 4) // 7
    days = [tday(t) for t in h1.t]
    today = days[-1]
    prev = [i for i, d in enumerate(days) if d == today - 1] or [i for i, d in enumerate(days) if d < today][-24:]
    if prev:
        start = next(i for i, d in enumerate(days) if d == today) if today in days else len(days) - 1
        out["pdh"] = {"price": max(h1.h[i] for i in prev), "from": h1.t[start], "name": "PDH"}
        out["pdl"] = {"price": min(h1.l[i] for i in prev), "from": h1.t[start], "name": "PDL"}
    wk = week(today)
    pw = [i for i, d in enumerate(days) if week(d) == wk - 1]
    if pw:
        start = next((i for i, d in enumerate(days) if week(d) == wk), len(days) - 1)
        out["pwh"] = {"price": max(h1.h[i] for i in pw), "from": h1.t[start], "name": "PWH"}
        out["pwl"] = {"price": min(h1.l[i] for i in pw), "from": h1.t[start], "name": "PWL"}
    # opening gaps: the 16:00 hour's close against the 18:00 hour's open (New York)
    for i in range(1, len(h1)):
        a, z = ny(utc(h1.t[i - 1])), ny(utc(h1.t[i]))
        if (a % DAY) // 3600 == 16 and (z % DAY) // 3600 == 18 and z - a <= 3 * DAY:
            top, bot = max(h1.c[i - 1], h1.o[i]), min(h1.c[i - 1], h1.o[i])
            gap = {"top": top, "bottom": bot, "ce": (top + bot) / 2, "from": h1.t[i], "size": top - bot}
            (out["nwog"] if z - a > DAY else out["ndog"]).append(gap)
    out["ndog"], out["nwog"] = out["ndog"][-3:], out["nwog"][-2:]
    # Asian range 20:00-02:00 that ended last (from M5, else H1)
    b = m5 if m5 is not None and len(m5) > 100 else h1
    hs, ls, key, done = [], [], None, None
    for i in range(len(b)):
        x = ny(utc(b.t[i]))
        mm = x % DAY // 60
        k = (x + 4 * 3600) // DAY
        if mm >= 1200 or mm < 120:
            if k != key:
                key, hs, ls = k, [], []
            hs.append(b.h[i])
            ls.append(b.l[i])
        elif key == k and hs:
            done = {"high": max(hs), "low": min(ls), "from": b.t[i]}
    if done:
        out["asia"] = done
    # New York midnight open
    for i in range(len(b) - 1, -1, -1):
        x = ny(utc(b.t[i]))
        if x % DAY < b.sec:
            out["nmo"] = {"price": b.o[i], "from": b.t[i], "name": "NMO"}
            break
    return out


def external_levels(lv: dict, htf_swings: list | None = None) -> list:
    """The levels a raid on external liquidity takes, as Tape externals: {"price", "dir" (+1 a high), "name", "from"}."""
    out = []
    for k, d in (("pdh", 1), ("pdl", -1), ("pwh", 1), ("pwl", -1)):
        if lv.get(k):
            out.append({"price": lv[k]["price"], "dir": d, "name": lv[k]["name"], "from": lv[k]["from"]})
    if lv.get("asia"):
        out.append({"price": lv["asia"]["high"], "dir": 1, "name": "Asian high", "from": lv["asia"]["from"]})
        out.append({"price": lv["asia"]["low"], "dir": -1, "name": "Asian low", "from": lv["asia"]["from"]})
    for g in lv.get("ndog", []) + lv.get("nwog", []):
        kind = "NWOG" if g in lv.get("nwog", []) else "NDOG"
        out.append({"price": g["top"], "dir": 1, "name": f"{kind} top", "from": g["from"]})
        out.append({"price": g["bottom"], "dir": -1, "name": f"{kind} bottom", "from": g["from"]})
    for s in htf_swings or []:
        out.append(s)
    return out
