"""
quant/hft/models/volatility.py
================================
Volatility estimation for HFT: GARCH(1,1) + Jump-detection (Merton).

GARCH(1,1):
    sigma^2_t = omega + alpha * r^2_{t-1} + beta * sigma^2_{t-1}

where r_t = log(P_t / P_{t-1}) are log-returns.

Stationarity: alpha + beta < 1
Long-run variance: sigma^2_LR = omega / (1 - alpha - beta)

Merton Jump Detection:
    At each bar, test if |r_t| > jump_threshold * sigma_t
    (where jump_threshold ≈ 3.0 sigma events are classified as jumps)
    Volatility cluster = window where jump-rate > baseline.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Deque

import numpy as np

logger = logging.getLogger(__name__)

_EPS = 1e-12


@dataclass
class VolatilityState:
    sigma: float           # current conditional std (fractional, per-bar)
    sigma_annual: float    # annualised sigma (sigma * sqrt(252*24*60) for 1m bars)
    is_jump: bool          # True if latest return was a jump event
    jump_rate: float       # fraction of recent bars flagged as jumps
    cluster_active: bool   # True if in a volatility cluster window


class GARCH11:
    """
    Online GARCH(1,1) estimator with real-time jump detection.
    Uses a warm-up buffer before producing reliable estimates.
    """

    def __init__(
        self,
        omega: float = 1e-6,
        alpha: float = 0.10,
        beta: float = 0.85,
        min_periods: int = 30,
        jump_threshold: float = 3.0,
        cluster_window: int = 20,
        cluster_jump_rate: float = 0.15,
        bars_per_year: float = 525_600.0,  # 1-minute bars in a year
    ) -> None:
        assert alpha + beta < 1.0, "GARCH must be stationary: alpha + beta < 1"
        self.omega = omega
        self.alpha = alpha
        self.beta = beta
        self.min_periods = min_periods
        self.jump_threshold = jump_threshold
        self.cluster_window = cluster_window
        self.cluster_jump_rate = cluster_jump_rate
        self.bars_per_year = bars_per_year

        self._sigma2: float = omega / max(1 - alpha - beta, _EPS)
        self._prev_close: float | None = None
        self._returns: Deque[float] = deque(maxlen=min_periods * 2)
        self._jump_flags: Deque[bool] = deque(maxlen=cluster_window)
        self._n: int = 0

    @property
    def sigma(self) -> float:
        return float(np.sqrt(max(self._sigma2, _EPS)))

    @property
    def sigma_annualised(self) -> float:
        return self.sigma * float(np.sqrt(self.bars_per_year))

    def update(self, close: float) -> VolatilityState:
        """
        Feed a new close price. Returns updated VolatilityState.
        """
        if self._prev_close is None or self._prev_close <= 0:
            self._prev_close = close
            return VolatilityState(
                sigma=self.sigma,
                sigma_annual=self.sigma_annualised,
                is_jump=False,
                jump_rate=0.0,
                cluster_active=False,
            )

        r = np.log(close / self._prev_close)
        self._prev_close = close
        self._returns.append(r)
        self._n += 1

        # GARCH(1,1) variance update
        self._sigma2 = (
            self.omega
            + self.alpha * r ** 2
            + self.beta * self._sigma2
        )

        sigma_t = self.sigma

        # Jump detection: Merton criterion
        is_jump = abs(r) > self.jump_threshold * sigma_t
        self._jump_flags.append(is_jump)

        # Cluster detection
        jump_rate = float(np.mean(list(self._jump_flags))) if self._jump_flags else 0.0
        cluster_active = jump_rate >= self.cluster_jump_rate

        return VolatilityState(
            sigma=sigma_t,
            sigma_annual=self.sigma_annualised,
            is_jump=is_jump,
            jump_rate=jump_rate,
            cluster_active=cluster_active,
        )

    def long_run_variance(self) -> float:
        return self.omega / max(1.0 - self.alpha - self.beta, _EPS)

    def long_run_sigma(self) -> float:
        return float(np.sqrt(self.long_run_variance()))

    def is_warmed_up(self) -> bool:
        return self._n >= self.min_periods

    def fit(self, prices: np.ndarray) -> None:
        """
        Batch-fit omega, alpha, beta by minimising negative log-likelihood.
        Uses scipy optimiser; then warms up internal state.
        """
        from scipy.optimize import minimize

        returns = np.diff(np.log(prices))
        T = len(returns)

        def neg_loglik(params: np.ndarray) -> float:
            o, a, b = params
            if o <= 0 or a <= 0 or b <= 0 or a + b >= 1:
                return 1e12
            sig2 = np.zeros(T)
            sig2[0] = np.var(returns)
            for t in range(1, T):
                sig2[t] = o + a * returns[t - 1] ** 2 + b * sig2[t - 1]
            if np.any(sig2 <= 0):
                return 1e12
            ll = -0.5 * np.sum(np.log(2 * np.pi * sig2) + returns ** 2 / sig2)
            return -ll

        x0 = np.array([self.omega, self.alpha, self.beta])
        bounds = [(1e-10, None), (1e-6, 0.999), (1e-6, 0.999)]
        result = minimize(neg_loglik, x0, method="L-BFGS-B", bounds=bounds)
        if result.success:
            self.omega, self.alpha, self.beta = result.x
            logger.info("GARCH fit: omega=%.2e alpha=%.4f beta=%.4f", *result.x)

        # Warm up internal state
        for p in prices[-self.min_periods:]:
            self.update(p)
