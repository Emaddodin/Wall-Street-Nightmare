"""
scripts/flip_odds.py
====================
How likely is a $12.47 account to reach $100 with the MR P FX scalp, and with which parameters?

1. Signals from the LIVE MRPBreakRetestStrategy on real 1m gold candles (computed once, in parallel).
2. For each exit profile, every signal is played through the LIVE GridExitController on a 4-tick-per-bar path
   (mark at bid/ask, exit slippage) -> net POINTS per trade. Points are lot-independent, so ...
3. ... balance paths are cheap arithmetic: for every start day and every risk fraction we compound until the
   balance hits the target, falls below the ruin floor, or the horizon ends.

    python3 scripts/flip_odds.py --dir data/candles/real_bt2 --balance 12.47 --target 100
"""
import argparse
import concurrent.futures as cf
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ghost_grid.exit_controller import GridExitController, GridExitConfig  # noqa: E402
from ghost_grid.mrp_break_retest import MRPBreakRetestStrategy  # noqa: E402

TAIL = 130
SLIP = 0.05

PROFILES = {
    "tight":  GridExitConfig(quick_tp_pts=0.80, hard_stop_pts=0.90, watermark_min_peak_pts=0.45, stagnation_cut_secs=60, stagnation_min_pts=0.10, max_hold_time_secs=180),
    "mid":    GridExitConfig(quick_tp_pts=1.20, hard_stop_pts=1.00, watermark_min_peak_pts=0.70, stagnation_cut_secs=120, stagnation_min_pts=0.15, max_hold_time_secs=360),
    "video":  GridExitConfig(quick_tp_pts=2.00, hard_stop_pts=1.20, watermark_min_peak_pts=1.00, watermark_floor_pts=0.30, stagnation_cut_secs=240, stagnation_min_pts=0.20, max_hold_time_secs=900),
}


def day_signals(args):
    path, tail = args
    df = pd.read_csv(path)
    rows = df.to_dict("records")
    for r in rows:
        r["minute_ts"] = int(r["open_time"] // 1000)
        r["volume"] = float(r.get("volume", 1.0))
    strat = MRPBreakRetestStrategy()
    hist = list(tail)
    sigs = []
    for k, r in enumerate(rows):
        hist.append(r)
        if len(hist) > 300:
            hist = hist[-300:]
        if len(hist) < 55:
            continue
        s = strat.evaluate(hist[-TAIL:])
        if s:
            sigs.append((k, s.direction))
    return rows, sigs


def play(o, h, l, c, t, sigs, atr14, cfg, spread, min_atr):
    """Return list of (sig_idx_global, net_pts, end_idx_global)."""
    ctl_cfg = cfg
    out = []
    busy = -1
    n = len(c)
    for gi, d in sigs:
        if gi <= busy or gi + 2 >= n or atr14[gi] < min_atr:
            continue
        is_buy = d == "BUY"
        entry = c[gi] + spread / 2 if is_buy else c[gi] - spread / 2
        ctl = GridExitController(ctl_cfg)
        t0 = t[gi] + 60
        pos = [{"volume": 0.01, "entry_time": t0, "unrealized_pl": 0.0}]
        pts_net, end = None, None
        for j in range(gi + 1, min(n, gi + 200)):
            path = [o[j], l[j], h[j], c[j]] if c[j] >= o[j] else [o[j], h[j], l[j], c[j]]
            for k, px in enumerate(path):
                q = px - spread / 2 if is_buy else px + spread / 2
                pts = (q - entry) if is_buy else (entry - q)
                pos[0]["unrealized_pl"] = pts
                dec = ctl.evaluate_grid_tick(px, pos, t[j] + k * 15)
                if dec.action == "CLOSE_ALL":
                    pts_net, end = pts - SLIP, j
                    break
            if end is not None:
                break
        if end is None:
            end = min(n - 1, gi + 199)
            pts_net = pts - SLIP
        out.append((gi, pts_net, end))
        busy = end
    return out


def flip(trades, day_of, day_start_idx, start_day, bal0, risk, stop_pts, target, ruin, horizon_days, lev, price_ref=4300.0):
    bal = bal0
    for gi, pts, end in trades:
        dd = day_of[gi]
        if dd < start_day:
            continue
        if dd - start_day > horizon_days:
            return "timeout", bal, dd - start_day
        lots = int(risk * bal / (stop_pts * 100) / 0.01) * 0.01
        lots = max(0.01, round(lots, 2))
        max_lots = 0.9 * bal * lev / (price_ref * 100)
        if lots > max_lots:
            lots = round(int(max_lots / 0.01) * 0.01, 2)
        if lots < 0.01:
            return "ruin", bal, dd - start_day
        bal += pts * lots * 100
        if bal >= target:
            return "hit", bal, dd - start_day
        if bal < ruin:
            return "ruin", bal, dd - start_day
    return "timeout", bal, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/candles/real_bt2")
    ap.add_argument("--balance", type=float, default=12.47)
    ap.add_argument("--target", type=float, default=100.0)
    ap.add_argument("--spread", type=float, default=0.18)
    ap.add_argument("--leverage", type=float, default=1000.0)
    ap.add_argument("--horizon", type=int, default=60)
    a = ap.parse_args()

    files = sorted(Path(a.dir).glob("gold_m1_*.csv"))
    work, prev = [], []
    for f in files:
        work.append((str(f), prev))
        d = pd.read_csv(f).tail(TAIL).to_dict("records")
        for r in d:
            r["minute_ts"] = int(r["open_time"] // 1000)
        prev = d
    with cf.ProcessPoolExecutor() as ex:
        days = list(ex.map(day_signals, work))

    O, H, L, C, T, DAY, SIGS = [], [], [], [], [], [], []
    for di, (rows, sigs) in enumerate(days):
        base = len(C)
        for r in rows:
            O.append(r["open"]); H.append(r["high"]); L.append(r["low"]); C.append(r["close"])
            T.append(r["open_time"] / 1000.0); DAY.append(di)
        SIGS += [(base + k, d) for k, d in sigs]
    O, H, L, C, T = map(np.array, (O, H, L, C, T))
    rng = H - L
    atr14 = pd.Series(rng).rolling(14, min_periods=1).mean().to_numpy()
    print(f"days={len(days)} bars={len(C)} signals={len(SIGS)}", flush=True)

    starts = list(range(0, len(days) - 20, 3))
    results = []
    for pname, min_atr in itertools.product(PROFILES, (0.0, 1.0, 1.5)):
        cfg = PROFILES[pname]
        trades = play(O, H, L, C, T, SIGS, atr14, cfg, a.spread, min_atr)
        pts = np.array([x[1] for x in trades])
        stat = {"profile": pname, "min_atr": min_atr, "trades": len(trades),
                "wr": round(float((pts > 0).mean() * 100), 1) if len(pts) else 0,
                "avg_pts": round(float(pts.mean()), 3) if len(pts) else 0,
                "pf": round(float(pts[pts > 0].sum() / -pts[pts < 0].sum()), 2) if (pts < 0).any() else None}
        print(stat, flush=True)
        for risk in (0.10, 0.20, 0.30, 0.45, 0.60):
            res = [flip(trades, DAY, None, s, a.balance, risk, cfg.hard_stop_pts, a.target, 3.0, a.horizon, a.leverage) for s in starts]
            hit = [r for r in res if r[0] == "hit"]
            ruin = [r for r in res if r[0] == "ruin"]
            row = dict(stat, risk=risk, p_hit=round(len(hit) / len(res) * 100, 1), p_ruin=round(len(ruin) / len(res) * 100, 1),
                       med_days_hit=float(np.median([r[2] for r in hit])) if hit else None, starts=len(res))
            results.append(row)
    df = pd.DataFrame(results).sort_values(["p_hit", "p_ruin"], ascending=[False, True])
    pd.set_option("display.width", 250)
    print(df.head(20).to_string(index=False))
    df.to_csv(ROOT / "data/flip_odds.csv", index=False)


if __name__ == "__main__":
    main()
