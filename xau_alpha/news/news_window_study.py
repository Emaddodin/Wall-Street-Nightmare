"""
xau_alpha/news/news_window_study.py
MEASURED effect of the production news windows and guard vetoes on real Dukascopy XAUUSD data.

Part 1  Microstructure around HIGH-impact minutes T (unique minutes, Dukascopy M1 bid/ask):
        mean mid range and mean spread by offset bucket, as a ratio to the quiet same-minute baseline;
        post-news drift: mid return from T+5 to T+45 (production boosts BUY size x1.5 / TP x2 in that window).
Part 2  Runner flip strategy (lib/ref_runner.py = ghost_grid/runner_strategy.py + runner_exit.py) simulated with
        lib/sim.py ('base' costs), with and without the production guards replayed by news/replay.py.
        Only TRAIN (2025-01-21..2025-12-31) and VALID (2026-01-01..2026-05-31) are reported: TEST stays untouched.

Run: python3 xau_alpha/news/news_window_study.py      (single process; ~2-4 min)
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
LIB = HERE.parent / "lib"
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(HERE))

import replay  # noqa: E402

MIN = 60_000
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)


def part1(cal, m1):
    ts = m1["ts"].values.astype(np.int64)
    mid_h = (m1["bh"].values + m1["ah"].values) / 2
    mid_l = (m1["bl"].values + m1["al"].values) / 2
    mid_c = (m1["bc"].values + m1["ac"].values) / 2
    rng = mid_h - mid_l
    spr = m1["spr"].values
    pos = pd.Series(np.arange(len(ts)), index=ts)
    ev_all = np.sort(cal["ts"].values)
    hi = np.unique(cal.loc[cal["impact"] == "HIGH", "ts"].values)
    hi = hi[(hi > ts[0] + 30 * 86_400_000) & (hi < ts[-1] - 2 * 3_600_000)]

    def quiet(t, tol=15 * MIN):
        j = np.searchsorted(ev_all, t - tol)
        return not (j < len(ev_all) and ev_all[j] <= t + tol)

    offs = np.arange(-30, 91)
    R = np.full((len(hi), len(offs)), np.nan)
    S = np.full((len(hi), len(offs)), np.nan)
    for a, t in enumerate(hi):
        # quiet baseline: same minute-of-day, previous 20 quiet weekdays, for each offset
        base_days = []
        d = 1
        while len(base_days) < 20 and d < 60:
            tt = t - d * 86_400_000
            d += 1
            if pd.Timestamp(tt, unit="ms", tz="UTC").dayofweek < 5 and quiet(tt, 120 * MIN):
                base_days.append(tt)
        for b, k in enumerate(offs):
            i = pos.get(t + k * MIN)
            if i is None:
                continue
            bi = [pos.get(x + k * MIN) for x in base_days]
            bi = [x for x in bi if x is not None]
            if len(bi) < 5:
                continue
            br, bs = np.median(rng[bi]), np.median(spr[bi])
            if br > 0:
                R[a, b] = rng[i] / br
            if bs > 0:
                S[a, b] = spr[i] / bs
    buckets = [(-30, -16), (-15, -6), (-5, -1), (0, 0), (1, 4), (5, 14), (15, 44), (45, 90)]
    tab = []
    for lo, hi_ in buckets:
        sel = (offs >= lo) & (offs <= hi_)
        tab.append({"offset_min": f"[{lo},{hi_}]", "range_ratio_mean": round(float(np.nanmean(R[:, sel])), 2),
                    "range_ratio_median": round(float(np.nanmedian(R[:, sel])), 2),
                    "spread_ratio_mean": round(float(np.nanmean(S[:, sel])), 2)})
    # post-news drift T+5 -> T+45 (what the BUY boost bets on) and T -> T+5 direction persistence
    drift, first = [], []
    for t in hi:
        i0, i5, i45, im1 = pos.get(t), pos.get(t + 4 * MIN), pos.get(t + 44 * MIN), pos.get(t - MIN)
        if None in (i0, i5, i45, im1):
            continue
        p_before, p5, p45 = mid_c[im1], mid_c[i5], mid_c[i45]      # closes: T-1, T+4 (=T+5 known), T+44
        first.append(p5 - p_before)
        drift.append(p45 - p5)
    drift, first = np.array(drift), np.array(first)
    cont = np.sign(first) * drift
    res = {"n_high_minutes": int(len(hi)), "by_offset": tab,
           "post_T5_T45_drift_usd": {"n": int(len(drift)), "mean": round(float(drift.mean()), 3),
                                     "median": round(float(np.median(drift)), 3),
                                     "t_stat": round(float(drift.mean() / (drift.std(ddof=1) / np.sqrt(len(drift)))), 2),
                                     "share_up": round(float((drift > 0).mean()), 3)},
           "continuation_of_first5min_move_usd": {"mean": round(float(cont.mean()), 3),
                                                  "t_stat": round(float(cont.mean() / (cont.std(ddof=1) / np.sqrt(len(cont)))), 2),
                                                  "share_continue": round(float((cont > 0).mean()), 3)}}
    return res


def part2(cal, m1):
    from ref_runner import runner_orders
    from sim import COSTS, simulate, split_stats

    orders = runner_orders(m1=m1)
    t = np.array([o["t"] for o in orders], dtype=np.int64)
    pm = replay.production_masks(t, cal, window_minutes=15)
    for o, fz, po in zip(orders, pm["frozen"], pm["post"]):
        o["frozen"], o["post"] = bool(fz), bool(po)
        o["hour"] = int((o["t"] // 3_600_000) % 24)

    verdicts = {}
    for state in ("offline_seed", "neutral"):
        rp = replay.ProductionGuardReplay(cal=cal, state=state, feed="calendar")
        v = [rp.ghost_verdict(o["t"], "BUY" if o["d"] > 0 else "SELL") for o in orders]
        verdicts[state] = v
    rp_off = replay.ProductionGuardReplay(cal=cal, state="offline_seed", feed="offline")
    verdicts["offline_seed_nofeed"] = [rp_off.ghost_verdict(o["t"], "BUY" if o["d"] > 0 else "SELL") for o in orders]

    cost = COSTS["base"]
    variants = {
        "all_signals": orders,
        "drop_news_freeze[T-15,T+5]": [o for o in orders if not o["frozen"]],
        "only_news_freeze_window": [o for o in orders if o["frozen"]],
        "only_post_news(T+5,T+45]": [o for o in orders if o["post"]],
        "long_only": [o for o in orders if o["d"] > 0],
        "short_only": [o for o in orders if o["d"] < 0],
        "prod_guards_offline_seed(calendar_feed)": [o for o, v in zip(orders, verdicts["offline_seed"]) if v["allowed"]],
        "prod_guards_neutral(calendar_feed)": [o for o, v in zip(orders, verdicts["neutral"]) if v["allowed"]],
        "prod_guards_offline_seed(no_feed)": [o for o, v in zip(orders, verdicts["offline_seed_nofeed"]) if v["allowed"]],
    }
    out = {"n_orders": len(orders)}
    for name, od in variants.items():
        tr = simulate(od, cost)
        out[name] = {"orders": len(od), **split_stats(tr)}
    # veto attribution on the unfiltered signal set
    import collections
    for state, v in verdicts.items():
        c = collections.Counter(x["why"] or "allowed" for x in v)
        out[f"veto_reasons_{state}"] = dict(c)
    # trade-level: trades of the unfiltered run tagged by window / RAG-veto class
    tr = simulate(orders, cost)
    tmap = {o["t"]: o for o in orders}
    tr["frozen"] = tr["t_sig"].map(lambda x: tmap[x]["frozen"])
    tr["post"] = tr["t_sig"].map(lambda x: tmap[x]["post"])
    tr["hour"] = tr["t_sig"].map(lambda x: tmap[x]["hour"])
    trv = tr[tr["split"] <= 1]

    def pf(df):
        p = df["pnl"].values
        gl = -p[p < 0].sum()
        return {"n": int(len(df)), "pf": round(float(p[p > 0].sum() / gl), 3) if gl > 0 else None,
                "avg_R": round(float(np.nanmean(df["R"])), 3) if len(df) else None,
                "sum_R": round(float(np.nansum(df["R"])), 1)}
    out["trade_level_train+valid"] = {
        "in_freeze": pf(trv[trv["frozen"]]), "in_post": pf(trv[trv["post"]]),
        "outside_news": pf(trv[~trv["frozen"] & ~trv["post"]]),
        "short_h09_23": pf(trv[(trv["d"] < 0) & (trv["hour"] >= 9)]),
        "short_h00_08": pf(trv[(trv["d"] < 0) & (trv["hour"] < 9)]),
        "long_all": pf(trv[trv["d"] > 0]), "hour23": pf(trv[trv["hour"] == 23]),
    }
    return out


def main():
    cal = replay.load_cal()
    m1 = pd.read_parquet(HERE.parent / "data/m1_ba.parquet")
    res = {"part1": part1(cal, m1)}
    print(json.dumps(res["part1"], indent=1), flush=True)
    from data import load_m1
    res["part2"] = part2(cal, load_m1())
    print(json.dumps(res["part2"], indent=1), flush=True)
    (OUT / "news_window_study.json").write_text(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
