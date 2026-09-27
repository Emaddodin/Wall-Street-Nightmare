"""
xau_alpha/news/replay.py
Historical replay of the production news guards and the rule-fallback oracle, so they can be backtested.

Two layers
  1. Vectorised freeze masks (fast, numpy) that re-implement PoliticianBrain.check_calendar_freeze exactly:
       HIGH   event T: frozen for now in [T - window, T + 5 min]   (window = 15 in Apex and inside the oracle,
                                                                   5 in PoliticianBrain.is_entry_allowed / Ghost)
       MEDIUM event T: frozen for now in [T - 5, T + 5 min]
       HIGH   event T: "post-news expansion" for now in (T + 5, T + 45 min]  (Apex lifts its session filter,
                                                                               Politician boosts BUY size x1.5, TP x2)
     plus the offline fallback (used only when the FairEconomy feed is empty): Thursday 08:30 NY claims
     frozen [T-10, T+5], post (T+5, T+45]; first Friday (day <= 7) 08:30 NY NFP frozen [T-20, T+5], post (T+5, T+60].
     `parity_check()` proves equality with the unmodified production function under a fake clock.
  2. ProductionGuardReplay: calls the unmodified production objects (PoliticianBrain, LayaOracle rule fallback,
     RegimePriorEngine, TradeJournalRAG) with a fake clock, a historical calendar and a fixed headline state.

Not replayable (documented in recon/news_llm.md): the live Google-News/ForexLive headline state (the feeds keep no
point-in-time archive; the code also stamps every headline with the fetch time), the exact FairEconomy impact labels
(our calendar is a subset of FF's USD High/Medium list), and the Laya / Jeff model outputs (no weights here).
"""
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CAL = HERE.parent / "data/econ_calendar.csv"
NY = ZoneInfo("America/New_York")
MIN = 60_000


def load_cal(path=CAL) -> pd.DataFrame:
    cal = pd.read_csv(path)
    cal["ts"] = pd.to_datetime(cal["ts_utc"], utc=True).astype("int64") // 1_000_000
    return cal.sort_values("ts").reset_index(drop=True)


# ----------------------------------------------------------------------------- vectorised masks
def _interval_mask(t: np.ndarray, ev: np.ndarray, lo_min: float, hi_min: float, lo_open=False, hi_open=False):
    """True where some event e has t in [e+lo, e+hi] (open ends optional). t, ev in ms."""
    if len(ev) == 0:
        return np.zeros(len(t), dtype=bool)
    ev = np.sort(ev)
    lo, hi = lo_min * MIN, hi_min * MIN
    # candidate events: e in [t-hi, t-lo]
    a = np.searchsorted(ev, t - hi, side="left" if not hi_open else "right")
    b = np.searchsorted(ev, t - lo, side="right" if not lo_open else "left")
    return b > a


def calendar_masks(t_ms, cal: pd.DataFrame, window_minutes=15) -> dict:
    """Production semantics with a (FairEconomy-like) calendar available. t_ms = decision times (ms UTC)."""
    t = np.asarray(t_ms, dtype=np.int64)
    hi_ev = cal.loc[cal["impact"] == "HIGH", "ts"].values.astype(np.int64)
    md_ev = cal.loc[cal["impact"] == "MEDIUM", "ts"].values.astype(np.int64)
    frozen = _interval_mask(t, hi_ev, -window_minutes, 5) | _interval_mask(t, md_ev, -5, 5)
    post = _interval_mask(t, hi_ev, 5, 45, lo_open=True) & ~frozen
    return {"frozen": frozen, "post": post}


def fallback_masks(t_ms) -> dict:
    """Production semantics when the FairEconomy calendar is unavailable (deterministic NY schedule)."""
    t = np.asarray(t_ms, dtype=np.int64)
    dt = pd.to_datetime(t, unit="ms", utc=True).tz_convert(NY)
    # 08:30 NY on the same NY calendar day
    tgt = (dt.normalize() + pd.Timedelta(hours=8, minutes=30))
    diff = (tgt.asi8 // 1_000_000 - t) / MIN          # minutes until 08:30 (positive before)
    thu = dt.dayofweek == 3
    ffri = (dt.dayofweek == 4) & (dt.day <= 7)
    frozen = (thu & (diff >= -5) & (diff <= 10)) | (ffri & (diff >= -5) & (diff <= 20))
    post = ((thu & (diff >= -45) & (diff < -5)) | (ffri & (diff >= -60) & (diff < -5))) & ~frozen
    return {"frozen": np.asarray(frozen), "post": np.asarray(post)}


def ff_week_events(cal: pd.DataFrame, now_ms: int) -> pd.DataFrame:
    """Events of the FairEconomy 'this week' file containing now (weeks run Sunday..Saturday, New-York dates)."""
    d = pd.Timestamp(now_ms, unit="ms", tz="UTC").tz_convert(NY)
    start = (d.normalize() - pd.Timedelta(days=(d.dayofweek + 1) % 7))
    end = start + pd.Timedelta(days=7)
    s, e = start.value // 1_000_000, end.value // 1_000_000
    return cal[(cal["ts"] >= s) & (cal["ts"] < e)]


def production_masks(t_ms, cal: pd.DataFrame, window_minutes=15) -> dict:
    """Exact production result with a weekly FF-style feed: calendar rule, plus the fallback schedule whenever the
    week has no event at or after now-60 min (production's `active_events` test)."""
    t = np.asarray(t_ms, dtype=np.int64)
    cm = calendar_masks(t, cal, window_minutes)
    fb = fallback_masks(t)
    d = pd.to_datetime(t, unit="ms", utc=True).tz_convert(NY)
    wk_start = (d.normalize() - pd.to_timedelta((d.dayofweek + 1) % 7, unit="D")).asi8 // 1_000_000
    wk_end = wk_start + 7 * 24 * 60 * MIN
    ev = np.sort(cal["ts"].values.astype(np.int64))
    # any event with ts in [max(t-60min, wk_start), wk_end) ?
    lo = np.maximum(t - 60 * MIN, wk_start)
    active = np.searchsorted(ev, wk_end, side="left") > np.searchsorted(ev, lo, side="left")
    frozen = cm["frozen"] | (~active & fb["frozen"])
    post = (cm["post"] | (~active & fb["post"])) & ~frozen
    return {"frozen": frozen, "post": post, "active_feed": active}


# ----------------------------------------------------------------------------- production objects, fake clock
_clock = {"now": datetime(2025, 1, 1, tzinfo=timezone.utc)}


class _FakeDT(datetime):
    @classmethod
    def now(cls, tz=None):
        n = _clock["now"]
        return n.astimezone(tz) if tz else n.replace(tzinfo=None)


def _import_production():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import os
    os.environ.setdefault("JEFF_SKIP_HEAVY_WEIGHTS", "1")
    import scalper.brain.politician_brain as pb
    pb.datetime = _FakeDT             # production module reads datetime.now(timezone.utc) -> replay clock
    return pb


def _ff_events(cal_week: pd.DataFrame):
    out = []
    for _, r in cal_week.iterrows():
        ts = pd.Timestamp(r["ts"], unit="ms", tz="UTC")
        out.append({"title": r["event"], "country": "USD", "impact": r["impact"].title(),
                    "date": ts.tz_convert(NY).isoformat(), "epoch": ts.value / 1e9})
    return out


def make_brain(pb, state="offline_seed"):
    """A PoliticianBrain without its network threads, with a fixed headline state."""
    b = pb.PoliticianBrain.__new__(pb.PoliticianBrain)
    b._lock = threading.Lock()
    b._cached_calendar_events, b._cached_headlines = [], []
    b.regime_stats, b._running, b._last_update_ts = {}, False, 0.0
    b._active_headline = "replay"
    states = {   # (regime, bias, heat)
        "offline_seed": (pb.PoliticalRegime.TRADE_WAR_TARIFFS, pb.MacroBias.STRONG_BULL, 67.5),
        "neutral": (pb.PoliticalRegime.NEUTRAL_CHOP, pb.MacroBias.NEUTRAL, 40.0),
        "strong_bear": (pb.PoliticalRegime.HAWKISH_DOLLAR_SURGE, pb.MacroBias.STRONG_BEAR, 70.0),
    }
    b._current_regime, b._current_bias, b._geopolitical_heat_index = states[state]
    return b


def parity_check(n_random=4000, seed=7, cal: pd.DataFrame = None) -> dict:
    """Compare production check_calendar_freeze (fake clock) with production_masks / fallback_masks."""
    cal = load_cal() if cal is None else cal
    pb = _import_production()
    b = make_brain(pb)
    rng = np.random.default_rng(seed)
    lo = int(pd.Timestamp("2025-01-06", tz="UTC").value // 1_000_000)
    hi = int(pd.Timestamp("2026-09-26", tz="UTC").value // 1_000_000)
    near = cal["ts"].values[rng.integers(0, len(cal), n_random)] + rng.integers(-70, 70, n_random) * MIN
    thu = rng.integers(lo, hi, n_random) // MIN * MIN
    t = np.r_[rng.integers(lo, hi, n_random) // MIN * MIN, near, thu]
    t = t + rng.integers(0, 60, len(t)) * 1000          # arbitrary seconds, too
    out = {}
    for mode in ("calendar", "offline"):
        vec = production_masks(t, cal) if mode == "calendar" else fallback_masks(t)
        mism = 0
        for i, ti in enumerate(t):
            _clock["now"] = datetime.fromtimestamp(ti / 1000, tz=timezone.utc)
            b._cached_calendar_events = _ff_events(ff_week_events(cal, ti)) if mode == "calendar" else []
            fz, _, post = b.check_calendar_freeze(window_minutes=15)
            if bool(fz) != bool(vec["frozen"][i]) or bool(post) != bool(vec["post"][i]):
                mism += 1
        out[mode] = {"n": int(len(t)), "mismatches": mism,
                     "frozen_share": round(float(vec["frozen"].mean()), 4), "post_share": round(float(vec["post"].mean()), 4)}
    return out


class ProductionGuardReplay:
    """Replays Ghost-engine entry guards (PoliticianBrain.is_entry_allowed + LayaOracle.evaluate_setup rule fallback)
    for historical signals. `state` fixes the (non-replayable) headline state."""

    def __init__(self, cal: pd.DataFrame = None, state="offline_seed", feed="calendar"):
        self.cal = load_cal() if cal is None else cal
        self.pb = _import_production()
        import scalper.brain.laya_oracle as lo
        self.brain = make_brain(self.pb, state)
        self.feed = feed
        orc = lo.LayaOracle.__new__(lo.LayaOracle)
        from scalper.brain.macro_watchdog import MacroWatchdog
        from scalper.brain.regime_prior_engine import RegimePriorEngine
        from scalper.brain.trade_journal_rag import get_trade_journal_rag
        orc.model_id, orc._agent, orc._is_ready, orc._loading = "replay", None, False, False
        orc._last_decision, orc._decision_lock = None, threading.Lock()
        orc.rag = None
        orc.watchdog = MacroWatchdog()        # production never registers events -> always SAFE
        orc.regime = RegimePriorEngine()
        orc.politician = self.brain
        orc.trade_rag = get_trade_journal_rag()
        self.oracle = orc

    def ghost_verdict(self, t_ms: int, direction: str, wick_ratio=0.6, setup="BREAKOUT_RETEST", trend_aligned=True,
                      entry_price=0.0, sl_price=0.0):
        now = datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc)
        _clock["now"] = now
        self.brain._cached_calendar_events = _ff_events(ff_week_events(self.cal, t_ms)) if self.feed == "calendar" else []
        ok, reason = self.brain.is_entry_allowed()
        if not ok:
            return {"allowed": False, "why": "politician_freeze_5m"}
        d = self.oracle.evaluate_setup_sync({"direction": direction, "entry_price": entry_price, "sl_price": sl_price,
                                             "wick_ratio": wick_ratio, "setup_type": setup,
                                             "trend_aligned": trend_aligned, "hour_utc": now.hour})
        if not d.is_valid:
            return {"allowed": False, "why": d.matched_ict_concepts[0], "note": d.regime_notes[:80]}
        return {"allowed": True, "why": "", "size_mult": d.compounding_multiplier, "tp_mult": d.tp_expansion_multiplier,
                "grade": d.setup_grade}


if __name__ == "__main__":
    print(parity_check(n_random=1500))
