"""Binance Vision (data.binance.vision) bulk downloader for USDT-M futures.

Self-contained research data layer for the new quant project.  Downloads:

  * monthly 1m klines       data/futures/um/monthly/klines/{SYM}/1m/{SYM}-1m-{YM}.zip
  * monthly funding rates   data/futures/um/monthly/fundingRate/{SYM}/{SYM}-fundingRate-{YM}.zip
  * daily 5m metrics (OI, taker L/S vol ratio, top-trader ratios)
                            data/futures/um/daily/metrics/{SYM}/{SYM}-metrics-{YMD}.zip

Resumable: every (symbol, period) chunk is checked against the stored parquet
coverage before being fetched; months/days already stored are skipped.

Notes:
  * 404 for a period means the symbol was not listed yet -> recorded as
    "unavailable", not retried again within a run.
  * Data quality: monthly klines zips for the CURRENT month are updated daily
    on Vision; the current month is therefore always (re)downloaded.
"""
from __future__ import annotations

import io
import json
import logging
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

log = logging.getLogger("quant.fetch.vision")

BASE = "https://data.binance.vision/data/futures/um"

# minute spans for coverage checks
MIN_MS = 60_000
DAY_MS = 86_400_000
# average days per month, used only for coverage bookkeeping
MONTH_DAYS = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
              7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}


def month_list(n: int) -> list[str]:
    out = []
    now = datetime.now(timezone.utc)
    y, m = now.year, now.month
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y}-{m:02d}")
    return out


def month_bounds(ym: str) -> tuple[int, int]:
    y, m = (int(x) for x in ym.split("-"))
    start = int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
    end = int(datetime(y + (m == 12), (m % 12) + 1, 1,
                        tzinfo=timezone.utc).timestamp() * 1000)
    return start, end


def fetch_zip(url: str, tries: int = 3, timeout: int = 120) -> bytes | None:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None          # not listed / no data -> unavailable
            if i == tries - 1:
                log.warning("GET %s -> HTTP %s (giving up)", url, e.code)
                return None
        except Exception as e:
            if i == tries - 1:
                log.warning("GET %s failed: %s", url, e)
                return None
        time.sleep(1.5 * (2 ** i))
    return None


def _read_zip_csv(raw: bytes, **kw) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        name = z.namelist()[0]
        csv = z.read(name).decode()
    return pd.read_csv(io.StringIO(csv), **kw)


# ----------------------------------------------------------------------
# 1m klines
# ----------------------------------------------------------------------

def klines_month_raw(symbol: str, ym: str) -> pd.DataFrame | None:
    url = f"{BASE}/monthly/klines/{symbol}/1m/{symbol}-1m-{ym}.zip"
    raw = fetch_zip(url)
    if raw is None:
        return None
    df = _read_zip_csv(raw, header=None, skiprows=1, usecols=[0, 1, 2, 3, 4, 7],
                       names=["open_time", "open", "high", "low", "close",
                              "volume"])
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["open_time"] = pd.to_numeric(df["open_time"], errors="coerce")
    df = df.dropna(subset=["open_time", "close"])
    df["open_time"] = df["open_time"].astype("int64")
    return df


def klines_covered_months(per: Path, ym: str) -> bool:
    """True if the parquet already covers >=95% of the minutes of month `ym`
    that have elapsed (full coverage for past months)."""
    if not per.exists():
        return False
    try:
        df = pd.read_parquet(per, columns=["open_time"])
    except Exception:
        return False
    start, end = month_bounds(ym)
    now = int(time.time() * 1000)
    eff_end = min(end, now)
    if eff_end <= start:
        return True
    span = eff_end - start
    inside = ((df["open_time"] >= start) & (df["open_time"] < eff_end)).sum()
    need = span / MIN_MS
    return inside >= 0.95 * need


def update_klines(symbol: str, months: list[str], out_dir: Path) -> str:
    """Download missing months for one symbol; returns status string."""
    per = out_dir / f"{symbol}_1m.parquet"
    new_frames: list[pd.DataFrame] = []
    for ym in months:
        if klines_covered_months(per, ym):
            continue
        df = klines_month_raw(symbol, ym)
        if df is None:
            log.debug("[%s] %s unavailable", symbol, ym)
            continue
        new_frames.append(df)
        log.debug("[%s] %s: %d bars", symbol, ym, len(df))
    if not new_frames:
        return "uptodate"
    df = pd.concat(new_frames, ignore_index=True)
    if per.exists():
        try:
            old = pd.read_parquet(per)
            df = pd.concat([old, df], ignore_index=True)
        except Exception:
            log.warning("[%s] could not read existing parquet; replacing",
                        symbol)
    df = (df.drop_duplicates("open_time", keep="last")
            .sort_values("open_time").reset_index(drop=True))
    df.to_parquet(per, index=False)
    return f"stored:{len(df)}"


# ----------------------------------------------------------------------
# funding rates (8h)
# ----------------------------------------------------------------------

def funding_month_raw(symbol: str, ym: str) -> pd.DataFrame | None:
    url = f"{BASE}/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{ym}.zip"
    raw = fetch_zip(url)
    if raw is None:
        return None
    return _read_zip_csv(raw)


def funding_covered(per: Path, ym: str) -> bool:
    if not per.exists():
        return False
    try:
        df = pd.read_parquet(per, columns=["calc_time"])
    except Exception:
        return False
    start, end = month_bounds(ym)
    now = int(time.time() * 1000)
    eff_end = min(end, now)
    if eff_end <= start:
        return True
    inside = ((df["calc_time"] >= start) & (df["calc_time"] < eff_end)).sum()
    # 3 funding marks per day
    days = (eff_end - start) / DAY_MS
    return inside >= 0.9 * 3 * days


def update_funding(symbol: str, months: list[str], out_dir: Path) -> str:
    per = out_dir / f"{symbol}.parquet"
    frames: list[pd.DataFrame] = []
    for ym in months:
        if funding_covered(per, ym):
            continue
        df = funding_month_raw(symbol, ym)
        if df is None:
            continue
        frames.append(df)
    if not frames:
        return "uptodate"
    df = pd.concat(frames, ignore_index=True)
    if per.exists():
        try:
            df = pd.concat([pd.read_parquet(per), df], ignore_index=True)
        except Exception:
            pass
    df["calc_time"] = df["calc_time"].astype("int64")
    df["last_funding_rate"] = df["last_funding_rate"].astype(float)
    df = (df.drop_duplicates("calc_time", keep="last")
            .sort_values("calc_time").reset_index(drop=True))
    df.to_parquet(per, index=False)
    return f"stored:{len(df)}"


# ----------------------------------------------------------------------
# daily 5m metrics (OI, taker L/S vol ratio, top-trader ratios)
# ----------------------------------------------------------------------

METRICS_COLS = ["create_time", "symbol", "sum_open_interest",
                "sum_open_interest_value",
                "count_toptrader_long_short_ratio",
                "sum_toptrader_long_short_ratio",
                "count_long_short_ratio", "sum_taker_long_short_vol_ratio"]


def metrics_day_raw(symbol: str, ymd: str) -> pd.DataFrame | None:
    url = f"{BASE}/daily/metrics/{symbol}/{symbol}-metrics-{ymd}.zip"
    raw = fetch_zip(url, timeout=60)
    if raw is None:
        return None
    df = _read_zip_csv(raw, usecols=METRICS_COLS)
    df["create_time"] = pd.to_datetime(df["create_time"], utc=True)
    return df


def day_list(n_days: int) -> list[str]:
    out = []
    now = datetime.now(timezone.utc)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for _ in range(n_days):
        out.append(day.strftime("%Y-%m-%d"))
        day -= pd.Timedelta(days=1)
    return out


def update_metrics(symbol: str, days: list[str], out_dir: Path,
                   inner_workers: int = 8) -> str:
    per = out_dir / f"{symbol}_metrics.parquet"
    have = set()
    if per.exists():
        try:
            old = pd.read_parquet(per, columns=["create_time"])
            have = set(old["create_time"].dt.strftime("%Y-%m-%d").unique())
        except Exception:
            pass
    todo = [d for d in days if d not in have]
    if not todo:
        return "uptodate"

    def fetch_day(ymd: str):
        return ymd, metrics_day_raw(symbol, ymd)

    frames: list[pd.DataFrame] = []
    with ThreadPoolExecutor(max_workers=inner_workers) as ex:
        for ymd, df in ex.map(fetch_day, todo):
            if df is not None and len(df):
                frames.append(df)
    if not frames:
        return "uptodate"
    df = pd.concat(frames, ignore_index=True)
    if per.exists():
        try:
            df = pd.concat([pd.read_parquet(per), df], ignore_index=True)
        except Exception:
            pass
    df = (df.drop_duplicates("create_time", keep="last")
            .sort_values("create_time").reset_index(drop=True))
    df.to_parquet(per, index=False)
    return f"stored:{len(df)}"


# ----------------------------------------------------------------------
# orchestration
# ----------------------------------------------------------------------

def _job(fn, args):
    symbol = args[0]
    try:
        return symbol, fn(*args)
    except Exception as e:
        log.warning("[%s] failed: %s", symbol, e)
        return symbol, f"error:{e}"


def run_all(jobs: list[tuple], workers: int = 8, label: str = "vision"):
    """jobs: list of (fn, args) tuples; fn returns a status string."""
    t0 = time.time()
    results: dict[str, str] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_job, fn, args): args[0] for fn, args in jobs}
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                sym, status = fut.result()
                results[sym] = status
            except Exception as e:
                results[sym] = f"error:{e}"
            done += 1
            if done % 20 == 0:
                el = (time.time() - t0) / 60.0
                log.info("[%s] %d/%d jobs (%.1f min)", label, done,
                         len(jobs), el)
    ok = sum(1 for v in results.values() if not v.startswith("error"))
    log.info("[%s] finished: %d ok, %d errors (%.1f min)", label, ok,
             len(results) - ok, (time.time() - t0) / 60.0)
    return results


def save_manifest(path: Path, results: dict[str, str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(results, f, indent=1, sort_keys=True)
    tmp.replace(path)
