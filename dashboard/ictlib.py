"""ICT primitives for one timeframe: the event tape the playbook models (playbook.py) are built from.

Every rule follows the gold ICT notes in the user's goldictcontents folder (ICT Concepts for Gold Trading + the
@ICT_Success slides). Only closed candles are read and each event carries the index of the candle on which it
became known, so nothing repaints.

  swing      3-candle pivot high / low, known 3 candles after it.
  sweep      a candle trades through an untaken swing level (or an external level: PDH / PDL, Asian range, opening
             gaps, round numbers) and its body closes back inside, on that candle or the next one (turtle soup).
             Wicks through a level are sweeps, never structure.
  disp       displacement candle: body at least 70 % of its range and the range at least DISP_ATR x ATR.
  FVG        three-candle imbalance: candle 1's wick and candle 3's wick don't overlap. BISI (up) / SIBI (down).
             CE = its 50 %. A body close beyond the CE degrades it; a body close beyond its far edge inverts it.
  IFVG       inversion fair value gap: a failed FVG, closed through by a candle body, flips to the other side.
  BPR        balanced price range: a bullish and a bearish FVG born close together whose prices overlap. The
             overlap is the zone; its side is the newer gap's.
  OB         order block: the last opposite-close candle at a swing (the last up candle at a swing high is a
             bearish OB, the last down candle at a swing low a bullish OB). MT = 50 % of its body. A body close
             beyond the MT invalidates it; a body close beyond its far extreme turns it into a breaker (BB) the
             other way.
  CISD       change in state of delivery: the run of same-colour candles that delivered price into the newest
             extreme (lowest low / highest high of the last CISD_LOOK candles) has an opening price; a later body
             close beyond it flips delivery. Earliest signal, more false positives.
  MSS        market structure shift: a body close beyond the last opposite swing, preceded by a sweep in the leg
             and done with displacement that left an FVG. A close beyond a swing without those is BOS (with the
             trend) or CHoCH (against it).
  range      dealing range: the last swing high and low, stretched by newer extremes. 50 % is equilibrium;
             above it premium, below it discount. OTE 62-79 % back, golden zone 62-70 %.
"""
from __future__ import annotations

from engine import Bars

PIVOT = 3
DISP_BODY = 0.70          # body / range of a displacement candle
DISP_ATR = 1.0            # ...and its range in ATRs
FVG_ATR = 0.10            # smallest gap kept, in ATRs ...
MIN_GAP = 0.15            # ... and in dollars: a gap narrower than gold's spread is noise
CISD_LOOK = 20            # the extreme a CISD is measured from: lowest low / highest high of this many candles
CISD_WITHIN = 12          # the CISD must come within this many candles of the run's end
BPR_WITHIN = 12           # the two gaps of a BPR are born within this many candles
EQ_ATR = 0.10             # swing levels this close are equal highs / lows (relative equal = HRLR pool)
KEEP_SWINGS = 40
OTE = (0.62, 0.79)
GOLDEN = (0.62, 0.70)


def atr_series(b: Bars, n: int = 14) -> list:
    out = []
    for i in range(len(b)):
        tr = b.h[i] - b.l[i] if i == 0 else max(b.h[i] - b.l[i], abs(b.h[i] - b.c[i - 1]), abs(b.l[i] - b.c[i - 1]))
        out.append(tr if i == 0 else (out[-1] * (n - 1) + tr) / n)
    return out


def is_disp(b: Bars, i: int, atr: list, d: int = 0) -> bool:
    """Displacement candle at i (in direction d when given)."""
    rng = b.h[i] - b.l[i]
    body = b.c[i] - b.o[i]
    if rng <= 0 or abs(body) < DISP_BODY * rng or rng < DISP_ATR * (atr[i - 1] if i else atr[i]):
        return False
    return d == 0 or body * d > 0


class Tape:
    """All ICT events of one timeframe's closed candles. `b` must hold closed candles only."""

    def __init__(self, b: Bars, name: str = "", pivot: int = PIVOT, external: list | None = None,
                 min_gap: float = MIN_GAP):
        self.b, self.name, self.pivot, self.min_gap = b, name, pivot, min_gap
        self.n = n = len(b)
        self.atr = atr_series(b)
        self.swings: list = []        # {"i", "price", "dir" (+1 high, -1 low), "known"}
        self.sweeps: list = []        # {"dir" (+1 swept a low = bullish), "i", "level", "ext", "ext_i", "kind", "eq"}
        self.fvgs: list = []          # {"dir", "top", "bottom", "ce", "i", "born", "state", "end", "ce_broken"}
        self.ifvgs: list = []         # {"dir", "top", "bottom", "ce", "born", "from_fvg", "end"}
        self.bprs: list = []          # {"dir", "top", "bottom", "ce", "born"}
        self.obs: list = []           # {"dir", "kind" OB|BB, "top", "bottom", "mt", "i", "born", "end", "end_why"}
        self.cisd: list = []          # {"dir", "i", "ref", "ext", "ext_i", "run_i"}
        self.breaks: list = []        # {"dir", "kind" MSS|BOS|CHoCH, "i", "level", "swing_i", "ext", "ext_i",
                                      #  "sweep", "disp", "fvg", "leg_ob"}
        self.trend = 0
        self.untaken, self.ext_levels, self.cisd_ref = {1: [], -1: []}, [], {1: None, -1: None}
        if n >= 2 * pivot + 3:
            self._run(external or [])

    # ------------------------------------------------------------------ the forward pass
    def _run(self, external: list) -> None:
        b, P, atr = self.b, self.pivot, self.atr
        t, o, h, l, c = b.t, b.o, b.h, b.l, b.c
        n = self.n
        pend = {}                                    # known index -> swings confirmed then
        for i in range(P, n - P):
            if h[i] > max(h[i - P:i]) and h[i] >= max(h[i + 1:i + P + 1]):
                pend.setdefault(i + P, []).append({"i": i, "price": h[i], "dir": 1, "known": i + P})
            if l[i] < min(l[i - P:i]) and l[i] <= min(l[i + 1:i + P + 1]):
                pend.setdefault(i + P, []).append({"i": i, "price": l[i], "dir": -1, "known": i + P})
        untaken = {1: [], -1: []}                    # swing highs / lows not traded through yet
        ext_levels = [dict(x) for x in external]     # {"price", "dir" (+1 a high), "name", "from"}
        broke = []                                   # levels whose candle closed beyond: a sweep if the next closes back
        last_sw = {1: None, -1: None}                # newest confirmed swing high / low (dict, plus "broken")
        live_fvg, live_ob = [], []
        runs = {1: None, -1: None}                   # start index of the current down run (1) / up run (-1)
        ref = {1: None, -1: None}                    # pending CISD reference: (open, run start, extreme idx, run end)
        for j in range(n):
            # -------- FVGs: update, then create
            keep = []
            for g in live_fvg:
                d = g["dir"]
                body_lo, body_hi = min(o[j], c[j]), max(o[j], c[j])
                if not g["ce_broken"] and ((c[j] < g["ce"]) if d == 1 else (c[j] > g["ce"])):
                    g["ce_broken"] = j
                if (c[j] < g["bottom"]) if d == 1 else (c[j] > g["top"]):          # body closed through: inverted
                    g["state"], g["end"] = "inverted", j
                    self.ifvgs.append({"dir": -d, "top": g["top"], "bottom": g["bottom"], "ce": g["ce"], "born": j,
                                       "from_fvg": g["born"], "end": None})
                    continue
                if (l[j] <= g["bottom"]) if d == 1 else (h[j] >= g["top"]):
                    g["state"], g["end"] = "filled", j
                keep.append(g)
            live_fvg = [g for g in keep if g["state"] == "open" or j - g["end"] < 60]
            for x in self.ifvgs:                                                    # an IFVG ends on a close back
                if x["end"] is None and x["born"] < j and \
                        ((c[j] < x["bottom"]) if x["dir"] == 1 else (c[j] > x["top"])):
                    x["end"] = j
            if j >= 2:
                for d, lo, hi in ((1, h[j - 2], l[j]), (-1, h[j], l[j - 2])):
                    if hi - lo >= max(FVG_ATR * atr[j], self.min_gap):
                        g = {"dir": d, "top": hi, "bottom": lo, "ce": (hi + lo) / 2, "i": j - 1, "born": j,
                             "state": "open", "end": None, "ce_broken": None,
                             "disp": is_disp(b, j - 1, atr, d)}
                        self.fvgs.append(g)
                        live_fvg.append(g)
                        br = self.breaks[-1] if self.breaks else None                # a gap confirmed one candle
                        if br and br["dir"] == d and br["ext_i"] < g["i"] <= br["i"] + 1 and \
                                _better(g, br["fvg"]):                                # after the break belongs to it
                            br["fvg"] = g
                            if br["kind"] != "MSS" and br["sweep"] and br["disp"]:
                                br.update(kind="MSS", known=j)
                        for k in range(len(self.fvgs) - 2, -1, -1):                 # balanced price range
                            q = self.fvgs[k]
                            if j - q["born"] > BPR_WITHIN:
                                break
                            if q["dir"] == -d:
                                top, bot = min(hi, q["top"]), max(lo, q["bottom"])
                                if top > bot:
                                    self.bprs.append({"dir": d, "top": top, "bottom": bot, "ce": (top + bot) / 2,
                                                      "born": j, "end": None})
                                    break
            for x in self.bprs:
                if x["end"] is None and x["born"] < j and \
                        ((c[j] < x["bottom"]) if x["dir"] == 1 else (c[j] > x["top"])):
                    x["end"] = j
            # -------- order blocks / breakers
            keep = []
            for z in live_ob:
                d = z["dir"]
                if (c[j] < z["bottom"]) if d == 1 else (c[j] > z["top"]):
                    z["end"], z["end_why"] = j, "closed through"
                    if z["kind"] == "OB":
                        bb = {"dir": -d, "kind": "BB", "top": z["top"], "bottom": z["bottom"], "mt": z["mt"],
                              "i": z["i"], "born": j, "end": None, "end_why": None, "swing_i": z.get("swing_i")}
                        self.obs.append(bb)
                        keep.append(bb)
                    continue
                if z["kind"] == "OB" and z.get("mt_broken") is None and ((c[j] < z["mt"]) if d == 1 else (c[j] > z["mt"])):
                    z["mt_broken"] = j                                              # beyond the MT: abandoned
                keep.append(z)
            live_ob = keep[-40:]
            # -------- sweeps of swing levels and external levels
            for lv in broke:                                                        # closed beyond last candle:
                d = lv["dir"]                                                       # a sweep if this one closes back
                if (c[j] < lv["price"]) if d == 1 else (c[j] > lv["price"]):
                    ext_i = lv["j"] + (1 if ((h[j] > h[lv["j"]]) if d == 1 else (l[j] < l[lv["j"]])) else 0)
                    self._sweep(-d, j, lv, ext_i)
            broke = []
            for d in (1, -1):
                still = []
                for s in untaken[d]:
                    if (h[j] > s["price"]) if d == 1 else (l[j] < s["price"]):
                        if (c[j] < s["price"]) if d == 1 else (c[j] > s["price"]):
                            self._sweep(-d, j, s, j)
                        else:
                            broke.append(dict(s, j=j))
                    else:
                        still.append(s)
                untaken[d] = still
            for lv in ext_levels:
                if lv.get("taken") or lv.get("from", -1) >= t[j]:
                    continue
                d = lv["dir"]
                if (h[j] > lv["price"]) if d == 1 else (l[j] < lv["price"]):
                    lv["taken"] = True
                    if (c[j] < lv["price"]) if d == 1 else (c[j] > lv["price"]):
                        self._sweep(-d, j, lv, j)
                    else:
                        broke.append(dict(lv, j=j))
            # -------- CISD
            for d in (1, -1):
                same = (c[j] < o[j]) if d == 1 else (c[j] > o[j])                 # the delivery run for side d
                if same:
                    if runs[d] is None:
                        runs[d] = j
                elif runs[d] is not None:
                    s = runs[d]
                    rng = range(s, j)
                    k = min(rng, key=lambda m: l[m]) if d == 1 else max(rng, key=lambda m: h[m])
                    lo_i = max(0, j - CISD_LOOK)
                    if (d == 1 and l[k] <= min(l[lo_i:j])) or (d == -1 and h[k] >= max(h[lo_i:j])):
                        ref[d] = (o[s], s, k, j - 1)
                    runs[d] = None
            for d in (1, -1):
                r = ref[d]
                if not r:
                    continue
                newer = (l[j] < l[r[2]]) if d == 1 else (h[j] > h[r[2]])
                if newer and not ((c[j] < o[j]) if d == 1 else (c[j] > o[j])):
                    ref[d] = None                                                   # a new extreme by a turning candle
                    continue
                if j - r[3] > CISD_WITHIN:
                    ref[d] = None
                elif j > r[3] and (c[j] - r[0]) * d > 0 and (c[j] - o[j]) * d > 0:
                    self.cisd.append({"dir": d, "i": j, "ref": r[0], "ext": l[r[2]] if d == 1 else h[r[2]],
                                      "ext_i": r[2], "run_i": r[1]})
                    ref[d] = None
            # -------- structure: a body close through the last opposite swing
            for d in (1, -1):
                s = last_sw[d]
                if s and not s.get("broken") and (c[j] - s["price"]) * d > 0:
                    s["broken"] = True
                    self._break(d, j, s)
            # -------- swings confirmed now
            for s in pend.get(j, []):
                self.swings.append(s)
                d = s["dir"]
                if (max(h[s["i"] + 1:j + 1]) <= s["price"]) if d == 1 else (min(l[s["i"] + 1:j + 1]) >= s["price"]):
                    untaken[d] = (untaken[d] + [s])[-KEEP_SWINGS:]
                last_sw[d] = dict(s)
                ob = self._swing_ob(s)                                              # the OB at this swing
                if ob:
                    self.obs.append(ob)
                    live_ob.append(ob)
        self.untaken = untaken
        self.ext_levels = ext_levels
        # the level a CISD would have to close beyond right now: the open of the run still delivering price
        # (or of the last run that made the extreme), per side
        self.cisd_ref = {}
        for d in (1, -1):
            if runs[d] is not None:
                self.cisd_ref[d] = o[runs[d]]
            elif ref[d]:
                self.cisd_ref[d] = ref[d][0]
            else:
                self.cisd_ref[d] = None

    def _sweep(self, d: int, j: int, lv: dict, ext_i: int) -> None:
        """A sweep at j of level lv. d: +1 when a low was swept (bullish), -1 a high."""
        b = self.b
        eq = False
        if "i" in lv and "name" not in lv:                                         # relative equal highs / lows
            a = self.atr[j] * EQ_ATR
            eq = any(s is not lv and s["dir"] == lv["dir"] and abs(s["price"] - lv["price"]) <= a and s["i"] < lv["i"]
                     for s in self.swings[-12:])
        lo_i = max(0, ext_i - 2)
        ext_i = min(range(lo_i, j + 1), key=lambda m: b.l[m]) if d == 1 else max(range(lo_i, j + 1), key=lambda m: b.h[m])
        self.sweeps.append({"dir": d, "i": j, "level": lv["price"], "ext": b.l[ext_i] if d == 1 else b.h[ext_i],
                            "ext_i": ext_i, "kind": lv.get("name") or f"{self.name} swing {'low' if d == 1 else 'high'}",
                            "eq": eq, "swing_i": lv.get("i")})

    def _swing_ob(self, s: dict) -> dict | None:
        """At a swing high the last up-close candle (bearish OB); at a swing low the last down-close (bullish OB)."""
        b, i = self.b, s["i"]
        want = 1 if s["dir"] == 1 else -1                                          # candle colour sought
        for k in range(i, max(-1, i - 4), -1):
            if (b.c[k] - b.o[k]) * want > 0:
                d = -s["dir"]                                                       # OB direction
                return {"dir": d, "kind": "OB", "top": b.h[k], "bottom": b.l[k], "mt": (b.o[k] + b.c[k]) / 2,
                        "i": k, "born": s["known"], "end": None, "end_why": None, "swing_i": i, "mt_broken": None}
        return None

    def _break(self, d: int, j: int, s: dict) -> None:
        b, atr = self.b, self.atr
        rng = range(s["i"], j + 1)
        ext_i = min(rng, key=lambda m: b.l[m]) if d == 1 else max(rng, key=lambda m: b.h[m])
        ext = b.l[ext_i] if d == 1 else b.h[ext_i]
        sweep = next((w for w in reversed(self.sweeps) if w["dir"] == d and s["i"] - 2 <= w["i"] <= j
                      and abs(w["ext_i"] - ext_i) <= 3), None)
        disp = any(is_disp(b, m, atr, d) for m in range(ext_i + 1, j + 1))
        fvg = None
        for g in self.fvgs[-30:]:
            if g["dir"] == d and ext_i < g["i"] <= j and _better(g, fvg):
                fvg = g
        if sweep and disp and fvg:
            kind = "MSS"
        else:
            kind = "BOS" if self.trend == d else "CHoCH"
        leg_ob = self._swing_ob({"i": ext_i, "dir": -d, "known": j})                # last opposite candle at the leg's start
        self.breaks.append({"dir": d, "kind": kind, "i": j, "level": s["price"], "swing_i": s["i"], "ext": ext,
                            "ext_i": ext_i, "sweep": sweep, "disp": disp, "fvg": fvg, "leg_ob": leg_ob})
        self.trend = d

    # ------------------------------------------------------------------ queries (as of the last closed candle)
    def last(self, kind: str, d: int | None = None, since: int = 0):
        xs = getattr(self, kind)
        for x in reversed(xs):
            if x.get("i", x.get("born", 0)) < since:
                return None
            if d is None or x["dir"] == d:
                return x
        return None

    def dealing_range(self) -> dict | None:
        hs = [s for s in self.swings if s["dir"] == 1]
        ls = [s for s in self.swings if s["dir"] == -1]
        if not hs or not ls:
            return None
        b, n = self.b, self.n
        hi, lo = hs[-1], ls[-1]
        top = max(hi["price"], max(b.h[hi["i"]:n]))
        bot = min(lo["price"], min(b.l[lo["i"]:n]))
        if top <= bot:
            return None
        px = b.c[-1]
        eq = (top + bot) / 2
        pos = (px - bot) / (top - bot)
        leg = 1 if hi["i"] > lo["i"] else -1                                       # the newest leg's direction
        return {"high": top, "low": bot, "eq": eq, "pos": round(pos, 3),
                "zone": "premium" if px > eq else "discount", "leg": leg,
                "high_t": b.t[hi["i"]], "low_t": b.t[lo["i"]]}

    def open_fvgs(self, d: int | None = None) -> list:
        return [g for g in self.fvgs if g["state"] == "open" and (d is None or g["dir"] == d)]

    def live_obs(self, d: int | None = None, kind: str | None = None) -> list:
        return [z for z in self.obs if z["end"] is None and (d is None or z["dir"] == d) and (kind is None or z["kind"] == kind)]

    def live_ifvgs(self, d: int | None = None) -> list:
        return [x for x in self.ifvgs if x["end"] is None and (d is None or x["dir"] == d)]

    def live_bprs(self, d: int | None = None) -> list:
        return [x for x in self.bprs if x["end"] is None and (d is None or x["dir"] == d)]

    def erl(self, d: int, price: float) -> list:
        """External range liquidity on side d (+1 above price): untaken swing levels, nearest first."""
        xs = [s for s in self.untaken[d] if (s["price"] - price) * d > 0]
        return sorted(xs, key=lambda s: abs(s["price"] - price))

    def irl(self, d: int, price: float) -> list:
        """Internal range liquidity on side d (+1 above price): open FVGs, live IFVGs and BPRs, nearest first."""
        out = []
        for g in self.open_fvgs():
            out.append({"kind": "FVG", "dir": g["dir"], "top": g["top"], "bottom": g["bottom"], "ce": g["ce"], "born": g["born"]})
        for x in self.live_ifvgs():
            out.append({"kind": "IFVG", "dir": x["dir"], "top": x["top"], "bottom": x["bottom"], "ce": x["ce"], "born": x["born"]})
        for x in self.live_bprs():
            out.append({"kind": "BPR", "dir": x["dir"], "top": x["top"], "bottom": x["bottom"], "ce": x["ce"], "born": x["born"]})
        out = [z for z in out if ((z["bottom"] - price) if d == 1 else (price - z["top"])) > -1e-9]
        return sorted(out, key=lambda z: abs(z["ce"] - price))

    def time(self, i: int) -> int:
        return self.b.t[i]


def _better(g: dict, cur: dict | None) -> bool:
    """The entry gap of a leg: the displacement candle's gap first, then the larger one."""
    if cur is None:
        return True
    if g.get("disp") != cur.get("disp"):
        return bool(g.get("disp"))
    return g["top"] - g["bottom"] > cur["top"] - cur["bottom"]


def ote_zone(d: int, start: float, tip: float) -> dict:
    """Fib of a leg from `start` (level 1, the swing the move began at) to `tip` (level 0) for a trade in
    direction d: buys retrace down into 0.62-0.79 of an up leg."""
    span = (tip - start) * d
    lvl = lambda r: tip - d * r * span
    return {"start": start, "tip": tip, "62": lvl(0.62), "70": lvl(0.70), "705": lvl(0.705), "79": lvl(0.79),
            "50": lvl(0.5), "ext_-0.5": tip + d * 0.5 * span, "ext_-0.27": tip + d * 0.27 * span,
            "golden": sorted((lvl(GOLDEN[0]), lvl(GOLDEN[1]))), "ote": sorted((lvl(OTE[0]), lvl(OTE[1])))}
