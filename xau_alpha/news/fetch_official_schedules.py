"""
xau_alpha/news/fetch_official_schedules.py
Fetch and parse the OFFICIAL release schedules used to build the economic calendar (no API keys needed).

Sources (all public; bls.gov, ismworld.org and pre-2025-11 census.gov pages are read through the Internet Archive
because bls.gov answers 403 to scripted clients, ismworld.org serves a captcha, and census.gov only shows the
current schedule):
  FOMC       https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm        (meetings, minutes dates)
  Testimony  https://www.federalreserve.gov/json/ne-testimony.json                  (Chair semiannual testimony)
  Speeches   https://www.federalreserve.gov/json/ne-speeches.json                   (Jackson Hole Chair speech)
  BEA        https://apps.bea.gov/API/signup/release_dates.json                     (GDP, Personal Income & Outlays, UTC)
             https://www.bea.gov/news/schedule/full-2025 , /news/schedule/full      (release names: Advance/Second/...)
  BLS        https://web.archive.org/web/20260819060328/https://www.bls.gov/schedule/2025/home.htm
             https://web.archive.org/web/20260822104702/https://www.bls.gov/schedule/2026/home.htm
  Census     https://www.census.gov/retail/release_schedule.html                    (Nov-2025 onward)
             https://web.archive.org/web/20250306212836/https://www.census.gov/retail/release_schedule.html
  ISM        https://web.archive.org/web/20250708101444/https://www.ismworld.org/supply-management-news-and-reports/reports/rob-report-calendar/
             https://web.archive.org/web/20260824122435/https://www.ismworld.org/supply-management-news-and-reports/reports/rob-report-calendar

Output: xau_alpha/data/news_raw/official_schedules.json  (+ raw pages cached in data/news_raw/pages/)
"""
import codecs
import html
import json
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/news_raw"
PAGES = RAW / "pages"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")

URLS = {
    "fomc": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    "testimony": "https://www.federalreserve.gov/json/ne-testimony.json",
    "speeches": "https://www.federalreserve.gov/json/ne-speeches.json",
    "bea_json": "https://apps.bea.gov/API/signup/release_dates.json",
    "bea_2025": "https://www.bea.gov/news/schedule/full-2025",
    "bea_full": "https://www.bea.gov/news/schedule/full",
    "bls_2025": "https://web.archive.org/web/20260819060328/https://www.bls.gov/schedule/2025/home.htm",
    "bls_2026": "https://web.archive.org/web/20260822104702/https://www.bls.gov/schedule/2026/home.htm",
    "census_now": "https://www.census.gov/retail/release_schedule.html",
    "census_2025": "https://web.archive.org/web/20250306212836/https://www.census.gov/retail/release_schedule.html",
    "ism_2025": "https://web.archive.org/web/20250708101444/https://www.ismworld.org/supply-management-news-and-reports/reports/rob-report-calendar/",
    "ism_2026": "https://web.archive.org/web/20260824122435/https://www.ismworld.org/supply-management-news-and-reports/reports/rob-report-calendar",
}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def get(key: str) -> str:
    PAGES.mkdir(parents=True, exist_ok=True)
    f = PAGES / f"{key}.txt"
    if f.exists() and f.stat().st_size > 500:
        return f.read_text(encoding="utf-8", errors="ignore")
    req = urllib.request.Request(URLS[key], headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    s = codecs.decode(raw, "utf-8-sig", errors="ignore")
    f.write_text(s, encoding="utf-8")
    time.sleep(1.0)
    return s


def text(s: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)))


def d_iso(month_name: str, day: str, year: str) -> str:
    return f"{int(year):04d}-{MONTHS.index(month_name) + 1:02d}-{int(day):02d}"


def parse_fomc():
    t = text(get("fomc"))
    out = {"decision": [], "minutes": []}
    for yr in ("2024", "2025", "2026"):
        i = t.find(f"{yr} FOMC Meetings")
        j = t.find("FOMC Meetings", i + 20)
        seg = t[i:j]
        for m in re.finditer(r"(" + "|".join(MONTHS) + r")(?:/(" + "|".join(MONTHS) + r"))? (\d{1,2})-(\d{1,2})\*? "
                             r"Statement(.*?)(?=(?:" + "|".join(MONTHS) + r")(?:/\w+)? \d{1,2}-\d{1,2}\*? Statement|$)", seg):
            m1, m2, d1, d2, rest = m.groups()
            mon = m2 or m1
            dec = d_iso(mon, d2, yr)
            out["decision"].append({"date": dec, "press_conference": "Press Conference" in rest,
                                    "sep": "Projection Materials" in rest})
            mm = re.search(r"Released (" + "|".join(MONTHS) + r") (\d{1,2}), (\d{4})", rest)
            if mm:
                out["minutes"].append({"date": d_iso(mm.group(1), mm.group(2), mm.group(3)), "meeting": dec})
    return out


def parse_fed_json(key, pred):
    d = json.loads(get(key))
    out = []
    for x in d:
        if pred(x):
            dt = datetime.strptime(x["d"], "%m/%d/%Y %I:%M:%S %p")
            out.append({"et": dt.strftime("%Y-%m-%d %H:%M"), "title": x.get("t"), "speaker": x.get("s"),
                        "where": x.get("lo"), "link": "https://www.federalreserve.gov" + x.get("l", "")})
    return out


def parse_bea():
    js = json.loads(get("bea_json"))
    names = []
    for key in ("bea_2025", "bea_full"):
        t = text(get(key))
        for m in re.finditer(r"(" + "|".join(MONTHS) + r") (\d{1,2}) (\d{1,2}:\d\d [AP]M) N ews ((?:GDP|Gross Domestic Product|"
                             r"Personal Income and Outlays)[^|]{0,160}?)(?= View| (?:" + "|".join(MONTHS) + r") \d| To Be|$)", t):
            yr = "2025" if key == "bea_2025" else "2026"
            names.append({"date": d_iso(m.group(1), m.group(2), yr), "time_et": m.group(3), "name": m.group(4).strip()})
    return {"gdp_utc": js["Gross Domestic Product"]["release_dates"],
            "pio_utc": js["Personal Income and Outlays"]["release_dates"], "names": names}


def parse_bls():
    out = []
    wd = r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
    for key in ("bls_2025", "bls_2026"):
        t = text(get(key))
        for m in re.finditer(wd + r", (" + "|".join(MONTHS) + r") (\d{1,2}), (\d{4}) (\d\d:\d\d [AP]M) (.+?)(?= " + wd + r", |$)", t):
            name = m.group(5)
            for kind, pat in (("NFP", r"^Employment Situation for "), ("CPI", r"^Consumer Price Index for "),
                              ("PPI", r"^Producer Price Index for "),
                              ("JOLTS", r"^Job Openings and Labor Turnover Survey for ")):
                if re.match(pat, name):
                    per = re.match(pat + r"(\w+ \d{4})", name)
                    out.append({"kind": kind, "date": d_iso(m.group(1), m.group(2), m.group(3)),
                                "time_et": m.group(4), "period": per.group(1) if per else "", "src": key})
    return out


def parse_census():
    out = []
    for key in ("census_2025", "census_now"):
        t = text(get(key))
        i = t.find("Advance Monthly Retail Trade Report")
        j = t.find("Historical Release Dates", i)
        seg = t[i:j]
        for m in re.finditer(r"(" + "|".join(MONTHS) + r") (\d{4}) (" + "|".join(MONTHS) + r") (\d{1,2}), (\d{4})", seg):
            out.append({"period": f"{m.group(1)} {m.group(2)}", "date": d_iso(m.group(3), m.group(4), m.group(5)),
                        "src": key})
    return out


def parse_ism():
    out = []
    for key, yr in (("ism_2025", 2025), ("ism_2026", 2026)):
        t = text(get(key))
        i = t.find("Release Dates Month")
        seg = t[i:i + 1500]
        for mon in MONTHS:
            m = re.search(r"\b" + mon + r"(?: " + str(yr) + r")? (\d{1,2})\*? (\d{1,2})\b", seg)
            if m:
                out.append({"month": f"{yr}-{MONTHS.index(mon) + 1:02d}", "mfg": int(m.group(1)), "srv": int(m.group(2)),
                            "src": key})
    return out


def main():
    res = {
        "fomc": parse_fomc(),
        "testimony": parse_fed_json("testimony", lambda x: "Semiannual Monetary Policy Report" in x.get("t", "")
                                    and x["d"].split("/")[2][:4] in ("2025", "2026")),
        "jackson_hole": parse_fed_json("speeches", lambda x: "Jackson Hole" in x.get("lo", "")
                                       and x["d"].split("/")[2][:4] in ("2025", "2026")
                                       and ("Chair " in x.get("s", "") or "Chairman" in x.get("s", ""))
                                       and "Vice" not in x.get("s", "")),
        "bea": parse_bea(),
        "bls": parse_bls(),
        "census_retail": parse_census(),
        "ism": parse_ism(),
        "urls": URLS,
    }
    (RAW / "official_schedules.json").write_text(json.dumps(res, indent=1))
    print({k: (len(v) if isinstance(v, list) else {kk: len(vv) for kk, vv in v.items()} if isinstance(v, dict) else v)
           for k, v in res.items() if k != "urls"})


if __name__ == "__main__":
    main()
