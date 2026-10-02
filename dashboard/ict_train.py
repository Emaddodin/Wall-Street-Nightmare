"""Fit and judge the trend model (ictmodel.py) on M1 gold history. Needs numpy (training only; the live page doesn't).

    python3 ict_train.py data/dukascopy_xauusd_m1.csv.gz --split 2026-06-01 --check data/litefinance_xauusd_m1.csv.gz

Samples: every 5th M1 candle from 07:00 to 17:00 UTC on weekdays. Target: does the close `horizon` minutes later
end above the close now? Fitted on the candles before --split, judged on the ones after it (and on --check, a
second feed). Writes model_weights.json only with --save.
"""
from __future__ import annotations

import argparse
import json
import math
from bisect import bisect_left
from datetime import datetime, timezone

from boom_backtest import aggregate, load
from engine import Bars
from ictmodel import TFS, MarketRead, WEIGHTS, daily

STEP = 5


def frames(m1: list) -> dict:
    out = {}
    for name, sec in TFS:
        if name == "D1":
            continue
        elif name == "M1":
            b = Bars(60)
            for r in m1:
                b.append(*r[:6])
            out[name] = b
        else:
            out[name] = aggregate(m1, sec)
    out["D1"] = daily(out["H1"])
    return out


def dataset(m1: list, horizons: tuple) -> tuple:
    mr = MarketRead(frames(m1))
    t1 = [r[0] for r in m1]
    X, Y, T, A = [], [], [], []
    atr1 = mr.f["M1"].atr
    for i in range(300, len(m1), STEP):
        t = m1[i][0]
        m = t % 86400 // 60
        if not (420 <= m < 1020) or (t // 86400 + 3) % 7 >= 5:
            continue
        tc = t + 60
        ys = []
        for h in horizons:
            k = bisect_left(t1, t + h * 60)
            if k >= len(m1) or m1[k][0] != t + h * 60:
                break
            ys.append(m1[k][4] - m1[i][4])
        if len(ys) != len(horizons):
            continue
        x = mr.features(tc, m1[i][4])
        if x is None:
            continue
        X.append(x)
        Y.append(ys)
        T.append(t)
        A.append(atr1[i])
    return X, Y, T, A


def fit(Xm, y, l2: float = 1.0, iters: int = 400):
    import numpy as np
    n, k = Xm.shape
    w, b = np.zeros(k), 0.0
    lr = 0.5
    for _ in range(iters):
        z = Xm @ w + b
        p = 1 / (1 + np.exp(-z))
        g = Xm.T @ (p - y) / n + l2 * w / n
        gb = float(np.mean(p - y))
        w -= lr * g
        b -= lr * gb
    return w, b


def report(name, p, y):
    import numpy as np
    acc = float(np.mean((p > 0.5) == (y > 0.5)))
    eps = 1e-9
    ll = float(-np.mean(y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps)))
    base = float(np.mean(y))
    ll0 = -(base * math.log(base) + (1 - base) * math.log(1 - base))
    conf = p >= 0.6
    conf_n = int(np.sum(conf | (p <= 0.4)))
    conf_acc = float(np.mean(((p >= 0.6) & (y > 0.5)) | ((p <= 0.4) & (y < 0.5)))) * len(p) / max(conf_n, 1)
    band = 1.96 * math.sqrt(0.25 / len(y))
    print(f"  {name:<34} n {len(y):>6}  right {acc:6.1%} (coin +/-{band:.1%}, up share {base:.1%})  "
          f"log-loss {ll:.4f} vs {ll0:.4f}  |p-0.5|>=0.1: {conf_n} calls, right {conf_acc:.1%}")


def main() -> None:
    import numpy as np
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--split", default="2026-06-01")
    ap.add_argument("--check")
    ap.add_argument("--horizons", default="30,60,120")
    ap.add_argument("--save", type=int, help="horizon (minutes) to save as model_weights.json")
    a = ap.parse_args()
    hz = tuple(int(x) for x in a.horizons.split(","))
    m1 = load(a.csv)
    X, Y, T, A = dataset(m1, hz)
    keys = list(X[0])
    Xa = np.array([[x[k] for k in keys] for x in X])
    Ya = np.array(Y)
    Ta = np.array(T)
    cut = int(datetime.fromisoformat(a.split).replace(tzinfo=timezone.utc).timestamp())
    warm = m1[0][0] + 45 * 86400
    tr, te = (Ta >= warm) & (Ta < cut), Ta >= cut
    mean, std = Xa[tr].mean(0), Xa[tr].std(0) + 1e-9
    Z = (Xa - mean) / std
    chk = None
    if a.check:
        Xc, Yc, Tc, _ = dataset(load(a.check), hz)
        chk = (((np.array([[x[k] for k in keys] for x in Xc])) - mean) / std, np.array(Yc))
    print(f"{a.csv}: {int(tr.sum())} training samples (to {a.split}), {int(te.sum())} test samples after it")
    saved = None
    for hi, h in enumerate(hz):
        y = (Ya[:, hi] > 0).astype(float)
        w, b = fit(Z[tr], y[tr])
        p = lambda Zs: 1 / (1 + np.exp(-(Zs @ w + b)))
        print(f"\nHorizon {h} min:")
        report("model, training months", p(Z[tr]), y[tr])
        report("model, later months (out of sample)", p(Z[te]), y[te])
        if chk is not None:
            report("model, LiteFinance feed", p(chk[0]), (chk[1][:, hi] > 0).astype(float))
        for k in ("mom_M15", "st_H1", "reg_H4", "st_M15"):
            j = keys.index(k)
            s = np.sign(Xa[te, j])
            m = s != 0
            print(f"  baseline sign({k}) out of sample: right {float(np.mean((s[m] > 0) == (y[te][m] > 0))):.1%} of {int(m.sum())}")
        top = sorted(zip(keys, w), key=lambda kv: -abs(kv[1]))
        print("  weights: " + ", ".join(f"{k} {v:+.3f}" for k, v in top))
        if a.save == h:
            mv = float(np.mean(np.abs(Ya[tr, hi]) / np.maximum(np.array(A)[tr], 1e-9)))
            pt = p(Z[te])
            saved = {"weights": dict(zip(keys, map(float, w))), "bias": float(b),
                     "mean": dict(zip(keys, map(float, mean))), "std": dict(zip(keys, map(float, std))),
                     "horizon_min": h, "move_atr": mv, "samples": int(tr.sum()),
                     "trained": f"Dukascopy XAUUSD M1, {datetime.fromtimestamp(warm, timezone.utc):%Y-%m-%d} to {a.split}",
                     "tested": f"after {a.split}",
                     "accuracy_oos": round(float(np.mean((pt > 0.5) == (y[te] > 0.5))), 4),
                     "baseline_oos": round(float(max(np.mean(y[te]), 1 - np.mean(y[te]))), 4)}
    if saved:
        WEIGHTS.write_text(json.dumps(saved, indent=1))
        print(f"\nSaved {WEIGHTS}")


if __name__ == "__main__":
    main()
