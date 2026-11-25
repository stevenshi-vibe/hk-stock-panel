"""解析测试。夹具是合成的：虚构代码、虚构名称、虚构数字，不是上游抓取结果。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.southbound.classify import classify_disclosure, classify_fund
from services.southbound.models import CONFIRMED, NON_CONNECT, POSSIBLE
from services.southbound.parse import (
    mutualmarket_code,
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

# 结构对齐天天基金档案页，内容全部虚构。
SYNTHETIC_FUND_JS = (
    'var apidata={ content:"<div class=\'box\'><h4 class=\'t\'>合成港股通样本ETF 2026年3季度股票投资明细</h4>'
    '<font class=\'px12\'>2026-09-30</font><table><thead><tr>'
    '<th>序号</th><th>股票代码</th><th>股票名称</th><th>占净值比例</th>'
    '<th>持股数（万股）</th><th>持仓市值（万元）</th></tr></thead><tbody><tr>'
    '<td>1</td><td>99001</td><td>合成港股甲</td><td>12.50%</td>'
    '<td>1,234.50</td><td>61,725.00</td></tr></tbody></table></div>",arryear:[2026],curyear:2026};'
)

SYNTHETIC_DI_HTML = """
<table><tr>
<td>CS20260101E00001</td>
<td>合成公司</td>
<td>99001</td>
<td>Ordinary Shares</td>
<td>合成外资样本公司</td>
<td>01/01/2026</td>
<td>1205(L)</td>
<td>1,000(L)</td>
<td></td>
<td>旧余额</td>
<td><table><tr><td>200,000(L)</td><td>5.50</td></tr><tr><td>1,000(S)</td><td>0.10</td></tr></table></td>
</tr></table>
"""

SYNTHETIC_MUTUAL = """
<h2><span>Shareholding Date: 2026/09/30</span></h2>
<table><tr>
<td class="col-stock-code"><div class="mobile-list-heading">Stock Code:</div><div class="mobile-list-body">99001</div></td>
<td class="col-stock-name"><div class="mobile-list-body">SYNTHETIC CO</div></td>
<td class="col-shareholding"><div class="mobile-list-heading">Shareholding in CCASS:</div><div class="mobile-list-body">1,000,000</div></td>
<td class="col-shareholding-percent"><div class="mobile-list-body">10.00%</div></td>
</tr></table>
"""


class UnitTests(unittest.TestCase):
    def test_wan_to_shares(self):
        self.assertEqual(wan_to_shares(1234.50), 12_345_000)

    def test_price_confirms_wan_unit(self):
        # 61,725 万元 / 1,234.50 万股 = 50 元。
        self.assertEqual(assess_unit(1234.50, "wan_shares", 61725), "ok")

    def test_unconverted_shares_are_rejected(self):
        self.assertEqual(assess_unit(1234.50, "shares", 61725), "mismatch")


class PositionTests(unittest.TestCase):
    def test_next_lot_is_not_eaten_as_percent(self):
        tokens = parse_position_tokens("200,000(L) 1,000(S)")
        self.assertEqual(
            [(item["position"], item["shares"]) for item in tokens],
            [("L", 200_000), ("S", 1_000)],
        )
        self.assertIsNone(tokens[0]["pct"])

    def test_percent_between_lots(self):
        tokens = parse_position_tokens("200,000(L) 5.50 1,000(S) 0.10")
        self.assertEqual(tokens[0]["pct"], 5.50)
        self.assertEqual(tokens[1]["shares"], 1_000)


class FundParseTests(unittest.TestCase):
    def test_synthetic_archive(self):
        rows = parse_eastmoney_fund_html(
            SYNTHETIC_FUND_JS,
            fund_code="990001",
            fund_name="合成港股通样本ETF",
            fund_type="指数型-股票",
            stock_code="99001",
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.shares, 12_345_000)
        self.assertIn("99001", row.notes)
        self.assertEqual(row.report_date, "2026-09-30")
        self.assertEqual(row.channel_tag, CONFIRMED)
        self.assertTrue(row.counts_toward_southbound)
        self.assertEqual(row.unit_status, "ok")
        self.assertEqual(row.report_scope, "top10")

    def test_hits_csv_channels(self):
        content = (
            "基金代码,基金名称,查询年份,序号,股票代码,股票名称,占净值比例,持股数,持仓市值,季度\n"
            "990001,合成港股通样本ETF,2026,1,99001,合成港股甲,12.5,1234.50,61725.00,2026年3季度股票投资明细\n"
            "990002,合成全球股票(QDII),2026,2,99001,合成港股甲,1.0,10.00,500.00,2026年3季度股票投资明细\n"
            "990004,合成沪港深样本,2026,3,99001,合成港股甲,2.0,20.00,1000.00,2026年3季度股票投资明细\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as handle:
            handle.write(content)
            path = handle.name
        catalog = {
            "990002": {"fund_type": "QDII-普通股票"},
            "990004": {"fund_type": "混合型"},
        }
        rows = {
            row.holder_key: row
            for row in parse_fund_hits_csv(path, catalog)
            if "99001" in (row.notes or "")
        }
        self.assertEqual(rows["fund:990001"].channel_tag, CONFIRMED)
        self.assertEqual(rows["fund:990001"].shares, 12_345_000)
        self.assertEqual(rows["fund:990002"].channel_tag, NON_CONNECT)
        self.assertFalse(rows["fund:990002"].counts_toward_southbound)
        self.assertEqual(rows["fund:990004"].channel_tag, POSSIBLE)


class DisclosureAndDailyTests(unittest.TestCase):
    def test_di_html_splits_long_and_short(self):
        rows = parse_di_summary_html(SYNTHETIC_DI_HTML, stock_code="99001", summary_date="2026-01-02")
        by_pos = {row.position_type: row for row in rows}
        self.assertEqual(by_pos["L"].shares, 200_000)
        self.assertEqual(by_pos["S"].shares, 1_000)
        self.assertEqual(by_pos["L"].report_date, "2026-01-01")
        self.assertEqual(by_pos["L"].channel_tag, NON_CONNECT)
        self.assertFalse(by_pos["L"].counts_toward_southbound)

    def test_roster_prefers_consolidated_and_does_not_sum(self):
        content = (
            "stock_code,report,form_serial,shareholder,shares_interested,pct_issued_voting,last_notice_date,form_href\n"
            "99001,complete_ss,CS20260101E00099,合成外资样本公司,\"9,999(L)\",1.00(L),01/01/2026,\n"
            "99001,consolidated_ss,CS20260101E00001,合成外资样本公司,\"200,000(L) 1,000(S)\",\"5.50(L) 0.10(S)\",01/01/2026,\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as handle:
            handle.write(content)
            path = handle.name
        rows = parse_di_roster_csv(path, "99001")
        long_rows = [row for row in rows if row.position_type == "L"]
        short_rows = [row for row in rows if row.position_type == "S"]
        self.assertEqual(len(long_rows), 1)
        self.assertEqual(long_rows[0].shares, 200_000)
        self.assertEqual(long_rows[0].source_ref, "CS20260101E00001")
        self.assertEqual(short_rows[0].shares, 1_000)
        self.assertNotEqual(long_rows[0].shares + short_rows[0].shares, 9_999)

    def test_daily_ratio_choice(self):
        point = parse_eastmoney_daily_row({
            "TRADE_DATE": "2026-09-30 00:00:00",
            "HOLD_SHARES": 1_000_000,
            "HOLD_SHARES_RATIO": 10.0,
            "TOTAL_SHARES_RATIO": 99.0,
            "CLOSE_PRICE": 10,
            "CHANGE_RATE": 1.2,
            "HOLD_MARKET_CAP": 10_000_000,
        })
        self.assertEqual(point.shares, 1_000_000)
        self.assertEqual(point.pct_of_issued, 10.0)
        self.assertNotEqual(point.pct_of_issued, 99.0)

    def test_mutualmarket_reads_value_not_heading(self):
        points = parse_mutualmarket_html(SYNTHETIC_MUTUAL)
        self.assertEqual(len(points), 1)
        self.assertEqual(mutualmarket_code(points[0]), "99001")
        self.assertEqual(points[0].shares, 1_000_000)
        self.assertEqual(points[0].pct_of_issued, 10.0)
        self.assertEqual(points[0].trade_date, "2026-09-30")

    def test_seats_sum(self):
        seats = [
            parse_eastmoney_seat_row({
                "HOLD_DATE": "2026-09-30", "HOLD_NUM": 400_000,
                "PARTICIPANT_CODE": "A00003", "ORG_NAME": "合成沪",
            }),
            parse_eastmoney_seat_row({
                "HOLD_DATE": "2026-09-30", "HOLD_NUM": 600_000,
                "PARTICIPANT_CODE": "A00004", "ORG_NAME": "合成深",
            }),
        ]
        self.assertEqual(sum(seat.shares for seat in seats), 1_000_000)

    def test_licensed_csv(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as handle:
            handle.write(
                "trade_date,stock_code,shares,pct_of_issued\n"
                "2026-09-30,99001,1000000,10\n"
                "2026-09-30,99002,5,1\n"
            )
            path = handle.name
        points = parse_licensed_csv(path, "99001")
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0].shares, 1_000_000)
        self.assertEqual(points[0].provider_id, "hkex_licensed_csv")


class ClassifyTests(unittest.TestCase):
    def test_channels(self):
        self.assertEqual(classify_fund("合成港股通样本ETF", "指数型-股票")[0], CONFIRMED)
        self.assertEqual(classify_fund("合成全球股票(QDII)", "QDII-普通股票")[0], NON_CONNECT)
        self.assertEqual(classify_fund("合成恒生样本ETF", "指数型-海外股票")[0], NON_CONNECT)
        self.assertEqual(classify_fund("合成沪港深样本", "混合型")[0], POSSIBLE)
        self.assertEqual(classify_disclosure("合成外资样本公司")[0], NON_CONNECT)
        self.assertEqual(classify_disclosure("中国人寿合成样本")[0], POSSIBLE)


if __name__ == "__main__":
    unittest.main()
