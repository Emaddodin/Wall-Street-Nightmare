"""Print Kronos gold forecasts as lines for the TradingView indicator's "Kronos forecasts" box.

Each line is one forecast:
    KRONOS|<open time of the last closed M1 bar, UTC s>|<its close>|<ATR14>|<model>|<p1,p2,...,pN>
p1..pN are the forecast closes of the next N minutes. Paste one line (or several, newest last)
into the indicator's settings. It reads the last closed LiteFinance M1 bars, like Gold Desk.

    python3 kronos_pine_line.py --size base                     # one forecast now
    python3 kronos_pine_line.py --size base --loop              # a new line after every closed minute
    python3 kronos_pine_line.py --size base --history 20 --every 15
                                # 20 past forecasts, 15 min apart: shows Kronos on past bars in Pine

On the VPS run it with the Kronos venv's python and point --repo at the Kronos code,
e.g. /opt/kronos/venv/bin/python kronos_pine_line.py --repo /opt/kronos/Kronos
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kronos_backtest import load_litefinance          # noqa: E402
from kronos_signal import DEFAULT_REPO, atr          # noqa: E402

REPO_GUESSES = [DEFAULT_REPO, Path("/opt/kronos/Kronos"), Path("/root/kronos/Kronos"), Path("/root/kronos")]


def pine_line(res: dict, rows: list, model: str) -> str:
    path = ",".join(f"{p['value']:.2f}" for p in res["path"])
    return f"KRONOS|{rows[-1][0]}|{rows[-1][4]:.2f}|{atr(rows):.2f}|{model}|{path}"


def find_repo(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser()
    for p in REPO_GUESSES:
        if (p / "model" / "kronos.py").exists():
            return p
    raise SystemExit("Kronos code not found; pass --repo /path/to/Kronos")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo")
    ap.add_argument("--size", default="base", choices=["mini", "small", "base"])
    ap.add_argument("--lookback", type=int, default=400)
    ap.add_argument("--horizon", type=int, default=15)
    ap.add_argument("--samples", type=int, default=5)
    ap.add_argument("--loop", action="store_true", help="forecast again after every closed minute")
    ap.add_argument("--history", type=int, default=0, help="also make N past forecasts")
    ap.add_argument("--every", type=int, default=15, help="minutes between past forecasts")
    ap.add_argument("--out", default="kronos_pine.txt", help="file the lines are appended to")
    a = ap.parse_args()

    from kronos_signal import Kronos
    k = Kronos(find_repo(a.repo), a.size, a.lookback, a.horizon, a.samples)
    model = f"Kronos-{a.size}"
    out = Path(a.out)

    def emit(line: str) -> None:
        print(line, flush=True)
        with out.open("a") as f:
            f.write(line + "\n")

    rows = load_litefinance(2)
    if a.history:
        ends = [len(rows) - 1 - i * a.every for i in range(a.history, 0, -1)]
        windows = [rows[:e + 1] for e in ends if e + 1 >= 100]
        for w, res in zip(windows, k.forecast_batch(windows)):
            emit(pine_line(res, w[-k.lookback:], model))

    done = 0
    while True:
        if rows and rows[-1][0] != done:
            w = rows[-k.lookback:]
            emit(pine_line(k.forecast(w), w, model))
            done = rows[-1][0]
        if not a.loop:
            break
        time.sleep(62 - time.time() % 60)              # just after the next minute closes
        rows = load_litefinance(1)


if __name__ == "__main__":
    main()
