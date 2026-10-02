"""Knowledge mesh: one record of everything the desk knows at each closed M1 candle, and a live score of who
was right.

Every closed M1 candle goes to ~/.golddesk/mesh/mesh_YYYY-MM.jsonl. Every 5th minute the line also carries the
whole trend reading (ictmodel.py features of M1 .. D1), the vote of each group, Kronos's up-probability and the
trend line's score. So the price, the reading and what happened next all sit in one place, for this live feed.

The scoreboard checks each source against the price `h` minutes later (10, 30, 60, 120), on samples h minutes apart
so no two overlap, next to two baselines (always up, the last 30 minutes' direction). It is rebuilt from the
saved scoreboard (board.json, every 30 minutes) and the candles
written after it when the dashboard starts. Nothing is ever deleted; writing pauses when the disk has under 1 GB.

Trust: a group of the trend line (Higher timeframes, Intraday structure, ICT order flow, Kronos) that has been
right or wrong on at least MIN_N independent 60-minute samples, beyond what a coin would do (3 sigma, since
many sources are checked at once and a few cross 2 sigma by luck), gets its
weight scaled between 0 (always wrong) and 2 (clearly right). Until then its weight stays as set. Nothing here
is proven; the scoreboard is how we will find out.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import time
from collections import deque
from pathlib import Path

DIR = Path.home() / ".golddesk" / "mesh"
HORIZONS = (10, 30, 60, 120)
TRUST_H = 60
MIN_N = 200
REBUILD_DAYS = 120
FEATURE_EVERY = 300
SNAPSHOT_EVERY = 1800           # the scoreboard is saved to board.json this often, so a restart reads only the tail
MIN_FREE = 1 << 30             # stop writing (never delete) when the disk has less than 1 GB free
GROUPS = ("Higher timeframes", "Intraday structure", "ICT order flow", "Kronos")


def _sign(v) -> int:
    return 0 if v is None or abs(v) < 1e-9 else (1 if v > 0 else -1)


class Score:
    def __init__(self):
        self.n, self.right = 0, 0

    def add(self, ok: bool) -> None:
        self.n += 1
        self.right += ok

    def view(self) -> dict:
        acc = self.right / self.n if self.n else None
        band = 2 * math.sqrt(0.25 / self.n) if self.n else None
        return {"n": self.n, "right": round(acc, 4) if acc is not None else None,
                "coin_band": round(band, 4) if band else None,
                "beats_coin": bool(acc is not None and self.n >= 30 and abs(acc - 0.5) > band)}


def signs(row: dict, momentum: int) -> dict:
    """The direction each source pointed at this row."""
    out = {"Always up": 1, "Last 30 min": momentum, "Trend line": _sign(row.get("s"))}
    for name, v in (row.get("g") or {}).items():
        out[name] = _sign(v)
    for k, v in (row.get("x") or {}).items():
        out[k] = _sign(v)
    return out


class Board:
    """Non-overlapping live scores per source and horizon."""

    def __init__(self):
        self.score: dict = {h: {} for h in HORIZONS}
        self.pending: dict = {h: deque() for h in HORIZONS}
        self.closes: deque = deque(maxlen=40)
        self.first_t = None
        self.rows = 0

    def feed(self, row: dict) -> None:
        t, c = row["t"], row["c"]
        self.rows += 1
        self.closes.append((t, c))
        for h in HORIZONS:
            q = self.pending[h]
            while q and t >= q[0][0] + h * 60:
                t0, c0, sg = q.popleft()
                if t > t0 + h * 60 + 600:                 # a gap (weekend, outage): no clean outcome
                    continue
                out = _sign(c - c0)
                if not out:
                    continue
                sc = self.score[h]
                for name, s in sg.items():
                    if s:
                        sc.setdefault(name, Score()).add(s == out)
        if row.get("x") is None:
            return
        if self.first_t is None:
            self.first_t = t
        past = [cc for tt, cc in self.closes if tt <= t - 1800]
        mom = _sign(c - past[-1]) if past else 0
        sg = signs(row, mom)
        for h in HORIZONS:
            if t % (h * 60) == 0:
                self.pending[h].append((t, c, sg))

    def dump(self) -> dict:
        return {"score": {str(h): {n: [s.n, s.right] for n, s in sc.items()} for h, sc in self.score.items()},
                "pending": {str(h): list(q) for h, q in self.pending.items()}, "closes": list(self.closes),
                "first_t": self.first_t, "rows": self.rows}

    def load(self, d: dict) -> None:
        for h in HORIZONS:
            for n, (cnt, right) in d["score"].get(str(h), {}).items():
                sc = self.score[h].setdefault(n, Score())
                sc.n, sc.right = cnt, right
            self.pending[h] = deque(tuple(p) for p in d["pending"].get(str(h), []))
        self.closes.extend(tuple(c) for c in d["closes"])
        self.first_t, self.rows = d["first_t"], d["rows"]

    def trust(self) -> dict:
        out = {}
        for name in GROUPS:
            s = self.score[TRUST_H].get(name)
            if not s or s.n < MIN_N:
                continue
            acc = s.right / s.n
            if abs(acc - 0.5) > 3 * math.sqrt(0.25 / s.n):
                out[name] = round(1 + max(-1.0, min(1.0, (acc - 0.5) / 0.1)), 2)
        return out

    def view(self) -> dict:
        names = sorted({n for h in HORIZONS for n in self.score[h]},
                       key=lambda n: (n not in GROUPS and not n.startswith("Desk ")
                                      and n not in ("Trend line", "10-min line", "Always up", "Last 30 min"), n))
        return {"sources": [{"name": n, "by_h": {str(h): self.score[h][n].view() if n in self.score[h] else None
                                                 for h in HORIZONS}} for n in names]}


class Mesh:
    def __init__(self, folder: Path | None = DIR):
        self.dir = folder
        self.board = Board()
        self.last_t = 0
        self.error = None
        if folder:
            try:
                self._rebuild()
            except Exception as e:                        # a damaged file never stops the dashboard
                self.error = f"Mesh rebuild: {e}"

    def _files(self) -> list:
        return sorted(self.dir.glob("mesh_*.jsonl")) if self.dir and self.dir.exists() else []

    def _rebuild(self) -> None:
        """The saved scoreboard plus the candles written after it; without one, the last REBUILD_DAYS of files."""
        since = time.time() - REBUILD_DAYS * 86400
        snap = self.dir / "board.json"
        files = self._files()[-5:]
        if snap.exists():
            d = json.loads(snap.read_text())
            self.board.load(d["board"])
            self.last_t = since = d["t"]
            files = self._files()[-2:]
        for f in files:
            with open(f) as fh:
                for line in fh:
                    try:
                        t = int(line[5:line.index(",")])          # every line starts {"t":<seconds>,
                        if t < since or t <= self.last_t:
                            continue
                        row = json.loads(line)
                    except ValueError:
                        continue
                    self.board.feed(row)
                    self.last_t = row["t"]

    def _save(self) -> None:
        tmp = self.dir / "board.json.tmp"
        tmp.write_text(json.dumps({"t": self.last_t, "board": self.board.dump()}, separators=(",", ":")))
        os.replace(tmp, self.dir / "board.json")

    def record(self, t: int, o: float, h: float, l: float, c: float, reading: dict | None = None) -> None:
        """A closed M1 candle; t is its close time. `reading` = {x, g, k, s, atr}, kept on every 5th minute."""
        if t <= self.last_t:
            return
        row = {"t": t, "o": o, "h": h, "l": l, "c": c}
        if reading and t % FEATURE_EVERY == 0:
            row["x"] = {k: round(v, 3) for k, v in reading["x"].items()}
            row["g"] = reading.get("g") or {}
            row["k"] = reading.get("k")
            row["s"] = reading.get("s")
            row["atr"] = reading.get("atr")
        self.last_t = t
        self.board.feed(row)
        if self.dir:
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                if shutil.disk_usage(self.dir).free < MIN_FREE:
                    self.error = "Mesh paused: less than 1 GB free on the disk (nothing was deleted)"
                    return
                name = time.strftime("mesh_%Y-%m.jsonl", time.gmtime(t))
                with open(self.dir / name, "a") as fh:
                    fh.write(json.dumps(row, separators=(",", ":")) + "\n")
                if t % SNAPSHOT_EVERY == 0:
                    self._save()
                self.error = None
            except OSError as e:
                self.error = f"Mesh write: {e}"

    def trust(self) -> dict:
        return self.board.trust()

    def state(self) -> dict:
        b = self.board
        v = b.view()
        v.update(horizons=list(HORIZONS), rows=b.rows, since=b.first_t, trust=b.trust(), trust_h=TRUST_H,
                 min_n=MIN_N, error=self.error, proven=False,
                 note="How often each source pointed the right way h minutes later on this live feed, on samples "
                      "that don't overlap. Inside the coin band means no better than a coin so far.")
        return v
