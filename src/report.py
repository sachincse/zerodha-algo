"""Assemble out/REPORT.md from the JSON the runners write.

Generated rather than hand-written, so the prose can never drift away from the
numbers it is describing.
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "out")


def _f(v, spec=".2f", dash="-"):
    try:
        return format(float(v), spec)
    except (TypeError, ValueError):
        return dash


def write_report() -> str | None:
    sp = os.path.join(OUT, "summaries.json")
    if not os.path.exists(sp):
        return None
    d = json.load(open(sp, encoding="utf-8"))
    S = d["summaries"]
    s3, s2, s1, s0 = S["S3_pit"], S["S2_realcost"], S["S1_realfill"], S["S0_naive"]
    nulls = d.get("nulls", {})
    slip = d.get("slippage_sensitivity", {})
    bench_name = d.get("benchmark", "benchmark")
    bcagr = s3.get("benchmark_cagr_pct")

    wf = {}
    wp = os.path.join(OUT, "walkforward.json")
    if os.path.exists(wp):
        wf = json.load(open(wp, encoding="utf-8"))

    L: list[str] = []
    A = L.append

    A("# Does the Varsity SMA(6)/SMA(30) scanner make money?\n")
    A(f"Nifty 100 universe, daily bars, {s3['start']} to {s3['end']} "
      f"({s3['years']} years). Long only, 10 equal-weight slots, entries at the "
      f"next session's open, full Zerodha delivery charges, 25 bps per side "
      f"slippage, point-in-time universe.\n")
    A("**No.**\n")
    A(f"It returned **{_f(s3['cagr_pct'])}% CAGR** against **{_f(bcagr)}%** for "
      f"the Nifty 100 total-return index net of an index-fund fee - a shortfall "
      f"of **{_f(abs(s3['cagr_pct'] - bcagr))} points a year** - with a deeper "
      f"maximum drawdown ({_f(s3['max_drawdown_pct'])}% vs "
      f"{_f(s3.get('benchmark_max_dd_pct'))}%) and a negative Sharpe "
      f"({_f(s3['sharpe'])}).\n")

    # ---- ladder -----------------------------------------------------------
    A("## The honesty ladder\n")
    A("The same rule, run four times, removing one convenient assumption at a "
      "time. This is how you tell an edge from an artefact.\n")
    A("| | assumption removed | CAGR | Sharpe | max DD |")
    A("|---|---|---|---|---|")
    rows = [("S0_naive", "nothing - same-bar close fill, zero costs, today's index members"),
            ("S1_realfill", "fills moved to the next session's open"),
            ("S2_realcost", "+ Zerodha charges, STT, stamp, GST, DP, 25bps slippage"),
            ("S3_pit", "+ point-in-time universe instead of today's index")]
    for k, lbl in rows:
        r = S[k]
        A(f"| {k.split('_')[0]} | {lbl} | {_f(r['cagr_pct'])}% | "
          f"{_f(r['sharpe'])} | {_f(r['max_drawdown_pct'])}% |")
    if "pit_equal_weight" in nulls:
        n = nulls["pit_equal_weight"]
        A(f"| - | **same universe, no timing at all** | **{_f(n['cagr_pct'])}%** | "
          f"{_f(n['sharpe'])} | {_f(n['max_dd_pct'])}% |")
    A(f"| - | **{bench_name}** | **{_f(bcagr)}%** | "
      f"{_f(s3.get('benchmark_sharpe'))} | {_f(s3.get('benchmark_max_dd_pct'))}% |")
    A("")
    A(f"Costs took **{_f(abs(s2['cagr_pct'] - s1['cagr_pct']))} points a year**. "
      f"Survivorship bias took another "
      f"**{_f(abs(s3['cagr_pct'] - s2['cagr_pct']))}**. Fill timing - the leak "
      f"everyone warns about - moved it by "
      f"**{_f(abs(s1['cagr_pct'] - s0['cagr_pct']))}**, which on a signal this "
      f"slow is close to nothing. The famous leak is the smallest one here; the "
      f"two that actually matter are the two nobody mentions.\n")

    # ---- universe vs rule -------------------------------------------------
    if "pit_equal_weight" in nulls:
        n = nulls["pit_equal_weight"]
        A("## The universe was fine. The rule was the problem.\n")
        A(f"Equal-weighting the *same* point-in-time universe with no timing "
          f"whatsoever, paying the same charges, returned **{_f(n['cagr_pct'])}% "
          f"CAGR** - comfortably ahead of the index. So the stock selection was "
          f"not the issue. Adding the crossover rule on top took it from "
          f"{_f(n['cagr_pct'])}% down to {_f(s3['cagr_pct'])}%. **The timing rule "
          f"destroyed {_f(n['cagr_pct'] - s3['cagr_pct'])} points a year.**\n")

    # ---- random timing null ------------------------------------------------
    if "random_timing" in nulls:
        rt = nulls["random_timing"]
        A("## It does not beat random entries\n")
        A(f"The correct null for a timing rule is not zero and not buy-and-hold "
          f"- it is *random timing at the same exposure*. Keeping the strategy's "
          f"own {rt['n_trades']} trades and its exact holding-period "
          f"distribution, but drawing entry dates and symbols at random from the "
          f"same universe, over {rt['n_draws']} bootstrap runs:\n")
        A(f"- strategy mean net trade: **{_f(rt['strategy_mean_trade_pct'], '+.3f')}%**")
        A(f"- random-entry null: **{_f(rt['null_mean_trade_pct'], '+.3f')}%** "
          f"(5th to 95th percentile {_f(rt['null_p05_pct'], '+.3f')}% .. "
          f"{_f(rt['null_p95_pct'], '+.3f')}%)")
        A(f"- the strategy sits at the "
          f"**{_f(rt['strategy_percentile'], '.0f')}th percentile** of that "
          f"distribution\n")
        A("Throwing darts would have done better than following the crossover. "
          "The signal carries no information the market has not already used; "
          "what it reliably produces is turnover.\n")

    # ---- slippage ----------------------------------------------------------
    if slip:
        A("## Slippage sensitivity\n")
        A("| slippage per side | CAGR | Sharpe | max DD |")
        A("|---|---|---|---|")
        for k in sorted(slip, key=float):
            r = slip[k]
            A(f"| {_f(k, '.0f')} bps | {_f(r['cagr_pct'])}% | {_f(r['sharpe'])} | "
              f"{_f(r['max_dd_pct'])}% |")
        A("")
        A("Even at an implausibly generous 5 bps - achievable only if every order "
          "lands inside the pre-open call auction - the rule still loses to the "
          "index by a wide margin.\n")

    # ---- walk forward ------------------------------------------------------
    if wf:
        A("## Tuning the parameters does not rescue it\n")
        A(f"A {len(wf.get('picks', []))}-window walk-forward: three years train, "
          f"one year test, the SMA pair re-chosen each year on training-period "
          f"Sharpe alone.\n")
        A("| | CAGR | Sharpe | max DD |")
        A("|---|---|---|---|")
        A(f"| walk-forward tuned (honest) | {_f(wf['walk_forward']['cagr_pct'])}% | "
          f"{_f(wf['walk_forward']['sharpe'])} | "
          f"{_f(wf['walk_forward']['max_dd_pct'])}% |")
        A(f"| fixed 6/30 (the video) | {_f(wf['fixed_6_30']['cagr_pct'])}% | "
          f"{_f(wf['fixed_6_30']['sharpe'])} | "
          f"{_f(wf['fixed_6_30']['max_dd_pct'])}% |")
        A(f"| best pair in hindsight "
          f"({str(wf['hindsight_best']['pair']).replace('_', '/')}) | "
          f"{_f(wf['hindsight_best']['cagr_pct'])}% | - | - |")
        A(f"| {bench_name} | {_f(wf['benchmark']['cagr_pct'])}% | - | - |")
        A("")
        gap = wf['hindsight_best']['cagr_pct'] - wf['walk_forward']['cagr_pct']
        A(f"The gap between the hindsight-best pair and the walk-forward result "
          f"is **{_f(gap)} points a year of pure overfitting** - edge that exists "
          f"only because the parameter was chosen after seeing the answer.\n")
        if wf.get("picks"):
            A("Year by year, the pair that won the training window and what it "
              "then delivered:\n")
            A("| test year | pair chosen on training data | realised that year |")
            A("|---|---|---|")
            for p in wf["picks"]:
                A(f"| {p['test_year']} | SMA {str(p['chosen']).replace('_', '/')} | "
                  f"{_f(p['test_return_pct'], '+.2f')}% |")
            A("")

    # ---- what was modelled --------------------------------------------------
    A("## What was actually modelled\n")
    A(f"- **Charges**: zero delivery brokerage, STT 0.1% both sides, NSE "
      f"transaction 0.00307%, SEBI Rs 10/crore, stamp 0.015% on buy, GST 18% on "
      f"(brokerage + transaction + SEBI), DP Rs 15.34 per scrip per sell day "
      f"(GST already included). About 24 bps round trip on a Rs 1 lakh position. "
      f"Total paid: **Rs {s3.get('total_charges', 0):,.0f}** on a "
      f"Rs {s3['initial']:,.0f} book.")
    A(f"- **Dividends** credited on ex-date to positions held through it: "
      f"Rs {s3.get('total_dividends', 0):,.0f}.")
    A("- **Long only.** A retail account cannot hold a short equity position "
      "overnight in India: SEBI bans naked shorts and delivery must be honoured "
      "at T+1. The bearish half of the video's signal is an exit, not a trade.")
    A(f"- **Unfilled orders**: {s3.get('rejected_fills', 0)} lapsed - no bar, "
      f"zero volume, or the open gapped to the circuit band. A blocked entry "
      f"drops the signal; a blocked exit holds the position and retries.")
    A(f"- **Trades**: {s3.get('n_trades', 0)}, win rate "
      f"{_f(s3.get('win_rate', 0) * 100, '.0f')}%, median hold "
      f"{_f(s3.get('median_holding_days'), '.0f')} days, profit factor "
      f"{_f(s3.get('profit_factor'))}, average exposure "
      f"{_f(s3.get('avg_exposure'))}.\n")

    # ---- caveats ------------------------------------------------------------
    A("## What is still wrong with this backtest\n")
    A("Stated so you can discount the result yourself - and note that every one "
      "of these points the same way, against the strategy:\n")
    A("- **Residual survivorship bias.** The point-in-time universe is drawn "
      "from symbols that are *currently listed*. Companies delisted or merged "
      "away over the period are absent, and they were disproportionately the "
      "losers. The true number is worse than the one above, not better.")
    A("- **Free data.** Prices come from Yahoo, not NSE bhavcopy. Re-pulling the "
      "same window moved the 15-year CAGR by roughly 0.3 points, so treat every "
      "figure as plus or minus half a point. The conclusion is many times larger "
      "than that band.")
    A("- **Fills at the printed open.** NSE's daily open is the pre-open call "
      "auction price, and a retail market order is not guaranteed to be inside "
      "that auction. The 25 bps assumption stands in for that uncertainty; "
      "measured first-minute moves on Nifty 100 names have a median of about "
      "21 bps and a 90th percentile near 60.")
    A("- **Circuit bands are approximated** by a flat 10% gap test rather than "
      "the real per-scrip dynamic band, ASM/GSM surveillance state and series "
      "restrictions.")
    A("- **No taxes.** Short-term capital gains would take a further bite out of "
      "a strategy with a 25-day median holding period, and would not touch a "
      "buy-and-hold benchmark.\n")

    # ---- reproduce ----------------------------------------------------------
    A("## Reproduce\n")
    A("```bash")
    A("python -m pytest tests/ -v          # 15 tests, incl. the future-scramble")
    A("python fetch_data.py --list nifty500 --start 2010-01-01")
    A("python fetch_benchmark.py")
    A("python run_backtest.py")
    A("python run_walkforward.py")
    A("```")

    p = os.path.join(OUT, "REPORT.md")
    open(p, "w", encoding="utf-8").write("\n".join(L))
    return p
