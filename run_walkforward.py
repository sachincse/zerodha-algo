"""Walk-forward parameter selection -- the leak nobody notices.

The 6/30 pair in the video was picked out of the air. The moment you try a few
pairs and keep the best, you have used the whole sample to choose, and the
reported number is no longer something you could have earned. This script
measures that.

Method
    1. Run the full backtest once for every (short, long) pair on the grid.
       Each run is individually leak-free (next-open fills, real costs,
       point-in-time universe).
    2. Walk forward in one-year steps. At the start of each test year, pick the
       pair with the best Sharpe over the PRECEDING ``train_years`` only.
    3. Record that pair's returns for the test year. Stitch the test years into
       an out-of-sample curve.
    4. Compare against: fixed 6/30, the best-in-hindsight pair, and the index.

The gap between "best in hindsight" and "walk-forward" is the overfitting
premium -- the part of a tuned backtest that does not survive contact with the
future.

Usage:
    python run_walkforward.py --train-years 3
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.backtest import run_backtest                        # noqa: E402
from src.config import CostModel, ExecModel, PortfolioModel, Settings  # noqa: E402
from src.data import build_store, load_nifty100_symbols      # noqa: E402
from src.metrics import cagr, max_drawdown, sharpe           # noqa: E402
from src.universe import pit_turnover_membership             # noqa: E402
from run_backtest import load_benchmark                      # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")

SHORTS = [3, 5, 6, 8, 10, 15, 20]
LONGS = [20, 30, 40, 50, 100, 200]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2010-01-01")
    ap.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    ap.add_argument("--trade-from", default="2011-01-01")
    ap.add_argument("--train-years", type=int, default=3)
    ap.add_argument("--pool", default="nifty500")
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    ap.add_argument("--max-positions", type=int, default=10)
    ap.add_argument("--slippage-bps", type=float, default=25.0)
    args = ap.parse_args()

    syms = sorted(pd.read_csv(
        os.path.join(HERE, "data", f"ind_{args.pool}list.csv")
    )["Symbol"].astype(str).str.strip().unique())
    print(f"loading {len(syms)} symbols ...", flush=True)
    store = build_store(syms, args.start, args.end, verbose=False)
    mem = pit_turnover_membership(store, top_n=100, lookback=60, min_history=200)
    trade_from = pd.Timestamp(args.trade_from)
    bench = load_benchmark(args.start, args.end)

    pf = PortfolioModel(initial_capital=args.capital,
                        max_positions=args.max_positions)
    grid = [(s, l) for s, l in itertools.product(SHORTS, LONGS) if s < l]
    print(f"running {len(grid)} parameter pairs, point-in-time universe, "
          f"full costs ...", flush=True)

    curves: dict[tuple[int, int], pd.Series] = {}
    # Trade counts are kept because the README's headline observation about
    # this grid -- corr(CAGR, log trade count) = -0.86 -- is computed from
    # them. They were previously printed to the console and then dropped, so
    # the committed param_grid.csv had two columns the code could not
    # regenerate.
    trade_counts: dict[tuple[int, int], int] = {}
    for k, (s, l) in enumerate(grid, 1):
        cfg = Settings(costs=CostModel(), execution=ExecModel(slippage_bps=args.slippage_bps),
                       portfolio=pf, short_window=s, long_window=l)
        res = run_backtest(store, mem, cfg, label=f"sma{s}_{l}", start=trade_from)
        curves[(s, l)] = res.equity
        trade_counts[(s, l)] = len(res.trades)
        print(f"  [{k:2d}/{len(grid)}] SMA {s:>3}/{l:<3}  "
              f"CAGR {cagr(res.equity) * 100:6.2f}%  "
              f"Sharpe {sharpe(res.equity):5.2f}  "
              f"maxDD {max_drawdown(res.equity) * 100:6.1f}%  "
              f"trades {len(res.trades):4d}", flush=True)

    rets = pd.DataFrame({f"{s}_{l}": c.pct_change() for (s, l), c in curves.items()})
    rets = rets.dropna(how="all")

    # ---- walk forward -----------------------------------------------------
    years = sorted({d.year for d in rets.index})
    first_test = years[0] + args.train_years
    picks, oos_chunks = [], []

    for y in [y for y in years if y >= first_test]:
        train = rets.loc[f"{y - args.train_years}-01-01": f"{y - 1}-12-31"]
        test = rets.loc[f"{y}-01-01": f"{y}-12-31"]
        if len(train) < 200 or test.empty:
            continue
        # selection uses ONLY the training slice
        sh = train.mean() / train.std() * np.sqrt(252)
        best = sh.idxmax()
        oos_chunks.append(test[best].rename("oos"))
        picks.append({"test_year": y, "chosen": best,
                      "train_sharpe": round(float(sh[best]), 3),
                      "test_return_pct": round(float((1 + test[best]).prod() - 1) * 100, 2)})

    oos = pd.concat(oos_chunks)
    oos_eq = (1 + oos).cumprod() * args.capital

    fixed = rets["6_30"].loc[oos.index]
    fixed_eq = (1 + fixed).cumprod() * args.capital

    full_sharpe = rets.mean() / rets.std() * np.sqrt(252)
    hind = full_sharpe.idxmax()
    hind_eq = (1 + rets[hind].loc[oos.index]).cumprod() * args.capital

    b = bench.reindex(oos.index).ffill().dropna()
    b_eq = b / b.iloc[0] * args.capital

    print(f"\n{'=' * 78}\nWALK-FORWARD ({args.train_years}y train -> 1y test), "
          f"{oos.index[0].date()} -> {oos.index[-1].date()}\n{'=' * 78}")
    for p in picks:
        print(f"  {p['test_year']}  trained-best SMA {p['chosen'].replace('_', '/'):8s}"
              f"  train Sharpe {p['train_sharpe']:5.2f}"
              f"  -> realised {p['test_return_pct']:+7.2f}%")

    def line(name, eq):
        return (f"  {name:34s} CAGR {cagr(eq) * 100:6.2f}%   "
                f"Sharpe {sharpe(eq):5.2f}   maxDD {max_drawdown(eq) * 100:6.1f}%")

    print(f"\n{'=' * 78}\nOUT-OF-SAMPLE COMPARISON (same window for all)\n{'=' * 78}")
    print(line("walk-forward (honest tuning)", oos_eq))
    print(line("fixed SMA 6/30 (the video)", fixed_eq))
    print(line(f"best in hindsight SMA {hind.replace('_', '/')}", hind_eq))
    print(line(f"{bench.name} (total return)", b_eq))
    print(f"\n  overfitting premium: "
          f"{(cagr(hind_eq) - cagr(oos_eq)) * 100:+.2f}% CAGR of the hindsight-best "
          f"pair's edge does not survive honest selection")

    span_years = (rets.index[-1] - rets.index[0]).days / 365.25
    grid_tbl = pd.DataFrame([
        {"short": s, "long": l,
         "cagr_pct": round(cagr(c) * 100, 2),
         "sharpe": round(sharpe(c), 3),
         "max_dd_pct": round(max_drawdown(c) * 100, 2),
         "trades": trade_counts[(s, l)],
         "trades_per_yr": trade_counts[(s, l)] / span_years}
        for (s, l), c in curves.items()
    ]).sort_values("sharpe", ascending=False)
    grid_tbl.to_csv(os.path.join(OUT, "param_grid.csv"), index=False)

    # The per-pair return matrix is the evidence behind every overfitting
    # statistic below. Without it a reader can see WHICH pair won but has no
    # way to ask whether winning meant anything.
    rets.to_csv(os.path.join(OUT, "grid_returns.csv"))

    # ---- was the winner skill, or the luckiest of N? ----------------------
    from src.overfitting import analyse_grid

    bench_rets = b_eq.pct_change().reindex(rets.index)
    over = analyse_grid(rets, benchmark=bench_rets,
                        labels={c: f"SMA {c.replace('_', '/')}" for c in rets.columns})
    print("\n  --- selection bias -------------------------------------------")
    print(over.render())

    with open(os.path.join(OUT, "overfitting.json"), "w") as f:
        json.dump({k: v for k, v in vars(over).items()}, f, indent=2, default=str)

    pd.DataFrame({"walk_forward": oos_eq, "fixed_6_30": fixed_eq,
                  f"hindsight_{hind}": hind_eq, "nifty100": b_eq}
                 ).to_csv(os.path.join(OUT, "walkforward_equity.csv"))
    with open(os.path.join(OUT, "walkforward.json"), "w") as f:
        json.dump({"picks": picks,
                   "walk_forward": {"cagr_pct": cagr(oos_eq) * 100,
                                    "sharpe": sharpe(oos_eq),
                                    "max_dd_pct": max_drawdown(oos_eq) * 100},
                   "fixed_6_30": {"cagr_pct": cagr(fixed_eq) * 100,
                                  "sharpe": sharpe(fixed_eq),
                                  "max_dd_pct": max_drawdown(fixed_eq) * 100},
                   "hindsight_best": {"pair": hind, "cagr_pct": cagr(hind_eq) * 100},
                   "benchmark": {"cagr_pct": cagr(b_eq) * 100}},
                  f, indent=2)

    print(f"\n  full grid ({len(grid)} pairs) written to out/param_grid.csv")
    print(f"  grid CAGR spread: {grid_tbl['cagr_pct'].min():.2f}% .. "
          f"{grid_tbl['cagr_pct'].max():.2f}%")


if __name__ == "__main__":
    main()
