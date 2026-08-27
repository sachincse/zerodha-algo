# BACKTEST SPECIFICATION — NSE Nifty 100 Daily SMA(6/30) Crossover
**Binding spec v1.0 · authored 2026-08-22 · all rates point-in-time as of 2026-08-22**

This document is normative. Where it says MUST, code that does otherwise is defective, not "approximate". Where a figure is marked `[UNCERTAIN]`, the code MUST expose it as a named constant and include it in the sensitivity grid.

---

## 1. COST MODEL

### 1.1 Rate constants

```python
# ---- NSE cash segment, standard EQ scrips, Zerodha, as of 2026-08-22 ----
NSE_TXN_RATE        = 0.0000307   # 0.00307% = Rs 3.07/lakh. INCLUDES NSE IPFT. Both legs.
SEBI_FEE_RATE       = 0.0000010   # Rs 10/crore. Both legs. Same for delivery & intraday.
GST_RATE            = 0.18        # on (brokerage + SEBI fee + exchange txn charge) ONLY

STT_DELIVERY        = 0.0010      # 0.1% on BUY and 0.1% on SELL
STT_INTRADAY_SELL   = 0.00025     # 0.025%, SELL LEG ONLY. Zero on intraday buy.

STAMP_DELIVERY_BUY  = 0.00015     # 0.015% = Rs 1,500/crore. BUY SIDE ONLY.
STAMP_INTRADAY_BUY  = 0.00003     # 0.003% = Rs 300/crore.  BUY SIDE ONLY.

DP_CHARGE_PER_SELL  = 15.34       # Rs, flat, per ISIN per SELL DAY, delivery only.
                                  # GST IS ALREADY INCLUDED. Adding 18% on top is a
                                  # Rs 2.76 overstatement per sell. Do not do it.

BROK_DELIVERY       = 0.0         # zero brokerage on CNC
BROK_INTRADAY_RATE  = 0.0003      # 0.03%
BROK_INTRADAY_CAP   = 20.0        # Rs, per EXECUTED ORDER (cap binds above Rs 66,666.67)
```

**Rates flagged uncertain:**

| Constant | Status | Rule |
|---|---|---|
| `NSE_TXN_RATE` combined 0.00307% | **HIGH confidence** | Use as-is. |
| Internal base/IPFT split post 2026-03-01 | `[UNCERTAIN — medium]` | Irrelevant: model the combined rate only. Never model IPFT separately. |
| `NSE_TXN_RATE` for dates **before 2024-10-01** | `[UNCERTAIN]` | NSE was slab-priced before the SEBI true-to-label move. Research did not pin the schedule. Apply 0.00307% flat for all dates and record it as an understatement of ≤1 bp. |
| `STAMP_*` for dates **before 2020-07-01** | `[UNCERTAIN]` | Pre-2020 stamp duty was state-dependent with daily caps. Apply the current 0.015% / 0.003% flat for all dates — this **overstates** pre-2020 cost, which is the acceptable direction. |
| `DP_CHARGE_PER_SELL` historically | `[UNCERTAIN]` | Apply Rs 15.34 flat for all dates. Overstates historically. Acceptable direction. |
| `STT_DELIVERY` before 2012-07-01 | `[UNCERTAIN]` | Delivery STT was higher pre-2012. If the backtest window starts before 2012-07-01 the model MUST be re-verified before publication. Within a 2016→2026 window, 0.1%/0.1% is confirmed unchanged. |
| Female-first-holder DP discount (Rs 15.04) | `[UNCERTAIN — derived arithmetic, not published]` | Do **not** apply. Use Rs 15.34. |
| F&O STT rates | `[LIKELY WRONG in source — do not use]` | Out of scope. Strategy variant V2 (§2) MUST re-source F&O STT from a primary source before it is run. |

**Blanket rule for rate history:** apply the current 2026 rate to every date in the window. Never back-apply a lower historical rate you cannot cite. Cost overstatement is acceptable; understatement is a defect.

### 1.2 Fee function (implementable pseudocode)

```python
from math import floor

def fees(value_rs, side, product, is_first_sell_of_isin_today=True):
    """
    value_rs : float, executed notional = fill_price * shares (RAW prices, §3)
    side     : 'BUY' | 'SELL'
    product  : 'CNC' | 'MIS'
    Returns  : total statutory + broker charges in Rs. EXCLUDES slippage (§6).
    """
    v = value_rs

    # --- brokerage -------------------------------------------------------
    if product == 'CNC':
        brokerage = 0.0
    else:  # MIS: per EXECUTED ORDER, charged on both legs separately
        brokerage = min(BROK_INTRADAY_RATE * v, BROK_INTRADAY_CAP)

    # --- STT -------------------------------------------------------------
    if product == 'CNC':
        stt = STT_DELIVERY * v                       # both legs
    else:
        stt = STT_INTRADAY_SELL * v if side == 'SELL' else 0.0

    # --- exchange transaction charge (incl. IPFT) ------------------------
    exch = NSE_TXN_RATE * v                          # both legs, both products

    # --- SEBI turnover fee ----------------------------------------------
    sebi = SEBI_FEE_RATE * v                         # both legs, both products

    # --- stamp duty (BUY ONLY) -------------------------------------------
    if side == 'BUY':
        stamp = (STAMP_DELIVERY_BUY if product == 'CNC' else STAMP_INTRADAY_BUY) * v
    else:
        stamp = 0.0

    # --- GST: base excludes STT and stamp duty ---------------------------
    gst = GST_RATE * (brokerage + sebi + exch)

    # --- DP / demat debit -------------------------------------------------
    # Per ISIN, per SELL DAY, irrespective of quantity or number of orders.
    # CNC sells only. Never on intraday. Already GST-inclusive.
    if product == 'CNC' and side == 'SELL' and is_first_sell_of_isin_today:
        dp = DP_CHARGE_PER_SELL
    else:
        dp = 0.0

    return brokerage + stt + exch + sebi + stamp + gst + dp
```

### 1.3 Materialised rates (verify your implementation against these)

**(a) Equity delivery BUY** — `0.001187406 × value` = **11.874 bps**, no fixed component.
Breakdown: STT 10.000 + stamp 1.500 + exchange 0.307 + GST 0.057 + SEBI 0.010 bps.

**(b) Equity delivery SELL** — `0.001037406 × value + Rs 15.34`.
Breakdown: STT 10.000 + exchange 0.307 + GST 0.057 + SEBI 0.010 bps, plus flat Rs 15.34.

**Delivery round trip (same notional both legs):**

| Notional | Buy | Sell | Round trip | Round trip bps |
|---|---|---|---|---|
| Rs 50,000 | Rs 59.37 | Rs 67.21 | Rs 126.58 | **25.32 bps** |
| Rs 1,00,000 | Rs 118.74 | Rs 119.08 | Rs 237.82 | **23.78 bps** |
| Rs 2,00,000 | Rs 237.48 | Rs 222.82 | Rs 460.30 | **23.02 bps** |

The DP charge is 3.07 bps at Rs 50k and 0.77 bps at Rs 2L — it is the only size-dependent term and it MUST be modelled as a flat Rs, never as bps.

**(c) Equity intraday MIS**, Rs 1,00,000 per leg round trip:
brokerage 20+20 = 40, STT 25 (sell only), exchange 6.14, SEBI 0.20, stamp 3.00, GST 8.34, DP 0 → **Rs 82.68 = 8.27 bps round trip**.
Statutory-only component (STT + stamp) is 2.8 bps intraday vs 21.5 bps delivery — a ~19 bps swing that dwarfs any slippage assumption. This is not a reason to switch to intraday; see §2.

### 1.4 Mandatory cost outputs (every run)

1. `gross_return` and `net_return`, side by side. Reporting only one is a defect.
2. `annual_turnover = Σ |Δ weight|` and the implied annual cost in pp.
3. **Cost breakeven**: solve for the round-trip bps at which net Sharpe = 0. At base assumptions (§6) the realised round trip is **~73–75 bps** (25 bps/side slippage + ~23–25 bps statutory). **If breakeven is below 40 bps, the strategy does not exist — report it as dead.**
4. Turnover hurdle, stated explicitly: at ~73 bps/round trip, 50 round trips/year needs ~36.5 pp/yr of gross edge; 20 round trips/year needs ~14.6 pp/yr.

`commission=0` and any single flat-bps commission are forbidden in config.

---

## 2. WHAT IS TRADEABLE

### 2.1 The blunt statement

**The bearish leg cannot be shorted. A multi-day short in NSE cash equity does not exist for a retail account.** Not "expensive", not "hard to borrow" — **not placeable**.

- SEBI prohibits naked short selling: every seller must mandatorily honour delivery at settlement.
- Zerodha rejects CNC sells without demat holdings. MIS is the only equity-short product and it is force-closed the same session (Equity Cash CAS **15:12**, non-CAS **15:25**), so the maximum holding period of a cash short is **~6.25 hours**.
- Settlement is T+1, so the delivery obligation crystallises the next day. There is no window.
- Failing to deliver triggers a T+2 buy-in auction (14:00–14:45, ±20% band) with penalty = (auction price − sale price) × qty + 0.05%/day, and if no seller appears a close-out at max(highest price T→auction day, auction-day close + 20%). Uncapped and one-directional.

A daily SMA(6)/SMA(30) backtest that shorts cash equity and holds for days-to-weeks is **simulating a trade that cannot be placed. The short leg's P&L is fiction.** This is a correctness defect, not a slippage approximation. It is also where the fake alpha concentrates, so it will typically be a large share of reported P&L.

### 2.2 Honest strategy variants

**V1 — LONG / FLAT. This is the MANDATED DEFAULT.**
`position ∈ {0, +1}`. On a bearish crossover the position goes to zero, not negative. Hard assertion in code: `assert (position >= 0).all()`. Product = CNC. Costs from §1(a)/(b). No further approvals needed. **Run this unless the orchestrator explicitly approves another variant in writing.**

**V2 — LONG / SHORT via single-stock futures (NRML).** The only instrument reproducing near-linear short exposure over days-to-weeks. Approving V2 obligates the code to model **all** of:
- PIT restriction to F&O-eligible names (today 97 of Nifty 100; missing = **ENRIN, TATACAP, TMCV**; note **TMPV has futures while its demerger sibling TMCV does not** — a universe-construction trap).
- Lot-size quantisation. You cannot short 1 share. Median lot notional **Rs 6.97 lakh** (p25 5.89L, p75 8.20L, min 3.11L VEDL, max 11.91L BOSCHLTD). Equal-weighting is impossible at this granularity.
- SPAN + exposure margin. Exposure = max(3.5% of contract value, 1.5 SD of 6m log returns). All-in initial margin **15–25% of notional** `[UNCERTAIN — estimate; SPAN leg is exchange-computed]`.
- Monthly roll cost, roll slippage, and futures-vs-spot basis.
- Compulsory **physical settlement** on expiry — a short future held to expiry obliges delivery of the underlying.
- Expiry-day margin escalation to min(50% of contract value, 1.5× NRML).
- F&O ban periods (no fresh positions when market-wide limits breach).
- F&O STT re-sourced from primary (§1.1 flag).

**Capital verdict for V2:** one lot of each of the 97 names = **Rs 7.01 crore notional**, needing **Rs 1.05–1.75 crore of margin**. Ten concurrent shorts is ~Rs 70 lakh notional / ~Rs 14 lakh margin. A retail account cannot run a 100-name short book. V2 is only coherent as a **≤10-name concentrated short sleeve**, and must be labelled as such.

**V3 — Long stock + index hedge** (short Nifty futures or long Nifty puts). Capital-efficient and executable. But the short side is then market beta, not stock selection. If you run V3 you MUST stop describing it as a stock-selection strategy and MUST report the long-leg alpha separately from the hedge.

**V4 — Intraday MIS long/short.** Legal, cheap (8.3 bps round trip), and **incompatible with a daily SMA(6/30) signal**, which prescribes multi-day holds. Adopting V4 is redesigning the strategy, not fixing the backtest. Rejected.

**V5 — SLB borrow.** Rejected outright. Offline only (not on Kite → not algorithmically tradeable), borrower posts **100% of contract value plus VAR/ELM** (zero leverage — worse than buying the stock outright), min 500 shares, monthly expiry first Tuesday, 20% + 18% GST processing charge, ~48 working hours to activate.

---

## 3. DATA

### 3.1 Source of record

**Primary price source: NSE bhavcopy. Not Kite. Not yfinance.**

| Range | File | Notes |
|---|---|---|
| 1996-01-03 → 2024-07-05 | `nsearchives.nseindia.com/content/historical/EQUITIES/{YYYY}/{MMM}/cm{DD}{MMM}{YYYY}bhav.csv.zip` (MMM uppercase) | Legacy. Discontinued 2024-07-08 per NSE Circular 62424. Verified live. |
| 2024-01-01 → present | `.../content/cm/BhavCopy_NSE_CM_0_0_0_{YYYYMMDD}_F_0000.csv.zip` | UDiFF, 34 cols, ISO dates. 6-month overlap Jan–Jul 2024 for format reconciliation. |
| 2019-09-30 → present | `.../products/content/sec_bhavdata_full_{DDMMYYYY}.csv` (note **DDMMYYYY**) | Adds `DELIV_QTY`/`DELIV_PER`. Uncompressed. **No ISIN column.** |

Mandatory ingest guards:
- Send a browser `User-Agent` **and** `Referer: https://www.nseindia.com/`, or NSE returns an HTML block page — sometimes with HTTP 200 — which naive code writes to disk as a corrupt CSV. **Assert the header row after every download.**
- UDiFF→legacy column remap: `TckrSymb→SYMBOL`, `SctySrs→SERIES`, `ClsPric→CLOSE`, `OpnPric→OPEN`, `TtlTradgVol→TOTTRDQTY`, `TtlTrfVal→TOTTRDVAL`.
- `sec_bhavdata_full` prefixes **every header and every value with a leading space** (`' SERIES'`, `' EQ'`). `.strip()` or joins silently fail.
- Restrict to `SERIES in {EQ}` (optionally BE, flagged). A single day contains SM 362, GB 45, GS 55 etc. that will pollute the universe.
- **jugaad-data v0.35.4 is the only actively maintained NSE library** but has a reproduced severe bug: `bhavcopy_save()` for pre-2024 dates routes to the UDiFF endpoint, receives NSE's HTML error page, and **writes it to disk as `.csv` without raising**. If used at all, assert the first line is not `<!DOCTYPE html>`. `nsepy` is dead (last release 2020-03-07).
- jugaad-data `stock_df()` stamps dates `18:30:00` (IST midnight rendered as prior-day UTC), so 2026-08-20 appears as `2026-08-19 18:30:00`. Naive `.date()` shifts every bar back one day.

**Kite Connect** (Rs 500/month, historical included since 2025-02-08, `day` interval max 2000 days/request, 3 req/s, no daily cap) is permitted as a **cross-check only**, never as the price of record — see 3.2. Use the raw API with pykiteconnect for bulk pulls; the free hosted MCP (`https://mcp.kite.trade/mcp`) is unsuitable (12-hour sessions, no chunking, and it collapses every upstream error to the string `"Failed to get historical data"`, so an over-wide range is indistinguishable from an auth failure).

**yfinance is DISQUALIFIED as the price source.** It returns **zero rows** for delisted Indian tickers (MINDTREE.NS, CAIRN.NS, IBULHSGFIN.NS all 0 rows over 2015–2026, vs 2,876 for RELIANCE.NS). It is structurally survivorship-biased for India regardless of how good its adjustment logic is. It may be used only as a source of *corporate-action factors* for names that still exist.

### 3.2 Is it corporate-action adjusted?

**NSE bhavcopy: NO. Entirely unadjusted for splits, bonuses and dividends. No NSE archive file carries an adjustment factor.**

**Kite: claims yes, CONTESTED.** Zerodha states adjustment for bonuses, splits, rights issues, spin-offs and *extraordinary* dividends. Ordinary dividends are **not** adjusted. The claim is actively disputed on Zerodha's own forum (SBIN 2020-05-04: Kite close 178.85 vs TradingView adjusted 165.03, ~10% apart, identical volume 58,122,608, unresolved by staff as of Nov 2025). There is **no corporate-actions endpoint** in Kite — you cannot retrieve the split/bonus ratios to verify. Adjustments are applied retroactively at BOD before 09:15 on each ex-date, so **a cached history file silently diverges from the API over time.**

**Mandated architecture: two price series.**

```
px_raw          := bhavcopy OHLC, unadjusted, immutable, ISIN-keyed, vintage-stamped
px_adj(as_of=T) := px_raw × cumulative factor built ONLY from corporate actions with
                   announcement_date <= T AND ex_date <= T
```

`px_adj` is rebuilt as of each evaluation date. CA factors are self-built from an NSE/BSE corporate-action feed (Kite provides none).

### 3.3 Exact leakage implication for an SMA crossover

**Using back-ADJUSTED prices for the signal is NOT a leak.** Proof, and the code must carry it as a comment:

Let `A_t = P_t × F_t` where `F_t = Π f_T` over all ex-dates `T > t`. `F` is a positive step function, constant between ex-dates, and `F = 1` after the last one.

The signal is `sign(mean(A, 6) − mean(A, 30))`. If **no ex-date falls inside the trailing 30 bars**, `F` is a single constant across the entire window, so `mean(A,6) = F·mean(P,6)` and `mean(A,30) = F·mean(P,30)`, and since `F > 0` the sign is **identical** to the sign on raw prices. The future information in `F` cancels exactly, because the crossover is scale-invariant and homogeneous of degree 1 in price. If an ex-date `T ≤ t` *does* fall inside the window, `f_T` was known at `T`, so the within-window relative shape is exactly what a real-time trader computed; only the global factor from ex-dates strictly after `t` multiplies the window uniformly and again cancels.

**Using UNADJUSTED prices is strictly worse, not safer.** On the ex-date of a 1:1 bonus or 2:1 split the raw close halves overnight while the printed previous close does not reflect the ratio. The panel records a **genuine −50% one-day return**, SMA6 crosses below SMA30 within **1–2 bars**, producing a **guaranteed false SELL signal on every bonus and split in the panel** plus a fabricated −50% loss in the equity curve. Across a Nifty 100 panel this fires dozens of times per decade.

**Where adjustment DOES leak** (the global future factor cancels only for scale-invariant functions):
- (a) Rupee-denominated filters (`price > 100`, `ATR > 5`) screen a different set of stocks than were screenable at the time.
- (b) Share sizing `shares = notional / A_t` gives the wrong share count → wrong per-share DP charge, wrong tick rounding, wrong turnover.
- (c) Rupee stop-losses and circuit-band checks.
- (d) **Mixing adjusted and raw fields** — adjusted `Close` against raw `Open` prints a fictitious 100% intraday move across a 1:2 split.
- (e) **Demergers and rights issues** — factors are not simple known-in-advance ratios; they are computed after the fact and revised. Unlike a split, a demerger factor **changes the return series, not merely its scale.** The pre-announcement series genuinely encodes post-announcement information here.
- (f) Conditioning the universe on "stocks with a clean adjusted series" is conditioning on future corporate actions.

**Dividend back-adjustment** cancels in the signal by the same argument, but **leaks in P&L**: `pct_change` on a dividend-adjusted series credits the dividend on the ex-date, tax-free, instantly reinvested at the ex-date close. None of that is true for an Indian resident (taxable at slab since FY2020-21, TDS above threshold, cash at pay date weeks later). Note yfinance flipped `auto_adjust` from False to True by default and dropped the separate `Adj Close` column.

**Binding rule:** *signals on `px_adj`; every rupee-denominated rule, all sizing, all fills, all fees, all band checks on `px_raw` of that date.*

**Required assertion (CI gate):** for every bar with no ex-date in the trailing 30 sessions, `sign(sma6 − sma30)` computed on `px_raw` MUST equal the sign computed on `px_adj`. Any mismatch means the adjustment is not a clean multiplicative factor — a rights issue, a demerger, or a vendor error — and localises exactly where to look.

### 3.4 Identity and vintage

- Join key is **ISIN**, never symbol. Present in legacy bhavcopy, UDiFF, `ind_nifty100list.csv`, `EQUITY_L.csv`. Absent from `sec_bhavdata_full` (symbol only — bridge via a symbol→ISIN table built from the same day's UDiFF).
- Renames: `nsearchives.nseindia.com/content/equities/symbolchange.csv`, 1,056 records, no header, `Company,OLD,NEW,DD-MMM-YYYY`. **Covers renames only, not mergers** — MINDTREE is absent from the file despite the Mindtree→LTIMindtree merger; only the `LTI→LTIM` rename is captured.
- Kite `instrument_token` for equities changes on corporate actions and segment moves (EQ→T2T). Key on `exchange + tradingsymbol`, or better ISIN.
- **F&O `instrument_token`s are RECYCLED the day after expiry.** A cached derivatives token silently resolves to a different contract with no error. If V2 is approved, re-download the full instrument master **every trading day**. For expired futures history use `continuous=1` (NFO/MCX futures, day candles only).
- Maintain an **append-only, ISIN-keyed vintage store** queried with an explicit `as_of`. Store a row-level hash of `(open, high, low, close, volume, series)` keyed by `(isin, date, knowledge_date)` and emit a diff report on every re-pull. History is rewritten under you: bhavcopy is reissued after trade annulments, and adjusted series are recomputed on every new corporate action.

---

## 4. UNIVERSE

### 4.1 Point-in-time construction rule

Build a membership table with schema `(isin, symbol, effective_from, effective_to)` and select at each `t`:

```python
tradeable[t] = members[(members.effective_from <= t) & (t < members.effective_to)]
```

**Membership sources (in this order):**

1. **2003-03-19 → 2020-07-31** — NSE official `https://archives.nseindia.com/content/indices/IndexInclExcl.xls`, "Nifty 100" sheet, 336 event rows. Two parsing traps: the `Event Date` column mixes Python datetimes with `DD-MM-YYYY` strings *within the same sheet*, and constituents are given as **company names** (`Steel Authority of India Ltd.`), not tickers, some with stray non-ASCII bytes. This file is **abandoned** — OLE metadata last-saved 2020-09-22, last event 2020-07-31.
2. **2020-07-31 → present** — `https://niftyhistory.in/api/archive`, undocumented unauthenticated JSON, 328 events / 76 Nifty 100 events, schema `{effective_date, index_type, inclusions, exclusions}`. **Validated exact against NSE's official file over the 2015-01-01→2020-07-31 overlap: 18/18 event dates and 18/18 per-date inclusion counts match, zero discrepancies.** Unaffiliated third party; **mirror it locally now** — it could disappear without notice.
3. **Ticker reconciliation** — the niftyhistory ticker mapping is dirty (see 4.2). Use it for **event dates only**; resolve names to ISINs against the bhavcopy of the effective date.

**Hard rules:**

- **Activate membership on NSE's published EFFECTIVE date** (end of March / end of September), **never** on the cut-off date (31 Jan / 31 Jul) and **never** on the announcement date (≥4 weeks earlier). Switching on the cut-off or announcement date captures the mechanical, non-repeatable run-up in additions caused by index-fund front-running, and symmetrically avoids holding deletions into their fall.
- **Model off-cycle events.** Reviews are not the only source of change. Official NSE data shows ad-hoc Nifty 100 events at **2015-10-19, 2016-11-15, 2017-05-26, 2020-03-19, 2020-06-26** for M&A, delisting and suspension. A universe rebuilt only on the semi-annual calendar is wrong on those dates.
- **2016-04-01 saw 14 simultaneous inclusions** (a methodology change) versus the typical 1–5. Any backtest crossing April 2016 with a static universe is badly misspecified.
- Typical scheduled turnover is a stable **+3/−3 per review** across the last 12 reviews (one exception: 2022-03-31 was +3/−2).
- Never build the universe from `ind_nifty100list.csv` or `EQUITY_L.csv`. `ind_nifty100list.csv` is **overwritten in place at every rebalance** — there is no dated copy, and it is the direct cause of survivorship bias. Wayback is not a substitute: only **8 captures total** exist, none between 2019 and 2023, one of them a 301.
- Proof the price layer is bias-free: CAIRN, IBULHSGFIN and MINDTREE all appear in the 2015-01-07 bhavcopy and are **absent** from today's `EQUITY_L.csv`. Union bhavcopy across time; never derive the tradable set from a current master list.
- **Delisted and merged names stay in the panel** with an explicit terminal value (§8 Q7). Never let a series vanish silently.
- **The top-100-by-turnover proxy is REJECTED for a Nifty 100 study.** Measured overlap against the real index: **38/100** on single-day turnover; **56/100** using 26 weekly snapshots over 6 months; 68/100 at N=120, 76/100 at N=150, **91/100 only at N=200**. Root cause: Nifty 100 selects on **full market capitalisation**, and no bhavcopy variant contains a shares-outstanding field, so market cap cannot be computed from NSE free data at all. The false positives are high-velocity small/mid caps (CUPID, BAJAJHIND, IDEA, HINDCOPPER, GRSE, BHEL, DATAPATTNS); the names missed are precisely the stable mega-caps a large-cap strategy needs (ASIANPAINT, BRITANNIA, NESTLEIND, DRREDDY, CIPLA, DMART, GRASIM). ETFs also contaminate the proxy — GOLDBEES lands inside the top 100 by traded value; filter with `eq_etfseclist.csv`, which is **not UTF-8** (read as latin-1/cp1252).

### 4.2 Residual survivorship bias — honest statement with magnitude

**Uncorrected bias (what you get from today's list):** over 2016-08-22 → 2026-08-22 there were **24 Nifty 100 change events, 71 inclusions and 71 exclusions, 61 distinct symbols added and 64 distinct symbols removed.** The union of all names that were index members at some point over the decade is **≥168**, against the 100 you would see today. Using today's list silently discards ~64 companies that were members and then fell out — the losers (RCOM, IBULHSGFIN, IDEA, BHEL, SAIL, CAIRN, GLENMARK, NMDC, SUNTV) — while back-injecting winners that were not yet members. Roughly **18 of today's 100 were not in the index in Aug 2016**. Effective one-way universe error is **~60–70% per decade.**

**Residual bias after applying the §4.1 rule — three named, unfixed sources:**

1. **Ticker-mapping drift in the post-2020 feed.** Replaying niftyhistory events backwards from today's 100 yields **116 members at 2016-08-22 instead of 100 — a 16-name drift**, and the exclusion sets contain non-equity garbage (`722HPCL29`, `NIFTY`, `BI`, `SALSTEEL`, `ACCELYA`, `DHANBANK`). The **event dates are trustworthy** (18/18 validated); the **symbol resolution is not**. Residual membership error is therefore **on the order of 10–16 names (10–16% of the universe) at the 10-year horizon**, shrinking toward zero at the recent end.
2. **Terminal-value gap.** bhavcopy gives no delisting reason, no final settlement value, no recovery to shareholders. Any default that exits at last-traded price **overstates returns on bankruptcies**.
3. **Merger linkage gap.** `symbolchange.csv` covers renames only; merged entities terminate abruptly with no successor pointer.

**Magnitude estimate of the return impact.** `[ESTIMATE — not directly sourced; derived from the turnover figures above and the mechanism]`
- Uncorrected (today's list back-run 10 years): plausibly **+2 to +4 pp/yr** of spurious CAGR on a long-only trend strategy, concentrated in the fact that the removed names are exactly where a trend rule takes its worst losses. It also inflates the apparent hit rate and **understates** how often price bands bind and how large gaps are.
- After applying §4.1 with the dirty-ticker caveat: residual **+0.3 to +0.8 pp/yr**, dominated by source (1) at the early end of the window and source (2) for bankruptcies.

**These are estimates and MUST be quoted as such** in any results write-up, alongside the two hard, sourced diagnostics below.

**Detection gates (CI, all three MUST pass):**
- `n_distinct_symbols_in_panel` over 10y MUST be **≥ 168**, over 15y MUST be **~180–220**. If it is 100, the panel is fake.
- `count(symbols where last_valid_index() < sample_end)` MUST be **> 0**. A clean panel with zero terminations is proof of survivorship bias.
- `count(symbols where first_valid_index() > sample_start)` MUST be **> 0**.

---

## 5. NO-LEAK RULES

Each rule is phrased so it can be verified by reading the code or by an assertion. All are MUST.

**Signal timing**

1. Every value of `sma6[t]` and `sma30[t]` uses only rows with index `≤ t`. All rolling calls are `rolling(window=N, center=False, min_periods=N)`.
2. No two-sided or acausal smoothers anywhere in a feature path. `grep -nE 'center\s*=\s*True|filtfilt|savgol_filter|seasonal_decompose|hpfilter|STL\('` MUST return zero hits. There is no legitimate use of a centered window in a feature that drives a trade.
3. **Two distinct lags.** With open-to-open marking: `o2o[k] = open_raw[k] / open_raw[k-1] - 1` and `strategy_ret[k] = signal.shift(2)[k] * o2o[k]`. Lag one is signal→decision (close of `t` → order for `t+1`); lag two is decision→fill (order → `open[t+1]`). The same timestamp MUST NEVER appear in both the feature window and the execution price.
4. Costs from §1 are booked into bar `k` when `held[k] != held[k-1]`, i.e. charged against the fill at `open_raw[k-1]` which is bar `k`'s entry price.
5. `grep -nE 'shift\(-'` returns hits only on a column explicitly named as a label/target that is never an input to a position or a fill price.
6. `grep -nE 'bfill|backfill|\.interpolate\('` returns zero hits. Forward-fill only, within a symbol. Prefer leaving NaN and masking the bar untradeable over filling it.
7. `.expanding()` is always followed by `.shift(1)` and has `min_periods >= 250`. No `.expanding().apply(f)` where `f` closes over the enclosing DataFrame rather than only its window argument.
8. Every `resample()` call site passes `label=` and `closed=` explicitly, and the result is `.shift(1)`-ed before any join back to daily. The trailing partial bucket is dropped. (pandas defaults `closed`/`label` to `'left'` except for `ME, YE, QE, BME, BA, BQE, W` which default `'right'`; `origin` only affects tick frequencies.)
9. Every `merge_asof` uses `direction='backward', allow_exact_matches=False` and joins on a `knowledge_ts` column (publication time + conservative lag), never on `event_ts`.
10. `grep -nE '\[::-1\]|\.iloc\[::-1\]'` returns zero hits.

**Panel hygiene**

11. Every `.shift/.rolling/.diff/.pct_change/.ewm` on a long `(date, symbol)` frame is inside `.groupby(level='symbol')`, or the panel is WIDE (dates × symbols) where the operation is column-wise. Unit test: a two-symbol toy frame where symbol B's first return MUST be NaN.
12. Every `pct_change` passes `fill_method=None`.
13. `grep -n 'dropna('` on the panel returns zero hits. NaN is carried through to the position matrix as "not tradeable on this date". `panel.shape` is logged before and after any NA handling.
14. The index is on the **NSE trading calendar** derived from bhavcopy file presence. `grep -nE "date_range\(.*freq=.D."` returns zero hits. Muhurat sessions are flagged and their treatment (include/exclude) is declared explicitly.
15. `assert df.index.tz is not None` and `assert df.index.max() <= today` at load. Any jugaad-data `18:30:00` stamps are normalised to the IST date before use.

**Universe**

16. The membership table has `effective_from`/`effective_to` columns and `effective_from` equals NSE's published **effective** date, never the 31-Jan/31-Jul cut-off and never the announcement date.
17. `tradeable[t, s] = pit_member[t, s] & has_raw_bar[t, s] & series_is_EQ[t, s] & not_surveillance_restricted[t, s]`.
18. The three §4.2 detection gates pass.

**Sizing and no full-sample statistics**

19. `grep -nE '\.std\(|\.mean\(|\.quantile\(|zscore\(|fit_transform\(|qcut\(|MinMaxScaler|rank\(pct=True\)'` — every hit is either inside a `rolling`/`expanding` context followed by `.shift(1)`, or is a reporting-only statistic computed after the backtest and never fed back into a position. Volatility for sizing is `returns.rolling(60, min_periods=40).std().shift(1)` or `ewm(halflife=20).std().shift(1)`.
20. `N` in any `1/N` or `capital / N` weighting is the count of PIT members with a live signal **as of the decision timestamp** and varies over time. `assert weights_denominator.nunique() > 1`.
21. `shares = floor(notional / close_raw[t])`, fill booked at `open_raw[t+1]`, residual cash carried explicitly. Sizing MUST NOT divide by an adjusted price and MUST NOT divide by the fill price itself.
22. Cash ledger carries a settlement date per leg (T+1). `assert settled_cash_balance >= 0` on every date; log the count of dates it would have gone negative if unmodelled.

**Instrument correctness**

23. `assert (position >= 0).all()` for any cash-equity run (V1). If V2 is approved, this is replaced by an assertion that every negative position is a futures instrument on a PIT F&O-eligible ISIN with an integer lot count.
24. Signals read `px_adj(as_of=t)`; fills, fees, rupee thresholds, tick rounding and band checks read `px_raw`. `grep` for any join of an adjusted column with a raw `Open`/`High`/`Low` returns zero hits.
25. The §3.3 raw-vs-adjusted sign assertion passes on every bar with no ex-date in the trailing 30.
26. No fill ever uses the `close` column as an executable price. (Before 2026-08-03 the NSE close was the VWAP of the last 30 minutes — a computed statistic no order can fill at. From 2026-08-03 the Closing Auction Session makes a close fill legitimate **only** for Category I / F&O names and only inside the ±3% band.)
27. Dividends are counted exactly once. Signals on adjusted, P&L on raw with an explicit dated net-of-tax credit only on days the position was on. Assertion: `strategy_total_return − Σ raw_price_returns ≈ Σ dividends_credited_while_long`. If yfinance appears anywhere, `auto_adjust` is passed explicitly.

**Selection and reporting**

28. No code path that chooses `(6, 30)` or any other parameter ever sees the full date range. Walk-forward is **3y train / 6m test / 6m step**, with a **purge ≥ 60 trading days** (30 for SMA30 + median holding period) between train end and test start, and an embargo after each test window. `assert train.index.max() + purge <= test.index.min()`. `grep` for any `.fit`/`argmax` whose input slice end exceeds the fold's train end.
29. Only the concatenated out-of-sample curve is reported. Re-selecting parameters by looking at the concatenated OOS curve, or refitting on all data after walk-forward "validated the method", are both forbidden. Report the parameter path across folds — wild jumps mean the surface is noise.
30. The full grid's Sharpe surface is reported, not the argmax. A Deflated Sharpe Ratio using the honest trial count (including every abandoned universe definition, cost assumption and rerun) accompanies any headline Sharpe. Reference: a 2..200 fast/slow grid is ~20,000 ordered pairs; `E[max Sharpe] ≈ sqrt(2 ln N) ≈ 4.4` SE, and with 15y daily data `SE(Sharpe) ≈ 0.26`, so **a zero-edge system yields a best-in-sample Sharpe near 1.1 by construction.**
31. The headline result is a **single pooled portfolio statistic** over all PIT members, not a filtered list of names where the rule worked. Any per-name table applies Benjamini-Hochberg FDR across the 100 t-stats and clusters standard errors by date.
32. Cash drag is symmetric: credit the 91-day T-bill on uninvested cash **and** subtract the same series in the Sharpe numerator. Identical treatment for the benchmark.
33. Every RNG is seeded and the seed is logged. `grep -n 'np\.random\.'` shows no unseeded call.

**Verification harness (all three are CI gates, cheaper than a line-by-line audit)**

34. **TEST 1 — point-in-time determinism.** Truncate all inputs at date `T`, run the entire pipeline, persist the signal and position matrices. Extend inputs to `T+90`, rerun. **Assert every value for dates ≤ T is bit-identical.** This single test catches retroactive adjustment, restated prices, survivorship, static universes, full-sample scaling and expanding-window misuse simultaneously. A pipeline that cannot pass this is not shippable.
35. **TEST 2 — permutation null.** Replace the real return panel with a stationary block bootstrap (block length ~20 days, to preserve volatility clustering) that destroys the sign structure. Rerun the **complete selection pipeline including the grid search**, ≥200 replications. If the live Sharpe sits inside that distribution, the finding is null and MUST be reported as null.
36. **TEST 3 — cost breakeven and exposure decomposition.** Report the round-trip bps that drives net Sharpe to zero (§1.4), and decompose the return into `(average net exposure × Nifty 100 TRI return) + timing residual`. For most SMA crossovers on a large-cap Indian universe the first term is essentially all of it, and this MUST be stated in the results either way.

---

## 6. EXECUTION ASSUMPTIONS

### 6.1 Fill price

**Fill at `open_raw[t+1]` — the official NSE bhavcopy `OpnPric` (UDiFF `BhavCopy_NSE_CM_*.csv.zip`).** Not a third-party candle: Yahoo's daily open and its own 09:15 one-minute bar open disagree by a **median 13.0 bps** (p75 24.8, p90 42.6), and the Yahoo daily open falls inside the first-minute high–low range only **47.6%** of the time.

The NSE daily open **is** the pre-open call-auction equilibrium price, not a continuous-session print. Verified: the pre-open IEP equalled bhavcopy `OpnPric` for **100/100** Nifty 100 constituents on 2026-08-21.

### 6.2 Slippage

Slippage is applied to the fill price and is **always adverse**:

```python
BASE_SLIPPAGE_BPS = 25.0    # mandated base case

buy_fill  = open_raw * (1 + slip_bps / 10_000)
sell_fill = open_raw * (1 - slip_bps / 10_000)
```

**Mandatory sensitivity grid: 5 / 25 / 50 bps per side. Headline reported at 25. If the strategy fails at 50, report it as fragile; if it fails at 25, report it as dead.**

Justification for 25 (sources conflicted; the conservative branch is taken):
- **3–5 bps per side is achievable ONLY if the order is provably inside the pre-open auction** (entered 09:00–09:07; the exchange closes collection at a system-driven random point between the 7th and 8th minute — empirically 09:07:39–09:07:53 across all 100 names on 2026-08-21). NSE Clearing's published Rs-1-lakh impact cost for Nifty 100 is median **2 bps**, p90 3 bps, max 8 bps (JAN-2026 vintage), stable year over year (JAN-2025: median 3 bps). But that number is computed from four snapshots inside the **continuous** session over six months. **Liquidity at 09:15 is structurally different and thinner — treat 2–3 bps as a FLOOR, not a point estimate.**
- **25–30 bps per side is the honest number for a market order in the first minute of continuous trading.** Measured over 5 sessions, n=500 symbol-days: `|09:15-minute close − open|` median **21.2 bps** (mean 27.7, p90 60.3); `|09:16 open − 09:15 open|` median 21.4 bps; `|09:20 close − open|` median 31.5 bps; `|09:30 close − open|` median 37.3 bps (p90 100.8). The 09:15 bar's own high-low range is a median **34.1 bps** of the open (p90 71.4, p99 109). **Timing risk dominates impact cost by an order of magnitude.**
- Broker routing is unverified end-to-end (§8 Q13). Assume the order does **not** reach the auction until a live test order proves otherwise.
- Quoted bid-ask is not binding: one tick is 0.15–1.23 bps across the Nifty 100 interquartile range (tick Rs 0.05 above Rs 250, Rs 0.01 below; only 13 of 100 names trade below Rs 250).
- These measurements are **5 quiet sessions only** and are indicative of central tendency, not the tail. Model a fat right tail: constituent impact cost spikes on event days (Nifty Next 50 pooled monthly max 0.73% in 2024, 2.10% in 2021).

### 6.3 Size and market impact

- **Participation cap: `order_value ≤ 1% of the 20-day median traded value`.** Compute distribution of `order_value / rolling_20d_median_traded_value` for every simulated trade and plot it — above 1–2% needs an impact model, **above 10% is fiction**.
- For a delivery hold, use **`DELIV_QTY × price`** from `sec_bhavdata_full` (2019-09-30+), not total traded quantity — a large share of printed volume is intraday churn a delivery position cannot access.
- Above the cap, add a square-root impact term `c × daily_sigma × sqrt(Q / ADV)` with `c = 1.0`, spread the residual over subsequent days, and book the delay cost. Or reject the order and log it.
- Pre-open depth reality check (single session, 2026-08-21): the Nifty 100 crossed **Rs 142 cr in the pre-open vs Rs 28,358 cr full-day = 0.50%**; median per-stock auction cross **Rs 48.8 lakh** (p10 Rs 11.3 lakh, min Rs 1.59 lakh). A **Rs 50,000** order is a median 1.03% of the cross (p90 4.4%) — safely fillable. A **Rs 2,00,000** order is a median 4.11% and exceeds 10% of the cross in **23 of 100 names** — not safely fillable in the thinner quartile.

### 6.4 When a fill is impossible

A fill is **impossible** on `(isin, date)` if any of the following holds:

| # | Condition | Data source |
|---|---|---|
| F1 | No raw bar exists for that `(isin, date)` | bhavcopy presence |
| F2 | `open == high == low == close` and `volume == 0` | bhavcopy |
| F3 | **BUY** and `open_raw >= prev_close * (1 + band) - tick` | band table + bhavcopy |
| F4 | **SELL** and `open_raw <= prev_close * (1 - band) + tick` | band table + bhavcopy |
| F5 | `SERIES != 'EQ'` (BE / BZ / T2T / SM / ST / GS / GB / IV / RR / SZ) | bhavcopy `SERIES` |
| F6 | Symbol is on the ASM/GSM/ESM list for that date | daily surveillance list |
| F7 | (V2 only) symbol is in an F&O ban period | daily ban list |

**Band values.** The exemption is **derivative availability, not index membership.** Names with their own derivatives carry **no fixed band** but a **10% dynamic operating range** from the previous close, flexed in 5% steps (trigger: LTP ≥ 9.90% of base **and** ≥25 trades with 5 distinct UCCs on each side at or above 9.90%). Non-derivative names carry a fixed 2 / 5 / 10 / 20% band. Today, **97 of 100 Nifty 100 names are F&O-eligible; ENRIN, TATACAP and TMCV are not** and carry a fixed band. ASM Stage I cuts the band to 5% or lower and imposes 100% margin; **ASM Stage II moves the scrip to Trade-for-Trade, which removes intraday netting entirely** — no intraday exit and no intraday short exists at all for that name.

**Frequency (why this is not a rounding error).** Over 327,553 clean stock-days (current Nifty 100 members, 2011-08-22 → 2026-08-21, split and >15%-gap artefacts removed): `|c2c| ≥ 9.9%` on **0.32%** of stock-days (1 in 310); price touched ±9.9% intraday on **0.82%**; `|c2c| ≥ 4.9%` on **3.39%** (1 in 30). **The case that matters for an open-executed strategy: the OPEN itself printed within 0.2% of the ±10% band on 125 occasions = 0.038% of stock-days (1 in 2,620).** On those days open-to-close averaged +0.63% but ranged **−33.4% to +32.9%**, with 54% continuing in the gap direction (PNB 2017-10-25 opened +10.0% and closed +46.2%; ADANIENT 2023-02-02 opened +10.0% and closed −26.7%). Band-hitting is heavily clustered in crisis years (306 events in 2020, 19 in 2025).

**Required behaviour — asymmetric and conservative:**

- **Entry blocked → DROP the signal.** Do not carry it forward, do not fill at the band, do not silently fill at the next available price. A momentum crossover fires disproportionately on exactly these days, so the un-fillable bars are the *profitable* ones — dropping them is a large, one-directional haircut, and that is the point.
- **Exit blocked → HOLD the position** and retry at the next bar's open with the same slippage, booking the intervening return. You eat the loss you could not escape.
- **F5/F6 (series or surveillance restriction) → no new entries; existing positions become delivery-only** and use the tightened band from the surveillance table, not the default band.

**Required reporting:** `n_impossible_fills`, `pct_signals_dropped`, and `position_days_spent_in_a_restricted_state`, in every run. Then rerun crediting the blocked fills at the band price and report the delta — **that delta is the size of the optimism you are choosing not to book.**

### 6.5 Settlement and DP

- T+1 default. Sale proceeds are available the next session. A same-day sell-A-buy-B rotation is implicitly using unsettled cash: either finance the gap explicitly or delay the offsetting buy by one session and book the timing cost. Optional T+0 exists for the top 500 by market cap (phased from 2025-01-31) but is thin — do not model it.
- The Rs 15.34 DP charge triggers **once per `(isin, sell_date)`**, irrespective of quantity or number of orders.

### 6.6 Two live regime breaks that straddle this window

- **2026-08-03 — the CLOSE changed meaning.** Closing Auction Session now runs 15:15–15:35 for cash-segment stocks with derivatives (reference = 15:00–15:15 VWAP, ±3% band; continuous trading ends 15:15). A signal computed on `close` is computed on a **different object** before and after this date. Split the out-of-sample period at 2026-08-03.
- **2026-09-07 — the OPEN changes.** New pre-open phase structure (09:00–09:05 limit + market entry; 09:05–09:10 limit only with random close between 09:08 and 09:10; 09:10–09:12 matching; 09:12–09:15 transition), and **market orders get matching priority** instead of being matched last. Post-Sep-2026 fills will be *better* than the 25 bps assumption, so keeping it is conservative — but it MUST be labelled as calibrated on the pre-Sep-2026 microstructure.

---

## 7. BENCHMARK

**The benchmark is the NIFTY 100 TOTAL RETURN INDEX (TRI), minus 25 bps/yr. Not the price index.**

**Source (free, programmatic):**
```
POST https://niftyindices.com/BackPage/getTotalReturnIndexString
Referer: https://niftyindices.com/reports/historical-data
body: {"cinfo":"{'name':'NIFTY 100','startDate':'01-Jan-2003','endDate':'21-Aug-2026','indexName':'NIFTY 100'}"}
```
Returns 5,875 daily TRI values from 2003-01-01 at base 1000. The older `/Backpage.aspx/` endpoint is dead. PRI OHLC comes from `POST /BackPage/getHistoricaldatatabletoString` with the same payload.

**Do NOT use `nsearchives.nseindia.com/content/indices/ind_close_all_DDMMYYYY.csv` as a benchmark source — it is verified PRI-only, zero TRI rows.** It is useful for other index PR closes and valuation ratios, nothing more.

**Why TRI and not PRI.** SEBI mandated TRI benchmarking (circular SEBI/HO/IMD/DF3/CIR/P/2018/04, effective 2018-02-01) precisely because PRI excludes dividends and "does not represent an accurate picture". The **TRI−PRI drag is 1.23 pp/yr over 5 years, 1.32 pp over 10, 1.38 pp over 15 and 20** — which is typically the entire reported "alpha" of a long/flat large-cap timing rule. Current index dividend yield 1.22%.

**Hurdle definition.** Nifty 100 TRI is investable via index funds/ETFs at roughly 20–30 bps TER, so:
```
hurdle_cagr = nifty100_tri_cagr - 0.25pp
```
compared against the strategy net of the §1 cost model and §6 slippage.

**Reference levels (TRI 35,227.35 at 2026-08-21):** 5y **10.05%**, 10y **12.39%**, 15y **13.15%**, 20y 12.20%, 3y 10.78%, 1y −0.17%. At the cleaner month-end 2026-07-31 (TRI 35,316.52): 5y **10.93%**, 10y **12.49%**, 15y 12.28%, 20y 12.64% — the 5y 10.93% and PRI 9.70% match the official NSE factsheet exactly, validating the pull.

**Never quote a single point-to-point CAGR as the hurdle.** Endpoint sensitivity is large: the 15-year TRI CAGR is 13.15% from 2011-08-19 but **12.28% from 2011-07-29** — a 0.87 pp swing from moving the start date three weeks. Report the **rolling-window distribution**: rolling 5y CAGR across 4,636 daily start dates 2003–2021 — median 13.9%, mean 14.4%, p25 10.5%, p10 6.8%, min −0.7%, max 47.0%, with 14% of windows under 8% and none negative. Rolling 10y (n=3,398) — median 13.5%, p25 11.4%, min 5.5%, max 23.1%.

**Three comparisons are mandatory, not optional:**
1. **Nifty 100 TRI − 25 bps/yr.**
2. **Buy-and-hold of the same point-in-time universe, run through the identical §1 cost model.** This isolates the timing rule from the universe.
3. **Random-timing null at the same average net exposure** — bootstrap entry dates preserving the observed holding-period distribution, ≥1,000 draws. A long/flat crossover on large caps is mostly a beta overlay at ~60–70% time-in-market; **this, not zero and not buy-and-hold, is the correct null for a timing rule.**

**Also report:** regression of strategy excess returns on TRI excess returns, with alpha carrying a **Newey-West** t-stat and the realised beta stated; and calendar-year TRI returns for drawdown/regime sanity checking (2011 −24.93, 2012 +32.51, 2013 +7.89, 2014 +34.88, 2015 −1.26, 2016 +5.01, 2017 +32.88, 2018 +2.57, 2019 +11.83, 2020 +16.08, 2021 +26.45, 2022 +4.94, 2023 +21.24, 2024 +12.95, 2025 +10.24, 2026 YTD to 21-Aug −4.01).

**Declared asymmetry:** Nifty 100 has **no Net Total Return variant** (NSE computes NTR only for Nifty 50, Nifty Midcap 50 and Nifty 500). Nifty 100 TRI reinvests dividends **gross of withholding tax** and excludes special dividends ≥2% of market price. If strategy P&L is modelled after tax, the comparison is not apples-to-apples and MUST say so.

---

## 8. OPEN QUESTIONS

Each carries the conservative default the code MUST adopt until the question is closed.

**Q1 — Historical fee schedule.** Exchange transaction charges were slab-based before 2024-10-01; stamp duty was state-dependent with daily caps before 2020-07-01; DP charges were lower historically; delivery STT was higher before 2012-07-01. None of these histories were pinned.
→ **Default:** apply the current 2026 rate to every date. This overstates pre-2020 stamp duty and pre-2024 DP (acceptable) and understates the pre-2024 exchange charge by <1 bp (immaterial against 20 bps of STT). **If the window starts before 2012-07-01, delivery STT MUST be re-verified before publication.**

**Q2 — NSE IPFT / base transaction-charge split after 2026-03-01.** The NSE circular PDF (`FA73061.pdf`) timed out; the split comes from a secondary regulatory tracker.
→ **Default:** model only the combined 0.00307%, which is high-confidence (it is what Zerodha bills today). Never model IPFT as a separate line.

**Q3 — Is Kite historical data actually corporate-action adjusted?** Zerodha says yes; its own forum contests it (SBIN ~10% divergence, unresolved). No CA endpoint exists to verify.
→ **Default:** do **not** use any vendor's pre-adjusted series as the signal input. Build factors from an NSE/BSE CA feed and validate against at least one known split, one bonus, one ordinary dividend, one rights issue and one demerger per year of the window before the run is considered valid.

**Q4 — Unmodelled corporate actions.**
→ **Default:** any `|1-day raw return| > 30%` that cannot be matched to a modelled CA is treated as a suspected unmodelled action. Mask that symbol for the date and the following 30 bars; it produces no signal and no fill. Report the count. **A material count invalidates the run.**

**Q5 — Point-in-time membership after 2020-07-31** depends on `niftyhistory.in`, an unaffiliated undocumented endpoint with demonstrably dirty tickers (backward replay yields 116 vs 100).
→ **Default:** use it for **event dates only** (validated 18/18 against NSE for 2015–2020); reconcile every symbol to an ISIN against the bhavcopy of that effective date; where the replayed count ≠ 100 on any date, mark the excess/deficit names UNKNOWN and exclude them from both strategy and benchmark universes. **Mirror the endpoint locally immediately.** Audit-grade membership requires a commercial feed (NSE Indices / Refinitiv / CMIE Prowess).

**Q6 — No free point-in-time shares-outstanding or full market cap**, so the Nifty 100 selection rule cannot be replicated from raw NSE data. NSE monthly market-cap report URLs 404.
→ **Default:** do not attempt to reconstruct membership from raw data. If the membership feed is unusable, the study cannot be a "Nifty 100" study — relabel it "top-200 by 6-month average EQ turnover, ETFs excluded" (91/100 overlap) and say so in the title.

**Q7 — Terminal value for delisted / bankrupt names.** bhavcopy gives no delisting reason, no final settlement value, no recovery figure.
→ **Default:** **−100% on suspension or delisting for cause**; merger consideration where known and linkable; **NEVER a last-traded-price exit** — that overstates returns for bankruptcies, which is exactly the failure mode survivorship bias already causes.

**Q8 — Merger/demerger linkage.** `symbolchange.csv` records renames only (MINDTREE absent despite the LTIMindtree merger).
→ **Default:** on an unlinked series termination, close at the last traded raw price and mark the ISIN untradeable thereafter; log `n_unlinked_terminations`. If that count is material relative to the number of exclusions, the result is not publishable.

**Q9 — Historical per-symbol price bands and ASM/GSM/ESM status.** Only a single 2026-08-21 snapshot was verified. These are daily-varying data; applying today's list across history is itself a look-ahead bias.
→ **Default:** assume a **10% dynamic band** for names PIT-eligible for F&O and **20%** otherwise; where the PIT F&O list is unavailable for a date, assume the **tighter 10%**. Do not apply today's ASM list across history. If the daily surveillance archive is not built, record ASM/GSM as **unmodelled** in the caveats and note the resulting optimism explicitly.

**Q10 — Regime breaks (CAS 2026-08-03, pre-open 2026-09-07).**
→ **Default:** split the out-of-sample period at 2026-08-03. Keep the 25 bps slippage assumption after 2026-09-07 (it will be conservative once market orders get auction priority) but label it as calibrated on the old microstructure.

**Q11 — Point-in-time F&O eligibility for variant V2.** The F&O universe is not static: 45 stocks added 2024-11-29; ATHERENERG, BANKMAHARASHTRA, SAGILITY effective 2026-08-26.
→ **Default:** build the PIT F&O list from dated NSE circulars. **If it cannot be built, V2 is not runnable — do NOT substitute today's 97-name list.** Also note lot sizes are revised periodically, so lot notionals are point-in-time too.

**Q12 — Slippage tail.** The 25 bps base derives from 5 quiet sessions (n=500 symbol-days, Nifty 50 moved 0.08% on 2026-08-21) and the pre-open depth analysis is a **single** session.
→ **Default:** report the full 5 / 25 / 50 bps grid; headline at 25; **declare the strategy dead if it fails at 50**. Re-measure over ≥6 months, separately by volatility regime, before hard-coding any slippage constant into a live system.

**Q13 — Broker routing of pre-open orders is unverified end-to-end.** Whether Zerodha forwards a pre-open order to the exchange, accepts MARKET orders in the pre-open, and whether AMOs route into the auction or into the 09:15 continuous session all determine whether you get the printed open.
→ **Default:** assume the order does **not** reach the auction (25 bps, not 3–5 bps) until a live test order proves otherwise. Then re-run and report the delta.

**Q14 — GST treatment of the Rs 500/month Kite Connect fee is not stated on Zerodha's pricing page.**
→ **Default:** exclude from per-trade costs; carry as a fixed **Rs 590/month** infrastructure line in any live-viability statement.

**Q15 — Risk-free series for cash drag and Sharpe was not pinned by the research.**
→ **Default:** 91-day T-bill (FBIL/RBI weekly auction cut-off yield), forward-filled to daily, used identically in the strategy Sharpe numerator, the benchmark Sharpe numerator, and the flat-day cash credit.

**Q16 — Dividend receipt on the long leg.** Nifty 100 dividend yield is ~1.0–1.3% p.a. Crediting it correctly requires a dated, per-ISIN, net-of-slab-tax ledger that the free data stack does not supply.
→ **Default:** model **zero dividend receipt** on the strategy. This understates strategy return by up to ~1.3 pp/yr on fully-invested days **while the TRI benchmark does include dividends** — a deliberate double penalty in the conservative direction. **This asymmetry MUST be stated next to every benchmark comparison.** Crediting dividends is permitted only if the §5 rule 27 double-count assertion passes.

---

### Conflicts resolved in this spec

| Conflict | Resolution |
|---|---|
| Slippage 3–5 bps (auction) vs 25–30 bps (first minute) | **25 bps.** Auction execution is unproven for this broker (Q13) and the published 2–3 bps impact cost is a continuous-session number, i.e. a floor for open execution. |
| Kite "data is CA-adjusted" vs forum evidence of ~10% divergence | **Trust neither.** Build own factors from a CA feed; validate before use. |
| yfinance has correct CA handling vs yfinance returns 0 rows for delisted Indian names | **Disqualified as price source.** Permitted only as a CA-factor source for live names. |
| "Adjusted prices leak" vs "adjusted prices are safe for SMA" | **Both true, for different things.** Safe for the crossover **sign** (scale invariance); leaks for every rupee-denominated quantity. Hence the mandatory two-series architecture. |
| Zerodha support article listing MCP capabilities vs verified live tool list | **Trust the live probe.** The support article is stale; the hosted MCP does expose `place_order`. Treat the free hosted endpoint as capable of live order placement, not read-only. |
| Legacy bhavcopy "is dead" vs verified 200s through 2024-07-05 | **Not dead.** Use legacy ≤ 2024-07-05, UDiFF ≥ 2024-07-08, per NSE Circular 62424. |
| Turnover proxy as a Nifty 100 substitute | **Rejected** at N=100 (38–56/100 overlap). Only acceptable at N=200 and only under a different study name. |