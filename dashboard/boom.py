"""Boom / Crash: short scalp calls for when Kronos and the indicator agree.
Same rules as the BOOM / CRASH box on the VPS Kronos chart, so both pages call the same thing.

Checked after every closed entry bar (M5 by default), once Kronos has forecast the next bars:

  1. Kronos expects a move of at least 0.6 x ATR(14) within the next 30 minutes.
  2. Its path there is clean: it never runs against the move by more than 60% of the move.
  3. The indicator agrees: the H4 trend, the H1 structure or the indicator's own entry signal on that bar
     points the same way, and the H4 trend and H1 structure are not both against it.

BOOM is the buy, CRASH the sell, at the live price. Stop 1 x ATR; target the Kronos 30-minute target,
kept between 0.8 and 1.5 x ATR; the call ends at the target, the stop, or after 30 minutes.
One call at a time. These are signals for you to judge: nothing here places an order.
kronos_backtest.py measures them on past gold; every finished live call goes to ~/.golddesk/boom_calls.csv.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from engine import LADDER

MOVE_ATR = 0.6           # Kronos must expect at least this many ATRs ...
WITHIN_MIN = 30          # ... within this many minutes, which is also how long a call lives
CLEAN = 0.6              # largest pull-back along the forecast path, as a share of the move
SL_ATR = 1.0
TP_ATR = (0.8, 1.5)      # target distance is kept inside this band
LOG_FILE = Path.home() / ".golddesk" / "boom_calls.csv"
NAME = {1: "BOOM", -1: "CRASH"}
SIDE = {1: "BUY", -1: "SELL"}


def setup(eng) -> dict:
    """What the indicator says about the entry bar that just closed, per side (1 = buy, -1 = sell)."""
    ctx = eng.ctx
    macro, struct, _ = LADDER.get({60: "M1", 300: "M5"}.get(eng.sec, "M1"))
    out = {"agree": {1: [], -1: []}, "against": {1: [], -1: []}}
    if ctx is None:
        return out
    for d in (1, -1):
        up = d == 1
        if ctx["regime"] == d:
            out["agree"][d].append(f"{macro} trend {'up' if up else 'down'}")
        elif ctx["regime"] == -d:
            out["against"][d].append(f"{macro} trend {'down' if up else 'up'}")
        if ctx["bias15"] == d:
            out["agree"][d].append(f"{struct} structure {'bullish' if up else 'bearish'}")
        elif ctx["bias15"] == -d:
            out["against"][d].append(f"{struct} structure {'bearish' if up else 'bullish'}")
        if eng.sig.get(d):
            out["agree"][d].append(f"indicator {SIDE[d]} signal ({eng.WHY[eng.sig[d]].lower()})")
    return out


def gate(st: dict, d: int) -> bool:
    """The indicator half: something agrees, and trend and structure are not both against."""
    return bool(st["agree"][d]) and len(st["against"][d]) < 2


def _window(fc: dict, sec: int) -> list:
    n = max(1, WITHIN_MIN * 60 // sec)
    path = [p["value"] for p in (fc.get("path") or [])[:n]]
    return path if len(path) == n else []


def decide(fc: dict, st: dict, sec: int) -> int:
    """1 for BOOM, -1 for CRASH, 0 for nothing."""
    a, path = fc.get("atr") or 0.0, _window(fc, sec)
    if a <= 0 or not path:
        return 0
    move = path[-1] - fc["last"]
    d = 1 if move >= MOVE_ATR * a else (-1 if move <= -MOVE_ATR * a else 0)
    if not d:
        return 0
    worst = min(path) if d == 1 else max(path)
    if max(0.0, (fc["last"] - worst) * d) > CLEAN * abs(move):
        return 0
    return d if gate(st, d) else 0


def levels(d: int, entry: float, fc: dict, sec: int) -> tuple:
    a = fc["atr"]
    reach = min(max(abs(_window(fc, sec)[-1] - fc["last"]), TP_ATR[0] * a), TP_ATR[1] * a)
    return entry - d * SL_ATR * a, entry + d * reach


def bar_exit(d: int, sl: float, tp: float, h: float, l: float, spread: float):
    """Stop or target inside one bar (bid prices; a sell closes at the ask). Stop first when both."""
    if d == 1:
        return ("stop", sl) if l <= sl else (("target", tp) if h >= tp else None)
    return ("stop", sl) if h + spread >= sl else (("target", tp) if l + spread <= tp else None)


class BoomTracker:
    """Keeps the live call, scores each one when it ends, and logs it."""

    def __init__(self, digits: int = 2, log: Path | None = LOG_FILE):
        self.digits, self.log = digits, log
        self.active: dict | None = None
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

    def on_forecast(self, fc: dict, st: dict, tick: dict | None, sec: int) -> dict | None:
        """A fresh Kronos forecast on the bar that just closed. Returns the new call, if there is one."""
        if self.active or not tick:
            return None                                   # one call at a time
        d = decide(fc, st, sec)
        if not d:
            return None
        entry = tick["ask"] if d == 1 else tick["bid"]
        sl, tp = levels(d, entry, fc, sec)
        a = fc["atr"]
        move = round(_window(fc, sec)[-1] - fc["last"], 2)
        sig = {
            "kind": NAME[d], "side": SIDE[d], "dir": d, "t": fc["t"], "at": tick.get("time"),
            "entry": round(entry, self.digits), "sl": round(sl, self.digits), "tp": round(tp, self.digits),
            "atr": round(a, 2), "move": move, "move_atr": round(abs(move) / a, 1), "minutes": WITHIN_MIN,
            "expires": fc["t"] + sec + WITHIN_MIN * 60, "strong": len(st["agree"][d]) >= 2,
            "why": st["agree"][d], "model": fc.get("model"),
        }
        sig["text"] = (f"XAUUSD {sig['kind']}: {sig['side']} @ {self._px(entry)} | SL {self._px(sl)} | "
                       f"TP {self._px(tp)} | Kronos {move:+.2f} in {WITHIN_MIN} min ({sig['move_atr']} ATR) | "
                       + ", ".join(sig["why"]))
        self.active = sig
        return sig

    def on_bar(self, t: int, h: float, l: float, c: float, sec: int, spread: float = 0.0) -> list:
        """A closed entry bar. Returns the calls that ended on it."""
        s = self.active
        if not s or t <= s["t"]:
            return []
        hit = bar_exit(s["dir"], s["sl"], s["tp"], h, l, spread)
        if hit:
            return [self._finish(*hit)]
        if t + sec >= s["expires"]:
            return [self._finish("time", c if s["dir"] == 1 else c + spread)]
        return []

    def _finish(self, how: str, px: float) -> dict:
        s, self.active = self.active, None
        d, risk = s["dir"], SL_ATR * s["atr"]
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
        return {"active": self.active, "history": h[-20:],
                "stats": {"calls": len(h), "wins": wins, "losses": len(h) - wins,
                          "net_r": round(sum(s["r"] for s in h), 2),
                          "net_usd_001": round(sum(s["usd_001"] for s in h), 2)},
                "rules": {"move_atr": MOVE_ATR, "within_min": WITHIN_MIN, "clean": CLEAN, "sl_atr": SL_ATR,
                          "tp_atr": list(TP_ATR)},
                "proven": False}
