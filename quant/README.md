# quant/ — New leveraged crypto futures research project

Independent from the legacy TBT engine and the `scalper/` project.  This
directory contains the full research lifecycle for discovering the
strongest short-term leveraged crypto futures strategy the historical data
can support, researched toward a +100% net daily ROE target.

## Start here

* **`FINAL_REPORT.md`** — round 1 deliverable: strategy spec, research
  history, historical + OOS + walk-forward + robustness results, +100%
  target math.
* **`SELECTIVE_BITUNIX_REPORT.md`** — round 2 deliverable: the
  selectivity experiment (event-level scoring, leverage/allocation/
  frequency grids, Monte Carlo, Bitunix execution model) and the final
  verdict on the +100% daily ROE question.
* **`RESEARCH.md`** — the running research log (discovery findings,
  candidate evolution, honesty audits).

## Headline result

A real, universal, statistically strong short-horizon mean-reversion edge
exists (~2.5–6 bps per cross-sectional snapshot at 15m–48h horizons across
88 symbols × 24–36 months), but it is smaller than realistic round-trip
costs (4–12 bps).  The strongest honest configuration (S5 "Q-FADE",
maker-limit cross-sectional fade) nets −2.65 bps/trade (PF 0.92).  The
+100%/day target would require net +0.39 bps/trade at N=895 trades/day ×
20x leverage — the report quantifies the gap and the exact cost regime
(≤ ~3 bps round trip) that would close it.

## Layout

```
fetch/            Binance Vision downloader, universe, orchestration
lib/              parquet store, causal features, forward outcomes
research/         5m panel + breadth, bucket/2D/event-study discovery
engine/           event-driven backtester (fees/slippage/funding/lev/liq,
                  maker limit orders, compounding + fixed-base modes)
strategies/       S1 momentum (control), S2 taker fade, S3 maker d-fade,
                  S5 Q-FADE (final)
experiments/      runner + leaderboard (JSONL) + per-run reports
tools/            discovery, cross-section, game hunt, cond2d, vol-scaled,
                  momo-alts, verify_fill (adversarial audit), venue_check,
                  roll_wf (walk-forward), target_analysis (+100% math)
data/             klines (102 syms x 1m, 24-36mo), metrics (OI/taker),
                  funding, research CSVs, experiment reports
```

## Reproduce

```bash
# data (already downloaded; resumable)
python3 quant/fetch/download.py --klines-months 24 --funding-months 24 \
    --metrics-days 365 --workers 8

# strongest candidate backtest (S5 Q-FADE, honest 1m resolution)
python3 quant/experiments/runner.py --strategy s5_cond_fade_1m \
  --params '{"min_z":1.5,"tp_bps":80,"sl_bps":60,"max_bars":60,"alloc":0.25,
            "lev":20,"limit_bps":50,"limit_wait":30,"lookback":15,
            "vol_win":1440}' \
  --window 2025-01-01:2026-09-01 --top-k 0 --min-bars 300000 \
  --taker-fee-bps 6 --slippage-bps 2 --no-compound \
  --hypothesis "S5 reproduction"

# walk-forward
python3 quant/tools/roll_wf.py --strategy s5_cond_fade_1m \
  --params '{...}' --window-months 3

# +100% target math on any run
python3 quant/tools/target_analysis.py --report <experiment_id>
```

Memory note: the backtester keeps all symbol frames in RAM (~4 GB at 1m
for the full universe).  Do not run more than one heavy job at a time on
an 8 GB machine.
