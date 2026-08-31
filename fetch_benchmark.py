"""Fetch the NIFTY 100 TOTAL RETURN index from NSE Indices.

The price index (^CNX100 on Yahoo) excludes dividends and understates the
benchmark by roughly 1.3 pp a year -- which is typically the entire apparent
alpha of a long/flat large-cap timing rule. SEBI mandated TRI benchmarking in
2018 for exactly this reason. Comparing a dividend-collecting strategy against a
price index is a thumb on the scale, so we do not.
"""
from __future__ import annotations

import io
import json
import os

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "nifty100_tri.csv")

URL = "https://niftyindices.com/BackPage/getTotalReturnIndexString"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Referer": "https://niftyindices.com/reports/historical-data",
    "Content-Type": "application/json; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}


def fetch_tri(start="01-Jan-2003", end="21-Aug-2026", index_name="NIFTY 100") -> pd.Series:
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get("https://niftyindices.com/reports/historical-data", timeout=30)

    payload = {"cinfo": json.dumps({
        "name": index_name, "startDate": start, "endDate": end,
        "indexName": index_name}).replace('"', "'")}
    r = s.post(URL, json=payload, timeout=60)
    r.raise_for_status()

    body = r.json()
    data = body.get("d", body) if isinstance(body, dict) else body
    if isinstance(data, str):
        data = json.loads(data)
    df = pd.DataFrame(data)

    date_col = next(c for c in df.columns if "date" in c.lower())
    val_col = next(c for c in df.columns
                   if c != date_col and df[c].astype(str).str.replace(
                       ".", "", regex=False).str.replace(
                       ",", "", regex=False).str.isnumeric().mean() > 0.8)

    out = pd.Series(
        pd.to_numeric(df[val_col].astype(str).str.replace(",", ""), errors="coerce").values,
        index=pd.to_datetime(df[date_col], format="mixed", dayfirst=True),
        name="NIFTY100_TRI").dropna().sort_index()
    out.index = out.index.normalize()
    return out


if __name__ == "__main__":
    import argparse

    # The end date was hard-coded, so the benchmark could not be pinned to the
    # same window as the backtest. run_backtest.py takes --end and this did
    # not, which made "reproduce the published numbers" impossible the moment
    # the index advanced past the baked-in date.
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="01-Jan-2003")
    ap.add_argument("--end", default="22-Aug-2026",
                    help="dd-Mon-YYYY or an ISO date; match the --end you pass "
                         "to run_backtest.py")
    ap.add_argument("--index", default="NIFTY 100")
    args = ap.parse_args()

    def _as_niftyindices(d: str) -> str:
        """Accept 2026-08-22 as well as 22-Aug-2026 — the runners use ISO."""
        try:
            return pd.Timestamp(d).strftime("%d-%b-%Y")
        except (ValueError, TypeError):
            return d

    try:
        tri = fetch_tri(start=_as_niftyindices(args.start),
                        end=_as_niftyindices(args.end),
                        index_name=args.index)
        tri.to_csv(OUT)
        print(f"{len(tri)} rows  {tri.index.min().date()} -> {tri.index.max().date()}")
        print(f"last value {tri.iloc[-1]:,.2f}")
        for yrs in (5, 10, 15):
            past = tri[tri.index <= tri.index[-1] - pd.DateOffset(years=yrs)]
            if len(past):
                c = (tri.iloc[-1] / past.iloc[-1]) ** (1 / yrs) - 1
                print(f"  {yrs:>2}y TRI CAGR {c * 100:.2f}%")
        print("wrote", OUT)
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
