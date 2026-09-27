"""
xau_alpha/news/build_calendar.py
Build the historical US high-impact economic calendar for gold (2025-01-01 .. 2026-09-30).

Inputs (run the two fetchers first):
  data/news_raw/official_schedules.json   official agency schedules (fetch_official_schedules.py)
  data/news_raw/nasdaq_us_rows*.jsonl     Nasdaq economic-calendar API rows (fetch_nasdaq_calendar.py)

Rules
  * Every scheduled release is taken from its OFFICIAL source (BLS, BEA, Census, ISM, Federal Reserve) with the
    agency's own Eastern-Time clock time, converted to UTC with the America/New_York zone (so 08:30 ET is 13:30 UTC
    in winter / EST and 12:30 UTC in summer / EDT).
  * Weekly Initial Jobless Claims (DOL, not published during the Oct-Nov 2025 shutdown) come from the Nasdaq rows
    that carry an actual value; holiday shifts are therefore the ones that really happened.
  * Nasdaq is used as an independent cross-check: each official event is marked confirmed when Nasdaq lists the
    same indicator at the same UTC minute. Nasdaq quirks (MEASURED): query date D returns day D-1, and its "gmt"
    column is a fixed UTC-4 clock, so UTC = shown + 4 h.

Outputs
  data/econ_calendar.csv       ts_utc, event, impact, source           (the contract used by lib/data.py)
  data/econ_calendar_ext.csv   + type, et_local, period, nasdaq_check, actual, consensus
"""
import glob
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/news_raw"
ET = ZoneInfo("America/New_York")
START, END = "2025-01-01", "2026-09-30"

SRC = {
    "bls": "BLS release calendar (bls.gov/schedule/{y}/home.htm via web.archive.org)",
    "bea": "BEA release dates (apps.bea.gov/API/signup/release_dates.json; bea.gov/news/schedule)",
    "fomc": "Federal Reserve FOMC calendar (federalreserve.gov/monetarypolicy/fomccalendars.htm)",
    "census": "Census retail release schedule (census.gov/retail/release_schedule.html{wb})",
    "ism": "ISM Report On Business calendar (ismworld.org via web.archive.org)",
    "testimony": "Federal Reserve testimony list (federalreserve.gov/json/ne-testimony.json)",
    "speech": "Federal Reserve speeches list (federalreserve.gov/json/ne-speeches.json)",
    "nasdaq": "Nasdaq economic calendar API (api.nasdaq.com/api/calendar/economicevents)",
}

# Nasdaq eventName -> calendar type (headline rows only)
NQ_MAP = {
    "Nonfarm Payrolls": "NFP", "CPI": "CPI", "Core CPI": "CPI", "PPI": "PPI", "Core PPI": "PPI",
    "Core PCE Price Index": "PCE", "PCE price index": "PCE", "PCE Price index": "PCE",
    "GDP": "GDP", "Retail Sales": "RETAIL", "Core Retail Sales": "RETAIL",
    "ISM Manufacturing PMI": "ISM_MFG", "ISM Non-Manufacturing PMI": "ISM_SRV", "ISM Services PMI": "ISM_SRV",
    "JOLTS Job Openings": "JOLTS", "Initial Jobless Claims": "CLAIMS",
    "Fed Interest Rate Decision": "FOMC", "FOMC Statement": "FOMC", "FOMC Press Conference": "FOMC_PRESS",
    "FOMC Meeting Minutes": "FOMC_MINUTES",
}


def utc_from_et(date_s: str, hhmm: str) -> datetime:
    h, m = hhmm.split(":")
    local = datetime.fromisoformat(date_s).replace(hour=int(h), minute=int(m), tzinfo=ET)
    return local.astimezone(timezone.utc)


def hhmm24(t12: str) -> str:
    return datetime.strptime(t12.strip(), "%I:%M %p").strftime("%H:%M")


def load_nasdaq() -> pd.DataFrame:
    best = {}                     # several range files overlap: keep the fullest response per query day
    for f in sorted(glob.glob(str(RAW / "nasdaq_us_rows*.jsonl"))):
        for line in open(f):
            r = json.loads(line)
            if r["query_date"] not in best or r["n_rows_all"] > best[r["query_date"]]["n_rows_all"]:
                best[r["query_date"]] = r
    seen, recs = set(best), []
    for r in best.values():
        if True:
            for x in r["rows"]:
                g = x.get("gmt", "")
                if not re.match(r"^\d\d:\d\d$", g):
                    continue
                shown = datetime.fromisoformat(r["event_day"] + "T" + g)
                recs.append({"ts": (shown + timedelta(hours=4)).replace(tzinfo=timezone.utc), "name": x["eventName"],
                             "type": NQ_MAP.get(x["eventName"]), "actual": x.get("actual", ""),
                             "consensus": x.get("consensus", ""), "day": r["event_day"]})
    df = pd.DataFrame(recs)
    df["actual"] = df["actual"].astype(str).str.replace("&nbsp;", "", regex=False).str.strip()
    df["consensus"] = df["consensus"].astype(str).str.replace("&nbsp;", "", regex=False).str.strip()
    return df, seen


def main():
    off = json.loads((RAW / "official_schedules.json").read_text())
    nq, days = load_nasdaq()
    ev = []

    def add(typ, ts, event, impact, source, period="", et_local=None):
        ev.append({"ts": ts, "type": typ, "event": event, "impact": impact, "source": source, "period": period,
                   "et_local": et_local or ts.astimezone(ET).strftime("%Y-%m-%d %H:%M %Z")})

    # --- BLS: NFP, CPI, PPI, JOLTS
    label = {"NFP": "Nonfarm Payrolls / Employment Situation", "CPI": "CPI (Consumer Price Index)",
             "PPI": "PPI (Producer Price Index)", "JOLTS": "JOLTS Job Openings"}
    for r in off["bls"]:
        ts = utc_from_et(r["date"], hhmm24(r["time_et"]))
        add(r["kind"], ts, f"{label[r['kind']]} ({r['period']})", "HIGH",
            SRC["bls"].format(y=r["src"][-4:]), r["period"])

    # --- BEA: GDP (advance/initial = HIGH, later estimates = MEDIUM) and Personal Income & Outlays (Core PCE)
    names = {}
    for n in off["bea"]["names"]:
        if re.match(r"^(Gross Domestic Product|GDP)\b", n["name"]) and "State" not in n["name"][:40] \
                and "County" not in n["name"] and "Puerto" not in n["name"]:
            names[n["date"]] = n["name"]
    for s in sorted(set(off["bea"]["gdp_utc"])):
        ts = datetime.fromisoformat(s).astimezone(timezone.utc)
        nm = names.get(ts.astimezone(ET).strftime("%Y-%m-%d"), "GDP")
        adv = ("Advance" in nm) or ("Initial" in nm)
        stage = "advance" if "Advance" in nm else "initial (replaced cancelled advance)" if "Initial" in nm else \
            "second" if "Second" in nm else "third" if "Third" in nm else "updated" if "Updated" in nm else "estimate"
        q = re.search(r"(\d)(?:st|nd|rd|th) Quarter(?: and Year)? (\d{4})", nm)
        per = f"Q{q.group(1)} {q.group(2)}" if q else ""
        add("GDP_ADV" if adv else "GDP", ts, f"GDP {stage} ({per})", "HIGH" if adv else "MEDIUM", SRC["bea"], per)
    for s in sorted(set(off["bea"]["pio_utc"])):
        ts = datetime.fromisoformat(s).astimezone(timezone.utc)
        add("PCE", ts, "Core PCE / Personal Income and Outlays", "HIGH", SRC["bea"])

    # --- Census: Advance Monthly Retail Sales, 08:30 ET. The current page supersedes the pre-shutdown page.
    by_period = {}
    for r in off["census_retail"]:
        if r["src"] == "census_now" or r["period"] not in by_period:
            by_period[r["period"]] = r
    for per, r in by_period.items():
        wb = " via web.archive.org 2025-03-06" if r["src"] == "census_2025" else ""
        add("RETAIL", utc_from_et(r["date"], "08:30"), f"Retail Sales ({per})", "HIGH", SRC["census"].format(wb=wb), per)

    # --- ISM: Manufacturing (1st business day) and Services (3rd business day), 10:00 ET
    for r in off["ism"]:
        y, mth = r["month"].split("-")
        add("ISM_MFG", utc_from_et(f"{y}-{mth}-{r['mfg']:02d}", "10:00"), "ISM Manufacturing PMI", "HIGH", SRC["ism"])
        add("ISM_SRV", utc_from_et(f"{y}-{mth}-{r['srv']:02d}", "10:00"), "ISM Services PMI", "HIGH", SRC["ism"])

    # --- FOMC: statement 14:00 ET, press conference 14:30 ET, minutes 14:00 ET
    for r in off["fomc"]["decision"]:
        sep = " + SEP" if r["sep"] else ""
        add("FOMC", utc_from_et(r["date"], "14:00"), f"FOMC rate decision / statement{sep}", "HIGH", SRC["fomc"])
        if r["press_conference"]:
            add("FOMC_PRESS", utc_from_et(r["date"], "14:30"), "FOMC press conference (Chair)", "HIGH", SRC["fomc"])
    for r in off["fomc"]["minutes"]:
        add("FOMC_MINUTES", utc_from_et(r["date"], "14:00"), f"FOMC minutes (meeting {r['meeting']})", "HIGH", SRC["fomc"])

    # --- Fed Chair semiannual testimony (hearing 10:00 ET; the Fed posts the prepared text at the time it lists)
    #     Second-day hearings are named on the Fed's 2025 testimony page; 2026 second day from Nasdaq if listed.
    for r in off["testimony"]:
        d, t = r["et"].split(" ")
        spk = r["speaker"].replace("Chairman ", "").replace("Chair ", "")
        body = "House" if "House" in r["where"] else "Senate"
        add("CHAIR_TESTIMONY", utc_from_et(d, "10:00"), f"Fed Chair {spk} semiannual testimony ({body}), hearing",
            "HIGH", SRC["testimony"])
        if t != "10:00":
            add("CHAIR_TESTIMONY_TEXT", utc_from_et(d, t), f"Fed Chair {spk} testimony prepared text released",
                "MEDIUM", SRC["testimony"])
    second_days = {"2025-02-12": ("Powell", "House"), "2025-06-25": ("Powell", "Senate")}
    for d, (spk, body) in second_days.items():
        add("CHAIR_TESTIMONY", utc_from_et(d, "10:00"), f"Fed Chair {spk} semiannual testimony ({body}), day 2",
            "HIGH", "Federal Reserve 2025 testimony page note (federalreserve.gov/newsevents/2025-testimony.htm)")
    for r in off["jackson_hole"]:
        d, t = r["et"].split(" ")
        spk = r["speaker"].replace("Chairman ", "").replace("Chair ", "")
        add("JACKSON_HOLE", utc_from_et(d, t), f"Jackson Hole: Fed Chair {spk} speech", "HIGH", SRC["speech"])

    # --- Weekly Initial Jobless Claims from Nasdaq rows with an actual value
    #     DOL publishes at 08:30 ET only; Nasdaq also lists the post-shutdown backlog weeks (2025-11-18/20) at
    #     artificial minutes (03:10, 08:24..08:29), which are dropped.
    cl = nq[(nq["type"] == "CLAIMS") & (nq["actual"] != "")]
    cl = cl[cl["ts"].map(lambda x: x.astimezone(ET).strftime("%H:%M")) == "08:30"]
    for ts in sorted(set(cl["ts"])):
        add("CLAIMS", ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts, "Initial Jobless Claims (weekly)",
            "MEDIUM", SRC["nasdaq"])

    cal = pd.DataFrame(ev)
    cal["ts"] = pd.to_datetime(cal["ts"], utc=True)
    cal = cal[(cal["ts"] >= pd.Timestamp(START, tz="UTC")) &
              (cal["ts"] < pd.Timestamp(END, tz="UTC") + pd.Timedelta(days=1))].copy()

    # 2026 second-day Chair testimony: take it from Nasdaq if it lists one the day after the Fed-listed hearing
    for _, r in cal[cal["type"] == "CHAIR_TESTIMONY"].iterrows():
        nxt = (r["ts"] + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        hit = nq[(nq["day"] == nxt) & nq["name"].str.contains("Testif", na=False)]
        if len(hit) and not ((cal["type"] == "CHAIR_TESTIMONY") & (cal["ts"].dt.strftime("%Y-%m-%d") == nxt)).any():
            ts2 = hit["ts"].iloc[0]
            cal.loc[len(cal) + 10_000] = {"ts": ts2, "type": "CHAIR_TESTIMONY", "event": f"Fed Chair testimony, day 2 ({hit['name'].iloc[0]})",
                                          "impact": "HIGH", "source": SRC["nasdaq"], "period": "",
                                          "et_local": ts2.astimezone(ET).strftime("%Y-%m-%d %H:%M %Z")}

    # --- Nasdaq cross-check for every row
    nq_key = {}
    for _, x in nq[nq["type"].notna()].iterrows():
        nq_key.setdefault(x["type"], []).append(x)
    nq_types = {"GDP_ADV": "GDP", "GDP": "GDP", "CHAIR_TESTIMONY": None, "CHAIR_TESTIMONY_TEXT": None,
                "JACKSON_HOLE": None}

    def check(r):
        t = nq_types.get(r["type"], r["type"])
        day = r["ts"].tz_convert(ET).strftime("%Y-%m-%d")
        qd = (r["ts"].tz_convert(ET) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        if qd not in days:
            return "nasdaq_day_not_fetched", "", ""
        if t is None:
            name_pat = "Testif" if "TESTIMONY" in r["type"] else "Jackson|Powell Speaks|Warsh Speaks"
            same = nq[(nq["day"] == day) & nq["name"].str.contains(name_pat, na=False)]
            if not len(same):
                return "absent_in_nasdaq", "", ""
            ok = (same["ts"] == r["ts"]).any()
            return ("confirmed_same_minute" if ok else "listed_other_time:" + ",".join(
                sorted(set(same["ts"].dt.strftime("%H:%M"))))), "", ""
        rows = [x for x in nq_key.get(t, []) if x["day"] == day]
        if not rows:
            return "absent_in_nasdaq", "", ""
        exact = [x for x in rows if x["ts"] == r["ts"]]
        if exact:
            x = exact[0]
            return "confirmed_same_minute", x["actual"], x["consensus"]
        return "listed_other_time:" + ",".join(sorted(set(x["ts"].strftime("%H:%M") for x in rows))), "", ""

    chk = cal.apply(check, axis=1, result_type="expand")
    cal["nasdaq_check"], cal["actual"], cal["consensus"] = chk[0], chk[1], chk[2]
    cal = cal.sort_values(["ts", "type"]).reset_index(drop=True)
    cal["ts_utc"] = cal["ts"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    cal[["ts_utc", "event", "impact", "source"]].to_csv(ROOT / "data/econ_calendar.csv", index=False)
    cal[["ts_utc", "event", "impact", "source", "type", "et_local", "period", "nasdaq_check", "actual",
         "consensus"]].to_csv(ROOT / "data/econ_calendar_ext.csv", index=False)

    print("rows", len(cal), "HIGH", int((cal.impact == "HIGH").sum()), "MEDIUM", int((cal.impact == "MEDIUM").sum()))
    print(cal.groupby("type").size().to_dict())
    print(cal["nasdaq_check"].str.split(":").str[0].value_counts().to_dict())
    bad = cal[~cal["nasdaq_check"].isin(["confirmed_same_minute"])]
    print(bad[["ts_utc", "type", "event", "nasdaq_check"]].to_string())


if __name__ == "__main__":
    main()
