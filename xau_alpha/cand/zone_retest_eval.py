"""
cand/zone_retest_eval.py - final evaluation of the zone_retest (H2) config selected on TRAIN.

    python3 cand/zone_retest_eval.py [--seeds 50]

Selection: the highest TRAIN t-stat (lf_base) among configs with >= 60 TRAIN trades (the engine's ranking rule) over
every config of stages 1-3 (cand/results/zone_retest_stage*_train.csv).
Then, on TRAIN and VALID only (never TEST):
  - lf_base / lf_harsh / mid / duka_raw stats, long/short, flip-eligible (risk in [1.2, 4.0] $/oz), TRAIN halves
  - nulltest.random_direction (seeds) on TRAIN and VALID, mirror
  - control arms at the selected config: trigger off/on, touch 1/2, placebo zones shifted +-1.5*w*A
  - stage-1 arm comparison (TRAIN only): trigger vs no-trigger, touch 1 vs touch 2, paired on the other params
  - monthly R table and drop-best-month PF
Writes cand/results/zone_retest_final.json and cand/results/zone_retest_monthly.csv (small files).
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "cand"))
from data import load_m1  # noqa: E402
from nulltest import mirror, random_direction  # noqa: E402
from sim import COSTS, simulate, split_stats, stats  # noqa: E402
from sweep import MIN_N_RANK, TEST_MS, evaluate  # noqa: E402

import zone_retest as Z  # noqa: E402

NAME = "zone_retest"
RES = ROOT / "cand/results"
PKEYS = list(Z.DEFAULTS)


def _clean(v):
    v = v.item() if hasattr(v, "item") else v
    return v


def load_all_train():
    frames = []
    for s in ("1", "2a_lowN", "2", "3"):
        f = RES / f"{NAME}_stage{s}_train.csv"
        if f.exists():
            d = pd.read_csv(f)
            d["stage"] = f"s{s}"
            frames.append(d)
    return pd.concat(frames, ignore_index=True)


def params_of(row):
    p = {k: _clean(row[k]) for k in PKEYS if k in row and pd.notna(row[k])}
    return {**Z.DEFAULTS, **p}


def halves(tr0):
    h = len(tr0) // 2
    return stats(tr0.iloc[:h]).get("pf"), stats(tr0.iloc[h:]).get("pf")


def arm_table(s1, key):
    """Stage-1 TRAIN comparison of an arm, paired on all other stage-1 keys."""
    others = [k for k in Z.GRID if k != key]
    g = s1.groupby(key).agg(n_cfg=("tr_n", "size"), med_n=("tr_n", "median"), med_pf=("tr_pf", "median"),
                            med_avg_pts=("tr_avg_pts", "median"), med_avgR=("tr_avg_R", "median"),
                            med_t=("tr_t", "median"), best_t=("tr_t", "max"))
    vals = sorted(s1[key].unique())
    a, b = vals[-1], vals[0]
    m = s1[s1[key] == a].merge(s1[s1[key] == b], on=others, suffixes=("_a", "_b"))
    diff = m["tr_avg_R_a"] - m["tr_avg_R_b"]
    return {"by_arm": json.loads(g.round(3).to_json(orient="index")),
            "paired": {"a": _clean(a), "b": _clean(b), "pairs": int(len(m)),
                       "share_a_better_avgR": round(float((diff > 0).mean()), 3),
                       "median_diff_avgR": round(float(diff.median()), 3)}}


def main(seeds):
    m1 = load_m1()
    allt = load_all_train().dropna(subset=["tr_t"])
    allt = allt.sort_values("tr_t", ascending=False)
    ranked = allt[allt["tr_n"] >= MIN_N_RANK]            # engine ranking rule (sweep.MIN_N_RANK)
    best_row = ranked.iloc[0]
    best = params_of(best_row)
    print("configs in stage CSVs:", len(allt), "best stage", best_row["stage"], best, flush=True)
    out = {"n_configs_stage_csvs": int(len(allt)), "best_stage": best_row["stage"], "best": best}

    # costs
    ev = {}
    trades = {}
    for c in ("base", "harsh", "mid", "duka_raw"):
        e, tr = evaluate(NAME, best, cost=c, return_trades=True)
        ev[c] = e
        trades[c] = tr
        print(c, "train", e["train"], "\n      valid", e["valid"], flush=True)
    out["eval"] = ev
    tb = trades["base"]
    out["train_halves_pf_base"] = halves(tb[tb.split == 0])
    out["flip_share"] = {sp: round(float(((tb.risk >= 1.2) & (tb.risk <= 4.0))[tb.split == s].mean()), 3)
                         for s, sp in ((0, "train"), (1, "valid"))}
    out["risk_quantiles"] = {sp: [round(float(x), 2) for x in tb[tb.split == s].risk.quantile([.1, .5, .9])]
                             for s, sp in ((0, "train"), (1, "valid"))}
    # t-stat of R
    for s, sp in ((0, "train"), (1, "valid")):
        r = tb[tb.split == s]["R"]
        out[f"{sp}_t_base"] = round(float(r.mean() / (r.std(ddof=1) + 1e-12) * np.sqrt(len(r))), 2) if len(r) > 1 else None
    out["exit_reasons_base"] = {sp: tb[tb.split == s].reason.value_counts().to_dict() for s, sp in ((0, "train"), (1, "valid"))}

    # nulls
    od = [o for o in Z.orders(m1, **best) if o["t"] < TEST_MS]
    for s, sp in ((0, "train"), (1, "valid")):
        nd = random_direction(od, seeds=range(seeds), cost="base", split=s)
        act = ev["base"][sp]
        p_avg = (1 + int((nd["avg_pts"] >= act["avg_pts"]).sum())) / (1 + len(nd))
        p_pf = (1 + int((nd["pf"] >= act["pf"]).sum())) / (1 + len(nd))
        out[f"null_{sp}"] = {"seeds": int(len(nd)), "p_avg_pts": round(p_avg, 4), "p_pf": round(p_pf, 4),
                             "null_pf_median": round(float(nd["pf"].median()), 3),
                             "null_pf_p95": round(float(nd["pf"].quantile(0.95)), 3),
                             "null_avg_median": round(float(nd["avg_pts"].median()), 3)}
        print("null", sp, out[f"null_{sp}"], flush=True)
    mt = simulate(mirror(od), COSTS["base"])
    out["mirror_base"] = split_stats(mt)
    print("mirror", out["mirror_base"], flush=True)

    # control arms at the selected config (lf_base, TRAIN and VALID)
    arms = {}
    for nm, ch in (("trig_off", {"trig": 1 - int(best["trig"])}), ("touch_other", {"touch": 3 - int(best["touch"])}),
                   ("placebo_up", {"placebo": 1.5}), ("placebo_dn", {"placebo": -1.5})):
        e = evaluate(NAME, {**best, **ch}, cost="base")
        em = evaluate(NAME, {**best, **ch}, cost="mid")
        arms[nm] = {"change": ch, "train": e["train"], "valid": e["valid"],
                    "train_mid": em["train"], "valid_mid": em["valid"]}
        print("arm", nm, ch, e["train"], e["valid"], "| mid", em["train"], em["valid"], flush=True)
    out["arms_at_best"] = arms

    # stage-1 arm comparisons (TRAIN only, paired)
    s1 = allt[allt.stage == "s1"]
    out["stage1_arms"] = {k: arm_table(s1, k) for k in ("trig", "touch")}
    out["stage1_summary"] = {"n_cfg": int(len(s1)), "share_pf_gt1": round(float((s1.tr_pf > 1).mean()), 3),
                             "median_pf": round(float(s1.tr_pf.median()), 3), "max_pf": round(float(s1.tr_pf.max()), 3),
                             "median_n": float(s1.tr_n.median())}

    # monthly R table
    tb = tb.copy()
    tb["month"] = pd.to_datetime(tb["t_in"], unit="ms", utc=True).dt.strftime("%Y-%m")
    rows = []
    for mth, g in tb.groupby("month"):
        st = stats(g)
        rows.append({"month": mth, "split": int(g.split.iloc[0]), "n": st["n"], "long": int((g.d > 0).sum()),
                     "short": int((g.d < 0).sum()), "sum_R": st["sum_R"], "avg_R": st["avg_R"],
                     "avg_pts": st["avg_pts"], "pf": st["pf"], "avg_risk": st["avg_risk"]})
    mon = pd.DataFrame(rows)
    mon.to_csv(RES / f"{NAME}_monthly.csv", index=False)
    print(mon.to_string(index=False), flush=True)
    drop = {}
    for s, sp in ((0, "train"), (1, "valid")):
        mm = mon[mon.split == s]
        if len(mm):
            bm = mm.sort_values("sum_R").iloc[-1]["month"]
            drop[sp] = {"best_month": bm, "pf_without": stats(tb[(tb.split == s) & (tb.month != bm)]).get("pf")}
    out["drop_best_month"] = drop
    with open(RES / f"{NAME}_final.json", "w") as f:
        json.dump(out, f, indent=1, default=str)
    print(json.dumps({k: out[k] for k in ("train_halves_pf_base", "flip_share", "drop_best_month", "stage1_arms")},
                     indent=1, default=str))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=50)
    main(ap.parse_args().seeds)
