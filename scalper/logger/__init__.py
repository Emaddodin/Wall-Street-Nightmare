"""JSONL logging: every trade, every rejected setup, every run (spec 24).

One line per event, append-only, safe under crash (flush per batch).  The
fields mirror the spec's required trade record; rejected setups carry the
exact failing leg plus how many legs had already passed.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


class EventLog:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._bufs: dict[str, list] = {}

    def _path(self, name: str) -> Path:
        return self.root / f"{name}.jsonl"

    def write(self, name: str, event: dict) -> None:
        self._bufs.setdefault(name, []).append(event)
        if len(self._bufs[name]) >= 20:
            self.flush(name)

    def flush(self, name: str | None = None) -> None:
        names = [name] if name else list(self._bufs)
        for n in names:
            rows = self._bufs.pop(n, [])
            if not rows:
                continue
            with open(self._path(n), "a") as fh:
                for r in rows:
                    fh.write(json.dumps(r, default=_default) + "\n")

    # ------------------------------------------------------------------
    def trade(self, rec: dict) -> None:
        self.write("trades", rec)

    def rejection(self, rec: dict) -> None:
        self.write("rejections", rec)

    def run(self, summary: dict, cfg_raw: dict, meta: dict) -> None:
        self.write("runs", {
            "ts": int(time.time() * 1000),
            "summary": summary, "config": cfg_raw, "meta": meta,
        })

    def research(self, rec: dict) -> None:
        self.write("research", rec)


def _default(o):
    if hasattr(o, "item"):
        return o.item()
    if isinstance(o, float) and o != o:
        return None
    return str(o)
