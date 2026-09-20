"""
Drive the REAL papertrade loop to produce dataset rows.

`run()` executes `papertrade.main()` -- the actual book, with its actual
argument parsing, gating, sizing and exit handling -- over a faked chart and
exchange (tests/fakes.py, the same stand-ins the 400+ tests use). Every
decision row in the dataset therefore comes out of the engine itself: the log
lines ARE the reason strings, and the book on disk IS the trade record.

Only the two edges are replaced, exactly as in the test suite: the chart
serves recorded real candles and one real indicator signal, and the exchange
serves the real prices that followed it.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import papertrade as P  # noqa: E402
import harness  # noqa: E402  (tests/harness.py)
from fakes import FakeBitunix  # noqa: E402
import market as M  # noqa: E402  (tests/market.py)

# The live configuration, verbatim from services/tbt-paper.service.
LIVE_ARGS = [
    "--source", "combo", "--scout", "--skip-window", "1",
    "--interval", "2.0", "--sprint", "0.2", "--stack",
    "--tp", "5", "--lev", "40", "--frac", "0.5", "--sl", "1.25",
    "--max-exposure", "0.5",
    "--min-confidence", "0", "--min-agents", "3", "--min-atr", "2.5",
    "--with-trend", "--per-coin-lev", "--min-entry", "40", "--min-lev", "0",
    "--per-day", "8", "--day-start", "17.5", "--max-per-day", "0",
    "--fill-bars", "8", "--step-at", "10000", "--step-frac", "0.25",
    "--break-even", "0", "--ct-exit", "3.5",
]

BAR = 900          # 15m, the live chart timeframe
RES = "15"         # the live charts run on 15m, whatever the collector used


def argv(overrides: dict | None = None) -> list[str]:
    """The live flags, with per-scenario overrides.

    overrides: {flag: value} -- value True adds a bare flag, None/False
    removes it, anything else becomes the flag's value.
    """
    out = list(LIVE_ARGS)
    for k, v in (overrides or {}).items():
        while k in out:                    # strip flag + its value
            j = out.index(k)
            if j + 1 < len(out) and not out[j + 1].startswith("--"):
                out.pop(j + 1)
            out.pop(j)
        if v is True:
            out += [k]
        elif v not in (None, False):
            out += [k, str(v)]
    return out


def combo_window(sym: str, sig: dict, prefix: dict[int, tuple]):
    """A chart with real history whose last CLOSED bar will carry the signal.

    The signal plot fires on exactly one bar -- the recorded signal bar --
    which the caller adds to `spec["ohlc"]` mid-run via `inject()`. A signal
    already present when the book starts would be absorbed as backlog and
    never traded, which is also the only way it happens in life.
    """
    t = int(sig["t"])
    side = sig["side"]
    spec = {"symbol": f"BITUNIX:{sym}.P", "res": RES, "ohlc": dict(prefix),
            "plots": {}}

    def at(bar_t, v):
        return v if bar_t == t else None

    spec["plots"] = {
        f"TBT_{side}_SPAN": lambda bt: at(bt, int(sig.get("span") or 0)),
        f"TBT_{side}_TIER": lambda bt: at(bt, int(sig.get("tier") or 0)),
        f"TBT_{side}_SCORE": lambda bt: at(bt, int(sig.get("score") or 0)),
        f"SNIP_{side}_VOTE": lambda bt: at(bt, int(sig.get("agents") or 0)),
        f"TBT_{side}_LAST": lambda bt: at(bt, int(sig.get("who") or 0)),
        "TSL_WIRED": lambda bt: at(bt, 1.0 if sig.get("wired") else 0.0),
    }
    if sig.get("counter"):
        spec["plots"][f"TBT_CT{side}_SPAN"] = \
            lambda bt: at(bt, int(sig.get("span") or 0))
    return spec


def inject(spec: dict, sig: dict, forming_close: float | None = None):
    """Print the recorded signal bar onto the chart, plus one forming bar.

    The forming bar makes the signal the last CLOSED bar, which is the only
    bar the engine reads a signal from.
    """
    t = int(sig["t"])
    bar = sig.get("bar") or {}
    o = float(bar.get("o") or 0)
    h = float(bar.get("h") or 0)
    l = float(bar.get("l") or 0)
    c = float(bar.get("c") or 0)
    if not c:
        c = float(sig.get("px") or 1.0)
        o = h = l = c
    ohlc = spec["ohlc"]
    ohlc[t] = (o, h, l, c)
    fc = forming_close if forming_close else c
    ohlc[t + BAR] = (fc, fc * 1.0002, fc * 0.9998, fc)


def quiet_prefix(t: int, base: float, n: int = 45) -> dict[int, tuple]:
    """A calm band before the signal, for coins with no recorded history.

    Same generator the tests use; provenance is marked wherever it is used.
    """
    return M.quiet_band(n=n, base=base, t0=t - n * BAR)


def run(book_state: dict, clock_start: float, windows: dict[int, dict],
        prices: dict[str, float], args: list[str], polls: int = 4,
        on_poll=None, scout_rows: list | None = None,
        boom_rows: list | None = None, watch_measures: dict | None = None,
        measures: dict | None = None, exchange_kwargs: dict | None = None,
        tmpdir: str | None = None):
    """Run the real book for `polls` polls.

    Returns (harness.Book, clock, measures_file). One tmpdir serves every
    run -- 65k mkdtemp dirs once filled the machine's disk, and a run costs
    five small files that the next run overwrites anyway.
    """
    tmp = tmpdir or tempfile.mkdtemp(prefix="tbt-ds-")
    meas = Path(tmp) / "atr_measures.json"
    meas.write_text(json.dumps(measures or {}))
    # The measures file is rewritten in place, so its mtime can repeat
    # within a second; the coin cache is reset rather than trusted.
    P._ATR.update({"t": 0.0, "by": {}, "trend": {}, "shape": {},
                   "stamp": 0})
    ex_kw = {"prices": prices, "spread_bps": 0.0}
    ex_kw.update(exchange_kwargs or {})
    late = {}
    for k in ("open_positions", "closed_positions"):
        if k in ex_kw:
            late[k] = ex_kw.pop(k)
    ex = FakeBitunix(**ex_kw)
    for k, v in late.items():
        setattr(ex, k, v)
    with mock.patch.object(P, "ATR_M", meas), \
            contextlib.redirect_stdout(io.StringIO()):
        book, clock = harness.run_book(
            tmp, args, windows, ex, polls=polls, on_poll=on_poll,
            clock_start=clock_start, scout_rows=scout_rows or [],
            boom_rows=boom_rows or [], watch_measures=watch_measures or {},
            book_state=book_state)
    return book, clock, meas


def score_with(meas: Path | dict, sym: str, side: str, agents: int):
    """entry_score as the book computed it, over the run's measures file.

    A dict (the measures themselves) is accepted as a fallback; a Path is
    the file the run wrote.
    """
    if isinstance(meas, dict):
        p = Path(tempfile.mkdtemp(prefix="tbt-ds-")) / "atr_measures.json"
        p.write_text(json.dumps(meas))
        meas = p
    P._ATR.update({"t": 0.0, "by": {}, "trend": {}, "shape": {},
                   "stamp": 0})
    with mock.patch.object(P, "ATR_M", meas):
        return P.entry_score(sym, side, agents)


def walk_path(side: str, entry: float, bars, tp_pct: float = 5.0,
              sl_pct: float = 1.25):
    """The real post-signal path, in the engine's own vocabulary.

    A bar that spans both target and stop is a STOP -- the convention the
    repo's own replay tools use, because which came first inside one candle
    is unknowable and resolving it in our favour is how a backtest lies.
    Returns (reason, exit_price, bars_held, max_fav_pct).
    """
    up = side == "BUY"
    tp = entry * (1 + tp_pct / 100) if up else entry * (1 - tp_pct / 100)
    sl = entry * (1 - sl_pct / 100) if up else entry * (1 + sl_pct / 100)
    best = 0.0
    for i, hi, lo in bars:
        fav = ((hi / entry - 1) if up else (1 - lo / entry)) * 100
        best = max(best, fav)
        hit_sl = lo <= sl if up else hi >= sl
        hit_tp = hi >= tp if up else lo <= tp
        if hit_sl:
            return "stop", sl, i + 1, best
        if hit_tp:
            return "target", tp, i + 1, best
    return "open", None, len(bars), best
