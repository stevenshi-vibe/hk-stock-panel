"""口径对齐和覆盖率。输入是合成持仓，不是真实抓取。"""
import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.southbound.align import align_to_trading_day
from services.southbound.analyze import abnormal_days, concentration, cross_check, quarterly_attribution
from services.southbound.decompose import decompose
from services.southbound.models import CONFIRMED, NON_CONNECT, DailyPoint, Holding, SeatPoint


def fund(key, name, report_date, shares, *, tag=CONFIRMED, counts=True, scope="top10"):
    return Holding(
        holder_key=key,
        holder_name=name,
        holder_type="public_fund",
        channel_tag=tag,
        report_date=report_date,
        shares=shares,
        raw_shares=shares / 10000,
        raw_unit="wan_shares",
        source="合成",
        provider_id="t",
        confidence=0.9,
        counts_toward_southbound=counts,
        unit_status="ok",
        report_scope=scope,
    )


def disclosure(name, report_date, shares):
    return Holding(
        holder_key="di:" + name,
        holder_name=name,
        holder_type="disclosure",
        channel_tag=NON_CONNECT,
        report_date=report_date,
        shares=shares,
        raw_shares=float(shares),
        raw_unit="shares",
        source="合成权益披露",
        provider_id="t",
        confidence=0.9,
        counts_toward_southbound=False,
        position_type="L",
        source_ref="CS-SYN",
        unit_status="unchecked",
    )


class AlignTests(unittest.TestCase):
    def test_exact_previous_and_stale(self):
        days = ["2026-09-29", "2026-09-30", "2026-10-09"]
        self.assertEqual(align_to_trading_day("2026-09-30", days)["quality"], "exact")
        weekend = align_to_trading_day("2026-10-03", days)
        self.assertEqual(weekend["aligned_date"], "2026-09-30")
        self.assertEqual(weekend["quality"], "ok")
        stale = align_to_trading_day("2026-10-20", ["2026-09-30"])
        self.assertEqual(stale["quality"], "stale")
        self.assertEqual(stale["gap_days"], 20)


class CoverageTests(unittest.TestCase):
    def test_only_confirmed_same_report_counts(self):
        daily = [
            DailyPoint("2026-06-30", 800_000, provider_id="t"),
            DailyPoint("2026-09-30", 1_000_000, provider_id="t"),
            DailyPoint("2026-11-24", 1_100_000, provider_id="t"),
        ]
        holdings = [
            fund("fund:1", "合成港股通甲", "2026-09-30", 100_000),
            fund("fund:1", "合成港股通甲", "2026-06-30", 80_000),
            fund("fund:2", "合成沪港深乙", "2026-09-30", 400_000, tag="possible_connect", counts=False),
            fund("fund:3", "合成QDII丙", "2026-09-30", 50_000, tag=NON_CONNECT, counts=False),
            disclosure("合成外资样本公司", "2026-09-30", 300_000),
        ]
        view = decompose(daily, holdings, end="2026-11-24")
        snap = view["snapshot"]
        self.assertEqual(snap["report_date"], "2026-09-30")
        self.assertEqual(snap["aligned_southbound_date"], "2026-09-30")
        self.assertEqual(snap["alignment_quality"], "exact")
        self.assertEqual(snap["explained_shares"], 100_000)
        self.assertEqual(snap["unidentified_shares"], 900_000)
        self.assertEqual(snap["possible_shares_same_report"], 400_000)
        self.assertAlmostEqual(snap["coverage"], 0.1)
        self.assertEqual([row["holder_key"] for row in view["holders"]["confirmed"]], ["fund:1"])
        self.assertEqual(view["holders"]["confirmed"][0]["shares_change"], 20_000)
        self.assertTrue(view["holders"]["non_connect_funds"])
        self.assertTrue(view["holders"]["disclosures"])
        self.assertTrue(all(not row["counts_toward_southbound"] for row in view["holders"]["disclosures"]))
        self.assertTrue(snap["misaligned_coverage"]["warning"])

    def test_over_explained_and_stale_fund(self):
        daily = [
            DailyPoint("2026-06-30", 1_000, provider_id="t"),
            DailyPoint("2026-09-30", 100, provider_id="t"),
        ]
        view = decompose(daily, [
            fund("fund:new", "合成港股通甲", "2026-09-30", 150),
            fund("fund:old", "合成港股通乙", "2026-06-30", 500),
        ])
        self.assertTrue(view["snapshot"]["over_explained"])
        self.assertEqual(view["snapshot"]["explained_shares"], 150)
        self.assertEqual(view["snapshot"]["unidentified_shares"], 0)
        self.assertEqual(view["snapshot"]["excess_shares"], 50)
        self.assertEqual(view["holders"]["stale_confirmed"][0]["holder_key"], "fund:old")


class AttributionTests(unittest.TestCase):
    def test_top10_gap_is_not_zero(self):
        daily = [
            DailyPoint("2026-06-30", 1000, provider_id="t"),
            DailyPoint("2026-09-30", 1100, provider_id="t"),
        ]
        pair = quarterly_attribution(daily, [
            fund("fund:other", "合成乙", "2026-06-30", 10),
            fund("fund:1", "合成甲", "2026-09-30", 80),
        ])[0]
        newcomer = next(item for item in pair["holders"] if item["holder_key"] == "fund:1")
        self.assertIsNone(newcomer["shares_change"])
        self.assertEqual(newcomer["quality"], "top10_gap")
        self.assertEqual(pair["confirmed_known_change"], 0)
        self.assertEqual(pair["southbound_change"], 100)

    def test_interim_date_is_not_a_quarter(self):
        daily = [
            DailyPoint("2026-06-30", 1000, provider_id="t"),
            DailyPoint("2026-09-30", 900, provider_id="t"),
        ]
        rows = quarterly_attribution(daily, [
            fund("fund:1", "合成甲", "2026-06-30", 100),
            fund("fund:1", "合成甲", "2026-08-11", 90),
            fund("fund:1", "合成甲", "2026-09-30", 80),
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["report_start"], "2026-06-30")
        self.assertEqual(rows[0]["report_end"], "2026-09-30")
        self.assertEqual(rows[0]["holders"][0]["shares_change"], -20)


class AnalysisTests(unittest.TestCase):
    def test_abnormal_day_flags_a_spike(self):
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
                provider_id="t",
            ))
        flags = abnormal_days(series, window=40, z_min=2.5)
        self.assertTrue(flags)
        self.assertEqual(flags[0]["trade_date"], series[-1].trade_date)
        self.assertEqual(flags[0]["direction"], "流入")
        self.assertGreaterEqual(abs(flags[0]["z"]), 2.5)

    def test_cross_check_and_concentration(self):
        seats = [
            SeatPoint("2026-09-30", "A00003", "合成沪", 400_000, source="合成"),
            SeatPoint("2026-09-30", "A00004", "合成深", 600_000, source="合成"),
        ]
        result = cross_check(
            trade_date="2026-09-30",
            daily_shares=1_000_000,
            seats=seats,
            official_shares=1_000_000,
        )
        self.assertTrue(result["match_daily"])
        self.assertTrue(result["match_official"])
        self.assertEqual(result["diff_vs_daily"], 0)
        conc = concentration([
            {"holder_name": "甲", "shares": 75},
            {"holder_name": "乙", "shares": 25},
        ])
        self.assertAlmostEqual(conc["hhi"], 0.75 ** 2 + 0.25 ** 2)
        self.assertEqual(conc["top1_name"], "甲")
        self.assertIn("不代表全部港股通", conc["note"])


if __name__ == "__main__":
    unittest.main()
