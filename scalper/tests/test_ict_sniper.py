"""ICT Sniper: detector gates, spec bounds, causality, farm registration.

The synthetic frame encodes the exact ICT chain on a 15m-like series:
  bar 5: swing low 99.00 (confirmed at bar 7)
  bar 6: swing high 99.35 (confirmed at bar 8)
  bar 8: SSL sweep -- wick to 98.95 (>= 60% of range), close back above
  bar 9: displacement bar closes above the swing high (MSS)
  bar 10: bullish FVG completes -> limit at CE, SL beyond the sweep wick.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config.loader import load_config
from pa.sniper import SniperParams, detect_setups, detect_states
from strategies import make_strategies


def synth_sniper_df() -> pd.DataFrame:
    # 8 quiet warmup bars (disp avg window needs >= 10 prior bars; ATR 14),
    # then the encoded chain:
    #   bar 13: swing low 99.00 (confirmed 15); bar 14: swing high 99.35
    #           (confirmed 16)
    #   bar 16: SSL sweep -- wick to 98.95, close back above
    #   bar 17: displacement bar closes above the swing high (MSS)
    #   bar 18: bullish FVG completes -> limit at CE
    o = [99.30] * 8 + [99.20, 99.25, 99.22, 99.28, 99.22, 99.10, 99.08,
                       99.15, 98.99, 99.10, 99.65, 99.70, 99.66, 99.72, 99.68]
    h = [99.34] * 8 + [99.28, 99.30, 99.30, 99.31, 99.28, 99.20, 99.35,
                       99.20, 99.03, 99.75, 99.75, 99.80, 99.78, 99.80, 99.76]
    lo = [99.26] * 8 + [99.16, 99.18, 99.15, 99.22, 99.10, 99.00, 99.04,
                        99.06, 98.95, 99.05, 99.60, 99.62, 99.58, 99.64, 99.60]
    c = [99.32] * 8 + [99.25, 99.22, 99.28, 99.24, 99.12, 99.08, 99.15,
                       99.05, 99.03, 99.70, 99.72, 99.66, 99.72, 99.68, 99.72]
    t = np.arange(len(o), dtype=np.int64) * 900_000
    return pd.DataFrame({"open_time": t, "open": o, "high": h,
                         "low": lo, "close": c, "volume": 1.0})


SWEEP_BAR = 16
MSS_BAR = 17
EMIT_BAR = 18


def test_single_long_setup_at_expected_bar():
    df = synth_sniper_df()
    p = SniperParams()
    st = detect_states(df, p)
    assert int(st["emit_l"].sum()) == 1, "exactly one long emission"
    i = int(np.nonzero(st["emit_l"].to_numpy())[0][0])
    assert i == EMIT_BAR
    assert not st["emit_s"].any()
    row = st.iloc[i]
    assert int(row["s_l"]) == SWEEP_BAR                 # swept bar
    assert int(row["m_l"]) == MSS_BAR                   # MSS bar
    assert abs(row["entry_l"] - 99.315) < 1e-6          # FVG CE
    assert row["sl_l"] < 99.00                          # beyond the sweep wick
    assert 0 < row["sl_bps_l"] <= p.max_sl_bps
    assert p.tp_min_bps <= row["tp_bps_l"] <= p.tp_max_bps + 1e-6
    assert row["rr_l"] >= p.rr_min                      # strict 1:2.5


def test_setup_bounds_on_emissions():
    df = synth_sniper_df()
    setups = detect_setups(df, SniperParams())
    assert len(setups) == 1
    s = setups.iloc[0]
    assert s.side == 1
    assert s.entry < s.close                          # limit below price
    assert s.tp_bps >= 300.0 and s.tp_bps <= 500.001   # runner band
    assert s.rr >= 2.5 and s.sl_bps <= 200.0
    assert s.sweep_bar == SWEEP_BAR and s.mss_bar == MSS_BAR


def test_causality_truncation_invariance():
    df = synth_sniper_df()
    p = SniperParams()
    full = detect_states(df, p)
    trunc = detect_states(df.iloc[:-1], p)
    # rows beyond len-1-arm legitimately lose right-arm swing confirmation
    cut = len(trunc) - p.arm
    a = full.iloc[:cut].fillna(-1).values
    b = trunc.iloc[:cut].fillna(-1).values
    assert a.shape == b.shape and (a == b).all()


def test_farm_registry_and_params():
    cfg = load_config({"strategies.enabled": ["ict_sniper"]})
    strats = make_strategies(cfg)
    s = strats["ict_sniper"]
    assert s.name == "ict_sniper"
    assert s.tf_minutes == 5
    assert s.p.rr_min == 2.5
    assert s.p.wick_frac == 0.45
    assert s.p.sl_mode == "sweep"
    # phase-7 production spec (paper-trading candidate)
    assert s.p.tp_min_bps == 400 and s.p.tp_max_bps == 400
    assert s.p.tp1_bps == 120 and s.p.tp1_frac == 0.75
    assert s.p.be_after_r == 0.75
    assert s.p.time_exit_bars == 80


def test_sl_mode_fvg_edge_vs_sweep():
    """fvg_edge: SL at the far (opposite) edge of the gap + pad; sweep:
    SL beyond the sweep wick + pad."""
    df = synth_sniper_df()
    st_edge = detect_states(df, SniperParams(sl_mode="fvg_edge"))
    st_sweep = detect_states(df, SniperParams(sl_mode="sweep"))
    i = EMIT_BAR
    # bull gap = [h[16], l[18]] = [99.03, 99.60]; entry at CE 99.315
    sl_edge = float(st_edge["sl_l"].iloc[i])
    sl_sweep = float(st_sweep["sl_l"].iloc[i])
    assert sl_edge < 99.03 and sl_edge > 98.9        # edge minus ~0.25*ATR
    assert sl_sweep < 98.95                          # beyond the sweep low
    assert sl_edge > sl_sweep                        # tighter structural stop
    assert float(st_edge["sl_bps_l"].iloc[i]) < \
        float(st_sweep["sl_bps_l"].iloc[i])


def test_sl_mode_mid_is_between_edge_and_sweep():
    """mid: the exact midpoint of (FVG far edge, sweep wick extreme)."""
    df = synth_sniper_df()
    st_mid = detect_states(df, SniperParams(sl_mode="mid"))
    st_edge = detect_states(df, SniperParams(sl_mode="fvg_edge"))
    st_sweep = detect_states(df, SniperParams(sl_mode="sweep"))
    i = EMIT_BAR
    sl_mid = float(st_mid["sl_l"].iloc[i])
    sl_edge = float(st_edge["sl_l"].iloc[i])
    sl_sweep = float(st_sweep["sl_l"].iloc[i])
    # edge (99.03 - pad) > mid > sweep (98.95 - pad): strictly between
    assert sl_edge > sl_mid > sl_sweep
    # with zero pad the mid is the arithmetic midpoint of the anchors
    st_mid0 = detect_states(df, SniperParams(sl_mode="mid",
                                             sl_buffer_atr_mult=0.0))
    assert float(st_mid0["sl_l"].iloc[i]) == pytest.approx((99.03 + 98.95) / 2)


def test_params_reject_bad_values():
    with pytest.raises(ValueError):
        SniperParams(time_exit_bars=150)    # runner horizon cap: 120
    with pytest.raises(ValueError):
        SniperParams(entry_at="middle")
    with pytest.raises(ValueError):
        SniperParams(tp_mode="martingale")
    with pytest.raises(ValueError):
        SniperParams(fvg_mitigation="sometimes")
    with pytest.raises(ValueError):
        SniperParams(tp1_frac=1.0)
    with pytest.raises(ValueError):
        SniperParams(sl_mode="wick")


def test_relaxed_fvg_mitigation_survives_wicks():
    """Partial-mitigation model: a wick into the gap keeps it alive, a
    CLOSE beyond the CE kills it."""
    df = synth_sniper_df()
    # bar 19 wicks below the CE (99.315) but closes back above -> alive
    # under "close", dead under "wick"
    wick_row = {"open_time": df["open_time"].iloc[-1] + 900_000,
                "open": 99.62, "high": 99.70, "low": 99.20, "close": 99.55,
                "volume": 1.0}
    df_w = pd.concat([df, pd.DataFrame([wick_row])], ignore_index=True)
    st_close = detect_states(df_w, SniperParams(fvg_mitigation="close"))
    st_wick = detect_states(df_w, SniperParams(fvg_mitigation="wick"))
    i = len(df_w) - 1
    assert bool(st_close["bull_fvg"].iloc[i])          # survives the wick
    assert not bool(st_wick["bull_fvg"].iloc[i])       # library rule kills it
    # a candle that CLOSES beyond the CE kills it under both rules
    close_row = {"open_time": df["open_time"].iloc[-1] + 900_000,
                 "open": 99.62, "high": 99.70, "low": 99.10, "close": 99.12,
                 "volume": 1.0}
    df_c = pd.concat([df, pd.DataFrame([close_row])], ignore_index=True)
    st_c = detect_states(df_c, SniperParams(fvg_mitigation="close"))
    assert not bool(st_c["bull_fvg"].iloc[len(df_c) - 1])
