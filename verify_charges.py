"""Price the study's cost model against Zerodha's own contract note.

The headline finding here is that charges consumed 95% of gross trading gains.
That rested entirely on rates transcribed by hand into config.py. This asks the
broker to price the same orders and prints both, side by side.

    set KITE_API_KEY=...
    set KITE_ACCESS_TOKEN=...
    python verify_charges.py

Nothing is placed. /charges/orders is a pricing endpoint; the orders it prices
do not exist and never will. Exits non-zero if the model and the broker
disagree by more than the tolerance, so it can gate a release.

Without credentials it still runs, comparing the model against a frozen
snapshot of a previous broker response (out/broker_charges.json) if one is
present, and otherwise explaining what it needs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.broker_charges import (DEFAULT_GRID, build_grid, kite_from_env,
                                model_cost, parse_quote, quote_basket)
from src.config import DEFAULT

HERE = Path(__file__).resolve().parent
SNAPSHOT = HERE / "out" / "broker_charges.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--product", default="CNC", choices=["CNC", "MIS"])
    ap.add_argument("--tolerance-bps", type=float, default=0.5,
                    help="max acceptable gap, in basis points of turnover")
    ap.add_argument("--save", action="store_true",
                    help="write the broker's raw response to out/ as evidence")
    args = ap.parse_args()

    cost = DEFAULT.costs
    orders = build_grid(args.product)

    live = False
    try:
        kite = kite_from_env()
        raw = kite.get_virtual_contract_note(orders)
        live = True
    except Exception as e:                                  # noqa: BLE001
        if not SNAPSHOT.exists():
            print(f"  no live session and no snapshot at {SNAPSHOT}")
            print(f"  {e}")
            return 3
        print(f"  no live session ({type(e).__name__}) - using the saved "
              f"snapshot instead\n")
        raw = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

    rows = raw if isinstance(raw, list) else raw.get("data", raw)
    quotes = [parse_quote(r) for r in rows]

    if live and args.save:
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(rows, indent=1), encoding="utf-8")
        print(f"  saved the broker's response to {SNAPSHOT}\n")

    print(f"  product {args.product}   source "
          f"{'LIVE broker' if live else 'saved snapshot'}\n")
    print(f"  {'ORDER':<22}{'TURNOVER':>12}{'BROKER':>10}{'MODEL':>10}"
          f"{'DIFF':>9}{'DIFF':>8}")
    print(f"  {'':<22}{'':>12}{'Rs':>10}{'Rs':>10}{'Rs':>9}{'bps':>8}")
    print("  " + "-" * 69)

    worst = 0.0
    failures = []
    for o, q in zip(orders, quotes):
        side = o["transaction_type"]
        turnover = q.turnover or o["quantity"] * o["average_price"]
        mine = model_cost(cost, turnover, side, args.product)
        diff = mine - q.total
        bps = abs(diff) / turnover * 1e4 if turnover else 0.0
        worst = max(worst, bps)
        flag = " <--" if bps > args.tolerance_bps else ""
        if flag:
            failures.append((o["tradingsymbol"], side, turnover, q.total, mine, bps))
        label = f"{o['tradingsymbol']} {side} x{o['quantity']}"
        print(f"  {label:<22}{turnover:>12,.0f}{q.total:>10,.2f}{mine:>10,.2f}"
              f"{diff:>9,.2f}{bps:>8.3f}{flag}")

    print("  " + "-" * 69)
    print(f"  worst disagreement: {worst:.3f} bps of turnover "
          f"(tolerance {args.tolerance_bps})\n")

    if failures:
        print("  THE MODEL DISAGREES WITH THE BROKER:")
        for sym, side, t, broker, mine, bps in failures:
            print(f"    {sym} {side} on Rs {t:,.0f}: broker {broker:,.2f}, "
                  f"model {mine:,.2f}  ({bps:.2f} bps)")
        print("\n  Rates change. Update src/config.py CostModel to match, then "
              "re-run run_backtest.py - the headline numbers depend on it.")
        return 1

    print("  The cost model agrees with Zerodha's own contract note.")
    print("  The 95%-of-gross-gains finding is priced, not assumed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
