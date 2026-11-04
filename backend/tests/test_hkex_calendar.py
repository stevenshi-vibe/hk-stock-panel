"""HKEX settlement calendar and the CCASS shareholding-date mapping.

These tests never call HKEX. They pin the 2026 holiday schedule: National Day,
the day following Chung Yeung, Lunar New Year, and the half-day non-settlement
sessions.
"""
import os
import sys
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from scrapers.ccass_scraper import (  # noqa: E402
    compute_query_to_data_date,
    find_latest_query_date,
    shareholding_page_matches,
)
from services.ccass_range import plan_backfill_query_dates  # noqa: E402
from services.hkex_calendar import (  # noqa: E402
    is_settlement_day,
    is_shareholding_query_day,
    is_trading_day,
)


class SettlementCalendarTests(unittest.TestCase):
    def test_national_day_is_closed_and_maps_to_the_settlement_day(self):
        self.assertFalse(is_trading_day(date(2026, 10, 1)))
        self.assertFalse(is_settlement_day(date(2026, 10, 1)))
        # Shareholding date 5 Oct is the settlement of trades on 30 Sep.
        # The old weekday count stored that book on the holiday itself.
        self.assertEqual(compute_query_to_data_date('2026/10/05'), '2026-09-30')
        self.assertNotEqual(compute_query_to_data_date('2026/10/05'), '2026-10-01')
        self.assertEqual(compute_query_to_data_date('2026/10/02'), '2026-09-29')

    def test_chung_yeung_book_is_not_stored_on_the_holiday(self):
        self.assertFalse(is_trading_day(date(2026, 10, 19)))
        # 21 Oct settles trades from 16 Oct. It must not be labeled 19 Oct.
        self.assertEqual(compute_query_to_data_date('2026/10/21'), '2026-10-16')
        self.assertNotEqual(compute_query_to_data_date('2026/10/21'), '2026-10-19')
        self.assertEqual(compute_query_to_data_date('2026/10/20'), '2026-10-15')
        self.assertEqual(compute_query_to_data_date('2026/10/22'), '2026-10-20')

    def test_lunar_new_year_skips_the_closure_and_the_half_day(self):
        for closed in (date(2026, 2, 17), date(2026, 2, 18), date(2026, 2, 19)):
            self.assertFalse(is_trading_day(closed))
            self.assertFalse(is_shareholding_query_day(closed))
        # 16 Feb is a morning session and not a settlement day.
        self.assertTrue(is_trading_day(date(2026, 2, 16)))
        self.assertFalse(is_settlement_day(date(2026, 2, 16)))
        self.assertFalse(is_shareholding_query_day(date(2026, 2, 16)))
        # 20 Feb settles trades from 12 Feb: 13 Feb is T+1, then the half-day
        # and the three-day closure are not settlement days.
        self.assertEqual(compute_query_to_data_date('2026/02/20'), '2026-02-12')
        self.assertEqual(compute_query_to_data_date('2026/02/23'), '2026-02-13')
        self.assertEqual(compute_query_to_data_date('2026/02/24'), '2026-02-20')

    def test_year_end_half_days_are_not_settlement_days(self):
        for half in (date(2026, 12, 24), date(2026, 12, 31)):
            self.assertTrue(is_trading_day(half))
            self.assertFalse(is_settlement_day(half))
            self.assertFalse(is_shareholding_query_day(half))
        # 28 Dec is the second settlement day before 30 Dec. Christmas and
        # both half-days are skipped.
        self.assertEqual(compute_query_to_data_date('2026/12/30'), '2026-12-28')

    def test_latest_query_walks_back_over_the_holiday_and_the_half_day(self):
        self.assertEqual(find_latest_query_date(datetime(2026, 10, 20, 9, 0)), '2026/10/16')
        self.assertEqual(find_latest_query_date(datetime(2026, 2, 17, 9, 0)), '2026/02/13')
        self.assertEqual(find_latest_query_date(datetime(2026, 2, 20, 9, 0)), '2026/02/13')

    def test_backfill_plan_does_not_request_closures_or_half_days(self):
        planned = plan_backfill_query_dates(
            set(), datetime(2026, 2, 24), 30, compute_query_to_data_date,
        )
        self.assertNotIn('2026/02/16', planned)
        self.assertNotIn('2026/02/17', planned)
        self.assertNotIn('2026/02/18', planned)
        self.assertNotIn('2026/02/19', planned)
        self.assertIn('2026/02/13', planned)
        self.assertIn('2026/02/20', planned)

    def test_rewritten_shareholding_page_is_rejected(self):
        # Selecting Chung Yeung Monday makes HKEX show the previous book.
        self.assertFalse(shareholding_page_matches('2026/10/19', '2026/10/17', '00700', '00700'))
        self.assertFalse(shareholding_page_matches('2026/10/01', '2026/09/30', '00700', '00700'))
        self.assertTrue(shareholding_page_matches('2026/10/21', '2026/10/21', '00700', '00700'))
        self.assertFalse(shareholding_page_matches('2026/10/21', '2026/10/21', '00700', '00000'))

    def test_builtin_2026_matches_xhkg_when_installed(self):
        try:
            import exchange_calendars as xcals
            import pandas as pd
        except ImportError:
            self.skipTest('exchange_calendars is not installed')
        calendar = xcals.get_calendar('XHKG')
        start, end = date(2026, 1, 1), date(2026, 12, 31)
        sessions = {day.date() for day in calendar.sessions_in_range(start, end)}
        early = {day.date() for day in calendar.early_closes if day.year == 2026}
        cursor = start
        while cursor <= end:
            if cursor.weekday() < 5:
                self.assertEqual(is_trading_day(cursor), cursor in sessions, cursor)
                self.assertEqual(is_settlement_day(cursor), cursor in sessions and cursor not in early, cursor)
            cursor += pd.Timedelta(days=1)


if __name__ == '__main__':
    unittest.main()
