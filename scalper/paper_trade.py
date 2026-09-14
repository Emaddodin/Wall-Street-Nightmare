"""Paper trading service entrypoint (VP scalper -- no TESLA).

    python paper_trade.py --profile aggressive [--poll 15]

Runs the SAME engine as the backtester against live Bitunix candles.
State survives restarts in data/state/paper.json; fills/closes push ntfy.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.loader import load_config                      # noqa: E402
from market_data.store import CandleStore                  # noqa: E402
from paper_trader import PaperTrader                       # noqa: E402

log = logging.getLogger("scalper.paper")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=None)
    ap.add_argument("--poll", type=float, default=None)
    ap.add_argument("--data", default="data")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = load_config(extra_file=Path(f"config/{a.profile}.yaml") if a.profile else None)
    poll = a.poll or cfg.paper["poll_seconds"]

    data = Path(a.data)
    store = CandleStore(data / "candles")
    trader = PaperTrader(cfg, store, data / "state", data / "logs")

    stop = threading.Event()

    def _sig(*_):
        stop.set()

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    trader._ntfy("scalper session started")
    log.info("paper trader up: equity %.2f, poll %.0fs",
             trader.risk.equity, poll)
    # fast live-price thread: the app's position card ticks with the
    # exchange ticker every ~2s, independent of the (slow) engine step
    def _live_loop():
        while not stop.is_set():
            stop.wait(2)
            try:
                trader.refresh_live()
            except Exception:
                pass
    threading.Thread(target=_live_loop, daemon=True).start()

    while not stop.is_set():
        try:
            trader.step()
        except Exception as e:
            log.exception("step failed: %s", e)
        stop.wait(poll)
    trader._persist()
    log.info("paper trader stopped")


if __name__ == "__main__":
    main()
