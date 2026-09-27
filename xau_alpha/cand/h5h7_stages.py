"""
cand/h5h7_stages.py - staged sweep / validation / null-test driver for cand/comex_momentum.py (H5) and
cand/news_second_leg.py (H7).

    python3 cand/h5h7_stages.py h5s1            # H5 stage 1: 216 configs (variant x theta x exit x kappa x cap)
    python3 cand/h5h7_stages.py h5s2            # H5 stage 2: refine the top 5 stage-1 parents
    python3 cand/h5h7_stages.py h7s1            # H7 stage 1: 96 configs (ev x W x mode x tp_r x f)
    python3 cand/h5h7_stages.py h7s2            # H7 stage 2: refine the top 5 stage-1 parents
    python3 cand/h5h7_stages.py valid <name> <stage_csv> [top]    # VALID check of the top TRAIN configs
    python3 cand/h5h7_stages.py final <name> '<json params>' [seeds]  # full report numbers for one frozen config

Selection is TRAIN only: every config is simulated with orders at/after 2026-06-01 dropped (sweep.run_orders), stats
on split 0 at cost 'lf_base', ranked by the TRAIN t-stat of R among configs with >= min_n TRAIN trades
(60 for H5 = sweep.MIN_N_RANK; 20 for H7, whose whole event universe is ~45-100 TRAIN events). The 'mid' (zero cost)
TRAIN stats are logged next to them to show the gross edge. Uses at most 2 worker processes.
"""
import itertools
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
from data import load_m1  # noqa: E402
from sim import stats  # noqa: E402

RES = ROOT / "cand/results"
H5, H7 = "comex_momentum", "news_second_leg"
MIN_N = {H5: 60, H7: 20}


def _clean(v):
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and np.isnan(v):
        return None
    return v


def tstat(x):
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 3:
        return float("nan")
    return float(x.mean() / (x.std(ddof=1) + 1e-12) * np.sqrt(len(x)))


def _trainstats(tr):
    tr0 = tr[tr["split"] == 0] if len(tr) else tr
    st = stats(tr0)
    if len(tr0) > 2:
        st["t"] = round(tstat(tr0["R"]), 2)
        st["tp"] = round(tstat(tr0["pnl"]), 2)
        h = len(tr0) // 2
        st["pf_h1"] = stats(tr0.iloc[:h]).get("pf")
        st["pf_h2"] = stats(tr0.iloc[h:]).get("pf")
        fe = tr0[(tr0["risk"] >= sweep.FLIP_STOP[0]) & (tr0["risk"] <= sweep.FLIP_STOP[1])]
        st["fe_n"] = int(len(fe))
        st["fe_avgR"] = round(float(fe["R"].mean()), 3) if len(fe) else None
        for d, nm in ((1, "L"), (-1, "S")):
            s = tr0[tr0.d == d]
            st[f"n{nm}"] = int(len(s))
            st[f"avgR{nm}"] = round(float(s["R"].mean()), 3) if len(s) else None
    return st


def job(args):
    """One config: orders once, simulate at lf_base and mid, TRAIN stats only."""
    name, params = args
    try:
        import importlib
        mod = importlib.import_module(name)
        m1 = load_m1()
        od = mod.orders(m1, **params)
        out = {**params}
        for cost, pre in (("base", "tr_"), ("mid", "mid_")):
            st = _trainstats(sweep.run_orders(od, cost))
            if pre == "mid_":
                st = {k: st.get(k) for k in ("n", "pf", "avg_pts", "avg_R", "t", "tp")}
            out.update({f"{pre}{k}": v for k, v in st.items()})
        return out
    except Exception as e:  # keep the sweep alive
        return {**params, "error": repr(e)[:300]}


def run(name, cfgs, out_csv, stage):
    t0 = time.time()
    rows = []
    with ProcessPoolExecutor(max_workers=2) as ex:
        for i, r in enumerate(ex.map(job, [(name, c) for c in cfgs])):
            rows.append({**r, "stage": stage})
            if i % 20 == 0:
                print(f"[{stage}] {i + 1}/{len(cfgs)} {time.time() - t0:.0f}s", flush=True)
    df = pd.DataFrame(rows)
    df["rank_t"] = df["tr_t"].where(df["tr_n"] >= MIN_N[name], -np.inf)
    df = df.sort_values("rank_t", ascending=False)
    df.to_csv(out_csv, index=False)
    pd.set_option("display.width", 250)
    cols = [c for c in df.columns if c in cfgs[0]] + ["tr_n", "tr_pf", "tr_avg_pts", "tr_t", "tr_pf_h1", "tr_pf_h2",
                                                      "tr_fe_n", "mid_pf", "mid_avg_pts", "mid_t"]
    print(df[cols].head(15).to_string(), flush=True)
    print(f"[{stage}] {len(cfgs)} configs in {time.time() - t0:.0f}s -> {out_csv}", flush=True)
    return df


def parents(csv, keys, n, name):
    df = pd.read_csv(csv)
    df = df[df["tr_n"] >= MIN_N[name]].dropna(subset=["tr_t"]).sort_values("tr_t", ascending=False)
    return [{k: _clean(r[k]) for k in keys} for _, r in df.head(n).iterrows()]


# ---------------------------------------------------------------------------------------------------------------- H5
H5_S1_KEYS = ["variant", "theta", "exit", "kappa", "cap"]


def h5s1():
    import comex_momentum as CM
    cfgs = []
    for var in ("a", "b", "c"):
        for th, ex, ka, cap in itertools.product([0.0, 0.25, 0.5], CM.EXITS[var], [1.0, 2.0],
                                                 ["none", "skip", "clamp"]):
            cfgs.append(dict(variant=var, theta=th, exit=ex, kappa=ka, cap=cap))
    return run(H5, cfgs, RES / f"{H5}_stage1_train.csv", "h5s1")


def h5s2():
    ps = parents(RES / f"{H5}_stage1_train.csv", H5_S1_KEYS, 5, H5)
    cfgs, seen = [], set()
    for p in ps:
        for th_mul, ka, med_n in itertools.product([0.6, 1.0, 1.6], [0.75, 1.5, 3.0], [10, 40]):
            th = round(p["theta"] * th_mul, 3) if p["theta"] > 0 else [0.0, 0.1, 0.15][[0.6, 1.0, 1.6].index(th_mul)]
            q = {**p, "theta": th, "kappa": ka, "med_n": med_n}
            k = json.dumps(q, sort_keys=True)
            if k not in seen:
                seen.add(k)
                cfgs.append(q)
    print(f"[h5s2] {len(ps)} parents, {len(cfgs)} configs", flush=True)
    return run(H5, cfgs, RES / f"{H5}_stage2_train.csv", "h5s2")


# ---------------------------------------------------------------------------------------------------------------- H7
H7_S1_KEYS = ["ev", "W", "mode", "tp_r", "f"]


def h7s1():
    import news_second_leg as NS
    cfgs = list(sweep.grid_iter(NS.GRID))
    return run(H7, cfgs, RES / f"{H7}_stage1_train.csv", "h7s1")


def h7s2():
    ps = parents(RES / f"{H7}_stage1_train.csv", H7_S1_KEYS, 5, H7)
    cfgs = []
    for p in ps:
        # R (retest / limit life) has no effect on the 'imm' arm, so it is not varied there
        Rs = [30] if p["mode"] == "imm" else [15, 45]
        for sigma, tmax, brk, R in itertools.product([0.2, 0.5], [30, 60], [60, 120], Rs):
            cfgs.append({**p, "sigma": sigma, "tmax": tmax, "brk": brk, "R": R})
    print(f"[h7s2] {len(ps)} parents, {len(cfgs)} configs", flush=True)
    return run(H7, cfgs, RES / f"{H7}_stage2_train.csv", "h7s2")


# ------------------------------------------------------------------------------------------------------ validation
def _flat(prefix, st):
    return {f"{prefix}{k}": v for k, v in st.items()}


def valid(name, csv, top=15):
    df = pd.read_csv(csv)
    keys = [c for c in df.columns if not (c.startswith(("tr_", "mid_", "rank_")) or c in ("stage", "error"))]
    df = df[df["tr_n"] >= MIN_N[name]].dropna(subset=["tr_t"]).sort_values("tr_t", ascending=False).head(int(top))
    rows = []
    for rank, (_, r) in enumerate(df.iterrows()):
        p = {k: _clean(r[k]) for k in keys if _clean(r[k]) is not None}
        for cost in ("base", "harsh", "mid"):
            ev, tr = sweep.evaluate(name, p, cost=cost, return_trades=True)
            row = {"rank": rank, **p, "cost": cost, **_flat("tr_", ev["train"]), **_flat("va_", ev["valid"])}
            if len(tr):
                row["tr_t"] = round(tstat(tr[tr.split == 0]["R"]), 2)
                row["va_t"] = round(tstat(tr[tr.split == 1]["R"]), 2)
                row["pool_t"] = round(tstat(tr[tr.split <= 1]["R"]), 2)
                for sp in ("train", "valid"):
                    for s in ("long", "short", "flip_eligible"):
                        st = ev.get(f"{sp}_{s}", {})
                        row[f"{sp[:2]}_{s[:4]}_n"] = st.get("n")
                        row[f"{sp[:2]}_{s[:4]}_pf"] = st.get("pf")
                        row[f"{sp[:2]}_{s[:4]}_avgR"] = st.get("avg_R")
            rows.append(row)
    out = pd.DataFrame(rows)
    stem = Path(csv).stem.replace("_train", "")
    out.to_csv(RES / f"{stem}_valid.csv", index=False)
    pd.set_option("display.width", 250)
    show = [c for c in ["rank", "cost", "tr_n", "tr_pf", "tr_avg_pts", "tr_t", "va_n", "va_pf", "va_avg_pts", "va_t",
                        "pool_t"] if c in out]
    print(out[keys + show].to_string(), flush=True)
    return out


# ----------------------------------------------------------------------------------------------------------- final
def monthly(tr):
    t = tr[tr.split <= 1].copy()
    t["month"] = pd.to_datetime(t["t_in"], unit="ms", utc=True).dt.strftime("%Y-%m")
    g = t.groupby("month")
    return pd.DataFrame({"n": g.size(), "sum_R": g["R"].sum().round(2), "avg_R": g["R"].mean().round(3),
                         "sum_pts": g["pnl"].sum().round(2),
                         "pf": g["pnl"].apply(lambda p: round(p[p > 0].sum() / -p[p < 0].sum(), 2)
                                              if (p < 0).any() else float("inf"))})


def final(name, params, seeds=60, extra_nulls=True):
    import nulltest
    import importlib
    mod = importlib.import_module(name)
    m1 = load_m1()
    out = {"name": name, "params": params}
    trades = {}
    for cost in ("base", "harsh", "mid", "duka_raw"):
        ev, tr = sweep.evaluate(name, params, cost=cost, return_trades=True)
        ev.pop("params", None)
        for sp in (0, 1):
            ev[f"{['train', 'valid'][sp]}"]["t"] = round(tstat(tr[tr.split == sp]["R"]), 2) if len(tr) else None
        ev["pool_t"] = round(tstat(tr[tr.split <= 1]["R"]), 2) if len(tr) else None
        ev["pool"] = stats(tr[tr.split <= 1]) if len(tr) else {"n": 0}
        out[cost] = ev
        trades[cost] = tr
    tr = trades["base"]
    tr0 = tr[tr.split == 0]
    h = len(tr0) // 2
    out["train_halves_pf"] = [stats(tr0.iloc[:h]).get("pf"), stats(tr0.iloc[h:]).get("pf")]
    mon = monthly(tr)
    out["monthly"] = mon.reset_index().to_dict(orient="records")
    # drop the best month (pooled train+valid)
    if len(mon):
        best = mon["sum_R"].idxmax()
        t2 = tr[(tr.split <= 1)]
        t2 = t2[pd.to_datetime(t2["t_in"], unit="ms", utc=True).dt.strftime("%Y-%m") != best]
        out["drop_best_month"] = {"month": best, **stats(t2)}
    # random-direction null on VALID and TRAIN (base)
    od = [o for o in mod.orders(m1, **params) if o["t"] < sweep.TEST_MS]
    for sp, nm in ((1, "valid"), (0, "train")):
        real = stats(tr[tr.split == sp])
        if real.get("n", 0) == 0:
            continue
        rd = nulltest.random_direction(od, seeds=range(int(seeds)), cost="base", split=sp)
        for key in ("avg_R", "pf", "avg_pts"):
            vals = rd[key].replace([np.inf], np.nan).dropna().values
            out[f"null_rd_{nm}_{key}"] = {"real": real.get(key), "null_mean": round(float(np.mean(vals)), 3),
                                          "null_sd": round(float(np.std(vals)), 3),
                                          "p": round(float((np.sum(vals >= real.get(key)) + 1) / (len(vals) + 1)), 4)}
    # mirror (every direction reversed)
    mt = sweep.run_orders(nulltest.mirror(od), "base")
    out["mirror"] = {"train": stats(mt[mt.split == 0]), "valid": stats(mt[mt.split == 1])} if len(mt) else {}
    RES.mkdir(parents=True, exist_ok=True)
    fn = RES / f"{name}_final.json"
    fn.write_text(json.dumps(out, indent=1, default=lambda v: v.item() if hasattr(v, "item") else str(v)))
    mon.to_csv(RES / f"{name}_monthly.csv")
    print(json.dumps({k: out[k] for k in out if k not in ("monthly",)}, indent=1, default=str)[:6000])
    print(mon.to_string())
    return out


def _row(label, name, params, costs=("base", "mid")):
    """TRAIN / VALID stats for one diagnostic config (counted as a config tried)."""
    out = {"label": label}
    for cost in costs:
        ev, tr = sweep.evaluate(name, params, cost=cost, return_trades=True)
        for sp in ("train", "valid"):
            st = ev[sp]
            out.update({f"{cost}_{sp}_{k}": st.get(k) for k in ("n", "pf", "avg_pts", "avg_R")})
            out[f"{cost}_{sp}_t"] = round(tstat(tr[tr.split == ["train", "valid"].index(sp)]["R"]), 2) if len(tr) else None
    return out


def _pooled_rows(label, name, plist, cost):
    """Pool the trades of several order sets (e.g. placebo shifts) into one TRAIN / VALID stats row."""
    trs = [sweep.evaluate(name, p, cost=cost, return_trades=True)[1] for p in plist]
    tr = pd.concat([t for t in trs if len(t)], ignore_index=True) if any(len(t) for t in trs) else pd.DataFrame()
    out = {"label": label}
    for sp, nm in ((0, "train"), (1, "valid")):
        s = tr[tr.split == sp] if len(tr) else tr
        st = stats(s)
        out.update({f"{cost}_{nm}_{k}": st.get(k) for k in ("n", "pf", "avg_pts", "avg_R")})
        out[f"{cost}_{nm}_t"] = round(tstat(s["R"]), 2) if len(s) > 2 else None
    return out


def h7extra(params):
    import news_second_leg as NS
    rows = [_row("finalist", H7, params)]
    for c in ("base", "mid"):
        rows.append(_pooled_rows(f"placebo +-1,+-2 weeks, same params ({c})", H7,
                                 [{**params, "placebo": k} for k in (1, -1, 2, -2)], c))
        rows.append(_pooled_rows(f"placebo +-1,+-2 weeks, f=0 ({c})", H7,
                                 [{**params, "placebo": k, "f": 0.0} for k in (1, -1, 2, -2)], c))
    rows.append(_row("blk=30 (flip margin rule)", H7, {**params, "blk": 30}))
    rows.append(_row("ev=all", H7, {**params, "ev": "all"}))
    rows.append(_row("mode=rt (trigger arm)", H7, {**params, "mode": "rt"}))
    rows.append(_row("mode=imm (chase arm)", H7, {**params, "mode": "imm"}))
    df = pd.DataFrame(rows)
    df.to_csv(RES / f"{H7}_diagnostics.csv", index=False)
    pd.set_option("display.width", 250)
    print(df.T.to_string())
    # by event type (finalist, base)
    m1 = load_m1()
    su = NS.setups(m1, **params)
    ev, tr = sweep.evaluate(H7, params, cost="base", return_trades=True)
    cal = NS._base(m1)["cal"]
    # event type of the HIGH row at T (same-minute MEDIUM rows such as jobless claims are ignored)
    hi = cal[cal["impact"] == "HIGH"].copy()
    hi["core"] = hi["type"].isin(NS.CORE)
    typ = hi.sort_values("core", ascending=False).drop_duplicates("ts").set_index("ts")["type"]
    tmap = dict(zip(su["t"].astype(np.int64), su["T"].astype(np.int64)))
    tr["T"] = tr["t_sig"].astype(np.int64).map(tmap)
    tr["type"] = tr["T"].map(typ)
    tr["month"] = pd.to_datetime(tr["t_in"], unit="ms", utc=True).dt.strftime("%Y-%m")
    by = tr[tr.split <= 1].groupby(["type"]).agg(n=("R", "size"), sum_R=("R", "sum"), avg_pts=("pnl", "mean"),
                                                 avg_risk=("risk", "mean")).round(3)
    print(by.to_string())
    by.to_csv(RES / f"{H7}_by_type.csv")
    cols = ["t_in", "d", "risk", "pnl", "R", "reason", "split", "type"]
    print(tr[cols].assign(t_in=pd.to_datetime(tr.t_in, unit="ms", utc=True)).to_string())
    # neighbour robustness (SYNTHESIS pass bar): VALID lf_base of every stage-1/2 config one step from the finalist
    s2 = pd.read_csv(RES / f"{H7}_stage2_train.csv")
    nb = [dict(ev="core", W=45, mode="touch", tp_r=1.5, f=0.0), dict(ev="core", W=45, mode="touch", tp_r=2.0, f=2.0),
          dict(ev="core", W=30, mode="touch", tp_r=2.0, f=0.0), dict(ev="all", W=45, mode="touch", tp_r=2.0, f=0.0)]
    fam = s2[(s2.ev == "core") & (s2.W == 45) & (s2["mode"] == "touch") & (s2.tp_r == 2.0) & (s2.f == 0.0)]
    nb += [{**params, "sigma": r.sigma, "tmax": int(r.tmax), "brk": int(r.brk), "R": int(r.R)}
           for r in fam.itertuples(index=False)]
    rows = []
    for q in nb:
        ev = sweep.evaluate(H7, q, cost="base")
        rows.append({**q, "tr_n": ev["train"].get("n"), "tr_pf": ev["train"].get("pf"),
                     "va_n": ev["valid"].get("n"), "va_pf": ev["valid"].get("pf"),
                     "va_avg_pts": ev["valid"].get("avg_pts")})
    nbd = pd.DataFrame(rows)
    nbd.to_csv(RES / f"{H7}_neighbours_valid.csv", index=False)
    print(nbd.to_string())
    print("median VALID PF of neighbours:", nbd["va_pf"].median(), " share > 1:", (nbd["va_pf"] > 1).mean())


def h5extra(params):
    rows = [_row("finalist", H5, params)]
    for te in (11 * 60, 15 * 60):
        rows.append(_row(f"placebo entry {te // 60}:00 ET", H5, {**params, "t_entry": te}))
    df = pd.DataFrame(rows)
    # post-hoc: variant b showed the OPPOSITE sign on TRAIN (late-day reversal); one pre-declared mirror check
    import nulltest
    m1 = load_m1()
    import comex_momentum as CM
    pb = dict(variant="b", theta=0.25, exit=25, kappa=2.0, cap="none")
    od = [o for o in CM.orders(m1, **pb) if o["t"] < sweep.TEST_MS]
    rev = {"label": "POST-HOC H5b reversal (mirror of b, theta .25, exit 16:25, kappa 2, cap none)"}
    for cost in ("base", "mid", "harsh"):
        mt = sweep.run_orders(nulltest.mirror(od), cost)
        for sp, nm in ((0, "train"), (1, "valid")):
            st = stats(mt[mt.split == sp])
            rev.update({f"{cost}_{nm}_{k}": st.get(k) for k in ("n", "pf", "avg_pts", "avg_R")})
            rev[f"{cost}_{nm}_t"] = round(tstat(mt[mt.split == sp]["R"]), 2)
    df = pd.concat([df, pd.DataFrame([rev])], ignore_index=True)
    df.to_csv(RES / f"{H5}_diagnostics.csv", index=False)
    pd.set_option("display.width", 250)
    print(df.T.to_string())


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "valid":
        valid(sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 15)
    elif cmd == "final":
        final(sys.argv[2], json.loads(sys.argv[3]), int(sys.argv[4]) if len(sys.argv) > 4 else 60)
    elif cmd == "h7extra":
        h7extra(json.loads(sys.argv[2]))
    elif cmd == "h5extra":
        h5extra(json.loads(sys.argv[2]))
    else:
        {"h5s1": h5s1, "h5s2": h5s2, "h7s1": h7s1, "h7s2": h7s2}[cmd]()
