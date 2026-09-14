#!/usr/bin/env python3
"""
Paper trading, live, across every TradingView chart window you have open.

Same signals and same sizing as autotrade.py, but positions are held in a local
book and marked against the real ticker instead of being sent to the exchange.
The point is a record: after a few days you have a real sample of what this
configuration does, taken forward in time rather than fitted backwards.

    python papertrade.py                    # follow every open chart
    python papertrade.py --equity 100
    python papertrade.py --colour purple blue

Yellow (Team 45 confirmed) signals are never taken -- that is not a default,
it is the rule you set, so it is enforced by leaving orange out of --colour.

State lives in data/paper.json and survives restarts, so stopping the process
does not reset the experiment.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
import logging
import os
import signal
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except Exception:
    pass
from exchange.bitunix import (BitunixClient, BitunixError,  # noqa: E402
                              BitunixUnknown)
from signals.tv_cdp import TradingViewCDP  # noqa: E402
import pace  # noqa: E402

log = logging.getLogger("paper")
WHO = {1: "blue/Bank", 2: "orange/Team45", 3: "purple/Tesla"}
COLOURS = {"blue": 1, "orange": 2, "purple": 3}
TIER = {3: "PEERLESS", 2: "EXCELLENT", 1: "GOOD"}
BOOK = Path(__file__).resolve().parent / "data" / "paper.json"
BARS_JS = """
(function(){
  try {
    var d = window.TradingViewApi.activeChart().getSeries().data();
    var rows = [];
    d.each(function(i, v){ var a = v && v.value ? v.value : v;
      if (a && a.length >= 5) rows.push([a[0],a[1],a[2],a[3],a[4]]); return false; });
    return JSON.stringify(rows);
  } catch (e) { return "[]"; }
})()
"""


@dataclass
class Pending:
    """
    A signal we have accepted but not yet filled.

    In "smart" mode this is not a resting limit -- the order will be a market
    order. It is a decision to keep watching for a few seconds and fire the
    moment price comes back toward the candle's own extreme, because with a
    0.52% stop an entry 0.5% off the low has already spent the whole trade.
    That happened once for real: an ONG long entered 1.12% above the low,
    stopped out, and the same signal entered at the low would have taken the
    target.

    Three things end the hunt: price reaches `want`, price runs away past
    `give_up`, or `deadline` passes. All three fire a market order -- the
    signal is never dropped, only priced.
    """
    sym: str
    side: str
    want: float
    give_up: float
    signal_px: float
    placed: float
    deadline: float
    expires: float
    best_seen: float = 0.0
    sig: dict = field(default_factory=dict)
    # --- set only in --live, where this is a real order on the exchange -----
    # The shape's whole premise is that the entry is the level: the stop can be
    # tight because a real break does not come back through the band. A market
    # order at the moment price touches the level does not deliver that -- it
    # crosses the spread at whatever the book offers two seconds later. So in
    # --live this rests an actual limit order AT the level, which can never
    # fill worse than it, and the book learns the fill from the exchange
    # instead of assuming it got the price it asked for.
    live: bool = False
    order_id: str = ""
    client_id: str = ""
    # How much of the order the exchange has reported filled so far. A partial
    # fill is a real position of a size nobody chose, and it has to be booked
    # at the size that exists rather than the size that was ordered.
    filled: float = 0.0
    qty: float = 0.0
    # Set once the order is known to be off the book, so a cancel is never
    # sent twice for one order.
    settled: bool = False


@dataclass
class Trade:
    sym: str
    side: str
    qty: float
    entry: float
    tp: float
    sl: float
    opened: float
    bar: int
    who: int
    tier: int
    score: int
    agents: int
    margin: float
    notional: float
    closed: float | None = None
    exit: float | None = None
    reason: str = ""
    entry_kind: str = "limit"
    counter: bool = False
    # How far the fill landed from the best price the signal bar offered, as a
    # fraction of the stop distance. 0 means we got the extreme; 1 means the
    # entry error alone equalled the whole stop, which is what turned a +2%
    # winner into a -1% loser once already.
    entry_err_r: float = 0.0
    pnl: float = 0.0
    # Set only in --live. `live` marks a trade the exchange is actually
    # holding, so reconciliation knows which rows it owns and which are paper.
    live: bool = False
    order_id: str = ""
    client_id: str = ""
    # The exchange's id for the resulting position, learned the first time the
    # position is seen open. The order id is a different number and never
    # appears in the closed-position history, so matching on it silently never
    # succeeds and the PnL falls back to an equity guess.
    position_id: str = ""
    # Best favourable move seen since entry, in percent. Only used by
    # --break-even, and kept on the trade so a restart does not forget it.
    best_pct: float = 0.0
    moved_to_be: bool = False
    # Funding accrued while the position was open, from the venue's REAL
    # rate (positive rate: longs pay, shorts receive). Kept on the trade so
    # a restart does not forget it either.
    funding: float = 0.0
    funded_at: float = 0.0


SILENT = False
# Set from --live so every notification says which money it is about.
LIVE_MODE = False

# Bars fetched per poll. Generous enough that a signal cannot slip
# through between polls, small enough that the payload is trivial.
LIVE_BARS = 20


# What to believe about the spread when the book cannot be read. The fallback
# is the same one the old code used on an exception; the ceiling is what makes
# an unreadable book look like an exception rather than like a number. Fifty
# basis points is already five times the widest spread this scanner will let a
# coin through on, so nothing legitimate is ever refused by it.
DEFAULT_SPREAD_BPS = 1.0
MAX_SPREAD_BPS = 500.0

# How long a spread reading stays good. Long enough that a burst of signals
# does not hammer the depth endpoint, short enough that a fill is priced off
# the book as it is rather than as it was when the process started.
SPREAD_TTL_S = 60.0

# How often to say why trades are or are not happening.
TALLY_EVERY_S = 900.0

# How far behind a chart's newest candle may fall before the book calls the
# feed stopped. A healthy chart is always inside one bar -- the newest is the
# one forming now. Matches the guard's own threshold.
STALE_CHART_BARS = 3

# How long a freshly placed live order is given to appear in the exchange's
# open-positions list before the reconciler is allowed to conclude it has
# closed. A market order normally shows up at once; "normally" is not a thing
# to settle a fifty-times position on.
OPEN_GRACE_S = 30.0

# A post-only order is refused when it would cross the book. For this strategy
# that is not a failure -- it is the "price is already at the level" case, and
# the answer is a plain limit at the same price, which still cannot fill worse
# than the level. Matched on the message because the venue's numeric codes for
# it are not documented anywhere we can rely on; anything unrecognised is
# treated as a real rejection rather than quietly retried without the maker
# guard.
_CROSS_HINTS = ("post only", "post_only", "postonly", "would immediately match",
                "immediately match", "would take", "maker", "cross")


def _crossing(exc: Exception) -> bool:
    return any(h in str(exc).lower() for h in _CROSS_HINTS)


# Right on the extreme.
ENTRY_OK = 0.12
# Still a fair entry: a miss worth up to this fraction of the stop.  A
# small difference is not a wrong entry -- it is a cost, and a third of
# the risk is a cost worth paying to be in the trade.
ENTRY_SLACK = 0.35


def ping(title: str, body: str, test: bool = False) -> None:
    """
    One line to the phone, when a position opens or closes -- the two moments
    that change what you own. Everything else stays in the log.

    Back to ntfy, on your own topics from .env. The app can show the book and
    the charts; this is what wakes the phone.

    `test` prefixes the title so a probe can never be mistaken for a real
    exit. That confusion already happened once: a check of the notification
    channels went out reading "LONG ZKCUSDT TARGET  $147.50", which is exactly
    the shape of a real fill, and it was read as a trade that never existed.
    """
    if SILENT:
        return
    if LIVE_MODE and not test:
        title = f"[REAL] {title}"
    if test:
        title = "[TEST] " + title
    topics = []
    for k in ("NTFY_TOPIC", "NTFY_TOPIC_SHARED"):
        topics += [x.strip() for x in os.getenv(k, "").split(",") if x.strip()]
    if not topics:
        log.warning("no ntfy topic set -- nothing sent: %s", title[:60])
        return
    sent = 0
    for t in topics:
        try:
            req = urllib.request.Request(
                f"https://ntfy.sh/{urllib.parse.quote(t)}",
                data=body.encode("utf-8"),
                headers={"Title": title.encode("utf-8").decode("latin-1",
                                                               "replace"),
                         "Priority": "high"})
            urllib.request.urlopen(req, timeout=10).close()
            sent += 1
        except Exception as e:
            log.warning("ntfy %s failed: %s", t[:12], str(e)[:60])
    # Silence used to look exactly like success here. It does not any more.
    if not sent:
        log.warning("nothing reached the phone: %s", title[:60])


# One socket per chart window, kept open. Opening and closing a websocket
# twice a second is what produced the "Connection reset by peer" drops: the
# reconnect logic handled them, but every drop is a poll that saw nothing.
_CONNS: dict[int, TradingViewCDP] = {}


def _conn(idx: int) -> TradingViewCDP:
    c = _CONNS.get(idx)
    if c is None:
        c = TradingViewCDP(target_index=idx)
        _CONNS[idx] = c
    return c


def drop_conn(idx: int) -> None:
    c = _CONNS.pop(idx, None)
    if c:
        try:
            c.close()
        except Exception:
            pass


_STUDY_ID: dict[int, str] = {}
_STUDY_SWAPPED: set[int] = set()
# The coin each window last showed. A window sent back to a coin we left an
# hour ago presents an hour of unseen signals, all of which read as new.
_WINDOW_SYM: dict[int, str] = {}

# The council travels packed: TradingView charges a script for every series it
# publishes and the ceiling is 64, so six votes ride in one base-3 number and
# seven flags ride in another. These two are the other half of the packing in
# section 10 of the Pine -- change one, change both.
COUNCIL = ("Bank", "Team45", "Tesla", "Sniper", "HTF", "MA")


def unpack_votes(packed):
    """Six member votes, lowest digit first, each -1 / 0 / +1."""
    n = int(packed)
    return {nm: (n // 3 ** i) % 3 - 1 for i, nm in enumerate(COUNCIL)}


def unpack_state(st):
    n = int(st)
    return {
        "plan_dir": n % 3 - 1,          # what the council decided
        "ready": bool((n // 3) % 2),    # price is standing at the entry
        "votes": (n // 6) % 7,          # how many agreed
        "fib_dir": (n // 42) % 3 - 1,   # which way Fibonacci points
        "fib_hit": bool((n // 126) % 2),
        "ma_dir": (n // 252) % 3 - 1,
        "ma_over": bool((n // 756) % 2),
    }


# The leverage the stop ceiling is measured against. Every caller of
# `_stop_room` asks with no arguments -- read_window has no access to the
# parsed flags, and the scout and the scanner are separate processes -- so the
# number lived as a default of 50 and did not move when --lev did. At 100x the
# exchange closes the position 0.5% against us while a 1.5% stop was still
# being accepted as "inside the leverage", which is the one thing the ceiling
# exists to prevent; at 20x it refused shapes that had three percent of room
# to spare. main() sets this from the flags actually in force, so the default
# below is only what the scout and the scanner assume, and it is the value the
# live units run.
_ROOM = {"lev": 50.0, "maint": 0.005}


# How many times a price has to have been respected before it counts as a
# level. Carried the same way as _ROOM and for the same reason: read_window
# holds no flags, and the scout is a separate process.
#
# Two is what breakout() was born with, and two is what this still defaults to
# -- nothing changes until somebody sets the flag. It is reachable now because
# it is the one thing that moved the numbers. Over 119 shapes filled on 41
# hours of real 15m candles across 70 coins, the whole set reached +10% 2.5%
# of the time and stopped 84.9% of the time; the subset whose level had held
# four times or more reached it 8.3% of the time, and with a thrust of 2.0 or
# better, 14.3%. Those subsets are 24 and 14 shapes -- a direction, not a
# proof, and the score floor could not be applied offline. Which is exactly
# why this is a flag and not a new default.
_SHAPE = {"min_touches": 2}


def _stop_room(lev: float | None = None, maint: float | None = None,
               keep: float = 1.0) -> float:
    """The furthest a stop may sit and still be the thing that closes us.

    Liquidation is about 1/leverage of adverse price movement, less what the
    exchange holds back. A stop beyond that is decoration: the position is gone
    before price reaches it. `keep` is the fraction of that distance a stop may
    use. At fifty times this is one and a half percent, which is the line: a
    setup whose stop needs more than that is not taken at all, and one that
    needs less is better, not worse.
    """
    lev = _ROOM["lev"] if lev is None else lev
    maint = _ROOM["maint"] if maint is None else maint
    return max(0.05, (1.0 / max(lev, 1.0) - maint) * 100 * keep)


def ripeness(row, at, need=3):
    """How close the indicator is to printing, read off its own arithmetic.

    Reverse-engineered rather than guessed. Section 5 of the Pine builds a
    signal like this:

        evB       = how many of Bank, Team45 and Tesla have fired recently
        snipVote  = evB plus the two standing agents
        snipBull  = evB just increased AND snipVote >= need AND it leads

    which means SNIP_BUY_VOTE is not a result, it is a running total. A coin
    sitting one short of the threshold is one module away from a signal, and
    that is knowable now rather than after the fact. The engine has been
    waiting for the print and then reacting; this reads the count that
    produces the print.

    Nothing here is invented and nothing needs adding to the indicator -- all
    of it is already published. Returns None when the series are not there.

        side     which way it is ripening, or 0
        vote     the running total on that side
        short    how many more modules it needs
        tide     whether the higher timeframe agrees
        lean     how many council members already point that way
        ripe     0-100
    """
    def v(name):
        i = at.get(name)
        if i is None or i >= len(row):
            return None
        x = row[i]
        return None if x is None or x != x else x

    b, sv = v("SNIP_BUY_VOTE"), v("SNIP_SELL_VOTE")
    if b is None or sv is None:
        return None
    b, sv = int(b), int(sv)
    side = 1 if b > sv else -1 if sv > b else 0
    vote = max(b, sv)
    if not side:
        return {"side": 0, "vote": vote, "short": need, "tide": 0,
                "lean": 0, "ripe": 0.0}
    short = max(0, need - vote)
    tide = int(v("HTF_BIAS") or 0)
    packed = v("VOTES_PACKED")
    lean = 0
    if packed is not None:
        mem = unpack_votes(packed)
        lean = sum(1 for x in mem.values() if x == side)
    # One module short with the tide behind it is the moment to be watching.
    # Already at the threshold means it is printing now, which is the other
    # half of the engine's job, not this one's.
    # Weighted from the record, not from what sounded right. Over 13,500 bars
    # on 45 coins, asking whether the indicator printed our way within the
    # next two hours:
    #
    #   one module short, tide with it     30.0%   (2307 bars)
    #   one module short, tide against it   3.7%   (1584 bars)
    #
    # Eight times, on that one condition. Being one short is worth almost
    # nothing by itself -- 19.2%, which is below the 26% a bar picked at
    # random reaches over the same window. The tide is the whole thing, and
    # my first weighting had it exactly backwards: 45 for the distance and 25
    # for the tide.
    if tide != side:
        return {"side": side, "vote": vote, "short": short, "tide": tide,
                "lean": lean, "ripe": 0.0}
    near = 1.0 if short == 1 else 0.7 if short == 2 else 0.4
    ripe = 55 + near * 30 + min(1.0, lean / 4.0) * 15
    return {"side": side, "vote": vote, "short": short, "tide": tide,
            "lean": lean, "ripe": ripe}


def coiling(ohlc, t, vol=None, w=20, prior=60):
    """How ready a coin is to move -- before it has moved, and either way.

    Everything else in this engine reacts: it waits for a level to break and
    then decides. This looks one step earlier and asks whether the conditions
    that precede a move are in place, so an order can already be resting when
    the break comes rather than chasing it afterwards.

    Nothing here says which way. A coil is a coil; the market picks the side,
    and a compression that resolves downward is worth exactly as much as one
    that resolves up.

    Four things, all of them classic and all of them the same idea said
    differently -- the market goes quiet before it goes:

      squeeze   the Bollinger band inside the Keltner channel. When the
                statistical range of closes contracts inside the average true
                range, volatility has fallen faster than movement, which is
                the textbook coil.
      pinch     this stretch's high-to-low against the stretch before it.
      dry       volume drying up. Crowds leave before a move, not during.
      wedge     highs coming down while lows come up -- an actual coil rather
                than a drift sideways.

    Returns None when there is not enough history, else a dict with each
    reading and a `pressure` from 0 to 100.
    """
    if not ohlc:
        return None
    keys = [k for k in sorted(ohlc) if k <= t]
    if len(keys) < prior + w + 2:
        return None
    recent = keys[-w:]
    before = keys[-(prior + w):-w]
    closes = [ohlc[k][3] for k in recent]
    c_now = closes[-1]
    if not c_now:
        return None

    # --- squeeze: the band inside the channel -----------------------------
    mean = sum(closes) / len(closes)
    var = sum((x - mean) ** 2 for x in closes) / len(closes)
    sd = var ** 0.5
    bb = 2.0 * sd
    trs = []
    for i in range(1, len(recent)):
        o, h, l, c = ohlc[recent[i]]
        pc = ohlc[recent[i - 1]][3]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs) / len(trs) if trs else 0.0
    kc = 1.5 * atr
    squeeze = 1.0 - min(1.0, bb / kc) if kc > 0 else 0.0

    # --- pinch: this stretch against the one before -----------------------
    hi_n = max(ohlc[k][1] for k in recent)
    lo_n = min(ohlc[k][2] for k in recent)
    hi_p = max(ohlc[k][1] for k in before)
    lo_p = min(ohlc[k][2] for k in before)
    rng_n = (hi_n - lo_n) / c_now
    rng_p = (hi_p - lo_p) / c_now
    pinch = 1.0 - min(1.0, rng_n / rng_p) if rng_p > 0 else 0.0

    # --- dry: volume leaving ---------------------------------------------
    dry = 0.0
    if vol:
        v_n = [vol.get(k, 0.0) for k in recent]
        v_p = [vol.get(k, 0.0) for k in before]
        a_n = sum(v_n) / max(len(v_n), 1)
        a_p = sum(v_p) / max(len(v_p), 1)
        if a_p > 0:
            dry = max(0.0, 1.0 - a_n / a_p)

    # --- wedge: highs down, lows up ---------------------------------------
    half = len(recent) // 2
    h1 = max(ohlc[k][1] for k in recent[:half])
    h2 = max(ohlc[k][1] for k in recent[half:])
    l1 = min(ohlc[k][2] for k in recent[:half])
    l2 = min(ohlc[k][2] for k in recent[half:])
    closing = ((h1 - h2) + (l2 - l1)) / c_now
    wedge = max(0.0, min(1.0, closing / max(rng_n, 1e-9)))

    pressure = (squeeze * 35 + pinch * 30 + dry * 20 + wedge * 15)
    return {
        "squeeze": squeeze, "pinch": pinch, "dry": dry, "wedge": wedge,
        "pressure": pressure,
        # where the lid and the floor are, so an order can rest on both
        "top": hi_n, "bottom": lo_n,
        "width": rng_n * 100,
    }


def breakout(ohlc, t, look=36, touch_tol=0.25, min_touches=2,
             min_body=0.45, buffer_mult=1.6, max_stop=1.0, reach=None):
    """A level that held, then stopped holding.

    Nothing here is about a timeframe or a pattern name. It is one idea, and it
    is the same idea from both sides: a price the market has respected more
    than once is a price one side has been defending. When a candle closes
    decisively through it, that side has stopped defending, and the move away
    is immediate because the orders that were holding it are gone.

    The whole point is where that puts the stop. Not a percentage, and not the
    far side of a range -- just past the level itself, beyond the wicks that
    were testing it. Being stopped then means the level was reclaimed, which is
    the one piece of news that says the trade was wrong. Nothing else can take
    us out.
    """
    if not ohlc:
        return None
    keys = [k for k in sorted(ohlc) if k <= t]
    if len(keys) < look + 4:
        return None
    o, h, l, c = ohlc[keys[-1]]
    if not c:
        return None
    rng = max(h, o, c) - min(l, o, c)
    if rng <= 0:
        return None
    body = abs(c - o) / rng
    if body < min_body:
        return None                       # not decisive, just noise

    ks = keys[-(look + 1):-1]
    typ = sum((ohlc[k][1] - ohlc[k][2]) / ohlc[k][3]
              for k in ks if ohlc[k][3]) / max(len(ks), 1) * 100
    if typ <= 0:
        return None
    # A fast coin's level is a zone, not a line. Measured across 583 coins,
    # movement and the number of clean levels pull against each other at
    # -0.23: the coins that travel keep breaking and rebuilding their own
    # levels, so nothing sits still long enough to be touched twice inside a
    # tolerance set for a quiet coin. Widening it with the coin's own
    # movement is not a loosening -- it is the same rule measured on the same
    # scale as the coin it is applied to.
    widen = 1.0
    if reach:
        widen = 1.0 + min(1.2, max(0.0, (reach - 10.0) / 30.0))
    tol = typ * touch_tol * widen / 100.0        # as a price fraction

    up = c > o
    if up:
        level = max(ohlc[k][1] for k in ks)
        if c <= level:
            return None                   # closed inside; nothing broke
    else:
        level = min(ohlc[k][2] for k in ks)
        if c >= level:
            return None

    touches, wicks = 0, []
    for k in ks:
        ko, kh, kl, kc = ohlc[k]
        if abs((kh if up else kl) - level) > level * tol:
            continue
        touches += 1
        w = ((kh - max(ko, kc)) if up else (min(ko, kc) - kl)) / level
        wicks.append(abs(w))
    if touches < min_touches:
        return None                       # never a level, just a high

    wicks.sort()
    noise = wicks[len(wicks) // 2] if wicks else tol
    # Entry at the level: we are not chasing the candle that broke it. Price
    # comes back to a broken level far more often than it runs from the close
    # of the candle that broke it, and entering there is the difference
    # between a one percent stop and a five percent one.
    entry = level
    stop = (level * (1 - (noise * buffer_mult + tol)) if up
            else level * (1 + (noise * buffer_mult + tol)))
    stop_pct = abs(entry - stop) / entry * 100
    if stop_pct > max_stop or stop_pct <= 0:
        return None
    return {
        "side": "BUY" if up else "SELL",
        "level": level, "entry": entry, "stop": stop,
        "stop_pct": stop_pct, "touches": touches, "noise": noise * 100,
        "body": body, "thrust": (rng / c * 100) / typ,
    }


def trend_ride(ohlc, t, look=30, min_steps=3, min_body=0.25,
               buffer_mult=1.6, max_stop=1.0, max_stretch=0.6):
    """The second shape: a trend already walking, joined at its own last level.

    The other half of the same idea. In a run downward the market keeps making
    a lower high -- each one is a price sellers took back, and the newest is
    the level the trend is currently respecting. Enter near it and the stop
    goes just beyond it: while the trend holds that price is never traded
    again, and if it is, the trend is over, which is the only reason to be out.
    """
    if not ohlc:
        return None
    keys = [k for k in sorted(ohlc) if k <= t]
    if len(keys) < look + 4:
        return None
    ks = keys[-(look + 1):]
    typ = sum((ohlc[k][1] - ohlc[k][2]) / ohlc[k][3]
              for k in ks if ohlc[k][3]) / max(len(ks), 1) * 100
    if typ <= 0:
        return None

    for want, name in ((-1, "SELL"), (1, "BUY")):
        steps = 0
        for i in range(len(ks) - 1, 0, -1):
            o0, h0, l0, c0 = ohlc[ks[i - 1]]
            o1, h1, l1, c1 = ohlc[ks[i]]
            # A lower high is the statement; a lower low is usually along with
            # it but not always, and demanding both on every candle made this
            # fire twice in four days across twenty-five coins.
            ok = (h1 <= h0 and c1 < c0) if want == -1 else (h1 >= h0 and c1 > c0)
            if not ok:
                break
            steps += 1
        if steps < min_steps:
            continue
        run = ks[-(steps + 1):]
        # The newest level is the one the trend is respecting NOW: the last
        # candle's high on a downtrend (the newest lower high), its low on
        # an uptrend (the newest higher low). The far extreme of the whole
        # run is where the leg STARTED -- entering there and measuring
        # "already travelled" from it judged the whole move, not progress
        # since the last level.
        level = (ohlc[run[-1]][1] if want == -1
                 else ohlc[run[-1]][2])
        bodies, wicks = [], []
        for k in run:
            ko, kh, kl, kc = ohlc[k]
            r = max(kh, ko, kc) - min(kl, ko, kc)
            if r > 0:
                bodies.append(abs(kc - ko) / r)
            w = (kh - max(ko, kc)) if want == -1 else (min(ko, kc) - kl)
            if level:
                wicks.append(abs(w) / level)
        if not bodies or sum(bodies) / len(bodies) < min_body:
            continue
        c_now = ohlc[ks[-1]][3]
        travelled = abs(level - c_now) / level
        far = (min(ohlc[k][2] for k in run) if want == -1
               else max(ohlc[k][1] for k in run))
        span = abs(level - far) / level
        if span <= 0 or travelled / span > max_stretch:
            continue                     # already too far from the level
        wicks.sort()
        noise = wicks[len(wicks) // 2] if wicks else typ / 100 * 0.25
        # A floor under the buffer. In a clean run the candles barely wick at
        # all, so the median wick comes out near zero and the stop lands three
        # hundredths of a percent from the entry -- a distance any single tick
        # covers. That is not a tight stop, it is no stop. Half of the coin's
        # own ordinary candle is the least that means anything; the breakout
        # path has always had this through its tolerance term and this one was
        # simply missing it.
        noise = max(noise, typ / 100 * 0.5)
        entry = level
        stop = (level * (1 + noise * buffer_mult) if want == -1
                else level * (1 - noise * buffer_mult))
        stop_pct = abs(stop - entry) / entry * 100
        if stop_pct <= 0 or stop_pct > max_stop:
            continue
        # The same force the breakout reports, said for a run: the candles
        # doing the walking, measured against this coin's ordinary one. Without
        # it a trend carried no thrust at all, and thrust is the heaviest thing
        # in the reading -- so every trend shape scored near zero and could
        # never be taken, whatever it looked like.
        drive = sum(abs(ohlc[k][3] - ohlc[k][0]) / ohlc[k][3]
                    for k in run if ohlc[k][3]) / max(len(run), 1) * 100
        return {
            "side": name, "level": level, "entry": entry, "stop": stop,
            "stop_pct": stop_pct, "steps": steps, "noise": noise * 100,
            "body": sum(bodies) / len(bodies), "stretch": travelled / span,
            "thrust": drive / typ if typ else 0.0,
        }
    return None


def staircase(ohlc, t, side, w=14):
    """How far a move has already gone, and how far it has come back.

    `worst` is the point of it: the largest move against us at any moment
    inside the run. A stop several times further away than anything the move
    has yet done is not one we expect to be hunted out of.
    """
    if not ohlc:
        return None
    keys = [k for k in sorted(ohlc) if k <= t][-(w + 1):]
    if len(keys) < 6:
        return None
    want = 1 if side in ("BUY", 1) else -1
    steps = 0
    for i in range(1, len(keys)):
        _o0, h0, l0, _c0 = ohlc[keys[i - 1]]
        _o1, h1, l1, _c1 = ohlc[keys[i]]
        if (h1 > h0 and l1 > l0) if want == 1 else (h1 < h0 and l1 < l0):
            steps += 1
    intact = 0
    for i in range(len(keys) - 1, 0, -1):
        _o0, h0, l0, _c0 = ohlc[keys[i - 1]]
        _o1, h1, l1, _c1 = ohlc[keys[i]]
        ok = (h1 > h0 and l1 > l0) if want == 1 else (h1 < h0 and l1 < l0)
        if not ok:
            break
        intact += 1
    start, now = ohlc[keys[0]][3], ohlc[keys[-1]][3]
    # Every other shape function here refuses a series with no price in it;
    # this one divided by it. A feed that stalls mid-load hands back zeros,
    # and one of them took the whole poll down -- including the book write at
    # the end of it -- rather than costing a single reading.
    if not start:
        return None
    travel = ((now / start - 1) if want == 1 else (1 - now / start)) * 100
    worst, shadow, peak = 0.0, 0.0, None
    for k in keys:
        o, h, l, c = ohlc[k]
        edge = h if want == 1 else l
        peak = edge if peak is None else (max(peak, edge) if want == 1
                                          else min(peak, edge))
        if not peak:
            continue
        against = ((peak - l) / peak if want == 1 else (h - peak) / peak) * 100
        worst = max(worst, against)
        sh = (((min(o, c) - l) if want == 1 else (h - max(o, c))) / c * 100
              if c else 0)
        shadow = max(shadow, sh)
    return {"steps": steps, "intact": intact, "travel": travel,
            "worst": worst, "shadow": shadow, "bars": len(keys) - 1}


def _ramp(v, lo, hi):
    """0 below lo, 1 above hi, straight line between. None stays None."""
    if v is None:
        return None
    if hi == lo:
        return 1.0 if v >= hi else 0.0
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


# Weighted on one question: will this reach the target. The stop is settled --
# it is the line the leverage allows and nothing else -- so nothing here is
# spent on it. Conservatism about the stop buys nothing in a scalp; certainty
# about the target is the only thing that pays.
# Rebuilt from what the record actually separates rather than from what
# sounded sensible. Over a hundred and seventy-five resolved setups, three
# things told winners from losers and four told nothing at all:
#
#   thrust   the breaking candle against the coin's own normal -- winners
#            broke out on 2.3x, losers on 1.7x
#   reach    how often the coin covers ten percent at all -- winners 35%,
#            losers 29%
#   agree    kept because the council's lean was leaning the right way on
#            every example from the phone, though the record here cannot
#            check it: the shape and the council are read at different moments
#
# and these were dropped because they measured nothing: how directly the coin
# travels, how many times the level was tested, how much of the breaking
# candle is body, and how long the run was. Three of the four were slightly
# INVERTED -- the losers had marginally more of them.
# ---------------------------------------------------------------------------
# The entry decision, in one place.
#
# The weights are the ones the record supports and they are unchanged: the
# breaking candle against the coin's own ordinary one, how often the coin
# covers the ten percent target at all, and how many of the six council
# members lean the same way. `reach` stays IN the score rather than becoming a
# gate -- a coin that travels less is worth less, by degrees, not excluded.
#
# What changed is the floor. At 70, a perfect candle (40) and a unanimous
# council (25) came to 65 and could not clear it, so every trade depended on
# `reach` and setup quality decided nothing. At 60 the two measures a setup
# controls can carry it on their own, and `reach` moves the score up from
# there instead of being the only thing that ever reached it.
#
# MAX_ACHIEVABLE_SCORE is derived from the weights rather than declared, and
# `check_score_reachable` refuses to start when the floor sits above it. That
# is the guard against the original bug: every part working, every test
# passing, and the book silently never trading.
# ---------------------------------------------------------------------------

# What each measure is worth, out of a hundred.
CONF_WEIGHTS = {"tall": 40.0, "reach": 35.0, "agree": 25.0,
                "smooth": 0.0, "stairs": 0.0, "body": 0.0, "run": 0.0,
                "wick": 0.0, "safety": 0.0, "votes": 0.0}

# No weighted measure is ever dropped from the reckoning.
#
# The score used to be divided by the weight of whatever could be READ, which
# means an unreadable measure raised the score instead of lowering it. It bit
# twice. A coin the scanner had not measured scored a perfect 100 while a coin
# measured never to travel scored 65. And a shape whose council reading failed
# -- which happens when the scout's study read does not come back -- was
# divided by 75 instead of 100: a perfect candle on a coin covering the target
# 20% of the time went from 51 to 68, straight through a floor of 60.
#
# Both are the same mistake. "Could not be read" is not "does not apply": it
# earns no credit and still occupies its share, so a missing reading can only
# ever make a setup harder to take.
GROUND = tuple(k for k, w in CONF_WEIGHTS.items() if w > 0)

# The scale everything is reported on, so `--min-confidence` reads as a
# percentage -- which is what every log line and every operator assumes.
SCORE_MAX = 100.0

# The best any setup can possibly score, derived from the weights so it cannot
# drift away from them.
MAX_ACHIEVABLE_SCORE = sum(w for w in CONF_WEIGHTS.values() if w > 0)

# The measures that describe the COIN rather than the setup in front of us.
# Kept separate from GROUND above, which is about what may be dropped: these
# two are about what a setup does not control.
COIN_MEASURES = ("reach", "smooth")

# What a setup can reach on its own merits, with no help from the coin. This
# is the number that made 70 unreachable, and the reason the floor is 60.
QUALITY_ONLY_MAX = sum(w for k, w in CONF_WEIGHTS.items()
                       if w > 0 and k not in COIN_MEASURES)


def check_score_reachable(threshold: float) -> str | None:
    """None if the floor is reachable, else why it is not.

    The bug this exists to prevent did not look like a bug: every component
    worked, every test of every component passed, and the engine simply never
    traded. A threshold above the maximum the scale can produce is a silent
    stop, so it is refused out loud instead.
    """
    if threshold <= 0:
        return None
    if MAX_ACHIEVABLE_SCORE + 1e-9 < threshold:
        return (f"--min-confidence {threshold:g} can never be reached: the "
                f"highest score this engine can produce is "
                f"{MAX_ACHIEVABLE_SCORE:.1f}. Nothing would ever be traded, "
                f"and nothing would say so.")
    return None


def confidence(run=None, tall=None, body=None, wick=None, votes=None,
               stairs=None, safety=None, agree=None, against=None,
               reach=None, smooth=None):
    """One number for how good a setup is, and the reasoning behind it.

    Every test in this engine used to be a cliff -- four candles of run passed
    and three point nine did not, however good everything else was. That left
    holes between the thresholds. Nothing is a cliff now: each measure earns
    part of a hundred on a ramp, a strong one can carry a weak one, and a
    single dial decides how sure the engine has to be.

    Two of them are not scores though. They are the ground.
    """
    parts = {
        # How often this coin covers the target at all, from the scanner's own
        # measurement of its recent history. The heaviest thing here, because
        # it is the only one that says whether there is anywhere to go.
        "reach": (reach, _ramp(reach, 8.0, 45.0)),
        # NOT IN FORCE. This carries weight 0.0 in CONF_WEIGHTS, so nothing
        # below it changes any score -- the ramp is kept because the reasoning
        # is worth having if it is ever weighted again, but as the engine
        # stands, smoothness is measured, printed, and ignored.
        #
        # And whether it gets there in a line or by thrashing. A move that
        # oscillates on its way to ten percent visits our stop first.
        #
        # The scale is the market's, not mine. I first set this to run from
        # 0.25 to 0.65 on the assumption that a directional move would score
        # near one -- but a real fifteen-minute crypto move covers a fifth to a
        # third of its own path, and never more. Every coin therefore landed at
        # the bottom of the ramp, and because this multiplies rather than adds,
        # it dragged every setup with it: a hundred and seventy-five setups in
        # a row all scored under fifty, so a floor of seventy could never be
        # reached by anything and the book took nothing at all.
        "smooth": (smooth, _ramp(smooth, 0.18, 0.38)),
        # Same correction: a coin covering ten percent in a couple of hours
        # forty percent of the time does not exist. Half that is exceptional.
        # This ramp was reading every real coin as barely moving.
        # How many of the six are leaning the same way. They will not publish
        # a plan on this shape and are never asked to -- but on every example
        # that worked they were already pointing at it.
        "agree": (agree, None if agree is None else
                  max(0.0, min(1.0, (agree - 1.5 * (against or 0)) / 4.0))),
        # How many times the market respected the level, or how many candles
        # the trend has been remaking it.
        "stairs": (stairs, _ramp(stairs, 1.5, 5.0)),
        "safety": (safety, _ramp(safety, 1.0, 2.2)),
        "run": (run, _ramp(run, 2.0, 6.0)),
        # The breaking candle against the coin's own ordinary one. Passed in
        # as thrust/2, so this ramp runs from an ordinary candle to one three
        # times the size -- which is the range the winners actually lived in.
        "tall": (tall, _ramp(tall, 0.7, 1.6)),
        "body": (body, _ramp(body, 0.25, 0.80)),
        "wick": (wick, None if wick is None else
                 1.0 - _ramp(wick, 30.0, 85.0)),
        "votes": (votes, None if votes is None else
                  (0.6 if votes == 4 else 1.0 if votes == 5
                   else 0.25 if votes == 6 else 0.0)),
    }
    # Reach and smoothness multiply rather than add. A coin that does not cover
    # ten percent has nowhere to go, and a coin that gets there by thrashing
    # visits the stop on the way -- neither can be made acceptable by a
    # beautiful level or a unanimous council, and adding them up let exactly
    # that happen: a coin that never moves scored seventy-one because
    # everything else was perfect.
    # These used to multiply, on the reasoning that a coin which cannot travel
    # has nowhere to go whatever else is true. Sound in principle and wrong in
    # practice: two factors around a half multiplied to a quarter, every setup
    # in the record scored under fifty, and a floor of seventy could never be
    # reached by anything at all. The book took nothing for hours and the
    # reason was arithmetic, not the market.
    got = {k: v for k, v in parts.items() if v[1] is not None}
    if not got:
        return 0.0, {}
    total = sum(CONF_WEIGHTS[k] for k in got)
    # A measure that could not be read must not leave the reckoning, because
    # leaving it lowers the bar instead of raising it: drop `reach` -- the
    # heaviest single thing here and the only one that says whether the coin
    # can reach the target at all -- and the remaining weights renormalise to
    # a hundred, so a coin nobody has measured scores HIGHER than a coin
    # measured never to travel. The scanner's files are rewritten on a timer,
    # so "could not be read" is a real state and not a theoretical one.
    for k in GROUND:
        if k not in got and CONF_WEIGHTS.get(k, 0) > 0:
            total += CONF_WEIGHTS[k]
    if total <= 0:
        return 0.0, {}
    craft = sum(CONF_WEIGHTS[k] * v[1] for k, v in got.items()) / total
    out = {k: (v[0], CONF_WEIGHTS[k] * v[1] / total * SCORE_MAX)
           for k, v in got.items() if CONF_WEIGHTS[k] > 0}
    return craft * SCORE_MAX, out


def run_strength(ohlc, t, side, w=6):
    """Of the last few candles, how many closed our way on a real body."""
    if not ohlc:
        return 0
    keys = [k for k in sorted(ohlc) if k <= t][-w:]
    if len(keys) < w:
        return 0
    want = 1 if side in ("BUY", 1) else -1
    n = 0
    for k in keys:
        o, h, l, c = ohlc[k]
        rng = max(h, o, c) - min(l, o, c)
        if rng <= 0:
            continue
        body = (c - o) / rng
        if (body > 0.25 and want == 1) or (body < -0.25 and want == -1):
            n += 1
    return n


def leg_of(ohlc, t, move=8.0, bars=3, retr=0.618, wait=8):
    """The impulse an entry is a retracement of: (direction, level, span, base)."""
    if not ohlc:
        return 0, None, 0.0, None
    keys = [k for k in sorted(ohlc) if k <= t]
    if len(keys) < bars + 2:
        return 0, None, 0.0, None
    d, base, span, armed = 0, 0.0, 0.0, -10000
    for i, k in enumerate(keys):
        o, h, l, c = ohlc[k]
        w = keys[max(0, i - bars):i + 1]
        lo_n = min(ohlc[x][2] for x in w)
        hi_n = max(ohlc[x][1] for x in w)
        up = (h - lo_n) / lo_n * 100 if lo_n else 0
        dn = (hi_n - l) / hi_n * 100 if hi_n else 0
        stale = i - armed > wait
        if up >= move and (stale or d != 1):
            d, base, span, armed = 1, lo_n, h - lo_n, i
        elif dn >= move and (stale or d != -1):
            d, base, span, armed = -1, hi_n, hi_n - l, i
    if not d or (len(keys) - 1 - armed) > wait:
        return 0, None, 0.0, None
    entry = base + span * (1 - retr) if d == 1 else base - span * (1 - retr)
    return d, entry, span, base


def expansion(ohlc, t, bars=20):
    """This candle's range against the coin's own recent normal."""
    if not ohlc or t not in ohlc:
        return None
    keys = [k for k in sorted(ohlc) if k <= t]
    if len(keys) < 8:
        return None
    window = keys[-(bars + 1):-1]
    if not window:
        return None
    typ = 0.0
    for k in window:
        o, h, l, c = ohlc[k]
        if c:
            typ += (h - l) / c
    typ /= len(window)
    o, h, l, c = ohlc[t]
    if not c or typ <= 0:
        return None
    return ((h - l) / c) / typ


def body_share(ohlc, t):
    """How much of the candle is body rather than shadow."""
    o = (ohlc or {}).get(t)
    if not o:
        return None
    op, hi, lo, cl = o
    hi = max(hi, op, cl)
    lo = min(lo, op, cl)
    rng = hi - lo
    if rng <= 0 or not cl or rng / cl * 100 < 0.05:
        return None
    return abs(cl - op) / rng


def wick_risk(ohlc, side, stop_pct, bars=96, t=None):
    """How often this coin's shadows alone would reach the stop.

    `t` is the bar the decision is being made on; nothing after it is counted.
    Without it this measured the last 96 bars of whatever series it was
    handed, which on any signal older than the newest bar meant reading
    candles that printed AFTER the decision -- and the answer inverts: a coin
    that had never once wicked through the stop reads as doing it 61% of the
    time once an adverse hour is appended. Every other measure in this file
    already takes the bar it is asked about; this one did not.
    """
    if not ohlc or stop_pct <= 0:
        return None
    keys = sorted(ohlc) if t is None else [k for k in sorted(ohlc) if k <= t]
    rows = [ohlc[k] for k in keys[-bars:]]
    if len(rows) < 30:
        return None
    hits = 0
    for o, h, l, c in rows:
        if not c:
            continue
        adverse = (min(o, c) - l) if side == "BUY" else (h - max(o, c))
        if adverse / c * 100 >= stop_pct:
            hits += 1
    return hits / len(rows) * 100


RANKED = Path(__file__).resolve().parent / "data" / "boom.json"
WATCH_M = Path(__file__).resolve().parent / "data" / "watch_measures.json"
_REACH: dict = {"t": 0.0, "by": {}}


ATR_M = Path(__file__).resolve().parent / "data" / "atr_measures.json"
_ATR = {"t": 0.0, "by": {}, "trend": {}, "shape": {}, "stamp": 0}

# What each reading is measured against: the median of 163 of the indicator's
# own signals. Medians, not opinions -- each splits the measured set in half,
# and the half that did better is the half that scores.
_MID = {"vol20": 1.17, "volx": 0.94, "mom1h": 1.09}


def coin_atr(sym: str) -> float | None:
    """The coin's ATR as a percent of price, from the coin finder.

    Re-read when the file changes, the same way the reach numbers are: the
    finder runs every ten minutes, and a coin that has gone quiet since the
    last scan must stop being treated as one that moves.

    None means the finder has never measured this coin -- which is not the
    same as measuring it at zero, and the caller has to decide which way to
    fail. Refusing on a missing measurement would blind the book on any coin
    the finder has not reached yet.
    """
    try:
        stamp = ATR_M.stat().st_mtime if ATR_M.exists() else 0
    except OSError:
        stamp = 0
    if stamp > _ATR.get("stamp", 0) or time.time() - _ATR["t"] > 300:
        _ATR["stamp"] = stamp
        by, tr, sh = {}, {}, {}
        ok = False
        try:
            for k, v in json.loads(ATR_M.read_text()).items():
                kk = str(k).upper()
                a = (v or {}).get("atr")
                if a is not None:
                    by[kk] = float(a)
                t_ = (v or {}).get("trend")
                if t_ is not None:
                    tr[kk] = float(t_)
                sh[kk] = {q: (v or {}).get(q)
                          for q in ("vol20", "mom6h", "mom1h", "volx")}
            ok = True
        except Exception:
            pass
        # A truncated mid-write read must keep the last good measurements,
        # not replace them with an empty map -- missing is not bad, but an
        # invented emptiness is worse.
        if ok:
            _ATR["by"] = by
            _ATR["trend"] = tr
            _ATR["shape"] = sh
            _ATR["t"] = time.time()
        else:
            _ATR["stamp"] = 0          # try again on the next call
    return _ATR["by"].get(str(sym).upper())


def entry_score(sym: str, side: str, agents: int) -> tuple[float, str]:
    """How good this signal is, out of a hundred, and why.

    With one position at a time the book cannot take everything it sees, so it
    has to prefer. Every term was measured at the signal bar over 163 of the
    indicator's own signals, and each is worth what it separated:

      agents      four or more reached the target 35.4% of the time against
                  19.4% at three -- the biggest thing the indicator publishes
                  about its own conviction.
      a calm coin the strongest separator of all, and it points DOWN: the
                  calmer half won 23.9% for +12.7% a trade, the wilder half
                  10.1% for -20.5%. A 1.25% stop on a wild coin is taken out
                  by noise, not by being wrong.
      six hours   the longer move with the trade: +7.4% against -14.4%.
      one hour    the shorter move NOT already spent our way: a coin that has
                  just jumped in our direction has made the move we wanted.
      volume      the candle carrying more than the coin's own normal: +3.2%
                  against -10.5%.
      travel      ATR enough to reach five percent at all.

    The weights are 40, 25, 15, 10, 5 and 5, in the order they separated, and
    they add to exactly a hundred. That is not decoration: a scale whose top
    cannot be reached is a floor nobody can meet, and this engine has already
    been stopped for weeks by one.

    A reading the finder does not have scores nothing, neither for nor
    against. Missing is not bad, and refusing on a number nobody has would
    blind the book on every coin the finder has not reached yet.
    """
    coin_atr(sym)                        # refreshes the shared cache
    sh = _ATR["shape"].get(str(sym).upper()) or {}
    up = 1 if side == "BUY" else -1
    pts, why = 0.0, []

    pts += min(40.0, max(0.0, (agents - 3) * 20.0))
    if agents >= 4:
        why.append(f"agents {agents}")

    v20 = sh.get("vol20")
    if v20 is not None and v20 < _MID["vol20"]:
        pts += 25.0
        why.append(f"calm {v20:.2f}")

    m6 = sh.get("mom6h")
    if m6 is not None and m6 * up > 0:
        pts += 15.0
        why.append(f"6h {m6:+.1f}%")

    m1 = sh.get("mom1h")
    if m1 is not None and m1 * up < _MID["mom1h"]:
        pts += 10.0
        why.append(f"1h {m1:+.1f}%")

    vx = sh.get("volx")
    if vx is not None and vx >= _MID["volx"]:
        pts += 5.0
        why.append(f"vol {vx:.2f}x")

    at = coin_atr(sym)
    if at is not None and at >= 4.0:
        pts += 5.0
        why.append(f"ATR {at:.1f}%")

    return pts, ", ".join(why) or "nothing in its favour"


def coin_trend(sym: str) -> float | None:
    """Which way the coin has been going, over the last two hours.

    From the same file and the same cache as the ATR. None means the finder
    has never measured this coin, which is not the same as flat.
    """
    coin_atr(sym)                       # shares the cache and its reload
    return _ATR["trend"].get(str(sym).upper())


def model_features(sym: str, side: str, sig: dict) -> dict:
    """The learned filter's features, at signal time.

    The same thirteen the selector was trained on. Missing measures stay
    missing -- the model's own imputation handles them, and a missing
    reading must never be dressed up as a number.
    """
    coin_atr(sym)                       # refresh the shared cache
    sh = _ATR["shape"].get(str(sym).upper()) or {}
    return {
        "atr": _ATR["by"].get(str(sym).upper()),
        "trend": _ATR["trend"].get(str(sym).upper()),
        "vol20": sh.get("vol20"), "mom6h": sh.get("mom6h"),
        "mom1h": sh.get("mom1h"), "volx": sh.get("volx"),
        "agents": float(sig.get("agents") or 0),
        "tier": float(sig.get("tier") or 0),
        "score": float(sig.get("score") or 0),
        "who": float(sig.get("who") or 0),
        "counter": float(bool(sig.get("counter"))),
        "side": 1.0 if side == "BUY" else -1.0,
        "hour": (float(sig.get("t") or 0) % 86400) / 3600.0,
    }


def coin_reach(sym: str):
    """(how often it covers the target, how directly) from the scanner.

    Re-read every few minutes rather than cached for the process's life: the
    scanner runs on a timer, and a coin that has gone quiet since the last
    ranking should stop being treated as one that moves.
    """
    # Reload when the file changes, not merely when five minutes have passed.
    # The scanner rewrites these every run, and a purely time-based cache meant
    # the book spent the next five minutes judging coins on the previous run's
    # numbers -- APEXUSDT was refused at "0% of the time" while the file on
    # disk said 11.2%.
    try:
        stamp = max(WATCH_M.stat().st_mtime if WATCH_M.exists() else 0,
                    RANKED.stat().st_mtime if RANKED.exists() else 0)
    except Exception:
        stamp = 0
    if stamp > _REACH.get("stamp", 0) or time.time() - _REACH["t"] > 300:
        _REACH["stamp"] = stamp
        by = {}
        # The wider file first: the scout walks a hundred and fifty coins and
        # the short list only measures a couple of dozen of them, so without
        # this the heaviest half of the reading is missing on most of what it
        # finds -- and a missing measure is dropped rather than counted, which
        # quietly changes the scale a plan is judged on.
        try:
            # `k`, not `sym`. The loop variable used to shadow the argument,
            # so once the cache had been rebuilt the lookup on the last line
            # asked for whatever the final key in the file happened to be --
            # and returned another coin's numbers for the coin in hand, once
            # after every scanner run, silently and with full confidence.
            for k, m in json.loads(WATCH_M.read_text()).items():
                by[k] = (float(m.get("reach") or 0.0),
                         float(m.get("smooth") or 0.0))
        except Exception:
            pass
        try:
            for r in json.loads(RANKED.read_text()):
                by[r["sym"]] = (float(r.get("reach") or 0.0),
                                float(r.get("smooth") or 0.0))
        except Exception:
            pass
        # The scanner writes both of these files without a temporary, so a
        # read that lands mid-write returns a truncated document and parses to
        # nothing. Caching that emptiness is the dangerous half: an unmeasured
        # coin does not score badly, it drops out of the reckoning entirely
        # and is judged on the remaining measures alone -- which is a LOWER
        # bar, not a higher one. For five minutes every coin on the book would
        # have been graded without the heaviest thing in the grade. Keeping
        # the last good map costs nothing and cannot invent a coin.
        if by:
            _REACH["by"] = by
            _REACH["t"] = time.time()
        else:
            log.warning("no coin measurements could be read from %s or %s -- "
                        "keeping the previous %d", WATCH_M.name, RANKED.name,
                        len(_REACH.get("by") or {}))
            _REACH["stamp"] = 0          # try again on the next call
    return _REACH["by"].get(sym, (None, None))


def floor_coverage(threshold: float) -> tuple[int, int]:
    """(coins that could clear `threshold` at all, coins measured at all).

    A perfect setup on each coin -- full candle, unanimous council -- plus
    whatever that coin's own reach is worth. Printed at start-up, because a
    floor nobody can reach looks exactly like a quiet market, and that is
    precisely how this engine spent weeks taking nothing.
    """
    coin_reach("")                       # force the cache to load
    by = _REACH.get("by") or {}
    able = 0
    for v in by.values():
        if not v or v[0] is None:
            continue
        ceiling = QUALITY_ONLY_MAX + CONF_WEIGHTS["reach"] * (
            _ramp(v[0], 8.0, 45.0) or 0.0)
        if ceiling + 1e-9 >= threshold:
            able += 1
    return able, len(by)


SCOUT = Path(__file__).resolve().parent / "data" / "scout.json"


_RIPE: dict = {"t": 0.0, "by": {}}


def ripe_now(max_age_min: float = 20.0) -> dict:
    """Coins the scout says are one module from the indicator printing.

    Read fresh rather than cached for the process's life: ripeness decays --
    a coin that was one module short twenty minutes ago has either printed or
    gone cold, and acting on a stale one is worse than not knowing.
    """
    if time.time() - _RIPE["t"] > 20:
        by = {}
        try:
            now = time.time()
            for r in json.loads(SCOUT.read_text()):
                if r.get("kind") != "ripe":
                    continue
                if now - float(r.get("at", 0)) > max_age_min * 60:
                    continue
                by[r["sym"]] = r
        except Exception:
            # A truncated mid-write read must not zero the last good map,
            # or every ripe bonus vanishes for the next twenty seconds.
            by = None
        if by is not None:
            _RIPE["by"] = by
        _RIPE["t"] = time.time()
    return _RIPE["by"]


def ct_prints(max_age_min: float = 20.0) -> dict:
    """Which coins the indicator has just printed a signal on, and which way.

    Read from the scout's file, because the scout is the only thing that sees
    every coin -- the book itself watches one window. Kept short: a print from
    half an hour ago says nothing about now.

    Any signal counts, not only a counter-trend one. Reading 4USDT bar by bar
    showed why: the `ct` flag is a label on the signal's own type, not a
    separate warning. It appears on the same bar as the signal and always
    points the same way, so gating on it meant the book would leave a winning
    short on a counter-trend BUY and sit through a plain one -- and a plain
    BUY against an open short says the same thing about the move. The
    counter-trend flag is kept in the reading because it is worth studying,
    but it is not what decides.

    Returns {SYMBOL: +1 where the indicator is calling for a buy, -1 a sell}.
    """
    try:
        rows = json.loads(SCOUT.read_text())
    except Exception:
        return {}
    out, now = {}, time.time()
    for r in rows:
        if r.get("kind") != "combo":
            continue
        if now - float(r.get("at", 0)) > max_age_min * 60:
            continue
        sym = str(r.get("sym") or "").upper()
        if not sym:
            continue
        d = r.get("ct") or r.get("dir")
        if d:
            out[sym] = int(d)
    return out


def scout_plans(seen, held, max_age_min: float = 45.0):
    """Plans the scout found on coins no window is currently holding.

    Two chart windows can only ever watch two coins, and a coin offers a setup
    every few days -- which is why the book could be correct and still trade
    once a fortnight. The scout walks the ranked list on one window and writes
    down what it finds; this reads that list. The plan carries its own entry
    level, so nothing has to keep watching the coin for the order to rest.
    """
    try:
        rows = json.loads(SCOUT.read_text())
    except Exception:
        return []
    out, now = [], time.time()
    for r in rows:
        if now - float(r.get("at", 0)) > max_age_min * 60:
            continue
        sym = r.get("sym")
        # The scout writes four kinds of row. Only two of them are orders.
        # A "ripe" row is the forecast -- the print has not happened yet -- and
        # a "coil" row is a coin winding up; neither carries an entry or a
        # stop, so neither can be a trade. They exist to tell the scout where
        # to perch. Reading them as plans is what killed 69 polls in a row:
        # the ripe row has no "dir" key at all, and the book died on it before
        # it ever reached the shapes further down the file.
        if r.get("kind") in ("ripe", "coil"):
            continue
        # The indicator's own signal, walked onto every coin on the list.
        #
        # This is not a council row and must not be dressed as one: the book
        # drops council rows on --source combo, which is how the scout's whole
        # output vanished silently for an hour. It carries no entry level
        # either -- a combo signal is taken at the print, so the price is
        # whatever the market is when the book reads it.
        if r.get("kind") == "combo":
            key = (f"SCOUT:{sym}", int(r.get("t", 0)), r.get("side"), False)
            if sym in held or key in seen:
                continue
            seen.add(key)
            out.append({
                "counter": False, "scout": True, "kind": "combo",
                "t": int(r.get("t", 0)), "side": r.get("side"),
                "span": int(r.get("span", 0)), "tier": int(r.get("tier", 0)),
                "who": int(r.get("who", 0)), "score": int(r.get("score", 0)),
                "agents": int(r.get("agents", 0)),
                "wired": bool(r.get("wired")), "sym": sym,
                # The candle the signal fired on, so the edge entry can
                # rest at its extreme without a window on the coin.
                "bar": r.get("bar"),
            })
            continue
        if r.get("dir") is None or r.get("entry") is None:
            log.debug("scout row for %s carries no direction or entry -- "
                      "skipped, not traded", sym)
            continue
        # A coin a window already shows reports its plans through the window.
        # Taking the scout's copy as well opened the same trade twice.
        if sym in held:
            continue
        # The book's own memory, which is written to disk. Keeping this in a
        # module-level set meant every restart forgot what it had acted on and
        # re-traded the scout's whole list -- which is how one plan on PORTAL
        # became three positions.
        key = (f"SCOUT:{sym}", int(r.get("t", 0)),
               "BUY" if r.get("dir") == 1 else "SELL", False)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "council": True, "counter": False, "scout": True,
            # A break carries its own stop -- the far side of the band -- and
            # that is the whole reason a tight stop is defensible here.
            "kind": r.get("kind", "council"),
            "agree": r.get("agree"), "against": r.get("against"),
            "stop_pct": r.get("stop_pct"),
            "level": r.get("level"), "touches": r.get("touches"),
            "steps": r.get("steps"), "thrust": r.get("thrust"),
            "t": int(r.get("t", 0)),
            "side": "BUY" if r.get("dir") == 1 else "SELL",
            "entry": float(r["entry"]), "votes": int(r.get("votes", 0)),
            "ready": bool(r.get("ready")), "fib_hit": bool(r.get("ready")),
            "members": r.get("members", {}),
            # The scout measured these on the plan's own bar; dropping them
            # here made --tp-leg unreachable, the candle floors silently
            # no-op, and confidence() score every scout plan zero.
            "leg": r.get("leg"), "expansion": r.get("expansion"),
            "body": r.get("body"), "wick": r.get("wick"),
            "run": r.get("run"),
            "span": 0, "tier": 0, "who": 0, "score": int(r.get("votes", 0)),
            "agents": int(r.get("votes", 0)), "wired": True,
            "sym": sym,
        })
    return out


# The plan direction each window last showed. A plan is a state, not a print:
# it holds for as long as the council holds together, so acting on every bar
# that carries one would re-enter the same trade every candle. Only the
# transition into a plan is a signal.
_PLAN_DIR: dict[int, int] = {}


def reunion_ok(pnd) -> bool:
    """The three-agent print must still stand when the scout order fills.

    The scout rewrites data/scout.json at the end of every sweep; a combo
    row carrying this order's own signal bar means the last sweep still
    saw the print. Once it clears, filling this order would be entering
    a trade the agents have left -- the one kind this book must not take.
    An unreadable file allows the fill: a sync failure must not silently
    stop the book from trading.
    """
    try:
        rows = json.loads(SCOUT.read_text())
    except Exception:
        return True
    t = pnd.sig.get("t", 0)
    for r in rows:
        if (r.get("kind") == "combo" and r.get("sym") == pnd.sym
                and r.get("side") == pnd.side
                and int(r.get("t", 0)) == int(t)):
            return True
    return False


def read_window(idx: int):
    c = _conn(idx)
    if True:
        st = [s for s in c.studies() if "TBT" in s["name"]]
        if not st:
            return None
        if _STUDY_ID.get(idx) not in (None, st[0]["id"]):
            log.info("study on window %d changed (%s -> %s) -- its history will "
                     "be absorbed, not traded", idx, _STUDY_ID[idx], st[0]["id"])
            _STUDY_SWAPPED.add(idx)
        _STUDY_ID[idx] = st[0]["id"]
        # Only the recent bars matter here: a signal is acted on the moment it
        # prints, and everything older is already in `seen`. Asking for the
        # whole history cost ~200 ms of the ~230 ms poll.
        r = c.raw_series(st[0]["id"], limit=LIVE_BARS)
        if not r or not r.get("rows"):
            return None
        at = {p: i + 1 for i, p in enumerate(r["plots"])}

        def v(name, row):
            i = at.get(name)
            if i is None or i >= len(row):
                return None
            x = row[i]
            return None if x is None or x != x else x

        bars = c.evaluate(BARS_JS)
        ohlc = {}
        try:
            for b in json.loads(bars):
                ohlc[int(b[0])] = (float(b[1]), float(b[2]), float(b[3]), float(b[4]))
        except Exception:
            pass

        sigs = []
        for row in r["rows"]:
            # Two streams. A counter-trend combo is the same three robots
            # agreeing against the higher-timeframe phase -- worth taking, but
            # at half size, so it is tagged rather than merged.
            for side in ("BUY", "SELL"):
                if v(f"TBT_CT{side}_SPAN", row) is None:
                    continue
                # tier and last are published once, for both streams -- they
                # describe the same convergence, and six extra series pushed
                # the script past the compile budget.
                sigs.append({
                    "t": int(row[0]), "side": side, "counter": True,
                    "span": int(v(f"TBT_CT{side}_SPAN", row)),
                    "tier": int(v(f"TBT_{side}_TIER", row) or 0),
                    "who": int(v(f"TBT_{side}_LAST", row) or 0),
                    "score": int(v(f"TBT_{side}_SCORE", row) or 0),
                    "agents": int(v(f"SNIP_{side}_VOTE", row) or 0),
                    "wired": bool(v("TSL_WIRED", row)),
                })
            for side in ("BUY", "SELL"):
                if v(f"TBT_{side}_SPAN", row) is None:
                    continue
                sigs.append({
                    "counter": False,
                    "t": int(row[0]), "side": side,
                    "span": int(v(f"TBT_{side}_SPAN", row)),
                    "tier": int(v(f"TBT_{side}_TIER", row) or 0),
                    "who": int(v(f"TBT_{side}_LAST", row) or 0),
                    "score": int(v(f"TBT_{side}_SCORE", row) or 0),
                    "agents": int(v(f"SNIP_{side}_VOTE", row) or 0),
                    "wired": bool(v("TSL_WIRED", row)),
                })
        # The council, read off the same rows. Walking them in order means a
        # plan that formed between two polls is still caught on its own bar
        # rather than at whatever price happened to arrive later.
        for row in r["rows"]:
            stv = v("STATE", row)
            if stv is None:
                break                      # an older study, no council on it
            d = unpack_state(stv)
            was = _PLAN_DIR.get(idx, 0)
            _PLAN_DIR[idx] = d["plan_dir"]
            if d["plan_dir"] == 0 or d["plan_dir"] == was:
                continue                   # no plan, or the same one as before
            entry = v("PLAN_ENTRY", row)
            if entry is None:
                continue                   # a plan with no level cannot be traded
            sigs.append({
                "council": True, "counter": False,
                "t": int(row[0]),
                "side": "BUY" if d["plan_dir"] == 1 else "SELL",
                "entry": float(entry), "votes": d["votes"],
                "ready": d["ready"], "fib_hit": d["fib_hit"],
                "members": unpack_votes(v("VOTES_PACKED", row) or 0),
                "span": 0, "tier": 0, "who": 0, "score": d["votes"],
                "agents": d["votes"], "wired": True,
            })
        # The picture, on the window the book is holding. Same function, same
        # shape; it simply does not need the indicator to see it.
        if ohlc:
            # The last CLOSED candle, not the one still forming. Both shapes
            # are defined on a finished bar -- "closed through the level" and
            # "mostly body" are meaningless while the candle is still moving,
            # and reading the live bar is why the scout swept twenty-seven
            # coins for forty minutes and found nothing at all while the same
            # test on completed candles fires on one bar in seventy.
            _ks = sorted(ohlc)
            _t = _ks[-2] if len(_ks) > 1 else _ks[-1]
            # Two shapes, one principle: the level the market is respecting is
            # the stop. A candle bursting through a level it had respected, or
            # a run that keeps making a new one -- the first is a level that
            # gave way, the second is a level being remade every few candles.
            # The ceiling on the stop is not a taste, it is the leverage.
            # The exchange closes a position about 1/leverage against us, so a
            # stop further out than that is never reached: the loss is the
            # whole margin at a worse fill, and the exit is not ours. Anything
            # that cannot be stopped properly is not taken at all.
            _room = _stop_room()
            # The coin this window is on, so its level is measured on its own
            # scale. read_window works in TradingView's naming, so it is peeled
            # back to the plain symbol the scanner uses.
            _sym0 = str(r.get("symbol") or "").split(":")[-1].replace(".P", "")
            _rr = coin_reach(_sym0)[0] if _sym0 else None
            _br = (breakout(ohlc, _t, max_stop=_room, reach=_rr,
                            min_touches=_SHAPE["min_touches"])
                   or trend_ride(ohlc, _t, max_stop=_room))
            if _br:
                # What the six are saying, attached but never used as a gate.
                # On every example so far the council has been leaning the same
                # way while reading "no plan" -- it will not publish, because a
                # breakout is not the retracement it looks for, but its lean is
                # real information and agreement is what makes a setup certain
                # rather than merely valid.
                _lean = 0
                try:
                    # The same closed bar the shape was read on. The indicator
                    # is set to signal on unclosed candles, so the live vote
                    # can still change before the bar finishes -- reading it
                    # one bar later than the shape would mean the two halves
                    # of the same decision came from different moments.
                    _last = r["rows"][-2] if len(r["rows"]) > 1 else r["rows"][-1]
                    _pv = _last[at["VOTES_PACKED"]] if "VOTES_PACKED" in at else None
                    if _pv is not None and _pv == _pv:
                        _v = unpack_votes(_pv)
                        _want = 1 if _br["side"] == "BUY" else -1
                        _lean = sum(1 for x in _v.values() if x == _want)
                        _br["against"] = sum(1 for x in _v.values()
                                             if x == -_want)
                except Exception:
                    pass
                _br["agree"] = _lean
                sigs.append({
                    "council": True, "counter": False, "kind": "break",
                    "t": _t, "side": _br["side"], "entry": _br["entry"],
                    "stop_pct": _br["stop_pct"],
                    "level": _br.get("level"),
                    "touches": _br.get("touches"),
                    "steps": _br.get("steps"),
                    "thrust": _br.get("thrust"),
                    "agree": _br.get("agree", 0),
                    "against": _br.get("against", 0),
                    "votes": 0, "ready": True,
                    "fib_hit": True, "members": {},
                    "span": 0, "tier": 0, "who": 0, "score": 0,
                    "agents": 0, "wired": True,
                })
        if _WINDOW_SYM.get(idx) not in (None, r["symbol"]):
            log.info("window %d moved %s -> %s -- the new coin's backlog will "
                     "be absorbed, not traded", idx, _WINDOW_SYM[idx],
                     r["symbol"])
            _STUDY_SWAPPED.add(idx)
        _WINDOW_SYM[idx] = r["symbol"]
        return r["symbol"], str(r["res"]), sigs, ohlc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--equity", type=float, default=100.0)
    ap.add_argument("--book", default="", metavar="PATH",
                    help="state file for this book. The default is "
                         "data/paper.json; a second, separate book runs "
                         "beside it with its own state, lock and journal, "
                         "all derived from this path.")
    ap.add_argument("--lev", type=float, default=50)
    ap.add_argument("--frac", type=float, default=0.25)
    ap.add_argument("--tp", type=float, default=1.9)
    ap.add_argument("--sl", type=float, default=0.52)
    ap.add_argument("--tp-atr", type=float, default=0.0, metavar="MULT",
                    help="size the target as this many ATR instead of a fixed "
                         "percent. A coin whose candles breathe 0.4%% cannot "
                         "reach a 2%% target in two bars, and one that breathes "
                         "3%% is barely asked to move; scaling to the coin asks "
                         "every coin the same question. 0 keeps --tp.")
    ap.add_argument("--sl-atr", type=float, default=0.0, metavar="MULT",
                    help="same for the stop. A fixed 0.52%% sits inside a single "
                         "candle on most coins we trade, so the ordinary "
                         "pullback of the next bar takes it out. 0 keeps --sl.")
    ap.add_argument("--fee-bps", type=float, default=12.0)
    ap.add_argument("--colour", nargs="+", default=["purple", "blue"],
                    choices=list(COLOURS))
    ap.add_argument("--min-agents", type=int, default=3)
    ap.add_argument("--day-start", type=float, default=0.0, metavar="HOURS",
                    help="where the trading day begins, in hours past "
                         "midnight UTC. The budget is a day's budget and the "
                         "day belongs to whoever is watching it -- 17.5 makes "
                         "it run from nine in the evening in Tehran.")
    ap.add_argument("--per-day", type=int, default=0, metavar="N",
                    help="how many trades a day to spend, on the best N the "
                         "day offers. Not a cap: a cap refuses the ninth "
                         "signal whatever it is, and the ninth is as likely "
                         "to be the best as the first. The book keeps every "
                         "score it has seen for a day, works out how often "
                         "signals arrive and how good they usually are, and "
                         "raises its bar to whatever percentile it can afford "
                         "with the slots and hours left. A rich day makes it "
                         "fussy, a thin one makes it patient, and the budget "
                         "lands on N rather than on the first N. 0 disables "
                         "the pacing entirely.")
    ap.add_argument("--per-coin-lev", action="store_true", default=False,
                    help="trade each coin at the most leverage the exchange "
                         "allows on it, rather than one number for all of "
                         "them. A coin capped at 25x is traded at 25 and one "
                         "that allows 75 at 75 -- except that the leverage is "
                         "still held to whatever leaves the stop inside the "
                         "liquidation line, because a stop the exchange "
                         "reaches first is not a stop.")
    ap.add_argument("--filter-model", default="", metavar="PATH",
                    help="the learned selector's artifact, trained by "
                         "dataset/selector.py --save-model. A candidate "
                         "the model scores below --filter-min is refused "
                         "with its own reason. A model that will not load "
                         "is a refusal to start, never a silent no-op.")
    ap.add_argument("--filter-min", type=float, default=0.0, metavar="PROB",
                    help="the model probability a candidate must reach, "
                         "0-1. 0 keeps the filter off. The artifact's "
                         "top-10%% cut is the measured default.")
    ap.add_argument("--min-entry", type=float, default=0.0, metavar="POINTS",
                    help="the score a signal must reach, out of a hundred, "
                         "before the book spends its one position on it. "
                         "Built from what measured: agents, a calm coin, the "
                         "six-hour move with the trade, the one-hour move not "
                         "already spent, volume above the coin's own normal, "
                         "and enough ATR to reach the target. 0 takes "
                         "whatever passes the other gates.")
    ap.add_argument("--with-trend", action="store_true", default=False,
                    help="refuse a signal that points against the coin's own "
                         "last two hours. Over 163 of the indicator's own "
                         "signals, one going with the coin won 20.7%% of the "
                         "time for +5.3%% a trade and one going against it "
                         "13.0%% for -12.1%% -- and a coin that had just "
                         "fallen more than five percent produced thirteen "
                         "signals and won none at all.")
    ap.add_argument("--min-atr", type=float, default=0.0, metavar="PCT",
                    help="refuse a signal on a coin whose candles are smaller "
                         "than this, as a percent of price over fourteen bars "
                         "of 15m. This is what separates a good signal from a "
                         "worthless one: over 163 of the indicator's own "
                         "signals, the 24 that fired at three agents on coins "
                         "under 2.5%% ATR won NOTHING, while the 74 above it "
                         "reached +10%% a quarter of the time. The "
                         "indicator's own score and tier do not separate them "
                         "-- inside the three-agent set, PEERLESS measured "
                         "worse than GOOD. Off by default; a coin the finder "
                         "has never measured is never refused on a number "
                         "nobody has.")
    ap.add_argument("--max-body", type=float, default=0.0, metavar="FRAC",
                    help="refuse a signal whose candle is more body than wick, "
                         "as a fraction of its own range. A candle that opened "
                         "at one end and closed at the other is a move that "
                         "already finished; buying it is arriving late. Over "
                         "190 recorded signals a candle above 0.7 body reached "
                         "the target 13.8%% of the time against 24.5%% for one "
                         "under 0.3. Candles thinner than 0.15%% of price are "
                         "exempt -- their shape is rounding noise. 0 is off.")
    ap.add_argument("--skip-tier", type=int, nargs="*", default=[], metavar="T",
                    help="tiers to refuse. The indicator grades a convergence "
                         "3 PEERLESS / 2 EXCELLENT / 1 GOOD by how close "
                         "together the three modules fired. Measured over 294 "
                         "recorded signals, its own top grade is its worst "
                         "signal: tier 3 reached the target inside 15 minutes "
                         "2.7%% of the time against 15.2%% for tier 2. Tight "
                         "convergence means the move already happened.")
    ap.add_argument("--priority", nargs="*", default=[],
                    help="symbols that get first claim on free capacity. When "
                         "the book is full, a signal on one of these closes "
                         "the worst-placed position on a non-priority symbol "
                         "to make room; a non-priority signal simply waits.")
    ap.add_argument("--counter-frac", type=float, default=0.5,
                    help="size multiplier for counter-trend combos, the ones "
                         "only the higher-timeframe phase would have blocked")
    ap.add_argument("--max-exposure", type=float, default=1.0,
                    help="ceiling on total margin in open positions, as a "
                         "multiple of equity. 1.0 lets two 50%% trades run "
                         "together and refuses a third.")
    ap.add_argument("--min-touches", type=int, default=2, metavar="N",
                    help="how many times a price must have been respected "
                         "before it counts as a level. 2 is what the shape "
                         "was built with. Levels that held four times or more "
                         "were the only cut that turned the measured "
                         "expectancy positive, on a small sample.")
    ap.add_argument("--one-per-symbol", action="store_true", default=True,
                    help="never stack two positions on the same coin")
    ap.add_argument("--max-entry-r", type=float, default=0.0, metavar="R",
                    help="refuse a fill that is already this many stops away "
                         "from the signal price -- at 1.0 the whole stop is "
                         "spent before the trade even opens. This was declared "
                         "and never read, so nothing capped it: the book has "
                         "filled at 441 stops out. Default 0 keeps that old "
                         "behaviour; set it to turn the rail on.")
    ap.add_argument("--max-shape-age", type=float, default=8.0,
                    metavar="BARS",
                    help="how old a level-break may be and still be traded; "
                         "the level is a price, not an event")
    ap.add_argument("--max-age", type=float, default=1.0, metavar="BARS",
                    help="refuse a signal whose bar is older than this many "
                         "bars. Replacing a chart study republishes its whole "
                         "history at once; without this the book trades all "
                         "of it as if it had just printed.")
    ap.add_argument("--ct-exit", type=float, default=0.0, metavar="PCT",
                    help="once a position is this far in front, take the "
                         "profit as soon as the indicator prints a "
                         "counter-trend signal against it. The target is ten "
                         "percent and a trade that has run six or seven is "
                         "most of the way there; a counter-trend print is the "
                         "indicator saying the move is being taken back, and "
                         "riding that back down to the stop turns a good "
                         "trade into a loss. 0 disables it.")
    ap.add_argument("--break-even", type=float, default=0.0, metavar="PCT",
                    help="once a trade has run this far in our favour, move "
                         "its stop to the entry price. 0 disables it. "
                         "Measured over 72 signals this was the only exit "
                         "tweak that did not cost money, and the margin was "
                         "inside the noise -- treat it as an experiment.")
    ap.add_argument("--recon-secs", type=float, default=3.0, metavar="SEC",
                    help="how often --live asks the exchange what it is "
                         "actually holding.")
    ap.add_argument("--live", action="store_true",
                    help="place real orders on Bitunix. Without it the same "
                         "decisions are taken and logged, and nothing is sent.")
    ap.add_argument("--max-live", type=int, default=0, metavar="N",
                    help="stop opening new live positions after N of them. "
                         "0 means no cap. A first live session should set it.")
    ap.add_argument("--require-wired", action="store_true", default=False,
                    help="skip a signal when Tesla is not wired. Off: the "
                         "signal is taken anyway.")
    ap.add_argument("--stack", action="store_true", default=False,
                    help="allow more than one position per symbol.")
    ap.add_argument("--step-at", type=float, default=10000.0, metavar="USD",
                    help="equity at which the per-trade size drops to --step-frac.")
    ap.add_argument("--step-frac", type=float, default=0.25,
                    help="per-trade size once equity reaches --step-at.")
    ap.add_argument("--sprint", type=float, default=0.05, metavar="SEC",
                    help="poll gap while a bar is turning over, when a "
                         "signal can actually appear. --interval covers "
                         "the rest of the bar, where nothing can print.")
    ap.add_argument("--catch-up", type=float, default=90.0, metavar="SEC",
                    help="on startup, do not throw away a signal this new. "
                         "A restart should not cost a trade that printed "
                         "seconds ago; the entry grading still decides "
                         "whether the price is good enough. 0 disables.")
    ap.add_argument("--entry-back", type=float, default=0.2, metavar="PCT",
                    help="entry=back: how far behind the signal price the limit "
                         "rests, in percent. Small on purpose -- the candle's "
                         "midpoint is far too deep. 0.2%% back, with the market "
                         "taken if it does not fill, moved 221 recorded signals "
                         "from -0.100%% to +0.036%% a trade: a better price, and "
                         "the maker fee instead of the taker one.")
    # Which chart signal the book trades. "combo" is the original three-robot
    # convergence. "council" is the six-member vote from section 10 of the Pine,
    # which already carries its own entry level, so it ignores --entry entirely
    # and rests at the level the indicator published.
    # Stop-hunt guard. A coin whose ordinary candles routinely throw a shadow
    # as long as the stop will take the stop out on noise alone, whatever the
    # council thinks of the direction. Percent of recent candles allowed to do
    # that; 0 turns the guard off.
    # Volume, in the only form the chart series carries: how big this candle
    # is against the coin's own recent normal. 0 turns it off.
    # What a stop costs the wallet, as a percent of it. Set this and the
    # margin and leverage are both derived per coin, so every trade risks the
    # same amount whatever the coin's own volatility. 0 keeps the old
    # behaviour, where the margin is fixed and the loss is whatever the coin
    # decides.
    # The target, said as a multiple of the stop rather than as a distance.
    # One dial instead of two, and the two can never come apart.
    # How much of the impulse the continuation is asked to repeat. This is
    # the target: it comes from the same leg the entry was drawn on, so the
    # two cannot drift apart. Measured over the recorded history, six tenths
    # of the leg is where the engine actually pays.
    ap.add_argument("--skip-window", type=int, nargs="*", default=[],
                    help="chart windows to leave alone -- the scout owns these")
    ap.add_argument("--scout", action="store_true",
                    help="also trade plans the scout finds on coins no chart "
                         "window is holding")
    ap.add_argument("--tp-leg", type=float, default=0.0,
                    help="target as a fraction of the impulse the entry "
                         "retraced (0 falls back to --tp-r)")
    ap.add_argument("--tp-r", type=float, default=0.0,
                    help="target as a multiple of the stop (4.8 reproduces "
                         "the old +10%%/-2.1%%)")
    ap.add_argument("--r-adapt", type=float, default=0.0,
                    help="how far the coin's own character may move that "
                         "multiple: 0 fixed, 1 fully")
    ap.add_argument("--r-min", type=float, default=3.0,
                    help="the target never sits closer than this many stops")
    ap.add_argument("--r-max", type=float, default=50.0,
                    help="nor further than this -- the ceiling on a runner")
    ap.add_argument("--risk-pct", type=float, default=0.0,
                    help="percent of the wallet a single stop costs")
    ap.add_argument("--tp-margin", type=float, default=0.0, metavar="FRAC",
                    help="the operator's rule: the target pays this fraction "
                         "of the MARGIN, fixed, on every trade. The price "
                         "distance follows the trade's own leverage "
                         "(tp%% = FRAC*100/lev), so a capped coin chases a "
                         "wider move and a 75x coin a tighter one, while the "
                         "money at the target is always FRAC of what was "
                         "risked. 0 keeps --tp/--tp-atr/--tp-r.")
    ap.add_argument("--trail-atr", type=float, default=0.0, metavar="MULT",
                    help="once a trade has run one full ATR in our favour, "
                         "the stop trails MULT x the coin's own ATR behind "
                         "the best price -- never below entry. The measured "
                         "answer to the pullback that eats the almost-there "
                         "trades: over the recorded paths it is the only "
                         "protection whose expectancy beats the bare "
                         "target/stop. 0 off.")
    # How much of the signal candle has to be body rather than shadow. Height
    # says the coin moved; body says it stayed moved.
    # Getting stuck in a range is the way this strategy dies: the target never
    # comes, the stop eventually does, and the margin was unavailable the whole
    # time. These two are the way out.
    ap.add_argument("--stale-min", type=float, default=0.0,
                    help="minutes after which a trade that never travelled is "
                         "closed (0 off)")
    ap.add_argument("--stale-r", type=float, default=0.5,
                    help="how far in our favour it must have gone by then, "
                         "measured in stops")
    ap.add_argument("--max-hold-min", type=float, default=0.0,
                    help="outright limit on how long a position is held, in "
                         "minutes (0 off)")
    # How much of the move must already have happened. The single strongest
    # thing measured over the recorded history: it takes the stop rate from
    # about half to about a third.
    # The one dial that decides. Everything the engine can read about a plan
    # is rolled into a single percentage, and this is how sure it has to be.
    # Whether the exchange is allowed to close a position before our own stop
    # does. It always is in real life; off only for comparing against older
    # records that were written without it.
    # Where the stop goes. Behind the swing the impulse started from, plus a
    # buffer sized from this coin's own shadows -- so reaching it means the
    # premise broke, not that the coin twitched.
    # The stop costs the margin, full stop. Leverage is untouched and the
    # entry carries the risk instead: nothing is taken unless the move has
    # never once come back as far as the stop.
    ap.add_argument("--stop-at-margin", action="store_true",
                    help="place the stop where the loss equals the margin, "
                         "just inside where the exchange would close it")
    ap.add_argument("--min-safety", type=float, default=0.0,
                    help="how many times over the stop must clear the worst "
                         "this move has already done against us")
    ap.add_argument("--min-stairs", type=int, default=0,
                    help="how many of the last fourteen candles must have "
                         "continued the staircase")
    ap.add_argument("--stop-structure", action="store_true",
                    help="place the stop behind the impulse's origin instead "
                         "of at a fixed percentage")
    ap.add_argument("--model-liquidation", action="store_true", default=True,
                    help="close a position when the loss reaches the margin, "
                         "as the exchange would")
    ap.add_argument("--no-model-liquidation", dest="model_liquidation",
                    action="store_false")
    ap.add_argument("--maint-margin", type=float, default=0.005,
                    help="maintenance margin rate, as a fraction of notional")
    # Three or four of these is a day's work. Forty setups a day appear, and
    # taking all of them is a different strategy wearing the same signal --
    # the whole point is that a handful at this reward is worth twenty
    # ordinary trades. So there is a hard count as well as a quality floor:
    # the floor decides what is good enough, the count stops a quiet quality
    # bar from letting a flood through anyway.
    ap.add_argument("--max-per-day", type=int, default=0,
                    help="how many positions may be opened in twenty-four "
                         "hours (0 = no limit)")
    # The only thing that can stop a bad day. config.yaml has described a 20%
    # daily loss limit since the beginning and nothing has ever read that file,
    # so no circuit breaker has been in force at any point: at half the wallet
    # per trade and fifty times leverage a single stop costs about 41% of the
    # account, and nothing counted the second one.
    #
    # Off by default, because switching it on changes what the book does and
    # that is the operator's decision, not this audit's. --live says so loudly
    # at start-up when it is not set.
    ap.add_argument("--daily-loss-limit", type=float, default=0.0,
                    metavar="PCT",
                    help="stop opening positions once the day's realised loss "
                         "reaches this percent of the equity the day started "
                         "with. Open positions are left alone -- their stops "
                         "are already on the exchange. 0 = no limit.")
    ap.add_argument("--min-confidence", type=float, default=0.0,
                    help="how sure the engine must be, 0-100 (0 disables it "
                         "and falls back to the floors below)")
    # The one thing a good score must never be able to buy its way out of.
    #
    # The score adds up, so a strong coin and a unanimous council can carry a
    # candle that did nothing: on a coin covering the target 45% of the time,
    # a 6-0 council alone reaches the floor and ANY shape passes whatever its
    # candle looks like. Only eleven of 573 scanned coins travel far enough
    # for that, and the one such setup in the recorded history stopped out --
    # but it is exactly the trade this strategy is not for.
    #
    # The breaking candle against the coin's own ordinary one is the single
    # strongest thing separating the setups that reach the target from the
    # ones that do not: over 175 resolved setups the winners broke out on
    # 2.3x and the losers on 1.7x. This is a floor, not a score, so nothing
    # can compensate for it. 0 is off.
    # The reward this strategy is built on is the leverage. Ten percent of
    # price is five hundred percent of the margin at fifty times -- and one
    # hundred percent at ten. Rather more than half the coins the scout walks
    # are capped by the exchange below fifty, so the headline the whole design
    # rests on is simply not true on them: same stop, same risk, a fifth of
    # the payout. This refuses them outright.
    #
    # A symbol whose cap cannot be read is refused too. "Unknown" is not
    # "fifty" -- guessing upward here would put a trade on exactly the coin
    # the filter exists to exclude.
    ap.add_argument("--min-lev", type=float, default=0.0, metavar="X",
                    help="refuse a symbol the exchange caps below this "
                         "leverage, whatever the setup looks like (0 = off)")
    ap.add_argument("--min-thrust", type=float, default=0.0, metavar="X",
                    help="refuse a shape whose breaking candle is less than "
                         "this many times the coin's own ordinary candle, "
                         "whatever else the reading says (0 = off)")
    ap.add_argument("--size-by-confidence", action="store_true",
                    help="scale the position with how sure it is, between "
                         "the floor and full size")
    ap.add_argument("--min-run", type=int, default=0,
                    help="of the last six candles, how many must have closed "
                         "the plan's way on a real body")
    ap.add_argument("--min-body", type=float, default=0.0,
                    help="refuse a plan whose signal candle is less than this "
                         "fraction body")
    ap.add_argument("--min-expansion", type=float, default=0.0,
                    help="refuse a plan whose signal candle is smaller than "
                         "this multiple of the coin's ordinary candle")
    ap.add_argument("--max-wick", type=float, default=0.0,
                    help="refuse a plan when more than this %% of recent "
                         "candles wick past the stop on their own")
    ap.add_argument("--source", choices=("combo", "council", "both", "break"),
                    default="combo",
                    help="trade the raw convergence, the council, or both")
    ap.add_argument("--entry",
                    choices=["now", "edge", "market", "smart", "extreme", "mid",
                             "back"],
                    default="now",
                    help="market takes the signal-bar close. extreme rests a "
                         "limit at the signal bar's low (long) or high (short). "
                         "mid splits the difference.")
    ap.add_argument("--fill-bars", type=int, default=3,
                    help="how long to wait for the limit before deciding")
    ap.add_argument("--hunt-seconds", type=float, default=45.0,
                    help="smart entry: how long to hunt for a better price "
                         "before taking whatever the market offers")
    ap.add_argument("--hunt-give-up", type=float, default=0.35,
                    help="smart entry: fraction of the stop distance we are "
                         "willing to be worse than the signal price. Past "
                         "this the entry error eats the trade, so fire.")
    ap.add_argument("--fallback-market", action="store_true", default=True,
                    help="if the limit never fills, take the trade at market "
                         "anyway so no signal is skipped")
    ap.add_argument("--no-fallback", dest="fallback_market",
                    action="store_false",
                    help="let unfilled limits expire instead")
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--reset", action="store_true", help="start a fresh book")
    ap.add_argument("--silent", action="store_true",
                    help="log everything, notify nothing")
    ap.add_argument("--heartbeat", type=float, default=0.0,
                    help="minutes between state pushes. 0 is off, which is the "
                         "default: only opens and closes reach the phone.")
    ap.add_argument("--push-each-close", action="store_true", default=True,
                    help="push the running total every time a trade closes")
    a = ap.parse_args()
    # The state file this run owns, with its lock and its journal beside it.
    # Must happen before journal.attach and before the state is loaded, and
    # it is the whole difference between the book and the beast: same code,
    # same charts, separate ledgers.
    global BOOK
    if a.book:
        BOOK = Path(a.book).expanduser().resolve()
    # The stop ceiling is a statement about THIS run's leverage. Every place
    # that asks for it does so without arguments, so it is set here once, from
    # the flags actually in force, rather than assumed.
    _ROOM["lev"], _ROOM["maint"] = a.lev, a.maint_margin
    _SHAPE["min_touches"] = a.min_touches
    pace.DAY_OFFSET_H = a.day_start
    # A floor above the highest score the scale can produce is a silent stop:
    # every part works, every test passes, and the book simply never trades.
    # That is what happened here, for weeks. It is refused out loud now.
    _unreachable = check_score_reachable(a.min_confidence)
    if _unreachable:
        print(_unreachable)
        return 1

    # The learned filter. A model that will not load is a refusal to
    # start -- a filter that silently does nothing is the classic trap.
    _filter = None
    _filter_stamp = 0.0
    _filter_checked = 0.0
    if a.filter_model:
        try:
            import filter_model as FM
            _filter = FM.FilterModel(a.filter_model)
            _filter_stamp = os.stat(a.filter_model).st_mtime_ns
            print(f"filter: the learned model loaded ({_filter.n} training "
                  f"rows, trained "
                  f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(_filter.trained_at))} "
                  f"UTC); its top-10% cut is {_filter.threshold:.3f}")
            if a.filter_min <= 0:
                print("filter: --filter-min is 0, so the model is loaded "
                      "but refuses nothing")
            else:
                print(f"filter: this book's floor is {a.filter_min:.3f} "
                      f"model probability")
        except Exception as e:
            print(f"--filter-model {a.filter_model} will not load: {e}")
            return 1
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("signals.tv_cdp").setLevel(logging.WARNING)
    # Every decision the book makes is already a log line; this writes the
    # same lines as structured events beside the book, append-only. A
    # journal that can fail must never take the poll down with it.
    try:
        import journal
        journal.attach(log, BOOK.with_suffix(".events.jsonl"))
    except Exception:
        pass
    global SILENT
    SILENT = a.silent
    want = {COLOURS[x] for x in a.colour}
    if 2 in want:
        print("note: orange is in --colour, which contradicts 'never take yellow'")

    if a.min_confidence > 0:
        _able, _seen = floor_coverage(a.min_confidence)
        log.warning("entry: the floor is %.0f of %.0f; a setup reaches %.0f on "
                    "its own and %d of %d scanned coins (%.1f%%) could clear "
                    "the floor at all", a.min_confidence, MAX_ACHIEVABLE_SCORE,
                    QUALITY_ONLY_MAX, _able, _seen,
                    100.0 * _able / max(_seen, 1))
        if a.min_confidence > QUALITY_ONLY_MAX:
            log.warning("      above %.0f no setup can clear the floor on its "
                        "own merits -- every trade then depends on how far the "
                        "COIN travels, which the setup does not control",
                        QUALITY_ONLY_MAX)
        if _seen and _able == 0:
            log.error("no scanned coin can clear the floor -- nothing will be "
                      "traded until the scanner runs again")
    if a.min_lev > 0:
        log.warning("entry: and only coins the exchange allows at least %.0fx "
                    "on -- below that, ten percent of price is not the payout "
                    "this strategy is built on", a.min_lev)
    if a.max_entry_r > 0:
        log.warning("entry: and nothing whose price has already run more than "
                    "%.1f stops from its own level -- that order does not come "
                    "back", a.max_entry_r)
    if a.min_thrust > 0:
        log.warning("entry: and nothing whose breaking candle is under %.2fx "
                    "this coin's own normal, whatever it scores", a.min_thrust)
    elif a.min_confidence > 0 and a.min_confidence <= QUALITY_ONLY_MAX:
        log.warning("entry: no floor on the candle. A coin that travels far "
                    "enough plus a unanimous council reaches %.0f on its own, "
                    "so a shape with no thrust at all can pass. --min-thrust "
                    "is the guard.", CONF_WEIGHTS["reach"] + CONF_WEIGHTS["agree"])

    cli = BitunixClient()

    live_ready: dict[str, dict] = {}
    _lev_cap: dict[str, float] = {}
    _atr_cache: dict[str, tuple[float, float]] = {}      # sym -> (atr%, when)

    def atr_pct(sym: str) -> float | None:
        """ATR(14) on 15m as a percent of price, cached for five minutes.

        Signals are rare next to polls, and ATR barely moves inside a bar, so
        one fetch per coin per five minutes is plenty and costs the book
        nothing at the moment a signal needs answering.

        The cache must stay the cache: the combo loop once bound a local
        `_atr` over it, and the next ATR read died with "float has no get"
        the moment a flag actually asked for it.
        """
        got = _atr_cache.get(sym)
        if got and time.time() - got[1] < 300:
            return got[0]
        try:
            import pandas as pd
            h = cli.history(sym, interval="15m", bars=200)
            if h is None or len(h) < 30:
                return got[0] if got else None
            tr = pd.concat([h.high - h.low,
                            (h.high - h.close.shift()).abs(),
                            (h.low - h.close.shift()).abs()], axis=1).max(axis=1)
            v = float((tr.ewm(alpha=1/14, adjust=False).mean()
                       / h.close * 100).iloc[-1])
            if v != v or v <= 0:
                return got[0] if got else None
            _atr_cache[sym] = (v, time.time())
            return v
        except Exception as e:
            log.debug("atr for %s: %s", sym, str(e)[:60])
            return got[0] if got else None

    _rmul: dict[str, tuple[float, float]] = {}

    def r_multiple(sym: str) -> float:
        """How many stops of target this coin can actually carry.

        The stop is one ATR on every coin, so the only thing left to decide is
        how far away the target sits -- and that is not a property of us, it is
        a property of the coin. Two things say it:

          drive      how much of the recent movement was closed rather than
                     merely reached. A coin whose candles are mostly body is
                     going somewhere; one that is mostly wick is being sold
                     into and bought back, and a far target on it is a target
                     that gets approached and never touched.
          expansion  whether its range is opening or closing. A coin coming
                     out of compression has room in front of it; one already
                     at its widest is finishing a move, not starting one.

        Both are ratios of the coin to itself, so a quiet coin and a violent
        one are judged on the same scale. The result is clamped -- this tunes
        the target, it does not get to invent one.
        """
        got = _rmul.get(sym)
        if got and time.time() - got[1] < 300:
            return got[0]
        base = a.tp_r if a.tp_r > 0 else (a.tp_atr / max(a.sl_atr, 1e-9))
        if a.r_adapt <= 0:
            return base
        try:
            h = cli.history(sym, interval="15m", bars=200)
            if h is None or len(h) < 60:
                return got[0] if got else base
            rng = (h.high - h.low)
            body = (h.close - h.open).abs()
            drive = float(body.tail(40).sum() / max(float(rng.tail(40).sum()), 1e-12))
            recent = float(rng.tail(24).median())
            older = float(rng.iloc[-96:-24].median())
            expansion = recent / older if older > 0 else 1.0
            # drive runs about 0.30 to 0.65 in practice and expansion about
            # 0.6 to 1.8; both are centred so an ordinary coin lands on 1.0.
            lean = (drive / 0.45) * (expansion ** 0.5)
            mult = 1.0 + (lean - 1.0) * a.r_adapt
            mult = max(a.r_min / base, min(a.r_max / base, mult))
            r = max(a.r_min, min(a.r_max, base * mult))
            _rmul[sym] = (r, time.time())
            return r
        except Exception as e:
            log.debug("r_multiple for %s: %s", sym, str(e)[:60])
            return got[0] if got else base

    _atr_warned: set[str] = set()

    def geo(sym: str) -> tuple[float, float]:
        """Target and stop for this coin, as percentages of the entry."""
        if a.tp_atr <= 0 and a.sl_atr <= 0 and a.tp_margin <= 0:
            return a.tp, a.sl
        v = atr_pct(sym)
        if v is None:
            if sym not in _atr_warned:
                _atr_warned.add(sym)
                log.warning("%s: no ATR available, falling back to the fixed "
                            "+%.2f%%/-%.2f%%", sym, a.tp, a.sl)
            return a.tp, a.sl
        sl = v * a.sl_atr if a.sl_atr > 0 else a.sl
        if a.tp_margin > 0:
            # The target pays a fixed fraction of the margin, so its price
            # distance is decided by THIS trade's leverage -- which is the
            # coin's cap held inside the liquidation line by THIS trade's
            # stop. The two must be computed from the same stop or the
            # payment stops being FRAC.
            cap = sym_cap(sym) if a.per_coin_lev else a.lev
            want = a.lev if cap is None else cap
            if a.per_coin_lev:
                room = sl / 100.0 + a.maint_margin
                if room > 0:
                    want = min(want, 1.0 / room)
            lev = max(1.0, want)
            return a.tp_margin * 100.0 / lev, sl
        # The target is expressed against the stop, not against price, so the
        # two can never drift apart: widen the stop for a violent coin and the
        # target widens with it, and the money risked stays the same either way
        # because the margin is derived from the stop as well.
        if a.tp_r > 0 or a.r_adapt > 0:
            tp = sl * r_multiple(sym)
        else:
            tp = v * a.tp_atr if a.tp_atr > 0 else a.tp
        return tp, sl

    def stop_of_margin() -> float:
        """The stop as a price distance, when it is set to cost the margin.

        At a fixed leverage the margin runs out at 1/leverage of adverse price
        movement, less what the exchange keeps back. Placing the stop a shade
        inside that is the furthest a stop can meaningfully go: any further and
        the exchange closes the position first, which is the same loss with a
        worse fill and no control over the exit.

        This is not a risk setting. It is the decision to stop using the stop
        as a risk tool at all -- the entry does that job now, by only taking
        moves that have never come back this far.
        """
        return max(0.05, (1.0 / max(a.lev, 1.0) - a.maint_margin) * 100 * 0.95)

    def geo_leg(sym: str, ohlc, t) -> tuple[float, float]:
        """Target and stop when the target comes from the leg itself.

        The stop is still one ATR -- that is a statement about noise and has
        nothing to do with the leg. The target is a fraction of the impulse,
        projected from the entry, which is why the two stay in step: a small
        leg asks for a small target and a large one asks for more, without
        anybody choosing a number.
        """
        _tp, sl = geo(sym)
        if a.tp_leg <= 0:
            return _tp, sl
        _d, entry, span, _base = leg_of(ohlc, t)
        if not entry or span <= 0:
            return _tp, sl
        tp = span * a.tp_leg / entry * 100
        r = tp / sl if sl else 0.0
        if r < a.r_min:
            tp = sl * a.r_min
        elif r > a.r_max:
            tp = sl * a.r_max
        return tp, sl

    def safe_lev(sym: str, sl_pct: float) -> float:
        """Leverage that still leaves room for the stop to be reached.

        A position is liquidated at roughly 1/leverage against it, so at fifty
        times a stop wider than about two percent is never reached -- the
        exchange closes the position first, at a worse price and for the whole
        margin. Once the stop is sized to the coin rather than fixed, that
        stops being a corner case: a coin whose ordinary candle is three
        percent needs a three percent stop, and fifty times cannot hold one.

        So the stop chooses the leverage, not the other way round. The margin
        is kept at half the liquidation distance so an ordinary wick cannot
        reach it either.
        """
        cap = sym_lev(sym)
        # Only when the stop is being sized to the coin. On a fixed stop the
        # leverage is a number the operator chose deliberately, and quietly
        # capping it turns a 500%-of-margin target into a 230% one -- the
        # setting would be ignored while still appearing to be in force.
        # Leverage is not derived any more. It is what it is set to, and the
        # stop is placed to fit inside it rather than the other way round.
        if sl_pct <= 0 or a.risk_pct <= 0:
            return cap
        return max(1.0, min(cap, 50.0 / sl_pct))

    def sized(sym: str, sl_pct: float, equity: float) -> tuple[float, float]:
        """(margin, leverage) so that hitting the stop costs a fixed slice of
        the wallet, whatever coin it is.

        Without this a quiet coin and a violent one take the same margin and
        lose wildly different amounts, and the account's risk is decided by
        which coin the scanner happened to pick that morning.
        """
        lev = safe_lev(sym, sl_pct)
        if a.risk_pct <= 0 or sl_pct <= 0:
            return equity * live_frac(), lev
        notional = equity * (a.risk_pct / 100.0) / (sl_pct / 100.0)
        return notional / lev, lev

    def sym_cap(sym: str) -> float | None:
        """The exchange's own ceiling for this symbol, or None if unreadable.

        Kept separate from `sym_lev`, which answers a different question --
        what we will actually use. The cap is what decides whether the coin is
        worth trading at all, and an unreadable one must not be mistaken for a
        generous one.
        """
        if sym not in _lev_cap:
            try:
                _lev_cap[sym] = float(cli.max_leverage(sym))
            except Exception as e:
                log.info("%s: leverage cap unreadable (%s)", sym, str(e)[:70])
                return None
        return _lev_cap[sym]

    def sym_lev(sym: str) -> float:
        """The leverage this coin will be traded at.

        With --per-coin-lev the exchange's own ceiling is taken, so a coin that
        allows 75x is traded at 75 and one that allows 25 at 25. Without it,
        --lev is the number and the cap only ever reduces it.

        One thing binds either way: the stop has to be the thing that closes
        the position. The exchange closes it at about (1/leverage - maintenance)
        of adverse price, so a 1.25% stop at 75x -- where that line sits at
        0.83% -- would never be reached. The loss would be the whole margin at
        a worse price and the exit would be the exchange's choice, not ours.
        So the leverage is capped at whatever leaves the stop inside the line.
        """
        cap = sym_cap(sym)
        if not a.per_coin_lev:
            return min(a.lev, a.lev if cap is None else cap)
        want = a.lev if cap is None else cap
        # The most leverage a stop of this size can survive. Applied only
        # here: when the operator names a leverage themselves it is theirs to
        # name, and quietly reducing it would change the size of every trade
        # in a configuration nobody asked to change. When the exchange's
        # ceiling is being taken automatically, nothing else is holding it.
        # With --sl-atr the stop is THIS coin's own stop, and the cap must
        # follow it -- otherwise the leverage names a line the actual stop
        # sits past, and the exit stops being ours.
        stop_pct = a.sl
        if a.sl_atr > 0:
            v = atr_pct(sym)
            if v is not None:
                stop_pct = v * a.sl_atr
        room = stop_pct / 100.0 + a.maint_margin
        if room > 0:
            want = min(want, 1.0 / room)
        return max(1.0, want)


    def live_preflight(symbols: list[str]) -> bool:
        """
        Everything that can be checked without sending an order: keys work, the
        account has money, the symbols exist, our leverage is allowed, and the
        smallest order we would send clears the exchange minimum.

        A failure here is a refusal to start. Discovering any of it at the
        moment a signal prints means a position that should exist does not.
        """
        try:
            eq = cli.equity()
        except Exception as e:
            log.error("cannot read the account: %s", str(e)[:160])
            log.error("--live needs BITUNIX_API_KEY and BITUNIX_API_SECRET in .env")
            return False
        log.info("live account equity $%.2f", eq)
        if eq <= 0:
            log.error("the account holds nothing; refusing to run --live")
            return False
        try:
            mode = cli.position_mode()
        except Exception as e:
            log.error("cannot read the position mode: %s", str(e)[:120])
            return False
        log.info("position mode: %s", mode)
        ok = True
        for sym in symbols:
            try:
                mx = cli.max_leverage(sym)
                mn = cli.min_qty(sym)
                lev = min(a.lev, mx)
                if lev < a.lev:
                    log.warning("%s caps leverage at %dx, below the %gx asked",
                                sym, mx, a.lev)
                # The smallest position this config can open, at the smallest
                # size step it will ever use.
                smallest = eq * min(a.frac, a.step_frac) * a.counter_frac
                px = prices.get(sym) or 0
                if not px:
                    log.error("%s has no price on Bitunix", sym)
                    ok = False
                    continue
                q = cli.round_qty(sym, smallest * lev / px)
                if q < mn:
                    log.error("%s: the smallest order this config makes is %g, "
                              "below the exchange minimum of %g. Raise --frac or "
                              "fund the account.", sym, q, mn)
                    ok = False
                cli.set_margin_mode(sym, "ISOLATION")
                cli.set_leverage(sym, int(lev))
                live_ready[sym] = {"lev": int(lev), "min_qty": mn}
                log.info("%s ready: %dx isolated, min qty %g, smallest order %g",
                         sym, int(lev), mn, q)
            except Exception as e:
                log.error("%s not ready: %s", sym, str(e)[:160])
                ok = False
        return ok


    def announce(tr: Trade, how: str = "") -> None:
        """A position opened. Say so, once, from wherever it opened.

        Three of the six places a position can open said nothing -- including
        the one the live configuration actually uses. Closes were announced
        and opens were not, so the phone reported the end of trades whose
        beginning it had never mentioned. One function, called from every
        site, is the only way that stays fixed.
        """
        side = "LONG" if tr.side == "BUY" else "SHORT"
        # Still short enough to read on a lock screen, but it now says which
        # of the day's eight this was and what the book thought of it. With a
        # budget, "the fourth of eight, scored 71" is the whole story of the
        # decision; without it the phone reported a price and nothing else.
        head = f"{tr.sym} {side}"
        if a.per_day > 0:
            head += f"  {pace.spent(state.get('trades') or trades)}/{a.per_day}"
        line = f"{tr.entry:.8g}"
        if getattr(tr, "score", 0):
            line += f"  ·  {tr.score:.0f}/100"
        ping(head, f"{line}\n${state['equity']:,.2f}")

    def live_frac() -> float:
        """Per-trade size for the equity standing right now."""
        if a.step_at > 0 and state["equity"] >= a.step_at:
            return a.step_frac
        return a.frac

    # ------------------------------------------------------------------
    # The live entry, for the sources whose entry IS a resting order.
    #
    # A shape and a council plan both carry the price they want to be filled
    # at, and the tight stop is only defensible because of it: the band is
    # narrow because the coin had gone quiet, and a real break does not come
    # back through it. Entering anywhere else is a different trade wearing the
    # same signal.
    #
    # So this places a genuine limit order at that level. A limit can never
    # fill worse than its price, which is the property the whole geometry
    # depends on -- and it is naturally a maker order, because a long rests
    # BELOW the market and a short rests ABOVE it. Watching the ticker and
    # firing a market order when it touches would give away exactly the
    # difference the strategy exists to capture.
    # ------------------------------------------------------------------
    def order_qty(sym: str, price: float, margin: float, lev: float):
        """(qty string, qty float) or None when the exchange would refuse it."""
        if price <= 0:
            return None
        try:
            raw = margin * lev / price
            qty_s = cli.fmt_qty(sym, cli.round_qty(sym, raw))
            qty_f = float(qty_s)
        except Exception as e:
            log.error("cannot size %s: %s", sym, str(e)[:120])
            return None
        if qty_f <= 0:
            log.info("skip %s -- the order rounds to nothing at %.8g",
                     sym, price)
            return None
        try:
            mn = live_ready.get(sym, {}).get("min_qty")
            if mn is None:
                mn = cli.min_qty(sym)
        except Exception:
            mn = 0.0
        if mn and qty_f < mn:
            log.info("skip %s -- order %s is under the exchange minimum %g",
                     sym, qty_s, mn)
            return None
        return qty_s, qty_f

    def send_resting(pnd: Pending, tp_pct: float, sl_pct: float,
                     tag: str) -> bool:
        """Rest a real limit order at the plan's level. True if it is on the book.

        The stop and the target ride on the same request. A protective order
        sent afterwards can fail or land late, and a fifty-times position that
        is naked for even a few seconds is the one thing this must never do.
        """
        sym, side = pnd.sym, pnd.side
        lev = pnd.sig.get("lev") or sym_lev(sym)
        got = order_qty(sym, pnd.want, pnd.sig.get("margin", 0.0), lev)
        if not got:
            return False
        qty_s, qty_f = got
        entry = pnd.want
        tp = entry * (1 + tp_pct / 100) if side == "BUY" else entry * (1 - tp_pct / 100)
        sl = entry * (1 - sl_pct / 100) if side == "BUY" else entry * (1 + sl_pct / 100)
        try:
            px_s = cli.fmt_price(sym, entry)
            tp_s, sl_s = cli.fmt_price(sym, tp), cli.fmt_price(sym, sl)
        except Exception as e:
            log.error("cannot price %s: %s", sym, str(e)[:120])
            return False
        # Unique per plan, and the only handle that survives a restart.
        pnd.client_id = (f"tbt{int(pnd.sig.get('t') or 0)}{side[0]}"
                         f"L{sym}")[:32]
        pnd.qty = qty_f
        if any(t.client_id == pnd.client_id for t in trades):
            log.info("skip %s -- %s has already been traded", tag,
                     pnd.client_id)
            return False

        def _send(post_only: bool):
            return cli.place_limit_order(
                sym, side, qty_s, px_s, tp_price=tp_s, sl_price=sl_s,
                client_id=pnd.client_id, post_only=post_only)

        try:
            r = _send(True)
        except BitunixUnknown as e:
            # Sent, never answered. It may be resting right now.
            st = cli.order_state(client_id=pnd.client_id)
            if st["status"] in ("new", "part_filled", "filled"):
                pnd.live, pnd.order_id = True, st["order_id"]
                log.error("%s -- the reply was lost but the order EXISTS (%s); "
                          "adopting it", tag, st["order_id"] or pnd.client_id)
                return True
            if st["status"] == "unknown":
                log.error("%s -- sent and never answered, and its state cannot "
                          "be read. NOT resting it locally; check %s by hand.",
                          tag, pnd.client_id)
                ping(f"CHECK {sym} BY HAND",
                     f"A {side} limit for {sym} was sent and never answered, "
                     f"and its state cannot be read. clientId {pnd.client_id}")
                return False
            log.error("ORDER UNKNOWN %s: %s", tag, str(e)[:160])
            return False
        except BitunixError as e:
            # A post-only order is refused when it would cross -- which here
            # means price is already through the level. That is not an error,
            # it is the "already there" case, and a plain limit at the same
            # price still cannot fill worse than the level.
            if _crossing(e):
                log.info("%s -- price is already at the level; resting a plain "
                         "limit instead of a maker-only one", tag)
                try:
                    r = _send(False)
                except Exception as e2:
                    log.error("ORDER FAILED %s: %s", tag, str(e2)[:160])
                    ping(f"ORDER FAILED - {sym}", f"{side} {sym}\n{str(e2)[:180]}")
                    return False
            else:
                log.error("ORDER FAILED %s: %s", tag, str(e)[:180])
                ping(f"ORDER FAILED - {sym}", f"{side} {sym}\n{str(e)[:180]}")
                return False
        except Exception as e:
            # Same reading as above, for a venue that signals the crossing
            # refusal with something other than a BitunixError.
            if _crossing(e):
                log.info("%s -- price is already at the level; resting a plain "
                         "limit instead of a maker-only one", tag)
                try:
                    r = _send(False)
                except Exception as e2:
                    log.error("ORDER FAILED %s: %s", tag, str(e2)[:160])
                    ping(f"ORDER FAILED - {sym}", f"{side} {sym}\n{str(e2)[:180]}")
                    return False
            else:
                log.error("ORDER FAILED %s: %s", tag, str(e)[:180])
                ping(f"ORDER FAILED - {sym}", f"{side} {sym}\n{str(e)[:180]}")
                return False
        pnd.live = True
        pnd.order_id = str((r or {}).get("orderId") or "")
        log.info("LIVE  %s | resting %s at %s  tp %s  sl %s  %.0fx  "
                 "margin $%.2f  order %s", tag, qty_s, px_s, tp_s, sl_s, lev,
                 pnd.sig.get("margin", 0.0), pnd.order_id or pnd.client_id)
        return True

    def open_from_fill(pnd: Pending, filled: float, avg: float | None,
                       kind: str) -> Trade | None:
        """Book the position the exchange actually gave us.

        Not the size that was ordered and not the price that was asked for: a
        partial fill is a real position of a size nobody chose, and recording
        the intended one would put the stop distance, the margin and every
        later PnL on a quantity that does not exist.
        """
        if filled <= 0:
            return None
        entry = avg if (avg and avg > 0) else pnd.want
        _tp = pnd.sig.get("tp") or geo(pnd.sym)[0]
        _sl = pnd.sig.get("sl") or geo(pnd.sym)[1]
        lev = pnd.sig.get("lev") or sym_lev(pnd.sym)
        notional = filled * entry
        tr = Trade(
            sym=pnd.sym, side=pnd.side, qty=filled, entry=entry,
            tp=entry * (1 + _tp / 100) if pnd.side == "BUY"
            else entry * (1 - _tp / 100),
            sl=entry * (1 - _sl / 100) if pnd.side == "BUY"
            else entry * (1 + _sl / 100),
            opened=time.time(), bar=pnd.sig.get("t", 0),
            who=pnd.sig.get("who", 0), tier=pnd.sig.get("tier", 0),
            score=pnd.sig.get("score", 0), agents=pnd.sig.get("agents", 0),
            margin=notional / lev if lev else notional,
            notional=notional, entry_kind=kind,
            counter=bool(pnd.sig.get("counter")),
            live=True, order_id=pnd.order_id, client_id=pnd.client_id)
        trades.append(tr)
        short = "" if pnd.qty <= 0 or filled >= pnd.qty - 1e-12 else (
            f"  PARTIAL {filled:g} of {pnd.qty:g}")
        log.info("LIVE OPEN %s %s | %g @ %.8g  tp %.8g  sl %.8g  "
                 "margin $%.2f%s", tr.side, tr.sym, tr.qty, tr.entry, tr.tp,
                 tr.sl, tr.margin, short)
        ping(f"{tr.sym} {'LONG' if tr.side == 'BUY' else 'SHORT'}",
             f"entry  {tr.entry:.8g}\nبرکت  {tr.tp:.8g}\n"
             f"بدفازی  {tr.sl:.8g}\n${state['equity']:,.2f}"
             + (f"\n{short.strip()}" if short else ""))
        return tr

    def cancel_resting(pnd: Pending, why: str) -> None:
        """Take the order off the book, and book whatever it filled first."""
        if pnd.settled:
            return
        pnd.settled = True
        try:
            cli.cancel_orders(pnd.sym, client_ids=[pnd.client_id])
        except BitunixUnknown:
            log.warning("cancel of %s was sent and never answered", pnd.client_id)
        except Exception as e:
            log.info("cancel of %s: %s", pnd.client_id, str(e)[:100])
        # Cancelling races the fill. Ask once more what actually happened
        # rather than assuming the cancel won -- a fill that lands in that gap
        # is a real position, and dropping it here is how one becomes invisible.
        st = cli.order_state(order_id=pnd.order_id or None,
                             client_id=pnd.client_id)
        if st["filled"] > pnd.filled:
            log.warning("%s filled %g while it was being cancelled",
                        pnd.sym, st["filled"])
        got = max(st["filled"], pnd.filled)
        if got > 0:
            open_from_fill(pnd, got, st["avg_price"], "limit-partial")
        else:
            fills["expired"] += 1
            log.info("DROP  %s %s -- %s (%d filled / %d dropped)", pnd.side,
                     pnd.sym, why, fills["filled"], fills["expired"])

    def watch_live_orders() -> None:
        """The exchange decides whether we are in; this reads its answer."""
        for pnd in list(resting):
            if not pnd.live or pnd.settled:
                continue
            st = cli.order_state(order_id=pnd.order_id or None,
                                 client_id=pnd.client_id)
            if st["order_id"] and not pnd.order_id:
                pnd.order_id = st["order_id"]
            pnd.filled = max(pnd.filled, st["filled"])
            if st["status"] == "filled":
                pnd.settled = True
                resting.remove(pnd)
                fills["filled"] += 1
                open_from_fill(pnd, st["filled"] or pnd.qty, st["avg_price"],
                               "limit")
                continue
            if st["status"] == "rejected":
                pnd.settled = True
                resting.remove(pnd)
                log.error("REJECTED %s %s -- the exchange refused the order",
                          pnd.side, pnd.sym)
                ping(f"ORDER REJECTED - {pnd.sym}",
                     f"{pnd.side} {pnd.sym} was refused by the exchange.")
                continue
            if st["status"] == "cancelled":
                # Already off the venue's book -- sending another cancel
                # asks again for something that no longer exists, and the
                # venue's answer to that is noise. Book it as the miss it
                # is, in the same words the funnel counts.
                resting.remove(pnd)
                pnd.settled = True
                fills["expired"] += 1
                log.info("DROP  %s %s -- cancelled at the exchange "
                         "(%d filled / %d dropped)", pnd.side, pnd.sym,
                         fills["filled"], fills["expired"])
                continue
            if st["status"] == "part_filled" and st["filled"] > 0:
                # There is a real position on the account NOW, with the stop
                # and target already attached to it. Two things follow, and
                # both matter more than the unfilled remainder.
                #
                # The book has to agree with the account immediately -- an
                # unbooked position is one the exposure check cannot see, so
                # the next plan would be sized as though the wallet were free.
                #
                # And the remainder has to go. Leaving it resting means the
                # position can grow later, underneath a stop distance and a
                # margin already computed for the smaller size. The rest of
                # the order is a miss, which is what an unfilled order has
                # always been here.
                resting.remove(pnd)
                fills["filled"] += 1
                cancel_resting(pnd, "partially filled")
                continue
            if time.time() >= pnd.expires:
                resting.remove(pnd)
                cancel_resting(pnd, f"never returned to {pnd.want:.8g}")

    def adopt_live_orders(symbols) -> None:
        """Anything of ours still resting when the process starts again.

        A restart in the middle of a resting order used to be invisible: the
        book forgot the order, the exchange kept it, and it could fill hours
        later into an account nothing was watching. Ours are recognisable by
        the clientId prefix.
        """
        for sym in symbols:
            try:
                for row in cli.pending_orders(sym) or []:
                    st = cli.read_order(row)
                    cid = st["client_id"]
                    if not cid.startswith("tbt"):
                        continue
                    if any(t.client_id == cid for t in trades):
                        continue
                    log.warning("adopting %s left resting on %s from an "
                                "earlier run -- cancelling it, because the "
                                "plan behind it is gone", cid, sym)
                    p = Pending(sym=sym, side=str(row.get("side") or "BUY"),
                                want=float(row.get("price") or 0) or 0.0,
                                give_up=0.0, signal_px=0.0, placed=time.time(),
                                deadline=time.time(), expires=time.time(),
                                live=True, order_id=st["order_id"],
                                client_id=cid, filled=st["filled"])
                    cancel_resting(p, "left over from an earlier run")
            except Exception as e:
                log.warning("could not check %s for leftover orders: %s",
                            sym, str(e)[:100])

    def day_loss() -> tuple[float, float]:
        """(lost today, what the day started with) -- realised only.

        Open positions are deliberately excluded: their stops are already on
        the exchange, and a breaker that counted unrealised drawdown would
        halt on a position that is about to come back, while doing nothing
        about the one that already cost the money.
        """
        since = time.time() - 86400
        done = [t for t in trades if t.closed and (t.closed or 0) >= since]
        realised = sum(t.pnl for t in done)
        started = state["equity"] - realised
        return (-realised if realised < 0 else 0.0), max(started, 1e-9)

    def loss_limit_hit() -> bool:
        if a.daily_loss_limit <= 0:
            return False
        lost, started = day_loss()
        return lost >= started * a.daily_loss_limit / 100.0

    def day_full(trades) -> bool:
        """Has the twenty-four hour count already been spent?

        Asked again at the moment a position actually opens, not only when the
        order was placed. Three shapes arriving in one poll all passed a cap of
        one, because none of them had opened anything yet when they were
        judged -- the count they were compared against was still zero. A limit
        resting for eight bars leaves the same gap open for two hours.
        """
        if a.max_per_day <= 0:
            return False
        since = time.time() - 86400
        return len([t for t in trades if (t.opened or 0) >= since]) >= \
            a.max_per_day
    # A taker crosses the book, so the fill is the far side, not the last
    # print. Without this the simulation quietly grants a better price than
    # Bitunix would ever give, on every single trade.
    # (spread in bps, when it was read). A spread is a fact about the book
    # right now, and it was being measured once per symbol and then believed
    # for the life of the process -- so every fill in a session that runs for
    # days was priced off a reading taken at start-up, through whatever the
    # market did afterwards. The whole point of crossing the spread in the
    # simulation is to stop granting a better price than Bitunix would give;
    # a stale reading quietly gives it back.
    spreads: dict[str, tuple[float, float]] = {}

    def cross(sym: str, px: float, side: str) -> float:
        got_cached = spreads.get(sym)
        if got_cached is None or time.time() - got_cached[1] > SPREAD_TTL_S:
            got = None
            try:
                got = float(cli.spread_bps(sym))
            except Exception:
                got = None
            # An empty or malformed order book does not raise -- spread_bps
            # returns infinity for it, and infinity was taken at face value.
            # The entry price became inf, the quantity zero, the stop inf, and
            # the stop then read as hit on the very next poll: exit inf, move
            # inf/inf, PnL nan, equity nan for the rest of the process's life
            # and in the book on disk. One bad depth reply is not a reason to
            # stop trading, but it is every reason not to believe it.
            if got is None or got != got or got < 0 or got > MAX_SPREAD_BPS:
                if got is not None and got == got:
                    log.warning("%s: spread read as %.1f bps -- refusing it and "
                                "using %.1f", sym, got, DEFAULT_SPREAD_BPS)
                # A bad reading must not be cached as though it were good, or
                # one unreadable book poisons the symbol for the whole run.
                keep = got_cached[0] if got_cached else DEFAULT_SPREAD_BPS
                spreads[sym] = (keep, time.time() - SPREAD_TTL_S / 2)
            else:
                spreads[sym] = (got, time.time())
        half = spreads[sym][0] / 2 / 1e4
        return px * (1 + half) if side == "BUY" else px * (1 - half)

    # Two instances sharing one book is silent corruption, not a crash: both
    # load it, both append, and whichever writes last erases the other's
    # trades. It happened once -- a closed -$28 stop vanished from the record.
    BOOK.parent.mkdir(parents=True, exist_ok=True)
    # Named after the book it guards, not after the file this happened to be
    # called first. Two books are two accounts and may run at once -- a
    # scratch one alongside the live one, for instance -- while two processes
    # on the SAME book is the thing that loses trades, and that is still
    # refused. The live book keeps the same lock name it always had.
    lock = BOOK.with_suffix(".lock")
    if lock.exists():
        try:
            old = int(lock.read_text().strip())
            os.kill(old, 0)
        except (ProcessLookupError, ValueError):
            pass                                  # stale lock, take it
        except PermissionError:
            pass
        else:
            print(f"another papertrade is already running (pid {old}). "
                  f"Stop it first:\n  pkill -f papertrade.py")
            return 1
    lock.write_text(str(os.getpid()))

    state = {"equity": a.equity, "start": a.equity, "trades": [], "seen": []}
    if BOOK.exists() and not a.reset:
        try:
            state = json.loads(BOOK.read_text())
        except Exception as e:
            print(f"the book at {BOOK} exists but will not parse: {e}\n"
                  f"Refusing to start: continuing would reset the equity and "
                  f"re-trade every signal in the chart's history.\n"
                  f"Move it aside to start a fresh book:\n"
                  f"  mv {BOOK} {BOOK}.broken")
            return 1
    # Keys used to be (symbol, bar, side); they are now (symbol, bar, side,
    # counter). A saved book full of the old shape would match nothing, and
    # because `first` is false whenever the book is non-empty, every signal on
    # the chart would read as new and be traded at once. Old keys are widened
    # to cover both streams so nothing already seen can fire again.
    seen = set()
    for x in state["seen"]:
        k = tuple(x)
        if len(k) == 3:
            seen.add((*k, False))
            seen.add((*k, True))
        else:
            seen.add(k)
    # Symbols whose backlog has already been absorbed. Switching a chart to a
    # new coin presents that coin's whole signal history at once; without this
    # every one of them reads as new and the book opens dozens of stale
    # positions in a single poll.
    known_syms = set(state.get("known_syms", []))
    trades = [Trade(**t) for t in state["trades"]]
    # Orders still waiting for price to come back to their level.
    #
    # These used to live only in memory, so every restart threw them away --
    # and because the signal that produced one is already in `seen`, it was
    # never offered again. The plan was consumed and then discarded. With
    # Restart=always that happens on every crash and every deploy, and it
    # quietly understates how many setups this strategy would have taken,
    # which is the one number the whole paper run exists to measure.
    resting: list[Pending] = []
    for _r in state.get("resting", []):
        try:
            _p = Pending(**_r)
        except Exception as e:
            log.warning("a saved pending order will not load: %s", str(e)[:80])
            continue
        # A live order is the exchange's, not ours to re-imagine: whatever is
        # still resting there is found and dealt with by `adopt_live_orders`.
        if _p.live:
            continue
        if time.time() >= _p.expires:
            log.info("dropped %s %s from the last run -- its window had "
                     "already passed", _p.side, _p.sym)
            continue
        resting.append(_p)
    if resting:
        log.info("carried %d resting order(s) over the restart: %s",
                 len(resting),
                 ", ".join(f"{x.side} {x.sym} @ {x.want:.8g}" for x in resting))
    fills = {"filled": 0, "expired": 0}
    # Always absorb whatever is already on the chart at startup. This was
    # `not seen`, which meant a book with any history skipped the absorb pass
    # entirely -- so every signal that printed while the bot was stopped got
    # traded the moment it came back, one of them 26 minutes stale.
    # This is not a filter on live signals: the bot takes every signal it sees
    # print while it is running, and none it did not. --catch-up still lets a
    # signal from the last seconds through, so a restart costs nothing real.
    first = True

    # macOS sends SIGTERM on shutdown and on logout. Catching it buys the one
    # send that matters most -- the state as of the moment the machine went
    # away. It fails if the network is already gone, which is why the
    # heartbeat exists as well.
    _leaving = []

    def _bye(signum, frame):
        # A second signal during the final report would push it twice and log a
        # second shutdown line for one shutdown.
        if _leaving:
            raise KeyboardInterrupt
        _leaving.append(True)
        log.info("shutting down -- pushing a final report")
        pass  # the phone gets opens and closes; there is no separate report
        raise KeyboardInterrupt

    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, _bye)
        except Exception:
            pass
    last_beat = time.time()
    last_px = 0.0
    prices: dict[str, float] = {}
    marks: dict[str, float] = {}
    last_recon = 0.0
    real_hl: dict[tuple[str, int], list[float]] = {}

    n = TradingViewCDP.chart_windows()

    if a.live:
        if a.break_even > 0:
            print("--live refuses --break-even.\n"
                  "The stop lives on the exchange, attached to the entry order,"
                  " and there is no call here that can move it. The book would"
                  " believe the stop had moved while the account still held the"
                  " original one.")
            return 1
        LIVE_SAFE = ("now", "market")
        if a.entry not in LIVE_SAFE:
            print(f"--live refuses --entry {a.entry}.\n"
                  f"Only {' and '.join(LIVE_SAFE)} place a real order before "
                  f"recording the position; the others record first and send "
                  f"nothing, so the book would hold trades the exchange does "
                  f"not.\nRe-run with --entry now.")
            return 1
        # A council plan and a shape do not go through the entry modes at all:
        # both carry the price they want to be filled at. In --live they are
        # placed as real limit orders AT that level, watched by
        # `watch_live_orders`, and booked from the exchange's own fill. That
        # code did not exist until it was written: this configuration used to
        # print LIVE BOOK, read the real account's equity, prefix every
        # notification with [REAL], and then open positions that existed in
        # the local JSON file and nowhere else.
        #
        # A limit order is not a convenience here, it is the strategy: the
        # stop can be tight because the entry is the level, and a limit can
        # never fill worse than its price. Watching the ticker and firing a
        # market order on the touch would give away exactly the difference the
        # whole shape exists to capture.
        global LIVE_MODE
        LIVE_MODE = True
        # Prices are needed before the pre-flight can size a test order, and the
        # pre-flight has to pass before a single signal is looked at.
        try:
            _tk = cli.tickers()
            prices = {t["symbol"]: float(t["lastPrice"]) for t in _tk}
        except Exception as e:
            print(f"cannot reach Bitunix: {e}")
            return 1
        _syms = []
        for _i in range(n):
            try:
                _got = read_window(_i)
                if _got:
                    _syms.append(_got[0].split(":")[-1].replace(".P", ""))
            except Exception:
                pass
        if not _syms:
            print("no chart is readable; refusing to start --live")
            return 1
        print(f"LIVE  preparing {', '.join(_syms)}")
        if not live_preflight(_syms):
            print("pre-flight failed; nothing was sent and nothing will be")
            return 1
        # Anything of ours still resting from a previous run. A restart in the
        # middle of a resting order was invisible: the book forgot it, the
        # exchange kept it, and it could fill hours later into an account with
        # nothing watching. The plan that justified it is gone, so it goes.
        adopt_live_orders(_syms)
        # The exchange is the authority on what the account holds.
        state["equity"] = cli.equity()
        for _s in _syms:
            print(f"LIVE  {_s}: {sym_lev(_s):.0f}x"
                  + (f" (asked {a.lev:.0f}x, exchange caps it)"
                     if sym_lev(_s) < a.lev else ""))
        print(f"LIVE  REAL MONEY. equity ${state['equity']:.2f}"
              + (f", capped at {a.max_live} positions" if a.max_live else ""))
        if a.daily_loss_limit > 0:
            print(f"LIVE  halts for the day after a "
                  f"{a.daily_loss_limit:.0f}% realised loss")
        else:
            _one = a.frac * 100 * min(a.sl, _stop_room()) * a.lev / 100
            print(f"LIVE  WARNING no daily loss limit is set. One stop at "
                  f"{a.frac*100:.0f}% of the wallet and {a.lev:.0f}x costs "
                  f"about {_one:.0f}% of the\n"
                  f"      account, and nothing here counts the second one. "
                  f"--daily-loss-limit is the only\n"
                  f"      circuit breaker this engine has; config.yaml's is "
                  f"read by nothing.")
    print(f"{'LIVE BOOK ' if a.live else 'PAPER BOOK'}  equity ${state['equity']:.2f} "
          f"(started ${state['start']:.2f})  book {BOOK.name}")
    print(f"following {n} chart window(s)")
    if a.source == "break":
        print("source: a quiet band, then one candle straight out of it")
        print("        The stop is the far side of that band -- not a number "
              "anybody chose,\n        which is why it can be tight and still "
              "be honest.")
        print("        The council is not asked. It reads \"no plan\" on the "
              "very chart where\n        this trade makes ten percent.")
        print(f"target: {a.tp:.0f}% of price, which at {a.lev:.0f}x is "
              f"{a.tp * a.lev:.0f}% of the margin committed")
        print(f"size:   {a.frac*100:.0f}% of the wallet per trade")
        if a.min_confidence > 0 or a.max_per_day > 0:
            bits = []
            if a.min_confidence > 0:
                bits.append(f"nothing under {a.min_confidence:.0f}% sure")
            if a.max_per_day > 0:
                bits.append(f"at most {a.max_per_day} a day")
            print("pick:   " + ", ".join(bits) +
                  " -- a handful at this reward is worth\n"
                  "        twenty ordinary trades, and taking all forty a day "
                  "is a different\n        strategy wearing the same signal")
        # Say what will actually happen, not what the design intended.
        #
        # "unfilled is a miss, not a loss" is the strategy's own sentence and
        # it is only true with --no-fallback. The flag defaults to ON, and the
        # live unit does not turn it off, so an order that never fills is
        # taken at MARKET when its window closes -- two hours later, at a
        # price the plan never saw, with the stop still measured from the
        # level. The banner said one thing and the code did the other.
        if a.fallback_market:
            print(f"entry:  the band's edge, rested for {a.fill_bars} bars -- "
                  f"then TAKEN AT MARKET if it never filled\n"
                  f"        (--no-fallback makes an unfilled order a miss "
                  f"instead, which is what\n"
                  f"        the shape's tight stop assumes)\n")
        else:
            print(f"entry:  the band's edge, rested for {a.fill_bars} bars -- "
                  f"unfilled is a miss, not a loss\n")
    elif a.source == "council":
        print("source: the council -- six members, a plan needs their agreement")
        if a.min_expansion > 0:
            print(f"        refused unless the signal candle is at least "
                  f"{a.min_expansion:.2g}x the coin's own normal size")
        if a.stop_at_margin:
            _sd = max(0.05, (1.0 / max(a.lev, 1.0) - a.maint_margin)
                      * 100 * 0.95)
            print(f"stop:   the whole margin -- {_sd:.2f}% of price at "
                  f"{a.lev:.0f}x, a shade inside where the\n"
                  f"        exchange would close it. It is not a risk setting; "
                  f"the entry is.")
            if a.min_safety > 0:
                print(f"        Nothing is taken unless this move has never "
                      f"come back within {a.min_safety:g}x of\n"
                      f"        that distance -- the NIULAI shape, measured "
                      f"rather than hoped for.")
        if a.min_confidence > 0:
            print(f"judge:  every plan is read on how far the move has already "
                  f"gone, the shape of its\n        candle and the coin's own "
                  f"shadow habit, rolled into one number; "
                  f"{a.min_confidence:.0f}%\n        is the least it may be")
            if a.size_by_confidence:
                print("        and the position is scaled by that number, not "
                      "fixed")
        if a.min_run > 0:
            print(f"        and unless {a.min_run} of the last six candles have "
                  f"already closed the plan's way -- we join a move,\n"
                  f"        we do not predict one")
        if a.min_body > 0:
            print(f"        and unless at least {a.min_body*100:.0f}% of it is "
                  f"body -- a tall candle that is mostly wick is a fight, "
                  f"not a move")
        if a.max_wick > 0:
            print(f"        refused when over {a.max_wick:.0f}% of recent "
                  f"candles clear that coin's own stop by themselves")
        if a.tp_r > 0 or a.r_adapt > 0:
            print(f"shape:  stop {a.sl_atr:g} x the coin's own 15m ATR, "
                  f"target {a.tp_r:g} stops away")
            if a.r_adapt > 0:
                print(f"        moved between {a.r_min:g}R and {a.r_max:g}R by "
                      f"how much of this coin's movement is\n        closed "
                      f"rather than merely reached, and whether its range is "
                      f"opening")
        elif a.tp_atr > 0 or a.sl_atr > 0:
            print(f"shape:  stop {a.sl_atr:g} x the coin's own 15m ATR, "
                  f"target {a.tp_atr:g} x")
        else:
            print(f"shape:  +{a.tp:.1f}%/-{a.sl:.2f}% fixed on every coin")
        if a.risk_pct > 0:
            print(f"size:   whatever margin makes a stop cost "
                  f"{a.risk_pct:g}% of the wallet, at the highest leverage "
                  f"that still\n        leaves room for that stop to be "
                  f"reached rather than liquidated through")
        else:
            print(f"size:   {a.frac*100:.0f}% at {a.lev:.0f}x")
        print(f"        {a.fee_bps:.1f} bps round trip")
        if a.stale_min > 0 or a.max_hold_min > 0:
            bits = []
            if a.stale_min > 0:
                bits.append(f"closed after {a.stale_min:.0f}m if it has not "
                            f"travelled {a.stale_r:g} stops in our favour")
            if a.max_hold_min > 0:
                bits.append(f"closed outright at {a.max_hold_min:.0f}m")
            print("clock:  " + ";\n        ".join(bits))
        if a.model_liquidation and a.lev > 0 and a.sl > 0:
            _liq = ((1.0 / a.lev) - a.maint_margin) * 100
            if _liq <= a.sl:
                print(f"WARNING at {a.lev:.0f}x the exchange closes the "
                      f"position around {_liq:.2f}% against us, which is\n"
                      f"        nearer than the {a.sl:.2f}% stop -- the stop "
                      f"will never be reached. The\n"
                      f"        loss is the whole margin and the exit is the "
                      f"exchange's, not ours.\n"
                      f"        {1.0/(a.sl/100 + a.maint_margin):.0f}x is the "
                      f"highest leverage this stop fits inside.")
        print(f"entry:  the level the council published, rested for "
              f"{a.fill_bars} bars -- unfilled is a miss, not a loss\n")
    else:
        print(f"filter: colour {'/'.join(a.colour)}"
              + (f", never tier {'/'.join(str(t) for t in sorted(a.skip_tier))}"
                 if a.skip_tier else "")
              + (f", candle body under {a.max_body:.0%}" if a.max_body > 0 else "")
              + (f", >= {a.min_agents} agents" if a.min_agents else "")
              # Said out loud because it is doing most of the work. At three
              # agents this floor is the whole difference between 74 signals
              # that reached the target a quarter of the time and 24 that
              # never reached it at all, and a banner that lists the agent
              # count while staying silent about the floor describes a book
              # that is not the one running.
              + (f", only coins whose candles are >= {a.min_atr:.1f}% of "
                 f"price (ATR)" if a.min_atr > 0 else "")
              + (", Tesla must be wired" if a.require_wired else "")
              + ("" if a.stack else ", one position per coin"))
        if a.priority:
            print(f"priority: {', '.join(a.priority)} -- these take capacity from "
                  f"the others when the book is full")
        if a.break_even > 0:
            print(f"stop:   moves to entry once a trade runs +{a.break_even}%")
        print(f"size:   {a.frac*100:.0f}% at {a.lev:.0f}x "
              f"(drops to {a.step_frac*100:.0f}% once equity reaches "
              f"${a.step_at:,.0f}), "
              + (f"+{a.tp_atr:g}xATR/-{a.sl_atr:g}xATR (sized per coin), "
                 if (a.tp_atr > 0 or a.sl_atr > 0)
                 else f"+{a.tp}%/-{a.sl}%, ")
              + f"{a.fee_bps} bps round trip")
        if a.tp_margin > 0:
            print(f"target: {a.tp_margin*100:.0f}% of the margin on EVERY "
                  f"trade -- the price distance follows the leverage this "
                  f"trade gets ({a.tp_margin*100:.0f}/lev % of price), so "
                  f"the payment never moves")
        if a.sl_atr > 0:
            print(f"stop:   {a.sl_atr:g} x the coin's own ATR -- every trade "
                  f"stops at its own coin's scale, and the leverage is held "
                  f"inside the liquidation line for THAT stop")
        if a.trail_atr > 0:
            print(f"trail:  after +1x ATR the stop follows "
                  f"{a.trail_atr:g} x ATR behind the best -- never below "
                  f"entry (the measured answer to the pullback)")
        print(f"        up to {a.max_exposure*100:.0f}% of equity committed at once "
              f"= {int(a.max_exposure/live_frac())} concurrent positions "
              f"at the current size")
        entry_desc = {
            "now": "market the instant the label prints -- no waiting for a "
                   "better price, the print and the fill are the same event",
            "edge": f"fills only at the signal candle's low (long) or high "
                    f"(short), {a.fill_bars} bars to get there or the signal is "
                    f"dropped -- no market fallback",
            "smart": f"market order, but hunted -- aims at the signal candle's low "
                     f"(long) / high (short) for up to {a.hunt_seconds:.0f}s, fires "
                     f"early if price runs {a.sl*a.hunt_give_up:.2f}% against us",
            "market": "market the instant the signal appears, crossing the spread",
            "extreme": "limit at the signal bar's low (long) / high (short)",
            "mid": "limit halfway between close and that extreme",
            "back": (f"limit {a.entry_back:g}% behind the signal price, "
                     f"{a.fill_bars} bars to fill"
                     + (", then market so the signal is not lost"
                        if a.fallback_market else " or the signal is dropped")),
        }.get(a.entry, a.entry)
        print(f"entry:  {entry_desc}\n")

    # Said once, not once a poll.
    _halted: list = []
    _frozen: set = set()
    # Why trades are not happening. A book that takes nothing looks exactly
    # like a book that is being careful, and the difference was invisible for
    # weeks: the score could not reach its own floor and no counter anywhere
    # said how many candidates died, or of what.
    tally = {"scanned": 0, "low_leverage": 0, "weak_candle": 0,
             "ran_away": 0, "below_threshold": 0, "passed": 0}
    last_tally = time.time()
    consecutive_errors = 0
    while True:
      # The nightly rebuild writes a fresher model; the book picks it up
      # without a restart. A reload failure keeps the model in hand.
      if _filter is not None and time.time() - _filter_checked > 60:
          _filter_checked = time.time()
          try:
              stamp = os.stat(a.filter_model).st_mtime_ns
              if stamp > _filter_stamp:
                  import filter_model as FM
                  _filter = FM.FilterModel(a.filter_model)
                  _filter_stamp = stamp
                  log.info("filter: model reloaded (%d training rows, "
                           "threshold %.3f)", _filter.n, _filter.threshold)
          except Exception:
              pass
      # One bad poll should cost one poll, not the night. A crash inside
      # the loop used to kill the process and stop the book until someone
      # noticed; now it is logged and the next pass runs. Five in a row
      # means something structural, so the chart sockets are rebuilt.
      try:
          charts = []
          # Windows the scout is walking are not ours to read: it moves
          # them every few seconds, so every poll would see a different coin
          # and absorb a backlog it never intended to trade. What the scout
          # finds arrives through its own list instead.
          _idx = [i for i in range(TradingViewCDP.chart_windows())
                  if i not in a.skip_window]
          if len(_idx) > 1:
              with ThreadPoolExecutor(max_workers=len(_idx)) as _ex:
                  _futs = {_ex.submit(read_window, _i): _i for _i in _idx}
                  _got = {}
                  for _f in _futs:
                      _i = _futs[_f]
                      try:
                          _got[_i] = _f.result()
                      except Exception as e:
                          log.warning("window %d unreadable: %s", _i, str(e)[:60])
                          drop_conn(_i)
              charts = [_got[_i] for _i in _idx if _got.get(_i)]
          else:
              for i in _idx:
                  try:
                      got = read_window(i)
                      if got:
                          charts.append(got)
                  except Exception as e:
                      log.warning("window %d unreadable: %s", i, str(e)[:60])
                      drop_conn(i)

          # Signals are read every poll; prices only need to be fresh enough to
          # What the account actually holds. A live row stays open until the
          # exchange stops reporting a position for it; then the realised PnL
          # comes from the exchange too, fees and all, rather than being
          # recomputed here from a price we guessed at.
          if a.live and time.time() - last_recon > a.recon_secs:
              last_recon = time.time()
              try:
                  # Keyed by symbol AND side: the account is in hedge mode, so
                  # a long and a short on one coin are two separate positions.
                  # Keying on the symbol alone made them one, and either row
                  # could then be declared closed while the other was still open.
                  held = {}
                  for pos in cli.positions():
                      _q = float(pos.get("qty") or 0)
                      if not _q:
                          continue
                      _side = str(pos.get("side") or "").upper()
                      _side = "BUY" if _side.startswith(("B", "L")) else "SELL"
                      held[(pos.get("symbol"), _side)] = pos
                  for tr in trades:
                      if tr.closed or not tr.live:
                          continue
                      _k = (tr.sym, tr.side)
                      if _k in held:
                          # Learn the position id while we can still see it;
                          # once it closes, this is the only field the history
                          # shares with anything we hold.
                          if not tr.position_id:
                              tr.position_id = str(
                                  held[_k].get("positionId") or "")
                          continue
                      # A position the exchange has not reported YET is not a
                      # position that has closed. The reconciler runs every
                      # three seconds and an order placed a moment ago may not
                      # be in get_pending_positions on the very next pass -- so
                      # a trade was being settled at zero the instant it
                      # opened, while the account went on holding a real 50x
                      # position with an attached stop that nothing in this
                      # process was watching any more. `position_id` is set the
                      # first time a row IS seen open, so its absence on a
                      # young row means "not seen yet", not "gone".
                      if not tr.position_id and \
                              time.time() - tr.opened < OPEN_GRACE_S:
                          log.debug("%s %s not reported open yet (%.0fs) -- "
                                    "waiting rather than settling it",
                                    tr.side, tr.sym, time.time() - tr.opened)
                          continue
                      # Two open rows on the same coin and side are one
                      # position to the exchange: it reports a single close with
                      # a single realised PnL. Handing that whole figure to each
                      # row would count the same money twice, so it is shared
                      # out by how much of the position each row actually was.
                      # Only the oldest runs the settlement; it closes its twins
                      # with it, and the rest are skipped.
                      _twins = sorted(
                          (t for t in trades
                           if not t.closed and t.live and (t.sym, t.side) == _k),
                          key=lambda t: t.opened)
                      if len(_twins) > 1 and tr is not _twins[0]:
                          continue
                      _share = sum(t.notional for t in _twins) or 1.0
                      realised, why = None, "closed"
                      try:
                          for h in cli.history_positions(tr.sym):
                              hid = str(h.get("positionId") or "")
                              # Match on the position id when we caught it. If
                              # the position opened and closed between two
                              # reconciliation passes we never saw it open, so
                              # fall back to the newest closed position on this
                              # symbol that started after the trade did.
                              same = (hid and hid == tr.position_id)
                              if not same and not tr.position_id:
                                  try:
                                      same = (int(h.get("ctime") or 0) / 1000
                                              >= tr.opened - 120)
                                  except (TypeError, ValueError):
                                      same = False
                              if same:
                                  realised = float(h.get("realizedPNL")
                                                   or h.get("realisedPNL") or 0)
                                  why = ("target" if realised > 0 else "stop")
                                  tr.exit = float(h.get("closePrice") or 0) or None
                                  break
                      except Exception as e:
                          log.warning("history lookup failed for %s: %s",
                                      tr.sym, str(e)[:100])
                      before = state["equity"]
                      try:
                          state["equity"] = cli.equity()
                      except Exception:
                          pass
                      _total = (realised if realised is not None
                                else state["equity"] - before)
                      _now = time.time()
                      for _t in _twins:
                          if _t is tr:
                              continue
                          _t.pnl = _total * (_t.notional / _share)
                          _t.reason = why
                          _t.closed = _now
                          if not _t.exit:
                              _t.exit = (marks.get(_t.sym) or prices.get(_t.sym)
                                         or 0.0)
                          log.info("LIVE CLOSED %s %s  %s  pnl $%+.2f  (share of "
                                   "one merged position)", _t.side, _t.sym, why,
                                   _t.pnl)
                      tr.pnl = _total * (tr.notional / _share)
                      tr.reason = why
                      tr.closed = _now
                      # The history gives the price the position actually closed
                      # at. Only fall back to the ticker when it did not -- the
                      # old line overwrote the real figure every time, so the
                      # book recorded whatever the market happened to be doing
                      # at the next reconciliation pass.
                      if not tr.exit:
                          tr.exit = (marks.get(tr.sym) or prices.get(tr.sym)
                                     or 0.0)
                      log.info("LIVE CLOSED %s %s  %s  pnl $%+.2f  equity $%.2f",
                               tr.side, tr.sym, why, tr.pnl, state["equity"])
                      side_tag = "LONG" if tr.side == "BUY" else "SHORT"
                      ping(f"{tr.sym} {why.upper()}",
                           f"{tr.pnl:+,.2f}\n${state['equity']:,.2f}")
              except Exception as e:
                  log.warning("reconcile failed: %s", str(e)[:140])

          # Open positions are marked from the exchange ticker, not the chart, so
          # navigating a chart away from a coin does not strand a position on it.
          # mark open positions, and hammering the REST endpoint every second
          # invites a rate limit that would cost far more than the staleness.
          # The fill is priced off this ticker, so a 2.5s-old quote is a 2.5s-old
          # entry.  Across the bar boundary that is the whole game, so refresh
          # hard there and leave the slower cadence for the rest of the bar,
          # where it only marks open positions and a rate limit would cost more.
          _bper = min((int(c[1]) * 60 if str(c[1]).isdigit() else 300)
                      for c in charts) if charts else 300
          _bin = time.time() % _bper
          _px_gap = 0.3 if (_bin < 5 or _bin > _bper - 3) else 2.5
          if time.time() - last_px > _px_gap:
              try:
                  _tk = cli.tickers()
                  prices = {t["symbol"]: float(t["lastPrice"]) for t in _tk}
                  # Entries fill at the traded price; the attached TP and SL
                  # trigger off the mark. Keep both so each is judged the way
                  # the exchange will judge it.
                  marks = {t["symbol"]: float(t["markPrice"]) for t in _tk
                           if t.get("markPrice")}
                  last_px = time.time()
              except Exception as e:
                  log.warning("ticker fetch failed: %s", str(e)[:60])

          # Real high and low of the bar in progress, sampled from the ticker.
          # The Heikin Ashi chart cannot be trusted for this.
          for _tv, _res, _sg, _oh in charts:
              _sym = _tv.split(":")[-1].replace(".P", "")
              _px = prices.get(_sym)
              if not _px:
                  continue
              _per = int(_res) * 60 if str(_res).isdigit() else 300
              _bar = int(time.time() // _per * _per)
              _k = (_sym, _bar)
              if _k in real_hl:
                  _hl = real_hl[_k]
                  _hl[0] = min(_hl[0], _px)
                  _hl[1] = max(_hl[1], _px)
              else:
                  real_hl[_k] = [_px, _px]
          if len(real_hl) > 400:
              for _k in sorted(real_hl)[:200]:
                  real_hl.pop(_k, None)

          # Open positions are marked from the exchange ticker, not the chart, so
          # navigating a chart away from a coin does not strand a position on it.
          # Read once per pass rather than per position: the file is the
          # same for all of them.
          _cts = ct_prints() if a.ct_exit > 0 else {}
          # mark open positions
          for tr in trades:
              if tr.closed or tr.live:
                  continue
              px = marks.get(tr.sym) or prices.get(tr.sym)
              if not px:
                  continue
              # Funding, from the real venue rate: accrued continuously so
              # a hold pays what the exchange would charge. An unreadable
              # rate accrues nothing -- and says so once, not per poll.
              if not a.live:
                  try:
                      import paper_venue
                      _fr = paper_venue.funding_rate(cli, tr.sym)
                      if _fr is not None:
                          _now = time.time()
                          _dt = _now - (tr.funded_at or tr.opened)
                          tr.funded_at = _now
                          _sign = -1.0 if tr.side == "BUY" else 1.0
                          tr.funding += (tr.notional * _fr * _sign
                                         * _dt / 3600.0 / 8.0)
                  except Exception:
                      pass
              # Break-even, if asked for. The stop only ever moves toward the
              # entry, never away, and only once -- a stop that can retreat is
              # not a stop.
              if a.break_even > 0 and not tr.moved_to_be:
                  fav = (px / tr.entry - 1) * 100 * (1 if tr.side == "BUY" else -1)
                  tr.best_pct = max(tr.best_pct, fav)
                  if tr.best_pct >= a.break_even:
                      tr.sl = tr.entry
                      tr.moved_to_be = True
                      log.info("BE    %s %s  ran +%.2f%%, stop moved to entry "
                               "%.8g", tr.side, tr.sym, tr.best_pct, tr.entry)
              # The trailing stop, if asked for. Once the trade has run a
              # full ATR in our favour, the stop follows MULT x ATR behind
              # the best price, and never moves away from safety: only
              # tighter, and never past entry. Measured first: over the
              # 10,702 recorded paths this is the only protection whose
              # expectancy beats the bare target/stop -- it turns the
              # "almost there, then the pullback ate it" trades into
              # scratches instead of full stops.
              if a.trail_atr > 0 and not tr.closed:
                  _fav = (px / tr.entry - 1) * 100 * (1 if tr.side == "BUY"
                                                      else -1)
                  tr.best_pct = max(tr.best_pct, _fav)
                  _atr = coin_atr(tr.sym)
                  if _atr is not None and tr.best_pct >= _atr:
                      _best_px = (tr.entry * (1 + tr.best_pct / 100)
                                  if tr.side == "BUY"
                                  else tr.entry * (1 - tr.best_pct / 100))
                      _trail = (_best_px * (1 - a.trail_atr * _atr / 100)
                                if tr.side == "BUY"
                                else _best_px * (1 + a.trail_atr * _atr / 100))
                      if tr.side == "BUY":
                          _want = max(tr.sl, _trail, tr.entry)
                      else:
                          _want = min(tr.sl, _trail, tr.entry)
                      if not tr.moved_to_be:
                          tr.moved_to_be = True
                          log.info("TRAIL %s %s  ran +%.2f%%, stop now "
                                   "follows %.2fx the coin's ATR behind the "
                                   "best", tr.side, tr.sym, tr.best_pct,
                                   a.trail_atr)
                      if (tr.side == "BUY" and _want > tr.sl) or \
                         (tr.side == "SELL" and _want < tr.sl):
                          tr.sl = _want
              # The indicator taking the move back, on a trade already in
              # front. Ten percent is the target and a trade six or seven
              # percent up is most of the way there -- riding a counter-trend
              # print back down to the stop turns that into a loss. This is
              # the only exit here that is neither the target nor the stop,
              # and it never fires on a losing trade.
              if a.ct_exit > 0 and not tr.closed:
                  _fav = (px / tr.entry - 1) * 100 * (1 if tr.side == "BUY"
                                                      else -1)
                  tr.best_pct = max(tr.best_pct, _fav)
                  _against = -1 if tr.side == "BUY" else 1
                  if _fav >= a.ct_exit and _cts.get(tr.sym) == _against:
                      tr.exit = px
                      tr.reason = "counter"
                      move = (px / tr.entry - 1) * (1 if tr.side == "BUY"
                                                    else -1)
                      tr.pnl = tr.notional * move - tr.notional * a.fee_bps / 1e4
                      tr.closed = time.time()
                      tr.pnl += tr.funding
                      state["equity"] += tr.pnl
                      log.info("COUNTER %s %s  +%.2f%% and the indicator "
                               "printed against it -- taken, pnl $%+.2f, "
                               "equity $%.2f", tr.side, tr.sym, _fav, tr.pnl,
                               state["equity"])
                      if a.push_each_close:
                          ping(f"{tr.sym} COUNTER",
                               f"{tr.pnl:+,.2f}\n${state['equity']:,.2f}")
                      continue
              hit_tp = px >= tr.tp if tr.side == "BUY" else px <= tr.tp
              hit_sl = px <= tr.sl if tr.side == "BUY" else px >= tr.sl
              # A stop only exists if the position survives long enough to
              # reach it. Leverage decides that: the exchange closes the
              # position once the loss eats the margin, which at fifty times
              # is about 1.5% of price -- nearer than a 2.1% stop. Modelling
              # the stop and ignoring that was writing a record of something
              # that cannot happen: the loss would be capped at the margin,
              # the fill would be worse, and the exit would be the exchange's
              # choice rather than ours.
              _lev = tr.notional / tr.margin if tr.margin else 0.0
              _liq_at = ((1.0 / _lev) - a.maint_margin) * 100 if _lev else 1e9
              _adverse = (1 - px / tr.entry) * 100 if tr.side == "BUY" \
                  else (px / tr.entry - 1) * 100
              # Which line price reached FIRST. Between two polls only the
              # start and the end are known, but price cannot arrive at 2%
              # against us without having passed 0.82% on the way -- so when
              # the stop is the nearer of the two, the stop is what closed the
              # position, and it closed it before the liquidation line was
              # ever in question. Taking the liquidation branch on any fast
              # move charged the whole margin for a trade the exchange would
              # have stopped out for a third of it: on the shape geometry that
              # is $50 recorded where $20.50 was lost, on every gap, in the
              # only record this system keeps of what it does.
              _sl_dist = (abs(tr.entry - tr.sl) / tr.entry * 100
                          if tr.entry else 1e9)
              _liq_first = _liq_at < _sl_dist or not hit_sl
              if (a.model_liquidation and _lev > 0 and _adverse >= _liq_at
                      and _liq_first):
                  tr.exit = tr.entry * (1 - _liq_at / 100) if tr.side == "BUY" \
                      else tr.entry * (1 + _liq_at / 100)
                  tr.reason = "liquidated"
                  # The whole margin, plus the round-trip fee -- the same
                  # fee every other close path charges. The entry leg was
                  # paid to get in, whatever closes us.
                  tr.pnl = -tr.margin - tr.notional * a.fee_bps / 1e4
                  tr.closed = time.time()
                  tr.pnl += tr.funding
                  state["equity"] += tr.pnl
                  log.error("LIQ   %s %s at %.8g -- %.2f%% against us reached "
                            "the liquidation line before the %.2f%% stop.  "
                            "lost the whole $%.2f margin  equity $%.2f",
                            tr.side, tr.sym, tr.exit, _adverse, 
                            abs(tr.sl / tr.entry - 1) * 100, tr.margin,
                            state["equity"])
                  if a.push_each_close:
                      ping(f"{tr.sym} LIQUIDATED",
                           f"{tr.pnl:+,.2f}\n${state['equity']:,.2f}")
                  continue
              # Time, as well as price. A position that reaches neither end is
              # not patient, it is stuck: it holds margin that cannot be used
              # elsewhere and it keeps paying for the chance of a random stop
              # while the target was never close. Two clocks end it.
              #
              #   stale   the move it was entered on never developed. Measured
              #           as favourable travel against the stop distance, so it
              #           reads the same on a quiet coin and a violent one.
              #   held    the outright limit, whatever happened.
              #
              # Both close at the price on the screen, and both are labelled as
              # themselves -- calling either one a stop would put a loss in the
              # record that the chart never shows.
              age_min = (time.time() - tr.opened) / 60.0
              risk = abs(tr.entry - tr.sl) / tr.entry * 100 or 1e-9
              fav = (px / tr.entry - 1) * 100 * (1 if tr.side == "BUY" else -1)
              tr.best_pct = max(tr.best_pct, fav)
              stale = (a.stale_min > 0 and age_min >= a.stale_min
                       and tr.best_pct < risk * a.stale_r)
              expired = a.max_hold_min > 0 and age_min >= a.max_hold_min
              if not (hit_tp or hit_sl or stale or expired):
                  continue
              if not (hit_tp or hit_sl):
                  tr.exit = px
                  tr.reason = "stalled" if stale else "timeout"
                  move = (tr.exit / tr.entry - 1) * (1 if tr.side == "BUY" else -1)
                  tr.pnl = tr.notional * move - tr.notional * a.fee_bps / 1e4
                  tr.closed = time.time()
                  tr.pnl += tr.funding
                  state["equity"] += tr.pnl
                  log.info("%-6s %s %s  %+.2f%%  after %.0fm, best it managed "
                           "was %+.2f%% against a %.2f%% stop  pnl $%+.2f  "
                           "equity $%.2f", tr.reason.upper(), tr.side, tr.sym,
                           move * 100, age_min, tr.best_pct, risk, tr.pnl,
                           state["equity"])
                  if a.push_each_close:
                      side_tag = "LONG" if tr.side == "BUY" else "SHORT"
                      ping(f"{tr.sym} {tr.reason.upper()}",
                           f"{tr.pnl:+,.2f}\n${state['equity']:,.2f}")
                  continue
              tr.exit = tr.tp if hit_tp else tr.sl
              # A break-even exit is not a stop. The trade ran far enough for
              # the stop to be pulled up to the entry, then came back and was
              # closed for the fee. Calling that "STOP" on the phone reads as a
              # loss the chart never shows -- and the chart often goes on to the
              # target afterwards, which is what makes the label look like a lie.
              be_exit = tr.moved_to_be and tr.sl == tr.entry
              tr.reason = "target" if hit_tp else ("flat" if be_exit else "stop")
              move = (tr.exit / tr.entry - 1) * (1 if tr.side == "BUY" else -1)
              tr.pnl = tr.notional * move - tr.notional * a.fee_bps / 1e4
              tr.closed = time.time()
              tr.pnl += tr.funding
              state["equity"] += tr.pnl
              log.info("%-6s %s %s  %+.2f%%  pnl $%+.2f  equity $%.2f",
                       tr.reason.upper(), tr.side, tr.sym, move * 100, tr.pnl,
                       state["equity"])
              if a.push_each_close:
                  # The only notification that goes out: a closed trade, tagged
                  # by direction, with the wallet. Entries are in the log only --
                  # by the time a phone buzzes the position already exists, so
                  # the message cannot be acted on.
                  side_tag = "LONG" if tr.side == "BUY" else "SHORT"
                  ping(f"{tr.sym} {tr.reason.upper()}",
                       f"{tr.pnl:+,.2f}\n${state['equity']:,.2f}")
              done_n = len([t for t in trades if t.closed])
              wins = len([t for t in trades if t.closed and t.pnl > 0])
              log.info("       record: %d/%d won (%.0f%%), equity $%.2f "
                       "from $%.2f", wins, done_n, 100 * wins / max(done_n, 1),
                       state["equity"], state["start"])
              if state["equity"] <= 0:
                  log.error("ACCOUNT WIPED OUT after %d trades", len(trades))

          # new signals
          # Resting limits: fill when price reaches the level, drop them when the
          # window passes. A limit at the signal bar's extreme is a better entry
          # only when price comes back; when it does not, there is no trade at
          # all, and that miss rate is the real cost of asking for the better fill.
          # A live order is the exchange's to fill, not ours to imagine. It
          # is watched here and nowhere else; the local price-touch logic
          # below would otherwise book a second, invented position on top of
          # the real one.
          if a.live:
              try:
                  watch_live_orders()
                  # A halt has to reach the exchange too. An order left resting
                  # can still fill an hour later, into a book that has already
                  # decided it is done for the day.
                  if loss_limit_hit():
                      for _p in list(resting):
                          if _p.live and not _p.settled:
                              resting.remove(_p)
                              cancel_resting(_p, "the day's loss limit")
              except Exception as e:
                  log.warning("watching live orders failed: %s", str(e)[:140])
          for pnd in list(resting):
              if pnd.live:
                  continue
              px = prices.get(pnd.sym)
              if px is None:
                  continue
              # The whole strategy is the entry: long at the signal candle's own
              # low, short at its high, with the stop a fixed distance from THAT
              # fill. Over the combos on these two charts that is 72.3% of decided
              # races against 17.0% for the same stop taken at the signal close.
              # There is deliberately no market fallback here -- filling anywhere
              # else is a different and far worse strategy wearing the same signal.
              if a.entry == "edge":
                  reached = px <= pnd.want if pnd.side == "BUY" else px >= pnd.want
                  if not reached:
                      if time.time() > pnd.expires:
                          resting.remove(pnd)
                          fills["expired"] += 1
                          log.info("DROP  %s %s -- never returned to %.8g "
                                   "(%d filled / %d dropped)", pnd.side, pnd.sym,
                                   pnd.want, fills["filled"], fills["expired"])
                      continue
                  # The reunion check: a scout-borne combo print is an
                  # EVENT, not a price. The order rests up to eight bars,
                  # and in that time the three agents can stop agreeing --
                  # the scout rewrites its file every sweep, so the row
                  # with this signal bar is still there only while the
                  # print still stands. Filling a trade the agents have
                  # left is the one kind of trade this book must not take.
                  if (pnd.sig.get("scout") and pnd.sig.get("kind") == "combo"
                          and not reunion_ok(pnd)):
                      resting.remove(pnd)
                      fills["expired"] += 1
                      log.info("DROP  %s %s -- the print cleared while the "
                               "order rested; the reunion is over "
                               "(%d filled / %d dropped)", pnd.side, pnd.sym,
                               fills["filled"], fills["expired"])
                      continue
                  resting.remove(pnd)
                  fills["filled"] += 1
                  open_now = [t for t in trades if not t.closed]
                  if a.one_per_symbol and not a.stack and any(t.sym == pnd.sym for t in open_now):
                      log.info("fill skipped %s %s -- already in %s",
                               pnd.side, pnd.sym, pnd.sym)
                      continue
                  if day_full(trades) or loss_limit_hit():
                      log.info("fill skipped %s %s -- %s", pnd.side, pnd.sym,
                               "the day's loss limit is reached"
                               if loss_limit_hit() else
                               "the day's count is spent")
                      resting[:] = [x for x in resting if x is not pnd]
                      continue
                  used = sum(t.margin for t in open_now)
                  margin = pnd.sig.get("margin", state["equity"] * live_frac())
                  if used + margin > state["equity"] * a.max_exposure + 1e-9:
                      log.info("MISSED %s %s -- exposure full", pnd.side, pnd.sym)
                      continue
                  # A maker resting at its own limit fills AT the limit,
                  # never worse. Adding the half-spread here charged every
                  # edge entry a crossing cost nobody pays, which is the
                  # same lie as granting a better price -- the simulator
                  # must not invent money in either direction.
                  e = pnd.want
                  sg = pnd.sig
                  notional = margin * (pnd.sig.get("lev")
                                       or sym_lev(pnd.sym))
                  # The plan measured its own leg; recomputing here would
                  # use a later candle and quietly move the target after the
                  # decision was made.
                  _tp = pnd.sig.get("tp") or geo(pnd.sym)[0]
                  _sl = pnd.sig.get("sl") or geo(pnd.sym)[1]
                  tr = Trade(sym=pnd.sym, side=pnd.side, qty=notional / e, entry=e,
                             tp=e * (1 + _tp/100) if pnd.side == "BUY" else e * (1 - _tp/100),
                             sl=e * (1 - _sl/100) if pnd.side == "BUY" else e * (1 + _sl/100),
                             opened=time.time(), bar=sg.get("t", 0), who=sg.get("who", 0),
                             tier=sg.get("tier", 0), score=sg.get("score", 0),
                             agents=sg.get("agents", 0), margin=margin, notional=notional,
                             entry_kind="edge", entry_err_r=0.0,
                             counter=bool(sg.get("counter")))
                  trades.append(tr)
                  log.info("FILL  %s %s at the candle edge %.8g (signal was %.8g, "
                           "%.3f%% better)  tp %.8g  sl %.8g", tr.side, tr.sym, e,
                           pnd.signal_px, abs(e / pnd.signal_px - 1) * 100, tr.tp, tr.sl)
                  ping(f"{tr.sym} {'LONG' if tr.side == 'BUY' else 'SHORT'}",
                       f"entry  {tr.entry:.8g}\n"
                       f"target {tr.tp:.8g}\n"
                       f"stop   {tr.sl:.8g}\n"
                       f"${state['equity']:,.2f}")
                  continue


              if a.entry == "smart":
                  better = px < pnd.best_seen if pnd.side == "BUY" else px > pnd.best_seen
                  if better:
                      pnd.best_seen = px
                  reached = px <= pnd.want if pnd.side == "BUY" else px >= pnd.want
                  ran_off = px >= pnd.give_up if pnd.side == "BUY" else px <= pnd.give_up
                  late = time.time() >= pnd.deadline
                  if not (reached or ran_off or late):
                      continue
                  why = ("hit the level" if reached else
                         "price ran away" if ran_off else "out of time")
                  resting.remove(pnd)
                  fills["filled" if reached else "expired"] += 1
                  open_now = [t for t in trades if not t.closed]
                  if a.one_per_symbol and not a.stack and any(t.sym == pnd.sym for t in open_now):
                      log.info("fill skipped %s %s -- already in %s",
                               pnd.side, pnd.sym, pnd.sym)
                      continue
                  if day_full(trades) or loss_limit_hit():
                      log.info("fill skipped %s %s -- %s", pnd.side, pnd.sym,
                               "the day's loss limit is reached"
                               if loss_limit_hit() else
                               "the day's count is spent")
                      resting[:] = [x for x in resting if x is not pnd]
                      continue
                  used = sum(t.margin for t in open_now)
                  margin = pnd.sig.get("margin", state["equity"] * live_frac())
                  if used + margin > state["equity"] * a.max_exposure + 1e-9:
                      log.info("MISSED %s %s -- exposure full", pnd.side, pnd.sym)
                      continue
                  e = cross(pnd.sym, px, pnd.side)
                  sg = pnd.sig
                  notional = margin * (pnd.sig.get("lev")
                                       or sym_lev(pnd.sym))
                  # The plan measured its own leg; recomputing here would
                  # use a later candle and quietly move the target after the
                  # decision was made.
                  _tp = pnd.sig.get("tp") or geo(pnd.sym)[0]
                  _sl = pnd.sig.get("sl") or geo(pnd.sym)[1]
                  err_r = abs(e / pnd.want - 1) * 100 / _sl if _sl else 0.0
                  tr = Trade(sym=pnd.sym, side=pnd.side, qty=notional / e, entry=e,
                             tp=e * (1 + _tp/100) if pnd.side == "BUY" else e * (1 - _tp/100),
                             sl=e * (1 - _sl/100) if pnd.side == "BUY" else e * (1 + _sl/100),
                             opened=time.time(), bar=sg.get("t",0), who=sg.get("who",0),
                             tier=sg.get("tier",0), score=sg.get("score",0),
                             agents=sg.get("agents",0), margin=margin,
                             notional=notional, entry_kind="smart",
                             entry_err_r=err_r,
                             counter=bool(sg.get("counter")))
                  trades.append(tr)
                  announce(tr)
                  log.info("OPEN  %s %s | %s @ %.8g  (%s, waited %.1fs, best seen "
                           "%.8g)  err %.2fR", tr.side, tr.sym, pnd.sig.get("tier",""),
                           e, why, time.time()-pnd.placed, pnd.best_seen, err_r)
                  continue
              touched = px <= pnd.want if pnd.side == "BUY" else px >= pnd.want
              if touched:
                  resting.remove(pnd)
                  fills["filled"] += 1
                  open_now = [t for t in trades if not t.closed]
                  if a.one_per_symbol and not a.stack and any(t.sym == pnd.sym for t in open_now):
                      log.info("fill skipped %s %s -- already in %s",
                               pnd.side, pnd.sym, pnd.sym)
                      continue
                  if day_full(trades) or loss_limit_hit():
                      log.info("fill skipped %s %s -- %s", pnd.side, pnd.sym,
                               "the day's loss limit is reached"
                               if loss_limit_hit() else
                               "the day's count is spent")
                      resting[:] = [x for x in resting if x is not pnd]
                      continue
                  used = sum(t.margin for t in open_now)
                  # The size travels with the order. These two fill sites recomputed it
                  # from the flat fraction and threw away what the plan had worked out
                  # from the coin's own stop -- so a sample trade that should have risked
                  # five dollars risked one and a half, and every council fill would have
                  # been under-sized without ever once looking wrong.
                  margin = pnd.sig.get("margin",
                                       state["equity"] * live_frac())
                  if used + margin > state["equity"] * a.max_exposure + 1e-9:
                      log.info("fill missed %s %s -- exposure full", pnd.side, pnd.sym)
                      continue
                  notional = margin * (pnd.sig.get("lev")
                                       or sym_lev(pnd.sym))
                  e = pnd.want
                  sg = pnd.sig
                  # The plan measured its own leg; recomputing here would
                  # use a later candle and quietly move the target after the
                  # decision was made.
                  _tp = pnd.sig.get("tp") or geo(pnd.sym)[0]
                  _sl = pnd.sig.get("sl") or geo(pnd.sym)[1]
                  tr = Trade(sym=pnd.sym, side=pnd.side, qty=notional / e, entry=e,
                             tp=e * (1 + _tp/100) if pnd.side == "BUY" else e * (1 - _tp/100),
                             sl=e * (1 - _sl/100) if pnd.side == "BUY" else e * (1 + _sl/100),
                             opened=time.time(), bar=sg.get("t", 0), who=sg.get("who", 0),
                             tier=sg.get("tier", 0), score=sg.get("score", 0),
                             agents=sg.get("agents", 0), margin=margin,
                             notional=notional, entry_err_r=0.0,
                             counter=bool(sg.get("counter")))
                  trades.append(tr)
                  announce(tr)
                  log.info("FILL  %s %s at %.8g (%.2f%% better than close %.8g)",
                           pnd.side, pnd.sym, e,
                           abs(e / sg.get("close", e) - 1) * 100, sg.get("close", e))
              elif time.time() > pnd.expires:
                  resting.remove(pnd)
                  fills["expired"] += 1
                  if not a.fallback_market:
                      log.info("EXPIRED %s %s -- price never returned to %.8g "
                               "(%d filled / %d expired)", pnd.side, pnd.sym,
                               pnd.want, fills["filled"], fills["expired"])
                      continue
                  # The better entry did not come. Taking it late at market keeps
                  # the signal in the record instead of quietly dropping it, and
                  # the log marks it so the two entry styles can be told apart
                  # when the numbers are counted.
                  open_now = [t for t in trades if not t.closed]
                  if a.one_per_symbol and not a.stack and any(t.sym == pnd.sym for t in open_now):
                      log.info("fill skipped %s %s -- already in %s",
                               pnd.side, pnd.sym, pnd.sym)
                      continue
                  if day_full(trades) or loss_limit_hit():
                      log.info("fill skipped %s %s -- %s", pnd.side, pnd.sym,
                               "the day's loss limit is reached"
                               if loss_limit_hit() else
                               "the day's count is spent")
                      resting[:] = [x for x in resting if x is not pnd]
                      continue
                  used = sum(t.margin for t in open_now)
                  # The size travels with the order. These two fill sites recomputed it
                  # from the flat fraction and threw away what the plan had worked out
                  # from the coin's own stop -- so a sample trade that should have risked
                  # five dollars risked one and a half, and every council fill would have
                  # been under-sized without ever once looking wrong.
                  margin = pnd.sig.get("margin",
                                       state["equity"] * live_frac())
                  if used + margin > state["equity"] * a.max_exposure + 1e-9:
                      log.info("LATE-SKIP %s %s -- exposure full", pnd.side, pnd.sym)
                      continue
                  # The better entry never came, so this is a taker now --
                  # and a taker crosses the book. Filling at the last print
                  # quietly granted a better price than Bitunix would give.
                  e = cross(pnd.sym, px, pnd.side)
                  sg = pnd.sig
                  notional = margin * (pnd.sig.get("lev")
                                       or sym_lev(pnd.sym))
                  # The plan measured its own leg; recomputing here would
                  # use a later candle and quietly move the target after the
                  # decision was made.
                  _tp = pnd.sig.get("tp") or geo(pnd.sym)[0]
                  _sl = pnd.sig.get("sl") or geo(pnd.sym)[1]
                  tr = Trade(sym=pnd.sym, side=pnd.side, qty=notional / e, entry=e,
                             tp=e * (1 + _tp/100) if pnd.side == "BUY" else e * (1 - _tp/100),
                             sl=e * (1 - _sl/100) if pnd.side == "BUY" else e * (1 + _sl/100),
                             opened=time.time(), bar=sg.get("t", 0), who=sg.get("who", 0),
                             tier=sg.get("tier", 0), score=sg.get("score", 0),
                             agents=sg.get("agents", 0), margin=margin,
                             notional=notional, entry_kind="late-market",
                             counter=bool(sg.get("counter")))
                  trades.append(tr)
                  announce(tr)
                  pass  # entry is logged, not notified
                  log.info("LATE  %s %s at market %.8g -- limit %.8g never hit "
                           "(%d on-limit / %d late)", pnd.side, pnd.sym, e,
                           pnd.want, fills["filled"], fills["expired"])

          # Whatever the scout turned up on coins no window is holding.
          # Presented as one more chart so it goes through exactly the same
          # gate as a plan found on a window we are watching -- the coin's own
          # measurements travel with it, because by now the scout has walked on.
          _held = {tv.split(":")[-1].replace(".P", "")
                   for tv, _r, _s, _o in charts}
          sc = scout_plans(seen, _held) if a.scout else []
          if sc:
              # The scout's plan carries the signal bar's own candle (the
              # scout read it off the chart when the print landed). Turning
              # it into the plan's ohlc is what lets the edge entry rest at
              # the candle's extreme on a coin no window is watching --
              # without it every scout plan died at "no candle".
              charts = list(charts)
              for x in sc:
                  bars = {}
                  b = x.get("bar") or {}
                  if all(isinstance(b.get(k), (int, float))
                         for k in ("o", "h", "l", "c")):
                      bars[int(x["t"])] = (b["o"], b["h"], b["l"], b["c"])
                  charts.append((f"BITUNIX:{x['sym']}.P", "15", [x], bars))
              log.info("scout brought %d plan(s): %s", len(sc),
                       ", ".join(f"{x['side']} {x['sym']}" for x in sc))
          for tv_sym, res, sigs, ohlc in charts:
              sym = tv_sym.split(":")[-1].replace(".P", "")
              fresh_chart = tv_sym not in known_syms or bool(_STUDY_SWAPPED)
              if fresh_chart:
                  known_syms.add(tv_sym)
                  if not first:
                      log.info("new chart %s @ %sm -- absorbing %d existing "
                               "signals, watching from here", tv_sym, res, len(sigs))
              for s in sigs:
                  # The entry score, bound for every path through this loop.
                  # Only the combo branch computes one; the others carry the
                  # indicator's own number so a trade opened there still
                  # records something true rather than a stale value from
                  # whatever the previous signal happened to be.
                  _pts, _why = float(s.get("score") or 0), ""
                  key = (tv_sym, s["t"], s["side"],
                         bool(s.get("counter")))
                  if key in seen:
                      if s.get("scout"):
                          log.info("drop  %s %s -- already acted on this one",
                                   s["side"], sym)
                      continue
                  seen.add(key)
                  # Hard age cap, independent of the absorb logic above. `s["t"]`
                  # is the bar's opening time, so a signal acted on at that bar's
                  # close is already one full bar old -- hence the extra bar of
                  # slack. Anything past it is backlog, and filling it means
                  # paying a price the signal never saw.
                  try:
                      bar_sec = float(res) * 60.0
                  except (TypeError, ValueError):
                      bar_sec = 300.0      # anything but a minute chart: assume 5m
                  age = time.time() - s["t"]
                  # A shape is not a print. The convergence signal is an event
                  # that means something at the moment it fires and nothing an
                  # hour later, which is what --max-age was written for. A
                  # broken level is not an event -- it is a price, and it is
                  # the same price an hour later. The order rests there either
                  # way, and if the level has been reclaimed since then the
                  # shape function will no longer return it at all.
                  #
                  # Keeping one limit for both meant the scout was told to look
                  # six candles back while the book threw away anything older
                  # than one: the two halves disagreed and every shape the
                  # scout found was dropped on arrival.
                  _age_cap = (a.max_shape_age if s.get("kind") == "break"
                              else a.max_age)
                  if _age_cap > 0 and age > (_age_cap + 1) * bar_sec:
                      log.info("stale %s %s bar %.0fs old (%.1f bars) -- not "
                               "traded", s["side"], sym, age, age / bar_sec)
                      continue
                  # The startup absorb exists so a fresh chart's hour of
                  # history is not traded all at once. The scout's list is not
                  # history: it is a short-lived file of levels that are still
                  # standing, and the book's own memory of what it has acted on
                  # survives a restart. Absorbing it meant every restart threw
                  # away whatever the scout had found -- and today that was
                  # most of them.
                  if (first or fresh_chart) and not s.get("scout"):
                      age = time.time() - s["t"]
                      if not (a.catch_up > 0 and age <= a.catch_up):
                          log.info("drop  %s %s -- absorbed as backlog on a "
                                   "fresh chart", s["side"], sym)
                          continue
                      log.info("catch-up  %s %s printed %.0fs ago -- still fresh, "
                               "judging it on price", s["side"], sym, age)
                      s = {**s, "catchup": True}
                  # ---- the council ------------------------------------
                  # A council plan is not a print to be judged: the six members
                  # have already argued, and the plan carries the level they
                  # agreed to enter at. None of the combo filters below apply --
                  # colour, tier and Tesla-wired describe a convergence signal,
                  # and this is not one.
                  if s.get("council"):
                      if a.source == "combo":
                          # Said out loud, like every other refusal here.
                          #
                          # This was a bare `continue`, and the scout marks
                          # every row it writes as a council row -- so on this
                          # source the book dropped the scout's entire output
                          # without a word. Eleven coins walked, a plan on
                          # AKEUSDT delivered, and nothing anywhere between
                          # "scout brought 1 plan" and silence. A candidate
                          # that disappears without a reason is the one fault
                          # this engine cannot afford, and it is invisible to
                          # the funnel unless the drop is logged.
                          log.info("drop  %s %s -- the scout's shape, and "
                                   "this book trades the indicator's own "
                                   "signal", s["side"], sym)
                          continue
                      # "break" trades the picture and nothing else. The
                      # council, the Fibonacci retracement, the vote ceiling,
                      # the staircase -- none of them produced the trade on the
                      # phone, and on that chart the council reads "no plan"
                      # while the trade in front of it makes ten percent. So on
                      # this source they are not consulted at all: a quiet
                      # band, one candle out of it, the far edge as the stop.
                      if a.source == "break" and s.get("kind") != "break":
                          log.info("drop  %s %s -- not a shape (kind=%s)",
                                   s["side"], sym, s.get("kind"))
                          continue
                      if a.source == "council" and s.get("kind") == "break":
                          log.info("drop  %s %s -- a shape, but this book "
                                   "trades the council", s["side"], sym)
                          continue
                      voted = ", ".join(f"{k} {'+' if x > 0 else '-'}"
                                        for k, x in s["members"].items() if x)
                      ctag = (f"COUNCIL {s['side']} {sym} {res}m "
                              f"{s['votes']}/6 [{voted}]")
                      px = prices.get(sym)
                      if not px:
                          log.warning("skip  %s -- no price on Bitunix", ctag)
                          continue
                      # The stop goes behind the structure when the leg can
                      # be read. A percentage stop is a number we chose and the
                      # market has no opinion about it, which is why a shadow
                      # can reach it; the swing the impulse came from is the
                      # trade's own premise, and price has to undo the move to
                      # get there.
                      # The stop costs the margin and nothing decides it
                      # but the leverage. What decides whether we are in the
                      # trade at all is below: the move must never have come
                      # back this far, by a wide margin.
                      # Both start from the run's own fixed geometry. Every
                      # branch below overwrites them, but the branches do not
                      # cover every case: a council plan read off a chart
                      # window, on a book not using --stop-at-margin, reached
                      # the safety test with neither name bound and took the
                      # whole poll down with an UnboundLocalError -- which the
                      # loop then swallowed as "poll failed", losing the plan,
                      # the book write at the end of that pass, and any sign
                      # that something was wrong beyond one line in the log.
                      _csl, _ctp = a.sl, a.tp
                      if s.get("kind") == "break" and s.get("stop_pct"):
                          # The band decides. Nothing else may move it: this
                          # stop is the reason the picture is tradable at all.
                          _csl = float(s["stop_pct"])
                          _ctp = a.tp
                          log.info("      %s: level %.8g held %s time(s), "
                                   "stop %.2f%% just past it", sym,
                                   s.get("level") or 0,
                                   s.get("touches") or s.get("steps") or "?",
                                   _csl)
                      elif a.stop_at_margin:
                          # The stop moves; the target does not follow it. On a
                          # fixed target this is a.tp, and when the target is
                          # taken from the leg it has already been worked out
                          # above -- reading it here as well was reading a name
                          # before it had a value on the fixed path.
                          _csl = stop_of_margin()
                          if a.tp_leg <= 0:
                              _ctp = a.tp
                      _st = (s.get("stairs") if s.get("scout")
                             else staircase(ohlc, s["t"], s["side"]))
                      _safety = None
                      if _st and _st["worst"] > 0:
                          _safety = _csl / _st["worst"]
                      elif _st:
                          _safety = 99.0
                      if a.min_safety > 0 and _st:
                          if _safety is None or _safety < a.min_safety:
                              log.info("skip  %s -- this move has already come "
                                       "back %.2f%% against us and the stop is "
                                       "%.2f%%: only %.1fx clear, wanted %.1fx",
                                       ctag, _st["worst"], _csl,
                                       _safety or 0.0, a.min_safety)
                              continue
                      if a.min_stairs > 0 and (not _st
                                               or _st["steps"] < a.min_stairs):
                          log.info("skip  %s -- %s steps of staircase, wanted "
                                   "%d", ctag,
                                   _st["steps"] if _st else "no", a.min_stairs)
                          continue
                      if _st:
                          log.info("      %s: %d steps, %d unbroken, travelled "
                                   "%.2f%%, worst pullback %.2f%%, stop is "
                                   "%.1fx clear of it", sym, _st["steps"],
                                   _st["intact"], _st["travel"], _st["worst"],
                                   _safety or 0.0)
                      if s.get("scout") and s.get("leg") and not a.stop_structure:
                          _dummy, _csl = geo(sym)
                          _ctp = (s["leg"] * a.tp_leg / s["entry"] * 100
                                  if a.tp_leg > 0 and s["entry"] else _dummy)
                          _r = _ctp / _csl if _csl else 0.0
                          if _r < a.r_min:
                              _ctp = _csl * a.r_min
                          elif _r > a.r_max:
                              _ctp = _csl * a.r_max
                      elif s.get("kind") != "break":
                          _ctp, _csl = geo_leg(sym, ohlc, s["t"])
                      # A shape's stop is the level it broke, and that was settled fifty
                      # lines above. This branch used to run for it too and quietly put
                      # the fixed 2.1% back -- which sits past the liquidation line at
                      # fifty times, so the whole 'the stop comes from the level' design
                      # never reached a single trade.
                      if a.min_expansion > 0:
                          ex = (s.get("expansion") if s.get("scout")
                                else expansion(ohlc, s["t"]))
                          if ex is not None and ex < a.min_expansion:
                              log.info("skip  %s -- the signal candle is only "
                                       "%.2fx this coin's normal, nothing is "
                                       "moving", ctag, ex)
                              continue
                      # One judgement, not six cliffs. Each measure earns
                      # part of a hundred on a ramp, so a strong one can carry
                      # a weak one and nothing falls through a crack between
                      # two thresholds. The old hard gates are still here as
                      # floors -- set them to 0 and this decides alone, which
                      # is how it runs.
                      _rs = (s.get("run") if s.get("scout")
                             else run_strength(ohlc, s["t"], s["side"]))
                      _ex = (s.get("expansion") if s.get("scout")
                             else expansion(ohlc, s["t"]))
                      _bs = (s.get("body") if s.get("scout")
                             else body_share(ohlc, s["t"]))
                      _wr = (s.get("wick") if s.get("scout")
                             else wick_risk(ohlc, s["side"], _csl,
                                            t=s["t"]))
                      if s.get("kind") == "break":
                          # Read on the shape's own terms. How many times the
                          # market respected that level, how hard the candle
                          # left it, how much of the candle is body, and how
                          # far the stop sits inside what the leverage allows
                          # -- a stop with room to spare is a level that was
                          # tested cleanly.
                          _room = _stop_room()
                          _rch, _smo = coin_reach(sym)
                          tally["scanned"] += 1
                          # A coin the indicator is one module from printing
                          # on, leaning the same way as this shape, is the
                          # thing the whole scout exists to catch. It does not
                          # create a trade on its own -- it makes one the
                          # engine was already going to consider more certain.
                          _rp = ripe_now().get(sym)
                          _ripe_add = 0.0
                          if _rp and _rp.get("side") == (1 if s["side"] == "BUY"
                                                         else -1):
                              _ripe_add = min(12.0, _rp.get("ripe", 0) / 100
                                              * 12.0)
                          conf, why_conf = confidence(
                              stairs=(s.get("touches") or s.get("steps")),
                              safety=(_room / _csl if _csl else None),
                              agree=s.get("agree"),
                              against=s.get("against"),
                              reach=_rch, smooth=_smo,
                              run=None,
                              tall=(s.get("thrust") or 0) / 2.0,
                              body=_bs, wick=_wr, votes=None)
                          conf = min(100.0, conf + _ripe_add)
                          _reads = (
                              (f"RIPE +{_ripe_add:.0f}  " if _ripe_add else "")
                              + f"covers 10% "
                              f"{('?' if _rch is None else f'{_rch:.0f}%')} "
                              f"of the time"
                              # Printed, not scored. `smooth` carries weight
                              # 0.0 and has done since the ramps were rebuilt;
                              # the line used to read as though it were part
                              # of the judgement, which is how a number gets
                              # tuned for months without changing anything.
                              # The weight is a strategy decision and is left
                              # exactly as it is -- what changes here is only
                              # that the log stops implying otherwise.
                              f", {('?' if _smo is None else f'{_smo:.2f}')} of "
                              f"it in a line (not scored)"
                              f"  council {s.get('agree') or 0}-"
                              f"{s.get('against') or 0}  "
                              f"level {s.get('touches') or s.get('steps') or 0}x"
                              f"  candle {s.get('thrust') or 0:.1f}x  "
                              f"stop {_csl:.2f}%")
                          # Before the score, because no score may buy past
                          # it. A candle that did nothing is not a break,
                          # however good the coin and however sure the council.
                          if a.min_lev > 0:
                              _cap = sym_cap(sym)
                              if _cap is None or _cap < a.min_lev:
                                  tally["low_leverage"] += 1
                                  log.info("reject %s -- low_leverage: the "
                                           "exchange caps this coin at %s, the "
                                           "floor is %.0fx (10%% of price would "
                                           "be %s of the margin, not %.0f%%)",
                                           ctag,
                                           "unknown" if _cap is None
                                           else f"{_cap:.0f}x",
                                           a.min_lev,
                                           "unknown" if _cap is None
                                           else f"{a.tp * min(a.lev, _cap):.0f}%",
                                           a.tp * a.lev)
                                  continue
                          _thr = float(s.get("thrust") or 0.0)
                          if a.min_thrust > 0 and _thr < a.min_thrust:
                              tally["weak_candle"] += 1
                              # Three decimals, not two. A candle at 1.4996
                              # printed as "1.50x ... the floor is 1.50x",
                              # which reads as a refusal at the boundary and
                              # sends whoever is looking hunting for an
                              # off-by-one that is not there. The number shown
                              # has to be the number compared.
                              log.info("reject %s -- weak_candle: the breaking "
                                       "candle is %.3fx this coin's normal, "
                                       "the floor is %.2fx", ctag, _thr,
                                       a.min_thrust)
                              continue
                          if a.min_confidence > 0 and conf < a.min_confidence:
                              tally["below_threshold"] += 1
                              log.info("reject %s -- below_threshold: scored "
                                       "%.1f of %.0f, needs %.0f [%s]", ctag,
                                       conf, MAX_ACHIEVABLE_SCORE,
                                       a.min_confidence, _reads)
                              continue
                          tally["passed"] += 1
                          log.info("      %s: scored %.1f of %.0f -- %s", sym,
                                   conf, MAX_ACHIEVABLE_SCORE, _reads)
                      else:
                          conf, why_conf = confidence(
                          run=_rs, tall=_ex, body=_bs, wick=_wr,
                          votes=s.get("votes"),
                          stairs=(_st or {}).get("steps"),
                          safety=_safety)
                      _reads = "  ".join(
                          f"{k} {(('%.2f' % v[0]) if isinstance(v[0], float) else v[0])}"
                          f"->{v[1]:.0f}" for k, v in why_conf.items())
                      if a.min_confidence > 0 and conf < a.min_confidence:
                          log.info("skip  %s -- %.0f%% sure, wanted %.0f%%  "
                                   "[%s]", ctag, conf, a.min_confidence, _reads)
                          continue
                      # Floors, for anything that must never be traded whatever
                      # the rest of the reading says. All off by default.
                      if a.min_run > 0 and _rs is not None and _rs < a.min_run:
                          log.info("skip  %s -- run %s under the floor",
                                   ctag, _rs)
                          continue
                      if (a.min_expansion > 0 and _ex is not None
                              and _ex < a.min_expansion):
                          log.info("skip  %s -- %.2fx tall, under the floor",
                                   ctag, _ex)
                          continue
                      if (a.min_body > 0 and _bs is not None
                              and _bs < a.min_body):
                          log.info("skip  %s -- %.0f%% body, under the floor",
                                   ctag, _bs * 100)
                          continue
                      if (a.max_wick > 0 and _wr is not None
                              and _wr > a.max_wick):
                          log.info("skip  %s -- %.0f%% of candles clear the "
                                   "stop, over the ceiling", ctag, _wr)
                          continue
                      if state["equity"] <= 0:
                          log.error("skip  %s -- equity is gone, nothing "
                                    "more will be taken", ctag)
                          continue
                      open_now = [t for t in trades if not t.closed]
                      if loss_limit_hit():
                          _lost, _started = day_loss()
                          log.error("HALT  %s -- $%.2f lost today against a "
                                    "%.0f%% limit on $%.2f. No new positions.",
                                    ctag, _lost, a.daily_loss_limit, _started)
                          if not _halted:
                              _halted.append(True)
                              ping("TRADING HALTED",
                                   f"${_lost:,.2f} lost today, which is the "
                                   f"{a.daily_loss_limit:.0f}% limit on "
                                   f"${_started:,.2f}.\nNo new positions will "
                                   f"be opened.")
                          continue
                      if a.max_per_day > 0:
                          _since = time.time() - 86400
                          _today = [t for t in trades
                                    if (t.opened or 0) >= _since]
                          if len(_today) >= a.max_per_day:
                              log.info("skip  %s -- %d taken in the last "
                                       "twenty-four hours, which is the limit",
                                       ctag, len(_today))
                              continue
                      if a.one_per_symbol and not a.stack and any(
                              t.sym == sym for t in open_now):
                          log.info("skip  %s -- already in %s", ctag, sym)
                          continue
                      used = sum(t.margin for t in open_now)
                      margin, _clev = sized(sym, _csl, state["equity"])
                      if a.size_by_confidence and a.min_confidence > 0:
                          # Bet in proportion to the reading rather than the
                          # same amount on a scraped plan and a certain one.
                          span = max(1e-9, 100.0 - a.min_confidence)
                          scale = 0.5 + 0.5 * min(1.0, max(0.0,
                                  (conf - a.min_confidence) / span))
                          margin *= scale
                      if used + margin > state["equity"] * a.max_exposure + 1e-9:
                          log.info("skip  %s -- $%.2f of $%.2f already "
                                   "committed", ctag, used,
                                   state["equity"] * a.max_exposure)
                          continue
                      period = int(res) * 60 if str(res).isdigit() else 300
                      aim = s["entry"]
                      # How far price has already run from the level, measured
                      # in stops rather than percent so a tight coin and a wide
                      # one are judged the same way.
                      #
                      # `--max-entry-r` was written for exactly this and then
                      # only ever checked on the old convergence stream -- the
                      # source this book does not trade. On the shapes it does
                      # trade there was no limit at all, and it shows: over 125
                      # recorded shapes, the thirteen found more than two stops
                      # from their level NEVER FILLED. Not one. The order rests
                      # for eight bars at a price that is not coming back,
                      # while the signal behind it is already spent and can
                      # never be offered again.
                      _away_r = (abs(px - aim) / aim * 100 / _csl
                                 if aim and _csl else 0.0)
                      if a.max_entry_r > 0 and _away_r > a.max_entry_r:
                          tally["ran_away"] += 1
                          log.info("reject %s -- ran_away: price is %.1f stops "
                                   "from the level already (%.2f%% of a %.2f%% "
                                   "stop), the limit is %.1f", ctag, _away_r,
                                   abs(px - aim) / aim * 100, _csl,
                                   a.max_entry_r)
                          continue
                      pnd = Pending(
                          sym=sym, side=s["side"], want=aim, give_up=0.0,
                          signal_px=px, placed=time.time(),
                          deadline=time.time() + a.fill_bars * period,
                          expires=time.time() + a.fill_bars * period,
                          best_seen=px,
                          sig={**s, "close": px, "margin": margin,
                               "lev": _clev, "tp": _ctp, "sl": _csl,
                               "conf": conf})
                      if a.live and not send_resting(pnd, _ctp, _csl, ctag):
                          continue          # nothing was placed; nothing is owed
                      resting.append(pnd)
                      log.info("PLAN  %s | %.2f%% stop at %.0fx, entry %.8g, "
                               "price now %.8g "
                               "(%.3f%% away)%s, %d bars to fill", ctag,
                               _csl, _clev, aim, px, abs(aim / px - 1) * 100,
                               ", already there" if s["ready"] else "",
                               a.fill_bars)
                      continue
                  # Everything past this point is the old convergence signal.
                  # It has a fixed percentage stop, which on this leverage sits
                  # beyond the liquidation line -- one of these was liquidated
                  # at 1.69% before its own 2.10% stop was reached, for the
                  # whole margin. Only "combo" and "both" ask for them; the
                  # test used to name only "council", so "break" let them all
                  # through while believing it was trading shapes.
                  if a.source not in ("combo", "both"):
                      continue
                  tag = (f"{'CT ' if s.get('counter') else ''}{s['side']} {sym} "
                         f"{res}m {WHO.get(s['who'],'?')} {TIER.get(s['tier'],'?')} "
                         f"agents {s['agents']} score {s['score']}")
                  # Colour judges a combo but not a counter-trend signal.
                  # Measured over this chart's own history: an uncoloured combo
                  # won 19% where a coloured one won 59%, so the filter earns
                  # its place there. On the counter-trend stream it inverts --
                  # uncoloured won 60% against 45% for coloured -- and the
                  # filter was throwing the better half away. Orange stays out
                  # of both either way.
                  orange = COLOURS["orange"]
                  # One rule, both streams: orange is out, everything else is
                  # in. Colourless signals count as in -- the SELL side of the
                  # chart publishes no colour at all, so requiring one made the
                  # book long-only for combos without anyone asking it to.
                  #
                  # Worth knowing while this runs: over 225 recorded signals the
                  # colourless group won 41.6% against 54.9% for blue, and it is
                  # 93 of the 225. If that holds up as the sample grows it is an
                  # argument for narrowing again -- tbt-signals tracks it either
                  # way. The gate is the --colour flag, not a hardcoded "never
                  # orange": the indicator drops orange before it prints, so
                  # the book (purple, blue) behaves exactly as before, while
                  # the beast asks for all three and now actually gets them.
                  colour_ok = s["who"] in want
                  # How much of the signal candle is body rather than wick. A
                  # candle we cannot see is never a reason to refuse the trade,
                  # and a candle thinner than 0.15% of price has no meaningful
                  # shape -- the exchange's own rounding is that big.
                  body = None
                  _c = ohlc.get(s["t"])
                  if _c:
                      _o, _h, _l, _cl = _c
                      _h = max(_h, _o, _cl)
                      _l = min(_l, _o, _cl)
                      _rng = _h - _l
                      if _rng > 0 and _cl and _rng / _cl * 100 >= 0.15:
                          body = abs(_cl - _o) / _rng
                  fat = (a.max_body > 0 and body is not None
                         and body > a.max_body)
                  # The coin's own candles, which decide whether the signal is
                  # worth anything at all. A coin the finder has never
                  # measured is not refused: no number is not a small number.
                  _atr_pct = coin_atr(sym)
                  thin = (a.min_atr > 0 and _atr_pct is not None
                          and _atr_pct < a.min_atr)
                  # Which way the coin has been going. A coin the finder has
                  # never measured is not refused -- no number is not a flat
                  # coin -- and a coin that has barely moved is not "against"
                  # anything, so only a real move in the wrong direction counts.
                  _tr = coin_trend(sym)
                  wrong_way = (a.with_trend and _tr is not None
                               and abs(_tr) >= 1.0
                               and (_tr > 0) != (s["side"] == "BUY"))
                  # The exchange's own ceiling on this coin.
                  #
                  # --min-lev existed, was documented and was set, and was
                  # only ever checked on the shape source -- so on the source
                  # this book actually trades there was no floor at all. It
                  # opened USELESSUSDT at 25x under a --min-lev of 40 and
                  # nothing said a word. The size was honest, the refusal
                  # simply never happened, and a five percent target pays
                  # 125% of the margin at 25x rather than the 200% the whole
                  # arithmetic was built on.
                  _cap = sym_cap(sym) if a.min_lev > 0 else None
                  low_lev = a.min_lev > 0 and (_cap is None
                                               or _cap < a.min_lev)
                  # With one position at a time, taking a mediocre signal
                  # costs the next good one. This is the judgement that
                  # replaced the daily cap.
                  _pts, _why = entry_score(sym, s["side"], s["agents"])
                  poor = a.min_entry > 0 and _pts < a.min_entry
                  # The learned filter: the nightly-trained selector's
                  # probability that this candidate reaches +5% before
                  # -1.25%. Refused below the floor, with its own reason
                  # and its own journal branch -- and nothing is lost when
                  # it is off.
                  _fprob = None
                  _filtered = False
                  if _filter is not None and a.filter_min > 0:
                      _fprob = _filter.score(model_features(
                          sym, s["side"], s))
                      _filtered = _fprob < a.filter_min
                  # Every score is remembered, taken or refused. The bar is a
                  # percentile of what the day has offered, and a day judged
                  # only on what it accepted knows nothing about what it
                  # turned down: with the poor ones dropped the sample starts
                  # at the floor, every percentile of it sits too high, and
                  # the book grows fussier the worse the day gets -- the exact
                  # opposite of what the pacing is for.
                  state["scores"] = pace.remember(
                      state.get("scores"), sym, _pts)
                  _bar, _pace = (None, "")
                  if a.per_day > 0 and not poor:
                      _bar, _pace = pace.bar(state.get("scores"),
                                             state.get("trades") or trades,
                                             a.per_day, a.min_entry)
                      if _bar is None:
                          log.info("skip  %s -- the day's %d trades are "
                                   "spent [%s]", tag, a.per_day, _pace)
                          continue
                      if _pts < _bar:
                          log.info("wait  %s -- scored %.0f, the bar is "
                                   "%.0f right now [%s]",
                                   tag, _pts, _bar, _pace)
                          continue
                  if (not colour_ok
                          or (a.require_wired and not s["wired"])
                          or s["agents"] < a.min_agents
                          or s["tier"] in a.skip_tier
                          or thin
                          or wrong_way
                          or low_lev
                          or poor
                          or fat
                          or _filtered):
                      why = ("colour" if not colour_ok
                             else "Tesla unwired" if not s["wired"]
                             else f"tier {s['tier']}" if s["tier"] in a.skip_tier
                             else f"body {body:.0%} of the candle" if fat
                             else (f"thin_coin: ATR {_atr_pct:.2f}% of price, "
                                   f"the floor is {a.min_atr:.2f}%") if thin
                             else (f"low_leverage: the exchange caps this "
                                   f"coin at "
                                   f"{'unknown' if _cap is None else f'{_cap:.0f}x'}"
                                   f", the floor is {a.min_lev:.0f}x") if low_lev
                             else (f"against_trend: the coin has moved "
                                   f"{_tr:+.2f}% in two hours and this is a "
                                   f"{s['side']}") if wrong_way
                             else (f"scored {_pts:.0f} of 100, needs "
                                   f"{a.min_entry:.0f} [{_why}]") if poor
                             else (f"filter: the model gives it "
                                   f"{_fprob*100:.0f}%, the floor is "
                                   f"{a.filter_min*100:.0f}%") if _filtered
                             else "agents")
                      log.info("skip  %s -- %s", tag, why)
                      continue
                  if _fprob is not None:
                      log.info("model  %s %.0f%%", tag, _fprob * 100)

                  period = int(res) * 60 if str(res).isdigit() else 300
                  px = prices.get(sym)
                  if not px:
                      log.warning("skip  %s -- no price on Bitunix", tag)
                      continue
                  if state["equity"] <= 0:
                      log.error("skip  %s -- equity is gone, nothing more "
                                "will be taken", tag)
                      continue
                  if loss_limit_hit():
                      log.error("HALT  %s -- the day's loss limit is reached",
                                tag)
                      continue
                  open_now = [t for t in trades if not t.closed]
                  if a.one_per_symbol and not a.stack and any(t.sym == sym for t in open_now):
                      log.info("skip  %s -- already in %s", tag, sym)
                      continue
                  used = sum(t.margin for t in open_now)
                  # Counter-trend combos carry the same three-robot agreement
                  # but fight the larger phase, so they get half the size.
                  size_frac = live_frac() * (a.counter_frac if s.get("counter") else 1.0)
                  margin = state["equity"] * size_frac
                  # Two 50% trades fill the account exactly. A third would be
                  # betting money the first two are already using, so it waits.
                  if used + margin > state["equity"] * a.max_exposure + 1e-9:
                      prio = [x.upper().replace(".P", "") for x in a.priority]
                      if sym.upper() in prio:
                          # A priority coin outranks a position already open on a
                          # non-priority one. Yield the newest of those -- it has
                          # had the least time to work, so it is the cheapest to
                          # give up.
                          # Yielding closes a position in the book only. On a
                          # live row that would leave the exchange still
                          # holding it while the book believes it is flat --
                          # the phantom, inverted. Live positions are closed by
                          # their own attached stop and target, so they are
                          # never given up to make room.
                          victims = [t for t in open_now
                                     if t.sym.upper() not in prio and not t.live]
                          if a.live and any(t.sym.upper() not in prio
                                            for t in open_now) and not victims:
                              log.info("no room for %s and the position holding "
                                       "it is live -- leaving it alone", sym)
                          if victims:
                              v = max(victims, key=lambda t: t.opened)
                              px_v = prices.get(v.sym)
                              if px_v:
                                  mv = (px_v / v.entry - 1) * (1 if v.side == "BUY" else -1)
                                  v.exit = px_v
                                  v.reason = "yielded"
                                  v.pnl = v.notional * mv - v.notional * a.fee_bps / 1e4
                                  v.closed = time.time()
                                  v.pnl += v.funding
                                  state["equity"] += v.pnl
                                  log.info("YIELD %s %s closed at %+.2f%% ($%+.2f) "
                                           "to free room for %s", v.side, v.sym,
                                           mv * 100, v.pnl, sym)
                                  side_tag = "LONG" if v.side == "BUY" else "SHORT"
                                  ping(f"{v.sym} YIELDED",
                                       f"{v.pnl:+,.2f}\n${state['equity']:,.2f}")
                                  open_now = [t for t in trades if not t.closed]
                                  used = sum(t.margin for t in open_now)
                                  margin = state["equity"] * size_frac
                      if used + margin > state["equity"] * a.max_exposure + 1e-9:
                          log.info("skip  %s -- $%.2f of $%.2f already committed",
                                   tag, used, state["equity"] * a.max_exposure)
                          continue

                  # 'now' takes the print as the entry: no waiting, no resting
                  # order. The indicator fires the moment its three agents agree,
                  # so the label and the fill are the same event. The other modes
                  # below all wait for a better price and are kept for research.
                  if a.entry == "now":
                      pass
                  elif a.entry == "edge":
                      o = ohlc.get(s["t"])
                      if not o:
                          log.info("skip  %s -- no candle for the signal bar", tag)
                          continue
                      _, hi, lo, cl = o
                      aim = lo if s["side"] == "BUY" else hi
                      period = int(res) * 60 if str(res).isdigit() else 300
                      resting.append(Pending(
                          sym=sym, side=s["side"], want=aim, give_up=0.0,
                          signal_px=px, placed=time.time(),
                          deadline=time.time() + a.fill_bars * period,
                          expires=time.time() + a.fill_bars * period,
                          best_seen=px, sig={**s, "close": cl, "margin": margin}))
                      log.info("WAIT  %s | want %.8g, price now %.8g (%.3f%% away), "
                               "%d bars to fill or it is dropped", tag, aim, px,
                               abs(aim / px - 1) * 100, a.fill_bars)
                      continue


                  if a.entry == "smart":
                      o = ohlc.get(s["t"])
                      _, hi, lo, cl = o if o else (px, px, px, px)
                      # Aim at the candle's own extreme; accept anything up to
                      # hunt-give-up of the stop worse than the signal price.
                      aim = lo if s["side"] == "BUY" else hi
                      slip = geo(sym)[1] * a.hunt_give_up / 100
                      give = px * (1 + slip) if s["side"] == "BUY" else px * (1 - slip)
                      resting.append(Pending(
                          sym=sym, side=s["side"], want=aim, give_up=give,
                          signal_px=px, placed=time.time(),
                          deadline=time.time() + a.hunt_seconds,
                          expires=time.time() + a.hunt_seconds + 5,
                          best_seen=px,
                          sig={**s, "close": cl, "margin": margin}))
                      log.info("HUNT  %s | aiming %.8g vs signal %.8g (%.3f%% better), "
                               "abort above %.8g, %.0fs limit", tag, aim, px,
                               abs(aim/px-1)*100, give, a.hunt_seconds)
                      continue

                  if a.entry not in ("market", "now"):
                      o = ohlc.get(s["t"])
                      if not o:
                          log.info("skip  %s -- no candle for the signal bar", tag)
                          continue
                      _, hi, lo, cl = o
                      edge = lo if s["side"] == "BUY" else hi
                      if a.entry == "back":
                          # A fixed step behind the signal price, not a fraction
                          # of the candle: the candle's own size varies wildly
                          # and the midpoint is usually far deeper than the
                          # price ever comes back.
                          aim = (px * (1 - a.entry_back / 100)
                                 if s["side"] == "BUY"
                                 else px * (1 + a.entry_back / 100))
                      else:
                          aim = edge if a.entry == "extreme" else (cl + edge) / 2
                      period = int(res) * 60 if str(res).isdigit() else 300
                      resting.append(Pending(
                          sym=sym, side=s["side"], want=aim, give_up=0.0,
                          signal_px=px, placed=time.time(),
                          deadline=time.time() + a.fill_bars * period,
                          expires=time.time() + a.fill_bars * period,
                          best_seen=px, sig={**s, "close": cl, "margin": margin}))
                      log.info("LIMIT %s | resting at %.8g vs close %.8g "
                               "(%.2f%% better), %d bars to fill",
                               tag, aim, cl, abs(aim / cl - 1) * 100, a.fill_bars)
                      continue
                  notional = margin * sym_lev(sym)
                  side_tag = "LONG" if s["side"] == "BUY" else "SHORT"
                  # The paper venue: in live the exchange decides the
                  # fill; on paper the REAL order book decides it -- the
                  # order eats the book level by level and a thin book
                  # fills only part of the size, exactly as Bitunix would.
                  if a.live:
                      fill = cross(sym, px, s["side"])
                  else:
                      import paper_venue
                      fill, _frac, _known = paper_venue.depth_fill(
                          cli, sym, s["side"], notional, px)
                      if _frac < 1.0 - 1e-9:
                          log.info("PARTIAL %s -- the book absorbed %.0f%% "
                                   "of the size; the rest is a miss", tag,
                                   _frac * 100)
                          notional *= _frac
                          margin *= _frac
                  o = ohlc.get(s["t"])
                  _rhl = real_hl.get((sym, s["t"]))
                  if _rhl:
                      best = _rhl[0] if s["side"] == "BUY" else _rhl[1]
                  elif o:
                      best = o[2] if s["side"] == "BUY" else o[1]
                  else:
                      best = fill
                  _tp, _sl = geo(sym)
                  err_r = abs(fill / best - 1) * 100 / _sl if _sl else 0.0
                  if a.max_entry_r > 0 and err_r > a.max_entry_r:
                      log.info("skip  %s -- fill %.8g is %.2fR from the signal "
                               "price %.8g, past the %.2fR limit", tag, fill,
                               err_r, best, a.max_entry_r)
                      continue
                  lag = time.time() - (s["t"] + period)
                  tr = Trade(
                      sym=sym, side=s["side"], qty=notional / fill, entry=fill,
                      tp=fill * (1 + _tp / 100) if s["side"] == "BUY" else fill * (1 - _tp / 100),
                      sl=fill * (1 - _sl / 100) if s["side"] == "BUY" else fill * (1 + _sl / 100),
                      opened=time.time(), bar=s["t"], who=s["who"], tier=s["tier"],
                      score=_pts, agents=s["agents"], margin=margin,
                      notional=notional, entry_kind="market", entry_err_r=err_r,
                      # Without this every trade was filed as a combo, however
                      # it was actually sized. The money was right -- the size
                      # is worked out from the signal well before the row is
                      # built -- but the books said combo for counter-trend
                      # trades, so anything splitting the two read a fiction.
                      counter=bool(s.get("counter")))

                  # The order body is built either way. Without --live it is
                  # logged and nothing is sent, which exercises the sizing and
                  # the decimal formatting -- the two things that get an order
                  # rejected -- without spending anything.
                  _lev = live_ready.get(sym, {}).get("lev", int(a.lev))
                  _minq = live_ready.get(sym, {}).get("min_qty", 0.0)
                  try:
                      _qty_s = cli.fmt_qty(sym, cli.round_qty(sym, notional / fill))
                      _tp_s = cli.fmt_price(sym, tr.tp)
                      _sl_s = cli.fmt_price(sym, tr.sl)
                      _qty_f = float(_qty_s)
                  except Exception as e:
                      log.error("skip  %s -- cannot size the order: %s", tag, str(e)[:120])
                      continue
                  if _minq and _qty_f < _minq:
                      log.info("skip  %s -- order size %s is under the exchange "
                               "minimum %g", tag, _qty_s, _minq)
                      continue
                  # Paper mode enforces the REAL minimum order size too: a
                  # trade the venue would refuse is refused here with the
                  # same words, so the paper record is the live record.
                  if not a.live:
                      import paper_venue
                      _why_mn = paper_venue.enforce_min_qty(cli, sym, _qty_f)
                      if _why_mn:
                          log.info("skip  %s -- %s", tag, _why_mn)
                          continue
                  # Unique per order, or the exchange sees two of ours as one.
                  # The combo and counter-trend streams can both fire on the
                  # same bar and side -- that has already happened once in the
                  # book -- and a four-letter symbol slice collides across pairs
                  # sharing a prefix. Cancelling by clientId would then hit the
                  # wrong order.
                  tr.client_id = (f"tbt{int(s['t'])}{s['side'][0]}"
                                  f"{'c' if s.get('counter') else 'n'}{sym}")[:32]

                  if a.live:
                      _n_live = sum(1 for t in trades if t.live)
                      if a.max_live and _n_live >= a.max_live:
                          log.info("skip  %s -- live cap of %d positions reached",
                                   tag, a.max_live)
                          continue
                      try:
                          _r = cli.place_market_order(
                              sym, s["side"], _qty_s, tp_price=_tp_s, sl_price=_sl_s,
                              client_id=tr.client_id)
                          tr.live = True
                          tr.order_id = str((_r or {}).get("orderId") or "")
                          log.info("LIVE  %s | %s @ ~%.8g  tp %s  sl %s  %dx  "
                                   "margin $%.2f  order %s", tag, _qty_s, fill,
                                   _tp_s, _sl_s, _lev, margin, tr.order_id or "?")
                          # What the exchange actually gave us. `fill` above is
                          # the ticker crossed by half the spread -- an
                          # estimate made before the order was sent. A market
                          # order into a thin book fills at a worse price and
                          # sometimes for less than the whole size, and every
                          # later number here (the PnL, the exposure, the
                          # margin the exchange is really holding) was being
                          # computed from the guess instead of the fact.
                          #
                          # The target and stop are NOT recomputed: they are
                          # already resting on the exchange at the prices sent
                          # above, and moving them here would only make the
                          # book disagree with the account.
                          _st = cli.order_state(order_id=tr.order_id or None,
                                                client_id=tr.client_id)
                          if _st["filled"] > 0:
                              _px = _st["avg_price"] or tr.entry
                              if _st["filled"] < _qty_f - 1e-12:
                                  log.warning("PARTIAL %s -- %g of %s filled",
                                              tag, _st["filled"], _qty_s)
                              tr.qty = _st["filled"]
                              tr.entry = _px
                              tr.notional = tr.qty * _px
                              tr.margin = tr.notional / _lev if _lev else tr.notional
                          elif _st["status"] == "unknown":
                              log.warning("%s -- the fill could not be read; the "
                                          "book keeps the estimated entry %.8g",
                                          tag, tr.entry)
                      except BitunixUnknown as e:
                          # The request went out and no answer came back. That
                          # is not a failure -- the order may be resting on the
                          # exchange this second. Recording nothing leaves a
                          # 50x position the book will never manage or account
                          # for; recording a row invents one that may not
                          # exist. Neither guess is acceptable, so go and look.
                          got = None
                          for _try in range(3):
                              try:
                                  got = cli.order_detail(
                                      client_id=tr.client_id) or None
                                  if got:
                                      break
                              except Exception as _e:
                                  log.warning("checking %s after a lost reply: "
                                              "%s", tr.client_id, str(_e)[:80])
                              time.sleep(1.0)
                          if got:
                              tr.live = True
                              tr.order_id = str(got.get("orderId") or "")
                              log.error("LIVE  %s | the reply was lost but the "
                                        "order EXISTS (%s) -- adopting it",
                                        tag, tr.order_id or tr.client_id)
                              ping(f"{sym} ORDER LANDED LATE",
                                   f"{s['side']} {sym} -- the reply timed out "
                                   f"but the order is on the exchange.")
                          else:
                              log.error("ORDER UNKNOWN %s: %s -- no order found "
                                        "for %s, treating it as not placed",
                                        tag, str(e)[:160], tr.client_id)
                              ping(f"CHECK {sym} BY HAND",
                                   f"{s['side']} {sym} was sent and never "
                                   f"answered. Nothing was found for "
                                   f"{tr.client_id}, so the book holds no "
                                   f"position -- confirm on the exchange.")
                              continue
                      except Exception as e:
                          # The exchange understood and refused: no position
                          # exists, so no row should claim one.
                          log.error("ORDER FAILED %s: %s", tag, str(e)[:200])
                          ping(f"ORDER FAILED - {sym}",
                               f"{s['side']} {sym} was not placed.\n{str(e)[:180]}")
                          continue
                  else:
                      log.info("would send: %s %s qty %s tp %s sl %s %dx "
                               "(margin $%.2f) -- no --live, nothing sent",
                               s["side"], sym, _qty_s, _tp_s, _sl_s, _lev, margin)
                  trades.append(tr)
                  # The one thing the user asked to see on the phone: did we get
                  # in where we said we would?  For a long that is the candle's
                  # low, for a short its high.  Say it in words, not in numbers
                  # they have to decode, and say it plainly when we missed.
                  side_tag = "LONG" if s["side"] == "BUY" else "SHORT"
                  ping(f"{sym} {side_tag}",
                       f"entry  {fill:.8g}\n"
                       f"target {tr.tp:.8g}\n"
                       f"stop   {tr.sl:.8g}\n"
                       f"${state['equity']:,.2f}")
                  log.info("OPEN  %s | %.6g @ %.8g (last %.8g, %.1f bps spread) "
                           "tp %.8g sl %.8g  margin $%.2f  %.1fs after the signal"
                           "  entry error %.2fR", tag, tr.qty, fill, px,
                           (spreads.get(sym) or (0.0, 0.0))[0], tr.tp, tr.sl,
                           margin, lag, err_r)

          # A chart that has stopped moving. The guard now watches for this
          # too, but the book is the process that would act on the stale
          # candles, so it says so itself rather than trusting another service
          # to be running. It cost fourteen hours once, and the only trace was
          # one "stale bar" line per poll -- which is a log entry, not an alarm.
          for _tv, _res, _sg, _oh in charts:
              if not _oh:
                  continue
              try:
                  _bar_s = int(_res) * 60
              except (TypeError, ValueError):
                  _bar_s = 300
              _age = time.time() - max(_oh)
              _sym_f = _tv.split(":")[-1].replace(".P", "")
              if _age > STALE_CHART_BARS * _bar_s:
                  if _sym_f not in _frozen:
                      _frozen.add(_sym_f)
                      log.error("FROZEN %s -- newest candle is %.0f minutes old "
                                "on a %dm chart. The feed has stopped; nothing "
                                "will be traded from it.", _sym_f, _age / 60,
                                _bar_s // 60)
                      ping(f"{_sym_f} CHART FROZEN",
                           f"The newest candle is {_age/60:.0f} minutes old. "
                           f"The book is reading a stopped chart.")
              elif _sym_f in _frozen:
                  _frozen.discard(_sym_f)
                  log.info("%s is live again (newest candle %.0fs old)",
                           _sym_f, _age)

          if time.time() - last_tally >= TALLY_EVERY_S and tally["scanned"]:
              last_tally = time.time()
              log.info("candidates: %d scanned, %d low leverage, %d weak "
                       "candles, %d ran away, %d below %.0f, %d passed  "
                       "(over the last %.0f minutes)", tally["scanned"],
                       tally["low_leverage"], tally["weak_candle"],
                       tally["ran_away"], tally["below_threshold"],
                       a.min_confidence, tally["passed"], TALLY_EVERY_S / 60)
              for k in tally:
                  tally[k] = 0

          _STUDY_SWAPPED.clear()

          if first:
              syms = ", ".join(f"{c[0].split(':')[-1]}@{c[1]}m" for c in charts)
              log.info("watching %s -- %d existing signals ignored", syms, len(seen))
              first = False

          if a.heartbeat > 0 and time.time() - last_beat > a.heartbeat * 60:
              last_beat = time.time()
              pass  # the phone gets opens and closes; there is no separate report
          state["trades"] = [asdict(t) for t in trades]
          state["resting"] = [asdict(x) for x in resting]
          state["known_syms"] = sorted(known_syms)
          # The day's scores, so a restart does not hand the book a fresh
          # budget and an empty memory of what the day has been offering.
          state["scores"] = pace.seen(state.get("scores"))
          # `seen` only has to cover the bars still being read, so drop keys
          # older than a week rather than letting the file grow without bound
          # until a truncated write corrupts it.
          _floor = time.time() - 7 * 86400
          state["seen"] = [list(k) for k in seen if len(k) < 2 or k[1] >= _floor]
          _tmp = BOOK.with_suffix(".tmp")
          _tmp.write_text(json.dumps(state))
          _tmp.replace(BOOK)
          _per = min((int(c[1]) * 60 if str(c[1]).isdigit() else 300)
                     for c in charts) if charts else 300
          _into = time.time() % _per
          time.sleep(a.sprint if (_into < 5 or _into > _per - 3) else a.interval)
      except KeyboardInterrupt:
          raise
      except Exception as e:
          consecutive_errors += 1
          log.exception('poll failed (%d in a row): %s',
                        consecutive_errors, str(e)[:120])
          if consecutive_errors >= 5:
              for _k in list(_CONNS):
                  drop_conn(_k)
              consecutive_errors = 0
          time.sleep(3)
      else:
          consecutive_errors = 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(f"\nstopped -- book kept in {BOOK}")
    finally:
        # The lock belongs to THIS book, whichever ledger it owns. The
        # literal data/paper.lock here meant the beast's exit tested the
        # book's lock (and left its own behind).
        lk = BOOK.with_suffix(".lock")
        try:
            if lk.exists() and lk.read_text().strip() == str(os.getpid()):
                lk.unlink()
        except Exception:
            pass
