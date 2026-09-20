#!/usr/bin/env python3
"""What the indicator's OWN signal is worth, on coins that are moving.

Everything measured so far has been about breakout(), which the engine builds
itself and which needs a quiet band. The indicator publishes a signal of its
own -- TBT_BUY_SCORE, its tier, its span, its council -- and on --source break
the engine reads none of it for the decision. On the coins the user actually
trades from, in their own screenshots, that signal is what fired.

So this reads it. It drives one chart window across the coins worth sitting
on, takes the indicator's whole published history off the study, and pairs
every signal with what price did afterwards on real exchange candles.

Read-only. It changes nothing, places nothing, and touches no service other
than borrowing a chart window the way the scout does.

    python3 tools/signalcheck.py [--window 1] [--coins 12] [--target 10]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

BOT = Path(os.environ.get("TBT_BOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(BOT))

from signals.tv_cdp import TradingViewCDP              # noqa: E402
from exchange.bitunix import BitunixClient             # noqa: E402

FEE = 12 / 1e4
# The tiers the indicator itself names: PEERLESS is its best, then EXCELLENT,
# then GOOD. Published as TBT_BUY_TIER / TBT_SELL_TIER.
TIERS = {3: "PEERLESS", 2: "EXCELLENT", 1: "GOOD"}


def movers(cli, n, min_vol=1e6, bars=14):
    """The coins with the biggest candles, which is how the user picks them.

    Not a ranking anybody invented: open the chart with the highest ATR and
    look at it. ATR is taken as a percentage of price, because an ATR in
    dollars only says the coin is expensive -- a $4 range on a $100 coin and a
    $0.04 range on a $1 coin are the same trade.

    Volume is a floor, not a ranking term. A coin with enormous candles and no
    volume is a coin nobody can get out of.
    """
    rows = []
    for t in cli.tickers():
        s = t["symbol"]
        if not s.endswith(("USDT", "USDC")):
            continue
        try:
            vol = float(t.get("quoteVol") or t.get("baseVol")
                        or t.get("volume") or 0)
        except (TypeError, ValueError):
            continue
        if vol < min_vol:
            continue
        rows.append((s, vol))
    out = []
    for sym, vol in rows:
        try:
            df = cli.klines(sym, "15m", limit=bars + 2)
        except Exception:
            continue
        if df is None or len(df) < bars:
            continue
        tr, prev = [], None
        for _, r in df.iterrows():
            h, l, c = float(r.high), float(r.low), float(r.close)
            tr.append(max(h - l, abs(h - prev), abs(l - prev))
                      if prev is not None else h - l)
            prev = c
        atr = sum(tr[-bars:]) / bars
        last = float(df.iloc[-1].close)
        if not last:
            continue
        out.append((sym, atr / last * 100, vol))
    out.sort(key=lambda x: -x[1])
    return out[:n]


def signals_on(c, sym, dwell=6.0):
    """The indicator's published signal history for one coin.

    Returns [(bar_time, side, score, tier, span)]. Every value comes off the
    study's own plots -- nothing here re-derives a signal.
    """
    c.set_symbol(f"BITUNIX:{sym}.P")
    for _ in range(int(dwell / 0.5) + 10):
        time.sleep(0.5)
        if c.state().symbol.endswith(f"{sym}.P"):
            break
    else:
        return None
    time.sleep(dwell)
    sid = c.find_study("TBT")
    if not sid:
        return None
    r = c.raw_series(sid)
    if not r or not r.get("rows"):
        return None
    # The study has to have caught up with the chart, or the rows still
    # describe the previous coin. The scout learned this the hard way.
    if not str(r.get("symbol", "")).endswith(f"{sym}.P"):
        return None
    at = {p: i + 1 for i, p in enumerate(r["plots"])}
    need = ("TBT_BUY_SCORE", "TBT_SELL_SCORE", "TBT_BUY_TIER", "TBT_SELL_TIER")
    # The council's own reading, packed on the same row -- the one scoring
    # component that could never be measured offline.
    if any(k not in at for k in need):
        return None

    def v(row, name):
        i = at.get(name)
        return row[i] if i is not None and i < len(row) else None

    out = []
    for row in r["rows"]:
        t = row[0]
        for side, sc, tier, span in (
                ("BUY", "TBT_BUY_SCORE", "TBT_BUY_TIER", "TBT_BUY_SPAN"),
                ("SELL", "TBT_SELL_SCORE", "TBT_SELL_TIER", "TBT_SELL_SPAN")):
            s_ = v(row, sc)
            if s_ is None or s_ != s_ or s_ <= 0:
                continue
            out.append((int(t), side, float(s_),
                        int(v(row, tier) or 0), v(row, span),
                        v(row, "VOTES_PACKED"), v(row, "SNIP_BUY_VOTE"),
                        v(row, "SNIP_SELL_VOTE"), v(row, "SNIP_AGENTS_N")))
    return out


def outcome(bars, t0, side, target_pct, stop_pct):
    """What price did after the signal bar, on real exchange candles."""
    keys = [k for k in sorted(bars) if k > t0]
    if not keys:
        return None, None
    entry = bars[keys[0]][0]              # next bar's open: no lookahead
    tp = entry * (1 + target_pct / 100) if side == "BUY" \
        else entry * (1 - target_pct / 100)
    sl = entry * (1 - stop_pct / 100) if side == "BUY" \
        else entry * (1 + stop_pct / 100)
    for n, k in enumerate(keys):
        o, h, l, cl = bars[k]
        hit_sl = l <= sl if side == "BUY" else h >= sl
        hit_tp = h >= tp if side == "BUY" else l <= tp
        # A bar that spans both is called a stop. At these distances one
        # fifteen minute candle routinely covers the pair, and guessing the
        # order in our own favour is how a backtest lies.
        if hit_sl:
            return "stop", n
        if hit_tp:
            return "target", n
    return "open", len(keys)


def chart_targets():
    """Every TradingView chart tab, freshly listed each time it is asked."""
    import requests
    return [t for t in requests.get("http://127.0.0.1:9222/json",
                                    timeout=10).json()
            if t.get("type") == "page"
            and "tradingview.com/chart" in (t.get("url") or "")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--window", type=int, default=-1,
                    help="chart window to use. The default opens one of its "
                         "own and closes it again: borrowing the scout's "
                         "means two processes setting the same chart to "
                         "different coins, and the book goes blind for as "
                         "long as this runs.")
    ap.add_argument("--coins", type=int, default=12)
    ap.add_argument("--target", type=float, default=10.0)
    ap.add_argument("--stops", default="1.0,1.5,2.0,3.0",
                    help="stop distances to try, in percent")
    ap.add_argument("--dwell", type=float, default=6.0)
    ap.add_argument("--save", metavar="PATH",
                    help="write the signals and their candles here. Walking "
                         "the charts takes ten minutes and disturbs the "
                         "guard; slicing the result should cost neither.")
    a = ap.parse_args()

    cli = BitunixClient()
    own_id = None
    if a.window < 0:
        before = TradingViewCDP.chart_windows()
        before_ids = {t["id"] for t in chart_targets()}
        url = (BOT / "data" / "chart_url.txt").read_text().strip()
        urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:9222/json/new?{url}", method="PUT"),
            timeout=30).close()
        for _ in range(20):
            time.sleep(2)
            fresh = [t for t in chart_targets() if t["id"] not in before_ids]
            if fresh:
                own_id = fresh[0]["id"]
            if TradingViewCDP.chart_windows() > before:
                break
        a.window = TradingViewCDP.chart_windows() - 1
        print(f"  opened chart window {a.window} for this run "
              f"(the scout keeps its own)")
        time.sleep(20)

    picks = movers(cli, a.coins)
    print(f"\n  {len(picks)} coin(s) with the biggest candles "
          f"(ATR over 14 bars of 15m, as a percent of price)\n")

    found = []
    for sym, atr, vol in picks:
        c = None
        try:
            c = TradingViewCDP(target_index=a.window)
            sig = signals_on(c, sym, a.dwell)
        except Exception as e:
            print(f"    {sym:<15} could not be read: {str(e)[:50]}")
            continue
        finally:
            if c is not None:
                c.close()
        if sig is None:
            print(f"    {sym:<15} ATR {atr:>5.2f}%   the study never caught up")
            continue
        try:
            df = cli.klines(sym, "15m", limit=200)
        except Exception:
            df = None
        if df is None or len(df) < 20:
            print(f"    {sym:<15} ATR {atr:>5.2f}%   no candles")
            continue
        bars = {int(t.timestamp()): (float(r.open), float(r.high),
                                     float(r.low), float(r.close))
                for t, (_, r) in zip(df.index, df.iterrows())}
        first, last = min(bars), max(bars)
        # Only signals inside the candle window, and never the forming bar.
        mine = [s for s in sig if first <= s[0] // 1000 <= last - 900] \
            if sig and sig[0][0] > 1e12 else \
            [s for s in sig if first <= s[0] <= last - 900]
        scale = 1000 if sig and sig[0][0] > 1e12 else 1
        print(f"    {sym:<15} ATR {atr:>5.2f}%   "
              f"{len(mine)} signal(s) in the last {len(bars)} candles")
        for rec in mine:
            t, side, score, tier, span = rec[:5]
            found.append((sym, t // scale, side, score, tier, bars,
                          atr, rec[5], rec[6], rec[7], rec[8]))

    def _shut():
        """Close exactly the tab this run opened, whatever index it is now.

        The first version closed "window N" by position, and positions move --
        the scout and the guard open and close tabs while this runs. It left
        one behind that nothing was driving, TradingView stopped feeding it,
        and forty minutes later the book reported a frozen chart: a fault
        invented entirely by the tool that was measuring.
        """
        if own_id is None:
            return
        try:
            import requests
            requests.get("http://127.0.0.1:9222/json/close/" + own_id,
                         timeout=10)
            print("  closed the chart window this run opened")
        except Exception as e:
            print(f"  COULD NOT CLOSE the window this run opened ({own_id}): "
                  f"{str(e)[:60]}\n  close it by hand -- an undriven tab stops "
                  f"being fed and the book will call it frozen")

    if a.save:
        try:
            Path(a.save).write_text(json.dumps(
                [{"sym": f[0], "t": f[1], "side": f[2], "score": f[3],
                  "tier": f[4], "atr": f[6], "votes": f[7],
                  "buy_vote": f[8], "sell_vote": f[9], "agents": f[10],
                  "bars": {str(k): v for k, v in f[5].items()}}
                 for f in found]))
            print(f"  saved {len(found)} signal(s) to {a.save}")
        except Exception as e:
            print(f"  could not save: {str(e)[:60]}")

    if not found:
        print("\n  the indicator published no signal on any of them\n")
        _shut()
        return 0

    print(f"\n  {len(found)} signal(s) total. What happened after each, at a "
          f"{a.target:g}% target:\n")
    print(f"    {'stop':<10}{'signals':<10}{'target':<16}{'stopped':<16}"
          f"{'still open':<14}{'per trade at 50x'}")
    for stop_pct in [float(x) for x in a.stops.split(",")]:
        w = l = o = 0
        ret = 0.0
        for sym, t, side, score, tier, bars, *_ in found:
            res, _ = outcome(bars, t, side, a.target, stop_pct)
            if res == "target":
                w += 1
                ret += (a.target / 100 - FEE) * 50
            elif res == "stop":
                l += 1
                ret -= (stop_pct / 100 + FEE) * 50
            elif res == "open":
                o += 1
        n = w + l + o
        print(f"    {stop_pct:>4.1f}%     {n:<10}{w:>3} ({100*w/max(n,1):>4.1f}%)     "
              f"{l:>4} ({100*l/max(n,1):>4.1f}%)     {o:<14}"
              f"{ret/max(w+l,1)*100:>+8.1f}%")

    print(f"\n  by the indicator's own tier, at a "
          f"{[float(x) for x in a.stops.split(',')][-1]:g}% stop:")
    worst = [float(x) for x in a.stops.split(",")][-1]
    for tv, name in sorted(TIERS.items(), reverse=True):
        sel = [f for f in found if f[4] == tv]
        if not sel:
            continue
        w = sum(1 for s in sel if outcome(s[5], s[1], s[2], a.target,
                                          worst)[0] == "target")
        l = sum(1 for s in sel if outcome(s[5], s[1], s[2], a.target,
                                          worst)[0] == "stop")
        print(f"    {name:<12} {len(sel):>3} signal(s)   "
              f"target {w}   stopped {l}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
