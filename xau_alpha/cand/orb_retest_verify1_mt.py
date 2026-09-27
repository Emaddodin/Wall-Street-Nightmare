"""
cand/orb_retest_verify1_mt.py - multiple-testing adjustment for the H6 finalist (verifier #1). CSV-only, no data load.
Reads the logged stage-1/stage-2 TRAIN sweeps and the verifier JSON; prints and appends results to the JSON.
"""
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / "cand/results"
s1 = pd.read_csv(R / "orb_retest_train.csv")
s2 = pd.read_csv(R / "orb_retest_s2_train.csv")
v = json.load(open(R / "orb_retest_verify1.json"))

out = {}
N_raw = len(s1) + len(s2)
r1 = s1[s1.tr_n >= 60]
r2 = s2[s2.tr_n >= 60]
# distinct trade sets: identical (n, pf, avg, sum_R) rows are the same outcome
key = ["tr_n", "tr_pf", "tr_avg_pts", "tr_sum_R"]
d_all = pd.concat([r1[key + ["tr_t"]], r2[key + ["tr_t"]]]).drop_duplicates(key)
out["N_raw"] = N_raw
out["N_rankable"] = int(len(r1) + len(r2))
out["N_distinct_rankable"] = int(len(d_all))
t_all = d_all["tr_t"].values
out["train_t_cross_config"] = {"mean": round(float(t_all.mean()), 3), "sd": round(float(t_all.std(ddof=1)), 3),
                               "max": round(float(t_all.max()), 3)}

g = 0.5772156649


def emax(N):
    """E[max of N iid N(0,1)] (Bailey & Lopez de Prado 2014 approximation)."""
    return (1 - g) * norm.ppf(1 - 1.0 / N) + g * norm.ppf(1 - 1.0 / (N * math.e))


tr = v["lf_base"]["train"]
va = v["lf_base"]["valid"]
n_tr, t_tr = tr["n"], tr["t_R"]
sr_tr = t_tr / math.sqrt(n_tr)
res = {}
for N in (450, out["N_distinct_rankable"], 100):
    p1 = 1 - norm.cdf(t_tr)
    # deflated SR: benchmark SR0 = sd(SR across trials) * E[max Z]; trials' SR sd from their t / sqrt(n)
    sr_trials = (d_all["tr_t"] / np.sqrt(d_all["tr_n"])).values
    sr0 = float(np.std(sr_trials, ddof=1)) * emax(N)
    dsr = norm.cdf((sr_tr - sr0) * math.sqrt(n_tr - 1))           # normal returns (skew 0, kurt 3) assumed
    res[f"N={N}"] = {"E_max_t_under_null": round(float(emax(N)), 3), "train_t": t_tr,
                     "p_one_sided_raw": round(float(p1), 4), "p_bonferroni": round(float(min(1.0, p1 * N)), 4),
                     "p_sidak": round(float(1 - (1 - p1) ** N), 4),
                     "SR_per_trade": round(sr_tr, 4), "SR0_deflate": round(sr0, 4), "DSR_prob": round(float(dsr), 4)}
out["train_multiple_testing"] = res

# VALID: one finalist was frozen on TRAIN, but VALID numbers of many configs were looked at along the way
n_valid_looks = {"stage1_valid_csv_configs": 15, "stage2_valid_csv_configs": 8, "final_neighbours": 10,
                 "final_arms": 6, "context_configs": 3}
NV = sum(n_valid_looks.values())
out["valid_looks"] = {**n_valid_looks, "total": NV}
vres = {}
for lab, t in (("t_pts", va["t_pts"]), ("t_R", va["t_R"])):
    p1 = 1 - norm.cdf(t)
    vres[lab] = {"t": t, "p_one_sided": round(float(p1), 4), f"p_bonf_x{NV}": round(float(min(1, p1 * NV)), 4)}
rd = v["rd_valid"]
vres["random_direction"] = {k: rd[k] for k in ("p_avg_pts", "p_pf", "p_sum_R")}
vres["random_direction_bonf"] = {k: round(min(1.0, rd[k] * NV), 4) for k in ("p_avg_pts", "p_pf", "p_sum_R")}
out["valid_multiple_testing"] = vres
v["multiple_testing"] = out
json.dump(v, open(R / "orb_retest_verify1.json", "w"), indent=1)
print(json.dumps(out, indent=1))
