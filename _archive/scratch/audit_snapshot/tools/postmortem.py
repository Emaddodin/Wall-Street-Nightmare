#!/usr/bin/env python3
"""Every shape the engine could have seen, and what actually happened to it.

The engine refuses far more than it takes, and a refusal leaves no trace of
what it cost. This runs the real detector over real candles with every gate
switched OFF, follows each shape to its stop or its target, and then asks each
gate in turn: of what you refused, how much of it would have won?

A gate that refuses losers is worth its cost. A gate that refuses winners at
the same rate is only making the book smaller.

    python3 tools/postmortem.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BOT = Path(os.environ.get("TBT_BOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(BOT))

import papertrade as P                                    # noqa: E402
from exchange.bitunix import BitunixClient                # noqa: E402

TP, FILL_BARS, FEE, LEV = 10.0, 8, 12 / 1e4, 50


def shapes(limit_coins=70):
    c = BitunixClient()
    coins = json.loads((BOT / "data" / "watchlist.json").read_text())
    if coins and isinstance(coins[0], dict):
        coins = [x.get("symbol") or x.get("sym") for x in coins]
    room = P._stop_room()
    out = []
    for sym in coins[:limit_coins]:
        try:
            df = c.klines(sym, "15m", limit=200)
        except Exception:
            continue
        if df is None or len(df) < 80:
            continue
        ts = [int(t.timestamp()) for t in df.index]
        ohlc = {t: (float(r.open), float(r.high), float(r.low), float(r.close))
                for t, (_, r) in zip(ts, df.iterrows())}
        keys = sorted(ohlc)
        for i in range(40, len(keys) - 2):
            s = P.breakout(ohlc, keys[i], max_stop=room)
            if not s:
                continue
            e, st, sd = s["entry"], s["stop"], s["side"]
            tp = e * (1 + TP / 100) if sd == "BUY" else e * (1 - TP / 100)
            close = ohlc[keys[i]][3]
            # how far price had already run from the level when it printed,
            # measured in stops -- this is what --max-entry-r reads
            away_r = abs(close - e) / abs(e - st) if e != st else 99
            fj = None
            for j in range(i + 1, min(i + 1 + FILL_BARS, len(keys))):
                o, h, l, cl = ohlc[keys[j]]
                if (sd == "BUY" and l <= e) or (sd == "SELL" and h >= e):
                    fj = j
                    break
            if fj is None:
                out.append(dict(sym=sym, **{k: s[k] for k in
                                            ("thrust", "stop_pct", "touches",
                                             "body", "noise")},
                                away_r=away_r, fill_delay=None,
                                out="unfilled", bars=None, same_bar=False))
                continue
            o, h, l, cl = ohlc[keys[fj]]
            same = (l <= st) if sd == "BUY" else (h >= st)
            res, bars = "open", len(keys) - fj
            for j in range(fj, len(keys)):
                o, h, l, cl = ohlc[keys[j]]
                if sd == "BUY":
                    if l <= st: res, bars = "stop", j - fj; break
                    if h >= tp: res, bars = "target", j - fj; break
                else:
                    if h >= st: res, bars = "stop", j - fj; break
                    if l <= tp: res, bars = "target", j - fj; break
            out.append(dict(sym=sym, **{k: s[k] for k in
                                        ("thrust", "stop_pct", "touches",
                                         "body", "noise")},
                            away_r=away_r, fill_delay=fj - i,
                            out=res, bars=bars, same_bar=same))
    return out


def exp_of(rs):
    """Expectancy per filled trade, as a percentage of the margin committed."""
    f = [r for r in rs if r["out"] in ("target", "stop", "open")]
    if not f:
        return 0.0, 0, 0
    ret = sum((TP / 100 - FEE) * LEV if r["out"] == "target"
              else (-(r["stop_pct"] / 100 + FEE) * LEV if r["out"] == "stop"
                    else 0.0) for r in f)
    return ret / len(f) * 100, len(f), len([r for r in f if r["out"] == "target"])


def band(rs, label, lo=None, hi=None, key=None):
    sel = rs if key is None else [
        r for r in rs if r.get(key) is not None
        and (lo is None or r[key] >= lo) and (hi is None or r[key] < hi)]
    e, n, w = exp_of(sel)
    if n < 6:
        print(f"    {label:<28} n={n:<4} (کم)")
        return
    print(f"    {label:<28} n={n:<4} برد {w:>2} ({100*w/n:>4.1f}%)   "
          f"انتظار {e:>+7.1f}% مارجین")


def target_sweep(limit_coins=70):
    """The same shapes, asked how far they actually get.

    "How often does it reach fifteen times its risk" produces six events in
    forty-one hours and nothing can be learned from six. "How far does it get"
    uses every trade, and it is the same question asked so the data can answer.

    Both columns are printed because neither is the truth. Inside a fifteen
    minute bar the order of the high and the low is unknowable, and a stop of
    0.65% with a target under 2% means one bar routinely spans both. The
    pessimistic column calls every such bar a stop and is what to plan with;
    the optimistic column calls it a win and is the ceiling.
    """
    c = BitunixClient()
    coins = json.loads((BOT / "data" / "watchlist.json").read_text())
    if coins and isinstance(coins[0], dict):
        coins = [x.get("symbol") or x.get("sym") for x in coins]
    room = P._stop_room()
    T = []
    for sym in coins[:limit_coins]:
        try:
            df = c.klines(sym, "15m", limit=200)
        except Exception:
            continue
        if df is None or len(df) < 80:
            continue
        ts = [int(t.timestamp()) for t in df.index]
        ohlc = {t: (float(r.open), float(r.high), float(r.low), float(r.close))
                for t, (_, r) in zip(ts, df.iterrows())}
        keys = sorted(ohlc)
        for i in range(40, len(keys) - 2):
            s = P.breakout(ohlc, keys[i], max_stop=room)
            if not s or s["thrust"] < 1.5:
                continue
            e, st, sd = s["entry"], s["stop"], s["side"]
            fj = None
            for j in range(i + 1, min(i + 1 + FILL_BARS, len(keys))):
                o, h, l, cl = ohlc[keys[j]]
                if (sd == "BUY" and l <= e) or (sd == "SELL" and h >= e):
                    fj = j
                    break
            if fj is None:
                continue
            T.append((abs(e - st) / e * 100, e, st, sd,
                      [ohlc[k] for k in keys[fj:]]))
    if not T:
        return

    def sim(tgt, pessimistic):
        w, ret = 0, 0.0
        for R, e, st, sd, bars in T:
            tp = e * (1 + tgt / 100) if sd == "BUY" else e * (1 - tgt / 100)
            done = None
            for o, h, l, cl in bars:
                hs = l <= st if sd == "BUY" else h >= st
                ht = h >= tp if sd == "BUY" else l <= tp
                if hs and ht:
                    done = "stop" if pessimistic else "target"
                    break
                if hs:
                    done = "stop"
                    break
                if ht:
                    done = "target"
                    break
            if done == "target":
                w += 1
                ret += (tgt / 100 - FEE) * LEV
            elif done == "stop":
                ret -= (R / 100 + FEE) * LEV
        return w, ret / len(T) * 100

    med = sorted(t[0] for t in T)[len(T) // 2]
    print(f"\n  ۷) اگر فقط هدف عوض شود ({len(T)} ترید، استاپ میانه {med:.2f}%)")
    print(f"     {'هدف':<14}{'بدبینانه (هر ابهامی = استاپ)':<34}خوش‌بینانه")
    for tgt in (0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0):
        wp, ep = sim(tgt, True)
        wo, eo = sim(tgt, False)
        print(f"     {tgt:>5.2f}%      {wp:>4} ({100*wp/len(T):>4.1f}%)  "
              f"{ep:>+7.1f}% مارجین        {wo:>4} ({100*wo/len(T):>4.1f}%)  "
              f"{eo:>+7.1f}%")


def main() -> int:
    rs = shapes()
    filled = [r for r in rs if r["out"] != "unfilled"]
    print(f"\n  {len(rs)} شکل روی کندل واقعی، {len(filled)} تای آن پر شد "
          f"({100*len(filled)/max(len(rs),1):.0f}%)\n")

    print("  ۱) کندل شکست (thrust) -- کف فعلی 1.5 است، پس بند اول را رد می‌کنیم")
    for lo, hi, lab in ((0, 1.0, "زیر 1.0  (رد می‌شود)"),
                        (1.0, 1.5, "1.0-1.5  (رد می‌شود)"),
                        (1.5, 2.0, "1.5-2.0  (گرفته می‌شود)"),
                        (2.0, 3.0, "2.0-3.0  (گرفته می‌شود)"),
                        (3.0, 99, "بالای 3.0 (گرفته می‌شود)")):
        band(filled, lab, lo, hi, "thrust")

    print("\n  ۲) چند کندل طول کشید تا سفارش پر شود -- این همان --fill-bars است")
    for lo, hi, lab in ((1, 2, "کندل 1 (فوری)"), (2, 3, "کندل 2"),
                        (3, 5, "کندل 3-4"), (5, 9, "کندل 5-8 (بیات)")):
        band(filled, lab, lo, hi, "fill_delay")

    print("\n  ۳) فاصلهٔ قیمت از سطح موقع چاپ سیگنال -- این همان --max-entry-r است")
    for lo, hi, lab in ((0, 0.5, "زیر 0.5 استاپ"), (0.5, 1.0, "0.5-1.0"),
                        (1.0, 2.0, "1.0-2.0"), (2.0, 99, "بالای 2.0 (رد می‌شود)")):
        band(filled, lab, lo, hi, "away_r")

    print("\n  ۴) پهنای استاپ خودِ شکل")
    for lo, hi, lab in ((0, 0.4, "زیر 0.4%"), (0.4, 0.7, "0.4-0.7%"),
                        (0.7, 1.0, "0.7-1.0%"), (1.0, 9, "بالای 1.0%")):
        band(filled, lab, lo, hi, "stop_pct")

    print("\n  ۵) بدنهٔ کندل شکست")
    for lo, hi, lab in ((0, 0.6, "زیر 0.6"), (0.6, 0.8, "0.6-0.8"),
                        (0.8, 9, "بالای 0.8")):
        band(filled, lab, lo, hi, "body")

    target_sweep()

    same = [r for r in filled if r["same_bar"]]
    print(f"\n  ۶) همان کندلی که پر شد، استاپ هم خورد: {len(same)} از "
          f"{len(filled)} ({100*len(same)/max(len(filled),1):.0f}%)")
    print("     -- سفارش لیمیت وقتی پر می‌شود که قیمت از سطح رد می‌شود؛")
    print("        اگر با شتاب رد شود، استاپ هم در همان کندل می‌رود. KITEUSDT.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
