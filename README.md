# zerodha-algo

The strategy from [Zerodha Varsity — *Build a Trading Algo with AI, No Coding
Required*](https://www.youtube.com/watch?v=V9Ra8klDzrM), built properly and then
tested honestly.

The video builds a Nifty 100 SMA(6)/SMA(30) crossover scanner ranked by
crossover recency. It builds it well. What it never does is ask whether the
signal makes money. This repo answers that.

**It does not.** Over 2011–2026, with next-open fills, real Zerodha charges,
25 bps of slippage and a point-in-time universe, the rule returned **1.9% CAGR
against the Nifty 100 total-return index's 10.7%** — with a deeper drawdown and
a negative Sharpe. It also loses to random entries of the same length in the
same universe. See [out/REPORT.md](out/REPORT.md).

---

## What is here

```
src/
  config.py      cost model (Zerodha charges) + execution & portfolio assumptions
  data.py        yfinance daily OHLCV, cached; split-adjusted, dividend-unadjusted
  strategy.py    causal SMA crossover state + the scanner table
  universe.py    survivorship-biased vs point-in-time universe construction
  backtest.py    event-ordered day loop; fills at next open, never same bar
  metrics.py     CAGR, Sharpe, Sortino, drawdown, trade stats
  orders.py      turns a scan into a reviewable order sheet (never places one)
  broker_charges.py  prices orders through Kite instead of modelling them
  trading_calendar.py real NSE/BSE sessions, so a holiday is not a data gap
  overfitting.py deflated Sharpe, PBO, minimum backtest length, SPA
tests/
  test_no_leak.py        proves the engine cannot see the future
  test_leak_coverage.py  the four leaks that suite used to miss
  test_mutants.py        injects 8 real leaks; all must be caught
  test_charges.py        the cost model, against Zerodha's own contract note
  test_calendar.py       the panel's dates, against the real NSE calendar
run_scanner.py      today's signals -> out/scan.html + out/scan.csv
run_backtest.py     the honesty ladder, S0 -> S3
run_walkforward.py  walk-forward parameter selection + the full grid
make_report.py      charts
.claude/skills/daily-scan/   slash-command workflow for the live daily run
```

## Setup

```bash
pip install -r requirements.txt
python fetch_data.py --list nifty500 --start 2010-01-01   # ~4 min, one time
```

No Node, no React, no FastAPI, no Kite Connect subscription. The video needs all
four; none of them are load-bearing for a daily-timeframe scanner.

## Use

```bash
python run_scanner.py --refresh --lookback 10   # today's ranked signals
python verify_charges.py                        # cost model vs the broker
python make_tearsheet.py                        # standard QuantStats report
python run_backtest.py --end 2026-08-22         # the honesty ladder
python run_walkforward.py --train-years 3 --end 2026-08-22
python -m pytest tests/ -v                      # prove there is no leak
```

## Running it against your Zerodha account

The Kite MCP (`https://mcp.kite.trade/mcp`, which you add yourself with
`claude mcp add --transport http kite https://mcp.kite.trade/mcp`) gives Claude
read access to your holdings, positions, margins and live quotes, plus order
placement. The intended loop is **semi-automated, exactly as the video
recommends**:

1. You ask for the daily scan. Claude runs `run_scanner.py`.
2. Claude reads your holdings and cash over MCP and builds an order sheet.
3. Claude shows you the sheet and stops.
4. You approve specific rows. Only then does Claude place those orders.

`.claude/skills/daily-scan/SKILL.md` encodes that loop, including the rule that
a bearish signal is an exit and never a short — retail equity delivery in India
cannot be sold short overnight, so the "bearish" half of the video's signal is
not tradeable as a short at all.

## Why you should not trust a backtest that has not done this

Four things separate a number you can act on from a number that flatters you.
The repo measures each one rather than asserting it:

| | assumption | CAGR |
|---|---|---|
| S0 | naive: fills at the signal bar's close, no costs, today's Nifty 100 | 12.68% |
| S1 | fills moved to the next session's open | 12.47% |
| S2 | + Zerodha charges, STT, stamp duty, GST, DP, 25bps slippage | 5.85% |
| S3 | + point-in-time universe instead of today's index members | **1.92%** |
| | same universe, no timing rule at all | **13.65%** |
| | Nifty 100 TRI, net of a 25bps index-fund fee | **10.70%** |

Costs took 6.6 points a year. Survivorship bias took another 3.9. Fill
timing, on this slow a signal, was worth 0.2 — which is itself worth knowing,
because it is the leak everyone talks about and the smallest one here.

Two further nulls, both of which the rule fails:

- **Same universe, no timing at all** returns 13.65%. The stock pool was fine;
  adding the crossover destroyed **11.74 points a year**. (This null used to be
  quoted at 11.90% because it was measured on price return alone while the
  strategy collected dividends and the benchmark was a total-return index. It
  was the only one of the three measured differently, and the error flattered
  the strategy by understating what it was losing to. Fixed in src/nulls.py.)
- **Random entries** with the same trade count and holding-period distribution
  average +0.461% per trade against the strategy's +0.284%. It sits at the 28th
  percentile of that null. Holding the entry *dates* fixed and randomising only
  the stock still beats it (29th percentile), so neither half of the signal
  carries information.

Across the 41-pair parameter grid, `corr(CAGR, log trade count) = -0.86`. The
surface measures how little each pair traded, not how well it predicted. The
video's 6/30 ranks 33rd of 41.

### One more number that depends on an arbitrary choice

Signals are ranked by crossover recency, as in the video. But recency does not
actually rank them: on most days every fresh crossover ties at `bars_since == 0`,
and what decides who gets the free slots is the **tiebreak** — which was a bare
`cands.sort()`, i.e. alphabetical by symbol. Arbitrary, undocumented, and
quietly kind to names beginning with A.

Publishing a figure that turns on a coin-flip without saying how much it turns
on it is exactly what this repo exists to complain about, so it is measured
(`run_backtest.py` prints it; `out/summaries.json` stores it):

| tiebreak | CAGR | Sharpe | trades |
|---|---|---|---|
| turnover (most liquid first) | 0.66% | -0.284 | 1,584 |
| **alpha (shipped default)** | **1.92%** | **-0.202** | **1,574** |
| spread (widest cross first) | 2.44% | -0.159 | 1,529 |
| reverse alphabetical | 2.66% | -0.152 | 1,567 |

**A two-point band on a 1.92% headline.** The shipped default sits inside it,
not at either end. Every one of the four loses to the index's 10.70% and to
13.65% for holding the same stocks, so the conclusion is not sensitive to the
choice — but the headline figure is, and quoting it to two decimals without
this table would overstate how precise it is.

## How the no-leak claim is enforced

`tests/test_no_leak.py` makes three independent arguments:

- **Truncation** — signals computed on `data[:t]` equal signals computed on the
  full sample and sliced to `t`.
- **Future scramble** — replace every bar after date `T` with noise; the equity
  curve up to `T` must be bit-identical. This catches leakage through channels
  the author did not think of.
- **Fill timing** — every entry price equals the *next* session's open moved by
  exactly the slippage assumption, and the signal that caused it fired strictly
  earlier.

Plus: the universe is causal under truncation, and turning costs off must
improve results (otherwise the cost model is not actually wired in).

### How much that is actually worth

A green suite proves nothing by itself, so the tests are themselves tested.
`tests/test_mutants.py` injects eight real lookahead bugs — one at a time, into
a throwaway copy of the repo — and **requires the suite to go red for every
one**.

That check exists because the first time it ran, **four of the eight survived**
against the original 15 tests. The misses were not random. Every future-scramble
test passed `all_true_membership()`, so the point-in-time universe builder — the
component most likely to leak — was excluded from the strongest test in the
suite. The synthetic fixture was a smooth random walk with no gaps, no missing
bars, uniform volume and zero dividends, so the circuit-band, illiquidity and
dividend branches never executed and a leak inside any of them could not move a
single number. And trades were compared without quantity, so a position sized
from a price that was not knowable at fill time left every assertion untouched.

`tests/test_leak_coverage.py` closes those four. The mutation suite is what
keeps them closed:

| injected leak | now caught by |
|---|---|
| fill at the signal bar's close | fill timing |
| fill at a close only known after the session ends | fill timing |
| centered moving average | truncation |
| SMA shifted one bar forward | truncation |
| universe on partly-future turnover | universe causal at 8 cuts |
| universe ranked on turnover it has not seen | universe under scramble |
| circuit-band reference price from tomorrow | scramble on hostile data |
| position sized from the fill price | trade quantities under scramble |

**What this does not claim.** It says those eight channels are closed and that
the suite detects them. It is not a proof that no other leak exists — only that
every leak anyone has thought to write down is caught. If you find one that
survives, that is a real finding: add it to `MUTATIONS` and it becomes part of
the guarantee.

Two mutations that survived early turned out to be errors in the *mutation*,
not gaps in the suite — `expanding(min_periods=1)` reads only rows `0..t` and is
causal, and `cl.at[t]` is legitimately known when an order is queued at the
close of `t`. Both are documented inline, because a mutation that is not really
a leak proves nothing when it survives and quietly flatters the suite when it
dies.

## Reproducing this from a clean clone

The price cache is 125 MB and is not in the repo. Everything in `out/` is,
so you can check every number before deciding whether to spend the time.

```bash
pip install -r requirements.txt
python fetch_data.py --list nifty500 --start 2010-01-01   # ~4 min, one time
python run_backtest.py --end 2026-08-22                   # ~2 min
python run_walkforward.py --train-years 3 --end 2026-08-22
python -m pytest tests/ -q                                # ~25s
python -m pytest tests/test_mutants.py -q                 # ~2.5 min
```

`--end` is pinned on purpose. It defaults to today, so without it the window
grows every day and stops matching the published figures — `out/summaries.json`
records the exact arguments behind them.

Expect small differences anyway. Prices come from Yahoo rather than NSE
bhavcopy, and Yahoo revises history, so re-pulling the same window will move
these figures. By how much has not been measured — an earlier draft quoted "a
few tenths of a point" and that number traced to nothing on disk, so it is gone.
The conclusion here is an 8.8-point gap, which is far larger than any plausible
revision, but do not expect the fourth digit to match.

## Licence

MIT — see [LICENSE](LICENSE).

## Was the winner skill, or the luckiest of 41?

Sweeping 41 parameter pairs and reporting the winner is the textbook way to
manufacture an edge. The maximum of 41 noisy Sharpe ratios is biased upward
even when every pair is worthless, and the size of that bias is computable.
`run_walkforward.py` now reports it (`src/overfitting.py`, artifacts in
`out/overfitting.json` and `out/grid_returns.csv`):

```
  trials searched          41          (38 effective — the pairs overlap heavily)
  best pair                SMA 20/200, Sharpe 0.882
  deflated Sharpe          0.937       selection-adjusted
  P(Sharpe > 0)            1.000
  min backtest length      4.7 years   to trust Sharpe 1.0 after 41 trials
  P(backtest overfitting)  0.022       below 0.5, so selection is not the problem
  SPA p-value              0.578       no skill survives the search
```

Read together these say something sharper than the original claim. The grid
search is **not** where this fails: PBO of 0.022 means the in-sample winner
almost never lands in the bottom half out-of-sample, and 15.6 years is well past
the 4.7 needed for 41 trials. What fails is the whole family. Hansen's SPA
cannot reject the hypothesis that the best of all 41 pairs has no superiority
over simply holding the index, at p = 0.58.

Honest tuning still loses. Walk-forward selection returned 11.21% against the
benchmark's 12.86% on the same out-of-sample window, and the pair that looked
best in hindsight beat honest selection by 4.62 points a year — which is a
measurement of how much of a backtest is hindsight.

## Charges are priced, not modelled

`src/config.py` transcribes Zerodha's rates by hand, and the loudest finding
here — charges took 95% of gross trading gains — rests entirely on that
transcription. `verify_charges.py` prices the same orders through Kite's
`/charges/orders` endpoint, which returns the broker's own arithmetic for
orders that do not exist, and fails if the model disagrees by more than half a
basis point of turnover.

```bash
set KITE_API_KEY=...
set KITE_ACCESS_TOKEN=...
python verify_charges.py --save
```

`tests/test_charges.py` runs without credentials too, pinning the properties
that must hold whatever the statutory rates are this year: GST applies to
brokerage, exchange and SEBI charges but never to STT; stamp duty is buy-side
only; delivery costs scale linearly; and the DP charge is per scrip per sell
day, not per order — which is why comparing against a per-order contract note
needs `include_dp=False`.

## The calendar, and what it says about the data

`src/trading_calendar.py` uses the real XBOM session calendar instead of
inferring trading days from whether a bar exists. Auditing the 15-year panel
against it found the vendor and the exchange disagree on about 24 days out of
4,103 — and the disagreements are almost all **Diwali Muhurat** sessions, the
ceremonial one-hour sessions that land on whatever day Diwali falls, including
Saturdays and Sundays. That is 0.6% of sessions at minimal volume, so it does
not move any headline number, and it is pinned in `tests/test_calendar.py` so a
genuinely new data problem cannot hide inside a known one.
