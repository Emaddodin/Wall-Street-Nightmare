"""
Ghost Grid Trading Engine for LiteFinance Demo.
Builds candles, evaluates ApexTrinity, deploys obfuscated grids.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional, Any

from ghost_grid.noise_engine import NoiseEngine, NoiseConfig
from ghost_grid.grid_accumulator import GridAccumulator, GridConfig, GridBatch, Direction
from ghost_grid.compounding_ladder import CompoundingLadder
from ghost_grid.exit_controller import GridExitController, GridExitConfig, GridExitDecision
from ghost_grid.broker_chameleon import BrokerChameleon, ChameleonConfig

from scalper.strategies.apex_trinity import ApexTrinityStrategy

# Optional AI modules
try:
    from scalper.brain.laya_oracle import get_laya_oracle
except ImportError:
    get_laya_oracle = None

try:
    from scalper.brain.politician_brain import get_politician_brain
except ImportError:
    get_politician_brain = None

try:
    from bark_integration import send_alert
except ImportError:
    send_alert = None

def push_ntfy(title: str, message: str, tags: str = "ghost,zap", priority: str = "high") -> None:
    """Dispatches push notification via project NTFY channel."""
    try:
        clean_msg = " · ".join([line.strip() for line in message.strip().splitlines() if line.strip()])
        clean_title = title.strip()
        if send_alert:
            send_alert(title=clean_title, message=clean_msg, priority=priority)
    except Exception as e:
        logger.debug("push_ntfy dispatch failed: %s", e)

logger = logging.getLogger("GhostEngine")

class GhostEngine:
    def __init__(self, gateway):
        self.gateway = gateway
        
        self.noise_engine = NoiseEngine(NoiseConfig())
        self.grid_accumulator = GridAccumulator(GridConfig(
            num_orders=3, 
            base_lot_size=0.01, 
            tp1_points=15.0, 
            scale_out_pct=0.5, 
            hard_stop_loss_usd=15.0
        ))
        self.compounding = CompoundingLadder(withdrawal_threshold=50.0, max_loss_floor=15.0)
        self.exit_controller = GridExitController(GridExitConfig())
        self.chameleon = BrokerChameleon(ChameleonConfig())
        
        self.strategy = ApexTrinityStrategy(min_candles_warmup=30)
        
        # AI modules
        self.laya_oracle = get_laya_oracle() if get_laya_oracle else None
        self.politician_brain = get_politician_brain() if get_politician_brain else None
        
        # Market Data State
        self.candles_1m: List[Dict] = []
        self.current_1m_bar: Optional[Dict] = None
        
        # Position State
        self.active_positions: List[Dict] = []
        self.grid_start_time: Optional[float] = None
        self.cycle_start_balance: float = 0.0
        self.live_real_balance: float = 29.66  # Mirrors live real account balance
        self.target_mode: str = "REAL"
        
        self.state_file = Path("ghost_grid_state.json")
        self.load_state()

    def get_effective_balance(self, reported_balance: float) -> float:
        """Enforces live real balance ($29.66) on DEMO for 100% identical risk/sizing."""
        return getattr(self, "live_real_balance", 29.66)

    def load_state(self):
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text())
                self.cycle_start_balance = data.get("cycle_start_balance", 0.0)
                if data.get("live_real_balance"):
                    self.live_real_balance = float(data["live_real_balance"])
                if "target_mode" in data and data["target_mode"] in ("DEMO", "REAL"):
                    self.target_mode = data["target_mode"]
                logger.info(f"Loaded state from {self.state_file} (Mode: {self.target_mode})")
            except Exception as e:
                logger.error(f"Failed to load state: {e}")

    def save_state(self):
        data = {
            "cycle_start_balance": self.cycle_start_balance,
            "live_real_balance": self.live_real_balance,
            "target_mode": self.target_mode
        }
        self.state_file.write_text(json.dumps(data, indent=4))

    async def _update_candles(self, quote) -> bool:
        """Returns True if a new candle just formed. Automatically formats for ApexTrinity."""
        mid = quote.mid
        t = getattr(quote, "timestamp", time.time())
        current_minute_ts = int(t // 60) * 60
        open_time_ms = current_minute_ts * 1000
        now = datetime.now(timezone.utc)
        minute_key = now.strftime("%Y-%m-%d %H:%M")
        
        new_candle_formed = False
        if self.current_1m_bar is None or self.current_1m_bar.get("minute_ts") != current_minute_ts:
            if self.current_1m_bar:
                self.candles_1m.append(self.current_1m_bar)
                new_candle_formed = True
                if len(self.candles_1m) > 500:
                    self.candles_1m = self.candles_1m[-300:]
            
            # Initial fast warmup: Pre-seed history from current price so we don't wait 30 minutes idle
            if len(self.candles_1m) < 30:
                for idx in range(30 - len(self.candles_1m)):
                    past_ts = (current_minute_ts - (30 - idx) * 60) * 1000
                    self.candles_1m.append({
                        "minute_ts": current_minute_ts - (30 - idx) * 60,
                        "open_time": past_ts,
                        "time": minute_key,
                        "open": mid,
                        "high": mid + 0.10,
                        "low": mid - 0.10,
                        "close": mid,
                        "volume": 1
                    })
                logger.info(f"⚡ Fast Warmup: Seeded {len(self.candles_1m)} candles at ${mid:.2f}. Immediate execution armed!")

            self.current_1m_bar = {
                "minute_ts": current_minute_ts,
                "open_time": open_time_ms,
                "time": minute_key,
                "open": mid,
                "high": mid,
                "low": mid,
                "close": mid,
                "volume": 1
            }
        else:
            bar = self.current_1m_bar
            bar["high"] = max(bar["high"], mid)
            bar["low"] = min(bar["low"], mid)
            bar["close"] = mid
            bar["volume"] += 1
            
        return new_candle_formed

    async def tick(self, quote):
        # Production Sentinel Invariants:
        # Inviolable Weekend Flatten: Set to 21:45 UTC so we can test right up to the final minutes
        now_utc = datetime.now(timezone.utc)
        is_weekend_lockout = (now_utc.weekday() == 4 and (now_utc.hour > 21 or (now_utc.hour == 21 and now_utc.minute >= 45))) or now_utc.weekday() == 5
        if is_weekend_lockout:
            if self.active_positions:
                logger.warning("🚨 INVIOLABLE FRIDAY WEEKEND FORCE-FLATTEN TRIGGERED! Auto-flattening open grid...")
                await self._flatten_grid(is_win=False, reason="Friday Weekend Force-Flatten (Market Close)")
            return

        # Spread Blowout Guard (> $0.45 / 4.5 pips)
        spread = quote.ask - quote.bid
        if spread > 0.45:
            logger.warning("⚠️ Spread blowout detected ($%.2f > $0.45). Grid entry/exit paused.", spread)
            return

        # Update OHLCV
        new_candle = await self._update_candles(quote)
        
        # Periodic dashboard state sync for terminal and Omni-Auditor
        if int(time.time()) % 5 == 0:
            await self._sync_telemetry(quote)

        # Check active positions for exit
        if self.active_positions:
            await self._manage_active_grid(quote)
            return

        # Only evaluate entry on a new candle close
        if new_candle and len(self.candles_1m) >= 30:
            signal = self.strategy.evaluate(self.candles_1m)
            if signal:
                await self._handle_signal(signal, quote)

    async def _sync_telemetry(self, quote):
        """Sync live ghost grid telemetry to dashboard hft.json."""
        try:
            acc = await self.gateway.get_account_snapshot()
            data_dir = Path("data")
            state_dir = data_dir / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            
            clean_pos = self.active_positions[-1] if self.active_positions else None
            total_active_lots = sum(p.get("volume", 0.0) for p in self.active_positions)
            
            payload = {
                "engine": "👻 XAUUSD Ghost Grid Scalper Engine (DEMO)",
                "status": "ACTIVE",
                "bot_running": True,
                "fsm_state": "IN_POSITION" if self.active_positions else "SCANNING",
                "mode": "BROKER LIVE (Ghost Grid LiteFinance DEMO Account)",
                "account_mode": "DEMO",
                "symbol": "XAUUSD",
                "balance": round(acc.balance, 2),
                "equity": round(acc.equity, 2),
                "realized_pnl": round(acc.balance - self.cycle_start_balance, 2) if self.cycle_start_balance > 0 else 0.0,
                "current_price": quote.mid,
                "mid_price": quote.mid,
                "best_bid": quote.bid,
                "best_ask": quote.ask,
                "spread_bps": round(((quote.ask - quote.bid) / quote.mid * 10000.0), 2) if quote.mid > 0 else 0.0,
                "position": clean_pos,
                "active_grid_orders": len(self.active_positions),
                "active_grid_lots": round(total_active_lots, 2),
                "chameleon_detection_score": self.chameleon.profile.detection_score,
                "updated_at": time.time(),
                "updated_iso": datetime.now(timezone.utc).isoformat(),
            }
            target = state_dir / "hft.json"
            tmp = target.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(payload, f, indent=2)
            tmp.replace(target)
        except Exception:
            pass

    async def _manage_active_grid(self, quote):
        # Calculate total PNL
        total_pnl = 0.0
        total_volume = 0.0
        avg_entry = 0.0
        for pos in self.active_positions:
            is_buy = pos["direction"].upper() == "BUY"
            if is_buy:
                pnl = (quote.bid - pos["entry_price"]) * pos["volume"] * 100
            else:
                pnl = (pos["entry_price"] - quote.ask) * pos["volume"] * 100
            pos["unrealized_pl"] = pnl
            pos["lot_size"] = pos["volume"]
            total_pnl += pnl
            total_volume += pos["volume"]
            avg_entry += pos["entry_price"] * pos["volume"]
            
        if total_volume > 0:
            avg_entry /= total_volume
            
        # Hard total grid loss floor (-$5.00 for micro capital protection)
        if total_pnl <= -5.00:
            logger.warning("Hard grid loss floor reached (-$5.00). Flattening!")
            await self._flatten_grid(is_win=False, reason="Hard Stop Loss Ceiling Hit (-$5.00)")
            return
            
        # Call GridExitController
        now = time.time()
        decision = self.exit_controller.evaluate_grid_tick(
            current_price=quote.mid,
            grid_positions=self.active_positions,
            current_time=now
        )
        
        if decision.action in ("CLOSE_ALL", "SCALE_OUT_60", "SCALE_OUT_80"):
            logger.info(f"Exit controller triggered {decision.action}. Reason: {decision.reason}")
            is_win = total_pnl > 0
            await self._flatten_grid(is_win=is_win)
            
    async def _flatten_grid(self, is_win: bool, reason: str = "Exit Triggered"):
        logger.info(f"Flattening all grid positions ({reason}).")
        try:
            await self.gateway.flatten_all_positions()
        except Exception as e:
            logger.error(f"Error flattening positions: {e}")
            
        hold_time = time.time() - self.grid_start_time if self.grid_start_time else 0
        total_lot = sum(p.get("volume", 0.0) for p in self.active_positions)
        
        self.chameleon.record_trade_result(is_win=is_win, hold_time=hold_time, lot_size=total_lot, slippage=0)
        self.active_positions.clear()
        self.grid_start_time = None
        self.exit_controller.reset()
        
        # Check compounding / withdrawal & push rich alert
        try:
            account = await self.gateway.get_account_snapshot()
            win_tag = "💰 Profit Locked" if is_win else "🛑 Risk Stopped"
            push_ntfy(
                title=f"👻 Ghost Grid: {win_tag}",
                message=f"{reason} · Balance: ${account.balance:.2f} · Active Orders Closed",
                tags="moneybag,ghost" if is_win else "octagonal_sign,shield",
                priority="high"
            )
            if self.compounding.check_withdrawal(account.balance):
                logger.info(f"Withdrawal threshold met! Current balance: {account.balance}")
                self.cycle_start_balance = account.balance
                push_ntfy(
                    title="🎯 Withdrawal Threshold Met",
                    message=f"Balance ${account.balance:.2f} exceeds withdrawal threshold.",
                    tags="bank,trophy",
                    priority="urgent"
                )
            self.save_state()
        except Exception as e:
            logger.error(f"Error checking withdrawal: {e}")

    async def _handle_signal(self, signal, quote):
        # Map signal direction to broker direction
        raw_dir = str(getattr(signal, "direction", "BUY")).upper()
        broker_dir = "BUY" if "BUY" in raw_dir or "LONG" in raw_dir else "SELL"
        
        logger.info(f"Signal received: {raw_dir} -> {broker_dir} at {quote.mid}")
        
        # 1. Anti-detection skip
        if self.noise_engine.should_skip_setup():
            logger.info("NoiseEngine skipped setup for anti-detection.")
            return
            
        # 2. Broker Chameleon safety
        if not self.chameleon.is_safe_to_trade():
            logger.warning("BrokerChameleon says unsafe to trade.")
            return

        # 3. Macro Veto
        if self.politician_brain:
            try:
                allowed, reason = self.politician_brain.is_entry_allowed()
                if not allowed:
                    logger.info(f"PoliticianBrain vetoed: {reason}")
                    return
            except Exception as e:
                logger.error(f"PoliticianBrain error: {e}")
                
        # 4. AI Veto
        if self.laya_oracle:
            try:
                decision = self.laya_oracle.evaluate_setup_sync({
                    "direction": broker_dir,
                    "confidence": getattr(signal, "confidence", 0.8),
                    "wick_ratio": getattr(signal, "wick_ratio", 0.5)
                })
                if decision and not getattr(decision, "approve", True):
                    logger.info(f"LayaOracle vetoed: {getattr(decision, 'reasoning', 'No approval')}")
                    return
            except Exception as e:
                logger.error(f"LayaOracle error: {e}")
                
        # 5. Resolve compounding tier using effective balance ($29.66)
        account = await self.gateway.get_account_snapshot()
        eff_bal = self.get_effective_balance(account.balance)
        if self.cycle_start_balance == 0:
            self.cycle_start_balance = eff_bal
            self.save_state()
            
        tier = self.compounding.resolve_tier(eff_bal)
        base_lot = min(tier.lot_size, 0.05) # Safety limit for demo testing
        n_orders = min(tier.grid_count, 5) # Safety cap on demo
        
        logger.info(f"Deploying grid. Eff Balance: ${eff_bal:.2f} (Demo: ${account.balance:.2f}), Tier Lot: {base_lot}, Count: {n_orders}, Grade: {tier.risk_grade}")
        
        # Generate noise plan
        noise_plan = self.noise_engine.generate_grid_noise_plan(base_lot=base_lot, num_orders=n_orders)
        if noise_plan.skip_setup:
            logger.info("Noise plan chose to skip this setup.")
            return

        delays = [d / 1000.0 for d in noise_plan.delays_ms]
        lots = noise_plan.lot_adjustments
        
        self.grid_start_time = time.time()
        
        for i in range(len(lots)):
            delay = delays[i] if i < len(delays) else 0.5
            if delay > 0:
                await asyncio.sleep(delay)
                
            lot = min(lots[i], 0.05)
            
            logger.info(f"Executing grid order {i+1}/{len(lots)}: {broker_dir} {lot:.2f} lots")
            try:
                res = await self.gateway.open_market_order(
                    direction=broker_dir,
                    volume=lot,
                    sl_price=None,
                    tp_price=None,
                    expected_mode=self.target_mode
                )
                if res and res.get("success"):
                    self.active_positions.append({
                        "entry_price": quote.ask if broker_dir == "BUY" else quote.bid,
                        "volume": lot,
                        "direction": broker_dir,
                        "entry_time": time.time(),
                        "unrealized_pl": 0.0,
                        "lot_size": lot
                    })
                else:
                    logger.warning(f"Order {i+1} returned: {res}")
            except Exception as e:
                logger.error(f"Failed to place grid order {i+1}: {e}")
                
        logger.info(f"Grid deployment complete. {len(self.active_positions)} positions active.")
        if self.active_positions:
            total_active_lots = sum(p.get("volume", 0.0) for p in self.active_positions)
            push_ntfy(
                title=f"⚡ Ghost Grid Deployed: {broker_dir}",
                message=f"Dispatched {len(self.active_positions)} orders · Total: {total_active_lots:.2f} lots @ ${quote.mid:.2f} · Tier: {tier.risk_grade}",
                tags="zap,ghost",
                priority="high"
            )
