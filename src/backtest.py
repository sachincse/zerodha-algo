"""Event-ordered daily backtester.

THE ANTI-LEAK INVARIANT
The loop processes each day in a fixed order:

    for t in dates:
        1. FILL orders that were queued at the close of t-1, at open[t].
        2. Credit dividends with ex-date t on positions already held.
        3. MARK the book to close[t].
        4. DECIDE: read prices up to and including t, queue orders for t+1.

Step 4 is the only step that reads signals, and it happens after the book is
already marked, so nothing it computes can retroactively change today's P&L.
Step 1 only ever consumes a queue built on a previous iteration. A decision made
on day t therefore cannot be filled earlier than open[t+1], which is the first
price a real trader could have transacted at.

This is deliberately a Python loop rather than vectorised pandas. It is slower,
but a reviewer can read it top to bottom and see that no future row is touched.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Settings, DEFAULT
from .data import PriceStore
from .strategy import crossover_state


@dataclass
class Position:
    symbol: str
    qty: int
    entry_price: float          # net of slippage, excluding charges
    entry_date: pd.Timestamp
    cost_paid: float            # entry charges, rupees


@dataclass
class Trade:
    symbol: str
    qty: int
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: pd.Timestamp
    exit_price: float
    gross_pnl: float
    charges: float
    dividends: float
    net_pnl: float
    holding_days: int
    exit_reason: str

    @property
    def net_return_pct(self) -> float:
        return self.net_pnl / (self.entry_price * self.qty) * 100.0


@dataclass
class Order:
    symbol: str
    side: str          # BUY | SELL
    qty: int
    reason: str
    queued_on: pd.Timestamp


@dataclass
class BacktestResult:
    equity: pd.Series
    trades: list
    daily_positions: pd.Series
    rejected_fills: int
    settings: Settings
    label: str
    benchmark: pd.Series | None = None
    meta: dict = field(default_factory=dict)


def _slip(price: float, side: str, bps: float) -> float:
    """Slippage always works against us."""
    k = bps / 10_000.0
    return price * (1 + k) if side == "BUY" else price * (1 - k)


def _dividends_over(div: pd.DataFrame, sym: str,
                    entry: pd.Timestamp, exit_: pd.Timestamp) -> float:
    """Per-share dividends with ex-date strictly after entry and at or before
    exit. Entry is excluded because we buy at the open of the entry day, after
    that day's ex-adjustment is already reflected in its prices."""
    if sym not in div.columns:
        return 0.0
    s = div[sym]
    mask = (s.index > entry) & (s.index <= exit_)
    return float(s[mask].sum())


def run_backtest(
    store: PriceStore,
    membership: pd.DataFrame,
    settings: Settings = DEFAULT,
    label: str = "sma_crossover",
    exit_on_bear_cross: bool = True,
    max_holding_days: int | None = None,
    stop_loss_pct: float | None = None,
    start: pd.Timestamp | None = None,
    leaky_same_bar_fill: bool = False,
) -> BacktestResult:
    """Run the long-only SMA crossover portfolio.

    ``membership`` is a boolean frame aligned to the price panel: True where the
    symbol is a member of the tradeable universe as known on that date. The
    caller is responsible for making it point-in-time; see ``universe.py``.

    ``leaky_same_bar_fill`` DELIBERATELY BREAKS THE INVARIANT. It fills at the
    close of the bar that generated the signal -- a price that was not knowable
    until the session had already ended. It exists only so the size of that
    single, extremely common mistake can be measured. Never enable it for a
    result you intend to act on.
    """
    cfg = settings
    op = store.panels["Open"]
    cl = store.panels["Close"]
    vol = store.panels["Volume"]
    div = store.dividends

    state = crossover_state(cl, cfg.short_window, cfg.long_window)
    regime = state["regime"]
    bull = state["bull_cross"]
    bear = state["bear_cross"]
    bars_since = state["bars_since_cross"]

    # Trailing liquidity filter, causal: median of the last 60 bars up to and
    # including t. Used at t to decide whether a name is tradeable at t+1.
    turnover = (cl * vol).rolling(60, min_periods=30).median()

    # Previous close per symbol, on that symbol's own bar sequence, for the
    # circuit-band check. shift(1) on a gap-compressed series, so a name that
    # did not trade yesterday compares against its real last close.
    prev_close = pd.DataFrame(
        {c: cl[c].dropna().shift(1).reindex(cl.index) for c in cl.columns},
        index=cl.index, columns=cl.columns)

    # Index the crossovers sparsely. On a typical day a handful of the 500 names
    # cross, so walking every column every day is ~500x more work than needed.
    # This is a pure lookup rewrite -- same rows, same order, no new information.
    cols = list(cl.columns)
    bull_np = bull.to_numpy()
    bull_by_row = [[cols[j] for j in np.flatnonzero(bull_np[i])]
                   for i in range(bull_np.shape[0])]

    dates = cl.index
    first = dates.searchsorted(start) if start is not None else cfg.long_window + 1
    first = max(int(first), cfg.long_window + 1)

    cash = cfg.portfolio.initial_capital
    positions = {}
    pending = []
    trades = []
    equity_curve = []
    npos_curve = []
    rejected = 0
    band_blocked = 0

    for i in range(first, len(dates)):
        t = dates[i]

        # ---- 1. FILL yesterday's queued orders at today's open --------------
        held_over: list[Order] = []
        for o in pending:
            if leaky_same_bar_fill:
                # LEAKY: use the close of the signal bar, which nobody could
                # have transacted at once they knew the signal had fired.
                o_open = cl.at[o.queued_on, o.symbol]
                o_vol = vol.at[o.queued_on, o.symbol]
            else:
                o_open = op.at[t, o.symbol] if o.symbol in op.columns else np.nan
                o_vol = vol.at[t, o.symbol] if o.symbol in vol.columns else np.nan

            tradeable = (pd.notna(o_open) and float(o_open) > 0
                         and pd.notna(o_vol) and float(o_vol) > 0)

            # A market order cannot transact into a locked circuit band. This is
            # asymmetric on purpose: a blocked ENTRY drops the signal, while a
            # blocked EXIT holds the position and retries tomorrow. Momentum
            # signals fire disproportionately on gap days, so the un-fillable
            # bars skew profitable -- dropping them is a real, one-directional
            # haircut, and pretending otherwise is where optimism creeps in.
            if tradeable and not leaky_same_bar_fill:
                prev_c = prev_close.at[t, o.symbol] if o.symbol in prev_close.columns else np.nan
                if pd.notna(prev_c) and float(prev_c) > 0:
                    gap = float(o_open) / float(prev_c) - 1
                    edge = cfg.execution.circuit_pct - cfg.execution.band_tolerance
                    if (o.side == "BUY" and gap >= edge) or \
                       (o.side == "SELL" and gap <= -edge):
                        tradeable = False
                        band_blocked += 1

            if not tradeable:
                rejected += 1
                if o.side == "SELL" and o.symbol in positions:
                    held_over.append(o)      # could not escape; retry tomorrow
                continue                     # the order lapses; no pretend fill

            if o.side == "BUY":
                px = _slip(float(o_open), "BUY", cfg.execution.slippage_bps)
                qty = o.qty
                gross = px * qty
                charge = cfg.costs.buy_cost(gross)
                if gross + charge > cash:            # re-size to available cash
                    qty = int((cash * 0.995) // px)
                    if qty <= 0:
                        rejected += 1
                        continue
                    gross = px * qty
                    charge = cfg.costs.buy_cost(gross)
                cash -= gross + charge
                positions[o.symbol] = Position(o.symbol, qty, px, t, charge)

            else:  # SELL
                p = positions.get(o.symbol)
                if p is None:
                    continue
                px = _slip(float(o_open), "SELL", cfg.execution.slippage_bps)
                gross = px * p.qty
                charge = cfg.costs.sell_cost(gross)
                held_div = _dividends_over(div, p.symbol, p.entry_date, t) * p.qty
                cash += gross - charge + held_div
                trades.append(Trade(
                    symbol=p.symbol, qty=p.qty,
                    entry_date=p.entry_date, entry_price=p.entry_price,
                    exit_date=t, exit_price=px,
                    gross_pnl=(px - p.entry_price) * p.qty,
                    charges=p.cost_paid + charge,
                    dividends=held_div,
                    net_pnl=(px - p.entry_price) * p.qty - p.cost_paid - charge + held_div,
                    holding_days=int((t - p.entry_date).days),
                    exit_reason=o.reason,
                ))
                del positions[o.symbol]
        pending = list(held_over)

        # ---- 2. MARK to today's close ---------------------------------------
        mtm = 0.0
        for sym, p in positions.items():
            px = cl.at[t, sym]
            if pd.isna(px):                     # stale bar: carry last known
                prior = cl[sym].loc[:t].ffill()
                px = prior.iloc[-1] if len(prior) else p.entry_price
            mtm += float(px) * p.qty
        equity_curve.append(cash + mtm)
        npos_curve.append(len(positions))

        # ---- 3. DECIDE using data up to and including t ----------------------
        if i == len(dates) - 1:
            break                               # nothing left to fill tomorrow

        equity_now = cash + mtm

        # -- exits --
        for sym, p in list(positions.items()):
            reason = None
            if exit_on_bear_cross and bool(bear.at[t, sym]):
                reason = "bear_cross"
            elif exit_on_bear_cross and int(regime.at[t, sym]) == -1:
                reason = "regime_below"         # safety net if a cross was missed
            elif max_holding_days and (t - p.entry_date).days >= max_holding_days:
                reason = "max_holding"
            elif stop_loss_pct is not None:
                px_now = cl.at[t, sym]
                if pd.notna(px_now) and float(px_now) <= p.entry_price * (1 - stop_loss_pct):
                    reason = "stop_loss"
            if reason and not any(o.symbol == sym and o.side == "SELL"
                                  for o in pending):
                pending.append(Order(sym, "SELL", p.qty, reason, t))

        # -- entries --
        outgoing = {o.symbol for o in pending if o.side == "SELL"}
        free_slots = cfg.portfolio.max_positions - (len(positions) - len(outgoing))
        if free_slots > 0:
            queued = {o.symbol for o in pending}
            cands = []
            for sym in bull_by_row[i]:
                if sym in positions or sym in queued:
                    continue
                if not bool(membership.at[t, sym]):
                    continue
                px = cl.at[t, sym]
                if pd.isna(px) or float(px) < cfg.execution.min_price:
                    continue
                tv = turnover.at[t, sym]
                if pd.isna(tv) or float(tv) < cfg.execution.min_median_turnover:
                    continue
                cands.append((float(bars_since.at[t, sym]), sym))

            # Rank by recency of the crossover -- the video's rule. A fresh
            # cross (bars_since == 0) outranks an older one.
            cands.sort()
            budget_per_slot = equity_now / cfg.portfolio.max_positions
            projected_cash = cash
            for _, sym in cands[:free_slots]:
                ref_px = float(cl.at[t, sym])
                qty = int(budget_per_slot // (ref_px * 1.02))   # headroom for a gap up
                if qty <= 0:
                    continue
                est = ref_px * qty * 1.02
                if est > projected_cash:
                    qty = int((projected_cash * 0.98) // (ref_px * 1.02))
                    if qty <= 0:
                        continue
                    est = ref_px * qty * 1.02
                projected_cash -= est
                pending.append(Order(sym, "BUY", qty, "bull_cross", t))

    eq = pd.Series(equity_curve, index=dates[first:first + len(equity_curve)], name=label)
    np_s = pd.Series(npos_curve, index=eq.index, name="n_positions")
    return BacktestResult(
        equity=eq, trades=trades, daily_positions=np_s,
        rejected_fills=rejected, settings=cfg, label=label,
        meta={"band_blocked_fills": band_blocked},
    )
