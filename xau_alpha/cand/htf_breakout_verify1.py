"""
cand/htf_breakout_verify1.py - verifier 1, STATISTICAL ROBUSTNESS lens, for the htf_breakout finalist
    {"tf":60,"N":55,"sl":1.5,"ex":"r2","hold":"multi","fresh":0}
(H1 Donchian-55 close breakout, both directions, stop 1.5 x ATR14(H1), target 2R, flat after 10 days.)

Holdout discipline (TRAIN + VALID only):
  * orders come from cand/htf_breakout.py, whose htf_base cuts the M1 frame at the TEST start before any feature;
  * every order passed to the simulator is asserted t < 2026-06-01 and flat <= 2026-05-31 23:59 UTC;
  * every simulation in this file runs on 10-s broker arrays PHYSICALLY TRUNCATED at the TEST start (sim.simulate
    `arr=`), so no TEST bar can be read. The only exception is the library call nulltest.random_direction (unchanged,
    as the pass bar requires), whose orders are VALID-only and exit before the cut; its results are cross-checked
    bit-for-bit against the truncated-array replay.
  * sweep.evaluate(final=True) is never used.

    python3 cand/htf_breakout_verify1.py core     # reproduce; cost/delay stress; monthly R; drop-best-month; bootstrap
    python3 cand/htf_breakout_verify1.py nulls    # library random_direction (200 seeds) + exact replay nulls (20k):
                                                  # random dir, order permutation, trade-set permutation, mirror,
                                                  # always-long / always-short on the same timestamps
    python3 cand/htf_breakout_verify1.py timing   # random-timing exposure null and forward-jitter null (same geometry)
    python3 cand/htf_breakout_verify1.py nbrs     # pre-registered neighbourhoods (G8, F12, local cube) at lf_base
    python3 cand/htf_breakout_verify1.py swap     # stress variants with the SOURCED LiteFinance swap charged
    python3 cand/htf_breakout_verify1.py lomo     # VALID trade-set permutation p with each VALID month left out
    python3 cand/htf_breakout_verify1.py mt       # multiple-testing haircut (reads result files, no simulation)
Outputs: cand/results/htf_breakout_verify1_<mode>.json (+ _monthly.csv). No trade lists are written.
Single process each; run at most two at a time.
"""
import dataclasses
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
from sim import COSTS, broker_arrays, simulate, stats  # noqa: E402

TEST_MS = _ms(TEST[0])
VALID_MS = _ms(VALID[0])
P = {"tf": 60, "N": 55, "sl": 1.5, "ex": "r2", "hold": "multi", "fresh": 0}
OUT = ROOT / "cand/results"
C_BASE, C_HARSH, C_MID = COSTS["lf_base"], COSTS["lf_harsh"], COSTS["mid"]
# lf_base + 0.30 $/oz extra adverse slippage on each side (sim applies slip to market entries and SL/time exits;
# the "all exits" variant also charges it on TP fills, post hoc)
C_SLIP = dataclasses.replace(C_BASE, slip_entry=C_BASE.slip_entry + 0.30, slip_exit=C_BASE.slip_exit + 0.30)
SPLITS = ((0, "train"), (1, "valid"))

_ARR: dict = {}


def jdump(obj, path):
    with open(path, "w") as f:
        json.dump(obj, f, indent=1, default=lambda x: x.item() if hasattr(x, "item") else str(x))


def arr_for(cost):
    """Broker 10-s arrays for `cost`, truncated at the TEST start (views, no copy)."""
    if cost not in _ARR:
        a = broker_arrays(cost)
        kc = int(np.searchsorted(a["ts"], TEST_MS, side="left"))
        _ARR[cost] = {k: v[:kc] for k, v in a.items()}
    return _ARR[cost]


def last_flat():
    from htf_base import LAST_FLAT_MS
    return LAST_FLAT_MS


def run(orders, cost=C_BASE, one=True):
    lf = last_flat()
    for o in orders:
        assert o["t"] < TEST_MS and o.get("flat", math.inf) <= lf, "holdout guard"
    tr = simulate(orders, cost, one_at_a_time=one, arr=arr_for(cost))
    if len(tr):
        assert tr["t_out"].max() <= TEST_MS, "exit after TEST start"
    return tr


def module_orders(params=P):
    import htf_breakout as hb
    return [o for o in hb.orders(load_m1(), **params) if o["t"] < TEST_MS]


def pf(x):
    x = np.asarray(x, float)
    gl = -x[x < 0].sum()
    return float(x[x > 0].sum() / gl) if gl > 0 else float("inf")


def st(tr):
    """sim.stats plus R t-stat, R-space PF and long count."""
    if tr is None or len(tr) == 0:
        return {"n": 0}
    s = stats(tr)
    R = tr["R"].values
    s["t"] = round(float(R.mean() / (R.std(ddof=1) + 1e-12) * math.sqrt(len(R))), 2) if len(R) > 1 else None
    s["pf_R"] = round(pf(R), 3)
    s["n_long"] = int((tr["d"] > 0).sum())
    return s


def by_split(tr):
    return {nm: (st(tr[tr["split"] == sp]) if len(tr) else {"n": 0}) for sp, nm in SPLITS}


# --------------------------------------------------------------------------------------------------------------
# per-order outcomes for both directions + exact replay of sim.simulate's one-position-at-a-time rule

class Outcomes:
    """Each order simulated on its own, long and short (column 0 = long, 1 = short). For this candidate every order is
    a market order with sl_dist / tp_dist (no absolute levels), so a direction flip is exact (as in nulltest)."""

    def __init__(self, orders, cost=C_BASE):
        n = len(orders)
        self.orders = orders
        self.t = np.array([o["t"] for o in orders], np.int64)
        assert n == 0 or np.all(np.diff(self.t) > 0), "orders must be strictly time-sorted"
        self.filled = np.zeros((n, 2), bool)
        self.t_in = np.full((n, 2), -1, np.int64)
        self.t_out = np.full((n, 2), -1, np.int64)
        self.pnl = np.full((n, 2), np.nan)
        self.R = np.full((n, 2), np.nan)
        self.risk = np.full((n, 2), np.nan)
        self.split = np.full((n, 2), -1, np.int8)
        self.tp = np.zeros((n, 2), bool)
        for c, d in ((0, 1), (1, -1)):
            od = [{**o, "d": d, "tag": str(j)} for j, o in enumerate(orders)]
            tr = run(od, cost, one=False)
            j = tr["tag"].astype(int).values
            self.filled[j, c] = True
            self.t_in[j, c] = tr["t_in"].values
            self.t_out[j, c] = tr["t_out"].values
            self.pnl[j, c] = tr["pnl"].values
            self.R[j, c] = tr["R"].values
            self.risk[j, c] = tr["risk"].values
            self.split[j, c] = tr["split"].values
            self.tp[j, c] = (tr["reason"] == "tp").values
        self._tl = self.t.tolist()
        self._fl = self.filled.tolist()
        self._to = self.t_out.tolist()

    def replay(self, dirs, sel=None):
        """dirs: +1/-1 per order (sequence aligned with self.orders). sel: optional sorted subset of order indices.
        Returns (js, cs) of the trades taken, exactly as sim.simulate(one_at_a_time=True) would take them."""
        tl, fl, to = self._tl, self._fl, self._to
        busy = -1
        js, cs = [], []
        if sel is not None and hasattr(sel, "tolist"):
            sel = sel.tolist()
        idx = range(len(tl)) if sel is None else sel
        dl = dirs.tolist() if hasattr(dirs, "tolist") else list(dirs)
        for q, j in enumerate(idx):
            if tl[j] < busy:
                continue
            c = 0 if dl[q if sel is not None else j] > 0 else 1
            if not fl[j][c]:
                continue
            js.append(j)
            cs.append(c)
            busy = to[j][c]
        return np.asarray(js, np.int64), np.asarray(cs, np.int64)

    def fstats(self, js, cs, split=None):
        p, r, s = self.pnl[js, cs], self.R[js, cs], self.split[js, cs]
        if split is not None:
            m = s == split
            p, r = p[m], r[m]
        n = len(p)
        if n == 0:
            return {"n": 0, "avg_pts": 0.0, "pf": 0.0, "avg_R": 0.0, "pf_R": 0.0, "sum_R": 0.0}
        return {"n": n, "avg_pts": float(p.mean()), "pf": pf(p), "avg_R": float(r.mean()), "pf_R": pf(r),
                "sum_R": float(r.sum())}


STATS_KEYS = ("avg_pts", "pf", "avg_R", "pf_R")


def pvals(null_rows, real):
    out = {}
    for k in STATS_KEYS:
        v = np.array([r[k] for r in null_rows], float)
        out[k] = {"real": round(real[k], 4), "null_mean": round(float(np.mean(v)), 4),
                  "null_sd": round(float(np.std(v)), 4),
                  "null_q05_50_95": [round(float(x), 4) for x in np.quantile(v, [0.05, 0.5, 0.95])],
                  "p": round(float((np.sum(v >= real[k] - 1e-12) + 1) / (len(v) + 1)), 4)}
    out["null_n_median"] = float(np.median([r["n"] for r in null_rows]))
    out["draws"] = len(null_rows)
    return out


# --------------------------------------------------------------------------------------------------------------

def core():
    t0 = time.time()
    from sweep import evaluate
    od = module_orders()
    res = {"params": P, "n_orders": len(od), "n_orders_valid": int(sum(o["t"] >= VALID_MS for o in od))}
    ev = evaluate("htf_breakout", P, cost="lf_base")          # standard harness path (final=False)
    tr = run(od, C_BASE)
    res["repro"] = {"sweep_evaluate": {"train": ev["train"], "valid": ev["valid"]}, "truncated_arrays": by_split(tr)}
    t0tr = tr[tr.split == 0]
    h = len(t0tr) // 2
    res["train_halves_pf"] = [stats(t0tr.iloc[:h])["pf"], stats(t0tr.iloc[h:])["pf"]]
    res["long_short_lf_base"] = {f"{nm}_{sd}": st(tr[(tr.split == sp) & (tr.d == dv)])
                                 for sp, nm in SPLITS for sd, dv in (("long", 1), ("short", -1))}
    # R distribution moments (for the deflated Sharpe in mt)
    from scipy import stats as sst
    for sp, nm in SPLITS:
        R = tr.loc[tr.split == sp, "R"].values
        res[f"{nm}_R_moments"] = {"n": len(R), "mean": float(R.mean()), "sd": float(R.std(ddof=1)),
                                  "skew": float(sst.skew(R)), "kurt": float(sst.kurtosis(R, fisher=False))}
    print("repro", json.dumps(res["repro"]["truncated_arrays"]), flush=True)

    # ---------------- cost / latency stress (one-position-at-a-time, full sequence re-simulated) ----------------
    def delayed(orders, ms):
        return [{**o, "t": o["t"] + ms} for o in orders]

    def tp_charge(t, extra):
        if extra <= 0 or not len(t):
            return t
        t = t.copy()
        m = t["reason"] == "tp"
        t.loc[m, "pnl"] = t.loc[m, "pnl"] - extra
        t["R"] = t["pnl"] / t["risk"]
        return t

    od5 = delayed(od, 5_000)
    variants = [
        ("lf_base", od, C_BASE, 0.0), ("lf_harsh", od, C_HARSH, 0.0), ("mid (gross)", od, C_MID, 0.0),
        ("lf_base +0.30/side (sim: entry + SL/time exits)", od, C_SLIP, 0.0),
        ("lf_base +0.30/side (every exit incl. TP)", od, C_SLIP, 0.30),
        ("lf_base, entry +5 s", od5, C_BASE, 0.0),
        ("lf_harsh, entry +5 s", od5, C_HARSH, 0.0),
        ("lf_base +0.30/side every exit, entry +5 s", od5, C_SLIP, 0.30),
    ]
    stress = {}
    for name, orders, cost, extra in variants:
        t = tp_charge(run(orders, cost), extra)
        row = by_split(t)
        tt = t[t.split == 0]
        hh = len(tt) // 2
        row["train_halves_pf"] = [stats(tt.iloc[:hh])["pf"], stats(tt.iloc[hh:])["pf"]]
        row["valid_long_pf"] = st(t[(t.split == 1) & (t.d > 0)]).get("pf")
        row["valid_short_pf"] = st(t[(t.split == 1) & (t.d < 0)]).get("pf")
        if orders is od5:
            row["fill_delay_s_min_max"] = [float((t.t_in - t.t_sig + 5_000).min() / 1000),
                                           float((t.t_in - t.t_sig + 5_000).max() / 1000)]
        stress[name] = row
        print("stress", name, row["train"].get("pf"), row["valid"].get("pf"), row["valid"].get("n"), flush=True)
    res["stress"] = stress

    # ---------------- monthly R table, drop-best-month, leave-one-month-out ----------------
    mon = pd.to_datetime(tr["t_in"], unit="ms", utc=True).dt.strftime("%Y-%m")
    rows = []
    for m, g in tr.groupby(mon):
        rows.append({"month": m, "split": int(g.split.iloc[0]), "n": len(g), "long": int((g.d > 0).sum()),
                     "short": int((g.d < 0).sum()), "sum_R": round(float(g.R.sum()), 2),
                     "sum_pts": round(float(g.pnl.sum()), 1), "avg_pts": round(float(g.pnl.mean()), 2),
                     "pf": round(pf(g.pnl), 2), "pf_R": round(pf(g.R), 2),
                     "sum_R_long": round(float(g.R[g.d > 0].sum()), 2),
                     "sum_R_short": round(float(g.R[g.d < 0].sum()), 2)})
    mt = pd.DataFrame(rows)
    mt.to_csv(OUT / "htf_breakout_verify1_monthly.csv", index=False)
    res["monthly"] = rows
    dbm = {}
    for sp, nm in SPLITS:
        t = tr[tr.split == sp]
        mm = mon[tr.split == sp]
        g_pts = t.groupby(mm)["pnl"].sum().sort_values(ascending=False)
        g_R = t.groupby(mm)["R"].sum().sort_values(ascending=False)
        lomo = {m: round(pf(t.pnl[mm != m]), 3) for m in g_pts.index}
        dbm[nm] = {
            "best_month_pts": g_pts.index[0], "pf_drop_best_month": round(pf(t.pnl[mm != g_pts.index[0]]), 3),
            "pf_drop_best_2_months": round(pf(t.pnl[~mm.isin(g_pts.index[:2])]), 3),
            "best_month_R": g_R.index[0], "pfR_drop_best_month_R": round(pf(t.R[mm != g_R.index[0]]), 3),
            "avgR_drop_best_month_R": round(float(t.R[mm != g_R.index[0]].mean()), 3),
            "months_pos_R": int((g_R > 0).sum()), "months": int(len(g_R)),
            "leave_one_month_out_pf": lomo, "lomo_pf_min_max": [min(lomo.values()), max(lomo.values())],
        }
    res["drop_best_month"] = dbm

    # ---------------- trade-level influence and bootstrap ----------------
    boot = {}
    rng = np.random.default_rng(7)
    for sp, nm in SPLITS:
        t = tr[tr.split == sp]
        p, R = t.pnl.values, t.R.values
        order = np.argsort(-p)
        infl = {f"pf_without_top{k}": round(pf(np.delete(p, order[:k])), 3) for k in (1, 2, 3, 5)}
        infl["top5_pnl"] = [round(float(x), 2) for x in p[order[:5]]]
        infl["share_of_net_from_top1"] = round(float(p[order[0]] / p.sum()), 3) if p.sum() != 0 else None
        B = 20_000
        ix = rng.integers(0, len(p), size=(B, len(p)))
        P_ = p[ix]
        R_ = R[ix]
        gw = np.where(P_ > 0, P_, 0).sum(1)
        gl = -np.where(P_ < 0, P_, 0).sum(1)
        pfb = gw / np.maximum(gl, 1e-12)
        ar = R_.mean(1)
        boot[nm] = {"influence": infl,
                    "pf_q05_50_95": [round(float(x), 3) for x in np.quantile(pfb, [0.05, 0.5, 0.95])],
                    "avgR_q05_50_95": [round(float(x), 3) for x in np.quantile(ar, [0.05, 0.5, 0.95])],
                    "P(pf>1)": round(float((pfb > 1).mean()), 3), "P(pf>=1.15)": round(float((pfb >= 1.15).mean()), 3),
                    "P(avgR>0)": round(float((ar > 0).mean()), 3)}
    # power: VALID trades needed for t = 2 at the VALID mean/sd of R; VALID t expected if the TRAIN mean R held
    mv, sv, nv = res["valid_R_moments"]["mean"], res["valid_R_moments"]["sd"], res["valid_R_moments"]["n"]
    mtn = res["train_R_moments"]["mean"]
    boot["power"] = {"valid_trades_needed_for_t2": int(math.ceil((2 * sv / mv) ** 2)) if mv > 0 else None,
                     "expected_valid_t_if_train_meanR_true": round(mtn / sv * math.sqrt(nv), 2),
                     "valid_t": round(mv / sv * math.sqrt(nv), 2),
                     "valid_meanR_se": round(sv / math.sqrt(nv), 3)}
    res["bootstrap"] = boot
    res["secs"] = round(time.time() - t0)
    jdump(res, OUT / "htf_breakout_verify1_core.json")
    print(json.dumps({k: res[k] for k in ("drop_best_month", "bootstrap")}, indent=1, default=str), flush=True)


# --------------------------------------------------------------------------------------------------------------

def nulls(draws=20_000, perm_draws=100_000):
    t0 = time.time()
    import nulltest
    od = module_orders()
    res = {"params": P, "draws": draws}
    O = Outcomes(od, C_BASE)
    d_real = [o["d"] for o in od]
    # (a) replay parity with the one-at-a-time simulator
    js, cs = O.replay(d_real)
    tr = run(od, C_BASE)
    parity = (len(js) == len(tr) and np.array_equal(O.t_in[js, cs], tr["t_in"].values)
              and np.array_equal(O.pnl[js, cs], tr["pnl"].values))
    res["replay_parity_real"] = bool(parity)
    assert parity, "replay does not reproduce sim.simulate"
    for dv, nm in ((1, "long"), (-1, "short")):
        jj, cc = O.replay([dv] * len(od))
        t2 = run([{**o, "d": dv} for o in od], C_BASE)
        res[f"replay_parity_all_{nm}"] = bool(len(jj) == len(t2) and np.array_equal(O.pnl[jj, cc], t2["pnl"].values))
        assert res[f"replay_parity_all_{nm}"]
    idx = {"train": np.flatnonzero(O.t < VALID_MS), "valid": np.flatnonzero(O.t >= VALID_MS)}
    real = {}
    for sp, nm in SPLITS:
        sel = idx[nm]
        jj, cc = O.replay([d_real[j] for j in sel], sel=sel)
        real[nm] = O.fstats(jj, cc, sp)
        real[nm + "_trades"] = (jj, cc)
        # the split-only sequence equals the full-sequence trades of that split (no trade spans the boundary)
        full = O.fstats(js, cs, sp)
        res[f"{nm}_splitonly_equals_full"] = bool(full["n"] == real[nm]["n"] and
                                                 abs(full["avg_pts"] - real[nm]["avg_pts"]) < 1e-9)
    res["real"] = {nm: real[nm] for _, nm in SPLITS}
    print("real", res["real"], flush=True)

    # (b) LIBRARY nulltest.random_direction, 200 seeds, lf_base, VALID-only orders, scored on VALID
    od_v = [od[j] for j in idx["valid"]]
    t1 = time.time()
    rd = nulltest.random_direction(od_v, seeds=range(200), cost="base", split=1)
    lib_rows = rd.to_dict("records")
    res["library_random_direction_200"] = {
        "secs": round(time.time() - t1),
        "p_avg_pts": round(float((np.sum(rd["avg_pts"].values >= round(real["valid"]["avg_pts"], 3)) + 1) / 201), 4),
        "p_pf": round(float((np.sum(rd["pf"].values >= round(real["valid"]["pf"], 3)) + 1) / 201), 4),
        "p_avg_R": round(float((np.sum(rd["avg_R"].values >= round(real["valid"]["avg_R"], 3)) + 1) / 201), 4),
        "null_mean_avg_pts": round(float(rd["avg_pts"].mean()), 3), "null_sd_avg_pts": round(float(rd["avg_pts"].std()), 3),
        "null_mean_pf": round(float(rd["pf"].mean()), 3)}
    # parity: replay with the library's own RNG draws must give the same per-seed stats
    mism = 0
    for s, row in zip(range(200), lib_rows):
        dirs = np.random.default_rng(1000 + s).choice([-1, 1], size=len(od_v))
        jj, cc = O.replay(dirs, sel=idx["valid"])
        f = O.fstats(jj, cc, 1)
        if not (f["n"] == row["n"] and round(f["pf"], 3) == row["pf"] and round(f["avg_pts"], 3) == row["avg_pts"]):
            mism += 1
    res["library_vs_replay_mismatches_of_200"] = mism
    print("library 200:", res["library_random_direction_200"], "mismatches", mism, flush=True)

    # (c) replay nulls, 20k draws each
    def run_null(kind, nm, sp, sel, score_split):
        d0 = np.array([d_real[j] for j in sel])
        rows = []
        for s in range(draws):
            if kind == "random":
                dirs = np.random.default_rng(1000 + s).choice([-1, 1], size=len(sel))
            else:
                dirs = np.random.default_rng(5000 + s).permutation(d0)
            jj, cc = O.replay(dirs, sel=sel)
            rows.append(O.fstats(jj, cc, score_split))
        return pvals(rows, real[nm])

    for sp, nm in SPLITS:
        res[f"random_direction_{nm}"] = run_null("random", nm, sp, idx[nm], sp)
        res[f"order_permutation_{nm}"] = run_null("perm", nm, sp, idx[nm], sp)
        print(nm, "random", {k: res[f"random_direction_{nm}"][k]["p"] for k in STATS_KEYS},
              "perm", {k: res[f"order_permutation_{nm}"][k]["p"] for k in STATS_KEYS}, flush=True)
    # verifier-0 design: all 535 orders random, VALID trades scored
    allsel = np.arange(len(od))
    res["random_direction_all_orders_scored_valid"] = run_null("random", "valid", 1, allsel, 1)

    # (d) trade-set permutation: the realised trades' timestamps and exit geometry fixed, long/short count fixed,
    #     directions shuffled (each trade evaluated on its own for the direction it is given)
    for sp, nm in SPLITS:
        jj, cc = real[nm + "_trades"]
        pl, ps = O.pnl[jj, 0], O.pnl[jj, 1]
        rl, rs = O.R[jj, 0], O.R[jj, 1]
        assert np.all(O.filled[jj, 0]) and np.all(O.filled[jj, 1])
        dvec = np.where(cc == 0, 1, -1)
        rng = np.random.default_rng(77 + sp)
        vals = {k: [] for k in STATS_KEYS}
        for ch in range(0, perm_draws, 10_000):
            k = min(10_000, perm_draws - ch)
            D = rng.permuted(np.tile(dvec, (k, 1)), axis=1) > 0
            Pm = np.where(D, pl, ps)
            Rm = np.where(D, rl, rs)
            for key, M in (("avg_pts", Pm), ("avg_R", Rm)):
                vals[key].append(M.mean(1))
            for key, M in (("pf", Pm), ("pf_R", Rm)):
                vals[key].append(np.where(M > 0, M, 0).sum(1) / np.maximum(-np.where(M < 0, M, 0).sum(1), 1e-12))
        rows_v = {k: np.concatenate(v) for k, v in vals.items()}
        rr = real[nm]
        res[f"tradeset_permutation_{nm}"] = {
            k: {"real": round(rr[k], 4), "null_mean": round(float(rows_v[k].mean()), 4),
                "null_q05_50_95": [round(float(x), 4) for x in np.quantile(rows_v[k], [0.05, 0.5, 0.95])],
                "p": round(float((np.sum(rows_v[k] >= rr[k] - 1e-12) + 1) / (perm_draws + 1)), 5)}
            for k in STATS_KEYS}
        res[f"tradeset_permutation_{nm}"]["n_trades"] = int(len(jj))
        res[f"tradeset_permutation_{nm}"]["n_long"] = int((dvec > 0).sum())
        # always-long / always-short on the same timestamps; long-signal and short-signal subsets
        def s_(p, r):
            return {"n": int(len(p)), "avg_pts": round(float(p.mean()), 3), "pf": round(pf(p), 3),
                    "avg_R": round(float(r.mean()), 3), "pf_R": round(pf(r), 3), "sum_R": round(float(r.sum()), 1)}
        L, S = dvec > 0, dvec < 0
        res[f"same_timestamps_{nm}"] = {
            "strategy": s_(np.where(L, pl, ps), np.where(L, rl, rs)),
            "always_long": s_(pl, rl), "always_short": s_(ps, rs),
            "long_signals_as_long(strategy)": s_(pl[L], rl[L]), "long_signals_as_short": s_(ps[L], rs[L]),
            "short_signals_as_short(strategy)": s_(ps[S], rs[S]), "short_signals_as_long": s_(pl[S], rl[S]),
        }
        # always-long / always-short on the same ORDER set, one-position-at-a-time
        sel = idx[nm]
        for dv, lab in ((1, "always_long_orders"), (-1, "always_short_orders")):
            j2, c2 = O.replay([dv] * len(sel), sel=sel)
            f = O.fstats(j2, c2, sp)
            res[f"same_timestamps_{nm}"][lab] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in f.items()}
        print(nm, "tradeset perm p", {k: res[f"tradeset_permutation_{nm}"][k]["p"] for k in STATS_KEYS}, flush=True)

    # (e) library mirror
    mtr = run(nulltest.mirror(od), C_BASE)
    res["mirror_library"] = by_split(mtr)
    jj, cc = O.replay([-d for d in d_real])
    res["mirror_replay_parity"] = bool(len(jj) == len(mtr) and np.array_equal(O.pnl[jj, cc], mtr["pnl"].values))
    res["secs"] = round(time.time() - t0)
    for nm in ("train", "valid"):
        res["real"][nm] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in res["real"][nm].items()}
    jdump(res, OUT / "htf_breakout_verify1_nulls.json")
    print("mirror", res["mirror_library"], "secs", res["secs"], flush=True)


# --------------------------------------------------------------------------------------------------------------

def candidate_orders(params=P, maxd=10):
    """Every gated H1 close (same warm-up, gates, stop and target geometry as the module), direction +1 (placeholder)."""
    from htf_base import DAY_MS, LAST_FLAT_MS, base, htf
    b = base(load_m1())
    H = htf(b, params["tf"])
    ki, a = H["ki"], H["atr"]
    ts, gate, n = b["ts"], b["gate"], b["n"]
    out = []
    last_t = -1
    for k in range(H["nk"]):
        i = ki[k]
        if i >= n or k < params["N"] + 14 or not np.isfinite(a[k]) or not gate[i]:
            continue
        t = int(ts[i]) + 60_000
        if t == last_t:          # two buckets completing on the same M1 bar (data gap): keep the first, as sim would
            continue
        last_t = t
        sd = params["sl"] * a[k]
        out.append({"t": t, "d": 1, "kind": "mkt", "sl_dist": float(sd), "tp_dist": float(float(params["ex"][1:]) * sd),
                    "flat": min(LAST_FLAT_MS, t + int(maxd * DAY_MS)), "tag": "cand"})
    return [o for o in out if o["t"] < TEST_MS]


def timing(draws=5_000):
    """Does the Donchian breakout pick better ENTRY TIMES than chance, given the same exit geometry?
    (i) exposure null: the same number of orders per split at uniformly random gated H1 closes, with the strategy's
        own direction labels shuffled among them (keeps the long share), one position at a time;
    (ii) forward-jitter null: every strategy order moved to a random gated H1 close in (t, t + 24 h] of the same split,
         same direction (causal: the direction is known at the original t)."""
    t0 = time.time()
    od = module_orders()
    cand = candidate_orders()
    ct = np.array([o["t"] for o in cand], np.int64)
    # the strategy's orders must be a subset of the candidates with identical geometry
    pos = np.searchsorted(ct, [o["t"] for o in od])
    ok = all(pos[q] < len(ct) and ct[pos[q]] == o["t"] and cand[pos[q]]["sl_dist"] == o["sl_dist"]
             and cand[pos[q]]["tp_dist"] == o["tp_dist"] and cand[pos[q]]["flat"] == o["flat"] for q, o in enumerate(od))
    res = {"params": P, "n_candidates": len(cand), "strategy_orders_subset_of_candidates": bool(ok)}
    assert ok
    O = Outcomes(cand, C_BASE)
    d_real = np.array([o["d"] for o in od])
    ot = np.array([o["t"] for o in od], np.int64)
    # real, via the candidate replay (must equal the module's trades)
    dirs_full = np.zeros(len(cand), int)
    dirs_full[pos] = d_real
    real = {}
    for sp, nm in SPLITS:
        m = (ot >= VALID_MS) if sp == 1 else (ot < VALID_MS)
        sel = sorted(pos[m].tolist())
        jj, cc = O.replay([dirs_full[j] for j in sel], sel=sel)
        real[nm] = O.fstats(jj, cc, sp)
    tr = run(od, C_BASE)
    for sp, nm in SPLITS:
        t = tr[tr.split == sp]
        assert real[nm]["n"] == len(t) and abs(real[nm]["avg_pts"] - t.pnl.mean()) < 1e-9, "candidate replay parity"
    res["real"] = real
    csplit = np.where(ct >= VALID_MS, 1, 0)
    rng = np.random.default_rng(2026)
    for sp, nm in SPLITS:
        m = (ot >= VALID_MS) if sp == 1 else (ot < VALID_MS)
        M = int(m.sum())
        pool = np.flatnonzero(csplit == sp)
        dl = d_real[m]
        rows_e, rows_j = [], []
        # forward-jitter windows: candidates with t in (t_j, t_j + 24h], same split
        tj = ot[m]
        lo = np.searchsorted(ct, tj, side="right")
        hi = np.searchsorted(ct, tj + 86_400_000, side="right")
        hi = np.minimum(hi, pool.max() + 1)
        has = hi > lo
        for s in range(draws):
            sel = np.sort(rng.choice(pool, size=M, replace=False))
            dirs = rng.permutation(dl)
            jj, cc = O.replay(dirs.tolist(), sel=sel.tolist())
            rows_e.append(O.fstats(jj, cc, sp))
            pick = lo[has] + (rng.random(has.sum()) * (hi[has] - lo[has])).astype(np.int64)
            orderq = np.argsort(pick, kind="stable")
            selj = pick[orderq]
            dj = dl[has][orderq]
            # drop duplicate times (the second order at the same H1 close would be skipped by the simulator anyway
            # if the first filled; keep the first to mirror sim's stable sort)
            keep = np.r_[True, np.diff(selj) > 0]
            jj, cc = O.replay(dj[keep].tolist(), sel=selj[keep].tolist())
            rows_j.append(O.fstats(jj, cc, sp))
        res[f"exposure_null_{nm}"] = pvals(rows_e, real[nm])
        res[f"forward_jitter24h_null_{nm}"] = pvals(rows_j, real[nm])
        res[f"forward_jitter24h_{nm}_orders_with_window"] = int(has.sum())
        print(nm, "exposure p", {k: res[f"exposure_null_{nm}"][k]["p"] for k in STATS_KEYS},
              "jitter p", {k: res[f"forward_jitter24h_null_{nm}"][k]["p"] for k in STATS_KEYS}, flush=True)
    # unconditional per-H1-close outcome by direction (the sample's drift at this exit geometry)
    for sp, nm in SPLITS:
        m = (csplit == sp) & O.filled[:, 0] & O.filled[:, 1]
        res[f"every_gated_H1_close_{nm}"] = {
            "n": int(m.sum()),
            "long_avg_pts": round(float(O.pnl[m, 0].mean()), 3), "long_pf": round(pf(O.pnl[m, 0]), 3),
            "long_avg_R": round(float(O.R[m, 0].mean()), 3),
            "short_avg_pts": round(float(O.pnl[m, 1].mean()), 3), "short_pf": round(pf(O.pnl[m, 1]), 3),
            "short_avg_R": round(float(O.R[m, 1].mean()), 3)}
    res["secs"] = round(time.time() - t0)
    for nm in ("train", "valid"):
        res["real"][nm] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in res["real"][nm].items()}
    jdump(res, OUT / "htf_breakout_verify1_timing.json")
    print(json.dumps({k: v for k, v in res.items() if k.startswith("every")}, indent=1), "secs", res["secs"], flush=True)


# --------------------------------------------------------------------------------------------------------------
# PRE-REGISTERED neighbourhoods (fixed before any of them was simulated in this file)
G8 = [{"N": 45}, {"N": 34}, {"sl": 1.0}, {"sl": 3.0}, {"ex": "ch2"}, {"ex": "ch2.5"}, {"hold": "eod"}, {"fresh": 1}]
F12 = [{"N": 45}, {"N": 50}, {"N": 60}, {"N": 65}, {"sl": 1.25}, {"sl": 1.75}, {"ex": "r1.5"}, {"ex": "r1.75"},
       {"ex": "r2.25"}, {"ex": "r2.5"}, {"fresh": 1}, {"maxd": 5}]
CUBE = [{"N": n, "sl": s, "ex": e} for n in (45, 50, 55, 60, 65) for s in (1.25, 1.5, 1.75) for e in ("r1.5", "r2", "r2.5")]


def nbrs():
    t0 = time.time()
    res = {"params": P, "G8": [], "F12": [], "CUBE": []}
    seen = {}

    def ev(ch):
        q = {**P, **ch}
        key = json.dumps(q, sort_keys=True)
        if key not in seen:
            tr = run(module_orders(q), C_BASE)
            bs = by_split(tr)
            v = tr[tr.split == 1]
            seen[key] = {"change": ch, "train": bs["train"], "valid": bs["valid"],
                         "valid_long_pf": st(v[v.d > 0]).get("pf"), "valid_short_pf": st(v[v.d < 0]).get("pf")}
            print(ch, bs["train"].get("pf"), bs["valid"].get("pf"), bs["valid"].get("n"), flush=True)
        return seen[key]

    for nm, lst in (("G8", G8), ("F12", F12), ("CUBE", CUBE)):
        for ch in lst:
            res[nm].append(ev(ch))
    summ = {}
    for nm in ("G8", "F12", "CUBE"):
        rows = [r for r in res[nm] if {**P, **r["change"]} != P]
        vpf = np.array([r["valid"].get("pf", 0) for r in rows])
        vr = np.array([r["valid"].get("avg_R", 0) for r in rows])
        tpf = np.array([r["train"].get("pf", 0) for r in rows])
        summ[nm] = {"k": len(rows), "valid_pf_median": round(float(np.median(vpf)), 3),
                    "valid_pf_min_max": [round(float(vpf.min()), 3), round(float(vpf.max()), 3)],
                    "share_valid_pf_ge_1.05": round(float((vpf >= 1.05).mean()), 3),
                    "share_valid_pf_ge_1.15": round(float((vpf >= 1.15).mean()), 3),
                    "share_valid_pf_gt_1": round(float((vpf > 1).mean()), 3),
                    "valid_avgR_median": round(float(np.median(vr)), 3),
                    "train_pf_median": round(float(np.median(tpf)), 3),
                    "finalist_valid_pf_rank_among_k_plus_1": int(1 + (vpf > 1.073).sum())}
    res["summary"] = summ
    res["unique_configs_simulated"] = len(seen)
    res["secs"] = round(time.time() - t0)
    jdump(res, OUT / "htf_breakout_verify1_nbrs.json")
    print(json.dumps(summ, indent=1), "unique", len(seen), "secs", res["secs"], flush=True)


# --------------------------------------------------------------------------------------------------------------

def swap():
    """lf_base / lf_harsh / combined stress, each with the SOURCED LiteFinance swap charged (cand/x2_swap.py rules:
    long -0.891 $/oz, short +0.0345 $/oz per server-midnight rollover, Wednesday x3). Also the share of trades that
    would breach the swap-free terms (held over the triple-swap rollover, or held > 5 days)."""
    from x2_swap import SWAP_LONG, SWAP_SHORT, swap_nights
    od = module_orders()
    od5 = [{**o, "t": o["t"] + 5_000} for o in od]

    def tp_charge(t, extra):
        if extra <= 0 or not len(t):
            return t
        t = t.copy()
        m = t["reason"] == "tp"
        t.loc[m, "pnl"] = t.loc[m, "pnl"] - extra
        t["R"] = t["pnl"] / t["risk"]
        return t

    variants = [("lf_base", od, C_BASE, 0.0), ("lf_harsh", od, C_HARSH, 0.0), ("mid (gross)", od, C_MID, 0.0),
                ("lf_base +0.30/side every exit", od, C_SLIP, 0.0 + 0.30),
                ("lf_base, entry +5 s", od5, C_BASE, 0.0),
                ("lf_base +0.30/side every exit, entry +5 s", od5, C_SLIP, 0.30),
                ("lf_harsh, entry +5 s", od5, C_HARSH, 0.0)]
    res = {"params": P, "swap_long_per_night": SWAP_LONG, "swap_short_per_night": SWAP_SHORT}
    for name, orders, cost, extra in variants:
        t = tp_charge(run(orders, cost), extra)
        nights = swap_nights(t["t_in"].values, t["t_out"].values)
        sw = np.where(t["d"].values > 0, SWAP_LONG, SWAP_SHORT) * nights
        t2 = t.copy()
        t2["pnl"] = t["pnl"] + sw
        t2["R"] = t2["pnl"] / t2["risk"]
        row = by_split(t2)
        for sp, nm in SPLITS:
            m = t2.split == sp
            row[nm + "_avg_swap_nights"] = round(float(nights[m.values].mean()), 3)
            row[nm + "_avg_swap_pts"] = round(float(sw[m.values].mean()), 3)
            row[nm + "_long_pf"] = st(t2[m & (t2.d > 0)]).get("pf")
            row[nm + "_short_pf"] = st(t2[m & (t2.d < 0)]).get("pf")
            if name == "lf_base":
                a = pd.to_datetime(t2.loc[m, "t_in"], unit="ms", utc=True).dt.tz_convert("Europe/Athens")
                b = pd.to_datetime(t2.loc[m, "t_out"], unit="ms", utc=True).dt.tz_convert("Europe/Athens")
                wed = [any(d.dayofweek == 3 for d in pd.date_range(x.normalize() + pd.Timedelta(days=1), y.normalize(),
                                                                     freq="D")) for x, y in zip(a, b)]
                row[nm + "_share_held_over_triple_swap"] = round(float(np.mean(wed)), 3)
                row[nm + "_share_held_gt_5d"] = round(float(((t2.loc[m, "t_out"] - t2.loc[m, "t_in"])
                                                             > 5 * 86_400_000).mean()), 3)
        tt = t2[t2.split == 0]
        hh = len(tt) // 2
        row["train_halves_pf"] = [stats(tt.iloc[:hh])["pf"], stats(tt.iloc[hh:])["pf"]]
        res[name + " + swap"] = row
        print(name, "+swap", row["train"].get("pf"), row["valid"].get("pf"), row["valid"].get("avg_pts"),
              row["valid"].get("n"), flush=True)
    jdump(res, OUT / "htf_breakout_verify1_swap.json")


# --------------------------------------------------------------------------------------------------------------

def lomo(draws=50_000):
    """VALID trade-set permutation p ($ and R) with each VALID month left out in turn."""
    od = module_orders()
    O = Outcomes(od, C_BASE)
    sel = np.flatnonzero(O.t >= VALID_MS)
    jj, cc = O.replay([od[j]["d"] for j in sel], sel=sel)
    m = pd.to_datetime(O.t_in[jj, cc], unit="ms", utc=True).strftime("%Y-%m").values
    dvec = np.where(cc == 0, 1, -1)
    pl, ps, rl, rs = O.pnl[jj, 0], O.pnl[jj, 1], O.R[jj, 0], O.R[jj, 1]
    rng = np.random.default_rng(11)
    out = {}
    for drop in [None] + sorted(set(m)):
        k = np.ones(len(jj), bool) if drop is None else (m != drop)
        d, a, b, c, e = dvec[k], pl[k], ps[k], rl[k], rs[k]
        real_p, real_r = np.where(d > 0, a, b).mean(), np.where(d > 0, c, e).mean()
        D = rng.permuted(np.tile(d, (draws, 1)), axis=1) > 0
        np_, nr = np.where(D, a, b).mean(1), np.where(D, c, e).mean(1)
        out[str(drop)] = {"n": int(k.sum()), "avg_pts": round(float(real_p), 2), "avg_R": round(float(real_r), 3),
                          "p_pts": round(float((np.sum(np_ >= real_p) + 1) / (draws + 1)), 4),
                          "p_R": round(float((np.sum(nr >= real_r) + 1) / (draws + 1)), 4)}
        print(drop, out[str(drop)], flush=True)
    jdump(out, OUT / "htf_breakout_verify1_lomo_perm.json")


# --------------------------------------------------------------------------------------------------------------

def mt():
    from scipy import stats as sst
    res = {}
    core_ = json.load(open(OUT / "htf_breakout_verify1_core.json"))
    nul = json.load(open(OUT / "htf_breakout_verify1_nulls.json"))
    df = pd.read_csv(OUT / "htf_breakout_train.csv")
    df = df[(df["tr_n"] > 1) & np.isfinite(df["tr_t"])]
    N_hb = len(df)
    tp = pd.read_csv(OUT / "trend_pullback_train.csv")
    N_x2 = N_hb + len(tp)
    mom = core_["train_R_moments"]
    n, m_, sd = mom["n"], mom["mean"], mom["sd"]
    t_tr = m_ / sd * math.sqrt(n)
    p1 = float(sst.t.sf(t_tr, n - 1))
    res["train"] = {"t": round(t_tr, 3), "p_one_sided_unadjusted": round(p1, 5),
                    "bonferroni_256": round(min(1, p1 * N_hb), 4), "bonferroni_X2_family": round(min(1, p1 * N_x2), 4),
                    "sidak_256": round(1 - (1 - p1) ** N_hb, 4), "N_hb": N_hb, "N_x2": N_x2}
    # BHY-adjusted p over the logged htf_breakout TRAIN grid (valid under arbitrary dependence)
    ps = np.sort(sst.t.sf(df["tr_t"].values, df["tr_n"].values - 1))
    cN = np.sum(1.0 / np.arange(1, N_hb + 1))
    adj = np.minimum.accumulate((ps * N_hb * cN / np.arange(1, N_hb + 1))[::-1])[::-1]
    rank = int(np.searchsorted(ps, p1, side="left"))
    res["train"]["bhy_adjusted_p"] = round(float(min(1.0, adj[min(rank, N_hb - 1)])), 4)
    res["train"]["rank_of_finalist_by_p"] = rank + 1
    # deflated Sharpe ratio (Bailey & Lopez de Prado 2014) on per-trade R, trials = logged TRAIN grid
    sr_trials = (df["tr_t"] / np.sqrt(df["tr_n"])).values
    V = float(np.var(sr_trials, ddof=1))
    g = 0.5772156649
    Z = sst.norm
    sr0 = math.sqrt(V) * ((1 - g) * Z.ppf(1 - 1 / N_hb) + g * Z.ppf(1 - 1 / (N_hb * math.e)))
    sr = m_ / sd
    den = math.sqrt(1 - mom["skew"] * sr + (mom["kurt"] - 1) / 4 * sr ** 2)
    res["train"]["dsr"] = {"sr_per_trade": round(sr, 4), "sr_trials_mean": round(float(sr_trials.mean()), 4),
                           "sr_trials_sd": round(math.sqrt(V), 4), "sr0_expected_max_null": round(sr0, 4),
                           "psr_vs_0": round(float(Z.cdf(sr * math.sqrt(n - 1) / den)), 4),
                           "dsr": round(float(Z.cdf((sr - sr0) * math.sqrt(n - 1) / den)), 4),
                           "note": "trials share the same 2025 long drift, so sr0 is centred on 0 while every trial "
                                   "is shifted up by beta; DSR does not control for beta (the permutation nulls do)"}
    # VALID: the finalist was the better of 2 VALID looks (16 distinct configs looked at on VALID, SOURCED report)
    vm = core_["valid_R_moments"]
    tv = vm["mean"] / vm["sd"] * math.sqrt(vm["n"])
    pv = float(sst.t.sf(tv, vm["n"] - 1))
    best_null_p = min(nul["random_direction_valid"]["avg_pts"]["p"], nul["order_permutation_valid"]["avg_pts"]["p"],
                      nul["tradeset_permutation_valid"]["avg_pts"]["p"])
    res["valid"] = {"t_R": round(tv, 3), "p_one_sided_t_unadjusted": round(pv, 4),
                    "p_t_sidak_2_looks": round(1 - (1 - pv) ** 2, 4), "p_t_sidak_16_looks": round(1 - (1 - pv) ** 16, 4),
                    "smallest_null_p_avg_pts": best_null_p,
                    "smallest_null_p_sidak_2": round(1 - (1 - best_null_p) ** 2, 4),
                    "smallest_null_p_sidak_16": round(1 - (1 - best_null_p) ** 16, 4)}
    sr_v = vm["mean"] / vm["sd"]
    den_v = math.sqrt(1 - vm["skew"] * sr_v + (vm["kurt"] - 1) / 4 * sr_v ** 2)
    res["valid"]["psr_vs_0"] = round(float(Z.cdf(sr_v * math.sqrt(vm["n"] - 1) / den_v)), 4)
    # project-wide context: logged TRAIN configs per hypothesis (merged files where they exist; approximate)
    import re
    roots = {}
    for f in sorted(OUT.glob("*_train.csv")):
        stem = f.name[:-len("_train.csv")]
        if "verify" in stem or "OBSOLETE" in stem:
            continue
        root = re.sub(r"_(s\d+|stage\d+\w*)$", "", stem)
        k = sum(1 for _ in open(f)) - 1
        r = roots.setdefault(root, {"merged": 0, "stages": 0})
        if stem == root:
            r["merged"] = k
        else:
            r["stages"] += k
    # max(merged file rows, sum of stage-file rows): approximate count of logged TRAIN evaluations per hypothesis
    per_h = {k: int(max(v["merged"], v["stages"])) for k, v in roots.items()}
    res["project_logged_train_configs_approx"] = {"per_hypothesis": per_h, "total": int(sum(per_h.values()))}
    K = res["project_logged_train_configs_approx"]["total"]
    res["project_expected_max_t_under_null_independent"] = round(float(Z.ppf(1 - 1 / max(K, 2))), 2)
    jdump(res, OUT / "htf_breakout_verify1_mt.json")
    print(json.dumps(res, indent=1), flush=True)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "core"
    {"core": core, "nulls": nulls, "timing": timing, "nbrs": nbrs, "swap": swap, "lomo": lomo, "mt": mt}[mode]()
