"""
xau_alpha/live/news.py
Deterministic economic-calendar blackout for live trading (the only news guard allowed on the entry path;
SYNTHESIS.md section 4). Same rule as data.news_block_mask() in backtests.

FAIL CLOSED: if the calendar has no HIGH rows in the next `horizon_days` (stale file) and no fresh live feed rows
were supplied, entries are blocked. Live feed rows (e.g. from PoliticianBrain's ForexFactory poll) can be merged in
with add_events().
"""
import time
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

DEFAULT_CAL = Path(__file__).resolve().parents[1] / "data/econ_calendar.csv"


class NewsBlackout:
    def __init__(self, path: Path = DEFAULT_CAL, horizon_days: float = 7.0):
        self.path = Path(path)
        self.horizon_ms = horizon_days * 86_400_000
        self.events: list[tuple[int, str, str]] = []      # (ts_ms, impact, name)
        self.feed_fresh_until_ms = 0
        self.reload()

    def reload(self):
        ev = []
        if self.path.exists():
            cal = pd.read_csv(self.path)
            ts = pd.to_datetime(cal["ts_utc"], utc=True).astype("int64") // 1_000_000
            ev = list(zip(ts.tolist(), cal["impact"].astype(str).tolist(), cal["event"].astype(str).tolist()))
        self.events = sorted(ev)

    def add_events(self, rows: Iterable[tuple[int, str, str]], fresh_for_s: float = 3 * 86400):
        """Merge live-feed rows (ts_ms, impact, name). Marks the feed fresh for `fresh_for_s`."""
        s = set(self.events)
        for r in rows:
            s.add((int(r[0]), str(r[1]).upper(), str(r[2])))
        self.events = sorted(s)
        self.feed_fresh_until_ms = int(time.time() * 1000 + fresh_for_s * 1000)

    def coverage_ok(self, now_ms: int) -> bool:
        if now_ms < self.feed_fresh_until_ms:
            return True
        last = max((t for t, imp, _ in self.events if imp == "HIGH"), default=0)
        return last >= now_ms + self.horizon_ms * 0.5          # at least half the horizon is covered

    def blocked(self, now_ms: int, before_min: float, after_min: float, impacts=("HIGH",)) -> Optional[str]:
        """Reason string if entries must be blocked at now_ms, else None."""
        if not self.coverage_ok(now_ms):
            return "calendar stale (fail closed)"
        lo, hi = now_ms - after_min * 60_000, now_ms + before_min * 60_000
        for t, imp, name in self.events:
            if t > hi:
                break
            if t >= lo and imp in impacts:
                return f"news blackout: {name} at {pd.Timestamp(t, unit='ms', tz='UTC'):%H:%M}Z"
        return None
