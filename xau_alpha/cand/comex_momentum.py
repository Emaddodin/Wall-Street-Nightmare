"""
cand/comex_momentum.py - Hypothesis H5 (SYNTHESIS.md section 6): intraday momentum into the COMEX settle.

Idea (SOURCED web_research 2.3: Gao et al. 2018; Baltussen et al. 2021 JFE): the return over most of the trading day
predicts the return over the final ~30 minutes before the close / settlement, because hedgers and liquidity providers
trade late in the direction of the day's move. For gold the relevant "close" is the COMEX GC settlement window
(13:29-13:30 ET); variant b uses the last hour before the 17:00 ET metals daily break.

Rules (identical and mirrored for longs and shorts: d = sign(r); no directional bias):
  TRADING DAY   data.tday (NY date rolled at 17:00 ET), whose first bar is the 18:00 ET reopen.
  SIGNAL        variant a: r = c[bar ending at 13:00 ET] - o[first bar of the trading day]   (18:00 ET -> 13:00 ET)
                variant b: r = c[bar ending at 16:00 ET] - o[first bar of the trading day]   (18:00 ET -> 16:00 ET)
                variant c: r = c[bar ending at 08:50 ET] - o[bar starting 08:20 ET]          (08:20 -> 08:50 ET)
                `t_entry` (ET minute of day) moves the a-variant entry clock for the placebo-time null (11:00, 15:00).
  FILTER        |r| >= theta * ATRd * sqrt(win / 1380), where ATRd = mean true range of the prior 20 trading days
                (daily bars from M1 mid, prior days only) and win = signal-window length in minutes (1380 = one
                23-hour trading day). The sqrt scaling is a deviation from SYNTHESIS (OPINION): it keeps theta
                comparable between the ~19-22 h windows of a/b and the 30-minute window of c (a/b factor 0.91/0.98).
  ENTRY         market order decided at the close of the signal bar (t = ts + 60 s): 13:00 ET for a and c,
                16:00 ET for b, in the direction of r.
  EXIT          time exit (sim `flat`) `exit` minutes after entry: 13:25/13:30/13:45/14:00 ET for a/c,
                16:15/16:25/16:30/16:45 ET for b (never after 16:45 ET, the session gate).
  STOP          sd = kappa * median |same-clock 30-minute move| (|c[E+30] - c[E]|) over the prior 20 trading days.
                cap = 'none'  : sd as computed (the >= $100 account)
                      'skip'  : skip the day if sd is outside the flip band [1.2, 4.0] $/oz
                      'clamp' : sd clipped into [1.2, 4.0] $/oz (always flip-eligible)
  NEWS          skip the day if a HIGH calendar event falls in [entry - nb, exit] (nb = 30 min, the <$21 margin rule),
                and (SYNTHESIS) skip FOMC-statement days whenever the exit is after 13:30 ET.

Units: theta/kappa are scale-free (ATRd, median same-clock move); only the flip stop band is in $/oz (broker
constraint). All arrays are cut at the TEST start (2026-06-01) so no holdout bar can be used or emitted.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from data import TEST, _ms, load_calendar  # noqa: E402

TEST_MS = _ms(TEST[0])
DAY_MIN = 1380                       # minutes in one trading day (18:00 -> 17:00 ET)
FLIP_BAND = (1.2, 4.0)
SESSION_END_ET = 16 * 60 + 45        # flat by 16:45 ET
SETTLE_ET = 13 * 60 + 30

# variant -> (signal start: None = trading-day open or ET minute, signal end = entry clock ET minute)
VARIANTS = {"a": (None, 13 * 60), "b": (None, 16 * 60), "c": (8 * 60 + 20, 13 * 60)}
C_SIGNAL_END = 8 * 60 + 50
EXITS = {"a": (25, 30, 45, 60), "b": (15, 25, 30, 45), "c": (25, 30, 45, 60)}

# Stage-1 grid (SYNTHESIS H5): theta(3) x exit(4) x kappa(2) x cap(3) x variant(3) = 216. Variant b uses the exit
# set EXITS['b'] (the driver cand/h5h7_stages.py builds the exact list); this GRID is the a/c form.
GRID = {
    "variant": ["a", "c"],
    "theta": [0.0, 0.25, 0.5],
    "exit": [25, 30, 45, 60],
    "kappa": [1.0, 2.0],
    "cap": ["none", "skip", "clamp"],
}

DEFAULTS = dict(variant="a", theta=0.25, exit=30, kappa=2.0, cap="none", nb=30, fomc_x=1, t_entry=None, med_n=20,
                atr_n=20)

_C: dict = {}


def _base(m1):
    """Per-process cache: TEST-cut arrays, per-trading-day table and a (day, ET minute) -> bar index lookup."""
    key = (len(m1), int(m1["ts"].values[0]), int(m1["ts"].values[-1]))
    if _C.get("key") == key:
        return _C["b"]
    _C.clear()
    ts_all = m1["ts"].values
    n = int(np.searchsorted(ts_all, TEST_MS, side="left"))          # holdout cut
    ts = ts_all[:n].astype(np.int64)
    o, h, l, c = (m1[k].values[:n].astype(np.float64) for k in "ohlc")
    ny = m1["ny_mod"].values[:n].astype(np.int64)
    dcode, days = pd.factorize(m1["tday"].values[:n], sort=True)
    # daily bars from M1 mid, prior-day ATR
    g = pd.DataFrame({"d": dcode, "h": h, "l": l, "c": c, "i": np.arange(n)}).groupby("d", sort=True)
    D = pd.DataFrame({"H": g.h.max(), "L": g.l.min(), "C": g.c.last(), "first": g.i.first(), "nbar": g.i.size()})
    pc = D["C"].shift(1)
    D["TR"] = np.maximum(D["H"], pc.fillna(D["H"])) - np.minimum(D["L"], pc.fillna(D["L"]))
    D["O"] = o[D["first"].values]
    D["first_ny"] = ny[D["first"].values]
    # (day, ET minute) -> bar index, first occurrence (the DST fall-back hour at 01:00 ET is irrelevant here)
    lut = pd.Series(np.arange(n), index=pd.MultiIndex.from_arrays([dcode, ny]))
    lut = lut[~lut.index.duplicated(keep="first")]
    # FOMC statement days and HIGH event times (calendar)
    cal = load_calendar()
    hi = cal[cal["impact"] == "HIGH"]
    ev_ts = np.sort(hi["ts"].values.astype(np.int64))
    fomc_ts = hi.loc[hi["event"].str.contains("FOMC rate decision", case=False), "ts"].values.astype(np.int64)
    fomc_days = set()
    if len(fomc_ts):
        fd = pd.to_datetime(fomc_ts, unit="ms", utc=True).tz_convert("America/New_York") + pd.Timedelta(hours=7)
        fomc_days = set(fd.strftime("%Y-%m-%d"))
    b = dict(n=n, ts=ts, o=o, h=h, l=l, c=c, ny=ny, dcode=dcode, days=np.asarray(days), D=D, lut=lut, ev_ts=ev_ts,
             fomc_days=fomc_days)
    _C["key"], _C["b"] = key, b
    return b


def _bar_at(b, minute):
    """Array over trading days: M1 index of the bar starting at ET `minute` (-1 if missing)."""
    ck = ("bar", minute)
    if ck in _C:
        return _C[ck]
    nd = len(b["D"])
    out = np.full(nd, -1, dtype=np.int64)
    try:
        s = b["lut"].xs(minute, level=1)
        out[s.index.values] = s.values
    except KeyError:
        pass
    _C[ck] = out
    return out


def _med_move(b, E, med_n):
    """Per trading day: median over the PRIOR med_n days of |c[bar ending E+30] - c[bar ending E]| (ET clock)."""
    ck = ("med", E, med_n)
    if ck in _C:
        return _C[ck]
    i0 = _bar_at(b, E - 1)
    i1 = _bar_at(b, E + 29)
    c = b["c"]
    mv = np.where((i0 >= 0) & (i1 >= 0), np.abs(c[np.maximum(i1, 0)] - c[np.maximum(i0, 0)]), np.nan)
    med = pd.Series(mv).shift(1).rolling(med_n, min_periods=max(5, med_n // 2)).median().values
    _C[ck] = med
    return med


def _atrd(b, atr_n):
    ck = ("atrd", atr_n)
    if ck not in _C:
        _C[ck] = b["D"]["TR"].shift(1).rolling(atr_n, min_periods=max(5, atr_n // 2)).mean().values
    return _C[ck]


def signals(m1, **params):
    """One row per trading day that passes the rule (before the one-position-at-a-time filter)."""
    p = {**DEFAULTS, **params}
    b = _base(m1)
    var = str(p["variant"])
    s0, E = VARIANTS[var]
    if p.get("t_entry") not in (None, "", 0) and not (isinstance(p["t_entry"], float) and np.isnan(p["t_entry"])):
        E = int(p["t_entry"])
    ex = int(p["exit"])
    exit_clock = E + ex
    if var == "b":
        exit_clock = min(exit_clock, SESSION_END_ET)
    ts, o, c = b["ts"], b["o"], b["c"]
    D = b["D"]
    i_dec = _bar_at(b, (C_SIGNAL_END if var == "c" else E) - 1)     # bar whose close ends the signal window
    i_ent = _bar_at(b, E - 1) if var == "c" else i_dec              # bar whose close is the entry decision
    if s0 is None:
        p0 = D["O"].values
        ok0 = (D["first_ny"].values >= 18 * 60) & (D["first_ny"].values <= 18 * 60 + 30)   # a real 18:00 open
        win = (E - 18 * 60 + 1440) % 1440
    else:
        i_s = _bar_at(b, s0)
        p0 = np.where(i_s >= 0, o[np.maximum(i_s, 0)], np.nan)
        ok0 = i_s >= 0
        win = C_SIGNAL_END - s0
    r = np.where(i_dec >= 0, c[np.maximum(i_dec, 0)], np.nan) - p0
    atrd = _atrd(b, int(p["atr_n"]))
    thr = float(p["theta"]) * atrd * np.sqrt(win / DAY_MIN)
    med = _med_move(b, E, int(p["med_n"]))
    sd = float(p["kappa"]) * med
    cap = str(p["cap"])
    lo, hi = FLIP_BAND
    if cap == "clamp":
        sd = np.clip(sd, lo, hi)
    ok = ok0 & (i_dec >= 0) & (i_ent >= 0) & np.isfinite(r) & np.isfinite(thr) & np.isfinite(sd) & (sd > 0)
    ok &= np.abs(r) >= thr
    ok &= r != 0
    if cap == "skip":
        ok &= (sd >= lo) & (sd <= hi)
    rows = []
    ev = b["ev_ts"]
    nb = int(p["nb"]) * 60_000
    fomc_x = int(p["fomc_x"]) and exit_clock > SETTLE_ET
    for k in np.flatnonzero(ok):
        i = int(i_ent[k])
        t = int(ts[i]) + 60_000
        flat = t + (exit_clock - E) * 60_000
        if flat >= TEST_MS:
            continue
        if nb >= 0 and len(ev):
            j = np.searchsorted(ev, t - nb, side="left")
            if j < len(ev) and ev[j] <= flat:
                continue
        if fomc_x and b["days"][k] in b["fomc_days"]:
            continue
        d = 1 if r[k] > 0 else -1
        rows.append((t, d, flat, float(sd[k]), float(r[k]), float(thr[k]), b["days"][k], float(c[i])))
    return pd.DataFrame(rows, columns=["t", "d", "flat", "sd", "r", "thr", "tday", "px"])


def orders(m1, **params):
    p = {**DEFAULTS, **params}
    df = signals(m1, **p)
    tag = f"h5{p['variant']}"
    return [{"t": int(r.t), "d": int(r.d), "kind": "mkt", "sl_dist": float(r.sd), "flat": int(r.flat),
             "tag": f"{tag}{'L' if r.d > 0 else 'S'}"} for r in df.itertuples(index=False)]
