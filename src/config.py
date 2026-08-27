"""Central configuration. Every number a backtest depends on lives here, so it
can be audited in one place and changed without touching engine code."""
from __future__ import annotations
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Strategy defaults (these are the values from the Zerodha Varsity video)
# --------------------------------------------------------------------------
SHORT_WINDOW = 6
LONG_WINDOW = 30

# --------------------------------------------------------------------------
# Zerodha cost model, NSE equity.
#
# Rates verified against zerodha.com/charges. Anything marked UNVERIFIED must
# be confirmed before you trust a P&L number produced with it.
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class CostModel:
    """All rates are fractions of turnover unless the name says otherwise."""

    # --- Equity delivery (CNC) -------------------------------------------
    brokerage_delivery: float = 0.0            # Zerodha: free on delivery
    brokerage_delivery_cap: float = 0.0

    # --- Equity intraday (MIS) -------------------------------------------
    brokerage_intraday_pct: float = 0.0003     # 0.03%
    brokerage_intraday_cap: float = 20.0       # Rs 20 per executed order

    # --- Statutory, delivery ---------------------------------------------
    stt_delivery_buy: float = 0.001            # 0.1%
    stt_delivery_sell: float = 0.001           # 0.1%
    # --- Statutory, intraday ---------------------------------------------
    stt_intraday_buy: float = 0.0
    stt_intraday_sell: float = 0.00025         # 0.025% on sell only

    exchange_txn_nse: float = 0.0000307        # 0.00307% = Rs 3.07/lakh, incl NSE IPFT
    sebi_turnover: float = 0.000001            # Rs 10 per crore
    ipft_nse: float = 0.0                      # bundled into exchange_txn_nse above

    stamp_duty_delivery_buy: float = 0.00015   # 0.015% on buy side
    stamp_duty_intraday_buy: float = 0.00003   # 0.003% on buy side

    gst_rate: float = 0.18                     # on brokerage + txn + sebi
    dp_charge_per_sell: float = 15.34          # per scrip per sell day; GST ALREADY
    #                                           included -- do not gross it up again

    def buy_cost(self, turnover: float, product: str = "CNC") -> float:
        if product == "CNC":
            brok = self.brokerage_delivery
            stt = turnover * self.stt_delivery_buy
            stamp = turnover * self.stamp_duty_delivery_buy
        else:
            brok = min(turnover * self.brokerage_intraday_pct,
                       self.brokerage_intraday_cap)
            stt = turnover * self.stt_intraday_buy
            stamp = turnover * self.stamp_duty_intraday_buy
        txn = turnover * (self.exchange_txn_nse + self.ipft_nse)
        sebi = turnover * self.sebi_turnover
        gst = (brok + txn + sebi) * self.gst_rate
        return brok + stt + txn + sebi + stamp + gst

    def sell_cost(self, turnover: float, product: str = "CNC",
                  include_dp: bool = True) -> float:
        """`include_dp=False` prices a single ORDER rather than a sell day.

        The DP charge is levied per scrip per sell day, not per order, so it
        cannot appear on a per-order contract note. Comparing this model
        against the broker's own quote requires leaving it out, otherwise the
        two disagree by exactly Rs 15.34 every time and the comparison proves
        nothing. Backtesting always wants it, so it stays the default.
        """
        if product == "CNC":
            brok = self.brokerage_delivery
            stt = turnover * self.stt_delivery_sell
            dp = self.dp_charge_per_sell if include_dp else 0.0
        else:
            brok = min(turnover * self.brokerage_intraday_pct,
                       self.brokerage_intraday_cap)
            stt = turnover * self.stt_intraday_sell
            dp = 0.0
        txn = turnover * (self.exchange_txn_nse + self.ipft_nse)
        sebi = turnover * self.sebi_turnover
        gst = (brok + txn + sebi) * self.gst_rate
        return brok + stt + txn + sebi + dp + gst


# --------------------------------------------------------------------------
# Execution assumptions
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ExecModel:
    # Signal fires on the close of day t. We can only trade from day t+1.
    # We fill at day t+1's OPEN, degraded by slippage. Never at close[t].
    fill_on: str = "next_open"
    # 25 bps/side is the defensible number for a market order in the first minute
    # of continuous trading on a Nifty-100 name: measured |09:15 close - open| has
    # a median of ~21 bps and a p90 of ~60 bps. NSE's published Rs-1-lakh impact
    # cost (median 2 bps) is a FLOOR that applies only if the order is provably
    # inside the pre-open call auction, which retail routing does not guarantee.
    # Sensitivity is reported at 5 / 25 / 50 bps.
    slippage_bps: float = 25.0
    # A fill is refused if the bar it would print on has zero volume, or if the
    # open gaps to the circuit band, where a market order cannot transact.
    # F&O-eligible names -- 97 of the current Nifty 100 -- carry a 10% dynamic
    # operating range from the previous close; the rest carry a fixed band.
    # The open printed within 0.2% of the band on ~0.04% of stock-days
    # (1 in 2,620) over 2011-2026, clustered heavily in 2020.
    circuit_pct: float = 0.10
    band_tolerance: float = 0.002     # "within 0.2% of the band" counts as locked
    min_price: float = 20.0           # skip penny-priced instruments
    min_median_turnover: float = 5e7  # Rs 5 crore/day trailing median


@dataclass(frozen=True)
class PortfolioModel:
    initial_capital: float = 1_000_000.0
    max_positions: int = 10
    # Equal-weight across open slots. Sizing uses only information available at
    # the close of the signal day.
    allow_short: bool = False         # see docs: cannot short equity delivery
    # Rank competing entry signals by crossover recency (the video's rule).
    rank_by: str = "recency"


@dataclass(frozen=True)
class Settings:
    costs: CostModel = field(default_factory=CostModel)
    execution: ExecModel = field(default_factory=ExecModel)
    portfolio: PortfolioModel = field(default_factory=PortfolioModel)
    short_window: int = SHORT_WINDOW
    long_window: int = LONG_WINDOW


DEFAULT = Settings()
