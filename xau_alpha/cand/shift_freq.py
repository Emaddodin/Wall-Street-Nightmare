import sys, itertools, time
sys.path[:0] = ["lib", "cand"]
from concurrent.futures import ProcessPoolExecutor
import pandas as pd
_M = None
def _init():
    global _M
    from data import load_m1
    _M = load_m1()
def job(p):
    import shift_stack as S
    from sim import simulate, COSTS, stats
    sig = S.signals(_M, **p)
    ts = _M["ts"].values
    od = [{"t": int(ts[s["i"]]) + 60000, "d": s["d"], "kind": "mkt", "sl": s["stop"], "tmax": 300 * 60000} for s in sig]
    tr = simulate(od, COSTS["lf_base"])
    tr = tr[(tr.risk >= 0.8) & (tr.risk <= 6.0)]
    out = dict(p)
    days = _M.groupby("split").tday.nunique().to_dict()
    for s, nm in ((0, "tr"), (1, "va"), (2, "te")):
        x = tr[tr.split == s]; st = stats(x)
        out[f"{nm}_tpd"] = round(len(x) / days[s], 2); out[f"{nm}_pf"] = st.get("pf"); out[f"{nm}_R"] = st.get("avg_R")
    return out
if __name__ == "__main__":
    grid = {"drop": [2.0, 3.0], "mom": [1.5, 2.0], "nbull": [2, 3], "tol": [0.3, 0.6]}
    cfgs = [dict(zip(grid, v)) for v in itertools.product(*grid.values())]
    with ProcessPoolExecutor(3, initializer=_init) as ex:
        res = list(ex.map(job, cfgs))
    df = pd.DataFrame(res).sort_values("tr_tpd", ascending=False)
    pd.set_option("display.width", 250); print(df.to_string())
    df.to_csv("cand/results/shift_freq.csv", index=False)
