# Tests

    python3 -m pytest tests/ -q

212 tests, about twelve seconds, no network and no exchange. Two autouse fixtures
in `conftest.py` enforce that: the Bitunix credentials are removed from the
environment (importing `papertrade` calls `load_dotenv`, so they would
otherwise be present) and `socket.connect` is made to raise. One test was
already reaching the live API before those were added.

## What is real and what is faked

Only the two edges are replaced. Every test that opens a position has run the
actual `papertrade.main()` loop -- the actual argument parsing, shape
detection, confidence scoring, risk checks, resting-order fills, exit handling
and book write.

| | |
|---|---|
| `fakes.py` | the exchange and one chart window, both able to time out, reject, freeze, duplicate and lie |
| `market.py` | candle generators whose output the engine's OWN shape functions recognise |
| `harness.py` | drives `papertrade.main()` for a fixed number of polls on a clock the test owns |
| `replay.py` | the 13,500 real recorded candles in `data/council_history.jsonl` |

Nothing declares that a trade should happen. `market.py` builds a band and a
breaking candle, and the test asserts that `breakout()` returns one -- if the
shape logic changes so that it no longer does, the test fails rather than
quietly testing nothing.

## Layout

| file | what it covers |
|---|---|
| `test_shapes.py` | the two shapes both ways, rejections, and that no measure can see past its own bar |
| `test_confidence.py` | the 70 floor, and what a missing measurement does to it |
| `test_geometry.py` | leverage, the liquidation line, the stop ceiling, fees |
| `test_bitunix.py` | signing, precision, retry policy, error classification |
| `test_lifecycle.py` | whole trades: long, short, target, stop, gap, miss, caps, clocks |
| `test_failures.py` | outages, dead charts, frozen feeds, duplicate and malformed events |
| `test_persistence.py` | restarts, locks, corrupt books, key pruning |
| `test_live.py` | `--live`: what it refuses, what it sends, and reconciliation |
| `test_scout.py` | the scout's readings and the file it hands the book |
| `test_guard.py` | the watchdog, including the frozen-chart check it did not have |
| `test_edges.py` | position-size extremes, rapid fire, spread, marks, reconnects |
| `test_replay.py` | the engine's own logic over 45 coins of real recorded market |
| `test_soak.py` | randomised hostile scenarios, checking invariants after each |
| `test_risk_controls.py` | the daily loss breaker, spread freshness, the frozen-chart alarm, real fills |

## Writing another one

Use `flags(**overrides)` from `test_lifecycle` to change a setting -- never
slice the argv list. `quiet_window()` gives a chart with no shape on it, and
`add_break()` prints one while the book is watching. That order matters: a
shape already present when the process starts is deliberately absorbed as
backlog and never traded, which is also the only way it happens in life.


## The live entry path

`--source break` and `--source council` rest a **real limit order at the plan's
own level**, with the stop and target attached to the same request. That is not
a convenience: the shape's tight stop is only defensible because the entry IS
the level, and a limit order can never fill worse than its price. Watching the
ticker and firing a market order on the touch would give away exactly the
difference the shape exists to capture.

`test_live.py` covers the whole lifecycle against a fake that keeps a real
order book -- orders rest, fill when price reaches them, fill in parts, and
refuse a post-only that would cross:

* the order is a LIMIT at the level, post-only, with tp and sl attached
* the position is booked from the exchange's fill, not the price asked for
* a partial fill is booked at the size that exists, and the remainder cancelled
* an unfilled order is cancelled when its window passes
* a fill that races the cancel is still booked
* price already at the level falls back to a plain limit, still at the level
* a market order is never used for a shape
* an order left resting by an earlier run is adopted and cancelled

## Verifying the exchange contract

Everything above tests the engine against fakes built to match the code's own
documentation of Bitunix. That cannot prove the documentation is right.
`tools/apicheck.py` is read-only and checks the real venue:

    python3 tools/apicheck.py --private --order-id <a past order>

The fill fields are the ones that fail silently if wrong, so pass `--order-id`
for any order the account has already made.
