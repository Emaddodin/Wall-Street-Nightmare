"""The panel must never choose the chart coins while the eagle is flying.

Autopilot enforced its choice with `tbtctl coins`, which restarts the book.
perch.py owns data/chart_coins.json now, so the two fought over one file once
an hour and the loser's move cost a restart -- the same hourly restart that
once meant no resting order in the whole life of this book ever filled.
"""
import re
import pathlib


ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = (ROOT / "panel.py").read_text()


def test_autopilot_defaults_to_off():
    """A missing or unreadable autopilot.json must not re-enable switching."""
    body = re.search(r"def auto_state\(\):(.*?)\ndef ", SRC, re.S).group(1)
    assert '"on": False' in body, "auto_state must default 'on' to False"
    assert '"on": True' not in body


def test_switch_branch_does_not_default_on():
    """`st.get("on", True)` would switch whenever the key went missing."""
    assert 'st.get("on", True)' not in SRC


def test_perch_stands_autopilot_down():
    """The check must gate the switch, and come before the toggle."""
    assert "def perch_owns_charts" in SRC
    body = re.search(r"def autopilot\(\):(.*?)\ndef ", SRC, re.S).group(1)
    gate = body.index("perch_owns_charts()")
    toggle = body.index('st.get("on"')
    assert gate < toggle, "perch must be checked before the on/off toggle"


def test_perch_check_asks_systemd_for_the_timer():
    body = re.search(r"def perch_owns_charts\(\).*?\n\n", SRC, re.S).group(0)
    assert "tbt-perch.timer" in body
    assert "is-active" in body


def test_only_perch_writes_the_chart_file():
    """Two writers of chart_coins.json is the bug; keep it to one."""
    perch = (ROOT / "perch.py").read_text()
    assert "chart_coins.json" in perch
    # The panel may still name the file (set_wanted exists for the manual
    # button), but nothing on the autopilot path may reach it.
    body = re.search(r"def autopilot\(\):(.*?)\ndef ", SRC, re.S).group(1)
    reachable = body[:body.index("perch_owns_charts()")]
    assert "set_wanted" not in reachable
