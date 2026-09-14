"""THE LAB -- the bot invents its own strategies.

A sharp daytrader does not search randomly: he has hypotheses.  The lab
thinks the same way.  Every "idea" is a tradeable hypothesis assembled from
parts the brain already owns -- entry models, the VP touch window, the
take-profit legs, the trailing runner, session hours, cooldown, volatility
appetite.  Ideas are bred like a trading desk's playbook:

    * the champion (the live config) is the parent;
    * mutations flip a few parts at a time;
    * the best scorers cross like two traders comparing notes;
    * the brain's own memory (the lesson journal) votes AGAINST anything
      that leans on hours or coins it already lost money on.

The loop the operator asked for runs for real:

    strategy -> backtest -> measure win rate -> keep winners -> mutate again

but a genius trader also knows the oldest trap in the book: a loop that
tunes until the win rate is "unbelievable" on the SAME history is just
memorising the past.  So the lab splits time:

    * IN-SAMPLE  -- evolution happens here (first 2/3 of the window);
    * OUT-OF-SAMPLE -- the finalists are re-measured on the last 1/3,
      history none of them ever saw.  The number we believe is the
      out-of-sample one.

The universe is a smart variety of coins, not one favourite: majors, mids
and micro-caps, each calm/normal/wild by its own ATR.  The lab reports
WHICH KIND of coin each idea wins on -- "trade more micro-caps", or
"leave the majors alone" -- straight from the trades.

Nothing touches the live book unless it clears every guardrail AND beats
the champion out-of-sample, and even then adoption is gated by config
(inventor.adopt, off by default).  The lab proposes; the operator
disposes.
"""
from __future__ import annotations

import copy
import json
import random
import time
from pathlib import Path
from typing import Any

from config.loader import Config, ConfigError

# The brain's parts and the values it may try.
PARTS: dict[str, dict] = {
    "strategy.entry_models": {"kind": "subset",
                              "values": ["order_block", "fvg", "micro_poc"]},
    "strategies.enabled": {"kind": "subset",
                            "values": ["vp", "breakout", "turtle"]},
    "strategy.vp_touch_window_bars": {"kind": "choice",
                                      "values": [1, 3, 6, 12, 16, 24]},
    "tp.model": {"kind": "choice", "values": ["A", "B", "C", "D"]},
    "tp.fixed_r": {"kind": "choice", "values": [1.5, 2.0, 2.5, 3.0, 4.0]},
    "tp.partial.runner_exit": {"kind": "choice",
                               "values": ["trail", "bos"]},
    "tp.structure.runner_exit": {"kind": "choice",
                                 "values": ["trail", "bos"]},
    "tp.structure.first_frac": {"kind": "choice",
                                "values": [0.5, 0.75]},
    "tp.structure.runner_timeout_bars": {"kind": "choice",
                                         "values": [0, 30, 60, 120]},
    "tp.partial.runner_timeout_bars": {"kind": "choice",
                                       "values": [0, 30, 60, 120]},
    "trailing.breakeven_after_tp1": {"kind": "choice",
                                     "values": [True, False]},
    "trailing.trail_offset_atr_mult": {"kind": "choice",
                                       "values": [0.05, 0.1, 0.15, 0.2]},
    "strategy.session.trade_windows": {"kind": "choice",
                                       "values": [["any"], ["prime"],
                                                  ["london_open", "prime"],
                                                  ["ny", "prime"]]},
    "daily.cooldown_minutes": {"kind": "choice", "values": [0, 15, 30]},
    "volatility_filter.enabled": {"kind": "choice",
                                  "values": [True, False]},
    "volatility_filter.atr_zscore_max": {"kind": "choice",
                                         "values": [3.0, 4.0, 5.0]},
    "risk.risk_per_trade": {"kind": "choice",
                            "values": [0.15, 0.25, 0.35, 0.5]},
    "strategy.ob_max_age_bars": {"kind": "choice",
                                 "values": [15, 30, 45]},
    "strategy.fvg_max_age_bars": {"kind": "choice",
                                  "values": [10, 20, 30]},
    # the micro-cap lesson: tiny stops are eaten alive by fees.  The lab can
    # widen stops, demand bigger/liquid coins, or tighten the fee bar.
    "stop.atr_buffer_mult": {"kind": "choice",
                             "values": [0.25, 0.5, 1.0]},
    "stop.max_sl_atr_mult": {"kind": "choice",
                             "values": [2.0, 3.0, 4.0]},
    "stop.min_sl_atr_mult": {"kind": "choice",
                             "values": [0.0, 0.25, 0.5, 0.75]},
    "strategy.cvd_filter.enabled": {"kind": "choice",
                                    "values": [True, False]},
    # knowledge-store pieces (ICT / patterns repo / web research): the lab
    # itself decides which of these actually win
    "strategy.cycle_filter.enabled": {"kind": "choice",
                                      "values": [True, False]},
    "strategy.cycle_filter.max_phase": {"kind": "choice",
                                        "values": [0.25, 0.4, 0.6]},
    "strategy.cycle_filter.atr_expand_min": {"kind": "choice",
                                             "values": [0.0, 0.5, 0.8, 1.2]},
    "strategy.require_displacement": {"kind": "choice",
                                      "values": [True, False]},
    "strategy.pattern_confirm.enabled": {"kind": "choice",
                                         "values": [True, False]},
    "universe.min_atr_pct": {"kind": "choice",
                             "values": [0.15, 0.3, 0.5]},
    "universe.min_volume_usdt_24h": {"kind": "choice",
                                     "values": [2000000, 10000000,
                                                50000000]},
    "universe.top_n": {"kind": "choice",
                       "values": [8, 15, 25]},
    "execution.max_fee_r": {"kind": "choice",
                            "values": [0.75, 1.0, 1.5, 2.0]},
}


# ---------------------------------------------------------------- plumbing
def _leaf(d: dict, path: str) -> Any:
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(path)
        cur = cur[part]
    return cur


def _set_leaf(d: dict, path: str, val: Any) -> None:
    parts = path.split(".")
    cur = d
    for part in parts[:-1]:
        cur = cur[part]
    cur[parts[-1]] = copy.deepcopy(val)


# the lab knobs the brain can steer: part -> brain features it influences
PART_TO_FEATURES: dict[str, list[str]] = {
    "strategy.entry_models": ["entry_model"],
    "strategy.vp_touch_window_bars": ["stop_dist_pct", "plan_r"],
    "tp.model": ["plan_r"],
    "tp.fixed_r": ["plan_r"],
    "tp.structure.first_frac": ["plan_r", "avg_r"],
    "tp.partial.runner_exit": ["avg_r"],
    "tp.structure.runner_exit": ["avg_r"],
    "trailing.breakeven_after_tp1": ["stop_dist_pct"],
    "trailing.trail_offset_atr_mult": ["stop_dist_pct", "avg_r"],
    "strategy.session.trade_windows": ["hour_utc", "session"],
    "daily.cooldown_minutes": ["dow"],
    "volatility_filter.enabled": ["atr_regime_pct"],
    "volatility_filter.atr_zscore_max": ["atr_regime_pct"],
    "risk.risk_per_trade": ["leverage"],
    "strategy.ob_max_age_bars": ["entry_model"],
    "strategy.fvg_max_age_bars": ["entry_model"],
    "stop.atr_buffer_mult": ["stop_dist_pct", "avg_r"],
    "stop.max_sl_atr_mult": ["stop_dist_pct"],
    "stop.min_sl_atr_mult": ["stop_dist_pct", "avg_r"],
    "strategy.cvd_filter.enabled": ["vol_z", "trend_ema"],
    "strategy.cycle_filter.enabled": ["atr_regime_pct", "range_pos"],
    "strategy.cycle_filter.max_phase": ["range_pos", "hour_utc"],
    "strategy.cycle_filter.atr_expand_min": ["atr_regime_pct"],
    "strategy.require_displacement": ["mom5_r", "trend_ema"],
    "strategy.pattern_confirm.enabled": ["mom5_r"],
    "universe.min_atr_pct": ["kind", "stop_dist_pct"],
    "universe.min_volume_usdt_24h": ["kind"],
    "universe.top_n": ["kind"],
    "execution.max_fee_r": ["stop_dist_pct", "avg_r"],
}


def _part_weights(importances: dict | None) -> dict[str, float]:
    """The brain's learned importances steer the search: parts that touch
    high-importance features get mutated more often (gradient-directed
    evolution instead of blind shuffling)."""
    if not importances:
        return {k: 1.0 for k in PARTS}
    w = {}
    for part, feats in PART_TO_FEATURES.items():
        s = sum(max(float(importances.get(f, 0.0)), 0.0) for f in feats)
        w[part] = 0.15 + s          # base rate + learned gradient
    return w


def _weighted_sample(rng: random.Random, keys: list[str],
                     weights: dict[str, float], k: int) -> list[str]:
    keys = list(keys)
    out = []
    for _ in range(min(k, len(keys))):
        ws = [weights.get(x, 1.0) for x in keys]
        tot = sum(ws)
        r = rng.random() * tot
        acc = 0.0
        for i, x in enumerate(keys):
            acc += ws[i]
            if r <= acc:
                out.append(keys.pop(i))
                break
    return out


def mutate(base: dict, rng: random.Random, n_parts: int = 3,
           weights: dict[str, float] | None = None) -> tuple[dict, list[str]]:
    """Flip a few parts of the brain for one idea.  Returns (candidate,
    human-readable changes)."""
    cand = copy.deepcopy(base)
    keys = list(PARTS)
    picked = _weighted_sample(rng, keys,
                              weights or {k: 1.0 for k in keys},
                              min(n_parts, len(keys)))
    changes: list[str] = []
    for path in picked:
        spec = PARTS[path]
        if spec["kind"] == "subset":
            old = list(_leaf(cand, path))
            pool = spec["values"]
            if len(old) > 1 and rng.random() < 0.5:
                new = rng.sample(pool, k=len(old) - 1)
            else:
                new = rng.sample(pool, k=rng.randint(1, 3))
            new = sorted(set(new))
            if new and new != sorted(old):
                _set_leaf(cand, path, new)
                changes.append(f"{path}: {old} -> {new}")
        else:
            old = _leaf(cand, path)
            options = [v for v in spec["values"] if v != old]
            if not options:
                continue
            new = rng.choice(options)
            _set_leaf(cand, path, new)
            changes.append(f"{path}: {old} -> {new}")
        # paired fraction: structure lots must sum to 1.0
        if path == "tp.structure.first_frac":
            other = 1.0 - float(_leaf(cand, "tp.structure.first_frac"))
            _set_leaf(cand, "tp.structure.runner_frac", round(other, 2))
            changes.append("tp.structure.runner_frac: adjusted to "
                           f"{round(other, 2)}")
    return cand, changes


def cross(a: dict, b: dict, rng: random.Random) -> dict:
    """Two ideas compare notes: half the parts come from each parent."""
    cand = copy.deepcopy(a)
    for path in PARTS:
        if rng.random() < 0.5:
            try:
                _set_leaf(cand, path, copy.deepcopy(_leaf(b, path)))
            except KeyError:
                pass
    # re-pair the structure fractions: the first/runner pair must sum to 1
    try:
        first = float(_leaf(cand, "tp.structure.first_frac"))
        _set_leaf(cand, "tp.structure.runner_frac", round(1.0 - first, 2))
    except Exception:
        pass
    return cand


def dict_fingerprint(d: dict) -> str:
    return json.dumps(d, sort_keys=True, default=str)


# ---------------------------------------------------------------- memory
def brain_votes(lessons_path: Path, trades: list[dict]) -> tuple[float, dict]:
    """The lesson journal votes against ideas that lean on the hours or the
    coins the bot already lost money on.  Returns (penalty, evidence)."""
    if not lessons_path.exists():
        return 0.0, {}
    hours: dict[int, list[float]] = {}
    syms: dict[str, list[float]] = {}
    try:
        for line in lessons_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            ts = rec.get("ts_ms")
            pnl = float(rec.get("pnl") or 0.0)
            if not ts:
                continue
            h = int((ts // 3_600_000) % 24)
            hours.setdefault(h, []).append(pnl)
            s = rec.get("symbol")
            if s:
                syms.setdefault(s, []).append(pnl)
    except Exception:
        return 0.0, {}
    bad_hours = {h for h, pnls in hours.items()
                 if len(pnls) >= 2 and sum(pnls) < 0}
    bad_syms = {s for s, pnls in syms.items()
                if len(pnls) >= 2 and sum(pnls) < 0}
    if not bad_hours and not bad_syms:
        return 0.0, {}
    n_h = n_s = 0.0
    for t in trades:
        ts = t.get("ts_ms") or 0
        if int((ts // 3_600_000) % 24) in bad_hours:
            n_h += 1
        if t.get("symbol") in bad_syms:
            n_s += 1
    n = max(len(trades), 1)
    penalty = 0.05 * (n_h / n) + 0.05 * (n_s / n)
    evidence = {"bad_hours": sorted(bad_hours),
                "bad_symbols": sorted(bad_syms)[:8]}
    return min(penalty, 0.10), evidence


# ---------------------------------------------------------------- scoring
def stats_from_trades(trades: list[dict],
                      equity_curve: list | None = None) -> dict:
    n = len(trades)
    if n == 0:
        return {"n": 0, "wins": 0, "wr": 0.0, "pf": 0.0, "avg_r": 0.0,
                "max_dd_pct": 100.0, "gross_win": 0.0, "gross_loss": 0.0,
                "score": 0.0}
    wins = sum(1 for t in trades if (t.get("pnl") or 0) > 0)
    gw = sum(t.get("pnl") or 0.0 for t in trades if (t.get("pnl") or 0) > 0)
    gl = sum(-(t.get("pnl") or 0.0) for t in trades if (t.get("pnl") or 0) < 0)
    rs = [t.get("pnl_r") or 0.0 for t in trades]
    avg_r = sum(rs) / n
    wr = wins / n
    pf = gw / gl if gl > 0 else (gw / 1e-9)
    max_dd_pct = 0.0
    if equity_curve:
        peak = None
        for _ms, eq in equity_curve:
            if peak is None or eq > peak:
                peak = eq
            elif peak > 0:
                max_dd_pct = max(max_dd_pct, (peak - eq) / peak)
        max_dd_pct *= 100.0
    score = (0.30 * min(wr, 1.0)
             + 0.25 * min(avg_r / 0.5, 1.0)
             + 0.25 * min(pf / 2.0, 1.0)
             + 0.10 * min(n / 30.0, 1.0)
             + 0.10 * (1.0 - min(max_dd_pct / 50.0, 1.0)))
    return {"n": n, "wins": wins, "wr": round(wr, 4), "pf": round(pf, 3),
            "avg_r": round(avg_r, 4), "max_dd_pct": round(max_dd_pct, 2),
            "gross_win": round(gw, 2), "gross_loss": round(gl, 2),
            "score": round(score, 4)}


def adoptable(stats: dict, inv_cfg: dict) -> tuple[bool, str]:
    """Guardrails: a lab idea only leaves the lab with real numbers."""
    if stats["n"] < inv_cfg.get("min_trades", 8):
        return False, f"only {stats['n']} trades (need {inv_cfg.get('min_trades', 8)})"
    if stats["wr"] < inv_cfg.get("min_wr", 0.55):
        return False, f"win rate {stats['wr']:.0%} below bar"
    if stats["avg_r"] < inv_cfg.get("min_avg_r", 0.15):
        return False, f"expectancy {stats['avg_r']:.2f}R below bar"
    if stats["pf"] < inv_cfg.get("min_pf", 1.3):
        return False, f"profit factor {stats['pf']:.2f} below bar"
    if stats["max_dd_pct"] > inv_cfg.get("max_dd_pct", 25.0):
        return False, f"drawdown {stats['max_dd_pct']:.0f}% too deep"
    return True, ""


# ---------------------------------------------------------------- coins
def coin_kind(atr_pct: float, vol24h: float) -> str:
    if vol24h >= 200_000_000:
        size = "major"
    elif vol24h >= 20_000_000:
        size = "mid"
    else:
        size = "micro"
    if atr_pct >= 4.0:
        vola = "wild"
    elif atr_pct >= 1.0:
        vola = "normal"
    else:
        vola = "calm"
    return f"{size}-{vola}"


def coin_meta_from_frame(sym: str, df) -> dict:
    tail = df.tail(1440)
    px = float(tail["close"].iloc[-1]) if len(tail) else 0.0
    if px <= 0:
        return {"symbol": sym, "kind": "?", "atr_pct": 0.0,
                "vol24h": 0.0, "price": 0.0}
    rng_ = (tail["high"] - tail["low"]) / tail["close"].replace(0, float("nan"))
    atr_pct = float(rng_.mean() * 100.0) if len(rng_) else 0.0
    vol24h = float(tail["volume"].sum()) if len(tail) else 0.0
    return {"symbol": sym, "kind": coin_kind(atr_pct, vol24h),
            "atr_pct": round(atr_pct, 2), "vol24h": round(vol24h),
            "price": px}


def coin_insights(trades: list[dict], metas: dict[str, dict]) -> dict:
    """What the trades teach about WHICH kind of coin to trade more."""
    by_kind: dict[str, dict] = {}
    for t in trades:
        m = metas.get(t.get("symbol") or "", {})
        k = m.get("kind") or "?"
        b = by_kind.setdefault(k, {"n": 0, "wins": 0, "pnl": 0.0,
                                   "rs": [], "symbols": set()})
        b["n"] += 1
        if (t.get("pnl") or 0) > 0:
            b["wins"] += 1
        b["pnl"] += t.get("pnl") or 0.0
        if t.get("pnl_r") is not None:
            b["rs"].append(t["pnl_r"])
        b["symbols"].add(t.get("symbol") or "")
    rows = []
    for k, b in sorted(by_kind.items(), key=lambda kv: -kv[1]["n"]):
        n = b["n"]
        wr = b["wins"] / n if n else 0.0
        avg_r = sum(b["rs"]) / len(b["rs"]) if b["rs"] else 0.0
        if n >= 6 and wr >= 0.55 and avg_r > 0:
            call = "trade MORE"
        elif n >= 6 and (wr < 0.45 or avg_r < 0):
            call = "trade LESS"
        else:
            call = "as-is"
        rows.append({"kind": k, "n": n, "wr": round(wr, 4),
                     "avg_r": round(avg_r, 4), "pnl": round(b["pnl"], 2),
                     "call": call, "symbols": sorted(b["symbols"])[:6]})
    return {"by_kind": rows,
            "recommendation": "; ".join(
                f"{r['kind']} {r['call']} ({r['n']}t wr {r['wr']:.0%})"
                for r in rows if r["call"] != "as-is") or
            "not enough trades per kind yet"}


# ---------------------------------------------------------------- engine
# warmup_15m_bars: 500 -> the engine needs 500 x 15m bars (~5.2 days) of
# history before its indicators are warm enough to trade.  The engine runs
# from (start - warmup) so the book is already trading when the measurement
# window begins; only trades that CLOSE inside the window are measured.
WARMUP_MS = 6 * 86_400_000


def _run_one(cfg_dict: dict, frames: dict, start_ms: int, end_ms: int,
             lessons_path: Path) -> dict:
    """strategy -> backtest -> measure.  The heart of the loop."""
    try:
        cfg = Config(cfg_dict)
    except ConfigError as e:
        return {"ok": False, "why": f"invalid idea: {e}"}
    from engine import BacktestEngine
    eng = BacktestEngine(cfg)
    try:
        lo = start_ms - WARMUP_MS
        sds = eng.prepare(frames, lo, end_ms)
        if not sds:
            return {"ok": False, "why": "no symbol had enough history"}
        res = eng.run(sds, lo, end_ms)
    except Exception as e:
        return {"ok": False, "why": f"backtest failed: {e}"}
    trades = [t for t in res.trades if not t.get("phantom")
              and start_ms <= (t.get("ts_ms") or 0) <= end_ms]
    stats = stats_from_trades(trades, res.equity_curve)
    penalty, evidence = brain_votes(lessons_path, trades)
    stats["score"] = round(max(0.0, stats["score"] - penalty), 4)
    stats["brain_penalty"] = round(penalty, 4)
    return {"ok": True, "stats": stats, "brain": evidence, "trades": trades}


def invent(data_dir: Path, frames: dict, inv_cfg: dict | None = None,
           extra_file: str = "config/aggressive.yaml",
           end_ms: int | None = None, log=print) -> dict:
    """Run the strategy->backtest->measure->mutate loop.

    Evolution runs on the IN-SAMPLE window (first 2/3); the finalists are
    then re-measured OUT-OF-SAMPLE (last 1/3) -- history they never saw.
    Returns the full report; writes nothing itself."""
    from config.loader import load_config
    t0 = time.time()
    ic = inv_cfg or {}
    rng = random.Random(int(time.strftime("%Y%m%d", time.gmtime())))
    cfg = load_config(extra_file=extra_file)
    champion = cfg.raw()
    days = int(ic.get("backtest_days", 3))
    if end_ms is None:
        end_ms = int(time.time() * 1000) // 60_000 * 60_000
    start_ms = end_ms - days * 86_400_000
    split_ms = start_ms + int((end_ms - start_ms) * (2.0 / 3.0))
    lessons_path = data_dir / "state" / "lessons.jsonl"
    # gradient-directed evolution: the brain's learned feature importances
    # steer which parts the lab mutates first
    weights = None
    try:
        bm = json.loads((data_dir / "state" / "brain_meta.json").read_text())
        imps = bm.get("importances") or {}
        if imps:
            weights = _part_weights(imps)
            log("lab: brain steering %d parts (%d importances)",
                len(weights), len(imps))
    except Exception:
        pass
    # winner-genome mining: every trade of every idea lands in the pool so
    # the next brain sees the whole search, not just the champion
    pool_path = data_dir / "state" / "lab_pool.jsonl"

    metas = {s: coin_meta_from_frame(s, df) for s, df in frames.items()}
    log(f"lab: {len(frames)} symbols over {days}d "
        f"(in-sample {start_ms}..{split_ms}, out-of-sample {split_ms}..{end_ms})")

    def eval_one(cfg_dict: dict, label: str, start: int, end: int,
                 parent: str = "", changes: list | None = None) -> dict:
        r = _run_one(cfg_dict, frames, start, end, lessons_path)
        rec = {"name": label, "parent": parent,
               "changes": changes or [], "as_of_ms": end_ms}
        if r and r.get("ok"):
            rec.update(r["stats"])
            rec["brain"] = r.get("brain", {})
            rec["coins"] = coin_insights(r["trades"], metas)
            try:
                with open(pool_path, "a") as pf:
                    for t in r["trades"]:
                        pf.write(json.dumps({
                            "idea": label, "ts_ms": t.get("ts_ms"),
                            "opened_ms": t.get("opened_ms"),
                            "symbol": t.get("symbol"),
                            "direction": t.get("direction"),
                            "lot": t.get("lot"),
                            "strategy": t.get("strategy"),
                            "entry_model": t.get("entry_model"),
                            "entry": t.get("entry"), "stop": t.get("stop"),
                            "tp": t.get("tp"), "exit": t.get("exit"),
                            "pnl": t.get("pnl"), "pnl_r": t.get("pnl_r"),
                            "atr1m": t.get("atr1m"),
                            "leverage": t.get("leverage"),
                            "exit_reason": t.get("exit_reason"),
                        }, default=str) + "\n")
            except Exception:
                pass
        else:
            rec.update({"n": 0, "wr": 0.0, "pf": 0.0, "avg_r": 0.0,
                        "max_dd_pct": 0.0, "score": 0.0,
                        "why": (r or {}).get("why", "no result")})
        return rec

    report: list[dict] = []
    champ = eval_one(champion, "champion (live config)", split_ms, end_ms)
    champ["is_champion"] = True
    report.append(champ)
    log(f"lab: champion OOS n={champ.get('n')} wr={champ.get('wr', 0):.0%} "
        f"avgR={champ.get('avg_r', 0):+.2f} pf={champ.get('pf', 0):.2f} "
        f"score={champ.get('score', 0):.3f}")

    pop = int(ic.get("population", 5))
    gens = int(ic.get("generations", 2))
    elite: list[tuple[float, dict, str]] = []   # (score, config, name)
    seen = {dict_fingerprint(champion)}
    leaders: list[dict] = []

    for gen in range(gens):
        batch: list[tuple[dict, dict, str]] = []   # (rec, cfg, label)
        for i in range(pop):
            if elite and rng.random() < 0.4:
                p_score, p_cfg, p_name = rng.choice(elite)
                if rng.random() < 0.5 and len(elite) >= 2:
                    q = rng.choice(elite)
                    cand = cross(p_cfg, q[1], rng)
                    tag = f"gen{gen + 1}-{i + 1} (cross of {p_name})"
                else:
                    cand, changes = mutate(p_cfg, rng, weights=weights)
                    tag = f"gen{gen + 1}-{i + 1} (child of {p_name})"
                parent = p_name
            else:
                cand, changes = mutate(champion, rng, weights=weights)
                tag = f"gen{gen + 1}-{i + 1}"
                parent = "champion"
            fp = dict_fingerprint(cand)
            if fp in seen:
                continue
            seen.add(fp)
            rec = eval_one(cand, tag, start_ms, split_ms,
                           parent=parent, changes=changes)
            rec["_cfg"] = cand
            batch.append((rec, cand, tag))
            log(f"lab: {tag} IS n={rec.get('n')} wr={rec.get('wr', 0):.0%} "
                f"avgR={rec.get('avg_r', 0):+.2f} pf={rec.get('pf', 0):.2f} "
                f"score={rec.get('score', 0):.3f}"
                + (f" [{rec.get('why')}]" if rec.get("why") else ""))
        # keep the best two ideas of the generation in the breeding pool
        batch.sort(key=lambda x: -(x[0].get("score") or 0.0))
        for rec, cand, tag in batch[:2]:
            if rec.get("why") or rec.get("n", 0) == 0:
                continue
            elite.append((rec["score"], cand, tag))
        for rec, _cand, _tag in batch:
            report.append({k: v for k, v in rec.items() if k != "_cfg"})
            leaders.append(rec)

    # ---- out-of-sample: the finalists face history they never saw ----
    ok = [r for r in leaders if not r.get("why") and r.get("n", 0) > 0]
    ok.sort(key=lambda r: -(r.get("score") or 0.0))
    finalists = ok[:3]
    oos_rows = []
    for r in finalists:
        cfg_dict = r["_cfg"]
        name = r["name"]
        rec2 = eval_one(cfg_dict, name + " OOS", split_ms, end_ms,
                        parent=r.get("parent"), changes=r.get("changes"))
        rec2["in_sample"] = {k: r[k] for k in ("n", "wr", "pf", "avg_r",
                                                "max_dd_pct", "score")}
        rec2["_cfg"] = cfg_dict
        oos_rows.append(rec2)
        log(f"lab: {name} OOS n={rec2.get('n')} wr={rec2.get('wr', 0):.0%} "
            f"avgR={rec2.get('avg_r', 0):+.2f} pf={rec2.get('pf', 0):.2f} "
            f"score={rec2.get('score', 0):.3f}")

    oos_rows.sort(key=lambda r: -(r.get("score") or 0.0))
    best = oos_rows[0] if oos_rows else champ
    # multiple-testing guardrail (walk-forward discipline): the lab tried
    # many ideas, so the best score must clear the noise of the whole pack
    pack = [r.get("score") or 0.0 for r in report
            if not r.get("why") and r.get("n", 0) > 0]
    best_z = 0.0
    if len(pack) >= 3:
        mean = sum(pack) / len(pack)
        var = sum((s - mean) ** 2 for s in pack) / max(len(pack) - 1, 1)
        std = var ** 0.5
        if std > 0:
            best_z = round(((best.get("score") or 0.0) - mean) / std, 2)
    adopt = None
    if best and not best.get("is_champion"):
        can, why = adoptable(best, ic)
        beat = (best.get("score") or 0.0) > (champ.get("score") or 0.0) * (
            1.0 + ic.get("adopt_margin", 0.10))
        if can and beat:
            adopt = {"name": best["name"],
                     "changes": best.get("changes", []),
                     "stats": {k: best[k] for k in
                               ("n", "wr", "pf", "avg_r", "max_dd_pct",
                                "score")},
                     "config": best["_cfg"],
                     "note": "cleared every guardrail out-of-sample and beat "
                             "the champion -- waiting for inventor.adopt"}
        elif not can:
            log(f"lab: best idea NOT adoptable: {why}")
    for r in oos_rows:
        report.append({k: v for k, v in r.items()
                       if k not in ("_cfg", "config")})
    return {"ok": True, "seconds": round(time.time() - t0, 1),
            "champion": champ, "best": {k: v for k, v in best.items()
                                        if k != "_cfg"} if best else None,
            "inventions": [r for r in oos_rows
                           if not r.get("is_champion")],
            "trials": len(report), "best_z": best_z,
            "adopt": adopt,
            "adopt_gate": bool(ic.get("adopt", False)),
            "as_of_ms": end_ms}
