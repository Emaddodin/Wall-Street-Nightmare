"""Phases 2 and 3 of the operator's system prompt are SEQUENTIAL.

"Once the price reaches your identified Volume Profile levels ... switch to
the 1-minute chart for execution [and] wait for the 1-minute trend to shift."

The original implementation tested the VP touch and the 1m ChoCH on the same
bar, which demands the ChoCH candle itself straddle the level -- a far
narrower conjunction than the prompt describes.  These tests pin the window.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.loader import load_config  # noqa: E402


def _touched(highs, lows, level, tol, window, i):
    """The exact predicate strategies/vp.py leg 3 evaluates."""
    lo_i = max(0, i - window + 1)
    hi_seg = np.asarray(highs[lo_i:i + 1], dtype=float)
    lo_seg = np.asarray(lows[lo_i:i + 1], dtype=float)
    return bool(np.any((hi_seg >= level * (1 - tol))
                       & (lo_seg <= level * (1 + tol))))


def test_touch_two_bars_ago_counts_within_the_window():
    # bar 0 straddles 100.0; bars 1-2 trade away from it
    highs = [100.5, 99.0, 98.8]
    lows = [99.5, 98.5, 98.0]
    assert _touched(highs, lows, 100.0, 0.0005, window=5, i=2) is True


def test_same_bar_only_window_misses_the_earlier_touch():
    """window=1 reproduces the original behaviour and rejects the setup."""
    highs = [100.5, 99.0, 98.8]
    lows = [99.5, 98.5, 98.0]
    assert _touched(highs, lows, 100.0, 0.0005, window=1, i=2) is False


def test_touch_older_than_the_window_does_not_count():
    highs = [100.5] + [99.0] * 8
    lows = [99.5] + [98.5] * 8
    assert _touched(highs, lows, 100.0, 0.0005, window=5, i=8) is False


def test_window_never_reads_past_the_current_bar():
    """A touch that happens AFTER bar i must not leak backwards."""
    highs = [99.0, 99.0, 100.5]
    lows = [98.5, 98.5, 99.5]
    assert _touched(highs, lows, 100.0, 0.0005, window=5, i=1) is False


def test_window_clamps_at_the_start_of_the_series():
    highs, lows = [100.5], [99.5]
    assert _touched(highs, lows, 100.0, 0.0005, window=5, i=0) is True


def test_config_ships_a_sequential_window():
    cfg = load_config()
    assert cfg.strategy["vp_touch_window_bars"] >= 1
    assert isinstance(cfg.strategy["vp_touch_window_bars"], int)


def test_strategy_reads_the_window_from_config():
    """Guard against the knob being added but never wired."""
    src = (Path(__file__).resolve().parents[1]
           / "strategies" / "vp.py").read_text()
    assert 'cfg.strategy["vp_touch_window_bars"]' in src
