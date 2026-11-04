"""Hong Kong securities trading and settlement dates.

Shareholding search dates are settlement days. A settlement day is a day the
Hong Kong securities market is open for a full session. Weekends, full-day
closures, and half-day non-settlement sessions are not settlement days.

The half-day sessions below still trade in the morning, but HKEX does not run
settlement. They are not CCASS query dates: asking for one collides with the
next real settlement day's T+2 label.

Years listed here are taken from the HKEX securities-market holiday schedule
and work with no network and without exchange_calendars. Other years use
``exchange_calendars`` XHKG when that package is installed, and otherwise fall
back to weekdays.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

# Full-day securities-market closures.
# 2026: HKEX circular SEHK CT/075/2025.
# 2025: same source calendar (XHKG), whose 2026 sessions match that circular.
_FULL_CLOSURES = {
    # 2025
    date(2025, 1, 1),
    date(2025, 1, 29),
    date(2025, 1, 30),
    date(2025, 1, 31),
    date(2025, 4, 4),
    date(2025, 4, 18),
    date(2025, 4, 21),
    date(2025, 5, 1),
    date(2025, 5, 5),
    date(2025, 7, 1),
    date(2025, 10, 1),
    date(2025, 10, 7),
    date(2025, 10, 29),
    date(2025, 12, 25),
    date(2025, 12, 26),
    # 2026
    date(2026, 1, 1),
    date(2026, 2, 17),
    date(2026, 2, 18),
    date(2026, 2, 19),
    date(2026, 4, 3),
    date(2026, 4, 6),
    date(2026, 4, 7),
    date(2026, 5, 1),
    date(2026, 5, 25),
    date(2026, 6, 19),
    date(2026, 7, 1),
    date(2026, 10, 1),
    date(2026, 10, 19),
    date(2026, 12, 25),
}

# Morning session only; no settlement run.
_NON_SETTLEMENT_HALF_DAYS = {
    date(2025, 1, 28),
    date(2025, 12, 24),
    date(2025, 12, 31),
    date(2026, 2, 16),
    date(2026, 12, 24),
    date(2026, 12, 31),
}

_BUILTIN_YEARS = frozenset(d.year for d in _FULL_CLOSURES | _NON_SETTLEMENT_HALF_DAYS)

_xhkg = None
_xhkg_unavailable = False
_weekday_fallback_years: set[int] = set()


def _as_date(value: date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value


def _xhkg_calendar():
    """Return the XHKG calendar, or None when the package is missing."""
    global _xhkg, _xhkg_unavailable
    if _xhkg_unavailable:
        return None
    if _xhkg is not None:
        return _xhkg
    try:
        import exchange_calendars as xcals
        _xhkg = xcals.get_calendar('XHKG')
    except Exception as exc:
        logger.info('exchange_calendars XHKG unavailable, using the built-in holiday table: %s', exc)
        _xhkg_unavailable = True
        return None
    return _xhkg


def is_full_closure(day: date | datetime) -> bool:
    return _as_date(day) in _FULL_CLOSURES


def is_non_settlement_half_day(day: date | datetime) -> bool:
    return _as_date(day) in _NON_SETTLEMENT_HALF_DAYS


def is_trading_day(day: date | datetime) -> bool:
    """Weekday the securities market opens, including half-day sessions."""
    current = _as_date(day)
    if current.year in _BUILTIN_YEARS:
        return current.weekday() < 5 and current not in _FULL_CLOSURES
    calendar = _xhkg_calendar()
    if calendar is not None:
        try:
            return bool(calendar.is_session(current))
        except Exception:
            pass
    if current.year not in _weekday_fallback_years:
        _weekday_fallback_years.add(current.year)
        logger.warning('No HKEX holiday table for %s; treating weekdays as trading days', current.year)
    return current.weekday() < 5 and current not in _FULL_CLOSURES


def is_settlement_day(day: date | datetime) -> bool:
    """Day on which Exchange trades settle. Half-days do not."""
    current = _as_date(day)
    if not is_trading_day(current) or current in _NON_SETTLEMENT_HALF_DAYS:
        return False
    if current.year in _BUILTIN_YEARS:
        return True
    calendar = _xhkg_calendar()
    if calendar is not None:
        try:
            import pandas as pd
            if pd.Timestamp(current) in calendar.early_closes:
                return False
        except Exception:
            pass
    return True


def is_shareholding_query_day(day: date | datetime) -> bool:
    """Date HKEX will return as its own shareholding date.

    Full closures are rewritten to the previous book. Half-days have no
    settlement run, so they are not queried either.
    """
    return is_settlement_day(day)


def previous_settlement_day(day: date | datetime, n: int = 1) -> date:
    """The n-th settlement day strictly before ``day``."""
    if n < 1:
        raise ValueError('n must be at least 1')
    current = _as_date(day) - timedelta(days=1)
    seen = 0
    for _ in range(400):
        if is_settlement_day(current):
            seen += 1
            if seen == n:
                return current
        current -= timedelta(days=1)
    raise RuntimeError(f'no settlement day {n} before {day}')


def settlement_days_between(start: date | datetime, end: date | datetime) -> list[date]:
    """Settlement days from ``start`` through ``end``, inclusive."""
    cursor = _as_date(start)
    last = _as_date(end)
    if cursor > last:
        return []
    found = []
    while cursor <= last:
        if is_settlement_day(cursor):
            found.append(cursor)
        cursor += timedelta(days=1)
    return found
