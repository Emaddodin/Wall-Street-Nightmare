"""Robustness tests (spec section 22): perturbation, stress, Monte Carlo.

  * parameter perturbation -- move key parameters by +/-10% and +/-20%, one
    at a time, and check performance does not collapse;
  * slippage stress -- 1x / 2x / 3x the configured slippage;
  * fee stress -- 1x / 1.5x / 2x the configured fee;
  * Monte Carlo -- reshuffle trade order and add execution noise to the
    equity path: probability of ruin, expected drawdown, 5th-percentile
    equity curve.

Everything here is seeded -> reproducible.
"""
from __future__ import annotations

import math

import numpy as np

from engine import BacktestEngine
from metrics import summarize

PERTURB_KEYS = [
    ("regime.ema_fast", 0.1), ("regime.ema_slow", 0.1),
    ("regime.adx_min", 0.1), ("setup.sweep_lookback", 0.1),
    ("setup.sweep_fresh_bars", 0.1), ("entry.volume_mult", 0.1),
    ("entry.swing_lookback", 0.1), ("stop.atr_buffer_mult", 0.1),
    ("score.min", 0.1), ("risk.risk_per_trade", 0.1),
]


def perturb(cfg_factory, store, start_ms, end_ms, deltas=(0.1, 0.2)) -> list[dict]:
    """One-at-a-time parameter perturbation.  cfg_factory returns a fresh
    base Config for each run."""
    rows = []
    base_cfg = cfg_factory()
    for path, _ in PERTURB_KEYS:
        cur = base_cfg.get(path)
        if not isinstance(cur, (int, float)):
            continue
        for sign in (+1, -1):
            for d in deltas:
                if isinstance(cur, int):
                    new = int(round(cur * (1 + sign * d)))
                else:
                    new = round(cur * (1 + sign * d), 6)
                if new <= 0:
                    continue
                cfg = cfg_factory(overrides={path: new})
                summary = _run(cfg, store, start_ms, end_ms)
                rows.append({"path": path, "delta": sign * d, "value": new,
                             **summary})
    return rows


def stress_slippage_fees(cfg_factory, store, start_ms, end_ms) -> dict:
    out = {}
    for what, mults in (("slippage", (1.0, 2.0, 3.0)), ("fee", (1.0, 1.5, 2.0))):
        base = cfg_factory().get(f"execution.{what}_bps")
        for m in mults:
            cfg = cfg_factory(overrides={f"execution.{what}_bps": base * m})
            out[f"{what}x{m:g}"] = _run(cfg, store, start_ms, end_ms)
    return out


def monte_carlo(trades: list[dict], starting_equity: float, n_sims: int = 500,
                ruin_pct: float = 0.90, seed: int = 42,
                noise_frac: float = 0.05) -> dict:
    """Bootstrap the trade sequence with execution noise.

    noise_frac: each trade's PnL is jittered by +/- noise_frac * |pnl|
    (slippage/timing noise); ruin_pct: equity below starting*(1-ruin_pct)
    counts as ruin for the campaign."""
    rng = np.random.default_rng(seed)
    pnls = np.array([t["pnl"] for t in trades], dtype=float)
    if len(pnls) == 0:
        return {"prob_ruin": 1.0, "expected_dd_pct": 0.0,
                "p5_final_equity": starting_equity}
    ruins = 0
    finals = np.empty(n_sims)
    dds = np.empty(n_sims)
    ruin_floor = starting_equity * (1.0 - ruin_pct)
    for s in range(n_sims):
        order = rng.permutation(len(pnls))
        noisy = pnls[order] * (1.0 + rng.uniform(-noise_frac, noise_frac, len(pnls)))
        path = starting_equity + np.cumsum(noisy)
        finals[s] = path[-1]
        peak = np.maximum.accumulate(np.concatenate(([starting_equity], path)))
        dd = np.max((peak - np.concatenate(([starting_equity], path)))
                    / np.where(peak == 0, 1, peak))
        dds[s] = dd
        if (path <= ruin_floor).any():
            ruins += 1
    return {
        "prob_ruin": ruins / n_sims,
        "expected_dd_pct": float(np.mean(dds) * 100.0),
        "worst_dd_pct": float(np.max(dds) * 100.0),
        "p5_final_equity": float(np.percentile(finals, 5)),
        "median_final_equity": float(np.median(finals)),
    }


def _run(cfg, store, start_ms, end_ms) -> dict:
    frames = {s: store.load(s, "1m") for s in store.symbols()}
    eng = BacktestEngine(cfg)
    sds = eng.prepare(frames, start_ms, end_ms)
    res = eng.run(sds, start_ms, end_ms, starting_equity=cfg.paper["starting_equity"])
    s = summarize(res.trades, res.equity_curve, cfg.paper["starting_equity"])
    return {"n_trades": s["n_trades"], "net_pnl": s["net_pnl"],
            "final_equity": s["final_equity"], "profit_factor": s["profit_factor"],
            "expectancy": s["expectancy"], "max_dd_pct": s["max_drawdown_pct"],
            "avg_r": s["avg_r"], "sharpe": s["sharpe"]}
