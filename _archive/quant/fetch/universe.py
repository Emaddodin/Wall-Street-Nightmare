"""Symbol universe for the new quant project.

Binance fapi API is geo-blocked from this network, so volume ranking comes
from Bitunix (our actual venue) public tickers.  A curated list of globally
liquid majors (long Binance Vision histories) is forced into tier A; tier B
fills from the live Bitunix volume ranking.

Tiers:
  A  -- majors + current top volume: klines 36mo, metrics 24mo
  B  -- next by volume:              klines 24mo, metrics 12mo

Symbols whose zips are missing on Vision are simply absent for that period
(not listed yet).  If Bitunix is unreachable, falls back to the curated
majors list alone so research never blocks on network.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger("quant.fetch.universe")

# curated globally-liquid USDT-M perps with long histories on Binance Vision
MAJORS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "LINKUSDT", "AVAXUSDT", "LTCUSDT", "DOTUSDT", "TRXUSDT",
    "TONUSDT", "NEARUSDT", "SUIUSDT", "APTUSDT", "ARBUSDT", "OPUSDT",
    "FILUSDT", "UNIUSDT", "ATOMUSDT", "ETCUSDT", "INJUSDT", "SEIUSDT",
    "TIAUSDT", "ORDIUSDT", "RUNEUSDT", "AAVEUSDT", "MKRUSDT", "CRVUSDT",
    "EGLDUSDT", "GALAUSDT", "SANDUSDT", "MANAUSDT", "AXSUSDT", "CHZUSDT",
    "ALGOUSDT", "VETUSDT", "XLMUSDT", "EOSUSDT", "ICPUSDT", "KASUSDT",
    "FTMUSDT", "HBARUSDT", "IMXUSDT", "STXUSDT", "RENDERUSDT", "TAOUSDT",
    "FETUSDT", "WLDUSDT", "JUPUSDT", "PYTHUSDT", "ENAUSDT", "ONDOUSDT",
    "JASMYUSDT", "GRTUSDT", "1000PEPEUSDT", "1000SHIBUSDT", "WIFUSDT",
    "BONKUSDT", "FLOKIUSDT", "PEPEUSDT",
]

# tokenized commodities / odd pairs that are not crypto strategy targets
EXCLUDE = {"XAUUSDT", "XAGUSDT", "XAUTUSDT", "4USDT", "USUSDT",
           "USELESSUSDT", "MARSCOINUSDT"}


def bitunix_tickers() -> list[dict]:
    import sys
    ROOT = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(ROOT / "scalper"))
    from market_data.client import BitunixPublic
    c = BitunixPublic(pause=0.05)
    rows = []
    for t in c.tickers():
        if not t["symbol"].endswith("USDT"):
            continue
        if t["symbol"] in EXCLUDE:
            continue
        rows.append({"symbol": t["symbol"],
                     "quote_volume": t["usdt_volume_24h"]})
    return rows


def build_universe(out: Path, tier_a_n: int = 40, tier_b_n: int = 40) -> dict:
    seen: dict[str, str] = {}
    rows: list[dict] = []
    for s in MAJORS:
        seen[s] = "A"
    try:
        rows = bitunix_tickers()
        rows.sort(key=lambda t: -t["quote_volume"])
        for t in rows:
            if t["symbol"] in seen:
                continue
            if len([v for v in seen.values() if v == "A"]) < tier_a_n:
                seen[t["symbol"]] = "A"
            elif len([v for v in seen.values() if v == "B"]) < tier_b_n:
                seen[t["symbol"]] = "B"
    except Exception as e:
        log.warning("bitunix tickers failed (%s); majors-only universe", e)
    uni = {"built_at": time.time(),
           "symbols": {s: {"tier": seen[s]} for s in seen}}
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(uni, f, indent=1, sort_keys=True)
    tmp.replace(out)
    log.info("universe: %d symbols (%d A, %d B) -> %s", len(seen),
             sum(1 for v in seen.values() if v == "A"),
             sum(1 for v in seen.values() if v == "B"), out)
    return uni


def load_universe(path: Path) -> dict:
    if not path.exists():
        return build_universe(path)
    with open(path) as f:
        return json.load(f)
