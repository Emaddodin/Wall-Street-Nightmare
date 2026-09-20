"""
Replay over the market this engine actually saw.

`data/council_history.jsonl` is 45 coins x 300 bars of real 15-minute candles
recorded off the live chart, with the council's own published state on every
bar. It is the only real market data in the repository, and it is enough to
run the engine's own decision path over 13,500 genuine bars instead of over
candles a test author invented.

Nothing here decides that a trade should happen. The recorded candles are fed
to the same `breakout` and `trend_ride` the book uses, one bar at a time,
never showing them a bar that had not closed.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "data" / "council_history.jsonl"


def coins(limit: int | None = None):
    """(symbol, {bar_time: (o,h,l,c)}, {bar_time: row}) per recorded coin."""
    if not HISTORY.exists():
        return []
    out = []
    for line in HISTORY.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        ohlc, meta = {}, {}
        for row in rec.get("rows") or []:
            bar = row.get("ohlc")
            t = row.get("t")
            if not bar or t is None or len(bar) < 4:
                continue
            o, h, l, c = (float(bar[0]), float(bar[1]), float(bar[2]),
                          float(bar[3]))
            if not c:
                continue
            ohlc[int(t)] = (o, h, l, c)
            meta[int(t)] = row
        if len(ohlc) >= 120:
            out.append((rec["sym"], ohlc, meta))
        if limit and len(out) >= limit:
            break
    return out


def walk(ohlc, fn, warmup=80):
    """Run `fn(prefix, t)` over each bar, showing only what had closed by then.

    The prefix is rebuilt per bar rather than sliced from the whole series, so
    a function that reaches past `t` cannot get away with it -- there is
    nothing past `t` to reach.
    """
    keys = sorted(ohlc)
    out = []
    for i in range(warmup, len(keys)):
        t = keys[i]
        prefix = {k: ohlc[k] for k in keys[:i + 1]}
        got = fn(prefix, t)
        if got is not None:
            out.append((t, got))
    return out


def forward(ohlc, t, side, entry, tp_pct, sl_pct, bars=48):
    """What happened after this entry, on the recorded candles.

    Returns "target", "stop", or "open" -- and when both the target and the
    stop are inside one candle, the STOP is taken. Which came first inside a
    bar is unknowable from OHLC, and a replay that resolves that ambiguity in
    its own favour is how a backtest flatters a strategy.
    """
    keys = [k for k in sorted(ohlc) if k > t][:bars]
    up = side == "BUY"
    tp = entry * (1 + tp_pct / 100) if up else entry * (1 - tp_pct / 100)
    sl = entry * (1 - sl_pct / 100) if up else entry * (1 + sl_pct / 100)
    for k in keys:
        _o, h, l, _c = ohlc[k]
        hit_tp = h >= tp if up else l <= tp
        hit_sl = l <= sl if up else h >= sl
        if hit_sl:
            return "stop", k
        if hit_tp:
            return "target", k
    return "open", (keys[-1] if keys else t)


def filled(ohlc, t, side, entry, bars=8):
    """Did price come back to the level within the fill window?"""
    keys = [k for k in sorted(ohlc) if k > t][:bars]
    for k in keys:
        _o, h, l, _c = ohlc[k]
        if (l <= entry) if side == "BUY" else (h >= entry):
            return k
    return None
