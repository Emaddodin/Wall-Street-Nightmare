"""
Real data for the TBT flow dataset.

Every number the dataset trades on comes from one of the real files in
`data/` -- the indicator's own recorded signals, the exchange candles that
followed them, the council's recorded state, and the scanner's measurements.
Nothing in here invents a market; the only synthetic pieces are the quiet
prefix candles for signals that have no recorded history behind them, and
those are marked `synthetic-prefix` wherever they are used.

    signals.jsonl         the indicator's own prints (side, tier, agents...)
    outcomes.jsonl        what price actually did after each (entry, fav/adv)
    tesla.jsonl           the Tesla vote per signal
    council_history.jsonl 45 coins x ~300 bars of real 15m candles + council state
    boom.json             reach/smooth/leverage caps per coin
    watchlist.json        the ATR finder's list
    universe.json         the coin pool
    scout.json            what the scout wrote down on its real sweep
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


# ---------------------------------------------------------------------------
# The three joined files: signals + outcomes + tesla
# ---------------------------------------------------------------------------

def load_signals() -> list[dict]:
    rows = []
    with open(DATA / "signals.jsonl") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def load_outcomes() -> dict[str, dict]:
    """id -> the real post-signal path (entry, atr, bar, fav, adv, cls)."""
    out = {}
    with open(DATA / "outcomes.jsonl") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            out[r["id"]] = r
    return out


def load_tesla() -> dict[str, dict]:
    out = {}
    with open(DATA / "tesla.jsonl") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            out[r["id"]] = r.get("tesla") or {}
    return out


def joined() -> list[dict]:
    """One record per real signal with its real outcome attached, where both exist.

    The id is `SYM:t:SIDE:counter` on both sides, so the join is exact.
    """
    sigs = load_signals()
    outs = load_outcomes()
    tesla = load_tesla()
    rows = []
    for s in sigs:
        o = outs.get(s["id"])
        if not o:
            continue
        rows.append({**s, "outcome": o, "tesla": tesla.get(s["id"])})
    return rows


# ---------------------------------------------------------------------------
# The recorded candles: council_history.jsonl
# ---------------------------------------------------------------------------

def council_coins() -> list[dict]:
    """[{sym, htf_as_collected, ohlc: {t: (o,h,l,c)}, meta: {t: row}}] per coin."""
    out = []
    path = DATA / "council_history.jsonl"
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
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
        if len(ohlc) >= 40:
            out.append({"sym": rec["sym"], "htf_collected": rec.get("htf"),
                        "ohlc": ohlc, "meta": meta})
    return out


_COUNCIL: dict[str, dict] | None = None


def council() -> dict[str, dict]:
    """sym -> {htf_collected, ohlc, meta}, loaded once."""
    global _COUNCIL
    if _COUNCIL is None:
        _COUNCIL = {c["sym"]: c for c in council_coins()}
    return _COUNCIL


def prefix_for(sym: str, t: int, n: int = 45) -> dict[int, tuple] | None:
    """The last `n` recorded candles strictly before signal time, or None."""
    c = council().get(sym)
    if not c:
        return None
    keys = sorted(k for k in c["ohlc"] if k < t)
    if len(keys) < n:
        return None
    return {k: c["ohlc"][k] for k in keys[-n:]}


# ---------------------------------------------------------------------------
# Coin measurements: the scanner's own files
# ---------------------------------------------------------------------------

def lev_caps() -> dict[str, float]:
    out = {}
    try:
        for r in json.loads((DATA / "boom.json").read_text()):
            if r.get("lev"):
                out[r["sym"]] = float(r["lev"])
    except Exception:
        pass
    return out


def boom_rows() -> list[dict]:
    try:
        return json.loads((DATA / "boom.json").read_text())
    except Exception:
        return []


def watch_measures() -> dict:
    try:
        return json.loads((DATA / "watch_measures.json").read_text())
    except Exception:
        return {}


def watchlist() -> list[str]:
    try:
        return [str(x) for x in json.loads((DATA / "watchlist.json").read_text())]
    except Exception:
        return []


def universe() -> list[str]:
    try:
        return [str(x) for x in json.loads((DATA / "universe.json").read_text())]
    except Exception:
        return []


def scout_rows() -> list[dict]:
    try:
        return json.loads((DATA / "scout.json").read_text())
    except Exception:
        return []


# ---------------------------------------------------------------------------
# The higher-timeframe votes
# ---------------------------------------------------------------------------
# The 4h vote was recorded with the council data (htf_collected / row["htf"]).
# The 1h vote is recomputed from the same real candles with the same formula
# the Pine uses -- HMA(55) on the higher timeframe, offset by one bar -- with
# the WMA sub-lengths rounded to integers (28/55/7). The Pine computes WMA at
# fractional length; this is a documented approximation for a dataset feature,
# not part of the trading path.

def wma(vals: list[float], length: float) -> float | None:
    n = int(round(length))
    if n <= 0 or len(vals) < n:
        return None
    v = vals[-n:]
    w = list(range(1, n + 1))
    return sum(a * b for a, b in zip(v, w)) / sum(w)


def hma(closes: list[float], length: int = 55) -> float | None:
    half = wma(closes, length / 2.0)
    full = wma(closes, float(length))
    if half is None or full is None:
        return None
    k = int(round(length ** 0.5))
    # Build the series of (2*half - full) over the same rolling window, then
    # smooth it with a WMA of length k. The rolling window is the last k+1
    # half-length windows; a simpler standing computation:
    n = len(closes)
    need = int(length) + k
    if n < need:
        return None
    diffs = []
    for i in range(need - 1, n + 1):
        h = wma(closes[:i], length / 2.0)
        fu = wma(closes[:i], float(length))
        if h is None or fu is None:
            return None
        diffs.append(2.0 * h - fu)
    return wma(diffs, float(k))


def htf1h_series(ohlc: dict[int, tuple], period: int = 3600) -> dict[int, int]:
    """Per-bar 1h HTF vote: sign(close - HMA55_1h[1]), None before warmup."""
    if not ohlc:
        return {}
    keys = sorted(ohlc)
    by_hour: dict[int, float] = {}
    for k in keys:
        by_hour[k // period * period] = ohlc[k][3]   # last close wins
    hours = sorted(by_hour)
    hma_by_hour: dict[int, float | None] = {}
    hcloses = [by_hour[h] for h in hours]
    for i, h in enumerate(hours):
        # HMA needs the history UP TO AND INCLUDING this hour; the [1] in
        # the Pine takes the previous completed hour's line.
        if i + 1 >= 56:
            hma_by_hour[h] = hma(hcloses[:i + 1])
    prev_line: dict[int, float | None] = {}
    for i, h in enumerate(hours):
        prev_line[h] = hma_by_hour.get(hours[i - 1]) if i >= 1 else None
    out: dict[int, int] = {}
    for k in keys:
        h = k // period * period
        line = prev_line.get(h)
        if line is None:
            continue
        c = ohlc[k][3]
        out[k] = 1 if c > line else -1 if c < line else 0
    return out


def htf4h_at(meta: dict[int, dict], t: int) -> int | None:
    row = meta.get(t)
    if not row:
        return None
    h = row.get("htf")
    return int(h) if h is not None else None


_HTF1H_CACHE: dict[str, dict] = {}


def htf1h_at(sym: str, t: int) -> int | None:
    """The recomputed 1h HTF vote at bar `t`, from the recorded candles."""
    if sym not in _HTF1H_CACHE:
        c = council().get(sym)
        _HTF1H_CACHE[sym] = htf1h_series(c["ohlc"]) if c else {}
    return _HTF1H_CACHE.get(sym, {}).get(t)


# ---------------------------------------------------------------------------
# Outcome paths, in engine terms
# ---------------------------------------------------------------------------

def path_bars(outcome: dict, side: str, entry: float, n: int = 96):
    """[(bar_index, hi, lo)] after entry, from the real fav/adv arrays.

    fav/adv are recorded RELATIVE to the signal's side (the recorder
    computes them that way: for a SELL, fav is the DOWN move). The bars
    here are the absolute range the exchange saw, whatever the side:
    for a SELL the high comes from adv and the low from fav.
    """
    fav = outcome.get("fav") or []
    adv = outcome.get("adv") or []
    up = side == "BUY"
    rows = []
    for i in range(min(n, len(fav))):
        f = fav[i] / 100.0
        a = adv[i] / 100.0
        if up:
            hi, lo = entry * (1 + f), entry * (1 - a)
        else:
            hi, lo = entry * (1 + a), entry * (1 - f)
        rows.append((i, hi, lo))
    return rows
