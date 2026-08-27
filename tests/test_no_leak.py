"""Tests that try to *prove* the backtest cannot see the future.

Three independent arguments are made:

1. TRUNCATION       Signals computed on data[:t] equal signals computed on the
                    full sample and then sliced to t. If any signal peeked
                    ahead, truncating the input would change it.

2. FUTURE SCRAMBLE  Replace every bar after date T with noise. The equity curve
                    up to T must be bit-identical. This is the strongest test:
                    it catches leakage through any channel, including ones the
                    author did not think of.

3. FILL TIMING      Every entry price equals the *next* session's open moved by
                    exactly the slippage assumption. No trade is ever filled on
                    the bar that produced its signal.

Run:  python -m pytest tests/ -v
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.backtest import run_backtest            # noqa: E402
from src.config import DEFAULT, Settings, PortfolioModel  # noqa: E402
from src.data import PriceStore                  # noqa: E402
from src.strategy import crossover_state, signals_asof, _bars_since  # noqa: E402
from src.universe import pit_turnover_membership  # noqa: E402


# --------------------------------------------------------------------------
# synthetic fixture -- deterministic, no network
# --------------------------------------------------------------------------
def make_store(n_days: int = 600, n_syms: int = 8, seed: int = 7) -> PriceStore:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=n_days)
    syms = [f"SYM{i}" for i in range(n_syms)]

    close = {}
    for s in syms:
        steps = rng.normal(0.0004, 0.016, n_days)
        close[s] = 500 * np.exp(np.cumsum(steps))
    cl = pd.DataFrame(close, index=dates)

    op = cl.shift(1).fillna(cl.iloc[0]) * (1 + rng.normal(0, 0.004, cl.shape))
    hi = np.maximum(cl, op) * (1 + abs(rng.normal(0, 0.004, cl.shape)))
    lo = np.minimum(cl, op) * (1 - abs(rng.normal(0, 0.004, cl.shape)))
    vol = pd.DataFrame(rng.integers(4_000_000, 9_000_000, cl.shape),
                       index=dates, columns=syms).astype(float)

    panels = {"Open": pd.DataFrame(op, index=dates, columns=syms),
              "High": pd.DataFrame(hi, index=dates, columns=syms),
              "Low": pd.DataFrame(lo, index=dates, columns=syms),
              "Close": cl,
              "Volume": vol}
    div = pd.DataFrame(0.0, index=dates, columns=syms)
    return PriceStore(panels, div)


def all_true_membership(store: PriceStore) -> pd.DataFrame:
    cl = store.panels["Close"]
    return pd.DataFrame(True, index=cl.index, columns=cl.columns)


SETTINGS = Settings(portfolio=PortfolioModel(max_positions=4,
                                             initial_capital=1_000_000.0))


# --------------------------------------------------------------------------
# 1. TRUNCATION
# --------------------------------------------------------------------------
def test_signals_are_causal_under_truncation():
    store = make_store()
    cl = store.panels["Close"]
    full = crossover_state(cl, 6, 30)

    for cut in (200, 350, 500):
        t = cl.index[cut]
        part = crossover_state(cl.loc[:t], 6, 30)
        for key in ("sma_short", "sma_long", "regime", "bull_cross",
                    "bear_cross", "bars_since_cross"):
            a = part[key]
            b = full[key].loc[:t]
            pd.testing.assert_frame_equal(a, b, check_dtype=False,
                                          obj=f"{key} truncated at {t.date()}")


def test_scanner_table_is_causal():
    store = make_store()
    cl = store.panels["Close"]
    t = cl.index[400]
    from_full = signals_asof(cl, 6, 30, asof=t)
    from_part = signals_asof(cl.loc[:t], 6, 30, asof=t)
    pd.testing.assert_frame_equal(from_full, from_part)


def test_bars_since_never_looks_forward():
    idx = pd.bdate_range("2021-01-01", periods=10)
    flag = pd.DataFrame({"A": [False, False, True, False, False,
                               False, True, False, False, False]}, index=idx)
    got = _bars_since(flag)["A"].tolist()
    assert np.isnan(got[0]) and np.isnan(got[1])
    assert got[2:] == [0, 1, 2, 3, 0, 1, 2, 3]


# --------------------------------------------------------------------------
# 2. FUTURE SCRAMBLE -- the decisive test
# --------------------------------------------------------------------------
def _scramble_after(store: PriceStore, cut: int, seed: int) -> PriceStore:
    rng = np.random.default_rng(seed)
    panels = {}
    for k, v in store.panels.items():
        v2 = v.copy()
        tail = v2.iloc[cut + 1:]
        if k == "Volume":
            v2.iloc[cut + 1:] = rng.integers(1e6, 1e7, tail.shape).astype(float)
        else:
            v2.iloc[cut + 1:] = tail.to_numpy() * rng.uniform(0.4, 2.5, tail.shape)
        panels[k] = v2
    return PriceStore(panels, store.dividends.copy())


@pytest.mark.parametrize("cut", [250, 400])
@pytest.mark.parametrize("seed", [1, 2])
def test_future_scramble_leaves_past_equity_identical(cut, seed):
    """If any part of the engine reads a future bar, mangling the future will
    move the past equity curve. It must not."""
    base = make_store()
    scrambled = _scramble_after(base, cut, seed)

    r1 = run_backtest(base, all_true_membership(base), SETTINGS)
    r2 = run_backtest(scrambled, all_true_membership(scrambled), SETTINGS)

    t = base.panels["Close"].index[cut]
    e1 = r1.equity.loc[:t]
    e2 = r2.equity.loc[:t]

    assert len(e1) == len(e2) and len(e1) > 50, "not enough overlap to be meaningful"
    pd.testing.assert_series_equal(e1, e2, check_exact=False, rtol=1e-12)


def test_future_scramble_leaves_past_trades_identical():
    base = make_store()
    cut = 400
    scrambled = _scramble_after(base, cut, seed=3)
    t = base.panels["Close"].index[cut]

    r1 = run_backtest(base, all_true_membership(base), SETTINGS)
    r2 = run_backtest(scrambled, all_true_membership(scrambled), SETTINGS)

    # Compare only trades that both ENTERED and EXITED before the scramble
    # point; a trade still open at the cut is legitimately affected afterwards.
    k1 = [(x.symbol, x.entry_date, round(x.entry_price, 8), x.exit_date,
           round(x.exit_price, 8)) for x in r1.trades if x.exit_date <= t]
    k2 = [(x.symbol, x.entry_date, round(x.entry_price, 8), x.exit_date,
           round(x.exit_price, 8)) for x in r2.trades if x.exit_date <= t]
    assert k1 == k2 and len(k1) > 5


# --------------------------------------------------------------------------
# 3. FILL TIMING
# --------------------------------------------------------------------------
def test_every_fill_is_at_a_later_bars_open():
    store = make_store()
    res = run_backtest(store, all_true_membership(store), SETTINGS)
    op = store.panels["Open"]
    cl = store.panels["Close"]
    bull = crossover_state(cl, 6, 30)["bull_cross"]
    bps = SETTINGS.execution.slippage_bps / 10_000

    assert len(res.trades) > 5, "fixture produced too few trades to test"
    for tr in res.trades:
        # entry price is exactly the entry bar's open plus slippage
        expected = float(op.at[tr.entry_date, tr.symbol]) * (1 + bps)
        assert tr.entry_price == pytest.approx(expected, rel=1e-12), tr

        # and the signal that caused it fired STRICTLY BEFORE the entry bar
        i = cl.index.get_loc(tr.entry_date)
        assert i >= 1
        prior = cl.index[i - 1]
        assert bool(bull.at[prior, tr.symbol]), (
            f"{tr.symbol} filled {tr.entry_date.date()} without a cross on "
            f"{prior.date()}")

        # exits likewise
        expected_x = float(op.at[tr.exit_date, tr.symbol]) * (1 - bps)
        assert tr.exit_price == pytest.approx(expected_x, rel=1e-12), tr
        assert tr.exit_date > tr.entry_date


def test_no_trade_fills_on_its_own_signal_bar():
    store = make_store()
    res = run_backtest(store, all_true_membership(store), SETTINGS)
    cl = store.panels["Close"]
    bull = crossover_state(cl, 6, 30)["bull_cross"]
    for tr in res.trades:
        assert not bool(bull.at[tr.entry_date, tr.symbol]), (
            f"{tr.symbol} filled on the same bar its signal fired")


# --------------------------------------------------------------------------
# 4. UNIVERSE is point-in-time
# --------------------------------------------------------------------------
def test_pit_universe_is_causal_under_truncation():
    store = make_store(n_days=800, n_syms=12)
    full = pit_turnover_membership(store, top_n=6, lookback=40, min_history=60)
    t = store.panels["Close"].index[500]
    part = pit_turnover_membership(store.slice(end=t), top_n=6,
                                   lookback=40, min_history=60)
    pd.testing.assert_frame_equal(part, full.loc[:t], check_dtype=False)


# --------------------------------------------------------------------------
# 5. ACCOUNTING sanity -- a leak often shows up as impossible money
# --------------------------------------------------------------------------
def test_costs_always_reduce_pnl():
    from src.config import CostModel
    c = CostModel()
    for turnover in (10_000, 100_000, 1_000_000):
        assert c.buy_cost(turnover) > 0
        assert c.sell_cost(turnover) > c.buy_cost(turnover) * 0.5


def test_equity_never_uses_more_cash_than_it_has():
    store = make_store()
    res = run_backtest(store, all_true_membership(store), SETTINGS)
    assert (res.equity > 0).all()
    assert res.daily_positions.max() <= SETTINGS.portfolio.max_positions


def test_zero_cost_beats_real_cost():
    """Turning costs off must improve the result. If it does not, the cost
    model is not actually wired into the P&L."""
    from src.config import CostModel, ExecModel
    store = make_store()
    free = Settings(costs=CostModel(stt_delivery_buy=0, stt_delivery_sell=0,
                                    exchange_txn_nse=0, sebi_turnover=0,
                                    stamp_duty_delivery_buy=0, gst_rate=0,
                                    dp_charge_per_sell=0),
                    execution=ExecModel(slippage_bps=0.0),
                    portfolio=SETTINGS.portfolio)
    r_real = run_backtest(store, all_true_membership(store), SETTINGS)
    r_free = run_backtest(store, all_true_membership(store), free)
    assert r_free.equity.iloc[-1] > r_real.equity.iloc[-1]


# --------------------------------------------------------------------------
# 6. The sparse candidate index must equal a dense scan of every column
# --------------------------------------------------------------------------
def test_sparse_candidate_index_matches_dense_scan():
    """backtest.py indexes crossovers sparsely for speed. That rewrite must be
    a pure lookup change -- identical trades, identical equity."""
    import types
    import src.backtest as bt

    src_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "src", "backtest.py")
    code = open(src_path, encoding="utf-8").read()
    assert "for sym in bull_by_row[i]:" in code, "sparse loop not found"
    dense_code = code.replace(
        "for sym in bull_by_row[i]:",
        "for sym in cl.columns:\n"
        "                if not bool(bull.at[t, sym]):\n"
        "                    continue")

    mod = types.ModuleType("dense_bt")
    mod.__name__, mod.__package__ = "dense_bt", "src"
    sys.modules["dense_bt"] = mod
    exec(compile(dense_code, "dense_bt.py", "exec"), mod.__dict__)

    store = make_store(n_days=700, n_syms=10)
    mem = all_true_membership(store)
    fast = bt.run_backtest(store, mem, SETTINGS)
    dense = mod.run_backtest(store, mem, SETTINGS)

    pd.testing.assert_series_equal(fast.equity, dense.equity)
    key = lambda r: [(t.symbol, t.entry_date, t.exit_date, round(t.entry_price, 9))
                     for t in r.trades]
    assert key(fast) == key(dense) and len(fast.trades) > 5
