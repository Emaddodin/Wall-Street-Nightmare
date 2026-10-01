"""
xau_alpha/live/bootstrap.py
Warm-up history so the live trader can decide from its first minute (the old ghost engine waited hours for candles).

Source: Binance USD-M perpetual XAUUSDT 1-minute klines (real gold, reachable from the VPS). The perp trades at a small
basis to broker spot, so bars are shifted by (broker mid - perp close) measured at startup; bid/ask are set around the
shifted mid with the given spread. Live bars replace these as they form.
"""
import json
import time
import urllib.request

URL = "https://fapi.binance.com/fapi/v1/klines?symbol=XAUUSDT&interval=1m&limit=1500&endTime={end}"


def fetch_perp_m1(minutes: int = 4000, end_ms: int | None = None) -> list[list]:
    end = end_ms or int(time.time() * 1000)
    rows: list[list] = []
    while len(rows) < minutes:
        req = urllib.request.Request(URL.format(end=end), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            batch = json.loads(r.read())
        if not batch:
            break
        rows = batch + rows
        end = batch[0][0] - 1
        if len(batch) < 1500:
            break
    rows = sorted({int(k[0]): k for k in rows}.values(), key=lambda k: int(k[0]))
    return rows[-minutes:]


def to_bars(klines: list[list], broker_mid: float | None, spread: float = 0.25) -> list[dict]:
    """Closed klines only (the last one may still be forming). Shifted to the broker's price level."""
    now_min = int(time.time() // 60) * 60_000
    kl = [k for k in klines if int(k[0]) < now_min]
    shift = (broker_mid - float(kl[-1][4])) if (broker_mid and kl) else 0.0
    h = spread / 2
    bars = []
    for k in kl:
        o, hi, lo, c = (float(k[i]) + shift for i in (1, 2, 3, 4))
        bars.append({"ts": int(k[0]), "bo": o - h, "bh": hi - h, "bl": lo - h, "bc": c - h,
                     "ao": o + h, "ah": hi + h, "al": lo + h, "ac": c + h,
                     "spr": spread, "spr_max": spread, "n": int(k[8]), "vol": float(k[5])})
    return bars


def warm(trader, broker_mid: float, minutes: int = 4000) -> int:
    """Fill trader.bars with history older than its existing bars. Returns number of bars added."""
    bars = to_bars(fetch_perp_m1(minutes), broker_mid)
    have = {b["ts"] for b in trader.bars.bars}
    first_live = min(have) if have else None
    add = [b for b in bars if b["ts"] not in have and (first_live is None or b["ts"] < first_live)]
    trader.bars.bars = sorted(add + trader.bars.bars, key=lambda b: b["ts"])[-trader.bars.keep:]
    trader.bars.save()
    return len(add)
