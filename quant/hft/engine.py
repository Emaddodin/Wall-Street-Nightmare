"""
quant/hft/engine.py
====================
Main HFT Orchestrator — ties together all five pillars:

  1. Data Feed      → HyperliquidFeed (L2 WebSocket)
  2. Alpha          → MultiHawkes + SignalEngine (OFI + CatBoost)
  3. Execution      → AvellanadaStoikov + SquareRootSplitter
  4. Risk           → FractionalKelly + Isolated Margin math
  5. Exit           → ChandelierExit with ATR Ratchet
     Volatility     → GARCH(1,1) with Merton jump detection

Entry loop (per symbol, every tick):
  book_update → GARCH → Hawkes → SignalEngine → Kelly → AS quotes → Chandelier
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from .alpha import MultiHawkes, SignalEngine, SignalResult
from .data_feed import HyperliquidFeed, OrderBook
from .execution import AvellanadaStoikov, SquareRootSplitter
from .exits import ChandelierExit, ExitState
from .models import GARCH11, VolatilityState
from .risk import FractionalKelly, PositionSpec
from .monitor import LiveMonitorAgent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Open position tracker
# ---------------------------------------------------------------------------

@dataclass
class OpenPosition:
    spec: PositionSpec
    open_time: float = field(default_factory=time.time)
    chandelier: ChandelierExit = field(default_factory=ChandelierExit)
    peak_pnl_pct: float = 0.0

    def __post_init__(self) -> None:
        self.chandelier.init_position(self.spec.side, self.spec.entry_price)

    def current_pnl_pct(self, price: float) -> float:
        if self.spec.side == "long":
            return (price - self.spec.entry_price) / self.spec.entry_price
        return (self.spec.entry_price - price) / self.spec.entry_price

    def update_peak(self, price: float) -> None:
        pnl = self.current_pnl_pct(price)
        if pnl > self.peak_pnl_pct:
            self.peak_pnl_pct = pnl


# ---------------------------------------------------------------------------
# Per-symbol state
# ---------------------------------------------------------------------------

@dataclass
class SymbolState:
    symbol: str
    garch: GARCH11 = field(default_factory=GARCH11)
    hawkes: MultiHawkes = field(default_factory=MultiHawkes)
    signal_engine: SignalEngine = field(default_factory=SignalEngine)
    as_model: AvellanadaStoikov = field(default_factory=AvellanadaStoikov)
    splitter: SquareRootSplitter = field(default_factory=SquareRootSplitter)
    position: Optional[OpenPosition] = None
    last_vol: VolatilityState = field(default_factory=lambda: VolatilityState(
        sigma=0.01, sigma_annual=0.5, is_jump=False, jump_rate=0.0, cluster_active=False
    ))
    prev_snapshot: object = None  # OFISnapshot


# ---------------------------------------------------------------------------
# Engine config
# ---------------------------------------------------------------------------

@dataclass
class EngineConfig:
    symbols: list[str]
    balance_usdt: float = 1_000.0
    kelly_fraction: float = 0.25          # Quarter-Kelly
    max_leverage: int = 20
    mmr: float = 0.005                    # BTC maintenance margin rate
    safety_factor: float = 2.0
    max_risk_pct: float = 0.10
    min_confidence: float = 0.60
    min_hawkes_ratio: float = 1.5
    max_spread_bps: float = 20.0
    model_path: Optional[str] = None
    dry_run: bool = True                  # paper mode — no real orders


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------

class HFTEngine:
    """
    Async main loop orchestrator.

    Usage
    -----
    cfg = EngineConfig(symbols=["BTC", "ETH"], balance_usdt=500.0, dry_run=True)
    engine = HFTEngine(cfg)
    await engine.run()
    """

    def __init__(self, config: EngineConfig) -> None:
        self.config = config
        self._states: Dict[str, SymbolState] = {
            s: SymbolState(
                symbol=s,
                signal_engine=SignalEngine(model_path=config.model_path),
                as_model=AvellanadaStoikov(
                    gamma=0.1,
                    kappa=1.5,
                    sigma=0.02,
                    epoch_sec=300.0,
                    max_inventory=5.0,
                ),
            )
            for s in config.symbols
        }
        self._kelly = FractionalKelly(
            fraction=config.kelly_fraction,
            max_leverage=config.max_leverage,
            mmr=config.mmr,
            safety_factor=config.safety_factor,
            max_risk_pct=config.max_risk_pct,
        )
        self._feed = HyperliquidFeed(
            symbols=config.symbols,
            on_book_update=self._on_book_update,
            on_trade=self._on_trade,
        )
        self._running = False
        self._balance = config.balance_usdt
        self._trade_count = 0
        self._pnl_total = 0.0
        self.monitor = LiveMonitorAgent()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def run(self) -> None:
        self._running = True
        logger.info(
            "HFT Engine starting | symbols=%s | balance=%.2f USDT | dry_run=%s",
            self.config.symbols, self._balance, self.config.dry_run,
        )
        await self.monitor.start()
        await self._feed.start()

    async def stop(self) -> None:
        self._running = False
        await self._feed.stop()
        await self.monitor.stop()

    # ------------------------------------------------------------------
    # Book update callback (called on every L2 tick)
    # ------------------------------------------------------------------

    async def _on_book_update(self, symbol: str, book: OrderBook) -> None:
        st = self._states.get(symbol)
        if st is None:
            return

        mid = book.mid_price
        if mid != mid:  # NaN guard
            return

        bids, asks = book.get_levels(5)
        if not bids or not asks:
            return

        bids_t = [(l.price, l.qty) for l in bids]
        asks_t = [(l.price, l.qty) for l in asks]

        # --- 1. Volatility (GARCH + jump detection) ---
        vol = st.garch.update(mid)
        st.last_vol = vol

        # Update AS model and splitter with live sigma
        sigma_per_bar = vol.sigma
        st.as_model.update_sigma(sigma_per_bar)
        st.splitter.update_sigma(sigma_per_bar * 16)  # scale to ~daily

        # --- 2. Chandelier on open position ---
        if st.position is not None:
            await self._manage_open_position(st, mid, bids_t[0][0], asks_t[0][0], vol)
            return  # no new signal while in trade

        # --- 3. Hawkes intensity check ---
        excited, hk_dir = st.hawkes.net_imbalance_excited(self.config.min_hawkes_ratio)
        if not excited:
            return  # wait for liquidity cluster

        # --- 4. Alpha / signal generation ---
        signal: SignalResult = await st.signal_engine.generate_signal(
            bids=bids_t,
            asks=asks_t,
            mid_price=mid,
            hawkes_buy_lam=st.hawkes.buy_intensity,
            hawkes_sell_lam=st.hawkes.sell_intensity,
            hawkes_excited=excited,
            hawkes_direction=hk_dir,
        )

        if not signal.is_valid:
            logger.debug("[%s] Signal invalid: %s", symbol, signal.reason)
            return

        # Direction alignment check: Hawkes direction must agree with ML
        ml_dir = "long" if signal.direction == 1 else "short"
        if hk_dir != "neutral" and ml_dir != hk_dir:
            logger.debug("[%s] Hawkes/ML direction conflict — skip.", symbol)
            return

        # --- 5. Kelly position sizing ---
        side = "long" if signal.direction == 1 else "short"
        spec = self._kelly.compute_position(
            symbol=symbol,
            side=side,
            balance_usdt=self._balance,
            entry_price=mid,
            win_prob=signal.confidence,
            win_loss_ratio=1.5,          # target R:R
            predicted_vol_pct=vol.sigma,
            atr_price=vol.sigma * mid,
        )

        if spec is None:
            logger.debug("[%s] Kelly rejected trade.", symbol)
            return

        # --- 6. Avellaneda-Stoikov quote check ---
        quote = st.as_model.compute_quotes(mid)
        await self.monitor.log_as_dynamics(quote.spread, st.as_model.inventory)
        spread_bps = book.spread_bps
        if spread_bps > self.config.max_spread_bps:
            logger.debug("[%s] Spread %.1f bps > limit.", symbol, spread_bps)
            return

        # --- 7. Square-Root Law split check ---
        qty = spec.qty_base
        impact = st.splitter.estimate_impact(qty)
        if impact > 0.002:  # >0.2% impact
            chunks = st.splitter.split(qty)
            logger.info(
                "[%s] Large order: splitting into %d TWAP chunks (impact=%.4f%%)",
                symbol, len(chunks), impact * 100,
            )
        else:
            chunks = [None]  # single market order

        await self._open_position(st, spec, signal, quote, symbol)

    # ------------------------------------------------------------------
    # Trade callback (updates Hawkes process)
    # ------------------------------------------------------------------

    async def _on_trade(self, symbol: str, trade: dict) -> None:
        st = self._states.get(symbol)
        if st is None:
            return
        side = trade.get("side", "").lower()
        ts = float(trade.get("time", time.time() * 1000)) / 1000.0
        if side in ("buy", "b"):
            st.hawkes.buy.update(ts)
        elif side in ("sell", "s"):
            st.hawkes.sell.update(ts)

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    async def _open_position(
        self,
        st: SymbolState,
        spec: PositionSpec,
        signal: SignalResult,
        quote,
        symbol: str,
    ) -> None:
        pos = OpenPosition(spec=spec)
        # Feed current ATR into chandelier
        atr_price = st.last_vol.sigma * spec.entry_price
        for _ in range(22):  # seed ATR buffer
            pos.chandelier.update_bar(
                high=spec.entry_price * (1 + st.last_vol.sigma),
                low=spec.entry_price * (1 - st.last_vol.sigma),
                close=spec.entry_price,
            )
        pos.chandelier.init_position(spec.side, spec.entry_price)
        st.position = pos

        mode = "[DRY RUN]" if self.config.dry_run else "[LIVE]"
        logger.info(
            "%s OPEN %s %s | entry=%.4f liq=%.4f stop=%.4f "
            "margin=%.2f USDT lev=%dx OFI_mean=%.3f conf=%.2f",
            mode, spec.side.upper(), symbol,
            spec.entry_price, spec.liq_price, spec.stop_price,
            spec.margin_usdt, spec.leverage,
            float(signal.ofi_vector.mean()), signal.confidence,
        )
        self._trade_count += 1
        await self.monitor.log_alpha_trigger(
            signal.confidence, 
            st.hawkes.buy_intensity, 
            st.hawkes.sell_intensity, 
            float(signal.ofi_vector.mean()), 
            spec.leverage, 
            spec.entry_price
        )

    async def _manage_open_position(
        self,
        st: SymbolState,
        price: float,
        high: float,
        low: float,
        vol: VolatilityState,
    ) -> None:
        pos = st.position
        if pos is None:
            return

        old_mult = pos.chandelier.current_multiplier
        pos.chandelier.update_bar(high=high, low=low, close=price)
        if pos.chandelier.current_multiplier < old_mult:
            await self.monitor.log_ratchet_shift(old_mult, pos.chandelier.current_multiplier, pos.current_pnl_pct(price))
        pos.update_peak(price)
        exit_state: ExitState = pos.chandelier.evaluate(price)

        pnl_pct = pos.current_pnl_pct(price)

        # Hard stop: price hit liquidation zone
        liq_breach = (
            (pos.spec.side == "long" and price <= pos.spec.liq_price * 1.02) or
            (pos.spec.side == "short" and price >= pos.spec.liq_price * 0.98)
        )

        should_close = exit_state.triggered or liq_breach
        reason = exit_state.trigger_reason if exit_state.triggered else ("liq_breach" if liq_breach else "")

        if should_close:
            pnl_usdt = pnl_pct * pos.spec.notional_usdt
            self._pnl_total += pnl_usdt
            self._balance += pnl_usdt
            mode = "[DRY RUN]" if self.config.dry_run else "[LIVE]"
            logger.info(
                "%s CLOSE %s %s | exit=%.4f pnl=%.2f%% pnl_usdt=%.2f "
                "stop=%.4f mult=%.1f ATR=%.4f reason=%s",
                mode, pos.spec.side.upper(), pos.spec.symbol,
                price, pnl_pct * 100, pnl_usdt,
                exit_state.stop_price, exit_state.multiplier,
                exit_state.atr, reason,
            )
            st.position = None
            st.as_model.reset_epoch()
            await self.monitor.log_exit(pos.spec.side, price, pnl_usdt, pnl_pct, reason)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        return {
            "balance_usdt": self._balance,
            "total_pnl_usdt": self._pnl_total,
            "trade_count": self._trade_count,
            "open_positions": {
                s: st.position.spec.side if st.position else None
                for s, st in self._states.items()
            },
        }
