#!/usr/bin/env python3
"""
The pattern dataset: what the chart looks like right before a move.

The eagle's job is to sit over the coin that is ABOUT to move. Today it
ranks by the indicator's own ripeness; this dataset measures the candle
side of that question -- the shape of the chart at bar t, and what the
next 4/8/16/32 bars (1h/2h/4h/8h) actually did.

Per closed bar, on the recorded real candles:
  - the candle's own anatomy: range vs its typical, body share, wicks
  - the structure around it: quiet-band width and level touches, runs of
    up/down closes, higher-highs/lower-lows, distance from the recent
    swing high and low, momentum over 4/8/24 bars
  - the real coiling pressure (the engine's own function) and whether a
    breakout/trend shape exists on the bar
and as labels, strictly from bars AFTER t (no lookahead anywhere):
  - max up and max down move, the net close move, which direction touched
    2.5% first, and whether 5% was reached, per horizon.

Outputs:
  data/dataset/patterns.npz    X float32 (n, features), labels per horizon
  data/dataset/patterns.jsonl  one row per bar, human-readable
  data/dataset/patterns_report.txt  which shapes precede the big moves --
                               the measured answer to "where should the
                               eagle sit"

    python3 dataset/patterns.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import papertrade as P  # noqa: E402
from dataset import sources  # noqa: E402

OUT = ROOT / "data" / "dataset"
HORIZONS = (4, 8, 16, 32)
FEATURES = ["range_pct", "body_share", "up_wick", "dn_wick", "expansion",
            "band_width", "touches", "n_up_closes", "n_dn_closes",
            "hh_ll", "dist_high", "dist_low", "mom4", "mom8", "mom24",
            "coil", "shape_now", "since_shape", "hour"]


def bar_features(ohlc: dict, t: int) -> dict | None:
    """The chart's shape at bar t, from bars <= t only."""
    keys = [k for k in sorted(ohlc) if k <= t]
    if len(keys) < 62:
        return None
    bars = [ohlc[k] for k in keys]
    o, h, l, c = bars[-1]
    if not c:
        return None
    rng = h - l
    typ = float(np.median([(b[1] - b[2]) / b[3] for b in bars[-21:-1]
                           if b[3]]))
    if typ <= 0:
        return None
    band = bars[-13:-1]
    band_hi = max(b[1] for b in band)
    band_lo = min(b[2] for b in band)
    tol = typ * 0.25
    touches = sum(1 for b in band
                  if abs(b[1] - band_hi) < band_hi * tol
                  or abs(b[2] - band_lo) < band_lo * tol)
    closes = [b[3] for b in bars]
    n_up = sum(1 for i in range(-4, 0) if closes[i] > closes[i - 1])
    n_dn = sum(1 for i in range(-4, 0) if closes[i] < closes[i - 1])
    hh = sum(1 for i in (-3, -2, -1) if bars[i][1] > bars[i - 1][1])
    ll = sum(1 for i in (-3, -2, -1) if bars[i][2] < bars[i - 1][2])
    win24 = bars[-25:]
    hi24 = max(b[1] for b in win24)
    lo24 = min(b[2] for b in win24)
    coil = P.coiling(ohlc, t)
    shape_now = 1 if (P.breakout(ohlc, t, max_stop=1.0)
                      or P.trend_ride(ohlc, t, max_stop=1.0)) else 0
    since = 0
    for k in reversed(keys[:-1]):
        if P.breakout(ohlc, k, max_stop=1.0) or P.trend_ride(ohlc, k,
                                                            max_stop=1.0):
            break
        since += 1
    return {
        "range_pct": rng / c * 100,
        "body_share": abs(c - o) / rng if rng > 0 else 0.0,
        "up_wick": (h - max(o, c)) / rng if rng > 0 else 0.0,
        "dn_wick": (min(o, c) - l) / rng if rng > 0 else 0.0,
        "expansion": (rng / c) / typ,
        "band_width": (band_hi - band_lo) / c * 100,
        "touches": float(touches),
        "n_up_closes": float(n_up),
        "n_dn_closes": float(n_dn),
        "hh_ll": float(hh + ll),
        "dist_high": (hi24 - c) / c * 100,
        "dist_low": (c - lo24) / c * 100,
        "mom4": (c / closes[-5] - 1) * 100 if closes[-5] else 0.0,
        "mom8": (c / closes[-9] - 1) * 100 if closes[-9] else 0.0,
        "mom24": (c / closes[-25] - 1) * 100 if closes[-25] else 0.0,
        "coil": float(coil["pressure"]) if coil else np.nan,
        "shape_now": float(shape_now),
        "since_shape": float(since),
        "hour": (t % 86400) / 3600.0,
    }


def forward_labels(ohlc: dict, t: int) -> dict | None:
    """What happened strictly AFTER t, per horizon -- no lookahead."""
    keys = [k for k in sorted(ohlc) if k > t]
    c = ohlc[t][3]
    if not c or len(keys) < max(HORIZONS):
        return None
    out = {}
    for H in HORIZONS:
        win = [ohlc[k] for k in keys[:H]]
        max_up = max((b[1] - c) / c * 100 for b in win)
        max_dn = max((c - b[2]) / c * 100 for b in win)
        net = (win[-1][3] / c - 1) * 100
        first = 0
        for b in win:
            up = (b[1] - c) / c * 100
            dn = (c - b[2]) / c * 100
            if up >= 2.5 and dn >= 2.5:
                first = 1 if up >= dn else -1
                break
            if up >= 2.5:
                first = 1
                break
            if dn >= 2.5:
                first = -1
                break
        out[f"up{H}"] = max_up
        out[f"dn{H}"] = max_dn
        out[f"net{H}"] = net
        out[f"dir{H}"] = float(first)
        out[f"up5_{H}"] = float(max_up >= 5.0)
        out[f"dn5_{H}"] = float(max_dn >= 5.0)
    return out


def main() -> int:
    rows = []
    print("  labelling the recorded candles...", flush=True)
    for coin in sources.council_coins():
        ohlc = coin["ohlc"]
        keys = sorted(ohlc)
        for t in keys[65:-max(HORIZONS)]:
            f = bar_features(ohlc, t)
            if f is None:
                continue
            lab = forward_labels(ohlc, t)
            if lab is None:
                continue
            rows.append({"sym": coin["sym"], "t": t, **f, **lab})
    print(f"  {len(rows)} labelled bars across "
          f"{len(sources.council_coins())} coins", flush=True)

    X = np.array([[r[f] for f in FEATURES] for r in rows], dtype=np.float32)
    np.savez_compressed(OUT / "patterns.npz", X=X,
                        t=np.array([r["t"] for r in rows], dtype=np.int64),
                        features=np.array(FEATURES))
    with open(OUT / "patterns.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    # The measured answer to "where should the eagle sit": which shapes
    # precede a 5% move within 8 bars, and which precede nothing.
    lines = ["The eagle's question, measured over %d real bars:" % len(rows),
             "P(next 8 bars reach +5% or -5%), by the shape at the bar:",
             ""]
    rows_arr = np.array([[r[f] for f in FEATURES] for r in rows])
    for name, idx, lo, hi in (
            ("quiet band (width < median)", FEATURES.index("band_width"),
             0, np.nanmedian(rows_arr[:, FEATURES.index("band_width")])),
            ("wide band (width >= median)", FEATURES.index("band_width"),
             np.nanmedian(rows_arr[:, FEATURES.index("band_width")]), 1e9),
            ("coiling pressure >= 65", FEATURES.index("coil"), 65, 1e9),
            ("expansion >= 2x typical", FEATURES.index("expansion"), 2.0, 1e9),
            ("level touched 2+ times", FEATURES.index("touches"), 2.0, 1e9),
            ("3+ up closes", FEATURES.index("n_up_closes"), 3.0, 1e9),
            ("3+ down closes", FEATURES.index("n_dn_closes"), 3.0, 1e9),
            ("near 24-bar high (<1%)", FEATURES.index("dist_high"), 0, 1.0),
            ("near 24-bar low (<1%)", FEATURES.index("dist_low"), 0, 1.0),
            ("a shape on the bar", FEATURES.index("shape_now"), 0.5, 1e9)):
        col = rows_arr[:, idx]
        mask = (col >= lo) & (col <= hi)
        mask &= ~np.isnan(col)
        if mask.sum() < 50:
            continue
        big = np.array([r["up5_8"] or r["dn5_8"] for r in rows])[mask]
        up5 = np.array([r["up5_8"] for r in rows])[mask]
        dn5 = np.array([r["dn5_8"] for r in rows])[mask]
        lines.append(
            f"  {name:<28} n={mask.sum():<6} any5% {big.mean()*100:>5.1f}%"
            f"   up5 {up5.mean()*100:>5.1f}%  dn5 {dn5.mean()*100:>5.1f}%")
    base_big = np.mean([r["up5_8"] or r["dn5_8"] for r in rows])
    lines.append(f"\n  base rate (any bar): {base_big*100:.1f}%")
    (OUT / "patterns_report.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\n  {len(rows)} rows -> {OUT}/patterns.npz + patterns.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
