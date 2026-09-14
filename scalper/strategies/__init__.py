"""The strategy farm.

Each strategy is an independent, deterministic signal source evaluated on
every closed 1m bar.  The eagle (the engine's market-wide scan) feeds all
of them; the book trades whatever passes, tagged with the strategy name.

Strategies share the same signal type (entry_engine.EntrySignal, extended
with `strategy`), the same global risk machinery, and the same daily
+100% target halt.  A strategy's `on_bar` returns (signal|None,
rejections); it may keep per-symbol state via the `states` dict passed by
the caller (keyed (symbol, strategy_name)).
"""
from __future__ import annotations

from entry_engine import EntrySignal
from . import breakout, ict_sniper, impulse, pullback, sweep, turtle, vp

REGISTRY = {
    "vp": vp.VpStrategy,
    "breakout": breakout.BreakoutStrategy,
    "turtle": turtle.TurtleStrategy,
    "impulse": impulse.ImpulseStrategy,
    "pullback": pullback.PullbackStrategy,
    "sweep": sweep.SweepStrategy,
    "ict_sniper": ict_sniper.IctSniperStrategy,
}


def make_strategies(cfg) -> dict[str, object]:
    out = {}
    for name in cfg.strategies["enabled"]:
        cls = REGISTRY.get(name)
        if cls is None:
            raise ValueError(f"unknown strategy {name!r}")
        out[name] = cls(cfg)
    return out


def collect_signals(strategies: dict[str, object], sd, i: int, tf15, j15: int,
                    t_close: int, states: dict):
    """Evaluate every strategy on the closed 1m bar i.  Returns
    (signals, rejections).  When several strategies fire on the same bar,
    the config order wins (first enabled strategy has priority)."""
    signals = []
    rejections = []
    for name, strat in strategies.items():
        try:
            sig, rej = strat.on_bar(sd, i, tf15, j15, t_close,
                                    states.setdefault((sd.sym, name), {}))
        except Exception as e:  # a broken strategy must never kill the book
            rejections.append({"strategy": name, "leg": "error",
                               "why": str(e)[:120], "passed_legs": 0})
            continue
        for r in rej:
            r.setdefault("strategy", name)
        rejections.extend(rej)
        if sig is not None:
            sig.strategy = name
            signals.append(sig)
    # first enabled strategy wins on a contested bar
    order = {name: k for k, name in enumerate(strategies)}
    signals.sort(key=lambda s: order.get(s.strategy, 99))
    return signals, rejections
