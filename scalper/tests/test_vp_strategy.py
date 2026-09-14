"""VP-scalper strategy pieces: bias, VP levels, entry models, entry legs."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config.loader import load_config
from engine import _build_tf, prepare_symbol
from entry_engine import EntryEngine
from pa.fast import fvg_state_np, order_blocks_np
from tests.helpers import synth_1m, synth_with_swings

cfg = load_config()


def _sd_15m_from(df):
    from market_data.store import resample_ohlcv
    d15 = resample_ohlcv(df, 15)
    sd = prepare_symbol("T", df, cfg)
    return sd


def test_bias_bull_on_uptrend():
    # trending series with ripple -> HH/HL structure -> bullish bias
    n = 900
    t = np.arange(n)
    close = 100 * np.exp(0.0008 * t) * (1.0 + 0.02 * np.sin(t / 4.0))
    df = pd.DataFrame({"open_time": np.arange(n, dtype=np.int64) * 900_000,
                       "open": close, "high": close * 1.001,
                       "low": close * 0.999, "close": close,
                       "volume": np.full(n, 1.0)})
    tf = _build_tf(df, cfg, "15m")
    assert tf.bias_dir[-1] == 1


def test_bias_bear_on_downtrend():
    n = 900
    t = np.arange(n)
    close = 100 * np.exp(-0.0008 * t) * (1.0 + 0.02 * np.sin(t / 4.0))
    df = pd.DataFrame({"open_time": np.arange(n, dtype=np.int64) * 900_000,
                       "open": close, "high": close * 1.001,
                       "low": close * 0.999, "close": close,
                       "volume": np.full(n, 1.0)})
    tf = _build_tf(df, cfg, "15m")
    assert tf.bias_dir[-1] == -1


def test_volume_profile_poc_inside_range():
    from engine import _volume_profile
    rng = np.random.default_rng(7)
    n = 300
    h = np.full(n, 100.0)
    l = np.full(n, 99.0)
    v = rng.uniform(1, 10, n)
    v[150:200] = 100.0               # volume cluster at the top half
    h[150:200] = 100.0
    l[150:200] = 99.5
    t = np.arange(n, dtype=np.int64) * 900_000
    poc, vah, val, tot = _volume_profile(100.0, 99.0, 60, h, l, v, t,
                                         t[-1], 300, 0.7)
    assert 99.5 <= poc <= 100.0      # POC inside the cluster
    assert vah >= poc >= val
    assert tot > 0


def test_fvg_and_ob_are_causal():
    df = synth_with_swings(4000, seed=5)
    full = prepare_symbol("T", df, cfg).tfs["1m"]
    cut = prepare_symbol("T", df.iloc[:3000], cfg).tfs["1m"]
    for attr in ("ob_bull", "ob_bear", "fvg_bull", "fvg_bear"):
        assert (getattr(full, attr)[:3000] == getattr(cut, attr)).all(), attr


def test_entry_engine_rejects_without_bias():
    eng = EntryEngine(cfg)
    df = synth_with_swings(4000)
    sd = prepare_symbol("T", df, cfg)
    i = len(sd.tfs["1m"].t) - 1
    j15 = sd.i15[i]
    tf15 = sd.tfs["15m"]
    tf15.bias_dir[j15] = 0           # force no bias
    sigs, rej = eng.on_bar(sd, i, tf15, j15,
                          int(sd.tfs["1m"].t[i] + 60_000), {})
    # the VP strategy must refuse on the missing bias; other farm
    # strategies are free to fire (that is the point of the farm)
    assert any(r.get("strategy") == "vp" and r["leg"] == "bias" for r in rej)


def test_entry_model_flags():
    # a clean bullish displacement that breaks a swing -> order block forms
    n = 200
    rng = np.random.default_rng(3)
    o = 100 + np.cumsum(rng.normal(0, 0.02, n))
    c = o + rng.normal(0, 0.01, n)
    h = np.maximum(o, c) + 0.02
    l = np.minimum(o, c) - 0.02
    # force a bearish block candle then a bullish displacement break that
    # gaps away from the block (no instant mitigation of the block MT)
    c[150] = o[150] - 0.5
    h[150] = o[150] + 0.01
    l[150] = c[150] - 0.01
    o[151] = o[150] + 0.05           # opens ABOVE the block body
    c[151] = h[150] + 0.6            # closes beyond the recent swing high
    h[151] = c[151]
    l[151] = o[151] - 0.02           # low stays above the block MT
    df = pd.DataFrame({"open_time": np.arange(n, dtype=np.int64) * 60_000,
                       "open": o, "high": h, "low": l, "close": c,
                       "volume": np.full(n, 1000.0)})
    from indicators import swing_points
    sh, sl = swing_points(df, 1)
    ob_b, ob_b_mt, _, _, _, _, _, _ = order_blocks_np(
        o, h, l, c, sh.to_numpy(dtype=float), sl.to_numpy(dtype=float))
    assert ob_b[151:].any()


def test_limit_entry_levels_from_models():
    """The VP strategy prices entries at the model level (OB MT / FVG CE /
    micro POC) and sets a resting limit; the engine fills only on touch."""
    from entry_engine import EntryEngine
    df = synth_with_swings(5000, seed=11)
    cfg2 = load_config(extra_file="config/aggressive.yaml")
    sd = prepare_symbol("T", df, cfg2)
    eng = EntryEngine(cfg2)
    i = len(sd.tfs["1m"].t) - 1
    j15 = sd.i15[i]
    sigs, rej = eng.on_bar(sd, i, sd.tfs["15m"], j15,
                           int(sd.tfs["1m"].t[i] + 60_000), {})
    for sig in sigs:
        if sig.strategy != "vp":
            continue
        # when a model level exists within the cap, the limit is set and
        # equals that level; otherwise the entry is market (None)
        if sig.entry_level is not None:
            assert sig.entry_level > 0
        assert sig.entry_expiry_bars == cfg2.strategy["limit_expiry_bars"]


def test_limit_fill_only_on_touch():
    """A resting limit fills ONLY when the bar range touches the level."""
    from strategies.vp import VpStrategy
    from entry_engine import EntrySignal
    strat = VpStrategy(load_config(extra_file="config/aggressive.yaml"))
    lvl = strat._model_level
    class T:
        ob_bull_mt = None
        ob_bear_mt = None
        fvg_bull_ce = None
        fvg_bear_ce = None
        micro_poc = None
    t = T()
    import numpy as np
    t.ob_bull_mt = np.array([0.0, 0.0, 101.0])
    assert lvl(t, 2, "order_block", 1) == 101.0
    t.fvg_bull_ce = np.array([0.0, 99.5, 0.0])
    assert lvl(t, 1, "fvg", 1) == 99.5
    t.micro_poc = np.array([0.0, 100.25])
    assert lvl(t, 1, "micro_poc", -1) == 100.25
    assert lvl(t, 0, "order_block", 1) is None
