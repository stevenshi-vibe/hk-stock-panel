"""解析真实样例：万股、权益披露好仓/淡仓、官方合计与东财百分比。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.southbound.classify import classify_disclosure, classify_fund
from services.southbound.models import CONFIRMED, NON_CONNECT, POSSIBLE
from services.southbound.parse import (
    load_focus_catalog,
    mutualmarket_code,
    parse_akshare_daily_csv,
    parse_di_roster_csv,
    parse_di_summary_html,
    parse_eastmoney_daily_row,
    parse_eastmoney_fund_html,
    parse_eastmoney_seat_row,
    parse_fund_hits_csv,
    parse_licensed_csv,
    parse_mutualmarket_html,
    parse_position_tokens,
)
from services.southbound.units import assess_unit, wan_to_shares

FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "southbound")


class UnitTests(unittest.TestCase):
    def test_wan_shares_from_real_meituan_line(self):
        # 港股通互联网ETF富国 2026Q3 美团：5,018.57 万股。
        self.assertEqual(wan_to_shares(5018.57), 50_185_700)

    def test_declared_wan_matches_market_value(self):
        # 市值 308,892.98 万元 / 5,018.57 万股 ≈ 61.55 元，单位声明正确。
        self.assertEqual(assess_unit(5018.57, "wan_shares", 308892.98), "ok")

    def test_forgetting_wan_conversion_is_rejected(self):
        # 若把 5018.57 当成股，隐含价格会到几十万，必须剔除。
        self.assertEqual(assess_unit(5018.57, "shares", 308892.98), "mismatch")


class PositionTests(unittest.TestCase):
    def test_balance_does_not_swallow_next_lot(self):
        tokens = parse_position_tokens("336,749,593(L) 1,024,500(S)")
        self.assertEqual(
            [(item["position"], item["shares"]) for item in tokens],
            [("L", 336_749_593), ("S", 1_024_500)],
        )
        self.assertIsNone(tokens[0]["pct"])

    def test_balance_keeps_percent_between_lots(self):
        tokens = parse_position_tokens("326,833,563(L) 5.84 642,000(S) 0.01")
        self.assertEqual(tokens[0]["pct"], 5.84)
        self.assertEqual(tokens[1]["shares"], 642_000)
        self.assertEqual(tokens[1]["pct"], 0.01)


class FundParseTests(unittest.TestCase):
    def test_live_archive_html_for_159792(self):
        text = open(os.path.join(FIX, "fund_159792_2026.js"), encoding="utf-8").read()
        rows = parse_eastmoney_fund_html(
            text,
            fund_code="159792",
            fund_name="港股通互联网ETF富国",
            fund_type="指数型-股票",
            stock_code="03690",
        )
        q3 = next(row for row in rows if row.report_date == "2026-09-30")
        self.assertEqual(q3.shares, 50_185_700)
        self.assertEqual(q3.raw_unit, "wan_shares")
        self.assertEqual(q3.channel_tag, CONFIRMED)
        self.assertTrue(q3.counts_toward_southbound)
        self.assertEqual(q3.unit_status, "ok")
        self.assertEqual(q3.report_scope, "top10")
        self.assertAlmostEqual(q3.pct_nav, 9.32)

    def test_hits_csv_classifies_qdii_and_huhangshen(self):
        catalog = load_focus_catalog(os.path.join(FIX, "focus_funds.csv"))
        rows = [
            row for row in parse_fund_hits_csv(os.path.join(FIX, "fund_holdings_hits_01810_03690.csv"), catalog)
            if row.report_date == "2026-09-30" and row.holder_key.endswith("03690") or (
                row.report_date == "2026-09-30" and "03690" in row.notes
            )
        ]
        by_key = {}
        for row in parse_fund_hits_csv(os.path.join(FIX, "fund_holdings_hits_01810_03690.csv"), catalog):
            if row.report_date == "2026-09-30" and "03690" in (row.notes or ""):
                by_key[row.holder_key] = row
        self.assertEqual(by_key["fund:159792"].channel_tag, CONFIRMED)
        self.assertEqual(by_key["fund:513180"].channel_tag, NON_CONNECT)
        self.assertFalse(by_key["fund:513180"].counts_toward_southbound)
        q4 = [
            row for row in parse_fund_hits_csv(os.path.join(FIX, "fund_holdings_hits_01810_03690.csv"), catalog)
            if row.holder_key == "fund:000041" and row.report_date == "2025-12-31" and "03690" in row.notes
        ]
        self.assertEqual(q4[0].channel_tag, NON_CONNECT)


class DisclosureParseTests(unittest.TestCase):
    def test_daily_summary_row_splits_long_and_short(self):
        html = open(os.path.join(FIX, "di_dsm_03690_row.html"), encoding="utf-8").read()
        rows = parse_di_summary_html(html, stock_code="03690", summary_date="2026-11-17")
        by_pos = {row.position_type: row for row in rows}
        self.assertEqual(by_pos["L"].shares, 326_833_563)
        self.assertEqual(by_pos["S"].shares, 642_000)
        self.assertEqual(by_pos["L"].report_date, "2026-11-12")
        self.assertEqual(by_pos["L"].channel_tag, NON_CONNECT)
        self.assertFalse(by_pos["L"].counts_toward_southbound)
        self.assertEqual(by_pos["L"].source_ref, "CS20261117E00012")

    def test_roster_uses_consolidated_list(self):
        rows = parse_di_roster_csv(os.path.join(FIX, "di_substantial_shareholders.csv"), "03690")
        blackrock = [row for row in rows if "BlackRock" in row.holder_name and row.position_type == "L"]
        self.assertEqual(len(blackrock), 1)
        self.assertEqual(blackrock[0].shares, 336_749_593)
        self.assertAlmostEqual(blackrock[0].pct_issued, 6.02)
        self.assertEqual(blackrock[0].report_date, "2026-11-18")
        self.assertTrue(all(row.channel_tag == NON_CONNECT for row in rows))
        self.assertTrue(all(not row.counts_toward_southbound for row in rows))


class DailyParseTests(unittest.TestCase):
    def test_eastmoney_uses_hold_ratio_not_total_ratio(self):
        import json
        row = json.load(open(os.path.join(FIX, "eastmoney_daily_03690_20261124.json"), encoding="utf-8"))
        point = parse_eastmoney_daily_row(row)
        self.assertEqual(point.trade_date, "2026-11-24")
        self.assertEqual(point.shares, 1_200_236_536)
        self.assertEqual(point.pct_of_issued, 19.43)
        self.assertNotEqual(point.pct_of_issued, row["TOTAL_SHARES_RATIO"])

    def test_akshare_csv_last_row_matches_official(self):
        points = parse_akshare_daily_csv(os.path.join(FIX, "hsgt_individual_03690.csv"))
        last = points[-1]
        self.assertEqual(last.trade_date, "2026-11-24")
        self.assertEqual(last.shares, 1_200_236_536)
        self.assertEqual(last.pct_of_issued, 19.43)
        self.assertEqual(points[1].share_change, points[1].shares - points[0].shares)

    def test_mutualmarket_snippet(self):
        html = open(os.path.join(FIX, "mutualmarket_03690_snippet.html"), encoding="utf-8").read()
        points = parse_mutualmarket_html(html)
        self.assertEqual(len(points), 1)
        self.assertEqual(mutualmarket_code(points[0]), "03690")
        self.assertEqual(points[0].shares, 1_200_236_536)
        self.assertEqual(points[0].pct_of_issued, 19.43)
        self.assertEqual(points[0].trade_date, "2026-11-24")

    def test_seat_json_sums_to_total(self):
        import json
        rows = json.load(open(os.path.join(FIX, "eastmoney_seats_03690_20261124.json"), encoding="utf-8"))
        seats = [parse_eastmoney_seat_row(row) for row in rows]
        self.assertEqual(sum(seat.shares for seat in seats), 1_200_236_536)
        self.assertEqual({seat.participant_id for seat in seats}, {"A00003", "A00004"})


class ClassifyTests(unittest.TestCase):
    def test_channels(self):
        self.assertEqual(classify_fund("港股通互联网ETF富国", "指数型-股票")[0], CONFIRMED)
        self.assertEqual(classify_fund("华夏全球股票(QDII)(人民币)", "QDII-普通股票")[0], NON_CONNECT)
        self.assertEqual(classify_fund("恒生科技ETF华夏", "指数型-海外股票")[0], NON_CONNECT)
        self.assertEqual(classify_fund("前海开源沪港深蓝筹精选混合A", "混合型")[0], POSSIBLE)
        self.assertEqual(classify_disclosure("BlackRock, Inc.")[0], NON_CONNECT)
        self.assertEqual(classify_disclosure("中国人寿保险股份有限公司")[0], POSSIBLE)


class LicensedCsvTests(unittest.TestCase):
    def test_filters_stock_and_keeps_shares(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as handle:
            handle.write("trade_date,stock_code,shares,pct_of_issued\n")
            handle.write("2026-11-24,03690,1200236536,19.43\n")
            handle.write("2026-11-24,01810,4761661531,18.49\n")
            path = handle.name
        points = parse_licensed_csv(path, "03690")
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0].shares, 1_200_236_536)
        self.assertEqual(points[0].provider_id, "hkex_licensed_csv")


if __name__ == "__main__":
    unittest.main()
