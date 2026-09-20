"""perch must never ask for more coins than there are windows to hold them.

TradingView streams only TWO charts and the scout owns one, so exactly ONE
window can hold a coin. Asking for two makes the guard report a fault it can
never repair -- on 2026-09-06, "CASHCATUSDT missing from the charts (rebuild
did not take)" every two minutes with an open position.
"""
import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("perch", ROOT / "perch.py")
perch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(perch)


@pytest.fixture
def wanted(tmp_path, monkeypatch):
    f = tmp_path / "chart_coins.json"
    monkeypatch.setattr(perch, "WANTED", f)
    return f


def test_only_one_coin_is_ever_asked_for(wanted, monkeypatch):
    monkeypatch.setattr(perch, "held", lambda: {"CASHCATUSDT"})
    monkeypatch.setattr(perch, "ripest", lambda: ["NOMUSDT", "ARBUSDT"])
    assert perch.main() == 0
    assert json.loads(wanted.read_text()) == ["CASHCATUSDT"]


def test_a_held_coin_beats_a_ripe_one(wanted, monkeypatch):
    """The window belongs to the position, not to the next idea."""
    monkeypatch.setattr(perch, "held", lambda: {"CASHCATUSDT"})
    monkeypatch.setattr(perch, "ripest", lambda: ["NOMUSDT"])
    perch.main()
    assert json.loads(wanted.read_text()) == ["CASHCATUSDT"]


def test_with_nothing_held_the_ripest_gets_the_window(wanted, monkeypatch):
    monkeypatch.setattr(perch, "held", lambda: set())
    monkeypatch.setattr(perch, "ripest", lambda: ["NOMUSDT", "ARBUSDT"])
    perch.main()
    assert json.loads(wanted.read_text()) == ["NOMUSDT"]


def test_nothing_held_and_nothing_ripe_leaves_the_chart_alone(wanted, monkeypatch):
    monkeypatch.setattr(perch, "held", lambda: set())
    monkeypatch.setattr(perch, "ripest", lambda: [])
    assert perch.main() == 0
    assert not wanted.exists()


def test_holdable_matches_the_windows_the_scout_leaves_free():
    """MAX_WINDOWS is 2 and the scout owns one of them."""
    guard_src = (ROOT / "guard.py").read_text()
    assert "MAX_WINDOWS = 2" in guard_src
    assert perch.HOLDABLE == 1


def test_the_guard_never_gives_up_on_the_charts_forever():
    """Three failed rebuilds used to make the guard permanently passive.

    The strike counter only grew, so once past three the guard reported the
    fault every two minutes and never attempted a repair again -- even after
    the cause was gone. On 2026-09-06 fixing perch was not enough on its own;
    the guard had already stopped and stayed stopped until restarted by hand.
    """
    src = (ROOT / "guard.py").read_text()
    assert "CHART_RETRY_AFTER" in src
    give_up = src[src.index("rebuild did not take"):]
    give_up = give_up[:give_up.index("\n\n")] if "\n\n" in give_up else give_up
    assert 'strikes["charts"] = 1' in give_up, (
        "the give-up branch must reset the counter so the rebuild path "
        "runs again after a cooling-off period")
