"""Daily OHLCV loading and caching.

PRICE BASIS — this matters for leakage, so it is spelled out.

yfinance with ``auto_adjust=False`` returns Yahoo's Open/High/Low/Close already
adjusted for **splits and bonuses**, but NOT for dividends. ``Adj Close`` is
adjusted for both.

* Split/bonus adjustment is applied retroactively, but it cannot change the
  timing of a moving-average crossover: it multiplies every price in the
  pre-event history by the same constant, and both SMAs scale identically. A
  crossover happens at the same bar either way. So we generate SIGNALS on the
  split-adjusted, dividend-unadjusted series -- which is also exactly the chart
  a real trader would have been looking at.
* Dividend adjustment is different. It retroactively erases the ex-date price
  gap that a real trader actually experienced. Using ``Adj Close`` for signals
  would let a future dividend schedule reshape past prices. So we never use it
  for signals. Dividends are credited to RETURNS separately, on the ex-date,
  only if the position was held into that date.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

import pandas as pd
import yfinance as yf

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

FIELDS = ["Open", "High", "Low", "Close", "Volume"]


@dataclass
class PriceStore:
    """Wide panels keyed by field. Index = trading dates, columns = symbols.

    ``dividends`` is a sparse frame of per-share cash dividends on their
    ex-dates, on the same split-adjusted basis as the prices.
    """

    panels: dict[str, pd.DataFrame]
    dividends: pd.DataFrame

    @property
    def dates(self) -> pd.DatetimeIndex:
        return self.panels["Close"].index

    @property
    def symbols(self) -> list[str]:
        return list(self.panels["Close"].columns)

    def slice(self, start=None, end=None) -> "PriceStore":
        p = {k: v.loc[start:end] for k, v in self.panels.items()}
        d = self.dividends.loc[start:end] if len(self.dividends) else self.dividends
        return PriceStore(p, d)


def _cache_path(symbol: str) -> str:
    return os.path.join(CACHE_DIR, f"{symbol.replace('/', '_')}.csv")


def _read_cache(path: str) -> pd.DataFrame | None:
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        return df if len(df) else None
    except Exception:
        return None


def download_symbol(symbol: str, start: str, end: str,
                    force: bool = False) -> pd.DataFrame | None:
    """Download one NSE symbol. Returns a frame with OHLCV + Dividends, or None.

    The cache is MERGED, never replaced. A short refresh (say the scanner asking
    for the last 400 days) must not truncate a cache that already holds fifteen
    years, and a long backfill must not discard rows the short pull had already
    corrected. Fresh rows win on overlap; older rows outside the fetched window
    are kept.
    """
    path = _cache_path(symbol)
    cached = _read_cache(path)

    if cached is not None and not force:
        covers_start = cached.index.min() <= pd.Timestamp(start) + pd.Timedelta(days=10)
        covers_end = cached.index.max() >= pd.Timestamp(end) - pd.Timedelta(days=7)
        if covers_start and covers_end:
            return cached.loc[str(start):str(end)]

    for attempt in range(3):
        try:
            t = yf.Ticker(f"{symbol}.NS")
            df = t.history(start=start, end=end, auto_adjust=False,
                           actions=True, timeout=30)
            if df is None or df.empty:
                return cached.loc[str(start):str(end)] if cached is not None else None
            df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
            keep = [c for c in FIELDS + ["Dividends", "Stock Splits"] if c in df.columns]
            df = df[keep]

            if cached is not None:
                cached = cached.reindex(columns=df.columns)
                merged = pd.concat([cached[~cached.index.isin(df.index)], df])
                df = merged.sort_index()

            df.to_csv(path)
            return df.loc[str(start):str(end)]
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return cached.loc[str(start):str(end)] if cached is not None else None


def build_store(symbols: list[str], start: str, end: str,
                force: bool = False, verbose: bool = True) -> PriceStore:
    frames: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    for i, s in enumerate(symbols, 1):
        df = download_symbol(s, start, end, force=force)
        if df is None or df.empty:
            missing.append(s)
        else:
            frames[s] = df
        if verbose and i % 25 == 0:
            print(f"  ... {i}/{len(symbols)} ({len(missing)} missing)", flush=True)

    if verbose and missing:
        print(f"  no data for {len(missing)}: {', '.join(missing[:12])}"
              + (" ..." if len(missing) > 12 else ""))

    panels = {}
    for f in FIELDS:
        panels[f] = pd.DataFrame({s: d[f] for s, d in frames.items() if f in d}).sort_index()

    div = pd.DataFrame(
        {s: d["Dividends"] for s, d in frames.items() if "Dividends" in d}
    ).sort_index().fillna(0.0)

    # Align every panel onto the union calendar. Missing bars stay NaN -- they
    # are NOT forward filled, because a forward fill would invent a tradeable
    # price on a day the stock did not trade.
    cal = panels["Close"].index
    for f in FIELDS:
        panels[f] = panels[f].reindex(cal)
    div = div.reindex(cal).fillna(0.0)

    return PriceStore(panels, div)


def load_nifty100_symbols(csv_path: str | None = None) -> list[str]:
    csv_path = csv_path or os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data", "ind_nifty100list.csv")
    df = pd.read_csv(csv_path)
    return sorted(df["Symbol"].astype(str).str.strip().tolist())
