"""SMA-crossover signal generation -- the strategy from the Varsity video.

Rules, exactly as stated in the video:
  * universe: Nifty 100
  * daily bars
  * SMA(6) crossing SMA(30) -> bullish; crossing back down -> bearish
  * signals ranked by recency of the crossover (most recent ranks highest)

CAUSALITY CONTRACT
Every value this module produces for date ``t`` is a function of rows with
index <= t only. Concretely:
  * ``rolling(n).mean()`` at t averages the symbol's last n bars up to t.
  * a crossover at t compares the state at t against the state at its previous
    bar.
  * ``bars_since_cross`` at t counts back from t.
There is no ``shift(-1)``, no ``center=True``, no ``bfill``, and nothing is
computed over the full sample. ``tests/test_no_leak.py`` enforces this by
truncating the input and asserting the output is unchanged.

PER-SYMBOL CALENDARS
The wide price panel is indexed on the union of every symbol's trading days, so
a symbol that did not print a bar on some day carries a NaN there. Running
``rolling(30)`` straight across that panel would blank thirty days of signal
because of one missing bar, and would silently drop that name from the
candidate list. So every rolling quantity is computed on the symbol's OWN bar
sequence and then reindexed back onto the union calendar. Compressing out the
gaps cannot introduce lookahead -- it only removes rows -- and the truncation
test confirms it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _per_symbol_sma(close: pd.DataFrame, window: int) -> pd.DataFrame:
    out = {}
    for col in close.columns:
        s = close[col].dropna()
        out[col] = s.rolling(window, min_periods=window).mean().reindex(close.index)
    return pd.DataFrame(out, index=close.index, columns=close.columns)


def moving_averages(close: pd.DataFrame, short: int, long: int
                    ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Trailing SMAs on each symbol's own bar sequence. ``min_periods`` equals
    the window, so the first ``window - 1`` bars are NaN rather than being
    computed off a partial window."""
    return _per_symbol_sma(close, short), _per_symbol_sma(close, long)


def crossover_state(close: pd.DataFrame, short: int, long: int) -> dict[str, pd.DataFrame]:
    """Return the full causal signal state.

    bull_cross / bear_cross : True on the bar the crossover completes
    regime                  : +1 while SMA_short > SMA_long, -1 below, 0 unknown
    bars_since_cross        : bars elapsed since the most recent crossover
    """
    sma_s, sma_l = moving_averages(close, short, long)

    bull = pd.DataFrame(False, index=close.index, columns=close.columns)
    bear = pd.DataFrame(False, index=close.index, columns=close.columns)
    regime = pd.DataFrame(np.nan, index=close.index, columns=close.columns)

    for col in close.columns:
        # Restrict to the bars this symbol actually has a computable SMA pair
        # for, so "the previous bar" means the previous bar of THIS symbol.
        pair = pd.concat([sma_s[col], sma_l[col]], axis=1).dropna()
        if pair.empty:
            continue
        above = pair.iloc[:, 0].to_numpy() > pair.iloc[:, 1].to_numpy()
        regime.loc[pair.index, col] = np.where(above, 1.0, -1.0)

        if len(above) > 1:
            idx = pair.index[1:]
            up = above[1:] & ~above[:-1]
            dn = ~above[1:] & above[:-1]
            if up.any():
                bull.loc[idx[up], col] = True
            if dn.any():
                bear.loc[idx[dn], col] = True

    # Carry the regime forward across days a symbol did not trade, so a held
    # position is still evaluated on the next day it does. ffill only reads the
    # past, so this stays causal.
    regime = regime.ffill().fillna(0).astype(np.int8)

    cross = bull | bear
    return {
        "sma_short": sma_s,
        "sma_long": sma_l,
        "regime": regime,
        "bull_cross": bull,
        "bear_cross": bear,
        "bars_since_cross": _bars_since(cross),
    }


def _bars_since(flag: pd.DataFrame) -> pd.DataFrame:
    """For each column, bars elapsed since the last True at or before t.

    Backward looking only: the value at t depends on flags at indices <= t.
    NaN until the first True.
    """
    idx = np.arange(len(flag))
    out = {}
    for col in flag.columns:
        f = flag[col].to_numpy(dtype=bool)
        last = np.where(f, idx, -1)
        last = np.maximum.accumulate(last)      # most recent True at index <= t
        out[col] = np.where(last >= 0, idx - last, np.nan)
    return pd.DataFrame(out, index=flag.index, columns=flag.columns)


def signals_asof(close: pd.DataFrame, short: int, long: int,
                 asof: pd.Timestamp | None = None,
                 lookback: int = 30) -> pd.DataFrame:
    """The scanner's output: one row per stock that crossed within ``lookback``
    bars of ``asof``, ranked by recency. This is the table the video builds.

    Only rows with index <= asof are used. Prices come from the symbol's own
    most recent bar at or before ``asof``, reported with that bar's date, so a
    name that did not trade on the final session is shown honestly rather than
    as a blank.
    """
    if asof is None:
        asof = close.index[-1]
    hist = close.loc[:asof]
    st = crossover_state(hist, short, long)

    last = hist.index[-1]
    rows = []
    for sym in hist.columns:
        since = st["bars_since_cross"].at[last, sym]
        if pd.isna(since) or since > lookback:
            continue
        cross_idx = hist.index[hist.index.get_loc(last) - int(since)]
        is_bull = bool(st["bull_cross"].at[cross_idx, sym])

        bar = hist[sym].last_valid_index()
        s_short = st["sma_short"][sym].dropna()
        s_long = st["sma_long"][sym].dropna()
        if bar is None or s_short.empty or s_long.empty:
            continue
        px = float(hist.at[bar, sym])
        ss, sl = float(s_short.iloc[-1]), float(s_long.iloc[-1])

        rows.append({
            "symbol": sym,
            "signal": "BULLISH" if is_bull else "BEARISH",
            "crossover_date": cross_idx.date(),
            "bars_since": int(since),
            "close": round(px, 2),
            "price_date": bar.date(),
            "sma_short": round(ss, 2),
            "sma_long": round(sl, 2),
            "spread_pct": round((ss / sl - 1) * 100, 2),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["bars_since", "symbol"]).reset_index(drop=True)
