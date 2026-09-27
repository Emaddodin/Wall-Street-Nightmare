import sys, itertools
sys.path[:0] = ["lib", "cand"]
from concurrent.futures import ProcessPoolExecutor
import pandas as pd
_M = None
def _init():
    global _M
    from data import load_m1
    _M = load_m1()
def job(p):
    import trend_scalp as T
    from sim import simulate, COSTS, stats
    od = T.orders(_M, **p)
    tr = simulate(od, COSTS["lf_base"])          # one position at a time = back-to-back
    out = dict(p); days = _M.groupby("split").tday.nunique().to_dict()
    for s, nm in ((0, "25"), (1, "J-M26"), (2, "J-S26")):
        x = tr[tr.split == s]; st = stats(x)
        out[f"{nm}_tpd"] = round(len(x) / days[s], 1); out[f"{nm}_pf"] = st.get("pf"); out[f"{nm}_wr"] = st.get("wr")
    return out
if __name__ == "__main__":
    grid = {"htf": [60, 240], "sl": [2.5, 3.0], "tp": [1.0, 2.0, 3.0, 5.0], "body": [0.3]}
    cfgs = [dict(zip(grid, v)) for v in itertools.product(*grid.values())]
    with ProcessPoolExecutor(3, initializer=_init) as ex:
        res = list(ex.map(job, cfgs))
    df = pd.DataFrame(res); pd.set_option("display.width", 250); print(df.to_string())
    df.to_csv("cand/results/trend_scalp_grid.csv", index=False)
