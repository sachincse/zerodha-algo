"""One-off bulk download of daily NSE history into data/cache/.

Usage:
    python fetch_data.py --list nifty500 --start 2010-01-01
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.data import download_symbol  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LISTS = {
    "nifty100": "data/ind_nifty100list.csv",
    "nifty200": "data/ind_nifty200list.csv",
    "nifty500": "data/ind_nifty500list.csv",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", default="nifty500", choices=list(LISTS))
    ap.add_argument("--start", default="2010-01-01")
    ap.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    df = pd.read_csv(os.path.join(HERE, LISTS[args.list]))
    syms = sorted(df["Symbol"].astype(str).str.strip().unique().tolist())
    print(f"{len(syms)} symbols from {args.list}, {args.start} -> {args.end}", flush=True)

    ok = miss = 0
    t0 = time.time()
    for i, s in enumerate(syms, 1):
        d = download_symbol(s, args.start, args.end, force=args.force)
        if d is None or d.empty:
            miss += 1
            print(f"  MISS {s}", flush=True)
        else:
            ok += 1
        if i % 20 == 0:
            el = time.time() - t0
            print(f"  {i}/{len(syms)}  ok={ok} miss={miss}  {el:.0f}s "
                  f"(eta {el / i * (len(syms) - i):.0f}s)", flush=True)

    print(f"DONE ok={ok} miss={miss} in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
