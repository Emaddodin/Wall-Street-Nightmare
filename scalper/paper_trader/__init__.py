"""Paper trading -- the SAME engine as the backtester, fed live candles.

TESLA is gone; the VP scalper runs on pure price action + volume profile.
The strategy logic is identical to the backtester: prepare_symbol,
EntryEngine.on_bar, RiskManager and the position manager are the same
objects.  Only the feed and the fill venue differ:

  * candles poll Bitunix public REST every poll_seconds and merge into the
    same parquet store the backtester reads;
  * entries fill at the next poll's price with the LIVE top-of-book spread,
    slippage and fees -- not at the signal bar's close;
  * stops/targets trigger on the forming bar's high/low seen so far;
  * the daily +100% target halts the book until the next session.

State lives in data/state/paper.json and survives restarts; every trade and
rejection goes to the JSONL logs; fills and closes push an ntfy alert.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from config.loader import Config
from engine import BacktestEngine, prepare_symbol
from entry_engine import EntryEngine
from execution import entry_price, round_trip_cost_r
from logger import EventLog
from market_data.client import BitunixPublic
from market_data.store import CandleStore
from position_manager import (Position, build_position, execute_struct_exit,
                              step_position)
from risk_manager import RiskManager
from structure import LONG, SHORT, opposite_bos
import lessons

log = logging.getLogger("scalper.paper")

MIN_MS = 60_000
HOUR_MS = 3_600_000
DAY_MS = 86_400_000


def advance_bars(t_close_list, n_t: int, start_i: int, last_ts):
    """Which 1m bars to evaluate next + the new watermark.

    Pure and regression-tested: first contact evaluates from start_i;
    a growing store evaluates exactly the new closed bar; a sliding
    window evaluates the new closed bar by TIME (never by index, which
    is what silently starved the book before).
    Returns (start_index, new_last_ts); an empty range means "nothing
    new"."""
    if n_t < 2:
        return 0, last_ts
    closes = t_close_list + MIN_MS
    if last_ts is None:
        start = int(start_i)
    else:
        start = int(np.searchsorted(closes, last_ts, side="right"))
        start = max(start, int(start_i))
    new_last_ts = int(closes[n_t - 2])
    return start, new_last_ts


def _stale_signal(t_close: int, now_ms: int, cfg) -> bool:
    """True when a closed bar is too old to trade from.  Feed rotation can
    leave a symbol unprocessed for hours; replaying that backlog would emit
    signals on historical bars and fill them at today's price (phantom
    trades).  Such bars still update indicators -- they just never trade."""
    return now_ms - t_close > cfg.paper.get("max_signal_age_ms", 5 * MIN_MS)


def _fill_through(d: int, lo, hi, sl) -> bool:
    """The bar that tags a resting limit has already traded through the
    stop: LONG bar low below the stop, or SHORT bar high above it.  Filling
    that limit is a knife-catch and is refused."""
    if lo is None or hi is None or sl is None or sl != sl:
        return False
    return (d == LONG and lo < sl) or (d == SHORT and hi > sl)


@dataclass
class PendingPaper:
    sym: str
    at_ms: int
    signal: object


@dataclass
class PaperState:
    equity: float
    start_equity: float
    day_start_ms: int
    day_start_equity: float
    trades_today: int
    consec_losses: int
    cooldown_until_ms: int
    halted: bool
    halt_reason: str
    positions: list[dict] = field(default_factory=list)
    pos_counter: int = 0
    ts: int = 0


class PaperTrader:
    def __init__(self, cfg: Config, store: CandleStore, state_dir: Path,
                 log_dir: Path, live_spread: bool = True):
        self.cfg = cfg
        self.store = store
        self.state_path = state_dir / "paper.json"
        self.state_dir = state_dir
        self.eventlog = EventLog(log_dir)
        self.client = BitunixPublic(pause=0.3)
        self.live_spread = live_spread
        self.entry = EntryEngine(cfg)
        self.risk = RiskManager(cfg, cfg.paper["starting_equity"],
                                cfg.daily["day_start_hour_utc"])
        self.pending: list[PendingPaper] = []
        self.positions: dict[str, Position] = {}
        self.last_i: dict[str, int] = {}
        self.data_dir = state_dir.parent            # .../data
        self._paused: set[str] = set()
        self._autopilot_at = 0.0
        self._goal_ntfy_at = 0.0
        self.symbols: list[str] = []
        self._feed_list: list[str] = []
        self._feed_at: float = 0.0
        self._strat_states: dict = {}
        self.brain = None
        try:
            from brain import Brain
            self.brain = Brain(self.data_dir,
                               self.cfg.raw().get("brain") or {})
            log.info("brain: %s (lessons %d)",
                     "READY, filtering entries" if self.brain.ready()
                     else "warming up", self.brain.n_train())
        except Exception as e:
            log.warning("brain not loaded: %s", e)
        self.state = self._load_state()
        self._apply_state()

    # ------------------------------------------------------------ state
    def refresh_live(self, now_ms: int | None = None) -> None:
        """Push the current 1m close of every open position into live.json
        so the app's position card ticks in near-real-time.  Called from
        step() and from the fast sub-loop between steps."""
        now_ms = now_ms or int(time.time() * 1000)
        live = {}
        for sym in self.positions:
            px = None
            try:
                px = self.client.last_price(sym)
            except Exception:
                px = None
            if px is None:
                try:
                    page = self.client.klines_page(sym, "1m", 1)
                    if not page.empty:
                        px = float(page["close"].iloc[-1])
                except Exception:
                    px = None
            if px is not None and px > 0:
                live[sym] = {"px": px, "ts": now_ms}
        live_path = self.state_dir / "live.json"
        if live:
            tmp = live_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(live))
            tmp.replace(live_path)
        elif live_path.exists():
            live_path.unlink()

    def _load_state(self) -> PaperState:
        p = self.state_path
        if p.exists():
            try:
                return PaperState(**json.loads(p.read_text()))
            except Exception as e:
                log.warning("paper state unreadable (%s) -- fresh", e)
        return PaperState(
            equity=self.cfg.paper["starting_equity"],
            start_equity=self.cfg.paper["starting_equity"],
            day_start_ms=int(time.time() * 1000) // DAY_MS * DAY_MS,
            day_start_equity=self.cfg.paper["starting_equity"],
            trades_today=0, consec_losses=0, cooldown_until_ms=0,
            halted=False, halt_reason="")

    def _apply_state(self) -> None:
        s = self.state
        self.risk.equity = s.equity
        self.risk.day_start_ms = s.day_start_ms
        self.risk.day_start_equity = s.day_start_equity
        self.risk.trades_today = s.trades_today
        self.risk.consec_losses = s.consec_losses
        self.risk.cooldown_until_ms = s.cooldown_until_ms
        self.risk.halted = s.halted
        self.risk.halt_reason = s.halt_reason
        for pd_ in s.positions:
            pos = Position(symbol=pd_["symbol"], direction=pd_["direction"],
                           opened_ms=pd_["opened_ms"], pos_id=pd_["pos_id"],
                           be_active=pd_["be_active"],
                           trail_armed=pd_["trail_armed"],
                           struct_exit_pending=pd_["struct_exit_pending"],
                           realized_pnl=float(pd_.get("realized_pnl", 0.0)))
            for lot in pd_["lots"]:
                from position_manager import Lot
                pos.lots.append(Lot(
                    qty=lot["qty"], kind=lot["kind"],
                    sl=lot["sl"], tp=lot["tp"],
                    entry=lot["entry"],
                    entry_fee=lot.get("entry_fee", 0.0),
                    tp_r=lot.get("tp_r", 0.0),
                    exit_px=lot.get("exit_px"),
                    exit_reason=lot.get("exit_reason", ""),
                    exit_ms=lot.get("exit_ms", 0),
                    pnl=lot.get("pnl", 0.0)))
            pos.meta = pd_.get("meta", {})
            self.positions[pos.symbol] = pos

    def _persist(self) -> None:
        s = self.state
        s.equity = self.risk.equity
        s.day_start_ms = self.risk.day_start_ms
        s.day_start_equity = self.risk.day_start_equity
        s.trades_today = self.risk.trades_today
        s.consec_losses = self.risk.consec_losses
        s.cooldown_until_ms = self.risk.cooldown_until_ms
        s.halted = self.risk.halted
        s.halt_reason = self.risk.halt_reason
        s.ts = int(time.time() * 1000)
        s.positions = [self._pos_to_dict(p) for p in self.positions.values()]
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(s.__dict__, indent=1))
        tmp.replace(self.state_path)

    def _pos_to_dict(self, pos: Position) -> dict:
        return {
            "symbol": pos.symbol, "direction": pos.direction,
            "opened_ms": pos.opened_ms, "pos_id": pos.pos_id,
            "be_active": pos.be_active, "trail_armed": pos.trail_armed,
            "struct_exit_pending": pos.struct_exit_pending,
            "realized_pnl": pos.realized_pnl,
            "meta": pos.meta,
            "lots": [{"qty": l.qty, "kind": l.kind, "sl": l.sl, "tp": l.tp,
                      "entry": l.entry, "entry_fee": l.entry_fee,
                      "tp_r": l.tp_r,
                      # a CLOSED lot must stay closed across restarts --
                      # otherwise it resurrects and re-exits (double PnL)
                      "exit_px": l.exit_px, "exit_reason": l.exit_reason,
                      "exit_ms": l.exit_ms, "pnl": l.pnl}
                     for l in pos.lots],
        }

    # ------------------------------------------------------------ loop
    def step(self) -> dict:
        """One poll iteration.  Returns a status dict for the app."""
        cfg = self.cfg
        now_ms = int(time.time() * 1000)
        self.risk.roll_day(now_ms)
        # daily-goal heartbeat: every 4h, report distance to the target
        if time.time() - self._goal_ntfy_at > 4 * 3600:
            self._goal_ntfy_at = time.time()
            base = self.risk.day_start_equity or 1.0
            day_pct = (self.risk.equity - base) / base * 100.0
            target = base * 2.0
            need = (target - self.risk.equity) / max(self.risk.equity, 0.01) * 100.0
            self._ntfy(f"goal: equity {self.risk.equity:.2f} | "
                       f"day {day_pct:+.1f}% | target {target:.2f} | "
                       f"need +{need:.0f}% to hit")
        # self-teaching: re-derive strategy pauses from the lesson journal
        # (can be muted by the operator -- then every strategy hunts; a
        # from_ms timestamp re-enables it automatically at that instant)
        autopilot_on = cfg.lessons.get("autopilot_enabled", True)
        from_ms = cfg.lessons.get("autopilot_enabled_from_ms", 0)
        if not autopilot_on and from_ms and now_ms >= from_ms:
            autopilot_on = True
        if autopilot_on \
                and time.time() - self._autopilot_at > 30:
            self._autopilot_at = time.time()
            new_rules = {**lessons.evaluate(self.data_dir),
                         **lessons.discover(self.data_dir)}
            new_paused = set(new_rules)
            if new_paused != self._paused:
                lessons.write_autopilot(self.data_dir, new_rules)
                self._paused = new_paused
                if new_paused:
                    log.info("bot self-taught pause: %s", new_rules)
                    self._ntfy("bot paused strategies from its own lessons: "
                               + ", ".join(f"{k} ({v['reason']})"
                                           for k, v in new_rules.items()))
                else:
                    log.info("bot re-armed all strategies")
                    self._ntfy("bot re-armed all strategies")
        elif not autopilot_on and self._paused:
            # muted: clear any stale pauses so every strategy hunts
            self._paused = set()
            lessons.write_autopilot(self.data_dir, {})
        if self.risk.day_start_ms != self.state.day_start_ms:
            # new day (03:30 Tehran): fresh settings, fresh clock
            prev_start = self.state.day_start_ms
            self._persist()
            lessons.write_autopilot(self.data_dir, {})
            self._paused = set()
            self._goal_ntfy_at = 0.0
            self._ntfy(f"new day: fresh settings, base "
                       f"{self.risk.day_start_equity:.2f}, target "
                       f"{self.risk.day_start_equity * 2:.2f}")
            try:
                rev = lessons.daily_review(self.data_dir, prev_start,
                                           self.risk.day_start_ms)
                if rev["n"]:
                    by_txt = " | ".join(
                        f"{k} {v['n']}t {v['pnl']:+.2f}"
                        for k, v in rev["by"].items())
                    self._ntfy(f"bot daily lesson: {rev['wins']}W/"
                               f"{rev['losses']}L pnl {rev['pnl']:+.2f} "
                               f"({by_txt})")
                    log.info("daily lesson pushed: %s", rev)
            except Exception as e:
                log.warning("daily lesson failed: %s", e)
        paused = (self.state_dir / "paused").exists()
        # 1 -- fresh candles for the feed symbols
        self.symbols = self._pick_feed()
        for sym in self.symbols:
            try:
                page = self.client.klines_page(sym, "1m", 200)
                if not page.empty:
                    self.store.merge(sym, page, "1m")
            except Exception as e:
                log.warning("candle poll %s failed: %s", sym, e)
        # 2 -- step the engine on newly closed 1m bars
        fills_this_poll = []
        closes_this_poll = []
        if not paused:
            for sym in list(self.symbols):
                df1 = self.store.load(sym, "1m")
                if len(df1) < 1500:
                    continue
                tail = df1.tail(int(cfg.paper["rebuild_days"] * 1440))
                if len(tail) < 2:
                    continue
                # last_i holds the CLOSE TIME of the last processed bar
                # (time-based, not index-based: a sliding tail window would
                # make index bookkeeping silently skip every new bar)
                last_ts = self.last_i.get(sym)
                sd = prepare_symbol(sym, tail, cfg)
                n_t = len(sd.tfs["1m"].t)
                t1 = sd.tfs["1m"]
                if n_t < 2:
                    continue
                start, new_last_ts = advance_bars(t1.t, n_t, sd.start_i,
                                                  last_ts)
                for i in range(start, n_t - 1):
                    t_close = int(t1.t[i] + MIN_MS)
                    # STALE-BAR GUARD: when a symbol re-enters the feed
                    # after hours away, the backlog above replays old bars
                    # (e.g. yesterday's PRIME).  Signals from those bars
                    # would fill at TODAY's price -- phantom trades.  Ingest
                    # the bar for indicator continuity, never trade it.
                    if _stale_signal(t_close, now_ms, cfg):
                        continue
                    j15 = sd.i15[i]
                    if j15 < 5:
                        continue
                    tf15 = sd.tfs["15m"]
                    if tf15.bias_dir[j15] == 0:
                        continue
                    if cfg.volatility_filter["enabled"] \
                            and not np.isnan(tf15.atr_z[j15]) \
                            and tf15.atr_z[j15] > cfg.volatility_filter["atr_zscore_max"]:
                        continue
                    from engine import session_label
                    sess = session_label(t_close, cfg)
                    if "any" not in cfg.strategy["session"]["trade_windows"] \
                            and sess not in cfg.strategy["session"]["trade_windows"]:
                        continue
                    sigs, rej = self.entry.on_bar(sd, i, tf15, j15, t_close,
                                                   self._strat_states)
                    if self._paused:
                        hour_key = f"hour:{(t_close // HOUR_MS) % 24}"
                        sigs = [sg for sg in sigs
                                if f"s:{sg.strategy}" not in self._paused
                                and f"m:{sg.entry_model}" not in self._paused
                                and f"sym:{sym}" not in self._paused
                                and hour_key not in self._paused]
                    if rej:
                        self._log_rejects(sym, t_close, rej)
                    if not sigs:
                        continue
                    self.pending.append(PendingPaper(sym=sym, at_ms=t_close,
                                                     signal=sigs[0]))
                if n_t >= 2:
                    self.last_i[sym] = new_last_ts
        # 3 -- fill pending: market at the current price, limits on the
        # current bar's range (model-level entries)
        still = []
        for p in self.pending:
            try:
                page = self.client.klines_page(p.sym, "1m", 1)
                px = float(page["close"].iloc[-1]) if not page.empty else None
                hi = float(page["high"].iloc[-1]) if not page.empty else None
                lo = float(page["low"].iloc[-1]) if not page.empty else None
            except Exception:
                px = hi = lo = None
            if px is None:
                still.append(p)
                continue
            sig = p.signal
            d = sig.direction
            buf = cfg.stop["atr_buffer_mult"] * sig.atr1m
            sl = sig.swing_level - d * buf
            if not (isinstance(sl, (int, float)) and sl == sl):
                continue
            if sig.entry_level is not None:
                # resting limit; expires after entry_expiry_bars 1m bars
                bars_open = (now_ms - p.at_ms) // MIN_MS
                if bars_open > sig.entry_expiry_bars:
                    continue
                lvl = float(sig.entry_level)
                if not (lo is not None and lo <= lvl <= hi):
                    still.append(p)
                    continue
                # FILL-THROUGH GUARD: the bar that tags the limit must not
                # have already traded through the stop.  Entering a candle
                # that blew past the stop is a knife-catch, not a good call
                # (AKEUSDT 2026-09-09: filled and stopped in one bar).
                if _fill_through(d, lo, hi, sl):
                    continue
                fill = lvl
                fee_frac = cfg.execution["fee_bps"] / 1e4
            else:
                # market fill: only within a few bars of the signal bar
                # close -- never a stale backlog signal
                bars_open = (now_ms - p.at_ms) // MIN_MS
                if bars_open > cfg.paper.get("max_fill_age_bars", 3):
                    continue
                fill, fee_frac = entry_price(px, sig.direction,
                                             cfg.execution["fee_bps"],
                                             cfg.execution["slippage_bps"])
            ok, why = self.risk.can_enter(now_ms)
            if not ok or len(self.positions) >= cfg.risk["max_positions"] \
                    or p.sym in self.positions:
                continue
            if not (isinstance(fill, (int, float))) or fill != fill:
                continue
            if (d == LONG and sl >= fill) or (d == SHORT and sl <= fill):
                continue                      # stale signal gapped past its stop
            dist = abs(fill - sl)
            is_breakout = getattr(sig, "strategy", "") == "breakout"
            if dist <= 0 or (dist / fill * 100 > cfg.stop["max_sl_pct"]
                             and not is_breakout):
                continue
            # fee viability -- same bound the backtest enforces, so the live
            # book cannot take setups the backtest would have refused.
            if round_trip_cost_r(dist / fill, cfg.execution["fee_bps"],
                                 cfg.execution["slippage_bps"]) \
                    > cfg.execution["max_fee_r"]:
                continue
            # knife-catch guard (engine parity): a stop tighter than
            # min_sl_atr_mult x the 1m ATR is one noise bar from death
            min_sl = cfg.stop.get("min_sl_atr_mult", 0.0)
            if min_sl > 0 and sig.atr1m > 0 and not is_breakout \
                    and dist / sig.atr1m < min_sl:
                continue
            # volume-delta must agree with the trade (engine parity)
            cvd_cfg = cfg.strategy.get("cvd_filter") or {}
            if cvd_cfg.get("enabled"):
                try:
                    df_cvd = self.store.load(p.sym, "1m")
                    seg = df_cvd.tail(int(cvd_cfg.get("lookback", 60)))
                    vsum = float(seg["volume"].sum())
                    if vsum > 0 and len(seg) >= 5:
                        delta = float(((np.sign(seg["close"] - seg["open"])
                                        * seg["volume"]).sum()) / vsum)
                        want = float(cvd_cfg.get("min_abs", 0.05))
                        if (d == LONG and delta < want) \
                                or (d == SHORT and delta > -want):
                            continue
                except Exception:
                    pass
            size = self.risk.size(fill, sl)
            if size["qty"] <= 0 or size["notional"] < cfg.risk["min_order_notional_usdt"]:
                continue
            # ---- the brain: learned win probability as a selectivity gate
            brain_prob = None
            brain_cfg = cfg.raw().get("brain") or {}
            if brain_cfg.get("enabled", False) and self.brain is not None:
                try:
                    from brain import features_live
                    from strategies.inventor import coin_meta_from_frame
                    df_sym = self.store.load(p.sym, "1m")
                    kind = (coin_meta_from_frame(p.sym, df_sym)["kind"]
                            if len(df_sym) else "?")
                    feats = features_live(
                        p.sym, d, getattr(sig, "strategy", ""),
                        sig.entry_model, fill, sl, sig.tp_first, sig.atr1m,
                        size["leverage"], now_ms, kind, cfg, df=df_sym)
                    take, brain_prob = self.brain.should_take(feats,
                                                              log=log.info)
                    if not take:
                        log.info("brain veto: %s win-prob %.2f below bar %.2f",
                                 p.sym, brain_prob or 0.0,
                                 brain_cfg.get("veto_prob", 0.40))
                        self.eventlog.rejection(
                            {"ts_ms": now_ms, "symbol": p.sym,
                             "rejections": [{"leg": "brain",
                                             "why": "win prob below bar"}],
                             "passed_legs": 4})
                        continue
                except Exception as e:
                    log.warning("brain gate failed open: %s", e)
                    brain_prob = None
            entry_fee = size["notional"] * fee_frac
            self.state.pos_counter += 1
            pos = build_position(p.sym, d, fill, size["qty"], sl, cfg,
                                 self._meta(sig, size), entry_fee,
                                 now_ms, sig.atr1m,
                                 tp_first=sig.tp_first)
            pos.pos_id = self.state.pos_counter
            if brain_prob is not None:
                pos.meta["brain_prob"] = round(brain_prob, 3)
            self.positions[p.sym] = pos
            self.risk.on_fill(now_ms, entry_fee)
            fills_this_poll.append({"symbol": p.sym, "direction": d,
                                    "price": fill, "sl": sl,
                                    "qty": size["qty"], "lev": size["leverage"]})
            log.info("PAPER FILL %s %s @ %.8f sl=%.8f lev=%.1f model=%s%s",
                     p.sym, "LONG" if d == LONG else "SHORT", fill, sl,
                     size["leverage"], sig.entry_model,
                     " [limit]" if sig.entry_level is not None else "")
            self.eventlog.write("fills", {"ts_ms": now_ms, "symbol": p.sym,
                                          "direction": d, "price": fill,
                                          "sl": sl, "qty": size["qty"],
                                          "leverage": size["leverage"],
                                          "model": sig.entry_model})
            tp_txt = (f" tp={sig.tp_first:.6g}"
                      if sig.tp_first else " tp=none (full runner)")
            self._ntfy(f"PAPER {p.sym} {'LONG' if d == LONG else 'SHORT'} "
                       f"@ {fill:.6g} sl={sl:.6g}{tp_txt} "
                       f"lev {size['leverage']:.0f}x [{sig.entry_model}]")
        self.pending = still
        # 4 -- manage open positions against the newest bar data
        for sym in list(self.positions):
            pos = self.positions[sym]
            df1 = self.store.load(sym, "1m")
            if len(df1) < 2:
                continue
            last = df1.iloc[-1]
            t_close = int(last["open_time"] + MIN_MS)
            sd = prepare_symbol(
                sym, df1.tail(int(cfg.paper["rebuild_days"] * 1440)), cfg)
            t1 = sd.tfs["1m"]
            i = len(t1.t) - 1
            closed = execute_struct_exit(pos, float(t1.o[i]), t_close,
                                         cfg.execution["fee_bps"],
                                         cfg.execution["slippage_bps"])
            d = pos.direction
            lsw_lo = t1.last_sl[i] if not np.isnan(t1.last_sl[i]) else None
            lsw_hi = t1.last_sh[i] if not np.isnan(t1.last_sh[i]) else None
            opp = opposite_bos(t1, i, d)
            closed += step_position(pos, cfg, float(t1.h[i]), float(t1.l[i]),
                                    t_close, lsw_lo, lsw_hi, float(t1.atr[i]),
                                    opp, cfg.execution["fee_bps"],
                                    cfg.execution["slippage_bps"])
            for lot_rec in closed:
                rec = self._trade_record(sym, pos, lot_rec)
                held_m = (rec.get("ts_ms", 0) - rec.get("opened_ms", 0)) // 60_000
                if lot_rec["reason"] == "STOP" and held_m <= 2:
                    rec["knife_catch"] = True
                lessons.record_lesson(self.data_dir, rec)
                self.eventlog.trade(rec)
                self.eventlog.flush()
                pos.realized_pnl += lot_rec["pnl"]
                # credit this leg's PnL to the balance the moment it is
                # banked -- a trailing runner that closes in profit shows
                # up in the wallet right away, while the other leg keeps
                # running
                self.risk.on_close(lot_rec["pnl"], t_close)
                closes_this_poll.append(rec)
                log.info("PAPER CLOSE %s %s %s pnl=%+.2f",
                         sym, lot_rec["lot"], lot_rec["reason"], lot_rec["pnl"])
                open_left = sum(1 for l in pos.lots if l.open)
                tail = (" | LEG CLOSED, position still open"
                        if open_left else " | POSITION CLOSED")
                self._ntfy(f"PAPER CLOSE {sym} {lot_rec['lot']} "
                           f"{lot_rec['reason']} pnl={lot_rec['pnl']:+.2f} | "
                           f"entry {lot_rec['entry']:.6g} -> "
                           f"exit {lot_rec['exit']:.6g} "
                           f"(sl {lot_rec['sl']:.6g}){tail} | "
                           f"balance now {self.risk.equity:.2f}")
            if not pos.open:
                del self.positions[sym]
        # 5 -- live prices for the app (visible updates between events)
        self.refresh_live(now_ms)
        self._persist()
        return {"fills": fills_this_poll, "closes": closes_this_poll,
                "risk": self.risk.snapshot(now_ms),
                "equity": self.risk.equity}

    # ------------------------------------------------------------ helpers
    def _pick_feed(self) -> list[str]:
        """Volume + ATR + spread blend over the top-volume candidates,
        re-ranked every 5 minutes (the high-ATR movers)."""
        now = time.time()
        if self._feed_list and now - self._feed_at < 300:
            return self._feed_list
        cfg = self.cfg
        try:
            ticks = self.client.tickers()
        except Exception:
            return list(self.symbols) or list(self.positions)
        cands = [t for t in ticks if t["symbol"].endswith("USDT")
                 and t["usdt_volume_24h"] >= cfg.universe["min_volume_usdt_24h"]]
        cands.sort(key=lambda t: -t["usdt_volume_24h"])
        cands = cands[:60]
        rows = []
        for t in cands:
            sym = t["symbol"]
            df1 = self.store.load(sym, "1m")
            if len(df1) < 3000:
                try:
                    boot = self.client.klines(sym, "1m", 3000)
                    if len(boot) > 1000:
                        self.store.merge(sym, boot, "1m")
                        df1 = self.store.load(sym, "1m")
                except Exception:
                    pass
            if len(df1) < 1500:
                continue
            try:
                from indicators import atr as atr_ind
                from market_data.store import resample_ohlcv
                k15 = resample_ohlcv(df1.tail(6000), 15)
                a = atr_ind(k15, cfg.stop["atr_period"])
                atr_pct = float(a.iloc[-1] / k15["close"].iloc[-1] * 100.0)
                if atr_pct < cfg.universe["min_atr_pct"] \
                        or atr_pct > cfg.universe["volatility_cap_atr_pct"]:
                    continue
                spr = float(((k15["high"] - k15["low"]) / k15["close"] * 10_000.0)
                            .tail(96).median())
                rows.append({"symbol": sym, "volume": t["usdt_volume_24h"],
                             "vola": atr_pct, "trend": 0.0, "spread": spr})
            except Exception:
                continue
        if not rows:
            self._feed_list = [t["symbol"] for t in cands[:cfg.paper["feed_symbols"]]]
        else:
            import pandas as pd
            rows = pd.DataFrame(rows)
            w = cfg.universe["rank_weights"]
            rows["vol_pct"] = rows["volume"].rank(pct=True)
            rows["atr_pct"] = rows["vola"].rank(pct=True)
            rows["sp_pct"] = (1.0 - rows["spread"].rank(pct=True))
            rows["rank"] = ((w["liquidity"] + w["volume"]) * rows["vol_pct"]
                            + w["volatility"] * rows["atr_pct"]
                            + w["trend"] * 0.5
                            + w["spread"] * rows["sp_pct"])
            rows = rows.sort_values("rank", ascending=False)
            self._feed_list = rows["symbol"].head(cfg.paper["feed_symbols"]).tolist()
        for p in self.positions:
            if p not in self._feed_list:
                self._feed_list.append(p)
        self._feed_at = now
        return self._feed_list

    def _meta(self, sig, size: dict) -> dict:
        return {
            "signal_bar_ms": sig.at_ms, "entry_ref": sig.entry_price,
            "swing_level": sig.swing_level, "entry_model": sig.entry_model,
            "strategy": getattr(sig, "strategy", ""),
            "bias": sig.bias, "vp_poc": sig.vp_poc,
            "atr1m": sig.atr1m,
            "risk_pct": self.cfg.risk["risk_per_trade"],
            "leverage": size["leverage"], "risk_amount": size["risk_amount"],
        }

    def _trade_record(self, sym: str, pos: Position, lot_rec: dict) -> dict:
        return BacktestEngine(self.cfg)._trade_record(sym, pos, lot_rec)

    def _log_rejects(self, sym: str, t_close: int, rej: list[dict]) -> None:
        passed = rej[-1].get("passed_legs", 0) if rej else 0
        if passed < self.cfg.research["log_reject_min_legs"]:
            return
        self.eventlog.rejection({"ts_ms": t_close, "symbol": sym,
                                 "rejections": rej, "passed_legs": passed})
        self.eventlog.flush()

    def _ntfy(self, msg: str) -> None:
        topic = os.getenv("NTFY_TOPIC")
        if not topic:
            return
        try:
            import urllib.request
            req = urllib.request.Request(
                f"https://ntfy.sh/{topic}", data=msg.encode(),
                headers={"Title": "scalper"})
            urllib.request.urlopen(req, timeout=8).close()
        except Exception as e:
            log.warning("ntfy push failed: %s", e)
