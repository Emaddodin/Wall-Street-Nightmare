"""
quant/hft/alpha/signal_engine.py
==================================
Integrated OFI feature engineering + CatBoost micro-price direction predictor.

Feature vector (12 dims):
    [ofi_l1, ofi_l2, ofi_l3, ofi_l4, ofi_l5,
     spread_bps, vwap_dev, microprice_dev,
     bid_slope, ask_slope, hawkes_buy_lam, hawkes_sell_lam]

Signal valid when:
    confidence > 0.60  AND  spread_bps < 20  AND  buffer_ready
"""

from __future__ import annotations

import logging
import os
from collections import deque
from dataclasses import dataclass
from typing import Deque

import numpy as np

logger = logging.getLogger(__name__)

_EPS = 1e-12

try:
    from catboost import CatBoostClassifier
    _HAS_CATBOOST = True
except ImportError:
    _HAS_CATBOOST = False
    logger.warning("catboost not installed — DirectionPredictor will use heuristic fallback.")


# ---------------------------------------------------------------------------
# Signal result dataclass
# ---------------------------------------------------------------------------

@dataclass
class SignalResult:
    direction: int           # +1 = long, -1 = short, 0 = flat
    confidence: float        # probability of predicted direction [0, 1]
    ofi_vector: np.ndarray   # shape (5,)
    spread_bps: float
    hawkes_excited: bool
    hawkes_direction: str    # 'long' | 'short' | 'neutral'
    is_valid: bool
    reason: str


# ---------------------------------------------------------------------------
# Feature engine
# ---------------------------------------------------------------------------

class OFIFeatureEngine:
    """
    Maintains a rolling buffer of order-book snapshots and computes the
    12-dimensional feature vector for direction prediction.
    """

    def __init__(self, levels: int = 5, window_ticks: int = 50) -> None:
        self.levels = levels
        self.window_ticks = window_ticks
        self._bid_snaps: Deque[list[tuple[float, float]]] = deque(maxlen=window_ticks)
        self._ask_snaps: Deque[list[tuple[float, float]]] = deque(maxlen=window_ticks)
        self._mid_prices: Deque[float] = deque(maxlen=window_ticks)
        self._trade_prices: Deque[float] = deque(maxlen=window_ticks)
        self._trade_qtys: Deque[float] = deque(maxlen=window_ticks)

    def update(
        self,
        bids: list[tuple[float, float]],   # [(price, qty), ...]
        asks: list[tuple[float, float]],
        mid_price: float,
        trade_price: float | None = None,
        trade_qty: float | None = None,
    ) -> None:
        self._bid_snaps.append(bids[: self.levels])
        self._ask_snaps.append(asks[: self.levels])
        self._mid_prices.append(mid_price)
        if trade_price is not None:
            self._trade_prices.append(trade_price)
            self._trade_qtys.append(trade_qty or 0.0)

    def is_ready(self) -> bool:
        return len(self._bid_snaps) >= 2

    # ------------------------------------------------------------------
    # Per-level OFI
    # ------------------------------------------------------------------

    def compute_ofi_vector(self) -> np.ndarray:
        """
        OFI_k = (dB_k - dA_k) / (|dB_k| + |dA_k| + eps)  averaged over window.
        Returns shape (levels,).
        """
        if len(self._bid_snaps) < 2:
            return np.zeros(self.levels)

        ofi_accum = np.zeros(self.levels)
        n_pairs = 0
        snaps_b = list(self._bid_snaps)
        snaps_a = list(self._ask_snaps)
        for i in range(1, len(snaps_b)):
            prev_b, curr_b = snaps_b[i - 1], snaps_b[i]
            prev_a, curr_a = snaps_a[i - 1], snaps_a[i]
            for k in range(self.levels):
                pb = prev_b[k][1] if k < len(prev_b) else 0.0
                cb = curr_b[k][1] if k < len(curr_b) else 0.0
                pa = prev_a[k][1] if k < len(prev_a) else 0.0
                ca = curr_a[k][1] if k < len(curr_a) else 0.0
                d_bid = cb - pb
                d_ask = ca - pa
                denom = abs(d_bid) + abs(d_ask) + _EPS
                ofi_accum[k] += np.clip((d_bid - d_ask) / denom, -1.0, 1.0)
            n_pairs += 1

        return ofi_accum / max(n_pairs, 1)

    # ------------------------------------------------------------------
    # Spread impact
    # ------------------------------------------------------------------

    def compute_spread_bps(self, bids: list, asks: list) -> float:
        if not bids or not asks:
            return float("nan")
        mid = (bids[0][0] + asks[0][0]) / 2.0
        if mid < _EPS:
            return float("nan")
        spread = asks[0][0] - bids[0][0]
        return spread / mid * 10_000.0

    # ------------------------------------------------------------------
    # VWAP deviation
    # ------------------------------------------------------------------

    def compute_vwap_deviation(self) -> float:
        """
        (VWAP_recent - mid_price) / mid_price
        """
        if not self._trade_prices or not self._mid_prices:
            return 0.0
        prices = np.array(list(self._trade_prices))
        qtys = np.array(list(self._trade_qtys))
        vwap = np.dot(prices, qtys) / (qtys.sum() + _EPS)
        mid = self._mid_prices[-1]
        return (vwap - mid) / (mid + _EPS)

    # ------------------------------------------------------------------
    # Micro-price deviation from mid
    # ------------------------------------------------------------------

    def compute_microprice_dev(self, bids: list, asks: list) -> float:
        if not bids or not asks:
            return 0.0
        bid_px, bid_q = bids[0]
        ask_px, ask_q = asks[0]
        total = bid_q + ask_q + _EPS
        micro = bid_px * (ask_q / total) + ask_px * (bid_q / total)
        mid = (bid_px + ask_px) / 2.0
        return (micro - mid) / (mid + _EPS)

    # ------------------------------------------------------------------
    # LOB slope (linear regression of cumulative qty vs price)
    # ------------------------------------------------------------------

    @staticmethod
    def compute_lob_slope(levels: list[tuple[float, float]]) -> float:
        if len(levels) < 2:
            return 0.0
        prices = np.array([l[0] for l in levels])
        cum_qty = np.cumsum([l[1] for l in levels])
        if prices.std() < _EPS:
            return 0.0
        slope = np.polyfit(prices, cum_qty, 1)[0]
        return float(slope)

    # ------------------------------------------------------------------
    # Full feature vector
    # ------------------------------------------------------------------

    def get_feature_vector(
        self,
        bids: list[tuple[float, float]],
        asks: list[tuple[float, float]],
        hawkes_buy_lam: float = 0.0,
        hawkes_sell_lam: float = 0.0,
    ) -> np.ndarray:
        """
        Assemble 12-dim feature vector:
        [ofi_l1..l5, spread_bps, vwap_dev, microprice_dev,
         bid_slope, ask_slope, hawkes_buy, hawkes_sell]
        """
        ofi = self.compute_ofi_vector()
        spread = self.compute_spread_bps(bids, asks)
        vwap_dev = self.compute_vwap_deviation()
        mp_dev = self.compute_microprice_dev(bids, asks)
        bid_slope = self.compute_lob_slope(bids)
        ask_slope = self.compute_lob_slope(asks)

        feat = np.concatenate([
            ofi,
            [spread, vwap_dev, mp_dev, bid_slope, ask_slope,
             hawkes_buy_lam, hawkes_sell_lam],
        ])
        return feat.astype(np.float32)


# ---------------------------------------------------------------------------
# Direction predictor
# ---------------------------------------------------------------------------

class DirectionPredictor:
    """
    CatBoost binary classifier: +1 (price goes up) vs -1 (price goes down).
    Falls back to OFI-heuristic when model not fitted or catboost absent.
    """

    def __init__(self, model_path: str | None = None) -> None:
        self._model: "CatBoostClassifier | None" = None
        self._fitted = False
        if model_path and os.path.exists(model_path):
            self.load(model_path)

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    def predict_proba(self, features: np.ndarray) -> tuple[float, float]:
        """Returns (p_up, p_down)."""
        if self._fitted and _HAS_CATBOOST and self._model is not None:
            proba = self._model.predict_proba(features.reshape(1, -1))[0]
            return float(proba[1]), float(proba[0])
        # Heuristic fallback: use integrated OFI (mean of first 5 features)
        ofi_mean = float(np.mean(features[:5]))
        p_up = float(np.clip(0.5 + ofi_mean * 0.4, 0.01, 0.99))
        return p_up, 1.0 - p_up

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        """
        Train CatBoost on (features, labels) where labels in {0, 1}.
        Uses class_weights='balanced' to handle imbalanced datasets.
        """
        if not _HAS_CATBOOST:
            logger.warning("catboost not available — skipping fit.")
            return
        from catboost import CatBoostClassifier
        counts = np.bincount(y.astype(int))
        w = len(y) / (2.0 * counts + _EPS)
        sample_weights = np.where(y == 1, w[1], w[0])

        self._model = CatBoostClassifier(
            iterations=500,
            learning_rate=0.05,
            depth=6,
            loss_function="Logloss",
            eval_metric="AUC",
            use_best_model=True,
            early_stopping_rounds=50,
            verbose=False,
            random_seed=42,
        )
        split = int(len(X) * 0.85)
        self._model.fit(
            X[:split], y[:split],
            sample_weight=sample_weights[:split],
            eval_set=(X[split:], y[split:]),
        )
        self._fitted = True

    def save(self, path: str) -> None:
        if self._model:
            self._model.save_model(path)

    def load(self, path: str) -> None:
        if not _HAS_CATBOOST:
            return
        from catboost import CatBoostClassifier
        self._model = CatBoostClassifier()
        self._model.load_model(path)
        self._fitted = True


# ---------------------------------------------------------------------------
# Composite signal engine
# ---------------------------------------------------------------------------

class SignalEngine:
    """
    Composes OFIFeatureEngine + DirectionPredictor + MultiHawkes.
    Single call-site: await engine.generate_signal(...) -> SignalResult
    """

    CONFIDENCE_THRESHOLD = 0.60
    MAX_SPREAD_BPS = 20.0

    def __init__(
        self,
        model_path: str | None = None,
        ofi_levels: int = 5,
        window_ticks: int = 50,
    ) -> None:
        self.feature_eng = OFIFeatureEngine(levels=ofi_levels, window_ticks=window_ticks)
        self.predictor = DirectionPredictor(model_path=model_path)

    async def generate_signal(
        self,
        bids: list[tuple[float, float]],
        asks: list[tuple[float, float]],
        mid_price: float,
        hawkes_buy_lam: float = 0.5,
        hawkes_sell_lam: float = 0.5,
        hawkes_excited: bool = False,
        hawkes_direction: str = "neutral",
        trade_price: float | None = None,
        trade_qty: float | None = None,
    ) -> SignalResult:
        """Compute features and return a direction signal."""
        self.feature_eng.update(bids, asks, mid_price, trade_price, trade_qty)

        if not self.feature_eng.is_ready():
            return SignalResult(
                direction=0, confidence=0.5,
                ofi_vector=np.zeros(5), spread_bps=float("nan"),
                hawkes_excited=False, hawkes_direction="neutral",
                is_valid=False, reason="buffer_warming_up",
            )

        feat = self.feature_eng.get_feature_vector(
            bids, asks, hawkes_buy_lam, hawkes_sell_lam
        )
        spread_bps = self.feature_eng.compute_spread_bps(bids, asks)
        ofi_vec = feat[:5]

        p_up, p_down = self.predictor.predict_proba(feat)
        if p_up > p_down:
            direction, confidence = 1, p_up
        else:
            direction, confidence = -1, p_down

        valid = True
        reason = "ok"
        if confidence < self.CONFIDENCE_THRESHOLD:
            valid = False
            reason = f"low_confidence({confidence:.2f})"
        elif spread_bps > self.MAX_SPREAD_BPS:
            valid = False
            reason = f"spread_too_wide({spread_bps:.1f}bps)"

        return SignalResult(
            direction=direction,
            confidence=confidence,
            ofi_vector=ofi_vec,
            spread_bps=spread_bps,
            hawkes_excited=hawkes_excited,
            hawkes_direction=hawkes_direction,
            is_valid=valid,
            reason=reason,
        )
