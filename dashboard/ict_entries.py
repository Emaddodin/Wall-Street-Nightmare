"""M1 scalp entries from ICT order flow: a raid on external liquidity, a change in state of delivery, then a
limit order in the fair value gap the displacement left.

  1 liquidity  external pools: untaken M15 swing highs / lows (3-candle pivots), the Asian range (20:00-02:00
               New York) and the previous New York day's high and low (PDH / PDL)
  2 raid       an M1 candle trades through a pool and closes back inside it (turtle soup), inside a killzone
               (London 02:00-05:00, New York AM 08:30-11:00 New York time)
  3 CISD       within CISD_WITHIN candles a candle body closes beyond the open of the run of candles that made
               the raid's extreme: order flow has turned
  4 FVG        the leg from the extreme to the CISD left a fair value gap; the limit order sits at its 50%
               (consequent encroachment)
  5 fill       price comes back to the CE within FILL_WITHIN candles, before running past the extreme

Stop beyond the raid's extreme plus a buffer; target TP_R times the risk. The trend reading (consensus.py)
can be required to agree. Nothing here places an order.
"""
from __future__ import annotations

from ictmodel import ny_clock

KILLZONES_NY = ((120, 300), (510, 660))     # minutes after New York midnight
CISD_WITHIN = 15
FILL_WITHIN = 30
SL_BUFFER = 0.30          # dollars beyond the extreme (gold M1: spread is about 0.2)
MIN_RISK = 1.0            # dollars
MAX_RISK = 8.0
TP_R = 2.0
MAX_MIN = 120
FVG_MIN = 0.15            # dollars


def in_killzone(utc: int) -> bool:
    ny = ny_clock(utc)
    m = ny % 86400 // 60
    return (ny // 86400 + 3) % 7 < 5 and any(a <= m < z for a, z in KILLZONES_NY)


class Pools:
    """External liquidity levels known at each moment, fed M1 candles (UTC) plus closed M15 swings."""

    def __init__(self):
        self.m15 = {1: [], -1: []}      # untaken swing lows (1: sell-side, swept for a buy) / highs (-1)
        self.day = {"key": None, "hi": None, "lo": None, "pdh": None, "pdl": None, "asia_hi": None, "asia_lo": None,
                    "asia_key": None, "a_hi": None, "a_lo": None}
        self._m15 = []                  # building M15 candles from M1: [t, h, l]

    def add(self, utc: int, h: float, l: float) -> None:
        ny = ny_clock(utc)
        d = self.day
        tday = (ny - 17 * 3600) // 86400          # New York trading day from 17:00
        if tday != d["key"]:
            if d["key"] is not None:
                d["pdh"], d["pdl"] = d["hi"], d["lo"]
            d["key"], d["hi"], d["lo"] = tday, h, l
        else:
            d["hi"], d["lo"] = max(d["hi"], h), min(d["lo"], l)
        mins = ny % 86400 // 60
        akey = (ny + 4 * 3600) // 86400
        if mins >= 1200 or mins < 120:             # 20:00-02:00 New York: the Asian range builds
            if d["asia_key"] != akey:
                d["asia_key"], d["a_hi"], d["a_lo"] = akey, h, l
            else:
                d["a_hi"], d["a_lo"] = max(d["a_hi"], h), min(d["a_lo"], l)
        elif d["asia_key"] == akey:
            d["asia_hi"], d["asia_lo"] = d["a_hi"], d["a_lo"]
        k = utc - utc % 900
        if self._m15 and self._m15[-1][0] == k:
            b = self._m15[-1]
            b[1], b[2] = max(b[1], h), min(b[2], l)
        else:
            self._m15.append([k, h, l])
            self._pivots()
        for side in (1, -1):                       # taken levels drop out
            self.m15[side] = [x for x in self.m15[side] if (l > x if side == 1 else h < x)]

    def _pivots(self) -> None:
        b = self._m15[:-1]                         # closed M15 candles
        P = 3
        if len(b) < 2 * P + 1:
            return
        c = b[-P - 1]
        win = b[-2 * P - 1:]
        if c[2] == min(x[2] for x in win):
            self.m15[1] = (self.m15[1] + [c[2]])[-20:]
        if c[1] == max(x[1] for x in win):
            self.m15[-1] = (self.m15[-1] + [c[1]])[-20:]
        self._m15 = self._m15[-50:]

    def levels(self, side: int) -> list:
        """(price, name) of the pools a raid for `side` would take: lows for a buy, highs for a sell."""
        d, out = self.day, []
        if side == 1:
            out += [(x, "M15 swing low") for x in self.m15[1]]
            out += [(p, n) for p, n in ((d["asia_lo"], "Asian low"), (d["pdl"], "PDL")) if p is not None]
        else:
            out += [(x, "M15 swing high") for x in self.m15[-1]]
            out += [(p, n) for p, n in ((d["asia_hi"], "Asian high"), (d["pdh"], "PDH")) if p is not None]
        return out


class M1Entries:
    """Feed closed M1 candles (UTC times). `add` returns events: ("raid", ...), ("cisd", ...), ("order", ...)."""

    def __init__(self, killzones: bool = True):
        self.pools = Pools()
        self.t, self.o, self.h, self.l, self.c = [], [], [], [], []
        self.idea = {1: None, -1: None}
        self.killzones = killzones
        self.events: list = []          # recent raids / CISDs / orders for the chart

    def add(self, utc: int, o: float, h: float, l: float, c: float) -> list:
        before = {s: self.pools.levels(s) for s in (1, -1)}       # pools known before this candle
        for arr, x in ((self.t, utc), (self.o, o), (self.h, h), (self.l, l), (self.c, c)):
            arr.append(x)
        self.pools.add(utc, h, l)
        j = len(self.c) - 1
        out = []
        for d in (1, -1):
            out += self._step(d, j, before[d])
        self.events = (self.events + out)[-40:]
        if len(self.c) > 3000:
            for arr in (self.t, self.o, self.h, self.l, self.c):
                del arr[:1000]
            for d in (1, -1):
                if self.idea[d]:
                    for k in ("bar", "cisd_bar"):
                        if self.idea[d].get(k) is not None:
                            self.idea[d][k] -= 1000
        return out

    def _step(self, d: int, j: int, levels: list) -> list:
        o, h, l, c = self.o[j], self.h[j], self.l[j], self.c[j]
        idea, out = self.idea[d], []
        beyond = (l if d == 1 else h)
        if idea and idea["stage"] == "raid":
            if (beyond - idea["ext"]) * d < 0:          # a new extreme: the raid continues
                idea.update(ext=beyond, bar=j, ref=self._ref(d, j))
            elif (c - idea["ref"]) * d > 0 and (c - o) * d > 0:
                gap = self._fvg(d, idea["bar"], j)
                if gap:
                    ce = (gap[0] + gap[1]) / 2
                    idea.update(stage="order", cisd_bar=j, gap=gap, ce=ce)
                    out.append(("cisd", {"dir": d, "time": self.t[j], "price": idea["ref"]}))
                    out.append(("order", {"dir": d, "time": self.t[j], "entry": ce, "ext": idea["ext"],
                                          "gap": gap, "level": idea["level"], "name": idea["name"]}))
                else:
                    self.idea[d] = None
            elif j - idea["bar"] > CISD_WITHIN:
                self.idea[d] = None
        elif idea and idea["stage"] == "order" and j > idea["cisd_bar"]:
            if (beyond - idea["ext"]) * d < 0 or j - idea["cisd_bar"] > FILL_WITHIN:
                self.idea[d] = None
                out.append(("cancel", {"dir": d, "time": self.t[j]}))
        if self.idea[d] is None and (not self.killzones or in_killzone(self.t[j])):
            for lvl, name in levels:
                if (l < lvl < c) if d == 1 else (c < lvl < h):
                    self.idea[d] = {"stage": "raid", "bar": j, "ext": beyond, "ref": self._ref(d, j),
                                    "level": lvl, "name": name}
                    out.append(("raid", {"dir": d, "time": self.t[j], "price": lvl, "name": name,
                                         "ext": l if d == 1 else h}))
                    break
        return out

    def _ref(self, d: int, j: int) -> float:
        """CISD reference: the open of the run of opposite candles that delivered price into the extreme at j."""
        opp = lambda m: (self.c[m] - self.o[m]) * d < 0
        k = j if opp(j) else j - 1
        first = None
        while k >= 0 and opp(k) and j - k < 10:
            first, k = k, k - 1
        if first is not None:
            return self.o[first]
        return max(self.o[j], self.c[j]) if d == 1 else min(self.o[j], self.c[j])

    def _fvg(self, d: int, a: int, j: int):
        """The newest gap in candles a..j for side d, as (top, bottom)."""
        for m in range(j, max(a + 1, j - 12), -1):
            if d == 1 and self.l[m] - self.h[m - 2] >= FVG_MIN:
                return (self.l[m], self.h[m - 2])
            if d == -1 and self.l[m - 2] - self.h[m] >= FVG_MIN:
                return (self.l[m - 2], self.h[m])
        return None

    def take(self, d: int) -> None:
        """The order for side d was filled (or used): forget the idea."""
        self.idea[d] = None


def plan(d: int, entry: float, ext: float, spread: float, tp_r: float = TP_R):
    sl = ext - d * SL_BUFFER + (spread if d == -1 else 0.0)
    risk = (entry - sl) * d
    if risk < MIN_RISK:
        sl, risk = entry - d * MIN_RISK, MIN_RISK
    if risk > MAX_RISK:
        return None
    return {"sl": sl, "tp": entry + d * tp_r * risk, "risk": risk}
