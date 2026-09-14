"""ICT Sniper setup detector -- the sweep -> displacement/MSS -> FVG chain.

Pure, deterministic, strictly causal: the state at bar i uses ONLY bars
<= i.  Built exclusively on the existing PA modules (pa/ict.py,
indicators) and aligned with the ict-knowledge-library concepts:

  * concepts/02-liquidity/liquidity-sweep.md
        sweep = wick through a known pool + close back inside,
        sweep wick >= 60% of the bar range   -> pa.ict.turtle_soup
  * concepts/06-fair-value-gaps/fair-value-gap.md
        3-candle imbalance, zone [H[n-1], L[n+1]], CE = midpoint,
        gap alive until the CE is traded      -> pa.ict.fvg_state
  * concepts/01-market-structure
        MSS = close beyond the last confirmed swing in the new direction
        -> pa.ict.choch_state (+ displacement)

Long setup (short is mirrored):
  1. SSL sweep: a bar wicks through the most recent confirmed swing LOW
     (the pool) and closes back above it (wick >= wick_frac x range).
  2. Within mss_max_gap bars, a displacement bar closes above the most
     recent confirmed swing HIGH (MSS / ChoCH).
  3. That leg leaves a bullish FVG that is still UNMITIGATED and was
     created at/after the MSS bar.
  4. Entry = passive limit at the FVG CE (or gap edge).  SL = sweep wick
     extreme - buffer.  TP is clamped to [tp_min_bps, tp_max_bps] with
     rr = TP/SL >= rr_min; setups whose SL is too wide to reach tp_min
     at rr_min are rejected (the A+ gate).  Invalidation: the limit rests
     retest_bars; the trade time-exits after time_exit_bars.

The bps gates are the anti-fee-trap math: with TP >= 150 bps a ~8 bps
round-trip cost is <= ~5% of the target, whereas the old fade paid 6.4 bps
against a 3.8 bps gross edge (168% of the edge).

Every function takes a DataFrame with open/high/low/close[/volume] columns
sorted oldest-first and returns values whose row at bar i uses only bars
<= i.  No engine imports -- importable from both the live engine (as
`pa.sniper`) and the quant project (as `scalper.pa.sniper`).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import ict as pa_ict
# `pa` is loaded two ways in this repo: as top-level `pa` (live engine,
# scalper/ on sys.path) or as `scalper.pa` (quant project, root on
# sys.path).  The sibling import resolves to `indicators` vs
# `scalper.indicators` accordingly.
try:
    from ..indicators import atr as _atr, swing_points
except ImportError:                       # pragma: no cover - live context
    from indicators import atr as _atr, swing_points


@dataclass
class SniperParams:
    """All detector thresholds.  Defaults follow the library where the
    library has a number; the rest are A+-setup choices."""

    # structure ---------------------------------------------------------
    arm: int = 2                    # fractal arms each side (confirmation lag)
    swing_fresh_bars: int = 30      # sweep pool (swing) must be confirmed
                                    # within this many bars
    sweep_max_age: int = 12         # setup valid this many bars after the sweep
    mss_max_gap: int = 6            # MSS must follow the sweep within N bars
    # sweep --------------------------------------------------------------
    wick_frac: float = 0.45         # sweep wick >= 45% of range (relaxed
                                    # from the library's 60%: more setups)
    # displacement -------------------------------------------------------
    disp_body_mult: float = 1.5     # body >= mult x avg body (pa.ict default)
    disp_body_range: float = 0.70   # body/range >= this
    disp_max_opp_wick: float = 0.20 # opposing wick <= this x range
    disp_avg_window: int = 20
    # FVG ----------------------------------------------------------------
    fvg_max_age: int = 30           # gap dies after N bars (library default)
    fvg_after: str = "mss"          # FVG must be created at/after the MSS bar
    entry_at: str = "ce"            # limit at the CE; "edge" = zone edge
    fvg_mitigation: str = "close"   # "close" (relaxed): the gap survives
                                    #   wicks INTO it and dies only when a
                                    #   candle CLOSES beyond the CE.
                                    # "wick" (library): any CE touch kills.
    # risk/reward gates (the anti-fee-trap math) --------------------------
    rr_min: float = 2.5             # TP2/SL >= 2.5 strictly
    tp_mode: str = "fixed"          # "fixed" = clamp [tp_min_bps, tp_max_bps]
                                    # "atr"   = tp_atr_mult x ATR(tf), capped
                                    #           at tp_max_bps
    tp_min_bps: float = 300.0       # fixed mode: runner (TP2) at least 3%
    tp_max_bps: float = 500.0       # runner (TP2) at most 5%
    tp_atr_mult: float = 2.0        # atr mode: TP = mult x ATR of the TF
    tp1_bps: float = 100.0          # scale-out: close tp1_frac at +N bps,
    tp1_frac: float = 0.5           # move the runner's SL to breakeven
    trim: bool = False              # defensive mid-level trim: if price
                                    #   trades through the FVG-mid anchor
                                    #   (midpoint of FVG far edge <-> sweep
                                    #   wick), close trim_frac to cap risk;
                                    #   the remainder keeps the WIDE stop
    trim_frac: float = 0.5          # fraction closed at the mid trim
    max_sl_bps: float | None = None # SL wider than this is rejected
                                    # (default = tp_max_bps / rr_min = 200)
    sl_buffer_atr_mult: float = 0.25  # pad beyond the invalidation line
    sl_mode: str = "sweep"          # SL anchor (phase-7 production
                                    # default; phases 4-6 proved the wide
                                    # stop is structural -- deep retests
                                    # ARE the edge):
                                    # "sweep": beyond the sweep wick extreme
                                    #   + ATR pad
                                    # "fvg_edge": opposite (far) edge of the
                                    #   FVG zone + ATR pad (noise-clipped)
                                    # "mid": midpoint FVG-edge <-> sweep wick
                                    #   + ATR pad (phase 5: rejected)
    atr_period: int = 14
    # execution ----------------------------------------------------------
    time_exit_bars: int = 60        # kill the RUNNER at market after N TF
                                    # bars (5m: 60-80 = 5 to ~6.7 hours)
    retest_bars: int = 8            # the limit order's patience (TF bars)
    be_after_r: float | None = None # breakeven gate: once MFE >= N x risk,
                                    # stop -> entry + be_to_r x risk
                                    # (None = gate off)
    be_after_bps: float | None = None  # absolute pre-scale BE trigger:
                                    # once MFE >= N bps, stop -> entry +
                                    # be_to_r x risk (overrides be_after_r)
    be_to_r: float = 0.0            # 0.0 = breakeven, 0.5 = lock half-R
    neg_bars: int | None = None     # scratch rule: if the trade is still
                                    # negative after N TF bars, kill it at
                                    # market (None = off; e.g. 4)
    killzones: list[str] | None = None  # optional pa.ict.killzone filter
    tail_bars: int = 2000           # live adapter: detector history window

    def __post_init__(self) -> None:
        if self.max_sl_bps is None:
            self.max_sl_bps = self.tp_max_bps / self.rr_min
        if self.entry_at not in ("ce", "edge"):
            raise ValueError(f"entry_at must be 'ce' or 'edge', got {self.entry_at!r}")
        if self.fvg_after not in ("mss", "sweep"):
            raise ValueError(f"fvg_after must be 'mss' or 'sweep', got {self.fvg_after!r}")
        if self.fvg_mitigation not in ("close", "wick"):
            raise ValueError("fvg_mitigation must be 'close' or 'wick', "
                             f"got {self.fvg_mitigation!r}")
        if self.sl_mode not in ("fvg_edge", "sweep", "mid"):
            raise ValueError("sl_mode must be 'fvg_edge', 'sweep' or 'mid', "
                             f"got {self.sl_mode!r}")
        if self.tp_mode not in ("fixed", "atr"):
            raise ValueError(f"tp_mode must be 'fixed' or 'atr', got {self.tp_mode!r}")
        if not (2 <= self.time_exit_bars <= 120):
            raise ValueError("time_exit_bars must be 2-120 (runner horizon "
                             f"60-80 on 5m), got {self.time_exit_bars}")
        if not (0 < self.tp1_frac < 1):
            raise ValueError(f"tp1_frac must be in (0, 1), got {self.tp1_frac}")


# ----------------------------------------------------------------------
def _fvg_scan(hi: np.ndarray, lo: np.ndarray, c: np.ndarray,
              max_age: int = 30, mitigation: str = "close"):
    """(bull_fvg, bull_ce, bull_hi, bull_lo, bear_*) per bar.

    Same 3-candle creation rule as pa.ict.fvg_state / pa.fast.fvg_state_np,
    but with a selectable mitigation rule:
      * "wick"  (library): the gap dies the moment any bar's WICK reaches
        the CE.
      * "close" (relaxed): the gap survives wicks INTO it and dies only
        when a candle CLOSES beyond the CE (5m partial-mitigation model).
    """
    n = len(hi)
    bull_fvg = np.zeros(n, dtype=bool)
    bear_fvg = np.zeros(n, dtype=bool)
    bull_ce = np.full(n, np.nan)
    bull_hi = np.full(n, np.nan)
    bull_lo = np.full(n, np.nan)
    bear_ce = np.full(n, np.nan)
    bear_hi = np.full(n, np.nan)
    bear_lo = np.full(n, np.nan)
    cb = None   # (created, gap_hi, gap_lo, ce)
    cs = None
    for i in range(n):
        if i >= 2:
            if lo[i] > hi[i - 2]:
                gap_hi, gap_lo = hi[i - 2], lo[i]
                cb = (i, gap_hi, gap_lo, (gap_hi + gap_lo) / 2.0)
            if hi[i] < lo[i - 2]:
                gap_hi, gap_lo = hi[i], lo[i - 2]
                cs = (i, gap_hi, gap_lo, (gap_hi + gap_lo) / 2.0)
        if cb is not None:
            if mitigation == "wick":
                dead = lo[i] <= cb[3]
            else:
                dead = c[i] < cb[3]          # close beyond the CE
            if dead or i - cb[0] > max_age:
                cb = None
        if cs is not None:
            if mitigation == "wick":
                dead = hi[i] >= cs[3]
            else:
                dead = c[i] > cs[3]
            if dead or i - cs[0] > max_age:
                cs = None
        if cb is not None:
            bull_fvg[i] = True
            bull_hi[i], bull_lo[i], bull_ce[i] = cb[1], cb[2], cb[3]
        if cs is not None:
            bear_fvg[i] = True
            bear_hi[i], bear_lo[i], bear_ce[i] = cs[1], cs[2], cs[3]
    return (bull_fvg, bull_ce, bull_hi, bull_lo,
            bear_fvg, bear_ce, bear_hi, bear_lo)


def _last_where(mask: np.ndarray) -> np.ndarray:
    """Per-bar index of the most recent True in `mask` (-1 if never)."""
    idx = np.where(mask, np.arange(mask.shape[0]), -1)
    return np.maximum.accumulate(idx)


def _prev(arr: np.ndarray, fill) -> np.ndarray:
    out = np.empty_like(arr)
    out[0] = fill
    out[1:] = arr[:-1]
    return out


def detect_structure(df: pd.DataFrame, p: SniperParams) -> pd.DataFrame:
    """Per-bar STRUCTURAL state (tp-independent -> cacheable across a
    parameter grid).  The value in row i uses only bars <= i.

    Columns (n = len(df)):
      open_time              int   -- bar open time (ms; killzone gating)
      atr                    float -- ATR of the detection TF
      sweep_lo / sweep_hi    bool  -- turtle-soup sweep of last SSL / BSL
      mss_up / mss_dn        bool  -- displacement close-break of last
                                      swing HIGH / LOW
      bull_fvg / bear_fvg    bool  -- FVG alive (see fvg_mitigation)
      bull_ce / bear_ce      float -- consequent encroachment
      lsb / lsh_             int   -- last sweep bar (low / high)
      lmu / lmd              int   -- last MSS bar (up / down)
      lbc / lbrc             int   -- last bullish / bearish FVG creation
      struct_ok_l/_s         bool  -- all tp/SL-mode-independent gates
      s_l / m_l              int   -- sweep + MSS bars of the active long
      entry_l                float -- long entry (CE)
      sweep_ext_l/_s         float -- the sweep wick extreme
      gap_bottom_l/_s        float -- FVG zone bottom bound (SL anchor)
      gap_top_l/_s           float -- FVG zone top bound
      ... _s mirrors for shorts.  (SL/tp/rr/valid/emit come from
      gate_setups -- they depend on sl_mode and the TP band.)
    """
    n = len(df)
    idx0 = np.arange(n)
    hi = df["high"].to_numpy(dtype=float)
    lo = df["low"].to_numpy(dtype=float)

    # ---- confirmed swings (value indexed by CONFIRMATION bar) ----------
    sw_hi, sw_lo = swing_points(df.reset_index(drop=True), p.arm)
    sw_hi = sw_hi.to_numpy(dtype=float)
    sw_lo = sw_lo.to_numpy(dtype=float)
    last_sh = pd.Series(sw_hi).ffill().to_numpy()
    last_sl = pd.Series(sw_lo).ffill().to_numpy()
    conf_hi = np.where(~np.isnan(sw_hi), idx0, -1)
    conf_lo = np.where(~np.isnan(sw_lo), idx0, -1)
    last_conf_hi = np.maximum.accumulate(conf_hi)
    last_conf_lo = np.maximum.accumulate(conf_lo)
    sh_fresh = (last_conf_hi >= 0) & (idx0 - last_conf_hi <= p.swing_fresh_bars)
    sl_fresh = (last_conf_lo >= 0) & (idx0 - last_conf_lo <= p.swing_fresh_bars)

    # ---- sweep (pa.ict.turtle_soup = the library's sweep criteria) ------
    lvl_sl = pd.Series(last_sl).ffill()
    lvl_sh = pd.Series(last_sh).ffill()
    sweep_lo = (pa_ict.turtle_soup(df, lvl_sl, "low", p.wick_frac)
                .fillna(False).to_numpy() & sl_fresh)
    sweep_hi = (pa_ict.turtle_soup(df, lvl_sh, "high", p.wick_frac)
                .fillna(False).to_numpy() & sh_fresh)

    # ---- displacement + MSS --------------------------------------------
    disp = pa_ict.displacement(df, p.disp_body_mult, p.disp_body_range,
                               p.disp_max_opp_wick, p.disp_avg_window)
    disp_up = disp["disp_up"].to_numpy()
    disp_dn = disp["disp_dn"].to_numpy()
    choch = pa_ict.choch_state(df, pd.Series(sw_lo), pd.Series(sw_hi)).to_numpy()
    mss_up = (choch == 1) & disp_up
    mss_dn = (choch == -1) & disp_dn

    # ---- FVG (relaxed close-mitigation scan; creation rule identical to
    # ---- pa.ict.fvg_state / pa.fast.fvg_state_np) ----------------------
    (bull_fvg, bull_ce, bull_gap_hi, bull_gap_lo,
     bear_fvg, bear_ce, bear_gap_hi, bear_gap_lo) = _fvg_scan(
        hi, lo, df["close"].to_numpy(dtype=float),
        max_age=p.fvg_max_age, mitigation=p.fvg_mitigation)
    created_bull = np.zeros(n, dtype=bool)
    created_bear = np.zeros(n, dtype=bool)
    created_bull[2:] = lo[2:] > hi[:-2]   # 3-candle pattern completes at i
    created_bear[2:] = hi[2:] < lo[:-2]
    lbc = _last_where(created_bull)
    lbrc = _last_where(created_bear)

    lsb = _last_where(sweep_lo)
    lsh_ = _last_where(sweep_hi)
    lmu = _last_where(mss_up)
    lmd = _last_where(mss_dn)

    atr_s = _atr(df.reset_index(drop=True), p.atr_period).to_numpy(dtype=float)

    # ---- per-side STRUCTURAL gates (tp/SL-mode-independent -> cacheable)
    def side_structure(sweep_bar, mss_bar, fvg_alive, ce, gap_hi, gap_lo,
                       fvg_created, sweep_extreme, long: bool):
        s = sweep_bar.copy()
        m = mss_bar.copy()
        ok = np.ones(n, dtype=bool)   # gates AND onto a True baseline
        ok &= s >= 0
        ok &= m > s                          # MSS strictly AFTER the sweep
        ok &= (m - s) <= p.mss_max_gap
        ok &= (idx0 - s) <= p.sweep_max_age  # the raid is still fresh
        ok &= fvg_alive
        if p.fvg_after == "mss":
            ok &= fvg_created >= m
        else:
            ok &= fvg_created >= s
        entry = ce if p.entry_at == "ce" else (gap_hi if long else gap_lo)
        ok &= np.isfinite(entry)
        sweep_ext = np.where(s >= 0, sweep_extreme[np.clip(s, 0, n - 1)], np.nan)
        return dict(ok=ok, s=s, m=m, entry=entry, sweep_ext=sweep_ext,
                    gap_bottom=gap_hi.copy(), gap_top=gap_lo.copy())

    long_st = side_structure(lsb, lmu, bull_fvg, bull_ce, bull_gap_hi,
                             bull_gap_lo, lbc, lo, long=True)
    short_st = side_structure(lsh_, lmd, bear_fvg, bear_ce, bear_gap_hi,
                              bear_gap_lo, lbrc, hi, long=False)

    out = pd.DataFrame(index=df.index)
    out["open_time"] = df["open_time"].to_numpy(dtype=np.int64)
    out["atr"] = atr_s
    out["disp_up"] = disp_up
    out["disp_dn"] = disp_dn
    out["sweep_lo"] = sweep_lo
    out["sweep_hi"] = sweep_hi
    out["mss_up"] = mss_up
    out["mss_dn"] = mss_dn
    out["bull_fvg"] = bull_fvg
    out["bear_fvg"] = bear_fvg
    out["bull_ce"] = bull_ce
    out["bear_ce"] = bear_ce
    out["lsb"] = lsb
    out["lsh"] = lsh_
    out["lmu"] = lmu
    out["lmd"] = lmd
    out["lbc"] = lbc
    out["lbrc"] = lbrc
    for side, st in (("l", long_st), ("s", short_st)):
        out[f"struct_ok_{side}"] = st["ok"]
        out[f"s_{side}"] = st["s"]
        out[f"m_{side}"] = st["m"]
        out[f"entry_{side}"] = st["entry"]
        out[f"sweep_ext_{side}"] = st["sweep_ext"]
        out[f"gap_bottom_{side}"] = st["gap_bottom"]
        out[f"gap_top_{side}"] = st["gap_top"]
    for col in out.columns:                 # float32: bps precision is fine,
        if out[col].dtype == np.float64:    # halves sweep memory
            out[col] = out[col].astype(np.float32)
    return out


# columns gate_setups needs -- what a sweep shard caches per symbol
STRUCTURE_COLS = ["open_time", "atr",
                  "s_l", "m_l", "entry_l", "sweep_ext_l",
                  "gap_bottom_l", "gap_top_l", "struct_ok_l",
                  "s_s", "m_s", "entry_s", "sweep_ext_s",
                  "gap_bottom_s", "gap_top_s", "struct_ok_s"]


def gate_setups(struct: pd.DataFrame, p: SniperParams) -> pd.DataFrame:
    """TP/SL-mode-dependent gating from a cached structural frame (cheap,
    no scans).  Returns a frame indexed like `struct` with columns
    valid_l/s, emit_l/s, tp_bps_l/s, rr_l/s, sl_l/s, sl_bps_l/s."""
    atr_s = struct["atr"].to_numpy(dtype=float)
    out = pd.DataFrame(index=struct.index)
    for tag, long in (("l", True), ("s", False)):
        s = struct[f"s_{tag}"].to_numpy()
        entry = struct[f"entry_{tag}"].to_numpy(dtype=float)
        sweep_ext = struct[f"sweep_ext_{tag}"].to_numpy(dtype=float)
        gap_bottom = struct[f"gap_bottom_{tag}"].to_numpy(dtype=float)
        gap_top = struct[f"gap_top_{tag}"].to_numpy(dtype=float)
        ok = struct[f"struct_ok_{tag}"].to_numpy().astype(bool).copy()
        buf = p.sl_buffer_atr_mult * atr_s
        if p.sl_mode == "fvg_edge":
            # structural invalidation: the opposite (far) edge of the gap
            sl = (gap_bottom - buf) if long else (gap_top + buf)
        elif p.sl_mode == "mid":
            # midpoint between the FVG far edge and the sweep wick extreme:
            # out of the immediate noise band, ~half the wide-stop drag
            mid = (gap_bottom + sweep_ext) / 2.0 if long else \
                (gap_top + sweep_ext) / 2.0
            sl = (mid - buf) if long else (mid + buf)
        else:
            sl = sweep_ext - buf if long else sweep_ext + buf
        if long:
            ok &= np.isfinite(sl) & (sl < entry)
        else:
            ok &= np.isfinite(sl) & (sl > entry)
        with np.errstate(invalid="ignore", divide="ignore"):
            sl_bps = (entry - sl) / entry * 1e4 if long else \
                     (sl - entry) / entry * 1e4
            if p.tp_mode == "atr":
                atr_bps = atr_s / np.maximum(entry, 1e-12) * 1e4
                tp_bps = np.minimum(p.tp_atr_mult * atr_bps, p.tp_max_bps)
            else:
                tp_bps = np.minimum(
                    p.tp_max_bps,
                    np.maximum(p.rr_min * sl_bps, p.tp_min_bps))
            tp_bps = tp_bps * 1.000000001   # strict rr in float
        ok &= sl_bps > 0
        ok &= sl_bps <= p.max_sl_bps
        ok &= tp_bps >= p.rr_min * sl_bps - 1e-9
        # defensive mid-level trim anchor (independent of sl_mode): the
        # midpoint between the FVG far edge and the sweep wick extreme
        with np.errstate(invalid="ignore", divide="ignore"):
            if long:
                mid = (gap_bottom + sweep_ext) / 2.0
                trim_bps = (entry - mid) / entry * 1e4
            else:
                mid = (gap_top + sweep_ext) / 2.0
                trim_bps = (mid - entry) / entry * 1e4
        if p.killzones:
            t_ms = struct["open_time"].to_numpy(dtype=np.int64)
            ok &= pa_ict.killzone(t_ms, p.killzones)
        # emit on the rising edge of validity, or when the entry level /
        # sweep changes while valid (a NEW setup on the same run)
        prev_ok = _prev(ok, False)
        emit = ok & (~prev_ok | (entry != _prev(entry, np.nan))
                     | (s != _prev(s, -1)))
        rr = tp_bps / np.maximum(sl_bps, 1e-9)
        out[f"valid_{tag}"] = ok
        out[f"emit_{tag}"] = emit
        out[f"sl_{tag}"] = sl.astype(np.float32)
        out[f"sl_bps_{tag}"] = sl_bps.astype(np.float32)
        out[f"tp_bps_{tag}"] = tp_bps.astype(np.float32)
        out[f"trim_bps_{tag}"] = trim_bps.astype(np.float32)
        out[f"rr_{tag}"] = rr.astype(np.float32)
    return out


def detect_states(df: pd.DataFrame, p: SniperParams) -> pd.DataFrame:
    """Full per-bar state: structure + TP gates (the original API).  The
    value in row i uses only bars <= i."""
    struct = detect_structure(df, p)
    gates = gate_setups(struct, p)
    return pd.concat([struct, gates], axis=1)


def detect_setups(df: pd.DataFrame, p: SniperParams) -> pd.DataFrame:
    """One row per EMITTED setup (rising edge of a valid sweep->MSS->FVG
    chain).  Columns: side, bar, open_time, sweep_bar, mss_bar, sweep_level,
    entry, sl, tp, sl_bps, tp_bps, rr, score, close, atr."""
    states = detect_states(df, p)
    if len(states) == 0:
        return states
    t = df["open_time"].to_numpy(dtype=np.int64)
    c = df["close"].to_numpy(dtype=float)
    rows = []
    for side, tag in ((1, "l"), (-1, "s")):
        emit = states[f"emit_{tag}"].to_numpy()
        for i in np.nonzero(emit)[0]:
            rows.append({
                "side": side,
                "bar": int(i),
                "open_time": int(t[i]),
                "sweep_bar": int(states[f"s_{tag}"].iloc[i]),
                "mss_bar": int(states[f"m_{tag}"].iloc[i]),
                "sweep_level": float(states[f"sweep_ext_{tag}"].iloc[i]),
                "entry": float(states[f"entry_{tag}"].iloc[i]),
                "sl": float(states[f"sl_{tag}"].iloc[i]),
                "sl_bps": float(states[f"sl_bps_{tag}"].iloc[i]),
                "tp_bps": float(states[f"tp_bps_{tag}"].iloc[i]),
                "rr": float(states[f"rr_{tag}"].iloc[i]),
                "score": float(states[f"rr_{tag}"].iloc[i]),
                "close": float(c[i]),
                "atr": float(states["atr"].iloc[i]),
            })
    out = pd.DataFrame(rows)
    if len(out):
        out["tp"] = out["entry"] * (1 + out["side"] * out["tp_bps"] / 1e4)
        out = out.sort_values("open_time").reset_index(drop=True)
    return out
