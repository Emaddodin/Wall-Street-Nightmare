"""Is Kronos's "direction right" score real, and which settings make it better? A research run on gold.

  collect  Replays the last --days days of gold. At evenly spread, non-overlapping points (each forecast
           window ends before the next one starts, so every point is a separate test) Kronos draws many
           sample paths for each setting below, and every path is saved. Resumable, runs at low priority.
  report   Scores the saved paths against what gold really did, next to simple rules anyone could use
           (always up, last move continues, H1 / H4 trend), and tries the levers that need no new
           forecasts: how many paths, mean vs median vs vote, calling only when the paths agree, and
           calling only with the H1 / H4 trend. Settings are picked on the first half of the period and
           judged on the second half only.

    python3 kronos_research.py collect --repo /root/kronos/Kronos --size base --days 60 --points 240
    python3 kronos_research.py report                    # any time, also while collect is running

The live Gold Desk forecast is the "live" setting: 400 M5 bars in, 24 bars (2 hours) out, T 1.0,
top_p 0.9, and the call is the sign of the average of 10 paths. Nothing here touches the dashboard.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import os
import statistics
import time
from pathlib import Path

OUT = Path.home() / ".golddesk" / "kronos_research"
SEC = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400}
SPREAD = 0.22                      # LiteFinance gold, round trip, price units ($ per 0.01 lot)
# name -> input timeframe ("base" = the entry timeframe), bars in, temperature, top_p, paths
CONFIGS = {
    "live":   dict(tf="base", ctx=400, T=1.0, top_p=0.9, paths=30),
    "ctx512": dict(tf="base", ctx=512, T=1.0, top_p=0.9, paths=16),
    "ctx160": dict(tf="base", ctx=160, T=1.0, top_p=0.9, paths=16),
    "T0.6":   dict(tf="base", ctx=400, T=0.6, top_p=0.9, paths=16),
    "T1.3":   dict(tf="base", ctx=400, T=1.3, top_p=1.0, paths=16),
    "M15in":  dict(tf="M15", ctx=400, T=1.0, top_p=0.9, paths=16),
}


# ---------------------------------------------------------------- data

def save_bars(path: Path, rows: list) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "o", "h", "l", "c", "v"])
        w.writerows(rows)


def read_bars(path: Path) -> list:
    with open(path, newline="") as f:
        rd = csv.reader(f)
        next(rd)
        return [[int(r[0])] + [float(x) for x in r[1:6]] for r in rd]


def load_data(out: Path, tf: str, days: float) -> dict:
    """Bars of the entry timeframe and the ones above it. Downloaded once, then reused, so a resumed run
    and the report see exactly the same history."""
    need = [tf, "M15", "H1", "H4"] if tf == "M5" else [tf, "H4"]
    bars = {}
    for name in dict.fromkeys(need):
        f = out / f"bars_{name}.csv"
        if not f.exists():
            from kronos_backtest import load_litefinance
            extra = {"M5": 8, "M15": 20, "H1": 40, "H4": 120}.get(name, 30)   # context and EMA warm-up
            print(f"Downloading {days + extra:.0f} days of {name} gold from LiteFinance...", flush=True)
            save_bars(f, load_litefinance(days + extra, name))
        bars[name] = read_bars(f)
    return bars


def pick_points(rows: list, sec: int, h: int, days: float, n: int, align: int) -> list:
    """Bar indexes to forecast from: the next h bars have no gap, windows do not overlap, the bar closes on
    an `align`-second boundary (so an M15 input lines up), and 512 bars of context exist before it."""
    t_end = rows[-1][0]
    cand, last_t = [], -10 ** 12
    for i in range(512, len(rows) - h - 1):
        t = rows[i][0]
        if t < t_end - days * 86400 or (t + sec) % align or t < last_t + h * sec:
            continue
        if rows[i + h + 1][0] - t != (h + 1) * sec:
            continue
        cand.append(i)
        last_t = t
    if len(cand) > n > 1:
        cand = [cand[round(j * (len(cand) - 1) / (n - 1))] for j in range(n)]
    return cand


def closed_upto(rows: list, sec: int, t_close: int) -> int:
    """Index of the last bar of `rows` that has closed by t_close, or -1."""
    return bisect.bisect_right([r[0] for r in rows], t_close - sec) - 1


# ---------------------------------------------------------------- collect

def collect(a) -> None:
    try:
        os.nice(19)                                    # the live dashboard comes first
    except OSError:
        pass
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    bars = load_data(out, a.tf, a.days)
    rows, sec, h = bars[a.tf], SEC[a.tf], a.horizon
    pts = pick_points(rows, sec, h, a.days, a.points, 900 if a.tf == "M5" else sec)
    cfgs = [c for c in a.configs.split(",") if c in CONFIGS and (CONFIGS[c]["tf"] == "base" or a.tf == "M5")]
    meta = {"tf": a.tf, "horizon": h, "size": a.size, "points": [rows[i][0] for i in pts], "configs": cfgs}
    (out / "meta.json").write_text(json.dumps(meta))
    done = set()
    res_file = out / "paths.jsonl"
    if res_file.exists():
        for ln in res_file.read_text().splitlines():
            try:
                r = json.loads(ln)
                done.add((r["cfg"], r["t"]))
            except ValueError:
                pass
    span = f"{time.strftime('%Y-%m-%d', time.gmtime(rows[pts[0]][0]))} to " \
           f"{time.strftime('%Y-%m-%d', time.gmtime(rows[pts[-1]][0]))}"
    print(f"{len(pts)} test points, {span}, {h} {a.tf} bars ahead; settings: {', '.join(cfgs)}; "
          f"{len(done)} forecasts already saved.", flush=True)

    import torch
    from kronos_signal import Kronos
    k = Kronos(a.repo, a.size, 512, h, 1)
    torch.set_num_threads(a.threads)
    print(f"Kronos-{a.size} loaded, {a.threads} threads.", flush=True)
    with open(res_file, "a") as f:
        for cfg in cfgs:
            c = CONFIGS[cfg]
            tf_in = a.tf if c["tf"] == "base" else c["tf"]
            src, s_in = bars[tf_in], SEC[tf_in]
            h_in = h * sec // s_in
            todo = [i for i in pts if (cfg, rows[i][0]) not in done]
            t0, n = time.time(), 0
            for i in todo:
                t = rows[i][0]
                j = closed_upto(src, s_in, t + sec)
                ctx = src[max(0, j - c["ctx"] + 1):j + 1]
                if j < 0 or src[j][0] + s_in != t + sec or len(ctx) < min(c["ctx"], 100):
                    continue                           # the input timeframe has a gap here
                torch.manual_seed(a.seed * 1_000_003 + t % 1_000_000_007)
                df, ts = k._frame(ctx)
                fut = k._future(ctx[-1][0], s_in, h_in)
                t1 = time.time()
                outs = k.pred.predict_batch([df] * c["paths"], [ts] * c["paths"], [fut] * c["paths"],
                                            pred_len=h_in, T=c["T"], top_p=c["top_p"], sample_count=1,
                                            verbose=False)
                paths = [[round(float(x), 2) for x in o["close"].values] for o in outs]
                f.write(json.dumps({"cfg": cfg, "t": t, "last": ctx[-1][4], "paths": paths,
                                    "seconds": round(time.time() - t1, 1)}) + "\n")
                f.flush()
                n += 1
                per = (time.time() - t0) / n
                msg = (f"{cfg}: {n}/{len(todo)}, {per:.0f} s per forecast, "
                       f"{per * (len(todo) - n) / 3600:.1f} h left for this setting")
                (out / "progress.txt").write_text(msg + "\n")
                print("  " + msg, end="\r", flush=True)
                if n % 25 == 0:
                    refresh(a)
            print(f"\n{cfg}: done.", flush=True)
            refresh(a)
    print(f"All settings done. Next: python3 kronos_research.py report --out {out}")


# ---------------------------------------------------------------- report

def refresh(a) -> None:
    """Keep report.md current while collecting, so it can be read before the run ends."""
    try:
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            report(a)
    except Exception as e:                             # a report problem must not stop the run
        print(f"\n(report not updated: {e})", flush=True)


def ema(xs: list, n: int) -> list:
    k, out = 2 / (n + 1), []
    for x in xs:
        out.append(x if not out else out[-1] + k * (x - out[-1]))
    return out


def atr(rows: list, i: int, n: int = 14) -> float:
    trs = [max(rows[j][2], rows[j - 1][4]) - min(rows[j][3], rows[j - 1][4]) for j in range(i - n + 1, i + 1)]
    return sum(trs) / n


def wilson(k: int, n: int) -> tuple:
    if not n:
        return (0.0, 0.0)
    p, z = k / n, 1.96
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    w = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - w, c + w)


def sgn(x: float) -> int:
    return (x > 0) - (x < 0)


def kronos_rules(cfg: str, n_paths: int) -> dict:
    """Ways to turn one setting's sample paths into a call (+1 up, -1 down, 0 no call). p = point."""
    def mean_k(k):
        return lambda p: sgn(statistics.fmean(p["finals"][:k]) - p["last"])

    def agree(a):
        return lambda p: 1 if p["up"] >= a else (-1 if p["up"] <= 1 - a else 0)

    def move(m):
        return lambda p: sgn(p["mv"]) if abs(p["mv"]) >= m * p["atr"] else 0

    def with_trend(rule, keys):
        return lambda p: (lambda d: d if d and all(p[x] == d for x in keys) else 0)(rule(p))

    vote = agree(0.5 + 1e-9)
    r = {f"{cfg} mean of {k} paths": mean_k(k) for k in (1, 5, 10, 20, 30) if k <= n_paths}
    r[f"{cfg} median of {n_paths}"] = lambda p: sgn(statistics.median(p["finals"]) - p["last"])
    r[f"{cfg} vote of {n_paths}"] = vote
    for a in (0.6, 0.7, 0.8, 0.9):
        r[f"{cfg} only when >={a:.0%} of paths agree"] = agree(a)
    for m in (0.25, 0.5, 1.0):
        r[f"{cfg} only when the average moves >={m} ATR"] = move(m)
    for keys, name in ((("h1",), "H1"), (("h4",), "H4"), (("h1", "h4"), "H1 and H4")):
        r[f"{cfg} vote, only with the {name} trend"] = with_trend(vote, keys)
        r[f"{cfg} >=70% agree, only with the {name} trend"] = with_trend(agree(0.7), keys)
    return r


BASELINES = {
    "always UP": lambda p: 1,
    "last 1 h continues": lambda p: p["mom12"],
    "last 2 h continues": lambda p: p["mom24"],
    "last 4 h continues": lambda p: p["mom48"],
    "last 2 h reverses": lambda p: -p["mom24"],
    "entry-TF EMA20 vs EMA50": lambda p: p["ema"],
    "H1 EMA20 vs EMA50": lambda p: p["h1"],
    "H4 EMA20 vs EMA50": lambda p: p["h4"],
}


def score(rule, pts: list) -> dict:
    calls = [(rule(p), p) for p in pts]
    calls = [(d, p) for d, p in calls if d]
    right = sum((d > 0) == p["up_real"] for d, p in calls)
    n = len(calls)
    pnl = [d * p["pnl_move"] - SPREAD for d, p in calls]
    return {"n": n, "cov": n / len(pts) if pts else 0, "acc": right / n if n else None,
            "ci": wilson(right, n), "pnl": statistics.fmean(pnl) if pnl else None,
            "net": sum(pnl)}


def fmt(s: dict) -> str:
    if not s["n"]:
        return "no calls"
    return (f"{s['acc']:.1%} of {s['n']} ({s['ci'][0]:.0%}-{s['ci'][1]:.0%}), "
            f"calls on {s['cov']:.0%}, ${s['pnl']:+.2f}/call")


def report(a) -> None:
    out = Path(a.out).expanduser()
    meta = json.loads((out / "meta.json").read_text())
    tf, h = meta["tf"], meta["horizon"]
    sec = SEC[tf]
    bars = {f.stem[5:]: read_bars(f) for f in out.glob("bars_*.csv")}
    rows = bars[tf]
    idx = {r[0]: i for i, r in enumerate(rows)}
    closes = [r[4] for r in rows]
    e20, e50 = ema(closes, 20), ema(closes, 50)
    trend = {}
    for name in ("H1", "H4"):
        b = bars.get(name)
        if b:
            c = [r[4] for r in b]
            trend[name] = (b, [sgn(x - y) for x, y in zip(ema(c, 20), ema(c, 50))])

    def htf(name: str, t_close: int) -> int:
        if name not in trend:
            return 0
        b, s = trend[name]
        j = closed_upto(b, SEC[name], t_close)
        return s[j] if j >= 0 else 0

    by_cfg: dict = {}
    for ln in (out / "paths.jsonl").read_text().splitlines():
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        by_cfg.setdefault(r["cfg"], {})[r["t"]] = r

    base = {}                                          # what really happened at each test point
    for t in meta["points"]:
        i = idx[t]
        real = rows[i + h][4] - rows[i][4]
        if not real:
            continue
        base[t] = {"t": t, "last": rows[i][4], "up_real": real > 0, "pnl_move": rows[i + h][4] - rows[i + 1][1],
                   "atr": atr(rows, i), "ema": sgn(e20[i] - e50[i]),
                   "h1": htf("H1", t + sec), "h4": htf("H4", t + sec),
                   **{f"mom{k}": sgn(rows[i][4] - rows[i - k][4]) for k in (12, 24, 48)}}

    lines = [f"# Kronos research: {tf}, {h} bars ahead, Kronos-{meta['size']}", ""]
    say = lines.append
    # the points every saved setting has, so all rows compare the same moments
    common = sorted(set(base).intersection(*[set(v) for v in by_cfg.values()])) if by_cfg else sorted(base)
    if not common:
        print("No forecasts saved yet.")
        return
    half = len(common) // 2
    A, B = common[:half], common[half:]
    span = lambda ts: f"{time.strftime('%Y-%m-%d', time.gmtime(ts[0]))} to {time.strftime('%Y-%m-%d', time.gmtime(ts[-1]))}"
    ups = sum(base[t]["up_real"] for t in common)
    say(f"{len(common)} separate test points ({span(common)}), every one scored by every setting. "
        f"Gold rose over the next {h * sec // 60} min at {ups / len(common):.0%} of them.")
    say(f"First half ({len(A)}, {span(A)}) picks settings, second half ({len(B)}, {span(B)}) judges them.")
    say("Format: right % of calls (95% range), share of points with a call, average $ per 0.01 lot "
        f"after a {SPREAD} spread.")
    say("")

    def pts_of(cfg, ts):
        out_ = []
        for t in ts:
            p = dict(base[t])
            if cfg:
                r = by_cfg[cfg][t]
                p["finals"] = [x[-1] for x in r["paths"]]
                p["up"] = sum(f > p["last"] for f in p["finals"]) / len(p["finals"])
                p["mv"] = statistics.fmean(p["finals"]) - p["last"]
            out_.append(p)
        return out_

    families = [("baselines", None, BASELINES)]
    for cfg in meta["configs"]:
        if cfg in by_cfg:
            n_paths = min(len(r["paths"]) for r in by_cfg[cfg].values())
            families.append((cfg, cfg, kronos_rules(cfg, n_paths)))

    picks = []
    say("| rule | all points | first half | second half |")
    say("|---|---|---|---|")
    for fam, cfg, rules in families:
        pa, pb = pts_of(cfg, A), pts_of(cfg, B)
        best = None
        for name, rule in rules.items():
            sa, sb, sall = score(rule, pa), score(rule, pb), score(rule, pa + pb)
            say(f"| {name} | {fmt(sall)} | {fmt(sa)} | {fmt(sb)} |")
            if sa["n"] >= 20 and sa["cov"] >= 0.25 and (best is None or sa["acc"] > best[1]["acc"]):
                best = (name, sa, sb)
        if best:
            picks.append(best)
    say("")
    say("## Picked on the first half, judged on the second")
    say("For each family, the rule that scored best on the first half (at least 20 calls and a call on a "
        "quarter of the points), and how it then did on the second half:")
    say("")
    for name, sa, sb in picks:
        say(f"- {name}: first half {fmt(sa)}; **second half {fmt(sb)}**")

    tr = Path.home() / ".golddesk" / "kronos_track.json"
    if tr.exists():
        say("")
        say("## The live score on the dashboard")
        for k, d in json.loads(tr.read_text()).items():
            n, right = d.get("resolved", 0), d.get("right", 0)
            if not n:
                continue
            eff = max(1, round(n / 24))        # one forecast per bar, each 24 bars long: ~24 share an outcome
            lo, hi = wilson(round(right / n * eff), eff)
            say(f"- {k}: right {right} of {n} ({right / n:.0%}). The windows overlap, so that is about {eff} "
                f"separate outcomes; a 95% range of roughly {lo:.0%} to {hi:.0%}.")
    text = "\n".join(lines)
    (out / "report.md").write_text(text + "\n")
    print(text)
    print(f"\nSaved to {out / 'report.md'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["collect", "report"])
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--tf", default="M5", choices=["M5", "H1"], help="entry timeframe (H1 = the 24 h forecast)")
    ap.add_argument("--horizon", type=int, default=24, help="bars ahead (24 = 2 h on M5, 24 h on H1)")
    ap.add_argument("--days", type=float, default=60, help="test over the most recent N days")
    ap.add_argument("--points", type=int, default=240, help="at most this many test points")
    ap.add_argument("--configs", default=",".join(CONFIGS), help="settings to run, in this order")
    ap.add_argument("--size", default="base", choices=["mini", "small", "base"])
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent / "Kronos"))
    ap.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    collect(a) if a.what == "collect" else report(a)


if __name__ == "__main__":
    main()
