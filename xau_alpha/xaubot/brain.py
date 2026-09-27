"""
xau_alpha/xaubot/brain.py - leak-free rewrite of the XAUBot AI "brain" (upstream: aditisstillalive/xau-ai-trading-bot,
spec: PORT_SPEC.md). Used as a GATE on our own entries/exits (cand/shift_stack.py), not as an entry system.

Kept from upstream (PORT_SPEC sections 2, 4.1/4.2, 5): V1 feature set, Model D label (k=3 M15 bars, 0.3*ATR), XGBoost params,
3-state Gaussian HMM on the 8 regime inputs, LOW/MEDIUM/HIGH mapping by scaled vol20.
Fixed (PORT_SPEC sections 1.5, 3.3, 2.4, 5.3, 9): features use COMPLETED M15 bars only; raw price levels replaced by scale-free
versions; H1 inputs taken from the last COMPLETED H1 bar; the HMM is fitted on the training period and each bar's regime is
decoded from a trailing window that ends at that bar (no forward-backward over future rows); no order-block back-fill.
"""
import numpy as np
import pandas as pd

from data import atr as wilder_atr, resample_causal
from features import ema

XGB_PARAMS = dict(objective="binary:logistic", eval_metric="auc", max_depth=3, learning_rate=0.05, tree_method="hist",
                  min_child_weight=10, subsample=0.7, colsample_bytree=0.6, reg_alpha=1.0, reg_lambda=5.0, gamma=1.0,
                  max_delta_step=1, seed=42)


def _rsi(c, n=14):
    d = np.diff(c, prepend=c[0])
    g, l = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
    ag = pd.Series(g).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().values
    al = pd.Series(l).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().values
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(al == 0, 100.0, 100 - 100 / (1 + ag / al))


def m15_features(m1: pd.DataFrame):
    """Returns (M15 frame with features, known) where known[i] = last complete M15 bar at M1 index i."""
    M, known = resample_causal(m1, 15)
    o, h, l, c = (M[x].values for x in "ohlc")
    n_ticks = m1.groupby(m1["ts"].values // 900_000)["n"].sum().reindex(M["ts_open"].values // 900_000).values
    A = wilder_atr(M)
    f = pd.DataFrame(index=M.index)
    f["rsi"] = _rsi(c)
    f["atr_percent"] = A / c * 100
    macd = ema(c, 12) - ema(c, 26)
    f["macd_n"] = macd / A
    f["macd_hist_n"] = (macd - ema(macd, 9)) / A
    m20 = pd.Series(c).rolling(20).mean().values
    s20 = pd.Series(c).rolling(20).std().values
    f["bb_percent_b"] = (c - (m20 - 2 * s20)) / (4 * s20)
    f["bb_width"] = 4 * s20 / m20
    e9, e21 = ema(c, 9), ema(c, 21)
    f["ema_gap_n"] = (e9 - e21) / A
    above = e9 > e21
    prev = np.r_[False, above[:-1]]
    f["ema_cross_bull"] = (above & ~prev).astype(int)
    f["ema_cross_bear"] = (~above & prev).astype(int)
    for k in (1, 5, 20):
        f[f"returns_{k}"] = pd.Series(c).pct_change(k).values
    lr = np.log(c / np.r_[c[0], c[:-1]])
    f["volatility_20"] = pd.Series(lr).rolling(20).std().values
    f["normalized_range"] = (h - l) / c
    f["avg_normalized_range"] = pd.Series((h - l) / c).rolling(14).mean().values
    f["price_position"] = (c - l) / np.maximum(h - l, 1e-9)
    f["dist_from_sma_20"] = c / m20 - 1
    hh, ll = h > np.r_[np.nan, h[:-1]], l < np.r_[np.nan, l[:-1]]
    f["hh_count_5"] = pd.Series(hh.astype(float)).rolling(5).sum().values
    f["ll_count_5"] = pd.Series(ll.astype(float)).rolling(5).sum().values
    vr = n_ticks / pd.Series(n_ticks).rolling(20).mean().values
    f["volume_ratio"] = vr
    hr = pd.to_datetime(M["ts_open"], unit="ms", utc=True).dt.hour.values
    f["hour_sin"], f["hour_cos"] = np.sin(2 * np.pi * hr / 24), np.cos(2 * np.pi * hr / 24)
    f["weekday"] = pd.to_datetime(M["ts_open"], unit="ms", utc=True).dt.dayofweek.values
    f["london"] = ((hr >= 8) & (hr < 16)).astype(int)
    f["ny"] = ((hr >= 13) & (hr < 21)).astype(int)
    # SMC (causal): confirmed swings (5 bars late), BOS = close beyond the last confirmed swing, FVG on the 3rd candle
    k = 5
    hmax = pd.Series(h).rolling(2 * k + 1, center=True).max().values
    lmin = pd.Series(l).rolling(2 * k + 1, center=True).min().values
    sh = pd.Series(np.where(h == hmax, h, np.nan)).shift(k).ffill().values    # known k bars later
    sl = pd.Series(np.where(l == lmin, l, np.nan)).shift(k).ffill().values
    f["dist_sh_n"] = (sh - c) / A
    f["dist_sl_n"] = (c - sl) / A
    f["bos"] = np.where(c > sh, 1, np.where(c < sl, -1, 0))
    f["fvg_signal"] = np.where(l > np.r_[np.nan, np.nan, h[:-2]], 1, np.where(h < np.r_[np.nan, np.nan, l[:-2]], -1, 0))
    f["fvg_recent"] = pd.Series(f["fvg_signal"]).rolling(10, min_periods=1).sum().values
    # V2 extras that are causal
    f["volatility_zscore"] = (A - pd.Series(A).rolling(50, min_periods=10).mean().values) / pd.Series(A).rolling(50, min_periods=10).std().values
    f["wick_ratio"] = (np.abs(np.maximum(o, c) - h) + np.abs(l - np.minimum(o, c))) / np.maximum(h - l, 1e-9)
    f["body_ratio"] = np.abs(c - o) / np.maximum(h - l, 1e-9)
    # H1 context from the last COMPLETE H1 bar (upstream joined the forming H1 bar = look-ahead)
    H, kH = resample_causal(m1, 60)
    cH = H["c"].values
    AH = wilder_atr(H)
    m15_last_i = M["last_i"].values                      # M1 index of each M15 bar's last minute
    jH = kH[np.minimum(m15_last_i, len(kH) - 1)]
    ok = jH >= 0
    jj = np.maximum(jH, 0)
    f["h1_rsi"] = np.where(ok, _rsi(cH)[jj], np.nan)
    f["h1_ema20_dist_n"] = np.where(ok, (c - ema(cH, 20)[jj]) / A, np.nan)
    f["h1_atr_ratio"] = np.where(ok, AH[jj] / A, np.nan)
    M = M.assign(A=A)
    return M, known, f


REGIME_COLS = ["lr", "vol20", "vol100", "range_atr", "trend_strength", "rsi_dev", "autocorr", "vol_regime"]


def regime_inputs(M: pd.DataFrame) -> pd.DataFrame:
    c, h, l, A = M["c"].values, M["h"].values, M["l"].values, M["A"].values
    lr = np.log(c / np.r_[c[0], c[:-1]])
    s = pd.Series(lr)
    gain = pd.Series(np.where(np.diff(c, prepend=c[0]) > 0, np.diff(c, prepend=c[0]), 0)).rolling(14).mean()
    loss = pd.Series(np.where(np.diff(c, prepend=c[0]) < 0, -np.diff(c, prepend=c[0]), 0)).rolling(14).mean()
    rsi_c = 100 - 100 / (1 + gain / loss)
    return pd.DataFrame({
        "lr": lr, "vol20": s.rolling(20).std(), "vol100": s.rolling(100).std(), "range_atr": (h - l) / A,
        "trend_strength": (pd.Series(c).rolling(9).mean() - pd.Series(c).rolling(21).mean()).abs() / A,
        "rsi_dev": (rsi_c - 50).abs() / 50, "autocorr": (s * s.shift()).rolling(20).mean(),
        "vol_regime": (pd.Series(A) - pd.Series(A).rolling(100).mean()) / pd.Series(A).rolling(100).std(),
    }).replace([np.inf, -np.inf], np.nan)


class Brain:
    """Fit on a training slice of M1 bars (ts < train_end_ms), then score any later bar causally."""

    def __init__(self, thr=0.65):
        self.thr = thr

    def fit(self, m1: pd.DataFrame, train_end_ms: int):
        import xgboost as xgb
        from hmmlearn.hmm import GaussianHMM
        from sklearn.preprocessing import StandardScaler
        M, known, f = m15_features(m1)
        tr = M["ts_open"].values < train_end_ms
        # --- HMM regime (upstream 5.2), fitted on training rows only
        R = regime_inputs(M)
        Rtr = R[tr].dropna()
        self.scaler = StandardScaler().fit(Rtr.values)
        best = None
        for k in range(5):
            mdl = GaussianHMM(n_components=3, covariance_type="diag", min_covar=1e-2, n_iter=200, tol=1e-4,
                              random_state=42 + 17 * k)
            try:
                mdl.fit(self.scaler.transform(Rtr.values))
                sc = mdl.score(self.scaler.transform(Rtr.values))
            except Exception:
                continue
            if best is None or sc > best[0]:
                best = (sc, mdl)
        self.hmm = best[1]
        order = np.argsort(self.hmm.means_[:, REGIME_COLS.index("vol20")])     # low, medium, high
        self.state_rank = np.empty(3, dtype=int)
        self.state_rank[order] = np.arange(3)
        f["regime"] = self.regimes(R)
        # --- label (Model D): up/down move > 0.3*ATR within the next 3 closes
        c, A = M["c"].values, M["A"].values
        fut = np.vstack([np.r_[c[j:], [np.nan] * j] for j in (1, 2, 3)])
        up, dn = np.nanmax(fut, 0) - c, c - np.nanmin(fut, 0)
        th = 0.3 * A
        y = np.where((up > th) & (up > dn), 1, np.where((dn > th) & (dn > up), 0, np.nan))
        self.cols = list(f.columns)
        X = f.replace([np.inf, -np.inf], np.nan).values.astype(float)
        sel = tr & np.isfinite(y)
        n = sel.sum()
        idx = np.flatnonzero(sel)
        cut = idx[int(0.8 * n)]
        a, b = idx[idx < cut - 50], idx[idx >= cut]          # chronological 80/20 with a 50-bar gap (upstream 4.2)
        dtr = xgb.DMatrix(np.nan_to_num(X[a]), label=y[a], feature_names=self.cols)
        dva = xgb.DMatrix(np.nan_to_num(X[b]), label=y[b], feature_names=self.cols)
        self.model = xgb.train(XGB_PARAMS, dtr, num_boost_round=100, evals=[(dva, "val")],
                               early_stopping_rounds=10, verbose_eval=False)
        from sklearn.metrics import roc_auc_score
        self.auc_val = roc_auc_score(y[b], self.model.predict(dva))
        return self

    def regimes(self, R: pd.DataFrame, win=200) -> np.ndarray:
        """Causal regime: decode a trailing window ending at each bar and keep only its last state."""
        X = self.scaler.transform(np.nan_to_num(R.values, nan=0.0, posinf=3, neginf=-3))
        out = np.full(len(X), 1, dtype=int)
        step = 4                                        # re-decode every 4 bars (1 h); carry forward between
        last = 1
        for t in range(win, len(X)):
            if t % step == 0:
                st = self.hmm.predict(X[t - win + 1:t + 1])[-1]
                last = int(self.state_rank[st])
            out[t] = last
        return out

    def score(self, m1: pd.DataFrame):
        """Per M15 bar: p_up, regime (0 low, 1 medium, 2 high). Returns (M, known, p_up, regime)."""
        import xgboost as xgb
        M, known, f = m15_features(m1)
        f["regime"] = self.regimes(regime_inputs(M))
        X = np.nan_to_num(f[self.cols].replace([np.inf, -np.inf], np.nan).values.astype(float))
        p_up = self.model.predict(xgb.DMatrix(X, feature_names=self.cols))
        return M, known, p_up, f["regime"].values
