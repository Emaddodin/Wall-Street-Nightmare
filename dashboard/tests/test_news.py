"""The news box (news.py): feed parsing, de-duplication, the gold reading of headlines and calendar events, the bias,
the WAIT window, the LLM fallback and state() under failure. Fixture strings only, no network."""
import importlib.util
import json
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import news  # noqa: E402

NOW = int(datetime(2026, 10, 14, 11, 48, tzinfo=timezone.utc).timestamp())       # 07:48 New York (EDT)
CPI_T = int(datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc).timestamp())     # 08:30 New York
STATE_KEYS = {"status", "updated", "wait", "wait_text", "next_event", "events", "headlines", "bias", "bias_text",
              "summary", "analysis", "llm"}
EVENT_KEYS = {"title", "country", "impact", "time_utc", "in_min", "forecast", "previous", "actual", "gold_effect"}
HEADLINE_KEYS = {"title", "source", "time_utc", "age_min", "url", "importance", "impact", "tags", "why"}


def rfc(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")


def iso(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat().replace("+00:00", "Z")


GOOGLE_RSS = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel><title>"gold price" - Google News</title>
<item><title>Fed's Waller says no rush to cut rates - Reuters</title><link>https://news.google.com/rss/articles/a1</link>
  <guid isPermaLink="false">a1</guid><pubDate>{rfc(NOW - 25 * 60)}</pubDate>
  <description>&lt;a href="x"&gt;Fed's Waller&lt;/a&gt;&amp;nbsp;</description>
  <source url="https://www.reuters.com">Reuters</source></item>
<item><title>Israel launches missile strikes on Iran&nbsp;as oil jumps - Bloomberg</title>
  <link>https://news.google.com/rss/articles/a2</link><pubDate>{rfc(NOW - 40 * 60)}</pubDate>
  <source url="https://www.bloomberg.com">Bloomberg</source></item>
<item><title>Gold price today in Chennai: check 22K and 24K gold rate - Times of India</title>
  <link>https://news.google.com/rss/articles/a3</link><pubDate>{rfc(NOW - 10 * 60)}</pubDate>
  <source url="https://timesofindia.com">Times of India</source></item>
<item><title>Fed hikes rates in surprise move - CNBC</title><link>https://news.google.com/rss/articles/a4</link>
  <pubDate>{rfc(NOW - 8 * 3600)}</pubDate><source url="https://www.cnbc.com">CNBC</source></item>
</channel></rss>""".encode()

FXSTREET_ATOM = f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>FXStreet News</title>
<entry><title>Fed's Waller: No rush to cut interest rates</title>
  <link rel="alternate" href="https://www.fxstreet.com/news/w"/>
  <updated>{iso(NOW - 20 * 60)}</updated></entry>
<entry><title type="html">Gold ETFs see outflows for third week as SPDR holdings fall</title>
  <link href="https://www.fxstreet.com/news/etf"/><published>{iso(NOW - 70 * 60)}</published></entry>
</feed>""".encode()

RDF_FEED = f"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/"
         xmlns:dc="http://purl.org/dc/elements/1.1/">
<item><title>Treasury yields jump as bond selloff deepens</title><link>https://example.com/y</link>
  <dc:date>{iso(NOW - 5 * 60)}</dc:date></item>
</rdf:RDF>""".encode()

FF_JSON = json.dumps([
    {"title": "CPI m/m", "country": "USD", "date": "2026-10-14T08:30:00-04:00", "impact": "High", "forecast": "0.3%",
     "previous": "0.4%"},
    {"title": "PPI m/m", "country": "USD", "date": "2026-10-14T06:00:00-04:00", "impact": "High", "forecast": "0.2%",
     "previous": "0.1%"},
    {"title": "Unemployment Claims", "country": "USD", "date": "2026-10-15T08:30:00-04:00", "impact": "High",
     "forecast": "225K", "previous": "231K"},
    {"title": "Empire State Manufacturing Index", "country": "USD", "date": "2026-10-14T08:30:00-04:00",
     "impact": "Medium", "forecast": "5.1", "previous": "11.5"},
    {"title": "German ZEW Economic Sentiment", "country": "EUR", "date": "2026-10-14T05:00:00-04:00",
     "impact": "High", "forecast": "10.2", "previous": "12.5"},
    {"title": "10-y Bond Auction", "country": "USD", "date": "2026-10-14T13:00:00-04:00", "impact": "Low",
     "forecast": "", "previous": "4.12|2.5"},
    {"title": "Bank Holiday", "country": "JPY", "date": "2026-10-14T00:00:00-04:00", "impact": "Holiday",
     "forecast": "", "previous": ""},
]).encode()

FF_XML = b"""<?xml version="1.0" encoding="windows-1252"?>
<weeklyevents>
<event><title>PPI m/m</title><country>USD</country><date><![CDATA[10-14-2026]]></date><time><![CDATA[10:00am]]></time>
  <impact><![CDATA[High]]></impact><forecast><![CDATA[0.2%]]></forecast><previous><![CDATA[0.1%]]></previous>
  <actual><![CDATA[0.5%]]></actual><url><![CDATA[https://www.forexfactory.com/calendar/1-us-ppi]]></url></event>
<event><title>CPI m/m</title><country>USD</country><date><![CDATA[10-14-2026]]></date><time><![CDATA[12:30pm]]></time>
  <impact><![CDATA[High]]></impact><forecast><![CDATA[0.3%]]></forecast><previous><![CDATA[0.4%]]></previous></event>
<event><title>Unemployment Claims</title><country>USD</country><date><![CDATA[10-15-2026]]></date>
  <time><![CDATA[12:30pm]]></time><impact><![CDATA[High]]></impact><forecast><![CDATA[225K]]></forecast>
  <previous><![CDATA[231K]]></previous></event>
<event><title>OPEC Meetings</title><country>ALL</country><date><![CDATA[10-14-2026]]></date>
  <time><![CDATA[All Day]]></time><impact><![CDATA[Medium]]></impact><forecast /><previous /></event>
</weeklyevents>"""


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(news, "CACHE", tmp_path)            # never read or write the real ~/.golddesk
    yield tmp_path


def fake_getter(calls=None, extra=None):
    def get(url, timeout=8):
        if calls is not None:
            calls.append(url)
        for key, data in (extra or {}).items():
            if key in url:
                return data
        if "ff_calendar_thisweek.json" in url:
            return FF_JSON
        if "ff_calendar_thisweek.xml" in url:
            return FF_XML
        if "news.google.com" in url and "gold+price" in url:
            return GOOGLE_RSS
        if "fxstreet.com" in url:
            return FXSTREET_ATOM
        raise OSError("blocked in the test")
    return get


def failing_getter(url, timeout=8):
    raise OSError("network down")


class FakeCalendar:
    """Stands in for nodes.Calendar: only .events is read."""

    def __init__(self, events):
        self.events = events


def ev(title, t, impact="High", country="USD", forecast="0.3%", previous="0.4%"):
    return {"t": t, "title": title, "country": country, "impact": impact, "forecast": forecast, "previous": previous}


def desk_with(headlines=(), events=None, now=NOW, **kw):
    d = news.NewsDesk(calendar=FakeCalendar(events or []), fetch=False, **kw)
    d._items = news.merge_items([], [{"title": h, "time_utc": now - age * 60, "source": "Test", "url": f"u{i}"}
                                     for i, (h, age) in enumerate(headlines)], now)
    d._headline_ok = now
    d._tried = True
    return d


# ------------------------------------------------------------------ import
def test_import_is_stdlib_only_and_starts_nothing():
    code = ("import sys, threading; sys.path.insert(0, %r); import news; "
            "bad = [m for m in ('numpy', 'aiohttp', 'requests', 'feedparser', 'certifi', 'pandas') if m in sys.modules]; "
            "print(bad, threading.active_count())") % str(HERE)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[] 1"


# ------------------------------------------------------------------ parsing
def test_parse_google_rss():
    items = news.parse_feed(GOOGLE_RSS, "Google News")
    assert len(items) == 4
    a = items[0]
    assert a["title"] == "Fed's Waller says no rush to cut rates"          # " - Reuters" stripped
    assert a["source"] == "Reuters"
    assert a["url"] == "https://news.google.com/rss/articles/a1"
    assert a["time_utc"] == NOW - 25 * 60
    assert items[1]["title"] == "Israel launches missile strikes on Iran as oil jumps"   # &nbsp; -> a space
    assert items[1]["source"] == "Bloomberg"


def test_parse_atom_and_rdf():
    items = news.parse_feed(FXSTREET_ATOM, "FXStreet")
    assert [i["source"] for i in items] == ["FXStreet", "FXStreet"]
    assert items[0]["url"] == "https://www.fxstreet.com/news/w" and items[0]["time_utc"] == NOW - 20 * 60
    assert items[1]["time_utc"] == NOW - 70 * 60
    rdf = news.parse_feed(RDF_FEED, "Wire")
    assert rdf == [{"title": "Treasury yields jump as bond selloff deepens", "url": "https://example.com/y",
                    "time_utc": NOW - 5 * 60, "source": "Wire"}]


def test_parse_refuses_entity_bombs_and_broken_xml():
    bomb = (b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]>'
            b'<rss><item><title>&b;</title></item></rss>')
    with pytest.raises(ValueError):
        news.parse_feed(bomb, "x")
    with pytest.raises(Exception):
        news.parse_feed(b"<rss><channel><item><title>oops</channel>", "x")


@pytest.mark.parametrize("s,want", [
    ("Tue, 14 Oct 2026 11:23:00 GMT", NOW - 25 * 60),
    ("Tue, 14 Oct 2026 07:23:00 -0400", NOW - 25 * 60),
    ("2026-10-14T11:23:00Z", NOW - 25 * 60),
    ("2026-10-14T07:23:00.000-04:00", NOW - 25 * 60),
    ("2026-10-14 11:23:00", NOW - 25 * 60),
    ("yesterday", None), ("", None),
])
def test_parse_time(s, want):
    assert news.parse_time(s) == want


def test_parse_ff_json_and_xml():
    js = news.parse_ff_json(FF_JSON)
    cpi = next(e for e in js if e["title"] == "CPI m/m")
    assert cpi["t"] == CPI_T and cpi["forecast"] == "0.3%" and cpi["actual"] is None
    xml = news.parse_ff_xml(FF_XML)
    assert [e["title"] for e in xml] == ["PPI m/m", "CPI m/m", "Unemployment Claims"]      # "All Day" skipped
    assert xml[0]["actual"] == "0.5%" and xml[1]["t"] == CPI_T                              # XML times are GMT
    assert news._learn_offset(js, xml, 0) == 0
    shifted = news.parse_ff_xml(FF_XML, offset_s=0)
    for e in shifted:
        e["t"] -= 4 * 3600                                    # an XML on New York time would be learned as +4 h
    assert news._learn_offset(js, shifted, 0) == 4 * 3600


# ------------------------------------------------------------------ de-duplication
def test_same_story():
    assert news.same_story("Fed's Waller says no rush to cut rates", "Fed's Waller: No rush to cut interest rates")
    assert news.same_story("Gold hits record high above $4,000", "Gold hits record high above $4,000 an ounce")
    assert not news.same_story("Gold rises as dollar weakens", "Gold falls as dollar strengthens")
    assert not news.same_story("Fed's Waller says no rush to cut rates", "Fed's Miran calls for 50 bp cut")


def test_merge_dedups_keeps_six_hours_and_drops_noise():
    raw = news.parse_feed(GOOGLE_RSS, "Google News") + news.parse_feed(FXSTREET_ATOM, "FXStreet")
    items = news.merge_items([], raw, NOW)
    titles = [i["title"] for i in items]
    assert len([t for t in titles if "Waller" in t]) == 1                   # merged across two sources
    waller = next(i for i in items if "Waller" in i["title"])
    assert waller["sources"] == ["Reuters", "FXStreet"] and waller["t"] == NOW - 25 * 60
    assert not any("Chennai" in t for t in titles)                          # retail price junk
    assert not any("surprise move" in t for t in titles)                    # 8 hours old
    assert len(items) == 3
    again = news.merge_items(items, raw, NOW + 60)                          # the next poll adds nothing
    assert len(again) == 3 and next(i for i in again if "Waller" in i["title"])["sources"] == ["Reuters", "FXStreet"]
    later = news.merge_items(again, [], NOW + 6 * 3600 - 50 * 60)           # the oldest (70 min) ages out
    assert len(later) == 2


# ------------------------------------------------------------------ the reading of a headline
SIGNS = [
    ("Fed's Waller says no rush to cut rates, sees higher-for-longer", -1, "fed"),        # hawkish
    ("Fed minutes show officials see more hikes", -1, "fed"),
    ("Powell signals rate cuts ahead as labor market cools", 1, "fed"),                    # dovish
    ("BREAKING: Fed cuts rates by 50 basis points", 1, "fed"),
    ("US CPI rises more than expected in September, core inflation hotter", -1, "inflation"),   # hot CPI
    ("Hotter-than-expected CPI sends dollar higher", -1, "inflation"),
    ("US inflation cools more than expected", 1, "inflation"),                              # cool CPI
    ("Core PCE rises less than expected", 1, "inflation"),
    ("Israel launches missile strikes on Iran; oil jumps", 1, "geopolitics"),              # war
    ("Russia launches massive drone attack on Kyiv", 1, "geopolitics"),
    ("Israel and Hamas agree to ceasefire deal", -1, "geopolitics"),                       # ceasefire
    ("Ceasefire collapses as Israel resumes strikes on Gaza", 1, "geopolitics"),
    ("Hopes for Ukraine peace deal fade", 1, "geopolitics"),
    ("US economy adds 275,000 jobs, far above expectations; payrolls beat", -1, "jobs"),   # strong NFP
    ("Nonfarm payrolls miss estimates as unemployment rate rises to 4.4%", 1, "jobs"),     # weak NFP
    ("US adds fewer jobs than expected", 1, "jobs"),
    ("Weekly jobless claims fall to 210,000", -1, "jobs"),
    ("Central banks bought 1,000 tonnes of gold; PBOC adds to reserves for 11th month", 1, "central-bank gold"),
    ("China's central bank pauses gold purchases", -1, "central-bank gold"),
    ("Gold ETFs see outflows for third week as SPDR holdings fall", -1, "etf"),            # ETF outflows
    ("SPDR Gold Trust holdings rise to highest since 2022", 1, "etf"),
    ("Treasury yields jump to 5% as bond selloff deepens", -1, "yields"),
    ("Dollar slides to three-month low", 1, "dollar"),
    ("Strong dollar weighs on gold", -1, "dollar"),
    ("Trump threatens new tariffs on EU imports", 1, "tariffs"),
    ("US and China agree to extend tariff truce", -1, "tariffs"),
    ("Regional bank shares plunge as credit fears spread", 1, "banks"),
    ("Trump says he may fire Powell; dollar slides", 1, "fed independence"),
    ("Gold demand in India surges ahead of festival season", 1, "china demand"),
    ("ISM manufacturing PMI contracts for sixth month", 1, "growth"),
]


@pytest.mark.parametrize("headline,sign,tag", SIGNS)
def test_impact_sign(headline, sign, tag):
    a = news.analyze_headline(headline)
    assert a["impact"] * sign >= 0.2, a
    assert tag in a["tags"], a
    assert a["importance"] >= 0.4
    assert ("bullish gold" if sign > 0 else "bearish gold") in a["why"] or "→" in a["why"]


def test_reason_reads_like_the_desk():
    a = news.analyze_headline("Fed's Waller says no rush to cut rates, sees higher-for-longer")
    assert a["why"] == "Hawkish Fed (higher-for-longer) → stronger USD / real yields up → bearish gold"
    assert a["importance"] >= 0.7


@pytest.mark.parametrize("headline,sign", [
    ("Fed not expected to cut rates in December", -1),          # a negated cut is not dovish
    ("Fed's Logan says she doesn't see need for rate cut", -1),
    ("Fed's Hammack: no case for rate cuts", -1),
    ("Fed rate cut bets fade", -1),
    ("Fed rate cut odds fall after hot jobs report", -1),
    ("Fed's Kashkari: rate hike not off the table", -1),         # "not off the table" keeps it on
    ("Powell not ruling out a rate cut", 1),                     # double negation
    ("Fed's Barkin sounds less hawkish on inflation", 1),
    ("Fed won't hike rates", 1),
    ("Hike bets fade after soft PCE", 1),
])
def test_negations(headline, sign):
    a = news.analyze_headline(headline)
    assert a["impact"] * sign > 0.2, a


def test_negated_cut_explains_itself():
    a = news.analyze_headline("Fed not expected to cut rates in December")
    assert a["impact"] < 0 and "not cutting" in a["why"]
    assert "Dovish" not in a["why"]


@pytest.mark.parametrize("headline,sign", [
    ("US CPI: hotter than expected at 0.4%", -1),                # the surprise sits in the clause after the colon
    ("US CPI 0.4% vs 0.3% expected", -1),
    ("US core CPI 0.2% vs 0.3% forecast", 1),
    ("Jobless claims rise more than expected", 1),               # more claims = weaker labour = gold up
    ("Unemployment rate falls to 3.9%", -1),
    ("Payrolls beat estimates", -1),
    ("Payrolls miss estimates", 1),
    ("US GDP grows 3.1%, beating estimates", -1),
])
def test_comparatives(headline, sign):
    a = news.analyze_headline(headline)
    assert a["impact"] * sign > 0.2, a


def test_neutral_and_noise():
    for h in ("PCE inflation in line with expectations", "Fed Chair Powell to speak at 2:30 pm",
              "US CPI expected to rise 0.3% in September", "Dollar steady ahead of CPI"):
        a = news.analyze_headline(h)
        assert a["impact"] == 0.0 and a["importance"] >= 0.5, (h, a)        # in focus, no direction yet
    for h in ("Gold price today in Chennai: 22K gold rate", "Apple to add 1,000 jobs in Texas",
              "Dollar General shares rise", "Federal Reserve Board announces approval of application by X Bancorp",
              "Olympic gold medal for Norway", "Best credit cards of 2026"):
        assert news.analyze_headline(h)["importance"] < news.MIN_IMPORTANCE, h


def test_watchdog_keywords_agree_with_the_repo_watchdog():
    """The ported keyword lists read every watchdog phrase the way scalper/brain/macro_watchdog.py does."""
    phrases = news.WATCHDOG_USD_UP + news.WATCHDOG_USD_DOWN
    path = HERE.parent / "scalper" / "brain" / "macro_watchdog.py"
    expect = {p: (-1 if p in news.WATCHDOG_USD_UP else 1) for p in phrases}
    if path.exists():                       # in the repo: ask the watchdog itself (the Mac install lacks the file)
        spec = importlib.util.spec_from_file_location("macro_watchdog_for_test", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod                      # its dataclass needs the module registered
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.modules.pop(spec.name, None)
        wd = mod.MacroWatchdog()
        for p in phrases:
            bias = wd.assess_headline_heuristic(p)["usd_bias"]
            assert expect[p] == (-1 if bias == "bullish_usd_gold_bear" else 1), p
    for p in phrases:
        for h in (p, f"Markets: {p}"):
            assert news.analyze_headline(h)["impact"] * expect[p] > 0.2, h


# ------------------------------------------------------------------ calendar events
def test_event_surprise_and_effect_text():
    hot = dict(ev("CPI m/m", CPI_T), actual="0.5%")
    g, txt = news.surprise(hot)
    assert g <= -0.9 and txt == "0.5% vs 0.3% forecast: hotter (clear surprise) → USD up, gold down"
    g, txt = news.surprise(dict(ev("CPI m/m", CPI_T), actual="0.3%"))
    assert g == 0.0 and "in line" in txt
    g, txt = news.surprise(dict(ev("Unemployment Claims", CPI_T, forecast="225K", previous="231K"), actual="260K"))
    assert g > 0.5 and "higher" in txt                                        # more claims -> gold up
    g, _ = news.surprise(dict(ev("Non-Farm Employment Change", CPI_T, forecast="150K", previous="22K"), actual="50K"))
    assert g >= 0.9                                                           # a 100K miss: gold up, strongly
    g, txt = news.surprise(dict(ev("German ZEW Economic Sentiment", CPI_T, country="EUR", forecast="10.2"),
                                actual="25.0"))
    assert 0 < g <= 0.4 and "EUR up" in txt                                   # foreign: small, through the dollar
    assert news.surprise(ev("CPI m/m", CPI_T)) == (0.0, None)                 # no actual yet
    assert news.event_effect(ev("Unemployment Claims", CPI_T, forecast="225K"), NOW) == \
        "higher than 225K (weaker labour) → USD down, gold up; lower → gold down"
    assert news.event_effect(ev("Fed Chair Powell Speaks", CPI_T, forecast=None), NOW) == \
        "hawkish tone → USD up, gold down; dovish → gold up"


def test_next_event_countdown_text():
    d = desk_with(events=[ev("CPI m/m", CPI_T)])
    st = d.state(NOW)
    ne = st["next_event"]
    assert set(ne) == EVENT_KEYS and ne["in_min"] == 42 and ne["time_utc"] == CPI_T
    assert ne["gold_effect"] == ("CPI m/m 08:30 NY in 42 min: hotter than 0.3% → USD up, gold down; cooler → gold up. "
                                 "Expect a spread widening and a whipsaw; no new entries from 15 min before to "
                                 "10 min after.")
    assert not st["wait"] and st["wait_text"] is None
    assert "Next: CPI m/m at 08:30 NY, in 42 min." in st["summary"]


@pytest.mark.parametrize("offset_min,wait", [(-16, False), (-15, True), (-1, True), (0, True), (5, True), (10, True),
                                             (11, False)])
def test_wait_window(offset_min, wait):
    d = desk_with(events=[ev("CPI m/m", CPI_T)])
    st = d.state(CPI_T + offset_min * 60)
    assert st["wait"] is wait
    if wait:
        assert st["wait_text"].startswith("WAIT: CPI m/m") and "No new entries until 08:40 NY" in st["wait_text"]
        assert st["summary"].startswith("WAIT: ") and st["next_event"]["title"] == "CPI m/m"
    else:
        assert st["wait_text"] is None


def test_wait_only_for_high_impact_usd():
    d = desk_with(events=[ev("Empire State Manufacturing Index", CPI_T, impact="Medium"),
                          ev("German ZEW Economic Sentiment", CPI_T, country="EUR")])
    st = d.state(CPI_T)
    assert not st["wait"] and st["next_event"] is None
    assert [e["title"] for e in st["events"]] == ["Empire State Manufacturing Index", "German ZEW Economic Sentiment"]


def test_wait_matches_the_calendar_node():
    import nodes
    cal = nodes.Calendar.__new__(nodes.Calendar)
    cal.events = [ev("CPI m/m", CPI_T)]
    d = news.NewsDesk(calendar=cal, fetch=False)
    for m in range(-20, 20):
        t = CPI_T + m * 60
        assert d.state(t)["wait"] == bool(cal.near(t)), m


def test_events_list_today_max_eight():
    evs = [ev(f"Event {i}", NOW + i * 1800, impact="High" if i % 2 else "Medium") for i in range(12)]
    evs += [ev("Yesterday", NOW - 20 * 3600), ev("Low one", NOW + 600, impact="Low"),
            ev("Next week", NOW + 3 * 86400)]
    st = desk_with(events=evs).state(NOW)
    assert len(st["events"]) == 8 and all(set(e) == EVENT_KEYS for e in st["events"])
    titles = [e["title"] for e in st["events"]]
    assert "Yesterday" not in titles and "Low one" not in titles and "Next week" not in titles
    assert [e["time_utc"] for e in st["events"]] == sorted(e["time_utc"] for e in st["events"])


def test_released_event_without_actual_borrows_the_headlines():
    d = desk_with(headlines=[("US CPI rises more than expected, core inflation hotter", 3)],
                  events=[ev("CPI m/m", NOW - 5 * 60)])
    e = d.state(NOW)["events"][0]
    assert e["actual"] is None and "released 5 min ago" in e["gold_effect"]
    assert "gold-negative" in e["gold_effect"] and "hot inflation" in e["gold_effect"]


# ------------------------------------------------------------------ the desk with fixture feeds
def test_refresh_reuses_the_calendar_node_and_merges_the_xml_actual():
    calls = []
    cal = FakeCalendar(news.parse_ff_json(FF_JSON))
    for e in cal.events:                    # nodes.Calendar's events have no "actual" / "url" keys
        e.pop("actual"), e.pop("url")
    d = news.NewsDesk(calendar=cal, fetch=False)
    d._get = fake_getter(calls)
    d.refresh(NOW)
    assert not any(u.endswith(".json") for u in calls)                        # no second download of the JSON
    assert news.CAL_XML in calls
    st = d.state(NOW)
    ppi = next(e for e in st["events"] if e["title"] == "PPI m/m")
    assert ppi["actual"] == "0.5%" and "hotter" in ppi["gold_effect"] and "gold down" in ppi["gold_effect"]
    assert st["status"] == "ok" and st["updated"] == NOW
    assert [h["title"] for h in st["headlines"]][:2] == ["Fed's Waller says no rush to cut rates",
                                                        "Israel launches missile strikes on Iran as oil jumps"]
    assert st["headlines"][0]["source"] == "Reuters +1"
    assert all(set(h) == HEADLINE_KEYS for h in st["headlines"])
    assert st["bias"] < 0 and st["bias_text"].startswith("news leans")
    assert "PPI m/m 0.5% vs 0.2%" in st["analysis"]
    assert st["next_event"]["title"] == "CPI m/m"


def test_refresh_fetches_the_json_itself_without_a_node():
    calls = []
    d = news.NewsDesk(fetch=False)
    d._get = fake_getter(calls)
    d.refresh(NOW)
    assert news.CAL_URLS[0] in calls
    assert d.state(NOW)["next_event"]["title"] == "CPI m/m"
    calls.clear()
    d.refresh(NOW + 120)
    assert not any("ff_calendar" in u for u in calls)                          # hourly, not every round
    assert not any("fxstreet" in u for u in calls)                             # each feed keeps its own pace


def test_failing_source_backs_off():
    d = news.NewsDesk(fetch=False)
    d._get = fake_getter()
    d.refresh(NOW)
    fed = next(s for u, s in d._src.items() if "federalreserve" in u)
    assert fed["fails"] == 1 and fed["next"] > NOW + fed["every"] and "blocked" in fed["err"]
    assert any(s["error"] for s in d.sources())


def test_cache_survives_a_restart(tmp_cache):
    d = news.NewsDesk(fetch=False)
    d._get = fake_getter()
    d.refresh(NOW)
    d2 = news.NewsDesk(fetch=False)
    st = d2.state(NOW + 60)
    assert st["status"] == "stale" and st["headlines"] and st["next_event"]["title"] == "CPI m/m"
    assert st["updated"] == NOW


def test_status_goes_stale():
    d = news.NewsDesk(fetch=False)
    d._get = fake_getter()
    d.refresh(NOW)
    assert d.state(NOW + 300)["status"] == "ok"
    assert d.state(NOW + 20 * 60)["status"] == "stale"


# ------------------------------------------------------------------ the bias
def test_bias_bullish_bearish_and_none():
    bull = desk_with([("Powell signals rate cuts ahead as labor market cools", 10),
                      ("Israel launches missile strikes on Iran", 20),
                      ("Dollar slides to three-month low", 30)]).state(NOW)
    assert bull["bias"] > 0.3 and bull["bias_text"].startswith("news leans bullish for gold: ")
    bear = desk_with([("US CPI rises more than expected, core inflation hotter", 10),
                      ("Fed's Waller says no rush to cut rates", 20),
                      ("Gold ETFs see outflows for third week", 30)]).state(NOW)
    assert bear["bias"] < -0.3 and bear["bias_text"].startswith("news leans bearish for gold: ")
    assert "bearish for gold" in bear["analysis"].lower()
    none = desk_with([]).state(NOW)
    assert none["bias"] == 0.0 and none["headlines"] == []
    assert none["bias_text"] == "no gold-moving news in the last 6 h"


def test_bias_weighs_recency_and_shrinks_thin_news():
    fresh_bear = desk_with([("Fed's Waller says no rush to cut rates", 5),
                            ("Powell signals rate cuts ahead as labor market cools", 300)]).state(NOW)
    assert fresh_bear["bias"] < -0.1
    fresh_bull = desk_with([("Fed's Waller says no rush to cut rates", 300),
                            ("Powell signals rate cuts ahead as labor market cools", 5)]).state(NOW)
    assert fresh_bull["bias"] > 0.1
    one = desk_with([("Fed's Waller says no rush to cut rates", 1)]).state(NOW)
    assert -0.6 < one["bias"] < -0.2                         # one headline can't swing it to -0.85
    mixed = desk_with([("Fed's Waller says no rush to cut rates", 10),
                       ("Israel launches missile strikes on Iran", 10)]).state(NOW)
    assert abs(mixed["bias"]) < 0.15


def test_released_surprise_moves_the_bias():
    d = desk_with(events=[dict(ev("CPI m/m", NOW - 10 * 60), actual="0.6%")])
    st = d.state(NOW)
    assert st["bias"] < -0.3 and "hotter" in st["bias_text"]


def test_headlines_sorted_by_importance_and_recency():
    st = desk_with([("Gold ETFs see outflows for third week", 2),
                    ("Fed's Waller says no rush to cut rates", 10),
                    ("BREAKING: Fed cuts rates by 50 basis points", 300),
                    ("Gold edges higher in quiet Asian trade", 5), ("Dollar slides to three-month low", 60),
                    ("Trump threatens new tariffs on EU imports", 90), ("Treasury yields jump as bond selloff deepens", 30),
                    ("Gold demand in India surges ahead of festival season", 200),
                    ("Regional bank shares plunge as credit fears spread", 15),
                    ("Weekly jobless claims fall to 210,000", 45)]).state(NOW)
    hl = st["headlines"]
    assert len(hl) == 8
    assert hl[0]["title"] == "Fed's Waller says no rush to cut rates"
    scores = [h["importance"] * 0.5 ** (h["age_min"] / news.RANK_HALF_MIN) for h in hl]
    assert scores == sorted(scores, reverse=True)


# ------------------------------------------------------------------ LLM
class _LLM(BaseHTTPRequestHandler):
    mode = "chat"

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/v1/chat/completions" and self.mode == "chat":
            assert body["messages"][0]["role"] == "system"
            out = {"choices": [{"message": {"content": "Gold is under pressure from a hawkish Fed. The CPI print "
                                                       "in 42 minutes decides the next move. A third sentence."}}]}
        elif self.path == "/completion" and self.mode == "llama":
            assert body["prompt"].startswith("<|im_start|>system")
            out = {"content": "Hawkish Fed talk weighs on gold. CPI is next.<|im_end|>"}
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def llm_server():
    servers = []

    def start(mode):
        handler = type("H", (_LLM,), {"mode": mode})
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"
    yield start
    for s in servers:
        s.shutdown()


@pytest.fixture
def blackhole():
    """Accepts connections and never answers."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(16)
    yield f"http://127.0.0.1:{s.getsockname()[1]}"
    s.close()


def refused_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


def test_llm_summary_openai_compatible(llm_server):
    d = desk_with([("Fed's Waller says no rush to cut rates", 10)], events=[ev("CPI m/m", CPI_T)],
                  llm_url=llm_server("chat"))
    d._get = failing_getter
    d.refresh(NOW)
    st = d.state(NOW)
    assert st["llm"] is True
    assert st["summary"] == "Gold is under pressure from a hawkish Fed. The CPI print in 42 minutes decides the next move."
    assert "keyword rules" in st["analysis"]                                  # the analysis stays rule-based


def test_llm_summary_llama_cpp_completion(llm_server):
    d = desk_with([("Fed's Waller says no rush to cut rates", 10)], llm_url=llm_server("llama"))
    d._get = failing_getter
    d.refresh(NOW)
    st = d.state(NOW)
    assert st["llm"] is True and st["summary"] == "Hawkish Fed talk weighs on gold. CPI is next."


def test_llm_unreachable_falls_back_within_the_limit(blackhole):
    t0 = time.monotonic()
    assert news.llm_summary(blackhole, "prompt") is None
    assert time.monotonic() - t0 <= news.LLM_TIMEOUT + 0.5
    t0 = time.monotonic()
    assert news.llm_summary(refused_url(), "prompt") is None
    assert time.monotonic() - t0 < 1.0
    d = desk_with([("Fed's Waller says no rush to cut rates", 10)], llm_url=blackhole)
    d._get = failing_getter
    t0 = time.monotonic()
    d.refresh(NOW)
    assert time.monotonic() - t0 <= news.LLM_TIMEOUT + 1.0
    st = d.state(NOW)
    assert st["llm"] is False and st["summary"].startswith("News leans bearish for gold")
    t0 = time.monotonic()
    d.refresh(NOW + 90)                                       # a dead LLM is not asked again for 5 minutes
    assert time.monotonic() - t0 < 1.0


# ------------------------------------------------------------------ state() never raises
def test_state_when_every_source_fails():
    d = news.NewsDesk(fetch=False)
    d._get = failing_getter
    assert d.state(NOW)["status"] == "offline: fetching is off"
    d.refresh(NOW)
    st = d.state(NOW)
    assert set(st) == STATE_KEYS
    assert st["status"].startswith("offline: ") and "network down" in st["status"]
    assert st["headlines"] == [] and st["events"] == [] and st["next_event"] is None and st["wait"] is False
    assert st["bias"] == 0.0 and st["llm"] is False and st["updated"] is None
    assert "unavailable" in st["analysis"]
    json.dumps(st)


def test_state_with_a_broken_calendar_and_internals():
    class Broken:
        @property
        def events(self):
            raise RuntimeError("calendar exploded")
    d = news.NewsDesk(calendar=Broken(), fetch=False)
    d._get = failing_getter
    d.refresh(NOW)
    assert d.state(NOW)["status"].startswith("offline: ")
    d._items = [{"title": "garbage"}]                         # an item missing every field
    st = d.state(NOW)
    assert set(st) == STATE_KEYS and st["status"].startswith("offline: ")
    d._items = []
    d._compose = lambda *a, **k: 1 / 0
    st = d.state(NOW)
    assert set(st) == STATE_KEYS and st["status"].startswith("offline: error ZeroDivisionError")


def test_headline_sources_down_but_calendar_up():
    d = news.NewsDesk(calendar=FakeCalendar([ev("CPI m/m", CPI_T)]), fetch=False)
    d._get = failing_getter
    d.refresh(NOW)
    st = d.state(NOW)
    assert st["status"] == "offline: no headline source reachable (calendar ok)"
    assert st["next_event"]["title"] == "CPI m/m"


def test_background_thread_never_blocks(monkeypatch):
    started = threading.Event()

    def slow(url, timeout=8):
        started.set()
        time.sleep(2)
        raise OSError("slow")
    monkeypatch.setattr(news, "_http_get", slow)
    t0 = time.monotonic()
    d = news.NewsDesk(fetch=True)
    st = d.state(int(time.time()))
    assert time.monotonic() - t0 < 0.5
    assert set(st) == STATE_KEYS and st["status"].startswith("offline: ")
    assert started.wait(2)
    d.stop()


# ------------------------------------------------------------------ clock helpers
def test_new_york_clock_and_countdown():
    assert news.ny_clock(CPI_T) == "08:30"
    assert news.ny_clock(int(datetime(2026, 11, 5, 13, 30, tzinfo=timezone.utc).timestamp())) == "08:30"   # EST
    assert news.ny_clock(int(datetime(2026, 3, 9, 12, 30, tzinfo=timezone.utc).timestamp())) == "08:30"    # EDT
    assert news.countdown(42 * 60) == "42 min"
    assert news.countdown(125 * 60) == "2 h 5 min"
    assert news.countdown(27 * 3600) == "1 d 3 h"


def test_status_ok_when_feeds_answer_with_nothing_relevant():
    def quiet(url, timeout=8):
        if "faireconomy" in url:
            raise OSError("calendar down")
        return b"<rss><channel><item><title>Best credit cards of 2026</title></item></channel></rss>"
    d = news.NewsDesk(fetch=False)
    d._get = quiet
    d.refresh(NOW)
    st = d.state(NOW)
    assert st["status"] == "ok" and st["headlines"] == [] and st["updated"] == NOW
    assert st["bias_text"] == "no gold-moving news in the last 6 h"
    assert "Economic calendar unavailable (calendar json: OSError: calendar down" in st["analysis"]


def test_status_while_the_first_fetch_runs():
    d = news.NewsDesk(fetch=False)
    d.fetch = True                              # as if the thread were running but had not finished a round
    assert d.state(NOW)["status"] == "offline: starting (first fetch running)"
