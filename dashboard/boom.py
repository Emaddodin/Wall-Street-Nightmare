"""Boom / Crash: M1 scalp calls from ICT order flow, taken only with the higher timeframes, plus the trend reading
of every timeframe that the chart's gold line shows.

Every closed M1 candle:

  1. Trend reading (ictmodel.py): D1, H4, H1 structure and EMA regime, M15 / M5 structure, ICT events (M15
     turtle soup, M5 CISD, London / New York Judas swing of the Asian range, M15 fair value gaps) and Kronos
     vote into one score, -1 .. +1 (state.consensus). Its line on the chart starts at the price, follows half
     of the Kronos path and leans by the score.
  2. Entries (ict_entries.py): inside the London and New York AM killzones, an M1 candle raids external
     liquidity (an M15 swing, the Asian range, PDH / PDL) and closes back; a change in state of delivery
     follows; the displacement left a fair value gap. The call is a LIMIT order at the gap's 50%.
  3. Only with the higher timeframes: the D1 / H4 / H1 part of the reading must point the trade's way. On a
     year of Dukascopy gold, raids against it lost about 0.35 R each; with it they made about +0.1 R each on
     too few trades to call proven (ict_backtest.py).

BOOM is the buy, CRASH the sell. Stop beyond the raid's extreme plus 0.30, target 2 R, the order waits
ict_entries.FILL_WITHIN minutes and a filled call ends at the target, the stop or after MAX_MIN minutes.
One call at a time. Nothing here places an order. Finished calls go to ~/.golddesk/boom_calls.csv.
"""
from __future__ import annotations

import csv
import time
from datetime import datetime, timezone
from pathlib import Path

import ict_entries as ie
from engine import Bars
from ictmodel import MarketRead, consensus, daily
from mesh import Mesh

LOG_FILE = Path.home() / ".golddesk" / "boom_calls.csv"
NAME = {1: "BOOM", -1: "CRASH"}
SIDE = {1: "BUY", -1: "SELL"}
HISTORY = {"M1": 2500, "M5": 600, "M15": 400, "H1": 800, "H4": 300}
LINE_MIN = 120            # the trend line reaches this far ahead
LINE_STEP = 300


def bar_exit(d: int, sl: float, tp: float, h: float, l: float, spread: float):
    """Stop or target inside one bar (bid prices; a sell closes at the ask). Stop first when both."""
    if d == 1:
        return ("stop", sl) if l <= sl else (("target", tp) if h >= tp else None)
    return ("stop", sl) if h + spread >= sl else (("target", tp) if l + spread <= tp else None)


def _closed(b: Bars) -> Bars:
    """The candles without the last one, which is still forming."""
    return b.slice_from(0) if not len(b) else _trim(b.slice_from(0))


def _trim(b: Bars) -> Bars:
    for k in ("t", "o", "h", "l", "c", "v", "spread"):
        setattr(b, k, getattr(b, k)[:-1])
    return b


class BoomTracker:
    def __init__(self, digits: int = 2, log: Path | None = LOG_FILE, mesh: Mesh | None = None):
        self.digits, self.log = digits, log
        self.mesh = mesh if mesh is not None else Mesh(None)
        self.active: dict | None = None          # the call: status "waiting" (limit order) or "filled"
        self.history: list = self._read()
        self.entries: ie.M1Entries | None = None
        self.fed_t = 0                           # last M1 candle (candle clock) fed to the entries
        self.read: MarketRead | None = None
        self.consensus: dict | None = None
        self.flow: dict = {"raids": [], "cisd": [], "orders": []}
        self.watch: list = []
        self.trend = None                        # kept for soon.py's signature
        self.kronos: dict | None = None
        self.error: str | None = None

    # ------------------------------------------------------------ history file
    def _read(self) -> list:
        try:
            with open(self.log, newline="") as f:
                rows = list(csv.DictReader(f))[-50:]
        except (OSError, TypeError):
            return []
        out = []
        for r in rows:
            try:
                d = 1 if r["side"] == "BUY" else -1
                out.append({"kind": r["kind"], "side": r["side"], "dir": d, "entry": float(r["entry"]),
                            "sl": float(r["sl"]), "tp": float(r["tp"]), "exit": float(r["exit"]), "how": r["how"],
                            "r": float(r["r"]), "usd_001": float(r["usd_0.01lot"]), "why": r["why"].split("; "),
                            "t": int(datetime.strptime(r["bar_utc"], "%Y-%m-%d %H:%M")
                                     .replace(tzinfo=timezone.utc).timestamp()), "model": r.get("model")})
            except (KeyError, ValueError):
                continue
        return out

    def _px(self, x: float) -> str:
        return f"{x:.{self.digits}f}"

    # ------------------------------------------------------------ live
    def on_forecast(self, fc: dict, quote: dict | None = None) -> None:
        """A Kronos forecast: its up-probability and path feed the trend reading and line."""
        self.kronos = fc
        if self.read is not None and self.consensus is not None:
            self._consensus(self.consensus["t"], self.consensus["price"], self.consensus["atr"])
        return None

    def expire(self, quote: dict | None) -> None:          # kept for the server's poll loop
        return None

    def update(self, src, offset, quote: dict | None) -> list:
        """Each poll. Reads M1 and the higher timeframes when a new M1 candle has closed. Returns events:
        ("boom", call) a new limit order, ("boom_fill", call), ("boom_end", call)."""
        out = []
        a = self.active
        if a and a["status"] == "waiting" and quote:            # fill on the live quote
            if (quote["ask"] <= a["entry"]) if a["dir"] == 1 else (quote["bid"] >= a["entry"]):
                out.append(("boom_fill", self._fill(int(time.time()))))
        m1 = src.rates("M1", HISTORY["M1"] + 1)
        if len(m1) < 400:
            return out
        n = len(m1) - 1                                            # the last candle is still forming
        if m1.t[n - 1] == self.fed_t:
            return out
        utc = lambda t: t - offset(t)
        try:
            bars = {"M1": _closed(m1)}
            for tf in ("M5", "M15", "H1", "H4"):
                bars[tf] = _closed(src.rates(tf, HISTORY[tf] + 1))
            bars["D1"] = daily(bars["H1"], utc)
            self.read = MarketRead(bars, utc)
            self.error = None
        except Exception as e:                                     # a timeframe the source can't give yet
            self.error = f"Trend reading skipped: {e}"
            self.read = None
        spread = (quote["ask"] - quote["bid"]) if quote else (m1.spread[n - 1] or 0.22)
        first = self.entries is None
        if first:
            self.entries = ie.M1Entries()
        new = [i for i in range(n) if m1.t[i] > self.fed_t]
        for i in new:
            t, o, h, l, c = m1.t[i], m1.o[i], m1.h[i], m1.l[i], m1.c[i]
            out += self._manage(t, h, l, c, spread)
            for kind, e in self.entries.add(utc(t), o, h, l, c):
                e = dict(e, time=t)                                # back on the candle clock for the chart
                if kind in ("raid", "cisd", "order"):
                    key = {"raid": "raids", "cisd": "cisd", "order": "orders"}[kind]
                    self.flow[key] = (self.flow[key] + [self._round(e)])[-12:]
                if kind == "cancel" and self.active and self.active["status"] == "waiting" \
                        and self.active["dir"] == e["dir"]:
                    out.append(("boom_end", self._cancel("cancelled: price ran past the raid first")))
                if kind == "order" and i == n - 1 and not first and not self.active:
                    call = self._order(e, spread, t)
                    if call:
                        out.append(("boom", call))
        self.fed_t = m1.t[n - 1]
        if self.read is not None:
            self._consensus(m1.t[n - 1] + 60, m1.c[n - 1], self.read.f["M1"].atr[-1])
        for i in new:                                              # the knowledge mesh: candle + reading
            rd = None
            if i == n - 1 and self.consensus and self.consensus.get("x"):
                cs = self.consensus
                rd = {"x": cs["x"], "s": cs["score"], "atr": round(cs["atr"], 3),
                      "g": {p["name"]: p["score"] for p in cs["parts"]},
                      "k": (cs["parts"][-1]["items"]["up_prob"] if cs["kronos"] else None)}
            self.mesh.record(m1.t[i] + 60, m1.o[i], m1.h[i], m1.l[i], m1.c[i], rd)
        self.watch = self._watch()
        return out

    # ------------------------------------------------------------ calls
    def _order(self, e: dict, spread: float, t: int) -> dict | None:
        d = e["dir"]
        htf = self.consensus_htf(t + 60, e["entry"])
        if htf is None or htf * d <= 0:
            return None
        p = ie.plan(d, e["entry"], e["ext"], spread)
        if not p:
            return None
        why = [f"raided {e['name']} {self._px(e['level'])}", f"CISD {'up' if d == 1 else 'down'}",
               f"limit at the FVG 50% ({self._px(e['gap'][1])}-{self._px(e['gap'][0])})",
               f"higher timeframes {'up' if d == 1 else 'down'} ({htf:+.2f})"]
        if self.consensus:
            why.append(f"trend reading {self.consensus['score']:+.2f}")
        call = {"kind": NAME[d], "side": SIDE[d], "dir": d, "t": t, "tf": "M1", "order": "limit",
                "status": "waiting", "entry": round(e["entry"], self.digits), "sl": round(p["sl"], self.digits),
                "tp": round(p["tp"], self.digits), "risk": round(p["risk"], 2), "atr": round(p["risk"], 2),
                "move": 0.0, "move_atr": 0, "minutes": ie.MAX_MIN, "fill_by": t + 60 + ie.FILL_WITHIN * 60,
                "expires": t + 60 + (ie.FILL_WITHIN + ie.MAX_MIN) * 60, "strong": abs(htf) >= 0.6,
                "why": why, "model": None,
                "setup": {"pool": e["name"], "level": round(e["level"], 2), "extreme": round(e["ext"], 2),
                          "gap": [round(e["gap"][1], 2), round(e["gap"][0], 2)]}}
        call["text"] = (f"XAUUSD {call['kind']}: {call['side']} LIMIT @ {self._px(call['entry'])} | "
                        f"SL {self._px(call['sl'])} | TP {self._px(call['tp'])} | valid {ie.FILL_WITHIN} min | "
                        + ", ".join(why))
        self.active = call
        return call

    def consensus_htf(self, t: int, price: float) -> float | None:
        if self.read is None:
            return None
        x = self.read.features(t, price)
        return consensus(x)["parts"][0]["score"] if x else None

    def _fill(self, t: int) -> dict:
        a = self.active
        a.update(status="filled", t_in=t, expires=t + ie.MAX_MIN * 60)
        if self.entries:
            self.entries.take(a["dir"])
        a["text"] = f"XAUUSD {a['kind']} {a['side']} LIMIT filled at {self._px(a['entry'])}"
        return a

    def _cancel(self, why: str) -> dict:
        a, self.active = self.active, None
        a["status"] = "cancelled"
        a["text"] = f"XAUUSD {a['kind']} {a['side']} LIMIT @ {self._px(a['entry'])} {why}"
        return a

    def _manage(self, t: int, h: float, l: float, c: float, spread: float) -> list:
        a = self.active
        if not a or t <= a["t"]:
            return []
        out = []
        if a["status"] == "waiting":
            if (l <= a["entry"] - spread) if a["dir"] == 1 else (h >= a["entry"]):
                out.append(("boom_fill", self._fill(t)))
                if (l <= a["sl"]) if a["dir"] == 1 else (h + spread >= a["sl"]):
                    out.append(("boom_end", self._finish("stop", a["sl"])))
                return out
            if t + 60 > a["fill_by"]:
                return [("boom_end", self._cancel(f"not filled within {ie.FILL_WITHIN} min"))]
            return []
        hit = bar_exit(a["dir"], a["sl"], a["tp"], h, l, spread)
        if hit:
            return [("boom_end", self._finish(*hit))]
        if t + 60 >= a["expires"]:
            return [("boom_end", self._finish("time", c + (spread if a["dir"] == -1 else 0)))]
        return []

    def _finish(self, how: str, px: float) -> dict:
        s, self.active = self.active, None
        d, risk = s["dir"], s.get("risk") or 1.0
        s["exit"], s["how"] = round(px, self.digits), how
        s["usd_001"] = round((px - s["entry"]) * d, 2)
        s["r"] = round(s["usd_001"] / risk, 2) if risk else 0.0
        word = {"target": "hit target", "stop": "hit stop", "time": "time up"}[how]
        s["text"] = f"XAUUSD {s['kind']} {s['side']} from {self._px(s['entry'])} {word} at {self._px(px)}: {s['r']:+.2f} R"
        self.history = (self.history + [s])[-50:]
        self._write(s)
        return s

    def _write(self, s: dict) -> None:
        if not self.log:
            return
        try:
            new = not self.log.exists()
            self.log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log, "a", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["bar_utc", "kind", "side", "entry", "sl", "tp", "exit", "how", "r", "usd_0.01lot",
                                "kronos_move_30min", "atr", "why", "model"])
                w.writerow([datetime.fromtimestamp(s["t"], timezone.utc).strftime("%Y-%m-%d %H:%M"), s["kind"],
                            s["side"], s["entry"], s["sl"], s["tp"], s["exit"], s["how"], s["r"], s["usd_001"],
                            s["move"], s["atr"], "; ".join(s["why"]), s.get("model") or "ict-m1"])
        except OSError:
            pass

    # ------------------------------------------------------------ chart data
    def _round(self, e: dict) -> dict:
        return {k: (round(v, 2) if isinstance(v, float) else [round(x, 2) for x in v] if isinstance(v, tuple) else v)
                for k, v in e.items()}

    def _watch(self) -> list:
        a = self.active
        if not a or a["status"] != "waiting":
            return []
        return [{"dir": a["dir"], "poi": a["setup"]["gap"], "sweep_ext": a["setup"]["extreme"], "choch": a["entry"],
                 "since": a["t"], "until": a["fill_by"], "entry": a["entry"]}]

    def _consensus(self, t: int, price: float, atr_m1: float) -> None:
        x = self.read.features(t, price) if self.read else None
        if not x:
            self.consensus = None
            return
        k = self.kronos if self.kronos and abs((self.kronos.get("t") or 0) - t) < 1800 else None
        cs = consensus(x, k.get("up_prob") if k else None, self.mesh.trust())
        h1 = self.read.f.get("H1")
        unit = h1.atr[h1.at(t)] if h1 and h1.at(t) >= 0 else atr_m1 * 8
        steps = LINE_MIN * 60 // LINE_STEP
        kp = [p["value"] for p in (k.get("path") or [])][:steps] if k else []
        path = []
        for i in range(1, steps + 1):
            lean = cs["score"] * unit * i / steps
            kron = (kp[min(i, len(kp)) - 1] - k["last"]) if kp else 0.0
            path.append({"time": t - 60 + i * LINE_STEP, "value": round(price + lean + 0.5 * kron, 2)})
        tfs = {name: int(x.get(f"st_{name}", 0)) for name in ("M1", "M5", "M15", "H1", "H4", "D1")}
        cs.update(t=t, price=price, atr=atr_m1, path=path, minutes=LINE_MIN, tf=tfs, last=price,
                  target=path[-1]["value"], dir=cs["bias"],
                  label={1: "bullish", -1: "bearish", 0: "mixed"}[cs["bias"]],
                  kronos=bool(k), proven=False, x=x, trust=self.mesh.trust(),
                  note="What the timeframes, ICT events and Kronos say together. Descriptive: on a year of gold it "
                       "did not predict the next 30-120 minutes better than a coin flip.")
        self.consensus = cs

    def state(self) -> dict:
        h = self.history
        wins = sum(1 for s in h if s["r"] > 0)
        return {"active": self.active, "history": h[-20:], "watch": self.watch, "tf": "M1",
                "stats": {"calls": len(h), "wins": wins, "losses": len(h) - wins,
                          "net_r": round(sum(s["r"] for s in h), 2),
                          "net_usd_001": round(sum(s["usd_001"] for s in h), 2)},
                "rules": {"entry": "limit at the FVG 50% after a raid and CISD (M1, killzones)",
                          "sl": f"beyond the raid + {ie.SL_BUFFER}", "tp_r": ie.TP_R, "max_min": ie.MAX_MIN,
                          "fill_min": ie.FILL_WITHIN, "trend": "D1 / H4 / H1 must agree"},
                "error": self.error, "proven": False}
