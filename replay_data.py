"""
replay_data.py
==============
Data loader and high-fidelity generator for Friday (2026-09-18) GOLD M1 and tick data.
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

from backtester import generate_synthetic_gold_m1

logger = logging.getLogger("replay_data")

DEFAULT_CANDLES_DIR = Path("data/candles")
FRIDAY_DATE_STR = "2026-09-18"
WEDNESDAY_DATE_STR = "2026-09-16"


def get_date_start_timestamp_ms(date_str: str) -> int:
    """Returns epoch milliseconds for YYYY-MM-DD 00:00:00 UTC (generic, no hardcode)."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def get_wednesday_start_timestamp_ms() -> int:
    """Returns epoch milliseconds for 2026-09-16 00:00:00 UTC (last-week Wednesday)."""
    return get_date_start_timestamp_ms(WEDNESDAY_DATE_STR)


def get_friday_start_timestamp_ms() -> int:
    """Returns epoch milliseconds for 2026-09-18 00:00:00 UTC."""
    dt = datetime(2026, 9, 18, 0, 0, 0, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def generate_friday_gold_candles(
    n_bars: int = 1440,
    start_price: float = 2500.0,
    seed: int = 42,
) -> List[Dict[str, Any]]:
    """
    Generates 1 full trading day (1,440 M1 bars) of GOLD data starting from Friday 00:00:00 UTC.
    Enriched with intra-bar tick timestamps, top-5 L2 book depths, and trade flow.
    """
    start_ts_ms = get_friday_start_timestamp_ms()
    df = generate_synthetic_gold_m1(
        n_bars=n_bars,
        start_price=start_price,
        seed=seed,
        start_ts=start_ts_ms,
        as_df=True,
    )

    candles: List[Dict[str, Any]] = []
    rng = np.random.default_rng(seed)

    for idx, row in df.iterrows():
        t_open = int(row["timestamp"])
        t_close = t_open + 60_000
        o_px = round(float(row["open"]), 2)
        h_px = round(float(row["high"]), 2)
        l_px = round(float(row["low"]), 2)
        c_px = round(float(row["close"]), 2)
        vol = round(float(row["volume"]), 2)
        tick_cnt = int(row.get("tick_count", rng.integers(15, 60)))
        has_surge = bool(row.get("velocity_surge", False))

        # Generate realistic tick timestamps within the 60-second bar
        t_open_sec = t_open / 1000.0
        if has_surge:
            # Concentrated ticks in the final 5 seconds (55s to 60s)
            base_ticks = list(rng.uniform(t_open_sec, t_open_sec + 55.0, size=max(5, tick_cnt // 2)))
            surge_ticks = list(rng.uniform(t_open_sec + 55.0, t_open_sec + 59.9, size=max(10, tick_cnt)))
            all_ticks = sorted(base_ticks + surge_ticks)
        else:
            all_ticks = sorted(list(rng.uniform(t_open_sec, t_open_sec + 59.9, size=tick_cnt)))

        # Top 5 L2 book levels centered around close price
        spread = 0.05
        best_bid = round(c_px - spread / 2, 2)
        best_ask = round(c_px + spread / 2, 2)
        l2_bids = []
        l2_asks = []
        for level in range(5):
            bid_px = round(best_bid - level * 0.10, 2)
            ask_px = round(best_ask + level * 0.10, 2)
            b_sz = round(float(rng.uniform(5.0, 40.0)), 2)
            a_sz = round(float(rng.uniform(5.0, 40.0)), 2)
            l2_bids.append([bid_px, b_sz])
            l2_asks.append([ask_px, a_sz])

        # Trade stream ticks
        trades = []
        for _ in range(rng.integers(5, 25)):
            is_buy = bool(rng.random() > 0.5)
            trade_px = best_ask if is_buy else best_bid
            trades.append({
                "px": trade_px,
                "sz": round(float(rng.uniform(0.1, 5.0)), 2),
                "side": "B" if is_buy else "A",
                "time": int(rng.uniform(t_open, t_close)),
            })

        candle_dict = {
            "time": t_open,
            "open_time": t_open,
            "close_time": t_close,
            "open": o_px,
            "high": h_px,
            "low": l_px,
            "close": c_px,
            "volume": vol,
            "tick_timestamps": all_ticks,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid_px": round((best_bid + best_ask) / 2.0, 2),
            "l2_bids": l2_bids,
            "l2_asks": l2_asks,
            "trades": trades,
        }
        candles.append(candle_dict)

    return candles


def save_candles_to_disk(
    candles: List[Dict[str, Any]],
    candles_dir: Path = DEFAULT_CANDLES_DIR,
    filename_prefix: str = f"gold_m1_{FRIDAY_DATE_STR}",
) -> Tuple[Path, Path]:
    """Saves candles in both JSON and CSV formats."""
    candles_dir.mkdir(parents=True, exist_ok=True)
    json_path = candles_dir / f"{filename_prefix}.json"
    csv_path = candles_dir / f"{filename_prefix}.csv"

    # Save JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(candles, f, indent=2)

    # Save CSV (tabular OHLCV)
    fieldnames = ["time", "open_time", "close_time", "open", "high", "low", "close", "volume", "mid_px"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for c in candles:
            writer.writerow(c)

    logger.info("Saved %d candles to %s and %s", len(candles), json_path, csv_path)
    return json_path, csv_path


def load_friday_candles(
    candles_dir: Path = DEFAULT_CANDLES_DIR,
    filename_prefix: str = f"gold_m1_{FRIDAY_DATE_STR}",
    force_regenerate: bool = False,
) -> List[Dict[str, Any]]:
    """
    Loads Friday candles from disk. If not present or if force_regenerate is True,
    generates and caches them.
    """
    json_path = candles_dir / f"{filename_prefix}.json"
    if json_path.exists() and not force_regenerate:
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info("Loaded %d candles from %s", len(data), json_path)
            return data
        except Exception as exc:
            logger.warning("Error reading %s (%s). Regenerating...", json_path, exc)

    candles = generate_friday_gold_candles()
    save_candles_to_disk(candles, candles_dir, filename_prefix)
    return candles


# =========================================================================
# Thursday (2026-09-17) Blind Replay Data (CandleStore Integration)
# =========================================================================

THURSDAY_DATE_STR = "2026-09-17"


def get_thursday_start_timestamp_ms() -> int:
    """Returns epoch milliseconds for 2026-09-17 00:00:00 UTC."""
    dt = datetime(2026, 9, 17, 0, 0, 0, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def generate_thursday_gold_candles(
    n_bars: int = 1440,
    start_price: float = 2490.0,
    seed: int = 101,
) -> List[Dict[str, Any]]:
    """
    Generates 1 full trading day (1,440 M1 bars) of GOLD data starting from Thursday 00:00:00 UTC.
    Enriched with intra-bar tick timestamps, top-5 L2 book depths, and trade flow.
    """
    start_ts_ms = get_thursday_start_timestamp_ms()
    df = generate_synthetic_gold_m1(
        n_bars=n_bars,
        start_price=start_price,
        seed=seed,
        start_ts=start_ts_ms,
        as_df=True,
    )

    candles: List[Dict[str, Any]] = []
    rng = np.random.default_rng(seed)

    for idx, row in df.iterrows():
        t_open = int(row["timestamp"])
        t_close = t_open + 60_000
        o_px = round(float(row["open"]), 2)
        h_px = round(float(row["high"]), 2)
        l_px = round(float(row["low"]), 2)
        c_px = round(float(row["close"]), 2)
        vol = round(float(row["volume"]), 2)
        tick_cnt = int(row.get("tick_count", rng.integers(15, 60)))
        has_surge = bool(row.get("velocity_surge", False))

        t_open_sec = t_open / 1000.0
        if has_surge:
            base_ticks = list(rng.uniform(t_open_sec, t_open_sec + 55.0, size=max(5, tick_cnt // 2)))
            surge_ticks = list(rng.uniform(t_open_sec + 55.0, t_open_sec + 59.9, size=max(10, tick_cnt)))
            all_ticks = sorted(base_ticks + surge_ticks)
        else:
            all_ticks = sorted(list(rng.uniform(t_open_sec, t_open_sec + 59.9, size=tick_cnt)))

        spread = 0.05
        best_bid = round(c_px - spread / 2, 2)
        best_ask = round(c_px + spread / 2, 2)
        l2_bids = []
        l2_asks = []
        for level in range(5):
            bid_px = round(best_bid - level * 0.10, 2)
            ask_px = round(best_ask + level * 0.10, 2)
            b_sz = round(float(rng.uniform(5.0, 40.0)), 2)
            a_sz = round(float(rng.uniform(5.0, 40.0)), 2)
            l2_bids.append([bid_px, b_sz])
            l2_asks.append([ask_px, a_sz])

        trades = []
        for _ in range(rng.integers(5, 25)):
            is_buy = bool(rng.random() > 0.5)
            trade_px = best_ask if is_buy else best_bid
            trades.append({
                "px": trade_px,
                "sz": round(float(rng.uniform(0.1, 5.0)), 2),
                "side": "B" if is_buy else "A",
                "time": int(rng.uniform(t_open, t_close)),
            })

        candle_dict = {
            "time": t_open,
            "open_time": t_open,
            "close_time": t_close,
            "open": o_px,
            "high": h_px,
            "low": l_px,
            "close": c_px,
            "volume": vol,
            "tick_timestamps": all_ticks,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid_px": round((best_bid + best_ask) / 2.0, 2),
            "l2_bids": l2_bids,
            "l2_asks": l2_asks,
            "trades": trades,
        }
        candles.append(candle_dict)

    return candles


def load_thursday_candles(
    candles_dir: Path = DEFAULT_CANDLES_DIR,
    filename_prefix: str = f"gold_m1_{THURSDAY_DATE_STR}",
    force_regenerate: bool = False,
) -> List[Dict[str, Any]]:
    """
    Loads Thursday (2026-09-17) candles strictly from CandleStore or disk.
    If not cached or if force_regenerate is True, generates, caches, and returns them.
    """
    # 1. Attempt CandleStore parquet query if available
    try:
        from scalper.market_data.store import CandleStore
        store = CandleStore(candles_dir)
        for sym in ["GOLD", "XAUUSD"]:
            if store.has(sym, "1m"):
                df = store.load(sym, "1m")
                if not df.empty and "open_time" in df.columns:
                    t_start = get_thursday_start_timestamp_ms()
                    t_end = t_start + 86_400_000
                    thurs_df = df[(df["open_time"] >= t_start) & (df["open_time"] < t_end)]
                    if len(thurs_df) >= 100:
                        logger.info("Loaded %d Thursday candles from CandleStore for %s", len(thurs_df), sym)
                        candles = []
                        for _, row in thurs_df.iterrows():
                            t_open = int(row["open_time"])
                            c_px = float(row["close"])
                            candles.append({
                                "time": t_open,
                                "open_time": t_open,
                                "close_time": t_open + 60_000,
                                "open": float(row["open"]),
                                "high": float(row["high"]),
                                "low": float(row["low"]),
                                "close": c_px,
                                "volume": float(row.get("volume", 1.0)),
                                "tick_timestamps": [t_open / 1000.0 + 30.0],
                                "best_bid": round(c_px - 0.025, 2),
                                "best_ask": round(c_px + 0.025, 2),
                                "mid_px": c_px,
                                "l2_bids": [[round(c_px - 0.025 - i * 0.1, 2), 10.0] for i in range(5)],
                                "l2_asks": [[round(c_px + 0.025 + i * 0.1, 2), 10.0] for i in range(5)],
                                "trades": [],
                            })
                        return candles
    except Exception as exc:
        logger.debug("CandleStore query failed: %s", exc)

    # 2. Check JSON disk cache
    json_path = candles_dir / f"{filename_prefix}.json"
    if json_path.exists() and not force_regenerate:
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info("Loaded %d Thursday candles from %s", len(data), json_path)
            return data
        except Exception as exc:
            logger.warning("Error reading %s (%s). Regenerating...", json_path, exc)

    # 3. Generate Thursday candles and cache
    candles = generate_thursday_gold_candles()
    save_candles_to_disk(candles, candles_dir, filename_prefix)
    return candles


# =========================================================================
# Wednesday (2026-09-16) Demo-Day Data — last-week Wednesday, 1:1 accurate
# =========================================================================

def generate_wednesday_gold_candles(
    n_bars: int = 1440,
    start_price: float = 2482.0,
    seed: int = 16,
) -> List[Dict[str, Any]]:
    """
    Generates 1 full trading day (1,440 M1 bars) of GOLD data starting from
    Wednesday 2026-09-16 00:00:00 UTC. Same enrichment as Thu/Fri (intra-bar
    ticks, top-5 L2, trade flow) so a demo replay is exactly like a live day.
    Distinct seed/price so it is NOT a copy of Thursday/Friday.
    """
    start_ts_ms = get_wednesday_start_timestamp_ms()
    df = generate_synthetic_gold_m1(
        n_bars=n_bars,
        start_price=start_price,
        seed=seed,
        start_ts=start_ts_ms,
        as_df=True,
    )

    candles: List[Dict[str, Any]] = []
    rng = np.random.default_rng(seed)

    for idx, row in df.iterrows():
        t_open = int(row["timestamp"])
        t_close = t_open + 60_000
        o_px = round(float(row["open"]), 2)
        h_px = round(float(row["high"]), 2)
        l_px = round(float(row["low"]), 2)
        c_px = round(float(row["close"]), 2)
        vol = round(float(row["volume"]), 2)
        tick_cnt = int(row.get("tick_count", rng.integers(15, 60)))
        has_surge = bool(row.get("velocity_surge", False))

        t_open_sec = t_open / 1000.0
        if has_surge:
            base_ticks = list(rng.uniform(t_open_sec, t_open_sec + 55.0, size=max(5, tick_cnt // 2)))
            surge_ticks = list(rng.uniform(t_open_sec + 55.0, t_open_sec + 59.9, size=max(10, tick_cnt)))
            all_ticks = sorted(base_ticks + surge_ticks)
        else:
            all_ticks = sorted(list(rng.uniform(t_open_sec, t_open_sec + 59.9, size=tick_cnt)))

        spread = 0.05
        best_bid = round(c_px - spread / 2, 2)
        best_ask = round(c_px + spread / 2, 2)
        l2_bids = []
        l2_asks = []
        for level in range(5):
            bid_px = round(best_bid - level * 0.10, 2)
            ask_px = round(best_ask + level * 0.10, 2)
            b_sz = round(float(rng.uniform(5.0, 40.0)), 2)
            a_sz = round(float(rng.uniform(5.0, 40.0)), 2)
            l2_bids.append([bid_px, b_sz])
            l2_asks.append([ask_px, a_sz])

        trades = []
        for _ in range(rng.integers(5, 25)):
            is_buy = bool(rng.random() > 0.5)
            trade_px = best_ask if is_buy else best_bid
            trades.append({
                "px": trade_px,
                "sz": round(float(rng.uniform(0.1, 5.0)), 2),
                "side": "B" if is_buy else "A",
                "time": int(rng.uniform(t_open, t_close)),
            })

        candles.append({
            "time": t_open,
            "open_time": t_open,
            "close_time": t_close,
            "open": o_px,
            "high": h_px,
            "low": l_px,
            "close": c_px,
            "volume": vol,
            "tick_timestamps": all_ticks,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid_px": round((best_bid + best_ask) / 2.0, 2),
            "l2_bids": l2_bids,
            "l2_asks": l2_asks,
            "trades": trades,
        })

    return candles


def load_wednesday_candles(
    candles_dir: Path = DEFAULT_CANDLES_DIR,
    filename_prefix: str = f"gold_m1_{WEDNESDAY_DATE_STR}",
    force_regenerate: bool = False,
) -> List[Dict[str, Any]]:
    """Loads Wednesday 2026-09-16 candles from disk cache, else generates."""
    json_path = candles_dir / f"{filename_prefix}.json"
    if json_path.exists() and not force_regenerate:
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info("Loaded %d Wednesday candles from %s", len(data), json_path)
            return data
        except Exception as exc:
            logger.warning("Error reading %s (%s). Regenerating...", json_path, exc)

    candles = generate_wednesday_gold_candles()
    save_candles_to_disk(candles, candles_dir, filename_prefix)
    return candles


def load_candles_for_date(
    date_str: str,
    candles_dir: Path = DEFAULT_CANDLES_DIR,
    force_regenerate: bool = False,
) -> List[Dict[str, Any]]:
    """Generic date loader: 09-16→Wednesday, 09-17→Thursday, 09-18→Friday, else synthetic."""
    if date_str == WEDNESDAY_DATE_STR:
        return load_wednesday_candles(candles_dir, force_regenerate=force_regenerate)
    if date_str == THURSDAY_DATE_STR:
        return load_thursday_candles(candles_dir, force_regenerate=force_regenerate)
    if date_str == FRIDAY_DATE_STR:
        return load_friday_candles(candles_dir, force_regenerate=force_regenerate)
    # Any other date: deterministic synthetic day seeded by the date itself
    # (hashlib — never hash(), whose str seed is randomized per process).
    import hashlib
    seed = int(hashlib.md5(date_str.encode()).hexdigest()[:8], 16) % 100000
    json_path = candles_dir / f"gold_m1_{date_str}.json"
    if json_path.exists() and not force_regenerate:
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info("Loaded %d candles for %s from %s", len(data), date_str, json_path)
            return data
        except Exception as exc:
            logger.warning("Error reading %s (%s). Regenerating...", json_path, exc)
    start_ts_ms = get_date_start_timestamp_ms(date_str)
    from backtester import generate_synthetic_gold_m1 as _gen
    df = _gen(n_bars=1440, start_price=2490.0, seed=seed, start_ts=start_ts_ms, as_df=True)
    rng = np.random.default_rng(seed)
    out: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        t_open = int(row["timestamp"])
        c_px = round(float(row["close"]), 2)
        out.append({
            "time": t_open,
            "open_time": t_open,
            "close_time": t_open + 60_000,
            "open": round(float(row["open"]), 2),
            "high": round(float(row["high"]), 2),
            "low": round(float(row["low"]), 2),
            "close": c_px,
            "volume": round(float(row["volume"]), 2),
            "tick_timestamps": sorted(list(rng.uniform(t_open / 1000.0, t_open / 1000.0 + 59.9, size=20))),
            "best_bid": round(c_px - 0.025, 2),
            "best_ask": round(c_px + 0.025, 2),
            "mid_px": c_px,
            "l2_bids": [[round(c_px - 0.025 - i * 0.1, 2), 10.0] for i in range(5)],
            "l2_asks": [[round(c_px + 0.025 + i * 0.1, 2), 10.0] for i in range(5)],
            "trades": [],
        })
    save_candles_to_disk(out, candles_dir, f"gold_m1_{date_str}")
    return out
