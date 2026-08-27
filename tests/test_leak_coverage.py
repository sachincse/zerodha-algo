"""The leaks test_no_leak.py does not catch.

Mutation-testing the original suite — inject a real lookahead bug, see whether
the tests go red — killed 4 of 8 and missed 4. The misses were not random. They
fell into two blind spots:

  UNIVERSE   Every future-scramble test runs with all_true_membership(), so the
             real point-in-time universe builder is never executed. A universe
             selected on future turnover, or on the whole history at once, was
             therefore invisible: the engine tests bypassed it entirely, and
             the one test that did cover it checked a single truncation point.

  HOSTILE    The synthetic fixture is a smooth random walk: every symbol trades
             every day, no gaps, uniform volume, zero dividends. That means the
             circuit-band branch, the illiquidity branch and the dividend path
             never execute, so a leak inside any of them cannot change a single
             number. The tests were exercising the happy path and calling it
             proof.

  SIZING     Trades are compared on (symbol, dates, prices) with no quantity,
             so a position sized from a price that is not knowable at fill time
             leaves every assertion untouched.

Each test here is written against a specific mutation that survived. See
tests/test_mutants.py, which re-injects all eight and fails if any survives.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.backtest import run_backtest
from src.data import PriceStore
from src.universe import pit_turnover_membership

from tests.test_no_leak import (SETTINGS, _scramble_after, all_true_membership,
                                make_store)

UNIVERSE_KW = dict(top_n=6, lookback=40, min_history=60)


# --------------------------------------------------------------------------
# a fixture with the awkward days a real market has
# --------------------------------------------------------------------------
def make_hostile_store(n_days: int = 800, n_syms: int = 12,
                      seed: int = 11) -> PriceStore:
    """The smooth fixture with the awkward cases put back in.

    Gap days exist so the circuit-band check actually runs; a thin symbol
    exists so the liquidity filter actually rejects something; NaN holes exist
    so per-symbol calendars diverge; dividends are non-zero so that path is
    live. A leak hiding in any of those branches now has something to change.
    """
    rng = np.random.default_rng(seed)
    base = make_store(n_days=n_days, n_syms=n_syms, seed=seed)
    panels = {k: v.copy() for k, v in base.panels.items()}
    cl, op = panels["Close"], panels["Open"]
    syms = list(cl.columns)

    # Gap days: a jump far outside any circuit band, on scattered dates.
    for i, s in enumerate(syms[:5]):
        for d in rng.choice(np.arange(80, n_days - 5), size=6, replace=False):
            shock = 1.0 + (0.16 if (i + d) % 2 else -0.16)
            cl.iloc[d:, cl.columns.get_loc(s)] *= shock
            op.iloc[d, op.columns.get_loc(s)] *= shock

    # One name too thin to qualify, so min_median_turnover has work to do.
    panels["Volume"].iloc[:, panels["Volume"].columns.get_loc(syms[-1])] = 500.0

    # Days a symbol simply did not trade.
    for s in syms[1:4]:
        holes = rng.choice(np.arange(60, n_days), size=25, replace=False)
        for k in panels:
            panels[k].iloc[holes, panels[k].columns.get_loc(s)] = np.nan

    panels["High"] = np.maximum(panels["High"], np.maximum(cl, op))
    panels["Low"] = np.minimum(panels["Low"], np.minimum(cl, op))

    div = pd.DataFrame(0.0, index=cl.index, columns=cl.columns)
    for s in syms[:4]:
        for d in rng.choice(np.arange(100, n_days), size=3, replace=False):
            div.iloc[d, div.columns.get_loc(s)] = 4.0
    return PriceStore(panels, div)


# --------------------------------------------------------------------------
# UNIVERSE — the builder itself must be causal, at every cut
# --------------------------------------------------------------------------
@pytest.mark.parametrize("cut", [320, 380, 440, 500, 560, 620, 680, 740])
def test_pit_universe_is_causal_at_every_cut(cut):
    """One truncation point is a spot check, not a proof.

    A centered rolling window only changes the answer near the end of the
    series it is given, so testing a single cut can miss it entirely depending
    on where the rebalance dates fall.
    """
    store = make_store(n_days=800, n_syms=12)
    full = pit_turnover_membership(store, **UNIVERSE_KW)
    t = store.panels["Close"].index[cut]
    part = pit_turnover_membership(store.slice(end=t), **UNIVERSE_KW)
    pd.testing.assert_frame_equal(part, full.loc[:t], check_dtype=False)


def test_universe_does_not_react_to_a_scrambled_future():
    """Mangle every bar after T; membership up to T must not move."""
    base = make_store(n_days=800, n_syms=12)
    cut = 500
    scrambled = _scramble_after(base, cut, seed=5)
    t = base.panels["Close"].index[cut]

    m1 = pit_turnover_membership(base, **UNIVERSE_KW).loc[:t]
    m2 = pit_turnover_membership(scrambled, **UNIVERSE_KW).loc[:t]
    pd.testing.assert_frame_equal(m1, m2, check_dtype=False)


def test_full_backtest_through_the_real_universe_is_causal():
    """The end-to-end argument, with the universe builder actually in it.

    Every scramble test in test_no_leak.py passes all_true_membership, which
    means the component most likely to leak — the one that decides which
    symbols exist — was excluded from the strongest test in the suite.
    """
    base = make_store(n_days=800, n_syms=12)
    cut = 500
    scrambled = _scramble_after(base, cut, seed=5)
    t = base.panels["Close"].index[cut]

    r1 = run_backtest(base, pit_turnover_membership(base, **UNIVERSE_KW), SETTINGS)
    r2 = run_backtest(scrambled, pit_turnover_membership(scrambled, **UNIVERSE_KW),
                      SETTINGS)

    e1, e2 = r1.equity.loc[:t], r2.equity.loc[:t]
    assert len(e1) == len(e2) and len(e1) > 50
    pd.testing.assert_series_equal(e1, e2, check_exact=False, rtol=1e-12)


# --------------------------------------------------------------------------
# SIZING — quantity is part of a trade's identity
# --------------------------------------------------------------------------
def test_scramble_leaves_past_trade_QUANTITIES_identical():
    """The existing comparison omits qty, so sizing leaks slip past it.

    Size is chosen from a price. If that price is one the future supplied, the
    quantity moves while symbol, dates and fill prices all stay put — and every
    assertion in the original suite keeps passing.
    """
    base = make_store()
    cut = 400
    scrambled = _scramble_after(base, cut, seed=3)
    t = base.panels["Close"].index[cut]

    r1 = run_backtest(base, all_true_membership(base), SETTINGS)
    r2 = run_backtest(scrambled, all_true_membership(scrambled), SETTINGS)

    def key(res):
        return [(x.symbol, x.entry_date, x.qty, round(x.entry_price, 8),
                 x.exit_date, round(x.exit_price, 8))
                for x in res.trades if x.exit_date <= t]

    k1, k2 = key(r1), key(r2)
    assert len(k1) > 5, "not enough closed trades before the cut to be meaningful"
    assert k1 == k2


# --------------------------------------------------------------------------
# HOSTILE DATA — the branches the smooth fixture never reaches
# --------------------------------------------------------------------------
def test_scramble_is_causal_on_hostile_data():
    """Gaps, holes, a thin name and dividends — then scramble the future.

    This is what catches a leak in the circuit-band reference price. On the
    smooth fixture no bar ever gaps far enough to consult it, so that branch
    could read tomorrow's close and nothing would notice.
    """
    base = make_hostile_store()
    cut = 500
    scrambled = _scramble_after(base, cut, seed=9)
    t = base.panels["Close"].index[cut]

    r1 = run_backtest(base, all_true_membership(base), SETTINGS)
    r2 = run_backtest(scrambled, all_true_membership(scrambled), SETTINGS)

    e1, e2 = r1.equity.loc[:t], r2.equity.loc[:t]
    assert len(e1) == len(e2) and len(e1) > 50
    pd.testing.assert_series_equal(e1, e2, check_exact=False, rtol=1e-12)


def test_hostile_fixture_actually_exercises_the_awkward_branches():
    """A hostile fixture that is not actually hostile proves nothing.

    Asserted explicitly, because the whole failure being fixed here is a test
    that looked like it covered something and did not.
    """
    store = make_hostile_store()
    cl = store.panels["Close"]

    ret = cl.pct_change()
    assert (ret.abs() > 0.10).to_numpy().sum() >= 5, "no gap days — circuit band never runs"
    assert cl.isna().to_numpy().sum() > 50, "no missing bars — per-symbol calendars never diverge"
    assert (store.dividends > 0).to_numpy().sum() >= 8, "no dividends — that path never runs"

    turnover = (cl * store.panels["Volume"]).median()
    assert turnover.min() * 20 < turnover.median(), "no thin name — liquidity filter never rejects"
