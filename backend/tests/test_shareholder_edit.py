"""Imported shareholder rows stay editable, and a re-upload does not wipe those edits."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import db  # noqa: E402
from db import get_stock_conn, init_stock_db  # noqa: E402
from services.file_parser import apply_attributions  # noqa: E402


def _csv(path, rows):
    with open(path, 'w', encoding='utf-8', newline='') as handle:
        handle.write('股东名称,席位号,券商,持股数量,股东类型,成本,解禁日期\n')
        for row in rows:
            handle.write(','.join(str(cell) for cell in row) + '\n')


class ShareholderEditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._orig = (db.DATA_DIR, db.META_DB_PATH, db.UPLOAD_DIR)
        db.DATA_DIR = self.tmp.name
        db.META_DB_PATH = os.path.join(self.tmp.name, '_meta.db')
        db.UPLOAD_DIR = os.path.join(self.tmp.name, 'uploads')
        self.addCleanup(self._restore)
        from fastapi.testclient import TestClient
        import app as app_module
        self.client = TestClient(app_module.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def _restore(self):
        db.DATA_DIR, db.META_DB_PATH, db.UPLOAD_DIR = self._orig
        try:
            from services import scheduler
            scheduler.stop()
        except Exception:
            pass

    def _issued(self, code='00700'):
        init_stock_db(code)
        with get_stock_conn(code) as conn:
            conn.execute(
                "INSERT INTO ccass_total (trade_date, total_shares, pct_of_issued) VALUES ('2026-11-04', 100000000, 50)"
            )
            conn.execute(
                """
                INSERT INTO ccass_participants
                    (trade_date, participant_id, participant_name, shares, pct_of_issued)
                VALUES ('2026-11-04', 'C00019', '汇丰银行', 5000000, 2.5)
                """
            )

    def test_validation_and_create(self):
        self._issued()
        empty = self.client.post('/api/stock/00700/attribution', json={'shareholder_name': '  ', 'participant_id': 'C00019'})
        self.assertEqual(empty.status_code, 422)
        self.assertIn('股东名称', empty.json()['detail'])

        bad_seat = self.client.post('/api/stock/00700/attribution', json={
            'shareholder_name': '腾讯控股', 'participant_id': '汇丰',
        })
        self.assertEqual(bad_seat.status_code, 422)

        bad_shares = self.client.post('/api/stock/00700/attribution', json={
            'shareholder_name': '腾讯控股', 'participant_id': 'C00019', 'shares_baseline': -5,
        })
        self.assertEqual(bad_shares.status_code, 422)

        bad_date = self.client.post('/api/stock/00700/attribution', json={
            'shareholder_name': '腾讯控股', 'participant_id': 'C00019', 'unlock_date': '2026/13/40',
        })
        self.assertEqual(bad_date.status_code, 422)

        bad_pct = self.client.post('/api/stock/00700/attribution', json={
            'shareholder_name': '腾讯控股', 'participant_id': 'C00019', 'holding_pct': 120,
        })
        self.assertEqual(bad_pct.status_code, 422)

        created = self.client.post('/api/stock/00700/attribution', json={
            'shareholder_name': '腾讯控股',
            'participant_id': 'c00019',
            'participant_name': '汇丰银行',
            'shares_baseline': 12000000,
            'investor_type': '控股股东',
            'cost_hkd': 320.5,
            'unlock_date': '2026-06-30',
            'notes': '手工录入',
        })
        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()
        self.assertEqual(body['participant_id'], 'C00019')
        self.assertEqual(body['shares_baseline'], 12000000)
        self.assertEqual(body['notes'], '手工录入')
        self.assertEqual(body['manually_edited'], 1)
        # 1e8 CCASS shares at 50% => 2e8 issued; 12,000,000 / 2e8 = 6%.
        self.assertEqual(body['holding_pct'], 6.0)
        self.assertEqual(body['shares_current'], 12000000)
        self.assertTrue(body['id'])

        duplicate = self.client.post('/api/stock/00700/attribution', json={
            'shareholder_name': '腾讯控股', 'participant_id': 'C00019', 'shares_baseline': 1,
        })
        self.assertEqual(duplicate.status_code, 409)

        listed = self.client.get('/api/stock/00700/attribution')
        payload = listed.json()
        self.assertEqual(payload['totals']['count'], 1)
        self.assertEqual(payload['totals']['shares_baseline'], 12000000)
        self.assertEqual(payload['totals']['holding_pct'], 6.0)
        self.assertEqual(payload['issued_shares'], 200000000)

    def test_put_patch_recompute_percentage_and_delete(self):
        self._issued()
        created = self.client.post('/api/stock/00700/attribution', json={
            'shareholder_name': '腾讯控股', 'participant_id': 'C00019', 'shares_baseline': 1000, 'notes': '原备注',
        }).json()
        att_id = created['id']

        patched = self.client.patch(f'/api/stock/00700/attribution/{att_id}', json={'holding_pct': 6})
        self.assertEqual(patched.status_code, 200, patched.text)
        self.assertEqual(patched.json()['shares_baseline'], 12000000)
        self.assertEqual(patched.json()['shares_current'], 12000000)
        self.assertEqual(patched.json()['holding_pct'], 6.0)
        self.assertEqual(patched.json()['notes'], '原备注')

        replaced = self.client.put(f'/api/stock/00700/attribution/{att_id}', json={
            'shareholder_name': '腾讯控股',
            'participant_id': 'C00019',
            'participant_name': '汇丰',
            'shares_baseline': 4000000,
            'holding_pct': 6,
            'investor_type': '基石',
            'cost_hkd': 10,
            'unlock_date': '2026-12-01',
            'notes': '改过',
        })
        self.assertEqual(replaced.status_code, 200, replaced.text)
        row = replaced.json()
        # Shares win when both are sent, so 4,000,000 / 2e8 = 2%, not the stale 6.
        self.assertEqual(row['shares_baseline'], 4000000)
        self.assertEqual(row['holding_pct'], 2.0)
        self.assertEqual(row['notes'], '改过')
        self.assertEqual(row['unlock_date'], '2026-12-01')
        self.assertEqual(row['shares_current'], 4000000)

        with get_stock_conn('00700') as conn:
            selloff = conn.execute(
                "SELECT shares_remaining FROM shareholder_selloff WHERE shareholder_name = ?",
                ('腾讯控股',),
            ).fetchone()
            unlock = conn.execute(
                "SELECT shares FROM unlock_calendar WHERE shareholder_name = ?",
                ('腾讯控股',),
            ).fetchone()
        self.assertIsNotNone(selloff)
        self.assertEqual(selloff['shares_remaining'], 4000000)
        self.assertEqual(unlock['shares'], 4000000)

        missing = self.client.patch('/api/stock/00700/attribution/9999', json={'notes': 'x'})
        self.assertEqual(missing.status_code, 404)
        empty_patch = self.client.patch(f'/api/stock/00700/attribution/{att_id}', json={})
        self.assertEqual(empty_patch.status_code, 422)

        removed = self.client.delete(f'/api/stock/00700/attribution/{att_id}')
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(removed.json()['deleted_id'], att_id)
        again = self.client.delete(f'/api/stock/00700/attribution/{att_id}')
        self.assertEqual(again.status_code, 404)
        listed = self.client.get('/api/stock/00700/attribution')
        self.assertEqual(listed.json()['shareholders'], [])
        with get_stock_conn('00700') as conn:
            left = conn.execute("SELECT COUNT(*) AS c FROM shareholder_selloff").fetchone()['c']
            unlocks = conn.execute("SELECT COUNT(*) AS c FROM unlock_calendar").fetchone()['c']
        self.assertEqual(left, 0)
        self.assertEqual(unlocks, 0)

    def test_reimport_keeps_manual_edits_until_overwrite(self):
        self._issued()
        path = os.path.join(self.tmp.name, 'book.csv')
        _csv(path, [
            ['腾讯控股', 'C00019', '汇丰银行', 12000000, '控股股东', 320.5, '2026-06-30'],
            ['高瓴资本', 'C00010', '花旗银行', 8500000, '基石投资者', 280, '2026-12-01'],
        ])
        with open(path, 'rb') as handle:
            uploaded = self.client.post(
                '/api/stock/00700/upload',
                files={'file': ('book.csv', handle, 'text/csv')},
            )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        self.assertEqual(uploaded.json()['skipped_manual'], [])

        rows = {row['shareholder_name']: row for row in self.client.get('/api/stock/00700/attribution').json()['shareholders']}
        tencent_id = rows['腾讯控股']['id']
        edited = self.client.patch(f'/api/stock/00700/attribution/{tencent_id}', json={
            'shares_baseline': 100, 'notes': '不要被导入盖掉',
        })
        self.assertEqual(edited.status_code, 200, edited.text)

        with get_stock_conn('00700') as conn:
            conn.execute(
                "UPDATE shareholder_attribution SET notes = ? WHERE shareholder_name = ?",
                ('文件行的备注', '高瓴资本'),
            )

        _csv(path, [
            ['腾讯控股', 'C00019', '汇丰银行', 99999999, '控股股东', 1, '2027-01-01'],
            ['高瓴资本', 'C00010', '花旗银行', 1000, '基石投资者', 1, '2027-02-01'],
        ])
        with open(path, 'rb') as handle:
            again = self.client.post(
                '/api/stock/00700/upload',
                files={'file': ('book.csv', handle, 'text/csv')},
            )
        self.assertEqual(again.status_code, 200, again.text)
        body = again.json()
        self.assertEqual(len(body['skipped_manual']), 1)
        self.assertEqual(body['skipped_manual'][0]['shareholder_name'], '腾讯控股')
        self.assertIn('没有覆盖', body['import_warning'])

        rows = {row['shareholder_name']: row for row in self.client.get('/api/stock/00700/attribution').json()['shareholders']}
        self.assertEqual(rows['腾讯控股']['shares_baseline'], 100)
        self.assertEqual(rows['腾讯控股']['notes'], '不要被导入盖掉')
        self.assertEqual(rows['腾讯控股']['unlock_date'], '2026-06-30')
        self.assertEqual(rows['高瓴资本']['shares_baseline'], 1000)
        self.assertEqual(rows['高瓴资本']['notes'], '文件行的备注')
        self.assertEqual(rows['高瓴资本']['manually_edited'], 0)

        with open(path, 'rb') as handle:
            forced = self.client.post(
                '/api/stock/00700/upload',
                files={'file': ('book.csv', handle, 'text/csv')},
                params={'overwrite': 'true'},
            )
        self.assertEqual(forced.status_code, 200, forced.text)
        self.assertEqual(forced.json()['skipped_manual'], [])
        rows = {row['shareholder_name']: row for row in self.client.get('/api/stock/00700/attribution').json()['shareholders']}
        self.assertEqual(rows['腾讯控股']['shares_baseline'], 99999999)
        self.assertEqual(rows['腾讯控股']['notes'], '不要被导入盖掉')
        self.assertEqual(rows['腾讯控股']['unlock_date'], '2027-01-01')
        self.assertEqual(rows['腾讯控股']['manually_edited'], 0)
        # 99,999,999 / 200,000,000 = 49.9999995 -> 50.0 after rounding to 4 decimals.
        self.assertEqual(rows['腾讯控股']['holding_pct'], 50.0)

    def test_apply_reports_skipped_without_http(self):
        init_stock_db('00700')
        apply_attributions('00700', [{
            'shareholder_name': '张三', 'participant_id': 'B01010',
            'participant_name': '中信', 'shares_baseline': 10,
        }], [])
        with get_stock_conn('00700') as conn:
            conn.execute(
                "UPDATE shareholder_attribution SET manually_edited = 1, notes = ? WHERE shareholder_name = ?",
                ('手改', '张三'),
            )
        report = apply_attributions('00700', [{
            'shareholder_name': '张三', 'participant_id': 'B01010',
            'participant_name': '中信', 'shares_baseline': 99,
        }], [])
        self.assertEqual(report['skipped_count'], 1)
        self.assertEqual(report['applied_count'], 0)
        with get_stock_conn('00700') as conn:
            row = conn.execute("SELECT shares_baseline, notes FROM shareholder_attribution").fetchone()
        self.assertEqual(row['shares_baseline'], 10)
        self.assertEqual(row['notes'], '手改')


if __name__ == '__main__':
    unittest.main()
