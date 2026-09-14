"""Entry engine -- the strategy-farm dispatcher.

For every CLOSED 1m bar the farm evaluates every enabled strategy
(strategies/ package).  The engine keeps per-(symbol, strategy) state;
when several strategies fire on the same bar, the config's enabled order
wins.  Signals fill at the next bar's open; rejections carry the failing
leg AND the strategy that produced them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from structure import LONG, SHORT

MIN_MS = 60_000
HOUR_MS = 3_600_000


@dataclass
class EntrySignal:
    symbol: str
    direction: int                    # LONG / SHORT
    bar: int                          # 1m bar index of the trigger
    at_ms: int                        # trigger bar close time (ms)
    entry_price: float                # reference = trigger bar close
    swing_level: float                # the invalidation swing (SL anchor)
    entry_model: str                  # order_block | fvg | micro_poc | ...
    bias: int                         # the 15m bias direction
    vp_poc: float                     # POC of the current 15m profile
    strategy: str = ""                # which farm strategy produced it
    entry_level: float | None = None  # limit entry at the model level
    entry_expiry_bars: int = 4        # (None = market at next open)
    atr1m: float = 0.0
    tp_first: float | None = None     # 50% target: nearest opposite 15m swing
    rejections_this_bar: list = field(default_factory=list)


class EntryEngine:
    def __init__(self, cfg):
        self.cfg = cfg
        from strategies import make_strategies
        self.strategies = make_strategies(cfg)

    def on_bar(self, sd, i: int, tf15, j15: int, t_close: int, states: dict):
        """Evaluate the whole farm on the closed 1m bar i.
        Returns (signals, rejections)."""
        from strategies import collect_signals
        return collect_signals(self.strategies, sd, i, tf15, j15, t_close,
                               states)
