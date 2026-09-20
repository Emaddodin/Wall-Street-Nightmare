#!/usr/bin/env python3
"""
The 15m hunt: find the few setups a day that are worth the whole size.

The flow, in order:

  1. start from the coins that are throwing violent candles right now
  2. read them on 15m against their own 4h trend
  3. find the impulse leg, and the Fibonacci retracement that follows it
  4. before saying yes, ask this coin's own history what usually happens to a
     trade like this one -- how often price wicks 2% against you before it
     gives 10%, and how often it gives nothing at all
  5. print a verdict, not a signal

Nothing here places an order. It is the analysis that has to pass first.
"""
from __future__ import annotations
_BOT = str(__import__("pathlib").Path(__file__).resolve().parent)
import sys
import json
import logging
import argparse
sys.path.insert(0, _BOT)
logging.basicConfig(level=logging.ERROR)
import pandas as pd
from dotenv import load_dotenv
load_dotenv(_BOT + "/.env")
from exchange.bitunix import BitunixClient

TP, SL = 10.0, 2.0
IMPULSE, RETR, WAIT, HOLD = 8.0, 0.618, 8, 48


def frame(cli, sym):
    m = cli.history(sym, interval="15m", bars=2000)
    if m is None or len(m) < 300:
        return None, None
    h4 = m.resample("4h").agg({"open": "first", "high": "max", "low": "min",
                               "close": "last", "volume": "sum"}).dropna()
    return m, h4


def trend_of(m, h4):
    ema = h4.close.ewm(span=21, adjust=False).mean()
    up = (h4.close > ema)
    return up.reindex(m.index, method="ffill")


def setups(m, trend, upto=None):
    """Every impulse-then-retracement this coin has offered."""
    hi, lo, cl = m.high.to_numpy(), m.low.to_numpy(), m.close.to_numpy()
    vol = m.volume.to_numpy()
    v20 = m.volume.rolling(20).mean().to_numpy()
    idx = m.index
    n = upto if upto is not None else len(cl)
    out = []
    for i in range(20, n - 1):
        for k in (1, 2, 3):
            j = i + k
            if j >= n: break
            up = (hi[j] - lo[i]) / lo[i] * 100
            dn = (hi[i] - lo[j]) / hi[i] * 100
            long = a = b = None; move = 0.0
            if up >= IMPULSE: long, a, b, move = True, lo[i], hi[j], up
            elif dn >= IMPULSE: long, a, b, move = False, hi[i], lo[j], dn
            if long is None: continue
            span = abs(b - a)
            if span <= 0: break
            want = b - span * RETR if long else b + span * RETR
            fill = None
            for w in range(1, WAIT + 1):
                t = j + w
                if t >= n: break
                if (lo[t] <= want) if long else (hi[t] >= want):
                    fill = t; break
            out.append(dict(leg_end=j, at=idx[j], long=long, move=move,
                            a=a, b=b, want=float(want), fill=fill,
                            vol=float(vol[j] / v20[j]) if v20[j] else 1.0,
                            withtrend=bool(trend.iloc[j]) == long))
            break
    return out


def resolve(m, s):
    """What happened after the fill: target, stop, or neither."""
    if s["fill"] is None: return None
    hi, lo = m.high.to_numpy(), m.low.to_numpy()
    e, long = s["want"], s["long"]
    for t in range(s["fill"], min(s["fill"] + HOLD, len(hi))):
        adv = (e - lo[t]) / e * 100 if long else (hi[t] - e) / e * 100
        fav = (hi[t] - e) / e * 100 if long else (e - lo[t]) / e * 100
        if adv >= SL: return 0
        if fav >= TP: return 1
    return None


def awake(m):
    """Was this coin alive at each bar, or asleep?

    Averaging a coin's whole history answers the wrong question. This strategy
    only ever fires while a coin is throwing violent candles, so its record
    during the quiet weeks says nothing about what it does now. A bar counts as
    awake when the last six hours of range are running above the coin's own
    median -- the same wakefulness we are trading.
    """
    rng = (m.high - m.low) / m.close * 100
    recent = rng.rolling(24).median()
    return recent > rng.median()


def history_says(m, trend, like, only_awake=True):
    """This coin's own record for setups resembling this one, in the same
    state of wakefulness. Not the coin's life story -- the coin as it is now."""
    live = awake(m)
    past = [s for s in setups(m, trend, upto=len(m) - 1)
            if s["fill"] is not None
            and s["withtrend"] == like["withtrend"]
            and abs(s["move"] - like["move"]) <= 6
            and (not only_awake or bool(live.iloc[s["leg_end"]]))]
    res = [resolve(m, s) for s in past]
    dec = [r for r in res if r is not None]
    if not dec:
        return None
    won = sum(dec)
    # how often price came 2% against before it ever gave 10%
    shaken = len(dec) - won
    return dict(n=len(dec), won=won, hit=won / len(dec),
                stalled=len(res) - len(dec), shaken=shaken)


def wick_risk(m, s):
    """How brutal are this coin's shadows around a fill like this one?"""
    hi, lo, op, cl = (m.high.to_numpy(), m.low.to_numpy(),
                      m.open.to_numpy(), m.close.to_numpy())
    tail = slice(max(0, len(cl) - 96), len(cl))
    up_wick = (hi[tail] - pd.Series(cl[tail]).combine(
        pd.Series(op[tail]), max).to_numpy()) / cl[tail] * 100
    dn_wick = (pd.Series(cl[tail]).combine(
        pd.Series(op[tail]), min).to_numpy() - lo[tail]) / cl[tail] * 100
    against = dn_wick if s["long"] else up_wick
    return dict(median=float(pd.Series(against).median()),
                worst=float(pd.Series(against).max()),
                over_stop=float((pd.Series(against) >= SL).mean() * 100))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", nargs="*", default=None)
    ap.add_argument("--min-hit", type=float, default=17.7,
                    help="refuse a setup whose own history is under this")
    a = ap.parse_args()
    cli = BitunixClient()
    syms = a.coins or [r["sym"] for r in json.load(open(_BOT + "/data/boom.json"))][:10]
    print(f"\n  hunting on 15m across {len(syms)} coins that are moving now")
    print(f"  impulse >= {IMPULSE}%  ->  wait for {RETR} retracement  ->  "
          f"+{TP}% / -{SL}%\n")
    live = []
    for sym in syms:
        try:
            m, h4 = frame(cli, sym)
            if m is None: continue
            trend = trend_of(m, h4)
        except Exception as e:
            print(f"  {sym:<12} {str(e)[:50]}"); continue
        ss = setups(m, trend)
        recent = [s for s in ss if s["leg_end"] >= len(m) - WAIT - 1]
        px = float(m.close.iloc[-1])
        for s in recent:
            hist = history_says(m, trend, s)
            wick = wick_risk(m, s)
            live.append((sym, s, hist, wick, px))
    if not live:
        print("  nothing set up right now. The coins are moving but no leg has")
        print("  pulled back into its zone yet -- that is the wait, not a fault.\n")
        return 0
    # Overlapping legs on one coin are one idea, not eight. Keep the biggest
    # impulse per coin and direction; the rest are the same trade seen twice.
    best = {}
    for row in live:
        sym, s = row[0], row[1]
        k = (sym, s["long"])
        if k not in best or s["move"] > best[k][1]["move"]:
            best[k] = row
    live = sorted(best.values(), key=lambda r: -r[1]["move"])
    print(f"  {'coin':<12}{'dir':<6}{'leg':>7}{'entry':>12}{'now':>12}"
          f"{'to entry':>10}{'its record':>12}{'wick risk':>11}  verdict")
    print("  " + "-" * 96)
    for sym, s, hist, wick, px in live:
        away = (px / s["want"] - 1) * 100 * (1 if s["long"] else -1)
        rec = f"{hist['won']}/{hist['n']}" if hist else "no history"
        hit = hist["hit"] * 100 if hist else 0
        bad = []
        # No history is not a good history. A coin that has never offered this
        # setup before is a coin we know nothing about, and saying TAKE there
        # is the worst thing this tool could do.
        if not hist or hist["n"] < 8:
            bad.append(f"only {hist['n'] if hist else 0} past setups like it "
                       f"-- not enough to judge")
        elif hit < a.min_hit:
            bad.append(f"its own record is {hit:.0f}%, under the {a.min_hit:.0f}% "
                       f"this geometry needs")
        if not s["withtrend"]:
            bad.append("against the 4h trend")
        if wick["over_stop"] > 25:
            bad.append(f"{wick['over_stop']:.0f}% of its candles wick past the stop "
                       f"on their own")
        verdict = "TAKE" if not bad else "no -- " + "; ".join(bad)
        print(f"  {sym:<12}{'long' if s['long'] else 'short':<6}{s['move']:>6.1f}%"
              f"{s['want']:>12.6g}{px:>12.6g}{away:>9.1f}%"
              f"{rec:>8} {hit:>3.0f}%{wick['median']:>8.2f}%  {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
