"""Assemble data/candles/real_bt/gold_m1_<date>.csv from REAL candle folders (no synthetic files)."""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
out = ROOT / "data/candles/real_bt"
out.mkdir(parents=True, exist_ok=True)
n = 0
for src in ("data/candles/real_full", "data/candles/real"):
    for f in sorted((ROOT / src).glob("xau_m1_*.csv")):
        date = f.stem.replace("xau_m1_", "")
        dst = out / f"gold_m1_{date}.csv"
        if not dst.exists() and f.stat().st_size > 1000:
            shutil.copyfile(f, dst)
            n += 1
print(f"{n} files -> {out}; total {len(list(out.glob('gold_m1_*.csv')))} days")
