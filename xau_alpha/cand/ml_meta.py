"""
cand/ml_meta.py - X3: a machine-learned ENTRY FILTER (sklearn 1.3.2 HistGradientBoostingClassifier) on causal features
computed at every M5 close.

Hypothesis (OPINION): generic causal price / range / zone / time / cost features at an M5 close carry enough
information to rank bars by the probability that a symmetric +-w bracket (on MID prices) reaches its target before its
stop within T minutes, well enough that the top-ranked bars beat LiteFinance costs.

Revision 2026-09-30 (second agent; the first was killed mid-run). Changes against the first agent's partial version:
  * all features are recomputed with the FIXED features.window_range (running max/min, exposed only after the window);
    the cache key hashes lib/features.py + lib/data.py so no pre-fix cache can ever be loaded;
  * the stop/target distance w is 'cap' by default: w = clip(a*A5, 1.2, 4.0) $/oz ("a in ATR terms with a dollar
    cap", every trade flip-eligible); labels are computed with that same w, and the effective bracket w/A5 is a model
    feature ('wA'), because the cap binds on 73-97% of VALID bars at a = 1 (A5 median 5-8 $/oz in 2026) but rarely in
    Jan-Sep 2025. wmode='atr' (w = a*A5, uncapped; optional flip filter) is a comparison arm only;
  * causal checks: tests/test_ml_meta_causal.py (feature truncation invariance; the model does not change when all
    data from 2026-01-01 on is removed).

Pipeline (everything happens inside orders(); the fitted model only ever sees data before 2026-01-01, so VALID
(2026-01..05) is out-of-sample):

 1. Decision points: every completed M5 bar (data.resample_causal). The decision is taken at the close of the M1 bar
    at which the M5 bar becomes known; the order has t = ts[i] + 60 s. Entry gates (SYNTHESIS section 5):
    no entries 20:30-23:30 UTC, Friday >= 19:00 UTC, Saturday/Sunday (UTC), 16:15-18:00 ET; no entries within
    +-30 min of a HIGH calendar row (the < $21 equity rule, N2/G4). Positions are flattened at 16:45 ET.
 2. Features (causal, scale-free; 39 columns in FEATS + 'wA'): M5 returns over 1/3/6/12/48 bars in A5 units; last M5
    bar body/wicks/range; ATR ratios (M1 ATR / A5, A5_14 / A5_96, A5_14 / A5_288, A5 / its prior-20-day median);
    position in the trading day's range so far, in the completed Asia range 00:00-06:59 UTC (keyed by UTC date) and in
    the prior trading day's range; distance to prior-day high/low in A5; distance to the nearest multi-touch zone above
    / below (features.zone_timeline, L=240, k=3, w=0.5, K=3) in A5 and its touch count; inside-zone flag; London / New
    York minute of day (sin/cos); day of week; minutes to the next / since the last HIGH calendar event; sweep flags (a
    high above PDH / Asia high in the last 15 M1 bars and a close back below it, and the mirror); signed M5 FVG size,
    count of signed FVGs over the last 3 M5 bars; signed M5 engulfing; M1 spread / its 20-day same-UTC-hour median
    (prior days only); M1 spread / A5; and the effective bracket wA = w / A5.
 3. Labels on MID prices: from the decision close e = c[i], walk M1 bars i+1.. for at most T minutes (never past
    16:45 ET). Long win = high >= e+w before low <= e-w (both in one bar = loss); short is the mirror. Timeouts count
    as "not a win".
 4. Model: ONE classifier for both sides on the stacked data (long rows with features x, short rows with MIRRORED
    features m(x): signed features negated, range positions p -> 1-p, above/below pairs swapped). P_long = f(x),
    P_short = f(m(x)). Long and short rules are therefore identical mirror images and the model cannot learn an
    unconditional drift. (mode='sep', two separate side models, is a comparison arm only.)
      FIT        decision points with t < 2025-10-01 whose label window ends by 2025-10-01 (max_iter 300,
                 random_state 0, early_stopping off, single thread).
      EARLY-STOP the boosting iteration (>= 10) with the lowest log-loss on the 2025-Q4 slice (2025-10-01..12-31,
                 label window ends by 2026-01-01).
      CALIBRATE  Platt scaling of the raw probability on the same Q4 slice.
      THRESHOLD  thr = the (1-q) quantile of max(P_long, P_short) over the Q4 decision points that pass the gates;
                 q is chosen on the Q4 slice (the sweep's "train" split is Q4 when emit='oos').
 5. Orders: side = argmax(P_long, P_short); trade when that P >= thr; market order, sl_dist = tp_dist = w (from the
    fill), tmax = T, flat 16:45 ET.

emit: 'oos' (default) emits only the Q4-2025 slice (out-of-sample for the fit, but used for early stopping,
      calibration and the threshold) and VALID. 'all' also emits the in-sample Jan-Sep 2025 bars (tag 'insample':
      never judge them). Nothing at t >= 2026-06-01 is computed: m1 is truncated before any feature or label is built.

Set the environment variable ML_META_CACHE to a directory to cache the feature matrix and the fitted-model
predictions (npz, a few MB each).
"""
import hashlib
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
from data import _ms, atr, load_calendar, resample_causal  # noqa: E402
import features as F  # noqa: E402

FIT_END = _ms("2025-10-01")
CAL_END = _ms("2026-01-01")
TEST_MS = _ms("2026-06-01")
FLIP = (1.2, 4.0)
FEAT_VERSION = 5          # bump when build_features changes
MODEL_VERSION = 5         # bump when labels / predictions change

GRID = {"a": [0.75, 1.0, 1.5], "T": [30, 60, 120], "q": [0.02, 0.05, 0.10]}
DEFAULTS = {"a": 1.0, "T": 60, "q": 0.05, "wmode": "cap", "flip": 0, "mode": "mirror", "emit": "oos", "news": 30,
            "score": "p"}

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
EXTRA = ["wA"]            # appended per bracket (side-neutral)
ALL_NAMES = FNAMES + EXTRA

_BASE: dict = {}
_PRED: dict = {}


# ------------------------------------------------------------------------------------------------ helpers
def _cache_dir():
    d = os.environ.get("ML_META_CACHE")
    return Path(d) if d else None


def _engine_hash() -> str:
    h = hashlib.md5()
    for p in (LIB / "features.py", LIB / "data.py"):
        h.update(p.read_bytes())
    return h.hexdigest()[:10]


def bracket(A5: np.ndarray, a: float, wmode: str = "cap") -> np.ndarray:
    """Stop = target distance in $/oz. 'cap': clip(a*A5, 1.2, 4.0) (always flip-eligible); 'atr': a*A5."""
    if wmode == "cap":
        return np.clip(a * A5, FLIP[0], FLIP[1])
    if wmode == "atr":
        return a * A5
    raise ValueError(wmode)


# ------------------------------------------------------------------------------------------------ features
def build_features(m1: pd.DataFrame) -> dict:
    """Decision points, causal features and gates for an M1 frame. Pure function (no caching, no truncation):
    the value at a decision point depends only on M1 bars <= its decision bar (tests/test_ml_meta_causal.py)."""
    ts = m1["ts"].values.astype(np.int64)
    o, h, l, c = (m1[x].values.astype(np.float64) for x in "ohlc")
    n1 = len(m1)
    A1 = atr(m1)
    htf, known = resample_causal(m1, 5)
    ki = np.searchsorted(known, np.arange(len(htf)), side="left")   # M1 index at whose close M5 bar k is known
    K = np.flatnonzero(ki < n1)
    I = ki[K]
    keep = np.r_[I[1:] != I[:-1], True]                              # one decision per M1 bar (last M5 bar known)
    K, I = K[keep], I[keep]
    assert np.array_equal(known[I], K), "decision bar must be the first M1 close at which its M5 bar is known"
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
    med20 = pd.Series(A5all).shift(1).rolling(5760, min_periods=1000).median().values   # prior 20 days of M5 bars
    f["atr_reg20d"] = A5 / med20[K]
    tday = m1["tday"].values
    dhi, dlo = F.running_day_extremes(tday, h, l)
    rngd = dhi[I] - dlo[I]
    f["pos_day"] = np.where(rngd > 0, (cn - dlo[I]) / np.where(rngd > 0, rngd, 1), np.nan)
    f["day_rng"] = rngd / A5
    # Asia range 00:00-06:59 UTC keyed by the UTC date (fixed window_range: exposed only once the window is over)
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
    for r, j in enumerate(jz):
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
    ev = np.sort(cal.loc[cal["impact"] == "HIGH", "ts"].values.astype(np.int64))   # scheduled release times
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
    ref = tab.groupby(level=0).transform(lambda s: s.shift(1).rolling(20, min_periods=5).median())  # prior days
    refv = ref.reindex(pd.MultiIndex.from_arrays([hr[I], uday[I]])).values
    f["spr_rel"] = spr[I] / refv
    f["spr_atr"] = spr[I] / A5
    X = np.column_stack([f[k] for k in FNAMES]).astype(np.float32)
    X[~np.isfinite(X)] = np.nan

    # gates (decision time = close of bar I)
    modI, dowI, nyI = mod[I], dow[I], ny_mod[I]
    gate = ~((modI >= 20 * 60 + 30) & (modI < 23 * 60 + 30))
    gate &= ~((dowI == 4) & (modI >= 19 * 60))
    gate &= dowI < 5
    gate &= ~((nyI >= 16 * 60 + 15) & (nyI < 18 * 60))
    gate &= np.isfinite(A5) & (A5 > 0) & (K >= 48)
    near = np.minimum(nxt - td, td - prv)
    to_flat = (16 * 60 + 45 - nyI) % 1440          # minutes from bar I's open to 16:45 ET
    return {"ts": ts, "h": h, "l": l, "c": c, "n1": n1,
            "I": I, "K": K, "A5": A5, "X": X, "gate": gate, "near_ev_ms": near, "to_flat": to_flat, "td": td}


def _base(m1: pd.DataFrame) -> dict:
    """build_features on m1 truncated at 2026-06-01 (the TEST holdout is never touched), cached in memory / on disk."""
    key = (len(m1), int(m1["ts"].values[0]), int(m1["ts"].values[-1]))
    if key in _BASE:
        return _BASE[key]
    _BASE.clear()
    _PRED.clear()
    m1 = m1[m1["ts"].values < TEST_MS].reset_index(drop=True)
    dkey = f"{len(m1)}_{int(m1['ts'].values[0])}_{int(m1['ts'].values[-1])}"   # identity of the truncated data
    cp = _cache_dir()
    fp = None
    if cp is not None:
        tag = hashlib.md5(json.dumps({"f": FNAMES, "d": dkey, "v": FEAT_VERSION, "eng": _engine_hash()}
                                     ).encode()).hexdigest()[:10]
        fp = cp / f"ml_meta_feat_{tag}.npz"
        if fp.exists():
            z = np.load(fp)
            out = {k: z[k] for k in z.files}
            out["n1"] = int(out["n1"])
            out["dkey"] = dkey
            _BASE[key] = out
            return out
    out = build_features(m1)
    if fp is not None:
        fp.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(fp, **out)
    out["dkey"] = dkey
    _BASE[key] = out
    return out


def mirror_X(X: np.ndarray) -> np.ndarray:
    """Mirror image of the feature rows (price negated). Columns beyond FNAMES are side-neutral."""
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
def labels(b: dict, w: np.ndarray, T: int):
    """(y_long, y_short, t_end, R_long, R_short, complete) for every decision point: bracket +-w on MID M1 bars,
    at most T minutes and never past 16:45 ET. R_* = +1 target first, -1 stop first (both in one bar = stop), else
    the mark-to-market at the end of the window in units of w; complete = the window ends inside the (truncated)
    data. Labels look ahead by construction: only rows whose t_end is before a boundary may be used before it."""
    T = int(T)
    I, ts, h, l, c = b["I"], b["ts"], b["h"], b["l"], b["c"]
    n1 = int(b["n1"])
    e = c[I]
    H = np.minimum(T, np.maximum(b["to_flat"] - 1, 1))
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


def _cache_path(a, T, wmode, mode, dkey):
    d = _cache_dir()
    if d is None:
        return None
    tag = hashlib.md5(json.dumps({"a": a, "T": T, "wmode": wmode, "mode": mode, "hp": HP, "f": ALL_NAMES,
                                  "fv": FEAT_VERSION, "mv": MODEL_VERSION, "eng": _engine_hash(), "d": dkey},
                                 sort_keys=True).encode()).hexdigest()[:10]
    return d / f"ml_meta_{float(a)}_{int(T)}_{wmode}_{mode}_{tag}.npz"


def model_matrix(b: dict, w: np.ndarray) -> np.ndarray:
    return np.column_stack([b["X"], (w / b["A5"]).astype(np.float32)])


def _split_diag(yL, yS, PL, PS, RL, RS, msk):
    from sklearn.metrics import log_loss, roc_auc_score
    if msk.sum() < 50:
        return {"n": int(msk.sum())}
    d = {"n": int(msk.sum()), "base_rate_long": round(float(yL[msk].mean()), 4),
         "base_rate_short": round(float(yS[msk].mean()), 4)}
    for side, y, P in (("long", yL, PL), ("short", yS, PS)):
        try:
            d[f"auc_{side}"] = round(float(roc_auc_score(y[msk], P[msk])), 4)
        except ValueError:
            d[f"auc_{side}"] = None
    yy = np.r_[yL[msk], yS[msk]].astype(float)
    pp = np.r_[PL[msk], PS[msk]].astype(float)
    d["auc_stacked"] = round(float(roc_auc_score(yy, pp)), 4)
    d["brier"] = round(float(np.mean((pp - yy) ** 2)), 5)
    d["brier_base"] = round(float(np.mean((yy.mean() - yy) ** 2)), 5)
    d["logloss"] = round(float(log_loss(yy, pp)), 5)
    d["logloss_base"] = round(float(log_loss(yy, np.full(len(yy), yy.mean()))), 5)
    qs = np.quantile(pp, np.linspace(0, 1, 11))
    bins = np.clip(np.searchsorted(qs, pp, side="right") - 1, 0, 9)
    d["calib"] = [[round(float(pp[bins == k].mean()), 4), round(float(yy[bins == k].mean()), 4),
                   int((bins == k).sum())] for k in range(10)]
    # DIRECTION: among bars where exactly one side won, does P_long - P_short pick it?
    one = msk & ((yL == 1) ^ (yS == 1))
    d["n_one_side"] = int(one.sum())
    d["auc_direction"] = round(float(roc_auc_score(yL[one], (PL - PS)[one])), 4) if one.any() else None
    # RESOLUTION: does P_long + P_short predict that some bracket wins (a volatility forecast, no direction)?
    anyr = ((yL == 1) | (yS == 1))
    try:
        d["auc_resolution"] = round(float(roc_auc_score(anyr[msk], (PL + PS)[msk])), 4)
    except ValueError:
        d["auc_resolution"] = None
    pm = np.maximum(PL, PS)
    side = PL >= PS
    for qq in (0.10, 0.05, 0.02):
        thr = np.quantile(pm[msk], 1 - qq)
        top = msk & (pm >= thr)
        d[f"top{int(qq * 100)}"] = {
            "n": int(top.sum()), "mean_p": round(float(pm[top].mean()), 4),
            "win_model_side": round(float(np.where(side, yL, yS)[top].mean()), 4),
            "R_model_side": round(float(np.where(side, RL, RS)[top].mean()), 4),
            "R_opposite": round(float(np.where(side, RS, RL)[top].mean()), 4),
            "long_share": round(float(side[top].mean()), 3)}
    d["all_R_long"] = round(float(RL[msk].mean()), 4)
    d["all_R_short"] = round(float(RS[msk].mean()), 4)
    return d


def predictions(m1, a: float, T: int, wmode: str = "cap", mode: str = "mirror") -> dict:
    """Fit (or load) the model for bracket (a, T, wmode) and return calibrated P_long / P_short for every decision
    point, plus diagnostics. Only decision points whose label window ends before 2026-01-01 are used for fitting,
    early stopping and calibration."""
    a, T = float(a), int(T)
    b = _base(m1)
    key = (a, T, wmode, mode, b["dkey"])
    if key in _PRED:
        return _PRED[key]
    cp = _cache_path(a, T, wmode, mode, b["dkey"])
    if cp is not None and cp.exists():
        z = np.load(cp, allow_pickle=False)
        out = {k: z[k] for k in z.files if k != "diag"}
        out["diag"] = json.loads(str(z["diag"]))
        _PRED[key] = out
        return out
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import log_loss
    from threadpoolctl import threadpool_limits

    w = bracket(b["A5"], a, wmode)
    X = model_matrix(b, w)
    Xm = mirror_X(X)
    gate, td = b["gate"], b["td"]
    news_ok = b["near_ev_ms"] > 30 * 60_000
    yL, yS, tend, RL, RS, complete = labels(b, w, T)
    elig = gate & news_ok & np.isfinite(w)
    fit = elig & (tend <= FIT_END)
    cal = elig & (td >= FIT_END) & (tend <= CAL_END)
    val = elig & (td >= CAL_END) & (td < TEST_MS) & complete
    assert tend[fit].max() <= FIT_END and tend[cal].max() <= CAL_END and td[cal].min() >= FIT_END
    wA = w / b["A5"]
    diag = {"a": a, "T": T, "wmode": wmode, "mode": mode, "n_fit_points": int(fit.sum()),
            "n_q4_points": int(cal.sum()), "n_valid_points": int(val.sum()),
            "w_median": {nm: round(float(np.median(w[m])), 3) if m.any() else None
                         for nm, m in (("fit", fit), ("q4", cal), ("valid", val))},
            "wA_median": {nm: round(float(np.median(wA[m])), 3) if m.any() else None
                          for nm, m in (("fit", fit), ("q4", cal), ("valid", val))}}
    with threadpool_limits(limits=1):
        if mode == "mirror":
            Xf = np.vstack([X[fit], Xm[fit]])
            yf = np.r_[yL[fit], yS[fit]]
            Xc = np.vstack([X[cal], Xm[cal]])
            yc = np.r_[yL[cal], yS[cal]]
            clf = HistGradientBoostingClassifier(**HP).fit(Xf, yf)
            losses = [log_loss(yc, p[:, 1]) for p in clf.staged_predict_proba(Xc)]
            it = max(int(np.argmin(losses)) + 1, 10)
            rawL = _staged_at(clf, X, it)
            rawS = _staged_at(clf, Xm, it)
            rc = np.r_[rawL[cal], rawS[cal]]
            pl = LogisticRegression(C=1e6).fit(_logit(rc)[:, None], yc)
            PL = pl.predict_proba(_logit(rawL)[:, None])[:, 1]
            PS = pl.predict_proba(_logit(rawS)[:, None])[:, 1]
            diag["best_iter"] = it
            diag["q4_logloss_curve"] = [round(float(losses[k - 1]), 5) for k in (1, 10, 25, 50, 100, 200, 300)
                                        if k <= len(losses)]
            diag["q4_logloss_best"] = round(float(losses[it - 1]), 5)
            diag["q4_logloss_base"] = round(float(log_loss(yc, np.full(len(yc), yc.mean()))), 5)
            diag["platt"] = [round(float(pl.coef_[0, 0]), 4), round(float(pl.intercept_[0]), 4)]
            # importance proxy (HGB exposes no gain): how often each column is split on in the kept trees
            try:
                used = np.zeros(X.shape[1], dtype=int)
                for tree_list in clf._predictors[:it]:
                    for tr in tree_list:
                        nd = tr.nodes
                        np.add.at(used, nd["feature_idx"][nd["is_leaf"] == 0].astype(int), 1)
                diag["split_counts"] = {ALL_NAMES[j]: int(used[j]) for j in np.argsort(-used) if used[j] > 0}
            except Exception as e:  # private API; diagnostics only
                diag["split_counts"] = repr(e)[:100]
        else:  # 'sep': separate long and short models (comparison arm)
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
        diag[nm] = _split_diag(yL, yS, PL, PS, RL, RS, msk)
    out = {"PL": PL.astype(np.float32), "PS": PS.astype(np.float32), "yL": yL, "yS": yS,
           "RL": RL.astype(np.float32), "RS": RS.astype(np.float32), "tend": tend,
           "fitmask": fit, "calmask": cal, "valmask": val, "diag": diag}
    if cp is not None:
        cp.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(cp, **{k: v for k, v in out.items() if k != "diag"}, diag=json.dumps(diag))
    _PRED[key] = out
    return out


# ------------------------------------------------------------------------------------------------ orders
def select_score(P: dict, score: str = "p") -> np.ndarray:
    """'p' (the brief): P(win) of the better side = max(P_long, P_short). 'edge' (comparison arm): |P_long - P_short|,
    the directional part only (with a symmetric bracket E[R_long] ~ P_long - P_short, so 'p' also rewards bars where
    BOTH brackets are likely to resolve, i.e. volatility)."""
    if score == "p":
        return np.maximum(P["PL"], P["PS"])
    if score == "edge":
        return np.abs(P["PL"].astype(np.float64) - P["PS"].astype(np.float64))
    raise ValueError(score)


def threshold(P: dict, elig: np.ndarray, td: np.ndarray, q: float, score: str = "p") -> float:
    """Top-q quantile of the selection score over the gated Q4-2025 decision points (label-free)."""
    s = select_score(P, score)
    q4 = elig & (td >= FIT_END) & (td < CAL_END)
    return float(np.quantile(s[q4], 1.0 - q)) if q4.any() else float("inf")


def eligible(b: dict, w: np.ndarray, wmode: str, flip: int, news: int) -> np.ndarray:
    elig = b["gate"] & (b["near_ev_ms"] > news * 60_000) & np.isfinite(w)
    if wmode == "atr" and flip:
        elig &= (w >= FLIP[0]) & (w <= FLIP[1])
    return elig


def orders(m1, a=1.0, T=60, q=0.05, wmode="cap", flip=0, mode="mirror", emit="oos", news=30, score="p"):
    a, T, q, flip, news = float(a), int(T), float(q), int(flip), int(news)
    b = _base(m1)
    P = predictions(m1, a, T, wmode, mode)
    I, td = b["I"], b["td"]
    ts = b["ts"]
    w = bracket(b["A5"], a, wmode)
    elig = eligible(b, w, wmode, flip, news)
    thr = threshold(P, elig, td, q, score)
    s = select_score(P, score)
    pmax = np.maximum(P["PL"], P["PS"])
    fire = elig & (s >= thr) & (td < TEST_MS)
    if emit == "oos":
        fire &= td >= FIT_END
    flat = td + b["to_flat"].astype(np.int64) * 60_000 - 60_000   # 16:45 ET (to_flat counted from bar open)
    out = []
    for r in np.flatnonzero(fire):
        d = 1 if P["PL"][r] >= P["PS"][r] else -1
        t = int(ts[I[r]]) + 60_000
        tag = "insample" if t < FIT_END else ("q4_oos" if t < CAL_END else "valid")
        out.append({"t": t, "d": d, "kind": "mkt", "sl_dist": float(w[r]), "tp_dist": float(w[r]),
                    "tmax": int(T) * 60_000, "flat": int(flat[r]), "tag": tag, "p": round(float(pmax[r]), 4)})
    return out
