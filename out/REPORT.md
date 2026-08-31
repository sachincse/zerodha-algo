# Does the Varsity SMA(6)/SMA(30) scanner make money?

Nifty 100 universe, daily bars, 2011-01-03 to 2026-08-21 (15.63 years). Long only, 10 equal-weight slots, entries at the next session's open, full Zerodha delivery charges, 25 bps per side slippage, point-in-time universe.

**No.**

It returned **1.92% CAGR** against **10.70%** for the Nifty 100 total-return index net of an index-fund fee - a shortfall of **8.78 points a year** - with a deeper maximum drawdown (-42.13% vs -37.95%) and a negative Sharpe (-0.20).

## The honesty ladder

The same rule, run four times, removing one convenient assumption at a time. This is how you tell an edge from an artefact.

| | assumption removed | CAGR | Sharpe | max DD |
|---|---|---|---|---|
| S0 | nothing - same-bar close fill, zero costs, today's index members | 12.68% | 0.45 | -28.07% |
| S1 | fills moved to the next session's open | 12.47% | 0.44 | -26.86% |
| S2 | + Zerodha charges, STT, stamp, GST, DP, 25bps slippage | 5.85% | 0.03 | -32.34% |
| S3 | + point-in-time universe instead of today's index | 1.92% | -0.20 | -42.13% |
| - | **same universe, no timing at all** | **13.65%** | 0.44 | -44.85% |
| - | **NIFTY100_TRI_net** | **10.70%** | 0.32 | -37.95% |

Costs took **6.62 points a year**. Survivorship bias took another **3.94**. Fill timing - the leak everyone warns about - moved it by **0.20**, which on a signal this slow is close to nothing. The famous leak is the smallest one here; the two that actually matter are the two nobody mentions.

## The universe was fine. The rule was the problem.

Equal-weighting the *same* point-in-time universe with no timing whatsoever, paying the same charges, returned **13.65% CAGR** - comfortably ahead of the index. So the stock selection was not the issue. Adding the crossover rule on top took it from 13.65% down to 1.92%. **The timing rule destroyed 11.74 points a year.**

## It does not beat random entries

The correct null for a timing rule is not zero and not buy-and-hold - it is *random timing at the same exposure*. Keeping the strategy's own 1574 trades and its exact holding-period distribution, but drawing entry dates and symbols at random from the same universe, over 1000 bootstrap runs:

- strategy mean net trade: **+0.284%**
- random-entry null: **+0.461%** (5th to 95th percentile -0.012% .. +0.924%)
- the strategy sits at the **28th percentile** of that distribution

Throwing darts would have done better than following the crossover. The signal carries no information the market has not already used; what it reliably produces is turnover.

## Slippage sensitivity

| slippage per side | CAGR | Sharpe | max DD |
|---|---|---|---|
| 5 bps | 5.70% | 0.03 | -33.39% |
| 25 bps | 1.92% | -0.20 | -42.13% |
| 50 bps | -2.51% | -0.48 | -54.17% |

Even at an implausibly generous 5 bps - achievable only if every order lands inside the pre-open call auction - the rule still loses to the index by a wide margin.

## Tuning the parameters does not rescue it

A 13-window walk-forward: three years train, one year test, the SMA pair re-chosen each year on training-period Sharpe alone.

**These numbers cover a different window from the ladder above, which is why the same rows carry different values.** Three years are consumed by the first training fold, so this table begins at the fourth year while the ladder begins at 2011-01-01. Fixed 6/30 and the benchmark differ between the two tables for that reason alone. Compare rows within a table, never across them.

| | CAGR | Sharpe | max DD |
|---|---|---|---|
| walk-forward tuned (honest) | 11.21% | 0.34 | -27.05% |
| fixed 6/30 (the video) | 3.28% | -0.11 | -42.13% |
| best pair in hindsight (20/200) | 15.83% | - | - |
| NIFTY100_TRI_net | 12.86% | - | - |

The gap between the hindsight-best pair and the walk-forward result is **4.62 points a year of pure overfitting** - edge that exists only because the parameter was chosen after seeing the answer.

Year by year, the pair that won the training window and what it then delivered:

| test year | pair chosen on training data | realised that year |
|---|---|---|
| 2014 | SMA 10/200 | +62.50% |
| 2015 | SMA 3/200 | +5.26% |
| 2016 | SMA 8/200 | -5.53% |
| 2017 | SMA 20/200 | +46.56% |
| 2018 | SMA 20/200 | -1.99% |
| 2019 | SMA 20/200 | +7.87% |
| 2020 | SMA 10/200 | +16.52% |
| 2021 | SMA 6/200 | +9.89% |
| 2022 | SMA 15/100 | +1.03% |
| 2023 | SMA 6/40 | +13.48% |
| 2024 | SMA 15/200 | +11.36% |
| 2025 | SMA 3/200 | -5.87% |
| 2026 | SMA 3/200 | -1.01% |

## What was actually modelled

- **Charges**: zero delivery brokerage, STT 0.1% both sides, NSE transaction 0.00307%, SEBI Rs 10/crore, stamp 0.015% on buy, GST 18% on (brokerage + transaction + SEBI), DP Rs 15.34 per scrip per sell day (GST already included). About 24 bps round trip on a Rs 1 lakh position. Total paid: **Rs 323,801** on a Rs 1,000,000 book.
- **Dividends**: Rs 231,568. Summed over each holding period and paid as a lump at EXIT, uncompounded; a position still open on the last bar forgoes them. Both simplifications are conservative. (The buy-and-hold null credits on the ex-date, which is why it is the more generous of the two comparisons.)
- **Long only.** A retail account cannot hold a short equity position overnight in India: SEBI bans naked shorts and delivery must be honoured at T+1. The bearish half of the video's signal is an exit, not a trade.
- **Unfilled orders**: 14 lapsed - no bar, zero volume, or the open gapped to the circuit band. A blocked entry drops the signal; a blocked exit holds the position and retries.
- **Trades**: 1574, win rate 32%, median hold 25 days, profit factor 1.05, average exposure 0.92.

## What is still wrong with this backtest

Stated so you can discount the result yourself - and note that every one of these points the same way, against the strategy:

- **Residual survivorship bias.** The point-in-time universe is drawn from symbols that are *currently listed*. Companies delisted or merged away over the period are absent, and they were disproportionately the losers. The true number is worse than the one above, not better.
- **Free data.** Prices come from Yahoo, not NSE bhavcopy, and Yahoo revises history, so re-pulling the same window will move these figures. How much has never been measured, so no error bar is quoted: an earlier draft claimed 'roughly 0.3 points' and that number traced to nothing on disk. To measure it, re-run fetch_data.py --refresh into a copy of data/cache/, re-run the backtest, and diff the two summaries.json. What IS measured is the tiebreak band, and the 8.78-point shortfall is far larger than any plausible revision.
- **Fills at the printed open.** NSE's daily open is the pre-open call auction price, and a retail market order is not guaranteed to be inside that auction. The 25 bps assumption stands in for that uncertainty; measured first-minute moves on Nifty 100 names have a median of about 21 bps and a 90th percentile near 60.
- **Circuit bands are approximated** by a flat 10% gap test rather than the real per-scrip dynamic band, ASM/GSM surveillance state and series restrictions.
- **No taxes.** Short-term capital gains would take a further bite out of a strategy with a 25-day median holding period, and would not touch a buy-and-hold benchmark.

## Reproduce

```bash
python -m pytest tests/ -q         # causality suite + mutation tests
python fetch_data.py --list nifty500 --start 2010-01-01
python fetch_benchmark.py --end 2026-08-22
python run_backtest.py --end 2026-08-22
python run_walkforward.py --train-years 3 --end 2026-08-22
```