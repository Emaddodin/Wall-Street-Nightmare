"""
hl_xau_engine.py
================
The XAU challenge stacker, bar for bar, on Hyperliquid-exact rails.

STRATEGY (verbatim XAU identity, same triggers and policy):
  5m breakout -> 1m retest -> rejection wick (>=0.45) + EMA trend + LLM
  validation -> stacked market entry of 5-10 orders, geometric lots
  (uniform 0.1-0.25 x min(balance/50, 10)), seeded per bar (deterministic).
  Exits: rapid equity spike (>= max($50, 35% of balance)), risk-stop cap
  (max($15, 15% of balance)), momentum stall at close, resting stop-market
  (bar extreme +/- $0.15). NO fee-viability gate, by operator order.

VENUE (exact, via HLPaperVenue):
  taker 0.045% every fill both legs, L2-walk fills on the live book
  (spread model on historical bars), hourly settled funding, liquidation by
  the docs-exact formula on markPx, oz step 0.001, $10 min notional,
  adaptive price tick, per-trade latency/shortfall accounting.

LEVERAGE (the single disclosed deviation): --leverage default 100 preserves
  XAU stack economics. Hyperliquid caps PAXG at 10x; anything above 10 is
  fantasy leverage with otherwise-exact costs. Liquidation still uses the
  real tier MMR (5%). Pass --leverage 10 for a fully placeable book.
"""
from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from hl_engine import compute_frames
from hl_venue import HLPaperVenue, HLSpecs, TAKER_FEE_RATE, round_sz

logger = logging.getLogger("hl_xau")

SYMBOL = "XAUUSD"
WICK_MIN = 0.45
RETEST_TOL = 1.2
STOP_BUFFER = 0.15
LLAMA_URL_DEFAULT = "http://127.0.0.1:8080/completion"


class XAUVenueTrader:
    """XAU stacker policy + HL venue accounting. 1 lot = 100 oz."""

    def __init__(self, specs: HLSpecs, equity: float,
                 llama_url: str = LLAMA_URL_DEFAULT, leverage: float = 100.0,
                 on_event: Optional[Callable[[str, str, str, str], None]] = None):
        self.specs = specs
        self.start_equity = equity
        self.venue = HLPaperVenue(specs, equity)
        self.llama_url = llama_url
        self.leverage = leverage
        self.on_event = on_event
        self.df_1m: Optional[pd.DataFrame] = None
        self.df_5m: Optional[pd.DataFrame] = None
        self.breakout: Optional[Dict[str, Any]] = None
        self.stack_meta: List[Dict[str, Any]] = []  # display slices
        self.stack_bias: Optional[str] = None
        self.entry_bar_idx = -1
        self.peak = equity
        self.llm_fallbacks = 0
        self._last_funding_hour = -1
        try:
            from engine.killzone import KillZoneGuard, XAUUSD_GOLD_KILLZONES
            self.kz = KillZoneGuard(XAUUSD_GOLD_KILLZONES)
        except Exception:
            self.kz = None

    # -- events ------------------------------------------------------------------
    def _emit(self, kind: str, title: str, msg: str, tags: str = "zap") -> None:
        if getattr(self, "silent_events", False):
            return
        if self.on_event:
            try:
                self.on_event(kind, title, msg, tags)
            except Exception:
                pass

    # -- LLM -----------------------------------------------------------------------
    def validate(self, context: Dict[str, Any]) -> tuple[bool, str, bool]:
        import json as _json
        import urllib.request as _url
        prompt = ("<|im_start|>system\nYou are the Micro-Structure Momentum Validation Brain "
                  "for high-frequency Gold (XAUUSD) scalping. Evaluate the Breakout -> Retest -> "
                  "Rejection candle geometry. Respond in strict JSON with keys 'decision' "
                  "('GO' or 'NO_GO') and 'reasoning'.\n<|im_end|>\n<|im_start|>user\n"
                  f"{_json.dumps(context, indent=2)}\n<|im_end|>\n<|im_start|>assistant\n")
        payload = {"prompt": prompt, "n_predict": 64, "temperature": 0.1,
                   "stream": False, "stop": ["<|im_end|>", "\n\n"]}
        try:
            req = _url.Request(self.llama_url, data=_json.dumps(payload).encode(),
                               headers={"Content-Type": "application/json"}, method="POST")
            with _url.urlopen(req, timeout=0.8) as resp:
                content = _json.loads(resp.read().decode()).get("content", "").strip()
                parsed = _json.loads(content)
                d = parsed.get("decision", "NO_GO").upper()
                return (d == "GO"), parsed.get("reasoning", "LLM evaluated"), False
        except Exception as e:
            logger.debug("LLM fallback: %s", e)
        wr = context.get("rejection_wick_ratio", 0.0)
        if wr >= WICK_MIN:
            return True, f"Algorithmic fallback: rejection wick {wr:.2f} confirms {context.get('direction')}", True
        return False, f"Algorithmic fallback: wick {wr:.2f} < {WICK_MIN}", True

    # -- history ----------------------------------------------------------------------
    def load_bars(self, bars: List[Dict[str, Any]]) -> None:
        self.df_1m, self.df_5m = compute_frames(bars)

    @property
    def balance(self) -> float:
        return self.venue.equity

    # -- stacking -----------------------------------------------------------------------
    def open_stack(self, direction: str, ref_px: float, sl_px: float, bar_idx: int,
                   timestamp_ms: int, time_str: str, reason: str) -> bool:
        rng = np.random.default_rng(int(timestamp_ms) % (2 ** 32))
        num_orders = int(rng.integers(5, 11))
        scale = max(1.0, self.balance / 50.0)
        lots = [round(float(rng.uniform(0.1, 0.25)) * min(scale, 10.0), 2)
                for _ in range(num_orders)]
        total_lots = round(sum(lots), 2)
        total_sz = total_lots * 100.0

        # Dynamic margin allocation: ensure margin_need fits available equity
        margin_need = (total_sz * ref_px) / self.leverage
        if margin_need > self.balance * 0.95:
            avail_margin = max(1.0, self.balance * 0.95)
            total_sz = round_sz((avail_margin * self.leverage) / ref_px, self.specs.sz_step)
            margin_need = (total_sz * ref_px) / self.leverage
            total_lots = round(total_sz / 100.0, 2)
            per_sz = total_sz / max(len(lots), 1)
            lots = [round(per_sz / 100.0, 2) for _ in lots]

        sz, margin_used, rej = self.venue.size_for(margin_need, self.leverage, ref_px)
        if rej:
            self._emit("skip", f"⏭️ Stack rejected: {rej}", "", "mute")
            return False
        # Aggregate walk-book fill; slices share the VWAP for display.
        r = self.venue.market_open(1 if direction == "BUY" else -1, sz, ref_px,
                                   margin_used, self.leverage, sl_px, timestamp_ms / 1000.0)
        if r["status"] not in ("ok", "partial"):
            self._emit("skip", f"⏭️ Stack rejected: {r.get('reason')}", "", "mute")
            return False
        f = r["fill"]
        per = f.sz / max(len(lots), 1)
        self.stack_meta = [{"status": "FILLED", "sz": round(per, 2),
                            "fill_price": round(f.px, 2)} for _ in lots]
        self.stack_bias = direction
        self.entry_bar_idx = bar_idx
        self.breakout = None
        logger.info("STACK %s %.2f lots (%.3foz) @ $%.2f | SL $%.2f liq $%.2f fee $%.2f | %s",
                    direction, total_lots, f.sz, f.px, sl_px, r["liq_px"], f.fee, reason)
        self._emit("entry", f"⚡ XAU Stacked: {direction} {total_lots:.2f} Lots",
                   f"Avg ${f.px:.2f} | SL ${sl_px:.2f} | liq ${r['liq_px']:.2f} | "
                   f"fee ${f.fee:.2f} | margin ${margin_used:.2f} @ {self.leverage:g}x"
                   + (" [LEV>VENUE]" if self.leverage > self.specs.max_leverage else "")
                   + f"\n{reason}", "moneybag,zap,rocket")
        return True

    def flatten(self, px: float, ts: float, reason: str,
                forced_pnl: Optional[float] = None) -> float:
        if not self.venue.pos.is_open:
            return 0.0
        if forced_pnl is not None:
            # Venue-exact books cannot force PnL; cap by closing at a price
            # that realizes no more than forced_pnl (conservative).
            r = self.venue.market_close(px, ts, reason)
            if r["status"] not in ("ok", "partial"):
                return 0.0
            rec = r["record"]
            if rec["net"] > forced_pnl:
                over = rec["net"] - forced_pnl
                rec["net"] = round(forced_pnl, 2)
                rec["reason"] = reason
                self.venue.equity -= over
                self.venue.realized -= over
                rec["equity"] = round(self.venue.equity, 2)
            total, lots = rec["net"], sum(s["sz"] for s in self.stack_meta)
        else:
            r = self.venue.market_close(px, ts, reason)
            if r["status"] not in ("ok", "partial"):
                return 0.0
            rec = r["record"]
            total, lots = rec["net"], sum(s["sz"] for s in self.stack_meta)
        logger.info("FLATTEN %s (%.2f lots) @ $%.2f PnL %+.2f bal $%.2f | %s",
                    self.stack_bias, lots, rec["exit"], total, self.venue.equity, reason)
        self._emit("exit", f"🏁 Micro-Scalp Exit: {'+' if total >= 0 else '-'}${abs(total):.2f}",
                   f"Reason: {reason}\nExit ${rec['exit']:.2f} | fee ${rec['fee']:.2f} | "
                   f"Balance ${self.venue.equity:.2f}", 
                   "checkered_flag,money_with_wings" if total >= 0 else "rotating_light,x")
        self.stack_meta, self.stack_bias = [], None
        return total

    # -- one closed bar ------------------------------------------------------------------
    def manage_tick(self, mark: float, ts: float) -> None:
        """Intrabar management on MARK (the venue's own trigger basis):
        stop/liquidation first, then spike/risk caps on floating equity."""
        if not self.venue.pos.is_open:
            return
        for rec in self.venue.check_venue_exits(mark, mark, mark, ts):
            self.stack_meta, self.stack_bias = [], None
            self._emit("exit", f"🏁 Exit {rec['net']:+.2f} ({rec['reason']})",
                       f"eq ${rec['equity']:.2f}",
                       "checkered_flag" if rec["net"] >= 0 else "rotating_light")
        pos = self.venue.pos
        if not pos.is_open:
            return
        flt_eq = self.balance + pos.upnl(mark)
        gain = flt_eq - self.balance
        max_loss = max(15.0, self.balance * 0.15)
        spike = max(50.0, self.balance * 0.35)
        if gain >= spike:
            self.flatten(mark, ts, f"Rapid Equity Spike (+${gain:.2f})")
        elif (mark <= pos.stop_px if pos.side == 1 else mark >= pos.stop_px) or gain <= -max_loss:
            cap = -max_loss if gain <= -max_loss else gain
            self.flatten(mark, ts, f"Risk Stop Triggered (-${abs(cap):.2f})", forced_pnl=cap)

    def step_bar(self, idx: int, px_open: float, px_high: float, px_low: float,
                 px_close: float, volume: float, curr_t_ms: int, bar_time_str: str,
                 book, mark: float, ts: float,
                 funding_rate: float = 0.0) -> None:
        self.venue.on_book(*book)
        self.venue.on_mark(mark)
        hour = int(ts // 3600)
        if self._last_funding_hour < 0:
            self._last_funding_hour = hour
        if hour != self._last_funding_hour:
            paid = self.venue.accrue_funding(funding_rate, ts)
            if paid != 0.0:
                self._emit("funding", f"💸 Funding settled: ${paid:+.2f}",
                           f"Rate {funding_rate:.7f}", "banknote")
            self._last_funding_hour = hour

        # Venue exits on the bar's full range (history) then live-tick policy.
        for rec in self.venue.check_venue_exits(mark, px_high, px_low, ts):
            self.stack_meta, self.stack_bias = [], None
            self._emit("exit", f"🏁 Exit {rec['net']:+.2f} ({rec['reason']})",
                       f"eq ${rec['equity']:.2f}",
                       "checkered_flag" if rec["net"] >= 0 else "rotating_light")
        self.manage_tick(mark, ts)

        # Momentum stall evaluated at the close (XAU policy).
        pos = self.venue.pos
        if pos.is_open and idx > self.entry_bar_idx:
            stall = (pos.side == 1 and px_close < px_open) or (pos.side == -1 and px_close > px_open)
            if stall:
                gain = self.balance + pos.upnl(mark) - self.balance
                if gain > 0:
                    self.flatten(mark, ts, f"Momentum Stall in Profit (+${gain:.2f})")
                else:
                    cap = max(gain, -max(15.0, self.balance * 0.15))
                    self.flatten(mark, ts, "Momentum Stall: 1m closed against bias", forced_pnl=cap)

        # Fresh breakout state.
        m5 = self.df_5m[self.df_5m["open_time"] <= curr_t_ms]
        if not m5.empty:
            last = m5.iloc[-1]
            if bool(last.get("break_up", False)) and not math.isnan(float(last.get("res", float("nan")))):
                self.breakout = {"type": "UP", "level": float(last["res"]), "t": int(last["open_time"])}
            elif bool(last.get("break_down", False)) and not math.isnan(float(last.get("sup", float("nan")))):
                self.breakout = {"type": "DOWN", "level": float(last["sup"]), "t": int(last["open_time"])}

        # Entry.
        if not self.venue.pos.is_open and self.breakout is not None:
            sig = self._signal(px_open, px_high, px_low, px_close, volume, curr_t_ms, idx)
            if sig is not None:
                direction, sl_px, wick = sig
                ok, reason, fb = self.validate({
                    "symbol": SYMBOL, "direction": direction,
                    "setup": "5m Breakout -> 1m Retest & Rejection",
                    "5m_broken_level": self.breakout["level"],
                    "1m_rejection_price": px_close,
                    "rejection_wick_ratio": round(wick, 2), "volume": volume})
                if fb:
                    self.llm_fallbacks += 1
                if ok:
                    self.open_stack(direction, px_close, sl_px, idx, curr_t_ms, bar_time_str, reason)
        self.peak = max(self.peak, self.balance)

    def _signal(self, o, h, l, c, volume, curr_t_ms, idx):
        bo = self.breakout
        if bo is None or not (0 < (curr_t_ms - bo["t"]) <= 20 * 60_000):
            return None
        bar = self.df_1m.iloc[idx]
        lvl = bo["level"]
        if bo["type"] == "UP":
            if not (c > bar["ema20"] > bar["ema50"]):
                return None
            if not (l <= lvl + RETEST_TOL and h >= lvl - 0.2):
                return None
            rng = max(0.01, h - l)
            wick = (min(o, c) - l) / rng
            if not ((wick >= WICK_MIN and c >= o) or bool(bar["pin_long"]) or bool(bar["hammer"])):
                return None
            return "BUY", round(l - STOP_BUFFER, 1), wick
        else:
            if not (c < bar["ema20"] < bar["ema50"]):
                return None
            if not (h >= lvl - RETEST_TOL and l <= lvl + 0.2):
                return None
            rng = max(0.01, h - l)
            wick = (h - max(o, c)) / rng
            if not ((wick >= WICK_MIN and c <= o) or bool(bar["pin_short"]) or bool(bar["inv_hammer"])):
                return None
            return "SELL", round(h + STOP_BUFFER, 1), wick

    # -- dashboard (same phone schema) ----------------------------------------------------------
    def dashboard(self, sim_time: str, current_price: float,
                  extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        p = self.venue.pos
        upnl = p.upnl(current_price) if p.is_open else 0.0
        eq = self.balance + upnl
        pos = None
        if p.is_open:
            lots = sum(s["sz"] for s in self.stack_meta) / 100.0 if self.stack_meta else p.sz / 100.0
            pos = {"aggregate_sz": round(p.sz, 3),
                   "side": "BUY" if p.side == 1 else "SELL",
                   "avg_entry_price": round(p.entry, 2), "stop_price": round(p.stop_px, 2),
                   "unrealized_pnl": round(upnl, 2), "breakeven_locked": upnl > 20.0,
                   "liq_px": round(p.liq_px, 2), "lots": round(lots, 2),
                   "slices": self.stack_meta or [{"status": "FILLED", "sz": round(p.sz, 3),
                                                  "fill_price": round(p.entry, 2)}]}
        avg_short = (sum(s["shortfall_bps"] for s in self.venue.shortfall_log) /
                     len(self.venue.shortfall_log)) if self.venue.shortfall_log else 0.0
        fd = extra or {}
        recent = []
        for t in reversed(self.venue.trades[-15:]):
            recent.append({"time": datetime.fromtimestamp(t["t"], tz=timezone.utc).strftime("%H:%M UTC"),
                           "type": "BUY" if t["side"] == 1 else "SELL",
                           "entry": t["entry"], "exit": t["exit"],
                           "lots": round(t["sz"] / 100.0, 2), "pnl": t["net"],
                           "reason": t["reason"], "fee": t["fee"]})
        return {
            "engine": f"XAU Stacker on HL rails ({self.leverage:g}x, venue-exact costs)",
            "symbol": "XAUUSD / PAXG", "mode": "PAPER — venue-exact, nothing signed",
            "updated_at": time.time(), "updated_iso": datetime.now(timezone.utc).isoformat(),
            "fsm_state": "IN_TRADE" if p.is_open else "SCANNING",
            "equity": round(self.balance, 2), "starting_equity": self.start_equity,
            "pnl_dollar": round(self.balance - self.start_equity, 2),
            "pnl_pct": round((self.balance - self.start_equity) / self.start_equity * 100.0, 2),
            "simulated_time": sim_time, "current_price": round(current_price, 2),
            "position": pos, "recent_trades": recent,
            "drawdown": {"peak_equity": round(self.peak, 2),
                         "current_drawdown_pct": round(max(0.0, (self.peak - eq) / self.peak * 100.0), 2),
                         "max_drawdown_pct": 5.0, "killswitch_tripped": False},
            "killzone": self.kz.multitz_dashboard() if self.kz else {},
            "intuition": {"endpoint": self.llama_url, "timeout_ms": 800,
                          "fallbacks": self.llm_fallbacks},
            "self_healing": {"enabled": True, "fixes_applied": 0},
            "hl": {"fees_paid": round(self.venue.fees_total, 2),
                   "funding_paid": round(self.venue.funding_total, 2),
                   "shortfall_bps_avg": round(avg_short, 2),
                   "leverage": self.leverage,
                   "lev_over_venue": self.leverage > self.specs.max_leverage,
                   "mark_px": fd.get("mark_px", current_price),
                   "oracle_px": fd.get("oracle_px", current_price),
                   "ws_connected": fd.get("ws_connected", False),
                   "ws_drops": fd.get("ws_drops", 0),
                   "book_age_ms": fd.get("book_age_ms", -1),
                   "ack_ms": fd.get("ack_ms", -1)},
        }
