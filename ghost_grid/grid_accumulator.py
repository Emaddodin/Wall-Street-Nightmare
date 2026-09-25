"""
The Grid Accumulator for Ghost Grid.
Manages the deployment and tracking of staggered position grids.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum
import time

from .noise_engine import NoiseEngine, NoiseDecision

class GridState(Enum):
    PLANNING = "PLANNING"
    DEPLOYING = "DEPLOYING"
    ACTIVE = "ACTIVE"
    SCALING_OUT = "SCALING_OUT"
    CLOSED = "CLOSED"

class OrderStatus(Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CLOSED = "CLOSED"

class Direction(Enum):
    BUY = "BUY"
    SELL = "SELL"

class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"

@dataclass
class GridConfig:
    """Configuration for grid deployment."""
    num_orders: int = 5
    base_lot_size: float = 0.05
    tp1_points: float = 1.50
    scale_out_pct: float = 0.60
    hard_stop_loss_usd: float = 15.0
    symbol: str = "XAUUSD"

@dataclass
class OrderRequest:
    """A broker-agnostic request to place an order."""
    direction: Direction
    lot_size: float
    target_price: float
    order_type: OrderType = OrderType.MARKET
    delay_ms: float = 0.0

@dataclass
class GridOrder:
    """Represents an individual order within a grid."""
    internal_id: str
    direction: Direction
    lot_size: float
    target_entry_price: float
    order_type: OrderType
    
    status: OrderStatus = OrderStatus.PENDING
    broker_order_id: Optional[str] = None
    filled_price: Optional[float] = None
    current_price: Optional[float] = None
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    fill_time: Optional[float] = None
    close_time: Optional[float] = None
    
    def update_pnl(self, current_price: float):
        """Update unrealized PNL based on current price."""
        if self.status != OrderStatus.FILLED or self.filled_price is None:
            return
            
        self.current_price = current_price
        multiplier = 1.0 if self.direction == Direction.BUY else -1.0
        # Simplistic PnL calculation for XAUUSD (1 lot = 100 oz usually, ignoring contract size multiplier for simple demo)
        points = (current_price - self.filled_price) * multiplier
        self.unrealized_pnl = points * self.lot_size * 100.0  # Assuming 100 contract size

@dataclass
class GridBatch:
    """A collection of GridOrders forming one grid deployment."""
    batch_id: str
    orders: List[GridOrder]
    config: GridConfig
    state: GridState = GridState.PLANNING
    
    def total_lots(self) -> float:
        return sum(o.lot_size for o in self.orders if o.status in (OrderStatus.FILLED, OrderStatus.CLOSED))
        
    def total_unrealized_pnl(self) -> float:
        return sum(o.unrealized_pnl for o in self.orders)
        
    def total_realized_pnl(self) -> float:
        return sum(o.realized_pnl for o in self.orders)

class GridAccumulator:
    """Manages grid lifecycles."""
    
    def __init__(self, noise_engine: NoiseEngine):
        self.noise_engine = noise_engine
        self.active_grids: Dict[str, GridBatch] = {}
        self._batch_counter = 0
        
    def plan_grid(self, target_price: float, direction: Direction, config: GridConfig, is_marginal: bool = False) -> Optional[List[OrderRequest]]:
        """Plan a grid with noise injected. Returns None if setup skipped by noise engine."""
        noise_plan = self.noise_engine.generate_grid_noise_plan(config.base_lot_size, config.num_orders, is_marginal)
        
        if noise_plan.skip_setup:
            return None
            
        requests = []
        
        # Test order
        if noise_plan.test_order_first:
            requests.append(OrderRequest(
                direction=direction,
                lot_size=0.01,
                target_price=target_price,
                order_type=OrderType.MARKET,
                delay_ms=0.0
            ))
            
        for i in range(config.num_orders):
            price_offset = noise_plan.price_offsets[i]
            if direction == Direction.BUY:
                price = target_price + price_offset
            else:
                price = target_price - price_offset
                
            lot = noise_plan.lot_adjustments[i]
            delay = noise_plan.delays_ms[i]
            
            # Mix in limit orders occasionally based on noise config
            order_type = OrderType.MARKET
            if self.noise_engine.rng.random() < self.noise_engine.config.limit_order_prob:
                order_type = OrderType.LIMIT
                
            requests.append(OrderRequest(
                direction=direction,
                lot_size=lot,
                target_price=price,
                order_type=order_type,
                delay_ms=delay
            ))
            
        return requests
        
    def register_grid_deployment(self, batch_id: str, requests: List[OrderRequest], config: GridConfig) -> GridBatch:
        """Register a planned grid into the state machine."""
        orders = []
        for i, req in enumerate(requests):
            order = GridOrder(
                internal_id=f"{batch_id}_{i}",
                direction=req.direction,
                lot_size=req.lot_size,
                target_entry_price=req.target_price,
                order_type=req.order_type
            )
            orders.append(order)
            
        batch = GridBatch(batch_id=batch_id, orders=orders, config=config, state=GridState.DEPLOYING)
        self.active_grids[batch_id] = batch
        return batch
        
    def update_grid_price(self, batch_id: str, current_price: float):
        """Update PNL for a grid based on tick data."""
        if batch_id not in self.active_grids:
            return
            
        batch = self.active_grids[batch_id]
        for order in batch.orders:
            order.update_pnl(current_price)
            
        # Check Hard Stop Loss
        if batch.total_unrealized_pnl() <= -batch.config.hard_stop_loss_usd:
            self._trigger_hard_stop(batch)
            
        # Check TP1 Scale-Out (simplified: uses first order's filled price as proxy for 'entry' or weighted avg)
        # In full implementation, calculate weighted avg entry and check TP1 offset.
        
    def _trigger_hard_stop(self, batch: GridBatch):
        """Logic to close all positions in a grid."""
        batch.state = GridState.CLOSED
        # Broker execution would be called here via gateway callbacks

