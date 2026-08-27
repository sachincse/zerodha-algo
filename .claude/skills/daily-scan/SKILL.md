---
name: daily-scan
description: Run the Nifty 100 SMA crossover scan and propose Zerodha orders for review. Use after market close when the user asks for today's signals, the daily scan, crossover signals, or what to buy/sell today.
---

# Daily crossover scan

Semi-automated. You produce a signal list and a proposed order sheet. The user
decides. You never place an order that has not been approved in this
conversation, one order at a time.

## Steps

1. Run the scanner:

   ```
   python run_scanner.py --refresh --lookback 15
   ```

   It writes `out/scan.csv` and `out/scan.html`. Read the CSV.

2. Check the account through the Kite MCP:
   - `mcp__kite__get_profile` — if this errors, call `mcp__kite__login`, give
     the user the link, and wait. Do not proceed on a stale session.
   - `mcp__kite__get_holdings` — what is already owned
   - `mcp__kite__get_positions` — anything intraday still open
   - `mcp__kite__get_margins` — available cash

3. Build the order sheet with `src/orders.build_order_sheet`, passing real
   holdings and real available cash. Print it with `format_sheet`.

4. Sanity-check every BUY against a live quote (`mcp__kite__get_ltp`) before
   proposing it. If the LTP has moved more than 3% from the close the scan used,
   say so on that row — the signal was computed on a stale price.

5. Show the sheet. Stop. Ask which rows to place, if any.

6. Only on an explicit per-order yes, call `mcp__kite__place_order`. After each
   fill, read it back with `mcp__kite__get_order_history` and report the actual
   fill price against the estimate.

## Hard rules

- A BEARISH row is an EXIT for a held position. It is never a short. Retail
  equity delivery in India cannot be sold short overnight.
- Never place an order for a symbol not in the printed sheet.
- Never place a basket in one go because the user said "looks good" to the
  table. Confirm the specific rows.
- If `get_margins` shows less cash than the buy side of the sheet, say so and
  propose a reduced set rather than silently trimming quantities.

## Context the user should have every time

This strategy underperformed the Nifty 100 by roughly 4.7% CAGR over 2011-2026
once fills, Zerodha charges and a point-in-time universe were modelled honestly.
See `out/REPORT.md`. If the user is placing these trades, they are doing so
against the backtest, not because of it. Say this once per session, briefly, not
on every message.
