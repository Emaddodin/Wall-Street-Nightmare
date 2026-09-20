"""Reading a systemd unit the way systemd reads it.

Every test that checked a flag was matching the whole file with a regex, and
these units carry long comments that quote the very flags being checked --
including the reasons a number used to be different. So a test asking for
`--sl` found "# --lev 40 and --sl 2.0 go together" from a paragraph explaining
an older setting, and reported a stop the book has not used for hours. A test
that reads a comment is not testing anything.
"""
from __future__ import annotations

import re
from pathlib import Path


def exec_start(unit_path: Path) -> str:
    """The ExecStart command line, comments and continuations resolved."""
    out, joining = [], False
    for raw in Path(unit_path).read_text().splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        if not joining and not line.startswith("ExecStart="):
            continue
        joining = True
        out.append(line[len("ExecStart="):] if line.startswith("ExecStart=")
                   else line)
        if not line.endswith("\\"):
            break
    return " ".join(x.rstrip("\\").strip() for x in out)


def flag(unit_path: Path, name: str) -> str | None:
    """The value of `--name` as the service actually runs it, or None."""
    m = re.search(rf"(?:^|\s){re.escape(name)}\s+([^\s\\]+)",
                  exec_start(unit_path))
    return m.group(1) if m else None


def has_flag(unit_path: Path, name: str) -> bool:
    return re.search(rf"(?:^|\s){re.escape(name)}(?:\s|$)",
                     exec_start(unit_path)) is not None
