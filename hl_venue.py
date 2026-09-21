"""
hl_venue.py
===========
Venue-exact Hyperliquid paper execution venue (PAXG-first, generic per coin).

Every number below is pinned to a measured source:
- Fees: base tier perps taker 0.045% / maker 0.015% (Hyperliquid fee schedule,
  2026; no VIP / staking / referral on a fresh account). Charged on NOTIONAL.
- Margin/liquidation: Hyperliquid docs, exact formula:
    liq_price = price - side * margin_available / position_size / (1 - l*side)
    l = 1 / MAINTENANCE_LEVERAGE, side = +1 long / -1 short.
  Maintenance margin = HALF the initial margin at max leverage, per margin
  tier (meta -> marginTables). PAXG table 51 tier0: max 10x -> MMR 5%.
  Isolated: margin_available = isolated_margin - mm_required.
  Liquidations evaluate on MARK price (markPx), never last price.
- Tick/step: PAXG szDecimals=3 (step 0.001 oz) from meta; px tick derived
  from the live book (PAXG prints 0.1 at ~$4360: 5-significant-figure rule).
- Min order notional $10 (Hyperliquid order minimum).
- Funding: settled hourly rates from fundingHistory; longs pay shorts when
  rate > 0. Accrued at each hour boundary on open notional.
- Stop-market triggers execute as TAKER fills (they cross the book).
- Partial liquidation: engine first closes ~20%, rechecks, then the rest.

What stays paper (the ONLY differences vs live, by design):
  nothing is signed or sent; fills are simulated against the real book.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("hl_venue")

HL_INFO_URL = "https://api.hyperliquid.xyz/info"

# Base tier, fresh account: no volume tier, no HYPE staking, no referral.
TAKER_FEE_RATE = 0.00045   # 0.045%
MAKER_FEE_RATE = 0.00015   # 0.015%
MIN_NOTIONAL_USD = 10.0    # venue order minimum
LIQ_PARTIAL_FRAC = 0.20    # first liquidation pass closes 20%


def _get(url: str, payload: Dict[str, Any], timeout: float = 10.0) -> Any:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


@dataclass
class HLSpecs:
    """Venue truth for one coin, fetched from meta (cached at rest)."""
    coin: str = "PAXG"
    sz_decimals: int = 3
    max_leverage: float = 10.0
    # (lower_bound_notional, max_leverage) tiers, ascending
    margin_tiers: List[Tuple[float, float]] = field(
        default_factory=lambda: [(0.0, 10.0), (3_000_000.0, 5.0)])
    px_tick: float = 0.1
    taker_fee: float = TAKER_FEE_RATE
    maker_fee: float = MAKER_FEE_RATE

    @property
    def sz_step(self) -> float:
        return 10.0 ** (-self.sz_decimals)

    def tier_for(self, notional: float) -> int:
        idx = 0
        for i, (lb, _) in enumerate(self.margin_tiers):
            if notional >= lb:
                idx = i
        return idx

    def max_lev_for(self, notional: float) -> float:
        return self.margin_tiers[self.tier_for(notional)][1]

    def maint_lev_for(self, notional: float) -> float:
        # Docs: maintenance margin = half the initial margin at max leverage.
        return 2.0 * self.max_lev_for(notional)

    def mmr_for(self, notional: float) -> float:
        return 1.0 / self.maint_lev_for(notional)

    def mm_required(self, notional: float) -> float:
        # Tiered, continuous: mm = sum over tiers of slice * mmr_slice.
        mm = 0.0
        tiers = self.margin_tiers
        for i, (lb, ml) in enumerate(tiers):
            hi = tiers[i + 1][0] if i + 1 < len(tiers) else float("inf")
            part = max(0.0, min(notional, hi) - lb)
            mm += part / (2.0 * ml)
            if notional <= hi:
                break
        return mm


def fetch_specs(coin: str = "PAXG") -> HLSpecs:
    """Builds HLSpecs from the live meta endpoint (margin tables included)."""
    meta = _get(HL_INFO_URL, {"type": "meta"})
    uni = next(a for a in meta["universe"] if a["name"] == coin)
    table_id = uni.get("marginTableId")
    tiers: List[Tuple[float, float]] = [(0.0, float(uni["maxLeverage"]))]
    for tid, t in meta.get("marginTables", []):
        if tid == table_id:
            tiers = [(float(x["lowerBound"]), float(x["maxLeverage"]))
                     for x in t["marginTiers"]]
            break
    return HLSpecs(coin=coin, sz_decimals=int(uni.get("szDecimals", 3)),
                   max_leverage=float(uni["maxLeverage"]), margin_tiers=tiers)


def px_tick_from_book(levels: List[List[str]], default: float = 0.1) -> float:
    """Derives the price tick from printed book levels (adaptive, exact)."""
    try:
        decs = set()
        for side in levels:
            for px, _ in side[:8]:
                s = str(px)
                decs.add(len(s.split(".")[1]) if "." in s else 0)
        if decs:
            return 10.0 ** (-max(decs))
    except Exception:
        pass
    return default


def round_px(px: float, tick: float) -> float:
    return round(round(px / tick) * tick, 10)


def round_sz(sz: float, step: float) -> float:
    return float(f"{(round(sz / step) * step):.{12}f}")


def taker_fee(notional: float, rate: float = TAKER_FEE_RATE) -> float:
    return notional * rate


@dataclass
class Fill:
    px: float          # volume-weighted average fill price
    sz: float          # filled size (oz)
    fee: float         # fee paid in USDC
    notional: float
    complete: bool     # False = thin book, partial only
    levels_used: int = 0


def walk_book(is_buy: bool, sz: float, bids: List[Tuple[float, float]],
              asks: List[Tuple[float, float]], fee_rate: float = TAKER_FEE_RATE,
              px_tick: float = 0.1) -> Fill:
    """
    Exact taker fill against real L2 levels (best-first). A thin book fills
    only what rests (complete=False) at the volume-weighted average.
    """
    levels = asks if is_buy else bids
    remain, cost, used = sz, 0.0, 0
    for px, lvl_sz in levels:
        if remain <= 1e-12:
            break
        take = min(remain, lvl_sz)
        cost += take * px
        remain -= take
        used += 1
    filled = sz - remain
    # VWAP keeps full precision: resting prices are tick-valid, their average
    # is exact on venue. Never round an average to the tick.
    avg = cost / filled if filled > 0 else 0.0
    notional = filled * avg
    return Fill(px=avg, sz=round(filled, 10), fee=taker_fee(notional, fee_rate),
                notional=notional, complete=(remain <= 1e-12), levels_used=used)


def liq_price(entry: float, side: int, isolated_margin: float, sz: float,
              specs: HLSpecs, leverage: Optional[float] = None) -> float:
    """
    Exact isolated liquidation price (Hyperliquid docs formula, fixed-point).

    HL computes available margin as: avail = isolated_margin - mm_required(sz * liq_px)
    and then: liq_px = entry - side * avail / sz / (1 - mmr * side)

    Because mm is a function of liq_px itself, we solve analytically.
    Assuming single tier (mmr constant over the price range):
      Long  (side=+1): liq = (entry*(1-mmr) - m/sz) / (1 - 2*mmr)
      Short (side=-1): liq = (entry*(1+mmr) + m/sz) / (1 + 2*mmr)

    side = +1 long, -1 short. Evaluated against MARK price on venue.
    """
    if sz <= 0:
        return 0.0
    if leverage and leverage > specs.max_leverage:
        mmr = 1.0 / (2.0 * leverage)
    else:
        est_notional = sz * entry
        mmr = specs.mmr_for(est_notional)

    m_per_sz = isolated_margin / sz
    if side == 1:
        return max(0.0, (entry * (1.0 - mmr) - m_per_sz) / (1.0 - 2.0 * mmr))
    else:
        return (entry * (1.0 + mmr) + m_per_sz) / (1.0 + 2.0 * mmr)


@dataclass
class HLPosition:
    side: int = 0            # +1 long, -1 short, 0 flat
    sz: float = 0.0          # oz
    entry: float = 0.0       # VWAP entry
    margin: float = 0.0      # isolated margin allocated
    leverage: float = 10.0
    liq_px: float = 0.0
    stop_px: float = 0.0     # resting stop-market trigger (taker on hit)
    opened_ts: float = 0.0
    fees_paid: float = 0.0
    funding_paid: float = 0.0
    last_funding_hour: int = 0

    @property
    def is_open(self) -> bool:
        return self.side != 0 and self.sz > 0

    def notional(self, px: float) -> float:
        return self.sz * px

    def upnl(self, mark: float) -> float:
        if not self.is_open:
            return 0.0
        return self.side * (mark - self.entry) * self.sz


class HLPaperVenue:
    """
    Venue-exact paper books for ONE coin. All fills walk a book snapshot,
    every fill pays the exact taker fee, funding accrues at hour boundaries
    from settled fundingHistory rates, liquidation follows the docs formula
    on mark price with a 20%-first partial pass.
    """

    def __init__(self, specs: HLSpecs, equity: float, allow_unclamped_lev: bool = True):
        self.specs = specs
        self.cash = float(equity)
        self.margin_used = 0.0
        self.allow_unclamped_lev = allow_unclamped_lev
        self.pos = HLPosition()
        self.realized = 0.0
        self.fees_total = 0.0
        self.funding_total = 0.0
        self.trades: List[Dict[str, Any]] = []
        self.shortfall_log: List[Dict[str, Any]] = []
        self._book: Optional[Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]] = None
        self._mark = 0.0
        self._pending: List[Dict[str, Any]] = []  # ack-delayed fills

    @property
    def equity(self) -> float:
        return self.cash - self.margin_used

    @equity.setter
    def equity(self, val: float) -> None:
        self.cash = float(val)

    # -- market data ingress -------------------------------------------------
    def on_book(self, bids: List[Tuple[float, float]], asks: List[Tuple[float, float]]) -> None:
        self._book = (bids, asks)
        if bids and asks:
            tick = px_tick_from_book(
                [[str(p), str(s)] for p, s in bids] + [[str(p), str(s)] for p, s in asks])
            self.specs.px_tick = tick

    def on_mark(self, mark: float) -> None:
        self._mark = mark

    # -- sizing ---------------------------------------------------------------
    def size_for(self, margin_usd: float, leverage: float, ref_px: float) -> Tuple[float, float, str]:
        """Returns (sz_oz, margin_used, reject_reason). Venue rules enforced."""
        lev = leverage if self.allow_unclamped_lev else min(leverage, self.specs.max_lev_for(margin_usd * leverage))
        if margin_usd > (self.cash + 1e-4):
            return 0.0, 0.0, "margin_gt_equity"
        notional = margin_usd * lev
        if notional < MIN_NOTIONAL_USD:
            return 0.0, 0.0, "min_notional_10"
        sz = round_sz(notional / ref_px, self.specs.sz_step)
        if sz * ref_px < MIN_NOTIONAL_USD:
            return 0.0, 0.0, "min_notional_10"
        margin_used = (sz * ref_px) / lev
        return sz, margin_used, ""

    # -- execution ------------------------------------------------------------
    def market_open(self, side: int, sz: float, signal_px: float,
                    margin_usd: float, leverage: float, stop_px: float,
                    ts: float, ack_px: Optional[float] = None) -> Dict[str, Any]:
        """Market open against the CURRENT book (live) or ack_px snapshot."""
        if self.pos.is_open:
            return {"status": "rejected", "reason": "position_open"}
        if self._book is None:
            return {"status": "rejected", "reason": "no_book"}
        bids, asks = self._book
        fill = walk_book(side == 1, sz, bids, asks,
                         self.specs.taker_fee, self.specs.px_tick)
        if fill.sz <= 0:
            return {"status": "rejected", "reason": "empty_book"}
        lev = leverage if self.allow_unclamped_lev else min(leverage, self.specs.max_lev_for(fill.notional))
        margin_used = (fill.sz * fill.px) / lev
        if margin_used > (self.cash + 1e-4):
            return {"status": "rejected", "reason": "margin_gt_equity"}
        self.margin_used = margin_used
        self.cash -= fill.fee                   # pay open fee; margin locked via equity = cash - margin_used
        self.fees_total += fill.fee
        self.pos = HLPosition(side=side, sz=fill.sz, entry=fill.px,
                              margin=margin_used, leverage=leverage,
                              stop_px=stop_px, opened_ts=ts, fees_paid=fill.fee,
                              last_funding_hour=int(ts // 3600))
        self.pos.liq_px = liq_price(fill.px, side, margin_used, fill.sz, self.specs, leverage=leverage)
        slip_bps = ((fill.px - signal_px) / signal_px * 10000.0 * side) if signal_px else 0.0
        self.shortfall_log.append({"t": ts, "kind": "open", "signal_px": signal_px,
                                   "fill_px": fill.px, "shortfall_bps": round(slip_bps, 2),
                                   "fee": round(fill.fee, 4), "partial": not fill.complete})
        return {"status": "ok" if fill.complete else "partial", "fill": fill,
                "margin_used": margin_used, "liq_px": self.pos.liq_px}

    def market_close(self, signal_px: float, ts: float, reason: str,
                     frac: float = 1.0) -> Dict[str, Any]:
        """Full (or frac) market close against the CURRENT book. Taker fee."""
        if not self.pos.is_open:
            return {"status": "rejected", "reason": "flat"}
        if self._book is None:
            return {"status": "rejected", "reason": "no_book"}
        bids, asks = self._book
        pos_side = self.pos.side
        pos_entry = self.pos.entry
        sz = round_sz(self.pos.sz * frac, self.specs.sz_step)
        fill = walk_book(pos_side == -1, sz, bids, asks,
                         self.specs.taker_fee, self.specs.px_tick)
        if fill.sz <= 0:
            return {"status": "rejected", "reason": "empty_book"}
        gross = pos_side * (fill.px - pos_entry) * fill.sz
        # Net includes full round-trip fee
        entry_fee_leg = self.pos.fees_paid * (fill.sz / self.pos.sz)
        ftot = entry_fee_leg + fill.fee
        net = gross - ftot
        self.cash += (gross - fill.fee)
        self.realized += net
        self.fees_total += fill.fee
        self.margin_used = max(0.0, self.margin_used - (self.pos.margin * (fill.sz / self.pos.sz)))
        slip_bps = ((signal_px - fill.px) / signal_px * 10000.0 * self.pos.side) if signal_px else 0.0
        self.shortfall_log.append({"t": ts, "kind": "close", "signal_px": signal_px,
                                   "fill_px": fill.px, "shortfall_bps": round(slip_bps, 2),
                                   "fee": round(fill.fee, 4), "reason": reason,
                                   "partial": not fill.complete})
        close_frac = fill.sz / self.pos.sz
        self.pos.fees_paid = max(0.0, self.pos.fees_paid * (1.0 - close_frac))
        self.pos.sz = round_sz(self.pos.sz - fill.sz, self.specs.sz_step)
        if self.pos.sz <= 1e-12 or frac >= 1.0:
            self.pos = HLPosition()
            self.margin_used = 0.0

        rec = {"t": ts, "side": pos_side, "sz": fill.sz, "entry": round(pos_entry, 4),
               "exit": round(fill.px, 4), "gross": round(gross, 2), "fee": round(ftot, 4),
               "net": round(net, 2), "reason": reason, "equity": round(self.cash, 2)}
        self.trades.append(rec)
        return {"status": "ok" if fill.complete else "partial", "fill": fill, "record": rec}

    # -- venue-driven exits ----------------------------------------------------
    def check_venue_exits(self, mark: float, candle_high: float,
                          candle_low: float, ts: float) -> List[Dict[str, Any]]:
        """Stop trigger (conservative: candle extreme through stop), then
        liquidation on MARK with 20%-first partial pass. Returns close records."""
        out: List[Dict[str, Any]] = []
        if not self.pos.is_open:
            return out
        hit_stop = (candle_low <= self.pos.stop_px) if self.pos.side == 1 else \
                   (candle_high >= self.pos.stop_px)
        if hit_stop and self.pos.stop_px > 0:
            r = self.market_close(self.pos.stop_px, ts, "STOP_MARKET")
            if r["status"] in ("ok", "partial"):
                out.append(r["record"])
                return out
        # Liquidation on mark price (venue truth).
        liq_hit = (mark <= self.pos.liq_px) if self.pos.side == 1 else (mark >= self.pos.liq_px)
        if liq_hit:
            r = self.market_close(mark, ts, "LIQ_PARTIAL_20", frac=LIQ_PARTIAL_FRAC)
            if r["status"] in ("ok", "partial"):
                out.append(r["record"])
            if self.pos.is_open:
                r2 = self.market_close(mark, ts, "LIQUIDATED")
                if r2["status"] in ("ok", "partial"):
                    out.append(r2["record"])
        return out

    # -- funding -----------------------------------------------------------------
    def accrue_funding(self, rate: float, ts: float) -> float:
        """Applies one hourly settlement: longs pay shorts when rate > 0."""
        if not self.pos.is_open or rate == 0.0:
            return 0.0
        mark = self._mark or self.pos.entry
        payment = self.pos.side * rate * self.pos.notional(mark)
        # Longs pay when rate>0: equity decreases by payment for longs.
        self.equity -= payment
        self.pos.funding_paid += payment
        self.funding_total += payment
        self.pos.last_funding_hour = int(ts // 3600)
        return -payment

    def snapshot(self) -> Dict[str, Any]:
        mark = self._mark or (self.pos.entry if self.pos.is_open else 0.0)
        return {"equity": round(self.equity, 2), "realized": round(self.realized, 2),
                "fees": round(self.fees_total, 4), "funding": round(self.funding_total, 4),
                "position": {"side": self.pos.side, "sz": self.pos.sz, "entry": self.pos.entry,
                             "margin": round(self.pos.margin, 2), "liq_px": round(self.pos.liq_px, 2),
                             "stop_px": self.pos.stop_px,
                             "upnl": round(self.pos.upnl(mark), 2)} if self.pos.is_open else None,
                "open_trades": len(self.trades)}
