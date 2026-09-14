#!/usr/bin/env python3
"""
live_hyperliquid.py — ICT SNIPER, Hyperliquid paper-trading bot.

Phase-7 production spec (the first entry-level-positive configuration from
the seven-phase quant audit of scalper/strategies/ict_sniper.py):

  Market        : top-30 volatile perps by ATR% (atrscan.py doctrine:
                  ATR% = mean true range over 14 x 15m candles / price,
                  floor 0.8%, 24h notional volume floor $1M; refreshed
                  hourly; volume is a FLOOR, never a ranking term).
  Timeframe     : 5m.
  Entry         : passive LIMIT at the FVG Consequent Encroachment after
                  a liquidity sweep (wick >= 45% of range) -> displacement
                  MSS -> fresh FVG (close-based mitigation).
  Initial SL    : sweep-wick extreme - 0.25 x ATR14(5m)  (the WIDE stop;
                  phases 4-6 proved deep-retest survival is the edge).
  TP1 scale-out : +120 bps limit for 75% of the position (maker fill).
  Dynamic BE    : pre-scale: once MFE >= 0.75R, SL moves to entry;
                  exactly when TP1 fills, the runner's SL goes to
                  breakeven.
  TP2 runner    : +400 bps limit for the remaining 25%.
  Time kill     : market-close the remainder after 80 bars (~6.7h).
  Hit & Run     : halt a symbol for the UTC day after +300 bps realized,
                  and after 2 consecutive losing legs.
  RR gate       : TP2/SL >= 2.5 strictly, SL <= 160 bps.

PAPER MODE (default): a READ-ONLY wallet address is passed; nothing is
signed and nothing is sent.  The full order state machine runs against
live Hyperliquid candles/mids, fills are simulated with the same
candle-based trade-through rules as the backtest, and every order the bot
would place is logged.  PnL is tracked with the VIP venue assumption
(maker entry 0 bps, maker exits 1.5 bps, taker 3.0 bps, stop slippage
1.5 bps) -- the cost tier under which the strategy measured net-positive.

LIVE MODE (opt-in): --live --secret-key ... ; orders are placed via the
official SDK (hyperliquid-python-sdk) and the userFills WebSocket drives
the real-fill state machine instead of candle-simulated fills.

Install / run (Ubuntu 24.04):
    pip install hyperliquid-python-sdk
    python3 live_hyperliquid.py --address 0xYOUR_READ_ONLY_WALLET
    python3 live_hyperliquid.py --smoke --address 0x...   # one-shot check
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import queue
import signal
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple

from hyperliquid.info import Info
from hyperliquid.utils import constants as hl_constants

LOG = logging.getLogger("ict_sniper_hl")

DAY_MS = 86_400_000
MIN_MS = 60_000
BASE_DIR = Path(__file__).resolve().parent
# must be set in the environment for --live to do anything at all
LIVE_ACK_PHRASE = "I_UNDERSTAND_REAL_MONEY"


# ----------------------------------------------------------------------
# Phase-7 configuration (the audited production spec).
# ----------------------------------------------------------------------
@dataclass
class Config:
    # ---- detection / execution -------------------------------------------
    # v4: MACRO MOMENTUM DAY TRADER.  The hunting TF is 15m, aligned to the
    # 1h trend; stops are structural and targets are macro (multi-percent).
    # The phase-7 micro core is preserved and still selectable via
    # detector="phase7" (A/B or rollback).
    detector: str = "macro"         # "macro" (v4) | "phase7" (micro core)
                                    # | "macro_shadow": phase-7 keeps
                                    #   trading while the macro engine logs
                                    #   what it WOULD do (evidence first)
    tf_min: int = 15                # execution timeframe (15m)
    arm: int = 2                    # phase-7 swing fractal arms
    swing_fresh_bars: int = 30
    sweep_max_age: int = 12
    mss_max_gap: int = 6
    wick_frac: float = 0.45
    disp_body_mult: float = 1.5
    disp_body_range: float = 0.70
    disp_opp_wick: float = 0.20
    disp_avg_window: int = 20
    fvg_max_age: int = 30
    atr_period: int = 14
    sl_buffer_atr: float = 0.25
    rr_min: float = 2.0
    max_sl_bps: float = 350.0       # macro stops are wide (structural)
    min_sl_bps: float = 40.0        # micro noise stops refused outright
    # ---- v4 macro-momentum structure (analyze_macro) ---------------------
    macro_arm: int = 3              # fractal arms for the 15m structure
    macro_mss_max_age: int = 30     # MSS must be this fresh (15m bars)
    macro_min_leg_atr: float = 4.0  # sweep best: only monster impulses
    macro_retr_min: float = 0.10    # pullback depth vs the impulse leg
    macro_retr_max: float = 0.65    # deeper = structure likely broken
    macro_sl_buffer_atr: float = 0.60   # sweep best: wide structural stop
    macro_disp_body_mult: float = 1.3   # displacement on the MSS bar
    macro_disp_body_range: float = 0.55
    macro_tp1_atr: float = 2.5      # sweep best: TP1 ~ 2.5 x ATR(15m)
    macro_tp1_min_r: float = 0.75   # ... but never closer than 0.75R
    macro_tp2_atr: float = 6.0      # TP2 ceiling (measured move applies)
    macro_tp2_min_r: float = 2.0    # ... and never below 2R
    macro_entry_mode: str = "momentum"  # sweep best: trade WITH the move
                                       # or "momentum" (stop-entry on the
                                       # resumption, trading WITH the move)
    macro_require_flip: bool = False   # pullback must HOLD the broken level
    macro_max_signals_day: int = 3  # supreme setups per asset per day
    macro_cooldown_bars: int = 8    # 2h between attempts on one asset
    trail_pivot_bars: int = 3       # runner trail rides an N-bar pivot, not
                                    # a single candle (breathing room)
    trail_min_atr: float = 1.0      # ... and never closer than N x ATR
    tp1_bps: float = 120.0          # phase-7 fixed targets (legacy mode)
    tp1_frac: float = 0.40          # v4 tri-tier: 40% at TP1
    tp2_frac: float = 0.30          # v4 tri-tier: 30% at TP2
    tp2_bps: float = 400.0          # the final 30% rides the macro trail
    be_after_r: float = 1.0         # pre-scale breakeven after 1R
    time_exit_bars: int = 40        # 40 x 15m = 10h (a day-trade horizon)
    scratch_after_bars: int = 3     # no-follow-through: if an entry never
                                    # shows follow-through within this many
                                    # closed bars, cut it early
    scratch_min_mfe_r: float = 0.5  # ... where "follow-through" = this much
                                    # favorable excursion (R) at least once
    retest_bars: int = 8
    # ---- universe (coin finder) ----------------------------------------
    top_n: int = 70                 # hunting ground: 60-80 coins (v2)
    min_atr_pct: float = 0.8        # QUALITY gate -- untouched (backtested)
    min_vol_usdt: float = 300_000.0  # deeper liquidity pool; still liquid
    universe_refresh_s: int = 1800  # dynamic re-rank every 30 min
    scan_candidates: int = 100      # volume-prefiltered pool to ATR-scan
    # ---- money management ----------------------------------------------
    paper_equity: float = 10_000.0
    alloc: float = 0.5
    lev: float = 20.0
    min_notional: float = 10.0
    # ---- venue costs (VIP tier; the binding condition of the strategy) --
    maker_entry_bps: float = 0.0
    maker_exit_bps: float = 1.5
    taker_bps: float = 3.0
    slip_bps: float = 1.5
    # ---- circuit breakers ----------------------------------------------
    cap_bps: float = 300.0          # hit & run: halt symbol for the day
    breaker_losses: int = 2         # consecutive losing legs -> halt
    # ---- engine ---------------------------------------------------------
    history_bars: int = 800         # 800 x 15m ~= 8 days of structure
    candle_poll_s: float = 15.0
    status_s: float = 60.0
    live: bool = False              # place real orders (needs --secret-key)
    paper_only: bool = False        # ICT_PAPER_ONLY / PAPER_ONLY file lock:
                                    # refuses live even if flags are passed
    # ---- risk & execution modules (wrap the phase-7 core) ---------------
    daily_target_pct: float = 100.0   # halt the day once equity = 2x start
    goal_win_frac: float = 0.25       # legacy v1 goal term (superseded by
                                      # the v2 front-loaded ladder below)
    risk_pct_per_stop: float = 0.10   # taper/secure per-stop loss budget
    risk_early_pct: float = 0.20      # v2 front-load: per-stop budget while
                                      # the day is young and above its open
    max_lev: float = 50.0             # absolute leverage ceiling (no revenge)
    min_lev: float = 5.0
    taper_at_pct: float = 0.70        # >= this progress: taper zone
    taper_lev: float = 10.0           # leverage ceiling in the taper zone
    secure_lev: float = 5.0           # leverage once >= secure_at of target
    secure_at_pct: float = 0.80
    max_stop_loss_pct: float = 0.20   # hard ratchet: no stop costs more
                                      # than this of equity, whatever else
    max_daily_dd_pct: float = 25.0    # day halt if equity drops this far
                                      # below the day's open (no revenge)
    max_positions: int = 2            # concurrent positions
    max_same_side: int = 2            # no more than N longs/shorts at once
    chop_enabled: bool = True         # regime filter (standby on dead tape)
    chop_look: int = 12
    chop_norm: int = 96
    chop_tr_ratio: float = 0.55       # recent TR / norm below this = dead
    chop_vol_ratio: float = 0.70
    velocity_enabled: bool = True     # runner trail on fast TP1
    velocity_bars: int = 3
    velocity_body_atr: float = 1.8    # sum of bodies >= N x ATR
    velocity_cancel_tp2: bool = False  # legacy micro rule: a fast TP1 used
                                      # to cancel TP2.  For the macro day
                                      # trader this is OFF -- tiers bank,
                                      # the runner trails ON TOP
    news_blackout_enabled: bool = True
    news_blackout_utc: tuple = ((13 * 60 + 25, 13 * 60 + 40),  # US data
                                (14 * 60 + 25, 14 * 60 + 40))  # releases,
                                # both DSTs: +/-15m around 13:30/14:30 UTC
    news_calendar_url: str = ""       # optional feed; "" = static window only
    news_lead_min: int = 10           # block +/-N min around high impact
    # ---- v3 module 1: HTF trend bias (no counter-trend fading) -----------
    htf_filter_enabled: bool = True
    htf_minutes: int = 60             # macro TF built from the 5m series
    htf_ema: int = 20                 # EMA period on HTF closes
    htf_slope_bars: int = 3           # EMA slope measured over N HTF bars
    # ---- v3 module 2: volume/delta confirmation at FVG mitigation --------
    vol_confirm_enabled: bool = True
    vol_look: int = 20                # baseline window for the trigger bar
    vol_mult: float = 1.2             # volume >= mult x its own average
    vol_range_mult: float = 1.3       # OR range >= mult x its average
    # ---- v3 module 4: session / liquidity timing (v4 macro windows) -----
    session_filter_enabled: bool = True
    # stand down after the NY close (20:15) until the London open
    session_dead_utc: tuple = ((20 * 60 + 15, 24 * 60), (0, 6 * 60))
    # prime: London open 06:00-10:00 and the FULL NY session 12:00-20:00
    session_prime_utc: tuple = ((6 * 60, 10 * 60), (12 * 60, 20 * 60))
    prime_vol_relax: float = 0.8      # prime hours: easier vol confirmation
    prime_warmup_min: int = 30        # slot hygiene: no NEW entries in the
                                      # N minutes before a prime window so
                                      # the book has free slots at the open
    session_strict: bool = True      # entries ONLY in prime windows; normal
                                      # hours need a monster structure to
                                      # justify the risk (see below)
    flat_enabled: bool = True        # day-trader discipline: EVERYTHING is
                                      # closed at market once this UTC time is
                                      # reached -- nothing rides into the
                                      # dead Asian night
    flat_by_hm: tuple = (20, 15)     # (hour, minute) UTC = 23:45 Tehran
                                      # (15 min after the NY close)
    macro_normal_min_leg: float = 4.0  # outside prime: impulse leg must be
                                      # >= this x ATR to even be considered
    # --- Benz Mode: once half the daily target is banked, only monster
    # setups qualify -- one more of them finishes the day, so Paykan-class
    # trades are refused outright
    monster_mode_at_pct: float = 0.50  # progress >= this -> monster only
    monster_min_leg: float = 4.0       # impulse leg >= N x ATR
    monster_min_tp2_pct: float = 0.25  # full-TP2 potential >= N of equity
    # ---- app + alerts (the phone app reads these files) -----------------
    data_pace_s: float = 0.45       # pause between candle requests (the
                                    # venue rate-limits bursts hard)
    live_publish_s: float = 3.0     # while a position is open, push the
                                    # mids-driven snapshot this often so the
                                    # phone card's PnL ticks like an exchange
    data_dir: str = ""              # SCALPER_DATA / --data-dir
    ntfy_topic: str = ""            # NTFY_TOPIC / --ntfy-topic
    notify: bool = True             # push to ntfy.sh
    publish_state: bool = True      # write paper.json/live.json for the app


def bps(x: float) -> float:
    return x / 10_000.0


def round_sz(sz: float, decimals: int) -> float:
    """Hyperliquid sizes are multiples of 10^-szDecimals; floor so we
    never overshoot margin."""
    step = 10.0 ** -decimals
    return math.floor(sz / step) * step


def round_px(px: float) -> float:
    """Hyperliquid limit prices: 5 significant digits."""
    if px <= 0:
        return 0.0
    return float(f"{px:.5g}")


def day_key(t_ms: int) -> int:
    return int(t_ms // DAY_MS) * DAY_MS


# ----------------------------------------------------------------------
# Phase-7 detector, ported faithfully from scalper/pa/sniper.py
# (pure python over candle lists; every value at bar i uses bars <= i).
# ----------------------------------------------------------------------
def analyze_bars(bars: List[dict], cfg: Config) -> dict:
    """Run the sweep -> displacement/MSS -> FVG chain over the tail.
    Returns the NEWEST bar's setup: {valid, side, entry, sl, sl_bps,
    tp1, tp2, be_trigger, sweep_bar_t} ({} when no valid setup)."""
    n = min(len(bars), cfg.history_bars)
    if n < cfg.disp_avg_window + 6:
        return {}
    tail = bars[-n:]
    o = [float(b["o"]) for b in tail]
    h = [float(b["h"]) for b in tail]
    lo = [float(b["l"]) for b in tail]
    c = [float(b["c"]) for b in tail]

    # ---- confirmed swings (value indexed by CONFIRMATION bar) ----------
    sw_hi = [math.nan] * n
    sw_lo = [math.nan] * n
    for i in range(cfg.arm, n - cfg.arm):
        if all(h[i] > h[j] for j in range(i - cfg.arm, i + cfg.arm + 1)
               if j != i):
            sw_hi[i + cfg.arm] = h[i]
        if all(lo[i] < lo[j] for j in range(i - cfg.arm, i + cfg.arm + 1)
               if j != i):
            sw_lo[i + cfg.arm] = lo[i]
    last_sh, last_conf_hi = _ffill_last_conf(sw_hi)
    last_sl, last_conf_lo = _ffill_last_conf(sw_lo)
    sh_fresh = [last_conf_hi[i] >= 0
                and i - last_conf_hi[i] <= cfg.swing_fresh_bars
                for i in range(n)]
    sl_fresh = [last_conf_lo[i] >= 0
                and i - last_conf_lo[i] <= cfg.swing_fresh_bars
                for i in range(n)]

    # ---- ATR (Wilder, like indicators.atr) -----------------------------
    atr = _wilder_atr(h, lo, c, cfg.atr_period)

    # ---- sweep (turtle-soup: wick through the pool, close back) --------
    sweep_lo = [False] * n
    sweep_hi = [False] * n
    for i in range(n):
        rng = h[i] - lo[i]
        if rng <= 0:
            continue
        if sl_fresh[i] and math.isfinite(last_sl[i]) and lo[i] < last_sl[i] \
                and c[i] > last_sl[i] \
                and (last_sl[i] - lo[i]) >= cfg.wick_frac * rng:
            sweep_lo[i] = True
        if sh_fresh[i] and math.isfinite(last_sh[i]) and h[i] > last_sh[i] \
                and c[i] < last_sh[i] \
                and (h[i] - last_sh[i]) >= cfg.wick_frac * rng:
            sweep_hi[i] = True

    # ---- displacement + MSS --------------------------------------------
    disp_up = [False] * n
    disp_dn = [False] * n
    for i in range(n):
        body = abs(c[i] - o[i])
        rng = h[i] - lo[i]
        if rng <= 0:
            continue
        avg_body = _avg_prev_body(o, c, i, cfg.disp_avg_window)
        if avg_body <= 0 or body < cfg.disp_body_mult * avg_body:
            continue
        if body / rng < cfg.disp_body_range:
            continue
        if c[i] > o[i]:
            opp = h[i] - max(o[i], c[i])
            if opp <= cfg.disp_opp_wick * rng:
                disp_up[i] = True
        else:
            opp = min(o[i], c[i]) - lo[i]
            if opp <= cfg.disp_opp_wick * rng:
                disp_dn[i] = True
    mss_up = [False] * n
    mss_dn = [False] * n
    for i in range(n):
        if math.isfinite(last_sh[i]) and c[i] > last_sh[i] and disp_up[i]:
            mss_up[i] = True
        if math.isfinite(last_sl[i]) and c[i] < last_sl[i] and disp_dn[i]:
            mss_dn[i] = True

    # ---- FVG (3-candle, CLOSE-based mitigation -- phase-3 relaxation) --
    bull_alive = [False] * n
    bear_alive = [False] * n
    bull_ce = [math.nan] * n
    bear_ce = [math.nan] * n
    last_bull_created = [-1] * n
    last_bear_created = [-1] * n
    cur_bull = None   # (ce, created_at, gap_bottom, gap_top)
    cur_bear = None
    lbc = -1
    lbrc = -1
    for i in range(n):
        if i >= 2:
            if lo[i] > h[i - 2]:
                cur_bull = ((h[i - 2] + lo[i]) / 2.0, i, h[i - 2], lo[i])
                lbc = i
            if h[i] < lo[i - 2]:
                cur_bear = ((h[i] + lo[i - 2]) / 2.0, i, h[i], lo[i - 2])
                lbrc = i
        if cur_bull is not None:
            ce, created, _, _ = cur_bull
            if c[i] < ce or i - created > cfg.fvg_max_age:
                cur_bull = None
        if cur_bear is not None:
            ce, created, _, _ = cur_bear
            if c[i] > ce or i - created > cfg.fvg_max_age:
                cur_bear = None
        if cur_bull is not None:
            bull_alive[i] = True
            bull_ce[i] = cur_bull[0]
        if cur_bear is not None:
            bear_alive[i] = True
            bear_ce[i] = cur_bear[0]
        last_bull_created[i] = lbc
        last_bear_created[i] = lbrc

    # ---- per-side gates on the newest bar -------------------------------
    i = n - 1
    for tag, (sweep_arr, mss_arr, alive_arr, ce_arr, created_arr,
              sweep_ext_arr, long) in (
            ("l", (sweep_lo, mss_up, bull_alive, bull_ce,
                   last_bull_created, lo, True)),
            ("s", (sweep_hi, mss_dn, bear_alive, bear_ce,
                   last_bear_created, h, False))):
        s = _last_where(sweep_arr, i)
        m = _last_where(mss_arr, i)
        ok = (s >= 0 and m > s and (m - s) <= cfg.mss_max_gap
              and (i - s) <= cfg.sweep_max_age and alive_arr[i]
              and created_arr[i] >= m)
        if not ok:
            continue
        entry = ce_arr[i]
        if not math.isfinite(entry) or entry <= 0:
            continue
        sweep_ext = sweep_ext_arr[s]
        sl = (sweep_ext - cfg.sl_buffer_atr * atr[i]) if long else \
             (sweep_ext + cfg.sl_buffer_atr * atr[i])
        if not math.isfinite(sl) or (sl >= entry if long else sl <= entry):
            continue
        sl_bps = ((entry - sl) / entry * 1e4) if long else \
                 ((sl - entry) / entry * 1e4)
        if not (0 < sl_bps <= cfg.max_sl_bps):
            continue
        if cfg.tp2_bps < cfg.rr_min * sl_bps - 1e-9:
            continue
        side = 1 if long else -1
        return {
            "valid": True, "side": side, "entry": entry, "sl": sl,
            "sl_bps": sl_bps, "sweep_ext": sweep_ext,
            "sweep_bar_t": int(tail[s]["t"]),
            "tp1": entry * (1 + side * bps(cfg.tp1_bps)),
            "tp2": entry * (1 + side * bps(cfg.tp2_bps)),
            "be_trigger": entry + side * cfg.be_after_r * abs(entry - sl),
        }
    return {}


# ----------------------------------------------------------------------
# v4 detector: MACRO MOMENTUM DAY TRADER (15m structure + 1h trend)
#
# One chain, evaluated on the newest closed 15m bar:
#   1. STRUCTURE  confirmed fractal swings (macro_arm each side)
#   2. MSS        a displacement close THROUGH the last confirmed swing
#                 level (market structure shift) within macro_mss_max_age
#   3. IMPULSE    the leg that broke structure must be >= macro_min_leg_atr
#                 x ATR -- minor wicks and chop never qualify
#   4. PULLBACK   price retraces macro_retr_min..macro_retr_max of that leg
#                 WITHOUT breaking the origin (structure still intact)
#   5. TRIGGER    the newest bar is a momentum resumption (close back in
#                 the trend direction, above the prior bar's high/low) and
#                 the retest level sits BELOW/ABOVE the current price
#   6. LEVELS     entry at the flip/retest level, stop past the pullback
#                 extreme (structural, not a micro wick), macro targets
#                 from ATR + the measured move.
# The phase-7 micro core (analyze_bars) stays intact and selectable.
# ----------------------------------------------------------------------
def analyze_macro(bars: List[dict], cfg: Config) -> dict:
    """HTF momentum continuation setup on the newest bar ({} if none)."""
    n = min(len(bars), cfg.history_bars)
    a = cfg.macro_arm
    if n < max(cfg.atr_period + 8, a * 4 + 8, cfg.disp_avg_window + 8):
        return {}
    tail = bars[-n:]
    o = [float(b["o"]) for b in tail]
    h = [float(b["h"]) for b in tail]
    lo = [float(b["l"]) for b in tail]
    c = [float(b["c"]) for b in tail]
    t = [int(b["t"]) for b in tail]
    i = n - 1
    if cfg.macro_cooldown_bars < 0:
        return {}
    atr = _wilder_atr(h, lo, c, cfg.atr_period)
    if not math.isfinite(atr[i]) or atr[i] <= 0:
        return {}

    # ---- confirmed swings, indexed by their confirmation bar -----------
    sw_hi = [math.nan] * n
    sw_lo = [math.nan] * n
    for k in range(a, n - a):
        if all(h[k] > h[j] for j in range(k - a, k + a + 1) if j != k):
            sw_hi[k + a] = h[k]
        if all(lo[k] < lo[j] for j in range(k - a, k + a + 1) if j != k):
            sw_lo[k + a] = lo[k]
    last_sh, _ = _ffill_last_conf(sw_hi)
    last_sl_, _ = _ffill_last_conf(sw_lo)

    body_ok = [False] * n
    for k in range(n):
        rng = h[k] - lo[k]
        if rng <= 0:
            continue
        avg = _avg_prev_body(o, c, k, cfg.disp_avg_window)
        if avg <= 0:
            continue
        body = abs(c[k] - o[k])
        body_ok[k] = (body >= cfg.macro_disp_body_mult * avg
                      and body >= cfg.macro_disp_body_range * rng)

    # newest bar must be a momentum resumption in the trade direction
    res_up = c[i] > o[i] and c[i] > c[i - 1] and h[i] > h[i - 1]
    res_dn = c[i] < o[i] and c[i] < c[i - 1] and lo[i] < lo[i - 1]
    age_lo = max(0, i - cfg.macro_mss_max_age)

    for long in (True, False):
        side_dir = 1 if long else -1
        if (long and not res_up) or (not long and not res_dn):
            continue
        # ---- 2. newest MSS with displacement ---------------------------
        m = -1
        for k in range(i, age_lo - 1, -1):
            if not body_ok[k]:
                continue
            # a bullish MSS breaks the last swing HIGH, a bearish MSS the
            # last swing LOW (mirror the arrays, not just the comparison)
            lvl = (last_sh[k - 1] if long else last_sl_[k - 1]) \
                if k > 0 else math.nan
            if not math.isfinite(lvl):
                continue
            if (long and c[k] > lvl) or (not long and c[k] < lvl):
                m = k
                break
        if m < 0:
            continue
        broke = last_sh[m - 1] if long else last_sl_[m - 1]
        if not math.isfinite(broke):
            continue
        # ---- 3. impulse leg, measured from the STRUCTURAL origin ------
        # the last confirmed swing before the MSS is where the impulse
        # started -- a fixed 30-bar extreme would inflate the leg and make
        # the retracement ratio meaningless
        org = last_sl_[m - 1] if long else last_sh[m - 1]
        if not math.isfinite(org):
            org = min(lo[m - cfg.macro_mss_max_age:m + 1]) if long else \
                max(h[m - cfg.macro_mss_max_age:m + 1])
        extreme = max(h[m:i + 1]) if long else min(lo[m:i + 1])
        leg = (extreme - org) if long else (org - extreme)
        if leg < cfg.macro_min_leg_atr * atr[i]:
            continue
        # ---- 4. pullback into the retest zone, origin intact ----------
        pb = min(lo[m:i + 1]) if long else max(h[m:i + 1])
        retr = (extreme - pb) / leg if long else (pb - extreme) / leg
        if not (cfg.macro_retr_min <= retr <= cfg.macro_retr_max):
            continue
        if (long and pb <= org) or (not long and pb >= org):
            continue                    # impulse origin broken -> trend fail
        # ---- 5/6. retest level, structural stop, macro targets --------
        # the retest level is the pullback midpoint (or the broken level
        # when that is nearer price), clamped to sit BEHIND the current
        # close by a slice of ATR -- a genuine buy-dip / sell-bounce limit
        # -- and kept strictly inside the pullback so the stop is valid
        if cfg.macro_require_flip and (
                (long and pb <= broke) or (not long and pb >= broke)):
            continue                    # pullback lost the broken level
        mid = (extreme + pb) / 2.0
        pad = 0.15 * atr[i]
        if cfg.macro_entry_mode == "momentum":
            # trade WITH the resumption: stop-entry a hair beyond the
            # trigger bar's close, stop still under the pullback extreme
            entry = c[i] + side_dir * 0.10 * atr[i]
            if (long and entry <= pb) or (not long and entry >= pb):
                continue
        elif long:
            entry = min(max(broke, mid), c[i] - pad)
            if entry <= pb:
                entry = min(pb + 0.05 * atr[i], c[i] - pad)
        else:
            entry = max(min(broke, mid), c[i] + pad)
            if entry >= pb:
                entry = max(pb - 0.05 * atr[i], c[i] + pad)
        if cfg.macro_entry_mode != "momentum" and (
                (long and not (entry < c[i] and entry > pb))
                or (not long and not (entry > c[i] and entry < pb))):
            continue                    # no room for a valid retest order
        sl = pb - cfg.macro_sl_buffer_atr * atr[i] if long else \
            pb + cfg.macro_sl_buffer_atr * atr[i]
        dist = abs(entry - sl)
        if dist <= 0:
            continue
        sl_bps = dist / entry * 1e4
        if not (cfg.min_sl_bps <= sl_bps <= cfg.max_sl_bps):
            continue
        side = 1 if long else -1
        tp1 = entry + side * max(cfg.macro_tp1_atr * atr[i],
                                 cfg.macro_tp1_min_r * dist)
        tp2 = entry + side * max(cfg.macro_tp2_min_r * dist,
                                 min(leg, cfg.macro_tp2_atr * atr[i]))
        return {
            "valid": True, "side": side, "entry": entry, "sl": sl,
            "sl_bps": sl_bps, "sweep_ext": pb, "sweep_bar_t": t[m],
            "tp1": tp1, "tp2": tp2,
            "be_trigger": entry + side * cfg.be_after_r * dist,
            "leg_atr": leg / atr[i], "retr": retr,
        }
    return {}


def detect(bars: List[dict], cfg: Config) -> dict:
    """Dispatch to the configured hunting engine: v4 macro momentum
    (default) or the preserved phase-7 micro core."""
    if cfg.detector == "phase7":
        return analyze_bars(bars, cfg)
    return analyze_macro(bars, cfg)


def _ffill_last_conf(arr: List[float]) -> Tuple[List[float], List[int]]:
    out: List[float] = []
    conf: List[int] = []
    cur = math.nan
    cur_conf = -1
    for i, v in enumerate(arr):
        if not math.isnan(v):
            cur = v
            cur_conf = i
        out.append(cur)
        conf.append(cur_conf)
    return out, conf


def _wilder_atr(h: List[float], lo: List[float], c: List[float],
                period: int) -> List[float]:
    n = len(h)
    tr = [0.0] * n
    for i in range(n):
        pc = c[i - 1] if i > 0 else c[i]
        tr[i] = max(h[i] - lo[i], abs(h[i] - pc), abs(lo[i] - pc))
    atr = [math.nan] * n
    if n < period:
        return atr
    prev = sum(tr[:period]) / period
    atr[period - 1] = prev
    for i in range(period, n):
        prev = (prev * (period - 1) + tr[i]) / period
        atr[i] = prev
    return atr


def _avg_prev_body(o: List[float], c: List[float], i: int,
                   win: int) -> float:
    lo = max(0, i - win)
    bodies = [abs(c[j] - o[j]) for j in range(lo, i)]
    if len(bodies) < win // 2:
        return 0.0
    return sum(bodies) / len(bodies)


def _last_where(arr: List[bool], upto: int) -> int:
    for i in range(upto, -1, -1):
        if arr[i]:
            return i
    return -1


def _ema(vals: List[float], period: int) -> List[float]:
    """Standard EMA, seeded with the SMA of the first `period` values."""
    if period <= 0 or len(vals) < period:
        return []
    k = 2.0 / (period + 1.0)
    out = [sum(vals[:period]) / period]
    for v in vals[period:]:
        out.append(v * k + out[-1] * (1.0 - k))
    return out


def _true_ranges_simple(bars) -> List[float]:
    trs: List[float] = []
    prev = None
    for b in bars:
        h, lo, c = float(b["h"]), float(b["l"]), float(b["c"])
        trs.append(max(h - lo, abs(h - prev), abs(lo - prev))
                   if prev is not None else h - lo)
        prev = c
    return trs


# ----------------------------------------------------------------------
# .env + ntfy alerts + phone-app state publishing
# ----------------------------------------------------------------------
def load_env_file(path: Path) -> Dict[str, str]:
    """Tiny .env reader (KEY=VALUE, # comments).  No dependency."""
    out: Dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


# The bot reads ONLY these keys from .env.  Exchange API secrets that may
# live in the same file are never loaded into this process.
AGENT_ENV_ALLOWLIST = {"NTFY_TOPIC", "NTFY_TOPIC_SHARED", "SCALPER_DATA",
                       "HL_ADDRESS"}


def load_agent_env(path: Path) -> Dict[str, str]:
    raw = load_env_file(path)
    kept = {k: v for k, v in raw.items() if k in AGENT_ENV_ALLOWLIST}
    skipped = [k for k in raw if k not in AGENT_ENV_ALLOWLIST]
    if skipped:
        LOG.info("[SAFETY] .env keys ignored by the bot: %s",
                 ", ".join(sorted(skipped)))
    return kept


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def paper_lock_engaged(base_dir: Path) -> bool:
    """Hard paper lock: the env var ICT_PAPER_ONLY, or a PAPER_ONLY file
    next to the script.  Either one makes live trading impossible."""
    return _env_flag("ICT_PAPER_ONLY") or (base_dir / "PAPER_ONLY").exists()


def restore_day_state(cfg: Config, book: "PaperBook", risk: "RiskEngine",
                      appstate: "AppState") -> bool:
    """Crash recovery: on a restart WITHIN THE SAME UTC DAY, re-anchor the
    day from the persisted app state -- equity/PnL, the target & DD halt
    latches, per-coin guards and streaks, the signal budget and the
    counters -- so a restart can never re-trade a halted day or reset the
    PnL clock.  Returns True when a same-day snapshot was applied."""
    try:
        st = json.loads(Path(appstate.state_path).read_text())
    except (OSError, ValueError):
        return False
    try:
        dk = int(st.get("day_start_ms") or 0)
        if dk != day_key(int(time.time() * 1000)):
            return False                # stale snapshot: it is a new day
        dseq = float(st.get("day_start_equity") or 0)
        eq = float(st.get("equity") or 0)
        if dseq <= 0 or eq <= 0:
            return False
        # the open book is NOT day-scoped: resume open positions and
        # resting limits whenever the snapshot is fresh (< 24h), so a
        # restart can never wipe a live position again
        snap = st.get("book_snapshot") or {}
        if snap and (int(st.get("ts") or 0) >= int(time.time() * 1000)
                     - 24 * 3600 * 1000):
            def _f(v):
                # tiers can be null (TP2 consumed by the trail, etc.)
                return float(v) if v is not None else None
            for rp in snap.get("positions") or []:
                pos = Position(
                    coin=str(rp["coin"]), side=int(rp["side"]),
                    entry_px=float(rp["entry_px"]), qty=float(rp["qty"]),
                    sl_px=float(rp["sl_px"]),
                    be_trigger_px=_f(rp["be_trigger_px"]),
                    tp1_px=_f(rp["tp1_px"]), tp2_px=_f(rp["tp2_px"]),
                    tp1_qty=float(rp["tp1_qty"]),
                    tp2_qty=float(rp["tp2_qty"]),
                    runner_qty=float(rp["runner_qty"]),
                    fill_t=int(rp["fill_t"]), bars_held=int(rp["bars_held"]),
                    scaled=bool(rp["scaled"]), be_armed=bool(rp["be_armed"]),
                    trail_mode=bool(rp["trail_mode"]),
                    trail_ref=float(rp["trail_ref"]),
                    lev=float(rp.get("lev", cfg.lev)),
                    mfe_r=float(rp.get("mfe_r", 0.0)),
                    legs=[Leg(str(lg["exit_reason"]), float(lg["qty"]),
                              float(lg["entry_px"]), float(lg["exit_px"]),
                              float(lg["fees"]), float(lg["pnl"]),
                              float(lg["ret_bps"]), int(lg["t_ms"]))
                          for lg in (rp.get("legs") or [])])
                book.positions[pos.coin] = pos
            for ro in snap.get("orders") or []:
                order = EntryOrder(
                    coin=str(ro["coin"]), side=int(ro["side"]),
                    limit_px=float(ro["limit_px"]), qty=float(ro["qty"]),
                    signal_t=int(ro["signal_t"]), placed_t=int(ro["placed_t"]),
                    entry_px=float(ro["entry_px"]), sl_px=float(ro["sl_px"]),
                    be_trigger_px=_f(ro["be_trigger_px"]),
                    tp1_px=_f(ro["tp1_px"]), tp2_px=_f(ro["tp2_px"]),
                    tp1_qty=float(ro["tp1_qty"]),
                    tp2_qty=float(ro["tp2_qty"]),
                    runner_qty=float(ro["runner_qty"]),
                    lev=float(ro.get("lev", cfg.lev)))
                book.orders[order.coin] = order
        book.realized_pnl = eq - cfg.paper_equity
        risk.day_start_ms = dk
        risk.day_start_eq = dseq
        if bool(st.get("target_hit")):
            risk._hit_notified_day = dk
        if bool(st.get("dd_halt")):
            risk._dd_notified_day = dk
        appstate._day_ms = dk
        appstate._day_start_equity = dseq
        for coin in st.get("blocked_today") or []:
            book._blocked.add((str(coin), dk))
        for coin, n in (st.get("day_signals") or {}).items():
            book._day_signals[(str(coin), dk)] = int(n)
        for coin, bpsv in (st.get("day_profit") or {}).items():
            book._day_profit[(str(coin), dk)] = float(bpsv)
        for coin, streak in (st.get("day_streak") or {}).items():
            book._streak[(str(coin), dk)] = int(streak)
        cnt = st.get("counters") or {}
        book.n_signals = int(cnt.get("signals") or 0)
        book.n_fills = int(cnt.get("fills") or 0)
        book.n_exits = int(cnt.get("exits") or 0)
        LOG.info("[RECOVERY] same-day state restored: equity %.2f (day pnl "
                 "%+.2f) | target_hit=%s dd_halt=%s | blocked=%d coins | "
                 "signals used=%d | resumed: %d position(s), %d resting "
                 "order(s)", eq, eq - dseq, bool(st.get("target_hit")),
                 bool(st.get("dd_halt")),
                 len(st.get("blocked_today") or []), book.n_signals,
                 len(book.positions), len(book.orders))
        return True
    except (KeyError, TypeError, ValueError) as e:
        LOG.warning("[RECOVERY] snapshot unreadable (%s) -- starting a "
                    "fresh day", e)
        return False


class Notifier:
    """ntfy.sh push -- the operator's phone alerts.  Never fatal and never
    blocks the trading loop: a failed push is a warning in the log."""

    def __init__(self, topic: str, enabled: bool = True):
        self.topic = (topic or "").strip()
        self.enabled = bool(enabled and self.topic)
        self.sent = 0
        self.failed = 0
        self._last: Dict[str, float] = {}

    def send(self, msg: str, title: str = "ICT Sniper",
             tags: str = "chart_with_upwards_trend",
             priority: str = "default", throttle_s: float = 0.0,
             throttle_key: str = "") -> bool:
        if not self.enabled:
            LOG.info("[NTFY:off] %s | %s", title, msg.replace("\n", " | "))
            return False
        if throttle_s and throttle_key:
            now = time.time()
            if now - self._last.get(throttle_key, 0.0) < throttle_s:
                return False
            self._last[throttle_key] = now
        try:
            ascii_title = title.encode("ascii", "ignore").decode() or "ICT Sniper"
            req = urllib.request.Request(
                f"https://ntfy.sh/{self.topic}", data=msg.encode("utf-8"),
                headers={"Title": ascii_title, "Priority": priority,
                         "Tags": tags})
            with urllib.request.urlopen(req, timeout=10) as r:
                ok = 200 <= r.status < 300
            if ok:
                self.sent += 1
            else:
                self.failed += 1
            return ok
        except Exception as e:                      # noqa: BLE001
            self.failed += 1
            LOG.warning("[NTFY] push failed: %s", e)
            return False


class AppState:
    """Writes the phone app's state files in ITS schema:

        <data>/state/paper.json      equity, positions[].lots[], ts, ...
        <data>/state/live.json       {coin: {"px": .., "ts": ..}}
        <data>/logs/hl_trades.jsonl  the new system's own leg log

    scalper/app/app.py renders paper.json + live.json directly, so the
    phone shows the ICT Sniper without any app change."""

    def __init__(self, data_dir: Path, cfg: Config):
        self.dir = Path(data_dir)
        self.cfg = cfg
        self.state_path = self.dir / "state" / "paper.json"
        self.live_path = self.dir / "state" / "live.json"
        self.trades_path = self.dir / "logs" / "trades.jsonl"
        (self.dir / "state").mkdir(parents=True, exist_ok=True)
        (self.dir / "logs").mkdir(parents=True, exist_ok=True)
        self._day_ms = 0
        self._day_start_equity = cfg.paper_equity
        self.trades_today = 0
        self.writes = 0

    def _roll_day(self, now_ms: int, equity: float) -> None:
        dk = day_key(now_ms)
        if dk != self._day_ms:
            self._day_ms = dk
            self._day_start_equity = equity
            self.trades_today = 0

    def log_trade(self, coin: str, side: int, reason: str, qty: float,
                  entry_px: float, exit_px: float, ret_bps: float,
                  pnl: float, fees: float, t_ms: int,
                  opened_ms: int = 0) -> None:
        # the phone app reads data/logs/trades.jsonl and expects the
        # scalper's row schema: ts_ms/opened_ms/direction/lot/exit_reason
        row = {"ts_ms": t_ms, "opened_ms": opened_ms or t_ms,
               "symbol": coin, "direction": side, "side": side,
               "lot": reason, "kind": reason, "exit_reason": reason,
               "qty": qty, "entry": entry_px, "exit": exit_px,
               "ret_bps": round(ret_bps, 2), "pnl": round(pnl, 4),
               "fees": round(fees, 6), "strategy": "ict_sniper",
               "tf": f"{self.cfg.tf_min}m"}
        try:
            with open(self.trades_path, "a") as f:
                f.write(json.dumps(row) + "\n")
        except OSError as e:
            LOG.warning("[STATE] trade log failed: %s", e)
        self.trades_today += 1

    def publish(self, book: "PaperBook", mids: Dict[str, float]) -> None:
        if not self.cfg.publish_state:
            return
        now = int(time.time() * 1000)
        equity = self.cfg.paper_equity + book.realized_pnl
        self._roll_day(now, equity)
        positions = []
        for coin, p in book.positions.items():
            lots: List[dict] = []
            if p.qty > 0:            # the OPEN remainder goes first: the app
                tp2q = min(getattr(p, "tp2_qty", 0.0) or 0.0, p.qty)
                tp1_here = {"tp1": p.tp1_px, "be": p.be_trigger_px}
                if p.scaled and tp2q > 0 and p.qty > tp2q:
                    # v3 tri-tier: middle 25% (TP2) + trailed final 25%
                    lots.append({
                        "qty": tp2q, "kind": "tp2",
                        "sl": p.sl_px, "tp": p.tp2_px, "entry": p.entry_px,
                        "entry_fee": 0.0, "tp_r": 0.0, "exit_px": None,
                        "exit_reason": "", "exit_ms": 0, "pnl": 0.0,
                        **tp1_here})
                    lots.append({
                        "qty": p.qty - tp2q, "kind": "runner",
                        "sl": p.sl_px, "tp": p.tp2_px, "entry": p.entry_px,
                        "entry_fee": 0.0, "tp_r": 0.0, "exit_px": None,
                        "exit_reason": "", "exit_ms": 0, "pnl": 0.0,
                        **tp1_here})
                else:
                    lots.append({
                        "qty": p.qty,
                        "kind": "runner" if p.scaled else "full",
                        "sl": p.sl_px, "tp": p.tp2_px, "entry": p.entry_px,
                        "entry_fee": 0.0, "tp_r": 0.0, "exit_px": None,
                        "exit_reason": "", "exit_ms": 0, "pnl": 0.0,
                        **tp1_here})
            for leg in p.legs:
                lots.append({
                    "qty": leg.qty, "kind": leg.exit_reason,
                    "sl": p.entry_px, "tp": leg.exit_px,
                    "entry": leg.entry_px, "entry_fee": 0.0, "tp_r": 0.0,
                    "exit_px": leg.exit_px,
                    "exit_reason": leg.exit_reason, "exit_ms": leg.t_ms,
                    "pnl": round(leg.pnl, 6)})
            positions.append({
                "symbol": coin, "direction": p.side, "opened_ms": p.fill_t,
                "pos_id": f"hl-{p.fill_t}-{coin}",
                "be_active": bool(p.be_armed),
                "trail_armed": bool(getattr(p, "trail_mode", False)),
                "struct_exit_pending": False,
                "realized_pnl": round(sum(x.pnl for x in p.legs), 6),
                "meta": {"strategy": "ict_sniper", "entry_model": "fvg",
                         "leverage": getattr(p, "lev", self.cfg.lev),
                         "trail": bool(getattr(p, "trail_mode", False)),
                         "tf": f"{self.cfg.tf_min}m",
                         "tp1_frac": self.cfg.tp1_frac,
                         "tp2_frac": self.cfg.tp2_frac,
                         "time_exit_bars": self.cfg.time_exit_bars},
                "lots": lots})
        blocked = sorted({c for (c, d) in book._blocked
                          if d == day_key(now)})
        dk = day_key(now)
        risk = getattr(book, "risk", None)
        state = {
            "equity": round(equity, 6),
            "start_equity": self.cfg.paper_equity,
            "day_start_ms": self._day_ms,
            "day_start_equity": round(self._day_start_equity, 6),
            "trades_today": self.trades_today,
            "consec_losses": 0,
            "cooldown_until_ms": 0,
            "halted": bool(blocked),
            "halt_reason": ("hit&run/breaker: " + ",".join(blocked[:6])
                            if blocked else ""),
            # --- crash-recovery snapshot (restore_day_state mirrors it) --
            "target_hit": bool(risk is not None
                               and risk.day_start_ms > 0
                               and risk._hit_notified_day
                               == risk.day_start_ms),
            "dd_halt": bool(risk is not None
                            and risk.day_start_ms > 0
                            and risk._dd_notified_day == risk.day_start_ms),
            "day_signals": {c: n for (c, d), n in book._day_signals.items()
                            if d == dk},
            "day_profit": {c: round(b, 2)
                           for (c, d), b in book._day_profit.items()
                           if d == dk},
            "day_streak": {c: n for (c, d), n in book._streak.items()
                           if d == dk},
            "blocked_today": blocked,
            "counters": {"signals": book.n_signals, "fills": book.n_fills,
                         "exits": book.n_exits},
            "book_snapshot": {
                "positions": [{
                    "coin": p.coin, "side": p.side, "entry_px": p.entry_px,
                    "qty": p.qty, "sl_px": p.sl_px,
                    "be_trigger_px": p.be_trigger_px,
                    "tp1_px": p.tp1_px, "tp2_px": p.tp2_px,
                    "tp1_qty": p.tp1_qty, "tp2_qty": p.tp2_qty,
                    "runner_qty": p.runner_qty, "fill_t": p.fill_t,
                    "bars_held": p.bars_held, "scaled": p.scaled,
                    "be_armed": p.be_armed, "trail_mode": p.trail_mode,
                    "trail_ref": p.trail_ref, "lev": p.lev,
                    "mfe_r": p.mfe_r,
                    "legs": [{"exit_reason": lg.exit_reason, "qty": lg.qty,
                              "entry_px": lg.entry_px, "exit_px": lg.exit_px,
                              "fees": lg.fees, "pnl": lg.pnl,
                              "ret_bps": lg.ret_bps, "t_ms": lg.t_ms}
                             for lg in p.legs],
                } for p in book.positions.values()],
                "orders": [{
                    "coin": o.coin, "side": o.side, "limit_px": o.limit_px,
                    "qty": o.qty, "signal_t": o.signal_t,
                    "placed_t": o.placed_t, "entry_px": o.entry_px,
                    "sl_px": o.sl_px, "be_trigger_px": o.be_trigger_px,
                    "tp1_px": o.tp1_px, "tp2_px": o.tp2_px,
                    "tp1_qty": o.tp1_qty, "tp2_qty": o.tp2_qty,
                    "runner_qty": o.runner_qty, "lev": o.lev,
                } for o in book.orders.values()],
            },
            "positions": positions,
            "pos_counter": book.n_fills,
            "ts": now,
        }
        try:
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=1, default=float))
            tmp.replace(self.state_path)
            self.writes += 1
        except OSError as e:
            LOG.warning("[STATE] paper.json write failed: %s", e)
        live = {coin: {"px": px, "ts": now}
                for coin, px in mids.items() if coin in book.positions}
        try:
            if live:
                tmp = self.live_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(live))
                tmp.replace(self.live_path)
            elif self.live_path.exists():
                self.live_path.unlink()
        except OSError as e:
            LOG.warning("[STATE] live.json write failed: %s", e)


class Publisher:
    """Routes engine events to ntfy + the app state files."""

    def __init__(self, cfg: Config, notifier: Notifier, state: AppState,
                 book_getter, mids_getter):
        self.cfg = cfg
        self.notifier = notifier
        self.state = state
        self._book = book_getter
        self._mids = mids_getter

    def refresh(self) -> None:
        try:
            self.state.publish(self._book(), self._mids())
        except Exception as e:                      # noqa: BLE001
            LOG.warning("[STATE] publish failed: %s", e)

    def event(self, kind: str, **kw) -> None:
        coin = kw.get("coin", "?")
        side = kw.get("side", 0)
        d = "LONG" if side > 0 else "SHORT"
        try:
            if kind == "signal":
                self.notifier.send(
                    f"entry limit {kw['limit_px']:.6g} | SL {kw['sl_px']:.6g}"
                    f" ({kw['sl_bps']:.0f} bps)\nTP1 +{self.cfg.tp1_bps:.0f}"
                    f" ({self.cfg.tp1_frac * 100:.0f}%) | TP2 "
                    f"+{self.cfg.tp2_bps:.0f} | BE {kw['be_px']:.6g}",
                    title=f"SNIPER SIGNAL {coin} {d}", tags="mag")
            elif kind == "fill":
                self.notifier.send(
                    f"filled {kw['qty']:.6g} @ {kw['px']:.6g} (maker)\n"
                    f"SL {kw['sl_px']:.6g} | TP1 {kw['tp1_px']:.6g} | "
                    f"TP2 {kw['tp2_px']:.6g}",
                    title=f"SNIPER FILL {coin} {d}", tags="white_check_mark")
            elif kind == "tp1":
                self.notifier.send(
                    f"scaled {kw['qty']:.6g} @ {kw['px']:.6g} "
                    f"(+{kw['ret_bps']:.0f} bps, {kw['pnl']:+.2f})\n"
                    f"runner {kw['left']:.6g} -> SL breakeven",
                    title=f"TP1 HIT {coin} {d}", tags="moneybag")
            elif kind == "exit":
                reason = str(kw.get("reason", "")).upper()
                tags = {"TP2": "rocket", "SL": "warning", "BE": "shield",
                        "TIME": "hourglass"}.get(reason, "x")
                self.notifier.send(
                    f"{reason} {kw['qty']:.6g} @ {kw['px']:.6g} "
                    f"({kw['ret_bps']:+.0f} bps, {kw['pnl']:+.2f})\n"
                    f"bars held {kw.get('bars', 0)} | day "
                    f"{kw.get('day_bps', 0):+.0f} bps",
                    title=f"EXIT {reason} {coin} {d}", tags=tags)
            elif kind == "trail":
                self.notifier.send(
                    f"{coin} {d}: TP1 was fast -- runner switched to a "
                    f"pivot trail (static TP2 cancelled)",
                    title=f"TRAIL ARMED {coin} {d}", tags="dart")
            elif kind == "guard":
                self.notifier.send(kw.get("why", ""),
                                   title=f"HALT {coin} (day)", tags="no_entry",
                                   priority="high")
            elif kind == "day":
                self.notifier.send(kw.get("why", ""),
                                   title="SNIPER day summary", tags="bar_chart")
            elif kind == "boot":
                self.notifier.send(kw.get("why", ""),
                                   title="ICT Sniper started", tags="rocket")
        except Exception as e:                      # noqa: BLE001
            LOG.warning("[PUB] notify failed: %s", e)
        self.refresh()


# ----------------------------------------------------------------------
# Paper book: the order state machine (candle-based fills, VIP costs).
# ----------------------------------------------------------------------
@dataclass
class Leg:
    exit_reason: str
    qty: float
    entry_px: float
    exit_px: float
    fees: float
    pnl: float
    ret_bps: float
    t_ms: int


# ----------------------------------------------------------------------
# Module layer: dynamic leverage + goal seeking, portfolio caps, regime
# (chop) filter, velocity trail, news blackout.  The phase-7 FVG/sweep
# core (analyze_bars, 0.25-ATR sweep stop, TP1 75%) is NOT touched -- these
# only gate entries, size leverage, and manage the runner.
# ----------------------------------------------------------------------
class RiskEngine:
    """Consultation point for the execution modules."""

    def __init__(self, cfg: Config, book_getter, mids_getter,
                 bars_getter, notifier):
        self.cfg = cfg
        self._book = book_getter
        self._mids = mids_getter
        self._bars = bars_getter
        self.notifier = notifier
        self.day_start_ms = 0
        self.day_start_eq = cfg.paper_equity
        self._hit_notified_day = 0
        self._dd_notified_day = 0
        self._news_blocks: List[tuple] = []
        self._news_fetched_at = 0.0

    # ---- module 1: daily goal + dynamic leverage ------------------------
    def equity(self) -> float:
        return self.cfg.paper_equity + self._book().realized_pnl

    def target_equity(self) -> float:
        return self.day_start_eq * (1.0 + self.cfg.daily_target_pct / 100.0)

    def roll_day(self, now_ms: int) -> None:
        """Anchor/roll the UTC trading day -- HARDENED (v3).

        The daily target, the -DD floor and the whole leverage ladder hang
        off `day_start_eq`, so an anomalous stamp must never re-baseline the
        day mid-session.  Two guards: a future-dated stamp (malformed bar)
        is refused outright, and the anchor only ever moves FORWARD -- which
        also neutralises 0 / seconds-unit / stale stamps."""
        wnow = int(time.time() * 1000)
        if now_ms > wnow + 6 * 3600 * 1000:
            return                       # future-dated junk cannot roll it
        dk = day_key(now_ms)
        if dk > self.day_start_ms:       # forward roll only, once per day
            self.day_start_ms = dk
            self.day_start_eq = self.equity()
            LOG.info("[DAY] new UTC day anchored: equity %.2f -> target %.2f "
                     "| DD floor %.2f", self.day_start_eq,
                     self.target_equity(),
                     self.day_start_eq * (1.0 - self.cfg.max_daily_dd_pct
                                          / 100.0))

    def progress(self) -> float:
        gap = self.target_equity() - self.day_start_eq
        if gap <= 0:
            return 1.0
        return max(0.0, min(1.0, (self.equity() - self.day_start_eq) / gap))

    def daily_halted(self, now_ms: int) -> bool:
        """Module 1: once +100% of the day-start equity is reached, no new
        trades for the rest of the UTC day (open positions ride out)."""
        self.roll_day(now_ms)
        return self.equity() >= self.target_equity()

    def check_halt(self, now_ms: int) -> bool:
        """Idempotent halts, one ntfy each per UTC day:
          (a) +daily_target_pct banked -> trophy halt;
          (b) equity <= day-open x (1 - max_daily_dd_pct/100) -> DD halt.
        Open positions ride their stops; new scanning stops either way."""
        self.roll_day(now_ms)
        eq = self.equity()
        dd_floor = self.day_start_eq * (1.0 - self.cfg.max_daily_dd_pct
                                        / 100.0)
        if eq <= dd_floor:
            if self._dd_notified_day != self.day_start_ms:
                self._dd_notified_day = self.day_start_ms
                LOG.info("[DD-HALT] equity %.2f <= floor %.2f (-%.0f%% from "
                         "the day's open) -- scanning halted for the day",
                         eq, dd_floor, self.cfg.max_daily_dd_pct)
                if self.notifier is not None:
                    self.notifier.send(
                        f"Daily drawdown guard: equity {eq:.2f} hit the "
                        f"-{self.cfg.max_daily_dd_pct:.0f}% floor. Bot "
                        f"halted for the day -- no revenge trading.",
                        title="SNIPER DD-HALT", tags="rotating_light",
                        priority="high")
            return True
        if eq < self.target_equity():
            return False
        if self._hit_notified_day != self.day_start_ms:
            self._hit_notified_day = self.day_start_ms
            LOG.info("[TARGET] +%.0f%% daily target hit (equity %.2f) -- "
                     "scanning halted until the next UTC day",
                     self.cfg.daily_target_pct, eq)
            if self.notifier is not None:
                self.notifier.send(
                    f"+{self.cfg.daily_target_pct:.0f}% daily target hit "
                    f"(equity {eq:.2f}). Bot halted for the day -- no "
                    f"revenge trading.", title="SNIPER TARGET",
                    tags="trophy", priority="high")
        return True

    def leverage_for(self, sl_bps: float) -> float:
        """Module 1 v2 -- front-loaded, risk-first at every point.

        Day-young (progress < taper_at_pct, equity >= day open):
          confident mode: per-stop budget = risk_early_pct, ceiling =
          max_lev -> 40-50x on valid setups with tight stops, and at most
          risk_early_pct of equity on ANY single stop.
        Below the day's open, or in the taper zone (progress in
        [taper_at_pct, secure_at_pct)): budget = risk_pct_per_stop,
          ceiling = taper_lev -- protect what the day has earned.
        Banked >= secure_at_pct: ceiling = secure_lev -- secure mode.
        max_stop_loss_pct is a hard ratchet above all budgets: one stop
        can never cost more than that of equity, whatever the knobs say."""
        if not (0 < sl_bps < 5000):
            return self.cfg.min_lev
        p = self.progress()
        below_open = self.equity() < self.day_start_eq
        if not below_open and p < self.cfg.taper_at_pct:
            budget = self.cfg.risk_early_pct
            cap = self.cfg.max_lev
        elif p < self.cfg.secure_at_pct:
            budget = self.cfg.risk_pct_per_stop
            cap = self.cfg.taper_lev
        else:
            budget = self.cfg.risk_pct_per_stop
            cap = self.cfg.secure_lev
        denom = self.cfg.alloc * sl_bps / 1e4
        lev = min(budget / denom, cap, self.cfg.max_stop_loss_pct / denom)
        return float(max(self.cfg.min_lev, min(self.cfg.max_lev, lev)))

    # ---- module 2: portfolio / correlation guard -------------------------
    def can_open(self, side: int) -> bool:
        positions = self._book().positions
        if len(positions) >= self.cfg.max_positions:
            return False
        same = sum(1 for p in positions.values() if p.side == side)
        return same < self.cfg.max_same_side

    # ---- module 3: regime (chop) filter ----------------------------------
    def _true_ranges(self, bars) -> list:
        trs = []
        prev = None
        for b in bars:
            h, lo, c = float(b["h"]), float(b["l"]), float(b["c"])
            trs.append(max(h - lo, abs(h - prev), abs(lo - prev))
                       if prev is not None else h - lo)
            prev = c
        return trs

    def regime_ok(self, coin: str) -> bool:
        """Lightweight chop guard on the asset's own bars: standby when
        both the true range and the volume collapse vs their own norm."""
        if not self.cfg.chop_enabled:
            return True
        bars = self._bars(coin) or []
        if len(bars) < self.cfg.chop_norm + 2:
            return True                              # not enough history
        tail = bars[-self.cfg.chop_norm:]
        trs = self._true_ranges(tail)
        recent_tr = sum(trs[-self.cfg.chop_look:]) / self.cfg.chop_look
        norm_tr = sum(trs) / len(trs)
        vols = [float(b.get("v", 0) or 0) for b in tail]
        norm_v = sum(vols) / len(vols)
        recent_v = sum(vols[-self.cfg.chop_look:]) / self.cfg.chop_look
        if norm_tr <= 0 or norm_v <= 0:
            return True
        alive = (recent_tr / norm_tr >= self.cfg.chop_tr_ratio) or \
                (recent_v / norm_v >= self.cfg.chop_vol_ratio)
        if not alive:
            LOG.info("[CHOP] %s in standby: TR %.2fx, vol %.2fx of norm",
                     coin, recent_tr / norm_tr, recent_v / norm_v)
            if self.notifier is not None:
                self.notifier.send(
                    f"{coin} is flat (TR {recent_tr / norm_tr:.2f}x, "
                    f"vol {recent_v / norm_v:.2f}x of norm) -- setup "
                    f"ignored", title="SNIPER STANDBY", tags="zzz",
                    throttle_s=3600, throttle_key="chop")
        return alive

    # ---- module 4: velocity check for the runner -------------------------
    def velocity_fast(self, coin: str, side: int) -> bool:
        """Fast TP1: at least velocity_bars of the last N bars before the
        trigger in the trade direction, bodies summing to >=
        velocity_body_atr x ATR."""
        if not self.cfg.velocity_enabled:
            return False
        bars = self._bars(coin) or []
        if len(bars) < self.cfg.velocity_bars + 3:
            return False
        seg = bars[-(self.cfg.velocity_bars + 1):-1]
        trs = self._true_ranges(bars[-15:])
        atr = sum(trs) / len(trs) if trs else 0.0
        if atr <= 0:
            return False
        bodies = sum(abs(float(b["c"]) - float(b["o"])) for b in seg)
        directional = sum(1 for b in seg
                          if (float(b["c"]) > float(b["o"])) == (side > 0))
        return directional >= max(2, self.cfg.velocity_bars - 1) and \
            bodies >= self.cfg.velocity_body_atr * atr

    # ---- v3 module 1: HTF trend bias -------------------------------------
    def _htf_closes(self, bars) -> List[float]:
        """Aggregate the execution-TF series into HTF closes, aligned so the
        newest bucket is the most recent one (last 5m close = last HTF
        close).  No extra API calls -- built from the 5m bars we hold."""
        per = max(1, self.cfg.htf_minutes // max(1, self.cfg.tf_min))
        out: List[float] = []
        i = len(bars)
        while i - per >= 0:
            out.append(float(bars[i - 1]["c"]))
            i -= per
        out.reverse()
        return out

    def htf_bias(self, coin: str) -> Optional[int]:
        """Macro trend: 1 bull, -1 bear, 0 neutral (blocks -- no
        counter-trend fading), None = not enough history (allow)."""
        if not self.cfg.htf_filter_enabled:
            return None
        bars = self._bars(coin) or []
        per = self.cfg.htf_minutes // max(1, self.cfg.tf_min)
        if len(bars) < per * (self.cfg.htf_ema + self.cfg.htf_slope_bars + 1):
            return None
        closes = self._htf_closes(bars)
        ema = _ema(closes, self.cfg.htf_ema)
        if len(ema) < self.cfg.htf_slope_bars + 1:
            return None
        slope = ema[-1] - ema[-1 - self.cfg.htf_slope_bars]
        last = closes[-1]
        if slope > 0 and last > ema[-1]:
            return 1
        if slope < 0 and last < ema[-1]:
            return -1
        return 0

    # ---- v3 module 2: volume / expansion confirmation ---------------------
    def vol_ok(self, coin: str) -> bool:
        """The bar that fills the FVG retest must show participation:
        volume OR range expansion vs its own recent norm (smart money
        defending the level, not a low-volume drift)."""
        if not self.cfg.vol_confirm_enabled:
            return True
        bars = self._bars(coin) or []
        if len(bars) < self.cfg.vol_look + 2:
            return True
        trig = bars[-1]
        base = bars[-(self.cfg.vol_look + 1):-1]
        vols = [float(b.get("v", 0) or 0) for b in base]
        avg_v = sum(vols) / len(vols) if vols else 0.0
        if avg_v <= 0:
            return True
        mult = self.cfg.vol_mult
        if self.session_state(int(trig.get("t", 0))) == "prime":
            mult *= self.cfg.prime_vol_relax
        v = float(trig.get("v", 0) or 0)
        if v >= mult * avg_v:
            return True
        trs = _true_ranges_simple(base)
        avg_r = sum(trs) / len(trs) if trs else 0.0
        rng = float(trig["h"]) - float(trig["l"])
        return bool(avg_r > 0 and rng >= self.cfg.vol_range_mult * avg_r)

    # ---- v3 module 4: session / liquidity timing --------------------------
    def session_state(self, now_ms: int) -> str:
        """dead (paused) / prime (London-NY overlap: priority) / normal."""
        if not self.cfg.session_filter_enabled:
            return "normal"
        utc = time.gmtime(now_ms / 1000.0)
        hm = utc.tm_hour * 60 + utc.tm_min
        for lo, hi in self.cfg.session_dead_utc:
            if lo <= hm < hi:
                return "dead"
        for lo, hi in self.cfg.session_prime_utc:
            if lo <= hm < hi:
                return "prime"
        return "normal"

    def flat_time_reached(self, now_ms: int) -> bool:
        """Day-trader rule: after flat_by_hm UTC no new risk is taken."""
        if not self.cfg.flat_enabled:
            return False
        utc = time.gmtime(now_ms / 1000.0)
        hh, mm = self.cfg.flat_by_hm
        return (utc.tm_hour, utc.tm_min) >= (hh, mm)

    def session_ok(self, now_ms: int, leg_atr: float = None) -> bool:
        """Session discipline: dead hours never trade; prime windows are
        open season; normal hours require a monster structure (impulse leg
        >= macro_normal_min_leg x ATR) to justify the risk."""
        st = self.session_state(now_ms)
        if st == "dead":
            return False
        if self.flat_time_reached(now_ms):
            return False            # end of the trading day: flat only
        if st == "normal" and self.cfg.session_strict:
            return leg_atr is not None \
                and leg_atr >= self.cfg.macro_normal_min_leg
        return True

    def monster_gate_ok(self, leg_atr, lev: float,
                        tp2_bps: float) -> Tuple[bool, str]:
        """Benz Mode: with half the daily target banked, only setups that
        can meaningfully close the gap qualify.  A $6 tier is refused --
        we wait for the next MINA instead of degrading to Paykans."""
        if self.progress() < self.cfg.monster_mode_at_pct:
            return True, ""
        if leg_atr is not None and leg_atr < self.cfg.monster_min_leg:
            return False, "Benz mode: leg %.1fxATR < %.0f -- waiting for a " \
                "monster" % (leg_atr, self.cfg.monster_min_leg)
        pot = self.cfg.alloc * lev * tp2_bps / 1e4
        if pot < self.cfg.monster_min_tp2_pct:
            return False, "Benz mode: TP2 potential %.0f%% of equity < %.0f%%" \
                % (pot * 100, self.cfg.monster_min_tp2_pct * 100)
        return True, ""

    def pre_prime_stand_down(self, now_ms: int) -> bool:
        """Slot hygiene: in the warmup minutes before a prime window,
        stop opening NEW entries so the book has free slots when the
        session actually fires.  Existing positions always ride their own
        stops / targets / trails -- we never force-close anything."""
        if not self.cfg.session_filter_enabled or self.cfg.prime_warmup_min <= 0:
            return False
        utc = time.gmtime(now_ms / 1000.0)
        hm = utc.tm_hour * 60 + utc.tm_min
        for lo, _hi in self.cfg.session_prime_utc:
            if lo - self.cfg.prime_warmup_min <= hm < lo:
                return True
        return False

    # ---- module 5: news blackout -----------------------------------------
    def news_ok(self, now_ms: int) -> bool:
        if not self.cfg.news_blackout_enabled:
            return True
        utc = time.gmtime(now_ms / 1000.0)
        hm = utc.tm_hour * 60 + utc.tm_min
        for lo, hi in self.cfg.news_blackout_utc:
            if lo <= hm < hi:
                return False
        if self.cfg.news_calendar_url:
            self._refresh_calendar()
            for t0, t1 in self._news_blocks:
                if t0 <= now_ms <= t1:
                    return False
        return True

    def _refresh_calendar(self) -> None:
        """Best-effort, non-fatal: block high-impact USD events +/- lead.
        Cached for 15 minutes; a failed fetch never blocks trading."""
        now = time.time()
        if now - self._news_fetched_at < 900:
            return
        self._news_fetched_at = now
        try:
            req = urllib.request.Request(self.cfg.news_calendar_url,
                                         headers={"User-Agent": "ict-sniper/1"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
            blocks = []
            lead = self.cfg.news_lead_min * 60 * 1000
            for ev in data if isinstance(data, list) else []:
                if str(ev.get("impact", "")).lower() != "high":
                    continue
                if str(ev.get("country", "")) not in ("USD", "US"):
                    continue
                ts = ev.get("date") or ev.get("timestamp")
                if not ts:
                    continue
                try:
                    t = int(float(ts)) * 1000 if float(ts) < 1e12 \
                        else int(float(ts))
                except (TypeError, ValueError):
                    continue
                blocks.append((t - lead, t + lead))
            self._news_blocks = blocks
            if blocks:
                LOG.info("[NEWS] %d high-impact USD block(s) loaded", len(blocks))
        except Exception as e:                    # noqa: BLE001
            self._news_blocks = []
            LOG.warning("[NEWS] calendar fetch failed: %s", e)


@dataclass
class Position:
    coin: str
    side: int
    entry_px: float
    qty: float                    # CURRENT remainder qty
    sl_px: float
    be_trigger_px: float
    tp1_px: float
    tp2_px: float
    tp1_qty: float
    tp2_qty: float = 0.0          # v3 tri-tier: middle 25% (TP2 target)
    runner_qty: float = 0.0       # v3 tri-tier: final 25% (trail only)
    fill_t: int = 0
    bars_held: int = 0
    scaled: bool = False
    be_armed: bool = False
    legs: List[Leg] = field(default_factory=list)
    oid: int = 0                  # live-mode order id
    lev: float = 20.0             # module 1: per-trade dynamic leverage
    trail_mode: bool = False      # module 4: velocity trail on the runner
    trail_ref: float = 0.0        # pivot: prev-candle low (long) / high
    mfe_r: float = 0.0            # max favorable excursion, in R units

    @property
    def risk(self) -> float:
        return abs(self.entry_px - self.sl_px)


@dataclass
class EntryOrder:
    coin: str
    side: int
    limit_px: float
    qty: float
    signal_t: int                # bar open time of the signal candle
    placed_t: int                # first bar it may fill
    entry_px: float
    sl_px: float
    be_trigger_px: float
    tp1_px: float
    tp2_px: float
    tp1_qty: float
    tp2_qty: float = 0.0          # v3 tri-tier: middle 25% (TP2 target)
    runner_qty: float = 0.0       # v3 tri-tier: final 25% (trail only)
    oid: int = 0
    lev: float = 20.0             # module 1: per-trade dynamic leverage


class PaperBook:
    """Virtual order book + positions + per-(coin, day) guards."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.orders: Dict[str, EntryOrder] = {}     # coin -> resting entry
        self.positions: Dict[str, Position] = {}
        self.realized_pnl: float = 0.0
        self.n_signals = 0
        self.n_fills = 0
        self.n_exits = 0
        self._day_profit: Dict[Tuple[str, int], float] = {}
        self._streak: Dict[Tuple[str, int], int] = {}
        self._day_signals: Dict[Tuple[str, int], int] = {}   # v4 frequency
        self._last_signal_t: Dict[str, int] = {}             # v4 cooldown
        self._blocked: set = set()
        self.n_blocks = 0
        self.n_profit_hits = 0
        self.pub = None                      # Publisher (set by the engine)
        self.risk: Optional[RiskEngine] = None  # modules (set by the engine)
        self.bars_getter = None              # coin -> deque of bars

    # ---- event hook -----------------------------------------------------
    def _emit(self, kind: str, **kw) -> None:
        if self.pub is not None:
            self.pub.event(kind, **kw)

    # ---- guards ---------------------------------------------------------
    def blocked(self, coin: str, t_ms: int) -> bool:
        if (coin, day_key(t_ms)) in self._blocked:
            return True
        profit, streak = self._day_profit.get((coin, day_key(t_ms)), 0.0), \
            self._streak.get((coin, day_key(t_ms)), 0)
        return profit >= self.cfg.cap_bps or \
            streak >= self.cfg.breaker_losses

    def _on_leg(self, coin: str, leg: Leg):
        self.realized_pnl += leg.pnl
        self.n_exits += 1
        dk = day_key(leg.t_ms)
        key = (coin, dk)
        self._day_profit[key] = self._day_profit.get(key, 0.0) + leg.ret_bps
        if leg.pnl > 0:
            self._streak[key] = 0
        else:
            self._streak[key] = self._streak.get(key, 0) + 1
        if self._day_profit[key] >= self.cfg.cap_bps \
                and key not in self._blocked:
            self._blocked.add(key)
            self.n_profit_hits += 1
            LOG.info("[GUARD] %s halted for the day: +%.0f bps realized "
                     "(hit & run)", coin, self._day_profit[key])
            self._emit("guard", coin=coin, side=0,
                       why=f"hit & run: +{self._day_profit[key]:.0f} bps "
                           f"realized on {coin} -- halted for the day")
        if self._streak[key] >= self.cfg.breaker_losses \
                and key not in self._blocked:
            self._blocked.add(key)
            self.n_blocks += 1
            LOG.info("[GUARD] %s halted for the day: %d consecutive losing "
                     "legs", coin, self._streak[key])
            self._emit("guard", coin=coin, side=0,
                       why=f"circuit breaker: {self._streak[key]} "
                           f"consecutive losing legs on {coin} -- halted "
                           f"for the day")

    # ---- signal intake ---------------------------------------------------
    def on_signal(self, coin: str, side: int, entry: float, sl: float,
                  tp1: float, tp2: float, be_trigger: float, sz_dec: int,
                  mid: float, signal_t: int, place_fn,
                  leg_atr: float = None) -> bool:
        """Rest a virtual (or live) limit at the CE."""
        if coin in self.orders or coin in self.positions:
            return False
        if self.blocked(coin, signal_t):
            return False
        # v4 frequency discipline: at most macro_max_signals_day supreme
        # setups per asset per day, with a cooldown between attempts
        sig_key = (coin, day_key(signal_t))
        if self._day_signals.get(sig_key, 0) >= self.cfg.macro_max_signals_day:
            LOG.info("[SIGNAL] %s rejected: daily setup cap reached (%d/%d "
                     "on this asset)", coin,
                     self._day_signals.get(sig_key, 0),
                     self.cfg.macro_max_signals_day)
            return False
        gap_ms = self.cfg.macro_cooldown_bars * self.cfg.tf_min * MIN_MS
        last_t = self._last_signal_t.get(coin, 0)
        if last_t and signal_t - last_t < gap_ms:
            LOG.info("[SIGNAL] %s rejected: cooldown (%d/%d bars since the "
                     "last attempt)", coin,
                     (signal_t - last_t) // (self.cfg.tf_min * MIN_MS),
                     self.cfg.macro_cooldown_bars)
            return False
        if self.risk is not None:
            if self.risk.check_halt(signal_t):
                LOG.info("[SIGNAL] %s rejected: daily +%.0f%% target hit",
                         coin, self.cfg.daily_target_pct)
                return False
            if not self.risk.can_open(side):
                LOG.info("[SIGNAL] %s rejected: portfolio cap (%d positions, "
                         "max %d same-side)", coin, len(self.positions),
                         self.cfg.max_same_side)
                return False
            if not self.risk.regime_ok(coin):
                LOG.info("[SIGNAL] %s rejected: chop standby", coin)
                return False
            if not self.risk.news_ok(signal_t):
                LOG.info("[SIGNAL] %s rejected: news blackout window", coin)
                return False
            if not self.risk.session_ok(signal_t, leg_atr):
                st = self.risk.session_state(signal_t)
                LOG.info("[SIGNAL] %s rejected: %s session (%s UTC) -- outside "
                         "prime without monster structure (leg %s xATR)",
                         coin, st,
                         time.strftime("%H:%M", time.gmtime(signal_t / 1000.0)),
                         "%.1f" % leg_atr if leg_atr is not None else "-")
                return False
            if self.risk.pre_prime_stand_down(signal_t):
                LOG.info("[SIGNAL] %s rejected: pre-prime stand-down (slot "
                         "hygiene before the open)", coin)
                return False
        sl_bps = abs(entry - sl) / entry * 1e4
        if sl_bps < self.cfg.min_sl_bps:
            LOG.info("[SIGNAL] %s rejected: degenerate stop %.2f bps < %.1f "
                     "(sweep wick == entry)", coin, sl_bps,
                     self.cfg.min_sl_bps)
            return False
        if self.risk is not None:
            bias = self.risk.htf_bias(coin)
            if bias is not None and bias != side:
                LOG.info("[SIGNAL] %s rejected: HTF bias %s vs %s setup "
                         "(no counter-trend fading, module 1)", coin,
                         {1: "bullish", -1: "bearish"}.get(bias, "neutral"),
                         "LONG" if side > 0 else "SHORT")
                return False
        lev = self.risk.leverage_for(sl_bps) if self.risk is not None \
            else self.cfg.lev
        eq = self.risk.equity() if self.risk is not None \
            else self.cfg.paper_equity
        notional = eq * self.cfg.alloc * lev
        qty = round_sz(notional / mid, sz_dec)
        if qty * mid < self.cfg.min_notional:
            LOG.info("[SIGNAL] %s rejected: size %.6f below min notional",
                     coin, qty)
            return False
        tp2_bps_real = (tp2 / entry - 1.0) * side * 1e4
        if self.risk is not None:
            ok_m, why_m = self.risk.monster_gate_ok(leg_atr, lev,
                                                    tp2_bps_real)
            if not ok_m:
                LOG.info("[SIGNAL] %s rejected: %s", coin, why_m)
                return False
        tp1_qty = round_sz(qty * self.cfg.tp1_frac, sz_dec)
        tp2_qty = round_sz(qty * self.cfg.tp2_frac, sz_dec)
        runner_qty = round_sz(qty - tp1_qty - tp2_qty, sz_dec)
        if tp1_qty <= 0 or tp2_qty <= 0 or runner_qty <= 0:
            LOG.info("[SIGNAL] %s rejected: un-splittable size %.6f", coin,
                     qty)
            return False
        self.n_signals += 1
        self._day_signals[sig_key] = self._day_signals.get(sig_key, 0) + 1
        self._last_signal_t[coin] = signal_t
        order = EntryOrder(
            coin=coin, side=side, limit_px=round_px(entry), qty=qty,
            signal_t=signal_t, placed_t=signal_t + self.cfg.tf_min * MIN_MS,
            entry_px=entry, sl_px=sl, be_trigger_px=be_trigger,
            tp1_px=tp1, tp2_px=tp2, tp1_qty=tp1_qty, tp2_qty=tp2_qty,
            runner_qty=runner_qty, lev=lev)
        self.orders[coin] = order
        d = "LONG" if side > 0 else "SHORT"
        tp1_bps_real = (tp1 / entry - 1.0) * side * 1e4
        tp2_bps_real = (tp2 / entry - 1.0) * side * 1e4
        LOG.info("[SIGNAL] %s %s | entry limit %.6g | sl %.6g (%.1f bps) | "
                 "tp1 %.6g (+%.0f) | tp2 %.6g (+%.0f) | be @ %.6g | "
                 "qty %.6f (tp1 %.6f / tp2 %.6f / trail %.6f) | lev %.0fx",
                 coin, d, order.limit_px, sl, sl_bps,
                 tp1, tp1_bps_real, tp2, tp2_bps_real, be_trigger,
                 qty, tp1_qty, tp2_qty, runner_qty, lev)
        self._emit("signal", coin=coin, side=side, limit_px=order.limit_px,
                   sl_px=sl, sl_bps=abs(entry - sl) / entry * 1e4,
                   be_px=be_trigger)
        if place_fn is not None:
            oid = place_fn(order)
            if oid:
                order.oid = int(oid)
        return True

    # ---- candle processing ----------------------------------------------
    def on_candle(self, coin: str, bar: dict, eligible_fn) -> None:
        """Advance resting orders and positions on one CLOSED candle."""
        t_open = int(bar["t"])
        o, h, lo, c = (float(bar["o"]), float(bar["h"]), float(bar["l"]),
                      float(bar["c"]))
        # 1. resting entry limits
        order = self.orders.get(coin)
        if order is not None:
            expired = t_open - order.signal_t \
                >= self.cfg.retest_bars * self.cfg.tf_min * MIN_MS
            if expired:
                del self.orders[coin]
                LOG.info("[ORDER] %s entry limit expired (no retest)", coin)
            elif t_open >= order.placed_t:
                touched = (order.side > 0 and lo <= order.limit_px) or \
                          (order.side < 0 and h >= order.limit_px)
                if touched:
                    if eligible_fn and not eligible_fn(coin, t_open):
                        del self.orders[coin]
                        LOG.info("[ORDER] %s entry dropped: not in today's "
                                 "universe at fill time", coin)
                        return
                    if self.blocked(coin, t_open):
                        del self.orders[coin]
                        LOG.info("[ORDER] %s entry dropped: guard blocked",
                                 coin)
                        return
                    if self.risk is not None \
                            and not self.risk.can_open(order.side):
                        # the book filled up while this limit rested --
                        # re-check the portfolio cap AT FILL TIME
                        del self.orders[coin]
                        LOG.info("[ORDER] %s entry dropped at fill: "
                                 "portfolio cap", coin)
                        return
                    if self.risk is not None and not self.risk.vol_ok(coin):
                        # module 2: the retest bar shows no participation --
                        # keep the limit resting (it still expires on time)
                        LOG.info("[ORDER] %s retest NOT confirmed (no volume/"
                                 "range expansion on the trigger bar) -- "
                                 "limit stays", coin)
                    else:
                        del self.orders[coin]
                        self._open_position(coin, order, t_open)
        # 2. open positions
        pos = self.positions.get(coin)
        if pos is None:
            return
        if self.risk is not None and self.risk.flat_time_reached(t_open):
            # day-trader discipline: the session is over, close at market
            # (taker) and drop any resting limits -- nothing rides the
            # dead Asian night
            for oc in list(self.orders):
                del self.orders[oc]
                LOG.info("[FLAT] %s entry limit cancelled (end of day)", oc)
            self._close_remainder(coin, pos, c, "flat", t_open)
            return
        pos.bars_held += 1            # fill candle == bar 1 (engine parity)
        fill_bar = (t_open == pos.fill_t)
        # max favorable excursion (R units) -- the "is it working?" gauge.
        # the fill bar's own high/low may predate the fill, so only count
        # it from the bar AFTER the fill onward (conservative).
        if not fill_bar and pos.risk > 0:
            fav = ((h - pos.entry_px) / pos.risk if pos.side > 0
                   else (pos.entry_px - lo) / pos.risk)
            pos.mfe_r = max(pos.mfe_r, fav)
        # on the FILL bar a level only counts when the bar CLOSED beyond
        # it -- proof that price crossed it AFTER the fill (a same-bar
        # wick before the fill proves nothing)
        be_ok = (pos.side > 0 and c >= pos.be_trigger_px) or \
                (pos.side < 0 and c <= pos.be_trigger_px)
        tp1_ok = (pos.side > 0 and c >= pos.tp1_px) or \
                 (pos.side < 0 and c <= pos.tp1_px)
        if not fill_bar or be_ok:
            # pre-scale dynamic breakeven: MFE >= 0.75R -> stop to entry
            if not pos.scaled and not pos.be_armed:
                trig = pos.be_trigger_px
                if (pos.side > 0 and h >= trig) or \
                        (pos.side < 0 and lo <= trig):
                    pos.sl_px = pos.entry_px
                    pos.be_armed = True
                    LOG.info("[BE] %s stop -> breakeven @ %.6g (0.75R "
                             "trigger hit)", coin, pos.entry_px)
        if not fill_bar or tp1_ok:
            # TP1 scale-out (v3 tri-tier: 40% at TP1)
            if not pos.scaled:
                if (pos.side > 0 and h >= pos.tp1_px) or \
                        (pos.side < 0 and lo <= pos.tp1_px):
                    self._scale_tp1(coin, pos, t_open)
        # runner trail (v3: the final 25% is trail-managed from TP1 on;
        # module 4 keeps the velocity check as the accelerator that also
        # cancels the static TP2 for the middle 25%)
        if pos.trail_mode and not fill_bar and pos in self.positions.values():
            bars = self.bars_getter(coin) if self.bars_getter else None
            piv = self.cfg.trail_pivot_bars + 1
            if bars and len(bars) >= piv:
                seg = bars[-piv:-1]        # v4: ride an N-bar pivot, not the
                atr = self._atr(bars)      #     newest candle (breathing)
                if pos.side > 0:
                    ref = min(float(b["l"]) for b in seg)
                    pos.trail_ref = max(pos.trail_ref, ref)
                    pos.sl_px = max(pos.sl_px,
                                    self._trail_stop(1, pos.trail_ref, atr))
                else:
                    ref = max(float(b["h"]) for b in seg)
                    pos.trail_ref = min(pos.trail_ref, ref)
                    pos.sl_px = min(pos.sl_px,
                                    self._trail_stop(-1, pos.trail_ref, atr))
        # 3. stop (conservative: SL wins over TP2 when both touched).
        #    The FILL bar's own low filled the entry, so it cannot also
        #    stop the position in the same evaluation -- its low predates
        #    the fill.
        if pos in self.positions.values() and not fill_bar:
            hit_sl = (pos.side > 0 and lo <= pos.sl_px) or \
                     (pos.side < 0 and h >= pos.sl_px)
            if hit_sl:
                slip = bps(self.cfg.slip_bps)
                px = pos.sl_px * (1 - pos.side * slip)
                reason = ("trail" if pos.trail_mode
                          else "be" if pos.be_armed else "sl")
                self._close_remainder(coin, pos, px, reason, t_open)
                return
        # 4. TP2 tier (v3: closes ONLY the middle 25%; the final 25% keeps
        #    running under the trail.  Skipped when the velocity trail
        #    already owns the whole remainder.)
        if pos in self.positions.values() and pos.tp2_px is not None:
            if pos.scaled:
                hit = (pos.side > 0 and h >= pos.tp2_px) or \
                      (pos.side < 0 and lo <= pos.tp2_px)
                if fill_bar:
                    # the high may predate the fill: the close must prove
                    # the level was crossed AFTER entry
                    hit = hit and ((pos.side > 0 and c >= pos.tp2_px) or
                                   (pos.side < 0 and c <= pos.tp2_px))
            else:
                hit = (pos.side > 0 and o >= pos.tp2_px) or \
                      (pos.side < 0 and o <= pos.tp2_px)   # gap-open
            if hit:
                left = pos.qty - pos.tp2_qty
                if pos.scaled and pos.tp2_qty > 0 and left > 0:
                    self._scale_partial(coin, pos, pos.tp2_qty, pos.tp2_px,
                                        "tp2", t_open)
                    pos.tp2_qty = 0.0
                    pos.tp2_px = None    # tier consumed: the final 25% is
                                         # trail-only from here on
                else:
                    self._close_remainder(coin, pos, pos.tp2_px, "tp2",
                                          t_open)
                    return
        # 4b. no-follow-through scratch (market): an entry that has been
        #     underwater for N closed bars and never showed follow-through
        #     (MFE < scratch_min_mfe_r) is a failed sweep -- cut it before
        #     the market collects the full stop. Only pre-scale (the tiered
        #     phases have their own stop/trail); the stop check above still
        #     wins first when both would fire on the same bar.
        if pos in self.positions.values() and not pos.scaled and \
                not fill_bar and pos.bars_held >= self.cfg.scratch_after_bars \
                and pos.mfe_r < self.cfg.scratch_min_mfe_r:
            underwater = (pos.side > 0 and c < pos.entry_px) or \
                         (pos.side < 0 and c > pos.entry_px)
            if underwater:
                LOG.info("[SCRATCH] %s no follow-through in %d bars "
                         "(MFE %.2fR < %.2fR) -- cutting at market %.6g",
                         coin, pos.bars_held, pos.mfe_r,
                         self.cfg.scratch_min_mfe_r, c)
                self._close_remainder(coin, pos, c, "scratch", t_open)
                return
        # 5. time kill (market)
        if pos in self.positions.values() and \
                pos.bars_held >= self.cfg.time_exit_bars:
            self._close_remainder(coin, pos, c, "time", t_open)

    def _atr(self, bars) -> float:
        """Wilder ATR of the execution TF (0.0 when history is short)."""
        if not bars or len(bars) < self.cfg.atr_period + 1:
            return 0.0
        h = [float(b["h"]) for b in bars]
        lo = [float(b["l"]) for b in bars]
        c = [float(b["c"]) for b in bars]
        a = _wilder_atr(h, lo, c, self.cfg.atr_period)
        return float(a[-1]) if a and math.isfinite(a[-1]) else 0.0

    def _trail_stop(self, side: int, pivot: float, atr: float) -> float:
        """Stop just past the pivot with an ATR breathing-room buffer."""
        room = self.cfg.trail_min_atr * (atr or 0.0)
        return pivot - side * room

    def _open_position(self, coin: str, order: EntryOrder, t_open: int):
        pos = Position(
            coin=coin, side=order.side, entry_px=order.limit_px,
            qty=order.qty,
            sl_px=order.sl_px, be_trigger_px=order.be_trigger_px,
            tp1_px=order.tp1_px, tp2_px=order.tp2_px,
            tp1_qty=order.tp1_qty, tp2_qty=order.tp2_qty,
            runner_qty=order.runner_qty,
            fill_t=t_open, lev=order.lev)
        self.positions[coin] = pos
        self.n_fills += 1
        d = "LONG" if pos.side > 0 else "SHORT"
        LOG.info("[FILL] %s %s %.6f @ %.6g (maker entry, %.0fx) | sl %.6g | "
                 "be %.6g | tp1 %.6g | tp2 %.6g",
                 coin, d, pos.qty, pos.entry_px, pos.lev, pos.sl_px,
                 pos.be_trigger_px, pos.tp1_px, pos.tp2_px)
        self._emit("fill", coin=coin, side=pos.side, qty=pos.qty,
                   px=pos.entry_px, sl_px=pos.sl_px, tp1_px=pos.tp1_px,
                   tp2_px=pos.tp2_px)

    def _scale_tp1(self, coin: str, pos: Position, t_open: int):
        """v3 tri-tier TP1: realize 50% at the limit, SL -> breakeven and
        arm the pivot trail for the final 25%.  A fast TP1 (module 4) also
        cancels the static TP2 so the middle 25% rides the trail too."""
        px = pos.tp1_px
        qty = min(pos.tp1_qty, pos.qty)
        if qty <= 0:
            return
        fee = qty * px * bps(self.cfg.maker_exit_bps)
        gross = qty * (px - pos.entry_px) * pos.side
        pnl = gross - fee
        ret_bps = (px / pos.entry_px - 1) * pos.side * 1e4
        pos.legs.append(Leg("tp1", qty, pos.entry_px, px, fee, pnl,
                            ret_bps, t_open))
        if self.pub is not None:
            self.pub.state.log_trade(coin, pos.side, "tp1", qty,
                                     pos.entry_px, px, ret_bps, pnl, fee,
                                     t_open, pos.fill_t)
        pos.qty -= qty
        pos.scaled = True
        # the remaining book is tp2 (middle 25%) + the trailed final 25%
        pos.tp2_qty = min(pos.tp2_qty, pos.qty)
        pos.runner_qty = max(0.0, pos.qty - pos.tp2_qty)
        bars = self.bars_getter(coin) if self.bars_getter else None
        # v4: the final runner is ALWAYS trail-managed off an N-bar pivot
        # with an ATR buffer; the stop only ever ratchets, never loosens
        piv = self.cfg.trail_pivot_bars + 1
        if bars and len(bars) >= piv:
            seg = bars[-piv:-1]
            pos.trail_ref = min(float(b["l"]) for b in seg) if pos.side > 0 \
                else max(float(b["h"]) for b in seg)
        pos.trail_mode = True
        atr = self._atr(bars) if bars else 0.0
        stop = self._trail_stop(pos.side, pos.trail_ref or pos.entry_px, atr)
        if pos.side > 0:
            pos.sl_px = max(pos.entry_px, stop)
        else:
            pos.sl_px = min(pos.entry_px, stop)
        pos.be_armed = True
        fast = (self.risk is not None
                and self.risk.velocity_fast(coin, pos.side))
        if fast and self.cfg.velocity_cancel_tp2:
            pos.tp2_px = None            # legacy rule, opt-in only
            LOG.info("[TRAIL] %s TP1 was FAST -- TP2 cancelled (legacy), "
                     "the whole %.6f remainder rides the pivot trail",
                     coin, pos.qty)
            self._emit("trail", coin=coin, side=pos.side,
                       qty=pos.qty, px=px)
        self._on_leg(coin, pos.legs[-1])
        LOG.info("[TP1] %s scaled out %.6f @ %.6g (+%.1f bps, pnl %+.2f) | "
                 "left %.6f (tp2 %.6f / trail %.6f) -> SL breakeven%s",
                 coin, qty, px, ret_bps, pnl, pos.qty, pos.tp2_qty,
                 pos.runner_qty, " [fast: trail owns all]" if fast else "")
        self._emit("tp1", coin=coin, side=pos.side, qty=qty, px=px,
                   ret_bps=ret_bps, pnl=pnl, left=pos.qty)

    def _scale_partial(self, coin: str, pos: Position, qty: float, px: float,
                       reason: str, t_open: int):
        """Close PART of the remainder (v3 TP2: the middle 25% only) and
        keep the rest of the position running."""
        qty = min(qty, pos.qty)
        if qty <= 0:
            return
        maker = reason == "tp2"
        fee_bps = self.cfg.maker_exit_bps if maker else self.cfg.taker_bps
        fee = qty * px * bps(fee_bps)
        gross = qty * (px - pos.entry_px) * pos.side
        pnl = gross - fee
        ret_bps = (px / pos.entry_px - 1) * pos.side * 1e4
        pos.legs.append(Leg(reason, qty, pos.entry_px, px, fee, pnl,
                            ret_bps, t_open))
        if self.pub is not None:
            self.pub.state.log_trade(coin, pos.side, reason, qty,
                                     pos.entry_px, px, ret_bps, pnl, fee,
                                     t_open, pos.fill_t)
        pos.qty -= qty
        self._on_leg(coin, pos.legs[-1])
        LOG.info("[%s] %s scaled out %.6f @ %.6g (%.1f bps, pnl %+.2f) | "
                 "trail runner %.6f keeps running",
                 reason.upper(), coin, qty, px, ret_bps, pnl, pos.qty)
        if reason == "tp2":
            self._emit("exit", coin=coin, side=pos.side, qty=qty, px=px,
                       ret_bps=ret_bps, pnl=pnl, reason="TP2",
                       bars=pos.bars_held)
        if pos.qty <= 0:
            del self.positions[coin]

    def _close_remainder(self, coin: str, pos: Position, px: float,
                         reason: str, t_open: int):
        qty = pos.qty
        if qty <= 0:
            del self.positions[coin]
            return
        maker = reason == "tp2"
        fee_bps = self.cfg.maker_exit_bps if maker else self.cfg.taker_bps
        fee = qty * px * bps(fee_bps)
        gross = qty * (px - pos.entry_px) * pos.side
        pnl = gross - fee
        ret_bps = (px / pos.entry_px - 1) * pos.side * 1e4
        pos.legs.append(Leg(reason, qty, pos.entry_px, px, fee, pnl,
                            ret_bps, t_open))
        if self.pub is not None:
            self.pub.state.log_trade(coin, pos.side, reason, qty,
                                     pos.entry_px, px, ret_bps, pnl, fee,
                                     t_open, pos.fill_t)
        pos.qty = 0.0
        self._on_leg(coin, pos.legs[-1])
        LOG.info("[EXIT:%s] %s %.6f @ %.6g (%+.1f bps, pnl %+.2f) | "
                 "bars held %d", reason.upper(), coin, qty, px, ret_bps,
                 pnl, pos.bars_held)
        self._emit("exit", coin=coin, side=pos.side, reason=reason, qty=qty,
                   px=px, ret_bps=ret_bps, pnl=pnl, bars=pos.bars_held,
                   day_bps=self._day_profit.get(
                       (coin, day_key(t_open)), 0.0))
        del self.positions[coin]


# ----------------------------------------------------------------------
# Universe scanner (atrscan.py doctrine on Hyperliquid).
# ----------------------------------------------------------------------
def atr_pct_15m(candles: List[dict], bars: int = 14) -> Optional[float]:
    """Mean TRUE range over `bars` 15m candles as % of the last price."""
    if len(candles) < bars + 1:
        return None
    trs = []
    prev = None
    for cd in candles:
        h, lo, c = float(cd["h"]), float(cd["l"]), float(cd["c"])
        tr = max(h - lo, abs(h - prev), abs(lo - prev)) \
            if prev is not None else h - lo
        trs.append(tr)
        prev = c
    last = float(candles[-1]["c"])
    if last <= 0:
        return None
    return sum(trs[-bars:]) / bars / last * 100.0


def _interval_ms(interval: str) -> int:
    if interval.endswith("m"):
        return int(interval[:-1]) * MIN_MS
    if interval.endswith("h"):
        return int(interval[:-1]) * 60 * MIN_MS
    return 5 * MIN_MS


async def _retry_fetch(fn, *args, attempts: int = 4, base_s: float = 2.0):
    """Run a blocking Info endpoint in the executor, backing off on 429.

    The wide universe fires hundreds of candle snapshots per refresh; a
    burst that trips the venue's rate limiter must not silently drop a
    coin's bars (that coin then can never produce a setup).  Non-429
    errors propagate unchanged."""
    loop = asyncio.get_running_loop()
    for i in range(attempts):
        try:
            return await loop.run_in_executor(None, fn, *args)
        except Exception as e:                   # noqa: BLE001
            if "429" not in str(e) or i == attempts - 1:
                raise
            wait = base_s * (2 ** i)
            LOG.warning("[DATA] 429 from the venue -- retrying in %.1fs "
                        "(%d/%d)", wait, i + 1, attempts)
            await asyncio.sleep(wait)


class UniverseScanner:
    def _interval(self) -> str:
        return f"{self.cfg.tf_min}m"

    def __init__(self, cfg: Config, info: Info):
        self.cfg = cfg
        self.info = info
        self.meta: Dict[str, dict] = {}
        self.universe: Dict[str, dict] = {}    # name -> meta row
        self.atr_pct: Dict[str, float] = {}

    async def _fetch(self, coin: str, interval: str, n: int,
                     sem: asyncio.Semaphore) -> List[dict]:
        async with sem:
            if self.cfg.data_pace_s > 0:
                await asyncio.sleep(self.cfg.data_pace_s)   # pace the burst
            now = int(time.time() * 1000)
            start = now - n * _interval_ms(interval) * 2
            return await _retry_fetch(self.info.candles_snapshot,
                                      coin, interval, start, now)

    async def refresh(self) -> Dict[str, dict]:
        """Rescan the market; returns the new top-N universe."""
        loop = asyncio.get_running_loop()
        meta, ctxs = await loop.run_in_executor(
            None, self.info.meta_and_asset_ctxs)
        universe_rows = meta["universe"]
        self.meta = {u.get("name", ""): u for u in universe_rows}
        cands: List[Tuple[str, float]] = []
        for i, u in enumerate(universe_rows):
            name = u.get("name", "")
            if not name:
                continue
            vol = 0.0
            if i < len(ctxs):
                try:
                    vol = float(ctxs[i].get("dayNtlVlm") or 0.0)
                except (TypeError, ValueError):
                    vol = 0.0
            if vol >= self.cfg.min_vol_usdt:
                cands.append((name, vol))
        cands.sort(key=lambda x: -x[1])
        cands = cands[:self.cfg.scan_candidates]
        LOG.info("[UNIVERSE] scanning %d volume-qualified candidates ...",
                 len(cands))
        sem = asyncio.Semaphore(3)
        results = await asyncio.gather(
            *[self._fetch(name, self._interval(), 20, sem)
              for name, _ in cands],
            return_exceptions=True)
        rows: List[Tuple[str, float]] = []
        for (name, _), res in zip(cands, results):
            if isinstance(res, BaseException) or not res:
                continue
            a = atr_pct_15m(res, 14)
            if a is not None and a >= self.cfg.min_atr_pct:
                rows.append((name, a))
        rows.sort(key=lambda x: -x[1])
        self.universe = {name: self.meta[name] for name, _ in
                         rows[:self.cfg.top_n]}
        self.atr_pct = {name: a for name, a in rows}
        LOG.info("[UNIVERSE] top-%d by ATR%%: %s", len(self.universe),
                 ", ".join(f"{n}({self.atr_pct.get(n, 0):.1f}%)"
                           for n in list(self.universe)[:12]))
        return self.universe


# ----------------------------------------------------------------------
# Engine: asyncio + Hyperliquid WebSockets + the paper state machine.
# ----------------------------------------------------------------------
class SniperEngine:
    def _interval(self) -> str:
        return f"{self.cfg.tf_min}m"

    def __init__(self, cfg: Config, address: str, use_ws: bool = True):
        self.cfg = cfg
        self.address = address
        self.info = Info(hl_constants.MAINNET_API_URL, skip_ws=True)
        self.scanner = UniverseScanner(cfg, self.info)
        self.book = PaperBook(cfg)
        self.q: "queue.Queue[tuple]" = queue.Queue()
        self.ws = None
        self.use_ws = use_ws
        self.mids: Dict[str, float] = {}
        self.bars: Dict[str, Deque[dict]] = {}
        self.last_bar_t: Dict[str, int] = {}
        self.signal_key: Dict[str, tuple] = {}   # dedupe: (side, sweep_t,
                                                 #          entry)
        self.watched: set = set()
        self.exchange = None                     # live mode only
        self._shutdown = False
        # --- alerts + phone-app state -----------------------------------
        env = load_agent_env(BASE_DIR / ".env")
        topic = cfg.ntfy_topic or os.getenv("NTFY_TOPIC") \
            or env.get("NTFY_TOPIC", "")
        self.notifier = Notifier(topic, enabled=cfg.notify)
        self.state = AppState(Path(cfg.data_dir), cfg)
        self.pub = Publisher(cfg, self.notifier, self.state,
                             lambda: self.book, lambda: self.mids)
        self.book.pub = self.pub
        self.risk = RiskEngine(cfg, lambda: self.book, lambda: self.mids,
                               lambda c: list(self.bars.get(c, [])),
                               self.notifier)
        self.book.risk = self.risk
        self.book.bars_getter = self.risk._bars
        restore_day_state(cfg, self.book, self.risk, self.state)
        self._last_day = day_key(int(time.time() * 1000))

    # ---------------- WebSocket bridge (SDK threads -> queue) -----------
    def _start_ws(self):
        try:
            from hyperliquid.websocket_manager import WebsocketManager
            self.ws = WebsocketManager(hl_constants.MAINNET_API_URL)
            self.ws.start()
            self.ws.subscribe({"type": "allMids"}, self._cb_mids)
            self.ws.subscribe({"type": "userFills", "user": self.address},
                              self._cb_fills)
            LOG.info("[WS] allMids + userFills subscribed (address %s)",
                     self.address)
        except Exception as e:                   # noqa: BLE001
            LOG.warning("[WS] websocket unavailable (%s) -- REST polling "
                        "only", e)
            self.ws = None

    def _cb_mids(self, data):
        self.q.put(("mids", data))

    def _cb_fills(self, data):
        # Paper mode: a read-only wallet never fills.  In LIVE mode this
        # stream drives the real-fill state machine (see _on_external_fill).
        self.q.put(("fills", data))

    def _on_external_fill(self, fill: dict):
        """LIVE-mode fill integration point.  Paper mode logs only."""
        coin = fill.get("coin", "?")
        px = fill.get("px", "0")
        sz = fill.get("sz", "0")
        LOG.info("[WS-FILL] %s px=%s sz=%s (paper mode: not applied to "
                 "the virtual book)", coin, px, sz)

    # ---------------- candle feed (REST poll) ----------------------------
    async def _backfill(self, coin: str) -> bool:
        now = int(time.time() * 1000)
        start = now - self.cfg.history_bars * self.cfg.tf_min * MIN_MS * 2
        try:
            candles = await _retry_fetch(self.info.candles_snapshot,
                                         coin, self._interval(), start,
                                         now)
        except Exception as e:                   # noqa: BLE001
            LOG.warning("[DATA] backfill %s failed: %s", coin, e)
            return False
        if not candles:
            return False
        closed = [cd for cd in candles if int(cd["T"]) <= now]
        if not closed:
            return False
        self.bars[coin] = deque(closed[-self.cfg.history_bars:],
                                maxlen=self.cfg.history_bars * 2)
        self.last_bar_t[coin] = int(closed[-1]["t"])
        return True

    async def _poll_candles(self):
        while not self._shutdown:
            for coin in list(self.watched):
                try:
                    await self._poll_one(coin)
                except Exception as e:           # noqa: BLE001
                    LOG.debug("[DATA] poll %s: %s", coin, e)
                if self.cfg.data_pace_s > 0:
                    await asyncio.sleep(self.cfg.data_pace_s)
            await asyncio.sleep(self.cfg.candle_poll_s)

    async def _poll_one(self, coin: str):
        loop = asyncio.get_running_loop()
        now = int(time.time() * 1000)
        last = self.last_bar_t.get(coin, 0)
        start = max(0, last - self.cfg.tf_min * MIN_MS)
        candles = await loop.run_in_executor(
            None, self.info.candles_snapshot, coin, self._interval(),
            start, now)
        for cd in candles:
            t = int(cd["t"])
            if t > last and int(cd["T"]) <= now:
                self._on_closed_bar(coin, cd)

    def _on_closed_bar(self, coin: str, bar: dict):
        """Feed one closed 5m candle through the state machine."""
        t = int(bar["t"])
        self.bars.setdefault(coin, deque(maxlen=2000)).append(bar)
        self.last_bar_t[coin] = t
        self.risk.roll_day(t)
        if self.risk.check_halt(t):
            # module 1: +100% for the day -> cancel every resting order and
            # stop scanning; open positions ride their existing stops
            for c in list(self.book.orders):
                del self.book.orders[c]
                LOG.info("[HALT] %s entry limit cancelled (daily target)",
                         c)
            self.book.on_candle(coin, bar, self._eligible_now)
            self.pub.refresh()
            return
        self.book.on_candle(coin, bar, self._eligible_now)
        self.pub.refresh()
        if coin in self.book.orders or coin in self.book.positions:
            return                                   # one trade at a time
        if not self._eligible_now(coin, t):
            return
        if self.cfg.detector == "macro_shadow":
            self._shadow_macro(coin)
            state = analyze_bars(list(self.bars[coin]), self.cfg)
        else:
            state = detect(list(self.bars[coin]), self.cfg)
        if not state.get("valid"):
            return
        key = (state["side"], state["sweep_bar_t"], round(state["entry"], 8))
        if self.signal_key.get(coin) == key:
            return                                   # same setup, no re-fire
        self.signal_key[coin] = key
        row = self.scanner.meta.get(coin, {})
        sz_dec = int(row.get("szDecimals", 5) or 5)
        mid = self.mids.get(coin) or float(bar["c"])
        place = self._make_place_fn(coin) if self.cfg.live else None
        self.book.on_signal(
            coin, int(state["side"]), float(state["entry"]),
            float(state["sl"]), float(state["tp1"]), float(state["tp2"]),
            float(state["be_trigger"]), sz_dec, mid, t, place,
            state.get("leg_atr"))

    def _shadow_macro(self, coin: str) -> None:
        """Log-only: what the v4 macro engine would take right now, while
        the live book keeps running the phase-7 core (evidence first)."""
        st = analyze_macro(list(self.bars.get(coin, [])), self.cfg)
        if not st.get("valid"):
            return
        bias = self.risk.htf_bias(coin)
        if bias is not None and bias != st["side"]:
            return
        if not self.risk.session_ok(int(st["sweep_bar_t"])):
            return
        lev = self.risk.leverage_for(st["sl_bps"])
        LOG.info("[SHADOW] %s %s would trade: entry %.6g sl %.6g (%.0f bps) "
                 "tp1 %.6g tp2 %.6g | leg %.1fxATR retr %.0f%% | lev %.0fx",
                 coin, "LONG" if st["side"] > 0 else "SHORT", st["entry"],
                 st["sl"], st["sl_bps"], st["tp1"], st["tp2"],
                 st.get("leg_atr", 0.0), st.get("retr", 0.0) * 100, lev)

    def _eligible_now(self, coin: str, t_ms: int) -> bool:
        """Dynamic universe: the coin must be in the CURRENT top-N set
        (the live approximation of the backtest's per-day eligibility)."""
        return coin in self.scanner.universe

    def _make_place_fn(self, coin: str):
        """LIVE mode only: place the entry limit via the SDK.  Refuses to
        return a placer in paper mode, so no code path can sign an order."""
        if not self.cfg.live or self.exchange is None:
            raise RuntimeError(
                "SAFETY: refusing to build an order placer outside live mode")
        def place(order: EntryOrder) -> Optional[int]:
            try:
                res = self.exchange.order(
                    order.coin, order.side > 0, order.qty, order.limit_px,
                    {"limit": {"tif": "Gtc"}})
                if res.get("status") == "ok":
                    for st in res["response"]["data"]["statuses"]:
                        if "resting" in st:
                            return int(st["resting"]["oid"])
                LOG.error("[LIVE] order rejected: %s", res)
            except Exception as e:               # noqa: BLE001
                LOG.error("[LIVE] order failed: %s", e)
            return None
        return place

    # ---------------- main loop ------------------------------------------
    async def run(self):
        LOG.info("=== ICT SNIPER bot starting | %s engine | hunting TF %dm (structure) + %dm (trend) | tiers %.0f/%.0f/%.0f ===",
                 self.cfg.detector, self.cfg.tf_min, self.cfg.htf_minutes,
                 self.cfg.tp1_frac * 100, self.cfg.tp2_frac * 100,
                 (1 - self.cfg.tp1_frac - self.cfg.tp2_frac) * 100)
        if self.cfg.live:
            LOG.warning("[SAFETY] *** LIVE MODE: REAL ORDERS, REAL MONEY ***")
        else:
            if self.exchange is not None:
                raise RuntimeError("SAFETY: paper mode holds an exchange "
                                   "client -- refusing to start")
            LOG.info("[SAFETY] PAPER MODE: no signing client, no private key "
                     "loaded, no order can be signed. Fills are simulated "
                     "from closed %dm candles. Lock: %s", self.cfg.tf_min,
                     "ICT_PAPER_ONLY" if self.cfg.paper_only
                     else "(flags only)")
        LOG.info("address=%s equity=%.0f alloc=%.1f lev=%.0f top_n=%d "
                 "min_atr=%.1f%%", self.address, self.cfg.paper_equity,
                 self.cfg.alloc, self.cfg.lev, self.cfg.top_n,
                 self.cfg.min_atr_pct)
        if self.use_ws:
            self._start_ws()
        LOG.info("[ALERTS] ntfy %s | app state -> %s",
                 ("topic set" if self.notifier.enabled else "DISABLED"),
                 self.state.state_path)
        self.pub.event("boot", why=(
            f"paper session up | top-{self.cfg.top_n} ATR>="
            f"{self.cfg.min_atr_pct}% | tp1 {self.cfg.tp1_bps:.0f}bps "
            f"{self.cfg.tp1_frac * 100:.0f}% | tp2 {self.cfg.tp2_bps:.0f}bps"
            f" | equity {self.cfg.paper_equity:.0f}"))
        await self._refresh_universe()
        poller = asyncio.create_task(self._poll_candles())
        refresher = asyncio.create_task(self._universe_loop())
        status = asyncio.create_task(self._status_loop())
        liveloop = asyncio.create_task(self._live_price_loop())
        try:
            while not self._shutdown:
                await self._drain_ws()
                await asyncio.sleep(0.5)
        finally:
            for task in (poller, refresher, status, liveloop):
                task.cancel()
            await asyncio.gather(poller, refresher, status, liveloop,
                                 return_exceptions=True)
            if self.ws is not None:
                try:
                    self.ws.stop()
                except Exception:                # noqa: BLE001
                    pass
            self._print_summary()

    async def _drain_ws(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "mids":
                    # SDK delivers the whole ws message here:
                    # {"channel":"allMids","data":{"mids":{COIN: px, ...}}}
                    payload = data.get("data", data) \
                        if isinstance(data, dict) else {}
                    mids = payload.get("mids", payload) \
                        if isinstance(payload, dict) else {}
                    if isinstance(mids, dict):
                        for coin, px in mids.items():
                            try:
                                self.mids[coin] = float(px)
                            except (TypeError, ValueError):
                                pass
                elif kind == "fills":
                    fill = data.get("data", {}) if isinstance(data, dict) \
                        else {}
                    if isinstance(fill, dict) and fill.get("isFill"):
                        self._on_external_fill(fill)
        except queue.Empty:
            pass

    async def _refresh_universe(self):
        try:
            universe = await self.scanner.refresh()
        except Exception as e:                   # noqa: BLE001
            LOG.error("[UNIVERSE] refresh failed: %s", e)
            return
        for coin in list(universe):
            if coin not in self.bars:
                await self._backfill(coin)
                if self.cfg.data_pace_s > 0:
                    await asyncio.sleep(self.cfg.data_pace_s)
        for coin in list(self.bars):
            if coin not in universe and coin not in self.book.positions \
                    and coin not in self.book.orders:
                del self.bars[coin]
                self.last_bar_t.pop(coin, None)
        self.watched = set(self.bars) | set(self.book.positions)
        missing = [c for c in universe if c not in self.bars]
        if missing:
            LOG.warning("[DATA] %d/%d universe coins have NO bars yet "
                        "(will retry next refresh): %s", len(missing),
                        len(universe), ",".join(missing[:8]))
        else:
            LOG.info("[DATA] all %d universe coins loaded and polling",
                     len(universe))

    async def _universe_loop(self):
        while not self._shutdown:
            await asyncio.sleep(self.cfg.universe_refresh_s)
            await self._refresh_universe()

    async def _live_price_loop(self):
        """Exchange-app feel: with an open position, refresh the app
        state every live_publish_s so the card's floating PnL, the ROI%
        and the trail stop all tick in near-real time."""
        while not self._shutdown:
            await asyncio.sleep(self.cfg.live_publish_s)
            if self.book.positions:
                self.pub.refresh()

    async def _status_loop(self):
        while not self._shutdown:
            await asyncio.sleep(self.cfg.status_s)
            self.pub.refresh()
            self._maybe_day_summary()
            LOG.info("[STATUS] signals=%d fills=%d exits=%d | open_pos=%d "
                     "resting=%d | realized_pnl=%+.2f | blocks=%d "
                     "cap_hits=%d | ntfy=%d/%d", self.book.n_signals,
                     self.book.n_fills,
                     self.book.n_exits, len(self.book.positions),
                     len(self.book.orders), self.book.realized_pnl,
                     self.book.n_blocks, self.book.n_profit_hits,
                     self.notifier.sent, self.notifier.failed)
            for coin, p in self.book.positions.items():
                mid = self.mids.get(coin)
                upnl = p.qty * (mid - p.entry_px) * p.side if mid else 0.0
                LOG.info("  pos %s %s qty=%.6f entry=%.6g sl=%.6g "
                         "upnl=%+.2f bars=%d", coin,
                         "L" if p.side > 0 else "S", p.qty, p.entry_px,
                         p.sl_px, upnl, p.bars_held)

    def _maybe_day_summary(self) -> None:
        """One ntfy at each UTC day rollover: the day's realized PnL."""
        now = int(time.time() * 1000)
        dk = day_key(now)
        if dk == self._last_day:
            return
        day_bps = sum(b for (c, d), b in self.book._day_profit.items()
                      if d == self._last_day)
        equity = self.cfg.paper_equity + self.book.realized_pnl
        self.pub.event("day", why=(
            f"equity {equity:+.2f} | last day realized {day_bps:+.0f} bps "
            f"| legs {self.book.n_exits} | halts {self.book.n_blocks + self.book.n_profit_hits}"))
        self._last_day = dk

    def _print_summary(self):
        LOG.info("=== session summary ===")
        LOG.info("signals=%d fills=%d exits=%d realized_pnl=%+.2f",
                 self.book.n_signals, self.book.n_fills, self.book.n_exits,
                 self.book.realized_pnl)
        LOG.info("breaker halts=%d hit&run halts=%d",
                 self.book.n_blocks, self.book.n_profit_hits)

    def shutdown(self):
        self._shutdown = True


# ----------------------------------------------------------------------
# entry point
# ----------------------------------------------------------------------
def setup_logging(log_file: str = "bot_execution.log"):
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = RotatingFileHandler(log_file, maxBytes=10_000_000, backupCount=5)
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="ICT Sniper on Hyperliquid -- paper trading "
                    "(phase-7 production spec).")
    ap.add_argument("--address", default=None,
                    help="READ-ONLY wallet address for paper mode")
    ap.add_argument("--equity", type=float, default=None,
                    help="paper starting equity (default 10000)")
    ap.add_argument("--top-n", type=int, default=None)
    ap.add_argument("--min-atr-pct", type=float, default=None)
    ap.add_argument("--log", default="bot_execution.log")
    ap.add_argument("--data-dir", default=None,
                    help="app data dir (SCALPER_DATA); state/*.json land "
                         "there")
    ap.add_argument("--ntfy-topic", default=None,
                    help="ntfy.sh topic (default NTFY_TOPIC from .env)")
    ap.add_argument("--no-ntfy", action="store_true", help="no push alerts")
    ap.add_argument("--no-state", action="store_true",
                    help="do not write paper.json/live.json")
    ap.add_argument("--test-notify", action="store_true",
                    help="send one ntfy test message and exit")
    ap.add_argument("--no-ws", action="store_true",
                    help="disable websockets (REST polling only)")
    ap.add_argument("--smoke", action="store_true",
                    help="one-shot: scan a small universe, backfill 8 "
                         "coins, run the detector once, exit")
    ap.add_argument("--live", action="store_true",
                    help="PLACE REAL ORDERS (requires --secret-key)")
    ap.add_argument("--secret-key", default=None,
                    help="Hyperliquid wallet secret (LIVE mode only)")
    return ap.parse_args()


async def smoke(cfg: Config, engine: SniperEngine):
    cfg.scan_candidates = min(cfg.scan_candidates, 40)
    cfg.top_n = min(cfg.top_n, 20)
    await engine._refresh_universe()
    coins = list(engine.scanner.universe)[:8]
    for coin in coins:
        await engine._backfill(coin)
    for coin in coins:
        bars = list(engine.bars.get(coin, []))
        if len(bars) < 120:
            LOG.info("[SMOKE] %s: only %d bars", coin, len(bars))
            continue
        state = detect(bars, cfg)
        if state.get("valid"):
            LOG.info("[SMOKE] %s WOULD TRADE: %s entry=%.6g sl=%.6g "
                     "(%.1f bps) tp1=%.6g tp2=%.6g be=%.6g",
                     coin, "LONG" if state["side"] > 0 else "SHORT",
                     state["entry"], state["sl"], state["sl_bps"],
                     state["tp1"], state["tp2"], state["be_trigger"])
        else:
            LOG.info("[SMOKE] %s: no valid setup on the latest bar "
                     "(%d bars)", coin, len(bars))
    LOG.info("[SMOKE] detector verified -- run without --smoke for the "
             "full paper session")


def _enable_live(engine: SniperEngine, secret_key: str, address: str):
    """LIVE mode: construct the signed exchange client.  Real fills then
    arrive via the userFills websocket (_on_external_fill)."""
    from hyperliquid.exchange import Exchange
    from eth_account import Account as EthAccount
    try:
        wallet = EthAccount.from_key(secret_key)     # signs nothing by itself
        engine.exchange = Exchange(wallet, hl_constants.MAINNET_API_URL,
                                   account_address=address)
        LOG.warning("[LIVE] signed client ready for %s -- REAL ORDERS WILL "
                    "BE PLACED", address)
    except Exception as e:                           # noqa: BLE001
        LOG.error("[LIVE] client init failed (no orders will be placed): %s",
                  e)
        raise


def main() -> int:
    args = parse_args()
    setup_logging(args.log)
    cfg = Config()
    if args.equity:
        cfg.paper_equity = args.equity
    if args.top_n:
        cfg.top_n = args.top_n
    if args.min_atr_pct:
        cfg.min_atr_pct = args.min_atr_pct
    env = load_agent_env(BASE_DIR / ".env")
    cfg.paper_only = paper_lock_engaged(BASE_DIR)
    cfg.live = False
    if args.live:
        if cfg.paper_only:
            LOG.error("[SAFETY] live refused: paper lock engaged "
                      "(ICT_PAPER_ONLY=1 or PAPER_ONLY file present)")
            return 3
        if not args.secret_key:
            LOG.error("[SAFETY] live refused: --live requires --secret-key")
            return 2
        if os.getenv("ICT_LIVE_ACK", "").strip() != LIVE_ACK_PHRASE:
            LOG.error("[SAFETY] live refused: set ICT_LIVE_ACK=%s to "
                      "confirm real-money trading", LIVE_ACK_PHRASE)
            return 3
        cfg.live = True
    elif args.secret_key:
        LOG.warning("[SAFETY] --secret-key ignored (no --live requested) "
                    "-- staying in PAPER mode; the key was never stored")
    cfg.data_dir = (args.data_dir or os.getenv("SCALPER_DATA")
                    or env.get("SCALPER_DATA")
                    or str(BASE_DIR / "data"))
    cfg.ntfy_topic = (args.ntfy_topic or os.getenv("NTFY_TOPIC")
                      or env.get("NTFY_TOPIC", ""))
    cfg.notify = not args.no_ntfy
    cfg.publish_state = not args.no_state
    if cfg.live and not args.secret_key:
        LOG.error("--live requires --secret-key")
        return 2
    if args.test_notify:
        n = Notifier(cfg.ntfy_topic, enabled=cfg.notify)
        ok = n.send("test alert from the ICT Sniper bot -- wiring is live.",
                    title="SNIPER test", tags="test_tube")
        LOG.info("[NTFY] topic=%s enabled=%s sent=%s",
                 (cfg.ntfy_topic[:6] + "..." if cfg.ntfy_topic else "(none)"),
                 n.enabled, ok)
        return 0 if ok else 1
    address = (args.address or os.getenv("HL_ADDRESS")
               or env.get("HL_ADDRESS")
               or "0x0000000000000000000000000000000000000000")
    engine = SniperEngine(cfg, address, use_ws=not args.no_ws)
    if cfg.live:
        _enable_live(engine, args.secret_key, address)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, engine.shutdown)
        except NotImplementedError:              # non-unix fallback
            pass
    try:
        if args.smoke:
            loop.run_until_complete(smoke(cfg, engine))
        else:
            loop.run_until_complete(engine.run())
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
