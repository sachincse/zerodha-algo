"""The order sheet — the module that decides what a real order would be.

This had no tests at all, which is a poor place for a coverage hole: it is the
last thing between a scan and a broker, and its default was quietly changed
this session (max_bars_since 3 -> 0) with nothing to catch a mistake.

The two properties that matter most here are safety properties, not
correctness ones:

  * a SELL is only ever proposed for something actually held, because equity
    delivery in India cannot be sold short overnight;
  * nothing is proposed that the backtest did not test, which is why
    max_bars_since defaults to 0 — the backtest enters ONLY on the crossover
    bar and uses recency purely to rank.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.orders import build_order_sheet, format_sheet


def scan(rows) -> pd.DataFrame:
    """rows: (symbol, signal, bars_since, close)"""
    return pd.DataFrame(
        [{"symbol": s, "signal": sig, "bars_since": b, "close": c,
          "crossover_date": "2026-08-20"} for s, sig, b, c in rows])


BULL = [("AAA", "BULLISH", 0, 100.0),
        ("BBB", "BULLISH", 0, 200.0),
        ("CCC", "BULLISH", 2, 50.0)]


# --------------------------------------------------------------------------
# the safety property: never a short
# --------------------------------------------------------------------------

def test_a_bearish_signal_on_something_not_held_is_not_an_order():
    """The single most dangerous mistake this module could make."""
    orders, notes = build_order_sheet(scan([("ZZZ", "BEARISH", 0, 100.0)]),
                                      holdings={})
    assert [o for o in orders if o.transaction_type == "SELL"] == []
    assert any("cannot be shorted" in n for n in notes)


def test_a_bearish_signal_on_something_held_becomes_an_exit():
    orders, _ = build_order_sheet(scan([("ZZZ", "BEARISH", 0, 100.0)]),
                                 holdings={"ZZZ": 7})
    sells = [o for o in orders if o.transaction_type == "SELL"]
    assert len(sells) == 1
    assert sells[0].quantity == 7          # the whole holding, not a guess
    assert sells[0].tradingsymbol == "ZZZ"


def test_an_exit_never_sells_more_than_is_held():
    orders, _ = build_order_sheet(scan([("ZZZ", "BEARISH", 0, 100.0)]),
                                 holdings={"ZZZ": 3})
    assert all(o.quantity <= 3 for o in orders if o.transaction_type == "SELL")


# --------------------------------------------------------------------------
# entries match what the backtest actually tested
# --------------------------------------------------------------------------

def test_stale_signals_are_skipped_by_default():
    """The default is 0 because the backtest entered ONLY on the crossover bar.

    A default of 3 proposed trades no published number describes.
    """
    orders, notes = build_order_sheet(scan(BULL), holdings={})
    bought = {o.tradingsymbol for o in orders}
    assert bought == {"AAA", "BBB"}, "CCC is 2 bars old and should be skipped"
    assert any("older than 0 bars" in n for n in notes)


def test_raising_the_window_is_allowed_but_says_so():
    """Widening it is supported — it just stops being the tested rule, and the
    sheet has to say that out loud rather than silently drifting."""
    orders, notes = build_order_sheet(scan(BULL), holdings={}, max_bars_since=3)
    assert {o.tradingsymbol for o in orders} == {"AAA", "BBB", "CCC"}
    assert any("do not describe those trades" in n for n in notes), notes


def test_the_default_carries_no_such_warning():
    _, notes = build_order_sheet(scan(BULL), holdings={})
    assert not any("do not describe those trades" in n for n in notes)


# --------------------------------------------------------------------------
# sizing and slots
# --------------------------------------------------------------------------

def test_slots_account_for_what_is_already_held():
    """Two held names that are not being exited leave eight slots of ten."""
    held = {f"H{i}": 10 for i in range(8)}
    orders, _ = build_order_sheet(scan(BULL), holdings=held, max_positions=10)
    assert len([o for o in orders if o.transaction_type == "BUY"]) == 2


def test_a_full_book_proposes_nothing():
    held = {f"H{i}": 10 for i in range(10)}
    orders, notes = build_order_sheet(scan(BULL), holdings=held, max_positions=10)
    assert [o for o in orders if o.transaction_type == "BUY"] == []
    assert any("no free slots" in n for n in notes)


def test_sizing_is_equal_weight_and_never_overspends_a_slot():
    orders, _ = build_order_sheet(scan(BULL), holdings={},
                                  capital=1_000_000, max_positions=10)
    for o in (o for o in orders if o.transaction_type == "BUY"):
        assert o.est_value <= 100_000, f"{o.tradingsymbol} exceeds its slot"
        assert o.quantity >= 1


def test_a_share_dearer_than_the_slot_is_skipped_with_a_reason():
    """A Rs 200,000 share against a Rs 100,000 slot yields qty 0. Proposing a
    zero-quantity order would be rejected by the broker with a worse message."""
    orders, notes = build_order_sheet(scan([("RICH", "BULLISH", 0, 200_000.0)]),
                                      holdings={}, capital=1_000_000,
                                      max_positions=10)
    assert orders == []
    assert any("above the Rs" in n for n in notes)


def test_exits_are_proposed_before_entries():
    """They free both cash and slots, so order matters when acting top-down."""
    rows = [("HELD", "BEARISH", 0, 100.0)] + BULL
    orders, _ = build_order_sheet(scan(rows), holdings={"HELD": 5})
    sides = [o.transaction_type for o in orders]
    assert sides[0] == "SELL"
    assert "BUY" in sides and sides.index("SELL") < sides.index("BUY")


# --------------------------------------------------------------------------
# the handoff to the broker
# --------------------------------------------------------------------------

def test_kite_kwargs_carry_no_price_and_no_extras():
    """as_kite_kwargs feeds place_order directly. An unexpected key there is a
    TypeError at the worst possible moment."""
    orders, _ = build_order_sheet(scan(BULL), holdings={})
    kw = orders[0].as_kite_kwargs()
    assert set(kw) == {"tradingsymbol", "exchange", "transaction_type",
                       "quantity", "product", "order_type"}
    assert kw["product"] == "CNC" and kw["order_type"] == "MARKET"
    assert kw["quantity"] > 0


def test_an_empty_scan_explains_itself():
    orders, notes = build_order_sheet(pd.DataFrame(), holdings={})
    assert orders == []
    assert notes and "no signals" in notes[0]


def test_format_sheet_handles_no_orders():
    assert "(no orders)" in format_sheet([], ["nothing to do"])


@pytest.mark.parametrize("held", [{}, {"AAA": 5}, {"AAA": 5, "BBB": 3}])
def test_nothing_is_ever_proposed_twice_for_one_symbol(held):
    rows = BULL + [("AAA", "BEARISH", 0, 100.0)]
    orders, _ = build_order_sheet(scan(rows), holdings=held)
    syms = [o.tradingsymbol for o in orders]
    assert len(syms) == len(set(syms)), f"duplicate orders: {syms}"
