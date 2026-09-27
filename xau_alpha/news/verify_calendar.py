"""
xau_alpha/news/verify_calendar.py
Check every calendar timestamp against the real XAUUSD price reaction in Dukascopy M1 bid/ask bars
(xau_alpha/data/m1_ba.parquet, built from data/candles/duka_raw ticks by lib/build_m1.py).

Statistic per event at minute T (UTC):
  r(T)      = mid high - mid low of the M1 bar that opens at T
  base(T)   = median r at the same UTC minute-of-day over the previous 20 weekdays that have a bar there and have
              no calendar event within +-15 min of that minute (quiet baseline)
  ratio     = r(T) / base(T)
  MATCH     = ratio >= 2.0  AND  r(T) > max r over [T-10, T-1]      (a big range AT the release minute)
Placebos (same rule, same events): T-60 min and T+60 min (a US-DST mistake would put the spike there), and T+7 min.
Rows where the market is closed / bar missing are reported as 'no_bar'.

Outputs: data/econ_calendar_verify.csv and a summary printed + written to news/out/verify_summary.json
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
MIN = 60_000


def main(ratio_thr=2.0):
    cal = pd.read_csv(DATA / "econ_calendar_ext.csv")
    cal["ts"] = pd.to_datetime(cal["ts_utc"], utc=True).astype("int64") // 1_000_000
    m1 = pd.read_parquet(DATA / "m1_ba.parquet", columns=["ts", "bh", "bl", "ah", "al", "spr", "spr_max", "n"])
    ts = m1["ts"].values.astype(np.int64)
    rng = ((m1["bh"].values + m1["ah"].values) - (m1["bl"].values + m1["al"].values)) / 2.0
    sprmax = m1["spr_max"].values
    t0, t1 = ts[0], ts[-1]
    pos = pd.Series(np.arange(len(ts)), index=ts)

    ev_all = np.sort(cal["ts"].values)

    def r_at(t):
        i = pos.get(t)
        return (np.nan, np.nan) if i is None else (rng[i], sprmax[i])

    def near_event(t, tol=15 * MIN):
        j = np.searchsorted(ev_all, t - tol)
        return j < len(ev_all) and ev_all[j] <= t + tol

    def baseline(t, k=20):
        vals, d, day = [], 1, 86_400_000
        while len(vals) < k and d < 60:
            tt = t - d * day
            wd = pd.Timestamp(tt, unit="ms", tz="UTC").dayofweek
            d += 1
            if wd >= 5 or near_event(tt):
                continue
            v, _ = r_at(tt)
            if not np.isnan(v):
                vals.append(v)
        return np.median(vals) if len(vals) >= 5 else np.nan

    def test(t):
        r, sm = r_at(t)
        if np.isnan(r):
            return {"r": np.nan, "base": np.nan, "ratio": np.nan, "pre_max": np.nan, "match": None, "spr_max": np.nan}
        b = baseline(t)
        pre = [r_at(t - k * MIN)[0] for k in range(1, 11)]
        pre = [p for p in pre if not np.isnan(p)]
        pmax = max(pre) if pre else 0.0
        ratio = r / b if b and not np.isnan(b) and b > 0 else np.nan
        m = bool(ratio >= ratio_thr and r > pmax) if not np.isnan(ratio) else None
        return {"r": round(r, 3), "base": round(b, 3), "ratio": round(ratio, 2), "pre_max": round(pmax, 3),
                "match": m, "spr_max": round(sm, 3)}

    rows = []
    for _, e in cal.iterrows():
        t = int(e["ts"])
        if t < t0 or t > t1:
            continue
        rec = {"ts_utc": e["ts_utc"], "type": e["type"], "impact": e["impact"], "event": e["event"]}
        rec.update(test(t))
        for tag, off in (("m60", -60), ("p60", 60), ("p7", 7)):
            x = test(t + off * MIN)
            rec[f"{tag}_ratio"], rec[f"{tag}_match"] = x["ratio"], x["match"]
        rows.append(rec)
    v = pd.DataFrame(rows)
    v.to_csv(DATA / "econ_calendar_verify.csv", index=False)

    def rate(df, col="match"):
        s = df[col].dropna().astype(bool)
        return {"n": int(len(s)), "match": int(s.sum()), "rate": round(float(s.mean()), 3) if len(s) else None}

    # one row per (minute) for the headline rate: several releases often share a minute (e.g. CPI + claims)
    hi = v[v["impact"] == "HIGH"].drop_duplicates("ts_utc")
    summ = {
        "rule": f"ratio>={ratio_thr} vs quiet same-minute median of prior 20 weekdays AND r(T) > max r(T-10..T-1)",
        "events_in_data_range": int(len(v)), "no_bar": int(v["match"].isna().sum()),
        "HIGH_unique_minutes": rate(hi), "HIGH_placebo_T-60": rate(hi, "m60_match"),
        "HIGH_placebo_T+60": rate(hi, "p60_match"), "HIGH_placebo_T+7": rate(hi, "p7_match"),
        "HIGH_T_beats_T-60_and_T+60": {
            "n": int(hi[["ratio", "m60_ratio", "p60_ratio"]].dropna().shape[0]),
            "rate": round(float((hi["ratio"] > hi[["m60_ratio", "p60_ratio"]].max(axis=1)).loc[
                hi[["ratio", "m60_ratio", "p60_ratio"]].dropna().index].mean()), 3)},
        "MEDIUM_all": rate(v[v["impact"] == "MEDIUM"]),
        "claims_alone_minutes": rate(v[(v["type"] == "CLAIMS") & ~v["ts_utc"].isin(hi["ts_utc"])]),
        "by_type": {t: rate(g) for t, g in v.groupby("type")},
        "median_ratio_by_type": v.groupby("type")["ratio"].median().round(2).to_dict(),
        "median_spr_max_by_type": v.groupby("type")["spr_max"].median().round(3).to_dict(),
    }
    (HERE / "out").mkdir(exist_ok=True)
    (HERE / "out/verify_summary.json").write_text(json.dumps(summ, indent=1))
    print(json.dumps(summ, indent=1))
    miss = v[(v["impact"] == "HIGH") & (v["match"] == False)]  # noqa: E712
    print("HIGH non-matches:\n", miss[["ts_utc", "type", "r", "base", "ratio", "pre_max", "m60_ratio", "p60_ratio"]].to_string())


if __name__ == "__main__":
    main()
