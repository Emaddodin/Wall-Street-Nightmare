"""VP scalper strategy (the operator's system prompt) -- moved out of the
monolithic entry engine into the farm."""
from __future__ import annotations

import numpy as np

from entry_engine import EntrySignal
from structure import LONG, SHORT

MIN_MS = 60_000
HOUR_MS = 3_600_000


class VpStrategy:
    name = "vp"

    def __init__(self, cfg):
        self.cfg = cfg

    def on_bar(self, sd, i: int, tf15, j15: int, t_close: int, state: dict):
        cfg = self.cfg
        t1 = sd.tfs["1m"]
        rej: list[dict] = []
        passed = 0

        def reject(leg: str, why: str) -> None:
            rej.append({"leg": leg, "why": why, "passed_legs": passed})

        # leg 1: 15m bias
        dir_ = int(tf15.bias_dir[j15])
        if dir_ == 0:
            reject("bias", "no 15m structure bias")
            return None, rej
        passed = 1

        # leg 2: session liquidity filter
        s = cfg.strategy["session"]
        if s["asia_sweep_filter"]:
            hour = (t_close // HOUR_MS) % 24
            lo_h, hi_h = s["london_hours_utc"]
            if lo_h <= hour < hi_h:
                swept = (bool(tf15.asia_swept_up[j15]) if dir_ == LONG
                         else bool(tf15.asia_swept_dn[j15]))
                if not swept:
                    reject("session", "asia high/low not swept (london window)")
                    return None, rej
        passed = 2

        # leg 3: VP level touch.
        # The operator's sequence is "price REACHES the level, THEN drop to
        # the 1m and wait for the shift" -- the touch and the ChoCH are two
        # events in order, not one bar doing both.  Requiring them on the
        # same bar (the original implementation) demands the ChoCH candle
        # itself straddle the level, which is a far narrower conjunction
        # than the system prompt describes.  The touch is therefore
        # satisfied by ANY of the last `vp_touch_window_bars` closed bars,
        # bar i included, and stays strictly causal (never reads past i).
        tol = cfg.strategy["vp_touch_tol_pct"]
        levels = [tf15.vp_vah[j15], tf15.vp_poc[j15], tf15.vp_val[j15]]
        win = max(1, int(cfg.strategy["vp_touch_window_bars"]))
        lo_i = max(0, i - win + 1)
        hi_seg = t1.h[lo_i:i + 1]
        lo_seg = t1.l[lo_i:i + 1]
        touched = False
        for lv in levels:
            if np.isnan(lv):
                continue
            if np.any((hi_seg >= lv * (1 - tol)) & (lo_seg <= lv * (1 + tol))):
                touched = True
                break
        if not touched:
            reject("vp_level", f"no VAH/POC/VAL touch in {win} bars")
            return None, rej
        passed = 3

        # leg 4: 1m ChoCH in the bias direction
        if dir_ == LONG:
            level = t1.last_sh[i]
            broke = bool(not np.isnan(level) and t1.c[i] > level)
        else:
            level = t1.last_sl[i]
            broke = bool(not np.isnan(level) and t1.c[i] < level)
        if not broke:
            reject("choch", f"close vs swing={level}")
            return None, rej
        passed = 4

        # leg 5: entry model present
        entry_model = None
        for m in cfg.strategy["entry_models"]:
            if m == "order_block":
                ok = bool(t1.ob_bull[i]) if dir_ == LONG else bool(t1.ob_bear[i])
            elif m == "fvg":
                ok = bool(t1.fvg_bull[i]) if dir_ == LONG else bool(t1.fvg_bear[i])
            elif m == "micro_poc":
                poc = t1.micro_poc[i]
                ok = (not np.isnan(poc) and t1.h[i] >= poc and t1.l[i] <= poc)
            else:
                ok = False
            if ok:
                entry_model = m
                break
        if entry_model is None:
            reject("entry_model", f"none of {cfg.strategy['entry_models']}")
            return None, rej
        passed = 5

        if dir_ == LONG:
            swing_level = t1.last_sl[i]
            tp_first = (tf15.last_sh[j15]
                        if (not np.isnan(tf15.last_sh[j15])
                            and tf15.last_sh[j15] > t1.c[i]) else None)
        else:
            swing_level = t1.last_sh[i]
            tp_first = (tf15.last_sl[j15]
                        if (not np.isnan(tf15.last_sl[j15])
                            and tf15.last_sl[j15] < t1.c[i]) else None)
        # ---- entry POINT: the model's own level, not a chase ----
        lvl = self._model_level(t1, i, entry_model, dir_)
        max_pct = cfg.strategy["limit_entry_max_pct"] / 100.0
        close = float(t1.c[i])
        if lvl is not None and abs(lvl - close) / close <= max_pct:
            entry_level = lvl
        else:
            entry_level = None            # model too far -> market next open
        sig = EntrySignal(
            symbol=sd.sym, direction=dir_, bar=i,
            at_ms=int(t1.t[i] + MIN_MS),
            entry_price=float(t1.c[i]),
            swing_level=float(swing_level),
            entry_model=entry_model,
            bias=dir_,
            vp_poc=float(tf15.vp_poc[j15]) if not np.isnan(tf15.vp_poc[j15]) else 0.0,
            entry_level=entry_level,
            entry_expiry_bars=int(cfg.strategy["limit_expiry_bars"]),
            atr1m=float(t1.atr[i]),
            tp_first=tp_first)
        return sig, rej

    def _model_level(self, t1, i: int, model: str, dir_: int):
        """The limit price implied by the entry model:
        OB -> the block's mean threshold (O+C)/2,
        FVG -> the gap's midpoint (CE),
        micro-POC -> the leg's POC."""
        if model == "order_block":
            arr = t1.ob_bull_mt if dir_ == 1 else t1.ob_bear_mt
            v = arr[i]
            return float(v) if (not np.isnan(v) and v > 0) else None
        if model == "fvg":
            arr = t1.fvg_bull_ce if dir_ == 1 else t1.fvg_bear_ce
            v = arr[i]
            return float(v) if (not np.isnan(v) and v > 0) else None
        if model == "micro_poc":
            v = t1.micro_poc[i]
            return float(v) if (not np.isnan(v) and v > 0) else None
        return None
