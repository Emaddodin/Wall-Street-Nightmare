"""SMT divergence (smt.py), the silver / dollar feeds (silver.py) and the broker plumbing for other symbols,
on synthetic candles (no network)."""
import json
import math
import random
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import silver  # noqa: E402
import smt  # noqa: E402
from engine import Bars  # noqa: E402

T0 = 1_790_000_000 - 1_790_000_000 % 3600
EPS_G, EPS_S = 0.1, 0.002


def path(knots, n):
    """Closes linearly through (index, price) knots, flat after the last one."""
    out = []
    for i in range(n):
        for (i0, p0), (i1, p1) in zip(knots, knots[1:]):
            if i0 <= i <= i1:
                out.append(p0 + (p1 - p0) * (i - i0) / (i1 - i0))
                break
        else:
            out.append(knots[-1][1])
    return out


def bars(closes, eps, sec=60, t0=T0):
    b = Bars(sec)
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev
        b.append(t0 + i * sec, o, max(o, c) + eps, min(o, c) - eps, c)
        prev = c
    return b


def head(b, k):
    """The first k candles."""
    out = Bars(b.sec)
    out.t, out.o, out.h, out.l, out.c = b.t[:k], b.o[:k], b.h[:k], b.l[:k], b.c[:k]
    out.v, out.spread = b.v[:k], b.spread[:k]
    return out


N = 60
GOLD_LL = [(0, 2405), (10, 2400), (20, 2410), (30, 2395), (40, 2412), (59, 2406)]   # LL at 30, HH at 40


def silver_from(knots):
    return bars(path(knots, N), EPS_S)


# ------------------------------------------------------------------ confirmed SMT
def test_both_lower_lows_no_smt():
    g = bars(path(GOLD_LL, N), EPS_G)
    s = silver_from([(0, 30.3), (10, 30.0), (20, 30.5), (30, 29.9), (40, 30.6), (59, 30.4)])
    assert smt.divergences(g, s) == []
    assert smt.live_smt(g, s) is None


def test_bullish_gold_lower_low_silver_higher_low():
    g = bars(path(GOLD_LL, N), EPS_G)
    s = silver_from([(0, 30.3), (10, 30.0), (20, 30.5), (30, 30.1), (40, 30.6), (59, 30.4)])
    ev = smt.divergences(g, s)
    assert len(ev) == 1
    e = ev[0]
    assert e["dir"] == 1 and e["kind"] == "bullish" and e["side"] == "low"
    assert e["a_time"] == g.t[10] and e["b_time"] == g.t[30] and e["time"] == g.t[33]
    assert e["gold"] == pytest.approx([2400 - EPS_G, 2395 - EPS_G])
    assert e["other"] == pytest.approx([30.0 - EPS_S, 30.1 - EPS_S])
    assert e["leader"] == "silver" and e["swept"] is True and e["by"] == "gold"
    line = smt.describe("M5", e)
    assert line.startswith("M5 bullish SMT: gold swept 2399.90 (lower low) while silver held its low")
    assert "silver leads" in line


def test_bullish_mirror_silver_lower_low_gold_holds():
    g = bars(path([(0, 2405), (10, 2400), (20, 2410), (30, 2403), (40, 2412), (59, 2406)], N), EPS_G)
    s = silver_from([(0, 30.3), (10, 30.0), (20, 30.5), (30, 29.8), (40, 30.6), (59, 30.4)])
    ev = smt.divergences(g, s)
    assert [(e["dir"], e["leader"], e["swept"], e["by"]) for e in ev] == [(1, "gold", False, "other")]
    assert ev[0]["gold"] == pytest.approx([2400 - EPS_G, 2403 - EPS_G])
    assert ev[0]["other"] == pytest.approx([30.0 - EPS_S, 29.8 - EPS_S])
    assert "gold leads" in smt.describe("M1", ev[0])


def test_bearish_gold_higher_high_silver_lower_high():
    g = bars(path([(0, 2405), (10, 2410), (20, 2400), (30, 2415), (40, 2402), (59, 2408)], N), EPS_G)
    s = silver_from([(0, 30.3), (10, 30.5), (20, 30.0), (30, 30.4), (40, 30.05), (59, 30.3)])
    ev = smt.divergences(g, s)
    assert len(ev) == 1
    e = ev[0]
    assert e["dir"] == -1 and e["kind"] == "bearish" and e["side"] == "high"
    assert e["a_time"] == g.t[10] and e["b_time"] == g.t[30] and e["time"] == g.t[33]
    assert e["gold"] == pytest.approx([2410 + EPS_G, 2415 + EPS_G])
    assert e["other"] == pytest.approx([30.5 + EPS_S, 30.4 + EPS_S])
    assert e["leader"] == "silver" and e["swept"] is True


def test_tolerance_ignores_tiny_breaks():
    g = bars(path([(0, 2405), (10, 2400), (20, 2410), (30, 2399.95), (40, 2412), (59, 2406)], N), EPS_G)
    s = silver_from([(0, 30.3), (10, 30.0), (20, 30.5), (30, 30.1), (40, 30.6), (59, 30.4)])
    assert len(smt.divergences(g, s)) == 1
    assert smt.divergences(g, s, tol=0.0001) == []          # 0.05 below a 2400 low is < 1 bp


def test_inverse_dollar():
    g = bars(path(GOLD_LL, N), EPS_G)
    # the dollar's highs pair with gold's lows: a lower high at 30 = it failed to confirm gold's lower low
    d = bars(path([(0, 103.8), (10, 104.0), (20, 103.5), (30, 103.9), (40, 103.4), (59, 103.6)], N), 0.005)
    ev = smt.divergences(g, d, inverse=True, name="dxy")
    assert len(ev) == 1
    e = ev[0]
    assert e["dir"] == 1 and e["side"] == "low" and e["inverse"] is True and e["leader"] == "dxy"
    assert e["other"] == pytest.approx([104.005, 103.905])  # the dollar's highs
    assert "vs dxy" in smt.describe("M5", e) and "the dollar failed to make the matching higher high" in \
        smt.describe("M5", e)
    # the dollar confirms (higher high while gold makes the lower low): nothing
    d2 = bars(path([(0, 103.8), (10, 104.0), (20, 103.5), (30, 104.2), (40, 103.4), (59, 103.6)], N), 0.005)
    assert smt.divergences(g, d2, inverse=True) == []
    # mirror: the dollar makes a higher high while gold holds a higher low -> bullish, gold leads
    g3 = bars(path([(0, 2405), (10, 2400), (20, 2410), (30, 2403), (40, 2412), (59, 2406)], N), EPS_G)
    ev3 = smt.divergences(g3, d2, inverse=True, name="dxy")
    assert [(e["dir"], e["leader"], e["swept"]) for e in ev3] == [(1, "gold", False)]
    # without inverse, the same dollar series is a mess of "divergences": inverse matters
    assert smt.divergences(g, d) != ev


# ------------------------------------------------------------------ forming
FORM_N = 45
GOLD_FORM = [(0, 2405), (10, 2400), (25, 2410), (40, 2401), (43, 2397), (44, 2398)]


def test_forming_smt_on_last_candles():
    g = bars(path(GOLD_FORM, FORM_N), EPS_G)
    s = bars(path([(0, 30.3), (10, 30.0), (25, 30.6), (40, 30.2), (43, 30.1), (44, 30.12)], FORM_N), EPS_S)
    assert smt.divergences(g, s) == []                       # nothing confirmed yet
    e = smt.live_smt(g, s, k=5)
    assert e is not None and e["state"] == "forming"
    assert e["dir"] == 1 and e["leader"] == "silver" and e["swept"] is True
    assert e["a_time"] == g.t[10] and e["b_time"] == g.t[43] and e["time"] == g.t[44]
    assert e["gold"] == pytest.approx([2400 - EPS_G, 2397 - EPS_G])
    assert e["other"][0] == pytest.approx(30.0 - EPS_S) and e["other"][1] > e["other"][0]
    assert "forming bullish SMT: gold is sweeping 2399.90" in smt.describe("M1", e)
    # silver takes its low too: no divergence
    s2 = bars(path([(0, 30.3), (10, 30.0), (25, 30.6), (40, 30.2), (43, 29.95), (44, 30.0)], FORM_N), EPS_S)
    assert smt.live_smt(g, s2, k=5) is None
    # the sweep is older than the window: not "now"
    old = bars(path([(0, 2405), (10, 2400), (20, 2396), (30, 2398), (44, 2397)], FORM_N), EPS_G)
    assert smt.live_smt(old, s, k=5) is None


def test_forming_includes_the_forming_candle():
    closes = path(GOLD_FORM[:-2] + [(43, 2401.5), (44, 2397)], FORM_N)   # only the forming candle breaks
    g = bars(closes, EPS_G)
    s = bars(path([(0, 30.3), (10, 30.0), (25, 30.6), (40, 30.2), (44, 30.15)], FORM_N), EPS_S)
    e = smt.live_smt(g, s, k=3)
    assert e and e["b_time"] == g.t[44] and e["dir"] == 1


# ------------------------------------------------------------------ no repaint
def walk(n, seed, rho=0.85, gaps=False):
    rnd = random.Random(seed)
    gc, sc = [2400.0], [30.0]
    for _ in range(n - 1):
        z1 = rnd.gauss(0, 1)
        z2 = rho * z1 + math.sqrt(1 - rho * rho) * rnd.gauss(0, 1)
        gc.append(gc[-1] * math.exp(0.0006 * z1))
        sc.append(sc[-1] * math.exp(0.0009 * z2))
    g, s = Bars(60), Bars(60)
    for i in range(n):
        for b, cs, k in ((g, gc, 0.3), (s, sc, 0.004)):
            o = cs[i - 1] if i else cs[0]
            c = cs[i]
            w1, w2 = rnd.random() * k, rnd.random() * k
            if b is s and gaps and rnd.random() < 0.05:
                continue                                    # silver missing a minute now and then
            b.append(T0 + i * 60, o, max(o, c) + w1, min(o, c) - w2, c)
    return g, s


def test_no_repaint_prefix():
    g, s = walk(600, 7)
    full = smt.divergences(g, s, lookback=None)
    assert len(full) >= 4
    assert {e["dir"] for e in full} == {1, -1}
    for k in range(20, len(g) + 1, 7):
        part = smt.divergences(head(g, k), head(s, k), lookback=None)
        known = [e for e in full if e["time"] < g.t[k - 1]]     # the last candle of the prefix is forming
        assert part == known, k


def test_align_with_gaps():
    g, s = walk(300, 3, gaps=True)
    ga, sa = smt.align(g, s)
    assert len(ga) == len(sa) == len(s) < len(g)
    assert ga.t == sa.t and set(ga.t) <= set(g.t)
    i = g.t.index(ga.t[5])
    assert ga.h[5] == g.h[i] and sa.h[5] == s.h[s.t.index(ga.t[5])]
    # divergences align by themselves when the clocks differ
    assert smt.divergences(g, s, lookback=None) == smt.divergences(ga, sa, lookback=None)


# ------------------------------------------------------------------ correlation
def test_correlation():
    g, s = walk(400, 11, rho=0.9)
    c = smt.correlation(g, s, 300)
    assert c is not None and c > 0.75
    g2, s2 = walk(400, 12, rho=0.0)
    assert abs(smt.correlation(g2, s2, 300)) < 0.25
    neg = Bars(60)
    for i in range(len(s)):
        neg.append(s.t[i], 1 / s.o[i], 1 / s.l[i], 1 / s.h[i], 1 / s.c[i])
    assert smt.correlation(g, neg, 300) < -0.75
    assert smt.correlation(head(g, 10), head(s, 10)) is None
    flat = bars([30.0] * 100, 0.0)
    assert smt.correlation(head(g, 100), flat) is None


# ------------------------------------------------------------------ the reader
def test_reader_states_and_summary():
    g = bars(path(GOLD_LL, N), EPS_G)
    s = silver_from([(0, 30.3), (10, 30.0), (20, 30.5), (30, 30.1), (40, 30.6), (59, 30.4)])
    r = smt.SMTReader(recent=30, min_bars=40)
    x = r.update("M5", g, s)
    assert x["ok"] and x["tf"] == "M5" and x["state"] == 1 and x["latest"]["kind"] == "bullish"
    assert x["latest"]["age"] == 58 - 33 and x["latest"]["intact"]
    assert x["note"].startswith("M5 bullish SMT: gold swept 2399.90 (lower low) while silver held its low")
    assert len(x["events"]) == 1 and x["forming"] is None and x["lag"] == 0
    assert x["corr"] is not None and set(x) >= {"tf", "state", "latest", "forming", "events", "corr", "ok", "note"}
    stale = smt.SMTReader(recent=15).update("M5", g, s)     # known 25 candles ago: too old to count
    assert stale["state"] == 0 and stale["latest"] is None and "no SMT in the last 15 candles" in stale["note"]
    y = r.update("M1", head(g, 20), head(s, 20))
    assert not y["ok"] and y["state"] == 0 and "not enough" in y["note"]
    z = r.update("H1", g, None)
    assert not z["ok"] and "no silver candles" in z["note"]
    sm = r.summary()
    assert set(sm["tfs"]) == {"M1", "M5", "H1"} and sm["state"] == 1 and sm["score"] == 1.0
    assert sm["note"].startswith("Silver SMT: bullish (M5 bullish)")
    # gold trades below the SMT low afterwards: the event no longer counts
    g2 = bars(path(GOLD_LL[:-1] + [(50, 2412), (59, 2390)], N), EPS_G)
    w = smt.SMTReader(recent=40).update("M5", g2, s)
    assert w["events"] and not w["events"][0]["intact"] and w["state"] == 0


def test_reader_never_raises():
    r = smt.SMTReader()
    bad = Bars(60)
    bad.t, bad.o, bad.h, bad.l, bad.c = [1, 2], [1.0], [1.0], [1.0], [1.0]     # broken lengths
    x = r.update("M1", bad, bad)
    assert x["state"] == 0 and x["ok"] is False


def test_reader_correlation_broken_is_flagged():
    g, _ = walk(200, 21)
    _, s = walk(200, 22, rho=0.0)
    other = Bars(60)
    for i in range(len(s)):
        other.append(g.t[i], s.o[i], s.h[i], s.l[i], s.c[i])
    x = smt.SMTReader().update("M1", g, other)
    assert x["ok"] and x["corr"] is not None and not x["corr_ok"]
    assert "correlation broken" in x["note"]


# ------------------------------------------------------------------ silver feed
class NoRatesOf:
    symbol = "XAUUSD"


def _yahoo_blocked(url):
    raise OSError("Tunnel connection failed: 403 Forbidden")


def test_feed_unavailable_without_broker_and_yahoo(monkeypatch):
    monkeypatch.setattr(silver, "_get_json", _yahoo_blocked)
    f = silver.SilverFeed(NoRatesOf(), block=True)
    assert f.rates("M5", 120) is None
    assert f.status.startswith("unavailable") and "Yahoo SI=F" in f.status and "403" in f.status
    assert f.info()["source"] is None
    d = silver.DollarFeed(None, block=True)
    assert d.rates("M1", 50) is None and d.status.startswith("unavailable") and "DX-Y.NYB" in d.status


def _yahoo_chart(start, n, base=30.0):
    ts = [start + i * 60 for i in range(n)]
    cl = [base + 0.01 * math.sin(i / 7) for i in range(n)]
    q = {"open": cl, "high": [c + 0.005 for c in cl], "low": [c - 0.005 for c in cl], "close": cl,
         "volume": [1] * n}
    q["close"] = list(q["close"])
    q["close"][3] = None                            # Yahoo leaves holes; they're skipped
    return {"chart": {"result": [{"timestamp": ts, "indicators": {"quote": [q]}}]}}


def test_feed_falls_back_to_yahoo_on_candle_clock(monkeypatch):
    start = int(time.time()) // 3600 * 3600 - 3 * 3600
    calls = []

    def fake(url):
        calls.append(url)
        return _yahoo_chart(start, 150)
    monkeypatch.setattr(silver, "_get_json", fake)

    class Src:
        symbol = "XAUUSD"

        def rates_of(self, sym, tf, count):
            raise LookupError(f"no {sym}")
    f = silver.SilverFeed(Src(), to_candle_clock=lambda t: t + 3 * 3600, block=True)
    b = f.rates("M5", 20)
    assert b is not None and len(b) == 20 and b.sec == 300
    assert all(t % 300 == 0 for t in b.t) and b.t[-1] == (start + 149 * 60 + 3 * 3600) // 300 * 300
    assert f.source == "yahoo" and f.status.startswith("Yahoo SI=F (delayed") and f.info()["delayed"]
    m1 = f.rates("M1", 500)
    assert len(m1) == 149                           # one bar had no close
    n = len(calls)
    f.rates("M5", 20)                               # cached for a few seconds: no new read
    assert len(calls) == n


def test_feed_yahoo_reads_in_background(monkeypatch):
    import threading
    gate = threading.Event()
    start = int(time.time()) // 60 * 60 - 100 * 60

    def slow(url):
        gate.wait(5)
        return _yahoo_chart(start, 100)
    monkeypatch.setattr(silver, "_get_json", slow)
    f = silver.SilverFeed(NoRatesOf(), cache_sec=0)
    t0 = time.time()
    assert f.rates("M1", 50) is None and time.time() - t0 < 1.0      # the poll never waits on Yahoo
    assert "reading" in f.status
    gate.set()
    for _ in range(100):
        if not f.yahoo._busy:
            break
        time.sleep(0.02)
    b = f.rates("M1", 50)
    assert b is not None and len(b) == 50 and f.source == "yahoo" and not f.info()["delayed"]
    assert f.status == "Yahoo SI=F"


def test_feed_finds_broker_symbol_once(monkeypatch):
    monkeypatch.setattr(silver, "_get_json", _yahoo_blocked)
    tried = []

    class Src:
        symbol = "XAUUSDm"

        def rates_of(self, sym, tf, count):
            tried.append(sym)
            if sym != "XAGUSDm":
                raise LookupError(sym)
            return bars([30.0 + i * 0.01 for i in range(count)], 0.001, sec=silver.TF_SECONDS[tf])
    f = silver.SilverFeed(Src(), cache_sec=0)
    assert tried == [] and f.cands[0] == "XAGUSDm"
    b = f.rates("M1", 50)
    assert len(b) == 50 and f.status == "broker XAGUSDm" and f.symbol == "XAGUSDm"
    assert tried == ["XAGUSDm"]
    f.rates("M5", 50)
    assert tried == ["XAGUSDm", "XAGUSDm"]


def test_feed_old_ea_and_source_hint(monkeypatch):
    monkeypatch.setattr(silver, "_get_json", _yahoo_blocked)
    n = []

    class Old:
        symbol = "XAUUSD"

        def rates_of(self, sym, tf, count):
            n.append(sym)
            raise NotImplementedError("older EA")
    f = silver.SilverFeed(Old(), cache_sec=0)
    assert f.rates("M1", 10) is None and "older EA" in f.status
    f.rates("M1", 10)
    assert n == ["XAGUSD"]                          # not asked again for a while

    class LF:
        symbol = "XAUUSD"
        silver_symbols = ("XAGUSD",)
        dollar_symbols = ()

        def rates_of(self, sym, tf, count):
            raise AssertionError("must not be asked")
    assert silver.SilverFeed(LF()).cands == ["XAGUSD"]
    d = silver.DollarFeed(LF())
    assert d.rates("M1", 10) is None and d.status.startswith("unavailable")


# ------------------------------------------------------------------ broker plumbing
def test_mt5_bridge_rates_of_checks_the_echoed_symbol():
    import mt5bridge
    src = object.__new__(mt5bridge.MT5BridgeSource)
    src._of, src._mtime = {}, time.time()
    import threading
    src._of_lock = threading.Lock()
    src.st = {"spec": {"point": 0.01}}
    asked = []
    replies = {}

    def ask(fields, take=0, run=0):
        asked.append(fields)
        return replies["r"]
    src._ask = ask
    body = "".join(f"{T0 + i * 60},30.1,30.2,30.0,30.15,5,20\n" for i in range(3))
    replies["r"] = json.dumps({"ok": True, "n": 3}) + "\n" + body             # an old EA: gold, no "sym"
    with pytest.raises(NotImplementedError):
        src.rates_of("XAGUSD", "M1", 3)
    assert asked[-1]["symbol"] == "XAGUSD" and asked[-1]["op"] == "bars"
    replies["r"] = json.dumps({"ok": False, "nosym": True, "message": "No XAGUSDx in this MT5"}) + "\n"
    with pytest.raises(LookupError):
        src.rates_of("XAGUSDx", "M1", 3)
    replies["r"] = json.dumps({"ok": True, "n": 3, "sym": "XAGUSD", "point": 0.001, "digits": 3}) + "\n" + body
    b = src.rates_of("XAGUSD", "M5", 3)
    assert len(b) == 3 and b.sec == 300 and b.c[-1] == 30.15 and b.spread[0] == pytest.approx(0.02)
    k = len(asked)
    src.rates_of("XAGUSD", "M5", 3)                 # reused for a moment
    assert len(asked) == k
    with pytest.raises(LookupError):
        src.rates_of("XAG\nop=market", "M1", 3)      # never a line break into a request


def test_litefinance_rates_of_keeps_gold_cache_apart():
    import threading
    import litefinance as lf
    src = object.__new__(lf.LiteFinanceSource)
    src.symbol = "XAUUSD"
    src.cache = {tf: [] for tf in lf.RES}
    src._fresh_at, src.cache_of, src._fresh_of = {}, {}, {}
    src.sym_lock, src.data_lock = threading.Lock(), threading.Lock()
    src._hold_until, src._quote_at = 0.0, 0.0
    got = []
    now = int(time.time()) // 60 * 60

    def fetch(tf, t_from, t_to, symbol=None):
        got.append(symbol)
        if symbol == "NOPE":
            return []
        return [[now - (5 - i) * 60, 30.0, 30.1, 29.9, 30.05, 1.0] for i in range(6) if t_from <= now - (5 - i) * 60 < t_to]
    src._fetch = fetch
    b = src.rates_of("XAGUSD", "M1", 6)
    assert len(b) == 6 and b.c[-1] == 30.05 and set(got) == {"XAGUSD"}
    assert src.cache["M1"] == [] and src._fresh_at == {} and src.cache_of["XAGUSD"]["M1"]
    with pytest.raises(LookupError):
        src.rates_of("NOPE", "M1", 6)
    assert got.count("NOPE") == 2                    # two empty walks back, no more
