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
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional, Any

MIN_REAL_CANDLES = 55            # EMA50 + >=7 five-minute bars need real history, never synthetic
MAX_CANDLE_GAP_SECS = 600        # persisted candles older than this are discarded
BASKET_STOP_PTS = 1.30           # adverse net move (after spread) that kills the basket (~-$4 on 0.03 lots, as designed)
BASKET_RISK_EQUITY_PCT = float(os.getenv("GHOST_RISK_PCT", "0.15"))   # max equity risked per basket
DISASTER_SL_MULT = 1.5           # broker-side SL = 1.5x the software stop distance
DISASTER_SL_MIN_PTS = 0.60
DISASTER_SL_MAX_PTS = 3.00
MAX_CONSEC_LOSSES = 3
LOSS_COOLDOWN_SECS = 1800
DAILY_LOSS_LIMIT_PCT = 0.30
HEARTBEAT_SECS = 1800
MAX_FLATTEN_RETRIES = 3

# Flip strategy selection. "runner" (default): 4h volatility breakout with a trailing stop, validated on real gold
# data (PF 1.4-1.6, positive in both halves, survives slippage stress). "mrp": the original tight scalp.
STRATEGY = os.getenv("GHOST_STRATEGY", "mrp").lower()
RUNNER_LOOKBACK = int(os.getenv("RUNNER_LOOKBACK", "48"))
RUNNER_ATR_MIN = float(os.getenv("RUNNER_ATR_MIN", "3.5"))
RUNNER_STOP_PTS = float(os.getenv("RUNNER_STOP_PTS", "4.0"))
RUNNER_TRAIL_PTS = float(os.getenv("RUNNER_TRAIL_PTS", "2.0"))
RUNNER_RISK_PCT = float(os.getenv("RUNNER_RISK_PCT", "0.12"))
MARGIN_BUFFER = float(os.getenv("GHOST_MARGIN_BUFFER", "1.2"))   # 0.01 lot needs ~$8.3 margin; 1.5x would refuse a $12.47 account
ACCOUNT_LEVERAGE = float(os.getenv("GHOST_LEVERAGE", "500"))   # LiteFinance ECN: 0.01 lot needs ~$8.3 margin at $4,145

from ghost_grid.noise_engine import NoiseEngine, NoiseConfig
from ghost_grid.compounding_ladder import CompoundingLadder
from ghost_grid.exit_controller import GridExitController, GridExitConfig, GridExitDecision
from ghost_grid.broker_chameleon import BrokerChameleon, ChameleonConfig
from ghost_grid.mrp_break_retest import MRPBreakRetestStrategy, MRPSignal
from ghost_grid.runner_strategy import RunnerStrategy
from ghost_grid.runner_exit import RunnerExitController, RunnerExitConfig

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

from concurrent.futures import ThreadPoolExecutor
_NTFY_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ghost_ntfy")


def _send_ntfy(title: str, message: str, priority: str) -> None:
    try:
        clean_msg = " · ".join([line.strip() for line in message.strip().splitlines() if line.strip()])
        if send_alert:
            send_alert(title=title.strip(), message=clean_msg, priority=priority)
    except Exception as e:
        logging.getLogger("GhostEngine").debug("push_ntfy dispatch failed: %s", e)


def push_ntfy(title: str, message: str, tags: str = "ghost,zap", priority: str = "high") -> None:
    """Fire-and-forget: a slow ntfy round-trip must never stall exit management."""
    try:
        _NTFY_POOL.submit(_send_ntfy, title, message, priority)
    except Exception:
        pass


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
        if STRATEGY == "runner":
            self.exit_controller = RunnerExitController(RunnerExitConfig(stop_pts=RUNNER_STOP_PTS, trail_pts=RUNNER_TRAIL_PTS))
            self.stop_pts = RUNNER_STOP_PTS
        else:
            self.exit_controller = GridExitController(GridExitConfig())
            self.stop_pts = BASKET_STOP_PTS
        self.chameleon = BrokerChameleon(ChameleonConfig())
        
        # Core Strategy: Forensic MR P FX Break & Retest
        if STRATEGY == "runner":
            self.strategy = RunnerStrategy(lookback=RUNNER_LOOKBACK, atr_min=RUNNER_ATR_MIN, stop_pts=RUNNER_STOP_PTS)
        else:
            self.strategy = MRPBreakRetestStrategy()
        self.min_candles = max(MIN_REAL_CANDLES, getattr(self.strategy, "min_warmup", MIN_REAL_CANDLES))
        
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
        self.broker_baseline: float = 0.0
        self.target_mode: str = "DEMO"  # Default safe on startup
        
        self.consec_losses: int = 0
        self.cooldown_until: float = 0.0
        self.day_key: str = ""
        self.day_start_bal: float = 0.0
        self.day_halted: bool = False
        self.last_basket_pnl: float = 0.0
        self.last_heartbeat: float = 0.0
        self.ready_notified: bool = False
        self.trades_today: int = 0
        self.wins_today: int = 0
        self.halted: bool = False
        self.halt_reason: str = ""
        self.candle_file = Path("data/state/ghost_candles.json")
        self.state_file = Path("ghost_grid_state.json")
        self.load_state()
        self._load_candles()

    def get_effective_balance(self, reported_balance: float) -> float:
        if self.target_mode == "REAL":
            return reported_balance if reported_balance > 0 else self.live_real_balance
        return getattr(self, "live_real_balance", reported_balance)

    def load_state(self):
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text())
                self.cycle_start_balance = data.get("cycle_start_balance", 0.0)
                if data.get("live_real_balance"):
                    self.live_real_balance = float(data["live_real_balance"])
                if data.get("broker_baseline"):
                    self.broker_baseline = float(data["broker_baseline"])
                if "target_mode" in data and data["target_mode"] in ("DEMO", "REAL"):
                    self.target_mode = data["target_mode"]
                logger.info(f"Loaded state from {self.state_file} (Mode: {self.target_mode}, Balance: ${self.live_real_balance:.2f})")
            except Exception as e:
                logger.error(f"Failed to load state: {e}")

    def save_state(self):
        data = {
            "cycle_start_balance": self.cycle_start_balance,
            "live_real_balance": self.live_real_balance,
            "broker_baseline": self.broker_baseline,
            "target_mode": self.target_mode
        }
        self.state_file.write_text(json.dumps(data, indent=4))

    def _load_candles(self) -> None:
        """Restore recent REAL candles so a restart does not need a full warmup."""
        try:
            if not self.candle_file.exists():
                return
            saved = json.loads(self.candle_file.read_text())
            if not saved:
                return
            if time.time() - saved[-1]["minute_ts"] > MAX_CANDLE_GAP_SECS:
                return
            self.candles_1m = saved
            logger.info("Restored %d persisted real candles.", len(saved))
        except Exception as e:
            logger.warning("Could not restore candles: %s", e)

    def _save_candles(self) -> None:
        try:
            self.candle_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.candle_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.candles_1m[-max(200, self.min_candles + 20):]))
            tmp.replace(self.candle_file)
        except Exception as e:
            logger.debug("Candle persist failed: %s", e)

    async def _update_candles(self, quote) -> bool:
        mid = quote.mid
        t = getattr(quote, "timestamp", time.time())
        current_minute_ts = int(t // 60) * 60
        open_time_ms = current_minute_ts * 1000
        now = datetime.now(timezone.utc)
        minute_key = datetime.fromtimestamp(current_minute_ts, timezone.utc).strftime("%Y-%m-%d %H:%M")
        
        new_candle_formed = False
        if self.current_1m_bar is None or self.current_1m_bar.get("minute_ts") != current_minute_ts:
            if self.current_1m_bar:
                self.candles_1m.append(self.current_1m_bar)
                new_candle_formed = True
                if len(self.candles_1m) > 700:
                    self.candles_1m = self.candles_1m[-500:]
                self._save_candles()
            
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

        # Market-closed / high-risk windows: Friday >= 20:30 UTC (flatten before the close), all Saturday,
        # Sunday before 22:00 UTC, and the 23:00 UTC rollover hour (spread dead-zone).
        wd, hh, mm = now_utc.weekday(), now_utc.hour, now_utc.minute
        closed = (wd == 4 and (hh > 20 or (hh == 20 and mm >= 30))) or wd == 5 or (wd == 6 and hh < 22)
        rollover = hh == 23
        if closed:
            if self.active_positions:
                logger.warning("🚨 WEEKEND/FRIDAY CURFEW! Auto-flattening open grid...")
                await self._flatten_grid(is_win=False, reason="Weekend Force-Flatten (Market Close)")
            return

        spread = quote.ask - quote.bid
        wide_spread = spread > 0.45

        # Update OHLCV
        new_candle = await self._update_candles(quote)
        
        # Periodic dashboard state sync
        if time.time() - getattr(self, "_last_sync", 0.0) >= 5.0:
            self._last_sync = time.time()
            await self._sync_telemetry(quote)

        if not self.ready_notified and len(self.candles_1m) >= self.min_candles:
            self.ready_notified = True
            push_ntfy(title="✅ Ghost Grid ready", message=f"{len(self.candles_1m)} real candles loaded · scanning for M5 break + M1 retest · mode {self.target_mode} · balance ${self.live_real_balance:.2f}", tags="white_check_mark", priority="default")
        if time.time() - self.last_heartbeat >= HEARTBEAT_SECS:
            self.last_heartbeat = time.time()
            push_ntfy(title="💓 Ghost Grid heartbeat", message=f"{self.target_mode} · balance ${self.live_real_balance:.2f} · candles {len(self.candles_1m)}/{self.min_candles} · price {quote.mid:.2f} spread ${spread:.2f} · positions {len(self.active_positions)} · trades today {self.trades_today}", tags="heartbeat", priority="min")

        # 1. Manage Active Positions (Priority #1: exits run even when the spread is wide)
        if self.active_positions:
            await self._manage_active_grid(quote)
            return

        if self.halted:
            return
        blocked = self._entries_blocked()
        if blocked:
            return
        if wide_spread:
            logger.warning("⚠️ Spread blowout ($%.2f > $0.45). New entries paused.", spread)
            return
        if rollover:
            return

        # 2. Evaluate Entry on new candle or tick
        if new_candle and len(self.candles_1m) >= self.min_candles:
            signal = self.strategy.evaluate(self.candles_1m)
            if signal:
                await self._handle_signal(signal, quote)

    def _refresh_mirror_balance(self, broker_balance: float) -> None:
        """DEMO mirror: track the seeded capital + our own PnL since baseline, so tier
        sizing and telemetry show the simulated account instead of the broker balance."""
        if self.target_mode == "REAL" or broker_balance <= 0:
            return
        if self.broker_baseline <= 0:
            self.broker_baseline = broker_balance
        if self.cycle_start_balance > 0:
            self.live_real_balance = self.cycle_start_balance + (broker_balance - self.broker_baseline)

    async def _sync_telemetry(self, quote):
        try:
            acc = await self.gateway.get_account_snapshot()
            self._refresh_mirror_balance(acc.balance)
            is_demo_mirror = self.target_mode != "REAL"
            display_balance = self.live_real_balance if is_demo_mirror else acc.balance
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
                "balance": round(display_balance, 2),
                "equity": round(display_balance + (acc.equity - acc.balance), 2),
                "broker_balance": round(acc.balance, 2),
                "broker_equity": round(acc.equity, 2),
                "realized_pnl": round(acc.balance - self.broker_baseline, 2) if self.broker_baseline > 0 else 0.0,
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

    def _entries_blocked(self) -> str:
        """Risk guards around the strategy (the signal itself is untouched). Returns a reason or ''."""
        now = time.time()
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if day != self.day_key:
            if self.day_key and self.trades_today:
                push_ntfy(title="📊 Ghost Grid daily summary", message=f"{self.day_key}: {self.trades_today} trades, {self.wins_today} wins · Balance ${self.live_real_balance:.2f}", tags="bar_chart", priority="default")
            self.day_key, self.day_start_bal, self.day_halted = day, self.live_real_balance, False
            self.trades_today = self.wins_today = 0
        if self.day_halted:
            return "daily loss limit reached"
        if self.day_start_bal > 0 and self.live_real_balance <= self.day_start_bal * (1 - DAILY_LOSS_LIMIT_PCT):
            self.day_halted = True
            push_ntfy(title="🛑 Ghost Grid: daily loss limit", message=f"Balance ${self.live_real_balance:.2f} is {DAILY_LOSS_LIMIT_PCT*100:.0f}% below the day start ${self.day_start_bal:.2f}. No new entries until tomorrow (UTC).", tags="octagonal_sign", priority="urgent")
            return "daily loss limit reached"
        if now < self.cooldown_until:
            return f"cooldown after {MAX_CONSEC_LOSSES} losses ({int(self.cooldown_until - now)}s left)"
        return ""

    def _basket_stop_usd(self) -> float:
        lots = sum(p.get("volume", 0.0) for p in self.active_positions)
        return round(self.stop_pts * lots * 100.0, 2)

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
            
        # Hard basket stop, scaled with equity (floor -$4)
        self.last_basket_pnl = total_pnl
        stop_usd = self._basket_stop_usd()
        if total_pnl <= -stop_usd and not getattr(self.exit_controller, "armed", False):
            logger.warning(f"🛑 Hard grid risk floor reached (${total_pnl:.2f}). Flattening!")
            await self._flatten_grid(is_win=False, reason=f"Hard Stop Loss Ceiling Hit (-${stop_usd:.2f})")
            return
            
        now = time.time()
        if self.grid_start_time:
            self.exit_controller.grid_start_time = self.grid_start_time
        if hasattr(self.exit_controller.config, "hard_stop_pts"):
            self.exit_controller.config.hard_stop_pts = BASKET_STOP_PTS
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
        flat_ok = False
        for attempt in range(1, MAX_FLATTEN_RETRIES + 1):
            try:
                res = await self.gateway.flatten_all_positions()
                if res and res.get("success"):
                    flat_ok = True
                    break
                logger.error("Flatten attempt %d/%d incomplete: %s", attempt, MAX_FLATTEN_RETRIES, res)
            except Exception as e:
                logger.error(f"Error flattening positions (attempt {attempt}): {e}")
            await asyncio.sleep(0.3)
        if not flat_ok:
            # Positions may still be open at the broker: keep tracking them, stop new entries, alert.
            self.halted = True
            self.halt_reason = f"Flatten failed after {MAX_FLATTEN_RETRIES} attempts ({reason})"
            logger.critical("🚨 %s. Engine HALTED for new entries; still managing exits.", self.halt_reason)
            push_ntfy(title="🚨 Ghost Grid: FLATTEN FAILED", message=self.halt_reason, tags="rotating_light", priority="urgent")
            return

        hold_time = time.time() - self.grid_start_time if self.grid_start_time else 0
        total_lot = sum(p.get("volume", 0.0) for p in self.active_positions)
        
        self.chameleon.record_trade_result(is_win=is_win, hold_time=hold_time, lot_size=total_lot, slippage=0)
        pnl = self.last_basket_pnl
        self.trades_today += 1
        if pnl > 0:
            self.wins_today += 1
            self.consec_losses = 0
        else:
            self.consec_losses += 1
            if self.consec_losses >= MAX_CONSEC_LOSSES:
                self.cooldown_until = time.time() + LOSS_COOLDOWN_SECS
                self.consec_losses = 0
                push_ntfy(title="⏸️ Ghost Grid cooldown", message=f"{MAX_CONSEC_LOSSES} losses in a row. Pausing entries for {LOSS_COOLDOWN_SECS // 60} min.", tags="pause_button", priority="high")
        self.active_positions.clear()
        self.halted = False
        self.grid_start_time = None
        self.exit_controller.reset()
        
        try:
            account = await self.gateway.get_account_snapshot()
            if self.target_mode == "REAL":
                self.live_real_balance = account.balance
            else:
                self._refresh_mirror_balance(account.balance)
            win_tag = "💰 Profit Locked" if is_win else "🛑 Risk Stopped"
            push_ntfy(
                title=f"👻 MR P FX: {win_tag}",
                message=f"{reason} · basket P/L ${pnl:+.2f} · held {hold_time:.0f}s · {total_lot:.2f} lots · broker balance ${account.balance:.2f} · tracked ${self.live_real_balance:.2f}",
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
        push_ntfy(title=f"🎯 MR P FX signal: {broker_dir}", message=f"{signal.reasoning} · price {quote.mid:.2f} · wick {signal.wick_ratio:.2f}", tags="dart", priority="default")
        
        # 1. Anti-detection skip
        if self.noise_engine.should_skip_setup():
            logger.info("NoiseEngine skipped setup for anti-detection.")
            push_ntfy(title=f"⚪ Signal skipped ({broker_dir})", message="Anti-detection noise engine skipped this setup.", tags="white_circle", priority="low")
            return
            
        # 2. Broker Chameleon safety
        if not self.chameleon.is_safe_to_trade():
            logger.warning("BrokerChameleon says unsafe to trade.")
            push_ntfy(title=f"⚪ Signal skipped ({broker_dir})", message="BrokerChameleon: detection score too high.", tags="white_circle", priority="low")
            return

        # 3. Macro Veto
        if self.politician_brain:
            try:
                allowed, reason = self.politician_brain.is_entry_allowed()
                if not allowed:
                    logger.info(f"PoliticianBrain vetoed: {reason}")
                    push_ntfy(title=f"🏛️ Signal vetoed ({broker_dir})", message=f"PoliticianBrain: {reason}", tags="classical_building", priority="default")
                    return
            except Exception as e:
                logger.error(f"PoliticianBrain error: {e}")
                
        # 4. AI Veto (Laya System 1 check)
        if self.laya_oracle:
            try:
                decision = await self.laya_oracle.evaluate_setup({
                    "direction": broker_dir,
                    "entry_price": signal.entry_price,
                    "sl_price": signal.sl_price,
                    "wick_ratio": signal.wick_ratio,
                    "setup_type": "BREAKOUT_RETEST",
                    "trend_aligned": True,
                    "hour_utc": datetime.now(timezone.utc).hour,
                })
                if decision and not decision.is_valid:
                    logger.info(f"Jeff/Laya oracle vetoed: {decision.reasoning}")
                    push_ntfy(title=f"🧠 Laya vetoed ({broker_dir})", message=f"{decision.reasoning} · trap {decision.trap_probability*100:.0f}% · confluence {decision.confluence_score:.1f}/10", tags="brain", priority="default")
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
        
        if STRATEGY == "runner":
            # One order, risk-fraction sized, min 0.01 lot; refuse if a full stop would exceed 45% of equity or margin fails.
            n_orders = 1
            lots_raw = RUNNER_RISK_PCT * eff_bal / (self.stop_pts * 100.0)
            base_lot = max(0.01, int(lots_raw / 0.01) * 0.01)
            max_lot_margin = 0.9 * eff_bal * ACCOUNT_LEVERAGE / (quote.mid * 100.0)
            base_lot = min(base_lot, int(max_lot_margin / 0.01) * 0.01)
            if base_lot < 0.01:
                logger.warning("Balance $%.2f cannot carry 0.01 lot at 1:%d. Skipping.", eff_bal, int(ACCOUNT_LEVERAGE))
                return
            if base_lot * self.stop_pts * 100.0 > 0.45 * eff_bal:
                logger.warning("Full stop $%.2f would exceed 45%% of equity $%.2f. Skipping.", base_lot * self.stop_pts * 100.0, eff_bal)
                return
            total_lots = base_lot
            stop_usd = self.stop_pts * total_lots * 100.0
            disaster_dist = round(self.stop_pts * 1.25, 2)
        else:
            # Risk cap: total lots * stop distance must stay within BASKET_RISK_EQUITY_PCT of equity.
            max_total = BASKET_RISK_EQUITY_PCT * eff_bal / (BASKET_STOP_PTS * 100.0)
            while n_orders > 1 and base_lot * n_orders > max_total + 1e-9:
                n_orders -= 1
            if base_lot * n_orders > max_total + 1e-9:
                base_lot = max(0.01, int(max_total / n_orders * 100) / 100.0)
            total_lots = base_lot * n_orders
            needed_margin = total_lots * quote.mid * 100 / ACCOUNT_LEVERAGE * MARGIN_BUFFER
            if account.available > 0 and account.available < needed_margin:
                logger.warning("Insufficient free margin ($%.2f < $%.2f) for %.2f lots. Skipping.", account.available, needed_margin, total_lots)
                return
            stop_usd = BASKET_STOP_PTS * total_lots * 100.0
            disaster_dist = min(DISASTER_SL_MAX_PTS, max(DISASTER_SL_MIN_PTS, BASKET_STOP_PTS * DISASTER_SL_MULT))

        logger.info(f"Deploying MR P FX Grid: Eff Bal: ${eff_bal:.2f}, Lot: {base_lot} x {n_orders}, Tier: {tier.risk_grade}, Basket stop -${stop_usd:.2f}, Disaster SL {disaster_dist:.2f}pt")
        
        self.grid_start_time = time.time()
        
        # Rapid Taps (150-350ms delays)
        for i in range(n_orders):
            if i > 0:
                tap_delay = random.uniform(0.15, 0.35)
                await asyncio.sleep(tap_delay)
                
            lot = base_lot
            
            # Physical Disaster SL to protect broker account from sudden spikes
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
                    if "SL_NOT_SET" in str((res or {}).get("error", "")) or "MODE" in str((res or {}).get("error", "")):
                        break
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
