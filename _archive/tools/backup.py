#!/usr/bin/env python3
"""
Nightly backup of the irreplaceable record.

The signals, the outcomes, the journal and the book are the experiment --
hundreds of megabytes of live-recorded truth that no code can regenerate.
One tar a day, seven kept, written atomically.

    python3 tools/backup.py [--keep 7] [--dir data]
"""
from __future__ import annotations

import argparse
import os
import tarfile
import time
from pathlib import Path

BOT = Path(os.environ.get("TBT_BOT") or Path(__file__).resolve().parent.parent)
FILES = ("signals.jsonl", "outcomes.jsonl", "paper.json",
         "book_events.jsonl", "paper.events.jsonl", "watch_history.jsonl",
         "council_history.jsonl", "tesla.jsonl")
# Reset archives: the books that were replaced still hold the trade
# history, and a reset must not make that history unbacked.
ARCHIVES = ("paper-archive-*.json", "paper-events-archive-*.jsonl",
            "casestudy-*.jsonl", "paper-before-*.json",
            "casestudy-before-*.jsonl",
            "beast-archive-*.json", "beast-events-archive-*.jsonl")


def make_backup(data_dir: Path, keep: int = 7) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = data_dir / f"backup-{stamp}.tar.gz"
    tmp = out.with_suffix(".tmp")
    with tarfile.open(tmp, "w:gz") as tar:
        for name in FILES:
            f = data_dir / name
            if f.exists():
                tar.add(f, arcname=name)
        seen: set[str] = set()
        for pat in ARCHIVES:
            for f in sorted(data_dir.glob(pat)):
                if f.name not in seen:
                    seen.add(f.name)
                    tar.add(f, arcname=f.name)
    os.replace(tmp, out)
    prune(data_dir, keep)
    return out


def prune(data_dir: Path, keep: int = 7) -> None:
    backs = sorted(data_dir.glob("backup-*.tar.gz"))
    for old in backs[:-keep] if keep > 0 else []:
        try:
            old.unlink()
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=7)
    ap.add_argument("--dir", default=str(BOT / "data"))
    a = ap.parse_args()
    out = make_backup(Path(a.dir), a.keep)
    print(f"  backed up -> {out.name} "
          f"({out.stat().st_size / 1e6:.1f} MB, {a.keep} kept)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
