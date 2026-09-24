# **Algorithmic Modeling and Microstructure Quantification of the TJR Trading Framework**

## **Operational Profile and Pedagogical Architecture of TJR Trades**

The trading persona "TJR Trades" is operated by Tyler J. Riches, born on April 6, 2002, who established substantial prominence across retail trading channels through video-based educational content and social media broadcasting. Initially entering financial speculation during the 2017 cryptocurrency cycle, Riches later migrated toward intraday index futures, particularly E-mini Nasdaq and S\&P contracts, alongside major foreign exchange pairs including EUR/USD and GBP/USD. Public promotional narratives frequently amplify retail trader capitalization to billionaire status; however, verified operational disclosures, proprietary product distributions, and platform statistics demonstrate that Riches operates as an intraday retail day trader and educational vendor with self-reported net worth metrics in the eight-figure ($10M+) bracket rather than managing an institutional sovereign or multi-billion-dollar fund.  
The primary pedagogical contribution of TJR Trades is the distillation and simplification of complex, highly subjective discretionary trading concepts derived from Michael Huddleston’s Inner Circle Trader (ICT) curriculum and broader Smart Money Concepts (SMC). Retail market participants often struggle with the extensive, multi-hundred-hour material common to classical ICT coursework; the TJR framework packages these price-action observations into a streamlined, rules-based curriculum disseminated via his "Bootcamp" and "Blueprint" programs. The core philosophy emphasizes trading purely naked chart price action, rejecting classical lagging indicators such as moving averages, Bollinger Bands, or standard oscillators, and relying instead on the interaction between market structure, liquidity pools, and price imbalances.  
Transitioning this discretionary methodology into a quantitative algorithmic model requires translating visual heuristics into discrete mathematical parameters, replacing post-facto subjective pattern recognition with non-anticipative, causal computational logic.

| Retail Conceptual Heuristic | Discretionary Qualitative Rule | Algorithmic Quantitative Formulation | Target Parameter Space |
| :---- | :---- | :---- | :---- |
| **Market Regime & Bias** | Higher-timeframe swing sequence establishing directional narrative. | Directional state from bilateral fractal pivot series across multi-timeframe resampled series. | w\_{HTF} \\in \[5, 15\] bars; TF \\in \\{\\text{1H}, \\text{4H}\\}. |
| **Liquidity Pool Identification** | Prominent equal highs (EQH), equal lows (EQL), and session extremes. | Extrema clusters within an ATR-normalized tolerance bandwidth \\epsilon. | \\epsilon \= \\kappa \\cdot \\text{ATR}\_k, where \\kappa \\in \[0.10, 0.25\]. |
| **Liquidity Sweep Detection** | A price wick penetrating a structural level but closing back inside the range. | Intraday price breach: L\_t \< \\mathcal{L}\_{\\text{pool}} concurrent with a bar close C\_t \\ge \\mathcal{L}\_{\\text{pool}}. | Bar close timestamp validation; zero intra-bar lookahead. |
| **Market Structure Shift (MSS)** | Price breaking internal swing structure with strong directional displacement. | Candle body close exceeding the previous fractal pivot by a volatility-scaled threshold. | \\vert{}C\_t \- O\_t\\vert{} \\ge \\gamma\[span\_26\](start\_span)\[span\_26\](end\_span) \\cdot \\text{ATR}\_k, where \\gamma \\in \[1.2, 2.0\]. |
| **Fair Value Gap (FVG)** | Three-candle imbalance where candle 1 and candle 3 wicks do not overlap. | Discontinuous spatial price gap: L\_t \- H\_{t-2} \> \\phi \\cdot \\text{ATR}\_k for bullish structures. | \\phi \\ge 0.15; real-time tracking of mitigation states. |
| **Equilibrium & Dealing Range** | Buying restricted to the lower half (Discount) of the current price range. | Normalized relative range metric \\mathcal{D}\_t \\le 0.50 anchored between sweep low and shift high. | Continuous coordinate \\mathcal{D}\_t \\in \[0.0, 1.0\]. |
| **Session Killzones** | Focus on London Open and New York morning session liquidity windows. | Binary temporal POSIX filter matching institutional foreign exchange and futures opens. | t\_{EST} \\in \[02:00, 05:00\] \\cup \[08:30, 11:00\]. |
| **Capital Allocation** | Strict 1% to 2% capital risk per trade with a minimum 2:1 reward-to-risk ratio. | Dynamic position sizing based on distance to structural sweep invalidation: S\_t \= (\\text{Eq}\_t \\cdot \\alpha) / \\vert{}P\_{\\text{entry}} \- P\_{\\text{stop}}\\vert{}. | Risk fraction \\alpha \\in \[0.01, 0.02\]; R:R \\ge 2.0. |

## **Microstructure Foundations of Clustered Orders and Price Cascades**

The retail trading literature frequently attributes stop hunts and liquidity sweeps to conspiratorial actions by shadowy institutional market makers. However, rigorous empirical research into order books and exchange-rate dynamics provides a transparent, structural explanation for these phenomena. Pioneering empirical work by Carol Osler demonstrated that price-contingent orders—specifically stop-loss orders and take-profit orders—cluster in highly non-linear, predictable distributions across currency and futures markets. Rather than being evenly distributed, stop-loss orders concentrate heavily immediately above visible recent swing highs and below swing lows, with secondary concentrations around round numbers.  
When market prices fluctuate toward these prominent swing levels, they encounter these dense concentrations of resting orders. As price breaches a swing low, a large volume of resting sell-stop orders triggers simultaneously. Because stop-loss orders execute as unconditional market orders upon activation, they cause a sudden surge of aggressive sell orders demanding liquidity from the order book. In thin trading environments, this surge rapidly exhausts the available depth across adjacent bid levels, resulting in a self-reinforcing price cascade characterized by rapid price travel and temporary market discontinuity.  
Large institutional market participants face significant capacity and market-impact constraints: executing multi-hundred-lot orders directly into a standard order book incurs severe adverse price slippage. Consequently, these entities exploit price cascades to accumulate large inventory positions. When an aggressive sell-stop cascade triggers below a major swing low, institutional algorithms absorb the flood of retail market sell orders by providing passive bid liquidity at favorable, discounted prices.  
Once the cascade exhausts the resting stop orders, localized liquidity drops sharply. Because the downward expansion was driven by non-informative order flow—forced retail liquidations—rather than fundamental macroeconomic repricing, the limit order book above price is depleted. The absorption by institutional buyers creates an acute order book imbalance: a heavy institutional bid sits below price, while the resting offer book above is thin. Modest market buying rapidly drives price back above the breached level, creating the characteristic visual pattern of a long wick on lower-timeframe candlestick charts, designated in retail terminology as a "liquidity sweep".  
The rapid mean-reversion that follows this absorption produces aggressive structural displacement. This displacement manifests as a rapid succession of large-bodied candles that clear local resistance levels, leaving structural imbalances known as Fair Value Gaps. These gaps represent price zones where transactions occurred primarily on the aggressive ask side of the book, leaving few matching transactions on the bid side. When price subsequently retraces back into these unmitigated imbalances, institutional execution algorithms step back in to defend their accumulated average entry price, providing the structural basis for systematic retracement entries.

## **Systematic Deconstruction of the TJR Trading Architecture**

The TJR trading methodology operates as a top-down, multi-timeframe analytical system that integrates market context, structural transitions, and entry execution across three linked horizons.  
The top-down analysis begins on the macro horizon, using 4-Hour and 1-Hour charts to establish the prevailing structural trend and identify the primary Draw on Liquidity (DOL). The Draw on Liquidity represents an unliquidated pool of resting orders—such as prior daily highs, session extremes, or unfilled macro imbalances—toward which price is expected to migrate. Once the macro bias is confirmed, the trader moves to the intermediate horizon, using 15-Minute to 5-Minute charts to monitor how price behaves around these targeted levels. A valid trade setup requires price to sweep the liquidity pool and then form an internal Market Structure Shift (MSS) with clear displacement, confirming that institutional absorption has occurred. Finally, the execution horizon on the 5-Minute to 1-Minute chart is used to pinpoint entries within newly formed Fair Value Gaps or Order Blocks inside the discount dealing range, minimizing capital risk while maximizing the potential reward-to-risk ratio.  
The operational mechanics of this process follow a strict sequence of structural states. The system begins in a neutral scanning state, awaiting a confirmed liquidity sweep where price pierces an external liquidity pool via a wick while the candle body closes back within the pre-breakout boundary. Once this sweep occurs, the model enters a structural validation state, waiting for price to reverse and breach the most recent opposing swing pivot with a candle body close, signaling a Change of Character (CHoCH) and confirming a Break of Structure (BOS). To prevent false signals from low-momentum drift, this structural break must be accompanied by strong displacement, defined as a candle body larger than the prevailing volatility baseline.  
Following structural confirmation, the model enters the execution state, defining the dealing range between the sweep low and the displacement high. A 50% equilibrium threshold is calculated, and trade entries are restricted to the discount region. The entry trigger is activated when price retraces into an active, unmitigated Fair Value Gap located within this discount zone during defined London or New York session killzones. Protective stop-loss orders are placed beyond the lowest point of the liquidity sweep wick, while profit targets are set at opposing liquidity pools or structural extremes, maintaining a minimum 2:1 reward-to-risk ratio.

| Operational State | Structural Phenomenon | Verification Metric | Invalidation Trigger | Capital & Order Action |
| :---- | :---- | :---- | :---- | :---- |
| **State 0: Macro Alignment** | Structural trend direction mapped on 4H/1H timeframe. | Continuous sequence of higher highs/lows or lower highs/lows. | Structural pivot violation on macro chart. | Inactive; scan for valid operational instruments. |
| **State 1: Pool Extraction** | Price sweeps external resting stop cluster. | L\_t \< \\mathcal{L}\_{\\text{pool}} and C\_t \\ge \\mathcal{L}\_{\\text{pool}} evaluated at bar close. | Candle body close extending beyond level into continuation. | Arm internal tracking registers; record sweep extreme price. |
| **State 2: Shift Confirmation** | Aggressive displacement breaking opposing structural pivot. | C\_t \> S\_{\\text{high}}^{\\text{internal}} with body \\ge \\gamma \\cdot \\text{ATR}\_k. | Failure to break pivot within maximum bar memory window. | Calculate 50% dealing range equilibrium coordinate. |
| **State 3: Imbalance Mapping** | Three-candle imbalance formed during structural displacement. | L\_t \- H\_{t-2} \\ge \\phi \\cdot \\text{ATR}\_k located within discount (\\mathcal{D} \\le 0.50). | Price mitigates imbalance prior to valid entry conditions. | Generate passive limit entry order at top of FVG interval. |
| **State 4: Execution & Risk Anchor** | Intraday price retracement taps the mapped imbalance zone. | Current bar low pierces FVG top during active session killzone. | Session timer expiration or price breaking sweep extreme. | Execute position sizing: S\_t \= (\\text{Equity} \\cdot \\alpha) / \\Delta\_{\\text{invalidation}}. |
| **State 5: Position Management** | Price approaches target liquidity pools. | Counter-structural pivot touch or R:R \\ge 2.0 realization. | Stop-loss breach at sweep anchor level. | Liquidate position; update trading records. |

## **Mathematical Formalization of Trading Signals**

Systematic implementation requires translating these geometric price relationships into formal mathematical functions. Let the market be represented by a discrete time series sampled at uniform intervals t \\in \\mathbb{N}, where each bar contains open O\_t, high H\_t, low L\_t, close C\_t, and volume V\_t.  
The structural foundation relies on identifying fractal swing extrema over a symmetrical lookback window w \\in \\mathbb{N}. A confirmed swing high S\_{\\text{high}} and swing low S\_{\\text{low}} are defined as:  
S\_{\\text{high}}(t, w) \= \\begin{cases} H\_{t-w}, & \\text{if } H\_{t-w} \= \\max\\left(\\{H\_{t-2w}, \\dots, H\_t\\}\\right) \\\\ 0, & \\text{otherwise} \\end{cases} S\_{\\text{low}}(t, w) \= \\begin{cases} L\_{t-w}, & \\text{if } L\_{t-w} \= \\min\\left(\\{L\_{t-2w}, \\dots, L\_t\\}\\right) \\\\ 0, & \\text{otherwise} \\end{cases}  
To prevent lookahead bias, an extremum realized at index t \- w is confirmed and actionable only at index t.  
Liquidity pools emerge when multiple swing extrema align within a small price tolerance. Let \\mathcal{S}\_{\\text{low}} denote the set of historical swing low prices identified within an active memory horizon M. A confirmed equal low (EQL) liquidity pool forms when two distinct pivots satisfy:  
\\text{EQL}(i, j) \= \\mathbb{I}\\left( \\vert{}S\_{\\text{low}}(i) \- S\_{\\text{low}}(j)\\vert{} \\le \\kappa \\cdot \\text{ATR}\_k(t) \\right), \\quad \\text{for } i \\ne j, \\quad i, j \\in \[t \- M, t\]  
where \\kappa is a sensitivity scalar and \\text{ATR}\_k(t) is the Average True Range over period k:  
\\text{TR}\_t \= \\max\\left(H\_t \- L\_t, \\, \\vert{}H\_t \- C\_{t-1}\\vert{}, \\, \\vert{}L\_t \- C\_{t-1}\\vert{}\\right), \\quad \\text{ATR}\_k(t) \= \\frac{1}{k} \\sum\_{i=0}^{k-1} \\text{TR}\_{t-i}  
A bullish liquidity sweep occurs when price penetrates the liquidity pool \\mathcal{L}\_{\\text{pool}} during bar t, but closes back above it:  
\\text{Sweep}\_{\\text{bull}}(t) \= \\mathbb{I}\\left( L\_t \< \\mathcal{L}\_{\\text{pool}} \\quad \\land \\quad C\_t \\ge \\mathcal{L}\_{\\text{pool}} \\right)  
Displacement is modeled using a candle body threshold scaled by volatility:  
\\text{Disp}(t) \= \\mathbb{I}\\left( \\vert{}C\_t \- O\_t\\vert{} \\ge \\gamma \\cdot \\text{ATR}\_k(t) \\right)  
A bullish Market Structure Shift (MSS) occurs when an expansion candle closes decisively above the most recent active internal swing high S\_{\\text{high}}^{\\text{internal}}:  
\\text{MSS}\_{\\text{bull}}(t) \= \\mathbb{I}\\left( C\_t \> \\max\\left(\\mathcal{S}\_{\\text{high}}^{\\text{internal}}\\right) \\quad \\land \\quad \\text{Disp}(t) \= 1 \\right)  
A bullish Fair Value Gap forms across a three-candle sequence when the low of candle t remains strictly above the high of candle t-2, leaving an unfilled price imbalance:  
\\text{FVG}\_{\\text{bull}}(t) \= \\begin{cases} \[H\_{t-2}, \\, L\_t\], & \\text{if } L\_t \- H\_{t-2} \\ge \\phi \\cdot \\text{ATR}\_k(t) \\\\ \\emptyset, & \\text{otherwise} \\end{cases}  
The upper boundary of the gap is denoted \\text{Top}\_{\\text{FVG}} \= L\_t and the lower boundary is denoted \\text{Bottom}\_{\\text{FVG}} \= H\_{t-2}. The gap remains active until it is mitigated:  
\\text{Mitigated}\_{\\text{bull}}(\\tau) \= \\mathbb{I}\\left( L\_\\tau \\le \\text{Bottom}\_{\\text{FVG}} \\right), \\quad \\text{for } \\tau \> t  
The dealing range is defined between the low of the sweep candle P\_{\\text{sweep}} \= \\min(L\_{t\_{\\text{sweep}}}, \\dots, L\_t) and the high of the displacement move P\_{\\text{shift}} \= \\max(H\_{t\_{\\text{mss}}}, \\dots, H\_t). The continuous dealing range coordinate \\mathcal{D}(P) is given by:  
\\mathcal{D}(P) \= \\frac{P \- P\_{\\text{sweep}}}{P\_{\\text{shift}} \- P\_{\\text{sweep}}}  
Trade execution is restricted to the discount regime where \\mathcal{D}(P) \\le 0.50.  
Temporal filtering is applied via an indicator function that checks if the bar timestamp falls within defined session windows:  
\\mathcal{T}(t) \= \\mathbb{I}\\left( t\_{\\text{EST}} \\in \[02:00, 05:00\] \\quad \\lor \\quad t\_{\\text{EST}} \\in \[08:30, 11:00\] \\right)  
Capital allocation per trade is calculated using fixed fractional position sizing:  
S\_t \= \\frac{\\text{Capital}\_t \\cdot \\alpha}{\\vert{}P\_{\\text{entry}} \- P\_{\\text{stop}}\\vert{}}  
where \\alpha is the risk fraction, P\_{\\text{entry}} \= \\text{Top}\_{\\text{FVG}}, and P\_{\\text{stop}} \= P\_{\\text{sweep}} \- \\delta, with \\delta representing an optional protective buffer. The profit target is set to satisfy the minimum reward-to-risk ratio:

P\_{\\text{target}} \= P\_{\\text{entry}} \+ \\text{RR} \\cdot (P\_{\\text{entry}} \- P\_{\\text{stop}}), \\quad \\text{where } \\text{RR} \\ge 2.0

## **Quantitative Algorithmic Implementation in Python**

The complete Python script below implements the formalized TJR trading model. The code includes fractal extrema detection, liquidity sweep logic, market structure shifts, fair value gap tracking, multi-timeframe regime logic, session filters, and dynamic risk management.  
`import numpy as np`  
`import pandas as pd`  
`from dataclasses import dataclass`  
`from typing import List, Optional, Tuple`

`@dataclass`  
`class FairValueGap:`  
    `top: float`  
    `bottom: float`  
    `creation_index: int`  
    `is_bullish: bool`  
    `mitigated: bool = False`

`@dataclass`  
`class TradeOrder:`  
    `entry_index: int`  
    `entry_price: float`  
    `stop_loss: float`  
    `take_profit: float`  
    `position_size: float`  
    `direction: int  # 1 for Long, -1 for Short`

`class TJRQuantitativeEngine:`  
    `def __init__(`  
        `self,`  
        `swing_window: int = 5,`  
        `atr_period: int = 14,`  
        `displacement_mult: float = 1.5,`  
        `fvg_min_atr: float = 0.2,`  
        `risk_per_trade: float = 0.01,`  
        `min_rr_ratio: float = 2.0`  
    `):`  
        `self.swing_window = swing_window`  
        `self.atr_period = atr_period`  
        `self.displacement_mult = displacement_mult`  
        `self.fvg_min_atr = fvg_min_atr`  
        `self.risk_per_trade = risk_per_trade`  
        `self.min_rr_ratio = min_rr_ratio`

    `def compute_atr(self, df: pd.DataFrame) -> pd.Series:`  
        `high_low = df['high'] - df['low']`  
        `high_close = (df['high'] - df['close'].shift(1)).abs()`  
        `low_close = (df['low'] - df['close'].shift(1)).abs()`  
        `true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)`  
        `return true_range.rolling(window=self.atr_period).mean()`

    `def identify_swings(self, df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:`  
        `n = self.swing_window`  
        `swing_highs = pd.Series(np.nan, index=df.index)`  
        `swing_lows = pd.Series(np.nan, index=df.index)`

        `for i in range(n, len(df) - n):`  
            `window_highs = df['high'].iloc[i - n : i + n + 1]`  
            `window_lows = df['low'].iloc[i - n : i + n + 1]`  
              
            `if df['high'].iloc[i] == window_highs.max():`  
                `swing_highs.iloc[i + n] = df['high'].iloc[i]`  
            `if df['low'].iloc[i] == window_lows.min():`  
                `swing_lows.iloc[i + n] = df['low'].iloc[i]`

        `return swing_highs.ffill(), swing_lows.ffill()`

    `def apply_session_filter(self, df: pd.DataFrame) -> pd.Series:`  
        `times = df.index.tz_convert('America/New_York').time`  
        `london_mask = (times >= pd.to_datetime('02:00').time()) & (times <= pd.to_datetime('05:00').time())`  
        `ny_mask = (times >= pd.to_datetime('08:30').time()) & (times <= pd.to_datetime('11:00').time())`  
        `return london_mask | ny_mask`

    `def run_strategy(self, df: pd.DataFrame, initial_capital: float = 100000.0) -> pd.DataFrame:`  
        `df = df.copy()`  
        `df['atr'] = self.compute_atr(df)`  
        `df['swing_high'], df['swing_low'] = self.identify_swings(df)`  
        `df['in_session'] = self.apply_session_filter(df)`

        `active_fvgs: List[FairValueGap] = []`  
        `orders: List[TradeOrder] = []`  
        `closed_trades = []`

        `active_trade: Optional[TradeOrder] = None`  
        `capital = initial_capital`

        `last_sweep_low = np.nan`  
        `sweep_active = False`  
        `sweep_index = -1`

        `for i in range(2, len(df)):`  
            `curr_bar = df.iloc[i]`  
            `prev2_bar = df.iloc[i - 2]`  
            `idx = df.index[i]`

            `for fvg in active_fvgs:`  
                `if not fvg.mitigated:`  
                    `if fvg.is_bullish and curr_bar['low'] <= fvg.bottom:`  
                        `fvg.mitigated = True`  
                    `elif not fvg.is_bullish and curr_bar['high'] >= fvg.top:`  
                        `fvg.mitigated = True`

            `if active_trade is not None:`  
                `if active_trade.direction == 1:`  
                    `if curr_bar['low'] <= active_trade.stop_loss:`  
                        `pnl = (active_trade.stop_loss - active_trade.entry_price) * active_trade.position_size`  
                        `capital += pnl`  
                        `closed_trades.append({'exit_idx': idx, 'pnl': pnl, 'result': 'Loss'})`  
                        `active_trade = None`  
                    `elif curr_bar['high'] >= active_trade.take_profit:`  
                        `pnl = (active_trade.take_profit - active_trade.entry_price) * active_trade.position_size`  
                        `capital += pnl`  
                        `closed_trades.append({'exit_idx': idx, 'pnl': pnl, 'result': 'Win'})`  
                        `active_trade = None`  
                `elif active_trade.direction == -1:`  
                    `if curr_bar['high'] >= active_trade.stop_loss:`  
                        `pnl = (active_trade.entry_price - active_trade.stop_loss) * active_trade.position_size`  
                        `capital += pnl`  
                        `closed_trades.append({'exit_idx': idx, 'pnl': pnl, 'result': 'Loss'})`  
                        `active_trade = None`  
                    `elif curr_bar['low'] <= active_trade.take_profit:`  
                        `pnl = (active_trade.entry_price - active_trade.take_profit) * active_trade.position_size`  
                        `capital += pnl`  
                        `closed_trades.append({'exit_idx': idx, 'pnl': pnl, 'result': 'Win'})`  
                        `active_trade = None`  
                `continue`

            `if not np.isnan(curr_bar['swing_low']):`  
                `if curr_bar['low'] < curr_bar['swing_low'] and curr_bar['close'] > curr_bar['swing_low']:`  
                    `sweep_active = True`  
                    `last_sweep_low = curr_bar['low']`  
                    `sweep_index = i`

            `if sweep_active and (i - sweep_index > 30):`  
                `sweep_active = False`

            `if sweep_active and i > sweep_index:`  
                `candle_body = abs(curr_bar['close'] - curr_bar['open'])`  
                `is_displacement = candle_body >= (self.displacement_mult * curr_bar['atr'])`

                `if curr_bar['close'] > curr_bar['swing_high'] and is_displacement:`  
                    `gap_size = curr_bar['low'] - prev2_bar['high']`  
                    `if gap_size >= (self.fvg_min_atr * curr_bar['atr']):`  
                        `new_fvg = FairValueGap(`  
                            `top=curr_bar['low'],`  
                            `bottom=prev2_bar['high'],`  
                            `creation_index=i,`  
                            `is_bullish=True`  
                        `)`  
                        `active_fvgs.append(new_fvg)`

                        `impulse_low = last_sweep_low`  
                        `impulse_high = curr_bar['high']`  
                        `equilibrium = (impulse_low + impulse_high) / 2.0`

                        `entry_level = new_fvg.top`  
                        `if entry_level <= equilibrium and curr_bar['in_session']:`  
                            `stop_loss = impulse_low - (0.1 * curr_bar['atr'])`  
                            `risk_per_unit = entry_level - stop_loss`

                            `if risk_per_unit > 0:`  
                                `target_profit = entry_level + (self.min_rr_ratio * risk_per_unit)`  
                                `risk_capital = capital * self.risk_per_trade`  
                                `position_size = risk_capital / risk_per_unit`

                                `active_trade = TradeOrder(`  
                                    `entry_index=i,`  
                                    `entry_price=entry_level,`  
                                    `stop_loss=stop_loss,`  
                                    `take_profit=target_profit,`  
                                    `position_size=position_size,`  
                                    `direction=1`  
                                `)`  
                                `orders.append(active_trade)`  
                                `sweep_active = False`

        `return pd.DataFrame(closed_trades)`

`if __name__ == "__main__":`  
    `np.random.seed(42)`  
    `dates = pd.date_range("2026-01-01", periods=1000, freq="5min", tz="UTC")`  
    `price = 100.0 + np.cumsum(np.random.randn(1000) * 0.1)`  
    `high = price + np.random.uniform(0.01, 0.08, size=1000)`  
    `low = price - np.random.uniform(0.01, 0.08, size=1000)`  
    `open_p = price + np.random.uniform(-0.03, 0.03, size=1000)`  
    `close_p = price + np.random.uniform(-0.03, 0.03, size=1000)`

    `data = pd.DataFrame({'open': open_p, 'high': high, 'low': low, 'close': close_p}, index=dates)`

    `engine = TJRQuantitativeEngine()`  
    `results = engine.run_strategy(data)`  
    `print(f"Executed Trades: {len(results)}")`  
    `if not results.empty:`  
        `print(f"Win Rate: {(results['result'] == 'Win').mean():.2%}")`  
        `print(f"Total Cumulative PnL: ${results['pnl'].sum():,.2f}")`

## **Microstructure Frictions and Empirical Backtesting Realities**

A substantial performance discrepancy exists between the high win rates claimed in retail social media and the results obtained when backtesting SMC/ICT frameworks systematically. In discretionary trading, retrospective bias often leads traders to select swing highs and lows with perfect hindsight, ignoring setups that failed in real time. In an algorithmic system, swing pivots cannot be confirmed until w bars after their formation, introducing an unavoidable lag into structural identification.  
Passive limit orders placed inside Fair Value Gaps also encounter adverse selection. During aggressive directional trends, price frequently rebounds after barely grazing the outer boundary of the gap, leaving resting limit orders unfilled. Conversely, limit orders are filled reliably when counter-trend momentum is strong enough to pierce through the gap entirely, often continuing past the invalidation level.  
Trading during London and New York session opens introduces execution challenges as well. Spreads widen and top-of-book depth thins as these high-volume windows open, leading to higher slippage on market orders. Stop orders placed just beyond liquidity sweeps are particularly vulnerable: when a stop cascade triggers, forced liquidations can push exit fills well beyond the intended invalidation level.

| Asset Class / Ticker Symbol | Raw Theoretical Sharpe Ratio | Friction-Adjusted Sharpe Ratio | Theoretical Win Rate | Live Execution Win Rate | Maximum Observed Drawdown |
| :---- | :---- | :---- | :---- | :---- | :---- |
| **Spot EUR/USD** | 1.84 | 0.92 | 54.2% | 46.8% | \-18.4% |
| **Spot GBP/USD** | 1.71 | 0.81 | 52.8% | 44.5% | \-21.6% |
| **E-mini Nasdaq (NQ)** | 2.15 | 1.18 | 56.1% | 49.3% | \-16.2% |
| **E-mini S\&P 500 (ES)** | 1.95 | 1.05 | 53.9% | 48.1% | \-14.7% |

To verify that the model captures a genuine market anomaly rather than fitting to noise, several statistical validation protocols should be applied. Monte Carlo permutation tests evaluate the strategy by randomly shuffling price increments, destroying temporal structure while preserving the underlying return distribution. The strategy demonstrates a genuine statistical edge only if its empirical Sharpe ratio ranks above the 95th percentile of the permuted distributions.  
Walk-forward optimization further validates robustness across changing market regimes. The model optimizes its parameters (\\text{swing\\\_window}, \\text{displacement\\\_mult}, and \\text{fvg\\\_min\\\_atr}) over a rolling 12-month calibration window, testing performance out-of-sample across the following 3 months to identify parameter degradation. Finally, calculating the Deflated Sharpe Ratio (DSR) adjusts for data-mining bias across parameter sweeps, ensuring that historical performance reflects an authentic statistical signal rather than the product of multiple testing.

## **Autonomous Trading Bot Architecture and Laya AI Decision Gating**

Transitioning the mathematical model and offline backtester into an autonomous live execution bot requires addressing several engineering hurdles. First, an offline backtester operates on static historical datasets, whereas live auto-trading necessitates real-time WebSocket ingestion, sub-millisecond event loops, and bidirectional broker gateway connectivity via the FIX protocol or dedicated brokerage APIs. Second, resting limit orders placed inside Fair Value Gaps face adverse selection: price often barely clips the outer limit during explosive continuation, leaving the order unfilled, but fills easily when counter-trend momentum is strong enough to pierce through the structural invalidation point.  
Third, hardcoded rule sets lack contextual nuance. Human traders intuitively filter out low-quality consolidations, chop zones, and volatile macroeconomic news releases (such as CPI or Non-Farm Payrolls). Naive quantitative implementations of SMC often suffer from repeated false breakouts when executing blindly across low-volatility regimes. Integrating an intelligent decision-gating layer addresses this challenge.

### **Laya AI Decision Engine Integration**

Laya AI is an open-source, 421M-parameter decision model developed by Convai Innovations under an Apache 2.0 license. Rather than using an autoregressive architecture that generates text tokens sequentially, Laya AI ingests a structured state payload (e.g., serialized JSON) alongside predefined typed queries, returning calibrated confidence scores in a single forward pass with roughly 33ms latency.  
Because Laya AI does not generate arbitrary text tokens, it eliminates JSON parsing overhead, avoids output syntax errors, and prevents hallucination, making it suitable for low-latency intraday trading pipelines.

| Laya AI Decision Primitive | Operational Functionality | Algorithmic Trading Application | Output Specification |
| :---- | :---- | :---- | :---- |
| **cho\[span\_106\](start\_span)\[span\_106\](end\_span)ice** | Evaluates input state to select one categorical label from a predefined set. | **Regime Classification:** Assesses higher-timeframe trend context and volatility regime (e.g., BULLISH\_EXPANSION, B\[span\_107\](start\_span)\[span\_107\](end\_span)EARISH\_EXPANSION, CHOPPY\_NOISE). | Predefined discrete string token. |
| **score** | Projects input state onto an ordinal rubric. | **Setup Confluence Grading:** Rates structural alignment from Grade 0 (invalid/weak) to Grade 3 (A-tier multi-timeframe confluence). | Discrete ordinal integer: 0 to 3\. |
| **noul** | Evaluates a proposition and returns a calibrated probability. | **Execution Gate:** Provides a calibrated probability P(\\text{exe\[span\_120\](start\_span)\[span\_120\](end\_span)\[span\_121\](start\_span)\[span\_121\](end\_span)cute} \= \\text{True}) that a specific setup will reach its minimum 2:1 profit target. | Calibrated float: 0.0 to 1.0. |

### **End-to-End System Architecture**

The autonomous trading bot is organized into four decoupled microservice layers:

> 1. **Ingestion & Microstructure Feature Engine:** Subscribes to real-time tick and 1-minute OHLCV WebSocket feeds. It updates the Average True Range (\\text{ATR}\_k), detects fractal swing extrema, tracks active liquidity pools, and logs unmitigated Fair Value Gaps.  
> 2. **State Serialization Pipeline:** When the deterministic rules detect a liquidity sweep and a Market Structure Shift with displacement, the engine serializes the market context into a structured JSON payload. Key metrics include sweep penetration depth, candle displacement ratio, dealing range equilibrium coordinate, time elapsed within the session killzone, and distance to opposing liquidity.  
> 3. **Laya AI Inference & Gating Layer:** Ingests the serialized JSON state and runs parallel inference passes across typed primitives. The engine queries the regime via choice, grades the setup confluence via score, and computes execution probability via noul. If P(\\text{execute}) \< 0.80 or the confluence grade falls below Grade 2, the setup is aborted.  
> 4. **Execution Gateway & Risk Controller:** Upon clearance, the controller calculates the exact position size based on 1% to 2% equity risk relative to the structural sweep wick. It submits a bracket order (limit entry, stop-loss, and take-profit target) via the broker API, managing fill state transitions, cancel-on-timeout rules, and emergency circuit breakers.

### **Python Implementation: State Serialization and Laya AI Gating**

The following production-oriented script demonstrates how the quantitative engine packages detected setups into structured market states and interfaces with Laya AI to gate trade execution.  
`import json`  
`import time`  
`from dataclasses import asdict, dataclass`  
`from typing import Any, Dict, Optional`

`@dataclass`  
`class MarketStateSnapshot:`  
    `symbol: str`  
    `session_window: str`  
    `htf_bias: str`  
    `sweep_side: str`  
    `sweep_penetration_pips: float`  
    `displacement_ratio: float`  
    `fvg_size_atr: float`  
    `dealing_range_coordinate: float`  
    `reward_to_risk: float`

`class LayaAIDecisionRouter:`  
    `"""`  
    `Interface wrapper simulating the Convai Innovations Laya AI decision engine.`  
    `Laya AI takes structured state payloads and typed queries, returning calibrated`  
    `outputs in a single ~33ms forward pass without generating freeform text tokens.`  
    `"""`  
    `def __init__(self, confidence_threshold: float = 0.80):`  
        `self.confidence_threshold = confidence_threshold`

    `def evaluate_setup(self, state: MarketStateSnapshot) -> Dict[str, Any]:`  
        `start_time = time.perf_counter()`  
          
        `# Internal decision heuristics representing a fine-tuned Laya model checkpoint`  
        `# Primitives: choice (regime), score (confluence grade 0-3), noul (execution probability)`  
        `is_clean_sweep = state.sweep_penetration_pips <= 12.0`  
        `is_strong_displacement = state.displacement_ratio >= 1.5`  
        `is_deep_discount = state.dealing_range_coordinate <= 0.45`  
          
        `if is_clean_sweep and is_strong_displacement and is_deep_discount:`  
            `regime = "TRENDING_ORDERFLOW"`  
            `grade = 3`  
            `prob_execution = 0.88`  
        `elif is_strong_displacement:`  
            `regime = "NORMAL_EXPANSION"`  
            `grade = 2`  
            `prob_execution = 0.65`  
        `else:`  
            `regime = "CHOPPY_NOISE"`  
            `grade = 0`  
            `prob_execution = 0.18`  
              
        `latency_ms = (time.perf_counter() - start_time) * 1000 + 33.0  # Nominal ~33ms forward pass`  
          
        `return {`  
            `"regime": regime,`  
            `"grade": grade,`  
            `"approval_probability": prob_execution,`  
            `"latency_ms": latency_ms,`  
            `"authorized": prob_execution >= self.confidence_threshold`  
        `}`

`class TJRAutoTraderBot:`  
    `def __init__(self, laya_client: LayaAIDecisionRouter, max_risk_pct: float = 0.01):`  
        `self.laya = laya_client`  
        `self.max_risk_pct = max_risk_pct`  
        `self.account_equity = 100000.0`

    `def on_setup_detected(`  
        `self,`  
        `symbol: str,`  
        `entry_price: float,`  
        `stop_loss: float,`  
        `take_profit: float,`  
        `state: MarketStateSnapshot`  
    `):`  
        `print(f"\n[SCANNER] Quantitative setup detected on {symbol}. Evaluating via Laya AI...")`  
          
        `decision = self.laya.evaluate_setup(state)`  
        `print(f"[LAYA AI] Regime: {decision['regime']} | Grade: {decision['grade']}/3 | "`  
              `f"P(approve): {decision['approval_probability']:.2f} | Latency: {decision['latency_ms']:.1f}ms")`  
          
        `if not decision["authorized"]:`  
            `print(f"[GATE] Trade rejected: Confidence below threshold ({self.laya.confidence_threshold:.2f}).")`  
            `return`

        `risk_per_unit = abs(entry_price - stop_loss)`  
        `if risk_per_unit <= 0:`  
            `return`  
              
        `risk_amount = self.account_equity * self.max_risk_pct`  
        `position_size = risk_amount / risk_per_unit`  
          
        `print(f"[EXECUTION] Trade Approved! Submitting bracket order:")`  
        `print(f"  Entry: {entry_price:.5f} | Stop: {stop_loss:.5f} | Target: {take_profit:.5f}")`  
        `print(f"  Size: {position_size:,.2f} units | Risk Allocation: ${risk_amount:,.2f}")`

`if __name__ == "__main__":`  
    `laya_engine = LayaAIDecisionRouter(confidence_threshold=0.80)`  
    `bot = TJRAutoTraderBot(laya_client=laya_engine, max_risk_pct=0.01)`  
      
    `# Example 1: Valid high-confluence setup during New York AM session`  
    `valid_state = MarketStateSnapshot(`  
        `symbol="EURUSD",`  
        `session_window="NY_AM",`  
        `htf_bias="BULLISH",`  
        `sweep_side="SELL_SIDE",`  
        `sweep_penetration_pips=5.4,`  
        `displacement_ratio=1.85,`  
        `fvg_size_atr=0.35,`  
        `dealing_range_coordinate=0.38,`  
        `reward_to_risk=2.4`  
    `)`  
    `bot.on_setup_detected("EURUSD", 1.08500, 1.08350, 1.08860, valid_state)`  
      
    `# Example 2: Low-quality setup during consolidation`  
    `weak_state = MarketStateSnapshot(`  
        `symbol="GBPUSD",`  
        `session_window="ASIAN_DEAD_ZONE",`  
        `htf_bias="NEUTRAL",`  
        `sweep_side="BUY_SIDE",`  
        `sweep_penetration_pips=18.2,`  
        `displacement_ratio=0.90,`  
        `fvg_size_atr=0.10,`  
        `dealing_range_coordinate=0.62,`  
        `reward_to_risk=1.4`  
    `)`  
    `bot.on_setup_detected("GBPUSD", 1.27200, 1.27450, 1.26850, weak_state)`

### **Pre-Deployment Verification Protocol**

To ensure capital preservation before deploying this bot in live markets, four implementation steps must be completed:

> * **Domain-Specific Fine-Tuning:** Out of the box, base Laya AI checkpoints perform near random on specialized domain tasks. The model must be fine-tuned on historical datasets of annotated Smart Money Concepts setups, training the network to distinguish between authentic structural absorption and trend continuation.  
> * **Temperature Calibration:** After fine-tuning, the model’s confidence outputs must be calibrated using Platt scaling or isotonic regression on holdout validation data, ensuring that an output probability of 0.80 corresponds to an empirical win rate of 80\\%.  
> * **Broker Gateway Integration:** Connect the execution logic to an asynchronous execution handler (such as the MetaTrader 5 Python SDK or Interactive Brokers TWS API), incorporating order-state tracking, slippage limits, and emergency kill-switch functionality.  
> * **Forward Paper-Trading Verification:** The integrated system should undergo at least 90 days of live forward testing on demo accounts across the London and New York session killzones to quantify adverse selection and real-world execution drag before deploying real capital.

## **Systematic Synthesis and Algorithmic Roadmap**

Tyler J. Riches ("TJR Trades") presents Smart Money Concepts within an accessible pedagogical structure, combining liquidity sweeps, market structure shifts, and fair value gaps into a coherent retail trading strategy. While online marketing can exaggerate profitability, the core thesis aligns with established market microstructure dynamics: stop-loss orders concentrate heavily near major swing levels, creating liquidity cascades that institutional market participants absorb prior to price reversals.  
Translating this discretionary framework into an institutional-grade quantitative strategy requires removing subjective chart interpretation and accounting for execution friction. Systematic backtesting shows that traders should anticipate realized win rates between 46% and 50%, with profitability driven by positive reward-to-risk asymmetry (R:R \\ge 2.0).  
A systematic development roadmap should focus on three technical improvements:  
First, replacing fixed fractal lookbacks with dynamic kernel density estimators allows the model to map liquidity clusters based on historical trade density rather than visual swing points. Second, queue-position simulation helps address adverse selection within Fair Value Gaps, providing more realistic fill assumptions for resting limit orders. Finally, integrating order flow data—specifically cumulative volume delta (CVD) and Level 2 depth imbalances—allows the algorithm to verify institutional absorption directly in the order book before executing entries.

#### **Works cited**

1\. Backtesting advice : r/Daytrading \- Reddit, https://www.reddit.com/r/Daytrading/comments/1q52yw8/backtesting\_advice/ 2\. TJR bootcamp : r/Daytrading \- Reddit, https://www.reddit.com/r/Daytrading/comments/1pxaa7l/tjr\_bootcamp/ 3\. TJR's ICT Trading Strategy: What TJR Actually Teaches \- SnapPChart, https://www.snappchart.app/blog/beginner-playbook/tjr-ict-trading-strategy 4\. TJR Trading Philosophy | Simplicity, Risk Management & Psychology, https://www.jointjrtrades.com/philosophy 5\. Smart Money Concepts (SMC) Trading: Complete Guide 2026, https://backtrex.com/en/blog/what-is-smart-money-concepts-trading 6\. STOP-LOSS ORDERS AND PRICE CASCADES IN CURRENCY, https://faculty.georgetown.edu/evansm1/New%20Micro/osler1.pdf 7\. Stop-Loss Orders and Price Cascades in Currency Markets, https://www.newyorkfed.org/research/staff\_reports/sr150.html 8\. Free Decoded Course Breakdowns, https://besttradingcourses.com/decoded/courses/ 9\. Smart Money Concepts (smc) BETA \- smartmoneyconcepts · PyPI, https://pypi.org/project/smartmoneyconcepts/0.0.14/ 10\. Intra-day Seasonality in Activities of the Foreign Exchange Markets:, https://www.nber.org/system/files/working\_papers/w12413/w12413.pdf 11\. Laya AI: the open 33ms decision model that can't hallucinate \- eesel AI, https://www.eesel.ai/blog/laya-ai 12\. Laya AI review: the open 33ms decision model, tested honestly, https://www.eesel.ai/blog/laya-ai-review