"""Train and judge the session models (quant_sessions.py): Asia -> London (A), London morning -> overlap (B),
Asia + London -> New York (C), after Solomon Eshun's "Quantitative XAU/USD Session Strategy"
(https://github.com/soloshun/Quantitative-XAUUSD-Strategy, MIT licence).

    python3 quant_train.py --sessions data/dukascopy_xauusd_m1.csv.gz --silver data/dukascopy_xagusd_m1.csv.gz
    python3 quant_train.py --sessions xauusd_h1.csv.gz --split 2025-01-01      # an H1 CSV works too
    python3 quant_train.py --sessions --mt5 --mt5-bars 50000                    # your broker's H1 via the bridge
    python3 quant_train.py --sessions ... --ref-repo ~/Quantitative-XAUUSD-Strategy   # + their pickled XGB models
    (python3 quant_sessions_train.py ... is the same)

One sample per London weekday and hypothesis: inputs from H1 candles closed when the target session starts,
target = that session's direction and return in %. Sessions follow real UTC with Europe/London daylight saving
(MT5 server-time candles are converted first); see quant_sessions.py for what changed from the original and why.

Validation, the same honest way as the 30-minute model: the days from --split on (default: the last 20%, like
the original's 80/20) are held out and judged once. On the training days, walk-forward folds (train on the
past only, one day of embargo) pick depth / leaf size and, by early stopping, the number of trees; the
probabilities are Platt-calibrated on those folds' out-of-fold forecasts. Report: accuracy against a coin with
the 95% range for that many days, Brier skill (95% range by resampling days), log-loss, AUC, calibration, the
return regression's correlation, "trade the session in the forecast direction" after the 0.22 spread, and
year by year. beats_coin = held-out Brier skill > 0 AND accuracy's lower 95% bound > 50%.

A year of M1 is only ~250 sessions per hypothesis (~50 held out: a coin's range is then +/-14%). For a
meaningful test use years of H1 (MT5 keeps ~50,000 H1 candles = 8 years: --mt5 --mt5-bars 50000).

If xgboost is installed, the report adds the original's exact XGBoost settings (n_estimators 1000, learning
rate 0.05, max depth 5) on the same days, on these inputs and on the original's unscaled ones. With --ref-repo
(and xgboost + joblib), their shipped models/xgb_classifier_hyp_a_*.joblib are scored on your held-out days with
their own inputs, for comparison only: they were fitted on another broker's candles read on the wrong clock and
on dollar amounts from 2015-2025 prices, so they are out of distribution and are never used live.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import quant_features as qf
import quant_sessions as qs
import quant_train as qt

np = qt.np
SESSIONS_FILE = Path.home() / ".golddesk" / "quant_sessions.json"
MODEL_NAME = "quant-sessions v1"
CREDIT = ("Session hypotheses A / B / C after Solomon Eshun, Quantitative-XAUUSD-Strategy "
          "(https://github.com/soloshun/Quantitative-XAUUSD-Strategy), MIT licence")
SGRID = ({"depth": 2, "min_leaf_frac": 0.04}, {"depth": 3, "min_leaf_frac": 0.04}, {"depth": 2, "min_leaf_frac": 0.10})
SHP = {"lr": 0.05, "lam": 5.0, "subsample": 0.8, "colsample": 0.8}
XGB_THEIRS = {"objective": "binary:logistic", "eval_metric": "logloss", "n_estimators": 1000, "learning_rate": 0.05,
              "max_depth": 5, "random_state": 42}


def day_str(d: int) -> str:
    return datetime.fromtimestamp(d * qs.DAY, timezone.utc).strftime("%Y-%m-%d")


def to_h1(b, name: str):
    if b is None:
        return None
    if b.sec == 3600:
        return b
    if b.sec == 60:
        return qf.merge_closed(None, b, 3600)
    raise SystemExit(f"{name}: need M1 or H1 candles (got {b.sec // 60}-minute ones)")


# ====================================================================== dataset
def build(h1, silver=None, log=print) -> dict:
    """{hyp: {"X", "y", "ret", "day", "open", "close", "orig" (their inputs or None rows)}} from UTC H1 candles."""
    qt.need_numpy()
    t0 = time.time()
    sb = qs.SessionBuilder(h1, silver)
    days = sb.days()
    out = {}
    for hyp in ("A", "B", "C"):
        X, y, r, dd, op, cl, orig = [], [], [], [], [], [], []
        names = list(qs.ORIGINAL) + list(qs.ORIGINAL_EXTRA[hyp])
        for d in days:
            tg = sb.target(d, hyp)
            if tg is None:
                continue
            v = sb.vector(d, hyp)
            if v is None:
                continue
            X.append(v)
            y.append(tg[0])
            r.append(tg[1])
            dd.append(d)
            op.append(tg[2])
            cl.append(tg[3])
            o = sb.original(d, hyp)
            orig.append([o[k] for k in names] if o else [np.nan] * len(names))
        out[hyp] = {"X": np.array(X, float).reshape(len(X), len(qs.FEATURES[hyp])), "y": np.array(y, float),
                    "ret": np.array(r, float), "day": np.array(dd, np.int64), "open": np.array(op), "close": np.array(cl),
                    "orig": np.array(orig, float).reshape(len(orig), len(names)), "orig_names": names,
                    "features": list(qs.FEATURES[hyp])}
    if log:
        log(f"  session samples: " + ", ".join(f"{h} {len(out[h]['y'])}" for h in out) +
            f" ({time.time() - t0:.1f} s)")
    out["_builder"] = sb
    return out


# ====================================================================== one hypothesis
def session_trades(p, open_, close, edge: float = 0.0, spread: float = qt.SPREAD) -> dict:
    """Trade every session in the forecast direction (when |p - 0.5| >= edge), open to close, after the spread.
    Returns in % of price per session."""
    m = np.abs(p - 0.5) >= edge if edge > 0 else np.ones(len(p), bool)
    if not m.any():
        return {"trades": 0}
    d = np.where(p[m] > 0.5, 1.0, -1.0)
    r = (d * (close[m] - open_[m]) - spread) / open_[m] * 100
    sd = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    return {"trades": int(m.sum()), "hit": round(float((r > 0).mean()), 4), "mean_pct": round(float(r.mean()), 4),
            "total_pct": round(float(r.sum()), 3), "sharpe": round(float(r.mean() / sd * math.sqrt(252)), 2) if sd > 0 else None}


def train_hyp(ds: dict, split_day: int, folds: int = 4, grid=SGRID, max_trees: int = 300, patience: int = 30,
              seed: int = 7, log=print) -> tuple:
    X, y, ret, day = ds["X"], ds["y"], ds["ret"], ds["day"]
    tr, te = day < split_day - 1, day >= split_day                 # one day of embargo before the split
    if tr.sum() < 60 or te.sum() < 15:
        raise SystemExit(f"Too few sessions: {int(tr.sum())} before the split, {int(te.sum())} after. Load more "
                         "history (years of H1: --mt5 --mt5-bars 50000) or move --split.")
    Xtr, ytr, rtr, dtr = X[tr], y[tr], ret[tr], day[tr]
    edges = qt.make_edges(Xtr, 32)
    XbT, XbT_te = qt.apply_bins(Xtr, edges), qt.apply_bins(X[te], edges)
    t_tr = dtr * qs.DAY
    fl = qt.walk_forward(t_tr, folds, qs.DAY, qs.DAY)
    sel = qt.cv_select(XbT, ytr, fl, "logloss", grid, max_trees, patience, seed, log=log, hp=SHP)
    cfg = sel["cfg"]
    ml = max(5, int(cfg["min_leaf_frac"] * len(ytr) * SHP["subsample"]))
    have = ~np.isnan(sel["oof"])
    ab = qt.platt(sel["oof"][have], ytr[have])
    cls = qt.GBM(loss="logloss", depth=cfg["depth"], min_leaf=ml, seed=seed, **SHP).fit(XbT, ytr, n_trees=sel["trees"])
    delta = float(np.quantile(np.abs(rtr - np.median(rtr)), 0.9)) or 0.1
    rsel = qt.cv_select(XbT, rtr, fl, "huber", [cfg], max_trees, patience, seed, delta=delta, log=log, hp=SHP)
    reg = qt.GBM(loss="huber", depth=cfg["depth"], min_leaf=ml, seed=seed, delta=delta, **SHP).fit(
        XbT, rtr, n_trees=rsel["trees"])
    p = qt.calibrated(cls.margin(XbT_te), ab)
    yte = y[te]
    clim = float(np.clip(ytr.mean(), qt.P_LO, qt.P_HI))
    met = qt.binary_metrics(p, yte, len(yte), day=day[te], clim=clim)
    rp = reg.margin(XbT_te)
    met["reg_corr"] = qt.corr(rp, ret[te])
    met["reg_hit"] = round(float(np.mean(np.sign(rp) == np.sign(ret[te]))), 4)
    years = np.array([day_str(d)[:4] for d in day[te]])
    met["years"] = qt.group_table(years, p, yte)
    met["trade_all"] = session_trades(p, ds["open"][te], ds["close"][te])
    met["trade_edge"] = session_trades(p, ds["open"][te], ds["close"][te], edge=qt.EDGE)
    met["buy_hold"] = session_trades(np.ones(len(yte)), ds["open"][te], ds["close"][te])
    feats = ds["features"]
    gs = cls.gain.sum() or 1.0
    met["importance"] = [{"feature": feats[i], "gain": round(float(cls.gain[i] / gs), 4)}
                         for i in np.argsort(-cls.gain)[:12] if cls.gain[i] > 0]
    met["cv"] = {"direction": sel["all"], "move": rsel["all"], "chosen": cfg, "trees": sel["trees"],
                 "move_trees": rsel["trees"], "platt": [round(ab[0], 5), round(ab[1], 5)]}
    met["n_test"], met["n_train"] = int(len(yte)), int(len(ytr))
    ent = {"hypothesis": None, "features": feats, "cls": cls.export(edges), "reg": reg.export(edges),
           "platt": {"a": ab[0], "b": ab[1]}, "metrics": met, "beats_coin": qt.beats_coin(met),
           "trained_period": f"{day_str(dtr[0])} to {day_str(dtr[-1])}",
           "test_period": f"{day_str(day[te][0])} to {day_str(day[te][-1])}",
           "hyper": {**cfg, **SHP, "min_leaf": ml, "trees": sel["trees"], "move_trees": rsel["trees"],
                     "huber_delta": round(delta, 6), "folds": folds}}
    return ent, {"cls": cls, "reg": reg, "edges": edges, "train": tr, "test": te, "p": p}


# ====================================================================== comparisons (report only)
def xgb_compare(ds: dict, ex: dict, log=print) -> dict | None:
    """The original's exact XGBoost settings on the same days: on these scale-free inputs and on its own."""
    try:
        import xgboost as xgb
    except ImportError:
        return None
    tr, te = ex["train"], ex["test"]
    y = ds["y"]
    out = {}
    for name, X in (("this port's inputs", ds["X"]), ("original unscaled inputs", ds["orig"])):
        ok_tr, ok_te = tr & ~np.isnan(X).any(1), te & ~np.isnan(X).any(1)
        if ok_tr.sum() < 30 or ok_te.sum() < 10:
            continue
        m = xgb.XGBClassifier(**XGB_THEIRS)
        m.fit(X[ok_tr], y[ok_tr])
        p = np.clip(m.predict_proba(X[ok_te])[:, 1], qt.P_LO, qt.P_HI)
        out[name] = qt.binary_metrics(p, y[ok_te], int(ok_te.sum()), day=ds["day"][ok_te])
    return out


def reference_models(repo: str, sb, days, y, log=print) -> list:
    """Their shipped Hypothesis A classifiers on our held-out days, fed their own (unscaled) inputs."""
    try:
        import joblib
        import xgboost as xgb
    except ImportError:
        return [{"model": "-", "note": "needs xgboost and joblib (pip install xgboost joblib); skipped"}]
    files = sorted(glob.glob(os.path.join(os.path.expanduser(repo), "models", "xgb_classifier_hyp_a_*.joblib")))
    files = [f for f in files if not f.endswith("_model_columns.joblib")]
    if not files:
        return [{"model": "-", "note": f"no models/xgb_classifier_hyp_a_*.joblib under {repo}"}]
    rows = []
    for f in files:
        name = os.path.basename(f)
        try:
            m = joblib.load(f)
            bst = m.get_booster() if hasattr(m, "get_booster") else m
            names = list(getattr(m, "feature_names_in_", None) or bst.feature_names or [])
        except Exception as e:                                         # pickles from other versions
            rows.append({"model": name, "note": f"can't load ({type(e).__name__}: {str(e)[:80]})"})
            continue
        if not names or not set(names) <= set(qs.ORIGINAL):
            rows.append({"model": name, "note": f"needs inputs this port doesn't compute ({len(names)} columns)"})
            continue
        X, keep = [], []
        for k, d in enumerate(days):
            o = sb.original(int(d), "A")
            if o is not None:
                X.append([o[n] for n in names])
                keep.append(k)
        if len(keep) < 10:
            rows.append({"model": name, "note": "too few held-out days with their inputs"})
            continue
        try:
            p = bst.predict(xgb.DMatrix(np.array(X, float), feature_names=names))
        except Exception as e:
            rows.append({"model": name, "note": f"predict failed ({type(e).__name__}: {str(e)[:80]})"})
            continue
        p = np.clip(np.asarray(p, float), qt.P_LO, qt.P_HI)
        rows.append({"model": name, **qt.binary_metrics(p, y[np.array(keep)], len(keep))})
    return rows


# ====================================================================== report
def report(key: str, ent: dict, xgbr: dict | None, log=print) -> None:
    name, hyp = qs.SESSIONS[key][0], qs.SESSIONS[key][1]
    met = ent["metrics"]
    log(f"\nHypothesis {hyp}: {name}. Held-out {ent['test_period']} ({met['n_test']} sessions; trained "
        f"{ent['trained_period']}, {met['n_train']})")
    log(f"  direction right   {met['accuracy']:.1%}  (95% range {met['acc_low95']:.1%}-{met['acc_high95']:.1%}; coin "
        f"50% +/- {met['coin_band']:.1%}; up share {met['up_share']:.1%}; always-the-trend {met.get('drift_accuracy', 0):.1%})")
    ci = met.get("brier_skill_ci")
    log(f"  Brier skill       {met['brier_skill']:+.4f}" + (f" (95% by days {ci[0]:+.4f} to {ci[1]:+.4f})" if ci else "")
        + f";  log-loss {met['logloss']:.4f} vs {met['logloss_coin']:.4f};  AUC {met['auc']}")
    log(f"  return regression correlation {met['reg_corr']}, sign right {met['reg_hit']:.1%}")
    for lab, k in (("every session", "trade_all"), (f"|p-0.5| >= {qt.EDGE}", "trade_edge"), ("always long", "buy_hold")):
        r = met[k]
        if r.get("trades"):
            log(f"  trade {lab:<16} {r['trades']:>4} sessions, hit {r['hit']:.1%}, mean {r['mean_pct']:+.3f}%, total "
                f"{r['total_pct']:+.2f}%, Sharpe {r['sharpe']}")
    for r in met["years"]:
        log(f"    {r['group']}  n {r['n']:>4}  right {r['accuracy']:.1%}  Brier skill {r['brier_skill']:+.4f}  up {r['up_share']:.1%}")
    if met["importance"]:
        log("  inputs used: " + ", ".join(f"{r['feature']} {r['gain']:.0%}" for r in met["importance"][:8]))
    else:
        log("  the direction model kept no trees (nothing beat the base rate on the walk-forward folds)")
    if xgbr:
        for nm, m in xgbr.items():
            log(f"  original XGBoost settings, {nm}: right {m['accuracy']:.1%} ({m['acc_low95']:.1%}-{m['acc_high95']:.1%}), "
                f"Brier skill {m['brier_skill']:+.4f}, AUC {m['auc']}")
    log("  " + qt.verdict(met, ent["beats_coin"], f"the {name} model", unit="sessions", period="held-out days",
                          no_edge_tail=" The page shows it as a coin flip.").replace("Verdict: ", "-> "))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="?", help="gold M1 or H1 CSV (UTC), e.g. from fetch_history.py")
    ap.add_argument("--silver", help="silver M1 or H1 CSV, same clock")
    ap.add_argument("--mt5", nargs="?", const="auto", help="H1 history (and silver) from the running Gold Desk MT5 "
                                                           "bridge (optional: its folder)")
    ap.add_argument("--mt5-bars", type=int, default=50000, help="H1 candles to ask MT5 for")
    ap.add_argument("--utc-offset", type=float, help="MT5 server clock offset in hours, if not New York + 7")
    ap.add_argument("--split", help="YYYY-MM-DD: train before, judge from this day on (default: the last 20%%)")
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--max-trees", type=int, default=300)
    ap.add_argument("--ref-repo", help="path of a Quantitative-XAUUSD-Strategy clone: also score its pickled XGB "
                                       "models on the held-out days (report only; needs xgboost + joblib)")
    ap.add_argument("--no-xgb", action="store_true", help="skip the XGBoost comparison even if xgboost is installed")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(SESSIONS_FILE))
    a = ap.parse_args(argv)
    qt.need_numpy()
    t0 = time.time()
    if a.mt5:
        h1, sv, what = qt.load_mt5("H1", a.mt5_bars, None if a.mt5 == "auto" else a.mt5, a.utc_offset)
        if a.silver:
            sv = to_h1(qt.load_csv(a.silver), a.silver)
    else:
        if not a.csv:
            ap.error("give a gold M1 / H1 CSV or --mt5")
        raw = qt.load_csv(a.csv)
        h1 = to_h1(raw, a.csv)
        sv = to_h1(qt.load_csv(a.silver), a.silver) if a.silver else None
        what = f"{a.csv}: {len(raw)} {'M1' if raw.sec == 60 else 'H1'} candles -> {len(h1)} H1"
    print(f"{what}, {qt.utc_day(h1.t[0])} to {qt.utc_day(h1.t[-1])}" + (f"; silver {len(sv)} H1" if sv else ""))
    ds = build(h1, sv)
    out = {"version": 1, "model": MODEL_NAME, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "credit": CREDIT, "data": what, "inputs": {"silver": sv is not None}, "sessions": {},
           "note": ("Inputs from H1 candles closed at the target session's start, on real UTC with Europe/London "
                    "daylight saving; held-out days judged once; beats_coin = Brier skill > 0 and accuracy's lower "
                    "95% bound > 50%.")}
    sb = ds.pop("_builder")
    for key, (name, hyp, *_) in qs.SESSIONS.items():
        d = ds[hyp]
        if len(d["y"]) < 80:
            print(f"\nHypothesis {hyp} ({name}): only {len(d['y'])} sessions; skipped")
            continue
        split_day = (qt.parse_day(a.split) // qs.DAY) if a.split else int(d["day"][int(len(d["day"]) * 0.8)])
        print(f"\nHypothesis {hyp} ({name}): walk-forward CV")
        ent, ex = train_hyp(d, split_day, folds=a.folds, max_trees=a.max_trees, seed=a.seed)
        ent["hypothesis"], ent["session"] = hyp, name
        xg = None if a.no_xgb else xgb_compare(d, ex)
        if xg:
            ent["metrics"]["xgb_original_settings"] = xg
        if hyp == "A" and a.ref_repo:
            refs = reference_models(a.ref_repo, sb, d["day"][ex["test"]], d["y"][ex["test"]])
            ent["metrics"]["reference_models"] = refs
        out["sessions"][key] = ent
        report(key, ent, xg)
        if hyp == "A" and a.ref_repo:
            print("  their shipped models on these held-out days (report only, never used live: other broker, "
                  "server time read as UTC, $-scaled inputs from 2015-2025 prices):")
            for r in ent["metrics"]["reference_models"]:
                if "accuracy" in r:
                    print(f"    {r['model']}: right {r['accuracy']:.1%} ({r['acc_low95']:.1%}-{r['acc_high95']:.1%}) "
                          f"on {r['n']} days, Brier skill {r['brier_skill']:+.4f}, AUC {r['auc']}")
                else:
                    print(f"    {r['model']}: {r['note']}")
    if not out["sessions"]:
        raise SystemExit("No session model trained: not enough history.")
    p = qt.write_model(out, a.out)
    good = [qs.SESSIONS[k][0] for k, e in out["sessions"].items() if e["beats_coin"]]
    print(f"\nWrote {p} in {time.time() - t0:.0f} s. The live page shows each session's forecast with this record.")
    print("Verdict: " + (f"{', '.join(good)} beat a coin on the held-out days (small samples: watch the live record)."
                         if good else "no session model beat a coin on the held-out days; the page shows them as "
                                      "coin flips."))


if __name__ == "__main__":
    main()
