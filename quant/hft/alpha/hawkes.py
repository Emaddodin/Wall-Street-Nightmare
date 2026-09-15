"""
quant/hft/alpha/hawkes.py
==========================
Hawkes Process with exponential kernel for self-exciting order-arrival modelling.

Conditional intensity:
    lambda(t) = mu + alpha * R(t)

Recursive sufficient statistic (Markov property of exponential kernel):
    At event n:
        R(t_n) = exp(-beta * (t_n - t_{n-1})) * (1 + R(t_{n-1}))
    Between events (evaluation at arbitrary t > t_last):
        R(t) = exp(-beta * (t - t_last)) * R_last

Log-likelihood for MLE:
    L = -mu*T - (alpha/beta)*sum(1 - exp(-beta*(T-t_i)))
        + sum(log(mu + alpha * R(t_i)))
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
from scipy.optimize import minimize

_EPS = 1e-12


@dataclass
class HawkesProcess:
    """
    Univariate Hawkes process with exponential excitation kernel.

    Parameters
    ----------
    mu    : baseline arrival rate (events/sec)
    alpha : excitation magnitude
    beta  : decay rate (1/sec)
    window_sec : rolling history window (seconds)
    """
    mu: float = 0.5
    alpha: float = 0.8
    beta: float = 2.0
    window_sec: float = 60.0

    _R: float = field(default=0.0, init=False, repr=False)
    _last_event_t: float = field(default=0.0, init=False, repr=False)
    _event_times: list[float] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._R = 0.0
        self._last_event_t = time.time()
        self._event_times = []

    # ------------------------------------------------------------------
    # Event recording
    # ------------------------------------------------------------------

    def update(self, timestamp: float | None = None) -> None:
        """Record a new order-arrival event."""
        t = timestamp if timestamp is not None else time.time()
        dt = t - self._last_event_t
        # Recursive update: R(t_n) = e^{-beta*dt} * (1 + R(t_{n-1}))
        self._R = np.exp(-self.beta * max(dt, 0.0)) * (1.0 + self._R)
        self._last_event_t = t
        self._event_times.append(t)
        # Prune old events outside window
        cutoff = t - self.window_sec
        self._event_times = [ts for ts in self._event_times if ts >= cutoff]

    # ------------------------------------------------------------------
    # Intensity queries
    # ------------------------------------------------------------------

    @property
    def intensity(self) -> float:
        """Current conditional intensity lambda(t_now)."""
        now = time.time()
        dt = max(now - self._last_event_t, 0.0)
        r_now = np.exp(-self.beta * dt) * self._R
        return self.mu + self.alpha * r_now

    def intensity_at(self, t: float) -> float:
        """Evaluate lambda at an arbitrary time t >= last_event_t."""
        dt = max(t - self._last_event_t, 0.0)
        r_t = np.exp(-self.beta * dt) * self._R
        return self.mu + self.alpha * r_t

    def is_excited(self, threshold_multiplier: float = 2.0) -> bool:
        """True if current intensity exceeds threshold_multiplier * mu."""
        return self.intensity > threshold_multiplier * self.mu

    def excitation_probability(self, horizon_sec: float = 5.0) -> float:
        """
        P(at least one event in [now, now+horizon]) under Poisson approximation:
            P = 1 - exp(-lambda_now * horizon)
        """
        return 1.0 - np.exp(-self.intensity * horizon_sec)

    # ------------------------------------------------------------------
    # MLE fitting
    # ------------------------------------------------------------------

    def fit(self, timestamps: np.ndarray) -> None:
        """
        Fit mu, alpha, beta by maximum likelihood on historical event times.

        Log-likelihood (Ozaki 1979):
            L = -mu*T - (alpha/beta)*sum_i(1 - exp(-beta*(T-t_i)))
                + sum_i log(mu + alpha*R_i)
        """
        timestamps = np.sort(timestamps).astype(np.float64)
        T = timestamps[-1] - timestamps[0]
        t = timestamps - timestamps[0]

        def neg_loglik(params: np.ndarray) -> float:
            mu_, alpha_, beta_ = params
            if mu_ <= 0 or alpha_ <= 0 or beta_ <= 0:
                return 1e12
            if alpha_ >= beta_:  # stationarity
                return 1e12

            R_ = 0.0
            log_liks = []
            prev_t = 0.0
            for ti in t:
                dt = ti - prev_t
                R_ = np.exp(-beta_ * dt) * (1.0 + R_)
                lam = mu_ + alpha_ * R_
                log_liks.append(np.log(max(lam, _EPS)))
                prev_t = ti

            integral = mu_ * T + (alpha_ / beta_) * np.sum(
                1.0 - np.exp(-beta_ * (T - t))
            )
            return -(np.sum(log_liks) - integral)

        x0 = np.array([self.mu, self.alpha, self.beta])
        bounds = [(1e-6, None), (1e-6, None), (1e-6, None)]
        result = minimize(neg_loglik, x0, method="L-BFGS-B", bounds=bounds)
        if result.success:
            self.mu, self.alpha, self.beta = result.x
            self._R = 0.0  # reset running state

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"<HawkesProcess mu={self.mu:.4f} alpha={self.alpha:.4f} "
            f"beta={self.beta:.4f} lambda={self.intensity:.4f}>"
        )


class MultiHawkes:
    """
    Pair of Hawkes processes for buy-side and sell-side market orders.

    Used to detect directional liquidity clustering.
    """

    def __init__(
        self,
        mu: float = 0.5,
        alpha: float = 0.8,
        beta: float = 2.0,
    ) -> None:
        self.buy = HawkesProcess(mu=mu, alpha=alpha, beta=beta)
        self.sell = HawkesProcess(mu=mu, alpha=alpha, beta=beta)

    def update(self, side: str, timestamp: float | None = None) -> None:
        """Record a market order. side must be 'buy' or 'sell'."""
        if side.lower() in ("buy", "b", "long"):
            self.buy.update(timestamp)
        else:
            self.sell.update(timestamp)

    @property
    def buy_intensity(self) -> float:
        return self.buy.intensity

    @property
    def sell_intensity(self) -> float:
        return self.sell.intensity

    def net_imbalance_excited(self, ratio_threshold: float = 1.5) -> tuple[bool, str]:
        """
        Returns (True, direction) if one side dominates beyond ratio_threshold.
        direction: 'long' or 'short'
        """
        lam_b = max(self.buy_intensity, _EPS)
        lam_s = max(self.sell_intensity, _EPS)
        if lam_b / lam_s >= ratio_threshold:
            return True, "long"
        if lam_s / lam_b >= ratio_threshold:
            return True, "short"
        return False, "neutral"

    def both_excited(self, multiplier: float = 2.0) -> bool:
        """True if both sides are in an excited regime (high activity)."""
        return self.buy.is_excited(multiplier) and self.sell.is_excited(multiplier)
