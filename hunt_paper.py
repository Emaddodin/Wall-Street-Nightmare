#!/usr/bin/env python3
"""
Paper-trade the 15m hunt, live.

This does not average anything. It sits on the coins that are awake right now,
watches for an impulse to pull back into its Fibonacci zone, checks what that
coin does in this state -- not in its whole history -- and only then takes the
trade. Everything is recorded so the record can be argued with later.

  data/hunt.json     the book
  data/hunt.jsonl    every candidate ever considered, taken or refused

Nothing here places a real order.
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
_BOT = str(__import__("pathlib").Path(__file__).resolve().parent)

sys.path.insert(0, _BOT)
BOT = _BOT
BOOK = f"{_BOT}/data/hunt.json"
LOG = f"{_BOT}/data/hunt.jsonl"
TEH = timezone(timedelta(hours=3, minutes=30))
log = logging.getLogger("hunt")


def ping(title, body):
    for k in ("NTFY_TOPIC", "NTFY_TOPIC_SHARED"):
        for t in [x.strip() for x in os.getenv(k, "").split(",") if x.strip()]:
            try:
                urllib.request.urlopen(urllib.request.Request(
                    f"https://ntfy.sh/{urllib.parse.quote(t)}",
                    data=body.encode(),
                    headers={"Title": title.encode("utf-8").decode(
                        "latin-1", "replace"), "Priority": "high"}),
                    timeout=10).close()
            except Exception:
                pass


def scan_running() -> bool:
    return subprocess.run(["pgrep", "-f", "boom2.py"],
                          capture_output=True).returncode == 0


def kick_scan(why: str) -> bool:
    """Start the coin search now, off the back of a trade rather than a clock.

    The search takes about seven minutes. Firing it when a trade closes -- and
    again when one is nearly done -- means the list is fresh at the only moment
    it matters, which is when a slot opens. A timer cannot know that.
    """
    if scan_running():
        return False
    try:
        subprocess.Popen(
            ["/home/tbt/venv/bin/python", "-u", f"{_BOT}/boom2.py",
             "--workers", "12", "--top", "14"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, cwd=BOT)
        log.info("searching for coins -- %s", why)
        return True
    except Exception as e:
        log.warning("could not start the search: %s", str(e)[:70])
        return False


def load():
    try:
        return json.load(open(BOOK))
    except Exception:
        return {"equity": 100.0, "start": 100.0, "trades": [], "seen": []}


def save(st):
    tmp = BOOK + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, BOOK)


def note(rec):
    with open(LOG, "a") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tp", type=float, default=10.0)
    ap.add_argument("--sl", type=float, default=2.0)
    ap.add_argument("--frac", type=float, default=0.10)
    ap.add_argument("--lev", type=float, default=50.0)
    ap.add_argument("--max-day", type=int, default=3,
                    help="how many trades a day this is allowed to take")
    ap.add_argument("--min-hit", type=float, default=17.7)
    ap.add_argument("--min-past", type=int, default=8,
                    help="refuse a setup the coin has not shown this many times "
                         "while awake -- no history is not a good history")
    ap.add_argument("--max-wick", type=float, default=25.0,
                    help="refuse a coin whose candles wick past the stop more "
                         "often than this, in percent")
    ap.add_argument("--every", type=float, default=120.0)
    ap.add_argument("--rescan", type=float, default=7200.0,
                    help="fallback only. The real trigger is a trade ending; "
                         "this just stops the list going stale on a quiet day.")
    ap.add_argument("--near", type=float, default=0.7,
                    help="start searching once an open trade has travelled this "
                         "much of the way to its target or its stop, so a fresh "
                         "list is ready the moment the slot frees up")
    ap.add_argument("--hold-hours", type=float, default=6.0,
                    help="a coin, once picked, is kept at least this long. A "
                         "trend takes hours to form and the scan runs every "
                         "half hour; without this the list churns underneath "
                         "the setup we are waiting for.")
    ap.add_argument("--drop-rank", type=int, default=20,
                    help="only let go of a held coin once it has fallen this "
                         "far down the ranking, not the moment it leaves the "
                         "top of it -- the gap is what stops the flapping")
    ap.add_argument("--watch", type=int, default=10,
                    help="how many coins to follow at once")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    from dotenv import load_dotenv
    load_dotenv(f"{_BOT}/.env")
    from exchange.bitunix import BitunixClient
    import hunt

    cli = BitunixClient()
    st = load()
    seen = set(tuple(x) for x in st.get("seen", []))
    log.info("hunt book $%.2f, %d trades on file", st["equity"], len(st["trades"]))
    log.info("+%.1f%% / -%.1f%%, %.0f%% of the wallet at %.0fx, max %d a day",
             a.tp, a.sl, a.frac * 100, a.lev, a.max_day)

    # What we are following, and since when. Kept in the book so a restart does
    # not reshuffle the list and lose a setup that was half formed.
    held = {k: float(v) for k, v in (st.get("held") or {}).items()}
    coins, last_scan = list(held), 0.0
    if coins:
        log.info("resuming on %s", ", ".join(c.replace("USDT", "") for c in coins))

    while True:
        try:
            if time.time() - last_scan > a.rescan or not coins:
                last_scan = time.time()
                try:
                    ranked = [r["sym"] for r in json.load(open(_BOT + "/data/boom.json"))]
                except Exception as e:
                    log.warning("no coin list: %s", str(e)[:60])
                    ranked = []
                if ranked:
                    now = time.time()
                    openn = {t["sym"] for t in st["trades"] if not t.get("closed")}
                    keep, letgo = {}, []
                    for sym, since in held.items():
                        try:
                            rank = ranked.index(sym)
                        except ValueError:
                            rank = 10 ** 6
                        young = (now - since) < a.hold_hours * 3600
                        # Three reasons to stay: we are in it, we only just
                        # picked it, or it is still respectable. Anything else
                        # and the slot is better used elsewhere.
                        if sym in openn:
                            keep[sym] = since
                        elif young:
                            keep[sym] = since
                        elif rank < a.drop_rank:
                            keep[sym] = since
                        else:
                            letgo.append(sym)
                    for sym in ranked:
                        if len(keep) >= a.watch:
                            break
                        keep.setdefault(sym, now)
                    added = [c for c in keep if c not in held]
                    held = keep
                    coins = list(held)
                    st["held"] = {k: v for k, v in held.items()}
                    save(st)
                    if added or letgo:
                        log.info("list: +%s  -%s",
                                 ",".join(c.replace("USDT", "") for c in added) or "none",
                                 ",".join(c.replace("USDT", "") for c in letgo) or "none")
                    log.info("watching %s", ", ".join(
                        f"{c.replace('USDT','')}"
                        f"{'*' if c in openn else ''}" for c in coins))

            # ---- mark and close what is open
            px = {}
            try:
                px = {t["symbol"]: float(t["lastPrice"]) for t in cli.tickers()}
            except Exception as e:
                log.warning("prices: %s", str(e)[:60])
            for tr in st["trades"]:
                if tr.get("closed") or tr["sym"] not in px:
                    continue
                p, long = px[tr["sym"]], tr["side"] == "BUY"
                hit_tp = p >= tr["tp"] if long else p <= tr["tp"]
                hit_sl = p <= tr["sl"] if long else p >= tr["sl"]
                if not (hit_tp or hit_sl):
                    # Not finished, but how close? Starting the search while a
                    # trade is winding down means the list is ready when it
                    # ends rather than seven minutes after.
                    if not tr.get("searched"):
                        span_tp = abs(tr["tp"] - tr["entry"])
                        span_sl = abs(tr["entry"] - tr["sl"])
                        gone = abs(p - tr["entry"])
                        toward = (gone / span_tp if
                                  ((p > tr["entry"]) == long) else gone / span_sl)
                        if toward >= a.near:
                            tr["searched"] = True
                            save(st)
                            kick_scan(f"{tr['sym']} is {toward*100:.0f}% of the "
                                      f"way to a decision")
                    continue
                tr["exit"] = tr["tp"] if hit_tp else tr["sl"]
                tr["reason"] = "target" if hit_tp else "stop"
                move = (tr["exit"] / tr["entry"] - 1) * (1 if long else -1)
                tr["pnl"] = tr["notional"] * move - tr["notional"] * 12.0 / 1e4
                tr["closed"] = time.time()
                st["equity"] += tr["pnl"]
                log.info("%s %s %s  %+.2f%%  $%+.2f  equity $%.2f",
                         tr["reason"].upper(), tr["side"], tr["sym"],
                         move * 100, tr["pnl"], st["equity"])
                kick_scan(f"{tr['sym']} just closed")
                last_scan = 0.0        # take the new list as soon as it lands
                ping(f"{tr['sym']} {tr['reason'].upper()}",
                     f"{tr['pnl']:+,.2f}\n${st['equity']:,.2f}")
                save(st)

            # ---- look for something to take
            today = datetime.now(TEH).date().isoformat()
            took = sum(1 for t in st["trades"]
                       if datetime.fromtimestamp(t["opened"], TEH).date()
                       .isoformat() == today)
            openn = [t for t in st["trades"] if not t.get("closed")]
            if took < a.max_day and not openn:
                for sym in coins:
                    try:
                        m, h4 = hunt.frame(cli, sym)
                        if m is None:
                            continue
                        trend = hunt.trend_of(m, h4)
                        cands = [s for s in hunt.setups(m, trend)
                                 if s["leg_end"] >= len(m) - hunt.WAIT - 1]
                    except Exception as e:
                        log.warning("%s: %s", sym, str(e)[:60])
                        continue
                    if not cands:
                        continue
                    s = max(cands, key=lambda c: c["move"])
                    key = (sym, int(m.index[s["leg_end"]].timestamp()),
                           bool(s["long"]))
                    if key in seen:
                        continue
                    hist = hunt.history_says(m, trend, s)
                    wick = hunt.wick_risk(m, s)
                    now = px.get(sym) or float(m.close.iloc[-1])
                    why = []
                    if not hist or hist["n"] < a.min_past:
                        why.append(f"only {hist['n'] if hist else 0} like it while awake")
                    elif hist["hit"] * 100 < a.min_hit:
                        why.append(f"its record here is {hist['hit']*100:.0f}%")
                    if not s["withtrend"]:
                        why.append("against the 4h trend")
                    if wick["over_stop"] > a.max_wick:
                        why.append(f"{wick['over_stop']:.0f}% of candles wick past the stop")
                    reached = ((now <= s["want"]) if s["long"]
                               else (now >= s["want"]))
                    if not reached:
                        why.append("price has not come back to the entry yet")
                    note(dict(t=int(time.time()), sym=sym, long=s["long"],
                              move=s["move"], entry=s["want"], now=now,
                              hist=hist, wick=wick, refused=why))
                    if why:
                        log.info("pass  %s %s %.1f%% -- %s", sym,
                                 "long" if s["long"] else "short", s["move"],
                                 "; ".join(why))
                        seen.add(key)
                        continue
                    margin = st["equity"] * a.frac
                    notional = margin * a.lev
                    e = s["want"]
                    tr = dict(sym=sym, side="BUY" if s["long"] else "SELL",
                              entry=e, qty=notional / e,
                              tp=e * (1 + a.tp / 100) if s["long"]
                                 else e * (1 - a.tp / 100),
                              sl=e * (1 - a.sl / 100) if s["long"]
                                 else e * (1 + a.sl / 100),
                              opened=time.time(), margin=margin,
                              notional=notional, move=s["move"],
                              hist_n=hist["n"], hist_hit=hist["hit"],
                              wick=wick["over_stop"], closed=None)
                    st["trades"].append(tr)
                    seen.add(key)
                    st["seen"] = [list(k) for k in seen][-4000:]
                    save(st)
                    log.info("TAKE  %s %s  leg %.1f%%  entry %.8g  "
                             "record %d/%d  margin $%.2f", sym, tr["side"],
                             s["move"], e, int(hist["hit"] * hist["n"]),
                             hist["n"], margin)
                    ping(f"{sym} {tr['side']}",
                         f"entry {e:.8g}\ntarget {tr['tp']:.8g}\n"
                         f"stop {tr['sl']:.8g}\n${st['equity']:,.2f}")
                    break
        except Exception as e:
            log.error("loop: %s", str(e)[:150])
        time.sleep(a.every)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
