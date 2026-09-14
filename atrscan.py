#!/usr/bin/env python3
"""The coin finder, rebuilt around the one thing that measured.

The old scanner ranked on what a coin had done over weeks -- how often it
covered ten percent, how straight the move was, how many shapes it produced.
That is a statement about the coin's character, not about today, and it put
the market's biggest movers outside the list entirely: NIULAIUSDT, measured at
reach 78.8 and seventy-five minutes to target, was dropped because it produced
none of a shape that needs a quiet band, on a day it moved forty-four percent.

What actually separated winners from losers, over 163 of the indicator's own
signals on real candles:

    ATR under 2.5%    8.6% reached +10%    -54.6% per trade
    ATR 2.5 - 4.0%   27.3%                +57.6%
    ATR 4.0 - 5.5%   23.7%                +44.0%
    ATR over 5.5%    39.1%               +128.8%

So the rule is the one the operator was already using by hand: open the chart
with the biggest candles. ATR as a percentage of price, because an ATR in
dollars only says the coin is expensive.

Volume is a floor, never a ranking term. A coin with enormous candles and no
volume is a coin nobody can get out of, and ranking by volume would hand the
list straight back to BTC and ETH, which do not move enough to reach the
target at all.

    python3 atrscan.py [--top 60] [--min-atr 2.5] [--min-vol 1000000]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BOT = Path(os.environ.get("TBT_BOT") or Path(__file__).resolve().parent)
sys.path.insert(0, str(BOT))

from exchange.bitunix import BitunixClient          # noqa: E402

log = logging.getLogger("atrscan")

WATCH = BOT / "data" / "watchlist.json"
MEAS = BOT / "data" / "atr_measures.json"
WATCH_HIST = BOT / "data" / "watch_history.jsonl"
LOCK = BOT / "data" / "atrscan.lock"


def write_atomic(path: Path, obj) -> None:
    """A reader must never see half a list.

    The scout reads the watchlist on its own schedule, and a plain write is
    visible in pieces: it has read a truncated file mid-write before and
    walked nothing for a sweep.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, path)


def atr_pct(df, bars: int) -> float | None:
    """True range over `bars`, as a percentage of the last price.

    True range and not the candle's own high-low: a coin that gaps between
    candles is moving, and the gap is exactly the part a high-low range
    cannot see.
    """
    if df is None or len(df) < bars + 1:
        return None
    tr, prev = [], None
    for _, r in df.iterrows():
        h, l, c = float(r.high), float(r.low), float(r.close)
        tr.append(max(h - l, abs(h - prev), abs(l - prev))
                  if prev is not None else h - l)
        prev = c
    last = float(df.iloc[-1].close)
    if not last:
        return None
    return sum(tr[-bars:]) / bars / last * 100


def trend_pct(df, bars: int = 8) -> float | None:
    """Where the coin has come from, over `bars` candles.

    Eight bars of fifteen minutes is two hours -- long enough to say which way
    the coin has been going, short enough to still be about now.

    It is here because it measured, over 163 of the indicator's own signals:
    a signal with the coin's own two-hour direction won 20.7% of the time and
    returned +5.3% a trade, against it 13.0% and -12.1%. And a coin that had
    just fallen more than five percent produced thirteen signals and won none
    of them at all.
    """
    if df is None or len(df) < bars + 1:
        return None
    then = float(df.iloc[-(bars + 1)].close)
    now = float(df.iloc[-1].close)
    if not then:
        return None
    return (now / then - 1) * 100


def shape_of(df, bars: int = 20) -> dict:
    """The handful of readings that decided winners from losers.

    Every one was measured over 163 of the indicator's own signals, at the
    signal bar, on real candles. Nothing here is a preference:

      vol20   the standard deviation of close-to-close returns. The strongest
              separator of all and it points DOWN: the calmer half won 23.9%
              of the time for +12.7% a trade, the wilder half 10.1% and
              -20.5%. A tight stop on a wild coin is taken out by noise
              rather than by being wrong.
      mom6h   the six-hour move. With the trade it is worth having: +7.4%
              against -14.4%.
      mom1h   the one-hour move. With the trade it is worth AVOIDING: a coin
              that has just jumped our way has already done the move. -14.4%
              against +7.4%.
      volx    the last candle's volume against its own twenty-bar normal.
              Above it, +3.2%; below, -10.5%.
    """
    out = {}
    if df is None or len(df) < bars + 2:
        return out
    c = [float(x.close) for _, x in df.iterrows()]
    v = [float(getattr(x, "volume", 0) or 0) for _, x in df.iterrows()]
    rets = [c[i] / c[i - 1] - 1 for i in range(-bars, 0) if c[i - 1]]
    if rets:
        m = sum(rets) / len(rets)
        out["vol20"] = (sum((r - m) ** 2 for r in rets) / len(rets)) ** 0.5 * 100
    if len(c) > 24:
        out["mom6h"] = (c[-1] / c[-25] - 1) * 100
    if len(c) > 4:
        out["mom1h"] = (c[-1] / c[-5] - 1) * 100
    older = [x for x in v[-bars - 1:-1] if x]
    if older and v[-1]:
        out["volx"] = v[-1] / (sum(older) / len(older))
    return out


def one(cli, sym, bars, res):
    try:
        df = cli.klines(sym, res, limit=max(bars, 26) + 5)
    except Exception as e:
        return sym, None, str(e)[:60]
    a = atr_pct(df, bars)
    if a is None:
        return sym, None, "not enough candles"
    return sym, (a, trend_pct(df), shape_of(df)), None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top", type=int, default=60,
                    help="how many coins to leave on the watchlist")
    ap.add_argument("--min-atr", type=float, default=2.5, metavar="PCT",
                    help="the floor. Below this the measured expectancy was "
                         "negative on every slice, so a coin under it is not "
                         "a slow winner, it is a coin that cannot get there.")
    ap.add_argument("--min-vol", type=float, default=1e6, metavar="USD",
                    help="24h volume floor. Not a ranking term -- ranking by "
                         "volume hands the list back to the coins that do "
                         "not move.")
    ap.add_argument("--bars", type=int, default=14)
    ap.add_argument("--res", default="15m")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    # One scan at a time. Two of them writing the watchlist is how the scout
    # ends up walking a list that never existed.
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 900:
        print("another scan is running", flush=True)
        return 0
    LOCK.write_text(str(os.getpid()))
    try:
        return _scan(a)
    finally:
        try:
            LOCK.unlink()
        except OSError:
            pass


def _scan(a) -> int:
    cli = BitunixClient()
    liquid = []
    for t in cli.tickers():
        s = t.get("symbol") or ""
        if not s.endswith(("USDT", "USDC")):
            continue
        try:
            vol = float(t.get("quoteVol") or t.get("baseVol")
                        or t.get("volume") or 0)
        except (TypeError, ValueError):
            continue
        if vol >= a.min_vol:
            liquid.append((s, vol))
    print(f"  {len(liquid)} pairs above ${a.min_vol:,.0f} of volume",
          flush=True)

    rows, failed = [], 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(one, cli, s, a.bars, a.res): (s, v)
                for s, v in liquid}
        for f in as_completed(futs):
            sym, got, err = f.result()
            if got is None:
                failed += 1
                continue
            atr, trend, shape = got
            rows.append({"sym": sym, "atr": atr, "trend": trend,
                         "vol": dict(liquid).get(sym, 0.0), **shape})
    print(f"  measured {len(rows)} in {time.time()-t0:.0f}s, "
          f"{failed} could not be read", flush=True)

    rows.sort(key=lambda r: -r["atr"])
    keep = [r for r in rows if r["atr"] >= a.min_atr][:a.top]
    if not keep:
        # Never leave the scout with nothing. A market with no volatility at
        # all is a market with no trades in it, and an empty watchlist looks
        # exactly like a broken scanner.
        print(f"  nothing above {a.min_atr}% ATR -- keeping the top "
              f"{a.top} anyway so the scout has something to walk",
              flush=True)
        keep = rows[:a.top]

    if not keep:
        # Nothing measured AT ALL -- no candles came back from any pair.
        # The "keep the top anyway" fallback above covers a quiet market;
        # this covers a blind one, and it must not crash on its own empty
        # list or pretend the list is fine.
        print("  nothing could be measured -- no candles from any pair. "
              "The watchlist is written empty rather than invented.",
              flush=True)
        write_atomic(WATCH, [])
        write_atomic(MEAS, {})
        try:
            with open(WATCH_HIST, "a") as f:
                f.write(json.dumps({"ts": int(time.time()),
                                    "syms": []}) + "\n")
        except OSError:
            pass
        return 0

    write_atomic(WATCH, [r["sym"] for r in keep])
    write_atomic(MEAS, {
        r["sym"]: {k: r.get(k) for k in
                   ("atr", "vol", "trend", "vol20", "mom6h", "mom1h", "volx")}
        for r in rows})
    # One line per scan, append-only: a later question -- was this coin ON
    # the finder's list when the signal fired? -- can only be answered if
    # the list's history exists. The sweep of the recorded signals showed
    # the population is the strategy; this is the record that says which
    # population each signal belonged to.
    try:
        with open(WATCH_HIST, "a") as f:
            f.write(json.dumps({"ts": int(time.time()),
                                "syms": [r["sym"] for r in keep]}) + "\n")
    except OSError:
        pass
    print("\n  the biggest candles in the market right now:", flush=True)
    for r in keep[:15]:
        print(f"    {r['sym']:<16} ATR {r['atr']:>5.2f}%   "
              f"${r['vol']/1e6:>7.1f}M", flush=True)
    print(f"\n  {len(keep)} coins -> {WATCH.name}  "
          f"(ATR {keep[-1]['atr']:.2f}% to {keep[0]['atr']:.2f}%)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
