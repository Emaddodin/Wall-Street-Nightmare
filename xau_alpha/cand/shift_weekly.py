"""Weekly-flip evaluation of shift_stack: every ISO week starts at $13; report peak/final/bust/lock per week."""
import sys, json, itertools, time
from concurrent.futures import ProcessPoolExecutor
sys.path[:0] = ["lib", "cand"]
import numpy as np, pandas as pd

_M = _SIG = None


def _init():
    global _M, _SIG
    from data import load_m1
    import shift_stack as S
    _M = load_m1()
    _SIG = S.signals(_M)


def run(cfg, split, eq0=13.0, lock=450.0):
    import shift_stack as S
    from data import _ms
    if _M is None:
        _init()
    sp = _M["split"].values
    wk = pd.to_datetime(_M["ts"].values[[s["i"] for s in _SIG]], unit="ms").isocalendar().week.values * 1 + \
        pd.to_datetime(_M["ts"].values[[s["i"] for s in _SIG]], unit="ms").year.values * 100
    rows = []
    for w in sorted(set(wk)):
        ss = [s for s, ww in zip(_SIG, wk) if ww == w and sp[s["i"]] == split]
        if not ss:
            continue
        tr = S.backtest(_M, ss, eq0=eq0, until_ms=_ms("2026-06-01"), lock_eq=lock, **cfg)
        if not len(tr):
            continue
        eqs = tr["eq"].values
        rows.append({"week": w, "n": int(tr["pnl"].notna().sum()) if "pnl" in tr else 0,
                     "peak": float(np.max(eqs)), "final": float(eqs[-1]),
                     "bust": bool((tr.get("skip") == "ruined").any()) if "skip" in tr else False,
                     "lock": bool((tr.get("skip") == "locked").any()) if "skip" in tr else False})
    return pd.DataFrame(rows)


def summarize(df):
    if not len(df):
        return {}
    return {"weeks": len(df), "p_peak50": round((df.peak >= 50).mean(), 3), "p_peak100": round((df.peak >= 100).mean(), 3),
            "p_lock450": round(df.lock.mean(), 3), "p_bust": round(df.bust.mean(), 3),
            "med_final": round(df.final.median(), 2), "mean_final": round(df.final.mean(), 2), "max_peak": round(df.peak.max(), 1)}


def job(cfg):
    t = time.time()
    r = summarize(run(cfg, 0))
    return {**cfg, **{f"tr_{k}": v for k, v in r.items()}, "sec": round(time.time() - t, 1)}


if __name__ == "__main__":
    grid = {"add_mode": ["riskfree"], "rf_budget": [0.0, 0.5, 1.0], "piv_k": [2, 5, 10], "trail_buf": [0.3, 1.0],
            "flat_min": [300, 720], "max_adds": [30]}
    cfgs = [dict(zip(grid, v)) for v in itertools.product(*grid.values())]
    if len(sys.argv) > 1 and sys.argv[1] == "time":
        _init(); print(job(cfgs[0])); sys.exit()
    with ProcessPoolExecutor(3, initializer=_init) as ex:
        res = list(ex.map(job, cfgs))
    df = pd.DataFrame(res).sort_values(["tr_p_peak100", "tr_mean_final"], ascending=False)
    df.to_csv("cand/results/shift_weekly_rf_train.csv", index=False)
    pd.set_option("display.width", 250)
    print(df.head(15).to_string())
