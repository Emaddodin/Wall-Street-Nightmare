"""
xau_alpha/tests/replay_parity.py
Live-vs-backtest parity: replay real Dukascopy 10-second bid/ask bars as quotes through live.trader.AlphaTrader
(fake broker that fills at the quote and enforces the ticket SL), and compare its trades with sim.simulate() on the
orders the same module emits over the same window.

    python3 xau_alpha/tests/replay_parity.py [module] [start] [end] [params-json]
Default: runner module, 2026-01-12..2026-01-17 (VALID period; never TEST).
Guards that the backtest does not model (news, spread cap, sessions, daily limits) are disabled here on purpose:
this test checks execution semantics, not policy.
"""
import asyncio
import json
import math
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

X = Path(__file__).resolve().parents[1]
for p in (X / "lib", X / "cand", X / "live"):
    sys.path.insert(0, str(p))

from data import _ms, load_m1, load_s10  # noqa: E402
from sim import COSTS, simulate  # noqa: E402
from trader import AlphaTrader, TraderConfig  # noqa: E402


@dataclass
class Acc:
    balance: float
    equity: float
    assets_used: float
    available: float
    floating_pnl: float


@dataclass
class Q:
    bid: float
    ask: float
    mid: float
    timestamp: float


class FakeGateway:
    def __init__(self, eq=10_000.0):
        self.bal = eq
        self.pos = None          # (d, lots, entry, sl)
        self.q = None
        self.fills = []

    def set_quote(self, bid, ask, ts):
        self.q = Q(bid, ask, (bid + ask) / 2, ts)
        if self.pos:
            d, lots, entry, sl = self.pos
            if (d > 0 and bid <= sl) or (d < 0 and ask >= sl):      # broker-side SL on the ticket
                px = min(sl, bid) if d > 0 else max(sl, ask)
                self._close(px, "broker_sl")

    def _close(self, px, why):
        d, lots, entry, sl = self.pos
        self.bal += (px - entry) * d * lots * 100
        self.fills.append(("close", self.q.timestamp, px, why))
        self.pos = None

    async def get_account_snapshot(self, force_fresh=False):
        return Acc(self.bal, self.bal, 0.0, self.bal, 0.0)

    async def get_live_quote(self):
        return self.q

    async def open_market_order(self, direction, volume, sl_price=None, tp_price=None, expected_mode=None):
        d = 1 if direction == "BUY" else -1
        px = self.q.ask if d > 0 else self.q.bid
        self.pos = (d, volume, px, sl_price)
        self.fills.append(("open", self.q.timestamp, px, direction))
        return {"success": True}

    async def flatten_all_positions(self):
        if self.pos:
            d = self.pos[0]
            self._close(self.q.bid if d > 0 else self.q.ask, "flatten")
        return {"success": True}


async def replay(module, params, start, end):
    m1 = load_m1()
    s = load_s10()
    t0, t1 = _ms(start), _ms(end)
    tmp = Path(tempfile.mkdtemp())
    cfg = TraderConfig(module=module, params=params, min_bars=300, warmup_bars=1500, spread_cap=99, spread_rel_cap=99,
                       blackout_flip=(0, 0), blackout_main=(0, 0), no_entry_utc=(), flip_max_losses_day=99,
                       friday_last_entry_utc=24 * 60, friday_flatten_utc=24 * 60, handoff_equity=0.0,
                       main_risk_frac=0.0, main_max_risk_frac=1.0, state_dir=str(tmp))
    g = FakeGateway()
    tr = AlphaTrader(g, cfg)
    tr.news.coverage_ok = lambda now_ms: True
    tr.news.blocked = lambda *a, **k: None
    # warm up with research bars before the window (same values the live feed would have produced)
    warm = m1[(m1.ts < t0)].tail(tr.warmup)
    tr.bars.bars = warm[["ts", "bo", "bh", "bl", "bc", "ao", "ah", "al", "ac", "spr", "spr_max", "n", "vol"]].to_dict("records")
    await tr.refresh_equity()
    tr.phase = "MAIN"
    k0, k1 = np.searchsorted(s["ts"], t0), np.searchsorted(s["ts"], t1)
    for k in range(k0, k1):
        ts = s["ts"][k] / 1000.0
        seq = [(s["bo"][k], s["ao"][k], ts), (s["bl"][k], s["al"][k], ts + 2.5),
               (s["bh"][k], s["ah"][k], ts + 5.0), (s["bc"][k], s["ac"][k], ts + 7.5)]
        for bid, ask, t in seq:
            bid, ask = float(bid), float(ask)
            g.set_quote(bid, ask, t)
            if tr.pos is not None and g.pos is None:          # broker SL fired: sync the trader
                tr.pos = None
            await tr.on_quote(bid, ask, t)
    live = [f for f in g.fills]
    # backtest on the same orders
    orders = [o for o in __import__(module).orders(m1, **params) if t0 <= o["t"] < t1]
    bt = simulate(orders, COSTS["duka_raw"])
    return live, bt


def main():
    module = sys.argv[1] if len(sys.argv) > 1 else "runner"
    start = sys.argv[2] if len(sys.argv) > 2 else "2026-01-12"
    end = sys.argv[3] if len(sys.argv) > 3 else "2026-01-17"
    params = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {"lookback": 48, "atr_min": 3.5, "stop": 4.0, "trail": 2.0}
    live, bt = asyncio.run(replay(module, params, start, end))
    opens = [f for f in live if f[0] == "open"]
    closes = [f for f in live if f[0] == "close"]
    print(f"live trades {len(opens)}  backtest trades {len(bt)}")
    n = min(len(opens), len(bt))
    rows = []
    for i in range(n):
        o, c, b = opens[i], closes[i] if i < len(closes) else None, bt.iloc[i]
        rows.append({"live_in": pd.Timestamp(o[1], unit="s"), "bt_in": pd.Timestamp(b.t_in, unit="ms"),
                     "live_entry": o[2], "bt_entry": b.entry, "live_exit": c[2] if c else None, "bt_exit": b.exit,
                     "live_why": c[3] if c else None, "bt_why": b.reason})
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 220)
    print(df.to_string())
    if n:
        same_in = (abs((df.live_in - df.bt_in).dt.total_seconds()) <= 10).mean()
        d_entry = (df.live_entry - df.bt_entry).abs().median()
        d_exit = (df.live_exit - df.bt_exit).abs().median()
        print(f"entry-time match {same_in:.0%}, median |entry diff| {d_entry:.3f}, median |exit diff| {d_exit:.3f}")


if __name__ == "__main__":
    main()
