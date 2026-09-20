"""Execution guards for the quant Sim -- the ICT sniper's rule set C.

The plain Sim fills whatever the event stream offers.  The sniper's spec
demands stateful, trade-outcome-aware constraints:

  * Frequency: OPTIONAL fill cap per symbol per UTC day (None = off; the
    Hit & Run phase lets the price action dictate frequency).
  * Circuit breaker: 2 consecutive losing trades (pnl <= 0) on a symbol
    halt that symbol for the REST of the UTC day (spec: exactly 2).
  * Hit & Run daily profit cap: once the symbol's cumulative realized
    UN-leveraged move for the day (sum of per-leg ret bps) reaches
    `daily_profit_cap_bps` (e.g. 300 = +3%), trading halts for that
    symbol until the next UTC day -- one good expansion, then stop.
  * Optional dynamic eligibility: `eligible` maps
    (symbol, utc_day_start_ms) -> bool (the coin-finder scan output);
    False blocks both signal intake and limit fills for that day.

Implemented purely through the default-pass hooks added to Sim
(_candidate_ok / _limit_fill_ok) plus overrides of _open_position and
_close_position, so the engine's fill/exit machinery is untouched and the
plain Sim's behaviour for every existing strategy is bit-identical.

All day keys are UTC day-start timestamps in ms (t // DAY_MS * DAY_MS),
matching quant.universe.coin_filter.scan_daily.
"""
from __future__ import annotations

from quant.engine.backtest import Sim

DAY_MS = 86_400_000


def _day_key(t: int) -> int:
    """UTC day start (ms) for a bar open time (ms)."""
    return int(t // DAY_MS) * DAY_MS


class GuardedSim(Sim):
    def __init__(self, *args,
                 max_trades_per_day: int | None = None,
                 max_consecutive_losses: int | None = 2,
                 max_trades_global_per_day: int | None = None,
                 daily_profit_cap_bps: float | None = None,
                 eligible: dict[tuple[str, int], bool] | None = None,
                 **kwargs):
        """None disables a guard.  `eligible` maps
        (symbol, utc_day_start_ms) -> bool.  `daily_profit_cap_bps` halts
        a symbol for the rest of the day once its realized day PnL (sum of
        per-leg un-leveraged ret bps) reaches the cap."""
        super().__init__(*args, **kwargs)
        self.max_trades_per_day = max_trades_per_day
        self.max_consecutive_losses = max_consecutive_losses
        self.max_trades_global_per_day = max_trades_global_per_day
        self.daily_profit_cap_bps = daily_profit_cap_bps
        self.eligible = eligible
        self._fills: dict[tuple[str, int], int] = {}       # (sym, day) -> n
        self._global_fills: dict[int, int] = {}            # day -> n
        self._streak: dict[tuple[str, int], int] = {}      # (sym, day) -> losses
        self._day_profit: dict[tuple[str, int], float] = {}  # realized bps
        self._blocked: set[tuple[str, int]] = set()        # breaker-tripped
        self.n_blocks = 0                                  # breaker trips
        self.n_profit_hits = 0                             # hit&run halts

    # ------------------------------------------------------------------
    def _eligible_now(self, symbol: str, day: int) -> bool:
        if self.eligible is None:
            return True
        return bool(self.eligible.get((symbol, day), False))

    def _blocked_now(self, symbol: str, day: int) -> bool:
        if (symbol, day) in self._blocked:
            return True
        if self.max_trades_per_day is not None and \
                self._fills.get((symbol, day), 0) >= self.max_trades_per_day:
            return True
        if self.max_trades_global_per_day is not None and \
                self._global_fills.get(day, 0) >= self.max_trades_global_per_day:
            return True
        if self.daily_profit_cap_bps is not None and \
                self._day_profit.get((symbol, day), 0.0) \
                >= self.daily_profit_cap_bps:
            return True
        return False

    # ------------------------------------------------------------------
    def _open_position(self, symbol, side, score, exit_model, meta, row,
                       fill_price=None, maker=False, signal_t=0):
        before = len(self.positions)
        super()._open_position(symbol, side, score, exit_model, meta, row,
                               fill_price, maker, signal_t)
        if len(self.positions) == before:
            return                    # nothing was opened (no margin etc.)
        day = _day_key(int(self.sym[symbol]["t"][row]))
        self._fills[(symbol, day)] = self._fills.get((symbol, day), 0) + 1
        self._global_fills[day] = self._global_fills.get(day, 0) + 1

    def _close_position(self, p, price, reason, liq=False):
        super()._close_position(p, price, reason, liq)
        exit_t = self.timeline[self.bar_idx] \
            if self.bar_idx < len(self.timeline) else p.entry_t
        day = _day_key(int(exit_t))
        key = (p.symbol, day)
        ret_bps = (price / p.entry_price - 1) * p.side * 10_000.0
        # hit & run: accumulate the day's realized un-leveraged move
        if self.daily_profit_cap_bps is not None:
            self._day_profit[key] = self._day_profit.get(key, 0.0) + ret_bps
            if self._day_profit[key] >= self.daily_profit_cap_bps \
                    and key not in self._blocked:
                self._blocked.add(key)
                self.n_profit_hits += 1
        # loss streak / circuit breaker
        streak = self._streak.get(key, 0) + (1 if p.pnl <= 0 else 0)
        self._streak[key] = 0 if p.pnl > 0 else streak
        if self.max_consecutive_losses is not None and \
                streak >= self.max_consecutive_losses and key not in self._blocked:
            self._blocked.add(key)
            self.n_blocks += 1

    def _scale_out_position(self, p, price, gi, reason="tp1"):
        """TP1 / trim legs realize through Sim._scale_out_position, not
        _close_position -- count them for the hit&run cap (realized
        un-leveraged moves), and for the loss streak: TP1 legs are wins
        (reset), trim legs are losses (increment)."""
        super()._scale_out_position(p, price, gi, reason)
        exit_t = self.timeline[gi] if gi < len(self.timeline) else p.entry_t
        day = _day_key(int(exit_t))
        key = (p.symbol, day)
        ret_bps = (price / p.entry_price - 1) * p.side * 10_000.0
        if self.daily_profit_cap_bps is not None:
            self._day_profit[key] = self._day_profit.get(key, 0.0) + ret_bps
            if self._day_profit[key] >= self.daily_profit_cap_bps \
                    and key not in self._blocked:
                self._blocked.add(key)
                self.n_profit_hits += 1
        if ret_bps > 0:
            self._streak[key] = 0
        else:
            self._streak[key] = self._streak.get(key, 0) + 1
            if self.max_consecutive_losses is not None and \
                    self._streak[key] >= self.max_consecutive_losses \
                    and key not in self._blocked:
                self._blocked.add(key)
                self.n_blocks += 1

    def _candidate_ok(self, symbol, side, score, exit_model, meta, t):
        day = _day_key(int(t))
        return self._eligible_now(symbol, day) and \
            not self._blocked_now(symbol, day)

    def _limit_fill_ok(self, lo, t):
        day = _day_key(int(t))
        return self._eligible_now(lo.symbol, day) and \
            not self._blocked_now(lo.symbol, day)
