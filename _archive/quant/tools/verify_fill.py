"""Independent verification of the d=70 limit-fade result, WITHOUT the Sim.

For each 5m bar t with an extreme 15m move (|z| >= min_z), take the fade
direction; then scan forward up to `wait` bars for the first bar whose
extreme trades through the limit (close_t * (1 -/+ d)).  From the fill bar,
play the first-hit game: +tp_bps before -sl_bps within `max_bars` bars,
using 5m bar highs/lows with worst-case same-bar ordering (SL wins ties,
except when the fill bar opens beyond the target).

If the pooled win rate here matches the Sim's trade win rate (~54.6% at
d=70, tp=80, sl=60), the engine result is confirmed by an independent
implementation.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.lib import store  # noqa: E402
from quant.tools.discover5m import resample_5m  # noqa: E402

log = logging.getLogger("quant.tools.verify_fill")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=None)
    ap.add_argument("--min-bars", type=int, default=30_000)
    ap.add_argument("--min-z", type=float, default=1.5)
    ap.add_argument("--delta-bps", type=float, default=70.0)
    ap.add_argument("--tp-bps", type=float, default=80.0)
    ap.add_argument("--sl-bps", type=float, default=60.0)
    ap.add_argument("--wait", type=int, default=6)
    ap.add_argument("--max-bars", type=int, default=12)
    args = ap.parse_args()

    root = store.data_root()
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        uni = json.loads(open(root / "universe.json").read())
        symbols = sorted(uni["symbols"])

    wins = losses = timeouts = signals = fills = 0
    rows = []
    t0 = time.time()
    for si, sym in enumerate(symbols):
        try:
            df = store.load_klines(sym, root)
        except FileNotFoundError:
            continue
        if len(df) < args.min_bars * 5:
            continue
        d5 = resample_5m(df)
        if len(d5) < args.min_bars:
            continue
        c = d5["close"].to_numpy(dtype=float)
        h = d5["high"].to_numpy(dtype=float)
        l = d5["low"].to_numpy(dtype=float)
        o = d5["open"].to_numpy(dtype=float)
        n = len(c)
        r1 = np.full(n, np.nan)
        r1[1:] = np.log(c[1:] / c[:-1])
        r3 = np.full(n, np.nan)
        r3[3:] = np.log(c[3:] / c[:-3])
        sd = pd.Series(r1).rolling(288).std().to_numpy()
        z = r3 / np.maximum(sd * np.sqrt(3), 1e-9)
        d_frac = args.delta_bps / 1e4
        tp_frac = args.tp_bps / 1e4
        sl_frac = args.sl_bps / 1e4
        i = 0
        while i < n - 4:
            zi = z[i]
            if not np.isfinite(zi) or abs(zi) < args.min_z:
                i += 1
                continue
            side = -1 if r3[i] > 0 else 1       # fade direction
            signals += 1
            ref = c[i]
            limit = ref * ((1 - d_frac) if side > 0 else (1 + d_frac))
            fill_bar = -1
            for k in range(1, args.wait + 1):
                j = i + k
                if j >= n:
                    break
                if side > 0 and l[j] <= limit:
                    fill_bar = j
                    break
                if side < 0 and h[j] >= limit:
                    fill_bar = j
                    break
            if fill_bar < 0:
                i += 1
                continue
            fills += 1
            fill = limit
            if side > 0:
                tp = fill * (1 + tp_frac)
                sl = fill * (1 - sl_frac)
            else:
                tp = fill * (1 - tp_frac)
                sl = fill * (1 + sl_frac)
            outcome = "time"
            res = 0
            for k in range(fill_bar, min(fill_bar + args.max_bars + 1, n)):
                hh, ll, oo = h[k], l[k], o[k]
                if side > 0:
                    if oo >= tp:
                        res = 1
                        outcome = "tp"
                        break
                    hit_sl = ll <= sl
                    hit_tp = hh >= tp
                    if hit_sl and hit_tp:
                        res = -1
                        outcome = "sl"
                        break
                    if hit_sl:
                        res = -1
                        outcome = "sl"
                        break
                    if hit_tp:
                        res = 1
                        outcome = "tp"
                        break
                else:
                    if oo <= tp:
                        res = 1
                        outcome = "tp"
                        break
                    hit_sl = hh >= sl
                    hit_tp = ll <= tp
                    if hit_sl and hit_tp:
                        res = -1
                        outcome = "sl"
                        break
                    if hit_sl:
                        res = -1
                        outcome = "sl"
                        break
                    if hit_tp:
                        res = 1
                        outcome = "tp"
                        break
            if res == 1:
                wins += 1
            elif res == -1:
                losses += 1
            else:
                timeouts += 1
            i += 1
        log.info("[%s] done (%d/%d)", sym, si + 1, len(symbols))
    decided = wins + losses
    total = decided + timeouts
    print(f"signals={signals} fills={fills} fill_rate={fills/max(signals,1):.3f}")
    print(f"decided={decided} timeouts={timeouts}")
    print(f"win_rate (decided) = {wins/decided:.4f}" if decided else "n/a")
    print(f"win_rate (all)     = {wins/total:.4f}" if total else "n/a")
    print(f"ev per decided (bps) = "
          f"{(wins*tp_frac - losses*sl_frac)/decided*1e4:.2f}")
    print(f"({time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
