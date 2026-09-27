"""
xau_alpha/live/trader.py
Live trader for a frozen xau_alpha candidate (cand/<module>.py): same orders() as the backtest, position management
that mirrors sim.py, and a two-phase account state machine.

  FLIP  (equity < handoff_equity): 0.01 lot only; trade only if stop distance is in [flip_stop_min, flip_stop_max]
        and the 0.01-lot margin at 1:500 (1:200 near news) fits; at most `flip_max_losses_day` losses per day.
  MAIN  (equity >= handoff_equity): fractional risk sizing (main_risk_frac), same guards, larger daily budget.
        Demotes back to FLIP below demote_equity. HALT when 0.01 lot can no longer be opened.

Mirrors sim.py semantics: a decision at the close of bar i acts on the first quote after it; stop/limit orders are
emulated from quotes (long stop fills when ask >= px, long limit when ask <= px; mirrored for shorts); long exits
use bid, short exits use ask; SL is also placed broker-side on the ticket (the gateway cannot modify it later, so
break-even / trailing stops are enforced in software by flattening); TP, tmax and flat are enforced in software.

Guards (entry path only; exits always run): calendar blackout (fail closed), spread cap, session/rollover/Friday
windows, daily loss budget. Laya/Jeff are called in SHADOW mode only: their verdict is logged with the trade and
never vetoes or sizes (SYNTHESIS.md section 4, N5).
"""
import asyncio
import importlib
import json
import logging
import math
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "lib", ROOT / "cand", ROOT / "live"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from bars import LiveBars  # noqa: E402
from news import NewsBlackout  # noqa: E402

log = logging.getLogger("XauAlpha")
OZ_PER_LOT = 100.0


@dataclass
class TraderConfig:
    module: str
    params: dict = field(default_factory=dict)
    warmup_bars: int = 4_000              # bars passed to orders() each minute (module may override WARMUP_BARS)
    min_bars: int = 1_500                 # never trade before this many REAL bars exist
    handoff_equity: float = 100.0
    demote_equity: float = 60.0
    flip_stop_min: float = 1.2
    flip_stop_max: float = 4.0
    flip_max_losses_day: int = 2
    main_risk_frac: float = 0.02
    main_max_risk_frac: float = 0.05      # allow min-lot rounding up to this risk
    main_daily_loss_frac: float = 0.06
    leverage: float = 500.0
    news_leverage: float = 200.0
    margin_buffer: float = 1.05
    spread_cap: float = 0.40
    spread_rel_cap: float = 2.0           # also block if spread > rel_cap x median spread of the last 60 bars
    blackout_flip: tuple = (30.0, 30.0)   # minutes before/after HIGH events while equity < ~$21 (news margin)
    blackout_main: tuple = (5.0, 15.0)
    no_entry_utc: tuple = ((20 * 60 + 30, 23 * 60 + 30),)    # daily break / reopen spread window
    friday_last_entry_utc: int = 19 * 60
    friday_flatten_utc: int = 20 * 60 + 30
    mode: str = "DEMO"
    allow_real: bool = False
    state_dir: str = "data/state"


class Position:
    def __init__(self, order: dict, d: int, lots: float, entry: float, sl: float, tp: float, t_in: float):
        self.order, self.d, self.lots, self.entry, self.sl0, self.tp, self.t_in = order, d, lots, entry, sl, tp, t_in
        self.peak = -math.inf      # best exit-side price in long space seen BEFORE the current quote
        self.stop = sl * d         # long-space stop in force

    def long_space(self, price: float) -> float:
        return price * self.d


class AlphaTrader:
    def __init__(self, gateway, cfg: TraderConfig, oracle=None, now=time.time):
        self.g = gateway
        self.cfg = cfg
        self.now = now
        self.mod = importlib.import_module(cfg.module)
        self.warmup = int(getattr(self.mod, "WARMUP_BARS", cfg.warmup_bars))
        sd = Path(cfg.state_dir)
        sd.mkdir(parents=True, exist_ok=True)
        self.bars = LiveBars(sd / f"xau_alpha_bars.json")
        self.news = NewsBlackout()
        self.oracle = oracle
        self.trade_log = sd / "xau_alpha_trades.jsonl"
        self.quote_log_dir = sd / "quotes"
        self.pos: Optional[Position] = None
        self.pending: list[dict] = []
        self.equity: float = 0.0
        self.phase = "FLIP"
        self.day = ""
        self.day_start_eq = 0.0
        self.losses_today = 0
        self.halted = ""
        self._last_quote_log = 0.0
        self._busy = False
        if cfg.mode.upper() == "REAL" and not cfg.allow_real:
            raise RuntimeError("REAL mode requires allow_real=True (explicit operator opt-in)")

    # ------------------------------------------------------------------------------------------------------------
    async def refresh_equity(self):
        acc = await self.g.get_account_snapshot()
        if acc and acc.equity > 0:
            self.equity = acc.equity
        self._update_phase()
        return acc

    def _update_phase(self):
        c = self.cfg
        if self.phase == "FLIP" and self.equity >= c.handoff_equity:
            self.phase = "MAIN"
            log.info("PHASE -> MAIN at equity %.2f", self.equity)
        elif self.phase == "MAIN" and self.equity < c.demote_equity:
            self.phase = "FLIP"
            log.info("PHASE -> FLIP (demoted) at equity %.2f", self.equity)

    def _new_day(self, now_s: float):
        d = datetime.fromtimestamp(now_s, timezone.utc).strftime("%Y-%m-%d")
        if d != self.day:
            self.day, self.day_start_eq, self.losses_today = d, self.equity, 0

    # ------------------------------------------------------------------------------------------------------------
    async def on_quote(self, bid: float, ask: float, ts_s: Optional[float] = None):
        ts_s = ts_s if ts_s is not None else self.now()
        self._log_quote(bid, ask, ts_s)
        closed = self.bars.on_quote(bid, ask, ts_s)
        self._new_day(ts_s)
        # 1) exits first, on every quote
        if self.pos is not None:
            await self._manage(bid, ask, ts_s)
        # 2) pending stop/limit orders
        if self.pos is None and self.pending:
            await self._check_pending(bid, ask, ts_s)
        # 3) new decisions on a closed bar
        if closed is not None:
            self.bars.save()
            await self._on_bar_close(closed, bid, ask, ts_s)

    async def on_timer(self, bid: float, ask: float, ts_s: Optional[float] = None):
        """Call ~1/s even without new quotes so quiet minutes still close."""
        ts_s = ts_s if ts_s is not None else self.now()
        closed = self.bars.close_stale(ts_s)
        if closed is not None:
            self.bars.save()
            await self._on_bar_close(closed, bid, ask, ts_s)

    # ------------------------------------------------------------------------------------------------------------
    async def _on_bar_close(self, bar_ts: int, bid: float, ask: float, ts_s: float):
        if len(self.bars) < self.cfg.min_bars:
            return
        t_dec = bar_ts + 60_000
        try:
            frame = self.bars.frame(self.warmup)
            orders = [o for o in self.mod.orders(frame, **self.cfg.params) if o["t"] == t_dec]
        except Exception as e:
            log.exception("orders() failed: %s", e)
            return
        # expire stale pending orders
        self.pending = [p for p in self.pending if p.get("exp", 0) > ts_s * 1000]
        for o in orders:
            why = self._entry_block(o, bid, ask, ts_s)
            self._shadow(o, why, bid, ask)
            if why:
                log.info("signal %s %+d blocked: %s", o.get("tag", ""), o["d"], why)
                continue
            if o.get("kind", "mkt") == "mkt":
                if self.pos is None:
                    await self._open(o, bid, ask, ts_s)
            else:
                self.pending.append(o)

    def _entry_block(self, o: dict, bid: float, ask: float, ts_s: float) -> Optional[str]:
        c = self.cfg
        if self.halted:
            return f"halted: {self.halted}"
        if self.pos is not None:
            return "position open"
        now = datetime.fromtimestamp(ts_s, timezone.utc)
        mod = now.hour * 60 + now.minute
        if now.weekday() == 4 and mod >= c.friday_last_entry_utc:
            return "friday cutoff"
        if now.weekday() >= 5:
            return "weekend"
        for a, b in c.no_entry_utc:
            if a <= mod < b:
                return "daily break window"
        before, after = c.blackout_flip if (self.phase == "FLIP" or self.equity < 21) else c.blackout_main
        nb = self.news.blocked(int(ts_s * 1000), before, after)
        if nb:
            return nb
        spread = ask - bid
        recent = [b["spr"] for b in self.bars.bars[-60:] if "spr" in b]
        med = sorted(recent)[len(recent) // 2] if recent else spread
        if spread > c.spread_cap or spread > c.spread_rel_cap * max(med, 0.05):
            return f"spread {spread:.2f} (median {med:.2f})"
        if self.phase == "FLIP" and self.losses_today >= c.flip_max_losses_day:
            return "flip daily loss count reached"
        if self.phase == "MAIN" and self.day_start_eq > 0 and \
                self.equity <= self.day_start_eq * (1 - c.main_daily_loss_frac):
            return "main daily loss budget reached"
        return None

    def _stop_distance(self, o: dict, entry: float) -> float:
        if o.get("sl") is not None and not (isinstance(o["sl"], float) and math.isnan(o["sl"])):
            return abs(entry - o["sl"])
        return float(o["sl_dist"])

    def _size(self, stop_dist: float, price: float, ts_s: float) -> tuple[float, str]:
        c = self.cfg
        near_news = self.news.blocked(int(ts_s * 1000), 30, 30) is not None
        lev = c.news_leverage if near_news else c.leverage
        m_lot = OZ_PER_LOT * price / lev            # margin for 1.0 lot
        if self.phase == "FLIP":
            if not (c.flip_stop_min <= stop_dist <= c.flip_stop_max):
                return 0.0, f"flip stop {stop_dist:.2f} outside [{c.flip_stop_min}, {c.flip_stop_max}]"
            lots = 0.01
        else:
            raw = self.equity * c.main_risk_frac / (stop_dist * OZ_PER_LOT)
            lots = math.floor(raw / 0.01 + 1e-9) * 0.01
            if lots < 0.01:
                if 0.01 * stop_dist * OZ_PER_LOT <= self.equity * c.main_max_risk_frac:
                    lots = 0.01
                else:
                    return 0.0, "min lot exceeds main risk cap"
        max_by_margin = math.floor(self.equity / (m_lot * c.margin_buffer) / 0.01 + 1e-9) * 0.01
        lots = min(lots, max_by_margin)
        if lots < 0.01:
            if self.phase == "FLIP" and not near_news:
                self.halted = f"equity {self.equity:.2f} cannot carry 0.01 lot (needs {0.01 * m_lot * c.margin_buffer:.2f})"
            return 0.0, f"margin: equity {self.equity:.2f} < {0.01 * m_lot * c.margin_buffer:.2f} per 0.01 lot"
        # the stop (plus 0.5 slippage) must be hit before the broker's 20% margin-level stop-out
        if self.equity - lots * OZ_PER_LOT * (stop_dist + 0.5) < 0.2 * lots * m_lot:
            return 0.0, "stop beyond stop-out"
        return round(lots, 2), ""

    async def _open(self, o: dict, bid: float, ask: float, ts_s: float, trigger_px: Optional[float] = None):
        if self._busy:
            return
        self._busy = True
        try:
            d = o["d"]              # equity is refreshed by the runner loop and after every close (no extra latency here)
            entry = ask if d > 0 else bid
            if trigger_px is not None:
                entry = max(trigger_px, entry) if (o.get("kind") == "stop") == (d > 0) else min(trigger_px, entry)
            stop_dist = self._stop_distance(o, entry)
            lots, why = self._size(stop_dist, entry, ts_s)
            if lots <= 0:
                log.info("skip %s: %s", o.get("tag", ""), why)
                self._write({"ev": "skip", "why": why, "order": _jsonable(o), "ts": ts_s})
                return
            sl = o["sl"] if o.get("sl") is not None and not _isnan(o.get("sl")) else entry - d * o["sl_dist"]
            tp = o.get("tp")
            if tp is None or _isnan(tp):
                tp = entry + d * o["tp_dist"] if not _isnan(o.get("tp_dist")) else None
            t0 = time.perf_counter()
            res = await self.g.open_market_order(direction="BUY" if d > 0 else "SELL", volume=lots,
                                                 sl_price=round(sl, 2), tp_price=None,
                                                 expected_mode=self.cfg.mode.upper())
            lat = (time.perf_counter() - t0) * 1000
            if not res or not res.get("success"):
                self._write({"ev": "order_fail", "res": res, "order": _jsonable(o), "ts": ts_s, "lat_ms": lat})
                return
            q = await _quote(self.g)
            self.pos = Position(o, d, lots, entry, sl, tp if tp is not None else (math.inf * d), ts_s)
            self._write({"ev": "open", "tag": o.get("tag"), "d": d, "lots": lots, "phase": self.phase,
                         "intended": entry, "quote_after": _q(q), "sl": sl, "tp": tp, "eq": self.equity,
                         "lat_ms": lat, "ts": ts_s, "order": _jsonable(o)})
        finally:
            self._busy = False

    async def _check_pending(self, bid: float, ask: float, ts_s: float):
        keep = []
        for o in self.pending:
            if o.get("exp", math.inf) <= ts_s * 1000:
                continue
            d, px = o["d"], o["px"]
            if o["kind"] == "stop":
                hit = (ask >= px) if d > 0 else (bid <= px)
            else:
                hit = (ask <= px) if d > 0 else (bid >= px)
            if hit and self.pos is None and not self._entry_block(o, bid, ask, ts_s):
                await self._open(o, bid, ask, ts_s, trigger_px=px)
            else:
                keep.append(o)
        self.pending = keep if self.pos is None else []

    async def _manage(self, bid: float, ask: float, ts_s: float):
        p = self.pos
        o = p.order
        x = bid * p.d if p.d > 0 else -ask            # exit-side price in long space
        e = p.entry * p.d
        # stop in force uses the peak of PREVIOUS quotes (sim: previous bars)
        stop = p.sl0 * p.d
        mfe_prev = p.peak - e
        if not _isnan(o.get("be")) and mfe_prev >= o["be"]:
            stop = max(stop, e + o.get("be_off", 0.0))
        if not _isnan(o.get("trail")) and mfe_prev >= o.get("trail_act", 0.0):
            stop = max(stop, p.peak - o["trail"])
        p.stop = max(p.stop, stop)
        reason = None
        tmax = o.get("tmax")
        now = datetime.fromtimestamp(ts_s, timezone.utc)
        fri_flat = now.weekday() == 4 and now.hour * 60 + now.minute >= self.cfg.friday_flatten_utc
        if (tmax and ts_s * 1000 - p.t_in * 1000 >= tmax) or (o.get("flat") and ts_s * 1000 >= o["flat"]) or fri_flat:
            reason = "time"
        elif x <= p.stop:
            reason = "sl" if p.stop <= p.sl0 * p.d + 1e-9 else "trail"
        elif p.tp is not None and math.isfinite(p.tp) and x >= p.tp * p.d:
            reason = "tp"
        p.peak = max(p.peak, x)
        if reason:
            await self._close(reason, bid, ask, ts_s)

    async def _close(self, reason: str, bid: float, ask: float, ts_s: float):
        p = self.pos
        eq0 = self.equity
        res = None
        for _ in range(3):
            res = await self.g.flatten_all_positions()
            if res and res.get("success"):
                break
            await asyncio.sleep(0.3)
        if not res or not res.get("success"):
            self.halted = f"flatten failed ({reason})"
            self._write({"ev": "flatten_fail", "reason": reason, "res": res, "ts": ts_s})
            return
        await self.refresh_equity()
        pnl = self.equity - eq0
        exit_px = bid if p.d > 0 else ask
        R = ((exit_px - p.entry) * p.d) / max(abs(p.entry - p.sl0), 1e-9)
        if pnl < 0:
            self.losses_today += 1
        self._write({"ev": "close", "reason": reason, "d": p.d, "lots": p.lots, "entry": p.entry,
                     "exit_quote": exit_px, "R_quote": round(R, 3), "pnl_balance": round(pnl, 2),
                     "eq": self.equity, "phase": self.phase, "held_s": ts_s - p.t_in, "ts": ts_s})
        self.pos = None
        self._update_phase()

    # ------------------------------------------------------------------------------------------------------------
    def _shadow(self, o: dict, blocked: Optional[str], bid: float, ask: float):
        """Laya/Jeff shadow verdict: logged, never used."""
        if not self.oracle:
            return
        feat = {"direction": "BUY" if o["d"] > 0 else "SELL", "entry_price": ask if o["d"] > 0 else bid,
                "sl_price": o.get("sl"), "setup_type": o.get("tag", ""), "hour_utc": datetime.now(timezone.utc).hour,
                "trend_aligned": None, "wick_ratio": None}

        def run():
            try:
                dec = self.oracle.evaluate_setup_sync(feat)
                self._write({"ev": "shadow", "tag": o.get("tag"), "t": o["t"], "blocked": blocked,
                             "valid": getattr(dec, "is_valid", None),
                             "trap": getattr(dec, "trap_probability", None),
                             "score": getattr(dec, "confluence_score", None)})
            except Exception as e:
                self._write({"ev": "shadow_err", "err": repr(e)[:200], "t": o["t"]})

        try:
            asyncio.get_running_loop().run_in_executor(None, run)
        except RuntimeError:
            run()

    def _log_quote(self, bid: float, ask: float, ts_s: float):
        """1 Hz bid/ask log (SYNTHESIS G7: the LiteFinance spread has only been measured 10 times)."""
        if ts_s - self._last_quote_log < 1.0:
            return
        self._last_quote_log = ts_s
        try:
            self.quote_log_dir.mkdir(parents=True, exist_ok=True)
            f = self.quote_log_dir / (datetime.fromtimestamp(ts_s, timezone.utc).strftime("%Y-%m-%d") + ".csv")
            with open(f, "a") as fh:
                fh.write(f"{ts_s:.3f},{bid:.3f},{ask:.3f}\n")
        except Exception:
            pass

    def _write(self, rec: dict):
        try:
            with open(self.trade_log, "a") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
        except Exception:
            pass

    def telemetry(self) -> dict:
        p = self.pos
        return {"engine": f"xau_alpha:{self.cfg.module}", "phase": self.phase, "mode": self.cfg.mode,
                "equity": round(self.equity, 2), "bars": len(self.bars), "halted": self.halted,
                "losses_today": self.losses_today, "pending": len(self.pending),
                "position": None if p is None else {"d": p.d, "lots": p.lots, "entry": p.entry, "sl": p.sl0,
                                                    "stop_now": p.stop * p.d, "tp": p.tp}}


def _isnan(v: Any) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _jsonable(o: dict) -> dict:
    return {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in o.items()}


async def _quote(g):
    try:
        return await g.get_live_quote()
    except Exception:
        return None


def _q(q):
    return None if q is None else {"bid": q.bid, "ask": q.ask, "ts": getattr(q, "timestamp", None)}
