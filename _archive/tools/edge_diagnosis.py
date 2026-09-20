#!/usr/bin/env python3
"""EDGE DIAGNOSIS -- why is the macro entry negative, and can it be fixed?

Runs the LIVE detector over a long Binance 15m history and measures, per
setup, four things that each imply a different cure:

  1. NET      the live result (costs on)          -> the baseline
  2. GROSS    same, no fees/slippage              -> is the PATTERN predictive?
  3. INVERSE  the mirrored setup (costs on)       -> is it simply BACKWARDS?
  4. MFE      did it ever reach 1R / 2R before -1R -> the payoff geometry

Decision guide
  gross > 0, net < 0        -> a COST problem (maker-only, bigger targets)
  inverse clearly > 0       -> the pattern is INVERTED: flip the sign
  gross ~ 0 and inv ~ 0     -> the pattern carries NO information
  MFE never reaches 1R      -> the targets sit outside the noise band

    python3 tools/edge_diagnosis.py [days] [n_coins]
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import live_hyperliquid as m          # noqa: E402
from live_hyperliquid import Config   # noqa: E402

FEE_MAKER = 1.5
FEE_TAKER = 3.0
SLIP = 1.5
TF_MS = 15 * 60_000
CACHE = Path("/root/ict_sniper/data/research/binance_15m")

# liquid alts that exist on BOTH Binance and the Hyperliquid book
COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK",
         "UNI", "ARB", "OP", "NEAR", "ATOM", "FIL", "INJ", "SUI", "APT",
         "SEI", "TIA", "MINA", "SAGA", "ACE", "LTC", "DOT", "MATIC", "TON",
         "PEPE", "WIF", "ORDI"]


def bps(x: float) -> float:
    return x / 10_000.0


def fetch_binance(coin: str, days: int) -> list[dict]:
    """15m klines from Binance, cached on disk. Returns live-format bars."""
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{coin}_{days}d.json"
    if f.exists():
        try:
            raw = json.loads(f.read_text())
            if raw:
                return raw
        except Exception:
            pass
    now = int(time.time() * 1000)
    start = now - days * 86_400_000
    out: list[dict] = []
    cur = start
    while cur < now:
        url = ("https://api.binance.com/api/v3/klines?symbol=%sUSDT"
               "&interval=15m&startTime=%d&endTime=%d&limit=1000"
               % (coin, cur, now))
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, timeout=15) as r:
                    ks = json.loads(r.read())
                break
            except Exception:
                if attempt == 3:
                    ks = []
                time.sleep(0.6 * (attempt + 1))
        if not ks:
            break
        for k in ks:
            out.append({"t": int(k[0]), "T": int(k[6]), "o": float(k[1]),
                        "h": float(k[2]), "l": float(k[3]),
                        "c": float(k[4]), "v": float(k[5])})
        cur = int(ks[-1][0]) + TF_MS
        if len(ks) < 1000:
            break
        time.sleep(0.15)
    if out:
        f.write_text(json.dumps(out))
    return out


def sim(bars, sig_i, side, entry, sl, tp1, tp2, cfg, costs=True):
    """Walk one setup forward with the live exit ladder. Returns R."""
    sl_dist = abs(entry - sl)
    if sl_dist <= 0 or entry <= 0:
        return None
    fill_i = -1
    for k in range(sig_i + 1, min(len(bars), sig_i + 1 + cfg.retest_bars)):
        b = bars[k]
        if (side > 0 and float(b["l"]) <= entry) or \
                (side < 0 and float(b["h"]) >= entry):
            fill_i = k
            break
    if fill_i < 0:
        return {"filled": False}
    qty, r = 1.0, 0.0
    be, trail = False, 0.0
    for k in range(fill_i, min(len(bars), fill_i + cfg.time_exit_bars)):
        b = bars[k]
        hi, lo = float(b["h"]), float(b["l"])
        if not be and qty > 0.5:
            mfe = (hi - entry) if side > 0 else (entry - lo)
            if mfe >= cfg.be_after_r * sl_dist:
                sl, be = entry, True
        if (side > 0 and lo <= sl) or (side < 0 and hi >= sl):
            px = sl * (1 - side * bps(SLIP)) if costs else sl
            r += qty * (px - entry) * side / sl_dist
            if costs:
                r -= qty * px * bps(FEE_TAKER) / sl_dist
            return {"filled": True, "r": r, "why": "sl"}
        if (side > 0 and hi >= tp1) or (side < 0 and lo <= tp1):
            q = qty * cfg.tp1_frac
            r += q * (tp1 - entry) * side / sl_dist
            if costs:
                r -= q * tp1 * bps(FEE_MAKER) / sl_dist
            qty -= q
            sl, be = entry, True
            trail = lo if side > 0 else hi
        if qty > 0 and ((side > 0 and hi >= tp2) or (side < 0 and lo <= tp2)):
            q = min(qty, cfg.tp2_frac)
            r += q * (tp2 - entry) * side / sl_dist
            if costs:
                r -= q * tp2 * bps(FEE_MAKER) / sl_dist
            qty -= q
        if qty > 0 and trail:
            piv = cfg.trail_pivot_bars + 1
            if k >= piv:
                seg = bars[k - piv:k]
                ref = min(float(x["l"]) for x in seg) if side > 0 else \
                    max(float(x["h"]) for x in seg)
                trail = max(trail, ref) if side > 0 else min(trail, ref)
                atr = m._wilder_atr([float(x["h"]) for x in seg],
                                    [float(x["l"]) for x in seg],
                                    [float(x["c"]) for x in seg],
                                    cfg.atr_period)[-1]
                room = cfg.trail_min_atr * atr if atr == atr else 0.0
                cand = trail - side * room
                sl = max(sl, cand) if side > 0 else min(sl, cand)
        if qty > 0 and k == min(len(bars), fill_i + cfg.time_exit_bars) - 1:
            px = float(b["c"])
            r += qty * (px - entry) * side / sl_dist
            if costs:
                r -= qty * px * bps(FEE_TAKER) / sl_dist
            qty = 0.0
    return {"filled": True, "r": r, "why": "time"}


def collect(bars, cfg):
    """Live detector walk -> list of (i, st) setups, honouring the caps."""
    out, seen, day_sig, last_t = [], set(), {}, 0
    # analyze_macro only reads the last cfg.history_bars bars, so hand it a
    # window instead of the whole prefix (identical result, O(n) not O(n^2))
    win = cfg.history_bars + 2
    for i in range(120, len(bars) - 1):
        st = m.analyze_macro(bars[max(0, i + 1 - win):i + 1], cfg)
        if not st:
            continue
        key = (st["side"], st["sweep_bar_t"], round(st["entry"], 10))
        if key in seen:
            continue
        dk = int(bars[i]["t"]) // 86_400_000
        if day_sig.get(dk, 0) >= cfg.macro_max_signals_day:
            continue
        if last_t and bars[i]["t"] - last_t < cfg.macro_cooldown_bars * TF_MS:
            continue
        seen.add(key)
        day_sig[dk] = day_sig.get(dk, 0) + 1
        last_t = bars[i]["t"]
        out.append((i, st))
    return out


def main(days: int, want: int) -> int:
    cfg = Config()
    coins = COINS[:want]
    print("fetching %d coins x %d days of 15m from Binance ..." % (len(coins), days),
          flush=True)
    data = []
    for c in coins:
        bars = fetch_binance(c, days)
        if len(bars) >= 500:
            data.append((c, bars))
            print("  %-7s %6d bars" % (c, len(bars)), flush=True)
    print("\ndata: %d coins\n" % len(data), flush=True)
    if not data:
        return 1

    stats = {k: [] for k in ("net", "gross", "inv", "inv_gross")}
    mfe1 = mfe2 = 0
    n_setups = n_filled = 0
    for coin, bars in data:
        setups = collect(bars, cfg)
        for i, st in setups:
            n_setups += 1
            sd = abs(st["entry"] - st["sl"])
            if sd <= 0:
                continue
            inv = {"side": -st["side"], "entry": st["entry"],
                   "sl": st["entry"] + (st["entry"] - st["sl"]),
                   "tp1": st["entry"] - (st["tp1"] - st["entry"]),
                   "tp2": st["entry"] - (st["tp2"] - st["entry"])}
            a = sim(bars, i, st["side"], st["entry"], st["sl"], st["tp1"],
                    st["tp2"], cfg, costs=True)
            b = sim(bars, i, st["side"], st["entry"], st["sl"], st["tp1"],
                    st["tp2"], cfg, costs=False)
            c = sim(bars, i, inv["side"], inv["entry"], inv["sl"], inv["tp1"],
                    inv["tp2"], cfg, costs=True)
            d = sim(bars, i, inv["side"], inv["entry"], inv["sl"], inv["tp1"],
                    inv["tp2"], cfg, costs=False)
            if not a or not a.get("filled") or not b:
                continue
            n_filled += 1
            stats["net"].append(a["r"])
            stats["gross"].append(b["r"])
            # the mirror has its own fill condition, so it can miss a fill
            if c and c.get("filled"):
                stats["inv"].append(c["r"])
            if d and d.get("filled"):
                stats["inv_gross"].append(d["r"])
            if b["r"] >= 1.0:
                mfe1 += 1
            if b["r"] >= 2.0:
                mfe2 += 1

    def line(name, xs):
        if not xs:
            print("%-14s (none)" % name)
            return
        exp = sum(xs) / len(xs)
        win = sum(1 for x in xs if x > 0) / len(xs) * 100
        print("%-14s n=%4d  exp %+7.3fR  win %3.0f%%  total %+8.1fR"
              % (name, len(xs), exp, win, sum(xs)))

    print("=" * 68)
    print("setups %d | filled %d | days %d | coins %d"
          % (n_setups, n_filled, days, len(data)))
    print("=" * 68)
    line("NET (live)", stats["net"])
    line("INVERSE", stats["inv"])
    line("GROSS (no fee)", stats["gross"])
    line("INV GROSS", stats["inv_gross"])
    if n_filled:
        print("\npayoff geometry: reached +1R %.0f%% | +2R %.0f%% of filled"
              % (mfe1 / n_filled * 100, mfe2 / n_filled * 100))
    cost_drag = (sum(stats["gross"]) - sum(stats["net"])) / max(1, len(stats["net"]))
    print("cost drag: %.3fR per trade" % cost_drag)

    # ---- part 2: is the problem DIRECTION or GEOMETRY? -------------------
    # keep the entries/stops, re-derive the targets in R and sweep them.
    # a coin-flip with the same ladder is the "geometry-only" baseline.
    print("\n" + "=" * 68)
    print("GEOMETRY SWEEP (same entries/stops; targets in R)")
    print("=" * 68)
    setups = []
    for coin, bars in data:
        for i, st in collect(bars, cfg):
            sd = abs(st["entry"] - st["sl"])
            if sd > 0:
                setups.append((bars, i, st, sd))
    print("setups: %d\n" % len(setups))

    def run_geom(k1, k2, be_r, flip=False):
        nets, gross = [], []
        for bars, i, st, sd in setups:
            e = st["entry"]
            sgn = -st["side"] if flip else st["side"]
            sl = e - sgn * sd
            tp1 = e + sgn * k1 * sd
            tp2 = e + sgn * k2 * sd
            c = Config(be_after_r=be_r)
            r = sim(bars, i, sgn, e, sl, tp1, tp2, c, costs=True)
            g = sim(bars, i, sgn, e, sl, tp1, tp2, c, costs=False)
            if r and r.get("filled"):
                nets.append(r["r"])
            if g and g.get("filled"):
                gross.append(g["r"])
        return nets, gross

    def mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    print("%-16s | %-21s | %-21s" % ("tp1/tp2/be R", "OUR side net / gross",
                                     "FLIP side net / gross"))
    best = []
    for k1 in (0.5, 0.8, 1.0, 1.3, 2.0):
        for k2 in (1.5, 2.5):
            for be_r in (0.5, 0.75):
                a, ag = run_geom(k1, k2, be_r, False)
                b, bg = run_geom(k1, k2, be_r, True)
                if not a:
                    continue
                label = "%.1f / %.1f / %.2f" % (k1, k2, be_r)
                print("%-16s | %+7.3fR / %+7.3fR  | %+7.3fR / %+7.3fR"
                      % (label, mean(a), mean(ag), mean(b), mean(bg)))
                best.append((max(mean(ag), mean(bg)), label))
    best.sort(key=lambda x: -x[0])
    print("\nbest geometry-only GROSS: %s -> %+.3fR" % (best[0][1], best[0][0])
          if best else "no rows")
    return 0


if __name__ == "__main__":
    d = int(sys.argv[1]) if len(sys.argv) > 1 else 365
    c = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    raise SystemExit(main(d, c))
