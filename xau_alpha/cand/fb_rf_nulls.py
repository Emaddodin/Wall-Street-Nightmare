"""
cand/fb_rf_nulls.py - null / conflict checks for failed_breakout (H3) and range_fade (H10). TRAIN+VALID only.

    python3 cand/fb_rf_nulls.py twin_grid     # H3 stage-1 grid with fake=-1 (no-fake-out twin), TRAIN, paired
    python3 cand/fb_rf_nulls.py twin_final    # finalist + stage-2 top 5: fake=+1 / -1 / 0 (+ strict twin phi=0)
    python3 cand/fb_rf_nulls.py conflict      # H10 touch-event drift and H2 (zone_retest) on the same zones

Writes only small CSV/JSON summaries to cand/results/. At most 2 worker processes.
"""
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "cand"))
import sweep  # noqa: E402

RES = ROOT / "cand/results"
FB_S1 = ["w", "K", "N", "phi", "beta", "R", "mode"]
FB_S2 = FB_S1 + ["sigma", "tp_mode", "tp_r", "tmax"]
FINAL_FB = {"w": 1.0, "K": 6, "N": 30, "phi": 1.0, "beta": 0.2, "R": 5, "mode": "a", "sigma": 0.1,
            "tp_mode": "r", "tp_r": 1.5, "tmax": 45}
FINAL_RF = {"w": 0.5, "P": 60, "wid": 12, "K_edge": 6, "tp": "mid", "trig": 1, "sess": "lonny", "sigma": 0.3,
            "tmax": 30}
KEEP = ["n", "pf", "avg_pts", "avg_R", "avg_risk"]


def _clean(v):
    return v.item() if hasattr(v, "item") else v


def twin_grid():
    s1 = pd.read_csv(RES / "failed_breakout_s1_train.csv")
    cfgs = [{**{k: _clean(r[k]) for k in FB_S1}, "fake": -1} for _, r in s1.iterrows()]
    t0 = time.time()
    rows = []
    with ProcessPoolExecutor(max_workers=2) as ex:
        for i, r in enumerate(ex.map(sweep._job, [("failed_breakout", c, "base") for c in cfgs])):
            rows.append(r)
            if i % 40 == 0:
                print(f"[twin] {i + 1}/{len(cfgs)} {time.time() - t0:.0f}s", flush=True)
    tw = pd.DataFrame(rows)
    m = s1.merge(tw, on=FB_S1, suffixes=("", "_twin"))
    m.to_csv(RES / "failed_breakout_twin_train.csv", index=False)
    d_avgR = m["tr_avg_R"] - m["tr_avg_R_twin"]
    d_pts = m["tr_avg_pts"] - m["tr_avg_pts_twin"]
    out = {"configs": int(len(m)), "fake_beats_twin_avgR": int((d_avgR > 0).sum()),
           "fake_beats_twin_pts": int((d_pts > 0).sum()),
           "median_diff_avgR": round(float(d_avgR.median()), 3), "median_diff_pts": round(float(d_pts.median()), 3),
           "twin_pf_gt1": int((m["tr_pf_twin"] > 1).sum()), "twin_best_t": float(m["tr_t_twin"].max()),
           "by_mode": {k: round(float(g.median()), 3) for k, g in d_avgR.groupby(m["mode"])},
           "by_K": {int(k): round(float(g.median()), 3) for k, g in d_avgR.groupby(m["K"])}}
    print(json.dumps(out))


def _ev(name, p, cost):
    ev = sweep.evaluate(name, p, cost=cost)
    return {sp: {k: ev.get(sp, {}).get(k) for k in KEEP} for sp in ("train", "valid")}


def twin_final():
    s2 = pd.read_csv(RES / "failed_breakout_s2_train.csv")
    s2 = s2[np.isfinite(s2["rank_t"])].sort_values("rank_t", ascending=False).head(5)
    cfgs = [{k: _clean(r[k]) for k in FB_S2} for _, r in s2.iterrows()]
    out = []
    for i, p in enumerate(cfgs):
        arms = [(1, p["phi"]), (-1, p["phi"]), (0, p["phi"])] + ([(-1, 0.0)] if i == 0 else [])
        for fake, phi in arms:
            q = {**p, "fake": fake, "phi": phi}
            row = {"cfg": i, "fake": fake, "phi": phi}
            for cost in ("base", "mid"):
                e = _ev("failed_breakout", q, cost)
                for sp in ("train", "valid"):
                    for k in KEEP:
                        row[f"{cost}_{sp}_{k}"] = e[sp][k]
            out.append(row)
            print(json.dumps(row), flush=True)
    pd.DataFrame(out).to_csv(RES / "failed_breakout_twin_final.csv", index=False)


def conflict():
    from data import load_m1
    import range_fade as RF
    m1 = load_m1()
    import zonekit as ZK
    b = ZK.base(m1)
    c, A, n = b["c"], b["A"], b["n"]
    split = m1["split"].values[:n]
    res = {}
    # 1. touch-event drift in the FADE direction (mid, no cost, ATR units), control arm (trig=0) and trigger arm
    for tag, extra in (("final_zones_trig0", {"trig": 0}), ("final_zones_trig1", {"trig": 1}),
                       ("w1_K6_trig0", {"trig": 0, "w": 1.0})):
        p = {**FINAL_RF, **extra}
        df = RF.setups(m1, **p)
        j = df["j"].values.astype(int)
        d = df["d"].values
        for h in (5, 15, 30):
            ok = j + h < n
            r = d[ok] * (c[j[ok] + h] - c[j[ok]]) / A[j[ok]]
            sp = split[j[ok]]
            for s, nm in ((0, "train"), (1, "valid")):
                x = r[sp == s]
                if len(x) > 1:
                    res[f"{tag}_h{h}_{nm}"] = {"n": int(len(x)), "mean_A": round(float(x.mean()), 3),
                                               "t": round(float(x.mean() / x.std(ddof=1) * np.sqrt(len(x))), 2)}
    # 2. H2 (zone_retest, sibling module, its own defaults) on the same zone definition (w, K, L=240, k=3)
    for w, K in ((0.5, 6), (1.0, 6)):
        try:
            res[f"H2_zone_retest_w{w}_K{K}_base"] = _ev("zone_retest", {"w": w, "K": K}, "base")
            res[f"H2_zone_retest_w{w}_K{K}_mid"] = _ev("zone_retest", {"w": w, "K": K}, "mid")
        except Exception as e:  # sibling module may change under us
            res[f"H2_zone_retest_w{w}_K{K}"] = repr(e)[:200]
    # 3. H10 finalist and its mirror (continuation with the same geometry) at mid
    import nulltest
    orders = [o for o in RF.orders(m1, **FINAL_RF) if o["t"] < sweep.TEST_MS]
    from sim import stats
    for cost in ("mid", "base"):
        x = sweep.run_orders(nulltest.mirror(orders), cost)
        res[f"H10_mirror_{cost}"] = {"train": {k: stats(x[x.split == 0]).get(k) for k in KEEP},
                                     "valid": {k: stats(x[x.split == 1]).get(k) for k in KEEP}}
    (RES / "range_fade_conflict.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res))


if __name__ == "__main__":
    {"twin_grid": twin_grid, "twin_final": twin_final, "conflict": conflict}[sys.argv[1]]()
