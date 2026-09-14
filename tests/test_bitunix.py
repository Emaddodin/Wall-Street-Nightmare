"""
The exchange client, with no exchange.

Every request is intercepted, so the signature, the precision, the retry
policy and the error classification can be checked exactly -- including the
cases that only ever happen at the worst moment, like a reply that never
arrives for an order that was accepted.
"""
from __future__ import annotations

import hashlib
import json
import time
from unittest import mock

import pytest
import requests

from exchange.bitunix import BitunixClient, BitunixError, _usdt_volume


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


@pytest.fixture
def cli():
    return BitunixClient(api_key="KEY", api_secret="SECRET")


# ------------------------------------------------------------------ signing
def test_signature_matches_the_documented_scheme(cli):
    nonce, ts, query, body = "abc", "1700000000000", "id1uid200", '{"a":1}'
    digest = hashlib.sha256((nonce + ts + "KEY" + query + body).encode()).hexdigest()
    want = hashlib.sha256((digest + "SECRET").encode()).hexdigest()
    assert cli._sign(nonce, ts, query, body) == want


def test_query_string_is_sorted_key_value_with_no_separators(cli):
    assert cli._query_string({"uid": 200, "id": 1}) == "id1uid200"
    assert cli._query_string({}) == ""
    assert cli._query_string(None) == ""


def test_the_signed_body_is_the_body_that_is_sent(cli):
    """A body that differs from the signed string by one space is rejected."""
    seen = {}

    def capture(method, url, **kw):
        seen.update(kw)
        return FakeResponse({"code": "0", "data": {"orderId": "1"}})

    with mock.patch.object(cli._s, "request", side_effect=capture):
        cli.place_market_order("BTCUSDT", "BUY", "0.001",
                               position_mode="ONE_WAY")
    sent = seen["data"].decode()
    assert " " not in sent
    body = json.loads(sent)
    assert body["qty"] == "0.001" and body["orderType"] == "MARKET"
    # the signature covers exactly that string
    h = seen["headers"]
    assert cli._sign(h["nonce"], h["timestamp"], "", sent) == h["sign"]


def test_signing_without_keys_refuses_rather_than_sending():
    bare = BitunixClient(api_key="", api_secret="")
    with pytest.raises(BitunixError):
        bare.positions()


def test_no_key_is_ever_placed_in_a_query_string(cli):
    seen = {}

    def capture(method, url, **kw):
        seen.update(kw)
        return FakeResponse({"code": "0", "data": []})

    with mock.patch.object(cli._s, "request", side_effect=capture):
        cli.positions("BTCUSDT")
    assert "SECRET" not in json.dumps(seen, default=str)
    assert seen["headers"]["api-key"] == "KEY"


# ------------------------------------------------------------------ retries
def test_a_business_rejection_is_not_retried(cli):
    """An order the exchange refused is an answer, not a dropped connection."""
    calls = []

    def refuse(method, url, **kw):
        calls.append(1)
        return FakeResponse({"code": "10001", "msg": "insufficient margin"})

    with mock.patch.object(cli._s, "request", side_effect=refuse):
        with pytest.raises(BitunixError) as e:
            cli.place_market_order("BTCUSDT", "BUY", "1",
                                   position_mode="ONE_WAY")
    assert len(calls) == 1
    assert "10001" in str(e.value)


def test_a_transport_failure_is_retried_on_a_read(cli):
    calls = []

    def flaky(method, url, **kw):
        calls.append(1)
        if len(calls) < 3:
            raise requests.ConnectionError("reset by peer")
        return FakeResponse({"code": "0", "data": []})

    with mock.patch.object(cli._s, "request", side_effect=flaky), \
            mock.patch("time.sleep"):
        assert cli.positions() == []
    assert len(calls) == 3


def test_a_rate_limit_is_retried(cli):
    calls = []

    def limited(method, url, **kw):
        calls.append(1)
        if len(calls) == 1:
            return FakeResponse({}, status=429)
        return FakeResponse({"code": "0", "data": []})

    with mock.patch.object(cli._s, "request", side_effect=limited), \
            mock.patch("time.sleep"):
        cli.positions()
    assert len(calls) == 2


def test_giving_up_raises_rather_than_returning_nothing(cli):
    with mock.patch.object(cli._s, "request",
                           side_effect=requests.ConnectionError("gone")), \
            mock.patch("time.sleep"):
        with pytest.raises(RuntimeError):
            cli.positions()


def test_an_order_is_sent_once_even_when_the_reply_is_lost(cli):
    """The duplicate-position case: the order lands, the answer does not.

    A timeout on a POST does not mean the order was not executed. Retrying it
    blind is how one signal becomes two positions at fifty times leverage --
    and the account, not the book, is where the second one shows up.
    """
    sent = []

    def times_out(method, url, **kw):
        sent.append(json.loads(kw["data"].decode()))
        raise requests.ReadTimeout("timed out")

    with mock.patch.object(cli._s, "request", side_effect=times_out), \
            mock.patch("time.sleep"):
        with pytest.raises(RuntimeError):
            cli.place_market_order("BTCUSDT", "BUY", "1",
                                   client_id="tbt-1", position_mode="ONE_WAY")
    assert len(sent) == 1, (
        f"a lost reply resent the order {len(sent)} times; each one is a real "
        f"position")


def test_a_read_is_still_retried_after_the_write_rule(cli):
    """Guarding writes must not make the client fragile on reads."""
    calls = []

    def flaky(method, url, **kw):
        calls.append(1)
        if len(calls) < 2:
            raise requests.ReadTimeout("timed out")
        return FakeResponse({"code": "0", "data": [{"symbol": "X"}]})

    with mock.patch.object(cli._s, "request", side_effect=flaky), \
            mock.patch("time.sleep"):
        assert cli.positions() == [{"symbol": "X"}]
    assert len(calls) == 2


# ---------------------------------------------------------------- precision
@pytest.fixture
def sized(cli):
    cli._pairs_cache = {"AAAUSDT": {"basePrecision": 2, "quotePrecision": 5,
                                    "minTradeVolume": 0.1, "maxLeverage": 50}}
    cli._pairs_at = time.time()          # a freshly-read list: no refresh
    return cli


def test_quantity_is_floored_never_rounded_up(sized):
    assert sized.round_qty("AAAUSDT", 25.749) == pytest.approx(25.74)
    assert sized.round_qty("AAAUSDT", 25.741) == pytest.approx(25.74)
    assert sized.round_qty("AAAUSDT", 0.0001) == 0.0


def test_quantity_string_never_exceeds_the_declared_precision(sized):
    """Binary floats produce 25.740000000000002; the exchange rejects it."""
    for q in (25.74, 0.1 + 0.2, 1 / 3, 1e-9, 12345.6789):
        s = sized.fmt_qty("AAAUSDT", q)
        assert "e" not in s.lower()
        frac = s.split(".")[1] if "." in s else ""
        assert len(frac) <= 2, s


def test_a_whole_quantity_is_not_written_in_exponent_form(sized):
    assert sized.fmt_qty("AAAUSDT", 100.0) == "100"
    assert sized.fmt_qty("AAAUSDT", 1000000.0) == "1000000"


def test_price_is_formatted_to_the_quote_precision(sized):
    assert sized.fmt_price("AAAUSDT", 1.234567) == "1.23457"
    assert sized.fmt_price("AAAUSDT", 2.0) == "2.00000"


def test_an_unknown_symbol_does_not_silently_get_default_precision(sized):
    """A pair the cache has never seen must not be sized on a guess.

    basePrecision defaulted to 4 and quotePrecision to 2 for any symbol not in
    the cache -- so a newly listed coin, or one fetched while the cache was
    cold, would be sent with the wrong number of decimals and rejected, or
    worse, accepted at a size nobody chose.
    """
    with pytest.raises(BitunixError):
        sized.fmt_qty("NEVERHEARDOFUSDT", 1.23456789)
    with pytest.raises(BitunixError):
        sized.fmt_price("NEVERHEARDOFUSDT", 1.23456789)


# ------------------------------------------------------------------ payloads
def test_volume_fields_are_reconciled_against_price_not_trusted_by_name():
    """The field naming is inverted between endpoints; guessing loses a zero."""
    # quoteVol is the base amount here: 1000 coins at $50 = $50,000 turnover
    assert _usdt_volume({"quoteVol": 1000, "baseVol": 50000}, 50.0) == 50000
    assert _usdt_volume({"quoteVol": 50000, "baseVol": 1000}, 50.0) == 50000
    assert _usdt_volume({}, 50.0) == 0.0
    assert _usdt_volume({"quoteVol": "x"}, 50.0) == 0.0


def test_klines_come_back_oldest_first(cli):
    newest_first = [
        {"time": 3_000, "open": "3", "high": "3", "low": "3", "close": "3",
         "baseVol": "1", "quoteVol": "3"},
        {"time": 1_000, "open": "1", "high": "1", "low": "1", "close": "1",
         "baseVol": "1", "quoteVol": "1"},
        {"time": 2_000, "open": "2", "high": "2", "low": "2", "close": "2",
         "baseVol": "1", "quoteVol": "2"},
    ]
    with mock.patch.object(cli, "_request", return_value=newest_first):
        df = cli.klines("AAAUSDT")
    assert list(df.close) == [1.0, 2.0, 3.0]
    assert df.index.is_monotonic_increasing


def test_history_stops_rather_than_spinning_on_a_stuck_page(cli):
    """An endpoint that keeps returning the same page must not loop forever."""
    page = [{"time": 1_000_000, "open": "1", "high": "1", "low": "1",
             "close": "1", "baseVol": "1", "quoteVol": "1"}] * 200
    calls = []

    def same(*a, **kw):
        calls.append(1)
        return page

    with mock.patch.object(cli, "_request", side_effect=same), \
            mock.patch("time.sleep"):
        cli.history("AAAUSDT", bars=5000)
    assert len(calls) < 10


def test_spread_on_an_empty_book_is_not_a_usable_number(cli):
    """It returns infinity -- callers must treat that as 'unknown', not wide."""
    with mock.patch.object(cli, "depth", return_value={"bids": [], "asks": []}):
        assert cli.spread_bps("AAAUSDT") == float("inf")


def test_hedge_mode_adds_the_trade_side_and_one_way_does_not(cli):
    bodies = []

    def capture(method, url, **kw):
        bodies.append(json.loads(kw["data"].decode()))
        return FakeResponse({"code": "0", "data": {}})

    with mock.patch.object(cli._s, "request", side_effect=capture):
        cli.place_market_order("A", "BUY", "1", position_mode="HEDGE")
        cli.place_market_order("A", "BUY", "1", position_mode="ONE_WAY")
    assert bodies[0]["tradeSide"] == "OPEN"
    assert "tradeSide" not in bodies[1]


def test_a_reduce_only_order_never_carries_a_trade_side(cli):
    bodies = []

    def capture(method, url, **kw):
        bodies.append(json.loads(kw["data"].decode()))
        return FakeResponse({"code": "0", "data": {}})

    with mock.patch.object(cli._s, "request", side_effect=capture):
        cli.place_market_order("A", "SELL", "1", reduce_only=True,
                               position_mode="HEDGE")
    assert "tradeSide" not in bodies[0] and bodies[0]["reduceOnly"] is True


def test_stops_and_targets_ride_on_the_entry_order(cli):
    """A stop sent as a follow-up can fail; then a 50x position is naked."""
    bodies = []

    def capture(method, url, **kw):
        bodies.append(json.loads(kw["data"].decode()))
        return FakeResponse({"code": "0", "data": {}})

    with mock.patch.object(cli._s, "request", side_effect=capture):
        cli.place_market_order("A", "BUY", "1", tp_price="110", sl_price="99",
                               position_mode="ONE_WAY")
    b = bodies[0]
    assert b["tpPrice"] == "110" and b["slPrice"] == "99"
    assert b["tpOrderType"] == "MARKET" and b["slOrderType"] == "MARKET"
    assert b["slStopType"] == "MARK_PRICE"


def test_cancelling_with_no_ids_is_refused(cli):
    with pytest.raises(BitunixError):
        cli.cancel_orders("AAAUSDT")
