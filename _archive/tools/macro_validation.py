#!/usr/bin/env python3
"""Out-of-sample check for the v4 macro-momentum detector (read-only).

Fetches real 15m candles for the liquid universe, walks every closed bar,
and reports (a) how often analyze_macro fires per asset per day and
(b) what those setups would have paid with the live exit ladder
(40% TP1 / 30% TP2 / 30% trailing, structural stop, VIP costs).

    python3 tools/macro_validation.py [days] [coins]
"""
from __future__ import annotations

import asyncio
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import live_hyperliquid as m                      # noqa: E402
from live_hyperliquid import Config                 # noqa: E402

MIN_MS = 60_000
FEE_MAKER = 1.5     # bps, exit
FEE_TAKER = 3.0     # bps, stop exits
SLIP = 1.5          # bps on stop fills
TF_MS = 15 * MIN_MS


def bps(x: float) -> float:
    return x / 10_000.0


def htf_bull(bars: list[dict], i: int, cfg: Config) -> bool | None:
    """1h trend agreement at bar i (same rule as RiskEngine.htf_bias)."""
    per = max(1, cfg.htf_minutes // max(1, cfg.tf_min))
    need = per * (cfg.htf_ema + cfg.htf_slope_bars + 1)
    if i + 1 < need:
        return None
    closes = []
    k = i + 1
    while k - per >= 0:
        closes.append(float(bars[k - 1]["c"]))
        k -= per
    closes.reverse()
    ema = m._ema(closes, cfg.htf_ema)
    if len(ema) < cfg.htf_slope_bars + 1:
        return None
    slope = ema[-1] - ema[-1 - cfg.htf_slope_bars]
    up = slope > 0 and closes[-1] > ema[-1]
    dn = slope < 0 and closes[-1] < ema[-1]
    return True if up else (False if dn else None)


def simulate(bars: list[dict], sig_i: int, st: dict, cfg: Config) -> dict:
    """Walk the setup forward with the live exit rules.  Returns R + bps."""
    side, entry = st["side"], st["entry"]
    sl, tp1, tp2 = st["sl"], st["tp1"], st["tp2"]
    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        return {}
    r_mult = 0.0
    filled = False
    fill_i = -1
    for k in range(sig_i + 1, min(len(bars), sig_i + 1 + cfg.retest_bars)):
        b = bars[k]
        if (side > 0 and float(b["l"]) <= entry) or \
                (side < 0 and float(b["h"]) >= entry):
            if cfg.vol_confirm_enabled and k >= cfg.vol_look + 1:
                base = [float(x.get("v") or 0) for x in bars[k - cfg.vol_look:k]]
                avg = sum(base) / len(base) if base else 0.0
                v = float(b.get("v") or 0)
                rng = float(b["h"]) - float(b["l"])
                trs = [max(float(x["h"]) - float(x["l"]),
                           abs(float(x["c"]) - float(x.get("o", x["c"]))))
                       for x in bars[k - cfg.vol_look:k]]
                avr = sum(trs) / len(trs) if trs else 0.0
                if avg > 0 and v < cfg.vol_mult * avg and \
                        not (avr > 0 and rng >= cfg.vol_range_mult * avr):
                    continue          # no participation: keep waiting
            filled, fill_i = True, k
            break
    if not filled:
        return {"filled": False}
    qty, leg = 1.0, 0.0
    be_done = False
    trail_ref = 0.0
    for k in range(fill_i, min(len(bars), fill_i + cfg.time_exit_bars)):
        b = bars[k]
        hi, lo = float(b["h"]), float(b["l"])
        # pre-scale breakeven at be_after_r
        if not be_done and qty > 0.5:
            mfe = (hi - entry) if side > 0 else (entry - lo)
            if mfe >= cfg.be_after_r * sl_dist:
                sl, be_done = entry, True
        # stop first (conservative)
        if (side > 0 and lo <= sl) or (side < 0 and hi >= sl):
            px = sl * (1 - side * bps(SLIP))
            r = qty * (px - entry) * side / sl_dist \
                - qty * px * bps(FEE_TAKER) / sl_dist
            r_mult += r
            return {"filled": True, "r": r_mult, "legs": leg + 1,
                    "why": "trail" if trail_ref else ("be" if be_done else "sl")}
        if (side > 0 and hi >= tp1) or (side < 0 and lo <= tp1):
            q = qty * cfg.tp1_frac
            r_mult += q * (tp1 - entry) * side / sl_dist \
                - q * tp1 * bps(FEE_MAKER) / sl_dist
            qty -= q
            sl, be_done, trail_ref = entry, True, lo if side > 0 else hi
            leg += 1
        if qty > 0 and ((side > 0 and hi >= tp2) or (side < 0 and lo <= tp2)):
            q = min(qty, cfg.tp2_frac)
            r_mult += q * (tp2 - entry) * side / sl_dist \
                - q * tp2 * bps(FEE_MAKER) / sl_dist
            qty -= q
            leg += 1
        # runner trails an N-bar pivot with an ATR buffer
        if qty > 0 and trail_ref:
            piv = cfg.trail_pivot_bars + 1
            if k >= piv:
                seg = bars[k - piv:k]
                ref = min(float(x["l"]) for x in seg) if side > 0 else \
                    max(float(x["h"]) for x in seg)
                trail_ref = max(trail_ref, ref) if side > 0 else \
                    min(trail_ref, ref)
                atr = m._wilder_atr([float(x["h"]) for x in seg],
                                    [float(x["l"]) for x in seg],
                                    [float(x["c"]) for x in seg],
                                    cfg.atr_period)[-1]
                room = cfg.trail_min_atr * atr if math.isfinite(atr) else 0.0
                cand = trail_ref - side * room
                sl = max(sl, cand) if side > 0 else min(sl, cand)
        if qty > 0 and k == min(len(bars), fill_i + cfg.time_exit_bars) - 1:
            px = float(b["c"])
            r_mult += qty * (px - entry) * side / sl_dist \
                - qty * px * bps(FEE_TAKER) / sl_dist
            qty = 0.0
            leg += 1
    return {"filled": True, "r": r_mult, "legs": leg, "why": "time"}


async def main(days: int, want: int) -> int:
    info = m.Info(m.hl_constants.MAINNET_API_URL, skip_ws=True)
    loop = asyncio.get_running_loop()
    meta, ctxs = await loop.run_in_executor(None, info.meta_and_asset_ctxs)
    rows = meta["universe"]
    cands = []
    for i, u in enumerate(rows):
        name = u.get("name", "")
        if not name or i >= len(ctxs):
            continue
        try:
            vol = float(ctxs[i].get("dayNtlVlm") or 0)
        except (TypeError, ValueError):
            continue
        if vol >= 1e6:
            cands.append((name, vol))
    cands.sort(key=lambda x: -x[1])
    cands = cands[:want]
    now = int(time.time() * 1000)
    start = now - days * 24 * 3600 * 1000
    sem = asyncio.Semaphore(4)

    async def fetch(coin):
        async with sem:
            return await loop.run_in_executor(
                None, info.candles_snapshot, coin, "15m", start, now)

    got = await asyncio.gather(*[fetch(c) for c, _ in cands],
                               return_exceptions=True)
    data = []
    for (coin, _), bars in zip(cands, got):
        if isinstance(bars, BaseException) or not bars:
            continue
        bars = [b for b in bars if int(b["T"]) <= now]
        if len(bars) >= 200:
            data.append((coin, bars))
    n_days = max(1, days)
    print(f"data: {len(data)} coins x {n_days} days of 15m candles\n")

    def run(name, cfg, align=True, cap=True, session=False):
        total = filled = 0
        rs: list[float] = []
        per_coin: dict[str, int] = {}
        for coin, bars in data:
            seen = set()
            day_sig: dict[int, int] = {}
            last_t = 0
            for i in range(120, len(bars) - 1):
                if align:
                    bull = htf_bull(bars, i, cfg)
                    if bull is None:
                        continue
                else:
                    bull = None
                if session:
                    hm = time.gmtime(bars[i]["t"] / 1000)
                    mins = hm.tm_hour * 60 + hm.tm_min
                    if any(lo2 <= mins < hi2
                           for lo2, hi2 in cfg.session_dead_utc):
                        continue
                    if (hm.tm_hour, hm.tm_min) >= cfg.flat_by_hm:
                        continue
                    in_prime = any(lo2 <= mins < hi2
                                   for lo2, hi2 in cfg.session_prime_utc)
                    if cfg.session_strict and not in_prime and \
                            (cfg.flat_enabled or True):
                        # normal hours: only monster structure qualifies
                        stp = m.analyze_macro(bars[:i + 1], cfg)
                        if not stp:
                            continue
                        if stp.get("leg_atr", 0.0) < cfg.macro_normal_min_leg:
                            continue
                st = m.analyze_macro(bars[:i + 1], cfg)
                if not st:
                    continue
                if bull is not None and (st["side"] > 0) != bull:
                    continue
                key = (st["side"], st["sweep_bar_t"], round(st["entry"], 8))
                if key in seen:
                    continue
                dk = int(bars[i]["t"]) // 86_400_000
                if cap and day_sig.get(dk, 0) >= cfg.macro_max_signals_day:
                    continue
                if cap and last_t and \
                        bars[i]["t"] - last_t < cfg.macro_cooldown_bars * TF_MS:
                    continue
                seen.add(key)
                day_sig[dk] = day_sig.get(dk, 0) + 1
                last_t = bars[i]["t"]
                total += 1
                per_coin[coin] = per_coin.get(coin, 0) + 1
                res = simulate(bars, i, st, cfg)
                if res.get("filled"):
                    filled += 1
                    rs.append(res["r"])
        wins = sum(1 for r in rs if r > 0)
        exp = sum(rs) / len(rs) if rs else 0.0
        per_asset_day = total / max(1, len(data)) / n_days
        print(f"{name:26s} setups {total:4d} ({per_asset_day:4.2f}/asset/day) "
              f"filled {filled:4d} | exp {exp:+.3f}R | win "
              f"{(wins / len(rs) * 100 if rs else 0):3.0f}% | "
              f"total {(sum(rs) if rs else 0):+7.1f}R")
        return exp, total

    base = Config()
    quick = len(sys.argv) > 3 and sys.argv[3] == "quick"
    if len(sys.argv) > 3 and sys.argv[3] == "sweep":
        print("--- GRID SWEEP: hunting a positive entry config ---")
        import itertools
        grid = [
            ("entry", ["retest", "momentum"]),
            ("min_leg", [2.5, 3.0, 4.0]),
            ("stop_buf", [0.35, 0.60]),
            ("tp1_atr", [1.5, 2.5]),
        ]
        best = []
        for vals in itertools.product(*[v for _, v in grid]):
            kw = dict(zip([k for k, _ in grid], vals))
            cfg = Config(session_strict=True, flat_enabled=True,
                         macro_entry_mode=kw["entry"],
                         macro_min_leg_atr=kw["min_leg"],
                         macro_sl_buffer_atr=kw["stop_buf"],
                         macro_tp1_atr=kw["tp1_atr"],
                         trail_pivot_bars=3)
            name = "%s|leg%.1f|buf%.2f|tp1%.1f" % (
                kw["entry"][:3], kw["min_leg"], kw["stop_buf"],
                kw["tp1_atr"])
            exp, total = run(name, cfg, align=True, cap=True, session=True)
            best.append((exp, total, name, kw))
        best.sort(key=lambda x: -x[0])
        print("\nTOP 5 by expectancy:")
        for exp, total, name, kw in best[:5]:
            print("  %+.3fR/trade | %4d setups | %s" % (exp, total, name))
        return 0

    if quick:
        print("--- long-window check (align=1h, cooldown+cap) ---")
        run("retest entry, no filters", base)
        run("+ strict sessions only", Config(), align=True, cap=True,
            session=True)
        run("+ vol confirm at fill", Config(), align=True, cap=True,
            session=True)
        run("LIVE CONFIG (sessions+flat+vol)",
            Config(session_strict=True, flat_enabled=True),
            align=True, cap=True, session=True)
        return 0
    print("--- entry geometry (align=1h trend, cooldown+3/day cap) ---")
    run("retest entry (shipped)", base)
    run("momentum entry", Config(macro_entry_mode="momentum"))
    run("flip-hold required", Config(macro_require_flip=True))
    run("momentum + flip-hold", Config(macro_entry_mode="momentum",
                                        macro_require_flip=True))
    print("--- exits / stops on the best geometry ---")
    run("momentum + 0.6ATR stop", Config(macro_entry_mode="momentum",
                                         macro_sl_buffer_atr=0.60))
    run("momentum + TP1 2.5ATR", Config(macro_entry_mode="momentum",
                                        macro_tp1_atr=2.5))
    run("momentum + 25% at TP1", Config(macro_entry_mode="momentum",
                                        tp1_frac=0.25, tp2_frac=0.35))
    run("momentum + leg>=3xATR", Config(macro_entry_mode="momentum",
                                        macro_min_leg_atr=3.0))
    run("momentum + deep pullback", Config(macro_entry_mode="momentum",
                                           macro_retr_min=0.25,
                                           macro_retr_max=0.75))
    run("momentum all-in", Config(macro_entry_mode="momentum",
                                  macro_min_leg_atr=3.0,
                                  macro_sl_buffer_atr=0.60,
                                  macro_retr_min=0.25, macro_retr_max=0.75,
                                  tp1_frac=0.25, tp2_frac=0.35,
                                  macro_tp1_atr=2.5))
    return 0


if __name__ == "__main__":
    d = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    c = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    raise SystemExit(asyncio.run(main(d, c)))
