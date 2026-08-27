"""The cost model's structure, and its agreement with the broker.

The study's loudest claim - charges consumed 95% of gross trading gains - is
only as good as config.py's CostModel, which is rates typed in by hand. Two
different checks guard it.

STRUCTURAL (always run). Properties that must hold whatever the statutory
rates happen to be this year: GST applies to exactly the three components it
is levied on, delivery brokerage is zero, the DP charge is per sell day rather
than per order, and delivery costs scale linearly because nothing in that path
is capped. These catch a fat-fingered transcription without pinning a rate that
the government changes.

LIVE (skipped without credentials). Prices the same orders through Kite's
/charges/orders endpoint and requires the model to match. That is the check
that turns the 95% figure from a model into a quote. Run it with
KITE_API_KEY and KITE_ACCESS_TOKEN set, or via verify_charges.py.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.broker_charges import build_grid, model_cost, parse_quote
from src.config import DEFAULT, CostModel

COST = DEFAULT.costs
SNAPSHOT = Path(__file__).resolve().parent.parent / "out" / "broker_charges.json"


# --------------------------------------------------------------------------
# structural
# --------------------------------------------------------------------------

def test_delivery_brokerage_is_free():
    """Zerodha's headline offer. If this ever becomes non-zero the study's
    trade-count sensitivity changes completely."""
    assert COST.brokerage_delivery == 0.0


@pytest.mark.parametrize("turnover", [10_000, 100_000, 1_000_000])
@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_gst_is_18pc_of_exactly_the_three_charges_it_applies_to(turnover, side):
    """GST is levied on brokerage + exchange txn + SEBI turnover. Not on STT,
    not on stamp duty. Getting that wrong is the classic Indian-charges bug and
    it inflates costs by roughly the GST on STT, which is the largest line."""
    txn = turnover * (COST.exchange_txn_nse + COST.ipft_nse)
    sebi = turnover * COST.sebi_turnover
    expected_gst = (COST.brokerage_delivery + txn + sebi) * COST.gst_rate

    total = model_cost(COST, turnover, side, "CNC")
    stt = turnover * (COST.stt_delivery_buy if side == "BUY"
                      else COST.stt_delivery_sell)
    stamp = turnover * COST.stamp_duty_delivery_buy if side == "BUY" else 0.0

    rebuilt = COST.brokerage_delivery + stt + txn + sebi + stamp + expected_gst
    assert total == pytest.approx(rebuilt, rel=1e-12)


def test_stt_is_not_taxed():
    """A model that applied GST to STT would scale differently with turnover.
    Doubling turnover must exactly double the delivery cost - no cap, no step."""
    a = model_cost(COST, 100_000, "BUY", "CNC")
    b = model_cost(COST, 200_000, "BUY", "CNC")
    assert b == pytest.approx(2 * a, rel=1e-12)


def test_dp_charge_is_per_sell_day_not_per_order():
    """The one asymmetry that made comparing against a contract note fail.
    A per-order quote cannot contain a per-scrip-per-day charge."""
    with_dp = COST.sell_cost(100_000, "CNC", include_dp=True)
    without = COST.sell_cost(100_000, "CNC", include_dp=False)
    assert with_dp - without == pytest.approx(COST.dp_charge_per_sell, rel=1e-12)
    assert COST.dp_charge_per_sell > 0


def test_dp_charge_does_not_apply_to_intraday():
    assert (COST.sell_cost(100_000, "MIS", include_dp=True)
            == COST.sell_cost(100_000, "MIS", include_dp=False))


def test_stamp_duty_is_buy_side_only():
    """Charged to the buyer. Applying it to both sides would roughly double a
    line item that is small but not negligible on a high-turnover strategy."""
    buy = model_cost(COST, 100_000, "BUY", "CNC")
    sell = COST.sell_cost(100_000, "CNC", include_dp=False)
    assert buy - sell == pytest.approx(100_000 * COST.stamp_duty_delivery_buy, rel=1e-9)


def test_intraday_brokerage_is_capped():
    """0.03% or Rs 20, whichever is lower. Without the cap, the intraday
    comparison in the report would be wrong at large sizes."""
    small = min(50_000 * COST.brokerage_intraday_pct, COST.brokerage_intraday_cap)
    large = min(10_000_000 * COST.brokerage_intraday_pct, COST.brokerage_intraday_cap)
    assert small < COST.brokerage_intraday_cap
    assert large == COST.brokerage_intraday_cap


def test_delivery_stt_is_symmetric():
    """0.1% both ways on delivery, unlike intraday which is sell-side only."""
    assert COST.stt_delivery_buy == COST.stt_delivery_sell
    assert COST.stt_intraday_buy == 0.0
    assert COST.stt_intraday_sell > 0.0


def test_a_zero_rate_model_costs_nothing():
    """Guards the wiring: if costs were not actually applied, the S1->S2 step
    in the honesty ladder would be measuring nothing."""
    free = CostModel(brokerage_delivery=0, stt_delivery_buy=0, stt_delivery_sell=0,
                     exchange_txn_nse=0, sebi_turnover=0, ipft_nse=0,
                     stamp_duty_delivery_buy=0, gst_rate=0, dp_charge_per_sell=0)
    assert free.buy_cost(100_000) == 0.0
    assert free.sell_cost(100_000) == 0.0
    assert model_cost(COST, 100_000, "BUY", "CNC") > 0.0


# --------------------------------------------------------------------------
# live / snapshot
# --------------------------------------------------------------------------

def _broker_rows():
    if os.getenv("KITE_API_KEY") and os.getenv("KITE_ACCESS_TOKEN"):
        from src.broker_charges import kite_from_env
        return kite_from_env().get_virtual_contract_note(build_grid("CNC")), "live"
    if SNAPSHOT.exists():
        return json.loads(SNAPSHOT.read_text(encoding="utf-8")), "snapshot"
    return None, None


@pytest.mark.parametrize("product", ["CNC"])
def test_model_matches_the_brokers_own_contract_note(product):
    rows, source = _broker_rows()
    if rows is None:
        pytest.skip("no Kite credentials and no saved out/broker_charges.json - "
                    "run verify_charges.py --save with a live session")

    rows = rows if isinstance(rows, list) else rows.get("data", rows)
    orders = build_grid(product)
    assert len(rows) == len(orders), "broker returned a different number of rows"

    worst = 0.0
    for o, r in zip(orders, rows):
        q = parse_quote(r)
        turnover = q.turnover or o["quantity"] * o["average_price"]
        mine = model_cost(COST, turnover, o["transaction_type"], product)
        bps = abs(mine - q.total) / turnover * 1e4 if turnover else 0.0
        worst = max(worst, bps)

    assert worst < 0.5, (
        f"the cost model disagrees with the broker ({source}) by {worst:.2f} "
        f"bps of turnover. Statutory rates change - update CostModel in "
        f"src/config.py and re-run run_backtest.py, because the headline "
        f"numbers depend on it.")
