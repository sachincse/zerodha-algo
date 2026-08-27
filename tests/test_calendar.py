"""The exchange calendar, and what it says about the vendor's data.

Trading days were previously inferred from whether a bar existed, which makes a
market holiday, a suspended stock and a dropped bar indistinguishable. The
XBOM calendar separates them.

Running the audit against the real 15-year panel turned up a specific, and
mostly benign, disagreement: roughly 24 days out of 4,103 where the calendar
and Yahoo disagree, concentrated almost entirely on **Diwali Muhurat**
sessions. Those are ceremonial one-hour sessions that fall on whatever day
Diwali lands on - including Saturdays and Sundays (2019-10-27, 2020-11-14) -
and `exchange_calendars` includes some while excluding others.

That is 0.6% of sessions, all of them minimal-volume, so it does not move the
headline numbers. It is pinned here so a NEW data problem does not hide inside
a discrepancy that was already there and already understood.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.trading_calendar import (audit_panel, is_session, previous_session,
                                  sessions)


# --------------------------------------------------------------------------
# the calendar itself
# --------------------------------------------------------------------------

def test_weekends_are_not_sessions():
    assert not is_session("2026-08-15")     # Saturday, and Independence Day
    assert not is_session("2026-08-16")     # Sunday
    assert is_session("2026-08-27")         # ordinary Thursday


def test_republic_day_is_a_holiday():
    """26 January, and in 2026 it falls on a Monday, so a weekday check alone
    would call it a trading day."""
    assert pd.Timestamp("2026-01-26").day_name() == "Monday"
    assert not is_session("2026-01-26")


def test_a_year_has_roughly_250_sessions():
    for year in (2011, 2018, 2025):
        n = len(sessions(f"{year}-01-01", f"{year}-12-31"))
        assert 240 <= n <= 255, f"{year} had {n} sessions"


def test_previous_session_skips_the_weekend():
    """Monday's 'yesterday' is Friday. The circuit-band reference price depends
    on getting this right, and a naive day-minus-one gets it wrong 20% of the
    time."""
    monday = pd.Timestamp("2026-08-24")
    assert monday.day_name() == "Monday"
    prev = previous_session(monday)
    assert prev == pd.Timestamp("2026-08-21")
    assert prev.day_name() == "Friday"


def test_previous_session_skips_a_holiday_too():
    """26 Jan 2026 is a Monday holiday, so 27 Jan's previous session is the
    Friday before it, three days earlier."""
    prev = previous_session("2026-01-27")
    assert prev == pd.Timestamp("2026-01-23")


# --------------------------------------------------------------------------
# the audit
# --------------------------------------------------------------------------

def _panel(index, cols=("A", "B")) -> pd.DataFrame:
    return pd.DataFrame(100.0, index=pd.DatetimeIndex(index), columns=list(cols))


def test_a_clean_panel_audits_clean():
    idx = sessions("2025-01-01", "2025-06-30")
    a = audit_panel(_panel(idx))
    assert a.clean
    assert not a.gappy_symbols
    assert a.expected_sessions == a.panel_rows


def test_a_row_on_a_sunday_is_flagged():
    idx = list(sessions("2025-01-01", "2025-03-31")) + [pd.Timestamp("2025-03-30")]
    a = audit_panel(_panel(sorted(idx)))
    assert not a.clean
    assert pd.Timestamp("2025-03-30") in a.phantom_rows


def test_a_dropped_session_is_flagged():
    idx = list(sessions("2025-01-01", "2025-03-31"))
    dropped = idx[20]
    a = audit_panel(_panel([d for d in idx if d != dropped]))
    assert dropped in a.missing_sessions


def test_a_symbol_with_holes_is_named():
    """A stock that stops printing prices mid-window is either suspended or a
    vendor gap. Either way the backtest should not silently absorb it."""
    idx = sessions("2025-01-01", "2025-06-30")
    df = _panel(idx, cols=("GOOD", "PATCHY"))
    df.iloc[30:45, df.columns.get_loc("PATCHY")] = np.nan
    a = audit_panel(df, min_missing=1)
    assert a.gappy_symbols.get("PATCHY") == 15
    assert "GOOD" not in a.gappy_symbols


def test_report_is_readable_when_everything_is_fine():
    a = audit_panel(_panel(sessions("2025-01-01", "2025-06-30")))
    assert "matches the exchange exactly" in a.report()


# --------------------------------------------------------------------------
# against the real cache, when it is present
# --------------------------------------------------------------------------

@pytest.mark.slow
def test_the_real_panel_disagrees_only_on_known_muhurat_sessions():
    """Pins the known discrepancy so a new one cannot hide inside it.

    Skipped when the 125 MB cache is absent, which is the normal state of a
    fresh clone and of CI.
    """
    from src.data import CACHE_DIR, build_store, load_nifty100_symbols
    import os

    if not os.path.isdir(CACHE_DIR) or len(os.listdir(CACHE_DIR)) < 50:
        pytest.skip("price cache not present - run fetch_data.py")

    store = build_store(load_nifty100_symbols()[:100], "2010-01-01", "2026-08-22")
    a = audit_panel(store.panels["Close"], min_missing=10 ** 9)

    total = len(a.phantom_rows) + len(a.missing_sessions)
    assert total < 40, (
        f"{total} calendar disagreements, up from the 24 recorded when this "
        f"was written. Something changed in the data or the calendar:\n"
        f"{a.report()}")

    # Muhurat falls in October or November. If a disagreement shows up outside
    # those months it is not the known one and deserves a look.
    off_season = [d for d in a.phantom_rows + a.missing_sessions
                  if d.month not in (1, 2, 3, 4, 5, 6, 10, 11)]
    assert not off_season, f"unexplained calendar gaps: {off_season}"
