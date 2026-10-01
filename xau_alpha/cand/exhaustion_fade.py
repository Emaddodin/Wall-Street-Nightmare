"""
cand/exhaustion_fade.py - Hypothesis X1 (new, not in SYNTHESIS): mean reversion after an exhaustion bar.

Motivation (HANDOFF.md quick M1 event study, mid prices): after a 5m bar whose body exceeds ~2.5x the average 5m range,
the mid moves AGAINST the bar over the next 30-60 minutes in both TRAIN and VALID (|t| ~1.3-1.4, i.e. weak).
This module turns that into a tradeable, fully causal fade and its continuation twin.

Rules (written for a BULLISH displacement bar; a bearish one is the exact mirror: the rule runs on the negated
series o'=-o, h'=-l, l'=-h, c'=-c, so longs and shorts use byte-identical rules and parameters):

  DISPLACEMENT  a completed tf-minute bar (tf in {5, 15}, UTC-aligned buckets from data.resample_causal) with
                body = c - o >= k * A_tf, where A_tf = Wilder ATR14 of the tf-minute bars as of the PREVIOUS
                tf bar (the bar itself does not inflate its own threshold). For tf=5 this is A5; for tf=15 it is
                A15 (~1.7 x A5), so k keeps the meaning "multiple of the bar's own typical range".
                The bucket must be complete (its final minute printed).
  NEWS          HIGH calendar rows (data.news_block_mask, +-news_win minutes, default 30):
                news='ex'   skip if ANY minute of the displacement bar, or the entry decision bar, is in a window
                            (also the <$21-equity broker rule, SYNTHESIS N2 / G4);
                news='only' keep ONLY displacement bars that touch a window (separate test; not takeable < $21);
                news='all'  no filter.
  CONFIRMATION  scanned on the M1 bars after the displacement bar (start = its last minute + 1), at most m bars,
                aborted on a data gap > 5 min:
                conf='none'   enter at the close of the displacement bar itself;
                conf='opp'    the first bearish M1 bar (c < o);
                conf='back25' / 'back50'  the first M1 close <= C - q*(C - O) (back inside the bar's last q=25/50%);
                conf='nofail' no M1 high above the bar's high for m bars -> enter at the close of the m-th bar.
  ENTRY         market (short for the fade) at the close of the confirmation bar j: order t = ts[j] + 60_000.
  STOP          ext + s*A[j]; ext = highest high from the displacement bar's first minute to j; A = M1 ATR14.
  TARGET        tp='rXX'  retracement of the displacement leg: ext - f*(ext - bar low), f = 0.25/0.382/0.5;
                          skipped if closer than tp_min x stop distance to the entry (cost would dominate);
                tp='RX'   X x stop distance.
  TIME          tmax minutes; also flat at 16:45 ET (SYNTHESIS section 5).
  SESSION       always: no entries 20:30-23:30 UTC, Friday >= 19:00 UTC, Sunday (weekly open), or 15:45-18:00 ET.
                sess='all' (only those gates), 'eu_us' (07:00-20:00 UTC), 'lonny' (06:00-12:00 London local OR
                08:30-11:30 New York).
  SIDE          side='fade' (the hypothesis) or side='cont' (continuation twin, the null): the SAME decision time,
                stop distance and target distance, reflected around the decision-time mid, i.e. trading WITH the
                displacement.

Units: every strategy threshold is in ATR units (A, A_tf). The $1.2-4.0 flip stop band is NOT applied here; it is
reported as the flip-eligible subset (sweep.evaluate) so the wide-stop result stays visible for the >= $100 account.
All arrays are cut at the TEST start (2026-06-01): nothing here can see or emit a holdout bar.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from data import TEST, _ms, atr, news_block_mask, resample_causal  # noqa: E402

TEST_MS = _ms(TEST[0])

# Stage 1 (coarse): tf(2) x k(3) x conf(5) x tp(4) x tmax(2) = 240 configs, with s=0.3, m=5, news='ex', sess='all'.
# k=4 is left out of the simulated grid: it leaves < 60 TRAIN displacement bars on 5m and ~21 on 15m before any
# confirmation or session gate (setup counts, see the report), so it cannot reach n >= 150.
GRID = {
    "tf": [5, 15],
    "k": [2.0, 2.5, 3.0],
    "conf": ["none", "opp", "back25", "back50", "nofail"],
    "tp": ["r38", "r50", "R1", "R1.5"],
    "tmax": [30, 60],
}

DEFAULTS = dict(tf=5, k=2.5, conf="opp", m=5, s=0.3, tp="r38", tp_min=0.3, tmax=60, news="ex", news_win=30,
                sess="all", side="fade")

_C: dict = {}


def _base(m1):
    """Per-process cache of TEST-cut M1 arrays and the tf-minute bars."""
    ts_all = m1["ts"].values
    key = (len(m1), int(ts_all[0]), int(ts_all[-1]))
    if _C.get("key") == key:
        return _C["b"]
    _C.clear()
    n = int(np.searchsorted(ts_all, TEST_MS, side="left"))
    df = m1.iloc[:n]
    b = {"n": n, "ts": ts_all[:n].astype(np.int64), "A": atr(df),
         "o": df["o"].values.astype(np.float64), "h": df["h"].values.astype(np.float64),
         "l": df["l"].values.astype(np.float64), "c": df["c"].values.astype(np.float64),
         "mod": df["mod"].values.astype(np.int64), "ny_mod": df["ny_mod"].values.astype(np.int64),
         "lon_mod": df["lon_mod"].values.astype(np.int64), "dow": df["dow"].values.astype(np.int64),
         "htf": {}, "news": {}, "sess": {}}
    _C["key"], _C["b"] = key, b
    return b


def _htf(b, df_m1, tf):
    if tf not in b["htf"]:
        htf, known = resample_causal(df_m1, tf)
        ki = np.searchsorted(known, np.arange(len(htf)), side="left")      # M1 bar at whose close it is known
        Atf = atr(htf)
        last_i = htf["last_i"].values.astype(np.int64)
        complete = (b["ts"][last_i] == htf["ts_open"].values + (tf - 1) * 60_000)
        b["htf"][tf] = {"o": htf["o"].values, "h": htf["h"].values, "l": htf["l"].values, "c": htf["c"].values,
                        "ref": np.r_[np.nan, Atf[:-1]], "fi": htf["first_i"].values.astype(np.int64),
                        "li": last_i, "ki": ki.astype(np.int64), "complete": complete}
    return b["htf"][tf]


def _news(b, win):
    if win not in b["news"]:
        m = news_block_mask(b["ts"], win, win) if win > 0 else np.zeros(b["n"], dtype=bool)
        b["news"][win] = (m, np.r_[0, np.cumsum(m.astype(np.int64))])
    return b["news"][win]


def _session(b, sess):
    if sess not in b["sess"]:
        mod, ny, lon, dow = b["mod"], b["ny_mod"], b["lon_mod"], b["dow"]
        gate = ~((mod >= 1230) & (mod < 1410))                 # 20:30-23:30 UTC
        gate &= ~((dow == 4) & (mod >= 1140))                  # Friday >= 19:00 UTC
        gate &= ~(dow == 6)                                    # Sunday (weekly reopen)
        gate &= ~((ny >= 945) & (ny < 1080))                   # 15:45-18:00 ET (flat by 16:45 ET)
        if sess == "eu_us":
            gate &= (mod >= 420) & (mod < 1200)
        elif sess == "lonny":
            gate &= ((lon >= 360) & (lon < 720)) | ((ny >= 510) & (ny < 690))
        b["sess"][sess] = gate
    return b["sess"][sess]


def setups(m1, **params):
    """One row per entry signal (before the one-position-at-a-time filter of the simulator)."""
    p = {**DEFAULTS, **params}
    b = _base(m1)
    n, ts, A = b["n"], b["ts"], b["A"]
    tf, k, conf, m = int(p["tf"]), float(p["k"]), str(p["conf"]), int(p["m"])
    s, tp_mode, tp_min = float(p["s"]), str(p["tp"]), float(p["tp_min"])
    H = _htf(b, m1.iloc[:n], tf)
    nb, ncs = _news(b, int(p["news_win"]))
    gate = _session(b, str(p["sess"]))

    body = H["c"] - H["o"]
    ev = np.flatnonzero(H["complete"] & np.isfinite(H["ref"]) & (np.abs(body) >= k * H["ref"])
                        & (H["li"] + m + 2 < n))
    rows = []
    for e in ev:
        D = 1 if body[e] > 0 else -1
        fi, li = H["fi"][e], H["li"][e]
        in_news = (ncs[li + 1] - ncs[fi]) > 0
        if p["news"] == "ex" and in_news:
            continue
        if p["news"] == "only" and not in_news:
            continue
        # displacement space: the bar is bullish
        if D > 0:
            O, C, Hb, Lb = H["o"][e], H["c"][e], H["h"][e], H["l"][e]
        else:
            O, C, Hb, Lb = -H["o"][e], -H["c"][e], -H["l"][e], -H["h"][e]
        ext = Hb
        j = -1
        start = li + 1
        if conf == "none":
            j = li
        else:
            for jj in range(start, start + m):
                if ts[jj] - ts[jj - 1] > 300_000:
                    break
                oj, hj, cj = (b["o"][jj], b["h"][jj], b["c"][jj]) if D > 0 else (-b["o"][jj], -b["l"][jj], -b["c"][jj])
                if conf == "nofail":
                    if hj > Hb:
                        break
                    if jj == start + m - 1:
                        j = jj
                    continue
                ext = max(ext, hj)
                if conf == "opp" and cj < oj:
                    j = jj
                    break
                if conf in ("back25", "back50") and cj <= C - (0.25 if conf == "back25" else 0.5) * (C - O):
                    j = jj
                    break
        if j < 0 or not gate[j]:
            continue
        if p["news"] == "ex" and nb[j]:
            continue
        cj = b["c"][j] * D                                     # decision mid in displacement space
        stop = ext + s * A[j]
        sd = stop - cj
        if not sd > 0:
            continue
        if tp_mode[0] == "r":
            f = {"r25": 0.25, "r38": 0.382, "r50": 0.5}[tp_mode]
            tp = ext - f * (ext - Lb)
            if cj - tp < tp_min * sd:
                continue
        else:
            tp = cj - float(tp_mode[1:]) * sd
        if p["side"] == "cont":                                # reflect around the decision mid
            d_sp, stop, tp = +1, cj - sd, cj + (cj - tp)
        else:
            d_sp = -1
        d = d_sp * D
        rows.append((int(ts[j]) + 60_000, d, stop * D, tp * D, sd, abs(cj - tp) / sd,
                     int(e), int(j), j - li, (C - O) / H["ref"][e], in_news))
    cols = ["t", "d", "sl", "tp", "sd", "tp_R", "e", "j", "wait", "body_k", "news"]
    df = pd.DataFrame(rows, columns=cols)
    if len(df):
        df = df.sort_values(["t", "d"], kind="stable").reset_index(drop=True)
        both = df.groupby("t")["d"].transform("nunique") > 1   # no directional tie-break
        df = df[~both].drop_duplicates("t", keep="first").reset_index(drop=True)
    return df


def _flat_ms(b, j_idx):
    """16:45 ET flatten time for an entry decided at bar j (same NY session; next day if after 18:00 ET)."""
    ny = b["ny_mod"][j_idx]
    add = np.where(ny < 1005, 1005 - ny, 1005 + 1440 - ny)
    return b["ts"][j_idx] + 60_000 * add


def orders(m1, **params):
    p = {**DEFAULTS, **params}
    df = setups(m1, **p)
    if not len(df):
        return []
    b = _base(m1)
    flat = _flat_ms(b, df["j"].values)
    tag0 = "xf" if p["side"] == "fade" else "xc"
    out = []
    for r, fl in zip(df.itertuples(index=False), flat):
        out.append({"t": int(r.t), "d": int(r.d), "kind": "mkt", "sl": float(r.sl), "tp": float(r.tp),
                    "tmax": int(p["tmax"]) * 60_000, "flat": int(fl),
                    "tag": f"{tag0}{p['tf']}{'L' if r.d > 0 else 'S'}{'N' if r.news else ''}"})
    return out
