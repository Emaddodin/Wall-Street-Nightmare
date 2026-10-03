"""ICT playbook for gold: every model from the goldictcontents notes, read on every timeframe, the one that fits the
live market picked, and the chart told what to do in plain words.

Pipeline, every closed M1 candle (boom.py calls `Playbook.update`):

  1 Tapes       ictlib.Tape for M1, M5, M15, H1, H4 and D1, with the day's external levels (PDH / PDL, PWH / PWL,
                Asian range, NDOG / NWOG) and the higher timeframe's swings as the external liquidity of the lower one.
  2 Timeframes  each timeframe's own ICT read (`tf_report`): structure (MSS / BOS / CHoCH), dealing range and zone,
                OTE, the nearest internal (FVG / IFVG / BPR) and external (BSL / SSL) liquidity, where it sits in the
                IRL <-> ERL cycle, the latest sweep / CISD / MSS / SMT, and what that timeframe says to do.
  3 Models      each model looks for its own setup on the entry timeframes M1 and M5 (`MODELS`): Silver Bullet,
                Judas swing / AMD, Turtle Soup, MSS + FVG, Unicorn, Breaker, IFVG, OTE, Pulse IRL->ERL and ERL->IRL,
                HRLR -> LRLR, BPR, Order block mean threshold, NDOG / NWOG, SMT reversal and the classic CISD + FVG.
                A setup has an entry (limit at the FVG's 50 %, the breaker's edge, the unicorn overlap, the golden
                zone ...), a stop beyond the sweep, targets at opposing liquidity and the higher timeframe's draw.
  4 Checklist   every setup is graded on the four phases of the notes: higher-timeframe bias and key level,
                premium / discount, killzone / macro / Silver Bullet time, the sweep, the shift (MSS with
                displacement beats a bare CISD), SMT against silver, Kronos' next 30 minutes, reward to risk, news.
  5 Selector    which model fits now: its time window, the market's regime (trend or range), its measured record
                (playbook_backtest.py on a year of gold plus every live setup, scored by `Track`) and whether it has
                a setup armed. The best one drives the BOOM / CRASH calls (boom.py).
  6 Talk        the four phases in sentences, and one line of what to do now.

Nothing here places an order. The models are the notes written as rules; their edge is whatever `Track` and the
backtest measure, and the page shows those numbers next to every model.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import ictclock as ck
from engine import Bars
from ictlib import Tape, ote_zone

TRACK_FILE = Path.home() / ".golddesk" / "playbook_track.json"
STATS_FILE = Path.home() / ".golddesk" / "playbook_stats.json"
ORDER = ("D1", "H4", "H1", "M15", "M5", "M1")
ENTRY_TFS = ("M1", "M5")
HTF_OF = {"M1": "M15", "M5": "H1", "M15": "H4", "H1": "D1"}
FRESH = {"M1": 30, "M5": 12}            # a setup's shift must be this many candles old at most
FILL = {"M1": 30, "M5": 12}             # a limit waits this many candles
HOLD = {"M1": 120, "M5": 72}            # a filled setup ends after this many candles
RISK = {"M1": (0.8, 8.0), "M5": (1.5, 15.0)}
SPREAD = 0.22                           # gold's usual spread; buy limits sit this much above the level
GRADES = ((0.85, "A+"), (0.70, "A"), (0.55, "B"), (0.0, "C"))
KZ = [(n, a, z) for n, a, z in ck.KILLZONES if n not in ("NY Lunch",)]

#        id            name                       windows (NY)                      trend range  kind
MODELS = {
    "silver_bullet": ("Silver Bullet", ck.SILVER_BULLET, 1.0, 0.7, "time"),
    "judas": ("Judas Swing / AMD", (("London", 120, 330), ("NY open", 510, 630)), 0.8, 1.0, "time"),
    "turtle_soup": ("Turtle Soup", KZ, 0.6, 1.0, "reversal"),
    "mss_fvg": ("MSS + FVG", KZ, 0.9, 0.9, "reversal"),
    "unicorn": ("Unicorn (Breaker + FVG)", KZ, 0.9, 0.9, "reversal"),
    "breaker": ("Breaker Block", KZ, 0.8, 0.9, "reversal"),
    "ifvg": ("IFVG flip", KZ, 0.9, 0.8, "reversal"),
    "ote": ("OTE (62-79 %)", None, 1.0, 0.5, "continuation"),
    "pulse": ("Pulse IRL -> ERL", None, 1.0, 0.6, "continuation"),
    "pulse_rev": ("Pulse ERL -> IRL", None, 0.5, 1.0, "reversal"),
    "hrlr": ("HRLR -> LRLR", KZ, 0.7, 1.0, "reversal"),
    "bpr": ("Balanced Price Range", KZ, 0.8, 0.8, "continuation"),
    "ob_mt": ("Order Block (MT)", None, 1.0, 0.4, "continuation"),
    "gap": ("NDOG / NWOG", None, 0.7, 0.9, "reversal"),
    "smt": ("SMT reversal (XAU/XAG)", KZ, 0.6, 1.0, "reversal"),
    "cisd_fvg": ("CISD + FVG (classic)", (("London", 120, 300), ("NY AM", 510, 660)), 0.8, 0.9, "reversal"),
}
WAITING = {
    "silver_bullet": "inside the window: a sweep of a local high / low, then an MSS with displacement toward the draw "
                     "that prints an FVG; enter on the retrace into it",
    "judas": "the London (or NY open) run against the daily bias through the Asian range, then a CISD back",
    "turtle_soup": "a wick through PDH / PDL, the Asian range, a higher-timeframe swing or a $10 level that closes "
                   "back inside, then a CISD",
    "mss_fvg": "a sweep at a higher-timeframe key level, then a body close through the last swing with displacement",
    "unicorn": "an MSS that breaks an order block into a breaker with a fair value gap inside it",
    "breaker": "a sweep, then a close through the opposite order block; enter when price comes back to it",
    "ifvg": "a sweep, then a body close through the opposite fair value gap; enter on its retest",
    "ote": "a leg that broke structure after a sweep; enter 62-70 % back into it",
    "pulse": "price tapping a higher-timeframe gap on the way to its external liquidity, then a CISD on the entry chart",
    "pulse_rev": "a raid on higher-timeframe external liquidity, then a CISD toward the nearest open gap",
    "hrlr": "relative equal lows / highs swept at a key level with a shift; lower highs / higher lows opposite as targets",
    "bpr": "two opposing gaps overlapping; enter at the overlap's 50 % in the newer gap's direction",
    "ob_mt": "a retrace into the order block of the last break with the trend; out on a body close beyond its MT",
    "gap": "price reaching a new day / week opening gap, then a CISD off its edge or 50 %",
    "smt": "gold sweeping a high / low that silver doesn't, then a CISD",
    "cisd_fvg": "a raid on an M15 swing, the Asian range or PDH / PDL in a killzone, then a CISD and an FVG",
}


def _r(x, d=2):
    return round(x, d) if isinstance(x, (int, float)) else x


def grade(score: float) -> str:
    return next(g for lo, g in GRADES if score >= lo)


# ====================================================================== track record
class Track:
    """Outcome of every setup any model armed (live), by model and timeframe, plus the backtest's prior.
    `edge(model)` is the shrunk mean R: (sum of R) / (n + PRIOR_N), so a handful of lucky trades can't crown a model."""

    PRIOR_N = 30
    KEEP_DONE = 3000

    def __init__(self, file: Path | None = TRACK_FILE, stats_file: Path | None = STATS_FILE):
        self.file, self.data = file, {"setups": {}, "done": []}
        self.prior: dict = {}
        try:
            self.data = json.loads(file.read_text())
        except (OSError, ValueError, AttributeError):
            pass
        try:
            self.prior = json.loads(stats_file.read_text()).get("models", {})
        except (OSError, ValueError, AttributeError):
            pass
        self.data.setdefault("setups", {})
        self.data.setdefault("done", [])

    def add(self, s: dict) -> None:
        if s["id"] in self.data["setups"] or any(x["id"] == s["id"] for x in self.data["done"][-400:]):
            return
        self.data["setups"][s["id"]] = {k: s[k] for k in ("id", "model", "tf", "dir", "entry", "sl", "tp1", "t", "grade",
                                                         "order")} | {"stage": "armed", "fill_by": s["fill_by"], "name": s.get("name"),
                                                            "hour": None}
        self._save()

    def step(self, tf: str, b: Bars, spread: float = SPREAD) -> list:
        """Resolve pending setups of timeframe tf with its closed candles. Returns the setups that ended."""
        out, changed = [], False
        for sid, s in list(self.data["setups"].items()):
            if s["tf"] != tf:
                continue
            d = s["dir"]
            for i in range(len(b)):
                t = b.t[i]
                if t <= s.get("seen", s["t"]):
                    continue
                s["seen"] = t
                changed = True
                if s["stage"] == "armed":
                    if s["order"] == "market" or ((b.l[i] <= s["entry"] - spread) if d == 1 else (b.h[i] >= s["entry"])):
                        s["stage"], s["filled"] = "filled", t
                    elif ((b.l[i] < s["sl"]) if d == 1 else (b.h[i] > s["sl"])) or t > s["fill_by"]:
                        s["stage"], s["r"], s["how"] = "missed", None, "not filled"
                        break
                    else:
                        continue
                hit_sl = (b.l[i] <= s["sl"]) if d == 1 else (b.h[i] + spread >= s["sl"])
                hit_tp = (b.h[i] >= s["tp1"]) if d == 1 else (b.l[i] + spread <= s["tp1"])
                risk = abs(s["entry"] - s["sl"]) or 1.0
                if hit_sl:
                    s["stage"], s["r"], s["how"] = "done", -1.0, "stop"
                elif hit_tp:
                    s["stage"], s["r"], s["how"] = "done", round(abs(s["tp1"] - s["entry"]) / risk, 2), "target"
                elif (t - s["filled"]) >= HOLD[tf] * b.sec:
                    s["stage"], s["r"], s["how"] = "done", round((b.c[i] - s["entry"]) * d / risk, 2), "time"
                if s["stage"] == "done":
                    break
            if s["stage"] in ("done", "missed"):
                del self.data["setups"][sid]
                self.data["done"].append(s)
                if len(self.data["done"]) > self.KEEP_DONE:
                    del self.data["done"][:-self.KEEP_DONE]
                out.append(s)
        if changed:
            self._save()
        return out

    def stats(self, model: str, tf: str | None = None) -> dict:
        rs = [x["r"] for x in self.data["done"] if x["model"] == model and x.get("r") is not None
              and (tf is None or x["tf"] == tf)]
        p = self.prior.get(model, {})
        pn, pr = int(p.get("n", 0)), float(p.get("sum_r", 0.0))
        n, s = len(rs) + pn, sum(rs) + pr
        wins = sum(1 for r in rs if r > 0) + int(p.get("wins", 0))
        return {"n_live": len(rs), "n_backtest": pn, "n": n, "win_pct": round(100 * wins / n, 1) if n else None,
                "mean_r": round(s / n, 3) if n else None, "edge": round(s / (n + self.PRIOR_N), 3),
                "net_r": round(s, 2), "pending": sum(1 for x in self.data["setups"].values() if x["model"] == model)}

    def _save(self) -> None:
        if not self.file:
            return
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.file.write_text(json.dumps(self.data))
        except OSError:
            pass


# ====================================================================== context
class Ctx:
    """Everything the models read at one moment."""

    def __init__(self, tapes: dict, utc, levels: dict, desks: dict | None, kronos30: dict | None, smt: dict | None,
                 news: bool, spread: float):
        self.tapes, self.utc, self.lv = tapes, utc, levels
        self.desks, self.kronos30, self.smt, self.news, self.spread = desks or {}, kronos30, smt or {}, news, spread
        m1 = tapes.get("M1")
        self.price = m1.b.c[-1] if m1 and m1.n else None
        self.t = (m1.b.t[-1] + 60) if m1 and m1.n else 0
        self.clock = ck.clock(utc(self.t)) if self.t else {}
        for x in self.clock.get("next", []):
            x["start_t"] = self.t + 60 * x["in_min"]                       # candle clock, for an exact countdown
        self.bias, self.bias_why = self._bias()
        self.regime = self._regime()

    def _bias(self) -> tuple:
        """Higher-timeframe order flow, -1 .. +1: the D1 / H4 / H1 desks (tfdesk.py) weighted 1 / 2 / 2, else the
        tapes' structure."""
        w = {"D1": 1, "H4": 2, "H1": 2}
        if all(k in self.desks for k in w):
            s = sum(w[k] * self.desks[k]["score"] for k in w) / sum(w.values())
            return s, ", ".join(f"{k} {self.desks[k]['label']}" for k in w)
        got = [(k, self.tapes[k].trend) for k in w if k in self.tapes]
        if not got:
            return 0.0, "no higher-timeframe data"
        s = sum(w[k] * v for k, v in got) / sum(w[k] for k, _ in got)
        return s, ", ".join(f"{k} {'up' if v > 0 else 'down' if v < 0 else 'flat'}" for k, v in got)

    def _regime(self) -> dict:
        """Trend or range from M15: efficiency ratio of the last 32 candles (net move / path) and structure agreement."""
        m15 = self.tapes.get("M15")
        if not m15 or m15.n < 40:
            return {"kind": "unknown", "er": None}
        c = m15.b.c
        path = sum(abs(c[i] - c[i - 1]) for i in range(m15.n - 32, m15.n)) or 1e-9
        er = abs(c[-1] - c[-33]) / path
        h1 = self.tapes.get("H1")
        agree = h1 is not None and h1.trend == m15.trend and m15.trend != 0
        kind = "trend" if er >= 0.30 and agree else "range"
        return {"kind": kind, "er": round(er, 2), "dir": m15.trend if kind == "trend" else 0}

    def htf_zones(self, tf: str) -> list:
        """Higher-timeframe key levels / PD arrays for entries on tf: the HTF's and the next one's open gaps, live OBs,
        breakers, IFVGs and BPRs, plus the day's levels."""
        out = []
        for name in dict.fromkeys((HTF_OF.get(tf), "H1", "H4")):
            T = self.tapes.get(name)
            if not T or not T.n:
                continue
            for g in T.open_fvgs():
                out.append((g["bottom"], g["top"], f"{name} {'bullish' if g['dir'] == 1 else 'bearish'} FVG"))
            for z in T.live_obs():
                out.append((z["bottom"], z["top"], f"{name} {'bullish' if z['dir'] == 1 else 'bearish'} {z['kind']}"))
            for x in T.live_ifvgs() + T.live_bprs():
                out.append((x["bottom"], x["top"], f"{name} {'IFVG' if 'from_fvg' in x else 'BPR'}"))
        for k in ("pdh", "pdl", "pwh", "pwl", "nmo"):
            if self.lv.get(k):
                p = self.lv[k]["price"]
                out.append((p, p, self.lv[k]["name"]))
        if self.lv.get("asia"):
            out += [(self.lv["asia"]["high"],) * 2 + ("Asian high",), (self.lv["asia"]["low"],) * 2 + ("Asian low",)]
        for g in self.lv.get("ndog", []):
            out.append((g["bottom"], g["top"], "NDOG"))
        for g in self.lv.get("nwog", []):
            out.append((g["bottom"], g["top"], "NWOG"))
        return out

    def key_level_at(self, tf: str, price: float) -> str | None:
        H = self.tapes.get(HTF_OF.get(tf))
        tol = 0.25 * (H.atr[-1] if H and H.n else 2.0)
        best = None
        for lo, hi, name in self.htf_zones(tf):
            dist = 0.0 if lo - tol <= price <= hi + tol else min(abs(price - lo), abs(price - hi))
            if dist == 0.0:
                best = name
                break
        return best

    def pd_zone(self, tf: str, price: float) -> dict | None:
        T = self.tapes.get(HTF_OF.get(tf))
        return T.dealing_range() if T and T.n else None

    def kronos_vote(self, d: int) -> tuple:
        """(+1 agrees, -1 against, 0 neutral, None off) and why, from the 30-minute Kronos forecast."""
        k = self.kronos30
        if not k or k.get("up_prob") is None or abs((k.get("t") or 0) - self.t) > 900:
            return None, "Kronos 30-min forecast not available"
        up = k["up_prob"]
        p = up if d == 1 else 1 - up
        mv = (k.get("move") or 0.0) * d
        if p >= 0.55 and mv >= 0:
            return 1, f"Kronos 30 min agrees: {'up' if d == 1 else 'down'} {p:.0%}, {mv:+.2f}"
        if p <= 0.45 and mv <= 0:
            return -1, f"Kronos 30 min disagrees: {'up' if d == 1 else 'down'} only {p:.0%}"
        return 0, f"Kronos 30 min undecided ({'up' if d == 1 else 'down'} {p:.0%})"

    def smt_vote(self, tf: str, d: int, since_t: int) -> tuple:
        for name in (tf, HTF_OF.get(tf)):
            s = (self.smt or {}).get(name) if name else None
            if not s:
                continue
            for ev in [s.get("forming")] + list(reversed(s.get("events") or [])):
                if ev and ev.get("time", 0) >= since_t - 10 * 60:
                    return (1 if ev["dir"] == d else -1), (s.get("note") or f"{name} SMT {ev.get('kind')}")
        return None, "no XAU/XAG SMT at this sweep"


# ====================================================================== setups
def _seqs(T: Tape, d: int, age: int) -> list:
    """Recent reversal sequences for side d on tape T, newest first: a sweep, then a shift (MSS / CHoCH / BOS or
    CISD) off the swept extreme."""
    n, out = T.n, []
    for br in reversed(T.breaks):
        if br["i"] < n - age:
            break
        if br["dir"] == d and br["sweep"]:
            out.append({"shift": br, "kind": br["kind"], "sweep": br["sweep"], "i": br["i"], "ext": br["ext"],
                        "ext_i": br["ext_i"], "mss": br["kind"] == "MSS", "disp": br["disp"], "fvg": br["fvg"],
                        "ob": br["leg_ob"]})
    for cs in reversed(T.cisd):
        if cs["i"] < n - age:
            break
        if cs["dir"] != d:
            continue
        sw = next((w for w in reversed(T.sweeps) if w["dir"] == d and w["i"] <= cs["i"] and abs(w["ext_i"] - cs["ext_i"]) <= 3), None)
        if not sw:
            continue
        fvg = next((g for g in reversed(T.fvgs) if g["dir"] == d and cs["ext_i"] < g["i"] <= cs["i"] + 1 and g["born"] <= n - 1), None)
        if any(x["sweep"] is sw for x in out):
            continue
        out.append({"shift": cs, "kind": "CISD", "sweep": sw, "i": cs["i"], "ext": cs["ext"], "ext_i": cs["ext_i"],
                    "mss": False, "disp": any(_disp(T, m, d) for m in range(cs["ext_i"] + 1, cs["i"] + 1)),
                    "fvg": fvg, "ob": T._swing_ob({"i": cs["ext_i"], "dir": -d, "known": cs["i"]})})
    out.sort(key=lambda x: -x["i"])
    return out


def _disp(T: Tape, m: int, d: int) -> bool:
    from ictlib import is_disp
    return is_disp(T.b, m, T.atr, d)


class Builder:
    """Turns an entry zone into a setup with stop, targets and its checklist."""

    def __init__(self, ctx: Ctx, tf: str):
        self.ctx, self.tf = ctx, tf
        self.T = ctx.tapes[tf]
        self.H = ctx.tapes.get(HTF_OF[tf])

    def make(self, model: str, d: int, seq: dict | None, entry: float, zone: tuple, order: str = "limit",
             sl_from: float | None = None, why: list | None = None, tp_hint: float | None = None,
             tp2_hint: float | None = None, invalid: str = "") -> dict | None:
        T, ctx, tf = self.T, self.ctx, self.tf
        a = T.atr[-1] or 1.0
        buf = max(0.30, 0.10 * a)
        ext = sl_from if sl_from is not None else seq["ext"]
        if d == 1:
            entry += ctx.spread if order == "limit" else 0.0          # a buy limit fills on the ask
        sl = ext - d * buf
        risk = (entry - sl) * d
        lo, hi = RISK[tf]
        if risk <= 0:
            return None
        if risk < lo:
            sl, risk = entry - d * lo, lo
        if risk > hi:
            return None
        px = ctx.price
        if order == "limit" and (entry - px) * d > 0.5 * a:              # price already far from the entry: stale side
            return None
        opp = T.erl(d, entry)
        tp1 = next((s["price"] for s in opp if (s["price"] - entry) * d >= 1.5 * risk), entry + d * 2 * risk)
        if tp_hint is not None and (tp_hint - entry) * d >= 1.5 * risk:
            tp1 = tp_hint
        tp2 = None
        if tp2_hint is not None and (tp2_hint - tp1) * d > 0:
            tp2 = tp2_hint
        elif self.H is not None and self.H.n:
            far = self.H.erl(d, tp1)
            tp2 = far[0]["price"] if far else None
        if tp2 is None or (tp2 - tp1) * d <= 0:
            tp2 = entry + d * 3 * risk
        t_shift = T.b.t[seq["i"]] + T.b.sec if seq else ctx.t
        s = {"model": model, "name": MODELS[model][0], "tf": tf, "dir": d, "side": "BUY" if d == 1 else "SELL",
             "boom": "BOOM" if d == 1 else "CRASH", "order": order, "t": t_shift,
             "entry": _r(entry), "sl": _r(sl), "tp1": _r(tp1), "tp2": _r(tp2), "risk": _r(risk),
             "rr1": _r((tp1 - entry) * d / risk), "rr2": _r((tp2 - entry) * d / risk),
             "zone": {"top": _r(max(zone)), "bottom": _r(min(zone))}, "why": list(why or []),
             "invalid_if": invalid or f"a {tf} body closes beyond {_r(sl)}",
             "fill_by": t_shift + FILL[tf] * T.b.sec if order == "limit" else t_shift + T.b.sec}
        if seq:
            sw = seq["sweep"]
            s["sweep"] = {"kind": sw["kind"], "level": _r(sw["level"]), "ext": _r(sw["ext"]), "time": T.b.t[sw["i"]]}
            s["shift"] = {"kind": seq["kind"], "time": T.b.t[seq["i"]],
                          "level": _r(seq["shift"].get("level", seq["shift"].get("ref")))}
        s["id"] = f"{model}:{tf}:{d}:{t_shift}"
        self._status(s)
        self._checklist(s, seq)
        return s

    def _status(self, s: dict) -> None:
        """armed (waiting at the entry) / filled / invalid / expired, from the candles after the shift."""
        T, d = self.T, s["dir"]
        st = "armed" if s["order"] == "limit" else "filled"
        for i in range(T.n):
            t = T.b.t[i]
            if t < s["t"]:
                continue
            if st == "armed":
                if (T.b.l[i] <= s["entry"] - self.ctx.spread) if d == 1 else (T.b.h[i] >= s["entry"]):
                    st = "filled"
                elif (T.b.c[i] - s["sl"]) * d < 0 or t >= s["fill_by"]:
                    st = "expired" if t >= s["fill_by"] else "invalid"
                    break
            if st == "filled" and ((T.b.l[i] <= s["sl"]) if d == 1 else (T.b.h[i] >= s["sl"])):
                st = "stopped"
                break
            if st == "filled" and ((T.b.h[i] >= s["tp1"]) if d == 1 else (T.b.l[i] <= s["tp1"])):
                st = "target"
                break
        s["status"] = st

    def _checklist(self, s: dict, seq: dict | None) -> None:
        ctx, tf, d = self.ctx, self.tf, s["dir"]
        checks = []

        def add(key, label, ok, w, required=False):
            checks.append({"key": key, "label": label, "ok": ok, "weight": w, "required": required})

        # phase 1: higher-timeframe context
        b = ctx.bias
        add("bias", f"Higher timeframes ({ctx.bias_why}) {'agree' if b * d > 0.15 else 'disagree' if b * d < -0.15 else 'are mixed'}",
            True if b * d > 0.15 else (False if b * d < -0.15 else None), 3, required=MODELS[s["model"]][4] == "continuation")
        ref = seq["ext"] if seq else s["entry"]
        key = ctx.key_level_at(tf, ref)
        if seq and seq["sweep"]["kind"] and not seq["sweep"]["kind"].startswith(tf):
            key = key or seq["sweep"]["kind"]
        add("key", f"At a higher-timeframe key level ({key})" if key else "No higher-timeframe key level here", bool(key), 2)
        pdz = ctx.pd_zone(tf, s["entry"])
        if pdz:
            good = (s["entry"] < pdz["eq"]) if d == 1 else (s["entry"] > pdz["eq"])
            add("pd", f"Entry in {'discount' if s['entry'] < pdz['eq'] else 'premium'} of the {HTF_OF[tf]} range "
                      f"({pdz['low']:.2f}-{pdz['high']:.2f}, 50 % {pdz['eq']:.2f})", good, 2)
        # phase 2: time
        c = ctx.clock
        when = c.get("silver_bullet") or c.get("macro") or c.get("killzone")
        add("time", f"Time: {when}" if when and not c.get("lunch") else
            ("Time: NY lunch, avoid" if c.get("lunch") else "Time: outside the killzones"),
            bool(when) and not c.get("lunch"), 2)
        # phase 3: lower-timeframe validation
        if seq:
            add("sweep", f"Swept {seq['sweep']['kind']} at {seq['sweep']['level']:.2f}", True, 2)
            add("shift", "MSS with displacement and an FVG" if seq["mss"] else
                (f"{seq['kind']} {'with' if seq['disp'] else 'without'} displacement"), True if seq["mss"] else (None if seq["disp"] else False), 2)
            sv, why = ctx.smt_vote(tf, d, s["sweep"]["time"])
            add("smt", why, None if sv is None else sv > 0, 1.5)
        kv, kwhy = ctx.kronos_vote(d)
        add("kronos", kwhy, None if kv is None or kv == 0 else kv > 0, 2)
        # phase 4: entry and risk
        add("rr", f"Reward to risk {s['rr1']:.1f} to the first target, {s['rr2']:.1f} to the draw", s["rr2"] >= 2.0, 1)
        if ctx.news:
            add("news", "High-impact USD news within 15 minutes: WAIT", False, 5, required=True)
        earned = sum(x["weight"] for x in checks if x["ok"] is True)
        lost = sum(x["weight"] for x in checks if x["ok"] is False)
        half = sum(x["weight"] * 0.5 for x in checks if x["ok"] is None)
        tot = earned + lost + sum(x["weight"] for x in checks if x["ok"] is None)
        score = (earned + half) / tot if tot else 0.0
        if any(x["required"] and x["ok"] is False for x in checks):
            score = min(score, 0.5)
        s["checks"], s["score"], s["grade"] = checks, round(score, 3), grade(score)
        s["kronos"] = kv


def _entry_fvg(seq: dict, T: Tape, d: int):
    g = seq.get("fvg") or next((g for g in reversed(T.fvgs) if g["dir"] == d and seq["ext_i"] < g["i"] <= seq["i"] + 1), None)
    return g


def find(model: str, ctx: Ctx, tf: str) -> dict | None:
    """The newest setup of `model` on entry timeframe tf, or None."""
    T = ctx.tapes.get(tf)
    if not T or T.n < 60:
        return None
    B = Builder(ctx, tf)
    age = FRESH[tf]
    utc = ctx.utc
    best = None
    for d in (1, -1):
        s = _find_side(model, ctx, tf, T, B, d, age, utc)
        if s and (best is None or s["t"] > best["t"] or (s["t"] == best["t"] and s["score"] > best["score"])):
            best = s
    return best


def _find_side(model, ctx, tf, T, B, d, age, utc):
    seqs = _seqs(T, d, age)
    side = "up" if d == 1 else "down"
    if model in ("mss_fvg", "silver_bullet", "hrlr", "smt", "cisd_fvg", "judas", "turtle_soup"):
        for q in seqs:
            sw = q["sweep"]
            g = _entry_fvg(q, T, d)
            tsw, tsh = utc(T.b.t[sw["i"]]), utc(T.b.t[q["i"]])
            if model == "mss_fvg" and not (q["mss"] and g):
                continue
            if model == "silver_bullet" and not (g and q["kind"] in ("MSS", "CHoCH", "BOS") and q["disp"]
                                                 and ck.in_window(tsw, ck.SILVER_BULLET) and ck.in_window(tsh, ck.SILVER_BULLET)):
                continue
            if model == "hrlr":
                if not (sw["eq"] and q["kind"] in ("MSS", "CHoCH", "BOS")):
                    continue
                lr = _lrlr(T, d, q["ext_i"])
                if len(lr) < 2:
                    continue
            if model == "cisd_fvg" and not (q["kind"] == "CISD" and g and sw["kind"] and not sw["kind"].startswith(tf)
                                            and ck.in_window(tsw, MODELS["cisd_fvg"][1])):
                continue
            if model == "judas":
                if not (sw["kind"] in ("Asian low", "Asian high") and ck.in_window(tsw, MODELS["judas"][1])):
                    continue
                if ctx.bias * d < 0:
                    continue
            if model == "turtle_soup":
                if not (sw["kind"] and (not sw["kind"].startswith(tf) or _round_number(sw["level"]))):
                    continue
            if model == "smt":
                sv, _ = ctx.smt_vote(tf, d, T.b.t[sw["i"]])
                if sv != 1:
                    continue
            why = [f"swept {sw['kind']} {sw['level']:.2f}", f"{q['kind']} {side}"]
            tp_hint = tp2 = None
            if model == "judas" and ctx.lv.get("asia"):
                tp_hint = ctx.lv["asia"]["high" if d == 1 else "low"]
                why.insert(0, f"Judas swing against the Asian range in {ck.clock(tsw)['killzone'] or 'the open'}")
            if model == "turtle_soup":
                H = ctx.tapes.get(HTF_OF[tf])
                dr = H.dealing_range() if H and H.n else None
                if dr:
                    tp2 = dr["high"] if d == 1 else dr["low"]
            if model == "hrlr":
                lr = _lrlr(T, d, q["ext_i"])
                tp_hint = lr[0]
                tp2 = max(lr) if d == 1 else min(lr)
                why.append(f"HRLR: relative equal {'lows' if d == 1 else 'highs'} swept; LRLR targets {', '.join(f'{x:.2f}' for x in lr[:3])}")
            if g:
                entry, zone = g["ce"], (g["top"], g["bottom"])
                why.append(f"limit at the FVG 50 % (CE) {g['ce']:.2f}")
                inval = f"a {tf} body closes beyond the FVG's CE, or beyond {q['ext']:.2f}"
            else:
                ref = q["shift"].get("ref", q["shift"].get("level"))
                entry, zone = ref, (ref, ref)
                why.append(f"limit at the CISD level {ref:.2f}")
                inval = f"price trades beyond the sweep {q['ext']:.2f}"
            return B.make(model, d, q, entry, zone, why=why, tp_hint=tp_hint, tp2_hint=tp2, invalid=inval)
        return None
    if model in ("unicorn", "breaker"):
        for q in seqs:
            if q["kind"] == "CISD":
                continue
            bbs = [z for z in T.obs if z["kind"] == "BB" and z["dir"] == d and q["ext_i"] - 2 <= z["born"] <= q["i"] + 1]
            for bb in reversed(bbs):
                if model == "unicorn":
                    g = next((g for g in reversed(T.fvgs) if g["dir"] == d and q["ext_i"] < g["i"] <= q["i"] + 1
                              and min(g["top"], bb["top"]) > max(g["bottom"], bb["bottom"])), None)
                    if not g:
                        continue
                    top, bot = min(g["top"], bb["top"]), max(g["bottom"], bb["bottom"])
                    return B.make(model, d, q, (top + bot) / 2, (top, bot), why=[
                        f"swept {q['sweep']['kind']} {q['sweep']['level']:.2f}", f"{q['kind']} {side} broke an order block",
                        f"breaker {bb['bottom']:.2f}-{bb['top']:.2f} overlaps the FVG {g['bottom']:.2f}-{g['top']:.2f}",
                        f"limit in the overlap {bot:.2f}-{top:.2f}"],
                        invalid=f"a {tf} body closes through both the breaker MT {bb['mt']:.2f} and the FVG CE {g['ce']:.2f}")
                edge = bb["top"] if d == 1 else bb["bottom"]
                return B.make(model, d, q, edge, (bb["top"], bb["bottom"]), why=[
                    f"swept {q['sweep']['kind']} {q['sweep']['level']:.2f}", f"{q['kind']} {side}: the order block failed",
                    f"limit at the breaker's edge {edge:.2f}"],
                    invalid=f"a {tf} body closes beyond the breaker's far side {(bb['bottom'] if d == 1 else bb['top']):.2f}")
        return None
    if model == "ifvg":
        for w in reversed(T.sweeps):
            if w["i"] < T.n - 3 * age:
                break
            if w["dir"] != d:
                continue
            x = next((x for x in T.ifvgs if x["dir"] == d and w["i"] - 1 <= x["born"] <= w["i"] + age and x["end"] is None), None)
            if not x:
                continue
            edge = x["top"] if d == 1 else x["bottom"]
            q = {"sweep": w, "kind": "IFVG", "i": x["born"], "ext": w["ext"], "ext_i": w["ext_i"], "mss": False,
                 "disp": _disp(T, x["born"], d), "shift": {"level": edge}}
            return B.make(model, d, q, edge, (x["top"], x["bottom"]), why=[
                f"swept {w['kind']} {w['level']:.2f}", f"the {'bearish' if d == 1 else 'bullish'} FVG {x['bottom']:.2f}-{x['top']:.2f} "
                f"was closed through: now an IFVG {'support' if d == 1 else 'resistance'}", f"limit at its edge {edge:.2f}"],
                invalid=f"a {tf} body closes back through {(x['bottom'] if d == 1 else x['top']):.2f}")
        return None
    if model == "ote":
        for q in seqs:
            if q["kind"] == "CISD":
                continue
            rng = range(q["ext_i"], T.n)
            k = max(rng, key=lambda m: T.b.h[m]) if d == 1 else min(rng, key=lambda m: T.b.l[m])
            tip = T.b.h[k] if d == 1 else T.b.l[k]
            z = ote_zone(d, q["ext"], tip)
            if abs(tip - q["ext"]) < 1.5 * T.atr[-1]:
                continue
            entry, zone, note = z["62"], z["golden"], "limit at 0.62, add at 0.70; 0.79 only in extreme"
            for p in T.open_fvgs(d) + T.live_obs(d):                                # a PD array inside OTE: enter there
                mid = p.get("ce", p.get("mt"))
                if mid is not None and z["ote"][0] <= mid <= z["ote"][1]:
                    entry, note = mid, f"PD array inside OTE: limit at its 50 % {mid:.2f}"
                    break
            return B.make(model, d, q, entry, tuple(z["ote"]), why=[
                f"swept {q['sweep']['kind']} {q['sweep']['level']:.2f}", f"{q['kind']} {side}",
                f"leg {q['ext']:.2f} -> {tip:.2f}, OTE {z['ote'][0]:.2f}-{z['ote'][1]:.2f}", note],
                tp_hint=tip, tp2_hint=z["ext_-0.5"], invalid=f"price closes beyond the leg's start {q['ext']:.2f}")
        return None
    if model in ("pulse", "pulse_rev", "gap"):
        H = ctx.tapes.get(HTF_OF[tf])
        if not H or H.n < 20:
            return None
        for cs in reversed(T.cisd):
            if cs["i"] < T.n - age:
                break
            if cs["dir"] != d:
                continue
            lo = cs["ext"]
            if model == "pulse":
                zones = [g for g in H.open_fvgs(d) + H.live_bprs(d) + H.live_ifvgs(d)
                         if g["bottom"] - 0.1 * H.atr[-1] <= lo <= g["top"] + 0.1 * H.atr[-1]]
                if not zones:
                    continue
                erl = H.erl(d, cs["ext"])
                if not erl:
                    continue
                z = zones[-1]
                tgt = erl[0]["price"]
                why = [f"{HTF_OF[tf]} IRL tapped: {z['bottom']:.2f}-{z['top']:.2f}", f"{tf} CISD {side} at {cs['ref']:.2f}",
                       f"target {HTF_OF[tf]} ERL {tgt:.2f}"]
            elif model == "pulse_rev":
                sw = next((w for w in reversed(H.sweeps) if w["dir"] == d and H.n - w["i"] <= 4), None)
                if not sw:
                    continue
                irl = H.irl(d, cs["ext"])
                if not irl:
                    continue
                tgt = irl[0]["ce"]
                z = {"top": cs["ref"], "bottom": cs["ref"]}
                why = [f"{HTF_OF[tf]} ERL raided: {sw['kind']} {sw['level']:.2f}", f"{tf} CISD {side} at {cs['ref']:.2f}",
                       f"target {HTF_OF[tf]} IRL ({irl[0]['kind']} 50 % {tgt:.2f})"]
            else:
                gaps = [(g, "NDOG") for g in ctx.lv.get("ndog", [])] + [(g, "NWOG") for g in ctx.lv.get("nwog", [])]
                hit = next(((g, k) for g, k in gaps if g["size"] > 0.2 and g["bottom"] - 0.3 <= lo <= g["top"] + 0.3), None)
                if not hit or ctx.bias * d < -0.15:
                    continue
                z, k = hit
                erl = T.erl(d, cs["ext"])
                tgt = erl[0]["price"] if erl else None
                why = [f"price reached the {k} {z['bottom']:.2f}-{z['top']:.2f} (50 % {z['ce']:.2f})",
                       f"{tf} CISD {side} at {cs['ref']:.2f}"]
            q = {"sweep": {"kind": "IRL tap" if model == "pulse" else why[0], "level": lo, "ext": lo, "i": cs["ext_i"],
                           "ext_i": cs["ext_i"]},
                 "kind": "CISD", "i": cs["i"], "ext": cs["ext"], "ext_i": cs["ext_i"], "mss": False,
                 "disp": _disp(T, cs["i"], d), "shift": cs}
            return B.make(model, d, q, T.b.c[cs["i"]], (z["top"], z["bottom"]), order="market", why=why,
                          tp_hint=tgt, invalid=f"price trades beyond {cs['ext']:.2f}")
        return None
    if model == "bpr":
        for x in reversed(T.bprs):
            if x["born"] < T.n - 3 * age:
                break
            if x["dir"] != d or x["end"] is not None or ctx.bias * d < 0:
                continue
            lo_i = max(0, x["born"] - 15)
            ext_i = min(range(lo_i, x["born"] + 1), key=lambda m: T.b.l[m]) if d == 1 else max(range(lo_i, x["born"] + 1), key=lambda m: T.b.h[m])
            ext = T.b.l[ext_i] if d == 1 else T.b.h[ext_i]
            q = {"sweep": {"kind": "", "level": ext, "ext": ext, "i": ext_i, "ext_i": ext_i}, "kind": "BPR", "i": x["born"],
                 "ext": ext, "ext_i": ext_i, "mss": False, "disp": True, "shift": {"level": x["ce"]}}
            s = B.make(model, d, q, x["ce"], (x["top"], x["bottom"]), why=[
                f"balanced price range {x['bottom']:.2f}-{x['top']:.2f}: two opposing gaps overlap",
                f"newer gap {'up' if d == 1 else 'down'}, with the higher timeframes", f"limit at its 50 % {x['ce']:.2f}"],
                invalid=f"a {tf} body closes outside the range, beyond {(x['bottom'] if d == 1 else x['top']):.2f}")
            if s:
                s["checks"] = [c for c in s["checks"] if c["key"] != "sweep"]
            return s
        return None
    if model == "ob_mt":
        if ctx.bias * d <= 0.15:
            return None
        for br in reversed(T.breaks):
            if br["i"] < T.n - 3 * age:
                break
            if br["dir"] != d or br["kind"] == "CHoCH" or not br["leg_ob"]:
                continue
            ob = br["leg_ob"]
            edge = ob["top"] if d == 1 else ob["bottom"]
            q = {"sweep": {"kind": "", "level": br["ext"], "ext": ob["bottom"] if d == 1 else ob["top"], "i": br["ext_i"],
                           "ext_i": br["ext_i"]}, "kind": br["kind"], "i": br["i"], "ext": ob["bottom"] if d == 1 else ob["top"],
                 "ext_i": br["ext_i"], "mss": br["kind"] == "MSS", "disp": br["disp"], "shift": br}
            s = B.make(model, d, q, edge, (ob["top"], ob["bottom"]), why=[
                f"{br['kind']} {side} with the higher timeframes", f"order block {ob['bottom']:.2f}-{ob['top']:.2f}, MT {ob['mt']:.2f}",
                f"limit at its edge {edge:.2f}"], invalid=f"a {tf} body closes beyond the MT {ob['mt']:.2f}")
            if s:
                s["checks"] = [c for c in s["checks"] if c["key"] != "sweep"]
            return s
        return None
    return None


def _lrlr(T: Tape, d: int, since: int) -> list:
    """Low-resistance liquidity on the far side: for a buy, the falling swing highs (lower highs) before `since`
    that are still untaken, nearest first."""
    hs = [s for s in T.swings if s["dir"] == d and s["i"] < since][-6:]
    seq = []
    for s in reversed(hs):
        if not seq or (s["price"] - seq[-1]) * d > 0:
            seq.append(s["price"])
        else:
            break
    px = T.b.c[-1]
    return [p for p in seq if (p - px) * d > 0]


def _round_number(p: float, step: float = 10.0) -> bool:
    return abs(p - round(p / step) * step) <= 0.6


# ====================================================================== timeframe reports
ROLE = {"D1": "Bias", "H4": "Narrative", "H1": "Draw on liquidity", "M15": "Setup", "M5": "Confirmation", "M1": "Trigger"}


def tf_report(name: str, T: Tape, ctx: Ctx) -> dict:
    """One timeframe's own ICT read: what its candles alone say, and what to do about it on that timeframe."""
    b, px = T.b, ctx.price
    n = T.n
    br = T.breaks[-1] if T.breaks else None
    dr = T.dealing_range()
    up_erl, dn_erl = T.erl(1, px), T.erl(-1, px)
    up_irl, dn_irl = T.irl(1, px), T.irl(-1, px)
    sw = T.sweeps[-1] if T.sweeps else None
    cs = T.cisd[-1] if T.cisd else None
    # IRL <-> ERL: the last thing that happened decides what's next
    last_erl = sw["i"] if sw else -1
    tapped = -1
    for g in T.fvgs[-40:]:
        if g["end"] is not None and g["state"] == "filled":
            tapped = max(tapped, g["end"])
    if last_erl > tapped and sw:
        nxt = dn_irl if sw["dir"] == 1 else up_irl
        nxt = [z for z in (up_irl + dn_irl) if (z["ce"] - px) * sw["dir"] > 0] or nxt
        phase = {"from": "ERL", "to": "IRL", "text": f"took {sw['kind']} {sw['level']:.2f}: expect a move into internal "
                                                      f"liquidity" + (f" ({nxt[0]['kind']} 50 % {nxt[0]['ce']:.2f})" if nxt else "")}
    else:
        tgt = (up_erl if T.trend >= 0 else dn_erl)
        phase = {"from": "IRL", "to": "ERL", "text": "internal liquidity rebalanced: draw to external liquidity" +
                                                     (f" {tgt[0]['price']:.2f}" if tgt else "")}
    zone = None
    if dr:
        zone = {"high": _r(dr["high"]), "low": _r(dr["low"]), "eq": _r(dr["eq"]), "zone": dr["zone"],
                "pos": dr["pos"], "ote": [_r(x) for x in ote_zone(dr["leg"], dr["low"] if dr["leg"] == 1 else dr["high"],
                                                                      dr["high"] if dr["leg"] == 1 else dr["low"])["ote"]]}
    trend = T.trend
    desk = ctx.desks.get(name) or {}
    smt = (ctx.smt or {}).get(name) or {}
    do = _todo(name, trend, dr, ctx, T)
    since = lambda i: f"{(n - 1 - i)} candles ago" if i is not None else ""
    return {
        "tf": name, "role": ROLE[name], "trend": trend, "label": {1: "bullish", -1: "bearish", 0: "flat"}[trend],
        "last_break": {"kind": br["kind"], "dir": br["dir"], "level": _r(br["level"]), "time": b.t[br["i"]],
                       "ago": since(br["i"]), "disp": br["disp"]} if br else None,
        "range": zone,
        "erl": {"above": [_r(s["price"]) for s in up_erl[:3]], "below": [_r(s["price"]) for s in dn_erl[:3]]},
        "irl": {"above": [{"kind": z["kind"], "top": _r(z["top"]), "bottom": _r(z["bottom"]), "ce": _r(z["ce"])} for z in up_irl[:2]],
                "below": [{"kind": z["kind"], "top": _r(z["top"]), "bottom": _r(z["bottom"]), "ce": _r(z["ce"])} for z in dn_irl[:2]]},
        "phase": phase,
        "sweep": {"kind": sw["kind"], "dir": sw["dir"], "level": _r(sw["level"]), "time": b.t[sw["i"]], "ago": since(sw["i"])} if sw else None,
        "cisd": {"dir": cs["dir"], "ref": _r(cs["ref"]), "time": b.t[cs["i"]], "ago": since(cs["i"])} if cs else None,
        "obs": [{"kind": z["kind"], "dir": z["dir"], "top": _r(z["top"]), "bottom": _r(z["bottom"]), "mt": _r(z["mt"])}
                for z in T.live_obs()[-4:]],
        "smt": {"state": smt.get("state"), "note": smt.get("note")} if smt else None,
        "desk": {"score": desk.get("score"), "label": desk.get("label"), "why": [w["text"] for w in desk.get("why", [])][:3]} if desk else None,
        "atr": _r(T.atr[-1]), "do": do,
    }


def _todo(name: str, trend: int, dr: dict | None, ctx: Ctx, T: Tape) -> str:
    side = "longs" if trend > 0 else "shorts"
    if name in ("D1", "H4"):
        if not trend:
            return "No clear bias here: let H1 / M15 lead and take quick targets."
        z = dr["zone"] if dr else None
        if z and ((trend > 0 and z == "discount") or (trend < 0 and z == "premium")):
            return f"Bias {('bullish' if trend > 0 else 'bearish')} and price is in {z}: favour {side} from lower-timeframe setups."
        return f"Bias {('bullish' if trend > 0 else 'bearish')} but price is in {z or 'the middle'}: wait for a pullback into " \
               f"{'discount' if trend > 0 else 'premium'} before {side}."
    if name == "H1":
        up, dn = T.erl(1, ctx.price), T.erl(-1, ctx.price)
        a = T.atr[-1] or 1
        if up and (not dn or up[0]["price"] - ctx.price < ctx.price - dn[0]["price"]):
            return f"Draw on liquidity above: buy-side {up[0]['price']:.2f} ({(up[0]['price'] - ctx.price) / a:.1f} ATR)."
        if dn:
            return f"Draw on liquidity below: sell-side {dn[0]['price']:.2f} ({(ctx.price - dn[0]['price']) / a:.1f} ATR)."
        return "No untaken H1 liquidity close by."
    if name == "M15":
        sw = T.sweeps[-1] if T.sweeps else None
        if sw and T.n - sw["i"] <= 6:
            return f"Setup forming: {sw['kind']} {sw['level']:.2f} swept {T.n - 1 - sw['i']} candles ago; drop to M1 / M5 for the shift."
        return "No M15 setup yet: watch the nearest gap and swing levels for a sweep."
    sw = T.sweeps[-1] if T.sweeps else None
    cs = T.cisd[-1] if T.cisd else None
    if cs and T.n - cs["i"] <= 5:
        return f"{name} CISD {'up' if cs['dir'] == 1 else 'down'} {T.n - 1 - cs['i']} candles ago: entry window open on the retrace."
    if sw and T.n - sw["i"] <= 5:
        return f"{name} swept {sw['kind']} {sw['level']:.2f}: wait for a CISD / MSS before entering."
    return f"No {name} trigger: wait for a sweep, then a shift."


# ====================================================================== the playbook
class Playbook:
    def __init__(self, track: Track | None = None):
        self.track = track if track is not None else Track()
        self.tapes: dict = {}
        self._tape_key: dict = {}
        self.state: dict | None = None
        self.setups: list = []
        self.new: list = []           # setups first seen on the last update
        self.best: dict | None = None
        self.error: str | None = None
        self.seen: dict = {}          # setup id -> shift time, so each setup is scored once

    def update(self, bars: dict, utc, desks: dict | None = None, kronos30: dict | None = None, smt: dict | None = None,
               news: bool = False, spread: float = SPREAD, market_open: bool = True) -> dict | None:
        """bars: closed candles by timeframe (M1 M5 M15 H1 H4 D1). Returns state.ict."""
        t0 = time.time()
        h1, m5 = bars.get("H1"), bars.get("M5")
        lv = ck.levels(h1, m5, utc) if h1 is not None else {}
        for name in ORDER:
            b = bars.get(name)
            if b is None or len(b) < (12 if name == "D1" else 30):
                continue
            key = (b.t[-1], len(b), name)
            if self._tape_key.get(name) == key:
                continue
            ext = []
            up = HTF_OF.get(name)
            if up and up in self.tapes:                                 # the higher timeframe's swings are this one's ERL
                U = self.tapes[up]
                for s in U.swings[-20:]:
                    ext.append({"price": s["price"], "dir": s["dir"], "name": f"{up} swing {'high' if s['dir'] == 1 else 'low'}",
                                "from": U.b.t[s["known"]] + U.b.sec})
            if name in ("M1", "M5", "M15"):
                ext = ck.external_levels(lv, ext)
            self.tapes[name] = Tape(b, name, external=ext)
            self._tape_key[name] = key
        ctx = Ctx(self.tapes, utc, lv, desks, kronos30, smt, news, spread)
        if ctx.price is None:
            return None
        setups, new = [], []
        for tf in ENTRY_TFS:
            if tf not in self.tapes:
                continue
            for m in MODELS:
                try:
                    s = find(m, ctx, tf)
                except Exception as e:                                  # one model's bug never hides the others
                    self.error = f"{m} on {tf}: {e}"
                    continue
                if not s:
                    continue
                setups.append(s)
                if s["id"] not in self.seen:                            # first time seen: score it whatever happens
                    self.seen[s["id"]] = s["t"]                          # next (a fast fill is still a trade)
                    self.track.add(s)
                    if s["status"] == "armed":
                        new.append(s)
        for tf in ENTRY_TFS:
            if tf in bars and bars[tf] is not None and len(bars[tf]):
                self.track.step(tf, bars[tf], spread)
        self.setups, self.new = setups, new
        if len(self.seen) > 5000:
            cut = sorted(self.seen.values())[-2000]
            self.seen = {k: v for k, v in self.seen.items() if v >= cut}
        reports = {name: tf_report(name, self.tapes[name], ctx) for name in ORDER if name in self.tapes}
        ranking = self._rank(ctx, setups)
        self.best = ranking[0] if ranking else None
        talk = narrate(ctx, reports, ranking, setups, market_open)
        self.state = {
            "t": ctx.t, "price": _r(ctx.price), "clock": ctx.clock, "bias": round(ctx.bias, 2), "bias_why": ctx.bias_why,
            "regime": ctx.regime, "levels": _levels_out(lv), "timeframes": reports,
            "setups": sorted(setups, key=lambda s: (s["status"] != "armed", -s["score"]))[:12],
            "ranking": ranking, "best": self.best, "talk": talk, "news": news,
            "kronos30": {k: (kronos30 or {}).get(k) for k in ("up_prob", "move", "call", "dir", "confidence")} if kronos30 else None,
            "ms": round(1000 * (time.time() - t0)), "error": self.error, "proven": False,
        }
        return self.state

    def _rank(self, ctx: Ctx, setups: list) -> list:
        """Which model fits the live market: time window x regime x measured record x a setup on the board."""
        out = []
        utc_now = ctx.utc(ctx.t)
        for m, (name, windows, f_trend, f_range, kind) in MODELS.items():
            if windows is None:
                fit_t = 1.0
            elif ck.in_window(utc_now, windows):
                fit_t = 1.0
            else:
                fit_t = 0.0 if m in ("silver_bullet", "judas") else 0.35
            reg = ctx.regime.get("kind")
            fit_r = f_trend if reg == "trend" else (f_range if reg == "range" else 0.8)
            if ctx.clock.get("lunch"):
                fit_t *= 0.5
            st = self.track.stats(m)
            edge = st["edge"]
            edge_score = max(0.0, min(1.0, 0.5 + edge))
            mine = [s for s in setups if s["model"] == m and s["status"] in ("armed", "filled")]
            top = max(mine, key=lambda s: s["score"]) if mine else None
            present = top["score"] if top else 0.0
            fit = fit_t * fit_r
            score = 0.40 * fit + 0.25 * edge_score + 0.35 * present
            if ctx.news:
                score *= 0.5
            why = []
            why.append("in its time window" if fit_t == 1.0 and windows else ("any time" if windows is None else "outside its window"))
            why.append(f"{reg} market suits it" if fit_r >= 0.9 else f"{reg} market is not its best")
            why.append(f"record {st['mean_r']:+.2f} R over {st['n']} setups" if st["n"] else "no record yet")
            out.append({"model": m, "name": name, "score": round(score, 3), "fit": round(fit, 2),
                        "edge": edge, "stats": st, "setup": top, "why": why,
                        "waiting_for": None if top else WAITING[m]})
        out.sort(key=lambda x: -x["score"])
        return out


def _levels_out(lv: dict) -> dict:
    out = {}
    for k in ("pdh", "pdl", "pwh", "pwl", "nmo"):
        if lv.get(k):
            out[k] = _r(lv[k]["price"])
    if lv.get("asia"):
        out["asia_high"], out["asia_low"] = _r(lv["asia"]["high"]), _r(lv["asia"]["low"])
    out["ndog"] = [dict({k: _r(v) for k, v in g.items() if k != "from"}, time=g["from"]) for g in lv.get("ndog", [])]
    out["nwog"] = [dict({k: _r(v) for k, v in g.items() if k != "from"}, time=g["from"]) for g in lv.get("nwog", [])]
    return out


# ====================================================================== talk
def narrate(ctx: Ctx, rep: dict, ranking: list, setups: list, market_open: bool = True) -> dict:
    """The chart talking: the four phases of the gold notes, then one line of what to do now."""
    px = ctx.price
    lines = []
    b = ctx.bias
    dirw = "bullish" if b > 0.15 else ("bearish" if b < -0.15 else "mixed")
    h1 = rep.get("H1", {})
    d1, h4 = rep.get("D1", {}), rep.get("H4", {})
    p1 = f"Higher timeframes are {dirw} ({ctx.bias_why})."
    if h4.get("range"):
        p1 += f" H4 price is in {h4['range']['zone']} ({h4['range']['pos']:.0%} of {h4['range']['low']:.2f}-{h4['range']['high']:.2f})."
    if h1.get("do"):
        p1 += " " + h1["do"]
    lines.append({"phase": "1 Context", "text": p1})
    c = ctx.clock
    now = c.get("silver_bullet") or c.get("macro") or c.get("killzone")
    nxt = (c.get("next") or [None])[0]
    p2 = (f"New York {c.get('ny')}: {now} is on." if now else f"New York {c.get('ny')}: no killzone now.")
    if c.get("lunch"):
        p2 += " NY lunch: setups fail more, stand aside."
    if nxt:
        p2 += f" Next: {nxt['name']} at {nxt['start']} (in {nxt['in_min']} min)."
    if ctx.news:
        p2 += " High-impact USD news close: WAIT."
    if c.get("amd"):
        p2 += f" Power of 3: {c['amd']}."
    lines.append({"phase": "2 Time", "text": p2})
    m15, m5, m1 = rep.get("M15", {}), rep.get("M5", {}), rep.get("M1", {})
    p3 = " ".join(x for x in (m15.get("do"), m5.get("do"), m1.get("do")) if x)
    smt = [r["smt"]["note"] for r in (m1, m5, m15) if r.get("smt") and r["smt"].get("state")]
    if smt:
        p3 += " " + smt[0]
    lines.append({"phase": "3 Validation", "text": p3})
    best = ranking[0] if ranking else None
    armed = [s for s in setups if s["status"] == "armed"]
    act = None
    if best and best.get("setup") and best["setup"]["status"] == "armed":
        s = best["setup"]
        p4 = (f"Best model now: {best['name']} on {s['tf']} ({s['grade']}). {s['boom']} {s['side']} "
              f"{'limit' if s['order'] == 'limit' else 'at market'} {s['entry']:.2f}, stop {s['sl']:.2f}, "
              f"targets {s['tp1']:.2f} ({s['rr1']:.1f}R) and {s['tp2']:.2f} ({s['rr2']:.1f}R). " + "; ".join(s["why"]) + ".")
        act = {"do": f"{s['side']} {s['tf']}", "level": s["entry"], "grade": s["grade"], "dir": s["dir"]}
    elif best and best.get("setup"):
        s = best["setup"]
        p4 = (f"Best model now: {best['name']} on {s['tf']}: its {s['side']} from {s['entry']:.2f} is {s['status']} "
              f"(stop {s['sl']:.2f}, targets {s['tp1']:.2f} / {s['tp2']:.2f}). Manage it; no new entry from it.")
        act = {"do": f"MANAGE {s['side']} {s['tf']}", "level": s["entry"], "grade": s["grade"], "dir": s["dir"]}
    elif armed:
        s = max(armed, key=lambda s: s["score"])
        p4 = (f"Best fit is {best['name']} (no setup yet: waiting for {best['waiting_for']}). Meanwhile {s['name']} on {s['tf']} "
              f"has a {s['grade']} {s['side']} at {s['entry']:.2f}, stop {s['sl']:.2f}, target {s['tp1']:.2f}.")
        act = {"do": f"WATCH {s['side']} {s['tf']}", "level": s["entry"], "grade": s["grade"], "dir": s["dir"]}
    elif best:
        p4 = f"Best fit now: {best['name']}. Waiting for {best['waiting_for']}."
        act = {"do": "WAIT", "level": None, "grade": None, "dir": 0}
    else:
        p4 = "Not enough candles yet."
    k = ctx.kronos30
    if k and k.get("up_prob") is not None:
        p4 += f" Kronos next 30 min: {k.get('call', '?')} (up {k['up_prob']:.0%}, {k.get('move', 0):+.2f})."
    lines.append({"phase": "4 Entry", "text": p4})
    headline = (act or {}).get("do", "WAIT")
    if not market_open:
        headline = "MARKET CLOSED"
        lines.insert(0, {"phase": "Market", "text": "Gold is closed: this is the read of the last candles, ready for the open."})
    elif ctx.news:
        headline = "WAIT (news)"
    return {"headline": headline, "action": act, "lines": lines,
            "summary": " ".join(l["text"] for l in lines[-1:]),
            "price": _r(px)}
