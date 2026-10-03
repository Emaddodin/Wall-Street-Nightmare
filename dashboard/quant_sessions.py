"""Session models for gold on H1: does one trading session tell the next one's direction?

A port, in pure Python, of the session hypotheses of Solomon Eshun's "Quantitative XAU/USD Session Strategy"
(https://github.com/soloshun/Quantitative-XAUUSD-Strategy, MIT licence, (c) 2025 Solomon Eshun):

  A  Asia -> London          features of the Asian session predict the London session (08:00-17:00 London)
  B  London morning -> overlap   London 08:00-13:00 predicts the London / New York overlap (13:00-17:00 London)
  C  Asia + London -> New York   both predict New York's afternoon (17:00 London to the 17:00 New York close)

Each target is the session's direction (last H1 close above the first H1 open) and its return in %. What is
different from the original, and why:

  - The clock. MT5 candle times are the broker's server time (New York + 7 at most gold brokers: UTC+2 in
    winter, UTC+3 in summer). The original reads them as UTC, which shifts every session by 2-3 hours. Here
    every candle goes through utc(t) first, and sessions follow Europe/London daylight saving (last Sunday of
    March to the last Sunday of October, 01:00 UTC), like the original's london_tz, without pytz.
  - No overlap. The original's Asia is 22:00-07:59 UTC all year, so in summer (London opens 07:00 UTC) its
    last hour is London's first: the features saw the start of the target. Here Asia is 22:00 London (the
    evening before) to the London open, and every feature is read from H1 candles closed by the moment the
    forecast is made (known_at = the target session's start).
  - Scale-free inputs. Gold now trades far above the 2015-2025 prices the original was fitted on, so dollar
    amounts become % of price and ATRs: asia_range -> asia_range / ATR and % of price, ATR -> % of price,
    EMA distances in %, returns in %.
  - More inputs: whether the Asian range (and PDH / PDL) was swept (traded beyond and closed back inside) or
    broken, ICT structure on H1 and H4 (ictlib.Tape: last sweep, last break and whether it was an MSS,
    premium / discount, structure bias, nearest open FVGs), distance to PDH / PDL in ATRs, the 24-hour return,
    silver's return over the same session and gold's strength against it, the day of the week.

Their own published results were weak (test accuracy 0.47-0.56 on a few hundred days, backtest Sharpe 0.38
against +73% buy and hold); quant_sessions_train.py measures this port the same honest way as the 30-minute
model and the page shows that record next to every forecast. The original's pickled XGBoost models are only
ever evaluated in that training report (--ref-repo), never used live.
"""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta, timezone

import quant_features as qf
from engine import Bars, ema, rsi
from ictlib import atr_series

DAY = 86400
H1 = 3600
H4 = 14400

# London-local minutes from the London date's midnight (negative: the evening before)
ASIA = (-120, 480)            # 22:00 the evening before -> the London open
LONDON = (480, 1020)          # 08:00 - 17:00
LMORNING = (480, 780)         # 08:00 - 13:00
OVERLAP = (780, 1020)         # 13:00 - 17:00
NEWYORK = (1020, 1320)        # 17:00 - 22:00 (gold closes at 17:00 New York, 22:00 London most of the year)

SESSIONS = {  # key: (name, hypothesis, target window, minimum target candles, what it waits for)
    "london": ("London", "A", LONDON, 6, "the Asian session to end at 08:00 London"),
    "overlap": ("London / New York overlap", "B", OVERLAP, 3, "the London morning to end at 13:00 London"),
    "ny": ("New York afternoon", "C", NEWYORK, 3, "London to close at 17:00 London"),
}
HYP_KEY = {v[1]: k for k, v in SESSIONS.items()}
ICT_WIN = {"H1": (5 * DAY, 2 * DAY), "H4": (20 * DAY, 10 * DAY)}
ICT_PICK = (("sweep_dir", 0), ("brk_dir", 4), ("brk_mss", 6), ("fvg_up", 7), ("fvg_dn", 8), ("pd", 9), ("bias", 10))
ICT_PICK_H4 = (("sweep_dir", 0), ("brk_dir", 4), ("pd", 9), ("bias", 10))

COMMON = (["dow", "atr_pct", "rsi", "ema50_pct", "ema200_pct", "pdh_atr", "pdl_atr", "ret24_pct"]
          + [f"h1_{k}" for k, _ in ICT_PICK] + [f"h4_{k}" for k, _ in ICT_PICK_H4])
ASIA_F = ["asia_ret_pct", "asia_range_atr", "asia_range_pct", "asia_pos", "asia_took_pdh", "asia_took_pdl",
          "xag_asia_ret", "rs_asia"]


def _sess_f(p: str) -> list:
    return [f"{p}_ret_pct", f"{p}_range_atr", f"{p}_pos", f"{p}_hi_swept", f"{p}_hi_broken", f"{p}_lo_swept",
            f"{p}_lo_broken", f"{p}_took_pdh", f"{p}_took_pdl", f"xag_{p}_ret", f"rs_{p}"]


FEATURES = {"A": tuple(COMMON + ASIA_F), "B": tuple(COMMON + ASIA_F + _sess_f("lm")),
            "C": tuple(COMMON + ASIA_F + _sess_f("lon"))}
# the original repository's inputs (unscaled, $), for the comparison in the training report only
ORIGINAL = ("day_of_week", "asia_return", "asia_range", "atr_at_asia_close", "rsi_at_asia_close", "ema50_dist",
            "ema200_dist")
ORIGINAL_EXTRA = {"A": (), "B": ("london_morning_return", "london_morning_range"),
                  "C": ("london_full_return", "london_full_range")}


# ------------------------------------------------------------------ London clock (Europe/London DST, no pytz)
_EU: dict = {}


def _last_sunday(y: int, m: int) -> int:
    d = datetime(y, m + 1, 1, tzinfo=timezone.utc) - timedelta(days=1) if m < 12 else datetime(y, 12, 31, tzinfo=timezone.utc)
    return d.day - (d.weekday() + 1) % 7


def eu_dst_bounds(y: int) -> tuple:
    """(start, end) UTC seconds of British Summer Time in year y: 01:00 UTC on the last Sundays of March and
    October (EU rule since 1996)."""
    b = _EU.get(y)
    if b is None:
        s = datetime(y, 3, _last_sunday(y, 3), 1, tzinfo=timezone.utc)
        e = datetime(y, 10, _last_sunday(y, 10), 1, tzinfo=timezone.utc)
        b = _EU[y] = (int(s.timestamp()), int(e.timestamp()))
    return b


def london_offset(u: int) -> int:
    y = datetime.fromtimestamp(int(u), timezone.utc).year
    s, e = eu_dst_bounds(y)
    return 3600 if s <= u < e else 0


def london_day(u: int) -> int:
    """London calendar date of UTC instant u, as days since 1970-01-01."""
    return (u + london_offset(u)) // DAY


def london_to_utc(day: int, minute: int) -> int:
    """UTC seconds of London local time `minute` minutes after midnight of London date `day` (minute may be
    negative or past 1440)."""
    naive = day * DAY + minute * 60
    u = naive - 3600
    return u if london_offset(u) == 3600 else naive


def weekday(day: int) -> int:
    """Monday = 0 for a day number (1970-01-01 was a Thursday)."""
    return (day + 3) % 7


def window(day: int, w: tuple) -> tuple:
    return london_to_utc(day, w[0]), london_to_utc(day, w[1])


def instance(key: str, now_utc: int) -> int:
    """The London date of the next `key` session that hasn't finished at now_utc (weekends skipped)."""
    d = london_day(now_utc)
    w = SESSIONS[key][2]
    for _ in range(8):
        if weekday(d) < 5 and london_to_utc(d, w[1]) > now_utc:
            return d
        d += 1
    return d


def hhmm(u: int) -> str:
    """London local hh:mm of UTC instant u."""
    x = u + london_offset(u)
    return f"{x % DAY // 3600:02d}:{x % 3600 // 60:02d}"


# ------------------------------------------------------------------ features from H1 candles
class SessionBuilder:
    """Session features and targets from closed H1 candles (any clock; utc(t) gives UTC seconds).
    silver: closed H1 candles of silver on the same clock (optional). tape_cache: kept by the live page."""

    def __init__(self, h1: Bars, silver: Bars | None = None, utc=None, tape_cache: dict | None = None):
        self.b = h1
        self.utc = utc
        self.U = list(h1.t) if utc is None else [utc(t) for t in h1.t]
        self.atr = atr_series(h1)
        self.rsi = rsi(h1.c, 14)
        self.e50, self.e200 = ema(h1.c, 50), ema(h1.c, 200)
        self.h4 = qf.merge_closed(None, h1, H4, utc)
        self.U4 = list(self.h4.t) if utc is None else [utc(t) for t in self.h4.t]
        self.tape_cache = tape_cache if tape_cache is not None else {}
        self.used_tapes: set = set()
        keys, hl = [], {}
        for k, u in enumerate(self.U):
            d = (qf.ny_clock(u) - 61200) // DAY                  # New York trading day (17:00 to 17:00)
            if d in hl:
                x = hl[d]
                x[0], x[1] = max(x[0], h1.h[k]), min(x[1], h1.l[k])
            else:
                hl[d] = [h1.h[k], h1.l[k]]
                keys.append(d)
        self.nyd, self.nyd_hl = keys, hl
        self.sv = silver
        self.SU = None if silver is None else (list(silver.t) if utc is None else [utc(t) for t in silver.t])

    # -- helpers ---------------------------------------------------------------------------------------------
    def span(self, a: int, z: int) -> tuple:
        """Indexes [i, j) of the H1 candles opening in [a, z) (UTC)."""
        return bisect_left(self.U, a), bisect_left(self.U, z)

    def stats(self, a: int, z: int):
        """(open, high, low, close, n) of the candles opening in [a, z), or None."""
        i, j = self.span(a, z)
        if j <= i:
            return None
        b = self.b
        return b.o[i], max(b.h[i:j]), min(b.l[i:j]), b.c[j - 1], j - i

    def _silver_ret(self, a: int, z: int) -> float | None:
        if self.sv is None:
            return None
        i, j = bisect_left(self.SU, a), bisect_left(self.SU, z)
        if j - i < 2 or self.sv.o[i] <= 0:
            return None
        return (self.sv.c[j - 1] - self.sv.o[i]) / self.sv.o[i] * 100

    def _pd(self, j: int):
        """PDH / PDL: the New York trading day before the one candle j is in."""
        d = (qf.ny_clock(self.U[j]) - 61200) // DAY
        k = bisect_left(self.nyd, d) - 1
        return self.nyd_hl[self.nyd[k]] if k >= 0 else (None, None)

    def _tape(self, name: str, b: Bars, U: list, j: int, price: float) -> list:
        if j < 0:
            return qf.tape_features(None, -1, price)
        W, B = ICT_WIN[name]
        tp, s0 = qf.window_tape(b, U, j, W, B, self.tape_cache, "S" + name, self.used_tapes,
                                drop_old=self.utc is None)
        return qf.tape_features(tp, j - s0, price)

    # -- one forecast's inputs -------------------------------------------------------------------------------
    def features(self, day: int, hyp: str) -> dict | None:
        """Inputs of hypothesis `hyp` for London date `day`, from candles closed at the target's start. None
        when the history doesn't cover it (no candles, indicators not warmed up, Asian session missing)."""
        key = HYP_KEY[hyp]
        known = london_to_utc(day, SESSIONS[key][2][0])
        j = bisect_right(self.U, known - H1) - 1                  # last H1 candle closed by `known`
        if j < 210 or self.U[j] < known - 6 * H1:
            return None
        b = self.b
        c = b.c[j]
        atr = self.atr[j] if self.atr[j] > 0 else 1e-9
        a0, a1 = window(day, ASIA)
        asia = self.stats(a0, min(a1, known))
        if asia is None or asia[4] < 4:
            return None
        pdh, pdl = self._pd(j)
        x = {"dow": float(weekday(day)), "atr_pct": atr / c * 100, "rsi": self.rsi[j],
             "ema50_pct": (c - self.e50[j]) / self.e50[j] * 100, "ema200_pct": (c - self.e200[j]) / self.e200[j] * 100,
             "pdh_atr": qf._clip((c - pdh) / atr, 30.0) if pdh is not None else 0.0,
             "pdl_atr": qf._clip((c - pdl) / atr, 30.0) if pdl is not None else 0.0}
        k24 = bisect_right(self.U, self.U[j] - DAY) - 1
        x["ret24_pct"] = (c - b.c[k24]) / b.c[k24] * 100 if k24 >= 0 else 0.0
        v = self._tape("H1", b, self.U, j, c)
        for name, ix in ICT_PICK:
            x[f"h1_{name}"] = v[ix]
        j4 = bisect_right(self.U4, known - H4) - 1
        v = self._tape("H4", self.h4, self.U4, j4, c)
        for name, ix in ICT_PICK_H4:
            x[f"h4_{name}"] = v[ix]
        ao, ah, al, ac, _ = asia
        x["asia_ret_pct"] = (ac - ao) / ao * 100
        x["asia_range_atr"] = (ah - al) / atr
        x["asia_range_pct"] = (ah - al) / ac * 100
        x["asia_pos"] = (ac - al) / (ah - al) if ah > al else 0.5
        x["asia_took_pdh"] = 1.0 if pdh is not None and ah > pdh else 0.0
        x["asia_took_pdl"] = 1.0 if pdl is not None and al < pdl else 0.0
        sr = self._silver_ret(a0, min(a1, known))
        x["xag_asia_ret"] = sr if sr is not None else 0.0
        x["rs_asia"] = x["asia_ret_pct"] - sr if sr is not None else 0.0
        if hyp in ("B", "C"):
            p, w = ("lm", LMORNING) if hyp == "B" else ("lon", LONDON)
            s0, s1 = window(day, w)
            s = self.stats(s0, s1)
            if s is None or s[4] < 2:
                return None
            so, sh, sl, sc, _ = s
            x[f"{p}_ret_pct"] = (sc - so) / so * 100
            x[f"{p}_range_atr"] = (sh - sl) / atr
            x[f"{p}_pos"] = (sc - sl) / (sh - sl) if sh > sl else 0.5
            x[f"{p}_hi_swept"] = 1.0 if sh > ah and sc <= ah else 0.0
            x[f"{p}_hi_broken"] = 1.0 if sh > ah and sc > ah else 0.0
            x[f"{p}_lo_swept"] = 1.0 if sl < al and sc >= al else 0.0
            x[f"{p}_lo_broken"] = 1.0 if sl < al and sc < al else 0.0
            x[f"{p}_took_pdh"] = 1.0 if pdh is not None and sh > pdh else 0.0
            x[f"{p}_took_pdl"] = 1.0 if pdl is not None and sl < pdl else 0.0
            sr = self._silver_ret(s0, s1)
            x[f"xag_{p}_ret"] = sr if sr is not None else 0.0
            x[f"rs_{p}"] = x[f"{p}_ret_pct"] - sr if sr is not None else 0.0
        return x

    def vector(self, day: int, hyp: str) -> list | None:
        x = self.features(day, hyp)
        return None if x is None else [x[k] for k in FEATURES[hyp]]

    def target(self, day: int, hyp: str):
        """(direction 1 / 0, return %, open, close) of the target session, or None when it has too few candles."""
        _, _, w, need, _ = SESSIONS[HYP_KEY[hyp]]
        a, z = window(day, w)
        s = self.stats(a, z)
        if s is None or s[4] < need or s[0] <= 0 or s[3] == s[0]:
            return None
        return (1 if s[3] > s[0] else 0), (s[3] - s[0]) / s[0] * 100, s[0], s[3]

    def original(self, day: int, hyp: str = "A") -> dict | None:
        """The original repository's inputs, unscaled: Asia 22:00 (previous UTC day) to 07:59 UTC, but cut at
        the London open so nothing overlaps the target; indicators at Asia's last candle; plus, for B / C, the
        return and $ range of the London morning / full London session (its research plan)."""
        key = HYP_KEY[hyp]
        known = london_to_utc(day, SESSIONS[key][2][0])
        lo_open = london_to_utc(day, LONDON[0])
        a0, a1 = day * DAY - 2 * H1, min(day * DAY + 8 * H1, lo_open)
        i, j = self.span(a0, a1)
        if j - i < 4 or j - 1 < 210:
            return None
        b, e = self.b, j - 1
        ao, ac = b.o[i], b.c[e]
        x = {"day_of_week": float(weekday(day)), "asia_return": (ac - ao) / ao, "asia_range": max(b.h[i:j]) - min(b.l[i:j]),
             "atr_at_asia_close": self.atr[e], "rsi_at_asia_close": self.rsi[e],
             "ema50_dist": (ac - self.e50[e]) / self.e50[e], "ema200_dist": (ac - self.e200[e]) / self.e200[e]}
        if hyp in ("B", "C"):
            w = LMORNING if hyp == "B" else LONDON
            s = self.stats(*window(day, w))
            if s is None:
                return None
            nm = "london_morning" if hyp == "B" else "london_full"
            x[f"{nm}_return"] = (s[3] - s[0]) / s[0]
            x[f"{nm}_range"] = s[1] - s[2]
            e2 = bisect_right(self.U, known - H1) - 1
            x["atr_at_asia_close"], x["rsi_at_asia_close"] = self.atr[e2], self.rsi[e2]
            x["ema50_dist"] = (b.c[e2] - self.e50[e2]) / self.e50[e2]
            x["ema200_dist"] = (b.c[e2] - self.e200[e2]) / self.e200[e2]
        return x

    def days(self) -> list:
        """London weekdays covered by the candles."""
        if not self.U:
            return []
        d0, d1 = london_day(self.U[0]), london_day(self.U[-1])
        return [d for d in range(d0, d1 + 1) if weekday(d) < 5]


def live_h1(bars: dict, utc) -> Bars:
    """Closed H1 candles for the live page: the broker's H1 list extended by H1 built from closed M1."""
    m1 = bars.get("M1")
    h1 = bars.get("H1")
    if m1 is None or not len(m1):
        return h1 if h1 is not None else Bars(H1)
    return qf.merge_closed(h1, m1, H1)


def live_silver_h1(silver, gold_last_t: int | None) -> Bars | None:
    """Silver H1 from {"H1": Bars, "M1": Bars} or an M1 Bars (closed candles, gold's clock)."""
    if silver is None:
        return None
    if isinstance(silver, Bars):
        silver = {"M1": silver}
    m1, h1 = silver.get("M1"), silver.get("H1")
    if m1 is not None and gold_last_t is not None and len(m1) and m1.t[-1] > gold_last_t:
        m1 = qf.sub(m1, 0, bisect_right(m1.t, gold_last_t))
    if m1 is not None and len(m1):
        return qf.merge_closed(h1, m1, H1)
    return h1
