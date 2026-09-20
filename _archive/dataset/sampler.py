#!/usr/bin/env python3
"""
The million-sample market: every situation, made familiar.

The goal is a robot that has SEEN everything before it meets it. Real
records are scarce (11k triangle signals), so this generates millions of
samples the honest way:

  - the FEATURES are drawn from the real signals' own joint distribution
    (means, correlations, and the observed ranges -- plus uniform boundary
    coverage so the whole box is familiar, not just the dense middle)
  - the POST-SIGNAL PATHS are real: bootstrapped from the 10,702 recorded
    fav/adv paths in outcomes.jsonl, flipped when the sample's side is
    opposite the recorded one (the only assumption: a path's shape is
    side-symmetric)
  - the LABEL is computed by the engine's own walk_path (+5% / -1.25%,
    spanning-bar-is-a-stop), exactly as the book and the dataset do

Nothing trades; nothing is invented about the market's behaviour. Only the
pairing of features to paths is synthetic, which is precisely what lets the
model meet ten million situations that never happened to happen together.

Outputs (deterministic, seeded):
  data/dataset/samples/features.npz   X float32 (n, 13), y int8, reason,
                                      bars, provenance flag
  data/dataset/samples/manifest.json  counts, ranges, seed, timing

    python3 dataset/sampler.py [--n 10000000] [--chunk 1000000]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dataset import sources  # noqa: E402
from dataset.selector import FEATURES  # noqa: E402
from dataset.make_dataset import measures_for  # noqa: E402

OUT = ROOT / "data" / "dataset" / "samples"
CONT = ["atr", "trend", "vol20", "mom6h", "mom1h", "volx", "score", "hour"]
CAT = ["agents", "tier", "who", "counter", "side"]


def label_paths(fav: np.ndarray, adv: np.ndarray, flip: np.ndarray,
                tp: float = 5.0, sl: float = 1.25) -> tuple[np.ndarray,
                                                            np.ndarray]:
    """Vectorised walk_path: 1 target, 2 stop, 0 open -- stop first in a
    bar that spans both, the repo's own convention."""
    n, T = fav.shape
    up = ~flip
    hi = np.where(up[:, None], 1 + fav / 100.0, 1 + adv / 100.0)
    lo = np.where(up[:, None], 1 - adv / 100.0, 1 - fav / 100.0)
    tp_px, sl_px = 1 + tp / 100.0, 1 - sl / 100.0
    first_tp = np.full(n, T, dtype=np.int32)
    first_sl = np.full(n, T, dtype=np.int32)
    unresolved = np.ones(n, dtype=bool)
    for i in range(T):
        sl_hit = unresolved & (lo[:, i] <= sl_px)
        tp_hit = unresolved & ~sl_hit & (hi[:, i] >= tp_px)
        first_sl[sl_hit] = i
        first_tp[tp_hit] = i
        unresolved[sl_hit | tp_hit] = False
        if not unresolved.any():
            break
    reason = np.where((first_sl < T) & (first_sl <= first_tp), 2,
                      np.where(first_tp < T, 1, 0))
    # bars held, 1-based like the engine's walk_path
    bars = np.where(reason == 2, first_sl + 1,
                    np.where(reason == 1, first_tp + 1, T))
    return reason, bars


class FeatureSampler:
    """Draws realistic feature vectors from the recorded joint.

    The dense middle is jittered around a REAL anchor signal, and the row
    carries that anchor's own path id -- so a synthetic feature vector is
    labelled by the outcome that actually followed its nearest real
    neighbour, not by a random path. Only the boundary draws (uniform over
    the observed box) take a random path.
    """

    def __init__(self, rows: list[dict], anchor_paths: list[int],
                 rng: np.random.Generator):
        self.rng = rng
        self.anchor_paths = np.array(anchor_paths, dtype=np.int64)
        self.cat_pools = {}
        for f in CAT:
            self.cat_pools[f] = np.array(
                [float(r[f]) for r in rows if r.get(f) is not None])
        # an anchor is any real signal whose ATR was measured; its own
        # missingness pattern (e.g. no recorded volume -> volx NaN) is
        # preserved, not replaced
        self.anchor_rows = [i for i, r in enumerate(rows)
                            if r.get("atr") is not None
                            and r["atr"] == r["atr"]]
        self.M = np.full((len(rows), len(CONT)), np.nan, dtype=np.float32)
        self.CAT_M = np.zeros((len(rows), len(CAT)), dtype=np.float32)
        for i, r in enumerate(rows):
            for j, f in enumerate(CONT):
                v = r.get(f)
                if v is not None and v == v:
                    self.M[i, j] = v
            for j, f in enumerate(CAT):
                self.CAT_M[i, j] = float(r.get(f) or 0)
        self.missing_rate = {}
        self.lo = {}
        self.hi = {}
        for j, f in enumerate(CONT):
            col = self.M[:, j]
            vals = col[~np.isnan(col)]
            self.lo[f] = float(vals.min()) if len(vals) else 0.0
            self.hi[f] = float(vals.max()) if len(vals) else 0.0
            self.missing_rate[f] = float(np.isnan(col).mean())
        std = np.nanstd(self.M, axis=0)
        std[~np.isfinite(std)] = 0.0
        self.jit = 0.25 * std
        self.cat_pools = {f: np.array(v) for f, v in self.cat_pools.items()}

    def draw(self, n: int, boundary_frac: float = 0.0):
        """(X (n, 13) with NaN for missing, path ids (n,)) -- the path id
        is the anchor signal's own recorded outcome for jittered rows, a
        random one for boundary rows."""
        out = np.full((n, len(FEATURES)), np.nan, dtype=np.float32)
        path_ids = np.full(n, -1, dtype=np.int64)
        n_bound = int(n * boundary_frac)
        if not self.anchor_rows:
            n_bound = n              # no measurable anchors: all boundary
        n_joint = n - n_bound
        # the dense middle: a small perturbation AROUND a real anchor's own
        # vector, keeping the anchor's categories, its missingness pattern
        # and its recorded path -- so the synthetic row is labelled by its
        # nearest real neighbour
        if n_joint:
            anc = np.array(self.anchor_rows)[
                self.rng.integers(0, len(self.anchor_rows), n_joint)]
            path_ids[:n_joint] = self.anchor_paths[anc]
            z = self.M[anc] + self.rng.normal(
                0.0, 1.0, (n_joint, len(CONT))) * self.jit
            for j, f in enumerate(CONT):
                col = np.clip(z[:, j], self.lo[f], self.hi[f])
                out[:n_joint, FEATURES.index(f)] = col
            for j, f in enumerate(CAT):
                out[:n_joint, FEATURES.index(f)] = self.CAT_M[anc, j]
        # the boundary: uniform over the observed box, random paths
        path_ids[n_joint:] = self.rng.integers(0, len(self.anchor_paths),
                                               n_bound)
        for k, f in enumerate(CONT):
            out[n_joint:, FEATURES.index(f)] = \
                self.rng.uniform(self.lo[f], self.hi[f], n_bound)
        for f in CAT:
            pool = self.cat_pools[f]
            out[n_joint:, FEATURES.index(f)] = self.rng.choice(pool, n_bound)
        # missingness on the boundary draws, at the feature's observed rate
        for f in CONT:
            rate = self.missing_rate[f]
            if rate > 0:
                mask = self.rng.random(n_bound) < rate
                out[n_joint:, FEATURES.index(f)][mask] = np.nan
        boundary = np.zeros(n, dtype=bool)
        boundary[n_joint:] = True
        return out, path_ids, boundary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10_000_000)
    ap.add_argument("--chunk", type=int, default=1_000_000)
    ap.add_argument("--seed", type=int, default=0x7EA)
    ap.add_argument("--boundary", type=float, default=0.0,
                    help="fraction of uniform boundary draws with random paths "
                         "(marked in the prov column); measured to hurt the "
                         "learned selector, so 0 by default")
    a = ap.parse_args()

    print("  loading the real record...", flush=True)
    rows = []
    paths = []
    for sig in sources.joined():
        sym, t, side = sig["sym"], int(sig["t"]), sig["side"]
        meas = measures_for(sym, t, sig)
        out = sig.get("outcome") or {}
        fav, adv = out.get("fav"), out.get("adv")
        entry = float(out.get("entry") or sig.get("px") or 0)
        if not entry or not fav or not adv:
            continue
        rows.append({
            "atr": meas.get("atr"), "trend": meas.get("trend"),
            "vol20": meas.get("vol20"), "mom6h": meas.get("mom6h"),
            "mom1h": meas.get("mom1h"), "volx": meas.get("volx"),
            "agents": float(sig.get("agents") or 0),
            "tier": float(sig.get("tier") or 0),
            "score": float(sig.get("score") or 0),
            "who": float(sig.get("who") or 0),
            "counter": float(bool(sig.get("counter"))),
            "side": 1.0 if side == "BUY" else -1.0,
            "hour": (t % 86400) / 3600.0,
        })
        paths.append((np.asarray(fav[:96], dtype=np.float32),
                      np.asarray(adv[:96], dtype=np.float32), side))
    print(f"  {len(rows)} real feature vectors, {len(paths)} real paths",
          flush=True)

    # the paths once, padded, so a chunk is pure indexing
    L = 96
    PF = np.zeros((len(paths), L), dtype=np.float32)
    PA = np.zeros((len(paths), L), dtype=np.float32)
    PSELL = np.zeros(len(paths), dtype=bool)
    for i, (f_, a_, side) in enumerate(paths):
        PF[i, :len(f_)] = f_
        PA[i, :len(a_)] = a_
        PSELL[i] = side == "SELL"

    rng = np.random.default_rng(a.seed)
    sampler = FeatureSampler(rows, list(range(len(paths))), rng)
    OUT.mkdir(parents=True, exist_ok=True)
    X_path = OUT / "features.npz"

    t0 = time.time()
    total = 0
    # Memory-light on purpose: the full sample is 10M x 26 float32, about
    # 1 GB, and the box that builds it has Chrome holding the other 3 GB.
    # Accumulating chunk lists peaked at twice the final size and the
    # nightly build died right here. A memmap keeps the whole thing on
    # disk and only the current chunk in RAM; the save below streams each
    # array out block by block, so the peak never grows with n.
    mm = {name: np.memmap(OUT / f"{name}.bin", mode="w+", dtype=dt,
                          shape=(a.n, len(FEATURES)) if name == "X"
                          else (a.n,))
          for name, dt in (("X", np.float32), ("y", np.int8),
                           ("reason", np.int8), ("bars", np.int16),
                           ("prov", np.int8))}
    for chunk_start in range(0, a.n, a.chunk):
        n = min(a.chunk, a.n - chunk_start)
        X, path_ids, boundary = sampler.draw(n,
                                                boundary_frac=a.boundary)
        # the anchor's own recorded path for jittered rows, a random one
        # for boundary rows; flip when the sample side is opposite
        fav = PF[path_ids]
        adv = PA[path_ids]
        flip = PSELL[path_ids] != (X[:, FEATURES.index("side")] < 0)
        reason, bars = label_paths(fav, adv, flip)
        sl = slice(chunk_start, chunk_start + n)
        mm["X"][sl] = X
        mm["y"][sl] = (reason == 1).astype(np.int8)
        mm["reason"][sl] = reason.astype(np.int8)
        mm["bars"][sl] = bars.astype(np.int16)
        mm["prov"][sl] = boundary.astype(np.int8)
        total += n
        print(f"    {total}/{a.n}  ({time.time()-t0:.0f}s)", flush=True)

    for arr in mm.values():
        arr.flush()
    np.savez_compressed(X_path, X=mm["X"], y=mm["y"],
                        reason=mm["reason"], bars=mm["bars"],
                        prov=mm["prov"], features=np.array(FEATURES))
    manifest = {
        "n": total, "seed": a.seed, "seconds": round(time.time() - t0, 1),
        "features": FEATURES,
        "ranges": {f: [sampler.lo[f], sampler.hi[f]] for f in CONT},
        "missing_rate": sampler.missing_rate,
        "outcomes": {"target": int((mm["y"] == 1).sum()),
                     "stop": int((mm["reason"] == 2).sum()),
                     "open": int((mm["reason"] == 0).sum())},
        "note": "features synthetic from the recorded joint; each jittered "
                "row is labelled by its anchor signal's OWN recorded path "
                "(nearest-neighbour pairing), boundary rows by a random "
                "recorded path; labels by the engine's own walk_path. "
                "Only the feature pairing is synthetic.",
    }
    for arr in mm.values():
        try:
            arr._mmap.close()
        except Exception:
            pass
    for name in mm:
        try:
            (OUT / f"{name}.bin").unlink()
        except OSError:
            pass
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\n  {total} samples -> {X_path} "
          f"({X_path.stat().st_size/1e6:.0f} MB) in {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
