#!/usr/bin/env python3
"""
The entry question: which entry survives the 1.25% stop.

Eight wins a day is a selection problem and an entry problem. Selection is
the pattern dataset's answer (an expanding candle raises the chance of a
5% move in the next two hours from 4% to 36%); this measures the entry
half on the same 10,702 real recorded paths: for every entry style the
book could take, how often the 1.25% stop is hit BEFORE the 5% target --
and the maximum adverse excursion, which is the number that decides
whether the small stop survives at all.

Entry styles, all derived from the recorded signal bar without lookahead:
  open     the recorded next-bar open (what the live book takes now)
  close    the signal bar's close
  mid      halfway from the close to the candle's extreme
  extreme  the candle's low (long) / high (short) -- a resting limit

    python3 dataset/entries.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dataset import sources  # noqa: E402

OUT = ROOT / "data" / "dataset"
SL, TP = 1.25, 5.0


def edge_return_bars(adv, side: str, bar: dict, entry: float) -> int | None:
    """Bars until price returns to the signal candle's extreme, 1-based.

    The extreme is the low for a BUY and the high for a SELL, so the
    move back to it is ADVERSE for both sides -- adv is the side-relative
    adverse move. Reading fav for the SELL rows fed the down-move into a
    question about an up-move and quietly mirrored the answer.
    """
    c = float(bar.get("c") or entry)
    if side == "BUY":
        shift = (c - float(bar.get("l") or c)) / c * 100
    else:
        shift = (float(bar.get("h") or c) - c) / c * 100
    if shift <= 0:
        return None
    for i in range(min(8, len(adv))):
        if adv[i] >= shift:
            return i + 1
    return None


def outcome_for(side: str, entry: float, shift: float, fav, adv):
    """The result of entering at entry*(1-shift) on a real path.

    Returns (result, mae_pct, bars): result in stop/target/open, mae the
    worst adverse excursion, spanning-bar-is-a-stop as everywhere else.
    """
    up = side == "BUY"
    mae = 0.0
    for i in range(min(96, len(fav))):
        f = fav[i] / 100.0
        a = adv[i] / 100.0
        if up:
            adv_pct = (a - shift) / (1 - shift) * 100
            fav_pct = (f + shift) / (1 - shift) * 100
        else:
            adv_pct = (a - shift) / (1 + shift) * 100
            fav_pct = (f + shift) / (1 + shift) * 100
        mae = max(mae, max(0.0, adv_pct))
        hit_sl = adv_pct >= SL
        hit_tp = fav_pct >= TP
        if hit_sl:
            return "stop", mae, i + 1
        if hit_tp:
            return "target", mae, i + 1
    return "open", mae, min(96, len(fav))


def main() -> int:
    rows = []
    for sig in sources.joined():
        out = sig.get("outcome") or {}
        fav, adv, entry = out.get("fav"), out.get("adv"), out.get("entry")
        if not fav or not adv or not entry:
            continue
        side = sig["side"]
        bar = out.get("bar") or {}
        c = float(bar.get("c") or entry)
        if side == "BUY":
            extreme_shift = (c - float(bar.get("l") or c)) / c
        else:
            extreme_shift = (float(bar.get("h") or c) - c) / c
        extreme_shift = max(0.0, extreme_shift)
        for style, shift in (("open", 0.0), ("close", 0.0),
                             ("mid", extreme_shift / 2),
                             ("extreme", extreme_shift)):
            result, mae, nbars = outcome_for(side, entry, shift, fav, adv)
            rows.append({"id": sig["id"], "side": side, "style": style,
                         "shift_pct": round(shift * 100, 3),
                         "mae_pct": round(mae, 3), "result": result,
                         "bars": nbars})

    with open(OUT / "entry_quality.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    print(f"  {len(rows)} style x path combinations over "
          f"{len(rows) // 4} real recorded paths\n")
    print(f"  {'style':<10} {'stop':>7} {'target':>8} {'open':>6} "
          f"{'median MAE':>11} {'p90 MAE':>9}")
    by_style = defaultdict(list)
    for r in rows:
        by_style[r["style"]].append(r)
    for style in ("open", "close", "mid", "extreme"):
        got = by_style[style]
        n = len(got)
        stop = sum(1 for r in got if r["result"] == "stop") / n * 100
        tgt = sum(1 for r in got if r["result"] == "target") / n * 100
        opn = sum(1 for r in got if r["result"] == "open") / n * 100
        maes = sorted(r["mae_pct"] for r in got)
        med = maes[n // 2]
        p90 = maes[int(n * 0.9)]
        print(f"  {style:<10} {stop:>6.1f}% {tgt:>7.1f}% {opn:>5.1f}% "
              f"{med:>10.2f}% {p90:>8.2f}%")
    # the honest headline: how much of the stop problem is entry timing
    o = by_style["open"]
    e = by_style["extreme"]
    o_stop = sum(1 for r in o if r["result"] == "stop") / len(o) * 100
    e_stop = sum(1 for r in e if r["result"] == "stop") / len(e) * 100
    print(f"\n  entering at the candle's extreme takes the stop rate from "
          f"{o_stop:.1f}% to {e_stop:.1f}% -- and nothing can make it zero; "
          f"the paths that stop at the extreme genuinely went 1.25% past "
          f"the extreme.")

    # The edge fill question: of the signals whose candle extreme would be
    # the better entry, how many does price actually RETURN to within the
    # eight-bar window -- the miss rate of --entry edge on real paths.
    filled, missed = [], []
    for sig in sources.joined():
        out = sig.get("outcome") or {}
        fav, adv, entry = out.get("fav"), out.get("adv"), out.get("entry")
        if not fav or not adv or not entry:
            continue
        got = edge_return_bars(adv, sig["side"], out.get("bar") or {}, entry)
        if got:
            filled.append(got)
        else:
            missed.append(1)
    n = len(filled) + len(missed)
    if n:
        print("\n  --entry edge, on the recorded paths:")
        print(f"  price returns to the candle extreme within 8 bars: "
              f"{len(filled)}/{n} ({100*len(filled)/n:.0f}%); median "
              f"{sorted(filled)[len(filled)//2] if filled else '-'} bar(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
