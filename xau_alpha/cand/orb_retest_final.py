"""
cand/orb_retest_final.py - full TRAIN + VALID evaluation of the frozen H6 finalist (never TEST; every order list is cut
at 2026-06-01 by orb_retest._base and again by sweep.run_orders).

    python3 cand/orb_retest_final.py '<params json>' [--rd 200] [--perm 200] [--rand 100] [--tag final]

Contents (all MEASURED on TRAIN 2025-01-21..2025-12-31 and VALID 2026-01..05):
  * stats at lf_base / lf_harsh / mid; long and short separately; t-stat; TRAIN halves; PF without the best month
  * exits, stop-distance percentiles, flip-eligible share ([1.2, 4.0] $/oz), and the cap='skip' variant
  * beta checks: market drift per split; the same signals forced ALWAYS LONG and ALWAYS SHORT (same geometry);
    direction-permutation null (shuffle the realised long/short labels inside each split: keeps the long share,
    so it controls for a bull-market long bias); break-direction hit rate
  * nulltest.random_direction (rd seeds, each split simulated on its own orders)
  * random-window null (window='rand', matched days and holding horizon)
  * entry-mode arms (brk / rt5 / rt30 / nt5 / nt30), neighbour robustness (single-parameter moves), monthly table
Writes results/orb_retest_<tag>_eval.json and results/orb_retest_<tag>_monthly.csv (small; no trade lists).
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "cand"))

from data import _ms, load_m1  # noqa: E402
from nulltest import _apply_dir, _to_relative, random_direction  # noqa: E402
from sim import COSTS, simulate, stats  # noqa: E402
from sweep import FLIP_STOP, evaluate, run_orders  # noqa: E402

import orb_retest as M  # noqa: E402

VALID_MS = _ms("2026-01-01")


def tstat(tr):
    if len(tr) < 2:
        return None
    return round(float(tr["R"].mean() / (tr["R"].std(ddof=1) + 1e-12) * np.sqrt(len(tr))), 2)


def drop_best_month_pf(tr):
    if len(tr) == 0:
        return None
    mon = pd.to_datetime(tr["t_in"], unit="ms", utc=True).dt.strftime("%Y-%m")
    best = tr.groupby(mon)["pnl"].sum().idxmax()
    return stats(tr[mon != best]).get("pf")


def _pv(null_vals, actual):
    x = np.asarray(null_vals, dtype=float)
    x = x[np.isfinite(x)]
    return round(float(((x >= actual).sum() + 1) / (len(x) + 1)), 4), int(len(x))


def split_orders(ords):
    return {0: [o for o in ords if o["t"] < VALID_MS], 1: [o for o in ords if VALID_MS <= o["t"] < M.TEST_MS]}


def main(p, rd=200, perm=200, nrand=100, tag="final", log=print):
    t0 = time.time()
    m1 = load_m1()
    out = {"params": p}
    trades = {}
    # ---- core stats per cost
    for cost in ("lf_base", "lf_harsh", "mid"):
        ev, tr = evaluate("orb_retest", p, cost=cost, return_trades=True)
        trades[cost] = tr
        row = {k: v for k, v in ev.items() if k != "params"}
        for sp, nm in ((0, "train"), (1, "valid")):
            t = tr[tr.split == sp]
            row[f"{nm}_t"] = tstat(t)
            row[f"{nm}_drop_best_month_pf"] = drop_best_month_pf(t)
            row[f"{nm}_exits"] = t["reason"].value_counts().to_dict()
            row[f"{nm}_risk_q10_25_50_75_90"] = [round(float(x), 2) for x in np.percentile(t["risk"], [10, 25, 50, 75, 90])]
            fe = t[(t.risk >= FLIP_STOP[0]) & (t.risk <= FLIP_STOP[1])]
            row[f"{nm}_flip_share"] = round(len(fe) / max(len(t), 1), 3)
            row[f"{nm}_long_share"] = round(float((t.d > 0).mean()), 3)
            h = len(t) // 2
            row[f"{nm}_halves_pf"] = [stats(t.iloc[:h]).get("pf"), stats(t.iloc[h:]).get("pf")]
            row[f"{nm}_t_long"] = tstat(t[t.d > 0])
            row[f"{nm}_t_short"] = tstat(t[t.d < 0])
        out[cost] = row
        log(f"{cost} TRAIN {row['train']}\n   VALID {row['valid']}\n   L/S train {row.get('train_long')} | "
            f"{row.get('train_short')}\n   L/S valid {row.get('valid_long')} | {row.get('valid_short')}")
    tr = trades["lf_base"]

    # ---- monthly table (lf_base), long and short R separately
    mon = pd.to_datetime(tr["t_in"], unit="ms", utc=True).dt.strftime("%Y-%m")
    g = tr.assign(mon=mon, L=(tr.d > 0).astype(int), S=(tr.d < 0).astype(int),
                  RL=np.where(tr.d > 0, tr.R, 0.0), RS=np.where(tr.d < 0, tr.R, 0.0)).groupby("mon")
    mt = pd.DataFrame({"split": g["split"].first().map({0: "T", 1: "V"}), "n": g.size(), "L": g.L.sum(),
                       "S": g.S.sum(), "sum_R": g.R.sum().round(2), "sum_R_long": g.RL.sum().round(2),
                       "sum_R_short": g.RS.sum().round(2), "avg_pts": g.pnl.mean().round(2),
                       "pf": g.apply(lambda x: stats(x).get("pf")), "avg_risk": g.risk.mean().round(2)})
    mt.to_csv(ROOT / f"cand/results/orb_retest_{tag}_monthly.csv")
    out["monthly"] = mt.reset_index().to_dict(orient="records")
    log(mt.to_string())

    # ---- beta checks
    c = m1["c"].values
    ts = m1["ts"].values
    drift = {}
    for sp, (a, b) in ((0, ("2025-01-21", "2026-01-01")), (1, ("2026-01-01", "2026-06-01"))):
        ia, ib = np.searchsorted(ts, _ms(a)), np.searchsorted(ts, _ms(b)) - 1
        drift[sp] = {"start": round(float(c[ia]), 1), "end": round(float(c[ib]), 1),
                     "ret_pct": round(float(c[ib] / c[ia] - 1) * 100, 1)}
    out["market_drift"] = {"train": drift[0], "valid": drift[1]}
    ords = [o for o in M.orders(m1, **p) if o["t"] < M.TEST_MS]
    so = split_orders(ords)
    rel = {sp: _to_relative(so[sp]) for sp in (0, 1)}
    forced = {}
    for cost in ("lf_base", "mid"):
        for sp, nm in ((0, "train"), (1, "valid")):
            for dname, dv in (("always_long", 1), ("always_short", -1)):
                t2 = simulate(_apply_dir(rel[sp], [dv] * len(rel[sp])), COSTS[cost])
                forced[f"{cost}_{nm}_{dname}"] = stats(t2)
    out["forced_direction"] = forced
    log("forced " + json.dumps(forced))
    # break-direction hit rate: sign of (mid at flat - mid at decision) vs the trade direction
    hits = {}
    for sp, nm in ((0, "train"), (1, "valid")):
        dd = np.array([o["d"] for o in so[sp]])
        t_dec = np.array([o["t"] for o in so[sp]])
        t_fl = np.array([o["flat"] for o in so[sp]])
        i_dec = np.searchsorted(ts, t_dec - 60_000, side="right") - 1
        i_fl = np.searchsorted(ts, t_fl - 60_000, side="right") - 1
        mv = c[i_fl] - c[i_dec]
        hits[nm] = {"n": int(len(dd)), "hit_rate": round(float((np.sign(mv) == dd).mean()), 3),
                    "mean_move_to_flat_in_trade_dir": round(float((mv * dd).mean()), 3),
                    "mean_move_to_flat_unsigned_up": round(float(mv.mean()), 3)}
    out["direction_hit_to_flat"] = hits
    log("hit " + json.dumps(hits))

    # ---- direction-permutation null (long share preserved) and random-direction null, lf_base
    for sp, nm in ((0, "train"), (1, "valid")):
        act = stats(tr[tr.split == sp])
        dirs = np.array([o["d"] for o in so[sp]])
        rows = []
        for s in range(perm):
            rng = np.random.default_rng(5000 + s)
            t2 = simulate(_apply_dir(rel[sp], rng.permutation(dirs)), COSTS["lf_base"])
            rows.append(stats(t2))
        pdf = pd.DataFrame(rows)
        pa, npa = _pv(pdf["avg_pts"], act["avg_pts"])
        pp, _ = _pv(pdf["pf"], act["pf"])
        out[f"null_perm_{nm}"] = {"draws": npa, "pf_med": round(float(pdf["pf"].median()), 3),
                                  "pf_p95": round(float(pdf["pf"].quantile(0.95)), 3),
                                  "avg_med": round(float(pdf["avg_pts"].median()), 3), "p_avg": pa, "p_pf": pp,
                                  "actual_pf": act["pf"], "actual_avg": act["avg_pts"]}
        log(f"null_perm {nm} {out[f'null_perm_{nm}']}  ({time.time() - t0:.0f}s)")
        nd = random_direction(so[sp], seeds=range(rd), cost="lf_base")
        nd = nd[nd["n"] > 0]
        pa, npa = _pv(nd["avg_pts"], act["avg_pts"])
        pp, _ = _pv(nd["pf"], act["pf"])
        out[f"null_rd_{nm}"] = {"seeds": npa, "pf_med": round(float(nd["pf"].median()), 3),
                                "pf_p95": round(float(nd["pf"].quantile(0.95)), 3),
                                "avg_med": round(float(nd["avg_pts"].median()), 3), "p_avg": pa, "p_pf": pp,
                                "actual_pf": act["pf"], "actual_avg": act["avg_pts"]}
        log(f"null_rd {nm} {out[f'null_rd_{nm}']}  ({time.time() - t0:.0f}s)")

    # ---- random-window null (same rule on a random 30-min window per matched day, matched holding horizon)
    wins = str(p["window"]).split("+")
    if nrand > 0 and len(wins) == 1 and wins[0] in M.WINDOWS:
        b = M._base(m1)
        fu = p.get("flat_utc")
        T = M._windows_table(b, wins[0], int(p.get("brk_h", 120)), flat_utc=fu)
        hz = int(np.median((T["flat"].values - (b["ts"][T["e"].values] + 60_000)) / 60_000))
        out["rand_hz"] = hz
        rows = []
        for s in range(nrand):
            q = {**p, "window": "rand", "rseed": s, "match": wins[0], "rand_hz": hz}
            oq = M.orders(m1, **q)
            for cost in ("lf_base", "mid"):
                t2 = run_orders(oq, cost)
                for sp in (0, 1):
                    st = stats(t2[t2.split == sp]) if len(t2) else {"n": 0}
                    rows.append({"seed": s, "cost": cost, "split": sp, **st})
        rw = pd.DataFrame(rows)
        res = {}
        for cost in ("lf_base", "mid"):
            for sp, nm in ((0, "train"), (1, "valid")):
                x = rw[(rw.cost == cost) & (rw.split == sp) & (rw.n > 0)]
                actual = stats(trades[cost][trades[cost].split == sp])
                pa, nx = _pv(x["avg_pts"], actual["avg_pts"])
                res[f"{cost}_{nm}"] = {"seeds": nx, "n_med": float(x["n"].median()),
                                       "pf_med": round(float(x["pf"].median()), 3),
                                       "pf_p95": round(float(x["pf"].quantile(0.95)), 3),
                                       "avg_med": round(float(x["avg_pts"].median()), 3),
                                       "actual_pf": actual.get("pf"), "actual_avg": actual.get("avg_pts"), "p_avg": pa}
        out["null_randwin"] = res
        log("null_randwin " + json.dumps(res) + f" ({time.time() - t0:.0f}s)")

    # ---- flip-band variant and entry arms
    arms = {}
    for name, q in [("cap_skip", {**p, "cap": "skip"})] + [(e, {**p, "entry": e}) for e in
                                                             ("brk", "rt5", "rt30", "nt5", "nt30")]:
        for cost in ("lf_base", "mid"):
            ev = evaluate("orb_retest", q, cost=cost)
            arms[f"{name}_{cost}"] = {"train": ev.get("train"), "valid": ev.get("valid")}
    out["arms"] = arms
    for k, v in arms.items():
        log(f"arm {k} T {v['train'].get('n')} {v['train'].get('pf')} {v['train'].get('avg_pts')} | "
            f"V {v['valid'].get('n')} {v['valid'].get('pf')} {v['valid'].get('avg_pts')}")

    # ---- neighbours: single-parameter moves (VALID lf_base PF)
    steps = {"kap": [0.0, 0.1, 0.25], "tp": [0.0, 1.0, 1.5, 2.0], "flat_utc": [780, 960, 1140],
             "be": [0.0, 0.5, 1.0], "tmax": [0, 60, 120, 240], "brk_h": [60, 120, 240]}
    nbrs = []
    for k, vals in steps.items():
        cur = p.get(k)
        if cur not in vals:
            continue
        i = vals.index(cur)
        for j in (i - 1, i + 1):
            if 0 <= j < len(vals):
                nbrs.append({**p, k: vals[j]})
    stop_alt = [("far", 0.1), ("far", 0.25), ("mid", 0.1)]
    for s_, pad in stop_alt:
        if (s_, pad) != (p.get("stop"), p.get("pad")):
            nbrs.append({**p, "stop": s_, "pad": pad})
    nrows = []
    for q in nbrs:
        ev = evaluate("orb_retest", q, cost="lf_base")
        diff = {k: q[k] for k in q if q[k] != p.get(k)}
        nrows.append({"move": json.dumps(diff), "tr_n": ev["train"].get("n"), "tr_pf": ev["train"].get("pf"),
                      "va_n": ev["valid"].get("n"), "va_pf": ev["valid"].get("pf"),
                      "va_avg": ev["valid"].get("avg_pts")})
    ndf = pd.DataFrame(nrows)
    out["neighbours"] = nrows
    out["neighbours_valid_pf_median"] = float(ndf["va_pf"].median()) if len(ndf) else None
    log(ndf.to_string())
    out["elapsed_s"] = round(time.time() - t0)
    with open(ROOT / f"cand/results/orb_retest_{tag}_eval.json", "w") as f:
        json.dump(out, f, indent=1, default=str)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("params")
    ap.add_argument("--rd", type=int, default=200)
    ap.add_argument("--perm", type=int, default=200)
    ap.add_argument("--rand", type=int, default=100)
    ap.add_argument("--tag", default="final")
    a = ap.parse_args()
    main(json.loads(a.params), a.rd, a.perm, a.rand, a.tag, lambda s: print(s, flush=True))
