"""Event-driven portfolio backtester for the new quant project.

Design goals:
  * Lookahead-free: a signal computed on the CLOSE of bar t fills at the
    OPEN of the next bar.
  * Honest intrabar exits: a position filled at bar t's open can hit its
    stop/target within bar t; if one bar touches both, the stop wins
    (unless the bar opens beyond the target).
  * Realistic costs: taker fee both sides, adverse slippage on entries and
    stops, funding charged at the 8h marks while in position.
  * Leverage explicit: notional = margin * leverage; leverage is capped so
    the stop is survivable (sl_dist * lev < 95%).
  * Shared equity across symbols; cross-sectional entry selection by score.

Signal contract: precomputed events dict {close_timestamp: [(symbol, side,
score, exit_model, meta), ...]}.  exit_model: {'tp_bps','sl_bps','trail_bps',
'max_bars'}.  meta: {'alloc': equity fraction, 'lev': leverage}.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

log = logging.getLogger("quant.engine.backtest")

MARK_MS = 28_800_000  # 8h funding interval


@dataclass
class CostModel:
    taker_fee_bps: float = 5.0      # per side, of notional
    maker_fee_bps: float = 2.0      # per side, for maker exits (TP/TP1/TP2)
    maker_entry_fee_bps: float | None = None  # maker ENTRIES (None -> maker)
    slippage_bps: float = 1.0       # adverse, on market entries and stops
    funding: bool = True


@dataclass
class LimitOrder:
    symbol: str
    side: int
    limit_price: float
    expiry_gi: int
    score: float
    exit_model: dict
    meta: dict
    signal_t: int = 0        # signal bar open_time (ms)


@dataclass
class Position:
    symbol: str
    side: int
    entry_gi: int           # global bar index of the FILL bar
    entry_t: int            # fill bar open_time (ms)
    entry_price: float
    stop: float | None
    target: float | None
    trail: float | None
    max_bars: int | None
    margin: float
    lev: float
    notional: float
    signal_t: int = 0       # signal bar open_time (ms)
    score: float = 0.0
    meta: dict = field(default_factory=dict)
    trail_hi: float = 0.0
    trail_lo: float = np.inf
    fees_paid: float = 0.0          # entry fee (charged at open)
    funding_paid: float = 0.0
    bars_held: int = 0
    exit_reason: str = ""
    exit_price: float = np.nan
    pnl: float = 0.0
    liq: bool = False
    # breakeven/lock gate (exit_model keys be_after_r / be_after_bps)
    risk: float = 0.0               # |entry - initial stop|
    be_after_r: float | None = None # arm once MFE >= N x risk
    be_after_bps: float | None = None  # arm once MFE >= N bps (overrides R)
    be_to_r: float = 0.0            # then stop -> entry + to_r x risk
    be_armed: bool = False          # the gate actually raised the stop
    neg_bars: int | None = None     # scratch: kill at market if still
                                    # negative after N bars held
    # two-stage scale-out (exit_model keys tp1_bps / tp1_frac)
    tp1: float | None = None        # scale-out price (TP1, +100-120 bps)
    tp1_frac: float = 0.5           # fraction closed at TP1
    scaled: bool = False            # TP1 already realized
    scaled_gi: int = -1             # timeline bar of the scale-out
    # defensive mid-level trim (exit_model keys trim_bps / trim_frac)
    trim_price: float | None = None # resting limit at the FVG-mid anchor
    trim_frac: float = 0.5          # fraction closed at the trim
    trimmed: bool = False           # the trim already fired
    trimmed_gi: int = -1            # timeline bar of the trim


class Sim:
    def __init__(self, frames: dict[str, pd.DataFrame], cost: CostModel,
                 funding: dict[str, pd.DataFrame] | None = None,
                 start_equity: float = 100.0, compound: bool = True):
        """compound=False: sizing uses start_equity (fixed base), so PnL
        accumulates additively and stats stay interpretable regardless of
        trade count."""
        self.cost = cost
        self.start_equity = start_equity
        self.compound = compound
        self.cash = start_equity
        self.size_base = start_equity
        self.sym = {}
        for s, df in frames.items():
            self.sym[s] = {
                "t": df["open_time"].to_numpy(dtype=np.int64),
                "o": df["open"].to_numpy(dtype=np.float32),
                "h": df["high"].to_numpy(dtype=np.float32),
                "l": df["low"].to_numpy(dtype=np.float32),
                "c": df["close"].to_numpy(dtype=np.float32),
            }
        # funding: symbol -> {mark_ms_normalized: rate}
        self.fund_rate = {}
        if funding:
            for s, f in funding.items():
                if f is None or len(f) == 0:
                    continue
                t = f["calc_time"].to_numpy(dtype=np.int64)
                r = f["last_funding_rate"].to_numpy(dtype=float)
                key = t // MARK_MS * MARK_MS
                self.fund_rate[s] = dict(zip(key, r))
        all_t = np.unique(np.concatenate([d["t"]
                                          for d in self.sym.values()]))
        self.timeline = all_t
        self.ptr = {s: 0 for s in self.sym}
        self.positions: list[Position] = []
        self.trades: list[dict] = []
        self.equity_curve: list[tuple[int, float]] = []
        self.pending: list = []      # entries queued for the CURRENT bar open
        self.events: dict[int, list] = {}
        self.limit_orders: list[LimitOrder] = []
        self.total_fees = 0.0
        self.total_funding = 0.0
        self.n_liquidations = 0
        self.bar_idx = 0

    # ------------------------------------------------------------------
    def equity_now(self) -> float:
        eq = self.cash
        for p in self.positions:
            d = self.sym[p.symbol]
            row = self.ptr[p.symbol] - 1
            if row < 0:
                continue
            mark = d["c"][row]
            eq += p.margin + p.notional * (mark / p.entry_price - 1) * p.side
        return eq

    # ------------------------------------------------------------------
    # extension hooks (default: allow everything).  quant.engine.guards
    # .GuardedSim overrides these to enforce daily trade caps and
    # circuit breakers without touching the engine loop.
    def _candidate_ok(self, symbol: str, side: int, score: float,
                      exit_model: dict, meta: dict, t: int) -> bool:
        """Gate a fresh signal at bar `t` (open time, ms) before it is
        queued.  False = drop the signal."""
        return True

    def _limit_fill_ok(self, lo: LimitOrder, t: int) -> bool:
        """Gate a resting limit fill at bar `t` (open time, ms).  False =
        cancel the order outright."""
        return True

    def _open_position(self, symbol: str, side: int, score: float,
                       exit_model: dict, meta: dict, row: int,
                       fill_price: float | None = None,
                       maker: bool = False, signal_t: int = 0):
        d = self.sym[symbol]
        if maker:
            entry_fee_bps = (self.cost.maker_entry_fee_bps
                             if self.cost.maker_entry_fee_bps is not None
                             else self.cost.maker_fee_bps)
            fee = entry_fee_bps / 10_000.0
        else:
            fee = self.cost.taker_fee_bps / 10_000.0
        slip = 0.0 if maker else self.cost.slippage_bps / 10_000.0
        fill = fill_price if fill_price is not None else \
            d["o"][row] * ((1 + slip) if side > 0 else (1 - slip))
        alloc = float(meta.get("alloc", 1.0))
        lev = float(meta.get("lev", 20.0))
        sl_bps = exit_model.get("sl_bps")
        if sl_bps and sl_bps / 10_000.0 * lev >= 0.95:
            lev = 10_000.0 / sl_bps * 0.95
        eq = self.equity_now()
        base = self.size_base if not self.compound else eq
        margin = base * alloc
        if self.compound:
            margin = min(margin, max(self.cash, 0.0))
        if margin <= 0 or lev <= 0:
            return
        notional = margin * lev
        entry_fee = notional * fee
        self.cash -= margin + entry_fee
        self.total_fees += entry_fee
        if side > 0:
            stop = fill * (1 - sl_bps / 10_000.0) if sl_bps else None
            target = fill * (1 + exit_model["tp_bps"] / 10_000.0) \
                if exit_model.get("tp_bps") else None
        else:
            stop = fill * (1 + sl_bps / 10_000.0) if sl_bps else None
            target = fill * (1 - exit_model["tp_bps"] / 10_000.0) \
                if exit_model.get("tp_bps") else None
        p = Position(symbol=symbol, side=side, entry_gi=self.bar_idx,
                     entry_t=int(d["t"][row]), signal_t=signal_t,
                     entry_price=fill, stop=stop,
                     target=target,
                     trail=exit_model.get("trail_bps"),
                     max_bars=exit_model.get("max_bars"), margin=margin,
                     lev=lev, notional=notional, score=score, meta=meta,
                     fees_paid=entry_fee)
        if stop is not None:
            p.risk = abs(fill - stop)
        p.be_after_r = exit_model.get("be_after_r")
        p.be_after_bps = exit_model.get("be_after_bps")
        p.be_to_r = float(exit_model.get("be_to_r") or 0.0)
        p.neg_bars = exit_model.get("neg_bars")
        p.tp1_frac = float(exit_model.get("tp1_frac") or 0.5)
        if exit_model.get("tp1_bps"):
            p.tp1 = fill * (1 + side * float(exit_model["tp1_bps"]) / 10_000.0)
        p.trim_frac = float(exit_model.get("trim_frac") or 0.5)
        if exit_model.get("trim_bps"):
            p.trim_price = fill * (1 - side
                                   * float(exit_model["trim_bps"]) / 10_000.0)
        if side > 0:
            p.trail_hi = fill
        else:
            p.trail_lo = fill
        self.positions.append(p)

    def _close_position(self, p: Position, price: float, reason: str,
                        liq: bool = False):
        # TP/TP1/TP2 exits are resting limit orders -> maker fee;
        # SL/BE/time -> taker
        fee = (self.cost.maker_fee_bps
               if reason in ("tp", "tp1", "tp2")
               else self.cost.taker_fee_bps) / 10_000.0
        exit_fee = p.notional * fee
        if liq:
            gross = -p.margin
        else:
            gross = p.notional * (price / p.entry_price - 1) * p.side
        p.pnl = gross - p.fees_paid - exit_fee - p.funding_paid
        self.cash += p.margin + gross - exit_fee
        p.exit_price = price
        p.exit_reason = reason
        p.liq = liq
        self.total_fees += exit_fee
        if liq:
            self.n_liquidations += 1
        self.trades.append({
            "symbol": p.symbol, "side": p.side, "score": p.score,
            "entry_gi": p.entry_gi, "entry_t": p.entry_t,
            "signal_t": p.signal_t,
            "entry_price": p.entry_price,
            "exit_price": price, "exit_reason": reason,
            "exit_t": self.timeline[self.bar_idx] if self.bar_idx <
            len(self.timeline) else p.entry_t,
            "bars_held": p.bars_held, "pnl": p.pnl,
            "margin": p.margin, "lev": p.lev,
            "ret_pct": (price / p.entry_price - 1) * p.side * 100,
            "fees": p.fees_paid + exit_fee, "funding": p.funding_paid,
            "meta": dict(p.meta),
        })

    def _scale_out_position(self, p: Position, price: float, gi: int,
                            reason: str = "tp1"):
        """Partial close at a resting limit (maker fill).

        reason "tp1": realize tp1_frac at the TP1 limit, then move the
          runner's stop to breakeven (scaled=True).
        reason "trim": realize trim_frac at the mid-level trim limit
          (defensive); the remainder KEEPS the original wide stop
          (trimmed=True).  Trim legs are losses by construction.
        The closed leg is recorded as its own trade; the Position object
        keeps representing the remainder (margin/notional/fees/funding
        scaled down by 1-frac)."""
        frac = p.tp1_frac if reason == "tp1" else p.trim_frac
        if frac <= 0 or frac >= 1:
            return
        if reason == "tp1" and p.scaled:
            return
        if reason == "trim" and p.trimmed:
            return
        fee = self.cost.maker_fee_bps / 10_000.0
        notional_c = p.notional * frac
        gross = notional_c * (price / p.entry_price - 1) * p.side
        exit_fee = notional_c * fee
        entry_fee_c = p.fees_paid * frac          # proportional allocation
        fund_c = p.funding_paid * frac
        pnl = gross - entry_fee_c - exit_fee - fund_c
        self.cash += p.margin * frac + gross - exit_fee
        self.total_fees += exit_fee
        margin_c = p.margin * frac
        p.margin *= 1.0 - frac
        p.notional *= 1.0 - frac
        p.fees_paid *= 1.0 - frac
        p.funding_paid *= 1.0 - frac
        if reason == "tp1":
            p.scaled = True
            p.scaled_gi = gi
            p.stop = p.entry_price                  # runner SL -> breakeven
            p.be_armed = True                       # tagged 'be' on the exit
        else:
            p.trimmed = True
            p.trimmed_gi = gi
        self.trades.append({
            "symbol": p.symbol, "side": p.side, "score": p.score,
            "entry_gi": p.entry_gi, "entry_t": p.entry_t,
            "signal_t": p.signal_t,
            "entry_price": p.entry_price,
            "exit_price": price, "exit_reason": reason,
            "exit_t": self.timeline[gi] if gi < len(self.timeline)
            else p.entry_t,
            "bars_held": p.bars_held, "pnl": pnl,
            "margin": margin_c, "lev": p.lev,
            "ret_pct": (price / p.entry_price - 1) * p.side * 100,
            "fees": entry_fee_c + exit_fee, "funding": fund_c,
            "meta": dict(p.meta),
        })

    def _apply_funding(self, p: Position, mark: int):
        if not self.cost.funding or p.symbol not in self.fund_rate:
            return
        rate = self.fund_rate[p.symbol].get(mark)
        if rate is None:
            return
        charge = p.notional * rate * p.side
        p.funding_paid += charge
        self.cash -= charge
        self.total_funding += charge

    # ------------------------------------------------------------------
    def run(self, top_k: int | None = 1, min_score: float | None = None,
            allow_pyramiding: bool = False, log_every: int = 500_000):
        tline = self.timeline
        n = len(tline)
        for gi, t in enumerate(tline):
            self.bar_idx = gi
            # 1. advance pointers: bar t is now available
            for s in self.sym:
                d = self.sym[s]
                while self.ptr[s] < len(d["t"]) and d["t"][self.ptr[s]] <= t:
                    self.ptr[s] += 1
            # 2. fill pending entries at the OPEN of bar t
            if self.pending:
                queued, self.pending = self.pending, []
                for (symbol, side, score, exit_model, meta) in queued:
                    d = self.sym[symbol]
                    row = self.ptr[symbol] - 1
                    if row < 0 or d["t"][row] != t:
                        continue            # symbol has no bar at t
                    self._open_position(symbol, side, score, exit_model,
                                        meta, row)
            # 2b. limit orders: check fills against bar t's range
            if self.limit_orders:
                keep: list[LimitOrder] = []
                for lo in self.limit_orders:
                    d = self.sym[lo.symbol]
                    row = self.ptr[lo.symbol] - 1
                    if row < 0 or d["t"][row] != t:
                        if lo.expiry_gi > gi:
                            keep.append(lo)
                        continue
                    if not self._limit_fill_ok(lo, t):
                        continue            # guard cancelled the order
                    hi, low = d["h"][row], d["l"][row]
                    filled = (lo.side > 0 and low <= lo.limit_price) or \
                             (lo.side < 0 and hi >= lo.limit_price)
                    if filled:
                        if not any(q.symbol == lo.symbol
                                   for q in self.positions):
                            self._open_position(
                                lo.symbol, lo.side, lo.score, lo.exit_model,
                                lo.meta, row, fill_price=lo.limit_price,
                                maker=True, signal_t=lo.signal_t)
                    elif lo.expiry_gi > gi:
                        keep.append(lo)
                self.limit_orders = keep
            # 3. exits over bar t for all open positions
            still: list[Position] = []
            for p in self.positions:
                d = self.sym[p.symbol]
                row = self.ptr[p.symbol] - 1
                if row < 0 or d["t"][row] != t:
                    still.append(p)          # symbol has no bar at t
                    continue
                p.bars_held += 1
                h, lo, c_, o_ = (d["h"][row], d["l"][row], d["c"][row],
                                d["o"][row])
                closed = False
                if p.trail is not None:
                    tr = p.trail / 10_000.0
                    if p.side > 0:
                        p.trail_hi = max(p.trail_hi, h)
                        ns = p.trail_hi * (1 - tr)
                        if p.stop is None or ns > p.stop:
                            p.stop = ns
                    else:
                        p.trail_lo = min(p.trail_lo, lo)
                        ns = p.trail_lo * (1 + tr)
                        if p.stop is None or ns < p.stop:
                            p.stop = ns
                # breakeven/lock gate: once MFE >= be_after_r x risk (or
                # >= be_after_bps in absolute terms), the stop snaps to
                # entry + be_to_r x risk.  Deferred on the fill bar (same
                # intrabar path ambiguity as the TP rule).
                if (p.be_after_r is not None or p.be_after_bps is not None) \
                        and p.risk > 0 and gi > p.entry_gi \
                        and p.stop is not None:
                    if p.be_after_bps is not None:
                        trig = p.entry_price * (1 + p.side
                                                * p.be_after_bps / 10_000.0)
                    else:
                        trig = p.entry_price + p.side * p.be_after_r * p.risk
                    if p.side > 0:
                        if h >= trig:
                            ns = p.entry_price + p.be_to_r * p.risk
                            if ns > p.stop:
                                p.stop = ns
                                p.be_armed = True
                    else:
                        if lo <= trig:
                            ns = p.entry_price - p.be_to_r * p.risk
                            if ns < p.stop:
                                p.stop = ns
                                p.be_armed = True
                slip = self.cost.slippage_bps / 10_000.0
                # On the FILL bar itself the intrabar path is ambiguous.
                # Honest 1m rule:
                #  * SL on the fill bar IS legitimate: the order was
                #    resting from the previous close, and an SL touch
                #    beyond the limit necessarily happens AFTER the fill
                #    (price must trade through the limit first).
                #  * TP on the fill bar is DEFERRED: the TP level may have
                #    been touched before the limit fill (up-then-down
                #    path), so counting it would be optimistic.
                allow_tp = gi > p.entry_gi
                # two-stage scale-out: TP1 realizes tp1_frac at its limit
                # (maker) and snaps the runner's stop to breakeven.  On the
                # scale bar itself the BE stop is DEFERRED (the same-bar
                # path is ambiguous), but the runner's TP2 resting limit is
                # allowed (any path to TP2 crosses TP1 first).
                if p.side > 0:
                    if allow_tp and p.target is not None and \
                            o_ >= p.target:
                        self._close_position(p, p.target,
                                             "tp2" if p.tp1 is not None
                                             else "tp")
                        closed = True
                    else:
                        # defensive mid trim (downside first -- the
                        # conservative path wins on ambiguous bars).  A
                        # descending path through the trim is unambiguous,
                        # so it IS allowed on the fill bar (like the SL).
                        if p.trim_price is not None and not p.trimmed \
                                and lo <= p.trim_price:
                            self._scale_out_position(p, p.trim_price, gi,
                                                     "trim")
                        # TP1 deferred on the trim bar (same-bar up/down
                        # path ambiguity)
                        if p.tp1 is not None and not p.scaled and allow_tp \
                                and not (p.trimmed and gi == p.trimmed_gi) \
                                and h >= p.tp1:
                            self._scale_out_position(p, p.tp1, gi, "tp1")
                        hit_sl = p.stop is not None and lo <= p.stop
                        if p.scaled and gi == p.scaled_gi:
                            hit_sl = False      # BE stop effective next bar
                        hit_tp = allow_tp and p.target is not None and \
                            h >= p.target
                        sl_reason = "be" if p.be_armed else "sl"
                        if hit_sl and hit_tp:
                            self._close_position(p, p.stop * (1 + slip),
                                                 sl_reason)
                            closed = True
                        elif hit_sl:
                            self._close_position(p, p.stop * (1 + slip),
                                                 sl_reason)
                            closed = True
                        elif hit_tp:
                            self._close_position(p, p.target,
                                                 "tp2" if p.tp1 is not None
                                                 else "tp")
                            closed = True
                else:
                    if allow_tp and p.target is not None and \
                            o_ <= p.target:
                        self._close_position(p, p.target,
                                             "tp2" if p.tp1 is not None
                                             else "tp")
                        closed = True
                    else:
                        if p.trim_price is not None and not p.trimmed \
                                and h >= p.trim_price:
                            self._scale_out_position(p, p.trim_price, gi,
                                                     "trim")
                        if p.tp1 is not None and not p.scaled and allow_tp \
                                and not (p.trimmed and gi == p.trimmed_gi) \
                                and lo <= p.tp1:
                            self._scale_out_position(p, p.tp1, gi, "tp1")
                        hit_sl = p.stop is not None and h >= p.stop
                        if p.scaled and gi == p.scaled_gi:
                            hit_sl = False      # BE stop effective next bar
                        hit_tp = allow_tp and p.target is not None and \
                            lo <= p.target
                        sl_reason = "be" if p.be_armed else "sl"
                        if hit_sl and hit_tp:
                            self._close_position(p, p.stop * (1 - slip),
                                                 sl_reason)
                            closed = True
                        elif hit_sl:
                            self._close_position(p, p.stop * (1 - slip),
                                                 sl_reason)
                            closed = True
                        elif hit_tp:
                            self._close_position(p, p.target,
                                                 "tp2" if p.tp1 is not None
                                                 else "tp")
                            closed = True
                if closed:
                    continue
                # scratch rule: still negative after neg_bars bars -> kill
                # at market instead of waiting for the structural stop
                if p.neg_bars and p.bars_held >= p.neg_bars and \
                        (c_ - p.entry_price) * p.side < 0:
                    self._close_position(p, c_, "negkill")
                    continue
                if p.max_bars and p.bars_held >= p.max_bars:
                    self._close_position(p, c_, "time")
                    continue
                # funding at marks crossed while holding (mark = bar open)
                if t % MARK_MS == 0 and p.entry_gi < gi:
                    self._apply_funding(p, t)
                still.append(p)
            self.positions = still

            # 4. new signals from this bar's close -> pending for next bar
            if t in self.events:
                cands = self.events[t]
                if min_score is not None:
                    cands = [c for c in cands if c[2] >= min_score]
                if cands:
                    cands.sort(key=lambda c: -c[2])
                    take = cands if top_k is None else cands[:top_k]
                    for (symbol, side, score, exit_model, meta) in take:
                        if not allow_pyramiding and any(
                                q.symbol == symbol for q in self.positions):
                            continue
                        d = self.sym[symbol]
                        if self.ptr[symbol] >= len(d["t"]):
                            continue
                        if not self._candidate_ok(symbol, side, score,
                                                  exit_model, meta, t):
                            continue        # guard rejected the signal
                        if meta.get("limit_bps"):
                            # passive entry: limit order resting from next bar
                            row = self.ptr[symbol] - 1
                            ref = d["c"][row]
                            limit = ref * ((1 - meta["limit_bps"] / 10_000.0)
                                           if side > 0
                                           else (1 + meta["limit_bps"] /
                                                 10_000.0))
                            if os.environ.get("QUANT_DEBUG_LIMITS"):
                                print(f"[L] gi={gi} t={t} {symbol} side={side}"
                                      f" ref={ref} limit={limit} "
                                      f"meta={meta}", flush=True)
                            wait = int(meta.get("limit_wait_bars", 5))
                            self.limit_orders.append(LimitOrder(
                                symbol=symbol, side=side, limit_price=limit,
                                expiry_gi=gi + wait + 1, score=score,
                                exit_model=dict(exit_model), meta=dict(meta),
                                signal_t=int(t)))
                        else:
                            self.pending.append(
                                (symbol, side, score, exit_model, meta))
            # 6. equity marking (once per UTC day, at midnight bars)
            if t % 86_400_000 == 0:
                self.equity_curve.append((t, self.equity_now()))
            if gi % log_every == 0 and gi > 0:
                log.debug("bar %d/%d eq=%.2f pos=%d", gi, n,
                          self.equity_now(), len(self.positions))
        self.equity_curve.append((tline[-1], self.equity_now()))
        return self


def equity_daily(eq: list[tuple[int, float]]) -> pd.DataFrame:
    df = pd.DataFrame(eq, columns=["t", "equity"])
    df["day"] = df["t"] // 86_400_000
    daily = pd.DataFrame({"eq": df.groupby("day")["equity"].last()})
    daily["eq_first"] = daily["eq"]
    daily["eq_last"] = daily["eq"]
    daily["roe"] = daily["eq"].pct_change()      # day-over-day
    daily = daily.iloc[1:]
    return daily


def stats(trades: list[dict], equity_curve: list[tuple[int, float]]) -> dict:
    n = len(trades)
    out = {"trades": n}
    if n == 0:
        return out
    pnl = pd.Series([t["pnl"] for t in trades])
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    out["win_rate"] = float((pnl > 0).mean())
    out["profit_factor"] = float(wins.sum() / abs(losses.sum())) \
        if abs(losses.sum()) > 0 else float("inf")
    out["expectancy"] = float(pnl.mean())
    out["total_pnl"] = float(pnl.sum())
    out["fees"] = float(pd.Series([t["fees"] for t in trades]).sum())
    out["funding"] = float(pd.Series([t["funding"] for t in trades]).sum())
    out["avg_bars"] = float(pd.Series([t["bars_held"] for t in trades]).mean())
    daily = equity_daily(equity_curve)
    roe = daily["roe"]
    out["days"] = len(roe)
    out["avg_daily_roe"] = float(roe.mean() * 100)
    out["median_daily_roe"] = float(roe.median() * 100)
    out["pct_days_positive"] = float((roe > 0).mean() * 100)
    out["pct_days_100pct"] = float((roe >= 1.0).mean() * 100)
    out["best_day_roe"] = float(roe.max() * 100)
    out["worst_day_roe"] = float(roe.min() * 100)
    eq = pd.Series([e for _, e in equity_curve])
    if len(eq) > 1:
        dd = eq / eq.cummax() - 1
        out["max_dd"] = float(dd.min() * 100)
        rets = eq.pct_change().dropna()
        if len(rets) > 1 and rets.std() > 0:
            out["sharpe"] = float(rets.mean() / rets.std()
                                  * np.sqrt(525_600))
            dn = rets[rets < 0]
            out["sortino"] = float(rets.mean() / dn.std()
                                   * np.sqrt(525_600)) \
                if len(dn) > 0 and dn.std() > 0 else float("inf")
    s = (pnl > 0).astype(int)
    streak = cur = 0
    for x in s:
        cur = cur + 1 if x == 0 else 0
        streak = max(streak, cur)
    out["max_losing_streak"] = streak
    out["final_equity"] = float(eq.iloc[-1])
    out["trades_per_day"] = n / max(len(roe), 1)
    return out
