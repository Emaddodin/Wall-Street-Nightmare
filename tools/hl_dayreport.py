#!/usr/bin/env python3
"""Day report for the ICT Sniper paper bot (read-only, stdlib only).

Reads the systemd journal for a time window and prints: the universe
timeline, every signal/fill, the gate events (chop / news / DD / target),
data problems (429s, coins without bars) and the counters from the last
STATUS line.  Used to decide data-driven tuning (e.g. the ATR gate).

    python3 tools/hl_dayreport.py              # since 00:00 UTC today
    python3 tools/hl_dayreport.py 48h          # since 48 hours ago
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

UNIT = "stratton-oakmont-hl-sniper"
STATE = Path("/root/ict_sniper/data/state/paper.json")

RE_UNI = re.compile(r"\[UNIVERSE\] top-(\d+) by ATR%: (.*)")
RE_SIG = re.compile(r"\[SIGNAL\] (\S+) (LONG|SHORT) \| entry limit (\S+) "
                    r"\| sl (\S+) \(([\d.]+) bps\).*?\| lev (\S+)")
RE_SIG_NOL = re.compile(r"\[SIGNAL\] (\S+) (LONG|SHORT) \| entry limit (\S+) "
                        r"\| sl (\S+) \(([\d.]+) bps\)")
RE_FILL = re.compile(r"\[FILL\] (\S+) (LONG|SHORT) (\S+) @ (\S+)")
RE_CHOP = re.compile(r"\[CHOP\] (\S+) in standby")
RE_STAT = re.compile(r"\[STATUS\] signals=(\d+) fills=(\d+) exits=(\d+) \| "
                     r"open_pos=(\d+) resting=(\d+) \| realized_pnl=([+-][\d.]+) "
                     r"\| blocks=(\d+) cap_hits=(\d+)")


def journal(since: str) -> list[str]:
    out = subprocess.run(
        ["journalctl", "-u", UNIT, "--since", since, "--no-pager", "-o", "cat"],
        capture_output=True, text=True, check=False).stdout
    return [ln for ln in out.splitlines() if ln.strip()]


def hhmm(line: str) -> str:
    m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", line)
    return m.group(1)[11:16] if m else "??:??"


def main() -> int:
    since = sys.argv[1] if len(sys.argv) > 1 else "00:00"
    lines = journal(since)
    if not lines:
        print(f"no journal lines for {UNIT} since {since}")
        return 1

    uni, sigs, fills, chops = [], [], [], Counter()
    halts, target, dd, news = [], [], [], []
    data_bad = Counter()
    loaded = 0
    last_stat = None
    for ln in lines:
        if m := RE_UNI.search(ln):
            uni.append((hhmm(ln), int(m.group(1)), m.group(2)))
        elif m := RE_SIG.search(ln):
            sigs.append((hhmm(ln), m.group(1), m.group(2), float(m.group(5)),
                         m.group(6)))
        elif m := RE_SIG_NOL.search(ln):
            sigs.append((hhmm(ln), m.group(1), m.group(2), float(m.group(5)),
                         "-"))
        elif m := RE_FILL.search(ln):
            fills.append((hhmm(ln), m.group(1), m.group(2), m.group(4)))
        elif m := RE_CHOP.search(ln):
            chops[m.group(1)] += 1
        elif "[DD-HALT]" in ln:
            dd.append(ln)
        elif "[TARGET]" in ln:
            target.append(ln)
        elif "[NEWS]" in ln:
            news.append(ln)
        elif "[DATA]" in ln:
            msg = ln.split("[DATA]", 1)[1].strip()
            if any(k in msg for k in ("failed", "NO bars", "429",
                                      "retrying")):
                data_bad[msg[:70]] += 1
            else:
                loaded += 1
        elif "[HALT]" in ln:
            halts.append(ln)
        if m := RE_STAT.search(ln):
            last_stat = m.groups()

    print(f"=== ICT Sniper day report | {UNIT} | since {since} "
          f"| generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC ===")
    print(f"\n-- universe timeline ({len(uni)} refreshes) --")
    for t, n, names in uni:
        top = ", ".join(x.split("(")[0] for x in names.split(", ")[:6])
        print(f"  {t}  {n:3d} coins  ({top}...)")
    if uni:
        sizes = [n for _, n, _ in uni]
        print(f"  -> size min {min(sizes)} / max {max(sizes)} / "
              f"last {sizes[-1]}")

    print(f"\n-- signals ({len(sigs)}) --")
    for t, coin, side, slb, lev in sigs:
        print(f"  {t}  {coin:9s} {side:5s} sl {slb:6.1f} bps  lev {lev}")
    if not sigs:
        print("  (none)")

    print(f"\n-- fills ({len(fills)}) --")
    for t, coin, side, px in fills:
        print(f"  {t}  {coin:9s} {side:5s} @ {px}")

    print("\n-- gates --")
    print(f"  chop standby: {sum(chops.values())} "
          f"({', '.join(f'{k}x{v}' for k, v in chops.most_common(6)) or 'none'})")
    print(f"  daily-target halts: {len(target)} | DD halts: {len(dd)} "
          f"| symbol halts: {len(halts)} | news events: {len(news)}")

    print("\n-- data health --")
    print(f"  clean universe loads: {loaded}")
    if data_bad:
        for msg, n in data_bad.most_common(6):
            print(f"  {n:3d}x PROBLEM: {msg}")
    else:
        print("  no 429s, no coins left without bars")

    if last_stat:
        s, f, e, op, rest, pnl, blocks, caps = last_stat
        print(f"\n-- last STATUS --\n  signals={s} fills={f} exits={e} "
              f"open={op} resting={rest} realized_pnl={pnl} "
              f"blocks={blocks} cap_hits={caps}")

    try:
        st = json.loads(STATE.read_text())
        print(f"\n-- paper book --\n  equity {st.get('equity')} "
              f"(start {st.get('start_equity')}) | day open "
              f"{st.get('day_start_equity')} | open positions "
              f"{len(st.get('positions') or [])}")
    except OSError as e:
        print(f"\n-- paper book --\n  unreadable: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
