"""Do Kronos and the Boom / Crash calls beat a coin flip on gold, after the spread?

The indicator is replayed over the entry timeframe (M5 by default; trend H4, structure H1, zones M15).
At random bars where it has a setup on either side, Kronos forecasts the next `--horizon` bars from the
bars before, and the script checks what price really did:

  Kronos alone  a trade when the forecast moves more than `--min-atr` x ATR(14): enter at the next
                bar's open, exit at the close `--horizon` bars later, paying `--spread` once.
  Boom / Crash  the live rule in boom.py: Kronos expects >= 0.6 ATR within 30 minutes on a clean path and
                the indicator agrees; stop 1 ATR, target 0.8 to 1.5 ATR, out after 30 minutes otherwise.

P/L is in dollars for 0.01 lot (1 oz).

    python3 kronos_backtest.py --litefinance-days 30                 # LiteFinance history (public feed)
    python3 kronos_backtest.py --csv ~/dukascopy_xauusd_m1.csv       # any M1 CSV with time/open/high/low/close
    python3 kronos_backtest.py --tf M1 --litefinance-days 20         # the old M1 test
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import boom
from engine import LADDER, Bars, Engine, Params, Spec
from kronos_signal import DEFAULT_REPO, HORIZON, Kronos

SEC = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400}
RES = {"M1": "1", "M5": "5", "M15": "15", "H1": "60", "H4": "240"}


def load_csv(path: str) -> list:
    rows = []
    with open(Path(path).expanduser(), newline="") as f:
        rd = csv.reader(f)
        head = [h.strip().lower() for h in next(rd)]
        def col(*names):
            return next((i for i, h in enumerate(head) if h in names), None)
        it = col("time", "timestamp", "timestamps", "date", "datetime", "gmt time", "local time")
        io_, ih, il, ic = col("open", "o"), col("high", "h"), col("low", "l"), col("close", "c")
        iv = col("volume", "vol", "v", "tick_volume")
        if None in (it, io_, ih, il, ic):
            raise SystemExit(f"Need time, open, high, low, close columns; found {head}")
        for r in rd:
            if not r:
                continue
            ts = r[it].strip()
            try:
                t = float(ts)
                t = t / 1000 if t > 1e11 else t
            except ValueError:
                t = None
                for fmt in ("%d.%m.%Y %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y.%m.%d %H:%M",
                            "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M:%S"):
                    try:
                        t = datetime.strptime(ts.replace("Z", "").split("+")[0].replace(" GMT", ""), fmt)
                        t = t.replace(tzinfo=timezone.utc).timestamp()
                        break
                    except ValueError:
                        continue
                if t is None:
                    continue
            o, h, l, c = (float(r[i]) for i in (io_, ih, il, ic))
            v = float(r[iv]) if iv is not None and r[iv] else 0.0
            if v == 0 and h == l:          # Dukascopy pads closed hours with flat zero-volume bars
                continue
            rows.append([int(t), o, h, l, c, v])
    rows.sort(key=lambda r: r[0])
    return rows


def load_litefinance(days: float, tf: str = "M1") -> list:
    now, sec = int(time.time()), SEC[tf]
    chunk = min(5 * 86400, 4000 * sec) if tf == "M1" else 4000 * sec
    out, to = {}, now
    while to > now - days * 86400:
        url = (f"https://my.litefinance.org/chart/get-history?symbol=XAUUSD&resolution={RES[tf]}"
               f"&from={to - chunk}&to={to}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 GoldDesk"})
        d = json.loads(urllib.request.urlopen(req, timeout=20).read())
        d = d.get("data", d)
        for i, t in enumerate(d.get("t") or []):
            out[int(t)] = [int(t), d["o"][i], d["h"][i], d["l"][i], d["c"][i], (d.get("v") or [0] * len(d["t"]))[i]]
        to -= chunk
    rows = sorted(r for r in out.values() if r[0] >= now - days * 86400)
    return [r for r in rows if r[0] + sec <= now]           # closed bars only


def aggregate(rows: list, sec: int) -> list:
    """M1 rows -> `sec` bars (UTC-aligned)."""
    out: list = []
    for t, o, h, l, c, v in rows:
        k = t - t % sec
        if out and out[-1][0] == k:
            b = out[-1]
            b[2], b[3], b[4], b[5] = max(b[2], h), min(b[3], l), c, b[5] + v
        else:
            out.append([k, o, h, l, c, v])
    return out


def bars(rows: list, sec: int) -> Bars:
    b = Bars(sec)
    for r in rows:
        b.append(*r[:6])
    return b


def indicator_setups(entry: list, ladder: tuple, sec: int, spread: float, keep_from: int) -> dict:
    """Replay the indicator bar by bar (no look-ahead) and keep what boom.setup saw on each bar from keep_from on."""
    eng = Engine(Params(), Spec(), 1000.0, lambda t: 0, sec)
    eng.set_htf(*ladder)
    out = {}
    for i, r in enumerate(entry):
        eng.add_bar(*r[:6], spread)
        if i >= keep_from and i >= eng.warm and eng.ctx is not None:
            out[i] = boom.setup(eng)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv")
    ap.add_argument("--litefinance-days", type=int, default=0)
    ap.add_argument("--tf", default="M5", choices=sorted(LADDER), help="entry timeframe (default M5)")
    ap.add_argument("--last-days", type=float, default=30, help="test only the most recent N days of the data")
    ap.add_argument("--points", type=int, default=400, help="how many forecasts to test")
    ap.add_argument("--horizon", type=int, default=0, help="bars ahead (default: 24 on M5, 15 on M1)")
    ap.add_argument("--lookback", type=int, default=400)
    ap.add_argument("--samples", type=int, default=5)
    ap.add_argument("--min-atr", type=float, default=0.5)
    ap.add_argument("--spread", type=float, default=0.22, help="round-trip cost in price units (0.22 = LiteFinance gold)")
    ap.add_argument("--size", default="small", choices=["mini", "small", "base"])
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--repo", default=str(DEFAULT_REPO))
    ap.add_argument("--out", default="kronos_backtest_trades.csv")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()

    sec = SEC[a.tf]
    a.horizon = a.horizon or HORIZON.get(a.tf, 15)
    names = LADDER[a.tf]
    if a.csv:
        m1 = load_csv(a.csv)
        rows = m1 if a.tf == "M1" else aggregate(m1, sec)
        ladder = [bars(aggregate(m1, SEC[n]), SEC[n]) for n in names]
    else:
        days = a.litefinance_days or 30
        print(f"Loading {days} days of {a.tf} gold and the {'/'.join(names)} history behind it from LiteFinance...")
        rows = load_litefinance(days + 3, a.tf)
        warm = {names[0]: 250, names[1]: 30, names[2]: 5}                # calendar days the EMAs / bands need
        ladder = [bars(load_litefinance(days + 3 + warm[n], n), SEC[n]) for n in names]
    if len(rows) < a.lookback + a.horizon + 50:
        raise SystemExit(f"Only {len(rows)} bars loaded.")
    t_end = rows[-1][0]
    first = next(i for i, r in enumerate(rows) if r[0] >= t_end - a.last_days * 86400)
    first = max(first, a.lookback)
    last = len(rows) - a.horizon - 2
    setups = indicator_setups(rows, ladder, sec, a.spread, first)
    # only bars where the indicator has a setup on some side and the next horizon bars are contiguous
    # (no weekend or daily break inside the trade)
    cand = [i for i in range(first, last) if rows[i + a.horizon + 1][0] - rows[i][0] == (a.horizon + 1) * sec
            and i in setups and (boom.gate(setups[i], 1) or boom.gate(setups[i], -1))]
    random.seed(a.seed)
    pts = sorted(random.sample(cand, min(a.points, len(cand))))
    print(f"{len(rows)} {a.tf} bars, {datetime.fromtimestamp(rows[0][0], timezone.utc):%Y-%m-%d} to "
          f"{datetime.fromtimestamp(t_end, timezone.utc):%Y-%m-%d}. {len(cand)} bars with an indicator setup; "
          f"testing {len(pts)} forecasts, {a.horizon * sec // 60} min ahead, Kronos-{a.size}...")

    import torch
    torch.manual_seed(a.seed)
    k = Kronos(a.repo, a.size, a.lookback, a.horizon, a.samples, a.min_atr)
    print(f"Model on {k.device}.")
    res, booms, t0 = [], [], time.time()
    for j in range(0, len(pts), a.batch):
        chunk = pts[j:j + a.batch]
        fc = k.forecast_batch([rows[i - a.lookback + 1:i + 1] for i in chunk], step=sec)
        for i, f in zip(chunk, fc):
            entry = rows[i + 1][1]
            exit_ = rows[i + a.horizon][4]
            real = exit_ - entry
            pnl = (f["dir"] * real - a.spread) if f["dir"] else 0.0
            when = datetime.fromtimestamp(rows[i][0], timezone.utc).strftime("%Y-%m-%d %H:%M")
            res.append({"time": when, "call": f["call"], "forecast_move": f["move"], "real_move": round(real, 2),
                        "atr": f["atr"], "pnl_usd_0.01lot": round(pnl, 2)})
            d = boom.decide(f, setups[i], sec)
            if d:
                n_live = max(1, boom.WITHIN_MIN * 60 // sec)
                booms.append({"time": when, **boom_trade(d, f, rows[i + 1:i + 1 + n_live], a.spread, sec),
                              "why": "; ".join(setups[i]["agree"][d])})
        done = j + len(chunk)
        el = time.time() - t0
        print(f"  {done}/{len(pts)}  ({el / done:.1f} s per forecast)", end="\r", flush=True)
    print()

    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(res[0]))
        w.writeheader(); w.writerows(res)
    boom_out = Path(a.out).with_name(Path(a.out).stem + "_boom.csv")
    if booms:
        with open(boom_out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(booms[0]))
            w.writeheader(); w.writerows(booms)

    sign = [r for r in res if r["forecast_move"] != 0 and r["real_move"] != 0]
    dir_hit = sum((r["forecast_move"] > 0) == (r["real_move"] > 0) for r in sign) / max(1, len(sign))
    trades = [r for r in res if r["call"] != "FLAT"]
    wins = [r["pnl_usd_0.01lot"] for r in trades if r["pnl_usd_0.01lot"] > 0]
    losses = [r["pnl_usd_0.01lot"] for r in trades if r["pnl_usd_0.01lot"] <= 0]
    net = sum(wins) + sum(losses)
    pf = sum(wins) / abs(sum(losses)) if losses and sum(losses) else math.inf
    up_share = sum(r["real_move"] > 0 for r in sign) / max(1, len(sign))
    # 95% band for a coin flip with this many calls
    band = 1.96 * math.sqrt(0.25 / max(1, len(sign)))
    print(f"\nDirection right: {dir_hit:.1%} of {len(sign)} forecasts (coin flip: 50% +/- {band:.1%}; "
          f"price actually rose {up_share:.1%} of the time)")
    print(f"Trades (forecast > {a.min_atr} ATR): {len(trades)}, wins {len(wins)} "
          f"({len(wins) / max(1, len(trades)):.1%}), profit factor {pf:.2f}, net ${net:.2f} per 0.01 lot after spread")
    verdict = ("NO EDGE: within coin-flip range or losing after costs." if (dir_hit < 0.5 + band or net <= 0)
               else "Possible edge on this sample. Re-test on other months before trading it.")

    n = len(booms)
    bw = sum(1 for b in booms if b["r"] > 0)
    b_net = sum(b["usd_0.01lot"] for b in booms)
    b_r = sum(b["r"] for b in booms) / max(1, n)
    how = {h: sum(1 for b in booms if b["how"] == h) for h in ("target", "stop", "time")}
    print(f"Boom/Crash: {n} calls ({sum(b['kind'] == 'BOOM' for b in booms)} BOOM, "
          f"{sum(b['kind'] == 'CRASH' for b in booms)} CRASH), won {bw} ({bw / max(1, n):.1%}), "
          f"target {how['target']} / stop {how['stop']} / time up {how['time']}, average {b_r:+.2f} R, "
          f"net ${b_net:.2f} per 0.01 lot after spread")
    print("Verdict (Kronos alone):", verdict)
    print("Verdict (Boom/Crash):", f"UNPROVEN: only {n} calls, too few to judge." if n < 30 else
          ("NO EDGE: losing after spread on this sample." if b_net <= 0 else
           f"Positive on this sample ({n} calls). Still unproven: re-test on other months before trading it."))
    print(f"Every forecast: {Path(a.out).resolve()}" + (f"  Boom/Crash calls: {boom_out.resolve()}" if booms else ""))


def boom_trade(d: int, fc: dict, after: list, spread: float, sec: int) -> dict:
    """Play one Boom / Crash call on the bars after the signal bar, exactly like the live tracker."""
    entry = after[0][1] + (spread if d == 1 else 0.0)       # buy at the ask, sell at the bid
    sl, tp = boom.levels(d, entry, fc, sec)
    how, px = "time", after[-1][4] + (0.0 if d == 1 else spread)
    for t, o, h, l, c, v in (r[:6] for r in after):
        hit = boom.bar_exit(d, sl, tp, h, l, spread)
        if hit:
            how, px = hit
            break
    usd = (px - entry) * d
    return {"kind": boom.NAME[d], "entry": round(entry, 2), "sl": round(sl, 2), "tp": round(tp, 2),
            "exit": round(px, 2), "how": how, "r": round(usd / (boom.SL_ATR * fc["atr"]), 2),
            "usd_0.01lot": round(usd, 2), "atr": fc["atr"]}


if __name__ == "__main__":
    main()
