"""
The paper venue: fills come from the REAL order book, sizes obey the REAL
minimum, and the learned filter reloads when the nightly rebuild lands.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import paper_venue as V  # noqa: E402
from dataset import engine  # noqa: E402


class Cli:
    def __init__(self, depth=None, spread=4.0, pairs=None):
        self._depth = depth
        self._spread = spread
        self._pairs = pairs or {}

    def depth(self, sym, limit=20):
        if self._depth is None:
            raise RuntimeError("no book")
        return self._depth

    def spread_bps(self, sym):
        return self._spread

    def trading_pairs(self):
        return self._pairs


def test_market_fill_is_the_books_volume_weighted_average():
    cli = Cli(depth={"asks": [["1.00", "10"], ["1.01", "10"]]})
    px, frac, known = V.depth_fill(cli, "XUSDT", "BUY", 15.0, 1.0)
    # $10 at 1.00 then $5 at 1.01: vwap by value = 15/(10 + 5/1.01)
    assert abs(px - 15 / (10 + 5 / 1.01)) < 1e-9
    assert frac == 1.0 and known


def test_a_thin_book_fills_only_part_of_the_size():
    cli = Cli(depth={"asks": [["1.00", "5"]]})
    px, frac, known = V.depth_fill(cli, "XUSDT", "BUY", 20.0, 1.0)
    assert px == 1.0 and abs(frac - 0.25) < 1e-9 and known


def test_no_book_falls_back_to_half_spread_and_says_so():
    cli = Cli(spread=10.0)
    px, frac, known = V.depth_fill(cli, "XUSDT", "BUY", 10.0, 1.0)
    assert abs(px - 1.0 * (1 + 5 / 1e4)) < 1e-9
    assert frac == 1.0 and not known


def test_the_real_minimum_size_is_enforced():
    cli = Cli(pairs={"XUSDT": {"minTradeVolume": "5",
                               "basePrecision": 3, "quotePrecision": 4,
                               "maxLeverage": 50}})
    assert V.enforce_min_qty(cli, "XUSDT", 2.0) is not None
    assert V.enforce_min_qty(cli, "XUSDT", 6.0) is None


def _artifact(path, threshold=0.1, trained_at=1):
    n = 13
    np.savez_compressed(path, w=np.zeros(2 * n, dtype=np.float32), b=0.0,
                        mu=np.zeros(2 * n, dtype=np.float32),
                        sd=np.ones(2 * n, dtype=np.float32),
                        med=np.zeros(n, dtype=np.float32),
                        features=np.array(
                            ["atr", "trend", "vol20", "mom6h", "mom1h",
                             "volx", "agents", "tier", "score", "who",
                             "counter", "side", "hour"]),
                        threshold=float(threshold),
                        trained_at=int(trained_at), n=1)


def test_the_book_reloads_a_fresher_model_without_restart(tmp_path):
    art = tmp_path / "model.npz"
    _artifact(art, threshold=0.1, trained_at=1)
    prefix = engine.quiet_prefix(1788450300, 1.0)
    spec = {"symbol": "BITUNIX:XUSDT.P", "res": "15", "ohlc": prefix,
            "plots": {}}
    state = {"equity": 100.0, "start": 100.0, "trades": [], "seen": [],
             "resting": [], "scores": [], "known_syms": []}
    args = engine.argv({"--interval": "61"}) + [
        "--filter-model", str(art), "--filter-min", "0.05"]

    def on_poll(n, ex):
        if n == 1:
            _artifact(art, threshold=0.9, trained_at=2)

    # start away from the bar boundary: inside the 5s sprint window the
    # book polls every 0.2s and the 60s reload check never comes due
    book, _c, _m = engine.run(state, 1788450330, {0: spec},
                              {"XUSDT": 1.0}, args, polls=3,
                              on_poll=on_poll, tmpdir=str(tmp_path))
    assert any("filter: model reloaded" in ln and "0.900" in ln
               for ln in book.log), book.log
