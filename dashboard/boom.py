"""Boom / Crash: smart money scalp calls, with the trend judged by the higher timeframes and Kronos.

The rules live in orchestra.py (trend layer, setup layer, trade plan); this file runs them live:

  1. After every closed entry candle (M5 by default) the setup layer looks for its sequence: a liquidity
     sweep, a change of character, a return into the fair value gap or order block the move left, and a
     confirming candle (engulfing, hammer / shooting star, or a rejection close).
  2. When a setup completes, the trend layer must agree: H4 structure, H4 EMA 50/200, H1 structure and, when
     it is running, Kronos (its up-probability for the next two hours) vote, and the score must reach +2
     (BOOM) or -2 (CRASH) with H1 structure not against. If Kronos is running, the call waits for its
     forecast of that candle (at most KRONOS_WAIT seconds) so its vote counts.
  3. BOOM is the buy, CRASH the sell, at the live price, with the stop beyond the sweep and the target
     orchestra.TP_R times the risk. The call ends at the target, the stop, or after orchestra.MAX_MIN minutes.

One call at a time. These are signals for you to judge: nothing here places an order.
boom_backtest.py measures them on past gold; every finished live call goes to ~/.golddesk/boom_calls.csv.
"""
from __future__ import annotations

import csv
import time
from datetime import datetime, timezone
from pathlib import Path

import orchestra as oc
from engine import LADDER

KRONOS_WAIT = 120         # seconds a finished setup waits for the Kronos forecast of its candle
LOG_FILE = Path.home() / ".golddesk" / "boom_calls.csv"
NAME, SIDE = oc.NAME, oc.SIDE


def bar_exit(d: int, sl: float, tp: float, h: float, l: float, spread: float):
    """Stop or target inside one bar (bid prices; a sell closes at the ask). Stop first when both."""
    if d == 1:
        return ("stop", sl) if l <= sl else (("target", tp) if h >= tp else None)
    return ("stop", sl) if h + spread >= sl else (("target", tp) if l + spread <= tp else None)


class BoomTracker:
    """Runs the setup layer on the engine's candles, keeps the live call, scores each one when it ends, logs it."""

    def __init__(self, digits: int = 2, log: Path | None = LOG_FILE):
        self.digits, self.log = digits, log
        self.active: dict | None = None
        self.pending: dict | None = None          # a finished setup waiting for Kronos
        self.watch: list = []                     # setups past their CHoCH, waiting for the return (for heads-ups)
        self.setups: oc.Setups | None = None
        self.trend: oc.Trend | None = None
        self.names = ("H4", "H1")
        self.fed = 0                              # engine candles fed to the setup layer
        self.history: list = self._read()

    def _read(self) -> list:
        """Earlier finished calls, so the tally survives a restart."""
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
                                     .replace(tzinfo=timezone.utc).timestamp()), "model": r["model"]})
            except (KeyError, ValueError):
                continue
        return out

    def _px(self, x: float) -> str:
        return f"{x:.{self.digits}f}"

    # ------------------------------------------------------------ candles
    def on_candles(self, eng, quote: dict | None, kronos_on: bool) -> list:
        """Call after the engine took its new closed candles. Feeds the setup layer, ends the live call on a stop,
        target or timeout, and starts a call when a setup completes on the newest candle.
        Returns events: ("boom", call) and ("boom_end", call)."""
        m, sec, out = eng.m1, eng.sec, []
        if eng.macro is None or not len(m):
            return out
        if self.setups is None or self.fed > len(m) or (self.fed and m.t[self.fed - 1] != self.setups.t[-1]):
            self.setups, self.fed, self.pending = oc.Setups(sec), 0, None
        self.names = LADDER.get({60: "M1", 300: "M5"}.get(sec, "M5"))[:2]
        self.trend = oc.Trend(eng.macro.b, eng.struct.b, self.names)
        newest = len(m) - 1
        for i in range(self.fed, len(m)):
            done = self.setups.add(m.t[i], m.o[i], m.h[i], m.l[i], m.c[i])
            if self.active and m.t[i] > self.active["t"]:
                spread = eng.bar_spread(i)
                hit = bar_exit(self.active["dir"], self.active["sl"], self.active["tp"], m.h[i], m.l[i], spread)
                if hit:
                    out.append(("boom_end", self._finish(*hit)))
                elif m.t[i] + sec >= self.active["expires"]:
                    out.append(("boom_end", self._finish("time", m.c[i] + (spread if self.active["dir"] == -1 else 0))))
            if i == newest and done and quote:
                for d, s in done.items():
                    utc = m.t[i] + sec - eng.offset_fn(m.t[i] + sec)
                    if not oc.in_session(utc):
                        continue
                    s.update(t=m.t[i], close_t=m.t[i] + sec)
                    if kronos_on:
                        self.pending = {"setup": s, "since": time.time()}
                    else:
                        sig = self._decide(s, None, quote)
                        if sig:
                            out.append(("boom", sig))
        self.fed = len(m)
        self.watch = self.setups.waiting() if self.setups else []
        return out

    def on_forecast(self, fc: dict, quote: dict | None) -> dict | None:
        """The Kronos forecast of the newest candle. Decides a setup that was waiting for it."""
        p = self.pending
        if not p or fc.get("t") != p["setup"]["t"]:
            return None
        self.pending = None
        return self._decide(p["setup"], fc, quote)

    def expire(self, quote: dict | None) -> dict | None:
        """Kronos is slow or stuck: decide the waiting setup without its vote."""
        p = self.pending
        if p and time.time() - p["since"] > KRONOS_WAIT:
            self.pending = None
            return self._decide(p["setup"], None, quote)
        return None

    # ------------------------------------------------------------ calls
    def _decide(self, s: dict, fc: dict | None, quote: dict | None) -> dict | None:
        if self.active or not quote or not self.trend:
            return None
        d = s["dir"]
        votes = self.trend.votes(s["close_t"], (fc or {}).get("up_prob"))
        if oc.Trend.bias(votes) != d:
            return None
        entry = quote["ask"] if d == 1 else quote["bid"]
        spread = max(0.0, quote["ask"] - quote["bid"])
        p = oc.plan(s, entry, spread)
        if not p:
            return None
        a, minutes = s["atr"], oc.MAX_MIN
        move = round(fc["path"][-1]["value"] - fc["last"], 2) if fc and fc.get("path") else 0.0
        why = self.trend.words(votes, d) + oc.describe(s)
        sig = {
            "kind": NAME[d], "side": SIDE[d], "dir": d, "t": s["t"], "at": quote.get("time"),
            "entry": round(entry, self.digits), "sl": round(p["sl"], self.digits), "tp": round(p["tp"], self.digits),
            "atr": round(a, 2), "risk": round(p["risk"], 2), "move": move, "move_atr": round(abs(move) / a, 1) if a else 0,
            "minutes": minutes, "expires": s["close_t"] + minutes * 60, "strong": sum(votes.values()) * d >= 3,
            "why": why, "votes": votes, "up_prob": (fc or {}).get("up_prob"),
            "setup": {"sweep": round(s["sweep_level"], 2), "sweep_ext": round(s["sweep_ext"], 2),
                      "choch": round(s["choch"], 2), "poi": [round(x, 2) for x in s["poi"]], "pattern": s["pattern"]},
            "model": (fc or {}).get("model"),
        }
        sig["text"] = (f"XAUUSD {sig['kind']}: {sig['side']} @ {self._px(entry)} | SL {self._px(p['sl'])} | "
                       f"TP {self._px(p['tp'])} | " + ", ".join(why))
        self.active = sig
        return sig

    def _finish(self, how: str, px: float) -> dict:
        s, self.active = self.active, None
        d, risk = s["dir"], s.get("risk") or s["atr"]
        s["exit"], s["how"] = round(px, self.digits), how
        s["usd_001"] = round((px - s["entry"]) * d, 2)                # dollars on 0.01 lot (1 oz)
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
                            s["move"], s["atr"], "; ".join(s["why"]), s["model"]])
        except OSError:
            pass

    def state(self) -> dict:
        h = self.history
        wins = sum(1 for s in h if s["r"] > 0)
        return {"active": self.active, "history": h[-20:], "watch": self.watch,
                "stats": {"calls": len(h), "wins": wins, "losses": len(h) - wins,
                          "net_r": round(sum(s["r"] for s in h), 2),
                          "net_usd_001": round(sum(s["usd_001"] for s in h), 2)},
                "rules": {"sl": "beyond the sweep + 0.1 ATR", "tp_r": oc.TP_R, "max_min": oc.MAX_MIN,
                          "trend_need": oc.NEED_SCORE, "trend": [f"{self.names[0]} structure",
                                                                 f"{self.names[0]} EMA 50/200",
                                                                 f"{self.names[1]} structure", "Kronos"]},
                "proven": False}
