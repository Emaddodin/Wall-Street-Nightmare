#!/usr/bin/env python3
"""
Rank coins by how well they actually deliver OUR setup, not by how loud they are.

The old scan counted big candles. A coin can throw 8% candles all day and still
be useless to us: what matters is whether it offers the impulse, gives the
Fibonacci pullback, and then reaches the target before the stop. So this runs
the real strategy over each coin's recent history and ranks on the outcome.

It also stopped being slow. Seven hundred pairs fetched one after another took
twelve minutes; fetched in parallel it takes about one.

  data/boom.json   the ranked list the hunt reads
"""
from __future__ import annotations
import os
import sys
import json
import logging
import time
import argparse
import statistics as S
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Where this engine lives, taken from this file rather than written out. The
# literal path meant the scanner could only ever run on the server, and off it
# every write went to a directory that does not exist -- or, worse, could have
# gone to a stale copy of one that does.
BOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BOT))
logging.basicConfig(level=logging.ERROR)
from dotenv import load_dotenv
load_dotenv(BOT / ".env")
from exchange.bitunix import BitunixClient


def write_atomic(path, obj):
    """Replace the file, never edit it in place.

    The book reads these while this is writing them, on a timer, from another
    process. A reader that lands mid-write gets a truncated document -- and
    the one it matters most for is the measurements file, because an
    unmeasured coin used to be judged on a LOWER bar than a coin measured
    never to travel. os.replace is atomic within a filesystem, so a reader
    sees either the old file or the new one and never half of either.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

TP, SL = 10.0, 2.1   # the book's own geometry -- keep these in step
IMPULSE, RETR, WAIT, HOLD, LEGBARS = 8.0, 0.618, 8, 48, 3


def replay(m):
    """Run our own setup over this coin and report what it actually did."""
    hi, lo, cl = m.high.to_numpy(), m.low.to_numpy(), m.close.to_numpy()
    n = len(cl)
    won = lost = stalled = 0
    mins = []

    def leg_from(i):
        """The first impulse that starts at bar i, if there is one."""
        for k in range(1, LEGBARS + 1):
            j = i + k
            if j >= n:
                return None
            if (hi[j] - lo[i]) / lo[i] * 100 >= IMPULSE:
                return j, lo[i], hi[j], True
            if (hi[i] - lo[j]) / hi[i] * 100 >= IMPULSE:
                return j, hi[i], lo[j], False
        return None

    i = 20
    while i < n - 2:
        got = leg_from(i)
        if got is None:
            i += 1
            continue
        j, a, b, long = got
        span = abs(b - a)
        if span > 0:
            want = b - span * RETR if long else b + span * RETR
            fill = None
            for w in range(1, WAIT + 1):
                t = j + w
                if t >= n:
                    break
                if (lo[t] <= want) if long else (hi[t] >= want):
                    fill = t
                    break
            if fill is not None:
                e, out, at = want, None, fill
                for t in range(fill, min(fill + HOLD, n)):
                    adv = (e - lo[t]) / e * 100 if long else (hi[t] - e) / e * 100
                    fav = (hi[t] - e) / e * 100 if long else (e - lo[t]) / e * 100
                    if adv >= SL:
                        out, at = 0, t
                        break
                    if fav >= TP:
                        out, at = 1, t
                        break
                if out == 1:
                    won += 1
                    mins.append((at - fill) * 15)
                elif out == 0:
                    lost += 1
                else:
                    stalled += 1
        # Step past the leg we just used. Overlapping legs from one move are the
        # same idea, and counting each would inflate both the setup count and
        # the record built on it.
        i = j + 1
    dec = won + lost
    return dict(setups=won + lost + stalled, decided=dec,
                won=won, lost=lost, stalled=stalled,
                hit=(won / dec if dec else 0.0),
                med_min=(S.median(mins) if mins else 0))


def makes_the_shape(m, bars=600):
    """How often this coin actually produces the setup we trade, and how hard.

    Travelling ten percent is necessary and not sufficient. A coin can cover
    the distance every other hour and never once break a level with a candle
    worth entering on -- and that coin will sit at the top of the ranking
    doing nothing, which is exactly what happened: twenty-five coins ranked by
    movement produced two weak shapes in three hours.

    So the ranking asks the entry's own question. The same two functions the
    book uses are run over this coin's own recent history:

        shapes      how many appeared, per day
        force       the median breaking candle against the coin's ordinary
                    one -- the single strongest thing separating the setups
                    that reach the target from the ones that do not
        strong      how many of them had real force behind them

    A coin that never produces the shape is not a coin for this strategy,
    however far it travels.
    """
    try:
        from papertrade import breakout, trend_ride, _stop_room
    except Exception:
        return dict(shapes=0.0, force=0.0, strong=0.0)
    o, h, l, c = (m.open.to_numpy(), m.high.to_numpy(),
                  m.low.to_numpy(), m.close.to_numpy())
    n = len(c)
    if n < 120:
        return dict(shapes=0.0, force=0.0, strong=0.0)
    lo = max(0, n - bars)
    idx = list(range(lo, n))
    ohlc = {i: (float(o[i]), float(h[i]), float(l[i]), float(c[i]))
            for i in idx}
    room = _stop_room()
    forces = []
    for i in idx[70:]:
        g = breakout(ohlc, i, max_stop=room) or trend_ride(ohlc, i,
                                                           max_stop=room)
        if g:
            forces.append(g.get("thrust") or 0.0)
    days = len(idx) / 96.0
    forces.sort()
    return dict(
        shapes=len(forces) / days if days else 0.0,
        force=float(forces[len(forces) // 2]) if forces else 0.0,
        strong=len([f for f in forces if f >= 2.0]) / days if days else 0.0,
    )


def travels(m, want=10.0, window=32):
    """How often this coin simply travels the target, from anywhere.

    This is the whole strategy said in one measurement. The target is ten
    percent; a coin that covers ten percent in a couple of hours reaches it
    from any clean entry, and a coin that needs a day does not reach it from
    the best entry ever placed. Trend length, shape, confluence -- all of it is
    downstream of whether the coin moves that far at all.

    So: from every bar, look forward `window` bars and ask whether price ever
    got `want` percent away, in either direction. The answer is a fraction of
    all bars, which is the honest chance that any given entry on this coin has
    somewhere to go.

    Nothing here is about our setup, and that is deliberate. It measures the
    ground rather than our footwork.
    """
    hi, lo, cl = m.high.to_numpy(), m.low.to_numpy(), m.close.to_numpy()
    n = len(cl)
    if n < window + 20:
        return dict(reach=0.0, reach_up=0.0, reach_dn=0.0, median_bars=0.0)
    up = dn = 0
    bars_to, effs = [], []
    for i in range(n - window):
        c = cl[i]
        if not c:
            continue
        tu = c * (1 + want / 100)
        td = c * (1 - want / 100)
        hit_u = hit_d = None
        for j in range(i + 1, i + 1 + window):
            if hit_u is None and hi[j] >= tu:
                hit_u = j - i
            if hit_d is None and lo[j] <= td:
                hit_d = j - i
            if hit_u is not None and hit_d is not None:
                break
        if hit_u is not None:
            up += 1
        if hit_d is not None:
            dn += 1
        first = min([x for x in (hit_u, hit_d) if x is not None], default=None)
        if first is not None:
            bars_to.append(first)
            # How directly it got there. Net distance covered against the total
            # path walked: a move that goes straight has an efficiency near
            # one, a move that thrashes its way to the same place has an
            # efficiency near zero -- and it visits our stop on the way.
            # This is the difference between a coin that CAN pay and a coin
            # that WILL.
            j = i + first
            net = abs(cl[j] - c) / c * 100
            path = 0.0
            for k in range(i + 1, j + 1):
                path += (hi[k] - lo[k]) / c * 100
            if path > 0:
                effs.append(net / path)
    total = n - window
    bars_to.sort()
    effs.sort()
    return dict(
        reach=(len(bars_to) / total * 100) if total else 0.0,
        reach_up=up / total * 100 if total else 0.0,
        reach_dn=dn / total * 100 if total else 0.0,
        median_bars=float(bars_to[len(bars_to) // 2]) if bars_to else 0.0,
        smooth=float(effs[len(effs) // 2]) if effs else 0.0,
    )


def shape(m):
    """The things about the coin itself that decide whether we survive it."""
    rng = (m.high - m.low) / m.close * 100
    body = (m.close - m.open).abs() / m.close * 100
    top = m[["open", "close"]].max(axis=1)
    bot = m[["open", "close"]].min(axis=1)
    up_w = (m.high - top) / m.close * 100
    dn_w = (bot - m.low) / m.close * 100
    last = slice(-96, None)
    wick = ((up_w[last] >= SL) | (dn_w[last] >= SL)).mean() * 100
    recent = rng.tail(96).median()
    older = rng.iloc[-500:-96].median() if len(rng) > 200 else recent
    return dict(
        big8=int((rng.tail(96) >= 8).sum()),
        biggest=float(rng.tail(96).max()),
        typical=float(recent),
        expansion=float(recent / older) if older else 1.0,
        wick=float(wick),
        **travels(m),
        **makes_the_shape(m),

        body=float(body.tail(96).median()),
    )


def one(cli, sym):
    try:
        m = cli.history(sym, interval="15m", bars=1400)
    except Exception as e:
        return sym, None, f"fetch: {str(e)[:40]}"
    if m is None or len(m) < 400:
        return sym, None, f"only {0 if m is None else len(m)} bars"
    try:
        r = {"sym": sym, **shape(m), **replay(m)}
    except Exception as e:
        return sym, None, f"replay: {str(e)[:40]}"
    return sym, r, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--top", type=int, default=14)
    ap.add_argument("--min-setups", type=int, default=6,
                    help="a coin that has not offered our setup this many times "
                         "recently is a coin we cannot judge")
    ap.add_argument("--min-shapes", type=float, default=1.0,
                    help="how many times a day the coin must actually produce "
                         "the setup we trade")
    ap.add_argument("--max-minutes", type=float, default=60.0,
                    help="drop a coin whose own setups take longer than this "
                         "many minutes to reach the target (60 = four candles)")
    ap.add_argument("--max-wick", type=float, default=30.0)
    ap.add_argument("--max-spread", type=float, default=20.0)
    # The scout spends its whole day walking this list. Better than half of
    # what it walked was capped by the exchange below fifty times, where ten
    # percent of price is not the payout this strategy is built on -- so every
    # sweep spent on one was a sweep not spent on a coin the book can trade.
    # The ceiling comes from the instrument list that is already fetched, so
    # this costs nothing.
    ap.add_argument("--min-lev", type=float, default=0.0, metavar="X",
                    help="leave out coins the exchange caps below this "
                         "leverage (0 = keep them all)")
    a = ap.parse_args()

    # One scanner at a time.
    #
    # systemd will not start a second copy of the unit, but nothing stopped a
    # hand-run one going at the same time -- and both of them rewrite the
    # watchlist, the measurements and the ranked list that the book reads
    # while they are running. The writes are atomic one file at a time, so
    # neither can be seen half-written; what is not atomic is the SET of
    # three, and two overlapping runs can leave a watchlist from one and
    # measurements from the other. That happened at least twice today,
    # because the person running it by hand was me.
    lock = BOT / "data" / "boom.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists():
        try:
            old = int(lock.read_text().strip())
            os.kill(old, 0)
        except (ProcessLookupError, ValueError):
            pass                                  # stale, take it
        except PermissionError:
            pass
        else:
            print(f"  another scan is already running (pid {old}); this one "
                  f"would overwrite its results half-finished. Nothing done.",
                  flush=True)
            return 1
    lock.write_text(str(os.getpid()))
    try:
        return _scan(a)
    finally:
        try:
            if lock.exists() and lock.read_text().strip() == str(os.getpid()):
                lock.unlink()
        except Exception:
            pass


def _scan(a) -> int:
    cli = BitunixClient()
    pairs = [p for p in cli.trading_pairs() if p.endswith(("USDT", "USDC"))]
    t0 = time.time()
    print(f"  {len(pairs)} pairs, {a.workers} at a time", flush=True)

    rows, failed = [], []
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(one, cli, s): s for s in pairs}
        done = 0
        for f in as_completed(futs):
            sym, r, err = f.result()
            done += 1
            if done % 150 == 0:
                print(f"    {done}/{len(pairs)}  ({time.time()-t0:.0f}s)", flush=True)
            if r:
                rows.append(r)
            else:
                failed.append((sym, err))
    print(f"  measured {len(rows)} in {time.time()-t0:.0f}s, "
          f"{len(failed)} could not be read", flush=True)

    # Anything that cannot carry the strategy is out, and the reason is kept so
    # a coin never disappears without one.
    keep, dropped = [], []
    for r in rows:
        why = []
        if r["setups"] < a.min_setups:
            why.append(f"only {r['setups']} setups")
        if r.get("shapes", 0) < a.min_shapes:
            why.append(f"produces the setup {r.get('shapes', 0):.1f} times a "
                       f"day -- not a coin this entry can work on")
        if r["wick"] > a.max_wick:
            why.append(f"{r['wick']:.0f}% of candles wick past the stop")
        # Speed, measured inside the move rather than across the quiet hours
        # between them: once one of our own setups filled, how long did this
        # coin actually take to travel the ten percent. A coin that needs half
        # a day to get there is not a slow winner, it is a position we are
        # holding through everything that can happen in half a day.
        mm = r.get("med_min") or 0
        if mm <= 0:
            why.append("never reached the target from one of our own fills")
        elif mm > a.max_minutes:
            why.append(f"takes {mm:.0f}m to reach the target "
                       f"({mm/15:.0f} candles) -- too slow in a trend")
        if why:
            dropped.append((r["sym"], "; ".join(why)))
        else:
            keep.append(r)

    # Two lists, two jobs. `keep` is what the book is willing to trade and it
    # earns every filter above. The watchlist is only what the scout should
    # LOOK at, and looking is nearly free -- a coin refused for a thin record
    # or a slow target still deserves an eye on it, because the book applies
    # the real tests again at the moment a plan appears. Filtering the scout's
    # eyesight with the book's judgement is what left it walking nine coins.
    # Ordered by whether the coin can carry the trade, not by its replayed
    # record. The scout walks the top of this list, so the order is what it
    # actually looks at -- and sorting it by an old win rate meant it spent
    # its time on coins that make the shape and then go nowhere. Three strong
    # setups in a row were refused for exactly that: candles 2.7 and 2.4 times
    # normal on coins that reach ten percent less than one time in ten.
    #
    # Movement first, then whether the move is straight, then how often the
    # shape appears at all. A coin still has to make the shape to be here.
    def _rank(r):
        # Shapes weighted by how hard they break, not counted raw. Across 583
        # coins the number of shapes and their force pull against each other
        # at -0.25: a coin that produces a great many of them is producing
        # weak ones, and a ranking that counts them raw rewards exactly the
        # noise this entry cannot trade.
        good = r.get("shapes", 0.0) * max(0.0, r.get("force", 0.0) - 1.0)
        # A shape is only worth anything on a coin that can finish the trade.
        # The target is ten percent, and this term used to be added on its own
        # -- so a symbol measured never to cover ten percent could still rank
        # inside the walked list on shape production alone, and several did:
        # tokenised equities making three or four shapes a day and covering the
        # target on none of them. They cannot win by measurement, and every
        # sweep spent on one is a sweep not spent on a coin that can.
        travels = min(1.0, r.get("reach", 0.0) / 10.0)
        return -(r.get("reach", 0.0) * (0.5 + r.get("smooth", 0.0))
                 + good * 6.0 * travels)
    watch = [r["sym"] for r in sorted(rows, key=_rank)
             if r.get("shapes", 0) >= 0.5 and r.get("typical", 0) >= 0.30]
    if a.min_lev > 0:
        pairs = cli.trading_pairs()
        before = len(watch)
        kept = []
        for sym in watch:
            try:
                cap = float(pairs.get(sym, {}).get("maxLeverage") or 0)
            except (TypeError, ValueError):
                cap = 0.0
            # Unreadable is refused, not assumed generous.
            if cap >= a.min_lev:
                kept.append(sym)
        watch = kept
        print(f"  {before - len(watch)} of {before} coins dropped: the exchange "
              f"caps them under {a.min_lev:g}x", flush=True)
    # The names alone were not enough. The scout walks a hundred and fifty
    # coins while only the short list carried measurements, so for most of
    # what it found the book was reading blind -- the two heaviest parts of
    # its judgement, how far the coin travels and how straight, came back
    # unknown and were dropped from the reckoning entirely.
    try:
        by = {r["sym"]: r for r in rows}
        full = {s: {"reach": by[s].get("reach", 0.0),
                    "smooth": by[s].get("smooth", 0.0),
                    "shapes": by[s].get("shapes", 0.0),
                    "force": by[s].get("force", 0.0)}
                for s in watch if s in by}
        write_atomic(BOT / "data" / "watch_measures.json", full)
        print(f"  measurements for {len(full)} watched coins -> "
              f"data/watch_measures.json", flush=True)
    except Exception as e:
        print(f"  could not write the measurements: {e}", flush=True)
    try:
        write_atomic(BOT / "data" / "watchlist.json", watch)
        print(f"\n  {len(watch)} coins worth looking at -> data/watchlist.json",
              flush=True)
    except Exception as e:
        print(f"  could not write the watchlist: {e}", flush=True)

    for r in sorted(keep, key=lambda r: -(r["hit"] * r["decided"]))[:a.top * 2]:
        try:
            r["spread"] = cli.spread_bps(r["sym"])
            r["lev"] = cli.max_leverage(r["sym"])
            r["minq"] = cli.min_qty(r["sym"])
        except Exception:
            r["spread"], r["lev"], r["minq"] = 99.0, 0, 0
    keep = [r for r in keep if r.get("spread", 99) <= a.max_spread]

    # Rank on what the strategy actually got: the hit rate, weighted by how
    # much evidence there is for it, and penalised for what the spread costs.
    for r in keep:
        # Movement first. A coin that covers ten percent inside a couple of
        # hours reaches the target from any clean entry; one that does not
        # cannot be rescued by a better setup, and every replayed win rate on
        # such a coin is a measurement of luck. The replayed record still
        # counts, but as a modifier on top of the ground rather than as the
        # ground itself.
        conf = r["decided"] / (r["decided"] + 8.0)     # thin records count less
        move = r.get("reach", 0.0)
        speed = 1.0
        if r.get("median_bars"):
            # eight bars to the target is quick, thirty-two is the whole window
            speed = max(0.4, min(1.6, 16.0 / r["median_bars"]))
        # Reaching the target matters; reaching it without thrashing matters
        # just as much, because a move that oscillates on the way visits the
        # stop first. A coin that travels far but chops is not a coin this
        # strategy can hold.
        smooth = r.get("smooth", 0.0)
        # Movement is the ground; producing the setup is the point. A coin that
        # covers ten percent all day and never breaks a level with force is a
        # coin the book will watch and never trade -- and that is what the
        # first version of this ranking handed it.
        strong = r.get("strong", 0.0)          # strong shapes per day
        force = r.get("force", 0.0)            # how hard they break, typically
        r["score"] = ((move * speed * (0.4 + 1.2 * smooth)) * 0.4
                      + strong * 30.0
                      + max(0.0, force - 1.0) * 12.0
                      + r["hit"] * 100 * conf * 0.3
                      - r.get("spread", 0) / 20.0)
    keep.sort(key=lambda r: -r["score"])

    print(f"\n  {'coin':<13}{'setups':>7}{'hit':>7}{'won':>5}{'lost':>6}"
          f"{'to tp':>7}{'reach':>7}{'line':>6}{'shapes':>8}{'force':>7}"
          f"{'wick':>7}{'lev':>5}{'score':>7}")
    print("  " + "-" * 82)
    for r in keep[:a.top]:
        print(f"  {r['sym']:<13}{r['setups']:>7}{100*r['hit']:>6.1f}%{r['won']:>5}"
              f"{r['lost']:>6}{r['med_min']:>6.0f}m"
              f"{r.get('reach', 0):>6.1f}%{r.get('smooth', 0):>6.2f}"
              f"{r.get('strong', 0):>8.1f}{r.get('force', 0):>7.1f}"
              f"{r['wick']:>6.0f}%{r.get('lev', 0):>5.0f}{r['score']:>7.1f}")

    out = [{k: (round(v, 4) if isinstance(v, float) else v)
            for k, v in r.items()} for r in keep[:a.top]]
    write_atomic(BOT / "data" / "boom.json", out)
    print(f"\n  {len(keep)} coins can carry the strategy -> {BOT}/data/boom.json")
    print(f"  {len(dropped)} dropped, {len(failed)} unreadable")
    for sym, why in dropped[:5]:
        print(f"    {sym}: {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
