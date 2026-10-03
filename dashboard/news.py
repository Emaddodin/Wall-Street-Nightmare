"""The news box: what the economic calendar and the live headlines say for gold, in a few lines. Free, no keys.

  Calendar    Forex Factory's weekly feed (nfs.faireconomy.media), the same one as the Calendar node in nodes.py.
              Pass that node (NewsDesk(calendar=nodes.calendar)) and its events are read, not fetched again. The
              feed's XML twin is read too, for fields the JSON lacks (an actual once released, the event's link).
              Today's and the next 24 hours' USD events, plus high-impact EUR / CNY ones. WAIT is the same rule as
              the node: a high-impact USD event from 15 minutes before to 10 minutes after.
  Headlines   free RSS / Atom feeds, parsed with xml.etree (no feedparser): Google News searches, the Federal
              Reserve's press releases and speeches, FXStreet, CNBC, MarketWatch, Investing.com, Kitco (through
              Google News) and Yahoo Finance. Every source is optional: one that fails backs off and the rest carry
              on. Near-identical titles from different sources are merged, and only the last 6 hours are kept.
  Reading     each headline gets an importance (0..1) and a direction for gold (-1..+1) with a one-line reason,
              from keyword rules you can read below (no model): the Fed and its speakers, inflation, jobs, growth,
              yields, the dollar, war / ceasefire, central-bank gold buying, gold ETF flows, tariffs, bank stress,
              US fiscal risk, Fed independence, China / India demand. Negations ("not expected to cut", "no rush")
              and surprises ("hotter than expected", "beat", "miss", "0.4% vs 0.3% expected") are handled.
              The keyword lists of the repo's watchdog (scalper/brain/macro_watchdog.py) are ported in as a last
              layer, because the Mac install only checks out dashboard/; tests/test_news.py checks they agree.
  Bias        the importance- and recency-weighted mean of the directions (released calendar surprises count
              too), pulled toward 0 when there is little news. -1 bearish for gold .. +1 bullish.
  Summary     a rule-based summary and analysis always; with llm_url (an OpenAI-compatible server or llama.cpp,
              e.g. the local Qwen of macro/slm_intuition.py on http://127.0.0.1:8080) a 2-sentence summary is
              asked for in the background thread, with a hard 3-second limit, and dropped silently on any failure.

Analysis only: nothing here places or blocks an order. Fetching runs in its own thread; state() never waits and
never raises.

    python3 news.py                                       # fetch once and print the state (needs internet)
    python3 news.py --llm http://127.0.0.1:8080           # the same, with the local LLM's summary
    python3 news.py --headline "Fed's Waller says no rush to cut rates"
"""
from __future__ import annotations

import concurrent.futures
import difflib
import hashlib
import html
import html.entities
import json
import math
import random
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

try:    # the Calendar node's window and feed, so the page never shows two different WAITs
    from nodes import CAL_URLS, WAIT_AFTER, WAIT_BEFORE
except Exception:                                             # pragma: no cover - news.py used outside dashboard/
    WAIT_BEFORE, WAIT_AFTER = 15, 10
    CAL_URLS = ("https://nfs.faireconomy.media/ff_calendar_thisweek.json",
                "https://nfs.faireconomy.media/ff_calendar_nextweek.json")

CACHE = Path.home() / ".golddesk"                    # news.json: last headlines + calendar, so a restart shows them
CAL_XML = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
REFRESH = (70, 110)               # seconds between refreshes (random in this range); each source has its own pace
KEEP_H = 6                        # headlines older than this are dropped
LIVE_S = 600                      # status "ok" while a headline source answered within this many seconds
FETCH_TIMEOUT = 8
LLM_TIMEOUT = 3.0                 # hard limit for the LLM summary, in the background thread
MAX_BYTES = 3_000_000
RANK_HALF_MIN = 120               # the box's ranking: importance x 0.5 ** (age / 2 h)
BIAS_HALF_MIN = 90                # the bias: importance x 0.5 ** (age / 90 min)
BIAS_PRIOR = 1.0                  # weight of a neutral "no news" prior: one fresh headline can't swing the bias fully
MIN_IMPORTANCE = 0.3              # a headline below this isn't about anything that moves gold
CAL_EVERY = 3600                  # own JSON fetch when no Calendar node is given (the node also polls hourly)
XML_EVERY, XML_FAST = 3600, 600   # the XML hourly; every 10 min in the hour after a high-impact release with no actual
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/124.0 Safari/537.36 GoldDesk",
           "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*;q=0.5"}


def _gnews(q: str) -> str:
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})


# (name, url, seconds between fetches). None of these could be fetched from the build machine (its network blocks
# them), so each is marked with how sure we are that it is a live, keyless RSS / Atom feed. All are optional.
SOURCES = (
    # Google News search RSS: a well-known public endpoint; items carry <source> (the publisher) and the title ends
    # in " - Publisher". "when:12h" is Google News' own time operator (keeps the search to fresh stories).
    ("Google News", _gnews("gold price OR federal reserve OR fed OR inflation OR dollar"), 120),
    ("Google News", _gnews("(CPI OR payrolls OR jobless claims OR treasury yields OR FOMC OR Powell) when:12h"), 300),
    ("Google News", _gnews("(war OR missile OR ceasefire OR sanctions OR tariffs) gold when:12h"), 300),
    ("Google News", _gnews('("central bank" gold OR "gold ETF" OR "gold demand") when:2d'), 900),
    # federalreserve.gov publishes these RSS feeds (listed on federalreserve.gov/feeds): all press releases
    # (FOMC statements, minutes) and speeches. Well established.
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml", 300),
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/speeches.xml", 900),
    # FXStreet's news RSS (fxstreet.com/rss/news): long-standing, forex / gold wire-style headlines.
    ("FXStreet", "https://www.fxstreet.com/rss/news", 180),
    # CNBC's public RSS by section id (20910258 = Economy). Long-standing URL scheme.
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258", 300),
    # MarketWatch moved its RSS to Dow Jones' feeds host (mw_marketpulse = the running market wire). Likely live.
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_marketpulse", 300),
    # Investing.com's "Commodities & Futures News" RSS; often refuses scripts (403), so it is a bonus only.
    ("Investing.com", "https://www.investing.com/rss/news_11.rss", 600),
    # Kitco has no stable public RSS, so its stories come through a Google News site: search.
    ("Kitco", _gnews("site:kitco.com gold when:2d"), 900),
    # Yahoo Finance's per-symbol headline RSS for gold futures (GC=F). Old but still served; optional.
    ("Yahoo Finance", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GC=F&region=US&lang=en-US", 300),
)

_SSL = None


def _ssl_context():
    """certifi's CA bundle when it is installed (python.org Pythons on macOS often lack one), else the system's."""
    global _SSL
    if _SSL is None:
        try:
            import certifi
            _SSL = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            _SSL = ssl.create_default_context()
    return _SSL


def _http_get(url: str, timeout: float = FETCH_TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("feed too large")
    return data


# ================================================================== parsing
_XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}
_TAGS = re.compile(r"<[^>]+>")


def _fix_entities(data: bytes) -> bytes:
    """HTML entities (&nbsp; &mdash; ...) that feeds put in XML without declaring them -> numeric references."""
    def sub(m):
        name = m.group(1).decode("ascii", "ignore")
        if name in _XML_ENTITIES:
            return m.group(0)
        cp = html.entities.name2codepoint.get(name)
        return f"&#{cp};".encode() if cp else b""
    return re.sub(rb"&([A-Za-z][A-Za-z0-9]{1,31});", sub, data)


def _xml_root(data: bytes):
    head = data[:20000]
    if b"<!ENTITY" in head:                          # entity declarations: refused (no billion-laughs expansion)
        raise ValueError("XML with entity declarations refused")
    return ET.fromstring(_fix_entities(data))


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _text(el) -> str:
    if el is None:
        return ""
    s = "".join(el.itertext()) if len(el) else (el.text or "")
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub(" ", html.unescape(s)))).strip()


def parse_time(s: str) -> int | None:
    """RFC 822 (RSS) or ISO 8601 (Atom) -> UTC epoch seconds; None if unreadable."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
        if d is not None:
            return int((d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp())
    except (TypeError, ValueError, IndexError):
        pass
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)(?:[T ](\d\d):(\d\d)(?::(\d\d))?(?:\.\d+)?)?\s*(Z|[+-]\d\d:?\d\d)?$", s)
    if not m:
        return None
    y, mo, d, hh, mm, ss, tz = m.groups()
    try:
        t = datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), int(ss or 0), tzinfo=timezone.utc)
    except ValueError:
        return None
    off = 0
    if tz and tz != "Z":
        sign = -1 if tz[0] == "-" else 1
        tz = tz[1:].replace(":", "")
        off = sign * (int(tz[:2]) * 3600 + int(tz[2:4]) * 60)
    return int(t.timestamp()) - off


def parse_feed(data: bytes, source: str = "") -> list:
    """RSS 2.0, RSS 1.0 (RDF) or Atom -> [{"title", "url", "time_utc" (or None), "source"}]."""
    root = _xml_root(data)
    out = []
    google = "news.google." in source.lower() or source == "Google News"
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        kids: dict = {}
        for c in el:
            kids.setdefault(_local(c.tag), []).append(c)
        title = _text((kids.get("title") or [None])[0])
        if not title:
            continue
        url = ""
        for ln in kids.get("link", []):
            href = ln.get("href")
            if href and ln.get("rel", "alternate") == "alternate":
                url = href
                break
            if not href and (ln.text or "").strip():
                url = ln.text.strip()
                break
        g = (kids.get("guid") or [None])[0]
        if not url and g is not None and g.get("isPermaLink", "true") != "false":
            url = _text(g)
        t = None
        for k in ("pubdate", "published", "updated", "date", "issued", "modified"):
            if kids.get(k):
                t = parse_time(_text(kids[k][0]))
                if t:
                    break
        src = source
        s_el = (kids.get("source") or [None])[0]
        if s_el is not None:
            name = _text(s_el) or _text(s_el.find("{http://www.w3.org/2005/Atom}title"))
            if name:
                src = name
        if google or (src and src != source):           # Google News titles end in " - Publisher"
            m = re.match(r"^(.*\S)\s+[-–—|]\s+([^-–—|]{2,60})$", title)
            if m and (m.group(2).strip().lower() == src.lower() or google):
                title = m.group(1)
                if google and (not src or src == "Google News"):
                    src = m.group(2).strip()
        out.append({"title": title, "url": url, "time_utc": t, "source": src or source or "news"})
    return out


def _ff_event(title, country, t, impact, forecast, previous, actual=None, url=None) -> dict:
    blank = lambda v: (str(v).strip() or None) if v is not None else None     # noqa: E731
    return {"t": int(t), "title": (title or "").strip(), "country": (country or "").strip().upper(),
            "impact": (impact or "").strip().title(), "forecast": blank(forecast), "previous": blank(previous),
            "actual": blank(actual), "url": blank(url)}


def parse_ff_json(data) -> list:
    """Forex Factory's weekly JSON (as nodes.Calendar reads it) -> events, with "actual" when the feed has one."""
    rows = json.loads(data) if isinstance(data, (bytes, str)) else data
    out = []
    for e in rows or []:
        try:
            t = int(datetime.fromisoformat(e["date"]).timestamp())
        except (KeyError, TypeError, ValueError):
            continue
        out.append(_ff_event(e.get("title"), e.get("country"), t, e.get("impact"), e.get("forecast"),
                             e.get("previous"), e.get("actual"), e.get("url")))
    return out


def parse_ff_xml(data: bytes, offset_s: int = 0) -> list:
    """Forex Factory's weekly XML -> events. Its date (MM-DD-YYYY) and time (8:30am) are read as GMT; offset_s
    corrects that when the JSON (which carries a UTC offset) showed otherwise. "All Day" / "Tentative" are skipped."""
    root = _xml_root(data)
    out = []
    for ev in root.iter():
        if _local(ev.tag) != "event":
            continue
        f = {_local(c.tag): _text(c) for c in ev}
        m = re.match(r"(\d{1,2})-(\d{1,2})-(\d{4})$", f.get("date", ""))
        tm = re.match(r"(\d{1,2}):(\d\d)\s*([ap]m)$", f.get("time", "").lower())
        if not m or not tm:
            continue
        h = int(tm.group(1)) % 12 + (12 if tm.group(3) == "pm" else 0)
        try:
            t = datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)), h, int(tm.group(2)),
                         tzinfo=timezone.utc).timestamp() + offset_s
        except ValueError:
            continue
        out.append(_ff_event(f.get("title"), f.get("country"), t, f.get("impact"), f.get("forecast"),
                             f.get("previous"), f.get("actual"), f.get("url")))
    return out


# ================================================================== reading a headline
def _norm(s: str) -> str:
    s = html.unescape(s or "")
    s = s.translate({0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"', 0x2013: "-", 0x2014: " - ", 0x2011: "-"})
    s = s.lower()
    s = re.sub(r"\bu\.s\.(?=\W|$)", "us", s)
    s = re.sub(r"\bxau\s*/?\s*usd\b", "xau usd", s)
    s = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", s)                  # rate-cut -> rate cut, higher-for-longer
    return re.sub(r"\s+", " ", s).strip()


def _rx(*parts) -> re.Pattern:
    return re.compile("|".join(parts))


_BOUND = re.compile(r"[;:|,]|\s-\s|\b(?:as|after|amid|while|despite|but|following|ahead of|before|since|though|"
                    r"although|whereas)\b")
_BOUND_NC = re.compile(r"[;:|]|\s-\s|\b(?:as|after|amid|while|despite|but|following|ahead of|before|since|though|"
                       r"although|whereas)\b")                   # the same, keeping commas (data headlines)
NEG = re.compile(r"\b(?:not|no|never|without|unlikely|against|less|nor|neither|hardly)\b|n't\b|"
                 r"\b(?:rules?|ruled|ruling) out\b|\bdismiss\w*|\breject\w*|\bdoubts?\b|\bdoubtful\b|"
                 r"\bdelay\w*|\bpostpon\w*|\bhold(?:s|ing)? off\b|\bpush(?:es|ed|ing)? back\b|"
                 r"\bscal(?:e|es|ed|ing) back\b|\bpar(?:e|es|ed|ing)\b|\btoo (?:early|soon)\b|\bpremature\b|"
                 r"\bresist\w*|\babandon\w*|\bscrap\w*")
FADE = re.compile(r"\b(?:fade[sd]?|fading|wane[sd]?|waning|dim(?:s|med)?|reced\w*|evaporat\w*|vanish\w*|"
                  r"dash(?:ed|es)?|pared|trimmed|slashed|unwound|ebb(?:s|ed)?|cool(?:s|ed)?|"
                  r"collaps\w*|crumbl\w*)\b|\bpriced out\b|\bscaled back\b|\bpushed back\b|\boff the table\b")

# --- the Fed and other central banks: stance phrases (dovish = good for gold)
FED_CORE = _rx(r"\bfomc\b", r"\bpowell\b", r"\bfed chair\w*", r"\bfederal open market\b", r"\bfed (?:rate )?decision\b",
               r"\bfed minutes\b", r"\bjackson hole\b", r"\bdot plot\b", r"\bbeige book\b")
FED_ANY = _rx(r"\bfed\b(?! up)", r"\bfederal reserve\b", r"\bus central bank\b", r"\bfomc\b", r"\bpowell\b",
              r"\b(?:waller|kashkari|goolsbee|musalem|hammack|bostic|barkin)\b")
FED_POLICY = re.compile(r"\b(?:rates?|cuts?|cutting|hikes?|hiking|policy|inflation|minutes|statement|decision|"
                        r"outlook|economy|labou?r|jobs?|balance sheet|independence|says?|said|speech|remarks|"
                        r"testimony|testif\w+|speaks|sees|warns?|signals?|hawk\w*|dov\w*|meeting|chair\w*|governor|"
                        r"powell|fomc|yields?|dollar|gold|tariffs?|easing|tightening|pause|hold|holds|projections)\b")
FOREIGN_CB = re.compile(r"\b(?:ecb|european central bank|lagarde|boe|bank of england|boj|bank of japan|ueda|snb|"
                        r"swiss national bank|boc|bank of canada|rba|reserve bank of australia|rbnz|pboc|"
                        r"people's bank of china|china's central bank|rbi|reserve bank of india|riksbank|norges bank|"
                        r"central bank of \w+|\w+'s central bank)\b")
CHINA_CB = re.compile(r"\b(?:pboc|people's bank of china|china's central bank)\b")
HAWK = (   # (pattern, weight)
    (r"\bhawk(?:ish|s)?\b", 1.0), (r"\bhigher for longer\b", 1.0), (r"\brate hikes?\b", 1.0),
    (r"\bhik(?:e|es|ed|ing) (?:interest )?rates?\b", 1.0), (r"\brais(?:e|es|ed|ing) (?:interest )?rates\b", 1.0),
    (r"\btighten(?:ing|s|ed)?\b", 0.8), (r"\bno rush\b", 1.0), (r"\bin no hurry\b", 1.0),
    (r"\bnot in a hurry\b", 1.0), (r"\bpatien(?:t|ce)\b", 0.6), (r"\brestrictive\b", 0.7),
    (r"\bfewer (?:rate )?cuts\b", 1.0), (r"\bmore work to do\b", 0.8), (r"\bquantitative tightening\b", 0.6),
    (r"\b(?:holds?|held|keeps?|kept|leaves?|left) (?:interest )?rates? (?:steady|unchanged|on hold)\b", 0.4),
    (r"\bon hold\b", 0.4), (r"\b(?:more|further|additional) (?:rate )?hikes?\b", 1.0),
    (r"\bhike (?:bets|hopes|expectations|odds|pricing|wagers)\b", 1.0),
    (r"\bpaus(?:e|es|ed|ing) (?:on )?(?:rate|rates|cuts|cutting|easing|its easing)\b|\b(?:rate|easing|cutting) pause\b",
     0.4),
)
DOVE = (
    (r"\bdov(?:e|es|ish)\b", 1.0), (r"\brate cuts?\b", 1.0), (r"\bcut(?:s|ting)? (?:interest )?rates?\b", 1.0),
    (r"\b(?:25|50|75) (?:basis point|bp|bps) (?:rate )?cuts?\b", 1.0), (r"\bjumbo cut\b", 1.0),
    (r"\blower(?:s|ing)? (?:interest )?rates\b", 1.0), (r"\beas(?:e|es|ing) (?:monetary )?policy\b", 1.0),
    (r"\bmonetary easing\b|\beasing cycle\b|\bcutting cycle\b", 1.0), (r"\bpivot\w*\b", 0.8),
    (r"\b(?:more|further) cuts\b", 1.0), (r"\bcuts? (?:are )?(?:coming|ahead|on the way|likely)\b", 1.0),
    (r"\b(?:signals?|hints?|flags?|eyes|sees|backs|supports?|open to|favou?rs?) (?:at )?(?:\w+ ){0,2}cuts?\b", 1.0),
    (r"\bquantitative easing\b|\bqe\b", 0.8), (r"\bcut (?:bets|hopes|expectations|odds|pricing|wagers)\b", 1.0),
    (r"\bend(?:s|ing)? (?:qt|quantitative tightening)\b", 0.6),
)
HAWK = tuple((re.compile(p), w) for p, w in HAWK)
DOVE = tuple((re.compile(p), w) for p, w in DOVE)

# --- data, yields, dollar, gold: subjects whose direction comes from the word next to them
LEVEL_UP = re.compile(
    r"\b(?:ris(?:e|es|ing)|rose|risen|jump(?:s|ed|ing)?|surg(?:e|es|ed|ing)|climb(?:s|ed|ing)?|gain(?:s|ed|ing)?|"
    r"rall(?:y|ies|ied|ying)|soar(?:s|ed|ing)?|spik(?:e|es|ed|ing)|up|higher|highs?|accelerat\w*|hot|hotter|"
    r"heat(?:s|ed|ing)? up|firm(?:s|ed|er|ing)?|increas(?:e|es|ed|ing)|advanc(?:e|es|ed|ing)|rebound(?:s|ed|ing)?|"
    r"sticky|elevated|persistent|exceed(?:s|ed|ing)?|above|stronger|strengthen(?:s|ed|ing)?|swell(?:s|ed)?|"
    r"picks? up|picked up|too high|re ?accelerat\w*|grow(?:s|ing)?|grew|expand(?:s|ed|ing)?)\b")
LEVEL_DOWN = re.compile(
    r"\b(?:fall(?:s|ing)?|fell|fallen|drop(?:s|ped|ping)?|slid(?:e|es|ing)?|slump(?:s|ed|ing)?|declin(?:e|es|ed|ing)|"
    r"dip(?:s|ped|ping)?|eas(?:e|es|ed|ing)|cool(?:s|ed|ing|er)?|slow(?:s|ed|ing|er)?|slowdown|tumbl(?:e|es|ed|ing)|"
    r"plung(?:e|es|ed|ing)|sink(?:s|ing)?|sank|sunk|lower|lows?|decelerat\w*|retreat(?:s|ed|ing)?|shrink(?:s|ing)?|"
    r"shrank|contract(?:s|ed|ing|ion)?|slip(?:s|ped|ping)?|sag(?:s|ged|ging)?|down|los(?:e|es|ing)|lost|"
    r"shed(?:s|ding)?|moderat(?:e|es|ed|ing)|subdued|tame|tamer|reced(?:e|es|ed|ing)|below|crash(?:es|ed)?|"
    r"plummet(?:s|ed|ing)?|weaken(?:s|ed|ing)?)\b")
QUAL_UP = re.compile(r"\b(?:strong|strength|robust|solid|beats?|beating|better|upbeat|blowout|booming|resilient|"
                     r"tops?|topping)\b")
QUAL_DOWN = re.compile(r"\b(?:weak|weakness|miss(?:es|ed)?|missing|worse|disappoint\w*|soft|softer|softens|sluggish|"
                       r"dismal|gloomy|poor|tepid|anemic|stall(?:s|ed)?)\b")
FLAT = re.compile(r"\b(?:steady|flat|unchanged|little changed|stable|in line|as expected|muted|mixed|range ?bound|"
                  r"treads water|marks time)\b")
_EXP = r"(?:expected|forecasts?|estimated|estimates?|anticipated|consensus|economists expected|projections|views?|" \
       r"expectations)"
_UPW = r"more|higher|hotter|stronger|faster|bigger|larger|greater|above|better|firmer"
_DOWNW = r"less|lower|cooler|softer|weaker|slower|smaller|below|worse|fewer"
COMP = re.compile(rf"\b({_UPW}|{_DOWNW})((?: [a-z]+){{0,2}}) than {_EXP}")          # "fewer jobs than expected"
COMP2 = re.compile(rf"\b(above|beats?|beating|tops?|topping|exceeds?|exceeding|surpass(?:es)?|below|miss(?:es|ed)?|"
                   rf"missing|undershoots?|falls? short of|fell short of) {_EXP}\b")
COMP_UPSET = set(_UPW.split("|")) | {"beat", "beats", "beating", "top", "tops", "topping", "exceed", "exceeds",
                                     "exceeding", "surpass", "surpasses"}
# judgement words (good / bad for the economy) ignore a subject's polarity; size words ("more claims") don't
COMP_QUALITY = re.compile(r"^(?:better|worse|stronger|weaker|beat\w*|miss\w*|falls? short|fell short)")
VS = re.compile(r"(-?\d+(?:\.\d+)?)\s*(%|k|m|bp)?\s*(?:vs\.?|versus)\s*(?:an?\s+)?(?:expected\s+|forecast\s+|"
                r"est\.?\s+|consensus\s+|exp\.?\s+)?(-?\d+(?:\.\d+)?)\s*(%|k|m|bp)?\s*(expected|forecast|est\b|"
                r"estimate|consensus|exp\b|f'cast)?")
STRONG_MAG = re.compile(r"\b(?:surg|soar|plung|tumbl|spik|slump|crash|plummet|jumbo|sharp|far\b|much\b|biggest|"
                        r"record|massive|huge|steep|blowout)\w*")
MILD_MAG = re.compile(r"\b(?:edg(?:e|es|ed|ing)|inch(?:es|ed|ing)?|slight(?:ly)?|modest(?:ly)?|marginal(?:ly)?|"
                      r"mild(?:ly)?|a touch)\b")
FORWARD = re.compile(r"\b(?:expected to|seen|forecast to|likely to|could|might|set to|poised to|preview|"
                     r"week ahead|what to expect|awaits?|awaiting|eyes|in focus|on tap|looms?|due)\b")
FOREIGN = re.compile(r"\b(?:uk|britain|british|euro ?zone|euro area|german\w*|france|french|ital\w*|japan\w*|china|"
                     r"chinese|canad\w*|australia\w*|india\w*|swiss|switzerland|turkey|turkish|russia\w*|brazil\w*|"
                     r"mexic\w*|korea\w*|new zealand|spain|spanish)\b")
US_MARK = re.compile(r"\b(?:us|american|america|nation's|fed)\b")
MACRO_JOBS = re.compile(r"\b(?:us|american|america|nation|economy|economic|data|report|fed|labou?r|payrolls?|nfp|"
                        r"nonfarm|non farm|jobless|unemployment|claims|private|adp|jolts|challenger|wages?)\b")
DOLLAR_SKIP = re.compile(r"(?:billion|million|trillion|\d|-)\s*dollar|dollar (?:general|tree|stores?)")

# (name, pattern, polarity (+1: "up" means USD up), base importance, gold weight)
SUBJECTS = tuple((n, re.compile(p), pol, imp, w) for n, p, pol, imp, w in (
    ("inflation", r"\b(?:core (?:cpi|pce|inflation)|cpi|pce|ppi|inflation|consumer prices?|producer prices?|"
                  r"price pressures?|price index|prices paid)\b", 1, 0.9, 0.8),
    ("jobs_inv", r"\b(?:unemployment(?: rate)?|jobless(?: claims| rate)?|initial claims|continuing claims|"
                 r"weekly claims|layoffs?|job cuts)\b", -1, 0.75, 0.7),
    ("jobs", r"\b(?:non ?farm payrolls?|nonfarm payrolls?|nfp|payrolls?|jobs report|job growth|job gains|hiring|"
             r"employment|jobs|job openings|jolts|adp|wages?|wage growth|hourly earnings|labou?r market)\b", 1, 0.85, 0.75),
    ("growth", r"\b(?:gdp|retail sales|ism|pmi|consumer (?:confidence|sentiment|spending)|durable goods|factory orders|"
               r"industrial production|housing starts|home sales|economy|economic growth|us growth|"
               r"services activity|factory activity)\b", 1, 0.55, 0.5),
    ("yields_inv", r"\b(?:treasuries|treasury prices|bond prices)\b", -1, 0.55, 0.6),
    ("yields", r"\b(?:(?:treasury|bond|10 year|10yr|two year|2 year|30 year|real|us) yields?|yields)\b", 1, 0.6, 0.6),
    ("dollar_inv", r"\b(?:eur|gbp|aud|nzd) ?/? ?usd\b", -1, 0.5, 0.5),
    ("dollar", r"\b(?:(?:us )?dollar index|(?:us )?dollar|greenback|dxy|usd(?: ?/? ?(?:jpy|chf|cad|cnh|cny))?)\b",
     1, 0.6, 0.6),
    ("china", r"\b(?:(?:china|chinese|india|indian)(?:'s)? (?:gold )?(?:demand|imports?|buying|purchases|"
              r"jewell?ery)|(?:gold )?(?:demand|imports?) (?:in|from|by) (?:china|india)|shanghai (?:gold )?premiums?)\b",
     1, 0.5, 0.5),
    ("gold", r"\b(?:spot gold|gold futures|gold prices?|gold|bullion|xau usd|xau)\b", 1, 0.35, 0.4),
))

# --- whole-headline themes
GEO_ESC = re.compile(
    r"(?<!price )(?<!trade )(?<!tariff )(?<!currency )(?<!bidding )(?<!talent )\bwars?\b(?! (?:chest|of words|room))|"
    r"\bmissiles?\b|\bair ?strikes?\b|\bdrone (?:strikes?|attacks?)\b|\b(?:military|missile|nuclear|rocket) "
    r"(?:strikes?|attacks?|tests?|launch\w*)\b|\bstrikes? (?:on|against)\b|\battack(?:s|ed|ing)?\b(?! on (?:the )?fed)|"
    r"\binvasion\b|\binvad\w*|\bescalat\w*|\bhostilities\b|\bbomb(?:s|ing|ings|ed)?\b|\bshelling\b|\bclashes\b|"
    r"\btroops\b|\bmobiliz\w*|\bcoup\b|\bterror\w*|\bblockade\b|\bretaliat\w*|\bwarships?\b|"
    r"\bincursion\b|\bassassinat\w*|\bnuclear (?:threat|war|weapons?|escalation)\b")
GEO_MILD = re.compile(r"\btensions?\b|\bsanctions?\b|\bgeopolitic\w*|\bstandoff\b|\bconflict\b|\bunrest\b|"
                      r"\bthreat(?:en)?s?\b")
GEO_PLACE = re.compile(r"\b(?:iran\w*|israel\w*|gaza|hamas|hezbollah|houthis?|red sea|russia\w*|ukrain\w*|kremlin|"
                       r"putin|zelensk\w*|taiwan\w*|north korea\w*|pyongyang|venezuela\w*|middle east|hormuz|syria\w*|"
                       r"lebanon|yemen|kashmir|nato|pentagon|geopolitic\w*)\b")
GEO_DEESC = re.compile(r"\bcease ?fires?\b|\btruce\b|\barmistice\b|\bpeace\b|\bde ?escalat\w*|"
                       r"\bwithdraw\w* (?:troops|forces)\b|\btroops withdraw\w*|\bhostages? (?:release\w*|deal|freed)\b|"
                       r"\bend(?:s|ing)? (?:of )?(?:the )?war\b|\bdiplomatic (?:solution|breakthrough)\b|"
                       r"\bsanctions relief\b|\blift(?:s|ed|ing)? sanctions\b|\beas(?:e|es|ed|ing) sanctions\b|"
                       r"\bavert\w* (?:a )?(?:wider )?war\b|\bavoid\w* (?:a )?(?:wider )?(?:war|escalation)\b|"
                       r"\btensions? eas\w*|\beas(?:e|es|ed|ing) tensions\b|\bcalm\w* tensions\b")
GEO_FAIL = re.compile(r"\bcollaps\w*|\bfail\w*|\bstall\w*|\bbreak(?:s|ing)? down\b|\bbroke down\b|\bbreakdown\b|"
                      r"\bviolat\w*|\breject\w*|\bshatter\w*|\bcrumbl\w*|\bin doubt\b|\bjeopard\w*|\bsuspend\w*|"
                      r"\bderail\w*|\bfalter\w*|\bno (?:deal|ceasefire|cease fire|truce|progress)\b|"
                      r"\bwithout (?:a )?(?:deal|agreement)\b|\bwalk(?:s|ed)? (?:out|away)\b|"
                      r"\bresum\w* (?:fighting|strikes|attacks|war)\b|\bdespite (?:the )?(?:ceasefire|cease fire|truce)\b|"
                      r"\bend(?:s|ed)? (?:the )?(?:ceasefire|cease fire|truce)\b")
TRADE = re.compile(r"\btariffs?\b|\btrade war\b|\btrade (?:tensions?|dispute|conflict|fight|spat|deal|talks|truce|"
                   r"agreement|pact|negotiations?|barriers)\b|\bexport (?:controls|curbs|ban)\b|\bembargo\b|"
                   r"\bprotectionis\w*|\bsection (?:232|301)\b|\bretaliatory (?:duties|levies)\b")
TRADE_EASE = re.compile(r"\bdeal\b|\btruce\b|\bagreement\b|\bpact\b|\bframework\b|\bpaus\w*|\bdelay(?:s|ed)?\b|"
                        r"\bexempt\w*|\bexclusions?\b|\brelief\b|\broll(?:s|ed)? ?back\b|\blift(?:s|ed|ing)?\b|"
                        r"\b(?:cut|cuts|cutting|lower\w*|reduc\w*|slash\w*) tariffs\b|\beas(?:e|es|ed|ing)\b|"
                        r"\bsuspend\w*|\breprieve\b|\bstrikes? down\b|\bstruck down\b|\bblocks? tariffs\b|\billegal\b")
BANKS = re.compile(r"\bbank (?:failures?|runs?|collapse\w*|crisis|turmoil|stress|rescue|bailout|contagion|seizure)\b|"
                   r"\bbanking (?:crisis|turmoil|stress|contagion|woes|sector (?:turmoil|stress|woes))\b|\bcontagion\b|"
                   r"\bbailout\b|\bdeposit (?:flight|outflows|run)\b|\bcredit (?:crunch|crisis|event)\b|"
                   r"\bliquidity (?:crisis|crunch|squeeze)\b|\b(?:sovereign|debt) default\b|\bdefaults? on\b|"
                   r"\bfdic (?:seizes|takes over)\b|\bprivate credit (?:stress|turmoil|losses|cracks|woes|fears)\b|"
                   r"\b(?:lender|bank)s? (?:collapses?|fails|failed)\b|\bregional banks? (?:shares |stocks )?"
                   r"(?:plunge|tumble|slump|sell ?off|rout|fears|woes|stress)\w*")
CALM = re.compile(r"\b(?:eas(?:e|es|ed|ing)|calm\w*|stabiliz\w*|abat\w*|contained|recover\w*)\b")
FISCAL = re.compile(r"\bgovernment shutdown\b|\b(?:us|federal|government) (?:\w+ )?shutdown\b|\bshutdown (?:looms|"
                    r"threat|risk|deadline|fight)\b|\bdebt (?:ceiling|limit)\b|\b(?:credit )?rating downgrade\b|"
                    r"\bdowngrad\w* (?:the )?(?:us|united states)\b|\b(?:us|sovereign) credit rating\b|"
                    r"\bfiscal (?:crisis|cliff|concerns|worries)\b|\bdeficit (?:fears|worries|concerns|blowout)\b|"
                    r"\bbond vigilantes\b|\bdebt (?:crisis|spiral|fears|worries)\b")
FISCAL_EASE = re.compile(r"\bend(?:s|ed|ing)? (?:the )?(?:government )?shutdown\b|\bavert\w*|\bavoid\w*|"
                         r"\breopen\w*|\b(?:raise[sd]?|suspend\w*|lift\w*) (?:the )?debt (?:ceiling|limit)\b|"
                         r"\bfunding (?:bill|deal) (?:passes|signed|approved)\b|\bstopgap\b")
FED_INDEP = re.compile(r"\b(?:fire[sd]?|firing|oust\w*|remov\w*|replac\w*|sack\w*) (?:fed chair |fed governor |"
                       r"chair |governor )?(?:jerome |lisa )?(?:powell|cook|the fed chair)\b|\bfed independence\b|"
                       r"\bindependence of the (?:fed|federal reserve)\b|\b(?:pressure|attacks?|assault) on (?:the )?"
                       r"(?:fed|federal reserve|powell)\b|\bpoliticiz\w* (?:the )?fed\b|\b(?:trump|white house) "
                       r"(?:slams|blasts|attacks|criticiz\w*|pressures?|pushes|berates) (?:the )?(?:fed|powell)\b")
HAVEN = re.compile(r"\bsafe ?havens?\b|\bhaven (?:demand|buying|flows|bid|appeal|assets?|status)\b|"
                   r"\brisk (?:off|aversion)\b|\bflight to (?:safety|quality)\b")
HAVEN_LOSS = re.compile(r"\b(?:los(?:e|es|ing)|lost|fad(?:e|es|ed|ing)|wan(?:e|es|ed|ing)|dim(?:s|med)?|"
                        r"evaporat\w*|unwind\w*|diminish\w*|ebb\w*)\b")
RISK_OFF = re.compile(r"\b(?:stocks?|equities|wall street|s&p(?: 500)?|nasdaq|dow|global markets?)(?: \w+){0,2} "
                      r"(?:tumbl\w*|plung\w*|slump\w*|sink\w*|sell ?off|selloff|rout\w*|crash\w*|meltdown|turmoil)|"
                      r"\b(?:sell ?off|selloff|rout|crash|turmoil) (?:in|on|across) (?:stocks|equities|wall street|"
                      r"global markets|markets)\b|\bvix (?:jumps|spikes|surges|soars)\b")
CB_GOLD = re.compile(r"\bcentral banks?(?:'s?)?(?: \S+){0,4} gold\b|\bgold(?: \S+){0,3} (?:by|from|of) central banks?\b|"
                     r"\b(?:pboc|people's bank of china|china's central bank|rbi|reserve bank of india|"
                     r"national bank of poland|nbp|czech national bank|cbrt|bank of russia|central bank of \w+|"
                     r"official sector|monetary authority of singapore)(?: \S+){0,5} gold\b|\bgold reserves\b|"
                     r"\bofficial gold\b|\bcentral bank (?:gold )?(?:demand|buying|purchases)\b")
CB_BUY = re.compile(r"\b(?:buy\w*|bought|purchas\w*|add\w*|accumulat\w*|boost\w*|rais\w*|increas\w*|ris(?:e|es|ing)|"
                    r"rose|grow\w*|extend\w*|stock(?:s|ing)? up|demand|hoard\w*|record|streak|"
                    r"\d+(?:st|nd|rd|th) (?:straight |consecutive )?month)\b")
CB_SELL = re.compile(r"\b(?:sell\w*|sold|halt\w*|paus\w*|stop\w*|reduc\w*|slow\w*|declin\w*|fall\w*|fell|drop\w*|"
                     r"trim\w*|offload\w*|dump\w*|unload\w*|liquidat\w*)\b")
ETF = re.compile(r"\b(?:gold )?etfs?\b|\bexchange traded funds?\b|\bspdr gold\b|\bgld\b|\biau\b|\bishares gold\b|"
                 r"\bgold (?:funds?|trust)\b|\bgold holdings\b")
ETF_GOLD = re.compile(r"\b(?:gold|bullion|gld|iau|spdr)\b")
ETF_IN = re.compile(r"\binflows?\b|\bholdings (?:rise|rose|climb\w*|increas\w*|grow\w*|jump\w*|swell\w*|hit)\b|"
                    r"\b(?:add\w*|buying|attract\w*)\b|\bpour\w* into\b|\brecord (?:inflows|holdings)\b")
ETF_OUT = re.compile(r"\boutflows?\b|\bholdings (?:fall|fell|drop\w*|declin\w*|slip\w*|shrink\w*|sink\w*)\b|"
                     r"\bredemptions?\b|\bwithdrawals?\b|\bsell\w*|\bsold\b|\bbleed\w*|\bexodus\b|\bdump\w*")
FCAST_UP = re.compile(r"\b(?:rais\w*|lift\w*|boost\w*|hik\w*|ups|upgrad\w*|bump\w*)(?: \S+){0,3} gold (?:price )?"
                      r"(?:forecasts?|targets?|outlook|view)\b|\bgold (?:price )?(?:forecast|target) (?:raised|lifted|"
                      r"upgraded)\b")
FCAST_DOWN = re.compile(r"\b(?:cut\w*|lower\w*|trim\w*|slash\w*|downgrad\w*|reduc\w*)(?: \S+){0,3} gold (?:price )?"
                        r"(?:forecasts?|targets?|outlook|view)\b")
JUNK = re.compile(r"\bgold (?:rates?|prices?) today in\b|\bgold rates? today\b|\btoday'?s? gold rates?\b|"
                  r"\b(?:18|22|24) ?(?:k|kt|carat|ct)\b|\btola\b|\bper (?:10 )?grams?\b|\bgold (?:medal|card|coast|cup|"
                  r"glove|star)s?\b|\bgolden\b|\bolympic\w*|\bsovereign gold bond\b|\bakshaya\b|\bdhanteras\b")
BREAKING = re.compile(r"^(?:breaking|urgent|just in|flash|alert)\b")

# Ported from scalper/brain/macro_watchdog.py (MacroWatchdog.assess_headline_heuristic): its lists, as a last layer.
WATCHDOG_HIGH = ["cpi", "inflation", "fomc", "fed rate", "powell", "war", "missile", "nfp", "payroll"]
WATCHDOG_USD_UP = ["cpi hotter", "inflation rises", "hike rates", "strong jobs", "hawkish"]
WATCHDOG_USD_DOWN = ["cpi cools", "inflation falls", "cut rates", "weak jobs", "dovish", "geopolitical tension",
                     "safe haven"]

R = {   # (theme, sign) -> (one-line reason, short label)
    ("fed", -1): ("Hawkish Fed (higher-for-longer) → stronger USD / real yields up → bearish gold", "hawkish Fed"),
    ("fed", 1): ("Dovish Fed (cuts priced in) → weaker USD / real yields down → bullish gold", "dovish Fed"),
    ("fed_nocut", -1): ("Fed not cutting (cut hopes fade) → USD and real yields stay firm → bearish gold",
                        "no Fed cut"),
    ("fed", 0): ("Fed event: the tone sets the direction (hawkish → gold down, dovish → gold up)", "Fed in focus"),
    ("cb", -1): ("Dovish {cb} → its currency down, USD up → mildly bearish gold", "dovish {cb}"),
    ("cb", 1): ("Hawkish {cb} → its currency up, USD down → mildly bullish gold", "hawkish {cb}"),
    ("pboc", 1): ("PBOC easing → China growth / demand support → mildly bullish gold", "PBOC easing"),
    ("pboc", -1): ("PBOC tightening → less China support → mildly bearish gold", "PBOC tightening"),
    ("inflation", -1): ("Hotter inflation → Fed stays restrictive, USD / real yields up → bearish gold",
                        "hot inflation"),
    ("inflation", 1): ("Cooler inflation → room for Fed cuts, USD / yields down → bullish gold", "cool inflation"),
    ("inflation", 0): ("Inflation data in focus: hotter → gold down, cooler → gold up", "inflation in focus"),
    ("jobs", -1): ("Strong labour data → fewer Fed cuts priced, USD up → bearish gold", "strong jobs"),
    ("jobs", 1): ("Weak labour data → more Fed cuts priced, USD down → bullish gold", "weak jobs"),
    ("jobs", 0): ("Labour data in focus: strong → gold down, weak → gold up", "jobs in focus"),
    ("growth", -1): ("Strong US data → USD and yields up → mildly bearish gold", "strong US data"),
    ("growth", 1): ("Weak US data → cut bets rise, USD down → mildly bullish gold", "weak US data"),
    ("growth", 0): ("US data in focus: strong → gold down, weak → gold up", "US data in focus"),
    ("yields", -1): ("Higher yields raise the cost of holding gold → bearish gold", "higher yields"),
    ("yields", 1): ("Lower yields cut the cost of holding gold → bullish gold", "lower yields"),
    ("yields", 0): ("Yields in focus: up → gold down, down → gold up", "yields in focus"),
    ("dollar", -1): ("Stronger USD makes gold dearer abroad → bearish gold", "stronger USD"),
    ("dollar", 1): ("Weaker USD makes gold cheaper abroad → bullish gold", "weaker USD"),
    ("dollar", 0): ("Dollar in focus: up → gold down, down → gold up", "dollar in focus"),
    ("global", 1): ("Firm foreign data → foreign currencies up, USD softer → mildly bullish gold", "firm global data"),
    ("global", -1): ("Weak foreign data → USD firmer → mildly bearish gold", "weak global data"),
    ("global", 0): ("Foreign data: small effect on gold through the dollar", "global data"),
    ("geo", 1): ("Geopolitical escalation → safe-haven demand → bullish gold", "escalation"),
    ("geo", -1): ("De-escalation (ceasefire / talks) → haven premium unwinds → bearish gold", "de-escalation"),
    ("geo_fail", 1): ("Peace effort falters → tensions stay → bullish gold", "peace effort falters"),
    ("cbgold", 1): ("Central-bank gold buying → steady structural demand → bullish gold", "central-bank buying"),
    ("cbgold", -1): ("Central banks slowing or selling gold → less structural demand → bearish gold",
                     "central-bank selling"),
    ("cbgold", 0): ("Central-bank gold reserves in the news: structural demand theme", "central-bank gold"),
    ("etf", 1): ("Gold ETF inflows → investor demand → bullish gold", "ETF inflows"),
    ("etf", -1): ("Gold ETF outflows → investors selling → bearish gold", "ETF outflows"),
    ("trade", 1): ("Tariff / trade-war escalation → uncertainty and haven demand → bullish gold", "tariff escalation"),
    ("trade", -1): ("Trade truce / tariff relief → risk-on, less haven demand → bearish gold", "trade relief"),
    ("banks", 1): ("Banking / credit stress → haven demand and Fed-cut bets → bullish gold", "bank stress"),
    ("banks", -1): ("Banking stress easing → less haven demand → mildly bearish gold", "bank stress easing"),
    ("fiscal", 1): ("US fiscal / shutdown risk → doubts on USD, haven demand → bullish gold", "US fiscal risk"),
    ("fiscal", -1): ("US fiscal risk eases → less haven demand → mildly bearish gold", "fiscal relief"),
    ("fedindep", 1): ("Pressure on Fed independence → USD credibility doubts → bullish gold", "Fed independence"),
    ("haven", 1): ("Safe-haven flows → bullish gold", "haven flows"),
    ("haven", -1): ("Haven demand fading → bearish gold", "haven demand fades"),
    ("risk", 1): ("Risk-off in stocks → some haven demand → mildly bullish gold", "risk-off"),
    ("china", 1): ("Stronger China / India demand → bullish gold", "Asian demand up"),
    ("china", -1): ("Weaker China / India demand → bearish gold", "Asian demand down"),
    ("china", 0): ("China / India gold demand in the news", "Asian demand"),
    ("gold", 1): ("Gold price report: gold rising (describes the move, not a cause)", "gold rising"),
    ("gold", -1): ("Gold price report: gold falling (describes the move, not a cause)", "gold falling"),
    ("gold", 0): ("Gold price report", "gold"),
    ("fcast", 1): ("A bank raises its gold forecast → sentiment support → mildly bullish gold", "forecast raised"),
    ("fcast", -1): ("A bank cuts its gold forecast → mildly bearish gold", "forecast cut"),
    ("watchdog", 1): ("Watchdog rule: USD-negative headline → bullish gold", "USD-negative"),
    ("watchdog", -1): ("Watchdog rule: USD-positive headline → bearish gold", "USD-positive"),
    ("macro", 0): ("High-impact macro headline: expect volatility, direction unclear", "macro headline"),
}
TAG = {"fed": "fed", "fed_nocut": "fed", "cb": "central banks", "pboc": "central banks", "inflation": "inflation",
       "jobs": "jobs", "growth": "growth", "yields": "yields", "dollar": "dollar", "global": "global data",
       "geo": "geopolitics", "geo_fail": "geopolitics", "cbgold": "central-bank gold", "etf": "etf",
       "trade": "tariffs", "banks": "banks", "fiscal": "fiscal", "fedindep": "fed independence", "haven": "haven",
       "risk": "risk-off", "china": "china demand", "gold": "gold price", "fcast": "forecasts", "watchdog": "macro"}


def _sgn(x: float) -> int:
    return (x > 1e-9) - (x < -1e-9)


def _clauses(s: str, comma: bool = True) -> list:
    """Split a headline into clauses -> [text]."""
    out, pos = [], 0
    for m in (_BOUND if comma else _BOUND_NC).finditer(s):
        out.append(s[pos:m.start()])
        pos = m.end()
    out.append(s[pos:])
    return [c for c in out if c.strip()]


def _before(text: str, pos: int, n: int = 5) -> str:
    return " ".join(text[:pos].split()[-n:])


def _after(text: str, end: int, n: int = 4) -> str:
    return " ".join(text[end:].split()[:n])


def _odd_neg(text: str) -> bool:
    return len(NEG.findall(text)) % 2 == 1


def _negated(text: str, m) -> bool:
    return _odd_neg(_before(text, m.start()))


_BETS = re.compile(r"\b(?:bets?|hopes?|expectations?|odds|pricing|wagers?|chances|calls|talk)\b")
_BET_DOWN = re.compile(r"\b(?:fade[sd]?|fading|wane[sd]?|waning|dim(?:s|med)?|reced\w*|evaporat\w*|vanish\w*|"
                       r"dash(?:ed|es)?|pared|trimmed|slashed|unwound|ebb(?:s|ed)?|cool(?:s|ed)?|fall(?:s|ing)?|fell|"
                       r"drop(?:s|ped)?|slid(?:e|es)?|declin\w*|tumbl\w*|sink(?:s|ing)?|sank|shrink\w*|lower\w*|"
                       r"retreat\w*|plung\w*|collaps\w*)\b")


def _faded(text: str, m) -> bool:
    """'rate cut bets fade', 'hike hopes dim', 'cuts priced out'."""
    aft = _after(text, m.end())
    if re.search(r"\bnot (?:off the table|priced out)\b", aft):
        return False
    return bool((_BETS.search(m.group(0) + " " + aft) and _BET_DOWN.search(aft)) or
                re.search(r"\bpriced out\b|\boff the table\b", aft))


def _stance(clause: str) -> tuple:
    """Dovish (+) / hawkish (-) score of one clause, and whether the hawkish read is only a negated or faded cut
    ("not expected to cut")."""
    score, nocut, spans = 0.0, False, []
    for pats, sign in ((HAWK, -1), (DOVE, 1)):
        for rx, w in pats:
            for m in rx.finditer(clause):
                if any(a < m.end() and m.start() < b for a, b in spans):
                    continue
                spans.append((m.start(), m.end()))
                ctx = _before(clause, m.start())
                if sign > 0:                      # "sees no cuts": a negation inside a dovish phrase counts too
                    ctx += " " + clause[m.start():m.end()]
                if _odd_neg(ctx) != _faded(clause, m):
                    score += -sign * w * 0.6
                    nocut = nocut or sign > 0
                else:
                    score += sign * w
    return score, nocut and score < 0


def _subjects(cl: str) -> list:
    """Non-overlapping subject mentions in a clause, longest first -> [(start, end, name, pol, imp, w)]."""
    found = []
    for name, rx, pol, imp, w in SUBJECTS:
        for m in rx.finditer(cl):
            if name.startswith("dollar") and DOLLAR_SKIP.search(cl[max(0, m.start() - 12):m.end() + 8]):
                continue
            found.append((m.start(), m.end(), name, pol, imp, w))
    out = []
    for f in sorted(found, key=lambda x: x[0] - x[1]):
        if not any(f[0] < o[1] and o[0] < f[1] for o in out):
            out.append(f)
    return out


def _cues(cl: str, tok_at, subs: list) -> list:
    """Direction words in a clause -> [(token, usd direction for a +1 subject, quality?, surprise?, start, end)]."""
    cues = []
    for rx, d, qual in ((LEVEL_UP, 1, False), (LEVEL_DOWN, -1, False), (QUAL_UP, 1, True), (QUAL_DOWN, -1, True),
                        (FLAT, 0, False)):
        for m in rx.finditer(cl):
            cues.append((tok_at(m.start()), d, qual, False, m.start(), m.end()))
    for m in COMP.finditer(cl):
        word = m.group(1)
        d = 1 if word in COMP_UPSET else -1
        if word in ("more", "less") and not m.group(2).strip():          # "cools more than expected"
            prev = [c for c in cues if not c[3] and c[1] and c[5] <= m.start() and tok_at(m.start()) - c[0] <= 2]
            if prev:
                d = prev[-1][1] * (1 if word == "more" else -1)
        cues.append((tok_at(m.start()), d, bool(COMP_QUALITY.match(word)), True, m.start(), m.end()))
    for m in COMP2.finditer(cl):
        cues.append((tok_at(m.start()), 1 if m.group(1) in COMP_UPSET else -1, bool(COMP_QUALITY.match(m.group(1))),
                     True, m.start(), m.end()))
    for m in VS.finditer(cl):
        if m.group(5) or re.search(r"expected|forecast|est|consensus", m.group(0)):
            cues.append((tok_at(m.start()), _sgn(float(m.group(1)) - float(m.group(3))), True, True,
                         m.start(), m.end()))
    # cue words inside a subject's own words don't count ("job cuts", "price index")
    return [c for c in cues if not any(sa <= c[4] and c[5] <= sb for sa, sb, *_ in subs)]


def _best_cue(cues: list, ti: int, te: int):
    """A surprise within 8 words wins; else the nearest word up to 5 after or 3 before the subject."""
    comps = [c for c in cues if c[3] and (abs(c[0] - ti) <= 8 or abs(c[0] - te) <= 8)]
    best = None
    for c in comps or cues:
        dist = c[0] - te if c[0] > te else ti - c[0]
        if not comps and ((c[0] > te and dist > 5) or (c[0] < ti and dist > 3)):
            continue
        key = dist if c[0] > te else dist + 0.5
        if best is None or key < best[0]:
            best = (key, c)
    return best[1] if best else None


def _level_components(s: str) -> list:
    """Subjects (inflation, jobs, yields, the dollar, gold...) and the word that moves them, clause by clause ->
    [(name, polarity, importance, gold weight, usd direction x magnitude, surprise?, foreign?)]."""
    comps, carry = [], []           # carry: the previous clause's subjects that found no word ("US CPI: hotter ...")
    for cl in _clauses(s, comma=False):
        starts = [m.start() for m in re.finditer(r"\S+", cl)]
        tok_at = lambda p, st=starts: sum(1 for a in st if a <= p) - 1           # noqa: E731
        subs = _subjects(cl)
        cues = _cues(cl, tok_at, subs)
        fwd = bool(FORWARD.search(cl)) and not any(c[3] for c in cues)
        mag = 1.0 if STRONG_MAG.search(cl) else (0.45 if MILD_MAG.search(cl) else 0.75)
        if fwd:
            mag = 0.0                     # "CPI expected to rise": a forecast is not news; the print will be
        if not subs:
            for idx, (name, pol, imp, w, foreign) in carry:
                c = _best_cue(cues, -1, -1)
                if c is not None:
                    usd = c[1] * (1 if c[2] else pol)
                    comps[idx] = (name, pol, min(1.0, imp + (0.1 if c[3] else 0)), w, usd * mag, c[3], foreign)
            carry = []
            continue
        carry = []
        foreign = bool(FOREIGN.search(cl)) and not US_MARK.search(cl)
        for a, b, name, pol, imp, w in subs:
            if name.startswith("jobs") and not MACRO_JOBS.search(s):
                continue                                  # "Apple adds 1,000 jobs" is not the labour market
            if name == "growth" and not re.search(r"\b(?:us|american|economy|gdp|ism|pmi|retail|data|report|fed)\b", s):
                continue
            c = _best_cue(cues, tok_at(a), tok_at(b - 1))
            if c is None:
                comps.append((name, pol, imp * 0.7, w, 0.0, False, foreign))
                carry.append((len(comps) - 1, (name, pol, imp, w, foreign)))
                continue
            usd = c[1] * (1 if c[2] else pol)
            comps.append((name, pol, min(1.0, imp + (0.1 if c[3] else 0)), w, usd * mag, c[3], foreign))
    return comps


_CB_NAMES = {"lagarde": "ECB", "european central bank": "ECB", "ecb": "ECB", "boe": "BoE", "bank of england": "BoE",
             "boj": "BoJ", "bank of japan": "BoJ", "ueda": "BoJ", "snb": "SNB", "swiss national bank": "SNB",
             "boc": "BoC", "bank of canada": "BoC", "rba": "RBA", "reserve bank of australia": "RBA", "rbnz": "RBNZ",
             "rbi": "RBI", "reserve bank of india": "RBI", "riksbank": "Riksbank", "norges bank": "Norges Bank"}


def _cb_name(m: str) -> str:
    return _CB_NAMES.get(m, m.title())


def analyze_headline(title: str) -> dict:
    """One headline -> {"importance": 0..1, "impact": -1..+1 for gold, "tags": [...], "why": one line, "label"}.
    Rules only: every number comes from a keyword list in this file."""
    s = _norm(title)
    none = {"importance": 0.0, "impact": 0.0, "tags": [], "why": "not about gold's drivers", "label": ""}
    if not s or JUNK.search(s):
        return none
    comps = []      # dicts: theme, imp, d (gold direction), key (reason), cb (name), surprise

    def add(theme, imp, d, key, cb=None, surprise=False):
        comps.append({"theme": theme, "imp": imp, "d": d, "key": key, "cb": cb, "surprise": surprise})

    # central banks: stance per clause, owned by the bank named in it (else the one before; the Fed by default)
    fed_ctx, actor, fed_score, fed_nocut, foreign = bool(FED_ANY.search(s)), "fed", 0.0, False, {}
    for cl in _clauses(s):
        fm = FOREIGN_CB.search(cl)
        if fm:
            actor = fm.group(0)
        elif FED_ANY.search(cl):
            actor = "fed"
        sc, nocut = _stance(cl)
        if sc and actor == "fed":
            fed_score += sc
            fed_nocut = fed_nocut or nocut
        elif sc:
            foreign[actor] = foreign.get(actor, 0.0) + sc
    if fed_score or (fed_ctx and FED_POLICY.search(s)):
        st = max(-1.0, min(1.0, fed_score))
        key = ("fed_nocut", -1) if fed_nocut and st < 0 and fed_score > -0.9 else ("fed", _sgn(st))
        add("fed", 0.9 if FED_CORE.search(s) else 0.75 if fed_ctx else 0.7, st * 0.85, key)
    for cb, sc in foreign.items():
        st = max(-1.0, min(1.0, sc))
        if not st:
            continue
        if CHINA_CB.search(cb):
            add("pboc", 0.45, st * 0.25, ("pboc", _sgn(st)))
        else:
            add("cb", 0.45, -st * 0.35, ("cb", -_sgn(st)), cb=_cb_name(cb))

    # data, yields, the dollar, gold's own price, Asian demand
    for name, pol, imp, w, usd, surprise, frgn in _level_components(s):
        theme = name.replace("_inv", "")
        if theme in ("inflation", "jobs", "growth", "yields") and frgn:
            add("global", imp * 0.5, 0.25 * usd, ("global", _sgn(usd)))
        elif theme in ("gold", "china"):
            add(theme, imp, w * usd, (theme, _sgn(usd)), surprise=surprise)
        else:
            add(theme, imp, -w * usd, (theme, -_sgn(usd)), surprise=surprise)

    # geopolitics
    trade = TRADE.search(s)
    deesc = GEO_DEESC.search(s)
    fedind = FED_INDEP.search(s)
    esc = [m for m in GEO_ESC.finditer(s) if not (trade and re.match(r"retaliat|escalat", m.group(0))) and
           not (fedind and m.group(0).startswith("attack"))]
    if deesc and trade and not (GEO_PLACE.search(s) or esc):
        deesc = None                                     # "tariff truce" is trade, not war
    if deesc:
        if GEO_FAIL.search(s) or FADE.search(_after(s, deesc.end())) or _odd_neg(_before(s, deesc.start(), 3)):
            add("geo", 0.75, 0.7, ("geo_fail", 1))
        else:
            add("geo", 0.7, -0.6, ("geo", -1))
    elif esc:
        strong = re.search(r"\bwars?\b|missile|strike|invasion|invad|nuclear|attack|bomb", s)
        add("geo", 0.8 if strong else 0.7, 0.7, ("geo", 1))
    elif GEO_MILD.search(s) and GEO_PLACE.search(s) and not trade:
        add("geo", 0.55, 0.45, ("geo", 1))

    if trade:
        ease = TRADE_EASE.search(s)
        failed = ease and (GEO_FAIL.search(s) or FADE.search(_after(s, ease.end())) or
                           _odd_neg(_before(s, ease.start(), 3)))
        imp = 0.65 if re.search(r"\btariffs?\b|\btrade war\b|\bexport (?:controls|curbs|ban)\b|\bembargo\b", s) else 0.5
        if ease and not failed:
            add("trade", imp, -0.45, ("trade", -1))
        else:
            add("trade", imp, 0.5, ("trade", 1))
    if BANKS.search(s):
        if CALM.search(s):
            add("banks", 0.5, -0.25, ("banks", -1))
        else:
            add("banks", 0.7, 0.6, ("banks", 1))
    if FISCAL.search(s):
        if FISCAL_EASE.search(s):
            add("fiscal", 0.5, -0.3, ("fiscal", -1))
        else:
            add("fiscal", 0.55, 0.45, ("fiscal", 1))
    if fedind:
        add("fedindep", 0.7, 0.6, ("fedindep", 1))
    if HAVEN.search(s):
        lost = HAVEN_LOSS.search(s)
        add("haven", 0.5, -0.35 if lost else 0.5, ("haven", -1 if lost else 1))
    if RISK_OFF.search(s):
        add("risk", 0.4, 0.25, ("risk", 1))
    cbg = CB_GOLD.search(s)
    if cbg:
        sell = CB_SELL.search(s)
        if sell and not _negated(s, sell):
            add("cbgold", 0.6, -0.5, ("cbgold", -1))
        elif CB_BUY.search(s):
            add("cbgold", 0.6, 0.55, ("cbgold", 1))
        else:
            add("cbgold", 0.5, 0.0, ("cbgold", 0))
    if ETF.search(s) and ETF_GOLD.search(s) and not cbg:
        if ETF_OUT.search(s):
            add("etf", 0.5, -0.5, ("etf", -1))
        elif ETF_IN.search(s):
            add("etf", 0.5, 0.5, ("etf", 1))
    if FCAST_UP.search(s):
        add("fcast", 0.4, 0.3, ("fcast", 1))
    elif FCAST_DOWN.search(s):
        add("fcast", 0.4, -0.3, ("fcast", -1))

    # a gold-price line next to a gold-specific cause is the cause's echo: keep the cause only
    if any(c["theme"] in ("cbgold", "etf", "china", "fcast") for c in comps):
        comps = [c for c in comps if c["theme"] != "gold"]

    # the watchdog's lists (scalper/brain/macro_watchdog.py), as a last layer
    if not any(abs(c["d"]) > 0.05 for c in comps):
        if any(k in s for k in WATCHDOG_USD_UP):
            add("watchdog", 0.7, -0.6, ("watchdog", -1))
        elif any(k in s for k in WATCHDOG_USD_DOWN):
            add("watchdog", 0.7, 0.6, ("watchdog", 1))
        elif not comps and any(re.search(rf"\b{re.escape(k)}", s) for k in WATCHDOG_HIGH):
            add("watchdog", 0.5, 0.0, ("macro", 0))
    if not comps:
        return none

    themes = {c["theme"] for c in comps}
    surprise = any(c["surprise"] for c in comps)
    imp = max(c["imp"] for c in comps) + 0.06 * (len(themes) - 1) + (0.05 if surprise else 0) + \
        (0.1 if BREAKING.search(s) else 0)
    imp = max(0.0, min(1.0, imp))
    directional = [c for c in comps if abs(c["d"]) > 0.02]
    d = sum(c["imp"] * c["d"] for c in directional) / sum(c["imp"] for c in directional) if directional else 0.0
    d = max(-1.0, min(1.0, d))
    lead = max(directional, key=lambda c: c["imp"] * abs(c["d"])) if directional else \
        max(comps, key=lambda c: c["imp"])
    why, label = (x.format(cb=lead["cb"] or "") for x in R.get(lead["key"], R[("macro", 0)]))
    against = [c for c in directional if _sgn(c["d"]) == -_sgn(lead["d"]) and c["imp"] * abs(c["d"]) >= 0.2]
    if against and directional:
        o = max(against, key=lambda c: c["imp"] * abs(c["d"]))
        why += f" (against: {R.get(o['key'], R[('macro', 0)])[1].format(cb=o['cb'] or '')})"
    tags = sorted({TAG[c["theme"]] for c in comps} | ({"surprise"} if surprise else set()) |
                  ({"breaking"} if BREAKING.search(s) else set()))
    return {"importance": round(imp, 2), "impact": round(d, 2), "tags": tags, "why": why, "label": label}


# ================================================================== the calendar's events
EVENT_KINDS = tuple((k, re.compile(p)) for k, p in (
    ("speech", r"speaks|speech|testif|press conference|minutes|statement|projections|beige book|monetary policy report|"
               r"jackson hole|fomc member|fed chair|powell|lagarde|ueda|bailey"),
    ("rate", r"funds rate|rate decision|interest rate|refinancing rate|cash rate|bank rate|policy rate|"
             r"deposit (?:facility )?rate|loan prime rate|overnight rate|official bank rate"),
    ("jobs_inv", r"unemployment|jobless|claims|challenger|job cuts"),
    ("inflation", r"cpi|pce|ppi|inflation|price index|prices|hourly earnings|employment cost|deflator"),
    ("jobs", r"non ?-?farm|nfp|employment change|payrolls|jolts|job openings|adp|employment"),
    ("growth", r"gdp|retail sales|ism|pmi|durable|confidence|sentiment|production|housing|building|home sales|factory|"
               r"philly|empire|manufacturing|services|spending|income|orders"),
))
WORDS = {"inflation": ("hotter", "cooler"), "jobs": ("stronger", "weaker"), "jobs_inv": ("higher", "lower"),
         "growth": ("stronger", "weaker"), "rate": ("higher", "lower")}
POLARITY = {"inflation": 1, "jobs": 1, "jobs_inv": -1, "growth": 1, "rate": 1}
CCY_CB = {"EUR": "EUR", "GBP": "GBP", "JPY": "JPY", "CNY": "CNY", "AUD": "AUD", "CAD": "CAD", "CHF": "CHF",
          "NZD": "NZD"}
EVENT_FOCUS = {"inflation": ("inflation",), "jobs": ("jobs",), "jobs_inv": ("jobs",), "growth": ("growth",),
               "rate": ("fed",), "speech": ("fed",)}


def event_kind(title: str) -> str | None:
    t = (title or "").lower()
    for k, rx in EVENT_KINDS:
        if rx.search(t):
            return k
    return None


def _num(v) -> tuple:
    """'0.3%' -> (0.3, '%'); '250K' -> (250000.0, 'k'); '-1.2B' -> (-1.2e9, 'b'); None if no number."""
    m = re.match(r"^\s*[<>]?\s*(-?\d+(?:\.\d+)?)\s*([%kmbt]?)", str(v or ""), re.I)
    if not m:
        return None, ""
    unit = m.group(2).lower()
    return float(m.group(1)) * {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}.get(unit, 1.0), unit


def _scale(kind: str, f: float, unit: str) -> float:
    if unit == "%":
        return {"inflation": 0.1, "jobs_inv": 0.1, "jobs": 0.1, "growth": 0.3, "rate": 0.25}.get(kind, 0.2)
    if unit == "k":
        return {"jobs": 50e3, "jobs_inv": 12e3}.get(kind, max(1e3, 0.15 * abs(f)))
    if unit in ("m", "b", "t"):
        return 0.3e6 if kind == "jobs" and unit == "m" else max(1.0, 0.05 * abs(f))
    return 6.0 if abs(f) < 30 else max(1.0, 0.03 * abs(f))           # indexes: PMI ~50, Philly / Empire around 0


def surprise(ev: dict) -> tuple:
    """A released event's actual against its forecast (else previous, at half weight) ->
    (gold direction -1..+1, text) or (0.0, None) without numbers."""
    kind = event_kind(ev.get("title"))
    a, ua = _num(ev.get("actual"))
    f, uf = _num(ev.get("forecast"))
    base, what, w = (f, "forecast", 1.0) if f is not None else (_num(ev.get("previous"))[0], "previous", 0.5)
    if a is None or base is None or kind not in POLARITY:
        return 0.0, None
    unit = ua or uf
    z = (a - base) / _scale(kind, base, unit)
    up, down = WORDS[kind]
    if abs(z) < 0.5:
        return 0.0, f"{ev.get('actual')} vs {ev.get(what)} {what}: in line → little news for gold"
    size = "big" if abs(z) >= 3 else ("clear" if abs(z) >= 1.5 else "small")
    word = up if z > 0 else down
    mag = min(1.0, abs(z) / 2) * w
    usd = POLARITY[kind] * _sgn(z)
    if ev.get("country") == "USD":
        g = -usd * mag
        eff = f"USD {'up' if usd > 0 else 'down'}, gold {'down' if g < 0 else 'up'}"
    else:
        g = usd * mag * 0.4
        ccy = ev.get("country") or "?"
        eff = f"{ccy} {'up' if usd > 0 else 'down'}, gold slightly {'up' if g > 0 else 'down'}"
    return round(g, 2), f"{ev.get('actual')} vs {ev.get(what)} {what}: {word} ({size} surprise) → {eff}"


def _ny_offset(utc: int) -> int:
    """New York's UTC offset in seconds (US DST: 2nd Sunday of March 07:00 UTC to 1st Sunday of November 06:00 UTC)."""
    y = datetime.fromtimestamp(utc, tz=timezone.utc).year
    mar = datetime(y, 3, 8, tzinfo=timezone.utc)
    nov = datetime(y, 11, 1, tzinfo=timezone.utc)
    start = mar + timedelta(days=(6 - mar.weekday()) % 7, hours=7)
    end = nov + timedelta(days=(6 - nov.weekday()) % 7, hours=6)
    return -4 * 3600 if start.timestamp() <= utc < end.timestamp() else -5 * 3600


def ny_clock(utc: int) -> str:
    return datetime.fromtimestamp(utc + _ny_offset(utc), tz=timezone.utc).strftime("%H:%M")


def countdown(sec: float) -> str:
    """Same wording as the Calendar node: 42 min, 2 h 5 min, 1 d 3 h."""
    m = int(max(0, sec)) // 60
    return f"{m} min" if m < 90 else (f"{m // 60} h {m % 60} min" if m < 1440 else f"{m // 1440} d {m % 1440 // 60} h")


def _ago(sec: float) -> str:
    m = int(max(0, sec)) // 60
    return "just now" if m < 1 else (f"{m} min ago" if m < 90 else f"{m // 60} h {m % 60} min ago")


def event_effect(ev: dict, now: int, full: bool = False, headlines: list | None = None) -> str:
    """What the event means for gold: the beat / miss map before it, the surprise after it."""
    kind = event_kind(ev.get("title"))
    ccy, t = ev.get("country") or "", int(ev["t"])
    f = ev.get("forecast")
    if t <= now:
        g, txt = surprise(ev)
        if txt:
            return txt
        ago = now - t
        if ago <= 2 * 3600:
            s = f"released {_ago(ago)}; the feeds don't carry the actual yet"
            for h in headlines or []:
                if h["t"] >= t - 300 and abs(h["dir"]) > 0.15 and \
                        set(EVENT_FOCUS.get(kind, ())) & set(h.get("themes", ())):
                    s += f". Headlines read it {'gold-positive' if h['dir'] > 0 else 'gold-negative'}: " \
                         f"{h['label']} (“{h['title'][:80]}”)"
                    break
            return s
        return f"released at {ny_clock(t)} NY"
    than = f"than {f}" if f else "than expected"
    if ccy != "USD":
        if ccy == "CNY":
            m = f"stronger {than} → China demand / risk appetite → gold slightly up; weaker → slightly down"
        elif kind == "speech":
            m = f"hawkish tone → {ccy} up, USD softer → gold slightly up; dovish → slightly down"
        elif kind in POLARITY:
            up, down = WORDS[kind]
            pol = POLARITY[kind]
            m = f"{up} {than} → {ccy} {'up' if pol > 0 else 'down'}, gold slightly {'up' if pol > 0 else 'down'}; " \
                f"{down} → the reverse"
        else:
            m = f"a big surprise moves {ccy}; gold slightly the other way to USD"
    elif kind == "speech":
        m = "hawkish tone → USD up, gold down; dovish → gold up"
    elif kind == "rate":
        m = (f"a hike above {f} → gold down; a cut below → gold up; at {f} the statement's tone decides" if f else
             "a hike → gold down; a cut → gold up; the statement's tone decides")
    elif kind == "jobs_inv":
        m = f"higher {than} (weaker labour) → USD down, gold up; lower → gold down"
    elif kind in WORDS:
        up, down = WORDS[kind]
        m = f"{up} {than} → USD up, gold down; {down} → gold up"
    else:
        m = "a big surprise moves USD; gold usually the other way"
    if not full:
        return m
    s = f"{ev.get('title')} {ny_clock(t)} NY in {countdown(t - now)}: {m}."
    if ccy == "USD" and ev.get("impact") == "High":
        s += f" Expect a spread widening and a whipsaw; no new entries from {WAIT_BEFORE} min before to " \
             f"{WAIT_AFTER} min after."
    return s


def _ev_out(ev: dict, now: int, full: bool = False, headlines: list | None = None) -> dict:
    t = int(ev["t"])
    return {"title": ev.get("title"), "country": ev.get("country"), "impact": ev.get("impact"), "time_utc": t,
            "in_min": int(round((t - now) / 60)), "forecast": ev.get("forecast"), "previous": ev.get("previous"),
            "actual": ev.get("actual"), "gold_effect": event_effect(ev, now, full, headlines)}


def _decay(age_s: float, half_min: float) -> float:
    return 0.5 ** (max(0.0, age_s) / 60 / half_min)


# ================================================================== de-duplication
_STOP = {"the", "a", "an", "to", "of", "in", "on", "for", "and", "as", "at", "by", "with", "from", "is", "are", "after",
         "amid", "its", "says", "say", "said", "us", "over", "into", "new", "report", "reports", "update", "live", "be",
         "will", "this", "that", "it", "what", "why", "how"}


def _tokens(title: str) -> frozenset:
    return frozenset(w for w in re.findall(r"[a-z0-9%.]+", _norm(title)) if w not in _STOP and len(w) > 1)


def same_story(a: str, b: str, ta: frozenset | None = None, tb: frozenset | None = None) -> bool:
    """Near-identical titles: token overlap (Jaccard) >= 0.6, or 85 % the same characters."""
    ta, tb = ta if ta is not None else _tokens(a), tb if tb is not None else _tokens(b)
    if not ta or not tb:
        return _norm(a) == _norm(b)
    j = len(ta & tb) / len(ta | tb)
    if j >= 0.6:
        return True
    return j >= 0.3 and difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio() >= 0.85


def merge_items(items: list, new: list, now: int) -> list:
    """Add parsed headlines to the kept ones: drop the irrelevant and those older than KEEP_H, merge the same story
    from several sources (earliest time, extra sources counted). Returns the new list."""
    keep = [dict(it, sources=list(it["sources"])) for it in items if now - KEEP_H * 3600 <= it["t"]]
    for it in keep:
        it.setdefault("tok", _tokens(it["title"]))
    for n in new:
        title = (n.get("title") or "").strip()
        if not title:
            continue
        t = n.get("time_utc") or now
        t = min(int(t), now)
        if t < now - KEEP_H * 3600:
            continue
        tok = _tokens(title)
        hit = None
        for it in keep:
            if (n.get("url") and n.get("url") == it.get("url")) or same_story(title, it["title"], tok, it["tok"]):
                hit = it
                break
        src = n.get("source") or "news"
        if hit:
            if src not in hit["sources"]:
                hit["sources"].append(src)
            if t < hit["t"]:
                hit["t"] = t
            continue
        a = analyze_headline(title)
        if a["importance"] < MIN_IMPORTANCE:
            continue
        keep.append({"title": title, "url": n.get("url") or "", "t": t, "sources": [src], "imp": a["importance"],
                     "dir": a["impact"], "tags": a["tags"], "why": a["why"], "label": a["label"], "tok": tok,
                     "themes": sorted({x for x in a["tags"]})})
    return keep


def _item_imp(it: dict) -> float:
    return min(1.0, it["imp"] + 0.05 * min(3, len(it["sources"]) - 1))


# ================================================================== the desk
class NewsDesk:
    """Calendar + headlines + their reading for gold. Fetches in its own thread (fetch=True); state() never waits.

    calendar: a nodes.Calendar (or nodes.Nodes) whose events are reused instead of fetching the JSON again.
    llm_url:  an OpenAI-compatible server (…/v1/chat/completions) or llama.cpp (…/completion), or its base URL."""

    def __init__(self, calendar=None, fetch: bool = True, llm_url: str | None = None):
        self.calendar = getattr(calendar, "calendar", calendar)
        self.fetch, self.llm_url = fetch, (llm_url or None)
        self._get = None                                  # tests put a fake fetcher here; None = _http_get
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._items: list = []
        self._own_events: list = []                       # from the JSON when no Calendar node was given
        self._own_fetched = 0.0
        self._xml_events: list = []
        self._xml_fetched, self._xml_offset = 0.0, 0
        self._src = {u: {"name": n, "every": e, "next": 0.0, "ok": None, "err": None, "fails": 0}
                     for n, u, e in SOURCES}
        self._cal_err = None
        self._headline_ok = 0                             # last time a headline source answered
        self._cal_ok = 0                                  # last time the calendar JSON or XML answered
        self._updated = None
        self._tried = False
        self._llm = {"text": None, "key": None, "at": 0, "ok": False}
        self._llm_next, self._llm_good = 0.0, None
        self._content_key = None
        self._load_cache()
        self._thread = None
        if fetch:
            self._thread = threading.Thread(target=self._loop, name="news", daemon=True)
            self._thread.start()

    # ------------------------------------------------------------ running
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception as e:                              # never let the thread die
                self._cal_err = f"refresh failed: {type(e).__name__}: {str(e)[:80]}"
            self._stop.wait(random.uniform(*REFRESH))

    def stop(self) -> None:
        self._stop.set()

    def _fetch_url(self, url: str) -> bytes:
        return (self._get or _http_get)(url, FETCH_TIMEOUT)

    def refresh(self, now: float | None = None) -> None:
        """One round: due headline feeds (in parallel), the calendar, the LLM summary. Safe to call by hand."""
        with self._lock:
            now = int(now if now is not None else time.time())
            self._refresh_calendar(now)
            self._refresh_headlines(now)
            self._tried = True
            if now in (self._headline_ok, self._cal_ok):          # something answered in this round
                self._updated = now
            self._save_cache(now)
            if self.llm_url:
                self._refresh_llm(now)

    # ------------------------------------------------------------ calendar
    def _base_events(self) -> list:
        """The Calendar node's events (or our own JSON's), without the XML."""
        base = []
        if self.calendar is not None:
            try:
                base = [dict(e) for e in (self.calendar.events or []) if isinstance(e.get("t"), (int, float))]
            except Exception:
                base = []
        return base or [dict(e) for e in self._own_events]

    def _events_raw(self) -> list:
        base = self._base_events()
        if not base:
            return [dict(e) for e in self._xml_events]
        if self._xml_events:                                     # the XML's extra fields (actual, url)
            by = {}
            for x in self._xml_events:
                by.setdefault((x["country"], x["title"].lower()), []).append(x)
            for e in base:
                cands = by.get(((e.get("country") or "").upper(), (e.get("title") or "").lower()), [])
                if cands:
                    x = min(cands, key=lambda c: abs(c["t"] - e["t"]))
                    if abs(x["t"] - e["t"]) <= 14 * 3600:
                        for k in ("actual", "url"):
                            if x.get(k) and not e.get(k):
                                e[k] = x[k]
        return base

    def _refresh_calendar(self, now: int) -> None:
        errs, tried = [], False
        if self.calendar is None and now - self._own_fetched >= CAL_EVERY:
            tried = True
            evs = self._read_node_cache(now)
            if evs is None:
                evs = []
                for u in CAL_URLS:
                    try:
                        evs += parse_ff_json(self._fetch_url(u))
                    except Exception as e:                       # next week's file is often missing early on
                        if "thisweek" in u:
                            errs.append(f"calendar json: {_err(e)}")
            if evs:
                self._own_events = sorted({(e["t"], e["title"], e["country"]): e for e in evs}.values(),
                                          key=lambda e: e["t"])
                self._own_fetched = now
                self._cal_ok = now
            else:
                self._own_fetched = now - CAL_EVERY + 600          # retry in 10 minutes
        base = self._events_raw()
        fast = any(e.get("country") == "USD" and e.get("impact") == "High" and 0 <= now - e["t"] <= 3600
                   and not e.get("actual") for e in base)
        if now - self._xml_fetched >= (XML_FAST if fast else XML_EVERY):
            tried = True
            try:
                xml = parse_ff_xml(self._fetch_url(CAL_XML))
                self._xml_offset = _learn_offset(self._base_events(), xml, self._xml_offset)
                self._xml_events = [dict(x, t=x["t"] + self._xml_offset) for x in xml]
                self._xml_fetched = now
                self._cal_ok = now
            except Exception as e:
                errs.append(f"calendar xml: {_err(e)}")
                self._xml_fetched = now - (XML_FAST if fast else XML_EVERY) + 600
        if tried:
            self._cal_err = "; ".join(errs) or None

    def _read_node_cache(self, now: int):
        """The Calendar node's own cache (~/.golddesk/calendar.json) when it is fresh: no second download."""
        try:
            d = json.loads((CACHE / "calendar.json").read_text())
            if now - float(d["fetched"]) < CAL_EVERY and d["events"]:
                return [_ff_event(e.get("title"), e.get("country"), e["t"], e.get("impact"), e.get("forecast"),
                                  e.get("previous"), e.get("actual")) for e in d["events"]]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    # ------------------------------------------------------------ headlines
    def _refresh_headlines(self, now: int) -> None:
        due = [u for u, s in self._src.items() if now >= s["next"]]
        if not due:
            return
        got = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            futs = {ex.submit(self._fetch_url, u): u for u in due}
            for f in concurrent.futures.as_completed(futs):
                u = futs[f]
                try:
                    got[u] = f.result()
                except Exception as e:
                    got[u] = e
        new = []
        for u in due:
            s, r = self._src[u], got.get(u)
            try:
                if isinstance(r, Exception) or r is None:
                    raise r or ValueError("no answer")
                items = parse_feed(r, s["name"])
                new += items
                s.update(ok=now, err=None, fails=0, next=now + s["every"])
                self._headline_ok = now
            except Exception as e:
                s["fails"] += 1
                s.update(err=_err(e), next=now + min(1800, s["every"] * 2 ** min(s["fails"], 4)))
        self._items = merge_items(self._items, new, now)

    def sources(self) -> list:
        """Each source's state, for a debug view: name, url, last ok (UTC), last error."""
        return [{"name": s["name"], "url": u, "ok": s["ok"], "error": s["err"]} for u, s in self._src.items()]

    # ------------------------------------------------------------ cache
    def _load_cache(self) -> None:
        try:
            d = json.loads((CACHE / "news.json").read_text())
            need = ("title", "t", "sources", "imp", "dir", "tags", "why", "label")
            self._items = [dict(it, tok=_tokens(it["title"])) for it in d.get("items", [])
                           if isinstance(it, dict) and all(k in it for k in need)]
            self._own_events = d.get("events", [])
            self._own_fetched = float(d.get("events_fetched", 0))
            self._xml_events = d.get("xml_events", [])
            self._xml_offset = int(d.get("xml_offset", 0))
            self._updated = d.get("updated")
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _save_cache(self, now: int) -> None:
        try:
            CACHE.mkdir(parents=True, exist_ok=True)
            items = [{k: v for k, v in it.items() if k != "tok"} for it in self._items]
            (CACHE / "news.json").write_text(json.dumps({
                "saved": now, "updated": self._updated, "items": items, "events": self._own_events, "events_fetched": self._own_fetched,
                "xml_events": self._xml_events, "xml_offset": self._xml_offset}))
        except (OSError, TypeError, ValueError):
            pass

    # ------------------------------------------------------------ LLM
    def _refresh_llm(self, now: int) -> None:
        try:
            st = self._compose(now)
        except Exception:
            return
        key = hashlib.sha1(json.dumps([[h["title"] for h in st["headlines"][:5]],
                                       (st["next_event"] or {}).get("title"), st["wait"]]).encode()).hexdigest()
        self._content_key = key
        if (key == self._llm["key"] and now - self._llm["at"] < 900) or time.monotonic() < self._llm_next:
            return
        text = llm_summary(self.llm_url, _llm_prompt(st), LLM_TIMEOUT, self)
        if text:
            self._llm = {"text": text, "key": key, "at": now, "ok": True}
        else:
            self._llm = {"text": None, "key": None, "at": 0, "ok": False}
            self._llm_next = time.monotonic() + 300                   # the LLM is down: try again in 5 minutes

    # ------------------------------------------------------------ the state
    def state(self, utc_now: int) -> dict:
        """The news box. Never raises: a failure anywhere gives an "offline: ..." state."""
        try:
            return self._compose(int(utc_now), final=True)
        except Exception as e:
            return {"status": f"offline: error {type(e).__name__}: {str(e)[:80]}", "updated": None, "wait": False,
                    "wait_text": None, "next_event": None, "events": [], "headlines": [], "bias": 0.0,
                    "bias_text": "no news read", "summary": "News unavailable.", "analysis": "", "llm": False}

    def _compose(self, now: int, final: bool = False) -> dict:
        items = [it for it in list(self._items) if now - KEEP_H * 3600 <= it["t"] <= now + 600]
        ranked = sorted(items, key=lambda it: -_item_imp(it) * _decay(now - it["t"], RANK_HALF_MIN))
        headlines = [{"title": it["title"],
                      "source": it["sources"][0] + (f" +{len(it['sources']) - 1}" if len(it["sources"]) > 1 else ""),
                      "time_utc": int(it["t"]), "age_min": int(max(0, now - it["t"]) // 60), "url": it.get("url") or "",
                      "importance": round(_item_imp(it), 2), "impact": it["dir"], "tags": list(it["tags"]),
                      "why": it["why"]} for it in ranked[:8]]

        raw = sorted((e for e in self._events_raw() if isinstance(e.get("t"), (int, float))), key=lambda e: e["t"])
        day0 = now - (now + _ny_offset(now)) % 86400                    # New York midnight today
        wanted = [e for e in raw if day0 <= e["t"] <= now + 86400 and (
            (e.get("country") == "USD" and e.get("impact") in ("High", "Medium")) or
            (e.get("country") in ("EUR", "CNY") and e.get("impact") == "High"))]
        if len(wanted) > 8:
            rank = {"High": 0, "Medium": 1}
            wanted = sorted(sorted(wanted, key=lambda e: (rank.get(e.get("impact"), 2), abs(e["t"] - now)))[:8],
                            key=lambda e: e["t"])
        events = [_ev_out(e, now, headlines=ranked) for e in wanted]
        high = [e for e in raw if e.get("country") == "USD" and e.get("impact") == "High"]
        win = [e for e in high if -WAIT_BEFORE * 60 <= now - e["t"] <= WAIT_AFTER * 60]
        nxt = next((e for e in high if e["t"] >= now - WAIT_AFTER * 60), None)
        next_event = _ev_out(nxt, now, full=True, headlines=ranked) if nxt else None
        if nxt and nxt["t"] > now + 7 * 86400:
            next_event = None
        wait_text = None
        if win:
            e = win[0]
            d = e["t"] - now
            when = f"in {countdown(d)}" if d >= 60 else ("now" if d > -60 else f"released {_ago(-d)}")
            wait_text = f"WAIT: {e['title']} {when} ({ny_clock(e['t'])} NY). No new entries until " \
                        f"{ny_clock(e['t'] + WAIT_AFTER * 60)} NY; expect wide spreads and a whipsaw."

        # bias: headlines + released calendar surprises, by importance and recency, pulled toward 0 by a prior
        num = den = 0.0
        drivers = []
        for it in items:
            w = _item_imp(it) * _decay(now - it["t"], BIAS_HALF_MIN)
            num += w * it["dir"]
            den += w
            if abs(it["dir"]) >= 0.15:
                drivers.append((w * abs(it["dir"]), it["dir"], it["label"], it["title"], it["why"], now - it["t"]))
        for e in raw:
            if e.get("actual") and now - KEEP_H * 3600 <= e["t"] <= now and e.get("impact") in ("High", "Medium"):
                g, txt = surprise(e)
                if txt:
                    w = (1.0 if e.get("impact") == "High" else 0.6) * _decay(now - e["t"], BIAS_HALF_MIN)
                    num += w * g
                    den += w
                    if abs(g) >= 0.15:
                        drivers.append((w * abs(g), g, f"{e['title']} {e['actual']} vs {e.get('forecast') or '?'}",
                                        None, txt, now - e["t"]))
        bias = round(max(-1.0, min(1.0, num / (den + BIAS_PRIOR))), 2) if den else 0.0
        drivers.sort(key=lambda d: -d[0])
        bias_text = _bias_text(bias, drivers, bool(items))

        summary, analysis = self._texts(now, bias, bias_text, drivers, headlines, events, next_event, wait_text,
                                        items)
        llm = False
        if final and self._llm["ok"] and self._llm["text"] and self._llm["key"] == self._content_key and \
                now - self._llm["at"] <= 1200:
            summary, llm = (wait_text + " " if wait_text else "") + self._llm["text"], True

        if self._headline_ok and now - self._headline_ok <= LIVE_S:
            status = "ok"
        elif items or self._headline_ok:
            status = "stale"                                 # headlines from the cache or from >10 min ago
        elif raw:
            status = "offline: no headline source reachable (calendar ok)"
        elif not self.fetch and not self._tried:
            status = "offline: fetching is off"
        elif not self._tried:
            status = "offline: starting (first fetch running)"
        else:
            errs = [s["err"] for s in self._src.values() if s["err"]]
            errs += [e.split(": ", 1)[-1] for e in (self._cal_err or "").split("; ") if e]
            uniq = list(dict.fromkeys(errs))
            why = (f"all {len(errs)} sources: {uniq[0]}" if len(uniq) == 1 else "; ".join(uniq[:2])) if errs else ""
            status = _short("offline: no source reachable" + (f" ({why})" if why else ""), 160)
        return {"status": status, "updated": int(self._updated) if self._updated else None, "wait": bool(win),
                "wait_text": wait_text, "next_event": next_event, "events": events, "headlines": headlines,
                "bias": bias, "bias_text": bias_text, "summary": summary, "analysis": analysis, "llm": llm}

    def _texts(self, now, bias, bias_text, drivers, headlines, events, next_event, wait_text, items) -> tuple:
        top = headlines[0] if headlines else None
        s = []
        if wait_text:
            s.append(wait_text)
        if top:
            s.append(f"{bias_text[0].upper()}{bias_text[1:]}.")
            s.append(f"Top story: “{top['title']}” ({top['source']}, {_ago(now - top['time_utc'])}).")
        else:
            s.append("No gold-moving headlines in the last 6 h.")
        if next_event and not wait_text:
            s.append(f"Next: {next_event['title']} at {ny_clock(next_event['time_utc'])} NY, in "
                     f"{countdown(next_event['time_utc'] - now)}.")
        summary = " ".join(s)

        a = []
        if wait_text:
            a.append(wait_text)
        bear = [d for d in drivers if d[1] < 0][:3]
        bull = [d for d in drivers if d[1] > 0][:3]
        say = lambda d: f"{d[2]} (“{_short(d[3])}”, {_ago(d[5])})" if d[3] else f"{d[2]} ({_ago(d[5])})"  # noqa
        if bear:
            a.append("Bearish for gold: " + "; ".join(say(d) for d in bear) + ".")
        if bull:
            a.append("Bullish for gold: " + "; ".join(say(d) for d in bull) + ".")
        if items:
            a.append(f"Net news bias {bias:+.2f} from {len(items)} {'story' if len(items) == 1 else 'stories'} in the "
                     f"last {KEEP_H} h, weighted by importance and recency (thin news pulls it toward 0).")
            if top and not drivers:
                a.append(f"{top['why']}.")
        released = [e for e in events if e["actual"] and e["time_utc"] <= now]
        for e in released[-2:]:
            a.append(f"{e['title']}: {e['gold_effect']}.")
        if next_event:
            a.append(next_event["gold_effect"] if next_event["time_utc"] > now else
                     f"{next_event['title']}: {next_event['gold_effect']}.")
        elif events or self._events_raw():
            a.append("No high-impact USD event ahead in this week's calendar.")
        else:
            a.append("Economic calendar unavailable" + (f" ({self._cal_err})." if self._cal_err else "."))
        a.append("News sets the backdrop, not the entry: read it with the chart. Headlines are scored by keyword "
                 "rules (explainable, not a model).")
        return summary, " ".join(a)


def _short(t: str, n: int = 70) -> str:
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


def _bias_text(b: float, drivers: list, any_news: bool) -> str:
    if not any_news and not drivers:
        return "no gold-moving news in the last 6 h"
    if abs(b) < 0.1:
        if not drivers:
            return f"news is neutral for gold: nothing directional (bias {b:+.2f})"
        other = next((d[2] for d in drivers if _sgn(d[1]) != _sgn(drivers[0][1])), "little else")
        return f"news is mixed for gold: {drivers[0][2]} vs {other} (bias {b:+.2f})"
    word = "bullish" if b > 0 else "bearish"
    lead = next((d for d in drivers if _sgn(d[1]) == _sgn(b)), None)
    why = re.sub(r"\s*→ (?:mildly |slightly )?(?:bullish|bearish) gold$", "", lead[4]) if lead else ""
    why = why[0].lower() + why[1:] if why else "several smaller stories"
    return f"news leans {'slightly ' if abs(b) < 0.2 else ''}{word} for gold: {why} (bias {b:+.2f})"


def _err(e: Exception) -> str:
    s = str(e) or type(e).__name__
    return f"{type(e).__name__}: {s}"[:120] if type(e).__name__ not in s else s[:120]


def _learn_offset(ref: list, xml: list, current: int) -> int:
    """How far the XML's clock is from the JSON's (which carries its UTC offset): the most common gap, in 30 min."""
    if not ref or not xml:
        return current
    by = {}
    for e in ref:
        by.setdefault(((e.get("country") or "").upper(), (e.get("title") or "").lower()), []).append(e["t"])
    gaps = []
    for x in xml:
        ts = by.get((x["country"], x["title"].lower()))
        if ts:
            g = min((t - x["t"] for t in ts), key=abs)
            if abs(g) <= 14 * 3600:
                gaps.append(int(round(g / 1800)) * 1800)
    if len(gaps) < 3:
        return current
    best = max(set(gaps), key=gaps.count)
    return best if gaps.count(best) >= len(gaps) * 0.6 else current


# ================================================================== LLM summary (optional)
LLM_SYSTEM = ("You are the news desk of a gold (XAU/USD) dashboard. Write exactly 2 short sentences in plain English: "
              "what the most important news says, and what it means for gold right now. Use only the facts given. "
              "No advice to buy or sell, no lists, no preamble.")


def _llm_prompt(st: dict) -> str:
    lines = [f"News bias for gold: {st['bias']:+.2f} ({st['bias_text']})."]
    if st["headlines"]:
        lines.append("Top headlines (newest reading by keyword rules):")
        for h in st["headlines"][:5]:
            lines.append(f"- [{h['age_min']} min ago, {h['source']}] {h['title']} (rule read: {h['why']})")
    else:
        lines.append("No gold-moving headlines in the last 6 hours.")
    ne = st["next_event"]
    if ne:
        lines.append(f"Next high-impact US event: {ne['title']} (forecast {ne['forecast'] or 'n/a'}, previous "
                     f"{ne['previous'] or 'n/a'}{', actual ' + ne['actual'] if ne['actual'] else ''}).")
    if st["wait"]:
        lines.append("We are inside the no-new-entries window around that event.")
    return "\n".join(lines)


def _llm_endpoints(url: str) -> list:
    u = url.rstrip("/")
    if u.endswith("/chat/completions"):
        return [("chat", u)]
    if u.endswith("/completion") or u.endswith("/completions"):
        return [("completion", u)]
    if u.endswith("/v1"):
        return [("chat", u + "/chat/completions")]
    return [("chat", u + "/v1/chat/completions"), ("completion", u + "/completion")]


def _llm_post(kind: str, url: str, prompt: str, timeout: float) -> str:
    if kind == "chat":
        body = {"model": "local", "messages": [{"role": "system", "content": LLM_SYSTEM},
                                                {"role": "user", "content": prompt}],
                "temperature": 0.2, "max_tokens": 140, "stream": False}
    else:      # llama.cpp's own endpoint, with the ChatML format of macro/slm_intuition.py's Qwen prompt
        body = {"prompt": f"<|im_start|>system\n{LLM_SYSTEM}<|im_end|>\n<|im_start|>user\n{prompt}<|im_end|>\n"
                          f"<|im_start|>assistant\n", "n_predict": 140, "temperature": 0.2, "stream": False,
                "stop": ["<|im_end|>"]}
    host = urllib.parse.urlparse(url).hostname or ""
    handlers = [urllib.request.ProxyHandler({})] if host in ("127.0.0.1", "localhost", "::1") else []
    if url.startswith("https"):
        handlers.append(urllib.request.HTTPSHandler(context=_ssl_context()))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with opener.open(req, timeout=timeout) as r:
        d = json.loads(r.read(200_000))
    if kind == "chat":
        return d["choices"][0]["message"]["content"]
    return d.get("content") or (d.get("choices") or [{}])[0].get("text", "")


def _clean_llm(t) -> str | None:
    if not isinstance(t, str):
        return None
    t = re.sub(r"<\|[^|>]*\|>", " ", t)
    t = re.sub(r"<think>.*?</think>", " ", t, flags=re.S)
    t = re.sub(r"\s+", " ", t).strip().strip('"').strip()
    sents = re.split(r"(?<=[.!?])\s+", t)
    t = " ".join(sents[:2]).strip()
    if len(t) < 20:
        return None
    return t if len(t) <= 420 else t[:419].rstrip() + "…"


def llm_summary(url: str, prompt: str, budget: float = LLM_TIMEOUT, desk=None) -> str | None:
    """A 2-sentence summary from the LLM, or None. Never takes longer than `budget` seconds in all: each try runs in
    a helper thread that is abandoned at the deadline."""
    if not url:
        return None
    deadline = time.monotonic() + min(budget, LLM_TIMEOUT)
    eps = _llm_endpoints(url)
    if desk is not None and desk._llm_good in eps:                 # the one that worked last time first
        eps.remove(desk._llm_good)
        eps.insert(0, desk._llm_good)
    for ep in eps:
        left = deadline - time.monotonic()
        if left <= 0.05:
            break
        box: dict = {}

        def run(ep=ep, left=left):
            try:
                box["v"] = _llm_post(ep[0], ep[1], prompt, left)
            except Exception as e:
                box["e"] = e
        th = threading.Thread(target=run, daemon=True)
        th.start()
        th.join(left)
        text = _clean_llm(box.get("v")) if not th.is_alive() else None
        if text:
            if desk is not None:
                desk._llm_good = ep
            return text
    return None


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Gold Desk news box: fetch once and print the state.")
    ap.add_argument("--llm", help="LLM server, e.g. http://127.0.0.1:8080 (llama.cpp) or an OpenAI-compatible URL")
    ap.add_argument("--headline", help="only read this headline and print the result")
    a = ap.parse_args()
    if a.headline:
        print(json.dumps(analyze_headline(a.headline), indent=1, ensure_ascii=False))
    else:
        desk = NewsDesk(fetch=False, llm_url=a.llm)
        desk.refresh()
        print(json.dumps(desk.state(int(time.time())), indent=1, ensure_ascii=False))
        for s in desk.sources():
            print(f"  {s['name']:<16} {'ok' if s['ok'] else 'FAILED':<7} {s['error'] or ''}")
