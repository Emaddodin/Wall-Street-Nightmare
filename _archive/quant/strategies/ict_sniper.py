"""ICT Sniper -- quant engine adapter (events for quant.engine.backtest.Sim).

The detector brain lives in scalper/pa/sniper.py; this module maps its
emitted setups onto the Sim's event contract with zero lookahead:

  * Signals are computed on CLOSED TF bars (5m/15m) resampled causally
    from the 1m klines (partial buckets dropped).
  * An event is keyed to the OPEN TIME of the LAST 1m bar of the TF bar
    (== the TF bar's close time minus one minute).  The Sim then reads
    `ref` = that 1m bar's close == the exact TF close used by the
    detector, computes the FVG limit from it, and the resting limit can
    only fill on STRICTLY LATER 1m bars.  Nothing from the future is
    used, at any step.
  * The fill is a maker limit at the FVG CE: fills only when a later 1m
    bar's range trades through the level (the Sim's trade-through rule).
  * exit_model is per-trade and exact: sl_bps reaches the sweep extreme
    minus the ATR buffer; tp_bps is the clamped 150-300 bps target;
    max_bars = time_exit_bars x TF minutes (the 4-6 bar time kill).
  * Frequency throttle and the 2-loss circuit breaker are enforced by
    quant.engine.guards.GuardedSim, not here (they depend on fills).

Run it through quant/experiments/run_ict_sniper.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from scalper.pa.sniper import SniperParams, detect_states, gate_setups

MIN_MS = 60_000


def resample_causal_1m(df: pd.DataFrame, tf_ms: int) -> pd.DataFrame:
    """OHLCV-aggregate 1m klines to `tf_ms` bars.  Only COMPLETE buckets
    are kept (the last 1m bar of the bucket must exist), so the final
    partial TF bar can never leak into signal generation."""
    t = df["open_time"].to_numpy(dtype=np.int64)
    bucket = t // tf_ms * tf_ms
    g = df.groupby(bucket)
    out = g.agg(open=("open", "first"), high=("high", "max"),
                low=("low", "min"), close=("close", "last"),
                volume=("volume", "sum")).reset_index().rename(
                    columns={"index": "open_time"})
    last_t = g["open_time"].max().to_numpy()
    complete = last_t + MIN_MS == out["open_time"].to_numpy() + tf_ms
    return out[complete].reset_index(drop=True)


def build_events(tf_frames: dict[str, pd.DataFrame], tf_minutes: int,
                 params: SniperParams | dict | None = None,
                 alloc: float = 0.5, lev: float = 20.0,
                 structure: dict[str, pd.DataFrame] | None = None
                 ) -> dict[int, list]:
    """tf_frames: {symbol: TF OHLCV DataFrame (open_time, open, high, low,
    close, volume)}.  Returns the Sim events dict keyed by 1m open times
    (see module docstring for the no-lookahead keying).

    `structure`: optional cached detect_structure frames per symbol (the
    sweep computes them once -- only the TP gates vary across the grid)."""
    p = params if isinstance(params, SniperParams) else \
        SniperParams(**(params or {}))
    tf_ms = tf_minutes * MIN_MS
    events: dict[int, list] = {}
    for sym, df in tf_frames.items():
        if len(df) < 2 * p.arm + p.disp_avg_window + 6:
            continue
        if structure is not None and sym in structure:
            states = pd.concat([structure[sym],
                                gate_setups(structure[sym], p)], axis=1)
        else:
            states = detect_states(df.reset_index(drop=True), p)
        t = df["open_time"].to_numpy(dtype=np.int64)
        c = df["close"].to_numpy(dtype=float)
        for side, tag in ((1, "l"), (-1, "s")):
            emit = states[f"emit_{tag}"].to_numpy()
            for i in np.nonzero(emit)[0]:
                entry = float(states[f"entry_{tag}"].iloc[i])
                sl_bps = float(states[f"sl_bps_{tag}"].iloc[i])
                tp_bps = float(states[f"tp_bps_{tag}"].iloc[i])
                rr = float(states[f"rr_{tag}"].iloc[i])
                trim_bps = float(states[f"trim_bps_{tag}"].iloc[i]) \
                    if p.trim else None
                # key = last 1m bar of the TF bar (its close == TF close)
                key = int(t[i]) + tf_ms - MIN_MS
                limit_bps = (c[i] - entry) / c[i] * 1e4 if side > 0 else \
                    (entry - c[i]) / c[i] * 1e4
                exit_model = {"tp_bps": tp_bps, "sl_bps": sl_bps,
                              "trail_bps": None,
                              "max_bars": int(p.time_exit_bars * tf_minutes),
                              "be_after_r": p.be_after_r,
                              "be_after_bps": p.be_after_bps,
                              "be_to_r": p.be_to_r,
                              "neg_bars": p.neg_bars,
                              "tp1_bps": p.tp1_bps,
                              "tp1_frac": p.tp1_frac,
                              "trim_bps": trim_bps,
                              "trim_frac": p.trim_frac}
                meta = {"alloc": alloc, "lev": lev,
                        "limit_bps": float(limit_bps),
                        "limit_wait_bars": int(p.retest_bars * tf_minutes),
                        "be_after_r": p.be_after_r,
                        "be_after_bps": p.be_after_bps,
                        "be_to_r": p.be_to_r,
                        "neg_bars": p.neg_bars,
                        "tp1_bps": p.tp1_bps,
                        "tp1_frac": p.tp1_frac,
                        "trim_bps": trim_bps,
                        "trim_frac": p.trim_frac}
                events.setdefault(key, []).append(
                    (sym, side, rr, exit_model, meta))
    return events


class IctSniper:
    """Strategy-shaped wrapper (mirrors the runner's `.events()` convention;
    the canonical path is run_ict_sniper.py which also wires the guards)."""
    name = "ict_sniper"

    def __init__(self, tf_minutes: int = 15, alloc: float = 0.5,
                 lev: float = 20.0, **params):
        self.tf_minutes = tf_minutes
        self.alloc = alloc
        self.lev = lev
        self.p = SniperParams(**params)

    def events(self, frames: dict[str, pd.DataFrame]) -> dict[int, list]:
        return build_events(frames, self.tf_minutes, self.p,
                            self.alloc, self.lev)
