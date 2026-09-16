"""
quant/hft/engine.py
===================
Master event-loop orchestrator for the 5-pillar quantitative HFT system.

Pillars integrated:
  1. Order Flow Imbalance (OFI) L1-L5 feature extraction
  2. Mutually Exciting Hawkes Process trade intensity tracking
  3. CatBoost microsecond direction prediction
  4. Avellaneda-Stoikov inventory-aware market making
  5. Fractional Kelly dynamic leverage sizing & ATR Chandelier trailing ratchets
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .alpha import MultiHawkes, SignalEngine, SignalResult
from .data_feed import HyperliquidFeed, OrderBook
from .execution import AvellanedaStoikov, SquareRootSplitter
from .exits import ChandelierExit, ExitState
from .models import GARCH11, VolatilityState
from .risk import FractionalKelly, PositionSpec
from .risk.day_planner import DayPlanner
from .monitor import LiveMonitorAgent
from .flow_checker import TradeFlowAuditor
from .utils.killzone import KillZoneGuard

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State containers
# ---------------------------------------------------------------------------

@dataclass
class OpenPosition:
    spec: PositionSpec
    chandelier: ChandelierExit
    highest_price: float
    lowest_price: float
    entry_ts: float = field(default_factory=time.time)

    def update_peak(self, price: float) -> None:
        if price > self.highest_price:
            self.highest_price = price
        if price < self.lowest_price:
            self.lowest_price = price

    def unrealized_pnl(self, current_price: float) -> float:
        if self.spec.side == "long":
            return (current_price - self.spec.entry_price) * self.spec.qty_base
        else:
            return (self.spec.entry_price - current_price) * self.spec.qty_base

    def current_pnl_pct(self, current_price: float) -> float:
        ret = (current_price - self.spec.entry_price) / self.spec.entry_price
        return ret if self.spec.side == "long" else -ret


@dataclass
class SymbolState:
    symbol: str
    garch: GARCH11 = field(default_factory=GARCH11)
    hawkes: MultiHawkes = field(default_factory=MultiHawkes)
    signal_engine: SignalEngine = field(default_factory=SignalEngine)
    as_model: AvellanedaStoikov = field(default_factory=AvellanedaStoikov)
    splitter: SquareRootSplitter = field(default_factory=SquareRootSplitter)
    position: Optional[OpenPosition] = None
    last_vol: VolatilityState = field(default_factory=lambda: VolatilityState(
        sigma=0.01, sigma_annual=0.5, is_jump=False, jump_rate=0.0, cluster_active=False
    ))
    _last_telemetry_ts: float = 0.0
    _last_conf: float = 0.0
    _last_dir: str = "NEUTRAL"
    _last_lev: int = 1
    _last_kelly: float = 0.0


# ---------------------------------------------------------------------------
# Engine config
# ---------------------------------------------------------------------------

@dataclass
class HFTEngineConfig:
    symbols: list[str] = field(default_factory=lambda: ["BTC"])
    balance_usdt: float = 65.0
    dry_run: bool = True
    model_path: Optional[str] = None
    kelly_fraction: float = 0.25
    max_leverage: int = 20
    max_spread_bps: float = 8.0
    min_hawkes_ratio: float = 1.3
    ofi_levels: int = 5
    ofi_window: int = 50


# ---------------------------------------------------------------------------
# Main Engine
# ---------------------------------------------------------------------------

class HFTEngine:
    def __init__(self, config: Optional[HFTEngineConfig] = None) -> None:
        self.config = config or HFTEngineConfig()
        self._states: dict[str, SymbolState] = {}
        for s in self.config.symbols:
            st = SymbolState(symbol=s)
            if self.config.model_path:
                st.signal_engine = SignalEngine(
                    model_path=self.config.model_path,
                    ofi_levels=self.config.ofi_levels,
                    window_ticks=self.config.ofi_window,
                )
            self._states[s] = st

        self._kelly = FractionalKelly(
            fraction=self.config.kelly_fraction,
            max_leverage=self.config.max_leverage,
        )
        self._feed = HyperliquidFeed(
            symbols=self.config.symbols,
            on_book_update=self._on_book_update,
            on_trade=self._on_trade,
        )
        self._running = False
        self._balance = self.config.balance_usdt
        self._trade_count = 0
        self._pnl_total = 0.0
        now_sec = time.time()
        self._current_utc_day = int(now_sec // 86400)
        self._day_start_balance = self._balance
        self._day_trades = 0
        self._day_target_pct = 1.0        # +100% daily target
        self._day_loss_limit_pct = 0.50   # -50% daily loss limit
        self._day_halted = False
        # Dynamic thresholds — recalculated each day from day_start_balance
        self._day_target_usdt  = self._day_start_balance * (1.0 + self._day_target_pct)
        self._day_loss_floor   = self._day_start_balance * (1.0 - self._day_loss_limit_pct)
        self.day_planner = DayPlanner(
            start_balance=self._balance,
            target_pct=self._day_target_pct,
            loss_limit_pct=self._day_loss_limit_pct,
        )
        self.monitor = LiveMonitorAgent()
        self.flow_auditor = TradeFlowAuditor()
        self.killzone = KillZoneGuard()

    async def run(self) -> None:
        self._running = True
        logger.info(
            "HFT Engine starting | symbols=%s | balance=%.2f USDT | dry_run=%s",
            self.config.symbols, self._balance, self.config.dry_run,
        )
        await self.monitor.start()
        self.flow_auditor.run_e2e_self_test(self)
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
        if mid is None or mid != mid:  # NaN guard
            return

        bids, asks = book.get_levels(5)
        if not bids or not asks:
            return

        bids_t = [(l.price, l.qty) for l in bids]
        self.flow_auditor.record_tick()
        asks_t = [(l.price, l.qty) for l in asks]

        # 1. Volatility update
        vol = st.garch.update(mid)
        st.last_vol = vol

        sigma_per_bar = vol.sigma
        st.as_model.update_sigma(sigma_per_bar)
        st.splitter.update_sigma(sigma_per_bar * 16)

        # 2. Warm up signal engine feature buffer
        st.signal_engine.feature_eng.update(bids_t, asks_t, mid)
        ofi_vec = st.signal_engine.feature_eng.compute_ofi_vector()
        ofi_mean = float(np.mean(ofi_vec)) if len(ofi_vec) else 0.0

        quote = st.as_model.compute_quotes(mid)

        # CatBoost inference preview for telemetry
        if st.signal_engine.feature_eng.is_ready():
            feat = st.signal_engine.feature_eng.get_feature_vector(
                bids_t, asks_t, st.hawkes.buy_intensity, st.hawkes.sell_intensity
            )
            p_up, p_down = st.signal_engine.predictor.predict_proba(feat)
            if p_up > p_down:
                st._last_dir = "LONG"
                st._last_conf = p_up
            else:
                st._last_dir = "SHORT"
                st._last_conf = p_down

            # Kelly preview
            k_spec = self._kelly.compute_position(
                symbol=symbol,
                side="long" if st._last_dir == "LONG" else "short",
                balance_usdt=self._balance,
                entry_price=mid,
                win_prob=st._last_conf,
                win_loss_ratio=1.5,
                predicted_vol_pct=vol.sigma,
                atr_price=vol.sigma * mid,
            )
            if k_spec:
                st._last_lev = k_spec.leverage
                st._last_kelly = k_spec.margin_usdt / self._balance if self._balance > 0 else 0.0

        # 3. Telemetry broadcast (every 1 second)
        now_ts = time.time()
        # Check UTC midnight rollover (00:00 UTC)
        current_day = int(now_ts // 86400)
        if current_day > self._current_utc_day:
            self._current_utc_day = current_day
            self._day_start_balance = self._balance
            self._day_trades = 0
            self._day_halted = False
            # Recompute thresholds from the new day's starting balance (compounding)
            self._day_target_usdt = self._day_start_balance * (1.0 + self._day_target_pct)
            self._day_loss_floor  = self._day_start_balance * (1.0 - self._day_loss_limit_pct)
            self.day_planner.reset_day(self._balance)
            day_num = current_day - int(1789430400 // 86400) + 1  # Path day counter
            asyncio.create_task(self.monitor.notify_day_rollover(
                day_num=max(1, day_num),
                balance=self._balance,
                target_balance=self._day_target_usdt,
            ))

        # Periodic E2E self-test every 10 mins
        if now_ts - self.flow_auditor.metrics.last_self_test_ts > 600.0:
            self.flow_auditor.run_e2e_self_test(self)

        # Check for prolonged inactivity notification
        if self.flow_auditor.should_alert_inactivity():
            diag_rep = self.flow_auditor.get_diagnostic_report()
            asyncio.create_task(self.monitor.notify_flow_diagnostic(diag_rep))
        if now_ts - st._last_telemetry_ts >= 1.0:
            st._last_telemetry_ts = now_ts
            pos_dict = None
            if st.position is not None:
                pos = st.position
                pnl_u = pos.unrealized_pnl(mid)
                pnl_pct_u = pos.current_pnl_pct(mid)
                pos_dict = {
                    "side": pos.spec.side.upper(),
                    "entry_price": round(pos.spec.entry_price, 2),
                    "current_price": round(mid, 2),
                    "qty": round(pos.spec.qty_base, 4),
                    "notional": round(pos.spec.notional_usdt, 2),
                    "unrealized_pnl": round(pnl_u, 2),
                    "unrealized_pnl_pct": round(pnl_pct_u * 100, 2),
                    "stop_price": round(pos.chandelier.evaluate(mid).stop_price, 2),
                    "ratchet_mult": round(pos.chandelier.current_multiplier, 1),
                    "leverage": pos.spec.leverage,
                }

            # Evaluate DayPlanner for regime and pacing
            self._day_plan = self.day_planner.evaluate(
                current_equity=self._balance,
                day_trades=self._day_trades,
            )
            asyncio.create_task(self.monitor.update_tick(
                mid=mid,
                bid=bids_t[0][0],
                ask=asks_t[0][0],
                spread_bps=book.spread_bps,
                as_spread=quote.spread,
                as_res=quote.reservation_price,
                as_inv=st.as_model.inventory,
                h_buy=st.hawkes.buy_intensity,
                h_sell=st.hawkes.sell_intensity,
                ofi_mean=ofi_mean,
                ofi_levels=list(ofi_vec),
                sigma=vol.sigma,
                balance=self._balance,
                realized_pnl=self._pnl_total,
                trade_count=self._trade_count,
                position=pos_dict,
                catboost_conf=st._last_conf,
                catboost_dir=st._last_dir,
                leverage=st._last_lev,
                kelly_f=st._last_kelly,
                killzone_label=self.killzone.zone_label(),
                day_plan=self.day_planner.to_dict(self._day_plan),
            ))
            self.monitor.metrics["trade_flow"] = self.flow_auditor.get_diagnostic_report()
            self.monitor._flush_state()

        # 4. Chandelier on open position
        if st.position is not None:
            await self._manage_open_position(st, mid, bids_t[0][0], asks_t[0][0], vol)
            return

        # 4b. DayPlanner gate — refuse new entries based on regime and quotas
        if not hasattr(self, '_day_plan'):
            self._day_plan = self.day_planner.evaluate(self._balance, self._day_trades)
        allowed, gate_reason = self.day_planner.should_allow_entry(self._day_plan)
        if not allowed:
            if not self._day_halted:
                logger.info("DayPlanner BLOCKED entry: %s (regime=%s)", gate_reason, self._day_plan.regime)
                self._day_halted = True
            return

        # 5. Kill zone gate — only take NEW entries inside ICT institutional windows
        in_kz, kz_name = self.killzone.check()
        if not in_kz:
            self.flow_auditor.record_outside_killzone()
            return


        # 6. Hawkes clustering check
        excited, hk_dir = st.hawkes.net_imbalance_excited(self.config.min_hawkes_ratio)
        if not excited:
            self.flow_auditor.record_hawkes_quiet()
            return

        # 7. Signal evaluation
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
            self.flow_auditor.record_confidence(signal.confidence)
            return

        # 7b. DayPlanner confidence floor — regime-adjusted minimum
        if signal.confidence < self._day_plan.confidence_floor:
            self.flow_auditor.record_confidence(signal.confidence)
            return

        ml_dir = "long" if signal.direction == 1 else "short"
        if hk_dir != "neutral" and ml_dir != hk_dir:
            self.flow_auditor.record_direction_conflict()
            return

        # 8. Kelly position sizing (with DayPlanner regime multiplier)
        side = "long" if signal.direction == 1 else "short"
        # Apply DayPlanner's Kelly multiplier and leverage cap
        adj_fraction = self.config.kelly_fraction * self._day_plan.kelly_multiplier
        adj_max_lev = min(self.config.max_leverage, self._day_plan.max_leverage_cap)
        regime_kelly = FractionalKelly(
            fraction=adj_fraction,
            max_leverage=adj_max_lev,
            mmr=self._kelly.mmr,
            safety_factor=self._kelly.safety_factor,
            max_risk_pct=self._kelly.max_risk_pct,
        )
        spec = regime_kelly.compute_position(
            symbol=symbol,
            side=side,
            balance_usdt=self._balance,
            entry_price=mid,
            win_prob=signal.confidence,
            win_loss_ratio=1.5,
            predicted_vol_pct=vol.sigma,
            atr_price=vol.sigma * mid,
        )

        if spec is None:
            self.flow_auditor.record_kelly_rejected()
            return

        # 9. Spread check — use abs() as safety net for any orderbook side-swap edge cases
        spread_bps = book.spread_bps
        if abs(spread_bps) > self.config.max_spread_bps or spread_bps < 0:
            self.flow_auditor.record_spread_wide()
            return

        # 10. Open position
        await self._open_position(st, symbol, spec, signal, mid, kz_name=kz_name)

    # ------------------------------------------------------------------
    # Trade callback
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
        symbol: str,
        spec: PositionSpec,
        signal: SignalResult,
        mid: float,
        kz_name: str = "",
    ) -> None:
        pos = OpenPosition(
            spec=spec,
            chandelier=ChandelierExit(
                initial_multiplier=3.0,
                step=0.5,
                floor_multiplier=1.5,
                ratchet_threshold_r=0.75,
            ),
            highest_price=spec.entry_price,
            lowest_price=spec.entry_price,
        )
        pos.chandelier.update_bar(
            high=spec.entry_price,
            low=spec.entry_price,
            close=spec.entry_price,
        )
        pos.chandelier.init_position(spec.side, spec.entry_price)
        st.position = pos

        mode = "[DRY RUN]" if self.config.dry_run else "[LIVE]"
        logger.info(
            "%s OPEN %s %s | entry=%.2f liq=%.2f stop=%.2f "
            "margin=%.2f USDT lev=%dx OFI_mean=%.3f conf=%.2f kz=%s",
            mode, spec.side.upper(), symbol,
            spec.entry_price, spec.liq_price, spec.stop_price,
            spec.margin_usdt, spec.leverage,
            float(signal.ofi_vector.mean()), signal.confidence, kz_name,
        )
        self._trade_count += 1
        self._day_trades += 1
        self.day_planner.record_session_trade(kz_name)
        self.flow_auditor.record_trade_executed()
        await self.monitor.notify_entry(
            symbol=symbol,
            side=spec.side,
            entry_price=spec.entry_price,
            qty=spec.qty_base,
            margin=spec.margin_usdt,
            leverage=spec.leverage,
            stop_price=spec.stop_price,
            confidence=signal.confidence,
            hawkes_buy=st.hawkes.buy_intensity,
            hawkes_sell=st.hawkes.sell_intensity,
            ofi=float(signal.ofi_vector.mean()),
            killzone=kz_name,
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
            new_mult = pos.chandelier.current_multiplier
            stop_px = pos.chandelier.evaluate(price).stop_price
            await self.monitor.log_ratchet_shift(old_mult, new_mult, pos.current_pnl_pct(price), new_stop=stop_px)
        pos.update_peak(price)
        exit_state: ExitState = pos.chandelier.evaluate(price)

        pnl_pct = pos.current_pnl_pct(price)

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
                "%s CLOSE %s %s | exit=%.2f pnl=%.2f%% pnl_usdt=%.2f "
                "stop=%.2f mult=%.1f ATR=%.2f reason=%s",
                mode, pos.spec.side.upper(), pos.spec.symbol,
                price, pnl_pct * 100, pnl_usdt,
                exit_state.stop_price, exit_state.multiplier,
                exit_state.atr, reason,
            )
            st.position = None
            st.as_model.reset_epoch()
            self.day_planner.record_trade_result(pnl_usdt)
            await self.monitor.log_exit(pos.spec.side, price, pnl_usdt, pnl_pct, reason, balance=self._balance)

            # --- Daily circuit breakers (dynamic thresholds, compound-aware) ---
            if self._balance >= self._day_target_usdt:
                self._day_halted = True
                await self.monitor.notify_daily_target_hit(self._balance)
                # Keep engine RUNNING so telemetry/guard/ntfy stay alive
                # New entries blocked via _day_halted gate above
                logger.info(
                    "DAILY TARGET HIT: $%.2f >= $%.2f — halting new entries until 00:00 UTC",
                    self._balance, self._day_target_usdt,
                )
            elif self._balance <= self._day_loss_floor:
                self._day_halted = True
                await self.monitor.notify_drawdown_halt(self._balance)
                logger.info(
                    "DAILY LOSS LIMIT HIT: $%.2f <= $%.2f — halting new entries until 00:00 UTC",
                    self._balance, self._day_loss_floor,
                )


    def stats(self) -> dict:
        return {
            "balance_usdt": self._balance,
            "total_pnl_usdt": self._pnl_total,
            "trade_count": self._trade_count,
            "open_positions": {s: bool(st.position) for s, st in self._states.items()},
        }

EngineConfig = HFTEngineConfig