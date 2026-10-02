"""Backtest the M1 ICT entries (ict_entries.py) on M1 gold history, with and without the trend consensus.

    python3 ict_backtest.py data/dukascopy_xauusd_m1.csv.gz --split 2026-06-01
    python3 ict_backtest.py data/litefinance_xauusd_m1.csv.gz

Orders are limits at the fair value gap's 50%: a buy fills when the ask trades down to it (bid low <= entry -
spread), a sell when the bid trades up to it. A candle that fills and touches the stop counts as a stop.
One trade at a time. Results in R and in dollars per 0.01 lot after the spread.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone

import ict_entries as ie
from boom_backtest import load, stats, line
from ict_train import frames
from ictmodel import MarketRead, consensus


def m5_before(t: int) -> int:
    """The last M5 candle closed when the M1 candle starting at t closes (its start time)."""
    return (t + 60) // 300 * 300 - 300


def run(m1: list, spread: float, mr: MarketRead | None, gate: str, tp_r: float, killzones: bool,
        kronos: dict | None = None, dump: set | None = None) -> list:
    ent = ie.M1Entries(killzones=killzones)
    trades, order, pos = [], None, None
    for i, (t, o, h, l, c, v) in enumerate(m1):
        if pos:                                               # manage the open trade on this candle
            d = pos["dir"]
            hit = None
            if d == 1:
                hit = ("stop", pos["sl"]) if l <= pos["sl"] else (("target", pos["tp"]) if h >= pos["tp"] else None)
            else:
                hit = ("stop", pos["sl"]) if h + spread >= pos["sl"] else (("target", pos["tp"]) if l + spread <= pos["tp"] else None)
            if not hit and t - pos["t_in"] >= ie.MAX_MIN * 60:
                hit = ("time", c + (spread if d == -1 else 0.0))
            if hit:
                usd = (hit[1] - pos["entry"]) * d
                pos.update(how=hit[0], exit=round(hit[1], 2), usd=round(usd, 2), r=round(usd / pos["risk"], 3),
                           **{"usd_0.01lot": round(usd, 2)})
                trades.append(pos)
                pos = None
        if order and not pos:                                 # a resting limit order
            d = order["dir"]
            filled = (l <= order["entry"] - spread) if d == 1 else (h >= order["entry"])
            if filled:
                p = ie.plan(d, order["entry"], order["ext"], spread, tp_r)
                if p:
                    pos = {"time": datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M"), "t": t,
                           "t_in": t, "dir": d, "entry": round(order["entry"], 2), "sl": p["sl"], "tp": p["tp"],
                           "risk": p["risk"], "pool": order["name"], "score": order.get("score")}
                    if (l <= p["sl"]) if d == 1 else (h + spread >= p["sl"]):     # the same minute ran to the stop
                        pos.update(how="stop", exit=round(p["sl"], 2), usd=round(-p["risk"], 2), r=-1.0,
                                   **{"usd_0.01lot": round(-p["risk"], 2)})
                        trades.append(pos)
                        pos = None
                order = None
                ent.take(d)
        for kind, e in ent.add(t, o, h, l, c):
            if kind == "cancel" and order and order["dir"] == e["dir"]:
                order = None
            if kind == "order" and dump is not None:
                dump.add(m5_before(t))
            if kind == "order" and not pos and not order:
                if kronos is not None:
                    up = kronos.get(m5_before(t))
                    if up is None or (up - 0.5) * e["dir"] <= 0:
                        continue
                if gate != "none" and mr is not None:
                    x = mr.features(t + 60, c)
                    cs = consensus(x) if x else None
                    s = cs["score"] if cs else 0.0
                    if gate.startswith("htf"):
                        s = cs["parts"][0]["score"] if cs else 0.0
                    if gate in ("agree", "htf") and s * e["dir"] <= 0:
                        continue
                    if gate == "htf_against" and s * e["dir"] >= 0:
                        continue
                    if gate == "strong" and s * e["dir"] < 0.35:
                        continue
                    if gate == "against" and s * e["dir"] >= 0:
                        continue
                    e["score"] = s
                order = dict(e)
    return trades


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--split")
    ap.add_argument("--spread", type=float, default=0.22)
    ap.add_argument("--out", default="ict_backtest_trades.csv")
    ap.add_argument("--dump", help="write the M5 candle before every order, for boom_kronos_votes.py")
    ap.add_argument("--kronos", help="CSV time,up_prob from boom_kronos_votes.py: adds Kronos-gated variants")
    a = ap.parse_args()
    m1 = load(a.csv)
    mr = MarketRead(frames(m1))
    warm = m1[0][0] + 45 * 86400
    cut = int(datetime.fromisoformat(a.split).replace(tzinfo=timezone.utc).timestamp()) if a.split else None
    periods = [("all", warm, 2 ** 40)] if not cut else [("in sample", warm, cut), ("out of sample", cut, 2 ** 40)]
    variants = [("killzones, any trend", "none", ie.TP_R, True),
                ("killzones, consensus agrees", "agree", ie.TP_R, True),
                ("killzones, consensus strong", "strong", ie.TP_R, True),
                ("killzones, consensus against", "against", ie.TP_R, True),
                ("killzones, higher timeframes agree", "htf", ie.TP_R, True),
                ("killzones, higher timeframes against", "htf_against", ie.TP_R, True),
                ("all hours, any trend", "none", ie.TP_R, False),
                ("all hours, consensus agrees", "agree", ie.TP_R, False),
                ("all hours, higher timeframes agree", "htf", ie.TP_R, False),
                ("killzones, target 1.5R", "none", 1.5, True),
                ("killzones, target 3R", "none", 3.0, True)]
    dump = set() if a.dump else None
    res = {n: run(m1, a.spread, mr, g, r, kz, dump=dump if not k else None) for k, (n, g, r, kz) in enumerate(variants)}
    if a.kronos:
        votes = {}
        with open(a.kronos, newline="") as f:
            for row in csv.DictReader(f):
                votes[int(float(row["time"]))] = float(row["up_prob"])
        res["killzones, Kronos agrees"] = run(m1, a.spread, mr, "none", ie.TP_R, True, votes)
        res["killzones, higher timeframes + Kronos agree"] = run(m1, a.spread, mr, "htf", ie.TP_R, True, votes)
    if dump is not None:
        with open(a.dump, "w") as f:
            f.write("time\n" + "\n".join(str(t) for t in sorted(dump)) + "\n")
    span = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")
    print(f"{a.csv}: {len(m1)} M1 bars, {span(m1[0][0])} to {span(m1[-1][0])}, spread {a.spread}")
    for label, lo, hi in periods:
        print(f"\n{label} ({span(lo)} to {span(min(hi, m1[-1][0]))}):")
        for n, tr in res.items():
            print(line(n, stats([x for x in tr if lo <= x["t"] < hi])))
    main_tr = res[variants[0][0]]
    if main_tr:
        with open(a.out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(main_tr[0]))
            w.writeheader()
            w.writerows(main_tr)


if __name__ == "__main__":
    main()
