"""Train and judge the quant model (quant.py): will gold be higher in 30 minutes, and by how much?

    python3 quant_train.py data/dukascopy_xauusd_m1.csv.gz --split 2026-06-01
    python3 quant_train.py data/dukascopy_xauusd_m1.csv.gz --silver data/dukascopy_xagusd_m1.csv.gz \\
        --dxy data/dukascopy_dollaridxusd_m1.csv.gz --split 2026-06-01 --step 5 --out ~/.golddesk/quant_model.json
    python3 quant_train.py --mt5 --split 2026-08-15          # your broker's own M1 candles, via the MT5 bridge EA
    python3 quant_train.py --sessions data/dukascopy_xauusd_m1.csv.gz ...    # the London / overlap / NY session
                                                                              # models: see quant_sessions_train.py

What it does, in order:
  1. Features (quant_features.py, the same code the live page runs) every --step M1 candles; the label is the
     close 30 minutes later against the close now: up or not (classification) and the move in units of
     M1 ATR(14) x sqrt(30) (regression). Samples whose 30 minutes run into a market close are skipped.
  2. Purging: no training sample's 30-minute label may reach past --split minus --embargo minutes. The months
     from --split on are held out and looked at once, at the end.
  3. Purged K-fold cross-validation on the training months (Lopez de Prado): contiguous folds; the training part
     of each fold drops samples whose label window overlaps the validation fold, plus an embargo after it. It
     picks the tree depth / leaf size and, by early stopping on each fold, the number of trees.
  4. Learner: gradient-boosted shallow trees written here in numpy (histogram splits on quantile bins,
     shrinkage, row and column subsampling): log-loss for direction, Huber for the move. Probabilities are
     Platt-calibrated on the CV folds' out-of-fold forecasts (each made by a model that never saw them; the
     last fold alone let its own up-share leak into the calibration).
  5. Held-out report: accuracy against a coin with the 95% range for that many independent 30-minute windows
     (samples every 5 minutes overlap, so n_eff = n x step / 30), Brier skill against always-50% (with a 95%
     range from resampling whole days), log-loss, AUC, a calibration table, the regression's correlation, a
     "trade when |p - 0.5| > x" P/L after the 0.22 spread per 0.01 lot, feature importance (gain), month by
     month and by part of the New York day.
  6. ~/.golddesk/quant_model.json: trees for both models, Platt, bins, periods, metrics and
     beats_coin = held-out Brier skill > 0 AND accuracy's lower 95% bound > 50%. The live page reads it;
     a model that doesn't beat the coin is shown as such and gets ~0 weight next to Kronos.

Needs numpy (pip install numpy) for training only. Kronos isn't replayed (minutes per forecast): its two
inputs are 0 here, so the trees never use them and Kronos joins through quant.blend_with_kronos instead.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import sys
import time
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path

import quant_features as qf
from engine import Bars, ny7_offset

try:
    import numpy as np
except ImportError:                                  # the live page never imports this file
    np = None

MODEL_FILE = Path.home() / ".golddesk" / "quant_model.json"
VERSION = 1
MODEL_NAME = "quant-gbm v1"
SPREAD = 0.22                                        # $ per 0.01 lot (1 oz) round trip
EDGE = 0.04                                          # the page's UP / DOWN threshold on |p - 0.5|
P_LO, P_HI = 0.01, 0.99
DAY = 86400


def need_numpy() -> None:
    if np is None:
        raise SystemExit("Training needs numpy: python3 -m pip install numpy  (the live page doesn't).")


# ====================================================================== data
def load_csv(path: str) -> Bars:
    """Candles from fetch_history.py's CSV (gzip or plain): time (UTC epoch s or ms, or ISO), open, high, low,
    close[, volume]. The timeframe is read off the spacing (M1, H1, ...)."""
    op = gzip.open if str(path).endswith(".gz") else open
    rows = {}
    with op(Path(path).expanduser(), "rt", newline="") as f:
        rd = csv.reader(f)
        head = [h.strip().lower() for h in next(rd)]
        ix = [head.index(k) for k in ("time", "open", "high", "low", "close")]
        iv = head.index("volume") if "volume" in head else (head.index("tick_volume") if "tick_volume" in head else None)
        for r in rd:
            if not r:
                continue
            raw = r[ix[0]]
            try:
                t = float(raw)
                t = int(t / 1000 if t > 1e11 else t)
            except ValueError:
                d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                t = int((d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp())
            rows[t] = (t, float(r[ix[1]]), float(r[ix[2]]), float(r[ix[3]]), float(r[ix[4]]),
                       float(r[iv]) if iv is not None and r[iv] else 0.0)
    ts = sorted(rows)
    if len(ts) < 3:
        raise SystemExit(f"{path}: not enough candles")
    gaps = sorted(b - a for a, b in zip(ts[:2000], ts[1:2001]))
    sec = gaps[len(gaps) // 4] or 60
    b = Bars(sec)
    for t in ts:
        b.append(*rows[t])
    return b


def offset_rule(told: int | None, t_now: int, fixed_hours: float | None):
    """Broker server clock -> UTC offset function. Most gold brokers run New York + 7 (UTC+2 / +3 with US
    daylight saving): when MT5's own offset matches that rule, history uses it day by day; otherwise the
    offset MT5 reports (or --utc-offset) is taken as fixed."""
    if fixed_hours is not None:
        off = int(round(fixed_hours * 3600))
        return lambda t: off
    if told is None or told == ny7_offset(t_now):
        return ny7_offset
    return lambda t: told


def to_utc(b: Bars, off) -> Bars:
    out, last = Bars(b.sec), None
    for i in range(len(b)):
        t = b.t[i] - off(b.t[i])
        if last is not None and t <= last:
            continue                                  # a clock change can repeat an hour: keep the first
        out.append(t, b.o[i], b.h[i], b.l[i], b.c[i], b.v[i] if i < len(b.v) else 0.0)
        last = t
    return out


def load_mt5(tf: str, count: int, folder: str | None = None, utc_offset: float | None = None,
             silver: bool = True, log=print) -> tuple:
    """(gold Bars in UTC, silver Bars in UTC or None, what) from the running Gold Desk MT5 bridge: the same
    broker candles the chart shows. The forming candle is dropped."""
    from mt5bridge import MT5BridgeSource
    src = MT5BridgeSource(folder)
    b = src.rates(tf, count)
    b = qf.sub(b, 0, len(b) - 1)
    if len(b) < 100:
        raise SystemExit(f"MT5 gave only {len(b)} {tf} candles. Raise 'Max bars in chart' in MT5's options "
                         "(Tools > Options > Charts) and scroll the chart back to load more history.")
    off = offset_rule(src.server_offset(), b.t[-1], utc_offset)
    gold = to_utc(b, off)
    what = f"MT5 {src.symbol} {tf}, {len(gold)} candles (server clock -> UTC: " + (
        "New York + 7 rule" if off is ny7_offset else f"fixed {off(0) / 3600:+.1f} h") + ")"
    sv = None
    if silver:
        sym = src.symbol
        cands = [sym.replace("XAU", "XAG"), sym.replace("GOLD", "SILVER"), "XAGUSD", "XAGUSDm", "SILVER"]
        for s in dict.fromkeys(c for c in cands if c and c != sym):
            try:
                x = src.rates_of(s, tf, count)
            except LookupError:
                continue
            except NotImplementedError as e:
                log(f"  silver: {e}")
                break
            except RuntimeError as e:
                log(f"  silver {s}: {e}")
                continue
            if len(x) > 100:
                sv = to_utc(qf.sub(x, 0, len(x) - 1), off)
                what += f"; silver {s} {len(sv)} candles"
                break
    return gold, sv, what


def as_m1(b: Bars | None, name: str) -> Bars | None:
    if b is not None and b.sec != 60:
        raise SystemExit(f"{name}: the 30-minute model needs M1 candles (got {b.sec // 60}-minute ones).")
    return b


def utc_day(t: float) -> str:
    return datetime.fromtimestamp(int(t), timezone.utc).strftime("%Y-%m-%d")


def parse_day(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())


# ====================================================================== dataset
def build_dataset(m1: Bars, silver: Bars | None = None, dxy: Bars | None = None, step: int = 5,
                  warm_days: float = 2.0, log=print) -> dict:
    """Samples every `step` M1 candles after `warm_days` of history. Each: features at the close of candle i,
    the 30-minute move in units (M1 ATR x sqrt(30)) and the prices for the P/L check."""
    need_numpy()
    t0 = time.time()
    fb = qf.FeatureBuilder(m1, silver=silver, dxy=dxy)
    t, c = m1.t, m1.c
    n = len(m1)
    i0 = max(qf.MIN_M1, bisect_right(t, t[0] + int(warm_days * DAY)))
    H = qf.HORIZON * 60
    X, mv, tq, px, px1, unit, sess = [], [], [], [], [], [], []
    todo = max(1, (n - i0) // step)
    for k, i in enumerate(range(i0, n, step)):
        target = t[i] + H
        j = bisect_right(t, target, i) - 1
        if j <= i or t[j] < target - 300:
            continue                                   # the 30 minutes run into a close / gap
        if c[j] == c[i]:
            continue
        v = fb.vector(i)
        if v is None:
            continue
        u = fb.unit(i)
        X.append(v)
        mv.append((c[j] - c[i]) / u)
        tq.append(t[i] + 60)
        px.append(c[i])
        px1.append(c[j])
        unit.append(u)
        sess.append(qf.session_name(qf.ny_clock(t[i] + 60) % DAY // 60))
        if log and k and k % 5000 == 0:
            el = time.time() - t0
            log(f"\r  features: {utc_day(t[i])}  {len(X)} samples  {el:.0f} s, ~{el / k * (todo - k):.0f} s left",
                end="", flush=True)
    if log:
        log(f"\r  features: {len(X)} samples in {time.time() - t0:.0f} s{' ' * 30}")
    if not X:
        raise SystemExit("No samples: need more history (at least a few days of M1 candles).")
    return {"X": np.array(X, dtype=float), "move": np.array(mv), "up": (np.array(mv) > 0).astype(float),
            "t": np.array(tq, dtype=np.int64), "price": np.array(px), "price_end": np.array(px1),
            "unit": np.array(unit), "session": np.array(sess), "features": list(qf.FEATURES), "step": step,
            "horizon_s": H, "silver": silver is not None, "dxy": dxy is not None}


# ====================================================================== binning
def make_edges(X, max_bins: int = 32) -> list:
    """Per feature: sorted unique quantile cut points (at most max_bins - 1). bin(x) = number of edges < x, so
    'bin <= b' is exactly 'x <= edges[b]'."""
    qs = np.linspace(0, 1, max_bins + 1)[1:-1]
    return [np.unique(np.quantile(X[:, f], qs)) for f in range(X.shape[1])]


def apply_bins(X, edges) -> "np.ndarray":
    """Feature-major uint8 bin matrix (F, n)."""
    out = np.empty((X.shape[1], X.shape[0]), dtype=np.uint8)
    for f, e in enumerate(edges):
        out[f] = np.searchsorted(e, X[:, f], side="left")
    return out


# ====================================================================== the learner
def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


class GBM:
    """Gradient-boosted trees of fixed depth on binned features (feature-major uint8, see apply_bins).
    loss "logloss" (y in {0, 1}, margin = log-odds) or "huber" (y real). Trees are complete binary heaps:
    node k's children are 2k+1 (bin <= b, i.e. x <= threshold) and 2k+2; feat -1 marks a leaf; every node keeps
    its own value (shrunk Newton step) so per-prediction contributions can be read off the path (Saabas)."""

    def __init__(self, loss: str = "logloss", depth: int = 3, lr: float = 0.05, lam: float = 10.0,
                 min_leaf: int = 200, subsample: float = 0.7, colsample: float = 0.7, max_trees: int = 400,
                 patience: int = 50, delta: float = 1.0, seed: int = 7, nb: int = 33):
        self.loss, self.depth, self.lr, self.lam = loss, int(depth), float(lr), float(lam)
        self.min_leaf, self.subsample, self.colsample = int(min_leaf), float(subsample), float(colsample)
        self.max_trees, self.patience, self.delta, self.seed, self.nb = int(max_trees), int(patience), float(delta), seed, nb
        self.trees: list = []
        self.base = 0.0
        self.curve: list = []
        self.best = 0
        self.gain = None

    # -- loss ------------------------------------------------------------------------------------------------
    def _grad(self, F, y):
        if self.loss == "logloss":
            p = sigmoid(F)
            return p - y, np.maximum(p * (1 - p), 1e-6)
        r = y - F
        return -np.clip(r, -self.delta, self.delta), np.ones_like(F)

    def _loss(self, F, y) -> float:
        if self.loss == "logloss":
            p = np.clip(sigmoid(F), 1e-12, 1 - 1e-12)
            return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
        a = np.abs(y - F)
        d = self.delta
        return float(np.mean(np.where(a <= d, 0.5 * a * a, d * (a - 0.5 * d))))

    # -- trees -----------------------------------------------------------------------------------------------
    def _hist(self, XbT, rows, cols, g, h):
        Fc, nb = len(cols), self.nb
        idx = (XbT[np.ix_(cols, rows)].astype(np.intp) + (np.arange(Fc) * nb)[:, None]).ravel()
        HC = np.bincount(idx, minlength=Fc * nb).reshape(Fc, nb).astype(float)
        HG = np.bincount(idx, weights=np.tile(g[rows], Fc), minlength=Fc * nb).reshape(Fc, nb)
        HH = HC if self.loss == "huber" else \
            np.bincount(idx, weights=np.tile(h[rows], Fc), minlength=Fc * nb).reshape(Fc, nb)
        return HG, HH, HC

    def _grow(self, XbT, g, h, rows, cols) -> tuple:
        D, lam, ml = self.depth, self.lam, self.min_leaf
        nn = 2 ** (D + 1) - 1
        feat = np.full(nn, -1, dtype=np.int32)
        tbin = np.zeros(nn, dtype=np.int32)
        val = np.zeros(nn)
        gains = []
        node_rows = [None] * nn
        hists = [None] * nn
        node_rows[0] = rows
        if D > 0 and len(rows) >= 2 * ml:
            hists[0] = self._hist(XbT, rows, cols, g, h)
        for k in range(nn):
            r = node_rows[k]
            if r is None:
                continue
            G, H, C = float(g[r].sum()), float(h[r].sum()), len(r)
            val[k] = -G / (H + lam) * self.lr
            level = int(math.log2(k + 1))
            hist = hists[k]
            hists[k] = None
            if level >= D or hist is None or C < 2 * ml:
                continue
            HG, HH, HC = hist
            GL, HL, CL = (np.cumsum(a, axis=1)[:, :-1] for a in (HG, HH, HC))
            GR, HR, CR = G - GL, H - HL, C - CL
            gain = GL * GL / (HL + lam) + GR * GR / (HR + lam) - G * G / (H + lam)
            gain = np.where((CL >= ml) & (CR >= ml), gain, -np.inf)
            best = int(np.argmax(gain))
            fl, b = divmod(best, self.nb - 1)
            if not gain[fl, b] > 1e-12:
                continue
            f = int(cols[fl])
            go = XbT[f, r] <= b
            L, R = r[go], r[~go]
            feat[k], tbin[k] = f, b
            gains.append((f, float(gain[fl, b])))
            node_rows[2 * k + 1], node_rows[2 * k + 2] = L, R
            if level + 1 < D and max(len(L), len(R)) >= 2 * ml:
                small_left = len(L) <= len(R)
                hs = self._hist(XbT, L if small_left else R, cols, g, h)     # the bigger child = parent - smaller
                hb = tuple(p - q for p, q in zip(hist, hs))
                hl, hr = (hs, hb) if small_left else (hb, hs)
                hists[2 * k + 1] = hl if len(L) >= 2 * ml else None
                hists[2 * k + 2] = hr if len(R) >= 2 * ml else None
        return feat, tbin, val, gains

    def _apply(self, tree, XbT):
        feat, tbin, val = tree[0], tree[1], tree[2]
        n = XbT.shape[1]
        idx = np.zeros(n, dtype=np.intp)
        ar = np.arange(n)
        for _ in range(self.depth):
            f = feat[idx]
            leaf = f < 0
            go = XbT[np.where(leaf, 0, f), ar] <= tbin[idx]
            idx = np.where(leaf, idx, np.where(go, 2 * idx + 1, 2 * idx + 2))
        return val[idx]

    # -- fit / predict ---------------------------------------------------------------------------------------
    def fit(self, XbT, y, XbT_val=None, y_val=None, n_trees: int | None = None) -> "GBM":
        rng = np.random.default_rng(self.seed)
        F, n = XbT.shape
        y = np.asarray(y, dtype=float)
        if self.loss == "logloss":
            p0 = min(max(float(np.mean(y)), 1e-3), 1 - 1e-3)
            self.base = math.log(p0 / (1 - p0))
        else:
            self.base = float(np.median(y))
        Fm = np.full(n, self.base)
        val = XbT_val is not None and n_trees is None
        if val:
            Fv = np.full(XbT_val.shape[1], self.base)
            self.curve = [self._loss(Fv, y_val)]
            best_loss, self.best = self.curve[0], 0
        trees = []
        T = self.max_trees if n_trees is None else int(n_trees)
        ns, fs = max(1, int(self.subsample * n)), max(1, int(round(self.colsample * F)))
        for it in range(T):
            g, h = self._grad(Fm, y)
            rows = np.sort(rng.choice(n, ns, replace=False)) if ns < n else np.arange(n)
            cols = np.sort(rng.choice(F, fs, replace=False)) if fs < F else np.arange(F)
            tree = self._grow(XbT, g, h, rows, cols)
            trees.append(tree)
            Fm += self._apply(tree, XbT)
            if val:
                Fv += self._apply(tree, XbT_val)
                L = self._loss(Fv, y_val)
                self.curve.append(L)
                if L < best_loss - 1e-9:
                    best_loss, self.best = L, it + 1
                elif it + 1 - self.best >= self.patience:
                    break
        self.trees = trees[:self.best] if val else trees
        if not val:
            self.best = len(self.trees)
        self.gain = np.zeros(F)
        for tr in self.trees:
            for f, gn in tr[3]:
                self.gain[f] += gn
        return self

    def margin(self, XbT):
        out = np.full(XbT.shape[1], self.base)
        for tr in self.trees:
            out += self._apply(tr, XbT)
        return out

    def margin_raw(self, X, edges):
        """The same margins from unbinned features through the float thresholds (what the JSON holds)."""
        n = X.shape[0]
        out = np.full(n, self.base)
        ar = np.arange(n)
        for feat, tbin, val, _ in self.trees:
            thr = np.array([edges[f][b] if f >= 0 else np.inf for f, b in zip(feat, tbin)])
            idx = np.zeros(n, dtype=np.intp)
            for _ in range(self.depth):
                f = feat[idx]
                leaf = f < 0
                go = X[ar, np.where(leaf, 0, f)] <= thr[idx]
                idx = np.where(leaf, idx, np.where(go, 2 * idx + 1, 2 * idx + 2))
            out += val[idx]
        return out

    def export(self, edges) -> dict:
        """{"base", "depth", "trees": [{"f": [...], "t": [...], "v": [...]}]} for quant.Booster (pure Python)."""
        trees = []
        for feat, tbin, val, _ in self.trees:
            last = max([0] + [2 * k + 2 for k in range(len(feat)) if feat[k] >= 0])       # last reachable node
            trees.append({"f": [int(f) for f in feat[:last + 1]],
                          "t": [float(edges[f][b]) if f >= 0 else None for f, b in zip(feat[:last + 1], tbin[:last + 1])],
                          "v": [float(v) for v in val[:last + 1]]})
        return {"base": float(self.base), "depth": self.depth, "loss": self.loss, "trees": trees}


# ====================================================================== validation
def purged_folds(t, k: int, horizon_s: int, embargo_s: int) -> list:
    """Contiguous K folds over time-sorted samples. Training part of each: samples whose label window
    [t, t + horizon] ends before the validation fold starts, or that start after its last label ended plus
    the embargo (Lopez de Prado's purged K-fold with embargo)."""
    n = len(t)
    cuts = np.linspace(0, n, k + 1).astype(int)
    out = []
    for f in range(k):
        a, z = cuts[f], cuts[f + 1]
        if z - a < 10:
            continue
        v0, v1 = t[a], t[z - 1] + horizon_s
        tr = (t + horizon_s < v0) | (t > v1 + embargo_s)
        va = np.zeros(n, dtype=bool)
        va[a:z] = True
        out.append((tr, va))
    return out


def walk_forward(t, k: int, horizon_s: int, embargo_s: int, min_train: float = 0.4) -> list:
    """Expanding-window walk-forward folds: train on everything before the fold (minus purge + embargo)."""
    n = len(t)
    start = int(n * min_train)
    cuts = np.linspace(start, n, k + 1).astype(int)
    out = []
    for f in range(k):
        a, z = cuts[f], cuts[f + 1]
        if z - a < 10:
            continue
        tr = t + horizon_s < t[a] - embargo_s
        va = np.zeros(n, dtype=bool)
        va[a:z] = True
        out.append((tr, va))
    return out


GRID = (
    {"depth": 2, "min_leaf_frac": 0.01},
    {"depth": 3, "min_leaf_frac": 0.01},
    {"depth": 3, "min_leaf_frac": 0.03},
)
BASE_HP = {"lr": 0.05, "lam": 10.0, "subsample": 0.7, "colsample": 0.7}


def cv_select(XbT, y, folds, loss: str, grid, max_trees: int, patience: int, seed: int, delta: float = 1.0,
              log=print, nb: int = 33, hp: dict | None = None) -> dict:
    """Pick the config with the lowest mean early-stopped validation loss. Returns the chosen config, its trees
    per fold, every config's score and the last fold's model (for Platt)."""
    hp = hp or BASE_HP
    res = []
    for cfg in grid:
        losses, iters, base_l, last = [], [], [], None
        oof = np.full(XbT.shape[1], np.nan)
        for tr, va in folds:
            ml = max(5, int(cfg["min_leaf_frac"] * tr.sum() * hp["subsample"]))
            m = GBM(loss=loss, depth=cfg["depth"], min_leaf=ml, max_trees=max_trees, patience=patience,
                    seed=seed, delta=delta, nb=nb, **hp)
            m.fit(XbT[:, tr], y[tr], XbT[:, va], y[va])
            losses.append(min(m.curve))
            base_l.append(m.curve[0])
            iters.append(m.best)
            last = (m, va)
            oof[va] = m.margin(XbT[:, va])
        r = {"cfg": cfg, "loss": float(np.mean(losses)), "base_loss": float(np.mean(base_l)), "iters": iters,
             "trees": int(np.median(iters)), "last": last, "oof": oof}
        res.append(r)
        if log:
            log(f"    depth {cfg['depth']}, leaf {cfg['min_leaf_frac']:.0%}: CV {loss} {r['loss']:.5f} vs "
                f"{r['base_loss']:.5f} with no trees; best trees per fold {iters}")
    best = min(res, key=lambda r: r["loss"])
    best["all"] = [{"cfg": r["cfg"], "loss": r["loss"], "base_loss": r["base_loss"], "iters": r["iters"]} for r in res]
    return best


def platt(margins, y, lam: float = 1.0) -> tuple:
    """Platt scaling p = sigmoid(a * margin + b) by Newton's method, with a small ridge toward a = b = 0
    (no skill), which also settles a model with no trees (constant margins)."""
    m, y = np.asarray(margins, float), np.asarray(y, float)
    a = b = 0.0
    for _ in range(50):
        p = sigmoid(a * m + b)
        w = p * (1 - p)
        ga, gb = float(np.sum((p - y) * m)) + lam * a, float(np.sum(p - y)) + lam * b
        haa, hab, hbb = float(np.sum(w * m * m)) + lam, float(np.sum(w * m)), float(np.sum(w)) + lam
        det = haa * hbb - hab * hab
        if det <= 1e-12:
            break
        da, db = (hbb * ga - hab * gb) / det, (haa * gb - hab * ga) / det
        a, b = a - da, b - db
        if abs(da) < 1e-10 and abs(db) < 1e-10:
            break
    return float(a), float(b)


def calibrated(margins, ab) -> "np.ndarray":
    return np.clip(sigmoid(ab[0] * margins + ab[1]), P_LO, P_HI)


# ====================================================================== metrics
def auc(p, y) -> float | None:
    y = np.asarray(y) > 0.5
    n1, n0 = int(y.sum()), int((~y).sum())
    if not n1 or not n0:
        return None
    _, inv, cnt = np.unique(p, return_inverse=True, return_counts=True)
    ranks = (np.cumsum(cnt) - (cnt - 1) / 2.0)[inv]
    return float((ranks[y].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def binary_metrics(p, y, n_eff: float, day=None, clim: float | None = None, seed: int = 11) -> dict:
    """Held-out scores of probabilities p for outcomes y (1 = up)."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    n = len(y)
    hit = ((p > 0.5) == (y > 0.5)).astype(float)
    acc = float(hit.mean())
    ne = max(1.0, float(n_eff))
    se = math.sqrt(max(acc * (1 - acc), 1e-12) / ne)
    brier = float(np.mean((p - y) ** 2))
    eps = 1e-12
    ll = float(-np.mean(y * np.log(np.clip(p, eps, 1)) + (1 - y) * np.log(np.clip(1 - p, eps, 1))))
    out = {"n": n, "n_eff": round(ne, 1), "accuracy": round(acc, 4), "acc_low95": round(acc - 1.96 * se, 4),
           "acc_high95": round(acc + 1.96 * se, 4), "coin_band": round(1.96 * math.sqrt(0.25 / ne), 4),
           "up_share": round(float(y.mean()), 4), "brier": round(brier, 5),
           "brier_skill": round(1 - brier / 0.25, 5), "logloss": round(ll, 5), "logloss_coin": round(math.log(2), 5),
           "auc": None}
    a = auc(p, y)
    out["auc"] = None if a is None else round(a, 4)
    if clim is not None:
        bc = float(np.mean((clim - y) ** 2))
        out["brier_skill_vs_drift"] = round(1 - brier / bc, 5) if bc > 0 else None
        out["drift_accuracy"] = round(float(np.mean((clim > 0.5) == (y > 0.5))), 4)
    if day is not None and n > 20:
        days, inv = np.unique(np.asarray(day), return_inverse=True)
        bs_d = np.bincount(inv, weights=(p - y) ** 2)
        n_d = np.bincount(inv).astype(float)
        rng = np.random.default_rng(seed)
        pick = rng.integers(0, len(days), size=(2000, len(days)))
        sk = 1 - bs_d[pick].sum(1) / (0.25 * n_d[pick].sum(1))
        out["brier_skill_ci"] = [round(float(np.percentile(sk, 2.5)), 5), round(float(np.percentile(sk, 97.5)), 5)]
    # calibration by decile of p
    qs = np.unique(np.quantile(p, np.linspace(0, 1, 11)))
    rows = []
    if len(qs) > 1:
        b = np.clip(np.searchsorted(qs, p, side="right") - 1, 0, len(qs) - 2)
        for k in range(len(qs) - 1):
            m = b == k
            if m.any():
                rows.append({"p_lo": round(float(qs[k]), 4), "p_hi": round(float(qs[k + 1]), 4), "n": int(m.sum()),
                             "mean_p": round(float(p[m].mean()), 4), "up_share": round(float(y[m].mean()), 4)})
    else:
        rows.append({"p_lo": round(float(p[0]), 4), "p_hi": round(float(p[0]), 4), "n": n,
                     "mean_p": round(float(p.mean()), 4), "up_share": round(float(y.mean()), 4)})
    out["calibration"] = rows
    return out


def beats_coin(m: dict) -> bool:
    return bool(m["brier_skill"] > 0 and m["acc_low95"] > 0.5)


def pnl_table(p, t, t_end, px, px_end, spread: float = SPREAD, xs=(0.02, 0.04, 0.06, 0.08, 0.10)) -> list:
    """Trade when |p - 0.5| > x: direction sign(p - 0.5), in at the close now, out 30 minutes later, one trade
    at a time, $ per 0.01 lot (1 oz) after the spread."""
    order = np.argsort(t, kind="stable")
    out = []
    for x in xs:
        busy, rs = -1, []
        for i in order:
            if t[i] < busy or abs(p[i] - 0.5) <= x:
                continue
            d = 1.0 if p[i] > 0.5 else -1.0
            rs.append(d * (px_end[i] - px[i]) - spread)
            busy = t_end[i]
        if rs:
            r = np.array(rs)
            sd = float(r.std(ddof=1)) if len(r) > 1 else 0.0
            out.append({"x": x, "trades": len(rs), "win": round(float((r > 0).mean()), 4),
                        "total": round(float(r.sum()), 2), "mean": round(float(r.mean()), 3),
                        "t_stat": round(float(r.mean() / (sd / math.sqrt(len(r)))), 2) if sd > 0 else None})
        else:
            out.append({"x": x, "trades": 0, "win": None, "total": 0.0, "mean": None, "t_stat": None})
    return out


def group_table(keys, p, y) -> list:
    out = []
    keys = np.asarray(keys)
    for k in sorted(set(keys.tolist())):
        m = keys == k
        pk, yk = p[m], y[m]
        out.append({"group": str(k), "n": int(m.sum()), "accuracy": round(float(np.mean((pk > 0.5) == (yk > 0.5))), 4),
                    "brier_skill": round(1 - float(np.mean((pk - yk) ** 2)) / 0.25, 4),
                    "up_share": round(float(yk.mean()), 4)})
    return out


def corr(a, b) -> float | None:
    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
        return None
    return round(float(np.corrcoef(a, b)[0, 1]), 4)


def spearman(a, b) -> float | None:
    if len(a) < 3:
        return None
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    return corr(ra, rb)


# ====================================================================== training
def train(ds: dict, split: int, folds: int = 4, embargo_min: int = 1440, grid=GRID, max_trees: int = 400,
          patience: int = 50, seed: int = 7, max_bins: int = 32, log=print) -> tuple:
    """Fit on the samples before `split` (UTC seconds; purged), judge once on the ones after.
    Returns (model JSON dict, report dict, extras for checks)."""
    need_numpy()
    X, y, mv, t = ds["X"], ds["up"], ds["move"], ds["t"]
    H, emb = ds["horizon_s"], embargo_min * 60
    tr_all = t + H < split - emb
    te = t >= split
    if tr_all.sum() < 200 or te.sum() < 50:
        raise SystemExit(f"Too few samples: {int(tr_all.sum())} to train before the split, {int(te.sum())} after it. "
                         "Move --split or load more history.")
    Xtr, ytr, mtr, ttr = X[tr_all], y[tr_all], mv[tr_all], t[tr_all]
    edges = make_edges(Xtr, max_bins)
    nb = max_bins + 1
    XbT_tr, XbT_te = apply_bins(Xtr, edges), apply_bins(X[te], edges)
    fl = purged_folds(ttr, folds, H, emb)
    if log:
        log(f"  {len(ttr)} training samples ({utc_day(ttr[0])} to {utc_day(ttr[-1])}), {int(te.sum())} held out "
            f"({utc_day(t[te][0])} to {utc_day(t[te][-1])}); purged {folds}-fold CV, embargo {embargo_min} min")
        log("  direction (log-loss):")
    sel = cv_select(XbT_tr, ytr, fl, "logloss", grid, max_trees, patience, seed, log=log, nb=nb)
    cfg = sel["cfg"]
    ml = max(5, int(cfg["min_leaf_frac"] * len(ytr) * BASE_HP["subsample"]))
    # Platt on the out-of-fold margins of every purged fold (each from a model that never saw them). Fitting it
    # on the last fold alone, as first planned, let that fold's up-share leak into the intercept.
    oof = sel["oof"]
    have = ~np.isnan(oof)
    ab = platt(oof[have], ytr[have])
    cls = GBM(loss="logloss", depth=cfg["depth"], min_leaf=ml, seed=seed, nb=nb, **BASE_HP).fit(
        XbT_tr, ytr, n_trees=sel["trees"])
    delta = float(np.quantile(np.abs(mtr - np.median(mtr)), 0.9)) or 1.0
    if log:
        log(f"  move (Huber, delta {delta:.2f} units), depth {cfg['depth']}:")
    rsel = cv_select(XbT_tr, mtr, fl, "huber", [cfg], max_trees, patience, seed, delta=delta, log=log, nb=nb)
    reg = GBM(loss="huber", depth=cfg["depth"], min_leaf=ml, seed=seed, delta=delta, nb=nb, **BASE_HP).fit(
        XbT_tr, mtr, n_trees=rsel["trees"])
    # ---- the held-out months, once
    mte = cls.margin(XbT_te)
    p = calibrated(mte, ab)
    yte = y[te]
    clim = float(np.clip(ytr.mean(), P_LO, P_HI))
    n_eff = len(yte) * min(1.0, ds["step"] * 60 / H)
    met = binary_metrics(p, yte, n_eff, day=t[te] // DAY, clim=clim)
    rte = reg.margin(XbT_te)
    met["reg_corr"] = corr(rte, mv[te])
    met["reg_spearman"] = spearman(rte, mv[te])
    met["reg_hit"] = round(float(np.mean(np.sign(rte) == np.sign(mv[te]))), 4)
    met["pnl"] = pnl_table(p, t[te], t[te] + H, ds["price"][te], ds["price_end"][te])
    months = np.array([utc_day(x)[:7] for x in t[te]])
    met["months"] = group_table(months, p, yte)
    met["sessions"] = group_table(ds["session"][te], p, yte)
    feats = ds["features"]
    gsum = cls.gain.sum() or 1.0
    met["importance"] = [{"feature": feats[i], "gain": round(float(cls.gain[i] / gsum), 4)}
                         for i in np.argsort(-cls.gain)[:20] if cls.gain[i] > 0]
    rg = reg.gain.sum() or 1.0
    met["importance_move"] = [{"feature": feats[i], "gain": round(float(reg.gain[i] / rg), 4)}
                              for i in np.argsort(-reg.gain)[:10] if reg.gain[i] > 0]
    met["cv"] = {"direction": sel["all"], "move": rsel["all"], "chosen": cfg, "trees": sel["trees"],
                 "move_trees": rsel["trees"], "platt": [round(ab[0], 5), round(ab[1], 5)]}
    met["n_test"] = int(len(yte))
    met["n_train"] = int(len(ytr))
    ok = beats_coin(met)
    tp = f"{utc_day(t[te][0])} to {utc_day(t[te][-1])}"
    trp = f"{utc_day(ttr[0])} to {utc_day(ttr[-1])}"
    y_rms = float(np.sqrt(np.mean(mtr ** 2)))
    model = {
        "version": VERSION, "model": MODEL_NAME, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "horizon": qf.HORIZON, "features": feats, "unit": "M1 ATR(14) x sqrt(30)", "y_rms": round(y_rms, 6),
        "inputs": {"silver": bool(ds["silver"]), "dxy": bool(ds["dxy"]), "kronos": False},
        "hyper": {**cfg, **BASE_HP, "min_leaf": ml, "trees": sel["trees"], "move_trees": rsel["trees"],
                  "huber_delta": round(delta, 6), "max_bins": max_bins, "folds": folds, "embargo_min": embargo_min,
                  "step": ds["step"]},
        "cls": cls.export(edges), "reg": reg.export(edges),
        "platt": {"a": float(ab[0]), "b": float(ab[1])},
        "bins": {f: [float(v) for v in e] for f, e in zip(feats, edges)},
        "trained_period": trp, "test_period": tp, "metrics": met, "beats_coin": ok,
        "note": ("Held-out months judged once. Kronos inputs were 0 in training (not replayable), so the trees "
                 "ignore them; blend with Kronos via quant.blend_with_kronos. beats_coin = held-out Brier skill > 0 "
                 "and the accuracy's lower 95% bound (n_eff = non-overlapping 30-minute windows) above 50%."),
    }
    extras = {"cls": cls, "reg": reg, "edges": edges, "test_mask": te, "p_test": p, "platt": ab}
    return model, met, extras


# ====================================================================== report
def verdict(met: dict, ok: bool, what: str = "the 30-minute quant model", unit: str = "independent 30-minute windows",
            period: str = "held-out months",
            no_edge_tail: str = " The page shows it as a coin and gives it ~0 weight next to Kronos.") -> str:
    acc, lo, hi, band = met["accuracy"], met["acc_low95"], met["acc_high95"], met["coin_band"]
    bss = met["brier_skill"]
    ci = met.get("brier_skill_ci")
    cis = f" (95% range {ci[0]:+.4f} to {ci[1]:+.4f})" if ci else ""
    if ok:
        drift = met.get("brier_skill_vs_drift")
        tail = "" if drift is None or drift > 0 else (
            " -- but no better than always forecasting the training period's up-share (the trend), so the inputs "
            "add nothing beyond the drift")
        return (f"Verdict: {what} beat a coin on the {period}: right {acc:.1%} (95% range {lo:.1%}-{hi:.1%}) "
                f"over {met['n_eff']:.0f} {unit}, Brier skill {bss:+.4f}{cis}{tail}. Small and "
                "measured on one stretch of history: keep watching its live record.")
    return (f"Verdict: no edge. On the {period} {what} was right {acc:.1%} of the time (a coin's 95% range "
            f"is {0.5 - band:.1%}-{0.5 + band:.1%} for {met['n_eff']:.0f} {unit}) and its Brier skill is "
            f"{bss:+.4f}{cis}.{no_edge_tail}")


def print_report(model: dict, met: dict, log=print) -> None:
    log(f"\nHeld-out months {model['test_period']} (trained {model['trained_period']}), {met['n_test']} samples, "
        f"{met['n_eff']:.0f} independent 30-minute windows")
    log(f"  direction right   {met['accuracy']:.2%}  (95% range {met['acc_low95']:.2%}-{met['acc_high95']:.2%}; "
        f"coin 50% +/- {met['coin_band']:.2%}; up share {met['up_share']:.1%}; always-the-trend "
        f"{met.get('drift_accuracy', float('nan')):.1%})")
    ci = met.get("brier_skill_ci")
    log(f"  Brier skill       {met['brier_skill']:+.5f} vs always 50%" + (f"  (95% by days {ci[0]:+.5f} to {ci[1]:+.5f})" if ci else "")
        + (f";  {met['brier_skill_vs_drift']:+.5f} vs the training up-share" if met.get("brier_skill_vs_drift") is not None else ""))
    log(f"  log-loss          {met['logloss']:.5f} vs {met['logloss_coin']:.5f} for a coin;  AUC {met['auc']}")
    log(f"  move regression   correlation {met['reg_corr']}, rank {met['reg_spearman']}, sign right {met['reg_hit']:.1%}")
    log("  calibration (deciles of the forecast):")
    for r in met["calibration"]:
        log(f"    p {r['p_lo']:.3f}-{r['p_hi']:.3f}  n {r['n']:>6}  forecast {r['mean_p']:.3f}  happened {r['up_share']:.3f}")
    log(f"  trade when |p - 0.5| > x (one at a time, 30 min, ${SPREAD} spread per 0.01 lot):")
    for r in met["pnl"]:
        if r["trades"]:
            log(f"    x {r['x']:.2f}: {r['trades']:>5} trades, win {r['win']:.1%}, total ${r['total']:+.2f}, "
                f"mean ${r['mean']:+.3f}, t {r['t_stat']}")
        else:
            log(f"    x {r['x']:.2f}: no trades")
    log("  by month:")
    for r in met["months"]:
        log(f"    {r['group']}  n {r['n']:>6}  right {r['accuracy']:.1%}  Brier skill {r['brier_skill']:+.4f}  up {r['up_share']:.1%}")
    log("  by part of the New York day:")
    for r in met["sessions"]:
        log(f"    {r['group']:<22} n {r['n']:>6}  right {r['accuracy']:.1%}  Brier skill {r['brier_skill']:+.4f}")
    if met["importance"]:
        log("  what the direction trees use (share of split gain): " +
            ", ".join(f"{r['feature']} {r['gain']:.0%}" for r in met["importance"][:12]))
    else:
        log("  the direction model kept no trees (early stopping found nothing that beat the base rate)")


def write_model(model: dict, out: str) -> Path:
    p = Path(out).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(model, separators=(",", ":")))
    tmp.replace(p)
    return p


def check_roundtrip(model: dict, extras: dict, ds: dict, k: int = 200) -> float:
    """Largest difference between the numpy margins and quant.Booster's (pure Python, from the JSON)."""
    from quant import Booster
    te = np.where(extras["test_mask"])[0][:k]
    X = ds["X"][te]
    b = Booster(json.loads(json.dumps(model["cls"])))
    py = np.array([b.margin(list(x)) for x in X])
    nump = extras["cls"].margin_raw(X, extras["edges"])
    return float(np.max(np.abs(py - nump))) if len(te) else 0.0


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--sessions" in argv:
        argv.remove("--sessions")
        import quant_sessions_train
        return quant_sessions_train.main(argv)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="?", help="gold M1 CSV from fetch_history.py (UTC)")
    ap.add_argument("--silver", help="silver M1 CSV (XAGUSD), same clock")
    ap.add_argument("--dxy", help="dollar index M1 CSV (DOLLARIDXUSD)")
    ap.add_argument("--mt5", nargs="?", const="auto", help="take M1 history (and silver) from the running Gold Desk "
                                                           "MT5 bridge instead of a CSV (optional: its folder)")
    ap.add_argument("--mt5-bars", type=int, default=200000, help="how many M1 candles to ask MT5 for")
    ap.add_argument("--utc-offset", type=float, help="MT5 server clock offset in hours, if not New York + 7")
    ap.add_argument("--split", help="YYYY-MM-DD: train before, judge from this day on (default: last 25%%)")
    ap.add_argument("--step", type=int, default=5, help="a sample every N M1 candles")
    ap.add_argument("--embargo", type=int, default=1440, help="minutes dropped around held-out / CV folds")
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--max-trees", type=int, default=400)
    ap.add_argument("--days", type=int, default=0, help="only the last N days of history (0: all)")
    ap.add_argument("--quick", action="store_true", help="one configuration, fewer trees (a fast first look)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(MODEL_FILE))
    a = ap.parse_args(argv)
    need_numpy()
    t0 = time.time()
    if a.mt5:
        m1, sv, what = load_mt5("M1", a.mt5_bars, None if a.mt5 == "auto" else a.mt5, a.utc_offset)
        if a.silver:
            sv = as_m1(load_csv(a.silver), a.silver)
    else:
        if not a.csv:
            ap.error("give a gold M1 CSV or --mt5")
        m1 = as_m1(load_csv(a.csv), a.csv)
        sv = as_m1(load_csv(a.silver), a.silver) if a.silver else None
        what = f"{a.csv}: {len(m1)} M1 candles"
    dx = as_m1(load_csv(a.dxy), a.dxy) if a.dxy else None
    if a.days:
        keep = lambda b: None if b is None else qf.sub(b, bisect_right(b.t, m1.t[-1] - a.days * DAY), len(b))
        m1, sv, dx = keep(m1), keep(sv), keep(dx)
    print(f"{what}, {utc_day(m1.t[0])} to {utc_day(m1.t[-1])}" + (f"; silver {len(sv)}" if sv else "") +
          (f"; dollar index {len(dx)}" if dx else ""))
    ds = build_dataset(m1, sv, dx, step=a.step)
    split = parse_day(a.split) if a.split else int(np.quantile(ds["t"], 0.75)) // DAY * DAY
    grid, mt = (GRID[1:2], min(a.max_trees, 150)) if a.quick else (GRID, a.max_trees)
    model, met, extras = train(ds, split, folds=a.folds, embargo_min=a.embargo, grid=grid, max_trees=mt,
                               seed=a.seed)
    model["data"] = what
    print_report(model, met)
    err = check_roundtrip(model, extras, ds)
    print(f"\n  JSON trees reproduce the numpy margins to {err:.1e}")
    p = write_model(model, a.out)
    print(f"Wrote {p} ({p.stat().st_size // 1024} KB) in {time.time() - t0:.0f} s. The live page reads it "
          "(quant.QuantModel).")
    print(verdict(met, model["beats_coin"]))


if __name__ == "__main__":
    main()
