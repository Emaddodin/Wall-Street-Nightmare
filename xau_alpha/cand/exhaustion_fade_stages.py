"""
cand/exhaustion_fade_stages.py - staged TRAIN search for cand/exhaustion_fade.py on an explicit config list
(families are not a cartesian product, so sweep.py's GRID cannot express stage 2 / 3 directly).

    python3 cand/exhaustion_fade_stages.py s2      # top-5 stage-1 families x s(3) x m(3) x tmax(3) = 135
    python3 cand/exhaustion_fade_stages.py s3      # top-3 stage-2 configs x sess {eu_us, lonny} = 6

Ranking is by TRAIN t-stat of R at lf_base (sweep._job, n >= 60), exactly as sweep.py. The top configs are then
checked once on VALID at lf_base and lf_harsh. Uses 2 worker processes. TEST is never simulated.
Writes cand/results/exhaustion_fade_<stage>_train.csv and _valid.csv.
"""
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "cand"))
import sweep  # noqa: E402

NAME = "exhaustion_fade"
KEYS = ["tf", "k", "conf", "tp", "tmax", "s", "m", "sess"]
RES = ROOT / "cand/results"


def fam_key(r):
    return (int(r["tf"]), float(r["k"]), r["conf"], r["tp"])


def stage2_cfgs():
    s1 = pd.read_csv(RES / f"{NAME}_s1_train.csv")
    s1 = s1[np.isfinite(s1["rank_t"])].sort_values("rank_t", ascending=False)
    fams = []
    for _, r in s1.iterrows():
        f = fam_key(r)
        if f not in fams:
            fams.append(f)
        if len(fams) == 5:
            break
    cfgs = []
    for tf, k, conf, tp in fams:
        for s in (0.15, 0.3, 0.6):
            for m in (3, 5, 10):
                for tmax in (30, 60, 120):
                    cfgs.append(dict(tf=tf, k=k, conf=conf, tp=tp, tmax=tmax, s=s, m=m, sess="all"))
    return fams, cfgs


def stage3_cfgs():
    s2 = pd.read_csv(RES / f"{NAME}_s2_train.csv")
    s2 = s2[np.isfinite(s2["rank_t"])].sort_values("rank_t", ascending=False).head(3)
    cfgs = []
    for _, r in s2.iterrows():
        for sess in ("eu_us", "lonny"):
            cfgs.append(dict(tf=int(r["tf"]), k=float(r["k"]), conf=r["conf"], tp=r["tp"], tmax=int(r["tmax"]),
                             s=float(r["s"]), m=int(r["m"]), sess=sess))
    return None, cfgs


def run(stage, top=10):
    fams, cfgs = {"s2": stage2_cfgs, "s3": stage3_cfgs}[stage]()
    if fams:
        print("families:", fams, flush=True)
    print(f"[{stage}] {len(cfgs)} configs", flush=True)
    with ProcessPoolExecutor(max_workers=2) as ex:
        rows = list(ex.map(sweep._job, [(NAME, c, "base") for c in cfgs]))
    df = pd.DataFrame(rows)
    df["rank_t"] = np.where(df.get("tr_n", 0) >= sweep.MIN_N_RANK, df["tr_t"], -np.inf)
    df = df.sort_values("rank_t", ascending=False)
    df.to_csv(RES / f"{NAME}_{stage}_train.csv", index=False)
    vrows = []
    for _, r in df[np.isfinite(df["rank_t"])].head(top).iterrows():
        p = {k: (r[k].item() if hasattr(r[k], "item") else r[k]) for k in KEYS}
        for c in ("base", "harsh"):
            ev = sweep.evaluate(NAME, p, cost=c)
            vrows.append({**p, "cost": c, **{f"tr_{k}": v for k, v in ev["train"].items()},
                          **{f"va_{k}": v for k, v in ev["valid"].items()}})
    vdf = pd.DataFrame(vrows)
    vdf.to_csv(RES / f"{NAME}_{stage}_valid.csv", index=False)
    pd.set_option("display.width", 250)
    cols = KEYS + ["tr_n", "tr_pf", "tr_avg_pts", "tr_avg_R", "tr_t", "tr_pf_h1", "tr_pf_h2", "tr_fe_n", "tr_fe_avgR"]
    print(df[cols].head(20).to_string())
    print(vdf.to_string())


if __name__ == "__main__":
    run(sys.argv[1])
