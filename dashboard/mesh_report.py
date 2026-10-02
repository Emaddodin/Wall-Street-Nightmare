"""Read the knowledge mesh (mesh.py): who was right, and which sources are worth more when they agree.

    python3 mesh_report.py                                   # the live files in ~/.golddesk/mesh
    python3 mesh_report.py --history data/dukascopy_xauusd_m1.csv.gz --split 2026-06-01

--history rebuilds the same rows from an M1 history file (the trend reading at every 5th minute, no Kronos), so
the live scoreboard can be compared with a year of gold. With --split only rows after it are scored.
Samples are non-overlapping per horizon; "band" is two standard deviations of a coin on that many samples.
Many sources are checked at once, so expect a few to cross the band by luck.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
from datetime import datetime, timezone

import mesh
from mesh import HORIZONS, Board, _sign


def live_rows(folder) -> list:
    rows = []
    for f in sorted(folder.glob("mesh_*.jsonl")):
        for line in open(f):
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def history_rows(path: str) -> list:
    from boom_backtest import load
    from ict_train import frames
    from ictmodel import MarketRead, consensus
    from tfdesk import desks
    m1 = load(path)
    mr = MarketRead(frames(m1))
    rows = []
    for r in m1:
        t = r[0] + 60
        row = {"t": t, "o": r[1], "h": r[2], "l": r[3], "c": r[4]}
        if t % mesh.FEATURE_EVERY == 0:
            x = mr.features(t, r[4])
            if x:
                cs = consensus(x)
                g = {p["name"]: p["score"] for p in cs["parts"]}
                g.update({f"Desk {k}": d["score"] for k, d in desks(mr, t, r[4]).items()})
                row.update(x=x, s=cs["score"], g=g)
        rows.append(row)
    return rows


def pairs(rows: list, h: int, names: list) -> list:
    """For two sources: how often right when they agree, and how often they agree."""
    closes = {r["t"]: r["c"] for r in rows}
    samples = []
    for r in rows:
        if r.get("x") is None or r["t"] % (h * 60):
            continue
        c1 = closes.get(r["t"] + h * 60)
        if c1 is None or not _sign(c1 - r["c"]):
            continue
        sg = mesh.signs(r, 0)
        samples.append((sg, _sign(c1 - r["c"])))
    out = []
    names = [n for n in names if n not in ("Always up", "Last 30 min")]
    for a, b in itertools.combinations(names, 2):
        both = [(sg[a], o) for sg, o in samples if sg.get(a) and sg.get(a) == sg.get(b)]
        if len(both) >= 30:
            acc = sum(s == o for s, o in both) / len(both)
            out.append((a, b, len(both), acc, 2 * math.sqrt(0.25 / len(both))))
    return sorted(out, key=lambda z: -abs(z[3] - 0.5) / z[4])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--history")
    ap.add_argument("--split", help="score only rows from this date (UTC)")
    a = ap.parse_args()
    rows = history_rows(a.history) if a.history else live_rows(mesh.DIR)
    if a.split:
        cut = int(datetime.fromisoformat(a.split).replace(tzinfo=timezone.utc).timestamp())
        rows = [r for r in rows if r["t"] >= cut]
    b = Board()
    for r in rows:
        b.feed(r)
    src = a.history or str(mesh.DIR)
    print(f"Knowledge mesh, {src}: {b.rows} candles, {sum(1 for r in rows if r.get('x'))} readings")
    for h in HORIZONS:
        print(f"\n{h} min later (samples {h} min apart):")
        for name, s in sorted(b.score[h].items(), key=lambda kv: -abs(kv[1].right / kv[1].n - 0.5)):
            v = s.view()
            flag = "  <- beyond a coin" if v["beats_coin"] else ""
            print(f"  {name:<20} n {v['n']:>5}  right {v['right']:6.1%}  (coin +/-{v['coin_band']:.1%}){flag}")
    print(f"\nTrust the live trend line would use now: {b.trust() or 'none yet (every group as set)'}")
    groups = [g for g in mesh.GROUPS if g in b.score[60]] + ["Trend line"] + \
        [f"Desk {k}" for k in ("D1", "H4", "H1", "M15", "M5", "M1")]
    top = [n for n, _ in sorted(b.score[60].items(), key=lambda kv: -kv[1].n)[:12]]
    names = list(dict.fromkeys(groups + top))
    print("\nWhen two sources agree, 60 min later (best first; within the band means no edge):")
    for a_, b_, n, acc, band in pairs(rows, 60, names)[:15]:
        print(f"  {a_} + {b_}: n {n}, right {acc:.1%} (coin +/-{band:.1%})")


if __name__ == "__main__":
    main()
