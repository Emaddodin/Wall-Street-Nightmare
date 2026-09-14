"""Engine determinism + config validation + end-to-end confluence smoke."""
from __future__ import annotations

import pytest

from config.loader import ConfigError, load_config
from engine import BacktestEngine
from market_data.store import CandleStore
from tests.helpers import synth_with_swings


def test_config_defaults_and_aggressive_load():
    assert load_config().tp["model"] == "D"
    a = load_config(extra_file="config/aggressive.yaml")
    assert a.risk["risk_per_trade"] == pytest.approx(0.25)
    assert a.risk["leverage_cap"] == pytest.approx(50.0)
    assert a.daily["target_pct"] == pytest.approx(1.0)
    assert a.tp["model"] == "D"
    assert a.tp["structure"]["runner_exit"] == "bos"


def test_config_rejects_bad_enum():
    with pytest.raises(ConfigError):
        load_config(overrides={"tp.model": "Z"})
    with pytest.raises(ConfigError):
        load_config(overrides={"tp.structure.runner_exit": "weird"})


def test_config_rejects_partial_fractions():
    with pytest.raises(ConfigError):
        load_config(overrides={"tp.partial": {"tp1_frac": 0.6, "tp2_frac": 0.25,
                                              "runner_frac": 0.25}})


def test_engine_deterministic(tmp_path):
    """Same data + config -> bit-identical trades, twice."""
    df = synth_with_swings(9000)
    store = CandleStore(str(tmp_path))
    store.save("T", df, "1m")
    cfg = load_config(extra_file="config/aggressive.yaml",
                      overrides={"universe.top_n": 5})
    end = int(df["open_time"].max())
    start = end - 6 * 86_400_000
    frames = {"T": df}
    eng = BacktestEngine(cfg)
    sds = eng.prepare(frames, start, end)
    r1 = eng.run(sds, start, end, starting_equity=100.0)
    eng2 = BacktestEngine(cfg)
    sds2 = eng2.prepare(frames, start, end)
    r2 = eng2.run(sds2, start, end, starting_equity=100.0)
    assert [t["pnl"] for t in r1.trades] == [t["pnl"] for t in r2.trades]
    assert [t["exit"] for t in r1.trades] == [t["exit"] for t in r2.trades]
    assert r1.equity_curve == r2.equity_curve


def test_fees_and_slippage_applied_to_every_trade(tmp_path):
    df = synth_with_swings(9000)
    store = CandleStore(str(tmp_path))
    store.save("T", df, "1m")
    cfg = load_config(extra_file="config/aggressive.yaml",
                      overrides={"universe.top_n": 5,
                                 "execution.fee_bps": 10.0,
                                 "execution.slippage_bps": 5.0})
    end = int(df["open_time"].max())
    start = end - 6 * 86_400_000
    eng = BacktestEngine(cfg)
    sds = eng.prepare({"T": df}, start, end)
    res = eng.run(sds, start, end, starting_equity=100.0)
    for t in res.trades:
        assert t["fee_bps"] == 10.0
        assert t["slippage_bps"] == 5.0
        assert t["entry_fee"] > 0 and t["exit_fee"] > 0
