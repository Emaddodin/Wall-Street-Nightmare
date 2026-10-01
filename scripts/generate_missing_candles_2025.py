"""
scripts/generate_missing_candles_2025.py
========================================
Generates high-fidelity deterministic 1m candles for all trading days
from January 21, 2025 up to March 19, 2026 so the complete period
from Day 1 of Trump's administration to now (434 trading days) is populated.
"""
import concurrent.futures
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import replay_data

CANDLES_DIR = Path("data/candles")
CANDLES_DIR.mkdir(parents=True, exist_ok=True)

start_dt = datetime(2025, 1, 21)
end_dt = datetime(2026, 9, 20)

all_trading_days = []
curr = start_dt
while curr <= end_dt:
    if curr.weekday() < 5:  # Mon-Fri
        all_trading_days.append(curr.strftime("%Y-%m-%d"))
    curr += timedelta(days=1)

missing_days = [d for d in all_trading_days if not (CANDLES_DIR / f"gold_m1_{d}.csv").exists()]
print(f"Total Trading Days in Period (2025-01-21 to 2026-09-20): {len(all_trading_days)}")
print(f"Already Present: {len(all_trading_days) - len(missing_days)}")
print(f"Missing to Generate: {len(missing_days)}")

def generate_day(date_str: str) -> str:
    try:
        replay_data.load_candles_for_date(date_str, candles_dir=CANDLES_DIR)
        return f"OK: {date_str}"
    except Exception as e:
        return f"ERR {date_str}: {e}"

if missing_days:
    t0 = time.time()
    print(f"Generating {len(missing_days)} missing days with 8 parallel workers...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(generate_day, missing_days))
    print(f"Completed generation of {len(results)} days in {time.time()-t0:.2f}s!")
else:
    print("All trading days already present!")
