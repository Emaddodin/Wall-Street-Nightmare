"""THE LAB runner -- strategy -> backtest -> measure win rate -> mutate.

One-shot job (systemd timer, once a day after the Tehran day rollover):

    * builds a smart variety of coins (majors / mids / micro-caps,
      stratified by their own volatility, plus the live feed symbols);
    * downloads whatever candle history the store is missing;
    * runs the inventor loop (in-sample evolution, out-of-sample verdict);
    * writes data/state/inventions.json for the app's "the lab" card;
    * pushes one ntfy with the headline result.

Read-only for everything except its own report file: the live book is
never touched.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

log = logging.getLogger("scalper.invent")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

DATA = Path(os.getenv("SCALPER_DATA", str(ROOT / "data")))
CANDLES = DATA / "candles"
OUT = DATA / "state" / "inventions.json"
LESSONS = DATA / "state" / "lessons.jsonl"


def _ntfy(msg: str) -> None:
    topic = os.getenv("NTFY_TOPIC")
    if not topic:
        return
    try:
        import urllib.request
        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}", data=msg.encode(),
            headers={"Title": "scalper-lab"})
        urllib.request.urlopen(req, timeout=8).close()
    except Exception as e:
        log.warning("ntfy push failed: %s", e)


def _inv_cfg() -> dict:
    from config.loader import load_config
    cfg = load_config(extra_file="config/aggressive.yaml")
    return dict(cfg.raw().get("inventor") or {})


def _needed_bars(days: int) -> int:
    # window + engine warmup (500 x 15m bars) + breathing room
    return days * 1440 + 6 * 1440 + 2000


def pick_universe(client, store, ic: dict) -> list[str]:
    """A smart variety, not one favourite: the top coins of each size
    class by 24h volume, plus every symbol the store already follows and
    every live position."""
    import pandas as pd
    syms: set[str] = set(store.symbols("1m"))
    try:
        for p in json.loads((DATA / "state" / "paper.json").read_text()) \
                .get("positions", []):
            syms.add(p["symbol"])
    except Exception:
        pass
    try:
        ticks = client.tickers()
    except Exception as e:
        log.warning("ticker fetch failed (%s) -- using store symbols", e)
        return sorted(syms)
    rows = [t for t in ticks if t["symbol"].endswith("USDT")
            and t["usdt_volume_24h"] > 0]
    df = pd.DataFrame(rows).sort_values("usdt_volume_24h", ascending=False)
    per = int(ic.get("coins_per_class", 6))
    majors = df[df["usdt_volume_24h"] >= 200_000_000]
    mids = df[(df["usdt_volume_24h"] >= 20_000_000)
              & (df["usdt_volume_24h"] < 200_000_000)]
    micros = df[(df["usdt_volume_24h"] >= 1_000_000)
                & (df["usdt_volume_24h"] < 20_000_000)]
    for sub in (majors, mids, micros):
        for s in sub["symbol"].head(per):
            syms.add(s)
    log.info("universe: majors %d, mids %d, micros %d -> %d symbols",
             len(majors.head(per)), len(mids.head(per)),
             len(micros.head(per)), len(syms))
    return sorted(syms)


def ensure_candles(client, store, syms: list[str], days: int) -> None:
    need = _needed_bars(days)
    end_ms = int(time.time() * 1000) // 60_000 * 60_000
    for sym in syms:
        try:
            have = store.load(sym, "1m")
            if len(have) >= need:
                continue
            log.info("fetch %s (%d/%d bars)", sym, len(have), need)
            df = client.klines(sym, "1m", need, end_ms=end_ms)
            if len(df) < 500:
                continue
            if len(have):
                import pandas as pd
                df = pd.concat([have, df]).drop_duplicates(
                    subset="open_time", keep="last").sort_values("open_time")
            df = df[df["open_time"] >= end_ms - days * 86_400_000 - 2000 * 60_000]
            store.save(sym, "1m", df.reset_index(drop=True))
        except Exception as e:
            log.warning("candles %s failed: %s", sym, e)


def main() -> int:
    ic = _inv_cfg()
    if not ic.get("enabled", True):
        log.info("inventor disabled in config -- nothing to do")
        return 0
    # grand-hunt overrides: big database runs are launched from the env
    ic = dict(ic)
    for key, env in (("backtest_days", "SCALPER_LAB_DAYS"),
                     ("population", "SCALPER_LAB_POP"),
                     ("generations", "SCALPER_LAB_GENS")):
        v = os.environ.get(env)
        if v:
            ic[key] = int(v)
            log.info("%s override: %s", env, v)
    from market_data.client import BitunixPublic
    from market_data.store import CandleStore
    from strategies.inventor import invent

    client = BitunixPublic()
    store = CandleStore(CANDLES)
    syms = pick_universe(client, store, ic)
    days = int(ic.get("backtest_days", 3))
    ensure_candles(client, store, syms, days)

    syms_cap = int(os.environ.get("SCALPER_LAB_SYMS", "0"))
    if syms_cap > 0 and len(syms) > syms_cap:
        from market_data.store import CandleStore as _CS
        _st = _CS(CANDLES)
        _d = {s: len(_st.load(s, "1m")) for s in syms}
        syms = sorted(syms, key=lambda s: -_d.get(s, 0))[:syms_cap]
        log.info("grand hunt: capped to %d deepest symbols", len(syms))
    frames = {}
    for s in syms:
        try:
            df = store.load(s, "1m")
            if len(df) >= 1500:
                frames[s] = df
        except Exception:
            continue
    if not frames:
        log.error("no candles for any symbol -- abort")
        return 1

    report = invent(DATA, frames, inv_cfg=ic, log=log.info)
    if not report.get("ok"):
        log.error("lab failed: %s", report.get("why"))
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=1, default=str))
    tmp.replace(OUT)

    champ = report["champion"]
    best = report["best"] or {}
    lines = [
        "lab run done",
        f"champion: {champ.get('n')}t wr {champ.get('wr', 0):.0%} "
        f"avgR {champ.get('avg_r', 0):+.2f} pf {champ.get('pf', 0):.2f}",
    ]
    if best.get("n"):
        lines.append(
            f"best idea: {best['name']} -> {best.get('n')}t "
            f"wr {best.get('wr', 0):.0%} avgR {best.get('avg_r', 0):+.2f} "
            f"pf {best.get('pf', 0):.2f} dd {best.get('max_dd_pct', 0):.0f}%")
        coins = best.get("coins", {}).get("recommendation", "")
        if coins:
            lines.append(f"coins: {coins}")
        for ch in best.get("changes", [])[:4]:
            lines.append(f"- {ch}")
    else:
        lines.append("no idea beat the champion out-of-sample")
    if report.get("adopt"):
        lines.append("ADOPT-READY: " + report["adopt"]["name"])
    else:
        lines.append(f"adopt gate: {'OPEN' if report.get('adopt_gate') else 'closed'}")
    for l in lines:
        log.info(l)
    _ntfy("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
