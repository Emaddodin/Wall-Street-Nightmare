"""The lab (AI strategy inventor): guardrails, memory votes, scoring."""
import json
import random
from pathlib import Path

import pytest

from strategies import inventor as inv
from config.loader import load_config


def _base() -> dict:
    return load_config(extra_file="config/aggressive.yaml").raw()


def test_mutate_changes_something_and_stays_valid():
    from config.loader import Config
    rng = random.Random(7)
    base = _base()
    cand, changes = inv.mutate(base, rng)
    assert changes
    assert inv.dict_fingerprint(cand) != inv.dict_fingerprint(base)
    Config(cand)  # raises if the idea is invalid
    # only known parts may differ
    for ch in changes:
        path = ch.split(":")[0].strip()
        assert path in inv.PARTS or path == "strategy.entry_models"


def test_cross_mixes_parents():
    rng = random.Random(3)
    base = _base()
    a, _ = inv.mutate(base, rng)
    b, _ = inv.mutate(base, rng)
    kid = inv.cross(a, b, rng)
    from config.loader import Config
    Config(kid)


def test_stats_math():
    trades = [
        {"pnl": 10.0, "pnl_r": 1.0},
        {"pnl": 5.0, "pnl_r": 0.5},
        {"pnl": -4.0, "pnl_r": -0.4},
        {"pnl": -1.0, "pnl_r": -0.1},
    ]
    s = inv.stats_from_trades(trades)
    assert s["n"] == 4
    assert s["wins"] == 2
    assert s["wr"] == pytest.approx(0.5)
    assert s["pf"] == pytest.approx(15.0 / 5.0)
    assert s["avg_r"] == pytest.approx(0.25)
    assert inv.stats_from_trades([])["n"] == 0


def test_adoptable_guardrails():
    ic = {"min_trades": 8, "min_wr": 0.55, "min_avg_r": 0.15,
          "min_pf": 1.3, "max_dd_pct": 25.0}
    good = {"n": 12, "wr": 0.6, "avg_r": 0.3, "pf": 1.6, "max_dd_pct": 10.0}
    assert inv.adoptable(good, ic)[0] is True
    assert inv.adoptable({**good, "n": 4}, ic)[0] is False
    assert inv.adoptable({**good, "wr": 0.4}, ic)[0] is False
    assert inv.adoptable({**good, "avg_r": 0.05}, ic)[0] is False
    assert inv.adoptable({**good, "pf": 1.1}, ic)[0] is False
    assert inv.adoptable({**good, "max_dd_pct": 40.0}, ic)[0] is False


def test_brain_votes_penalises_known_losers(tmp_path: Path):
    lessons = tmp_path / "lessons.jsonl"
    rows = [
        {"ts_ms": 10 * 3_600_000, "symbol": "LOSERUSDT", "pnl": -5.0},
        {"ts_ms": 10 * 3_600_000 + 60_000, "symbol": "LOSERUSDT", "pnl": -3.0},
        {"ts_ms": 11 * 3_600_000, "symbol": "WINUSDT", "pnl": 4.0},
    ]
    lessons.write_text("\n".join(json.dumps(r) for r in rows))
    bad = [{"ts_ms": 10 * 3_600_000, "symbol": "LOSERUSDT", "pnl": -1.0},
           {"ts_ms": 12 * 3_600_000, "symbol": "OKUSDT", "pnl": 1.0}]
    penalty, ev = inv.brain_votes(lessons, bad)
    assert penalty > 0
    assert 10 in ev["bad_hours"]
    assert "LOSERUSDT" in ev["bad_symbols"]
    good = [{"ts_ms": 15 * 3_600_000, "symbol": "OKUSDT", "pnl": 1.0}]
    p2, _ = inv.brain_votes(lessons, good)
    assert p2 == 0.0


def test_coin_kind_and_recommendation():
    assert inv.coin_kind(2.0, 300_000_000) == "major-normal"
    assert inv.coin_kind(0.5, 30_000_000) == "mid-calm"
    assert inv.coin_kind(6.0, 5_000_000) == "micro-wild"
    metas = {"X": {"kind": "micro-wild"}, "Y": {"kind": "mid-calm"}}
    trades = []
    for i in range(8):
        trades.append({"symbol": "X", "pnl": 2.0, "pnl_r": 0.5})
    for i in range(8):
        trades.append({"symbol": "Y", "pnl": -1.0, "pnl_r": -0.3})
    ins = inv.coin_insights(trades, metas)
    calls = {r["kind"]: r["call"] for r in ins["by_kind"]}
    assert calls["micro-wild"] == "trade MORE"
    assert calls["mid-calm"] == "trade LESS"


def test_warmup_constant_matches_engine():
    from config.loader import load_config
    cfg = load_config(extra_file="config/aggressive.yaml")
    need_min = cfg.backtest["warmup_15m_bars"] * 15 * 60_000
    assert inv.WARMUP_MS >= need_min
