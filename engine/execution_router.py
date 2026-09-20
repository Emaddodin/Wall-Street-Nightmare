"""
engine/execution_router.py
==========================
Order Slicing & Risk Invariants (Hyperliquid CLOB Architecture).

Hyperliquid Architecture Constraints & Invariants:
1. Leverage & Margin Ceiling:
   Enforces 100x leverage (Hyperliquid maximum for GOLD perpetuals) with initial
   margin capped strictly at <= 20% of account equity ($13.00 max on a $65 account).
2. SL Envelope (Absolute Delta):
   Strictly constrained to an absolute price difference of $1.00 to $1.50 from the
   entry price. The trigger price is placed $0.10 to $0.15 beyond the invalidation
   wick. Setups requiring an SL delta > $1.50 are systematically rejected.
3. No Static TP:
   Orders are dispatched entirely open-ended. No resting Take-Profit orders are
   placed on the central limit order book (CLOB).
4. Order Slicing (Momentum Layering):
   fire_layered_orders() concurrently dispatches micro-units (e.g., three sz = 1.0
   slices) using exchange.market_open(coin="GOLD") with a 50ms stagger jitter via
   asyncio.gather to optimize execution fill.
5. Detached Stop Mechanism:
   Immediately following the entry slices, a unified Stop Market order is dispatched
   via exchange.market_close() for the aggregate size. This order MUST include the
   reduce_only=True flag to prevent reverse positioning.
6. Breakeven Lock:
   At +1.5R floating profit, the existing structural Stop Market order is programmatically
   cancelled, and a new reduce_only=True Stop order is transmitted at Entry Price +/- $0.10.
7. Dynamic Basket Close:
   Instant liquidation of the entire aggregate position via
   exchange.market_close(sz=total_sz, reduce_only=True) upon receiving the EXIT flag
   from the local LLM intuition hook.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable

try:
    from hyperliquid.exchange import Exchange
    from hyperliquid.info import Info
    from hyperliquid.utils import constants as hl_constants
    _HYPERLIQUID_SDK_AVAILABLE = True
except ImportError:
    Exchange = None
    Info = None
    hl_constants = None
    _HYPERLIQUID_SDK_AVAILABLE = False

try:
    from eth_account import Account as EthAccount
except ImportError:
    EthAccount = None

logger = logging.getLogger("execution_router")


# -------------------------------------------------------------------------
# Telemetry Formatting for Antigravity Agent Runtime
# -------------------------------------------------------------------------

def emit_telemetry(
    component: str,
    event: str,
    data: Dict[str, Any],
    level: str = "INFO",
) -> None:
    """Emit Antigravity structured JSON telemetry to stdout/logs."""
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": "Antigravity-RelapseScalper",
        "component": component,
        "event": event,
        "level": level,
        "data": data,
    }
    log_line = json.dumps(payload, separators=(",", ":"))
    if level in ("ERROR", "CRITICAL"):
        logger.error(log_line)
    elif level == "WARNING":
        logger.warning(log_line)
    else:
        logger.info(log_line)


# -------------------------------------------------------------------------
# Domain Models & Hyperliquid CLOB Invariants
# -------------------------------------------------------------------------

class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class RiskInvariants:
    """Core Hyperliquid CLOB risk and account sizing invariants."""
    coin: str = "GOLD"                      # Hyperliquid GOLD perpetual contract
    leverage: float = 100.0                 # 100x leverage (Hyperliquid max for GOLD)
    max_margin_pct: float = 0.20            # 20% max initial margin utilization ($13 on $65)
    min_sl_delta: float = 1.00              # Absolute $1.00 minimum SL delta
    max_sl_delta: float = 1.50              # Absolute $1.50 maximum SL delta
    wick_buffer: float = 0.12               # $0.10 - $0.15 beyond invalidation wick
    breakeven_trigger_r: float = 1.5        # +1.5R triggers breakeven lock
    breakeven_lock_offset: float = 0.10     # Entry Price +/- $0.10
    initial_account_equity: float = 65.0    # Base micro account size ($65 -> $10k goal)
    slice_count: int = 3                    # 3 layered micro-unit slices
    slice_jitter_ms: int = 50               # 50ms stagger jitter between slices


@dataclass
class OrderSlice:
    """Individual market open order slice on Hyperliquid CLOB."""
    ticket_id: str
    basket_id: str
    coin: str
    is_buy: bool
    sz: float
    entry_price: float
    created_at: float = field(default_factory=time.time)
    is_active: bool = True
    close_price: Optional[float] = None
    realized_pnl: float = 0.0
    tier: int = 1


@dataclass
class OrderBasket:
    """
    Aggregate position basket on Hyperliquid CLOB.
    Manages entry slices, detached stop market order, and breakeven lock.
    """
    basket_id: str
    coin: str
    is_buy: bool
    total_sz: float
    sl_price: float
    invalidation_price: float
    risk_r_dist: float                      # Dollar distance of 1R ($1.00 - $1.50)
    entry_price: float = 0.0
    slices: List[OrderSlice] = field(default_factory=list)
    stop_order_id: Optional[str] = None     # Unified detached Stop Market order ID
    breakeven_locked: bool = False
    is_active: bool = True
    tier: int = 1
    created_at: float = field(default_factory=time.time)

    @property
    def side(self) -> OrderSide:
        return OrderSide.BUY if self.is_buy else OrderSide.SELL

    @property
    def active_slices(self) -> List[OrderSlice]:
        return [s for s in self.slices if s.is_active]

    @property
    def current_sz(self) -> float:
        return sum(s.sz for s in self.active_slices)

    def calculate_unrealized_pnl(self, current_price: float) -> Tuple[float, float]:
        """
        Calculate total dollar unrealized PnL and R-multiple on Hyperliquid.
        Returns: (dollar_pnl, unrealized_r)
        """
        if not self.active_slices or self.entry_price <= 0:
            return 0.0, 0.0

        if self.is_buy:
            price_diff = current_price - self.entry_price
        else:
            price_diff = self.entry_price - current_price

        # Hyperliquid GOLD perps: PnL = price_diff * sz
        dollar_pnl = price_diff * self.current_sz
        unrealized_r = price_diff / self.risk_r_dist if self.risk_r_dist > 0 else 0.0
        return dollar_pnl, unrealized_r


# -------------------------------------------------------------------------
# Hyperliquid CLOB Exchange Interface & Simulated Venue
# -------------------------------------------------------------------------

@runtime_checkable
class HyperliquidVenue(Protocol):
    """Protocol matching Hyperliquid native Exchange & Info API."""

    async def get_equity(self) -> float:
        ...

    async def get_market_price(self, coin: str) -> float:
        ...

    async def market_open(
        self,
        coin: str,
        is_buy: bool,
        sz: float,
        px: Optional[float] = None,
        slippage: float = 0.01,
    ) -> Dict[str, Any]:
        """
        Hyperliquid CLOB market open.
        Dispatches open-ended order (NO static Take-Profit).
        """
        ...

    async def market_close(
        self,
        coin: str,
        sz: Optional[float] = None,
        px: Optional[float] = None,
        slippage: float = 0.01,
        trigger_px: Optional[float] = None,
        reduce_only: bool = True,
    ) -> Dict[str, Any]:
        """
        Hyperliquid CLOB market close or detached Stop Market order.
        MUST include reduce_only=True flag.
        """
        ...

    async def cancel(self, coin: str, oid: str) -> bool:
        """Cancel resting trigger/stop order."""
        ...


# Type alias for backwards compatibility
BrokerVenue = HyperliquidVenue


class SimulatedBrokerVenue:
    """
    High-fidelity simulated Hyperliquid venue for paper testing,
    backtesting, and micro-account scaling verification.
    """

    def __init__(
        self,
        initial_equity: float = 65.0,
        slippage_delta: float = 0.02,
    ) -> None:
        self.equity: float = initial_equity
        self.slippage_delta: float = slippage_delta
        self._current_prices: Dict[str, float] = {"GOLD": 2500.00, "XAUUSD": 2500.00}
        self._orders: Dict[str, Dict[str, Any]] = {}
        self._resting_stops: Dict[str, Dict[str, Any]] = {}

    def set_market_price(self, coin: str, price: float) -> None:
        self._current_prices[coin] = round(price, 2)

    async def get_equity(self) -> float:
        return self.equity

    async def get_market_price(self, coin: str) -> float:
        return self._current_prices.get(coin, 2500.00)

    async def market_open(
        self,
        coin: str,
        is_buy: bool,
        sz: float,
        px: Optional[float] = None,
        slippage: float = 0.01,
    ) -> Dict[str, Any]:
        base_price = px or await self.get_market_price(coin)
        slip = self.slippage_delta if is_buy else -self.slippage_delta
        fill_price = round(base_price + slip, 2)

        order_id = f"HL-OPEN-{uuid.uuid4().hex[:8].upper()}"
        res = {
            "status": "ok",
            "oid": order_id,
            "coin": coin,
            "is_buy": is_buy,
            "sz": sz,
            "fill_price": fill_price,
            "take_profit": None,  # Invariant: open-ended
        }
        self._orders[order_id] = res
        return res

    async def market_close(
        self,
        coin: str,
        sz: Optional[float] = None,
        px: Optional[float] = None,
        slippage: float = 0.01,
        trigger_px: Optional[float] = None,
        reduce_only: bool = True,
    ) -> Dict[str, Any]:
        order_id = f"HL-CLOSE-{uuid.uuid4().hex[:8].upper()}"

        if trigger_px is not None:
            # Detached Stop Market order resting on CLOB
            stop_record = {
                "oid": order_id,
                "coin": coin,
                "sz": sz,
                "trigger_px": round(trigger_px, 2),
                "reduce_only": reduce_only,
                "status": "resting",
            }
            self._resting_stops[order_id] = stop_record
            return {"status": "ok", "oid": order_id, "type": "stop_market", "reduce_only": reduce_only}

        # Immediate market liquidation
        current_price = px or await self.get_market_price(coin)
        res = {
            "status": "ok",
            "oid": order_id,
            "coin": coin,
            "sz": sz,
            "fill_price": current_price,
            "reduce_only": reduce_only,
        }
        if sz is None:
            self._orders.clear()
        else:
            remaining = sz
            for o_id in list(self._orders.keys()):
                ord_sz = self._orders[o_id].get("sz", 0.0)
                if ord_sz <= remaining + 1e-6:
                    remaining -= ord_sz
                    self._orders.pop(o_id, None)
                else:
                    self._orders[o_id]["sz"] = round(ord_sz - remaining, 4)
                    remaining = 0.0
                    break
        return res

    async def cancel(self, coin: str, oid: str) -> bool:
        if oid in self._resting_stops:
            self._resting_stops[oid]["status"] = "cancelled"
            return True
        return False


class HyperliquidDEXVenue:
    """
    Production & Testnet Hyperliquid DEX Execution Venue.
    Conforms to the HyperliquidVenue protocol. Wraps hyperliquid-python-sdk
    (hyperliquid.exchange.Exchange and hyperliquid.info.Info).

    Synchronous SDK network calls are executed in worker threads via asyncio.to_thread()
    to guarantee zero blocking of the asynchronous event loop.
    Supports testnet (hl_constants.TESTNET_API_URL) and mainnet (hl_constants.MAINNET_API_URL).
    """

    def __init__(
        self,
        secret_key: Optional[str] = None,
        account_address: Optional[str] = None,
        testnet: bool = True,
        base_url: Optional[str] = None,
        exchange: Optional[Any] = None,
        info: Optional[Any] = None,
        default_equity: float = 65.0,
    ) -> None:
        self.testnet = testnet
        self.default_equity = default_equity

        # Base API URL
        if base_url:
            self.base_url = base_url
        elif hl_constants is not None:
            self.base_url = (
                hl_constants.TESTNET_API_URL if testnet else hl_constants.MAINNET_API_URL
            )
        else:
            self.base_url = (
                "https://api.hyperliquid-testnet.xyz"
                if testnet
                else "https://api.hyperliquid.xyz"
            )

        # Credentials
        self.secret_key = (
            secret_key
            or os.getenv("HYPERLIQUID_SECRET_KEY")
            or os.getenv("HL_SECRET_KEY")
        )
        self.account_address = (
            account_address
            or os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS")
            or os.getenv("HL_ACCOUNT_ADDRESS")
        )

        # Info client
        if info is not None:
            self.info = info
        elif _HYPERLIQUID_SDK_AVAILABLE and Info is not None:
            try:
                self.info = Info(self.base_url, skip_ws=True)
            except Exception as exc:
                logger.warning("Failed to initialize Hyperliquid Info client: %s", exc)
                self.info = None
        else:
            self.info = None

        # Exchange client
        if exchange is not None:
            self.exchange = exchange
            if self.account_address is None and hasattr(exchange, "account_address"):
                self.account_address = exchange.account_address
        elif (
            self.secret_key
            and _HYPERLIQUID_SDK_AVAILABLE
            and Exchange is not None
            and EthAccount is not None
        ):
            try:
                self.wallet = EthAccount.from_key(self.secret_key)
                if not self.account_address:
                    self.account_address = self.wallet.address
                self.exchange = Exchange(
                    self.wallet,
                    self.base_url,
                    account_address=self.account_address,
                )
            except Exception as exc:
                logger.error("Failed to initialize Hyperliquid Exchange client: %s", exc)
                self.exchange = None
                self.wallet = None
        else:
            self.exchange = None
            self.wallet = None
            if not self.secret_key:
                logger.info(
                    "[HyperliquidDEXVenue] Initialized in read-only / unauthenticated mode "
                    "(no secret_key). Testnet=%s, URL=%s",
                    self.testnet,
                    self.base_url,
                )

    # ---------------------------------------------------------------------
    # Asynchronous Interface (HyperliquidVenue Protocol)
    # ---------------------------------------------------------------------

    async def get_equity(self) -> float:
        """Fetch account equity asynchronously via asyncio.to_thread."""
        return await asyncio.to_thread(self._sync_get_equity)

    async def get_market_price(self, coin: str = "GOLD") -> float:
        """Fetch current mid price asynchronously via asyncio.to_thread."""
        return await asyncio.to_thread(self._sync_get_market_price, coin)

    async def market_open(
        self,
        coin: str,
        is_buy: bool,
        sz: float,
        px: Optional[float] = None,
        slippage: float = 0.01,
    ) -> Dict[str, Any]:
        """Dispatch open-ended market order asynchronously via asyncio.to_thread."""
        return await asyncio.to_thread(self._sync_market_open, coin, is_buy, sz, px, slippage)

    async def market_close(
        self,
        coin: str,
        sz: Optional[float] = None,
        px: Optional[float] = None,
        slippage: float = 0.01,
        trigger_px: Optional[float] = None,
        reduce_only: bool = True,
        is_buy: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Dispatch market close or detached stop order asynchronously via asyncio.to_thread."""
        return await asyncio.to_thread(
            self._sync_market_close,
            coin,
            sz,
            px,
            slippage,
            trigger_px,
            reduce_only,
            is_buy,
        )

    async def cancel(self, coin: str, oid: str) -> bool:
        """Cancel resting order asynchronously via asyncio.to_thread."""
        return await asyncio.to_thread(self._sync_cancel, coin, oid)

    # ---------------------------------------------------------------------
    # Synchronous SDK Worker Thread Handlers
    # ---------------------------------------------------------------------

    def _sync_get_equity(self) -> float:
        if self.info and self.account_address:
            try:
                state = self.info.user_state(self.account_address)
                if isinstance(state, dict):
                    margin_summary = state.get("marginSummary") or state.get("crossMarginSummary")
                    if isinstance(margin_summary, dict) and "accountValue" in margin_summary:
                        val = float(margin_summary["accountValue"])
                        if val > 0:
                            return val
                    if "withdrawable" in state:
                        val = float(state["withdrawable"])
                        if val > 0:
                            return val
            except Exception as exc:
                logger.warning("Error fetching equity from Hyperliquid Info: %s", exc)
        return self.default_equity

    def _sync_get_market_price(self, coin: str) -> float:
        if self.info:
            try:
                mids = self.info.all_mids()
                if isinstance(mids, dict) and coin in mids:
                    return float(mids[coin])
            except Exception as exc:
                logger.warning("Error fetching mid price for %s: %s", coin, exc)
        return 2500.00

    def _sync_market_open(
        self,
        coin: str,
        is_buy: bool,
        sz: float,
        px: Optional[float] = None,
        slippage: float = 0.01,
    ) -> Dict[str, Any]:
        if self.exchange is None:
            raise RuntimeError(
                "Hyperliquid Exchange client not initialized. Provide secret_key or an exchange instance."
            )
        try:
            sdk_res = self.exchange.market_open(coin, is_buy, sz, px=px, slippage=slippage)
        except Exception as exc:
            logger.error("Hyperliquid market_open call failed for %s: %s", coin, exc)
            raise

        status = "ok"
        oid = None
        fill_price = px

        if isinstance(sdk_res, dict):
            status = sdk_res.get("status", "ok")
            resp = sdk_res.get("response", {})
            if isinstance(resp, dict):
                data = resp.get("data", {})
                statuses = data.get("statuses", []) if isinstance(data, dict) else []
                if statuses and isinstance(statuses, list):
                    first = statuses[0]
                    if isinstance(first, dict):
                        if "filled" in first:
                            oid = str(first["filled"].get("oid"))
                            avg_px = first["filled"].get("avgPx")
                            if avg_px is not None:
                                fill_price = float(avg_px)
                        elif "resting" in first:
                            oid = str(first["resting"].get("oid"))
                        elif "error" in first:
                            status = "err"

        if not oid:
            oid = f"HL-OPEN-{uuid.uuid4().hex[:8].upper()}"
        if fill_price is None:
            fill_price = self._sync_get_market_price(coin)

        return {
            "status": status,
            "oid": oid,
            "coin": coin,
            "is_buy": is_buy,
            "sz": sz,
            "fill_price": fill_price,
            "take_profit": None,  # Invariant: open-ended
            "raw": sdk_res,
        }

    def _sync_market_close(
        self,
        coin: str,
        sz: Optional[float] = None,
        px: Optional[float] = None,
        slippage: float = 0.01,
        trigger_px: Optional[float] = None,
        reduce_only: bool = True,
        is_buy: Optional[bool] = None,
    ) -> Dict[str, Any]:
        if self.exchange is None:
            raise RuntimeError(
                "Hyperliquid Exchange client not initialized. Provide secret_key or an exchange instance."
            )

        if trigger_px is not None:
            # Detached Stop Market order on Hyperliquid CLOB
            if is_buy is None:
                cur_px = self._sync_get_market_price(coin)
                try:
                    if self.account_address and self.info:
                        state = self.info.user_state(self.account_address)
                        for p in state.get("assetPositions", []):
                            pos = p.get("position", {})
                            if pos.get("coin") == coin:
                                szi = float(pos.get("szi", 0.0))
                                if szi != 0.0:
                                    is_buy = (szi < 0)
                                    break
                        else:
                            is_buy = (trigger_px > cur_px)
                    else:
                        is_buy = (trigger_px > cur_px)
                except Exception:
                    is_buy = (trigger_px > cur_px)

            order_type = {
                "trigger": {
                    "triggerPx": float(trigger_px),
                    "isMarket": True,
                    "tpsl": "sl",
                }
            }
            try:
                sdk_res = self.exchange.order(
                    name=coin,
                    is_buy=is_buy,
                    sz=sz if sz is not None else 1.0,
                    limit_px=float(trigger_px),
                    order_type=order_type,
                    reduce_only=reduce_only,
                )
            except Exception as exc:
                logger.error("Hyperliquid trigger order call failed for %s: %s", coin, exc)
                raise

            status = "ok"
            oid = None
            if isinstance(sdk_res, dict):
                status = sdk_res.get("status", "ok")
                resp = sdk_res.get("response", {})
                if isinstance(resp, dict):
                    data = resp.get("data", {})
                    statuses = data.get("statuses", []) if isinstance(data, dict) else []
                    if statuses and isinstance(statuses, list):
                        first = statuses[0]
                        if isinstance(first, dict):
                            if "resting" in first:
                                oid = str(first["resting"].get("oid"))
                            elif "filled" in first:
                                oid = str(first["filled"].get("oid"))
                            elif "error" in first:
                                status = "err"

            if not oid:
                oid = f"HL-CLOSE-{uuid.uuid4().hex[:8].upper()}"

            return {
                "status": status,
                "oid": oid,
                "coin": coin,
                "sz": sz,
                "trigger_px": trigger_px,
                "reduce_only": reduce_only,
                "type": "stop_market",
                "raw": sdk_res,
            }

        else:
            # Immediate Market Close
            try:
                sdk_res = self.exchange.market_close(
                    coin=coin,
                    sz=sz,
                    px=px,
                    slippage=slippage,
                )
            except Exception as exc:
                logger.error("Hyperliquid market_close call failed for %s: %s", coin, exc)
                raise

            status = "ok"
            oid = None
            fill_price = px

            if isinstance(sdk_res, dict):
                status = sdk_res.get("status", "ok")
                resp = sdk_res.get("response", {})
                if isinstance(resp, dict):
                    data = resp.get("data", {})
                    statuses = data.get("statuses", []) if isinstance(data, dict) else []
                    if statuses and isinstance(statuses, list):
                        first = statuses[0]
                        if isinstance(first, dict):
                            if "filled" in first:
                                oid = str(first["filled"].get("oid"))
                                avg_px = first["filled"].get("avgPx")
                                if avg_px is not None:
                                    fill_price = float(avg_px)
                            elif "resting" in first:
                                oid = str(first["resting"].get("oid"))
                            elif "error" in first:
                                status = "err"

            if not oid:
                oid = f"HL-CLOSE-{uuid.uuid4().hex[:8].upper()}"
            if fill_price is None:
                fill_price = self._sync_get_market_price(coin)

            return {
                "status": status,
                "oid": oid,
                "coin": coin,
                "sz": sz,
                "fill_price": fill_price,
                "reduce_only": reduce_only,
                "raw": sdk_res,
            }

    def _sync_cancel(self, coin: str, oid: str) -> bool:
        if self.exchange is None:
            return False
        try:
            try:
                oid_val = int(oid)
                res = self.exchange.cancel(coin, oid_val)
            except (ValueError, TypeError):
                if hasattr(self.exchange, "cancel_by_cloid"):
                    res = self.exchange.cancel_by_cloid(coin, oid)
                else:
                    res = self.exchange.cancel(coin, oid)

            if isinstance(res, dict):
                if res.get("status") == "ok":
                    return True
                statuses = res.get("response", {}).get("data", {}).get("statuses", [])
                if statuses and statuses[0] == "success":
                    return True
            return True
        except Exception as exc:
            logger.warning("Failed to cancel order %s on %s: %s", oid, coin, exc)
            return False


# -------------------------------------------------------------------------
# Hyperliquid Execution Router
# -------------------------------------------------------------------------

class ExecutionRouter:
    """
    Coordinates Hyperliquid CLOB execution:
    - 100x leverage & <= 20% margin ceiling enforcement.
    - Strict $1.00 - $1.50 SL envelope (absolute delta).
    - Open-ended order slicing via exchange.market_open() with 50ms jitter.
    - Detached reduce_only=True Stop Market order placement.
    - Breakeven lock at +1.5R (cancellation + re-transmission at Entry +/- $0.10).
    - Dynamic basket liquidation via exchange.market_close(reduce_only=True) on EXIT.
    """

    def __init__(
        self,
        venue: HyperliquidVenue,
        risk: Optional[RiskInvariants] = None,
    ) -> None:
        self.venue = venue
        self.risk = risk or RiskInvariants()
        self.baskets: Dict[str, OrderBasket] = {}
        self.active_basket_id: Optional[str] = None
        self._lock = asyncio.Lock()

    @property
    def active_basket(self) -> Optional[OrderBasket]:
        return self.baskets.get(self.active_basket_id) if self.active_basket_id else None

    # ---------------------------------------------------------------------
    # Margin & Risk Invariant Checks
    # ---------------------------------------------------------------------

    def calculate_margin_required(self, sz: float, price: float) -> float:
        """
        Calculate initial margin requirement at 100x leverage on Hyperliquid.
        Margin = (sz * price) / 100.0
        """
        return (sz * price) / self.risk.leverage

    async def validate_margin(self, sz: float, price: float) -> Tuple[bool, float, float]:
        """
        Validates that total initial margin <= 20% of account equity ($13 max on $65).
        Returns: (is_valid, required_margin, max_allowed_margin)
        """
        equity = await self.venue.get_equity()
        required_margin = self.calculate_margin_required(sz, price)
        max_allowed_margin = equity * self.risk.max_margin_pct

        is_valid = required_margin <= max_allowed_margin
        if not is_valid:
            emit_telemetry(
                component="ExecutionRouter",
                event="MARGIN_LIMIT_EXCEEDED",
                data={
                    "equity": equity,
                    "required_margin": round(required_margin, 4),
                    "max_allowed_margin": round(max_allowed_margin, 4),
                    "sz": sz,
                    "price": price,
                    "leverage": self.risk.leverage,
                },
                level="WARNING",
            )
        return is_valid, required_margin, max_allowed_margin

    def calculate_and_validate_sl(
        self,
        is_buy: bool,
        entry_price: float,
        invalidation_wick_price: float,
    ) -> Tuple[bool, float, float, str]:
        """
        SL Envelope (Absolute Delta):
        Strictly constrained to an absolute price difference of $1.00 to $1.50 from
        the entry price. Trigger price placed $0.10 to $0.15 beyond the invalidation wick.
        Setups requiring an SL delta > $1.50 are systematically rejected.

        Returns: (is_valid, sl_price, sl_delta, reason)
        """
        buffer = self.risk.wick_buffer  # $0.10 - $0.15

        if is_buy:
            raw_sl = invalidation_wick_price - buffer
            sl_delta = round(entry_price - raw_sl, 2)

            if sl_delta > self.risk.max_sl_delta:
                reason = (
                    f"Stop-Loss delta ${sl_delta:.2f} exceeds maximum allowed ${self.risk.max_sl_delta:.2f}"
                )
                return False, 0.0, sl_delta, reason

            # If tighter than $1.00, clamp to minimum $1.00 delta
            if sl_delta < self.risk.min_sl_delta:
                sl_delta = self.risk.min_sl_delta
                sl_price = round(entry_price - self.risk.min_sl_delta, 2)
            else:
                sl_price = round(raw_sl, 2)

            return True, sl_price, sl_delta, "OK"

        else:  # Short / Sell
            raw_sl = invalidation_wick_price + buffer
            sl_delta = round(raw_sl - entry_price, 2)

            if sl_delta > self.risk.max_sl_delta:
                reason = (
                    f"Stop-Loss delta ${sl_delta:.2f} exceeds maximum allowed ${self.risk.max_sl_delta:.2f}"
                )
                return False, 0.0, sl_delta, reason

            # If tighter than $1.00, clamp to minimum $1.00 delta
            if sl_delta < self.risk.min_sl_delta:
                sl_delta = self.risk.min_sl_delta
                sl_price = round(entry_price + self.risk.min_sl_delta, 2)
            else:
                sl_price = round(raw_sl, 2)

            return True, sl_price, sl_delta, "OK"

    # ---------------------------------------------------------------------
    # Asynchronous Order Slicing & Detached Stop Placement
    # ---------------------------------------------------------------------

    async def fire_layered_orders(
        self,
        symbol: Optional[str] = None,
        side: Optional[OrderSide] = None,
        invalidation_wick_price: float = 0.0,
        total_lots: Optional[float] = None,
        total_sz: float = 3.0,
        num_slices: int = 3,
        tier: int = 1,
        is_buy: Optional[bool] = None,
    ) -> Optional[OrderBasket]:
        """
        Hyperliquid CLOB Order Slicing:
        1. Concurrently dispatches micro-units (e.g. three sz = 1.0 slices) using
           exchange.market_open(coin="GOLD") with 50ms stagger jitter via asyncio.gather.
        2. Dispatches entirely open-ended (NO resting Take-Profit on CLOB).
        3. Immediately following entry slices, dispatches a unified Stop Market order
           via exchange.market_close() for aggregate size with reduce_only=True.
        """
        coin = symbol or self.risk.coin
        if is_buy is None:
            is_buy = (side == OrderSide.BUY) if side is not None else True

        # Support both total_sz and total_lots argument for backwards compatibility
        effective_sz = total_sz if total_lots is None else total_lots

        async with self._lock:
            current_price = await self.venue.get_market_price(coin)

            # 1. Validate Margin at 100x Leverage (<= 20% equity)
            margin_ok, req_margin, max_margin = await self.validate_margin(effective_sz, current_price)
            if not margin_ok:
                emit_telemetry(
                    component="ExecutionRouter",
                    event="ORDER_REJECTED_MARGIN",
                    data={
                        "coin": coin,
                        "sz": effective_sz,
                        "req_margin": req_margin,
                        "max_margin": max_margin,
                        "leverage": self.risk.leverage,
                    },
                    level="WARNING",
                )
                return None

            # 2. Validate Stop Loss Envelope ($1.00 to $1.50 absolute delta)
            sl_ok, sl_price, sl_delta, sl_reason = self.calculate_and_validate_sl(
                is_buy, current_price, invalidation_wick_price
            )
            if not sl_ok:
                emit_telemetry(
                    component="ExecutionRouter",
                    event="ORDER_REJECTED_SL_ENVELOPE",
                    data={
                        "coin": coin,
                        "is_buy": is_buy,
                        "entry": current_price,
                        "wick": invalidation_wick_price,
                        "sl_delta": sl_delta,
                        "reason": sl_reason,
                    },
                    level="WARNING",
                )
                return None

            basket_id = f"HL-BSK-{uuid.uuid4().hex[:8].upper()}"
            slice_sz = round(effective_sz / num_slices, 2)

            basket = OrderBasket(
                basket_id=basket_id,
                coin=coin,
                is_buy=is_buy,
                total_sz=effective_sz,
                sl_price=sl_price,
                invalidation_price=invalidation_wick_price,
                risk_r_dist=sl_delta,
                entry_price=current_price,
                tier=tier,
            )

            emit_telemetry(
                component="ExecutionRouter",
                event="DISPATCHING_HYPERLIQUID_SLICES",
                data={
                    "basket_id": basket_id,
                    "coin": coin,
                    "is_buy": is_buy,
                    "total_sz": effective_sz,
                    "num_slices": num_slices,
                    "slice_sz": slice_sz,
                    "sl_price": sl_price,
                    "sl_delta": sl_delta,
                    "take_profit": None,  # Open-ended
                    "jitter_ms": self.risk.slice_jitter_ms,
                    "leverage": self.risk.leverage,
                },
            )

            # Concurrent order slicing with 50ms stagger jitter
            async def _dispatch_single_slice(idx: int) -> OrderSlice:
                if idx > 0:
                    delay_s = (idx * self.risk.slice_jitter_ms) / 1000.0
                    await asyncio.sleep(delay_s)
                # Open-ended market order on Hyperliquid CLOB
                res = await self.venue.market_open(
                    coin=coin,
                    is_buy=is_buy,
                    sz=slice_sz,
                )
                fill_px = res.get("fill_price", current_price)
                return OrderSlice(
                    ticket_id=res.get("oid", f"TICK-{uuid.uuid4().hex[:6]}"),
                    basket_id=basket_id,
                    coin=coin,
                    is_buy=is_buy,
                    sz=slice_sz,
                    entry_price=fill_px,
                    tier=tier,
                )

            tasks = [_dispatch_single_slice(i) for i in range(num_slices)]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Check for slice exceptions or failures
            slice_exceptions = [r for r in results if isinstance(r, BaseException) or not isinstance(r, OrderSlice)]
            successful_slices = [r for r in results if isinstance(r, OrderSlice)]

            if slice_exceptions or len(successful_slices) < num_slices:
                filled_sz = round(sum(s.sz for s in successful_slices), 4)
                emit_telemetry(
                    component="ExecutionRouter",
                    event="SLICE_EXECUTION_FAILURE",
                    data={
                        "basket_id": basket_id,
                        "coin": coin,
                        "failed_count": len(slice_exceptions),
                        "successful_count": len(successful_slices),
                        "filled_sz": filled_sz,
                        "errors": [str(e) for e in slice_exceptions],
                    },
                    level="ERROR",
                )
                if filled_sz > 0:
                    emit_telemetry(
                        component="ExecutionRouter",
                        event="EMERGENCY_MARKET_CLOSE_UNHEDGED_SLICES",
                        data={
                            "basket_id": basket_id,
                            "coin": coin,
                            "filled_sz": filled_sz,
                            "reduce_only": True,
                        },
                        level="CRITICAL",
                    )
                    await self.venue.market_close(
                        coin=coin,
                        sz=filled_sz,
                        reduce_only=True,
                    )
                return None

            basket.slices = list(successful_slices)

            # Weighted average entry price
            if basket.slices:
                total_notional = sum(s.entry_price * s.sz for s in basket.slices)
                total_filled_sz = sum(s.sz for s in basket.slices)
                basket.entry_price = round(total_notional / total_filled_sz, 2)

            # 3. Detached Stop Mechanism:
            # Immediately dispatch unified Stop Market order for aggregate size with reduce_only=True
            stop_res = await self.venue.market_close(
                coin=coin,
                sz=basket.total_sz,
                trigger_px=basket.sl_price,
                reduce_only=True,
            )
            basket.stop_order_id = stop_res.get("oid")

            self.baskets[basket_id] = basket
            self.active_basket_id = basket_id

            emit_telemetry(
                component="ExecutionRouter",
                event="DETACHED_STOP_PLACED",
                data={
                    "basket_id": basket_id,
                    "stop_order_id": basket.stop_order_id,
                    "trigger_px": basket.sl_price,
                    "aggregate_sz": basket.total_sz,
                    "reduce_only": True,
                    "avg_entry": basket.entry_price,
                },
            )
            return basket

    # ---------------------------------------------------------------------
    # Breakeven Lock Mechanism
    # ---------------------------------------------------------------------

    async def evaluate_breakeven_lock(self, current_price: float) -> bool:
        """
        Breakeven Lock at +1.5R:
        1. Programmatically cancels existing structural Stop Market order.
        2. Transmits new reduce_only=True Stop order at Entry Price +/- $0.10.
        """
        async with self._lock:
            if not self.active_basket_id:
                return False

            basket = self.baskets.get(self.active_basket_id)
            if not basket or not basket.is_active or basket.breakeven_locked:
                return False

            _, unrealized_r = basket.calculate_unrealized_pnl(current_price)

            if unrealized_r >= self.risk.breakeven_trigger_r:
                # Calculate new breakeven stop price at Entry +/- $0.10
                if basket.is_buy:
                    new_sl = round(basket.entry_price + self.risk.breakeven_lock_offset, 2)
                else:
                    new_sl = round(basket.entry_price - self.risk.breakeven_lock_offset, 2)

                emit_telemetry(
                    component="ExecutionRouter",
                    event="TRIGGERING_BREAKEVEN_LOCK",
                    data={
                        "basket_id": basket.basket_id,
                        "unrealized_r": round(unrealized_r, 2),
                        "old_sl": basket.sl_price,
                        "new_sl": new_sl,
                        "entry_price": basket.entry_price,
                        "old_stop_oid": basket.stop_order_id,
                    },
                )

                # Cancel existing structural stop order
                if basket.stop_order_id:
                    await self.venue.cancel(basket.coin, basket.stop_order_id)

                # Verify basket is still active after awaiting cancel
                if not basket.is_active or self.active_basket_id != basket.basket_id:
                    return False

                # Transmit new reduce_only=True stop order at Breakeven
                new_stop_res = await self.venue.market_close(
                    coin=basket.coin,
                    sz=basket.current_sz,
                    trigger_px=new_sl,
                    reduce_only=True,
                )

                basket.stop_order_id = new_stop_res.get("oid")
                basket.sl_price = new_sl
                basket.breakeven_locked = True

                emit_telemetry(
                    component="ExecutionRouter",
                    event="BREAKEVEN_LOCKED",
                    data={
                        "basket_id": basket.basket_id,
                        "new_stop_oid": basket.stop_order_id,
                        "new_sl": new_sl,
                        "reduce_only": True,
                    },
                )
                return True

            return False

    async def sync_global_trailing_sl(self, new_sl_price: float) -> None:
        """
        Synchronizes trailing stop across aggregate position by cancelling
        and re-placing the detached Stop Market order with reduce_only=True.
        """
        async with self._lock:
            if not self.active_basket_id:
                return

            basket = self.baskets.get(self.active_basket_id)
            if not basket or not basket.is_active:
                return

            if basket.stop_order_id:
                await self.venue.cancel(basket.coin, basket.stop_order_id)

            if not basket.is_active or self.active_basket_id != basket.basket_id:
                return

            new_stop_res = await self.venue.market_close(
                coin=basket.coin,
                sz=basket.current_sz,
                trigger_px=new_sl_price,
                reduce_only=True,
            )
            basket.stop_order_id = new_stop_res.get("oid")
            basket.sl_price = new_sl_price

            emit_telemetry(
                component="ExecutionRouter",
                event="GLOBAL_SL_SYNCED",
                data={
                    "basket_id": basket.basket_id,
                    "new_sl": new_sl_price,
                    "new_stop_oid": basket.stop_order_id,
                },
            )

    # ---------------------------------------------------------------------
    # Dynamic Basket Close (Instant Market Liquidation)
    # ---------------------------------------------------------------------

    async def close_basket(self, reason: str = "EXIT_SIGNAL") -> Optional[Dict[str, Any]]:
        """
        Dynamic Basket Close:
        Instant liquidation of the entire aggregate position via
        exchange.market_close(sz=total_sz, reduce_only=True) upon receiving
        the EXIT flag from the local LLM intuition hook.
        Cancels any resting detached stop order.
        """
        async with self._lock:
            if not self.active_basket_id:
                return None

            basket = self.baskets.get(self.active_basket_id)
            if not basket or not basket.is_active:
                return None

            aggregate_sz = basket.current_sz
            if aggregate_sz <= 0:
                basket.is_active = False
                self.active_basket_id = None
                return None

            emit_telemetry(
                component="ExecutionRouter",
                event="DYNAMIC_BASKET_CLOSE_INITIATED",
                data={
                    "basket_id": basket.basket_id,
                    "coin": basket.coin,
                    "aggregate_sz": aggregate_sz,
                    "reason": reason,
                    "reduce_only": True,
                },
            )

            # 1. Cancel resting detached stop market order
            if basket.stop_order_id:
                await self.venue.cancel(basket.coin, basket.stop_order_id)
                basket.stop_order_id = None

            # 2. Instant liquidation of entire aggregate position via market_close
            close_res = await self.venue.market_close(
                coin=basket.coin,
                sz=aggregate_sz,
                reduce_only=True,
            )

            exit_price = close_res.get("fill_price", await self.venue.get_market_price(basket.coin))

            # Calculate realized PnL
            if basket.is_buy:
                pnl = (exit_price - basket.entry_price) * aggregate_sz
            else:
                pnl = (basket.entry_price - exit_price) * aggregate_sz

            for s in basket.slices:
                s.is_active = False
                s.close_price = exit_price

            basket.is_active = False
            self.active_basket_id = None
            current_equity = await self.venue.get_equity()

            summary = {
                "basket_id": basket.basket_id,
                "coin": basket.coin,
                "aggregate_sz": round(aggregate_sz, 4),
                "exit_price": exit_price,
                "total_pnl": round(pnl, 2),
                "reason": reason,
                "equity_after": round(current_equity, 2),
            }

            emit_telemetry(
                component="ExecutionRouter",
                event="DYNAMIC_BASKET_CLOSED",
                data=summary,
            )
            return summary
