"""Core engine -- the Volume Profile + Market Structure scalper.

    bias      : 15m structure (HH/HL vs LH/LL); oversized swing legs fall
                back to the internal structure (last 15m ChoCH).
    location  : volume profile over the 15m swing range (VAH / POC / VAL).
    confirm   : price touches a VP level AND the 1m structure shifts
                (ChoCH in the bias direction).
    entry     : 1m order block / 1m FVG / micro-POC of the 1m leg.
    stop      : beyond the recent 1m swing (+ ATR buffer).
    target    : 50% at the nearest opposite 15m swing, 50% runner that
                trails 1m swings and exits on an opposite 1m BOS.

TESLA is gone.  The only learned inputs are the reimplemented stolgo /
ict / motivewave detectors in pa/.

The SAME engine drives backtests, paper trading and research mode -- only
the feed and the fill venue change.  No future bars: signals at a 1m close
fill at the next bar's open; 15m state consumed is CLOSED-bar state only.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config.loader import Config
from entry_engine import EntryEngine, EntrySignal
from execution import entry_price, exit_price, round_trip_cost_r
from indicators import atr, swing_points
from market_data.store import resample_ohlcv
from pa.fast import (eq_levels_np, fvg_state_np, order_blocks_np,
                     patterns_np, turtle_soup_np)
from position_manager import (Position, build_position, execute_struct_exit,
                              step_position)
from risk_manager import RiskManager
from structure import LONG, SHORT, opposite_bos
from universe_selector import _rank_rows

log = logging.getLogger("scalper.engine")

MIN_MS = 60_000
HOUR_MS = 3_600_000
DAY_MS = 86_400_000


# ======================================================================
@dataclass
class TFData:
    t: np.ndarray                 # open times (ms)
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    atr: np.ndarray | None = None
    swing_hi: np.ndarray | None = None   # confirmed swing value, indexed by confirm bar
    swing_lo: np.ndarray | None = None
    last_sh: np.ndarray | None = None    # most recent confirmed swing high known at j
    last_sl: np.ndarray | None = None
    spread24h: np.ndarray | None = None  # 1m: trailing 24h spread proxy (bps)
    # 1m entry-model state
    ob_bull: np.ndarray | None = None
    ob_bull_mt: np.ndarray | None = None
    ob_bear: np.ndarray | None = None
    ob_bear_mt: np.ndarray | None = None
    fvg_bull: np.ndarray | None = None
    fvg_bull_ce: np.ndarray | None = None
    fvg_bear: np.ndarray | None = None
    fvg_bear_ce: np.ndarray | None = None
    micro_poc: np.ndarray | None = None  # POC of the current 1m leg
    # 15m bias / volume-profile state
    sh1: np.ndarray | None = None
    sh2: np.ndarray | None = None
    sl1: np.ndarray | None = None
    sl2: np.ndarray | None = None
    struct_ok_up: np.ndarray | None = None
    struct_ok_dn: np.ndarray | None = None
    bias_dir: np.ndarray | None = None   # +1 / -1 / 0 per closed 15m bar
    vp_vah: np.ndarray | None = None
    vp_poc: np.ndarray | None = None
    vp_val: np.ndarray | None = None
    asia_high: np.ndarray | None = None
    asia_low: np.ndarray | None = None
    asia_swept_up: np.ndarray | None = None
    asia_swept_dn: np.ndarray | None = None
    ts_long: np.ndarray | None = None     # turtle-soup sweeps of EQL (fresh)
    ts_short: np.ndarray | None = None    # turtle-soup sweeps of EQH (fresh)
    atr_z: np.ndarray | None = None
    vol24h: np.ndarray | None = None


@dataclass
class SymbolData:
    sym: str
    df1m: pd.DataFrame
    tfs: dict[str, TFData]
    i15: np.ndarray                    # 1m idx -> last CLOSED 15m idx
    start_i: int = 0                   # first 1m index after warmup


# ======================================================================
def _last_swings(sw: np.ndarray, lookback: int) -> tuple[np.ndarray, np.ndarray]:
    """(last1, last2): most recent / second most recent confirmed swing value
    known at each bar, confined to a trailing `lookback` window.  Vectorized
    via searchsorted gathers; NaN when none exists."""
    n = len(sw)
    valid = ~np.isnan(sw)
    idx = np.flatnonzero(valid)
    last1 = np.full(n, np.nan)
    last2 = np.full(n, np.nan)
    if len(idx) == 0:
        return last1, last2
    pos = np.searchsorted(idx, np.arange(n), side="right") - 1
    at = np.where(pos >= 0, idx[np.clip(pos, 0, len(idx) - 1)], -1)
    ok1 = at >= 0
    j_ok = np.flatnonzero(ok1)
    k_ok = at[j_ok]
    win = (j_ok - k_ok) < lookback
    jj, kk = j_ok[win], k_ok[win]
    last1[jj] = sw[kk]
    k2 = at[np.maximum(k_ok - 1, 0)]
    valid2 = (k_ok > 0) & (k2 >= 0) & ((j_ok - k2) < lookback)
    last2[j_ok[valid2]] = sw[k2[valid2]]
    return last1, last2


def _recent_mask(flags: np.ndarray, window: int) -> np.ndarray:
    n = len(flags)
    idx = np.flatnonzero(flags)
    out = np.zeros(n, dtype=bool)
    if len(idx) == 0:
        return out
    pos = np.searchsorted(idx, np.arange(n), side="right") - 1
    valid = pos >= 0
    last = idx[np.clip(pos, 0, len(idx) - 1)]
    ok = valid & ((np.arange(n) - last) < window)
    out[ok] = True
    return out


def _volume_profile(hi: float, lo: float, bins: int, h: np.ndarray,
                    l: np.ndarray, v: np.ndarray, t: np.ndarray,
                    up_to_ms: int, window_bars: int,
                    va_pct: float) -> tuple[float, float, float, float]:
    """Volume profile over [lo, hi] using bars with open_time <= up_to_ms
    (at most window_bars of them) that overlap the range.  Each bar's volume
    is spread evenly across the bins its high-low covers.  Returns
    (poc_price, vah_price, val_price, total_volume) with the value area at
    va_pct of volume around the POC."""
    mask = (t <= up_to_ms)
    idx = np.flatnonzero(mask)
    if len(idx) == 0:
        return np.nan, np.nan, np.nan, 0.0
    idx = idx[-window_bars:]
    hh, ll, vv = h[idx], l[idx], v[idx]
    span = hi - lo
    if span <= 0 or len(idx) == 0:
        return np.nan, np.nan, np.nan, 0.0
    hist = np.zeros(bins)
    for k in range(len(idx)):
        b0 = max(0, int((max(ll[k], lo) - lo) / span * bins))
        b1 = min(bins - 1, int((min(hh[k], hi) - lo) / span * bins))
        if b1 < b0:
            continue
        hist[b0:b1 + 1] += vv[k] / (b1 - b0 + 1)
    total = float(hist.sum())
    if total <= 0:
        return np.nan, np.nan, np.nan, 0.0
    poc_bin = int(np.argmax(hist))
    acc = hist[poc_bin]
    lo_b = hi_b = poc_bin
    while acc < va_pct * total and (lo_b > 0 or hi_b < bins - 1):
        take_lo = lo_b > 0 and (hi_b >= bins - 1
                                or hist[lo_b - 1] >= hist[hi_b + 1])
        if take_lo:
            lo_b -= 1
            acc += hist[lo_b]
        elif hi_b < bins - 1:
            hi_b += 1
            acc += hist[hi_b]
        else:
            break
    price = lambda b: lo + (b + 0.5) / bins * span
    return price(poc_bin), price(hi_b), price(lo_b), total


def _build_tf(df: pd.DataFrame, cfg: Config, kind: str) -> TFData:
    tf = TFData(
        t=df["open_time"].to_numpy(dtype=np.int64),
        o=df["open"].to_numpy(dtype=float),
        h=df["high"].to_numpy(dtype=float),
        l=df["low"].to_numpy(dtype=float),
        c=df["close"].to_numpy(dtype=float),
        v=df["volume"].to_numpy(dtype=float))
    n = len(df)
    arm = 2 if kind == "15m" else 1
    sw_hi, sw_lo = swing_points(df, arm)
    tf.swing_hi = sw_hi.to_numpy(dtype=float)
    tf.swing_lo = sw_lo.to_numpy(dtype=float)
    tf.last_sh, _ = _last_swings(tf.swing_hi,
                                 cfg.strategy["internal_swing_lookback"]
                                 if kind == "15m" else 60)
    tf.last_sl, _ = _last_swings(tf.swing_lo,
                                 cfg.strategy["internal_swing_lookback"]
                                 if kind == "15m" else 60)
    tf.atr = atr(df, cfg.stop["atr_period"]).to_numpy(dtype=float)
    if kind == "1m":
        hl_spread = ((df["high"] - df["low"]) / df["close"].replace(0, np.nan)
                     * 10_000.0)
        tf.spread24h = hl_spread.rolling(1440, min_periods=300).median().to_numpy(dtype=float)
        # entry models
        (b_f, b_ce, b_hi, b_lo, s_f, s_ce, s_hi, s_lo) = fvg_state_np(
            tf.h, tf.l, cfg.strategy["fvg_max_age_bars"])
        tf.fvg_bull, tf.fvg_bull_ce = b_f, b_ce
        tf.fvg_bear, tf.fvg_bear_ce = s_f, s_ce
        (ob_b, ob_b_mt, ob_b_lo, ob_b_hi, ob_s, ob_s_mt, ob_s_lo, ob_s_hi) = \
            order_blocks_np(tf.o, tf.h, tf.l, tf.c, tf.swing_hi, tf.swing_lo,
                            max_age=cfg.strategy["ob_max_age_bars"])
        tf.ob_bull, tf.ob_bull_mt = ob_b, ob_b_mt
        tf.ob_bear, tf.ob_bear_mt = ob_s, ob_s_mt
        # micro POC of the current 1m leg (recomputed when a swing forms)
        tf.micro_poc = _micro_poc_series(tf, cfg)
    if kind == "15m":
        tf.sh1, tf.sh2 = _last_swings(tf.swing_hi, cfg.strategy["internal_swing_lookback"])
        tf.sl1, tf.sl2 = _last_swings(tf.swing_lo, cfg.strategy["internal_swing_lookback"])
        with np.errstate(invalid="ignore"):
            tf.struct_ok_up = ((tf.sh1 > tf.sh2) & (tf.sl1 > tf.sl2)).astype(bool)
            tf.struct_ok_dn = ((tf.sh1 < tf.sh2) & (tf.sl1 < tf.sl2)).astype(bool)
        # bias per closed bar
        bias = np.zeros(n, dtype=np.int8)
        choch = _choch_dir(tf)
        thr = cfg.strategy["internal_swing_threshold_atr"]
        for j in range(n):
            up = bool(tf.struct_ok_up[j])
            dn = bool(tf.struct_ok_dn[j])
            leg = abs(tf.last_sh[j] - tf.last_sl[j])
            oversized = (leg > thr * tf.atr[j]) if not np.isnan(tf.atr[j]) else False
            if up and not dn:
                bias[j] = 1 if not oversized else (choch[j] if choch[j] != 0 else 1)
            elif dn and not up:
                bias[j] = -1 if not oversized else (choch[j] if choch[j] != 0 else -1)
            else:
                bias[j] = choch[j]
        tf.bias_dir = bias
        # volume profile per 15m bar (recomputed when the swing range changes)
        (vah, poc, val) = _vp_series(tf, cfg)
        tf.vp_vah, tf.vp_poc, tf.vp_val = vah, poc, val
        # turtle-soup sweeps of the EQH/EQL pools (fresh-window mask)
        if "turtle" in cfg.strategies["enabled"]:
            from pa.fast import eq_levels_np, turtle_soup_np
            eqh, _, eql, _ = eq_levels_np(tf.swing_hi, tf.swing_lo,
                                          cfg.strategies["turtle"]["tol_pct"])
            ts_l = turtle_soup_np(tf.h, tf.l, tf.c, eql, "low",
                                  cfg.strategies["turtle"]["wick_frac"])
            ts_s = turtle_soup_np(tf.h, tf.l, tf.c, eqh, "high",
                                  cfg.strategies["turtle"]["wick_frac"])
            tf.ts_long = _recent_mask(ts_l, cfg.strategies["turtle"]["fresh_bars"])
            tf.ts_short = _recent_mask(ts_s, cfg.strategies["turtle"]["fresh_bars"])
        # asian range + sweep flags
        (ah, al, sup, sdn) = _asia_series(tf, cfg)
        tf.asia_high, tf.asia_low = ah, al
        tf.asia_swept_up, tf.asia_swept_dn = sup, sdn
        # volatility filter
        hist = max(1, int(cfg.volatility_filter["history_hours"] * 4))
        atr_s = pd.Series(tf.atr)
        m = atr_s.rolling(hist, min_periods=hist).mean()
        s = atr_s.rolling(hist, min_periods=hist).std()
        tf.atr_z = ((atr_s - m) / s.replace(0, np.nan)).to_numpy(dtype=float)
        tf.vol24h = df["volume"].rolling(96, min_periods=96).sum().to_numpy(dtype=float)
    return tf


def _choch_dir(tf: TFData) -> np.ndarray:
    """Last 15m ChoCH direction known at each bar (+1/-1/0)."""
    n = len(tf.c)
    out = np.zeros(n, dtype=np.int8)
    last = 0
    for i in range(n):
        if not np.isnan(tf.last_sh[i]) and tf.c[i] > tf.last_sh[i]:
            last = 1
        elif not np.isnan(tf.last_sl[i]) and tf.c[i] < tf.last_sl[i]:
            last = -1
        out[i] = last
    return out


def _micro_poc_series(tf: TFData, cfg: Config) -> np.ndarray:
    """POC of the current 1m leg (range = last 1m swing low..high), rebuilt
    only when a swing confirms; vectorized histogram per rebuild."""
    n = len(tf.c)
    out = np.full(n, np.nan)
    bins = cfg.strategy["vp_bins"]
    cur_lo = cur_hi = np.nan
    leg_lo = leg_hi = np.nan
    cached = np.nan
    for i in range(n):
        if not np.isnan(tf.swing_lo[i]):
            cur_lo = tf.swing_lo[i]
        if not np.isnan(tf.swing_hi[i]):
            cur_hi = tf.swing_hi[i]
        if not np.isnan(cur_lo) and not np.isnan(cur_hi) and cur_hi > cur_lo:
            if np.isnan(leg_lo) or cur_lo != leg_lo or cur_hi != leg_hi:
                leg_lo, leg_hi = cur_lo, cur_hi
                cached = _leg_poc(tf, i, leg_lo, leg_hi, bins)
            out[i] = cached
    return out


def _leg_poc(tf: TFData, i: int, lo: float, hi: float, bins: int) -> float:
    """POC of the leg [lo, hi] from bars at or before i (vectorized)."""
    span = hi - lo
    if span <= 0:
        return np.nan
    j = max(0, i - 90)
    h = tf.h[j:i + 1]
    l = tf.l[j:i + 1]
    v = tf.v[j:i + 1]
    keep = ~((h < lo) | (l > hi))
    h, l, v = h[keep], l[keep], v[keep]
    if len(h) == 0:
        return np.nan
    b0 = np.clip(((np.maximum(l, lo) - lo) / span * bins).astype(int), 0, bins - 1)
    b1 = np.clip(((np.minimum(h, hi) - lo) / span * bins).astype(int), 0, bins - 1)
    hist = np.zeros(bins)
    w = v / np.maximum(b1 - b0 + 1, 1)
    for k in range(len(h)):
        hist[b0[k]:b1[k] + 1] += w[k]
    if hist.sum() <= 0:
        return np.nan
    return lo + (int(np.argmax(hist)) + 0.5) / bins * span


def _vp_series(tf: TFData, cfg: Config) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """VAH / POC / VAL per 15m bar from the profile of the current swing
    range, computed from the 15m frame's own bars within the range (volume
    at the bias timeframe).  Rebuilt only when the swing range changes."""
    n = len(tf.c)
    vah = np.full(n, np.nan)
    poc = np.full(n, np.nan)
    val = np.full(n, np.nan)
    last_lo = last_hi = np.nan
    cached = None
    va_pct = cfg.strategy["value_area_pct"]
    for j in range(n):
        if not np.isnan(tf.last_sl[j]):
            last_lo = tf.last_sl[j]
        if not np.isnan(tf.last_sh[j]):
            last_hi = tf.last_sh[j]
        if np.isnan(last_lo) or np.isnan(last_hi) or last_hi <= last_lo:
            continue
        if cached is None or cached[0] != (last_lo, last_hi):
            p, vh, vl, _ = _volume_profile(
                last_hi, last_lo, cfg.strategy["vp_bins"], tf.h, tf.l, tf.v,
                tf.t, int(tf.t[j] + 15 * MIN_MS),
                cfg.strategy["vp_window_bars"], va_pct)
            cached = ((last_lo, last_hi), p, vh, vl)
        poc[j], vah[j], val[j] = cached[1], cached[2], cached[3]
    return vah, poc, val


def _asia_series(tf: TFData, cfg: Config):
    """Per-bar Asia range high/low (00:00-04:00 UTC) and cumulative sweep
    flags (has price traded beyond the bound since the window closed)."""
    n = len(tf.t)
    sh, eh = cfg.strategy["session"]["asia_hours_utc"]
    day = (tf.t - sh * HOUR_MS) // DAY_MS
    hours = ((tf.t // HOUR_MS) % 24).astype(int)
    in_asia = (hours >= sh) & (hours < eh)
    ah = np.full(n, np.nan)
    al = np.full(n, np.nan)
    sup = np.zeros(n, dtype=bool)
    sdn = np.zeros(n, dtype=bool)
    # running per-day asia max/min
    cur_day = None
    hi = lo = np.nan
    swept_up = swept_dn = False
    for i in range(n):
        if day[i] != cur_day:
            cur_day = day[i]
            hi = lo = np.nan
            swept_up = swept_dn = False
        if in_asia[i]:
            hi = tf.h[i] if np.isnan(hi) else max(hi, tf.h[i])
            lo = tf.l[i] if np.isnan(lo) else min(lo, tf.l[i])
        else:
            if not np.isnan(hi) and tf.h[i] > hi:
                swept_up = True
            if not np.isnan(lo) and tf.l[i] < lo:
                swept_dn = True
        if not np.isnan(hi):
            ah[i], al[i] = hi, lo
        sup[i], sdn[i] = swept_up, swept_dn
    return ah, al, sup, sdn


# ======================================================================
def prepare_symbol(sym: str, df1m: pd.DataFrame, cfg: Config) -> SymbolData:
    """Full causal preparation of one symbol (1m + 15m only)."""
    df1m = df1m.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    t1 = df1m["open_time"].to_numpy(dtype=np.int64)
    n = len(t1)
    d15 = resample_ohlcv(df1m, 15)
    tf1 = _build_tf(df1m, cfg, "1m")
    tf15 = _build_tf(d15, cfg, "15m")
    t15a = d15["open_time"].to_numpy(dtype=np.int64)
    i15 = np.searchsorted(t15a, t1 + MIN_MS - 15 * MIN_MS, side="right") - 1
    sd = SymbolData(sym=sym, df1m=df1m, tfs={"1m": tf1, "15m": tf15}, i15=i15)
    need = cfg.backtest["warmup_15m_bars"]
    warm = np.flatnonzero(i15 >= need - 1)
    sd.start_i = int(warm[0]) if len(warm) else n
    return sd


# ======================================================================
@dataclass
class PendingSignal:
    at_ms: int
    sym: str
    signal: EntrySignal


@dataclass
class Result:
    trades: list[dict]
    rejections: list[dict]
    equity_curve: list[tuple[int, float]]
    universes: dict[int, list[str]]
    params: dict
    start_ms: int
    end_ms: int
    seconds: float
    research_signals: list[dict] = field(default_factory=list)


def _in_window(hour: float, lo: float, hi: float) -> bool:
    if hi >= lo:
        return lo <= hour < hi
    return hour >= lo or hour < hi      # wraps midnight


def session_label(t_close_ms: int, cfg: Config) -> str:
    hour = (t_close_ms // HOUR_MS) % 24 + ((t_close_ms // MIN_MS) % 60) / 60.0
    for name, (lo, hi) in cfg.strategy["session"]["windows_utc"].items():
        if _in_window(hour, lo, hi):
            return name
    return "?"


class BacktestEngine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.entry = EntryEngine(cfg)
        self._strat_states: dict = {}

    def _session_label(self, t_close_ms: int) -> str:
        return session_label(t_close_ms, self.cfg)

    # ------------------------------------------------------------------
    def prepare(self, frames: dict[str, pd.DataFrame],
                start_ms: int, end_ms: int) -> dict[str, SymbolData]:
        out = {}
        for sym, df in sorted(frames.items()):
            df = df[(df["open_time"] >= start_ms) & (df["open_time"] <= end_ms)]
            if len(df) < 1500:
                continue
            try:
                out[sym] = prepare_symbol(sym, df, self.cfg)
            except Exception as e:
                log.warning("skip %s: prep failed: %s", sym, e)
        return out

    # ------------------------------------------------------------------
    def _daily_universe(self, sds: dict[str, SymbolData], boundary_ms: int) -> list[str]:
        cfg = self.cfg
        rows = []
        for sym, sd in sds.items():
            i = int(np.searchsorted(sd.tfs["1m"].t, boundary_ms, side="right") - 1)
            if i < 0 or i < sd.start_i:
                continue
            j = int(sd.i15[i])
            tf15 = sd.tfs["15m"]
            tf1 = sd.tfs["1m"]
            if j < 5 or np.isnan(tf15.atr[j]):
                continue
            if np.isnan(tf15.vol24h[j]) or np.isnan(tf1.spread24h[i]):
                continue
            vol = float(tf15.vol24h[j])
            if vol < cfg.universe["min_volume_usdt_24h"]:
                continue
            spread = float(tf1.spread24h[i])
            if spread > cfg.universe["max_spread_bps"]:
                continue
            atr_pct = float(tf15.atr[j] / tf15.c[j] * 100.0)
            if atr_pct < cfg.universe["min_atr_pct"]:
                continue
            trend = 0.0
            if tf15.sh1[j] and tf15.sl1[j] and not np.isnan(tf15.sh1[j]) and not np.isnan(tf15.sl1[j]):
                trend = abs(float(tf15.sh1[j] / tf15.sl1[j] - 1.0))
            rows.append({"symbol": sym, "volume": vol, "spread": spread,
                         "liquidity": vol, "vola": atr_pct, "trend": trend})
        if not rows:
            return []
        out = _rank_rows(pd.DataFrame(rows), cfg.universe["rank_weights"],
                         cfg.universe["volatility_cap_atr_pct"])
        return out["symbol"].head(cfg.universe["top_n"]).tolist()

    # ------------------------------------------------------------------
    def run(self, sds: dict[str, SymbolData], start_ms: int, end_ms: int,
            starting_equity: float | None = None,
            research_mode: bool = False) -> Result:
        cfg = self.cfg
        t0 = time.time()
        day_hour = cfg.daily["day_start_hour_utc"]
        risk = RiskManager(cfg, starting_equity
                           if starting_equity is not None
                           else cfg.paper["starting_equity"], day_hour)

        syms = sorted(sds)
        timelines = [sds[s].tfs["1m"].t[sds[s].start_i:] for s in syms]
        timelines = [tl for tl in timelines if len(tl)]
        if not timelines:
            raise ValueError("no symbol has enough history past the warmup window")
        timeline = np.unique(np.concatenate(timelines))
        per_idx: dict[str, np.ndarray] = {}
        for s in syms:
            tt = sds[s].tfs["1m"].t
            pos = np.searchsorted(tt, timeline)
            valid = pos < len(tt)
            idx = np.where(valid & (tt[np.minimum(pos, len(tt) - 1)] == timeline),
                           pos, -1).astype(np.int32)
            per_idx[s] = idx

        positions: dict[str, Position] = {}
        pending: list[PendingSignal] = []
        trades: list[dict] = []
        rejections: list[dict] = []
        research_signals: list[dict] = []
        equity_curve: list[tuple[int, float]] = []
        universes: dict[int, list[str]] = {}
        universe: list[str] = []
        last_day = -1
        pos_counter = 0

        def cur_universe(ms: int) -> list[str]:
            nonlocal universe, last_day
            day = ms // DAY_MS
            if day != last_day:
                universe = self._daily_universe(sds, day * DAY_MS)
                universes[day * DAY_MS] = list(universe)
                last_day = day
            return universe

        def reject_log(sym: str, i: int, rej: list[dict], why: str = "") -> None:
            sd = sds[sym]
            t1 = sd.tfs["1m"]
            if rej:
                passed = rej[-1].get("passed_legs", 1)
            else:
                passed = 1
            if passed < cfg.research["log_reject_min_legs"]:
                return
            rejections.append({
                "ts_ms": int(t1.t[i] + MIN_MS), "symbol": sym,
                "rejections": rej, "why": why,
                "close": float(t1.c[i]), "passed_legs": passed})

        for ti, t in enumerate(timeline):
            t_close = int(t + MIN_MS)
            cur_universe(t_close)
            # ---------------- fills: market at this bar's open, limits
            # ---------------- on this bar's range (model-level entries) --
            if pending:
                pending.sort(key=lambda p: (p.at_ms, p.sym))
                still = []
                for p in pending:
                    sd = sds[p.sym]
                    i = per_idx[p.sym][ti]
                    if i < 0:
                        still.append(p)
                        continue
                    t1 = sd.tfs["1m"]
                    sig = p.signal
                    if sig.entry_level is not None:
                        # resting limit at the entry model's level
                        if i - sig.bar > sig.entry_expiry_bars:
                            reject_log(p.sym, i, [], "limit expired (no fill)")
                            continue
                        lvl = float(sig.entry_level)
                        if not (t1.l[i] <= lvl <= t1.h[i]):
                            still.append(p)          # keep resting
                            continue
                        fill = lvl                    # limits fill at the level
                        # a resting limit that fills is a MAKER fill: it pays
                        # the maker rate and eats no slippage
                        fee_frac = cfg.execution["maker_fee_bps"] / 1e4
                        is_maker_entry = True
                    else:
                        # market: strictly the next bar's open
                        if i != sig.bar + 1:
                            continue
                        open_px = float(t1.o[i])
                        fill, fee_frac = entry_price(open_px, sig.direction,
                                                     cfg.execution["fee_bps"],
                                                     cfg.execution["slippage_bps"])
                        is_maker_entry = False
                    ok, why = risk.can_enter(t_close)
                    if not ok:
                        reject_log(p.sym, i, [], f"risk gate: {why}")
                        continue
                    if len(positions) >= cfg.risk["max_positions"]:
                        reject_log(p.sym, i, [], "max_positions")
                        continue
                    if p.sym in positions:
                        reject_log(p.sym, i, [], "already in position")
                        continue
                    d = sig.direction
                    atr1m = sig.atr1m
                    buf = cfg.stop["atr_buffer_mult"] * atr1m
                    sl = sig.swing_level - d * buf
                    if not np.isfinite(sl) or not np.isfinite(fill):
                        reject_log(p.sym, i, [], "non-finite fill/sl")
                        continue
                    # the stop must sit on the LOSING side of the fill; a
                    # stale signal gapped beyond its own stop is a reject,
                    # never a phantom fill
                    if (d == LONG and sl >= fill) or (d == SHORT and sl <= fill):
                        reject_log(p.sym, i, [], "sl wrong side (stale signal)")
                        continue
                    dist = abs(fill - sl)
                    is_breakout = getattr(p.signal, "strategy", "") == "breakout"
                    too_wide = (dist / fill * 100.0 > cfg.stop["max_sl_pct"]
                                or (atr1m > 0
                                    and dist / atr1m > cfg.stop["max_sl_atr_mult"]))
                    if dist <= 0 or (too_wide and not is_breakout):
                        reject_log(p.sym, i, [], "sl_too_wide")
                        continue
                    # knife-catch guard: a stop tighter than min_sl_atr_mult
                    # x the 1m ATR is one noise bar from death -- and its
                    # round trip is friction-dominated.  Reject it.
                    min_sl = cfg.stop.get("min_sl_atr_mult", 0.0)
                    if min_sl > 0 and atr1m > 0 and not is_breakout \
                            and dist / atr1m < min_sl:
                        reject_log(p.sym, i, [], "sl_too_tight (knife)")
                        continue
                    # volume-delta edge (2026 order-flow research): the last
                    # N bars' signed volume must AGREE with the direction --
                    # a long into selling pressure loses more often.
                    cvd_cfg = cfg.strategy.get("cvd_filter") or {}
                    if cvd_cfg.get("enabled") and i >= 2:
                        L = int(cvd_cfg.get("lookback", 60))
                        slc = slice(max(0, i - L + 1), i + 1)
                        vseg = t1.v[slc]
                        if vseg.sum() > 0:
                            delta = float((np.sign(t1.c[slc] - t1.o[slc])
                                           * vseg).sum() / vseg.sum())
                            want = float(cvd_cfg.get("min_abs", 0.05))
                            if ((d == LONG and delta < want)
                                    or (d == SHORT and delta > -want)):
                                reject_log(p.sym, i, [],
                                           f"cvd disagrees ({delta:+.2f})")
                                continue

                    # ECA piece (Elements of Cycle Analysis, web): entries
                    # only in the EARLY phase of the dominant swing cycle,
                    # and only on an ATR-expansion bar -- late-cycle chasing
                    # and flat bars are where the losses cluster.
                    cyc = cfg.strategy.get("cycle_filter") or {}
                    if cyc.get("enabled") and i >= 20:
                        conf = np.flatnonzero(~np.isnan(t1.swing_hi)
                                              | ~np.isnan(t1.swing_lo))
                        conf = conf[conf < i]
                        if len(conf) >= 3:
                            gaps = np.diff(conf[-31:])
                            if len(gaps) >= 2:
                                gap = float(np.median(gaps[-10:]))
                                if gap > 0:
                                    phase = (i - int(conf[-1])) / gap
                                    if phase > float(cyc.get("max_phase", 0.4)):
                                        reject_log(p.sym, i, [],
                                                   f"late cycle phase {phase:.2f}")
                                        continue
                        want_x = float(cyc.get("atr_expand_min", 0.0))
                        if want_x > 0 and t1.atr[i] and t1.atr[i] > 0 \
                                and (t1.h[i] - t1.l[i]) / t1.atr[i] < want_x:
                            reject_log(p.sym, i, [], "no atr expansion")
                            continue

                    # MSS piece (ICT): a ChoCH without displacement is a
                    # trap -- the trigger bar body must move the market.
                    if cfg.strategy.get("require_displacement"):
                        b = int(getattr(sig, "bar", i))
                        if 0 <= b < len(t1.c) and t1.atr[b] and t1.atr[b] > 0:
                            body = abs(float(t1.c[b]) - float(t1.o[b]))
                            if body < 1.5 * t1.atr[b]:
                                reject_log(p.sym, i, [], "no displacement")
                                continue

                    # pattern piece (motivewave library): the trigger bar
                    # must itself agree with the direction (hammer /
                    # marubozu family), not just the levels.
                    pc = cfg.strategy.get("pattern_confirm") or {}
                    if pc.get("enabled") and 0 <= i < len(t1.c):
                        o_, h_, l_, c_ = (float(t1.o[i]), float(t1.h[i]),
                                          float(t1.l[i]), float(t1.c[i]))
                        rng = h_ - l_
                        body = abs(c_ - o_)
                        wick = float(pc.get("min_wick_ratio", 2.0))
                        if rng > 0:
                            ok = False
                            if d == LONG and c_ > o_:
                                ok = (min(o_, c_) - l_ >= wick * body
                                      and body > 0) or body >= 0.8 * rng
                            elif d == SHORT and c_ < o_:
                                ok = (h_ - max(o_, c_) >= wick * body
                                      and body > 0) or body >= 0.8 * rng
                            if not ok:
                                reject_log(p.sym, i, [],
                                           "trigger bar pattern disagrees")
                                continue
                    # fee viability: a stop so tight that the round trip
                    # costs more than max_fee_r of the risk budget is a
                    # negative-sum trade before any edge exists.
                    fee_r = round_trip_cost_r(
                        dist / fill,
                        cfg.execution["fee_bps"],
                        cfg.execution["slippage_bps"],
                        entry_fee_bps=fee_frac * 1e4,
                        entry_slippage=not is_maker_entry)
                    import os as _os
                    if _os.environ.get("TBT_DEBUG_FEE"):
                        print(f"DEBUG fee_r={fee_r:.3f} bound={cfg.execution['max_fee_r']} "
                              f"dist_pct={dist/fill*100:.4f} strat={getattr(sig,'strategy','')}", flush=True)
                    if fee_r > cfg.execution["max_fee_r"]:
                        reject_log(p.sym, i, [], "fee_gt_edge")
                        continue
                    size = risk.size(fill, sl)
                    # brain gate (R&D backtest mode): the trained
                    # win-probability model vetoes low-probability entries
                    # inside the backtest, exactly like the live book
                    bgate = cfg.raw().get("brain") or {}
                    if bgate.get("backtest_gate") and bgate.get("enabled"):
                        try:
                            from brain import Brain, features_live, kind_of
                            self._brain = getattr(self, "_brain", None)
                            if self._brain is None:
                                from pathlib import Path as _P
                                self._brain = Brain(
                                    _P("data"),
                                    {"min_train_trades": 100,
                                     "veto_prob": float(bgate.get("veto_prob", 0.5)),
                                     "min_keep_frac": 1.0})
                            dfk = sds[p.sym].df1m
                            k = kind_of(p.sym,
                                        {p.sym: dfk},
                                        int(sig.at_ms))
                            feats = features_live(
                                p.sym, d, getattr(sig, "strategy", ""),
                                sig.entry_model, fill, sl, sig.tp_first,
                                sig.atr1m, size["leverage"],
                                int(sig.at_ms), k, cfg, df=dfk)
                            prob = self._brain.win_prob(feats)
                            if prob is not None \
                                    and prob < float(bgate.get("veto_prob", 0.5)):
                                reject_log(p.sym, i, [],
                                           f"brain veto {prob:.2f}")
                                continue
                        except Exception as e:
                            reject_log(p.sym, i, [],
                                       f"brain gate err {str(e)[:40]}")
                    if size["rejected"] or size["qty"] <= 0:
                        reject_log(p.sym, i, [], f"size: {size['rejected']}")
                        continue
                    if size["notional"] < cfg.risk["min_order_notional_usdt"]:
                        reject_log(p.sym, i, [], "notional_too_small")
                        continue
                    entry_fee = size["notional"] * fee_frac
                    meta = {
                        "signal_bar_ms": sig.at_ms,
                        "entry_ref": sig.entry_price,
                        "swing_level": sig.swing_level,
                        "entry_model": sig.entry_model,
                        "strategy": getattr(sig, "strategy", ""),
                        "bias": sig.bias,
                        "vp_poc": sig.vp_poc,
                        "atr1m": atr1m,
                        "risk_pct": cfg.risk["risk_per_trade"],
                        "leverage": size["leverage"],
                        "risk_amount": size["risk_amount"],
                    }
                    pos_counter += 1
                    pos = build_position(p.sym, d, fill, size["qty"], sl, cfg,
                                         meta, entry_fee, t_close, atr1m,
                                         tp_first=sig.tp_first)
                    pos.pos_id = pos_counter
                    positions[p.sym] = pos
                    risk.on_fill(t_close, entry_fee)
                pending = still

            # ---------------- manage open positions with this bar --------
            for sym in list(positions):
                pos = positions[sym]
                i = per_idx[sym][ti]
                if i < 0:
                    continue
                sd = sds[sym]
                t1 = sd.tfs["1m"]
                closed_now = execute_struct_exit(
                    pos, float(t1.o[i]), t_close, cfg.execution["fee_bps"],
                    cfg.execution["slippage_bps"])
                d = pos.direction
                last_sw_lo = t1.last_sl[i] if not np.isnan(t1.last_sl[i]) else None
                last_sw_hi = t1.last_sh[i] if not np.isnan(t1.last_sh[i]) else None
                opp = opposite_bos(t1, i, d)
                closed_now += step_position(
                    pos, cfg, float(t1.h[i]), float(t1.l[i]), t_close,
                    last_sw_lo, last_sw_hi, float(t1.atr[i]), opp,
                    cfg.execution["fee_bps"], cfg.execution["slippage_bps"])
                for lot_rec in closed_now:
                    trades.append(self._trade_record(sym, pos, lot_rec))
                    pos.realized_pnl += lot_rec["pnl"]
                if not pos.open:
                    risk.on_close(pos.realized_pnl, t_close)
                    del positions[sym]

            # ---------------- signals at this bar's close ----------------
            for sym in syms:
                i = per_idx[sym][ti]
                if i < 0 or i < sds[sym].start_i:
                    continue
                sd = sds[sym]
                t1 = sd.tfs["1m"]
                j15 = sd.i15[i]
                if j15 < 5:
                    continue
                tf15 = sd.tfs["15m"]
                # NOTE: the 15m VP bias gate used to live here, silently
                # starving every non-VP family (impulse, breakout, turtle)
                # that does not need a VP bias.  The VP strategy re-checks
                # its own bias internally (leg 1), so no signal is lost.
                # volatility filter
                if cfg.volatility_filter["enabled"] and not np.isnan(tf15.atr_z[j15]) \
                        and tf15.atr_z[j15] > cfg.volatility_filter["atr_zscore_max"]:
                    continue
                # session window gate (operator's Tehran map, UTC)
                sess = self._session_label(t_close)
                if "any" not in cfg.strategy["session"]["trade_windows"] \
                        and sess not in cfg.strategy["session"]["trade_windows"]:
                    continue
                sigs, rej = self.entry.on_bar(sd, i, tf15, j15, t_close,
                                               self._strat_states)
                if rej:
                    reject_log(sym, i, rej)
                if not sigs:
                    continue
                sig = sigs[0]
                # universe membership
                if sym not in universe:
                    reject_log(sym, i, [], "not in universe")
                    continue
                # correlation gate (only when several positions are allowed)
                if len(positions) >= 1 and cfg.risk["max_positions"] > 1:
                    if self._correlated(sds, sym, positions, i):
                        reject_log(sym, i, [], "correlated exposure")
                        continue
                if research_mode:
                    research_signals.append({
                        "symbol": sym, "bar": i, "at_ms": t_close,
                        "direction": "LONG" if sig.direction == LONG else "SHORT",
                        "entry_ref": sig.entry_price,
                        "swing_level": sig.swing_level,
                        "entry_model": sig.entry_model,
                        "bias": sig.bias, "vp_poc": sig.vp_poc,
                        "atr1m": sig.atr1m,
                    })
                    continue
                pending.append(PendingSignal(at_ms=t_close, sym=sym, signal=sig))

            # ---------------- equity marking -----------------------------
            marked = risk.equity
            for pos in positions.values():
                i = per_idx[pos.symbol][ti]
                if i >= 0:
                    marked += pos.unrealized(float(sds[pos.symbol].tfs["1m"].c[i]))
            equity_curve.append((t_close, marked))

        return Result(trades=trades, rejections=rejections,
                      equity_curve=equity_curve, universes=universes,
                      research_signals=research_signals,
                      params=self.cfg.raw(),
                      start_ms=start_ms, end_ms=end_ms,
                      seconds=time.time() - t0)

    # ------------------------------------------------------------------
    def _correlated(self, sds, sym: str, positions: dict, i: int) -> bool:
        cfg = self.cfg
        w = cfg.risk["corr_window_bars"]
        a = sds[sym].tfs["1m"]
        xa = a.c[max(0, i - w + 1): i + 1]
        for pos in positions.values():
            b = sds[pos.symbol].tfs["1m"]
            j = int(np.searchsorted(b.t, a.t[i] + MIN_MS, side="right") - 1)
            xb = b.c[max(0, j - w + 1): j + 1]
            n = min(len(xa), len(xb))
            if n < 30:
                continue
            ra = np.diff(np.log(xa[-n:]))
            rb = np.diff(np.log(xb[-n:]))
            if np.std(ra) == 0 or np.std(rb) == 0:
                continue
            corr = float(np.corrcoef(ra, rb)[0, 1])
            if corr > cfg.risk["max_corr"]:
                return True
        return False

    # ------------------------------------------------------------------
    def _trade_record(self, sym: str, pos: Position, lot_rec: dict) -> dict:
        cfg = self.cfg
        entry_px = lot_rec["entry"]
        exit_px = lot_rec["exit"]
        sl = lot_rec["sl"]
        r = abs(entry_px - sl)
        pnl_r = lot_rec["pnl"] / (r * lot_rec["qty"]) if r * lot_rec["qty"] else 0.0
        meta = pos.meta
        return {
            "pos_id": pos.pos_id,
            "ts_ms": lot_rec["exit_ms"],
            "opened_ms": pos.opened_ms,
            "symbol": sym,
            "direction": "LONG" if pos.direction == LONG else "SHORT",
            "lot": lot_rec["lot"],
            "bias": meta.get("bias"),
            "entry_model": meta.get("entry_model"),
            "strategy": meta.get("strategy"),
            "vp_poc": meta.get("vp_poc"),
            "swing_level": meta.get("swing_level"),
            "entry": entry_px,
            "stop": sl,
            "tp": lot_rec["tp"],
            "exit": exit_px,
            "qty": lot_rec["qty"],
            "notional": lot_rec["qty"] * entry_px,
            "risk_amount": meta.get("risk_amount"),
            "leverage": meta.get("leverage"),
            "fee_bps": cfg.execution["fee_bps"],
            "slippage_bps": cfg.execution["slippage_bps"],
            "entry_fee": lot_rec["qty"] * entry_px * cfg.execution["fee_bps"] / 1e4,
            "exit_fee": lot_rec["exit_fee"],
            "atr1m": meta.get("atr1m"),
            "pnl": lot_rec["pnl"],
            "pnl_r": pnl_r,
            "entry_reason": "bias+vp_level+choch+entry_model",
            "exit_reason": lot_rec["reason"],
            "mae_r": lot_rec.get("mae_r", 0.0),
            "mfe_r": lot_rec.get("mfe_r", 0.0),
        }
