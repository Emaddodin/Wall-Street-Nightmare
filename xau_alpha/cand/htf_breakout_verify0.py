"""
cand/htf_breakout_verify0.py - adversarial causality / implementation audit of cand/htf_breakout.py (verifier 0).

  python3 cand/htf_breakout_verify0.py trunc      # A: truncation invariance + independent signal rebuild + order semantics
  python3 cand/htf_breakout_verify0.py eval       # B: re-run sweep.evaluate (lf_base, lf_harsh, mid) + trade-level checks
  python3 cand/htf_breakout_verify0.py null       # C: random-direction + permutation null on VALID (lf_base)

TRAIN/VALID only: every order is < 2026-06-01 (htf_base truncates the frame; run_orders drops t >= TEST).
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "cand"))

from data import TEST, _ms, load_m1  # noqa: E402

BEST = {"tf": 60, "N": 55, "sl": 1.5, "ex": "r2", "hold": "multi", "fresh": 0}
VARIANTS = [
    BEST,
    {"tf": 60, "N": 55, "sl": 1.5, "ex": "ch2", "hold": "eod", "fresh": 0},
    {"tf": 60, "N": 34, "sl": 3.0, "ex": "don", "hold": "multi", "fresh": 0},
    {"tf": 240, "N": 20, "sl": 3.0, "ex": "ch3.5", "hold": "eod", "fresh": 0},
    {"tf": 60, "N": 45, "sl": 1.0, "ex": "ch2", "hold": "multi", "fresh": 1},
]
TEST_MS = _ms(TEST[0])
FIELDS = ["t", "d", "kind", "sl_dist", "tp_dist", "trail", "trail_act", "flat", "tag"]


def _key(o):
    return (o["t"], o["d"])


def _eq(a, b):
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            return a is b
        if math.isnan(a) and math.isnan(b):
            return True
        return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))
    return a == b


def trunc():
    import htf_breakout as hb
    m1 = load_m1()
    rng = np.random.default_rng(20260930)
    lo, hi = _ms("2025-03-15"), _ms("2026-05-25")
    cuts = sorted(int(x) for x in rng.integers(lo, hi, size=5))
    # two adversarial edge cuts: exactly on an H1 boundary and exactly on an H4 boundary + 1 min
    cuts += [_ms("2025-09-17") + 14 * 3_600_000, _ms("2026-03-04") + 12 * 3_600_000 + 60_000]
    report = []
    for p in VARIANTS:
        full = hb.orders(m1, **p)
        assert all(o["t"] < TEST_MS for o in full), "order in TEST"
        for T in cuts:
            cut = m1[m1.ts < T].reset_index(drop=True)
            oc = hb.orders(cut, **p)
            lim = T - 5 * 60_000
            f = {_key(o): o for o in full if o["t"] <= lim}
            c = {_key(o): o for o in oc if o["t"] <= lim}
            only_f = sorted(set(f) - set(c))
            only_c = sorted(set(c) - set(f))
            diff_fields, flat_future = [], 0
            for k in set(f) & set(c):
                for fld in FIELDS:
                    a, b = f[k].get(fld), c[k].get(fld)
                    if not _eq(a, b):
                        # a 'don' exit that is only decided AFTER the cut cannot be known in the truncated run
                        if fld == "flat" and p["ex"] == "don" and a > lim and b > a:
                            flat_future += 1
                            continue
                        diff_fields.append((k, fld, a, b))
            late = [o["t"] for o in oc if o["t"] > T + 60_000]
            report.append({"cfg": f"{p['tf']}/{p['N']}/{p['sl']}/{p['ex']}/{p['hold']}/f{p['fresh']}",
                           "T": str(pd.Timestamp(T, unit="ms", tz="UTC")), "n_full_le": len(f), "n_cut_le": len(c),
                           "only_full": len(only_f), "only_cut": len(only_c), "field_diffs": len(diff_fields),
                           "don_flat_after_cut(expected)": flat_future, "cut_orders_t>T+60s": len(late),
                           "examples": [str(x) for x in (only_f[:2] + only_c[:2] + diff_fields[:3])]})
    df = pd.DataFrame(report)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 80)
    print(df.drop(columns=["examples"]).to_string())
    bad = df[(df.only_full > 0) | (df.only_cut > 0) | (df.field_diffs > 0) | (df["cut_orders_t>T+60s"] > 0)]
    print("TRUNCATION VIOLATIONS:", len(bad))
    if len(bad):
        print(bad[["cfg", "T", "examples"]].to_string())

    # ---- independent rebuild of the BEST config's signals from raw M1 mid (pandas resample, no htf_base code) ----
    full = hb.orders(m1, **BEST)
    m = m1[m1.ts < TEST_MS]
    dt = pd.to_datetime(m["ts"], unit="ms", utc=True)
    s = pd.DataFrame({"o": m["o"].values, "h": m["h"].values, "l": m["l"].values, "c": m["c"].values,
                      "ts": m["ts"].values}, index=dt)
    H = s.resample("60min", label="left", closed="left").agg({"o": "first", "h": "max", "l": "min", "c": "last",
                                                                "ts": "last"}).dropna()
    hh = H["h"].shift(1).rolling(55, min_periods=55).max()
    ll = H["l"].shift(1).rolling(55, min_periods=55).min()
    pc = H["c"].shift(1).fillna(H["c"].iloc[0])
    tr = np.maximum(H["h"] - H["l"], np.maximum((H["h"] - pc).abs(), (H["l"] - pc).abs()))
    atr_h = tr.ewm(alpha=1 / 14, adjust=False).mean()
    H = H.assign(hh=hh, ll=ll, atr=atr_h, up=H["c"] > hh, dn=H["c"] < ll,
                 bucket_end=(H.index.astype("int64") // 1_000_000 + 3_600_000).values)
    # map each order to the H1 bucket whose end is the last one <= t
    be = H["bucket_end"].values
    mism, early, sig_bad, atr_bad = 0, 0, 0, 0
    for o in full:
        j = np.searchsorted(be, o["t"], side="right") - 1
        row = H.iloc[j]
        if o["t"] < row["bucket_end"]:
            early += 1
        want = row["up"] if o["d"] > 0 else row["dn"]
        if not want:
            sig_bad += 1
        if abs(o["sl_dist"] - 1.5 * row["atr"]) > 1e-6 * row["atr"]:
            atr_bad += 1
        if o["tp_dist"] != 2 * o["sl_dist"]:
            mism += 1
    print(f"INDEPENDENT REBUILD (best cfg): orders={len(full)} decided_before_bucket_end={early} "
          f"signal_not_confirmed={sig_bad} sl_dist!=1.5*ATR_H1={atr_bad} tp!=2*sl={mism}")
    # coverage: independent (ungated) signals vs. module orders (module applies gates; count how many are gated)
    t_ind = set(int(x) for x in H.loc[H.up | H.dn, "bucket_end"].values)
    t_mod = set(o["t"] for o in full)
    print(f"  independent signals={len(t_ind)} module orders={len(t_mod)} module_not_in_independent="
          f"{len(t_mod - t_ind)} independent_not_in_module={len(t_ind - t_mod)} (expected: gated / warm-up / "
          f"incomplete-bucket shifts)")
    # inspect module orders whose t is not a bucket end: must be LATER than the bucket end (incomplete last minute)
    off = [o for o in full if o["t"] not in t_ind]
    if off:
        dl = [(o["t"] - be[np.searchsorted(be, o["t"], side="right") - 1]) / 60_000 for o in off]
        print(f"  off-grid order delays vs bucket end (min): min={min(dl):.1f} max={max(dl):.1f} n={len(dl)}")

    # ---- order semantics ----
    sem = {"n": len(full), "t_not_minute": sum(o["t"] % 60_000 != 0 for o in full),
           "sl_dist<=0": sum(not (o["sl_dist"] > 0) for o in full),
           "tp_dist<=0": sum(not (o.get("tp_dist", 1) > 0) for o in full),
           "flat<=t": sum(o["flat"] <= o["t"] for o in full),
           "flat>LAST": sum(o["flat"] > TEST_MS - 60_000 for o in full),
           "kind!=mkt": sum(o["kind"] != "mkt" for o in full),
           "has_abs_sl_tp_px": sum(("sl" in o) or ("tp" in o) or ("px" in o) for o in full),
           "long_share": round(float(np.mean([o["d"] > 0 for o in full])), 3),
           "sl_dist_min": round(min(o["sl_dist"] for o in full), 2), "sl_dist_max": round(max(o["sl_dist"] for o in full), 2)}
    print("ORDER SEMANTICS (best cfg):", json.dumps(sem))


def evalb():
    import sweep
    from sim import load_s10
    out = {}
    trades = {}
    for c in ("base", "harsh", "mid"):
        ev, tr = sweep.evaluate("htf_breakout", BEST, cost=c, return_trades=True)
        trades[c] = tr
        t0 = tr[tr.split == 0]
        ev["train_t"] = round(float(t0.R.mean() / t0.R.std(ddof=1) * np.sqrt(len(t0))), 2)
        h = len(t0) // 2
        ev["train_halves_pf"] = [round(float(x.pnl[x.pnl > 0].sum() / -x.pnl[x.pnl < 0].sum()), 3)
                                 for x in (t0.iloc[:h], t0.iloc[h:])]
        v = tr[tr.split == 1]
        ev["valid_t"] = round(float(v.R.mean() / v.R.std(ddof=1) * np.sqrt(len(v))), 2)
        out[c] = ev
        print(c, json.dumps({k: v for k, v in ev.items() if k != "params"}, default=str))
    # ---- trade-level checks on lf_base ----
    tr = trades["base"]
    delay = (tr.t_in - tr.t_sig) / 1000
    print("fill delay s: min", delay.min(), "max", delay.max(), "| t_out max",
          pd.Timestamp(int(tr.t_out.max()), unit="ms", tz="UTC"), "| reasons", tr.reason.value_counts().to_dict())
    ov = (tr.t_in.values[1:] < tr.t_out.values[:-1]).sum()
    print("overlapping trades:", int(ov), "| risk range", round(tr.risk.min(), 2), round(tr.risk.max(), 2),
          "| flip-eligible", int(((tr.risk >= 1.2) & (tr.risk <= 4.0)).sum()))
    # ---- independent naive re-simulation at MID cost (python loop over raw S10 mid) ----
    import htf_breakout as hb
    s = load_s10()
    ts = s["ts"]
    mo = (s["ao"].astype(np.float64) + s["bo"]) / 2
    mh_l = (s["ah"].astype(np.float64) + s["bh"]) / 2      # mid high (approx.: mean of ask-high and bid-high)
    ml_l = (s["al"].astype(np.float64) + s["bl"]) / 2
    orders = sorted([o for o in hb.orders(load_m1(), **BEST) if o["t"] < TEST_MS], key=lambda o: o["t"])
    busy, rows = -1, []
    for o in orders:
        if o["t"] < busy:
            continue
        k = int(np.searchsorted(ts, o["t"]))
        if k >= len(ts) or ts[k] - o["t"] > 60_000:
            continue
        d = o["d"]
        e = mo[k]
        sl = e - d * o["sl_dist"]
        tp = e + d * o["tp_dist"]
        j = k
        while True:
            if ts[j] >= o["flat"]:
                x, why = mo[j], "time"; break
            lo_hit = (ml_l[j] <= sl) if d > 0 else (mh_l[j] >= sl)
            if lo_hit:
                x = min(sl, mo[j]) if d > 0 else max(sl, mo[j]); why = "sl"; break
            hi_hit = (mh_l[j] >= tp) if d > 0 else (ml_l[j] <= tp)
            if hi_hit and j > k:
                x = max(tp, mo[j]) if d > 0 else min(tp, mo[j]); why = "tp"; break
            j += 1
        rows.append({"t_in": int(ts[k]), "d": d, "pnl": (x - e) * d, "reason": why, "R": (x - e) * d / o["sl_dist"]})
        busy = int(ts[j]) + 10_000
    nv = pd.DataFrame(rows)
    nv["split"] = np.where(nv.t_in < _ms("2026-01-01"), 0, 1)
    tm = trades["mid"]
    for sp in (0, 1):
        a, b = nv[nv.split == sp], tm[tm.split == sp]
        pf = lambda p: round(float(p[p > 0].sum() / -p[p < 0].sum()), 3)
        print(f"NAIVE MID resim split {sp}: n={len(a)} pf={pf(a.pnl.values)} avg={a.pnl.mean():.3f} | "
              f"sim mid n={len(b)} pf={pf(b.pnl.values)} avg={b.pnl.mean():.3f}")
    mg = nv.merge(tm[["t_in", "d", "pnl", "reason"]], on=["t_in", "d"], how="outer", suffixes=("_nv", "_sim"),
                  indicator=True)
    print("naive vs sim: matched", int((mg._merge == "both").sum()), "only naive", int((mg._merge == "left_only").sum()),
          "only sim", int((mg._merge == "right_only").sum()),
          "| reason agree", int((mg.reason_nv == mg.reason_sim).sum()),
          "| max |pnl diff|", round(float((mg.pnl_nv - mg.pnl_sim).abs().max()), 3))
    json.dump(out, open(ROOT / "cand/results/htf_breakout_verify0_eval.json", "w"), default=str, indent=1)


def null():
    import sweep
    from nulltest import _apply_dir, _to_relative
    from sim import COSTS, simulate, stats
    import htf_breakout as hb
    m1 = load_m1()
    orders = [o for o in hb.orders(m1, **BEST) if o["t"] < TEST_MS]
    real = simulate(orders, COSTS["base"])
    rv = real[real.split == 1]
    rel = _to_relative(orders)
    res = {"real_valid_avg": round(float(rv.pnl.mean()), 3), "real_valid_pf": stats(rv)["pf"]}
    for kind in ("random", "perm"):
        avgs = []
        for sd in range(50):
            rng = np.random.default_rng(7000 + sd)
            if kind == "random":
                dirs = rng.choice([-1, 1], size=len(orders))
            else:
                dirs = rng.permutation([o["d"] for o in orders])
            t = simulate(_apply_dir(rel, dirs), COSTS["base"])
            t = t[t.split == 1]
            avgs.append(float(t.pnl.mean()))
        avgs = np.array(avgs)
        res[kind] = {"null_mean": round(float(avgs.mean()), 3), "null_sd": round(float(avgs.std()), 3),
                     "p": round(float(((avgs >= rv.pnl.mean()).sum() + 1) / (len(avgs) + 1)), 3)}
    print(json.dumps(res))


if __name__ == "__main__":
    {"trunc": trunc, "eval": evalb, "null": null}[sys.argv[1]]()
