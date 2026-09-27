"""
cand/sweep_reclaim_analysis.py - finalist evaluation and null tests for cand/sweep_reclaim.py (H1).

    cd xau_alpha && python3 cand/sweep_reclaim_analysis.py '<params json>' [--seeds 50] [--placebo 20] [--out tag]

TRAIN + VALID only (orders with t >= 2026-06-01 are removed before ANY simulation, including the null tests).
Writes cand/results/sweep_reclaim_analysis[_tag].json and prints a summary. Single process.
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

import nulltest  # noqa: E402
import sweep_reclaim as sr  # noqa: E402
from data import load_m1  # noqa: E402
from sim import COSTS, simulate, stats  # noqa: E402
from sweep import FLIP_STOP, TEST_MS, evaluate  # noqa: E402

NAME = "sweep_reclaim"


def _tstat(R):
    R = np.asarray(R, dtype=float)
    R = R[np.isfinite(R)]
    if len(R) < 2:
        return float("nan")
    return float(R.mean() / (R.std(ddof=1) + 1e-12) * np.sqrt(len(R)))


def _clean(x):
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    return x


def split_extra(tr):
    """t-stat of R, train halves, drop-best-month PF, per tag."""
    out = {}
    for sp, nm in ((0, "train"), (1, "valid")):
        t = tr[tr.split == sp].sort_values("t_in")
        if not len(t):
            continue
        h = len(t) // 2
        mon = pd.to_datetime(t["t_in"], unit="ms", utc=True).dt.strftime("%Y-%m")
        by_m = t.groupby(mon.values)["pnl"].sum()
        best = by_m.idxmax() if len(by_m) else None
        out[nm] = {"t_R": round(_tstat(t["R"]), 2),
                   "se_R": round(float(t["R"].std(ddof=1) / np.sqrt(len(t))), 3) if len(t) > 1 else None,
                   "half1": stats(t.iloc[:h]), "half2": stats(t.iloc[h:]),
                   "drop_best_month": {"month": best, **stats(t[mon.values != best])} if best else None}
    return out


def monthly(tr):
    t = tr.copy()
    t["month"] = pd.to_datetime(t["t_in"], unit="ms", utc=True).dt.strftime("%Y-%m")
    g = t.groupby("month").agg(n=("R", "size"), sum_R=("R", "sum"), avg_R=("R", "mean"), sum_pts=("pnl", "sum"),
                               wr=("pnl", lambda p: (p > 0).mean()))
    gw = t[t.pnl > 0].groupby("month")["pnl"].sum()
    gl = -t[t.pnl < 0].groupby("month")["pnl"].sum()
    g["pf"] = (gw / gl).reindex(g.index)
    return g.round(3)


def by_tag(tr):
    rows = {}
    for (sp, tag), t in tr.groupby(["split", "tag"]):
        rows[f"{['train', 'valid'][sp]}:{tag}"] = stats(t)
    return rows


def event_study(m1, P, horizons=(5, 15, 30, 60)):
    """Exit-free check: signed mid move from the decision close c[j] to c[j+h], in units of A[j] (M1 ATR).
    Uses every signal (no one-at-a-time filter), TRAIN/VALID only."""
    b = sr._base(m1)
    c, A, ts = b["c"], b["A"], b["ts"]
    out = {}
    for mode in ("fade", "cont"):
        od = [o for o in sr.orders(m1, **{**P, "mode": mode}) if o["t"] < TEST_MS]
        j = np.searchsorted(ts, np.array([o["t"] for o in od]) - 60_000)
        d = np.array([o["d"] for o in od])
        sp = (ts[j] >= 1767225600000).astype(int)          # 2026-01-01 = VALID start
        for h in horizons:
            jj = np.minimum(j + h, len(c) - 1)
            mv = (c[jj] - c[j]) * d / A[j]
            for s, nm in ((0, "train"), (1, "valid")):
                x = mv[sp == s]
                if len(x) > 1:
                    out[f"{mode}_{nm}_h{h}"] = {"n": int(len(x)), "mean_A": round(float(x.mean()), 3),
                                                "t": round(float(x.mean() / (x.std(ddof=1) + 1e-12) * np.sqrt(len(x))), 2)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("params")
    ap.add_argument("--seeds", type=int, default=50)
    ap.add_argument("--placebo", type=int, default=20)
    ap.add_argument("--out", default="")
    ap.add_argument("--skip_null", action="store_true")
    a = ap.parse_args()
    P = json.loads(a.params)
    m1 = load_m1()
    res = {"params": P}
    t0 = time.time()

    # 1. cost models
    trades = {}
    for cost in ("base", "harsh", "mid"):
        ev, tr = evaluate(NAME, P, cost=cost, return_trades=True)
        res[cost] = ev
        trades[cost] = tr
        print(f"[{time.time() - t0:.0f}s] {cost}: train {ev['train']} | valid {ev['valid']}", flush=True)
    trb = trades["base"]
    res["extra_base"] = split_extra(trb)
    res["extra_mid"] = split_extra(trades["mid"])
    res["by_tag_base"] = by_tag(trb)
    res["reasons_base"] = {f"{['train', 'valid'][sp]}": g["reason"].value_counts().to_dict()
                           for sp, g in trb.groupby("split")}
    res["risk_quantiles"] = {f"{['train', 'valid'][sp]}": g["risk"].quantile([.1, .25, .5, .75, .9]).round(2).tolist()
                             for sp, g in trb.groupby("split")}
    res["flip_share"] = {f"{['train', 'valid'][sp]}": float(((g.risk >= FLIP_STOP[0]) & (g.risk <= FLIP_STOP[1])).mean())
                         for sp, g in trb.groupby("split")}
    mon = monthly(trb)
    mon_mid = monthly(trades["mid"])
    res["monthly_base"] = mon.reset_index().to_dict(orient="records")
    res["monthly_mid"] = mon_mid.reset_index().to_dict(orient="records")
    res["event_study"] = event_study(m1, P)

    # 2. flip-only variant (orders filtered to [1.2, 4.0] before simulation, so one-at-a-time interplay changes)
    for cost in ("base", "harsh"):
        ev = evaluate(NAME, {**P, "flip": True}, cost=cost)
        res[f"flip_only_{cost}"] = {k: ev.get(k) for k in ("train", "valid", "train_long", "train_short",
                                                           "valid_long", "valid_short")}
    print(f"[{time.time() - t0:.0f}s] flip-only done", flush=True)

    # 3. news-window sensitivity (base)
    for nw in ("off", "2_5", "15_30"):
        if nw == P.get("news", "30"):
            continue
        ev = evaluate(NAME, {**P, "news": nw}, cost="base")
        res[f"news_{nw}"] = {"train": ev["train"], "valid": ev["valid"]}
    print(f"[{time.time() - t0:.0f}s] news sensitivity done", flush=True)

    if not a.skip_null:
        orders = [o for o in sr.orders(m1, **P) if o["t"] < TEST_MS]      # HOLDOUT: drop TEST before simulating
        # 4. random direction null, VALID and TRAIN, lf_base
        for sp, nm in ((1, "valid"), (0, "train")):
            rd = nulltest.random_direction(orders, seeds=range(a.seeds), cost="base", split=sp)
            real = res["base"][nm]
            p_avg = float(((rd["avg_pts"] >= real["avg_pts"]).sum() + 1) / (len(rd) + 1))
            p_pf = float(((rd["pf"] >= real["pf"]).sum() + 1) / (len(rd) + 1))
            res[f"random_dir_{nm}"] = {"seeds": len(rd), "p_avg_pts": round(p_avg, 4), "p_pf": round(p_pf, 4),
                                       "null_avg_pts_mean": round(float(rd["avg_pts"].mean()), 3),
                                       "null_avg_pts_sd": round(float(rd["avg_pts"].std()), 3),
                                       "null_pf_median": round(float(rd["pf"].median()), 3),
                                       "null_pf_p95": round(float(rd["pf"].quantile(0.95)), 3),
                                       "real_avg_pts": real["avg_pts"], "real_pf": real["pf"]}
            print(f"[{time.time() - t0:.0f}s] random dir {nm}: {res[f'random_dir_{nm}']}", flush=True)

        # 5. mirror (same times, opposite direction = continuation at the reclaim time)
        trm = simulate(nulltest.mirror(orders), COSTS["base"])
        res["mirror_base"] = {"train": stats(trm[trm.split == 0]), "valid": stats(trm[trm.split == 1])}
        trm = simulate(nulltest.mirror(orders), COSTS["mid"])
        res["mirror_mid"] = {"train": stats(trm[trm.split == 0]), "valid": stats(trm[trm.split == 1])}

        # 6. continuation twin (enter with the sweep at the first close beyond L + delta*A5)
        for cost in ("base", "mid"):
            ev = evaluate(NAME, {**P, "mode": "cont"}, cost=cost)
            res[f"cont_twin_{cost}"] = {k: ev.get(k) for k in ("train", "valid", "train_long", "train_short",
                                                               "valid_long", "valid_short")}
        print(f"[{time.time() - t0:.0f}s] twins done", flush=True)

        # 7. placebo levels
        pl = []
        for s in range(a.placebo):
            for cost in ("base", "mid"):
                ev = evaluate(NAME, {**P, "placebo_seed": s}, cost=cost)
                for sp in ("train", "valid"):
                    st = ev[sp]
                    pl.append({"seed": s, "cost": cost, "split": sp, "n": st.get("n", 0), "pf": st.get("pf"),
                               "avg_pts": st.get("avg_pts"), "avg_R": st.get("avg_R")})
        pdf = pd.DataFrame(pl)
        summ = {}
        for (cost, sp), g in pdf.groupby(["cost", "split"]):
            real = res[cost][sp]
            se = res["extra_base" if cost == "base" else "extra_mid"][sp]["se_R"]
            summ[f"{cost}_{sp}"] = {"placebo_n_mean": round(float(g.n.mean()), 1),
                                    "placebo_avg_R_mean": round(float(g.avg_R.mean()), 4),
                                    "placebo_avg_R_sd": round(float(g.avg_R.std()), 4),
                                    "placebo_pf_median": round(float(g.pf.median()), 3),
                                    "placebo_avg_pts_mean": round(float(g.avg_pts.mean()), 3),
                                    "real_avg_R": real["avg_R"], "real_se_R": se,
                                    "real_minus_placebo_in_SE": round((real["avg_R"] - float(g.avg_R.mean())) / se, 2)
                                    if se else None,
                                    "frac_placebo_ge_real_avgR": round(float((g.avg_R >= real["avg_R"]).mean()), 3)}
        res["placebo"] = summ
        print(f"[{time.time() - t0:.0f}s] placebo: {summ}", flush=True)

    out = ROOT / f"cand/results/{NAME}_analysis{('_' + a.out) if a.out else ''}.json"
    out.write_text(json.dumps(_clean(res), indent=1, default=str))
    print("wrote", out, f"{time.time() - t0:.0f}s")
    pd.set_option("display.width", 200)
    print(mon.to_string())


if __name__ == "__main__":
    main()
