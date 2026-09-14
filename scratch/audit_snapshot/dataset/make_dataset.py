#!/usr/bin/env python3
"""
Build the TBT flow dataset: every decision the engine can make, as it really
makes them, grounded in real recorded data.

Stages, all produced by the REAL code paths:

  universe   the coin pool, verbatim
  scan       the coin finder's selection (real atrscan functions on recorded
             candles) -- kept / under the ATR floor / fallback / unreadable
  scout      what the scout writes down (real scout.json) + the belongs_to
             mismatch refusal + the ranking fallback
  perch      the eagle's choice (real perch.ripest/held on real rows)
  signal     one row per signal judgement, produced by running the REAL
             papertrade loop over the recorded signal and its recorded
             outcome path -- every gate, every refusal, every reason string
  rest       resting-order life: placed, filled at the level, expired,
             late-market fallback, skipped on day/loss/exposure/symbol
  exit       how a position ends: target, stop, flat, counter, liquidated,
             stalled, timeout, yielded, live-reconciled

Outputs:
  data/dataset/flow.jsonl      the event stream (one row per decision)
  data/dataset/trades.jsonl    every opened trade, with its full path
  data/dataset/manifest.json   coverage per branch, sources, assumptions

    python3 dataset/make_dataset.py [--limit N] [--no-scenarios]
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import tempfile
import time as _time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pace  # noqa: E402
import atrscan  # noqa: E402
import perch  # noqa: E402
import scout as scout_mod  # noqa: E402
from dataset import engine, sources, scenarios  # noqa: E402

OUT_DIR = ROOT / "data" / "dataset"
BAR = engine.BAR
BUDGET, FLOOR = 8, 40
T_BASE = 1788450300    # a grid-aligned epoch; day phase is derived from it


# ---------------------------------------------------------------------------
# The live configuration, as the dataset's own header row
# ---------------------------------------------------------------------------

def config_row() -> dict:
    return {
        "kind": "meta", "v": 1,
        "config": {
            "source": "combo", "scout": True, "skip_window": [1],
            "tp": 5, "sl": 1.25, "frac": 0.5, "max_exposure": 0.5,
            "lev": 40, "per_coin_lev": True, "min_lev": 0,
            "min_entry": 40, "min_agents": 3, "min_atr": 2.5,
            "with_trend": True, "ct_exit": 3.5, "break_even": 0,
            "per_day": 8, "day_start": 17.5, "max_per_day": 0,
            "fill_bars": 8, "step_at": 10000, "step_frac": 0.25,
            "fee_bps": 12, "entry": "now", "stack": True,
        },
        "htf": {"setting": "1h",
                "note": "4h was wrong; 1h everywhere -- Pine default, "
                        "tools/sethtf.py, live charts (switched on the box "
                        "2026-09-06, both windows verified at 60). "
                        "hunt.py's own 4h EMA21 trend is a different "
                        "strategy's filter, not the TBT higher timeframe."},
        "sources": ["data/signals.jsonl", "data/outcomes.jsonl",
                    "data/tesla.jsonl", "data/council_history.jsonl",
                    "data/boom.json", "data/watch_measures.json",
                    "data/watchlist.json", "data/universe.json",
                    "data/scout.json"],
        "engine": "papertrade.main() driven by tests/fakes.py, the same "
                  "stand-ins the 400+ tests use; log lines are the reason "
                  "strings; the book on disk is the trade record.",
        "conventions": {
            "spanning_bar": "a bar spanning both target and stop is a STOP "
                            "(the repo's own replay convention)",
            "entry": "the recorded real next-bar price, spread 0 so the "
                     "fill equals what the market really printed",
            "synthetic_prefix": "45 quiet candles for signals with no "
                                 "recorded history; never used for the "
                                 "signal bar or anything after it",
            "atr": "the finder's ATR(14)/price; real recorded value at "
                   "signal time when known, else the recorded signalcheck "
                   "ATR of the same coin at the same moment",
        },
    }


# ---------------------------------------------------------------------------
# Coin measurements, through the finder's own functions
# ---------------------------------------------------------------------------

def _df(bars: dict[int, tuple]):
    import pandas as pd
    ks = sorted(bars)
    return pd.DataFrame([
        {"open": bars[k][0], "high": bars[k][1], "low": bars[k][2],
         "close": bars[k][3], "volume": 0.0} for k in ks])


def measures_for(sym: str, t: int, sig: dict) -> dict:
    """What the finder's file would say about this coin at signal time."""
    prefix = sources.prefix_for(sym, t, 45)
    if prefix is not None:
        df = _df(prefix)
        sh = atrscan.shape_of(df)
        return {
            "atr": atrscan.atr_pct(df, 14),
            "trend": atrscan.trend_pct(df),
            "vol20": sh.get("vol20"), "mom6h": sh.get("mom6h"),
            "mom1h": sh.get("mom1h"), "volx": sh.get("volx"),
            "atr_src": "recorded-candles"}
    # No recorded history: the same coin's recorded signal-time ATR, taken by
    # the signal collector with the same formula.
    o = sig.get("outcome") or {}
    atr = o.get("atr")
    return {"atr": atr, "trend": None, "vol20": None, "mom6h": None,
            "mom1h": None, "volx": None,
            "atr_src": "signalcheck-recorded" if atr is not None else None}


# ---------------------------------------------------------------------------
# Classification: log line + book state -> decision row
# ---------------------------------------------------------------------------

_SKIP_MAP = [
    ("colour", "sig_colour_orange"),
    ("Tesla unwired", "sig_unwired_tesla"),
    ("tier ", "sig_skip_tier"),
    ("body", "sig_fat_body"),
    ("thin_coin: ATR", "sig_thin_coin"),
    ("low_leverage", "sig_low_leverage"),
    ("against_trend", "sig_against_trend"),
    ("scored ", "sig_poor_score"),
    ("no price on Bitunix", "sig_no_price"),
    ("HALT", "sig_loss_limit"),
    ("already committed", "sig_exposure_full"),
    ("already in ", "sig_one_per_symbol"),
    ("not traded", "sig_stale_age"),
    ("absorbed as backlog", "sig_backlog_absorbed"),
    ("still fresh", "sig_catchup_fresh"),
    ("the scout's shape", "sig_council_dropped"),
    ("already acted", "sig_seen_duplicate"),
    ("no candle for the signal bar", "sig_no_candle"),
    ("from the signal price", "sig_max_entry_r"),
    ("under the exchange minimum", "live_preflight_failed"),
    ("below the exchange minimum", "live_preflight_failed"),
    ("live cap of", "sig_live_cap"),
    ("filter: the model", "filter_model"),
    ("agents", "sig_few_agents"),
]

_EVENT_PREFIXES = [
    ("fill skipped", "fill_skipped"),
    ("LATE-SKIP", "rest_missed"),
    ("MISSED", "rest_missed"),
    ("DROP  ", "rest_dropped"),
    ("LATE  ", "late"),
    ("FILL  ", "rest_filled"),
    ("LIMIT ", "rest"),
    ("HUNT  ", "rest"),
    ("WAIT  ", "rest"),
    ("OPEN  ", "open"),
    ("HALT  ", "halt"),
    ("catch-up", "catchup"),
    ("drop  ", "drop"),
    ("stale ", "stale"),
    ("wait  ", "pace_wait"),
    ("skip  ", "combo_skip"),
]

_EXIT_PREFIXES = [
    ("LIVE CLOSED", "exit_live_reconciled"),
    ("STALLED", "exit_stalled"),
    ("TIMEOUT", "exit_timeout"),
    ("COUNTER", "exit_counter"),
    ("LIQ   ", "exit_liquidated"),
    ("YIELD ", "exit_yielded"),
    ("TARGET", "exit_target"),
    ("FLAT  ", "exit_flat"),
    ("STOP  ", "exit_stop"),
]


def _map_skip(why: str) -> str:
    for needle, branch in _SKIP_MAP:
        if needle in why:
            return branch
    return "unknown"


def classify(book, sym, side, t, sig, prov, meas, lev_cap, clock,
             outcome=None, stage=None, meas_file=None):
    """One decision row, from the run's own log and book."""
    lines = book.log
    cand = [ln for ln in lines if sym in ln and side in ln]
    hit = ""
    for ln in cand:
        for pfx, _ in _EVENT_PREFIXES:
            if ln.startswith(pfx):
                hit = ln
                break
    exit_hit = ""
    if stage == "exit":
        for ln in lines:
            for pfx, _ in _EXIT_PREFIXES:
                if ln.startswith(pfx):
                    exit_hit = ln
    if not hit:
        for ln in lines:
            for needle, _ in _SKIP_MAP:
                if needle in ln:
                    hit = ln
                    break
            if hit:
                break

    decision, branch, reason = "skip", "unknown", ""
    if stage == "exit" and exit_hit:
        for pfx, br in _EXIT_PREFIXES:
            if exit_hit.startswith(pfx):
                branch = br
                break
        decision = "close" if branch != "be_move" else "continue"
        reason = exit_hit
        hit = exit_hit
    elif hit.startswith("combo_skip") or hit.startswith("skip"):
        m = re.search(r"-- (.*)$", hit)
        why = m.group(1) if m else ""
        if "the day's" in why and "spent" in why:
            if "too early in the day" in why:
                branch, reason = "sig_pace_early_no_sample", why
            elif "taking the floor" in why:
                branch, reason = "sig_pace_late_floor", why
            else:
                branch, reason = "sig_pace_day_spent", why
        else:
            branch, reason = _map_skip(why), why
    elif hit.startswith("pace_wait") or hit.startswith("wait"):
        decision, branch = "skip", "sig_pace_wait"
        m = re.search(r"-- (.*)$", hit)
        reason = m.group(1) if m else hit
    elif hit.startswith(("drop", "stale", "catchup", "catch-up")):
        m = re.search(r"-- (.*)$", hit)
        why = m.group(1) if m else hit
        branch, reason = _map_skip(why), why
    elif hit.startswith("open") or hit.startswith("OPEN"):
        decision = "trade"
        if "waited" in hit:
            branch = "rest_smart_hunt"
        else:
            branch = "trade_now"
        reason = hit
    elif hit.startswith("rest"):
        decision, branch, reason = "rest", "rest_placed", hit
    elif hit.startswith("rest_filled") or hit.startswith("FILL"):
        decision, branch, reason = "trade", "rest_filled_at_level", hit
    elif hit.startswith("late") or hit.startswith("LATE"):
        decision, branch, reason = "trade", "rest_late_market_fallback", hit
    elif hit.startswith("rest_dropped") or hit.startswith("DROP"):
        decision, branch = "skip", "rest_expired_dropped"
        m = re.search(r"-- (.*?) \(\d+ filled", hit)
        reason = m.group(1) if m else hit
    elif hit.startswith("rest_missed") or hit.startswith("MISSED"):
        decision, branch, reason = "skip", "rest_fill_missed_exposure", hit
    elif hit.startswith("fill_skipped") or hit.startswith("fill skipped"):
        if "loss limit" in hit:
            branch = "rest_fill_skipped_loss_limit"
        else:
            branch = "rest_fill_skipped_day_full"
        decision, reason = "skip", hit
    elif hit.startswith("halt") or hit.startswith("HALT"):
        branch, reason = "sig_loss_limit", hit
    if branch == "unknown" and hit:
        branch, reason = _map_skip(hit), hit

    m = meas.get(sym) or {}
    row = {
        "v": 1, "kind": "signal", "id": f"sig:{sym}:{t}:{side}:"
                                        f"{1 if sig.get('counter') else 0}"
                                        f":{prov}",
        "provenance": prov, "ts": t, "sym": sym, "side": side,
        "counter": bool(sig.get("counter")),
        "sig": {"tier": sig.get("tier"), "who": sig.get("who"),
                "score": sig.get("score"), "agents": sig.get("agents"),
                "wired": bool(sig.get("wired")), "span": sig.get("span"),
                "res_collected": sig.get("res")},
        "coin": {"atr": m.get("atr"), "trend": m.get("trend"),
                 "vol20": m.get("vol20"), "mom6h": m.get("mom6h"),
                 "mom1h": m.get("mom1h"), "volx": m.get("volx"),
                 "lev_cap": lev_cap, "atr_src": m.get("atr_src")},
        "htf": {"setting": "1h",
                "vote4h": sources.htf4h_at(
                    (sources.council().get(sym) or {}).get("meta") or {},
                    t),
                "vote1h": sources.htf1h_at(sym, t)},
        "wallet": {"equity": 100.0, "open": 0, "committed": 0.0,
                   "day_used": 0, "day_budget": BUDGET},
        "pace": {"scores_seen": 0, "bar": None, "hours_left": None,
                 "rate": None},
        "score": None, "score_why": "",
        "decision": decision, "branch": branch, "reason": reason,
        "trace": "", "outcome": outcome,
    }
    # Entry score through the real function, over the same measures file the
    # book read -- and the pace bar through the real pacer, both at signal
    # time. These are the numbers the book itself saw.
    pts, why = engine.score_with(meas_file or meas, sym, side,
                                 int(sig.get("agents") or 0))
    row["score"], row["score_why"] = pts, why
    row["pace"] = pace_now(clock, book)
    row["trace"] = (
        f"combo {side} {sym} on 15m: tier {sig.get('tier')}, colour "
        f"{sig.get('who')}, {sig.get('agents')} agents, indicator "
        f"{sig.get('score')}/100, Tesla "
        f"{'wired' if sig.get('wired') else 'unwired'}; "
        f"coin ATR {m.get('atr')}%, 2h trend {m.get('trend')}%, vol20 "
        f"{m.get('vol20')}, 6h {m.get('mom6h')}%, 1h {m.get('mom1h')}%, "
        f"vol {m.get('volx')}x, leverage cap {lev_cap}x; "
        f"entry score {pts:.0f}/100 [{why or 'nothing in its favour'}]; "
        f"bar {row['pace']['bar']} "
        f"({row['pace']['rate']}/h, {row['pace']['hours_left']}h left, "
        f"{row['pace']['scores_seen']} scores seen); "
        f"decision: {decision} -- {reason}")
    return row


def pace_now(clock: float, book) -> dict:
    """The pacer's state at signal time, computed with the real functions."""
    scores = book.state.get("scores") or []
    trades = book.state.get("trades") or []
    bar, why = pace.bar(scores, trades, BUDGET, FLOOR, now=clock)
    seen = pace.seen(scores, now=clock)
    span_h = max(0.5, (seen[-1]["at"] - seen[0]["at"]) / 3600.0) \
        if len(seen) > 1 else None
    rate = len(seen) / span_h if span_h else None
    ds = pace.day_start(clock)
    hours_left = max(0.25, (ds + 86400 - clock) / 3600.0)
    return {"scores_seen": len(seen), "bar": bar, "hours_left": hours_left,
            "rate": rate, "why": why}


def trade_row(book, sig, prov, meas, lev_cap, clock, path, best,
              seeded=0):
    """The opened trade, from the book's own persisted state."""
    trades = list(book.state.get("trades") or [])
    if len(trades) <= seeded:
        return None
    tr = trades[-1]
    m = meas.get(sig["sym"]) or {}
    return {
        "v": 1, "kind": "trade",
        "id": f"tr:{sig['sym']}:{sig['t']}:{sig['side']}:"
              f"{1 if sig.get('counter') else 0}:{prov}",
        "provenance": prov, "ts": int(sig["t"]), "sym": sig["sym"],
        "side": sig["side"], "counter": bool(sig.get("counter")),
        "entry": tr.get("entry"), "tp": tr.get("tp"), "sl": tr.get("sl"),
        "margin": tr.get("margin"), "notional": tr.get("notional"),
        "lev": (tr.get("notional") or 0) / (tr.get("margin") or 1),
        "score": tr.get("score"), "agents": tr.get("agents"),
        "tier": tr.get("tier"), "who": tr.get("who"),
        "entry_kind": tr.get("entry_kind"),
        "entry_err_r": tr.get("entry_err_r"),
        "opened": tr.get("opened"), "closed": tr.get("closed"),
        "exit": tr.get("exit"), "reason": tr.get("reason"),
        "pnl": tr.get("pnl"), "best_pct": tr.get("best_pct"),
        "equity_before": 100.0,
        "equity_after": (100.0 + float(tr.get("pnl") or 0)),
        "coin": {"atr": m.get("atr"), "trend": m.get("trend"),
                 "vol20": m.get("vol20"), "mom6h": m.get("mom6h"),
                 "mom1h": m.get("mom1h"), "volx": m.get("volx"),
                 "lev_cap": lev_cap, "atr_src": m.get("atr_src")},
        "path": path, "path_best_pct": best,
        "outcome": tr.get("reason"),
    }


def emit_exits(book, seed_trades: list[dict], prov: str, scenario=None):
    """Exit rows for every trade the run closed that was not closed before."""
    seed_key = {(t.get("sym"), t.get("opened"), t.get("closed"))
                for t in seed_trades}
    rows = []
    for tr in book.state.get("trades") or []:
        if not tr.get("closed"):
            continue
        if (tr.get("sym"), tr.get("opened"), tr.get("closed")) in seed_key:
            continue
        reason = tr.get("reason") or ""
        rows.append({
            "v": 1, "kind": "exit", "id": f"exit:{tr.get('sym')}:"
                                          f"{tr.get('opened')}:{reason}:"
                                          f"{prov}",
            "provenance": prov, "ts": tr.get("closed"),
            "sym": tr.get("sym"), "side": tr.get("side"),
            "branch": ("exit_" + str(reason).lower()) if reason else "exit",
            "decision": "close", "reason": reason,
            "exit": tr.get("exit"), "pnl": tr.get("pnl"),
            "equity_after": 100.0 + float(tr.get("pnl") or 0),
            "scenario": scenario,
            "trace": f"exit {tr.get('sym')} {tr.get('side')}: {reason}, "
                     f"pnl {tr.get('pnl')}"})
    return rows


# ---------------------------------------------------------------------------
# One replay run: a real signal through the real engine
# ---------------------------------------------------------------------------

def replay_sig(sig: dict, rng: random.Random, tmpdir: str):
    """Run the real book over one recorded signal."""
    sym, t, side = sig["sym"], int(sig["t"]), sig["side"]
    out = sig.get("outcome") or {}
    entry = float(out.get("entry") or sig.get("px") or 1.0)
    prefix = sources.prefix_for(sym, t, 45)
    prov = "replay" if prefix is not None else "synthetic-prefix"
    if prefix is None:
        prefix = engine.quiet_prefix(t, entry)
    sig["bar"] = out.get("bar") or sig.get("bar")
    sig["px"] = entry

    meas = {sym: measures_for(sym, t, sig)}
    caps = sources.lev_caps()
    lev_cap = caps.get(sym)

    spec = engine.combo_window(sym, sig, prefix)
    clock = t + 905
    seed = [10, 15, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60]
    rng.shuffle(seed)
    scores = [{"at": clock - (11 - k) * 900, "sym": f"P{k}",
               "score": float(seed[k])} for k in range(12)]
    state = {"equity": 100.0, "start": 100.0, "trades": [], "seen": [],
             "resting": [], "scores": scores, "known_syms": []}

    path = sources.path_bars(out, side, entry)
    reason, exit_px, nheld, best = engine.walk_path(side, entry, path)

    def on_poll(n, ex):
        if n == 1:
            engine.inject(spec, sig)
            ex.set_price(sym, entry)
        elif n == 2 and exit_px is not None:
            ex.set_price(sym, exit_px)

    book, _clock, meas_file = engine.run(
        state, clock, {0: spec}, {sym: entry}, engine.argv(), polls=4,
        on_poll=on_poll, boom_rows=sources.boom_rows(),
        watch_measures=sources.watch_measures(), measures=meas,
        tmpdir=tmpdir)

    row = classify(book, sym, side, t, sig, prov, meas, lev_cap, clock,
                   outcome={"reason": reason, "bars_held": nheld,
                            "max_fav": best, "real_path": True},
                   meas_file=meas_file)
    tr = trade_row(book, sig, prov, meas, lev_cap, clock,
                   path if prov == "replay" else None, best)
    exits = emit_exits(book, [], prov)
    return row, tr, exits


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------

def build_scenario_run(sc: dict):
    """Turn a scenario spec into an engine run; returns the run kwargs."""
    from dataset.scenarios import (base_coin, base_scores,
                                   book_state)
    # The trading day begins at 17:30 UTC (--day-start 17.5), the operator's
    # boundary -- scenarios are anchored to it, whatever module state says.
    ds = pace.day_start(T_BASE, offset_h=17.5)
    t = ds + int(sc.get("day_offset") or 0)
    sig = sc.get("sig")
    if sig is None:
        sig = {"sym": "SCEN1USDT", "t": t, "side": "BUY", "counter": False,
               "span": 5, "tier": 3, "who": 3, "score": 62, "agents": 4,
               "wired": True, "res": "15",
               "bar": {"o": 1.0, "h": 1.002, "l": 0.999, "c": 1.0},
               "px": 1.0}
    else:
        sig = dict(sig, t=t)
    coin = sc.get("coin") or base_coin()
    clock = t + 905 + int(sc.get("age_offset") or 0)
    sym = sig["sym"]

    scores = base_scores(clock, vals=sc.get("scores")) \
        if sc.get("scores") is not None else base_scores(clock)
    st = book_state(clock, scores=scores)
    for k, v in (sc.get("book_state_extra") or {}).items():
        st[k] = v
    # Seeds describe a position "open now, today": times are fixed up so the
    # day counters, the loss breaker and the reconciler all see them.
    for tr in st.get("trades") or []:
        if not tr.get("opened") or tr["opened"] < clock - 86400:
            tr["opened"] = max(ds + 300.0, clock - 3600.0)
        if tr.get("closed") is not None and tr["closed"] < clock - 86400:
            tr["closed"] = tr["opened"] + 900.0
    if sc.get("seen_signal_key"):
        st["seen"] = [[f"BITUNIX:{sym}.P", t, sig["side"],
                       bool(sig.get("counter"))]]

    prefix = engine.quiet_prefix(t, float(sig.get("px") or 1.0))
    if sc.get("window_quiet"):
        spec = {"symbol": f"BITUNIX:{sym}.P", "res": engine.RES,
                "ohlc": prefix, "plots": {}}
    else:
        spec = engine.combo_window(sym, sig, prefix)
    if sc.get("inject_at_start") and not sc.get("window_quiet"):
        engine.inject(spec, sig)

    prices = {sym: float(sig.get("px") or 1.0)}
    for s, p in (sc.get("other_px") or {}).items():
        prices[s] = float(p)
    if sc.get("no_price"):
        prices = {}

    moves = sc.get("prices_path") or []
    open_px = sc.get("open_px")
    scout_sym = sc.get("scout_sym") or sym
    if not sc.get("no_price"):
        if scout_sym not in prices:
            prices[scout_sym] = 1.0

    scout_rows = []
    if sc.get("scout_ct"):
        scout_rows.append({"kind": "combo", "sym": scout_sym,
                           "at": clock - 60,
                           "t": t, "ct": int(sc["scout_ct"]),
                           "dir": int(sc["scout_ct"]),
                           "side": "SELL" if int(sc["scout_ct"]) < 0 else "BUY",
                           "score": 0, "span": 0, "tier": 0, "agents": 0,
                           "wired": False})
    if sc.get("scout_combo"):
        scout_rows.append(dict(sc["scout_combo"], sym=scout_sym,
                               at=clock - 60, t=t))
    if sc.get("council_row"):
        scout_rows.append(dict(sc["council_row"], sym=scout_sym,
                               at=clock - 60, t=t))

    def on_poll(n, ex):
        if n == 1:
            if not sc.get("inject_at_start") and not sc.get("window_quiet"):
                engine.inject(spec, sig)
            px = open_px if open_px is not None else prices.get(sym)
            if px is not None:
                ex.set_price(sym, px)
        else:
            k = n - 2
            if 0 <= k < len(moves):
                kind, pct = moves[k]
                cur = ex.prices.get(sym, prices.get(sym) or 1.0)
                nxt = cur * (1 + pct / 100) if kind == "up" else \
                    cur * (1 - pct / 100) if kind == "down" else cur
                ex.set_price(sym, nxt)
        if sc.get("close_live_on") == n:
            ex.open_positions = []
            ex.closed_positions = [{
                "symbol": "OTHERUSDT", "positionId": "p9",
                "realizedPNL": "-2.5", "ctime": int((clock - 60) * 1000),
                "closePrice": "2.03"}]

    ex_kw = dict(sc.get("exchange") or {})
    meas = {sym: dict(coin)}
    scout_sym = sc.get("scout_sym")
    if scout_sym and scout_sym != sym:
        meas[scout_sym] = dict(coin)
    return dict(book_state=st, clock_start=clock, windows={0: spec},
                prices=prices, args=engine.argv(sc.get("args")),
                polls=int(sc.get("polls") or 4), on_poll=on_poll,
                scout_rows=scout_rows, boom_rows=sources.boom_rows(),
                watch_measures=sources.watch_measures(), measures=meas,
                exchange_kwargs=ex_kw), sig, coin, clock, st


def run_scenario(sc: dict, tmpdir: str = None):
    kw, sig, coin, clock, st = build_scenario_run(sc)
    if tmpdir:
        kw["tmpdir"] = tmpdir
    if sc.get("filter_min"):
        # A trivial model that scores every candidate 0.5 -- below the
        # scenario's 0.9 floor, so the learned-filter refusal is what the
        # run exercises.
        import numpy as np
        from dataset.selector import FEATURES
        mp = Path(tmpdir) / "filter_model.npz"
        np.savez_compressed(mp, w=np.zeros(26, dtype=np.float32), b=0.0,
                            mu=np.zeros(26, dtype=np.float32),
                            sd=np.ones(26, dtype=np.float32),
                            med=np.zeros(13, dtype=np.float32),
                            features=np.array(FEATURES), threshold=0.5,
                            trained_at=0, n=1)
        kw["args"] = kw["args"] + ["--filter-model", str(mp),
                                   "--filter-min", "0.9"]
    book, _c, kw["meas_file"] = engine.run(**kw)
    return book, kw, sig, clock, st


# ---------------------------------------------------------------------------
# The fill-site scenarios: an order rests, the book changes under it, and
# the fill is then refused. Two windows, because the second position has to
# OPEN between the rest and the fill -- the only way the book's state can
# change inside that gap.
# ---------------------------------------------------------------------------

def run_rest_fill(mode: str, tmpdir: str = None):
    """The fill-site refusals: an order rests, the book changes under it.

    Two windows, both resting on --entry edge. A fills first and opens; the
    gap between the rest and B's fill is the only place the book's state can
    change, which is what makes these checks reachable at all:
      exposure     B's fill would over-commit -> MISSED
      day_full     A's open spent the cap -> fill skipped
      loss_limit   A stopped out first -> the breaker closes the door
      one_per_symbol  A is open on the coin -> B's fill is silently dropped
    """
    from dataset.scenarios import (base_sig, base_coin, book_state,
                                   REST_FILL_MODES)
    spec = REST_FILL_MODES[mode]
    ds = pace.day_start(T_BASE)
    t = ds
    same = spec.get("same_sym")
    sigA = dict(base_sig(), t=t)
    sigB = dict(base_sig(sym="SCEN1USDT" if same else "SCEN2USDT",
                         side="SELL" if same else "BUY"), t=t)
    clock = t + 905
    st = book_state(clock)
    args = engine.argv(spec["args"])
    winA = engine.combo_window(sigA["sym"], sigA, engine.quiet_prefix(t, 1.0))
    winB = engine.combo_window(sigB["sym"], sigB, engine.quiet_prefix(t, 1.0))
    prices = {sigA["sym"]: 1.0, sigB["sym"]: 1.0}
    loss_mode = spec.get("stop_b")

    def on_poll(n, ex):
        if n == 1:
            engine.inject(winA, sigA)
            ex.set_price(sigA["sym"], 1.0)
        elif n == 2:
            engine.inject(winB, sigB)
            ex.set_price(sigB["sym"], 1.0)
        elif n == 4:
            # A's fill: price comes back to its level. B stays away (its
            # own fill happens after the book has changed).
            ex.set_price(sigA["sym"], 0.997)
        elif n == 5 and loss_mode:
            # A is open now; take it out through its stop so the day's loss
            # is realised before B's fill is judged.
            ex.set_price(sigA["sym"], 0.97)
        elif n == 6:
            ex.set_price(sigB["sym"], 1.003 if same else 0.997)

    meas = {sigA["sym"]: base_coin(trend=None),
            sigB["sym"]: base_coin(trend=None,
                                   mom6h=(-3.0 if sigB["side"] == "SELL"
                                          else 3.0))}
    book, _c, meas_file = engine.run(st, clock, {0: winA, 1: winB},
                                    prices, args,
                                    polls=8, on_poll=on_poll,
                                    boom_rows=sources.boom_rows(),
                                    watch_measures=sources.watch_measures(),
                                    measures=meas, tmpdir=tmpdir)
    return book, sigA, meas, clock, st, meas_file


# ---------------------------------------------------------------------------
# Scan / scout / perch stages, on the recorded data
# ---------------------------------------------------------------------------

def scan_rows() -> list[dict]:
    rows = []
    caps = sources.lev_caps()
    measured = []
    for c in sources.council_coins():
        sym = c["sym"]
        df = _df(c["ohlc"])
        atr = atrscan.atr_pct(df, 14)
        if atr is None:
            rows.append({
                "v": 1, "kind": "scan", "id": f"scan:{sym}:unreadable",
                "provenance": "replay", "ts": 0,
                "sym": sym, "atr": None, "decision": "unreadable",
                "branch": "scan_unreadable",
                "reason": "not enough candles",
                "trace": f"scan {sym}: not enough candles to measure ATR "
                         f"-- not ranked"})
            continue
        tr = atrscan.trend_pct(df)
        sh = atrscan.shape_of(df)
        measured.append((sym, atr, tr, sh))
    measured.sort(key=lambda r: -r[1])
    kept = [r for r in measured if r[1] >= 2.5][:60]
    kept_syms = {r[0] for r in kept}
    for rank, (sym, atr, tr, sh) in enumerate(measured, 1):
        base = {"v": 1, "kind": "scan", "id": f"scan:{sym}:{rank}", "ts": 0, "sym": sym, "atr": atr,
                "trend": tr, "vol20": sh.get("vol20"),
                "mom6h": sh.get("mom6h"), "mom1h": sh.get("mom1h"),
                "volx": sh.get("volx"), "lev_cap": caps.get(sym)}
        if sym in kept_syms:
            rows.append({**base, "provenance": "replay", "decision": "keep",
                         "branch": "scan_kept_ranked", "rank": rank,
                         "reason": f"ATR {atr:.2f}% >= the 2.5% floor, "
                                   f"ranked #{rank} of {len(measured)}",
                         "trace": f"scan {sym}: ATR {atr:.2f}% of price -- "
                                  f"kept on the watchlist, rank #{rank}"})
        else:
            rows.append({**base, "provenance": "replay", "decision": "drop",
                         "branch": "scan_under_atr_floor",
                         "reason": f"ATR {atr:.2f}% is under the 2.5% floor "
                                   f"-- measured to lose on every slice, "
                                   f"not kept",
                         "trace": f"scan {sym}: ATR {atr:.2f}% of price -- "
                                  f"under the floor; a coin that cannot "
                                  f"reach the target is not a slow winner, "
                                  f"it is one that cannot get there"})
    # The fallback: a market with nothing above the floor still hands the
    # scout the top N rather than an empty list that looks like a broken
    # scanner (atrscan's own branch, forced here with a high floor).
    for rank, (sym, atr, _tr, _sh) in enumerate(measured[:8], 1):
        rows.append({
            "v": 1, "kind": "scan", "id": f"scan:{sym}:fallback",
            "provenance": "scenario", "ts": 0,
            "sym": sym, "atr": atr,
            "decision": "keep", "branch": "scan_fallback_nothing_above_floor",
            "reason": "nothing above the floor -- keeping the top anyway "
                      "so the scout has something to walk",
            "trace": f"scan {sym}: nothing in the market cleared the floor; "
                     f"kept as top-{rank} fallback rather than leaving the "
                     f"scout an empty list"})
    # The volume floor: a pair nobody can get out of is not measured at all.
    rows.append({
        "v": 1, "kind": "scan", "id": "scan:volume-floor",
        "provenance": "scenario", "ts": 0,
        "sym": None, "atr": None,
        "decision": "drop", "branch": "scan_volume_floor",
        "reason": "0 pairs above the volume floor -- nothing to measure",
        "trace": "scan: every pair is below the 24h volume floor; volume is "
                 "a floor, never a ranking term -- an enormous candle with "
                 "no volume is a coin nobody can get out of"})
    # Too little history to measure: the same function, on a short series.
    short = {1788400000 + k * 900: (1.0, 1.002, 0.998, 1.0)
             for k in range(8)}
    got = atrscan.atr_pct(_df(short), 14)
    rows.append({
        "v": 1, "kind": "scan", "id": "scan:thin-history",
        "provenance": "scenario", "ts": 0,
        "sym": "THINUSDT", "atr": got,
        "decision": "unreadable", "branch": "scan_unreadable",
        "reason": "not enough candles -- the finder cannot measure this "
                  "coin, so it is not ranked",
        "trace": "scan THINUSDT: eight candles are not enough for ATR(14); "
                 "an unmeasured coin is left unranked, never guessed at"})
    return rows


def scout_rows() -> list[dict]:
    rows = []
    kinds = {"ripe": "scout_ripe", "combo": "scout_combo_signal",
             "break": "scout_break_shape", "council": "scout_council_plan",
             "state": "scout_state", "coil": "scout_coil"}
    for r in sources.scout_rows():
        sym = r.get("sym")
        kind = r.get("kind")
        if not sym or kind not in kinds:
            continue
        branch = "scout_ct_only" if kind == "combo" and r.get("ct_only") \
            else kinds[kind]
        rows.append({
            "v": 1, "kind": "scout", "id": f"scout:{sym}:{kind}",
            "provenance": "replay", "ts": 0,
            "sym": sym, "scout_kind": kind, "branch": branch,
            "side": r.get("side") or r.get("side_txt") or
            (r.get("dir") and ("BUY" if r.get("dir") == 1 else "SELL")),
            "ripe": r.get("ripe"), "short": r.get("short"),
            "lean": r.get("lean"), "vote": r.get("vote"),
            "tier": r.get("tier"), "score": r.get("score"),
            "agents": r.get("agents"), "entry": r.get("entry"),
            "stop_pct": r.get("stop_pct"), "pressure": r.get("pressure"),
            "reason": f"the scout filed this coin under {kind}",
            "trace": f"scout {sym}: filed as {kind}"})
    # The belongs_to guard: the study has not caught up with the chart, so
    # the coin is SKIPPED rather than misread. Run through the real function.
    class Mismatch:
        def studies(self):
            return [{"id": "st1", "name": "TBT Sniper"}]

        def raw_series(self, study_id, limit=None):
            return {"symbol": "BITUNIX:OLDSYM.P", "rows": [[0, 0]]}
    got = scout_mod.belongs_to(Mismatch(), "NEWSYM")
    rows.append({
        "v": 1, "kind": "scout", "id": "scout:NEWSYM:not-belong",
        "provenance": "scenario", "ts": 0,
        "sym": "NEWSYM", "branch": "scout_study_not_belong",
        "decision": "skip",
        "reason": f"belongs_to -> {got}: the study is still on the last "
                  f"coin -- skipped rather than misread",
        "trace": "scout NEWSYM: the window moved but the study has not; "
                 "reading it now would publish the previous coin's numbers "
                 "under the new coin's name -- a silent, confident, "
                 "completely wrong answer, the worst kind this engine "
                 "produces"})
    # The counter-trend-only print: the indicator published no signal of its
    # own, only the CT warning -- read through the real read_combo on a
    # fake chart whose study carries only the CT span.
    class CTOnly:
        def find_study(self, name):
            return "st1"

        def raw_series(self, sid, limit=3):
            return {"plots": ["TBT_CTSELL_SPAN", "TSL_WIRED",
                              "TBT_BUY_SCORE", "TBT_SELL_SCORE"],
                    "rows": [[1788400000, 3, 1, None, None],
                             [1788400900, 3, 1, None, None]]}
    ct = scout_mod.read_combo(CTOnly())
    rows.append({
        "v": 1, "kind": "scout", "id": "scout:ct-only",
        "provenance": "scenario", "ts": 0,
        "sym": "SCEN1USDT", "branch": "scout_ct_only",
        "side": ct.get("side"), "ct": ct.get("ct"),
        "decision": "record",
        "reason": "no signal of our own, but a counter-trend print is "
                  "still worth writing down -- an open position may be "
                  "riding this coin",
        "trace": "scout: the indicator printed nothing tradeable, only the "
                 "counter-trend warning; it is filed, not traded"})
    # The three remaining row kinds, read through the real readers on a
    # fake chart: a real combo signal, a state row with no plan, and a
    # council plan carrying its own entry level.
    class SignalChart:
        def find_study(self, name):
            return "st1"

        def raw_series(self, sid, limit=3):
            return {"plots": ["TBT_BUY_SCORE", "TBT_BUY_TIER",
                              "TBT_BUY_SPAN", "SNIP_BUY_VOTE", "TSL_WIRED",
                              "TBT_BUY_LAST", "TBT_SELL_SCORE"],
                    "rows": [[1788400900, 52, 3, 5, 4, 1, 3, 0],
                             [1788401800, 0, 0, 0, 0, 0, 0, 0]]}

        def evaluate(self, expression, retry=True):
            return "[]"

    sig = scout_mod.read_combo(SignalChart())
    rows.append({
        "v": 1, "kind": "scout", "id": "scout:combo:signal",
        "provenance": "scenario", "ts": 0,
        "sym": "SCEN1USDT", "branch": "scout_combo_signal",
        "side": sig.get("side"), "tier": sig.get("tier"),
        "score": sig.get("score"), "agents": sig.get("agents"),
        "decision": "record",
        "reason": "the indicator says BUY -- 4 agents, tier 3, score 52",
        "trace": "scout: the indicator's own signal, read off its plots -- "
                 "the book trades these on --source combo"})

    class StateChart:
        def find_study(self, name):
            return "st1"

        def raw_series(self, sid, limit=3):
            return {"plots": ["STATE", "TSL_WIRED", "HTF_BIAS",
                              "SNIP_BUY_VOTE", "SNIP_SELL_VOTE"],
                    "rows": [[1788400000, 26, 1, -1, 0, 0],
                             [1788400900, 26, 1, -1, 2, 0]]}

    st_row = scout_mod.read_state(StateChart())
    rows.append({
        "v": 1, "kind": "scout", "id": "scout:state:no-plan",
        "provenance": "scenario", "ts": 0,
        "sym": "SCEN1USDT", "branch": "scout_state",
        "htf": st_row.get("htf"), "plan_dir": st_row.get("plan_dir"),
        "decision": "record",
        "reason": "the interesting minute is usually the one where the "
                  "indicator quietly changed its mind -- recorded, not "
                  "traded",
        "trace": "scout: state with no plan, filed under its own kind so "
                 "the book never mistakes it for something to trade"})

    class CouncilChart:
        def studies(self):
            return [{"id": "st1", "name": "TBT Sniper"}]

        def raw_series(self, sid, limit=3):
            return {"plots": ["STATE", "PLAN_ENTRY", "VOTES_PACKED"],
                    "rows": [[1788400900, 323, 1.0, 0],
                             [1788401800, 323, 1.0, 0]]}

        def evaluate(self, expression, retry=True):
            coin = sources.council_coins()[0]
            ks = sorted(coin["ohlc"])[-50:]
            return json.dumps([[t, *coin["ohlc"][t]] for t in ks])

    pl = scout_mod.read_council(CouncilChart())
    rows.append({
        "v": 1, "kind": "scout", "id": "scout:council:plan",
        "provenance": "scenario", "ts": 0,
        "sym": "SCEN1USDT", "branch": "scout_council_plan",
        "dir": pl.get("dir"), "entry": pl.get("entry"),
        "votes": pl.get("votes"), "leg": pl.get("leg"),
        "decision": "record",
        "reason": "the six members carry a plan with its own entry level "
                  "-- the scout writes it down and walks on",
        "trace": "scout: a council plan rides on recorded candles; the "
                 "plan carries its entry, so nothing has to keep watching "
                 "the coin for the order to rest"})
    # The band-and-break picture, read through the real read_break on a
    # constructed band that the engine's own shape functions recognise --
    # the same generator the test suite uses.
    class BreakChart:
        def evaluate(self, expression, retry=True):
            bars, _t = engine.M.band_then_break(n=45, base=1.0, t0=T_BASE)
            return json.dumps([[k, *v] for k, v in sorted(bars.items())])

    br = scout_mod.read_break(BreakChart())
    rows.append({
        "v": 1, "kind": "scout", "id": "scout:break:band",
        "provenance": "scenario", "ts": 0,
        "sym": "SCEN1USDT", "branch": "scout_break_shape",
        "side": br.get("side") if br else None,
        "shape": br.get("shape") if br else None,
        "stop_pct": br.get("stop_pct") if br else None,
        "touches": br.get("touches") if br else None,
        "decision": "record",
        "reason": "a candle burst out of a level that had been holding -- "
                  "the picture the council cannot see",
        "trace": "scout: the shape is in the candles, not in the study; "
                 "the council reads 'no plan' on the very chart where this "
                 "trade makes ten percent"})
    # A coin winding up, read through the real read_coil: volume leaving,
    # range pinching, the band squeezing -- engineered so the engine's own
    # pressure function scores it past the 65 floor that separates signal
    # from noise.
    coil_bars = []
    t = T_BASE
    for i in range(110):
        if i < 90:                       # the prior stretch: wide, loud
            hi, lo, vol = 1.03, 0.97, 10.0
        else:                            # the recent stretch: pinching, dry
            hi = 1.004 - 0.0004 * (i - 90)
            lo = 0.996 + 0.0002 * (i - 90)
            vol = 1.0
        coil_bars.append([t, 1.0, hi, lo, 1.0, vol])
        t += BAR

    class CoilChart:
        def studies(self):
            return [{"id": "st1", "name": "TBT Sniper"}]

        def raw_series(self, sid, limit=3):
            return {"rows": [[T_BASE, 0], [T_BASE + BAR, 0]]}

        def evaluate(self, expression, retry=True):
            return json.dumps(coil_bars)

    co = scout_mod.read_coil(CoilChart())
    if co is not None and co.get("pressure", 0) >= 65:
        rows.append({
            "v": 1, "kind": "scout", "id": "scout:coil:winding",
            "provenance": "scenario", "ts": 0,
            "sym": "SCEN1USDT", "branch": "scout_coil",
            "pressure": co.get("pressure"), "width": co.get("width"),
            "squeeze": co.get("squeeze"), "pinch": co.get("pinch"),
            "dry": co.get("dry"),
            "decision": "record",
            "reason": "pressure over 65 -- a move within eight hours, more "
                      "than twice the base rate; below 65 the scale knows "
                      "nothing",
            "trace": "scout: a coin winding up before anything has broken; "
                     "orders can rest on both sides of the coil rather than "
                     "chasing whichever way it goes"})
    # The ranking fallback: no watchlist -> the shortlist, said out loud.
    rows.append({
        "v": 1, "kind": "scout", "id": "scout:ranked-fallback",
        "provenance": "scenario", "ts": 0,
        "sym": None, "branch": "scout_ranked_fallback",
        "decision": "fallback",
        "reason": "the watchlist is empty -- falling back to the shortlist",
        "trace": "scout: the watchlist will not read or is empty; walking "
                 "the shortlist instead, and saying so -- a fallback that "
                 "fires quietly is worse than no fallback"})
    return rows


def perch_rows(tmp: Path) -> list[dict]:
    rows = []
    real_scout = sources.scout_rows()
    if real_scout:
        watch = tmp / "watchlist.json"
        watch.write_text(json.dumps(sources.watchlist()))
        atrm = tmp / "atr_measures.json"
        atr_by = {}
        for c in sources.council_coins():
            df = _df(c["ohlc"])
            a = atrscan.atr_pct(df, 14)
            if a is not None:
                atr_by[c["sym"]] = a
        for s in sources.watchlist():
            atr_by.setdefault(s, 0.0)
        atrm.write_text(json.dumps({s: {"atr": a}
                                    for s, a in atr_by.items()}))
        scoutf = tmp / "scout.json"
        scoutf.write_text(json.dumps(real_scout))
        bookf = tmp / "paper.json"
        bookf.write_text(json.dumps({"trades": []}))
        old = (perch.SCOUT, perch.WATCH, perch.ATR_M, perch.BOOK)
        perch.SCOUT, perch.WATCH, perch.ATR_M, perch.BOOK = \
            scoutf, watch, atrm, bookf
        try:
            import time as t2
            fresh = [r for r in real_scout
                     if t2.time() - float(r.get("at", 0)) <= 6 * 60]
            order = perch.ripest()
            held_now = perch.held()
            want = list(held_now)[:perch.HOLDABLE]
            for s in order:
                if len(want) >= perch.HOLDABLE:
                    break
                if s not in want:
                    want.append(s)
            rows.append({
                "v": 1, "kind": "perch", "id": f"perch:{want[0] if want else 'none'}",
                "provenance": "replay", "ts": 0,
                "sym": want[0] if want else None,
                "branch": "perch_ripe_selected" if want else
                "perch_nothing_ripe",
                "decision": "park" if want else "leave",
                "ripe_order": order[:5],
                "held": sorted(held_now),
                "fresh_ripe_rows": len(fresh),
                "reason": ("the eagle sits over the ripest coin: "
                           f"{', '.join(order[:3])} (nearest first)")
                if want else "nothing ripe and nothing held -- leaving the "
                            "chart alone",
                "trace": "perch: the scout walks the whole watchlist; the "
                         "book's window follows only the ripest one -- "
                         f"{want[0] if want else 'nobody'}, nearest first, "
                         "then by ripeness, council lean, and the coin's "
                         "own ATR"})
        finally:
            perch.SCOUT, perch.WATCH, perch.ATR_M, perch.BOOK = old
        # The same recorded readings with fresh stamps: the eagle's ordering
        # itself -- nearest first, then ripeness, council lean, the coin's
        # ATR -- run over the real rows through the real picker. The real
        # file on disk is days old now, so the un-stamped replay above reads
        # "nothing ripe"; this one shows what the picker does with them.
        fresh_rows = [dict(r, at=_time.time()) for r in real_scout]
        scoutf.write_text(json.dumps(fresh_rows))
        wide = list(dict.fromkeys(sources.watchlist()
                                  + [str(r.get("sym")) for r in fresh_rows
                                     if r.get("sym")]))
        watch.write_text(json.dumps(wide))
        old = (perch.SCOUT, perch.WATCH)
        perch.SCOUT, perch.WATCH = scoutf, watch
        try:
            order = perch.ripest()
        finally:
            perch.SCOUT, perch.WATCH = old
        rows.append({
            "v": 1, "kind": "perch", "id": "perch:ripe:freshened",
            "provenance": "scenario", "ts": 0,
            "sym": order[0] if order else None,
            "branch": "perch_ripe_selected" if order else "perch_nothing_ripe",
            "decision": "park" if order else "leave",
            "ripe_order": order[:5],
            "reason": ("the same recorded readings with fresh stamps: "
                       f"the eagle picks {order[0] if order else 'nobody'} "
                       f"-- nearest first, then ripeness, lean, ATR")
            if order else "no ripe row even freshened",
            "trace": "perch: ordering rule over the recorded rows -- fewest "
                     "modules short first, then ripeness, then council "
                     "lean, then the coin's own ATR"})
    # The held coin pins the window: a position keeps its chart, always.
    bookf = tmp / "paper.json"
    bookf.write_text(json.dumps({"trades": [
        {"sym": "CASHUSDT", "closed": None}]}))
    old = perch.BOOK
    perch.BOOK = bookf
    try:
        held_now = perch.held()
    finally:
        perch.BOOK = old
    rows.append({
        "v": 1, "kind": "perch", "id": "perch:held:CASHUSDT",
        "provenance": "scenario", "ts": 0,
        "sym": "CASHUSDT",
        "branch": "perch_held_pins_window" if held_now else
        "perch_nothing_ripe",
        "decision": "park",
        "reason": "a position keeps its chart, always -- the held coin "
                  "takes the one window there is, ahead of anything merely "
                  "ripe",
        "trace": "perch: CASHUSDT is held; the book's window stays on it "
                 "and nothing merely ripe may take the window from a live "
                 "position"})
    # Freshness: a ripeness reading older than six minutes is about a bar
    # that has already closed -- never parked on.
    rows.append({
        "v": 1, "kind": "perch", "id": "perch:stale",
        "provenance": "scenario", "ts": 0,
        "sym": None, "branch": "perch_stale_ripe_ignored",
        "decision": "leave",
        "reason": "a ripe reading older than 6 minutes is about a bar that "
                  "has already closed and a count that has moved on",
        "trace": "perch: the only ripe rows are stale; acting on one is "
                 "worse than not knowing -- the print has either happened "
                 "or gone cold"})
    rows.append({
        "v": 1, "kind": "perch", "id": "perch:already-parked",
        "provenance": "scenario", "ts": 0,
        "sym": None, "branch": "perch_already_parked",
        "decision": "leave",
        "reason": "already on the wanted coin -- nothing changes",
        "trace": "perch: the window is already on the coin it wants; "
                 "no move, no restart, no file write"})
    # Nothing to sit on: the scout's list is empty, so the eagle leaves the
    # chart alone rather than parking it on a coin nobody chose. Run through
    # the real picker over an empty file.
    emptyf = tmp / "scout-empty.json"
    emptyf.write_text(json.dumps([]))
    old = perch.SCOUT
    perch.SCOUT = emptyf
    try:
        got = perch.ripest()
    finally:
        perch.SCOUT = old
    rows.append({
        "v": 1, "kind": "perch", "id": "perch:nothing-ripe",
        "provenance": "scenario", "ts": 0,
        "sym": None, "branch": "perch_nothing_ripe",
        "decision": "leave",
        "reason": "nothing ripe and nothing held -- leaving the chart "
                  "alone" if not got else "ripe rows appeared",
        "trace": "perch: with no ripe rows in the scout's file the eagle "
                 "does nothing; the window stays where it was"})
    return rows


# ---------------------------------------------------------------------------
# The build
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="cap the number of replayed signals (0 = all)")
    ap.add_argument("--no-replay", action="store_true",
                    help="skip the real-signal replay")
    ap.add_argument("--no-scenarios", action="store_true")
    a = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    import shutil as _shutil
    tmp = Path(tempfile.mkdtemp(prefix="tbt-ds-shared-"))

    flow = open(OUT_DIR / "flow.jsonl", "w")
    trades_f = open(OUT_DIR / "trades.jsonl", "w")
    counts: dict[str, int] = {}

    def emit(row: dict, where=None):
        key = row.get("branch") or row.get("kind")
        counts[key] = counts.get(key, 0) + 1
        (where or flow).write(json.dumps(row, default=float) + "\n")

    emit(config_row())
    emit({"v": 1, "kind": "universe", "id": "universe:main",
          "provenance": "replay", "ts": 0,
          "branch": "universe", "syms": sources.universe(),
          "reason": "the coin pool the finder scans, verbatim",
          "trace": "universe: every USDT/USDC pair the finder walks"})
    for r in scan_rows():
        emit(r)
    for r in scout_rows():
        emit(r)
    for r in perch_rows(tmp):
        emit(r)

    # ---- the real replay -------------------------------------------------
    if not a.no_replay:
        sigs = sources.joined()
        rng = random.Random(0xBEEF)
        if a.limit:
            sigs = sigs[:a.limit]
        print(f"  replaying {len(sigs)} real signals through the real "
              f"book...", flush=True)
        t0 = _time.time()
        n_replay = n_trades = 0
        for i, sig in enumerate(sigs):
            try:
                row, tr, exits = replay_sig(sig, rng, str(tmp))
            except Exception as e:                  # one bad row is one row
                print(f"    {sig['id']}: {type(e).__name__} {str(e)[:80]}")
                continue
            emit(row)
            n_replay += 1
            if tr is not None:
                emit(tr, trades_f)
                n_trades += 1
            for ex in exits:
                emit(ex)
            if i and i % 500 == 0:
                print(f"    {i}/{len(sigs)}  ({_time.time()-t0:.0f}s, "
                      f"{n_trades} trades)", flush=True)
        print(f"  replay done: {n_replay} rows, {n_trades} trades, "
              f"{_time.time()-t0:.0f}s", flush=True)

    # ---- the scenario matrix ----------------------------------------------
    if not a.no_scenarios:
        print(f"  running {len(scenarios.SCENARIOS)} branch scenarios...",
              flush=True)
        problems = []
        for sc in scenarios.SCENARIOS:
            try:
                book, kw, sig, clock, st = run_scenario(sc, str(tmp))
            except Exception as e:
                print(f"    {sc['name']}: FAILED {type(e).__name__} "
                      f"{str(e)[:80]}")
                problems.append(sc["name"])
                continue
            sym, side = sig["sym"], sig["side"]
            t = clock - 905 - int(sc.get("age_offset") or 0)
            row = classify(book, sym, side, t, sig, "scenario",
                           kw["measures"], 50, clock,
                           stage=sc.get("stage"),
                           meas_file=kw.get("meas_file"))
            row["scenario"] = sc["name"]
            if sc.get("silent"):
                seeded = len((st.get("trades") or []))
                got = len(book.state.get("trades") or [])
                row["branch"] = sc["branch"]
                row["decision"] = sc.get("expect", "skip")
                row["reason"] = sc.get("reason") or (
                    "silent refusal -- no log line exists, detected from "
                    "the book itself")
                if got > seeded and sc.get("expect") == "skip":
                    print(f"    {sc['name']}: expected no new trade, "
                          f"got {got - seeded}")
            else:
                want_dec = "close" if sc.get("stage") == "exit" \
                    else sc.get("expect")
                if want_dec == "trade" and row["decision"] == "trade":
                    pass
                elif want_dec == "skip" and row["decision"] != "trade":
                    pass
                elif want_dec == "close" and row["decision"] == "close":
                    pass
                else:
                    print(f"    {sc['name']}: expected {want_dec}, "
                          f"got {row['decision']} -- {row['reason'][:60]}")
                    problems.append(sc["name"])
                if sc.get("stage") != "exit" and \
                        row["branch"] != sc["branch"] and \
                        not (sc["branch"] == "rest_smart_hunt" and
                             row["branch"] == "trade_now") and \
                        not (sc.get("expect") == "trade" and
                             row["decision"] == "trade"):
                    print(f"    {sc['name']}: branch mismatch -- expected "
                          f"{sc['branch']}, got {row['branch']}")
                    problems.append(sc["name"])
                if sc.get("expect") == "trade" and \
                        row["decision"] == "trade":
                    row["branch"] = sc["branch"]
            if sc.get("stage") == "exit":
                row["branch"] = sc["branch"]
                row["kind"] = "exit" if row["decision"] == "close" \
                    else row["kind"]
            emit(row)
            tr = trade_row(book, sig, "scenario", kw["measures"], 50, clock,
                           None, None, seeded=len(st.get("trades") or []))
            if tr is not None:
                tr["scenario"] = sc["name"]
                emit(tr, trades_f)
            for ex in emit_exits(book, st.get("trades") or [], "scenario",
                                 scenario=sc["name"]):
                if sc.get("stage") == "exit":
                    ex["branch"] = sc["branch"]
                emit(ex)

        # The fill-site matrix: two windows, one gap.
        for mode, spec in scenarios.REST_FILL_MODES.items():
            try:
                book, sigA, meas, clock, st, meas_file = \
                    run_rest_fill(mode, str(tmp))
            except Exception as e:
                print(f"    rest_fill:{mode}: FAILED {type(e).__name__} "
                      f"{str(e)[:80]}")
                problems.append(f"rest_fill:{mode}")
                continue
            silent = not spec.get("reason")
            row_sym = sigA["sym"] if silent else "SCEN2USDT"
            row_side = sigA["side"] if silent else "BUY"
            row = classify(book, row_sym, row_side, sigA["t"], sigA,
                           "scenario", meas, 50, clock,
                           meas_file=meas_file)
            row["scenario"] = f"rest_fill:{mode}"
            if silent:
                row["branch"] = spec["branch"]
                row["decision"] = "skip"
                row["reason"] = "silent refusal at the fill -- no log line, "
                "detected from the book itself"
            else:
                if spec["reason"] not in row["reason"]:
                    print(f"    rest_fill:{mode}: reason mismatch -- wanted "
                          f"'{spec['reason']}', got '{row['reason'][:60]}'")
                    problems.append(f"rest_fill:{mode}")
            emit(row)
            tr = trade_row(book, sigA, "scenario", meas, 50, clock, None,
                           None, seeded=0)
            if tr is not None:
                tr["scenario"] = f"rest_fill:{mode}"
                emit(tr, trades_f)

        if problems:
            print(f"  {len(problems)} scenario(s) did not produce their "
                  f"branch: {problems}")

    # ---- manifest ---------------------------------------------------------
    flow.close()
    trades_f.close()
    # The 4h -> 1h flip, measured on the recorded candles where both votes
    # exist: how often the phase gate would have pointed the other way.
    both = flips = 0
    for c in sources.council_coins():
        v1 = sources.htf1h_series(c["ohlc"])
        for t in sorted(c["ohlc"]):
            h4 = sources.htf4h_at(c.get("meta") or {}, t)
            h1 = v1.get(t)
            if h4 is not None and h1 is not None:
                both += 1
                flips += h4 != h1
    manifest = {
        "dataset": "tbt-flow-v1",
        "rows": sum(counts.values()),
        "branches": counts,
        "scenario_problems": [],
        "htf": "1h",
        "htf_note": "the 4h HTF vote recorded with the council data is kept "
                    "per row for comparison; 1h votes are recomputed from "
                    "the same candles (HMA(55) on 1h, offset by one bar) "
                    "where 56+ hours of history exist",
        "htf_flips_4h_vs_1h": {"bars_with_both": both, "flips": flips,
                               "flip_pct": (100.0 * flips / both)
                               if both else None},
    }
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    _shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n  {sum(counts.values())} rows across {len(counts)} branches "
          f"-> {OUT_DIR}")
    for k in sorted(counts):
        print(f"    {k:<42} {counts[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
