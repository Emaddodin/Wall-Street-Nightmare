"""
cand/ml_meta.py - X3: a machine-learned ENTRY FILTER (sklearn HistGradientBoostingClassifier) on causal features
computed at every M5 close.

Hypothesis (OPINION): generic causal price / range / zone / time / cost features at an M5 close carry enough
information to rank bars by the probability that a symmetric +-a*A5 bracket (on MID prices) reaches its target
before its stop within T minutes, well enough that the top-ranked bars beat LiteFinance costs.

Pipeline (everything happens inside orders(); the fitted model only ever sees data before 2026-01-01, so VALID
(2026-01..05) is out-of-sample):

 1. Decision points: every completed M5 bar (data.resample_causal). The decision is taken at the close of the M1 bar
    at which the M5 bar becomes known; the order has t = ts[i] + 60 s. Entry gates (SYNTHESIS section 5):
    no entries 20:30-23:30 UTC, Friday >= 19:00 UTC, Saturday/Sunday, 16:15-18:00 ET; no entries within +-30 min of a
    HIGH calendar row (the < $21 equity rule, G4). Positions are flattened at 16:45 ET.
 2. Features (causal, scale-free; ~36 columns, see FEATS): M5 returns over 1/3/6/12/48 bars in A5 units; last M5 bar
    body/wicks/range; ATR ratios (M1 ATR / A5, A5_14 / A5_96, A5_14 / A5_288, A5 / its prior-20-day median);
    position in the trading day's range, in the (completed) Asia range 00:00-06:59 UTC and in the prior trading day's
    range; distance to prior-day high/low in A5; distance to the nearest multi-touch zone above / below
    (features.zone_timeline, L=240, k=3, w=0.5, K=3) in A5 and its touch count; inside-zone flag; London / New York
    minute of day (sin/cos); day of week; minutes to the next / since the last HIGH calendar event; sweep flags
    (a high above PDH / Asia high in the last 15 M1 bars and a close back below it, and the mirror); signed M5 FVG
    size, count of signed FVGs over the last 3 M5 bars; signed M5 engulfing; M1 spread / its prior-20-day median of
    the same UTC hour; M1 spread / A5.
 3. Labels on MID prices: from the decision close e = c[i] and w = a*A5, walk M1 bars i+1.. for at most T minutes
    (and never past 16:45 ET). Long win = high >= e+w before low <= e-w (both in one bar = loss); short is the mirror.
    Timeouts count as "not a win".
 4. Model: ONE classifier for both sides on the stacked data (long rows with features x, short rows with MIRRORED
    features m(x): signed features negated, range positions p -> 1-p, above/below pairs swapped). P_long = f(x),
    P_short = f(m(x)). The rules for longs and shorts are therefore identical and mirrored, and the model cannot learn
    an unconditional drift. (mode='sep', two separate side models, is a directional comparison arm only.)
      FIT       decision points with t < 2025-10-01 whose label horizon ends before 2025-10-01.
      EARLY-STOP the boosting iteration (<= 300) with the lowest log-loss on the 2025-Q4 slice (2025-10-01..12-31,
                horizon ends before 2026-01-01), which is still TRAIN.
      CALIBRATE Platt scaling of the raw probability on the same Q4 slice.
      THRESHOLD thr = the (1-q) quantile of max(P_long, P_short) over the Q4 decision points that pass the gates
                (and the flip stop filter when flip=1). q is a grid parameter chosen on Q4 (sweep "train" = Q4).
 5. Orders: side = argmax(P_long, P_short); trade when P >= thr; market order, sl_dist = tp_dist = a*A5 (from the
    fill), tmax = T, flat 16:45 ET. flip=1 skips bars whose stop a*A5 is outside [1.2, 4.0] $/oz.

emit: 'oos' (default) emits only the Q4-2025 slice (out-of-sample for the fit; the sweep's TRAIN split is therefore
      Q4 only) and VALID. 'all' also emits the in-sample Jan-Sep 2025 bars (tag 'insample': never judge them).
Nothing at t >= 2026-06-01 is computed: m1 is truncated before any feature or label is built.

Set the environment variable ML_META_CACHE to a directory to cache fitted-model predictions (npz, ~2 MB each).
"""
import hashlib
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from data import _ms, atr, load_calendar, resample_causal  # noqa: E402
import features as F  # noqa: E402

FIT_END = _ms("2025-10-01")
CAL_END = _ms("2026-01-01")
TEST_MS = _ms("2026-06-01")
FLIP = (1.2, 4.0)

GRID = {
    "a": [0.5, 1.0, 1.5],
    "T": [30, 60, 120],
    "q": [0.02, 0.05, 0.10, 0.20],
    "flip": [0, 1],
}
DEFAULTS = {"a": 1.0, "T": 60, "q": 0.05, "flip": 0, "mode": "mirror", "emit": "oos", "news": 30}

HP = dict(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=400, l2_regularization=1.0,
          max_bins=63, early_stopping=False, random_state=0)

# feature spec: name -> mirror rule ('neg' negate, 'flip' p -> 1-p, 'swap:<other>' exchange, None neutral)
FEATS = {
    "r1": "neg", "r3": "neg", "r6": "neg", "r12": "neg", "r48": "neg",
    "body5": "neg", "wick_up5": "swap:wick_dn5", "wick_dn5": "swap:wick_up5", "rng5": None,
    "atr1_5": None, "atr_s96": None, "atr_s288": None, "atr_reg20d": None,
    "pos_day": "flip", "day_rng": None,
    "pos_asia": "flip", "asia_rng": None,
    "pos_pd": "flip", "d_pdh": "swap:d_pdl", "d_pdl": "swap:d_pdh",
    "z_up": "swap:z_dn", "z_dn": "swap:z_up", "z_up_k": "swap:z_dn_k", "z_dn_k": "swap:z_up_k", "in_zone": None,
    "lon_s": None, "lon_c": None, "ny_s": None, "ny_c": None, "dow": None,
    "ev_next": None, "ev_prev": None,
    "sweep_hi": "swap:sweep_lo", "sweep_lo": "swap:sweep_hi",
    "fvg5": "neg", "fvg5_3": "neg", "eng5": "neg",
    "spr_rel": None, "spr_atr": None,
}
FNAMES = list(FEATS)

_BASE: dict = {}
_PRED: dict = {}


# ------------------------------------------------------------------------------------------------ features
def _base(m1: pd.DataFrame) -> dict:
    """Decision points, features and gates. m1 is truncated at 2026-06-01 first (holdout never touched)."""
    key = (len(m1), int(m1["ts"].values[0]), int(m1["ts"].values[-1]))
    if key in _BASE:
        return _BASE[key]
    _BASE.clear()
    m1 = m1[m1["ts"].values < TEST_MS].reset_index(drop=True)
    ts = m1["ts"].values.astype(np.int64)
    o, h, l, c = (m1[x].values.astype(np.float64) for x in "ohlc")
    n1 = len(m1)
    core = {"ts": ts, "h": h, "l": l, "c": c, "n1": n1}
    cp = _cache_dir()
    fp = None
    if cp is not None:
        tag = hashlib.md5(json.dumps({"f": FNAMES, "n1": n1, "t0": int(ts[0]), "t1": int(ts[-1]), "v": 3}
                                     ).encode()).hexdigest()[:10]
        fp = cp / f"ml_meta_feat_{tag}.npz"
        if fp.exists():
            z = np.load(fp)
            out = {**core, **{k: z[k] for k in z.files}}
            _BASE[key] = out
            return out
    A1 = atr(m1)
    htf, known = resample_causal(m1, 5)
    ki = np.searchsorted(known, np.arange(len(htf)), side="left")   # M1 index at whose close M5 bar k is known
    K = np.flatnonzero(ki < n1)
    I = ki[K]
    keep = np.r_[I[1:] != I[:-1], True]                              # one decision per M1 bar (last M5 bar known)
    K, I = K[keep], I[keep]
    o5, h5, l5, c5 = (htf[x].values for x in "ohlc")
    A5all = atr(htf, 14)
    A5 = A5all[K]
    cn = c[I]
    f = {}
    for nb in (1, 3, 6, 12, 48):
        prev = np.where(K - nb >= 0, c5[np.maximum(K - nb, 0)], np.nan)
        f[f"r{nb}"] = (cn - prev) / A5
    f["body5"] = (c5[K] - o5[K]) / A5
    f["wick_up5"] = (h5[K] - np.maximum(o5[K], c5[K])) / A5
    f["wick_dn5"] = (np.minimum(o5[K], c5[K]) - l5[K]) / A5
    f["rng5"] = (h5[K] - l5[K]) / A5
    f["atr1_5"] = A1[I] / A5
    f["atr_s96"] = A5 / atr(htf, 96)[K]
    f["atr_s288"] = A5 / atr(htf, 288)[K]
    med20 = pd.Series(A5all).shift(1).rolling(5760, min_periods=1000).median().values
    f["atr_reg20d"] = A5 / med20[K]
    tday = m1["tday"].values
    dhi, dlo = F.running_day_extremes(tday, h, l)
    rngd = dhi[I] - dlo[I]
    f["pos_day"] = np.where(rngd > 0, (cn - dlo[I]) / np.where(rngd > 0, rngd, 1), np.nan)
    f["day_rng"] = rngd / A5
    # Asia range 00:00-06:59 UTC keyed by the UTC date (NOT tday: see the report, window_range + tday leaks)
    mod = m1["mod"].values.astype(np.int64)
    uday = ts // 86_400_000
    ahi, alo = F.window_range(mod, uday, h, l, 0, 420)
    ar = ahi[I] - alo[I]
    f["pos_asia"] = np.clip((cn - alo[I]) / np.where(ar > 0, ar, np.nan), -3, 4)
    f["asia_rng"] = ar / A5
    pdh, pdl = F.prev_day_hl(tday, h, l)
    pr = pdh[I] - pdl[I]
    f["pos_pd"] = np.clip((cn - pdl[I]) / np.where(pr > 0, pr, np.nan), -3, 4)
    f["d_pdh"] = np.clip((pdh[I] - cn) / A5, -20, 20)
    f["d_pdl"] = np.clip((cn - pdl[I]) / A5, -20, 20)
    # multi-touch zones known at the close of bar i
    chg, zsets = F.zone_timeline(h, l, A1, L=240, k=3, w=0.5, K=3)
    jz = np.searchsorted(chg, I, side="right") - 1
    zu, zd, zuk, zdk, inz = (np.full(len(I), np.nan) for _ in range(5))
    for r, (i, j) in enumerate(zip(I, jz)):
        if j < 0:
            continue
        z = zsets[j]
        if not len(z):
            inz[r] = 0
            continue
        x = cn[r]
        up = z[:, 0] > x
        dn = z[:, 1] < x
        inz[r] = float(np.any(~up & ~dn))
        if up.any():
            m = np.argmin(np.where(up, z[:, 0], np.inf))
            zu[r], zuk[r] = (z[m, 0] - x) / A5[r], z[m, 2]
        if dn.any():
            m = np.argmax(np.where(dn, z[:, 1], -np.inf))
            zd[r], zdk[r] = (x - z[m, 1]) / A5[r], z[m, 2]
    f["z_up"], f["z_dn"] = np.clip(zu, 0, 20), np.clip(zd, 0, 20)
    f["z_up_k"], f["z_dn_k"], f["in_zone"] = zuk, zdk, inz
    lon = m1["lon_mod"].values[I] * (2 * np.pi / 1440)
    ny_mod = m1["ny_mod"].values.astype(np.int64)
    nym = ny_mod[I] * (2 * np.pi / 1440)
    f["lon_s"], f["lon_c"], f["ny_s"], f["ny_c"] = np.sin(lon), np.cos(lon), np.sin(nym), np.cos(nym)
    dow = m1["dow"].values.astype(np.int64)
    f["dow"] = dow[I].astype(float)
    cal = load_calendar()
    ev = np.sort(cal.loc[cal["impact"] == "HIGH", "ts"].values.astype(np.int64))
    td = ts[I] + 60_000
    jn = np.searchsorted(ev, td, side="left")
    nxt = np.where(jn < len(ev), ev[np.minimum(jn, len(ev) - 1)], td + 10 ** 12)
    prv = np.where(jn > 0, ev[np.maximum(jn - 1, 0)], td - 10 ** 12)
    f["ev_next"] = np.minimum((nxt - td) / 60_000, 1440)
    f["ev_prev"] = np.minimum((td - prv) / 60_000, 1440)
    # sweep of PDH / Asia high in the last 15 M1 bars with a close back through it (and the mirror)
    mx15 = pd.Series(h).rolling(15, min_periods=1).max().values[I]
    mn15 = pd.Series(l).rolling(15, min_periods=1).min().values[I]
    sh = np.zeros(len(I))
    sl_ = np.zeros(len(I))
    for Hlev, Llev in ((pdh[I], pdl[I]), (ahi[I], alo[I])):
        sh = np.maximum(sh, ((mx15 > Hlev) & (cn < Hlev)).astype(float))
        sl_ = np.maximum(sl_, ((mn15 < Llev) & (cn > Llev)).astype(float))
    f["sweep_hi"], f["sweep_lo"] = sh, sl_
    bull, b_lo, b_hi, bear, r_lo, r_hi = F.fvg(h5, l5)
    fsz = np.where(bull, (b_hi - b_lo), 0.0) - np.where(bear, (r_hi - r_lo), 0.0)
    fsz = np.nan_to_num(fsz)
    f["fvg5"] = fsz[K] / A5
    sg = np.sign(fsz)
    f["fvg5_3"] = pd.Series(sg).rolling(3, min_periods=1).sum().values[K]
    eb, er = F.engulf(o5, c5)
    f["eng5"] = (eb.astype(float) - er.astype(float))[K]
    spr = m1["spr"].values.astype(np.float64)
    hr = m1["hour"].values.astype(np.int64)
    tab = pd.DataFrame({"d": uday, "h": hr, "s": spr}).groupby(["h", "d"])["s"].median()
    ref = tab.groupby(level=0).transform(lambda s: s.shift(1).rolling(20, min_periods=5).median())
    refv = ref.reindex(pd.MultiIndex.from_arrays([hr[I], uday[I]])).values
    f["spr_rel"] = spr[I] / refv
    f["spr_atr"] = spr[I] / A5
    X = np.column_stack([f[k] for k in FNAMES]).astype(np.float32)
    X[~np.isfinite(X)] = np.nan

    # gates
    modI, dowI, nyI = mod[I], dow[I], ny_mod[I]
    gate = ~((modI >= 20 * 60 + 30) & (modI < 23 * 60 + 30))
    gate &= ~((dowI == 4) & (modI >= 19 * 60))
    gate &= dowI < 5
    gate &= ~((nyI >= 16 * 60 + 15) & (nyI < 18 * 60))
    gate &= np.isfinite(A5) & (A5 > 0) & (K >= 48)
    near = np.minimum(nxt - td, td - prv)
    # minutes until 16:45 ET (flat), used for the order and the label horizon
    to_flat = (16 * 60 + 45 - nyI) % 1440
    derived = {"I": I, "A5": A5, "X": X, "gate": gate, "near_ev_ms": near, "to_flat": to_flat, "td": td}
    if fp is not None:
        fp.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(fp, **derived)
    out = {**core, **derived}
    _BASE[key] = out
    return out


def _cache_dir():
    d = os.environ.get("ML_META_CACHE")
    return Path(d) if d else None


def mirror_X(X: np.ndarray) -> np.ndarray:
    Xm = X.copy()
    col = {k: j for j, k in enumerate(FNAMES)}
    for k, rule in FEATS.items():
        j = col[k]
        if rule == "neg":
            Xm[:, j] = -X[:, j]
        elif rule == "flip":
            Xm[:, j] = 1.0 - X[:, j]
        elif rule and rule.startswith("swap:"):
            Xm[:, j] = X[:, col[rule[5:]]]
    return Xm


# ------------------------------------------------------------------------------------------------ labels
def labels(b: dict, a: float, T: int):
    T = int(T)
    """(y_long, y_short, t_end, R_long, R_short, complete) for every decision point: bracket +-a*A5 on MID M1 bars,
    T minutes max. R_* = +1 target first, -1 stop first, else the mark-to-market at the end of the window in units
    of w; complete = the window ends inside the (truncated) data."""
    I, ts, h, l, c = b["I"], b["ts"], b["h"], b["l"], b["c"]
    n1 = b["n1"]
    e = c[I]
    w = a * b["A5"]
    H = np.minimum(T, np.maximum(b["to_flat"] - 1, 1))  # never past 16:45 ET (= ts[i] + to_flat minutes)
    tend = ts[I] + 60_000 + H * 60_000                 # label window [ts[i]+60s, tend)
    stL = np.zeros(len(I), dtype=np.int8)
    stS = np.zeros(len(I), dtype=np.int8)
    last_c = e.copy()
    for j in range(1, T + 1):
        idx = I + j
        ok = idx < n1
        idx = np.minimum(idx, n1 - 1)
        ok &= ts[idx] < tend
        hj, lj = h[idx], l[idx]
        u = ok & (stL == 0)
        lossL = u & (lj <= e - w)
        winL = u & ~lossL & (hj >= e + w)
        stL[lossL] = -1
        stL[winL] = 1
        u = ok & (stS == 0)
        lossS = u & (hj >= e + w)
        winS = u & ~lossS & (lj <= e - w)
        stS[lossS] = -1
        stS[winS] = 1
        last_c = np.where(ok, c[idx], last_c)
    RL = np.where(stL != 0, stL.astype(float), (last_c - e) / w)
    RS = np.where(stS != 0, stS.astype(float), (e - last_c) / w)
    complete = tend <= ts[n1 - 1]
    return (stL == 1).astype(np.int8), (stS == 1).astype(np.int8), tend, RL, RS, complete


# ------------------------------------------------------------------------------------------------ model
def _logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _staged_at(clf, X, it):
    """Raw P(win) after `it` boosting iterations."""
    for s, p in enumerate(clf.staged_predict_proba(X), start=1):
        if s == it:
            return p[:, 1]
    return clf.predict_proba(X)[:, 1]


def _cache_path(a, T, mode):
    d = _cache_dir()
    if d is None:
        return None
    tag = hashlib.md5(json.dumps({"a": a, "T": T, "mode": mode, "hp": HP, "f": FNAMES, "v": 3},
                                 sort_keys=True).encode()).hexdigest()[:10]
    return d / f"ml_meta_{float(a)}_{int(T)}_{mode}_{tag}.npz"


def predictions(m1, a: float, T: int, mode: str = "mirror") -> dict:
    """Fit (or load) the model for bracket (a, T) and return calibrated P_long / P_short for every decision point,
    plus diagnostics. Uses only decision points whose label window ends before 2026-01-01 for fitting/calibration."""
    a, T = float(a), int(T)
    key = (a, T, mode)
    if key in _PRED:
        return _PRED[key]
    b = _base(m1)
    cp = _cache_path(a, T, mode)
    if cp is not None and cp.exists():
        z = np.load(cp, allow_pickle=True)
        out = {k: z[k] for k in z.files}
        out["diag"] = json.loads(str(out["diag"]))
        _PRED[key] = out
        return out
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import log_loss, roc_auc_score
    from threadpoolctl import threadpool_limits

    X, gate, td = b["X"], b["gate"], b["td"]
    news_ok = b["near_ev_ms"] > 30 * 60_000
    yL, yS, tend, RL, RS, complete = labels(b, a, T)
    Xm = mirror_X(X)
    elig = gate & news_ok
    fit = elig & (tend <= FIT_END)
    cal = elig & (td >= FIT_END) & (tend <= CAL_END)
    val = elig & (td >= CAL_END) & complete
    diag = {"a": a, "T": T, "mode": mode, "n_fit_points": int(fit.sum()), "n_q4_points": int(cal.sum()),
            "n_valid_points": int(val.sum())}
    with threadpool_limits(limits=1):
        if mode == "mirror":
            Xf = np.vstack([X[fit], Xm[fit]])
            yf = np.r_[yL[fit], yS[fit]]
            Xc = np.vstack([X[cal], Xm[cal]])
            yc = np.r_[yL[cal], yS[cal]]
            clf = HistGradientBoostingClassifier(**HP).fit(Xf, yf)
            losses = [log_loss(yc, p[:, 1]) for p in clf.staged_predict_proba(Xc)]
            it = int(np.argmin(losses)) + 1
            it = max(it, 10)
            rawL = _staged_at(clf, X, it)
            rawS = _staged_at(clf, Xm, it)
            rc = np.r_[rawL[cal], rawS[cal]]
            pl = LogisticRegression(C=1e6).fit(_logit(rc)[:, None], yc)
            PL = pl.predict_proba(_logit(rawL)[:, None])[:, 1]
            PS = pl.predict_proba(_logit(rawS)[:, None])[:, 1]
            diag["best_iter"] = it
            diag["q4_logloss_best"] = round(float(losses[it - 1]), 5)
            diag["q4_logloss_iter1"] = round(float(losses[0]), 5)
            diag["q4_logloss_base"] = round(float(log_loss(yc, np.full(len(yc), yc.mean()))), 5)
        else:  # 'sep': separate long and short models (directional comparison arm)
            PL, PS, its = None, None, []
            for side in (1, -1):
                y = yL if side == 1 else yS
                clf = HistGradientBoostingClassifier(**HP).fit(X[fit], y[fit])
                losses = [log_loss(y[cal], p[:, 1]) for p in clf.staged_predict_proba(X[cal])]
                it = max(int(np.argmin(losses)) + 1, 10)
                raw = _staged_at(clf, X, it)
                pl = LogisticRegression(C=1e6).fit(_logit(raw[cal])[:, None], y[cal])
                P = pl.predict_proba(_logit(raw)[:, None])[:, 1]
                its.append(it)
                if side == 1:
                    PL = P
                else:
                    PS = P
            diag["best_iter"] = its
    for nm, msk in (("fit", fit), ("q4", cal), ("valid", val)):
        d = {"base_rate_long": round(float(yL[msk].mean()), 4), "base_rate_short": round(float(yS[msk].mean()), 4)}
        for side, y, P in (("long", yL, PL), ("short", yS, PS)):
            try:
                d[f"auc_{side}"] = round(float(roc_auc_score(y[msk], P[msk])), 4)
            except ValueError:
                d[f"auc_{side}"] = None
        yy = np.r_[yL[msk], yS[msk]]
        pp = np.r_[PL[msk], PS[msk]]
        d["auc_stacked"] = round(float(roc_auc_score(yy, pp)), 4)
        d["brier"] = round(float(np.mean((pp - yy) ** 2)), 5)
        d["brier_base"] = round(float(np.mean((yy.mean() - yy) ** 2)), 5)
        # calibration by decile of predicted P (stacked long + short rows)
        qs = np.quantile(pp, np.linspace(0, 1, 11))
        bins = np.clip(np.searchsorted(qs, pp, side="right") - 1, 0, 9)
        d["calib"] = [[round(float(pp[bins == k].mean()), 4), round(float(yy[bins == k].mean()), 4),
                       int((bins == k).sum())] for k in range(10)]
        # directional AUC: does P_long - P_short rank the realized label-R of the long bracket?
        diag[nm] = d
    out = {"PL": PL.astype(np.float32), "PS": PS.astype(np.float32), "yL": yL, "yS": yS,
           "RL": RL.astype(np.float32), "fitmask": fit, "calmask": cal, "valmask": val,
           "diag": diag}
    if cp is not None:
        cp.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(cp, **{k: v for k, v in out.items() if k != "diag"}, diag=json.dumps(diag))
    _PRED[key] = out
    return out


# ------------------------------------------------------------------------------------------------ orders
def orders(m1, a=1.0, T=60, q=0.05, flip=0, mode="mirror", emit="oos", news=30):
    a, T, q, flip, news = float(a), int(T), float(q), int(flip), int(news)
    b = _base(m1)
    P = predictions(m1, a, T, mode)
    I, A5, td, gate = b["I"], b["A5"], b["td"], b["gate"]
    ts = b["ts"]
    w = a * A5
    elig = gate & (b["near_ev_ms"] > news * 60_000)
    if flip:
        elig &= (w >= FLIP[0]) & (w <= FLIP[1])
    pmax = np.maximum(P["PL"], P["PS"])
    q4 = elig & (td >= FIT_END) & (td < CAL_END)
    if not q4.any():
        return []
    thr = float(np.quantile(pmax[q4], 1.0 - q))
    fire = elig & (pmax >= thr)
    if emit == "oos":
        fire &= td >= FIT_END
    fire &= td < TEST_MS
    flat = td + b["to_flat"].astype(np.int64) * 60_000 - 60_000   # 16:45 ET (to_flat counted from bar open)
    out = []
    for r in np.flatnonzero(fire):
        d = 1 if P["PL"][r] >= P["PS"][r] else -1
        t = int(ts[I[r]]) + 60_000
        tag = "insample" if t < FIT_END else ("q4_oos" if t < CAL_END else "valid")
        out.append({"t": t, "d": d, "kind": "mkt", "sl_dist": float(w[r]), "tp_dist": float(w[r]),
                    "tmax": int(T) * 60_000, "flat": int(flat[r]), "tag": tag,
                    "p": round(float(pmax[r]), 4)})
    return out
