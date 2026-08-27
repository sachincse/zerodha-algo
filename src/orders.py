"""Turn a scan into a reviewable order sheet.

This module deliberately stops one step short of trading. It sizes positions and
prints exactly what would be sent, and nothing here can reach a broker. Order
placement happens through the Kite MCP, one call at a time, after you have read
the sheet and said yes.

The sizing rule mirrors the backtest so that live behaviour and tested
behaviour do not silently diverge:
  * equal weight across ``max_positions`` slots
  * a slot is only filled by a BULLISH signal that is fresh (``bars_since`` <=
    ``max_bars_since``)
  * BEARISH rows are exits for names you already hold; they are never shorts,
    because equity delivery in India cannot be sold short overnight
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class ProposedOrder:
    tradingsymbol: str
    exchange: str
    transaction_type: str      # BUY | SELL
    quantity: int
    product: str               # CNC
    order_type: str            # MARKET
    est_price: float
    est_value: float
    rationale: str

    def as_kite_kwargs(self) -> dict:
        """Exactly the arguments the Kite place_order call takes."""
        return {
            "tradingsymbol": self.tradingsymbol,
            "exchange": self.exchange,
            "transaction_type": self.transaction_type,
            "quantity": self.quantity,
            "product": self.product,
            "order_type": self.order_type,
        }


def build_order_sheet(
    scan: pd.DataFrame,
    holdings: dict[str, int] | None = None,
    capital: float = 1_000_000.0,
    max_positions: int = 10,
    max_bars_since: int = 3,
    exchange: str = "NSE",
) -> tuple[list[ProposedOrder], list[str]]:
    """Return (orders, notes).

    ``holdings`` maps tradingsymbol -> quantity currently held, so exits can be
    proposed only for things you actually own. Pass what
    ``mcp__kite__get_holdings`` returns.
    """
    holdings = holdings or {}
    notes: list[str] = []
    orders: list[ProposedOrder] = []

    if scan.empty:
        return orders, ["scan produced no signals"]

    # ---- exits first: they free both cash and slots ----------------------
    bearish = scan[scan["signal"] == "BEARISH"]
    for _, r in bearish.iterrows():
        qty = holdings.get(r["symbol"], 0)
        if qty > 0:
            orders.append(ProposedOrder(
                tradingsymbol=r["symbol"], exchange=exchange,
                transaction_type="SELL", quantity=int(qty),
                product="CNC", order_type="MARKET",
                est_price=float(r["close"]), est_value=float(r["close"]) * qty,
                rationale=f"bearish cross {r['crossover_date']} "
                          f"({r['bars_since']} bars ago)"))
    held_after = {s: q for s, q in holdings.items()
                  if s not in set(bearish["symbol"])}

    n_bear_not_held = len(bearish) - sum(1 for o in orders if o.transaction_type == "SELL")
    if n_bear_not_held:
        notes.append(f"{n_bear_not_held} bearish signals ignored -- not held, and "
                     f"equity delivery cannot be shorted overnight in India")

    # ---- entries ----------------------------------------------------------
    free_slots = max_positions - len(held_after)
    if free_slots <= 0:
        notes.append(f"no free slots ({len(held_after)}/{max_positions} held)")
        return orders, notes

    fresh = scan[(scan["signal"] == "BULLISH")
                 & (scan["bars_since"] <= max_bars_since)
                 & (~scan["symbol"].isin(held_after))]
    stale_bulls = int(((scan["signal"] == "BULLISH")
                       & (scan["bars_since"] > max_bars_since)).sum())
    if stale_bulls:
        notes.append(f"{stale_bulls} bullish signals skipped -- older than "
                     f"{max_bars_since} bars; the backtest ranks on recency")

    budget = capital / max_positions
    for _, r in fresh.head(free_slots).iterrows():
        px = float(r["close"])
        qty = int(budget // px)
        if qty <= 0:
            notes.append(f"{r['symbol']} skipped -- one share costs "
                         f"Rs {px:,.0f}, above the Rs {budget:,.0f} slot budget")
            continue
        orders.append(ProposedOrder(
            tradingsymbol=r["symbol"], exchange=exchange,
            transaction_type="BUY", quantity=qty,
            product="CNC", order_type="MARKET",
            est_price=px, est_value=px * qty,
            rationale=f"bullish cross {r['crossover_date']} "
                      f"({r['bars_since']} bars ago)"))

    return orders, notes


def format_sheet(orders: list[ProposedOrder], notes: list[str]) -> str:
    if not orders:
        body = "  (no orders)"
    else:
        w = max(len(o.tradingsymbol) for o in orders)
        rows = [f"  {o.transaction_type:4s} {o.quantity:>6d}  {o.tradingsymbol:<{w}}  "
                f"@ ~{o.est_price:>10,.2f}  = Rs {o.est_value:>11,.0f}   {o.rationale}"
                for o in orders]
        buy = sum(o.est_value for o in orders if o.transaction_type == "BUY")
        sell = sum(o.est_value for o in orders if o.transaction_type == "SELL")
        rows += ["", f"  buy side  Rs {buy:,.0f}    sell side  Rs {sell:,.0f}"
                     f"    net Rs {buy - sell:+,.0f}"]
        body = "\n".join(rows)

    out = ["PROPOSED ORDERS  (nothing has been sent)", "", body]
    if notes:
        out += ["", "notes:"] + [f"  - {n}" for n in notes]
    out += ["", "Prices are last close, not live. Market orders will fill at the "
                "open, which can gap.", "Review, then approve each order explicitly."]
    return "\n".join(out)
