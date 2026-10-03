"""Boom / Crash: M1 scalp calls from ICT order flow, taken only with the higher timeframes, plus the trend reading
of every timeframe that the chart's gold line shows.

Every closed M1 candle:

  1. Trend reading (ictmodel.py): D1, H4, H1 structure and EMA regime, M15 / M5 structure, ICT events (M15
     turtle soup, M5 CISD, London / New York Judas swing of the Asian range, M15 fair value gaps) and Kronos
     vote into one score, -1 .. +1 (state.consensus). Its line on the chart starts at the price, follows half
     of the Kronos path and leans by the score.
  2. ICT playbook (playbook.py): every model of the gold ICT notes (Silver Bullet, Judas swing / AMD, Turtle
     Soup, MSS + FVG, Unicorn, Breaker, IFVG, OTE, Pulse IRL <-> ERL, HRLR -> LRLR, BPR, order block MT,
     NDOG / NWOG, XAU/XAG SMT reversal, the classic raid -> CISD -> FVG) looks for its setup on M1 and M5, each
     setup graded on the notes' checklist, and the models are ranked for the live market (time window, trend or
     range, measured record, a setup on the board). The M1 raid / CISD / FVG marks (ict_entries.py) stay on the
     chart.
  3. BOOM / CRASH: a setup armed on this candle, graded A or A+, from one of the `top_models` best-ranked models,
     with Kronos' next 30 minutes (M1 and M5 forecasts blended and calibrated, kronos_signal.py) on its side
     (`kronos_mode` "require"; "prefer" lets Kronos only grade, "off" ignores it).

BOOM is the buy, CRASH the sell. Limit at the model's entry (FVG 50 %, breaker edge, unicorn overlap, OTE ...) or
at market for the CISD models, stop beyond the sweep, TP1 at opposing liquidity (at least 1.5 R, else 2 R) and
TP2 at the higher timeframe's draw. A limit waits 30 M1 / 12 M5 candles; a filled call ends at TP1, the stop or
after 2 h (M1) / 6 h (M5). One call at a time. Nothing here places an order and none of it is proven: the
playbook's track record (playbook_backtest.py, ~/.golddesk/playbook_track.json) says how each model really did.
Finished calls go to ~/.golddesk/boom_calls.csv.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

import ict_entries as ie
from engine import Bars
from ictmodel import MarketRead, consensus, daily
from mesh import Mesh
import nowcast as nc
from playbook import Playbook
from tfdesk import desks

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
        self.desks: dict = {}                    # each timeframe's own concepts and call (tfdesk.py)
        self.nowcast: dict | None = None         # the 10-minute line from the live price, every poll (nowcast.py)
        self.sig: float | None = None            # one-minute sigma of the last closed candles
        self.nowcast30: dict | None = None       # the 30-minute line, band and sample paths (nowcast.py)
        self._utc = lambda t: t
        self._m1c: list = []
        self.nodes = None                        # nodes.Nodes: clock, calendar, cross-markets (set by the server)
        self.flow: dict = {"raids": [], "cisd": [], "orders": []}
        self.watch: list = []
        self.trend = None                        # kept for soon.py's signature
        self.kronos: dict | None = None
        self.error: str | None = None
        # ICT playbook (playbook.py): every model on M1 / M5, the best one for now, the chart's talk. BOOM / CRASH
        # calls come from it: a fresh A-grade setup of a top-ranked model, confirmed by Kronos' next 30 minutes.
        self.playbook = Playbook()
        self.ict: dict | None = None
        self.kronos30: dict | None = None        # Kronos' blended M1 / M5 30-minute forecast (kronos_signal.py)
        self.smt_fn = None                       # callable(gold bars by tf) -> SMT state by tf (set by the server)
        self.smt: dict | None = None
        self.kronos_mode = "require"             # require: Kronos must agree; prefer: it grades only; off
        self.kronos_running = lambda: False      # set by the server: is the Kronos worker loaded?
        self.min_grade = ("A+", "A")
        self.top_models = 3                      # calls come from the models ranked this high for the live market
        self.market_open = lambda: True

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

    def on_forecast30(self, fc: dict) -> None:
        """Kronos' 30-minute forecast (M1 and M5 blended, calibrated): the playbook's Kronos check reads it."""
        self.kronos30 = fc

    def kronos_gate(self, d: int) -> tuple:
        """(allowed?, why) for a call in direction d under the Kronos mode."""
        fc = self.kronos30
        fresh = fc and fc.get("up_prob") is not None and self.ict and abs((fc.get("t") or 0) - self.ict["t"]) <= 900
        if self.kronos_mode == "off":
            return True, "Kronos not used for calls"
        if not fresh:
            if self.kronos_mode == "require" and self.kronos_running():
                return False, "waiting for Kronos' 30-minute forecast"
            return True, "Kronos off: ICT only"
        p = fc["up_prob"] if d == 1 else 1 - fc["up_prob"]
        mv = (fc.get("move") or 0.0) * d
        agree = p >= 0.55 and mv >= 0
        against = p <= 0.45
        if self.kronos_mode == "require" and not agree:
            return False, f"Kronos 30 min does not confirm ({'up' if d == 1 else 'down'} {p:.0%})"
        if against:
            return False, f"Kronos 30 min against ({'up' if d == 1 else 'down'} {p:.0%})"
        return True, f"Kronos 30 min {'agrees' if agree else 'neutral'}: {'up' if d == 1 else 'down'} {p:.0%}, {mv:+.2f}"

    def _from_playbook(self, spread: float, t: int) -> dict | None:
        """A BOOM / CRASH call from the playbook: a setup armed on this candle, A grade or better, from one of the
        models that fit the live market best, with Kronos' next 30 minutes on its side."""
        ict = self.ict
        rank = {r["model"]: k for k, r in enumerate(ict.get("ranking") or [])}
        cands = [s for s in self.playbook.new if s["grade"] in self.min_grade and rank.get(s["model"], 99) < self.top_models]
        cands.sort(key=lambda s: (rank.get(s["model"], 99), -s["score"]))
        self.gate_note = None
        for s in cands:
            ok, why = self.kronos_gate(s["dir"])
            if not ok:
                self.gate_note = f"{s['name']} {s['side']} {s['tf']} held back: {why}"
                continue
            return self._order_setup(s, why, rank.get(s["model"], 0) + 1, t)
        return None

    def _order_setup(self, s: dict, kronos_why: str, rank: int, t: int) -> dict:
        d = s["dir"]
        sec = 60 if s["tf"] == "M1" else 300
        hold = {"M1": 120, "M5": 72}[s["tf"]] * sec
        why = [f"{s['name']} on {s['tf']} ({s['grade']}, #{rank} for this market)"] + s["why"] + [kronos_why]
        market = s["order"] == "market"
        call = {"kind": NAME[d], "side": SIDE[d], "dir": d, "t": t, "tf": s["tf"], "order": s["order"],
                "status": "filled" if market else "waiting", "entry": round(s["entry"], self.digits),
                "sl": round(s["sl"], self.digits), "tp": round(s["tp1"], self.digits), "tp2": round(s["tp2"], self.digits),
                "risk": s["risk"], "atr": s["risk"], "move": 0.0, "move_atr": 0, "minutes": hold // 60,
                "fill_by": s["fill_by"], "expires": (t + hold) if market else s["fill_by"] + hold,
                "strong": s["grade"] == "A+", "why": why, "model": s["name"], "model_id": s["model"],
                "grade": s["grade"], "checks": s["checks"], "setup_id": s["id"],
                "setup": {"pool": (s.get("sweep") or {}).get("kind"), "level": (s.get("sweep") or {}).get("level"),
                          "extreme": (s.get("sweep") or {}).get("ext"), "gap": [s["zone"]["bottom"], s["zone"]["top"]]}}
        if market:
            call["t_in"] = t
        call["text"] = (f"XAUUSD {call['kind']} ({s['name']}, {s['tf']}, {s['grade']}): {call['side']} "
                        f"{'LIMIT @' if not market else 'NOW ~'} {self._px(call['entry'])} | SL {self._px(call['sl'])} | "
                        f"TP1 {self._px(call['tp'])} | TP2 {self._px(call['tp2'])} | " + ", ".join(why[1:]))
        self.active = call
        return call

    def expire(self, quote: dict | None) -> None:          # kept for the server's poll loop
        return None

    def update(self, src, offset, quote: dict | None) -> list:
        """Each poll. Reads M1 and the higher timeframes when a new M1 candle has closed. Returns events:
        ("boom", call) a new limit order, ("boom_fill", call), ("boom_end", call)."""
        out = []
        m1 = src.rates("M1", HISTORY["M1"] + 1)
        self._utc = lambda t: t - offset(t)
        self._m1c = m1.c[-2900:-1] if len(m1) else []
        a = self.active
        if a and a["status"] == "waiting" and quote and len(m1):   # fill on the live quote
            if (quote["ask"] <= a["entry"]) if a["dir"] == 1 else (quote["bid"] >= a["entry"]):
                # the forming candle's time: the candle clock, like every other time here (the quote's own
                # time can be UTC while the broker's candles run ahead of it)
                out.append(("boom_fill", self._fill(m1.t[-1])))
        if len(m1) < 400:
            return out
        n = len(m1) - 1                                            # the last candle is still forming
        if m1.t[n - 1] == self.fed_t:
            self._nowcast(m1.t[n], self._live_px(quote, m1.c[n]))
            return out
        utc = self._utc
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
        self.fed_t = m1.t[n - 1]
        if self.read is not None:
            self._consensus(m1.t[n - 1] + 60, m1.c[n - 1], self.read.f["M1"].atr[-1])
        news = False
        if self.nodes is not None:
            try:
                news = self.nodes.calendar.near(utc(m1.t[n - 1]) + 60) is not None
            except Exception:
                news = False
        if self.read is not None:
            try:
                self.smt = self.smt_fn(bars) if self.smt_fn else None
            except Exception as e:
                self.smt, self.error = None, f"SMT skipped: {e}"
            try:
                self.ict = self.playbook.update(bars, utc, desks=self.desks, kronos30=self.kronos30, smt=self.smt,
                                                news=news, spread=spread, market_open=self.market_open())
            except Exception as e:
                self.error = f"ICT playbook skipped: {e}"
            if not first and not self.active and self.ict:
                call = self._from_playbook(spread, m1.t[n - 1])
                if call:
                    out.append(("boom", call))
        self.sig = nc.sigma(m1.c[max(0, n - nc.SIGMA_N - 1):n])
        self._nowcast(m1.t[n - 1] + 60, m1.c[n - 1])               # from the close, for the mesh's record
        closed_nc = self.nowcast
        for i in new:                                              # the knowledge mesh: candle + reading
            rd = None
            if i == n - 1 and self.consensus and self.consensus.get("x"):
                cs = self.consensus
                rd = {"x": cs["x"], "s": cs["score"], "atr": round(cs["atr"], 3),
                      "g": dict({p["name"]: p["score"] for p in cs["parts"]},
                                **{f"Desk {k}": d["score"] for k, d in self.desks.items()}),
                      "k": (cs["parts"][-1]["items"]["up_prob"] if cs["kronos"] else None)}
                if closed_nc:
                    rd["g"]["10-min line"] = round(closed_nc["target"] - closed_nc["last"], 3)
            if i == n - 1 and self.nodes is not None:
                tu = utc(m1.t[i])
                try:
                    gold = {utc(m1.t[k]): (m1.h[k], m1.l[k], m1.c[k]) for k in range(max(0, n - 70), n)}
                    votes = self.nodes.votes(gold, tu)
                    news = self.nodes.calendar.near(tu + 60) is not None
                except Exception as e:
                    votes, news, self.error = {}, False, f"Mesh nodes skipped: {e}"
                if rd is not None:
                    rd["g"].update(votes)
                    rd["news"] = news
                elif news:
                    rd = {"x": {}, "news": True}
            self.mesh.record(m1.t[i] + 60, m1.o[i], m1.h[i], m1.l[i], m1.c[i], rd)
        self.watch = self._watch()
        self._nowcast(m1.t[n], self._live_px(quote, m1.c[n]))
        return out

    @staticmethod
    def _live_px(quote: dict | None, forming_close: float) -> float:
        return (quote["bid"] + quote["ask"]) / 2 if quote else forming_close

    def _nowcast(self, t: int, price: float) -> None:
        """The 10-minute line from the live price; t is the forming candle's open (candle clock)."""
        if not self.sig:
            self.nowcast = None
            return
        line = self.consensus["score"] if self.consensus else None
        k = self.kronos if self.kronos and abs((self.kronos.get("t") or 0) - t) < 1800 else None
        self.nowcast = nc.nowcast(t, price, self.sig, nc.score(self.desks, line), k, self.digits)
        self.nowcast30 = None
        closes = list(self._m1c) + [price]
        hsd = self.nodes.clock.sd30(self._utc(t)) if self.nodes is not None else None
        sde = nc.sigma_end(closes, 30, hsd)
        if sde:
            self.nowcast30 = nc.nowcast(t, price, self.sig, 0.0, None, self.digits, minutes=30, sd_end=sde,
                                        samples=nc.bootstrap(closes, 30, seed=t // 60))
        if self.consensus is not None:
            self.consensus["live"] = self.nowcast
            self.consensus["live30"] = self.nowcast30

    # ------------------------------------------------------------ calls
    def consensus_htf(self, t: int, price: float) -> float | None:
        if self.read is None:
            return None
        x = self.read.features(t, price)
        return consensus(x)["parts"][0]["score"] if x else None

    def _fill(self, t: int) -> dict:
        a = self.active
        a.update(status="filled", t_in=t)
        if self.entries:
            self.entries.take(a["dir"])
        a["expires"] = t + 60 * a.get("minutes", ie.MAX_MIN)
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
                return [("boom_end", self._cancel(f"not filled within {(a['fill_by'] - a['t']) // 60} min"))]
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
                    # "bar_utc" is a historical name: the column holds the candle clock (the chart's time), and
                    # _read() turns it back into the same number, so reloaded calls sit on the right candle.
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
        try:
            self.desks = desks(self.read, t, price)
        except Exception as e:
            self.desks, self.error = {}, f"Timeframe desks skipped: {e}"
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
                "rules": {"entry": "a fresh A / A+ setup of one of the top-ranked ICT models on M1 or M5 (playbook.py)",
                          "sl": "beyond the sweep (0.30 or 0.1 ATR buffer)", "tp": "TP1 opposing liquidity (>= 1.5 R) or 2 R; "
                          "TP2 the higher timeframe's draw", "kronos": self.kronos_mode,
                          "grades": list(self.min_grade), "top_models": self.top_models},
                "gate": getattr(self, "gate_note", None), "kronos30": self.kronos30,
                "error": self.error, "proven": False}
