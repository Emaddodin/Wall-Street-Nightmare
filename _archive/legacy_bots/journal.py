#!/usr/bin/env python3
"""
The book's own decisions, append-only, one JSONL line per event.

`papertrade` logs every decision it makes -- the funnel already parses those
lines, which makes them the canonical record. This module turns the same
lines into structured events and appends them to `data/book_events.jsonl`
(derived from the book path, so a test or a second book writes beside its own
state file). Nothing here changes a decision; it only remembers one.

Fields per event: kind (signal/rest/exit/state), sym, side, branch, decision,
the numbers the line carries (score, agents, tier, bar), the reason text and
the raw log line. The file is append-only: a crash mid-write costs at most
the last line, never the history. Past 64 MB the file is rotated to `.1`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

# The same needle->branch mapping the dataset classifier uses, so a branch
# is spelled the same everywhere. A line is classified by the first needle
# it carries.
SKIP_MAP = [
    # Pace states come first: their lines carry a score too, and the pace
    # wording is the decision, not the number.
    ("too early in the day", "sig_pace_early_no_sample"),
    ("the bar is", "sig_pace_wait"),
    ("the day's", "sig_pace_skip"),
    ("equity is gone", "sig_equity_zero"),
    ("colour", "sig_colour_orange"),
    ("Tesla unwired", "sig_unwired_tesla"),
    ("tier ", "sig_skip_tier"),
    ("body", "sig_fat_body"),
    ("thin_coin: ATR", "sig_thin_coin"),
    ("low_leverage", "sig_low_leverage"),
    ("against_trend", "sig_against_trend"),
    ("scored ", "sig_poor_score"),
    ("no price on Bitunix", "sig_no_price"),
    ("HALT", "sig_loss_limit"),
    ("already committed", "sig_exposure_full"),
    ("already in ", "sig_one_per_symbol"),
    ("not traded", "sig_stale_age"),
    ("absorbed as backlog", "sig_backlog_absorbed"),
    ("still fresh", "sig_catchup_fresh"),
    ("the scout's shape", "sig_council_dropped"),
    ("already acted", "sig_seen_duplicate"),
    ("no candle for the signal bar", "sig_no_candle"),
    ("from the signal price", "sig_max_entry_r"),
    ("under the exchange minimum", "live_preflight_failed"),
    ("below the exchange minimum", "live_preflight_failed"),
    ("live cap of", "sig_live_cap"),
    ("filter: the model", "filter_model"),
    ("model reloaded", "filter_reloaded"),
    ("agents", "sig_few_agents"),
]

# line prefix -> (kind, decision)
_PREFIX = [
    ("LIVE CLOSED", "exit", "close"),
    ("LIVE OPEN", "exit", "open"),
    ("COUNTER", "exit", "close"),
    ("LIQ   ", "exit", "close"),
    ("STALLED", "exit", "close"),
    ("TIMEOUT", "exit", "close"),
    ("TARGET", "exit", "close"),
    ("FLAT  ", "exit", "close"),
    ("STOP  ", "exit", "close"),
    ("YIELD ", "exit", "close"),
    ("BE    ", "state", "continue"),
    ("FROZEN", "state", "continue"),
    ("HALT  ", "state", "skip"),
    ("filter: model reloaded", "state", "pass"),
    ("fill skipped", "rest", "skip"),
    ("LATE-SKIP", "rest", "skip"),
    ("MISSED", "rest", "skip"),
    ("DROP  ", "rest", "skip"),
    ("LATE  ", "rest", "open"),
    ("FILL  ", "rest", "open"),
    ("LIMIT ", "rest", "rest"),
    ("HUNT  ", "rest", "rest"),
    ("WAIT  ", "rest", "rest"),
    ("OPEN  ", "signal", "trade"),
    ("model ", "signal", "pass"),
    ("catch-up", "signal", "trade"),
    ("drop  ", "signal", "skip"),
    ("stale ", "signal", "skip"),
    ("wait  ", "signal", "wait"),
    ("skip  ", "signal", "skip"),
]

_SYM = re.compile(r"\b(BUY|SELL|LONG|SHORT)\s+([A-Z0-9]{3,20})\b")
_PROB_REFUSED = re.compile(r"gives it (\d+(?:\.\d+)?)%")
_PROB_TAIL = re.compile(r"(\d+(?:\.\d+)?)%$")
_NUM = {
    "score": (re.compile(r"score\s+(\d+)"), int),
    "scored": (re.compile(r"scored\s+([0-9.]+)\s+of"), float),
    "agents": (re.compile(r"agents\s+(\d+)"), int),
    "tier": (re.compile(r"tier\s+(\d+)"), int),
    "pnl": (re.compile(r"pnl\s+\$([+-][0-9.]+)"), float),
    "equity": (re.compile(r"equity\s+\$([0-9.]+)"), float),
    "prob": (re.compile(r"(\d+)%%"), int),
}


def classify_line(msg: str) -> dict | None:
    """One log line -> one event, or None when it is not a decision."""
    msg = msg.strip()
    kind = decision = None
    for pfx, k, d in _PREFIX:
        if msg.startswith(pfx):
            kind, decision = k, d
            break
    if kind is None:
        return None
    if kind == "exit":
        for pfx, br in (("LIVE CLOSED", "exit_live_reconciled"),
                        ("LIVE OPEN", "exit_live_open"),
                        ("COUNTER", "exit_counter"),
                        ("LIQ   ", "exit_liquidated"),
                        ("STALLED", "exit_stalled"),
                        ("TIMEOUT", "exit_timeout"),
                        ("TARGET", "exit_target"),
                        ("FLAT  ", "exit_flat"),
                        ("STOP  ", "exit_stop"),
                        ("YIELD ", "exit_yielded")):
            if msg.startswith(pfx):
                branch = br
                break
        else:
            branch = "exit"
    elif kind == "rest":
        if msg.startswith("fill skipped"):
            if "loss limit" in msg:
                branch = "rest_fill_skipped_loss_limit"
            elif "already in " in msg:
                branch = "rest_fill_skipped_one_per_symbol"
            else:
                branch = "rest_fill_skipped_day_full"
        else:
            for pfx, br in (("DROP  ", "rest_expired_dropped"),
                            ("FILL  ", "rest_filled_at_level"),
                            ("LATE  ", "rest_late_market_fallback"),
                            ("MISSED", "rest_fill_missed_exposure"),
                            ("LATE-SKIP", "rest_fill_missed_exposure")):
                if msg.startswith(pfx):
                    branch = br
                    break
            else:
                branch = "rest_placed"
    elif kind == "signal" and decision == "pass":
        branch = "filter_pass"
    elif kind == "signal" and decision == "trade":
        branch = "sig_catchup_fresh" if "still fresh" in msg else "trade_now"
    elif kind == "signal":
        branch = "unknown"
        for needle, br in SKIP_MAP:
            if needle in msg:
                branch = br
                break
    else:  # state lines: the reason is the branch
        branch = "unknown"
        for needle, br in SKIP_MAP:
            if needle in msg:
                branch = br
                break
    ev: dict = {"kind": kind, "decision": decision, "branch": branch,
                "line": msg[:400]}
    m = _SYM.search(msg)
    if m:
        ev["side"] = m.group(1)
        ev["sym"] = m.group(2)
    for key, (rx, cast) in _NUM.items():
        got = rx.search(msg)
        if got:
            try:
                ev[key] = cast(got.group(1))
            except ValueError:
                pass
    # The model's own lines carry their probability at the very end of the
    # line ("model  TAG 23%") or mid-sentence ("the model gives it 23%").
    # The generic %% needle above never matches a single trailing %, so read
    # those two spots explicitly -- the prob is the number this whole filter
    # exists to record.
    if branch in ("filter_model", "filter_pass"):
        got = _PROB_REFUSED.search(msg) or _PROB_TAIL.search(msg)
        if got:
            try:
                ev["prob"] = int(float(got.group(1)))
            except ValueError:
                pass
    return ev


def events_for(msg: str) -> list[dict]:
    """One decision line -> its event."""
    ev = classify_line(msg)
    return [ev] if ev is not None else []


MAX_BYTES = 64 * 1024 * 1024


class JournalHandler(logging.Handler):
    """Append the book's decision lines to the journal beside the book."""

    def __init__(self, path: Path):
        super().__init__()
        self.path = Path(path)
        self._written = 0

    def emit(self, rec):
        try:
            msg = rec.getMessage()
        except Exception:
            return
        events = events_for(msg)
        if not events:
            return
        try:
            if self.path.exists() and self.path.stat().st_size > MAX_BYTES:
                try:
                    os.replace(self.path, self.path.with_suffix(".events.jsonl.1"))
                except OSError:
                    pass
            with open(self.path, "a") as f:
                for ev in events:
                    ev = {"v": 1, "ts": int(time.time()), **ev}
                    f.write(json.dumps(ev) + "\n")
        except Exception:
            # A journal that can take a poll down is worse than no journal.
            pass


def attach(logger, path: Path) -> JournalHandler:
    """Attach one handler to the book's logger; replaces any previous one.

    The book process is long-lived, but the tests and the dataset runner
    call main() many times in one process -- each attach must not stack.
    The path is derived from the book's own state file, so a test book or
    a second book journals beside itself.
    """
    for h in list(logger.handlers):
        if isinstance(h, JournalHandler):
            logger.removeHandler(h)
    h = JournalHandler(Path(path))
    logger.addHandler(h)
    return h
