"""
xau_alpha/live/bars.py
Live 1-minute bid/ask bars built from broker quotes, exposed with EXACTLY the columns data.load_m1() gives the
backtest, so a candidate module's orders() sees the same inputs live as in research.

A bar with ts t covers quotes in [t, t+60s). It closes when the first quote of a later minute arrives, or when
close_stale() is called at least `grace_s` seconds after its minute ended (quiet market).
"""
import json
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

COLS = ["ts", "bo", "bh", "bl", "bc", "ao", "ah", "al", "ac", "spr", "spr_max", "n", "vol"]


def derive(df: pd.DataFrame) -> pd.DataFrame:
    """Same derived columns as data.load_m1() (kept identical; tests/test_live_parity.py checks it)."""
    df = df.copy()
    for c in "ohlc":
        df[c] = (df["b" + c] + df["a" + c]) / 2.0
    dt = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df["dt"] = dt
    ny = dt.dt.tz_convert("America/New_York")
    lon = dt.dt.tz_convert("Europe/London")
    df["hour"] = dt.dt.hour.astype(np.int16)
    df["mod"] = (dt.dt.hour * 60 + dt.dt.minute).astype(np.int16)
    df["ny_mod"] = (ny.dt.hour * 60 + ny.dt.minute).astype(np.int16)
    df["lon_mod"] = (lon.dt.hour * 60 + lon.dt.minute).astype(np.int16)
    df["dow"] = dt.dt.dayofweek.astype(np.int8)
    # NY date rolled at 17:00 (same as data.load_m1, which uses strftime; this vectorised form is ~100x faster)
    local = (ny.dt.tz_localize(None) + pd.Timedelta(hours=7)).values.astype("datetime64[D]")
    df["tday"] = np.datetime_as_string(local, unit="D")
    df["split"] = np.int8(2)
    return df.reset_index(drop=True)


class LiveBars:
    def __init__(self, path: Optional[Path] = None, keep: int = 12_000, grace_s: float = 2.0):
        self.path = Path(path) if path else None
        self.keep = keep
        self.grace_s = grace_s
        self.bars: list[dict] = []
        self.cur: Optional[dict] = None
        self._sprsum = 0.0
        if self.path and self.path.exists():
            self._load()

    # -- persistence ---------------------------------------------------------------------------------------------
    def _load(self):
        try:
            data = json.loads(self.path.read_text())
            # keep at most 14 days; weekends and short outages are normal gaps in the research data too
            cutoff = (time.time() - 14 * 86400) * 1000
            self.bars = [b for b in data if b["ts"] >= cutoff][-self.keep:]
        except Exception:
            self.bars = []

    def save(self):
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.bars[-self.keep:]))
        tmp.replace(self.path)

    # -- aggregation ---------------------------------------------------------------------------------------------
    def on_quote(self, bid: float, ask: float, ts_s: float) -> Optional[int]:
        """Feed one quote. Returns the ts (ms) of a bar that just CLOSED, else None."""
        if not (bid > 0 and ask > 0 and ask >= bid):
            return None
        minute = int(ts_s // 60) * 60_000
        closed = None
        if self.cur is not None and minute > self.cur["ts"]:
            closed = self._close()
        if self.cur is None:
            self.cur = {"ts": minute, "bo": bid, "bh": bid, "bl": bid, "bc": bid,
                        "ao": ask, "ah": ask, "al": ask, "ac": ask, "spr_max": ask - bid, "n": 0, "vol": 0.0}
            self._sprsum = 0.0
        c = self.cur
        c["bh"], c["bl"], c["bc"] = max(c["bh"], bid), min(c["bl"], bid), bid
        c["ah"], c["al"], c["ac"] = max(c["ah"], ask), min(c["al"], ask), ask
        c["spr_max"] = max(c["spr_max"], ask - bid)
        c["n"] += 1
        self._sprsum += ask - bid
        return closed

    def close_stale(self, now_s: float) -> Optional[int]:
        if self.cur is not None and now_s * 1000 >= self.cur["ts"] + 60_000 + self.grace_s * 1000:
            return self._close()
        return None

    def _close(self) -> int:
        c = self.cur
        c["spr"] = self._sprsum / max(c["n"], 1)
        self.bars.append(c)
        if len(self.bars) > self.keep:
            self.bars = self.bars[-self.keep:]
        self.cur = None
        return c["ts"]

    def frame(self, last: Optional[int] = None) -> pd.DataFrame:
        rows = self.bars[-last:] if last else self.bars
        df = pd.DataFrame(rows, columns=COLS)
        return derive(df)

    def __len__(self):
        return len(self.bars)
