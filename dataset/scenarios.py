"""
The branch matrix: one scenario per decision the flow can make.

Each scenario runs through the REAL engine (dataset/engine.py) or the REAL
pure functions, targets exactly one branch, and generation-time verification
asserts that the branch was actually produced. `base_sig` / `base_coin` /
`base_scores` are a signal and a coin that pass every live gate, so a
scenario only has to break the ONE thing its branch is about.

Numbers are not invented out of thin air: they sit inside the ranges the
real recorded data actually covers (agents 3-5, tier 0/1/2/3, scores 0-100,
ATR 0.03-10%, momentum in both directions, leverage caps 10-75x).

Scenario keys the runner understands:
  sig            the window signal (None = a quiet window with no signal)
  coin           measures-file contents for the signal's coin
  scores         pace history (default: a seen day of 60s)
  args           argv overrides vs the live flags
  book_state_extra   extra state fields (trades, equity, seen...)
  exchange       FakeBitunix kwargs (max_lev, min_qty, open_positions...)
  day_offset     seconds after the 17:30 UTC day boundary for the signal bar
  age_offset     extra seconds between signal bar and the book's clock
  inject_at_start  the signal bar exists before the first poll (backlog)
  no_price       no ticker for the symbol at all
  prices_path    [(kind, pct)] applied from poll 3 on (after the open poll)
  open_px        price at the open poll (default: the signal price)
  other_px       extra symbol prices
  polls          polls budget (default 4)
  silent         no log line exists; the branch is verified structurally
  expect         "trade" | "skip"
  reason         substring the log line must carry
  stage          "signal" | "exit" | "scout"
  window_quiet   build the window without signal plots
  scout_combo / council_row / scout_ct   rows written into scout.json
  close_live_on  poll at which a live seed position disappears into history
"""
from __future__ import annotations

BAR = 900


def base_sig(side="BUY", **kw) -> dict:
    """A PEERLESS, 4-agent, wired, non-counter signal that passes every gate."""
    s = {"sym": "SCEN1USDT", "t": 0, "side": side, "counter": False,
         "span": 5, "tier": 3, "who": 3, "score": 62, "agents": 4,
         "wired": True, "res": "15",
         "bar": {"o": 1.0, "h": 1.002, "l": 0.999, "c": 1.0},
         "px": 1.0}
    s.update(kw)
    return s


def base_coin(**kw) -> dict:
    """Coin measurements that earn a full entry score on the live flags."""
    c = {"atr": 4.5, "trend": 2.0, "vol20": 0.8, "mom6h": 3.0,
         "mom1h": -0.5, "volx": 1.3, "lev_cap": 50}
    c.update(kw)
    return c


def base_scores(clock_start: float, vals=None) -> list[dict]:
    """A day that has been looked at: 12 scores over the last three hours."""
    vals = vals if vals is not None else [60] * 12
    out = []
    for k, v in enumerate(vals):
        out.append({"at": clock_start - (11 - k) * 900, "sym": f"S{k}",
                    "score": float(v)})
    return out


def day_start(now: float) -> float:
    import pace
    return pace.day_start(now)


def book_state(clock_start: float, **kw) -> dict:
    st = {"equity": 100.0, "start": 100.0, "trades": [], "seen": [],
          "resting": [], "scores": base_scores(clock_start),
          "known_syms": []}
    st.update(kw)
    return st


def open_trade(sym="OTHERUSDT", side="SELL", margin=50.0, opened=None,
               notional=None, entry=1.0, lev=40.0, **kw) -> dict:
    n = notional if notional is not None else margin * lev
    t = {"sym": sym, "side": side, "qty": n / entry, "entry": entry,
         "tp": entry * 0.95 if side == "SELL" else entry * 1.05,
         "sl": entry * 1.0125 if side == "SELL" else entry * 0.9875,
         "opened": opened, "bar": 0, "who": 0, "tier": 0, "score": 0,
         "agents": 0, "margin": margin, "notional": n, "closed": None,
         "exit": None, "reason": "", "entry_kind": "market",
         "counter": False, "entry_err_r": 0.0, "pnl": 0.0,
         "live": False, "order_id": "", "client_id": "", "position_id": "",
         "best_pct": 0.0, "moved_to_be": False}
    t.update(kw)
    return t


def lost_today(pnl: float = -10.0) -> dict:
    return {"sym": "LOSSUSDT", "side": "BUY", "qty": 40.0, "entry": 1.0,
            "tp": 1.05, "sl": 0.9875, "opened": 0, "bar": 0,
            "who": 0, "tier": 0, "score": 0, "agents": 0, "margin": 50.0,
            "notional": 2000.0, "closed": 0, "exit": 0.9875,
            "reason": "stop", "entry_kind": "market", "counter": False,
            "entry_err_r": 0.0, "pnl": pnl, "live": False, "order_id": "",
            "client_id": "", "position_id": "", "best_pct": 0.0,
            "moved_to_be": False}


SCENARIOS: list[dict] = []


def sc(name: str, branch: str, **kw) -> dict:
    d = {"name": name, "branch": branch}
    d.update(kw)
    SCENARIOS.append(d)
    return d


sc("filter_refused", "filter_model",
   stage="signal", expect="skip",
   sig=base_sig(), coin=base_coin(trend=None),
   filter_min=0.9,
   reason="filter: the model")

sc("baseline_trade", "trade_now",
   stage="signal", expect="trade",
   sig=base_sig(), coin=base_coin(trend=None),
   reason="OPEN")


# ---------------------------------------------------------------------------
# Signal-gate branches
# ---------------------------------------------------------------------------

sc("agents_below_floor", "sig_few_agents",
   stage="signal", expect="skip",
   sig=base_sig(agents=2), reason="agents")

sc("orange_colour", "sig_colour_orange",
   stage="signal", expect="skip",
   sig=base_sig(who=2), reason="colour")

sc("tesla_unwired", "sig_unwired_tesla",
   stage="signal", expect="skip",
   args={"--require-wired": True},
   sig=base_sig(wired=False), reason="Tesla unwired")

sc("tier_skipped", "sig_skip_tier",
   stage="signal", expect="skip",
   args={"--skip-tier": "3"},
   sig=base_sig(tier=3), reason="tier 3")

sc("fat_body", "sig_fat_body",
   stage="signal", expect="skip",
   args={"--max-body": "0.7"},
   sig=base_sig(bar={"o": 1.0, "h": 1.05, "l": 0.999, "c": 1.049}),
   reason="body")

sc("thin_coin", "sig_thin_coin",
   stage="signal", expect="skip",
   sig=base_sig(),
   coin=base_coin(atr=1.2),
   reason="thin_coin: ATR")

sc("against_trend", "sig_against_trend",
   stage="signal", expect="skip",
   sig=base_sig(side="BUY"),
   coin=base_coin(trend=-3.5),
   reason="against_trend")

sc("low_leverage", "sig_low_leverage",
   stage="signal", expect="skip",
   args={"--min-lev": "75"},
   sig=base_sig(),
   coin=base_coin(lev_cap=50),
   exchange={"max_lev": 50},
   reason="low_leverage")

sc("poor_score", "sig_poor_score",
   stage="signal", expect="skip",
   sig=base_sig(agents=3, score=30),
   coin={"atr": None, "trend": None, "vol20": None, "mom6h": None,
         "mom1h": None, "volx": None, "lev_cap": 50},
   reason="scored 0 of 100, needs 40")

sc("pace_wait", "sig_pace_wait",
   stage="signal", expect="skip",
   sig=base_sig(),
   coin=base_coin(atr=4.0, mom6h=None, mom1h=None, volx=None, trend=None),
   scores=[95] * 12,
   reason="the bar is")

sc("pace_day_spent", "sig_pace_day_spent",
   stage="signal", expect="skip",
   sig=base_sig(),
   book_state_extra={"trades": [open_trade(margin=0.0, notional=0.0)
                                 for _ in range(8)]},
   reason="trades are spent")

sc("pace_early_no_sample", "sig_pace_early_no_sample",
   stage="signal", expect="skip",
   sig=base_sig(),
   day_offset=3600,
   scores=[],
   reason="too early in the day")

sc("pace_late_floor", "sig_pace_late_floor",
   stage="signal", expect="trade",
   sig=base_sig(),
   day_offset=19 * 3600,
   scores=[],
   reason="taking the floor")

sc("no_price", "sig_no_price",
   stage="signal", expect="skip",
   sig=base_sig(),
   no_price=True,
   reason="no price on Bitunix")

sc("equity_zero", "sig_equity_zero",
   stage="signal", expect="skip",
   sig=base_sig(),
   book_state_extra={"equity": 0.0},
   silent=True)

sc("loss_limit_halt", "sig_loss_limit",
   stage="signal", expect="skip",
   args={"--daily-loss-limit": "5"},
   sig=base_sig(),
   book_state_extra={"trades": [lost_today()]},
   reason="HALT")

sc("exposure_full", "sig_exposure_full",
   stage="signal", expect="skip",
   sig=base_sig(),
   book_state_extra={"trades": [open_trade()]},
   reason="already committed")

sc("one_per_symbol", "sig_one_per_symbol",
   stage="signal", expect="skip",
   args={"--stack": False},
   sig=base_sig(sym="OTHERUSDT"),
   book_state_extra={"trades": [open_trade(sym="OTHERUSDT", side="BUY")]},
   reason="already in OTHERUSDT")

sc("stale_age", "sig_stale_age",
   stage="signal", expect="skip",
   sig=base_sig(),
   age_offset=3 * BAR + 100,
   reason="not traded")

sc("backlog_absorbed", "sig_backlog_absorbed",
   stage="signal", expect="skip",
   sig=base_sig(),
   inject_at_start=True,
   reason="absorbed as backlog")

sc("catchup_fresh", "sig_catchup_fresh",
   stage="signal", expect="trade",
   args={"--catch-up": "200"},
   sig=base_sig(),
   inject_at_start=True,
   age_offset=-800,
   reason="still fresh")

sc("council_dropped", "sig_council_dropped",
   stage="signal", expect="skip",
   sig=None, window_quiet=True,
   scout_sym="SCEN2USDT",
   council_row={"kind": "council", "dir": 1, "entry": 1.0, "votes": 4,
                "ready": True, "members": {}},
   reason="the scout's shape")

sc("seen_duplicate", "sig_seen_duplicate",
   stage="signal", expect="skip",
   sig=base_sig(),
   seen_signal_key=True,
   silent=True)

sc("order_under_min_qty", "live_preflight_failed",
   stage="signal", expect="skip",
   args={"--live": True, "--equity": "12"},
   sig=base_sig(),
   exchange={"min_qty": 1000000.0},
   reason="below the exchange minimum")

sc("live_cap", "sig_live_cap",
   stage="signal", expect="skip",
   args={"--live": True, "--max-live": "1"},
   sig=base_sig(),
   book_state_extra={"trades": [open_trade(margin=0.0, notional=0.0,
                                            live=True)]},
   exchange={"open_positions": [{"symbol": "OTHERUSDT", "side": "SELL",
                                 "qty": "25", "positionId": "p9"}]},
   reason="live cap of 1 positions reached")


# ---------------------------------------------------------------------------
# Entry-mode branches
# ---------------------------------------------------------------------------

sc("edge_filled", "rest_filled_at_level",
   stage="signal", expect="trade",
   args={"--entry": "edge"},
   sig=base_sig(), coin=base_coin(trend=None),
   prices_path=[("down", 0.3)],
   reason="at the candle edge")

sc("edge_dropped", "rest_expired_dropped",
   stage="signal", expect="skip",
   args={"--entry": "edge", "--fill-bars": "1"},
   sig=base_sig(), coin=base_coin(trend=None),
   polls=500,
   prices_path=[("up", 0.4)],
   reason="never returned to")

sc("back_late_market", "rest_late_market_fallback",
   stage="signal", expect="trade",
   args={"--entry": "back", "--fill-bars": "1"},
   sig=base_sig(), coin=base_coin(trend=None),
   polls=500,
   prices_path=[("up", 0.4)],
   reason="at market")

sc("smart_hunt", "rest_smart_hunt",
   stage="signal", expect="trade",
   args={"--entry": "smart"},
   sig=base_sig(), coin=base_coin(trend=None),
   prices_path=[("down", 0.3)],
   reason="hit the level")

sc("extreme_filled", "rest_filled_at_level",
   stage="signal", expect="trade",
   args={"--entry": "extreme"},
   sig=base_sig(), coin=base_coin(trend=None),
   prices_path=[("down", 0.3)],
   reason="better than close")

sc("mid_filled", "rest_filled_at_level",
   stage="signal", expect="trade",
   args={"--entry": "mid"},
   sig=base_sig(), coin=base_coin(trend=None),
   prices_path=[("down", 0.3)],
   reason="better than close")

sc("no_candle", "sig_no_candle",
   stage="signal", expect="skip",
   args={"--entry": "edge"},
   sig=None, window_quiet=True,
   scout_sym="SCEN2USDT",
   scout_combo={"kind": "combo", "side": "BUY", "span": 5, "tier": 3,
                "who": 3, "score": 62, "agents": 4, "wired": True},
   reason="no candle for the signal bar")

sc("max_entry_r", "sig_max_entry_r",
   stage="signal", expect="skip",
   args={"--max-entry-r": "0.5", "--interval": "3.0"},
   sig=base_sig(),
   open_px=1.02,
   reason="from the signal price")


# ---------------------------------------------------------------------------
# Exit branches: the trade opens now, then price does each thing.
# ---------------------------------------------------------------------------

def exit_scenario(name, branch, args, moves, reason, **kw):
    # The book's own ticker-freshness gate skips refetches under 2.5s, so
    # the poll cadence is 3s: every poll sees the price the scenario set.
    args = {"--interval": "3.0", **args}
    sc(name, branch, stage="exit", expect="trade",
       args=args, sig=base_sig(), coin=base_coin(trend=None),
       prices_path=moves, reason=reason, **kw)


exit_scenario("exit_target", "exit_target", {},
              [("up", 5.5)], "TARGET")
exit_scenario("exit_stop", "exit_stop", {},
              [("down", 1.6)], "STOP")
exit_scenario("exit_flat", "exit_flat", {"--break-even": "1.0"},
              [("up", 1.4), ("down", 1.5)], "FLAT")
exit_scenario("exit_counter", "exit_counter", {"--ct-exit": "3.5"},
              [("up", 4.2), ("flat", 0.0)], "COUNTER", scout_ct=-1)
exit_scenario("exit_liquidated", "exit_liquidated",
              {"--lev": "100", "--per-coin-lev": False},
              [("down", 0.7)], "LIQUIDATED",
              exchange={"max_lev": 125})
exit_scenario("exit_stalled", "exit_stalled",
              {"--stale-min": "10", "--stale-r": "0.5", "--interval": "601"},
              [("flat", 0.2)], "STALLED")
exit_scenario("exit_timeout", "exit_timeout",
              {"--max-hold-min": "10", "--interval": "601"},
              [("flat", 0.2)], "TIMEOUT")

sc("exit_yielded", "exit_yielded",
   stage="exit", expect="trade",
   args={"--priority": "SCEN1USDT"},
   sig=base_sig(), coin=base_coin(trend=None),
   book_state_extra={"trades": [open_trade()]},
   other_px={"OTHERUSDT": 1.0},
   prices_path=[("flat", 0.0)],
   reason="YIELD")

sc("exit_live_reconciled", "exit_live_reconciled",
   stage="exit", expect="skip",
   args={"--live": True},
   sig=None, window_quiet=True,
   book_state_extra={"trades": [open_trade(margin=0.0, notional=0.0,
                                            live=True, position_id="p9")]},
   exchange={"open_positions": [{"symbol": "OTHERUSDT", "side": "SELL",
                                 "qty": "25", "positionId": "p9"}]},
   close_live_on=1,
   reason="LIVE CLOSED")


# ---------------------------------------------------------------------------
# Fill-site branches: an order rests, the book's state changes while it
# waits, and the fill is then refused. These need two windows -- the second
# position has to OPEN between the rest and the fill, which is the only way
# the book's state can change in that gap. Run by run_rest_fill_scenario.
# ---------------------------------------------------------------------------

REST_FILL_MODES = {
    "exposure": {"branch": "rest_fill_missed_exposure",
                 "reason": "exposure full",
                 "args": {"--entry": "edge", "--skip-window": None},
                 "stop_b": False},
    "day_full": {"branch": "rest_fill_skipped_day_full",
                 "reason": "the day's count is spent",
                 "args": {"--entry": "edge", "--max-per-day": "1",
                          "--per-day": "0", "--skip-window": None},
                 "stop_b": False},
    "loss_limit": {"branch": "rest_fill_skipped_loss_limit",
                   "reason": "the day's loss limit is reached",
                   "args": {"--entry": "edge", "--daily-loss-limit": "5",
                            "--skip-window": None},
                   "stop_b": True},
    "one_per_symbol": {"branch": "rest_fill_skipped_one_per_symbol",
                       "reason": "",
                       "args": {"--entry": "edge", "--stack": False,
                                "--skip-window": None},
                       "same_sym": True,
                       "stop_b": False},
}
