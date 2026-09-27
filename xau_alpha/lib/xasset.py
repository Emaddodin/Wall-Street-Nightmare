"""Aligned 1-minute cross-asset frame: Dukascopy XAUUSD mid + Binance XAUUSDT/XAGUSDT perps + EURUSDT spot."""
import glob, zipfile, io
from pathlib import Path
import numpy as np, pandas as pd
from data import load_m1

XA = Path(__file__).resolve().parents[1] / "data/xa"


def _klines(sym):
    parts = []
    for f in sorted(glob.glob(str(XA / f"{sym}-1m-*.zip"))):
        with zipfile.ZipFile(f) as z:
            raw = z.read(z.namelist()[0]).decode()
        first = raw.split("\n", 1)[0]
        df = pd.read_csv(io.StringIO(raw), header=0 if first.startswith("open_time") else None)
        df = df.iloc[:, :6]
        df.columns = ["t", "o", "h", "l", "c", "v"]
        parts.append(df)
    df = pd.concat(parts)
    df["t"] = df["t"].astype(np.int64)
    df.loc[df.t > 10**14, "t"] //= 1000          # some 2025+ spot files use microseconds
    return df.drop_duplicates("t").set_index("t").sort_index()


def frame():
    m = load_m1()
    out = pd.DataFrame({"ts": m.ts.values, "xau": m.c.values, "split": m.split.values, "dow": m.dow.values,
                        "mod": m["mod"].values}).set_index("ts")
    for sym, col in (("XAUUSDT", "perp"), ("XAGUSDT", "xag"), ("EURUSDT", "eur")):
        k = _klines(sym)
        out[col] = k["c"].reindex(out.index)
    return out
