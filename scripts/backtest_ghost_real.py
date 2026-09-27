"""
scripts/backtest_ghost_real.py
==============================
Backtest of the LIVE MR P FX scalper (ghost_grid: MRPBreakRetestStrategy + GridExitController + CompoundingLadder +
the engine's risk-capped sizing, margin gate, loss cooldown and daily loss limit) on real 1m candles, optionally
followed by Apex Trinity once the balance reaches the handoff level (the live two-phase plan).

Signals only depend on candles, so they are computed once per day in parallel and cached. Tick model: 4 points per
1m bar (O-L-H-C for up bars, O-H-L-C for down bars), 15 s apart; the basket is marked at bid (BUY) / ask (SELL) so
the spread is paid; exits fill with slippage.

    python3 scripts/backtest_ghost_real.py --dir data/candles/duka --balance 12.47 --handoff 100 --tag scalper_1247
    python3 scripts/backtest_ghost_real.py --dir data/candles/duka --balance 12.47 --handoff 100 --then-apex --tag combo_1247
"""
import argparse
import concurrent.futures as cf
import hashlib
import json
import pickle
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from ghost_grid.compounding_ladder import CompoundingLadder  # noqa: E402
from ghost_grid.exit_controller import GridExitController, GridExitConfig  # noqa: E402
from ghost_grid.mrp_break_retest import MRPBreakRetestStrategy  # noqa: E402
import ghost_grid.ghost_engine as GE  # noqa: E402

TAIL = 130
STOP_BALANCE = 3.0   # account effectively dead; trades below the margin gate are simply skipped by size()


def _breakout_minutes(rows, tail):
    """Indices (into rows) of minutes on which a completed 5m bar has just closed beyond the prior 6-bar range
    (same 0.02% margin as MRPBreakRetestStrategy). Only these minutes can arm a breakout."""
    import numpy as np
    allr = list(tail) + rows
    ts = np.array([r["open_time"] // 1000 for r in allr], dtype=np.int64)
    hi = np.array([float(r["high"]) for r in allr]); lo = np.array([float(r["low"]) for r in allr]); cl = np.array([float(r["close"]) for r in allr])
    bucket = ts // 300
    starts = np.flatnonzero(np.r_[True, bucket[1:] != bucket[:-1]])
    ends = np.r_[starts[1:], len(ts)]
    h5 = np.array([hi[a:b].max() for a, b in zip(starts, ends)]); l5 = np.array([lo[a:b].min() for a, b in zip(starts, ends)])
    c5 = np.array([cl[b - 1] for b in ends])
    hits = []
    off = len(tail)
    for k in range(6, len(starts)):
        res = h5[k - 6:k].max(); sup = l5[k - 6:k].min()
        if c5[k] > res * 1.0002 or c5[k] < sup * 0.9998:
            last_min = ends[k] - 1                    # last 1m bar of that 5m bucket
            if last_min >= off:
                hits.append(last_min - off)
    return hits


def day_signals(args, fast=True):
    path, tail = args
    cache = ROOT / "data/state/ghost_days" / (Path(path).stem + ".pkl")
    if fast and cache.exists():
        return pickle.load(open(cache, "rb"))
    res = _day_signals_uncached(path, tail, fast)
    if fast:
        cache.parent.mkdir(parents=True, exist_ok=True)
        pickle.dump(res, open(cache, "wb"))
    return res


def _day_signals_uncached(path, tail, fast):
    df = pd.read_csv(path)
    rows = df.to_dict("records")
    for r in rows:
        r["minute_ts"] = int(r["open_time"] // 1000)
        r["volume"] = float(r.get("volume", 1.0))
    strat = MRPBreakRetestStrategy()
    if fast:
        cand = set()
        for m in _breakout_minutes(rows, tail):
            cand.update(range(m, min(len(rows), m + 27)))      # arming minute + the 25-min retest window
        wanted = sorted(cand)
    else:
        wanted = range(len(rows))
    hist_full = list(tail) + rows
    off = len(tail)
    sigs = []
    for k in wanted:
        hist = hist_full[max(0, off + k - 299): off + k + 1]
        if len(hist) < GE.MIN_REAL_CANDLES:
            continue
        s = strat.evaluate(hist[-TAIL:])
        if s:
            sigs.append((k, s.direction))
    return Path(path).stem.split("_")[-1], rows, sigs


def load_days(dirpath):
    files = sorted(Path(dirpath).glob("*_m1_*.csv"))
    key = hashlib.md5(("|".join(f"{f.name}:{f.stat().st_size}" for f in files) + "v4fast").encode()).hexdigest()[:10]
    cache = ROOT / f"data/state/ghost_signals_{key}.pkl"
    if cache.exists():
        return pickle.load(open(cache, "rb"))
    work, prev = [], []
    for f in files:
        work.append((str(f), prev))
        prev = pd.read_csv(f).tail(TAIL).to_dict("records")
        for r in prev:
            r["minute_ts"] = int(r["open_time"] // 1000)
    with cf.ProcessPoolExecutor() as ex:
        days = list(ex.map(day_signals, work))
    cache.parent.mkdir(parents=True, exist_ok=True)
    pickle.dump(days, open(cache, "wb"))
    return days


def iter_days(dirpath, chunk=8):
    """Yield (date, rows, sigs) in date order, computing signals lazily in parallel chunks so a simulation that
    stops early (ruin / handoff) never pays for the days it does not reach."""
    files = sorted(Path(dirpath).glob("*_m1_*.csv"))
    prev = []
    work = []
    for f in files:
        work.append((str(f), prev))
        prev = pd.read_csv(f).tail(TAIL).to_dict("records")
        for r in prev:
            r["minute_ts"] = int(r["open_time"] // 1000)
    with cf.ProcessPoolExecutor() as ex:
        for i in range(0, len(work), chunk):
            for res in ex.map(day_signals, work[i:i + chunk]):
                yield res
            print(f"  scanned {min(i + chunk, len(work))}/{len(work)} days", flush=True)


def size(balance, ladder, mid, leverage):
    """Mirror of GhostEngine._handle_signal sizing (mrp path). Returns (lot, n) or None to skip."""
    tier = ladder.resolve_tier(balance)
    lot, n = tier.lot_size, tier.grid_count
    max_total = GE.BASKET_RISK_EQUITY_PCT * balance / (GE.BASKET_STOP_PTS * 100.0)
    while n > 1 and lot * n > max_total + 1e-9:
        n -= 1
    if lot * n > max_total + 1e-9:
        lot = max(0.01, int(max_total / n * 100) / 100.0)
    if lot * n * mid * 100 / leverage * GE.MARGIN_BUFFER > balance:
        return None
    return lot, n


def path_points(o, h, l, c):
    return [o, l, h, c] if c >= o else [o, h, l, c]


def run_scalper(days, balance, handoff, spread=0.18, slip=0.05, leverage=500.0):
    ladder = CompoundingLadder()
    trades, daily, cool_until, consec, handed = [], [], 0.0, 0, None
    last_di = -1
    for di, (date, rows, sigs) in enumerate(days):
        last_di = di
        day_start = balance
        day_lock = False
        busy = -1
        if handed is None:
            for k, d in sigs:
                if k <= busy or day_lock:
                    continue
                r = rows[k]
                t_sig = r["open_time"] / 1000.0 + 60
                if t_sig < cool_until:
                    continue
                if balance <= day_start * (1 - GE.DAILY_LOSS_LIMIT_PCT):
                    day_lock = True
                    continue
                sz = size(balance, ladder, r["close"], leverage)
                if sz is None:
                    continue
                lot, n = sz
                total = lot * n
                is_buy = d == "BUY"
                entry = r["close"] + spread / 2 if is_buy else r["close"] - spread / 2
                ctl = GridExitController(GridExitConfig())
                pos = [{"volume": lot, "entry_time": t_sig, "unrealized_pl": 0.0} for _ in range(n)]
                pts_net, reason, end = None, "DATA_END", len(rows) - 1
                done = False
                for j in range(k + 1, len(rows)):
                    b = rows[j]
                    for q_i, px in enumerate(path_points(b["open"], b["high"], b["low"], b["close"])):
                        q = px - spread / 2 if is_buy else px + spread / 2
                        pts = (q - entry) if is_buy else (entry - q)
                        for p in pos:
                            p["unrealized_pl"] = pts * p["volume"] * 100
                        dec = ctl.evaluate_grid_tick(px, pos, b["open_time"] / 1000.0 + q_i * 15)
                        if dec.action == "CLOSE_ALL":
                            pts_net, reason, end, done = pts - slip, dec.reason.split(" (")[0], j, True
                            break
                    if done:
                        break
                if pts_net is None:
                    pts_net = pts - slip
                pnl = pts_net * total * 100
                balance += pnl
                busy = end
                consec = 0 if pnl > 0 else consec + 1
                if consec >= GE.MAX_CONSEC_LOSSES:
                    cool_until = rows[end]["open_time"] / 1000.0 + GE.LOSS_COOLDOWN_SECS
                    consec = 0
                trades.append({"date": date, "dir": d, "lots": round(total, 2), "pts": round(pts_net, 3), "pnl": round(pnl, 2),
                               "exit_reason": reason, "running_balance": round(balance, 2), "is_win": pnl > 0})
                if balance < STOP_BALANCE:
                    break
                if handoff and balance >= handoff:
                    handed = date
                    break
        daily.append({"day_num": di + 1, "date": date, "start_balance": round(day_start, 2), "end_balance": round(balance, 2),
                      "day_pnl": round(balance - day_start, 2), "withdrawn_today": 0.0, "cumulative_withdrawn": 0.0,
                      "trades_count": sum(1 for t in trades if t["date"] == date), "phase": "SCALPER" if handed is None or handed == date else "HANDOFF"})
        if balance < STOP_BALANCE or handed:
            # remaining days are reported flat by the caller
            return trades, daily, balance, handed, di
    return trades, daily, balance, handed, last_di


def summarize(trades, start, final, vault, daily, tag_extra=None):
    t = pd.DataFrame(trades)
    s = {"starting_capital": start, "final_retained_equity_usd": round(final, 2), "total_cash_withdrawn_usd": round(vault, 2),
         "total_trades_logged": len(t)}
    if len(t):
        w, l = t[t.pnl > 0].pnl.sum(), -t[t.pnl < 0].pnl.sum()
        eq = pd.Series(t.running_balance.values)
        peak = eq.cummax().clip(lower=start)
        s.update({"overall_win_rate_pct": round((t.pnl > 0).mean() * 100, 1), "profit_factor": round(w / l, 2) if l else 0.0,
                  "expectancy_usd": round(t.pnl.mean(), 2), "max_drawdown_pct": round(float(((peak - eq) / peak).max() * 100), 1),
                  "exit_breakdown": t.exit_reason.value_counts().to_dict()})
    d = pd.DataFrame(daily)
    s["profitable_days"] = int((d.day_pnl > 0).sum()); s["losing_days"] = int((d.day_pnl < 0).sum())
    if tag_extra:
        s.update(tag_extra)
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/candles/duka")
    ap.add_argument("--balance", type=float, default=12.47)
    ap.add_argument("--handoff", type=float, default=100.0)
    ap.add_argument("--spread", type=float, default=0.18)
    ap.add_argument("--slip", type=float, default=0.05)
    ap.add_argument("--leverage", type=float, default=500.0)
    ap.add_argument("--then-apex", action="store_true")
    ap.add_argument("--tag", default="scan")
    ap.add_argument("--scan-only", nargs=2, type=int, metavar=("FROM", "TO"), help="only compute+cache day signals for day indexes [FROM, TO)")
    a = ap.parse_args()

    if a.scan_only:
        files = sorted((ROOT / a.dir).glob("*_m1_*.csv"))
        work, prev = [], []
        for f in files:
            work.append((str(f), prev))
            prev = pd.read_csv(f).tail(TAIL).to_dict("records")
            for r in prev:
                r["minute_ts"] = int(r["open_time"] // 1000)
        lo, hi = a.scan_only
        with cf.ProcessPoolExecutor() as ex:
            for n, _ in enumerate(ex.map(day_signals, work[lo:hi]), 1):
                if n % 10 == 0:
                    print(f"scanned {n}/{len(work[lo:hi])}", flush=True)
        print("SCAN_DONE", flush=True)
        return
    all_dates = [f.stem.split("_")[-1] for f in sorted((ROOT / a.dir).glob("*_m1_*.csv"))]
    days = iter_days(ROOT / a.dir)
    trades, daily, bal, handed, last_i = run_scalper(days, a.balance, a.handoff, a.spread, a.slip, a.leverage)
    print(f"simulated {last_i + 1} of {len(all_dates)} days before stopping (balance ${bal:.2f}, handoff={handed})", flush=True)
    vault = 0.0
    extra = {"handoff_date": handed}
    if a.then_apex and handed:
        import os
        os.environ.setdefault("BT_MIN_ATR", "1.5")
        import backtest_live_loop_trump_regime as BT
        BT.MIN_ENTRY_ATR = float(os.environ["BT_MIN_ATR"])
        nxt = all_dates[last_i + 1] if last_i + 1 < len(all_dates) else None
        if nxt:
            summ, journal, apex_daily = BT.run_live_loop_backtest(data_dir=str(ROOT / a.dir), start_date_str=nxt, starting_balance=bal, mode="live")
            for r in apex_daily:
                r["day_num"] = len(daily) + r["day_num"]; r["phase"] = "APEX"
            daily += apex_daily
            trades += [{"date": j.date, "dir": j.direction, "lots": j.initial_volume, "pts": j.points_captured, "pnl": j.realized_pnl,
                        "exit_reason": j.exit_reason, "running_balance": j.running_balance, "is_win": j.is_win} for j in journal]
            bal = summ["final_retained_equity_usd"]; vault = summ["total_cash_withdrawn_usd"]
    else:
        for i in range(last_i + 1, len(all_dates)):     # scalper stopped (handoff or ruin): report the remaining days flat
            daily.append({"day_num": i + 1, "date": all_dates[i], "start_balance": round(bal, 2), "end_balance": round(bal, 2), "day_pnl": 0.0,
                          "withdrawn_today": 0.0, "cumulative_withdrawn": 0.0, "trades_count": 0, "phase": "HANDOFF-WAIT" if handed else "STOPPED"})
    s = summarize(trades, a.balance, bal, vault, daily, extra)
    out = ROOT / "data"
    json.dump(s, open(out / f"{a.tag}_summary.json", "w"), indent=1, default=str)
    pd.DataFrame(daily).to_csv(out / f"{a.tag}_daily.csv", index=False)
    pd.DataFrame(trades).rename(columns={"pnl": "realized_pnl"}).to_csv(out / f"{a.tag}_journal.csv", index=False)
    print(json.dumps(s, indent=1, default=str))


if __name__ == "__main__":
    main()
