# X2b trend_pullback (`cand/trend_pullback.py`): FAIL

Date: 2026-09-30. The full write-up is in `cand/reports/htf_breakout.md`: section 8 covers this module, and sections 0 and 10 cover the family verdict and the engine notes. This file is a pointer with the key numbers.

**Rule.** Longs are described; shorts are the exact mirror.
- **Trend:** from H1 (C > EMA50 > EMA200), or from the H4 Donchian-20 state.
- **Pullback:** a fresh M1 touch of the M5 EMA20, or of the latest live bullish M5 FVG.
- **Trigger:** an M1 engulfing bar, or a break of the minor swing, within W bars.
- **Stop:** behind the pullback swing (min low − σ·A, but at least smin·A).
- **Exit:** R-multiple target or an A5 trail, with a time stop of 240 min and flat at 16:45 ET.

**Search.** TRAIN only at lf_base, 216 unique configs:
- stage 1: 96 configs;
- stage 2: 128 configs around the fvg / engulf / London-NY-day top 5.

**Best by TRAIN t:** `{"trend":"ema","zone":"fvg","trig":"eng","sess":"day","ex":"tr4","sigma":1.0,"smin":1.0,"gap":15,"W":30}`

All figures are MEASURED.

| split | cost | n | PF | avg $/oz | long PF | short PF |
|---|---|---|---|---|---|---|
| TRAIN | lf_base | 606 | 1.081 | +0.27 | 1.10 | 1.04 |
| TRAIN | mid | 600 | 1.240 | +0.72 | 1.26 | 1.19 |
| TRAIN | lf_harsh | 617 | 0.806 | −0.75 | 0.83 | 0.75 |
| VALID | lf_base | 255 | 0.752 | −1.80 | 0.84 | 0.68 |
| VALID | mid | 254 | 0.814 | −1.29 | 0.90 | 0.75 |

- **TRAIN gross edge:** real in-sample, and not beta. At lf_base (50 seeds), random-direction p = 0.02 and permutation p = 0.02, and long and short are both positive at mid. The ~0.45 $/oz LiteFinance round trip removes most of it.
- **VALID:** fails even at zero cost. Random p = 0.57, permutation p = 0.45. The top 10 by TRAIN t score VALID PF 0.75-1.03.
- **Flip-eligible share:** 48% of trades on TRAIN, but only 11% on VALID, because stops doubled with volatility.
- **Verdict:** FAIL.
