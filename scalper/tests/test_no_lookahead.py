"""The no-lookahead contract for the VP scalper engine."""
from __future__ import annotations

import numpy as np
import pandas as pd

from config.loader import load_config
from engine import prepare_symbol
from tests.helpers import synth_1m, synth_with_swings

cfg = load_config()


def _arrays_of(sd):
    out = {}
    for tf_name, tf in sd.tfs.items():
        for attr in ("t", "o", "h", "l", "c", "v", "atr", "swing_hi",
                     "swing_lo", "last_sh", "last_sl", "spread24h",
                     "ob_bull", "ob_bear", "fvg_bull", "fvg_bear",
                     "micro_poc", "bias_dir", "vp_vah", "vp_poc", "vp_val",
                     "asia_high", "asia_low", "asia_swept_up",
                     "asia_swept_dn", "atr_z", "vol24h"):
            val = getattr(tf, attr, None)
            if val is not None:
                out[f"{tf_name}.{attr}"] = np.asarray(val)
    return out


def test_append_future_bars_does_not_change_known_state():
    full = synth_with_swings(8000)
    cut = full.iloc[:7000].reset_index(drop=True)
    sd_full = prepare_symbol("T", full, cfg)
    sd_cut = prepare_symbol("T", cut, cfg)
    a_full, a_cut = _arrays_of(sd_full), _arrays_of(sd_cut)
    for name in a_full:
        f, c = a_full[name], a_cut[name]
        n = min(len(f), len(c))
        assert np.allclose(f[:n], c[:n], equal_nan=True), \
            f"future leaked into {name}"


def test_resample_drops_partial_bars():
    from market_data.store import resample_ohlcv
    df = synth_1m(10)
    d3 = resample_ohlcv(df, 3)
    assert len(d3) == 3
    d15 = resample_ohlcv(df, 15)
    assert len(d15) == 0


def test_resample_alignment():
    from market_data.store import resample_ohlcv
    df = synth_1m(45)
    d15 = resample_ohlcv(df, 15)
    assert list(d15["open_time"]) == list(df["open_time"].iloc[::15])


def test_entry_at_next_open_only():
    from execution import entry_price
    from structure import LONG
    px, fee = entry_price(100.0, LONG, 6.0, 2.0)
    assert px == pytest.approx(100.02)
    px_s, _ = entry_price(100.0, -LONG, 6.0, 2.0)
    assert px_s < 100.0


import pytest  # noqa: E402  (kept at bottom so the module loads cleanly)
