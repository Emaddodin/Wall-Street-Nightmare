"""Risk manager: dynamic position sizing and the daily guardrails.

Sizing is separated from leverage, always:

    risk_amount = equity * risk_per_trade
    qty         = risk_amount / stop_distance
    notional    = qty * entry_price
    leverage    = notional / equity        (pure arithmetic fact)

Leverage is an EXECUTION CONSTRAINT ONLY: if the implied leverage exceeds
the cap, the size is cut to the cap -- risk goes down, never up.  Available
leverage never grows a position.

Daily controls: loss limit, consecutive-loss cap, cooldown after losses,
trade budget -- all reset at the session boundary.  A halt blocks new
entries only; open positions always follow their own stop/target rules.
"""
from __future__ import annotations

DAY_MS = 86_400_000


class RiskManager:
    def __init__(self, cfg, starting_equity: float, day_start_hour_utc: int = 0):
        self.cfg = cfg
        self.starting_equity = float(starting_equity)
        self.equity = float(starting_equity)
        self.day_start_hour_utc = day_start_hour_utc
        self.day_start_ms = 0
        self.day_start_equity = float(starting_equity)
        self.trades_today = 0
        self.consec_losses = 0
        self.last_close_ms = 0
        self.cooldown_until_ms = 0
        self.halted = False
        self.halt_reason = ""

    # ------------------------------------------------------------ session
    def _day_start(self, ms: int) -> int:
        day = ms // DAY_MS
        return day * DAY_MS + self.day_start_hour_utc * 3_600_000

    def roll_day(self, ms: int) -> None:
        """Re-arm limits at the session boundary."""
        ds = self._day_start(ms)
        if ds != self.day_start_ms:
            self.day_start_ms = ds
            self.day_start_equity = self.equity
            self.trades_today = 0
            self.consec_losses = 0
            self.cooldown_until_ms = 0
            self.halted = False
            self.halt_reason = ""

    # ------------------------------------------------------------ gating
    def can_enter(self, ms: int) -> tuple[bool, str]:
        self.roll_day(ms)
        d = self.cfg.daily
        if self.halted:
            return False, f"halted: {self.halt_reason}"
        # halts (stronger) are judged before the softer cooldown
        day_pnl_pct = (self.equity - self.day_start_equity) / self.day_start_equity
        if d["target_pct"] > 0 and day_pnl_pct >= d["target_pct"]:
            # the daily KPI is in the bag -- lock it in, stop until tomorrow
            self.halted = True
            self.halt_reason = f"daily target reached (+{d['target_pct']:.0%})"
            return False, self.halt_reason
        if day_pnl_pct <= -d["loss_limit_pct"]:
            self.halted = True
            self.halt_reason = f"daily loss limit ({d['loss_limit_pct']:.0%})"
            return False, self.halt_reason
        if self.consec_losses >= d["max_consecutive_losses"]:
            self.halted = True
            self.halt_reason = (f"{d['max_consecutive_losses']} losses in a row")
            return False, self.halt_reason
        if self.trades_today >= d["max_trades_per_day"]:
            return False, f"max_trades/day ({d['max_trades_per_day']})"
        if ms < self.cooldown_until_ms:
            return False, "cooldown"
        return True, ""

    def size(self, entry: float, stop: float, equity: float | None = None) -> dict:
        """(qty, notional, leverage, risk_amount) for a long/short sized so
        the stop costs exactly risk_per_trade of equity.  Leverage-capped."""
        r = self.cfg.risk
        eq = self.equity if equity is None else float(equity)
        dist = abs(entry - stop)
        if dist <= 0:
            return {"qty": 0.0, "notional": 0.0, "leverage": 0.0,
                    "risk_amount": 0.0, "rejected": "zero stop distance"}
        risk_amount = eq * r["risk_per_trade"]
        # Kelly cap (R&D 2026-09-11): the 60-day replay showed 25% fixed
        # risk = ruin in ~25 days.  A hard cap on the risk fraction keeps
        # the book alive; set from the strategy family's measured stats.
        cap = float(r.get("max_risk_kelly_pct", 0.0))
        if cap > 0:
            risk_amount = min(risk_amount, eq * cap)
        qty = risk_amount / dist
        notional = qty * entry
        lev = notional / eq if eq > 0 else 0.0
        if lev > r["leverage_cap"]:
            notional = eq * r["leverage_cap"]
            qty = notional / entry
            lev = r["leverage_cap"]
            risk_amount = qty * dist
        return {"qty": qty, "notional": notional, "leverage": lev,
                "risk_amount": risk_amount, "rejected": None}

    # ------------------------------------------------------------ ledger
    def on_fill(self, ms: int, entry_fee: float) -> None:
        self.roll_day(ms)
        self.trades_today += 1
        self.equity -= entry_fee

    def on_close(self, pnl: float, ms: int) -> None:
        """Credit realized PnL to the ledger.  The live paper book calls
        this once per CLOSED LOT so a trailing runner's profit lands in the
        balance the moment it is banked; the backtester calls it once per
        finished position."""
        self.roll_day(ms)
        self.equity += pnl
        self.last_close_ms = ms
        if pnl < 0:
            self.consec_losses += 1
            d = self.cfg.daily
            if self.consec_losses >= d["cooldown_after_losses"]:
                self.cooldown_until_ms = ms + d["cooldown_minutes"] * 60_000
        else:
            self.consec_losses = 0

    def snapshot(self, ms: int) -> dict:
        self.roll_day(ms)
        day_pnl = (self.equity - self.day_start_equity) / self.day_start_equity
        target = self.cfg.daily["target_pct"]
        return {
            "equity": self.equity,
            "day_start_equity": self.day_start_equity,
            "day_pnl_pct": day_pnl * 100.0,
            "target_pct": target * 100.0,
            "target_hit": bool(target > 0 and day_pnl >= target),
            "trades_today": self.trades_today,
            "consec_losses": self.consec_losses,
            "cooldown_until_ms": self.cooldown_until_ms,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
        }
