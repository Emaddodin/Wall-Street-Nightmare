"""The daily +target must bank the whole book on LIVE equity, not realized.

Reproduces the Sep-14 overnight miss: the phone app showed +116% (live
equity) while the bot's halt keyed off realized equity (~53%) and, even on
a trophy, only stopped new entries instead of closing open winners.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import live_hyperliquid as m
from live_hyperliquid import Config, PaperBook, Position, RiskEngine


def _mk():
    cfg = Config(paper_equity=100.0)
    book = PaperBook(cfg)
    book.realized_pnl = 20.0
    book.positions["X"] = Position(
        coin="X", side=1, entry_px=100.0, qty=1.0, sl_px=99.0,
        be_trigger_px=101.0, tp1_px=102.0, tp2_px=105.0, tp1_qty=0.4)
    mids = {"X": 160.0}
    risk = RiskEngine(cfg, lambda: book, lambda: mids, lambda: None, None)
    risk.day_start_ms = 1789344000000
    risk.day_start_eq = 100.0
    return cfg, book, mids, risk


def test_live_equity_is_realized_plus_floating():
    cfg, book, mids, risk = _mk()
    assert risk.equity() == 120.0
    assert abs(risk.live_equity() - 180.0) < 1e-9   # 120 + 1*(160-100)


def test_trophy_keys_off_live_not_realized():
    cfg, book, mids, risk = _mk()
    assert risk.trophy_hit(1789344000001) is False   # live 180 < target 200
    mids["X"] = 200.0                                 # floating +100
    assert abs(risk.live_equity() - 220.0) < 1e-9
    assert risk.trophy_hit(1789344000002) is True    # live 220 >= 200


def test_close_all_banks_at_mids():
    cfg, book, mids, risk = _mk()
    mids["X"] = 200.0
    book.close_all(mids, "target", 1789344000002)
    assert "X" not in book.positions
    # 20 + 1*(200-100) - taker fee(0.03% of 200) = 119.94
    assert abs(book.realized_pnl - 119.94) < 0.05


def test_check_halt_trophy_uses_live():
    cfg, book, mids, risk = _mk()
    # realized 120 is far below target 200, but live 180 is too -> not halted
    assert risk.check_halt(1789344000001) is False
    mids["X"] = 210.0                                # live 230
    assert risk.check_halt(1789344000002) is True
