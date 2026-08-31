"""The honesty ladder.

Runs the SAME strategy four times, removing one convenient lie at each rung, so
the cost of each lie is visible as a number rather than argued about:

    S0  naive      same-bar close fill, no costs, no slippage, today's Nifty 100
    S1  + real fill        signal at close t, filled at open t+1
    S2  + real costs       Zerodha charges, STT, stamp, GST, DP, 25bps slippage
    S3  + real universe    point-in-time top-100 by trailing traded value

S3 is the only rung whose number means anything. Everything above it is there to
show you how much of a backtest's apparent edge is manufactured by assumptions.

Usage:
    python run_backtest.py --start 2011-01-01 --end 2026-08-21
    python run_backtest.py --sweep          # walk-forward parameter sweep
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.backtest import run_backtest                       # noqa: E402
from src.config import (CostModel, ExecModel, PortfolioModel,  # noqa: E402
                        Settings)
from src.data import build_store, load_nifty100_symbols     # noqa: E402
from src.metrics import cagr, format_summary, max_drawdown, sharpe, summarize  # noqa: E402
from src.nulls import pit_equal_weight, random_timing_null  # noqa: E402
from src.universe import (current_index_membership,          # noqa: E402
                          pit_turnover_membership,
                          report_residual_bias)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
os.makedirs(OUT, exist_ok=True)

FREE = CostModel(brokerage_delivery=0, brokerage_intraday_pct=0,
                 stt_delivery_buy=0, stt_delivery_sell=0,
                 stt_intraday_buy=0, stt_intraday_sell=0,
                 exchange_txn_nse=0, sebi_turnover=0,
                 stamp_duty_delivery_buy=0, stamp_duty_intraday_buy=0,
                 gst_rate=0, dp_charge_per_sell=0)


ETF_TER = 0.0025      # what an investable Nifty 100 index fund actually costs


def load_benchmark(start: str, end: str, tri: bool = True) -> pd.Series:
    """Nifty 100 TOTAL RETURN index, net of a 25 bps/yr index-fund fee.

    The strategy collects dividends, so it must be measured against an index
    that does too. The price index understates the benchmark by roughly 1.3 pp a
    year -- about the size of the entire apparent alpha of a long/flat large-cap
    timing rule. Falls back to the Yahoo price index only if the TRI file is
    missing, and says so loudly when it does.
    """
    p = os.path.join(HERE, "data", "nifty100_tri.csv")
    if tri and os.path.exists(p):
        s = pd.read_csv(p, index_col=0, parse_dates=True).iloc[:, 0]
        s = s.loc[start:end].dropna()
        if len(s) > 100:
            # charge the index fund's TER daily so the hurdle is investable
            drag = (1 - ETF_TER) ** (1 / 252)
            s = s * pd.Series(drag ** np.arange(len(s)), index=s.index)
            s.name = "NIFTY100_TRI_net"
            return s
        print("  WARNING: TRI file too short, falling back to price index")
    else:
        print("  WARNING: no TRI file (run fetch_benchmark.py); "
              "using the PRICE index, which understates the benchmark ~1.3%/yr")

    d = yf.Ticker("^CNX100").history(start=start, end=end, auto_adjust=False)
    if d.empty:
        return pd.Series(dtype=float)
    s = d["Close"].copy()
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    s.name = "NIFTY100_PRI"
    return s


def build_panel(pool: str, start: str, end: str):
    path = os.path.join(HERE, "data", f"ind_{pool}list.csv")
    syms = sorted(pd.read_csv(path)["Symbol"].astype(str).str.strip().unique())
    print(f"loading {len(syms)} symbols from {pool} ...", flush=True)
    store = build_store(syms, start, end)
    print(f"  panel: {store.panels['Close'].shape[0]} sessions x "
          f"{store.panels['Close'].shape[1]} symbols "
          f"({store.dates.min().date()} -> {store.dates.max().date()})")
    return store, syms


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2010-01-01")
    ap.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    ap.add_argument("--trade-from", default="2011-01-01",
                    help="first date the portfolio may trade (warm-up before)")
    ap.add_argument("--pool", default="nifty500",
                    choices=["nifty100", "nifty200", "nifty500"])
    ap.add_argument("--short", type=int, default=6)
    ap.add_argument("--long", type=int, default=30)
    ap.add_argument("--max-positions", type=int, default=10)
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    ap.add_argument("--slippage-bps", type=float, default=25.0)
    ap.add_argument("--skip-nulls", action="store_true")
    args = ap.parse_args()

    store, pool_syms = build_panel(args.pool, args.start, args.end)
    n100 = load_nifty100_symbols()
    bench = load_benchmark(args.start, args.end)
    trade_from = pd.Timestamp(args.trade_from)

    bias = report_residual_bias(store, n100)
    print(f"\nNifty-100 coverage: {bias['with_data']}/{bias['requested']} have data; "
          f"{len(bias['listed_after_start'])} listed after {args.start}")

    pf = PortfolioModel(initial_capital=args.capital,
                        max_positions=args.max_positions)

    mem_current = current_index_membership(store, n100)
    print("building point-in-time universe ...", flush=True)
    mem_pit = pit_turnover_membership(store, top_n=100, lookback=60, min_history=200)
    avg_pit = mem_pit.sum(axis=1).loc[trade_from:].mean()
    overlap = (mem_pit & mem_current).sum(axis=1).loc[trade_from:].mean()
    print(f"  PIT universe averages {avg_pit:.0f} names/day, "
          f"{overlap:.0f} of them also in today's Nifty 100")

    rungs = [
        dict(key="S0_naive",
             desc="same-bar close fill, zero costs, today's Nifty 100",
             settings=Settings(costs=FREE, execution=ExecModel(slippage_bps=0.0),
                               portfolio=pf, short_window=args.short,
                               long_window=args.long),
             membership=mem_current, leaky=True),
        dict(key="S1_realfill",
             desc="next-open fill, zero costs, today's Nifty 100",
             settings=Settings(costs=FREE, execution=ExecModel(slippage_bps=0.0),
                               portfolio=pf, short_window=args.short,
                               long_window=args.long),
             membership=mem_current, leaky=False),
        dict(key="S2_realcost",
             desc="next-open fill, full Zerodha charges + slippage, today's Nifty 100",
             settings=Settings(costs=CostModel(),
                               execution=ExecModel(slippage_bps=args.slippage_bps),
                               portfolio=pf, short_window=args.short,
                               long_window=args.long),
             membership=mem_current, leaky=False),
        dict(key="S3_pit",
             desc="next-open fill, full costs, POINT-IN-TIME universe",
             settings=Settings(costs=CostModel(),
                               execution=ExecModel(slippage_bps=args.slippage_bps),
                               portfolio=pf, short_window=args.short,
                               long_window=args.long),
             membership=mem_pit, leaky=False),
    ]

    summaries = {}
    equities = {}
    s3_result = None
    for r in rungs:
        print(f"\n{'=' * 78}\n{r['key']}  --  {r['desc']}\n{'=' * 78}", flush=True)
        res = run_backtest(store, r["membership"], r["settings"],
                           label=r["key"], start=trade_from,
                           leaky_same_bar_fill=r["leaky"])
        s = summarize(res, benchmark=bench)
        s["band_blocked_fills"] = res.meta.get("band_blocked_fills", 0)
        summaries[r["key"]] = s
        equities[r["key"]] = res.equity
        if r["key"] == "S3_pit":
            s3_result = res
        print(format_summary(s))
        print(f"  band-blocked      {s['band_blocked_fills']} "
              f"(orders dropped because the open gapped to the circuit limit)")
        pd.DataFrame([vars(t) for t in res.trades]).to_csv(
            os.path.join(OUT, f"trades_{r['key']}.csv"), index=False)

    # ---- tiebreak sensitivity ---------------------------------------------
    # Recency does not rank same-day crossovers; the tiebreak does. It was
    # alphabetical by accident, which is arbitrary and favours names starting
    # with A. Publishing a number that depends on an arbitrary choice without
    # showing how much it depends on it is the sort of thing this repo exists
    # to complain about, so the band is measured and reported.
    print(f"\n{'=' * 78}\nTIEBREAK SENSITIVITY (S3, same universe and costs)"
          f"\n{'=' * 78}", flush=True)
    tb_rows = {}
    for mode in ("alpha", "turnover", "spread", "reverse"):
        cfg = Settings(costs=CostModel(),
                       execution=ExecModel(slippage_bps=args.slippage_bps,
                                           tiebreak=mode),
                       portfolio=pf, short_window=args.short,
                       long_window=args.long)
        rr = run_backtest(store, mem_pit, cfg, label=f"S3_tb_{mode}",
                          start=trade_from)
        tb_rows[mode] = {"cagr_pct": cagr(rr.equity) * 100,
                         "sharpe": sharpe(rr.equity),
                         "max_dd_pct": max_drawdown(rr.equity) * 100,
                         "trades": len(rr.trades)}
        print(f"  {mode:<9s}      CAGR {tb_rows[mode]['cagr_pct']:6.2f}%   "
              f"Sharpe {tb_rows[mode]['sharpe']:5.2f}   "
              f"maxDD {tb_rows[mode]['max_dd_pct']:6.1f}%   "
              f"trades {tb_rows[mode]['trades']}")
    _lo = min(v["cagr_pct"] for v in tb_rows.values())
    _hi = max(v["cagr_pct"] for v in tb_rows.values())
    print(f"\n  band {_lo:.2f}% .. {_hi:.2f}%  (spread {_hi - _lo:.2f} points). "
          f"The shipped default is 'alpha'.")

    # ---- slippage sensitivity: 5 / 25 / 50 bps per side --------------------
    print(f"\n{'=' * 78}\nSLIPPAGE SENSITIVITY (S3, point-in-time, full charges)"
          f"\n{'=' * 78}", flush=True)
    slip_rows = {}
    for bps in (5.0, 25.0, 50.0):
        cfg = Settings(costs=CostModel(), execution=ExecModel(slippage_bps=bps),
                       portfolio=pf, short_window=args.short, long_window=args.long)
        rr = run_backtest(store, mem_pit, cfg, label=f"S3_slip{bps:g}",
                          start=trade_from)
        slip_rows[bps] = {"cagr_pct": cagr(rr.equity) * 100,
                          "sharpe": sharpe(rr.equity),
                          "max_dd_pct": max_drawdown(rr.equity) * 100}
        print(f"  {bps:>4.0f} bps/side   CAGR {slip_rows[bps]['cagr_pct']:6.2f}%   "
              f"Sharpe {slip_rows[bps]['sharpe']:5.2f}   "
              f"maxDD {slip_rows[bps]['max_dd_pct']:6.1f}%")

    # ---- the two nulls a timing rule actually has to beat ------------------
    nulls = {}
    if not args.skip_nulls:
        print(f"\n{'=' * 78}\nNULL HYPOTHESES\n{'=' * 78}", flush=True)

        bh = pit_equal_weight(store, mem_pit, CostModel(), capital=args.capital,
                              slippage_bps=args.slippage_bps, start=trade_from)
        equities["PIT_BUY_HOLD"] = bh
        nulls["pit_equal_weight"] = {"cagr_pct": cagr(bh) * 100,
                                     "sharpe": sharpe(bh),
                                     "max_dd_pct": max_drawdown(bh) * 100}
        print(f"  same universe, NO timing, same costs:"
              f"   CAGR {nulls['pit_equal_weight']['cagr_pct']:6.2f}%"
              f"   Sharpe {nulls['pit_equal_weight']['sharpe']:5.2f}"
              f"   maxDD {nulls['pit_equal_weight']['max_dd_pct']:6.1f}%")

        print("  bootstrapping random-timing null (1000 draws) ...", flush=True)
        rt = random_timing_null(store.slice(start=trade_from),
                                mem_pit.loc[trade_from:], s3_result.trades,
                                CostModel(), slippage_bps=args.slippage_bps,
                                n_draws=1000)
        nulls["random_timing"] = rt
        print(f"  random entries, same trade count & holding periods:")
        print(f"    strategy mean trade {rt['strategy_mean_trade_pct']:+.3f}%"
              f"   vs null {rt['null_mean_trade_pct']:+.3f}%"
              f"  [p05 {rt['null_p05_pct']:+.3f}%, p95 {rt['null_p95_pct']:+.3f}%]")
        print(f"    strategy sits at the {rt['strategy_percentile']:.1f}th percentile "
              f"of the null -> "
              f"{'BEATS' if rt['beats_null'] else 'DOES NOT BEAT'} random timing "
              f"at 95%")
        print(f"  same days as the strategy, random stock each time:")
        print(f"    strategy {rt['strategy_mean_trade_pct']:+.3f}%"
              f"   vs date-matched null {rt['datematched_mean_trade_pct']:+.3f}%"
              f"  [p05 {rt['datematched_p05_pct']:+.3f}%, "
              f"p95 {rt['datematched_p95_pct']:+.3f}%]")
        print(f"    strategy sits at the {rt['datematched_percentile']:.1f}th "
              f"percentile -> "
              f"{'BEATS' if rt['beats_datematched'] else 'DOES NOT BEAT'} "
              f"random stock selection on its own chosen days")

    eqdf = pd.DataFrame(equities)
    b = bench.reindex(eqdf.index).ffill()
    eqdf["BENCHMARK_N100"] = b / b.iloc[0] * args.capital
    eqdf.to_csv(os.path.join(OUT, "equity_curves.csv"))

    with open(os.path.join(OUT, "summaries.json"), "w") as f:
        json.dump({"args": vars(args), "bias": {k: v for k, v in bias.items()
                                                if k != "no_data_at_all"},
                   "summaries": summaries, "slippage_sensitivity": slip_rows,
            "tiebreak_sensitivity": tb_rows,
                   "nulls": nulls, "benchmark": bench.name}, f, indent=2, default=str)

    print(f"\n{'=' * 78}\nHOW MUCH EACH ASSUMPTION WAS WORTH (CAGR, % per year)\n{'=' * 78}")
    prev = None
    for r in rungs:
        c = summaries[r["key"]]["cagr_pct"]
        delta = "" if prev is None else f"   ({c - prev:+.2f} vs previous rung)"
        print(f"  {r['key']:16s} {c:7.2f}%{delta}")
        prev = c
    if "pit_equal_weight" in nulls:
        print(f"  {'same univ, no timing':16s} "
              f"{nulls['pit_equal_weight']['cagr_pct']:7.2f}%")
    bc = summaries["S3_pit"].get("benchmark_cagr_pct")
    if bc is not None:
        print(f"  {str(bench.name):16s} {bc:7.2f}%   "
              f"(total return, net of a 25bps index-fund fee)")
        print(f"\n  Honest edge (S3 minus benchmark): "
              f"{summaries['S3_pit']['cagr_pct'] - bc:+.2f}% CAGR")
    print(f"\nwrote {OUT}/equity_curves.csv, summaries.json, trades_*.csv")


if __name__ == "__main__":
    main()
