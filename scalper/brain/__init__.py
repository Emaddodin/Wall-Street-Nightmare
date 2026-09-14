"""THE BRAIN -- a learned trade filter, not a promise.

A genius daytrader's rarest skill is selectivity: knowing which setups to
take and which to let pass.  The brain learns that skill from the bot's own
experience -- thousands of closed trades replayed by the real engine over
the full candle history, plus every live trade -- and turns each candidate
entry into a win probability:

    features (known at entry time, no lookahead):
      hour of day, day of week, session label, strategy, entry model,
      direction, coin kind (size x volatility), 1m ATR as % of price,
      stop distance as % of price, plan-R (target vs stop), leverage

    label: did the trade win (pnl > 0)

The model is a gradient-boosted tree trained with a strict TIME split:
the last slice of history is never used for training, only for grading.
The live book consults the brain before every fill and skips entries it
scores below the veto bar -- but never more than (1 - min_keep_frac) of
recent signals, so the brain filters, it does not freeze.

If the model is missing or the training set is too small, the brain stays
quiet ("warming up") and the book trades as before.
"""
from __future__ import annotations

import json
import pickle
import time
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np

FEATURES = [
    "hour_utc",          # 0..23
    "hour_sin",          # time of day as a circle (session seasonality)
    "hour_cos",
    "dow",               # 0=Mon .. 6=Sun
    "session",           # categorical: session label string
    "strategy",          # categorical
    "entry_model",       # categorical
    "direction",         # 1 long / -1 short
    "kind",              # categorical: "major-normal", "micro-wild", ...
    "atr1m_pct",         # 1m ATR as % of entry price
    "stop_dist_pct",     # |entry - stop| / entry * 100
    "plan_r",            # |tp - entry| / |entry - stop|, 0.0 = runner only
    "leverage",          # position leverage
    # -- 2026 edge research, computable from 1m OHLCV + 15m aggregates:
    "mom5_r",            # 5-bar 1m return in units of 1m ATR (momentum)
    "atr_regime_pct",    # current 15m range percentile vs trailing 96 bars
    "vol_z",             # 1m volume vs same minute-of-day 20d median
    "trend_ema",         # 15m EMA20 slope in units of 15m ATR (signed)
    "range_pos",         # entry's position in the 96x15m high-low range 0..1
]

CAT_FEATURES = ["session", "strategy", "entry_model", "kind"]

# the engine needs 500 x 15m bars of history before it can trade
WARMUP_MS = 6 * 86_400_000


def session_label(ts_ms: int, cfg: Any) -> str:
    from engine import session_label as _sl
    return _sl(ts_ms, cfg)


def kind_of(symbol: str, frames: dict, ts_ms: int) -> str:
    """Coin kind from the candle frame (last 24h before the bar)."""
    from strategies.inventor import coin_meta_from_frame
    df = frames.get(symbol)
    if df is None:
        return "?"
    lo = ts_ms - 24 * 3_600_000
    d = df[(df["open_time"] >= lo) & (df["open_time"] <= ts_ms)]
    if len(d) < 60:
        d = df[df["open_time"] <= ts_ms].tail(1440)
    if not len(d):
        return "?"
    return coin_meta_from_frame(symbol, d)["kind"]


def candle_features(df, ts_ms: int) -> dict:
    """Entry-time market context from the 1m frame (everything is computed
    from bars CLOSED at or before ts -- no lookahead)."""
    try:
        d = df[df["open_time"] <= ts_ms]
        if len(d) < 1440:
            return {}
        d = d.tail(20 * 1440)
        h = d["high"].to_numpy(float)
        l = d["low"].to_numpy(float)
        c = d["close"].to_numpy(float)
        v = d["volume"].to_numpy(float)
        t = d["open_time"].to_numpy()
        px = float(c[-1])
        if px <= 0:
            return {}
        rng1 = (h - l)
        atr1 = float(rng1[-60:].mean()) if len(d) >= 60 else float(rng1.mean())
        # 5-bar momentum in 1m-ATR units
        mom5_r = (c[-1] / c[-6] - 1.0) / (atr1 / px) if atr1 > 0 else 0.0
        # volatility regime: current 15m range percentile vs trailing 96x15m
        def bar15(i: int) -> float:
            seg_h = h[i:i + 15]
            seg_l = l[i:i + 15]
            return float(seg_h.max() - seg_l.min())
        n15 = len(d) // 15
        ranges15 = [bar15(i * 15) for i in range(n15 - 1)]
        cur15 = bar15((n15 - 1) * 15)
        if ranges15:
            atr_regime_pct = float(sum(1 for r in ranges15 if r < cur15)
                                   / len(ranges15))
        else:
            atr_regime_pct = 0.5
        # relative volume vs the same minute-of-day over the trailing 20 days
        mod = int(t[-1] // 60_000) % 1440
        same = v[(t // 60_000) % 1440 == mod]
        vol_z = (float(v[-1]) - float(same.mean())) / float(same.std() + 1e-9) \
            if len(same) >= 5 else 0.0
        # 15m EMA20 slope in ATR15 units
        closes15 = c[::15][-40:]
        if len(closes15) >= 22:
            import numpy as _np
            ema = _np.zeros(len(closes15))
            k = 2.0 / 21.0
            ema[0] = closes15[0]
            for i in range(1, len(closes15)):
                ema[i] = closes15[i] * k + ema[i - 1] * (1 - k)
            if ranges15:
                atr15 = float(_np.mean(ranges15[-30:]))
            else:
                atr15 = atr1
            trend_ema = float((ema[-1] - ema[-8]) / atr15) if atr15 > 0 else 0.0
        else:
            trend_ema = 0.0
        # position inside the 96x15m range
        lo = float(min(l[-96 * 15:])) if len(l) >= 96 * 15 else float(l.min())
        hi = float(max(h[-96 * 15:])) if len(h) >= 96 * 15 else float(h.max())
        range_pos = (px - lo) / (hi - lo) if hi > lo else 0.5
        return {
            "hour_sin": __import__("math").sin(2 * 3.141592653589793
                                               * ((ts_ms // 3_600_000) % 24
                                                  + (ts_ms // 60_000) % 60 / 60)
                                               / 24),
            "hour_cos": __import__("math").cos(2 * 3.141592653589793
                                               * ((ts_ms // 3_600_000) % 24
                                                  + (ts_ms // 60_000) % 60 / 60)
                                               / 24),
            "mom5_r": round(float(mom5_r), 4),
            "atr_regime_pct": round(atr_regime_pct, 3),
            "vol_z": round(vol_z, 3),
            "trend_ema": round(trend_ema, 4),
            "range_pos": round(float(range_pos), 3),
        }
    except Exception:
        return {}


def features_from_record(rec: dict, kind: str, cfg: Any,
                         df=None) -> dict | None:
    """Build one training row from a closed trade record.  Only fields that
    exist BEFORE the entry count (no exit information, ever)."""
    try:
        entry = float(rec["entry"])
        stop = float(rec["stop"])
        ts = int(rec["opened_ms"])
        dist = abs(entry - stop)
        tp = rec.get("tp")
        plan_r = abs(float(tp) - entry) / dist if tp is not None and dist > 0 else 0.0
        atr1m = float(rec.get("atr1m") or 0.0)
        row = {
            "hour_utc": (ts // 3_600_000) % 24,
            "dow": ((ts // 86_400_000) + 3) % 7,
            "session": session_label(ts, cfg),
            "strategy": str(rec.get("strategy") or "?"),
            "entry_model": str(rec.get("entry_model") or "?"),
            "direction": 1 if rec.get("direction") == "LONG" else -1,
            "kind": kind,
            "atr1m_pct": atr1m / entry * 100.0 if entry else 0.0,
            "stop_dist_pct": dist / entry * 100.0 if entry else 0.0,
            "plan_r": round(plan_r, 3),
            "leverage": float(rec.get("leverage") or 0.0),
        }
        if df is not None:
            row.update(candle_features(df, ts))
        return row
    except Exception:
        return None


def features_live(symbol: str, direction: int, strategy: str,
                  entry_model: str, entry: float, stop: float, tp: float | None,
                  atr1m: float, leverage: float, ts_ms: int,
                  kind: str, cfg: Any, df=None) -> dict:
    dist = abs(entry - stop)
    plan_r = abs(tp - entry) / dist if tp is not None and dist > 0 else 0.0
    row = {
        "hour_utc": (ts_ms // 3_600_000) % 24,
        "dow": ((ts_ms // 86_400_000) + 3) % 7,
        "session": session_label(ts_ms, cfg),
        "strategy": strategy or "?",
        "entry_model": entry_model or "?",
        "direction": 1 if direction > 0 else -1,
        "kind": kind or "?",
        "atr1m_pct": atr1m / entry * 100.0 if entry else 0.0,
        "stop_dist_pct": dist / entry * 100.0 if entry else 0.0,
        "plan_r": round(plan_r, 3),
        "leverage": float(leverage or 0.0),
    }
    if df is not None:
        row.update(candle_features(df, ts_ms))
    return row


class Brain:
    """Loads the trained model and scores candidate entries."""

    def __init__(self, data_dir: Path, inv_cfg: dict | None = None):
        self.data_dir = Path(data_dir)
        self.model_path = self.data_dir / "state" / "brain.pkl"
        self.meta_path = self.data_dir / "state" / "brain_meta.json"
        self.inv_cfg = inv_cfg or {}
        self.model = None
        self.meta: dict = {}
        self._recent: deque[bool] = deque(maxlen=20)   # True = taken
        self._load()

    # ------------------------------------------------------------ state
    def _load(self) -> None:
        if self.model_path.exists():
            try:
                with open(self.model_path, "rb") as fh:
                    self.model = pickle.load(fh)
            except Exception:
                self.model = None
        if self.meta_path.exists():
            try:
                self.meta = json.loads(self.meta_path.read_text())
            except Exception:
                self.meta = {}

    def ready(self) -> bool:
        return (self.model is not None
                and (self.meta.get("n_train") or 0)
                >= int(self.inv_cfg.get("min_train_trades", 300)))

    def n_train(self) -> int:
        return int(self.meta.get("n_train") or 0)

    # ------------------------------------------------------------ scoring
    def _row(self, feats: dict):
        import pandas as pd
        row = {f: feats.get(f) for f in FEATURES}
        for f in FEATURES:
            if row[f] is None:
                row[f] = 0 if f not in CAT_FEATURES else "?"
        df = pd.DataFrame([row], columns=FEATURES)
        for f in CAT_FEATURES:
            df[f] = df[f].astype("category")
        return df

    def win_prob(self, feats: dict) -> float | None:
        if not self.ready():
            return None
        try:
            probs = self.model.predict_proba(self._row(feats))[0]
            return float(probs[1])
        except Exception:
            return None

    def should_take(self, feats: dict, log=print) -> tuple[bool, float | None]:
        """The gate: veto entries the brain scores below the bar, but never
        freeze the book -- at most (1 - min_keep_frac) of recent signals."""
        p = self.win_prob(feats)
        if p is None:
            return True, None
        veto_bar = float(self.inv_cfg.get("veto_prob", 0.40))
        keep_frac = float(self.inv_cfg.get("min_keep_frac", 0.35))
        if p >= veto_bar:
            self._recent.append(True)
            return True, p
        vetoed = sum(1 for t in self._recent if not t)
        if len(self._recent) >= 10 and vetoed / max(len(self._recent), 1) >= (1.0 - keep_frac):
            # the brain is allowed to filter, not to freeze
            self._recent.append(True)
            return True, p
        self._recent.append(False)
        return False, p


# ---------------------------------------------------------------- training
def build_dataset(records: list[dict], frames: dict, cfg: Any) -> tuple[list, list]:
    """(X rows, y labels) from trade records + coin kinds.  Rows with any
    missing feature are skipped; wins are pnl > 0."""
    xs, ys = [], []
    for rec in records:
        sym = rec.get("symbol") or ""
        ts = int(rec.get("opened_ms") or 0)
        kind = kind_of(sym, frames, ts)
        df = frames.get(sym)
        row = features_from_record(rec, kind, cfg, df=df)
        if row is None:
            continue
        xs.append(row)
        ys.append(1 if (rec.get("pnl") or 0) > 0 else 0)
    return xs, ys


def train(records: list[dict], frames: dict, cfg: Any,
          meta_out: Path | None = None, log=print) -> dict:
    """Train the win-probability brain with a strict time split: the last
    20% of samples are the exam, never the lesson."""
    import pandas as pd
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score
    xs, ys = build_dataset(records, frames, cfg)
    n = len(ys)
    log(f"brain: {n} labeled trades")
    if n < 100:
        return {"ok": False, "why": f"only {n} samples", "n": n}
    order = sorted(range(n), key=lambda i: records[i].get("opened_ms") or 0)
    cut = int(n * 0.8)
    tr_idx, va_idx = order[:cut], order[cut:]
    # embargo (walk-forward discipline, Lopez de Prado): trades near the
    # split share the same market regime as the exam -- drop them from the
    # lesson set so autocorrelation cannot leak across the boundary
    if tr_idx:
        split_ts = records[order[cut]]["opened_ms"] if cut < n else None
        if split_ts:
            embargo_ms = 4 * 3_600_000
            kept = [i for i in tr_idx
                    if (records[i].get("opened_ms") or 0) < split_ts - embargo_ms]
            if len(kept) >= 60:
                tr_idx = kept

    X = pd.DataFrame([{f: (r.get(f) if r.get(f) is not None
                           else ("?" if f in CAT_FEATURES else 0.0))
                       for f in FEATURES} for r in xs], columns=FEATURES)
    for f in FEATURES:
        if f not in CAT_FEATURES:
            X[f] = X[f].astype(float)
        else:
            X[f] = X[f].astype("category")
    y = np.asarray(ys, dtype=int)

    model = HistGradientBoostingClassifier(
        max_iter=200, max_depth=4, min_samples_leaf=20,
        learning_rate=0.06, categorical_features=CAT_FEATURES,
        early_stopping=True, validation_fraction=0.15, random_state=42)
    model.fit(X.iloc[tr_idx], y[tr_idx])

    probs = model.predict_proba(X.iloc[va_idx])[:, 1]
    yv = y[va_idx]
    base_wr = float(yv.mean())
    auc = roc_auc_score(yv, probs) if len(set(yv)) > 1 else 0.5
    # lift at a few bars: win rate of the kept set vs everyone
    lifts = {}
    for bar in (0.40, 0.45, 0.50, 0.55):
        keep = probs >= bar
        if keep.sum() >= 5:
            lifts[str(bar)] = {"kept_pct": round(100.0 * keep.mean(), 1),
                               "wr": round(float(yv[keep].mean()), 3)}
    try:
        from sklearn.inspection import permutation_importance
        pi = permutation_importance(model, X.iloc[va_idx], yv,
                                    n_repeats=10, random_state=42)
        imps = {f: float(pi.importances_mean[i])
                for i, f in enumerate(FEATURES)}
    except Exception:
        imps = {}
    meta = {"n_train": int(len(tr_idx)), "n_valid": int(len(va_idx)),
            "base_wr": round(base_wr, 3), "auc": round(auc, 3),
            "lifts": lifts, "features": FEATURES,
            "importances": imps,
            "trained_ms": int(time.time() * 1000)}
    out = {"ok": True, **meta}
    if meta_out is not None:
        meta_out.parent.mkdir(parents=True, exist_ok=True)
        # the Brain loader reads state/brain.pkl -- keep the model name
        # aligned with the loader, never with meta_out's suffix
        (meta_out.parent / "brain.pkl").write_bytes(pickle.dumps(model))
        meta_out.write_text(json.dumps(meta, indent=1))
        log(f"brain: saved model, {len(tr_idx)} train / {len(va_idx)} exam, "
            f"base wr {base_wr:.0%}, AUC {auc:.2f}")
    return out
