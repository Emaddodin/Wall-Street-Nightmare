"""Gold scalping engine (same rules as mt5/XAU_M1_Scalper.mq5), on any entry timeframe.

                     M1 entry   M5 entry (Gold Desk default)
macro  regime/shock  H1         H4     EMA 50/200 regime + ATR velocity-shock guard
struct structure     M15        H1     BOS / CHoCH market structure + EMA 21/55 channel
zone   zones/RSI     M5         M15    fair value gaps, order blocks, RSI(14) with standard-deviation bands
entry  trigger       M1         M5     liquidity sweeps, engulfing (+volume), rejection pins, inside-bar breaks

Zero repaint: each entry bar is evaluated once, after it closed, using only
higher-timeframe bars that had closed by then. All times are the broker's
server time in epoch seconds, exactly as MetaTrader 5 returns them.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, asdict, fields
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional


@dataclass
class Params:
    mode: str = "strict"            # "strict": zone touch or sweep required; "balanced": pattern only
    session_filter: bool = True
    trade_london: bool = True       # 07:00-10:00 UTC
    trade_ny: bool = True           # 12:30-16:00 UTC
    sweep_len: int = 10
    use_engulf: bool = True
    vol_mult: float = 1.5
    use_pin: bool = True
    use_inside: bool = True
    use_sweep: bool = True
    touch_bars: int = 3
    cooldown_bars: int = 3
    macro_fast: int = 50
    macro_slow: int = 200
    shock_atr_mult: float = 1.8
    shock_range_mult: float = 2.5
    shock_hold: int = 2
    struct_fast: int = 21
    struct_slow: int = 55
    swing_len: int = 3
    rsi_len: int = 14
    rsi_band_len: int = 50
    rsi_band_k: float = 1.5
    fvg_min_atr: float = 0.10
    ob_disp_atr: float = 1.2
    zone_max_age: int = 48
    risk_pct: float = 1.0
    sl_lookback: int = 8
    sl_buffer_atr: float = 0.10
    min_sl_atr: float = 0.8
    atr_sl_mult: float = 1.5
    max_sl_atr: float = 3.0
    tp1_r: float = 1.5
    tp2_r: float = 2.5
    partial_pct: float = 50.0
    trail_atr: float = 1.0
    use_broker_spread: bool = True
    fixed_spread: float = 0.25
    commission: float = 7.0         # per 1.0 lot round turn
    slippage_pts: int = 1

    @classmethod
    def from_dict(cls, d: dict) -> "Params":
        p = cls()
        for f in fields(cls):
            if f.name in d and d[f.name] is not None:
                cur = getattr(p, f.name)
                v = d[f.name]
                if isinstance(cur, bool):
                    v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
                else:
                    v = type(cur)(v)
                setattr(p, f.name, v)
        return p


@dataclass
class Spec:
    point: float = 0.01
    digits: int = 2
    vpu: float = 100.0      # account money per 1.0 price move per 1.0 lot
    min_lot: float = 0.01
    lot_step: float = 0.01
    max_lot: float = 100.0


class Bars:
    """OHLC series, oldest first. `spread` is in price units."""

    def __init__(self, sec: int):
        self.sec = sec
        self.t: List[int] = []
        self.o: List[float] = []
        self.h: List[float] = []
        self.l: List[float] = []
        self.c: List[float] = []
        self.v: List[float] = []
        self.spread: List[float] = []

    def __len__(self) -> int:
        return len(self.t)

    def append(self, t, o, h, l, c, v=0.0, spread=0.0) -> None:
        self.t.append(int(t))
        self.o.append(float(o))
        self.h.append(float(h))
        self.l.append(float(l))
        self.c.append(float(c))
        self.v.append(float(v))
        self.spread.append(float(spread))

    def slice_from(self, start: int) -> "Bars":
        b = Bars(self.sec)
        b.t, b.o, b.h, b.l, b.c = self.t[start:], self.o[start:], self.h[start:], self.l[start:], self.c[start:]
        b.v, b.spread = self.v[start:], self.spread[start:]
        return b


# ---------------------------------------------------------------- math
def ema(src: List[float], n: int) -> List[float]:
    out: List[float] = []
    k = 2.0 / (n + 1.0)
    for i, x in enumerate(src):
        out.append(x if i == 0 else x * k + out[-1] * (1.0 - k))
    return out


def atr(h: List[float], l: List[float], c: List[float], n: int) -> List[float]:
    out: List[float] = []
    for i in range(len(c)):
        if i == 0:
            out.append(h[0] - l[0])
            continue
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        out.append((out[-1] * (n - 1) + tr) / n)
    return out


def sma(src: List[float], n: int) -> List[float]:
    out: List[float] = []
    s = 0.0
    for i, x in enumerate(src):
        s += x
        if i >= n:
            s -= src[i - n]
        out.append(s / min(i + 1, n))
    return out


def rsi(c: List[float], n: int) -> List[float]:
    out: List[float] = []
    ag = al = 0.0
    for i in range(len(c)):
        if i == 0:
            out.append(50.0)
            continue
        ch = c[i] - c[i - 1]
        g, d = max(ch, 0.0), max(-ch, 0.0)
        if i <= n:
            ag += g
            al += d
            if i < n:
                out.append(50.0)
                continue
            ag /= n
            al /= n
        else:
            ag = (ag * (n - 1) + g) / n
            al = (al * (n - 1) + d) / n
        out.append(100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al))
    return out


def bands(src: List[float], n: int, k: float):
    mid = sma(src, n)
    up, lo = [], []
    for i in range(len(src)):
        cnt = min(i + 1, n)
        m = mid[i]
        sd = math.sqrt(sum((src[j] - m) ** 2 for j in range(i - cnt + 1, i + 1)) / cnt)
        up.append(m + k * sd)
        lo.append(m - k * sd)
    return mid, up, lo


# ---------------------------------------------------------------- clock
def us_dst(server_t: int) -> bool:
    d = datetime.fromtimestamp(server_t, tz=timezone.utc)
    if d.month < 3 or d.month > 11:
        return False
    if 3 < d.month < 11:
        return True
    last_sunday = d.day - (d.isoweekday() % 7)   # isoweekday: Sunday = 7 -> 0
    if d.month == 3:
        return last_sunday >= 8
    return last_sunday < 1


def ny7_offset(server_t: int) -> int:
    """Most gold brokers run the server clock at New York + 7 (GMT+2 winter, GMT+3 summer)."""
    return (3 if us_dst(server_t) else 2) * 3600


def market_hours(utc: float) -> tuple:
    """Gold trades from Sunday 18:00 to Friday 17:00 New York time, with a break from 17:00 to 18:00 each day.
    -> (open?, why closed or None, UTC time it opens again or None)."""
    ny = int(utc) + ny7_offset(int(utc)) - 7 * 3600
    day, mins = (ny // 86400 + 3) % 7, ny % 86400 // 60          # Monday = 0
    if day == 5 or (day == 4 and mins >= 1020) or (day == 6 and mins < 1080):
        why, back = "weekend", (6 - day) * 86400 + 1080 * 60 - mins * 60
    elif 1020 <= mins < 1080:
        why, back = "daily break", 1080 * 60 - mins * 60
    else:
        return True, None, None
    t = int(utc) - int(utc) % 60 + back
    return False, why, t + ny7_offset(int(utc)) - ny7_offset(t)      # the clocks may change over a weekend


def utc_minutes(server_t: int, offset_fn: Callable[[int], int]) -> int:
    u = server_t - offset_fn(server_t)
    return (u % 86400) // 60


def session_of(server_t: int, offset_fn) -> int:
    """1 London open, 2 NY / US, 3 Asian, 0 between sessions."""
    m = utc_minutes(server_t, offset_fn)
    if 420 <= m < 600:
        return 1
    if 750 <= m < 960:
        return 2
    if m < 420:
        return 3
    return 0


SESSION_NAMES = {1: "London Open", 2: "NY / US", 3: "Asian", 0: "Between sessions"}


def session_ok(server_t: int, p: Params, offset_fn) -> bool:
    if not p.session_filter:
        return True
    s = session_of(server_t, offset_fn)
    return (s == 1 and p.trade_london) or (s == 2 and p.trade_ny)


# ---------------------------------------------------------------- higher timeframes
class HTF:
    def __init__(self, bars: Bars):
        self.b = bars
        self.closes = [t + bars.sec for t in bars.t]

    def last_closed(self, at: int) -> int:
        return bisect_right(self.closes, at) - 1


class Macro(HTF):
    def __init__(self, bars: Bars, p: Params):
        super().__init__(bars)
        b = bars
        self.ema_f = ema(b.c, p.macro_fast)
        self.ema_s = ema(b.c, p.macro_slow)
        self.atr = atr(b.h, b.l, b.c, 14)
        avg = sma(self.atr, 50)
        self.reg: List[int] = []
        self.shock: List[int] = []
        last_idx, last_dir = -10 ** 9, 0
        for j in range(len(b)):
            c, f, s = b.c[j], self.ema_f[j], self.ema_s[j]
            self.reg.append(1 if c > f > s else (-1 if c < f < s else 0))
            hit = (j >= 50 and self.atr[j] > p.shock_atr_mult * avg[j]) or \
                  (j >= 1 and (b.h[j] - b.l[j]) > p.shock_range_mult * self.atr[j - 1])
            if hit:
                last_idx, last_dir = j, (1 if b.c[j] >= b.o[j] else -1)
            self.shock.append(last_dir if j - last_idx < p.shock_hold else 0)


class Struct(HTF):
    def __init__(self, bars: Bars, p: Params):
        super().__init__(bars)
        b = bars
        ef, es = ema(b.c, p.struct_fast), ema(b.c, p.struct_slow)
        L = max(1, p.swing_len)
        sw_h = sw_l = 0.0
        h_broken = l_broken = True
        st = last_ev = 0
        self.struct, self.event, self.bias, self.ema_up, self.sw_h, self.sw_l = [], [], [], [], [], []
        for j in range(len(b)):
            c = j - L
            if c - L >= 0:
                win = range(c - L, c + L + 1)
                if all(b.h[k] <= b.h[c] for k in win if k != c):
                    sw_h, h_broken = b.h[c], False
                if all(b.l[k] >= b.l[c] for k in win if k != c):
                    sw_l, l_broken = b.l[c], False
            if not h_broken and b.c[j] > sw_h:
                last_ev = 2 if st == -1 else 1
                st, h_broken = 1, True
            elif not l_broken and b.c[j] < sw_l:
                last_ev = -2 if st == 1 else -1
                st, l_broken = -1, True
            up = ef[j] > es[j]
            self.struct.append(st)
            self.event.append(last_ev)
            self.ema_up.append(up)
            self.sw_h.append(sw_h)
            self.sw_l.append(sw_l)
            self.bias.append(1 if (st == 1 and up) else (-1 if (st == -1 and not up) else 0))


class ZoneTF(HTF):
    def __init__(self, bars: Bars, p: Params):
        super().__init__(bars)
        b = bars
        self.p = p
        self.atr = atr(b.h, b.l, b.c, 14)
        self.rsi = rsi(b.c, p.rsi_len)
        self.mid, self.up, self.lo = bands(self.rsi, p.rsi_band_len, p.rsi_band_k)
        self._cache_j = -1
        self._cache: List[dict] = []

    def zones_at(self, zj: int) -> List[dict]:
        """Unmitigated FVG / order-block zones built from the last zone_max_age closed bars."""
        if zj == self._cache_j:
            return self._cache
        b, p = self.b, self.p
        zones: List[dict] = []
        for j in range(max(3, zj - p.zone_max_age), zj + 1):
            cj = b.c[j]
            zones = [z for z in zones if not ((z["dir"] == 1 and cj < z["bottom"]) or (z["dir"] == -1 and cj > z["top"]))]
            a = self.atr[j]
            born = b.t[j] + b.sec
            new = []
            if b.l[j] > b.h[j - 2] and b.l[j] - b.h[j - 2] >= p.fvg_min_atr * a:
                new.append((1, "FVG", b.l[j], b.h[j - 2]))
            if b.h[j] < b.l[j - 2] and b.l[j - 2] - b.h[j] >= p.fvg_min_atr * a:
                new.append((-1, "FVG", b.l[j - 2], b.h[j]))
            if abs(b.c[j] - b.o[j]) >= p.ob_disp_atr * a:
                bull = b.c[j] > b.o[j]
                for k in range(j - 1, max(0, j - 3) - 1, -1):
                    if bull and b.c[k] < b.o[k]:
                        new.append((1, "OB", b.h[k], b.l[k]))
                        break
                    if not bull and b.c[k] > b.o[k]:
                        new.append((-1, "OB", b.h[k], b.l[k]))
                        break
            for d, kind, top, bot in new:
                if top > bot:
                    zones.append({"dir": d, "kind": kind, "top": top, "bottom": bot, "born": born})
            if len(zones) > 30:
                zones = zones[-30:]
        self._cache_j, self._cache = zj, zones
        return zones


def touch(zones: List[dict], d: int, lo: float, hi: float) -> bool:
    return any(z["dir"] == d and lo <= z["top"] and hi >= z["bottom"] for z in zones)


# ---------------------------------------------------------------- engine
# entry timeframe -> (macro, struct, zone) timeframes
LADDER = {"M1": ("H1", "M15", "M5"), "M5": ("H4", "H1", "M15")}


class Engine:
    """Feeds closed entry-timeframe bars one at a time and simulates the trade lifecycle.
    (The series is still called m1 for the API; its bar length is `sec`.)"""

    WHY = {1: "Engulfing", 2: "Rejection pin", 3: "Inside-bar break", 4: "Liquidity sweep"}

    def __init__(self, p: Params, spec: Spec, start_equity: float, offset_fn: Callable[[int], int] = ny7_offset,
                 sec: int = 60):
        self.p, self.spec, self.offset_fn, self.sec = p, spec, offset_fn, sec
        self.m1 = Bars(sec)
        self.atr: List[float] = []
        self.volsma: List[float] = []
        self.macro: Optional[Macro] = None
        self.struct: Optional[Struct] = None
        self.zone: Optional[ZoneTF] = None
        self.trades: List[dict] = []
        self.cur: Optional[dict] = None
        self.last_exit = -10 ** 9
        self.last_touch = {1: -10 ** 9, -1: -10 ** 9}
        self.sig = {1: 0, -1: 0}          # entry signal on the last bar per side (pattern id, 0 = none)
        self.warm = max(30, max(p.sweep_len, p.sl_lookback) + 5)
        self.start_eq = self.equity = self.peak = float(start_equity)
        self.max_dd = self.gross_w = self.gross_l = self.sum_r = 0.0
        self.wins = self.losses = 0
        self.equity_curve: List[list] = []
        self.ctx: Optional[dict] = None
        self.zones: List[dict] = []
        self.asian = {"day": None, "hi": 0.0, "lo": 0.0}

    # -- data
    def set_htf(self, macro: Bars, struct: Bars, zone: Bars) -> None:
        self.macro = Macro(macro, self.p)
        self.struct = Struct(struct, self.p)
        self.zone = ZoneTF(zone, self.p)

    def context(self, at: int) -> Optional[dict]:
        hj = self.macro.last_closed(at)
        sj = self.struct.last_closed(at)
        zj = self.zone.last_closed(at)
        if hj < 1 or sj < 1 or zj < 3:
            return None
        z = self.zone
        ctx = {
            "regime": self.macro.reg[hj], "shock": self.macro.shock[hj], "atr_macro": self.macro.atr[hj],
            "ema_f": self.macro.ema_f[hj], "ema_s": self.macro.ema_s[hj],
            "struct": self.struct.struct[sj], "event": self.struct.event[sj], "ema_up15": self.struct.ema_up[sj],
            "bias15": self.struct.bias[sj], "sw_h": self.struct.sw_h[sj], "sw_l": self.struct.sw_l[sj],
            "rsi": z.rsi[zj], "rsi_mid": z.mid[zj], "rsi_up": z.up[zj], "rsi_lo": z.lo[zj],
            "m5_long": z.rsi[zj] > z.rsi[zj - 1] and z.rsi[zj] < z.up[zj],
            "m5_short": z.rsi[zj] < z.rsi[zj - 1] and z.rsi[zj] > z.lo[zj],
        }
        self.zones = z.zones_at(zj)
        return ctx

    def htf_ok(self, ctx: dict, d: int) -> bool:
        if d == 1:
            return ctx["regime"] == 1 and ctx["shock"] != -1 and ctx["bias15"] == 1 and ctx["m5_long"]
        return ctx["regime"] == -1 and ctx["shock"] != 1 and ctx["bias15"] == -1 and ctx["m5_short"]

    def bar_spread(self, i: int) -> float:
        s = self.m1.spread[i]
        return s if (self.p.use_broker_spread and s > 0) else self.p.fixed_spread

    # -- sizing
    def norm_lots(self, lots: float) -> float:
        s = self.spec
        v = math.floor(lots / s.lot_step + 1e-9) * s.lot_step
        return round(min(v, s.max_lot), 2)

    def calc_lots(self, risk_money: float, sl_dist: float):
        per_lot = sl_dist * self.spec.vpu + self.p.commission
        if per_lot <= 0:
            return self.spec.min_lot, True
        lots = self.norm_lots(risk_money / per_lot)
        if lots < self.spec.min_lot:
            return self.spec.min_lot, True
        return lots, False

    # -- trades
    def _close(self, tr: dict, px: float, t: int, i: int) -> None:
        tr["pnl"] += (px - tr["entry"]) * tr["dir"] * tr["remain"] * self.spec.vpu
        tr.update(remain=0.0, exit=px, t_out=t, open=False)
        risk_money = tr["lots"] * tr["R"] * self.spec.vpu
        tr["r"] = tr["pnl"] / risk_money if risk_money > 0 else 0.0
        self.cur, self.last_exit = None, i
        self.equity += tr["pnl"]
        self.peak = max(self.peak, self.equity)
        if self.peak > 0:
            self.max_dd = max(self.max_dd, (self.peak - self.equity) / self.peak * 100.0)
        self.sum_r += tr["r"]
        if tr["pnl"] > 0:
            self.wins += 1
            self.gross_w += tr["pnl"]
        else:
            self.losses += 1
            self.gross_l += -tr["pnl"]
        self.equity_curve.append([t, round(self.equity, 2)])

    def _partial(self, tr: dict) -> None:
        leg = self.norm_lots(tr["lots"] * self.p.partial_pct / 100.0)
        if leg < self.spec.min_lot or tr["lots"] - leg < self.spec.min_lot - 1e-9:
            leg = 0.0   # 0.01 lot cannot be split: TP1 only moves the stop to breakeven
        tr["pnl"] += (tr["tp1"] - tr["entry"]) * tr["dir"] * leg * self.spec.vpu
        tr["remain"] -= leg
        tr["partial"] = True
        tr["sl"] = max(tr["sl"], tr["entry"]) if tr["dir"] == 1 else min(tr["sl"], tr["entry"])

    def _manage(self, i: int, ctx: dict) -> None:
        tr, m = self.cur, self.m1
        d = tr["dir"]
        o, h, l, c, t = m.o[i], m.h[i], m.l[i], m.c[i], m.t[i]
        spr, slip = self.bar_spread(i), self.p.slippage_pts * self.spec.point
        # likely intrabar path: bullish bars O-L-H-C, bearish O-H-L-C
        path = [o, l, h, c] if c >= o else [o, h, l, c]
        for k, raw in enumerate(path):
            px = raw if d == 1 else raw + spr        # longs exit on the bid, shorts on the ask
            if (d == 1 and px <= tr["sl"]) or (d == -1 and px >= tr["sl"]):
                fill = px if k == 0 else tr["sl"]    # a gap through the stop fills at the open
                self._close(tr, fill - d * slip, t, i)
                return
            if not tr["partial"] and ((d == 1 and px >= tr["tp1"]) or (d == -1 and px <= tr["tp1"])):
                self._partial(tr)
            if tr["partial"] and ((d == 1 and px >= tr["tp2"]) or (d == -1 and px <= tr["tp2"])):
                self._close(tr, tr["tp2"], t, i)
                return
        if tr["partial"]:
            if ctx["bias15"] == -d:                   # M15 structure flipped: structural invalidation
                px = c if d == 1 else c + spr
                self._close(tr, px - d * slip, t + self.sec, i)
                return
            a = self.atr[i]
            tr["sl"] = max(tr["sl"], c - self.p.trail_atr * a) if d == 1 else min(tr["sl"], c + spr + self.p.trail_atr * a)

    def _open(self, d: int, why: int, i: int, swing_lo: float, swing_hi: float) -> Optional[dict]:
        p, m = self.p, self.m1
        a, spr, slip = self.atr[i], self.bar_spread(i), p.slippage_pts * self.spec.point
        c = m.c[i]
        if d == 1:
            entry = c + spr + slip                      # buy at the ask
            sl = swing_lo - p.sl_buffer_atr * a
            if entry - sl < p.min_sl_atr * a:
                sl = entry - p.atr_sl_mult * a
        else:
            entry = c - slip                            # sell at the bid
            sl = swing_hi + p.sl_buffer_atr * a + spr   # stop triggers on the ask
            if sl - entry < p.min_sl_atr * a:
                sl = entry + p.atr_sl_mult * a
        R = abs(entry - sl)
        if R <= 0 or R > p.max_sl_atr * a:
            return None
        lots, capped = self.calc_lots(self.equity * p.risk_pct / 100.0, R)
        tr = {
            "id": len(self.trades), "dir": d, "side": "BUY" if d == 1 else "SELL", "why": self.WHY[why],
            "bar": i, "t_bar": m.t[i], "t_in": m.t[i] + self.sec, "t_out": None,
            "entry": entry, "sl0": sl, "sl": sl, "tp1": entry + d * p.tp1_r * R, "tp2": entry + d * p.tp2_r * R,
            "R": R, "lots": lots, "remain": lots, "partial": False, "open": True, "min_lot_capped": capped,
            "pnl": -p.commission * lots, "exit": None, "r": None,
        }
        self.trades.append(tr)
        self.cur = tr
        return tr

    def add_bar(self, t, o, h, l, c, v=0.0, spread=0.0) -> Optional[dict]:
        """Append one CLOSED entry bar and evaluate it. Returns the trade opened on it, if any."""
        m, p = self.m1, self.p
        m.append(t, o, h, l, c, v, spread)
        i = len(m) - 1
        self.sig = {1: 0, -1: 0}
        tr = (h - l) if i == 0 else max(h - l, abs(h - m.c[i - 1]), abs(l - m.c[i - 1]))
        self.atr.append(tr if i == 0 else (self.atr[-1] * 13.0 + tr) / 14.0)
        lo20 = max(0, i - 19)
        self.volsma.append(sum(m.v[lo20:i + 1]) / (i + 1 - lo20))

        u = t - self.offset_fn(t)
        day, mins = u // 86400, (u % 86400) // 60
        if day != self.asian["day"]:
            self.asian = {"day": day, "hi": 0.0, "lo": 0.0}
        if mins < 420:
            self.asian["hi"] = h if self.asian["hi"] == 0 else max(self.asian["hi"], h)
            self.asian["lo"] = l if self.asian["lo"] == 0 else min(self.asian["lo"], l)

        if i < self.warm or self.macro is None:
            return None
        ctx = self.context(t + self.sec)
        if ctx is None:
            return None
        self.ctx = ctx

        if self.cur is not None and i > self.cur["bar"]:
            self._manage(i, ctx)

        # entry-bar microstructure
        min_prev = min(m.l[i - p.sweep_len:i])
        max_prev = max(m.h[i - p.sweep_len:i])
        sweep = {1: l < min_prev and c > min_prev, -1: h > max_prev and c < max_prev}
        for d in (1, -1):
            if touch(self.zones, d, l, h):
                self.last_touch[d] = i
        body, rng = abs(c - o), h - l
        po, pc = m.o[i - 1], m.c[i - 1]
        p_body = abs(pc - po)
        vol_ok = v > p.vol_mult * self.volsma[i - 1]
        eng = {1: p.use_engulf and c > o and pc < po and c >= po and o <= pc and body > p_body and vol_ok,
               -1: p.use_engulf and c < o and pc > po and c <= po and o >= pc and body > p_body and vol_ok}
        lw, uw = min(o, c) - l, h - max(o, c)
        pin = {1: p.use_pin and rng > 0 and lw >= 2 * body and lw >= 0.6 * rng and (sweep[1] or self.last_touch[1] == i),
               -1: p.use_pin and rng > 0 and uw >= 2 * body and uw >= 0.6 * rng and (sweep[-1] or self.last_touch[-1] == i)}
        inside = m.h[i - 1] <= m.h[i - 2] and m.l[i - 1] >= m.l[i - 2]
        ib = {1: p.use_inside and inside and c > m.h[i - 2] and c > o,
              -1: p.use_inside and inside and c < m.l[i - 2] and c < o}
        sw = {1: p.use_sweep and sweep[1] and c > o, -1: p.use_sweep and sweep[-1] and c < o}

        sess = session_ok(t, p, self.offset_fn)
        sig = {}
        for d in (1, -1):
            why = 1 if eng[d] else 2 if pin[d] else 3 if ib[d] else 4 if sw[d] else 0
            conf = p.mode == "balanced" or (i - self.last_touch[d]) < p.touch_bars or sweep[d]
            sig[d] = why if (sess and self.htf_ok(ctx, d) and why and conf) else 0
        self.sig = sig

        if self.cur is not None or (i - self.last_exit) <= p.cooldown_bars or bool(sig[1]) == bool(sig[-1]):
            return None
        d = 1 if sig[1] else -1
        lo_n = max(0, i - p.sl_lookback + 1)
        return self._open(d, sig[d], i, min(m.l[lo_n:i + 1]), max(m.h[lo_n:i + 1]))

    # -- reporting
    def stats(self) -> dict:
        n = self.wins + self.losses
        return {
            "trades": n, "wins": self.wins, "losses": self.losses,
            "win_rate": (100.0 * self.wins / n) if n else 0.0,
            "profit_factor": (self.gross_w / self.gross_l) if self.gross_l > 0 else None,
            "max_dd_pct": self.max_dd,
            "avg_win": (self.gross_w / self.wins) if self.wins else 0.0,
            "avg_loss": (self.gross_l / self.losses) if self.losses else 0.0,
            "net": self.equity - self.start_eq, "net_r": self.sum_r,
            "start_equity": self.start_eq, "equity": self.equity,
            "from": self.m1.t[min(self.warm, len(self.m1) - 1)] if len(self.m1) else None,
            "to": self.m1.t[-1] if len(self.m1) else None,
            "bars": len(self.m1),
        }


def run_backtest(m1: Bars, macro: Bars, struct: Bars, zone: Bars, p: Params, spec: Spec,
                 start_equity: float, offset_fn=ny7_offset) -> dict:
    eng = Engine(p, spec, start_equity, offset_fn, m1.sec)
    eng.set_htf(macro, struct, zone)
    eng.equity_curve.append([m1.t[0] if len(m1) else 0, round(start_equity, 2)])
    for i in range(len(m1)):
        eng.add_bar(m1.t[i], m1.o[i], m1.h[i], m1.l[i], m1.c[i], m1.v[i], m1.spread[i])
    closed = [t for t in eng.trades if not t["open"]]
    return {"stats": eng.stats(), "trades": closed, "open_trade": eng.cur, "equity": eng.equity_curve,
            "params": asdict(p)}
