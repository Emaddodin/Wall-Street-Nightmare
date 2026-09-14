#!/usr/bin/env python3
"""
The learned selector: can the "makes sense" filter be learned from the record?

The operator's thesis: the golden triangle (Tesla + Bank + Team45, the combo
source) produces good signals, and the missing piece is the human filter
that knows which of them make sense. The record has every triangle signal,
its features at signal time, and what price did afterwards -- so the filter
can be LEARNED instead of guessed: a logistic regression over the recorded
features, trained day-by-day (walk-forward, no lookahead), asked one
question: does this signal reach +5% before -1.25%?

The question is the one the LIVE book asks now (the 50% rule): does
this signal reach its own target -- 50% of the margin, i.e. 50/lev %
of price -- before the coin's own ATR stop? Each row is labelled by
walking its recorded path with ITS OWN geometry, so the model and the
book can never drift apart again (they did once: the model kept asking
+5% before -1.25% for days after the book moved on).

The report answers three things:
  1. does a learned selector beat the population on the unseen day?
  2. does it beat the book's own hand-weighted entry score?
  3. which features does the data say carry the edge (the coefficients)?

Nothing here trades. Read-only over recorded data.
"""
from __future__ import annotations

import json
import os
import sys
import time
import tempfile
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import papertrade as P  # noqa: E402
import pace  # noqa: E402
from dataset import engine, sources  # noqa: E402
from dataset.make_dataset import measures_for  # noqa: E402

TP, SL = 5.0, 1.25
FEATURES = ["atr", "trend", "vol20", "mom6h", "mom1h", "volx", "agents",
            "tier", "score", "who", "counter", "side", "hour"]


def rows_with_features() -> list[dict]:
    tmpf = Path(tempfile.mkdtemp(prefix="tbt-sel-")) / "atr_measures.json"
    out = []
    for sig in sources.joined():
        sym, t, side = sig["sym"], int(sig["t"]), sig["side"]
        meas = measures_for(sym, t, sig)
        entry = float((sig.get("outcome") or {}).get("entry") or
                      sig.get("px") or 0)
        if not entry:
            continue
        # The label: the live rule's own walk, per-signal geometry.
        # stop = 1 x the coin's ATR, leverage = min(cap, 1/(stop+maint)),
        # target = 50/lev % of price. TP/SL remain only the fallback
        # for a coin whose ATR cannot be read.
        atr = meas.get("atr")
        if atr is not None and atr > 0:
            _lev = min(50.0, 100.0 / (atr + 0.5))
            _tp_pct, _sl_pct = 50.0 / _lev, atr
        else:
            _tp_pct, _sl_pct = TP, SL
        out_fav = (sig.get("outcome") or {}).get("fav")
        out_adv = (sig.get("outcome") or {}).get("adv")
        if not out_fav or not out_adv:
            continue
        from dataset.pullback import walk as _walk
        _f = out_adv if side == "SELL" else out_fav
        _a = out_fav if side == "SELL" else out_adv
        reason, _ex, _best = _walk(_f, _a, _tp_pct, _sl_pct)
        tmpf.write_text(json.dumps({sym: meas}))
        P._ATR.update({"t": 0.0, "by": {}, "trend": {}, "shape": {},
                       "stamp": 0})
        with mock.patch.object(P, "ATR_M", tmpf):
            pts, _why = P.entry_score(sym, side,
                                      int(sig.get("agents") or 0))
        out.append({
            "t": t, "sym": sym,
            "atr": meas.get("atr"), "trend": meas.get("trend"),
            "vol20": meas.get("vol20"), "mom6h": meas.get("mom6h"),
            "mom1h": meas.get("mom1h"), "volx": meas.get("volx"),
            "agents": float(sig.get("agents") or 0),
            "tier": float(sig.get("tier") or 0),
            "score": float(sig.get("score") or 0),
            "who": float(sig.get("who") or 0),
            "counter": float(bool(sig.get("counter"))),
            "side": 1.0 if side == "BUY" else -1.0,
            "hour": (t % 86400) / 3600.0,
            "hit": 1.0 if reason == "target" else 0.0,
            "pts": pts,
        })
    tmpf.parent.joinpath("x").unlink(missing_ok=True)
    import shutil
    shutil.rmtree(tmpf.parent, ignore_errors=True)
    return out


def split_by_day(rows: list[dict], test_days: int = 1):
    """Walk-forward: train on everything before the last `test_days`
    trading days, test on them. No test row is ever seen in training."""
    days = sorted({pace.day_start(r["t"], offset_h=17.5) for r in rows})
    test_starts = set(days[-test_days:]) if test_days else set()
    train = [r for r in rows
             if pace.day_start(r["t"], offset_h=17.5) not in test_starts]
    test = [r for r in rows
            if pace.day_start(r["t"], offset_h=17.5) in test_starts]
    return train, test


def build_matrix(rows: list[dict]):
    """X with median imputation plus missingness flags; y; indices."""
    n = len(rows)
    X = np.zeros((n, len(FEATURES) * 2))
    y = np.zeros(n)
    for i, r in enumerate(rows):
        y[i] = r["hit"]
        for j, f in enumerate(FEATURES):
            v = r.get(f)
            if v is None or v != v:
                X[i, len(FEATURES) + j] = 1.0
            else:
                X[i, j] = v
    # median imputation, from the training fold only
    return X, y


def median_impute(X_train, X_test):
    for j in range(len(FEATURES)):
        col = X_train[:, j]
        present = X_train[:, len(FEATURES) + j] == 0
        med = float(np.median(col[present])) if present.any() else 0.0
        for X in (X_train, X_test):
            X[:, j] = np.where(X[:, len(FEATURES) + j] == 1, med, X[:, j])
    return X_train, X_test


def standardize(X_train, X_test):
    mu = X_train.mean(axis=0)
    sd = X_train.std(axis=0)
    sd[sd < 1e-9] = 1.0
    return (X_train - mu) / sd, (X_test - mu) / sd, mu, sd


def logistic(X, y, iters=300, lr=0.05, l2=1.0, seed=0, w0=None, b0=0.0):
    rng = np.random.default_rng(seed)
    n, k = X.shape
    w = w0.copy() if w0 is not None else rng.normal(0, 0.02, k)
    b = float(b0)
    for _ in range(iters):
        z = X @ w + b
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        g = X.T @ (p - y) / n + l2 * w / n
        w -= lr * g
        b -= lr * float((p - y).mean())
    return w, b


def predict_proba(X, w, b):
    z = X @ w + b
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def auc(y, score):
    """Rank-sum AUC: how often a random winner outranks a random loser."""
    wins = score[y == 1]
    losses = score[y == 0]
    if len(wins) == 0 or len(losses) == 0:
        return 0.5
    return float((wins[:, None] > losses[None, :]).mean())


def top_slice_hit(y, score, frac=0.1):
    order = np.argsort(-score)
    k = max(1, int(len(order) * frac))
    sel = order[:k]
    return y[sel].mean(), len(sel)


def pretrain(npz_path: str, max_rows: int = 5_000_000, iters: int = 60):
    """Train on the million-sample record alone; return (w, b, mu, sd)
    so real days can be scored WITHOUT seeing any real training row."""
    d = np.load(npz_path)
    X = d["X"]
    y = d["y"].astype(float)
    if len(y) > max_rows:
        rng = np.random.default_rng(0)
        keep = rng.choice(len(y), max_rows, replace=False)
        X, y = X[keep], y[keep]
    flags = np.isnan(X).astype(np.float32)
    Xn = np.where(flags == 1, 0.0, X)
    med = np.zeros(X.shape[1], dtype=np.float32)
    for j in range(X.shape[1]):
        col = Xn[:, j][flags[:, j] == 0]
        med[j] = float(np.median(col)) if len(col) else 0.0
    Xn = np.where(flags == 1, med, Xn)
    Xfull = np.concatenate([Xn, flags], axis=1)
    mu = Xfull.mean(axis=0)
    sd = Xfull.std(axis=0)
    sd[sd < 1e-9] = 1.0
    Xs = (Xfull - mu) / sd
    w, b = logistic(Xs, y, iters=iters, lr=0.05, l2=1.0, seed=0)
    return w, b, mu, sd, med


def score_with_stats(X_raw, flags, mu, sd, med, w, b):
    """Score real rows in the sample's own scale -- no real statistics.

    Missing features are imputed with the SAME medians the pretraining
    used; plugging zeros would hand the model values it never saw.
    """
    Xn = np.where(flags == 1, med, X_raw)
    Xs = (np.concatenate([Xn, flags], axis=1) - mu) / sd
    return predict_proba(Xs, w, b)


def save_model(path: str) -> dict:
    """Train on every recorded row and save the live filter's artifact.

    The walk-forward evaluation answers 'does it generalise'; the live book
    uses a model trained on everything known today. The artifact carries
    the weights, the scale, the imputation medians, and the probability at
    the top-10% cut -- the default floor for the paper run.
    """
    rows = rows_with_features()
    X, y = build_matrix(rows)
    # Raw-space imputation medians, so the live scorer can plug them in
    # BEFORE standardising -- the same order the training used.
    med = np.zeros(len(FEATURES))
    Xr = X[:, :len(FEATURES)]
    Xf = X[:, len(FEATURES):]
    for j in range(len(FEATURES)):
        col = Xr[:, j][Xf[:, j] == 0]
        med[j] = float(np.median(col)) if len(col) else 0.0
    X, _ = median_impute(X, X)
    X, _, mu, sd = standardize(X, X)
    w, b = logistic(X, y, iters=300, lr=0.05, seed=0)
    prob = predict_proba(X, w, b)
    threshold = float(np.quantile(prob, 0.90))
    out = {
        "w": w.astype(np.float32), "b": float(b),
        "mu": mu.astype(np.float32), "sd": sd.astype(np.float32),
        "med": med.astype(np.float32),
        "features": np.array(FEATURES),
        "threshold": threshold, "n": int(len(rows)),
        "trained_at": int(time.time()),
        "hit_at_threshold": float(y[prob >= threshold].mean())
        if (prob >= threshold).any() else 0.0,
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # Atomic: two books hot-reload this file every minute, and a torn
    # write would hand them a half-saved artifact. Write beside it and
    # swap, so a reader sees the old model or the new one, never a mix.
    # (numpy appends .npz to any name that does not end in it, hence the
    # double suffix.)
    tmp = path + ".tmp.npz"
    np.savez_compressed(tmp, **out)
    os.replace(tmp, path)
    return out


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", default=None,
                    help="a sampler features.npz to pretrain on; the real "
                         "walk-forward days then test whether synthetic "
                         "training alone generalises")
    ap.add_argument("--save-model", default=None,
                    help="train on every recorded row and save the live "
                         "filter artifact to this path")
    args = ap.parse_args()

    if args.save_model:
        got = save_model(args.save_model)
        print(f"  saved the live filter: {got['n']} rows, threshold "
              f"{got['threshold']:.3f} (top-10% cut), hit rate above it "
              f"{got['hit_at_threshold']*100:.1f}%")
        return 0

    rows = rows_with_features()
    print(f"  {len(rows)} triangle signals with features and outcomes\n")
    days = sorted({pace.day_start(r["t"], offset_h=17.5) for r in rows})
    print(f"  {len(days)} trading days of recorded data; leave-one-day-out "
          f"walk-forward (every row is scored by a model that never saw "
          f"its day)\n")

    pooled_y, pooled_prob, pooled_pts = [], [], []
    if args.samples:
        pooled_pre = []
        pooled_ft = []
        w_pre, b_pre, mu_pre, sd_pre, med_pre = pretrain(args.samples)
        print("  pretrained on the synthetic sample alone\n")
    for i in range(2, len(days)):
        d = days[i]
        train = [r for r in rows
                 if pace.day_start(r["t"], offset_h=17.5) < d]
        test = [r for r in rows
                if pace.day_start(r["t"], offset_h=17.5) == d]
        if not test:
            continue
        Xtr, ytr = build_matrix(train)
        Xte, yte = build_matrix(test)
        Xtr, Xte = median_impute(Xtr, Xte)
        Xtr, Xte, _mu, _sd = standardize(Xtr, Xte)
        w, b = logistic(Xtr, ytr)
        prob = predict_proba(Xte, w, b)
        pooled_y.append(yte)
        pooled_prob.append(prob)
        pooled_pts.append(np.array([r["pts"] for r in test]))
        if args.samples:
            Xte_raw = build_matrix(test)[0]
            Xte13, Xte_fl = (Xte_raw[:, :len(FEATURES)],
                             Xte_raw[:, len(FEATURES):])
            pooled_pre.append(score_with_stats(Xte13, Xte_fl, mu_pre,
                                               sd_pre, med_pre,
                                               w_pre, b_pre))
            # transfer: warm-start from the synthetic weights, a few steps
            # on the real history, everything in the sample's own scale --
            # no real statistics anywhere.
            Xtr_raw = build_matrix(train)[0]
            Xtr13, Xtr_fl = (Xtr_raw[:, :len(FEATURES)],
                             Xtr_raw[:, len(FEATURES):])
            Xtr_s = (np.concatenate(
                [np.where(Xtr_fl == 1, med_pre, Xtr13), Xtr_fl], axis=1)
                - mu_pre) / sd_pre
            w_ft, b_ft = logistic(Xtr_s, ytr, iters=40, lr=0.05, seed=1,
                                  w0=w_pre, b0=b_pre)
            pooled_ft.append(score_with_stats(Xte13, Xte_fl, mu_pre,
                                              sd_pre, med_pre,
                                              w_ft, b_ft))
    y = np.concatenate(pooled_y)
    prob = np.concatenate(pooled_prob)
    pts = np.concatenate(pooled_pts)

    base = y.mean()
    print(f"  the unseen days, unsliced: {base*100:.1f}% hit "
          f"(n={len(y)})\n")
    print(f"  {'slice':<34} {'hit':>7} {'n':>6}   (does the selector find "
          f"the winners?)")
    for frac in (0.05, 0.10, 0.20):
        hp, n = top_slice_hit(y, prob, frac)
        sp, ns = top_slice_hit(y, pts, frac)
        print(f"  top {frac*100:>3.0f}% by learned model        {hp*100:>6.1f}% "
              f"{n:>6}")
        if args.samples:
            pre = np.concatenate(pooled_pre)
            ft = np.concatenate(pooled_ft)
            pp, np_ = top_slice_hit(y, pre, frac)
            fhp, nf = top_slice_hit(y, ft, frac)
            print(f"  top {frac*100:>3.0f}% by synthetic-pretrained {pp*100:>6.1f}% "
                  f"{np_:>6}")
            print(f"  top {frac*100:>3.0f}% by synthetic + fine-tuned {fhp*100:>6.1f}% "
                  f"{nf:>6}")
        print(f"  top {frac*100:>3.0f}% by the entry score      {sp*100:>6.1f}% "
              f"{ns:>6}")
    print(f"\n  AUC learned model {auc(y, prob):.3f}   "
          f"AUC entry score {auc(y, pts):.3f}")
    if args.samples:
        pre = np.concatenate(pooled_pre)
        ft = np.concatenate(pooled_ft)
        print(f"  AUC synthetic-pretrained (never saw a real row) "
              f"{auc(y, pre):.3f}")
        print(f"  AUC synthetic + fine-tuned on real history "
              f"{auc(y, ft):.3f}")
    print()

    print("  what the data says carries the edge (coefficients, "
          "standardised):")
    order = np.argsort(-np.abs(w[:len(FEATURES)]))
    for j in order:
        name = FEATURES[j]
        coef = w[j]
        if abs(coef) < 0.01:
            continue
        print(f"    {name:<10} {coef:+.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
