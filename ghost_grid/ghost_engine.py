"""
Ghost Grid Trading Engine (Forensic MR P FX Scalper Edition).
Directly mirrors MR P FX's XAUUSD strategy from the video:
1. Entry: Pure M5 Key Support/Resistance Break + M1 Surgical Retest with Rejection Wick.
2. Deployment: Staggered Micro-Grid (0.10s-0.25s human taps) with Disaster SL protection.
3. Exit: Lightning-fast profit lock (+$0.35-$0.60 pts, $1.20-$3.50 immediate basket take-profit).
4. Time decay: Strictly 45-90 seconds max holding time. Never hold through chops.
5. AI Veto: Fast Laya/Politician sanity check before entry.
"""

import asyncio
import json
import logging
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional, Any

from ghost_grid.noise_engine import NoiseEngine, NoiseConfig
from ghost_grid.compounding_ladder import CompoundingLadder
from ghost_grid.exit_controller import GridExitController, GridExitConfig, GridExitDecision
from ghost_grid.broker_chameleon import BrokerChameleon, ChameleonConfig
from ghost_grid.mrp_break_retest import MRPBreakRetestStrategy, MRPSignal

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
        
        # Noise Engine with fast human taps (150ms - 450ms) matching MR P FX video
        noise_cfg = NoiseConfig(
            delay_mu_ms=250.0,
            delay_sigma_ms=75.0,
            delay_min_ms=120.0,
            delay_max_ms=450.0,
            hesitation_prob=0.05,
            skip_marginal_setup_prob=0.0
        )
        self.noise_engine = NoiseEngine(noise_cfg)
        self.compounding = CompoundingLadder(withdrawal_threshold=2500.0, max_loss_floor=4.0)
        self.exit_controller = GridExitController(GridExitConfig())
        self.chameleon = BrokerChameleon(ChameleonConfig())
        
        # Core Strategy: Forensic MR P FX Break & Retest
        self.strategy = MRPBreakRetestStrategy(lookback_5m=12)
        
        # AI Safety Layer
        self.laya_oracle = get_laya_oracle() if get_laya_oracle else None
        self.politician_brain = get_politician_brain() if get_politician_brain else None
        
        # Market Data State
        self.candles_1m: List[Dict] = []
        self.current_1m_bar: Optional[Dict] = None
        
        # Position State
        self.active_positions: List[Dict] = []
        self.grid_start_time: Optional[float] = None
        self.cycle_start_balance: float = 0.0
        self.live_real_balance: float = 14.36
        self.target_mode: str = "DEMO"  # Default safe on startup
        
        self.state_file = Path("ghost_grid_state.json")
        self.load_state()

    def get_effective_balance(self, reported_balance: float) -> float:
        return getattr(self, "live_real_balance", reported_balance)

    def load_state(self):
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text())
                self.cycle_start_balance = data.get("cycle_start_balance", 0.0)
                if data.get("live_real_balance"):
                    self.live_real_balance = float(data["live_real_balance"])
                if "target_mode" in data and data["target_mode"] in ("DEMO", "REAL"):
                    self.target_mode = data["target_mode"]
                logger.info(f"Loaded state from {self.state_file} (Mode: {self.target_mode}, Balance: ${self.live_real_balance:.2f})")
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
            
            # Initial fast warmup
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
                        "volume": 50.0
                    })
                logger.info(f"⚡ Fast Warmup: Seeded 30 candles at ${mid:.2f}. Immediate execution armed!")
                
            self.current_1m_bar = {
                "minute_ts": current_minute_ts,
                "open_time": open_time_ms,
                "time": minute_key,
                "open": mid,
                "high": mid,
                "low": mid,
                "close": mid,
                "volume": 1.0
            }
        else:
            self.current_1m_bar["high"] = max(self.current_1m_bar["high"], mid)
            self.current_1m_bar["low"] = min(self.current_1m_bar["low"], mid)
            self.current_1m_bar["close"] = mid
            self.current_1m_bar["volume"] = self.current_1m_bar.get("volume", 0.0) + 1.0
            
        return new_candle_formed

    async def tick(self, quote):
        if quote is None:
            return

        now_utc = datetime.now(timezone.utc)

        # Inviolable Friday Curfew: 21:45 UTC (01:15 AM Tehran Saturday)
        if now_utc.weekday() == 4 and (now_utc.hour > 21 or (now_utc.hour == 21 and now_utc.minute >= 45)):
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
        
        # Periodic dashboard state sync
        if int(time.time()) % 5 == 0:
            await self._sync_telemetry(quote)

        # 1. Manage Active Positions (Priority #1: Rapid Scalp Exits)
        if self.active_positions:
            await self._manage_active_grid(quote)
            return

        # 2. Evaluate Entry on new candle or tick
        if new_candle and len(self.candles_1m) >= 30:
            signal = self.strategy.evaluate(self.candles_1m)
            if signal:
                await self._handle_signal(signal, quote)

    async def _sync_telemetry(self, quote):
        try:
            acc = await self.gateway.get_account_snapshot()
            data_dir = Path("data")
            state_dir = data_dir / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            
            clean_pos = self.active_positions[-1] if self.active_positions else None
            total_active_lots = sum(p.get("volume", 0.0) for p in self.active_positions)
            
            payload = {
                "engine": "👻 MR P FX Break & Retest Scalper (Ghost Grid)",
                "status": "ACTIVE",
                "bot_running": True,
                "fsm_state": "IN_POSITION" if self.active_positions else "SCANNING",
                "mode": f"BROKER LIVE (LiteFinance {self.target_mode} Account)",
                "account_mode": self.target_mode,
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
            
        # Hard total basket stop loss ceiling (-$4.00)
        if total_pnl <= -4.00:
            logger.warning(f"🛑 Hard grid risk floor reached (${total_pnl:.2f}). Flattening!")
            await self._flatten_grid(is_win=False, reason="Hard Stop Loss Ceiling Hit (-$4.00)")
            return
            
        now = time.time()
        decision = self.exit_controller.evaluate_grid_tick(
            current_price=quote.mid,
            grid_positions=self.active_positions,
            current_time=now
        )
        
        if decision.action == "CLOSE_ALL":
            logger.info(f"⚡ Exit controller triggered CLOSE_ALL. Reason: {decision.reason}")
            is_win = total_pnl > 0
            await self._flatten_grid(is_win=is_win, reason=decision.reason)

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
        
        try:
            account = await self.gateway.get_account_snapshot()
            self.live_real_balance = account.balance
            win_tag = "💰 Profit Locked" if is_win else "🛑 Risk Stopped"
            push_ntfy(
                title=f"👻 MR P FX: {win_tag}",
                message=f"{reason} · Balance: ${account.balance:.2f} · Scalp Closed",
                tags="moneybag,ghost" if is_win else "octagonal_sign,shield",
                priority="high"
            )
            if self.compounding.check_withdrawal(account.balance):
                logger.info(f"Withdrawal threshold met! Balance: ${account.balance:.2f}")
                push_ntfy(
                    title="🎯 Withdrawal Threshold Met",
                    message=f"Balance ${account.balance:.2f} exceeds withdrawal threshold.",
                    tags="bank,trophy",
                    priority="urgent"
                )
            self.save_state()
        except Exception as e:
            logger.error(f"Error updating state after flatten: {e}")

    async def _handle_signal(self, signal: MRPSignal, quote):
        broker_dir = signal.direction
        logger.info(f"MR P FX Signal: {broker_dir} at {quote.mid:.2f} (Break: {signal.breakout_level:.2f})")
        
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
                
        # 4. AI Veto (Laya System 1 check)
        if self.laya_oracle:
            try:
                decision = self.laya_oracle.evaluate_setup_sync({
                    "direction": broker_dir,
                    "confidence": 0.85,
                    "wick_ratio": 0.50
                })
                if decision and not getattr(decision, "approve", True):
                    logger.info(f"LayaOracle vetoed: {getattr(decision, 'reasoning', 'No approval')}")
                    return
            except Exception as e:
                logger.error(f"LayaOracle error: {e}")
                
        # 5. Resolve Compounding Tier
        account = await self.gateway.get_account_snapshot()
        eff_bal = self.get_effective_balance(account.balance)
        if self.cycle_start_balance == 0:
            self.cycle_start_balance = eff_bal
            self.save_state()
            
        tier = self.compounding.resolve_tier(eff_bal)
        base_lot = tier.lot_size
        n_orders = tier.grid_count
        
        logger.info(f"Deploying MR P FX Grid: Eff Bal: ${eff_bal:.2f}, Lot: {base_lot} x {n_orders}, Tier: {tier.risk_grade}")
        
        self.grid_start_time = time.time()
        
        # Rapid Taps (150-350ms delays)
        for i in range(n_orders):
            if i > 0:
                tap_delay = random.uniform(0.15, 0.35)
                await asyncio.sleep(tap_delay)
                
            lot = base_lot
            
            # Physical Disaster SL to protect broker account from sudden spikes
            disaster_dist = 2.50
            sl_price = round(quote.bid - disaster_dist if broker_dir == "BUY" else quote.ask + disaster_dist, 2)
            
            logger.info(f"Executing scalp order {i+1}/{n_orders}: {broker_dir} {lot:.2f} lots (Disaster SL: {sl_price})")
            try:
                res = await self.gateway.open_market_order(
                    direction=broker_dir,
                    volume=lot,
                    sl_price=sl_price,
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
                title=f"⚡ MR P FX Scalp Deployed: {broker_dir}",
                message=f"Dispatched {len(self.active_positions)} orders · Total: {total_active_lots:.2f} lots @ ${quote.mid:.2f} · Tier: {tier.risk_grade}",
                tags="zap,ghost",
                priority="high"
            )
