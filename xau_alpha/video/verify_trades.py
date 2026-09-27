"""Cross-check trades read off the videos against Dukascopy XAUUSD M1 (UTC, mid).

Reads xau_alpha/recon/video_trades.csv, prints per-trade Dukascopy context:
  - mid low/high in entry minute and exit minute
  - MFE / MAE (in $/oz, side-aware, from avg_entry_est) between entry and exit minute
  - wipe-out distance = balance_before / (total_lots * 100)  ($/oz adverse move that
    takes equity to ~0; Exness 'unlimited leverage' stop-out is at ~0% margin level)
Then a base-rate study: for random M1 entries (both sides) over the last N days of
Dukascopy data, P(favourable +TP reached before adverse -SL) within H minutes, using
M1 high/low (same-bar ambiguity counted as a loss = conservative).
Usage: python3 verify_trades.py
"""
import glob
import os

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUKA = os.path.join(os.path.dirname(ROOT), "data", "candles", "duka")


def load_day(d):
    df = pd.read_csv(os.path.join(DUKA, f"xau_m1_{d}.csv"))
    df["dt"] = pd.to_datetime(df.time, unit="ms")
    return df.set_index("dt")


def hhmm(s):
    s = str(s).replace("~", "").split("-")[0].strip()
    return s if len(s) == 5 and s[2] == ":" else None


def per_trade():
    tr = pd.read_csv(os.path.join(ROOT, "recon", "video_trades.csv"))
    rows = []
    for _, t in tr.iterrows():
        e, x = hhmm(t.entry_utc), hhmm(t.exit_utc_est)
        if not e or not x:
            continue
        d = load_day(t.date_utc)
        te = pd.Timestamp(f"{t.date_utc} {e}")
        tx = pd.Timestamp(f"{t.date_utc} {x}")
        seg = d.loc[te:tx]
        try:
            avg = float(t.avg_entry_est)
        except ValueError:
            continue
        sgn = 1 if t.side == "BUY" else -1
        mfe = (seg.high.max() - avg) if sgn > 0 else (avg - seg.low.min())
        mae = (avg - seg.low.min()) if sgn > 0 else (seg.high.max() - avg)
        try:
            lots = float(str(t.total_lots_est).replace("~", "").replace(">=", ""))
            bal = float(str(t.balance_before).replace("~", ""))
            wipe = bal / (lots * 100.0)
        except ValueError:
            wipe = np.nan
        er, xr = d.loc[te], d.loc[tx]
        rows.append(dict(id=t.id, date=t.date_utc, entry=e, exit=x, side=t.side,
                         avg_entry=avg, duka_entry_lo=round(er.low, 2), duka_entry_hi=round(er.high, 2),
                         exit_est=t.exit_px_est, duka_exit_lo=round(xr.low, 2), duka_exit_hi=round(xr.high, 2),
                         mfe=round(mfe, 2), mae=round(mae, 2), wipe_dist=round(wipe, 2),
                         lots=t.total_lots_est, bal=t.balance_before, pnl=t.pnl_usd))
    out = pd.DataFrame(rows)
    print(out.to_string(index=False))
    return out


def base_rate(days=60, horizons=(5, 15, 30), grid=((5.0, 1.75), (4.0, 2.0), (1.0, 1.75), (5.0, 5.0))):
    files = sorted(glob.glob(os.path.join(DUKA, "xau_m1_*.csv")))[-days:]
    df = pd.concat([pd.read_csv(f) for f in files])
    df = df.sort_values("time").reset_index(drop=True)
    t = df.time.values
    hi, lo, cl = df.high.values, df.low.values, df.close.values
    n = len(df)
    print(f"\nBase rate on {len(files)} days ({files[0][-14:-4]}..{files[-1][-14:-4]}), {n} M1 bars, random entry at bar close (mid, no spread)")
    for H in horizons:
        for tp, sl in grid:
            res = {"BUY": [0, 0, 0], "SELL": [0, 0, 0]}  # win, loss, neither
            for i in range(0, n - H - 1, 3):
                # skip entries whose next H bars span a gap > H+5 minutes (weekend/rollover)
                if (t[i + H] - t[i]) > (H + 5) * 60000:
                    continue
                p = cl[i]
                for side in ("BUY", "SELL"):
                    outcome = 2
                    for j in range(i + 1, i + H + 1):
                        if side == "BUY":
                            adv, fav = p - lo[j], hi[j] - p
                        else:
                            adv, fav = hi[j] - p, p - lo[j]
                        if adv >= sl:
                            outcome = 1
                            break
                        if fav >= tp:
                            outcome = 0
                            break
                    res[side][outcome] += 1
            for side, (w, l_, nn) in res.items():
                tot = w + l_ + nn
                print(f"H={H:>2}m TP=+{tp:.2f} SL=-{sl:.2f} {side:4s}: win {w/tot:6.1%} loss {l_/tot:6.1%} neither {nn/tot:6.1%} (n={tot})")
    rng = (df.high - df.low)
    print(f"M1 range $: median {rng.median():.2f}  p75 {rng.quantile(.75):.2f}  p90 {rng.quantile(.9):.2f}  p99 {rng.quantile(.99):.2f}")


if __name__ == "__main__":
    per_trade()
    base_rate()
