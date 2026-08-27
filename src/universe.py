"""Universe construction -- where survivorship bias lives or dies.

Two modes are provided so the two can be compared directly.

``current_index``
    Use today's Nifty 100 membership for the whole history. This is what the
    video's scanner does and what almost every casual backtest does. It is
    LOOKAHEAD: you are choosing, in 2015, to trade the stocks that you know
    turned out to still be in the Nifty 100 in 2026. Names that fell out of the
    index -- usually after underperforming -- are silently excluded, and names
    that were never in it but got promoted are silently included early.

``pit_turnover``
    Rebalance monthly. On each rebalance date, rank the candidate pool by
    trailing 60-day median traded value using only bars at or before that date,
    and take the top N. This is genuinely point-in-time: the selection at any
    date is computable from that date's information alone. It tracks the real
    Nifty 100 closely, because NSE's own index selection is driven by free-float
    market cap and liquidity.

    RESIDUAL BIAS: the candidate pool itself is drawn from currently listed
    symbols. Companies that were delisted, merged away, or renamed are absent,
    because free daily history for them is not retrievable from Yahoo. This
    understates losses. Use ``report_residual_bias`` to state the gap honestly
    rather than hiding it.
"""
from __future__ import annotations

import pandas as pd

from .data import PriceStore


def current_index_membership(store: PriceStore, symbols: list[str]) -> pd.DataFrame:
    """True wherever the symbol is in today's index list and has a real bar.

    Survivorship-biased on purpose -- this is the comparison case.
    """
    cl = store.panels["Close"]
    member = pd.DataFrame(False, index=cl.index, columns=cl.columns)
    keep = [s for s in symbols if s in cl.columns]
    member[keep] = cl[keep].notna()
    return member


def pit_turnover_membership(
    store: PriceStore,
    top_n: int = 100,
    lookback: int = 60,
    min_history: int = 200,
    rebalance: str = "ME",
) -> pd.DataFrame:
    """Point-in-time top-N-by-liquidity universe, rebalanced monthly.

    At each rebalance date r the ranking uses ``close * volume`` over the
    trailing ``lookback`` bars ending at r. Nothing after r is read. The chosen
    set then applies to every date strictly after r, up to and including the
    next rebalance date -- so the set in force on any trading day was decided on
    an earlier day.
    """
    cl = store.panels["Close"]
    vol = store.panels["Volume"]

    traded_value = (cl * vol).rolling(lookback, min_periods=lookback // 2).median()
    history = cl.notna().cumsum()          # bars of history available as of t

    member = pd.DataFrame(False, index=cl.index, columns=cl.columns)

    rebal_dates = (
        pd.Series(cl.index, index=cl.index).resample(rebalance).last().dropna().tolist()
    )
    rebal_dates = [d for d in rebal_dates if d in cl.index]

    prev_selection: list[str] = []
    for k, r in enumerate(rebal_dates):
        row = traded_value.loc[r]
        eligible = row[(row.notna()) & (history.loc[r] >= min_history)]
        selection = eligible.sort_values(ascending=False).head(top_n).index.tolist()

        # Apply the PREVIOUS selection up to and including r, then switch. This
        # guarantees that the set active on date d was decided strictly before d.
        start = rebal_dates[k - 1] if k > 0 else cl.index[0]
        window = (cl.index > start) & (cl.index <= r)
        if prev_selection:
            member.loc[window, prev_selection] = True
        prev_selection = selection

    if prev_selection:
        member.loc[cl.index > rebal_dates[-1], prev_selection] = True

    # A name is only tradeable on a day it actually printed a bar.
    return member & cl.notna()


def report_residual_bias(store: PriceStore, symbols: list[str]) -> dict:
    """Quantify what the pool cannot see, so the number can be quoted."""
    cl = store.panels["Close"]
    have = [s for s in symbols if s in cl.columns]
    missing = [s for s in symbols if s not in cl.columns]
    first_bar = {s: cl[s].first_valid_index() for s in have}
    last_bar = {s: cl[s].last_valid_index() for s in have}
    end = cl.index[-1]
    stopped = [s for s, d in last_bar.items()
               if d is not None and (end - d).days > 30]
    return {
        "requested": len(symbols),
        "with_data": len(have),
        "no_data_at_all": missing,
        "stopped_trading_before_end": stopped,
        "listed_after_start": sorted(
            s for s, d in first_bar.items()
            if d is not None and d > cl.index[0] + pd.Timedelta(days=10)
        ),
    }
