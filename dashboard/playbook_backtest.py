"""How did each ICT model of the playbook really do on gold? Replays M1 history through playbook.py exactly as the
live page reads it (closed candles only, every timeframe built from the M1 candles), scores every setup each model
armed (fill at the entry, then stop / first target / time), and prints the record by model, timeframe, grade and
killzone, before and after --split. The result is written to ~/.golddesk/playbook_stats.json, which the live
selector reads as each model's prior record (shrunk toward zero, see playbook.Track).

    python3 fetch_history.py dukascopy 2025-10-01 2026-09-30                 # gold M1 (once)
    python3 fetch_history.py dukascopy 2025-10-01 2026-09-30 XAGUSD          # silver, for the SMT model (optional)
    python3 playbook_backtest.py data/dukascopy_xauusd_m1.csv.gz --silver data/dukascopy_xagusd_m1.csv.gz \\
        --split 2026-06-01

Differences from live, on purpose: the higher-timeframe bias comes from each timeframe's structure (the live page
also uses the timeframe desks), there is no Kronos (too slow to replay a year) and no news calendar. So the
"Kronos agrees" check is neutral here; the live record (~/.golddesk/playbook_track.json) has it.
P/L in R after the spread; 1 R = the setup's own risk.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import time
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path

import ictclock as ck
from engine import Bars
from ictmodel import daily
from playbook import MODELS, STATS_FILE, Playbook, Track

SEC = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400}
WINDOW = {"M1": 600, "M5": 300, "M15": 200, "H1": 360, "H4": 120}


def load(path: str) -> list:
    """M1 rows [t, o, h, l, c] (UTC seconds) from fetch_history's CSV (gzip or plain)."""
    op = gzip.open if str(path).endswith(".gz") else open
    rows = []
    with op(Path(path).expanduser(), "rt", newline="") as f:
        rd = csv.reader(f)
        head = [h.strip().lower() for h in next(rd)]
        ix = [head.index(k) for k in ("time", "open", "high", "low", "close")]
        for r in rd:
            if r:
                t = float(r[ix[0]])
                rows.append([int(t / 1000 if t > 1e11 else t)] + [float(r[k]) for k in ix[1:]])
    rows.sort()
    return rows


def resample(m1: list, sec: int) -> list:
    out = []
    for t, o, h, l, c in m1:
        k = t - t % sec
        if out and out[-1][0] == k:
            b = out[-1]
            b[2], b[3], b[4] = max(b[2], h), min(b[3], l), c
        else:
            out.append([k, o, h, l, c])
    return out


class Series:
    """One timeframe's candles with a fast 'closed by time T' window."""

    def __init__(self, rows: list, sec: int):
        self.rows, self.sec = rows, sec
        self.close_t = [r[0] + sec for r in rows]

    def window(self, now: int, n: int) -> Bars:
        j = bisect_right(self.close_t, now)
        b = Bars(self.sec)
        for r in self.rows[max(0, j - n):j]:
            b.append(*r)
        return b


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", help="gold M1 CSV from fetch_history.py")
    ap.add_argument("--silver", help="silver M1 CSV (XAGUSD) for the SMT model and checks")
    ap.add_argument("--days", type=int, default=0, help="only the last N days (0: all)")
    ap.add_argument("--split", help="YYYY-MM-DD: report before / after this date separately")
    ap.add_argument("--step", type=int, default=5, help="read the market every N M1 candles (setups stay findable "
                                                         "for 30 M1 candles, so nothing is missed up to 30)")
    ap.add_argument("--spread", type=float, default=0.22)
    ap.add_argument("--out", default=str(STATS_FILE), help="where the live selector reads the record from")
    a = ap.parse_args()

    m1 = load(a.csv)
    if a.days:
        m1 = [r for r in m1 if r[0] >= m1[-1][0] - a.days * 86400]
    if len(m1) < 5000:
        raise SystemExit("Need at least a few days of M1 candles.")
    tfs = {"M1": Series(m1, 60)}
    for tf in ("M5", "M15", "H1", "H4"):
        tfs[tf] = Series(resample(m1, SEC[tf]), SEC[tf])
    sv = None
    if a.silver:
        from smt import SMTReader
        s1 = load(a.silver)
        sv = {"M1": Series(s1, 60)}
        for tf in ("M5", "M15", "H1"):
            sv[tf] = Series(resample(s1, SEC[tf]), SEC[tf])
        reader = SMTReader()
    utc = lambda t: t
    track = Track(None, None)
    track.KEEP_DONE = 10 ** 9
    pb = Playbook(track)
    split = int(datetime.strptime(a.split, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) if a.split else None
    start = next(i for i, r in enumerate(m1) if r[0] >= m1[0][0] + 6 * 86400)          # warm-up: a week of history
    t0, steps = time.time(), 0
    for i in range(start, len(m1), a.step):
        now = m1[i][0] + 60
        bars = {tf: s.window(now, WINDOW[tf]) for tf, s in tfs.items()}
        bars["D1"] = daily(tfs["H1"].window(now, 24 * 40), utc)
        smt = None
        if sv:
            smt = {}
            for tf in ("M1", "M5", "M15", "H1"):
                g = bars[tf]
                smt[tf] = reader.update(tf, g, sv[tf].window(now, WINDOW[tf]), last_forming=False)
        pb.update(bars, utc, smt=smt, spread=a.spread)
        steps += 1
        if steps % 500 == 0:
            done = len(track.data["done"])
            el = time.time() - t0
            left = el / (i - start + 1) * (len(m1) - i)
            print(f"\r{datetime.fromtimestamp(m1[i][0], timezone.utc):%Y-%m-%d}  setups scored {done}  "
                  f"{el / 60:.0f} min, ~{left / 60:.0f} min left", end="", flush=True)
    print()
    rows = [x for x in track.data["done"] if x.get("r") is not None]
    missed = [x for x in track.data["done"] if x.get("r") is None]
    print(f"\n{len(rows)} filled setups, {len(missed)} limits not filled, {m1[start][0]:.0f}..{m1[-1][0]:.0f} "
          f"({(m1[-1][0] - m1[start][0]) / 86400:.0f} days), step {a.step}\n")

    def table(title: str, key) -> None:
        groups = {}
        for x in rows:
            k = key(x)
            if k is not None:
                groups.setdefault(k, []).append(x["r"])
        print(title)
        print(f"  {'':34s} {'n':>5s} {'win%':>6s} {'mean R':>7s} {'net R':>7s}  95% range of the mean")
        for k in sorted(groups, key=lambda k: -len(groups[k])):
            rs = groups[k]
            n, m = len(rs), sum(rs) / len(rs)
            sd = math.sqrt(sum((r - m) ** 2 for r in rs) / (n - 1)) if n > 1 else 0.0
            lo, hi = m - 1.96 * sd / math.sqrt(n), m + 1.96 * sd / math.sqrt(n)
            print(f"  {str(k)[:34]:34s} {n:5d} {100 * sum(r > 0 for r in rs) / n:5.1f}% {m:+7.3f} {sum(rs):+7.1f}  "
                  f"[{lo:+.2f}, {hi:+.2f}]{'  <- above zero' if lo > 0 else ''}")
        print()

    name = lambda x: MODELS[x["model"]][0]
    table("By model (all)", name)
    table("By model and timeframe", lambda x: f"{name(x)} {x['tf']}")
    table("By grade", lambda x: x.get("grade"))
    table("By killzone at the setup", lambda x: ck.clock(x["t"]).get("killzone") or "outside")
    table("By part of the New York day (ictclock.DAY_MAP)", lambda x: ck.session(x["t"])["name"])
    for sess, *_ in ck.DAY_MAP:
        if any(ck.session(x["t"])["name"] == sess for x in rows):
            table(f"  {sess}: by model", lambda x, s=sess: name(x) if ck.session(x["t"])["name"] == s else None)
    if split:
        table(f"By model, before {a.split}", lambda x: name(x) if x["t"] < split else None)
        table(f"By model, from {a.split} (out of sample for anything tuned before it)",
              lambda x: name(x) if x["t"] >= split else None)
    models = {}
    for x in rows:
        m = models.setdefault(x["model"], {"n": 0, "sum_r": 0.0, "wins": 0})
        m["n"] += 1
        m["sum_r"] += x["r"]
        m["wins"] += x["r"] > 0
        ss = m.setdefault("sessions", {}).setdefault(ck.session(x["t"])["name"], {"n": 0, "sum_r": 0.0, "wins": 0})
        ss["n"] += 1
        ss["sum_r"] += x["r"]
        ss["wins"] += x["r"] > 0
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "csv": a.csv, "silver": a.silver,
           "from": m1[start][0], "to": m1[-1][0], "step": a.step, "spread": a.spread, "models": models,
           "note": "bias from structure only, no Kronos, no news: see the docstring"}
    try:
        Path(a.out).expanduser().parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).expanduser().write_text(json.dumps(out, indent=1))
        print(f"Wrote {a.out}: the live page ranks models with this record (plus its own live record).")
    except OSError as e:
        print(f"Could not write {a.out}: {e}")


if __name__ == "__main__":
    main()
