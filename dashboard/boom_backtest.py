"""Backtest the smart money BOOM / CRASH (orchestra.py) against the current scalper, on M1 gold history.

    python3 boom_backtest.py data/dukascopy_xauusd_m1.csv.gz --split 2026-06-01
    python3 boom_backtest.py data/litefinance_xauusd_m1.csv.gz
    python3 boom_backtest.py data/dukascopy_xauusd_m1.csv.gz --kronos kronos_votes.csv   # with Kronos votes

Setups are found on M5 candles (UTC), the trend on H4 / H1 built from the same history, and every trade is
played on the M1 candles after the entry (stop first when one minute touches both). Buys pay the spread on
entry, sells on exit. Results are in R (multiples of the stop distance) and in dollars per 0.01 lot.
--kronos takes a CSV of time,up_prob (time = the M5 candle the forecast was made after); setups without a
forecast get no Kronos vote. --dump writes the setup times so Kronos can be run on exactly those candles.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import math
from datetime import datetime, timezone
from pathlib import Path

import orchestra as oc
from engine import Bars, Params, Spec, run_backtest


def load(path: str) -> list:
    op = gzip.open if path.endswith(".gz") else open
    rows = []
    with op(Path(path).expanduser(), "rt", newline="") as f:
        rd = csv.reader(f)
        next(rd)
        for r in rd:
            if r:
                rows.append([int(float(r[0])), float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                             float(r[5]) if len(r) > 5 and r[5] else 0.0])
    rows.sort(key=lambda r: r[0])
    return rows


def aggregate(rows: list, sec: int) -> Bars:
    b = Bars(sec)
    for t, o, h, l, c, v in rows:
        k = t - t % sec
        if len(b) and b.t[-1] == k:
            b.h[-1], b.l[-1], b.c[-1], b.v[-1] = max(b.h[-1], h), min(b.l[-1], l), c, b.v[-1] + v
        else:
            b.append(k, o, h, l, c, v)
    return b


def play(m1: list, start: int, d: int, entry: float, sl: float, tp: float, spread: float, until: int):
    """Walk M1 candles from index start. -> (exit price, how, end index)."""
    k = start
    while k < len(m1) and m1[k][0] < until:
        _, o, h, l, c, _ = m1[k]
        if d == 1:
            if l <= sl:
                return (min(o, sl), "stop", k)
            if h >= tp:
                return (tp, "target", k)
        else:
            if h + spread >= sl:
                return (max(o + spread, sl), "stop", k)
            if l + spread <= tp:
                return (tp, "target", k)
        k += 1
    k = min(k, len(m1)) - 1
    return (m1[k][4] + (spread if d == -1 else 0.0), "time", k)


def run(m1: list, m5: Bars, trend: oc.Trend | None, a, kronos: dict, tp_r: float, confirm: str,
        use_trend: bool, need: int = oc.NEED_SCORE, collect: list | None = None, session: bool = True,
        kronos_cut: float | None = None, **setup_kw) -> list:
    st = oc.Setups(300, confirm=confirm, **setup_kw)
    t1 = [r[0] for r in m1]
    from bisect import bisect_left
    trades, busy_until = [], 0
    for j in range(len(m5)):
        done = st.add(m5.t[j], m5.o[j], m5.h[j], m5.l[j], m5.c[j])
        t_close = m5.t[j] + 300
        for d, s in done.items():
            if session and not oc.in_session(t_close):
                continue
            if collect is not None:
                collect.append(m5.t[j])
            if t_close < busy_until:
                continue
            v = trend.votes(t_close, kronos.get(m5.t[j])) if trend else {}
            bias = oc.Trend.bias(v, need) if v else 0
            if use_trend and bias != d:
                continue
            if kronos_cut is not None:
                up = kronos.get(m5.t[j])
                if up is None or (up if d == 1 else 1 - up) < kronos_cut:
                    continue
            k = bisect_left(t1, t_close)
            if k >= len(m1) or m1[k][0] - t_close > 600:
                continue                              # market closed after the setup
            entry = m1[k][1] + (a.spread if d == 1 else 0.0)
            p = oc.plan(s, entry, a.spread, tp_r)
            if not p:
                continue
            px, how, end = play(m1, k, d, entry, p["sl"], p["tp"], a.spread, t_close + oc.MAX_MIN * 60)
            usd = (px - entry) * d
            busy_until = m1[end][0] + 60
            trades.append({"time": datetime.fromtimestamp(m5.t[j], timezone.utc).strftime("%Y-%m-%d %H:%M"),
                           "t": m5.t[j], "kind": oc.NAME[d], "dir": d, "entry": round(entry, 2),
                           "sl": round(p["sl"], 2), "tp": round(p["tp"], 2), "exit": round(px, 2), "how": how,
                           "r": round(usd / p["risk"], 3), "usd_0.01lot": round(usd, 2),
                           "risk": round(p["risk"], 2), "pattern": s["pattern"],
                           "bias": bias, "votes": "; ".join(f"{k_} {x:+d}" for k_, x in v.items()),
                           "kronos": kronos.get(m5.t[j])})
    return trades


def stats(tr: list) -> dict:
    n = len(tr)
    if not n:
        return {"n": 0}
    rs = [x["r"] for x in tr]
    w = [r for r in rs if r > 0]
    gl = -sum(r for r in rs if r <= 0)
    avg = sum(rs) / n
    sd = math.sqrt(sum((r - avg) ** 2 for r in rs) / max(1, n - 1))
    eq = peak = dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return {"n": n, "win": len(w) / n, "avg_r": avg, "net_r": sum(rs), "pf": sum(w) / gl if gl else math.inf,
            "t": avg / (sd / math.sqrt(n)) if sd > 0 else 0.0, "max_dd_r": dd,
            "usd": sum(x["usd_0.01lot"] for x in tr),
            "how": {h: sum(x["how"] == h for x in tr) for h in ("target", "stop", "time")}}


def line(name: str, s: dict) -> str:
    if not s.get("n"):
        return f"  {name:<34} no trades"
    return (f"  {name:<34} {s['n']:>4} trades  win {s['win']:5.1%}  avg {s['avg_r']:+.3f}R  net {s['net_r']:+7.1f}R  "
            f"PF {s['pf']:.2f}  t {s['t']:+.2f}  maxDD {s['max_dd_r']:.1f}R  ${s['usd']:+.0f}/0.01lot  "
            f"T/S/time {s['how']['target']}/{s['how']['stop']}/{s['how']['time']}")


def scalper(m1: list, a, lo: int, hi: int) -> dict:
    """The current scalper (engine.py, M5 entries, H4 / H1 / M15 ladder) on the same candles."""
    rows = [r for r in m1 if r[0] < hi]
    m5, h4, h1, m15 = (aggregate(rows, s) for s in (300, 14400, 3600, 900))
    p = Params()
    p.fixed_spread, p.commission = a.spread, 0.0
    res = run_backtest(m5, h4, h1, m15, p, Spec(), 10000.0, lambda t: 0)
    tr = [x for x in res["trades"] if x["t_bar"] >= lo]
    return stats([{"r": x["r"], "how": "time", "usd_0.01lot": x["pnl"] / max(x["lots"], 0.01) / 100} for x in tr])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--split", help="YYYY-MM-DD: report before (rules chosen here) and after (out of sample)")
    ap.add_argument("--spread", type=float, default=0.22)
    ap.add_argument("--kronos", help="CSV time,up_prob")
    ap.add_argument("--dump", help="write the M5 candle times of every setup to this file")
    ap.add_argument("--out", default="boom_backtest_trades.csv")
    ap.add_argument("--no-scalper", action="store_true")
    ap.add_argument("--grid", action="store_true", help="also try other targets / confirmations")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=N",
                    help="setup layer setting, e.g. sweep_pivot=3 choch_within=24 (see orchestra.Setups)")
    a = ap.parse_args()
    kw = {k: (int(v) if v.isdigit() else v) for k, v in (x.split("=", 1) for x in a.set)}

    m1 = load(a.csv)
    m5, h1, h4 = aggregate(m1, 300), aggregate(m1, 3600), aggregate(m1, 14400)
    trend = oc.Trend(h4, h1)
    kronos = {}
    if a.kronos:
        with open(a.kronos, newline="") as f:
            for r in csv.DictReader(f):
                kronos[int(float(r["time"]))] = float(r["up_prob"])
    span = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")
    print((f"Setup settings: {kw}\n" if kw else "") + f"{a.csv}: {len(m1)} M1 bars, {span(m1[0][0])} to {span(m1[-1][0])}; spread {a.spread}"
          + (f"; {len(kronos)} Kronos forecasts" if kronos else ""))
    warm = m1[0][0] + 45 * 86400                     # the H4 EMA 200 needs about a month and a half
    cut = int(datetime.fromisoformat(a.split).replace(tzinfo=timezone.utc).timestamp()) if a.split else None
    periods = [("all", warm, 2 ** 40)] if not cut else [("in sample", warm, cut), ("out of sample", cut, 2 ** 40)]

    variants = [("SMC + trend (the new BOOM/CRASH)", oc.TP_R, oc.CONFIRM, True),
                ("SMC setup, no trend filter", oc.TP_R, oc.CONFIRM, False),
                ("SMC + trend, enter on touch", oc.TP_R, "touch", True)]
    if a.grid:
        variants += [(f"SMC + trend, target {r}R", r, oc.CONFIRM, True) for r in (1.0, 1.5, 3.0)]
    collect = [] if a.dump else None
    results = {}
    for name, r, conf, use in variants:
        results[name] = run(m1, m5, trend, a, kronos, r, conf, use, collect=collect if not results else None, **kw)
    if kronos:
        k_only = [x for x in results[variants[0][0]] if x["kronos"] is not None]
        k_agree = [x for x in k_only if (x["kronos"] - 0.5) * x["dir"] > 0]
        results["  ...with a Kronos forecast"] = k_only
        results["  ...and Kronos agreeing (up_prob side)"] = k_agree
        for cut in (0.5, 0.6, 0.7):
            results[f"SMC setup + Kronos only, >= {cut:.0%} of paths"] = run(
                m1, m5, trend, a, kronos, oc.TP_R, oc.CONFIRM, False, kronos_cut=cut, **kw)
    for label, lo, hi in periods:
        print(f"\n{label} ({span(lo)} to {span(min(hi, m1[-1][0]))}):")
        for name, tr in results.items():
            print(line(name, stats([x for x in tr if lo <= x["t"] < hi])))
        if not a.no_scalper:
            print(line("current scalper (engine.py, M5)", scalper(m1, a, lo, hi)))
    main_tr = results[variants[0][0]]
    with open(a.out, "w", newline="") as f:
        if main_tr:
            w = csv.DictWriter(f, fieldnames=list(main_tr[0]))
            w.writeheader()
            w.writerows(main_tr)
    if a.dump and collect is not None:
        with open(a.dump, "w") as f:
            f.write("time\n" + "\n".join(str(t) for t in sorted(set(collect))) + "\n")
        print(f"\n{len(set(collect))} setup candles written to {a.dump}")
    print(f"\nTrades: {Path(a.out).resolve()}")


if __name__ == "__main__":
    main()
