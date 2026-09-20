#!/usr/bin/env python3
"""
The choose-stage question, under the rule the book ACTUALLY runs now.

The learned filter was trained for +5% before -1.25%. The book now
runs the 50% rule: stop = 1 x the coin's ATR, target = 50/lev % of
price. A model answering the old question is measuring the wrong thing
-- this script measures what the recorded data says about selection
under the NEW question, feature by feature, and whether a selector
trained on the new labels can lift the win rate to what the rule
needs (above ~53% with fees).

    python3 dataset/choose.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pace  # noqa: E402
from dataset import sources  # noqa: E402
from dataset.make_dataset import measures_for  # noqa: E402
from dataset.selector import (FEATURES, auc, logistic, median_impute,  # noqa: E402
                              predict_proba, standardize, top_slice_hit)
from dataset.pullback import walk  # noqa: E402

MAINT = 0.5
CAP = 50.0


def rows_for_the_new_rule() -> list[dict]:
    rows = []
    for sig in sources.joined():
        out = sig.get("outcome") or {}
        fav, adv = out.get("fav"), out.get("adv")
        if not fav or not adv or not out.get("entry"):
            continue
        meas = measures_for(sig["sym"], int(sig["t"]), sig)
        atr = meas.get("atr")
        if atr is None or atr <= 0:
            continue
        sl_pct = atr
        lev = min(CAP, 100.0 / (sl_pct + MAINT))
        tp_pct = 50.0 / lev
        sell = sig["side"] == "SELL"
        f = adv if sell else fav
        a = fav if sell else adv
        got, _ex, _best = walk(f, a, tp_pct, sl_pct)
        r = {"t": int(sig["t"]), "hit": float(got == "target"),
             "atr": meas.get("atr"), "trend": meas.get("trend"),
             "vol20": meas.get("vol20"), "mom6h": meas.get("mom6h"),
             "mom1h": meas.get("mom1h"), "volx": meas.get("volx"),
             "agents": float(sig.get("agents") or 0),
             "tier": float(sig.get("tier") or 0),
             "score": float(sig.get("score") or 0),
             "who": float(sig.get("who") or 0),
             "counter": float(bool(sig.get("counter"))),
             "side": 1.0 if not sell else -1.0,
             "hour": (int(sig["t"]) % 86400) / 3600.0,
             "sym": sig["sym"]}
        rows.append(r)
    return rows


def main() -> int:
    rows = rows_for_the_new_rule()
    n = len(rows)
    base = sum(r["hit"] for r in rows) / n * 100
    print(f"{n} signals, new-rule win rate (the number selection must "
          f"lift): {base:.1f}%   (break-even ~53% with fees)\n")

    def by(fn, label, buckets):
        print(f"  {label}")
        for lo, hi in buckets:
            sub = [r for r in rows if lo <= fn(r) < hi]
            if len(sub) < 40:
                continue
            print(f"    {lo:>6}..{hi:<6} n={len(sub):>6}  "
                  f"win {sum(r['hit'] for r in sub)/len(sub)*100:5.1f}%")
        print()

    by(lambda r: r["agents"], "agents:", [(0, 3), (3, 4), (4, 5), (5, 9)])
    by(lambda r: r["score"], "score:", [(0, 25), (25, 40), (40, 60),
                                        (60, 101)])
    by(lambda r: r["tier"], "tier:", [(1, 2), (2, 3), (3, 4)])
    by(lambda r: r["who"], "who (colour):", [(1, 2), (2, 3), (3, 4)])
    by(lambda r: r["atr"] or 0, "coin ATR%:", [(0, 2), (2, 3.5),
                                                (3.5, 5), (5, 40)])
    by(lambda r: r["trend"] if r["trend"] is not None else 0,
       "coin trend%:", [(-99, -5), (-5, 0), (0, 5), (5, 99)])

    # The old filter, answering the old question, scored on the new one.
    try:
        import filter_model as FM
        m = FM.FilterModel(str(ROOT / "data" / "dataset" / "samples"
                                / "model.npz"))
        prob = np.array([m.score(r) for r in rows])
        cut = np.quantile(prob, 0.90)
        top = np.array([r["hit"] for r in rows])[prob >= cut]
        print(f"  the OLD filter (trained for +5%/-1.25%) on the new rule: "
              f"top-10% (cut {cut:.3f}) wins {top.mean()*100:.1f}% of "
              f"{len(top)} -- base {base:.1f}%")
        auc_now = auc(np.array([r["hit"] for r in rows]), prob)
        print(f"  its AUC under the new rule: {auc_now:.3f} (1.0 perfect, "
              f"0.5 noise)\n")
    except Exception as e:
        print(f"  old model unreadable: {str(e)[:80]}\n")

    # A selector trained on the NEW labels, walk-forward by day.
    X, y = [], []
    for r in rows:
        vec = [r[f] for f in FEATURES]
        flags = [1.0 if v is None else 0.0 for v in vec]
        X.append([0.0 if v is None else float(v) for v in vec] + flags)
        y.append(r["hit"])
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    days = sorted({pace.day_start(r["t"], offset_h=17.5) for r in rows})
    pooled, pooled_hit = [], []
    for d in days[2:]:
        tr = [i for i, r in enumerate(rows)
              if pace.day_start(r["t"], offset_h=17.5) < d]
        te = [i for i, r in enumerate(rows)
              if pace.day_start(r["t"], offset_h=17.5) == d]
        if not tr or not te:
            continue
        Xtr, Xte = X[tr], X[te]
        Xtr, Xte = median_impute(Xtr, Xte)
        Xtr, Xte, mu, sd = standardize(Xtr, Xte)
        w, b = logistic(Xtr, y[tr], iters=300, lr=0.05, seed=0)
        p = predict_proba(Xte, w, b)
        pooled += list(p)
        pooled_hit += list(y[te])
    pooled = np.array(pooled)
    pooled_hit = np.array(pooled_hit)
    print(f"  a selector trained on the NEW labels, walk-forward "
          f"(leave-one-day-out):")
    print(f"    AUC {auc(pooled_hit, pooled):.3f}")
    for frac in (0.5, 0.2, 0.1, 0.05):
        h, nn = top_slice_hit(pooled_hit, pooled, frac)
        print(f"    top {frac*100:>3.0f}%: wins {h*100:5.1f}% of {nn}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
