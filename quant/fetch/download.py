"""Orchestrate the full research dataset download.

Usage:
  python3 quant/fetch/download.py --klines-months 24 --metrics-days 365
  python3 quant/fetch/download.py --only klines

Env overrides:
  QUANT_DATA      data root (default: quant/data)
  QUANT_WORKERS   download parallelism (default: 8)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.fetch import universe, vision  # noqa: E402
from quant.lib.store import ensure_dirs  # noqa: E402

log = logging.getLogger("quant.fetch.download")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

DATA = Path(os.getenv("QUANT_DATA", str(ROOT / "quant" / "data")))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["klines", "funding", "metrics"])
    ap.add_argument("--klines-months", type=int, default=24,
                    help="months of 1m klines for tier B; tier A gets +12")
    ap.add_argument("--funding-months", type=int, default=24)
    ap.add_argument("--metrics-days", type=int, default=365,
                    help="days of 5m metrics for tier B; tier A gets x2")
    ap.add_argument("--workers", type=int,
                    default=int(os.getenv("QUANT_WORKERS", "8")))
    args = ap.parse_args()

    ensure_dirs(DATA)
    uni_path = DATA / "universe.json"
    if not uni_path.exists():
        universe.build_universe(uni_path)
    uni = universe.load_universe(uni_path)
    symbols = sorted(uni["symbols"])
    tiers = {s: uni["symbols"][s]["tier"] for s in symbols}
    # longest histories for the true majors only (disk budget)
    deep = set(universe.MAJORS[:12])
    k_months = {s: (args.klines_months + 12) if s in deep
                else args.klines_months for s in symbols}
    f_months = {s: (args.funding_months + 12) if s in deep
                else args.funding_months for s in symbols}
    m_days = {s: (args.metrics_days * 2) if tiers[s] == "A"
              else args.metrics_days for s in symbols}

    kdir = DATA / "klines"
    fdir = DATA / "funding"
    mdir = DATA / "metrics"
    for d in (kdir, fdir, mdir):
        d.mkdir(parents=True, exist_ok=True)

    jobs: list = []

    if args.only in (None, "funding"):
        jobs = []
        for s in symbols:
            jobs.append((vision.update_funding,
                         (s, vision.month_list(f_months[s]), fdir)))
        log.info("funding jobs: %d", len(jobs))
        vision.run_all(jobs, args.workers, "funding")
        vision.save_manifest(DATA / "funding" / "manifest.json",
                             {s: "done" for s in symbols})

    if args.only in (None, "klines"):
        jobs = []
        for s in symbols:
            jobs.append((vision.update_klines,
                         (s, vision.month_list(k_months[s]), kdir)))
        log.info("klines jobs: %d (deep %dmo, rest %dmo)", len(jobs),
                 args.klines_months + 12, args.klines_months)
        res = vision.run_all(jobs, args.workers, "klines")
        vision.save_manifest(kdir / "manifest.json", res)

    if args.only in (None, "metrics"):
        jobs = []
        for s in symbols:
            # tier B gets 6 months only (research focus: liquid majors)
            d = (args.metrics_days * 2) if tiers[s] == "A" \
                else min(args.metrics_days, 180)
            jobs.append((vision.update_metrics,
                         (s, vision.day_list(d), mdir)))
        log.info("metrics jobs: %d (tier A x2 days)", len(jobs))
        vision.run_all(jobs, args.workers, "metrics")
        vision.save_manifest(mdir / "manifest.json", {s: "done" for s in symbols})

    log.info("download phase complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
