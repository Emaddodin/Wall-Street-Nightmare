"""Strategy S6 -- SELECTIVE Q-FADE: 1m-signal maker fade with a full
causal feature vector per event, dynamic limit depth, and optional
exhaustion-confirmation gates.

The strategy is the SELECTOR: it emits every candidate event with its
feature meta; the analysis layer (tools/event_analysis.py) ranks, scores,
and slices events into selectivity levels.  A frozen composite score can
also be applied here via score_expr so the engine itself can enforce
min_score thresholds in re-runs.

Every feature in meta is computable at the signal bar close with no
future data.  Execution is unchanged from the honest S5 standard:
1m intrabar fills/exits, TP deferred on the fill bar, SL allowed.

params:
  lookback, vol_win, min_z         signal definition (as S5)
  confirm: none|bar|wick|both      exhaustion confirmation on signal bar
  delta_mode: fixed|vol            limit depth
  limit_bps / delta_k / delta_min / delta_max
  tp_bps, sl_bps, trail_bps, max_bars (1m bars), alloc, lev,
  limit_wait (1m bars)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant.strategies.base import Strategy
from quant.lib import store


class SelectiveFade(Strategy):
    name = "s6_selective_fade"

    def __init__(self, lookback: int = 15, vol_win: int = 1440,
                 min_z: float = 1.5, confirm: str = "none",
                 delta_mode: str = "fixed", limit_bps: float = 50.0,
                 delta_k: float = 1.5, delta_min: float = 30.0,
                 delta_max: float = 120.0, tp_bps: float = 80.0,
                 sl_bps: float = 60.0, trail_bps: float | None = None,
                 max_bars: int = 60, alloc: float = 0.25,
                 lev: float = 20.0, limit_wait: int = 30,
                 score_expr: str | None = None,
                 with_derivatives: bool = True):
        super().__init__()
        self.p = dict(lookback=lookback, vol_win=vol_win, min_z=min_z,
                      confirm=confirm, delta_mode=delta_mode,
                      limit_bps=limit_bps, delta_k=delta_k,
                      delta_min=delta_min, delta_max=delta_max,
                      tp_bps=tp_bps, sl_bps=sl_bps, trail_bps=trail_bps,
                      max_bars=max_bars, alloc=alloc, lev=lev,
                      limit_wait=limit_wait, score_expr=score_expr,
                      with_derivatives=with_derivatives)

    # ------------------------------------------------------------------
    def _symbol_features(self, df: pd.DataFrame) -> dict[str, np.ndarray]:
        p = self.p
        c = df["close"].to_numpy(dtype=float)
        h = df["high"].to_numpy(dtype=float)
        l = df["low"].to_numpy(dtype=float)
        o = df["open"].to_numpy(dtype=float)
        v = df["volume"].to_numpy(dtype=float)
        t = df["open_time"].to_numpy(dtype=np.int64)
        n = len(c)
        out = {"t": t}
        r1 = np.full(n, np.nan)
        r1[1:] = np.log(c[1:] / c[:-1])
        lb = p["lookback"]
        r3 = np.full(n, np.nan)
        r3[lb:] = np.log(c[lb:] / c[:-lb])
        for k in (5, 15, 60):
            rr = np.full(n, np.nan)
            rr[k:] = np.log(c[k:] / c[:-k])
            out[f"r{k}"] = rr
        out["r1"] = r1
        out["r3"] = r3
        sd = pd.Series(r1).rolling(p["vol_win"]).std().to_numpy()
        out["rv"] = sd                              # 1m-bar vol
        z = r3 / np.maximum(sd * np.sqrt(lb), 1e-9)
        out["z"] = z
        # time-series extremity: |z| vs its own recent distribution
        zsd = pd.Series(z).rolling(p["vol_win"]).std().to_numpy()
        out["z_time"] = np.abs(z) / np.maximum(zsd, 1e-9)
        # volatility state
        rv5 = pd.Series(r1).rolling(5).std().to_numpy() * np.sqrt(5)
        rv60 = pd.Series(r1).rolling(60).std().to_numpy() * np.sqrt(60)
        out["rv5"] = rv5
        out["rv60"] = rv60
        out["vol_accel"] = rv5 / np.maximum(rv60, 1e-9)
        # volume
        vma = pd.Series(v).rolling(p["vol_win"]).mean().to_numpy()
        out["rvol"] = v / np.maximum(vma, 1e-9)
        out["rvol_accel"] = out["rvol"] / np.maximum(
            pd.Series(out["rvol"]).rolling(5).mean().to_numpy(), 1e-9)
        # position within extremes
        out["hi_dist60"] = np.log(np.maximum(c, 1e-12)) - np.log(
            np.maximum(pd.Series(h).rolling(60).max().to_numpy(), 1e-12))
        out["lo_dist60"] = np.log(np.maximum(c, 1e-12)) - np.log(
            np.maximum(pd.Series(l).rolling(60).min().to_numpy(), 1e-12))
        out["hi_dist240"] = np.log(np.maximum(c, 1e-12)) - np.log(
            np.maximum(pd.Series(h).rolling(240).max().to_numpy(), 1e-12))
        out["lo_dist240"] = np.log(np.maximum(c, 1e-12)) - np.log(
            np.maximum(pd.Series(l).rolling(240).min().to_numpy(), 1e-12))
        # candle anatomy (signal bar)
        rng = np.maximum(h - l, 1e-12)
        out["range_frac"] = rng / np.maximum(c, 1e-12)
        out["body_frac"] = np.abs(c - o) / rng
        out["wick_up"] = (h - np.maximum(c, o)) / rng
        out["wick_dn"] = (np.minimum(c, o) - l) / rng
        out["close_dir"] = np.sign(c - o)
        return out

    # ------------------------------------------------------------------
    def _btc_join(self, frames: dict[str, pd.DataFrame], feat: dict,
                  times: np.ndarray) -> dict[str, np.ndarray]:
        """Attach BTC same-bar returns (causal: same-timestamp closes)."""
        btc_r = {"btc_r5": np.full(len(times), np.nan),
                 "btc_r15": np.full(len(times), np.nan),
                 "btc_rv": np.full(len(times), np.nan)}
        if "BTCUSDT" not in frames:
            return btc_r
        bdf = frames["BTCUSDT"]
        bc = bdf["close"].to_numpy(dtype=float)
        bt = bdf["open_time"].to_numpy(dtype=np.int64)
        b5 = np.full(len(bc), np.nan)
        b5[5:] = np.log(bc[5:] / bc[:-5])
        b15 = np.full(len(bc), np.nan)
        b15[15:] = np.log(bc[15:] / bc[:-15])
        br1 = np.full(len(bc), np.nan)
        br1[1:] = np.log(bc[1:] / bc[:-1])
        brv = pd.Series(br1).rolling(60).std().to_numpy() * np.sqrt(60)
        idx = np.searchsorted(bt, times, side="right") - 1
        ok = (idx >= 0) & (bt[np.minimum(idx, len(bt) - 1)] == times)
        btc_r["btc_r5"][ok] = b5[idx[ok]]
        btc_r["btc_r15"][ok] = b15[idx[ok]]
        btc_r["btc_rv"][ok] = brv[idx[ok]]
        return btc_r

    # ------------------------------------------------------------------
    def _deriv_join(self, sym: str, feat: dict, times: np.ndarray,
                    n: int) -> None:
        """Attach 5m metrics + funding (causal: previous completed
        bucket / last mark <= t)."""
        for key in ("oi_chg", "taker_imb", "funding_ann", "lsr"):
            feat[key] = np.full(n, np.nan)
        try:
            m = store.load_metrics(sym)
        except FileNotFoundError:
            m = None
        if m is not None and len(m):
            mt = m["create_time"].astype("int64").to_numpy() // 10**9 * 1000
            order = np.argsort(mt)
            mt = mt[order]
            oi = m["sum_open_interest"].to_numpy()[order]
            imb = m["sum_taker_long_short_vol_ratio"].to_numpy()[order]
            lsr = m["count_long_short_ratio"].to_numpy()[order]
            idx = np.searchsorted(mt, times - 300_000,
                                  side="right") - 1
            ok = idx >= 0
            oi_prev = np.full(n, np.nan)
            oi_prev[ok] = oi[np.minimum(idx[ok], len(oi) - 1)]
            oi_chg = np.full(n, np.nan)
            oi_chg[1:] = np.log(np.maximum(oi_prev[1:], 1e-12)) - np.log(
                np.maximum(oi_prev[:-1], 1e-12))
            feat["oi_chg"] = oi_chg
            feat["taker_imb"][ok] = imb[np.minimum(idx[ok], len(imb) - 1)]
            feat["lsr"][ok] = lsr[np.minimum(idx[ok], len(lsr) - 1)]
        try:
            f = store.load_funding(sym)
        except FileNotFoundError:
            f = None
        if f is not None and len(f):
            ft = f["calc_time"].to_numpy(dtype=np.int64)
            fr = f["last_funding_rate"].to_numpy(dtype=float)
            idx = np.searchsorted(ft, times, side="right") - 1
            ok = idx >= 0
            feat["funding_ann"][ok] = fr[np.minimum(
                idx[ok], len(fr) - 1)] * 3 * 365 * 100

    # ------------------------------------------------------------------
    def events(self, frames: dict[str, pd.DataFrame]) -> dict[int, list]:
        p = self.p
        ex = self.exit_model()
        events: dict[int, list] = {}
        side_frames: list[pd.DataFrame] = []
        import time as _time
        for sym, df in frames.items():
            if sym == "BTCUSDT":
                continue
            feat = self._symbol_features(df)
            t = feat["t"]
            n = len(t)
            btc_r = self._btc_join(frames, feat, t)
            feat.update(btc_r)
            if p["with_derivatives"]:
                self._deriv_join(sym, feat, t, n)
            z = feat["z"]
            r3 = feat["r3"]
            # score array (vectorized expression over feature arrays)
            if p["score_expr"]:
                env = {"np": np}
                env.update({k: feat[k] for k in feat if k != "t"})
                score = eval(p["score_expr"], {"__builtins__": {}}, env)
                score = np.nan_to_num(score, nan=0.0)
            else:
                score = np.abs(z)
            feat["score_val"] = score
            ok = np.isfinite(z) & np.isfinite(r3)
            short_m = ok & (z >= p["min_z"]) & (r3 > 0)
            long_m = ok & (z <= -p["min_z"]) & (r3 < 0)
            # exhaustion confirmation (signal bar shape)
            if p["confirm"] in ("bar", "both"):
                # fade short (up move): signal bar must close down
                short_m &= feat["close_dir"] < 0
                long_m &= feat["close_dir"] > 0
            if p["confirm"] in ("wick", "both"):
                short_m &= feat["wick_up"] > 0.4
                long_m &= feat["wick_dn"] > 0.4
            rows: list[dict] = []
            for i in np.nonzero(short_m)[0]:
                meta = self._meta_for(feat, i)
                side = -1
                events.setdefault(int(t[i]), []).append(
                    (sym, side, float(meta["score"]), ex, meta))
                rows.append(self._side_row(sym, int(t[i]), side, feat, i))
            for i in np.nonzero(long_m)[0]:
                meta = self._meta_for(feat, i)
                side = 1
                events.setdefault(int(t[i]), []).append(
                    (sym, side, float(meta["score"]), ex, meta))
                rows.append(self._side_row(sym, int(t[i]), side, feat, i))
            if rows:
                side_frames.append(pd.DataFrame(rows))
        if side_frames:
            out_path = (store.data_root() / "research" /
                        f"s6_events_{int(_time.time())}.parquet")
            pd.concat(side_frames, ignore_index=True).to_parquet(
                out_path, index=False)
            self.events_out = str(out_path)
        return events

    def _meta_for(self, feat: dict, i: int) -> dict:
        p = self.p
        # limit depth
        if p["delta_mode"] == "vol":
            delta = float(np.clip(p["delta_k"] * feat["rv5"][i] * 1e4,
                                  p["delta_min"], p["delta_max"]))
        else:
            delta = p["limit_bps"]
        meta = {"alloc": p["alloc"], "lev": p["lev"],
                "limit_bps": round(delta, 1),
                "limit_wait_bars": p["limit_wait"],
                "score": float(feat["score_val"][i])}
        return meta

    def _side_row(self, sym: str, signal_t: int, side: int,
                  feat: dict, i: int) -> dict:
        row = {"symbol": sym, "signal_t": signal_t, "side": side,
               "score": float(feat["score_val"][i])}
        for key in ("z", "z_time", "r1", "r3", "r5", "r15", "r60", "rv",
                    "rv5", "rv60", "vol_accel", "rvol", "rvol_accel",
                    "hi_dist60", "lo_dist60", "hi_dist240", "lo_dist240",
                    "range_frac", "body_frac", "wick_up", "wick_dn",
                    "close_dir", "btc_r5", "btc_r15", "btc_rv", "oi_chg",
                    "taker_imb", "lsr", "funding_ann"):
            v = feat.get(key)
            if v is not None and np.isfinite(v[i]):
                row[key] = float(v[i])
        return row

    def exit_model(self) -> dict:
        return {"tp_bps": self.p["tp_bps"], "sl_bps": self.p["sl_bps"],
                "trail_bps": self.p["trail_bps"],
                "max_bars": self.p["max_bars"]}
