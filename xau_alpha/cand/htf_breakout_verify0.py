"""
cand/htf_breakout_verify0.py - adversarial causality / implementation audit of cand/htf_breakout.py (verifier 0).

  python3 cand/htf_breakout_verify0.py trunc   # A: truncation invariance (8 cuts x 5 configs), positive control with an
                                               #    injected leak, future-scramble test, independent signal rebuild,
                                               #    order semantics
  python3 cand/htf_breakout_verify0.py eval    # B: sweep.evaluate at lf_base / lf_harsh / mid vs the report's claims,
                                               #    trade-level checks, naive independent re-simulation at mid,
                                               #    entry-delay and calendar-gate sensitivity
  python3 cand/htf_breakout_verify0.py null    # C: nulltest.random_direction (200 seeds) + permutation null, VALID
  python3 cand/htf_breakout_verify0.py trunc2  # targeted cuts inside signal buckets: real module vs injected leak;
                                               #    every non-emitted signal explained by gates / warm-up
  python3 cand/htf_breakout_verify0.py extra   # +10 s / +30 s delays, stale-order count, gap-fill P&L
  python3 cand/htf_breakout_verify0.py gaps    # each SL/TP gap fill: real market gap or data hole; PF without gaps
  python3 cand/htf_breakout_verify0.py delaydiag  # per-signal (no one-at-a-time) +0 s vs +10 s: timing vs path

TRAIN/VALID only: htf_base truncates the M1 frame at 2026-06-01 and run_orders drops t >= TEST; final=True is never
used. No lib file is modified (monkeypatches below live only inside this process). Writes small JSON files
cand/results/htf_breakout_verify0_{trunc,eval,null}.json.
"""
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "cand"))

from data import TEST, VALID, _ms, load_m1  # noqa: E402

BEST = {"tf": 60, "N": 55, "sl": 1.5, "ex": "r2", "hold": "multi", "fresh": 0}
VARIANTS = [
    BEST,
    {"tf": 60, "N": 55, "sl": 1.5, "ex": "ch2", "hold": "eod", "fresh": 0},
    {"tf": 60, "N": 34, "sl": 3.0, "ex": "don", "hold": "multi", "fresh": 0},
    {"tf": 240, "N": 20, "sl": 3.0, "ex": "ch3.5", "hold": "eod", "fresh": 0},
    {"tf": 60, "N": 45, "sl": 1.0, "ex": "ch2", "hold": "multi", "fresh": 1},
]
TEST_MS = _ms(TEST[0])
VALID_MS = _ms(VALID[0])
FIELDS = ["t", "d", "kind", "sl_dist", "tp_dist", "trail", "trail_act", "flat", "tag"]
# 4 cuts in TRAIN, 4 in VALID. Mid-hour cuts leave a partial H1/H4 bucket at the end of the cut frame; two cuts sit
# exactly on (or 1 min after) a bar boundary.
CUTS = ["2025-04-09 13:37:21.500", "2025-07-23 08:05:00", "2025-09-17 14:00:00", "2025-11-14 19:59:59.999",
        "2026-01-29 15:22:10", "2026-03-04 12:01:00", "2026-03-18 18:00:00.001", "2026-05-20 10:11:12"]
# numbers claimed by cand/reports/htf_breakout.md section 3a (and results/htf_breakout_multi_eval.json)
CLAIM = {"base": {"train": {"n": 164, "pf": 1.542, "avg_pts": 5.546}, "valid": {"n": 70, "pf": 1.073, "avg_pts": 2.219},
                  "train_t": 2.82, "valid_t": 0.74},
         "harsh": {"train": {"n": 165, "pf": 1.379, "avg_pts": 4.105}, "valid": {"n": 70, "pf": 1.063, "avg_pts": 1.929}},
         "mid": {"train": {"n": 163, "pf": 1.625, "avg_pts": 6.248}, "valid": {"n": 70, "pf": 1.079, "avg_pts": 2.389}}}
RES = ROOT / "cand/results"


def _eq(a, b):
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            return a is b
        if math.isnan(a) and math.isnan(b):
            return True
        return a == b                      # bit-exact: a causal prefix computation must give identical floats
    return a == b


def _cfg(p):
    return f"{p['tf']}/{p['N']}/{p['sl']}/{p['ex']}/{p['hold']}/f{p['fresh']}"


def compare(full, oc, T, ex):
    """Orders with t <= T - 5 min must be identical in the full and the cut run."""
    lim = T - 5 * 60_000
    fl = [o for o in full if o["t"] <= lim]
    cl = [o for o in oc if o["t"] <= lim]
    f = {(o["t"], o["d"]): o for o in fl}
    c = {(o["t"], o["d"]): o for o in cl}
    dup = (len(fl) - len(f)) + (len(cl) - len(c))
    only_f = sorted(set(f) - set(c))
    only_c = sorted(set(c) - set(f))
    diffs, don_after_cut = [], 0
    for k in sorted(set(f) & set(c)):
        for fld in FIELDS:
            a, b = f[k].get(fld), c[k].get(fld)
            if not _eq(a, b):
                # a 'don' exit bar that closes after the cut cannot exist in the cut frame (not a leak)
                if fld == "flat" and ex == "don" and a > lim and b > a:
                    don_after_cut += 1
                    continue
                diffs.append((k, fld, a, b))
    late = sum(o["t"] > T + 60_000 for o in oc)
    return {"n_full_le": len(f), "n_cut_le": len(c), "dup_keys": dup, "only_full": len(only_f),
            "only_cut": len(only_c), "field_diffs": len(diffs), "don_flat_after_cut": don_after_cut,
            "cut_orders_after_T": late,
            "examples": [str(x) for x in (only_f[:2] + only_c[:2] + diffs[:3])]}


def trunc():
    import htf_base
    import htf_breakout as hb
    m1 = load_m1()
    cuts = [_ms(s) for s in CUTS]
    out = {"cuts": CUTS, "rows": []}
    t0 = time.time()
    for p in VARIANTS:
        full = hb.orders(m1, **p)
        assert all(o["t"] < TEST_MS for o in full), "order in TEST"
        for T, Ts in zip(cuts, CUTS):
            oc = hb.orders(m1[m1.ts < T].reset_index(drop=True), **p)
            r = compare(full, oc, T, p["ex"])
            out["rows"].append({"cfg": _cfg(p), "T": Ts, **r})
    df = pd.DataFrame(out["rows"])
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 90)
    print(df.drop(columns=["examples"]).to_string(), flush=True)
    bad = df[(df.only_full > 0) | (df.only_cut > 0) | (df.field_diffs > 0) | (df.cut_orders_after_T > 0)]
    out["violations"] = int(len(bad))
    out["compared_orders"] = int(df.n_full_le.sum())
    print(f"TRUNCATION VIOLATIONS: {len(bad)} of {len(df)} (cfg, cut) pairs; compared orders {df.n_full_le.sum()}"
          f" [{time.time() - t0:.0f}s]", flush=True)
    if len(bad):
        print(bad[["cfg", "T", "examples"]].to_string(), flush=True)

    # ---- positive control: inject a known leak (HTF bucket 'known' at its FIRST minute) and re-run the same test ----
    real_rs = htf_base.resample_causal

    def leaky(m, minutes):
        bars, _ = real_rs(m, minutes)
        known_at = bars["first_i"].values
        return bars, np.searchsorted(known_at, np.arange(len(m)), side="right") - 1

    htf_base.resample_causal = leaky
    htf_base._C.clear()
    rng = np.random.default_rng(930)
    pc_cuts = sorted(int(x) for x in rng.integers(_ms("2025-03-01"), _ms("2026-05-25"), size=30))
    fullL = hb.orders(m1, **BEST)
    det = 0
    for T in pc_cuts:
        r = compare(fullL, hb.orders(m1[m1.ts < T].reset_index(drop=True), **BEST), T, "r2")
        det += int(r["only_full"] + r["only_cut"] + r["field_diffs"] > 0)
    htf_base.resample_causal = real_rs
    htf_base._C.clear()
    out["positive_control"] = {"leak": "HTF bucket known at its first minute", "cuts": len(pc_cuts), "detected": det}
    print(f"POSITIVE CONTROL (injected leak): detected at {det} of {len(pc_cuts)} random cuts", flush=True)

    # ---- future-scramble: same frame length, prices after T replaced by a mirrored path (cache must be cleared,
    #      htf_base.base keys its cache on (len, first ts, last ts), which a same-length frame would hit) ----
    full = hb.orders(m1, **BEST)
    scr = []
    for Ts in (CUTS[1], CUTS[4], CUTS[7]):
        T = _ms(Ts)
        m2 = m1.copy()
        fut = m2.ts.values >= T
        ref = float(m2.loc[~fut, "c"].values[-1])
        for col in "ohlc":
            m2.loc[fut, col] = 2 * ref - m1.loc[fut, col].values
        m2.loc[fut, ["h", "l"]] = m2.loc[fut, ["l", "h"]].values        # mirrored path: swap high/low
        htf_base._C.clear()
        o2 = hb.orders(m2, **BEST)
        htf_base._C.clear()
        r = compare(full, o2, T, "r2")
        after_diff = len({(o["t"], o["d"]) for o in full if o["t"] > T + 3_600_000}
                         ^ {(o["t"], o["d"]) for o in o2 if o["t"] > T + 3_600_000})
        scr.append({"T": Ts, **{k: r[k] for k in ("n_full_le", "only_full", "only_cut", "field_diffs")},
                    "orders_after_T_that_changed(should be >0)": after_diff})
    out["future_scramble"] = scr
    print("FUTURE SCRAMBLE:", json.dumps(scr), flush=True)

    # ---- independent rebuild of the BEST config's signals from raw M1 mid (pandas resample, no htf_base code) ----
    full = hb.orders(m1, **BEST)
    m = m1[m1.ts < TEST_MS]
    dt = pd.to_datetime(m["ts"], unit="ms", utc=True)
    s = pd.DataFrame({"o": m["o"].values, "h": m["h"].values, "l": m["l"].values, "c": m["c"].values,
                      "ts": m["ts"].values}, index=dt.values)
    H = s.resample("60min", label="left", closed="left").agg({"o": "first", "h": "max", "l": "min", "c": "last",
                                                               "ts": "last"}).dropna()
    hh = H["h"].shift(1).rolling(55, min_periods=55).max()
    ll = H["l"].shift(1).rolling(55, min_periods=55).min()
    pc = H["c"].shift(1).fillna(H["c"].iloc[0])
    tr = np.maximum(H["h"] - H["l"], np.maximum((H["h"] - pc).abs(), (H["l"] - pc).abs()))
    atr_h = tr.ewm(alpha=1 / 14, adjust=False).mean()
    H = H.assign(hh=hh, ll=ll, atr=atr_h, up=H["c"] > hh, dn=H["c"] < ll,
                 bucket_end=(H.index.astype("int64") // 1_000_000 + 3_600_000).values)
    be = H["bucket_end"].values
    delay, sig_bad, atr_bad, tp_bad, last_min_missing = [], 0, 0, 0, 0
    for o in full:
        j = np.searchsorted(be, o["t"], side="right") - 1
        row = H.iloc[j]
        delay.append((o["t"] - row["bucket_end"]) / 60_000)
        if not (row["up"] if o["d"] > 0 else row["dn"]):
            sig_bad += 1
        if abs(o["sl_dist"] - 1.5 * row["atr"]) > 1e-9 * row["atr"]:
            atr_bad += 1
        if o["tp_dist"] != 2 * o["sl_dist"]:
            tp_bad += 1
        if row["ts"] != row["bucket_end"] - 60_000:
            last_min_missing += 1
    delay = np.array(delay)
    t_ind = set(int(x) for x in H.loc[H.up | H.dn, "bucket_end"].values)
    t_mod = set(o["t"] for o in full)
    rb = {"orders": len(full), "t_minus_bucket_end_min": {"==0": int((delay == 0).sum()), "min": float(delay.min()),
                                                          "max": float(delay.max())},
          "signal_not_confirmed_by_rebuild": sig_bad, "sl_dist_ne_1.5xATR_H1": atr_bad, "tp_ne_2xsl": tp_bad,
          "signal_bucket_last_minute_missing": last_min_missing,
          "rebuild_signal_times": len(t_ind), "module_order_times": len(t_mod),
          "module_times_not_in_rebuild": len(t_mod - t_ind), "rebuild_times_not_in_module": len(t_ind - t_mod)}
    out["rebuild"] = rb
    print("INDEPENDENT REBUILD (best cfg):", json.dumps(rb), flush=True)

    # ---- order semantics vs sim.py ----
    sem = {"n": len(full), "t_not_minute_aligned": sum(o["t"] % 60_000 != 0 for o in full),
           "sl_dist<=0_or_nan": sum(not (o["sl_dist"] > 0) for o in full),
           "tp_dist!=2sl": sum(not (o.get("tp_dist") == 2 * o["sl_dist"]) for o in full),
           "flat<=t": sum(o["flat"] <= o["t"] for o in full),
           "flat>2026-05-31T23:59": sum(o["flat"] > TEST_MS - 60_000 for o in full),
           "flat_ne_min(t+10d,LAST)": sum(o["flat"] != min(TEST_MS - 60_000, o["t"] + 10 * 86_400_000) for o in full),
           "kind!=mkt": sum(o["kind"] != "mkt" for o in full),
           "abs_sl_tp_px_or_trail": sum(any(k in o for k in ("sl", "tp", "px", "trail", "be", "tmax")) for o in full),
           "long_share": round(float(np.mean([o["d"] > 0 for o in full])), 3),
           "t>=TEST": sum(o["t"] >= TEST_MS for o in full),
           "sl_dist_min": round(min(o["sl_dist"] for o in full), 2),
           "sl_dist_max": round(max(o["sl_dist"] for o in full), 2)}
    out["semantics"] = sem
    print("ORDER SEMANTICS (best cfg):", json.dumps(sem), flush=True)
    json.dump(out, open(RES / "htf_breakout_verify0_trunc.json", "w"), default=str, indent=1)


def trunc2():
    """Targeted truncation: cuts 37m21s into 40 H1 buckets that carry a finalist signal (20 TRAIN, 20 VALID), on the
    real module and on the injected-leak module (power check); plus: every rebuilt signal the module did NOT emit
    must be explained by the gates or the warm-up (no signal is dropped for an unexplained reason)."""
    import htf_base
    import htf_breakout as hb
    m1 = load_m1()
    full = hb.orders(m1, **BEST)
    tt = np.array(sorted({o["t"] for o in full}))
    tt = tt[tt % 3_600_000 == 0]                                   # orders exactly at an H1 bucket end
    rng = np.random.default_rng(31)
    pick = np.r_[rng.choice(tt[tt < VALID_MS], 20, replace=False), rng.choice(tt[tt >= VALID_MS], 20, replace=False)]
    cuts = sorted(int(t - 3_600_000 + 37 * 60_000 + 21_000) for t in pick)
    out = {"n_cuts": len(cuts)}
    real_rs = htf_base.resample_causal

    def leaky(m, minutes):
        bars, _ = real_rs(m, minutes)
        known_at = bars["first_i"].values
        return bars, np.searchsorted(known_at, np.arange(len(m)), side="right") - 1

    for nm, rs in (("real", real_rs), ("injected_leak", leaky)):
        htf_base.resample_causal = rs
        htf_base._C.clear()
        f = hb.orders(m1, **BEST)
        viol, compared = 0, 0
        for T in cuts:
            r = compare(f, hb.orders(m1[m1.ts < T].reset_index(drop=True), **BEST), T, "r2")
            viol += int(r["only_full"] + r["only_cut"] + r["field_diffs"] + r["cut_orders_after_T"] > 0)
            compared += r["n_full_le"]
        out[nm] = {"cuts_with_violation": viol, "compared_orders": compared}
        print(f"TARGETED TRUNCATION [{nm}]: violations at {viol} of {len(cuts)} cuts, compared orders {compared}",
              flush=True)
    htf_base.resample_causal = real_rs
    htf_base._C.clear()

    # ---- coverage: rebuilt (ungated) signals that the module did not emit ----
    b = htf_base.base(m1)
    H = htf_base.htf(b, 60)
    full = hb.orders(m1, **BEST)
    t_mod = {o["t"] for o in full}
    up, dn, _, _ = hb._signals(b, 60, 55, 0)
    why = {"emitted": 0, "warmup": 0, "gate_session": 0, "gate_news": 0, "unknown_at_cut": 0, "UNEXPLAINED": 0}
    from data import news_block_mask
    news = news_block_mask(b["ts"], before_min=5, after_min=15)
    for k in np.flatnonzero(up | dn):
        i = H["ki"][k]
        if i >= b["n"]:
            why["unknown_at_cut"] += 1
            continue
        t = int(b["ts"][i]) + 60_000
        if t in t_mod:
            why["emitted"] += 1
        elif k < 55 + 14 or not np.isfinite(H["atr"][k]):
            why["warmup"] += 1
        elif not b["gate"][i]:
            why["gate_news" if news[i] else "gate_session"] += 1
        else:
            why["UNEXPLAINED"] += 1
    out["signal_coverage"] = why
    print("SIGNAL COVERAGE (module signals vs emitted orders):", json.dumps(why), flush=True)
    json.dump(out, open(RES / "htf_breakout_verify0_trunc2.json", "w"), default=str, indent=1)


def _pf(p):
    p = np.asarray(p, dtype=float)
    gl = -p[p < 0].sum()
    return round(float(p[p > 0].sum() / gl), 3) if gl > 0 else float("inf")


def evalb():
    import sweep
    import htf_base
    import htf_breakout as hb
    from sim import COSTS, load_s10, simulate, stats
    out, trades = {}, {}
    for c in ("base", "harsh", "mid"):
        t0 = time.time()
        ev, tr = sweep.evaluate("htf_breakout", BEST, cost=c, return_trades=True)
        trades[c] = tr
        for sp, nm in ((0, "train"), (1, "valid")):
            x = tr[tr.split == sp]
            ev[f"{nm}_t"] = round(float(x.R.mean() / x.R.std(ddof=1) * np.sqrt(len(x))), 2)
        t0s = tr[tr.split == 0]
        h = len(t0s) // 2
        ev["train_halves_pf"] = [stats(t0s.iloc[:h]).get("pf"), stats(t0s.iloc[h:]).get("pf")]
        chk = {}
        for sp in ("train", "valid"):
            for k, v in CLAIM[c][sp].items():
                chk[f"{sp}_{k}"] = (ev[sp][k], v, "OK" if ev[sp][k] == v else "DIFF")
        for k in ("train_t", "valid_t"):
            if k in CLAIM[c]:
                chk[k] = (ev[k], CLAIM[c][k], "OK" if ev[k] == CLAIM[c][k] else "DIFF")
        ev["claim_check"] = chk
        out[c] = ev
        print(c, f"[{time.time() - t0:.0f}s]", json.dumps({k: v for k, v in ev.items() if k != "params"},
                                                          default=str), flush=True)
    # ---- trade-level checks (lf_base) ----
    m1 = load_m1()
    orders = sorted([o for o in hb.orders(m1, **BEST) if o["t"] < TEST_MS], key=lambda o: o["t"])
    sd_of = {o["t"]: o["sl_dist"] for o in orders}
    tr = trades["base"]
    delay = (tr.t_in - tr.t_sig) / 1000
    ov = int((tr.t_in.values[1:] < tr.t_out.values[:-1]).sum())
    x_boundary = tr[(tr.split == 0) & (tr.t_out >= VALID_MS)]
    risk_err = np.abs(tr.risk.values - np.array([sd_of[t] for t in tr.t_sig.values]))
    tl = {"orders": len(orders), "fill_delay_s_min": float(delay.min()), "fill_delay_s_max": float(delay.max()),
          "t_out_max": str(pd.Timestamp(int(tr.t_out.max()), unit="ms", tz="UTC")),
          "t_out>TEST": int((tr.t_out > TEST_MS).sum()),
          "reasons": tr.reason.value_counts().to_dict(), "overlapping_trades": ov,
          "risk_min_max": [round(float(tr.risk.min()), 2), round(float(tr.risk.max()), 2)],
          "max_abs(risk - order sl_dist)": float(risk_err.max()),
          "flip_eligible": int(((tr.risk >= 1.2) & (tr.risk <= 4.0)).sum()),
          "train_trades_exiting_in_valid": {"n": int(len(x_boundary)), "pnl": round(float(x_boundary.pnl.sum()), 2)}}
    out["trade_level"] = tl
    print("TRADE LEVEL:", json.dumps(tl, default=str), flush=True)

    # ---- naive independent re-simulation at MID (python loop over raw S10 mid; no sim.py code) ----
    s = load_s10()
    ts = s["ts"]
    mo = (s["ao"].astype(np.float64) + s["bo"]) / 2
    mh = (s["ah"].astype(np.float64) + s["bh"]) / 2
    ml = (s["al"].astype(np.float64) + s["bl"]) / 2
    busy, rows = -1, []
    for o in orders:
        if o["t"] < busy:
            continue
        k = int(np.searchsorted(ts, o["t"]))
        if k >= len(ts) or ts[k] - o["t"] > 60_000:
            continue
        d = o["d"]
        e = mo[k]
        sl, tp = e - d * o["sl_dist"], e + d * o["tp_dist"]
        j = k
        while True:
            if ts[j] >= o["flat"]:
                x, why = mo[j], "time"
                break
            if (ml[j] <= sl) if d > 0 else (mh[j] >= sl):
                x = min(sl, mo[j]) if d > 0 else max(sl, mo[j])
                why = "sl"
                break
            if j > k and ((mh[j] >= tp) if d > 0 else (ml[j] <= tp)):
                x = max(tp, mo[j]) if d > 0 else min(tp, mo[j])
                why = "tp"
                break
            j += 1
        rows.append({"t_in": int(ts[k]), "d": d, "pnl": (x - e) * d, "reason": why})
        busy = int(ts[j]) + 10_000
    nv = pd.DataFrame(rows)
    nv["split"] = np.where(nv.t_in < VALID_MS, 0, 1)
    tm = trades["mid"]
    nres = {}
    for sp, nm in ((0, "train"), (1, "valid")):
        a, b = nv[nv.split == sp], tm[tm.split == sp]
        nres[nm] = {"naive_n": len(a), "naive_pf": _pf(a.pnl), "naive_avg": round(float(a.pnl.mean()), 3),
                    "sim_mid_n": len(b), "sim_mid_pf": _pf(b.pnl), "sim_mid_avg": round(float(b.pnl.mean()), 3)}
    mg = nv.merge(tm[["t_in", "d", "pnl", "reason"]], on=["t_in", "d"], how="outer", suffixes=("_nv", "_sim"),
                  indicator=True)
    both = mg[mg._merge == "both"]
    nres["match"] = {"both": int(len(both)), "only_naive": int((mg._merge == "left_only").sum()),
                     "only_sim": int((mg._merge == "right_only").sum()),
                     "reason_agree": int((both.reason_nv == both.reason_sim).sum()),
                     "median_abs_pnl_diff": round(float((both.pnl_nv - both.pnl_sim).abs().median()), 3),
                     "max_abs_pnl_diff": round(float((both.pnl_nv - both.pnl_sim).abs().max()), 3)}
    out["naive_mid_resim"] = nres
    print("NAIVE MID RESIM:", json.dumps(nres), flush=True)

    # ---- entry-delay sensitivity (lf_base): same orders, executed later ----
    dl = {}
    for dsec in (0, 60, 300, 900):
        od = [dict(o, t=o["t"] + dsec * 1000) for o in orders]
        x = sweep.run_orders(od, "base")
        dl[f"+{dsec}s"] = {nm: {k: stats(x[x.split == sp]).get(k) for k in ("n", "pf", "avg_pts")}
                           for sp, nm in ((0, "train"), (1, "valid"))}
    out["entry_delay_lf_base"] = dl
    print("ENTRY DELAY (lf_base):", json.dumps(dl), flush=True)

    # ---- calendar-gate sensitivity: the only external input; recompute orders without it (in-process patch) ----
    gs = {}
    real_nbm = htf_base.news_block_mask
    for nm, fn in (("no_news_gate", lambda ts_, **kw: np.zeros(len(ts_), dtype=bool)),):
        htf_base.news_block_mask = fn
        htf_base._C.clear()
        od = hb.orders(m1, **BEST)
        x = sweep.run_orders(od, "base")
        gs[nm] = {"orders": len(od), **{s_: {k: stats(x[x.split == sp]).get(k) for k in ("n", "pf", "avg_pts")}
                                        for sp, s_ in ((0, "train"), (1, "valid"))}}
    htf_base.news_block_mask = real_nbm
    htf_base._C.clear()
    gs["with_gate_orders"] = len(hb.orders(m1, **BEST))
    out["gate_sensitivity_lf_base"] = gs
    print("GATE SENSITIVITY (lf_base):", json.dumps(gs), flush=True)
    json.dump(out, open(RES / "htf_breakout_verify0_eval.json", "w"), default=str, indent=1)


def null():
    import htf_breakout as hb
    from nulltest import _apply_dir, _to_relative, random_direction
    from sim import COSTS, simulate, stats
    m1 = load_m1()
    orders = [o for o in hb.orders(m1, **BEST) if o["t"] < TEST_MS]
    real = simulate(orders, COSTS["base"])
    rv = real[real.split == 1]
    ra, rpf = float(rv.pnl.mean()), stats(rv)["pf"]
    res = {"real_valid_avg": round(ra, 3), "real_valid_pf": rpf, "real_valid_n": int(len(rv))}
    t0 = time.time()
    rd = random_direction(orders, seeds=range(200), cost="base", split=1)       # library null, all orders, VALID
    res["random_direction_200"] = {
        "null_mean_avg": round(float(rd.avg_pts.mean()), 3), "null_sd_avg": round(float(rd.avg_pts.std()), 3),
        "p_avg": round(float(((rd.avg_pts >= ra).sum() + 1) / (len(rd) + 1)), 4),
        "p_pf": round(float(((rd.pf >= rpf).sum() + 1) / (len(rd) + 1)), 4), "secs": round(time.time() - t0)}
    print("RANDOM DIRECTION:", json.dumps(res["random_direction_200"]), flush=True)
    rel = _to_relative(orders)
    d0 = np.array([o["d"] for o in orders])
    avgs, pfs = [], []
    for sd in range(200):
        t = simulate(_apply_dir(rel, np.random.default_rng(9000 + sd).permutation(d0)), COSTS["base"])
        t = t[t.split == 1]
        avgs.append(float(t.pnl.mean()))
        pfs.append(stats(t)["pf"])
    avgs, pfs = np.array(avgs), np.array(pfs)
    res["permutation_200"] = {"null_mean_avg": round(float(avgs.mean()), 3), "null_sd_avg": round(float(avgs.std()), 3),
                              "p_avg": round(float(((avgs >= ra).sum() + 1) / 201), 4),
                              "p_pf": round(float(((pfs >= rpf).sum() + 1) / 201), 4)}
    print("PERMUTATION:", json.dumps(res["permutation_200"]), flush=True)
    json.dump(res, open(RES / "htf_breakout_verify0_null.json", "w"), default=str, indent=1)


def extra():
    """Realistic-latency delays (+10 s = next 10-s bar, +30 s) and the P&L share of gap fills beyond SL/TP (lf_base)."""
    import sweep
    import htf_breakout as hb
    from sim import stats
    from sim import load_s10
    m1 = load_m1()
    orders = sorted([o for o in hb.orders(m1, **BEST) if o["t"] < TEST_MS], key=lambda o: o["t"])
    s10 = load_s10()["ts"]
    tt = np.array([o["t"] for o in orders])
    k0 = np.searchsorted(s10, tt, side="left")
    gap = s10[np.minimum(k0, len(s10) - 1)] - tt
    out = {"orders_without_10s_bar_within_60s(dropped_as_stale)": int((gap > 60_000).sum()),
           "orders_filled_on_bar_opening_exactly_at_t": int((gap == 0).sum()), "orders": len(orders)}
    for dsec in (10, 30):
        x = sweep.run_orders([dict(o, t=o["t"] + dsec * 1000) for o in orders], "base")
        out[f"delay+{dsec}s"] = {nm: {k: stats(x[x.split == sp]).get(k) for k in ("n", "pf", "avg_pts")}
                                 for sp, nm in ((0, "train"), (1, "valid"))}
    x = sweep.run_orders(orders, "base")
    move = (x.exit.values - x.entry.values) * x.d.values          # favourable move, before commission
    risk = x.risk.values
    tp, sl = (x.reason == "tp").values, (x.reason == "sl").values
    bonus = np.where(tp, move - 2 * risk, 0.0)                     # > 0: target filled beyond the level (gap)
    pen = np.where(sl, -move - risk - 0.10, 0.0)                   # > 0: stop filled beyond level + slip (gap)
    for sp, nm in ((0, "train"), (1, "valid")):
        m = (x.split == sp).values
        pnl = x.pnl.values[m]
        capped = pnl - bonus[m]                                    # TP gap fills removed (fill exactly at target)
        out[f"gaps_{nm}"] = {"tp_gap_n": int((bonus[m] > 0.01).sum()), "tp_gap_sum": round(float(bonus[m].sum()), 2),
                             "sl_gap_n": int((pen[m] > 0.01).sum()), "sl_gap_sum": round(float(pen[m].sum()), 2),
                             "pf": _pf(pnl), "pf_tp_gaps_removed": _pf(capped),
                             "net_pnl": round(float(pnl.sum()), 1)}
    print("EXTRA:", json.dumps(out), flush=True)
    json.dump(out, open(RES / "htf_breakout_verify0_extra.json", "w"), default=str, indent=1)


def gaps():
    """Every SL/TP that filled beyond its level (lf_base): is the jump a real market gap (weekend / daily break) or a
    hole in the Dukascopy tick data while the market traded? Also PF with the gap effects removed symmetrically."""
    import sweep
    import htf_breakout as hb
    from sim import broker_arrays, COSTS
    m1 = load_m1()
    orders = sorted([o for o in hb.orders(m1, **BEST) if o["t"] < TEST_MS], key=lambda o: o["t"])
    x = sweep.run_orders(orders, "base").reset_index(drop=True)
    arr = broker_arrays(COSTS["base"])
    ts = arr["ts"]
    mid_o = (arr["ao"].astype(np.float64) + arr["bo"]) / 2
    mid_c = (arr["ac"].astype(np.float64) + arr["bc"]) / 2
    move = (x.exit.values - x.entry.values) * x.d.values
    risk = x.risk.values
    tp, sl = (x.reason == "tp").values, (x.reason == "sl").values
    bonus = np.where(tp, move - 2 * risk, 0.0)
    pen = np.where(sl, -move - risk - 0.10, 0.0)
    rows = []
    for j in np.flatnonzero((bonus > 0.01) | (pen > 0.01)):
        kx = int(np.searchsorted(ts, int(x.t_out[j]) - 10_000))       # exit bar
        prev = ts[kx - 1]
        tx = pd.Timestamp(int(ts[kx]), unit="ms", tz="UTC")
        tp_ = pd.Timestamp(int(prev), unit="ms", tz="UTC")
        m1_hole = int(((m1.ts.values > prev) & (m1.ts.values < ts[kx])).sum())   # M1 bars inside the S10 hole
        rows.append({"split": int(x.split[j]), "d": int(x.d[j]), "reason": x.reason[j],
                     "t_in": str(pd.Timestamp(int(x.t_in[j]), unit="ms", tz="UTC")),
                     "exit_bar": str(tx), "prev_bar": str(tp_), "hole_min": round((ts[kx] - prev) / 60_000, 1),
                     "exit_dow": tx.day_name()[:3], "m1_bars_inside_hole": m1_hole,
                     "mid_jump": round(float(mid_o[kx] - mid_c[kx - 1]), 2),
                     "beyond_level": round(float(bonus[j] if tp[j] else -pen[j]), 2), "risk": round(float(risk[j]), 2)})
    g = pd.DataFrame(rows)
    pd.set_option("display.width", 250)
    print(g.to_string(), flush=True)
    out = {"fills": rows}
    for sp, nm in ((0, "train"), (1, "valid")):
        m = (x.split == sp).values
        p = x.pnl.values[m]
        out[nm] = {"pf": _pf(p), "pf_tp_gaps_removed": _pf(p - bonus[m]), "pf_sl_gaps_removed": _pf(p + pen[m]),
                   "pf_both_removed": _pf(p - bonus[m] + pen[m]), "sum_tp_bonus": round(float(bonus[m].sum()), 2),
                   "sum_sl_pen": round(float(pen[m].sum()), 2), "net_pnl": round(float(p.sum()), 2)}
    print("GAPS:", json.dumps({k: v for k, v in out.items() if k != "fills"}), flush=True)
    json.dump(out, open(RES / "htf_breakout_verify0_gaps.json", "w"), default=str, indent=1)


def delaydiag():
    """Is the +10 s delay swing a timing effect or path reshuffling? Simulate every signal as its own trade
    (one_at_a_time=False; a DIAGNOSTIC, not a config) at +0 s and +10 s, and compare the one-at-a-time sequences."""
    import sweep
    import htf_breakout as hb
    from sim import COSTS, simulate
    orders = sorted([o for o in hb.orders(load_m1(), **BEST) if o["t"] < TEST_MS], key=lambda o: o["t"])
    res = {}
    for dsec in (0, 10):
        tr = simulate([dict(o, t=o["t"] + dsec * 1000) for o in orders], COSTS["base"], one_at_a_time=False)
        tr["sig"] = tr["t_sig"] - dsec * 1000
        res[dsec] = tr.set_index("sig")
    a, b = res[0], res[10]
    common = a.index.intersection(b.index)
    d_entry = ((b.loc[common, "entry"] - a.loc[common, "entry"]) * a.loc[common, "d"]).values   # > 0: worse
    out = {"per_signal": {f"{nm}_+{dsec}s": {"n": int((tr.split == sp).sum()), "pf": _pf(tr.pnl[tr.split == sp]),
                                              "avg": round(float(tr.pnl[tr.split == sp].mean()), 3)}
                          for sp, nm in ((0, "train"), (1, "valid")) for dsec, tr in res.items()},
           "entry_worse_by_10s_delay": {"mean": round(float(d_entry.mean()), 3),
                                        "median": round(float(np.median(d_entry)), 3),
                                        "p10": round(float(np.percentile(d_entry, 10)), 3),
                                        "p90": round(float(np.percentile(d_entry, 90)), 3), "n": int(len(d_entry))}}
    s0 = sweep.run_orders(orders, "base")
    s10 = sweep.run_orders([dict(o, t=o["t"] + 10_000) for o in orders], "base")
    k0, k10 = set(s0.t_sig), set(s10.t_sig - 10_000)
    out["sequence_overlap"] = {"trades_+0s": len(k0), "trades_+10s": len(k10), "same_signal": len(k0 & k10)}
    print("DELAY DIAG:", json.dumps(out), flush=True)
    json.dump(out, open(RES / "htf_breakout_verify0_delaydiag.json", "w"), default=str, indent=1)


if __name__ == "__main__":
    {"trunc": trunc, "trunc2": trunc2, "eval": evalb, "null": null, "extra": extra, "gaps": gaps,
     "delaydiag": delaydiag}[sys.argv[1]]()
