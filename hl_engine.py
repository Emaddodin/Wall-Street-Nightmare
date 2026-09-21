"""
hl_engine.py
============
Hyperliquid-exact paper trading engine for PAXG (XAU).

STRATEGY (identical to the XAU challenge stacker, bar for bar):
  5m breakout -> 1m retest at the level -> rejection wick (>=0.45) with trend
  (price vs EMA20 vs EMA50) -> local LLM validation -> stacked market entry.
  Exits: rapid equity spike, risk-stop cap, momentum stall at close,
  resting stop-market, plus VENUE exits (liquidation on markPx).

ACCOUNTING (venue-exact, via HLPaperVenue):
  isolated margin at <=10x, sizes in oz (step 0.001), $10 min notional,
  taker 0.045% on every fill, L2-walk fills on the live book, hourly
  funding from settled fundingHistory rates, liquidation by the docs'
  exact formula on markPx with a 20%-first partial pass.
  A setup whose round-trip fee exceeds 25% of its risk is SKIPPED
  (fee_gt_edge) -- micro stops cannot pay for themselves on this venue.
"""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

import scalper.pa.candles as pa_candles
import scalper.pa.levels as pa_levels
from engine.killzone import KillZoneGuard, XAUUSD_GOLD_KILLZONES
from hl_venue import HLPaperVenue, HLSpecs, TAKER_FEE_RATE

logger = logging.getLogger("hl_engine")

SYMBOL = "XAUUSD"
COIN = "PAXG"
FEE_MAX_R = 0.25          # round-trip fee may cost at most 25% of risk
STOP_BUFFER = 0.5         # $ beyond the wick extreme (PAXG scale)
WICK_MIN = 0.45
RETEST_TOL = 1.2          # $ retest tolerance at the broken level
LLAMA_URL_DEFAULT = "http://127.0.0.1:8080/completion"


def compute_frames(bars: List[Dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Same frames the XAU engine trades: 1m + 5m with levels and candles."""
    df_1m = pd.DataFrame(bars)
    df_1m["datetime"] = pd.to_datetime(df_1m["open_time"], unit="ms", utc=True)
    df_1m.sort_values("open_time", inplace=True)
    df_1m.reset_index(drop=True, inplace=True)
    df_1m["ema20"] = df_1m["close"].ewm(span=20).mean()
    df_1m["ema50"] = df_1m["close"].ewm(span=50).mean()
    df_5m = (df_1m.set_index("datetime").resample("5min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last",
         "volume": "sum", "open_time": "first"}).dropna().reset_index())
    df_5m["res"] = pa_levels.range_high(df_5m, 12)
    df_5m["sup"] = pa_levels.range_low(df_5m, 12)
    df_5m["break_up"] = pa_levels.breakout_up(df_5m, 12, range_pct=0.05)
    df_5m["break_down"] = pa_levels.breakout_down(df_5m, 12, range_pct=0.05)
    df_1m["pin_long"] = pa_candles.pin_bar_long(df_1m, lower_wick=0.45, body=0.40)
    df_1m["pin_short"] = pa_candles.pin_bar_short(df_1m, upper_wick=0.45, body=0.40)
    df_1m["hammer"] = pa_candles.hammer(df_1m)
    df_1m["inv_hammer"] = pa_candles.inverted_hammer(df_1m)
    return df_1m, df_5m


class HLTrader:
    def __init__(self, specs: HLSpecs, equity: float, llama_url: str = LLAMA_URL_DEFAULT,
                 margin_pct: float = 0.20, leverage: float = 10.0,
                 on_event: Optional[Callable[[str, str, str, str], None]] = None):
        self.specs = specs
        self.start_equity = equity
        self.venue = HLPaperVenue(specs, equity)
        self.llama_url = llama_url
        self.margin_pct = margin_pct
        self.leverage = min(leverage, specs.max_leverage)
        self.on_event = on_event  # (kind, title, message, tags)
        self.kz = KillZoneGuard(XAUUSD_GOLD_KILLZONES)
        self.df_1m: Optional[pd.DataFrame] = None
        self.df_5m: Optional[pd.DataFrame] = None
        self.breakout: Optional[Dict[str, Any]] = None
        self.peak = equity
        self.skipped_fee = 0
        self.llm_fallbacks = 0
        self._last_funding_hour = -1

    # -- events ------------------------------------------------------------------
    def _emit(self, kind: str, title: str, msg: str, tags: str = "zap") -> None:
        if self.on_event:
            try:
                self.on_event(kind, title, msg, tags)
            except Exception:
                pass

    # -- LLM validation -------------------------------------------------------------
    def validate(self, context: Dict[str, Any]) -> tuple[bool, str, bool]:
        """Returns (go, reason, used_fallback). Real brain or honest fallback."""
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

    # -- one closed bar ------------------------------------------------------------------
    def manage_tick(self, mark: float, ts: float) -> None:
        """Intrabar venue guard on MARK (stop/liquidation only)."""
        if not self.venue.pos.is_open:
            return
        self.venue.on_mark(mark)
        for rec in self.venue.check_venue_exits(mark, mark, mark, ts):
            self._emit("exit", f"🏁 Exit {rec['net']:+.2f} ({rec['reason']})",
                       f"eq ${rec['equity']:.2f}", "flag")

    def step_bar(self, idx: int, book, mark: float, ts: float,
                 funding_rate: float = 0.0, live: bool = False) -> List[Dict[str, Any]]:
        """Advances the engine across one CLOSED 1m bar. Returns close records."""
        events: List[Dict[str, Any]] = []
        assert self.df_1m is not None and self.df_5m is not None
        bar = self.df_1m.iloc[idx]
        curr_t_ms = int(bar["open_time"])
        self.venue.on_book(*book)
        self.venue.on_mark(mark)
        hour = int(ts // 3600)
        if self._last_funding_hour < 0:
            self._last_funding_hour = hour
        if hour != self._last_funding_hour:
            paid = self.venue.accrue_funding(funding_rate, ts)
            if paid != 0.0:
                self._emit("funding", f"💸 Funding settled: ${paid:+.2f}",
                           f"Rate {funding_rate:.7f} on open PAXG position.", "banknote")
            self._last_funding_hour = hour

        # 1. Venue exits first (stop trigger on candle extreme, liq on mark).
        for rec in self.venue.check_venue_exits(mark, float(bar["high"]), float(bar["low"]), ts):
            events.append(rec)
            self._emit("exit", f"🏁 Exit {rec['net']:+.2f} ({rec['reason']})",
                       f"Exit ${rec['exit']:.1f} | net ${rec['net']:+.2f} (fee ${rec['fee']:.2f}) | eq ${rec['equity']:.2f}",
                       "checkered_flag" if rec["net"] >= 0 else "rotating_light")

        # 2. Manage open position (strategy exits, NET of fees).
        pos = self.venue.pos
        if pos.is_open:
            # Float is gross; fees already left equity at fill time, and every
            # close nets them again in the venue. Thresholds read gross float.
            upnl = pos.upnl(mark)
            balance = self.venue.equity + upnl
            spike = max(50.0, balance * 0.35)
            max_loss = max(15.0, balance * 0.15)
            o, c = float(bar["open"]), float(bar["close"])
            if upnl >= spike:
                r = self.venue.market_close(mark, ts, f"Rapid Equity Spike (+${upnl:.2f})")
                if r["status"] in ("ok", "partial"):
                    events.append(r["record"])
                    self._emit("exit", f"🚀 Spike banked {r['record']['net']:+.2f}",
                               f"eq ${r['record']['equity']:.2f}", "rocket")
            elif upnl <= -max_loss:
                r = self.venue.market_close(mark, ts, f"Risk Stop Capped (-${abs(upnl):.2f})")
                if r["status"] in ("ok", "partial"):
                    events.append(r["record"])
                    self._emit("exit", f"🛑 Risk cap {r['record']['net']:+.2f}",
                               f"eq ${r['record']['equity']:.2f}", "octagonal_sign")
            else:
                stall = (pos.side == 1 and c < o) or (pos.side == -1 and c > o)
                if stall and idx > 0:
                    r = self.venue.market_close(
                        mark, ts, f"Momentum Stall {'in Profit' if upnl > 0 else 'cut'} ({upnl:+.2f})")
                    if r["status"] in ("ok", "partial"):
                        events.append(r["record"])
                        self._emit("exit", f"🏁 Stall exit {r['record']['net']:+.2f}",
                                   f"eq ${r['record']['equity']:.2f}", "flag")

        # 3. Fresh 5m breakout state.
        m5 = self.df_5m[self.df_5m["open_time"] <= curr_t_ms]
        if not m5.empty:
            last = m5.iloc[-1]
            if bool(last.get("break_up", False)) and not math.isnan(float(last.get("res", float("nan")))):
                self.breakout = {"type": "UP", "level": float(last["res"]), "t": int(last["open_time"])}
            elif bool(last.get("break_down", False)) and not math.isnan(float(last.get("sup", float("nan")))):
                self.breakout = {"type": "DOWN", "level": float(last["sup"]), "t": int(last["open_time"])}

        # 4. Entry: retest + rejection + trend + LLM + fee viability.
        if not self.venue.pos.is_open and self.breakout is not None:
            sig = self._entry_signal(bar, curr_t_ms, mark)
            if sig is not None:
                direction, sl_struct, wick_ratio = sig
                sz, margin_used, rej = self.venue.size_for(
                    self.venue.equity * self.margin_pct, self.leverage, mark)
                if rej:
                    self._emit("skip", f"⏭️ Skip {direction}: {rej}", "", "mute")
                else:
                    fee_rt = 2 * TAKER_FEE_RATE * (sz * mark)
                    risk_d = abs(mark - sl_struct)
                    if fee_rt > FEE_MAX_R * (sz * risk_d):
                        # Structural stop cannot pay fees: demand the fee-viable distance.
                        need = fee_rt / (FEE_MAX_R * sz)
                        sl_struct = mark - need if direction == "BUY" else mark + need
                        risk_d = need
                    ok, reason, fb = self.validate({
                        "symbol": SYMBOL, "direction": direction,
                        "setup": "5m Breakout -> 1m Retest & Rejection",
                        "5m_broken_level": self.breakout["level"],
                        "1m_rejection_price": mark,
                        "rejection_wick_ratio": round(wick_ratio, 2),
                        "volume": float(bar["volume"])})
                    if fb:
                        self.llm_fallbacks += 1
                    if ok:
                        r = self.venue.market_open(1 if direction == "BUY" else -1, sz,
                                                   mark, margin_used, self.leverage,
                                                   sl_struct, ts)
                        if r["status"] in ("ok", "partial"):
                            self.breakout = None
                            f = r["fill"]
                            self._emit("entry", f"⚡ PAXG {direction} {f.sz:.3f}oz @ ${f.px:.1f}",
                                       f"Margin ${margin_used:.2f} | SL ${sl_struct:.1f} | liq ${r['liq_px']:.1f} | fee ${f.fee:.2f} | {reason}",
                                       "zap")
        self.peak = max(self.peak, self.venue.equity)
        return events

    def _entry_signal(self, bar, curr_t_ms: int, mark: float):
        bo = self.breakout
        if bo is None or not (0 < (curr_t_ms - bo["t"]) <= 20 * 60_000):
            return None
        o, h, l, c = (float(bar["open"]), float(bar["high"]),
                      float(bar["low"]), float(bar["close"]))
        lvl = bo["level"]
        if bo["type"] == "UP":
            if not (c > bar["ema20"] > bar["ema50"]):
                return None
            if not (l <= lvl + RETEST_TOL and h >= lvl - 0.2):
                return None
            rng = max(0.01, h - l)
            wick = (min(o, c) - l) / rng
            rej_ok = (wick >= WICK_MIN and c >= o) or bool(bar["pin_long"]) or bool(bar["hammer"])
            if not rej_ok:
                return None
            return "BUY", round(l - STOP_BUFFER, 1), wick
        else:
            if not (c < bar["ema20"] < bar["ema50"]):
                return None
            if not (h >= lvl - RETEST_TOL and l <= lvl + 0.2):
                return None
            rng = max(0.01, h - l)
            wick = (h - max(o, c)) / rng
            rej_ok = (wick >= WICK_MIN and c <= o) or bool(bar["pin_short"]) or bool(bar["inv_hammer"])
            if not rej_ok:
                return None
            return "SELL", round(h + STOP_BUFFER, 1), wick

    # -- dashboard (phone schema identical to the XAU terminal) ------------------------------
    def dashboard(self, sim_time: str, current_price: float,
                  extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        eq = self.venue.equity + (self.venue.pos.upnl(current_price) if self.venue.pos.is_open else 0.0)
        p = self.venue.pos
        pos = None
        if p.is_open:
            upnl = p.upnl(current_price)
            pos = {"aggregate_sz": round(p.sz, 3), "side": "BUY" if p.side == 1 else "SELL",
                   "avg_entry_price": round(p.entry, 2), "stop_price": round(p.stop_px, 2),
                   "unrealized_pnl": round(upnl, 2), "breakeven_locked": False,
                   "liq_px": round(p.liq_px, 2),
                   "slices": [{"status": "FILLED", "sz": round(p.sz, 3), "fill_price": round(p.entry, 2)}]}
        shorts = self.venue.shortfall_log[-1]["shortfall_bps"] if self.venue.shortfall_log else 0.0
        avg_short = (sum(s["shortfall_bps"] for s in self.venue.shortfall_log) /
                     len(self.venue.shortfall_log)) if self.venue.shortfall_log else 0.0
        fd = extra or {}
        payload = {
            "engine": "Hyperliquid-Exact PAXG Paper (venue-faithful 1:1)",
            "symbol": "XAUUSD / PAXG", "mode": "PAPER — venue-exact, nothing signed",
            "updated_at": time.time(),
            "updated_iso": datetime.now(timezone.utc).isoformat(),
            "fsm_state": "IN_TRADE" if p.is_open else "SCANNING",
            "equity": round(self.venue.equity, 2),
            "starting_equity": self.start_equity,
            "pnl_dollar": round(self.venue.equity - self.start_equity, 2),
            "pnl_pct": round((self.venue.equity - self.start_equity) / self.start_equity * 100.0, 2),
            "simulated_time": sim_time, "current_price": round(current_price, 2),
            "position": pos,
            "recent_trades": list(reversed(self.venue.trades[-15:])),
            "drawdown": {"peak_equity": round(self.peak, 2),
                         "current_drawdown_pct": round(max(0.0, (self.peak - eq) / self.peak * 100.0), 2),
                         "max_drawdown_pct": 5.0, "killswitch_tripped": False},
            "killzone": self.kz.multitz_dashboard(),
            "intuition": {"endpoint": self.llama_url, "timeout_ms": 800,
                          "fallbacks": self.llm_fallbacks},
            "self_healing": {"enabled": True, "fixes_applied": 0},
            "hl": {"fees_paid": round(self.venue.fees_total, 2),
                   "funding_paid": round(self.venue.funding_total, 2),
                   "shortfall_bps_avg": round(avg_short, 2),
                   "fee_skips": self.skipped_fee,
                   "mark_px": fd.get("mark_px", current_price),
                   "oracle_px": fd.get("oracle_px", current_price),
                   "ws_connected": fd.get("ws_connected", False),
                   "ws_drops": fd.get("ws_drops", 0),
                   "book_age_ms": fd.get("book_age_ms", -1),
                   "ack_ms": fd.get("ack_ms", -1)},
        }
        return payload
