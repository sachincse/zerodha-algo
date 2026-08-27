"""Daily semi-automated scanner -- the deliverable from the video, minus the
React app, the FastAPI server, the Node install and the Rs 500/month app fee.

It prints (and writes to HTML) the ranked crossover table for the Nifty 100 as
of the last completed session, then stops. It never places an order. Order
placement stays with you, or with Claude via the Kite MCP after you have looked
at the table and said yes.

Usage:
    python run_scanner.py                     # last completed session
    python run_scanner.py --asof 2026-06-30   # reproduce any past day
    python run_scanner.py --lookback 10       # only crossovers in last 10 bars
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.data import build_store, load_nifty100_symbols   # noqa: E402
from src.strategy import signals_asof                     # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
os.makedirs(OUT, exist_ok=True)


CSS = """
body{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:32px;
     background:#0f1115;color:#e6e8eb}
h1{font-size:20px;margin:0 0 4px}
.sub{color:#8b929e;font-size:13px;margin-bottom:24px}
table{border-collapse:collapse;width:100%;max-width:1100px;font-variant-numeric:tabular-nums}
th{text-align:left;font-size:11px;letter-spacing:.08em;text-transform:uppercase;
   color:#8b929e;border-bottom:1px solid #2a2f3a;padding:8px 12px}
td{padding:9px 12px;border-bottom:1px solid #1c2027}
tr:hover td{background:#161a21}
.bull{color:#3fb950;font-weight:600}
.bear{color:#f85149;font-weight:600}
.num{text-align:right}
.fresh{background:#1a2a1e}
.note{margin-top:28px;padding:14px 16px;background:#161a21;border-left:3px solid #d29922;
      max-width:1100px;color:#c9d1d9;font-size:13px}
"""


def to_html(df: pd.DataFrame, asof, short: int, long: int, lookback: int) -> str:
    rows = []
    for _, r in df.iterrows():
        cls = "bull" if r["signal"] == "BULLISH" else "bear"
        fresh = ' class="fresh"' if r["bars_since"] == 0 else ""
        rows.append(
            f"<tr{fresh}><td>{r['symbol']}</td>"
            f"<td class='{cls}'>{r['signal']}</td>"
            f"<td>{r['crossover_date']}</td>"
            f"<td class='num'>{r['bars_since']}</td>"
            f"<td class='num'>{r['close']:,.2f}</td>"
            f"<td>{r['price_date']}</td>"
            f"<td class='num'>{r['sma_short']:,.2f}</td>"
            f"<td class='num'>{r['sma_long']:,.2f}</td>"
            f"<td class='num'>{r['spread_pct']:+.2f}%</td></tr>")

    n_bull = int((df["signal"] == "BULLISH").sum()) if len(df) else 0
    n_bear = int((df["signal"] == "BEARISH").sum()) if len(df) else 0

    return f"""<!doctype html><meta charset="utf-8">
<title>SMA {short}/{long} scan - {asof}</title><style>{CSS}</style>
<h1>Nifty 100 &middot; SMA({short}) x SMA({long}) crossover scan</h1>
<div class="sub">As of close {asof} &middot; {len(df)} signals within
{lookback} sessions &middot; {n_bull} bullish, {n_bear} bearish &middot;
ranked by recency &middot; generated {datetime.now():%Y-%m-%d %H:%M}</div>
<table><thead><tr><th>Symbol</th><th>Signal</th><th>Crossover date</th>
<th class="num">Bars since</th><th class="num">Close</th><th>Price date</th>
<th class="num">SMA{short}</th><th class="num">SMA{long}</th>
<th class="num">Spread</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<div class="note"><b>This is a signal list, not advice.</b> Backtested on
2011&ndash;2026 with realistic fills, Zerodha charges and a point-in-time
universe, this rule did not beat buying the index. Read out/REPORT.md before
acting on any row. Bearish rows are exit signals &mdash; you cannot short
equity delivery in India.</div>
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--short", type=int, default=6)
    ap.add_argument("--long", type=int, default=30)
    ap.add_argument("--lookback", type=int, default=30,
                    help="only report crossovers within this many sessions")
    ap.add_argument("--asof", default=None, help="YYYY-MM-DD; default last session")
    ap.add_argument("--refresh", action="store_true", help="force re-download")
    args = ap.parse_args()

    syms = load_nifty100_symbols()
    end = pd.Timestamp.today() + pd.Timedelta(days=1)
    start = (pd.Timestamp(args.asof) if args.asof else pd.Timestamp.today()) \
        - pd.Timedelta(days=400)

    print(f"fetching {len(syms)} Nifty 100 symbols ...", flush=True)
    store = build_store(syms, start.strftime("%Y-%m-%d"),
                        end.strftime("%Y-%m-%d"), force=args.refresh, verbose=False)

    cl = store.panels["Close"]
    asof = pd.Timestamp(args.asof) if args.asof else cl.index[-1]
    if asof not in cl.index:
        asof = cl.index[cl.index <= asof][-1]

    df = signals_asof(cl, args.short, args.long, asof=asof, lookback=args.lookback)

    print(f"\nSMA({args.short}) x SMA({args.long})  --  as of close {asof.date()}")
    print(f"{len(df)} signals in the last {args.lookback} sessions\n")
    if df.empty:
        print("  (none)")
    else:
        print(df.to_string(index=False))

    html = to_html(df, asof.date(), args.short, args.long, args.lookback)
    hp = os.path.join(OUT, "scan.html")
    with open(hp, "w", encoding="utf-8") as f:
        f.write(html)
    df.to_csv(os.path.join(OUT, "scan.csv"), index=False)
    print(f"\nwrote {hp}")


if __name__ == "__main__":
    main()
