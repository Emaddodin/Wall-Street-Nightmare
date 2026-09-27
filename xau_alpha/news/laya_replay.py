"""
xau_alpha/news/laya_replay.py
Make the Laya / Jeff oracle backtestable: run the UNMODIFIED production LayaOracle.evaluate_setup_sync on historical
signals with a caching model backend, a fake clock and a fixed headline state (news/replay.py).

Backends
  none   (default here) dry run: records the exact (state, questions) the oracle would send, then raises so the
         oracle takes its rule fallback, exactly as production does on any model error.
  laya   laya.Router() (pip laya 0.3.4; needs a working torch and the convaiinnovations/laya weights: the English
         route downloads the whole 2.37 GB hub repo because Router passes no subfolder for it).
  jeff   scalper.brain.jeff_client.JeffClient (needs JEFF_URL and the separate Jeff-1 server).
Every model answer is cached on disk keyed by sha1(state, questions), so a replay is deterministic and re-runnable.

Usage (on a machine where the model loads):
  python3 xau_alpha/news/laya_replay.py --backend laya --state neutral --limit 500
Output: news/out/laya_replay_<backend>_<state>.csv  (+ cache news/out/laya_cache_<backend>.jsonl)
"""
import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "lib"))
import replay  # noqa: E402

def laya_lang():
    """laya/lang.py is dependency-free: load it without importing the torch-dependent package."""
    import site
    for sp in site.getsitepackages():
        p = Path(sp) / "laya/lang.py"
        if p.exists():
            spec = importlib.util.spec_from_file_location("laya_lang_standalone", p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    return None


class CachingAgent:
    def __init__(self, backend: str, cache: Path):
        self.backend_name = backend
        self.cache_path = cache
        self.cache = {}
        if cache.exists():
            for line in cache.read_text().splitlines():
                r = json.loads(line)
                self.cache[r["key"]] = r["res"]
        self.captured = []
        self.backend = None
        if backend == "laya":
            from laya import Router
            self.backend = Router()
        elif backend == "jeff":
            sys.path.insert(0, str(HERE.parents[1]))
            from scalper.brain.jeff_client import JeffClient
            self.backend = JeffClient()

    def predict(self, state, questions):
        key = hashlib.sha1(json.dumps([state, questions], sort_keys=True).encode()).hexdigest()
        self.captured.append({"key": key, "state": state, "questions": questions})
        if key in self.cache:
            return self.cache[key]
        if self.backend is None:
            raise RuntimeError("dry-run backend: no model")
        res = self.backend.predict(state, questions)
        res = {"answers": res.get("answers", {})}
        self.cache[key] = res
        with self.cache_path.open("a") as f:
            f.write(json.dumps({"key": key, "res": res}) + "\n")
        return res


def run(backend="none", state="neutral", limit=None):
    from data import load_m1
    from ref_runner import runner_orders

    rp = replay.ProductionGuardReplay(state=state, feed="calendar")
    from scalper.brain.ict_rag import get_ict_rag
    orc = rp.oracle
    orc.rag = get_ict_rag()
    agent = CachingAgent(backend, HERE / f"out/laya_cache_{backend}.jsonl")
    orc._agent, orc._is_ready = agent, True
    m1 = load_m1()
    orders = runner_orders(m1=m1)
    close_at = dict(zip(m1["ts"].values + 60_000, m1["c"].values))    # price known at the decision time
    if limit:
        orders = orders[: int(limit)]
    rows = []
    for o in orders:
        d = "BUY" if o["d"] > 0 else "SELL"
        px = float(close_at.get(o["t"], 0.0))
        v = rp.ghost_verdict(o["t"], d, entry_price=px, sl_price=px - o["d"] * o["sl_dist"])
        rows.append({"t": o["t"], "dir": d, **{k: v.get(k) for k in ("allowed", "why", "grade", "size_mult")}})
    out = pd.DataFrame(rows)
    out.to_csv(HERE / f"out/laya_replay_{backend}_{state}.csv", index=False)
    return out, agent


def dry_run_report():
    out, agent = run("none", "neutral", limit=300)
    lang = laya_lang()
    ex = agent.captured[0] if agent.captured else None
    routes = {}
    if lang and agent.captured:
        for c in agent.captured:
            a = lang.analyse(c["state"])
            k = "english" if a["script"] in ("latin", "unknown") and a["is_english"] else "multilingual"
            routes[k] = routes.get(k, 0) + 1
    uniq_states = len({json.dumps(c["state"], sort_keys=True) for c in agent.captured})
    varying = {}
    if agent.captured:
        keys = agent.captured[0]["state"].keys()
        for k in keys:
            varying[k] = len({str(c["state"].get(k)) for c in agent.captured})
    rep = {"signals_evaluated": int(len(out)), "model_calls_attempted": len(agent.captured),
           "unique_states": uniq_states, "distinct_values_per_state_field": varying,
           "router_route_counts": routes, "state_chars_example": len(json.dumps(ex["state"])) if ex else 0,
           "example_state": ex["state"] if ex else None, "example_questions": ex["questions"] if ex else None}
    (HERE / "out/laya_dry_run.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps({k: v for k, v in rep.items() if not k.startswith("example")}, indent=1))
    return rep


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="none", choices=["none", "laya", "jeff"])
    ap.add_argument("--state", default="neutral", choices=["offline_seed", "neutral", "strong_bear"])
    ap.add_argument("--limit", default=None)
    a = ap.parse_args()
    if a.backend == "none":
        dry_run_report()
    else:
        o, _ = run(a.backend, a.state, a.limit)
        print(o["allowed"].mean(), o["why"].value_counts().to_dict())
