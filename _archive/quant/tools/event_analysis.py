"""Event-level selectivity analysis for S6 Q-FADE runs.

Inputs: an S6 experiment (trades parquet with signal_t + events side-table
with per-event causal features).  Outputs:

  1. per-trade net bps (gross ret - fees - funding, all in price bps)
  2. feature -> win-rate / net-edge tables (which features predict wins)
  3. selectivity curve: slice EVENTS by score into top 50/25/10/5/2/1/
     0.5%, compute filled-trade stats per slice (the live view: the score
     is applied to events BEFORE knowing fill)
  4. learned logistic score (fit on a discovery window only)
  5. daily ROE distribution under (lev, alloc, trades/day cap) with
     sequential intraday compounding
  6. Monte Carlo (block bootstrap >= 10k paths) of daily ROE:
     P(+100%), P(<=-20%), P(<=-50%), P(<=-80%), P(ruin)
  7. leverage x alloc x cap grid

Usage:
  python3 quant/tools/event_analysis.py --report <run_id> \
      --discovery 2025-01-01:2025-08-31 --o 2025-09-01:2026-01-31 \
      --holdout 2026-02-01:2026-09-01
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DAY = 86_400_000

FEATURES = ["z", "z_time", "rvol", "rvol_accel", "vol_accel", "rv5",
            "rv60", "range_frac", "body_frac", "wick_up", "wick_dn",
            "hi_dist60", "lo_dist60", "hi_dist240", "lo_dist240",
            "oi_chg", "taker_imb", "lsr", "funding_ann", "btc_r15",
            "btc_rv", "r15", "r60"]


def load_run(rdir: Path, events_parquet: Path | None = None):
    rec = json.loads(open(rdir / "record.json").read())
    tr = pd.read_parquet(rdir / "trades.parquet")
    if events_parquet is None:
        events_parquet = rdir / rec.get("events_out", "")
    if events_parquet.exists():
        ev = pd.read_parquet(events_parquet)
    else:
        ev = None
    return rec, tr, ev


def net_bps(tr: pd.DataFrame) -> np.ndarray:
    """net price bps per trade = gross ret bps - fee bps - funding bps."""
    gross = tr["ret_pct"].to_numpy() * 100.0          # ret_pct is %
    notional = (tr["margin"] * tr["lev"]).to_numpy()
    fees = tr["fees"].to_numpy() / np.maximum(notional, 1e-9) * 1e4
    fund = tr["funding"].to_numpy() / np.maximum(notional, 1e-9) * 1e4
    return gross - fees - fund


def add_net(tr: pd.DataFrame) -> pd.DataFrame:
    out = tr.copy()
    out["net_bps"] = net_bps(tr)
    out["gross_bps"] = tr["ret_pct"].to_numpy() * 100.0
    out["fee_bps"] = tr["fees"].to_numpy() / np.maximum(
        tr["margin"].to_numpy() * tr["lev"].to_numpy(), 1e-9) * 1e4
    return out


def trade_stats(sub: pd.DataFrame) -> dict:
    n = len(sub)
    if n == 0:
        return {"trades": 0}
    nb = sub["net_bps"].to_numpy()
    wins = nb[nb > 0]
    losses = nb[nb <= 0]
    days = (sub["signal_t"].to_numpy() // DAY).max() - \
        (sub["signal_t"].to_numpy() // DAY).min() + 1
    out = {
        "trades": n,
        "trades_day": n / max(days, 1),
        "win_rate": float((nb > 0).mean()),
        "avg_win_bps": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss_bps": float(losses.mean()) if len(losses) else 0.0,
        "expectancy_bps": float(nb.mean()),
        "gross_bps": float(sub["gross_bps"].mean()),
        "fee_bps": float(sub["fee_bps"].mean()),
        "pf": float(wins.sum() / abs(losses.sum())) if losses.sum() != 0
        else float("inf"),
        "p95_win_bps": float(np.quantile(nb, 0.95)),
        "p5_loss_bps": float(np.quantile(nb, 0.05)),
        "avg_bars": float(sub["bars_held"].mean()),
    }
    return out


def day_partition(tr: pd.DataFrame) -> pd.Series:
    return pd.Series(tr["signal_t"].to_numpy() // DAY)


def daily_roe_series(sub: pd.DataFrame, lev: float, alloc: float,
                     per_day_cap: int | None = None,
                     cap_by_score: bool = False,
                     score_col: str = "score",
                     presorted: bool = False) -> tuple[pd.Series, int]:
    """Sequential intraday compounding of per-trade net returns; daily
    compounding across days.  Trade factor = 1 + alloc*lev*net_ret.
    cap_by_score: keep the per_day_cap HIGHEST-score trades each day.
    Returns (daily roe series, number of trades used)."""
    if presorted:
        df = sub.reset_index(drop=True)
    else:
        df = sub.sort_values(["signal_t", "entry_t"]).reset_index(drop=True)
    r = df["net_bps"].to_numpy() / 1e4
    day = df["signal_t"].to_numpy() // DAY
    sc = df[score_col].to_numpy() if score_col in df.columns else None
    out = {}
    used = 0
    for d in np.unique(day):
        m = day == d
        rr = r[m]
        if per_day_cap and len(rr) > per_day_cap:
            if cap_by_score and sc is not None:
                idx = np.argsort(-np.nan_to_num(sc[m], nan=-np.inf)
                                 )[:per_day_cap]
                rr = rr[np.sort(idx)]           # keep time order
            else:
                rr = rr[:per_day_cap]
        used += len(rr)
        roe = np.prod(1.0 + alloc * lev * rr) - 1.0
        out[d] = roe
    return pd.Series(out).sort_index(), used


def monte_carlo(daily: np.ndarray, n_paths: int = 10_000,
                path_len: int | None = None, seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    path_len = path_len or len(daily)
    paths = rng.choice(daily, size=(n_paths, path_len), replace=True)
    # per-path metrics
    hit100 = (paths >= 1.0).any(axis=1).mean()
    hit100_repeat = (paths >= 1.0).sum(axis=1)
    ruin = (paths <= -1.0).any(axis=1).mean()
    dd = np.maximum.accumulate(1.0 + paths, axis=1)
    maxdd = ((1.0 + paths) / dd - 1.0).min(axis=1)
    geo = np.prod(1.0 + paths, axis=1) - 1.0
    return {
        "n_paths": n_paths,
        "path_len": path_len,
        "P_hit_100_at_least_once": float(hit100),
        "P_hit_100_repeat": float((hit100_repeat >= 2).mean()),
        "P_hit_100_typical_day": float((daily >= 1.0).mean()),
        "P_ruin": float(ruin),
        "P_dd_gt_50": float((maxdd <= -0.5).mean()),
        "P_dd_gt_80": float((maxdd <= -0.8).mean()),
        "median_geo_growth": float(np.median(geo)),
        "mean_geo_growth": float(np.mean(geo)),
    }


def selectivity_curve(ev: pd.DataFrame, tr: pd.DataFrame, score_col: str,
                      fracs=(0.5, 0.25, 0.10, 0.05, 0.02, 0.01, 0.005)
                      ) -> pd.DataFrame:
    """Slice EVENTS by score; stats of the FILLED trades in each slice."""
    rows = []
    th = 1.0 - np.array(fracs)
    qs = np.quantile(ev[score_col].dropna(), th)
    for frac, q in zip(fracs, qs):
        sel_events = ev[ev[score_col] >= q]
        key = pd.MultiIndex.from_arrays(
            [sel_events["symbol"], sel_events["signal_t"]])
        tr_key = pd.MultiIndex.from_arrays(
            [tr["symbol"], tr["signal_t"]])
        sub = tr[tr_key.isin(key)]
        st = trade_stats(sub)
        st["selection"] = frac
        st["score_thr"] = q
        rows.append(st)
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--events", default=None, help="events parquet path")
    ap.add_argument("--discovery", default=None, help="YYYY-MM-DD:YYYY-MM-DD")
    ap.add_argument("--holdout", default=None)
    ap.add_argument("--lev", type=float, default=20.0)
    ap.add_argument("--alloc", type=float, default=0.25)
    ap.add_argument("--cap", type=int, default=None)
    ap.add_argument("--score", default="z_time",
                    help="score feature or 'logit' (fit on discovery)")
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    rdir = ROOT / "quant" / "data" / "experiments" / "reports" / args.report
    rec, tr, ev = load_run(rdir, Path(args.events) if args.events else None)
    tr = add_net(tr)
    if ev is not None and len(ev):
        # join event features onto trades (by symbol + signal_t)
        keep = ["symbol", "signal_t"] + \
               [c for c in (["score"] + FEATURES) if c in ev.columns]
        ev_dedup = ev[keep].drop_duplicates(["symbol", "signal_t"],
                                            keep="last")
        tr = tr.merge(ev_dedup, on=["symbol", "signal_t"], how="left")
    print(f"run {args.report}: {rec['strategy']} '{rec['hypothesis']}'")
    print(f"trades {len(tr)}  events {0 if ev is None else len(ev)}")
    print(f"ALL trades: {trade_stats(tr)}")

    # window splits by signal date
    def in_window(col, w):
        if not w:
            return np.ones(len(col), dtype=bool)
        a, _, b = w.partition(":")
        s = int(datetime.fromisoformat(a).replace(
            tzinfo=timezone.utc).timestamp() * 1000)
        e = int(datetime.fromisoformat(b).replace(
            tzinfo=timezone.utc).timestamp() * 1000)
        return (col >= s) & (col < e)

    if ev is None or "z_time" not in ev.columns:
        print("no events side-table with features; scoring on trades only")
        return 1

    # score selection
    if args.score == "logit":
        disc = in_window(ev["signal_t"], args.discovery)
        if not disc.any():
            print("logit needs a discovery window")
            return 1
        from scipy.optimize import minimize
        Xd = ev.loc[disc, FEATURES].to_numpy(dtype=float)
        # label: filled-and-won event = 1, else 0 (fill-conditional label)
        tr_disc = tr[in_window(tr["signal_t"], args.discovery)]
        won = set(zip(tr_disc.loc[tr_disc["net_bps"] > 0, "symbol"],
                      tr_disc.loc[tr_disc["net_bps"] > 0, "signal_t"]))
        filled = set(zip(tr_disc["symbol"], tr_disc["signal_t"]))
        y = np.array([1 if (s, t) in won else 0 for s, t in
                      zip(ev.loc[disc, "symbol"],
                          ev.loc[disc, "signal_t"])])
        mu = np.nanmean(Xd, axis=0)
        sd = np.nanstd(Xd, axis=0)
        Xs = (Xd - mu) / np.maximum(sd, 1e-9)
        Xs = np.nan_to_num(Xs)
        Xs = np.column_stack([np.ones(len(Xs)), Xs])
        def nll(w):
            p = 1 / (1 + np.exp(-Xs @ w))
            p = np.clip(p, 1e-9, 1 - 1e-9)
            return -(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()
        res = minimize(nll, np.zeros(Xs.shape[1]), method="L-BFGS-B")
        w = res.x
        Xall = (ev[FEATURES].to_numpy(dtype=float) - mu) / np.maximum(sd,
                                                                      1e-9)
        Xall = np.nan_to_num(Xall)
        ev["_score"] = np.column_stack([np.ones(len(Xall)), Xall]) @ w
        score_col = "_score"
        print("logit weights (intercept + features):")
        for f, wi in zip(["intercept"] + FEATURES, w):
            print(f"  {f:14s} {wi:+.3f}")
        print(f"train n={len(Xd)}")
    else:
        score_col = args.score
        ev["_score"] = ev[score_col]
        score_col = "_score"

    # selectivity curve on DISCOVERY window (events + their trades)
    print("\n=== selectivity curve (events sliced by score) ===")
    if args.discovery:
        ev_sel = ev[in_window(ev["signal_t"], args.discovery)]
        tr_sel = tr[in_window(tr["signal_t"], args.discovery)]
        print(f"(discovery window {args.discovery}: "
              f"{len(ev_sel)} events, {len(tr_sel)} trades)")
    else:
        ev_sel, tr_sel = ev, tr
    curve = selectivity_curve(ev_sel, tr_sel, score_col)
    cols = ["selection", "trades_day", "win_rate", "expectancy_bps",
            "gross_bps", "fee_bps", "pf", "avg_win_bps", "avg_loss_bps",
            "p95_win_bps", "p5_loss_bps", "avg_bars"]
    print(curve[cols].round(3).to_string(index=False))

    # per-feature win-rate table (discovery)
    print("\n=== single-feature conditioning (decile edges, discovery) ===")
    tr_sel2 = tr if not args.discovery else \
        tr[in_window(tr["signal_t"], args.discovery)]
    rows = []
    for f in FEATURES:
        if f not in tr.columns:
            continue
        x = pd.to_numeric(tr_sel2[f], errors="coerce")
        ok = x.notna() & (x.abs() < 1e9)
        if ok.sum() < 500:
            continue
        lo = tr_sel2.loc[ok & (x <= x[ok].quantile(0.2)), "net_bps"]
        hi = tr_sel2.loc[ok & (x >= x[ok].quantile(0.8)), "net_bps"]
        rows.append({"feature": f, "n": int(ok.sum()),
                     "lo_net": float(lo.mean()), "hi_net": float(hi.mean()),
                     "edge": float(hi.mean() - lo.mean()),
                     "lo_wr": float((lo > 0).mean()),
                     "hi_wr": float((hi > 0).mean())})
    feat_tab = pd.DataFrame(rows)
    if len(feat_tab):
        feat_tab = feat_tab.sort_values("edge", key=abs, ascending=False)
    print(feat_tab.round(3).to_string(index=False))

    # daily ROE for the full set and for the top-slice, under lev/alloc/cap
    print(f"\n=== daily ROE distribution "
          f"(lev={args.lev}, alloc={args.alloc}, cap={args.cap or 'inf'}) "
          f"===")
    for label, sub in (("ALL", tr), ("top1%", None)):
        if sub is None:
            q = np.quantile(ev_sel[score_col].dropna(), 0.99)
            sel_events = ev[ev[score_col] >= q]
            key = pd.MultiIndex.from_arrays(
                [sel_events["symbol"], sel_events["signal_t"]])
            tr_key = pd.MultiIndex.from_arrays(
                [tr["symbol"], tr["signal_t"]])
            sub = tr[tr_key.isin(key)]
        daily, _used = daily_roe_series(sub, args.lev, args.alloc,
                                          args.cap)
        d = daily.to_numpy()
        mc = monte_carlo(d, n_paths=10_000, path_len=30)
        sharpe = d.mean() / d.std() * np.sqrt(365) if d.std() > 0 else 0
        dn = d[d < 0]
        sortino = d.mean() / dn.std() * np.sqrt(365) if len(dn) and \
            dn.std() > 0 else float("inf")
        print(f"[{label}] n_trades={len(sub)} days={len(d)}")
        print(f"  median daily ROE: {np.median(d)*100:+.2f}%   "
              f"mean: {d.mean()*100:+.2f}%   "
              f"p5: {np.quantile(d,0.05)*100:+.2f}%   "
              f"p95: {np.quantile(d,0.95)*100:+.2f}%   "
              f"sharpe: {sharpe:.2f}  sortino: {sortino:.2f}")
        print(f"  P(day>=+100%): {(d>=1).mean()*100:.2f}%   "
              f"P(day<=-20%): {(d<=-0.2).mean()*100:.2f}%   "
              f"P(day<=-50%): {(d<=-0.5).mean()*100:.2f}%   "
              f"P(day<=-80%): {(d<=-0.8).mean()*100:.2f}%   "
              f"P(ruin): {(d<=-1).mean()*100:.2f}%")
        print(f"  MC(10k paths x 30d): P(hit +100% >=once)="
              f"{mc['P_hit_100_at_least_once']*100:.1f}%   "
              f"P(repeat)={mc['P_hit_100_repeat']*100:.1f}%   "
              f"P(ruin)={mc['P_ruin']*100:.1f}%   "
              f"P(dd>50%)={mc['P_dd_gt_50']*100:.1f}%   "
              f"median geo={mc['median_geo_growth']*100:+.1f}%")

    print("\n=== trade-frequency policy (top-K/day by score, ALL window, "
          f"lev={args.lev} alloc={args.alloc}) ===")
    print(f"{'cap':>8} {'n/day':>7} {'medROE':>8} {'meanROE':>8} "
          f"{'P100':>6} {'P-20':>6} {'P-50':>6} {'Pruin':>6}")
    for cap in (None, 100, 50, 25, 15, 10, 5, 3, 1):
        daily, used = daily_roe_series(tr, args.lev, args.alloc, cap,
                                       cap_by_score=True)
        d = daily.to_numpy()
        nd = used / max(len(d), 1)
        print(f"{str(cap):>8} {nd:7.1f} {np.median(d)*100:8.2f} "
              f"{d.mean()*100:8.2f} {(d>=1).mean()*100:6.2f} "
              f"{(d<=-0.2).mean()*100:6.2f} {(d<=-0.5).mean()*100:6.2f} "
              f"{(d<=-1).mean()*100:6.2f}")

    if args.grid:
        print("\n=== lev x alloc x cap grid (ALL trades, median daily ROE "
              "/ P(+100%) / P(ruin)) ===")
        for lev in (5, 10, 20, 30, 50):
            for alloc in (0.1, 0.25, 0.5):
                for cap in (None, 25, 10, 5):
                    daily = daily_roe_series(tr, lev, alloc, cap)
                    d = daily.to_numpy()
                    print(f"L={lev:2d} a={alloc:.2f} cap={str(cap):>4}  "
                          f"med={np.median(d)*100:+7.2f}%  "
                          f"P100={(d>=1).mean()*100:5.2f}%  "
                          f"Pruin={(d<=-1).mean()*100:5.2f}%  "
                          f"P-20={(d<=-0.2).mean()*100:5.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
