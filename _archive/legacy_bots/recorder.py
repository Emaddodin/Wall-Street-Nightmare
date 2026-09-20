#!/usr/bin/env python3
"""
Record every signal the real indicator prints, and what happened next.

The faithful copy of the strategy exists only inside TradingView -- the Python
port in core/tbt.py reproduces about a tenth of it, so no backtest run here can
answer whether the strategy has an edge. The only instrument that can is the
chart itself, read continuously.

So this keeps two append-only files:

  data/signals.jsonl   one line per signal the indicator printed, with the whole
                       context that was true at that moment
  data/outcomes.jsonl  one line per signal, added about two hours later, holding
                       the minute-by-minute path that followed

Nothing is ever rewritten, so a crash mid-write costs at most the last line and
never the history. The two files are joined on `id`.

It reads each chart once a minute. The paper book reads them several times a
second, so this adds well under a percent to the load, and it never writes to a
chart or changes a symbol.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BOT = os.path.dirname(os.path.abspath(__file__))
SIGNALS = f"{BOT}/data/signals.jsonl"
OUTCOMES = f"{BOT}/data/outcomes.jsonl"

SCAN_EVERY = 60          # seconds between chart reads
SETTLE_MIN = 130         # how long to let a signal breathe before scoring it
FORWARD_MIN = 120        # minutes of path kept per signal

log = logging.getLogger("rec")


# --------------------------------------------------------------- reading them
def chart_signals(idx: int) -> tuple[str, list[dict]] | None:
    """Every signal currently on chart window `idx`, with its context."""
    from signals.tv_cdp import TradingViewCDP

    c = TradingViewCDP(target_index=idx)
    try:
        c.connect()
        studies = c.studies()
        st = [s for s in studies if "TBT" in s["name"]]
        if not st:
            return None
        # Tesla is the third leg of every convergence and the one thing that
        # cannot be recomputed anywhere else -- it is a closed-source indicator
        # wired into the engine on the chart. Reading what it published on each
        # bar is the only way its behaviour is ever available for analysis.
        tsl = {}
        tstudy = [s for s in studies if "TESLA" in s["name"].upper()]
        if tstudy:
            try:
                tr = c.raw_series(tstudy[0]["id"], limit=None)
                tat = {p: i + 1 for i, p in enumerate(tr["plots"])}
                for row in tr["rows"]:
                    def tv(name):
                        i = tat.get(name)
                        if i is None or i >= len(row):
                            return None
                        x = row[i]
                        return None if x is None or x != x else x
                    tsl[int(row[0])] = {
                        "long": tv("Long"), "short": tv("Short"),
                        "trend": tv("X Trend"), "mid": tv("mid")}
            except Exception as e:
                log.debug("tesla series: %s", str(e)[:60])
        # The whole history, not just the tail: on the first run this seeds the
        # file with everything the chart still remembers, which is worth days.
        r = c.raw_series(st[0]["id"], limit=None)
        if not r or not r.get("rows"):
            return None
        sym = r["symbol"].split(":")[-1].replace(".P", "")
        at = {p: i + 1 for i, p in enumerate(r["plots"])}

        def v(name, row):
            i = at.get(name)
            if i is None or i >= len(row):
                return None
            x = row[i]
            return None if x is None or x != x else x

        out = []
        for row in r["rows"]:
            for side in ("BUY", "SELL"):
                for ct in (False, True):
                    span = v(f"TBT_{'CT' if ct else ''}{side}_SPAN", row)
                    if span is None:
                        continue
                    out.append({
                        "sym": sym, "t": int(row[0]), "side": side,
                        "counter": ct, "res": str(r.get("res", "5")),
                        "span": int(span),
                        "tier": int(v(f"TBT_{side}_TIER", row) or 0),
                        "who": int(v(f"TBT_{side}_LAST", row) or 0),
                        "score": int(v(f"TBT_{side}_SCORE", row) or 0),
                        "agents": int(v(f"SNIP_{side}_VOTE", row) or 0),
                        "wired": bool(v("TSL_WIRED", row)),
                        "tesla": tsl.get(int(row[0])),
                    })
        return sym, out
    finally:
        try:
            c.close()
        except Exception:
            pass


def sig_id(d: dict) -> str:
    return f"{d['sym']}:{d['t']}:{d['side']}:{int(d['counter'])}"


def load_ids(path: str) -> set[str]:
    """Ids already on file. A truncated last line is skipped, not fatal."""
    ids = set()
    if not os.path.exists(path):
        return ids
    with open(path) as f:
        for line in f:
            try:
                ids.add(json.loads(line)["id"])
            except Exception:
                continue
    return ids


def append(path: str, rec: dict) -> None:
    with open(path, "a") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())


# -------------------------------------------------------------- scoring them
def atr_series(m5):
    """ATR(14) as a percentage of price, the way the indicator computes it."""
    import pandas as pd
    tr = pd.concat([m5.high - m5.low,
                    (m5.high - m5.close.shift()).abs(),
                    (m5.low - m5.close.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / 14, adjust=False).mean() / m5.close * 100


def score(cli, sig: dict, m1=None) -> dict | None:
    """The path that followed a signal, as percentages from the entry.

    `m1` is passed in so a whole pass over one coin costs a single fetch. The
    exchange is shared with the book, and scoring is never worth slowing a fill.
    """
    import pandas as pd

    if m1 is None:
        m1 = cli.history(sig["sym"], interval="1m", bars=1200)
    if m1 is None or not len(m1):
        return None
    per = int(sig["res"]) * 60 if str(sig["res"]).isdigit() else 300
    # The bot enters at the close of the bar the label printed on, which is the
    # open of the minute after it.
    ets = pd.Timestamp(sig["t"] + per, unit="s", tz="UTC")
    if ets not in m1.index:
        return None
    i = m1.index.get_loc(ets)
    w = m1.iloc[i:i + FORWARD_MIN]
    if len(w) < 30:
        return None

    # The exchange's own 1m klines are not always self-consistent -- 6 to 9% of
    # them come back with a high under the body or a low above it. The error is
    # small enough that it never changed a target-or-stop verdict when checked,
    # but it wrecks anything measuring the SHAPE of a candle: a 0.05% error on a
    # 0.18% candle produced bodies of 118% of the range. Clamp it at the source.
    m1 = m1.copy()
    m1["high"] = m1[["high", "open", "close"]].max(axis=1)
    m1["low"] = m1[["low", "open", "close"]].min(axis=1)
    m5 = m1.resample("5min").agg({"open": "first", "high": "max", "low": "min",
                                  "close": "last", "volume": "sum"}).dropna()
    a = atr_series(m5)
    bts = pd.Timestamp(sig["t"], unit="s", tz="UTC")
    atr = float(a.loc[bts]) if bts in a.index else float("nan")

    e = float(m1.loc[ets, "open"])
    long = sig["side"] == "BUY"
    fav = ((w.high - e) / e * 100) if long else ((e - w.low) / e * 100)
    adv = ((e - w.low) / e * 100) if long else ((w.high - e) / e * 100)
    cls = ((w.close - e) / e * 100) if long else ((e - w.close) / e * 100)

    sigbar = m5.loc[bts] if bts in m5.index else None
    return {
        "id": sig["id"], "entry": e,
        "atr": None if atr != atr else round(atr, 4),
        # the candle the signal printed on, so entry placement stays answerable
        "bar": None if sigbar is None else {
            "o": float(sigbar.open), "h": float(sigbar.high),
            "l": float(sigbar.low), "c": float(sigbar.close),
            "v": float(sigbar.volume)},
        "fav": [round(x, 3) for x in fav.tolist()],
        "adv": [round(x, 3) for x in adv.tolist()],
        "cls": [round(x, 3) for x in cls.tolist()],
        "scored": int(time.time()),
    }


# --------------------------------------------------------------------- loop
def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    from signals.tv_cdp import TradingViewCDP

    seen = load_ids(SIGNALS)
    scored = load_ids(OUTCOMES)
    log.info("recorder up -- %d signals on file, %d of them scored",
             len(seen), len(scored))

    cli = None
    last_score = 0.0
    while True:
        # ---- collect
        try:
            n = TradingViewCDP.chart_windows()
        except Exception as e:
            log.warning("no charts: %s", str(e)[:80])
            n = 0
        fresh = 0
        for i in range(n):
            try:
                got = chart_signals(i)
            except Exception as e:
                log.warning("window %d: %s", i, str(e)[:90])
                continue
            if not got:
                continue
            sym, sigs = got
            for d in sigs:
                d["id"] = sig_id(d)
                if d["id"] in seen:
                    continue
                d["first_seen"] = int(time.time())
                append(SIGNALS, d)
                seen.add(d["id"])
                fresh += 1
        if fresh:
            log.info("recorded %d new signal(s) -- %d on file", fresh, len(seen))

        # ---- score whatever has had time to play out
        if time.time() - last_score > 300:
            last_score = time.time()
            due = []
            # A fresh deploy whose charts printed nothing yet has no
            # signals file at all; that is an empty record, not a crash.
            try:
                with open(SIGNALS) as f:
                    for line in f:
                        try:
                            d = json.loads(line)
                        except Exception:
                            continue
                        if d["id"] in scored:
                            continue
                        per = int(d["res"]) * 60 if str(d["res"]).isdigit() else 300
                        if time.time() - (d["t"] + per) < SETTLE_MIN * 60:
                            continue
                        due.append(d)
            except FileNotFoundError:
                pass                          # nothing printed yet: nothing due
            if due:
                if cli is None:
                    from dotenv import load_dotenv
                    load_dotenv(f"{BOT}/.env")
                    from exchange.bitunix import BitunixClient
                    cli = BitunixClient()
                ok = 0
                # A handful at a time, grouped by coin: the exchange is shared
                # with the book, and the backlog drains over a few passes either
                # way. One fetch per coin, not one per signal.
                batch = due[:60]
                bars: dict[str, object] = {}
                for sym in {d["sym"] for d in batch}:
                    # Reach back far enough to cover the oldest signal still
                    # waiting on this coin, or the seeded chart history would be
                    # thrown away for want of candles.
                    oldest = min(d["t"] for d in batch if d["sym"] == sym)
                    need = int((time.time() - oldest) / 60) + 250
                    try:
                        bars[sym] = cli.history(sym, interval="1m",
                                                bars=max(1200, min(need, 9000)))
                    except Exception as e:
                        log.warning("candles for %s: %s", sym, str(e)[:70])
                for d in batch:
                    if d["sym"] not in bars:
                        continue
                    try:
                        rec = score(cli, d, bars[d["sym"]])
                    except Exception as e:
                        log.warning("scoring %s: %s", d["id"], str(e)[:70])
                        continue
                    if rec is None:
                        # too old for the candle window -- mark it so the pass
                        # does not retry it forever
                        rec = {"id": d["id"], "scored": int(time.time()),
                               "skipped": "no candles"}
                    append(OUTCOMES, rec)
                    scored.add(d["id"])
                    ok += 1
                if ok:
                    log.info("scored %d (%d still waiting)", ok, len(due) - ok)

        time.sleep(SCAN_EVERY)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
