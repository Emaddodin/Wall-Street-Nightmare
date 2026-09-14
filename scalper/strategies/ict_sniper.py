"""ICT SNIPER -- the low-frequency, high-reward strategy (farm adapter).

Live-engine side of the ICT sniper.  The detection brain is
scalper/pa/sniper.py (pure, causal, shared with the quant backtester);
this class adapts it to the strategy-farm contract:

    on_bar(sd, i, tf15, j15, t_close, state) -> (EntrySignal | None, [])

Detection runs on CLOSED 15m bars (or 5m, configurable) built from the
engine's own arrays; the signal is emitted on the 1m bar that completes
the TF bar, so the resting limit can only fill on LATER 1m bars.

Setup (spec A): SSL/BSL sweep -> displacement MSS -> fresh FVG, passive
LIMIT at the FVG CE only.  SL beyond the sweep wick (swing_level = the
wick extreme; the engine adds its own stop.atr_buffer_mult), TP fixed at
150-300 bps with RR >= 2.5, time exit 4-6 TF bars, 2-5 trades/day max --
the live engine already enforces the daily throttle/breaker
(daily.max_trades_per_day / daily.max_consecutive_losses); the backtest
mirrors them via quant/engine/guards.GuardedSim.

Live config requirements when enabling this strategy:
  strategies.enabled: [..., ict_sniper]
  stop.max_sl_atr_mult: ~60   (the sniper's 15m-scale stop is >> 3x the
                               1m ATR; the default 3.0 would reject it)
  daily.max_trades_per_day: 5 (spec 2-5)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from entry_engine import EntrySignal
from structure import LONG, SHORT
from pa.sniper import SniperParams, detect_states

MIN_MS = 60_000
TF_COLS = ["open_time", "open", "high", "low", "close", "volume"]


class IctSniperStrategy:
    name = "ict_sniper"

    def __init__(self, cfg):
        self.cfg = cfg
        raw = (cfg.get("strategies", {}) or {}).get("ict_sniper", {}) or {}
        self.tf_minutes = 5 if raw.get("timeframe", "15m") == "5m" else 15
        self.tf_ms = self.tf_minutes * MIN_MS
        known = SniperParams.__dataclass_fields__
        kw = {k: v for k, v in raw.items() if k in known}
        self.p = SniperParams(**kw)

    # ------------------------------------------------------------------
    def _tf_bar(self, sd, i, tf15, j15, t_close, state):
        """Return the just-closed TF bar row (dict) or None when the TF
        bar has not just closed / warmup is incomplete."""
        if self.tf_minutes == 15:
            if j15 < 0 or t_close != int(tf15.t[j15]) + 900_000:
                return None, j15
            return {
                "open_time": int(tf15.t[j15]),
                "open": float(tf15.o[j15]), "high": float(tf15.h[j15]),
                "low": float(tf15.l[j15]), "close": float(tf15.c[j15]),
                "volume": float(tf15.v[j15])}, j15
        # 5m: aggregate the last five closed 1m bars (t_close is their
        # shared close time; the 5m bar closes when t_close % 300000 == 0)
        if t_close % 300_000 != 0 or i < 4:
            return None, i
        t1 = sd.tfs["1m"]
        if int(t1.t[i - 4]) + 300_000 != t_close:   # gap guard
            return None, i
        seg = slice(i - 4, i + 1)
        return {
            "open_time": int(t1.t[i - 4]),
            "open": float(t1.o[i - 4]),
            "high": float(np.max(t1.h[seg])),
            "low": float(np.min(t1.l[seg])),
            "close": float(t1.c[i]),
            "volume": float(np.sum(t1.v[seg]))}, i

    def on_bar(self, sd, i: int, tf15, j15: int, t_close: int, state: dict):
        p = self.p
        row, bar_key = self._tf_bar(sd, i, tf15, j15, t_close, state)
        if row is None:
            return None, []
        if state.get("last_tf_key") == bar_key:
            return None, []                     # this TF close was processed
        state["last_tf_key"] = bar_key

        # append to the rolling detector frame
        tail = state.get("tail")
        if tail is None:
            tail = pd.DataFrame(columns=TF_COLS)
        tail = pd.concat([tail, pd.DataFrame([row])],
                         ignore_index=True).tail(p.tail_bars)
        state["tail"] = tail
        warmup = 2 * p.arm + p.disp_avg_window + 6
        if len(tail) < warmup:
            return None, []

        states = detect_states(tail.reset_index(drop=True), p)
        last = states.iloc[-1]
        if not (bool(last["emit_l"]) or bool(last["emit_s"])):
            return None, []
        side = LONG if bool(last["emit_l"]) else SHORT
        tag = "l" if side == LONG else "s"

        # one emission per (sweep bar, side) -- never re-fire the same raid
        setup_key = (int(last[f"s_{tag}"]), side)
        if state.get("last_setup") == setup_key:
            return None, []
        state["last_setup"] = setup_key

        entry = float(last[f"entry_{tag}"])
        tp_bps = float(last[f"tp_bps_{tag}"])
        tp = entry * (1.0 + side * tp_bps / 1e4)
        t1 = sd.tfs["1m"]
        atr1m = (float(t1.atr[i])
                 if t1.atr is not None and i < len(t1.atr)
                 and t1.atr[i] and t1.atr[i] > 0 else 0.0)
        bias = 0
        if tf15.bias_dir is not None and 0 <= j15 < len(tf15.bias_dir):
            bias = int(tf15.bias_dir[j15])
        vp_poc = float("nan")
        if tf15.vp_poc is not None and 0 <= j15 < len(tf15.vp_poc):
            vp_poc = float(tf15.vp_poc[j15])

        # SL parity: the engine applies stop.atr_buffer_mult x atr1m on top
        # of swing_level; pre-compensate so the live stop lands exactly on
        # the core's computed level (sweep / FVG-edge / mid anchor + pad).
        sl_core = float(last[f"sl_{tag}"])
        buf_eng = self.cfg.stop["atr_buffer_mult"] * atr1m
        swing_level = sl_core + side * buf_eng

        sig = EntrySignal(
            symbol=sd.sym, direction=side, bar=i,
            at_ms=int(t_close),
            entry_price=float(row["close"]),
            swing_level=swing_level,
            entry_model="fvg", bias=bias, vp_poc=vp_poc,
            atr1m=atr1m,
            tp_first=tp,
            entry_level=entry,
            entry_expiry_bars=int(p.retest_bars * self.tf_minutes))
        return sig, []
