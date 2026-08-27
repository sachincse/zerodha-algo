"""A standard tearsheet for the strategy, against the Nifty 100 benchmark.

out/report.html is hand-written and says exactly what this study wanted to say.
This is the complement, not the replacement: a conventional QuantStats
tearsheet that a reader already familiar with the format can scan without
learning our layout first, and which computes a long tail of standard metrics
(Omega, tail ratio, VaR, rolling Sharpe, monthly heatmap) that nobody should
re-implement by hand.

    python make_tearsheet.py                # S3, the honest one
    python make_tearsheet.py --curve S0_naive

Reads the committed out/equity_curves.csv, so it needs no price cache and no
network.
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--curve", default="S3_pit",
                    help="column in out/equity_curves.csv to report on")
    ap.add_argument("--benchmark", default="BENCHMARK_N100")
    ap.add_argument("--out", default=str(OUT / "tearsheet.html"))
    args = ap.parse_args()

    src = OUT / "equity_curves.csv"
    if not src.exists():
        print(f"  {src} not found - run run_backtest.py first")
        return 2

    eq = pd.read_csv(src, index_col=0, parse_dates=True)
    for col in (args.curve, args.benchmark):
        if col not in eq.columns:
            print(f"  '{col}' is not in {src.name}. Available: "
                  f"{', '.join(eq.columns)}")
            return 2

    rets = eq[args.curve].pct_change().dropna()
    bench = eq[args.benchmark].pct_change().dropna()
    rets.name, bench.name = args.curve, args.benchmark

    # QuantStats is stalling and leans on older pandas/numpy idioms; its
    # deprecation noise would otherwise bury the actual output.
    warnings.filterwarnings("ignore")

    import quantstats as qs

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"  {args.curve} vs {args.benchmark}, {len(rets)} sessions")
    try:
        qs.reports.html(rets, benchmark=bench, output=str(out),
                        title=f"{args.curve} vs Nifty 100 TRI",
                        download_filename=str(out))
    except Exception as e:                                      # noqa: BLE001
        # A tearsheet is a convenience. If the library trips over a pandas
        # change, say so plainly rather than failing the whole pipeline - every
        # number this study actually depends on comes from src/metrics.py.
        print(f"  quantstats could not build the HTML ({type(e).__name__}: "
              f"{str(e)[:120]})")
        print("  falling back to the metrics table")
        try:
            qs.reports.metrics(rets, benchmark=bench, mode="full", display=True)
        except Exception as e2:                                 # noqa: BLE001
            print(f"  and the metrics table failed too ({type(e2).__name__})")
            return 1
        return 0

    print(f"  wrote {out}  ({out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
