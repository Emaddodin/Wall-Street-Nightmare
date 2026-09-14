"""Configuration loading and validation.

All strategy parameters live in YAML (config/default.yaml).  A sweep or the
backtester may override any leaf with a plain dict; the engine never reads a
parameter that was not declared here, so a typo fails loudly instead of
silently running the default.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent / "default.yaml"

_SCHEMA: dict[str, type] = {
    "timeframes.htf": str,
    "timeframes.entry": str,
    "universe.top_n": int,
    "universe.min_volume_usdt_24h": (int, float),
    "universe.max_spread_bps": (int, float),
    "universe.min_book_usdt": (int, float),
    "universe.min_atr_pct": (int, float),
    "universe.min_history_days": (int, float),
    "universe.rank_weights": dict,
    "universe.volatility_cap_atr_pct": (int, float),
    "strategy.internal_swing_threshold_atr": (int, float),
    "strategy.internal_swing_lookback": int,
    "strategy.value_area_pct": (int, float),
    "strategy.vp_bins": int,
    "strategy.vp_window_bars": int,
    "strategy.vp_touch_tol_pct": (int, float),
    "strategy.session.asia_sweep_filter": bool,
    "strategy.session.asia_hours_utc": list,
    "strategy.session.london_hours_utc": list,
    "strategy.session.prime_hours_utc": list,
    "strategy.session.trade_windows": list,
    "strategy.session.windows_utc": dict,
    "strategy.entry_models": list,
    "strategy.limit_entry_max_pct": (int, float),
    "strategy.limit_expiry_bars": int,
    "strategies.enabled": list,
    "strategies.breakout.range_bars": int,
    "strategies.breakout.range_pct": (int, float),
    "strategies.turtle.wick_frac": (int, float),
    "strategies.turtle.tol_pct": (int, float),
    "strategies.turtle.fresh_bars": int,
    "strategies.ict_sniper.timeframe": str,
    "strategies.ict_sniper.arm": int,
    "strategies.ict_sniper.swing_fresh_bars": int,
    "strategies.ict_sniper.sweep_max_age": int,
    "strategies.ict_sniper.mss_max_gap": int,
    "strategies.ict_sniper.wick_frac": (int, float),
    "strategies.ict_sniper.fvg_max_age": int,
    "strategies.ict_sniper.fvg_mitigation": str,
    "strategies.ict_sniper.entry_at": str,
    "strategies.ict_sniper.sl_mode": str,
    "strategies.ict_sniper.rr_min": (int, float),
    "strategies.ict_sniper.tp_min_bps": (int, float),
    "strategies.ict_sniper.tp_max_bps": (int, float),
    "strategies.ict_sniper.tp1_bps": (int, float),
    "strategies.ict_sniper.tp1_frac": (int, float),
    "strategies.ict_sniper.be_after_r": (int, float),
    "strategies.ict_sniper.be_to_r": (int, float),
    "strategies.ict_sniper.max_sl_bps": (int, float),
    "strategies.ict_sniper.sl_buffer_atr_mult": (int, float),
    "strategies.ict_sniper.time_exit_bars": int,
    "strategies.ict_sniper.retest_bars": int,
    "strategy.vp_touch_window_bars": int,
    "strategy.ob_max_age_bars": int,
    "strategy.fvg_max_age_bars": int,
    "stop.atr_buffer_mult": (int, float),
    "stop.min_sl_atr_mult": (int, float),
    "stop.max_sl_pct": (int, float),
    "stop.max_sl_atr_mult": (int, float),
    "stop.atr_period": int,
    "tp.model": str,
    "tp.fixed_r": (int, float),
    "tp.partial": dict,
    "tp.partial.runner_exit": str,
    "tp.structure.first_frac": (int, float),
    "tp.structure.runner_frac": (int, float),
    "tp.structure.runner_exit": str,
    "trailing.breakeven_after_tp1": bool,
    "trailing.trail_tf": str,
    "trailing.trail_arm": int,
    "trailing.trail_offset_atr_mult": (int, float),
    "trailing.only_after_tp1": bool,
    "risk.risk_per_trade": (int, float),
    "risk.leverage_cap": (int, float),
    "risk.max_positions": int,
    "risk.max_corr": (int, float),
    "risk.corr_window_bars": int,
    "risk.min_order_notional_usdt": (int, float),
    "daily.loss_limit_pct": (int, float),
    "daily.target_pct": (int, float),
    "daily.max_consecutive_losses": int,
    "daily.cooldown_after_losses": int,
    "daily.cooldown_minutes": (int, float),
    "daily.max_trades_per_day": int,
    "daily.day_start_hour_utc": int,
    "volatility_filter.enabled": bool,
    "volatility_filter.atr_zscore_max": (int, float),
    "volatility_filter.history_hours": int,
    "volatility_filter.spread_abnormal_bps": (int, float),
    "execution.fee_bps": (int, float),
    "execution.maker_fee_bps": (int, float),
    "execution.slippage_bps": (int, float),
    "execution.entry_at": str,
    "execution.exit_slippage_adverse": bool,
    "execution.intrabar_sl_first": bool,
    "execution.max_fee_r": (int, float),
    "paper.starting_equity": (int, float),
    "paper.poll_seconds": (int, float),
    "paper.day_start_hour_utc": int,
    "paper.feed_symbols": int,
    "paper.archive_days": int,
    "paper.rebuild_days": int,
    "goal.start_equity": (int, float),
    "goal.target_equity": (int, float),
    "goal.days": int,
    "app.port": int,
    "app.host": str,
    "app.session_hours": int,
    "app.certfile": str,
    "app.keyfile": str,
    "app.title": str,
    "backtest.warmup_15m_bars": int,
    "backtest.universe_rebalance": str,
    "walkforward.train_days": int,
    "walkforward.val_days": int,
    "walkforward.test_days": int,
    "research.log_all_scores": bool,
    "research.log_reject_min_legs": int,
    "research.outcome_window_bars": int,
}

_ENUMS = {
    "tp.model": ("A", "B", "C", "D"),
    "tp.structure.runner_exit": ("trail", "bos"),
    "execution.entry_at": ("next_open",),
    "backtest.universe_rebalance": ("daily",),
}


class ConfigError(ValueError):
    pass


def _leaf(d: dict, path: str):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _set_leaf(d: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = d
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def load_config(overrides: dict[str, Any] | None = None,
                extra_file: str | Path | None = None,
                extra_files: list[str | Path] | None = None) -> "Config":
    """Load defaults, apply extra YAML file(s) in order, then overrides.

    Override keys may be nested dicts OR dotted paths ("tp.model": "C")."""
    with open(DEFAULT_PATH) as fh:
        data = yaml.safe_load(fh)
    files: list[str | Path] = []
    if extra_file is not None:
        files.append(extra_file)
    if extra_files:
        files.extend(extra_files)
    for f in files:
        with open(f) as fh:
            extra = yaml.safe_load(fh) or {}
        _deep_merge(data, extra)
    if overrides:
        _deep_merge(data, copy.deepcopy(overrides))
    return Config(data)


def _deep_merge(base: dict, extra: dict) -> None:
    for k, v in extra.items():
        if "." in k:
            parts = k.split(".")
            cur = base
            for part in parts[:-1]:
                nxt = cur.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    cur[part] = nxt
                cur = nxt
            if isinstance(v, dict) and isinstance(cur.get(parts[-1]), dict):
                _deep_merge(cur[parts[-1]], v)
            else:
                cur[parts[-1]] = copy.deepcopy(v)
        elif isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = copy.deepcopy(v)


class Config:
    """Validated, frozen-feeling config.  Attribute + item access both work."""

    def __init__(self, data: dict):
        self._data = data
        self._validate()

    # -- validation -----------------------------------------------------
    def _validate(self) -> None:
        for path, typ in _SCHEMA.items():
            val = _leaf(self._data, path)
            if val is None:
                raise ConfigError(f"config missing required key: {path}")
            if not isinstance(val, typ):
                raise ConfigError(
                    f"config {path} must be {typ}, got {type(val).__name__}")
        for path, allowed in _ENUMS.items():
            val = _leaf(self._data, path)
            if val not in allowed:
                raise ConfigError(f"config {path} must be one of {allowed}, got {val!r}")
        d = self._data
        for m in d["strategy"]["entry_models"]:
            if m not in ("order_block", "fvg", "micro_poc"):
                raise ConfigError(f"unknown entry model {m!r}")
        if not (0 < d["strategy"]["value_area_pct"] < 1):
            raise ConfigError("strategy.value_area_pct must be in (0, 1)")
        fr = d["tp"]["structure"]["first_frac"] + d["tp"]["structure"]["runner_frac"]
        if abs(fr - 1.0) > 1e-9:
            raise ConfigError("tp.structure fractions must sum to 1.0")
        p = d["tp"]["partial"]
        fracs = p["tp1_frac"] + p["tp2_frac"] + p["runner_frac"]
        if abs(fracs - 1.0) > 1e-9:
            raise ConfigError("tp.partial fractions must sum to 1.0")
        w = d["universe"]["rank_weights"]
        if abs(sum(w.values()) - 1.0) > 1e-9:
            raise ConfigError("universe.rank_weights must sum to 1.0")

    # -- access ----------------------------------------------------------
    def raw(self) -> dict:
        return copy.deepcopy(self._data)

    def get(self, path: str, default: Any = None) -> Any:
        val = _leaf(self._data, path)
        return default if val is None else val

    def __getitem__(self, key: str):
        return self._data[key]

    def __getattr__(self, name: str):
        if name in self._data:
            val = self._data[name]
            return val
        raise AttributeError(name)

    def __repr__(self) -> str:
        return f"Config({self._data!r})"
