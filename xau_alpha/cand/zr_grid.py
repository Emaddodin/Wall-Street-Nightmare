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
    import zr
    from sim import simulate, COSTS, stats
    od = zr.orders(_M, **p)
    out = dict(p); days = _M.groupby("split").tday.nunique().to_dict()
    for cn in ("mid", "lf_base"):
        tr = simulate(od, COSTS[cn])
        for s, nm in ((0, "25"), (1, "JM26"), (2, "JS26")):
            x = tr[tr.split == s]; st = stats(x)
            if cn == "lf_base":
                out[f"{nm}_n/d"] = round(len(x) / days[s], 1); out[f"{nm}_wr"] = st.get("wr")
            out[f"{nm}_{cn[:3]}"] = st.get("pf")
    return out
if __name__ == "__main__":
    cfgs = []
    for mode in ("trend", "trend_or_strong"):
        for sl, tp in ((3, 6), (4, 8), (6, 9), (6, 12)):
            cfgs.append(dict(mode=mode, sl=sl, tp=tp, stop_mode="fixed"))
        cfgs.append(dict(mode=mode, sl=6, tp=12, stop_mode="struct"))
    with ProcessPoolExecutor(3, initializer=_init) as ex:
        res = list(ex.map(job, cfgs))
    df = pd.DataFrame(res); pd.set_option("display.width", 250); print(df.to_string())
    df.to_csv("cand/results/zr_grid.csv", index=False)
