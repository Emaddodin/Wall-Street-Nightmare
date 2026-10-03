"""ICT tape and playbook on hand-built gold candles: sweep -> MSS with displacement -> FVG -> retrace."""
import calendar
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ictclock as ck                     # noqa: E402
from engine import Bars                   # noqa: E402
from ictlib import Tape, ote_zone         # noqa: E402
from playbook import Playbook, Track, MODELS   # noqa: E402

# Tuesday 2026-09-15 07:00 UTC = 03:00 New York (EDT): London killzone and the London Silver Bullet
T0 = calendar.timegm((2026, 9, 15, 7, 0, 0))
UTC = lambda t: t


def bars(rows, sec=60, t0=T0):
    b = Bars(sec)
    for i, (o, h, l, c) in enumerate(rows):
        b.append(t0 + i * sec, o, h, l, c)
    return b


def walk(path, sec=60, t0=T0, wick=0.15):
    """Candles through the given closes, each opening at the previous close."""
    rows, prev = [], path[0]
    for c in path[1:]:
        rows.append((prev, max(prev, c) + wick, min(prev, c) - wick, c))
        prev = c
    return bars(rows, sec, t0)


def bullish_mss():
    """Down drift, swing low 1998, bounce to a 2004 swing high, sweep 1997 closing back, displacement up through
    2004 leaving a gap, then a retrace into the gap."""
    rows = []
    p = 2015.0
    for _ in range(50):                                   # quiet drift down
        rows.append((p, p + 0.3, p - 0.6, p - 0.3))
        p -= 0.3
    rows += [(p, p + .2, p - 1.0, p - .8), (p - .8, p - .4, 1998.0, 1998.6),       # swing low 1998.0
             (1998.6, 2000.5, 1998.4, 2000.2), (2000.2, 2002.6, 2000.0, 2002.4), (2002.4, 2004.0, 2002.0, 2003.5),
             (2003.5, 2003.7, 2001.8, 2002.0), (2002.0, 2002.3, 2000.0, 2000.3), (2000.3, 2000.6, 1999.0, 1999.2),
             (1999.2, 1999.5, 1997.0, 1999.3),                                     # sweep of 1998 closing back
             (1999.3, 2000.4, 1999.1, 2000.2),
             (2000.2, 2006.0, 2000.1, 2005.8),                                     # displacement through 2004
             (2005.8, 2007.0, 2003.0, 2006.6),                                     # leaves a gap 2000.4-2003.0
             (2006.6, 2007.4, 2005.9, 2007.1), (2007.1, 2007.3, 2005.0, 2005.2), (2005.2, 2005.4, 2002.4, 2002.8)]
    return bars(rows)


def test_tape_sweep_mss_fvg():
    T = Tape(bullish_mss(), "M1")
    sw = [s for s in T.sweeps if s["dir"] == 1 and abs(s["level"] - 1998.0) < 1e-9]
    assert sw, T.sweeps
    br = [b for b in T.breaks if b["dir"] == 1 and abs(b["level"] - 2004.0) < 1e-9]
    assert br and br[-1]["kind"] == "MSS", br
    g = br[-1]["fvg"]
    assert abs(g["bottom"] - 2000.4) < 1e-9 and abs(g["top"] - 2003.0) < 1e-9
    assert abs(g["ce"] - 2001.7) < 1e-9


def test_no_repaint_prefix():
    b = bullish_mss()
    full = Tape(b, "M1")
    for k in (30, 45, 50):
        part = Tape(b.slice_from(0) if False else _head(b, k), "M1")
        n = len(part.sweeps)
        assert [(s["i"], s["level"]) for s in part.sweeps] == [(s["i"], s["level"]) for s in full.sweeps[:n]]
        m = len(part.cisd)
        assert [(c["i"], c["ref"]) for c in part.cisd] == [(c["i"], c["ref"]) for c in full.cisd[:m]]


def _head(b, k):
    x = Bars(b.sec)
    for i in range(k):
        x.append(b.t[i], b.o[i], b.h[i], b.l[i], b.c[i])
    return x


def test_ifvg_and_bpr():
    # a bearish gap, then a bullish candle closing through its top: an IFVG support
    rows = [(2010, 2010.5, 2009.5, 2010)] * 5 + [(2010, 2010.2, 2008.0, 2008.2), (2008.2, 2008.3, 2005.0, 2005.2),
                                                (2005.2, 2006.0, 2004.8, 2005.8), (2005.8, 2009.6, 2005.6, 2009.4)]
    T = Tape(bars(rows), "M1")
    assert any(x["dir"] == 1 for x in T.ifvgs), T.ifvgs


def test_ote_levels():
    z = ote_zone(1, 2000.0, 2010.0)
    assert abs(z["62"] - 2003.8) < 1e-9 and abs(z["79"] - 2002.1) < 1e-9
    assert z["golden"] == sorted([2003.8, 2003.0])
    s = ote_zone(-1, 2010.0, 2000.0)
    assert abs(s["62"] - 2006.2) < 1e-9


def test_clock_windows():
    c = ck.clock(T0 + 10 * 60)                               # 03:10 New York
    assert c["killzone"] == "London" and c["silver_bullet"] == "London SB"
    c = ck.clock(calendar.timegm((2026, 9, 15, 13, 55, 0)))  # 09:55 New York: the NY AM primary macro
    assert c["macro"] == "NY AM Primary Macro" and c["killzone"] == "NY AM"
    c = ck.clock(calendar.timegm((2026, 9, 15, 16, 30, 0)))  # 12:30 New York
    assert c["lunch"]


def test_playbook_finds_mss_fvg_setup():
    m1 = bullish_mss()
    pb = Playbook(Track(None, None))
    st = pb.update({"M1": m1}, UTC, spread=0.2)
    assert st is not None
    names = {s["model"] for s in st["setups"] if s["tf"] == "M1" and s["dir"] == 1}
    assert "mss_fvg" in names, [(s["model"], s["dir"]) for s in st["setups"]]
    s = next(s for s in st["setups"] if s["model"] == "mss_fvg")
    assert abs(s["entry"] - (2001.7 + 0.2)) < 1e-6           # FVG CE plus the spread for a buy limit
    assert s["sl"] < 1997.0 and s["tp1"] > s["entry"] and s["rr1"] >= 1.5
    assert s["grade"] in ("A+", "A", "B", "C") and s["checks"]
    assert st["talk"]["lines"] and st["ranking"][0]["model"] in MODELS
    assert set(st["timeframes"]) == {"M1"}


def test_track_resolves_target():
    tr = Track(None, None)
    s = {"id": "x", "model": "mss_fvg", "tf": "M1", "dir": 1, "entry": 2001.9, "sl": 1996.7, "tp1": 2012.0, "t": T0,
         "grade": "A", "order": "limit", "fill_by": T0 + 1800}
    tr.add(s)
    rows = [(2003, 2003.2, 2001.5, 2002), (2002, 2013, 2001.9, 2012.5)]
    done = tr.step("M1", bars(rows, t0=T0 + 60), spread=0.2)
    assert done and done[0]["how"] == "target" and done[0]["r"] > 1.9
    assert tr.stats("mss_fvg")["n_live"] == 1
