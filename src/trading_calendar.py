"""The real NSE/BSE session calendar, instead of guessing from the data.

Both repos previously decided what a trading day was by asking whether a bar
existed. That conflates three different things:

  * a market holiday, when nobody traded and nothing is wrong;
  * a suspension or a listing that had not happened yet, which is a genuine
    per-symbol absence the backtest must respect;
  * a hole in the vendor's data, which is a bug that silently changes results.

Yahoo is the price source here, and Yahoo drops bars. Without a calendar those
three are indistinguishable, so a data problem looks exactly like a holiday and
gets absorbed into the result rather than reported.

`exchange_calendars` ships XBOM with a precomputed holiday list running from
1997 to the end of 2026, sourced from NSE's own published schedule.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import pandas as pd

CALENDAR = "XBOM"          # BSE/NSE share a session calendar


@lru_cache(maxsize=4)
def _calendar(name: str = CALENDAR):
    import exchange_calendars as xc
    return xc.get_calendar(name)


def sessions(start, end, name: str = CALENDAR) -> pd.DatetimeIndex:
    """Actual trading sessions in [start, end], tz-naive to match the panels."""
    cal = _calendar(name)
    lo = max(pd.Timestamp(start), pd.Timestamp(cal.first_session).tz_localize(None))
    hi = min(pd.Timestamp(end), pd.Timestamp(cal.last_session).tz_localize(None))
    if lo > hi:
        return pd.DatetimeIndex([])
    idx = cal.sessions_in_range(lo, hi)
    return pd.DatetimeIndex(idx).tz_localize(None).normalize()


def is_session(day, name: str = CALENDAR) -> bool:
    d = pd.Timestamp(day).normalize()
    return len(sessions(d, d, name)) == 1


def previous_session(day, name: str = CALENDAR) -> pd.Timestamp | None:
    """The last session strictly before `day` — what "yesterday's close" means."""
    d = pd.Timestamp(day).normalize()
    s = sessions(d - pd.Timedelta(days=20), d - pd.Timedelta(days=1), name)
    return s[-1] if len(s) else None


@dataclass
class CalendarAudit:
    """What the panel's index disagrees with the exchange about."""
    first: pd.Timestamp
    last: pd.Timestamp
    expected_sessions: int
    panel_rows: int
    phantom_rows: list = field(default_factory=list)   # in panel, not a session
    missing_sessions: list = field(default_factory=list)  # session, no row at all
    gappy_symbols: dict = field(default_factory=dict)  # symbol -> missing count

    @property
    def clean(self) -> bool:
        return not self.phantom_rows and not self.missing_sessions

    def report(self, top: int = 8) -> str:
        out = [f"  {self.first.date()} to {self.last.date()}",
               f"  {self.expected_sessions} exchange sessions, "
               f"{self.panel_rows} rows in the panel"]
        if self.phantom_rows:
            out.append(f"  {len(self.phantom_rows)} rows on NON-trading days "
                       f"(vendor artefacts): "
                       f"{', '.join(str(d.date()) for d in self.phantom_rows[:5])}")
        if self.missing_sessions:
            out.append(f"  {len(self.missing_sessions)} sessions absent from the "
                       f"panel entirely: "
                       f"{', '.join(str(d.date()) for d in self.missing_sessions[:5])}")
        if self.gappy_symbols:
            worst = sorted(self.gappy_symbols.items(), key=lambda kv: -kv[1])[:top]
            out.append(f"  symbols missing the most sessions "
                       f"(suspension, late listing, or dropped bars):")
            out += [f"    {s:<14} {n} sessions" for s, n in worst]
        if self.clean and not self.gappy_symbols:
            out.append("  the panel's calendar matches the exchange exactly")
        return "\n".join(out)


def audit_panel(close: pd.DataFrame, name: str = CALENDAR,
                min_missing: int = 1) -> CalendarAudit:
    """Compare a price panel's index against the real exchange calendar.

    `gappy_symbols` counts sessions where the exchange was open, the panel has
    a row, and that symbol has no price. Some of that is legitimate — a stock
    listed halfway through the window — so this reports rather than raises.
    """
    idx = pd.DatetimeIndex(close.index).tz_localize(None).normalize()
    exp = sessions(idx.min(), idx.max(), name)

    audit = CalendarAudit(first=idx.min(), last=idx.max(),
                          expected_sessions=len(exp), panel_rows=len(idx))
    exp_set, idx_set = set(exp), set(idx)
    audit.phantom_rows = sorted(idx_set - exp_set)
    audit.missing_sessions = sorted(exp_set - idx_set)

    on_session = close.loc[idx.isin(exp)]
    missing = on_session.isna().sum()
    audit.gappy_symbols = {s: int(n) for s, n in missing.items() if n >= min_missing}
    return audit
