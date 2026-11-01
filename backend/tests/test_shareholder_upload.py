"""Shareholder-register upload: common HK/IR files must parse and then show up."""
import csv
import os
import sys
import tempfile
import unittest
import zipfile
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from openpyxl import Workbook  # noqa: E402

from db import get_stock_conn, init_stock_db  # noqa: E402
import db  # noqa: E402
from services.file_parser import apply_attributions, parse_uploaded_file  # noqa: E402
from services.selloff_engine import get_shareholder_summary  # noqa: E402


def _xlsx(path, rows):
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    wb.save(path)
    wb.close()


class ShareholderParseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def parse_rows(self, name, rows):
        path = os.path.join(self.tmp.name, name)
        _xlsx(path, rows)
        return parse_uploaded_file(path, name)

    def test_simplified_headers_and_example_csv(self):
        example = os.path.join(os.path.dirname(__file__), '..', '..', 'examples', 'shareholder_register_example.csv')
        result = parse_uploaded_file(example, 'shareholder_register_example.csv')
        self.assertIsNone(result.get('error'))
        names = [a['shareholder_name'] for a in result['attributions']]
        self.assertEqual(names, ['腾讯控股', '高瓴资本'])
        self.assertEqual(result['attributions'][0]['participant_id'], 'C00019')
        self.assertEqual(result['attributions'][0]['participant_name'], '汇丰银行')
        self.assertEqual(result['attributions'][0]['shares_baseline'], 12000000)
        self.assertEqual(result['attributions'][0]['unlock_date'], '2026-06-30')
        self.assertEqual(result['attributions'][1]['shares_baseline'], 8500000)
        self.assertEqual(result['attributions'][1]['unlock_date'], '2026-12-01')

        xlsx = os.path.join(os.path.dirname(example), 'shareholder_register_example.xlsx')
        xlsx_result = parse_uploaded_file(xlsx, 'shareholder_register_example.xlsx')
        self.assertEqual(
            [a['shareholder_name'] for a in xlsx_result['attributions']],
            ['腾讯控股', '高瓴资本'],
        )

        gbk = os.path.join(self.tmp.name, 'gbk.csv')
        with open(gbk, 'w', encoding='gbk', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['股東名稱', '席位號', '持股數量'])
            writer.writerow(['阿爾法', 'C00033', '3000000'])
        gbk_result = parse_uploaded_file(gbk, 'gbk.csv')
        self.assertEqual(gbk_result['attributions'][0]['shareholder_name'], '阿爾法')
        self.assertEqual(gbk_result['attributions'][0]['participant_id'], 'C00033')
        self.assertEqual(gbk_result['attributions'][0]['shares_baseline'], 3000000)

    def test_traditional_chinese_register(self):
        result = self.parse_rows('trad.xlsx', [
            ['某某有限公司'],
            ['股東名冊'],
            ['截止日期', '2026-10-31'],
            ['股東名稱', '參與者編號', '券商', '持股數量', '解禁日期'],
            ['Alpha Limited', 'C00033', '摩根士丹利', '3,000,000', '2026/12/01'],
        ])
        self.assertIsNone(result.get('error'))
        self.assertEqual(len(result['attributions']), 1)
        row = result['attributions'][0]
        self.assertEqual(row['shareholder_name'], 'Alpha Limited')
        self.assertEqual(row['participant_id'], 'C00033')
        self.assertEqual(row['participant_name'], '摩根士丹利')
        self.assertEqual(row['shares_baseline'], 3000000)
        self.assertEqual(row['unlock_date'], '2026-12-01')

    def test_name_and_customer_columns_are_not_dropped(self):
        named = self.parse_rows('xingming.xlsx', [
            ['序号', '姓名', '持股数量', '席位'],
            [1, '张三', 1000000, 'C00019'],
            [2, '李四', 2000000, 'B01010'],
        ])
        self.assertEqual(
            [(a['shareholder_name'], a['participant_id'], a['shares_baseline']) for a in named['attributions']],
            [('张三', 'C00019', 1000000), ('李四', 'B01010', 2000000)],
        )

        broker = self.parse_rows('broker.xlsx', [
            ['客户编号', '客户姓名', '经纪商', 'CCASS编号', '股数'],
            ['A001', '王五', '中信证券', 'B01234', 500000],
        ])
        row = broker['attributions'][0]
        self.assertEqual(row['shareholder_name'], '王五')
        self.assertEqual(row['participant_id'], 'B01234')
        self.assertEqual(row['participant_name'], '中信证券')
        self.assertEqual(row['shares_baseline'], 500000)

    def test_title_row_does_not_steal_header(self):
        result = self.parse_rows('title.xlsx', [
            ['股东名册 席位持股一览'],
            ['股东名称', '席位号', '持股数量', '券商'],
            ['Alpha Fund', 'C00033', 3000000, '大摩'],
            ['Beta Ltd', 'C00088', 1500000, '高盛'],
        ])
        self.assertEqual(
            [(a['shareholder_name'], a['participant_id'], a['shares_baseline']) for a in result['attributions']],
            [('Alpha Fund', 'C00033', 3000000), ('Beta Ltd', 'C00088', 1500000)],
        )

    def test_shareholder_code_column_does_not_replace_name(self):
        result = self.parse_rows('order.xlsx', [
            ['股东编号', '股东名称', '席位号', '持股数量'],
            ['SH001', '张三', 'C00019', 1000000],
        ])
        row = result['attributions'][0]
        self.assertEqual(row['shareholder_name'], '张三')
        self.assertEqual(row['participant_id'], 'C00019')

    def test_english_participant_name_is_not_the_id(self):
        result = self.parse_rows('en.xlsx', [
            ['Shareholder Name', 'Participant ID', 'Participant Name', 'Shares', 'Investor Type', 'Unlock Date'],
            ['Tencent', 'C00019', 'HSBC', 12000000, 'controlling', '2026-06-30'],
        ])
        row = result['attributions'][0]
        self.assertEqual(row['shareholder_name'], 'Tencent')
        self.assertEqual(row['participant_id'], 'C00019')
        self.assertEqual(row['participant_name'], 'HSBC')
        self.assertEqual(row['investor_type'], 'controlling')

    def test_stale_worksheet_dimension_still_reads_rows(self):
        src = os.path.join(self.tmp.name, 'src.xlsx')
        _xlsx(src, [
            ['股东名称', '席位号', '券商', '持股数量'],
            ['Alpha Fund', 'C00033', '大摩', 3000000],
            ['Beta Ltd', 'C00088', '高盛', 1500000],
        ])
        dst = os.path.join(self.tmp.name, 'bad_dim.xlsx')
        with zipfile.ZipFile(src, 'r') as zin, zipfile.ZipFile(dst, 'w') as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == 'xl/worksheets/sheet1.xml':
                    text = data.decode('utf-8')
                    text = re.sub(r'(<dimension[^>]*ref=")[^"]+(")', r'\1A1\2', text, count=1)
                    data = text.encode('utf-8')
                zout.writestr(item, data)
        result = parse_uploaded_file(dst, 'bad_dim.xlsx')
        self.assertEqual(len(result['attributions']), 2)
        self.assertEqual(result['attributions'][0]['shareholder_name'], 'Alpha Fund')

    def test_csv_extra_column_and_tsv_do_not_crash(self):
        extra = os.path.join(self.tmp.name, 'extra.csv')
        with open(extra, 'w', encoding='utf-8', newline='') as f:
            f.write('股东名称,席位号,持股数量\nAlpha,C00033,100,extra\n')
        result = parse_uploaded_file(extra, 'extra.csv')
        self.assertEqual(result['attributions'][0]['shareholder_name'], 'Alpha')
        self.assertEqual(result['attributions'][0]['shares_baseline'], 100)

        tsv = os.path.join(self.tmp.name, 'book.tsv')
        with open(tsv, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f, delimiter='\t')
            writer.writerow(['姓名', '参与者编号', '持股数'])
            writer.writerow(['赵六', 'B01555', '250000'])
        result = parse_uploaded_file(tsv, 'book.tsv')
        self.assertEqual(result['attributions'][0]['shareholder_name'], '赵六')
        self.assertEqual(result['attributions'][0]['participant_id'], 'B01555')

    def test_unrecognized_file_returns_error_instead_of_silent_success(self):
        path = os.path.join(self.tmp.name, 'notes.txt')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('this is not a register\njust some notes\n')
        result = parse_uploaded_file(path, 'notes.txt')
        self.assertEqual(result['attributions'], [])
        self.assertTrue(result.get('error'))

    def test_old_xls_is_a_clear_error(self):
        path = os.path.join(self.tmp.name, 'old.xls')
        with open(path, 'wb') as f:
            f.write(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'\x00' * 64)
        result = parse_uploaded_file(path, 'old.xls')
        self.assertEqual(result['attributions'], [])
        self.assertIn('.xlsx', result.get('error') or '')


class ShareholderSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._orig = (db.DATA_DIR, db.META_DB_PATH, db.UPLOAD_DIR)
        db.DATA_DIR = self.tmp.name
        db.META_DB_PATH = os.path.join(self.tmp.name, '_meta.db')
        db.UPLOAD_DIR = os.path.join(self.tmp.name, 'uploads')
        self.addCleanup(self._restore)

    def _restore(self):
        db.DATA_DIR, db.META_DB_PATH, db.UPLOAD_DIR = self._orig

    def test_uploaded_rows_show_without_ccass_participants(self):
        code = '00700'
        init_stock_db(code)
        with get_stock_conn(code) as conn:
            conn.execute(
                "INSERT INTO ccass_total (trade_date, total_shares, pct_of_issued) VALUES ('2026-10-30', 100, 10)"
            )
        apply_attributions(code, [{
            'shareholder_name': '腾讯控股',
            'participant_id': 'C00019',
            'participant_name': '汇丰银行',
            'shares_baseline': 12000000,
            'investor_type': '控股股东',
            'cost_hkd': 320.5,
            'unlock_date': '2026-06-30',
        }], [])
        summary = get_shareholder_summary(code)
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]['shareholder_name'], '腾讯控股')
        self.assertEqual(summary[0]['participant_id'], 'C00019')
        self.assertEqual(summary[0]['shares_baseline'], 12000000)
        self.assertEqual(summary[0]['shares_current'], 12000000)

    def test_upload_api_then_attribution_api(self):
        from fastapi.testclient import TestClient
        import app as app_module

        example = os.path.join(
            os.path.dirname(__file__), '..', '..', 'examples', 'shareholder_register_example.csv'
        )
        with TestClient(app_module.app) as client:
            with open(example, 'rb') as f:
                uploaded = client.post(
                    '/api/stock/00700/upload',
                    files={'file': ('shareholder_register_example.csv', f, 'text/csv')},
                )
            self.assertEqual(uploaded.status_code, 200, uploaded.text)
            body = uploaded.json()
            self.assertNotIn('error', body)
            self.assertEqual(body['summary']['attributions_found'], 2)

            listed = client.get('/api/stock/00700/attribution')
            self.assertEqual(listed.status_code, 200, listed.text)
            names = [row['shareholder_name'] for row in listed.json()['shareholders']]
            self.assertEqual(names, ['腾讯控股', '高瓴资本'])

            bad = client.post(
                '/api/stock/00700/upload',
                files={'file': ('notes.txt', '只是一段说明\n没有表格\n', 'text/plain')},
            )
            self.assertEqual(bad.status_code, 200, bad.text)
            self.assertIn('error', bad.json())
            self.assertEqual(bad.json()['attributions'], [])


if __name__ == '__main__':
    unittest.main()
