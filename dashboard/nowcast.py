"""The 10- and 30-minute lines: where gold may be over the next 10 / 30 M1 candles, recomputed on every poll from
the live price.

It starts at the live price (the forming candle) and leans by what the reading says, using the M1 trigger desk,
the M5 confirmation desk and the trend line, plus the Kronos path when a fresh forecast covers the next 10 minutes.
The band is how far gold usually moves in that many minutes right now: the spread of the last 60 closed M1
candles' moves, grown with the square root of time. p25-p75 is the middle half and lo-hi is 5 % to 95 %.
So the band is a range estimate, while the lean is a guess.

The 30-minute band uses a sharper volatility estimate, fitted before June and checked after it: 0.6 x the last
hour + 0.3 x what this New York hour usually does (clock_stats.json) + 0.1 x the last 4 hours (log-weighted,
x0.9). Its centre does not lean on the reading: leaning made both lines miss slightly more out of sample, and
direction stayed a coin flip. Out of sample it kept 90 % coverage with a band about 5 % narrower and a better probabilistic score
(CRPS) than the last hour alone. `samples` are a dozen real 30-minute stretches from the last two days,
rescaled to that volatility and drawn faintly for texture. They show what such moves look like and are not
separate predictions.

    python3 nowcast.py data/dukascopy_xauusd_m1.csv.gz --split 2026-06-01 [--minutes 30]

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
LEAN_BY = {30: 0.0}             # 30 min: leaning made the line miss more out of sample, so it stays centred
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


def _logsd(closes: list, n: int) -> float | None:
    c = [x for x in closes[-(n + 1):] if x > 0]
    if len(c) < max(20, n // 2):
        return None
    r = [math.log(b / a) for a, b in zip(c, c[1:])]
    m = sum(r) / len(r)
    return math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1)) or None


def sigma_end(closes: list, minutes: int, hour_sd: float | None) -> float | None:
    """Price sd of the move `minutes` ahead. 30 min: the blend fitted before June (see the docstring)."""
    s60 = _logsd(closes, 60)
    if not s60:
        return None
    p = closes[-1]
    if minutes != 30:
        return s60 * math.sqrt(minutes) * p
    s240 = _logsd(closes, 240) or s60
    a = math.log(s60 * math.sqrt(30))
    h = math.log(hour_sd) if hour_sd else a
    return 0.9 * math.exp(0.6 * a + 0.3 * h + 0.1 * math.log(s240 * math.sqrt(30))) * p


def bootstrap(closes: list, minutes: int, n: int = 12, seed: int = 0) -> list:
    """n real stretches of `minutes` one-minute moves from the recent closes, as relative paths with unit end sd."""
    import random
    c = closes[-2900:]
    if len(c) < minutes * 4:
        return []
    rng = random.Random(seed)
    ends = [c[k + minutes] / c[k] - 1 for k in range(len(c) - minutes)]
    sd = math.sqrt(sum(e * e for e in ends) / len(ends)) or 1e-9
    out = []
    for _ in range(n):
        k = rng.randrange(len(c) - minutes)
        out.append([(c[k + j] / c[k] - 1) / sd for j in range(1, minutes + 1)])
    return out


def score(desks: dict, line: float | None) -> float:
    parts = {"M1": (desks.get("M1") or {}).get("score"), "M5": (desks.get("M5") or {}).get("score"), "line": line}
    got = [(w, parts[k]) for k, w in MIX if parts[k] is not None]
    return sum(w * v for w, v in got) / sum(w for w, _ in got) if got else 0.0


def nowcast(t: int, price: float, sig: float, s: float, kronos: dict | None = None, digits: int = 2,
            minutes: int = MINUTES, sd_end: float | None = None, samples: list | None = None) -> dict:
    """t: the forming M1 candle's open (candle clock); price: the live price; sig: one-minute sigma; s: -1..+1.
    sd_end: price sd of the move `minutes` ahead (default sig * sqrt(minutes)); samples: from bootstrap()."""
    MINUTES = minutes
    sd_end = sd_end or sig * math.sqrt(MINUTES)
    s = max(-1.0, min(1.0, s))
    end = s * LEAN_BY.get(MINUTES, LEAN) * sd_end
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
        sd = sd_end * math.sqrt(i / MINUTES)
        path.append({"time": ti, "value": r(m)})
        band.append({"time": ti, "lo": r(m - Z90 * sd), "hi": r(m + Z90 * sd), "p25": r(m - Z50 * sd),
                     "p75": r(m + Z50 * sd)})
    target = path[-1]["value"]
    mid = [p["value"] for p in path[1:]]
    path = path[1:]                                      # 1 minute apart; the line starts at `last`
    sd = sd_end
    sm = [[{"time": t + 60 * (j + 1), "value": r(mid[j] + v * sd_end)} for j, v in enumerate(p)]
          for p in (samples or [])]
    up = 0.5 * (1 + math.erf((target - price) / (sd * math.sqrt(2))))
    return {"t": t, "last": r(price), "target": target, "dir": 1 if target > price else (-1 if target < price else 0),
            "up_prob": round(up, 3), "minutes": MINUTES, "path": path, "band": band,
            "range": {k: band[-1][k] for k in ("lo", "hi", "p25", "p75")}, "score": round(s, 3),
            "sigma_1m": round(sig, 3), "sd_end": round(sd_end, 3), "samples": sm, "kronos": bool(kp), "live": True,
            "proven": False}


def _interp(pts: list, t: int) -> float:
    if t <= pts[0][0]:
        return pts[0][1]
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t <= t1:
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return pts[-1][1]


def hour_sd30(m1: list, until: int | None = None) -> dict:
    """RMS of 30-minute log moves by New York hour (the clock's table), from candles before `until`."""
    from nodes import _ny_hour
    c = {r[0]: r[4] for r in m1}
    acc: dict = {}
    for r in m1:
        if r[0] % 600 or (until and r[0] >= until - 1800):
            continue
        e = c.get(r[0] + 1740)
        if e:
            a = acc.setdefault(_ny_hour(r[0])[0], [0, 0.0])
            a[0] += 1
            a[1] += math.log(e / r[1]) ** 2
    return {h: math.sqrt(v / n) for h, (n, v) in acc.items() if n > 30}


def _crps(sd: float, y: float) -> float:
    z = y / sd
    return sd * (z * math.erf(z / math.sqrt(2)) + 2 * math.exp(-z * z / 2) / math.sqrt(2 * math.pi) - 1 / math.sqrt(math.pi))


def check(path: str, split: str, minutes: int = MINUTES) -> None:
    from boom_backtest import load
    from ict_train import frames
    from ictmodel import MarketRead, consensus
    from nodes import _ny_hour
    from tfdesk import desks
    m1 = load(path)
    mr = MarketRead(frames(m1))
    cut = int(datetime.fromisoformat(split).replace(tzinfo=timezone.utc).timestamp())
    hsd = hour_sd30(m1, cut)                   # learned only from candles before the split
    closes = [r[4] for r in m1]
    at = {r[0]: i for i, r in enumerate(m1)}
    n = right = moved = inside50 = inside90 = in90_plain = 0
    err_line = err_flat = crps = crps_plain = width = width_plain = 0.0
    for i, r in enumerate(m1):
        t = r[0] + 60                              # the next candle opens; the line starts from this close
        if r[0] < cut or t % (minutes * 60):
            continue
        j = at.get(t + 60 * (minutes - 1))         # the candle that closes `minutes` later
        sig = sigma(closes[:i + 1])
        x = mr.features(t, r[4])
        if j is None or not sig or not x:
            continue
        past = closes[max(0, i - 300):i + 1]
        sde = sigma_end(past, minutes, hsd.get(_ny_hour(r[0])[0]))
        plain = sigma_end(past, 10, None) * math.sqrt(minutes / 10) if sde else None
        if not sde:
            continue
        nc = nowcast(t, r[4], sig, score(desks(mr, t, r[4]), consensus(x)["score"]), minutes=minutes, sd_end=sde)
        out = m1[j][4]
        n += 1
        rg = nc["range"]
        inside50 += rg["p25"] <= out <= rg["p75"]
        inside90 += rg["lo"] <= out <= rg["hi"]
        in90_plain += abs(out - nc["target"]) <= Z90 * plain
        crps += _crps(sde, out - nc["target"])
        crps_plain += _crps(plain, out - nc["target"])
        width += 2 * Z90 * sde
        width_plain += 2 * Z90 * plain
        err_line += abs(out - nc["target"])
        err_flat += abs(out - r[4])
        if nc["dir"] and out != r[4]:
            moved += 1
            right += (out > r[4]) == (nc["dir"] > 0)
    band = 2 * math.sqrt(0.25 / max(moved, 1))
    print(f"{path}, after {split}: {n} samples {minutes} minutes apart")
    print(f"  direction right {right / max(moved, 1):.1%} of {moved} (coin +/-{band:.1%})")
    print(f"  average miss of the target {err_line / n:.3f} vs 'no change' {err_flat / n:.3f}")
    print(f"  ended inside p25-p75 {inside50 / n:.1%} (aim 50%), inside lo-hi {inside90 / n:.1%} (aim 90%)")
    print(f"  band 5-95% width {width / n:.2f} vs last-hour-only {width_plain / n:.2f} (inside {in90_plain / n:.1%}); "
          f"CRPS {crps / n:.4f} vs {crps_plain / n:.4f} (lower is better)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--split", default="2026-06-01")
    ap.add_argument("--minutes", type=int, default=MINUTES)
    a = ap.parse_args()
    check(a.csv, a.split, a.minutes)
