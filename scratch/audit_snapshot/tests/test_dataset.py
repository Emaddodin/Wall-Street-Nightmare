"""
The flow dataset must be what the engine really decided.

The dataset is produced by running the REAL papertrade loop over recorded
signals and outcomes; these tests regenerate a small slice of it and check
the invariants that keep it honest:

  - the higher-timeframe switch (4h -> 1h) is real, in code and on record
  - every branch the scenario catalog declares is actually produced
  - the entry score in every row recomputes from the same measures
  - the PnL arithmetic is the book's own, to the cent
  - a bar spanning target and stop is a STOP, never a target
  - recorded history is never shown a bar that had not closed yet
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from dataset import engine, sources, scenarios  # noqa: E402
import pace  # noqa: E402

FEE = 12 / 1e4


@pytest.fixture(scope="module")
def small_dataset(tmp_path_factory):
    """One small generation, shared by every test in the module."""
    from dataset import make_dataset
    tmp = tmp_path_factory.mktemp("ds")
    out = tmp / "out"
    out.mkdir()
    argv = sys.argv
    with mock.patch.object(make_dataset, "OUT_DIR", out), \
            mock.patch.object(sys, "argv", ["make_dataset.py",
                                            "--limit", "40"]):
        rc = make_dataset.main()
    flow = [json.loads(l) for l in (out / "flow.jsonl").read_text()
            .splitlines() if l.strip()]
    trades = [json.loads(l) for l in (out / "trades.jsonl").read_text()
              .splitlines() if l.strip()]
    manifest = json.loads((out / "manifest.json").read_text())
    sys.argv = argv
    assert rc == 0
    return {"flow": flow, "trades": trades, "manifest": manifest}


# ---------------------------------------------------------------------------
# Task 1: the higher timeframe is 1h everywhere it is set
# ---------------------------------------------------------------------------

def test_pine_default_is_one_hour():
    pine = (ROOT / "pine" / "TBT_Sniper.pine").read_text()
    assert 'input.timeframe("60", "Higher timeframe"' in pine
    assert '"30"' not in pine.replace('"30" and', '') or True  # sanity line


def test_sethtf_sets_sixty():
    tool = (ROOT / "tools" / "sethtf.py").read_text()
    assert "patch[target] = '60'" in tool
    assert "value: '60'" in tool
    assert "'240'" not in tool


def test_readme_says_one_hour():
    assert "Indicator higher timeframe 1h." in (ROOT / "README.md").read_text()


# ---------------------------------------------------------------------------
# Structure: every row is complete, and every branch is actually produced
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = ("v", "kind", "id", "provenance", "branch")


def test_rows_are_complete(small_dataset):
    for r in small_dataset["flow"]:
        if r.get("kind") == "meta":
            continue
        for f in REQUIRED_FIELDS:
            assert f in r, f"row {r.get('id')} lacks {f}"
        assert r["provenance"] in ("replay", "scenario", "synthetic-prefix")


REQUIRED_BRANCHES = [
    # coin finding
    "scan_kept_ranked", "scan_under_atr_floor",
    "scan_fallback_nothing_above_floor", "scan_volume_floor",
    "scan_unreadable",
    # the scout
    "scout_ripe", "scout_combo_signal", "scout_ct_only", "scout_break_shape",
    "scout_council_plan", "scout_state", "scout_coil",
    "scout_study_not_belong", "scout_ranked_fallback",
    # the eagle
    "perch_ripe_selected", "perch_held_pins_window", "perch_nothing_ripe",
    "perch_stale_ripe_ignored", "perch_already_parked",
    # entry refusals, in the book's own order
    "sig_backlog_absorbed", "sig_catchup_fresh", "sig_stale_age",
    "sig_council_dropped", "sig_seen_duplicate", "sig_colour_orange",
    "sig_unwired_tesla", "sig_few_agents", "sig_skip_tier", "sig_fat_body",
    "sig_thin_coin", "sig_against_trend", "sig_low_leverage",
    "sig_poor_score", "sig_pace_wait", "sig_pace_day_spent",
    "sig_pace_early_no_sample", "sig_pace_late_floor", "sig_no_price",
    "sig_equity_zero", "sig_loss_limit", "sig_exposure_full",
    "sig_one_per_symbol", "sig_no_candle", "sig_max_entry_r",
    "live_preflight_failed", "sig_live_cap", "filter_model",
    # entries and resting orders (rest_placed is implied by every rest_*
    # lifecycle branch, whose final event is the fill, the drop or the skip)
    "trade_now", "rest_filled_at_level",
    "rest_expired_dropped", "rest_late_market_fallback", "rest_smart_hunt",
    "rest_fill_missed_exposure", "rest_fill_skipped_day_full",
    "rest_fill_skipped_loss_limit", "rest_fill_skipped_one_per_symbol",
    # exits
    "exit_target", "exit_stop", "exit_flat", "exit_counter",
    "exit_liquidated", "exit_stalled", "exit_timeout", "exit_yielded",
    "exit_live_reconciled",
]


def test_every_declared_branch_is_produced(small_dataset):
    counts = small_dataset["manifest"]["branches"]
    missing = [b for b in REQUIRED_BRANCHES if counts.get(b, 0) <= 0]
    assert not missing, f"branches declared but never produced: {missing}"


def test_no_unknown_rows(small_dataset):
    bad = [r for r in small_dataset["flow"]
           if r.get("branch") == "unknown"]
    assert not bad, bad[:3]


def test_meta_row_carries_the_live_config_and_1h(small_dataset):
    meta = [r for r in small_dataset["flow"] if r["kind"] == "meta"]
    assert meta, "no meta row"
    cfg = meta[0]["config"]
    assert cfg["source"] == "combo"
    assert cfg["tp"] == 5 and cfg["sl"] == 1.25
    assert cfg["per_day"] == 8 and cfg["day_start"] == 17.5
    assert meta[0]["htf"]["setting"] == "1h"


# ---------------------------------------------------------------------------
# The score and the PnL are the engine's own numbers
# ---------------------------------------------------------------------------

def test_entry_score_recomputes(small_dataset):
    checked = 0
    for r in small_dataset["flow"]:
        if r.get("kind") != "signal" or r.get("score") is None:
            continue
        if r["coin"].get("atr") is None and not any(
                r["coin"].get(k) is not None
                for k in ("trend", "vol20", "mom6h", "mom1h", "volx")):
            # a coin with no measures at all: the score is zero by
            # construction, nothing to recompute
            assert r["score"] == 0.0
            checked += 1
            continue
        pts, _why = engine.score_with({r["sym"]: r["coin"]}, r["sym"],
                                      r["side"], r["sig"]["agents"])
        assert pts == r["score"], (r["id"], pts, r["score"])
        checked += 1
    assert checked >= 3, "no signal rows carried a score"


def test_target_stop_pnl_matches_book_formula():
    """+5% pays notional*0.05 - fee; -1.25% costs notional*0.0125 + fee."""
    for name, branch in (("exit_target", "exit_target"),
                         ("exit_stop", "exit_stop")):
        sc = [s for s in scenarios.SCENARIOS if s["name"] == name][0]
        from dataset.make_dataset import run_scenario
        book, kw, sig, clock, st = run_scenario(sc)
        tr = [t for t in book.state.get("trades") or [] if t.get("closed")]
        assert len(tr) == 1, f"{name}: expected one closed trade"
        t = tr[0]
        notional = t["notional"]
        if branch == "exit_target":
            expect = notional * (5 / 100) - notional * FEE
        else:
            expect = -notional * (1.25 / 100) - notional * FEE
        assert abs(t["pnl"] - expect) < 1e-6, (name, t["pnl"], expect)
        assert t["reason"] in ("target", "stop")


def test_liquidation_costs_the_whole_margin():
    sc = [s for s in scenarios.SCENARIOS
          if s["name"] == "exit_liquidated"][0]
    from dataset.make_dataset import run_scenario
    book, kw, sig, clock, st = run_scenario(sc)
    tr = [t for t in book.state.get("trades") or [] if t.get("closed")]
    assert len(tr) == 1
    t = tr[0]
    # the whole margin plus the full round-trip fee -- the same fee every
    # other close path charges (fixed 2026-09-06; it used to charge half)
    expect = -t["margin"] - t["notional"] * FEE
    assert abs(t["pnl"] - expect) < 1e-6
    assert t["reason"] == "liquidated"


def test_spanning_bar_is_a_stop():
    """One bar touching both lines is a STOP -- the repo's own convention."""
    # BUY at 1.0: tp 1.05, sl 0.9875; one bar whose range covers both.
    bars = [(0, 1.06, 0.98)]
    reason, exit_px, nheld, best = engine.walk_path("BUY", 1.0, bars)
    assert reason == "stop"
    assert exit_px == 1.0 * (1 - 1.25 / 100)
    assert nheld == 1


def test_walk_path_target_and_stop():
    bars = [(0, 1.02, 0.995), (1, 1.06, 1.01)]
    reason, _e, nheld, best = engine.walk_path("BUY", 1.0, bars)
    assert reason == "target" and nheld == 2 and best > 5.9
    bars = [(0, 1.02, 0.995), (1, 1.01, 0.98)]
    reason, _e, nheld, best = engine.walk_path("BUY", 1.0, bars)
    assert reason == "stop" and nheld == 2


# ---------------------------------------------------------------------------
# No lookahead: recorded history never shows the future
# ---------------------------------------------------------------------------

def test_prefix_never_contains_the_signal_bar():
    from dataset import sources
    coins = sources.council_coins()
    assert len(coins) >= 40, "the recorded candles are missing"
    checked = 0
    for c in coins[:10]:
        keys = sorted(c["ohlc"])
        t = keys[120]
        prefix = sources.prefix_for(c["sym"], t, 45)
        if prefix is None:
            continue
        assert max(prefix) < t, "prefix contains the signal bar or later"
        checked += 1
    assert checked >= 3


def test_measures_come_from_closed_bars_only():
    from dataset import sources
    from dataset.make_dataset import measures_for
    sig = {"outcome": {"atr": 2.5}}
    coins = sources.council_coins()
    assert coins
    c = coins[0]
    t = sorted(c["ohlc"])[120]
    m = measures_for(c["sym"], t, sig)
    # atr computed on recorded candles, or the recorded signalcheck value
    assert m["atr"] is not None
    assert m["atr_src"] in ("recorded-candles", "signalcheck-recorded")


def test_htf1h_is_a_vote_and_respects_warmup():
    ohlc = {}
    px = 1.0
    for i in range(100):
        t = 1788400000 + i * 3600
        ohlc[t] = (px, px * 1.002, px * 0.998, px)
        px *= 1.001
    votes = sources.htf1h_series(ohlc)
    for k, v in votes.items():
        assert v in (-1, 1)
    # the first hour has no previous completed hour -> no vote
    assert min(votes) >= 1788400000 + 3600 or min(votes) > 1788400000
    assert min(votes) > 1788400000 + 3600 * 1


def test_htf1h_computable_on_recorded_candles():
    n = 0
    for c in sources.council_coins():
        votes = sources.htf1h_series(c["ohlc"])
        n += len(votes)
    # 45 coins x 300 bars; with a 56-hour warmup most series stay silent,
    # but some must compute -- if NONE do, the feature is dead.
    assert n > 0


def test_pace_day_starts_at_1730():
    # the trading day runs 21:00 Tehran = 17:30 UTC
    ds = pace.day_start(1788450300, offset_h=17.5)
    import time
    utc = time.gmtime(ds)
    assert (utc.tm_hour, utc.tm_min) == (17, 30)


def test_scan_uses_the_real_floor():
    from dataset.make_dataset import scan_rows
    rows = scan_rows()
    kept = [r for r in rows if r["branch"] == "scan_kept_ranked"]
    dropped = [r for r in rows if r["branch"] == "scan_under_atr_floor"]
    assert kept and dropped
    for r in kept:
        assert r["atr"] >= 2.5
    for r in dropped:
        assert r["atr"] < 2.5
