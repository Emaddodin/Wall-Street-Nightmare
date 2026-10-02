"""The 10-minute line: where gold may be over the next 10 M1 candles, recomputed on every poll from the live price.

It starts at the live price (the forming candle) and leans by what the reading says, using the M1 trigger desk,
the M5 confirmation desk and the trend line, plus the Kronos path when a fresh forecast covers the next 10 minutes.
The band is how far gold usually moves in that many minutes right now: the spread of the last 60 closed M1
candles' moves, grown with the square root of time. p25-p75 is the middle half and lo-hi is 5 % to 95 %.
So the band is a range estimate, while the lean is a guess.

    python3 nowcast.py data/dukascopy_xauusd_m1.csv.gz --split 2026-06-01

That command checks the line at the 10-minute horizon after --split, on samples 10 minutes apart. It reports how
often the direction was right against a coin, whether the target beat "no change", and how often the price ended
inside each band (it should be about 50 % and 90 %). Nothing here is proven.
"""
from __future__ import annotations

import argparse
import math
from datetime import datetime, timezone

MINUTES = 10
SIGMA_N = 60
LEAN = 0.5                      # a full-strength reading leans this many 10-minute sigmas
MIX = (("M1", 0.4), ("M5", 0.3), ("line", 0.3))
Z50, Z90 = 0.6745, 1.6449


def sigma(closes: list) -> float | None:
    """Standard deviation of the last SIGMA_N one-minute moves."""
    c = closes[-(SIGMA_N + 1):]
    if len(c) < 20:
        return None
    d = [b - a for a, b in zip(c, c[1:])]
    m = sum(d) / len(d)
    return math.sqrt(sum((x - m) ** 2 for x in d) / (len(d) - 1)) or None


def score(desks: dict, line: float | None) -> float:
    parts = {"M1": (desks.get("M1") or {}).get("score"), "M5": (desks.get("M5") or {}).get("score"), "line": line}
    got = [(w, parts[k]) for k, w in MIX if parts[k] is not None]
    return sum(w * v for w, v in got) / sum(w for w, _ in got) if got else 0.0


def nowcast(t: int, price: float, sig: float, s: float, kronos: dict | None = None, digits: int = 2) -> dict:
    """t: the forming M1 candle's open (candle clock); price: the live price; sig: one-minute sigma; s: -1..+1."""
    s = max(-1.0, min(1.0, s))
    end = s * LEAN * sig * math.sqrt(MINUTES)
    kp = None
    if kronos and kronos.get("path") and kronos.get("last") is not None:
        pts = [(kronos["path"][0]["time"] - (kronos["path"][1]["time"] - kronos["path"][0]["time"])
                if len(kronos["path"]) > 1 else kronos["path"][0]["time"] - 300, kronos["last"])]
        pts += [(p["time"], p["value"]) for p in kronos["path"]]
        if pts[0][0] <= t + 60 and pts[-1][0] >= t + 60 * MINUTES:
            kp = pts
    r = lambda x: round(x, digits)
    path, band = [{"time": t, "value": r(price)}], []
    for i in range(1, MINUTES + 1):
        ti = t + 60 * i
        m = price + end * i / MINUTES
        if kp:                                           # half the Kronos move over the same minutes
            m += 0.5 * (_interp(kp, ti) - _interp(kp, t))
        sd = sig * math.sqrt(i)
        path.append({"time": ti, "value": r(m)})
        band.append({"time": ti, "lo": r(m - Z90 * sd), "hi": r(m + Z90 * sd), "p25": r(m - Z50 * sd),
                     "p75": r(m + Z50 * sd)})
    target = path[-1]["value"]
    path = path[1:]                                      # 10 points, 1 minute apart; the line starts at `last`
    sd = sig * math.sqrt(MINUTES)
    up = 0.5 * (1 + math.erf((target - price) / (sd * math.sqrt(2))))
    return {"t": t, "last": r(price), "target": target, "dir": 1 if target > price else (-1 if target < price else 0),
            "up_prob": round(up, 3), "minutes": MINUTES, "path": path, "band": band,
            "range": {k: band[-1][k] for k in ("lo", "hi", "p25", "p75")}, "score": round(s, 3),
            "sigma_1m": round(sig, 3), "kronos": bool(kp), "live": True, "proven": False}


def _interp(pts: list, t: int) -> float:
    if t <= pts[0][0]:
        return pts[0][1]
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t <= t1:
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return pts[-1][1]


def check(path: str, split: str) -> None:
    from boom_backtest import load
    from ict_train import frames
    from ictmodel import MarketRead, consensus
    from tfdesk import desks
    m1 = load(path)
    mr = MarketRead(frames(m1))
    cut = int(datetime.fromisoformat(split).replace(tzinfo=timezone.utc).timestamp())
    closes = [r[4] for r in m1]
    at = {r[0]: i for i, r in enumerate(m1)}
    n = right = moved = inside50 = inside90 = 0
    err_line = err_flat = 0.0
    for i, r in enumerate(m1):
        t = r[0] + 60                              # the next candle opens; the line starts from this close
        if r[0] < cut or t % (MINUTES * 60):
            continue
        j = at.get(t + 60 * (MINUTES - 1))         # the candle that closes 10 minutes later
        sig = sigma(closes[:i + 1])
        x = mr.features(t, r[4])
        if j is None or not sig or not x:
            continue
        nc = nowcast(t, r[4], sig, score(desks(mr, t, r[4]), consensus(x)["score"]))
        out = m1[j][4]
        n += 1
        rg = nc["range"]
        inside50 += rg["p25"] <= out <= rg["p75"]
        inside90 += rg["lo"] <= out <= rg["hi"]
        err_line += abs(out - nc["target"])
        err_flat += abs(out - r[4])
        if nc["dir"] and out != r[4]:
            moved += 1
            right += (out > r[4]) == (nc["dir"] > 0)
    band = 2 * math.sqrt(0.25 / max(moved, 1))
    print(f"{path}, after {split}: {n} samples 10 minutes apart")
    print(f"  direction right {right / max(moved, 1):.1%} of {moved} (coin +/-{band:.1%})")
    print(f"  average miss of the target {err_line / n:.3f} vs 'no change' {err_flat / n:.3f}")
    print(f"  ended inside p25-p75 {inside50 / n:.1%} (aim 50%), inside lo-hi {inside90 / n:.1%} (aim 90%)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--split", default="2026-06-01")
    a = ap.parse_args()
    check(a.csv, a.split)
