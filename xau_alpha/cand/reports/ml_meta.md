# X3 ml_meta: gradient-boosted entry filter (`cand/ml_meta.py`): FAIL (no tradeable edge)

Date: 2026-09-30. Written in a cloud session from the saved result files only. The price data
(`xau_alpha/data/*.parquet`, `data/candles/duka_raw/`) is not in git, and the cloud container's network policy
blocks `datafeed.dukascopy.com`, so nothing was re-simulated here. All numbers are SOURCED from
`cand/results/ml_meta_models.json`, `ml_meta_s1_train.csv` and `ml_meta_s1_valid.csv`.

Method: see the module docstring. One mirrored HistGradientBoosting classifier, fitted before 2025-10-01,
early-stopped and calibrated on 2025-Q4 (still TRAIN). "TRAIN" columns below are that Q4 slice. VALID is 2026-01..05.
TEST was never touched.

## Verdict

**FAIL.** The model does not rank direction. The best flip-eligible config clears lf_base on VALID, but it is one
of 72 configurations, its TRAIN t-stat is 1.15, and it loses at lf_harsh on both splits.

## Evidence

1. **Out-of-sample AUC is close to 0.5** (stacked long+short, 19 models):
   - Brackets of 0.5-1.0 A5: VALID AUC 0.503-0.542.
   - Wider or faster brackets (1.25-1.5 A5, T = 30-60) reach 0.54-0.59. Those models mostly predict "the bracket
     times out", which is a volatility effect, not a direction. Early stopping picked 10-51 trees, so the model
     found very little to fit.
2. **Search size vs result.** 72 TRAIN configs; 15 were looked at on VALID (30 rows with the two cost models).
   The best VALID row (a 1.0, T 120, q 0.02, flip 1) is:

   | split | cost | n | WR | PF | avg R | sum R |
   |---|---|---|---|---|---|---|
   | Q4 (TRAIN) | lf_base | 83 | 57.8% | 1.25 | +0.128 | +10.6 |
   | VALID | lf_base | 65 | 56.9% | 1.23 | +0.110 | +7.2 |
   | Q4 (TRAIN) | lf_harsh | 77 | 48.1% | 0.78 | −0.130 | −10.0 |
   | VALID | lf_harsh | 62 | 51.6% | 0.90 | −0.049 | −3.0 |

   - TRAIN t = 1.15, and the first TRAIN half has PF 0.99.
   - Its neighbours fail on VALID: q 0.05 gives PF 1.12, q 0.10 gives 1.05, T 60 gives 0.86.
3. **Costs decide the sign.** Every flip-eligible config (stops 3.2-3.6 $/oz) is negative at lf_harsh on both
   splits. The ~0.45 $/oz round trip is 13-14% of the stop, which is about the size of the whole edge.

## What would change the verdict

Nothing that can be done without new data. If a forward test is wanted, the only defensible use is a DEMO shadow
run of the frozen a 1.0 / T 120 / q 0.02 / flip 1 config with a pre-registered bar (for example 100 trades,
PF ≥ 1.15 at the real LiteFinance fills). It must not size or gate REAL trades before that.
