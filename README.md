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
tests/
  test_no_leak.py        proves the engine cannot see the future
  test_leak_coverage.py  the four leaks that suite used to miss
  test_mutants.py        injects 8 real leaks; all must be caught
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
| | same universe, no timing rule at all | 11.90% |
| | Nifty 100 TRI, net of a 25bps index-fund fee | **10.70%** |

Costs took 6.6 points a year. Survivorship bias took another 3.9. Fill
timing, on this slow a signal, was worth 0.2 — which is itself worth knowing,
because it is the leak everyone talks about and the smallest one here.

Two further nulls, both of which the rule fails:

- **Same universe, no timing at all** returns 11.90%. The stock pool was fine;
  adding the crossover destroyed ten points a year.
- **Random entries** with the same trade count and holding-period distribution
  average +0.461% per trade against the strategy's +0.284%. It sits at the 28th
  percentile of that null. Holding the entry *dates* fixed and randomising only
  the stock still beats it (29th percentile), so neither half of the signal
  carries information.

Across the 41-pair parameter grid, `corr(CAGR, log trade count) = -0.86`. The
surface measures how little each pair traded, not how well it predicted. The
video's 6/30 ranks 33rd of 41.

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
bhavcopy, and re-pulling the same window moves the 15-year CAGR by a few tenths
of a point. The conclusion here is an 8.8-point gap, which is many times larger
than that noise, but do not expect the fourth digit to match.

## Licence

MIT — see [LICENSE](LICENSE).
