"""
engine/monte_carlo_scaling.py
=============================
Walk-Forward Backtesting & Monte Carlo Scaling Harness ($65 -> $10,000).

Architectural Invariants & Mathematical Principles:
1. Starting & Target Capital:
   - Initial equity: $65.00 micro-account.
   - Target equity: $10,000.00.
2. Leverage & Margin Ceiling:
   - Leverage: 100x (Hyperliquid GOLD perpetual contract maximum).
   - Margin ceiling: Initial margin strictly capped at <= 20% equity ($13.00 max on $65).
3. Dynamic Compounding Formula:
   - Basket size: S(E) = max(0.45, round_down(0.18 * E * 100 / P, 2))
     where E = account equity ($), P = GOLD price ($/oz, typically ~$2,500), 100 = leverage.
   - 3 slices with 50ms jitter delay model (0ms, 50ms, 100ms).
   - Risk strictly < 0.86% equity (and dollar risk < 1.0% equity).
4. Realistic Friction Modeling:
   - Hyperliquid fees: 3.5 bps taker fee (0.00035), -0.2 bps maker rebate (-0.00002).
   - Slippage: 0.5 to 1.5 pips ($0.05 to $0.15 per oz) adverse execution slippage.
   - Funding: 1-hour Hyperliquid funding rates applied over position holding duration.
5. 5% Daily Max Drawdown Killswitch:
   - Tracks peak equity per UTC day.
   - Unconditionally halts new trades for the remainder of the day if daily drawdown >= 5%.
   - Automatically resets peak equity and resumes trading on UTC day rollover (00:00 UTC).
6. Monte Carlo Permutation Engine:
   - Simulates N permutations (default 1,000) over historical or synthetic ICT trade series.
   - Evaluates probability of reaching $10,000, probability of ruin, median time/trades to target,
     max drawdown distribution (percentiles), and annualized Sharpe ratio.
7. CLI Support:
   - Direct execution via `python3 engine/monte_carlo_scaling.py --simulations 1000 --trades 500`.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import random
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from engine.fsm import DAY_MS, DailyDrawdownGuard
except ImportError:
    DAY_MS = 86_400_000

    class DailyDrawdownGuard:  # type: ignore[no-redef]
        """Fallback DailyDrawdownGuard if engine.fsm is not directly on path."""

        def __init__(self, max_drawdown_pct: float = 0.05) -> None:
            self.max_drawdown_pct = max_drawdown_pct
            self._current_day: int = 0
            self._peak_day_equity: float = 0.0
            self._is_tripped: bool = False

        def update(
            self, current_equity: float, current_ts: Optional[float] = None
        ) -> Tuple[bool, float]:
            ts = current_ts if current_ts is not None else time.time()
            day_key = int(ts * 1000 // DAY_MS) * DAY_MS

            if day_key != self._current_day:
                self._current_day = day_key
                self._peak_day_equity = current_equity
                self._is_tripped = False

            if current_equity > self._peak_day_equity:
                self._peak_day_equity = current_equity

            if self._peak_day_equity > 0:
                drawdown_pct = (
                    self._peak_day_equity - current_equity
                ) / self._peak_day_equity
            else:
                drawdown_pct = 0.0

            if drawdown_pct >= self.max_drawdown_pct:
                self._is_tripped = True

            return self._is_tripped, drawdown_pct

logger = logging.getLogger("monte_carlo_scaling")

# -------------------------------------------------------------------------
# Architectural Constants
# -------------------------------------------------------------------------

DEFAULT_STARTING_EQUITY: float = 65.00
DEFAULT_TARGET_EQUITY: float = 10_000.00
DEFAULT_RUIN_EQUITY: float = 13.00
DEFAULT_GOLD_PRICE: float = 2500.00
DEFAULT_LEVERAGE: float = 100.0
MAX_MARGIN_PCT: float = 0.20  # 20% margin ceiling
MAX_RISK_PCT: float = 0.0086  # Strictly < 0.86% risk ceiling
MIN_BASKET_SIZE: float = 0.45  # Minimum basket size in oz (3 slices of 0.15)
SLICE_COUNT: int = 3
SLICE_JITTER_MS: int = 50
TAKER_FEE_RATE: float = 0.00035  # 3.5 bps Hyperliquid taker fee
MAKER_FEE_RATE: float = -0.00002  # -0.2 bps Hyperliquid maker rebate
PIP_SIZE: float = 0.10  # 1 pip in Gold = $0.10
MIN_SLIPPAGE_PIPS: float = 0.5  # 0.5 pip = $0.05
MAX_SLIPPAGE_PIPS: float = 1.5  # 1.5 pip = $0.15
DEFAULT_1H_FUNDING_RATE: float = 0.0000125  # 0.00125% per hour (~1.25 bps / 8h)
MAX_DAILY_DRAWDOWN_PCT: float = 0.05  # 5% daily drawdown killswitch


class SimulationDailyDrawdownGuard:
    """Fast, silent daily drawdown guard for Monte Carlo scaling simulation."""

    def __init__(self, max_drawdown_pct: float = MAX_DAILY_DRAWDOWN_PCT) -> None:
        self.max_drawdown_pct = max_drawdown_pct
        self._current_day: int = 0
        self._peak_day_equity: float = 0.0
        self._is_tripped: bool = False

    def update(
        self, current_equity: float, current_ts: Optional[float] = None
    ) -> Tuple[bool, float]:
        ts = current_ts if current_ts is not None else time.time()
        day_key = int(ts * 1000 // DAY_MS) * DAY_MS

        if day_key != self._current_day:
            self._current_day = day_key
            self._peak_day_equity = current_equity
            self._is_tripped = False

        if current_equity > self._peak_day_equity:
            self._peak_day_equity = current_equity

        if self._peak_day_equity > 0:
            drawdown_pct = (
                self._peak_day_equity - current_equity
            ) / self._peak_day_equity
        else:
            drawdown_pct = 0.0

        if drawdown_pct >= self.max_drawdown_pct:
            self._is_tripped = True

        return self._is_tripped, drawdown_pct

    @property
    def is_tripped(self) -> bool:
        return self._is_tripped
DAY_SECONDS: int = 86_400


# -------------------------------------------------------------------------
# Mathematical Utilities & Invariant Calculations
# -------------------------------------------------------------------------


def round_down(val: float, decimals: int = 2) -> float:
    """
    Deterministically truncate / round down float to specified decimal places,
    guarding against floating point epsilon representation artifacts.
    """
    factor = 100.0 if decimals == 2 else 10.0**decimals
    return math.floor(val * factor + 1e-9) / factor


def calculate_basket_size(
    equity: float,
    price: float = DEFAULT_GOLD_PRICE,
    leverage: float = DEFAULT_LEVERAGE,
    min_size: float = MIN_BASKET_SIZE,
    max_margin_pct: float = MAX_MARGIN_PCT,
    max_risk_pct: float = MAX_RISK_PCT,
    sl_distance: float = 1.15,
) -> float:
    """
    Dynamic compounding formula:
        S(E) = max(0.45, round_down(0.18 * E * 100 / P, 2))

    Guarantees:
    1. Initial margin strictly <= 20% of account equity.
    2. Dollar risk strictly < 0.86% of account equity (at standard SL distance).
    3. Position is divisible into 3 micro-unit slices.
    """
    if equity <= 0 or price <= 0:
        return 0.0

    # Primary dynamic compounding calculation
    raw_compounded = (0.18 * equity * leverage) / price
    compounded_size = math.floor(raw_compounded * 100.0 + 1e-9) / 100.0
    candidate_size = max(min_size, compounded_size)

    # Invariant 1: Margin Ceiling: initial margin strictly <= max_margin_pct * equity
    # Required Margin = (size * price) / leverage
    # Therefore: size <= (max_margin_pct * equity * leverage) / price
    max_size_by_margin = math.floor((max_margin_pct * equity * leverage) / price * 100.0 + 1e-9) / 100.0
    if candidate_size > max_size_by_margin:
        candidate_size = max_size_by_margin

    # Invariant 2: Risk Ceiling: risk strictly < max_risk_pct * equity
    # Dollar Risk = size * sl_distance
    # Therefore: size < (max_risk_pct * equity) / sl_distance
    if sl_distance > 0:
        max_size_by_risk = math.floor((max_risk_pct * equity) / sl_distance * 100.0 + 1e-9) / 100.0
        if candidate_size > max_size_by_risk:
            candidate_size = max_size_by_risk

    return round(candidate_size, 2)


def calculate_margin_required(
    size: float,
    price: float = DEFAULT_GOLD_PRICE,
    leverage: float = DEFAULT_LEVERAGE,
) -> float:
    """Calculate initial margin requirement on Hyperliquid CLOB."""
    if leverage <= 0:
        return 0.0
    return round((size * price) / leverage, 4)


def validate_margin_invariant(
    equity: float,
    size: float,
    price: float = DEFAULT_GOLD_PRICE,
    leverage: float = DEFAULT_LEVERAGE,
    max_margin_pct: float = MAX_MARGIN_PCT,
) -> bool:
    """
    Validate that initial margin is strictly <= max_margin_pct (20%) of equity.
    """
    if equity <= 0:
        return False
    margin = calculate_margin_required(size, price, leverage)
    max_allowed = equity * max_margin_pct
    return margin <= (max_allowed + 1e-6)


def calculate_dollar_risk(size: float, sl_distance: float) -> float:
    """Calculate dollar risk per trade = size * sl_distance."""
    return round(size * sl_distance, 4)


def slice_basket(
    basket_size: float, num_slices: int = SLICE_COUNT
) -> List[float]:
    """
    Partition basket size into num_slices (default 3) micro-units,
    ensuring sum(slices) == basket_size with 2-decimal precision.
    """
    if num_slices <= 1:
        return [round(basket_size, 2)]

    base_slice = math.floor(basket_size / num_slices * 100.0 + 1e-9) / 100.0
    remainder = round(basket_size - (num_slices - 1) * base_slice, 2)
    return [base_slice] * (num_slices - 1) + [remainder]


# -------------------------------------------------------------------------
# Trade & Execution Data Models
# -------------------------------------------------------------------------


@dataclass
class SliceExecutionResult:
    """Telemetry for an individual sliced ticket dispatch."""

    slice_index: int
    delay_ms: int
    sz: float
    fill_price: float
    slippage_pips: float
    fee: float


@dataclass
class TradeResult:
    """Execution telemetry and net accounting for a completed trade."""

    trade_id: str
    timestamp: float
    is_buy: bool
    basket_size: float
    entry_price: float
    exit_price: float
    sl_distance: float
    r_multiple: float
    holding_duration_hours: float
    gross_pnl: float
    entry_fee: float
    exit_fee: float
    funding_fee: float
    net_pnl: float
    equity_before: float
    equity_after: float
    margin_required: float
    margin_utilization_pct: float
    dollar_risk: float
    risk_pct: float
    is_win: bool
    is_breakeven: bool
    slices: List[SliceExecutionResult] = field(default_factory=list)


@dataclass
class SimulationRunResult:
    """Summary of a single Monte Carlo scaling run from $65 toward $10,000."""

    run_id: int
    starting_equity: float
    final_equity: float
    target_reached: bool
    ruined: bool
    trades_executed: int
    trades_skipped_killswitch: int
    days_elapsed: float
    max_drawdown_pct: float
    sharpe_ratio: float
    killswitch_trips: int
    equity_curve: List[float] = field(default_factory=list)
    daily_equity_curve: List[float] = field(default_factory=list)
    trade_pnls: List[float] = field(default_factory=list)


@dataclass
class SimulationReport:
    """Aggregated institutional Monte Carlo simulation report."""

    n_simulations: int
    n_trades: int
    starting_equity: float
    target_equity: float
    probability_target_reached: float
    probability_ruin: float
    median_trades_to_target: Optional[float]
    median_days_to_target: Optional[float]
    max_drawdown_dist: Dict[str, float]
    median_max_drawdown: float
    p95_max_drawdown: float
    sharpe_ratio_dist: Dict[str, float]
    mean_sharpe: float
    median_sharpe: float
    killswitch_trips_total: int
    killswitch_trips_mean: float
    runs: List[SimulationRunResult] = field(default_factory=list)

    def summary(self) -> str:
        """Render institutional terminal summary table."""
        lines = [
            "=" * 78,
            "MONTE CARLO ACCOUNT SCALING SIMULATION ($65.00 -> $10,000.00)",
            "=" * 78,
            f"Simulations:                {self.n_simulations:,}",
            f"Trades Allocated per Run:   {self.n_trades:,}",
            f"Starting Equity:            ${self.starting_equity:.2f}",
            f"Target Equity:              ${self.target_equity:.2f}",
            "-" * 78,
            "SCALING OUTCOMES:",
            f"Probability Target Reached: {self.probability_target_reached * 100.0:.2f}%",
            f"Probability of Ruin:        {self.probability_ruin * 100.0:.2f}%",
            (
                f"Median Trades to $10,000:   {self.median_trades_to_target:.1f}"
                if self.median_trades_to_target is not None
                else "Median Trades to $10,000:   N/A (target not reached)"
            ),
            (
                f"Median Days to $10,000:     {self.median_days_to_target:.1f} days"
                if self.median_days_to_target is not None
                else "Median Days to $10,000:     N/A"
            ),
            "-" * 78,
            "DRAWDOWN DISTRIBUTION (Peak-to-Trough):",
            f"  Min MaxDD:                {self.max_drawdown_dist.get('min', 0.0) * 100.0:.2f}%",
            f"  25th Percentile:          {self.max_drawdown_dist.get('p25', 0.0) * 100.0:.2f}%",
            f"  Median (50th):            {self.median_max_drawdown * 100.0:.2f}%",
            f"  75th Percentile:          {self.max_drawdown_dist.get('p75', 0.0) * 100.0:.2f}%",
            f"  95th Percentile:          {self.p95_max_drawdown * 100.0:.2f}%",
            f"  99th Percentile:          {self.max_drawdown_dist.get('p99', 0.0) * 100.0:.2f}%",
            f"  Max MaxDD:                {self.max_drawdown_dist.get('max', 0.0) * 100.0:.2f}%",
            "-" * 78,
            "PERFORMANCE & RISK METRICS:",
            f"  Mean Sharpe Ratio:        {self.mean_sharpe:.2f}",
            f"  Median Sharpe Ratio:      {self.median_sharpe:.2f}",
            f"  95th Pct Sharpe:          {self.sharpe_ratio_dist.get('p95', 0.0):.2f}",
            f"  5% DD Killswitch Halts:   {self.killswitch_trips_total:,} total ({self.killswitch_trips_mean:.2f} / run)",
            "=" * 78,
        ]
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize simulation report to dictionary."""
        return {
            "n_simulations": self.n_simulations,
            "n_trades": self.n_trades,
            "starting_equity": self.starting_equity,
            "target_equity": self.target_equity,
            "probability_target_reached": self.probability_target_reached,
            "probability_ruin": self.probability_ruin,
            "median_trades_to_target": self.median_trades_to_target,
            "median_days_to_target": self.median_days_to_target,
            "max_drawdown_dist": self.max_drawdown_dist,
            "median_max_drawdown": self.median_max_drawdown,
            "p95_max_drawdown": self.p95_max_drawdown,
            "sharpe_ratio_dist": self.sharpe_ratio_dist,
            "mean_sharpe": self.mean_sharpe,
            "median_sharpe": self.median_sharpe,
            "killswitch_trips_total": self.killswitch_trips_total,
            "killswitch_trips_mean": self.killswitch_trips_mean,
        }


# -------------------------------------------------------------------------
# Friction & Execution Simulation Engine
# -------------------------------------------------------------------------


class FrictionEngine:
    """
    Models Hyperliquid microstructural execution frictions:
    - 3-slice stagger jitter delay (0ms, 50ms, 100ms) with micro-drift.
    - 0.5 to 1.5 pip slippage ($0.05 to $0.15) on entries and market/stop exits.
    - Hyperliquid CLOB fees (3.5 bps taker, -0.2 bps maker rebate).
    - 1-hour funding payments based on trade duration.
    """

    def __init__(
        self,
        taker_fee_rate: float = TAKER_FEE_RATE,
        maker_fee_rate: float = MAKER_FEE_RATE,
        min_slippage_pips: float = MIN_SLIPPAGE_PIPS,
        max_slippage_pips: float = MAX_SLIPPAGE_PIPS,
        pip_size: float = PIP_SIZE,
        funding_rate_1h: float = DEFAULT_1H_FUNDING_RATE,
        slice_jitter_ms: int = SLICE_JITTER_MS,
    ) -> None:
        self.taker_fee_rate = taker_fee_rate
        self.maker_fee_rate = maker_fee_rate
        self.min_slippage_pips = min_slippage_pips
        self.max_slippage_pips = max_slippage_pips
        self.pip_size = pip_size
        self.funding_rate_1h = funding_rate_1h
        self.slice_jitter_ms = slice_jitter_ms

    def sample_slippage(
        self, rng: Optional[random.Random] = None
    ) -> Tuple[float, float]:
        """
        Sample adverse slippage in pips and dollars.
        Returns: (slippage_pips, slippage_dollars)
        """
        r = rng if rng is not None else random
        pips = r.uniform(self.min_slippage_pips, self.max_slippage_pips)
        dollars = round(pips * self.pip_size, 4)
        return pips, dollars

    def execute_entry_slices(
        self,
        basket_size: float,
        base_price: float,
        is_buy: bool,
        rng: Optional[random.Random] = None,
        include_slices: bool = True,
    ) -> Tuple[float, float, List[SliceExecutionResult]]:
        """
        Simulate concurrent order slicing across 3 tickets with 50ms stagger jitter:
        - Slice 0: t=0ms, base market price + slippage
        - Slice 1: t=50ms, micro-drift + slippage
        - Slice 2: t=100ms, micro-drift + slippage

        Returns: (weighted_avg_entry_price, total_entry_fee, slice_results)
        """
        slices = slice_basket(basket_size, SLICE_COUNT)
        slice_results: List[SliceExecutionResult] = []

        total_notional = 0.0
        total_sz = 0.0
        total_fee = 0.0
        r = rng if rng is not None else random

        for idx, sz in enumerate(slices):
            delay_ms = idx * self.slice_jitter_ms
            pips, slip_dollars = self.sample_slippage(r)

            # Micro-jitter drift during 50ms/100ms latency window
            jitter_drift = 0.0
            if idx > 0:
                # 50ms-100ms micro-drift ~0.005 to 0.015 per oz
                jitter_drift = r.uniform(-0.01, 0.02) if is_buy else r.uniform(-0.02, 0.01)

            if is_buy:
                fill_px = round(base_price + jitter_drift + slip_dollars, 2)
            else:
                fill_px = round(base_price - jitter_drift - slip_dollars, 2)

            notional = sz * fill_px
            fee = round(notional * self.taker_fee_rate, 4)

            total_notional += notional
            total_sz += sz
            total_fee += fee

            if include_slices:
                slice_results.append(
                    SliceExecutionResult(
                        slice_index=idx,
                        delay_ms=delay_ms,
                        sz=sz,
                        fill_price=fill_px,
                        slippage_pips=round(pips, 2),
                        fee=fee,
                    )
                )

        avg_entry = round(total_notional / total_sz, 2) if total_sz > 0 else base_price
        return avg_entry, total_fee, slice_results

    def execute_exit(
        self,
        basket_size: float,
        raw_exit_price: float,
        is_buy: bool,
        is_maker: bool = False,
        rng: Optional[random.Random] = None,
    ) -> Tuple[float, float]:
        """
        Calculate exit fill price with adverse slippage and exit fee.
        Returns: (fill_price, exit_fee)
        """
        r = rng if rng is not None else random
        if is_maker:
            # Maker exit (limit order): zero adverse slippage, maker rebate
            fill_px = round(raw_exit_price, 2)
            fee = round((basket_size * fill_px) * self.maker_fee_rate, 4)
        else:
            # Taker exit (stop-market or dynamic market liquidation): adverse slippage
            _, slip_dollars = self.sample_slippage(r)
            if is_buy:
                fill_px = round(raw_exit_price - slip_dollars, 2)
            else:
                fill_px = round(raw_exit_price + slip_dollars, 2)
            fee = round((basket_size * fill_px) * self.taker_fee_rate, 4)

        return fill_px, fee

    def calculate_funding_fee(
        self,
        basket_size: float,
        entry_price: float,
        holding_duration_hours: float,
        is_buy: bool,
    ) -> float:
        """
        Calculate 1-hour Hyperliquid funding payment over trade holding duration.
        Returns funding fee (positive = cost, negative = rebate).
        """
        notional = basket_size * entry_price
        hours = max(0.0, holding_duration_hours)
        rate = self.funding_rate_1h * hours
        # Positive funding rate: longs pay shorts
        return round(notional * rate if is_buy else -notional * rate, 4)


# -------------------------------------------------------------------------
# Synthetic Trade Series Generator (Relapse ICT Scalper Model)
# -------------------------------------------------------------------------


@dataclass
class SyntheticTradeProfile:
    """Historical profile of the 5M XAUUSD Relapse ICT Scalper."""

    win_rate: float = 0.65
    avg_win_r: float = 3.20
    win_r_std: float = 0.75
    min_win_r: float = 1.50
    max_win_r: float = 6.00
    breakeven_prob: float = 0.10  # Probability of +0.1R breakeven lock exit
    breakeven_r: float = 0.10
    loss_r: float = -1.00
    maker_exit_prob: float = 0.60  # 60% of win exits are limit orders (maker rebate)
    min_sl_distance: float = 1.00
    max_sl_distance: float = 1.50
    trades_per_day_mean: float = 5.0
    avg_duration_hours: float = 0.75  # ~45 minutes holding time


class SyntheticTradeGenerator:
    """Generates realistic trade sequences matching 5M Gold Relapse Scalper."""

    def __init__(
        self,
        profile: Optional[SyntheticTradeProfile] = None,
        seed: Optional[int] = None,
    ) -> None:
        self.profile = profile or SyntheticTradeProfile()
        self.rng = random.Random(seed)

    def generate_trades(
        self,
        n_trades: int,
        start_ts: float = 1774000000.0,
    ) -> List[Dict[str, Any]]:
        """Generate synthetic trade signals with timestamps spanning trading days."""
        trades: List[Dict[str, Any]] = []
        current_ts = start_ts

        for i in range(n_trades):
            # Advance timestamp within London/NY killzones
            # Average interval between trades ~3 to 5 hours of market time
            trade_gap_hours = self.rng.uniform(2.0, 5.5)
            current_ts += trade_gap_hours * 3600.0

            is_buy = self.rng.random() < 0.5
            sl_dist = round(
                self.rng.uniform(
                    self.profile.min_sl_distance, self.profile.max_sl_distance
                ),
                2,
            )

            # Determine outcome
            rand_val = self.rng.random()
            if rand_val < self.profile.breakeven_prob:
                r_mult = self.profile.breakeven_r
                is_win = True
                is_be = True
                is_maker = False
            elif rand_val < (self.profile.win_rate + self.profile.breakeven_prob):
                r_mult = max(
                    self.profile.min_win_r,
                    min(
                        self.profile.max_win_r,
                        self.rng.gauss(
                            self.profile.avg_win_r, self.profile.win_r_std
                        ),
                    ),
                )
                r_mult = round(r_mult, 2)
                is_win = True
                is_be = False
                is_maker = self.rng.random() < self.profile.maker_exit_prob
            else:
                r_mult = self.profile.loss_r
                is_win = False
                is_be = False
                is_maker = False

            duration_hours = max(
                0.25, round(self.rng.gauss(self.profile.avg_duration_hours, 0.3), 2)
            )

            trades.append(
                {
                    "trade_id": f"SYN-{i+1:05d}",
                    "timestamp": current_ts,
                    "is_buy": is_buy,
                    "sl_distance": sl_dist,
                    "r_multiple": r_mult,
                    "duration_hours": duration_hours,
                    "is_win": is_win,
                    "is_breakeven": is_be,
                    "is_maker": is_maker,
                }
            )

        return trades



# -------------------------------------------------------------------------
# Monte Carlo Account Scaling Simulator Engine
# -------------------------------------------------------------------------


class MonteCarloScalingSimulator:
    """
    Monte Carlo Scaling Simulator for 5-Minute XAUUSD Relapse Scalper.
    Scales a $65.00 micro-account toward $10,000.00 under 100x leverage on Hyperliquid.
    """

    def __init__(
        self,
        starting_equity: float = DEFAULT_STARTING_EQUITY,
        target_equity: float = DEFAULT_TARGET_EQUITY,
        ruin_equity: float = DEFAULT_RUIN_EQUITY,
        gold_price: float = DEFAULT_GOLD_PRICE,
        leverage: float = DEFAULT_LEVERAGE,
        taker_fee_rate: float = TAKER_FEE_RATE,
        maker_fee_rate: float = MAKER_FEE_RATE,
        min_slippage_pips: float = MIN_SLIPPAGE_PIPS,
        max_slippage_pips: float = MAX_SLIPPAGE_PIPS,
        funding_rate_1h: float = DEFAULT_1H_FUNDING_RATE,
        max_daily_drawdown_pct: float = MAX_DAILY_DRAWDOWN_PCT,
        max_margin_pct: float = MAX_MARGIN_PCT,
        max_risk_pct: float = MAX_RISK_PCT,
        seed: Optional[int] = None,
    ) -> None:
        self.starting_equity = starting_equity
        self.target_equity = target_equity
        self.ruin_equity = ruin_equity
        self.gold_price = gold_price
        self.leverage = leverage
        self.max_daily_drawdown_pct = max_daily_drawdown_pct
        self.max_margin_pct = max_margin_pct
        self.max_risk_pct = max_risk_pct
        self.seed = seed

        self.friction_engine = FrictionEngine(
            taker_fee_rate=taker_fee_rate,
            maker_fee_rate=maker_fee_rate,
            min_slippage_pips=min_slippage_pips,
            max_slippage_pips=max_slippage_pips,
            funding_rate_1h=funding_rate_1h,
        )

    def simulate_single_run(
        self,
        run_id: int,
        n_trades: int,
        trade_series: Optional[Sequence[Dict[str, Any]]] = None,
        rng: Optional[random.Random] = None,
    ) -> SimulationRunResult:
        """
        Execute a single simulation trajectory from starting_equity ($65).
        Tracks dynamic position sizing, friction costs, 5% daily drawdown killswitch,
        and capital compounding up to target or ruin.
        """
        r = rng if rng is not None else random.Random()
        equity = self.starting_equity
        peak_equity = equity
        max_drawdown = 0.0
        killswitch_trips = 0
        trades_executed = 0
        trades_skipped_killswitch = 0

        guard = SimulationDailyDrawdownGuard(
            max_drawdown_pct=self.max_daily_drawdown_pct
        )

        equity_curve: List[float] = [equity]
        trade_pnls: List[float] = []
        daily_equity_map: Dict[int, float] = {}

        # If trade series not provided, generate trades on the fly
        is_bootstrap = trade_series is not None and len(trade_series) > 0
        if not is_bootstrap:
            gen_profile = SyntheticTradeProfile()
            current_ts = 1774000000.0
            first_ts = 0.0
        else:
            first_ts = trade_series[0]["timestamp"] if trade_series else 0.0

        last_ts = first_ts
        target_reached = False
        ruined = False

        for trade_idx in range(n_trades):
            if is_bootstrap:
                trade_dict = r.choice(trade_series)
                ts = trade_dict["timestamp"]
                is_buy = trade_dict.get("is_buy", True)
                sl_distance = trade_dict.get("sl_distance", 1.15)
                r_multiple = trade_dict.get("r_multiple", 2.0)
                is_maker = trade_dict.get("is_maker", False)
                duration_hours = trade_dict.get("duration_hours", 0.75)
            else:
                trade_gap_hours = r.uniform(2.0, 5.5)
                current_ts += trade_gap_hours * 3600.0
                ts = current_ts
                is_buy = r.random() < 0.5
                sl_distance = round(
                    r.uniform(
                        gen_profile.min_sl_distance, gen_profile.max_sl_distance
                    ),
                    2,
                )
                rand_val = r.random()
                if rand_val < gen_profile.breakeven_prob:
                    r_multiple = gen_profile.breakeven_r
                    is_maker = False
                elif rand_val < (gen_profile.win_rate + gen_profile.breakeven_prob):
                    r_mult = max(
                        gen_profile.min_win_r,
                        min(
                            gen_profile.max_win_r,
                            r.gauss(gen_profile.avg_win_r, gen_profile.win_r_std),
                        ),
                    )
                    r_multiple = round(r_mult, 2)
                    is_maker = r.random() < gen_profile.maker_exit_prob
                else:
                    r_multiple = gen_profile.loss_r
                    is_maker = False
                duration_hours = max(
                    0.25, round(r.gauss(gen_profile.avg_duration_hours, 0.3), 2)
                )

            if trade_idx == 0 and not is_bootstrap:
                first_ts = ts
            last_ts = ts
            day_key = int(ts // DAY_SECONDS) * DAY_SECONDS

            # Check target and ruin thresholds
            if equity >= self.target_equity:
                target_reached = True
                break
            if equity <= self.ruin_equity:
                ruined = True
                break

            # 1. Update Daily Drawdown Guard and evaluate killswitch state
            is_tripped, _ = guard.update(equity, ts)
            if is_tripped:
                trades_skipped_killswitch += 1
                daily_equity_map[day_key] = equity
                continue

            # 2. Dynamic Compounding Sizing Formula
            basket_size = calculate_basket_size(
                equity=equity,
                price=self.gold_price,
                leverage=self.leverage,
                min_size=MIN_BASKET_SIZE,
                max_margin_pct=self.max_margin_pct,
                max_risk_pct=self.max_risk_pct,
                sl_distance=sl_distance,
            )

            # Invariant check: Margin requirement <= 20% equity
            margin_req = calculate_margin_required(
                basket_size, self.gold_price, self.leverage
            )
            if margin_req > (equity * self.max_margin_pct + 1e-4):
                # Clamp size strictly to margin ceiling
                basket_size = round_down(
                    (self.max_margin_pct * equity * self.leverage) / self.gold_price,
                    2,
                )
                margin_req = calculate_margin_required(
                    basket_size, self.gold_price, self.leverage
                )

            # 3. Simulate 3-slice entry with 50ms stagger jitter & slippage
            avg_entry_price, entry_fee, _ = (
                self.friction_engine.execute_entry_slices(
                    basket_size=basket_size,
                    base_price=self.gold_price,
                    is_buy=is_buy,
                    rng=r,
                    include_slices=False,
                )
            )

            # 4. Simulate exit with friction & funding
            raw_exit_delta = r_multiple * sl_distance
            raw_exit_price = (
                avg_entry_price + raw_exit_delta
                if is_buy
                else avg_entry_price - raw_exit_delta
            )

            # If winner with limit TP, maker exit (rebate). Otherwise stop out / market close (taker)
            exit_fill_price, exit_fee = self.friction_engine.execute_exit(
                basket_size=basket_size,
                raw_exit_price=raw_exit_price,
                is_buy=is_buy,
                is_maker=is_maker,
                rng=r,
            )

            funding_fee = self.friction_engine.calculate_funding_fee(
                basket_size=basket_size,
                entry_price=avg_entry_price,
                holding_duration_hours=duration_hours,
                is_buy=is_buy,
            )

            # 5. Net PnL Accounting
            if is_buy:
                gross_pnl = (exit_fill_price - avg_entry_price) * basket_size
            else:
                gross_pnl = (avg_entry_price - exit_fill_price) * basket_size

            net_pnl = round(gross_pnl - entry_fee - exit_fee - funding_fee, 4)

            # Update Equity & Curves
            equity = round(equity + net_pnl, 2)
            trades_executed += 1
            trade_pnls.append(net_pnl)
            equity_curve.append(equity)
            daily_equity_map[day_key] = equity

            # Update peak equity and peak-to-trough max drawdown
            if equity > peak_equity:
                peak_equity = equity
            dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
            if dd > max_drawdown:
                max_drawdown = dd

            # Post-trade guard check: trip killswitch if daily drawdown >= 5%
            was_tripped_before = guard.is_tripped
            is_tripped_now, _ = guard.update(equity, ts)
            if not was_tripped_before and is_tripped_now:
                killswitch_trips += 1

        if equity >= self.target_equity:
            target_reached = True
        elif equity <= self.ruin_equity:
            ruined = True

        days_elapsed = (
            max(1.0, (last_ts - first_ts) / DAY_SECONDS) if first_ts > 0 else 1.0
        )

        # Compute Sharpe Ratio from daily returns
        daily_equity_vals = list(daily_equity_map.values())
        sharpe_ratio = self._calculate_sharpe(daily_equity_vals)

        return SimulationRunResult(
            run_id=run_id,
            starting_equity=self.starting_equity,
            final_equity=equity,
            target_reached=target_reached,
            ruined=ruined,
            trades_executed=trades_executed,
            trades_skipped_killswitch=trades_skipped_killswitch,
            days_elapsed=round(days_elapsed, 1),
            max_drawdown_pct=round(max_drawdown, 4),
            sharpe_ratio=round(sharpe_ratio, 2),
            killswitch_trips=killswitch_trips,
            equity_curve=equity_curve,
            daily_equity_curve=daily_equity_vals,
            trade_pnls=trade_pnls,
        )

    def _calculate_sharpe(self, daily_equities: List[float]) -> float:
        """Calculate annualized Sharpe ratio from daily equity series."""
        if len(daily_equities) < 3:
            return 0.0

        daily_returns: List[float] = []
        for i in range(1, len(daily_equities)):
            prev = daily_equities[i - 1]
            if prev > 0:
                ret = (daily_equities[i] - prev) / prev
                daily_returns.append(ret)

        if not daily_returns:
            return 0.0

        mean_ret = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean_ret) ** 2 for r in daily_returns) / len(daily_returns)
        std_ret = math.sqrt(variance)

        if std_ret < 1e-8:
            return 0.0

        # Annualize assuming 252 trading days
        return round((mean_ret / std_ret) * math.sqrt(252.0), 2)

    def run_simulation(
        self,
        n_simulations: int = 1000,
        n_trades: int = 500,
        trades: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> SimulationReport:
        """
        Execute N Monte Carlo permutations over trade series.
        Aggregates distribution statistics, target attainment probability,
        max drawdown percentiles, and Sharpe ratios.
        """
        master_rng = random.Random(self.seed)
        runs: List[SimulationRunResult] = []

        target_reached_count = 0
        ruin_count = 0
        trades_to_target_list: List[int] = []
        days_to_target_list: List[float] = []
        max_drawdowns: List[float] = []
        sharpe_ratios: List[float] = []
        total_killswitch_trips = 0

        for sim_idx in range(n_simulations):
            sim_seed = master_rng.randint(0, 2_147_483_647)
            sim_rng = random.Random(sim_seed)

            run = self.simulate_single_run(
                run_id=sim_idx + 1,
                n_trades=n_trades,
                trade_series=trades,
                rng=sim_rng,
            )
            runs.append(run)

            if run.target_reached:
                target_reached_count += 1
                trades_to_target_list.append(run.trades_executed)
                days_to_target_list.append(run.days_elapsed)

            if run.ruined:
                ruin_count += 1

            max_drawdowns.append(run.max_drawdown_pct)
            sharpe_ratios.append(run.sharpe_ratio)
            total_killswitch_trips += run.killswitch_trips

        # Percentile calculations
        sorted_dds = sorted(max_drawdowns)
        sorted_sharpes = sorted(sharpe_ratios)

        def percentile(arr: List[float], pct: float) -> float:
            if not arr:
                return 0.0
            idx = int(round((len(arr) - 1) * pct))
            return arr[max(0, min(len(arr) - 1, idx))]

        dd_dist = {
            "min": round(sorted_dds[0], 4) if sorted_dds else 0.0,
            "p25": round(percentile(sorted_dds, 0.25), 4),
            "p50": round(percentile(sorted_dds, 0.50), 4),
            "p75": round(percentile(sorted_dds, 0.75), 4),
            "p90": round(percentile(sorted_dds, 0.90), 4),
            "p95": round(percentile(sorted_dds, 0.95), 4),
            "p99": round(percentile(sorted_dds, 0.99), 4),
            "max": round(sorted_dds[-1], 4) if sorted_dds else 0.0,
        }

        sharpe_dist = {
            "min": round(sorted_sharpes[0], 2) if sorted_sharpes else 0.0,
            "p25": round(percentile(sorted_sharpes, 0.25), 2),
            "p50": round(percentile(sorted_sharpes, 0.50), 2),
            "p75": round(percentile(sorted_sharpes, 0.75), 2),
            "p90": round(percentile(sorted_sharpes, 0.90), 2),
            "p95": round(percentile(sorted_sharpes, 0.95), 2),
            "max": round(sorted_sharpes[-1], 2) if sorted_sharpes else 0.0,
        }

        prob_target = target_reached_count / n_simulations if n_simulations > 0 else 0.0
        prob_ruin = ruin_count / n_simulations if n_simulations > 0 else 0.0

        median_trades = (
            float(percentile(sorted(trades_to_target_list), 0.50))
            if trades_to_target_list
            else None
        )
        median_days = (
            float(percentile(sorted(days_to_target_list), 0.50))
            if days_to_target_list
            else None
        )

        mean_sharpe = (
            round(sum(sharpe_ratios) / len(sharpe_ratios), 2)
            if sharpe_ratios
            else 0.0
        )
        median_sharpe = round(percentile(sorted_sharpes, 0.50), 2)

        return SimulationReport(
            n_simulations=n_simulations,
            n_trades=n_trades,
            starting_equity=self.starting_equity,
            target_equity=self.target_equity,
            probability_target_reached=round(prob_target, 4),
            probability_ruin=round(prob_ruin, 4),
            median_trades_to_target=median_trades,
            median_days_to_target=median_days,
            max_drawdown_dist=dd_dist,
            median_max_drawdown=dd_dist["p50"],
            p95_max_drawdown=dd_dist["p95"],
            sharpe_ratio_dist=sharpe_dist,
            mean_sharpe=mean_sharpe,
            median_sharpe=median_sharpe,
            killswitch_trips_total=total_killswitch_trips,
            killswitch_trips_mean=(
                round(total_killswitch_trips / n_simulations, 2)
                if n_simulations > 0
                else 0.0
            ),
            runs=runs,
        )


# -------------------------------------------------------------------------
# CLI Entry Point
# -------------------------------------------------------------------------


def parse_args(args: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monte Carlo Scaling Simulator for 5M XAUUSD Relapse Scalper on Hyperliquid ($65 -> $10k)"
    )
    parser.add_argument(
        "--simulations",
        "-s",
        type=int,
        default=1000,
        help="Number of Monte Carlo permutations (default: 1000)",
    )
    parser.add_argument(
        "--trades",
        "-t",
        type=int,
        default=500,
        help="Number of trades per simulation run (default: 500)",
    )
    parser.add_argument(
        "--initial-equity",
        "-e",
        type=float,
        default=DEFAULT_STARTING_EQUITY,
        help="Initial account equity in USD (default: 65.0)",
    )
    parser.add_argument(
        "--target-equity",
        type=float,
        default=DEFAULT_TARGET_EQUITY,
        help="Target account equity in USD (default: 10000.0)",
    )
    parser.add_argument(
        "--gold-price",
        "-p",
        type=float,
        default=DEFAULT_GOLD_PRICE,
        help="Underlying GOLD perpetual price in USD/oz (default: 2500.0)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for deterministic reproducibility",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured JSON report to stdout",
    )
    return parser.parse_args(args)


def main(args: Optional[List[str]] = None) -> int:
    parsed = parse_args(args)
    simulator = MonteCarloScalingSimulator(
        starting_equity=parsed.initial_equity,
        target_equity=parsed.target_equity,
        gold_price=parsed.gold_price,
        seed=parsed.seed,
    )

    t0 = time.time()
    report = simulator.run_simulation(
        n_simulations=parsed.simulations,
        n_trades=parsed.trades,
    )
    elapsed = time.time() - t0

    if parsed.json:
        data = report.to_dict()
        data["elapsed_seconds"] = round(elapsed, 3)
        print(json.dumps(data, indent=2))
    else:
        print(report.summary())
        print(f"Simulation completed in {elapsed:.3f} seconds.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
