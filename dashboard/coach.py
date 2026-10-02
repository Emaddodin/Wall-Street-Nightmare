"""Your daily trading session on your phone: ntfy pushes from 10:30 to 21:00 Tehran time, Monday to Friday.

  10:30   The session starts (London is open): the day's read, meaning price, H4 trend, H1 structure, the
          smart-money read of the chart and yesterday's high and low, plus the hours that matter today.
  NY      Five minutes before New York's data hour (08:30 New York: 16:00 Tehran in summer, 17:00 in winter):
          the busiest three hours start, and US news can spike price.
  20:45   Fifteen minutes left: close or protect open trades.
  21:00   The session is over: each setup sent today and how it ended.
  Ready   The indicator's entries and BOOM / CRASH calls as they fire, with entry, stop and target.

The "setup likely soon" heads-ups (soon.py) also go out only inside the session. Nothing here places an order,
and none of these signals has shown an edge yet. Other hours: --session 09:00-18:00 (Tehran time).
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from engine import LADDER, market_hours, ny7_offset

HOME = Path.home() / ".golddesk"
WINDOW = "10:30-21:00"
TEHRAN = timezone(timedelta(hours=3, minutes=30))   # Iran dropped summer time in 2022: +03:30 all year
READY_CAP = 10          # ready-setup pushes a day at most
NY_DATA = (8, 30)       # New York's data hour: the busiest three hours start here
NY_LATE = (10, 0)       # the second batch of US numbers
BUSY_H = 3
WORD = {1: "up", -1: "down", 0: "flat"}


def parse_window(s: str) -> tuple:
    """'10:30-21:00' -> (630, 1260), minutes after Tehran midnight."""
    a, b = (int(x) * 60 + int(y) for x, y in (p.strip().split(":") for p in s.split("-")))
    if not 0 <= a < b <= 24 * 60:
        raise ValueError(f"session hours must look like 10:30-21:00, not {s!r}")
    return a, b


def local(utc: float) -> datetime:
    return datetime.fromtimestamp(utc, TEHRAN)


def at_local(day: datetime, minutes: int) -> int:
    """UTC seconds of `minutes` after midnight, Tehran time, on `day`'s date."""
    mid = datetime(day.year, day.month, day.day, tzinfo=TEHRAN)
    return int(mid.timestamp()) + minutes * 60


def ny_utc(day: datetime, hh: int, mm: int) -> int:
    """UTC seconds of hh:mm New York time on `day`'s date."""
    naive = int(datetime(day.year, day.month, day.day, hh, mm, tzinfo=timezone.utc).timestamp())
    return naive + 7 * 3600 - ny7_offset(naive + 5 * 3600)      # New York runs 4 h (summer) or 5 h behind UTC


def hhmm(utc: float) -> str:
    return local(utc).strftime("%H:%M")


class SessionCoach:
    """Session pushes on a clock thread; ready-setup pushes when the hub calls ready_*(). Remembers what it sent
    today (~/.golddesk/session_coach.json), so a restart never repeats a push."""

    def __init__(self, snapshot, ntfy, window: str = WINDOW, file: Path | None = HOME / "session_coach.json",
                 clock=time.time, run: bool = True):
        self.snapshot, self.ntfy, self.file, self.clock = snapshot, ntfy, file, clock
        self.window = window
        self.start, self.end = parse_window(window)
        self.lock = threading.Lock()
        self.data = {"day": "", "done": [], "sent": [], "balance": None, "hello": ""}
        try:
            self.data.update(json.loads(file.read_text()))
        except (OSError, ValueError, AttributeError):
            pass
        if self.ntfy and self.data.get("hello") != f"{ntfy.topic} {window}":
            self._push("Gold Desk trading session is set",
                       f"Every market day from {self._clock(self.start)} to {self._clock(self.end)} Tehran time I'll "
                       "push the session start with the day's read, the New York open, 15 minutes left, and a recap "
                       "at the end, plus each ready setup (indicator entries and BOOM / CRASH calls) with entry, "
                       "stop and target. Untested signals: you decide every trade.", "default", "bell")
            self.data["hello"] = f"{ntfy.topic} {window}"
            self._save()
        if run:
            threading.Thread(target=self._loop, daemon=True).start()

    # ------------------------------------------------------------ clock
    @staticmethod
    def _clock(minutes: int) -> str:
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    def in_session(self, utc: float | None = None) -> bool:
        """Inside today's session hours, on a day gold trades."""
        utc = self.clock() if utc is None else utc
        d = local(utc)
        return at_local(d, self.start) <= utc < at_local(d, self.end) and market_hours(utc)[0]

    def times(self, utc: float) -> dict:
        """Today's session moments, UTC seconds."""
        d = local(utc)
        start, end = at_local(d, self.start), at_local(d, self.end)
        return {"start": start, "end": end, "ny": ny_utc(d, *NY_DATA) - 300, "last15": end - 900,
                "data": ny_utc(d, *NY_DATA), "late": ny_utc(d, *NY_LATE)}

    def _loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as e:                   # a push problem must never stop the clock
                print(f"Session pushes: {e}", flush=True)
            time.sleep(20)

    def tick(self, utc: float | None = None) -> list:
        """Sends whichever session push is due now. Returns their keys."""
        utc = self.clock() if utc is None else utc
        tm, sent = self.times(utc), []
        with self.lock:
            self._new_day(utc)
            done = self.data["done"]
            if not market_hours(min(max(utc, tm["start"]), tm["end"] - 60))[0]:
                return sent                          # weekend: no session today
            due = [("start", tm["start"] <= utc < tm["last15"]),           # late (after a restart) is still useful
                   ("ny", tm["start"] <= tm["ny"] < tm["end"] and tm["ny"] <= utc < min(tm["ny"] + 1800, tm["end"])),
                   ("last15", tm["last15"] <= utc < tm["end"] and "start" in done),
                   ("end", tm["end"] <= utc < tm["end"] + 1800 and "start" in done)]
            for key, now in due:
                if now and key not in done:
                    done.append(key)
                    self._save()
                    sent.append(key)
        for key in sent:                             # outside the lock: the snapshot takes the hub's lock
            title, body, prio, tags = getattr(self, "_msg_" + key)(utc, tm)
            self._push(title, body, prio, tags)
        return sent

    def _new_day(self, utc: float) -> None:
        day = local(utc).strftime("%Y-%m-%d")
        if self.data["day"] != day:
            self.data.update(day=day, done=[], sent=[], balance=None)

    # ------------------------------------------------------------ messages
    def _read(self, s: dict) -> list:
        lines, d = [], s.get("digits", 2)
        names = LADDER.get(s.get("tf") or "M5", ("H4", "H1", "M15"))
        ctx = s.get("ctx")
        px = f"Gold {s['price']:.{d}f}. " if s.get("price") else ""
        if ctx:
            lines.append(f"{px}{names[0]} trend {WORD[ctx['regime']]}, {names[1]} structure "
                         f"{ {1: 'bullish', -1: 'bearish', 0: 'mixed'}[ctx['bias15']] }.")
        elif px:
            lines.append(px.strip())
        smc = s.get("smc") or {}
        if smc.get("summary"):
            lines.append(f"Smart money: {smc['summary']}.")
        lv = {x["label"]: x["price"] for x in smc.get("levels") or []}
        if "PDH" in lv and "PDL" in lv:
            lines.append(f"Yesterday's high {lv['PDH']:.{d}f}, low {lv['PDL']:.{d}f}.")
        k = s.get("kronos")
        if k and k.get("move") is not None:
            share = f", {k['up_prob']:.0%} of paths up" if k.get("up_prob") is not None else ""
            lines.append(f"Kronos next {k.get('minutes') or 120} min: {'up' if k['move'] > 0 else 'down'} "
                         f"{abs(k['move']):.2f}{share} (unproven).")
        return lines

    def _msg_start(self, utc: float, tm: dict) -> tuple:
        s = self.snapshot()
        with self.lock:
            self.data["balance"] = s.get("balance")
            self._save()
        busy = f"{hhmm(tm['data'])}-{hhmm(tm['data'] + BUSY_H * 3600)}"
        lines = self._read(s) + [
            f"Busiest: {busy}, when New York joins London. US news can spike price at {hhmm(tm['data'])} "
            f"and {hhmm(tm['late'])}.",
            f"I'll push ready setups until {self._clock(self.end)}. Untested signals: you decide every trade."]
        return f"Trading session open: {self._clock(self.start)}-{self._clock(self.end)}", "\n".join(lines), \
            "default", "sunrise"

    def _msg_ny(self, utc: float, tm: dict) -> tuple:
        s = self.snapshot()
        px = f" Gold {s['price']:.{s.get('digits', 2)}f}." if s.get("price") else ""
        return "New York opens: busiest hours now", (
            f"From {hhmm(tm['data'])} to {hhmm(tm['data'] + BUSY_H * 3600)} gold moves most. US news can spike "
            f"price at {hhmm(tm['data'])} and {hhmm(tm['late'])}: wait 2-5 minutes after big releases.{px}"), \
            "default", "bell"

    def _msg_last15(self, utc: float, tm: dict) -> tuple:
        return "15 minutes left in today's session", (
            f"The session ends at {self._clock(self.end)}. Close or protect any open trade; no new trades now."), \
            "default", "hourglass_flowing_sand"

    def _msg_end(self, utc: float, tm: dict) -> tuple:
        s = self.snapshot()
        d, lines = s.get("digits", 2), []
        trades = {(t["side"], t.get("t_in")): t for t in s.get("trades") or []}
        calls = {(c["side"], c.get("t")): c for c in s.get("calls") or []}
        for x in self.data["sent"]:
            if x["kind"] == "trade":
                t = trades.get((x["side"], x["key"]))
                how = "still open" if not t or t.get("open") else f"{t['r']:+.1f} R"
            else:
                c = calls.get((x["side"], x["key"]))
                how = "still running" if not c or "how" not in c else \
                    {"target": "hit target", "stop": "hit stop", "time": "time up"}[c["how"]] + f", {c['r']:+.1f} R"
            lines.append(f"{hhmm(x['at'])} {x['label']} @ {x['entry']:.{d}f}: {how}")
        head = f"Today: {len(lines)} ready setup{'s' if len(lines) != 1 else ''}" + (":" if lines else ".")
        b0, b1 = self.data.get("balance"), s.get("balance")
        if b0 is not None and b1 is not None:
            lines.append(f"Balance {b0:,.2f} -> {b1:,.2f} ({b1 - b0:+,.2f}).")
        nxt = local(utc) + timedelta(days=3 if local(utc).weekday() == 4 else 1)
        lines.append(f"Next session: {nxt.strftime('%A')} {self._clock(self.start)}.")
        return "Session over", "\n".join([head] + lines), "default", "checkered_flag"

    # ------------------------------------------------------------ ready setups (called by the hub)
    def ready_trade(self, tr: dict, ctx: dict | None, tf: str, digits: int = 2) -> bool:
        """The indicator just entered on a closed bar."""
        f = lambda x: f"{x:.{digits}f}"
        names = LADDER.get(tf, ("H4", "H1", "M15"))
        why = [tr.get("why") or "signal"]
        if ctx:
            why.append(f"{names[0]} trend {WORD[ctx['regime']]}")
        body = (f"Entry {f(tr['entry'])} · stop {f(tr['sl0'])} · TP1 {f(tr['tp1'])} · TP2 {f(tr['tp2'])}\n"
                f"Why: {', '.join(why)}.\nUntested signal: check the chart, you decide.")
        return self._ready("trade", tr["side"], tr.get("t_in"), tr["entry"], f"{tr['side']} (indicator)",
                           f"Gold {tr['side']} setup ready ({tf} indicator)", body, tr["dir"])

    def ready_call(self, sig: dict, digits: int = 2) -> bool:
        """A BOOM / CRASH call just started."""
        f = lambda x: f"{x:.{digits}f}"
        body = (f"Entry {f(sig['entry'])} · stop {f(sig['sl'])} · target {f(sig['tp'])}, within {sig['minutes']} min\n"
                f"{', '.join(sig.get('why') or [])}.\n"
                "Untested call: check the chart, you decide.")
        return self._ready("call", sig["side"], sig.get("t"), sig["entry"], f"{sig['kind']} {sig['side']}",
                           f"Gold {sig['kind']} call: {sig['side']} now", body, sig["dir"])

    def _ready(self, kind: str, side: str, key, entry: float, label: str, title: str, body: str, d: int) -> bool:
        utc = self.clock()
        with self.lock:
            self._new_day(utc)
            sent = self.data["sent"]
            if not self.in_session(utc) or len(sent) >= READY_CAP or \
                    any(x["kind"] == kind and x["side"] == side and x["key"] == key for x in sent):
                return False
            sent.append({"kind": kind, "side": side, "key": key, "entry": entry, "label": label, "at": int(utc)})
            self._save()
        self._push(title, body, "high", "chart_with_upwards_trend" if d == 1 else "chart_with_downwards_trend")
        return True

    # ------------------------------------------------------------ plumbing
    def _push(self, title: str, body: str, prio: str, tags: str) -> None:
        if self.ntfy:
            self.ntfy.push(title, body, prio, tags)

    def _save(self) -> None:
        if not self.file:
            return
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.file.write_text(json.dumps(self.data))
        except OSError:
            pass

    def state(self) -> dict:
        utc = self.clock()
        tm = self.times(utc)
        nxt = next((tm[k] for k in ("start", "ny", "last15", "end") if tm[k] > utc), None)
        return {"window": self.window, "tz": "Tehran", "open": self.in_session(utc), "push": bool(self.ntfy),
                "sent_today": len(self.data["sent"]), "done_today": list(self.data["done"]), "next_push": nxt}
