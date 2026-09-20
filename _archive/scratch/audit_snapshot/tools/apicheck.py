#!/usr/bin/env python3
"""
Confirm what this engine assumes about Bitunix, against the real Bitunix.

Everything in `exchange/bitunix.py` is tested against fakes built to match the
code's own documentation of the venue. That proves the engine is consistent
with what it believes; it cannot prove that what it believes is true. The
field names in particular are guesses that fail SILENTLY: read the fill
quantity from the wrong key and a filled order reports as unfilled, which
leaves a real position with nothing watching it and puts the next one on top.

This closes that gap without risking anything. It is READ-ONLY: it places no
order, cancels nothing, and changes no account setting. It reads the public
endpoints, then -- only if keys are present -- the private read endpoints, and
reports which of the engine's assumptions it could actually confirm.

    python3 tools/apicheck.py              # public only
    python3 tools/apicheck.py --private    # also the signed read endpoints
    python3 tools/apicheck.py --order-id 12345   # check one real past order

What it cannot check without you placing a trade yourself: the exact field
names on a FILLED order. Run it with --order-id for any order the account has
already made, by hand or otherwise, and it will tell you whether
`read_order()` understands it.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT))
try:
    from dotenv import load_dotenv
    load_dotenv(BOT / ".env")
except Exception:
    pass

from exchange.bitunix import BitunixClient  # noqa: E402

OK, BAD, MEH = "  ok  ", " FAIL ", "  ?   "
_fails: list[str] = []


def check(name: str, fn, needed=True):
    try:
        got = fn()
    except Exception as e:
        mark = BAD if needed else MEH
        if needed:
            _fails.append(name)
        print(f"[{mark}] {name}\n         {str(e)[:150]}")
        return None
    if got is None:
        if needed:
            _fails.append(name)
        print(f"[{BAD if needed else MEH}] {name} -- nothing came back")
        return None
    print(f"[{OK}] {name}: {got}")
    return got


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--private", action="store_true",
                    help="also read the signed endpoints (needs keys in .env)")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--order-id", default=None,
                    help="an order the account already has, to confirm the "
                         "fill fields are understood")
    a = ap.parse_args()
    cli = BitunixClient()

    print(f"\nchecking {cli.base_url} -- READ ONLY, nothing will be sent\n")
    print("-- public --------------------------------------------------------")
    pairs = check("trading_pairs", lambda: f"{len(cli.trading_pairs())} pairs")
    if pairs:
        p = cli.trading_pairs().get(a.symbol)
        if not p:
            print(f"[{BAD}] {a.symbol} is not in the pair list")
            _fails.append("symbol")
        else:
            for k in ("basePrecision", "quotePrecision", "minTradeVolume",
                      "maxLeverage"):
                if k in p:
                    print(f"[{OK}] {a.symbol}.{k} = {p[k]}")
                else:
                    print(f"[{BAD}] {a.symbol} has no {k!r} -- the engine "
                          f"sizes every order from this")
                    _fails.append(k)

    check("tickers", lambda: f"{len(cli.tickers())} symbols")
    tk = None
    try:
        tk = next(t for t in cli.tickers() if t["symbol"] == a.symbol)
    except Exception:
        pass
    if tk:
        for k in ("lastPrice", "markPrice"):
            if tk.get(k):
                print(f"[{OK}] {a.symbol}.{k} = {tk[k]}")
            else:
                print(f"[{BAD}] ticker has no {k!r} -- "
                      + ("entries are priced off this" if k == "lastPrice"
                         else "stops and targets trigger off this"))
                _fails.append(k)

    check("depth/spread_bps",
          lambda: f"{cli.spread_bps(a.symbol):.2f} bps")
    check("klines oldest-first", lambda: (
        "yes" if cli.klines(a.symbol, "15m", 5).index.is_monotonic_increasing
        else "NO -- every indicator here assumes oldest first"))

    if not a.private:
        print("\n(skipping the signed endpoints; pass --private to read them)")
        return _report()

    print("\n-- private (read only) -------------------------------------------")
    if not (os.getenv("BITUNIX_API_KEY") and os.getenv("BITUNIX_API_SECRET")):
        print(f"[{BAD}] no BITUNIX_API_KEY / BITUNIX_API_SECRET in the "
              f"environment")
        _fails.append("keys")
        return _report()

    acct = check("account", lambda: "read")
    if acct:
        d = cli.account()
        for k in ("available", "margin"):
            print(f"[{OK if k in d else BAD}] account.{k} = {d.get(k)!r}")
            if k not in d:
                _fails.append(f"account.{k}")
        print(f"[{OK}] equity() = {cli.equity():.4f}")
        print(f"[{OK}] position_mode() = {cli.position_mode()}")

    pos = check("positions", lambda: f"{len(cli.positions())} open")
    if pos is not None and cli.positions():
        row = cli.positions()[0]
        for k in ("symbol", "side", "qty", "positionId"):
            print(f"[{OK if k in row else BAD}] position.{k} = {row.get(k)!r}")
            if k not in row:
                _fails.append(f"position.{k}")
    else:
        print(f"[{MEH}] no open position -- the reconciler's field names "
              f"(symbol/side/qty/positionId) could not be confirmed")

    check("history_positions",
          lambda: f"{len(cli.history_positions(a.symbol))} closed",
          needed=False)
    rows = []
    try:
        rows = cli.history_positions(a.symbol) or cli.history_positions()
    except Exception:
        pass
    if rows:
        row = rows[0]
        for k in ("positionId", "realizedPNL", "closePrice", "ctime"):
            alt = "realisedPNL" if k == "realizedPNL" else None
            here = k in row or (alt and alt in row)
            print(f"[{OK if here else BAD}] closed_position.{k} = "
                  f"{row.get(k, row.get(alt)) if here else 'MISSING'!r}")
            if not here:
                _fails.append(f"closed.{k}")
    else:
        print(f"[{MEH}] no closed positions -- the settlement field names "
              f"(realizedPNL/closePrice/ctime) could not be confirmed")

    print("\n-- the fill fields, which fail silently if wrong ------------------")
    if a.order_id:
        raw = check(f"order_detail({a.order_id})", lambda: "read")
        if raw is not None:
            d = cli.order_detail(order_id=a.order_id)
            st = cli.read_order(d)
            print(f"       raw keys: {sorted(d)}")
            print(f"       read_order -> status={st['status']!r} "
                  f"filled={st['filled']} avg={st['avg_price']}")
            if st["status"] == "unknown":
                print(f"[{BAD}] the status field is not one this engine knows. "
                      f"Add its spelling to BitunixClient._STATUS_KEYS / the "
                      f"status map.")
                _fails.append("order.status")
            if st["filled"] == 0 and st["status"] == "filled":
                print(f"[{BAD}] a FILLED order reports zero filled quantity -- "
                      f"the quantity key is wrong. Add it to "
                      f"_FILLED_QTY_KEYS.")
                _fails.append("order.filled")
            if st["status"] == "filled" and not st["avg_price"]:
                print(f"[{BAD}] no average fill price found -- add its key to "
                      f"_AVG_PRICE_KEYS. The book would record the price it "
                      f"asked for instead of the one it got.")
                _fails.append("order.avg_price")
    else:
        pend = []
        try:
            pend = cli.pending_orders() or []
        except Exception as e:
            print(f"[{MEH}] pending_orders: {str(e)[:110]}")
        if pend:
            st = cli.read_order(pend[0])
            print(f"       a resting order reads as status={st['status']!r}")
            print(f"       raw keys: {sorted(pend[0])}")
        else:
            print(f"[{MEH}] nothing resting and no --order-id given, so the "
                  f"fill fields are still UNCONFIRMED.")
            print("       Re-run with --order-id <a past order> to close this "
                  "gap. It is the one assumption in this client that fails "
                  "quietly rather than loudly.")
    return _report()


def _report() -> int:
    print()
    if _fails:
        print(f"{len(_fails)} assumption(s) NOT confirmed: {', '.join(_fails)}")
        print("Do not trade live until these are understood.")
        return 1
    print("every assumption this checked was confirmed.")
    print("Note what it could NOT check is listed above as '?' -- those remain "
          "unverified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
