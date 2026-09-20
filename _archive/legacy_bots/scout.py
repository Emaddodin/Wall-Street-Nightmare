#!/usr/bin/env python3
"""
The scout: one chart window, many coins.

Two windows is not a strategy limit, it is an eyesight limit. Each coin offers
a setup every few days, so two of them produce a trade a fortnight while the
strategy is perfectly capable of three or four a day -- there was simply
nothing looking at the other six hundred coins.

So one window stops holding a coin and starts walking the ranked list instead,
a few seconds each. Every sweep it reads the council off the indicator exactly
as the book does, and when a coin is carrying a plan it writes it down. The
book reads that file and rests its order there. The chart the plan was found on
has already moved on -- it does not need to stay, because the plan carries its
own entry level and the exchange carries the price.

Windows the book holds a position on are left alone. A scout that wanders off
a live trade is how a position becomes invisible.
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# The engine's root is wherever this file is, which on the server is
# /home/tbt/bot and everywhere else is the checkout. It was written out
# literally, so importing this module anywhere else raised before the first
# statement ran -- which is why nothing in here had ever been exercised off
# the server, and why an os.chdir to a directory that may not exist was the
# first thing the process did.
BOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BOT))
os.chdir(BOT)
from signals.tv_cdp import TradingViewCDP           # noqa: E402
from papertrade import (unpack_state, unpack_votes, expansion,   # noqa: E402
                        body_share, wick_risk, leg_of,
                        run_strength, breakout, trend_ride,
                        coiling, ripeness, coin_reach,
                        _stop_room, _SHAPE, BARS_JS)

log = logging.getLogger("scout")
# How many closed candles back a shape may be and still count. Six bars of
# fifteen minutes is an hour and a half -- long enough to cover the time this
# window spends away on other coins, short enough that the level is still the
# level.
LOOKBACK = 6

# Only the extreme end of the scale is worth anything: above this a coil was
# followed by a ten percent move more than twice as often as a bar picked at
# random, and below it the reading was indistinguishable from noise.
COIL_MIN = 65.0

# A coin one module short of the indicator's own threshold, with the tide
# behind it and the council already leaning. Below this the count is not
# saying anything useful yet.
RIPE_MIN = 70.0

# How many sweeps to keep circling a ripe coin before letting it go. The
# whole point is to be standing over it when the signal prints rather than
# arriving three minutes later on the next pass.
PERCH_SWEEPS = 4

# The chart's own candles, with volume -- the coil needs it and BARS_JS does
# not carry it.
BARS_VOL_JS = """
(function(){try{var d=window.TradingViewApi.activeChart().getSeries().data();
var rows=[];d.each(function(i,v){var a=v&&v.value?v.value:v;
if(a&&a.length>=5)rows.push([a[0],a[1],a[2],a[3],a[4],a.length>5?a[5]:null]);
return false;});return JSON.stringify(rows);}catch(e){return "[]";}})()
"""


def readiness_of(row: dict) -> float:
    """The readiness score, shared by the scout and the perch.

    One formula, two users, so the eyesight and the perch can never
    disagree about which coin matters most:
      one module short: 30% print in 2h; two: 19.2% (0.64); three: 0.40
      tide with the print: 8x the print rate of tide against (0.12)
      expansion >= 2x: 36.6% chance of a 5% move in 8 bars (9x quiet)
    """
    short = int(row.get("short", 9))
    p_short = 1.0 if short <= 1 else 0.64 if short == 2 \
        else 0.40 if short == 3 else 0.25
    side = int(row.get("side", 0))
    tide = int(row.get("tide", 0))
    p_tide = 1.0 if tide == side else 0.12 if tide == -side else 0.5
    p_move = min(1.0, float(row.get("exp") or 0.0) / 2.0)
    return p_short * p_tide * p_move


def walk_order(coins: list[str], perched: list[str],
               exp_rank: dict[str, float],
               ripe_rank: dict[str, float] | None = None) -> list[str]:
    """The sweep order: perched coins circle first, then the ripest by the
    SAME readiness the perch uses, then the rest by how much they are
    EXPANDING -- measured, an expansion of 2x carries a 36.6% chance of a
    5% move in eight bars against 4.2% quiet, so the eyesight goes where
    the move is brewing."""
    rest = [c for c in coins if c not in perched]
    ripe_rank = ripe_rank or {}
    rest.sort(key=lambda c: (-(ripe_rank.get(c, 0.0) or 0.0),
                             -(exp_rank.get(c, 0.0) or 0.0), c))
    return list(perched) + rest


def dwell_for(exp, base: float) -> float:
    """Seconds to let a coin load: the expanding ones get the long look,
    the quiet ones a glance -- and nothing less than a second."""
    if exp is None:
        return base
    if exp >= 2.0:
        return base * 1.5
    if exp < 1.0:
        return max(1.0, base * 0.5)
    return base


FOUND = BOT / "data" / "scout.json"
RANKED = BOT / "data" / "boom.json"


WATCH = BOT / "data" / "watchlist.json"


def ranked(limit: int) -> list[str]:
    """What to walk: the watchlist if there is one, else the shortlist.

    The shortlist is what the book will trade, which is a much smaller thing
    than what is worth watching -- and walking only the shortlist is why this
    was looking at nine coins while six hundred went unwatched.

    Any non-empty watchlist wins. It used to need ten names before it counted,
    a guard against reading a file mid-write, and that guard outlived the
    problem: the scanner writes atomically now. What it did instead was hide a
    replacement. The ATR finder deliberately keeps only the coins whose
    candles are big enough to reach the target, and on a calm morning that is
    eight names -- so the scout silently fell back to a ranking written by a
    scanner that had been switched off, and spent the morning walking coins
    nobody had chosen. A fallback that fires quietly is worse than no
    fallback, so this one says so.
    """
    try:
        got = [str(x) for x in json.loads(WATCH.read_text()) if x]
        if got:
            return got[:limit]
        log.warning("the watchlist is empty -- falling back to the shortlist")
    except Exception as e:
        log.warning("the watchlist will not read (%s) -- falling back to the "
                    "shortlist", str(e)[:60])
    try:
        return [r["sym"] for r in json.loads(RANKED.read_text())][:limit]
    except Exception:
        return []


def read_combo(c) -> dict | None:
    """The indicator's own signal on this coin, off its own plots.

    The scout walks the whole watchlist; the book follows two windows. On the
    council and shape sources that gap did not matter, because the scout wrote
    down what it found and the book read the note. On --source combo it
    mattered completely: the book could only ever see the indicator on the two
    coins in front of it, so eleven coins were walked every forty seconds and
    the book evaluated nothing for an hour.

    Nothing here re-derives a signal. TBT_BUY_SCORE and its tier, span and
    agent count are what the indicator publishes, and they are what measured:
    over 163 of these on the highest-ATR coins, four agents or more reached
    +10% 35.4% of the time against 19.4% at three.

    Only the last CLOSED bar is read. The indicator's confirmOnly input is off
    by default, so a signal can appear inside a forming candle and be gone by
    its close; acting on the forming bar would trade prints that never
    existed.
    """
    sid = c.find_study("TBT")
    if not sid:
        return None
    r = c.raw_series(sid, limit=3)
    if not r or not r.get("rows") or len(r["rows"]) < 2:
        return None
    at = {p_: i + 1 for i, p_ in enumerate(r["plots"])}
    if "TBT_BUY_SCORE" not in at or "TBT_SELL_SCORE" not in at:
        return None
    row = r["rows"][-2]                      # the last closed bar

    def v(name):
        i = at.get(name)
        return row[i] if i is not None and i < len(row) else None

    # The counter-trend print, carried alongside. It is not a trade on this
    # source -- it is the warning that the move an open position is riding is
    # being taken back, and the book uses it to leave with a profit rather
    # than watch the target slip away.
    ct = 0
    if v("TBT_CTBUY_SPAN") is not None:
        ct = 1
    elif v("TBT_CTSELL_SPAN") is not None:
        ct = -1

    # The candle the signal fired on, off the chart's own bars. The book's
    # --entry edge needs the extreme (the low for a long, the high for a
    # short), and a scout row without it was dropped at "no candle for the
    # signal bar" every single time -- so every scout signal was unfillable
    # under edge entry while nothing anywhere said why.
    t_sig = int(row[0]) // (1000 if int(row[0]) > 1e12 else 1)
    bar = None
    try:
        for b in json.loads(c.evaluate(BARS_JS)):
            if int(b[0]) == t_sig:
                bar = {"o": float(b[1]), "h": float(b[2]),
                       "l": float(b[3]), "c": float(b[4])}
                break
    except Exception:
        pass

    for side, sc, tier, span, vote in (
            ("BUY", "TBT_BUY_SCORE", "TBT_BUY_TIER", "TBT_BUY_SPAN",
             "SNIP_BUY_VOTE"),
            ("SELL", "TBT_SELL_SCORE", "TBT_SELL_TIER", "TBT_SELL_SPAN",
             "SNIP_SELL_VOTE")):
        s_ = v(sc)
        if s_ is None or s_ != s_ or s_ <= 0:
            continue
        return {
            "kind": "combo",
            "t": t_sig,
            "side": side,
            "dir": 1 if side == "BUY" else -1,
            "score": int(s_),
            "tier": int(v(tier) or 0),
            "span": int(v(span) or 0),
            "agents": int(v(vote) or 0),
            "wired": bool(v("TSL_WIRED")),
            "who": int(v(f"TBT_{side}_LAST") or 0),
            "ct": ct,
            "bar": bar,
        }
    # No signal of our own, but a counter-trend print is still worth writing
    # down: an open position elsewhere in the book may be riding this coin.
    if ct:
        return {
            "kind": "combo", "ct_only": True, "ct": ct,
            "t": int(row[0]) // (1000 if int(row[0]) > 1e12 else 1),
            "side": "BUY" if ct == 1 else "SELL",
            "dir": ct, "score": 0, "tier": 0, "span": 0, "agents": 0,
            "wired": bool(v("TSL_WIRED")), "who": 0,
        }
    return None


def read_state(c) -> dict | None:
    """What the indicator is saying right now, signal or not.

    read_combo() only answers when a tradeable signal is printing, which is
    most of the time nothing. That is right for trading and useless for
    studying a trade: the interesting minute is usually the one where the
    indicator quietly changed its mind, and there is no signal on that bar to
    carry the news.

    So this reads the state on every sweep -- the council's own vote, whether
    it is carrying a plan, the higher timeframe's bias, and any counter-trend
    print. Written under its own kind so the book never mistakes it for
    something to trade.
    """
    sid = c.find_study("TBT")
    if not sid:
        return None
    r = c.raw_series(sid, limit=3)
    if not r or not r.get("rows") or len(r["rows"]) < 2:
        return None
    at = {p_: i + 1 for i, p_ in enumerate(r["plots"])}
    row = r["rows"][-2]                      # the last closed bar

    def v(name):
        i = at.get(name)
        return row[i] if i is not None and i < len(row) else None

    ct = 1 if v("TBT_CTBUY_SPAN") is not None else (
        -1 if v("TBT_CTSELL_SPAN") is not None else 0)
    st = v("STATE")
    d = unpack_state(st) if st is not None else {}
    htf = v("HTF_BIAS")
    t = int(row[0]) // (1000 if int(row[0]) > 1e12 else 1)
    return {
        "kind": "state",
        "t": t,
        "ct": ct,
        "votes": d.get("votes"),
        "plan_dir": d.get("plan_dir"),
        "ready": d.get("ready"),
        "htf": int(htf) if htf is not None else None,
        "wired": bool(v("TSL_WIRED")),
        "buy_vote": v("SNIP_BUY_VOTE"),
        "sell_vote": v("SNIP_SELL_VOTE"),
        "exp": _expansion_at(c, t),
    }


def read_break(c, sym: str | None = None) -> dict | None:
    """The band-and-break picture, read off the candles the chart already has.

    Nothing here asks the indicator anything. The shape is in the candles, and
    the council does not see it -- on the screenshot it reads "no plan" while
    the trade in front of it made ten percent.
    """
    ohlc = {}
    try:
        for b in json.loads(c.evaluate(BARS_JS)):
            ohlc[int(b[0])] = (float(b[1]), float(b[2]), float(b[3]),
                               float(b[4]))
    except Exception:
        return None
    if not ohlc:
        return None
    # The last several CLOSED candles, newest first -- not just the most
    # recent one. A shape is a statement about a finished bar, so the forming
    # one is never asked; but looking only at the newest closed bar meant a
    # shape that formed while this window was away visiting other coins was
    # never seen at all. The manual check found three shapes on coins this
    # swept twenty-five times and reported nothing on.
    #
    # Nothing is loosened by looking further back. The entry is a resting
    # order at the level, and a level that broke half an hour ago is still
    # the same level at the same price -- if it has been reclaimed since,
    # breakout() will not return it anyway.
    _ks = sorted(ohlc)
    if len(_ks) < 3:
        return None
    room = _stop_room()
    # The coin's own movement, so its level is judged on its own scale. A
    # coin that travels keeps breaking and rebuilding its levels, and a
    # tolerance set for a quiet one never sees them at all.
    rr = coin_reach(sym)[0] if sym else None
    got, shape, t = None, None, None
    for cand in reversed(_ks[-(LOOKBACK + 1):-1]):
        g = breakout(ohlc, cand, max_stop=room, reach=rr,
                     min_touches=_SHAPE["min_touches"])
        sh = "break"
        if not g:
            g = trend_ride(ohlc, cand, max_stop=room)
            sh = "trend"
        if g:
            got, shape, t = g, sh, cand
            break
    if not got:
        return None
    got["t"] = t
    got["kind"] = "break"
    got["shape"] = shape
    # The council's lean, attached here because the chart is in front of us and
    # will not be by the time the book reads this. It is a quarter of the
    # reading, and a shape arriving without it scored ten points below an
    # identical shape found on a window the book happens to be holding -- so
    # everything the scout found sat under the floor for a reason that had
    # nothing to do with the setup.
    try:
        st = [x for x in c.studies() if "TBT" in x["name"]]
        if st:
            # limit must reach back past LOOKBACK bars: the shape may sit
            # several closed candles behind the newest one.
            r = c.raw_series(st[0]["id"], limit=LOOKBACK + 4)
            rows = (r or {}).get("rows") or []
            if rows:
                at = {q: i + 1 for i, q in enumerate(r["plots"])}
                # the closed bar the shape was found on -- not the newest
                # one, which may have voted differently
                _row = next((x for x in rows if int(x[0]) == t), None)
                if _row is None:
                    _row = rows[-1]
                pv = _row[at["VOTES_PACKED"]] if "VOTES_PACKED" in at else None
                if pv is not None and pv == pv:
                    v = unpack_votes(pv)
                    want = 1 if got["side"] == "BUY" else -1
                    got["agree"] = sum(1 for x in v.values() if x == want)
                    got["against"] = sum(1 for x in v.values() if x == -want)
    except Exception:
        pass
    return got


def belongs_to(c, sym: str) -> bool:
    """Is what the study is publishing actually this coin's?

    The window is being walked faster now, and a short dwell risks reading the
    previous coin's numbers under the new coin's name -- a silent, confident,
    completely wrong answer, which is the worst kind this engine produces.
    So the series is asked whose it is before anything is read off it, and a
    mismatch means skip rather than guess.
    """
    try:
        st = [x for x in c.studies() if "TBT" in x["name"]]
        if not st:
            return False
        r = c.raw_series(st[0]["id"], limit=2)
        got = str((r or {}).get("symbol") or "")
        return got.split(":")[-1].replace(".P", "").upper() == sym.upper()
    except Exception:
        return False


def _expansion_at(c, t) -> float | None:
    """The last closed candle's range against the coin's own normal.

    Measured over 9,135 recorded bars: an expansion of 2x or more carries a
    36.6% chance of a 5% move within the next two hours, against 4.2% when
    the band is quiet -- this is the shape side of "where the eagle sits".
    """
    try:
        ohlc = {}
        for b in json.loads(c.evaluate(BARS_JS)):
            ohlc[int(b[0])] = (float(b[1]), float(b[2]), float(b[3]),
                               float(b[4]))
        return expansion(ohlc, t)
    except Exception:
        return None


def read_ripe(c) -> dict | None:
    """How close this coin is to the indicator printing, from its own counters.

    Section 5 of the Pine builds a signal out of a running total: how many of
    Bank, Team45 and Tesla have fired lately, plus the two standing agents.
    That total is published on every bar, which means a coin sitting one
    module short of the threshold is one module away from a signal -- and that
    is knowable now, not after the print.

    Everything before this waited for the print and then reacted. This is the
    other half: the count that produces the print, read while it is still
    climbing.
    """
    st = [x for x in c.studies() if "TBT" in x["name"]]
    if not st:
        return None
    r = c.raw_series(st[0]["id"], limit=4)
    rows = (r or {}).get("rows") or []
    if not rows:
        return None
    at = {q: i + 1 for i, q in enumerate(r["plots"])}
    row = rows[-2] if len(rows) > 1 else rows[-1]
    g = ripeness(row, at)
    if not g or not g.get("side") or g["ripe"] < RIPE_MIN:
        return None
    g["t"] = int(row[0])
    g["kind"] = "ripe"
    g["side_txt"] = "BUY" if g["side"] == 1 else "SELL"
    g["exp"] = _expansion_at(c, g["t"])
    return g


def read_coil(c) -> dict | None:
    """A coin winding up, before anything has broken.

    Everything else here reacts to a break. This looks a step earlier: the
    conditions that precede a move, so the book can have an order resting on
    both sides of the coil rather than chasing whichever way it goes.

    Measured over fifteen and a half thousand readings on twenty coins, a
    pressure above sixty-five was followed by a ten percent move within eight
    hours 52.5% of the time against a base rate of 23.5% -- and everything
    below sixty-five was indistinguishable from noise. So only the extreme
    end is reported; the middle of the scale knows nothing.
    """
    ohlc, vol = {}, {}
    try:
        for b in json.loads(c.evaluate(BARS_VOL_JS)):
            ohlc[int(b[0])] = (float(b[1]), float(b[2]), float(b[3]),
                               float(b[4]))
            if len(b) > 5 and b[5] is not None:
                vol[int(b[0])] = float(b[5])
    except Exception:
        return None
    if len(ohlc) < 90:
        return None
    keys = sorted(ohlc)
    t = keys[-2] if len(keys) > 1 else keys[-1]
    g = coiling(ohlc, t, vol or None)
    if not g or g["pressure"] < COIL_MIN:
        return None
    g["t"] = t
    g["kind"] = "coil"
    return g


def read_council(c) -> dict | None:
    st = [x for x in c.studies() if "TBT" in x["name"]]
    if not st:
        return None
    r = c.raw_series(st[0]["id"], limit=3)
    rows = (r or {}).get("rows") or []
    if not rows:
        return None
    at = {p: i + 1 for i, p in enumerate(r["plots"])}
    row = rows[-1]

    def v(name):
        i = at.get(name)
        if i is None or i >= len(row):
            return None
        x = row[i]
        return None if x is None or x != x else x

    sv = v("STATE")
    if sv is None:
        return None
    d = unpack_state(sv)
    if not d["plan_dir"]:
        return None
    entry = v("PLAN_ENTRY")
    if entry is None:
        return None
    # Measured here, not later. The book judges a plan on the shape of the
    # candle that produced it and on the leg the entry retraced, and by the
    # time the book sees this the window has walked on to another coin. A plan
    # that arrives without its measurements would silently skip every candle
    # test the engine has.
    ohlc = {}
    try:
        for b in json.loads(c.evaluate(BARS_JS)):
            ohlc[int(b[0])] = (float(b[1]), float(b[2]), float(b[3]),
                               float(b[4]))
    except Exception:
        pass
    t = int(row[0])
    _d, _e, span, _base = leg_of(ohlc, t)
    return {"t": t, "dir": d["plan_dir"], "votes": d["votes"],
            "ready": d["ready"], "entry": float(entry),
            "members": unpack_votes(v("VOTES_PACKED") or 0),
            "expansion": expansion(ohlc, t),
            "body": body_share(ohlc, t),
            # The live stop is 1.25%; the wick measure must answer the
            # question the book actually asks, not a 1.0% straw man.
            "wick": wick_risk(ohlc, "BUY" if d["plan_dir"] == 1 else "SELL",
                              1.25, t=t),
            "run": run_strength(ohlc, t,
                                "BUY" if d["plan_dir"] == 1 else "SELL"),
            "leg": span}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--window", type=int, default=1,
                    help="which chart window to walk (the other one is left "
                         "for the book to hold positions on)")
    ap.add_argument("--top", type=int, default=40,
                    help="how many of the ranked coins to walk")
    ap.add_argument("--dwell", type=float, default=6.0,
                    help="seconds to let each coin load and compute")
    ap.add_argument("--keep-min", type=float, default=45.0,
                    help="how long a found plan stays on the list")
    ap.add_argument("--min-touches", type=int, default=2, metavar="N",
                    help="how many times a price must have been respected "
                         "before it counts as a level. Must match the book's "
                         "--min-touches: the scout finds the shape and the "
                         "book judges it, and two different answers to the "
                         "same question means the scout offers shapes the "
                         "book will not take.")
    a = ap.parse_args()
    _SHAPE["min_touches"] = a.min_touches
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    found: dict[str, dict] = {}
    _perch: dict[str, int] = {}
    while True:
        coins = ranked(a.top)
        if not coins:
            log.warning("no ranked coins to walk -- waiting for the scanner")
            time.sleep(60)
            continue
        seen = plans = 0
        # Ripe coins first, and again, until they either print or go cold.
        # Walking past a coin that is one module from a signal and coming
        # back three minutes later is how the print gets missed.
        # The previous sweep's expansion readings: the walk order and the
        # dwell follow them, so the eyesight spends itself where the move
        # is brewing.
        exp_rank: dict[str, float] = {}
        ripe_rank: dict[str, float] = {}
        for k, row in found.items():
            e = row.get("exp")
            if e is not None and "state" in str(k):
                exp_rank.setdefault(str(row.get("sym") or "").upper(),
                                    float(e))
            if "ripe" in str(k):
                sym = str(row.get("sym") or "").upper()
                # The freshest reading wins, so a coin the scout just
                # visited cannot be ranked on a stale picture.
                cur = ripe_rank.get(sym)
                if cur is None or readiness_of(row) > cur:
                    ripe_rank[sym] = readiness_of(row)
        perched = [k for k, v in list(_perch.items()) if v > 0]
        for k in list(_perch):
            _perch[k] -= 1
            if _perch[k] <= 0:
                _perch.pop(k, None)
        if perched:
            log.info("circling %s", ", ".join(perched))
        coins = walk_order(coins, perched, exp_rank, ripe_rank)
        for sym in coins:
            # Opened outside the try so the finally below can always close it.
            # Every failure inside the sweep -- a chart that will not load, a
            # study still on the last coin, a dropped socket -- used to leave
            # the websocket open and jump to the next coin. One connection per
            # coin per sweep, a hundred and fifty coins a sweep, all night:
            # the file descriptors run out and every read starts failing for a
            # reason that has nothing to do with the market.
            c = None
            try:
                c = TradingViewCDP(target_index=a.window)
                c.set_symbol(f"BITUNIX:{sym}.P")
                # The chart is looked at by a person as well as read by the
                # book. TradingView keeps its own spacing across a symbol
                # change and drifts wide, which turns thirty hours of candles
                # into a line nobody can read.
                # The price axis has to fit the candles and nothing else:
                # the indicator's packed integers are plots too, and with
                # scaleSeriesOnly off the scale stretches to hold a value of
                # 1,345 on a coin trading at 0.139, flattening every candle.
                try:
                    c.evaluate("(function(){try{var a=window.TradingViewApi"
                               ".activeChart();a.setBarSpacing(9);"
                               "a._chartWidget.properties().childs()"
                               ".scalesProperties.childs().scaleSeriesOnly"
                               ".setValue(true);}catch(e){}})()")
                except Exception:
                    pass
                dwell = dwell_for(exp_rank.get(sym), a.dwell)
                ok = False
                for _ in range(int(dwell / 0.5) + 8):
                    time.sleep(0.5)
                    if c.state().symbol.endswith(f"{sym}.P"):
                        ok = True
                        break
                if ok:
                    time.sleep(dwell)
                    # The chart says it has moved; the study has to agree
                    # before a single number is read off it.
                    if not belongs_to(c, sym):
                        time.sleep(a.dwell)
                        if not belongs_to(c, sym):
                            log.debug("%s: the study is still on the last "
                                      "coin -- skipped rather than misread",
                                      sym)
                            c.close()
                            continue
                    seen += 1
                    got = read_council(c)
                    if got:
                        got["sym"] = sym
                        got["at"] = time.time()
                        got["kind"] = "council"
                        found[sym] = got
                        plans += 1
                        who = ",".join(k for k, x in got["members"].items()
                                       if x == got["dir"])
                        log.info("%s carries a %s plan %d/6 at %.8g [%s]", sym,
                                 "long" if got["dir"] == 1 else "short",
                                 got["votes"], got["entry"], who)
                    # The picture, which the council cannot see. Kept under its
                    # own key so one coin can offer both at once without either
                    # overwriting the other.
                    # One module from a print. Not a trade -- a reason to
                    # stay over this coin instead of walking on.
                    rp = read_ripe(c)
                    if rp:
                        rp["sym"] = sym
                        rp["at"] = time.time()
                        found[f"{sym}:ripe"] = rp
                        _perch[sym] = PERCH_SWEEPS
                        log.info("%s is %d module(s) from a %s print: "
                                 "vote %d, tide %s, %d of six already leaning "
                                 "(ripe %.0f)", sym, rp["short"],
                                 rp["side_txt"], rp["vote"],
                                 "with" if rp["tide"] == rp["side"] else
                                 "against", rp["lean"], rp["ripe"])
                    # A coin winding up. Not a trade yet -- a warning that
                    # one is coming, and where its edges are.
                    co = read_coil(c)
                    if co:
                        co["sym"] = sym
                        co["at"] = time.time()
                        found[f"{sym}:coil"] = co
                        log.info("%s is winding up: pressure %.0f, band "
                                 "%.2f%% wide between %.8g and %.8g "
                                 "(squeeze %.2f pinch %.2f dry %.2f)", sym,
                                 co["pressure"], co["width"], co["bottom"],
                                 co["top"], co["squeeze"], co["pinch"],
                                 co["dry"])
                    stt = read_state(c)
                    if stt:
                        stt["sym"] = sym
                        stt["at"] = time.time()
                        found[f"{sym}:state"] = stt
                    cb = read_combo(c)
                    if cb:
                        cb["sym"] = sym
                        cb["at"] = time.time()
                        found[f"{sym}:combo"] = cb
                        plans += 1
                        log.info("%s: the indicator says %s -- %d agents, "
                                 "tier %d, score %d", sym, cb["side"],
                                 cb["agents"], cb["tier"], cb["score"])
                    br = read_break(c, sym)
                    if br:
                        br["sym"] = sym
                        br["at"] = time.time()
                        br["dir"] = 1 if br["side"] == "BUY" else -1
                        found[f"{sym}:break"] = br
                        plans += 1
                        _age = int((time.time() - br["t"]) / 60)
                        log.info("%s %s (%dm old): level %.8g %s, entry there, "
                                 "stop %.2f%% just past it (%s)", sym,
                                 br.get("shape", "?"), _age, br["level"],
                                 (f"held {br['touches']}x"
                                  if br.get("touches")
                                  else f"remade {br.get('steps', 0)} candles "
                                       f"running"),
                                 br["stop_pct"],
                                 "long" if br["side"] == "BUY" else "short")
            except Exception as e:
                log.debug("%s: %s", sym, str(e)[:60])
                continue
            finally:
                if c is not None:
                    try:
                        c.close()
                    except Exception:
                        pass
            cutoff = time.time() - a.keep_min * 60
            live = {k: x for k, x in found.items() if x["at"] >= cutoff}
            if live != found:
                found = live
            tmp = str(FOUND) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(sorted(found.values(), key=lambda x: -x["at"]), f)
            os.replace(tmp, FOUND)
        log.info("swept %d coins, %d carrying a plan, %d still fresh",
                 seen, plans, len(found))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
