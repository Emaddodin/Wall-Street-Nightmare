"""
xau_alpha/news/fetch_nasdaq_calendar.py
Fetch the historical US economic calendar from the public Nasdaq calendar API, one day per request.

Source: https://api.nasdaq.com/api/calendar/economicevents?date=YYYY-MM-DD  (public JSON, no key).

Two quirks were MEASURED on 2026-09-30 against releases whose times are known exactly
(NFP 2025-01-10 08:30 EST, ADP 2025-07-02 08:15 EDT, FOMC minutes 2025-01-08 14:00 EST):
  1. `date=D` returns the events of calendar day D-1.
  2. The `gmt` column is NOT GMT: it is a fixed UTC-4 clock (the current US Eastern offset when fetched),
     so UTC = shown + 4h for every date, winter or summer.
Both are handled in build_calendar.py (and re-checked there against the 8:30/10:00/14:00 ET rules).

Output: xau_alpha/data/news_raw/nasdaq_us_rows.jsonl, one line per query day:
  {"query_date": "...", "event_day": "... (query_date - 1)", "as_of": "...", "n_rows_all": N, "rows": [US rows]}
Resumable: days already in the file are skipped. Sequential, polite (sleep between calls).
Usage: python3 fetch_nasdaq_calendar.py [start] [end] [out_path]   (two ranges may run into two files, then cat)
"""
import json
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data/news_raw/nasdaq_us_rows.jsonl"
URL = "https://api.nasdaq.com/api/calendar/economicevents?date={}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}


def fetch(d: str, tries: int = 4):
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(URL.format(d), headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # network hiccup / throttling: back off and retry
            last = e
            time.sleep(3 * (k + 1))
    raise RuntimeError(f"{d}: {last}")


def main(start="2025-01-01", end="2026-10-01", out=None, sleep_s=0.6):
    global OUT
    if out:
        OUT = Path(out)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if OUT.exists():
        for line in OUT.read_text().splitlines():
            try:
                done.add(json.loads(line)["query_date"])
            except Exception:
                pass
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    n_new = 0
    with OUT.open("a") as f:
        d = d0
        while d <= d1:
            qd = d.isoformat()
            if qd not in done:
                try:
                    js = fetch(qd)
                except Exception as e:
                    print("FAIL", e, flush=True)
                    d += timedelta(days=1)
                    continue
                data = js.get("data") or {}
                rows = data.get("rows") or []
                us = [r for r in rows if r.get("country") == "United States"]
                rec = {"query_date": qd, "event_day": (d - timedelta(days=1)).isoformat(),
                       "as_of": data.get("asOf"), "n_rows_all": len(rows), "rows": us}
                f.write(json.dumps(rec) + "\n")
                f.flush()
                n_new += 1
                if n_new % 25 == 0:
                    print(qd, "rows", len(rows), "us", len(us), flush=True)
                time.sleep(sleep_s)
            d += timedelta(days=1)
    print("done, new days:", n_new, flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])
