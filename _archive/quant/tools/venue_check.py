"""Bitunix venue test: does the cross-sectional fade have STRONGER game
win rates on our actual venue (less efficient, wider spreads)?

Fetches 5m klines for the top liquid symbols (walking back ~30 days),
then measures the fade game (g80x60, 1h horizon) on extreme 15m moves
with the same vol-normalized z condition, and compares against the
Binance baseline (~39.6% base win rate).

Usage:
  python3 quant/tools/venue_check.py --days 30 --top 30 \
      --out data/research/venue_games.csv
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scalper"))

from market_data.client import BitunixPublic  # noqa: E402
from quant.lib import store  # noqa: E402

log = logging.getLogger("quant.tools.venue_check")
logging.basicConfig(level=logging.INFO)


def fetch_5m(client: BitunixPublic, symbol: str, bars: int) -> pd.DataFrame:
    return client.klines(symbol, "5m", bars)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    root = store.data_root()
    client = BitunixPublic(pause=0.05)
    ticks = client.tickers()
    rows = [t for t in ticks if t["symbol"].endswith("USDT")
            and t["usdt_volume_24h"] > 5e6]
    rows.sort(key=lambda t: -t["usdt_volume_24h"])
    symbols = [t["symbol"] for t in rows[:args.top]]
    bars = args.days * 288

    base_wins = []
    t0 = time.time()
    for si, sym in enumerate(symbols):
        try:
            df = fetch_5m(client, sym, bars)
        except Exception as e:
            log.warning("[%s] fetch failed: %s", sym, e)
            continue
        if len(df) < 2000:
            continue
        c = df["close"].to_numpy(dtype=float)
        h = df["high"].to_numpy(dtype=float)
        l = df["low"].to_numpy(dtype=float)
        n = len(c)
        r1 = np.full(n, np.nan)
        r1[1:] = np.log(c[1:] / c[:-1])
        r3 = np.full(n, np.nan)
        r3[3:] = np.log(c[3:] / c[:-3])
        sd = pd.Series(r1).rolling(288).std().to_numpy()
        z = r3 / np.maximum(sd * np.sqrt(3), 1e-9)
        logc = np.log(c)
        logh = np.log(h)
        logl = np.log(l)
        # fade game g80x60_12 from extreme bars, fade direction = -sign(r3)
        tp_log, sl_log = np.log1p(80 / 1e4), np.log1p(60 / 1e4)
        mask = (np.abs(z) >= 1.5) & np.isfinite(z)
        mask[n - 12:] = False
        sgn = np.sign(r3)
        wins = dec = 0
        for i in np.nonzero(mask)[0]:
            side = -sgn[i]
            entry = logc[i]
            t_tp = t_sl = 99
            for k in range(1, 13):
                j = i + k
                if j >= n:
                    break
                if side > 0:
                    tp_hit = logh[j] - entry >= tp_log
                    sl_hit = entry - logl[j] >= sl_log
                else:
                    tp_hit = entry - logl[j] >= tp_log
                    sl_hit = logh[j] - entry >= sl_log
                if tp_hit and t_tp == 99:
                    t_tp = k
                if sl_hit and t_sl == 99:
                    t_sl = k
                if t_tp < 99 or t_sl < 99:
                    if t_tp <= t_sl:
                        wins += 1
                    if min(t_tp, t_sl) < 99:
                        dec += 1
                    break
        if dec:
            base_wins.append({"symbol": sym, "win": wins / dec, "n": dec,
                              "bars": n})
        log.info("[%s] %d/%d done", sym, si + 1, len(symbols))

    res = pd.DataFrame(base_wins)
    if args.out:
        res.to_csv(args.out, index=False)
    if len(res):
        w = res["n"].to_numpy(dtype=float)
        pooled = float((res["win"] * w).sum() / w.sum())
        print(f"BITUNIX fade g80x60_12 (|z|>=1.5, {len(res)} symbols, "
              f"{args.days}d): pooled win rate = {pooled:.3f}  "
              f"n={int(w.sum())}")
        print("(Binance baseline for comparison: 0.396)")
        print("per-symbol:")
        print(res.sort_values("win", ascending=False).head(12).round(3)
              .to_string(index=False))
    print(f"({time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
