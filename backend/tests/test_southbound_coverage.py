"""口径对齐和覆盖率。分子只用同一报告期、确认经港股通、单位通过的持仓。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.southbound.align import align_to_trading_day
from services.southbound.analyze import abnormal_days, concentration, cross_check, quarterly_attribution
from services.southbound.decompose import decompose
from services.southbound.models import CONFIRMED, DailyPoint, Holding, SeatPoint
from services.southbound.parse import (
    load_focus_catalog,
    parse_akshare_daily_csv,
    parse_di_roster_csv,
    parse_eastmoney_seat_row,
    parse_fund_hits_csv,
    parse_official_snapshot_csv,
)

FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "southbound")


def _load_meituan():
    catalog = load_focus_catalog(os.path.join(FIX, "focus_funds.csv"))
    daily = parse_akshare_daily_csv(os.path.join(FIX, "hsgt_individual_03690.csv"))
    funds = [
        row for row in parse_fund_hits_csv(os.path.join(FIX, "fund_holdings_hits_01810_03690.csv"), catalog)
        if "03690" in (row.notes or "")
    ]
    roster = parse_di_roster_csv(os.path.join(FIX, "di_substantial_shareholders.csv"), "03690")
    return daily, funds + roster


class AlignTests(unittest.TestCase):
    def test_exact_previous_and_stale(self):
        days = ["2026-09-29", "2026-09-30", "2026-10-09"]
        exact = align_to_trading_day("2026-09-30", days)
        self.assertEqual(exact["aligned_date"], "2026-09-30")
        self.assertEqual(exact["quality"], "exact")
        weekend = align_to_trading_day("2026-10-03", days)
        self.assertEqual(weekend["aligned_date"], "2026-09-30")
        self.assertEqual(weekend["quality"], "ok")
        stale = align_to_trading_day("2026-10-20", ["2026-09-30"])
        self.assertEqual(stale["aligned_date"], "2026-09-30")
        self.assertEqual(stale["quality"], "stale")
        self.assertEqual(stale["gap_days"], 20)


class CoverageTests(unittest.TestCase):
    def test_real_meituan_snapshot_excludes_qdii_and_blackrock(self):
        daily, holdings = _load_meituan()
        view = decompose(daily, holdings, end="2026-11-24")
        snap = view["snapshot"]
        self.assertEqual(snap["report_date"], "2026-09-30")
        self.assertEqual(snap["aligned_southbound_date"], "2026-09-30")
        self.assertEqual(snap["alignment_quality"], "exact")
        confirmed = view["holders"]["confirmed"]
        self.assertEqual([row["holder_key"] for row in confirmed], ["fund:159792"])
        self.assertEqual(confirmed[0]["shares"], 50_185_700)
        names = {row["holder_name"] for row in view["holders"]["non_connect_funds"]}
        self.assertIn("恒生科技ETF华夏", names)
        self.assertTrue(any("QDII" in row["holder_name"] for row in view["holders"]["non_connect_funds"]))
        self.assertTrue(view["holders"]["disclosures"])
        self.assertTrue(all(not row["counts_toward_southbound"] for row in view["holders"]["disclosures"]))
        self.assertNotIn("BlackRock, Inc.", {row["holder_name"] for row in confirmed})

        aligned = next(point for point in daily if point.trade_date == "2026-09-30")
        self.assertEqual(snap["southbound_shares"], aligned.shares)
        self.assertEqual(snap["explained_shares"], 50_185_700)
        self.assertEqual(snap["unidentified_shares"], aligned.shares - 50_185_700)
        self.assertAlmostEqual(snap["coverage"], 50_185_700 / aligned.shares)
        self.assertFalse(snap["over_explained"])
        # 最新南下持股日晚于基金报告期，错位覆盖率必须单独标成不可比。
        self.assertEqual(snap["latest_southbound_date"], "2026-11-24")
        self.assertTrue(snap["misaligned_coverage"]["warning"])
        self.assertGreater(snap["staleness_days"], 30)

    def test_possible_is_not_added_into_explained(self):
        daily = [DailyPoint("2026-09-30", 1_000_000, provider_id="t")]
        confirmed = Holding(
            holder_key="fund:1", holder_name="港股通样本", holder_type="public_fund",
            channel_tag=CONFIRMED, report_date="2026-09-30", shares=100_000,
            raw_shares=10, raw_unit="wan_shares", source="t", provider_id="t",
            confidence=0.9, counts_toward_southbound=True, unit_status="ok", position_type="L",
        )
        possible = Holding(
            holder_key="fund:2", holder_name="沪港深样本", holder_type="public_fund",
            channel_tag="possible_connect", report_date="2026-09-30", shares=400_000,
            raw_shares=40, raw_unit="wan_shares", source="t", provider_id="t",
            confidence=0.5, counts_toward_southbound=False, unit_status="ok", position_type="L",
        )
        view = decompose(daily, [confirmed, possible])
        self.assertEqual(view["snapshot"]["explained_shares"], 100_000)
        self.assertEqual(view["snapshot"]["unidentified_shares"], 900_000)
        self.assertEqual(view["snapshot"]["possible_shares_same_report"], 400_000)
        self.assertAlmostEqual(view["snapshot"]["coverage"], 0.1)

    def test_over_explained_is_flagged(self):
        daily = [DailyPoint("2026-09-30", 100, provider_id="t")]
        holding = Holding(
            holder_key="fund:1", holder_name="港股通样本", holder_type="public_fund",
            channel_tag=CONFIRMED, report_date="2026-09-30", shares=150,
            raw_shares=1, raw_unit="shares", source="t", provider_id="t",
            confidence=0.9, counts_toward_southbound=True, unit_status="ok", position_type="L",
        )
        snap = decompose(daily, [holding])["snapshot"]
        self.assertTrue(snap["over_explained"])
        self.assertEqual(snap["unidentified_shares"], 0)
        self.assertEqual(snap["excess_shares"], 50)

    def test_older_report_does_not_fill_the_snapshot(self):
        daily = [
            DailyPoint("2026-06-30", 1_000, provider_id="t"),
            DailyPoint("2026-09-30", 1_000, provider_id="t"),
        ]
        newer = Holding(
            holder_key="fund:new", holder_name="港股通甲", holder_type="public_fund",
            channel_tag=CONFIRMED, report_date="2026-09-30", shares=100,
            raw_shares=1, raw_unit="shares", source="t", provider_id="t",
            confidence=0.9, counts_toward_southbound=True, unit_status="ok",
        )
        older = Holding(
            holder_key="fund:old", holder_name="港股通乙", holder_type="public_fund",
            channel_tag=CONFIRMED, report_date="2026-06-30", shares=500,
            raw_shares=1, raw_unit="shares", source="t", provider_id="t",
            confidence=0.9, counts_toward_southbound=True, unit_status="ok",
        )
        view = decompose(daily, [newer, older])
        self.assertEqual(view["snapshot"]["explained_shares"], 100)
        self.assertEqual(view["holders"]["stale_confirmed"][0]["holder_key"], "fund:old")


class AttributionTests(unittest.TestCase):
    def test_real_quarter_change_does_not_invent_zero(self):
        daily, holdings = _load_meituan()
        rows = quarterly_attribution(daily, holdings)
        pair = next(row for row in rows if row["report_start"] == "2026-06-30" and row["report_end"] == "2026-09-30")
        member = pair["holders"][0]
        self.assertEqual(member["holder_key"], "fund:159792")
        self.assertEqual(member["shares_start"], 72_513_200)
        self.assertEqual(member["shares_end"], 50_185_700)
        self.assertEqual(member["shares_change"], -22_327_500)
        self.assertEqual(member["quality"], "known")
        start = next(point.shares for point in daily if point.trade_date == pair["southbound_date_start"])
        end = next(point.shares for point in daily if point.trade_date == pair["southbound_date_end"])
        self.assertEqual(pair["southbound_change"], end - start)
        self.assertEqual(pair["confirmed_known_change"], -22_327_500)

    def test_interim_as_of_does_not_become_a_quarter(self):
        daily = [
            DailyPoint("2026-06-30", 1000, provider_id="t"),
            DailyPoint("2026-09-30", 900, provider_id="t"),
        ]
        q2 = Holding(
            holder_key="fund:1", holder_name="港股通甲", holder_type="public_fund",
            channel_tag=CONFIRMED, report_date="2026-06-30", shares=100,
            raw_shares=1, raw_unit="shares", source="t", provider_id="t",
            confidence=0.9, counts_toward_southbound=True, unit_status="ok",
        )
        interim = Holding(
            holder_key="fund:1", holder_name="港股通甲", holder_type="public_fund",
            channel_tag=CONFIRMED, report_date="2026-08-11", shares=90,
            raw_shares=1, raw_unit="shares", source="t", provider_id="t",
            confidence=0.9, counts_toward_southbound=True, unit_status="ok",
        )
        q3 = Holding(
            holder_key="fund:1", holder_name="港股通甲", holder_type="public_fund",
            channel_tag=CONFIRMED, report_date="2026-09-30", shares=80,
            raw_shares=1, raw_unit="shares", source="t", provider_id="t",
            confidence=0.9, counts_toward_southbound=True, unit_status="ok",
        )
        rows = quarterly_attribution(daily, [q2, interim, q3])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["report_start"], "2026-06-30")
        self.assertEqual(rows[0]["report_end"], "2026-09-30")
        self.assertEqual(rows[0]["holders"][0]["shares_change"], -20)

    def test_top10_gap_is_not_zero(self):
        daily = [
            DailyPoint("2026-06-30", 1000, provider_id="t"),
            DailyPoint("2026-09-30", 1100, provider_id="t"),
        ]
        earlier = Holding(
            holder_key="fund:other", holder_name="港股通乙", holder_type="public_fund",
            channel_tag=CONFIRMED, report_date="2026-06-30", shares=10,
            raw_shares=1, raw_unit="shares", source="t", provider_id="t",
            confidence=0.9, counts_toward_southbound=True, unit_status="ok",
            report_scope="top10",
        )
        only_end = Holding(
            holder_key="fund:1", holder_name="港股通甲", holder_type="public_fund",
            channel_tag=CONFIRMED, report_date="2026-09-30", shares=80,
            raw_shares=1, raw_unit="shares", source="t", provider_id="t",
            confidence=0.9, counts_toward_southbound=True, unit_status="ok",
            report_scope="top10",
        )
        pair = quarterly_attribution(daily, [earlier, only_end])[0]
        newcomer = next(item for item in pair["holders"] if item["holder_key"] == "fund:1")
        self.assertIsNone(newcomer["shares_change"])
        self.assertEqual(newcomer["quality"], "top10_gap")
        self.assertEqual(pair["unknown_funds"], 2)
        self.assertEqual(pair["confirmed_known_change"], 0)


class AnalysisTests(unittest.TestCase):
    def test_abnormal_day_on_a_spike(self):
        from datetime import date, timedelta
        series = []
        shares = 1_000_000
        start = date(2026, 1, 1)
        for index in range(45):
            if index == 0:
                change = None
            elif index == 44:
                change = 200_000
            else:
                change = 1_000 if index % 2 == 0 else -800
            if change:
                shares += change
            series.append(DailyPoint(
                (start + timedelta(days=index)).isoformat(),
                shares,
                share_change=change,
                change_pct=0.1,
                provider_id="t",
            ))
        flags = abnormal_days(series, window=40, z_min=2.5)
        self.assertTrue(flags)
        self.assertEqual(flags[0]["trade_date"], series[-1].trade_date)
        self.assertEqual(flags[0]["direction"], "流入")

    def test_real_series_events_exist_in_the_file(self):
        daily = parse_akshare_daily_csv(os.path.join(FIX, "hsgt_individual_03690.csv"))
        flags = abnormal_days(daily)
        dates = {point.trade_date for point in daily}
        for flag in flags:
            self.assertIn(flag["trade_date"], dates)
            self.assertGreaterEqual(abs(flag["z"]), 2.5)

    def test_cross_check_real_seats(self):
        import json
        seats = [
            parse_eastmoney_seat_row(row)
            for row in json.load(open(os.path.join(FIX, "eastmoney_seats_03690_20261124.json"), encoding="utf-8"))
        ]
        official = parse_official_snapshot_csv(os.path.join(FIX, "hkex_official_southbound_01810_03690_20261124.csv"))
        official_shares = next(point.shares for point in official if "03690" in point.source)
        result = cross_check(
            trade_date="2026-11-24",
            daily_shares=1_200_236_536,
            seats=seats,
            official_shares=official_shares,
        )
        self.assertTrue(result["match_daily"])
        self.assertTrue(result["match_official"])
        self.assertEqual(result["diff_vs_daily"], 0)
        self.assertEqual(result["a00003_shares"] + result["a00004_shares"], 1_200_236_536)

    def test_concentration_is_within_explained_only(self):
        rows = [
            {"holder_name": "甲", "shares": 75},
            {"holder_name": "乙", "shares": 25},
        ]
        result = concentration(rows)
        self.assertTrue(result["available"])
        self.assertAlmostEqual(result["hhi"], 0.75 ** 2 + 0.25 ** 2)
        self.assertEqual(result["top1_name"], "甲")
        self.assertIn("不代表全部港股通", result["note"])


if __name__ == "__main__":
    unittest.main()
