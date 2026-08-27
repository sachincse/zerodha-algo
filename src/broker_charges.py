"""Price charges through Zerodha instead of modelling them.

The cost model in config.py is a careful transcription of published rates, and
the study's loudest finding rests on it: charges consumed 95% of gross trading
gains. A transcription can be out of date, can miss a slab, and cannot be
audited by a reader. Kite exposes `/charges/orders`, which returns the broker's
own arithmetic for orders you have NOT placed - the same numbers that would
appear on a real contract note.

So the model becomes checkable. `verify_charges.py` prices a grid of orders
both ways and fails if they disagree by more than a whisker.

Nothing here places an order. get_virtual_contract_note is a pricing call.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .config import CostModel


@dataclass(frozen=True)
class Quote:
    """What the broker says one order costs."""
    turnover: float
    brokerage: float
    stt: float
    exchange_txn: float
    sebi: float
    stamp: float
    gst: float
    total: float

    @property
    def bps(self) -> float:
        return self.total / self.turnover * 1e4 if self.turnover else 0.0


def _order(i: int, symbol: str, qty: int, price: float, side: str,
           product: str, exchange: str = "NSE") -> dict:
    return {
        "order_id": str(i),
        "exchange": exchange,
        "tradingsymbol": symbol,
        "transaction_type": side,
        "variety": "regular",
        "product": product,
        "order_type": "MARKET",
        "quantity": int(qty),
        "average_price": float(price),
    }


def _num(d: dict, *path, default=0.0) -> float:
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    try:
        return float(cur)
    except (TypeError, ValueError):
        return default


def parse_quote(row: dict) -> Quote:
    """Flatten one /charges/orders row.

    The response nests the statutory pieces under `charges`, with GST under
    `charges.gst`. Field names are read defensively: this is a live API whose
    shape is documented but not versioned, and a silent zero here would make
    the model look accurate rather than unverified.
    """
    ch = row.get("charges", row)
    turnover = _num(row, "total_price") or _num(row, "turnover")
    if not turnover:
        turnover = _num(row, "quantity") * _num(row, "average_price")

    gst = (_num(ch, "gst", "total")
           or _num(ch, "gst", "cgst") + _num(ch, "gst", "sgst") + _num(ch, "gst", "igst"))

    return Quote(
        turnover=turnover,
        brokerage=_num(ch, "brokerage"),
        stt=_num(ch, "transaction_tax"),
        exchange_txn=_num(ch, "exchange_turnover_charge"),
        sebi=_num(ch, "sebi_turnover_charge"),
        stamp=_num(ch, "stamp_duty"),
        gst=gst,
        total=_num(ch, "total"),
    )


def quote_basket(kite, orders: list[dict]) -> list[Quote]:
    """Ask the broker to price a list of hypothetical orders."""
    resp = kite.get_virtual_contract_note(orders)
    rows = resp if isinstance(resp, list) else resp.get("data", resp)
    return [parse_quote(r) for r in rows]


def model_cost(cost: CostModel, turnover: float, side: str,
               product: str = "CNC") -> float:
    """The same order priced by the hand-written model, for comparison.

    DP charge is deliberately excluded: it is levied per scrip per sell DAY,
    not per order, so a per-order contract note cannot contain it and including
    it here would manufacture a disagreement.
    """
    if side == "BUY":
        return cost.buy_cost(turnover, product)
    return cost.sell_cost(turnover, product, include_dp=False)


def kite_from_env():
    """A read-only client from KITE_API_KEY + KITE_ACCESS_TOKEN.

    Kept out of the library path on purpose - the study runs offline, and this
    is the one place that needs a live session.
    """
    from kiteconnect import KiteConnect

    api_key = os.getenv("KITE_API_KEY", "").strip()
    token = os.getenv("KITE_ACCESS_TOKEN", "").strip()
    if not api_key or not token:
        raise RuntimeError(
            "Set KITE_API_KEY and KITE_ACCESS_TOKEN to price charges through "
            "the broker. The access token is the one minted by a Kite login; "
            "it expires at 6 AM IST like every other Kite session.")
    k = KiteConnect(api_key=api_key)
    k.set_access_token(token)
    return k


# A grid that spans the sizes this strategy actually trades. The backtest sizes
# positions at equity/10 on Rs 10 lakh, so Rs 1 lakh is the typical order and
# the tails matter for the per-order caps.
DEFAULT_GRID = [
    ("RELIANCE", 8, 1_250.0),        # ~Rs 10k
    ("RELIANCE", 40, 1_250.0),       # ~Rs 50k
    ("RELIANCE", 80, 1_250.0),       # ~Rs 1 lakh - the typical slot
    ("RELIANCE", 400, 1_250.0),      # ~Rs 5 lakh
    ("INFY", 60, 1_650.0),           # ~Rs 1 lakh, different scrip
    ("IDEA", 10_000, 7.5),           # penny stock: caps and rounding bite here
]


def build_grid(product: str = "CNC") -> list[dict]:
    orders = []
    i = 0
    for symbol, qty, price in DEFAULT_GRID:
        for side in ("BUY", "SELL"):
            i += 1
            orders.append(_order(i, symbol, qty, price, side, product))
    return orders
