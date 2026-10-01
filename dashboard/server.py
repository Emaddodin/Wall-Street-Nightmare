"""One-page gold scalping dashboard served on http://127.0.0.1:8765

Reads candles, ticks, balance and contract specs straight from the MetaTrader 5
terminal running on this PC (MetaTrader5 Python package), so every price on the
page is your broker's own quote. Orders go out only when you click Buy, Sell or Close.

    python server.py            # live, from your open MT5 terminal
    python server.py --demo     # synthetic gold data, no MT5 needed
    python server.py --litefinance-login   # Mac: save your LiteFinance login once
    python server.py --litefinance         # Mac: LiteFinance chart + orders
"""
from __future__ import annotations

import argparse
import json
import math
import random
import secrets
import threading
import time
import traceback
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from boom import BoomTracker, setup as boom_setup
from engine import (LADDER, Bars, Engine, Params, Spec, SESSION_NAMES, market_hours, ny7_offset, run_backtest,
                    session_of, session_ok, utc_minutes)
from smc import analyze as smc_analyze
from soon import SoonAlerts, find_topic, ntfy_server

STATIC = Path(__file__).resolve().parent / "static"
MAGIC = 26100102       # tags orders placed from this page
TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400}
HISTORY_BARS = 3000    # entry-timeframe bars replayed on start-up for the on-page stats and markers


# =================================================================== data sources
class MT5Source:
    """Your own MetaTrader 5 terminal. It must be open and logged in on this PC."""

    kind = "mt5"

    def __init__(self, symbol: str | None = None, terminal_path: str | None = None):
        import MetaTrader5 as mt5  # Windows only
        self.mt5 = mt5
        ok = mt5.initialize(terminal_path) if terminal_path else mt5.initialize()
        if not ok:
            raise RuntimeError(f"Could not connect to MetaTrader 5 {mt5.last_error()}. "
                               "Open MT5, log in to your account, then start the dashboard again.")
        self.symbol = self._resolve(symbol)
        if not mt5.symbol_select(self.symbol, True):
            raise RuntimeError(f"MT5 would not show {self.symbol} in Market Watch.")
        self.tfs = {"M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1,
                    "H4": mt5.TIMEFRAME_H4}
        self.io = threading.Lock()   # one MT5 call at a time; each call takes about a millisecond
        self._spec = None

    def _resolve(self, want: str | None) -> str:
        names = [s.name for s in (self.mt5.symbols_get() or [])]
        if want:
            if want in names:
                return want
            raise RuntimeError(f"Symbol {want} not found at your broker.")
        for cand in ("XAUUSD", "XAUUSDm", "XAUUSD.a", "XAUUSD_i", "XAUUSD.pro", "XAUUSD+", "GOLD", "XAUUSD.r"):
            if cand in names:
                return cand
        for n in names:
            if n.upper().startswith("XAUUSD") or n.upper().startswith("GOLD"):
                return n
        raise RuntimeError("No gold symbol (XAUUSD / GOLD) found at your broker. Start with --symbol NAME.")

    def rates(self, tf: str, count: int) -> Bars:
        with self.io:
            r = self.mt5.copy_rates_from_pos(self.symbol, self.tfs[tf], 0, int(count))
        b = Bars(TF_SECONDS[tf])
        if r is None:
            return b
        pt = (self._spec or self.spec()).point
        for x in r:
            b.append(int(x["time"]), x["open"], x["high"], x["low"], x["close"], float(x["tick_volume"]),
                     float(x["spread"]) * pt)
        return b

    def tick(self) -> dict | None:
        with self.io:
            t = self.mt5.symbol_info_tick(self.symbol)
        if t is None:
            return None
        return {"bid": t.bid, "ask": t.ask, "time": int(t.time)}

    def spec(self) -> Spec:
        with self.io:
            si = self.mt5.symbol_info(self.symbol)
        tv = si.trade_tick_value_loss or si.trade_tick_value
        vpu = tv / si.trade_tick_size if tv and si.trade_tick_size else (si.trade_contract_size or 100.0)
        self._spec = Spec(point=si.point, digits=si.digits, vpu=vpu, min_lot=si.volume_min or 0.01,
                          lot_step=si.volume_step or 0.01, max_lot=si.volume_max or 100.0)
        self._filling = si.filling_mode
        return self._spec

    def account(self) -> dict:
        with self.io:
            a = self.mt5.account_info()
            ti = self.mt5.terminal_info()
        if a is None:
            return {"balance": 0.0, "equity": 0.0, "currency": "USD", "server": "not logged in", "mode": "unknown",
                    "trade_allowed": False}
        mode = {0: "demo", 1: "contest", 2: "real"}.get(a.trade_mode, "unknown")
        return {"balance": a.balance, "equity": a.equity, "currency": a.currency, "server": a.server,
                "company": a.company, "mode": mode, "login": a.login,
                "trade_allowed": bool(a.trade_allowed and a.trade_expert and ti is not None and ti.trade_allowed),
                "ping_ms": round(ti.ping_last / 1000, 1) if ti is not None and ti.ping_last else None}

    # ------------------------------------------------------------ manual trading (only on your clicks)
    def _fill_type(self):
        f = getattr(self, "_filling", 0) or 0
        if f & 1:
            return self.mt5.ORDER_FILLING_FOK
        if f & 2:
            return self.mt5.ORDER_FILLING_IOC
        return self.mt5.ORDER_FILLING_RETURN

    def positions(self) -> list:
        with self.io:
            ps = self.mt5.positions_get(symbol=self.symbol) or []
        return [{"ticket": p.ticket, "side": "BUY" if p.type == 0 else "SELL", "volume": p.volume,
                 "open": p.price_open, "sl": p.sl, "tp": p.tp, "price": p.price_current, "profit": p.profit,
                 "time": p.time, "magic": p.magic, "comment": p.comment} for p in ps]

    def _send(self, req: dict) -> dict:
        mt5 = self.mt5
        t0 = time.perf_counter()
        with self.io:
            res = mt5.order_send(req)
            err = mt5.last_error() if res is None else None
        ms = round((time.perf_counter() - t0) * 1000, 1)
        if res is None:
            return {"ok": False, "message": f"MT5 refused the request {err}", "ms": ms}
        ok = res.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED, mt5.TRADE_RETCODE_DONE_PARTIAL)
        return {"ok": ok, "retcode": res.retcode, "message": res.comment, "price": res.price,
                "volume": res.volume, "order": res.order, "deal": res.deal, "ms": ms}

    def market(self, side: str, lots: float, sl: float, tp: float) -> dict:
        mt5 = self.mt5
        tk = self.tick()
        req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": self.symbol, "volume": float(lots),
               "type": mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL,
               "price": tk["ask"] if side == "BUY" else tk["bid"], "sl": float(sl or 0.0), "tp": float(tp or 0.0),
               "deviation": 30, "magic": MAGIC, "comment": "gold desk manual",
               "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._fill_type()}
        return self._send(req)

    def close(self, ticket: int, volume: float | None = None) -> dict:
        mt5 = self.mt5
        pos = next((p for p in self.positions() if p["ticket"] == ticket), None)
        if pos is None:
            return {"ok": False, "message": "Position not found (already closed?)"}
        tk = self.tick()
        vol = float(volume) if volume else pos["volume"]
        req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": self.symbol, "volume": vol, "position": ticket,
               "type": mt5.ORDER_TYPE_SELL if pos["side"] == "BUY" else mt5.ORDER_TYPE_BUY,
               "price": tk["bid"] if pos["side"] == "BUY" else tk["ask"], "deviation": 30, "magic": MAGIC,
               "comment": "gold desk close", "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._fill_type()}
        return self._send(req)

    def modify(self, ticket: int, sl: float, tp: float) -> dict:
        mt5 = self.mt5
        req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": self.symbol, "position": ticket,
               "sl": float(sl or 0.0), "tp": float(tp or 0.0), "magic": MAGIC}
        return self._send(req)


class DemoSource:
    """Synthetic gold so the page can be tried without MT5. One demo minute = `speed` seconds."""

    kind = "demo"

    def __init__(self, speed: float = 3.0, days: int = 20, seed: int = 7):
        self.symbol = "XAUUSD (demo)"
        self.speed = speed
        rnd = random.Random(seed)
        self.rnd = rnd
        now = int(time.time()) + ny7_offset(int(time.time()))
        self.t0 = now - now % 60 - days * 1440 * 60
        self.m1 = []
        px, drift = 2650.0, 0.0
        t = self.t0
        for _ in range(days * 1440):
            self.m1.append(self._bar(t, px, drift))
            px = self.m1[-1][4]
            if rnd.random() < 0.004:
                drift = rnd.uniform(-0.12, 0.12)
            t += 60
        self.live_start = time.time()
        self.base_t = t
        self.form = None
        self.balance = 1000.0
        self._pos: list = []
        self._next_ticket = 1000

    def _vol_at(self, t: int) -> float:
        m = (t - ny7_offset(t)) % 86400 // 60
        return 1.6 if (420 <= m < 600 or 750 <= m < 960) else 0.7

    def _bar(self, t: int, px: float, drift: float):
        rnd, v = self.rnd, self._vol_at(t)
        o = px
        steps = [rnd.gauss(drift / 6, 0.22 * v) for _ in range(6)]
        path = [o]
        for s in steps:
            path.append(path[-1] + s)
        c = path[-1]
        h = max(path) + abs(rnd.gauss(0, 0.08 * v))
        l = min(path) - abs(rnd.gauss(0, 0.08 * v))
        vol = max(5.0, rnd.gauss(120 * v, 40 * v))
        return [t, round(o, 2), round(h, 2), round(l, 2), round(c, 2), vol, 0.20]

    def _advance(self) -> None:
        elapsed = (time.time() - self.live_start) / self.speed
        target = self.base_t + int(elapsed) * 60
        drift = self.rnd.uniform(-0.05, 0.05)
        while self.m1[-1][0] + 60 < target:
            self.m1.append(self._bar(self.m1[-1][0] + 60, self.m1[-1][4], drift))
        frac = elapsed - int(elapsed)
        last = self.m1[-1]
        if self.form is None or self.form[0] != last[0] + 60:
            self.form = [last[0] + 60, last[4], last[4], last[4], last[4], 1.0, 0.20]
        f = self.form
        f[4] = round(f[4] + self.rnd.gauss(0, 0.12), 2)
        f[2], f[3], f[5] = max(f[2], f[4]), min(f[3], f[4]), f[5] + 3 * frac

    def rates(self, tf: str, count: int) -> Bars:
        self._advance()
        rows = self.m1 + [self.form]
        sec = TF_SECONDS[tf]
        b = Bars(sec)
        if sec == 60:
            for r in rows[-int(count):]:
                b.append(*r)
            return b
        agg = {}
        order = []
        for r in rows:
            k = r[0] - r[0] % sec
            if k not in agg:
                agg[k] = [k, r[1], r[2], r[3], r[4], r[5], r[6]]
                order.append(k)
            else:
                a = agg[k]
                a[2], a[3], a[4], a[5] = max(a[2], r[2]), min(a[3], r[3]), r[4], a[5] + r[5]
        for k in order[-int(count):]:
            b.append(*agg[k])
        return b

    def tick(self) -> dict:
        self._advance()
        return {"bid": self.form[4], "ask": round(self.form[4] + 0.20, 2), "time": self.form[0] + 30}

    def spec(self) -> Spec:
        return Spec(point=0.01, digits=2, vpu=100.0, min_lot=0.01, lot_step=0.01, max_lot=100.0)

    def account(self) -> dict:
        float_pl = sum(p["profit"] for p in self.positions())
        return {"balance": round(self.balance, 2), "equity": round(self.balance + float_pl, 2), "currency": "USD",
                "server": "Demo data (not your broker)", "company": "Demo", "mode": "paper",
                "trade_allowed": True, "ping_ms": None}

    # paper trading on the synthetic feed
    def positions(self) -> list:
        tk = self.tick()
        out = []
        for p in list(self._pos):
            px = tk["bid"] if p["side"] == "BUY" else tk["ask"]
            d = 1 if p["side"] == "BUY" else -1
            hit_sl = p["sl"] and ((d == 1 and px <= p["sl"]) or (d == -1 and px >= p["sl"]))
            hit_tp = p["tp"] and ((d == 1 and px >= p["tp"]) or (d == -1 and px <= p["tp"]))
            if hit_sl or hit_tp:
                exit_px = p["sl"] if hit_sl else p["tp"]
                self.balance += (exit_px - p["open"]) * d * p["volume"] * 100.0
                self._pos.remove(p)
                continue
            p["price"] = px
            p["profit"] = round((px - p["open"]) * d * p["volume"] * 100.0, 2)
            out.append(dict(p))
        return out

    def market(self, side: str, lots: float, sl: float, tp: float) -> dict:
        tk = self.tick()
        self._next_ticket += 1
        px = tk["ask"] if side == "BUY" else tk["bid"]
        self._pos = self._pos + [{"ticket": self._next_ticket, "side": side, "volume": float(lots), "open": px,
                                  "sl": float(sl or 0), "tp": float(tp or 0), "price": px, "profit": 0.0,
                                  "time": tk["time"], "magic": MAGIC, "comment": "paper"}]
        return {"ok": True, "message": "Paper fill (demo data)", "price": px, "volume": lots,
                "order": self._next_ticket, "ms": 0.1}

    def close(self, ticket: int, volume: float | None = None) -> dict:
        self.positions()
        p = next((x for x in self._pos if x["ticket"] == ticket), None)
        if p is None:
            return {"ok": False, "message": "Position not found (already closed?)"}
        vol = min(float(volume), p["volume"]) if volume else p["volume"]
        d = 1 if p["side"] == "BUY" else -1
        self.balance += (p["price"] - p["open"]) * d * vol * 100.0
        p["volume"] = round(p["volume"] - vol, 2)
        if p["volume"] <= 0:
            self._pos.remove(p)
        return {"ok": True, "message": "Paper close", "price": p["price"], "volume": vol, "ms": 0.1}

    def modify(self, ticket: int, sl: float, tp: float) -> dict:
        p = next((x for x in self._pos if x["ticket"] == ticket), None)
        if p is None:
            return {"ok": False, "message": "Position not found"}
        p["sl"], p["tp"] = float(sl or 0), float(tp or 0)
        return {"ok": True, "message": "Paper modify", "ms": 0.1}


# =================================================================== live hub
class Hub:
    def __init__(self, source, params: Params, utc_offset_hours: float | None = None, max_lots: float = 1.0,
                 entry_tf: str = "M5"):
        self.src, self.p = source, params
        self.tf, self.sec = entry_tf, TF_SECONDS[entry_tf]     # the indicator enters on closed bars of this timeframe
        self.max_lots = max_lots
        self.lock = threading.RLock()
        self.fixed_offset = None if utc_offset_hours is None else int(utc_offset_hours * 3600)
        self.live_offset = None
        self.events: list = []
        self.event_id = 0
        self.radar_bar = {1: 0, -1: 0}
        self.radar = None
        self.error = None
        self.spec = source.spec()
        self.engine: Engine | None = None
        self.forming = None
        self.tick_ = None
        self.kronos = None
        self.boom = BoomTracker(self.spec.digits)
        self.soon: SoonAlerts | None = None     # "setup likely soon" pushes to your phone (needs Kronos)
        self.booted = False                     # history loaded; until then the page opens and says why not
        self.chart_tf = entry_tf                # the timeframe the page's chart shows (its last candle request)
        self.smc, self._smc_at, self._smc_tf, self._smc_err = None, 0.0, None, None
        self.bootstrap()

    # server clock -> UTC
    def offset(self, server_t: int) -> int:
        if self.fixed_offset is not None:
            return self.fixed_offset
        if self.live_offset is not None:
            return self.live_offset
        return ny7_offset(server_t)

    def _learn_offset(self, tick: dict | None) -> None:
        if self.src.kind != "mt5" or tick is None or self.fixed_offset is not None:
            return
        d = tick["time"] - time.time()
        cand = round(d / 1800) * 1800
        if abs(d - cand) < 120:          # the tick is fresh, so the difference is the server's UTC offset
            self.live_offset = int(cand)

    def _htf(self, entry_bars: int):
        """Macro / structure / zone bars covering `entry_bars` entry bars plus each one's warm-up."""
        p = self.p
        need = lambda tf, warm: int(entry_bars * self.sec / TF_SECONDS[tf]) + warm + 10
        macro, struct, zone = LADDER[self.tf]
        return (self.src.rates(macro, need(macro, p.macro_slow * 5)),
                self.src.rates(struct, need(struct, p.struct_slow * 6)),
                self.src.rates(zone, need(zone, p.rsi_band_len * 4 + p.zone_max_age)))

    def bootstrap(self) -> None:
        """Load the history and replay it. If the broker won't give candles yet, start empty and retry from poll."""
        with self.lock:
            try:
                self._learn_offset(self.src.tick())
                m1 = self.src.rates(self.tf, HISTORY_BARS + 1)
                acct = self.src.account()
                eng = Engine(self.p, self.spec, acct["balance"] or 1000.0, self.offset, self.sec)
                eng.set_htf(*self._htf(HISTORY_BARS))
            except Exception as e:
                self.error = f"Waiting for candle history: {e}"
                print(self.error, flush=True)
                if self.engine is None:
                    self.engine = Engine(self.p, self.spec, 1000.0, self.offset, self.sec)    # empty for now
                return
            for i in range(len(m1) - 1):                      # last bar is still forming
                eng.add_bar(m1.t[i], m1.o[i], m1.h[i], m1.l[i], m1.c[i], m1.v[i], m1.spread[i])
            self.engine = eng
            self.forming = self._bar_dict(m1, len(m1) - 1) if len(m1) else None
            self.booted, self.error = True, None

    @staticmethod
    def _bar_dict(b: Bars, i: int) -> dict:
        return {"time": b.t[i], "open": b.o[i], "high": b.h[i], "low": b.l[i], "close": b.c[i]}

    def _event(self, kind: str, text: str, side: str | None = None) -> None:
        self.event_id += 1
        self.events.append({"id": self.event_id, "kind": kind, "text": text, "side": side, "at": int(time.time())})
        self.events = self.events[-60:]

    def poll(self) -> None:
        if not self.booted:
            self.bootstrap()
            if not self.booted:
                raise RuntimeError(self.error)
        with self.lock:
            tick = self.src.tick()
            self.tick_ = tick
            self._learn_offset(tick)
            eng = self.engine
            last_t = eng.m1.t[-1] if len(eng.m1) else 0
            m1 = self.src.rates(self.tf, 10)
            if len(m1) and m1.t[0] > last_t + self.sec and last_t:
                m1 = self.src.rates(self.tf, min(5000, (m1.t[-1] - last_t) // self.sec + 5))
            if not len(m1):
                return
            new = [i for i in range(len(m1) - 1) if m1.t[i] > last_t]
            if new:
                eng.set_htf(*self._htf(len(new) + 10))
                for i in new:
                    opened = eng.add_bar(m1.t[i], m1.o[i], m1.h[i], m1.l[i], m1.c[i], m1.v[i], m1.spread[i])
                    if opened and i == len(m1) - 2:
                        self._event("execute", self.exec_text(opened), opened["side"])
                    for done in self.boom.on_bar(m1.t[i], m1.h[i], m1.l[i], m1.c[i], self.sec, eng.bar_spread(len(eng.m1) - 1)):
                        self._event("boom_end", done["text"], done["side"])
            self.forming = self._bar_dict(m1, len(m1) - 1)
            self._radar()
            self._update_smc()

    def _update_smc(self) -> None:
        """Smart Money / ICT read of the chart's timeframe (state.smc): when the chart's timeframe changes, else
        every 15 s, so a closed candle shows up within that."""
        tf = self.chart_tf
        if tf == self._smc_tf and time.time() - self._smc_at < 15:
            return
        self._smc_tf, self._smc_at = tf, time.time()
        try:
            b = self.src.rates(tf, 1200)
            self.smc = smc_analyze(b, b if tf == "H1" else self.src.rates("H1", 400), self.offset)
            if self.smc:
                self.smc["tf"] = tf
            self._smc_err = None
        except Exception as e:
            self.smc = None
            if str(e) != self._smc_err:
                self._smc_err = str(e)
                print(f"SMC/ICT layer skipped: {e}", flush=True)

    def exec_text(self, tr: dict) -> str:
        d = self.spec.digits
        return (f"XAUUSD SCALP EXECUTE: {tr['side']} @ {tr['entry']:.{d}f} | SL: {tr['sl0']:.{d}f} | "
                f"TP1: {tr['tp1']:.{d}f} | TP2: {tr['tp2']:.{d}f}")

    def _radar(self) -> None:
        """Bar still open: HTF aligned and price at a zone or sweeping a swing."""
        eng, f = self.engine, self.forming
        self.radar = None
        if eng.ctx is None or eng.cur is not None or f is None or len(eng.m1) < self.p.sweep_len:
            return
        if not session_ok(f["time"], self.p, self.offset):
            return
        lows, highs = eng.m1.l[-self.p.sweep_len:], eng.m1.h[-self.p.sweep_len:]
        sides = []
        for d in (1, -1):
            at_zone = any(z["dir"] == d and f["low"] <= z["top"] and f["high"] >= z["bottom"] for z in eng.zones)
            swept = f["low"] < min(lows) if d == 1 else f["high"] > max(highs)
            if eng.htf_ok(eng.ctx, d) and (at_zone or swept):
                sides.append(d)
                if self.radar_bar[d] != f["time"]:
                    self.radar_bar[d] = f["time"]
                    side = "BUY" if d == 1 else "SELL"
                    self._event("radar", f"XAUUSD SCALP RADAR: Setup forming on {self.tf}. HTF Confluence verified. "
                                         f"Prepare for entry. [{side}]", side)
        if sides:
            self.radar = "BUY" if sides == [1] else ("SELL" if sides == [-1] else "BOTH")

    def _quote(self) -> dict | None:
        """The live quote, or (broker page not connected) the last price of the forming bar plus the usual spread."""
        tk = self.tick_ or self.src.tick()
        if tk or not self.forming:
            return tk
        px = self.forming["close"]
        return {"bid": px, "ask": round(px + getattr(self.src, "spread", 0.22), self.spec.digits),
                "time": self.forming["time"]}

    def on_kronos(self, fc: dict) -> None:
        """A Kronos forecast finished (worker thread): check it against the indicator for a Boom / Crash call,
        then for a setup it expects soon (a heads-up push to your phone)."""
        with self.lock:
            eng = self.engine
            if not eng or not len(eng.m1) or fc.get("t") != eng.m1.t[-1] or eng.ctx is None:
                return                                   # a newer bar closed meanwhile; the next forecast decides
            sig = self.boom.on_forecast(fc, boom_setup(eng), self._quote(), self.sec)
            if sig:
                self._event("boom", sig["text"], sig["side"])
            if self.soon:
                busy = {x["dir"] for x in (eng.cur, self.boom.active) if x}
                for pr in self.soon.check(eng, fc, self.sec, busy):
                    self._event("soon", f"{pr['title']}: watch {pr['area'][0]:.2f}-{pr['area'][1]:.2f}", pr["side"])

    # ------------------------------------------------------------ API payloads
    def market(self) -> dict:
        """Is gold trading now? Synthetic demo prices never stop, so the demo is always open."""
        now = time.time()
        is_open, why, back = market_hours(now) if self.src.kind != "demo" else (True, None, None)
        note = None
        if not is_open:
            mins = max(1, -(-(back - int(now)) // 60))
            d, h, m = mins // 1440, mins % 1440 // 60, mins % 60
            wait = " ".join(f"{v} {u}" for v, u in (((d, "d"), (h, "h")) if d else ((h, "h"), (m, "min"))) if v)
            note = (f"Market closed: gold's daily break (17:00 to 18:00 New York). Prices start again by themselves in {wait}."
                    if why == "daily break" else
                    f"Market closed for the weekend. Gold opens again Sunday 18:00 New York time, in {wait}.")
        return {"open": is_open, "why": why, "reopens": back, "note": note}

    def state(self) -> dict:
        mk = self.market()
        with self.lock:
            eng, tk = self.engine, self.tick_
            acct = self.src.account()
            now_srv = tk["time"] if tk else (self.forming["time"] if self.forming else int(time.time()))
            ctx = eng.ctx
            um = utc_minutes(now_srv, self.offset)
            ses = session_of(now_srv, self.offset)
            asian = eng.asian
            ar = (asian["hi"] - asian["lo"]) if asian["hi"] else None
            closed = [t for t in eng.trades if not t["open"]][-80:]
            return {
                "source": self.src.kind, "symbol": self.src.symbol, "account": acct,
                "spec": asdict(self.spec), "params": asdict(self.p),
                "tick": tk, "forming": self.forming,
                "clock": {"utc": f"{um // 60:02d}:{um % 60:02d}", "session": SESSION_NAMES[ses],
                          "session_ok": session_ok(now_srv, self.p, self.offset), "server_time": now_srv,
                          "utc_offset_h": self.offset(now_srv) / 3600,
                          "offset_source": "fixed" if self.fixed_offset is not None else
                          ("terminal" if self.live_offset is not None else "NY+7 rule")},
                "asian": {"range": ar, "contracted": bool(ar and ctx and ar < 2.0 * ctx["atr_macro"])},
                "context": ctx, "zones": eng.zones, "radar": self.radar,
                "active": eng.cur, "last_trade": eng.trades[-1] if eng.trades else None,
                "trades": closed, "stats": eng.stats(),
                "atr_m1": eng.atr[-1] if eng.atr else None,       # ATR(14) of the entry timeframe (name kept for the page)
                "entry_tf": self.tf, "ladder": dict(zip(("macro", "struct", "zone"), LADDER[self.tf])),
                "events": self.events[-30:],
                "error": self.error or getattr(self.src, "feed_note", None) or mk["note"],
                "market": mk,
                "positions": self.src.positions(), "caps": getattr(self.src, "caps", {"positions": True}),
                "broker_rows": getattr(self.src, "rows", []),
                "broker": self.src.broker() if hasattr(self.src, "broker") else {"connected": True, "message": None},
                "kronos": self.kronos.state() if self.kronos else None,
                "boom": self.boom.state(),
                "alerts": self.soon.state() if self.soon else None,
                "smc": self.smc,
                "max_lots": min(self.max_lots, self.spec.max_lot),
            }

    # ------------------------------------------------------------ manual orders (your clicks only)
    def _round(self, px) -> float:
        return round(float(px or 0.0), self.spec.digits)

    def _lots(self, lots) -> float:
        sp = self.spec
        v = math.floor(float(lots) / sp.lot_step + 1e-9) * sp.lot_step
        return round(v, 2)

    def order(self, body: dict) -> dict:
        side = str(body.get("side", "")).upper()
        if side not in ("BUY", "SELL"):
            return {"ok": False, "message": "Side must be BUY or SELL"}
        lots = self._lots(body.get("lots", 0))
        cap = min(self.max_lots, self.spec.max_lot)
        if lots < self.spec.min_lot:
            return {"ok": False, "message": f"Lot size is below your broker's minimum ({self.spec.min_lot})"}
        if lots > cap + 1e-9:
            return {"ok": False, "message": f"Lot size {lots} is above the {cap} lot cap. Raise --max-lots to allow it."}
        tk = self.src.tick()
        if not tk:
            return {"ok": False, "message": "No live price from the broker right now"}
        sl, tp = self._round(body.get("sl")), self._round(body.get("tp"))
        if side == "BUY" and ((sl and sl >= tk["bid"]) or (tp and tp <= tk["ask"])):
            return {"ok": False, "message": "For a BUY the stop must be below the bid and the target above the ask"}
        if side == "SELL" and ((sl and sl <= tk["ask"]) or (tp and tp >= tk["bid"])):
            return {"ok": False, "message": "For a SELL the stop must be above the ask and the target below the bid"}
        res = self.src.market(side, lots, sl, tp)
        self._log_trade(f"{side} {lots} lots", res, side)
        return res

    def close(self, body: dict) -> dict:
        vol = body.get("volume")
        res = self.src.close(int(body["ticket"]), self._lots(vol) if vol else None)
        self._log_trade(f"Close #{body['ticket']}" + (f" {vol} lots" if vol else ""), res)
        return res

    def close_all(self) -> dict:
        results = [self.src.close(p["ticket"]) for p in self.src.positions()]
        ok = all(r["ok"] for r in results)
        res = {"ok": ok, "message": f"Closed {sum(r['ok'] for r in results)} of {len(results)} positions",
               "ms": round(sum(r.get("ms", 0) or 0 for r in results), 1)}
        self._log_trade("Close all", res)
        return res

    def modify(self, body: dict) -> dict:
        sl = self._round(body.get("sl"))
        pos = next((p for p in self.src.positions() if p["ticket"] == int(body["ticket"])), None)
        tk = self.src.tick()
        if pos and tk and sl and ((pos["side"] == "BUY" and sl >= tk["bid"]) or (pos["side"] == "SELL" and sl <= tk["ask"])):
            return {"ok": False, "message": "That stop is on the wrong side of the current price. Breakeven needs the trade in profit first."}
        res = self.src.modify(int(body["ticket"]), self._round(body.get("sl")), self._round(body.get("tp")))
        self._log_trade(f"Modify #{body['ticket']}", res)
        return res

    def _log_trade(self, what: str, res: dict, side: str | None = None) -> None:
        with self.lock:
            msg = res.get("message") or ""
            price = f" @ {res['price']:.{self.spec.digits}f}" if res.get("ok") and res.get("price") else ""
            self._event("order" if res.get("ok") else "reject",
                        f"{what}{price}: {msg} ({res.get('ms', '?')} ms)", side)

    def candles(self, tf: str, count: int) -> list:
        with self.lock:
            self.chart_tf = tf
            b = self.src.rates(tf, count)
            return [{"time": b.t[i], "open": b.o[i], "high": b.h[i], "low": b.l[i], "close": b.c[i]}
                    for i in range(len(b))]

    def backtest(self, days: int, overrides: dict) -> dict:
        p = Params.from_dict({**asdict(self.p), **overrides})
        bars = days * 86400 // self.sec
        with self.lock:
            m1 = self.src.rates(self.tf, bars + 200)
            acct = self.src.account()
            htf = self._htf(bars)
        start_eq = float(overrides.get("balance") or acct["balance"] or 1000.0)
        t0 = time.time()
        res = run_backtest(_closed(m1), *htf, p, self.spec, start_eq, self.offset)
        res["seconds"] = round(time.time() - t0, 2)
        res["source"] = self.src.kind
        res["bars_loaded"] = len(m1)
        res["days_requested"] = days
        return res


def _closed(b: Bars) -> Bars:
    """Drop the still-forming last bar."""
    out = Bars(b.sec)
    n = len(b) - 1
    out.t, out.o, out.h, out.l, out.c, out.v, out.spread = b.t[:n], b.o[:n], b.h[:n], b.l[:n], b.c[:n], b.v[:n], b.spread[:n]
    return out


# =================================================================== HTTP
HUB: Hub | None = None
TOKEN = secrets.token_hex(16)     # the page gets it; other websites cannot read it
PORT = 8765


def _clean(o):
    """JSON-safe: NaN/inf become null."""
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(_clean(obj)).encode(), "application/json")

    def _host_ok(self) -> bool:
        # blocks DNS-rebinding: only http://127.0.0.1:PORT or http://localhost:PORT may talk to us
        return self.headers.get("Host", "") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-Dash-Token") != TOKEN:
            return self._json({"ok": False, "message": "Forbidden"}, 403)
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            route = urlparse(self.path).path
            if route == "/api/order":
                return self._json(HUB.order(body))
            if route == "/api/close":
                return self._json(HUB.close(body))
            if route == "/api/close_all":
                return self._json(HUB.close_all())
            if route == "/api/modify":
                return self._json(HUB.modify(body))
            return self._json({"ok": False, "message": "unknown route"}, 404)
        except Exception as e:
            traceback.print_exc()
            return self._json({"ok": False, "message": str(e)}, 500)

    def _live(self) -> None:
        """Server-sent events: every bid/ask change, pushed as it happens."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        last, beat = None, time.time()
        try:
            while True:
                tk = HUB.src.tick()
                if tk and (tk["bid"], tk["ask"]) != last:
                    last = (tk["bid"], tk["ask"])
                    self.wfile.write(f"data: {json.dumps({'bid': tk['bid'], 'ask': tk['ask'], 't': time.time()})}\n\n".encode())
                    self.wfile.flush()
                    beat = time.time()
                elif time.time() - beat > 10:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    beat = time.time()
                time.sleep(0.03)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, b"forbidden", "text/plain")
        u = urlparse(self.path)
        if u.path == "/api/live":
            return self._live()
        q = {k: v[-1] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/api/state":
                return self._json(HUB.state())
            if u.path == "/api/candles":
                tf = q.get("tf", HUB.tf)
                if tf not in TF_SECONDS:
                    return self._json({"error": "unknown timeframe"}, 400)
                return self._json(HUB.candles(tf, min(int(q.get("count", 600)), 5000)))
            if u.path == "/api/backtest":
                days = max(1, min(int(q.pop("days", 10)), 90))
                return self._json(HUB.backtest(days, q))
            name = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
            f = (STATIC / name).resolve()
            if STATIC not in f.parents or not f.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = {"html": "text/html; charset=utf-8", "js": "text/javascript", "css": "text/css"}.get(
                f.suffix.lstrip("."), "application/octet-stream")
            data = f.read_bytes()
            if f.name == "index.html":
                data = data.replace(b"__DASH_TOKEN__", TOKEN.encode())
            return self._send(200, data, ctype)
        except Exception as e:  # keep the page alive and show the reason
            traceback.print_exc()
            return self._json({"error": str(e)}, 500)


def poll_loop() -> None:
    said = None
    while True:
        try:
            HUB.poll()
            HUB.error = said = None
            time.sleep(0.25)
        except Exception as e:
            HUB.error = str(e)
            if HUB.error != said:                  # once per new problem, not four times a second
                said = HUB.error
                traceback.print_exc()
            time.sleep(2)


def main() -> None:
    global HUB, PORT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="synthetic data, no MT5 needed")
    ap.add_argument("--symbol", help="broker symbol, e.g. XAUUSDm (auto-detected by default)")
    ap.add_argument("--terminal", help="path to terminal64.exe if you run several MT5 installs")
    ap.add_argument("--utc-offset", type=float, help="broker server UTC offset in hours (auto by default)")
    ap.add_argument("--max-lots", type=float, default=1.0, help="largest order the page may send (default 1.0)")
    ap.add_argument("--litefinance", action="store_true", help="use the LiteFinance web terminal (Mac or Windows)")
    ap.add_argument("--litefinance-login", action="store_true", help="open a window to log in to LiteFinance and save it")
    ap.add_argument("--lf-headless", action="store_true", help="hide the LiteFinance browser window")
    ap.add_argument("--lf-dry-run", action="store_true", help="fill the LiteFinance ticket but never press its button")
    ap.add_argument("--lf-url", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--account", choices=["demo", "real"], help="label the LiteFinance account (auto by default)")
    ap.add_argument("--kronos", nargs="?", const="small", choices=["mini", "small", "base"],
                    help="show Kronos forecasts (model size, default small); needs install_kronos.sh")
    ap.add_argument("--kronos-repo", help="folder with the Kronos code (default: ../Kronos)")
    ap.add_argument("--entry-tf", default="M5", choices=sorted(LADDER), help="timeframe the indicator enters on (default M5)")
    ap.add_argument("--ntfy-topic", help="ntfy topic for 'setup likely soon' pushes (default: ~/.golddesk/ntfy_topic, "
                                         "else your bots' NTFY_TOPIC)")
    ap.add_argument("--no-alerts", action="store_true", help="never push 'setup likely soon' heads-ups to ntfy")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    if a.litefinance_login:
        from litefinance import login
        return login()
    if a.demo:
        src = DemoSource()
    elif a.litefinance:
        from litefinance import CHART, LiteFinanceSource
        print("Opening LiteFinance...")
        src = LiteFinanceSource(headless=a.lf_headless, account_type=a.account, url=a.lf_url or CHART,
                                dry_run=a.lf_dry_run)
        if a.utc_offset is None:
            a.utc_offset = 0.0            # LiteFinance history is in UTC
    else:
        try:
            src = MT5Source(a.symbol, a.terminal)
        except ImportError:
            raise SystemExit("The MetaTrader5 package is missing. Run: py -m pip install MetaTrader5")
    print(f"Data: {src.kind}  symbol: {src.symbol}  entry timeframe: {a.entry_tf}  loading history...")
    PORT = a.port
    HUB = Hub(src, Params(), a.utc_offset, a.max_lots, a.entry_tf)
    threading.Thread(target=poll_loop, daemon=True).start()
    if a.kronos:
        from kronos_signal import DEFAULT_REPO, HORIZON, KronosWorker
        topic, where = find_topic(a.ntfy_topic)
        HUB.soon = SoonAlerts(topic, where, ntfy_server(), push=src.kind != "demo" and not a.no_alerts)
        HUB.kronos = KronosWorker(HUB, repo=a.kronos_repo or DEFAULT_REPO, size=a.kronos,
                                  horizon=HORIZON.get(a.entry_tf, 15))
        print(f"Kronos-{a.kronos}: loading in the background (first run downloads it)")
        print("Setup heads-ups: " + (f"pushed to ntfy ({where})" if HUB.soon.ntfy else "shown on the page only"))
    url = f"http://127.0.0.1:{a.port}"
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    srv.daemon_threads = True
    print(f"Dashboard running at {url}  (Ctrl+C to stop)")
    if not a.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
