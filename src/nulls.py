"""Null hypotheses a timing rule has to beat.

Comparing a long/flat crossover against zero, or against nothing at all, is not
a test. A rule that is in the market 90% of the time on large caps is mostly a
beta overlay, so the honest questions are narrower:

1. ``pit_equal_weight``   -- would simply owning the same point-in-time universe,
                             with no timing at all and the same cost model, have
                             done better? This separates the universe from the rule.

2. ``random_timing_null`` -- if you kept the number of trades and the holding-period
                             distribution but chose entries at random from the same
                             universe on the same days, what would you have got?
                             This is the correct null for a *timing* rule: it asks
                             whether the crossover picked better moments than a coin.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import CostModel
from .data import PriceStore


def pit_equal_weight(store: PriceStore, membership: pd.DataFrame,
                     costs: CostModel, capital: float = 1_000_000.0,
                     slippage_bps: float = 25.0,
                     start: pd.Timestamp | None = None,
                     rebalance: str = "ME") -> pd.Series:
    """Equal-weight the whole point-in-time universe, rebalanced monthly, with
    the same charges and slippage the strategy pays. No timing whatsoever."""
    cl = store.panels["Close"]
    idx = cl.index if start is None else cl.index[cl.index >= start]
    cl = cl.loc[idx]
    mem = membership.loc[idx]

    # Dividends belong in this null, and were missing.
    #
    # The strategy credits every dividend it holds through, and the benchmark
    # is a total-return index. This null was pure price return, so it was the
    # odd one out — and the error ran in the STRATEGY's favour, because the
    # thing it is losing to was being understated. An honest null has to be
    # measured the same way as the thing it is a null for.
    #
    # Credited on the ex-date against the previous close, which is what a
    # holder actually receives.
    rets = cl.pct_change()
    div = getattr(store, "dividends", None)
    if div is not None and len(div):
        d = div.reindex(index=idx, columns=cl.columns).fillna(0.0)
        rets = rets.add(d.div(cl.shift(1)).replace([np.inf, -np.inf], 0.0)
                        .fillna(0.0), fill_value=0.0)

    w = mem.div(mem.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)

    # Hold last month's weights through the month: the set in force on any day
    # was decided on an earlier day, same as the strategy's universe.
    marks = pd.Series(idx, index=idx).resample(rebalance).last().dropna()
    held = w.reindex(marks.values).reindex(idx).ffill().fillna(0.0)

    port = (held.shift(1) * rets).sum(axis=1).fillna(0.0)

    # Rebalance cost: one-way turnover x (round-trip charges + slippage)/2.
    rt_bps = (costs.buy_cost(1e5) + costs.sell_cost(1e5)) / 1e5 * 10_000
    cost_bps = (rt_bps + 2 * slippage_bps) / 2 / 10_000
    turnover = held.diff().abs().sum(axis=1).fillna(0.0)
    port = port - turnover * cost_bps

    return (1 + port).cumprod() * capital


def random_timing_null(store: PriceStore, membership: pd.DataFrame,
                       trades: list, costs: CostModel,
                       slippage_bps: float = 25.0,
                       n_draws: int = 1000, seed: int = 11) -> dict:
    """Bootstrap the strategy's own trade schedule onto random entries.

    Keeps the number of trades and the exact holding-period distribution the
    strategy realised, but draws the entry date uniformly over the sample and
    the symbol uniformly from the universe eligible on that date. Returns the
    distribution of mean net trade return across draws, and the percentile the
    strategy's own mean sits at.
    """
    rng = np.random.default_rng(seed)
    op = store.panels["Open"]
    dates = op.index
    n = len(dates)

    holds = np.array([max(1, (t.exit_date - t.entry_date).days) for t in trades])
    # convert calendar holding days to bar counts using the observed mapping
    bar_holds = []
    for t in trades:
        i0, i1 = dates.get_loc(t.entry_date), dates.get_loc(t.exit_date)
        bar_holds.append(max(1, i1 - i0))
    bar_holds = np.array(bar_holds)

    mem_np = membership.to_numpy()
    op_np = op.to_numpy()
    cols = np.array(op.columns)

    # Pre-index eligible symbols per row once.
    eligible = [np.flatnonzero(mem_np[i] & np.isfinite(op_np[i])) for i in range(n)]
    usable = np.array([i for i in range(n) if len(eligible[i]) > 0])

    k = len(bar_holds)
    slip = slippage_bps / 10_000.0
    means = np.empty(n_draws)

    # The strategy's own entry rows, for the date-matched variant below.
    own_rows = np.array([dates.get_loc(t.entry_date) for t in trades])
    own_rows = own_rows[np.isin(own_rows, usable)]

    for d in range(n_draws):
        rows = rng.choice(usable, size=k, replace=True)
        hold = rng.permutation(bar_holds)
        rets = np.empty(k)
        for j in range(k):
            i0 = rows[j]
            i1 = min(i0 + int(hold[j]), n - 1)
            pool = eligible[i0]
            c = pool[rng.integers(len(pool))]
            p0, p1 = op_np[i0, c], op_np[i1, c]
            if not np.isfinite(p0) or not np.isfinite(p1) or p0 <= 0:
                rets[j] = 0.0
                continue
            buy = p0 * (1 + slip)
            sell = p1 * (1 - slip)
            notional = 1e5
            qty = notional / buy
            gross = (sell - buy) * qty
            charges = costs.buy_cost(buy * qty) + costs.sell_cost(sell * qty)
            rets[j] = (gross - charges) / notional
        means[d] = rets.mean()

    # ---- date-matched variant --------------------------------------------
    # Same entry DAYS the strategy chose, same holding periods, but a random
    # eligible symbol each time. This separates two different ways to be wrong:
    # picking bad days, and picking bad stocks on reasonable days.
    dm = np.empty(n_draws)
    kk = len(own_rows)
    for d in range(n_draws):
        hold = rng.permutation(bar_holds)[:kk]
        rets = np.empty(kk)
        for j in range(kk):
            i0 = own_rows[j]
            i1 = min(i0 + int(hold[j]), n - 1)
            pool = eligible[i0]
            c = pool[rng.integers(len(pool))]
            p0, p1 = op_np[i0, c], op_np[i1, c]
            if not np.isfinite(p0) or not np.isfinite(p1) or p0 <= 0:
                rets[j] = 0.0
                continue
            buy, sell = p0 * (1 + slip), p1 * (1 - slip)
            qty = 1e5 / buy
            charges = costs.buy_cost(buy * qty) + costs.sell_cost(sell * qty)
            rets[j] = ((sell - buy) * qty - charges) / 1e5
        dm[d] = rets.mean()

    actual = float(np.mean([t.net_pnl / (t.entry_price * t.qty) for t in trades]))
    pct = float((means < actual).mean() * 100)
    dm_pct = float((dm < actual).mean() * 100)
    return {
        "n_draws": n_draws,
        "n_trades": k,
        "strategy_mean_trade_pct": actual * 100,
        "null_mean_trade_pct": float(means.mean() * 100),
        "null_p05_pct": float(np.percentile(means, 5) * 100),
        "null_p95_pct": float(np.percentile(means, 95) * 100),
        "strategy_percentile": pct,
        "beats_null": pct > 95.0,
        # date-matched: strategy's own days, random stock picks
        "datematched_mean_trade_pct": float(dm.mean() * 100),
        "datematched_p05_pct": float(np.percentile(dm, 5) * 100),
        "datematched_p95_pct": float(np.percentile(dm, 95) * 100),
        "datematched_percentile": dm_pct,
        "beats_datematched": dm_pct > 95.0,
    }
